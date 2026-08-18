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

git_discard (prsunc) is a DELIBERATE, SCOPED-POSTURE exception to that fail-closed rule (EN-6 redesign).
It has THREE outcomes: ALLOW (exit 0 silent), DENY, and ASK (permissionDecision "ask", which prompts the
human). It fails OPEN (ALLOW) only at the TRUE BOUNDARY - a non-git command, no recognized lossy verb, or
an unparseable command - because that is not a discard it can reason about. WITHIN scope (a recognized
lossy verb: checkout/switch/restore/reset/clean/stash/rm/branch) it PROVES SAFE: a provably-safe discard
ALLOWS, a CONFIRMED tracked-work loss DENIES, and a lossy verb it can neither prove safe nor confirm lossy
ASKS. It never silent-allows a recognized lossy verb whose loss it cannot disprove (that was the EN-4
leak this redesign closes). Its probes (git status/rev-parse/clean -n) are read-only and offline; it never
mutates the repo. GUARDRAIL_ALLOW_DISCARD=<truthy> anywhere in the command opts out to ALLOW.

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
import os
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
# SCOPED-POSTURE guard (EN-6 redesign, supersedes the EN-4 fail-open-lexical hook). Three outcomes:
#   ALLOW  exit 0 silent  - the true boundary (non-git, no recognized lossy verb, unparseable), and the
#                           PROVE-SAFE fast paths (a clean tree, a path disjoint from the dirty tracked
#                           set, a pure unstage, a clean branch switch git itself guards).
#   DENY   permissionDecision deny - a CONFIRMED loss of uncommitted TRACKED work (a dirty overlap the
#                           status probe reports for the exact target).
#   ASK    permissionDecision ask  - a recognized lossy verb whose loss cannot be PROVEN either way (an
#                           unresolvable repo dir, pathspecs sourced from a file, a status probe that did
#                           not complete), and the softer discards (clean of untracked files, stash
#                           drop/clear, branch -D, reset --merge) that cannot be proven unwanted offline.
# This is the POSTURE FLIP from EN-4: fail-open ALLOW survives ONLY at the true boundary; a recognized
# lossy verb whose loss cannot be disproven now ASKS (never silent-allows, which was the EN-4 leak). The
# unprovable middle ALWAYS asks (ask is recoverable), so the outcome does not depend on detecting
# interactivity - a deliberate conservative first cut, recorded here per the spec. An explicit
# GUARDRAIL_ALLOW_DISCARD truthy assignment anywhere in the command opts out to ALLOW, unchanged. The
# probes (git status/rev-parse/clean -n) are READ-ONLY and offline; the guard never mutates the repo.
_DISCARD_OPTOUT_RE = re.compile(r"^GUARDRAIL_ALLOW_DISCARD=(.+)$")
_DISCARD_FALSY = frozenset(("", "0", "false", "no", "off"))  # value (case-insensitive) that is NOT truthy
# The safe alternatives named in every DENY/ASK reason, so the actor is never left without a next step.
_DISCARD_ALTS = (
    "Safe alternatives: commit or 'git stash' your work first; scope the revert with an explicit "
    "'-- <paths>'; unstage without touching the worktree via 'git restore --staged' or 'git rm "
    "--cached'; change branch with 'git switch' (it aborts on a dirty tree); or, for a known-safe "
    "discard, prefix the command with GUARDRAIL_ALLOW_DISCARD=1 to override this guard.")


def _has_discard_optout(segments):
    """True when any token in any segment is a truthy GUARDRAIL_ALLOW_DISCARD assignment, the explicit
    opt-out that leaves a trace in the command text. A value in {'', '0', 'false', 'no', 'off'}
    (case-insensitive) is NOT truthy; a bare 'GUARDRAIL_ALLOW_DISCARD=' (no value) does not match at all."""
    for tokens, _sep in segments:
        for tok in tokens:
            m = _DISCARD_OPTOUT_RE.match(tok)
            if m and m.group(1).lower() not in _DISCARD_FALSY:
                return True
    return False


