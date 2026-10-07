#!/usr/bin/env python3
r"""PostToolUse, PostToolUseFailure and Stop hook (rerun-pass-check): a rerun pass is not conclusive verification.

WHAT IT DOES
    A check that fails and then passes on a rerun with no deliberate change in between is an unresolved
    intermittent result: the earlier failure is recorded and investigated, and the later pass is not presented
    as conclusive verification (rerun-pass-is-still-failure). This hook watches for the two common shapes and
    keeps the earlier failure in view:

    1. A CI RERUN. A shell command that reruns a CI run (`gh run rerun`, `glab ci retry`) is a rerun by
       definition. The hook reads the command in a small closed grammar (SHELL GRAMMAR, below) and does not
       decide which word is the command: a simple command whose word values, each expansion read as empty,
       hold a word whose last path part is gh, then run, then rerun (or glab, ci, retry), in that order at any
       positions, names a CI rerun. When its call is not reported as failed, it is a CERTAIN rerun, which means
       only that the hook keeps it outstanding for the Stop check (4): the hook adds a note to the assistant's
       context that a command naming a CI rerun was not reported as failed, that this does not show that a rerun
       was started (an exit status can hide a refused call) or that CI failed before, and that if CI that had
       failed was rerun, the rerun's pass does not erase that failure, which is to be recorded and investigated.
    2. A LOCAL RERUN. A recognized check command (CHECK_RE: a test runner, `make test` or `make check`, a
       `--self-test` or `--check` run) that failed and then, run again as the same words (check_key: each word
       as written, with its quoting, so pytest 'a  b' and pytest 'a b' differ; blanks and comments between
       words, leading and trailing newlines, and a final ; after a word or ) do not count; every other operator
       counts as written, so pytest -q; ls and pytest -q && ls differ, and so do pytest -q; ls and the same
       words with a newline in place of the ;), passed, with no change recorded between the two runs, is a
       CERTAIN rerun. A check command whose words hold an expansion (an allowed $ expansion, quoted or not, or
       an unquoted * ? or ~ anywhere in a word) takes its words' values only when it runs, so it is never
       compared with another run, and its fail-then-pass gets no note (\npytest $LINENO and pytest $LINENO
       pass different arguments). After the passing run, the hook adds a note that the pass does not erase the recorded
       failure, which is to be recorded and investigated, and keeps the rerun outstanding.
    3. An UNCERTAIN rerun gets a note after the tool call and nothing more: it is never kept, so it never arms
       the Stop refusal. It is either a command outside the grammar (off-grammar), always a possible CI rerun
       because the hook could not parse it, so a CI rerun cannot be ruled out, and no search of its text
       decides otherwise (noted even when the call fails); or a CI rerun call (1) that fails, which may or may
       not have started the rerun (gh run rerun N && gh run watch N timing out started one; false && gh run
       rerun N started none). Its note says that a CI rerun cannot be ruled out, that this does not show that
       CI was rerun or that a check failed before, and that if CI or a check that had failed was rerun, the
       rerun's pass does not erase that failure. A blank command (only whitespace) runs nothing and gets no
       note.
    4. AT TURN END (Stop), while a certain rerun is outstanding, a final message that presents a pass as conclusive
       (CONCLUSIVE_RE: "all tests pass", "CI is green", "verified", and similar, each not directly after a negation
       such as "not", "cannot", "n't", or "never") without naming the earlier failure (DISCLOSED_RE: "flaky",
       "intermittent", "rerun", "earlier failure", and similar) is refused, with a reason that says the session ran
       a command that names a CI rerun and was not reported as failed, or saw a check pass on a rerun after a
       recorded failure, names those reruns, and asks the assistant to state any earlier failure and that it is
       unresolved; it does not assert that a CI rerun was started or followed a failure. A final message that
       names any disclosure word, wherever it stands (also inside a denial, a quotation or a negation, as in "I did
       not rerun CI"), clears the outstanding reruns.
       A loop cap bounds the refusals: inside one continuous stop_hook_active run at most BLOCK_CAP, then the stop
       is allowed with a one-line warning that says the same as the refusal: it asserts no CI rerun either, and
       names a local rerun only as a check seen to pass after a recorded failure. The reruns stay outstanding,
       so a later turn is refused again.

    A CHANGE between two runs is any Write, Edit, MultiEdit, or NotebookEdit call that did not fail, any shell
    command outside the grammar that did not fail (also a possible CI rerun, see 3), and any other shell command
    that did not fail, is not a CI rerun, and is not read-only. A command is read-only only
    inside the grammar: no simple command writes a file, and in each simple command, after leading ! { } if
    then elif else fi do done while until, either nothing remains or the command word holds no expansion and
    its last path part (so ./cat and /usr/bin/env count) is on the short read-only list (READ_ONLY: cat, ls,
    grep, echo, and similar), git with READ_ONLY_GIT
    (status, diff, log, ...), gh with READ_ONLY_GH (run view, pr checks, ...), env with no argument, or a
    for NAME [in ...] header. So an install, a checkout, a sed, an echo into a file, an assignment prefix, or
    any command outside the grammar between the runs counts as a deliberate change and no local rerun is
    flagged (a command outside the grammar is itself noted as a possible CI rerun, see 3).

    SHELL GRAMMAR. The hook reads the characters of this grammar exactly; anything outside it is off-grammar.
    Inside it, bash still expands some words when the command runs (an expansion, a pathname pattern, a
    leading ~), which the hook does not do (RESIDUAL COVERAGE).
    - The whole text is off-grammar if it holds a NUL or is longer than MAX_PARSE (8192) characters, so the
      reading work is bounded (a longer command is a possible CI rerun, see 3). Blanks are space and tab. An
      unquoted # where a token starts is a comment up to the next newline (a backslash before that newline
      does not extend it).
    - Operators, longest first: <<< &>> &> >> >| >& <& && || |& > < ; & | ( ) and newline. Off-grammar:
      <<, <<-, <>, ;;, ;&, ((, and a ( directly after a word.
    - A word is one or more pieces; any other character outside quotes (a carriage return, a non-ASCII
      character, a backtick) is off-grammar. A piece is: an unquoted character from A-Za-z0-9_./:=+,@%^-~*?]#
      (# not at the start; [ only in the whole plain word [ or [[, { and } and ! only as the whole plain word
      { } or !; the hook reads ~ as a plain character, though bash tilde-expands a word that starts with it,
      such as ~ or ~-); \c, read as a literal c (a \ before a newline or at the end of the text is off-grammar);
      '...', read literally, which must close; "...", in which \ before $ ` " or \ gives that character, \
      before a newline is off-grammar, \ before any other character keeps both, a backtick is off-grammar,
      a $ must start an allowed expansion, and which must close; or an allowed expansion, unquoted or inside
      "...": exactly $NAME, ${NAME}, $0 to $9, and $? $# $$ $! $@ $* $-. Every other $ ($(, $((, ${x:-...},
      $', $", a lone $) is off-grammar.
    - A word of unquoted digits only, directly before < or >, is a descriptor when it is one digit and is
      off-grammar when it is two or more (x12>f is the word x12 and a redirection).
    - A redirection is an optional descriptor, then one of < > >> >| &> &>> <<< >& <&, then an operand
      word; an operator, a comment or the end of the text in its place is off-grammar. The operand of >& or
      <& must be one unquoted digit or an unquoted -, and >& with no descriptor also accepts an unquoted
      /dev/null; any other operand is off-grammar. After > >> >| &> or &>> the operand names a file written
      unless it is the unquoted word /dev/null; < and <<< write nothing.
    - Simple commands end at every control operator.

    A check command line that also runs a simple command that is neither a check, nor read-only, nor cd,
    pushd, popd, set or tee (`pip install -e . && pytest -q`) counts as one
    change for later runs; it is counted after that line's own comparison, so the identical line run twice
    is still a rerun. A failed call is not a change, even one
    that changed something before it failed, and a CI rerun changes nothing locally. A failed run is a
    PostToolUseFailure call, or a PostToolUse call whose tool_response holds interrupted true or a nonzero
    integer exit code field (exit_code, exitCode, returncode or returnCode); any other PostToolUse call is a
    pass, also one with no such field or with an exit code in another form ("1", 1.0).

    Events: PostToolUse and PostToolUseFailure (matcher Bash|Write|Edit|MultiEdit|NotebookEdit), and Stop.
    Output: nothing, or ONE line of JSON on stdout: a hookSpecificOutput additionalContext note (after a tool
    call), a top-level decision "block" object with a reason, or a top-level systemMessage warning (Stop).
    Exit status: always 0. No configuration is needed. State: one JSON file per session (named by a SHA-256 of
    session_id, else transcript_path) in AIQT_HOOK_STATE_DIR/rerun-pass-check when that is an absolute path,
    else $XDG_STATE_HOME/aiqt-guardrails/rerun-pass-check, else $HOME/.local/state/aiqt-guardrails/
    rerun-pass-check (created 0700, written by an atomic replace).

FAILURE DIRECTION
    After a tool call the safe direction is to inform: a rerun the hook recognizes is noted even when its state
    cannot be saved. At Stop the safe direction is not to hold the session on a guess: with no readable state,
    no final message in the payload (last_assistant_message), or an unknown or unsaveable refusal count under
    stop_hook_active, the stop is allowed. A state file longer than STATE_MAX_BYTES, or with a negative
    counter or a non-text flag, is malformed and read as no state (it is never parsed from a cut prefix). A
    well-formed state file without the current STATE_VERSION (written by an earlier revision of the hook) is
    discarded and read as no earlier runs, so no flag it holds arms a refusal; one of the current STATE_VERSION
    holding a flag without a prefix this version keeps (CI_FLAG "CI rerun command: ", LOCAL_FLAG "local rerun: ")
    is malformed.
    Any error, an unreadable payload, or an unrecognized event exits 0 with no output. A worker process
    (AIQT_HOOKS_WORKER=1, or a legacy spelling) is skipped.

RESIDUAL COVERAGE
    It recognizes only the listed CI rerun and check commands run through the shell tool. A rerun through a web
    page, a pushed empty commit, a runner's own retry option, an API call such as gh api .../rerun, a gh alias, or a
    changed command line is not seen. The shell reading is exact only for the closed grammar described above. Inside
    the grammar, a CI rerun is missed only when its words come into existence when the command runs, or bash runs
    gh under another name: an expansion that has a value ($c run rerun, with c=gh), a tilde expansion (~ run rerun
    with HOME=/path/to/gh, ~+ run rerun with PWD=/path/to/gh, or ~- with OLDPWD set so; bash tilde-expands a word
    the hook reads as a plain ~), a pathname expansion (/usr/bin/g? run rerun), an alias, a shell function, a hashed
    command name (hash -p /path/to/gh x; x run rerun), or a command string handed to another program (sh -c '...',
    eval '...', ssh, xargs input, a script).
    The name rule ignores command position and whether the named words ran, so a simple command that names gh, run
    and rerun in order without running them is noted anyway, and when its call succeeds it is kept as a certain
    rerun: echo gh run rerun, unquoted or with each word quoted alone (echo "gh" "run" "rerun"), command -v gh run
    rerun, a function body that is defined but never called, false && gh run rerun 7; true (a false note, and false
    refusals at Stop). The hook reads an exit status only from an integer exit code field of the tool response,
    where one is present, and that status is the whole call's, not each named command's: a later command,
    || true, a background &, a ! or an if or while condition can hide a gh call the server refused (HTTP 403),
    with or without pipefail set, and so can a pipe under default bash options, so gh run rerun 7 2>&1 | tail -5,
    gh run rerun 7 || true, gh run rerun 7; echo done, gh run rerun 7 &, ! gh run rerun 7 and if gh run rerun 7;
    then :; fi, with gh refused, exit 0 and are kept as certain (a note that says only that the call was not
    reported as failed, and false refusals at Stop); with pipefail set, the pipe form exits nonzero and gets the
    uncertain note only, and the other forms still exit 0. Every command outside the grammar
    is a possible CI rerun, by construction: this includes `echo "$(date)"`, a here-document, a line continuation,
    ${x:-y}, $'...', a redirection spelling not listed, and any command longer than MAX_PARSE characters. Each
    nonblank one gets a note, even when it fails (a blank command, only whitespace, gets no note), so an
    off-grammar command that reruns nothing (a here-document commit, notably) gets a false note. An uncertain
    rerun (3) is never kept and never brings a refusal: a real CI rerun written outside the grammar, a CI rerun
    call that failed after the rerun started, and an off-grammar check command that fails and then passes each get
    their note and no refusal at Stop (a missed refusal). An off-grammar command also counts as a change unless it
    fails, so a local rerun across it gets no local-rerun note of its own, and a check command outside the grammar
    is never compared as a check.
    Read-only is decided only inside the grammar: env with any argument, an assignment prefix (X=1 ls), a command
    word holding an expansion, and any output redirection (> >> >| &> &>>) whose target is not the unquoted word
    /dev/null count as a change; an input redirection (< <<<) and a descriptor duplication or close (>&2, <&0, >&-)
    write nothing. A failed call is never a change, so a fix made by a command that then failed reads as no change
    (a false note). A change made outside the tool calls it sees (by the user, another process, a background job) is
    not seen, so a fix applied that way reads as no change and the pass is flagged anyway (a false note); a
    read-only-looking command with a side effect, or a tee into a source file beside a check, reads as no change. A
    setup command that is not read-only beside a check (`source venv/bin/activate && pytest`, an export) counts as a
    change, so a later rerun of that check alone is not flagged (a missed note). The pass and fail reading rests on
    the event name and a few tool_response fields, not on the command's output. The Stop check reads only the final
    message, by fixed phrase lists: a conclusive claim worded otherwise passes, a claim after an unlisted negation
    ("could not get it verified") is refused, and a message naming any disclosure word passes and clears the reruns,
    whether or not it records the failure and wherever the word stands, also inside a denial, a quotation or a
    negation ("I did not rerun CI"). It does not record or investigate the failure itself. Concurrent hook runs in
    one session can lose a state update. A state file written by an earlier revision (without the current
    STATE_VERSION) is discarded, so a local rerun across it is missed. The state file is trusted: a hand-edited
    state of the current STATE_VERSION whose flags carry a prefix this version keeps still arms a refusal, and a
    failure recorded in it still makes a later pass a rerun. A check run again with a word quoted another way
    ('a b', then "a b" or a\ b) is a different check (a missed note), and so is one with an operator spelled
    another way (pytest -q; ls, then pytest -q && ls, or a newline in place of the ;); leading and trailing
    newlines and a final ; do not count. A check command whose words hold an expansion (an allowed $ expansion,
    or an unquoted * ? or ~ anywhere in a word, even where bash would not expand it) is never compared with
    another run, so its fail-then-pass gets no local-rerun note (a missed note: pytest tests/test_*.py, or
    pytest $LINENO run twice; never a false one). State keeps at most MAX_CHECKS commands
    (one longer than MAX_KEY JSON characters is keyed by its SHA-256, so the state stays under STATE_MAX_BYTES) and
    MAX_FLAGS outstanding reruns. The hook fails open on its own failure (warn-only by design: an advisory hook must
    not block on its own failure): an internal error exits 0 with no output, an unreadable state file is read as no
    earlier runs (so a local rerun across it is missed) and allows the stop, though the current call's own CI rerun
    or possible CI rerun note is still given, and a note or refusal that cannot be written to stdout is dropped, so
    each can miss a note or a refusal.

Self-test: python3 -I -S -B rerun-pass-check.py --self-test
"""

