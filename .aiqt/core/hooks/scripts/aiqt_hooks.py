#!/usr/bin/env python3
"""AIQT Guardrails enforcement hooks for Claude Code. Stdlib only, offline.

SOURCE tree copy: this file lives at .aiqt/core/hooks/scripts/aiqt_hooks.py and is copied
byte-identical into the generated plugin surface plugin/aiqt-guardrails-hooks/hooks/scripts/
aiqt_hooks.py by tools/gen_hooks.py; edit the source, never the generated copy. One dispatcher, one
handler function per control declared in .aiqt/core/hooks/manifest.toml:

  diff_wall_stop      Stop        cnsdif  surface (WARN) a unified-diff wall in the final assistant message
  diff_source_pretool PreToolUse  cnsdif  deny a Bash command that dumps a bare console diff
  commit_identity     PreToolUse  cmtidn  deny a git authoring command that names an AI identity
  absolute_paths      PreToolUse  abspth  deny a relative path where the tool requires absolute
  git_discard         PreToolUse  prsunc  allow/ask/deny a git command that would discard uncommitted work

Contract (doc-confirmed 2026-08-17 against code.claude.com/docs/en/hooks): the hook payload arrives
as JSON on stdin. A PreToolUse handler that decides emits, on exit 0,
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"|"deny",
"permissionDecisionReason": "..."}}; an allow decision is expressed as NO output (exit 0 silent), so
the user's own permission flow is never bypassed, and a deny decision blocks the tool. exit 2 is a
blocking error whose stderr is fed back to Claude. The Stop payload carries the final assistant text
as last_assistant_message (there is NO stop_hook_active field in the current Stop payload).

Error posture at the PreToolUse layer: FAIL CLOSED. A control that cannot read the input it is meant
to cover, or is invoked in a context it does not understand, DENIES rather than waving the action
through (per integ-check-fails-closed-on-unreadable): a missing tool_name, an unreadable command
string, or an unreadable required field all deny. A detected violation denies the same way. A clean
pass emits NO decision and exits 0 silently.

git_discard (prsunc) is a DELIBERATE, COARSE-ASK exception to that fail-closed rule (EN-6, GD-37). It has
THREE outcomes: ALLOW (exit 0 silent), DENY, and ASK (permissionDecision "ask", which prompts the human).
It fails OPEN (ALLOW) only at the TRUE BOUNDARY - a non-git command, or no recognized lossy verb - because
that is not a discard it can reason about; an UNPARSEABLE command (unbalanced quote) is not a free pass, it
is scanned raw for a lossy verb keyword and ASKS when one is present. WITHIN scope (a recognized lossy verb:
checkout/switch/restore/reset/clean/stash/rm/branch) the rule is COARSE: ASK, UNLESS the whole working tree
is PROVABLY CLEAN (the read-only porcelain probe reports no uncommitted tracked change) or the opt-out is
set, in which case ALLOW; a whole-tree-clobbering verb (reset --hard, checkout -f, switch --force) on a
confirmed-dirty tree DENIES. GD-37 REMOVED the earlier cut's "prove-safe fast paths" (path-disjoint,
per-path scoped probes, git-faithful option/dir modelling) - three tri-family QA rounds found ~13 ways
those clever paths were FOOLED into silently allowing a real discard, so the guard no longer tries to prove
an individual dirty-tree form safe. It never silent-allows a recognized lossy verb whose loss it cannot
disprove; worst case it ASKS. The one probe kept (git status --porcelain) is read-only and offline; it
never mutates the repo. GUARDRAIL_ALLOW_DISCARD=<truthy> leading a command opts out to ALLOW.

Stop layer is a DELIBERATE exception, non-blocking by design (GD-24 tri-family QA, 2026-08-17,
flagged for Architect review): it SURFACES a diff wall with a strong systemMessage and exits 0 (WARN),
it does NOT hard-block. The wall has already rendered by Stop time, so blocking cannot unsend it; and
because there is no stop_hook_active field and no documented built-in loop bound, a hard exit-2 Stop
block could re-fire on the forced continuation and wedge the session. The hard PREVENTION for console
diffs lives in the PreToolUse diff_source layer at the command source; the Stop layer only surfaces.

This is enforced at the DISPATCHER, not left to the handler alone: main() reads each handler's event
class from HANDLER_EVENT (the argv mode, never the payload, which may be unreadable) and, for a
Stop/SubagentStop handler, converts EVERY error path (a bad argv count, unreadable/malformed stdin, a
JSON parse failure, a non-dict payload, or a handler crash) into a non-blocking systemMessage warning
on exit 0. No Stop invocation can reach exit 2 for any input; only a PreToolUse handler fails closed via
exit 2, and only a genuinely UNKNOWN mode (not in HANDLERS, an unidentifiable broken install) does so on
a bad invocation.
"""
import json
import re
import shlex
import subprocess
import sys

PRETOOL = "PreToolUse"
STOP_EVENTS = ("Stop", "SubagentStop")


# --- decision constructors ---------------------------------------------------------------------------
# A handler returns (exit_code, stdout_obj_or_None, stderr_text_or_None). The dispatcher prints the
# stdout object as JSON when present, prints the stderr text when present, and exits with the code.

def _allow():
    """A clean pass: no decision at all (never an explicit allow, which would bypass the user's own
    permission flow), exit 0 silent."""
    return (0, None, None)


def _deny(reason, banner):
    """A PreToolUse block: permissionDecision deny on exit 0, honoured by the platform."""
    return (0, {"hookSpecificOutput": {"hookEventName": PRETOOL,
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason},
                "systemMessage": banner},
            None)


def _ask(reason, banner):
    """A PreToolUse ASK: permissionDecision ask on exit 0, which prompts the human to confirm rather
    than blocking outright. The recoverable middle of the git_discard three-outcome posture (allow /
    ask / deny): used when a recognized lossy verb cannot be PROVEN safe but is not confirmed lossy."""
    return (0, {"hookSpecificOutput": {"hookEventName": PRETOOL,
                                       "permissionDecision": "ask",
                                       "permissionDecisionReason": reason},
                "systemMessage": banner},
            None)


def _stop_warn(banner):
    """A Stop surfacing WARN: exit 0 with a systemMessage banner and NOTHING blocking. Every Stop
    outcome that is not a clean pass uses this, so the Stop layer can never wedge a turn chain (see the
    module docstring's design note)."""
    return (0, {"systemMessage": banner}, None)


def _hard_block(message):
    """A fail-closed hard block where no structured decision can be formed (a mis-wired PreToolUse
    event): exit 2 with the diagnostic on stderr, so a broken guard blocks rather than silently
    passing. Not used by the Stop handler, which is warn-only."""
    return (2, None, message)


def _deny_missing_tool_name(rule):
    """Fail closed on a PreToolUse payload with no tool_name: it cannot be matched against, so it
    cannot be cleared. A present-but-different tool is handled separately (allow, defensive)."""
    return _deny("AIQT rule {} (fail-closed): malformed payload: missing tool_name.".format(rule),
                 "AIQT guardrail: denied a PreToolUse call with no tool_name (rule {}, fail-closed)."
                 .format(rule))


# --- shell command segmentation (quote-aware) --------------------------------------------------------
# Tokenize the Bash command with a shell lexer that RESPECTS quoting (GD-24 fix round 3: the naive
# whitespace/separator split caused false-allows, because a ';' or '(' inside a quoted commit message,
# or a quoted global-option value with a space, was split at the wrong place). shlex(posix=True,
# punctuation_chars=True) with whitespace_split keeps a separator or space INSIDE a quoted string as
# part of that one token (quotes stripped), and yields the shell operators ; | || && & ( ) as distinct
# tokens ONLY when unquoted. We then group the token list into SEGMENTS on those operator tokens and on
# newlines (each line is lexed on its own, so an unquoted newline is a hard separator), so each control
# judges one command at a time on correctly-tokenized input. A parse error (unbalanced quotes) raises
# ValueError; the callers fall back to a conservative raw-string scan rather than silent-allow.
# Best-effort still: it does not defeat deliberate escaping/obfuscation (recorded in the manifest residue).
_SEGMENT_OPERATORS = frozenset((";", "|", "||", "&&", "&", "(", ")"))


