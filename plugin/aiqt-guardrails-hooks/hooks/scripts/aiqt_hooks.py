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
  gate_weakening      PreToolUse  gatdis  deny a git hook bypass; ask a swallowed or truncated checker
  secrets_shift_left  PreToolUse  secsec  deny a Write/Edit/Bash writing an obvious hardcoded secret

Contract (doc-confirmed 2026-08-17 against code.claude.com/docs/en/hooks): the hook payload arrives
as JSON on stdin. A PreToolUse handler that decides emits, on exit 0,
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"|"deny",
"permissionDecisionReason": "..."}}; an allow decision is expressed as NO output (exit 0 silent), so
the user's own permission flow is never bypassed, and a deny decision blocks the tool. exit 2 is a
blocking error whose stderr is fed back to Claude. The Stop payload carries the final assistant text
as last_assistant_message (there is NO stop_hook_active field in the current Stop payload).

Error posture at the PreToolUse layer: FAIL CLOSED, for every control EXCEPT git_discard (whose
deliberate boundary posture is stated next). A fail-closed control that cannot read the input it is meant
to cover, or is invoked in a context it does not understand, DENIES rather than waving the action
through (per integ-check-fails-closed-on-unreadable): a missing tool_name, an unreadable command
string, or an unreadable required field all deny. A detected violation denies the same way. A clean
pass emits NO decision and exits 0 silently.