import functools
import hashlib
import json
import os
import re
import stat
import sys
import tempfile

HOOK = "rerun-pass-check"
STATE_MAX_BYTES = 1 << 18
BLOCK_CAP = 2  # refusals per continuous stop_hook_active run before a Stop is allowed with a warning
MAX_CHECKS = 200
MAX_FLAGS = 20
MAX_SHOWN = 160  # characters of a command shown in a note
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
MAX_KEY = 512  # JSON characters of a check command kept as its state key; a longer one is keyed by its SHA-256
# A CI rerun is a simple command whose words hold one of these name sequences, in order, at any positions.
CI_RERUN = (("gh", "run", "rerun"), ("glab", "ci", "retry"))
# The closed grammar (SHELL GRAMMAR in the module docstring): unquoted word characters, the operators longest
# first, the operators outside it, the redirection operators, and the allowed expansions.
_WORD_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./:=+,@%^-~*?]#")
_OP_CHARS = "<>&|;()\n"
_OPS = ("<<<", "&>>", "&>", ">>", ">|", ">&", "<&", "&&", "||", "|&", ">", "<", ";", "&", "|", "(", ")", "\n")
_BAD_OPS = ("<<", "<>", ";;", ";&", "((")
_REDIRS = frozenset(("<", ">", ">>", ">|", "&>", "&>>", "<<<", ">&", "<&"))
_EXPANSION_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\}|[0-9?#$!@*-])")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# The longest command the grammar reads, in characters; a longer one is off-grammar (a possible CI rerun). It
# bounds the reading work, and CHECK_RE, whose worst case is quadratic, then sees only commands this long.
MAX_PARSE = 8192
SKIPPED = frozenset(("!", "{", "}", "if", "then", "elif", "else", "fi", "do", "done", "while", "until"))
# A negation directly before a conclusive phrase ("not verified", "hasn't been verified", "never green").
_NEGATED_RE = re.compile(r"(?:\bnot|\bcannot|n't|\bnever|\bno longer)"
                         r"(?:\s+(?:yet|been|be|fully|really|actually))*\s*$", re.I)
CHECK_RE = re.compile(
    r"(?:^|[\s;&|(/])(?:pytest|py\.test|tox|nox|jest|vitest|mocha|ctest|phpunit|rspec)(?:[\s;]|$)"
    r"|\bpython[0-9.]*\s+(?:-\S+\s+)*-m\s+(?:pytest|unittest)\b"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|check)\b"
    r"|\b(?:go|cargo)\s+test\b|\b(?:make|gmake|just)\s+(?:\S+\s+)*(?:test|tests|check)(?:[\s;]|$)"
    r"|(?:^|[\s;&|(])(?:mvn|gradle|\./gradlew)\s+(?:\S+\s+)*test\b|\s--self-test\b|\s--check\b")
CONCLUSIVE_RE = re.compile(
    r"\ball (?:the )?(?:tests|checks|gates)(?: now)? (?:pass|passed|passing|are passing|are green)\b"
    r"|\b(?:tests|checks|gates|suite|build|pipeline|ci)(?: now)? (?:passes|passed|pass|is green|are green|is passing)\b"
    r"|\b(?:ci|build|pipeline) (?:is )?(?:green|passing)\b|\ball green\b|\bverified\b"
    r"|\bconfirmed (?:working|passing)\b",
    re.I)
DISCLOSED_RE = re.compile(
    r"\bflak(?:y|e|es|iness)\b|\bintermittent(?:ly)?\b|\bre-?run\b|\bre-?ran\b|\bretr(?:y|ied)\b"
    r"|\bearlier fail(?:ure|ed)?\b|\bfirst (?:run|attempt) fail(?:ed|s)?\b|\bfailed (?:once|first|initially)\b",
    re.I)
# Only a certain rerun is kept as a flag for the Stop check; an uncertain rerun (a command outside the grammar, or a
# CI rerun call that failed) gets a note only. A state file without this version (written by an earlier revision of
# the hook, which kept other flags) is discarded, so no flag it holds arms a refusal.
STATE_VERSION = 2
# The prefixes of the flags this version keeps: a state of this version holding any other flag is malformed.
CI_FLAG = "CI rerun command: "
LOCAL_FLAG = "local rerun: "
READ_ONLY = frozenset(("cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "ls", "pwd",
                       "echo", "printf", "wc", "date", "sleep", "true", "file", "stat", "which", "type", "diff",
                       "cmp", "sha256sum", "md5sum", "du", "df", "id", "whoami", "uname", "jq", "tree"))
BESIDE_CHECK = frozenset(("cd", "pushd", "popd", "set", "tee"))  # not a change on a check command line
READ_ONLY_GIT = frozenset(("status", "log", "diff", "show", "rev-parse", "blame", "ls-files"))
READ_ONLY_GH = (("run", "view"), ("run", "list"), ("run", "watch"), ("pr", "view"), ("pr", "checks"))


def _cfg(name, env=None):
    """AIQT_<name> primary; the legacy ORCH_<name> spelling is accepted as a fallback."""
    env = os.environ if env is None else env
    v = env.get("AIQT_" + name)
    return v if v is not None else env.get("ORCH_" + name)


def _is_worker(env=None):
    """True in a subordinate worker process: AIQT_HOOKS_WORKER=1 (legacy spellings are also accepted)."""
    env = os.environ if env is None else env
    if env.get("AIQT_HOOKS_WORKER") == "1":
        return True
    return env.get("ORCH_WORKER") == "1" or "ORCH_VERIFY_OWNER" in env  # legacy spellings


def state_path(payload, env):
    sid = payload.get("session_id")
    tp = payload.get("transcript_path")
    if isinstance(sid, str) and sid:
        key = "s:" + sid
    elif isinstance(tp, str) and tp:
        key = "t:" + tp
    else:
        return None
    v = _cfg("HOOK_STATE_DIR", env)
    x = env.get("XDG_STATE_HOME")
    h = env.get("HOME")
    if v and os.path.isabs(v):
        d = os.path.join(v, HOOK)
    elif x and os.path.isabs(x):
        d = os.path.join(x, "aiqt-guardrails", HOOK)
    elif h and os.path.isabs(h):
        d = os.path.join(h, ".local", "state", "aiqt-guardrails", HOOK)
    else:
        return None
    return os.path.join(d, hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()[:32] + ".json")


def new_state():
    return dict(version=STATE_VERSION, change=0, checks=dict(), flags=[], blocks=0)


def load_state(path):
    """(state, readable): an absent file, or one without STATE_VERSION, is a fresh state; an unreadable or
    malformed one (also one of STATE_VERSION holding a flag without CI_FLAG or LOCAL_FLAG) is (fresh, False).
    The file is trusted: a hand-edited state of STATE_VERSION with flags of those prefixes arms a refusal."""
    if path is None:
        return new_state(), False
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return new_state(), True
    except OSError:
        return new_state(), False
    try:
        with os.fdopen(fd, "rb") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                return new_state(), False
            data = fh.read(STATE_MAX_BYTES + 1)
            if len(data) > STATE_MAX_BYTES:
                return new_state(), False  # oversized: never parse a cut prefix
            obj = json.loads(data)
        ok = (isinstance(obj, dict) and type(obj.get("change")) is int and obj["change"] >= 0
              and isinstance(obj.get("checks"), dict) and isinstance(obj.get("flags"), list)
              and all(isinstance(f, str) for f in obj["flags"]) and type(obj.get("blocks")) is int
              and obj["blocks"] >= 0)
        if not ok:
            return new_state(), False
        if not (type(obj.get("version")) is int and obj["version"] == STATE_VERSION):
            return new_state(), True  # written by an earlier revision: discarded, read as no earlier runs
        if not all(f.startswith((CI_FLAG, LOCAL_FLAG)) for f in obj["flags"]):
            return new_state(), False  # a flag this version never keeps: malformed
        return obj, True
    except Exception:
        return new_state(), False


