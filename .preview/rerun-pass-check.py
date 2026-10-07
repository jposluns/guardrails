#!/usr/bin/env python3
"""PostToolUse, PostToolUseFailure and Stop hook (rerun-pass-check): a rerun pass is not conclusive verification.

WHAT IT DOES
    A check that fails and then passes on a rerun with no deliberate change in between is an unresolved
    intermittent result: the earlier failure is recorded and investigated, and the later pass is not presented
    as conclusive verification (rerun-pass-is-still-failure). This hook watches for the two common shapes and
    keeps the earlier failure in view:

    1. A CI RERUN. A shell command that reruns a CI run (`gh run rerun`, `glab ci retry`, named or given by
       path, as the command of a simple command) is a rerun by definition. The command is the first word of a
       simple command after optional shell keywords (if, then, else, elif, fi, do, done, while, until, time
       and its -p, coproc and the NAME before its { group, !, {, }), NAME=VALUE assignments, a command or exec
       builtin prefix with its options (command -v or -V runs nothing), and an env prefix. The words come from
       the standard library's POSIX shlex lexer, which reads quotes, escapes, comments and operators as the
       shell does for the syntax this reading models (a # starts a comment only at the start of a word; an
       unquoted descriptor number or {name} directly before < or >, as in 2>/dev/null, is part of the
       redirection, never a word), and the body of each $(...) or backtick command substitution
       that is unquoted or in double quotes is read as a command line of its own, recursively; quoted text
       around a substitution stays quoted. After it runs, the hook adds a note to the assistant's context: the
       rerun's result does not erase the earlier failure, which is to be recorded and investigated.
    2. A LOCAL RERUN. A recognized check command (CHECK_RE: a test runner, `make test` or `make check`, a
       `--self-test` or `--check` run) that failed and then, run again with the identical command text, passed,
       with no change recorded between the two runs. After the passing run, the hook adds the same note.
    3. AT TURN END (Stop), while such a rerun is outstanding, a final message that presents a pass as
       conclusive (CONCLUSIVE_RE: "all tests pass", "CI is green", "verified", and similar, each not directly
       after a negation such as "not", "cannot", "n't", or "never") without naming the earlier failure (DISCLOSED_RE:
       "flaky", "intermittent", "rerun", "earlier failure", and similar) is refused once, with the reason. A final
       message that names it clears the outstanding reruns. A loop cap bounds the refusals: inside one
       continuous stop_hook_active run at most BLOCK_CAP, then the stop is allowed with a one-line warning.

    A CHANGE between two runs is any Write, Edit, MultiEdit, or NotebookEdit call that did not fail, and any
    other shell command that did not fail, is not a CI rerun, and is not read-only. A read-only command has
    every simple command start with a word on the short read-only list (READ_ONLY: cat, ls, grep, echo, git
    status, git diff, gh run view, and similar; env only when no command follows it, where an option operand
    such as -u NAME or -C DIR is not a command and -S supplies one) and redirects output only to /dev/null or
    another descriptor, so an install, a checkout, a sed, or an echo into a file between the runs counts as a
    deliberate change and no rerun is flagged. The words are read as in 1, so a substituted command counts.
    A command the lexer cannot fully tokenize (an unbalanced quote) or that uses syntax this reading does not
    model (a here-document, case, arithmetic or process substitution, a quote inside a double-quoted
    substitution, a backslash inside backticks, ANSI-C or locale quoting such as $'...' or $"...", which
    shlex would read as a literal $ and a plain quote, a ${...} expansion that is unclosed or holds a quote,
    a backslash, a backtick or a $ (unquoted, also a blank or an operator character), a [[ test split at an
    operator inside it) is read conservatively: it is a change,
    and when its text, with line continuations, a $ directly before a quote, quotes and backslashes
    removed, names a CI rerun command anywhere, it is also a possible CI rerun, noted and flagged like one.
    A check command line that also runs a simple command that is neither a check, nor read-only, nor cd,
    pushd, popd, set or tee (`pip install -e . && pytest -q`) counts as one
    change for later runs; it is counted after that line's own comparison, so the identical line run twice
    is still a rerun. A failed call is not a change, even one
    that changed something before it failed, and a CI rerun changes nothing locally. A failed run is a
    PostToolUseFailure call, or a PostToolUse call whose tool_response reports an interruption or a nonzero
    exit code field; any other PostToolUse call is a pass.

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
    counter or a non-text flag, is malformed and read as no state (it is never parsed from a cut prefix).
    Any error, an unreadable payload, or an unrecognized event exits 0 with no output. A worker process
    (AIQT_HOOKS_WORKER=1, or a legacy spelling) is skipped.

RESIDUAL COVERAGE
    It recognizes only the listed CI rerun commands and check commands, run through the shell tool; a rerun
    through a web page, a pushed empty commit, a retry option of the runner itself (pytest --reruns, a CI
    retry setting), a script that wraps the check, a rerun command whose name comes from an expansion ($VAR,
    a substitution's output) or that follows a wrapper such as sudo, sh -c, or env -S, or a changed command
    line (an added flag, another order) is not seen. Shell syntax that this reading neither models nor
    detects as unmodelled (bash extensions beyond those listed in 1 and above) can be misread, hiding or
    inventing a rerun. A command the lexer cannot read counts as a change, so a
    rerun across it (a here-document, notably) is not flagged (a missed note), and when it names a CI rerun
    command anywhere, even in a here-document body, it is flagged as a possible CI rerun (a false note). A
    failed call is never a change, so a fix made by a command that then failed reads as no change (a false
    note). A change made outside the tool calls it sees (by the user, another process, a
    background job) is not seen, so a fix applied that way reads as no change and the pass is flagged anyway
    (a false note); a read-only-looking command with a side effect, or a tee into a source file beside a
    check, reads as no change. A setup command that is not read-only beside a check (`source
    venv/bin/activate && pytest`, an export) counts as a change, so a later rerun of that check alone is not
    flagged (a missed note). The pass and fail reading rests on the event name and a few tool_response
    fields, not on the command's output. The Stop check reads only the final
    message, by fixed phrase lists: a conclusive claim worded otherwise passes, a claim after an unlisted
    negation ("could not get it verified") is refused, and a message naming any disclosure word passes and
    clears the reruns, whether or not it records the failure. It does not record or investigate the failure
    itself. Concurrent hook runs in one session can lose a state update. State keeps at most MAX_CHECKS
    commands (one longer than MAX_KEY JSON characters is keyed by its SHA-256, so the state stays under
    STATE_MAX_BYTES) and MAX_FLAGS outstanding reruns.

Self-test: python3 -I -S -B rerun-pass-check.py --self-test
"""