def _lex_line(line):
    """The quote-aware token list of one line. Quotes are stripped; a separator or space inside a quoted
    string stays part of its one token; the shell operators are yielded as distinct unquoted tokens.
    Raises ValueError on a shell parse error (e.g. an unbalanced quote), so the caller can fall back."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _segments(command):
    """Group a command into SEGMENTS, quote-aware. Returns a list of (tokens, sep_after): tokens is the
    quote-stripped token list of the segment (never containing an operator token), and sep_after is the
    operator token that ended it (one of _SEGMENT_OPERATORS) or "" at a line end or the command end. The
    command is split into segments on the shell operators ; | || && & ( ) and on newlines. '( git diff )'
    yields a segment [git, diff] (the parens are separators), closing the subshell/grouping bypass.
    Raises ValueError on a shell parse error so callers can fall back conservatively."""
    # Splice bash line-continuations (a backslash immediately before a newline joins the continued line)
    # BEFORE splitting on newlines, so a continued command lexes as one line instead of raising. Residual:
    # a backslash-newline inside single quotes is a literal in bash and is over-spliced by this naive
    # replace (an accepted, astronomically-rare edge).
    command = command.replace("\\\n", "")
    result = []
    for line in command.split("\n"):
        current = []
        for tok in _lex_line(line):
            if tok in _SEGMENT_OPERATORS:
                result.append((current, tok))
                current = []
            else:
                current.append(tok)
        result.append((current, ""))
    return result


# A leading inline shell env-var assignment (FOO=bar) that PREFIXES a command, e.g. the GIT_PAGER=cat in
# 'GIT_PAGER=cat git diff'. Such assignments are SKIPPED when resolving a segment's command word and its
# git subcommand, so that form resolves to command word 'git' / subcommand 'diff' and is judged like a
# bare 'git diff' rather than slipping through as a non-git command word. The any-segment
# identity-assignment scan still inspects these same tokens for an AI name (GIT_AUTHOR_NAME=Claude ...).
# Best-effort, per the manifest residue: the 'env VAR=x git ...' command form and the 'command git ...'
# builtin remain out of scope.
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _command_word_index(tokens):
    """Index of the segment's command word: the first token that is NOT a leading shell env-var
    assignment (FOO=bar). Returns len(tokens) for an empty segment or one that is all assignments."""
    i = 0
    n = len(tokens)
    while i < n and _ENV_ASSIGN_RE.match(tokens[i]):
        i += 1
    return i


def _command_word(tokens):
    """The command word of a segment: the basename of the first non-assignment token, so an absolute
    path to the tool still resolves (/usr/bin/git -> git) and a leading env-assignment prefix (FOO=bar,
    e.g. GIT_PAGER=cat git diff) is skipped so the command word is still 'git'. '' for an empty segment
    or one that is all assignments."""
    idx = _command_word_index(tokens)
    if idx >= len(tokens):
        return ""
    return tokens[idx].rsplit("/", 1)[-1]


# git global options that CONSUME a following space-separated argument, so the option's value (now its
# own token) is never mistaken for the subcommand: '-C DIR', '-c NAME=VALUE', and the long forms below.
# Their '--opt=value' inline shape carries the value in the same token (an '=' is present), consuming no
# separate arg; that case is handled by the '=' test, so only the space-separated forms skip two tokens.
_GIT_ARG_OPTS = frozenset((
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix",
    "--config-env"))


def _git_subcommand(tokens):
    """The git subcommand of a segment whose command word is git: the first non-option token after the
    command word, skipping any leading env-assignment prefix and the git global options. An
    arg-consuming global option in its space-separated form (-C DIR, --git-dir DIR, ...) skips two tokens
    (its value is now its own token, per shlex) so the value is not read as the subcommand; the
    '--opt=value' form and any other leading '-' token skip one. None when there is no subcommand
    token."""
    i = _command_word_index(tokens) + 1  # skip leading env assignments and the command word itself
    n = len(tokens)
    while i < n:
        token = tokens[i]
        if token.startswith("-"):
            # An '=' inline form carries its value in the same token: skip one. Otherwise a
            # space-separated arg-consuming global option skips two; any other option skips one.
            if "=" not in token and token in _GIT_ARG_OPTS:
                i += 2
            else:
                i += 1
            continue
        return token
    return None


# --- cnsdif (Stop): the diff-wall shape --------------------------------------------------------------
# Thresholds are deliberately permissive toward small illustrative excerpts: the rule forbids burying
# the review surface under a raw dump, not quoting three lines of a patch. Each detector is lexical.
GIT_HEADER_RE = re.compile(r"^diff --git ", re.M)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.M)
HUNK_MIN = 2        # one quoted hunk header can be illustrative; two is a pasted patch
FENCE_MIN = 10      # a diff/patch fence with this many content lines is a dump, not an excerpt
RUN_MIN = 8         # consecutive lines starting with + or - ...
SIGN_MIN = 3        # ... containing at least this many of EACH sign (a bullet list is all "-")
_FENCE_LANGS = ("diff", "patch", "udiff")
# A leading Markdown blockquote marker: optional indentation, then one or more '>' each optionally
# followed by a single space (a nested '> > ' quote is stripped whole).
_BLOCKQUOTE_RE = re.compile(r"^\s*(?:>\s?)+")


def _strip_quote_indent(line):
    """Strip a leading Markdown blockquote marker ('> ', possibly repeated) and leading indentation from
    a line, so a quoted or indented diff wall is still seen by the WARN-layer detectors. The blockquote
    marker is removed first, then any remaining leading whitespace, so '> ~~~diff' becomes '~~~diff' and
    a four-space-indented '    +added' becomes '+added'. WARN layer only (non-blocking); permissive
    normalization here can only surface more walls, never block."""
    return _BLOCKQUOTE_RE.sub("", line).lstrip()


def _diff_fence_lines(lines):
    """The largest content line count inside a diff/patch/udiff fenced block. Both backtick (```) and
    tilde (~~~) fences are recognized, and the fence info-string is judged by its FIRST token, so a
    fence opened as `diff path/to/file` or `diff title=x` still counts as a diff fence."""
    best = 0
    fence = None    # the marker (``` or ~~~) that opened the current block, or None when outside
    count = 0
    for line in lines:
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                best = max(best, count)
                fence = None
            else:
                count += 1
            continue
        marker = "```" if stripped.startswith("```") else ("~~~" if stripped.startswith("~~~") else None)
        if marker is None:
            continue
        info = stripped[len(marker):].split()
        if info and info[0].lower() in _FENCE_LANGS:
            fence = marker
            count = 0
    if fence is not None:  # an unterminated fence still counts
        best = max(best, count)
    return best


def _plus_minus_run(lines):
    """The longest run of consecutive +/- lines that mixes both signs (>= SIGN_MIN each), which is
    the diff-body shape; an all-minus run is a Markdown bullet list and never trips this. A mixed
    +/- Markdown checklist can rarely trip it; that residual is accepted and noted in the manifest."""
    run = plus = minus = 0
    for line in list(lines) + [""]:  # the sentinel flushes a run that ends the message
        head = line[:1]
        # Explicit tuple test: `head in "+-"` would be True for the EMPTY string (a substring of any
        # string), so a blank line would silently extend a run and the sentinel would never flush.
        if head in ("+", "-"):
            run += 1
            if head == "+":
                plus += 1
            else:
                minus += 1
        else:
            if run >= RUN_MIN and plus >= SIGN_MIN and minus >= SIGN_MIN:
                return run
            run = plus = minus = 0
    return 0


def detect_diff_wall(text):
    """Return a human-readable description of the diff-wall shape found, or None. Each line is first
    normalized (leading blockquote marker and indentation stripped), so a diff wall quoted with '> ' or
    indented four spaces is detected exactly as a bare one; the header/hunk regexes are '^'-anchored and
    would otherwise miss a quoted or indented line."""
    lines = [_strip_quote_indent(line) for line in text.splitlines()]
    normalized = "\n".join(lines)
    if GIT_HEADER_RE.search(normalized):
        return "a 'diff --git' patch header"
    hunks = len(HUNK_RE.findall(normalized))
    if hunks >= HUNK_MIN:
        return "{} unified-diff @@ hunk headers".format(hunks)
    fenced = _diff_fence_lines(lines)
    if fenced >= FENCE_MIN:
        return "a fenced diff block of {} lines".format(fenced)
    run = _plus_minus_run(lines)
    if run:
        return "a run of {} consecutive +/- diff lines".format(run)
    return None


