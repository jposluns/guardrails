#!/usr/bin/env python3
"""AIQT Guardrails enforcement hooks for Claude Code. Stdlib only. Offline: every control is a lexical
scan, except git_discard, which runs one local, offline `git status` subprocess as its guard probe.

SOURCE tree copy: this file lives at .aiqt/core/hooks/scripts/aiqt_hooks.py and is copied
byte-identical into the generated plugin surface plugin/aiqt-guardrails-hooks/hooks/scripts/
aiqt_hooks.py by tools/gen_hooks.py; edit the source, never the generated copy. One dispatcher, one
handler function per control declared in .aiqt/core/hooks/manifest.toml:

  diff_wall_stop      Stop        cnsdif  surface (WARN) a unified-diff wall in the final assistant message
  diff_source_pretool PreToolUse  cnsdif  deny a Bash command that dumps a bare console diff
  commit_identity     PreToolUse  cmtidn  deny a git authoring command that names an AI identity
  absolute_paths      PreToolUse  abspth  deny a relative path where the tool requires absolute
  git_discard         PreToolUse  prsunc  block a git command that discards uncommitted tracked work (fail-OPEN)

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

git_discard (prsunc) is a DELIBERATE, documented fail-OPEN exception to that PreToolUse posture (like
the Stop-layer exception above, but for a different reason): it is a self-inflicted-fix-loss convenience
guard, not a security control, so it BLOCKS only a discard it can confirm is lossy and ALLOWS on every
doubt (a missing/other tool, an unreadable command, an unparseable command, an undeterminable repo, or
any git-status error), because a guard that traps the actor on its own malfunction gets disabled. It
never asserts an input is CLEAN, so it does not contradict integ-check-fails-closed-on-unreadable, which
governs a coverage check that would otherwise pass an unreadable input as clean.

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
# DELIBERATELY FAIL-OPEN, UNLIKE the other PreToolUse controls (which fail closed): per its rule a guard
# that traps the actor on its own doubt gets disabled, so this one blocks ONLY a git discard it can
# confirm is lossy and ALLOWS on every doubt (an unreadable/unparseable command, an undeterminable repo,
# any git-status error). It is a self-inflicted-fix-loss convenience guard, not a security control. It is
# quote-aware segmented (the shared _segments) and matches three whole-file discard shapes on a git
# segment: checkout with a '--' pathspec separator, restore that touches the worktree, and reset --hard.
# Before blocking it runs a local, offline `git status --porcelain` and blocks only when a TRACKED
# modification or staged change is reported for the target (an untracked '??'/ignored '!!' path never
# blocks). An explicit GUARDRAIL_ALLOW_DISCARD truthy assignment anywhere in the command opts out. It
# does NOT assert anything is clean, so it does not contradict integ-check-fails-closed-on-unreadable
# (which governs a coverage check that would otherwise pass an unreadable input as clean).
_DISCARD_OPTOUT_RE = re.compile(r"^GUARDRAIL_ALLOW_DISCARD=(.+)$")
_DISCARD_FALSY = frozenset(("", "0", "false", "no", "off"))  # value (case-insensitive) that is NOT truthy


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


def _pathspec_from_file(args):
    """True when a checkout/restore sources its pathspecs from a file or stdin: a '--pathspec-from-file'
    (inline '=' or bare space form) OR a '--pathspec-file-nul' appears in the arg region given. The caller
    passes the PRE-'--' region only (see _discard_shape), so a literal pathspec named '--pathspec-from-file'
    after the '--' separator is not misread as an option. When True the command-line paths are not the
    source, so the probe cannot enumerate them and the handler fails OPEN (seed-sanctioned)."""
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


def _restore_flags(pre):
    """Resolve a restore command's pre-'--' tokens to (has_staged, has_worktree, valid) with git's
    semantics. Location flags are last-wins and honour '--no-*'; clustered short flags ('-SW' == '-S -W')
    and unambiguous long-option abbreviations ('--stag'/'--worktr') are decoded; the value-taking '-s'/
    '--source' (and abbreviations) consume the next token or the rest of the cluster as the ref. git's
    rule: if neither location is mentioned, the worktree is restored (a discard); '--staged' alone is a
    pure unstage (no worktree effect); an explicit negation or an ambiguous prefix (bare '--s') that leaves
    NO location is a git error, reported as valid=False so the caller treats it as not-a-discard (fail-open,
    matching git rejecting it)."""
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


def _reset_mode(args):
    """Return the EFFECTIVE reset mode from the pre-'--' region, honouring git's last-option-wins and
    unambiguous-prefix abbreviations ('--har' == '--hard'), or None if no mode flag is given (git defaults
    to --mixed). A '--mode=value' form is invalid for these valueless flags (git errors), so it is ignored;
    an ambiguous prefix (bare '--m' shared by mixed/merge) is ignored (git also rejects it)."""
    modes = ("hard", "soft", "mixed", "merge", "keep")
    pre = args[:args.index("--")] if "--" in args else args
    mode = None
    for a in pre:
        if not a.startswith("--") or "=" in a:
            continue
        name = a[2:]
        cands = [m for m in modes if m.startswith(name)]
        if len(cands) == 1:
            mode = cands[0]  # last-wins
    return mode


def _reset_is_hard(args):
    """True only if the effective (last-wins) reset mode is --hard, the mode that discards both index and
    worktree. --mixed/--soft/--keep preserve the worktree; --merge can lose but is out of this control's
    documented --hard scope."""
    return _reset_mode(args) == "hard"


def _discard_shape(tokens):
    """The whole-file discard shape of a git segment (the caller has confirmed the command word is git),
    as a dict {"kind", "paths", "affects_index", "from_file"}, or None when it is not a discard. Option
    parsing is SCOPED to the region before the first '--' (pre); every token after it (post) is a pathspec
    verbatim, even a '-'-leading one, and the value-taking option values (checkout/restore
    '--pathspec-from-file', restore '--source'/'-s', including a cluster-ending '-s' like '-Ws' whose next
    token is the source ref) are skipped so they are not enumerated as paths.
    checkout: a discard with a '--' pathspec separator ('git checkout -- <paths>', 'git checkout <ref>
    -- <paths>'; paths are the post tokens) OR with a '--pathspec-from-file' source (the paths then come
    from a file, not enumerated). A 'git checkout <branch>' or '-b' (a branch switch/create) and a
    'git checkout <path>' with no '--' and no pathspec-from-file (ambiguous with a branch name) are NOT
    discards. restore: touches the worktree unless it is staged-only ('--staged'/'-S' with no
    '--worktree'/'-W'), so 'git restore --staged <path>' (unstage only) is NOT matched; paths are the post
    tokens plus the bare pre tokens (skipping the space-form values of value-taking options). reset: a
    discard only with '--hard' (a reset without it keeps the worktree); the whole tree is the target, so
    paths is None. git stash is out of scope (recoverable). affects_index says whether the form ALSO
    rewrites the index (see FIX 4): checkout with a ref, restore --staged, and reset --hard do; a bare
    checkout '--' and a default/worktree restore do not. from_file is True when the paths are sourced from
    a file or stdin ('--pathspec-from-file'/'--pathspec-file-nul'); the probe cannot enumerate them, so the
    handler fails OPEN (seed-sanctioned)."""
    sub, args = _git_sub_and_args(tokens)
    if "--" in args:
        idx = args.index("--")
        pre, post = args[:idx], args[idx + 1:]
    else:
        pre, post = args, []
    if sub == "checkout":
        from_file = _pathspec_from_file(pre)
        is_discard = ("--" in args) or from_file
        if not is_discard:
            return None
        return {"kind": "checkout", "paths": post,
                "affects_index": _checkout_affects_index(pre), "from_file": from_file}
    if sub == "restore":
        has_staged, has_worktree, valid = _restore_flags(pre)
        if not valid or not has_worktree:
            return None
        from_file = _pathspec_from_file(pre)
        # enumerate paths: all post tokens, plus bare pre tokens, skipping the space-form values of the
        # value-taking options ('--source'/'-s'/'--pathspec-from-file'). A short-flag cluster ending in
        # 's' (e.g. '-Ws', '-s') also consumes the next token as the --source value, so skip it too.
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
        return {"kind": "restore", "paths": paths, "affects_index": has_staged, "from_file": from_file}
    if sub == "reset":
        if _reset_is_hard(args):
            return {"kind": "reset-hard", "paths": None, "affects_index": True, "from_file": False}
        return None
    return None


def _join_repo(base, value):
    """Combine a '-C' selector with the directory selected so far, the way git chains multiple -C: an
    absolute value resets, a relative value is taken relative to the base selected up to this point."""
    return value if os.path.isabs(value) else os.path.join(base, value)


def _discard_repo_dir(tokens):
    """The worktree the discard would act on, from the '-C' global options that PRECEDE the subcommand.
    The scan runs only over the global-option region (it stops at the first non-option token, which is
    the subcommand, and never reads a pathspec after it), and it accumulates multiple '-C' selectors
    CUMULATIVELY as git does (each is relative to the directory selected so far; an absolute value
    resets). Default '.' (the invocation cwd). A '--git-dir' is the git dir, not the worktree, so it is
    ignored; only '-C' selects the worktree cwd. The handler's fail-open probe covers any oddity."""
    repo = "."
    i = _command_word_index(tokens) + 1  # after the command word; global options precede the subcommand
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "-C" and i + 1 < n:
            repo = _join_repo(repo, tokens[i + 1])
            i += 2
            continue
        if tok.startswith("-C") and len(tok) > 2:  # attached form '-C<dir>'
            repo = _join_repo(repo, tok[2:])
            i += 1
            continue
        if tok.startswith("-"):  # another global option: skip its value too if it consumes one
            i += 2 if ("=" not in tok and tok in _GIT_ARG_OPTS) else 1
            continue
        break  # reached the subcommand; global options end here, never scan pathspecs after it
    return repo