git_discard (prsunc) is a DELIBERATE, ULTRA-CONSERVATIVE "ask unless PRISTINE and provably clean" exception
to that fail-closed rule (EN-6). It has THREE outcomes: ALLOW (exit 0 silent), DENY, and ASK
(permissionDecision "ask", which prompts the human). UNLIKE the fail-closed controls above, it fails OPEN
(ALLOW) at the TRUE BOUNDARY - a non-Bash or absent tool, a malformed or missing tool_input.command it
cannot read as a discard, a non-git command, or no recognized lossy verb - because none of those is a
discard it can reason about, and it never silently allows a recognized WORKING-TREE-CONTENT discard (a
recognized verb in a genuinely non-destructive FORM - checkout -b, reset --soft, clean -n - preserves the
index and worktree content, though reset --soft still MOVES HEAD, a reflog-recoverable ref move, so it
discards no working-tree content and may ALLOW even on a dirty tree, as detailed below). That no-silent-allow
guarantee is bounded to WORKING-TREE CONTENT and is best-effort, not categorical: some ref-level moves (a
merged-branch delete, reset --soft moving HEAD) are reflog-recoverable, and the obfuscation/config residuals
disclosed below (a fragmented git command word or verb, a git alias, ambient config) can hide a discard the
lexical scan never sees. An UNPARSEABLE command
(unbalanced quote) is not a free pass, it is scanned raw for a lossy verb keyword and ASKS when one is
present. WITHIN scope (a recognized lossy verb: checkout/switch/restore/reset/clean/stash/
rm/branch) the outcome is ASK unless the command is a PRISTINE SINGLE BARE 'git <verb>' invocation AND the
tree is provably clean (or the leading opt-out is set, or the form is genuinely non-destructive). PRISTINE
SINGLE BARE means, PURELY LEXICALLY (the bash grammar is never parsed): after optional leading KEY=value
assignments the command is exactly 'git <args>' as ONE simple command, and the RAW string carries NO shell
metacharacter anywhere EVEN INSIDE QUOTES (none of ; | & < > ( ) { } $ backtick backslash ! newline, which
also rules out &&/||/|&, every redirection form, and every command/process substitution), no shell reserved
word, and a command word that is LITERALLY 'git' (not a path, not a wrapper such as sudo/nice/timeout/nohup/
env/command/exec/builtin/xargs/time/!/sh -c/bash -c). ANY shell metacharacter, wrapper, redirect, reserved
word, or second command makes the command not-pristine - a PURELY LEXICAL determination - and it ASKS
WITHOUT ever consulting the probe (a safe over-ask). An option the form-classifier cannot resolve is a
SEPARATE mechanism and does NOT bear on pristineness: a pristine command carrying an unresolved option still
reaches the probe, where its role routes to a scoped ASK, so it is not silently allowed either. A wrapper is
caught only while the raw scan still sees a contiguous git verb keyword, so 'any wrapper ASKS' is NOT
categorical (a wrapper that ALSO fragments the verb is a disclosed residual, below). Only a
metacharacter-free 'git <verb> <plain args>'
reaches the probe, where PROVABLY CLEAN means the read-only, config-forced porcelain probe (git -c
status.showUntrackedFiles=all status --porcelain --untracked-files=all, so a repo-local
status.showUntrackedFiles=no cannot hide an untracked file) reports NO tracked change AND NO untracked ('??')
entry (only an ignored '!!' entry counts as clean). A pristine bare whole-tree clobber (reset --hard,
checkout -f, switch --force/--discard-changes) on a probed-dirty tree DENIES; everything else in scope ASKS.
No recognized lossy verb that would discard WORKING-TREE CONTENT is silently ALLOWED except a pristine bare
'git <verb>' whose FORM is genuinely non-destructive (checkout -b, reset --soft, clean -n, which ALLOW even
on a dirty tree), or on a provably-clean tree, or a pristine bare form carrying the leading
GUARDRAIL_ALLOW_DISCARD=1 opt-out - worst case it ASKS, at the cost of more asks. This guarantee is bounded to
working-tree content: ref-level moves (reset --soft moving HEAD, a merged-branch delete) are reflog-
recoverable, and the obfuscation/config residuals below are best-effort, not categorical. The HONEST RESIDUAL a lexical hook cannot catch: a git alias or shell
function renaming git; deliberate token fragmentation or obfuscation of the COMMAND WORD 'git' ITSELF or of
the verb (a wrapper whose git command word or verb is SPLIT so neither the token scan nor the raw scan sees
a contiguous git+verb keyword, e.g. env git re'set' --hard, eval git re'set', or command g'it' reset --hard
/ env /usr/bin/g'it' reset --hard, whose raw string carries no contiguous 'git'+'reset', reads as 'no
recognized lossy verb' and is silently ALLOWED - a best-effort residual, not chased); a real discard whose
VERB is outside the recognized set AND the raw scan does NOT flag at all (a 'git worktree remove -f' of a
dirty linked worktree discards that worktree's uncommitted work, yet 'worktree' matches no lossy keyword, so
it is allowed at the true boundary - disclosed, not closed this round). A command the raw scan DOES flag as
in-scope whose resolved subcommand is nonetheless outside the recognized set ('git checkout-index -a -f',
'git read-tree -u --reset HEAD', flagged by their 'checkout'/'reset' substring) is NO LONGER silently
allowed: it ASKS (F-97), since a flagged sub the classifier cannot resolve to a known verb cannot be proven
non-destructive. A discard performed outside the Bash tool; or
persistent shell/config state; the deferred recovery/snapshot layer is the backstop. The one probe kept is
read-only and offline; it never mutates the repo.

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
import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

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
    "--config-env", "--attr-source"))  # --attr-source (git 2.40+) consumes its value in separated form;
    # git does NOT abbreviate top-level options, so exact membership is complete here (F-121)


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
    that also dumps the full diff alongside a summary flag (git diff --stat -p). Clustered short patch
    flags (-wp), patch-implying options (-U/-c/--cc/-L), and wrapped producers are NOT modelled here: the
    diff-dump guard is best-effort and those forms are a DISCLOSED lexical residual routed to a separate
    cnsdif hardening effort (F-119)."""
    return any(t in _PATCH_FLAGS or t.startswith("--patch") for t in tokens)


def _has_summary_flag(tokens):
    """True when a segment carries a summary/listing flag (--stat, --name-only, --name-status, --numstat,
    --shortstat), in either the bare or the '=value' shape. A summary flag is a listing rather than a raw
    diff, but ONLY when no patch flag is also present (see the handler: --stat -p still dumps the full
    diff), so the summary escape is gated on _has_patch_flag being false. A `--stat`/summary token in a
    NON-flag position - the value of a value-taking option (git diff -S --stat), a post-`--` pathspec
    (git diff -- --stat), or a redirect target (git diff > --stat) - is still counted as a summary flag; a
    role-aware fix would over-deny common commands such as `git diff -M --stat`, so this contrived
    mis-count is a disclosed residual rather than a fix (F-119)."""
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
    trailing stdout fd-dup after a real-file redirect flips the segment back to a console dump. A csh-style
    `>&file`/`&>file` that redirects BOTH streams to a real file is classified here as a to-descriptor dup
    and DENIED; that is a safe-direction over-deny (recoverable via `> file`), a disclosed residual (F-119)."""
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
    redirect also lands on a console/descriptor, so it is not a real-file redirect either. The tokenized
    form '2> file' (which shlex splits into '2', '>', 'file') redirects a non-stdout fd, so a '>'/'>>'
    preceded by a bare fd digit other than 1 is not a stdout real-file redirect (F-118). shlex cannot
    distinguish `2>` (an fd-2 redirect) from `2 > file` (a bare operand `2` plus a stdout redirect) - both
    tokenize identically - so a `>`/`>>` preceded by a bare fd digit other than 1 always takes the
    fail-safe non-stdout reading; the rare spaced `N > file` form is a safe over-deny, and the ambiguous
    `> realfile ... N > /dev/stdout` combination is a disclosed inherent residual (F-117/F-119)."""
    last_is_real_file = False
    n = len(tokens)
    for i, tok in enumerate(tokens):
        if tok in _REDIRECT_TOKENS and i + 1 < n:
            prev = tokens[i - 1] if i > 0 else ""
            if prev.isdigit() and prev != "1":
                continue  # 'N>' for N != 1 redirects a non-stdout fd (e.g. '2>' stderr); stdout still
                          # reaches the console, so this is not a stdout real-file redirect (F-118)
            last_is_real_file = not _DEV_PROC_TARGET_RE.match(tokens[i + 1])
        elif _is_stdout_fd_dup(tokens, i):
            last_is_real_file = False
    return last_is_real_file


def _has_info_flag(tokens):
    """True when a segment carries an info flag (--help or -h) as a GENUINE help invocation: git would
    show the subcommand's manual rather than run it, so the segment renders no diff and lands no push or
    commit. Judged on the segment's own token stream (never a raw substring), modelled on git's own
    argument parsing (F-117):
      - END-OF-OPTIONS: after a '--' or '--end-of-options' token every argument is positional (a pathspec or refspec), so a
        --help/-h at or after the first '--' is NOT help (git runs the command); only earlier tokens
        are considered.
      - VALUE / OPTION SLOT: a --help/-h that is the VALUE of a preceding separated option
        (git commit -m --help, git push -o --help --force) is that option's argument, not a help flag,
        so a token whose PREDECESSOR starts with '-' does not count.
      - REDIRECT TARGET: a --help/-h that is a shell redirect target (git ... > --help) is a filename,
        not a git argument, so a token whose predecessor is a redirect operator (a token of only the
        characters '<>&|') does not count.
    An info flag counts only when its predecessor is a plain word (the subcommand, an operand, or a
    consumed value), exactly where git treats it as help. Fail-safe by construction: every 'git actually
    runs it' shape is excluded, and incompleteness of any value-option list can never cause a silent
    allow. The safe-direction residual is an over-ask/over-deny on ANY complete option placed immediately
    before help (a valueless flag, git commit --amend --help, or an attached-value flag, git commit
    -mfoo --help / git push -ofoo --help --force), where git shows help but the preceding '-' token makes
    this treat the segment as live; recoverable, re-issue as 'git <subcommand> --help'."""
    end = len(tokens)
    for j, tok in enumerate(tokens):
        if tok == "--" or tok == "--end-of-options":  # git's two end-of-options spellings (F-117)
            end = j
            break
    for i in range(1, end):
        prev = tokens[i - 1]
        if (tokens[i] in _INFO_FLAGS and not prev.startswith("-")
                and not (prev and all(c in "<>&|" for c in prev))):
            return True
    return False


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
# ULTRA-CONSERVATIVE "ask unless PRISTINE and provably clean" guard (EN-6, EN-6 hardening pass over the
# GD-41 coarse cut). For a command that names any recognized lossy git verb (checkout incl -B force-create,
# switch incl -C/--force-create, restore/reset/clean/stash drop-clear/rm/branch force delete/move/copy/reset)
# the outcome is ASK unless the command is a PRISTINE SINGLE
# BARE 'git <verb>' invocation on a PROVABLY CLEAN tree (or, for that same pristine form, the LEADING opt-out
# is set). Three outcomes:
#   ALLOW  exit 0 silent  - the true boundary (a non-git command, no recognized lossy verb, an
#                           unparseable command with no lossy verb keyword); OR a PRISTINE SINGLE BARE
#                           'git <verb>' whose FORM is genuinely non-destructive (a bare no-op, reset
#                           --soft, an unforced branch-create, clean -n, stash push, branch -d); OR a
#                           PRISTINE SINGLE BARE lossy 'git <verb>' on a PROVABLY CLEAN tree (the config-
#                           forced porcelain probe reports no tracked change AND no untracked entry), so
#                           there is nothing to discard; OR the LEADING opt-out on that same pristine form.
#   DENY   permissionDecision deny - a PRISTINE SINGLE BARE WHOLE-TREE-clobbering verb (reset --hard,
#                           checkout -f with no pathspec, switch --force/--discard-changes) on a tree the
#                           probe confirms is dirty: the loss is certain.
#   ASK    permissionDecision ask  - EVERYTHING else in scope. This is deliberately the common outcome:
#                           ANY command that is not a pristine single bare git invocation - ANY shell
#                           metacharacter anywhere (even quoted), ANY wrapper/redirect/reserved-word/
#                           compound, a first command word that is not literally 'git', or an option the
#                           form-classifier cannot resolve - ASKS without ever consulting the probe; and a
#                           pristine lossy form on a not-provably-clean tree (tracked OR untracked change),
#                           a worktree the guard cannot resolve to the session cwd, a status probe that did
#                           not complete, or a softer/index-only discard (clean of untracked files, stash
#                           drop/clear, branch -D, restore --staged, mixed/path reset, rm --cached) also ASKS.
# The gate is PURELY LEXICAL and never parses the bash grammar: the presence of any shell metacharacter in a
# lossy-verb command is treated as a reason to ASK (a safe over-ask), so ONLY a metacharacter-free
# 'git <verb> <plain args>' ever reaches the probe. This closes the residual silent-allows that survived the
# coarse cut (a shell 'if'/'for', a backtick or '$()' substitution, a '|&' or leading/interspersed redirect,
# and ANY wrapper in front of git - sudo/stdbuf/doas/setsid/eval/sh -c/...: the raw 'git'+work-losing-verb
# scan is trusted UNCONDITIONALLY, not an enumerated wrapper list, so an un-listed wrapper is caught): ASK.
# That "cannot slip" is NOT categorical: a wrapper that ALSO fragments the command word 'git' or the verb so
# neither the token scan nor the raw scan sees a contiguous git+verb keyword reads as "no recognized lossy
# verb" and is silently ALLOWED (a DISCLOSED best-effort residual, see the module docstring; GD-34
# do-not-chase). The raw scan also OVER-ASKS in the SAFE direction: a command that merely CONTAINS a git
# token and a lossy verb as TEXT while running no discard ('echo git branch', 'git log | grep branch') still
# ASKS; narrowing that is the whack-a-mole GD-34 rejected, so the over-ask is disclosed, not chased.
# The status probe (git -c status.showUntrackedFiles=all status --porcelain --untracked-files=all) is
# read-only and offline and FORCES untracked reporting so a repo-local status.showUntrackedFiles=no config
# cannot hide an untracked file. The classifier never mutates working state; BENEATH it, the EN-6 recovery
# layer (see the block comment above _SNAPSHOTTABLE_VERBS) is SIDE-EFFECTING - it writes an inert
# refs/aiqt-recovery/* snapshot ref and an external ledger for a not-provably-clean discard - but writes only
# git objects and one private ref, never the real index, worktree, HEAD, or a branch. HONEST RESIDUAL (not
# caught by a lexical hook): a git alias or shell function that renames git, a discard performed outside the
# Bash tool, or persistent shell/config state set in a prior turn; the recovery/snapshot layer backstops a
# discard it CAN see, but cannot snapshot content the probe itself cannot see (assume-unchanged/skip-worktree
# marks or submodule.<name>.ignore hide work from the probe) nor ignored files (git add --all excludes them).
# An explicit GUARDRAIL_ALLOW_DISCARD truthy assignment LEADING a pristine bare git command opts out to ALLOW.
# The value capture is `(.*)` (matches an EMPTY value, `GUARDRAIL_ALLOW_DISCARD=`), consulted with fullmatch, so
# an exact leading assignment with an empty value updates the last-wins value to '' (bash: effective empty =
# falsy), and `GUARDRAIL_ALLOW_DISCARD=1 GUARDRAIL_ALLOW_DISCARD= git ...` does NOT opt out (the empty final
# assignment wins). A `(.+)` capture would ignore the empty final assignment and wrongly honour the earlier 1.
_DISCARD_OPTOUT_RE = re.compile(r"^GUARDRAIL_ALLOW_DISCARD=(.*)$")
_DISCARD_FALSY = frozenset(("", "0", "false", "no", "off"))  # value (case-insensitive) that is NOT truthy
# Fallback-only raw-string probes, used when shlex cannot parse the command (unbalanced quote). We cannot
# segment safely, so scan the RAW string for git AND a recognized work-losing verb: any of the
# always-lossy verbs, or 'branch' in ANY form. On the raw path we do NOT parse branch flags: an unparseable
# 'git branch -d -f topic <heredoc>' / '-df' / '--del --for' cannot be told from a create or a list, so ANY
# raw 'git' + 'branch' is treated as lossy and ASKS regardless of a delete flag. This over-asks a benign
# unparseable 'git branch --list' (rare, safe) and ends the raw-branch silent-allow gap.
# A hit -> ASK (cannot prove safe); no hit -> ALLOW (the true boundary). Mirrors how diff_source/
# commit_identity fall back to a conservative raw scan on a parse failure. The opt-out is NOT consulted on
# this path: the guard cannot parse the command, so it cannot trust an opt-out-looking prefix inside it; an
# unparseable in-scope command ALWAYS ASKS. The opt-out is honoured ONLY on a PARSEABLE pristine bare command.
_RAW_GIT_RE = re.compile(r"(?i)\bgit\b")
_RAW_LOSSY_VERB_RE = re.compile(r"(?i)\b(?:checkout|switch|restore|reset|clean|rm|stash)\b")
_RAW_BRANCH_RE = re.compile(r"(?i)\bbranch\b")
# Shell wrappers/prefixes that stand IN FRONT of the git command so 'git <lossy>' is not the invocation
# actually being classified. The EN-6 ultra-conservative pass widens the GD-41 set (command/exec/builtin/
# env/xargs/time/!) with the privilege/scheduling/interpreter prefixes sudo/nice/timeout/nohup/sh/bash: when
# one of these is the command word and a lossy git verb is present, the guard cannot classify with certainty,
# so it ASKS. (A pristine bare git never has a wrapper as its command word, so this only widens the ask set.)
_WRAPPER_WORDS = frozenset((
    "command", "exec", "builtin", "env", "xargs", "time", "!",
    "sudo", "nice", "timeout", "nohup", "sh", "bash"))
# The ULTRA-CONSERVATIVE pristine gate. A lossy-verb command is a PRISTINE SINGLE BARE 'git <verb>'
# invocation only when the RAW command string carries NONE of this shell structure ANYWHERE, even inside
# quotes (a deliberate over-ask): a metacharacter (; | & < > ( ) { } $ backtick backslash ! newline, which
# also covers &&/||/|&, every redirection form, and command/process substitution) or a shell reserved word.
# The gate NEVER parses the grammar; this lexical scan stands in for it, so only a metacharacter-free
# 'git <verb> <plain args>' ever reaches the clean probe. Everything else in scope ASKS.
_SHELL_META_RE = re.compile(r"[;|&<>(){}$`\\!*?\[\n\r]")  # includes glob chars *?[ (bash filename expansion)
_SHELL_RESERVED_WORDS = frozenset((
    "if", "then", "elif", "else", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "function", "select", "in", "[[", "]]", "{", "}"))
# The safe alternatives named in every DENY/ASK reason, so the actor is never left without a next step.
# The opt-out guidance is PATH-AWARE and appended by the caller. The opt-out is honoured on the command AS
# ISSUED whenever a leading GUARDRAIL_ALLOW_DISCARD=1 prefix short-circuits to ALLOW: on a pristine bare
# command the leading prefix opts THIS command out (_OPTOUT_PRISTINE), and because that short-circuit fires
# BEFORE the repository-view-redirect gate, a pristine repository-view-redirected form (a 'git -C <dir>
# <verb>' or an ambient-GIT_* form that ASKS at that gate) is ALSO opted out by prefixing the command as
# issued, keeping its -C (_OPTOUT_PRISTINE too). Only on a raw/unparseable or non-pristine command, where no
# pristine bare form is present to carry the prefix, is the opt-out NOT honoured on the command as issued, so
# the actor is told to RE-ISSUE the discard as a parseable pristine bare form carrying the prefix
# (_OPTOUT_REISSUE).
_DISCARD_ALTS = (
    "Safe alternatives: commit or 'git stash' your work first; scope the revert with an explicit "
    "'-- <paths>'; unstage without touching the worktree via 'git restore --staged' or 'git rm "
    "--cached'; change branch with 'git switch' (it carries non-conflicting changes and aborts rather than "
    "overwrite them).")
# Opt-out guidance for a PRISTINE bare form, where the leading prefix opts THIS command out.
_OPTOUT_PRISTINE = (
    " Or, for a known-safe discard, prefix this command with GUARDRAIL_ALLOW_DISCARD=1 to override this "
    "guard.")
# Opt-out guidance for a raw/unparseable or non-pristine form, where the opt-out is NOT honoured on the
# command as issued: re-issue it as a parseable pristine bare 'git <verb>' with the leading prefix. A
# repository-view-redirected form does NOT use this: it is pristine-lexically, so its leading prefix opts it
# out on the command as issued (_OPTOUT_PRISTINE, keeping its -C), short-circuiting before the redirect gate.
_OPTOUT_REISSUE = (
    " For a known-safe discard, re-issue it as a parseable pristine bare 'git <verb>' command carrying a "
    "leading GUARDRAIL_ALLOW_DISCARD=1 prefix; the opt-out is honoured only on that pristine bare form, not "
    "on the command as issued here.")


def _segment_has_optout(tokens):
    """True when THIS segment carries a truthy GUARDRAIL_ALLOW_DISCARD as a LEADING env-assignment (the
    documented 'GUARDRAIL_ALLOW_DISCARD=1 git ...' prefix on the git command itself). Only the leading
    assignment region (the tokens before this segment's command word) is inspected, so the same string
    buried in an argument does NOT count. The caller consults this ONLY on a segment whose command word is
    git (GD-41 blocker 4), so an opt-out leading a NON-git command ('GUARDRAIL_ALLOW_DISCARD=1 true; git
    reset --hard') never disables the guard on the later git command. A value in {'', '0', 'false', 'no',
    'off'} (case-insensitive) is NOT truthy. When the leading region repeats the assignment, bash last-wins
    applies: only the LAST GUARDRAIL_ALLOW_DISCARD assignment's value is evaluated (=1 then =0 does NOT opt
    out)."""
    last = None
    for tok in tokens[:_command_word_index(tokens)]:
        m = _DISCARD_OPTOUT_RE.fullmatch(tok)
        if m:
            last = m.group(1)  # bash last-wins: keep the LAST leading assignment's value, evaluate only it
    return last is not None and last.lower() not in _DISCARD_FALSY


def _raw_has_lossy_git(command):
    """True when the RAW command string names git AND a recognized work-losing verb (an always-lossy verb,
    or 'branch' in ANY form: the raw path does not parse branch flags, so any raw 'git' + 'branch' is
    treated as lossy regardless of a delete flag). A coarse fail-safe used both by the unparseable-command
    fallback and by the wrapper-obscured path (GD-41 blocker 8), where a wrapper hides the git verb from the
    token scan; a hit there means the guard cannot classify with certainty and ASKS."""
    if not _RAW_GIT_RE.search(command):
        return False
    return bool(_RAW_LOSSY_VERB_RE.search(command) or _RAW_BRANCH_RE.search(command))


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


def _has_long_prefix(tokens, full):
    """True when a '--<name>' option token in tokens is a CONSERVATIVE prefix of `full`. The match is
    deliberately broad: it fires whenever `name` is any leading substring of `full`, WITHOUT verifying that
    git itself would accept the abbreviation. Git accepts many UNAMBIGUOUS abbreviations ('--patc' for
    '--patch', '--del' for '--delete', '--dis' for '--discard-changes'), but it REJECTS an AMBIGUOUS one:
    'git branch --for' is ambiguous with '--format' and 'git switch --for' with '--force-create', so git
    errors rather than running '--force'. This guard intentionally treats such an ambiguous force-prefix
    ('--for') as force ANYWAY, routing it to ASK or DENY. The name compared is the part before any '=', so
    '--orphan=x' matches 'orphan'. A bare '--' (empty name) never matches. GD-41 blocker 5: a destructive
    option must be recognized by prefix, not only by its full spelling, or an abbreviated
    force/patch/delete/discard slips through as an inert token and is silently allowed. Erring toward a
    MATCH is safe: over-recognizing a destructive option, even an abbreviation git would reject as
    ambiguous, only routes a form to ASK or DENY, never to a silent allow."""
    for t in tokens:
        if not t.startswith("--"):
            continue
        name = t.split("=", 1)[0][2:]
        if name and full.startswith(name):
            return True
    return False


# --- the one probe kept: the coarse whole-tree clean signal ------------------------------------------

# The ambient git environment could redirect a real-state git call (the clean probe, the recovery
# snapshot) to a DECOY repository, inject config into it, hide content from it, or make a "read-only" call
# WRITE a trace file: an inherited GIT_* var can point git at a different index, object store, work tree,
# ref namespace, or discovery boundary than the one at `-C <repo>` (producing a false "provably clean", a
# false recovery point, or a dirty-tree ALLOW left unchanged), propagate config through the `git -c`
# GIT_CONFIG_PARAMETERS channel (able to hide untracked content via core.excludesFile or inject a
# filter.*.clean that runs during the snapshot `git add --all`), suppress system config via
# GIT_CONFIG_NOSYSTEM, redirect attribute lookup through GIT_ATTR_SOURCE, or (git treats an absolute
# GIT_TRACE value as a FILE PATH and APPENDS trace output to it) make even the read-only status probe and
# the recovery git calls WRITE a file, possibly inside the protected worktree, violating the read-only/inert
# posture. Rather than ENUMERATE this family (rounds 4-5 kept finding new members: GIT_CONFIG_*, then
# GIT_TRACE*, then GIT_ATTR_SOURCE), every real-state call takes an ALLOWLIST posture: _isolate_git_env
# scrubs EVERY ambient GIT_*-prefixed var before the caller applies its own env, so after the scrub a
# real-state call observes the ACTUAL repo at `-C <repo>` rather than an ambient-env decoy and writes no
# trace file; a snapshot call's own GIT_INDEX_FILE (supplied via env_extra) still wins because env_extra is
# applied AFTER the scrub. This is BOUNDED, not categorical: the allowlist scrub closes the whole ambient-env
# class at once, but the residual is NOT limited to on-disk config - it spans the non-GIT_ environment
# (HOME/XDG_CONFIG_HOME/PATH/TMPDIR), on-disk git configuration AND attributes (repo/global/system .gitconfig
# and .gitattributes), index and ignore state, submodules and embedded repos, configured hooks and filters,
# PATH-based git resolution, and partial-clone object availability, all of which git reads by design and none
# neutralized here.


# The FIXED INTERNAL identity the recovery snapshot's `git commit-tree` commits under. The allowlist scrub
# strips every ambient GIT_* (including any GIT_AUTHOR_*/GIT_COMMITTER_*) AND neutralizes on-disk config's
# reach for the call, so with no ambient identity re-applied commit-tree would FALL BACK to user.name/email
# and then FAIL on a host that has none (a CI runner or a fresh install with no global gitconfig), silently
# disabling the recovery backstop there. Re-applying this fixed identity after the scrub makes commit-tree
# depend on NO ambient state; a deterministic identity is also correct on its own terms, since a recovery
# commit is attributed to the guard, not to the user. The address is a fixed non-routable localhost one.
_RECOVERY_IDENTITY_NAME = "aiqt-recovery"
_RECOVERY_IDENTITY_EMAIL = "aiqt-recovery@localhost"


def _isolate_git_env(env):
    """Scrub EVERY ambient GIT_*-prefixed var from env in place, re-assert the PROTECTIVE vars and the fixed
    recovery commit identity, and return it (allowlist posture: no ambient git env is trusted). A real-state
    git call is fully specified by `-C <repo>` plus the few vars the caller re-applies AFTER this scrub
    (GIT_OPTIONAL_LOCKS for the read-only probe; a temp GIT_INDEX_FILE via env_extra for a snapshot), so it
    observes the ACTUAL on-disk repo and writes no trace file. AFTER the scrub this SETS GIT_NO_LAZY_FETCH=1
    and GIT_TERMINAL_PROMPT=0, so the scrub cannot strip an operator's offline/non-interactive posture:
    without them a partial-clone probe/add could LAZY-FETCH (network I/O, writes objects) instead of failing
    offline, and prompting could be re-enabled. It ALSO re-applies GIT_AUTHOR_NAME/EMAIL and
    GIT_COMMITTER_NAME/EMAIL as the fixed _RECOVERY_IDENTITY_* so the snapshot's `git commit-tree` never
    depends on an ambient or on-disk identity the scrub removed (without it commit-tree FAILS where no git
    identity is configured, silently disabling the recovery backstop); the read-only probes simply ignore
    it. A caller's own later env_extra (a temp GIT_INDEX_FILE) still wins because it is applied
    after this returns. Over-scrubbing fails SAFE: any GIT_* the call genuinely needed makes it error, and
    the caller treats that as a probe/snapshot FAILURE (None / SubprocessError) -> fail-to-ASK, never a
    silent allow. This closes the whole ambient-env class at once (GIT_CONFIG_*, GIT_CONFIG_PARAMETERS,
    GIT_TRACE*, GIT_ATTR_SOURCE, GIT_DIR/GIT_WORK_TREE, ...) instead of enumerating it. The residual is NOT
    limited to on-disk config: it spans the non-GIT_ environment (HOME/XDG_CONFIG_HOME/PATH/TMPDIR), on-disk
    git configuration AND attributes (repo/global/system .gitconfig and .gitattributes), index and ignore
    state, submodules and embedded repos, configured hooks and filters, PATH-based git resolution, and
    partial-clone object availability."""
    for _k in [k for k in env if k.startswith("GIT_")]:
        env.pop(_k, None)
    # Re-assert the offline + non-interactive posture the scrub would otherwise strip (Class B): keep a
    # partial-clone operation from lazy-fetching over the network, and keep git from ever prompting.
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    # Re-assert a fixed internal commit identity the scrub removed, so the snapshot's `git commit-tree`
    # never falls back to (now-scrubbed) ambient config and FAILS where no git identity is configured; the
    # read-only probes ignore it. A recovery commit is the guard's, not the user's, so the identity is fixed.
    env["GIT_AUTHOR_NAME"] = _RECOVERY_IDENTITY_NAME
    env["GIT_AUTHOR_EMAIL"] = _RECOVERY_IDENTITY_EMAIL
    env["GIT_COMMITTER_NAME"] = _RECOVERY_IDENTITY_NAME
    env["GIT_COMMITTER_EMAIL"] = _RECOVERY_IDENTITY_EMAIL
    return env


def _tree_is_clean(repo):
    """The clean-tree signal, the ONLY probe this guard keeps, hardened to be CONFIG-PROOF. True when the
    read-only porcelain probe reports NOTHING that a lossy verb could destroy: no uncommitted tracked change
    (any staged or unstaged modification, in EITHER porcelain column) AND no untracked ('??') entry. False
    when it reports either, None when the probe could not run (a subprocess error, a non-zero return, an
    unreadable repo). Untracked files are uncommitted work a force-checkout, a 'git clean', or a 'git reset
    --hard' can destroy, so an untracked-only-dirty tree is NOT provably clean.

    The probe FORCES untracked reporting - 'git -c status.showUntrackedFiles=all status --porcelain
    --untracked-files=all' - so a repo-local 'status.showUntrackedFiles=no' config cannot hide an untracked
    file from the guard and thereby win a silent allow for reset --hard / checkout -f / clean -f. The '-c'
    override and the explicit '--untracked-files=all' both defeat the config; either alone would suffice, and
    carrying both keeps the intent legible. An ignored '!!' entry is treated as clean (porcelain does not emit
    '!!' without --ignored anyway). A forced checkout or 'clean -x' CAN overwrite/remove an ignored file, but
    probing --ignored would ASK on every repo carrying build artifacts, so ignored-file loss on those forms is
    a DISCLOSED residual the recovery layer backstops (every non-dry-run clean already ASKS regardless).
    Deliberately coarse: ANY tracked change or untracked file anywhere makes the tree not-provably-clean.
    Offline, read-only, 5s timeout; the guard never mutates the repo. GIT_OPTIONAL_LOCKS=0 keeps this probe
    from refreshing/writing the real `.git/index`, and EVERY ambient GIT_*-prefixed var is scrubbed via
    _isolate_git_env (the allowlist posture, re-applying only GIT_OPTIONAL_LOCKS after the scrub) so the
    probe reads the REAL repo at `-C <repo>` rather than a foreign preset that could redirect it to a decoy
    clean repo and win a false ALLOW, and writes no ambient GIT_TRACE file."""
    try:
        env = _isolate_git_env(dict(os.environ))
        env["GIT_OPTIONAL_LOCKS"] = "0"
        result = subprocess.run(
            ["git", "-C", repo, "-c", "status.showUntrackedFiles=all",
             "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=5, env=env)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if not line or line[:2] == "!!":
                continue  # only an ignored entry is treated as clean
            return False  # a tracked change (staged or unstaged) OR an untracked file: not provably clean
        return True
    except Exception:
        return None


# --- verb-form recognition (coarse; erring toward lossy) ---------------------------------------------
# Each _*_role returns (role, kind): role is one of "allow" (this FORM never discards tracked work),
# "ask" (a softer discard that ASKS unconditionally, not gated on the tracked-tree probe - clean of
# untracked files, stash drop/clear, branch -D), "scoped" (an index- or worktree-touching discard: ALLOW on
# a provably-clean tree, else ASK; this now includes the index-only forms restore --staged, mixed/path
# reset, and rm --cached, which can erase staged-only content, GD-41 blocker 6), or "clobber" (a WHOLE-TREE
# overwrite: ALLOW on a clean tree, DENY on a confirmed-dirty tree). kind is a short human label. This
# recognition is purely lexical and never probes to prove an individual dirty-tree form safe; it only
# classifies the FORM, matching destructive options by CONSERVATIVE PREFIX (blocker 5; an ambiguous
# force-prefix like '--for' is treated as force, erring safe) and respecting a '--'
# operand boundary (blocker 3), then the handler gates a scoped/clobber form on the single whole-tree clean
# probe AND on the command being a single simple 'git <verb>' invocation.

# git checkout/switch short options that CONSUME a NEW-BRANCH-NAME argument: '-b'/'-B' (checkout create /
# force-create), '-c'/'-C' (switch create / force-create). The option letter itself is a genuine flag, but
# the REST of its cluster token (attached '-bfoo') or the NEXT token (separated '-b foo') is that branch
# name and must NOT be char-scanned as clustered force flags. Mirrors the branch '-u<upstream>' parser
# (_branch_parse_options, F-82). checkout has no -c/-C and switch has no -b/-B, so one shared set is safe.
_CHECKOUT_SWITCH_NAME_OPTS = frozenset(("b", "B", "c", "C"))


def _checkout_switch_parse_options(pre):
    """Parse a checkout/switch option region (already split before any '--') into (short_flags, operands).
    short_flags is the set of genuine short flag letters; operands is the list of bare operands. A branch-
    name-taking short option ('-b'/'-B' for checkout, '-c'/'-C' for switch) is recorded as a flag, then the
    remainder of its cluster token (attached '-bfoo') or the next token (separated '-b foo') is its NEW-
    BRANCH-NAME value and is NOT char-scanned as force flags. This mirrors _branch_parse_options (F-82) and
    closes the F-85 over-restriction where an attached name like '-bfoo' (the 'f') or '-bBranch' (the 'B')
    was char-scanned as carrying -f/-B and mis-routed to DENY/ASK. Long options are not char-scanned here
    (force/orphan/discard-changes/force-create match by prefix on `pre`)."""
    short_flags = set()
    operands = []
    skip_value = False  # the previous token opened a separated branch-name slot (e.g. '-b <name>')
    for tok in pre:
        if skip_value:
            skip_value = False
            continue  # this token is a prior option's branch-name VALUE, not a flag or an operand
        if tok.startswith("--"):
            continue  # long options match by prefix on `pre`, not char-scanned here
        if tok.startswith("-") and len(tok) > 1:
            body = tok[1:]
            for i, ch in enumerate(body):
                if ch in _CHECKOUT_SWITCH_NAME_OPTS:  # record the option, then its branch name is the
                    short_flags.add(ch)                # rest of this token (or the next token)
                    if i == len(body) - 1:
                        skip_value = True  # a bare '-b': the name is the next token
                    break  # stop the cluster scan; the remainder is the new-branch name, not flags
                short_flags.add(ch)
            continue
        operands.append(tok)  # a bare operand (a branch or a pathspec)
    return short_flags, operands


def _checkout_role(args):
    """git checkout. Force ('-f'/'--force', abbreviations included) is decided FIRST (GD-41 blocker 7). A
    '-B' force branch-create/RESET overwrites an existing branch ref and can orphan committed commits (the
    same reflog-recoverable loss class as 'git branch -f'/'-M'/'-C'), so it ASKS unconditionally, INCLUDING
    when combined with other flags (F-81), before the unforced branch-create allow. A plain branch-create
    ('-b'/'--orphan') only allows when force is ABSENT, because a FORCED branch-create can discard, so
    'checkout -f -b <name>' must fall through to a lossy classification, not the early
    allow. A '-m'/'--merge' or '--conflict[=<style>]' checkout (matched like the switch classifier,
    unambiguous-prefix aware) does a THREE-WAY merge that can overwrite local changes, so even combined
    with a branch-create it is not unconditionally safe -> scoped (ALLOW on a provably-clean tree, ASK on a
    dirty one), decided BEFORE the plain branch-create allow (F-88); a plain '-b'/'--orphan' create with NO
    merge option stays allow. The new-branch NAME of '-b'/'-B' is consumed as that option's ARGUMENT (attached '-bfoo' or
    separated '-b foo') and is NOT char-scanned as force flags, so '-bfoo'/'-bBranch' ALLOW and are never
    mis-read as carrying -f/-B (F-85; see _checkout_switch_parse_options). '-p'/'--patch' (prefix-matched)
    interactively discards worktree hunks -> scoped. A forced
    checkout that carries a BARE OPERAND ('checkout -f <operand>') is lexically ambiguous - the operand may
    be a branch (a whole-tree switch) OR a pathspec (a scoped path-restore), and it is no longer
    disambiguated by a rev-parse probe - so it is treated as -> scoped (which ASKS on a not-provably-clean
    tree; recoverable and human-gated), never a hard DENY that would false-block a legitimate forced
    path-restore. Any '-- <paths>' or other bare-operand form is scoped for the same reason. Only an
    operand-FREE forced checkout ('checkout -f' with no branch and no pathspec) clobbers the whole worktree
    with certainty -> clobber (which DENIES on a confirmed-dirty tree). A bare 'git checkout' with no options
    and no operands has no worktree effect -> allow."""
    pre, post, _had_sep = _split_pre_post(args)
    if _has_long_prefix(pre, "pathspec-from-file"):  # prefix-matched: '--pathspec-from-f' too
        return ("scoped", "git checkout --pathspec-from-file (reverts paths listed in a file)")
    short_flags, operands = _checkout_switch_parse_options(pre)  # '-b'/'-B' consume their branch name (F-85)
    force = "f" in short_flags or _has_long_prefix(pre, "force")
    if "B" in short_flags:  # -B force-creates/RESETS a branch ref, orphaning committed commits (F-81)
        return ("ask", "git checkout -B (a force branch-create/reset that overwrites an existing branch "
                       "ref and may orphan its commits)")
    if not force and ("m" in short_flags or _has_long_prefix(pre, "merge")
                      or _has_long_prefix(pre, "conflict")):  # F-88: a three-way merge, even with a -b
        return ("scoped", "git checkout --merge/--conflict (a three-way merge that can overwrite local "
                          "changes)")  # decided BEFORE the branch-create allow, mirroring the switch classifier
    branch_create = ("b" in short_flags or _has_long_prefix(pre, "orphan"))
    if branch_create and not force:
        return ("allow", None)  # create/switch to a new branch; git carries changes and aborts on conflict
    if "--patch" in pre or "p" in short_flags or _has_long_prefix(pre, "patch"):
        return ("scoped", "git checkout -p/--patch (an interactive worktree-hunk discard)")
    has_paths = bool(post) or bool(operands)
    # A forced branch-create ('checkout -f -b <name>') is lossy but NOT the operand-free whole-tree clobber
    # (blocker 7): its branch name is consumed by -b so it leaves no operand, yet it must stay SCOPED (a
    # recoverable ASK), not clobber. Only a force with NO branch-create and NO operand is the certain
    # whole-tree clobber. (Pre-F-85 the branch name was mis-counted as a bare operand and reached scoped via
    # has_paths; parsing it as -b's argument keeps that outcome without relying on the mis-count.)
    if force and not has_paths and not branch_create:
        return ("clobber", "git checkout -f (a forced branch switch that overwrites the worktree)")
    if has_paths or branch_create:
        return ("scoped", "git checkout (a worktree revert; a forced or branch-create form can discard)")
    if pre:  # options present but none recognized as safe (an abbreviated/exotic option) -> do not trust
        return ("scoped", "git checkout (an option this guard cannot prove non-destructive)")
    return ("allow", None)  # a truly bare 'git checkout': no options, no operand, no worktree effect


def _switch_role(args):
    """git switch. '-f'/'--force'/'--discard-changes' overwrites the worktree (whole-tree) -> clobber; force
    and discard-changes are matched by CONSERVATIVE prefix (blocker 5), so '--dis' is recognized and an
    ambiguous '--for' (which git itself rejects for switch as ambiguous with '--force-create') is treated as
    force anyway, erring safe.
    A '-C'/'--force-create' force branch-create/RESET (matched by prefix too) overwrites an existing branch
    ref and can orphan committed commits (the same reflog-recoverable loss class as 'git branch
    -f'/'-M'/'-C'), so it ASKS (F-81); plain '-c' create keeps the allow. The new-branch NAME of '-c'/'-C'
    is consumed as that option's ARGUMENT (attached '-cfoo' or separated '-c foo') and is NOT char-scanned
    as force flags, so '-cfeature' ALLOWs and is never mis-read as carrying -f/-C (F-85; see
    _checkout_switch_parse_options). '--force' is decided FIRST, so a
    genuine worktree clobber still DENIES rather than being downgraded to the force-create ASK.
    A '-m'/'--merge' or '--conflict[=<style>]' switch performs a THREE-WAY merge into the worktree that can
    overwrite local changes, so it is not unconditionally safe -> scoped (ALLOW on a provably-clean tree, ASK
    on a dirty one), matched by prefix too. A plain switch preserves non-conflicting local changes and aborts
    only when the switch would lose them (git protects), so it is not a silent discard -> allow."""
    pre, _post, _had_sep = _split_pre_post(args)
    short_flags, _operands = _checkout_switch_parse_options(pre)  # '-c'/'-C' consume their branch name (F-85)
    if ("f" in short_flags or _has_long_prefix(pre, "force")
            or _has_long_prefix(pre, "discard-changes")):
        return ("clobber", "git switch --force/--discard-changes (overwrites the worktree)")
    if "C" in short_flags or _has_long_prefix(pre, "force-create"):
        return ("ask", "git switch -C/--force-create (a force branch-create/reset that overwrites an "
                       "existing branch ref and may orphan its commits)")
    if "m" in short_flags or _has_long_prefix(pre, "merge") or _has_long_prefix(pre, "conflict"):
        return ("scoped", "git switch --merge/--conflict (a three-way merge that can overwrite local changes)")
    return ("allow", None)


def _restore_role(args):
    """git restore reverts tracked content, and NO form is unconditionally safe (GD-41 blocker 6): a
    worktree restore drops uncommitted edits, '-p'/'--patch' discards selected hunks, and even a pure
    '--staged' unstage can erase staged-ONLY content (present in the index but not HEAD or the worktree).
    So every restore routes through the whole-tree clean probe -> scoped: it ALLOWS on a provably-clean tree
    (a clean index has no staged-only content to lose) and ASKS on a dirty one. The earlier cut allowed a
    '--staged' unstage unconditionally, which silently discarded staged-only work."""
    return ("scoped", "git restore (reverts tracked content; --staged can erase staged-only changes)")


def _rm_role(args):
    """git rm removes tracked content, and NO form is unconditionally safe (GD-41 blocker 6): plain rm drops
    uncommitted worktree changes, and '--cached' unstages and can erase staged-ONLY content (present in the
    index but not HEAD or the worktree). So both route through the whole-tree clean probe -> scoped: ALLOW on
    a provably-clean tree, ASK on a dirty one. This also means an operand after '--' (git rm -f -- --cached,
    GD-41 blocker 3) can never be misread as the '--cached' option to win an allow: rm is scoped either way.
    The earlier cut allowed '--cached' unconditionally, which silently discarded staged-only work."""
    return ("scoped", "git rm (removes tracked content, dropping uncommitted or staged-only changes)")


def _reset_role(args):
    """git reset. The EFFECTIVE (last-wins) mode from the option region decides: an effective '--hard'
    overwrites the whole worktree -> clobber; only '--soft' is unconditionally safe -> allow (it moves HEAD
    only, leaving the index AND the worktree intact). Every other mode touches the index or worktree and can
    lose work, so it routes through the whole-tree clean probe -> scoped: '--merge' (may abort, may lose),
    '--mixed'/'--keep', a path-scoped reset, or NO mode flag (git defaults to --mixed, which unstages and can
    erase staged-ONLY content, GD-41 blocker 6 - the earlier cut allowed these unconditionally). Mode flags
    are matched by unambiguous PREFIX ('--ha' == '--hard'), and an ambiguous bare '--m' (mixed OR merge) is
    scoped either way; option parsing stops at the first '--'/'--end-of-options' so a mode-looking operand
    past it is a pathspec, not a mode."""
    pre, _post, _had_sep = _split_pre_post(args)
    if any(a == "--pathspec-from-file" or a.startswith("--pathspec-from-file=") for a in pre):
        return ("scoped", "git reset --pathspec-from-file (a path-scoped reset from a file)")
    mode = None  # "clobber" / "soft" / "scoped"
    for a in pre:
        if not (a.startswith("--") and "=" not in a):
            continue
        name = a[2:]
        if not name:
            continue
        if "hard".startswith(name):
            mode = "clobber"
        elif "soft".startswith(name):
            mode = "soft"
        elif ("merge".startswith(name) or "mixed".startswith(name) or "keep".startswith(name)):
            mode = "scoped"  # index/worktree-touching (an ambiguous '--m' also lands here): err toward probe
    if mode == "clobber":
        return ("clobber", "git reset --hard")
    if mode == "soft":
        return ("allow", None)  # HEAD-only: the index and worktree are untouched
    return ("scoped", "git reset (an index or worktree reset that can drop staged or unstaged changes)")


# git clean options that CONSUME the following token (so its value must not be read as a flag): '-e'/
# '--exclude PAT'. The '--exclude=PAT' inline shape carries its value in the same token, consuming none.
_CLEAN_ARG_OPTS = frozenset(("-e", "--exclude"))


def _clean_is_dry_run(pre):
    """True when a git clean option region (already split before any '--') is a genuine DRY RUN
    (-n/--dry-run) that removes nothing. ULTRA-CONSERVATIVE about the arg-consuming '-e'/'--exclude': when
    one is present the guard CANNOT reliably tell a real '-n' flag from an exclude PATTERN token that merely
    looks like '-n' (git clean -f -e '*.keep' -n, or the adversarial git clean -f -e -n where '-n' is the
    pattern), so it does NOT trust '-n' at all and returns False, routing the clean to its unconditional ASK.
    A '-n'/'--dry-run' with no arg-consuming option present still reports a dry run and allows."""
    for t in pre:
        if t in _CLEAN_ARG_OPTS or t.startswith("--exclude="):
            return False  # a separate/inline arg-consuming exclude: do not trust '-n'
        if t.startswith("-") and not t.startswith("--") and "e" in t[1:]:
            return False  # an ATTACHED short exclude ('-en' is '-e' with value 'n'): '-n' not trustworthy
    if _has_long_prefix(pre, "no-dry-run"):
        return False  # the negation (prefix-matched, e.g. '--no-dry-r') turns the dry run back on
    return "--dry-run" in pre or _has_short(pre, "n")


# git branch long options that CONSUME the following token as a REQUIRED separated value, so that value is
# never read as an operand or scanned as clustered force flags. Only required-arg options are listed:
# optional-arg long options (--track/--contains/--merged/--points-at/--color/--abbrev) take a value only in
# the attached '--opt=val' form and never consume a following token.
_BRANCH_LONG_ARG_OPTS = ("set-upstream-to", "sort", "format")
# Long options a '--<name>' abbreviation could ALSO mean; when a name could abbreviate one of these
# destructive options it is NOT treated as arg-consuming, so an ambiguous '--f' errs toward ASK rather than
# swallowing the following operand.
_BRANCH_FORCE_OPTS = ("force", "delete", "move", "copy")


def _branch_long_takes_arg(name):
    """True when the '--<name>' branch option (its '--' and any '=value' already stripped) is an unambiguous
    PREFIX of a git-branch long option that REQUIRES a separated value, so the NEXT token is that value
    rather than a flag or an operand. When `name` could also abbreviate a destructive option (--force/
    --delete/--move/--copy) it is NOT treated as arg-consuming, so an ambiguous '--f' still routes to ASK
    rather than swallowing the following operand and winning a silent allow."""
    if any(full.startswith(name) for full in _BRANCH_FORCE_OPTS):
        return False
    return any(full.startswith(name) for full in _BRANCH_LONG_ARG_OPTS)


def _branch_parse_options(pre):
    """Parse a 'git branch' option region (already split before any '--') into (short_flags, operands).
    short_flags is the set of genuine short flag letters; operands is the list of bare operands. An
    argument-taking option's VALUE is never added to either: the arg-taking short option is '-u <upstream>'
    (attached '-u<val>' or separated '-u <val>'), so a short cluster stops scanning at the first '-u' and the
    remainder of that token (or the next token) is the upstream value, NOT clustered force flags. This closes
    the F-82 over-ASK regression where a blind char-scan read '-ufoo'/'-uMain'/'-uCandidate' as carrying
    -f/-M/-C/-d. Long options are not char-scanned here (the force forms match them by prefix on `pre`); a
    required-arg long option ('--set-upstream-to'/'--sort'/'--format') consumes its separated value so it is
    not mistaken for an operand or a flag."""
    short_flags = set()
    operands = []
    skip_value = False  # the previous token opened a separated-value slot (e.g. '-u <upstream>')
    for tok in pre:
        if skip_value:
            skip_value = False
            continue  # this token is a prior option's VALUE, not a flag or an operand
        if tok.startswith("--"):
            name = tok.split("=", 1)[0][2:]
            if name and "=" not in tok and _branch_long_takes_arg(name):
                skip_value = True  # its value is the next token
            continue
        if tok.startswith("-") and len(tok) > 1:
            body = tok[1:]
            for i, ch in enumerate(body):
                if ch == "u":  # '-u <upstream>': the remainder of this token is the VALUE
                    if i == len(body) - 1:
                        skip_value = True  # a bare '-u': the value is the next token
                    break  # stop the cluster scan; the rest of this token is the upstream value
                short_flags.add(ch)
            continue
        operands.append(tok)  # a bare operand (a branch name)
    return short_flags, operands


def _discard_role(sub, args):
    """Classify a git segment's subcommand into (role, kind); see the block comment above. clean, stash,
    and branch do not use the tracked-tree probe: a real 'git clean' removes UNTRACKED files (a different,
    unrecoverable asset the tracked-change probe does not see), so ANY non-dry-run clean ASKS
    unconditionally (this also closes the clean.requireForce / -q / -i / clustered-flag edges, since the
    guard no longer tries to model whether the clean would fire); stash drop/clear ASK, and a force branch
    delete/move/copy/reset ASKS (an unforced create/list/-d keeps its allow)."""
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
        # Judge dry-run on the option region BEFORE any '--' (blocker 3): an operand after '--' (git clean
        # -f -- -nasty is a file literally named -nasty) must NOT be read as the '-n' dry-run flag, or a real
        # force-clean is misclassified as a harmless dry run and silently allowed. _clean_is_dry_run also
        # refuses to trust '-n' when an arg-consuming clean option ('-e'/'--exclude') is present, since '-n'
        # may be that option's value rather than the dry-run flag.
        pre, _post, _had_sep = _split_pre_post(args)
        if _clean_is_dry_run(pre):
            return ("allow", None)  # a dry run removes nothing
        return ("ask", "git clean (removes untracked files, which cannot be recovered)")
    if sub == "stash":
        first = next((a for a in args if not a.startswith("-")), None)
        if first in ("drop", "clear"):
            return ("ask", "git stash {} (discards saved stash entries)".format(first))
        # F-95: 'stash export' writes stash state to a ref, and its --to-ref form overwrites an arbitrary ref
        # UNCONDITIONALLY (no fast-forward or merged-ref safeguard), so every 'export' spelling ASKS; ASK for
        # all export forms (including --print) is acceptable per the disclosed over-ask posture.
        if first == "export":
            return ("ask", "git stash export (writes stash state to a ref, and --to-ref overwrites an "
                           "arbitrary ref unconditionally)")
        return ("allow", None)
    if sub == "branch":
        pre, post, _had_sep = _split_pre_post(args)
        short_flags, operands = _branch_parse_options(pre)
        operands = operands + post
        has_big_d = "D" in short_flags
        has_big_m = "M" in short_flags  # -M is 'move --force' (a force rename)
        has_big_c = "C" in short_flags  # -C is 'copy --force' (a force copy)
        # Delete, move, copy, and force are matched by CONSERVATIVE prefix too (GD-41 blocker 5):
        # '--del'/'--mov'/'--cop', and an ambiguous '--for' (which git itself rejects for branch as
        # ambiguous with '--format') is treated as force anyway, erring safe. Short flags are case-
        # sensitive, so -m/-c (unforced move/copy)
        # are distinct from -M/-C (their force forms). _branch_parse_options stops a short cluster at the
        # arg-taking '-u <upstream>' so an attached value ('-ufoo'/'-uMain') is the UPSTREAM, never scanned
        # as a clustered force flag (F-82 over-ASK regression: round-21's blind char-scan mis-read it).
        has_delete = _has_long_prefix(pre, "delete") or "d" in short_flags
        has_move = _has_long_prefix(pre, "move") or "m" in short_flags
        has_copy = _has_long_prefix(pre, "copy") or "c" in short_flags
        has_force = _has_long_prefix(pre, "force") or "f" in short_flags
        has_remotes = _has_long_prefix(pre, "remotes") or "r" in short_flags
        # F-94: a delete (-d/-D/--delete) combined with -r/--remotes deletes remote-tracking refs, which git
        # force-removes UNCONDITIONALLY, bypassing the merged-branch safeguard that protects a plain local
        # '-d'. So a delete+remotes ASKS even without an explicit force flag; a local non-force '-d' keeps its
        # allow below. Decided before the -D/force-delete branch so a delete-of-remotes gets its own reason.
        if has_remotes and (has_big_d or has_delete):
            return ("ask", "git branch -d/-D --remotes (deletes remote-tracking refs, which git "
                           "force-removes past the merged-branch safeguard)")
        # A force DELETE, force MOVE/rename, force COPY, or a bare force branch RESET each reset or overwrite
        # a branch ref and can orphan committed commits (the same reflog-recoverable loss class as -D), so all
        # ASK. A non-force create/list, an unforced -m/-c, and a safe -d delete keep their prior outcome.
        if has_big_d or (has_delete and has_force):
            return ("ask", "git branch -D/--delete --force (a force branch delete that may drop unmerged "
                           "commits)")
        if has_big_m or (has_move and has_force):
            return ("ask", "git branch -M/--move --force (a force rename that overwrites an existing branch "
                           "ref and may orphan its commits)")
        if has_big_c or (has_copy and has_force):
            return ("ask", "git branch -C/--copy --force (a force copy that overwrites an existing branch "
                           "ref and may orphan its commits)")
        if has_force and operands:
            return ("ask", "git branch -f/--force (a force branch reset that overwrites an existing branch "
                           "ref and may orphan its commits)")
        return ("allow", None)
    return ("allow", None)  # not a recognized lossy verb: the true boundary


# --- outcome text ------------------------------------------------------------------------------------

def _discard_ask_reason(kind, detail, optout=None):
    """The (reason, banner) pair for an ASK. Stored by the handler and emitted via _ask if no segment
    DENIES first, so a confirmed loss still wins over a recoverable ask. `optout` selects the PATH-AWARE
    opt-out guidance folded into the reason and the banner: _OPTOUT_PRISTINE (the default) on a pristine
    bare command, INCLUDING a pristine repository-view-redirected form (its leading GUARDRAIL_ALLOW_DISCARD=1
    prefix opts THIS command out, short-circuiting even the redirect gate), or _OPTOUT_REISSUE on a
    raw/unparseable or non-pristine command where no pristine bare form is present, so the opt-out is honoured
    only on a re-issued pristine bare form, never on the command as issued."""
    if optout is None:
        optout = _OPTOUT_PRISTINE
    reason = ("AIQT rule prsunc (preserve-uncommitted-work): {} {}. Confirm before proceeding, or commit "
              "or stash your work first. {}{}".format(kind, detail, _DISCARD_ALTS, optout))
    if optout is _OPTOUT_REISSUE:
        banner = ("AIQT guardrail: {} - confirm this discard, or re-issue it as a parseable pristine bare "
                  "'git <verb>' command carrying a leading GUARDRAIL_ALLOW_DISCARD=1 prefix to skip this "
                  "prompt (the opt-out is not honoured on the command as issued) (rule prsunc)."
                  .format(kind))
    else:
        banner = ("AIQT guardrail: {} - confirm this discard, or prefix GUARDRAIL_ALLOW_DISCARD=1 to skip "
                  "this prompt (rule prsunc).".format(kind))
    return (reason, banner)


def _discard_deny(kind):
    """A DENY: a whole-tree-clobbering verb on a tree the probe confirms is dirty (an uncommitted tracked
    change OR an untracked file the verb could reach), so the loss is certain. The wording covers untracked
    too, because the config-forced probe now counts an untracked-only-dirty tree as dirty."""
    reason = ("AIQT rule prsunc (preserve-uncommitted-work): {} would overwrite the working tree, which "
              "currently holds uncommitted or untracked changes the command could destroy, discarding any "
              "fix you have applied but not yet committed. {}{}"
              .format(kind, _DISCARD_ALTS, _OPTOUT_PRISTINE))
    banner = ("AIQT guardrail: blocked a git command that would discard uncommitted work (rule prsunc). "
              "Prefix GUARDRAIL_ALLOW_DISCARD=1 to override.")
    return _deny(reason, banner)


# --- worktree-certainty (coarse; replaces the removed dir modelling) ---------------------------------

# FAIL-SAFE repository-view check. An ambient GIT_*-prefixed var in the process env can point git at a
# different git dir, work tree, common dir, index, object store, ref namespace, replace/reference view, or
# config/attribute source than the session cwd. The clean probe scrubs EVERY ambient GIT_* before it runs
# (so the PROBE reads the REAL cwd repo), but the guard cannot scrub the operator's shell env from the
# ACTUAL command git will run: with an ambient GIT_DIR at a dirty repo and a clean cwd, the probe reads
# clean and would ALLOW while the real command clobbers the redirected dir; with an ambient GIT_INDEX_FILE
# the probe reads the default (clean) index while the command discards the custom one. Enumerating the
# redirecting vars proved a whack-a-mole (round after round found new members: GIT_NO_REPLACE_OBJECTS,
# GIT_REPLACE_REF_BASE, GIT_REFERENCE_BACKEND, and more will exist), so this FLIPS to fail-safe: ANY
# ambient GIT_* var forces ASK EXCEPT a small COSMETIC allowlist proven not to change the discard target,
# the object/ref view, the index, or dirtiness detection - only UI/editor/pager/prompt/lock/identity
# concerns. A GIT_TRACE* var is NOT cosmetic (an absolute GIT_TRACE value makes the ACTUAL command append
# trace output to that path, which could be a repo file), so it too forces ASK. An unknown or new GIT_* var
# ASKS rather than silently allowing.
_COSMETIC_GIT_VARS = frozenset((
    "GIT_PAGER",             # selects the pager UI; no effect on target/view/index/dirtiness
    "GIT_EDITOR",            # selects the commit-message editor; UI only
    "GIT_SEQUENCE_EDITOR",   # editor for the rebase todo list; UI only
    "GIT_ASKPASS",           # credential-prompt helper; auth UI only
    "GIT_TERMINAL_PROMPT",   # whether git prompts on the terminal; interactivity only
    "GIT_OPTIONAL_LOCKS",    # whether to take optional locks; performance, not target/dirtiness
    "GIT_NO_LAZY_FETCH",     # whether to lazy-fetch missing objects; network posture, not the view
    "GIT_ADVICE",            # whether to print advice hints; output UI only
    "GIT_FLUSH",             # output buffering/flushing; I/O behaviour only
    "GIT_MERGE_AUTOEDIT",    # whether merge auto-opens the editor; UI only
    "GIT_AUTHOR_NAME",       # author identity stamped on new commits; not the working-tree view
    "GIT_AUTHOR_EMAIL",      # author identity; not the view
    "GIT_AUTHOR_DATE",       # author date; not the view
    "GIT_COMMITTER_NAME",    # committer identity; not the view
    "GIT_COMMITTER_EMAIL",   # committer identity; not the view
    "GIT_COMMITTER_DATE",    # committer date; not the view
))


def _ambient_repo_view_override():
    """FAIL-SAFE: True when the ambient process environment carries ANY GIT_*-prefixed variable that is
    NOT in the small cosmetic allowlist. Such a variable can point git at a
    different git dir, work tree, index, object/ref view, replace/reference view, or config/attribute
    source than the session cwd. The clean probe scrubs these before it runs, so IT reads the real cwd
    repo, but the guard cannot scrub them from the ACTUAL command the shell runs: that command still
    inherits them and may act on a DIFFERENT repository view than the one probed. Enumerating the
    redirecting vars was a whack-a-mole (new members kept appearing: GIT_NO_REPLACE_OBJECTS,
    GIT_REPLACE_REF_BASE, GIT_REFERENCE_BACKEND), so this asks whenever it cannot PROVE the scrubbed
    cwd-probe matches the command's actual repository view: an unknown or new GIT_* var ASKS rather than
    silently allowing (clean becomes not-provably-clean -> ASK, never a silent ALLOW). Only clearly
    cosmetic UI/editor/pager/prompt/lock/identity vars, which cannot change the discard target, the
    object/ref view, the index, or dirtiness detection, are allowed through. A GIT_TRACE* var is NOT
    cosmetic and ASKS: an absolute GIT_TRACE value makes the ACTUAL command append trace output to that
    path, which could be a file inside the repo, so it is not provably view-neutral (the recovery/probe git
    calls still scrub the whole GIT_TRACE family, so THEY write no trace, but the command's own ambient
    GIT_TRACE is not neutralized)."""
    for key in os.environ:
        if not key.startswith("GIT_"):
            continue
        if key in _COSMETIC_GIT_VARS:
            continue
        return True
    return False


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


def _git_discard_fallback(command, cwd=None):
    """FAIL-SAFE conservative scan when shlex cannot parse the command (unbalanced quote): we cannot
    segment safely, so scan the RAW string. The opt-out is NOT consulted here: the guard cannot parse the
    command, so it cannot soundly trust an opt-out-looking prefix inside it (a quoted-falsy `="0"`, an
    interspersed `OTHER=x`, an opt-out on a DIFFERENT command `...=1 true; git`, a captured-truthy `0;` all
    read as opt-outs to a raw scan), so an unparseable in-scope command ALWAYS ASKS regardless of any leading
    opt-out-looking prefix. The opt-out is honoured ONLY on a PARSEABLE pristine bare command (see
    _segment_has_optout). If git is present AND a recognized work-losing verb keyword is present (an
    always-lossy verb, or 'branch' in any form) -> ASK (cannot prove safe); otherwise ALLOW (the true
    boundary). Documented best-effort: a genuinely clean but unparseable command that merely mentions a lossy
    verb keyword may ASK.

    Class C: an unparseable command reaching an ASK is a NON-PRISTINE discard, so it gets the SAME
    best-effort SESSION-CWD snapshot the non-pristine path takes (base resolvable + tree not provably
    clean) BEFORE returning the ASK, so a discard that is asked-then-approved is still recoverable. Without
    it, `git reset --hard <<'EOF'\\n'\\nEOF` (Bash-valid, shlex ValueError) reached ASK with NO recovery
    ref. The snapshot is decision-INDEPENDENT and best-effort against the session repo only (the verb is
    unparseable, so the ledger label is a generic 'discard'); on snapshot fail the decision stays ASK with
    the failure surfaced."""
    if not _raw_has_lossy_git(command):
        return _allow()  # no git, or no recognized work-losing verb: the true boundary
    base = cwd if isinstance(cwd, str) and cwd else None
    snap = None
    if base is not None and _tree_is_clean(base) is not True:  # dirty or probe-uncertain: snapshot first
        snap = _record_recovery(base, "discard")
    reason = (
        "AIQT rule prsunc (preserve-uncommitted-work): the command could not be parsed by the shell lexer "
        "(likely an unbalanced quote) and it names a git work-losing verb this guard cannot prove safe; "
        "asking rather than silently allowing. {}{}".format(_DISCARD_ALTS, _OPTOUT_REISSUE))
    banner = (
        "AIQT guardrail: an unparseable git command names a work-losing verb this guard could not prove "
        "safe - confirm this discard. The GUARDRAIL_ALLOW_DISCARD opt-out is NOT honoured on an unparseable "
        "command (the guard cannot parse it); to opt out, re-issue the discard as a parseable bare git "
        "command with the leading prefix (rule prsunc, fail-safe).")
    if snap is not None and snap[0] == "ok":
        reason = reason + " " + _recovery_pointer(snap[1])
    elif snap is not None and snap[0] == "fail":
        reason = reason + (" NOTE: no pre-command recovery snapshot could be created ({}), so this discard "
                           "would not be recoverable by this guard.".format(snap[1]))
    return _ask(reason, banner)


def _pristine_single_bare_git(command, segments):
    """Return the token list of the sole git command when `command` is a PRISTINE SINGLE BARE 'git <verb>'
    invocation, else None (=> the caller ASKS). ULTRA-CONSERVATIVE and PURELY LEXICAL - the bash grammar is
    never parsed. Pristine requires ALL of:
      * the RAW command string carries NO shell metacharacter anywhere, EVEN INSIDE QUOTES (a deliberate
        over-ask): none of ; | & < > ( ) { } $ backtick backslash ! or a newline, which by construction also
        rules out &&/||/|&, every redirection form (>, >>, <, 2>, &>, >&, n>&m, a leading or interspersed
        redirect), and every command/process substitution ($( ), backtick, <( ), >( ));
      * no shell RESERVED-WORD token (if/then/for/while/case/[[/{/... a compound-command keyword);
      * exactly ONE operator-free command segment (guaranteed once no metacharacter is present, asserted);
      * after optional leading KEY=value env/opt-out assignments, the sole command word is LITERALLY 'git'
        (not a path like /usr/bin/git, and not a wrapper such as sudo/env/sh -c/... whose word is not 'git').
    Anything else - a compound, a wrapper/redirect/reserved-word, or a command word that is not literally
    'git' - returns None so the guard ASKS rather than trust the clean probe on a command it cannot read
    with certainty."""
    if _SHELL_META_RE.search(command):
        return None  # any shell metacharacter (even quoted): not pristine -> ASK
    # With no metacharacter there is exactly one operator-free segment; require precisely that.
    populated = [(toks, sep) for toks, sep in segments if toks or sep]
    if len(populated) != 1 or populated[0][1]:
        return None
    tokens = populated[0][0]
    if any(t in _SHELL_RESERVED_WORDS for t in tokens):
        return None  # a shell reserved word: not a bare git invocation -> ASK
    idx = _command_word_index(tokens)  # skip leading KEY=value assignments (incl. the opt-out)
    if idx >= len(tokens) or tokens[idx] != "git":
        return None  # a wrapper, a pathed git, or no command word: not a bare 'git' -> ASK
    return tokens


# --- the recovery/snapshot layer (EN-6): an INERT git-DB sealed-epoch snapshot -----------------------
# BENEATH the ultra-conservative classifier sits a recovery layer that makes a discard RECOVERABLE. Before
# git_discard returns its decision for an in-scope lossy verb whose worktree it can resolve to the session
# cwd AND whose tree is NOT provably clean, it takes an INERT snapshot of the uncommitted work, on the ALLOW
# and the ASK paths alike (the hook fires once at PreToolUse; there is no post-approval callback, so a
# discard that is asked-then-approved, or one wrongly allowed by a classifier mis-parse, must ALREADY have a
# recovery point). The snapshot is decision-INDEPENDENT by design: it does not trust the form-classifier to
# be right about which forms are safe, so it fires for every snapshottable verb on a not-provably-clean tree
# regardless of the allow/ask/deny verdict. It is skipped when the tree is PROVABLY CLEAN (nothing the probe
# can see to lose), when the command is not in scope, and for stash/branch (a worktree snapshot cannot
# capture stash entries or branch commits; ref-pinning for those is a separate, deferred mechanism).
# F-D EXPANSION: a NON-PRISTINE in-scope ASK (a compound/wrapped/redirected snapshottable command) is now
# also snapshot-backed BEST-EFFORT against the SESSION CWD repo, so an asked-then-approved wrapped discard is
# recoverable too. It is best-effort because a non-pristine command's redirected dir is NOT parsed: a command
# that changes into a DIFFERENT repository may be snapshotted at the session repo rather than the target (a
# same-repo cd is still captured by the whole-tree `git add --all`); on snapshot fail the decision stays ASK.
#
# Mechanism (all stdlib/subprocess, offline): the repo TOPLEVEL is resolved once (the anchor for the size
# estimate and the inside-the-repo containment checks, since status paths are toplevel-relative and the cwd
# may be a subdir); a TEMPORARY GIT_INDEX_FILE in a throwaway dir normally OUTSIDE the toplevel (best-effort
# and guarded: the snapshot FAILS rather than write inside the tree when TMPDIR resolves within it), seeded
# by COPYING the real index (so staged content is the baseline); `git add --all` into that temp index
# overlays tracked-modified and UNTRACKED content (ignored excluded); `git write-tree` (temp index) -> a
# tree; `git commit-tree <tree> [-p HEAD]` -> a snapshot commit; then a CREATE-ONLY `git update-ref
# refs/aiqt-recovery/<utc-ts>-<pid> <sha> ""` (empty expected-old value: a name collision FAILS the snapshot
# rather than clobbering a prior recovery ref) protects it from GC.
#
# The BOUND on "inert" (honest framing): the snapshot's OWN git operations write only git objects and one
# private ref (plus a reflog entry for that ref when core.logAllRefUpdates=always, or when a reflog already
# exists for the ref) and do NOT themselves
# modify the real index, worktree, HEAD, or any branch; the ref is invisible to plain `git status`, `git
# branch`, and `git log`, THOUGH reachable via `git log --all` / `git for-each-ref refs/aiqt-recovery` /
# `git show-ref` (a real ref, not hidden). The selftest asserts the real status/index/HEAD, index bytes,
# config, and stash list are unchanged. Every real-state call in this layer scrubs EVERY ambient GIT_*-prefixed
# var (_isolate_git_env, the allowlist posture), so an ambient GIT_CONFIG_PARAMETERS injection, a
# GIT_ATTR_SOURCE redirect, or a GIT_TRACE file-write vector cannot reach these calls; the residual is NOT
# limited to on-disk config - it spans the non-GIT_ environment (HOME/XDG_CONFIG_HOME/PATH/TMPDIR), on-disk
# git configuration AND attributes (repo/global/system), index and ignore state, submodules and embedded
# repos, configured hooks and filters, PATH-based git resolution, and partial-clone object availability, all
# read by git by design. BUT git may ADDITIONALLY run any git-configured (repo, global,
# system, or command-scope) program during the
# snapshot, whose effects are OUTSIDE this guard's control, so the inert guarantee is BOUNDED, not
# categorical: a clean/process filter runs on `git add --all` (the CHECK-IN / clean direction, NOT smudge -
# smudge would run only on a restore/checkout), an fsmonitor hook runs on the `git status` probe, a
# reference-transaction hook AND a post-index-change hook can run on the temp-index write, and a
# reference-transaction hook runs on `git update-ref`. Because a clean filter can TRANSFORM worktree bytes
# before they enter the snapshot, the snapshot is NOT guaranteed byte-exact on a filtered repo. Further, a
# single overlaid tree cannot capture dirty content inside a SUBMODULE or an untracked EMBEDDED git repo (it
# stores only the gitlink), and it flattens staged-vs-unstaged into ONE tree. One JSONL line is appended to a
# per-user ledger normally OUTSIDE the repo (so a `git clean` inside the repo cannot destroy it); the write
# is BEST-EFFORT: normally one line per snapshot, but skipped when no per-user location resolves (absent
# HOME/XDG), when the path would land inside the repo (a misconfigured XDG_STATE_HOME/HOME), or when the
# directory/open/write fails. FAIL POSTURE: when a snapshot is warranted but cannot be made (probe/write
# error, an undecodable non-UTF-8 path, over the size cap, a bare or broken git dir, or a temp dir resolving
# inside the repo) the recovery layer NEVER lets that become a silent allow of a not-provably-clean discard -
# a would-be ALLOW is DOWNGRADED to ASK, and an already-ASK/DENY decision is left as-is with the failure
# surfaced in its reason. HONEST LIMITATION: the snapshot cannot capture what the probe cannot see (content
# hidden by assume-unchanged/skip-worktree marks or submodule.<name>.ignore), nor ignored files (git add
# --all excludes them), so a discard of that content is not recoverable here.
_SNAPSHOTTABLE_VERBS = frozenset(("checkout", "switch", "restore", "reset", "rm", "clean"))
# The lossy git verbs the form-classifier can reason about (the recognized-verb set). A command the raw scan
# flags as in-scope whose resolved subcommand is outside this set (e.g. 'checkout-index', 'read-tree',
# flagged by a 'checkout'/'reset' substring) cannot be classified, so it must not win the catch-all allow
# (F-97): it ASKS instead. This is a SUPERSET of _SNAPSHOTTABLE_VERBS (it also covers stash/branch, whose
# assets a worktree snapshot cannot capture).
_RECOGNIZED_VERBS = frozenset((
    "checkout", "switch", "restore", "reset", "rm", "clean", "stash", "branch"))
_RECOVERY_REF_NS = "refs/aiqt-recovery"
# Skip (and fail to ASK) rather than snapshot an enormous working tree: a changed+untracked estimate over
# this many bytes is treated as a snapshot failure, so the guard never tries to seal a multi-gigabyte tree.
_RECOVERY_SIZE_CAP = 100 * 1024 * 1024  # 100 MiB
# The per-user ledger path components under the XDG state dir. Normally outside any repo; the write is
# guarded (skipped if the resolved path would land inside the repo).
_RECOVERY_LEDGER_PARTS = ("aiqt-guardrails", "recovery.jsonl")


def _recovery_git(repo, args, env_extra=None, timeout=10):
    """Run a git subcommand against repo and return the CompletedProcess. INERT TO WORKING STATE: across all
    of its git calls it never touches the real index (a snapshot call uses a TEMP GIT_INDEX_FILE via
    env_extra), the worktree, HEAD, or a branch, and in a NORMAL repo writes only objects and one private ref
    (see the block comment above _SNAPSHOTTABLE_VERBS for the repo-config residual). EVERY ambient
    GIT_*-prefixed var is ALWAYS scrubbed via _isolate_git_env (the allowlist posture: no ambient git env is
    trusted, so GIT_DIR/GIT_WORK_TREE/GIT_CONFIG_*/GIT_TRACE*/GIT_ATTR_SOURCE and any future GIT_* go at
    once) so a call meant to observe the REAL repo state (the status probe,
    `rev-parse --show-toplevel`/`--git-path index`) and the snapshot itself cannot be redirected to a
    foreign preset repo (a decoy that would win a false clean, a false recovery point, or leak into the
    snapshot) and cannot be made to WRITE a trace file through an ambient GIT_TRACE path; a snapshot call
    overrides GIT_INDEX_FILE with its own temp index through env_extra, applied AFTER the scrub. This scrub
    is BOUNDED to the ambient ENV: the residual is NOT limited to on-disk config - it spans the non-GIT_
    environment (HOME/XDG_CONFIG_HOME/PATH/TMPDIR), on-disk git configuration AND attributes (repo, global,
    and system), index and ignore state, submodules and embedded repos, configured hooks and filters,
    PATH-based git resolution, and partial-clone object availability, all read by git by design and none
    neutralized here.
    Raises subprocess.SubprocessError / OSError on a spawn or timeout failure, which the caller treats as a
    snapshot failure. offline, bounded by `timeout`."""
    cmd = ["git", "-C", repo] + list(args)
    env = _isolate_git_env(dict(os.environ))  # real-state calls must see the REAL repo, not an ambient decoy
    if env_extra:
        env.update(env_extra)  # a snapshot call sets its own GIT_INDEX_FILE here, applied AFTER the scrub
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def _recovery_toplevel(repo):
    """The absolute repo TOPLEVEL for repo, via `git rev-parse --show-toplevel` (run through _recovery_git,
    so it inherits the discovery-env neutralization and targets the REAL repo). Returns the toplevel path, or
    None when git cannot resolve it (a non-zero return or an empty result: a bare repo, a broken git dir, or
    not a work tree) - the caller treats None as a snapshot failure. This matters because `status --porcelain`
    paths are repo-ROOT-relative while the session cwd can be a SUBDIR, so the size-estimate joins and the
    inside-the-repo containment checks must anchor on the TOPLEVEL, not on the (possibly deeper) cwd."""
    try:
        result = _recovery_git(repo, ["rev-parse", "--show-toplevel"], timeout=5)
    except (subprocess.SubprocessError, OSError):
        return None
    top = result.stdout.strip()
    if result.returncode != 0 or not top:
        return None
    return top


def _recovery_status_entries(repo, top):
    """(classes, total_bytes) from a read-only, config-forced porcelain probe of repo: `classes` is the
    set of covered classes among {'staged','tracked','untracked'} across every changed/untracked entry,
    and `total_bytes` is the summed on-disk size of those paths (a size-cap estimate; a missing path, e.g.
    a deletion, contributes 0). Its paths are repo-ROOT-relative, so a relative one is joined onto `top`
    (the resolved toplevel), NOT the passed repo, which can be a subdir and would under-count the estimate.
    Parses the NUL-delimited `--porcelain -z` form, consuming the extra origin field of a rename/copy record.
    Raises subprocess.SubprocessError / OSError on a probe failure, or UnicodeDecodeError (a ValueError) when
    a non-UTF-8 path cannot be decoded, which the caller turns into a snapshot fail (a graceful ASK)."""
    result = _recovery_git(
        repo, ["-c", "status.showUntrackedFiles=all", "status", "--porcelain",
               "--untracked-files=all", "-z"], env_extra={"GIT_OPTIONAL_LOCKS": "0"}, timeout=5)
    if result.returncode != 0:
        raise subprocess.SubprocessError("status probe returned {}".format(result.returncode))
    classes = set()
    total = 0
    fields = result.stdout.split("\0")
    i, n = 0, len(fields)
    while i < n:
        rec = fields[i]
        i += 1
        if not rec or len(rec) < 3:
            continue
        xy, path = rec[:2], rec[3:]
        # A rename/copy record (X in R/C) carries its ORIGIN path as the next NUL field: consume it.
        if xy and xy[0] in ("R", "C") and i < n:
            i += 1
        if xy == "??":
            classes.add("untracked")
        else:
            if xy[0] not in (" ", "?"):
                classes.add("staged")
            if xy[1] not in (" ", "?"):
                classes.add("tracked")
        try:
            full = path if os.path.isabs(path) else os.path.join(top, path)  # paths are TOPLEVEL-relative
            total += os.path.getsize(full)
        except OSError:
            pass  # a deleted or unreadable path contributes nothing to the size estimate
    return classes, total


def _path_is_within(candidate, parent):
    """True when the realpath of candidate is parent itself or lies under it, tested on whole path
    COMPONENTS (os.path.commonpath) so a sibling like '/repo-x' is not judged inside '/repo', AND a real
    subpath of the filesystem root '/' is correctly judged inside it: the earlier `base + os.sep` test made
    '/' into '//', so every candidate read as OUTSIDE and the guard was silently bypassed (Gemini G1). Used
    to refuse writing recovery data inside the repo it protects (a misconfigured TMPDIR/XDG_STATE_HOME/HOME).
    realpath(strict=False) resolves a not-yet-existing path (the ledger file on its FIRST write) WITHOUT
    raising, so the except fires only on a genuine resolution fault (a symlink loop, or commonpath given
    mixed absolute/relative inputs); on any such error err toward True (unsafe -> refuse) rather than risk
    writing inside the tree."""
    try:
        cand = os.path.realpath(candidate)
        base = os.path.realpath(parent)
        return cand == base or os.path.commonpath([cand, base]) == base
    except (OSError, ValueError):
        return True


def _take_snapshot(repo, top, verb):
    """Take an INERT git-DB snapshot of the working tree (tracked-modified + staged + untracked; ignored
    excluded) via a TEMPORARY GIT_INDEX_FILE normally outside the repo, protected by a private
    refs/aiqt-recovery/<utc-ts>-<pid> ref. `top` is the resolved repo TOPLEVEL (the containment anchor and
    the size-estimate root; the session cwd may be a subdir). In a NORMAL repo writes only git objects and
    that one ref; the real index, worktree, HEAD, and every branch are untouched (see the block comment above
    _SNAPSHOTTABLE_VERBS for the repo-config residual). Returns ('ok', info) with info =
    {ref, sha, classes, restore}, or ('fail', reason)."""
    try:
        classes, total = _recovery_status_entries(repo, top)
    except (subprocess.SubprocessError, OSError) as exc:
        return ("fail", "the working-tree status probe failed ({})".format(exc))
    except UnicodeDecodeError as exc:
        # A non-UTF-8 filename makes `status -z` (text=True) raise a UnicodeDecodeError (a ValueError), which
        # is NOT a SubprocessError/OSError; catch it so it fails the snapshot -> graceful ASK, never an
        # uncaught crash that the dispatcher would turn into an exit-2 HARD BLOCK of a would-ALLOW command.
        return ("fail", "the working-tree status probe returned an undecodable (non-UTF-8) path ({})"
                        .format(exc))
    if total > _RECOVERY_SIZE_CAP:
        return ("fail", "the working tree exceeds the {} MiB recovery snapshot size cap"
                        .format(_RECOVERY_SIZE_CAP // (1024 * 1024)))
    tmpdir = None
    try:
        tmpdir = tempfile.mkdtemp(prefix="aiqt-recovery-")  # normally OUTSIDE the repo, in the system temp dir
        if _path_is_within(tmpdir, top):
            # A TMPDIR misconfigured to resolve inside the repo would seal recovery data where a `git clean`
            # could destroy it; refuse rather than write inside the tree the snapshot protects.
            return ("fail", "the temp snapshot dir resolved inside the repo (TMPDIR misconfigured), so no "
                            "recovery snapshot was written inside the tree it protects")
        tmp_index = os.path.join(tmpdir, "index")
        # Seed the temp index from the REAL index (so staged content is the baseline). git add --all then
        # overlays the worktree (tracked-modified) and untracked files; ignored files are excluded.
        real_index = _recovery_git(
            repo, ["rev-parse", "--path-format=absolute", "--git-path", "index"], timeout=5).stdout.strip()
        if real_index and os.path.exists(real_index):
            shutil.copyfile(real_index, tmp_index)  # else: git creates a fresh temp index on add --all
        env = {"GIT_INDEX_FILE": tmp_index}
        if _recovery_git(repo, ["add", "--all"], env_extra=env, timeout=20).returncode != 0:
            return ("fail", "git add --all into the temp index failed")
        wt = _recovery_git(repo, ["write-tree"], env_extra=env, timeout=10)
        if wt.returncode != 0 or not wt.stdout.strip():
            return ("fail", "git write-tree (temp index) failed")
        tree = wt.stdout.strip()
        head = _recovery_git(repo, ["rev-parse", "--verify", "-q", "HEAD^{commit}"], timeout=5)
        parent = head.stdout.strip() if head.returncode == 0 else ""
        commit_args = ["commit-tree", tree]
        if parent:
            commit_args += ["-p", parent]  # parent HEAD when present; an unborn HEAD makes a rootless snapshot
        commit_args += ["-m", "aiqt-guardrails recovery snapshot before git {}".format(verb)]
        ct = _recovery_git(repo, commit_args, timeout=10)
        if ct.returncode != 0 or not ct.stdout.strip():
            return ("fail", "git commit-tree failed")
        sha = ct.stdout.strip()
        # Microsecond precision plus the pid makes the ref name unique for several snapshots within the same
        # second in the same process; the CREATE-ONLY update-ref below (an empty-string expected-old value)
        # is the backstop, so a name that DID repeat (a clock rollback or a reused pid) FAILS the snapshot
        # rather than silently clobbering a prior recovery point onto the same ref.
        ref = "{}/{}-{}".format(
            _RECOVERY_REF_NS,
            datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"), os.getpid())
        # The trailing "" is the expected OLD value: git makes this a CREATE-ONLY update that fails if the ref
        # already exists, so a colliding name returns non-zero -> ('fail') -> ASK, never an overwrite.
        if _recovery_git(repo, ["update-ref", ref, sha, ""], timeout=5).returncode != 0:
            return ("fail", "git update-ref for the recovery ref failed (a create-only collision or a "
                            "ref-store error), so no recovery point was written over a prior one")
    except (subprocess.SubprocessError, OSError, UnicodeDecodeError) as exc:
        return ("fail", "the snapshot could not be created ({})".format(exc))
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return ("ok", {"ref": ref, "sha": sha, "classes": sorted(classes),
                   "restore": "git checkout {} -- :/".format(ref)})


def _recovery_ledger_path():
    """The per-user ledger path, normally OUTSIDE any repo: $XDG_STATE_HOME/aiqt-guardrails/recovery.jsonl, or
    ~/.local/state/aiqt-guardrails/recovery.jsonl when XDG_STATE_HOME is unset. None when neither
    XDG_STATE_HOME nor HOME resolves (no per-user location to write to). The write itself is guarded in
    _write_recovery_ledger: it is skipped if this path would resolve inside the repo (a misconfigured
    XDG_STATE_HOME/HOME)."""
    base = os.environ.get("XDG_STATE_HOME")
    if not base:
        home = os.environ.get("HOME")
        if not home:
            return None
        base = os.path.join(home, ".local", "state")
    return os.path.join(base, *_RECOVERY_LEDGER_PARTS)


def _write_recovery_ledger(repo, top, verb, info):
    """Append ONE JSONL record for a snapshot to the per-user ledger. Records ONLY: utc timestamp, repo
    path, the triggering git VERB (never the raw command or file contents, for privacy), the recovery ref,
    the snapshot sha, the covered classes, and the exact restore command. Best-effort: ANY error (not only
    an OSError) is swallowed to False (the ref itself is the recovery point; the ledger is a convenience
    trace read later), so a ledger fault never changes the guard's decision. The containment check anchors on
    `top` (the resolved toplevel), so a per-user path landing inside the worktree ABOVE the session cwd is
    still refused. Returns True on a successful write, else False."""
    path = _recovery_ledger_path()
    if not path:
        return False
    if _path_is_within(path, top):
        return False  # a misconfigured XDG_STATE_HOME/HOME pointing inside the repo: skip (best-effort)
    record = {"ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "repo": repo, "verb": verb, "ref": info["ref"], "sha": info["sha"],
              "classes": info["classes"], "restore": info["restore"]}
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        return True
    except Exception:
        return False


def _record_recovery(repo, verb):
    """Take a recovery snapshot and, on success, append the ledger (best-effort). Returns ('ok', info) or
    ('fail', reason). Called ONLY when the worktree is resolvable to the session cwd and the tree is NOT
    provably clean, so a snapshot is genuinely warranted. Resolves the repo TOPLEVEL ONCE (via _recovery_git,
    so it inherits the discovery-env neutralization) and threads it to both the snapshot and the ledger, so
    the size estimate and the inside-the-repo containment checks anchor on the toplevel, not the (possibly
    deeper) session cwd. An unresolvable toplevel (a bare or broken git dir) is itself a snapshot fail. ANY
    snapshot-path fault (a non-UTF-8 repo/toplevel path, an embedded-NUL cwd, or any future snapshot fault)
    is caught and downgraded to a snapshot failure (a graceful fail-to-ASK), so no snapshot-path exception
    can propagate to the dispatcher and crash the guard. The ledger write is OUTSIDE that boundary and
    separately best-effort: it runs only on a successful snapshot and any fault in it is swallowed, so it can
    NEVER downgrade a successful snapshot's ('ok', info) to a failure."""
    try:
        top = _recovery_toplevel(repo)
        if not top:
            return ("fail", "the repository toplevel could not be resolved (a bare or broken git dir)")
        result = _take_snapshot(repo, top, verb)
    except Exception as exc:  # a snapshot-path fault -> graceful fail-to-ASK
        return ("fail", "the recovery snapshot could not be taken ({}: {})".format(
            type(exc).__name__, exc))
    if result[0] == "ok":
        try:
            _write_recovery_ledger(repo, top, verb, result[1])
        except Exception:  # best-effort ledger: a fault here never affects the snapshot outcome
            pass
    return result


def _recovery_pointer(info):
    """A one-line human pointer to a saved snapshot for an ASK/DENY reason. Says a snapshot was SAVED, not
    that work was discarded (PreToolUse cannot know the command ran). Discloses that the primary restore is
    OVERLAY mode (it brings back modified and new content but does NOT re-apply a recorded file deletion),
    and points to the isolated-branch form for an exact, deletion-inclusive restore."""
    covered = ", ".join(info["classes"]) if info["classes"] else "the working tree"
    return ("A pre-command recovery snapshot was saved ({}) at ref {}; restore it with '{}' (overlay mode: "
            "it brings back modified and new content but does NOT re-apply a file deletion recorded in the "
            "snapshot), or for an exact, deletion-inclusive restore put it on an isolated branch with 'git "
            "switch -c aiqt-recover-<id> {}'.".format(
                covered, info["ref"], info["restore"], info["ref"]))


def _ask_with_recovery(kind, detail, snap, optout=None):
    """An ASK whose reason folds in the recovery outcome: a restore pointer on a successful snapshot, or
    the failure surfaced (decision stays ASK) on a snapshot failure. snap is None (no snapshot warranted),
    ('ok', info), or ('fail', reason). `optout` is passed through to _discard_ask_reason to select the
    path-aware opt-out guidance (default pristine; the non-pristine caller passes _OPTOUT_REISSUE, while the
    ambient/view-redirected caller passes _OPTOUT_PRISTINE because a pristine redirected form is opted out by
    prefixing the command as issued)."""
    reason, banner = _discard_ask_reason(kind, detail, optout)
    if snap is not None and snap[0] == "ok":
        reason = reason + " " + _recovery_pointer(snap[1])
    elif snap is not None and snap[0] == "fail":
        reason = reason + (" NOTE: no pre-command recovery snapshot could be created ({}), so this "
                           "discard would not be recoverable by this guard.".format(snap[1]))
    return _ask(reason, banner)


def _deny_with_recovery(kind, snap):
    """A DENY (a confirmed whole-tree clobber on a dirty tree) whose reason folds in the recovery outcome,
    mirroring _ask_with_recovery."""
    code, obj, err = _discard_deny(kind)
    if snap is not None and snap[0] == "ok":
        obj["hookSpecificOutput"]["permissionDecisionReason"] += " " + _recovery_pointer(snap[1])
    elif snap is not None and snap[0] == "fail":
        obj["hookSpecificOutput"]["permissionDecisionReason"] += (
            " NOTE: no pre-command recovery snapshot could be created ({}).".format(snap[1]))
    return (code, obj, err)


def git_discard(data):
    """prsunc (integ/preserve-uncommitted-work), PreToolUse/Bash. ULTRA-CONSERVATIVE "ask unless PRISTINE
    and provably clean" guard (EN-6). For a command that names any recognized lossy git verb (checkout incl
    -B force-create, switch incl -C/--force-create, restore/reset/clean/stash drop-clear/rm/branch force
    delete/move/copy/reset) the outcome is ASK unless the command
    is a PRISTINE SINGLE BARE 'git <verb>' invocation (see _pristine_single_bare_git: no shell metacharacter
    anywhere even quoted, no reserved word, no wrapper/redirect/compound, and the sole command word literally
    'git') AND either its FORM is genuinely non-destructive, or the working tree is PROVABLY CLEAN (the
    config-forced porcelain probe reports no tracked change AND no untracked entry), or the LEADING opt-out is
    set - in which cases ALLOW. A pristine single bare WHOLE-TREE clobber (reset --hard, checkout -f with no
    pathspec, switch --force/--discard-changes) on a probed-DIRTY tree DENIES. EVERYTHING ELSE in scope ASKS:
    any shell structure at all (a metacharacter, wrapper, redirect, reserved word, or second segment), an
    option the form-classifier cannot resolve, a worktree it cannot resolve to the session cwd, an incomplete
    probe, or a softer/index-only discard. No recognized lossy command that would discard WORKING-TREE CONTENT
    is silently ALLOWED unless it is a pristine
    bare git whose FORM is genuinely non-destructive (checkout -b, reset --soft, clean -n, which ALLOW even on
    a dirty tree), or on a provably-clean tree, or a pristine bare form carrying the leading
    GUARDRAIL_ALLOW_DISCARD=1 opt-out - worst case it ASKS; the guarantee is bounded to working-tree content
    (ref-level moves such as reset --soft moving HEAD or a merged-branch delete are reflog-recoverable) and is
    best-effort against the disclosed obfuscation/config residuals. Fail-open ALLOW is reserved for the TRUE boundary
    (a non-Bash or absent tool, a malformed or missing command it cannot read as a discard, a non-git command,
    or no recognized lossy verb). A wrapper is in scope only while the raw scan still
    sees a contiguous git verb keyword, so 'any wrapper ASKS' is NOT categorical: a wrapper that ALSO
    fragments the COMMAND WORD 'git' itself or the verb (env git re'set' --hard / eval git re'set' / command
    g'it' reset --hard, whose raw string carries no contiguous 'git'+'reset') reads as 'no recognized lossy
    verb' and is silently ALLOWED - a DISCLOSED best-effort residual, not chased. A real discard whose VERB is
    outside the recognized set AND unflagged by the raw scan ('git worktree remove -f' of a dirty linked
    worktree, where 'worktree' matches no lossy keyword) is allowed at the true boundary - disclosed, not
    closed this round. BUT a command the raw scan DOES flag whose resolved subcommand is outside the
    recognized set ('git checkout-index -a -f', 'git read-tree -u --reset HEAD') now ASKS (F-97): a flagged
    sub the classifier cannot resolve to a known verb cannot be proven non-destructive, so it never wins the
    catch-all allow. The status probe (git status --porcelain,
    config-forced to report untracked) is read-only and offline.

    SIDE-EFFECTING (EN-6 recovery layer): this handler is NO LONGER pure-decision. Before returning its
    decision for a snapshottable in-scope verb (checkout/switch/restore/reset/rm/clean) whose worktree it can
    resolve to the session cwd AND whose tree is NOT provably clean, it takes an INERT recovery snapshot of
    the uncommitted work (a private refs/aiqt-recovery/<utc-ts>-<pid> ref over a temp-index tree, git objects
    + one ref only) and BEST-EFFORT appends a line to an EXTERNAL per-user ledger (normally one per snapshot;
    skipped when no per-user location resolves, the path would land inside the repo, or the write fails), on
    the ALLOW and ASK paths alike (the hook fires once, with no post-approval callback). It NEVER mutates the real index, worktree, HEAD, or any
    branch. If a warranted snapshot cannot be made, a would-be ALLOW is downgraded to ASK ('no recovery point
    could be created'); an already-ASK/DENY decision is left as-is with the failure surfaced. F-D EXPANSION: a
    NON-PRISTINE in-scope ASK (a compound/wrapped/redirected snapshottable command) is ALSO snapshot-backed
    BEST-EFFORT against the SESSION CWD repo (the redirected dir of a non-pristine command is not parsed, so a
    command that changes into a DIFFERENT repo may be snapshotted at the session repo rather than the target;
    a same-repo cd is still captured by the whole-tree add --all). See the recovery block comment above
    _SNAPSHOTTABLE_VERBS. The snapshot cannot capture what the probe cannot see (assume-unchanged/skip-worktree
    marks, submodule.<name>.ignore) or ignored files (git add --all excludes them), so a discard of that
    content is not recoverable here."""
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
        # Conservative raw scan, not a silent allow. Pass cwd so an unparseable in-scope discard still gets
        # a best-effort recovery snapshot before the ASK (Class C), mirroring the non-pristine path.
        return _git_discard_fallback(command, data.get("cwd"))

    # Precisely-identified lossy git segments (the clean-parse signal). A git command-word segment whose
    # verb-form is not "allow" is a real in-scope lossy form. Used for the in-scope decision on a
    # metacharacter-free, unwrapped command, where the segmentation can be trusted.
    lossy = []  # (role, kind, tokens) for each such segment
    for tokens, _sep in segments:
        if _command_word(tokens) != "git":
            continue
        sub, args = _git_sub_and_args(tokens)
        if sub is None:
            continue
        role, kind = _discard_role(sub, args)
        if role != "allow":
            lossy.append((role, kind, tokens))

    # A wrapper or metacharacter can hide a lossy 'git <verb>' from the command-word segment scan, so the
    # in-scope decision below trusts the raw scan rather than any (unbounded) wrapper enumeration.
    # Trust the raw scan UNCONDITIONALLY (round-3 fix): enumerating wrappers is unbounded (stdbuf/doas/
    # setsid/eval defeated the list), so any raw 'git' + work-losing verb is in scope even when the precise
    # `lossy` list is empty (a wrapped or quoted git verb). A non-lossy git command still routes through the
    # pristine path below and allows. Residual: git renamed out of the string (alias/function) -> recovery layer.
    raw_lossy = _raw_has_lossy_git(command)
    if not lossy and not raw_lossy:
        return _allow()  # boundary: no git + work-losing verb anywhere

    # In scope. Apply the ULTRA-CONSERVATIVE pristine gate: anything that is not a pristine single bare
    # 'git <verb>' invocation ASKS, without ever consulting the probe (a safe over-ask).
    pristine = _pristine_single_bare_git(command, segments)
    if pristine is None:
        kind = lossy[0][1] if lossy else "a git work-losing verb"
        # F-D EXPAND (GD-41, Architect-approved): a non-pristine in-scope command still ASKS, but now gets a
        # BEST-EFFORT recovery point first, so an asked-then-approved compound/wrapped discard is recoverable
        # (the hook fires once, with no post-approval callback). This is BEST-EFFORT against the SESSION CWD
        # repo only: we do NOT parse a non-pristine command's redirected dir, so a command that changes into a
        # DIFFERENT repo may be snapshotted at the session repo rather than the target (a same-repo cd is
        # still captured by the whole-tree add --all). The snapshot is decision-INDEPENDENT (same
        # _record_recovery path); on snapshot fail the decision stays ASK with the failure surfaced (never
        # allow).
        np_subs = set()
        for _role, _kind, _toks in lossy:
            _s, _ = _git_sub_and_args(_toks)
            if _s is not None:
                np_subs.add(_s)
        # This command is ALREADY IN SCOPE (a visible lossy token routed it here), so the snapshot is no
        # longer gated on lexical snappable-detection: shell quoting/eval can hide WHICH snappable verb an
        # in-scope command carries (a `re'set'` fragment assembles `reset` at runtime, so np_subs may miss
        # it), so whenever the base resolves and the tree is NOT provably clean we take the inert best-effort
        # snapshot regardless of which verb is (or is not) visible. A verb obfuscated so thoroughly that it
        # evades even the raw scan (a standalone `eval git re'set'`, whose raw string has no contiguous
        # `reset` for _raw_has_lossy_git to catch) never reaches here at all: it is ALLOWED at the in-scope
        # boundary above, a DISCLOSED best-effort residual (the classifier's documented obfuscation residual),
        # not something this snapshot closes. Over-snapshotting a pure stash/branch non-pristine command is an
        # accepted inert cost (a worktree snapshot cannot capture their asset, but it is never an
        # under-protection). The np_verb label is best-effort from any visible snappable sub.
        np_cwd = data.get("cwd")
        np_base = np_cwd if isinstance(np_cwd, str) and np_cwd else None
        np_snap = None
        if np_base is not None and _tree_is_clean(np_base) is not True:
            np_verb = next(iter(sorted(np_subs & _SNAPSHOTTABLE_VERBS)), "discard")
            np_snap = _record_recovery(np_base, np_verb)
        return _ask_with_recovery(
            kind, "is not a pristine single bare 'git <verb>' invocation (it carries a shell "
                  "metacharacter, wrapper, redirect, reserved word, a second command, or a command word "
                  "that is not literally 'git'), so this guard will not trust a clean probe on it", np_snap,
            _OPTOUT_REISSUE)

    # A pristine single bare git command. Honour a truthy LEADING opt-out on it (an explicit override).
    # This short-circuits BEFORE the recovery layer, so an opt-out discard is NOT snapshot-backed: the
    # operator has explicitly taken responsibility for having saved the work.
    if _segment_has_optout(pristine):
        return _allow()

    # Re-derive the verb form from the sole pristine git command.
    sub, args = _git_sub_and_args(pristine)
    role, kind = _discard_role(sub, args) if sub is not None else ("allow", None)

    # FAIL-SAFE (EN-6, structural completion): the command's repository view cannot be proven to be the
    # session cwd when EITHER cause is present. (1) A NON-COSMETIC ambient GIT_* var: the probe scrubs it,
    # but the ACTUAL command still inherits it and may act on a redirected git dir, work tree, index, or
    # object/ref view. (2) A COMMAND-LOCAL redirect that _segment_dir_simple flags: a -C/--git-dir/--work-
    # tree global option or an inline GIT_DIR=/GIT_WORK_TREE= leading assignment ON the command can point it
    # at a different worktree or config. In EITHER case ANY in-scope pristine form ASKS here - a destructive
    # form OR a genuinely non-destructive allow form (reset --soft, plain switch, clean -n, checkout -b) -
    # BEFORE the role/allow logic below that would otherwise let an allow form through and bypass the fail-
    # safe. Only the explicit leading opt-out (short-circuited above) bypasses it. A best-effort snapshot of
    # the SESSION CWD is still taken on a not-provably-clean snappable tree (the cwd is known even when the
    # target is not): it is inert and provides recovery IF the command acts on the cwd (the common benign
    # non-redirecting case), but may NOT capture a redirected tree.
    if _ambient_repo_view_override() or not _segment_dir_simple(pristine):
        ao_cwd = data.get("cwd")
        ao_base = ao_cwd if isinstance(ao_cwd, str) and ao_cwd else None
        ao_snap = None
        if ao_base is not None and sub in _SNAPSHOTTABLE_VERBS and _tree_is_clean(ao_base) is not True:
            ao_snap = _record_recovery(ao_base, sub)
        return _ask_with_recovery(
            kind or "a git work-losing verb",
            "runs under a non-cosmetic ambient GIT_* variable or carries a command-local redirect "
            "(-C/--git-dir/--work-tree or an inline GIT_DIR=/GIT_WORK_TREE= assignment), so this guard "
            "cannot prove the command's repository view is the session directory (the probe scrubs an "
            "ambient var, but the actual command still inherits it, and a command-local redirect points "
            "elsewhere); any recovery snapshot is best-effort against the session cwd and may not capture "
            "a redirected tree", ao_snap, _OPTOUT_PRISTINE)

    if role == "allow" and any(t.lower().startswith(("alias.", "-calias.")) for t in pristine):
        return _ask(*_discard_ask_reason(
            "a git inline alias ('-c alias.<name>=...')",
            "may expand to a work-losing verb this guard cannot resolve"))

    # Resolve the session worktree ONCE: both the recovery layer and the clean probe need it. A non-cosmetic
    # ambient GIT_* override AND a command-local redirect (a -C/--git-dir/--work-tree/-c global option or a
    # GIT_DIR/GIT_WORK_TREE env assignment ON THE COMMAND, both flagged by _segment_dir_simple) were already
    # handled ABOVE (each ASKS with a best-effort cwd snapshot, which may not capture a redirected tree), so
    # neither reaches here. The _segment_dir_simple guard is kept in `resolvable` as a defensive backstop;
    # when the worktree cannot be resolved to the session cwd, no snapshot is possible and a lossy form ASKS.
    cwd = data.get("cwd")
    base = cwd if isinstance(cwd, str) and cwd else None
    resolvable = base is not None and _segment_dir_simple(pristine)
    snapshottable = sub in _SNAPSHOTTABLE_VERBS

    # THE RECOVERY LAYER. For a subcommand a discard could use to destroy worktree or untracked content
    # (checkout/switch/restore/reset/rm/clean), when the worktree is resolvable and the tree is NOT provably
    # clean, snapshot BEFORE returning ANY decision. Decision-INDEPENDENT (it does not trust the
    # form-classifier), so an asked-then-approved OR a wrongly-allowed (mis-parse) discard still has a
    # recovery point; the hook fires once with no post-approval callback. Skipped on a provably-clean tree
    # (nothing to lose) and for stash/branch (a worktree snapshot cannot capture their asset).
    clean = _tree_is_clean(base) if (resolvable and snapshottable) else None
    snap = None
    if resolvable and snapshottable and clean is not True:  # dirty or probe-uncertain: not provably clean
        snap = _record_recovery(base, sub)

    # F-97 (structural class-fix): the command is IN SCOPE (the raw scan flagged a git work-losing keyword)
    # yet its resolved subcommand is NOT one of the recognized lossy verbs - e.g. 'git checkout-index -a -f'
    # or 'git read-tree -u --reset HEAD', whose 'checkout'/'reset' substring trips the raw scan while
    # _discard_role falls to its catch-all allow. Such a command can discard tracked working-tree content
    # (and its sub is not snapshottable, so no recovery point exists), so it must NOT win the catch-all allow:
    # ASK, since a flagged sub the classifier cannot resolve to a known verb cannot be proven non-destructive.
    # A genuine safe FORM of a RECOGNIZED verb (checkout -b, reset --soft, clean -n) is unaffected: its sub IS
    # recognized, so this never fires for it.
    if raw_lossy and sub is not None and sub not in _RECOGNIZED_VERBS:
        return _ask(*_discard_ask_reason(
            kind or "a git command the raw scan flags as work-losing",
            "resolves to the git subcommand {!r}, which is outside the recognized lossy-verb set "
            "(checkout/switch/restore/reset/rm/clean/stash/branch), so this guard cannot prove it "
            "non-destructive".format(sub)))

    if role == "allow":
        # A genuinely non-destructive bare form (bare no-op, reset --soft, unforced -b, plain switch, clean
        # dry-run, stash pop). FAIL POSTURE: if a snapshot was warranted (a not-provably-clean snapshottable
        # tree, a defensive backstop against a mis-parse) and it FAILED, downgrade the allow to ASK - never
        # silent-allow a not-provably-clean discard with no recovery point. Otherwise ALLOW stands.
        if snap is not None and snap[0] == "fail":
            return _ask(*_discard_ask_reason(
                kind or "a git work-losing verb",
                "would run on a working tree that is not provably clean and no recovery point could be "
                "created ({}), so this guard will not silently allow it".format(snap[1])))
        return _allow()

    if role == "ask":
        # A softer discard (a real clean of untracked files, stash drop/clear, a force branch delete/move/
        # copy/reset): ASK regardless of the tracked-tree probe, which does not see the asset these verbs
        # destroy (a branch ref is a separate, reflog-recoverable asset). A clean may have been
        # snapshotted above (git add --all captures untracked); stash/branch are not snapshottable.
        return _ask_with_recovery(kind, "cannot be proven safe offline", snap)

    # A scoped or clobber form (all snapshottable): gate on the clean probe, which must resolve to the
    # session worktree.
    if not resolvable:
        return _ask(*_discard_ask_reason(
            kind, "targets a working tree this guard cannot resolve to the session directory with "
                  "certainty, so it cannot prove the tree clean"))
    if clean is True:
        return _allow()  # pristine bare lossy verb on a PROVABLY CLEAN tree: nothing to lose, no snapshot
    if clean is None:
        # probe-uncertain: a snapshot was attempted above (snap set); fold its outcome into the ASK reason.
        return _ask_with_recovery(
            kind, "targets a repository whose status probe did not complete, so this guard cannot prove "
                  "the working tree clean", snap)
    # clean is False: the tree holds uncommitted tracked changes or untracked files this verb could reach.
    if role == "clobber":
        return _deny_with_recovery(kind, snap)  # a confirmed whole-tree loss, still recoverable if approved
    return _ask_with_recovery(kind, "may discard uncommitted changes in the working tree", snap)


_PROTECTED = frozenset(("main", "master"))  # the protected line(s); default {main, master}, source-level config

# The safe alternative named in every deny/ask reason, so the actor is never left without a next
# step (mirrors _DISCARD_ALTS): the pack's own rule IS the route.
_PROTECTED_ALTS = (
    "Safe route: push to a feature branch instead ('git switch -c <branch>' then push, or 'git push "
    "<remote> HEAD:refs/heads/<feature>') and change the protected branch only through a reviewed, "
    "verified merge on green; never force-push or commit to it directly.")

# git-push options that CONSUME a following token in their SEPARATED form, so the token loop skips
# their value rather than misreading it as the remote or a refspec. Verified against
# git-scm.com/docs/git-push (SYNOPSIS/OPTIONS) and git-scm.com/docs/gitcli, 2026-08-19, and
# empirically against git 2.53.0: a MANDATORY option value may be attached (--opt=value, -ovalue) or
# separated (--opt value, -o value), and each option listed here is documented only WITH a value,
# hence mandatory, hence separable. --recurse-submodules is RESTORED (F-112 round-3, reverting the
# round-2 1A removal): its value is MANDATORY, so git consumes the NEXT token even separated
# ('git push --recurse-submodules check origin main' consumes 'check' as the value; a bare
# '--recurse-submodules origin main' fails with 'fatal: bad recurse-submodules argument: origin' -
# both observed on git 2.53.0; the negated no-value spelling is the DIFFERENT token
# '--no-recurse-submodules', which is not in this set and consumes nothing). Without the skip the
# value token read as an operand and inflated the refspec region, so a truly refspec-less force
# ('git push -f --recurse-submodules on-demand origin') bypassed the HEAD probe - a silent allow.
# The attached shape carries its value in the same token (the '=' / '-o<value>' test in the loop),
# consuming no separate token, so only the exact bare spelling triggers the skip. NOT listed, on the
# same gitcli(7) rule read the other way: --force-with-lease and --signed take an OPTIONAL value,
# legal only in the "stuck" attached form (--force-with-lease=main), and their bare form consumes
# NOTHING (listing them would skip a real operand); --force-if-includes takes NO value in ANY form.
# None of the three ever consumes a following token. Force DETECTION lives in the token loop, not here.
_PUSH_LONG_ARG_OPTS = frozenset((
    "--push-option", "--repo", "--receive-pack", "--exec", "--recurse-submodules"))
_PUSH_SHORT_ARG_OPTS = frozenset(("o",))  # bare letter: the char-scan tests body chars; '-o <val>' skips its value, attached '-o<val>' does not

# Fallback-only raw-string probes, used when shlex cannot parse the command (unbalanced quote);
# mirrors the _RAW_DIFF_PRODUCER_RE / _RAW_LOSSY_VERB_RE posture: conservative, over-matching, never
# a silent allow. _RAW_PUSH_RE/_RAW_COMMIT_RE pair git with the verb in one pattern ((?is): case-fold,
# '.' spans newlines). _RAW_PUSH_FORCE_RE spots a force or sweep spelling: '--for[a-z-]*' covers
# --for/--force/--force-with-lease/--force-if-includes; the short cluster scan now admits a digit
# ('-[A-Za-z0-9]*f'), so an ipv4/ipv6 '-4f'/'-6f' clustered with force is caught (F-117), and its
# disclosed false-hit on an attached -o value ending in f ('-of') now also covers '-o4f'; the short
# cluster anchor now also admits a preceding QUOTE character (matching the '+refspec' anchor), so a
# quoted wrapped cluster like env git push '-4f' ... is caught (F-117); and the
# '+<ref>' force-refspec anchor admits a preceding
# QUOTE character as well as start/whitespace, so a quoted '+main:main' under a wrapper is caught
# (F-112 round-3) - all accepted over-asks on this path. _RAW_PUSH_DELETE_RE spots a branch-deletion
# spelling (round-3: a delete rewrites the protected line with no force flag and no '+'): a '--de...'
# long flag ('--de' is git-unambiguous for --delete; '--dry-run' shares no such prefix), a
# '--pru...' long flag (--prune deletes remote refs absent locally with no force flag,
# F-117), or a 'd' carried ANYWHERE in a '-' short cluster that now admits digits
# ('-[A-Za-z0-9]*d'), so a clustered ipv4/ipv6 '-4d'/'-6d' is caught (F-117), and like the force
# anchor it too now admits a preceding QUOTE character (so a quoted wrapped '-4d' is caught),
# mirroring the force '-[A-Za-z0-9]*f' shape (round-4: the old
# cluster-END '-[A-Za-z]*d\b' let a wrapped 'git push -dv origin main' slip while the parsed path
# denies it), both name-independent like the force spellings (the true target may be unreadable or
# shell-expanded); or an empty-source ':<protected>' refspec, judged by its visible name and built
# from _PROTECTED (single source, no drift), so an ordinary colon token (a URL, a src:dst refspec)
# does not ask. _RAW_PROTECTED_RE is built from _PROTECTED and folds case: a raw over-match only
# asks/denies, never allows. An embedded quote or backslash-escape INSIDE a flag in the raw string
# (env git push -'f' origin main, \-f, escaped +main) is not matched, because bash quote/escape removal
# cannot be replicated by a raw-string regex; this is the same inherent lexical boundary as a
# shell-expanded $VAR, disclosed and not chased (F-117/F-119).
_RAW_PUSH_RE = re.compile(r"(?is)\bgit\b.*?\bpush\b")
_RAW_PUSH_FORCE_RE = re.compile(r"(?i)--for[a-z-]*|--mirror\b|--all\b|--branches\b|(?:^|[\s'\"])-[A-Za-z0-9]*f|(?:^|[\s'\"])\+\S")
_RAW_PUSH_DELETE_RE = re.compile(
    r"(?i)--de[a-z-]*|--pru[a-z-]*|(?:^|[\s'\"])-[A-Za-z0-9]*d[A-Za-z0-9]*|(?:^|[\s'\"]):(?:refs/heads/|heads/)?(?:"
    + "|".join(sorted(_PROTECTED)) + r")\b")
_RAW_PROTECTED_RE = re.compile(r"(?i)\b(?:" + "|".join(sorted(_PROTECTED)) + r")\b")
_RAW_COMMIT_RE = re.compile(r"(?is)\bgit\b.*?\bcommit\b")

def _head_branch(repo):
    """The branch HEAD is on at `repo`, or None when it cannot be read: a detached HEAD (symbolic-ref
    exits non-zero), an unborn ref, a broken or absent repo, a timeout, or any subprocess error.
    Read-only, offline, 5s timeout, mirroring _tree_is_clean: EVERY ambient GIT_*-prefixed var is
    scrubbed via _isolate_git_env so the probe reads the REAL repo at `-C <repo>` rather than an
    ambient-env decoy (and writes no ambient GIT_TRACE file), and GIT_OPTIONAL_LOCKS=0 keeps the
    read-only posture explicit. None is the fail-safe answer: the caller treats an unreadable HEAD as
    UNKNOWN, never as 'not protected'."""
    try:
        env = _isolate_git_env(dict(os.environ))
        env["GIT_OPTIONAL_LOCKS"] = "0"
        result = subprocess.run(
            ["git", "-C", repo, "symbolic-ref", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, env=env)
        if result.returncode != 0:
            return None  # detached HEAD, not a repository, or an unreadable one
        return result.stdout.strip() or None
    except Exception:
        return None

def _is_protected_ref(name):
    """True when a ref, or the DESTINATION side of a refspec already split by the caller, names a
    protected branch: bare ('main') or carrying the 'refs/heads/' or 'heads/' prefix a push refspec
    may spell out. Comparison is EXACT and case-sensitive, as git resolves ref names ('Main' is a
    different ref); case-folding lives only in the raw fallback. A name in another namespace
    (refs/tags/..., refs/remotes/...) is not a protected-branch destination."""
    if not name:
        return False
    for prefix in ("refs/heads/", "heads/"):
        if name.startswith(prefix):
            return name[len(prefix):] in _PROTECTED
    return name in _PROTECTED

def _push_parse(args):
    """Parse the token list AFTER a 'git push' subcommand (the args of _git_sub_and_args). Returns
    (force, delete, mirror, sweep_all, prune, operands): force is any force spelling (-f/--force, every
    --force-with-lease spelling - a lease-guarded force still rewrites the remote ref - and
    --force-if-includes, with --mirror implying force as git does); delete is the branch-deletion mode
    (--delete or a clustered -d; the empty-source ':<dst>' delete refspec is judged per-operand by the
    caller); mirror and sweep_all are the ref-sweeping modes (--mirror; --all/--branches pushes EVERY
    branch, protected included, so forced it clobbers the protected one without naming it); prune is
    --prune (round-4: it DELETES every remote branch absent locally, with NO force flag - witnessed
    deleting a remote master through a wildcard refspec - and the caller judges it against the refspec
    shape); operands is the remote/refspec token list in command order. The option region is split from
    the operand region at the first '--'/'--end-of-options' boundary via the shipped _split_pre_post, so
    an operand that merely looks like an option ('git push origin -- --force') is never read as one; post
    tokens extend operands AFTER the loop, preserving command order. Flag detection is VALUE-AWARE (F-112
    round-3): force/delete/mirror/sweep/prune are recognized INSIDE the token loop, only on a token that
    is NOT the value of a preceding value-taking option, so '-o --force' and '--push-option --force' read
    as the option VALUE they are, never as a force flag. Each candidate long option is still matched by
    CONSERVATIVE LONG PREFIX (the shipped _has_long_prefix, applied one token at a time), plus the
    short '-f'/'-d' cluster scan, so an abbreviated '--for' or '--d' - even one git itself would reject
    as ambiguous - routes to ASK/DENY, never a silent allow. A value-taking option is likewise
    recognized by prefix (--rep for --repo, --push-opt for --push-option), so its value token is
    skipped rather than misread as the repository or a refspec (F-121). A value-taking option in its SEPARATED
    form (_PUSH_LONG_ARG_OPTS / _PUSH_SHORT_ARG_OPTS) sets skip_value so its value token is skipped,
    never scanned as a flag or an operand; the attached '--opt=value'/'-o<value>' shape carries its
    value in the same token and needs no skip. --force-with-lease and --signed take an OPTIONAL value
    git accepts only ATTACHED (gitcli(7) stuck form), and --force-if-includes takes no value at all, so
    none of the three ever consumes a following token and none is in the skip sets. Negation and no-op
    flags are NOT modelled (round-4, disclosed): '--no-force'/'--no-delete' cancel nothing here,
    '--force-if-includes' alone (a documented no-op without --force-with-lease) still reads as force,
    and --dry-run is judged like the real thing - all safe-direction over-denies; re-issue without the
    contrived flag combination. Also NOT modelled (disclosed, contrived under-block): a
    `--`/`--end-of-options` that is the VALUE of a preceding value-taking option (git push -o
    --end-of-options --force ...) is split as an end-of-options boundary by _split_pre_post
    before the value-aware loop, so a force after it can read as an operand; re-issue without the
    contrived option-value."""
    pre, post, _had_sep = _split_pre_post(args)
    force = delete = mirror = sweep_all = prune = False
    skip_value = False  # the previous token was a separated value-taking option: skip its value
    operands = []       # remote + refspecs in command order; the loop appends, then post extends
    for tok in pre:
        if skip_value:
            skip_value = False
            continue  # this token is a prior option's VALUE, not a flag or an operand
        if tok.startswith("--"):
            if "=" not in tok and any(_has_long_prefix([tok], _n[2:]) for _n in _PUSH_LONG_ARG_OPTS):
                skip_value = True  # a value-taking push option OR an unambiguous abbreviation git accepts
                                   # (e.g. --rep for --repo, --push-opt for --push-option) consumes the
                                   # next token as its value; erring toward skip is safe (an ambiguous
                                   # prefix git itself rejects is moot), and this closes the abbreviated
                                   # value-option silent-allow (F-121)
            elif _has_long_prefix([tok], "mirror"):  # --mirror: a forced sweep of ALL refs
                mirror = True
            elif _has_long_prefix([tok], "all") or _has_long_prefix([tok], "branches"):
                sweep_all = True  # --all/--branches: every branch, protected included
            elif (_has_long_prefix([tok], "force") or _has_long_prefix([tok], "force-with-lease")
                  or _has_long_prefix([tok], "force-if-includes")):
                force = True
            elif _has_long_prefix([tok], "delete"):  # '--d'/'--de'/... err toward delete; '--dry-run'
                delete = True                        # shares no prefix with 'delete', so it never matches
            elif _has_long_prefix([tok], "prune"):   # --prune deletes remote refs absent locally; the
                prune = True                         # caller judges it against the refspec shape
            continue  # other long options carry no judged meaning
        if tok.startswith("-") and len(tok) > 1:
            body = tok[1:]
            for i, ch in enumerate(body):
                if ch in _PUSH_SHORT_ARG_OPTS:  # '-o': the rest of this token (or the next) is its value
                    if i == len(body) - 1:
                        skip_value = True  # a bare '-o': the value is the next token
                    break  # stop the cluster scan; the remainder is the value, not flags
                if ch == "f":
                    force = True
                elif ch == "d":
                    delete = True
            continue
        operands.append(tok)  # a bare operand: the remote or a refspec
    operands += post  # after '--' every token is a refspec (push has no pathspec position)
    return force or mirror, delete, mirror, sweep_all, prune, operands

def _push_protected(tokens, args, cwd):
    """Classify one git push segment against the protected set: ('deny', detail, act_noun), where
    act_noun names the denied act for the banner ('force-push' or 'branch deletion'); ('ask', detail,
    None); or None (the push provably misses the protected branches, or is neither forced nor a
    deletion nor a sweep). The refspec-NAMED paths are judged purely lexically and are
    redirect-INDEPENDENT (a 'git -C <dir> push --force origin main' still denies: the protected NAME
    is what is guarded, wherever the remote lives). Operand ROLES follow the grammar git itself uses
    ('git push <repository> <refspec>...'): the FIRST bare operand is the repository and is never
    judged as a destination (a remote literally named 'main' is not a protected refspec - F-112
    round-3), and only the later, refspec-position operands are judged, by their DESTINATION. The
    exemption is safe against a skip-set omission: git itself reads the first operand as the
    repository, and an unskipped option value can only SHIFT operands right (a spurious extra first
    operand), keeping every real refspec in judged positions - the over-inclusive direction, never a
    hidden refspec. A protected DELETION denies like a force (F-112 round-3): '--delete'/'-d' with a
    protected refspec-position operand, or the empty-source ':<dst>' refspec form. A SWEEP this guard
    cannot prove misses the protected names ASKS (round-4 completes the set): a wildcard force or
    delete destination over the branch namespace, --mirror, a forced --all/--branches, the MATCHING
    refspec ':' (every branch existing on both ends; '+:' is its forced form), which is empty on BOTH
    sides so the per-operand loop has no destination to judge (round-3 skipped it - a silent allow),
    and --prune with a wildcard or matching refspec, which DELETES every remote branch absent locally
    with NO force flag (witnessed deleting a remote master). Only the refspec-less force-push and a
    forced or deleted HEAD/@ consult the HEAD probe, and only when the repository view is provable
    (no non-cosmetic ambient GIT_* var, no command-local redirect, a usable session cwd); otherwise
    it ASKS, never a silent allow of an apparent force or deletion. Git resolves a pushed or FORCED
    HEAD/@ to the current branch; a DELETED HEAD or ':@' git itself REJECTS as a nonexistent or
    invalid remote ref, so the probe-backed deny on the delete side is a harmless safe-direction
    over-deny kept for uniformity (round-4 rewording: round-3 wrongly claimed git resolves a deleted
    HEAD to the current branch)."""
    force, delete, mirror, sweep_all, prune, operands = _push_parse(args)
    refspecs = operands[1:]  # operands[0] is the repository (git's own grammar): never a destination
    # Collect every FORCED and every DELETED destination over the REFSPEC-position operands. Forced:
    # the dst of a '+'-prefixed refspec always, and of every refspec when a force flag is present; the
    # src:dst DESTINATION is what the push overwrites ('feature:main' forces main; 'main:feature'
    # forces only feature). Deleted: every refspec dst when --delete/-d is present, and the dst of an
    # empty-source ':<dst>' refspec (git's push-to-delete form) whatever the flags. The MATCHING
    # refspec ':' (and its forced '+:' form) is empty on BOTH sides: it pushes every branch existing
    # on both ends, protected included, yet names no destination for this loop to judge, so it is
    # flagged as a sweep rather than skipped (round-4; the skip was a silent allow). A wildcard
    # destination over the branch namespace is also tracked UNFORCED, for the --prune judgment.
    forced_dsts = []
    deleted_dsts = []
    matching = False     # a ':' or '+:' matching refspec was seen ('+:' is its forced form)
    wild_branch = False  # some refspec dst wildcards the branch namespace, forced or not
    for op in refspecs:
        plus = op.startswith("+")
        spec = op[1:] if plus else op
        if spec == ":":
            matching = True  # the matching refspec: a sweep whether bare (':') or forced ('+:')
            continue
        if ":" in spec:
            src, dst = spec.split(":", 1)
        else:
            src, dst = None, spec  # a bare refspec pushes to a like-named destination
        if not dst:
            continue  # 'main:' names no destination to judge
        if "*" in dst and (dst.startswith(("refs/heads/", "heads/")) or not dst.startswith("refs/")):
            wild_branch = True
        if plus or force:
            forced_dsts.append(dst)
        if delete or src == "":
            deleted_dsts.append(dst)
    for dst in forced_dsts:
        if _is_protected_ref(dst):
            return ("deny", "force-pushes the protected branch {!r} (a force flag or a '+'-prefixed "
                            "refspec targeting it)".format(dst), "force-push")
    for dst in deleted_dsts:
        if _is_protected_ref(dst):
            return ("deny", "deletes the protected branch {!r} (a --delete/-d flag or an empty-source "
                            "':<dst>' refspec targeting it); a deletion rewrites the protected line as "
                            "surely as a force-push".format(dst), "branch deletion")
    # A sweep the guard cannot prove misses the protected names ASKS rather than denies: a wildcard
    # force or delete destination over the branch namespace (or an unqualified one), the matching
    # refspec, --prune over a wildcard refspec (a deletion needing no force flag), a --mirror push
    # (which force-updates EVERY ref), or a forced --all/--branches.
    for dst in forced_dsts + deleted_dsts:
        if "*" in dst and (dst.startswith(("refs/heads/", "heads/")) or not dst.startswith("refs/")):
            return ("ask", "force-pushes or deletes the wildcard refspec {!r}, a sweep this guard "
                           "cannot prove misses the protected branches".format(dst), None)
    if matching:
        return ("ask", "pushes the matching refspec ':' ('+:' is its forced form), a matching-refspec "
                       "push of every branch existing on both ends, which this guard cannot prove "
                       "misses the protected branches", None)
    if prune and wild_branch:
        return ("ask", "carries --prune with a wildcard refspec, which DELETES every remote branch "
                       "absent locally with no force flag, a sweep this guard cannot prove misses "
                       "the protected branches", None)
    if mirror:
        return ("ask", "is a --mirror push, which force-updates every remote ref including any "
                       "protected branch", None)
    if force and sweep_all:
        return ("ask", "force-pushes --all/--branches, a sweep that includes any protected branch",
                None)
    # HEAD and @ are explicit refspecs. On a push or FORCE git resolves them to the current branch
    # (documented: 'git push <remote> HEAD' pushes the current branch to a like-named remote branch),
    # so a forced HEAD/@ on a protected branch rewrites the protected line even though the literal
    # token is not a protected NAME: treat it like a refspec-less force and resolve via the HEAD
    # probe (F-112 B1). A DELETED HEAD/@ git itself REJECTS ('--delete <remote> HEAD' names a
    # nonexistent remote ref, ':@' an invalid one), so the probe-backed deny on the delete side is a
    # harmless safe-direction over-deny kept for uniformity (round-4 rewording; round-3 wrongly
    # claimed git resolves a deleted HEAD). Computed BEFORE the out-of-scope gate so a '+HEAD' or a
    # deleted HEAD with no global -f is not returned as out-of-scope.
    head_proxy = any(dst in ("HEAD", "@") for dst in forced_dsts + deleted_dsts)
    if not force and not delete and not head_proxy:
        return None  # no force, no delete, no forced/deleted HEAD/@: a plain (or plus-forced
        # non-protected) push is out of scope (a protected NAME was already checked and a '+feature'
        # plus-force to a non-protected branch is allowed)
    if not head_proxy and refspecs:
        return None  # explicit non-HEAD refspecs present, every destination judged above, none protected
    if delete and not force and not head_proxy:
        return None  # a refspec-less --delete: git itself rejects it ("--delete doesn't make sense
        # without any refs"), so there is no implicit current-branch deletion to resolve
    # The forced (or deleted) target is the CURRENT branch (a refspec-less force, or a forced/deleted
    # HEAD/@). Resolve it via the read-only HEAD probe, only when the repository view is provable; the
    # push.default=matching configured-state residual (which could force every matching branch) is
    # disclosed, not modelled.
    act, act_noun = (("deletes", "branch deletion") if delete and not force
                     else ("force-pushes", "force-push"))
    if _ambient_repo_view_override() or not _segment_dir_simple(tokens):
        return ("ask", "{} the current branch (a refspec-less push, or a HEAD/@ refspec) under a "
                       "non-cosmetic ambient GIT_* variable or a command-local redirect, so this guard "
                       "cannot resolve which branch it would target".format(act), None)
    base = cwd if isinstance(cwd, str) and cwd else None
    head = _head_branch(base) if base is not None else None
    if head is None:
        return ("ask", "{} the current branch (a refspec-less push, or a HEAD/@ refspec) but HEAD "
                       "could not be resolved, so this guard cannot prove the target is off the "
                       "protected line".format(act), None)
    if _is_protected_ref(head):
        return ("deny", "{} the current branch (a refspec-less push, or a HEAD/@ refspec) while HEAD "
                        "is the protected branch {!r}, so the apparent target is the protected line "
                        "itself".format(act, head), act_noun)
    return None  # HEAD provably a non-protected branch: the forced or deleted target is off the protected line

def _commit_on_protected(tokens, cwd):
    """The ASK detail when this git commit segment cannot be proven to land off the protected line, else
    None (HEAD provably a non-protected branch). Fail-to-ASK posture throughout: an unprovable repository
    view (a non-cosmetic ambient GIT_* var, or a command-local -C/--git-dir/--work-tree redirect or
    leading env assignment, both via the shared _segment_dir_simple/_ambient_repo_view_override checks),
    a missing session cwd, and an unresolvable HEAD (detached, a non-repository, a probe error) all ASK;
    only a probe that positively names a non-protected branch allows. A 'cd' in an EARLIER segment of the
    same compound command is NOT modelled (the probe reads the session cwd): the covered accidental case
    is the plain add-and-commit chain in the session repo, and asking on every compound commit would
    defeat the guard's own purpose (this is an ASK-level, fully-recoverable surface, so the lighter
    posture than git_discard's compound handling is proportionate)."""
    if _ambient_repo_view_override() or not _segment_dir_simple(tokens):
        return ("runs under a non-cosmetic ambient GIT_* variable or carries a command-local redirect "
                "(-C/--git-dir/--work-tree or a leading env assignment), so this guard cannot prove "
                "which repository's HEAD it would commit on")
    base = cwd if isinstance(cwd, str) and cwd else None
    head = _head_branch(base) if base is not None else None
    if head is None:
        return ("targets a repository whose HEAD this guard could not resolve (no usable session "
                "directory, a detached HEAD, or a failed probe), so it cannot prove the commit lands "
                "off the protected line")
    if _is_protected_ref(head):
        return "would commit directly on the protected branch {!r}".format(head)
    return None


def _protected_line_fallback(command):
    """FAIL-SAFE conservative raw scan for the two cases the parsed path cannot judge: shlex could not
    parse the command (unbalanced quotes), OR git is hidden under a command-word wrapper (env/sudo/...).
    An apparent git force-push (any -f/--force/--for.../--mirror/--all form, or a '+'-refspec anchored
    to start, whitespace, or either quote character, so a quoted '+main:main' under sudo is caught), or
    an apparent branch DELETION (a '--de...' long flag or a '-d' cluster, protected-named or not - like
    the force spellings, the true target may be unreadable or shell-expanded - or an empty-source
    ':<protected>' refspec, judged by its visible name), protected-named or not, ASKS; an apparent git
    commit ASKS; anything else ALLOWS (the true boundary). It ASKS, NEVER a hard DENY (a recoverable
    prompt, matching _git_discard_fallback) and NEVER a silent allow of an apparent force-push or
    deletion. It OVER-MATCHES by design (a keyword in prose or an unrelated '+' or '-d' token asks),
    the documented posture of the sibling fallbacks (_diff_source_fallback, _git_discard_fallback)."""
    if _RAW_PUSH_RE.search(command) and (_RAW_PUSH_FORCE_RE.search(command)
                                         or _RAW_PUSH_DELETE_RE.search(command)):
        named = " a protected branch" if _RAW_PROTECTED_RE.search(command) else " a target this guard cannot read"
        return _ask(
            "AIQT rule prtbrn (protected-branch-integrity): this command could not be fully parsed by the "
            "shell lexer (unbalanced quotes) or hides git under a command-word wrapper, and it appears to "
            "force-push or delete{}; confirm it cannot rewrite the protected line, or re-issue it as a "
            "plain, parseable git command. {}".format(named, _PROTECTED_ALTS),
            "AIQT guardrail: an apparent force-push or branch deletion this guard cannot fully parse - "
            "confirm before proceeding (rule prtbrn, fail-safe).")
    if _RAW_COMMIT_RE.search(command):
        return _ask(
            "AIQT rule artbr1 (branch-and-merge-on-green): the command could not be parsed by the shell "
            "lexer (likely unbalanced quotes) and it appears to run git commit; this guard cannot prove "
            "the commit lands off the protected line, so confirm, or re-issue it as a parseable "
            "command. {}".format(_PROTECTED_ALTS),
            "AIQT guardrail: an unparseable command appears to commit; this guard cannot prove it lands "
            "off the protected branch - confirm before proceeding (rule artbr1, fail-safe).")
    return _allow()

def protected_line(data):
    """prtbrn + artbr1 (integ/protected-branch-integrity, integ/branch-and-merge-on-green),
    PreToolUse/Bash. DENY a git push segment that force-pushes a protected branch - a force spelling
    (--force, --force-with-lease bare or =value, --force-if-includes, a bare or clustered -f, a
    conservative long prefix) or a '+'-prefixed refspec whose DESTINATION names a protected branch -
    or that DELETES one: a --delete/-d flag with a protected refspec-position operand, or the
    empty-source ':<dst>' delete refspec (F-112 round-3); the deny banner names the actual act,
    force-push vs branch deletion (round-4). Destinations are judged only in REFSPEC position (the
    first bare operand is the repository, so a remote literally named 'main' is not a false deny),
    and flag detection is value-aware (a force or delete spelling in an option-value position,
    '-o --force', is not a flag). A refspec-less force-push and a forced or deleted HEAD/@ resolve
    their target through the read-only HEAD probe (deny on a protected HEAD, fail-to-ASK when
    unprovable; the deleted-HEAD deny is a harmless over-deny, git itself rejecting a HEAD delete as
    a nonexistent ref). A sweep this guard cannot prove misses the protected names ASKS: a wildcard
    force or delete refspec over the branch namespace, --mirror, a forced --all/--branches, the
    matching ':'/'+:' refspec, and --prune with a wildcard or matching refspec (round-4: prune
    deletes absent remote branches with no force flag). A force-push or delete to a non-protected
    ref and a plain non-force push ALLOW. ASK a git commit segment while HEAD is a protected branch
    (probed read-only under the ambient-GIT_* scrub; fail-to-ASK when HEAD cannot be resolved): the
    protected line changes only through a reviewed merge, and the direct commit is the accidental
    case this client guard catches - only the literal 'commit' subcommand (a merge, cherry-pick, or
    revert that lands commits on the protected line is out of this accidental-case scope by design);
    server-side branch protection is the real gate. A DENY found in any segment wins over a pending
    ASK (a confirmed rewrite outranks the recoverable prompt, mirroring the git_discard posture). No
    escape hatch prefix: the ASK outcomes are themselves the human gate, and the deny mirrors
    commit_identity's absoluteness."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: protected_line wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("prtbrn")
    if tool_name != "Bash":
        return _allow()  # a present-but-different tool is out of scope (defensive; the matcher governs)
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return _deny(
            "AIQT rule prtbrn (protected-branch-integrity): the Bash payload carried no readable command "
            "string, so the protected-line check could not run; failing closed.",
            "AIQT guardrail: denied a Bash call with no readable command (rule prtbrn, fail-closed).")
    try:
        segments = _segments(command)
    except ValueError:
        return _protected_line_fallback(command)
    cwd = data.get("cwd")
    pending_ask = None  # the first ASK found; a DENY anywhere returns immediately and wins over it
    saw_git = False     # did any parsed segment have 'git' as its command word?
    for tokens, _sep in segments:
        if _command_word(tokens) != "git":
            continue
        saw_git = True
        if _has_info_flag(tokens):
            continue  # a --help/-h segment shows help, it pushes and commits nothing (see diff_source)
        sub, args = _git_sub_and_args(tokens)
        if sub == "push":
            outcome = _push_protected(tokens, args, cwd)
            if outcome is None:
                continue
            decision, detail, act_noun = outcome
            if decision == "deny":
                return _deny(
                    "AIQT rule prtbrn (protected-branch-integrity): this git push {}. The protected "
                    "line is never rewritten or overwritten directly; it changes only through a "
                    "reviewed, verified merge (artbr1). {}".format(detail, _PROTECTED_ALTS),
                    "AIQT guardrail: denied a {} targeting a protected branch (rule prtbrn)."
                    .format(act_noun))
            if pending_ask is None:
                pending_ask = _ask(
                    "AIQT rule prtbrn (protected-branch-integrity): this git push {}. Confirm it "
                    "cannot rewrite the protected line before proceeding. {}"
                    .format(detail, _PROTECTED_ALTS),
                    "AIQT guardrail: a git push this guard cannot prove misses the protected branch - "
                    "confirm before proceeding (rule prtbrn).")
        elif sub == "commit":
            detail = _commit_on_protected(tokens, cwd)
            if detail is not None and pending_ask is None:
                pending_ask = _ask(
                    "AIQT rule artbr1 (branch-and-merge-on-green): this git commit {}. A change "
                    "develops on a feature branch and lands on the protected line only through a "
                    "reviewed merge (prtbrn); server-side branch protection remains the real gate, and "
                    "this prompt covers the accidental direct commit. {}"
                    .format(detail, _PROTECTED_ALTS),
                    "AIQT guardrail: a direct commit on (or unprovably off) the protected branch - "
                    "confirm, or move to a feature branch first (rule artbr1).")
    if pending_ask is not None:
        return pending_ask
    # No parsed segment had 'git' as its command word, yet the raw command names git: a
    # command-word wrapper (env/sudo/command/xargs/timeout/nohup/sh -c) or obfuscation hides the
    # git call. Mirror git_discard's raw posture - an apparent wrapped force-push, deletion, or
    # commit ASKS rather than passing silently; a FRAGMENTED command word or verb is the disclosed
    # residual (F-112 1C), and so is a compound in which ANY OTHER segment - earlier OR later -
    # parses with git as its command word ('git status && sudo git push -f ...', and equally
    # 'env git push -f ... && git status'): the benign git segment satisfies saw_git and suppresses
    # this catch - a round-3 disclosure reworded in round-4 (the suppression was never only-earlier),
    # adversarial and best-effort like git_discard's fragmented-verb residual, not chased.
    if not saw_git and _RAW_GIT_RE.search(command):
        return _protected_line_fallback(command)
    return _allow()


# --- gatdis (EN-5 PR-B): a Bash command that weakens a verification gate -------------------------------

# The git subcommands that accept --no-verify, and the two where the SHORT -n IS --no-verify. Verified
# against the git 2.53.0 man pages (git-commit(1), git-merge(1), git-push(1), git-pull(1), git-rebase(1),
# git-am(1)), 2026-08-19: commit and am bind -n to --no-verify; on push -n is --dry-run and on merge and
# pull it is --no-stat, so the short scan runs ONLY where -n is the bypass, never where it is a harmless
# dry-run or diffstat flag (flagging -n there would block safe commands, the opposite of this control's
# purpose). cherry-pick and revert accept no --no-verify at all and are out of the roster.
_NOVERIFY_VERBS = frozenset(("commit", "merge", "push", "pull", "rebase", "am"))
_NOVERIFY_SHORT_N_VERBS = frozenset(("commit", "am"))

# Value-taking options of the short-n verbs, so an option VALUE is never char-scanned as a clustered
# flag (the '-o' lesson of _push_parse applied to commit -m). Verified against git-commit(1) and
# git-am(1), git 2.53.0, 2026-08-19, on the gitcli(7) rule the push tables use (aiqt_hooks.py, the
# _PUSH_LONG_ARG_OPTS note): a MANDATORY value may be attached or separated, so the cluster scan stops
# at the letter and a bare trailing letter consumes the next token; an OPTIONAL value is legal only in
# the attached "stuck" form, so the scan stops at the letter but a bare one consumes NOTHING; the LONG
# sets are the mandatory-value long options in their SEPARATED form (--message <msg>), whose value
# token could itself begin with '-n' and must be skipped (their '--opt=value' shape carries the value
# in the same token and consumes nothing). These sets cover the COMMON value-taking options rather
# than claiming an exhaustive enumeration: an OMITTED value-taking option is safe-direction (on a
# contrived input whose value itself spells -n or --no-verify it can only cause an over-ask/over-deny,
# never a silent allow), so an option is added only once confirmed value-taking (adding a NON-value-
# taking option would wrongly skip a real operand and could cause a silent allow, so it is never done).
_GATE_SHORT_VALUE_OPTS = {
    "commit": frozenset(("m", "F", "C", "c", "t")),
    "am": frozenset(()),
}
_GATE_SHORT_STUCK_OPTS = {
    "commit": frozenset(("S", "u")),
    "am": frozenset(("S",)),
}
_GATE_LONG_ARG_OPTS = {
    "commit": frozenset((
        "--message", "--file", "--author", "--date", "--template", "--trailer", "--cleanup",
        "--reuse-message", "--reedit-message", "--fixup", "--squash", "--pathspec-from-file")),
    "am": frozenset(("--whitespace", "--exclude", "--include", "--directory", "--quoted-cr",
                     "--resolvemsg")),
}

# The checker lexicon for the ASK heuristics. Three shapes qualify a segment as checker-shaped: a
# known checker COMMAND WORD; a command whose NAME PARTS (basename split on non-alphanumerics, exact
# part match so 'latest' never trips a 'test' substring) contain a checker part; or a known RUNNER
# whose non-option, non-assignment operand qualifies by either test ('make test', 'python -m pytest',
# 'bash tools/run_all_checks.sh'). The lexicon is deliberately a heuristic: it routes to ASK only,
# never a deny, so an over-match costs a prompt and an under-match is the disclosed residue.
_CHECKER_WORDS = frozenset((
    "pytest", "tox", "nox", "unittest", "mypy", "pyright", "ruff", "flake8", "pylint", "bandit",
    "eslint", "tsc", "jest", "vitest", "mocha", "rspec", "rubocop", "phpunit", "phpstan",
    "golangci-lint", "staticcheck", "shellcheck", "hadolint", "yamllint", "markdownlint",
    "gitleaks", "pre-commit", "ctest", "cppcheck", "clang-tidy"))
_CHECKER_RUNNERS = frozenset((
    "make", "npm", "pnpm", "yarn", "npx", "node", "go", "cargo", "mvn", "mvnw", "gradle", "gradlew",
    "python", "python3", "py", "rake", "bundle", "poetry", "pipenv", "uv", "uvx",
    "sh", "bash", "zsh", "dash"))
_CHECKER_NAME_PARTS = frozenset((
    "test", "tests", "selftest", "selftests", "check", "checks", "checker", "lint", "linter",
    "verify", "validate", "audit", "conformance", "vet", "clippy", "gate", "gates"))
_NAME_SPLIT_RE = re.compile(r"[^a-z0-9]+")

# The failure-discarding right-hand sides: an exit-status swallow after '||' (true or the ':' builtin),
# and a truncating stdout sink after '|' that, under default pipeline semantics, replaces the checker
# exit status with its own and can cut the failing tail of the output. tee/cat/less are NOT truncating
# and never qualify.
_EXIT_SWALLOWS = frozenset(("true", ":"))
_TRUNCATING_SINKS = frozenset(("head", "tail"))

# The safe alternative named in every deny/ask reason (mirrors _PROTECTED_ALTS): the rule IS the route.
_GATE_ALTS = (
    "Safe route: run the gate as-is and let its exit status stand; a failing gate is signal, so fix "
    "the artefact it guards. A genuinely broken hook or check is repaired or retired at its source "
    "through a reviewed change, never bypassed for one run.")

# Fallback-only raw-string probes, used when shlex cannot parse the command (unbalanced quote);
# mirrors the _RAW_PUSH_RE posture: conservative, over-matching, never a silent allow. '--no-ver'
# deliberately also hits '--no-verbose' (an over-deny on the unparseable path only); the short cluster
# probe requires a whitespace-preceded single-dash token so a long option ('--amend') never matches.
_RAW_NOVERIFY_VERB_RE = re.compile(r"(?is)\bgit\b.*?\b(?:commit|merge|push|pull|rebase|am)\b")
_RAW_NOVERIFY_RE = re.compile(r"(?i)--no-ver")
_RAW_SHORT_NOVERIFY_VERB_RE = re.compile(r"(?is)\bgit\b.*?\b(?:commit|am)\b")
_RAW_SHORT_N_RE = re.compile(r"(?:^|\s)-[A-Za-z]*n\b")
_RAW_CHECKER_RE = re.compile(
    r"(?i)\b(?:pytest|tox|nox|unittest|mypy|pyright|ruff|flake8|pylint|bandit|eslint|tsc|jest|"
    r"vitest|mocha|rspec|rubocop|phpunit|phpstan|golangci-lint|staticcheck|shellcheck|hadolint|"
    r"yamllint|markdownlint|gitleaks|pre-commit|ctest|cppcheck|clang-tidy|"
    r"tests?|selftests?|checks?|checker|lint\w*|verify|validate|audit|conformance)\b")
_RAW_SWALLOW_RE = re.compile(r"\|\|\s*(?:true|:)(?=\s|$)")
_RAW_TRUNCATE_RE = re.compile(r"\|\s*(?:head|tail)\b")


def _no_verify_spelling(sub, args):
    """The matched hook-bypass spelling on this git segment, or None. The long form: a --no-verify
    token in the option region (before '--'/'--end-of-options', via the shipped _split_pre_post),
    matched exact or by CONSERVATIVE LONG PREFIX (_has_long_prefix, exactly as protected_line matches
    'force'): an ambiguous abbreviation git itself would reject ('--no-ver') matches ANYWAY, and a
    separated option VALUE in pre is scanned too - both err toward a deny, never an allow, while a
    LONGER distinct option (--no-verify-signatures, --no-verbose) never matches because its full name
    is not a prefix of 'no-verify'. The short form: a bare or clustered -n, ONLY on the verbs where -n
    IS --no-verify (commit, am), with the value-taking short and long options skipped (mirroring
    _push_parse) so an attached value ('-m-n ...'), a separated value ('--message -n'), or a stuck
    optional value ('-Skeyid') is never char-scanned as the flag."""
    pre, _post, _had_sep = _split_pre_post(args)
    if _has_long_prefix(pre, "no-verify"):
        return "a --no-verify spelling"
    if sub not in _NOVERIFY_SHORT_N_VERBS:
        return None
    value_opts = _GATE_SHORT_VALUE_OPTS[sub]
    stuck_opts = _GATE_SHORT_STUCK_OPTS[sub]
    long_arg_opts = _GATE_LONG_ARG_OPTS[sub]
    skip_value = False  # the previous token was a separated value-taking option: skip its value
    for tok in pre:
        if skip_value:
            skip_value = False
            continue  # this token is a prior option's VALUE, not a flag
        if tok.startswith("--"):
            if "=" not in tok and tok in long_arg_opts:
                skip_value = True  # its value is the next token
            continue  # long options were judged by _has_long_prefix above, never char-scanned
        if tok.startswith("-") and len(tok) > 1:
            body = tok[1:]
            for i, ch in enumerate(body):
                if ch in value_opts:  # mandatory value: attached remainder, or the next token if bare
                    if i == len(body) - 1:
                        skip_value = True
                    break  # the remainder of this token is the value, not flags
                if ch in stuck_opts:  # optional value is attached-only: bare form consumes NOTHING
                    break
                if ch == "n":
                    return "the short -n (--no-verify) in {!r}".format(tok[:40])
    return None


def _name_parts_hit(name):
    """True when a name, split on non-alphanumerics and lowercased, carries a checker-shaped PART
    (exact part match: 'run_all_checks.sh' hits on 'checks', while 'latest' never hits on a 'test'
    substring)."""
    return any(p in _CHECKER_NAME_PARTS for p in _NAME_SPLIT_RE.split(name.lower()) if p)


def _is_checker_segment(tokens):
    """True when a segment is CHECKER-SHAPED: its command word is a known checker, its command word's
    name parts hit the checker parts, or it is a known runner one of whose non-option, non-assignment
    operands qualifies by either test ('make test', 'go vet', 'python -m pytest', 'npx jest',
    'bash tools/run_all_checks.sh'). Heuristic by design (routes to ASK only): a runner's separated
    option value is scanned as an operand (an accepted over-ask), and a checker hidden inside an
    'sh -c' quoted script body is one opaque token and is missed (the disclosed residue)."""
    word = _command_word(tokens)
    if not word:
        return False
    lw = word.lower()
    if lw in _CHECKER_WORDS or _name_parts_hit(lw):
        return True
    if lw not in _CHECKER_RUNNERS:
        return False
    for tok in tokens[_command_word_index(tokens) + 1:]:
        if tok.startswith("-") or _ENV_ASSIGN_RE.match(tok):
            continue
        if tok.lower() in _CHECKER_WORDS or _name_parts_hit(tok):
            return True
    return False


def _gate_weakening_fallback(command):
    """FAIL-SAFE conservative scan when shlex cannot parse the command (unbalanced quotes): an apparent
    git hook bypass (a no-verify verb plus a --no-ver spelling) DENIES; an apparent git commit/am with
    a short -n cluster ASKS (the raw string cannot bind the cluster to its subcommand, so it cannot
    prove the -n is the bypass rather than an unrelated flag); a checker keyword next to a raw swallow
    or truncating-pipe spelling ASKS; anything else ALLOWS (the true boundary). Over-matching by
    design, the documented posture of the sibling fallbacks (_diff_source_fallback,
    _commit_identity_fallback, _protected_line_fallback): re-issue the command parseable."""
    if _RAW_NOVERIFY_VERB_RE.search(command) and _RAW_NOVERIFY_RE.search(command):
        return _deny(
            "AIQT rule gatdis (gate-discipline): the command could not be parsed by the shell lexer "
            "(likely unbalanced quotes) and it appears to bypass verification hooks with a --no-verify "
            "spelling; failing closed. {}".format(_GATE_ALTS),
            "AIQT guardrail: denied an unparseable command that appears to bypass git verification "
            "hooks (rule gatdis, fail-safe).")
    if _RAW_SHORT_NOVERIFY_VERB_RE.search(command) and _RAW_SHORT_N_RE.search(command):
        return _ask(
            "AIQT rule gatdis (gate-discipline): the command could not be parsed by the shell lexer "
            "(likely unbalanced quotes) and it appears to run git commit or git am with a short -n, "
            "which on those verbs bypasses the verification hooks; confirm it does not, or re-issue "
            "it as a parseable command. {}".format(_GATE_ALTS),
            "AIQT guardrail: an unparseable git commit/am appears to carry -n (--no-verify) - confirm "
            "before proceeding (rule gatdis, fail-safe).")
    if _RAW_CHECKER_RE.search(command) and (
            _RAW_SWALLOW_RE.search(command) or _RAW_TRUNCATE_RE.search(command)):
        return _ask(
            "AIQT rule gatdis (gate-discipline): the command could not be parsed by the shell lexer "
            "(likely unbalanced quotes) and it appears to swallow or truncate a checker's failure "
            "signal ('|| true', '|| :', '| head', '| tail'); confirm the checker's exit status still "
            "gates, or re-issue it as a parseable command. {}".format(_GATE_ALTS),
            "AIQT guardrail: an unparseable command appears to discard a checker's failure signal - "
            "confirm before proceeding (rule gatdis, fail-safe).")
    return _allow()


def gate_weakening(data):
    """gatdis (integ/gate-discipline), PreToolUse/Bash. DENY a git segment that bypasses its
    verification hooks: a --no-verify spelling (exact or conservative long prefix) on a subcommand
    that accepts it (commit, merge, push, pull, rebase, am), or the short -n on the two verbs where
    -n IS --no-verify (commit, am; on push -n is --dry-run and on merge/pull it is --no-stat, so it
    is deliberately not flagged there). ASK a checker-shaped segment whose failure signal is
    discarded: swallowed by an immediately following '|| true' or '|| :', or piped into a truncating
    sink (head, tail) whose exit status replaces the checker's under default pipeline semantics. The
    split posture is deliberate: the hook bypass is lexical and zero-intent, so it DENIES with no
    escape-hatch prefix (mirroring commit_identity's absoluteness); 'what is a gate' is not lexically
    certain, so the heuristics only ASK and a mis-shaped name costs a prompt, never a block. A DENY
    found in any segment wins over a pending ASK (mirrors protected_line)."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: gate_weakening wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("gatdis")
    if tool_name != "Bash":
        return _allow()  # a present-but-different tool is out of scope (defensive; the matcher governs)
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return _deny(
            "AIQT rule gatdis (gate-discipline): the Bash payload carried no readable command string, "
            "so the gate-weakening check could not run; failing closed.",
            "AIQT guardrail: denied a Bash call with no readable command (rule gatdis, fail-closed).")
    try:
        segments = _segments(command)
    except ValueError:
        return _gate_weakening_fallback(command)
    pending_ask = None  # the first ASK found; a DENY anywhere returns immediately and wins over it
    for index, (tokens, sep_after) in enumerate(segments):
        if _command_word(tokens) == "git" and not _has_info_flag(tokens):
            sub, args = _git_sub_and_args(tokens)
            if sub in _NOVERIFY_VERBS:
                spelling = _no_verify_spelling(sub, args)
                if spelling is not None:
                    return _deny(
                        "AIQT rule gatdis (gate-discipline): this git {} carries {}, a deliberate "
                        "bypass of the verification hooks that gate it. Never weaken a gate to obtain "
                        "a pass; fix the artefact instead. {}".format(sub, spelling, _GATE_ALTS),
                        "AIQT guardrail: denied a git verification-hook bypass (--no-verify family) "
                        "(rule gatdis).")
        if not _is_checker_segment(tokens):
            continue
        # The "immediately following" segment, advancing PAST any EMPTY segments (a bare
        # line-continuation/newline inserts a segment with no tokens, so 'pytest ||\n true' and
        # 'pytest |\n head' would otherwise read as having no following swallow/sink and miss the
        # ASK). Only genuinely empty segments are skipped; a real intervening command (a non-empty
        # segment) still breaks adjacency and is NOT skipped.
        nxt_index = index + 1
        while nxt_index < len(segments) and not segments[nxt_index][0]:
            nxt_index += 1
        nxt = segments[nxt_index][0] if nxt_index < len(segments) else []
        if sep_after == "||" and _command_word(nxt) in _EXIT_SWALLOWS:
            if pending_ask is None:
                pending_ask = _ask(
                    "AIQT rule gatdis (gate-discipline): {!r} looks like a verification gate and its "
                    "failure is swallowed by the following '|| {}', so a failing check would read as "
                    "a pass. If it gates this work, run it bare and let the exit status stand; "
                    "confirm only when this command is genuinely not a gate. {}"
                    .format(_command_word(tokens), _command_word(nxt), _GATE_ALTS),
                    "AIQT guardrail: a checker-shaped command has its failure swallowed ('|| true') - "
                    "confirm before proceeding (rule gatdis).")
        elif sep_after == "|" and _command_word(nxt) in _TRUNCATING_SINKS:
            if pending_ask is None:
                pending_ask = _ask(
                    "AIQT rule gatdis (gate-discipline): {!r} looks like a verification gate and is "
                    "piped into '{}', a truncating sink: under default pipeline semantics the "
                    "pipeline reports the sink's exit status, not the checker's, and the truncation "
                    "can also cut the failing output, so the gate's failure signal is discarded. Run "
                    "it bare, or redirect the output to a file and read that. {}"
                    .format(_command_word(tokens), _command_word(nxt), _GATE_ALTS),
                    "AIQT guardrail: a checker-shaped command is piped into a truncating sink "
                    "(| head/tail) - confirm before proceeding (rule gatdis).")
    if pending_ask is not None:
        return pending_ask
    return _allow()


# --- secsec: an obvious hardcoded secret in a Write/Edit/Bash write-form -----------------------------
# A COMPENSATING, shift-left control: it does NOT replace the CI secret-scan and gitleaks gates (they
# remain the real backstop), it moves the same high-signal detection to the moment a secret would be
# written, so an accidental paste is caught before it ever lands on disk. The patterns are SINGLE-SOURCED
# from tools/check_secrets.py, the pack's source of truth: the PREFIX provider-token shapes, the
# credential-named ASSIGN shape, and the PLACEHOLDER non-secret shape are rendered into the GENERATED
# REGION below by tools/gen_secret_patterns.py (drift-gated), so the hook can never fork from the scanner
# and the standalone plugin needs no runtime import of check_secrets (it stays stdlib-only). The decision
# mirrors check_secrets.py EXACTLY, scanning line by line: a provider-prefix match is a hit; a
# credential-named assignment is a hit only when its value is real (an unquoted value must carry both a
# letter and a digit) AND is not a PLACEHOLDER. The secret value is NEVER echoed into a reason; only the
# pattern label is named. Best-effort, targeting the accidental paste/commit, not an adversary: it does
# NOT catch an entropy-only secret with no recognizable shape, a secret written by a tool other than
# Write/Edit/Bash, a secret split across tokens/lines or built by concatenation, the post-shlex
# shell-syntax boundary the other lexical hooks share (a redirect or an embedded '#') on the Bash path,
# or a base64/obfuscated form.
#
# The pattern SOURCE STRINGS below are GENERATED from tools/check_secrets.py by
# tools/gen_secret_patterns.py and are drift-gated; NEVER hand-edit them, and NEVER runtime-import
# check_secrets. Edit tools/check_secrets.py and regenerate (gen_secret_patterns.py, then gen_hooks.py).
# BEGIN generated secret patterns (source: tools/check_secrets.py; regenerate with tools/gen_secret_patterns.py)
_SECSEC_PREFIX_SOURCES = [
    ('\\bgh[pousr]_[A-Za-z0-9]{16,}', 'GitHub token'),
    ('\\bgithub_pat_[A-Za-z0-9_]{20,}', 'GitHub fine-grained PAT'),
    ('\\bsk-[A-Za-z0-9]{20,}', 'OpenAI-style secret key'),
    ('\\bsk-ant-[A-Za-z0-9\\-_]{20,}', 'Anthropic key'),
    ('\\bAKIA[0-9A-Z]{16}\\b', 'AWS access key id'),
    ('\\bxox[baprs]-[A-Za-z0-9-]{10,}', 'Slack token'),
    ('-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----', 'private key block'),
]
_SECSEC_ASSIGN_SOURCE = '(?ix)\n    (?:^|[^A-Za-z0-9])                       # start, or a non-alphanumeric\n    [A-Za-z0-9]*[_-]?                        # optional prefix such as aws_ or my-\n    (passwd|password|secret|token|api[_-]?key|access[_-]?key|\n       client[_-]?secret|auth[_-]?token|private[_-]?key|credential)\n    \\s*[:=]\\s*\n    (?:\n        (?P<q>[\'"])(?P<qvalue>[^\'"\\n]{12,})(?P=q)    # quoted\n      | (?P<value>[A-Za-z0-9+/=_.\\-]{16,})              # or unquoted; charset excludes {$<( so\n                                                     # templates and f-string holes cannot match\n    )\n    '
_SECSEC_PLACEHOLDER_SOURCE = '(?i)^(x{3,}|\\.{3,}|\\*{3,}|<[^>]+>|\\$\\{[^}]+\\}|\\$[A-Z_]+|(your|my|the)[_-]?\\w*|change[_-]?me|placeholder|example|sample|dummy|redacted|fake|test|todo|none|null|n/?a|actual_password_here)$'
# END generated secret patterns
# Compiled at module load from the generated source strings (stdlib re only; no runtime import of
# check_secrets). Recompiling from a pattern's .pattern string preserves its inline flags ((?ix)/(?i)),
# so these behave identically to check_secrets.py's own compiled objects.
_SECSEC_PREFIXES = [(re.compile(_pattern), _label) for _pattern, _label in _SECSEC_PREFIX_SOURCES]
_SECSEC_ASSIGN = re.compile(_SECSEC_ASSIGN_SOURCE)
_SECSEC_PLACEHOLDER = re.compile(_SECSEC_PLACEHOLDER_SOURCE)
# The target field per tool: the text a Write/Edit would write, or the command a Bash call would emit.
_SECSEC_FIELD = {"Write": "content", "Edit": "new_string", "Bash": "command"}
_SECSEC_TOOLS = frozenset(_SECSEC_FIELD)


def _scan_secret(text):
    """Return the pattern label of the first real (non-placeholder) secret in text, or None. Mirrors
    tools/check_secrets.py's own decision EXACTLY, line by line: a provider-prefix match on a line is a
    hit (check_secrets does not placeholder-exclude a prefixed token); a credential-named ASSIGN match is
    a hit only when its value is real - an UNQUOTED value must contain both a letter and a digit (else it
    is likelier ordinary prose or code), and the value must not be a PLACEHOLDER. The matched value is
    never returned, only the label, so a reason can name the shape without echoing the secret."""
    for line in text.splitlines():
        for pattern, label in _SECSEC_PREFIXES:
            if pattern.search(line):
                return label
        match = _SECSEC_ASSIGN.search(line)
        if match:
            value = match.group("qvalue") or match.group("value") or ""
            value = value.strip()
            # An UNQUOTED value must additionally look like a credential (letters AND digits), the same
            # extra bar check_secrets.py applies, because an unquoted match is far likelier to be prose.
            if value and match.group("qvalue") is None:
                if not (any(c.isalpha() for c in value) and any(c.isdigit() for c in value)):
                    value = ""
            if value and not _SECSEC_PLACEHOLDER.match(value):
                return "credential-named variable assigned a literal"
    return None


def secrets_shift_left(data):
    """secsec (security/keep-secrets-out), PreToolUse on Write|Edit|Bash: DENY a call that would write an
    obvious hardcoded secret. Fail-closed like the other PreToolUse controls: a missing tool_name denies,
    and a present tool in scope whose target field is absent or not a string denies (the check cannot read
    what would be written). A present tool NOT in {Write, Edit, Bash} is out of scope and allows. The
    target text is the Write content, the Edit new_string (the text being introduced), or the Bash command
    string (the write-form path, best-effort). A DENY names the pattern label only, never the secret."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: secrets_shift_left wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("secsec")
    if tool_name not in _SECSEC_TOOLS:
        return _allow()  # out of scope (defensive; the matcher governs Write/Edit/Bash)
    field = _SECSEC_FIELD[tool_name]
    target = (data.get("tool_input") or {}).get(field)
    if not isinstance(target, str):
        return _deny(
            "AIQT rule secsec (keep-secrets-out): the {} payload carried no readable {}, so the "
            "secret scan could not run over what would be written; failing closed.".format(tool_name, field),
            "AIQT guardrail: denied a {} call with no readable {} (rule secsec, fail-closed)."
            .format(tool_name, field))
    label = _scan_secret(target)
    if label is None:
        return _allow()
    reason = ("AIQT rule secsec (keep-secrets-out): the text this {} would write contains what looks like "
              "a hardcoded secret ({}). No credential, token, or key is written to a repository or any "
              "persisted location; supply it through the platform's secret store, environment, or auth "
              "flow instead. If it reached a remote, treat it as compromised and rotate it. (This is a "
              "compensating shift-left control; the CI secret-scan and gitleaks gates remain the backstop. "
              "The value is redacted from this message.)".format(tool_name, label))
    return _deny(reason,
                 "AIQT guardrail: denied a {} that would write an apparent hardcoded secret ({}) "
                 "(rule secsec).".format(tool_name, label))


# --- dispatcher ---------------------------------------------------------------------------------------
HANDLERS = {
    "diff_wall_stop": diff_wall_stop,
    "diff_source_pretool": diff_source_pretool,
    "commit_identity": commit_identity,
    "absolute_paths": absolute_paths,
    "git_discard": git_discard,
    "protected_line": protected_line,
    "gate_weakening": gate_weakening,
    "secrets_shift_left": secrets_shift_left,
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
    "protected_line": PRETOOL,
    "gate_weakening": PRETOOL,
    "secrets_shift_left": PRETOOL,
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