def save_state(path, state):
    """Write the state by an atomic replace; True on success, False on any failure (never raises)."""
    if path is None:
        return False
    tmp = None
    try:
        d = os.path.dirname(path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        os.replace(tmp, path)
        return True
    except Exception:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


class _Unsupported(Exception):
    """The command is outside the closed grammar the hook reads (see SHELL GRAMMAR in the module docstring)."""


def _expansion(text, i):
    """The end of the allowed expansion that starts with the $ at text[i]; _Unsupported for any other $."""
    m = _EXPANSION_RE.match(text, i)
    if m is None:
        raise _Unsupported()
    return m.end()


def _tokens(text, spell=None, expands=None):
    """The tokens of `text` in one pass: ("op", operator), ("fd", digit) for a descriptor, or ("word", value,
    raw), where value is the word as the shell reads it with each expansion standing as one NUL character and
    raw is the word's text when every piece of it is an unquoted literal character, else None. Comments are
    dropped. When `spell` is a list, each token's text as written (a word with its quoting and expansions) is
    appended to it. When `expands` is a list, the text of each word that holds an expansion (an allowed $
    expansion, or an unquoted * ? or ~ anywhere in it) is appended to it. Raises _Unsupported for anything
    outside the grammar."""
    if "\0" in text:
        raise _Unsupported()
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t":
            i += 1
        elif c == "#":  # a comment, up to the newline (a backslash before that newline does not extend it)
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c in _OP_CHARS:
            if text.startswith(_BAD_OPS, i) and not text.startswith("<<<", i):
                raise _Unsupported()  # <<, <<-, <>, ;;, ;&, ((
            op = next(o for o in _OPS if text.startswith(o, i))
            out.append(("op", op))
            if spell is not None:
                spell.append(op)
            i += len(op)
        else:
            val, raw, special, pattern, j = [], True, False, False, i
            while j < n:
                c = text[j]
                if c in _WORD_CHARS:
                    val.append(c)
                    pattern = pattern or c in "*?~"
                    j += 1
                elif c in "[{}!":
                    val.append(c)
                    special = True
                    j += 1
                elif c == "\\":
                    if j + 1 >= n or text[j + 1] == "\n":
                        raise _Unsupported()  # a line continuation, or a backslash at the end
                    val.append(text[j + 1])
                    raw = False
                    j += 2
                elif c == "'":
                    k = text.find("'", j + 1)
                    if k < 0:
                        raise _Unsupported()
                    val.append(text[j + 1:k])
                    raw = False
                    j = k + 1
                elif c == '"':
                    raw, j = False, j + 1
                    while True:
                        c = text[j:j + 1]
                        if c in ("", "`"):
                            raise _Unsupported()  # unclosed, or a backtick
                        if c == '"':
                            j += 1
                            break
                        if c == "\\":
                            d = text[j + 1:j + 2]
                            if d in ("", "\n"):
                                raise _Unsupported()
                            val.append(d if d in '$`"\\' else c + d)
                            j += 2
                        elif c == "$":
                            j = _expansion(text, j)
                            val.append("\0")
                        else:
                            val.append(c)
                            j += 1
                elif c == "$":
                    j = _expansion(text, j)
                    val.append("\0")
                    raw = False
                elif c in " \t" or c in _OP_CHARS:
                    break
                else:
                    raise _Unsupported()  # a backtick, a carriage return, a non-ASCII or another character
            word = "".join(val)
            if special and not (raw and word in ("[", "[[", "{", "}", "!")):
                raise _Unsupported()  # [ { } ! only as a whole plain word
            nxt = text[j:j + 1]
            if nxt == "(":
                raise _Unsupported()  # a ( directly after a word
            if spell is not None:
                spell.append(text[i:j])
            if expands is not None and (pattern or "\0" in word):
                expands.append(text[i:j])
            if raw and word.isdigit() and nxt in ("<", ">"):
                if len(word) > 1:
                    raise _Unsupported()  # a descriptor of two or more digits
                out.append(("fd", word))
            else:
                out.append(("word", word, word if raw else None))
            i = j
    return out


@functools.lru_cache(maxsize=4)
def parse(cmd):
    """The simple commands `cmd` runs, as a tuple of (words, writes) pairs: words is the tuple of word values
    with redirections removed (an expansion stands as a NUL character, read as empty), and writes is True
    when a redirection of that simple command writes a file; None when `cmd` is outside the grammar (including
    any command longer than MAX_PARSE characters)."""
    if len(cmd) > MAX_PARSE:
        return None
    try:
        toks = _tokens(cmd)
    except _Unsupported:
        return None
    cmds, words, writes, i = [], [], False, 0
    while i < len(toks):
        tok, fd = toks[i], None
        if tok[0] == "fd":
            fd, i = tok[1], i + 1
            tok = toks[i]  # the scanner gives a descriptor only directly before < or >
        if tok[0] == "op" and tok[1] in _REDIRS:
            if i + 1 >= len(toks) or toks[i + 1][0] != "word":
                return None  # a redirection with no operand word
            op, raw = tok[1], toks[i + 1][2]
            if op in (">&", "<&"):
                if not (raw == "-" or raw is not None and len(raw) == 1 and raw.isdigit()
                        or op == ">&" and fd is None and raw == "/dev/null"):
                    return None  # an operand spelling not listed
            elif op not in ("<", "<<<") and raw != "/dev/null":
                writes = True
            i += 2
        elif tok[0] == "op":
            if words or writes:
                cmds.append((tuple(words), writes))
            words, writes, i = [], False, i + 1
        else:
            words.append(tok[1])
            i += 1
    if words or writes:
        cmds.append((tuple(words), writes))
    return tuple(cmds)


def check_key(cmd):
    """The state key of an in-grammar check command: its tokens as written, each word with its quoting and
    expansions, so pytest 'a  b' and pytest 'a b' are different checks; blanks and comments between tokens do
    not count, so pytest  -q and pytest -q are one check, and neither do leading and trailing newlines or a
    final ; after a word or ), so pytest -q; and pytest -q are one check. Every other operator counts as
    written (a newline in place of a ; between two commands is a different check). The tokens are joined by
    one space, except that a descriptor stays joined to its redirection (2>f, not the word 2 and >f)."""
    spell = []
    toks = _tokens(cmd, spell)
    lo, hi = 0, len(toks)
    while lo < hi and toks[lo] == ("op", "\n"):
        lo += 1
    while hi > lo and toks[hi - 1] == ("op", "\n"):
        hi -= 1
    if hi - lo > 1 and toks[hi - 1] == ("op", ";") and (toks[hi - 2][0] == "word" or toks[hi - 2] == ("op", ")")):
        hi -= 1  # a final ; ends the last command as the end of the text does (a ; after another operator does not)
    return "".join(spell[k] if k == lo or toks[k - 1][0] == "fd" else " " + spell[k] for k in range(lo, hi))


def expands(cmd):
    """True when a word of the in-grammar `cmd` holds an expansion (an allowed $ expansion, quoted or not, or an
    unquoted * ? or ~ anywhere in the word): its values are known only when it runs, so two runs of it are never
    known to pass the same words (\npytest $LINENO and pytest $LINENO differ)."""
    found = []
    _tokens(cmd, expands=found)
    return bool(found)


def _names(words):
    """True when the word values hold a word whose last path part is gh, then run, then rerun (or glab, ci,
    retry), in that order at any positions, each expansion read as empty."""
    vals = [w.replace("\0", "") for w in words]
    for names in CI_RERUN:
        k = 0
        for v in vals:
            if k < 3 and (os.path.basename(v) if k == 0 else v) == names[k]:
                k += 1
        if k == 3:
            return True
    return False


def ci_rerun(cmd):
    """True when a simple command of `cmd` names a CI rerun command (see _names), and always when `cmd` is
    outside the grammar: the hook cannot parse it, so a CI rerun cannot be ruled out (a possible CI rerun)."""
    cmds = parse(cmd)
    return cmds is None or any(_names(words) for words, _ in cmds)


def _command(words):
    """`words` after leading ! { } and the keywords if then elif else fi do done while until."""
    i = 0
    while i < len(words) and words[i] in SKIPPED:
        i += 1
    return words[i:]


def quiet(words, writes):
    """True when one simple command (its words, and whether a redirection writes a file) is read-only: it writes
    no file and, after the skipped keywords, nothing remains or its command word holds no expansion and is on
    the read-only list (git and gh with a listed subcommand, env with no argument, a for NAME [in ...] header)."""
    w = _command(words)
    if writes:
        return False
    if not w:
        return True
    if "\0" in w[0]:
        return False
    name = os.path.basename(w[0])
    if name in READ_ONLY:
        return True
    if name == "git" and len(w) > 1 and w[1] in READ_ONLY_GIT:
        return True
    if name == "gh" and tuple(w[1:3]) in READ_ONLY_GH:
        return True
    if name == "env":
        return len(w) == 1
    return w[0] == "for" and len(w) > 1 and _NAME_RE.fullmatch(w[1]) is not None and (len(w) == 2 or w[2] == "in")


def read_only(cmd):
    """True when `cmd` is inside the grammar and every simple command in it is read-only (see quiet)."""
    cmds = parse(cmd)
    return cmds is not None and all(quiet(words, writes) for words, writes in cmds)


def changes_beside_check(cmd):
    """True when a simple command in `cmd` that is not itself a check command is not read-only and not cd,
    pushd, popd, set or tee (an install or an edit on the same line as the check, as in
    `pip install -e . && pytest -q`), or when `cmd` is outside the grammar."""
    cmds = parse(cmd)
    if cmds is None:
        return True
    for words, writes in cmds:
        first = os.path.basename((_command(words) or ("",))[0])
        if not CHECK_RE.search(" " + " ".join(words).replace("\0", "")) and not quiet(words, writes) \
                and first not in BESIDE_CHECK:
            return True
    return False


def failed(event, response):
    if event == "PostToolUseFailure":
        return True
    if isinstance(response, dict):
        if response.get("interrupted") is True:
            return True
        for k in ("exit_code", "exitCode", "returncode", "returnCode"):
            v = response.get(k)
            if type(v) is int and v != 0:
                return True
    return False


def conclusive(msg):
    """True when `msg` holds a conclusive-pass phrase that is not directly after a negation."""
    for m in CONCLUSIVE_RE.finditer(msg):
        if not _NEGATED_RE.search(msg[max(0, m.start() - 40):m.start()]):
            return True
    return False


def _control(c):
    """True for a control character: U+0000 to U+001F, and U+007F to U+009F (DEL and the C1 controls)."""
    return ord(c) < 32 or 127 <= ord(c) <= 159


_SHOWN_ESC = {"\\": "\\\\", "'": "\\'", "\t": "\\t", "\n": "\\n", "\r": "\\r"}


def shown(cmd):
    """`cmd` for a note, stripped, except that when the stripped text ends with a backslash the one character
    after it is kept: as written (spaces kept: quoting may need them) when it holds no control character, else
    as one bash $'...' string in which \\ ' tab, newline, carriage return and every other control character
    (U+0000 to U+001F, and U+007F to U+009F) are escaped (\\t, \\n, \\r, \\xHH below U+0080, and \\u00HH for
    U+0080 to U+009F, since \\xHH is one byte, and \\u00HH is that character in a UTF-8 locale). An
    in-grammar command never starts with $', so two checks that check_key keeps apart never show the same text
    unless both are cut at MAX_SHOWN characters."""
    one = cmd.strip()
    if one.endswith("\\"):
        one = cmd.lstrip()[:len(one) + 1]  # pytest a\<space> and pytest a\<tab> pass different words
    if any(_control(c) for c in one):
        one = "$'" + "".join(_SHOWN_ESC.get(c) or (("\\x%02x" if ord(c) < 128 else "\\u%04x") % ord(c)
                                                   if _control(c) else c) for c in one) + "'"
    return one if len(one) <= MAX_SHOWN else one[:MAX_SHOWN] + "..."


TAIL = (" A pass on a rerun with no deliberate change in between does not erase the earlier failure: record the "
        "failure, investigate it as an intermittent result, and do not present the later pass as conclusive "
        "verification.")
# The tail of a certain CI rerun's note: the hook knows only that a command naming a CI rerun was not reported as
# failed, so it asserts neither that a rerun started (an exit status of 0 can hide a refused call) nor that CI failed
# before.
CI_TAIL = (" This does not show that a rerun was started (a later command, || true, a background &, a ! or an if "
           "or while condition can hide the exit status of a refused call, with or without pipefail set, and so can "
           "a pipe under default bash options) or that CI failed before. If CI that had failed was rerun, the "
           "rerun's pass does not erase that failure: record the failure, investigate it as an intermittent result, "
           "and do not present the later pass as conclusive verification.")
# The tail of an uncertain rerun's note: it asserts no rerun and no earlier check failure, since neither may have
# happened.
UNCERTAIN_TAIL = (" This does not show that CI was rerun or that a check failed before. If CI, or a check that had "
                  "failed, was rerun, the rerun's pass does not erase that failure: record the failure, and do not "
                  "present the later pass as conclusive verification.")


def note(event, what):
    return dict(hookSpecificOutput=dict(hookEventName=event, additionalContext=(
        "RERUN NOTE (rerun-pass-check hook): " + what)))


def after_tool(payload, state, event):
    """Update `state` for one finished tool call; return the note text, or None."""
    tool = payload.get("tool_name")
    bad = failed(event, payload.get("tool_response"))
    if tool in EDIT_TOOLS:
        if not bad:  # a failed edit is not evidence of a change
            state["change"] += 1
        return None
    if tool != "Bash":
        return None
    ti = payload.get("tool_input")
    cmd = ti.get("command") if isinstance(ti, dict) else None
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    if ci_rerun(cmd):  # changes nothing locally, so it never separates two local runs
        if parse(cmd) is None:  # uncertain: a possible CI rerun, noted only, and a change unless it failed
            if not bad:
                state["change"] += 1
            return ("the hook could not parse a command (" + shown(cmd) + "), so it is a possible CI rerun: a CI "
                    "rerun cannot be ruled out" + (" (the call failed)" if bad else "") + "." + UNCERTAIN_TAIL)
        if bad:  # uncertain: the rerun may or may not have started (false && gh run rerun 7 starts none)
            return ("a command naming a CI rerun (" + shown(cmd) + ") failed, so it is a possible CI rerun: a CI "
                    "rerun cannot be ruled out, since the call may or may not have started one." + UNCERTAIN_TAIL)
        state["flags"] = (state["flags"] + [CI_FLAG + shown(cmd)])[-MAX_FLAGS:]
        return ("a command naming a CI rerun (" + shown(cmd) + ") was not reported as failed (no failure event, no "
                "interruption, and no nonzero integer exit-code field, where one is present)." + CI_TAIL)
    if not CHECK_RE.search(cmd):
        if not bad and not read_only(cmd):
            state["change"] += 1
        return None
    # In the grammar here (a command outside it is a possible CI rerun, handled above). A check whose words hold an
    # expansion takes their values only when it runs, so it is never compared with another run.
    if expands(cmd):
        if not bad and changes_beside_check(cmd):
            state["change"] += 1
        return None
    key = check_key(cmd)
    if len(json.dumps(key)) > MAX_KEY:  # bounds the state below STATE_MAX_BYTES
        key = "sha256:" + hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()
    checks = state["checks"]
    prior = checks.pop(key, None)
    if bad:
        checks[key] = ["fail", state["change"]]
    else:
        checks[key] = ["pass", state["change"]]
    while len(checks) > MAX_CHECKS:
        checks.pop(next(iter(checks)))
    rerun = not bad and isinstance(prior, list) and len(prior) == 2 and prior[0] == "fail" \
        and prior[1] == state["change"]
    if not bad and changes_beside_check(cmd):
        state["change"] += 1  # counted after this line's own comparison: the same line run twice is a rerun
    if rerun:
        state["flags"] = (state["flags"] + [LOCAL_FLAG + shown(cmd)])[-MAX_FLAGS:]
        return ("the check `" + shown(cmd) + "` failed earlier and passed on a rerun with no change recorded "
                "between." + TAIL)
    return None


def at_stop(payload, state, readable, path):
    if not readable or not state["flags"]:
        return None
    msg = payload.get("last_assistant_message")
    if not isinstance(msg, str) or not msg.strip():
        return None
    if DISCLOSED_RE.search(msg):
        state["flags"] = []
        state["blocks"] = 0
        save_state(path, state)
        return None
    if not conclusive(msg):
        return None
    active = payload.get("stop_hook_active") is True
    blocks = state["blocks"] if active else 0
    allowed = ("rerun-pass-check: turn end allowed; the final message presents a pass as conclusive, and this "
               "session ran a command that names a CI rerun and was not reported as failed, or saw a check pass on a "
               "rerun after a recorded failure")
    if blocks >= BLOCK_CAP:
        return dict(systemMessage=allowed + " (loop cap)")
    state["blocks"] = blocks + 1
    if not save_state(path, state) and active:
        return dict(systemMessage=allowed + " (refusal count cannot be saved)")
    return dict(decision="block", reason="rerun-pass-check: your final message presents a pass as conclusive, "
                "but this session ran a command that names a CI rerun and was not reported as failed, or saw a "
                "check pass on a rerun after a recorded failure (" + "; ".join(state["flags"][-3:]) + "). State any "
                "earlier failure and that it is unresolved (intermittent), and do not call the later pass conclusive "
                "(refusal " + str(blocks + 1) + " of at most " + str(BLOCK_CAP) + ").")


def decide(payload, env):
    event = payload.get("hook_event_name")
    path = state_path(payload, env)
    if event in ("PostToolUse", "PostToolUseFailure"):
        state, readable = load_state(path)
        what = after_tool(payload, state, event)
        save_state(path, state)  # a failed save still notes below
        return note(event, what) if what else None
    if event == "Stop":
        state, readable = load_state(path)
        return at_stop(payload, state, readable and path is not None, path)
    return None


def _emit_line(text):
    """Write one line to stdout; an output error is swallowed so the hook still exits 0."""
    try:
        print(text, flush=True)
    except Exception:
        try:
            fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(fd, sys.stdout.fileno())
            finally:
                os.close(fd)
        except Exception:
            os._exit(0)


def main(argv):
    if len(argv) > 1 and argv[1] == "--self-test":
        return _self_test()
    try:
        if _is_worker():
            return 0
        buf = getattr(sys.stdin, "buffer", None)
        payload = json.loads(buf.read() if buf is not None else sys.stdin.read())
        if not isinstance(payload, dict):
            return 0
        out = decide(payload, os.environ)
        if out is not None:
            _emit_line(json.dumps(out))
    except Exception:
        pass  # fail open: any error exits 0 with no output
    return 0


def _self_test():
    import shutil
    import subprocess
    import unittest

    here = os.path.abspath(__file__)
    # Commands bash 5.3 ran a CI rerun for (stub gh and glab on a stub-only PATH): the round 4 to 7 reproductions.
    ACCEPT = ('gh run rerun 12345 --failed', 'gh run rerun 7', 'gh run rerun 9', "echo '#' ; gh run rerun 1",
              'echo a#b; gh run rerun 1', "# it's\ngh run rerun 4", 'for id in 5 6; do gh run rerun $id; done',
              'if true; then gh run rerun 5; fi', 'time gh run rerun 5', '{ gh run rerun 5; }', '! gh run rerun 5',
              'x=`gh run rerun 5`', 'echo "$(gh run rerun 7)"', 'x=$(echo $(gh run rerun 4))',
              'echo `gh run rerun 3`', 'echo "a `gh run rerun 2` b"', "echo don\\'t && gh run rerun 1 && echo it\\'s",
              'gh run \\\n rerun 1', 'echo a # c \\\ngh run rerun 2', '2>/dev/null gh run rerun 7',
              'gh 2>/dev/null run rerun 7', '{fd}>/dev/null gh run rerun 7', '&>/dev/null gh run rerun 7',
              '2>&1 gh run rerun 7', '3>&- gh run rerun 7', "gh $'run' $'rerun' 7", 'gh $"run" $"rerun" 7',
              "$'gh' run rerun 1", "true;$'gh' run rerun 1", "printf $'%s\\'\\n' x; gh run rerun 123  # don't",
              "echo $'\\''; gh run rerun 1 #'", "gh $\\\n'run' rerun 1", 'time -p gh run rerun 1',
              'coproc gh run rerun 1', 'coproc N { gh run rerun 1; }', 'command gh run rerun 1',
              'exec -a x gh run rerun 1', 'command exec gh run rerun 1', 'gh 2>&1>/dev/null run rerun 7',
              '2>err.log gh run rerun 7', '{fd}>fdlog gh run rerun 7', 'gh 2>err.log run rerun 7',
              '>out.txt gh run rerun 7', '&>log gh run rerun 7', '>&2>/dev/null gh run rerun 7',
              '( gh 2>e run rerun 7 ) 2>e', '{ gh >x run rerun 7; }', 'gh run rerun 1 2>&-1',
              '{a[0]}>/dev/null gh run rerun 7', 'exec -aextra gh run rerun 1', 'exec -la x gh run rerun 1',
              'exec -a -l gh run rerun 1', 'builtin exec gh run rerun 1', 'time -p -- gh run rerun 1',
              'function f { gh run rerun 1; }; f', 'echo "$\\\n(gh run rerun 1)"', '{gh,} run rerun 1',
              'gh >&-1 run rerun 7', 'gh run rerun >& -1', 'gh 2>e run rerun 7 <&-x', 'X+=1 gh run rerun 1',
              'a[0]=1 gh run rerun 1', '$(true) gh run rerun 1', '$empty gh run rerun 1',
              '${e} command gh run rerun 1', '2>e X=1 gh run rerun 1', 'time -- gh run rerun 7',
              'builtin command gh run rerun 1', 'builtin builtin exec gh run rerun 1', 'gh >\r run rerun 7',
              'gh run rerun 1', 'echo x\\ # ; gh run rerun 7', 'true;`gh run rerun 1`',
              '2>e gh 3<>f run <<<x rerun 7', 'gh >&"-1" run rerun 7', 'gh >&"-"1 run rerun 7',
              'gh >&\\-1 run rerun 7', "gh >& ''-log run rerun 7", 'gh 1>&x run rerun 7', 'ID=3 glab ci retry 3',
              'gh run rerun 8', 'gh run rerun 5', '>&-gh run rerun 7', '<&-gh run rerun 7', '2>&-gh run rerun 7',
              'gh 2>&"1"- run rerun 7', "gh 2>&'1'- run rerun 7", 'gh 2>&1\\- run rerun 7', 'gh <&"0"- run rerun 7',
              'gh 3>&"1"- run rerun 7', 'gh {v}>&"1"- run rerun 7', '$@ gh run rerun 7', '"$@" gh run rerun 7',
              '$* gh run rerun 7', '$1 gh run rerun 7', '${x:-} gh run rerun 7', '$x$y gh run rerun 7',
              '$(:)$(:) gh run rerun 7', 'exec $x gh run rerun 7', 'command $x gh run rerun 7',
              'gh 2>&1""- run rerun 7', 'gh >"a b" run rerun $\'7\'', '$empty$other gh run rerun 7',
              '${empty:-} gh run rerun 7', '/usr/bin/gh run rerun 1', 'command -p -- gh run rerun 1',
              # Rounds 8 and 9: off-grammar reruns a search of the text missed; each is a possible CI rerun now.
              'echo "$(date)" # CI failed on main\\\ngh run rerun 123 --failed', 'echo "$(date)" a\\\\\ngh run rerun 7',
              'ls $((1))  # see C:\\temp\\\nglab ci retry 9', 'gh {run,rerun} 7', 'gh run {rerun,7}',
              '{gh,run,rerun} 7', 'glab {ci,retry} 7', 'gh run re$(true)run 7', 'gh run re`true`run 7',
              'g$(:)h run rerun 7', 'gh `true`run rerun 7', "$'\\x67'h run rerun 7", "$'\\147'h run rerun 7",
              "gh run re$'\\x72'un 7", 'gh run re${x:-${y}}run 7', 'g$(echo $(true))h run rerun 7',
              'g\\\nh run rerun; echo $', 'gh \\> run rerun 7', '$!gh run rerun 7', 'gh run re{run,try} 7',
              "#x\\\ng\\\nh run rerun 7", "g$'\\0x'h run rerun 7", "g$'\\400'h run rerun 7", "$'\\547h' run rerun 7")
    # Shapes that run no rerun but name one: accepted false notes.
    FLIPPED = ("command -v gh run rerun 1", "command -V gh run rerun 1", "exec -q gh run rerun 7",
               "exec --help gh run rerun 1", "exec -: gh run rerun 7", "builtin gh run rerun 1",
               "builtin -p exec gh run rerun 1", ">/dev/null ! gh run rerun 1", "2>e { gh run rerun 1; }",
               "if 2>e ! gh run rerun 1; then :; fi", "time 2>/dev/null -p gh run rerun 1", "<&x gh run rerun 1",
               "gh <&\"-1\" run rerun 7", "{$(:)}>/dev/null gh run rerun 1", "gh $'x' > run rerun 7",
               "x=$(echo gh) run rerun 1", "echo \"$(true); gh run rerun 7\"", "echo time -p gh run rerun 1",
               "echo gh run rerun", "echo \"note; gh run rerun is not needed, $(date)\"",
               "cat > n.md <<'EOF'\nwe'll use gh run rerun 4\nEOF")
    NON_RERUNS = ("echo 'gh run rerun 1'", "ls # gh run rerun 1", "gh run view 7 --log | grep rerun",
                  "git commit -m \"fix: gh run rerun is not needed\"", "gh > run rerun 7", "echo x #\n# gh run rerun 2",
                  "echo '$(gh run rerun 5)'", "gh > run rerun 7 # (", "echo \"use gh run rerun to retry\"",
                  "echo ok # ; gh run rerun 123", "echo \"\\$(gh run rerun 6)\"", "echo \"gh\" 'run rerun'")

    class T(unittest.TestCase):
        def setUp(self):
            self.tmp = tempfile.mkdtemp(prefix="rerun-pass-check-test-")
            self.env = dict(AIQT_HOOK_STATE_DIR=os.path.join(self.tmp, "st"))

        def tearDown(self):
            shutil.rmtree(self.tmp, ignore_errors=True)

        def bash(self, cmd, ok=True, **response):
            event = "PostToolUse" if ok else "PostToolUseFailure"
            return decide(dict(hook_event_name=event, session_id="s", tool_name="Bash",
                               tool_input=dict(command=cmd), tool_response=response), self.env)

        def edit(self):
            return decide(dict(hook_event_name="PostToolUse", session_id="s", tool_name="Edit",
                               tool_input=dict(file_path="/x")), self.env)

        def stop(self, msg, active=False):
            return decide(dict(hook_event_name="Stop", session_id="s", last_assistant_message=msg,
                               stop_hook_active=active), self.env)

        def test_01_local_rerun_is_noted(self):
            self.assertIsNone(self.bash("pytest -q tests", ok=False))
            self.assertIsNone(self.bash("git status"))
            out = self.bash("pytest  -q tests")
            self.assertIn("failed earlier and passed on a rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")

        def test_02_a_change_between_is_not_a_rerun(self):
            for change in (self.edit, lambda: self.bash("sed -i s/a/b/ f.py"), lambda: self.bash("pip install x")):
                self.assertIsNone(self.bash("make test", ok=False))
                change()
                self.assertIsNone(self.bash("make test"))
            self.assertIsNone(self.stop("All tests pass."))

        def test_03_ci_rerun_is_noted(self):
            out = self.bash("gh run rerun 12345 --failed")
            self.assertIn("CI rerun", out["hookSpecificOutput"]["additionalContext"])
            out = self.bash("gh run rerun 1", ok=False)  # noted anyway: the rerun may or may not have started
            self.assertIn("may or may not have started one", out["hookSpecificOutput"]["additionalContext"])

        def test_04_stop_refuses_a_conclusive_claim_with_a_cap(self):
            self.bash("gh run rerun 7")
            out = self.stop("CI is green now; the change is verified.")
            self.assertEqual(out["decision"], "block")
            n = 1
            for _ in range(10):
                out = self.stop("CI is green.", active=True)
                if "decision" not in out:
                    break
                n += 1
            self.assertEqual(n, BLOCK_CAP)
            allowed = ("rerun-pass-check: turn end allowed; the final message presents a pass as conclusive, and this "
                       "session ran a command that names a CI rerun and was not reported as failed, or saw a check "
                       "pass on a rerun after a recorded failure")
            self.assertEqual(out["systemMessage"], allowed + " (loop cap)")
            # Round 13 (codex MEDIUM): the same warning when the refusal count cannot be saved (no state path).
            state = dict(new_state(), flags=["CI rerun command: false && gh run rerun 7; true"], blocks=1)
            self.assertEqual(at_stop(dict(last_assistant_message="All tests pass.", stop_hook_active=True), state, True,
                                     None), dict(systemMessage=allowed + " (refusal count cannot be saved)"))

        def test_05_disclosure_clears_and_other_messages_pass(self):
            self.bash("npm test", ok=False)
            self.bash("npm test")
            self.assertIsNone(self.stop("I looked at the logs."))
            self.assertIsNone(self.stop("Tests pass on the rerun, but the first run failed; this is flaky."))
            self.assertIsNone(self.stop("All tests pass."))

        def test_06_failure_fields_in_a_posttooluse_response(self):
            self.bash("cargo test", exit_code=1)
            self.assertIsNotNone(self.bash("cargo test"))
            self.bash("go test ./...", interrupted=True)
            self.assertIsNotNone(self.bash("go test ./..."))

        def test_07_no_flag_no_state_no_message(self):
            self.assertIsNone(self.stop("All tests pass."))
            self.assertIsNone(self.bash("pytest"))
            self.assertIsNone(self.bash("pytest"))
            self.bash("tox", ok=False)
            self.bash("tox")
            self.assertIsNone(decide(dict(hook_event_name="Stop", session_id="s"), self.env))
            self.assertIsNone(decide(dict(hook_event_name="Stop", session_id="other",
                                          last_assistant_message="All tests pass."), self.env))
            self.assertIsNone(decide(dict(hook_event_name="SessionStart", session_id="s"), self.env))

        def test_08_without_state_it_still_notes_but_never_blocks(self):
            self.env = dict()
            self.assertIsNotNone(self.bash("gh run rerun 9"))
            self.assertIsNone(self.stop("All checks pass."))

        def test_09_bad_state_allows_at_stop(self):
            self.bash("gh run rerun 9")
            path = state_path(dict(session_id="s"), self.env)
            with open(path, "w", encoding="ascii") as fh:
                fh.write("\x7bbroken")
            self.assertIsNone(self.stop("All checks pass."))

        def test_10_classifiers(self):
            for c in ("pytest", "python3 -m pytest -x", "python3 -B -m unittest", "make -C d check",
                      "python3 tools/x.py --self-test", "yarn run test", "./gradlew build test", "pytest;",
                      "make test;", "tox;", "make -C d check;"):
                self.assertTrue(CHECK_RE.search(c), c)
            for c in ("ls tests", "echo test", "git commit -m test", "cat pytest.ini"):
                self.assertFalse(CHECK_RE.search(c), c)
            self.assertTrue(read_only("git log | head -5; ls && cat f"))
            self.assertFalse(read_only("ls; rm f"))
            self.assertTrue(CONCLUSIVE_RE.search("All the tests now pass."))
            self.assertFalse(CONCLUSIVE_RE.search("The tests still fail."))

        def edit_failed(self):
            return decide(dict(hook_event_name="PostToolUseFailure", session_id="s", tool_name="Edit",
                               tool_input=dict(file_path="/x")), self.env)

        def test_13_failed_operations_are_not_changes(self):
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNotNone(self.bash("gh run rerun 7", ok=False))  # noted, and not a change
            self.assertIsNone(self.edit_failed())
            self.assertIsNone(self.bash("sed -i s/a/b/ f.py", ok=False))
            self.assertIn("CI rerun", self.bash("gh run rerun 8")["hookSpecificOutput"]["additionalContext"])
            out = self.bash("pytest -q")
            self.assertIn("failed earlier and passed on a rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(self.stop("All tests pass.")["decision"], "block")

        def test_14_negative_counters_and_odd_flags_are_malformed(self):
            path = state_path(dict(session_id="s"), self.env)
            os.makedirs(os.path.dirname(path))
            for bad in (dict(blocks=-100), dict(change=-1), dict(flags=[7])):
                seed = dict(change=0, checks=dict(), flags=["CI rerun: gh run rerun 1"], blocks=0)
                seed.update(bad)
                with open(path, "w", encoding="ascii") as fh:
                    json.dump(seed, fh)
                self.assertFalse(load_state(path)[1], bad)
                for _ in range(5):
                    self.assertIsNone(self.stop("All tests pass.", active=True), bad)

        def test_15_redirection_env_quotes_and_negation(self):
            self.assertFalse(read_only("echo 'TIMEOUT=30' > settings.py"))
            self.assertFalse(read_only("cat a >> b"))
            self.assertFalse(read_only("env PYTHONPATH=. python3 fix.py"))
            self.assertTrue(read_only("env"))
            self.assertFalse(read_only("env -0 X=1 cat f"))  # env with an argument is a change
            self.assertTrue(read_only("grep x f 2>/dev/null; ls 2>&1 | head"))
            self.assertTrue(read_only("echo 'a > b'"))
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNone(self.bash("echo 'TIMEOUT=30' > settings.py"))
            self.assertIsNone(self.bash("pytest -q"))
            self.assertIsNone(self.stop("Fixed the timeout in settings.py; all tests pass."))
            self.assertIsNone(self.bash('echo "use gh run rerun to retry"'))
            self.assertTrue(ci_rerun("cd x && gh run rerun 3") and ci_rerun("ID=3 glab ci retry 3"))
            self.assertIsNotNone(self.bash("gh run rerun 1"))
            self.assertIsNone(self.stop("I have not verified the fix yet; waiting on CI."))
            self.assertIsNone(self.stop("CI hasn't been verified and the build is not yet green."))
            out = self.stop("The fix is verified.")
            self.assertEqual(out["decision"], "block")
            self.assertIn("ran a command that names a CI rerun and was not reported as failed, or saw a check pass on "
                          "a rerun after a recorded failure", out["reason"])

        def test_16_oversized_state_is_malformed(self):
            self.bash("gh run rerun 9")
            path = state_path(dict(session_id="s"), self.env)
            with open(path, "rb") as fh:
                body = fh.read()
            with open(path, "wb") as fh:  # valid JSON, padded to the limit: still read
                fh.write(body + b" " * (STATE_MAX_BYTES - len(body)))
            self.assertTrue(load_state(path)[1])
            with open(path, "ab") as fh:  # one byte more: never parsed from the cut prefix
                fh.write(b"X")
            self.assertFalse(load_state(path)[1])
            self.assertIsNone(self.stop("All checks pass."))

        def test_17_comments_and_shell_keywords(self):
            self.assertIsNone(self.bash("echo ok # ; gh run rerun 123"))
            self.assertIsNone(self.stop("All tests pass."))
            for c in ("echo '#' ; gh run rerun 1", "echo a#b; gh run rerun 1", "# it's\ngh run rerun 4",
                      "for id in 5 6; do gh run rerun $id; done", "if true; then gh run rerun 5; fi",
                      "time gh run rerun 5", "{ gh run rerun 5; }", "! gh run rerun 5", "x=`gh run rerun 5`"):
                self.assertTrue(ci_rerun(c), c)
            for c in ("echo 'gh run rerun 1'", "ls # gh run rerun 1", "echo x #\n# gh run rerun 2"):
                self.assertFalse(ci_rerun(c), c)

        def test_18_env_with_any_argument_is_a_change(self):
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNone(self.bash("env -u UNUSED"))
            self.assertIsNone(self.bash("pytest -q"))  # env with an argument counted as a change: a missed note
            self.assertIsNone(self.stop("All tests pass."))
            for c in ("env -u UNUSED", "env -uUNUSED", "env --unset UNUSED", "env --unset=UNUSED", "env --uns A",
                      "env -C /tmp", "env -iu A", "env -i -u A -- B=1", "env -C /tmp cat f", "env -u A rm f",
                      "env -S 'rm f'", "env --split-string=x", "env -iS x", "env -- rm f", "env -u A B=1 make"):
                self.assertFalse(read_only(c), c)
            self.assertTrue(read_only("env") and read_only("/usr/bin/env | grep PATH"))

        def test_19_a_change_beside_a_check_counts(self):
            for change in ("pip install -e . && pytest -q", "sed -i s/old/new/ app.py; pytest -q"):
                self.assertIsNone(self.bash("pytest -q", ok=False))
                self.assertIsNone(self.bash(change))
                self.assertIsNone(self.bash("pytest -q"))
            self.assertIsNone(self.stop("Installed the missing package; all tests pass."))
            self.assertIsNone(self.bash("pip install -e . && pytest -q", ok=False))
            self.assertIsNotNone(self.bash("pip install -e . && pytest -q"))  # the same line twice: a rerun
            self.assertIsNone(self.bash("pytest -q tests | tail -5", ok=False))
            self.assertIsNotNone(self.bash("pytest -q tests | tail -5"))
            self.assertFalse(read_only('echo "$(sed -i s/a/b/ f.py)"'))
            self.assertFalse(read_only("echo `sed -i s/a/b/ f.py`"))
            self.assertTrue(read_only('echo "$HOME is set"'))

        def test_20_cannot_is_a_negation(self):
            self.assertFalse(conclusive("This cannot be verified until CI finishes."))
            self.assertTrue(conclusive("It is verified."))

        def test_21_a_substitution_is_outside_the_grammar(self):
            out = self.bash('echo "$(true); gh run rerun 7"')  # prints the text; runs no rerun: a false note
            self.assertIn("possible CI rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertTrue(ci_rerun('echo "note; gh run rerun is not needed, $(date)"'))
            for c in ('echo "$(date) done"', 'echo "=== $(date) ==="', 'echo "$(ls | wc -l) files"',
                      'printf "%s\x5cn" "$(git rev-parse HEAD) is HEAD"'):
                self.assertFalse(read_only(c), c)
            self.assertTrue(read_only("echo '$(gh run rerun 5)'"))
            for c in ('echo "$(gh run rerun 7)"', "x=$(echo $(gh run rerun 4))", "echo `gh run rerun 3`",
                      'echo "a `gh run rerun 2` b"'):
                self.assertTrue(ci_rerun(c), c)
            self.assertFalse(ci_rerun('echo "\x5c$(gh run rerun 6)"'))
            self.assertIsNone(self.bash("pytest -q", ok=False))
            out = self.bash('echo "$(date) done"')  # off-grammar: always a possible CI rerun, and a change
            self.assertIn("could not parse a command", out["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(self.bash("pytest -q"))  # no local-rerun note, and the possible rerun is not kept
            self.assertIsNone(self.stop("All tests pass."))  # an uncertain rerun is note-only

        def test_22_an_escaped_space_or_quote_is_part_of_a_word(self):
            self.assertIsNotNone(self.bash("echo x\x5c # ; gh run rerun 7"))  # the # is inside the word
            self.assertEqual(self.stop("All tests pass.")["decision"], "block")
            for c in ("echo don\x5c't && gh run rerun 1 && echo it\x5c's", "gh run \x5c\n rerun 1",
                      "echo a # c \x5c\ngh run rerun 2", "/usr/bin/gh run rerun 1", "env X=1 gh run rerun 2"):
                self.assertTrue(ci_rerun(c), c)
            self.assertTrue(read_only("echo ''#x; ls") and read_only("echo hi >&2"))
            self.assertFalse(read_only("echo hi >&f"))

        def test_23_quoted_env_operands_are_arguments_too(self):
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNone(self.bash('env --unset="UNUSED"'))
            self.assertIsNone(self.bash("pytest -q"))
            for c in ("env -u'A'", 'env "-u" A', "env -C'/tmp' cat f", "env -f .env ls", "env --argv0=x cat f",
                      "env -a cat rm f", "env --argv0 cat rm f", "env -f .env rm f", '"env"x', "$x env"):
                self.assertFalse(read_only(c), c)

        def test_24_cd_or_tee_beside_a_check_is_not_a_change(self):
            for pre, post in (("cd /repo && ", ""), ("", " 2>&1 | tee /tmp/a.log")):
                self.assertIsNone(self.bash(pre + "pytest -q" + post, ok=False))
                self.assertIsNone(self.bash(pre + "pytest -q tests/test_a.py" + post))
                self.assertIsNotNone(self.bash(pre + "pytest -q" + post))
            self.assertEqual(self.stop("All tests pass.")["decision"], "block")
            self.assertTrue(changes_beside_check("source venv/bin/activate && pytest -q"))

        def test_25_a_command_the_lexer_cannot_read_is_a_change(self):
            for c in ("echo 'unclosed", "ls \x5c", "cat > f <<'EOF'\nit's\nEOF", "echo $((1 + 2))",
                      "diff <(ls) f", 'echo "$(git log --format="%h" -1)"', "case $x in a) ls;; esac", "echo $(ls",
                      "echo `ls"):
                self.assertIsNone(parse(c), c)
                self.assertFalse(read_only(c), c)
                self.assertTrue(changes_beside_check(c), c)
                self.assertTrue(ci_rerun(c), c)  # off-grammar: a possible CI rerun
            for c in ("case $x in a) gh run rerun 1;; esac", "cat <<EOF\ng'h' run rerun 1\nEOF",
                      "echo 'x; gh run rerun 1"):
                self.assertTrue(ci_rerun(c), c)
            self.assertIsNone(self.bash("pytest -q", ok=False))
            out = self.bash("cat > n.md <<'EOF'\nwe'll use gh run rerun 4\nEOF")
            self.assertIn("possible CI rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(self.bash("pytest -q"))  # the unreadable command also counts as a change
            self.assertIsNone(self.stop("All tests pass."))  # an uncertain rerun is note-only

        def test_26_long_commands_keep_the_state_readable(self):
            self.bash("gh run rerun 9")
            for n in range(MAX_CHECKS + 5):
                self.bash("pytest -q -k " + str(n) + " '" + "\u00e9" * 600 + "'")
            path = state_path(dict(session_id="s"), self.env)
            self.assertLessEqual(os.path.getsize(path), STATE_MAX_BYTES)
            self.assertTrue(load_state(path)[1])
            self.assertEqual((self.stop("All tests pass.") or dict()).get("decision"), "block")

        def test_27_grammar_table(self):
            z = "\0"
            for c, cmds in (
                    ("ls -la src", ((("ls", "-la", "src"), False),)),
                    ("echo 'a b' \"c d\" e\\ f \\$x", ((("echo", "a b", "c d", "e f", "$x"), False),)),
                    ("echo \"\\$ \\` \\\" \\\\ \\n \\\u00e9\r\"", ((("echo", "$ ` \" \\ \\n \\\u00e9\r"), False),)),
                    ("echo $HOME ${HOME}/x $10 $? $# $$ $! $@ $* $-", ((("echo", z, z + "/x", z + "0") + (z,) * 7,
                                                                       False),)),
                    ("echo \"a $x b\"$y", ((("echo", "a " + z + " b" + z), False),)),
                    ("a && b || c; d & e | f |& g\nh", tuple(((w,), False) for w in "abcdefgh")),
                    ("(ls) ; { ls; }", ((("ls",), False), (("{", "ls"), False), (("}",), False))),
                    ("ls # c ; rm f\nls;#x", ((("ls",), False), (("ls",), False))),
                    ("echo a#b ''#c # d \\\nls", ((("echo", "a#b", "#c"), False), (("ls",), False))),
                    ("ls >f", ((("ls",), True),)), ("ls >>f 2>/dev/null", ((("ls",), True),)),
                    ("ls >|f", ((("ls",), True),)), ("ls &>f", ((("ls",), True),)),
                    ("ls &>>/dev/null 2>&1 1>&2 <&0 >&-", ((("ls",), False),)),
                    ("ls >&/dev/null", ((("ls",), False),)), ("ls >& /dev/null", ((("ls",), False),)),
                    ("ls >\"/dev/null\"", ((("ls",), True),)), ("cat <f <<<x 0<g", ((("cat",), False),)),
                    ("echo x2>f 2&>/dev/null", ((("echo", "x2", "2"), True),)),
                    ("\"2\">f ls", ((("2", "ls"), True),)), (">f", (((), True),)),
                    ("[ -f x ] && [[ -n y ]]", ((("[", "-f", "x", "]"), False), (("[[", "-n", "y", "]]"), False))),
                    ("! ls", ((("!", "ls"), False),)),
                    ("echo \"a!b\" a\\!b '{'", ((("echo", "a!b", "a!b", "{"), False),)),
                    ("echo a,b ~/x *.py a?c ]:=+@%^-", ((("echo", "a,b", "~/x", "*.py", "a?c", "]:=+@%^-"), False),)),
                    ("echo \\\u00e9 '\u00e9\r'", ((("echo", "\u00e9", "\u00e9\r"), False),)),
                    ("for f in a b; do cat $f; done", ((("for", "f", "in", "a", "b"), False), (("do", "cat", z), False),
                                                       (("done",), False))),
                    ("gh > run rerun 7", ((("gh", "rerun", "7"), True),)), ("", ()), ("ls;", ((("ls",), False),))):
                self.assertEqual(parse(c), cmds, c)
            for c in ("ls\0", "cat <<EOF\nx\nEOF", "cat <<-EOF\nx\nEOF", "ls <>f", "case x in x) ls;; esac",
                      "case x in x) ls;& esac", "((x=1))", "f() { ls; }", "echo @(a)", "ls\r", "echo \u00e9",
                      "a[0]=1 ls", "{a,b}", "echo x}", "{fd}>f ls", "echo a!b", "ls \\\n-l", "ls \\", "echo 'a",
                      "echo \"a\\\nb\"", "echo \"`ls`\"", "echo \"a", "echo \"$(ls)\"", "echo $(ls)", "echo $((1))",
                      "echo ${x:-y}", "echo ${#x}", "echo $'a'", "echo $\"a\"", "echo $", "echo \"$\"", "echo `ls`",
                      "echo \\\\`ls`", "ls 10>f", "ls >", "ls > ; ls", "ls > # c", "ls >&", "ls >&\"1\"", "ls >&f",
                      "ls >&1-", "ls >&-1", "ls 2>&/dev/null", "ls <&/dev/null", "ls >&\"/dev/null\"", "diff <(ls) f",
                      "ls >>& f", "{gh,} run rerun 1"):
                self.assertIsNone(parse(c), c)
                self.assertFalse(read_only(c), c)
                self.assertTrue(changes_beside_check(c), c)

        def test_28_ci_acceptance(self):
            for c in ACCEPT:
                self.assertTrue(ci_rerun(c), c)
            for c in ("glab ci retry 4", "x | ./bin/glab -R a ci -x retry", "ls; glab $x ci \"retry\""):
                self.assertTrue(ci_rerun(c), c)

        def test_29_in_grammar_non_reruns(self):
            for c in NON_RERUNS:
                self.assertIsNotNone(parse(c), c)
                self.assertFalse(ci_rerun(c), c)
            for c in ("gh run", "gh rerun run", "run rerun gh", "gh-x run rerun", "gh/ run rerun", "glab ci",
                      "glab retry ci", "echo gh; run rerun", "\"g h\" run rerun", "cat gh.txt | grep run rerun",
                      "ls # ${x:-}gh run rerun"):
                self.assertFalse(ci_rerun(c), c)
            for c in ("cat <<EOF\nnothing here\nEOF", "echo $(gh run view 7)", "echo ${x:-gh} run", "g`h` run"):
                self.assertIsNone(parse(c), c)
                self.assertTrue(ci_rerun(c), c)  # off-grammar: a possible CI rerun (a false note)

        def test_30_read_only_table(self):
            for c in ("ls -la", "cat README.md", "git status", "git diff HEAD~1 -- f.py", "git log --oneline -5",
                      "grep -rn foo src | head -20", "ls 2>/dev/null", "wc -l *.py", "pwd", "gh run view 7 --log",
                      "gh pr checks 3", "echo $HOME", "head -50 f.py && tail -n 20 log.txt", "rg -n 'a b' src/",
                      "for f in *.py; do wc -l $f; done", "for f\ndo cat \"$f\"; done", "ls >&/dev/null",
                      "diff a b 2>&1 | head", "which python3", "date", "git show HEAD:f.py | head", "cat f | jq .x",
                      "stat f; du -sh .", "env", "/usr/bin/env", "while true; do sleep 1; done", "{ ls; } 2>/dev/null",
                      "if true; then cat f; elif true; then ls; else pwd; fi", "! grep -q x f", "ls <f", "grep x <<<y",
                      "git log | head -5; ls && cat f", "echo hi >&2", "ls >|/dev/null &>>/dev/null", "ls &", "(ls)",
                      "until true; do ls; done 2>/dev/null"):
                self.assertTrue(read_only(c), c)
            for c in ("ls >&f", "$x ls", "${x} ls", "\"$x\" ls", "l$x", "env -u A", "env X=1", "X=1 ls", "ls >f",
                      "ls >\"/dev/null\"", "ls >/dev/null$x", "echo \"$(date)\"", "cat <<EOF\nx\nEOF", "ls; rm f",
                      "git commit -m x", "git $x status", "gh run rerun 7", "gh pr merge 3", "time ls", "for",
                      "for 1 in a", "for f of a", "for $f in a", "ls 2>/dev/null >f", "ls; >f", "[ -f x ]",
                      "true && sed -i s/a/b/ f", ":"):
                self.assertFalse(read_only(c), c)

        def test_31_bash_differential(self):
            bash = "/usr/bin/bash"
            if not os.access(bash, os.X_OK):
                self.skipTest("no /usr/bin/bash")
            stubs, log = os.path.join(self.tmp, "bin"), os.path.join(self.tmp, "log")
            os.makedirs(stubs)
            for name in ("gh", "glab"):
                with open(os.path.join(stubs, name), "w", encoding="ascii") as fh:
                    fh.write("#!" + bash + "\nprintf '%s\\037' " + name + " \"$@\" >> \"$STUB_LOG\"\n"
                             "printf '\\n' >> \"$STUB_LOG\"\n")
                os.chmod(os.path.join(stubs, name), 0o755)

            def runs(cmd):  # whether bash, with only the stubs on PATH, runs gh run rerun or glab ci retry
                cwd = tempfile.mkdtemp(dir=self.tmp)
                if os.path.exists(log):
                    os.unlink(log)
                try:
                    subprocess.run([bash, "--noprofile", "--norc", "-c", cmd], cwd=cwd, stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=5, env=dict(PATH=stubs, HOME=cwd, STUB_LOG=log))
                except subprocess.TimeoutExpired:
                    pass
                if not os.path.exists(log):
                    return False
                with open(log, encoding="utf-8", errors="replace") as fh:
                    calls = [line.split("\x1f")[:-1] for line in fh.read().split("\n")]
                return any(_names(a) and os.path.basename(a[0]) in ("gh", "glab") for a in calls if a)

            self.assertTrue(runs("gh run rerun 7") and runs("glab ci retry 7"))
            # A command that names a path or could reach a real gh (command -p, PATH) is never run here.
            safe = [c for c in ACCEPT + FLIPPED + NON_RERUNS if "/" not in c.replace("/dev/null", "")
                    and "PATH" not in c and not re.search(r"\bcommand\b[^\n;&|]*\s-\w*p", c)]
            self.assertGreater(len(safe), 100)
            for c in safe:
                if runs(c):
                    self.assertTrue(ci_rerun(c), c)
            for c in NON_RERUNS:
                self.assertFalse(runs(c), c)

        def test_32_after_tool_sequences(self):
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNone(self.bash("ls >&/dev/null"))
            self.assertIn("failed earlier", self.bash("pytest -q")["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(self.bash("pytest -q", ok=False))
            out = self.bash("gh 2>&1\"\"- run rerun 7")
            self.assertIn("possible CI rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(self.bash("pytest -q"))  # the command outside the grammar counted as a change
            self.assertIsNone(self.bash("pytest -q", ok=False))
            out = self.bash("echo \"$(date)\"")
            self.assertIn("possible CI rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(self.bash("pytest -q"))  # a change too: the off-grammar command's own note stands
            out = self.bash("$@ gh run rerun 7")
            self.assertIn("a command naming a CI rerun ($@ gh run rerun 7) was not reported as failed (no failure",
                          out["hookSpecificOutput"]["additionalContext"])
            self.assertTrue(changes_beside_check(">f && pytest -q") and changes_beside_check("$x; pytest -q"))
            self.assertFalse(changes_beside_check("ls; pytest -q") or changes_beside_check("pytest $ARGS"))
            self.assertFalse(changes_beside_check("if true; then cd d; fi && pytest -q | tee log"))

        def test_33_accepted_false_notes_and_narrowed_read_only(self):
            for c in FLIPPED:
                self.assertTrue(ci_rerun(c), c)
            for c in ("env -0 X=1 cat f", "env -u UNUSED", "env -C /tmp cat f", "echo \"$(date) done\"",
                      "echo \"${HOME:-a b}\"", "echo \"$'x'\"", "ls >&1-", "gh 2>&\"-1\" run rerun 7", "ls <&x; ls"):
                self.assertFalse(read_only(c), c)

        def test_34_off_grammar_is_always_a_possible_rerun(self):
            # x + " run rerun; echo $" is off-grammar (a lone $): flagged whatever x is, with no search of the text.
            for x in ("g${x}h", "g$m'h'", "g\\h", "g`h", "g$(echo $(true))h", "$'\\x67'h", "g${x:-${y}}h", "{gh,}",
                      "g${x{}'h'", "g$_h", "g$Ah", "g$'\\x67'", "$'\\x6G'h", "g$'\\1'h", "xgh", "ghx", "", "ls"):
                self.assertIsNone(parse(x + " run rerun; echo $"), x)
                self.assertTrue(ci_rerun(x + " run rerun; echo $"), x)
            for c in ("echo $", "ls \\", "cat <<EOF\nx\nEOF", "echo $((1))", "echo 'a", "ls\0"):
                self.assertIsNone(parse(c), c)
                self.assertTrue(ci_rerun(c), c)
            pad = "ls " + "a" * (MAX_PARSE - 3)  # the longest command the grammar reads
            self.assertEqual(parse(pad), ((("ls", "a" * (MAX_PARSE - 3)), False),))
            self.assertTrue(read_only(pad))
            self.assertFalse(ci_rerun(pad))
            for c in (pad + "a", pad + " ", "gh run rerun 7 " + "a" * MAX_PARSE):  # one character too long
                self.assertIsNone(parse(c), len(c))
                self.assertTrue(ci_rerun(c), len(c))
                self.assertFalse(read_only(c), len(c))
                self.assertTrue(changes_beside_check(c), len(c))

        def test_35_reader_witnesses(self):
            # Each assertion pins a reader detail that a mutation sweep found no other test for.
            z = "\0"
            for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./:=+,@%^-~*?]#":
                self.assertEqual(parse("echo a" + ch), ((("echo", "a" + ch), False),), ch)
            for op in ("<<<", "&>>", "&>", ">>", ">|", ">&", "<&", "&&", "||", "|&", ">", "<", ";", "&", "|", "(",
                       ")", "\n"):
                self.assertEqual(_tokens("ls " + op + "f"), [("word", "ls", "ls"), ("op", op), ("word", "f", "f")])
            for op in ("<<", "<>", ";;", ";&", "(("):
                self.assertRaises(_Unsupported, _tokens, "ls " + op + "f")
            self.assertEqual(_tokens("$x"), [("word", z, None)])
            self.assertEqual(parse("ls\t-l \"\\x\" 2>&1 | tee f"),
                             ((("ls", "-l", "\\x"), False), (("tee", "f"), False)))
            for x in ("$_", "$xm", "$x0", "$x5", "$x_", "$_a", "$A", "$Z", "$a", "$z9_", "$0", "$9", "$?", "$#", "$$",
                      "$!", "$@", "$*", "$-", "${x}", "${_}", "${xm}", "${x0}", "${x5}", "${x_}", "${A}",
                      "${b}", "${_a}", "${ab}", "${a1}", "${a_b}", "${Z9}"):
                self.assertEqual(parse("echo " + x), ((("echo", z), False),), x)
            self.assertEqual(parse("echo $x- $Agh"), ((("echo", z + "-", z), False),))
            for c in ("echo $:", "echo ${5}", "echo ${x-}", "echo ${x:}", "echo ${1}", "echo ${A=}",
                      'echo "' + z + '"', "echo '" + z + "'", "#" + z, "!''", "ls >&12"):
                self.assertIsNone(parse(c), c)
            for a in ("A", "M", "Z", "z", "_", "B", "_x", "xA", "xM", "aB", "ab", "xm", "a1", "x0", "x5", "x_", "a_b"):
                self.assertTrue(read_only("for " + a + " in a; do ls; done"), a)
            for a in ("x-", "x:", "a=", "1", "x]", '"\\x"'):
                self.assertFalse(read_only("for " + a + " in a; do ls; done"), a)
            for c in ("ls >\\/dev/null", "ls >'/dev/null'"):
                self.assertEqual(parse(c), ((("ls",), True),), c)
            self.assertEqual(parse("ls \\> f"), ((("ls", ">", "f"), False),))
            self.assertTrue(read_only("echo \\a") and read_only("ls \\> f") and read_only("ls &&>&2 cat f"))
            for c in ("/$x/ls", "$x/ls", "! x status", "rm run view", "git", "gh", "gh run", "gh view run", "gh pr",
                      "ls &&x", "ls ||x", "ls |&x", "git '' x", "git x status"):
                self.assertFalse(read_only(c), c)
            for w in ("cat less more head tail grep egrep fgrep rg ls pwd echo printf wc date sleep true file stat "
                      "which type diff cmp sha256sum md5sum du df id whoami uname jq tree").split():
                self.assertTrue(read_only(w + " x"), w)
            for w in "! { } if then elif else fi do done while until".split():
                self.assertTrue(read_only(w + " ls"), w)
            for w in "status log diff show rev-parse blame ls-files".split():
                self.assertTrue(read_only("git " + w), w)
            for w in ("run view", "run list", "run watch", "pr view", "pr checks"):
                self.assertTrue(read_only("gh " + w + " 7"), w)
            for c in ("cd d", "pushd d", "popd", "set -e", "tee f", "ls <f"):
                self.assertIs(changes_beside_check(c + " && pytest -q"), False, c)
            for c in ("--self-test", "pyt${x}est", "./gradlew build test", "ls <f"):
                self.assertIs(changes_beside_check(c), False, c)
            self.assertIs(_names(("ls",)), False)
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNone(self.bash("cd d"))  # not a check and not read-only: a change
            self.assertIsNone(self.bash("pytest -q"))
            self.assertIsNone(self.bash("rm f", ok=False))
            self.assertIsNone(self.bash("rm f"))  # not a check: never compared as one
            tail = (" A pass on a rerun with no deliberate change in between does not erase the earlier failure: "
                    "record the failure, investigate it as an intermittent result, and do not present the later "
                    "pass as conclusive verification.")
            self.assertIsNone(self.bash("pytest -q", ok=False))
            # A certain CI rerun's tail asserts neither a rerun nor an earlier failure (round 12, claude MEDIUM-1 and
            # MINOR-3); an uncertain rerun's tail asserts no rerun and no earlier check failure.
            ci_tail = (" This does not show that a rerun was started (a later command, || true, a background &, a ! "
                       "or an if or while condition can hide the exit status of a refused call, with or without "
                       "pipefail set, and so can a pipe under default bash options) or that CI failed before. If CI "
                       "that had failed was rerun, the rerun's pass does not erase that failure: record the failure, "
                       "investigate it as an intermittent result, and do not present the later pass as conclusive "
                       "verification.")
            uncertain = (" This does not show that CI was rerun or that a check failed before. If CI, or a check that "
                         "had failed, was rerun, the rerun's pass does not erase that failure: record the failure, "
                         "and do not present the later pass as conclusive verification.")
            for cmd, what, end in (("pytest -q", "the check `pytest -q` failed earlier and passed on a rerun with no "
                                                 "change recorded between.", tail),
                                   ("gh run rerun 7", "a command naming a CI rerun (gh run rerun 7) was not "
                                    "reported as failed (no failure event, no interruption, and no nonzero integer "
                                    "exit-code field, where one is present).", ci_tail),
                                   ("gh {run,rerun} 7", "the hook could not parse a command (gh {run,rerun} 7), so "
                                    "it is a possible CI rerun: a CI rerun cannot be ruled out.", uncertain)):
                out = self.bash(cmd)["hookSpecificOutput"]["additionalContext"]
                self.assertEqual(out, "RERUN NOTE (rerun-pass-check hook): " + what + end)
            self.assertIn("recorded failure (local rerun: pytest -q; CI rerun command: gh run rerun 7). State any "
                          "earlier failure", self.stop("All tests pass.")["reason"])
            state = new_state()
            for cmd in ("pytest " + "a" * 503, "pytest " + "a" * 504):  # JSON lengths 512 and 513
                after_tool(dict(tool_name="Bash", tool_input=dict(command=cmd)), state, "PostToolUse")
            self.assertEqual(list(state["checks"]), ["pytest " + "a" * 503, "sha256:" + hashlib.sha256(
                ("pytest " + "a" * 504).encode()).hexdigest()])

        def test_36_failure_is_not_pass(self):
            state = new_state()
            payload = dict(tool_name="Bash", tool_input=dict(command="pytest -q"))
            self.assertIsNone(after_tool(payload, state, "PostToolUseFailure"))
            self.assertIsNone(after_tool(payload, state, "PostToolUseFailure"))
            self.assertEqual(state["flags"], [])

        def test_37_check_retention(self):
            state = new_state()
            for i in range(201):
                payload = dict(
                    tool_name="Bash",
                    tool_input=dict(command="pytest t" + str(i)),
                )
                after_tool(payload, state, "PostToolUseFailure")
            self.assertEqual(
                list(state["checks"]),
                ["pytest t" + str(i) for i in range(1, 201)],
            )

        def test_38_newline_is_not_continuation(self):
            # Inside the grammar a newline ends a simple command; with a lone $ the text is off-grammar instead.
            self.assertFalse(ci_rerun("echo g\nh run rerun"))
            self.assertTrue(ci_rerun("echo g\nh run rerun; echo $"))

        def test_39_round_9_reproductions_are_flagged(self):
            for c in ("#x\\\ng\\\nh run rerun 7", "g$'\\0x'h run rerun 7", "g$'\\400'h run rerun 7",
                      "$'\\547h' run rerun 7", "g" + "${x:-" * 40000 + "}" * 40000 + "h run rerun",
                      "gh run re{run,try} 7", "echo $; " + "gh run " * 800):
                self.assertIsNone(parse(c), c[:40])
                self.assertTrue(ci_rerun(c), c[:40])
                state = new_state()
                what = after_tool(dict(tool_name="Bash", tool_input=dict(command=c)), state, "PostToolUse")
                self.assertIn("so it is a possible CI rerun: a CI rerun cannot be ruled out.", what)
                self.assertEqual((state["flags"], state["change"]), ([], 1))  # noted only, never kept; a change
                state = new_state()  # a failed call is still noted (a rerun may have started), not a change
                what = after_tool(dict(tool_name="Bash", tool_input=dict(command=c)), state, "PostToolUseFailure")
                self.assertIn("a CI rerun cannot be ruled out (the call failed). This does not show", what)
                self.assertEqual((state["flags"], state["change"]), ([], 0))

        def test_40_bounds_and_pins(self):
            self.assertEqual(MAX_PARSE, 8192)  # the cap the docstring, README and residue state
            for cmd, kept in (("echo $; ", 0), ("gh run rerun ", MAX_FLAGS)):  # an uncertain rerun is never kept
                state = new_state()
                for i in range(MAX_FLAGS + 5):
                    after_tool(dict(tool_name="Bash", tool_input=dict(command=cmd + str(i))), state, "PostToolUse")
                self.assertEqual(len(state["flags"]), kept, cmd)
            self.assertEqual(state["flags"][-1], "CI rerun command: gh run rerun " + str(MAX_FLAGS + 4))
            state = new_state()
            state["checks"]["pytest -q"] = ["skip", 0]  # only a recorded failure makes a rerun
            self.assertIsNone(after_tool(dict(tool_name="Bash", tool_input=dict(command="pytest -q")), state,
                                         "PostToolUse"))

        def test_41_an_uncertain_rerun_is_note_only(self):
            path = state_path(dict(session_id="s"), self.env)
            # Round 10: the standard heredoc commit is off-grammar, and no rerun or failure happened.
            out = self.bash("git commit -m \"$(cat <<'EOF'\nFix parser\nEOF\n)\"")["hookSpecificOutput"]
            for s in ("could not parse", "a CI rerun cannot be ruled out", "does not show that CI was rerun"):
                self.assertIn(s, out["additionalContext"])
            self.assertNotIn("investigate it as an intermittent", out["additionalContext"])
            self.assertIsNone(self.stop("Committed; all tests pass."))
            self.assertIsNone(self.stop("Committed; all tests pass.", active=True))
            # Round 11 (codex 1): false && gh run rerun 7 exits 1 with no gh call; (claude MEDIUM-1): gh run rerun 7
            # refused with an HTTP 403. Neither started a rerun, and the note asserts none.
            for c in ("false && gh run rerun 7", "gh run rerun 7"):
                out = self.bash(c, ok=False)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("(" + c + ") failed, so it is a possible CI rerun: a CI rerun cannot be ruled out", out)
                for s in ("a CI rerun command ran", "was started", "investigate it as an intermittent"):
                    self.assertNotIn(s, out)
                self.assertIsNone(self.stop("All tests pass."), c)
                self.assertIsNone(self.stop("CI is green."), c)
            # Round 11 (codex 2, claude MINOR-1): real reruns outside the grammar are note-only, so no wording of a
            # final message clears or arms anything.
            for c in ("gh {run,rerun} 7", "gh run rerun \"$(gh run list -L1 --json databaseId -q .[0].databaseId)\""):
                self.assertIn("possible CI rerun", self.bash(c)["hookSpecificOutput"]["additionalContext"])
                for m in ("No CI reruns failed; all tests pass.", "The documentation says \"CI was not retriggered\". "
                          "All tests pass.", "It is not true that CI was not retriggered. All tests pass.",
                          "The scheduler has not retriggered the workflow, so I started it again by hand; CI is green.",
                          "I did not rerun CI until the fix landed; CI is green.", "The hook says to write \"I did "
                          "not rerun CI\" if that is so; CI is green.", "It is not true that I did not rerun CI. CI "
                          "is green.", "CI is green."):
                    self.assertIsNone(self.stop(m), m)
            self.assertEqual(load_state(path)[0]["flags"], [])

        def test_42_a_certain_rerun_keeps_the_refusal(self):
            path = state_path(dict(session_id="s"), self.env)
            for real, flag in ((lambda: self.bash("gh run rerun 7"), "CI rerun command: gh run rerun 7"),
                               (lambda: (self.bash("pytest -q", ok=False), self.bash("pytest -q")),
                                "local rerun: pytest -q")):
                self.bash("echo $; true")  # uncertain: never kept beside the certain rerun (codex 3)
                real()
                self.assertEqual(load_state(path)[0]["flags"], [flag])
                for m in ("No CI reruns failed; all tests pass.", "The documentation says \"CI was not retriggered\". "
                          "All tests pass.", "It is not true that CI was not retriggered. All tests pass."):
                    self.assertEqual(self.stop(m)["reason"], "rerun-pass-check: your final message presents a pass "
                                     "as conclusive, but this session ran a command that names a CI rerun and was "
                                     "not reported as failed, or saw a check pass on a rerun after a recorded "
                                     "failure (" + flag +
                                     "). State any earlier failure and that it is unresolved (intermittent), and do "
                                     "not call the later pass conclusive (refusal 1 of at most 2).", m)
                # Any disclosure word clears, wherever it stands, also inside a denial (claude MINOR-2).
                self.assertIsNone(self.stop("I did not rerun CI; all tests pass."))
                self.assertEqual(load_state(path)[0]["flags"], [])
                self.assertIsNone(self.stop("All tests pass."))

        def test_43_a_failed_ci_rerun_call_is_note_only(self):
            # gh run rerun N && gh run watch N --exit-status timing out: the call fails, the rerun may have started.
            for c, kw in (("gh run rerun 7 && gh run watch 7 --exit-status", dict(ok=False)),
                          ("gh run rerun 7; exit 3", dict(exit_code=3)), ("gh run rerun 7", dict(interrupted=True)),
                          ("echo $; gh run rerun 7 && false", dict(ok=False))):
                self.tearDown()
                self.setUp()
                out = self.bash(c, **kw)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("a CI rerun cannot be ruled out", out, c)
                self.assertNotIn("was started", out, c)
                self.assertIsNone(self.bash("gh run view 7"))  # read-only: green on a later look
                self.assertIsNone(self.stop("CI is green."), c)  # uncertain: a note, no refusal
            # Round 11 (claude MEDIUM-2): an off-grammar check that fails and then passes is uncertain: note-only,
            # and its note does not say both that the call failed and that nothing failed.
            self.tearDown()
            self.setUp()
            for ok in (False, True):
                out = self.bash("pytest -q -k \"$(cat sel.txt)\"", ok=ok)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("a CI rerun cannot be ruled out" + ("." if ok else " (the call failed)."), out)
                self.assertIn("If CI, or a check that had failed, was rerun", out)
                self.assertNotIn("does not mean that anything failed", out)
            self.assertIsNone(self.stop("All tests pass."))
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNotNone(self.bash("echo \"$(date)\"", ok=False))  # noted, but a failed call is no change
            out = self.bash("pytest -q")["hookSpecificOutput"]["additionalContext"]
            self.assertIn("failed earlier and passed", out)

        def test_44_documented_misses(self):
            # Pinned residuals: a blank command gets no note; ~, ~+ and ~- are tilde-expanded by bash and a hashed
            # name (hash -p) is looked up by bash, so these in-grammar reruns are missed.
            state = new_state()
            for c in ("\n" * 8193, " \t\n"):
                payload = dict(tool_name="Bash", tool_input=dict(command=c))
                self.assertIsNone(after_tool(payload, state, "PostToolUse"))
            self.assertEqual(state["flags"], [])
            for c in ("HOME=/s/gh; ~ run rerun 7", "PWD=/s/gh; ~+ run rerun 7", "OLDPWD=/s/gh; ~- run rerun 7",
                      "hash -p /s/gh x; x run rerun 7"):
                self.assertIsNotNone(parse(c), c)
                self.assertFalse(ci_rerun(c), c)
            # The name rule ignores whether the words ran: this call succeeds, starts no rerun, and is kept.
            out = self.bash("false && gh run rerun 7; true")["hookSpecificOutput"]["additionalContext"]
            self.assertIn("(false && gh run rerun 7; true) was not reported as failed (no failure event", out)
            self.assertNotIn("succeeded", out)  # round 15 (codex MINOR 2): the note says what the Stop messages say
            refusal = self.stop("All tests pass.")
            self.assertEqual(refusal["decision"], "block")
            # Round 13 (codex MEDIUM): at the loop cap the warning asserts no CI rerun either. Round 14 (codex MINOR 3,
            # claude MINOR-2): neither the refusal nor the warning says the call succeeded, only that it was not
            # reported as failed.
            self.stop("All tests pass.", active=True)
            warning = self.stop("All tests pass.", active=True)["systemMessage"]
            self.assertNotIn("after a rerun", warning)
            for text in (refusal["reason"], warning):
                self.assertNotIn("succeeded", text)
                self.assertIn("names a CI rerun and was not reported as failed", text)
            # Round 14 (gemini): the docstring says what the warning asserts (it names a local rerun it saw).
            doc = " ".join(__doc__.split())
            self.assertIn("the same as the refusal: it asserts no CI rerun either, and names a local rerun only", doc)
            self.assertNotIn("asserts no rerun either", doc)
            # Round 13 (claude MINOR-2): the hook reads only the event, interrupted and an integer exit-code field.
            for n, response in enumerate((dict(exit_code="1"), dict(exit_code=1.0), dict(exitStatus=1),
                                          dict(stderr="HTTP 403", is_error=True))):
                self.env = dict(AIQT_HOOK_STATE_DIR=os.path.join(self.tmp, "r" + str(n)))
                out = self.bash("gh run rerun 7", **response)["hookSpecificOutput"]["additionalContext"]
                self.assertIn("(gh run rerun 7) was not reported as failed (no failure event", out, response)
                self.assertNotIn("succeeded", out, response)
            # Round 12 (claude MEDIUM-1): an exit status of 0 can hide a gh call the server refused. Each command runs
            # the stub gh, which exits 1, and under default bash options exits 0 itself (REFUSED; with pipefail set,
            # the pipe exits 1), so it is kept as certain: its note says only that the call was not reported as
            # failed, and Stop refuses (a false refusal). The pipe under pipefail is note-only.
            # Round 14 (claude MINOR-4): a background &, a ! and an if condition exit 0 even with pipefail set.
            refused = (("gh run rerun 7 2>&1 | tail -5", (), 0), ("gh run rerun 7 || true", (), 0),
                       ("gh run rerun 7; echo done", (), 0), ("gh run rerun 7 2>&1 | tail -5", ("-o", "pipefail"), 1),
                       ("gh run rerun 7 &", ("-o", "pipefail"), 0), ("! gh run rerun 7", ("-o", "pipefail"), 0),
                       ("if gh run rerun 7; then :; fi", ("-o", "pipefail"), 0),
                       ("while gh run rerun 7; do :; done", (), 0),  # round 15 (claude NIT): a while condition too
                       ("while gh run rerun 7; do :; done", ("-o", "pipefail"), 0),
                       ("gh run rerun 7 || true", ("-o", "pipefail"), 0))
            for n, (c, opts, rc) in enumerate(refused):
                self.env = dict(AIQT_HOOK_STATE_DIR=os.path.join(self.tmp, "st" + str(n)))
                out = self.bash(c, exit_code=rc)["hookSpecificOutput"]["additionalContext"]
                if rc:
                    self.assertIn("(" + c + ") failed, so it is a possible CI rerun", out)
                    self.assertIsNone(self.stop("CI is green."), c)
                    continue
                self.assertIn("(" + c + ") was not reported as failed (no failure event, no interruption, and "
                              "no nonzero integer exit-code field, where one is present). This does not show that a "
                              "rerun was started (a later command, || true, a background &, a ! or an if or while "
                              "condition can hide the exit status of a refused call, with or without pipefail set", out)
                self.assertEqual(self.stop("CI is green.")["decision"], "block", c)
            # The exit statuses above are what bash gives with stub gh and tail (round 13, gemini: a missing bash is a
            # skip, never a silent pass).
            bash, stubs, log = "/usr/bin/bash", os.path.join(self.tmp, "bin"), os.path.join(self.tmp, "log")
            if not os.access(bash, os.X_OK):
                self.skipTest("no /usr/bin/bash")
            os.makedirs(stubs)
            for name, body in (("gh", 'echo "STUB gh $*" >> "$STUB_LOG"\necho "HTTP 403: Must have admin rights" >&2\n'
                                      'exit 1\n'), ("tail", "while read -r line; do :; done\n")):
                with open(os.path.join(stubs, name), "w", encoding="ascii") as fh:
                    fh.write("#!" + bash + "\n" + body)
                os.chmod(os.path.join(stubs, name), 0o755)
            for c, opts, rc in refused:
                if os.path.exists(log):
                    os.unlink(log)
                got = subprocess.run([bash, "--noprofile", "--norc"] + list(opts) + ["-c", c], cwd=self.tmp,
                                     stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
                                     env=dict(PATH=stubs, HOME=self.tmp, STUB_LOG=log)).returncode
                with open(log, encoding="ascii") as fh:
                    self.assertEqual((got, fh.read()), (rc, "STUB gh run rerun 7\n"), (c, opts))

        def test_45_a_state_without_the_current_version_is_discarded(self):
            # Round 12 (codex MAJOR, claude MINOR-1): an earlier revision kept a failed call as "CI rerun: ...", and
            # another kept "possible CI rerun: ..."; a state without STATE_VERSION is read as no earlier runs.
            path = state_path(dict(session_id="s"), self.env)
            legacy = dict(change=0, checks=dict(), blocks=0, flags=["CI rerun: false && gh run rerun 7",
                                                                    "possible CI rerun: echo $"])
            for version in (None, 1, "2", True, 2.0, 3):
                self.assertTrue(save_state(path, legacy if version is None else dict(legacy, version=version)))
                self.assertEqual(load_state(path), (new_state(), True), version)
                self.assertIsNone(self.stop("All tests pass."), version)
            # Repeating the failed call before Stop rewrites the state: still no refusal.
            self.assertTrue(save_state(path, legacy))
            self.assertIn("a CI rerun cannot be ruled out",
                          self.bash("false && gh run rerun 7", ok=False)["hookSpecificOutput"]["additionalContext"])
            self.assertEqual(load_state(path)[0], new_state())
            self.assertIsNone(self.stop("All tests pass."))
            # A recorded failure in a discarded state is forgotten too (a missed local-rerun note).
            self.assertTrue(save_state(path, dict(legacy, flags=[], checks={"pytest -q": ["fail", 0]})))
            self.assertIsNone(self.bash("pytest -q"))
            # A state of the current version keeps its certain flag, and the refusal count works on it.
            self.assertTrue(save_state(path, dict(new_state(), flags=["CI rerun command: gh run rerun 7"])))
            self.assertIn("recorded failure (CI rerun command: gh run rerun 7). State",
                          self.stop("All tests pass.")["reason"])
            self.assertIn("(refusal 2 of at most 2)", self.stop("All tests pass.", active=True)["reason"])
            self.assertIsNone(self.stop("It was flaky; all tests pass."))  # a disclosure resets the count too
            self.bash("gh run rerun 8")
            self.assertIn("(refusal 1 of at most 2)", self.stop("All tests pass.", active=True)["reason"])
            self.assertEqual(load_state(path)[0]["version"], STATE_VERSION)
            # Round 13 (codex MINOR): a current-version flag without a prefix this version keeps is malformed and
            # arms nothing; the known prefixes are kept (the state file is trusted within them).
            for flags, kept in ((["possible CI rerun: echo $"], False), (["CI rerun: gh run rerun 7"], False),
                                (["local rerun: pytest -q", "ci rerun command: x"], False), (["local rerun"], False),
                                (["local rerun: pytest -q", "CI rerun command: gh run rerun 7"], True)):
                self.assertTrue(save_state(path, dict(new_state(), flags=flags)))
                self.assertEqual(load_state(path), (dict(new_state(), flags=flags), True) if kept else
                                 (new_state(), False), flags)
                self.assertEqual(self.stop("All tests pass.") is None, not kept, flags)

        def test_46_the_check_key_keeps_quoting(self):
            # Round 12 (codex MEDIUM): pytest 'a  b' and pytest 'a b' pass different arguments. A stub pytest exits 1
            # for the first and 0 for the second: the pass is not a rerun of the failure.
            # The exit statuses rcs are what bash gives with that stub (checked last, reported as a skip without bash).
            c1, c2, rcs, bash = "pytest 'a  b'", "pytest 'a b'", [1, 0], "/usr/bin/bash"
            self.assertIsNone(self.bash(c1, exit_code=rcs[0]))
            self.assertIsNone(self.bash(c2, exit_code=rcs[1]))
            self.assertIsNone(self.stop("All tests pass."))
            # Blanks and comments between words do not count: the same words, quoting and all, are one check, and
            # the note shows the command with its spaces.
            out = self.bash("pytest  'a  b'  # again", exit_code=0)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("the check `pytest  'a  b'  # again` failed earlier and passed on a rerun", out)
            for a, b in (("pytest 'a b'", 'pytest "a b"'), ("pytest $A", "pytest $B"), ("pytest '*'", "pytest *"),
                         ("pytest x 2>f", "pytest x 2 >f"), ("pytest -q; ls", "pytest -q && ls"),
                         ("pytest ''", "pytest")):
                self.assertNotEqual(check_key(a), check_key(b), a)
            for a, b in (("pytest  -q\t-x", "pytest -q -x"), ("pytest -q # c", "pytest -q"),
                         ("pytest x 2>f", "pytest x  2>f")):
                self.assertEqual(check_key(a), check_key(b), a)
            self.assertEqual(check_key("pytest  'a  b'  2>&1|tail"), "pytest 'a  b' 2>& 1 | tail")
            # Round 13 (claude MINOR-1): leading and trailing newlines and a final ; after a word or ) do not count;
            # a ; after another operator, a leading ;, a final & and an inner operator do.
            for a in ("pytest -q\n", "\npytest -q", "\n\npytest -q;\n\n", "pytest -q;", "pytest -q ; # c",
                      "# c\npytest -q\n# d\n"):
                self.assertEqual(check_key(a), "pytest -q", a)
            self.assertEqual(check_key("( pytest -q );"), check_key("( pytest -q )"))
            for a, b in (("pytest -q\n;", "pytest -q"), ("; pytest -q", "pytest -q"), ("pytest -q &", "pytest -q"),
                         ("pytest -q; ls", "pytest -q\nls"), ("pytest -q &&\nls", "pytest -q && ls"),
                         ("pytest -q &;", "pytest -q &"), ("pytest -q |;", "pytest -q |")):  # round 14 (claude MINOR-1)
                self.assertNotEqual(check_key(a), check_key(b), a)
            self.assertIsNone(self.bash("pytest -q", exit_code=1))
            self.assertIn("the check `pytest -q` failed earlier and passed on a rerun",
                          self.bash("pytest -q\n", exit_code=0)["hookSpecificOutput"]["additionalContext"])
            # Round 13 (codex MINOR, claude MINOR-3): a tab, a newline or another control character is shown escaped,
            # in one $'...' string, so checks that check_key keeps apart are never shown as the same text.
            for cmd, text in (("pytest 'a\tb'", "$'pytest \\'a\\tb\\''"), ("pytest 'a\nb'", "$'pytest \\'a\\nb\\''"),
                              ("pytest 'a\rb'", "$'pytest \\'a\\rb\\''"),
                              ("pytest 'a\x0bb'", "$'pytest \\'a\\x0bb\\''"),
                              ("pytest 'a\x7f' a\\ b", "$'pytest \\'a\\x7f\\' a\\\\ b'"),
                              ("pytest 'a b' a\\ b", "pytest 'a b' a\\ b"), ("  pytest -q\n", "pytest -q"),
                              ("pytest 'a\x85b'", "$'pytest \\'a\\u0085b\\''"),  # round 14 (claude MINOR-3): C1 too
                              ("pytest 'a\x9bb'", "$'pytest \\'a\\u009bb\\''"),
                              ("pytest '\xa0\xe9'", "pytest '\xa0\xe9'")):
                self.assertEqual(shown(cmd), text, cmd)
            group = ("pytest 'a\tb'", "pytest 'a\nb'", "pytest 'a b'", "pytest 'a\\tb'", "pytest 'a\\nb'",
                     "pytest \\\t", "pytest \\$'\\t'", "pytest $'\\t'x", "pytest 'a\rb'", "pytest 'a\\rb'",
                     "pytest a\\ ", "pytest a\\\t", "pytest a")
            ok = [c for c in group if parse(c) is not None]
            self.assertGreater(len(ok), 7)
            self.assertEqual(len({check_key(c) for c in ok}), len(ok))
            self.assertEqual(len({shown(c) for c in ok}), len(ok))
            self.assertIsNone(self.bash("pytest 'a\tb'", exit_code=1))
            self.assertIn("the check `$'pytest \\'a\\tb\\''` failed earlier",
                          self.bash("pytest 'a\tb'", exit_code=0)["hookSpecificOutput"]["additionalContext"])
            if not os.access(bash, os.X_OK):
                self.skipTest("no /usr/bin/bash")  # round 13 (gemini): a missing bash is a skip, never a silent pass
            stubs = os.path.join(self.tmp, "bin")
            os.makedirs(stubs)
            with open(os.path.join(stubs, "pytest"), "w", encoding="ascii") as fh:
                fh.write("#!" + bash + '\n[ "$1" = "a b" ]\n')
            os.chmod(os.path.join(stubs, "pytest"), 0o755)
            self.assertEqual([subprocess.run([bash, "--noprofile", "--norc", "-c", c], cwd=self.tmp,
                                             stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
                                             env=dict(PATH=stubs, HOME=self.tmp)).returncode for c in (c1, c2)], rcs)
            # Round 15 (claude MINOR): read by bash in a UTF-8 locale, each $'...' shown text is the command that
            # ran (\xHH is one byte, so C1 controls are spelled \u00HH).
            # Round 16 (codex MEDIUM): bash decodes \u00HH only in a locale it can use, so a probe that does not
            # use shown() decides first whether this host has one; without it the round trip is a skip.
            utf8 = "C.UTF-8"
            probe = subprocess.run([bash, "--noprofile", "--norc", "-c", "printf %s $'\\u00e9'"], cwd=self.tmp,
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
                                   env=dict(PATH=stubs, HOME=self.tmp, LC_ALL=utf8))
            if (probe.returncode, probe.stdout, probe.stderr) != (0, b"\xc3\xa9", b""):
                self.skipTest("bash cannot use the " + utf8 + " locale")
            for cmd in ("pytest 'a\x85b'", "pytest 'a\x9b\x7f\x0b\tb'\n", "pytest '\x80\x9f\xa0'"):
                got = subprocess.run([bash, "--noprofile", "--norc", "-c", 'printf %s ' + shown(cmd)], cwd=self.tmp,
                                     stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
                                     env=dict(PATH=stubs, HOME=self.tmp, LC_ALL=utf8)).stdout
                self.assertEqual(got, cmd.strip().encode("utf-8"), cmd)

        def test_47_an_expanding_check_is_never_compared(self):
            # Round 14 (codex MEDIUM): "\npytest $LINENO" passes 2 and "pytest $LINENO" passes 1; a stub pytest
            # exits 0 only for 1, so the pass is not a rerun of the failure. A check whose words hold an expansion
            # is never compared with another run (a missed note, never a false one).
            c1, c2, bash = "\npytest $LINENO", "pytest $LINENO", "/usr/bin/bash"
            path = state_path(dict(session_id="s"), self.env)
            self.assertIsNone(self.bash(c1, exit_code=1))
            self.assertIsNone(self.bash(c2, exit_code=0))
            self.assertIsNone(self.stop("All tests pass."))
            for c in ("pytest $X", 'pytest "${X}"', "pytest tests/test_*.py", "pytest t?.py", "pytest ~/t",
                      "pytest a=~", "pytest -q; echo $?", "pytest -q > $F", "make test;\npytest x*"):
                self.assertTrue(expands(c), c)
                self.assertIsNone(self.bash(c, exit_code=1), c)
                self.assertIsNone(self.bash(c, exit_code=0), c)
            self.assertIsNone(self.stop("All tests pass."))
            self.assertEqual(load_state(path)[0]["checks"], dict())
            for c in ("pytest '*' \"~\" '$X' \\? \"a\\$b\" \\~", "pytest -q", "make test;", "[ -f x ]"):
                self.assertFalse(expands(c), c)
            for c in ("pytest $(date)", "pytest `date`", "pytest ${X:-a}"):  # off-grammar: noted, never compared
                self.assertIn("could not parse", self.bash(c, exit_code=1)["hookSpecificOutput"]["additionalContext"])
            # Expansion-free commands keep the newline and final ; normalization, and (round 14, codex MINOR 2) a
            # check command followed by a final ; is recognized as one.
            for a, b in (("pytest", "pytest;"), ("make test", "make test;"), ("pytest -q", "\npytest -q\n")):
                self.assertIsNone(self.bash(a, exit_code=1), a)
                self.assertIn("the check `" + shown(b) + "` failed earlier and passed on a rerun",
                              self.bash(b, exit_code=0)["hookSpecificOutput"]["additionalContext"], b)
            # Round 15 (codex MINOR 1, claude MEDIUM): a change made beside an expanding check is still counted, so
            # the next pass of another check is no rerun; a read-only command beside one is no change.
            for beside, change in (('sed -i s/a/b/ app.py; pytest "$X"', 1),
                                   ("pip install -e . && pytest tests/*.py", 1), ('ls; pytest "$X"', 0)):
                self.assertIsNone(self.bash("pytest -q", exit_code=1), beside)
                before = load_state(path)[0]["change"]
                self.assertIsNone(self.bash(beside, exit_code=0), beside)
                self.assertEqual(load_state(path)[0]["change"] - before, change, beside)
                out = self.bash("pytest -q", exit_code=0)
                if change:
                    self.assertIsNone(out, beside)
                else:
                    self.assertIn("the check `pytest -q` failed earlier and passed on a rerun with no change recorded "
                                  "between", out["hookSpecificOutput"]["additionalContext"], beside)
            if not os.access(bash, os.X_OK):
                self.skipTest("no /usr/bin/bash")
            stubs = os.path.join(self.tmp, "bin")
            os.makedirs(stubs)
            with open(os.path.join(stubs, "pytest"), "w", encoding="ascii") as fh:
                fh.write("#!" + bash + '\necho "$1"\n[ "$1" = 1 ]\n')
            os.chmod(os.path.join(stubs, "pytest"), 0o755)
            got = [subprocess.run([bash, "--noprofile", "--norc", "-c", c], cwd=self.tmp, stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=5, env=dict(PATH=stubs, HOME=self.tmp))
                   for c in (c1, c2)]
            self.assertEqual([(p.returncode, p.stdout) for p in got], [(1, b"2\n"), (0, b"1\n")])

        def test_48_readme_paragraphs_are_wrapped(self):
            # Round 14 (claude NIT): the README paragraphs about this hook wrap like their neighbours.
            readme = os.path.join(os.path.dirname(here), "README.md")
            if not os.path.exists(readme):
                self.skipTest("no README.md beside the hook")
            with open(readme, encoding="utf-8") as fh:
                lines, inside, items = fh.read().split("\n"), False, []
            for n, line in enumerate(lines, 1):
                if line.startswith("- **`rerun-pass-check.py`**"):
                    inside = True
                    items.append([])
                elif not line.startswith("  "):
                    inside = False
                if inside:
                    self.assertLessEqual(len(line), 110, n)
                    items[-1].append(line)
            self.assertEqual(len(items), 2)
            # Round 15 (claude NIT): each Stop message also names a local rerun, so "only" is scoped to its CI clause.
            # Round 16 (codex MINOR): the README may quote old wording elsewhere, so both read the hook description.
            text = [" ".join(" ".join(item).split()) for item in items]
            desc = [t for t in text if t.startswith("- **`rerun-pass-check.py`** keeps an earlier failure in view")]
            self.assertEqual(len(desc), 1)
            self.assertIn("(of CI, each says only that a command naming a CI rerun was not reported as failed)",
                          desc[0])
            self.assertNotIn("(each says only", desc[0])

        def run_hook(self, payload, env):
            base = dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"))
            base.update(env)
            p = subprocess.run([sys.executable, "-I", "-S", "-B", here], input=payload, capture_output=True,
                               env=base, timeout=30)
            return p.returncode, p.stdout, p.stderr

        def test_11_process_contract(self):
            call = json.dumps(dict(hook_event_name="PostToolUse", session_id="p", tool_name="Bash",
                                   tool_input=dict(command="gh run rerun 5"))).encode()
            rc, out, err = self.run_hook(call, self.env)
            self.assertEqual((rc, err, out.count(b"\n")), (0, b"", 1))
            self.assertIn("additionalContext", json.loads(out)["hookSpecificOutput"])
            stop = json.dumps(dict(hook_event_name="Stop", session_id="p",
                                   last_assistant_message="All checks pass.")).encode()
            rc, out, err = self.run_hook(stop, self.env)
            self.assertEqual((rc, err, json.loads(out)["decision"]), (0, b"", "block"))
            for junk in (b"", b"\x7b", b"[]", b"\xff\xfe", b"null"):
                self.assertEqual(self.run_hook(junk, self.env), (0, b"", b""))
            self.assertEqual(self.run_hook(stop, dict(self.env, AIQT_HOOKS_WORKER="1")), (0, b"", b""))

        def test_12_source_house_rules(self):
            with open(here, "rb") as fh:
                src = fh.read()
            src.decode("ascii")
            for n, line in enumerate(src.split(b"\n"), 1):
                self.assertLessEqual(len(line), 120, n)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(T)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