def _discard_would_lose_work(repo, shape):
    """The tracked paths a discard would lose, or [] when it would lose nothing (or cannot be confirmed).
    Runs a local, offline 'git -C <repo> status --porcelain' (for reset-hard, the whole tree) or the same
    scoped to '-- <paths...>' (for checkout/restore). NEVER raises: every error path (a subprocess or repo
    failure, a non-zero return, a checkout/restore with no probe paths, an unreadable pathspec file)
    returns [], the fail-open posture. An untracked '??' or ignored '!!' line is skipped (these commands
    do not discard it). Whether a remaining line is a loss depends on which columns the form rewrites
    (shape['affects_index'], set in _discard_shape): a form that rewrites the index too (a ref-based
    checkout, a --staged --worktree restore, reset --hard) loses on EITHER porcelain column (X or Y), so a
    staged-only change counts; a worktree-only form (a bare checkout '--', a default/worktree restore)
    loses only when the WORKTREE column (Y) shows a change, so a staged-only change does NOT block. When a
    '--pathspec-from-file'/'--pathspec-file-nul' source is set (shape['from_file']), the paths come from a
    file or stdin and are not enumerated here, so the probe fails OPEN (seed-sanctioned). A rename's
    post-'-> ' path is the surviving name."""
    kind = shape["kind"]
    affects_index = shape["affects_index"]
    try:
        probe_paths = shape["paths"]
        if kind in ("checkout", "restore"):
            if shape.get("from_file"):
                return []  # pathspecs sourced from a file or stdin: not enumerated, fail OPEN (seed-sanctioned)
            if not probe_paths:  # nothing named: fail open, do not probe the whole tree
                return []
        cmd = ["git", "-C", repo, "status", "--porcelain"]
        if kind in ("checkout", "restore"):
            cmd.append("--")
            cmd.extend(probe_paths)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return []
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
        return []