def _git_sub_and_args(tokens):
    """Like _git_subcommand but returns (sub, args): the git subcommand and the token list AFTER it, or
    (None, []) when there is no subcommand. Reuses the _command_word_index skip of leading env
    assignments and the _GIT_ARG_OPTS skip, so a '-C DIR' value is never mistaken for the subcommand."""
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
# ref), never an option, so a token that merely LOOKS like an option (--hard, --force) past this point is
# a literal operand. Honouring '--end-of-options' as well as '--' is part of closing F-57 (an operand
# named like a mode is not read as the mode).
_EOO_TOKENS = frozenset(("--", "--end-of-options"))


def _split_pre_post(args):
    """Split a subcommand's arg list at the FIRST option-boundary token ('--' or '--end-of-options').
    Returns (pre, post, had_sep): pre is the option region before it, post is every operand after it
    (all pathspecs, verbatim, even a '-'-leading one), had_sep says a boundary token was present."""
    for idx, a in enumerate(args):
        if a in _EOO_TOKENS:
            return args[:idx], args[idx + 1:], True
    return args, [], False


def _has_short(tokens, ch):
    """True when a clustered short-flag token (a single '-' then letters, e.g. '-fd') carries the letter
    ch. Used to spot '-f' inside a cluster (checkout '-f', clean '-fd', branch '-D' via ch='D')."""
    for t in tokens:
        if t.startswith("-") and not t.startswith("--") and len(t) > 1 and ch in t[1:]:
            return True
    return False


def _pathspec_from_file(args):
    """True when a subcommand sources its pathspecs from a file or stdin: a '--pathspec-from-file' (inline
    '=' or bare space form) OR a '--pathspec-file-nul' appears in the region given (the caller passes the
    PRE-boundary region only, so a literal pathspec named '--pathspec-from-file' after '--' is not misread
    as an option). When True the command-line paths are not the source, so the probe cannot enumerate them
    and the verb ASKS (cannot prove safe)."""
    for a in args:
        if a == "--pathspec-file-nul" or a == "--pathspec-from-file" or a.startswith("--pathspec-from-file="):
            return True
    return False


def _checkout_affects_index(pre):
    """True when a checkout carries a ref before its pathspecs, so it rewrites the INDEX as well as the
    worktree ('git checkout <ref> -- <paths>'); False for the index-preserving 'git checkout -- <paths>'.
    It receives the PRE-'--' region (post-'--' tokens are all pathspecs, never a ref), and walks it
    skipping the value-taking checkout option '--pathspec-from-file' with its space-form value and any
    other '-'-leading option token; a remaining bare token is a ref."""
    i = 0
    n = len(pre)
    while i < n:
        a = pre[i]
        if a == "--pathspec-from-file" and i + 1 < n:
            i += 2  # skip the option and its space-form value, which is not a ref
            continue
        if a.startswith("-"):
            i += 1
            continue
        return True  # a bare token: a ref
    return False


def _restore_long_kind(name):
    """Resolve a restore long-option name (no leading '--', no '=value') to 'staged', 'worktree',
    'source', 'ambiguous' (an unambiguous-prefix collision among these, which git also rejects), or None.
    Only these three loss-relevant options are considered."""
    if not name:
        return None
    cands = [w for w in ("staged", "worktree", "source") if w.startswith(name)]
    if len(cands) == 1:
        return cands[0]
    if len(cands) >= 2:
        return "ambiguous"
    return None