def diff_wall_stop(data):
    """cnsdif (trust/no-console-diff-dumps), Stop: SURFACE (non-blocking WARN) a final response that is
    a raw diff wall. This layer never blocks; see the module docstring's design note (the wall has
    already rendered, and a hard Stop block could wedge the session with no documented loop bound). It
    surfaces via systemMessage on exit 0; the PreToolUse diff_source layer is the hard prevention."""
    if data.get("hook_event_name") not in STOP_EVENTS:
        # Even a mis-wired event only WARNS here: the Stop layer is warn-only end to end, so nothing on
        # this path can wedge a turn chain. The generator's event whitelist is the real guard.
        return _stop_warn(
            "AIQT guardrail (rule cnsdif): the Stop diff-wall check was wired to an unexpected event "
            "{!r}; surfacing a warning rather than blocking.".format(data.get("hook_event_name")))
    message = data.get("last_assistant_message")
    if not isinstance(message, str):
        # Unreadable Stop payload: WARN, do not exit 2 (no wedge). The check could not run; surface it.
        return _stop_warn(
            "AIQT guardrail (rule cnsdif): the Stop payload carried no readable last_assistant_message, "
            "so the diff-wall check could not run. Surfacing a warning (non-blocking).")
    found = detect_diff_wall(message)
    if found is None:
        return _allow()
    return _stop_warn(
        "AIQT guardrail WARNING (rule cnsdif, no-console-diff-dumps): the final response contains {}. "
        "A raw diff wall buries the review surface. Report the change as a concise summary and surface "
        "the full detail through a file, an artefact, or the client's own diff view. (This is a "
        "surfacing warning; the PreToolUse layer is the hard prevention at the command source.)"
        .format(found))


# --- cnsdif (PreToolUse): a bare console diff at the source -------------------------------------------
# Layer A of the F-36 catch: deny a Bash command that renders a version-control diff to the console.
# Quote-aware segmented (shlex; split on ; && || | & ( ) and newlines), so a bare 'git diff' chained
# AFTER an allowed form, or grouped in a subshell '( git diff )', is still caught. A diff-producer
# segment is ALLOWED (excluded from denial) in four cases, each judged PER DIFF SEGMENT on that
# segment's own tokens (never a raw substring): (1) an INFO FLAG on the segment (--help or -h) is a help
# invocation, not a diff; (2) a SUMMARY flag (--stat etc.) with NO co-present patch flag (-p/-u/--patch*)
# is a listing, not a raw diff; (3) the segment's LAST stdout redirect targets a REAL non-console file
# (an ordinary path, not one under /dev/ or /proc/), diverting the diff off the review surface; (4) a PAGER PIPE, the segment piped
# (an unquoted '|') into a known interactive pager (less/more/most/pager), is interactive review rather
# than a console wall. Cases 1 and 4 were restored in GD-24 fix round 7 after fix round 6 over-corrected
# them to DENY; a pipe to cat/tee/anything else still denies. No comment escape exists (GD-24 fix round 6
# dropped the fragile '# allow-diff' quote-parsing bug surface, and it stays removed): an opt-in
# anti-diff-dump hook blocks console diffs, and these allows cover the legitimate non-wall cases.
_PATCH_FLAGS = frozenset(("-p", "-u"))
_SUMMARY_FLAGS = frozenset(("--stat", "--name-only", "--name-status", "--numstat", "--shortstat"))
# An info flag turns a diff subcommand into a help invocation, not a diff dump. A pager pipe sends the
# diff into an interactive reader, not a console wall; cat/tee/anything else is not a pager.
_INFO_FLAGS = frozenset(("--help", "-h"))
_PAGERS = frozenset(("less", "more", "most", "pager"))
_REDIRECT_TOKENS = frozenset((">", ">>"))  # stdout to a file; a grouped '>&'/'&>' fd-dup is not here
# A redirect target UNDER /dev/ or /proc/ is never a real diff-output file: it lands on a console or
# terminal (/dev/tty, /dev/pts/N, /dev/console), or on stdout/stderr (/dev/stdout, /dev/stderr,
# /dev/fd/N, /proc/self/fd/1, /proc/PID/fd/N), so a diff sent there still reaches the review surface. A
# real diff-output file is an ordinary path, never under one of these two trees.
_DEV_PROC_TARGET_RE = re.compile(r"^/(?:dev|proc)/")
# Fallback-only regexes over the RAW command string, used when shlex cannot parse the command (see
# _diff_source_fallback). SUMMARY/REDIRECT mirror the token escapes; the producer regex is a loose
# 'git ... diff|show|range-diff' probe. Conservative on a parse failure: deny a plausible producer.
SUMMARY_RE = re.compile(r"(?:^|\s)--(?:stat|name-only|name-status|numstat|shortstat)\b")
REDIRECT_TO_FILE_RE = re.compile(r"(?<![0-9&>])[12]?>>?\s*(?![&|])\S")
_RAW_DIFF_PRODUCER_RE = re.compile(r"(?is)\bgit\b.*?\b(?:diff|show|range-diff)\b")


def _has_patch_flag(tokens):
    """True when a segment carries a patch flag (-p, -u, or a --patch* form: --patch, --patch-with-stat,
    --patch-with-raw), the flag that turns a listing/plumbing/stash producer into a console patch and
    that also dumps the full diff alongside a summary flag (git diff --stat -p)."""
    return any(t in _PATCH_FLAGS or t.startswith("--patch") for t in tokens)


def _has_summary_flag(tokens):
    """True when a segment carries a summary/listing flag (--stat, --name-only, --name-status, --numstat,
    --shortstat), in either the bare or the '=value' shape. A summary flag is a listing rather than a raw
    diff, but ONLY when no patch flag is also present (see the handler: --stat -p still dumps the full
    diff), so the summary escape is gated on _has_patch_flag being false."""
    return any(t.split("=", 1)[0] in _SUMMARY_FLAGS for t in tokens)


def _is_diff_producer(tokens):
    """True when a git segment runs a subcommand that renders a diff to the console. The caller has
    already confirmed the segment's command word is git. Judging the SUBCOMMAND (not a bare 'diff'
    token) avoids a false positive on a commit message that mentions the word diff.

    Always a diff dump: diff, show, range-diff (they render a patch by default). Patch-flag gated: log,
    the plumbing producers diff-tree, diff-index, diff-files, and stash 'show' (they emit a listing by
    default and a patch only with -p/-u/--patch). stdout gated: format-patch (writes numbered files by
    default and dumps to the console only with --stdout). Gating the plumbing/format-patch/stash forms
    on their flag keeps the file-writing and name-only forms from a false positive."""
    sub = _git_subcommand(tokens)
    if sub in ("diff", "show", "range-diff"):
        return True
    if sub in ("log", "diff-tree", "diff-index", "diff-files"):
        return _has_patch_flag(tokens)
    if sub == "format-patch":
        return "--stdout" in tokens
    if sub == "stash":
        return "show" in tokens and _has_patch_flag(tokens)
    return False


def _is_stdout_fd_dup(tokens, i):
    """True when the token at index i is an fd-duplication that redirects STDOUT (to another descriptor
    or a console), so it is never a real-file diff output: '&>' (redirects both streams), and '>&' (from
    '>&1', '>&2', or a bare '>&') UNLESS an explicit non-stdout source fd precedes it (the '2' of a
    '2>&1', which redirects stderr and leaves stdout untouched). Used by the last-redirect-wins scan so a
    trailing stdout fd-dup after a real-file redirect flips the segment back to a console dump."""
    tok = tokens[i]
    if tok == "&>":
        return True
    if tok == ">&":
        prev = tokens[i - 1] if i > 0 else ""
        return not (prev.isdigit() and prev != "1")
    return False


def _redirects_to_real_file(tokens):
    """True when a segment's LAST stdout redirect sends stdout to a REAL, non-console file. Judged on the
    segment's OWN token stream (never a raw substring), LAST-redirect-wins: a segment may carry more than
    one stdout redirect and only the last one governs where stdout finally lands, so 'git diff >/dev/tty
    >/tmp/x' diverts to a real file (allow) while 'git diff >/tmp/x >/dev/tty' ends on the console (deny).
    A '>'/'>>' token whose target is an ordinary path is a real-file redirect; a target under /dev/ or
    /proc/ (a console/terminal, /dev/stdout, /dev/stderr, /dev/fd/N, /proc/self/fd/1, ...) is NOT, because
    the diff still reaches the console there. The fd-duplication forms ('>&1', '>&2', '1>&2', '2>&1', '&>',
    '>&') tokenize as their own '>&'/'&>' tokens, never a '>'/'>>' token, and a stdout fd-dup as the last
    redirect also lands on a console/descriptor, so it is not a real-file redirect either."""
    last_is_real_file = False
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok in _REDIRECT_TOKENS and i + 1 < n:
            last_is_real_file = not _DEV_PROC_TARGET_RE.match(tokens[i + 1])
        elif _is_stdout_fd_dup(tokens, i):
            last_is_real_file = False
    return last_is_real_file


