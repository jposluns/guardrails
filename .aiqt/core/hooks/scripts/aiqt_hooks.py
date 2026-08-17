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
"""
import json
import re
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


# --- shell command segmentation ----------------------------------------------------------------------
# Split a Bash command string into segments on the shell separators ; && || | and newlines. This is a
# deliberately SIMPLE lexical split, not a shell parse (per the GD-24 fix brief): it lets each control
# judge one command word at a time, so a bare offending segment chained after an allowed one is still
# caught, and a quoted/echoed occurrence whose command word is not git is skipped by the command-word
# test. Best-effort: it does not defeat deliberate escaping or obfuscation (recorded in the manifest
# residue).
_SEGMENT_SEP_RE = re.compile(r"\|\||&&|[;|\n]")


def _split_segments(command):
    """Segments of a command string, split on ; && || | and newlines. Quoting is NOT honoured: a
    separator inside a quoted string still splits. That can only OVER-split (extra harmless segments);
    it never merges a bare offending segment into an allowed one, so the per-segment DENY stays sound."""
    return _SEGMENT_SEP_RE.split(command)


def _command_word(segment):
    """The command word (first whitespace-delimited token) of a segment, basename only so an absolute
    path to the tool still resolves (/usr/bin/git -> git). '' for an empty segment. An env-assignment
    prefix (FOO=bar) is left as the token, so such a segment's command word is not 'git' and its
    commit/diff subcommand checks do not fire; the any-segment identity-assignment check still covers
    an inline GIT_AUTHOR_NAME=... git commit form."""
    tokens = segment.split()
    if not tokens:
        return ""
    return tokens[0].rsplit("/", 1)[-1]


def _git_subcommand(segment):
    """The git subcommand of a segment whose command word is git: the first non-option token after the
    command word, skipping git global options (-C DIR and -c NAME=VALUE each take an argument). None
    when there is no subcommand token. Token-based (not a regex over the whole segment) so a git name
    quoted inside an argument, e.g. --format='git commit', cannot be mistaken for the subcommand."""
    tokens = segment.split()
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("-"):
            i += 2 if token in ("-C", "-c") else 1
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
    """Return a human-readable description of the diff-wall shape found, or None."""
    if GIT_HEADER_RE.search(text):
        return "a 'diff --git' patch header"
    hunks = len(HUNK_RE.findall(text))
    if hunks >= HUNK_MIN:
        return "{} unified-diff @@ hunk headers".format(hunks)
    lines = text.splitlines()
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
# Segmented (split on ; && || | and newlines), so a bare 'git diff' chained AFTER an allowed form is
# still caught. Per-segment escapes: an explicit summary flag or a redirection of stdout to a file (an
# explicit fd is honoured). The '# allow-diff' token is honoured anywhere in the whole command. An
# informational form (--help / -h) is not a diff dump. A diff piped to a pager still dumps, so a pipe
# is NOT an escape.
LOG_PATCH_RE = re.compile(r"(?:^|\s)(?:-p|-u|--patch)\b")
SUMMARY_RE = re.compile(r"(?:^|\s)--(?:stat|name-only|name-status|numstat|shortstat)\b")
# An output redirection to a file: an optional fd (1 or 2), then '>' or '>>' that is not part of a
# '2>&1'/'>&2' fd-dup (not followed by '&' or '|'), then the start of a filename token. '> /dev/null'
# counts (the diff leaves the console). The optional leading fd is matched (not excluded by a lookbehind
# on a digit), so '1>', '2>', '1>>' are recognized as redirections to a file.
REDIRECT_TO_FILE_RE = re.compile(r"(?<![0-9&>])[12]?>>?\s*(?![&|])\S")
INFO_FLAG_RE = re.compile(r"(?:^|\s)(?:--help|-h)(?:\s|$)")
ALLOW_DIFF_MARKER = "# allow-diff"


def _is_diff_producer(segment):
    """True when a git segment runs a subcommand that renders a diff: diff, show, or log with a patch
    flag. The caller has already confirmed the segment's command word is git. Judging the SUBCOMMAND
    (not a bare 'diff' token) avoids a false positive on a commit message that mentions the word diff."""
    sub = _git_subcommand(segment)
    if sub not in ("diff", "show", "log"):
        return False
    if sub == "log":
        return bool(LOG_PATCH_RE.search(segment))
    return True


def diff_source_pretool(data):
    """cnsdif (trust/no-console-diff-dumps), PreToolUse/Bash: deny a Bash command that dumps a bare
    console diff, allowing the per-segment summary and file-redirection escapes and the whole-command
    '# allow-diff' escape."""
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
    allow_marker = ALLOW_DIFF_MARKER in command  # honoured anywhere in the whole command
    for segment in _split_segments(command):
        if _command_word(segment) != "git":
            continue
        if not _is_diff_producer(segment):
            continue
        if INFO_FLAG_RE.search(segment):
            continue  # 'git diff --help' is informational, not a diff dump
        if allow_marker or SUMMARY_RE.search(segment) or REDIRECT_TO_FILE_RE.search(segment):
            continue
        reason = ("AIQT rule cnsdif (no-console-diff-dumps): this command renders a version-control "
                  "diff to the console, burying the review surface under a raw dump. Use a summary form "
                  "(--stat, --name-only, --name-status, --numstat), redirect the diff to a file, or, if "
                  "a console diff is genuinely intended, append the explicit '# allow-diff' token.")
        return _deny(reason,
                     "AIQT guardrail: denied a bare console diff dump (rule cnsdif).")
    return _allow()


# --- cmtidn: AI identity in a git authoring command --------------------------------------------------
# Segmented: the AI-identity check evaluates the commit-MESSAGE contexts (co-author trailer, --author=)
# only on a segment whose command word is git and whose subcommand is a commit-creating verb, so a
# read-side use of the same tokens (git log --author=Claude) never trips. Separately, an identity
# ASSIGNMENT (a git-identity env var, or user.name/user.email config) is a violation in ANY segment,
# to harden the common 'set the identity then commit' form.
AI_IDENTITY_RE = re.compile(
    r"(?i)\b(claude|anthropic|openai|chatgpt|codex|copilot|gemini|gpt-?[0-9o][a-z0-9.-]*)\b"
    r"|@anthropic\.com|@openai\.com")
_VALUE = r"(\"[^\"]*\"|'[^']*'|[^\s\"';|&]+)"
COMMIT_VERBS = ("commit", "merge", "cherry-pick", "am", "rebase", "revert", "commit-tree")
# Commit-MESSAGE identity contexts: judged only on a git commit segment.
COMMIT_CONTEXTS = (
    # A co-author trailer's value runs to the end of the segment or the enclosing shell quote.
    (re.compile(r"(?i)co[- ]?authored[- ]?by\s*:?\s*([^\n\"']{1,160})"), "co-author trailer"),
    (re.compile(r"--author[= ]\s*" + _VALUE), "--author value"),
)
# Identity ASSIGNMENT contexts: judged on EVERY segment (env var / git config), so setting an AI
# identity in a separate segment before the commit is caught too.
IDENTITY_ASSIGN_CONTEXTS = (
    (re.compile(r"(?i)\bGIT_(?:AUTHOR|COMMITTER)_(?:NAME|EMAIL)\s*=\s*" + _VALUE), "git identity variable"),
    (re.compile(r"(?i)\buser\.(?:name|email)\s*[= ]\s*" + _VALUE), "user.name/user.email value"),
)


def _match_ai(contexts, segment):
    """Return '<label> <value>' for the first context in contexts whose value names an AI identity in
    this segment, else None."""
    for regex, label in contexts:
        for match in regex.finditer(segment):
            value = match.group(1)
            if AI_IDENTITY_RE.search(value):
                return "{} {!r}".format(label, value.strip()[:80])
    return None


def find_ai_authorship(command):
    """Return '<context> <value>' when a segment sets an AI identity for a recorded change, else None:
    an identity-assignment (env var / git config) in ANY segment, or a commit-message context (co-author
    trailer, --author=) on a git commit segment."""
    for segment in _split_segments(command):
        hit = _match_ai(IDENTITY_ASSIGN_CONTEXTS, segment)
        if hit is not None:
            return hit
        if _command_word(segment) == "git" and _git_subcommand(segment) in COMMIT_VERBS:
            hit = _match_ai(COMMIT_CONTEXTS, segment)
            if hit is not None:
                return hit
    return None


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
    hit = find_ai_authorship(command)
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


def main(argv):
    if len(argv) != 1 or argv[0] not in HANDLERS:
        print("aiqt_hooks: usage: aiqt_hooks.py <{}>".format("|".join(sorted(HANDLERS))),
              file=sys.stderr)
        return 2
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            raise ValueError("payload is not a JSON object")
    except (ValueError, UnicodeDecodeError, OSError) as exc:
        # Fail CLOSED: a hook that cannot read its payload cannot clear the action, so it blocks. exit 2
        # is the platform's blocking path; the diagnostic reaches Claude on stderr.
        print("aiqt_hooks: unreadable hook payload ({}); failing closed".format(exc), file=sys.stderr)
        return 2
    try:
        code, stdout_obj, stderr_text = HANDLERS[argv[0]](data)
    except Exception as exc:  # a handler crash is an unreadable result: fail closed (block), not pass
        print("aiqt_hooks: handler {} failed ({}); failing closed".format(argv[0], exc), file=sys.stderr)
        return 2
    if stdout_obj is not None:
        print(json.dumps(stdout_obj))
    if stderr_text:
        print(stderr_text, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