def _restore_flags(pre):
    """Resolve a restore command's pre-'--' tokens to (has_staged, has_worktree, valid) with git's
    semantics. Location flags are last-wins and honour '--no-*'; clustered short flags ('-SW' == '-S -W')
    and unambiguous long-option abbreviations ('--stag'/'--worktr') are decoded; the value-taking '-s'/
    '--source' (and abbreviations) consume the next token or the rest of the cluster as the ref. git's
    rule: if neither location is mentioned, the worktree is restored (a discard); '--staged' alone is a
    pure unstage (no worktree effect); an explicit negation or an ambiguous prefix (bare '--s') that leaves
    NO location is a git error, reported as valid=False so the caller treats it as not-a-discard (matching
    git rejecting it)."""
    staged = worktree = None  # None = not mentioned
    ambiguous = False
    skip_next = False
    for tok in pre:
        if skip_next:
            skip_next = False
            continue
        if tok.startswith("--"):
            name = tok[2:].split("=", 1)[0]
            neg = name.startswith("no-")
            if neg:
                name = name[3:]
            kind = _restore_long_kind(name)
            if kind == "staged":
                staged = not neg
            elif kind == "worktree":
                worktree = not neg
            elif kind == "source":
                if not neg and "=" not in tok:
                    skip_next = True  # space form: the ref is the next token
            elif kind == "ambiguous":
                ambiguous = True
        elif tok == "-s":
            skip_next = True
        elif tok.startswith("-") and len(tok) > 1:
            for ch in tok[1:]:
                if ch == "S":
                    staged = True
                elif ch == "W":
                    worktree = True
                elif ch == "s":
                    break  # the rest of the cluster is the --source value
    has_staged = staged is True
    if worktree is not None:
        has_worktree = worktree
    elif staged is None:
        has_worktree = True   # git default: restore the worktree
    else:
        has_worktree = False  # staged mentioned, worktree not: staged-scoped
    valid = not ambiguous and (has_staged or has_worktree)
    return has_staged, has_worktree, valid


def _restore_paths(pre, post):
    """Enumerate a restore's pathspecs: all post-'--' tokens, plus the bare pre tokens, skipping the
    space-form values of the value-taking options ('--source'/'-s'/'--pathspec-from-file'); a short-flag
    cluster ending in 's' (e.g. '-Ws', '-s') also consumes the next token as the --source ref, so skip it
    too. So 'git restore --source HEAD file.txt' enumerates file.txt (the path), not HEAD (the ref)."""
    paths = list(post)
    i = 0
    while i < len(pre):
        a = pre[i]
        if a in ("--source", "-s", "--pathspec-from-file") and i + 1 < len(pre):
            i += 2  # skip the option and its value
            continue
        if (a.startswith("-") and not a.startswith("--") and len(a) > 1
                and a.endswith("s") and i + 1 < len(pre)):
            i += 2  # a cluster-ending '-s' consumes the next token as the source ref
            continue
        if a.startswith("-"):
            i += 1  # any other option (including inline --source=.../-Sfoo) takes no separate token
            continue
        paths.append(a)  # a bare token is a pathspec
        i += 1
    return paths


def _reset_effective_mode(args):
    """The EFFECTIVE (last-option-wins) reset mode from the option region, or None if no mode flag is
    given (git defaults to --mixed). Honours git's unambiguous-prefix abbreviations ('--har' == '--hard').
    Option parsing STOPS at the first '--'/'--end-of-options', so a mode-looking operand past it is a
    pathspec, not a mode, and the value-taking '--pathspec-from-file' in its space form has its value
    SKIPPED so a file literally named '--hard' is not read as the hard mode: together these close F-57. A
    '--mode=value' form is invalid for these valueless flags (git errors), so it is ignored; an ambiguous
    prefix (bare '--m' shared by mixed/merge) is ignored (git also rejects it)."""
    modes = ("hard", "soft", "mixed", "merge", "keep")
    mode = None
    i = 0
    n = len(args)
    while i < n:
        a = args[i]
        if a in _EOO_TOKENS:
            break  # options end here; remaining tokens are operands, not modes (closes F-57)
        if a == "--pathspec-from-file" and i + 1 < n:
            i += 2  # skip the space-form value, which may be a file named like a mode (closes F-57)
            continue
        if a.startswith("--") and "=" not in a:
            cands = [m for m in modes if m.startswith(a[2:])]
            if len(cands) == 1:
                mode = cands[0]  # last-wins
        i += 1
    return mode