def _has_info_flag(tokens):
    """True when a segment carries an info flag (--help or -h) as its own token: the segment is invoking
    the subcommand's help text, not rendering a diff, so it is not a console diff dump. Judged on the
    segment's own token stream (never a raw substring)."""
    return any(t in _INFO_FLAGS for t in tokens)


def _piped_to_pager(segments, index):
    """True when the diff-producer segment at `index` is piped (an unquoted '|' separator token) directly
    into a known interactive pager (less, more, most, pager): interactive review, not a console wall.
    Judged on the segment's sep_after and the NEXT segment's command word, both from the quote-aware
    token stream. A pipe to cat, tee, or anything else is NOT a pager pipe and still denies; a non-'|'
    separator (a ';', '&&', ...) is not a pipe and never qualifies."""
    _tokens, sep_after = segments[index]
    if sep_after != "|":
        return False
    nxt = index + 1
    if nxt >= len(segments):
        return False
    return _command_word(segments[nxt][0]) in _PAGERS


def _diff_source_fallback(command):
    """FAIL-SAFE conservative check when shlex cannot parse the command (unbalanced quotes): we cannot
    segment safely, so scan the RAW string. If it invokes a git diff-producer with no summary flag and no
    redirect-to-file escape, deny; never silent-allow a plausibly-violating command on a parse failure.
    Documented as best-effort: a genuinely clean but unparseable command may deny."""
    if _RAW_DIFF_PRODUCER_RE.search(command) and not (
            SUMMARY_RE.search(command) or REDIRECT_TO_FILE_RE.search(command)):
        return _deny(
            "AIQT rule cnsdif (no-console-diff-dumps): the command could not be parsed by the shell "
            "lexer (likely unbalanced quotes) and it appears to render a version-control diff to the "
            "console with no summary or redirect-to-file escape; failing closed. Re-issue with a "
            "summary form or a redirect to a real file.",
            "AIQT guardrail: denied an unparseable command that appears to dump a console diff (rule "
            "cnsdif, fail-safe).")
    return _allow()


def diff_source_pretool(data):
    """cnsdif (trust/no-console-diff-dumps), PreToolUse/Bash: deny a Bash command that dumps a bare
    console diff. A diff-producer segment is allowed only when, on its own tokens, it carries an info
    flag (--help/-h, a help invocation), a summary flag with no co-present patch flag, its LAST stdout
    redirect to a real non-console file, or is piped into a known interactive pager (less/more/most/pager).
    A diff segment matching none of these is denied."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: diff_source_pretool wired to unexpected event {!r}; failing "
                           "closed".format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("cnsdif")
    if tool_name != "Bash":
        return _allow()  # a present-but-different tool is out of scope (defensive; the matcher governs)
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return _deny(
            "AIQT rule cnsdif (no-console-diff-dumps): the Bash payload carried no readable command "
            "string, so the diff-source check could not run; failing closed.",
            "AIQT guardrail: denied a Bash call with no readable command (rule cnsdif, fail-closed).")
    try:
        segments = _segments(command)
    except ValueError:
        return _diff_source_fallback(command)
    for index, (tokens, _sep) in enumerate(segments):
        if _command_word(tokens) != "git":
            continue
        if not _is_diff_producer(tokens):
            continue
        # Allowed cases, each judged on THIS segment's own tokens (never a raw substring): an info flag
        # (--help/-h, a help invocation, not a diff), a summary flag, a redirect of stdout to a real
        # non-console file, or a pipe into a known interactive pager.
        if _has_info_flag(tokens):
            continue
        if _has_summary_flag(tokens) and not _has_patch_flag(tokens):
            continue
        if _redirects_to_real_file(tokens):
            continue
        if _piped_to_pager(segments, index):
            continue
        reason = ("AIQT rule cnsdif (no-console-diff-dumps): this command renders a version-control "
                  "diff to the console, burying the review surface under a raw dump. Use a summary form "
                  "(--stat, --name-only, --name-status, --numstat), redirect the diff to a real file "
                  "(> file), or pipe it into a pager (| less), not the console.")
        return _deny(reason,
                     "AIQT guardrail: denied a bare console diff dump (rule cnsdif).")
    return _allow()


# --- cmtidn: AI identity in a git authoring command --------------------------------------------------
# Quote-aware and token-based. The commit-MESSAGE contexts (a Co-Authored-By trailer, an --author value)
# are judged only on a segment whose command word is git and whose subcommand is a commit-creating verb,
# so a read-side use of the same tokens (git log --author=Claude) never trips. Separately, an identity
# ASSIGNMENT (a git-identity env var, or a user.name/user.email config value) is a violation on ANY
# segment, to harden the common 'set the identity then commit' form. Because the tokens are quote-aware,
# a trailer hiding inside a quoted -m message (e.g. -m "fix; Co-authored-by: Claude", which the old
# whitespace split broke at the ';') now lives intact in the single message token, and a substring scan
# on that token catches it.
AI_IDENTITY_RE = re.compile(
    r"(?i)\b(claude|anthropic|openai|chatgpt|codex|copilot|gemini|gpt-?[0-9o][a-z0-9.-]*)\b"
    r"|@anthropic\.com|@openai\.com")
COMMIT_VERBS = ("commit", "merge", "cherry-pick", "am", "rebase", "revert", "commit-tree")
# A Co-Authored-By trailer inside a single token; its value runs to the token end (the token IS the
# quoted message, so no shell quote can appear within it).
CO_AUTHOR_RE = re.compile(r"(?i)co[- ]?authored[- ]?by\s*:?\s*([^\n]{1,160})")
# Identity-assignment tokens (any segment): a git-identity env var, or a user.name/user.email config
# value in the '-c user.name=VALUE' inline shape.
GIT_ENV_RE = re.compile(r"(?is)^GIT_(?:AUTHOR|COMMITTER)_(?:NAME|EMAIL)=(.*)$")
USER_CONF_EQ_RE = re.compile(r"(?is)^user\.(?:name|email)=(.*)$")
# Fallback-only raw-string contexts, used when shlex cannot parse the command (see
# _commit_identity_fallback). A value runs to whitespace or a shell separator/quote.
_VALUE = r"(\"[^\"]*\"|'[^']*'|[^\s\"';|&]+)"
_RAW_IDENTITY_CONTEXTS = (
    (re.compile(r"(?i)co[- ]?authored[- ]?by\s*:?\s*([^\n\"']{1,160})"), "co-author trailer"),
    (re.compile(r"(?i)--author[= ]\s*" + _VALUE), "--author value"),
    (re.compile(r"(?i)\bGIT_(?:AUTHOR|COMMITTER)_(?:NAME|EMAIL)\s*=\s*" + _VALUE), "git identity variable"),
    (re.compile(r"(?i)\buser\.(?:name|email)\s*[= ]\s*" + _VALUE), "user.name/user.email value"),
)


def _commit_message_and_author_ai(tokens):
    """A hit label if this git commit segment names an AI identity in an --author value or in a
    Co-Authored-By trailer (scanned as a substring of any token, so the trailer that lives inside the
    single quoted -m message token is caught), else None. The bare message body is deliberately NOT
    matched against the identity set: a legitimate message that merely mentions an AI product name
    (e.g. -m 'fix claude adapter') is not a recorded AI identity, and denying it would be a false
    positive; only a trailer or an --author value records identity."""
    n = len(tokens)
    for i, tok in enumerate(tokens):
        m = CO_AUTHOR_RE.search(tok)
        if m and AI_IDENTITY_RE.search(m.group(1)):
            return "co-author trailer {!r}".format(m.group(1).strip()[:80])
        if tok.startswith("--author="):
            value = tok[len("--author="):]
            if AI_IDENTITY_RE.search(value):
                return "--author value {!r}".format(value.strip()[:80])
        elif tok == "--author" and i + 1 < n and AI_IDENTITY_RE.search(tokens[i + 1]):
            return "--author value {!r}".format(tokens[i + 1].strip()[:80])
    return None


def _identity_assignment_ai(tokens):
    """A hit label if any token of this segment ASSIGNS an AI identity: a git-identity env var
    (GIT_AUTHOR_NAME=..., ...), a '-c user.name=VALUE' config inline value, or a 'config user.name VALUE'
    /'config user.name=VALUE' pair. Judged on EVERY segment, so setting the identity in a prior segment
    before the commit is caught too."""
    n = len(tokens)
    for i, tok in enumerate(tokens):
        m = GIT_ENV_RE.match(tok)
        if m and AI_IDENTITY_RE.search(m.group(1)):
            return "git identity variable {!r}".format(tok.strip()[:80])
        m = USER_CONF_EQ_RE.match(tok)
        if m and AI_IDENTITY_RE.search(m.group(1)):
            return "user.name/user.email value {!r}".format(tok.strip()[:80])
        if tok in ("user.name", "user.email") and i + 1 < n and AI_IDENTITY_RE.search(tokens[i + 1]):
            return "user.name/user.email value {!r}".format(tokens[i + 1].strip()[:80])
    return None


def find_ai_authorship(command):
    """Return '<context> <value>' when a segment sets an AI identity for a recorded change, else None:
    an identity-assignment (env var / git config) on ANY segment, or a commit-message context (co-author
    trailer, --author value) on a git commit segment. Raises ValueError on a shell parse error (via
    _segments), which the handler turns into the conservative fallback."""
    for tokens, _sep in _segments(command):
        hit = _identity_assignment_ai(tokens)
        if hit is not None:
            return hit
        if _command_word(tokens) == "git" and _git_subcommand(tokens) in COMMIT_VERBS:
            hit = _commit_message_and_author_ai(tokens)
            if hit is not None:
                return hit
    return None


def _commit_identity_fallback(command):
    """FAIL-SAFE conservative scan when shlex cannot parse the command (unbalanced quotes): scan the RAW
    string for an AI identity in a Co-Authored-By trailer, an --author value, a git-identity env var, or
    a user.name/user.email value; deny on a hit. Never silent-allow a plausibly-violating command on a
    parse failure."""
    for regex, label in _RAW_IDENTITY_CONTEXTS:
        for match in regex.finditer(command):
            if AI_IDENTITY_RE.search(match.group(1)):
                hit = "{} {!r}".format(label, match.group(1).strip()[:80])
                reason = ("AIQT rule cmtidn (commit-identity): the command could not be parsed by the "
                          "shell lexer (likely unbalanced quotes) and it appears to set an AI identity "
                          "({}); failing closed. Remove it and commit as the maintainer.".format(hit))
                return _deny(reason,
                             "AIQT guardrail: denied an unparseable git command that appears to record "
                             "an AI commit identity (rule cmtidn, fail-safe).")
    return _allow()


def commit_identity(data):
    """cmtidn (integ/commit-identity), PreToolUse/Bash: deny a git command that records an AI as
    author, committer, or co-author. No escape hatch: the rule is absolute."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: commit_identity wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("cmtidn")
    if tool_name != "Bash":
        return _allow()  # a present-but-different tool is out of scope (defensive; the matcher governs)
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return _deny(
            "AIQT rule cmtidn (commit-identity): the Bash payload carried no readable command string, "
            "so the commit-identity check could not run; failing closed.",
            "AIQT guardrail: denied a Bash call with no readable command (rule cmtidn, fail-closed).")
    try:
        hit = find_ai_authorship(command)
    except ValueError:
        return _commit_identity_fallback(command)
    if hit is None:
        return _allow()
    reason = ("AIQT rule cmtidn (commit-identity): a recorded change carries the human maintainer's "
              "own identity, with no AI as author, committer, or co-author; this git command sets an "
              "AI identity ({}). Remove it and commit as the maintainer.".format(hit))
    return _deny(reason,
                 "AIQT guardrail: denied a git command recording an AI commit identity (rule cmtidn).")