def git_discard(data):
    """prsunc (integ/preserve-uncommitted-work), PreToolUse/Bash. DELIBERATE fail-OPEN convenience guard,
    UNLIKE the other PreToolUse controls (which fail closed): per its rule, a guard that traps the actor
    on its own doubt gets disabled, so it blocks ONLY a discard it can confirm is lossy and allows on
    every doubt. It is a self-inflicted-fix-loss integrity guard, not a security control, and it does not
    assert anything is clean (so it does not contradict integ-check-fails-closed-on-unreadable, which
    governs a coverage check that would otherwise pass an unreadable input as clean)."""
    if data.get("hook_event_name") != PRETOOL:
        # A mis-wired event is a broken install: loud (unreachable given the generator's event whitelist).
        return _hard_block("aiqt_hooks: git_discard wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name != "Bash":
        return _allow()  # missing/other tool: allow (fail open, unlike the siblings' fail-closed posture)
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return _allow()  # unreadable/malformed command container: fail OPEN
    try:
        segments = _segments(command)
    except ValueError:
        return _allow()  # unparseable: fail OPEN, no raw-string discard scan
    if _has_discard_optout(segments):
        return _allow()  # GUARDRAIL_ALLOW_DISCARD truthy anywhere
    for tokens, _sep in segments:
        if _command_word(tokens) != "git":
            continue
        shape = _discard_shape(tokens)
        if shape is None:
            continue
        repo = _discard_repo_dir(tokens)
        lost = _discard_would_lose_work(repo, shape)  # a list of lost paths, or []; never raises
        if lost:
            files = "the working tree" if shape["kind"] == "reset-hard" else ", ".join(sorted(set(lost)))
            reason = (
                "AIQT rule prsunc (preserve-uncommitted-work): this command discards uncommitted changes "
                "in {}, which would also delete any fix you have applied but not yet committed (the "
                "mutation-testing fix-loss: reverting a temporary change reverts the whole file and drops "
                "the real fix with it). Do one of: (1) commit the fix first, then re-run this to revert "
                "the mutation, so the revert restores the committed fix; (2) revert only the mutated lines "
                "surgically, not the whole file; (3) git stash to shelve the fix, reset, then git stash "
                "pop. After any commit that claims a fix landed, verify it with git show <sha> --stat plus "
                "a grep of the committed content before writing 'fixed'. To override this guard for a "
                "known-safe discard, prefix the command with GUARDRAIL_ALLOW_DISCARD=1.".format(files))
            return _deny(reason,
                         "AIQT guardrail: blocked a git command that would discard uncommitted work "
                         "(rule prsunc). Prefix GUARDRAIL_ALLOW_DISCARD=1 to override.")
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