# --- repo-directory resolution (spec section 5) ------------------------------------------------------
# The worktree the discard would act on, resolved from: the payload cwd (the session dir), a LEADING
# cd/pushd parsed from the segment chain, cumulative '-C', and '--git-dir'/'--work-tree' (work-tree wins
# for the porcelain probe). A dir is represented as an absolute (or process-relative) path STRING when
# known, or None when it cannot be resolved (no cwd and only relative selectors, a 'cd $VAR', a 'cd -',
# an argless 'cd'); an unresolvable dir means the loss cannot be disproven, so a recognized lossy verb
# there ASKS rather than silent-allowing.

def _combine(base, value):
    """Combine a directory selector (a '-C'/cd value) with the base resolved so far, git/shell style: an
    absolute value resets (and is returned even when base is None, because it does not depend on base); a
    relative value is joined onto a known base, or yields None (unresolvable) when the base is unknown."""
    if os.path.isabs(value):
        return os.path.normpath(value)
    if base is None:
        return None
    return os.path.normpath(os.path.join(base, value))


def _cd_target(tokens):
    """The single literal directory operand of a leading 'cd'/'pushd' segment, or None when it cannot be
    resolved statically: an argless 'cd' (goes to HOME), 'cd -' (the previous dir), two or more operands,
    or an operand carrying a shell expansion we do not evaluate ('$VAR', '~', a glob, a command
    substitution). Option tokens (e.g. 'cd -P dir') are ignored when isolating the operand."""
    idx = _command_word_index(tokens)
    operands = [t for t in tokens[idx + 1:] if not t.startswith("-")]
    if len(operands) != 1:
        return None
    tgt = operands[0]
    if tgt.startswith("~") or "$" in tgt or "*" in tgt or "`" in tgt:
        return None
    return tgt


def _apply_cd(base, tokens):
    """Apply a leading 'cd'/'pushd' segment to the running base dir, returning the new base (a path string
    or None). An unresolvable target makes the base None (unknown); a later ABSOLUTE '-C' or 'cd' can still
    re-pin it, since _combine returns an absolute value regardless of base."""
    tgt = _cd_target(tokens)
    if tgt is None:
        return None
    return _combine(base, tgt)


def _resolve_repo(base, tokens):
    """The worktree dir a git segment acts on, from the base dir plus the segment's global options, or
    None when unresolvable. Scans only the global-option region (up to the first non-option token, the
    subcommand; a pathspec after it is never read): cumulative '-C' (each relative to the dir resolved so
    far, an absolute value resets, exactly as git chains -C), and '--work-tree' (which WINS for the probe,
    since that is the tree git operates on). '--git-dir' is the git dir, not the worktree, so it is
    skipped. Other arg-consuming global options have their value skipped so it is not misread."""
    repo = base
    worktree = None
    worktree_set = False
    i = _command_word_index(tokens) + 1  # after the command word; global options precede the subcommand
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "-C" and i + 1 < n:
            repo = _combine(repo, tokens[i + 1])
            i += 2
            continue
        if tok.startswith("-C") and len(tok) > 2:  # attached form '-C<dir>'
            repo = _combine(repo, tok[2:])
            i += 1
            continue
        if tok == "--work-tree" and i + 1 < n:
            worktree = _combine(repo, tokens[i + 1])
            worktree_set = True
            i += 2
            continue
        if tok.startswith("--work-tree="):
            worktree = _combine(repo, tok[len("--work-tree="):])
            worktree_set = True
            i += 1
            continue
        if tok.startswith("-"):  # another global option: skip its value too if it consumes one
            i += 2 if ("=" not in tok and tok in _GIT_ARG_OPTS) else 1
            continue
        break  # reached the subcommand; global options end here, never scan pathspecs after it
    return worktree if worktree_set else repo


# --- read-only probes (offline; the guard never mutates the repo) ------------------------------------