# --- abspth: relative path where the tool requires absolute ------------------------------------------
# Read/Write/Edit require an absolute file_path by the tool's own contract, so the rule's carve-out
# never applies to them. Glob is honoured under the carve-out: its `pattern` is legitimately relative
# to a named root and is never judged, but its optional `path` search root should be absolute.
FILE_PATH_TOOLS = ("Read", "Write", "Edit")
DRIVE_RE = re.compile(r"^[A-Za-z]:")     # a Windows drive-letter path (C:\, C:/, or drive-relative C:x)


def _is_absolute(path):
    """POSIX absolute, Windows drive-letter (C:\\, C:/, or drive-relative C:x), a drive-relative path
    with a leading backslash (\\x), or UNC (\\\\server). A ~-prefixed path is NOT absolute: these tools
    do not expand it, so it would resolve relative to a literal ~ directory. Accepting the Windows
    drive-letter and leading-backslash forms avoids a Windows-path false positive; on POSIX a legitimate
    relative path never takes those shapes."""
    return path.startswith("/") or path.startswith("\\") or bool(DRIVE_RE.match(path))


def _deny_relative(tool, field, value):
    reason = ("AIQT rule abspth (absolute-paths): {} requires an absolute {}; got {!r}. The working "
              "directory can silently differ between tool calls, so re-issue the call with the full "
              "absolute path.".format(tool, field, value))
    return _deny(reason,
                 "AIQT guardrail: denied a relative {} where an absolute one is required "
                 "(rule abspth).".format(field))


