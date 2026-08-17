#!/usr/bin/env python3
"""AIQT Guardrails enforcement hooks for Claude Code. Stdlib only, offline.

SOURCE tree copy: this file lives at .aiqt/core/hooks/scripts/aiqt_hooks.py and is copied
byte-identical into the generated plugin surface plugin/aiqt-guardrails-hooks/hooks/scripts/
aiqt_hooks.py by tools/gen_hooks.py; edit the source, never the generated copy. One dispatcher, one
handler function per control declared in .aiqt/core/hooks/manifest.toml:

  diff_wall_stop      Stop        cnsdif  block a unified-diff wall in the final assistant message
  diff_source_pretool PreToolUse  cnsdif  deny a Bash command that dumps a bare console diff
  commit_identity     PreToolUse  cmtidn  deny a git authoring command that names an AI identity
  absolute_paths      PreToolUse  abspth  deny a relative path where the tool requires absolute

Contract (doc-confirmed 2026-08-17 against code.claude.com/docs/en/hooks): the hook payload arrives
as JSON on stdin. A PreToolUse handler that decides emits, on exit 0,
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"|"deny",
"permissionDecisionReason": "..."}}; an allow decision is expressed as NO output (exit 0 silent), so
the user's own permission flow is never bypassed, and a deny decision blocks the tool. exit 2 is a
blocking error whose stderr is fed back to Claude; the Stop diff-wall block uses it. The Stop payload
carries the final assistant text as last_assistant_message.

Error posture: FAIL CLOSED. A control that cannot read the input it is meant to cover, or that is
invoked in a context it does not understand, BLOCKS rather than waving the action through (per
integ-check-fails-closed-on-unreadable): a PreToolUse handler emits a deny with a failing-closed
reason, a Stop handler exits 2, and an unreadable top-level payload or a handler crash exits 2. A
detected violation blocks the same way. A clean pass emits NO decision and exits 0 silently. The Stop
loop guard (stop_hook_active) bounds the block so a fail-closed Stop can never wedge a turn chain.
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


def _stop_block(feedback, banner):
    """A Stop block: exit 2 with the reason on stderr (fed back to Claude), plus a systemMessage banner
    on stdout for the operator (honoured on exit 0 paths; harmless if the platform ignores it here)."""
    return (2, {"systemMessage": banner}, feedback)


def _hard_block(message):
    """A fail-closed hard block where no structured decision can be formed (a mis-wired event): exit 2
    with the diagnostic on stderr, so a broken guard blocks rather than silently passing."""
    return (2, None, message)


# --- cnsdif (Stop): the diff-wall shape --------------------------------------------------------------
# Thresholds are deliberately permissive toward small illustrative excerpts: the rule forbids burying
# the review surface under a raw dump, not quoting three lines of a patch. Each detector is lexical.
GIT_HEADER_RE = re.compile(r"^diff --git ", re.M)
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", re.M)
HUNK_MIN = 2        # one quoted hunk header can be illustrative; two is a pasted patch
FENCE_MIN = 10      # a ```diff fence with this many content lines is a dump, not an excerpt
RUN_MIN = 8         # consecutive lines starting with + or - ...
SIGN_MIN = 3        # ... containing at least this many of EACH sign (a bullet list is all "-")


def _diff_fence_lines(lines):
    """The largest content line count inside a ```diff / ```patch / ```udiff fenced block."""
    best = 0
    inside = False
    count = 0
    for line in lines:
        stripped = line.strip()
        if inside:
            if stripped.startswith("```"):
                inside = False
                best = max(best, count)
            else:
                count += 1
        elif stripped.startswith("```"):
            if stripped[3:].strip().lower() in ("diff", "patch", "udiff"):
                inside = True
                count = 0
    if inside:  # an unterminated fence still counts
        best = max(best, count)
    return best