def _is_ref(repo, name):
    """True when `name` resolves to a commit in `repo` (a branch/tag/ref), so a bare 'git checkout <name>'
    is a branch switch, not a pathspec discard. Runs the read-only 'git rev-parse --verify --quiet
    <name>^{commit}'. On any subprocess error it returns False, so an unverifiable name is treated as a
    pathspec and routed through the porcelain probe (which itself ASKS when it cannot complete), never
    silent-allowed."""
    if repo is None:
        return False
    try:
        result = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet",
                                 name + "^{commit}"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _probe_porcelain(repo, paths, whole_tree, affects_index):
    """The tracked paths a discard would lose, or None when the probe cannot answer. Runs the read-only
    'git -C <repo> status --porcelain' (whole tree) or the same scoped to '-- <paths>'. Returns None on
    ANY inability to answer (a subprocess failure, a non-zero return, an unreadable repo), the ASK
    trigger; returns [] when it ran clean and nothing would be lost (the prove-safe path); returns the
    lost paths when a tracked change overlaps the target. An untracked '??' or ignored '!!' line is
    skipped (these verbs do not discard it). Whether a remaining line is a loss depends on which columns
    the form rewrites: a form that also rewrites the index (a ref-based checkout, a '--staged --worktree'
    restore, reset --hard, rm) loses on EITHER porcelain column (X or Y); a worktree-only form (a bare
    'checkout --', a default/worktree restore) loses only when the WORKTREE column (Y) shows a change, so
    a staged-only change does NOT count. A rename's post-'-> ' path is the surviving name."""
    try:
        cmd = ["git", "-C", repo, "status", "--porcelain"]
        if not whole_tree:
            cmd.append("--")
            cmd.extend(paths)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
        lost = []
        for line in result.stdout.splitlines():
            if not line:
                continue
            code = line[:2]
            if code in ("??", "!!"):
                continue
            x, y = code[0], code[1]
            is_loss = (x != " " or y != " ") if affects_index else (y != " ")
            if not is_loss:
                continue
            path = line[3:]
            if " -> " in path:  # a rename 'R  old -> new': the surviving name is after the arrow
                path = path.split(" -> ", 1)[1]
            lost.append(path)
        return lost
    except Exception:
        return None


def _probe_clean_removes(repo, args):
    """The paths 'git clean' WOULD remove, via the read-only dry run 'git -C <repo> clean -n <flags>', or
    None when the probe cannot answer. The user's own flags are mirrored (so -d/-x/-X scope the dry run to
    exactly what the real command would touch) with '-n' forced on top ('-n' wins over '-f', so this never
    deletes) and the interactive flags dropped (so the probe cannot hang). Each 'Would remove' line is one
    path; [] means the clean would remove nothing (prove-safe)."""
    try:
        probe_args = [a for a in args if a not in ("-i", "--interactive")]
        cmd = ["git", "-C", repo, "clean", "-n"] + probe_args
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
        return [ln[len("Would remove "):] for ln in result.stdout.splitlines()
                if ln.startswith("Would remove ")]
    except Exception:
        return None


# --- outcome builders --------------------------------------------------------------------------------
# Each _eval_* returns a 3-tuple (outcome, reason, banner): outcome is "allow"/"ask"/"deny"; reason and
# banner are the decision text and the systemMessage (both None for an allow). The handler picks the
# worst outcome across the command's segments (a deny beats an ask beats an allow).
_ALLOW_OUTCOME = ("allow", None, None)


def _deny_outcome(kind, files):
    reason = ("AIQT rule prsunc (preserve-uncommitted-work): {} would discard uncommitted tracked "
              "changes in {}, dropping any fix you have applied but not yet committed. {}"
              .format(kind, files, _DISCARD_ALTS))
    return ("deny", reason,
            "AIQT guardrail: blocked a git command that would discard uncommitted work (rule prsunc). "
            "Prefix GUARDRAIL_ALLOW_DISCARD=1 to override.")


def _ask_outcome(kind, detail):
    reason = ("AIQT rule prsunc (preserve-uncommitted-work): {} {}. Confirm before proceeding. {}"
              .format(kind, detail, _DISCARD_ALTS))
    return ("ask", reason,
            "AIQT guardrail: {} - confirm this discard, or prefix GUARDRAIL_ALLOW_DISCARD=1 to skip this "
            "prompt (rule prsunc).".format(kind))