import functools
import hashlib
import json
import os
import re
import shlex
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
MAX_DEPTH = 8  # nesting of command substitutions read; a deeper one is syntax this reading does not model
# A CI rerun is a simple command whose words, after shell keywords and NAME=VALUE assignments (and an env
# prefix), start with one of these; the words come from the shell lexer, so a rerun command named inside a
# quoted string, an echo, or a comment is not one.
CI_RERUN = (("gh", "run", "rerun"), ("glab", "ci", "retry"))
KEYWORDS = frozenset(("if", "then", "else", "elif", "fi", "do", "done", "while", "until", "time", "coproc", "!",
                      "{", "}"))
# A CI rerun command named anywhere in a command the lexer cannot tokenize, read with quotes and backslashes
# removed: such a command is read as a possible CI rerun.
_NAMED_RERUN_RE = re.compile(r"\b(?:gh\s+run\s+rerun|glab\s+ci\s+retry)\b")
_PUNCT = "();<>|&\n"  # the shell operator characters, a newline included, for the shlex lexer
# One shell operator in a run of operator characters, longest first.
_OP_RE = re.compile(r"[<>]\(|&>>?|>>?\||>>?&?|<<<|<<-?|<&|<>|<|;;&?|;&|&&|\|\||\|&|[;&|()\n]")
_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)")
_UNQUOTED = (" ", "a", "c")  # the shlex states in which a character is read outside quotes and escapes
_FD_RE = re.compile(r"[0-9]+|\{[A-Za-z_][A-Za-z0-9_]*\}")  # a redirection's descriptor: 2 in 2>f, {fd} in {fd}>f
_ASSIGN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# A negation directly before a conclusive phrase ("not verified", "hasn't been verified", "never green").
_NEGATED_RE = re.compile(r"(?:\bnot|\bcannot|n't|\bnever|\bno longer)"
                         r"(?:\s+(?:yet|been|be|fully|really|actually))*\s*$", re.I)