def absolute_paths(data):
    """abspth (quali/absolute-paths), PreToolUse on Read|Write|Edit|Glob: deny a relative path where
    the tool requires absolute, honouring the rule's carve-out for a Glob pattern."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: absolute_paths wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool = data.get("tool_name")
    if tool is None:
        return _deny_missing_tool_name("abspth")
    tool_input = data.get("tool_input") or {}
    if tool in FILE_PATH_TOOLS:
        file_path = tool_input.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return _deny(
                "AIQT rule abspth (absolute-paths): the {} payload carried no readable file_path, so "
                "the absolute-path check could not run; failing closed.".format(tool),
                "AIQT guardrail: denied a {} call with no readable file_path (rule abspth, "
                "fail-closed).".format(tool))
        if _is_absolute(file_path):
            return _allow()
        return _deny_relative(tool, "file_path", file_path)
    if tool == "Glob":
        # Carve-out: `pattern` is legitimately relative to the named search root and is never judged.
        # The optional `path` search root, when given, should be absolute; when absent, the current
        # directory is the named root, which the carve-out permits, so allow.
        path = tool_input.get("path")
        if path is None:
            return _allow()
        if not isinstance(path, str) or not path:
            return _deny(
                "AIQT rule abspth (absolute-paths): the Glob payload carried an unreadable path search "
                "root, so the absolute-path check could not run; failing closed.",
                "AIQT guardrail: denied a Glob call with an unreadable path (rule abspth, fail-closed).")
        if _is_absolute(path):
            return _allow()
        return _deny_relative("Glob", "path search root", path)
    return _allow()  # a present-but-different tool is out of scope (defensive; the matcher governs)


# --- prsunc: a git discard that would lose uncommitted work ------------------------------------------
# COARSE-ASK guard (EN-6, GD-37 simplification of the earlier scoped prove-safe posture). Three outcomes:
#   ALLOW  exit 0 silent  - the true boundary (a non-git command, no recognized lossy verb, an
#                           unparseable command with no lossy verb), the escape hatch, and the ONE
#                           prove-safe case kept: the whole working tree is PROVABLY CLEAN (the porcelain
#                           probe reports no uncommitted tracked change), so there is nothing to discard.
#   DENY   permissionDecision deny - a WHOLE-TREE-clobbering verb (reset --hard, checkout -f, switch
#                           --force/--discard-changes) on a tree the probe confirms holds uncommitted
#                           tracked changes: the loss is certain.
#   ASK    permissionDecision ask  - the default within scope: any recognized lossy verb the guard cannot
#                           prove safe (the tree is not provably clean, its worktree cannot be resolved
#                           with certainty, the status probe did not complete, or the verb is a softer
#                           discard - clean of untracked files, stash drop/clear, branch -D, reset
#                           --merge - whose loss cannot be proven unwanted offline).
# GD-37 (Architect, 2026-08-18) REMOVED the "prove-safe fast paths" the earlier cut carried - the
# path-disjoint-against-the-dirty-set test, the per-path scoped porcelain probe, and the git-faithful
# option/dir modelling (cumulative -C, --git-dir/--work-tree, GIT_DIR/GIT_WORK_TREE, cd/subshell
# threading, restore/reset abbreviation decoding, the rev-parse ref-vs-pathspec probe). Three tri-family
# QA rounds found ~13 ways those clever paths were FOOLED into probing the wrong (clean) target and
# SILENTLY ALLOWING a real discard (shell expansions in a pathspec, option abbreviations with attached
# args, clean.requireForce, backgrounded/subshell cd, --git-dir/GIT_DIR, clean -q/-i). The coarse rule
# retires that whole class: it never tries to prove an individual dirty-tree form safe. The only safe
# signal is a PROVABLY CLEAN whole tree; anything the guard cannot resolve with certainty ASKS (never a
# silent allow). Net effect: dramatically fewer silent under-blocks (worst case is now a recoverable
# ASK), at the cost of more asks. If in doubt, ASK. An explicit GUARDRAIL_ALLOW_DISCARD truthy assignment
# leading a command opts out to ALLOW. The one probe kept (git status --porcelain) is read-only and
# offline; the guard never mutates the repo.
_DISCARD_OPTOUT_RE = re.compile(r"^GUARDRAIL_ALLOW_DISCARD=(.+)$")
_DISCARD_FALSY = frozenset(("", "0", "false", "no", "off"))  # value (case-insensitive) that is NOT truthy
# Fallback-only raw-string probes, used when shlex cannot parse the command (unbalanced quote). We cannot
# segment safely, so scan the RAW string for git AND a recognized work-losing verb: any of the
# always-lossy verbs, or a 'branch' paired with a delete flag ('-D', a clustered '-...D', or '--delete').
# A hit -> ASK (cannot prove safe); no hit -> ALLOW (the true boundary). Mirrors how diff_source/
# commit_identity fall back to a conservative raw scan on a parse failure. A truthy opt-out still ALLOWs.
_RAW_GIT_RE = re.compile(r"(?i)\bgit\b")
_RAW_LOSSY_VERB_RE = re.compile(r"(?i)\b(?:checkout|switch|restore|reset|clean|rm|stash)\b")
_RAW_BRANCH_RE = re.compile(r"(?i)\bbranch\b")
_RAW_BRANCH_DELETE_RE = re.compile(r"(?:(?<![\w-])-[A-Za-z]*D)|(?:--delete\b)")
_RAW_OPTOUT_RE = re.compile(r"(?i)\bGUARDRAIL_ALLOW_DISCARD=(\S+)")
# The safe alternatives named in every DENY/ASK reason, so the actor is never left without a next step.
_DISCARD_ALTS = (
    "Safe alternatives: commit or 'git stash' your work first; scope the revert with an explicit "
    "'-- <paths>'; unstage without touching the worktree via 'git restore --staged' or 'git rm "
    "--cached'; change branch with 'git switch' (it aborts on a dirty tree); or, for a known-safe "
    "discard, prefix the command with GUARDRAIL_ALLOW_DISCARD=1 to override this guard.")


def _has_discard_optout(segments):
    """True when a segment carries a truthy GUARDRAIL_ALLOW_DISCARD as a LEADING env-assignment (the
    documented 'GUARDRAIL_ALLOW_DISCARD=1 git ...' opt-out prefix), the explicit escape hatch. Only the
    leading-assignment region of each segment is inspected (the tokens before its command word), so the
    same string buried in an argument (e.g. 'echo GUARDRAIL_ALLOW_DISCARD=1') does NOT disable the guard.
    A value in {'', '0', 'false', 'no', 'off'} (case-insensitive) is NOT truthy."""
    for tokens, _sep in segments:
        for tok in tokens[:_command_word_index(tokens)]:
            m = _DISCARD_OPTOUT_RE.match(tok)
            if m and m.group(1).lower() not in _DISCARD_FALSY:
                return True
    return False


def _git_sub_and_args(tokens):
    """(sub, args): the git subcommand and the token list AFTER it, or (None, []) when there is no
    subcommand. Reuses the _command_word_index skip of leading env assignments and the _GIT_ARG_OPTS skip,
    so a '-C DIR' value is never mistaken for the subcommand."""
    i = _command_word_index(tokens) + 1  # skip leading env assignments and the command word itself
    n = len(tokens)
    while i < n:
        token = tokens[i]
        if token.startswith("-"):
            if "=" not in token and token in _GIT_ARG_OPTS:
                i += 2
            else:
                i += 1
            continue
        return token, tokens[i + 1:]
    return None, []


# The option-parsing boundary tokens: after either, every remaining token is an operand (a pathspec or
# ref), never an option, so a token that merely LOOKS like an option past this point is a literal operand.
_EOO_TOKENS = frozenset(("--", "--end-of-options"))


def _split_pre_post(args):
    """Split a subcommand's arg list at the FIRST option-boundary token ('--' or '--end-of-options').
    Returns (pre, post, had_sep): pre is the option region before it, post is every operand after it (all
    pathspecs, verbatim), had_sep says a boundary token was present."""
    for idx, a in enumerate(args):
        if a in _EOO_TOKENS:
            return args[:idx], args[idx + 1:], True
    return args, [], False


def _has_short(tokens, ch):
    """True when a clustered short-flag token (a single '-' then letters, e.g. '-fd') carries the letter
    ch. Spots '-f' inside a cluster (checkout '-f', clean '-fd'), '-p', '-S'/'-W' (restore)."""
    for t in tokens:
        if t.startswith("-") and not t.startswith("--") and len(t) > 1 and ch in t[1:]:
            return True
    return False


# --- the one probe kept: the coarse whole-tree clean signal ------------------------------------------

def _tree_is_clean(repo):
    """The coarse clean-tree signal, the ONLY probe this guard keeps. True when 'git -C <repo> status
    --porcelain' reports NO uncommitted tracked change (any staged or unstaged modification, in EITHER
    porcelain column), False when it reports one, None when the read-only probe could not run (a
    subprocess error, a non-zero return, an unreadable repo). Untracked '??' and ignored '!!' entries are
    a different asset (a real 'git clean' ASKS regardless), so they are skipped here. Deliberately coarse:
    ANY tracked change anywhere makes the tree not-provably-clean, so the guard no longer scopes the probe
    to the command's target paths (that per-path scoping was the fooled fast path GD-37 removed). Offline,
    read-only, 5s timeout; the guard never mutates the repo."""
    try:
        result = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if not line or line[:2] in ("??", "!!"):
                continue
            return False  # a tracked change (staged or unstaged): not provably clean
        return True
    except Exception:
        return None


# --- verb-form recognition (coarse; erring toward lossy) ---------------------------------------------
# Each _*_role returns (role, kind): role is one of "allow" (this FORM never discards tracked work),
# "ask" (a softer discard that ASKS unconditionally, not gated on the tracked-tree probe - clean of
# untracked files, stash drop/clear, branch -D), "scoped" (a worktree-touching discard: ALLOW on a
# provably-clean tree, else ASK), or "clobber" (a WHOLE-TREE overwrite: ALLOW on a clean tree, DENY on a
# confirmed-dirty tree). kind is a short human label. This recognition is purely lexical and never probes
# to prove an individual dirty-tree form safe; it only classifies the FORM, then the handler gates a
# scoped/clobber form on the single whole-tree clean probe.

def _checkout_role(args):
    """git checkout. A branch-create ('-b'/'-B'/'--orphan') never discards -> allow. '-p'/'--patch'
    interactively discards worktree hunks -> scoped. A force switch with NO pathspec ('checkout -f
    <branch>') overwrites the whole worktree -> clobber. Any form that carries pathspecs (a '-- <paths>',
    or bare operands, which may be a ref OR a pathspec - no longer disambiguated by a rev-parse probe) ->
    scoped. A bare 'git checkout' with no operands has no worktree effect -> allow."""
    pre, post, _had_sep = _split_pre_post(args)
    if any(a in ("-b", "-B", "--orphan") or a.startswith("--orphan=") for a in pre):
        return ("allow", None)  # creating/switching to a new branch; git aborts on a conflicting dirty tree
    if "--patch" in pre or _has_short(pre, "p"):
        return ("scoped", "git checkout -p/--patch (an interactive worktree-hunk discard)")
    force = "-f" in pre or "--force" in pre or _has_short(pre, "f")
    has_paths = bool(post) or any(not a.startswith("-") for a in pre)
    if force and not has_paths:
        return ("clobber", "git checkout -f (a forced branch switch that overwrites the worktree)")
    if has_paths:
        return ("scoped", "git checkout (a worktree revert)")
    return ("allow", None)  # no operand, no worktree effect


def _switch_role(args):
    """git switch. Only '-f'/'--force'/'--discard-changes' overwrites the worktree (whole-tree) -> clobber;
    a plain switch aborts on a dirty tree (git protects), so it is not a silent discard -> allow."""
    if ("-f" in args or "--force" in args or "--discard-changes" in args or _has_short(args, "f")
            or any(a.startswith("--discard") for a in args)):
        return ("clobber", "git switch --force/--discard-changes (overwrites the worktree)")
    return ("allow", None)


def _restore_role(args):
    """git restore. '-p'/'--patch' interactively discards hunks -> scoped. A pure unstage (an exact
    '--staged'/'-S' WITHOUT '--worktree'/'-W') never touches the worktree by form -> allow; an abbreviated
    '--stag' is NOT matched here (it falls through to scoped and ASKS, erring safe). Every other restore
    touches the worktree -> scoped."""
    pre, _post, _had_sep = _split_pre_post(args)
    if "--patch" in pre or _has_short(pre, "p"):
        return ("scoped", "git restore -p/--patch (an interactive hunk discard)")
    staged = "--staged" in pre or _has_short(pre, "S")
    worktree = "--worktree" in pre or _has_short(pre, "W")
    if staged and not worktree:
        return ("allow", None)  # a pure unstage: the worktree is untouched by form
    return ("scoped", "git restore (a worktree revert)")


def _reset_role(args):
    """git reset. The EFFECTIVE (last-wins) mode from the option region decides: an effective '--hard'
    overwrites the whole worktree -> clobber; '--merge' can lose but may also abort -> scoped;
    '--soft'/'--mixed'/'--keep', a path-scoped reset, or no mode flag (git defaults to --mixed, which
    keeps the worktree) -> allow. Mode flags are matched by unambiguous PREFIX ('--ha' == '--hard'), and
    an ambiguous bare '--m' (mixed OR merge) is treated as merge (scoped, erring safe); option parsing
    stops at the first '--'/'--end-of-options' so a mode-looking operand past it is a pathspec, not a
    mode."""
    pre, _post, _had_sep = _split_pre_post(args)
    mode = None  # "clobber" / "scoped" / "safe"
    for a in pre:
        if not (a.startswith("--") and "=" not in a):
            continue
        name = a[2:]
        if not name:
            continue
        if "hard".startswith(name):
            mode = "clobber"
        elif "merge".startswith(name):
            mode = "scoped"  # ambiguous '--m' lands here first: err toward ASK
        elif "mixed".startswith(name) or "soft".startswith(name) or "keep".startswith(name):
            mode = "safe"
    if mode == "clobber":
        return ("clobber", "git reset --hard")
    if mode == "scoped":
        return ("scoped", "git reset --merge")
    return ("allow", None)


def _rm_role(args):
    """git rm. '--cached' only unstages and keeps the worktree file -> allow; otherwise it removes a
    tracked file, dropping any uncommitted changes to it -> scoped."""
    if "--cached" in args:
        return ("allow", None)
    return ("scoped", "git rm (removes a tracked file, dropping any uncommitted changes to it)")


def _discard_role(sub, args):
    """Classify a git segment's subcommand into (role, kind); see the block comment above. clean, stash,
    and branch do not use the tracked-tree probe: a real 'git clean' removes UNTRACKED files (a different,
    unrecoverable asset the tracked-change probe does not see), so ANY non-dry-run clean ASKS
    unconditionally (this also closes the clean.requireForce / -q / -i / clustered-flag edges, since the
    guard no longer tries to model whether the clean would fire); stash drop/clear and branch -D ASK."""
    if sub == "checkout":
        return _checkout_role(args)
    if sub == "switch":
        return _switch_role(args)
    if sub == "restore":
        return _restore_role(args)
    if sub == "reset":
        return _reset_role(args)
    if sub == "rm":
        return _rm_role(args)
    if sub == "clean":
        if "--dry-run" in args or _has_short(args, "n"):
            return ("allow", None)  # a dry run removes nothing
        return ("ask", "git clean (removes untracked files, which cannot be recovered)")
    if sub == "stash":
        first = next((a for a in args if not a.startswith("-")), None)
        if first in ("drop", "clear"):
            return ("ask", "git stash {} (discards saved stash entries)".format(first))
        return ("allow", None)
    if sub == "branch":
        short = [t for t in args if t.startswith("-") and not t.startswith("--")]
        has_big_d = "-D" in args or any("D" in t[1:] for t in short)
        has_delete = "--delete" in args or any("d" in t[1:] for t in short)
        has_force = "--force" in args or any("f" in t[1:] for t in short)
        if has_big_d or (has_delete and has_force):
            return ("ask", "git branch -D (a force branch delete that may drop unmerged commits)")
        return ("allow", None)
    return ("allow", None)  # not a recognized lossy verb: the true boundary


# --- outcome text ------------------------------------------------------------------------------------

def _discard_ask_reason(kind, detail):
    """The (reason, banner) pair for an ASK. Stored by the handler and emitted via _ask if no segment
    DENIES first, so a confirmed loss still wins over a recoverable ask."""
    reason = ("AIQT rule prsunc (preserve-uncommitted-work): {} {}. Confirm before proceeding, or commit "
              "or stash your work first. {}".format(kind, detail, _DISCARD_ALTS))
    banner = ("AIQT guardrail: {} - confirm this discard, or prefix GUARDRAIL_ALLOW_DISCARD=1 to skip this "
              "prompt (rule prsunc).".format(kind))
    return (reason, banner)


def _discard_deny(kind):
    """A DENY: a whole-tree-clobbering verb on a tree the probe confirms holds uncommitted tracked
    changes, so the loss is certain."""
    reason = ("AIQT rule prsunc (preserve-uncommitted-work): {} would overwrite the working tree, which "
              "currently holds uncommitted tracked changes, discarding any fix you have applied but not "
              "yet committed. {}".format(kind, _DISCARD_ALTS))
    banner = ("AIQT guardrail: blocked a git command that would discard uncommitted work (rule prsunc). "
              "Prefix GUARDRAIL_ALLOW_DISCARD=1 to override.")
    return _deny(reason, banner)


# --- worktree-certainty (coarse; replaces the removed dir modelling) ---------------------------------

def _segment_dir_simple(tokens):
    """True when a git segment names its worktree simply enough that the session dir IS the worktree the
    command acts on: no leading env-assignment other than the opt-out (a GIT_DIR/GIT_WORK_TREE could
    redirect it), and no global option before the subcommand (a -C/--git-dir/--work-tree/-c, possibly
    abbreviated or attached, could redirect the worktree or change config). Anything else -> not simple ->
    the handler ASKS rather than trust a clean probe in the session dir. This is the coarse replacement
    for the git-faithful -C/--git-dir/--work-tree/env dir modelling GD-37 removed (that modelling was
    repeatedly fooled into probing a clean dir and silently allowing - F-62/F-64/F-66)."""
    cw_idx = _command_word_index(tokens)
    for tok in tokens[:cw_idx]:
        if _DISCARD_OPTOUT_RE.match(tok):
            continue  # the opt-out assignment is benign and is handled separately
        return False  # some other leading env-assignment: it may redirect the worktree or config
    i = cw_idx + 1  # the token after the 'git' command word
    if i < len(tokens) and tokens[i].startswith("-"):
        return False  # a global option before the subcommand: may be -C/--git-dir/--work-tree/-c
    return True


def _git_discard_fallback(command):
    """FAIL-SAFE conservative scan when shlex cannot parse the command (unbalanced quote): we cannot
    segment safely, so scan the RAW string. A truthy GUARDRAIL_ALLOW_DISCARD opt-out still ALLOWs. If git
    is present AND a recognized work-losing verb keyword is present (an always-lossy verb, or 'branch' with
    a delete flag) -> ASK (cannot prove safe); otherwise ALLOW (the true boundary). Documented best-effort:
    a genuinely clean but unparseable command that merely mentions a lossy verb keyword may ASK."""
    m = _RAW_OPTOUT_RE.search(command)
    if m and m.group(1).lower() not in _DISCARD_FALSY:
        return _allow()  # explicit opt-out, honoured even on an unparseable command
    if not _RAW_GIT_RE.search(command):
        return _allow()  # no git: the true boundary
    lossy = _RAW_LOSSY_VERB_RE.search(command) or (
        _RAW_BRANCH_RE.search(command) and _RAW_BRANCH_DELETE_RE.search(command))
    if not lossy:
        return _allow()  # git present but no recognized work-losing verb: the true boundary
    return _ask(
        "AIQT rule prsunc (preserve-uncommitted-work): the command could not be parsed by the shell lexer "
        "(likely an unbalanced quote) and it names a git work-losing verb this guard cannot prove safe; "
        "asking rather than silently allowing. {}".format(_DISCARD_ALTS),
        "AIQT guardrail: an unparseable git command names a work-losing verb this guard could not prove "
        "safe - confirm this discard, or prefix GUARDRAIL_ALLOW_DISCARD=1 to skip this prompt (rule "
        "prsunc, fail-safe).")


def git_discard(data):
    """prsunc (integ/preserve-uncommitted-work), PreToolUse/Bash. COARSE-ASK guard (EN-6, GD-37): fail-open
    ALLOW only at the true boundary (a non-git command, or no recognized lossy verb; an unparseable command
    is scanned raw and ASKS when it names a lossy verb). For a recognized lossy verb the rule is COARSE:
    ASK, UNLESS the whole working tree is PROVABLY CLEAN (the porcelain probe reports no uncommitted
    tracked change) or the opt-out is set, in which case ALLOW; a whole-tree-clobbering verb (reset --hard,
    checkout -f, switch --force) on a confirmed-dirty tree DENIES. It never tries to prove an individual
    dirty-tree form safe (the removed prove-safe fast paths were the silent-under-block bug source), so a
    lossy command on a not-provably-clean tree is never silently allowed - worst case it ASKS. The one
    probe kept (git status --porcelain) is read-only and offline; the guard never mutates the repo.
    GUARDRAIL_ALLOW_DISCARD=<truthy> leading a command opts out to ALLOW."""
    if data.get("hook_event_name") != PRETOOL:
        # A mis-wired event is a broken install: loud (unreachable given the generator's event whitelist).
        return _hard_block("aiqt_hooks: git_discard wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name != "Bash":
        return _allow()  # boundary: a missing or other tool is out of scope
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return _allow()  # boundary: unreadable/malformed command container
    try:
        segments = _segments(command)
    except ValueError:
        return _git_discard_fallback(command)  # conservative raw scan, not a silent allow
    if _has_discard_optout(segments):
        return _allow()  # GUARDRAIL_ALLOW_DISCARD truthy, leading a segment
    cwd = data.get("cwd")
    base = cwd if isinstance(cwd, str) and cwd else None  # the session dir, or None when absent
    # Any directory-shifting shell construct anywhere in the command (a cd/pushd/popd, or a subshell
    # group) means the guard cannot be CERTAIN which worktree a later git command acts on, so it will not
    # trust a clean probe in the session dir for a lossy verb (it ASKS). This is the coarse replacement
    # for the git-faithful cd/subshell dir threading GD-37 removed (repeatedly fooled - F-62/F-64/F-66).
    dir_shift = False
    for tokens, sep_after in segments:
        if _command_word(tokens) in ("cd", "pushd", "popd") or sep_after in ("(", ")"):
            dir_shift = True
            break
    pending_ask = None
    for tokens, _sep in segments:
        if _command_word(tokens) != "git":
            continue
        sub, args = _git_sub_and_args(tokens)
        if sub is None:
            continue
        role, kind = _discard_role(sub, args)
        if role == "allow":
            continue
        if role == "ask":
            # A softer discard (clean of untracked, stash drop/clear, branch -D): ASK unconditionally.
            if pending_ask is None:
                pending_ask = _discard_ask_reason(kind, "cannot be proven safe offline")
            continue
        # role is "scoped" or "clobber": a tracked-worktree lossy verb, gated on the clean-tree probe.
        if base is None or dir_shift or not _segment_dir_simple(tokens):
            # The worktree cannot be resolved with certainty: never silent-allow -> ASK.
            if pending_ask is None:
                pending_ask = _discard_ask_reason(
                    kind, "targets a repository this guard cannot resolve with certainty, so it cannot "
                          "prove the working tree clean")
            continue
        clean = _tree_is_clean(base)
        if clean is True:
            continue  # PROVABLY CLEAN: nothing uncommitted to lose -> allow this segment
        if clean is None:
            if pending_ask is None:
                pending_ask = _discard_ask_reason(
                    kind, "targets a repository whose status probe did not complete, so this guard cannot "
                          "prove the working tree clean")
            continue
        # clean is False: the tree holds uncommitted tracked changes this verb could reach.
        if role == "clobber":
            return _discard_deny(kind)  # a confirmed whole-tree loss wins immediately
        if pending_ask is None:
            pending_ask = _discard_ask_reason(kind, "may discard uncommitted tracked changes in the "
                                                    "working tree")
    if pending_ask is not None:
        return _ask(*pending_ask)
    return _allow()


# --- dispatcher ---------------------------------------------------------------------------------------
HANDLERS = {
    "diff_wall_stop": diff_wall_stop,
    "diff_source_pretool": diff_source_pretool,
    "commit_identity": commit_identity,
    "absolute_paths": absolute_paths,
    "git_discard": git_discard,
}

# Handler -> event class, so the dispatcher can decide its ERROR posture from the argv MODE alone,
# without reading the (possibly unreadable) payload. This is the load-bearing half of the fail-closed
# design: a Stop/SubagentStop handler must NEVER exit 2, because a hard Stop block could re-fire on the
# forced continuation and wedge the session (no stop_hook_active field, no documented loop bound), so on
# ANY error (unreadable stdin, JSON parse failure, non-dict payload, or a handler crash) it emits a
# non-blocking systemMessage warning and exits 0. Only a PreToolUse handler fails closed via exit 2.
HANDLER_EVENT = {
    "diff_wall_stop": "Stop",
    "diff_source_pretool": PRETOOL,
    "commit_identity": PRETOOL,
    "absolute_paths": PRETOOL,
    "git_discard": PRETOOL,
}


def _dispatcher_stop_warn(detail):
    """A Stop/SubagentStop dispatcher-level error, surfaced as a non-blocking WARN: {"systemMessage": ...}
    on stdout, so main can print it and exit 0. Mirrors the handler's own _stop_warn posture so the Stop
    layer is warn-only end to end, at the dispatcher as well as inside the handler."""
    return {"systemMessage": (
        "AIQT guardrail (rule cnsdif): the Stop diff-wall check could not run ({}); surfacing a warning "
        "rather than blocking (non-blocking, so the session is never wedged).".format(detail))}


def main(argv):
    # The MODE (argv[0]) alone decides the error posture, never the payload (which may be unreadable).
    # A genuinely unknown mode is not identifiable as Stop and is a broken install, so it fails closed
    # via exit 2. But a KNOWN handler invoked with the wrong argv count must NOT reach exit 2 when it is
    # a Stop/SubagentStop handler: a hard exit-2 Stop path could re-fire on the forced continuation and
    # wedge the session (no stop_hook_active field, no documented loop bound), so a bad-argv Stop
    # invocation WARNS on exit 0 like every other Stop error path (FIX 2). A bad-argv PreToolUse handler
    # still fails closed (exit 2).
    mode = argv[0] if argv else None
    if mode not in HANDLERS:
        print("aiqt_hooks: usage: aiqt_hooks.py <{}>".format("|".join(sorted(HANDLERS))),
              file=sys.stderr)
        return 2
    handler_name = mode
    is_stop = HANDLER_EVENT[handler_name] in STOP_EVENTS
    if len(argv) != 1:
        detail = "expected exactly one mode argument, got {}".format(len(argv))
        if is_stop:
            # A Stop handler NEVER exits 2, not even on a malformed invocation: WARN and exit 0.
            print(json.dumps(_dispatcher_stop_warn("bad invocation: {}".format(detail))))
            return 0
        print("aiqt_hooks: {} ({}); failing closed".format(handler_name, detail), file=sys.stderr)
        return 2
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            raise ValueError("payload is not a JSON object")
    except (ValueError, UnicodeDecodeError, OSError) as exc:
        # Unreadable/malformed stdin, JSON parse error, UnicodeDecodeError, or a non-dict payload.
        if is_stop:
            # A Stop handler NEVER exits 2: surface a non-blocking warning and exit 0, so no Stop
            # payload (including a bare '{' or any garbage) can ever wedge the session.
            print(json.dumps(_dispatcher_stop_warn("unreadable payload: {}".format(exc))))
            return 0
        # A PreToolUse hook that cannot read its payload cannot clear the action, so it fails CLOSED.
        # exit 2 is the platform's blocking path; the diagnostic reaches Claude on stderr.
        print("aiqt_hooks: unreadable hook payload ({}); failing closed".format(exc), file=sys.stderr)
        return 2
    try:
        code, stdout_obj, stderr_text = HANDLERS[handler_name](data)
    except Exception as exc:  # a handler crash is an unreadable result
        if is_stop:
            # Same event-aware posture for a crash inside the Stop handler (e.g. the detector throws on a
            # pathological message): WARN and exit 0, never exit 2.
            print(json.dumps(_dispatcher_stop_warn("handler crash: {}".format(exc))))
            return 0
        # A PreToolUse handler crash fails closed (block), not pass.
        print("aiqt_hooks: handler {} failed ({}); failing closed".format(handler_name, exc),
              file=sys.stderr)
        return 2
    if stdout_obj is not None:
        print(json.dumps(stdout_obj))
    if stderr_text:
        print(stderr_text, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