def _porcelain_outcome(repo, paths, whole_tree, affects_index, from_file, deny_on_loss, kind):
    """Map a porcelain-probe verb (checkout/restore/reset/rm/switch) to an outcome. Unresolvable repo or
    file-sourced pathspecs -> ASK (cannot prove); a no-op with nothing named -> ALLOW; a completed probe
    that finds nothing -> ALLOW (prove-safe); a confirmed tracked overlap -> DENY (deny_on_loss) or ASK
    (the softer forms, e.g. reset --merge, whose loss is real but git may still abort)."""
    if repo is None:
        return _ask_outcome(kind, "targets a repository this guard could not resolve, so it cannot prove "
                                  "the change safe")
    if from_file:
        return _ask_outcome(kind, "sources its pathspecs from a file or stdin, which this guard cannot "
                                  "enumerate to prove the change safe")
    if not whole_tree and not paths:
        return _ALLOW_OUTCOME  # nothing named: a no-op, nothing to lose
    lost = _probe_porcelain(repo, paths, whole_tree, affects_index)
    if lost is None:
        return _ask_outcome(kind, "targets a repository whose status probe did not complete, so this "
                                  "guard cannot prove the change safe")
    if not lost:
        return _ALLOW_OUTCOME  # prove-safe: the probe ran and found nothing to lose
    files = "the working tree" if whole_tree else ", ".join(sorted(set(lost)))
    if deny_on_loss:
        return _deny_outcome(kind, files)
    return _ask_outcome(kind, "may discard uncommitted tracked changes in {}".format(files))


def _eval_checkout(repo, args):
    """git checkout: the worktree-affecting forms. '-f'/'--force' (a forced branch switch that overwrites
    the dirty worktree) -> whole-tree probe. An explicit '-- <paths>' (index-preserving) or '<ref> --
    <paths>' (index-rewriting) -> path probe. A branch create ('-b'/'-B'/'--orphan') -> ALLOW. A bare form
    with no separator is disambiguated by a rev-parse probe: a first operand that is a ref is a branch
    switch (ALLOW when it is the only operand; a ref + trailing pathspecs is an index+worktree path
    checkout); a first operand that is not a ref means every operand is a pathspec (a worktree discard,
    e.g. 'git checkout .')."""
    pre, post, had_sep = _split_pre_post(args)
    if "-f" in pre or "--force" in pre or _has_short(pre, "f"):
        return _porcelain_outcome(repo, None, True, True, False, True,
                                  "git checkout -f (a forced branch switch)")
    if any(a in ("-b", "-B", "--orphan") or a.startswith("--orphan=") for a in pre):
        return _ALLOW_OUTCOME  # creating/switching to a new branch; git aborts on a conflicting dirty tree
    if had_sep:
        from_file = _pathspec_from_file(pre)
        if not post and not from_file:
            return _ALLOW_OUTCOME  # 'git checkout --' with no paths: a no-op
        return _porcelain_outcome(repo, post, False, _checkout_affects_index(pre), from_file, True,
                                  "git checkout -- (a worktree revert)")
    if _pathspec_from_file(pre):
        return _ask_outcome("git checkout", "sources its pathspecs from a file or stdin, which this guard "
                                            "cannot enumerate to prove the change safe")
    bare = [a for a in pre if not a.startswith("-")]
    if not bare:
        return _ALLOW_OUTCOME  # no operand: nothing to discard
    if repo is None:
        return _ask_outcome("git checkout", "cannot be resolved to a repository, so this guard cannot tell "
                                            "a branch switch from a worktree-discarding pathspec")
    if _is_ref(repo, bare[0]):
        rest = bare[1:]
        if not rest:
            return _ALLOW_OUTCOME  # a clean branch switch: git itself aborts on a dirty tree
        return _porcelain_outcome(repo, rest, False, True, False, True,
                                  "git checkout <ref> <paths> (a worktree revert from a ref)")
    return _porcelain_outcome(repo, bare, False, False, False, True,
                              "git checkout <paths> (a worktree revert)")