CHECK_RE = re.compile(
    r"(?:^|[\s;&|(/])(?:pytest|py\.test|tox|nox|jest|vitest|mocha|ctest|phpunit|rspec)(?:\s|$)"
    r"|\bpython[0-9.]*\s+(?:-\S+\s+)*-m\s+(?:pytest|unittest)\b"
    r"|\b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|check)\b"
    r"|\b(?:go|cargo)\s+test\b|\b(?:make|gmake|just)\s+(?:\S+\s+)*(?:test|tests|check)(?:\s|$)"
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
READ_ONLY = frozenset(("cat", "less", "more", "head", "tail", "grep", "egrep", "fgrep", "rg", "ls", "pwd",
                       "echo", "printf", "wc", "date", "sleep", "true", "file", "stat", "which", "type", "diff",
                       "cmp", "sha256sum", "md5sum", "du", "df", "id", "whoami", "uname", "jq", "tree"))
BESIDE_CHECK = frozenset(("cd", "pushd", "popd", "set", "tee"))  # not a change on a check command line
READ_ONLY_GIT = frozenset(("status", "log", "diff", "show", "rev-parse", "blame", "ls-files"))
READ_ONLY_GH = (("run", "view"), ("run", "list"), ("run", "watch"), ("pr", "view"), ("pr", "checks"))
ENV_LONG = ("ignore-environment", "null", "unset", "chdir", "split-string", "block-signal", "default-signal",
            "ignore-signal", "list-signal-handling", "debug", "help", "version", "argv0", "file")
ENV_OPERAND = ("unset", "chdir", "argv0", "file")  # the long options that take an operand


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
    return dict(change=0, checks=dict(), flags=[], blocks=0)


def load_state(path):
    """(state, readable): an absent file is a fresh state; an unreadable or malformed one is (fresh, False)."""
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
        return (obj, True) if ok else (new_state(), False)
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
    """The lexer cannot fully tokenize a command, or it uses syntax this reading does not model."""


class _Source:
    """The text the shlex lexer reads, recording the lexer state each character is read in (so a character's
    quoting is the lexer's own reading); it drops a backslash-newline outside single quotes (a line
    continuation) and leaves a comment's closing newline in the stream, where it separates commands."""

    def __init__(self, text):
        self.text, self.pos, self.lexer, self.last, self.char, self.quoted = text, 0, None, None, "", False
        self.ctx = [None] * len(text)  # the state each character was read in; None: never read (a comment)

    def read(self, n=1):
        state, text = self.lexer.state, self.text
        while state not in ("\\", "'") and text.startswith("\\\n", self.pos):
            self.pos += 2
        self.last, self.char = state, text[self.pos:self.pos + 1]
        self.quoted = self.quoted or state in ("\\", "'", '"')  # since _lex last cleared it
        if self.pos >= len(text):
            return ""
        self.ctx[self.pos] = state
        self.pos += 1
        return text[self.pos - 1]

    def readline(self):
        end = self.text.find("\n", self.pos)
        self.pos = len(self.text) if end < 0 else end
        return ""


class _CommentStart:
    """The lexer's comment characters: only a # that starts a word, as in the shell (shlex alone would also
    start a comment at a # inside a word, as in echo a#b)."""

    def __init__(self, lexer):
        self.lexer = lexer

    def __contains__(self, ch):
        return ch == "#" and self.lexer.state != "a"


def _after(text, ctx, i):
    """The index of the character the shell reads after character i, past any line continuation."""
    i += 1
    while text.startswith("\\\n", i) and ctx[i] is None:
        i += 2
    return i


def _unmodelled_quoting(text, ctx):
    """Raise _Unsupported where shlex and the shell would read the quoting differently: a $ outside single
    quotes and escapes that starts ANSI-C ($'...') or locale ($"...") quoting, which shlex reads as a literal $
    and a plain quote, or a ${...} expansion holding a character that shlex would split at or pair as a quote
    (inside double quotes a quote, backslash, backtick or $; unquoted also a blank or an operator)."""
    for i, ch in enumerate(text):
        q = ctx[i]
        if ch != "$" or (q not in _UNQUOTED and q != '"'):
            continue
        j = _after(text, ctx, i)
        nxt = text[j:j + 1]
        if nxt in ("'", '"') and q in _UNQUOTED:
            raise _Unsupported()  # the first such quote is read alike by both, so it is always found here
        if nxt == "{":
            end = text.find("}", j)
            stop = "'\"\\`$" if q == '"' else "'\"\\`$ \t\r\n" + _PUNCT
            if end < 0 or any(c in stop for c in text[j + 1:end]):
                raise _Unsupported()


def _lex(text):
    """(tokens, ctx) for one command line, from the standard library's POSIX shlex lexer: tokens are (word,
    is_operator) pairs and ctx[i] is the lexer state character i was read in. A descriptor word directly
    before a redirection operator (the 2 of 2>f, the {fd} of {fd}>f) is dropped, as the shell reads it as part
    of the redirection. Raises _Unsupported for text shlex cannot tokenize (an unbalanced quote, a trailing
    backslash), a here-document, or quoting shlex reads otherwise than the shell (see _unmodelled_quoting)."""
    src = _Source(text)
    lexer = shlex.shlex(src, posix=True, punctuation_chars=_PUNCT)
    src.lexer = lexer
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = _CommentStart(lexer)
    tokens = []
    try:
        while True:
            src.quoted = False
            tok = lexer.get_token()
            if tok is None:
                break
            op = src.last == "c"  # an operator token ends with the lexer in its punctuation state
            if op and _HEREDOC_RE.search(tok):
                raise _Unsupported()  # a here-document body is not shell words
            # An unquoted word ended directly by < or > (read in the word state) that is a descriptor number
            # the shell accepts, or a {name}, is the descriptor of the redirection that follows, never a word.
            if not op and not src.quoted and src.last == "a" and src.char in ("<", ">") and _FD_RE.fullmatch(tok) \
                    and (not tok.isdigit() or int(tok) <= 0x7FFFFFFF):
                continue
            tokens.append((tok, op))
    except ValueError:
        raise _Unsupported()
    _unmodelled_quoting(text, src.ctx)
    return tokens, src.ctx


def _substitutions(text, ctx):
    """The (start, end, body) spans of the command substitutions that run: each $(...) and backtick outside
    single quotes, comments, and escapes (so unquoted or in double quotes)."""
    spans, i, n = [], 0, len(text)
    while i < n:
        q = ctx[i]
        if text[i] not in "$`" or (q not in _UNQUOTED and q != '"'):
            i += 1
            continue
        if q == '"':
            def same(j):
                if ctx[j] not in ('"', "\\"):
                    raise _Unsupported()  # a quote inside a double-quoted substitution is not modelled
                return ctx[j] == '"'
        else:
            def same(j):
                return ctx[j] in _UNQUOTED
        if text[i] == "$":
            if i + 1 >= n or text[i + 1] != "(" or not same(i + 1):
                i += 1
                continue
            if text.startswith("((", i + 1):
                raise _Unsupported()  # arithmetic expansion
            depth, j = 1, i + 2
            while j < n:
                if same(j) and text[j] in "()":
                    depth += 1 if text[j] == "(" else -1
                    if depth == 0:
                        break
                j += 1
            else:
                raise _Unsupported()
            spans.append((i, j + 1, text[i + 2:j]))
        else:
            j = i + 1
            while j < n and not (same(j) and text[j] == "`"):
                j += 1
            if j >= n or "\\" in text[i + 1:j]:
                raise _Unsupported()  # unclosed, or the escape rules inside backticks
            spans.append((i, j + 1, text[i + 1:j]))
        i = j + 1
    return spans


def _command_words(words):
    """`words` after leading shell keywords (with time's -p, and coproc's NAME before a { group) and
    NAME=VALUE assignments: the command and its arguments."""
    i = 0
    while i < len(words):
        if words[i] in KEYWORDS or (i and words[i] == "-p" and words[i - 1] == "time"):
            i += 1
        elif i and words[i - 1] == "coproc" and words[i + 1:i + 2] == ["{"]:
            i += 2
        else:
            break
    while i < len(words) and _ASSIGN_RE.match(words[i]):
        i += 1
    return words[i:]


def _split(tokens):
    """The word lists of the simple commands in `tokens`, redirections removed; a redirection that writes a
    file (any target but /dev/null, or a descriptor after >&) leaves the word >W in its command."""
    cmds, words, target = [], [], None
    for tok, op in tokens:
        if not op:
            if target is None:
                words.append(tok)
            elif tok != "/dev/null" and (target == "w" or (target == "dup" and not (tok.isdigit() or tok == "-"))):
                words.append(">W")
            target = None
            continue
        pieces = _OP_RE.findall(tok)
        if "".join(pieces) != tok:
            raise _Unsupported()
        for p in pieces:
            if target in ("w", "dup"):
                words.append(">W")  # a redirection with no target
            target = None
            if p in ("<(", ">("):
                raise _Unsupported()  # process substitution
            if ">" in p:
                target = "dup" if p.endswith("&") else "w"
            elif "<" in p:
                target = "r"
            elif words:
                cmds.append(words)
                words = []
    if target in ("w", "dup"):
        words.append(">W")
    if words:
        cmds.append(words)
    if any(_command_words(w)[:1] == ["case"] for w in cmds):
        raise _Unsupported()  # case patterns end in a bare )
    if any(_command_words(w)[:1] == ["[["] and "]]" not in w for w in cmds):
        raise _Unsupported()  # a [[ test split at an operator (&&, ||, a parenthesis) inside it
    return cmds


def _commands(text, depth):
    if depth > MAX_DEPTH:
        raise _Unsupported()
    tokens, ctx = _lex(text)
    spans = _substitutions(text, ctx)
    out = []
    if spans:
        parts, at = [], 0
        for start, end, body in spans:
            out.extend(_commands(body, depth + 1))
            parts.append(text[at:start] + "S")
            at = end
        tokens = _lex("".join(parts) + text[at:])[0]
    return _split(tokens) + out


@functools.lru_cache(maxsize=4)
def parse(cmd):
    """The simple commands `cmd` runs, as a tuple of word tuples (redirections removed; a redirection that
    writes a file leaves the word >W), with the commands inside each command substitution that runs, read as
    command lines of their own; None when the lexer cannot fully tokenize `cmd` or it uses syntax this reading
    does not model (a here-document, case, arithmetic or process substitution, a quote inside a double-quoted
    substitution, a backslash inside backticks, ANSI-C or locale quoting, a ${...} expansion that is unclosed
    or holds a quote, backslash, backtick or $ (unquoted, also a blank or an operator character), a [[ test
    split at an operator)."""
    try:
        return tuple(tuple(w) for w in _commands(cmd, 0))
    except (_Unsupported, RecursionError):
        return None


def builtin_command(words):
    """The words of the command a `command` or `exec` builtin (words[0]) runs, options removed; [] when none
    runs (none follows, or command -v or -V only describes it)."""
    while words and words[0] in ("command", "exec"):
        name, i = words[0], 1
        while i < len(words) and words[i].startswith("-") and words[i] != "-":
            if words[i] == "--":
                i += 1
                break
            if name == "command" and ("v" in words[i] or "V" in words[i]):
                return []
            i += 2 if name == "exec" and words[i].endswith("a") else 1  # exec -a NAME
        words = words[i:]
    return words


def _is_ci_rerun(words):
    w = builtin_command(_command_words(list(words)))
    if w and os.path.basename(w[0]) == "env":
        w = env_command(w) or []
    return bool(w) and (os.path.basename(w[0]),) + tuple(w[1:3]) in CI_RERUN


def ci_rerun(cmd):
    """True when `cmd` runs a CI rerun command at a command position, outside quotes and comments; a command
    the lexer cannot tokenize is one when it names a CI rerun command anywhere (a possible CI rerun)."""
    cmds = parse(cmd)
    if cmds is None:
        plain = re.sub(r"\$(?=['\"])", "", cmd.replace("\\\n", ""))  # $'x' and $"x" read as x
        return _NAMED_RERUN_RE.search(re.sub(r"[\\'\"]", "", plain)) is not None
    return any(_is_ci_rerun(w) for w in cmds)


def simple_commands(cmd):
    """The word lists of the simple commands in `cmd` (see parse); None when it cannot be read."""
    cmds = parse(cmd)
    return None if cmds is None else [list(w) for w in cmds]


def env_command(words):
    """The words of the command an env invocation (words[0]) runs: [] when none follows, None when -S supplies
    one. An option that takes an operand (-u NAME, -C DIR, -a ARG, -f FILE, or a long form, given by any unique
    prefix) consumes it, so the operand is never read as the command."""
    i = 1
    while i < len(words):
        w = words[i]
        if w == "--":
            i += 1
            break
        if not w.startswith("-"):
            break
        i += 1
        if w.startswith("--"):
            name = w[2:].split("=", 1)[0]
            hits = [name] if name in ENV_LONG else [o for o in ENV_LONG if o.startswith(name)]
            opt = hits[0] if len(hits) == 1 else None
            if opt == "split-string":
                return None
            if opt in ENV_OPERAND and "=" not in w:
                i += 1
            continue
        for k in range(1, len(w)):
            if w[k] == "S":
                return None
            if w[k] in "uCaf":
                if k == len(w) - 1:
                    i += 1
                break
    while i < len(words) and _ASSIGN_RE.match(words[i]):
        i += 1
    return words[i:]


def quiet(words):
    """True when one simple command (its words) starts with a word on the read-only list and writes no file."""
    if ">W" in words:
        return False
    w = os.path.basename(words[0])
    if w == "env":
        words = env_command(words)
        if words is None:
            return False
        if not words:
            return True  # env alone prints the environment
        w = os.path.basename(words[0])
    if w in READ_ONLY:
        return True
    if w == "git" and len(words) > 1 and words[1] in READ_ONLY_GIT:
        return True
    return w == "gh" and tuple(words[1:3]) in READ_ONLY_GH


def read_only(cmd):
    """True when every simple command in `cmd` starts with a word on the read-only list (env only with no
    command after it) and no output redirection writes a file (a /dev/null or descriptor target is allowed);
    False for a command that cannot be read."""
    cmds = parse(cmd)
    return cmds is not None and all(quiet(words) for words in cmds)


def changes_beside_check(cmd):
    """True when a simple command in `cmd` that is not itself a check command is not read-only and not cd,
    pushd, popd, set or tee (an install or an edit on the same line as the check, as in
    `pip install -e . && pytest -q`), or when `cmd` cannot be read."""
    cmds = parse(cmd)
    return cmds is None or any(not CHECK_RE.search(" " + " ".join(words)) and not quiet(words)
                               and os.path.basename(words[0]) not in BESIDE_CHECK for words in cmds)


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


def shown(cmd):
    one = " ".join(cmd.split())
    return one if len(one) <= MAX_SHOWN else one[:MAX_SHOWN] + "..."


def note(event, what):
    return dict(hookSpecificOutput=dict(hookEventName=event, additionalContext=(
        "RERUN NOTE (rerun-pass-check hook): " + what + " A pass on a rerun with no deliberate change in "
        "between does not erase the earlier failure: record the failure, investigate it as an intermittent "
        "result, and do not present the later pass as conclusive verification.")))


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
        if bad:
            return None
        if parse(cmd) is None:  # read conservatively: a possible CI rerun, and a change
            state["change"] += 1
            state["flags"] = (state["flags"] + ["possible CI rerun: " + shown(cmd)])[-MAX_FLAGS:]
            return ("a command the hook cannot fully parse names a CI rerun command (" + shown(cmd) + "), so it "
                    "is read as a possible CI rerun.")
        state["flags"] = (state["flags"] + ["CI rerun: " + shown(cmd)])[-MAX_FLAGS:]
        return "a CI rerun was started (" + shown(cmd) + ")."
    if not CHECK_RE.search(cmd):
        if not bad and not read_only(cmd):
            state["change"] += 1
        return None
    key = " ".join(cmd.split())
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
        state["flags"] = (state["flags"] + ["local rerun: " + shown(cmd)])[-MAX_FLAGS:]
        return "the check `" + shown(cmd) + "` failed earlier and passed on a rerun with no change recorded between."
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
    allowed = "rerun-pass-check: turn end allowed; the final message presents a pass as conclusive after a rerun"
    if blocks >= BLOCK_CAP:
        return dict(systemMessage=allowed + " (loop cap)")
    state["blocks"] = blocks + 1
    if not save_state(path, state) and active:
        return dict(systemMessage=allowed + " (refusal count cannot be saved)")
    return dict(decision="block", reason="rerun-pass-check: your final message presents a pass as conclusive, "
                "but this session started a CI rerun or saw a check pass on a rerun after it failed ("
                + "; ".join(state["flags"][-3:]) + "). "
                "State the earlier failure and that it is unresolved (intermittent), and do not call the "
                "later pass conclusive (refusal " + str(blocks + 1) + " of at most " + str(BLOCK_CAP) + ").")


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
            self.assertIsNone(self.bash("gh run rerun 1", ok=False))

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
            self.assertIn("loop cap", out["systemMessage"])

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
                      "python3 tools/x.py --self-test", "yarn run test", "./gradlew build test"):
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
            self.assertIsNone(self.bash("gh run rerun 7", ok=False))
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
            self.assertTrue(read_only("env -0 X=1 cat f"))
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
            self.assertIn("started a CI rerun or saw a check pass on a rerun", out["reason"])

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

        def test_18_env_option_operands_are_not_commands(self):
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNone(self.bash("env -u UNUSED"))
            self.assertIsNotNone(self.bash("pytest -q"))
            self.assertEqual(self.stop("All tests pass.")["decision"], "block")
            for c in ("env -u UNUSED", "env -uUNUSED", "env --unset UNUSED", "env --unset=UNUSED", "env --uns A",
                      "env -C /tmp", "env -iu A", "env -i -u A -- B=1", "env -C /tmp cat f"):
                self.assertTrue(read_only(c), c)
            for c in ("env -u A rm f", "env -S 'rm f'", "env --split-string=x", "env -iS x",
                      "env -C /tmp sed -i s/a/b/ f", "env -- rm f", "env -u A B=1 make"):
                self.assertFalse(read_only(c), c)

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

        def test_21_quoted_text_around_a_substitution_stays_quoted(self):
            self.assertIsNone(self.bash('echo "$(true); gh run rerun 7"'))  # prints the text; runs no rerun
            self.assertIsNone(self.stop("All tests pass."))
            self.assertFalse(ci_rerun('echo "note; gh run rerun is not needed, $(date)"'))
            for c in ('echo "$(date) done"', 'echo "=== $(date) ==="', 'echo "$(ls | wc -l) files"',
                      'printf "%s\x5cn" "$(git rev-parse HEAD) is HEAD"', "echo '$(gh run rerun 5)'"):
                self.assertTrue(read_only(c), c)
            for c in ('echo "$(gh run rerun 7)"', "x=$(echo $(gh run rerun 4))", "echo `gh run rerun 3`",
                      'echo "a `gh run rerun 2` b"'):
                self.assertTrue(ci_rerun(c), c)
            self.assertFalse(ci_rerun('echo "\x5c$(gh run rerun 6)"'))
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNone(self.bash('echo "$(date) done"'))
            self.assertIsNotNone(self.bash("pytest -q"))

        def test_22_an_escaped_space_or_quote_is_part_of_a_word(self):
            self.assertIsNotNone(self.bash("echo x\x5c # ; gh run rerun 7"))  # the # is inside the word
            self.assertEqual(self.stop("All tests pass.")["decision"], "block")
            for c in ("echo don\x5c't && gh run rerun 1 && echo it\x5c's", "gh run \x5c\n rerun 1",
                      "echo a # c \x5c\ngh run rerun 2", "/usr/bin/gh run rerun 1", "env X=1 gh run rerun 2"):
                self.assertTrue(ci_rerun(c), c)
            self.assertTrue(read_only("echo ''#x; ls") and read_only("echo hi >&2"))
            self.assertFalse(read_only("echo hi >&f"))

        def test_23_quoted_attached_env_operands(self):
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNone(self.bash('env --unset="UNUSED"'))
            self.assertIsNotNone(self.bash("pytest -q"))
            self.assertEqual(self.stop("All tests pass.")["decision"], "block")
            for c in ("env -u'A'", 'env "-u" A', "env -C'/tmp' cat f", "env -f .env ls", "env --argv0=x cat f"):
                self.assertTrue(read_only(c), c)
            for c in ("env -a cat rm f", "env --argv0 cat rm f", "env -f .env rm f"):
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
                self.assertFalse(ci_rerun(c), c)
            for c in ("case $x in a) gh run rerun 1;; esac", "cat <<EOF\ng'h' run rerun 1\nEOF",
                      "echo 'x; gh run rerun 1"):
                self.assertTrue(ci_rerun(c), c)
            self.assertIsNone(self.bash("pytest -q", ok=False))
            out = self.bash("cat > n.md <<'EOF'\nwe'll use gh run rerun 4\nEOF")
            self.assertIn("possible CI rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(self.bash("pytest -q"))  # the unreadable command also counts as a change
            self.assertEqual(self.stop("All tests pass.")["decision"], "block")

        def test_26_long_commands_keep_the_state_readable(self):
            self.bash("gh run rerun 9")
            for n in range(MAX_CHECKS + 5):
                self.bash("pytest -q -k " + str(n) + " '" + "\u00e9" * 600 + "'")
            path = state_path(dict(session_id="s"), self.env)
            self.assertLessEqual(os.path.getsize(path), STATE_MAX_BYTES)
            self.assertTrue(load_state(path)[1])
            self.assertEqual((self.stop("All tests pass.") or dict()).get("decision"), "block")

        def test_27_a_descriptor_redirection_is_not_a_word(self):
            for c in ("2>/dev/null gh run rerun 7", "gh 2>/dev/null run rerun 7", "{fd}>/dev/null gh run rerun 7",
                      "2<f gh run rerun 7", "&>/dev/null gh run rerun 7", "2>&1 gh run rerun 7", "3>&- gh run rerun 7",
                      "2147483647>/dev/null gh run rerun 7"):
                self.assertEqual(parse(c), (("gh", "run", "rerun", "7"),), c)
            for c, words in (("echo 2&>f", ("echo", "2", ">W")), ('"2">f ls', ("2", ">W", "ls")),
                             ("x\x5c\n2>f ls", ("x2", ">W", "ls")),
                             ("echo 2147483648>f", ("echo", "2147483648", ">W"))):
                self.assertEqual(parse(c), (words,), c)
            self.assertTrue(read_only("2>/dev/null echo ok") and read_only("echo hi 2>&-"))
            out = self.bash("2>/dev/null gh run rerun 7")
            self.assertIn("a CI rerun was started", out["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(self.bash("pytest -q", ok=False))
            self.assertIsNone(self.bash("2>/dev/null echo ok"))  # read-only: not a change
            self.assertIsNotNone(self.bash("pytest -q"))
            self.assertEqual(self.stop("All tests pass.")["decision"], "block")

        def test_28_ansi_c_locale_and_braced_expansions_are_not_read(self):
            for c in ("gh $'run' $'rerun' 7", 'gh $"run" $"rerun" 7', "$'gh' run rerun 1", "true;$'gh' run rerun 1",
                      "printf $'%s\x5c'\x5cn' x; gh run rerun 123  # don't", "echo $'\x5c''; gh run rerun 1 #'",
                      "echo ${x:-a; gh run rerun 1}", 'echo "${x:-"; gh run rerun 1; "}"', "gh $\x5c\n'run' rerun 1"):
                self.assertIsNone(parse(c), c)
                self.assertTrue(ci_rerun(c), c)
            self.assertIsNone(parse("printf $'a\x5c'b\x5cn' ; rm -rf build #'"))
            self.assertFalse(read_only("printf $'a\x5c'b\x5cn' ; rm -rf build #'"))
            for c in ("echo \"$'x'\"", "echo ${HOME}/x", 'echo "${HOME:-a b}"', "echo \x5c$'x'", "echo '$\"x\"'"):
                self.assertTrue(read_only(c), c)
            self.assertIsNone(self.bash("pytest -q", ok=False))
            out = self.bash("gh $'run' $'rerun' 7")
            self.assertIn("possible CI rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(self.bash("pytest -q"))  # the unreadable command also counts as a change
            self.assertEqual(self.stop("All tests pass.")["decision"], "block")

        def test_29_here_documents_and_lexer_branches(self):
            self.assertIsNone(parse("cat <<EOF\nls\nEOF"))
            self.assertIsNone(parse("cat <<-EOF\n\tls\n\tEOF"))
            self.assertIsNone(self.bash("pytest -q", ok=False))
            out = self.bash("cat > n.md <<EOF\ngh run rerun 4\nEOF")
            self.assertIn("possible CI rerun", out["hookSpecificOutput"]["additionalContext"])
            self.assertIsNone(self.bash("pytest -q"))
            self.assertTrue(read_only("grep x <<< 'a b'") and read_only("ls >&-"))
            self.assertIsNone(parse("echo `ls \x5c$x`"))
            self.assertTrue(ci_rerun("true;`gh run rerun 1`"))
            self.assertEqual(parse("true&&`ls`"), (("true",), ("S",), ("ls",)))

        def test_30_shell_prefixes_and_tests(self):
            for c in ("time -p gh run rerun 1", "coproc gh run rerun 1", "coproc N { gh run rerun 1; }",
                      "command gh run rerun 1", "command -p -- gh run rerun 1", "exec -a x gh run rerun 1",
                      "command exec gh run rerun 1"):
                self.assertTrue(ci_rerun(c), c)
            for c in ("command -v gh run rerun 1", "echo time -p gh run rerun 1", "-p time", "coproc N"):
                self.assertFalse(ci_rerun(c), c)
            for c in ("[[ $x =~ (a|b) ]]", "[[ -n $x && -f y ]]"):
                self.assertIsNone(parse(c), c)
            self.assertEqual(parse("[[ -f x ]] && ls"), (("[[", "-f", "x", "]]"), ("ls",)))

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
