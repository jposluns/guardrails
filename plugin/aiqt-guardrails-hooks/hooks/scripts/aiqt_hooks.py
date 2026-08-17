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
_PAGERS = frozenset(("less", "more", "most"))  # a diff piped to one of these is interactive review


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
# AFTER an allowed form, or grouped in a subshell '( git diff )', is still caught. Per-segment escapes:
# an explicit summary flag or a redirection of stdout to a file. The '# allow-diff' token is honoured
# ONLY as a genuine UNQUOTED trailing shell comment (not the literal buried in a quoted argument). An
# informational form (--help / -h) is not a diff dump. A diff piped to a KNOWN PAGER (less/more/most) is
# legitimate interactive review and is allowed; a pipe to cat/tee/console still dumps and denies.
_PATCH_FLAGS = frozenset(("-p", "-u"))
_SUMMARY_FLAGS = frozenset(("--stat", "--name-only", "--name-status", "--numstat", "--shortstat"))
_INFO_FLAGS = frozenset(("--help", "-h"))
_REDIRECT_TOKENS = frozenset((">", ">>"))  # stdout to a file; a grouped '>&'/'>|' fd-dup is not here
ALLOW_DIFF_MARKER = "# allow-diff"


def _find_unquoted_comment(line):
    """The trailing shell-comment text of one line (everything after the '#'), or None when the line has
    no genuine comment. A '#' begins a comment ONLY when it is unquoted and at a word boundary (the start
    of the line or preceded by whitespace); a '#' inside a single- or double-quoted string, or joined to
    a word (foo#bar), is not a comment. Quote and backslash state are tracked so a '#' buried in a quoted
    argument is never mistaken for a comment."""
    quote = None       # the open quote char (' or ") or None outside a quote
    escaped = False     # the previous char was an unquoted backslash
    prev_ws = True      # start of line counts as a word boundary
    for idx, ch in enumerate(line):
        if escaped:
            escaped = False
            prev_ws = False
            continue
        if quote:
            if ch == quote:
                quote = None
            prev_ws = False
            continue
        if ch == "\\":
            escaped = True
            prev_ws = False
            continue
        if ch in ("'", '"'):
            quote = ch
            prev_ws = False
            continue
        if ch == "#" and prev_ws:
            return line[idx + 1:]
        prev_ws = ch.isspace()
    return None


def _has_allow_diff_comment(command):
    """True when the command carries the '# allow-diff' escape as a GENUINE unquoted trailing shell
    comment, not as the literal string buried inside a quoted argument (GD-24 fix round 4: the old raw
    substring match wrongly honoured a '# allow-diff' inside a quoted commit message). Each line is
    scanned for an unquoted comment; the marker is honoured only when it opens that comment."""
    for line in command.split("\n"):
        comment = _find_unquoted_comment(line)
        if comment is not None and ALLOW_DIFF_MARKER in ("#" + comment):
            return True
    return False
# Fallback-only regexes over the RAW command string, used when shlex cannot parse the command (see
# _diff_source_fallback). SUMMARY/INFO/REDIRECT mirror the token escapes; the producer regex is a loose
# 'git ... diff|show|range-diff' probe. Conservative on a parse failure: deny a plausible producer.
SUMMARY_RE = re.compile(r"(?:^|\s)--(?:stat|name-only|name-status|numstat|shortstat)\b")
INFO_FLAG_RE = re.compile(r"(?:^|\s)(?:--help|-h)(?:\s|$)")
REDIRECT_TO_FILE_RE = re.compile(r"(?<![0-9&>])[12]?>>?\s*(?![&|])\S")
_RAW_DIFF_PRODUCER_RE = re.compile(r"(?is)\bgit\b.*?\b(?:diff|show|range-diff)\b")


def _has_patch_flag(tokens):
    """True when a segment carries a patch flag (-p, -u, or a --patch* form), the flag that turns a
    listing/plumbing/stash producer into a console patch."""
    return any(t in _PATCH_FLAGS or t.startswith("--patch") for t in tokens)


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


def _diff_source_fallback(command, allow_marker):
    """FAIL-SAFE conservative check when shlex cannot parse the command (unbalanced quotes): we cannot
    segment safely, so scan the RAW string. If it invokes a git diff-producer with no summary, --help,
    or redirect escape (and no '# allow-diff'), deny; never silent-allow a plausibly-violating command
    on a parse failure. Documented as best-effort: a genuinely clean but unparseable command may deny."""
    if allow_marker:
        return _allow()
    if _RAW_DIFF_PRODUCER_RE.search(command) and not (
            SUMMARY_RE.search(command) or INFO_FLAG_RE.search(command)
            or REDIRECT_TO_FILE_RE.search(command)):
        return _deny(
            "AIQT rule cnsdif (no-console-diff-dumps): the command could not be parsed by the shell "
            "lexer (likely unbalanced quotes) and it appears to render a version-control diff to the "
            "console with no summary/redirect/'# allow-diff' escape; failing closed. Re-issue with a "
            "summary form, a redirect to a file, or the explicit '# allow-diff' token.",
            "AIQT guardrail: denied an unparseable command that appears to dump a console diff (rule "
            "cnsdif, fail-safe).")
    return _allow()


def diff_source_pretool(data):
    """cnsdif (trust/no-console-diff-dumps), PreToolUse/Bash: deny a Bash command that dumps a bare
    console diff, allowing the per-segment summary and file-redirection escapes, the pager-pipe escape,
    and the genuine unquoted trailing '# allow-diff' comment escape."""
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
    allow_marker = _has_allow_diff_comment(command)  # only a genuine unquoted trailing '# allow-diff'
    try:
        segments = _segments(command)
    except ValueError:
        return _diff_source_fallback(command, allow_marker)
    for idx, (tokens, sep_after) in enumerate(segments):
        if _command_word(tokens) != "git":
            continue
        if not _is_diff_producer(tokens):
            continue
        if any(t in _INFO_FLAGS for t in tokens):
            continue  # 'git diff --help' is informational, not a diff dump
        if allow_marker:
            continue
        if any(t.split("=", 1)[0] in _SUMMARY_FLAGS for t in tokens):
            continue
        if any(t in _REDIRECT_TOKENS for t in tokens):
            continue
        # A diff piped to a known pager is interactive review, not a response wall (FIX 4).
        if sep_after == "|" and idx + 1 < len(segments) \
                and _command_word(segments[idx + 1][0]) in _PAGERS:
            continue
        reason = ("AIQT rule cnsdif (no-console-diff-dumps): this command renders a version-control "
                  "diff to the console, burying the review surface under a raw dump. Use a summary form "
                  "(--stat, --name-only, --name-status, --numstat), redirect the diff to a file, pipe it "
                  "to a pager (less/more/most), or, if a console diff is genuinely intended, append the "
                  "explicit '# allow-diff' token.")
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


# --- dispatcher ---------------------------------------------------------------------------------------
HANDLERS = {
    "diff_wall_stop": diff_wall_stop,
    "diff_source_pretool": diff_source_pretool,
    "commit_identity": commit_identity,
    "absolute_paths": absolute_paths,
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