def _eval_switch(repo, args):
    """git switch: only '-f'/'--force'/'--discard-changes' discard the worktree; a plain switch aborts on
    a dirty tree (git protects), so it is not a silent discard."""
    if ("-f" in args or "--force" in args or "--discard-changes" in args or _has_short(args, "f")
            or any(a.startswith("--discard") for a in args)):
        return _porcelain_outcome(repo, None, True, True, False, True,
                                  "git switch --force/--discard-changes (overwrites the worktree)")
    return _ALLOW_OUTCOME


def _eval_restore(repo, args):
    """git restore: a discard when it touches the worktree. '--staged' alone (a pure unstage) or a git-
    invalid flag combination is NOT a discard (ALLOW)."""
    pre, post, _had_sep = _split_pre_post(args)
    has_staged, has_worktree, valid = _restore_flags(pre)
    if not valid or not has_worktree:
        return _ALLOW_OUTCOME
    return _porcelain_outcome(repo, _restore_paths(pre, post), False, has_staged,
                              _pathspec_from_file(pre), True, "git restore (a worktree revert)")


def _eval_reset(repo, args):
    """git reset: a whole-tree discard only when the EFFECTIVE (last-wins) mode is --hard (DENY on a
    confirmed overlap) or --merge (ASK; --merge can lose but may also abort, so it is not a hard block).
    --mixed/--soft/--keep and a path-scoped reset preserve the worktree (ALLOW)."""
    mode = _reset_effective_mode(args)
    if mode == "hard":
        return _porcelain_outcome(repo, None, True, True, False, True, "git reset --hard")
    if mode == "merge":
        return _porcelain_outcome(repo, None, True, True, False, False, "git reset --merge")
    return _ALLOW_OUTCOME


def _eval_rm(repo, args):
    """git rm: removing a tracked path with uncommitted (staged or worktree) changes loses them. '--cached'
    only unstages and keeps the worktree file, so it is not a discard (ALLOW). A clean tracked path is
    recoverable from HEAD, so the probe (which reports only a dirty overlap) leaves it ALLOW."""
    if "--cached" in args:
        return _ALLOW_OUTCOME
    pre, post, _had_sep = _split_pre_post(args)
    paths = list(post)
    i = 0
    while i < len(pre):
        a = pre[i]
        if a == "--pathspec-from-file" and i + 1 < len(pre):
            i += 2
            continue
        if a.startswith("-"):
            i += 1
            continue
        paths.append(a)
        i += 1
    return _porcelain_outcome(repo, paths, False, True, _pathspec_from_file(pre), True,
                              "git rm (removes a tracked file with uncommitted changes)")


def _eval_stash(args):
    """git stash: 'drop' and 'clear' discard saved stash entries, which cannot be proven unwanted offline
    -> ASK. Every other stash form (push/save/pop/apply/list/show/branch/create/store) is not a discard of
    uncommitted work for this control's first cut."""
    first = next((a for a in args if not a.startswith("-")), None)
    if first in ("drop", "clear"):
        return _ask_outcome("git stash {}".format(first),
                            "discards saved stash entries, which this guard cannot prove are unwanted")
    return _ALLOW_OUTCOME


def _eval_branch(args):
    """git branch: a force-delete ('-D', or '--delete'/'-d' together with '--force'/'-f') can drop an
    unmerged branch's commits; proving a branch merged needs a rev walk this guard does not do offline ->
    ASK. A plain '-d'/'--delete' refuses an unmerged branch (git protects), so it is not a silent loss."""
    short = [t for t in args if t.startswith("-") and not t.startswith("--")]
    has_big_d = "-D" in args or any("D" in t[1:] for t in short)
    has_delete = "--delete" in args or any("d" in t[1:] for t in short)
    has_force = "--force" in args or any("f" in t[1:] for t in short)
    if has_big_d or (has_delete and has_force):
        return _ask_outcome("git branch -D (a force branch delete)",
                            "may drop unmerged commits, which this guard cannot prove are merged offline")
    return _ALLOW_OUTCOME