def _plus_minus_run(lines):
    """The longest run of consecutive +/- lines that mixes both signs (>= SIGN_MIN each), which is
    the diff-body shape; an all-minus run is a Markdown bullet list and never trips this."""
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
    """cnsdif (trust/no-console-diff-dumps), Stop: block a final response that is a raw diff wall."""
    if data.get("hook_event_name") not in STOP_EVENTS:
        return _hard_block("aiqt_hooks: diff_wall_stop wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    # Loop guard: when the platform re-runs the Stop hook on the continuation it forced, do not block
    # again, so a hook cycle stays bounded (one enforcement pass per turn chain). Residue: a repeat
    # offence in that immediate continuation passes; the next fresh turn is scanned again.
    if data.get("stop_hook_active"):
        return _allow()
    message = data.get("last_assistant_message")
    if not isinstance(message, str):
        # Fail closed: the control is meant to cover the final assistant text and cannot read it. The
        # loop guard above bounds this so a fresh continuation is not blocked twice.
        return _stop_block(
            "AIQT rule cnsdif (no-console-diff-dumps): the Stop payload carried no last_assistant_"
            "message text, so the diff-wall check could not run; failing closed. Answer again so the "
            "check can inspect the final response.",
            "AIQT guardrail: blocked a Stop with no readable assistant text (rule cnsdif, fail-closed).")
    found = detect_diff_wall(message)
    if found is None:
        return _allow()
    feedback = ("AIQT rule cnsdif (no-console-diff-dumps): the final response contains {}. Do not "
                "bury the review surface under a raw diff dump: report the change as a concise "
                "summary and surface the full detail through a file, an artefact, or the client's "
                "own diff view, then answer again without the raw dump.".format(found))
    return _stop_block(
        feedback, "AIQT guardrail: blocked a raw diff wall in the response (rule cnsdif).")


# --- cnsdif (PreToolUse): a bare console diff at the source -------------------------------------------
# Layer A of the F-36 catch: deny a Bash command that renders a version-control diff to the console.
# Escape hatches (SYNTHESIS section 6): an explicit summary form, redirection of the diff to a file,
# or an explicit '# allow-diff' token. A bare console diff (or one piped to a pager/head) is blocked.
GIT_DIFF_SUBCMD_RE = re.compile(r"\bgit\b(?:\s+-\S+(?:\s+\S+)?)*\s+(diff|show|log)\b")
LOG_PATCH_RE = re.compile(r"(?:^|\s)(?:-p|-u|--patch)\b")
SUMMARY_RE = re.compile(r"(?:^|\s)--(?:stat|name-only|name-status|numstat|shortstat)\b")
# An output redirection to a file: a '>' or '>>' not part of a '2>&1'/'>&2' fd-dup (not preceded by a
# digit/&/>, not followed by '&' or '|'), then the start of a filename token. '> /dev/null' counts (the
# diff leaves the console). A pipe is NOT an escape: a diff piped to a pager still dumps to the console.
REDIRECT_TO_FILE_RE = re.compile(r"(?<![0-9&>])>>?\s*(?![&|])\S")
ALLOW_DIFF_MARKER = "# allow-diff"


def _is_diff_producer(command):
    """True when the command runs a git subcommand that renders a diff: diff, show, or log with a
    patch flag. Matching the SUBCOMMAND (not a bare 'diff' token) avoids a false positive on a commit
    message that merely mentions the word diff."""
    match = GIT_DIFF_SUBCMD_RE.search(command)
    if not match:
        return False
    if match.group(1) == "log":
        return bool(LOG_PATCH_RE.search(command))
    return True


def diff_source_pretool(data):
    """cnsdif (trust/no-console-diff-dumps), PreToolUse/Bash: deny a Bash command that dumps a bare
    console diff, allowing the explicit summary, file-redirection, and '# allow-diff' escapes."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: diff_source_pretool wired to unexpected event {!r}; failing "
                           "closed".format(data.get("hook_event_name")))
    if data.get("tool_name") != "Bash":
        return _allow()  # the matcher governs targeting; a non-Bash call is out of scope
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return _deny(
            "AIQT rule cnsdif (no-console-diff-dumps): the Bash payload carried no readable command "
            "string, so the diff-source check could not run; failing closed.",
            "AIQT guardrail: denied a Bash call with no readable command (rule cnsdif, fail-closed).")
    if not _is_diff_producer(command):
        return _allow()
    if ALLOW_DIFF_MARKER in command or SUMMARY_RE.search(command) or REDIRECT_TO_FILE_RE.search(command):
        return _allow()
    reason = ("AIQT rule cnsdif (no-console-diff-dumps): this command renders a version-control diff "
              "to the console, burying the review surface under a raw dump. Use a summary form "
              "(--stat, --name-only, --name-status), redirect the diff to a file, or, if a console "
              "diff is genuinely intended, append the explicit '# allow-diff' token.")
    return _deny(reason,
                 "AIQT guardrail: denied a bare console diff dump (rule cnsdif).")


# --- cmtidn: AI identity in a git authoring command --------------------------------------------------
# Gate first on a git AUTHORING invocation, so read-side uses of the same tokens (git log --author=...)
# are never inspected; then extract only the authorship-context VALUES and test those for an AI
# identity, so an AI name elsewhere in a commit message never trips the control.
GIT_AUTHORING_RE = re.compile(r"\bgit\b[\s\S]*\b(commit|merge|cherry-pick|am|rebase|revert|commit-tree)\b")
AI_IDENTITY_RE = re.compile(
    r"(?i)\b(claude|anthropic|openai|chatgpt|codex|copilot|gemini|gpt-?[0-9o][a-z0-9.-]*)\b"
    r"|@anthropic\.com|@openai\.com")
_VALUE = r"(\"[^\"]*\"|'[^']*'|[^\s\"';|&]+)"
AUTHORSHIP_CONTEXTS = (
    # A co-author trailer's value runs to the end of its line or the enclosing shell quote.
    (re.compile(r"(?i)co[- ]?authored[- ]?by\s*:?\s*([^\n\"']{1,160})"), "co-author trailer"),
    (re.compile(r"--author[= ]\s*" + _VALUE), "--author value"),
    (re.compile(r"(?i)\bGIT_(?:AUTHOR|COMMITTER)_(?:NAME|EMAIL)\s*=\s*" + _VALUE), "git identity variable"),
    (re.compile(r"(?i)\buser\.(?:name|email)\s*[= ]\s*" + _VALUE), "user.name/user.email value"),
)


def find_ai_authorship(command):
    """Return '<context> <value>' when a git authoring command sets an AI identity, else None."""
    if not GIT_AUTHORING_RE.search(command):
        return None
    for regex, label in AUTHORSHIP_CONTEXTS:
        for match in regex.finditer(command):
            value = match.group(1)
            if AI_IDENTITY_RE.search(value):
                return "{} {!r}".format(label, value.strip()[:80])
    return None


def commit_identity(data):
    """cmtidn (integ/commit-identity), PreToolUse/Bash: deny a git command that records an AI as
    author, committer, or co-author. No escape hatch: the rule is absolute."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: commit_identity wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    if data.get("tool_name") != "Bash":
        return _allow()  # matcher governs targeting; a non-Bash call is out of scope
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
DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _is_absolute(path):
    """POSIX absolute, Windows drive-absolute, or UNC. A ~-prefixed path is NOT absolute: these tools
    do not expand it, so it would resolve relative to a literal ~ directory."""
    return path.startswith("/") or path.startswith("\\\\") or bool(DRIVE_RE.match(path))


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
    return _allow()  # matcher governs targeting; any other tool is out of scope


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