def _eval_clean(repo, args):
    """git clean: '-f'/'--force' removes untracked (and, with -x/-X, ignored) files, which are
    unrecoverable but a DIFFERENT asset from uncommitted tracked work, so a confirmed removal -> ASK (not a
    hard DENY). Without a force flag git refuses, and a user-supplied '-n'/'--dry-run' removes nothing, so
    neither is a discard. The dry run 'git clean -n' enumerates exactly what would go; nothing enumerated
    -> ALLOW."""
    if "--dry-run" in args or _has_short(args, "n"):
        return _ALLOW_OUTCOME  # the command is itself a dry run: it removes nothing
    if not ("--force" in args or _has_short(args, "f")):
        return _ALLOW_OUTCOME  # git refuses a clean with no force flag: no discard happens
    if repo is None:
        return _ask_outcome("git clean -f", "targets a repository this guard could not resolve, so it "
                                            "cannot prove nothing would be removed")
    removed = _probe_clean_removes(repo, args)
    if removed is None:
        return _ask_outcome("git clean -f", "targets a repository whose dry run did not complete, so this "
                                            "guard cannot prove nothing would be removed")
    if not removed:
        return _ALLOW_OUTCOME  # prove-safe: the dry run found nothing to remove
    return _ask_outcome("git clean -f", "would remove untracked files ({})"
                        .format(", ".join(sorted(set(removed)))))


# The per-subcommand evaluators that take (repo, args). stash and branch need no repo (no probe), so they
# are dispatched separately in _eval_git_segment.
_DISCARD_EVALS = {
    "checkout": _eval_checkout,
    "switch": _eval_switch,
    "restore": _eval_restore,
    "reset": _eval_reset,
    "rm": _eval_rm,
    "clean": _eval_clean,
}


def _eval_git_segment(repo, tokens):
    """Evaluate one git segment (command word already confirmed to be git) to an (outcome, reason, banner)
    tuple. Returns the ALLOW tuple for a non-discard subcommand."""
    sub, args = _git_sub_and_args(tokens)
    if sub is None:
        return _ALLOW_OUTCOME
    if sub == "stash":
        return _eval_stash(args)
    if sub == "branch":
        return _eval_branch(args)
    handler = _DISCARD_EVALS.get(sub)
    if handler is None:
        return _ALLOW_OUTCOME
    return handler(repo, args)


def git_discard(data):
    """prsunc (integ/preserve-uncommitted-work), PreToolUse/Bash. SCOPED-POSTURE guard (EN-6): fail-open
    ALLOW only at the true boundary (a non-git command, no recognized lossy verb, or an unparseable
    command); within scope it PROVES SAFE - a provably-safe discard ALLOWS, a CONFIRMED tracked-work loss
    DENIES, and a recognized lossy verb it cannot prove either way ASKS (never silent-allows, the EN-4
    leak). The unprovable middle ALWAYS asks regardless of interactivity, a deliberate conservative first
    cut (ask is recoverable). Every probe (git status/rev-parse/clean -n) is read-only and offline; the
    guard never mutates the repo. GUARDRAIL_ALLOW_DISCARD=<truthy> anywhere in the command opts out."""
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
        return _allow()  # boundary: unparseable (no raw-string discard scan)
    if _has_discard_optout(segments):
        return _allow()  # GUARDRAIL_ALLOW_DISCARD truthy anywhere
    cwd = data.get("cwd")
    base = cwd if isinstance(cwd, str) and cwd else None  # the session dir, or None when absent
    pending_ask = None
    for tokens, _sep in segments:
        cw = _command_word(tokens)
        if cw in ("cd", "pushd"):
            base = _apply_cd(base, tokens)  # thread a leading cd/pushd into the effective dir
            continue
        if cw != "git":
            continue
        repo = _resolve_repo(base, tokens)
        outcome, reason, banner = _eval_git_segment(repo, tokens)
        if outcome == "deny":
            return _deny(reason, banner)  # a confirmed loss wins immediately
        if outcome == "ask" and pending_ask is None:
            pending_ask = (reason, banner)
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
