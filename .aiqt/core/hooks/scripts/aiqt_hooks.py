#!/usr/bin/env python3
"""AIQT Guardrails enforcement hooks for Claude Code. Stdlib only, offline.

SOURCE tree copy: this file lives at .aiqt/core/hooks/scripts/aiqt_hooks.py and is copied
byte-identical into the generated plugin surface plugin/aiqt-guardrails-hooks/hooks/scripts/
aiqt_hooks.py by tools/gen_hooks.py; edit the source, never the generated copy. One dispatcher, one
handler function per control declared in .aiqt/core/hooks/manifest.toml:

  diff_wall_stop      Stop        cnsdif  surface (WARN) a unified-diff wall in the final assistant message
  diff_source_pretool PreToolUse  cnsdif  deny a Bash command that dumps a bare console diff
  commit_identity     PreToolUse  cmtidn  deny a git authoring command that names an AI identity
  absolute_paths      PreToolUse  abspth  deny a relative path where a typed-path tool requires absolute
  bash_absolute_paths PreToolUse  abspth  allow+note a relative cd/pushd operand or redirect target in Bash; deny a truncating redirect to a relative/opaque target
  git_explicit_binding PreToolUse expbnd allow+note an ambient git target or broad scope before relocation/publish
  git_discard         PreToolUse  prsunc  allow / snapshot-then-allow / deny a git command that discards work
  branch_root         PreToolUse  brnrot  deny branch creation from an orphaned or unprovable start point
  gate_weakening      PreToolUse  gatdis  deny a git hook bypass; deny a swallowed or truncated checker
  commit_msg_subst    PreToolUse  sectvl  deny shell substitution in a git commit argument
  secrets_shift_left  PreToolUse  secsec  deny a Write/Edit/MultiEdit/Bash writing an obvious hardcoded secret
  gensrc_guard        PreToolUse  gensrc  a Write/Edit/MultiEdit that hand-edits a registered generated artefact

Contract (doc-confirmed 2026-08-17 against code.claude.com/docs/en/hooks): the hook payload arrives
as JSON on stdin. A PreToolUse handler that decides emits, on exit 0,
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow"|"deny",
"permissionDecisionReason": "..."}}; an allow decision is expressed as NO output (exit 0 silent), so
the user's own permission flow is never bypassed, and a deny decision blocks the tool. exit 2 is a
blocking error whose stderr is fed back to Claude. The Stop payload carries the final assistant text
as last_assistant_message (there is NO stop_hook_active field in the current Stop payload).

NO-ASK POSTURE (maintainer directive): these hooks NEVER return permissionDecision "ask". An unattended
orchestrator must never stall waiting on a human, so there is no ask constructor at all and every decision
is one of exactly THREE outcomes:
  * ALLOW - a clean pass (exit 0 silent), or ALLOW-with-an-informational-note (_allow_note: exit 0 with a
    systemMessage and NO permissionDecision, so the user's own permission flow still governs) where a
    former ask hedged something that is BENIGN or a convention/style nudge, not a real hazard.
  * SNAPSHOT-THEN-ALLOW - a RECOVERABLE-destructive discard: the inert refs/aiqt-recovery/ snapshot is
    taken, then the discard is ALLOWED with a note pointing at the recovery ref; it DENIES only if the
    warranted snapshot itself could not be created (an unrecoverable discard).
  * DENY-and-educate - a CONFIRMED hazard (a security/integrity violation) whose message NAMES the reachable
    correct action, so the orchestrator self-corrects and continues rather than stalling. A genuinely
    AMBIGUOUS hazard-class action (a fallback/parse-error on a dangerous command, an unprovable
    protected-line rewrite) denies fail-safe on the same terms.

READING KEY FOR "ASK"/"ASKS" BELOW (disclose-accuracy): several of these guards were ORIGINALLY built as
three-outcome allow/ask/deny controls, and the words "ASK" and "ASKS" still appear throughout this module's
docstrings and inline comments as HISTORICAL shorthand for that retired ask outcome and the option-classifier
scopes it once fed. Every such occurrence is historical, not a live decision: read it as its no-ask
resolution above - a benign or convention-level case ALLOWS (with an informational note), a recoverable
discard is SNAPSHOT-THEN-ALLOWED, and a confirmed or genuinely-ambiguous hazard DENIES-and-educates. NO code
path in this module emits permissionDecision "ask" (the _ask constructor is removed and the self-test's
global invariant asserts it), so wherever the prose says a form "ASKS", "routes to ASK", "over-ASKS", or
"fails safe to ASK", the LIVE behaviour is that no-ask resolution. Each top-level guard's own docstring
states its current outcome explicitly under a "NO-ASK posture" heading; this key governs the finer-grained
option-classifier and helper narration that still uses the historical term.

Error posture at the PreToolUse layer: FAIL CLOSED. A control that cannot read the input it is meant to
cover, or is invoked in a context it does not understand, DENIES rather than waving the action through (per
integ-check-fails-closed-on-unreadable): a missing tool_name, an unreadable command string, or an
unreadable required field deny (except where the check is a pure convention nudge - abspth, expbnd - whose
unreadable-input case allows with a note, since there is no hazard to fail closed on). A confirmed violation
denies. A clean pass emits NO decision and exits 0 silently.

gensrc_guard (gensrc): a CONFIRMED registry match (a hand-edit of a registered generated artefact) DENIES
and names the source to edit and the regenerate command. A PRESENT registry it cannot read (not a regular
file, a stat or read fault, an oversize, non-UTF-8, malformed-JSON or non-object file, a non-int or unknown
version, a non-list generated field, a malformed entry) is a cannot-evaluate branch that DENIES, fail
closed, so a corrupted registry cannot silently disable the protection. Its other cannot-evaluate branches
(an unreadable tool_name, tool_input or file_path payload field, a control character in file_path, no
session cwd, a non-git session, an unresolvable target or repo root, a target outside the repo or a
containment fault, a registry entry that cannot be resolved for containment) are NOT confirmed
generated-artefact edits and have no reachable "edit the source" action to name, and denying them would
block legitimate Write/Edit/MultiEdit calls, so they ALLOW with a note; the CI generated-artefact drift gate
remains the authoritative backstop. An absent registry is the inert ALLOW; a missing tool_name also denies
under the shared fail-closed contract, and a mis-wired event hard-blocks.

commit_msg_subst (sectvl): a backtick or $( command substitution in a git commit argument is a
command-injection hazard the shell runs before git sees the argument, so it DENIES and names the safe
re-issue (single quotes, escape the marker, or git commit -F <file>); the unparseable-fallback and unreadable
-command cases deny fail-safe on the same terms. Only a mis-wired event hard-blocks (no structured decision).

git_explicit_binding (expbnd): an ambient git target or a whole-tree breadth+publish is a binding CONVENTION
where the orchestrator normally binds its own target, not a hazard, so every former ask (including the
unreadable-command case) ALLOWS with a note.

git_discard (prsunc) is a DELIBERATE, ULTRA-CONSERVATIVE "recover then allow" guard (EN-6). NO-ASK
TRANSLATION: this control was originally an "ask unless PRISTINE and provably clean" three-outcome
(allow/ask/deny) guard; under the no-ask directive every former ASK is now resolved WITHOUT prompting -
a recoverable-destructive discard is SNAPSHOT-THEN-ALLOWED (the inert refs/aiqt-recovery/ snapshot is taken,
then it is allowed with a recovery-pointer note), and it DENIES only when a warranted recovery snapshot
cannot be created (an unrecoverable discard) or when the command cannot be classified or resolved at all (an
inline alias, an unrecognized flagged subcommand, an unresolvable worktree). The DENY of a confirmed
whole-tree clobber on a dirty tree is unchanged (it too carries a recovery snapshot when one could be made).
So wherever the detailed prose below says a form "ASKS", read it as "snapshot-then-allows when recoverable,
else denies-and-educates". UNLIKE the fail-closed controls above, it fails OPEN
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
(unbalanced quote) is not a free pass, it is scanned raw for a lossy verb keyword and, when one is present,
takes the recover-then-allow/deny resolution above (never a silent pass). WITHIN scope (a recognized lossy
verb: checkout/switch/restore/reset/clean/stash/
rm/branch) that resolution applies unless the command is a PRISTINE SINGLE BARE 'git <verb>' invocation AND
the tree is provably clean (or the leading opt-out is set, or the form is genuinely non-destructive), in
which case it ALLOWS. PRISTINE
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
on exit 0. No Stop invocation reaches exit 2 on an ERROR path; a Stop handler may still return exit 2 as a
DELIBERATE decision where the platform documents exit 2 as the block (the orchestration stop guard denies a
manufactured wind-down that way, doc-confirmed 2026-08-29, bounded by its own loop bound so it cannot wedge
the chain). Outside that deliberate deny, only a PreToolUse handler fails closed via exit 2, and only a
genuinely UNKNOWN mode (not in HANDLERS, an unidentifiable broken install) does so on a bad invocation.
"""
import sys

# PYTHON-FLOOR guard (the hook form of tools/check_python_floor.py). It runs before HANDLER_EVENT exists, so
# it carries its own literal of the fail-open modes, which selftest_aiqt_hooks.py holds equal to the
# HANDLER_EVENT entries whose event is in FAIL_OPEN_EVENTS. On an older interpreter that can start this file
# such a mode WARNS on exit 0 and never blocks (a Stop block here would re-fire with no cap); every other
# mode, PreToolUse and an unknown mode alike, fails closed with exit 2, as main() does on its own error paths.
# An older interpreter that cannot start this file never reaches the guard and fails with Python's own error
# first, and that exit has the event's normal meaning: one that accepts -I but cannot compile this file exits
# 1, a non-blocking error, so a PreToolUse call goes ahead unchecked; one that predates the -I option every
# hook entry passes exits 2 on every event the plugin hooks.json registers. That denies each PreToolUse
# call a registered matcher selects; blocks every UserPromptSubmit prompt, which FAIL_OPEN_EVENTS below says
# an error must never do; blocks every Stop, with no cap as above, and every TeammateIdle, the two
# FAIL_OPEN_EVENTS this file names exit 2 as the block for; on PostToolUse its tool has already run but the
# recorder records nothing; and SessionStart cannot block at all.
FLOOR_FAIL_OPEN_MODES = ("diff_wall_stop", "orch_dispatch_ledger", "orch_prompt_stamp", "orch_resume_audit",
                         "orch_stop_guard", "orch_teammate_idle")

if tuple(sys.version_info[:2]) < (3, 14):
    _floor_refusal = (
        "error: aiqt_hooks.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    sys.stderr.write(_floor_refusal)
    if len(sys.argv) > 1 and sys.argv[1] in FLOOR_FAIL_OPEN_MODES:
        import json
        sys.stdout.write(json.dumps(dict(systemMessage=(
            "AIQT guardrail: the %s check could not run (%s); surfacing a warning rather than blocking "
            "(non-blocking by design on this event)." % (sys.argv[1], _floor_refusal.strip())))) + "\n")
        raise SystemExit(0)
    raise SystemExit(2)

import collections
import datetime
import fnmatch
import json
import math
import os
import pathlib
import re
import selectors
import shlex
import signal
import shutil
import stat
import subprocess
import tempfile
import time

PRETOOL = "PreToolUse"
STOP_EVENTS = ("Stop", "SubagentStop")
# Events whose handlers must NEVER exit 2 on an ERROR path (a deliberate handler deny may still
# return 2 where the platform documents exit 2 as the block: Stop and TeammateIdle). SessionStart
# cannot block at all; an errored UserPromptSubmit stamp must never block a human prompt; a
# PostToolUse recorder error must surface as a warning, not a tool failure. Doc-confirmed 2026-08-29.
FAIL_OPEN_EVENTS = STOP_EVENTS + ("SessionStart", "TeammateIdle", "UserPromptSubmit", "PostToolUse")


# --- decision constructors ---------------------------------------------------------------------------
# A handler returns (exit_code, stdout_obj_or_None, stderr_text_or_None). The dispatcher prints the
# stdout object as JSON when present, prints the stderr text when present, and exits with the code.
#
# NOTE_CONSTRUCTORS is the declared set of note constructors and DENY_CONSTRUCTORS the declared set of
# deny constructors (_deny, whose block result also carries a banner, and every helper that returns a
# deny constructor's call). Only the single-return leaf constructors (_allow_note, _context_note, _stop_warn,
# _dispatcher_fail_open_warn and _deny) spell the {"systemMessage": ...} key; every other declared
# constructor reaches a result only by returning another constructor's call. The hooks self-test
# (tools/selftest_aiqt_hooks.py, _note_constructor_shape_failures) checks these shapes: a leaf builds its
# note or deny dict only as a dict literal (no call to dict by name or attribute, such as
# builtins.dict(...), no systemMessage or hookSpecificOutput keyword, and neither key string other than as
# a key of a dict literal) and holds that literal inside
# its return through tuple elements only, in a name bound once to that literal (a plain or annotated
# assignment) and loaded only there, or as the one argument of `print(json.dumps(...))`, with no global
# or nonlocal statement, and its hookSpecificOutput value is a dict literal; every use of a declared
# name is the callee of a call that is the direct value of a `return` statement (no assignment,
# unpacking, subscript, alias, attribute, conditional expression, lambda, comprehension or argument); no
# declared constructor, HANDLERS entry or main carries a decorator; and main holds no global or nonlocal
# statement, binds a handler's result once and uses its stdout object only in `print(json.dumps(...))`
# and `is None` tests. Those rules target an edit of a handler's stdout object; the exit code main
# returns is outside this check. Each note site is one `return <constructor>(...)` position that the
# hooks self-test inventories and requires to execute. That static check is not a defence against
# adversarial source: a result transformed, inside or after a constructor, by any construct the scan
# does not model (a note key assembled at run time, a lookup through getattr or globals(), or a patched
# json.dumps, print or sys.stdout, for example) is outside it.
NOTE_CONSTRUCTORS = (
    "_allow_note", "_context_note", "_stop_warn", "_dispatcher_fail_open_warn", "_diff_source_fallback",
    "_discard_recovery_result", "_expbnd_breadth_ask", "_expbnd_fallback", "_expbnd_target_ask",
    "_gate_weakening_fallback", "_gensrc_fail_ask", "_git_discard_fallback", "_stash_drop_clear_outcome",
    "_orch_stop_family")
DENY_CONSTRUCTORS = (
    "_deny", "_abspth_check_required", "_abspth_check_search_root", "_commit_denial",
    "_commit_identity_fallback", "_commit_msg_subst_fallback", "_deny_missing_tool_name", "_deny_relative",
    "_deny_with_recovery", "_discard_deny", "_expbnd_breadth_publish_deny", "_protected_line_fallback",
    "_wrtscp_deny")

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


def _allow_note(message):
    """A clean pass that ALSO surfaces an informational systemMessage. permissionDecision is deliberately
    OMITTED (so the user's own permission flow governs, exactly as _allow does), while the note explains
    what the guard observed. This is the replacement for the retired ASK outcome on a BENIGN or
    convention-level observation: hooks NEVER ask (an unattended orchestrator must never stall waiting on a
    human), so where an ASK once hedged something that is not a real hazard, the call PROCEEDS and this note
    is surfaced instead of prompting. A genuine hazard denies-and-educates (_deny); a recoverable-destructive
    discard is snapshotted then allowed with this note. There is no ask constructor: the module cannot emit
    permissionDecision "ask" from any path."""
    return (0, {"systemMessage": message}, None)


def _context_note(event, context, message):
    """A clean pass that adds model context (hookSpecificOutput.additionalContext, for an event such as
    UserPromptSubmit) and ALSO surfaces an informational systemMessage to the operator, who does not see that
    context. Like _allow_note it carries no permissionDecision and never blocks."""
    return (0, {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context},
                "systemMessage": message}, None)


def _stop_warn(banner):
    """A Stop surfacing WARN: exit 0 with a systemMessage banner and NOTHING blocking. Every Stop
    outcome that WARNS (a guard's own I/O error, or a loop-bound-relieved forced exit) uses this, so the
    warn path can never wedge a turn chain; a DELIBERATE Stop deny returns exit 2 directly (see the
    module docstring's design note)."""
    return (0, {"systemMessage": banner}, None)


def _hard_block(message):
    """A fail-closed hard block where no structured decision can be formed (a mis-wired PreToolUse
    event): exit 2 with the diagnostic on stderr, so a broken guard blocks rather than silently
    passing. The Stop handlers do not use this constructor: their ERROR paths warn (exit 0), while a
    DELIBERATE Stop deny returns exit 2 directly (the orchestration stop guard, not warn-only)."""
    return (2, None, message)


def _deny_missing_tool_name(rule):
    """Fail closed on a PreToolUse payload with no tool_name: it cannot be matched against, so it
    cannot be cleared. A present-but-different tool is handled separately (allow, defensive)."""
    return _deny("AIQT rule {} (fail-closed): malformed payload: missing tool_name.".format(rule),
                 "AIQT guardrail: denied a PreToolUse call with no tool_name (rule {}, fail-closed)."
                 .format(rule))


# --- shared raw-command tokenizer (quote/redirect-aware) ---------------------------------------------
# ONE raw-character lexical pass over the Bash command, shared by every lexical Bash hook (diff-source,
# commit-identity, protected-line, gate-weakening, git-explicit-binding, and git-discard's lossy scan). It
# REDIRECTION from RAW character positions and quote provenance BEFORE any token stream exists, so a shell
# redirection ANYWHERE in a command (leading, interspersed, or trailing: 'git >/dev/null commit', '>out
# pytest') is recorded as redirect metadata and REMOVED from the argv the handlers judge, closing the
# post-tokenize redirect-pollution class shared across the hooks. A naive post-tokenize strip was proven
# unsafe (a quoted '>' or a numeric option value was over-stripped into a silent allow, prtbrn round-10
# revert), so the strip happens HERE, from raw positions, where quote provenance and fd adjacency are still
# visible - never reconstructed from already-tokenized words.
#
# It is a LEXER, not a shell parser: it models the recognized redirection and separator grammar and, for
# any construct it does not model - a heredoc ('<<'/'<<-'), a here-string ('<<<'), a process substitution
# ('<('/'>('), a '{fd}>' redirect, a malformed or unterminated redirect, an unbalanced quote or escape,
# or a NUL - it RAISES ValueError so the caller falls back to its conservative raw scan rather than
# returning partial argv (never a partially-cleaned command). An expansion or command substitution ('$',
# '$( )', backtick) or a glob/brace/tilde is not modelled either: it marks the segment OPAQUE (the same
# residual the earlier tokenizer disclosed), so no diff-source ALLOW proof can rest on it.
_SEGMENT_OPERATORS = frozenset((";", "|", "|&", "||", "&&", "&", "(", ")"))
_METACHARS = "<>|&;()"  # unquoted, unescaped: begin an operator (redirect or separator)
# An unquoted expansion/substitution/glob/brace makes a word (and its segment) OPAQUE: no summary/file/pager
# proof may rest on it. A LEADING '~' is tilde expansion; a mid-word '~' is literal (HEAD~1), handled below.
_OPAQUE_WORD_CHARS = frozenset(("$", "`", "*", "?", "[", "{"))
_FD_VAR_RE = re.compile(r"^\{[A-Za-z_][A-Za-z0-9_]*\}$")  # a bash {varname}> fd-var redirect (unsupported)

# A redirect record: op (the operator text), src_fd (the effective source fd, or None for the both-streams
# '&>' forms), target (the decoded target word), target_class (static-real 'file-real', a console/device
# 'file-dev' under /dev or /proc, a numeric/'-' 'descriptor', or a dynamic 'opaque'), stdout_effect
# (what this redirect does to STDOUT: '' none, or 'file-real'/'file-dev'/'descriptor'/'opaque'), and
# target_leading_tilde (True only when the target begins with an UNQUOTED, whole-prefix-unquoted '~' that
# bash tilde-expands AND that expansion is a current-user '~'/'~/x', so the target is absolute like $HOME;
# the abspth Bash floor reads it so a '> ~/x' redirect is ALLOW-consistent with a 'cd ~/x' destination).
_Redirect = collections.namedtuple(
    "_Redirect", ("op", "src_fd", "target", "target_class", "stdout_effect", "target_leading_tilde"))
# A segment record: argv (the quote-decoded words the program receives, REDIRECTION ABSENT), sep_after (the
# operator that ended it, one of _SEGMENT_OPERATORS, or "" at a newline/command end), redirects (the ordered
# redirect records), raw (the raw slice of this segment), opaque_shell (True when the segment carried an
# unquoted expansion/substitution/glob/brace/tilde that no ALLOW proof may rest on), argv_opaque (the
# per-token opacity flags PARALLEL to argv: True where THAT word carried an unquoted expansion/glob/brace or
# a leading tilde, so a consumer can tell an unquoted, shell-expanded '~/x' from a quoted, literal '~/x'),
# and argv_leading_tilde (the per-token flags PARALLEL to argv: True ONLY where THAT word begins with an
# UNQUOTED tilde AND the WHOLE tilde-prefix - '~' up to the first unquoted '/' or end of word - is unquoted
# and unescaped, which is exactly when bash tilde-expansion applies and the word expands to an absolute home.
# This is a STRICTER signal than argv_opaque, which any unquoted expansion/glob/brace ANYWHERE in the token
# also sets; a quoted leading tilde with an unquoted tail, e.g. "~"/x*, and a leading tilde whose PREFIX
# carries a quote or escape, e.g. ~"/x" or ~\/x, are correctly NOT read as an expanded '~').
_Segment = collections.namedtuple(
    "_Segment", ("argv", "sep_after", "redirects", "raw", "opaque_shell", "argv_opaque",
                 "argv_leading_tilde"))


def _read_word(command, i, n):
    """Read exactly ONE shell word starting at index i (which must be at a word character, never a space,
    newline, or metacharacter). Returns (text, opaque, started, all_digits, leading_tilde, new_i): text is
    the quote/escape decoded word, opaque is True if it carried an unquoted
    expansion/substitution/glob/brace/leading-tilde, started is True once any character (even an empty ''
    quote) began the word, all_digits is True only when the whole word is UNQUOTED decimal digits (an
    IO_NUMBER candidate), leading_tilde is True ONLY when the word begins with an UNQUOTED tilde AND the
    WHOLE tilde-prefix (from that '~' up to the first UNQUOTED '/' or end of word) is unquoted and unescaped,
    which is exactly when bash tilde-expansion applies. A quoted, escaped, or non-leading tilde does not set
    it, and neither does a quote or escape ANYWHERE in the tilde-prefix (e.g. ~"/x", ~'', or an escaped '/'
    after the tilde stay relative), while a quote or expansion AFTER the prefix-closing '/' (e.g. ~/$VAR) does
    not block it. Stops at an
    unquoted space, tab, newline, or metacharacter. Raises ValueError on an unbalanced quote or an
    unterminated escape."""
    chars = []
    opaque = False
    started = False
    all_digits = True
    leading_tilde = False
    # tilde_prefix_open: True while we are still INSIDE the tilde-prefix (from a leading unquoted '~' up to
    # the first UNQUOTED '/', or end of word). Bash expands the leading '~' ONLY when the WHOLE tilde-prefix
    # is unquoted and unescaped; any quoted or escaped character in that span disables expansion and the word
    # stays RELATIVE. So while the prefix is open, a single-quote, double-quote, or backslash escape CONTAMINATES
    # it: leading_tilde is cleared. An unquoted '/' CLOSES the prefix cleanly (leading_tilde stays set), so
    # material AFTER it - 'cd ~/$VAR' - never blocks the expansion.
    tilde_prefix_open = False
    while i < n:
        c = command[i]
        if c in " \t\n" or c in _METACHARS:
            break
        if c == "\\" and i + 1 < n and command[i + 1] == "\n":
            # A backslash-newline is a LINE CONTINUATION: it is removed entirely and does NOT start or
            # contribute to a word, so 'git \<newline> commit' is [git, commit], never [git, "", commit]
            # (a synthetic empty argv element that mis-set the subcommand and defeated the sibling guards).
            i += 2
            continue
        if c == "#" and not started:
            # A '#' still at a WORD BOUNDARY (the word has not begun) starts a comment, even when the
            # boundary was EXPOSED by a preceding continuation join ('git diff \<newline># > out.txt'):
            # break so the caller re-applies its end-of-line comment handling rather than reading '#' as a
            # literal word (which would tokenize a commented-out redirect/pipe and earn a false proof).
            # A mid-word '#' (started is True, e.g. ticket#123 or a continuation-joined di\<newline>ff#x)
            # is left literal below.
            break
        was_started = started  # whether the word had begun BEFORE this char (a leading tilde needs it False)
        started = True
        if c == "'":  # single quote: everything literal, no escapes, until the next "'"
            if tilde_prefix_open:  # a quote inside the tilde-prefix disables bash expansion: stays relative
                leading_tilde = False
                tilde_prefix_open = False
            j = command.find("'", i + 1)
            if j < 0:
                raise ValueError("unterminated single quote")
            chars.append(command[i + 1:j])
            all_digits = False
            i = j + 1
            continue
        if c == '"':  # double quote: backslash escapes only "\ $ ` and newline; $ and backtick are opaque
            if tilde_prefix_open:  # a quote inside the tilde-prefix disables bash expansion: stays relative
                leading_tilde = False
                tilde_prefix_open = False
            i += 1
            while True:
                if i >= n:
                    raise ValueError("unterminated double quote")
                d = command[i]
                if d == '"':
                    i += 1
                    break
                if d == "\\":
                    if i + 1 >= n:
                        raise ValueError("unterminated escape")
                    e = command[i + 1]
                    if e in '"\\$`':
                        chars.append(e)
                        i += 2
                        continue
                    if e == "\n":  # line continuation inside a double quote: both characters removed
                        i += 2
                        continue
                    chars.append("\\")  # a backslash before any other char is literal inside "..."
                    i += 1
                    continue
                if d in "$`":
                    opaque = True
                chars.append(d)
                i += 1
            all_digits = False
            continue
        if c == "$" and i + 1 < n and command[i + 1] == "'":
            # ANSI-C quoting $'...': the content is LITERAL - bash performs NO command substitution,
            # parameter expansion, or globbing inside it - so a backtick or $( inside is NOT executable
            # (round-2 finding 15). It is read like a single quote (literal, NOT opaque); a backslash escapes
            # the next character (so an escaped \' does not end the string, and \\ is one backslash). The
            # common ANSI-C escapes are not expanded to their control bytes. Substitution-only consumers
            # may use the opacity result, but commit branch proof rejects this syntax because the lexer
            # has not established the execution-time bytes.
            if tilde_prefix_open:
                leading_tilde = False
                tilde_prefix_open = False
            i += 2
            while True:
                if i >= n:
                    raise ValueError("unterminated ANSI-C ($'...') quote")
                d = command[i]
                if d == "\\" and i + 1 < n:
                    chars.append(command[i + 1])
                    i += 2
                    continue
                if d == "'":
                    i += 1
                    break
                chars.append(d)
                i += 1
            all_digits = False
            continue
        if c == "\\":  # unquoted escape: the next character is literal (a backslash-newline continuation
            # was already consumed above, before the word could start)
            if tilde_prefix_open:  # an escape inside the tilde-prefix disables bash expansion: stays relative
                leading_tilde = False
                tilde_prefix_open = False
            if i + 1 >= n:
                raise ValueError("unterminated escape")
            chars.append(command[i + 1])
            all_digits = False
            i += 2
            continue
        # an ordinary unquoted character
        if c == "~" and not was_started:
            # A LEADING unquoted tilde is bash tilde-expansion (cwd-independent home): record it as such AND
            # mark the word opaque (no diff-source ALLOW proof may rest on the expanded value). Nothing has
            # begun the word before it, so an empty "" quote or any other char ahead of the '~' (""~, x~)
            # leaves was_started True and this branch is not taken - matching bash, which expands only a '~'
            # at the very start of the word.
            opaque = True
            leading_tilde = True
            tilde_prefix_open = True  # begin tracking the tilde-prefix; a later quote/escape voids the tilde
        elif c in _OPAQUE_WORD_CHARS:
            opaque = True
        elif c == "/" and tilde_prefix_open:
            # an UNQUOTED '/' ends the tilde-prefix cleanly: the leading '~' stays an expansion, and anything
            # after this '/' (an opaque '$VAR', a quote) no longer blocks it. 'cd ~/$VAR' remains an ALLOW.
            tilde_prefix_open = False
        if not c.isdigit():
            all_digits = False
        chars.append(c)
        i += 1
    return "".join(chars), opaque, started, all_digits, leading_tilde, i


def _match_operator(command, i, n):
    """Classify the operator at index i (a metacharacter). Returns (op, kind, length): kind is 'sep' for a
    segment separator, 'redirect' for a recognized redirection, or 'cannot' for an unsupported construct
    (a heredoc/here-string '<<', or a process substitution '<('/'>('). Longest-first: '&>>'/'&>' before '&',
    '>>'/'>|'/'>&' before '>', '<>'/'<&' before '<', '||'/'|&' before '|'."""
    c = command[i]
    nx = command[i + 1] if i + 1 < n else ""
    nx2 = command[i + 2] if i + 2 < n else ""
    if c == "&":
        if nx == ">":
            return ("&>>", "redirect", 3) if nx2 == ">" else ("&>", "redirect", 2)
        if nx == "&":
            return "&&", "sep", 2
        return "&", "sep", 1
    if c == ">":
        if nx == ">":
            return ">>", "redirect", 2
        if nx == "|":
            return ">|", "redirect", 2
        if nx == "&":
            return ">&", "redirect", 2
        if nx == "(":
            return ">(", "cannot", 2  # process substitution
        return ">", "redirect", 1
    if c == "<":
        if nx == "<":
            return "<<", "cannot", 2  # heredoc / here-string (<<, <<-, <<<)
        if nx == "(":
            return "<(", "cannot", 2  # process substitution
        if nx == ">":
            return "<>", "redirect", 2
        if nx == "&":
            return "<&", "redirect", 2
        return "<", "redirect", 1
    if c == "|":
        if nx == "|":
            return "||", "sep", 2
        if nx == "&":
            return "|&", "sep", 2
        return "|", "sep", 1
    return c, "sep", 1  # ';', '(', ')'


def _classify_redirect(op, src_fd, target, t_opaque, t_leading_tilde=False):
    """Build a _Redirect from a recognized operator, its explicit source fd (or None), the decoded target,
    and the target's clean-leading-tilde signal. Computes the target class and the effect on STDOUT
    (last-redirect-wins is applied by the caller). '&>'/'&>>' send both streams to the target (stdout
    affected); '>'/'>>'/'>|' affect stdout only when the effective source fd is 1; '>&' is fd
    duplication/close for a numeric or '-' target (descriptor, affecting stdout only from fd 1) or the csh
    both-streams-to-file form for a static word; the input forms '<'/'<>'/'<&' never touch stdout.
    tilde_abs is the ALREADY-GATED clean-tilde flag (an unquoted current-user '~'/'~/x' the shell expands to
    an absolute $HOME): it is stored on the record so the abspth floor allows such a target without
    re-deriving the gate, exactly as the cd/pushd destination path does."""
    tilde_abs = t_leading_tilde and bool(_LITERAL_TILDE_RE.match(target))
    if op in ("&>", "&>>"):
        tclass = _target_class(target, t_opaque)
        return _Redirect(op, None, target, tclass, tclass, tilde_abs)  # both streams -> stdout affected
    if op in (">", ">>", ">|"):
        src = 1 if src_fd is None else src_fd
        tclass = _target_class(target, t_opaque)
        return _Redirect(op, src, target, tclass, tclass if src == 1 else "", tilde_abs)
    if op == ">&":
        src = 1 if src_fd is None else src_fd
        if (not t_opaque) and (target == "-" or target.isdigit()):
            tclass = "descriptor"  # fd duplication or close, never a proven file
        else:
            tclass = _target_class(target, t_opaque)  # csh '>&file' both-streams-to-file form
        return _Redirect(op, src, target, tclass, tclass if src == 1 else "", tilde_abs)
    # input redirects ('<', '<>', '<&'): never a stdout effect
    src = 0 if src_fd is None else src_fd
    tclass = "descriptor" if op == "<&" else _target_class(target, t_opaque)
    return _Redirect(op, src, target, tclass, "", tilde_abs)


def _target_class(target, t_opaque):
    """Classify a redirect target: 'opaque' when it was formed with an unquoted expansion/substitution/
    glob/brace/tilde (dynamic, unresolvable) OR carries a '..' component (it can traverse to a device or
    anywhere and cannot be proven a plain file); 'file-dev' when it is a static path that resolves under
    /dev or /proc (a console/terminal or a stdout/stderr descriptor path, which still reaches the review
    surface); else a static 'file-real' ordinary path. The path is LEXICALLY normalized (os.path.normpath,
    never touching the filesystem) BEFORE classifying, so /tmp/../dev/stdout is recognized as a /dev target
    rather than passing as a plain /tmp file. Two cwd-dependent forms are NOT proven a plain file and route
    to 'opaque' (ASK): a '..' that normpath cannot resolve away (a relative escape such as
    ../../../dev/stdout), and a RELATIVE target whose normalized first component is 'dev' or 'proc'
    (dev/stdout, ./dev/stdout, proc/self/fd/1), which from cwd '/' or via a 'dev'/'proc' symlink IS the
    device the absolute form names but from a working tree is an ordinary relative file. The ABSOLUTE
    /dev,/proc form stays 'file-dev' (DENY, an unambiguous device); the relative form only ASKS, so a
    legitimate write into a repo's own dev/ or proc/ subdirectory is surfaced, not hard-blocked.

    A clean-leading-tilde target ('~'/'~/x') is intentionally left 'opaque' HERE (the tilde marks the word
    opaque): the abspth Bash floor reads the separate _Redirect.target_leading_tilde flag to allow it,
    consistent with the cd/pushd destination, WITHOUT reclassifying the target for the other consumers of
    this shared classifier (the L11 diff-source layer, which keeps its own tilde-target policy)."""
    if t_opaque:
        return "opaque"
    norm = os.path.normpath(target)
    if _DEV_PROC_TARGET_RE.match(norm) or _DEV_PROC_TARGET_RE.match(target):
        return "file-dev"
    if _REL_DEV_PROC_TARGET_RE.match(norm):
        return "opaque"  # a relative dev/proc-leading target is cwd-dependent: could BE the device
    if ".." in norm.replace("\\", "/").split("/") or ".." in target.replace("\\", "/").split("/"):
        return "opaque"  # a '..' component -> could resolve to a device; cannot be proven a plain file
    return "file-real"


def _parse_heredoc_delim(command, at, n):
    """ROUND-2 FINDING 17. Parse a heredoc delimiter spec starting immediately after '<<' (index `at`).
    Returns (quoted, delim, strip_tabs, next_i) or None when it cannot be parsed (so the caller keeps the
    conservative unsupported-construct raise). `quoted` is True for the <<'EOF' / <<"EOF" / <<\\EOF forms,
    whose body bash treats as LITERAL DATA with no expansion or command substitution; `strip_tabs` is True
    for the <<- form; `next_i` is the index just past the delimiter spec (still on the same line)."""
    j = at
    strip_tabs = False
    if j < n and command[j] == "-":
        strip_tabs = True
        j += 1
    while j < n and command[j] in " \t":
        j += 1
    if j >= n:
        return None
    # A heredoc delimiter is ONE shell word: the concatenation of adjacent quoted, escaped, and unquoted
    # fragments per shell word rules (round-3 finding 7). So <<'EO'F, <<EO'F', and <<E"OF" all resolve to
    # the delimiter EOF - the shell joins the adjacent fragments before comparing lines to it. Reading only
    # the first fragment (the pre-round-3 behaviour) mis-parsed a partially-quoted delimiter and let its true
    # closing line swallow a following command as heredoc body. The body is LITERAL (no expansion) when ANY
    # fragment was quoted or backslash-escaped; a fully-unquoted delimiter (<<EOF) leaves the body
    # interpolating. The three fully-quoted forms (<<'EOF'/<<"EOF"/<<\\EOF) still resolve exactly as before.
    quoted = False
    started = False
    delim_chars = []
    while j < n:
        c = command[j]
        if c in " \t\n" or c in _METACHARS:
            break                       # an unquoted separator/whitespace ends the delimiter word
        started = True
        if c == "'":                    # single quote: literal to the next "'"
            quoted = True
            k = command.find("'", j + 1)
            if k < 0:
                return None
            delim_chars.append(command[j + 1:k])
            j = k + 1
            continue
        if c == '"':                    # double quote: literal to the next '"', honouring \" \\ \$ \` escapes
            quoted = True
            k = j + 1
            frag = []
            while k < n and command[k] != '"':
                if command[k] == "\\" and k + 1 < n and command[k + 1] in '"\\$`':
                    frag.append(command[k + 1])
                    k += 2
                    continue
                frag.append(command[k])
                k += 1
            if k >= n:
                return None
            delim_chars.append("".join(frag))
            j = k + 1
            continue
        if c == "\\":                   # backslash quotes the next character -> literal body
            quoted = True
            if j + 1 >= n:
                return None
            delim_chars.append(command[j + 1])
            j += 2
            continue
        if c == "$" and j + 1 < n and command[j + 1] == "'":
            # ROUND-6 FINDING 2 (Lens E). ANSI-C quoting $'...' as (part of) a heredoc delimiter word.
            # bash ANSI-C-expands the word, so <<$'EOF' resolves to the LITERAL delimiter EOF, and its body
            # is literal (quoted). Treating '$' as an ordinary char (the pre-round-6 bug) built the delimiter
            # "$EOF", which never matched the real EOF line, so the lexer SWALLOWED every following executable
            # line as heredoc body and hid a later --no-verify commit / lossy discard. We resolve only the
            # ESCAPE-FREE content to its literal; a backslash inside the ANSI-C body is a form this parser does
            # not fully decode, so it returns None (the FAIL-CLOSED backstop: the caller then raises/scans the
            # remainder as still-executable rather than swallowing it under a guessed delimiter).
            quoted = True
            k = command.find("'", j + 2)
            if k < 0:
                return None
            frag = command[j + 2:k]
            if "\\" in frag:
                return None  # an ANSI-C escape we do not decode -> fail closed (scan the remainder)
            delim_chars.append(frag)
            j = k + 1
            continue
        if c == "$" and j + 1 < n and command[j + 1] == '"':
            # $"..." locale translation as (part of) a heredoc delimiter word: bash quote-removal yields the
            # inner text (the C-locale identity), so <<$"EOF" resolves to the literal delimiter EOF. Parsed
            # like a double-quoted fragment (honouring \" \\ \$ \` escapes) so it resolves to its literal
            # content; a form it cannot close returns None (fail closed, per the ANSI-C branch above).
            quoted = True
            k = j + 2
            frag = []
            while k < n and command[k] != '"':
                if command[k] == "\\" and k + 1 < n and command[k + 1] in '"\\$`':
                    frag.append(command[k + 1])
                    k += 2
                    continue
                frag.append(command[k])
                k += 1
            if k >= n:
                return None
            delim_chars.append("".join(frag))
            j = k + 1
            continue
        delim_chars.append(c)           # an ordinary unquoted character
        j += 1
    if not started:
        return None
    return (quoted, "".join(delim_chars), strip_tabs, j)


def _skip_heredoc_bodies(command, i, n, heredocs):
    """Advance past the bodies of a run of QUOTED heredocs (in the order their '<<' operators appeared on the
    line), each ending at a line that is EXACTLY its delimiter (leading tabs stripped for the <<- form). An
    unterminated heredoc consumes the rest of the command (bash would still be reading input). Returns the
    index just past the last consumed body. The bodies are literal data and are excluded from analysis."""
    for delim, strip_tabs in heredocs:
        while i < n:
            nl = command.find("\n", i)
            line = command[i:(nl if nl != -1 else n)]
            cand = line.lstrip("\t") if strip_tabs else line
            i = n if nl == -1 else nl + 1
            if cand == delim:
                break
    return i


def _strip_quoted_heredoc_bodies(command):
    """ROUND-2 FINDING 17. Return `command` with the BODIES of QUOTED heredocs (<<'EOF'/<<"EOF"/<<\\EOF)
    removed, so a RAW (regex) scan that runs unconditionally - e.g. git_discard's _raw_has_lossy_git - does
    not match shell syntax quoted inside heredoc prose. Everything OUTSIDE the quoted-heredoc bodies is
    preserved verbatim (a real 'git reset --hard <<'EOF'...' still scans as lossy). UNQUOTED heredoc bodies
    interpolate and stay in scope. Best-effort and conservative: on any ambiguity the text is left in place
    (the raw scan then over-matches, the safe direction), and multiple heredocs stacked on one line beyond
    the first are a disclosed over-match residual, never an under-match."""
    n = len(command)
    out = []
    i = 0
    while i < n:
        lt = command.find("<<", i)
        if lt < 0:
            out.append(command[i:])
            break
        parsed = _parse_heredoc_delim(command, lt + 2, n)
        if parsed is None or not parsed[0]:
            out.append(command[i:lt + 2])   # not a quoted heredoc: keep the '<<' and continue past it
            i = lt + 2
            continue
        _q, delim, strip_tabs, next_i = parsed
        nl = command.find("\n", next_i)
        if nl < 0:
            out.append(command[i:next_i])    # the opening line has no body yet (no newline): keep, done
            i = next_i
            continue
        out.append(command[i:nl + 1])        # keep through the opening line's newline
        i = _skip_heredoc_bodies(command, nl + 1, n, [(delim, strip_tabs)])  # drop the body region
    return "".join(out)


def _lex_command(command, partial=False):
    """Lex the raw Bash command into ordered _Segment records. Raises ValueError on an unbalanced quote or
    escape, a NUL, or an unsupported construct (an UNQUOTED heredoc, a here-string, a process substitution, a
    '{fd}>' redirect, or a malformed redirect), so the caller falls back conservatively rather than acting on
    a partially-cleaned command. A QUOTED heredoc (<<'EOF'/<<"EOF"/<<\\EOF) is NOT unsupported: its literal
    body is EXCLUDED from analysis (round-2 finding 17) so a guard never fires on shell syntax quoted inside
    heredoc prose. Linear, non-recursive, stdlib-only.

    When partial is True the raise-on-error contract is replaced by a best-effort one: instead of raising,
    the lexer returns (segments, complete), where segments are the COMPLETE segments recovered before the
    first unparseable construct and complete is False when such a construct truncated the scan (True when
    the whole command parsed). This lets the Bash abspth floor still inspect a resolvable PREFIX (an
    earlier relative cd/redirect) even when a LATER segment is unparseable, rather than discarding every
    parsed segment on the first heredoc/here-string/process-substitution. The default partial=False keeps
    the list-returning, raise-on-error contract every other caller relies on."""
    n = len(command)
    segments = []
    argv = []
    argv_opaque = []
    argv_leading_tilde = []
    redirects = []
    opaque = [False]
    seg_start = [0]
    pending_heredocs = []  # queued (delim, strip_tabs) for QUOTED heredocs whose bodies to skip (finding 17)

    def end_segment(sep, op_start, next_start):
        segments.append(_Segment(list(argv), sep, list(redirects),
                                  command[seg_start[0]:op_start], opaque[0], list(argv_opaque),
                                  list(argv_leading_tilde)))
        del argv[:]
        del argv_opaque[:]
        del argv_leading_tilde[:]
        del redirects[:]
        opaque[0] = False
        seg_start[0] = next_start

    def consume_redirect(op, oplen, at, src_fd):
        k = at + oplen
        while k < n and command[k] in " \t":  # inline whitespace before the target (never a newline)
            k += 1
        # A redirect target that begins with an unquoted '#' is a comment where a filename must be (bash
        # treats '#' at a word start as a comment, so the redirect has no target): cannot-evaluate, ASK.
        if k >= n or command[k] == "\n" or command[k] in _METACHARS or command[k] == "#":
            raise ValueError("malformed redirect: no target")
        target, t_opaque, started, _digits, t_ltilde, k2 = _read_word(command, k, n)
        if not started:
            raise ValueError("malformed redirect target")
        redirects.append(_classify_redirect(op, src_fd, target, t_opaque, t_ltilde))
        return k2

    try:
        if "\x00" in command:
            raise ValueError("NUL in command")
        i = 0
        while i < n:
            c = command[i]
            if c in " \t":
                i += 1
                continue
            if c == "\n":
                end_segment("", i, i + 1)
                i += 1
                if pending_heredocs:
                    # the bodies of any QUOTED heredocs opened on this line follow the newline; skip them as
                    # literal data (finding 17), then resume lexing after the closing delimiter line(s).
                    i = _skip_heredoc_bodies(command, i, n, pending_heredocs)
                    del pending_heredocs[:]
                    seg_start[0] = i
                continue
            if c == "#":
                # An unquoted '#' at a WORD BOUNDARY starts a comment to end of line (bash): the rest of
                # the line is ignored, so a redirect or pipe that is actually commented out (git diff # >
                # /tmp/x) never earns a proof. A mid-word '#' (--rev=abc#1, ticket#123) is read literally
                # inside _read_word and never reaches this word-start position.
                nl = command.find("\n", i)
                i = n if nl == -1 else nl
                continue
            if c in _METACHARS:
                op, kind, oplen = _match_operator(command, i, n)
                if kind == "sep":
                    end_segment(op, i, i + oplen)
                    i += oplen
                    continue
                if kind == "cannot":
                    if op == "<<":
                        parsed = _parse_heredoc_delim(command, i + oplen, n)
                        if parsed is not None and parsed[0]:
                            # a QUOTED heredoc: queue its delimiter; the literal body is skipped at the next
                            # newline (finding 17). The delimiter spec is consumed here; the current line's
                            # remaining tokens (e.g. 'cat <<'EOF' > out') keep being lexed normally. An
                            # UNQUOTED heredoc (parsed[0] False) interpolates and stays the unsupported raise.
                            _quoted, _delim, _strip, _next_i = parsed
                            pending_heredocs.append((_delim, _strip))
                            i = _next_i
                            continue
                    raise ValueError("unsupported shell construct: {}".format(op))
                i = consume_redirect(op, oplen, i, None)
                continue
            text, w_opaque, started, all_digits, w_leading_tilde, j = _read_word(command, i, n)
            # An IO_NUMBER: an entirely-unquoted-digit word IMMEDIATELY adjacent to a '<'/'>' redirect
            # operator is that redirect's source fd, consumed as fd syntax, never left in argv. '2>file' ->
            # fd 2; but '2 > file' (spaced), "'2'>file"/'\2>file' (quoted/escaped), and 'x2>file' keep it.
            if all_digits and text and j < n and command[j] in "<>":
                op, kind, oplen = _match_operator(command, j, n)
                if kind == "redirect":
                    i = consume_redirect(op, oplen, j, int(text))
                    continue
                if kind == "cannot":
                    raise ValueError("unsupported shell construct: {}".format(op))
            # A '{varname}>' fd-var redirect is not modelled: cannot-evaluate rather than a partial word.
            if text and j < n and command[j] in "<>" and _FD_VAR_RE.match(text):
                raise ValueError("unsupported {fd} redirect")
            if started:
                argv.append(text)
                argv_opaque.append(w_opaque)
                argv_leading_tilde.append(w_leading_tilde)
                if w_opaque:
                    opaque[0] = True
            i = j
        end_segment("", n, n)
    except ValueError:
        # partial mode recovers the COMPLETE segments lexed before the unparseable construct AND the
        # positions of the IN-PROGRESS segment that were fully parsed BEFORE it (an earlier relative
        # cd/redirect in the SAME, still-open segment - e.g. the 'out.txt' of 'cat > out.txt <<EOF' - is
        # thereby still inspected by the Bash abspth floor); a position WITHIN or AFTER the unparseable
        # construct stays uninspected, a disclosed residual. The default contract re-raises for every
        # other caller.
        if partial:
            if argv or redirects:
                segments.append(_Segment(list(argv), "", list(redirects),
                                         command[seg_start[0]:], opaque[0], list(argv_opaque),
                                         list(argv_leading_tilde)))
            return segments, False
        raise
    if partial:
        return segments, True
    return segments


def _segments(command):
    """Compatibility projection over _lex_command: a list of (argv, sep_after) tuples, argv being the
    quote-decoded words with shell REDIRECTION removed (so a leading/interspersed/trailing redirect no
    longer pollutes the token stream) and sep_after the ending operator or "". Raises ValueError on a
    parse error or unsupported construct so callers fall back conservatively. Existing consumers
    (protected_line, gate_weakening, git_explicit_binding, git_discard, find_ai_authorship) read
    redirect-free argv automatically; diff_source_pretool uses the richer _Segment records directly."""
    return [(seg.argv, seg.sep_after) for seg in _lex_command(command)]


# A leading inline shell env-var assignment (FOO=bar or the APPEND form FOO+=bar) that PREFIXES a command,
# e.g. the GIT_PAGER=cat in 'GIT_PAGER=cat git diff'. Such assignments are SKIPPED when resolving a
# segment's command word and its git subcommand, so that form resolves to command word 'git' / subcommand
# 'diff' and is judged like a bare 'git diff' rather than slipping through as a non-git command word. The
# APPEND '+=' form (OLDPWD+=..) is a real bash assignment prefix too, so it is recognized here; missing it
# left the append-assignment mistaken for the command word, so the following cd was never examined. The
# any-segment identity-assignment scan still inspects these same tokens for an AI name
# (GIT_AUTHOR_NAME=Claude ...). Best-effort, per the manifest residue: the 'env VAR=x git ...' command form
# and the 'command git ...' builtin remain out of scope. This matches the DECODED token, so an
# assignment-SHAPED token whose NAME was quoted or escaped ("OLDPWD"=.. -> decoded OLDPWD=..) also matches;
# bash would treat that as a command, not an assignment, so recognizing it is a deliberate conservative
# over-fire (disclosed in the manifest residue), never an under-block.
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")


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


def _git_subcommand_rest(tokens):
    """(subcommand, args-after-subcommand) for a git segment whose command word is git: the first
    non-option token after the command word (skipping any leading env-assignment prefix and the git global
    options), paired with every token AFTER it (the subcommand's own options and operands). An arg-consuming
    global option in its space-separated form (-C DIR, --git-dir DIR, ...) skips two tokens (its value is
    now its own token, per the tokenizer) so the value is not read as the subcommand; the '--opt=value' form
    and any other leading '-' token skip one. (None, []) when there is no subcommand token."""
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
        return token, tokens[i + 1:]
    return None, []


def _git_subcommand(tokens):
    """The git subcommand of a segment whose command word is git (see _git_subcommand_rest), or None when
    there is no subcommand token."""
    return _git_subcommand_rest(tokens)[0]


def _blob_selector_token(tok):
    """True when a single git-show operand is a BLOB SELECTOR: the '<ref>:<path>' or ':<path>' form that
    names a file at a revision (it contains a ':' and does NOT begin ':/'). The ':/<text>' form is git's
    commit-MESSAGE SEARCH, which resolves to a commit and renders a diff, not a file, so it is excluded."""
    return ":" in tok and not tok.startswith(":/")


# ROUND-2 FINDING 16: git-show options that CONSUME a following SEPARATED token as their VALUE. That value
# may itself contain a ':' (e.g. 'git show -L 1,3:file' renders a line-range DIFF), so it must be skipped,
# never read as a blob-selector operand - otherwise the diff producer is misread as a pure blob read and a
# console diff dump slips through. The attached '--opt=val' / '-Xval' forms carry the value in the same token
# and are already skipped whole. This is the named set (-L/-U/--output/-O/--pretty/--format/-S/-G and kin);
# an unlisted value-taking option remains the disclosed F-119-class residual below.
_SHOW_VALUE_OPTS = frozenset((
    "-L", "-U", "-S", "-G", "-O", "-o",
    "--unified", "--output", "--output-indicator-new", "--output-indicator-old",
    "--output-indicator-context", "--pretty", "--format", "--color", "--line-prefix",
    "--src-prefix", "--dst-prefix", "--anchored"))


def _show_blob_selector(args):
    """True when a git-show argument list (the tokens AFTER the 'show' subcommand) is a pure BLOB READ:
    it has AT LEAST ONE non-option operand and EVERY non-option operand is a blob selector (a
    '<ref>:<path>' or ':<path>' form, per _blob_selector_token). In that form 'git show' prints each
    named file's contents at a revision - no diff, no @@ hunk headers, no +/- lines - so it renders no
    console diff (it is `cat` against a revision, the normal way to read a file on an un-checked-out
    branch). git show consumes N object arguments, so a SINGLE bare ref/commit operand (no colon), or a
    ':/<text>' commit-message SEARCH operand, among the arguments means git renders a full commit diff:
    the command is then a diff producer, not this blob read, and falls through to DENY/ASK exactly as a
    bare 'git show <commit>' does. Leading and interspersed options ('-'/'--opt') are skipped; an
    end-of-options '--'/'--end-of-options' makes every following token an operand. A best-effort lexical
    heuristic with ONE disclosed residual (disclose-guard-residuals), a SAFE-DIRECTION over-allow of at
    worst a small console dump for this quality guard, never an under-read: a separated option VALUE that
    happens to contain a ':' and stands as the sole apparent operand (e.g. 'git show -O opt:val' with no
    object) could be misread as a blob selector (the F-119-class limit the sibling detectors carry).
    Detection errs toward reading a file, not toward a false diff-dump deny."""
    i = 0
    n = len(args)
    seen_operand = False
    while i < n:
        tok = args[i]
        if tok in _DIFF_END_OF_OPTIONS:
            # after '--'/'--end-of-options' every remaining token is an operand
            for operand in args[i + 1:]:
                if not _blob_selector_token(operand):
                    return False
                seen_operand = True
            return seen_operand
        if tok.startswith("-"):
            # ROUND-2 FINDING 16: skip a value-taking option AND its separated value, so a value containing a
            # ':' (e.g. '-L 1,3:file') is not misread as a blob selector. Attached '--opt=val'/'-Xval' forms
            # carry the value in-token and are skipped whole here.
            if tok in _SHOW_VALUE_OPTS and i + 1 < n:
                i += 2
            else:
                i += 1
            continue
        if not _blob_selector_token(tok):
            return False  # a bare ref/commit or ':/' search operand -> git renders a diff
        seen_operand = True
        i += 1
    return seen_operand


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
        # Even a mis-wired event only WARNS here: the diff-wall Stop layer is warn-only end to end (the
        # orchestration stop guard, a separate Stop handler, is the one that deliberately denies via exit
        # 2), so nothing here can wedge a turn chain. The generator's event whitelist is the real guard.
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
# Layer A of the F-36 catch, redesigned FAIL-SAFE-BY-CONSTRUCTION (GD-112 AIRTIGHT-NARROW philosophy, the
# same one applied to the truncation guard): rather than enumerating every dumping form of git (an
# unbounded shell + git-option grammar, GD-34/F-119), it proves the SAFE form or DENIES/ASKS. It works on
# the shared quote/redirect-aware tokenizer (_lex_command), so a redirection anywhere in the command is
# recorded as metadata and removed from argv, closing the redirect-pollution class. A git diff-PRODUCER is
# ALLOWED only when the WHOLE command matches one of FOUR closed proofs: (A) an exact metacharacter-free
# help invocation (git <producer> --help/-h); (B) an exact summary-only command (a metacharacter-free
# 'git <producer> ... --stat/--name-only/...' whose only other option may be --no-patch); (C) a single
# simple command whose command word is literally 'git', a possible producer, whose LAST stdout redirect is
# proven to land on a static non-/dev,/proc file (last-redirect-wins over the raw redirect metadata); or
# (D) an exact two-stage terminal pager pipeline 'git <producer> | less/more/most/pager'. A producer that
# is confirmed to emit a console patch (a default diff/show/range-diff, or a patch-flagged listing) and
# fits no proof DENIES; any other producer-capable-but-unproven form (a wrapper, a pathed git, quotes, a
# compound, extra summary modifiers, a dynamic redirect target, a pipe to a non-pager) ASKS. The ALLOW path
# deliberately does NOT parse the full git/shell option grammar, so it over-ASKS (a git log carrying any
# extra flag, quoted prose) rather than risk a false ALLOW; the residual is disclosed in the manifest. A
# fifth proof (E) ALLOWs a benign 'git log' commit listing (bare, --oneline, or bare operands, no diff).
_PATCH_FLAGS = frozenset(("-p", "-u"))
_SUMMARY_FLAGS = frozenset(("--stat", "--name-only", "--name-status", "--numstat", "--shortstat"))
_INFO_FLAGS = frozenset(("--help", "-h"))
_PAGERS = frozenset(("less", "more", "most", "pager"))
# The producer-capable git SURFACES (a git word followed later by one of these makes a segment a POSSIBLE
# producer, the fail-safe ASK scope). 'show' covers a plain 'git show' and 'git stash show'.
_PRODUCER_SURFACES = frozenset((
    "diff", "show", "range-diff", "log", "diff-tree", "diff-index", "diff-files", "format-patch"))
# The summary-capable producers for the proof-B and proof-A exact forms (format-patch writes files, never a
# summary listing, so it is excluded here).
_SUMMARY_PRODUCERS = frozenset((
    "diff", "show", "range-diff", "log", "diff-tree", "diff-index", "diff-files"))
# Proof E (benign 'git log' commit-listing): an EXACT, curated allowlist of git log options that are BOTH
# value-free (they never consume a following word: any optional value is inline '=value' only, so a bare
# operand is never a swallowed value) AND provably diff-free (they affect only commit-listing display or
# traversal, never a patch or a file-list). Kept exact, never a fuzzy pattern: 'git log' renders a patch
# ONLY with a patch-generating flag (-p/-u/--patch* and kin), and a summary/file-list form (--stat,
# --numstat, --shortstat, --name-only, --name-status) EMITS output so is NOT here (--stat and kin are proof
# B). Any OTHER option - a patch flag, a summary/file-list flag, a pickaxe (-G/-S) whose diff behaviour
# depends on a co-present -p, a value-taking option (--format, -n <count>, --author=, --since=, --grep=), or
# any unknown flag - is NOT admitted and routes to the unchanged airtight-narrow default (ASK, or DENY when
# a patch flag confirms a console patch).
_BENIGN_LOG_OPTS = frozenset((
    "--oneline", "--graph", "--decorate", "--no-decorate", "--abbrev-commit", "--reverse", "--all"))
# A pipe to one of these known console/truncating sinks is a confirmed console dump (DENY); a pipe to a
# pager is proof D (ALLOW); a pipe to anything else is unprovable (ASK).
_CONSOLE_SINKS = frozenset(("cat", "tee", "head", "tail"))
_DIFF_END_OF_OPTIONS = frozenset(("--", "--end-of-options"))
# Shell reserved words that change execution without a metacharacter (so the plain-command charset alone
# would admit them); an exact token match excludes them from the proof-A/B metacharacter-free forms.
_DIFF_RESERVED_WORDS = frozenset((
    "if", "then", "else", "elif", "fi", "case", "esac", "for", "select", "while", "until",
    "do", "done", "in", "function", "time", "coproc"))
# The conservative metacharacter-free character set for the proof-A/B forms (letters, digits, space, tab,
# and the punctuation '_ - . / = : @ , + %'), so the whole command carries no quote, redirect, separator,
# expansion, substitution, grouping, glob, or comment - matching the GD-112 truncation-guard charset.
_DIFF_PLAIN_RE = re.compile(r"[A-Za-z0-9_ \t./=:@,+%-]+")
# A redirect target UNDER /dev/ or /proc/ is never a real diff-output file: it lands on a console/terminal
# (/dev/tty, /dev/pts/N, /dev/console) or on a stdout/stderr descriptor path (/dev/stdout, /dev/stderr,
# /dev/fd/N, /proc/self/fd/1, /proc/PID/fd/N), so a diff sent there still reaches the review surface.
_DEV_PROC_TARGET_RE = re.compile(r"^/+(?:dev|proc)(?:/|$)")
# A RELATIVE target whose normalized FIRST component is 'dev' or 'proc' (dev/stdout, ./dev/stdout,
# proc/self/fd/1) is CWD-DEPENDENT: from cwd '/', or where a 'dev'/'proc' symlink exists, it resolves to
# the very device the absolute form names, but from an ordinary working tree it is a plain relative file.
# It therefore cannot be proven a plain file lexically, so it is routed OPAQUE (ASK), never a proven
# real-file ALLOW - matching how a '..' escape (equally cwd-dependent) is handled.
_REL_DEV_PROC_TARGET_RE = re.compile(r"^(?:dev|proc)(?:/|$)")
# Fallback-only broad producer probe over the RAW command, used only when _lex_command cannot parse the
# command: an apparent 'git ... <producer>' ASKS (never a silent allow, never a regex-earned allow).
_RAW_DIFF_PRODUCER_RE = re.compile(
    r"(?is)\bgit\b.*?\b(?:diff|show|range-diff|log|diff-tree|diff-index|diff-files|format-patch)\b")


def _has_patch_flag(tokens):
    """True when a segment carries a patch flag (-p, -u, or a --patch* form: --patch, --patch-with-stat,
    --patch-with-raw), the flag that turns a listing/plumbing/stash producer into a console patch and
    that also dumps the full diff alongside a summary flag (git diff --stat -p). Clustered short patch
    flags (-wp), patch-implying options (-U/-c/--cc/-L), and wrapped producers are NOT modelled here: the
    diff-dump guard is best-effort and those forms are a DISCLOSED lexical residual routed to a separate
    cnsdif hardening effort (F-119)."""
    return any(t in _PATCH_FLAGS or t.startswith("--patch") for t in tokens)


# --- cnsdif git-show no-patch exemption (F-R2-2/F-R2-3): a position/argument/cluster-aware pass -------
# The no-patch exemption (git show -s / --no-patch prints only commit metadata and the message, no diff, so
# it is NOT a console patch) is granted ONLY when a GENUINE suppression flag sits in a real OPTION position
# AND no patch-enabling option and no UNKNOWN option shares the option region. Conservative by construction
# (over-cover, never under-cover): an unrecognized option, a suppression token that is really a post-'--'
# pathspec, or a suppression token that is really the VALUE of a value-taking option (-S/-G ...) never earns
# the exemption. This supersedes the earlier exact-token _has_no_patch_flag/_has_patch_flag pair for the
# 'show' branch, which scanned every token position-blind and so mistook a patch-implying option (-U/-sp) for
# non-diff and a post-'--'/-S-argument -s for a suppression flag.
# Patch enablers (any one in the region disqualifies the exemption): -p/-u, the --patch* family, -U/--unified
# (attached =N or a separated value), -c/--cc (a combined diff is still a patch), and any SHORT cluster whose
# scan reaches a p/u/U/c option letter. -U here is a patch enabler, not a mere value-taking option.
_SHOW_LONG_PATCH_PREFIXES = ("--patch", "--unified")   # --patch, --patch-with-stat/-raw, --patch=, --unified=
_SHOW_LONG_PATCH_EXACT = frozenset(("--cc",))
# Value-taking SHORT option letters (-L range, -S/-G pickaxe, -O order-file, -o output): each consumes the
# REST of its cluster (or the next token when it is the last letter) as its value, so a p/u AFTER one is that
# value's bytes, not an option letter. Mirrors the value-option semantics _SHOW_VALUE_OPTS encodes for
# _show_blob_selector; -U is deliberately absent (it is a patch enabler above, never merely skipped).
_SHOW_SHORT_VALUE_LETTERS = frozenset(("L", "S", "G", "O", "o"))
# Long options that ALWAYS consume a SEPARATED value, so skipping that value keeps a following patch flag
# that is really this option's argument from being miscounted. Optional/attached-value display options
# (--format/--pretty/--color and kin) are NOT here: they do not reliably consume a separated token, so
# skipping one could hide a real patch flag; they are recognized non-patch below and consume nothing.
_SHOW_LONG_REQ_VALUE = frozenset((
    "--output", "--output-indicator-new", "--output-indicator-old", "--output-indicator-context",
    "--line-prefix", "--src-prefix", "--dst-prefix", "--anchored"))
# Recognized NON-patch long options that consume no separated value: --no-patch, the summary listings
# (_SUMMARY_FLAGS, bare or '=value'), the raw/summary listings, and display-only options whose value, if
# any, is attached. Any long option outside every recognized set is UNKNOWN and does NOT earn the exemption.
_SHOW_LONG_NONPATCH = frozenset((
    "--no-patch", "--raw", "--summary", "--format", "--pretty", "--color", "--no-color",
    "--abbrev-commit", "--no-abbrev-commit", "--textconv", "--no-textconv")) | _SUMMARY_FLAGS


def _show_short_cluster_kind(tok):
    """Classify a SHORT git-show option token ('-' followed by letters, tok[1] != '-') for the no-patch
    exemption. Returns (kind, saw_suppress, needs_sep_value): kind is 'patch' (a p/u/U/c option letter is
    reached before any value-taking letter), 'unknown' (an unrecognized option letter), or 'ok' (only
    recognized non-patch letters). Argument-aware: a value-taking letter (-L/-S/-G/-O/-o) consumes the REST
    of the token as its value, so scanning stops there, and needs_sep_value is True when that letter is the
    last char (its value is the NEXT token). 's' is the suppression flag."""
    saw_suppress = False
    n = len(tok)
    j = 1
    while j < n:
        ch = tok[j]
        if ch in ("p", "u", "U", "c"):
            return ("patch", saw_suppress, False)
        if ch in _SHOW_SHORT_VALUE_LETTERS:
            return ("ok", saw_suppress, j + 1 >= n)  # the rest of the token (or the next token) is its value
        if ch == "s":
            saw_suppress = True
            j += 1
            continue
        return ("unknown", saw_suppress, False)      # an unrecognized short option letter -> not exempt
    return ("ok", saw_suppress, False)


def _show_no_patch_exempt(args):
    """True when a 'git show' argument list (the tokens AFTER the 'show' subcommand) earns the cnsdif
    no-patch exemption: a genuine -s/--no-patch in a real option position, no patch-enabling option, and no
    unknown option, judged by ONE '--'-boundary / value-argument / short-cluster-aware pass over the option
    region (see the option sets above). Conservative: it errs toward NOT exempting (the segment stays a
    diff producer, so cnsdif still covers it)."""
    i = 0
    n = len(args)
    saw_suppress = False
    while i < n:
        tok = args[i]
        if tok in _DIFF_END_OF_OPTIONS:
            break  # every remaining token is an operand (a pathspec/ref), never a suppression flag
        if not tok.startswith("-") or tok == "-":
            i += 1
            continue  # a bare operand (a ref/commit) does not affect the exemption
        if tok in ("-s", "--no-patch"):
            saw_suppress = True
            i += 1
            continue
        if tok.startswith("--"):
            base = tok.split("=", 1)[0]
            if base in _SHOW_LONG_PATCH_EXACT or any(base.startswith(p) for p in _SHOW_LONG_PATCH_PREFIXES):
                return False                     # a patch-enabling long option anywhere in the region -> cover
            if base in _SHOW_LONG_REQ_VALUE:
                i += 1 if "=" in tok else 2      # skip a recognized required-value option's separated value
                continue
            if base in _SHOW_LONG_NONPATCH:
                i += 1
                continue
            return False                         # an unknown long option -> conservative, not exempt (cover)
        kind, sup, needs_value = _show_short_cluster_kind(tok)
        if kind in ("patch", "unknown"):
            return False
        saw_suppress = saw_suppress or sup
        i += 2 if needs_value else 1
    return saw_suppress


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

    Always a diff dump: diff, range-diff (they render a patch by default). 'show' renders a diff by default
    (git show <commit>) EXCEPT in the blob-read form git show <ref>:<path> / :<path>, which prints one
    file's contents at a revision with no diff and is therefore NOT a producer (see _show_blob_selector).
    Patch-flag gated: log, the plumbing producers diff-tree, diff-index, diff-files, and stash 'show' (they
    emit a listing by default and a patch only with -p/-u/--patch). stdout gated: format-patch (writes
    numbered files by default and dumps to the console only with --stdout). Gating the plumbing/format-patch/
    stash forms on their flag keeps the file-writing and name-only forms from a false positive."""
    sub, rest = _git_subcommand_rest(tokens)
    if sub in ("diff", "range-diff"):
        return True
    if sub == "show":
        # A blob selector (<ref>:<path> / :<path>) makes git show a file read, not a diff dump.
        if _show_blob_selector(rest):
            return False
        # -s / --no-patch SUPPRESSES the patch (git prints only the commit metadata and message, no diff),
        # the same non-diff class as the --stat/--name-only summary forms cnsdif already allows. The
        # exemption is granted only by a position/argument/cluster-aware pass (_show_no_patch_exempt over the
        # show ARGS): a genuine suppression flag in a real option position, no patch-enabling option
        # (-p/-u/--patch*/-U/--unified/-c/--cc or a p/u short cluster), and no unknown option. A suppression
        # token after '--' (a pathspec) or consumed as a value-taking option's argument (git show -S -s) does
        # NOT earn it, and any patch enabler re-enables the diff (git show -s -p). cleanlanguage adopter
        # report 2026-09-12; F-R2-2/F-R2-3 hardening 2026-09-12.
        if _show_no_patch_exempt(rest):
            return False
        return True
    if sub in ("log", "diff-tree", "diff-index", "diff-files"):
        return _has_patch_flag(tokens)
    if sub == "format-patch":
        return "--stdout" in tokens
    if sub == "stash":
        return "show" in tokens and _has_patch_flag(tokens)
    return False


def _has_info_flag(tokens):
    """True when a segment carries an info flag (--help or -h) as a GENUINE help invocation: git would
    show the subcommand's manual rather than run it, so the segment renders no diff and lands no push or
    commit. It receives the shell-CLEANED argv (the shared _lex_command has already removed shell
    redirection), so a --help/-h that was a redirect TARGET is no longer in the token stream at all; the
    two remaining git-argument-parsing cases (F-117) still apply:
      - END-OF-OPTIONS: after a '--' or '--end-of-options' token every argument is positional (a pathspec or refspec), so a
        --help/-h at or after the first '--' is NOT help (git runs the command); only earlier tokens
        are considered.
      - VALUE / OPTION SLOT: a --help/-h that is the VALUE of a preceding separated option
        (git commit -m --help, git push -o --help --force) is that option's argument, not a help flag,
        so a token whose PREDECESSOR starts with '-' does not count.
    An info flag counts only when its predecessor is a plain word (the subcommand, an operand, or a
    consumed value), exactly where git treats it as help. Fail-safe by construction: every 'git actually
    runs it' shape is excluded, and incompleteness of any value-option list can never cause a silent
    allow. The redirect-target exclusion (a predecessor made only of the characters '<>&|') is retained as
    a harmless belt-and-braces guard, though the tokenizer no longer surfaces a redirect operator here. The
    safe-direction residual is an over-ask/over-deny on ANY complete option placed immediately before help
    (a valueless flag, git commit --amend --help, or an attached-value flag, git commit -mfoo --help / git
    push -ofoo --help --force), where git shows help but the preceding '-' token makes this treat the
    segment as live; recoverable, re-issue as 'git <subcommand> --help'."""
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


def _token_possible_producer(argv):
    """True when the cleaned argv carries a 'git' word (by basename, so /usr/bin/git counts) followed LATER
    by a producer-capable surface (diff/show/range-diff/log/diff-tree/diff-index/diff-files/format-patch;
    'show' also covers 'git stash show'). This is the token half of the possible-producer scope: it catches
    a quote-fragmented producer (g'it' d'iff' -> git diff) that a raw regex over the still-quoted text
    cannot see."""
    seen_git = False
    for word in argv:
        base = word.rsplit("/", 1)[-1]
        if seen_git and base in _PRODUCER_SURFACES:
            return True
        if base == "git":
            seen_git = True
    return False


def _has_producer_surface(argv):
    """True when any argv word (by basename) is a producer surface. Used with an OPAQUE segment to widen
    the possible-producer scope, so a quoting/expansion form that could resolve to a git command word (an
    ANSI-C $'g'it, a $VAR that expands to git) alongside a producer surface is never silently ALLOWED."""
    return any(word.rsplit("/", 1)[-1] in _PRODUCER_SURFACES for word in argv)


def _is_possible_producer(seg):
    """A POSSIBLE producer (the fail-safe ASK scope): a git word followed by a producer surface, seen either
    in the segment's cleaned argv (_token_possible_producer, robust to quote fragmentation) OR by a broad
    raw-text probe over the segment (which over-matches quoted prose such as echo 'git diff' - a deliberate
    over-ASK, never an over-ALLOW), OR an OPAQUE segment (one carrying an unquoted expansion, substitution,
    or ANSI-C/other quoting the lexer cannot resolve to a literal command word) that also carries a producer
    surface, so $'g'it diff (which bash runs as git diff) cannot slip through as no-producer. It never
    establishes an ALLOW; it only widens the ASK/DENY scope."""
    return (_token_possible_producer(seg.argv)
            or bool(_RAW_DIFF_PRODUCER_RE.search(seg.raw))
            or (seg.opaque_shell and _has_producer_surface(seg.argv)))


# git diff/log write the diff to a --output/-o file instead of stdout, so a shell redirect or a pipe is a
# DECOY when --output is present (proofs C and D must not apply). --output/-o takes a file value, inline
# (--output=X / -oX) or separated (--output X / -o X).
def _diff_output_diversion(argv):
    """Return (present, target) for a git --output/-o diff-output diversion before any '--' boundary, or
    (False, None). The target decides console vs file: the diff lands there, not on stdout."""
    end = len(argv)
    for j, word in enumerate(argv):
        if word in _DIFF_END_OF_OPTIONS:
            end = j
            break
    i = 0
    while i < end:
        word = argv[i]
        if word in ("--output", "-o"):
            return True, (argv[i + 1] if i + 1 < len(argv) else None)
        if word.startswith("--output="):
            return True, word[len("--output="):]
        if word.startswith("-o") and not word.startswith("--") and len(word) > 2:
            return True, word[2:]
        i += 1
    return False, None


def _final_stdout_dest(redirects):
    """The classification of the segment's FINAL stdout-affecting redirect (last-redirect-wins), or None
    when no redirect touches stdout: 'file-real' a static non-/dev,/proc path, 'file-dev' a /dev,/proc
    console/descriptor path, 'descriptor' an fd duplication (e.g. 1>&2, unprovable as a file), or 'opaque'
    a dynamic target."""
    dest = None
    for red in redirects:
        if red.stdout_effect:
            dest = red.stdout_effect
    return dest


# Patch-ENABLING short option letters the summary classifier recognizes: -p/-u and -U (unified). It
# deliberately EXCLUDES the combined-diff letters -c/-cc, which emit a patch ONLY on a MERGE commit and are
# NOPATCH on the ordinary (non-merge) commit the classifier sees; treating them as enablers would over-deny
# the common `git show -c --stat` / `git show --cc --stat` summary form (verified NOPATCH against real git,
# F-R2-5), so they stay summary-only allow-notes. The merge-commit --cc/-c-with-summary patch is a disclosed
# residual (the guard cannot see whether the ref is a merge), not chased here. -U is an enabler whatever its
# attached value (`-U`, `-U3`); git rejects the separated `-U 3` form outright, so no separated value is skipped.
_SUMMARY_PATCH_SHORT = frozenset(("p", "u", "U"))


def _argv_has_patch_enabler(argv):
    """Argument-aware: True when a git producer's OPTION region (before a '--'/'--end-of-options' boundary)
    carries a patch-ENABLING option - one that makes git emit a patch even alongside a summary selector.
    Enablers: -p/-u, -U/--unified, the --patch* long family, and any short-option CLUSTER reaching a p/u/U
    letter. It reuses the same option sets and value-argument/'--'-boundary parsing as the git-show no-patch
    exemption (mirroring _show_no_patch_exempt / _show_short_cluster_kind): a value-taking option
    (-L/-S/-G/-O/-o, and the separated-value long options _SHOW_LONG_REQ_VALUE) consumes its value, so a
    p/u/U spelled inside that value is not miscounted, and the '--' boundary ends the option region. It
    narrows the short patch-letter set to _SUMMARY_PATCH_SHORT (p/u/U, excluding the merge-only -c letter;
    see that constant), and the long enablers to the --patch*/--unified* prefixes (excluding --cc, the
    merge-only combined-diff form). Any other (unknown) option is not a patch enabler; this predicate
    answers only 'is a patch forced on', leaving every other classification to the caller (F-R2-5)."""
    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok in _DIFF_END_OF_OPTIONS:
            break  # every remaining token is an operand (a pathspec/ref), never an option
        if not tok.startswith("-") or tok == "-":
            i += 1
            continue  # a bare operand (git, the subcommand, a ref) is not an option
        if tok.startswith("--"):
            base = tok.split("=", 1)[0]
            if any(base.startswith(p) for p in _SHOW_LONG_PATCH_PREFIXES):
                return True                      # --patch* / --unified* long enabler
            if base in _SHOW_LONG_REQ_VALUE:
                i += 1 if "=" in tok else 2      # skip a recognized required-value option's separated value
                continue
            i += 1                               # any other long option is not a summary patch enabler
            continue
        # a short cluster: scan its letters, honouring a value-taking letter that consumes the rest/next token
        j = 1
        m = len(tok)
        consumes_next = False
        found = False
        while j < m:
            ch = tok[j]
            if ch in _SUMMARY_PATCH_SHORT:
                found = True
                break
            if ch in _SHOW_SHORT_VALUE_LETTERS:
                consumes_next = j + 1 >= m       # a value letter as the LAST char takes the NEXT token
                break                            # the rest of this token is that letter's value
            j += 1
        if found:
            return True
        i += 2 if consumes_next else 1
    return False


def _diff_emits_only_summary(argv):
    """Role-aware: True when a producer's OPTION region (before a '--'/'--end-of-options' boundary) carries
    a summary selector (--stat/--name-only/--name-status/--numstat/--shortstat, bare or '=value') and NO
    patch enabler. Distinguishes a genuine summary listing (git diff -M --stat, which is not a console patch
    dump and so ASKS rather than DENIES) from a summary token in a NON-option position (git diff -- --stat, a
    pathspec: a real dump) and from a summary with a co-present patch enabler (git diff --stat -p, or the
    argument-aware -U/--unified and p/u/U-cluster forms, still a full patch dump). Patch-enabler recognition
    is argument-aware via _argv_has_patch_enabler, so -U/--unified and a p/u short cluster (git show -s --stat
    -U3 / --unified=3 / -sp) are no longer mistaken for summary-only (F-R2-5)."""
    has_summary = False
    for word in argv:
        if word in _DIFF_END_OF_OPTIONS:
            break
        if word.split("=", 1)[0] in _SUMMARY_FLAGS:
            has_summary = True
    return has_summary and not _argv_has_patch_enabler(argv)


def _seg_stdout_reaches_console(seg, segments, index):
    """Where a producer segment's stdout goes: 'file' (a static real file, NOT a console dump), 'console' (a
    /dev,/proc target, or a pipe into a known console/truncating sink cat/tee/head/tail, or a plain producer
    with no diversion), or 'unprovable' (a descriptor or dynamic redirect target, a both-streams '|&' pipe,
    or a pipe into a pager or any other command whose downstream this guard does not follow)."""
    dest = _final_stdout_dest(seg.redirects)
    if dest == "file-real":
        return "file"
    if dest in ("descriptor", "opaque"):
        return "unprovable"
    if seg.sep_after == "|":
        nxt = index + 1
        word = _command_word(segments[nxt].argv) if nxt < len(segments) else ""
        return "console" if word in _CONSOLE_SINKS else "unprovable"
    if seg.sep_after == "|&":
        return "unprovable"
    if dest == "file-dev":
        return "console"
    return "console"  # no stdout redirect and no pipe: straight to the console


def _is_console_dump(seg, segments, index):
    """True when a producer segment is CONFIRMED to emit a console patch: its command word is PROVABLY git
    (a literal 'git' or a path to it, so an opaque/expansion command word this guard cannot resolve is never
    a confirmed dump - it routes to ASK instead), it is a git diff producer, it does not emit only a summary
    listing, and its output is proven to reach a console. When the producer diverts its output with
    --output/-o, THAT target decides (a /dev,/proc console target is a dump; a file or dynamic target is not
    a confirmed dump, so it ASKS) and any shell redirect or pipe is a decoy. Otherwise stdout governs. Used
    only to choose DENY over ASK; its absence never establishes an ALLOW."""
    if _command_word(seg.argv) != "git":
        return False  # an opaque/wrapped command word is not a proven git dump -> ASK, never DENY
    if not _is_diff_producer(seg.argv):
        return False
    if _diff_emits_only_summary(seg.argv):
        return False
    diverted, out_target = _diff_output_diversion(seg.argv)
    if diverted:
        # the diff is written to the --output target, not stdout; the shell redirect/pipe is a decoy
        return out_target is not None and _target_class(out_target, False) == "file-dev"
    return _seg_stdout_reaches_console(seg, segments, index) == "console"


def _diff_proof_help(command):
    """Proof A: an EXACT metacharacter-free help invocation. Allows only 'git <producer> --help/-h' (or the
    exact 'git stash show --help/-h'); any extra option, operand, wrapper, path, quote, or shell syntax
    fails. A --help that reached this command as an option value, a path, or a redirect target cannot form
    this exact shape (the metacharacter-free requirement rules out a redirect, and an option value/path
    changes the exact word list)."""
    if not _DIFF_PLAIN_RE.fullmatch(command):
        return False
    words = command.split()
    if words[:1] != ["git"]:
        return False
    if len(words) == 3 and words[1] in _PRODUCER_SURFACES and words[2] in _INFO_FLAGS:
        return True
    return len(words) == 4 and words[1:3] == ["stash", "show"] and words[3] in _INFO_FLAGS


def _diff_proof_summary(command):
    """Proof B: an EXACT summary-only command. Requires a metacharacter-free single simple command (the
    conservative charset, no shell reserved word), the first resolved words literally 'git' and a
    summary-capable producer ('git stash show' handled explicitly), at least one exact summary selector
    before a '--'/'--end-of-options' boundary, and no other option-like word before the boundary except an
    exact --no-patch (bare operands are allowed). Any extra option (git diff -M --stat, git diff --stat=80),
    a wrapper, a path, an assignment, or a quote fails, routing to ASK rather than another growing option
    table."""
    if not _DIFF_PLAIN_RE.fullmatch(command):
        return False
    words = command.split()
    if _DIFF_RESERVED_WORDS & set(words):
        return False
    if words[:1] != ["git"]:
        return False
    if words[1:2] == ["stash"]:
        if words[2:3] != ["show"]:
            return False
        opts = words[3:]
    elif len(words) >= 2 and words[1] in _SUMMARY_PRODUCERS:
        opts = words[2:]
    else:
        return False
    boundary = len(opts)
    for j, word in enumerate(opts):
        if word in _DIFF_END_OF_OPTIONS:
            boundary = j
            break
    has_selector = False
    for word in opts[:boundary]:
        if word in _SUMMARY_FLAGS:
            has_selector = True
        elif word.startswith("-") and word != "--no-patch":
            return False
    return has_selector


def _diff_proof_log_listing(command):
    """Proof E (Architect-directed refinement): a benign 'git log' COMMIT LISTING that provably emits no diff
    to the console. Requires a metacharacter-free single simple command (the conservative charset, no shell
    reserved word, so no redirect and no pipe), the first resolved words literally 'git' and 'log', and every
    option-like word before a '--'/'--end-of-options' boundary drawn from the exact _BENIGN_LOG_OPTS
    allowlist (bare revision/pathspec operands are allowed, and an option there is valueless so no operand is
    a swallowed value). Any patch flag, pickaxe, value-taking option, or unknown flag fails this proof and
    routes to the unchanged default (ASK, or DENY on a confirmed patch). This is EXACT-form, not a fuzzy
    'anything without -p' allow: an unknown flag is never proven benign."""
    if not _DIFF_PLAIN_RE.fullmatch(command):
        return False
    words = command.split()
    if _DIFF_RESERVED_WORDS & set(words):
        return False
    if words[:2] != ["git", "log"]:
        return False
    opts = words[2:]
    boundary = len(opts)
    for j, word in enumerate(opts):
        if word in _DIFF_END_OF_OPTIONS:
            boundary = j
            break
    for word in opts[:boundary]:
        if word.startswith("-") and word not in _BENIGN_LOG_OPTS:
            return False
    return True


def _diff_proof_show_blob(command):
    """Proof F: a 'git show' BLOB READ, admitted ONLY when EVERY non-option operand after 'show' is a
    blob selector (a '<ref>:<path>' or ':<path>' form, per _show_blob_selector). In that form git show
    prints each named file's contents at a revision - no diff, no @@ hunk headers, no +/- lines - so it is
    `cat` against a revision, the normal way to read a file on an un-checked-out branch, and renders no
    console diff. Requires a metacharacter-free single simple command (the conservative charset, no shell
    reserved word, so no redirect and no pipe) and the first resolved words literally 'git' and 'show'.
    If ANY non-option operand is a bare ref/commit (no colon) or a ':/<text>' commit-message SEARCH, git
    renders a full commit diff, so the command is NOT this proof and stays covered (ASK, or DENY on a
    confirmed patch) exactly as a bare 'git show <commit>' does; 'git show HEAD:a HEAD' and 'git show :/x'
    are therefore not admitted. Residual (disclose-guard-residuals): a colon-bearing separated option VALUE
    standing as the sole apparent operand (e.g. 'git show -O opt:val' with no object) could still be misread
    as a blob selector; this is a SAFE-DIRECTION over-allow of at worst a small console dump for this quality
    guard, never an under-read."""
    if not _DIFF_PLAIN_RE.fullmatch(command):
        return False
    words = command.split()
    if _DIFF_RESERVED_WORDS & set(words):
        return False
    if words[:2] != ["git", "show"]:
        return False
    return _show_blob_selector(words[2:])


def _diff_proof_realfile(segments):
    """Proof C: a single simple command whose command word is literally 'git', a possible producer, with no
    opaque shell feature, whose FINAL stdout redirect (last-redirect-wins over the raw redirect metadata) is
    proven to land on a static non-/dev,/proc file. Supports leading, interspersed, and trailing redirects
    without token pollution; a dynamic target, a descriptor duplication, or a /dev,/proc target is not a
    proof."""
    if len(segments) != 1:
        return False
    seg = segments[0]
    if seg.opaque_shell:
        return False
    if not seg.argv or seg.argv[0] != "git":  # literal 'git': no assignment prefix, wrapper, or path
        return False
    if not _is_possible_producer(seg):
        return False
    if _diff_output_diversion(seg.argv)[0]:
        return False  # --output/-o governs where the diff lands; the shell redirect is a decoy
    return _final_stdout_dest(seg.redirects) == "file-real"


def _diff_proof_pager(segments):
    """Proof D: an EXACT two-stage terminal pager pipeline. Stage 1 is a plain literal unwrapped 'git'
    possible producer with no redirection or opaque feature; the separator is exactly '|' (not '|&'); stage
    2 is exactly one command word (less/more/most/pager) with no options, operands, redirection, or opaque
    feature, and it is the terminal stage (no later pipe)."""
    if len(segments) != 2:
        return False
    stage1, stage2 = segments
    if stage1.sep_after != "|" or stage2.sep_after != "":
        return False
    if stage1.redirects or stage1.opaque_shell:
        return False
    if not stage1.argv or stage1.argv[0] != "git" or not _is_possible_producer(stage1):
        return False
    if _diff_output_diversion(stage1.argv)[0]:
        return False  # --output/-o diverts the diff off the pipe; the pager is a decoy
    if stage2.redirects or stage2.opaque_shell:
        return False
    return len(stage2.argv) == 1 and stage2.argv[0] in _PAGERS


def _diff_source_fallback(command):
    """FAIL-SAFE conservative check when _lex_command cannot parse the command (an unbalanced quote or an
    unsupported construct such as a heredoc or process substitution): an apparent git diff-producer (the
    broad raw probe) ASKS, everything else ALLOWS. An unparseable command can never earn an ALLOW from a
    summary or redirect regex - the old raw-summary/redirect escapes are removed - so a plausibly-dumping
    unparseable command is surfaced for confirmation rather than proven either way."""
    if _RAW_DIFF_PRODUCER_RE.search(command):
        return _allow_note(
            "AIQT guardrail (rule cnsdif, no-console-diff-dumps): the shared tokenizer could not parse this "
            "command and it appears to invoke a git diff-producer. This is a console-readability convention, "
            "not a hazard, so the command is allowed. If it renders a diff to the console, prefer a summary "
            "form (--stat, --name-only), a redirect to a real file (> file), or a pager (| less) to keep the "
            "review surface readable.")
    return _allow()


def diff_source_pretool(data):
    """cnsdif (trust/no-console-diff-dumps), PreToolUse/Bash. FAIL-SAFE-BY-CONSTRUCTION: a git diff-producer
    is ALLOWED only when the WHOLE command matches one of six closed proofs (exact help, exact summary, a
    benign 'git log' commit listing, an exact 'git show <ref>:<path>' blob read, a single simple command
    proven to redirect stdout to a real file, or an exact terminal pager pipeline);
    a producer confirmed to emit a console patch and fitting no proof DENIES-and-educates (use --stat or a
    redirect/pager). NO-ASK posture: any other producer-capable but unproven form (a wrapper, pathed git,
    quotes, a compound, an extra option, a dynamic redirect, a non-pager pipe) is a console-readability
    CONVENTION, not a hazard, so it ALLOWS with an informational note rather than prompting. Across a compound
    or multiple producers, a CONFIRMED console dump anywhere still DENIES (deny outranks the allow-note), so a
    multi-command form is never admitted on one safe segment when another is a confirmed dump."""
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
        segments = _lex_command(command)
    except ValueError:
        return _diff_source_fallback(command)
    possibles = [(index, seg) for index, seg in enumerate(segments) if _is_possible_producer(seg)]
    if not possibles:
        return _allow()  # no producer-capable form anywhere: the bounded true boundary allows
    # AIRTIGHT-NARROW ALLOW: only when the WHOLE command is one of the five closed proofs.
    if (_diff_proof_help(command) or _diff_proof_summary(command)
            or _diff_proof_log_listing(command) or _diff_proof_show_blob(command)
            or _diff_proof_realfile(segments) or _diff_proof_pager(segments)):
        return _allow()
    # Otherwise judge each possible producer: a confirmed console dump DENIES (outranking ASK across a
    # compound), and any remaining producer-capable-but-unproven form ASKS.
    for index, seg in possibles:
        if _is_console_dump(seg, segments, index):
            return _deny(
                "AIQT rule cnsdif (no-console-diff-dumps): this command renders a version-control diff to "
                "the console, burying the review surface under a raw dump. Use a summary form (--stat, "
                "--name-only, --name-status, --numstat), redirect the diff to a real file (> file), or "
                "pipe it into a pager (| less), not the console.",
                "AIQT guardrail: denied a bare console diff dump (rule cnsdif).")
    return _allow_note(
        "AIQT guardrail (rule cnsdif, no-console-diff-dumps): this command is producer-capable (it can "
        "render a version-control diff) but is outside the guard's proven-safe forms - a wrapper, a pathed "
        "git, quotes, a compound, an extra option, a dynamic redirect target, or a pipe to a non-pager. "
        "Burying the review surface is a convention, not a hazard, so it is allowed (a bare console dump the "
        "guard CAN confirm is still denied above). If it dumps a diff to the console, prefer a summary form "
        "(--stat), a redirect to a real file (> file), or an exact pager pipeline (git diff | less).")


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
# Fallback-only raw-string contexts, used when the shared tokenizer cannot parse the command (see
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
    """FAIL-SAFE conservative scan when the shared tokenizer cannot parse the command (an unbalanced quote or an unsupported construct): scan the RAW
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
# Read, Write, and Edit require an absolute file_path, and NotebookEdit an absolute notebook_path, by the
# tool's own contract, so the rule's relative-to-a-named-root carve-out never applies to them. MultiEdit is
# kept on the same file_path wire for LEGACY COMPATIBILITY only: it is NOT in the current Claude Code
# built-in tool index, so its mapping is retained solely to cover an adopter on an older or re-enabled
# MultiEdit, never as an assertion that MultiEdit is a current/live tool. Glob and Grep are honoured under
# the carve-out: their `pattern` is legitimately relative to a named search root and is never judged, but
# their optional `path` search root should be absolute. Each field name is bound to a Claude Code tool
# schema (Read/Write/Edit -> file_path, NotebookEdit -> notebook_path, Glob/Grep -> optional path), with
# MultiEdit -> file_path the legacy-compat wire above, never assumed.
FILE_PATH_TOOLS = ("Read", "Write", "Edit", "MultiEdit")   # contract-absolute `file_path` (MultiEdit: legacy-compat wire)
NOTEBOOK_PATH_TOOLS = ("NotebookEdit",)                    # contract-absolute `notebook_path`
SEARCH_ROOT_TOOLS = ("Glob", "Grep")                       # optional `path` search root; carve-out


def _is_absolute(path):
    """Absolute under the runtime's own path semantics, not a lexical drive-letter guess: a POSIX
    absolute path (PurePosixPath.is_absolute) OR a Windows path carrying BOTH a drive and a root
    (PureWindowsPath.is_absolute), so a UNC \\\\server\\share and a drive-absolute C:\\ / C:/ count, while
    a drive-RELATIVE 'C:file', a bare 'C:', and a rooted-but-driveless '\\file' are correctly NOT
    absolute (each still resolves against a current directory, or a per-drive current directory, on
    Windows). A '~'-prefixed, empty, or otherwise relative path is likewise not absolute: these tools do
    not expand '~', so it would resolve against a literal '~' directory. Deferring to the stdlib
    predicate closes the drive-relative false positives the old lexical `^[A-Za-z]:` / leading-backslash
    union accepted (C:file, C:, \\file all read as absolute there)."""
    return pathlib.PurePosixPath(path).is_absolute() or pathlib.PureWindowsPath(path).is_absolute()


def _deny_relative(tool, field, value):
    reason = ("AIQT rule abspth (absolute-paths): {} requires an absolute {}; got {!r}. The working "
              "directory can silently differ between tool calls, so re-issue the call with the full "
              "absolute path.".format(tool, field, value))
    return _deny(reason,
                 "AIQT guardrail: denied a relative {} where an absolute one is required "
                 "(rule abspth).".format(field))


def _abspth_check_required(tool, field, value):
    """A field the tool's contract requires to be absolute (Read/Write/Edit/MultiEdit file_path,
    NotebookEdit notebook_path): fail closed when it is absent or not a non-empty string, allow when it
    is absolute, deny when it is relative."""
    if not isinstance(value, str) or not value:
        return _deny(
            "AIQT rule abspth (absolute-paths): the {} payload carried no readable {}, so the "
            "absolute-path check could not run; failing closed.".format(tool, field),
            "AIQT guardrail: denied a {} call with no readable {} (rule abspth, fail-closed)."
            .format(tool, field))
    if _is_absolute(value):
        return _allow()
    return _deny_relative(tool, field, value)


def _abspth_check_search_root(tool, path):
    """The optional `path` search root of Glob/Grep. Carve-out: a rootless call (no path) names the
    current directory as the root against which the relative pattern is legitimately judged, so allow;
    a present search root should be absolute, and a present-but-unreadable one fails closed."""
    if path is None:
        return _allow()  # rootless: the pattern's named root is the current directory (carve-out)
    if not isinstance(path, str) or not path:
        return _deny(
            "AIQT rule abspth (absolute-paths): the {} payload carried an unreadable path search root, "
            "so the absolute-path check could not run; failing closed.".format(tool),
            "AIQT guardrail: denied a {} call with an unreadable path (rule abspth, fail-closed)."
            .format(tool))
    if _is_absolute(path):
        return _allow()
    return _deny_relative(tool, "path search root", path)


def absolute_paths(data):
    """abspth (quali/absolute-paths), PreToolUse on Read|Write|Edit|MultiEdit|NotebookEdit|Glob|Grep:
    deny a relative path where the tool's contract requires absolute, honouring the rule's carve-out for
    the relative search pattern of Glob and Grep."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: absolute_paths wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool = data.get("tool_name")
    if tool is None:
        return _deny_missing_tool_name("abspth")
    in_scope = tool in FILE_PATH_TOOLS or tool in NOTEBOOK_PATH_TOOLS or tool in SEARCH_ROOT_TOOLS
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        # A falsy or malformed tool_input previously collapsed to {} and let the search-root branch take
        # its allow path (fail-open). Require a mapping first: for any in-scope tool a payload that is not
        # a readable mapping fails closed; an out-of-scope tool (the matcher governs) allows.
        if in_scope:
            return _deny(
                "AIQT rule abspth (absolute-paths): the {} payload was not a readable mapping, so the "
                "absolute-path check could not run; failing closed.".format(tool),
                "AIQT guardrail: denied a {} call with an unreadable tool_input (rule abspth, "
                "fail-closed).".format(tool))
        return _allow()
    if tool in FILE_PATH_TOOLS:
        return _abspth_check_required(tool, "file_path", tool_input.get("file_path"))
    if tool in NOTEBOOK_PATH_TOOLS:
        return _abspth_check_required(tool, "notebook_path", tool_input.get("notebook_path"))
    if tool in SEARCH_ROOT_TOOLS:
        return _abspth_check_search_root(tool, tool_input.get("path"))
    return _allow()  # a present-but-different tool is out of scope (defensive; the matcher governs)


# --- abspth (Bash floor): a relative cd/pushd or redirect target resolved against the cwd ------------
# A SEPARATE PreToolUse linkage of the same rule onto Bash, reusing the shared quote/redirect-aware
# lexer (_lex_command / _command_word). CONSERVATIVE FLOOR, ASK-defaulting: it judges only two positions
# that silently depend on the current directory - a cd/pushd DESTINATION operand, and a redirection
# TARGET - and NEVER an arbitrary command operand (a relative argument to some other command is not
# judged, to avoid over-firing). A relative or unresolvable such position ASKS; an absolute position, and
# a command carrying neither, allow. It never denies: the human confirming is the opt-out.
_CD_BUILTINS = frozenset(("cd", "pushd"))
# ROUND-2 FINDING 10: redirect operators that TRUNCATE (zero) their file target on open. A truncating
# redirect to a relative/opaque target can destroy the WRONG file if the ambient cwd differs, so abspth
# denies it. The append forms ('>>', '&>>') and the read/read-write forms ('<', '<>', '<&') do NOT truncate
# and stay a convention nudge; '>&' is included only for its csh both-streams-TO-A-FILE form (a descriptor
# target is skipped before this set is consulted).
_ABSPTH_TRUNCATING_OPS = frozenset((">", ">|", "&>", ">&"))
# cd/pushd option tokens that name NO destination path: the real option flags (cd -L/-P/-e/-@, pushd -n,
# and bundled combos like -LP), which are a limited closed set, NOT any '-'/'+'-prefixed token. A lone '-'
# is cd's $OLDPWD previous-directory shortcut (an absolute prior dir, cwd-independent); '--' ENDS option
# processing so the NEXT token is the destination even when it begins '-'/'+'; and pushd's +N/-N is a
# numeric directory-stack rotation. A '+relative' or '-relative' that is NOT one of these is a real
# relative directory NAME (the destination), matching the comment 'the first token that is not an option'.
_CD_OPT_BUNDLE_RE = re.compile(r"^-[LPe@n]+$")  # cd -L/-P/-e/-@ and pushd -n, singly or bundled (-LP, -nL)
_PUSHD_ROTATION_RE = re.compile(r"^[-+][0-9]+$")  # pushd +N/-N: a stack index, not a filesystem path
# An UNQUOTED CURRENT-USER tilde destination (~ or ~/path) is tilde-EXPANDED by the shell to $HOME, an
# absolute directory, so it is cwd-independent exactly like an absolute path. This is NARROWED to the
# current-user forms only: a '~user'/'~user/path' names another account's home whose EXISTENCE the hook
# cannot verify (an unresolved login name leaves the word RELATIVE), so it is NOT proven absolute here and
# ASKS; and the '~-'/'~+'/'~N' directory-stack forms likewise ASK. A QUOTED '~' (a literal directory named
# '~') is not expanded and stays relative; the LEADING-UNQUOTED-tilde flag (argv_leading_tilde), not the
# broader opacity flag, tells the expanded form from a quoted one whose unquoted tail merely carries opacity.
_LITERAL_TILDE_RE = re.compile(r"^~(/.*)?$")
# CONSERVATIVE CONSOLIDATION (round-4). Any inline env-assignment PREFIXING a cd/pushd command
# ('OLDPWD=.. cd -', 'HOME=.. cd', and the append forms 'OLDPWD+=.. cd -'/'HOME+=.. cd') can redirect where
# the cwd-INDEPENDENT destinations land: an inline OLDPWD= redirects the lone '-' ($OLDPWD) shortcut, and an
# inline HOME= redirects the bare 'cd' (no operand -> $HOME) default, in each case to a value the floor
# cannot prove absolute ('..'). Rather than enumerate OLDPWD/HOME by name (fragile: it misses the '+='
# append form, and the earlier OLDPWD-only check never covered the bare-cd HOME default), the floor takes
# the conservative stance that ANY leading assignment (any name, '=' or '+=', and even an assignment-SHAPED
# token bash would reject because its name was quoted) makes those two otherwise-cwd-independent
# destinations ASK. A plain zero-assignment 'cd -'/'cd -- -'/bare 'cd' keeps its ALLOW. The consequence
# 'FOO=x cd -' -> ASK is a deliberate conservative over-fire, disclosed in the manifest residue.


def _cd_destination_reason(word, argv, argv_leading_tilde):
    """For a cd/pushd segment, return a reason string when its destination operand is relative or
    unresolvable (opaque), else None: an absolute destination, an unquoted current-user tilde one ('~' or
    '~/path', which expands to $HOME), the OLDPWD 'cd -', or no destination at all (cd -> HOME, pushd -> swap
    the stack top), is cwd-independent here. Only the FIRST non-option token is the destination; the real
    cd/pushd option flags and a pushd +N/-N rotation name no relative path and are skipped, while '--' ends
    option processing so the next token is the destination even when it begins '-'/'+'. A lone '-' is the
    $OLDPWD shortcut wherever it lands as the destination, including as the post-'--' destination ('cd -- -',
    which bash still resolves to $OLDPWD), and a NO-operand cd/pushd is the $HOME/stack-swap default; both are
    cwd-independent UNLESS the command carries ANY leading inline env-assignment ('OLDPWD=.. cd -- -',
    'HOME=.. cd', 'OLDPWD+=.. cd -'), which can redirect the destination to a value the floor cannot prove
    absolute, so those two forms then ASK. This is the conservative consolidation (round-4): the trigger is
    the mere PRESENCE of a leading assignment, not its variable name, so the '+=' append form and the bare-cd
    HOME default are covered without name enumeration. An operand carrying an unexpanded expansion never reads
    as absolute unless it is literally rooted (a leading '/'), so an opaque 'cd $DIR' naturally ASKS while an
    absolute '/$X/y' allows. argv_leading_tilde is the per-token LEADING-UNQUOTED-tilde flags parallel to argv
    (True only where the word's leading char is an unquoted tilde, so tilde-expansion applies)."""
    cmd_idx = _command_word_index(argv)  # boundary: argv[:cmd_idx] are the leading env-assignments
    # ANY leading assignment (any name, '=' or '+=') can redirect the cwd-independent destinations ('cd -',
    # bare 'cd'), so it can no longer be trusted; presence, not the variable name, is the trigger.
    has_leading_assignment = cmd_idx > 0
    idx = cmd_idx + 1  # skip leading env-assignments and the command word itself
    end_of_options = False
    for pos in range(idx, len(argv)):
        tok = argv[pos]
        if not end_of_options:
            if tok == "--":
                end_of_options = True  # everything after '--' is an operand, not an option
                continue
            if _CD_OPT_BUNDLE_RE.match(tok):
                continue  # a cd/pushd option flag or bundle (-L/-P/-e/-@/-n): not a destination path
            if word == "pushd" and _PUSHD_ROTATION_RE.match(tok):
                continue  # pushd +N/-N: a directory-stack rotation index, not a filesystem path
        # end-of-options, or the first token that is not an option: this is the destination operand.
        if tok == "-":
            if has_leading_assignment:
                # a leading inline env-assignment can redirect $OLDPWD to a value the floor cannot prove abs
                return ("a {} destination '-' that a leading inline env-assignment can redirect off $OLDPWD"
                        .format(word))
            continue  # cd '-': the $OLDPWD previous-directory shortcut (an absolute prior dir), even
            # after '--' where bash still treats a lone '-' destination as $OLDPWD, not a relative name
        if argv_leading_tilde[pos] and _LITERAL_TILDE_RE.match(tok):
            return None  # an unquoted current-user tilde ('~', '~/x') expands to $HOME: cwd-independent
        if _is_absolute(tok):
            return None
        return "a relative {} destination {!r}".format(word, tok)
    # no destination operand: cd -> HOME, pushd -> stack swap, cwd-independent UNLESS a leading inline
    # env-assignment (e.g. HOME=..) can redirect that default to a value the floor cannot prove absolute.
    if has_leading_assignment:
        return ("a bare {} (no operand) whose $HOME default a leading inline env-assignment can redirect"
                .format(word))
    return None


def bash_absolute_paths(data):
    """abspth (quali/absolute-paths) Bash floor, PreToolUse on Bash: NO-ASK posture. Where a Bash command
    shifts the working directory through a relative cd/pushd operand, or names a relative/opaque
    NON-destructive redirection target (an append '>>', a read '<'/'<>', or a relative cd - each resolves
    against a working directory that can silently differ between calls), it ALLOWS with an informational
    note: those are a convention, not a hazard, so the floor never asks on them. The ONE exception
    (round-2 finding 10): a DESTRUCTIVE TRUNCATING redirect ('>', '>|', '&>', or a '>&' to a file) to a
    RELATIVE or OPAQUE target DENIES-and-educates - truncation ZEROES its target, so aiming it at a
    cwd-dependent relative/unverified path can destroy the WRONG file, the exact data-loss the absolute-paths
    rule prevents. It never asks, and it does not judge an arbitrary command operand."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: bash_absolute_paths wired to unexpected event {!r}; failing "
                           "closed".format(data.get("hook_event_name")))
    tool = data.get("tool_name")
    if tool is None:
        return _deny_missing_tool_name("abspth")
    if tool != "Bash":
        return _allow()  # a present-but-different tool is out of scope (defensive; the matcher governs)
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        # Cannot read the command to resolve either position: allow with a note (absolute-paths is a
        # convention nudge, not a hazard; hooks never ask).
        return _allow_note(
            "AIQT guardrail (rule abspth, absolute-paths): the Bash payload carried no readable command "
            "string, so the relative-cwd check could not run. Allowing; prefer absolute paths so a command "
            "cannot resolve against a working directory that differs between calls.")
    # Partial lex so an unparseable construct (a heredoc, here-string, process substitution, {fd} redirect,
    # or an unbalanced quote or escape) no longer discards every segment before it: a resolvable relative
    # cd/redirect in the parseable PREFIX still ASKS. Only a cd/redirect position WITHIN or AFTER the
    # unparseable construct is left to the human's own permission flow (a disclosed residual), rather than
    # over-asking on every such command. This is the conservative-floor boundary, not a proof of safety.
    segments = _lex_command(command, partial=True)[0]
    reasons = []       # relative/opaque cd or NON-destructive redirect: an allow-note convention nudge
    deny_reasons = []  # a DESTRUCTIVE truncating redirect to a relative/opaque target: fail-closed deny
    for seg in segments:
        word = _command_word(seg.argv)
        if word in _CD_BUILTINS:
            reason = _cd_destination_reason(word, seg.argv, seg.argv_leading_tilde)
            if reason is not None:
                reasons.append(reason)
        for redirect in seg.redirects:
            if redirect.target_class == "descriptor":
                continue  # a numeric/'-' fd duplication or close names no filesystem path
            if redirect.target_leading_tilde:
                continue  # an unquoted current-user '~'/'~/x' target expands to $HOME (absolute), as cd's
            if redirect.target_class == "opaque" or not _is_absolute(redirect.target):
                if redirect.op in _ABSPTH_TRUNCATING_OPS:
                    # ROUND-2 FINDING 10: a TRUNCATING redirect zeroes its target, so aiming it at a
                    # cwd-dependent relative/opaque path can destroy the WRONG file -> deny-and-educate.
                    deny_reasons.append("a truncating redirection ('{}') to the relative or unverified "
                                        "target {!r}".format(redirect.op, redirect.target))
                else:
                    reasons.append("a relative redirection target {!r}".format(redirect.target))
    if deny_reasons:
        seen = []
        for reason in deny_reasons:
            if reason not in seen:
                seen.append(reason)
        return _deny(
            "AIQT rule abspth (absolute-paths): this command carries {} that resolve(s) against the current "
            "working directory, which can silently differ between calls. A truncating redirect ZEROES its "
            "target, so aimed at a cwd-dependent relative or unverified path it can destroy the WRONG file; "
            "it is denied rather than run on an unverified destructive target. Name an ABSOLUTE, verified "
            "target for the truncating redirect (for example '> /abs/path/out'), or confirm the working "
            "directory. (A relative cd, an append '>>', or a read '<' redirect is only a convention nudge, "
            "not denied.)".format("; ".join(seen)),
            "AIQT guardrail: denied a truncating redirect to a relative/unverified target that could zero the "
            "wrong file (rule abspth); name an absolute, verified target.")
    if reasons:
        seen = []
        for reason in reasons:  # de-duplicate while preserving order so the banner stays bounded
            if reason not in seen:
                seen.append(reason)
        return _allow_note(
            "AIQT guardrail (rule abspth, absolute-paths): this command carries {} that resolve(s) against "
            "the current working directory, which can silently differ between calls. This is a convention "
            "nudge, not a hazard, so it is allowed; prefer absolute paths, or ensure the working directory is "
            "the intended one.".format("; ".join(seen)))
    return _allow()


# --- prsunc: a git discard that would lose uncommitted work ------------------------------------------
# ULTRA-CONSERVATIVE "recover then allow" guard (EN-6, EN-6 hardening pass over the GD-41 coarse cut).
# NO-ASK TRANSLATION (maintainer directive): this control was originally an "ask unless PRISTINE and provably
# clean" three-outcome (allow/ask/deny) guard. Under the no-ask directive it NEVER prompts: every former ASK
# is resolved WITHOUT a human - a recoverable-destructive discard is SNAPSHOT-THEN-ALLOWED (an inert
# refs/aiqt-recovery/ snapshot is taken, then it is allowed with a recovery-pointer note), and it DENIES when
# no warranted recovery snapshot can be created (an unrecoverable discard) or when the command cannot be
# classified/resolved (an inline alias, an unrecognized flagged subcommand, an unresolvable worktree). The
# text below still uses "ASK" for the recoverable middle; read every such "ASK" as "snapshot-then-allow when
# recoverable, else deny-and-educate". For a command that names any recognized lossy git verb (checkout incl
# -B force-create, switch incl -C/--force-create, restore/reset/clean/stash drop-clear/rm/branch force
# delete/move/copy/reset) the outcome is ASK(->recover-then-allow/deny) unless the command is a PRISTINE
# SINGLE BARE 'git <verb>' invocation on a PROVABLY CLEAN tree (or, for that same pristine form, the LEADING
# opt-out is set). Three outcomes:
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
#   ASK(->recover-then-allow/deny) - EVERYTHING else in scope. This is deliberately the common branch, now
#                           resolved WITHOUT a prompt (snapshot-then-allow when recoverable, else deny):
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
# Fallback-only raw-string probes, used when the shared tokenizer cannot parse the command (an unbalanced quote or an unsupported construct). We cannot
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


# --- expbnd: explicit git target and enumerated scope -----------------------------------------------
_GIT_MUTATING_VERBS = frozenset((
    "add", "am", "apply", "branch", "checkout", "cherry-pick", "clean", "commit", "config", "init",
    "merge", "mv", "notes", "pull", "push", "rebase", "reset", "restore", "revert", "rm", "stash",
    "switch", "tag", "update-index", "update-ref", "worktree"))
_EXPLICIT_GIT_TARGET_OPTS = frozenset(("-C", "--git-dir", "--work-tree"))
# branch/tag classification (GD-158 rounds 1-6, synthesis; flag behaviour pinned to git 2.53; the long
# universe is a conservative SUPERSET of git 2.53's branch/tag option table, validated against git 2.53
# by the eb-e57..e147 differential self-test and an out-of-suite real-git differential; a future git
# option-table change, such as a new write option absent from the table, is a DISCLOSED drift residual
# caught by re-validating the table on a git upgrade, with a dedicated option-table drift-tripwire gate a
# tracked follow-up, GD-158-T7). The classifier is FAIL-SAFE by
# construction: it DEFAULTS to MUTATING and returns READ only when every token positively resolves to a
# recognized read-neutral role and no create/rename/delete TARGET is present (before OR after '--'). A
# missed WRITE spelling would be a fail-open (forbidden), so WRITE recognition is complete across every
# spelling form; a missed READ is only an over-ASK (safe), so READ recognition may be incomplete. Roles,
# one char per long option: 'W' write (immediate mutating); 'L' list trigger (no value); 'V' tag verify
# (read mode, no value); 'F' filter (sets list mode, takes a value that is optional only when last); 'D'
# display taking a REQUIRED value but NOT setting list mode (a following name still creates); 'O' optional
# '='-attached value only (consumes NOTHING following); 'R' plain read (no value, no list mode). The long
# universe is a conservative SUPERSET of git's real per-subcommand option table (hidden aliases, the
# deprecated --set-upstream, and generated --no-* forms included): a MISSING entry could let a unique
# prefix resolve to a read where real git sees a write or an ambiguity (a fail-open), while an EXTRA entry
# only over-ASKs. Mode and filter CANCELLERS (--no-list, --no-verify, --no-points-at, --no-with,
# --no-without, --no-show-current) are roled W: roling --no-list read would fail-open
# 'git branch --list --no-list NAME' (a real create). Display negatives (--no-color, --no-sort, ...) are R
# (no value, no mode; a positional beside them still catches the create).
_GIT_REF_SPECS = {
    "branch": {
        "long": {
            # W: write or write-capable. Any unique abbreviation of these resolves to W -> MUTATING.
            "--delete": "W", "--no-delete": "W", "--move": "W", "--no-move": "W",
            "--copy": "W", "--no-copy": "W", "--force": "W", "--no-force": "W",
            "--track": "W", "--no-track": "W", "--set-upstream": "W",
            "--set-upstream-to": "W", "--no-set-upstream-to": "W",
            "--unset-upstream": "W", "--no-unset-upstream": "W",
            "--edit-description": "W", "--no-edit-description": "W",
            "--recurse-submodules": "W", "--no-recurse-submodules": "W",
            "--no-list": "W", "--no-show-current": "W",
            "--no-points-at": "W", "--no-with": "W", "--no-without": "W",
            # L / F: list trigger and filters (set list mode; F takes a value, optional only when last).
            "--list": "L",
            "--contains": "F", "--no-contains": "F", "--merged": "F", "--no-merged": "F",
            "--points-at": "F", "--with": "F", "--without": "F",
            # D: required value, NO list mode (a following name creates).
            "--format": "D", "--sort": "D",
            # O: optional '='-attached value only (never consumes a following token).
            "--color": "O", "--abbrev": "O", "--column": "O",
            # R: plain read, no value, no list mode (a name beside it -> positional -> create).
            "--all": "R", "--no-all": "R", "--remotes": "R", "--no-remotes": "R",
            "--verbose": "R", "--no-verbose": "R", "--quiet": "R", "--no-quiet": "R",
            "--show-current": "R", "--ignore-case": "R", "--no-ignore-case": "R",
            "--omit-empty": "R", "--no-omit-empty": "R",
            "--no-color": "R", "--no-abbrev": "R", "--no-column": "R",
            "--no-format": "R", "--no-sort": "R",
            "--create-reflog": "R", "--no-create-reflog": "R", "--help": "R",
        },
        "short_write": frozenset("dDmMcCutf"),  # d/D/m/M/c/C delete/move/copy; u upstream; t track; f force
        "short_list": frozenset("l"),           # -l list
        "short_read": frozenset("arvqih"),       # a/r all/remotes; v verbose; q quiet; i ignore-case; h help
        "short_optnum": frozenset(),             # branch has no -n
    },
    "tag": {
        "long": {
            # W
            "--annotate": "W", "--no-annotate": "W", "--sign": "W", "--no-sign": "W",
            "--message": "W", "--no-message": "W", "--file": "W", "--no-file": "W",
            "--local-user": "W", "--no-local-user": "W", "--force": "W", "--no-force": "W",
            "--delete": "W", "--no-delete": "W", "--edit": "W", "--no-edit": "W",
            "--cleanup": "W", "--no-cleanup": "W", "--trailer": "W", "--no-trailer": "W",
            "--no-list": "W", "--no-verify": "W",
            "--no-points-at": "W", "--no-with": "W", "--no-without": "W",
            # V / L / F
            "--verify": "V", "--list": "L",
            "--contains": "F", "--no-contains": "F", "--merged": "F", "--no-merged": "F",
            "--points-at": "F", "--with": "F", "--without": "F",
            # D / O / R
            "--format": "D", "--sort": "D",
            "--color": "O", "--column": "O",
            "--ignore-case": "R", "--no-ignore-case": "R", "--omit-empty": "R", "--no-omit-empty": "R",
            "--no-color": "R", "--no-column": "R", "--no-format": "R", "--no-sort": "R",
            "--create-reflog": "R", "--no-create-reflog": "R", "--help": "R",
        },
        "short_write": frozenset("asmFfedu"),  # a annotate; s sign; m message; F file; f force; e edit; d delete; u local-user
        "short_list": frozenset("lv"),          # l list; v verify (verify is a READ mode)
        "short_read": frozenset("ih"),          # i ignore-case; h help
        "short_optnum": frozenset("n"),         # -n[N]: list-implying, optional ATTACHED decimal
    },
}
_GIT_CONFIG_WRITE_FLAGS = frozenset((
    "--unset", "--unset-all", "--add", "--replace-all", "--rename-section", "--remove-section",
    "-e", "--edit"))
_EXPBND_WRAPPER_WORDS = frozenset(("command", "exec", "builtin", "env", "sudo"))
_RAW_EXPBND_CD_RE = re.compile(r"(?i)(?:^|[\s;&|()])(?:cd|pushd|popd)(?=$|[\s;&|()])")
_RAW_EXPBND_MUTATE_RE = re.compile(
    r"(?is)\bgit\b.*?\b(?:add|am|apply|branch|checkout|cherry-pick|clean|commit|config|init|merge|mv|"
    r"notes|pull|push|rebase|reset|restore|revert|rm|stash|switch|tag|update-index|update-ref|worktree)\b")
_RAW_EXPBND_PRUNING_FETCH_RE = re.compile(
    r"(?is)\bgit\b.*?\bfetch\b.*?(?:--prune(?:-tags)?\b|(?<!\S)-[^\s]*p)")
_RAW_EXPBND_BREADTH_RE = re.compile(
    r"(?is)\bgit\b.*?(?:\badd\b.*?(?:--all\b|(?<!\S)-[^\s]*A|(?<!\S)(?:\.|:/)(?!\S))|"
    r"\bcommit\b.*?(?:--all\b|(?<!\S)-[^\s]*a))")
_RAW_EXPBND_PUSH_RE = re.compile(r"(?is)\bgit\b.*?\bpush\b")


def _is_abs_binding(value):
    """A -C/--git-dir value counts as an explicit binding only when it is a non-empty ABSOLUTE path; a
    relative or missing value still resolves against the ambient cwd and so is not a complete binding."""
    return isinstance(value, str) and value != "" and _is_absolute(value)


def _git_target_is_explicit(tokens):
    """Whether git's global-option region carries a COMPLETE, ABSOLUTE explicit binding: an absolute -C
    directory, or an absolute --git-dir (with or without --work-tree). A RELATIVE -C/--git-dir still
    resolves against the ambient cwd, and a lone --work-tree without --git-dir does not name which
    repository, so neither is a complete explicit binding; both leave the target ambient and are not
    credited (they route to the same ASK a bare ambient mutation would)."""
    i = _command_word_index(tokens) + 1
    n = len(tokens)
    dash_c = None
    git_dir = None
    while i < n:
        token = tokens[i]
        if not token.startswith("-"):
            break  # the subcommand: the global-option region has ended
        if token == "-C":
            dash_c = tokens[i + 1] if i + 1 < n else None
            i += 2
            continue
        if token in ("--git-dir", "--work-tree"):
            if token == "--git-dir" and i + 1 < n:
                git_dir = tokens[i + 1]
            i += 2
            continue
        if token.startswith("--git-dir=") and token != "--git-dir=":
            git_dir = token.split("=", 1)[1]
            i += 1
            continue
        if "=" not in token and token in _GIT_ARG_OPTS:
            i += 2
            continue
        i += 1
    return _is_abs_binding(dash_c) or _is_abs_binding(git_dir)


def _resolve_long_role(name, long_roles):
    """Resolve a '--<name>' option to its role. Exact match first (git's own rule), else a UNIQUE prefix
    over the FULL universe (every key, W entries included). Zero matches (unknown) or two-or-more
    (ambiguous, exactly what real git rejects) resolve to None, which the caller treats as MUTATING. The
    universe MUST be a superset of git's real option table: a missing entry could let a prefix resolve
    uniquely to a read where git sees a write or an ambiguity (a fail-open); the table is validated against
    git 2.53 by the eb differential self-test and an out-of-suite real-git differential, with the drift
    residual disclosed and a dedicated option-table drift-tripwire gate tracked as follow-up GD-158-T7."""
    if name in long_roles:
        return long_roles[name]
    matches = [k for k in long_roles if k.startswith(name)]
    return long_roles[matches[0]] if len(matches) == 1 else None


def _short_cluster_verdict(chars, spec):
    """Classify a clustered short-flag body (the chars after a single leading '-'), scanning EVERY char
    left to right. A WRITE char short-circuits to 'MUT' before any attached value is reached, so
    '-uorigin/main' and '-mMSG' are caught by their leading write char. A list char latches list mode. The
    tag optnum char ('n') takes an optional ATTACHED decimal: trailing digits are its value and end the
    cluster ('-n'/'-n1' -> list mode); a non-digit tail is malformed -> 'MUT'. An unknown char is
    fail-safe 'MUT'. Returns 'MUT', 'LIST' (a list char or a valid -n[N] was seen), or 'READ' (only
    read-neutral chars, no list trigger)."""
    saw_list = False
    idx = 0
    while idx < len(chars):
        c = chars[idx]
        if c in spec["short_write"]:
            return "MUT"
        if c in spec["short_list"]:
            saw_list = True
            idx += 1
            continue
        if c in spec["short_read"]:
            idx += 1
            continue
        if c in spec["short_optnum"]:
            rest = chars[idx + 1:]
            if rest and not rest.isdigit():
                return "MUT"  # -n with a non-digit tail (e.g. -nf) is malformed
            return "LIST"     # -n / -nN: list mode, the attached digits are consumed as its value
        return "MUT"          # unknown short char -> fail-safe
    return "LIST" if saw_list else "READ"


def _git_ref_cmd_mutating(sub, args):
    """Fail-safe branch/tag mutation classifier (GD-158 rounds 1-6 + tri-family synthesis). DEFAULTS to
    MUTATING (return True); returns READ (return False) ONLY when every option token resolves to a known
    non-W role with a parseable value form, every positional is absent or covered by list/verify mode, and
    the post-'--' region is empty or covered by list/verify mode. Any unknown, ambiguous, or malformed form
    routes to True. Consumed by git_explicit_binding only. See _GIT_REF_SPECS for the role tables."""
    spec = _GIT_REF_SPECS[sub]
    long_roles = spec["long"]
    pre, post, _had = _split_pre_post(args)  # existing helper: splits at '--'/'--end-of-options'
    list_mode = False
    i, n = 0, len(pre)
    while i < n:
        tok = pre[i]
        i += 1
        if not tok.startswith("-") or tok == "-":
            if not list_mode:
                return True  # a bare positional is a create/rename/delete TARGET (fail-safe)
            continue         # list/verify mode: the positional is a pattern or verify operand
        if tok.startswith("--"):
            name, sep, _val = tok[2:].partition("=")
            role = _resolve_long_role("--" + name, long_roles)
            if role is None or role == "W":
                return True  # unknown / ambiguous prefix / write -> MUTATING
            if role in ("L", "V"):
                if sep:
                    return True  # '=' on a no-value option is malformed -> MUTATING
                list_mode = True
            elif role == "F":
                list_mode = True
                if not sep:
                    if i < n and not pre[i].startswith("-"):
                        i += 1       # separate filter value consumed
                    elif i < n:
                        return True  # a dash-leading separate value cannot be certified -> MUTATING
                    # at end: the filter value is optional-when-last (defaults HEAD) -> stays list mode
            elif role == "D":
                if not sep:
                    if i < n and not pre[i].startswith("-"):
                        i += 1       # separate display value consumed
                    else:
                        return True  # missing OR dash-leading required value -> MUTATING (closes D-3)
            elif role == "R":
                if sep:
                    return True  # '=' on a no-value option -> MUTATING
            # role "O": optional '='-attached value only; consumes NOTHING following, sets no mode
        else:
            verdict = _short_cluster_verdict(tok[1:], spec)
            if verdict == "MUT":
                return True
            if verdict == "LIST":
                list_mode = True
    if post and not list_mode:
        return True  # a create/rename/delete target placed after '--' (git accepts the target there)
    return False     # every token read-neutral and no target present -> READ


def _git_is_mutating(sub, args):
    """Conservative git mutation classifier, with only enumerated read-only forms exempted."""
    if sub == "fetch":
        return any(a in ("--prune", "--prune-tags", "-p") or
                   (a.startswith("-") and not a.startswith("--") and "p" in a[1:])
                   for a in args)
    if sub not in _GIT_MUTATING_VERBS:
        return False
    if sub == "branch":
        return _git_ref_cmd_mutating("branch", args)
    if sub == "tag":
        return _git_ref_cmd_mutating("tag", args)
    if sub == "stash":
        return not args or args[0] not in ("list", "show")
    if sub == "config":
        if any(a == "-l" or a == "--list" or a.startswith("--get") for a in args):
            return False
        if any(a in _GIT_CONFIG_WRITE_FLAGS for a in args):
            return True
        return len([a for a in args if not a.startswith("-")]) >= 2
    if sub == "worktree":
        return not args or args[0] != "list"
    if sub == "notes":
        return not args or args[0] not in ("list", "show")
    return True


def _git_is_breadth(sub, args):
    """Whether add/commit takes scope from the ambient whole tree rather than enumerated paths. Option
    recognition STOPS at a '--' end-of-options marker, so a file literally named '--all'/'-A' after '--'
    is an operand, not a breadth selector; a genuine whole-tree pathspec ('.'/':/'') still counts on
    either side of the marker."""
    if sub == "add":
        pre, post, _had = _split_pre_post(args)
        for arg in pre:
            if arg in (".", ":/", "--all"):
                return True
            if arg.startswith("-") and not arg.startswith("--") and "A" in arg[1:]:
                return True
        return any(arg in (".", ":/") for arg in post)
    if sub == "commit":
        pre, _post, _had = _split_pre_post(args)
        for arg in pre:
            if arg == "--all":
                return True
            if arg.startswith("-") and not arg.startswith("--") and "a" in arg[1:]:
                return True
    return False


def _expbnd_effective_tokens(tokens):
    """Peel a leading env-assignment prefix and any run of BARE command/exec/builtin/env/sudo wrappers,
    returning the tokens from the real command word onward so a wrapped 'command git ...'/'sudo git ...'
    /'command cd ...' is judged like the bare form. A wrapper carrying its own option or assignment
    ('env -i', 'env FOO=1', 'sudo -u u') stops the run and is left in place, a disclosed residual whose
    option grammar is never guessed."""
    idx = _command_word_index(tokens)
    while idx < len(tokens):
        if tokens[idx].rsplit("/", 1)[-1] not in _EXPBND_WRAPPER_WORDS:
            break
        nxt = idx + 1
        if nxt >= len(tokens):
            break
        following = tokens[nxt]
        if following.startswith("-") or _ENV_ASSIGN_RE.match(following):
            break
        idx = nxt
    return tokens[idx:]


def _expbnd_target_ask(verb):
    return _allow_note(
        "AIQT guardrail (rule expbnd, explicit-binding): a directory change in this command feeds a git "
        "'{}' with no explicit target, so the mutation binds to wherever the shell landed. This is a "
        "binding convention where the orchestrator normally binds its own target, not a hazard, so it is "
        "allowed; prefer 'git -C /absolute/repo {} ...' or ensure the working directory is the intended "
        "repository.".format(verb, verb))


def _expbnd_breadth_ask(verb):
    return _allow_note(
        "AIQT guardrail (rule expbnd, explicit-binding): git '{}' takes its scope from the whole ambient "
        "tree and this command relocates it. This is a binding convention, not a hazard, so it "
        "is allowed; prefer enumerated pathspecs over -A/-a/'.', or ensure the whole-tree scope is "
        "intended.".format(verb))


def _expbnd_breadth_publish_deny(verb):
    """ROUND-2 FINDING 11: a whole-tree BREADTH stage ('git add -A/./:/', 'git commit -a/--all') FOLLOWED BY
    a PUBLISH ('git push') in the SAME command is denied-and-educated. The breadth selector stages whatever
    the tree currently holds - including unrelated modified or untracked content - and the push then
    publishes that swept-in content irreversibly to a remote; that is exactly the ambient-whole-tree scope
    the explicit-binding rule forbids for a broad operation, made worse by immediate publication. A plain
    'git commit -m' and a scoped 'git add <path>' are NOT breadth and are unaffected; a breadth stage with
    NO publish in the command is only the convention nudge (allow-note), not this deny."""
    return _deny(
        "AIQT rule expbnd (explicit-binding-over-ambient-context): this command stages the whole ambient "
        "tree with git '{}' (-A/-a/'.') and then PUBLISHES it with 'git push' in the same command, so any "
        "unrelated modified or untracked content the tree currently holds is swept into the commit and "
        "pushed to a remote (an ambient-whole-tree scope for a broad, published operation). It is denied. "
        "Stage the specific paths the change touches with enumerated pathspecs ('git add <path> ...'), "
        "commit, then push as a separate step after reviewing what is staged; never pair a whole-tree "
        "breadth stage with a push in one command.".format(verb),
        "AIQT guardrail: denied a whole-tree breadth stage ('{}') paired with a push in one command (rule "
        "expbnd); stage explicit paths and push as a reviewed separate step.".format(verb))


def _expbnd_fallback(command):
    """Conservative ASK fallback for visible in-scope pairs in an unparseable shell command."""
    if (_RAW_EXPBND_CD_RE.search(command) and
            (_RAW_EXPBND_MUTATE_RE.search(command) or _RAW_EXPBND_PRUNING_FETCH_RE.search(command))):
        return _expbnd_target_ask("mutation")
    if _RAW_EXPBND_BREADTH_RE.search(command) and _RAW_EXPBND_PUSH_RE.search(command):
        return _expbnd_breadth_publish_deny("breadth operation")  # finding 11: breadth + publish -> deny
    return _allow()


def git_explicit_binding(data):
    """expbnd (integ/explicit-binding-over-ambient-context), PreToolUse/Bash. NO-ASK posture: an ambient git
    target, or a whole-tree breadth stage that merely RELOCATES (a breadth op after a cd, with no publish),
    is a binding CONVENTION where the orchestrator normally binds its own target, so those ALLOW with an
    informational note rather than prompting. The ONE deny (round-2 finding 11): a whole-tree BREADTH stage
    ('git add -A/./:/', 'git commit -a/--all') FOLLOWED BY a PUBLISH ('git push') in the SAME command
    DENIES-and-educates - the breadth selector sweeps in unrelated modified/untracked content and the push
    then publishes it irreversibly, so it is not waved through. A plain 'git commit -m' and a scoped
    'git add <path>' are not breadth and are unaffected. It never asks."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: git_explicit_binding wired to unexpected event {!r}; failing "
                           "closed".format(data.get("hook_event_name")))
    tool = data.get("tool_name")
    if tool is None:
        return _deny_missing_tool_name("expbnd")
    if tool != "Bash":
        return _allow()
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        return _allow_note(
            "AIQT guardrail (rule expbnd, explicit-binding): the Bash command was absent or unreadable, so "
            "its git target and scope could not be checked. This is a binding convention, not a hazard, so "
            "it is allowed; prefer an explicit '-C' target and enumerated scope.")
    try:
        segments = _segments(command)
    except ValueError:
        return _expbnd_fallback(command)

    # ROUND-2 FINDING 11 PRE-SCAN: a whole-tree BREADTH stage ('git add -A/./:/', 'git commit -a/--all')
    # FOLLOWED BY a PUBLISH ('git push') ANYWHERE later in the command DENIES-and-educates, regardless of any
    # cd/pushd between them. This runs BEFORE the dir-change/target logic below so a preceding cd (which would
    # otherwise short-circuit to the relocate allow-note) cannot mask the breadth+publish hazard. A push
    # BEFORE the breadth stage publishes only the pre-breadth state and does not fire.
    breadth_before_push = None
    for tokens, _sep in segments:
        eff = _expbnd_effective_tokens(tokens)
        if _command_word(eff) != "git":
            continue
        s, a = _git_sub_and_args(eff)
        if s is None:
            continue
        if s == "push" and breadth_before_push is not None:
            return _expbnd_breadth_publish_deny(breadth_before_push)
        if _git_is_breadth(s, a):
            breadth_before_push = s

    # A cd/pushd/popd SHIFTS the target out of the session cwd (popd lands on an unknowable stack-top
    # directory, so it too confuses a following git segment). The shift confuses a git segment only when it
    # PRECEDES it, so saw_dir_change is read at the point each git segment is processed (segments iterate in
    # command order), not accumulated and tested at the end: "git commit && cd /x" leaves the mutation
    # unconfused and is exempt, while "cd /x && git commit" and "popd && git commit" are not. A shift reached
    # only through a "||" (git runs only on the prior command's failure, so the cwd is unchanged) or a "|"
    # (a subshell cd) is conservatively treated as preceding: a safe over-ASK, not a silent allow. The
    # breadth+publish deny is handled by the pre-scan above; a breadth op after a cd with NO publish is only
    # the relocate convention nudge.
    saw_dir_change = False
    for tokens, _sep in segments:
        eff = _expbnd_effective_tokens(tokens)
        word = _command_word(eff)
        if word in _CD_BUILTINS or word == "popd":
            saw_dir_change = True
            continue
        if word != "git":
            continue
        sub, args = _git_sub_and_args(eff)
        if sub is None:
            continue
        if (_git_is_mutating(sub, args) and not _git_target_is_explicit(eff)
                and saw_dir_change):
            return _expbnd_target_ask(sub)
        if _git_is_breadth(sub, args) and saw_dir_change:
            return _expbnd_breadth_ask(sub)
    return _allow()


def _stash_pop_apply_bare(sub, args):
    """Whether stash pop/apply has no positional operand; presence is not ref validation."""
    if sub != "stash":
        return False
    first = next((a for a in args if not a.startswith("-")), None)
    if first not in ("pop", "apply"):
        return False
    rest = args[args.index(first) + 1:]
    pre, post, _had_sep = _split_pre_post(rest)
    return not any(not a.startswith("-") for a in pre) and not post


def git_stash_ref(data):
    """expbnd (integ/explicit-binding-over-ambient-context), PreToolUse/Bash. A bare stash pop/apply
    ALLOWS with one informational note; a positional operand silently allows. NO-ASK posture.

    HONEST RESIDUAL: presence is not identity, well-formedness, existence, or intent. A SHA-shaped
    token is not object validation, and stash@{N} can shift between listing and execution. Dynamic
    operands, including variable/command substitutions and an empty quoted operand, count as present
    even if empty at runtime. Aliases, functions, eval, unknown wrappers, and wrappers with their own
    options or assignments are not resolved. Simple quote fragments ARE decoded by the shared lexer;
    fragmentation/obfuscation that does not resolve to recognized command/verb tokens is not covered.
    The shared segmenter splits ordinary separators (`;`, `|`, `&&`, `||`, newlines) and `(...)`
    subshells but does NOT structurally parse `{...}` brace groups or shell control-flow
    constructs, so a bare pop/apply is missed only when an UNPEELED brace or shell-keyword token
    occupies the command-word position of `git`'s segment: for example a `{` group opener, or a
    control keyword such as `if`, `elif`, `else`, `while`, `until`, `then`, or `do` immediately
    preceding `git` in the same segment (these examples are illustrative, not exhaustive); a `git
    stash pop` that a `;` or newline delimits into its own segment IS seen and warns. git_discard's
    generic fallback may still note the missed same-segment form, and extending the shared segmenter
    to those forms is a family-wide follow-on.
    Only the current no-value pop/apply option grammar (-q/--quiet/--index) is modelled: a future
    value-taking option can make its value look like an operand and falsely allow; re-validate on a
    git upgrade. No git calls or filesystem probes: every recognized bare pop/apply warns regardless
    of stack state, with no escalation for a dangerous stack. Bash PreToolUse only; no prior-turn
    shell-state resolution. Other stash verbs, including bare stash (push), are outside this guard;
    drop/clear/export belong to git_discard. Unparseable text silently allows. An absent/unreadable
    command gets the sibling's convention note.
    This note can co-occur with git_discard's raw/pristine fallback note on a wrapped or compound
    pop/apply, and with git_explicit_binding's note on a relocated form (e.g. `cd /x && git stash
    pop`); such sibling notes are consistent, never conflicting.
    Only missing tool_name produces a structured deny;
    a miswired event hard-blocks and dispatcher errors retain the manifest's fail-closed posture."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: git_stash_ref wired to unexpected event {!r}; failing "
                           "closed".format(data.get("hook_event_name")))
    tool = data.get("tool_name")
    if tool is None:
        return _deny_missing_tool_name("expbnd")
    if tool != "Bash":
        return _allow()
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command:
        return _allow_note(
            "AIQT guardrail (rule expbnd, explicit-binding): the Bash command was absent or unreadable, so "
            "its git target and scope could not be checked. This is a binding convention, not a hazard, so "
            "it is allowed; prefer an explicit '-C' target and enumerated scope.")
    try:
        segments = _segments(command)
    except ValueError:
        return _allow()

    for tokens, _sep in segments:
        eff = _expbnd_effective_tokens(tokens)
        if _command_word(eff) != "git":
            continue
        sub, args = _git_sub_and_args(eff)
        if _stash_pop_apply_bare(sub, args):
            return _allow_note(
                "AIQT expbnd (explicit-binding-over-ambient-context): a bare git stash pop/apply acts on "
                "the top of the stash stack, ambient shared state that may not be the entry you mean. "
                "Run 'git stash list', then name the entry explicitly, for example "
                "\"git stash apply 'stash@{2}'\".")
    return _allow()


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
    """The (reason, banner) pair for an ASK. Stored by the handler and carried into the no-ask decision
    the role dispatch determines (deny, allow-with-note, or snapshot-then-allow), so a confirmed loss
    still wins over a recoverable discard; the "ASK" here is historical shorthand per the module's
    READING KEY FOR "ASK"/"ASKS", not a live outcome. `optout` selects the PATH-AWARE
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


def _discard_deny(kind, recovery=""):
    """A DENY: a whole-tree-clobbering verb on a tree the probe confirms is dirty (an uncommitted tracked
    change OR an untracked file the verb could reach), so the loss is certain. The wording covers untracked
    too, because the config-forced probe now counts an untracked-only-dirty tree as dirty. `recovery` is
    appended to the reason as given (see _deny_with_recovery), so no caller edits a built deny result."""
    reason = ("AIQT rule prsunc (preserve-uncommitted-work): {} would overwrite the working tree, which "
              "currently holds uncommitted or untracked changes the command could destroy, discarding any "
              "fix you have applied but not yet committed. {}{}"
              .format(kind, _DISCARD_ALTS, _OPTOUT_PRISTINE)) + recovery
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


def _segment_redirect_worktree(tokens, cwd):
    """ROUND-2 FINDING 4. For a PRISTINE single-bare git command that carries a command-local worktree-
    CHANGING redirect, resolve the effective WORKTREE DIRECTORY the command will act on, so a destructive
    discard can be snapshotted against its ACTUAL target rather than the session cwd. Returns:
      - None            : no worktree-changing redirect. A bare command, or one carrying only --git-dir /
                          GIT_DIR / -c, leaves the session cwd as the worktree, so the caller uses cwd.
      - a directory str : the effective worktree dir (a -C target, or a --work-tree / GIT_WORK_TREE value),
                          resolved against cwd (compounded -C dirs are applied left to right, as git does).
      - "opaque"        : a worktree-changing redirect is present but its target cannot be resolved (a
                          value-less flag, or a relative value with no cwd to anchor it), so the caller
                          cannot snapshot the target and DENIES a destructive discard rather than allow-note
                          a recovery ref that would not contain the discarded state.
    git applies -C left to right and chdirs there; a bare -C dir (with no --work-tree) then IS the worktree.
    A command-line --work-tree overrides an inline GIT_WORK_TREE=; a --git-dir / GIT_DIR alone does NOT move
    the worktree (git uses the cwd). Only the command-local redirect is read here; an ambient process-env
    GIT_* var is handled by the caller's fail-safe (it is unreadable from the command)."""
    cw = _command_word_index(tokens)
    env_wt = None
    for tok in tokens[:cw]:
        if tok.startswith("GIT_WORK_TREE="):
            env_wt = tok[len("GIT_WORK_TREE="):]      # last-wins mirrors the shell
    c_dirs = []
    opt_wt = None
    saw_redirect = env_wt is not None
    i = cw + 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if not tok.startswith("-"):
            break                                     # the subcommand
        if tok == "-C":
            if i + 1 >= n:
                return "opaque"                       # a value-less -C: cannot resolve the target
            c_dirs.append(tokens[i + 1]); saw_redirect = True; i += 2; continue
        if tok == "--work-tree":
            if i + 1 >= n:
                return "opaque"
            opt_wt = tokens[i + 1]; saw_redirect = True; i += 2; continue
        if tok.startswith("--work-tree="):
            opt_wt = tok[len("--work-tree="):]; saw_redirect = True; i += 1; continue
        if "=" not in tok and tok in _GIT_ARG_OPTS:   # a separated value-consuming global option: skip both
            i += 2; continue
        i += 1
    if not saw_redirect:
        return None                                   # only --git-dir/GIT_DIR/-c (or nothing): worktree == cwd
    base = cwd
    for d in c_dirs:
        if not d:
            return "opaque"
        if os.path.isabs(d):
            base = d
        elif base is None:
            return "opaque"                           # a relative -C with no cwd to anchor it
        else:
            base = os.path.join(base, d)
    wt = opt_wt if opt_wt is not None else env_wt     # a command-line --work-tree overrides GIT_WORK_TREE=
    if wt is not None:
        if not wt:
            return "opaque"
        if os.path.isabs(wt):
            return wt
        wt_base = base if base is not None else cwd
        if wt_base is None:
            return "opaque"
        return os.path.join(wt_base, wt)
    if c_dirs:
        return base if base is not None else "opaque"  # a bare -C dir is itself the worktree
    return None


def _segment_repo_dir(tokens, cwd):
    """ROUND-7 (codex findings 1 and 2). The directory git discovers the REPOSITORY (git-dir, index, refs,
    HEAD, stash) from for a git segment's index/ref/commit/branch/stash operation, resolving -C targets
    left-to-right against cwd but treating --work-tree / GIT_WORK_TREE as WORKTREE-ONLY. Git semantics:
    --work-tree (and GIT_WORK_TREE) relocate ONLY the working tree; the repository the command's index/ref/
    commit/branch/stash op acts on stays in the ambient git-dir (or --git-dir if given), NEVER the
    --work-tree value. _segment_redirect_worktree (which returns the --work-tree value) wrongly doubled as a
    REPO redirect, landing a recovery snapshot / stash preservation / HEAD-ancestry probe on the wrong repo.
    Returns:
      - a dir str : the repo-discovery dir - the resolved -C target, or, when a --work-tree/GIT_WORK_TREE
                    redirect is present with NO -C, the session cwd (git discovers the repo from cwd; the
                    --work-tree only moves the worktree). Callers snapshot/probe THIS dir, never --work-tree.
      - None      : no dir-relocating redirect this helper resolves: a bare command, or one carrying only
                    --git-dir/GIT_DIR (which names the repo directly, not a discoverable dir), -c, or a
                    non-opt-out env assignment. The caller keeps its own conservative cannot-pin handling.
      - "opaque"  : a -C redirect is present but unresolvable (value-less, or relative with no cwd to anchor).
    A --git-dir/GIT_DIR redirect returns None (the repo is named directly, left to the caller's cannot-pin
    path); a combined --git-dir + -C still returns None here, so the caller's --git-dir handling governs."""
    if _segment_has_gitdir_redirect(tokens):
        return None
    cw = _command_word_index(tokens)
    has_worktree = any(tok.startswith("GIT_WORK_TREE=") for tok in tokens[:cw])
    c_dirs = []
    i = cw + 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if not tok.startswith("-"):
            break                                     # the subcommand
        if tok == "-C":
            if i + 1 >= n:
                return "opaque"                       # a value-less -C: cannot resolve the repo dir
            c_dirs.append(tokens[i + 1]); i += 2; continue
        if tok == "--work-tree":
            has_worktree = True; i += 2 if i + 1 < n else 1; continue   # worktree-only: not a repo redirect
        if tok.startswith("--work-tree="):
            has_worktree = True; i += 1; continue
        if "=" not in tok and tok in _GIT_ARG_OPTS:   # a separated value-consuming global option: skip both
            i += 2; continue
        i += 1
    if not c_dirs:
        return cwd if has_worktree else None           # --work-tree alone: the repo is the ambient cwd
    base = cwd
    for d in c_dirs:
        if not d:
            return "opaque"
        if os.path.isabs(d):
            base = d
        elif base is None:
            return "opaque"                            # a relative -C with no cwd to anchor it
        else:
            base = os.path.join(base, d)
    return base


def _segment_has_worktree_redirect(tokens):
    """ROUND-7 (codex finding 1). True when a git segment carries a --work-tree / GIT_WORK_TREE redirect
    (a bare or '='-attached --work-tree global option, or a leading GIT_WORK_TREE= env assignment).
    --work-tree relocates ONLY the worktree, so a WORKTREE-CONTENT discard under it destroys content in a
    directory the ambient-repo snapshot does not cover; git_discard uses this to fail closed on such a
    discard unless that worktree is provably the repo's own toplevel."""
    cw = _command_word_index(tokens)
    if any(tok.startswith("GIT_WORK_TREE=") for tok in tokens[:cw]):
        return True
    i = cw + 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if not tok.startswith("-"):
            break
        if tok == "--work-tree" or tok.startswith("--work-tree="):
            return True
        if "=" not in tok and tok in _GIT_ARG_OPTS:
            i += 2; continue
        i += 1
    return False


def _discard_index_only(sub, args):
    """ROUND-7 (codex finding 1). True when a recognized destructive discard form touches ONLY the index/refs
    (NOT working-tree content), so a snapshot of the AMBIENT repository fully captures what it discards and a
    --work-tree redirect (which relocates only the worktree) does not move the discarded state out of reach.
    CONSERVATIVE / fail-closed: True only for PROVABLY index-only forms; every other form (worktree-touching,
    ambiguous, or unmodelled) returns False so the caller fails closed on a --work-tree-redirected discard.
    Index-only forms: restore --staged/-S without --worktree/-W (index unstage); reset without
    --hard/--merge/--keep (the default/mixed reset touches the index only; --hard/--merge/--keep update the
    worktree); rm --cached (index-only removal). checkout/switch/clean and every other form touch the
    worktree."""
    pre, _post, _had = _split_pre_post(args)
    if sub == "restore":
        staged = any(a == "--staged" or (a.startswith("-") and not a.startswith("--") and "S" in a[1:])
                     for a in pre)
        worktree = any(a == "--worktree" or (a.startswith("-") and not a.startswith("--") and "W" in a[1:])
                       for a in pre)
        return staged and not worktree
    if sub == "reset":
        for a in pre:
            if a.startswith("--") and "=" not in a and len(a) > 2:
                name = a[2:]
                # A prefix of hard/merge/keep (an ambiguous '--m' is a prefix of merge) touches the worktree.
                if "hard".startswith(name) or "merge".startswith(name) or "keep".startswith(name):
                    return False
        return True                                    # default/mixed reset: index-only
    if sub == "rm":
        return any(a == "--cached" for a in pre)       # --cached: index-only removal (strict, fail-closed)
    return False                                       # checkout/switch/clean/etc. touch worktree content


def _worktree_within_repo(wt_dir, repo_probe):
    """ROUND-7 (codex finding 1). True when the effective --work-tree dir lies AT or UNDER repo_probe's own
    repository toplevel, so a recovery snapshot of repo_probe ('git -C <toplevel> add --all' captures the
    whole worktree subtree, the ambient repo's index included) captures the worktree content a
    --work-tree-redirected discard destroys. This covers the redundant self-reference (--work-tree is the
    repo root, e.g. 'git --work-tree=<repo> --git-dir=<repo>/.git reset --hard') AND a --work-tree pointing at
    a SUBDIR of the ambient repo ('GIT_WORK_TREE=sub git reset --hard' from the repo root). When the
    --work-tree is OUTSIDE the repo's toplevel (a separate or decoy repo, or a non-repo dir), the ambient
    snapshot cannot reach that content and the discard fails closed. Any unresolvable side returns False
    (fail-closed), and the containment is a component-boundary match (a realpath prefix on a '/' boundary),
    not a bare string prefix, so a sibling like '<repo>-x' is not judged inside '<repo>'."""
    if not isinstance(wt_dir, str) or not wt_dir or wt_dir == "opaque" or not repo_probe:
        return False
    top = _recovery_toplevel(repo_probe)
    if not top:
        return False
    try:
        w = os.path.realpath(wt_dir)
        t = os.path.realpath(top)
    except (OSError, ValueError):
        return False
    return w == t or w.startswith(t + os.sep)


# ROUND-6 FINDING 4. Raw indicators, on the UNPARSEABLE fallback path only, that a lossy git command's
# target is NOT the session cwd but a redirected worktree/repository the guard cannot resolve (it could not
# even parse the command): a `git -C <dir>`, a --git-dir/--work-tree redirect, a leading/ambient
# GIT_DIR=/GIT_WORK_TREE= assignment, or a cd/pushd in the chain. When any is present the session-cwd
# cleanliness/snapshot basis is UNSOUND (a clean session cwd does not prove the redirected target clean, and
# a session snapshot would not capture it), so the fallback DENIES-and-educates rather than allow on that
# basis. Conservative and over-matching (a keyword in prose over-denies, the safe direction), mirroring the
# other raw fallbacks; it is consulted only after _raw_has_lossy_git has already flagged the command in scope.
_RAW_DISCARD_REDIRECT_RE = re.compile(
    r"(?i)(?:^|[\s'\";&|()])(?:cd|pushd)(?=$|[\s'\";&|()])"
    r"|(?:^|[\s'\";&|()])-C(?=[\s'\"=]|$)"
    r"|--git-dir\b|--work-tree\b"
    r"|(?:^|[\s'\";&|()])GIT_DIR="
    r"|(?:^|[\s'\";&|()])GIT_WORK_TREE=")


def _git_discard_fallback(command, cwd=None):
    """FAIL-SAFE conservative scan when the shared tokenizer cannot parse the command (an unbalanced quote or an unsupported construct): we cannot
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
    it, `git reset --hard <<'EOF'\\n'\\nEOF` (Bash-valid, a tokenizer ValueError) reached ASK with NO recovery
    ref. The snapshot is decision-INDEPENDENT and best-effort against the session repo only (the verb is
    unparseable, so the ledger label is a generic 'discard'); on snapshot fail the decision stays ASK with
    the failure surfaced."""
    if not _raw_has_lossy_git(command):
        return _allow()  # no git, or no recognized work-losing verb: the true boundary
    if _RAW_DISCARD_REDIRECT_RE.search(command):
        # ROUND-6 FINDING 4: the unparseable command carries a target redirect (a -C/--git-dir/--work-tree,
        # a leading/ambient GIT_DIR=/GIT_WORK_TREE=, or a cd/pushd), so its discard target is NOT provably the
        # session cwd. A clean session cwd cannot prove the redirected target clean and a session snapshot
        # would not capture it, so it must NOT allow on that basis: DENY-and-educate. Re-issue it as a plain,
        # parseable 'git <verb>' command run FROM the target repository, or commit or stash first.
        return _deny(
            "AIQT rule prsunc (preserve-uncommitted-work): the command could not be parsed by the shell "
            "lexer and it names a git work-losing verb together with a target redirect (a -C/--git-dir/"
            "--work-tree, a GIT_DIR=/GIT_WORK_TREE= assignment, or a cd/pushd), so its discard targets a "
            "worktree or repository this guard cannot resolve; a clean session directory does not prove that "
            "target clean and a session snapshot would not capture it, so this discard could be "
            "unrecoverable and is denied rather than run. Re-issue it as a plain, parseable 'git <verb>' "
            "command from the target repository, or commit or stash your work first. {}".format(_DISCARD_ALTS),
            "AIQT guardrail: denied an unparseable git discard carrying a target redirect this guard cannot "
            "resolve to snapshot (rule prsunc); run it from the target repo, or commit or stash first.")
    base = cwd if isinstance(cwd, str) and cwd else None
    snap = None
    if base is not None and _tree_is_clean(base) is not True:  # dirty or probe-uncertain: snapshot first
        snap = _record_recovery(base, "discard")
    # SNAPSHOT-THEN-ALLOW: a recoverable discard proceeds once its recovery point exists (hooks never ask).
    if snap is not None and snap[0] == "ok":
        return _allow_note(
            "AIQT guardrail (rule prsunc, preserve-uncommitted-work): the command could not be parsed by the "
            "shell lexer and it names a git work-losing verb this guard cannot prove safe. A pre-command "
            "recovery snapshot was saved, so it is allowed. {}".format(_recovery_pointer(snap[1])))
    if snap is not None and snap[0] == "fail":
        # A warranted recovery point could NOT be created: an unrecoverable discard denies-and-educates.
        return _deny(
            "AIQT rule prsunc (preserve-uncommitted-work): the command could not be parsed by the shell "
            "lexer and it names a git work-losing verb on a tree that is not provably clean, and no "
            "pre-command recovery snapshot could be created ({}), so this discard would be unrecoverable; "
            "denied. Commit or stash your work first, then retry. {}".format(snap[1], _DISCARD_ALTS),
            "AIQT guardrail: denied an unparseable, unrecoverable git discard (rule prsunc); commit or stash "
            "first, then retry.")
    if base is None:
        # No session cwd, so no recovery snapshot could even be attempted for this unparseable lossy command.
        return _deny(
            "AIQT rule prsunc (preserve-uncommitted-work): the command could not be parsed by the shell "
            "lexer and it names a git work-losing verb, but no session working directory is available to "
            "take a recovery snapshot, so this discard would be unrecoverable; denied. Re-issue it as a "
            "parseable bare 'git <verb>' command from the target repository (a leading "
            "GUARDRAIL_ALLOW_DISCARD=1 prefix then opts out), or commit or stash your work first. {}"
            .format(_DISCARD_ALTS),
            "AIQT guardrail: denied an unparseable git discard with no recoverable snapshot (rule prsunc).")
    # base is set and the tree is provably clean: nothing to discard.
    return _allow()


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
    except (subprocess.SubprocessError, OSError, ValueError):
        # ValueError covers a UnicodeDecodeError raised by text-mode decoding of a non-UTF-8 toplevel path.
        return None
    if result.returncode != 0:
        return None
    try:
        # Strip git's ONE output terminator (its single trailing newline), NOT arbitrary whitespace and
        # NOT every trailing newline: a repo dir name may legitimately carry a leading or trailing SPACE
        # (which .strip() would corrupt) and may even END in a newline (which rstrip("\n") would eat along
        # with git's terminator), and either corruption makes the registry read (or a containment anchor)
        # silently miss. git prints the path plus EXACTLY one \n, so dropping only that one terminator
        # preserves every path character; a decode or type fault fails safe to None (an unresolved root ->
        # the caller ASKs).
        top = result.stdout[:-1] if result.stdout.endswith("\n") else result.stdout
    except (AttributeError, TypeError):
        return None
    if not top or not os.path.isabs(top):
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
        # ROUND-6 FINDING 6 (subprocess-status sweep): the index-path rev-parse status is now CHECKED. A
        # non-zero return means the real index could not be located to seed the temp index, so a staged-only
        # snapshot would be built over an EMPTY index (write-tree would "succeed" on it and silently drop
        # staged-only content); fail closed so the caller DENIES rather than advertise a snapshot missing it.
        ri = _recovery_git(
            repo, ["rev-parse", "--path-format=absolute", "--git-path", "index"], timeout=5)
        if ri.returncode != 0:
            return ("fail", "the repository index path could not be resolved to seed the staged snapshot "
                            "(git rev-parse --git-path index failed)")
        real_index = ri.stdout.strip()
        if real_index and os.path.exists(real_index):
            shutil.copyfile(real_index, tmp_index)  # else: git creates a fresh temp index on add --all
        env = {"GIT_INDEX_FILE": tmp_index}
        # ROUND-2 FINDING 5: capture the PURE STAGED (index) tree NOW, BEFORE `git add --all` overlays the
        # worktree over the seeded index. A blob staged but differing from - or absent from - the worktree is
        # staged-only content that `git restore --staged`, `git rm --cached`, or a default/mixed `git reset`
        # discards; add --all re-stages the worktree over it, so without this the recovery ref (built from the
        # worktree tree) would NOT contain it. Threading the staged tree in as a SECOND PARENT of the snapshot
        # commit keeps its blobs reachable from the ref (GC-safe). Best-effort: if the pure-index write-tree
        # fails (e.g. unmerged index entries), the snapshot still captures the worktree overlay.
        staged_tree = ""
        swt = _recovery_git(repo, ["write-tree"], env_extra=env, timeout=10)
        if swt.returncode == 0 and swt.stdout.strip():
            staged_tree = swt.stdout.strip()
        elif "staged" in classes:
            # ROUND-3 FINDING 3: the pure-index (staged) write-tree FAILED, yet the index carries staged
            # content (index differs from HEAD) that a `git restore --staged`, `git rm --cached`, or a
            # default/mixed `git reset` discards and that the worktree overlay tree below cannot recover
            # (staged-ONLY content is absent from the worktree). Without this capture the recovery ref would
            # NOT contain that payload, so advertising a snapshot would falsely claim it recoverable. FAIL
            # CLOSED (the caller DENIES) rather than note a snapshot missing the staged-only state. Earlier
            # this was best-effort (proceed with the worktree overlay only), which under-protected a
            # staged-only discard when the pure-index write-tree could not run (e.g. unmerged index entries).
            return ("fail", "the staged (index) state could not be captured (git write-tree on the pure "
                            "index failed) while staged content is present, so a staged-only discard would "
                            "be unrecoverable")
        if _recovery_git(repo, ["add", "--all"], env_extra=env, timeout=20).returncode != 0:
            return ("fail", "git add --all into the temp index failed")
        wt = _recovery_git(repo, ["write-tree"], env_extra=env, timeout=10)
        if wt.returncode != 0 or not wt.stdout.strip():
            return ("fail", "git write-tree (temp index) failed")
        tree = wt.stdout.strip()
        head = _recovery_git(repo, ["rev-parse", "--verify", "-q", "HEAD^{commit}"], timeout=5)
        parent = head.stdout.strip() if head.returncode == 0 else ""
        # Build the staged-index commit only when the staged tree differs from the worktree overlay (else the
        # staged content is already captured by `tree`); it becomes the snapshot commit's second parent.
        staged_commit = ""
        if staged_tree and staged_tree != tree:
            sct_args = ["commit-tree", staged_tree]
            if parent:
                sct_args += ["-p", parent]
            sct_args += ["-m", "aiqt-guardrails staged (index) snapshot before git {}".format(verb)]
            sct = _recovery_git(repo, sct_args, timeout=10)
            # ROUND-6 FINDING 6: the staged (index) commit-tree status is now CHECKED. Distinct staged-only
            # content EXISTS here (staged_tree != tree, the worktree overlay), and it lives ONLY on this
            # commit's tree, so if commit-tree FAILS the staged tree object is unreferenced (GC-eligible) and
            # the recovery ref would NOT preserve the staged-only payload. Advertising the snapshot anyway
            # would falsely claim a staged-only discard recoverable, so FAIL CLOSED (the caller DENIES),
            # matching the round-3 finding-3 handling of the pure-index write-tree failure.
            if sct.returncode != 0 or not sct.stdout.strip():
                return ("fail", "the staged (index) snapshot commit could not be created (git commit-tree on "
                                "the pure index failed) while distinct staged content is present, so a "
                                "staged-only discard would be unrecoverable")
            staged_commit = sct.stdout.strip()
        commit_args = ["commit-tree", tree]
        if parent:
            commit_args += ["-p", parent]  # parent HEAD when present; an unborn HEAD makes a rootless snapshot
        if staged_commit:
            commit_args += ["-p", staged_commit]  # second parent: the pre-command staged (index) state
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
    # ROUND-3 FINDING 8: the staged (index) commit is the snapshot commit's SECOND parent only when a HEAD
    # parent precedes it; in an UNBORN-HEAD repo there is no HEAD parent, so the staged commit is the FIRST
    # parent. Record which, so the recovery pointer advertises the parent that actually resolves to the
    # preserved staged content (previously it always said '^2', which does not exist on an unborn HEAD).
    staged_pointer = ("^2" if parent else "^1") if staged_commit else ""
    # ROUND-6 FINDING 7: the recovery ref lives in the TARGET repository's ref store (`top`), which may NOT be
    # the session cwd (a -C/--work-tree redirect, or a resolved cd target). Bind every advertised recovery
    # command to that repo with `git -C <top> ...`, so 'git checkout <ref> -- :/' run from a DIFFERENT session
    # cwd no longer fails to find the ref. `top` is the resolved toplevel; -C accepts it, and ref operations
    # resolve identically from anywhere in the repo. _recovery_pointer reads info['repo'] for the same binding.
    # ROUND-7 (codex finding 3): shell-quote the interpolated repo PATH in every advertised recovery command,
    # so a repo path containing a space or a shell metacharacter cannot break the command or inject when the
    # advertised command is run (shlex.quote; the ref is a guard-generated metacharacter-free token).
    return ("ok", {"ref": ref, "sha": sha, "classes": sorted(classes), "repo": top,
                   "restore": "git -C {} checkout {} -- :/".format(shlex.quote(top), ref),
                   "staged": bool(staged_commit), "staged_pointer": staged_pointer})


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
    and points to the isolated-branch form for an exact, deletion-inclusive restore. When the snapshot also
    captured a distinct STAGED (index) state (round-2 finding 5), the pre-command staged content is preserved
    as the ref's SECOND PARENT, so a `git restore --staged` / `git rm --cached` / default-`git reset` that
    discards staged-only content is recoverable from there too."""
    covered = ", ".join(info["classes"]) if info["classes"] else "the working tree"
    # ROUND-3 FINDING 8: use the recorded staged-parent pointer ('^2' with a HEAD parent, '^1' on an unborn
    # HEAD) so the pointer names the parent that actually resolves to the preserved staged content; default
    # to '^2' for a snapshot taken before this field existed.
    sp = info.get("staged_pointer") or "^2"
    # ROUND-6 FINDING 7: bind every advertised recovery command to the TARGET repository the snapshot was
    # taken against (info['repo'], the resolved toplevel), so a command copied from an ASK/DENY reason and run
    # from a DIFFERENT session cwd still finds the ref (a bare 'git checkout <ref> ...' from session A fails
    # when the ref lives in the redirected target T). `-C <repo>` is prefixed on the staged-recover and the
    # isolated-branch forms; info['restore'] already carries it (built in _take_snapshot). Snapshots taken
    # before this field existed fall back to a bare form (no regression).
    # ROUND-7 (codex finding 3): shell-quote the interpolated repo PATH so a path with a space or a shell
    # metacharacter cannot break or inject when the advertised recovery command is run.
    repo_c = " -C {}".format(shlex.quote(info["repo"])) if info.get("repo") else ""
    staged = (" The pre-command STAGED (index) state is preserved as the ref's {1} parent (recover a file "
              "with 'git{3} show {0}{2}:<path>' or 'git{3} checkout {0}{2} -- <path>').".format(
                  info["ref"], "first" if sp == "^1" else "second", sp, repo_c)
              if info.get("staged") else "")
    return ("A pre-command recovery snapshot was saved ({}) at ref {}; restore it with '{}' (overlay mode: "
            "it brings back modified and new content but does NOT re-apply a file deletion recorded in the "
            "snapshot), or for an exact, deletion-inclusive restore put it on an isolated branch with 'git{} "
            "switch -c aiqt-recover-<id> {}'.{}".format(
                covered, info["ref"], info["restore"], repo_c, info["ref"], staged))


def _record_stash_recovery(repo):
    """ROUND-2 FINDING 6. Preserve every stash entry under DURABLE refs/aiqt-recovery/ refs before a
    'git stash drop' / 'git stash clear', which is NOT reflog-recoverable after the fact (drop/clear delete
    the stash reflog entry, leaving the stash commit unreachable and GC-eligible). Returns:
      - ('none', None)  : the repo has NO stash entries, so a drop/clear loses nothing (allow).
      - ('ok', info)    : every stash entry was preserved under a durable ref; info = {refs, count}.
      - ('fail', reason): the repo/entries could not be enumerated, or a durable ref could not be written.
    Reads the stash reflog and writes only private refs (no working state) through the scrubbed _recovery_git
    primitive (every ambient GIT_* removed), so it keeps the inert, decoy-proof posture of _take_snapshot;
    the create-only update-ref backstops a name collision as a failure rather than an overwrite. Any
    snapshot-path fault is caught and downgraded to a ('fail') so it can never crash the guard."""
    try:
        top = _recovery_toplevel(repo)
        if not top:
            return ("fail", "the repository toplevel could not be resolved (a bare or broken git dir)")
        # `stash list` over the reflog: one line per entry. An absent refs/stash yields an empty list (no
        # stashes), a genuine 'none'. A non-zero return is a fault -> fail (never a false 'none').
        verify = _recovery_git(repo, ["rev-parse", "--verify", "-q", "refs/stash"], timeout=5)
        if verify.returncode != 0 or not verify.stdout.strip():
            return ("none", None)                     # no stash ref at all: nothing to preserve
        listing = _recovery_git(repo, ["reflog", "show", "--no-abbrev", "--format=%H", "refs/stash"],
                                timeout=10)
        if listing.returncode != 0:
            return ("fail", "the stash reflog could not be enumerated (rc={})".format(listing.returncode))
        shas = [ln.strip() for ln in listing.stdout.splitlines() if ln.strip()]
        if not shas:
            return ("none", None)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        refs = []
        for idx, sha in enumerate(shas):
            ref = "{}/{}-{}-stash{}".format(_RECOVERY_REF_NS, stamp, os.getpid(), idx)
            # create-only (expected-old ""): a colliding name fails rather than clobbering a prior recovery.
            if _recovery_git(repo, ["update-ref", ref, sha, ""], timeout=5).returncode != 0:
                return ("fail", "git update-ref for a stash recovery ref failed (a create-only collision or "
                                "a ref-store error), so a stash entry was not preserved")
            refs.append(ref)
    except (subprocess.SubprocessError, OSError, UnicodeDecodeError) as exc:
        return ("fail", "the stash recovery snapshot could not be taken ({}: {})".format(
            type(exc).__name__, exc))
    return ("ok", {"refs": refs, "count": len(refs)})


def _discard_recovery_result(kind, detail, snap, optout=None):
    """The SNAPSHOT-THEN-ALLOW outcome for a recoverable-destructive discard (hooks never ask). snap is
    ('ok', info) (a recovery snapshot was saved), ('fail', reason) (a warranted snapshot could NOT be made),
    or None (no snapshot warranted: a ref-level move or a stash/branch asset a working-tree snapshot cannot
    hold, which is typically reflog-recoverable). On 'ok' or None the discard is ALLOWED with an
    informational note; on 'fail' the discard would be unrecoverable, so it is DENIED-and-educated (commit or
    stash first). `optout` selects the path-aware opt-out guidance folded into the deny wording only (an
    allow needs none)."""
    if snap is not None and snap[0] == "fail":
        reason, _banner = _discard_ask_reason(kind, detail, optout)
        return _deny(
            reason + " NOTE: no pre-command recovery snapshot could be created ({}), so this discard would "
                     "be unrecoverable; it is denied rather than run. Commit or stash your work first, then "
                     "retry.".format(snap[1]),
            "AIQT guardrail: denied an unrecoverable git discard - no recovery snapshot could be created "
            "(rule prsunc); commit or stash first, then retry.")
    if snap is not None and snap[0] == "ok":
        return _allow_note(
            "AIQT guardrail (rule prsunc, preserve-uncommitted-work): {} {}. A pre-command recovery snapshot "
            "was saved, so it is allowed. {}".format(kind, detail, _recovery_pointer(snap[1])))
    return _allow_note(
        "AIQT guardrail (rule prsunc, preserve-uncommitted-work): {} {}. This guard could not auto-snapshot "
        "it (a ref-level or stash/branch asset a working-tree snapshot cannot capture, typically "
        "reflog-recoverable), so it is allowed; ensure any work you need is saved.".format(kind, detail))


def _stash_drop_clear_outcome(repo, stash_op):
    """Preserve every stash entry of `repo` under durable refs/aiqt-recovery/ refs, then return the no-ask
    decision for a 'git stash drop'/'clear' (ROUND-2 FINDING 6, generalized in round-3 finding 2 to the
    -C/compound/redirected forms): 'none' -> ALLOW (no entries to lose), 'ok' -> ALLOW-WITH-NOTE (entries
    preserved under durable refs), 'fail' -> DENY (the entries could not be preserved, so the discard would
    be unrecoverable). The caller has already established `repo` is the repository the command will actually
    clear (drop/clear is NOT reflog-recoverable afterwards, so a worktree snapshot cannot protect it)."""
    st = _record_stash_recovery(repo)
    if st[0] == "none":
        return _allow()  # no stash entries: drop/clear loses nothing
    if st[0] == "fail":
        return _deny(
            "AIQT rule prsunc (preserve-uncommitted-work): git stash {} discards saved stash entries and is "
            "NOT reflog-recoverable afterwards, and this guard could not preserve the stash entries under a "
            "recovery ref first ({}), so the discard would be unrecoverable; denied rather than run. Commit "
            "or apply your stash first, then retry. {}".format(stash_op, st[1], _DISCARD_ALTS),
            "AIQT guardrail: denied an unrecoverable git stash drop/clear - the stash entries could not be "
            "preserved (rule prsunc); apply or commit the stash first, then retry.")
    # ROUND-7 (codex finding 4): bind the advertised stash-recovery command to the TARGET repo with
    # 'git -C <repo> stash apply <ref>' (the stash refs live in `repo`, which may be a -C/redirected target,
    # not the session cwd, so a bare 'git stash apply' from the session cwd would not find them), and
    # shell-quote the repo PATH (codex finding 3) so a path with a space or metacharacter cannot break it.
    return _allow_note(
        "AIQT guardrail (rule prsunc, preserve-uncommitted-work): git stash {} discards saved stash entries "
        "(not reflog-recoverable afterwards). {} stash entr{} were first preserved under durable refs so they "
        "remain recoverable ({}); recover one with 'git -C {} stash apply <ref>'. It is allowed.".format(
            stash_op, st[1]["count"], "y" if st[1]["count"] == 1 else "ies", ", ".join(st[1]["refs"]),
            shlex.quote(repo)))


def _deny_with_recovery(kind, snap):
    """A DENY (a confirmed whole-tree clobber on a dirty tree) whose reason folds in the recovery outcome,
    mirroring _discard_recovery_result. The recovery text goes in through _discard_deny's parameter: a
    deny constructor's result is only ever returned, never unpacked and edited."""
    recovery = ""
    if snap is not None and snap[0] == "ok":
        recovery = " " + _recovery_pointer(snap[1])
    elif snap is not None and snap[0] == "fail":
        recovery = " NOTE: no pre-command recovery snapshot could be created ({}).".format(snap[1])
    return _discard_deny(kind, recovery)


def _cd_target_dir(tokens, cw, base):
    """Resolve the destination directory of a top-level `cd`/`pushd` segment (`cw` is the command word),
    against the current effective directory `base`. Returns an absolute-normalized directory path when it
    can be resolved simply, or None when it cannot (a `popd`, a bare `cd`/`pushd` with no operand, a `cd -`,
    a relative operand with no `base` to anchor it, or an operand this guard cannot treat as a literal path).
    Only a single simple literal operand is honoured; anything else is unresolvable so the caller treats the
    subsequent discard target as unresolved (round-3 finding 1: a discard after a cd it cannot resolve must
    not be snapshotted against the wrong directory)."""
    if cw == "popd":
        return None                                   # returns to an unknowable dir off the pushd stack
    cwidx = _command_word_index(tokens)
    operands = [t for t in tokens[cwidx + 1:] if not t.startswith("-")]
    if len(operands) != 1 or not operands[0] or operands[0] == "-":
        return None                                   # no operand (HOME/rotate), 'cd -', or ambiguous
    d = operands[0]
    if os.path.isabs(d):
        return os.path.normpath(d)
    if base is None:
        return None                                   # a relative cd with no cwd to anchor it
    return os.path.normpath(os.path.join(base, d))


def _segment_has_gitdir_redirect(tokens):
    """True when a git segment carries a --git-dir / GIT_DIR= redirect. Unlike --work-tree (which moves only
    the worktree), --git-dir/GIT_DIR names a DIFFERENT repository, so it changes which repo a `git stash
    drop`/`clear` acts on. The stash-recovery paths use this to REFUSE (deny) a stash discard whose target
    repository this guard cannot confidently resolve to a plain `git -C <dir>` invocation, rather than
    preserve the wrong repo's stash (round-3 finding 2)."""
    cw = _command_word_index(tokens)
    for tok in tokens[:cw]:
        if tok.startswith("GIT_DIR="):
            return True
    i = cw + 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if not tok.startswith("-"):
            break                                     # the subcommand
        if tok == "--git-dir" or tok.startswith("--git-dir="):
            return True
        if "=" not in tok and tok in _GIT_ARG_OPTS:   # a separated value-consuming global option: skip both
            i += 2
            continue
        i += 1
    return False


def _segment_gitdir_value(tokens):
    """The --git-dir / leading GIT_DIR= value (raw, unresolved) of a git segment, or None. Reads the leading
    GIT_DIR= env assignment and the git global --git-dir/--git-dir= option before the subcommand; last-wins
    for repeats, mirroring the shell/git. Used only to decide whether a --git-dir redirect names the SESSION
    repository (round-6 finding 5)."""
    cw = _command_word_index(tokens)
    val = None
    for tok in tokens[:cw]:
        if tok.startswith("GIT_DIR="):
            val = tok[len("GIT_DIR="):]  # last-wins
    i = cw + 1
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if not tok.startswith("-"):
            break
        if tok == "--git-dir":
            if i + 1 < n:
                val = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("--git-dir="):
            val = tok[len("--git-dir="):]
            i += 1
            continue
        if "=" not in tok and tok in _GIT_ARG_OPTS:
            i += 2
            continue
        i += 1
    return val


def _gitdir_is_session_repo(tokens, cwd):
    """ROUND-6 FINDING 5. True/False/None: whether a segment's --git-dir/GIT_DIR redirect names the SAME git
    repository as the session cwd, so a session-cwd worktree+index snapshot WOULD capture what the redirected
    command discards. Compares the realpath of the redirected git-dir (resolved against cwd) to the session
    repo's own absolute git dir (via the scrubbed rev-parse primitive, so an ambient decoy cannot redirect the
    probe). None when either side cannot be resolved (a fault) - the caller then fails closed. A --git-dir to a
    DIFFERENT repo means the destroyed INDEX/refs live elsewhere than the session worktree the snapshot
    captures, so a destructive discard there is unrecoverable via a session snapshot and must DENY."""
    val = _segment_gitdir_value(tokens)
    if not val:
        return None
    base = cwd if isinstance(cwd, str) and cwd else None
    if base is None:
        return None
    gd = val if os.path.isabs(val) else os.path.join(base, val)
    try:
        gd_c = os.path.realpath(gd)
    except (OSError, ValueError):
        return None
    try:
        r = _recovery_git(base, ["rev-parse", "--absolute-git-dir"], timeout=5)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        sess_c = os.path.realpath(r.stdout.strip())
    except (OSError, ValueError):
        return None
    return gd_c == sess_c


def _wrapped_git_index(tokens):
    """ROUND-6 FINDING 3. The index of the first STANDALONE 'git' command token (its basename is 'git') in a
    segment whose own command word is NOT git, i.e. a WRAPPED git invocation (env/sudo/xargs/timeout/... git
    <verb>), or None. Matches a real token equal to 'git' (or '/usr/bin/git'), so a 'git <verb>' buried inside
    a single QUOTED argument ('sh -c "git reset --hard"', 'eval "git reset"') has no standalone git token and
    is NOT matched here (that fragmented/quoted form stays the disclosed best-effort residual). Purely lexical."""
    for i, tok in enumerate(tokens):
        if tok.rsplit("/", 1)[-1] == "git":
            return i
    return None


def _segment_carries_target_redirect(tokens):
    """ROUND-6 FINDING 3. True when a segment carries a git TARGET-redirect token: a '-C', a
    --git-dir/--work-tree (bare or '='-attached), or a leading/inline GIT_DIR=/GIT_WORK_TREE= assignment. Used
    only for a WRAPPED git discard the walk cannot resolve as a depth-0 literal-git segment: such a redirect
    moves the discard OFF the session cwd, so the session-cwd best-effort snapshot cannot capture it and the
    discard fails closed (DENY). A wrapped discard with NO redirect acts on the effective cwd, which that
    best-effort snapshot covers, so it is not flagged here. Conservative token scan (over-detection only
    over-denies)."""
    for tok in tokens:
        if tok == "-C" or tok.startswith("--git-dir") or tok.startswith("--work-tree"):
            return True
        if tok.startswith("GIT_DIR=") or tok.startswith("GIT_WORK_TREE="):
            return True
    return False


def _nonpristine_discard_actions(segments, cwd):
    """ROUND-3 FINDINGS 1 and 2. Walk a NON-PRISTINE in-scope command's segments and resolve, for each
    VISIBLE git discard segment, the effective worktree/repository the discard ACTUALLY acts on, so the
    recovery snapshot (a worktree discard) or the stash preservation (a `git stash drop`/`clear`) lands on
    that target rather than blindly on the session cwd. The shell cwd is tracked across TOP-LEVEL `cd`/`pushd`
    (a `&&`-gated cd applies to the next command with certainty; a `;`/`||`-sequenced one does NOT gate
    success so the cwd becomes uncertain; a backgrounded `cd &` and a cd inside a subshell `( )` do NOT change
    the foreground cwd), and each git segment's own -C/--work-tree/GIT_WORK_TREE= redirect is resolved against
    that cwd. Returns a dict:
      { 'snapshot_bases': [dir, ...]  # distinct worktree dirs a snappable destructive discard acts on
        'stash_ops':      [(dir, op)] # each stash drop/clear paired with the repo dir it acts on
        'unresolved':     bool        # a visible depth-0 discard whose target could NOT be resolved
        'hidden':         bool        # ROUND-6 FINDING 3: a lossy discard the walk cannot resolve to a
                                      #   snapshottable target - a git inside a subshell whose cwd a prior
                                      #   internal cd moved, or a WRAPPED git (env/sudo/... git) carrying a
                                      #   -C/--git-dir/--work-tree/GIT_DIR=/GIT_WORK_TREE= redirect - so its
                                      #   target cannot be snapshotted with certainty
        'saw_actionable': bool }      # any visible worktree-destructive or stash drop/clear discard
    A caller with unresolved=True OR hidden=True DENIES (fail closed: it cannot snapshot/preserve the exact
    target). Where a
    destructive segment acts on the plain session cwd (no cd, no redirect) that cwd is added to
    snapshot_bases so it is snapshotted through the same path; where no actionable discard is visible
    (obfuscated verbs, soft/ref-level forms, or an unknown cwd) saw_actionable is False and the caller keeps
    its existing best-effort session-cwd snapshot."""
    depth = 0
    session_cwd = cwd if isinstance(cwd, str) and cwd else None
    eff = session_cwd
    eff_certain = True
    cd_happened = False
    snapshot_bases = []
    stash_ops = []
    unresolved = False
    saw_actionable = False
    targets_session = False
    hidden = False   # ROUND-6 FINDING 3: a lossy discard the walk cannot resolve as a depth-0 literal-git
    # segment (a subshell-internal cd moved its target, or a WRAPPED git carries a target redirect) -> DENY.
    subshell_cd_seen = False  # a cd/pushd occurred at depth>0 in the current subshell nesting: a git discard
    # then running inside that subshell acts on a moved, un-modelled cwd. Reset when depth returns to 0.

    def _add(seq, item):
        if item not in seq:
            seq.append(item)

    for tokens, sep in segments:
        cw = _command_word(tokens)
        if cw == "git":
            sub, args = _git_sub_and_args(tokens)
            if sub is not None:
                role, _kind = _discard_role(sub, args)
                if role != "allow":
                    if depth != 0 and subshell_cd_seen:
                        # ROUND-6 FINDING 3: a lossy git discard inside a subshell '( ... )' where a cd/pushd
                        # earlier in that subshell moved the cwd the walk does not model (e.g.
                        # '(cd T && git restore -- f)'), so its target cannot be resolved and the session
                        # snapshot would not capture it -> fail closed (the caller denies). A subshell/command
                        # substitution with NO internal cd runs at the foreground cwd, so it falls through to
                        # the normal resolution below and is snapshot-backed there (the C6 design).
                        hidden = True
                        saw_actionable = True
                    elif sub in _SNAPSHOTTABLE_VERBS:
                        saw_actionable = True
                        if not eff_certain:
                            unresolved = True
                        elif _segment_has_worktree_redirect(tokens):
                            # ROUND-7 (codex finding 1): --work-tree relocates ONLY the worktree, not the repo.
                            # An INDEX/REF-only discard (restore --staged, mixed reset, rm --cached) acts on the
                            # ambient (or -C) repo, so snapshot THAT via _segment_repo_dir (--work-tree ignored).
                            # A WORKTREE-CONTENT discard destroys content in the --work-tree dir while the index/
                            # refs stay in the ambient repo: the split cannot be captured by one snapshot, so it
                            # is unresolved (the caller DENIES) rather than snapshotting the wrong --work-tree.
                            if _discard_index_only(sub, args):
                                rd = _segment_repo_dir(tokens, eff)
                                if rd == "opaque":
                                    unresolved = True
                                elif isinstance(rd, str):
                                    _add(snapshot_bases, rd)
                                elif eff is None:
                                    if cd_happened:
                                        unresolved = True
                                else:
                                    _add(snapshot_bases, eff)
                            else:
                                unresolved = True
                        else:
                            wt = _segment_redirect_worktree(tokens, eff)
                            if wt == "opaque":
                                unresolved = True
                            elif wt is not None:
                                _add(snapshot_bases, wt)
                            elif eff is None:
                                if cd_happened:
                                    unresolved = True     # a cd to an unresolvable dir preceded the discard
                                # else: cwd unknown -> keep the best-effort session path (not unresolved)
                            elif cd_happened:
                                _add(snapshot_bases, eff)  # a cd'd-into concrete dir
                            else:
                                targets_session = True     # plain session cwd
                    elif sub == "stash":
                        stash_op = next((a for a in args if not a.startswith("-")), None)
                        if stash_op in ("drop", "clear"):
                            saw_actionable = True
                            if not eff_certain or _segment_has_gitdir_redirect(tokens):
                                unresolved = True
                            else:
                                # ROUND-7 (codex finding 1): stash is a REPO/ref op; its target repo is the
                                # ambient (or -C) repo, NOT the --work-tree value, so resolve via
                                # _segment_repo_dir (--work-tree ignored) and preserve THAT repo's stash.
                                rd = _segment_repo_dir(tokens, eff)
                                if rd == "opaque":
                                    unresolved = True
                                elif isinstance(rd, str):
                                    _add(stash_ops, (rd, stash_op))
                                elif eff is None:
                                    if cd_happened:
                                        unresolved = True
                                    # else: cwd unknown -> best-effort session path
                                else:
                                    _add(stash_ops, (eff, stash_op))
                    # a force branch delete/move/copy/reset or stash export is ref-level/reflog-recoverable:
                    # a worktree snapshot cannot capture it, so no target is resolved here (the existing
                    # allow-note covers it).
        elif depth == 0 and cw in ("cd", "pushd", "popd"):
            if sep == "&":
                pass                                   # backgrounded: no foreground cwd change
            elif sep in ("&&", ""):
                nd = _cd_target_dir(tokens, cw, eff)
                eff = nd                               # a resolvable dir, or None (subsequent target opaque)
                cd_happened = True
            else:                                      # ';' or '||': cd success does not gate the next
                eff_certain = False
        elif cw in ("cd", "pushd", "popd"):
            # a cd/pushd at depth>0 (a subshell-internal cd, e.g. '(cd T && ...)'): the walk does not model
            # the subshell's cwd, so note it, and a later same-subshell git discard fails closed (finding 3).
            subshell_cd_seen = True
        else:
            # ROUND-6 FINDING 3: a segment whose command word is neither git nor a cd-family builtin. A
            # STANDALONE 'git' token here is a WRAPPED git discard (env/sudo/xargs/timeout/... git <verb>).
            # When it carries a target redirect (-C/--git-dir/--work-tree/GIT_DIR=/GIT_WORK_TREE=), or runs
            # inside a subshell whose cwd a prior internal cd moved, its target is off the session cwd (or
            # unmodellable) and the session snapshot would not capture it, so it cannot be resolved+snapshotted
            # -> fail closed. A wrapped discard with NO redirect and no moved-subshell-cwd acts on the effective
            # cwd, which the best-effort session snapshot covers, so it is NOT flagged (e.g. 'env git reset
            # --hard', or '$(echo git checkout -f)' which runs at the foreground cwd). A git verb
            # fragmented/quoted into a single token (no standalone 'git') stays the disclosed best-effort residual.
            gi = _wrapped_git_index(tokens)
            if gi is not None:
                wsub, wargs = _git_sub_and_args(tokens[gi:])
                if wsub is not None:
                    wrole, _wk = _discard_role(wsub, wargs)
                    if wrole != "allow" and (_segment_carries_target_redirect(tokens)
                                             or (depth != 0 and subshell_cd_seen)):
                        hidden = True
                        saw_actionable = True
        if sep == "(":
            depth += 1
        elif sep == ")":
            depth = max(0, depth - 1)
            if depth == 0:
                subshell_cd_seen = False   # left the subshell nesting: its internal cd no longer applies

    if targets_session and session_cwd is not None:
        _add(snapshot_bases, session_cwd)              # a plain session-cwd discard: snapshot the cwd too
    return {"snapshot_bases": snapshot_bases, "stash_ops": stash_ops, "hidden": hidden,
            "unresolved": unresolved, "saw_actionable": saw_actionable}


def git_discard(data):
    """prsunc (integ/preserve-uncommitted-work), PreToolUse/Bash. ULTRA-CONSERVATIVE "recover then allow"
    guard (EN-6). NO-ASK posture: every former ASK is resolved WITHOUT prompting - a recoverable-destructive
    discard is SNAPSHOT-THEN-ALLOWED (an inert refs/aiqt-recovery/ snapshot, then allow with a recovery note),
    and it DENIES-and-educates only when a warranted recovery snapshot cannot be made (an unrecoverable
    discard) or the command cannot be classified/resolved (an inline alias, an unrecognized flagged
    subcommand, an unresolvable worktree); a confirmed whole-tree clobber on a dirty tree still DENIES (with a
    snapshot when one could be made). Read every "ASK" below as that recover-then-allow/deny resolution. For a
    command that names any recognized lossy git verb (checkout incl
    -B force-create, switch incl -C/--force-create, restore/reset/clean/stash drop-clear/rm/branch force
    delete/move/copy/reset) the outcome is ASK(->recover-then-allow/deny) unless the command
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
    recognized set ('git checkout-index -a -f', 'git read-tree -u --reset HEAD') is DENIED-and-educated (F-97;
    historically this ASKED): a flagged sub the classifier cannot resolve to a known verb cannot be proven
    non-destructive and cannot be snapshotted, so it never wins the catch-all allow. The status probe (git
    status --porcelain,
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
    # ROUND-2 FINDING 17: the unconditional raw scan runs on a copy with QUOTED-heredoc bodies stripped, so a
    # lossy-verb keyword quoted inside heredoc prose (a 'git reset --hard' example in a here-doc) does not
    # falsely route the harmless outer command (e.g. 'cat <<'EOF'...') into the discard machinery. A real
    # lossy verb OUTSIDE the heredoc body is preserved and still caught.
    raw_lossy = _raw_has_lossy_git(_strip_quoted_heredoc_bodies(command))
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
        np_verb = next(iter(sorted(np_subs & _SNAPSHOTTABLE_VERBS)), "discard")
        # ROUND-3 FINDINGS 1 and 2: resolve the effective worktree/repository EACH visible discard acts on
        # (tracking top-level cd/pushd and each git segment's own -C/--work-tree/GIT_WORK_TREE= redirect), so
        # the snapshot/stash-preservation lands on the repo the command will actually mutate, not blindly on
        # the session cwd. A `git -C T restore ...; :` or `cd T && git restore ...` now snapshots T, and a
        # `git -C T stash clear` or `git stash clear; :` now preserves the stash of the repo it clears. Where
        # the effective target cannot be resolved with certainty (an unresolvable redirect/cd, a ;/||-gated
        # cd whose success is not guaranteed, or a --git-dir/GIT_DIR-redirected stash), the discard DENIES
        # (fail closed) rather than note a recovery that would not contain the discarded state.
        actions = _nonpristine_discard_actions(segments, np_base)
        if actions["unresolved"]:
            return _deny(
                "AIQT rule prsunc (preserve-uncommitted-work): {} runs in a compound/redirected command "
                "whose effective target worktree or repository this guard cannot resolve with certainty (an "
                "unresolvable -C/--work-tree/GIT_WORK_TREE= or --git-dir/GIT_DIR redirect, or a cd/pushd "
                "whose success does not gate the discard), so it cannot snapshot or preserve the exact target "
                "the command will discard from; denied rather than run on a possibly unrecoverable discard. "
                "Re-issue it as a plain 'git <verb>' command from the target repository, or commit or stash "
                "your work first. {}".format(kind, _DISCARD_ALTS),
                "AIQT guardrail: denied a compound/redirected git discard whose target this guard cannot "
                "resolve to snapshot (rule prsunc); run it from the target repo, or commit or stash first.")
        if actions["hidden"]:
            # ROUND-6 FINDING 3: a lossy discard the walk could not resolve as a depth-0 literal-git segment
            # (inside a subshell '( ... )', or a WRAPPED 'git' carrying a -C/--git-dir/--work-tree/GIT_DIR=/
            # GIT_WORK_TREE= redirect off the session cwd). Its target cannot be snapshotted with certainty and
            # the best-effort session-cwd snapshot would not capture it, so it fails closed: DENY-and-educate
            # (re-issue it as a plain, unwrapped 'git <verb>' command from the target repository).
            return _deny(
                "AIQT rule prsunc (preserve-uncommitted-work): {} runs inside a subshell, or as a wrapped "
                "'git' invocation (env/sudo/... git) carrying a -C/--git-dir/--work-tree/GIT_DIR=/"
                "GIT_WORK_TREE= redirect, so its effective target worktree or repository is off the session "
                "directory and this guard cannot resolve or snapshot it with certainty; denied rather than "
                "run on a possibly unrecoverable discard. Re-issue it as a plain, unwrapped 'git <verb>' "
                "command from the target repository, or commit or stash your work first. {}"
                .format(kind, _DISCARD_ALTS),
                "AIQT guardrail: denied a subshell/wrapped-and-redirected git discard whose target this guard "
                "cannot resolve to snapshot (rule prsunc); run it unwrapped from the target repo, or commit or "
                "stash first.")
        # Preserve the stash of every RESOLVED stash drop/clear target repo first (fail closed on a repo whose
        # stash cannot be preserved), so a `git -C T stash clear` / `git stash clear; :` no longer notes a
        # recovery that omits the cleared stash (round-3 finding 2).
        for _b, _op in actions["stash_ops"]:
            _st = _record_stash_recovery(_b)
            if _st[0] == "fail":
                return _deny(
                    "AIQT rule prsunc (preserve-uncommitted-work): git stash {} would discard the saved "
                    "stash entries of {} (not reflog-recoverable afterwards), which this guard could not "
                    "preserve first ({}), so the discard would be unrecoverable; denied rather than run. "
                    "Apply or commit the stash first, then retry. {}"
                    .format(_op, _b, _st[1], _DISCARD_ALTS),
                    "AIQT guardrail: denied an unrecoverable compound/redirected git stash drop/clear (rule "
                    "prsunc); apply or commit the stash first, then retry.")
        # Snapshot every worktree the command discards from: each RESOLVED redirect/cd target (round-3 finding
        # 1) PLUS the session cwd itself, which stays a best-effort catch-all because a snappable verb hidden
        # by shell quoting/eval/substitution may still discard the cwd (the rec-c6 residual). Any warranted
        # snapshot that FAILS denies (fail closed), never a note over a target with no recovery point.
        _bases = list(actions["snapshot_bases"])
        if np_base is not None and np_base not in _bases:
            _bases.append(np_base)
        np_snap = None
        for _b in _bases:
            if _tree_is_clean(_b) is not True:
                _s = _record_recovery(_b, np_verb)
                if _s[0] == "fail":
                    return _deny(
                        "AIQT rule prsunc (preserve-uncommitted-work): {} would discard from {}, which this "
                        "guard could not snapshot ({}), so the discard would be unrecoverable; denied rather "
                        "than run. Re-issue it from the target repository, or commit or stash your work "
                        "first. {}".format(kind, _b, _s[1], _DISCARD_ALTS),
                        "AIQT guardrail: denied an unrecoverable compound/redirected git discard (rule "
                        "prsunc); run it from the target repo, or commit or stash first.")
                if np_snap is None and _s[0] == "ok":
                    np_snap = _s
        return _discard_recovery_result(
            kind, "is not a pristine single bare 'git <verb>' invocation (it carries a shell "
                  "metacharacter, wrapper, redirect, reserved word, a second command, or a command word "
                  "that is not literally 'git'); a recovery snapshot targets the effective worktree(s) it "
                  "resolved plus the session directory before allowing", np_snap, _OPTOUT_REISSUE)

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
    ambient_override = _ambient_repo_view_override()
    if ambient_override or not _segment_dir_simple(pristine):
        ao_cwd = data.get("cwd")
        cwd_base = ao_cwd if isinstance(ao_cwd, str) and ao_cwd else None
        # ROUND-2 FINDING 4: for a COMMAND-LOCAL worktree redirect (-C/--work-tree/GIT_WORK_TREE=) with NO
        # ambient GIT_* override, resolve the ACTUAL target worktree and snapshot THAT repo, not the session
        # cwd, so the recovery ref contains the state the command will discard. If the redirect target cannot
        # be resolved, or a destructive verb's target cannot be snapshotted, DENY - never allow-note a discard
        # whose recovery ref does not contain the discarded state. A --git-dir/GIT_DIR/-c redirect (which does
        # NOT move the worktree) resolves to None and keeps the sound session-cwd snapshot; the ambient-GIT_*
        # case (unreadable from the command) keeps its disclosed best-effort session-cwd snapshot.
        # ROUND-7 (codex findings 1 and 2): resolve the REPO git's index/ref/stash op acts on via
        # _segment_repo_dir (-C/ambient, --work-tree treated as worktree-only), NOT _segment_redirect_worktree
        # (which returned the --work-tree VALUE and so landed the snapshot/stash/probe on the wrong repo). A
        # --work-tree relocates only the worktree; the WORKTREE-CONTENT-loss case it introduces is failed
        # closed just below.
        repo_dir = None if ambient_override else _segment_repo_dir(pristine, cwd_base)
        has_wt = False if ambient_override else _segment_has_worktree_redirect(pristine)
        destructive = sub in _SNAPSHOTTABLE_VERBS and role != "allow"
        # CLAUDE-F1 (round-7): a discard that entered this view-override branch (an ambient GIT_* view-override,
        # OR a command-local -C/--git-dir/--work-tree/-c/inline-GIT_*= redirect) whose raw scan flagged a lossy
        # keyword but whose RESOLVED subcommand is OUTSIDE the recognized lossy-verb set - an inline
        # '-c alias.<name>=' that may expand to a work-losing verb, or a redirected/ambient 'checkout-index'/
        # 'read-tree' - cannot be proven non-destructive and cannot be snapshotted, so it FAILS CLOSED here,
        # mirroring the F-97 deny the dir-simple path applies below. Without this it returned an allow-note with
        # NO snapshot from this branch (uncommitted work destroyed unrecoverably), and the round-6 inline-alias
        # deny below was unreachable (a '-c' global option always makes _segment_dir_simple False, routing the
        # command here). A RECOGNIZED verb under a -C/ambient redirect still snapshots-then-allows/denies below.
        if raw_lossy and sub is not None and sub not in _RECOGNIZED_VERBS:
            return _deny(
                "AIQT rule prsunc (preserve-uncommitted-work): this command resolves to the git subcommand "
                "{!r}, which is outside the recognized lossy-verb set "
                "(checkout/switch/restore/reset/rm/clean/stash/branch), and it carries a repository-view "
                "redirect (a non-cosmetic ambient GIT_* variable, or a command-local -C/--git-dir/--work-tree/"
                "-c or inline GIT_DIR=/GIT_WORK_TREE= assignment, e.g. an inline '-c alias.<name>=' or a "
                "redirected 'checkout-index'/'read-tree'); this guard cannot prove it non-destructive and "
                "cannot snapshot it, so it is denied rather than run on a possible discard. Re-issue as a "
                "recognized, provable form, or commit or stash your work first. {}".format(sub, _DISCARD_ALTS),
                "AIQT guardrail: denied a redirected git command whose subcommand this guard cannot prove "
                "non-destructive (rule prsunc); commit or stash first, or re-issue in a recognized form.")
        # ROUND-6 FINDING 5: a destructive discard carrying a --git-dir/GIT_DIR redirect (which does NOT move
        # the worktree, so redir_wt is None and the code below would snapshot the SESSION worktree) destroys
        # the REDIRECTED repository's INDEX/refs - which a session-worktree+index snapshot does NOT capture -
        # when that git-dir names a DIFFERENT repository than the session cwd (e.g. 'git --git-dir=T/.git
        # restore --staged'). Resolving the worktree alone cannot identify the redirected index, so a
        # --git-dir staged/worktree discard whose git-dir is not PROVABLY the session repo DENIES rather than
        # allow-note a session snapshot lacking that index. A --git-dir naming the SAME repo as cwd (the
        # session index IS the one discarded) still allows via the session snapshot below (dir-e).
        if destructive and _segment_has_gitdir_redirect(pristine):
            if _gitdir_is_session_repo(pristine, cwd_base) is not True:
                return _deny(
                    "AIQT rule prsunc (preserve-uncommitted-work): {} carries a --git-dir/GIT_DIR redirect to "
                    "a repository this guard cannot prove is the session repository, so the index and refs "
                    "the command would discard live in a DIFFERENT repository than the session worktree; a "
                    "session snapshot cannot capture that redirected index, so this discard could be "
                    "unrecoverable and is denied rather than run. Re-issue it as a plain 'git -C <repo> "
                    "<verb>' command run from the target repository, or commit or stash your work first. {}"
                    .format(kind or "a git work-losing verb", _DISCARD_ALTS),
                    "AIQT guardrail: denied a --git-dir/GIT_DIR-redirected git discard whose repository this "
                    "guard cannot resolve to snapshot (rule prsunc); run it from the target repo, or commit "
                    "or stash first.")
        # ROUND-7 (codex finding 1): a --work-tree/GIT_WORK_TREE redirect relocates ONLY the worktree; the
        # index/refs/HEAD stay in the ambient (or --git-dir) repository. A WORKTREE-CONTENT discard (checkout/
        # switch/clean, a worktree restore, reset --hard/--merge/--keep) under such a redirect destroys content
        # in the --work-tree directory, which a snapshot of the ambient repo does NOT capture, so the two are
        # split and a single recovery snapshot cannot hold them together: it FAILS CLOSED. The sole exception
        # is a redundant self-reference whose --work-tree IS the repo's own toplevel (e.g.
        # 'git --work-tree=<repo> --git-dir=<repo>/.git reset --hard'), which the ambient snapshot DOES capture.
        # An INDEX/REF-only discard (restore --staged, mixed reset, rm --cached) acts on the ambient repo and
        # is snapshotted correctly via repo_dir below; stash (ref-level) is handled separately below.
        if has_wt and destructive and not _discard_index_only(sub, args):
            wt_dir = _segment_redirect_worktree(pristine, cwd_base)
            repo_probe = repo_dir if isinstance(repo_dir, str) and repo_dir != "opaque" else cwd_base
            if not _worktree_within_repo(wt_dir, repo_probe):
                return _deny(
                    "AIQT rule prsunc (preserve-uncommitted-work): {} carries a --work-tree/GIT_WORK_TREE "
                    "redirect that relocates ONLY the working tree, so git discards WORKING-TREE content from "
                    "that directory while the index and refs stay in the ambient (or --git-dir) repository; "
                    "a single recovery snapshot cannot capture that split state, so this guard cannot snapshot "
                    "the exact content the command would discard and denies rather than run on a possibly "
                    "unrecoverable discard. Re-issue it as a plain 'git <verb>' command run FROM the target "
                    "working tree (without --work-tree), or commit or stash your work first. {}"
                    .format(kind or "a git work-losing verb", _DISCARD_ALTS),
                    "AIQT guardrail: denied a --work-tree-redirected worktree-content git discard whose split "
                    "index/worktree state this guard cannot snapshot (rule prsunc); run it from the target "
                    "worktree, or commit or stash first.")
        # ROUND-3 FINDING 2: a redirected/ambient 'git stash drop'/'clear' must PRESERVE the stash of the
        # repository it actually clears (drop/clear is NOT reflog-recoverable afterwards) or DENY when that
        # repository cannot be resolved. stash is not snapshottable, so the destructive/worktree logic below
        # does not cover it; a `git -C T stash clear` used to allow-note with NO stash preservation. Resolve
        # the target repo from the -C/--work-tree redirect and preserve there; an ambient GIT_* view-override,
        # an opaque redirect, or a --git-dir/GIT_DIR naming a repo this guard cannot map -> DENY.
        if sub == "stash":
            stash_op = next((a for a in args if not a.startswith("-")), None)
            if stash_op in ("drop", "clear"):
                if ambient_override or repo_dir == "opaque" or _segment_has_gitdir_redirect(pristine):
                    return _deny(
                        "AIQT rule prsunc (preserve-uncommitted-work): git stash {} discards saved stash "
                        "entries (not reflog-recoverable afterwards) and this command carries an ambient "
                        "GIT_* view-override or a --git-dir/GIT_DIR redirect naming a repository this guard "
                        "cannot resolve, so it cannot preserve the stash the command will actually clear; "
                        "denied rather than run on an unrecoverable discard. Re-issue it as a plain git "
                        "command from the target repository, or apply or commit the stash first. {}"
                        .format(stash_op, _DISCARD_ALTS),
                        "AIQT guardrail: denied a redirected git stash drop/clear whose repository this guard "
                        "cannot resolve to preserve (rule prsunc); run it from the target repo, or apply or "
                        "commit the stash first.")
                # ROUND-7 (codex finding 1): stash is a REPO/ref op; its target repo is the ambient (or -C)
                # repository, NOT the --work-tree value, so resolve it via repo_dir (--work-tree ignored).
                stash_repo = repo_dir if isinstance(repo_dir, str) else cwd_base
                if stash_repo is None:
                    return _deny(
                        "AIQT rule prsunc (preserve-uncommitted-work): git stash {} discards saved stash "
                        "entries and is NOT reflog-recoverable afterwards, and no target repository could be "
                        "resolved to preserve them first; denied rather than run on an unrecoverable discard. "
                        "Re-issue it from the target repository, or leave the stash in place. {}"
                        .format(stash_op, _DISCARD_ALTS),
                        "AIQT guardrail: denied a git stash drop/clear with no resolvable repository to "
                        "preserve the stash first (rule prsunc).")
                return _stash_drop_clear_outcome(stash_repo, stash_op)
        if repo_dir == "opaque":
            if destructive:
                return _deny(
                    "AIQT rule prsunc (preserve-uncommitted-work): {} carries a command-local repository "
                    "redirect (a -C target) whose location this guard cannot resolve, so it "
                    "cannot snapshot the repository the command will actually discard from; denied rather "
                    "than run on a possibly unrecoverable discard. Re-issue it as a plain git command from "
                    "the target repository, or commit or stash your work first. {}".format(
                        kind or "a git work-losing verb", _DISCARD_ALTS),
                    "AIQT guardrail: denied a repository-redirected git discard whose target this guard cannot "
                    "resolve to snapshot (rule prsunc); run it from the target repo, or commit or stash first.")
            return _allow_note(
                "AIQT guardrail (rule prsunc, preserve-uncommitted-work): {} carries a command-local "
                "repository redirect this guard cannot resolve, but it is a non-destructive form, so it is "
                "allowed.".format(kind or "a git command"))
        # ROUND-7 (codex findings 1/2): snapshot the REPO git acts on (repo_dir: a -C target, or the session
        # cwd when only --work-tree/--git-dir/-c is present), never the --work-tree value.
        target_base = repo_dir if isinstance(repo_dir, str) else cwd_base
        target_desc = ("the -C target ({})".format(repo_dir)
                       if isinstance(repo_dir, str) and repo_dir != cwd_base else "the session directory")
        ao_snap = None
        if target_base is not None and destructive and _tree_is_clean(target_base) is not True:
            ao_snap = _record_recovery(target_base, sub)
            if ao_snap is not None and ao_snap[0] == "fail":
                return _deny(
                    "AIQT rule prsunc (preserve-uncommitted-work): {} would discard from {}, which this "
                    "guard could not snapshot ({}), so the discard would be unrecoverable; denied rather "
                    "than run. Re-issue it as a plain git command from the target repository, or commit or "
                    "stash your work first. {}".format(kind or "a git work-losing verb", target_desc,
                                                       ao_snap[1], _DISCARD_ALTS),
                    "AIQT guardrail: denied an unrecoverable worktree-redirected git discard (rule prsunc); "
                    "run it from the target repo, or commit or stash first.")
        return _discard_recovery_result(
            kind or "a git work-losing verb",
            "runs under a non-cosmetic ambient GIT_* variable or carries a command-local redirect "
            "(-C/--git-dir/--work-tree or an inline GIT_DIR=/GIT_WORK_TREE= assignment); the recovery "
            "snapshot targets {} (a --git-dir/GIT_DIR alone leaves the worktree at the session directory)"
            .format(target_desc), ao_snap, _OPTOUT_PRISTINE)

    if role == "allow" and any(t.lower().startswith(("alias.", "-calias.")) for t in pristine):
        return _deny(
            "AIQT rule prsunc (preserve-uncommitted-work): this command sets a git inline alias "
            "('-c alias.<name>=...') that may expand to a work-losing verb this guard cannot resolve or "
            "snapshot; denied rather than run on an unknown discard. Re-issue the command the alias expands "
            "to explicitly (without '-c alias.<name>='), so this guard can classify it, or commit or stash "
            "your work first. {}".format(_DISCARD_ALTS),
            "AIQT guardrail: denied an unresolvable git inline alias that may discard work (rule prsunc); "
            "run the aliased command explicitly, or commit or stash first.")

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
        return _deny(
            "AIQT rule prsunc (preserve-uncommitted-work): this command resolves to the git subcommand "
            "{!r}, which is outside the recognized lossy-verb set "
            "(checkout/switch/restore/reset/rm/clean/stash/branch); this guard cannot prove it "
            "non-destructive and cannot snapshot it, so it is denied rather than run on a possible discard. "
            "Re-issue as a recognized, provable form, or commit or stash your work first. {}"
            .format(sub, _DISCARD_ALTS),
            "AIQT guardrail: denied a git command whose subcommand this guard cannot prove non-destructive "
            "(rule prsunc); commit or stash first, or re-issue in a recognized form.")

    if role == "allow":
        # A genuinely non-destructive bare form (bare no-op, reset --soft, unforced -b, plain switch, clean
        # dry-run, stash pop). FAIL POSTURE: if a snapshot was warranted (a not-provably-clean snapshottable
        # tree, a defensive backstop against a mis-parse) and it FAILED, downgrade the allow to ASK - never
        # silent-allow a not-provably-clean discard with no recovery point. Otherwise ALLOW stands.
        if snap is not None and snap[0] == "fail":
            return _deny(
                "AIQT rule prsunc (preserve-uncommitted-work): {} would run on a working tree that is not "
                "provably clean and no recovery point could be created ({}), so this discard would be "
                "unrecoverable; denied rather than run. Commit or stash your work first, then retry. {}"
                .format(kind or "a git work-losing verb", snap[1], _DISCARD_ALTS),
                "AIQT guardrail: denied an unrecoverable git discard - no recovery snapshot could be created "
                "(rule prsunc); commit or stash first, then retry.")
        return _allow()

    if role == "ask":
        # A softer discard (a real clean of untracked files, stash drop/clear, a force branch delete/move/
        # copy/reset): the tracked-tree probe does not see the asset these verbs destroy.
        if sub == "clean":
            # ROUND-2 FINDING 7: 'clean' is snapshottable (git add --all captures untracked) and destroys
            # WORKING-TREE CONTENT, so a clean whose worktree cannot be snapshotted is unrecoverable and must
            # DENY for parity with reset/checkout - never an allow-note with no recovery point (the old
            # role-ask fall-through allow-noted an unresolvable-worktree clean while reset/checkout denied it).
            if clean is True:
                return _allow()  # provably clean: nothing to remove, no snapshot needed
            if not resolvable:
                return _deny(
                    "AIQT rule prsunc (preserve-uncommitted-work): {} targets a working tree this guard "
                    "cannot resolve to the session directory with certainty, so it can neither prove the "
                    "tree clean nor take a recovery snapshot; denied rather than run on a possible "
                    "unrecoverable discard of untracked content. Re-issue it as a plain git command from the "
                    "target repository, or commit or stash your work first. {}".format(kind, _DISCARD_ALTS),
                    "AIQT guardrail: denied a git clean whose working tree this guard cannot resolve to "
                    "snapshot (rule prsunc); re-issue from the target repo, or commit or stash first.")
            # resolvable + not provably clean: a snapshot was taken above; fold its outcome in (ok ->
            # allow-note, fail -> deny), so a clean with no recovery point never silently allows.
            return _discard_recovery_result(kind, "removes untracked files, which cannot be recovered", snap)
        if sub == "stash":
            # ROUND-2 FINDING 6: 'git stash drop'/'clear' is NOT reflog-recoverable after the fact (it deletes
            # the stash reflog entry, orphaning the stash commit), so a worktree snapshot cannot protect it.
            # Preserve every stash entry under DURABLE refs/aiqt-recovery/ refs FIRST, then allow; deny if the
            # entries cannot be enumerated or preserved. 'stash export'/other forms discard no stash entry and
            # keep their allow-note. ('stash' is not snapshottable, so `snap` is None here.)
            stash_op = next((a for a in args if not a.startswith("-")), None)
            if stash_op in ("drop", "clear"):
                if base is None:
                    return _deny(
                        "AIQT rule prsunc (preserve-uncommitted-work): git stash {} discards saved stash "
                        "entries and is NOT reflog-recoverable afterwards, and no session working directory "
                        "is available to preserve the stash entries first; denied rather than run on an "
                        "unrecoverable discard. Re-issue it from the target repository, or leave the stash "
                        "in place. {}".format(stash_op, _DISCARD_ALTS),
                        "AIQT guardrail: denied an unrecoverable git stash drop/clear with no session "
                        "directory to preserve the stash first (rule prsunc).")
                return _stash_drop_clear_outcome(base, stash_op)
            # stash export / other ask-classified stash forms discard no stash entry: keep the allow-note.
            return _discard_recovery_result(kind, "cannot be proven safe offline", snap)
        # A force branch delete/move/copy/reset: a branch ref is a separate, reflog-recoverable asset a
        # worktree snapshot cannot capture, so it keeps the allow-note.
        return _discard_recovery_result(kind, "cannot be proven safe offline", snap)

    # A scoped or clobber form (all snapshottable): gate on the clean probe, which must resolve to the
    # session worktree.
    if not resolvable:
        return _deny(
            "AIQT rule prsunc (preserve-uncommitted-work): {} targets a working tree this guard cannot "
            "resolve to the session directory with certainty, so it can neither prove the tree clean nor "
            "take a recovery snapshot; denied rather than run on a possible unrecoverable discard. Re-issue "
            "it as a plain git command from the target repository, or commit or stash your work first. {}"
            .format(kind, _DISCARD_ALTS),
            "AIQT guardrail: denied a git discard whose working tree this guard cannot resolve to snapshot "
            "(rule prsunc); re-issue from the target repo, or commit or stash first.")
    if clean is True:
        return _allow()  # pristine bare lossy verb on a PROVABLY CLEAN tree: nothing to lose, no snapshot
    if clean is None:
        # probe-uncertain: a snapshot was attempted above (snap set); fold its outcome into the ASK reason.
        return _discard_recovery_result(
            kind, "targets a repository whose status probe did not complete, so this guard cannot prove "
                  "the working tree clean", snap)
    # clean is False: the tree holds uncommitted tracked changes or untracked files this verb could reach.
    if role == "clobber":
        return _deny_with_recovery(kind, snap)  # a confirmed whole-tree loss, still recoverable if approved
    return _discard_recovery_result(kind, "may discard uncommitted changes in the working tree", snap)


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

# Fallback-only raw-string probes, used when the shared tokenizer cannot parse the command (an unbalanced quote or an unsupported construct);
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
# GD-146: the raw-fallback parity for a command-local mirror configuration the parsed path cannot reach
# (a wrapped or unparseable 'git -c remote.<name>.mirror=true push' / '--config-env=remote.<name>.mirror
# =ENV push'). Anchored to the OPTION token ('-c' then whitespace and/or a shell quote, or '--config-env'
# then '='/whitespace and an optional shell quote) introducing 'remote.<something>.mirror', so a QUOTED
# argument ("-c 'remote.origin.mirror=true'", "--config-env 'remote.origin.mirror=MFLAG'") fires like the
# unquoted form, while a push-option value ('-o remote.origin.mirror=true') and prose do not. It matches
# only the COMMON quoted-argument forms above: a quote or backslash that FRAGMENTS the option token, the
# key, or the value (-'c', \-c, remote.origin.'mirror'=true which git 2.53 still reconstructs to
# mirror=true, or a split value) is NOT matched. Arbitrary shell fragmentation of an unparseable or
# wrapper-prefixed command cannot be robustly matched by a regex (only a shell parser could, which the
# pack deliberately avoids), so this is an inherent lexical-scan boundary, the same class as the disclosed
# wrapper/alias/fragmented-command-word residuals; it is DISCLOSED, not chased with more regex variants.
# The PARSED path is complete for parseable commands (the tokenizer normalizes quotes) and network-side
# branch protection remains the backstop. It parses NO value: a falsy value over-asks on this path
# (accepted, documented), mirroring the raw scan's over-matching posture.
_RAW_PUSH_MIRRORCFG_RE = re.compile(
    r"(?i)(?:^|[\s'\"])-c[\s'\"]+remote\.[^\s=]+\.mirror"
    r"|(?:^|[\s'\"])--config-env[=\s]['\"]*remote\.[^\s=]+\.mirror")
_RAW_COMMIT_RE = re.compile(r"(?is)\bgit\b.*?\bcommit\b")

# Commit proof contract. Recognizers establish syntax, never authorization.
# The ordered evaluator supplies the evidence consumed by _commit_on_protected.
# Recognized/apparent commits deny unless that evidence carries an A-D certificate.
#
# Certificate contract:
# A: plain session commit, intact absolute session binding, validated non-protected HEAD.
# B: exact absolute -C commit, proved target identity, applicable non-protected HEAD.
# C: admitted switch and commit have equal worktree identities; the uninterrupted &&
#    chain gates the commit on switch success; the conditional branch is non-protected.
# D: exactly one plain session commit, valid protected local HEAD, and remote probe
#    result exactly False. D is a policy exemption, NOT non-protected branch evidence.
#
# Every certificate additionally requires complete admitted syntax, literal arguments,
# no redirects/wrappers/assignments, acceptable ambient environment, and successful
# bounded probes. A syntax result is never a certificate. Unknown and protected states
# are distinct; neither authorizes a commit without the separately identified D exemption.
_COMMIT_CERTIFICATE_SHAPES = frozenset(("A", "B", "C", "D"))
_COMMIT_MAX_COMMAND = 65536
_COMMIT_MAX_SEGMENTS = 64

# target is None for a session-bound Git command, otherwise the exact absolute -C
# operand. branch is a literal switch operand, NOT a validated reference.
_CommitStep = collections.namedtuple(
    "_CommitStep", ("kind", "target", "branch", "segment"))

# status: "outside", "unverifiable", "help", or "grammar".
# "grammar" establishes syntax only. Records retain the original _Segment metadata.
_CommitSyntax = collections.namedtuple(
    "_CommitSyntax", ("status", "records", "steps", "commit_indexes", "detail"))

# Identity must be worktree-specific: the canonical absolute Git directory plus its
# validated filesystem identity, not merely the common repository directory.
# head is the validated terminal full refs/heads/... reference, including unborn HEAD.
_CommitTarget = collections.namedtuple(
    "_CommitTarget", ("directory", "git_directory", "identity", "head"))

# A switch postcondition describes what success WOULD establish; it is not an observed
# branch change. Only a matching target identity in the admitted && chain may consume it.
_CommitPostcondition = collections.namedtuple(
    "_CommitPostcondition", ("identity", "branch", "switch_index"))

# Only the ordered evaluator constructs certificates. switch_index is None
# for A/B/D and names the applicable preceding switch for C.
_CommitCertificate = collections.namedtuple(
    "_CommitCertificate", ("shape", "commit_index", "identity", "branch", "switch_index"))

# state: "proved", "protected", or "unverifiable". A proved result carries its explicit
# certificate; a protected/unverifiable result carries no certificate.
_CommitEvidence = collections.namedtuple(
    "_CommitEvidence", ("state", "certificate", "detail"))


def _commit_raw_literal(raw):
    """Audit syntax the shared lexer does not preserve faithfully enough for proof.

    Ordinary single/double quotes and backslash quoting are supported. ANSI-C and
    locale quoting are cannot-evaluate here: the shared lexer does not fully decode
    their execution-time bytes. A later decoder must establish those bytes before
    such syntax can participate in a proof. Literal dollars inside ordinary single
    quotes, or escaped dollars, remain supported.

    Quoted heredocs require particular care: the shared lexer drops their bodies and
    does not retain them as redirect records. Reject their raw operators here.
    This audit never scans quoted message/printf data as executable commands.
    """
    quote = None
    boundary = True
    i = 0
    while i < len(raw):
        c = raw[i]
        if quote == "'":
            if c == "'":
                quote = None
            i += 1
            continue
        if c == "\\":
            if i + 1 >= len(raw):
                return False
            if quote == '"' and raw[i + 1] not in ('"', "\\", "$", chr(96), "\n"):
                i += 1
                continue
            if raw[i + 1] != "\n":
                boundary = False
            i += 2
            continue
        if quote == '"':
            if c == '"':
                quote = None
            elif c in ("$", chr(96)):
                return False
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            boundary = False
            i += 1
            continue
        if c == "#" and boundary:
            # A comment runs to the end of this segment's raw slice.
            return "\n" not in raw[i:]
        if c in ("$", chr(96), "<", ">", ";", "|", "&", "(", ")", "\n"):
            return False
        boundary = c in " \t"
        i += 1
    return quote is None


def _commit_literal_segment(seg):
    """Require the full segment evidence, not just its argv projection."""
    return (bool(seg.argv)
            and not seg.redirects
            and not seg.opaque_shell
            and len(seg.argv_opaque) == len(seg.argv)
            and not any(seg.argv_opaque)
            and _commit_raw_literal(seg.raw))


def _commit_exact_prefix(tokens):
    """Return (target, verb, args) for ONLY the two accepted Git prefixes.

    No wrapper peeling, assignment skipping, option abbreviation, or generic Git
    option parser is an authorization input. Options after the verb remain arguments.
    """
    if len(tokens) >= 2 and tokens[0] == "git" and tokens[1] in (
            "commit", "switch", "checkout"):
        return None, tokens[1], tuple(tokens[2:])
    if (len(tokens) >= 4 and tokens[:2] == ["git", "-C"]
            and tokens[2] and os.path.isabs(tokens[2])
            and tokens[3] in ("commit", "switch", "checkout")):
        return tokens[2], tokens[3], tuple(tokens[4:])
    return None


def _commit_syntax_step(seg):
    """Recognize one finite-grammar segment; perform no filesystem observation."""
    if not _commit_literal_segment(seg):
        return None
    tokens = seg.argv
    if len(tokens) == 1 and tokens[0] in (":", "true", "false"):
        return _CommitStep("inert", None, None, seg)
    if len(tokens) == 3 and tokens[:2] == ["printf", "%s"]:
        return _CommitStep("inert", None, None, seg)
    if (len(tokens) == 2 and tokens[0] == "cd"
            and tokens[1] and os.path.isabs(tokens[1])):
        # This invalidates session binding; it does not establish a replacement cwd.
        return _CommitStep("cd", tokens[1], None, seg)
    prefix = _commit_exact_prefix(tokens)
    if prefix is None:
        return None
    target, verb, args = prefix
    if verb == "commit":
        return _CommitStep("commit", target, None, seg)
    if verb == "switch" and len(args) == 1:
        kind, branch = "switch-existing", args[0]
    elif verb == "switch" and len(args) == 2 and args[0] == "-c":
        kind, branch = "switch-create", args[1]
    elif verb == "checkout" and len(args) == 2 and args[0] == "-b":
        kind, branch = "switch-create", args[1]
    else:
        return None
    if not branch or branch.startswith("-") or branch == "@" or "@{" in branch:
        return None
    # Namespace/format, symbolic aliases, and local existence remain probe obligations.
    return _CommitStep(kind, target, branch, seg)


def _commit_apparent_segment(seg):
    """Conservative detection for an UNCLASSIFIED segment only.

    Decoded words expose ordinary quote/backslash fragmentation. Raw text additionally
    exposes apparent commands inside opaque syntax. Neither scan is applied to data
    in an already admitted literal commit/printf segment.

    This does not discover every renamed executable, alias, function, opaque script,
    or unparseable fragmentation. Detection is not a Bash interpreter.
    """
    sub, _args = _git_sub_and_args(seg.argv)
    if _command_word(seg.argv) == "git" and sub == "commit":
        return True
    return bool(_RAW_COMMIT_RE.search(" ".join(seg.argv))
                or _RAW_COMMIT_RE.search(seg.raw))


def _commit_quote_bytes_known(raw):
    """Reject ANSI-C/locale quote syntax the shared lexer cannot decode exactly."""
    quote = None
    i = 0
    while i < len(raw):
        c = raw[i]
        if quote == "'":
            if c == "'":
                quote = None
        elif c == "\\":
            if quote != '"' or (i + 1 < len(raw) and raw[i + 1] in '"\\$' + chr(96) + "\n"):
                i += 1
        elif quote == '"':
            if c == '"':
                quote = None
        elif c == "$" and raw[i + 1:i + 2] in ("'", '"'):
            return False
        elif c in ("'", '"'):
            quote = c
        i += 1
    return True


def _commit_command_syntax(command):
    """Return bounded syntax evidence, never an ALLOW decision.

    Inspect every unclassified segment independently of other parsed Git commands.
    Validate the whole command, including segments after a commit, before returning
    grammar evidence. Exact lone help forms are non-committing syntax, not A-D.
    """
    if not isinstance(command, str):
        return _CommitSyntax("unverifiable", (), (), (), "unreadable command")
    if len(command) > _COMMIT_MAX_COMMAND:
        return _CommitSyntax("unverifiable", (), (), (), "command size budget exhausted")
    try:
        records = tuple(_lex_command(command))
    except ValueError:
        # Partial records improve detection only. They can NEVER establish a proof.
        partial, _complete = _lex_command(command, partial=True)
        apparent = bool(_RAW_COMMIT_RE.search(command))
        apparent = apparent or not _commit_quote_bytes_known(command)
        apparent = apparent or any(_commit_apparent_segment(seg) for seg in partial)
        status = "unverifiable" if apparent else "outside"
        return _CommitSyntax(status, tuple(partial), (), (), "incomplete shell parse")
    if len(records) > _COMMIT_MAX_SEGMENTS:
        return _CommitSyntax("unverifiable", records, (), (), "segment budget exhausted")
    steps = tuple(_commit_syntax_step(seg) for seg in records)
    if any(not _commit_quote_bytes_known(seg.raw)
           for seg, step in zip(records, steps) if step is None):
        return _CommitSyntax("unverifiable", records, steps, (),
                             "ANSI-C or locale quoting has unknown execution-time bytes")
    commits = tuple(i for i, step in enumerate(steps)
                    if step is not None and step.kind == "commit")
    hidden = any(_commit_apparent_segment(seg)
                 for seg, step in zip(records, steps) if step is None)
    if not commits and not hidden:
        return _CommitSyntax("outside", records, steps, (), "no apparent direct commit")
    if (len(records) == 1 and steps[0] is not None
            and steps[0].kind == "commit" and records[0].sep_after == ""
            and records[0].argv in (["git", "commit", "--help"], ["git", "commit", "-h"])):
        return _CommitSyntax("help", records, steps, commits, "exact lone help form")
    if hidden or any(step is None for step in steps):
        return _CommitSyntax("unverifiable", records, steps, commits,
                             "segment outside the admitted commit grammar")
    if (not records or records[-1].sep_after != ""
            or any(seg.sep_after != "&&" for seg in records[:-1])):
        return _CommitSyntax("unverifiable", records, steps, commits,
                             "commit command is not an uninterrupted && chain")
    return _CommitSyntax("grammar", records, steps, commits,
                         "syntax established; target and state evidence still required")


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

def _wildcard_hits_branches(dst):
    """True when a wildcard push destination could expand into refs/heads/ (a protected branch),
    so a sweep the guard cannot enumerate ASKS. The wildcard's literal prefix (before the first
    '*') is compatible with refs/heads/ when it is a prefix of 'refs/heads/' (a wildcard at or
    above the heads namespace: '*', 'refs/*', 'refs/he*'), is itself under refs/heads/ or heads/,
    or carries no refs/ prefix at all (a bare 'name*'). A wildcard provably confined to a non-heads
    namespace (refs/tags/*, refs/remotes/*, refs/notes/*) cannot reach a branch and is excluded.
    GD-145: 'refs/*' (namespace-wide) evaded the old refs/heads/-only test."""
    if "*" not in dst:
        return False
    prefix = dst[:dst.index("*")]
    return ("refs/heads/".startswith(prefix) or prefix.startswith(("refs/heads/", "heads/"))
            or not prefix.startswith("refs/"))


# GD-146: a command-local `remote.<name>.mirror=true` configuration reproduces --mirror's force-and-delete
# sweep with no bulk flag in the judged push args, so it is read on the parsed path. The falsy set is git's
# documented boolean-false spellings plus empty; ANYTHING else fires (fail-safe): a documented truthy
# spelling, a bare key (git boolean true), or a value git would reject as a non-boolean (git dies on it
# before pushing, so firing there is a harmless safe-direction over-ask). Only a provably-falsy value stands
# the guard down, so an unknown future spelling or a mis-split value can never slip to a silent allow.
_MIRROR_FALSY = frozenset(("", "false", "no", "off", "0"))

def _mirror_falsy(value):
    """True when a `-c` value is PROVABLY falsy (git's false spellings plus empty), case-insensitive and
    without stripping, so the guard stands down; a bare key (value None) is git boolean true and is NOT
    falsy. Everything else returns False (fires), the fail-safe direction."""
    if value is None:
        return False  # a bare `-c remote.<name>.mirror` key is git boolean true: it fires
    return value.lower() in _MIRROR_FALSY

def _mirror_key_norm(key):
    """The normalized identity of a git config key when it names remote.<name>.mirror, else None. Matched
    by startswith('remote.')/endswith('.mirror') on the CASE-FOLDED key with a NONEMPTY middle, so a
    dotted remote name (remote.a.b.mirror) matches and remote..mirror never does (no naive three-way
    split). git treats the section and trailing key case-insensitively but the subsection (remote name)
    case-sensitively, so the normalized identity lowercases the fixed remote./.mirror frame and preserves
    the middle VERBATIM; last-value-wins keys on the same remote collapse to one identity, distinct remote
    names stay distinct."""
    low = key.lower()
    if not (low.startswith("remote.") and low.endswith(".mirror")):
        return None
    middle = key[len("remote."):len(key) - len(".mirror")]
    if not middle:
        return None  # remote..mirror: an empty remote name never fires
    return "remote." + middle + ".mirror"

def _push_mirror_config(tokens):
    """(state, key, value): scan the PRE-SUBCOMMAND global-option region for a command-local mirror
    configuration and return ('on', firing key, its value or None for a bare key), ('unknown', the
    --config-env key whose value this guard cannot read, None), or (None, None, None). Walks the same
    eight-line global-option region as _git_subcommand/_git_sub_and_args (start after the command word,
    stop at the first non-'-' token, a separated _GIT_ARG_OPTS member skips two tokens and any other
    option one), reading the assignment carried by each `-c` (separated only; git 2.53 rejects the
    attached -c<key>=<value> form and a top-level --config, so neither is parsed) and each --config-env
    (separated or attached). A separated `-c` value is split at its first '=' into (key, value), value
    absent (bare key, git boolean true) when no '=' is present. DIRECT `-c` assignments to the same
    normalized key apply LAST-VALUE-WINS, as git applies command-line config in order (witnessed on git
    2.53.0); a falsy assignment to one remote's key never cancels a truthy one on another's. A
    --config-env naming a mirror key forces the result to 'unknown' for that key regardless of the order
    of direct assignments to it, because the environment value is unreadable (a cannot-evaluate routes to
    the safe outcome, ASK) and the relative precedence of -c and --config-env was not witnessed, so an
    unwitnessed cross-mechanism ordering is not modelled (a deliberate safe-direction over-ask). 'on'
    (any direct key's final state is not provably falsy) outranks 'unknown' for the wording when both
    apply; both dispositions ASK. This is redirect-independent and reads no git config offline: it judges
    only what the command string spells."""
    i = _command_word_index(tokens) + 1  # skip leading env assignments and the command word itself
    n = len(tokens)
    direct = {}  # normalized key -> (fires: bool, original key, value or None); last direct assignment wins
    env = {}     # normalized key -> original key, named via --config-env (forces 'unknown')
    while i < n:
        token = tokens[i]
        if not token.startswith("-"):
            break  # the subcommand: the global-option region ends here
        if "=" not in token and token in _GIT_ARG_OPTS:
            value = tokens[i + 1] if i + 1 < n else None  # a trailing bare -c/--config-env has none
            if token == "-c" and value is not None:
                key, sep, val = value.partition("=")
                norm = _mirror_key_norm(key)
                if norm is not None:
                    val = val if sep else None  # no '=' present: a bare key (git boolean true)
                    direct[norm] = (not _mirror_falsy(val), key, val)
            elif token == "--config-env" and value is not None:
                key = value.partition("=")[0]  # separated form: KEY=ENVVAR
                norm = _mirror_key_norm(key)
                if norm is not None:
                    env[norm] = key
            i += 2
            continue
        if token.startswith("--config-env="):  # attached form: --config-env=KEY=ENVVAR
            key = token[len("--config-env="):].partition("=")[0]
            norm = _mirror_key_norm(key)
            if norm is not None:
                env[norm] = key
        i += 1
    for _norm, (fires, key, value) in direct.items():
        if fires:
            return ("on", key, value)  # dict preserves order: the first firing key names the ASK
    if env:
        return ("unknown", next(iter(env.values())), None)
    return (None, None, None)


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
    and --prune with a wildcard or matching refspec or --all/--branches, which DELETES every remote
    branch absent locally
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
        if _wildcard_hits_branches(dst):
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
    # force or delete destination that can reach the branch namespace, a --prune sweep (a wildcard
    # or matching refspec, or --all/--branches: it DELETES remote branches absent locally with no
    # force flag), the plain matching refspec, a --mirror push (which force-updates AND deletes every
    # ref), or a forced --all/--branches. The --prune combinations are judged BEFORE the plain
    # matching case so a pruning matching-refspec ('git push --prune origin :') names its deletion
    # effect rather than the non-deleting matching explanation (GD-145).
    for dst in forced_dsts + deleted_dsts:
        if _wildcard_hits_branches(dst):
            return ("ask", "force-pushes or deletes the wildcard refspec {!r}, a sweep this guard "
                           "cannot prove misses the protected branches".format(dst), None)
    if prune and (wild_branch or matching or sweep_all):
        return ("ask", "combines --prune with a sweeping selector (a wildcard or matching refspec, "
                       "or --all/--branches), which DELETES every selected remote ref whose local "
                       "counterpart is absent, without requiring a force flag; the sweep can remove a "
                       "protected "
                       "branch and cause potentially irreversible loss, and this guard cannot prove "
                       "it misses the protected branches", None)
    if matching:
        return ("ask", "pushes the matching refspec ':' ('+:' is its forced form), a matching-refspec "
                       "push of every branch existing on both ends, which this guard cannot prove "
                       "misses the protected branches", None)
    # --mirror OR a command-local mirror configuration (GD-146: '-c remote.<name>.mirror=true push', or
    # the same key through --config-env, both PRE-subcommand) is the same runtime act, so both ASK with the
    # same disposition. This joins the existing flag clause AFTER the named-protected force/delete DENY
    # loops (so a truthy mirror config with a '+main:main' or ':main' refspec still DENIES) and BEFORE the
    # early returns below (which would otherwise silent-allow 'push origin'/'push origin main' under the
    # config). Only the separated '-c <key>[=<value>]' and both --config-env spellings in the command
    # string are read; git 2.53 rejects a top-level '--config' and an attached '-c<key>=<value>', so
    # neither is parsed. A non-boolean direct value over-asks harmlessly (git dies on the bad boolean
    # before pushing), and the --config-env ASK is a deliberate over-ask on a value this guard cannot read.
    mirror_state, mc_key, mc_val = _push_mirror_config(tokens)
    if mirror:
        return ("ask", "is a --mirror push, which force-updates every matching remote ref and DELETES "
                       "every remote ref (branch, tag, note) absent locally; on a shared remote this "
                       "removes branches, protected or not, and can cause potentially irreversible "
                       "loss, and this guard cannot prove it misses the protected branches", None)
    if mirror_state == "on":
        shown = "{}={}".format(mc_key, mc_val) if mc_val is not None else mc_key
        return ("ask", "sets the command-local configuration '{}' (a bare '{}' is boolean true), which "
                       "makes the push behave exactly like --mirror: it force-updates every matching "
                       "remote ref and DELETES every remote ref (branch, tag, note) absent locally; on a "
                       "shared remote this removes branches, protected or not, and can cause potentially "
                       "irreversible loss, and this guard cannot prove it misses the protected branches"
                       .format(shown, mc_key), None)
    if mirror_state == "unknown":
        return ("ask", "names the command-local configuration '{}' through --config-env, whose value "
                       "lives in an environment variable this guard cannot read, so it cannot prove "
                       "mirror mode is off; a mirror push force-updates every matching remote ref and "
                       "DELETES every remote ref absent locally, and this guard cannot prove it misses "
                       "the protected branches".format(mc_key), None)
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
    # disclosed, not modelled; likewise a bare 'git push --prune <remote>' whose deletion is driven
    # by a configured remote.<name>.push refspec, and a PERSISTED mirror mode (remote.<name>.mirror
    # =true in repository, worktree, global, or system git config, or the GIT_CONFIG_* env protocol),
    # which reproduce the force-and-delete sweep with no bulk flag in the judged push args: the guard
    # reads no git config offline, so these are disclosed residuals, not modelled (GD-145). The
    # COMMAND-LOCAL mirror configuration ('git -c remote.<name>.mirror=true push', or the key through
    # --config-env) IS now modelled at the mirror clause above (GD-146), since it lives in the command
    # string the guard can read. A '--repo=<remote>'
    # option does not displace the positional repository operand: git gives the command-line positional
    # precedence (git-push(1)), so operands[0] stays the repository and the guard's slice is correct; a
    # '--repo' form carrying a refspec-shaped positional is rejected by git as an unknown repository
    # rather than executed as a push (verified, git 2.53).
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

# One evaluation gets seven seconds, including parsing, Git and filesystem probes.
# tools/gen_hooks.py gives the hook ten seconds. No probe resets this deadline.
_COMMIT_SECONDS = 7.0
_COMMIT_PROBE_SECONDS = 2.0
_COMMIT_PROBE_BYTES = 65536


class _CommitCannotEvaluate(Exception):
    pass


def _commit_remaining(deadline):
    remaining = deadline - time.monotonic()
    if not math.isfinite(remaining) or remaining <= 0:
        raise _CommitCannotEvaluate("commit evaluation deadline exhausted")
    return remaining


def _commit_run(argv, deadline):
    """Read-only subprocess with bounded output and a shared monotonic deadline.

    Filesystem probes also run here: a blocked stat/listing must not outlive the
    proof budget. POSIX process groups bound descendants as well as the direct child.
    Unsupported platforms, errors, excess output and timeouts withhold proof.
    """
    _commit_remaining(deadline)
    if os.name != "posix":
        raise _CommitCannotEvaluate("bounded commit probes require POSIX")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1",
               GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    end = min(deadline, time.monotonic() + _COMMIT_PROBE_SECONDS)
    proc = None
    try:
        proc = subprocess.Popen(argv, cwd="/", env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
        output = [bytearray(), bytearray()]
        size = 0
        with selectors.DefaultSelector() as sel:
            for index, stream in enumerate((proc.stdout, proc.stderr)):
                os.set_blocking(stream.fileno(), False)
                sel.register(stream, selectors.EVENT_READ, index)
            while sel.get_map():
                for key, _events in sel.select(_commit_remaining(end)):
                    part = os.read(key.fileobj.fileno(), 4096)
                    if not part:
                        sel.unregister(key.fileobj)
                        continue
                    size += len(part)
                    if size > _COMMIT_PROBE_BYTES:
                        raise _CommitCannotEvaluate("commit probe output budget exhausted")
                    output[key.data].extend(part)
            rc = proc.wait(timeout=_commit_remaining(end))
        _commit_remaining(deadline)
        return subprocess.CompletedProcess(argv, rc, output[0].decode("utf-8"),
                                           output[1].decode("utf-8"))
    except _CommitCannotEvaluate:
        raise
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise _CommitCannotEvaluate("commit probe failed: " + type(exc).__name__) from exc
    finally:
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            for stream in (proc.stdout, proc.stderr):
                stream.close()
            # Cleanup has its own small allowance, never a fresh evaluation budget.
            try:
                proc.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass


def _commit_git(repo, deadline, *args):
    return _commit_run(["git", "-C", repo, *args], deadline)


def _commit_checked(result):
    if result.returncode != 0 or result.stderr:
        raise _CommitCannotEvaluate("Git or filesystem probe did not succeed cleanly")
    return result.stdout


def _commit_line(text):
    if not text.endswith("\n"):
        raise _CommitCannotEvaluate("probe omitted its output terminator")
    value = text[:-1]
    if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise _CommitCannotEvaluate("probe returned an empty or malformed value")
    return value


# No filesystem access that supplies proof runs in the hook process itself.
# This helper is isolated Python, takes literal argv, and prints one JSON payload.
_COMMIT_FS_PROBE = r"""
import json, os, pathlib, stat, sys
def directory(value):
    path = str(pathlib.Path(value).resolve(strict=True))
    st = os.stat(path)
    if not stat.S_ISDIR(st.st_mode):
        raise ValueError("not a directory")
    with os.scandir(path):
        pass
    return path, st
try:
    if sys.argv[1] == "identity":
        repo, _ = directory(sys.argv[2])
        gitdir, st = directory(sys.argv[3])
        result = [repo, gitdir, st.st_dev, st.st_ino]
    elif sys.argv[1] == "legacy":
        common, _ = directory(sys.argv[2])
        result = False
        for name in ("remotes", "branches"):
            path = os.path.join(common, name)
            try:
                st = os.lstat(path)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(st.st_mode):
                raise ValueError("legacy remote directory is not a plain directory")
            with os.scandir(path) as entries:
                if next(entries, None) is not None:
                    result = True
                    break
    else:
        raise ValueError("unknown filesystem probe")
    print(json.dumps(result))
except Exception:
    sys.exit(2)
"""


def _commit_fs(deadline, operation, *paths):
    result = _commit_run([sys.executable, "-I", "-B", "-c", _COMMIT_FS_PROBE,
                          operation, *paths], deadline)
    try:
        return json.loads(_commit_checked(result))
    except ValueError as exc:
        raise _CommitCannotEvaluate("malformed filesystem probe payload") from exc


def _commit_validate_ref(repo, deadline, ref):
    if not ref.startswith("refs/heads/") or not ref[len("refs/heads/"):]:
        raise _CommitCannotEvaluate("HEAD or switch destination is not a local branch")
    if _commit_checked(_commit_git(repo, deadline, "check-ref-format", ref)) != "":
        raise _CommitCannotEvaluate("unexpected reference validation output")
    return ref


def _commit_ref_exists(repo, deadline, ref):
    result = _commit_git(repo, deadline, "show-ref", "--exists", ref)
    if result.returncode == 2 and not result.stdout:
        return False  # documented missing-reference status; never other errors
    if result.returncode == 0 and not result.stdout and not result.stderr:
        return True
    raise _CommitCannotEvaluate("local reference existence could not be established")


def _commit_ref_object(repo, deadline, ref):
    oid = _commit_line(_commit_checked(_commit_git(
        repo, deadline, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")))
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid) is None:
        raise _CommitCannotEvaluate("malformed branch object identity")
    if _commit_checked(_commit_git(repo, deadline, "cat-file", "-t", oid)) != "commit\n":
        raise _CommitCannotEvaluate("branch commit object is unavailable")


def _commit_target(directory, deadline):
    if not isinstance(directory, str) or not directory or not os.path.isabs(directory):
        raise _CommitCannotEvaluate("target requires a valid absolute directory")
    view = _commit_checked(_commit_git(
        directory, deadline, "rev-parse", "--is-inside-work-tree", "--absolute-git-dir"))
    if not view.startswith("true\n"):
        raise _CommitCannotEvaluate("target is not a working tree")
    gitdir = _commit_line(view[len("true\n"):])
    if not os.path.isabs(gitdir):
        raise _CommitCannotEvaluate("Git directory is not absolute")
    info = _commit_fs(deadline, "identity", directory, gitdir)
    if (not isinstance(info, list) or len(info) != 4
            or any(not isinstance(p, str) or not os.path.isabs(p) for p in info[:2])
            or any(type(n) is not int or n < 0 for n in info[2:]) or info[3] == 0):
        raise _CommitCannotEvaluate("malformed worktree directory identity")
    # symbolic-ref recurses to the terminal destination; never classify an alias name.
    head = _commit_line(_commit_checked(_commit_git(
        directory, deadline, "symbolic-ref", "--quiet", "--recurse", "HEAD")))
    _commit_validate_ref(directory, deadline, head)
    if _commit_ref_exists(directory, deadline, head):
        _commit_ref_object(directory, deadline, head)
    # A valid symbolic HEAD with an absent terminal local ref is an unborn branch.
    _commit_remaining(deadline)
    return _CommitTarget(info[0], info[1], tuple(info[2:]), head)


def _commit_switch_ref(step, target, deadline, created):
    repo, name = target.directory, step.branch
    if not name or name.startswith("-") or name in ("@", "HEAD") or "@{" in name:
        raise _CommitCannotEvaluate("switch requires a literal local branch name")
    checked = _commit_checked(_commit_git(repo, deadline, "check-ref-format", "--branch", name))
    if checked != name + "\n":
        raise _CommitCannotEvaluate("switch branch was expanded or normalized")
    ref = _commit_validate_ref(repo, deadline, "refs/heads/" + name)
    key = (target.identity, ref)
    if step.kind == "switch-create":
        if key in created or _commit_ref_exists(repo, deadline, ref):
            raise _CommitCannotEvaluate("new switch destination already exists")
        created.add(key)
        return ref
    # A creation postcondition proves HEAD, not a fresh observation of a local ref.
    # Existing switches still require observed local existence and unambiguous resolution.
    if not _commit_ref_exists(repo, deadline, ref):
        raise _CommitCannotEvaluate("switch destination is not an existing local branch")
    symbolic = _commit_git(repo, deadline, "symbolic-ref", "--quiet", "--recurse", ref)
    if symbolic.returncode != 1 or symbolic.stdout or symbolic.stderr:
        raise _CommitCannotEvaluate("symbolic or unreadable switch destination")
    resolved = _commit_line(_commit_checked(_commit_git(
        repo, deadline, "rev-parse", "--symbolic-full-name", "--verify", "--end-of-options", name)))
    if resolved != ref:
        raise _CommitCannotEvaluate("switch destination is ambiguous or shorthand")
    _commit_ref_object(repo, deadline, ref)
    return ref


def _repo_has_remote(repo, deadline):
    """D only: True for a remote, False for proved absence, None for any probe failure.

    Preserve config remote line presence (including whitespace names) and both legacy
    common-directory mechanisms. These observations consume the SAME commit deadline.
    """
    try:
        output = _commit_checked(_commit_git(repo, deadline, "remote"))
        if output.splitlines():
            return True
        common = _commit_line(_commit_checked(_commit_git(
            repo, deadline, "rev-parse", "--git-common-dir")))
        if not os.path.isabs(common):
            common = os.path.join(repo, common)
        result = _commit_fs(deadline, "legacy", common)
        _commit_remaining(deadline)
        return result if type(result) is bool else None
    except _CommitCannotEvaluate:
        return None


def _commit_issue(syntax, index, step, target, post, deadline):
    branch = target.head if post is None else post.branch
    if post is not None and (post.identity != target.identity or post.switch_index >= index):
        raise _CommitCannotEvaluate("switch evidence does not bind this commit")
    _commit_remaining(deadline)
    if _is_protected_ref(branch):
        # D is a policy exemption, not a proof of a non-protected destination.
        if len(syntax.steps) == 1 and step.target is None and post is None:
            remote = _repo_has_remote(target.directory, deadline)
            _commit_remaining(deadline)
            if remote is False:
                certificate = _CommitCertificate("D", index, target.identity, branch, None)
                return _CommitEvidence("proved", certificate, "D: lone session commit; no remote")
            if remote is None:
                return _CommitEvidence("unverifiable", None, "remote absence could not be proved")
        return _CommitEvidence("protected", None, "would commit on protected branch " + repr(branch))
    shape = "C" if post is not None else ("B" if step.target is not None else "A")
    certificate = _CommitCertificate(shape, index, target.identity, branch,
                                     None if post is None else post.switch_index)
    return _CommitEvidence("proved", certificate, shape + ": non-protected local branch")


def _commit_evaluate(syntax, cwd, deadline):
    """Issue evidence only after whole-command syntax and ordered effects are known.

    State is keyed by the worktree Git directory's device/inode, so alternate paths
    share postconditions while linked worktrees keep independent HEAD state.
    cd permanently invalidates session binding; it never supplies a replacement cwd.
    Trusted command resolution, stable config/paths and non-hostile hooks/editors/
    helpers remain assumptions. Concurrent mutation and probe-to-execution races
    remain residual risks. Switch aliases are refused; HEAD aliases are resolved.
    """
    try:
        _commit_remaining(deadline)
        if syntax.status != "grammar" or not syntax.commit_indexes:
            raise _CommitCannotEvaluate(syntax.detail)
        if _ambient_repo_view_override():
            raise _CommitCannotEvaluate("non-cosmetic ambient GIT_* variable")
        session_bound = isinstance(cwd, str) and bool(cwd) and os.path.isabs(cwd)
        targets, identities, directory_ids, posts, created = {}, {}, {}, {}, set()
        evidence = []
        for index, step in enumerate(syntax.steps):
            _commit_remaining(deadline)
            if step.kind == "inert":
                continue
            if step.kind == "cd":
                session_bound = False
                continue
            directory = step.target
            if directory is None:
                if not session_bound:
                    raise _CommitCannotEvaluate("session directory binding is unavailable after cd")
                directory = cwd
            if directory not in targets:
                target = _commit_target(directory, deadline)
                prior_id = directory_ids.get(target.git_directory)
                if prior_id is not None and prior_id != target.identity:
                    raise _CommitCannotEvaluate("Git directory identity changed during evaluation")
                prior = identities.get(target.identity)
                if prior is not None and prior.head != target.head:
                    raise _CommitCannotEvaluate("HEAD observations disagree for one worktree")
                directory_ids[target.git_directory] = target.identity
                identities[target.identity] = target
                targets[directory] = target
            target = targets[directory]
            if step.kind in ("switch-existing", "switch-create"):
                ref = _commit_switch_ref(step, target, deadline, created)
                posts[target.identity] = _CommitPostcondition(target.identity, ref, index)
            elif step.kind == "commit":
                evidence.append(_commit_issue(syntax, index, step, target,
                                              posts.get(target.identity), deadline))
            else:
                raise _CommitCannotEvaluate("unmodelled command effect")
        _commit_remaining(deadline)
        if len(evidence) != len(syntax.commit_indexes):
            raise _CommitCannotEvaluate("missing commit evidence")
        return tuple(evidence)
    except _CommitCannotEvaluate as exc:
        return (_CommitEvidence("unverifiable", None, str(exc)),)


def _commit_on_protected(evidence):
    """Consume proof only. Unknown/protected states DENY; there is no allow-note exit."""
    if isinstance(evidence, _CommitEvidence) and evidence.state == "proved":
        cert = evidence.certificate
        if (isinstance(cert, _CommitCertificate) and cert.shape in _COMMIT_CERTIFICATE_SHAPES
                and cert.identity and cert.branch.startswith("refs/heads/")
                and (cert.shape == "D") == _is_protected_ref(cert.branch)
                and ((cert.shape == "C" and type(cert.switch_index) is int
                      and 0 <= cert.switch_index < cert.commit_index)
                     or (cert.shape != "C" and cert.switch_index is None))):
            return None
    detail = evidence.detail if isinstance(evidence, _CommitEvidence) else "missing commit evidence"
    return ("deny", detail)


def _commit_denial(detail):
    return _deny(
        "AIQT rule artbr1 (branch-and-merge-on-green): " + detail + ". Commit denied because no "
        "admitted proof or lone no-remote exemption authorizes it. Use a separate plain feature-branch "
        "commit, or an exact absolute 'git -C /worktree commit' with a proved non-protected branch; "
        "an admitted switch may precede it through &&. " + _PROTECTED_ALTS,
        "AIQT guardrail: denied a protected or unproved direct commit (rule artbr1).")


def _protected_line_fallback(command):
    """Commit-free push fallback; commit syntax is checked before this path.

    Retain conservative raw push coverage and quoted-heredoc stripping. Hidden
    commits are checked independently of other Git segments by the commit recognizer.
    """
    syntax = _commit_command_syntax(command)
    if syntax.status != "outside":
        if syntax.status == "help":
            return _allow()
        return _commit_denial("apparent commit lacks complete proof: " + syntax.detail)
    scan = _strip_quoted_heredoc_bodies(command)
    if _RAW_PUSH_RE.search(scan) and (_RAW_PUSH_FORCE_RE.search(scan)
                                      or _RAW_PUSH_DELETE_RE.search(scan)
                                      or _RAW_PUSH_MIRRORCFG_RE.search(scan)):
        named = " a protected branch" if _RAW_PROTECTED_RE.search(scan) else " a target this guard cannot read"
        return _deny(
            "AIQT rule prtbrn (protected-branch-integrity): this command could not be fully parsed by the "
            "shell lexer (unbalanced quotes) or hides git under a command-word wrapper, and it appears to "
            "force-push or delete{}; it is denied fail-safe because this guard cannot prove it will not "
            "rewrite the protected line. The protected line changes only through a reviewed, verified merge. "
            "Re-issue it as a plain, parseable, non-force git command. {}".format(named, _PROTECTED_ALTS),
            "AIQT guardrail: denied an apparent force-push or branch deletion this guard cannot fully parse "
            "(rule prtbrn, fail-safe); push to a feature branch and merge on green.")
    return _allow()

def protected_line(data):
    """Commit proof runs before information shortcuts; push retains its separate policy.

    Direct/apparent commits require the finite grammar and an A-D certificate for
    each committing segment. Unknown wrappers/effects/control flow and failed probes
    deny. Literal detection cannot find every renamed executable, alias, function,
    opaque script or unparseable fragmentation; other commit-producing verbs and
    other tools remain outside this boundary. Server protection does not validate
    this pre-execution proof. Push coverage retains its existing separate limits.
    """
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: protected_line wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("prtbrn")
    if tool_name != "Bash":
        return _allow()
    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return _deny(
            "AIQT rule prtbrn (protected-branch-integrity): the Bash payload carried no readable command "
            "string, so the protected-line check could not run; failing closed.",
            "AIQT guardrail: denied a Bash call with no readable command (rule prtbrn, fail-closed).")
    deadline = time.monotonic() + _COMMIT_SECONDS
    syntax = _commit_command_syntax(command)
    if syntax.status == "help":
        return _allow()  # only literal lone git commit --help / -h
    if syntax.status == "unverifiable":
        return _commit_denial(syntax.detail)
    if syntax.status == "grammar":
        evidence = _commit_evaluate(syntax, data.get("cwd"), deadline)
        if not evidence:
            return _commit_denial("missing commit evidence")
        for item in evidence:
            outcome = _commit_on_protected(item)
            if outcome is not None:
                return _commit_denial(outcome[1])
        return _allow()

    # Only commit-free commands reach the existing push classifier/help shortcut.
    try:
        seg_records = _lex_command(command)
    except ValueError:
        return _protected_line_fallback(command)
    cwd = data.get("cwd")
    pending_deny = None
    saw_git = False
    for seg in seg_records:
        tokens = seg.argv
        if _command_word(tokens) != "git":
            continue
        saw_git = True
        if _has_info_flag(tokens):
            continue
        sub, args = _git_sub_and_args(tokens)
        if sub != "push":
            continue
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
        if pending_deny is None:
            pending_deny = (
                "AIQT rule prtbrn (protected-branch-integrity): this git push {}. This guard cannot "
                "prove it will not rewrite the protected line, so it is denied fail-safe rather than "
                "run. Re-issue it as a push this guard can prove misses the protected branch (an "
                "explicit non-protected refspec, no --mirror/--all/wildcard/prune sweep). {}"
                .format(detail, _PROTECTED_ALTS),
                "AIQT guardrail: denied a git push this guard cannot prove misses the protected branch "
                "(rule prtbrn); push to a feature branch and merge on green.")
    if pending_deny is not None:
        return _deny(*pending_deny)
    if not saw_git and _RAW_GIT_RE.search(command):
        return _protected_line_fallback(command)
    return _allow()


# --- brnrot: branch creation must stay rooted on origin/HEAD -----------------------------------------

# ============================================================================
# CONSTANTS (module-level)
# ============================================================================

# Module sentinel and end-of-options boundary already exist in the target module;
# reproduced here so the draft is self-contained and directly runnable.
_ASK_START = object()

# The ONLY options tolerated inside a clean canonical creation: valueless booleans.
# Exact match only -- git resolves an unambiguous long-option PREFIX to the full
# option, so an abbreviated form (e.g. "--qui") is deliberately NOT matched here and
# falls through to the ASK path, never to a clean-allow.
_CLEAN_LONG_BOOLEANS = frozenset(("--quiet", "--force"))
_CLEAN_SHORT_LETTERS = frozenset("qf")
# git branch additionally exposes --verbose/-v as a valueless boolean; checkout/switch/worktree do NOT
# (git rejects -v there and creates nothing), so -v is clean for BRANCH only, not shared.
_BRANCH_CLEAN_LONG = _CLEAN_LONG_BOOLEANS | frozenset(("--verbose",))
_BRANCH_CLEAN_SHORT = _CLEAN_SHORT_LETTERS | frozenset("v")

# git branch: copy (a creation whose start is the SOURCE).
_BRANCH_COPY_SHORT = frozenset("cC")
_BRANCH_COPY_LONG = frozenset(("--copy",))

# git branch: recognized NON-creation actions and listing forms (allow silently).
# delete (d/D), move/rename (m/M), all (a), remotes (r), set-upstream (u).
_BRANCH_NONCREATION_SHORT = frozenset("dDmMaru")
_BRANCH_NONCREATION_LONG = frozenset((
    "--delete", "--move", "--all", "--remotes", "--list",
    "--set-upstream-to", "--unset-upstream", "--show-current", "--edit-description",
))

# git worktree: only `add` can create; the rest are recognized non-creations.
_WORKTREE_NONCREATION_SUBS = frozenset((
    "list", "remove", "move", "prune", "lock", "unlock", "repair",
))
_WORKTREE_CREATE_SHORT = frozenset("bB")


# ============================================================================
# PARSER 1: checkout / switch
# ============================================================================

def _checkout_creation_start(args, switch=False):
    """Maximally-conservative start-point classifier for `git checkout` / `git switch`.

    Returns a start-point string (probe: rooted allows, orphaned DENIES) ONLY for a
    clean canonical creation; `_ASK_START` for any non-clean creation-capable form;
    `None` for a confident non-creation (checkout/switch of an existing ref).
    """
    if switch:
        short_triggers = frozenset("cC")            # -c / -C
        long_triggers = frozenset(("--create", "--force-create"))
    else:
        short_triggers = frozenset("bB")            # -b / -B
        long_triggers = frozenset()                 # checkout has no clean long creation form

    created = False
    branch_name = None      # the trigger's value (new branch name); None => none seen yet
    operands = []           # positional start-point candidates
    saw_eoo = False
    track_seen = False      # a --track/-t upstream-tracking request (ROUND-3 FINDING 6)

    i = 0
    n = len(args)
    while i < n:
        tok = args[i]

        if not saw_eoo and tok in _EOO_TOKENS:
            saw_eoo = True
            i += 1
            continue
        if saw_eoo:
            # checkout: post-boundary tokens are PATHSPECS (ignored, never the start).
            # switch: no pathspecs, so post-boundary tokens are operands.
            if switch:
                operands.append(tok)
            i += 1
            continue

        if tok.startswith("--") and len(tok) > 2:
            name = tok.split("=", 1)[0]
            has_value = "=" in tok
            if name in long_triggers and not created:
                created = True
                if has_value:
                    branch_name = tok.split("=", 1)[1]
                else:
                    i += 1
                    if i < n:
                        branch_name = args[i]
                i += 1
                continue
            if name in _CLEAN_LONG_BOOLEANS and not has_value:
                i += 1
                continue
            # ROUND-2 FINDING 12: '--track' only ENABLES upstream tracking; it consumes no operand (its
            # optional value is the attached '--track=direct|inherit' form), so the positional start-point
            # (e.g. the 'origin/main' in 'git switch -c topic --track origin/main') remains the operand and
            # its ancestry is checked. Tolerate it here instead of routing the whole form to a fail-safe deny.
            if name == "--track":
                track_seen = True
                i += 1
                continue
            # any other long option: unknown, value-taking, negation, abbreviation,
            # a second trigger, --orphan, --detach, --patch, ... -> ASK
            return _ASK_START

        if tok.startswith("-") and len(tok) > 1:
            chars = tok[1:]
            j = 0
            while j < len(chars):
                ch = chars[j]
                if ch in _CLEAN_SHORT_LETTERS or ch == "t":  # 't' is -t (--track): enables tracking only
                    if ch == "t":
                        track_seen = True                    # a -t upstream-tracking request (finding 6)
                    j += 1
                    continue
                if ch in short_triggers and not created:
                    created = True
                    rest = chars[j + 1:]
                    if rest:
                        branch_name = rest              # attached value: -bNAME
                    else:
                        i += 1
                        if i < n:
                            branch_name = args[i]       # separated value: -b NAME
                    break
                # unknown short letter, or a duplicate trigger -> ASK
                return _ASK_START
            i += 1
            continue

        operands.append(tok)
        i += 1

    if created:
        if len(operands) > 1:
            return _ASK_START                           # more operands than a clean creation
        if branch_name is None:
            return None                                 # trigger with no name: git error, no ref
        return operands[0] if operands else "HEAD"

    # ROUND-3 FINDING 6: a '--track'/'-t' checkout/switch WITHOUT an explicit -c/-B trigger and with a single
    # positional operand still CREATES a local branch (git's DWIM tracking creation) rooted at that operand
    # when the local branch does not yet exist, so its start point IS branch-creating and its ancestry must
    # be probed - a genuine orphan start (e.g. 'git switch --track origin/retired', merge-base with the
    # protected line empty) DENIES, a rooted upstream ('origin/main') ALLOWS, an unresolvable one notes.
    # Previously the missing explicit trigger made this a silent non-creation allow that skipped the probe.
    if track_seen and len(operands) == 1:
        return operands[0]

    # no creation trigger: a checkout/switch of an existing ref -> allow silently
    return None


# ============================================================================
# PARSER 2: branch
# ============================================================================

def _branch_command_creation_start(args):
    """Maximally-conservative start-point classifier for `git branch`.

    Clean creation is a bare `<name> [<start>]`, or a copy (`-c`/`-C`/`--copy`) whose
    start is the SOURCE (explicit first operand, else HEAD). Recognized non-creation
    actions and listing forms allow silently; everything else ASKs.
    """
    ask = False
    noncreation = False
    copy = False
    operands = []
    saw_eoo = False

    i = 0
    n = len(args)
    while i < n:
        tok = args[i]

        if not saw_eoo and tok in _EOO_TOKENS:
            saw_eoo = True
            i += 1
            continue
        if saw_eoo:
            operands.append(tok)                        # no pathspecs: operands
            i += 1
            continue

        if tok.startswith("--") and len(tok) > 2:
            name = tok.split("=", 1)[0]
            has_value = "=" in tok
            if tok.startswith("--no-"):
                ask = True                              # any negation flips the classifier -> ASK
            elif name in _BRANCH_COPY_LONG:
                if has_value:
                    ask = True                          # unexpected value on a copy trigger
                else:
                    copy = True
            elif name in _BRANCH_NONCREATION_LONG:
                noncreation = True
            elif name in _BRANCH_CLEAN_LONG and not has_value:
                pass                                    # tolerated valueless boolean (branch: +--verbose)
            else:
                ask = True                              # unknown/value-taking/abbreviated -> ASK
            i += 1
            continue

        if tok.startswith("-") and len(tok) > 1:
            for ch in tok[1:]:
                if ch in _BRANCH_COPY_SHORT:
                    copy = True
                elif ch in _BRANCH_NONCREATION_SHORT:
                    noncreation = True
                elif ch in _BRANCH_CLEAN_SHORT:
                    pass
                else:
                    ask = True                          # any letter outside the known sets -> ASK
            i += 1
            continue

        operands.append(tok)
        i += 1

    if ask:
        return _ASK_START
    if copy and noncreation:
        return _ASK_START                               # contradictory triggers -> ASK
    if noncreation:
        return None
    if copy:
        if len(operands) == 0:
            return None                                 # incomplete copy: git error, no ref
        if len(operands) == 1:
            return "HEAD"                               # copy current HEAD to the new name
        if len(operands) == 2:
            return operands[0]                          # copy the explicit SOURCE
        return _ASK_START                               # unexpected extra operands
    if operands:
        if len(operands) > 2:
            return _ASK_START
        return operands[1] if len(operands) > 1 else "HEAD"
    return None                                         # no operands, no trigger: a listing


# ============================================================================
# PARSER 3: worktree
# ============================================================================

def _worktree_creation_start(args):
    """Maximally-conservative start-point classifier for `git worktree`.

    Only `worktree add -b/-B <name> <path> [<start>]` is a clean creation. A bare
    `worktree add <path> [<commit-ish>]` (no -b) is ambiguous -> ASK. `--detach`/`-d`
    and `--orphan` -> ASK. Recognized non-creation subcommands allow silently.
    """
    if not args:
        return _ASK_START
    subcommand = args[0]
    if subcommand != "add":
        if subcommand in _WORKTREE_NONCREATION_SUBS:
            return None
        return _ASK_START                               # unknown/absent subcommand -> fail-safe

    created = False
    branch_name = None
    operands = []                                       # [<path>, <start>]
    saw_eoo = False

    i = 1
    n = len(args)
    while i < n:
        tok = args[i]

        if not saw_eoo and tok in _EOO_TOKENS:
            saw_eoo = True
            i += 1
            continue
        if saw_eoo:
            operands.append(tok)                        # no pathspecs: operands
            i += 1
            continue

        if tok.startswith("--") and len(tok) > 2:
            name = tok.split("=", 1)[0]
            has_value = "=" in tok
            if name in _CLEAN_LONG_BOOLEANS and not has_value:
                i += 1
                continue
            # --detach, --orphan, --no-*, unknown, value-taking, abbreviated -> ASK
            return _ASK_START

        if tok.startswith("-") and len(tok) > 1:
            chars = tok[1:]
            j = 0
            while j < len(chars):
                ch = chars[j]
                if ch in _CLEAN_SHORT_LETTERS:
                    j += 1
                    continue
                if ch in _WORKTREE_CREATE_SHORT and not created:
                    created = True
                    rest = chars[j + 1:]
                    if rest:
                        branch_name = rest              # attached value: -bNAME
                    else:
                        i += 1
                        if i < n:
                            branch_name = args[i]       # separated value: -b NAME
                    break
                # -d (detach) or any other short letter, or a duplicate trigger -> ASK
                return _ASK_START
            i += 1
            continue

        operands.append(tok)
        i += 1

    if not created:
        return _ASK_START                               # bare `add <path> [<commit-ish>]`: ambiguous
    if branch_name is None:
        return None                                     # -b with no name: git error, no ref
    if len(operands) == 0:
        return None                                     # add -b name  (no path): git error, no ref
    if len(operands) == 1:
        return "HEAD"                                   # add -b name /path  (no start-point)
    if len(operands) == 2:
        return operands[1]                              # operands = [path, start]
    return _ASK_START                                   # unexpected extra operands


# ============================================================================
# SELF-TEST: asserts EVERY required vector
# ============================================================================


def _branch_creation_start(sub, args):
    if sub == "checkout":
        return _checkout_creation_start(args, switch=False)
    if sub == "switch":
        return _checkout_creation_start(args, switch=True)
    if sub == "branch":
        return _branch_command_creation_start(args)
    if sub == "worktree":
        return _worktree_creation_start(args)
    return None


def _branch_root_git(repo, *args):
    env = _isolate_git_env(dict(os.environ))
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # Neutralize replace refs (git replace --graft, refs/replace/*) so a grafted parent cannot make an
    # orphaned start appear rooted through merge-base, which honours replacements by default. The on-disk
    # .git/info/grafts residual is out of scope (an accidental-case guardrail, disclosed in the residue).
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    try:
        return subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, text=True, timeout=5, env=env)
    except Exception:
        return None


# ROUND-2 FINDING 12: the protected line resolves from origin/HEAD FIRST, then falls back to a local
# main/master, so a repo with no remote (origin/HEAD unset) is not treated as having no protected line while
# a local main/master exists. When NONE of these resolves, there is genuinely no protected line to root
# against, and a MISSING protected line is not evidence a plain local branch is orphaned.
_BRANCH_ROOT_PROTECTED_REFS = ("origin/HEAD", "main", "master")


def _branch_root_protected_commit(repo):
    """The protected line's commit sha, trying origin/HEAD then a local main/master (finding 12), or None
    when no protected ref resolves at all (a repo with no origin/HEAD and no local main/master)."""
    for ref in _BRANCH_ROOT_PROTECTED_REFS:
        p = _branch_root_git(
            repo, "rev-parse", "--verify", "--quiet", "--end-of-options", "{}^{{commit}}".format(ref))
        if p is not None and p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    return None


def _branch_root_probe(repo, start):
    """Return ('rooted'|'orphaned'|'unknown'|'no-protected-ref', detail), based only on unmasked git exit
    statuses. 'no-protected-ref' (round-2 finding 12): no protected line (origin/HEAD or a local main/master)
    resolves, so the start cannot be shown orphaned; provided the start itself resolves to a real commit, the
    creation is allowed rather than denied (a missing origin is not orphan evidence)."""
    protected = _branch_root_protected_commit(repo)
    if protected is None:
        start_probe = _branch_root_git(
            repo, "rev-parse", "--verify", "--quiet", "--end-of-options", "{}^{{commit}}".format(start))
        if start_probe is not None and start_probe.returncode == 0 and start_probe.stdout.strip():
            return ("no-protected-ref", "no protected line (origin/HEAD or a local main/master) resolves, "
                                        "and a plain local branch from a resolvable start is not evidence of "
                                        "an orphan")
        return ("unknown", "no protected line resolves and the start point {!r} cannot be resolved either"
                           .format(start))
    start_probe = _branch_root_git(
        repo, "rev-parse", "--verify", "--quiet", "--end-of-options",
        "{}^{{commit}}".format(start))
    if start_probe is None or start_probe.returncode != 0 or not start_probe.stdout.strip():
        return ("unknown", "the branch start point {!r} cannot be resolved".format(start))
    merge = _branch_root_git(
        repo, "merge-base", protected, start_probe.stdout.strip())
    if merge is None:
        return ("unknown", "git merge-base could not be launched")
    if merge.returncode == 0 and merge.stdout.strip():
        return ("rooted", merge.stdout.strip())
    if merge.returncode == 1:
        # A shallow clone truncates history, so a missing merge base can mean the shared root was simply
        # not fetched, not that the branch is orphaned. In a shallow repo, treat exit 1 as unknown (ASK),
        # never a false orphaned deny.
        shallow = _branch_root_git(repo, "rev-parse", "--is-shallow-repository")
        if shallow is None or shallow.returncode != 0:
            return ("unknown", "the shallow-repository status could not be determined, so a missing "
                               "merge base cannot be told from an orphaned start")
        value = shallow.stdout.strip()
        if value == "true":
            return ("unknown", "the repository is shallow, so a missing merge base cannot be told from "
                               "an orphaned start; run `git fetch --unshallow` and retry")
        if value != "false":
            return ("unknown", "unexpected shallow-repository status {!r}".format(value))
        return ("orphaned", "git merge-base reported no common ancestor")
    return ("unknown", "git merge-base returned status {}".format(merge.returncode))


def branch_root(data):
    """brnrot PreToolUse/Bash guard over recognized branch-creation forms. NO-ASK posture: branch rooting is
    a hazard class (an orphan/unrooted branch dispatches work onto a retired root), so a confirmed orphan
    start DENIES, and a form this guard cannot prove rooted (an ambiguous/--orphan creation form, a
    dir-change or ambient repository-view override, an unresolved ancestry) also DENIES-and-educates
    fail-safe, naming the reachable correct action. ROUND-2 FINDING 12 (keep-working): it is -C-AWARE - a
    command-local '-C <dir>'/'--work-tree' redirect that RESOLVES to a concrete directory has its ancestry
    checked THERE (the -C fleet convention is honoured, not blocked): a rooted start ALLOWS, while a target
    that is not a rooted repository DENIES fail-safe via the ancestry-unknown path ('-C $VAR', '-C
    /nonexistent', '-C /etc', '--work-tree=/etc' all deny, since the probe finds no origin/HEAD merge base).
    A CHECKOUT/SWITCH '--track <ref>' names a rooted real start and ALLOWS (a 'git branch --track' is an
    unclassifiable form and DENIES, not an allow). A MISSING protected line (no origin/HEAD and no local
    main/master) is NOT evidence a plain local branch is orphaned, so a resolvable-start creation ALLOWS.
    ONLY a redirect whose worktree this guard cannot pin at all - a --git-dir/GIT_DIR/-c form, or a
    value-less/empty/relative-with-no-cwd -C/--work-tree that resolves 'opaque' - ALLOWS-WITH-NOTE (the CI
    branch-root gate remains the backstop) rather than denying. It never asks."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block(
            "aiqt_hooks: branch_root wired to unexpected event {!r}; failing closed"
            .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("brnrot")
    if tool_name != "Bash":
        return _allow()
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return _deny(
            "AIQT rule brnrot (branch-rooted-on-live-main): the Bash payload carried no readable "
            "command string, so branch creation could not be checked; failing closed.",
            "AIQT guardrail: denied a Bash call with no readable command (rule brnrot, fail-closed).")
    try:
        segments = _segments(command)
    except ValueError:
        # An unsupported LATER construct (a heredoc, a process substitution) must not discard a
        # proven-complete creation already recovered in the prefix: partial-lex and judge the recovered
        # segments, so a canonical orphan creation still DENIES (DENY outranks the parse error). A command
        # with no recovered creation falls back to the open-grammar allow (best-effort, disclosed).
        try:
            segments = [(seg.argv, seg.sep_after) for seg in _lex_command(command, partial=True)[0]]
        except ValueError:
            return _allow()  # even partial recovery failed: open grammar, best-effort

    # No-ask posture: branch rooting is a HAZARD class (an orphan/unrooted branch dispatches work onto a
    # retired root), so a case this guard cannot prove rooted DENIES-and-educates, naming the reachable
    # correct action (an explicit start point, a plain command from the target repo, or restoring
    # origin/HEAD). A confirmed orphan returns immediately; a cannot-prove deny's (reason, banner) is held in
    # pending_deny so a confirmed orphan elsewhere wins first.
    pending_deny = None
    pending_note = None
    saw_dir_change = False
    for tokens, _sep in segments:
        word = _command_word(tokens)
        if word in _CD_BUILTINS or word == "popd":
            # a cd/pushd/popd BEFORE the git segment moves the target out of the session cwd; popd lands on
            # an unknowable stack-top directory, so it too routes the following creation to a fail-safe deny.
            saw_dir_change = True
            continue
        if word != "git" or _has_info_flag(tokens):
            continue
        sub, args = _git_sub_and_args(tokens)
        start = _branch_creation_start(sub, args)
        if start is None:
            continue
        if start is _ASK_START:
            if pending_deny is None:
                pending_deny = (
                    "AIQT rule brnrot (branch-rooted-on-live-main): this command uses a branch/worktree form "
                    "this guard cannot classify with confidence (an --orphan, an abbreviated or negated "
                    "option, or an option of unknown arity); it may create a branch "
                    "from an unverified or orphaned start, so its ancestry cannot be checked here and it is "
                    "denied fail-safe. Re-issue it with an explicit start point (e.g. 'git switch -c <name> "
                    "origin/HEAD'); the CI branch-root gate remains the authoritative backstop.",
                    "AIQT guardrail: denied a branch-creation form whose start point is ambiguous "
                    "(rule brnrot); re-issue with an explicit start point.")
            continue
        cwd0 = data.get("cwd")
        cwd0 = cwd0 if isinstance(cwd0, str) and cwd0 else None
        probe_repo = cwd0
        if saw_dir_change or _ambient_repo_view_override():
            # a cd/pushd/popd earlier, or a non-cosmetic ambient GIT_* override: the repository view cannot be
            # reconciled with the session cwd and is NOT the honoured explicit-target form -> deny fail-safe.
            if pending_deny is None:
                pending_deny = (
                    "AIQT rule brnrot (branch-rooted-on-live-main): this branch-creation command runs under "
                    "a directory change or an ambient repository-view override this guard cannot reconcile "
                    "with the session repository (a cd/pushd in an earlier segment, or a non-cosmetic ambient "
                    "GIT_* variable), so its ancestry cannot be checked and it is denied fail-safe. Re-issue "
                    "it as a plain git command from the target repository.",
                    "AIQT guardrail: denied a branch creation whose repository this guard cannot reconcile "
                    "(rule brnrot); re-issue as a plain command from the target repo.")
            continue
        if not _segment_dir_simple(tokens):
            # ROUND-2 FINDING 12: a COMMAND-LOCAL redirect. Honour the -C fleet convention: resolve the
            # -C/--work-tree target and check ancestry THERE. A target that RESOLVES to a concrete directory
            # is PROBED there, so one that is not a rooted repo ('-C /nonexistent', '-C /etc', '--work-tree=
            # /etc', a '-C $VAR' that joins to a bogus path) DENIES via the ancestry-unknown fail-safe below.
            # ONLY a target this guard cannot pin to a worktree at all - a --git-dir/GIT_DIR/-c form, or a
            # value-less/empty/relative-with-no-cwd -C/--work-tree that resolves 'opaque' - ALLOWS-WITH-NOTE
            # rather than denying (the CI branch-root gate remains the backstop), never a hard block of a
            # legitimate explicit-target creation.
            # ROUND-7 (codex finding 2): a branch is created in the REPOSITORY git acts on, which --work-tree
            # NEVER relocates (it moves only the worktree); the repo is the ambient cwd, a -C target, or a
            # --git-dir. _segment_repo_dir resolves that (a -C target, or the session cwd when only --work-tree
            # is present), so 'git --work-tree=B branch new start' has its ancestry checked in the SESSION repo
            # it actually creates the branch in, not B. A --git-dir/GIT_DIR/-c form, or an unresolvable -C
            # target, stays the cannot-resolve allow-note (the CI branch-root gate remains the backstop).
            rd = _segment_repo_dir(tokens, cwd0)
            if isinstance(rd, str) and rd != "opaque":
                probe_repo = rd
            else:
                if pending_note is None:
                    pending_note = (
                        "AIQT guardrail (rule brnrot, branch-rooted-on-live-main): this branch creation "
                        "carries a command-local repository redirect whose target this guard cannot resolve "
                        "(a --git-dir/GIT_DIR/-c form, or an unresolvable -C target), so it "
                        "cannot check the start point's ancestry here; it is allowed and the CI branch-root "
                        "gate remains the backstop. Prefer an explicit '-C <dir>' target and start point.")
                continue
        if probe_repo is None:
            outcome, detail = ("unknown", "the session repository directory is unavailable")
        else:
            outcome, detail = _branch_root_probe(probe_repo, start)
        if outcome == "orphaned":
            return _deny(
                "AIQT rule brnrot (branch-rooted-on-live-main): start point {!r} has no merge base "
                "with origin/HEAD. Do not dispatch work onto the retired root; recut from origin/HEAD "
                "or replay the branch's unique commits onto it first.".format(start),
                "AIQT guardrail: denied branch creation from an orphaned start point (rule brnrot).")
        if outcome == "unknown" and pending_deny is None:
            pending_deny = (
                "AIQT rule brnrot (branch-rooted-on-live-main): branch-root ancestry could not be evaluated "
                "({}), so this guard cannot prove the start point is rooted and it is denied fail-safe. If "
                "origin/HEAD is missing, run `git remote set-head origin --auto`, then retry; confirm the "
                "repository and start point.".format(detail),
                "AIQT guardrail: denied a branch creation whose root ancestry is unresolved (rule brnrot); "
                "restore origin/HEAD, then retry.")
    if pending_deny is not None:
        return _deny(*pending_deny)  # a confirmed orphan / cannot-prove-rooted deny outranks an allow-note
    if pending_note is not None:
        return _allow_note(pending_note)  # an unresolvable command-local redirect (finding 12)
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

# Fallback-only raw-string probes, used when the shared tokenizer cannot parse the command (an unbalanced quote or an unsupported construct);
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
_RAW_COMMIT_MSG_GIT_RE = re.compile(r"(?is)\bgit\b.*?\bcommit\b")
_RAW_COMMIT_MSG_MARKER_RE = re.compile(r"`|\$\(")


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
    """FAIL-SAFE conservative scan when the shared tokenizer cannot parse the command (an unbalanced quote or an unsupported construct): an apparent
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
        return _deny(
            "AIQT rule gatdis (gate-discipline): the command could not be parsed by the shell lexer "
            "(likely unbalanced quotes) and it appears to run git commit or git am with a short -n, "
            "which on those verbs bypasses the verification hooks; it is denied fail-safe. Never weaken a "
            "gate to obtain a pass; re-issue it as a parseable command with no -n so the hooks run. {}"
            .format(_GATE_ALTS),
            "AIQT guardrail: denied an unparseable git commit/am that appears to carry -n (--no-verify) "
            "(rule gatdis, fail-safe); run the hooks, do not bypass them.")
    if _RAW_CHECKER_RE.search(command) and (
            _RAW_SWALLOW_RE.search(command) or _RAW_TRUNCATE_RE.search(command)):
        # ROUND-2 FINDING 14: the swallow/truncate checker-shape heuristic is too broad (a benign optional
        # probe is common), so even on the unparseable fallback it ALLOWS-WITH-NOTE rather than denying; only
        # the --no-verify bypass spellings above (a deliberate, unambiguous gate bypass) still deny fail-safe.
        return _allow_note(
            "AIQT guardrail (rule gatdis, gate-discipline): the command could not be parsed by the shell "
            "lexer (likely unbalanced quotes) and it appears to swallow or truncate a checker's failure "
            "signal ('|| true', '|| :', '| head', '| tail'). If it gates this work, run the checker bare and "
            "let its exit status stand (redirect output to a file if you need to page it), rather than "
            "discarding its failure signal; if it is a benign optional probe, this is allowed.")
    return _allow()


def gate_weakening(data):
    """gatdis (integ/gate-discipline), PreToolUse/Bash. DENY a git segment that bypasses its
    verification hooks: a --no-verify spelling (exact or conservative long prefix) on a subcommand
    that accepts it (commit, merge, push, pull, rebase, am), or the short -n on the two verbs where
    -n IS --no-verify (commit, am; on push -n is --dry-run and on merge/pull it is --no-stat, so it
    is deliberately not flagged there). ROUND-2 FINDING 14: the checker-shape HEURISTIC (a checker-shaped
    segment whose failure signal is swallowed by a following '|| true'/'|| :', or piped into a truncating
    sink head/tail) is too broad - a benign optional probe ('test -d /cache || true', 'pytest || true' while
    iterating) is common and is not a gate bypass - so it ALLOWS-WITH-NOTE (educating to run the gate bare if
    it genuinely gates the work), not deny. Only the CONFIRMED --no-verify bypass, a deliberate and
    unambiguous gate bypass, still DENIES (certain, returned immediately)."""
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
    pending_note = None  # the first heuristic gate-weakening allow-note; a certain --no-verify deny wins first
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
        # note). Only genuinely empty segments are skipped; a real intervening command (a non-empty
        # segment) still breaks adjacency and is NOT skipped.
        nxt_index = index + 1
        while nxt_index < len(segments) and not segments[nxt_index][0]:
            nxt_index += 1
        nxt = segments[nxt_index][0] if nxt_index < len(segments) else []
        # ROUND-2 FINDING 14: the checker-shape swallow ('|| true'/'|| :') and truncating-pipe ('| head/tail')
        # HEURISTIC is too broad - a benign optional probe ('test -d /cache || true', 'pytest || true' while
        # iterating, 'make | head' to glance at output) is common and is not a gate bypass - so it becomes an
        # ALLOW-WITH-NOTE, not a deny. Only the CONFIRMED --no-verify bypass above (a deliberate, unambiguous
        # gate bypass) still DENIES.
        if sep_after == "||" and _command_word(nxt) in _EXIT_SWALLOWS:
            if pending_note is None:
                pending_note = (
                    "AIQT guardrail (rule gatdis, gate-discipline): {!r} looks like a verification gate and "
                    "its failure would be swallowed by the following '|| {}'. If it genuinely gates this "
                    "work, do not swallow it: run it bare and let the exit status stand, so a failing check "
                    "does not read as a pass. If it is only a benign optional probe, this is allowed."
                    .format(_command_word(tokens), _command_word(nxt)))
        elif sep_after == "|" and _command_word(nxt) in _TRUNCATING_SINKS:
            if pending_note is None:
                pending_note = (
                    "AIQT guardrail (rule gatdis, gate-discipline): {!r} looks like a verification gate and "
                    "is piped into '{}', a truncating sink whose exit status replaces the checker's under "
                    "default pipeline semantics. If it gates this work, run it bare (or redirect the output "
                    "to a file and read that) so its failure signal is not discarded; if it is only a benign "
                    "output glance, this is allowed.".format(_command_word(tokens), _command_word(nxt)))
    if pending_note is not None:
        return _allow_note(pending_note)
    return _allow()


# --- sectvl: shell substitution in a git commit argument --------------------------------------------
def _commit_msg_subst_marker(value, opaque, value_index, seg):
    """The executable marker in a candidate git commit argument, or None. The per-token opacity signal
    distinguishes a substituting double-quoted/unquoted marker from a single-quoted or escaped literal;
    the final-token supplement covers the narrow unquoted '$(' split produced by _lex_command."""
    if not opaque:
        return None
    if "`" in value:
        return "a backtick"
    if "$(" in value:
        return "a $( command substitution"
    if value.endswith("$") and value_index == len(seg.argv) - 1 and seg.sep_after == "(":
        return "a $( command substitution"
    return None


_COMMIT_MSG_COMMAND_MODIFIERS = frozenset((
    "command", "exec", "builtin", "nohup", "time", "!"))
_COMMIT_MSG_ENV_VALUE_OPTS = frozenset((
    "-u", "--unset", "-C", "--chdir", "-S", "--split-string", "--argv0"))


def _commit_msg_command_index(tokens):
    """Index of this hook's effective command word after recognized leading command modifiers.

    This is deliberately local to commit_msg_subst: other guards rely on the shared _command_word's
    narrower contract. Leading shell assignments are skipped first. The command/exec/builtin/nohup/time/!
    modifiers advance one word; env additionally skips its leading option tokens, the separated values of
    its common value-taking options, and NAME=VALUE assignments. Returns len(tokens) when no command remains.
    """
    i = _command_word_index(tokens)
    n = len(tokens)
    while i < n:
        word = tokens[i].rsplit("/", 1)[-1]
        if word in _COMMIT_MSG_COMMAND_MODIFIERS:
            i += 1
            continue
        if word != "env":
            return i
        i += 1
        options = True
        while i < n:
            token = tokens[i]
            if _ENV_ASSIGN_RE.match(token):
                i += 1
                continue
            if options and token == "--":
                options = False
                i += 1
                continue
            if options and token.startswith("-") and token != "-":
                if "=" not in token and token in _COMMIT_MSG_ENV_VALUE_OPTS:
                    i += 2
                else:
                    i += 1
                continue
            break
    return i


def _commit_msg_subst_hit(seg):
    """Return (argument, marker) for the first post-subcommand hit in a recognized git commit segment.

    Every token after commit is inspected without Git option binding: Bash performs substitution before Git
    parses options or an end-of-options token, so neither an option value nor `--` makes a marker safe.
    """
    tokens = seg.argv
    command_index = _commit_msg_command_index(tokens)
    if (command_index >= len(tokens)
            or tokens[command_index].rsplit("/", 1)[-1] != "git"):
        return None
    sub, args = _git_sub_and_args(tokens[command_index:])
    if sub != "commit":
        return None
    offset = len(tokens) - len(args)
    for index, token in enumerate(args, offset):
        marker = _commit_msg_subst_marker(token, seg.argv_opaque[index], index, seg)
        if marker is not None:
            return token, marker
    return None


def _commit_msg_subst_fallback(command):
    """Conservative raw fallback for an unparseable command: an apparent git commit + substitution marker is
    a HAZARD-class (command-injection) case this guard cannot parse, so it DENIES-and-educates (hooks never
    ask; a possibly-executing substitution in a commit argument is blocked with the safe re-issue named).
    Anything else ALLOWS at the true boundary. The probes intentionally over-match."""
    if (_RAW_COMMIT_MSG_GIT_RE.search(command)
            and _RAW_COMMIT_MSG_MARKER_RE.search(command)):
        return _deny(
            "AIQT rule sectvl (tool-argument-validation): the command could not be parsed by the shell "
            "lexer and appears to carry a backtick or $( command substitution in a git commit argument, "
            "which the shell may execute BEFORE git processes the argument; it is denied fail-safe. Re-issue "
            "with single quotes, escape the marker as \\` or \\$(, or write the message to a file and use "
            "git commit -F <file>.",
            "AIQT guardrail: denied an unparseable git commit argument that appears to carry an executable "
            "command substitution (rule sectvl); re-issue single-quoted, escaped, or via -F <file>.")
    return _allow()


def commit_msg_subst(data):
    """sectvl (security/tool-argument-validation), PreToolUse/Bash. DENY-and-educate when a token after a
    recognized git commit subcommand is double-quoted or unquoted and carries a shell-executed backtick or
    $( command substitution (a command-injection hazard the shell runs before git sees the argument). Hooks
    never ask; the caller self-corrects by re-issuing single-quoted, escaped, or via git commit -F <file>."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: commit_msg_subst wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("sectvl")
    if tool_name != "Bash":
        return _allow()  # a present-but-different tool is out of scope (defensive; the matcher governs)
    tool_input = data.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return _deny(
            "AIQT rule sectvl (tool-argument-validation): the Bash payload carried no readable command "
            "string, so the git-commit argument substitution check could not run; it is denied fail-safe "
            "(the payload could not be parsed safely). Re-issue a well-formed Bash command.",
            "AIQT guardrail: denied a Bash call with no readable command; re-issue a well-formed command "
            "(rule sectvl, fail-safe).")
    try:
        segments = _lex_command(command)
    except ValueError:
        return _commit_msg_subst_fallback(command)
    for seg in segments:
        hit = _commit_msg_subst_hit(seg)
        if hit is None:
            continue
        argument, marker = hit
        return _deny(
            "AIQT rule sectvl (tool-argument-validation): this git commit argument {!r} carries {} in a "
            "lexical context that may substitute. Bash executes the marker BEFORE git processes the "
            "argument when it is double-quoted or unquoted, so it is denied. Re-issue with single quotes, "
            "escape the marker as \\` or \\$(, wrap the message in ANSI-C $'...' quoting, or write the "
            "message to a file and use git commit -F <file>."
            .format(argument[:80], marker),
            "AIQT guardrail: denied a git commit argument that appears to carry an executable command "
            "substitution (rule sectvl); re-issue single-quoted, escaped, or via -F <file>.")
    return _allow()


# --- secsec: an obvious hardcoded secret in a Write/Edit/MultiEdit/Bash write-form -------------------
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
# Write/Edit/MultiEdit/Bash, or a secret split across tokens/lines or built by concatenation; on the
# Bash path the command string is scanned as RAW TEXT (not tokenized by the shared lexer, not executed), so a secret
# assembled by concatenation or supplied through a shell variable or expansion is missed, but a redirect
# or an embedded '#' does NOT cause a Bash-path miss; and it does not catch a base64/obfuscated form.
#
# The pattern SOURCE STRINGS below are GENERATED from tools/check_secrets.py by
# tools/gen_secret_patterns.py and are drift-gated; NEVER hand-edit them, and NEVER runtime-import
# check_secrets. Edit tools/check_secrets.py and regenerate (gen_secret_patterns.py, then gen_hooks.py).
# BEGIN generated secret patterns (source: tools/check_secrets.py; regenerate with tools/gen_secret_patterns.py)
_SECSEC_PREFIX_SOURCES = [
    ('\\bgh[pousr]_[A-Za-z0-9]{16,}', 'GitHub token'),
    ('\\bgithub_pat_[A-Za-z0-9_]{20,}', 'GitHub fine-grained PAT'),
    ('\\bsk-[A-Za-z0-9]{20,}', 'OpenAI-style secret key'),
    ('\\bsk-proj-[A-Za-z0-9_-]{20,}', 'OpenAI project key'),
    ('\\bsk-ant-[A-Za-z0-9\\-_]{20,}', 'Anthropic key'),
    ('\\bAKIA[0-9A-Z]{16}\\b', 'AWS access key id'),
    ('\\bxox[baprs]-[A-Za-z0-9-]{10,}', 'Slack token'),
    ('\\bxapp-[A-Za-z0-9-]{10,}', 'Slack app-level token'),
    ('-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----', 'private key block'),
    ('\\beyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])', 'JWT (JSON Web Token)'),
    ('\\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])', 'Google API key'),
    ('\\b[sr]k_(?:live|test)_[0-9a-zA-Z]{20,}\\b', 'Stripe API key'),
    ('\\bglpat-[0-9A-Za-z_-]{20,}', 'GitLab personal access token'),
    ('\\bSG\\.[A-Za-z0-9_-]{22}\\.[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])', 'SendGrid API key'),
    ('\\bnpm_[A-Za-z0-9]{36}\\b', 'npm access token'),
    ('\\bpypi-AgEIcHlwaS[A-Za-z0-9_-]{50,}', 'PyPI upload token'),
    ('https://hooks\\.slack\\.com/services/T[A-Z0-9]{8,}/B[A-Z0-9]{8,}/[A-Za-z0-9]{24,}', 'Slack webhook URL'),
]
_SECSEC_ASSIGN_SOURCE = '(?ix)\n    (?:^|[^A-Za-z0-9])                       # start, or a non-alphanumeric\n    [A-Za-z0-9]*[_-]?                        # optional prefix such as aws_ or my-\n    (passwd|password|secret|token|api[_-]?key|access[_-]?key|\n       client[_-]?secret|auth[_-]?token|private[_-]?key|credential)\n    \\s*[:=]\\s*\n    (?:\n        (?P<q>[\'"])(?P<qvalue>(?:(?!(?P=q))[^\\n]){12,})(?P=q)  # quoted; qvalue excludes only the OPENING\n                                                     # delimiter (not both quotes), so a value that embeds\n                                                     # the other quote, e.g. "ab\'cd...", is not truncated\n      | (?P<value>[A-Za-z0-9+/=_.\\-]{16,})              # or unquoted; charset excludes {$<( so\n                                                     # templates and f-string holes cannot match\n    )\n    '
_SECSEC_PLACEHOLDER_SOURCE = '(?i)^(x{3,}|\\.{3,}|\\*{3,}|<[^>]+>|\\$\\{[^}]+\\}|\\$[A-Z_]+|(your|my|the)[_-]?\\w*|change[_-]?me|placeholder|example|sample|dummy|redacted|fake|test|todo|none|null|n/?a|actual_password_here)$'
_SECSEC_ENTROPY_ASSIGN_SOURCE = '(?x)\n    (?:^|[^A-Za-z0-9])\n    (?P<name>[A-Za-z][A-Za-z0-9_.\\-]*)\n    \\s*[:=]\\s*\n    (?:\n        # QUOTED: the WHOLE inter-quote value must be alphabet (closing quote required), so a value that\n        # embeds a non-alphabet char (an email\'s @, a URL\'s :) does NOT partially match on its prefix; no\n        # upper cap here because the closing quote bounds it, so a >150-char quoted secret still fires.\n        (?P<q>[\'"])(?P<qvalue>[A-Za-z0-9_./+=\\-]{40,})(?P=q)\n        # UNQUOTED: a possessive run (no backtracking) of 40 OR MORE alphabet chars that is NOT followed by @ or\n        # : (which would make it a prefix of a larger structured value such as an email or a URL); a longer\n        # all-alphabet unquoted value still fires on its 150-char prefix (char 151 is alphabet, not @/:).\n      | (?P<value>[A-Za-z0-9_./+=\\-]{40,}+)(?![@:])\n    )\n    '
_SECSEC_ENTROPY_MIN_LEN = 40
_SECSEC_ENTROPY_MAX_LEN = 150
_SECSEC_ENTROPY_THRESHOLD = 3.5
_SECSEC_CREDENTIAL_COMPONENTS = ('access', 'api', 'apikey', 'auth', 'client', 'credential', 'credentials', 'creds', 'key', 'pass', 'passphrase', 'passwd', 'password', 'pwd', 'secret', 'token')
_SECSEC_METADATA_COMPONENTS = ('alias', 'checksum', 'count', 'digest', 'dir', 'endpoint', 'file', 'fingerprint', 'id', 'length', 'name', 'path', 'public', 'size', 'type', 'uri', 'url', 'version')
# END generated secret patterns
# Compiled at module load from the generated source strings (stdlib re only; no runtime import of
# check_secrets). Recompiling from a pattern's .pattern string preserves its inline flags ((?ix)/(?i)),
# so these behave identically to check_secrets.py's own compiled objects.
_SECSEC_PREFIXES = [(re.compile(_pattern), _label) for _pattern, _label in _SECSEC_PREFIX_SOURCES]
_SECSEC_ASSIGN = re.compile(_SECSEC_ASSIGN_SOURCE)
_SECSEC_PLACEHOLDER = re.compile(_SECSEC_PLACEHOLDER_SOURCE)
# A JavaScript-style environment lookup (process.env.X, import.meta.env.X): a pure dotted-identifier
# path that BEGINS with a recognized env-access root is a CODE REFERENCE, not a literal secret, so it is
# excluded from the unquoted credential match (F-127). The root anchor is required, not a mere `env`
# segment anywhere, so a dotted token that only looks identifier-shaped (a Vault hvs.<random>, a PASETO
# v2.local.<payload>, or myorg.env.prod.<value> with env not at the root) stays caught. Mirrors
# check_secrets.py's DOTTED_PATH / _ENV_REF EXACTLY; hand-mirrored loop logic, not part of the generated
# region above (which carries only the single-sourced pattern strings).
_SECSEC_DOTTED_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_SECSEC_ENV_REF = re.compile(r"(?i)\A(?:process\.env|import\.meta\.env|env)\.")


# GD-121: the entropy-gated generic-assignment detector, hand-mirrored from check_secrets.py EXACTLY as
# the ASSIGN/env-ref logic above is. The regex source, the credential/metadata component sets, and the
# threshold are single-sourced through the generated region; the DECISION logic below is the hand mirror
# (not part of the generated region, so the parity battery in tools/selftest_aiqt_hooks.py guards it).
_SECSEC_ENTROPY_ASSIGN = re.compile(_SECSEC_ENTROPY_ASSIGN_SOURCE)
_SECSEC_CREDENTIAL_COMPONENT_SET = frozenset(_SECSEC_CREDENTIAL_COMPONENTS)
_SECSEC_METADATA_COMPONENT_SET = frozenset(_SECSEC_METADATA_COMPONENTS)
_SECSEC_COMPONENT_SPLIT = re.compile(r"[_.\-]+")
_SECSEC_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _secsec_shannon_entropy(value):
    """Shannon entropy (bits/char) of value; mirrors check_secrets._shannon_entropy EXACTLY."""
    if not value:
        return 0.0
    counts = {}
    for char in value:
        counts[char] = counts.get(char, 0) + 1
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _secsec_split_components(name):
    """Lower-cased exact identifier components (split on _ - . and camelCase); mirrors
    check_secrets._split_components EXACTLY."""
    components = []
    for part in _SECSEC_COMPONENT_SPLIT.split(name):
        if not part:
            continue
        for token in _SECSEC_CAMEL.findall(part):
            components.append(token.lower())
    return components


# The target field per SINGLE-FIELD tool: the text a Write/Edit would write, or the command a Bash call
# would emit. MultiEdit is in scope too but carries a LIST of edits rather than one field, so it is
# extracted separately (see _secsec_multiedit_text); its name is added to the in-scope tool set here.
_SECSEC_FIELD = {"Write": "content", "Edit": "new_string", "Bash": "command"}
_SECSEC_TOOLS = frozenset(_SECSEC_FIELD) | {"MultiEdit"}


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
        # Scan EVERY credential-named assignment on the line, not just the first, exactly as
        # check_secrets.py does: a placeholder assignment earlier on the line must not mask a real
        # one after it. The first real (non-placeholder) match on any line is the hit.
        for match in _SECSEC_ASSIGN.finditer(line):
            value = match.group("qvalue") or match.group("value") or ""
            value = value.strip()
            # An UNQUOTED value must additionally look like a credential (letters AND digits), the same
            # extra bar check_secrets.py applies, because an unquoted match is far likelier to be prose.
            if value and match.group("qvalue") is None:
                if not (any(c.isalpha() for c in value) and any(c.isdigit() for c in value)):
                    value = ""
                elif _SECSEC_DOTTED_PATH.fullmatch(value) and _SECSEC_ENV_REF.match(value):
                    value = ""
            if value and not _SECSEC_PLACEHOLDER.match(value):
                return "credential-named variable assigned a literal"
        # GD-121: only when neither a provider prefix nor a credential-named ASSIGN matched this line, try
        # the entropy-gated generic detector, mirroring check_secrets._entropy_assign_is_secret EXACTLY:
        # metadata component on the LHS wins, require a credential component, exclude PLACEHOLDER before
        # entropy, exclude an UNQUOTED env-lookup, require a letter and a digit, then the entropy floor.
        # Reaching here means neither loop above returned, so the "nothing else flagged the line" gate the
        # CI scanner applies holds here too. The matched value is never returned, only the label.
        for match in _SECSEC_ENTROPY_ASSIGN.finditer(line):
            value = (match.group("qvalue") or match.group("value") or "").strip()
            if not value:
                continue
            components = _secsec_split_components(match.group("name"))
            if any(c in _SECSEC_METADATA_COMPONENT_SET for c in components):
                continue
            if not any(c in _SECSEC_CREDENTIAL_COMPONENT_SET for c in components):
                continue
            if _SECSEC_PLACEHOLDER.match(value):
                continue
            if match.group("qvalue") is None:
                if _SECSEC_DOTTED_PATH.fullmatch(value) and _SECSEC_ENV_REF.match(value):
                    continue
            # entropy + letter/digit on the first _SECSEC_ENTROPY_MAX_LEN chars (capture cap), mirroring
            # check_secrets: full value seen by the env-ref exclusion above, prefix judged for entropy.
            candidate = value[:_SECSEC_ENTROPY_MAX_LEN]
            if not (any(c.isalpha() for c in candidate) and any(c.isdigit() for c in candidate)):
                continue
            if _secsec_shannon_entropy(candidate) >= _SECSEC_ENTROPY_THRESHOLD:
                return "high-entropy literal assigned to a credential-like name"
    return None


def _secsec_multiedit_text(tool_input):
    """Return the text a MultiEdit would introduce: the newline-joined concatenation of the new_string
    value of each edit in tool_input["edits"] (a LIST of dicts, each carrying the "new_string" that edit
    would add). Return None to signal FAIL-CLOSED - the edits field absent or not a list, or ANY element
    not a dict or carrying no string new_string - so the caller denies, consistent with the handler's
    posture that a payload the scan cannot read is blocked. Edits are joined on a newline so each edit's
    text stays on its own line for the per-line scan and no secret is split or fused across the boundary."""
    edits = tool_input.get("edits")
    if not isinstance(edits, list):
        return None
    parts = []
    for edit in edits:
        if not isinstance(edit, dict):
            return None
        new_string = edit.get("new_string")
        if not isinstance(new_string, str):
            return None
        parts.append(new_string)
    return "\n".join(parts)


def secrets_shift_left(data):
    """secsec (security/keep-secrets-out), PreToolUse on Write|Edit|MultiEdit|Bash: DENY a call that would
    write an obvious hardcoded secret. Fail-closed like the other PreToolUse controls: a missing tool_name
    denies, a non-dict tool_input denies (the payload cannot be read), and a present tool in scope whose
    target payload is absent or malformed denies (the check cannot read what would be written). A present
    tool NOT in {Write, Edit, MultiEdit, Bash} is out of scope and allows. The target text is the Write
    content, the Edit new_string, the newline-joined new_string values of a MultiEdit's edits (the text
    being introduced), or the Bash command string (the write-form path, best-effort). A DENY names the
    pattern label only, never the secret."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: secrets_shift_left wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("secsec")
    if tool_name not in _SECSEC_TOOLS:
        return _allow()  # out of scope (defensive; the matcher governs Write/Edit/MultiEdit/Bash)
    # A non-dict tool_input cannot answer the scan; fail CLOSED cleanly in-handler (mirrors git_discard's
    # isinstance-dict guard) rather than raising an AttributeError only the dispatcher would catch.
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return _deny(
            "AIQT rule secsec (keep-secrets-out): the {} payload carried no readable tool_input, so the "
            "secret scan could not run over what would be written; failing closed.".format(tool_name),
            "AIQT guardrail: denied a {} call with no readable payload (rule secsec, fail-closed)."
            .format(tool_name))
    if tool_name == "MultiEdit":
        target = _secsec_multiedit_text(tool_input)
        if target is None:
            return _deny(
                "AIQT rule secsec (keep-secrets-out): the MultiEdit payload carried no readable edits "
                "list, so the secret scan could not run over what would be written; failing closed.",
                "AIQT guardrail: denied a MultiEdit call with no readable edits (rule secsec, "
                "fail-closed).")
    else:
        field = _SECSEC_FIELD[tool_name]
        target = tool_input.get(field)
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


# --- gensrc generated-artefact edit guard ------------------------------------------------------------
# A NEW constant set, deliberately NOT reusing FILE_PATH_TOOLS, which is ("Read", "Write", "Edit"): it
# omits MultiEdit and includes Read, so it does not describe this control's matcher. Mirrors the
# _SECSEC_TOOLS idiom.
_GENSRC_TOOLS = ("Write", "Edit", "MultiEdit")   # MultiEdit carries ONE top-level file_path
_GENSRC_REGISTRY_REL = os.path.join(".aiqt", "gensrc.json")
_GENSRC_VERSION = 1
_GENSRC_MAX_BYTES = 1_000_000  # the real registry is ~5 KB; a larger one is malformed (SECA bound)


def _gensrc_fail_ask(detail):
    """The cannot-evaluate outcome shared by every gensrc_guard branch that cannot PROVE a registry match.
    Hooks never ask, and these branches are NOT confirmed generated-artefact edits: they are cases where
    the guard could not run (an unreadable/malformed payload, a non-git session, an outside-repo target, a
    resolution fault). A PRESENT-but-unreadable/malformed registry is NOT among them: it fails CLOSED (DENY)
    in gensrc_guard's 'bad' branch (finding 8), never routing here. Denying these would block legitimate
    Write/Edit/MultiEdit calls
    (an edit outside the repo, or in a non-git session) and stall an
    unattended orchestrator, and there is no confirmed hazard or reachable "edit the source" action to
    educate toward, so the edit is ALLOWED with an informational note. The CI generated-artefact drift gate
    remains the authoritative backstop; only a CONFIRMED registry match (gensrc_guard) denies-and-educates."""
    return _allow_note(
        "AIQT guardrail (rule gensrc, generated-artefact-source-only): the generated-artefact edit guard "
        "could not clear this call ({}), so it is allowed. If you are editing a generated artefact, edit its "
        "source and regenerate instead; the CI drift gate remains the backstop.".format(detail))


def _load_gensrc_registry(root):
    """Read <root>/.aiqt/gensrc.json AT DECISION TIME and return one of ("absent", None),
    ("ok", entries), or ("bad", detail). `entries` is the list of (kind, target, sources, regenerate)
    tuples in registry order with kind=block entries dropped (a path guard cannot see which lines an
    edit touches; the block drift gates backstop those).

    ABSENT (FileNotFoundError from the lstat probe: ENOENT on the file or a missing .aiqt/ component) is
    the inert boundary: a repo with no registry gets no coverage (adopters author their own). Every OTHER
    read fault is BAD, never absent, so an unreadable input can never read as clean (integ-check-fails-
    closed-on-unreadable): a registry that is not a regular file (a symlink dangling or redirecting, a
    FIFO, a directory, a socket, a device; not a trusted regular file), a
    PermissionError/NotADirectoryError/IsADirectoryError, a non-UTF-8 decode, an oversize file (the bound
    is on BYTES via a binary read), malformed JSON, a non-int/unknown version, a non-list `generated`, a
    malformed entry, or a control character (NUL included) in an entry target (rejected BEFORE the
    block-skip). We do NOT use os.path.exists, which swallows EACCES (the trap gen_hooks documents at
    gen_hooks.py:79).
    Per-entry validation mirrors _canonical_target (tools/gen_gensrc.py:193-213), because gen_gensrc's
    validation only guarantees THIS repo's registry; an adopter-authored or hand-tampered registry the
    guard cannot fully read is BAD (it cannot prove no-match). Unknown extra keys within version 1 are
    tolerated: the version field pins the schema, and strictness on additions would break a
    forward-compatibly authored registry. This loader is BEST-EFFORT against the ACCIDENTAL case, not a
    hardened TOCTOU check: it rejects a STATIONARY non-regular registry and fails a delete race safe to
    BAD, but a registry concurrently SWAPPED to a different file type in the lstat-to-open window is a
    disclosed residual (see the comment below and the control residue), a sibling of the hard-link,
    case-insensitive-filesystem, and NFC/NFD aliases."""
    path = os.path.join(root, _GENSRC_REGISTRY_REL)
    # A generated-artefact registry must be a trusted REGULAR file. Anything that is NOT a regular file (a
    # symlink dangling or redirecting, a FIFO, a directory, a socket, a device) is BAD: a symlink could
    # make a foreign or nonexistent target read as this repo's registry, and a FIFO at the path would block
    # the open until the hook timeout. os.lstat is probed BEFORE the open: it does NOT follow a symlink and
    # does NOT block on a FIFO, so a STATIONARY non-regular registry (a symlink, FIFO, directory, ...) is
    # rejected as non-regular. A FileNotFoundError from lstat is genuine absence (a non-symlink ENOENT) ->
    # inert absent (the ONLY inert-absent path); any other lstat OSError is BAD; a path that resolves to
    # something other than a regular file is BAD; and an open-time disappearance (a delete race in the
    # lstat->open window) fails safe to BAD, handled at the open below. This read is BEST-EFFORT against
    # the ACCIDENTAL case, not a hardened check: a registry concurrently SWAPPED to a different type in the
    # lstat->open window (the lstat sees a regular file, the open then binds a substituted symlink/FIFO) is
    # a DISCLOSED residual OUTSIDE the accidental-case scope - a sibling of the hard-link, case-insensitive
    # -filesystem, and NFC/NFD normalization residuals. It requires a concurrent writer inside the repo,
    # who could equally just author the registry, so O_NOFOLLOW/fstat is deliberately NOT added; the
    # disclosure is the honest resolution.
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return ("absent", None)
    except OSError as exc:
        return ("bad", "the registry could not be stat'd ({})".format(exc))
    if not stat.S_ISREG(st.st_mode):
        return ("bad", "the registry is not a regular file (a symlink, FIFO, directory, ...); "
                       "a generated-artefact registry must be a regular file")
    try:
        # BINARY read so the size bound is on BYTES, not characters: read one byte past the cap, and if the
        # file exceeds the cap treat it as malformed (a SECA resource bound a multibyte-oversize file must
        # not slip through a char-count read). Decode is done explicitly below so a decode fault is BAD.
        with open(path, "rb") as handle:
            raw_bytes = handle.read(_GENSRC_MAX_BYTES + 1)
    except FileNotFoundError:
        # The lstat above saw a regular file, but it is gone at open: a concurrent DELETE race in the
        # lstat->open window. This is BAD (gensrc_guard denies it fail-closed), never absent: absence is ONLY
        # the lstat-probe FileNotFoundError, so a benign delete race can never read as the inert no-coverage
        # ALLOW.
        return ("bad", "the registry disappeared during the read (a concurrent change); failing safe")
    except OSError as exc:
        return ("bad", "the registry could not be read ({})".format(exc))
    if len(raw_bytes) > _GENSRC_MAX_BYTES:
        return ("bad", "the registry exceeds the {}-byte bound".format(_GENSRC_MAX_BYTES))
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ("bad", "the registry is not valid UTF-8")
    try:
        obj = json.loads(raw)
    except ValueError:
        return ("bad", "the registry is malformed JSON")
    if not isinstance(obj, dict):
        return ("bad", "the registry is not a JSON object")
    version = obj.get("version")
    # type(version) is int, not `== _GENSRC_VERSION` alone: `True == 1` in Python, so a JSON bool version
    # (true) would else read as version 1. type(True) is bool, not int, so a bool (or a string "1", a
    # float) is rejected. A future version 2 is also BAD (gensrc_guard denies it fail-closed), never a
    # misread of an unknown shape.
    if type(version) is not int or version != _GENSRC_VERSION:
        return ("bad", "unknown registry version (expected {})".format(_GENSRC_VERSION))
    generated = obj.get("generated")
    if not isinstance(generated, list):
        return ("bad", "the registry carries no generated list")
    entries = []
    for item in generated:
        if not isinstance(item, dict):
            return ("bad", "a registry entry is not an object")
        kind = item.get("kind")
        target = item.get("target")
        sources = item.get("sources")
        regenerate = item.get("regenerate")
        if kind not in ("file", "tree", "block"):
            return ("bad", "a registry entry has an unknown kind")
        # target: a clean POSIX-relative path (no backslash, not absolute, no empty/'.'/'..' segment),
        # trailing '/' ONLY on a tree target (the tree marker); mirrors _canonical_target.
        if not isinstance(target, str) or not target or "\\" in target or _is_absolute(target):
            return ("bad", "a registry entry has a malformed target")
        # Reject a control character (any codepoint < 0x20, NUL included, or DEL 0x7f) in ANY target,
        # BEFORE the kind==block skip below, so a NUL-bearing block entry is BAD (gensrc_guard denies it
        # fail-closed), never silently dropped to zero entries and read as the inert no-coverage ALLOW.
        if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in target):
            return ("bad", "a registry entry target contains a control character")
        has_trailing = target.endswith("/")
        body = target[:-1] if has_trailing else target
        if any(segment in ("", ".", "..") for segment in body.split("/")):
            return ("bad", "a registry entry has a malformed target")
        if has_trailing and kind != "tree":
            return ("bad", "a non-tree entry target must not end with '/'")
        if kind == "tree" and not has_trailing:
            return ("bad", "a tree entry target must end with '/'")
        if not isinstance(sources, list) or not sources or not all(
                isinstance(source, str) and source for source in sources):
            return ("bad", "a registry entry has malformed sources")
        if not isinstance(regenerate, str) or not regenerate:
            return ("bad", "a registry entry has a malformed regenerate command")
        if kind == "block":
            continue  # blocks are excluded from matching by design; the block drift gates backstop them
        entries.append((kind, target, sources, regenerate))
    return ("ok", entries)


_GENSRC_MATCH_FAULT = object()  # sentinel: a resolution/containment fault so no-match cannot be PROVEN


def _gensrc_within(candidate, parent):
    """A D2-LOCAL, ERROR-AWARE containment tri-state: "in" when the realpath of `candidate` is `parent`
    itself or lies under it (whole-component os.path.commonpath), "out" when it is proven outside, and
    "err" when the check FAULTS (a symlink loop, or commonpath given mixed or foreign inputs such as two
    Windows drive roots). DELIBERATELY NOT the shared _path_is_within, which errs to True (matched) so
    git_discard REFUSES to write recovery data inside a tree it cannot clear; here an unresolved
    containment must not read as a match NOR as a silent no-match ALLOW, so the fault surfaces as "err" and
    the caller allows the edit with a warning systemMessage (_gensrc_fail_ask; there is no ask posture)."""
    try:
        cand = os.path.realpath(candidate)
        base = os.path.realpath(parent)
        if cand == base or os.path.commonpath([cand, base]) == base:
            return "in"
        return "out"
    except (OSError, ValueError):
        return "err"


def _gensrc_match(entries, target, root_c):
    """The first registry entry that `target` (an already-realpath'd absolute path) matches, as
    (entry_target, sources, regenerate); None on a PROVEN no-match; or the _GENSRC_MATCH_FAULT sentinel
    when a resolution or containment fault means no-match cannot be proven (the handler turns the
    sentinel into an allow with a warning systemMessage via _gensrc_fail_ask, never a match and never a
    silent allow; there is no ask posture). A FILE entry matches on
    realpath EQUALITY; a TREE entry matches when `target` is the tree root or lies under it, by
    component-boundary containment (_gensrc_within), never a raw string prefix, so gen-extra/ never
    matches gen/ and GEN.md.bak never matches GEN.md. Each entry target is repo-root-relative, so it is
    joined onto root_c and canonicalized under try/except: an unresolvable entry is a fault, not a
    no-match. Block entries were dropped at load."""
    for kind, entry_target, sources, regenerate in entries:
        try:
            entry_c = os.path.realpath(os.path.join(root_c, entry_target))
        except (OSError, ValueError):
            return _GENSRC_MATCH_FAULT
        if kind == "file":
            if entry_c == target:
                return (entry_target, sources, regenerate)
        else:  # tree
            verdict = _gensrc_within(target, entry_c)
            if verdict == "err":
                return _GENSRC_MATCH_FAULT
            if verdict == "in":
                return (entry_target, sources, regenerate)
    return None


def gensrc_guard(data):
    """gensrc (integ/generated-artefact-source-only), PreToolUse on Write|Edit|MultiEdit: DENY-and-educate a
    Write/Edit/MultiEdit that hand-edits a generated artefact registered in the per-repo
    .aiqt/gensrc.json, read AT DECISION TIME. A REGISTRY-DRIVEN PATH guard, not a content judge: it
    fires only when the file_path resolves onto a kind=file or kind=tree registry entry. Coverage is
    exactly the registry, so an ABSENT registry is the inert ALLOW by design; kind=block entries are
    EXCLUDED (a path guard cannot see which lines an edit touches); Bash is EXCLUDED by design
    (regeneration itself runs through Bash), so the matcher is Write|Edit|MultiEdit only. NO-ASK posture:
    a CONFIRMED registry match DENIES and names the source to edit and the regenerate command (a reachable
    correct action). A PRESENT-but-unreadable/malformed registry ALSO DENIES, fail-closed (round-2 finding
    8): a corrupted registry must not silently disable the generated-artefact protection, so a cannot-read of
    a PRESENT .aiqt/gensrc.json is a cannot-evaluate resolved to the safe (deny) outcome for this protective
    guard. The remaining cannot-evaluate branches (each routed through _gensrc_fail_ask: an unreadable
    tool_name (an empty string, a list, a bool), a non-dict tool_input, a missing or unreadable file_path, a
    control character in file_path, no session cwd, a non-git session, an unresolvable target or repo root, a
    target outside the repo or a containment fault, a registry entry that cannot be resolved for containment)
    are NOT confirmed generated-artefact edits, have no "edit the source" action to name, and are outside any
    expected coverage, so they ALLOW with an informational note rather than block a legitimate edit; a
    genuinely-ABSENT registry is likewise the inert ALLOW (adopters author their own). The CI
    generated-artefact drift gate remains the authoritative backstop. Besides a confirmed registry match, the
    guard denies fail-closed in exactly three cases: a missing tool_name (the shared fail-closed contract), a
    PRESENT-but-unreadable/malformed registry, and a mis-wired event (a hard block, exit 2). The repo root is
    the git toplevel of the SESSION cwd via the scrubbed _recovery_toplevel primitive (NOT
    _gen_common.repo_root, which falls back to cwd and would fabricate a root)."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: gensrc_guard wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        # A missing field cannot be matched. This is one of three fail-closed denies, with a present but
        # unreadable/malformed registry and a mis-wired event (the hard block above).
        return _deny_missing_tool_name("gensrc")
    if not isinstance(tool_name, str) or not tool_name:
        # A present-but-unreadable tool_name (an empty string, a list, a bool) cannot be matched against
        # the scope set; allow with a warning systemMessage rather than silently allow. The fail-closed
        # denies are a MISSING tool_name, a present but unreadable/malformed registry, and a mis-wired event.
        return _gensrc_fail_ask("the tool_name was unreadable")
    if tool_name not in _GENSRC_TOOLS:
        return _allow()  # out of scope (defensive; the matcher governs Write/Edit/MultiEdit)
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return _gensrc_fail_ask("the {} payload carried no readable tool_input".format(tool_name))
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return _gensrc_fail_ask("the {} payload carried no readable file_path".format(tool_name))
    # A control character (NUL included) in file_path is malformed input that would also raise inside
    # os.path.realpath ("embedded null byte"); reject it here so it allows with a warning systemMessage,
    # never crashes.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in file_path):
        return _gensrc_fail_ask("the {} payload file_path contains a control character".format(tool_name))
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return _gensrc_fail_ask("the payload carried no session cwd, so the repo root cannot be resolved")
    # The scrubbed rev-parse primitive (every ambient GIT_* removed), so an ambient decoy repo cannot
    # redirect the registry read; None means the cwd is outside any git work tree (a non-git session).
    root = _recovery_toplevel(cwd)
    if root is None:
        return _gensrc_fail_ask("the session cwd is outside any resolvable git work tree")
    status, payload = _load_gensrc_registry(root)
    if status == "absent":
        return _allow()  # the inert boundary: no registry, no coverage (adopters author their own)
    if status == "bad":
        # ROUND-2 FINDING 8: a PRESENT-but-unreadable/malformed registry is a cannot-evaluate for a
        # PROTECTIVE guard, so it fails CLOSED (DENY), never allow-note. Allow-noting it let a crippled or
        # corrupted .aiqt/gensrc.json disable the generated-artefact restriction outright (garble the
        # registry, then hand-edit any generated file). Only a genuinely-ABSENT registry (adopters author
        # their own) and a non-git session, where no generated-file protection is expected, stay allow /
        # allow-note; a PRESENT registry that cannot be read denies and names the (Bash-side) fix.
        return _deny(
            "AIQT rule gensrc (generated-artefact-source-only): the .aiqt/gensrc.json registry is PRESENT "
            "but could not be read ({}), so this guard cannot tell whether the target is a generated "
            "artefact. It fails closed and denies the edit rather than let a corrupted or unreadable "
            "registry silently disable the generated-artefact protection. Regenerate or repair "
            ".aiqt/gensrc.json (its generator runs through Bash, which this guard does not cover), then "
            "retry.".format(payload),
            "AIQT guardrail: denied a {} because .aiqt/gensrc.json is present but unreadable/malformed, a "
            "fail-closed cannot-evaluate (rule gensrc); regenerate or repair the registry, then retry."
            .format(tool_name))
    entries = payload
    if not entries:
        return _allow()  # a registry of only block entries has nothing this path guard can match
    # A relative file_path can only arrive via MultiEdit (abs-paths does not cover it); joining it onto
    # cwd matches the platform's own resolution. Canonicalize both to realpaths for the match; a
    # resolution fault (a symlink loop, an unresolvable path) is an unresolvable target -> allow with a
    # warning systemMessage, NOT an uncaught crash the dispatcher would turn into an exit-2 hard DENY.
    try:
        target = os.path.realpath(file_path if _is_absolute(file_path) else os.path.join(cwd, file_path))
        root_c = os.path.realpath(root)
    except (OSError, ValueError):
        return _gensrc_fail_ask("the target or repo root could not be resolved (an unresolvable path)")
    within = _gensrc_within(target, root_c)
    if within != "in":
        # "out" (proven outside) OR "err" (a containment fault) both allow with a warning systemMessage: an
        # uncleared target cannot be judged against the registry of THIS repo, so it must never read as a
        # silent allow.
        return _gensrc_fail_ask("the target canonicalizes outside the resolved repo, or the containment "
                                "check could not be cleared, so it cannot be judged against the registry "
                                "of this repo")
    match = _gensrc_match(entries, target, root_c)
    if match is _GENSRC_MATCH_FAULT:
        return _gensrc_fail_ask("a registry entry could not be resolved for containment, so a no-match "
                                "cannot be proven")
    if match is None:
        return _allow()  # not a registered generated artefact
    entry_target, sources, regenerate = match
    reason = ("AIQT rule gensrc (generated-artefact-source-only): {} is a generated artefact ({} in "
              ".aiqt/gensrc.json); it is changed only through its source, so a hand-edit is denied. Edit {} "
              "and regenerate with '{}' instead; source and derivative land in the same change."
              .format(file_path, entry_target, ", ".join(sources), regenerate))
    banner = ("AIQT guardrail: denied a {} to the generated artefact {} (rule gensrc): edit the "
              "source and regenerate ({}).".format(tool_name, entry_target, regenerate))
    return _deny(reason, banner)


# --- the orchestrator-integrity suite ----------------------------------------------------------
# One registry, one state directory, one PURE decision core (decide_yield), one delivery substrate; the
# six components are thin bindings over them. The whole suite is REGISTRY-SCOPED: BY DEFAULT, with no
# .aiqt/orchestration.local.json or .aiqt/orchestration.json at the session cwd's git-resolved toplevel
# (_orch_root, the scope every component except orch_truncation_guard uses) it is inert (the gensrc.json
# precedent), with two disclosed exceptions, both in orch_truncation_guard. First, that guard's scope is
# not the session repo root alone but the UNION of the cwd's physical ancestor chain and any git-resolved
# toplevel (_orch_registry_walk, _orch_git_toplevel_has_registry), and its pre-scope denies (a malformed
# tool_name, a Bash call's malformed cwd, an unwalkable cwd) apply before that scope in every session,
# registry or not; a malformed tool_input is checked only after the scope (inert with no registry). Second, in the opt-in
# registry-required mode (AIQT_ORCH_REQUIRE_REGISTRY set to anything but an explicit off value,
# _orch_registry_required) that guard is NOT inert with no registry: a Bash call that passes its pre-scope
# checks with no registry on that chain or at a git-resolved toplevel is DENIED, and so is one whose nearest
# registry entry the discovery probe cannot confirm (_ORCH_REG_CANNOT_EVALUATE: a discovery fault is not a
# registry). No other suite component reads that variable, so strict mode changes no other component's
# outcome. Outside the suite, the write-scope guard locates its declaration through this registry and is
# NOT inert on an absent one: _load_write_scope falls back to the XDG default state directory, and a
# declaration there arms slice confinement (see _orch_registry for every caller's reading of 'absent'). The
# backlog guards
# additionally require a live orchestrator lease or a
# declared mode record, so bounded workers and plain sessions never inherit the global backlog. The
# stop path fails OPEN on a guard's own error (which can never wedge a session) but DENIES on a backlog
# cannot-evaluate (ignorance refuses the wind-down); the schedule path fails CLOSED on
# cannot-evaluate (a denied tool call cannot wedge anything), bounded by a denial cap. See
# .aiqt/core/hooks/ORCHESTRATION.md for the registry schema and the AEI v1 protocol.

_ORCH_REGISTRY_FILES = (".aiqt/orchestration.local.json", ".aiqt/orchestration.json")
_ORCH_BLOCKER_KINDS = frozenset(
    ("tracked-task", "human-decision", "external", "foreign-lease", "not-before"))
_ORCH_STATES = frozenset(("open", "closed", "proposed"))
_ORCH_LOOP_BOUND = 2          # stop-path denies per epoch before ALLOW_WITH_FINDINGS
_ORCH_SCHEDULE_CAP = 3        # schedule-path denies on an unchanged basis before findings
_ORCH_MAX_NAMED = 10          # actionable items named in a deny message
# The `Operating-mode:` declaration is parsed per PHYSICAL line (the reader splits on newlines first, so the
# value can never cross a line boundary): optional leading horizontal whitespace, then the key, then the value
# is the REST OF THAT LINE ONLY. A present declaration whose value does not classify (empty or unrecognized)
# fails closed to guards-armed rather than falling through to the no-marker None.
_ORCH_MODE_DECL_LINE_RE = re.compile(r"^[ \t]*Operating-mode:[ \t]*(.*)$")
# The mode grammar (used for both the declaration value and the JSON `mode` string): a PREFIX/word-anchored
# match on the trimmed lowercased value, never a substring, so `disattended` (begins `dis`) and `not-attended`
# (begins `not`) do NOT match and fail closed to armed, while `unattended; continuous` and the real compound
# `attended (ipad); continuous mode` begin with the mode word and classify. Order: unattended before attended.
_ORCH_MODE_UNATTENDED_RE = re.compile(r"unattended(\b|$)")
_ORCH_MODE_ATTENDED_RE = re.compile(r"attended(\b|$)")
# The guards-armed posture a present-but-unusable mode marker fails closed to: it carries the `unattended`
# token, so the ask blocker arms on it and _orch_scope_live reads it as a live (non-None) mode. A recognized
# mode value carries the `attended`/`unattended` family token (so both `unattended` and `attended` spellings,
# and their compound forms, are recognized); a present value outside that family is unrecognized and fails
# closed to this posture rather than silently disarming.
_ORCH_MODE_ARMED = "unattended"
_ORCH_MODE_MAX_BYTES = 1 << 20  # _orch_mode_read reads at most this many bytes; a larger mode file fails closed
_ORCH_ESCAPE_NAME = "ESCAPE-ALLOW-YIELD"
_ORCH_QUIET_CLAIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*min(?:ute)?s?\b")  # minutes number; the "quiet" gate is applied separately
# A human-decision blocker ref must look like a decision id (uppercase-prefixed, for example XY-12),
# never a bare lowercase word or a session-id substring, so a common word present in the pending
# surface cannot forge a human-decision block. Mirrors the record-drift typed-ref discipline.
_ORCH_DECISION_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-[A-Z0-9][A-Za-z0-9-]*$")
_ORCH_CLOCK_SKEW = 300  # seconds of tolerance for a proof timestamp slightly ahead of the guard clock
_ORCH_ATTEST_APPROVED = frozenset(("accepted", "landed"))  # FIX 3: a ref counts only from an approved row


def _orch_now():
    """The clock-read UTC now (tstamp: a recorded timestamp is read from the clock, never recalled)."""
    return datetime.datetime.now(datetime.timezone.utc)


def _orch_parse_utc(text):
    """Parse an ISO-8601 UTC string tolerantly ('Z' accepted); None on anything unparseable, so an
    unreadable timestamp can never satisfy a freshness check."""
    if not isinstance(text, str) or not text:
        return None
    try:
        value = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value


def _orch_sha256_hex(text):
    """The sha256 hex of a single register line's bytes (the tip identity a chained register anchors)."""
    return __import__("hashlib").sha256(text.encode("utf-8")).hexdigest()


def _orch_root(data):
    """The session repo root via the scrubbed rev-parse primitive, or None (out of scope)."""
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None
    return _recovery_toplevel(cwd)


# Orchestration-scope discovery (round 4): the truncation guard decides scope by the UNION of two legs.
# Leg one walks the session cwd's PHYSICAL ancestor chain directly with no-follow, descriptor-anchored
# lookups and needs no git at all, so a git discovery failure alone (no git binary on PATH, a
# dubious-ownership refusal, a broken config, a bare repository, a cwd inside a .git directory, a timeout)
# never denies an ordinary session BY DEFAULT: with no registry on the walk and none at a git-resolved
# toplevel the session is out of scope (denied instead in the opt-in registry-required mode,
# _orch_registry_required), while the same session inside an orchestrated tree still finds the registry on
# the walk and keeps the guard active. Leg two (restored from the rev-parse scoping after the round-4
# finding) applies where git DOES resolve a toplevel for the cwd: core.worktree (set in a repository
# config or a gitfile's gitdir target) can point the work tree OFF the cwd's physical ancestor chain,
# where the walk alone would never visit its registry, so that toplevel's registry is consulted as well
# (_orch_git_toplevel_has_registry). BY DEFAULT git success can only ADD a deny and a git failure alone
# never denies; in registry-required mode a git failure where the registry is reachable only through the
# git toplevel reads as ABSENT and is denied, and git success can then remove that deny. The sibling orchestration guards still root via the scrubbed rev-parse primitive (_orch_root).
_ORCH_WALK_BOUND = 4096  # ancestor-chain safety bound; a deeper chain is a walk failure, never an allow
# O_PATH (Linux): a walk step then needs only SEARCH permission on the chain, exactly as path resolution
# itself does, so a search-only (execute-only) ancestor such as a shared parent directory does not fail the
# walk; where O_PATH is unavailable the O_RDONLY fallback additionally requires read permission on each
# ancestor, an over-DENY in the fail direction (never an allow) on such platforms.
_ORCH_O_WALK = getattr(os, "O_PATH", os.O_RDONLY)


# The registry probe's THIRD value (round 5): a registry entry the no-follow lookups can neither cleanly rule
# out nor confirm as a regular registry file. It is a non-empty string, so it is TRUTHY: every boolean
# reading of the probe (the walk's stop test, the default-mode scope decision, and every caller outside the
# truncation guard's registry-required branch) treats it as PRESENT exactly as before round 5, the deny-safe
# direction there. Only orch_truncation_guard's registry-required mode tells it apart from a confirmed
# registry, and DENIES it: a discovery fault never satisfies that mode.
_ORCH_REG_CANNOT_EVALUATE = "cannot-evaluate"
# The union leg's own fault value (round 6): git names a toplevel for the cwd but this process cannot open
# it as a directory, so its registry entry is never reached. Truthy like _ORCH_REG_CANNOT_EVALUATE (IN
# SCOPE by default, deny-safe) and denied in registry-required mode, with a reason naming the toplevel
# rather than a .aiqt entry.
_ORCH_REG_TOPLEVEL_UNOPENABLE = "toplevel-unopenable"


def _orch_dirfd_has_registry(dirfd):
    """Whether the directory open at dirfd carries an orchestration registry entry, judged with NO-FOLLOW,
    DESCRIPTOR-ANCHORED lookups (openat semantics, so a path component swapped mid-walk cannot redirect the
    probe). THREE-VALUED (round 5). Returns False ONLY on a clean not-present: the `.aiqt` entry, or both
    registry names inside a real `.aiqt` directory, raise FileNotFoundError. Returns True (a CONFIRMED
    registry) when the first registry name present inside a real `.aiqt` directory (the local name first,
    the whole-file precedence _orch_registry applies) is a regular file under a no-follow stat (presence,
    not validity, decides scope: a present-but-unreadable or malformed regular registry has always kept the
    guard ACTIVE, never inert). EVERY other outcome returns _ORCH_REG_CANNOT_EVALUATE: a `.aiqt` entry these
    lookups cannot cleanly rule out (a symlink the O_NOFOLLOW open refuses, a regular file, or any other
    fault), or a first present registry name that is not a regular file (a directory, a symlink, a FIFO, a
    socket, a device) or whose no-follow stat faults. For a real `.aiqt` directory the deciding permission
    is SEARCH (execute) on it, not read: the O_PATH open needs none, and each registry name is examined by
    a no-follow stat relative to it, which needs search permission only. So, where _ORCH_O_WALK is O_PATH, a
    `.aiqt` of mode 0o100 evaluates normally for its owner (no read bit needed; a process that is not the
    owner and that the mode bits bind has no search bit there either, so for it the stat faults and the
    value is _ORCH_REG_CANNOT_EVALUATE, while a process the mode bits do not bind, such as root or one
    holding CAP_DAC_READ_SEARCH or CAP_DAC_OVERRIDE, evaluates it normally), and one of mode 0o600 or 0o000
    (no search bit, so the stat faults with EACCES) is _ORCH_REG_CANNOT_EVALUATE for a process those modes
    bind. Where O_PATH is unavailable the O_RDONLY fallback open of `.aiqt` also needs read permission, so
    there a mode 0o100 `.aiqt` is _ORCH_REG_CANNOT_EVALUATE too for a process the mode bits bind, its owner
    included (an over-deny, never an allow). That value is TRUTHY,
    so every boolean caller reads it as PRESENT in the deny-safe direction it always had (it must never
    read as absent - that would silently disarm an orchestrated tree), while the truncation guard's
    registry-required mode denies it rather than counting a discovery fault as a registry."""
    try:
        aiqt_fd = os.open(".aiqt", _ORCH_O_WALK | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dirfd)
    except FileNotFoundError:
        return False
    except OSError:
        return _ORCH_REG_CANNOT_EVALUATE  # a .aiqt entry this walk cannot examine: not absent, not confirmed
    try:
        for rel in _ORCH_REGISTRY_FILES:
            name = rel.rsplit("/", 1)[-1]
            try:
                st = os.stat(name, dir_fd=aiqt_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue  # this registry name is cleanly not present: try the next one
            except OSError:
                return _ORCH_REG_CANNOT_EVALUATE  # a name these lookups cannot stat: not absent, not confirmed
            # The first present name decides (whole-file precedence): a regular file is a confirmed
            # registry; any other file type is present but unconfirmable.
            return True if stat.S_ISREG(st.st_mode) else _ORCH_REG_CANNOT_EVALUATE
        return False
    finally:
        os.close(aiqt_fd)


def _orch_probe_scope(probe):
    """Map a registry-probe result to a walk scope: 'cannot-evaluate' for _ORCH_REG_CANNOT_EVALUATE, else
    'found' (called only on a truthy probe)."""
    return "cannot-evaluate" if probe == _ORCH_REG_CANNOT_EVALUATE else "found"


def _orch_registry_walk(cwd):
    """Locate the truncation guard's registry scope for a non-empty string cwd WITHOUT consulting git: walk
    cwd's PHYSICAL ancestor chain (an O_PATH|O_DIRECTORY descriptor stepped with openat(fd, ".."), so no
    component is ever re-resolved by name, a symlinked cwd path cannot alias the chain, and each step needs
    only the SEARCH permission path resolution itself needs; ".." is never a symlink) looking for a
    directory that carries an orchestration registry (_orch_dirfd_has_registry). The walk ends only where
    parent and child share one dev/ino AND that identity is the filesystem root's own: a non-root dev/ino
    repeat (a directory bind-mounted onto its own child makes the mount root and its ".." parent one
    identity) is stepped THROUGH rather than misread as the root, so a registry above such a mount point
    is still reached (verified by simulation; these test hosts cannot create mounts, and the path-anchored
    recheck below independently re-probes the textual chain, so an fd-walk miss at a mount edge surfaces
    as a found or a deny, never an allow). The walk stops at the FIRST chain directory whose probe is not a
    clean not-present and returns ('found', None) when that probe confirms a registry, or
    ('cannot-evaluate', None) when it returns _ORCH_REG_CANNOT_EVALUATE (an entry the no-follow lookups can
    neither rule out nor confirm; the default mode reads it exactly as 'found', the deny-safe direction,
    and registry-required mode denies it without consulting the chain above it or the git toplevel);
    ('none', None) only when the walk
    reaches the root with every lookup a clean not-present AND the post-walk recheck agrees
    (_orch_walk_recheck, the round-4 concurrent-move detection: descriptor anchoring preserves each opened
    directory's identity, not its parent relationship, so a mid-walk rename of an ancestor can redirect
    this walk past a continuously present registry; the recheck re-resolves and re-probes the chain BY
    PATH, scopes the session IN when it finds a registry, and FAILS the walk on a chain mismatch, never
    allowing); ('fail', (detail, fix)) when the walk cannot be carried out - a NUL in the path, a path
    that cannot be stat'ed or is not a directory, a directory this process cannot read and enter, an
    ancestor directory the walk cannot open or examine, a chain past _ORCH_WALK_BOUND, or the recheck
    mismatch above - where detail completes "this Bash call's cwd ..." and fix names the action that
    repairs it. AGREEMENT with the git path (_orch_root/_recovery_toplevel, which the sibling
    orchestration guards still use) is NOT assumed: core.worktree can point a git-resolvable toplevel OFF
    this chain, which is why the truncation guard UNIONS this walk with _orch_git_toplevel_has_registry;
    the walk additionally reaches a registry above a nested repository or a filesystem boundary (git
    discovery stops at a mount point; this walk does not) and decides scope even where git cannot run or
    answer, which the rev-parse scoping turned into a blanket deny (the round-3 lockout, withdrawn). The
    walk-and-recheck is not atomic, neither with itself nor with the Bash call it gates, and two windows
    stay out of view (disclosed in the residue, see _orch_walk_recheck): a concurrent rename of an
    ancestor directory timed against BOTH the walk and the recheck can hide a registry, and any change
    after the recheck returns is unseen. Both lie outside this guard's threat model, which is ACCIDENTAL
    truncation in an orchestrated tree, not a party able to rename this host's ancestor directories
    concurrently with the hook."""
    if "\x00" in cwd:
        return ("fail", ("contains a NUL character",
                         "Re-issue the call with a cwd carrying no control characters."))
    try:
        st = os.stat(cwd)
    except (OSError, ValueError) as exc:
        return ("fail", ("is not an existing path ({})".format(type(exc).__name__),
                         "Re-issue the call from an existing directory."))
    if not stat.S_ISDIR(st.st_mode):
        return ("fail", ("is not a directory",
                         "Re-issue the call with a directory, not a file, as the cwd."))
    if not os.access(cwd, os.R_OK | os.X_OK):
        return ("fail", ("is a directory this process cannot read and enter",
                         "Grant this process read and search permission on it, or re-issue the call from "
                         "a readable directory."))
    try:
        root_st = os.stat(os.sep)
        root_id = (root_st.st_dev, root_st.st_ino)
    except OSError:
        # With the root identity unknowable, a parent/child dev/ino repeat is never read as the root:
        # the walk runs to its depth bound and FAILS (a deny), never misreading a mount edge as the top.
        root_id = None
    try:
        fd = os.open(cwd, _ORCH_O_WALK | os.O_DIRECTORY)
    except OSError as exc:
        return ("fail", ("could not be opened for the registry walk ({})".format(type(exc).__name__),
                         "Re-issue the call from a directory this process can open."))
    try:
        cur = os.fstat(fd)
        chain = [(cur.st_dev, cur.st_ino)]
        for _ in range(_ORCH_WALK_BOUND):
            probe = _orch_dirfd_has_registry(fd)
            if probe:
                return (_orch_probe_scope(probe), None)
            try:
                parent = os.open("..", _ORCH_O_WALK | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                return ("fail", ("has an ancestor directory this walk cannot open ({})"
                                 .format(type(exc).__name__),
                                 "Grant this process search permission on every ancestor directory of "
                                 "the cwd, or re-issue the call from a directory whose ancestors it can "
                                 "search."))
            try:
                pst = os.fstat(parent)
            except OSError as exc:
                os.close(parent)
                return ("fail", ("has an ancestor directory this walk cannot examine ({})"
                                 .format(type(exc).__name__),
                                 "Re-issue the call from a directory whose ancestors this process can "
                                 "read."))
            if (pst.st_dev == cur.st_dev and pst.st_ino == cur.st_ino
                    and (pst.st_dev, pst.st_ino) == root_id):
                os.close(parent)
                # The filesystem root with every lookup a clean not-present: confirm with the path-anchored
                # recheck before reporting no registry on the chain ('none'; the git-toplevel union and the
                # registry-required mode then decide the outcome). A dev/ino repeat that is NOT the root
                # (a directory bind-mounted onto its own child) falls through and is stepped through below.
                return _orch_walk_recheck(cwd, chain)
            os.close(fd)
            fd, cur = parent, pst
            chain.append((pst.st_dev, pst.st_ino))
        return ("fail", ("sits deeper than this walk's {}-directory ancestor bound"
                         .format(_ORCH_WALK_BOUND),
                         "Re-issue the call from a directory at an ordinary filesystem depth."))
    finally:
        os.close(fd)


def _orch_walk_recheck(cwd, chain):
    """CONFIRM a registry walk that found nothing (round-4 concurrent-move detection): re-resolve cwd's
    physical ancestor chain BY PATH (os.path.realpath, then each textual parent), re-probe every chain
    directory with the same registry probe the walk uses (_orch_dirfd_has_registry, so the deny-safe
    crafted-entry reads and the self-test ceiling apply identically), and compare the re-resolved
    (st_dev, st_ino) sequence against `chain`, the dev/ino sequence the descriptor walk actually visited.
    A registry found on this second, path-anchored pass scopes the session IN (('found', None), or
    ('cannot-evaluate', None) when the probe returns _ORCH_REG_CANNOT_EVALUATE there): that is
    the mid-walk-rename case, where the descriptor chain was redirected past a continuously present
    registry, and equally a registry that appeared while the walk ran. A sequence mismatch means an
    ancestor moved while the walk read the chain, so the clean not-present result cannot be trusted:
    ('fail', (detail, fix)), a deny, never an allow; a recheck step that cannot be carried out fails the
    same way (deny-safe). Only when every probe stays a clean not-present AND the two independently
    resolved chains agree does ('none', None) stand. RACE LIMIT (disclosed in the residue): the recheck is
    NOT a point-in-time read but a sequence of lookups, as the walk is, so agreement means only that the
    two sequences of observations matched. (1) A concurrent rename of an ancestor directory timed against
    the walk AND the recheck can hide a registry: a registry relocated within the chain so it is never
    where either pass probes, or a sibling directory swapped in under a textual chain path during the
    recheck so it reports the same dev/ino the redirected walk recorded, leaves every probe a clean
    not-present with the chains agreeing, and the call is allowed (denied under registry-required
    mode). (2) Any change AFTER the recheck
    returns, including a registry that appears only then, is out of view (the inherent pre-execution
    TOCTOU bound). Both are outside this guard's threat model: it stops ACCIDENTAL truncation in an
    orchestrated tree, and a party able to rename this host's ancestor directories concurrently with the
    hook is not that case. A mid-walk rename that is not also timed against the recheck is caught (a
    found or a deny, pinned by the raced-ancestor rows); no further race machinery is added."""
    try:
        path = os.path.realpath(cwd)
    except (OSError, ValueError):
        return ("fail", ("could not be re-resolved after the registry walk",
                         "Re-issue the call from a stable directory."))
    seen = []
    for _ in range(_ORCH_WALK_BOUND + 1):
        try:
            fd = os.open(path, _ORCH_O_WALK | os.O_DIRECTORY)
        except OSError as exc:
            return ("fail", ("has an ancestor chain this walk's recheck cannot re-resolve ({})"
                             .format(type(exc).__name__),
                             "Re-issue the call once the cwd's directory tree is stable."))
        try:
            try:
                rst = os.fstat(fd)
            except OSError as exc:
                return ("fail", ("has an ancestor chain this walk's recheck cannot examine ({})"
                                 .format(type(exc).__name__),
                                 "Re-issue the call once the cwd's directory tree is stable."))
            seen.append((rst.st_dev, rst.st_ino))
            probe = _orch_dirfd_has_registry(fd)
            if probe:
                return (_orch_probe_scope(probe), None)
        finally:
            os.close(fd)
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    else:
        return ("fail", ("sits deeper than this walk's {}-directory ancestor bound"
                         .format(_ORCH_WALK_BOUND),
                         "Re-issue the call from a directory at an ordinary filesystem depth."))
    if seen != chain:
        return ("fail", ("changed its ancestor chain while the registry walk read it (a concurrent "
                         "rename or mount moved an ancestor directory mid-walk, so the walk's clean "
                         "not-present result cannot be trusted)",
                         "Re-issue the call once the cwd's directory tree is stable."))
    return ("none", None)


def _orch_git_toplevel_has_registry(cwd):
    """The UNION leg of the truncation guard's registry scope (round 4): where git DOES resolve a toplevel
    for the session cwd (the scrubbed _recovery_toplevel primitive, exactly the rooting the old rev-parse
    scoping and the sibling orchestration guards use), that toplevel's registry is consulted IN ADDITION
    to the ancestor walk, because core.worktree (set in a repository config or a gitfile's gitdir target)
    can point the work tree OFF the cwd's physical ancestor chain: from inside such a repository's
    metadata directory the old scoping read the external work tree's registry and denied, and the walk
    alone never visits it (the round-4 finding). FOUR-VALUED (round 6; the registry probe it reuses is
    three-valued): returns True (IN SCOPE) when git resolves a toplevel and the same no-follow registry
    probe the walk uses (_orch_dirfd_has_registry, so the self-test ceiling masks this leg identically) confirms a registry
    there; returns _ORCH_REG_CANNOT_EVALUATE (truthy, so IN SCOPE by default, deny-safe; denied in
    registry-required mode) when that probe neither rules a registry out nor confirms one, and
    _ORCH_REG_TOPLEVEL_UNOPENABLE (truthy and denied in that mode the same way, with its own reason) when
    opening the resolved toplevel as a directory fails with any error other than FileNotFoundError (it is
    present but not a directory, or this process may not reach it: a toplevel git can name but this probe
    cannot examine is not cleanly registry-free, matching the old scoping's present-but-unreadable read).
    Returns False when git cannot resolve a toplevel at all (BY DEFAULT git success can only ADD a deny
    and a git failure alone never denies; in registry-required mode a False here with nothing on the walk
    is the ABSENT registry the caller denies, so a git failure where the registry is reachable only
    through the git toplevel is denied, and git success can then remove that deny), when its registry
    probe is a clean not-present, and when the resolved toplevel DOES NOT EXIST (FileNotFoundError, e.g.
    core.worktree naming a removed directory). That last case is deliberate (round 8): a directory that
    does not exist holds no registry, exactly as the walk reads a missing `.aiqt` entry as a clean
    not-present, so there is no fault to report. It still fails closed where that matters: with nothing
    on the walk the scope is then ('none', None), which registry-required mode DENIES as an absent
    registry, and the default mode is inert there exactly as for any other absent registry."""
    top = _recovery_toplevel(cwd)
    if top is None:
        return False
    try:
        fd = os.open(top, _ORCH_O_WALK | os.O_DIRECTORY)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return _ORCH_REG_TOPLEVEL_UNOPENABLE
    try:
        probe = _orch_dirfd_has_registry(fd)
        return _ORCH_REG_CANNOT_EVALUATE if probe == _ORCH_REG_CANNOT_EVALUATE else bool(probe)
    finally:
        os.close(fd)


def _orch_truncation_scope(cwd):
    """The truncation guard's registry scope for a non-empty string cwd (round 6: shared with
    tools/orch_doctor.py so the doctor reports exactly what the guard decides): the ancestor walk
    (_orch_registry_walk) and, only when the walk finds nothing on the chain, the git-toplevel union leg
    (_orch_git_toplevel_has_registry). Returns the walk's ('fail', (detail, fix)), ('found', None) or
    ('cannot-evaluate', None) as is; on a walk 'none' returns ('none', None) when the union leg is False,
    ('cannot-evaluate', None) when it returns _ORCH_REG_CANNOT_EVALUATE, ('toplevel-unopenable', None)
    when it returns _ORCH_REG_TOPLEVEL_UNOPENABLE, and ('found', None) when it confirms a registry. The
    NEAREST non-absent entry decides: a walk 'found' or 'cannot-evaluate' never consults the chain above it
    or the git toplevel."""
    scope, found = _orch_registry_walk(cwd)
    if scope != "none":
        return scope, found
    top_probe = _orch_git_toplevel_has_registry(cwd)
    if not top_probe:
        return ("none", None)
    if top_probe == _ORCH_REG_CANNOT_EVALUATE:
        return ("cannot-evaluate", None)
    if top_probe == _ORCH_REG_TOPLEVEL_UNOPENABLE:
        return ("toplevel-unopenable", None)
    return ("found", None)


def _orch_registry(root, nofollow=False, files=_ORCH_REGISTRY_FILES):
    """Load the orchestration registry: ('absent', None) only when a registry file is genuinely NOT PRESENT
    (a clean lstat FileNotFoundError), ('ok', dict) on a schema-valid
    registry, ('bad', detail) otherwise. A present-but-unreadable registry is a cannot-evaluate returned as
    bad, never absent: an lstat FAULT (a permission or I/O error), a read/parse error, a file that is not a
    regular file, or a non-version-1 object all fail closed rather than silently disarming a caller that
    locates confinement through it. The file is opened non-blocking and its type is checked on the open
    descriptor, so a FIFO or device registry is bad at once rather than blocking the hook; with nofollow a
    symlinked registry is bad too (the review dispatch pin reads it that way). The
    machine-local .aiqt/orchestration.local.json takes WHOLE-FILE precedence over the committed
    .aiqt/orchestration.json; there is no merge, so precedence is never ambiguous. files narrows the read
    to the named registry files (the review dispatch pin reads each file on its own).

    WHAT 'absent' MEANS TO EACH CALLER (it is NOT inert everywhere): orch_stop_guard and
    orch_teammate_idle (via _orch_stop_family), orch_yield_tool, orch_ask_guard, orch_untracked_wait_loop,
    orch_dispatch_ledger, orch_prompt_stamp, orch_resume_audit and orch_resume_barrier ALLOW (inert);
    _orch_state_dir_for_root resolves the XDG default state directory; _load_write_scope ALSO falls back
    to that XDG default and reads the write-scope declaration there, so a declaration present at the XDG
    default ARMS slice confinement on an ABSENT registry (by design: the harness writes the declaration);
    _companion_stores yields no stores, so cross-repo writes deny exactly as with no stores declared.
    orch_truncation_guard does not scope through this loader (its own ancestor walk unioned with the git
    toplevel decides its scope, and its opt-in registry-required mode denies an absent registry).
    review_dispatch_pin (_rdp_scope) reads each registry file on its own and skips an absent one, so its
    search for a binding goes on."""
    for rel in files:
        path = os.path.join(root, *rel.split("/"))
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue                     # genuinely not present: try the next registry file
        except OSError as exc:
            # An lstat FAULT (e.g. a permission error) is a cannot-evaluate, NOT absence: os.path.lexists
            # would have swallowed it to False and read a present-but-unreadable registry as absent, falling
            # back to XDG and disarming confinement. Surface it as bad so it denies instead.
            return ("bad", "{}: cannot stat registry path ({}); a cannot-evaluate denies rather than "
                           "disarming confinement".format(rel, exc))
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(path, flags | (os.O_NOFOLLOW if nofollow else 0))
        except (OSError, ValueError) as exc:
            return ("bad", "{}: {}".format(rel, exc))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return ("bad", "{}: not a regular file".format(rel))
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                chunks.append(chunk)
        except OSError as exc:
            return ("bad", "{}: {}".format(rel, exc))
        finally:
            os.close(fd)
        try:
            data = json.loads(b"".join(chunks).decode("utf-8"))
        except ValueError as exc:
            return ("bad", "{}: {}".format(rel, exc))
        if not isinstance(data, dict) or type(data.get("version")) is not int \
                or data.get("version") != 1:
            return ("bad", "{}: not a version-1 registry object".format(rel))
        return ("ok", data)
    return ("absent", None)


def _orch_path(root, value):
    """Resolve a registry-declared path: absolute kept, relative joined onto the repo root; None for
    a non-string or empty value."""
    if not isinstance(value, str) or not value:
        return None
    return value if os.path.isabs(value) else os.path.join(root, value)


def _state_dir_from_registry(root, reg_data):
    """Resolve the machine-written state directory for root from an ALREADY-READ registry dict (or None for
    an absent registry, or an ok registry that declares no usable state_dir): the registry's state_dir when
    it declares a usable one, else ${XDG_STATE_HOME:-$HOME/.local/state}/aiqt-guardrails/orch/<repo-key>/.
    PURE: it performs NO registry read of its own, so a caller that has already validated the registry passes
    that result here and never triggers a second, independently-faulting read (the TOCTOU fail-open where a
    second EACCES silently downgrades a confined session to the XDG default)."""
    declared = _orch_path(root, (reg_data or {}).get("state_dir"))
    if declared:
        return declared
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"),
                                                            ".local", "state")
    key = __import__("hashlib").sha256(root.encode("utf-8", "replace")).hexdigest()[:16]
    return os.path.join(base, "aiqt-guardrails", "orch", key)


def _orch_state_dir_for_root(root):
    """The machine-written state directory for a repo root: the registry's state_dir when declared, else
    ${XDG_STATE_HOME:-$HOME/.local/state}/aiqt-guardrails/orch/<repo-key>/. Reads the registry ONCE and
    delegates the resolution to the pure _state_dir_from_registry helper (a single read per call)."""
    status, reg = _orch_registry(root)
    return _state_dir_from_registry(root, reg if status == "ok" else None)


def _orch_append_jsonl(path, obj):
    """Best-effort JSONL append (parents created). Returns True on success; the CALLER decides what a
    failed write means (a recorder surfaces it; a guard never changes its decision over it)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, sort_keys=True) + "\n")
        return True
    except (OSError, ValueError):
        return False


def _orch_read_jsonl(path):
    """Read a JSONL file: (rows, bad_line_count), or (None, 0) when the file is absent or unreadable,
    so a caller can distinguish nothing-recorded from cannot-read (chkfcl)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except FileNotFoundError:
        return ([], 0)
    except (OSError, ValueError):
        return (None, 0)
    rows, bad = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            bad += 1  # valid JSON but not an object (null/list/number): malformed, never coerced to {}
    return (rows, bad)


def _orch_write_json(path, obj):
    """Best-effort JSON overwrite (parents created). Returns True on success; the CALLER decides what
    a failed write means (a recorder surfaces it; a guard never changes its decision over it)."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, sort_keys=True)
        return True
    except (OSError, ValueError):
        return False


def _orch_write_json_atomic(path, obj):
    """Crash-safe JSON overwrite, the one writer of turn-state.json, backlog-checkpoint.json,
    checkpoint-init.marker, attestations-validated.json and forced-exit-surfaced.json. Returns None on success,
    else a short failure detail the caller names in its output (the caller decides what a failure means).
    It serializes obj first (a value json cannot encode fails before any file is created), creates the
    parent directory and opens it ONCE (os.open _ORCH_O_WALK|O_DIRECTORY|O_CLOEXEC: where O_PATH exists the
    open needs no permission on that directory itself, only search permission on its ancestors, while the
    later lstat, create, replace and cleanup unlink need search permission on it, the last three write
    permission too, so a write-and-search-only (0300) state directory saves; where O_PATH
    is absent the O_RDONLY fallback also needs READ permission on that directory, so there such a directory
    fails every save, named with its error); every later step is bound to that directory descriptor through
    dir_fd, so a parent path swapped for a symlink or another directory after the open cannot redirect the
    save: the prior target's lstat (follow_symlinks=False), the create of the temporary file, the replace and
    the cleanup unlink all act in the directory opened, and the descriptor is closed exactly once on every
    path. It creates a temporary file of its own beside the target with
    os.open(O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW) under a random name, so a symlink or any other
    file already at a temporary-like name is never opened, written or removed (an existing name fails the
    create, and the save fails). It sets that file's permission bits to the existing target's (a regular
    file at the target), else to 0600, then writes, fsyncs and closes it through that descriptor only and
    os.replace()s it onto the target, so the target is swapped whole: a reader sees the previous file or the
    new one, never a part of either. The new file is owned by the writing uid; a symlink at the target is
    replaced, not followed. On any failure after the create it unlinks only its own temporary file; an
    unlink that fails leaves that file beside the target, and the returned detail names it by the parent
    path as given (which, after a swap, may no longer lead to it). A process killed between the create and
    the replace (a hook timeout, for example) leaves its temporary file, and nothing removes it. Not bound
    here: the parent path itself is resolved once, at the directory open (a symlink in it at that moment is
    followed), and a directory moved after the open still receives the save under its new name. Concurrent
    saves never share a temporary file, so one save cannot write into or remove another's; the last replace
    wins, so where callers read, modify and save the same file at the same time one update can be lost
    (read-modify-write is not serialized). The writing uid can itself replace any of these files, so this is
    crash and collision safety, not a boundary against that uid."""
    try:
        blob = json.dumps(obj, sort_keys=True).encode("utf-8")
        directory, name = os.path.split(path)
        os.makedirs(directory, exist_ok=True)
        dirfd = os.open(directory, _ORCH_O_WALK | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    except (OSError, TypeError, ValueError) as exc:
        return type(exc).__name__
    try:
        return _orch_write_json_at(dirfd, directory, name, blob)
    finally:
        try:
            os.close(dirfd)  # once: after a failed close the descriptor number may already be reused
        except OSError:
            pass  # a lookup-only directory descriptor: the save's outcome is settled already


def _orch_write_json_at(dirfd, directory, name, blob):
    """The save of _orch_write_json_atomic (see there), every step bound to the open directory dirfd:
    None on success, else the failure detail. directory is the parent path as given, used only to name a
    temporary file left behind."""
    try:
        try:
            prior = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
            mode = stat.S_IMODE(prior.st_mode) & 0o777 if stat.S_ISREG(prior.st_mode) else 0o600
        except FileNotFoundError:
            mode = 0o600
        tmp = "{}.{}.tmp".format(name, os.urandom(8).hex())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                     0o600, dir_fd=dirfd)
    except (OSError, TypeError, ValueError, NotImplementedError) as exc:
        return type(exc).__name__
    failure = None
    try:
        os.fchmod(fd, mode)
        view = memoryview(blob)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    except (OSError, ValueError) as exc:
        failure = type(exc).__name__
    finally:
        try:
            os.close(fd)  # once: after a failed close the descriptor number may already be reused
        except OSError as exc:
            failure = failure or type(exc).__name__
    if failure is None:
        try:
            os.replace(tmp, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
            return None
        except (OSError, ValueError, NotImplementedError) as exc:
            failure = type(exc).__name__
    try:
        os.unlink(tmp, dir_fd=dirfd)
    except FileNotFoundError:
        pass
    except (OSError, NotImplementedError) as exc:
        failure += "; removing its temporary file {} failed with {}, so remove it by hand".format(
            os.path.join(directory, tmp), type(exc).__name__)
    return failure


def _orch_guard_event(root, kind, decision, detail):
    """Best-effort append of one guard-events row (the over-fire metric and the mistakes-register feed).
    Returns whether the append succeeded (False on an I/O failure); the caller decides what a failure means
    and a guard never changes its decision over it."""
    sd = _orch_state_dir_for_root(root)
    return _orch_append_jsonl(os.path.join(sd, "guard-events.jsonl"),
                              {"ts": _orch_now().isoformat(), "kind": kind,
                               "decision": decision, "detail": detail})


def _orch_event_warn(root, kind, decision, detail):
    """_orch_guard_event for a path that must not lose its row silently (a deny, an allow with findings,
    or a recorded fact): returns '' when the append succeeded, else the short recording-failure warning
    the caller appends to its output (the block reason of a denied Stop or TeammateIdle, else the
    banner, and the deny reason too on a PreToolUse deny). The decision is never changed here."""
    if _orch_guard_event(root, kind, decision, detail):
        return ""
    return ("Additionally, the guard-events row for this {} ({}) could not be written; record it "
            "manually (nocncl).".format(decision, kind))


def _orch_escape_event_warn(root, kind, reason):
    """The guard-events row of an operator-escape ALLOW, the only record that the override was used:
    returns '' when the append succeeded, else the warning the caller appends to its output (the ALLOW
    stands)."""
    if _orch_guard_event(root, kind, "allow", reason):
        return ""
    return ("Additionally, the guard-events row recording this operator-escape release ({}) could not be "
            "written, so the override's use is unrecorded; record it manually (nocncl).".format(kind))


def _orch_warn_tail(*warns):
    """The recording-failure warnings of one hook call, each preceded by a space ('' when none)."""
    return "".join(" " + w for w in warns if w)


def _orch_turn_state(root):
    """The turn-state dict, or None on an unreadable/malformed file (the loop guard treats None as
    bound-reached, the fail-open direction: an unreadable counter can never license unbounded denies)."""
    path = os.path.join(_orch_state_dir_for_root(root), "turn-state.json")
    try:
        if not os.path.lexists(path):
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _orch_save_turn_state(root, state):
    """Save turn-state.json through _orch_write_json_atomic: None on success, else the failure detail the
    caller names in its output (the caller decides what a failure means). A failed save, mid-write
    included, leaves the previous turn-state.json (or its absence) in place unchanged, and a reader sees
    the previous file or the new one, never a part of either. The save needs a writable and searchable
    state directory (its temporary file is created there), so a writable turn-state.json in a directory
    the hook cannot write is not saved; where O_PATH is absent the directory must also be readable (see
    _orch_write_json_atomic). Two saves at once are both published whole, the later replacing the earlier,
    so one update can be lost (the read-modify-write is not serialized)."""
    return _orch_write_json_atomic(os.path.join(_orch_state_dir_for_root(root), "turn-state.json"), state)


def _orch_mode_classify(value):
    """Classify a raw operating-mode value string by the mode grammar (used for both the declaration value and
    the JSON `mode` string). Trims and lowercases, then matches word-anchored at the START, never as a
    substring: returns _ORCH_MODE_ARMED (the `unattended` token) when the value BEGINS with `unattended`,
    `"attended"` when it BEGINS with `attended`, and None when it does neither (empty or unrecognized, which
    the caller maps to _ORCH_MODE_ARMED). So `unattended; continuous` and the real compound `attended (ipad);
    continuous mode` classify, while `disattended`, `not-attended`, and `bananas` begin with neither mode word
    and return None."""
    v = value.strip().lower()
    if _ORCH_MODE_UNATTENDED_RE.match(v):
        return _ORCH_MODE_ARMED
    if _ORCH_MODE_ATTENDED_RE.match(v):
        return "attended"
    return None


def _orch_reject_duplicate_keys(pairs):
    """A json object_pairs_hook that rejects an object carrying a duplicate key by raising ValueError, so a
    marker such as {"mode": "unattended", "mode": "attended"} cannot silently collapse to json's last-wins
    value and disarm the guard; the caller treats the raised ValueError as a malformed marker (fail closed)."""
    seen = {}
    for key, val in pairs:
        if key in seen:
            raise ValueError("duplicate key: {}".format(key))
        seen[key] = val
    return seen


def _orch_mode_read(path):
    """Read the mode file at path without waiting for a FIFO writer and without reading past the bound:
    ('absent', None) when the open reports not found, ('ok', text) for a regular file of at most
    _ORCH_MODE_MAX_BYTES bytes that decodes as strict UTF-8, else ('bad', reason) for every other outcome.
    The path must encode as strict UTF-8, before any open: a path that is not (a lone surrogate, which is
    also how a surrogate-escaped non-UTF-8 name arrives) is refused as bad, fail-closed, even where the OS
    could open it, and so is a path with a NUL character. The open is os.open(O_RDONLY | O_NONBLOCK |
    O_NOCTTY | O_CLOEXEC), so a FIFO with no writer opens at once instead of waiting. A socket is refused
    at that open (it reports ENXIO on Linux, so the reason is the open's OSError), and so is any node the
    open itself fails on (a regular file without read permission, /dev/tty with no controlling terminal);
    after a successful open the descriptor is fstat'ed and anything not a regular file (a FIFO, a device such
    as /dev/zero, a directory) is bad without a read. The read asks for at most
    _ORCH_MODE_MAX_BYTES + 1 bytes in all and a longer file is bad. Any exception on the way (an OSError,
    a decode error, any other) is bad, named by its type, and the descriptor is closed exactly once. Not
    bounded here: the path lookup can stall on a hung mount, a regular file on a stalled filesystem can
    stall the read, and what opening a device node does is up to its driver; the hook timeout bounds each
    such stall."""
    try:
        if "\x00" in path:
            return ("bad", "the mode path contains a NUL character")
        path.encode("utf-8")
    except UnicodeEncodeError:
        return ("bad", "the mode path is not strict UTF-8 (a lone surrogate or a surrogate-escaped "
                       "non-UTF-8 byte), so it is refused, fail-closed, even where the OS could open it")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0)
                     | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return ("absent", None)
    except Exception as exc:
        return ("bad", "the mode file cannot be opened ({})".format(type(exc).__name__))
    chunks, total, result = [], 0, None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            result = ("bad", "the mode file is not a regular file")
        else:
            while total <= _ORCH_MODE_MAX_BYTES:
                chunk = os.read(fd, _ORCH_MODE_MAX_BYTES + 1 - total)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            if total > _ORCH_MODE_MAX_BYTES:
                result = ("bad", "the mode file is larger than the {}-byte bound".format(_ORCH_MODE_MAX_BYTES))
    except Exception as exc:
        result = ("bad", "the mode file cannot be read ({})".format(type(exc).__name__))
    finally:
        try:
            os.close(fd)  # once: after a failed close the descriptor number may already be reused
        except OSError as exc:
            result = result or ("bad", "the mode file cannot be closed ({})".format(type(exc).__name__))
    if result is not None:
        return result
    try:
        return ("ok", b"".join(chunks).decode("utf-8"))  # strict decode
    except UnicodeDecodeError:
        return ("bad", "the mode file is not valid UTF-8")


def _orch_mode(reg, root):
    """The operating-mode token of _orch_mode_detail (see there), without the read-failure reason."""
    return _orch_mode_detail(reg, root)[0]


def _orch_mode_detail(reg, root):
    """(mode, reason): the operating-mode token, and the reason the mode file could not be read where it
    fails closed for that (else None). The token comes from the declared mode record, parsed by ONE sound
    reader (never an incremental regex-plus-substring scan). The mode file is a SHARED text file: either a
    state-record file carrying an `Operating-mode:` line amid prose, or a peer JSON mode file. Contract:
      - None when NO marker is present, preserving the fail-open answer for the ask blocker (the file is
        shared, so prose must NOT arm): an undeclared mode path, a genuinely absent file (FileNotFoundError),
        an empty or whitespace-only file, or prose with no `Operating-mode:` declaration line and no JSON
        marker (including a sentence that merely mentions attended or unattended);
      - _ORCH_MODE_ARMED (the guards-armed `unattended` posture) when a marker IS present but cannot yield a
        recognized value, so the guard fails CLOSED rather than silently disarming: every outcome of
        _orch_mode_read other than not found or a read text (a path with a NUL character or one that is
        not strict UTF-8, a file that cannot be opened or read (a socket included), one that is not a
        regular file, one larger than the bound, or one whose bytes are not valid UTF-8), which also returns
        the reason;
        a present `Operating-mode:` declaration line whose value is empty or does not begin with attended or
        unattended; or, when no declaration line is present, a JSON-shaped marker (the content begins with
        `{`, `[`, or `"`) that is malformed or partial (an unterminated string, trailing garbage, or duplicate
        keys), or that parses to anything other than a dict with EXACTLY the single key `mode` whose value is a
        string beginning with attended or unattended (a scalar, an array, an object with extra keys, or an
        object whose `mode` is absent, non-string, or an unrecognized value all fail closed here);
      - "attended" when a recognized attended marker is present: a non-None value that does NOT contain
        `unattended`, so _orch_scope_live reads scope as live AND orch_ask_guard allows the ask.
    Soundness rules the reader enforces (each closing a fail-open leg an incremental scan leaked):
      1. A leading byte-order mark is stripped ONCE at the top, so every later check sees BOM-free text and a
         BOM-prefixed declaration OR JSON marker is classified rather than missed.
      2. The declaration is parsed per PHYSICAL line (split on \\r\\n, \\r, or \\n first), so the value is the
         rest of THAT line only and can never cross a newline to capture the next line; leading horizontal
         whitespace is tolerated consistently, so an indented declaration does not over-arm.
      3. The value and the JSON `mode` string are classified by the word-anchored mode grammar
         (_orch_mode_classify), a prefix match at the start, never a substring, so `disattended` and
         `not-attended` do NOT match `attended`.
      4. JSON is parsed strict-exact with a duplicate-key-rejecting hook and an exactly-{"mode": str} shape,
         so duplicate keys, extra keys, and non-object or non-string-mode values all fail closed."""
    path = _orch_path(root, (reg.get("mode") or {}).get("path") if isinstance(
        reg.get("mode"), dict) else None)
    if not path:
        return (None, None)
    status, text = _orch_mode_read(path)
    if status == "absent":
        return (None, None)               # no marker file present: unchanged no-marker default (fail open)
    if status != "ok":
        return (_ORCH_MODE_ARMED, text)   # any other read outcome fails closed to guards-armed, with a reason
    return (_orch_mode_classify_text(text), None)


def _orch_mode_classify_text(text):
    """The mode token of a mode file's decoded text, by the soundness rules of _orch_mode_detail."""
    text = text.lstrip("\ufeff")          # strip a leading byte-order mark ONCE, before every subsequent check
    # DECLARATION (line form): parse each PHYSICAL line so the value never crosses a newline. The FIRST line
    # that matches is the declaration; a present declaration never falls through to the no-marker None (an
    # empty or unrecognized value fails CLOSED to guards-armed).
    for line in re.split(r"\r\n|\r|\n", text):
        m = _ORCH_MODE_DECL_LINE_RE.match(line)
        if m:
            classified = _orch_mode_classify(m.group(1))
            return classified if classified is not None else _ORCH_MODE_ARMED
    # No declaration line: classify the whole BOM-stripped content as a strict-exact JSON marker.
    stripped = text.strip()
    if not stripped:
        return None                       # empty/whitespace-only: no marker present, unchanged (fail open)
    try:
        obj = json.loads(stripped, object_pairs_hook=_orch_reject_duplicate_keys)
    except (ValueError, RecursionError):
        # Unparseable (malformed, trailing garbage, duplicate keys, or nesting too deep to parse, which only
        # content beginning with `{` or `[` can reach): a JSON-SHAPED marker attempt (the content begins with
        # `{`, `[`, or `"`) fails CLOSED; anything else is ordinary prose with no declaration and no JSON, the
        # no-marker None (fail open, because the file is shared).
        return _ORCH_MODE_ARMED if stripped[:1] in ("{", "[", '"') else None
    if isinstance(obj, dict) and set(obj) == {"mode"} and isinstance(obj["mode"], str):
        classified = _orch_mode_classify(obj["mode"])
        return classified if classified is not None else _ORCH_MODE_ARMED
    # Any other JSON (a scalar, an array, an object with extra keys, or a dict without a string `mode`) is a
    # present-but-unrecognized marker and fails CLOSED to guards-armed.
    return _ORCH_MODE_ARMED


def _orch_scope_live(reg, root, session_id=None):
    """The D11 scope check: True when a declared orchestrator lease file is live (present, non-empty,
    within max_age_hours when declared) OR a declared mode record carries a mode line. When the lease
    declares holder_is_session_id AND the caller's session_id is known, a LIVE lease must additionally
    NAME that session_id, so a co-located NON-holder session (a bounded worker) does not inherit the
    global backlog (CX-M3). Where the lease records no comparable session identity, scope is repo-coarse
    (a co-located session may inherit the backlog): a disclosed residual, not a strict boundary."""
    lease = reg.get("lease") if isinstance(reg.get("lease"), dict) else None
    if lease:
        path = _orch_path(root, lease.get("path"))
        if path:
            try:
                st = os.stat(path)
                fresh = True
                max_age = lease.get("max_age_hours")
                if isinstance(max_age, (int, float)) and max_age > 0:
                    age = _orch_now().timestamp() - st.st_mtime
                    # a future mtime (age below -skew) is anomalous (clock skew or tamper) and never reads
                    # as fresh forever; a small future skew is tolerated (CX future-mtime finding).
                    fresh = -_ORCH_CLOCK_SKEW <= age <= max_age * 3600
                if st.st_size > 0 and fresh:
                    if lease.get("holder_is_session_id"):
                        # STRICT opt-in holder scoping: the caller must carry a session id AND the lease
                        # must be readable AND name that id as a whole token; anything else is a NON-holder
                        # (a co-located worker or an id-less session never inherits the global backlog).
                        if not (isinstance(session_id, str) and session_id):
                            return False
                        try:
                            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                                held = fh.read()
                        except OSError:
                            return False
                        return _orch_token_present(session_id, held)
                    return True
            except OSError:
                if lease.get("holder_is_session_id"):
                    return False  # holder mode: an unreadable lease -> caller cannot be confirmed holder
                # otherwise an absent/unreadable lease is not live; fall through to the mode check
    return _orch_mode(reg, root) is not None


def _orch_escape_stat(path):
    """The C.1 stat seam: bind the final-component stat to an O_NOFOLLOW-opened descriptor
    (os.open O_RDONLY|O_NOFOLLOW|O_NONBLOCK then os.fstat), or None on any OSError. FIX B: O_NONBLOCK
    keeps a FIFO sentinel with no writer from blocking the open (which would hang the guard); the
    S_ISREG check below then rejects the FIFO. Binding the stat to the opened
    inode rather than a pathname lstat means a name swap AFTER a pathname check cannot redirect the
    validation onto a different inode (a best-effort race narrowing; see _orch_escape_active for the
    disclosed residual). A one-line helper so the self-test can exercise the different-uid honour,
    hardlink, and writable legs hermetically on a single-uid host, where a real file owned by another
    uid (or with a fabricated link count) cannot be created."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW
                     | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        return os.fstat(fd)
    finally:
        os.close(fd)


def _orch_escape_active(reg, root):
    """C.1: the escape sentinel opens the clean-ALLOW channel only when it is demonstrably an
    operator-created artefact in a trusted place, NOT the assistant's own: a REGULAR file with a single
    hard link (st_nlink == 1), reached without following any symlinked path component, resolved under
    the operator-trusted anchor (the machine-written state dir), owned by a uid other than this guard's
    effective uid, and not group- or other-writable. Returns (active, spoof_detail): a present sentinel
    failing any condition reads INACTIVE with the failed condition as the detail, so the decision
    proceeds exactly as with no sentinel (no new deny anywhere) and the bindings can record the spoof.
    An absent or unstattable path is (False, None).

    FIX 2 raises the bar against the naming/link vectors: an absolute FOREIGN path (e.g. /etc/passwd or
    /proc/self/root/...), a '..' traversal, a symlinked path component, or a hard link no longer opens
    the channel. Residuals (disclosed in ORCHESTRATION.md): the trusted anchor is the machine-written
    state dir, which is itself actor-writable, so an actor with raw filesystem write access remains the
    CATEGORICAL residual (closed only by an operator-owned, assistant-non-writable state dir at the OS
    layer); component resolution is a best-effort lstat walk under the anchor rather than a fully
    race-free openat-under-anchor (a narrow TOCTOU window between the walk and the O_NOFOLLOW open); and
    uid inequality proves not-self, never operator identity."""
    anchor = os.path.realpath(_orch_state_dir_for_root(root))
    declared = (reg.get("escape") or {}).get("path") if isinstance(reg.get("escape"), dict) else None
    if declared is not None and (not isinstance(declared, str) or not declared):
        return (False, None)
    if declared:
        raw = declared if os.path.isabs(declared) else os.path.join(anchor, declared)
    else:
        raw = os.path.join(anchor, _ORCH_ESCAPE_NAME)
    if os.pardir in raw.split(os.sep):
        return (False, "escape sentinel path contains '..' (rejected)")
    norm = os.path.normpath(raw)
    if not (norm == anchor or norm.startswith(anchor.rstrip(os.sep) + os.sep)):
        return (False, "escape sentinel path is outside the operator-trusted anchor (rejected)")
    # refuse to follow a symlinked component from the anchor down to (not including) the final name
    rel = os.path.relpath(norm, anchor)
    parts = [] if rel in ("", os.curdir) else rel.split(os.sep)
    cur = anchor
    for comp in parts[:-1]:
        cur = os.path.join(cur, comp)
        try:
            if stat.S_ISLNK(os.lstat(cur).st_mode):
                return (False, "escape sentinel has a symlinked path component (rejected)")
        except OSError:
            return (False, None)  # a missing intermediate directory is simply no sentinel
    # a symlink AT the final name is a spoof: classify via lstat so it surfaces a detail (the seam's
    # O_NOFOLLOW open would otherwise report it only as an unopenable None)
    try:
        if stat.S_ISLNK(os.lstat(norm).st_mode):
            return (False, "escape sentinel is a symlink (not followed; ignored)")
    except OSError:
        pass  # absent on disk here: the seam is authoritative (and may be injected under test)
    st = _orch_escape_stat(norm)
    if st is None:
        return (False, None)
    if not stat.S_ISREG(st.st_mode):
        return (False, "escape sentinel is not a regular file (symlink or special file; ignored)")
    if st.st_nlink != 1:
        return (False, "escape sentinel has more than one hard link (ignored)")
    if st.st_uid == os.geteuid():
        return (False, "escape sentinel is owned by the assistant's own effective uid (ignored)")
    if st.st_mode & 0o022:
        return (False, "escape sentinel is group- or other-writable (ignored)")
    return (True, None)


def validate_enumeration(obj):
    """AEI v1 strict validation: (items, None) on a valid enumeration, (None, detail) otherwise. A
    duplicate id, a malformed item or field, an unknown version, a missing envelope, or a non-dict
    payload is an error, never an empty backlog (grdinp: a parse failure is a distinct cannot-evaluate,
    not a clean pass)."""
    if not isinstance(obj, dict):
        return (None, "payload is not a JSON object")
    version = obj.get("version")
    if type(version) is not int or version != 1:
        return (None, "unknown AEI version {!r} (an exact integer 1 is required)".format(version))
    gen = obj.get("generated_at_utc")
    gen_ts = _orch_parse_utc(gen) if isinstance(gen, str) else None
    if gen_ts is None:
        return (None, "missing or unparseable generated_at_utc")
    if (gen_ts - _orch_now()).total_seconds() > _ORCH_CLOCK_SKEW:
        return (None, "generated_at_utc is in the future")
    source = obj.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("locator"), str) \
            or not source.get("locator").strip():
        return (None, "missing or malformed source (a non-blank locator is required)")
    items = obj.get("items")
    if not isinstance(items, list):
        return (None, "items is not a list")
    seen, out = set(), []
    for raw in items:
        if not isinstance(raw, dict):
            return (None, "an item is not an object")
        iid = raw.get("id")
        if not isinstance(iid, str) or not iid:
            return (None, "an item has no string id")
        if iid in seen:
            return (None, "duplicate item id {!r}".format(iid))
        seen.add(iid)
        title = raw.get("title")
        if title is not None and not isinstance(title, str):
            return (None, "item {} has a non-string title".format(iid))
        state = raw.get("state")
        if state not in _ORCH_STATES:
            return (None, "item {} has invalid state {!r}".format(iid, state))
        granted = raw.get("granted")
        if not isinstance(granted, bool):
            return (None, "item {} has a non-boolean granted".format(iid))
        blocker = raw.get("blocker")
        if blocker is not None:
            if not isinstance(blocker, dict) or blocker.get("kind") not in _ORCH_BLOCKER_KINDS \
                    or not isinstance(blocker.get("ref"), str) or not blocker.get("ref").strip():
                return (None, "item {} has a malformed blocker".format(iid))
            ev = blocker.get("evidence")
            obs = blocker.get("observed_at_utc")
            if (ev is not None and not isinstance(ev, str)) \
                    or (obs is not None and not isinstance(obs, str)):
                return (None, "item {} has a malformed blocker field (evidence/observed_at_utc)"
                        .format(iid))
        out.append({"id": iid, "title": title if isinstance(title, str) and title else iid,
                    "state": state, "granted": granted, "blocker": blocker})
    return (out, None)


def _orch_enumerate(reg, root):
    """Run the registry enumerator: ('ok', items) or (status, detail) where status is NO_ENUMERATOR or
    ENUMERATOR_ERROR. The enumeration is always FRESH (never a list or a 'drained' claim from the
    agent's context), via the fixed argv array the registry declares (no shell string)."""
    spec = reg.get("enumerator")
    if not isinstance(spec, dict):
        return ("NO_ENUMERATOR", "the registry declares no enumerator")
    argv = spec.get("argv")
    if not isinstance(argv, list) or not argv or not all(
            isinstance(a, str) and a for a in argv):
        return ("ENUMERATOR_ERROR", "enumerator argv is not a list of non-empty strings")
    timeout = spec.get("timeout")
    timeout = timeout if isinstance(timeout, (int, float)) and 0 < timeout <= 600 else 60
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=root)
    except (OSError, subprocess.SubprocessError) as exc:
        return ("ENUMERATOR_ERROR", "enumerator failed to run: {}".format(exc))
    if result.returncode != 0:
        return ("ENUMERATOR_ERROR",
                "enumerator exit {}: {}".format(result.returncode, (result.stderr or "")[:200]))
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        return ("ENUMERATOR_ERROR", "enumerator output is not JSON: {}".format(exc))
    items, err = validate_enumeration(payload)
    if err:
        return ("ENUMERATOR_ERROR", err)
    return ("ok", items)


def _orch_live_ledger_ids(root, task_hours):
    """Task ids that are LIVE: the LAST ledger event for the id is a launch (relaunch after a completion
    counts as live again), the wake route is EXACTLY True (a non-bool truthy like "false" does not count),
    and the launch age is within the staleness horizon and not in the future beyond a small skew tolerance.
    Returns (live_set, readable, detail): readable is False when the ledger is unreadable OR carries any
    malformed line (a cannot-evaluate the caller must NOT treat as 'no live task')."""
    rows, bad = _orch_read_jsonl(os.path.join(_orch_state_dir_for_root(root),
                                              "dispatch-ledger.jsonl"))
    if rows is None:
        return (set(), False, "dispatch ledger unreadable")
    now = _orch_now()
    last = {}
    schema_bad = 0
    for row in rows:
        event = row.get("event")
        if event not in ("launch", "complete"):
            continue  # a row for neither event is not a ledger record: ignored, not counted malformed
        # a launch/complete row MUST carry a str task_id and a str ts; a launch MUST carry a bool wake.
        # A schema-invalid dict row (e.g. a launch missing ts) is MALFORMED, not a silently-ignored row,
        # so a tracked item cannot be demoted to actionable by a half-written record (CX-R4-1).
        if not isinstance(row.get("task_id"), str) or _orch_parse_utc(row.get("ts")) is None \
                or (event == "launch" and not isinstance(row.get("wake"), bool)):
            schema_bad += 1  # ts must PARSE, not merely be a string (a 'banana' ts is malformed, CX-R5-2)
            continue
        last[row["task_id"]] = row  # file order: the LAST valid event per tid wins (relaunch = live)
    live = set()
    for tid, row in last.items():
        if row.get("event") != "launch" or row.get("wake") is not True:
            continue
        ts = _orch_parse_utc(row.get("ts"))
        if ts is not None and -_ORCH_CLOCK_SKEW <= (now - ts).total_seconds() <= task_hours * 3600:
            live.add(tid)
    # an unparseable OR schema-invalid line means some rows could not be trusted: report readable=False so
    # the caller HOLDS the item as cannot-evaluate (never read as 'no live task'); FIX 1 then DENIES the
    # stop on it below the loop bound (ignorance refuses the wind-down), the operator escape releasing it.
    readable = bad == 0 and schema_bad == 0
    detail = "{} malformed ledger line(s) skipped".format(bad) if bad else ""
    return (live, readable, detail)


def _orch_pending_haystack(reg, root):
    """The human-decision proof surface: the DECLARED pending-decisions record ONLY. The machine-written
    pending-asks keys are NOT decision rows and are excluded (an ask key session::tool_use could otherwise
    forge a row via the id-colon match, CX-R4-2). Returns None when no surface is declared or it is
    unreadable, so an undeclared/unreadable surface HOLDS a human-decision item (cannot-evaluate) rather
    than demoting it to a definite actionable; FIX 1 DENIES the stop on that cannot-evaluate below the loop
    bound (ignorance refuses the wind-down), the operator escape releasing a genuine block."""
    rec = reg.get("record") if isinstance(reg.get("record"), dict) else {}
    declared = _orch_path(root, rec.get("pending_decisions"))
    if not declared:
        return None
    try:
        with open(declared, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _orch_attestation_refs(reg, root, staleness):
    """The C.2 attestation proof surface. Three-valued, matching the proof-source idiom: None when the
    registry declares no attestations register; "unreadable" when one is declared but the validated
    snapshot is absent, malformed, not a status-ok run, stale, its binding to the register on disk no
    longer verifies, or (FIX A1) the bound register no longer reads as a clean chain (held, never
    blocked or actionable); else the frozenset of blocker refs RE-DERIVED from the register content.
    The re-anchor (FIX 3) defends the register-SWAP and snapshot-STALENESS classes only: the reader
    re-reads the register, recomputes its append-only authority (git merge-base or <path>.anchor), and
    confirms the snapshot is bound to that same register path + anchored seq + tip digest, so a
    register swapped or a snapshot gone stale between audit and yield HOLDS. FIX A1: the snapshot's
    stored 'refs' field is non-authoritative; the reader re-derives the substantiated refs from the
    re-anchored register content itself (the shared _orch_attestation_derive), so a snapshot forged
    with fabricated refs substantiates none of them. This RAISES THE BAR over free-text evidence but
    is NOT categorically forgery-proof: an actor with filesystem write to the state-dir can still
    forge an attestation by APPENDING a valid approved row (uncommitted; A2 residual), which this
    derivation substantiates. The categorical closure is an operator-owned, assistant-non-writable
    register at the OS layer (SYSTEM-HARDENING.md entry 2). A pre-binding (v1) snapshot lacks the
    binding fields and so fails closed to HOLD (safe migration)."""
    at_path = _orch_path(root, reg.get("attestations"))
    if not at_path:
        return None
    path = os.path.join(_orch_state_dir_for_root(root), "attestations-validated.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, ValueError):
        return "unreadable"
    if not isinstance(snap, dict) or snap.get("status") != "ok" \
            or not isinstance(snap.get("refs"), list):
        return "unreadable"
    generated = _orch_parse_utc(snap.get("ts"))
    age = (_orch_now() - generated).total_seconds() if generated is not None else None
    ext_hours = _orch_validate("staleness", staleness)[1]["external_hours"]
    if age is None or not (-_ORCH_CLOCK_SKEW <= age <= ext_hours * 3600):
        return "unreadable"
    # FIX 3: re-bind the snapshot to the register on disk (re-read + re-anchor).
    astatus, aseq, adigest = _orch_register_authority(root, at_path)
    if astatus != "ok" or snap.get("register") != os.path.realpath(at_path) \
            or snap.get("seq") != aseq or snap.get("digest") != adigest:
        return "unreadable"
    # FIX A1: the snapshot's own 'refs' field is NOT authoritative. Re-derive the substantiated refs
    # from the re-anchored register content itself (the same audit derivation), so a snapshot forged
    # with fabricated refs but bound to the real tip substantiates none of them.
    rows, _detail = _orch_chained_rows(at_path, "AT-")
    if rows is None:
        return "unreadable"
    refs, _unsub, _findings = _orch_attestation_derive(reg, root,
                                                       _orch_state_dir_for_root(root), rows)
    return frozenset(refs)


def classify_backlog(items, live_ids, ledger_readable, pending_haystack, staleness,
                     attestations=None):
    """Pure classification of a VALID enumeration (step 5). Returns actionable/waiting/blocked/
    cannot_evaluate/proposed. A proof SOURCE that cannot be read (ledger unreadable-or-malformed, or
    pending_haystack is None) puts the item in cannot_evaluate: FIX 1 DENIES the stop on it (ignorance
    refuses the wind-down) and the schedule path DENIES on it too (missing evidence never licenses an idle).
    attestations is the C.2 tri-state from _orch_attestation_refs: None (no register declared) keeps
    the free-text external/foreign-lease evidence path byte-identical; "unreadable" (declared but the
    validated snapshot is absent, stale, or malformed) HOLDS such an item (cannot-evaluate), never
    blocked or actionable; a validated ref-set classifies blocked only when the blocker's ref is
    covered by a fresh validated row, the existing observed_at_utc freshness check still applying."""
    now = _orch_now()
    ext_hours = _orch_validate("staleness", staleness)[1]["external_hours"]
    out = {"actionable": [], "waiting": [], "blocked": [], "cannot_evaluate": [], "proposed": []}
    for it in items:
        if it["state"] == "closed":
            continue
        if it["state"] == "proposed" or not it["granted"]:
            out["proposed"].append(it["id"])
            continue
        blocker = it["blocker"]
        if not blocker:
            out["actionable"].append((it["id"], it["title"], "no blocker recorded"))
            continue
        kind, ref = blocker["kind"], blocker["ref"]
        if kind == "tracked-task":
            if not ledger_readable:
                # readable checked FIRST: a partially-malformed ledger cannot be trusted even for an id
                # that happens to appear live in a good row (CX-R5-1), so the item is HELD.
                out["cannot_evaluate"].append((it["id"], "cannot-evaluate",
                                               "ledger unreadable/malformed; held"))
            elif ref in live_ids:
                out["waiting"].append((it["id"], "tracked-task", ref))
            else:
                out["actionable"].append((it["id"], it["title"],
                                          "tracked-task ref {} resolves to no live ledger row"
                                          .format(ref)))
        elif kind == "human-decision":
            if pending_haystack is None:
                out["cannot_evaluate"].append((it["id"], "cannot-evaluate",
                                               "pending surface unreadable; held"))
            elif not _ORCH_DECISION_ID_RE.match(ref):
                out["actionable"].append((it["id"], it["title"],
                                          "human-decision ref {!r} is not a decision id".format(ref)))
            elif re.search(r"(?m)^[ \t]*(?:\|[ \t]*{0}[ \t]*\||{0}[ \t]*:)".format(re.escape(ref)),
                           pending_haystack):
                out["blocked"].append((it["id"], "human-decision", ref))
            else:
                out["actionable"].append((it["id"], it["title"],
                                          "ref {} is not at a pending-decision row-id position".format(ref)))
        elif kind in ("external", "foreign-lease"):
            observed = _orch_parse_utc(blocker.get("observed_at_utc"))
            age = (now - observed).total_seconds() if observed is not None else None
            fresh = age is not None and -_ORCH_CLOCK_SKEW <= age <= ext_hours * 3600
            if attestations == "unreadable":
                # C.2: a DECLARED attestation surface that cannot be read HOLDS the item, a distinct
                # cannot-evaluate: never blocked (a forgeable block) and never actionable (a false
                # deny). FIX 1 DENIES the stop and the schedule path denies bounded by the cap.
                out["cannot_evaluate"].append((it["id"], "cannot-evaluate",
                                               "attestation surface unreadable; held"))
            elif attestations is not None and ref not in attestations:
                out["actionable"].append((it["id"], it["title"],
                                          "{} blocker ref {} is covered by no fresh validated "
                                          "attestation".format(kind, ref)))
            elif (blocker.get("evidence") or "").strip() and fresh:
                out["blocked"].append((it["id"], kind, ref))
            else:
                out["actionable"].append((it["id"], it["title"],
                                          "{} blocker has no fresh (non-future) evidence".format(kind)))
        elif kind == "not-before":
            when = _orch_parse_utc(ref)
            if when is not None and now < when:
                out["blocked"].append((it["id"], "not-before", ref))
            else:
                out["actionable"].append((it["id"], it["title"],
                                          "not-before constraint unparseable or already met"))
    return out


def decide_yield(ctx):
    """The PURE decision core. ctx keys: kind, escape, loop_signal, counter, enum_status, enum_detail,
    actionable, waiting, blocked, cannot_evaluate, proposed, wake_named, schedule_denials, basis_unchanged.
    Returns (verdict, reason, disposition) with verdict ALLOW | ALLOW_WITH_FINDINGS | DENY."""
    kind = ctx["kind"]
    disposition = ([("blocked", i, c, p) for i, c, p in ctx["blocked"]]
                   + [("cannot_evaluate", i, c, p) for i, c, p in ctx.get("cannot_evaluate", [])]
                   + [("waiting", i, c, p) for i, c, p in ctx["waiting"]]
                   + [("actionable", i, t, w) for i, t, w in ctx["actionable"]]
                   + [("proposed", i, "", "") for i in ctx["proposed"]])
    if ctx["escape"]:
        return ("ALLOW", "operator escape artefact present (logged)", disposition)
    if kind != "schedule_idle" and (ctx["counter"] >= _ORCH_LOOP_BOUND or ctx["loop_signal"]):
        return ("ALLOW_WITH_FINDINGS",
                "loop bound reached after {} denial(s); yielding with the unresolved items as "
                "findings rather than re-firing".format(ctx["counter"]), disposition)
    if ctx["enum_status"] != "ok":
        if kind == "schedule_idle":
            if ctx["schedule_denials"] >= _ORCH_SCHEDULE_CAP and ctx["basis_unchanged"]:
                return ("ALLOW_WITH_FINDINGS",
                        "{} schedule denials on an unchanged basis; a guard that can be farmed "
                        "trains its own bypass, so this yield proceeds with findings"
                        .format(ctx["schedule_denials"]), disposition)
            return ("DENY",
                    "the backlog is not enumerable ({}: {}); missing evidence never licenses a new "
                    "idle or wake. Fix the enumerator or the registry, or release through the "
                    "operator-owned escape sentinel".format(ctx["enum_status"],
                                                            ctx["enum_detail"]), disposition)
        return ("DENY",
                "cntdef: the backlog is not enumerable ({}: {}); an unconfirmable enumeration cannot "
                "confirm whole-set exhaustion, so it cannot license a stop (ignorance refuses the "
                "wind-down). Fix the enumerator or the registry; the operator-owned escape sentinel is "
                "the release for a genuine block".format(
                    ctx["enum_status"], ctx["enum_detail"]), disposition)
    # ITEM-LEVEL cannot-evaluate (an unreadable proof source for a specific item) is missing evidence: the
    # schedule path DENIES (never licenses an idle), and (FIX 1) the stop path DENIES too, because an
    # unconfirmable blocker cannot confirm exhaustion (ignorance refuses the wind-down); the operator
    # escape is the release.
    if ctx.get("cannot_evaluate", []) and kind == "schedule_idle":
        if ctx["schedule_denials"] >= _ORCH_SCHEDULE_CAP and ctx["basis_unchanged"]:
            return ("ALLOW_WITH_FINDINGS",
                    "{} schedule denials on an unchanged basis; yielding with findings"
                    .format(ctx["schedule_denials"]), disposition)
        return ("DENY",
                "a proof source for {} open item(s) is unreadable (cannot-evaluate); missing evidence "
                "never licenses a new idle or wake. Fix the source, or release through the "
                "operator-owned escape sentinel.".format(len(ctx.get("cannot_evaluate", []))), disposition)
    if not ctx["actionable"]:
        rechecked = ctx["waiting"] + ctx["blocked"]
        if kind == "schedule_idle" and rechecked and ctx["wake_named"] is False:
            if ctx["schedule_denials"] >= _ORCH_SCHEDULE_CAP and ctx["basis_unchanged"]:
                # anti-farming: wake-hygiene denials are cap-relieved like every other schedule DENY, so
                # a repeated unnamed idle on an unchanged basis yields with findings rather than forever.
                return ("ALLOW_WITH_FINDINGS",
                        "{} schedule denials on an unchanged basis; yielding with findings"
                        .format(ctx["schedule_denials"]), disposition)
            return ("DENY",
                    "wake hygiene: a permitted idle must name the item or blocker it will recheck; "
                    "pending: {}".format(
                        ", ".join(i for i, _c, _p in rechecked[:_ORCH_MAX_NAMED])), disposition)
        if ctx.get("cannot_evaluate", []):
            return ("DENY",
                    "cntdef: {} open item(s) have an unreadable proof source (cannot-evaluate); an "
                    "unconfirmable blocker cannot license a stop (ignorance refuses the wind-down). "
                    "Fix the source; the operator-owned escape sentinel is the release for a genuine "
                    "block.".format(len(ctx.get("cannot_evaluate", []))), disposition)
        return ("ALLOW", "no actionable item remains; the disposition table is the enumeration",
                disposition)
    if kind == "schedule_idle" and ctx["schedule_denials"] >= _ORCH_SCHEDULE_CAP \
            and ctx["basis_unchanged"]:
        return ("ALLOW_WITH_FINDINGS",
                "{} schedule denials on an unchanged basis; yielding with findings"
                .format(ctx["schedule_denials"]), disposition)
    named = ctx["actionable"][:_ORCH_MAX_NAMED]
    more = len(ctx["actionable"]) - len(named)
    listing = "; ".join("{} ({}: {})".format(i, t, w) for i, t, w in named)
    if more > 0:
        listing += "; and {} more".format(more)
    return ("DENY",
            "AIQT rules setcmp/cntdef: {} granted open item(s) are actionable: {}. Two legal exits: "
            "do one of them, or record a proven blocker on each (a live tracked-task ref, a matching "
            "pending-decision row, fresh external evidence, or a not-before time). A fired timer or "
            "an existing cron is neither.".format(len(ctx["actionable"]), listing), disposition)


_ORCH_MAX_HORIZON_HOURS = 8760   # a staleness horizon beyond one year is out of range
_ORCH_COUNTER_MAX = 9999         # a denial counter beyond this is out of range (domain sanity)


def _v_exact_int(value, lo, hi):
    """An exact int in [lo, hi], else None (bool is rejected: True/False are not counts)."""
    return value if type(value) is int and lo <= value <= hi else None


def _v_finite_pos(value, hi):
    """A finite number in (0, hi], else None (bool, NaN, and Infinity are rejected)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or value <= 0 or value > hi:
        return None
    return value


def _orch_validate(boundary, raw):
    """The single validation membrane for a decision-input trust boundary. Returns ('ok', typed) with
    the boundary's fields validated (an invalid field is None where the caller applies the fail-safe
    direction, or already defaulted where the safe value is a default), or ('cannot-evaluate', detail)
    for an unregistered boundary. Every decision input is registered in _ORCH_SCHEMAS, so a new field
    cannot be read without a schema, and the validation-coverage gate holds the readers to this entry."""
    schema = _ORCH_SCHEMAS.get(boundary)
    if schema is None:
        return ("cannot-evaluate", "no schema for boundary {!r}".format(boundary))
    return schema(raw)


def _schema_staleness(raw):
    """A staleness horizon fails OPEN to the 24h default: a malformed horizon can neither age out valid
    evidence (a negative or NaN value) nor never age it out (Infinity), and never raises in arithmetic."""
    d = raw if isinstance(raw, dict) else {}
    th = _v_finite_pos(d.get("task_hours"), _ORCH_MAX_HORIZON_HOURS)
    eh = _v_finite_pos(d.get("external_hours"), _ORCH_MAX_HORIZON_HOURS)
    return ("ok", {"task_hours": th if th is not None else 24,
                   "external_hours": eh if eh is not None else 24})


def _schema_turn_state(raw):
    """A counter is 0 when its key is ABSENT on a readable turn-state (a fresh count), the value when
    present and valid, and None when present-but-malformed OR the whole turn-state is unreadable (raw is
    not a dict). The CALLER maps None to the fail-safe direction (stop -> the loop bound, so a malformed
    or unreadable counter never licenses unbounded denies; schedule -> 0, so it never buys cap relief).
    schedule_basis is a string or None."""
    if not isinstance(raw, dict):
        return ("ok", {"stop_denials": None, "schedule_denials": None, "schedule_basis": None})

    def _count(key):
        return 0 if key not in raw else _v_exact_int(raw[key], 0, _ORCH_COUNTER_MAX)
    basis = raw.get("schedule_basis")
    return ("ok", {"stop_denials": _count("stop_denials"),
                   "schedule_denials": _count("schedule_denials"),
                   "schedule_basis": basis if isinstance(basis, str) else None})


_ORCH_SCHEMAS = {"staleness": _schema_staleness, "turn_state": _schema_turn_state}


def _orch_token_present(needle, hay):
    """Whole-token (word-boundaried) presence test, so 'ci-7' does not match 'xci-70' and 's1' does not
    match 's10' (CX-M5/M8): the guard's id/ref matching is never a bare substring."""
    if not needle or not hay:
        return False
    return re.search(r"(?<![A-Za-z0-9_-]){}(?![A-Za-z0-9_-])".format(re.escape(needle)), hay) is not None


_ORCH_CHECKPOINT_MAX = 4096  # ids the C.3 checkpoint retains; a bound-forced drop is logged or warned about


def _orch_checkpoint_union(root, payload, record=True, warnings=None):
    """C.3 anti-shrinkage checkpoint union, run only after a status-ok enumeration. Compares the
    persisted checkpoint (<state_dir>/backlog-checkpoint.json) against the FULL validated payload
    (closed rows included: a closed row is the receipt that lets an id leave the checkpoint) and
    returns the cannot-evaluate rows to inject: an id recorded eligible or proposed that is absent from
    the payload entirely with no closed receipt (vanished), OR (FIX 2) an id recorded eligible now
    DEMOTED to proposed/ungranted with no closed receipt, is HELD rather than silently shrunk or
    demoted away. An unreadable or malformed checkpoint injects ONE marker row and is
    rewritten fresh. The rewritten checkpoint is the union of prior unreceipted eligible/proposed ids and
    this payload's, bounded at _ORCH_CHECKPOINT_MAX with a bound-forced drop logged to guard-events.
    Verdict effects are entirely existing branches (FIX 1: the stop path DENIES on an injected
    cannot-evaluate, the schedule path denies bounded by the cap); this helper adds no new deny logic, and a failed rewrite
    changes no verdict.

    record=False is the PREVIEW posture (tools/orch_preflight.py): the vanished-id injections are still
    COMPUTED for display, but NO checkpoint rewrite, init-marker write, or guard-event is emitted, so a
    preview makes no state change. A checkpoint or init marker that cannot be written adds a warning naming
    that write to the caller's warnings list whether or not its guard-events row is written, and each
    guard-events append made here (a bound-forced drop, an unwritable checkpoint, an unwritable init marker)
    that fails adds its recording-failure warning too; the hook surfaces the list in its output (the deny
    reason and banner, the block reason, or a note), so neither failed write nor any of the three rows is
    lost silently.

    FIX 4: an absent checkpoint is a legitimate FIRST window only when no prior window was ever
    initialised. When the durable init marker shows a prior window but the checkpoint is now gone, that
    is a possible reset (naive deletion or a crash) and is HELD (cannot-evaluate), never silently
    restarted. RESIDUAL (disclosed): the marker lives in the same actor-writable state dir, so this
    raises the bar (catches naive deletion) without being categorical. The checkpoint's eligibility memory (the FIX 2 demotion catch) lives in the same actor-writable state dir, so it raises the bar without being categorical."""
    sd = _orch_state_dir_for_root(root)
    path = os.path.join(sd, "backlog-checkpoint.json")
    marker = os.path.join(sd, "checkpoint-init.marker")
    injected, prior = [], {}
    present = os.path.lexists(path)
    try:
        if present:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            ids = data.get("ids") if isinstance(data, dict) else None
            if isinstance(ids, dict) and all(isinstance(v, str) for v in ids.values()):
                prior = ids
            else:
                injected.append(("backlog-checkpoint", "cannot-evaluate",
                                 "checkpoint malformed; rewritten fresh, one window uncompared"))
    except (OSError, ValueError):
        injected.append(("backlog-checkpoint", "cannot-evaluate",
                         "checkpoint unreadable; rewritten fresh, one window uncompared"))
    if not present and os.path.lexists(marker):
        # FIX 4: a prior init marker with the checkpoint now gone is a possible reset, held rather than
        # silently starting anti-shrinkage over.
        injected.append(("backlog-checkpoint", "cannot-evaluate",
                         "checkpoint missing though a prior window was initialised; possible reset"))
    # FIX 2: the per-id memory is an ELIGIBILITY label, not the raw state, so a demotion that keeps the
    # id present (state open->proposed, or granted true->false) is caught, not just an outright vanish.
    # "eligible" == granted and open (the id will classify actionable/waiting/blocked/cannot-evaluate);
    # "proposed" == state proposed or ungranted; "closed" is the receipt that lets an id leave. A legacy
    # checkpoint's "open" value reads as "eligible" (a back-compatible migration, no memory lost).
    def _label(it):
        if it["state"] == "closed":
            return "closed"
        return "eligible" if (it["granted"] and it["state"] == "open") else "proposed"
    current = {it["id"]: _label(it) for it in payload}
    for iid in sorted(prior):
        was = "eligible" if prior[iid] == "open" else prior[iid]
        if was in ("eligible", "proposed") and iid not in current:
            injected.append((iid, "cannot-evaluate",
                             "vanished from the enumeration without a close receipt"))
        elif was == "eligible" and current.get(iid) == "proposed":
            # the demotion variant: a previously-actionable id relabelled to proposed/ungranted routes
            # to the harmless proposed bucket and evades both the deny and the vanish check; held.
            injected.append((iid, "cannot-evaluate",
                             "demoted from actionable to proposed/ungranted without a close receipt; held"))
    union = {iid: ("eligible" if st == "open" else st) for iid, st in prior.items()
             if (st in ("open", "eligible", "proposed")) and current.get(iid) != "closed"}
    for it in payload:
        if it["state"] != "closed":
            union[it["id"]] = current[it["id"]]
    events = []
    if len(union) > _ORCH_CHECKPOINT_MAX:
        dropped = sorted(union)[_ORCH_CHECKPOINT_MAX:]
        union = {iid: union[iid] for iid in sorted(union)[:_ORCH_CHECKPOINT_MAX]}
        events.append(("checkpoint-bound", "dropped",
                       "{} id(s) past the {} bound: {}".format(
                           len(dropped), _ORCH_CHECKPOINT_MAX, ", ".join(dropped[:10]))))
    if record:
        # the failed write itself is reported in the hook's output; its guard-events row is a second record
        # that can succeed or fail on its own, so the warning never depends on that append failing too
        failed = []
        unsaved = _orch_write_json_atomic(path, {"version": 1, "ts": _orch_now().isoformat(),
                                                 "ids": union})
        if unsaved is not None:
            events.append(("checkpoint-unwritable", "recorded",
                           "the checkpoint could not be rewritten; the next window compares "
                           "against the prior state"))
            failed.append("Additionally, the anti-shrinkage checkpoint backlog-checkpoint.json could not be "
                          "written ({}), so it does not record this window's items: an item that leaves the "
                          "enumeration, or is demoted, before a later checkpoint write succeeds is not held "
                          "as vanished or demoted unless an earlier checkpoint already records it; record "
                          "this window's items manually (nocncl).".format(unsaved))
        elif not os.path.lexists(marker):
            # FIX 4: record that a window has now been initialised, so a later deletion is detectable.
            # FIX C: a failed marker write leaves no init marker, so a later checkpoint deletion would
            # go undetected; record it as a fact (fail-loud) and HOLD this window (cannot-evaluate),
            # never a silent gap, consistent with the other recorders.
            unsaved = _orch_write_json_atomic(marker, {"version": 1, "ts": _orch_now().isoformat()})
            if unsaved is not None:
                events.append(("checkpoint-marker-unwritable", "recorded",
                               "the checkpoint-init marker could not be written; a later "
                               "checkpoint deletion would be undetectable this window"))
                injected.append(("backlog-checkpoint", "cannot-evaluate",
                                 "checkpoint-init marker unwritable; a later deletion would go "
                                 "undetected"))
                failed.append("Additionally, the checkpoint-init marker checkpoint-init.marker could not be "
                              "written ({}), so this window is held as cannot-evaluate and a later deletion of "
                              "the checkpoint would go undetected; record it manually (nocncl).".format(unsaved))
        for ev_kind, ev_decision, ev_detail in events:
            warn = _orch_event_warn(root, ev_kind, ev_decision, ev_detail)
            if warn and warnings is not None:
                warnings.append(warn)
        if warnings is not None:
            warnings.extend(failed)
    return injected


def _orch_build_ctx(reg, root, kind, data, wake_text=None, record_checkpoint=True):
    """Assemble the decide_yield context from live state (the bindings' I/O half). Returns
    (ctx, turn_state_or_None, basis). The C.1 escape unpack, the C.2 attestation threading, and the
    C.3 checkpoint injection all land HERE, never in decide_yield: the decision core's verdict
    lattice is untouched, and ctx["escape_spoof"] and ctx["record_warn"] (the checkpoint's recording-failure
    warnings, '' when none) are recorder-only keys decide_yield never reads."""
    staleness = _orch_validate("staleness", reg.get("staleness"))[1]
    task_hours = staleness["task_hours"]
    ts = _orch_turn_state(root)
    tstate = _orch_validate("turn_state", ts)[1]
    # stop counter fails OPEN to the loop bound (an unreadable counter never licenses unbounded denies);
    # schedule counter fails CLOSED to 0 (a malformed value never manufactures cap relief, CX-R4-3).
    counter = tstate["stop_denials"] if tstate["stop_denials"] is not None else _ORCH_LOOP_BOUND
    schedule_denials = tstate["schedule_denials"] if tstate["schedule_denials"] is not None else 0
    status, payload = _orch_enumerate(reg, root)
    record_warn = []
    if status == "ok":
        live_ids, ledger_readable, _detail = _orch_live_ledger_ids(root, task_hours)
        classes = classify_backlog(payload, live_ids, ledger_readable,
                                   _orch_pending_haystack(reg, root), staleness,
                                   attestations=_orch_attestation_refs(reg, root, staleness))
        # C.3: an id recorded open/proposed at the checkpoint that vanished from this enumeration
        # with no closed receipt is held (cannot-evaluate); the injected ids enter the class-tagged
        # basis below, so under D12 a shrink is a CHANGED basis, never premature cap relief.
        classes["cannot_evaluate"].extend(
            _orch_checkpoint_union(root, payload, record=record_checkpoint, warnings=record_warn))
        enum_detail = ""
        # D12: tag each id with its class so an item flipping between classes reads as a CHANGED basis (an
        # untagged merge let such a flip collide to the same basis and skip fresh handling). CONV4-CX2 +
        # CONV6-C: waiting/blocked carry their blocker KIND:REF (a changed recheck obligation, e.g. a
        # blocker ref vendor-A->vendor-B, is a changed basis) and proposed (p:) its membership, so any
        # genuine backlog change resets the denial count rather than inheriting premature cap relief.
        basis = json.dumps(sorted(
            ["a:" + i for i, _t, _w in classes["actionable"]]
            + ["c:" + i for i, _c, _p in classes["cannot_evaluate"]]
            + ["w:{}:{}:{}".format(i, k, r) for i, k, r in classes["waiting"]]
            + ["b:{}:{}:{}".format(i, k, r) for i, k, r in classes["blocked"]]
            + ["p:" + i for i in classes["proposed"]]))
    else:
        classes = {"actionable": [], "waiting": [], "blocked": [], "cannot_evaluate": [], "proposed": []}
        enum_detail = payload
        basis = status
    wake_named = None
    rechecked = classes["waiting"] + classes["blocked"]
    if kind == "schedule_idle" and rechecked:
        wake_named = any(_orch_token_present(i, wake_text) or _orch_token_present(p, wake_text)
                         for i, _c, p in rechecked)
    basis_unchanged = tstate["schedule_basis"] == basis
    escape_active, escape_spoof = _orch_escape_active(reg, root)
    ctx = {"kind": kind, "escape": escape_active, "escape_spoof": escape_spoof,
           "record_warn": " ".join(record_warn),
           "loop_signal": data.get("stop_hook_active") is True,  # strict bool; a "false" string is not a signal
           "counter": counter, "enum_status": status, "enum_detail": enum_detail,
           "actionable": classes["actionable"], "waiting": classes["waiting"],
           "blocked": classes["blocked"], "cannot_evaluate": classes["cannot_evaluate"],
           "proposed": classes["proposed"],
           "wake_named": wake_named, "schedule_denials": schedule_denials,
           "basis_unchanged": basis_unchanged}
    return ctx, (ts if isinstance(ts, dict) else None), basis


def _orch_record_denial(root, ts, kind, basis):
    """Persist the deny counters (the guard-owned loop bound; platform-independent). Returns None on a
    successful persist, else the failure detail of _orch_save_turn_state. The increment base is sanitized
    so a tampered non-int counter cannot raise here; a STOP-path caller that gets a failure must fail OPEN,
    since an un-persistable counter never reaches the loop bound and would otherwise re-deny forever."""
    state = dict(ts or {})
    if kind == "schedule_idle":
        prior = _v_exact_int(state.get("schedule_denials"), 0, _ORCH_COUNTER_MAX)
        prior = prior if prior is not None else 0
        # D12: a CHANGED basis starts a fresh count (1), so denials accrued on a different basis can
        # never buy premature cap relief on this one.
        state["schedule_denials"] = prior + 1 if state.get("schedule_basis") == basis else 1
        state["schedule_basis"] = basis
    else:
        prior = _v_exact_int(state.get("stop_denials"), 0, _ORCH_COUNTER_MAX)
        state["stop_denials"] = (prior if prior is not None else 0) + 1
    return _orch_save_turn_state(root, state)


def _orch_record_escape_spoof(root, detail):
    """C.1 recorder: an ignored (foreign, symlinked, hardlinked, actor-owned, or writable) escape
    sentinel is a recorded fact, never a verdict input: the decision already proceeded exactly as with
    no sentinel. Appends a guard-events row and writes <state_dir>/escape-spoof.json so the next resume
    audit raises it: normally once, again at each later audit while its rename fails, and never where a
    later sentinel overwrote escape-spoof.json before that audit (only the later one is raised from it;
    the earlier one keeps only its guard-events row, where that append succeeded). FIX 6: the recording
    is FAIL-LOUD: returns '' on success, else the warning text the caller MUST append to its output (its
    banner, or the block reason of a denied Stop or TeammateIdle, which has no banner; a PreToolUse deny
    carries it in both its reason and its banner), so an ignored sentinel whose guard-events row or
    escape-spoof.json could not be written is never silently dropped: the warning names the failed write
    and asks for a manual record (when both writes fail, the resume audit has nothing to raise)."""
    ok_event = _orch_guard_event(root, "escape-spoof", "recorded", detail)
    ok_file = _orch_write_json(os.path.join(_orch_state_dir_for_root(root), "escape-spoof.json"),
                               {"ts": _orch_now().isoformat(), "detail": detail})
    if ok_event and ok_file:
        return ""
    return ("Additionally, an ignored escape sentinel could not be fully recorded (guard-events {}, "
            "escape-spoof.json {}); record the spoof manually before resuming (nocncl)."
            .format("ok" if ok_event else "FAILED", "ok" if ok_file else "FAILED"))


def _orch_open_dispositions(ctx):
    """Every NON-CLOSED disposition id a forced exit would leave behind: actionable, cannot-evaluate,
    waiting, blocked, and proposed. FIX 5: a forced (cap/bound) exit past ANY of these is recorded; the
    old gate recorded only over actionable or cannot-evaluate, so a cap/bound exit over a granted-open
    waiting or blocked row went unrecorded. When the enumeration itself failed the open set cannot even
    be established (enum_status != 'ok'); the caller records regardless in that case."""
    return ([i for i, _t, _w in ctx["actionable"]]
            + [i for i, _c, _p in ctx["cannot_evaluate"]]
            + [i for i, _k, _r in ctx["waiting"]]
            + [i for i, _k, _r in ctx["blocked"]]
            + list(ctx["proposed"]))


def _orch_record_forced_exit(root, event_name, ctx, reason):
    """C.4 recorder: a bound- or cap-released ALLOW_WITH_FINDINGS past any non-closed disposition is
    marked forced_unresolved so the next resume audit surfaces it for triage. FIX 5: the record is
    APPEND-ONLY and uniquely keyed (<state_dir>/forced-exit.jsonl, one row per forced exit with a
    unique key), so two forced exits before a resume are BOTH kept and each normally raised once (at
    least once if recording that it was raised fails), never clobbered into a single fixed file. No
    register row, attestation, or escape-adjacent artefact ever suppresses this record. Returns '' on
    success, else the failure text the caller MUST append to its banner (the one record this design
    leans on can never fail silently). The verdict is never changed here."""
    open_ids = _orch_open_dispositions(ctx)
    enum_ok = ctx.get("enum_status") == "ok"
    key = _orch_now().isoformat() + "-" + os.urandom(6).hex()
    named = (", ".join(open_ids[:_ORCH_MAX_NAMED])
             or ("open set unestablished" if not enum_ok else "none"))
    ok_event = _orch_guard_event(root, "forced_unresolved", "allow_with_findings",
                                 "{}: open ids {}".format(event_name, named))
    ok_file = _orch_append_jsonl(os.path.join(_orch_state_dir_for_root(root), "forced-exit.jsonl"),
                                 {"ts": _orch_now().isoformat(), "event": event_name, "key": key,
                                  "open_ids": open_ids, "reason": reason,
                                  "enum_status": ctx.get("enum_status")})
    if ok_event and ok_file:
        return ""
    return ("Additionally, the forced-exit record could not be fully persisted (guard-events {}, "
            "forced-exit.jsonl {}); record the forced exit manually before resuming (nocncl)."
            .format("ok" if ok_event else "FAILED", "ok" if ok_file else "FAILED"))


def _orch_stop_family(data, event_name, kind):
    """The shared Stop/TeammateIdle binding: scope, decide, map the verdict to the event's block
    mechanism (exit 2, doc-confirmed for both events 2026-08-29). Fail-OPEN on its own I/O errors;
    a cannot-evaluate DENIES (FIX 1)."""
    root = _orch_root(data)
    if root is None:
        return _allow()
    status, reg = _orch_registry(root)
    if status == "absent":
        return _allow()
    if status == "bad":
        return _stop_warn("AIQT guardrail: the orchestration registry could not be read "
                          "({}); the {} fails open on its own read error and, because the registry is "
                          "unreadable, writes NO guard-event (no record).".format(reg,
                                                                                  event_name))
    if not _orch_scope_live(reg, root, data.get("session_id")):
        return _allow()
    ctx, ts, basis = _orch_build_ctx(reg, root, kind, data)
    spoof_warn = (_orch_record_escape_spoof(root, ctx["escape_spoof"])
                  if ctx.get("escape_spoof") else "")
    verdict, reason, disposition = decide_yield(ctx)
    if verdict == "DENY":
        unsaved = _orch_record_denial(root, ts, kind, basis)
        if unsaved is not None:
            warn = ("the denial counter could not be persisted ({}), so the loop bound cannot advance; "
                    "failing OPEN with findings rather than re-denying. Underlying: ".format(unsaved) + reason)
            ev = _orch_event_warn(root, event_name, "allow_unpersistable", warn)
            return _stop_warn("AIQT guardrail ({}): {}{}".format(
                event_name, warn, _orch_warn_tail(ev, spoof_warn, ctx["record_warn"])))
        ev = _orch_event_warn(root, event_name, "deny", reason)
        # a DENY blocks (exit 2) and carries no banner, so every recording-failure warning (the deny's own
        # guard-events row, the spoof record, the checkpoint rows) goes on the block reason
        return (2, None, reason + _orch_warn_tail(ev, spoof_warn, ctx["record_warn"]))
    if verdict == "ALLOW_WITH_FINDINGS":
        ev = _orch_event_warn(root, event_name, verdict.lower(), reason)
        extra = ""
        if (ctx["counter"] >= _ORCH_LOOP_BOUND or ctx["loop_signal"]) \
                and (_orch_open_dispositions(ctx) or ctx["enum_status"] != "ok"):
            # C.4: the bound released this exit past open work; mark it forced_unresolved for the
            # next resume audit's triage. The fail-open verdict itself is unchanged.
            extra = _orch_record_forced_exit(root, event_name, ctx, reason)
        return _stop_warn("AIQT guardrail ({}): {}{}".format(
            event_name, reason, _orch_warn_tail(extra, ev, spoof_warn, ctx["record_warn"])))
    if ctx["escape"]:
        # the operator-escape ALLOW: its guard-events row is the only record that the override was used,
        # so a failed append is surfaced (the ALLOW stands)
        tail = _orch_warn_tail(_orch_escape_event_warn(root, event_name, reason), spoof_warn,
                               ctx["record_warn"])
    else:
        # a clean ALLOW with no escape: its row is the over-fire metric only, best effort, a failed append
        # is not surfaced
        _orch_guard_event(root, event_name, verdict.lower(), reason)
        tail = _orch_warn_tail(spoof_warn, ctx["record_warn"])
    if tail:
        # a clean ALLOW whose spoof or checkpoint record FAILED still surfaces it (never a silent None)
        return _stop_warn("AIQT guardrail ({}):{}".format(event_name, tail))
    return _allow()


def orch_stop_guard(data):
    """setcmp/cntdef/trkasy/cnclse, Stop: deny a stop past enumerated actionable work
    OR any cannot-evaluate (FIX 1: ignorance refuses the wind-down; the operator escape releases);
    bounded by the guard-owned counter."""
    return _orch_stop_family(data, "Stop", "stop")


def orch_teammate_idle(data):
    """The same decision core at the TeammateIdle boundary. Cannot-evaluate DENIES the idle
    (FIX 1: ignorance refuses the wind-down), the same as the Stop boundary."""
    return _orch_stop_family(data, "TeammateIdle", "stop_idle" if False else "stop")


def _orch_register_wake(root, ts, prompt):
    """Record the sha256 of an ALLOWED wake's prompt into turn-state wake_digests (bounded), so the
    returning UserPromptSubmit is recognised as timer-originated by orch_prompt_stamp and does not reset
    the loop-guard counters or stamp a false genuine-human-input time (G1: the classifier was dead
    because nothing ever wrote wake_digests). Returns '' when the digest was written or there is no prompt
    to register, else the warning the caller appends to its output (the ALLOW stands). The save is atomic, so
    a failed one leaves the previous turn-state unchanged; the warning still promises no classification of
    the returning prompt, which reads as timer-originated where turn-state holds a matching digest (an
    earlier identical wake's) when it arrives, and as genuine human input otherwise."""
    if not isinstance(prompt, str) or not prompt:
        return ""
    digest = __import__("hashlib").sha256(prompt.encode("utf-8", "replace")).hexdigest()
    state = dict(ts or {})
    wd = state.get("wake_digests")
    wd = [d for d in wd if isinstance(d, str)] if isinstance(wd, list) else []
    wd.append(digest)  # a multiset: two identical wakes register two tokens, each consumed once (CX-M6)
    state["wake_digests"] = wd[-64:]  # bounded so the list cannot grow without limit
    unsaved = _orch_save_turn_state(root, state)
    if unsaved is None:
        return ""
    return ("Additionally, this wake's prompt digest could not be written to turn-state.json ({}), so how the "
            "prompt it returns with is classified is uncertain: it may read as genuine human input or as "
            "timer-originated; record the wake manually (nocncl).".format(unsaved))


def orch_yield_tool(data):
    """setcmp/cntdef/tstamp/estsep, PreToolUse over the scheduling tools: judge a call that parks the
    run (schedule_idle) or ends the loop (stop=true) against a fresh enumeration. The schedule path
    fails CLOSED on cannot-evaluate, bounded by the denial cap."""
    root = _orch_root(data)
    if root is None:
        return _allow()
    status, reg = _orch_registry(root)
    if status == "absent":
        return _allow()
    if status != "ok":
        return _deny(
            "AIQT guardrail (setcmp/cntdef): the orchestration registry could not be read ({}); a "
            "scheduling call that would park or end the run cannot be judged, so it is denied fail-closed "
            "(ignorance refuses the wind-down). Fix the orchestration registry, or continue the actionable "
            "work rather than parking or stopping the run.".format(reg),
            "AIQT guardrail: denied a scheduling call under an unreadable orchestration registry "
            "(ignorance refuses the wind-down); fix the registry or keep working.")
    if not _orch_scope_live(reg, root, data.get("session_id")):
        return _allow()
    tool = data.get("tool_name")
    declared = reg.get("yield_tools")
    if not isinstance(declared, list) or tool not in declared:
        return _allow()  # inside the matcher but outside the registry roster: out of scope
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    kind = "stop" if tool_input.get("stop") is True else "schedule_idle"
    wake_text = " ".join(str(v) for v in tool_input.values() if isinstance(v, str))
    ctx, ts, basis = _orch_build_ctx(reg, root, kind, data, wake_text=wake_text)
    spoof_warn = (_orch_record_escape_spoof(root, ctx["escape_spoof"])
                  if ctx.get("escape_spoof") else "")
    # Measured quiet beats a claimed quiet duration (estsep: the two grades never blend silently).
    claim = _ORCH_QUIET_CLAIM_RE.search(wake_text or "")
    measured_min = None
    if isinstance(ts, dict):
        stamp = _orch_parse_utc(ts.get("last_human_input_utc"))
        if stamp:
            measured_min = (_orch_now() - stamp).total_seconds() / 60.0
    if kind == "schedule_idle" and claim and "quiet" in (wake_text or "").lower() \
            and measured_min is not None and float(claim.group(1)) > measured_min + 1.0:
        reason = ("AIQT rule estsep/tstamp: the call claims {} quiet minutes but the measured gap "
                  "since the last genuine human input is {:.1f} minutes; the measured figure wins. "
                  "Re-issue without the unmeasured claim.".format(claim.group(1), measured_min))
        # The quiet-claim DENY has a trivial legit exit (re-issue without the claim), so it does NOT
        # consume the schedule cap (CX-M2: repeated quiet-claim denials could otherwise farm the cap
        # into an ALLOW_WITH_FINDINGS that parks genuinely actionable work). A recording-failure warning
        # goes on both the deny reason and the banner.
        tail = _orch_warn_tail(_orch_event_warn(root, "yield-tool", "deny", reason), spoof_warn,
                               ctx["record_warn"])
        return _deny(reason + tail,
                     "AIQT guardrail: denied a scheduling call whose quiet-duration claim "
                     "contradicts the measured figure." + tail)
    verdict, reason, _disposition = decide_yield(ctx)
    if verdict == "DENY":
        # a counter that cannot be persisted leaves this deny uncounted, so the cap (or the loop bound) is
        # never reached while the write fails; the deny stands and the failure is reported on it
        unsaved = _orch_record_denial(root, ts, kind, basis)
        counter_warn = "" if unsaved is None else (
            "Additionally, the denial counter could not be written to turn-state.json, so this deny does not "
            "count toward the {} and its relief is not reached while the write fails ({}); record it manually "
            "(nocncl).".format("scheduling cap" if kind == "schedule_idle" else "loop bound", unsaved))
        tail = _orch_warn_tail(_orch_event_warn(root, "yield-tool", "deny", reason), counter_warn,
                               spoof_warn, ctx["record_warn"])
        return _deny(reason + tail,
                     "AIQT guardrail: denied a {} call past the enumerated backlog.{}".format(tool, tail))
    # G1: register the ALLOWED wake's prompt digest so its returning UserPromptSubmit is classified
    # timer-originated (not genuine human input), preserving the loop-guard counters across the wake; a
    # failed write is reported on every allow path below (the ALLOW stands)
    wake_warn = _orch_register_wake(root, ts, tool_input.get("prompt")) if kind == "schedule_idle" else ""
    if verdict == "ALLOW_WITH_FINDINGS":
        ev = _orch_event_warn(root, "yield-tool", verdict.lower(), reason)
        msg = "AIQT guardrail: {}".format(reason)
        forced = ((kind == "stop" and (ctx["counter"] >= _ORCH_LOOP_BOUND or ctx["loop_signal"]))
                  or (kind == "schedule_idle" and ctx["schedule_denials"] >= _ORCH_SCHEDULE_CAP
                      and ctx["basis_unchanged"]))
        if forced and (_orch_open_dispositions(ctx) or ctx["enum_status"] != "ok"):
            # C.4/FIX 5: cap or bound relief past ANY non-closed disposition (or during a whole-
            # enumerator failure) is marked forced_unresolved; a failed record surfaces in the
            # returned systemMessage rather than silently vanishing.
            extra = _orch_record_forced_exit(root, "yield-tool", ctx, reason)
            if extra:
                msg += " " + extra
        return _allow_note(msg + _orch_warn_tail(ev, wake_warn, spoof_warn, ctx["record_warn"]))
    if ctx["escape"]:
        # the operator-escape ALLOW: its guard-events row is the only record that the override was used,
        # so a failed append is surfaced (the ALLOW stands)
        tail = _orch_warn_tail(_orch_escape_event_warn(root, "yield-tool", reason), wake_warn, spoof_warn,
                               ctx["record_warn"])
    else:
        # a clean ALLOW with no escape: its row is the over-fire metric only, best effort, a failed append
        # is not surfaced
        _orch_guard_event(root, "yield-tool", verdict.lower(), reason)
        tail = _orch_warn_tail(wake_warn, spoof_warn, ctx["record_warn"])
    if tail:
        # a clean ALLOW whose wake digest, spoof or checkpoint record FAILED still surfaces it (never a
        # silent None)
        return _allow_note("AIQT guardrail:" + tail)
    return _allow()


def orch_ask_guard(data):
    """cntdef/recfst bounded by humovs, PreToolUse AskUserQuestion: deny a blocking question in
    unattended mode with the record-and-continue instruction. The operating mode is read by _orch_mode, one
    sound parser over the shared mode file: a leading byte-order mark is tolerated; the `Operating-mode:`
    declaration is parsed on its own PHYSICAL line (its value must begin with attended or unattended, compound
    annotations allowed, never a substring, or it fails closed); a JSON marker must be exactly
    {"mode": "<attended|unattended...>"} with no extra or duplicate keys or it fails closed. A mode marker that
    is present but unreadable (a socket, refused at the open, included), not a regular file (a FIFO or a
    device included, opened without waiting), larger than _ORCH_MODE_MAX_BYTES, or non-UTF-8, a mode path
    holding a NUL or not strict UTF-8 (each such
    read outcome names its reason in the deny), a present-but-unrecognized `Operating-mode:` declaration
    (empty, or not beginning with attended/unattended), a present JSON value that parses but is not exactly a single string
    "mode" key (a scalar, an array, an object with extra keys, or an object without a string "mode"), or, with no
    declaration line present, a JSON-shaped marker (content beginning with `{`, `[`, or `"`) that is malformed
    (an unterminated string, trailing garbage, or duplicate keys) fails CLOSED to the guards-armed (unattended)
    posture, so the blocker arms rather than silently disarming; only a genuinely absent file, an empty/whitespace
    file, or content with no declaration line that does not parse as JSON and whose first non-whitespace character
    is not `{`, `[`, or `"` (ordinary prose, for example `42 items done` or `true trailing`, so prose beginning
    with a number or word does not arm) is the fail-OPEN no-marker default (this guards one mistake shape, not a
    security boundary). Its regression vectors are held by the behaviour self-test."""
    if data.get("tool_name") != "AskUserQuestion":
        return _allow()
    root = _orch_root(data)
    if root is None:
        return _allow()
    status, reg = _orch_registry(root)
    if status != "ok":
        return _allow()  # absent OR unreadable registry: fail open, this control is advisory-shaped
    mode, unread = _orch_mode_detail(reg, root)
    if mode is None or "unattended" not in mode:
        if mode is None and not _orch_guard_event(
                root, "ask-guard", "fail-open",
                "mode record absent, empty, or prose with no declaration or JSON marker"):
            # the fail-open stands; only the failed guard-events row is surfaced, never a silent loss
            return _allow_note("AIQT guardrail: the ask guard failed open (no operating-mode record it "
                               "could read as a declaration), and the guard-events row recording that "
                               "fail-open could not be written; record it manually (nocncl).")
        return _allow()
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    questions = tool_input.get("questions") if isinstance(tool_input.get("questions"), list) else []
    text = json.dumps(tool_input, sort_keys=True)
    digest = __import__("hashlib").sha256(text.encode("utf-8", "replace")).hexdigest()
    key = "{}::{}".format(data.get("session_id", ""), data.get("tool_use_id", digest[:12]))
    pending_path = os.path.join(_orch_state_dir_for_root(root), "pending-asks.jsonl")
    rows, _bad = _orch_read_jsonl(pending_path)
    recorded = rows is not None and any(r.get("key") == key for r in rows)
    if rows is not None and not recorded:
        # A bounded REDACTED summary plus digest, never the question text (log-redaction discipline).
        recorded = _orch_append_jsonl(pending_path, {"ts": _orch_now().isoformat(), "key": key,
                                                     "questions": len(questions), "len": len(text),
                                                     "digest": digest})
    reason = ("AskUserQuestion BLOCKED: the session operating-mode is unattended. RECORD the decision "
              "as pending (the registry's pending-decisions surface) with your recommended option and "
              "rationale, then CONTINUE with the next authorized independent item. If the decision "
              "sits at the human-oversight threshold (high-consequence, irreversible, or "
              "outward-facing), record it and HOLD that item; the hold never licenses acting without "
              "the answer. If the maintainer is in fact present, set an attended operating-mode in "
              "the mode record first, then re-issue.")
    if unread:
        # a mode file the reader cannot read as text fails closed to unattended; the deny names why
        reason += " The mode record reads as unattended because {} (it fails closed).".format(unread)
    tail = _orch_warn_tail(_orch_event_warn(
        root, "ask-guard", "deny", "pending key {}{}".format(key, "" if recorded else " (NOT persisted)")))
    banner = ("AIQT guardrail: denied a blocking question in unattended mode; recorded pending."
              if recorded else "AIQT guardrail: denied a blocking question in unattended mode, but the "
              "pending row could NOT be persisted; record the decision manually (nocncl).")
    return _deny(reason + tail, banner + tail)


_ORCH_PLAIN_COMMAND_RE = re.compile(r"[A-Za-z0-9_ \t./=:@,+%-]+")
# Shell reserved words that change execution without a metacharacter: `coproc` backgrounds a coprocess whose
# output the harness does not capture, and the compound/prefix keywords start a construct that is not a plain
# producer. They carry only letters, so the plain-command charset alone would admit them; an exact token
# match excludes them. A quoted or otherwise obscured spelling already carries a metacharacter and is caught.
_ORCH_SHELL_KEYWORDS = frozenset((
    "if", "then", "else", "elif", "fi", "case", "esac", "for", "select", "while", "until",
    "do", "done", "in", "function", "time", "coproc"))


# Bash's own word-start rule for a `#` comment: a `#` opens a comment only at the start of a word, and bash
# delimits words with its blanks (space, tab) and newline and with its metacharacters (; & | ( ) < >). Other
# characters that Python's str.isspace() accepts (carriage return, vertical tab, form feed, no-break space,
# the 0x1c-0x1f separators, NEL, and the Unicode spaces) are ordinary word characters to bash.
_ORCH_BASH_WORD_BREAKS = frozenset(" \t\n;&|()<>")


def _orch_foreground_scan(command, bash_word_starts):
    """One quote- and escape-tracking pass over a foreground command: "detach" when it meets an executable,
    unquoted, unescaped bare `&` control operator, "unbalanced" when it ends still inside a single or double
    quote, None otherwise. bash_word_starts selects where a `#` opens a comment: False keeps the historical
    rule (after any str.isspace() character), True uses bash's rule (_ORCH_BASH_WORD_BREAKS). The guard runs
    BOTH and denies when either reports, so the bash rule can only ADD denies to the historical one."""
    in_single = in_double = escaped = False
    prev_dup = False  # the previous char was an unquoted, unescaped >, <, or | (a dup/pipe operator lead)
    word_start = True  # the next unquoted char begins a word (start of string, or after a word break)
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if escaped:
            escaped, prev_dup, word_start = False, False, False
            i += 1
            continue
        if in_single:
            if ch == "'":
                in_single = False
            prev_dup, word_start = False, False
            i += 1
            continue
        if in_double:
            if ch == "\\":
                escaped = True
            elif ch == '"':
                in_double = False
            prev_dup, word_start = False, False
            i += 1
            continue
        if ch == "#" and word_start:  # an unquoted, word-start comment: the rest of THIS line is not code
            # A `#` comment runs only to the end of ITS line, not to the end of a multi-line command. Skip
            # to the next newline and resume the scan, so a real bare `&` on a LATER line is not smuggled
            # past by a comment on an earlier one. Breaking the whole scan here silently allowed exactly that
            # (L-GS1: `echo hi  # note\nsleep 100 &`); this fails closed on the comment-obscured detach.
            nl = command.find("\n", i)
            if nl == -1:
                break  # no later line: the comment runs to the end of the string, nothing more to scan
            i = nl  # resume at the newline; the whitespace branch consumes it and begins a new line/word
            continue
        if ch.isspace():
            prev_dup, word_start = False, (ch in _ORCH_BASH_WORD_BREAKS) if bash_word_starts else True
            i += 1
            continue
        if ch == "\\":
            escaped, prev_dup, word_start = True, False, False
            i += 1
            continue
        if ch == "'":
            in_single, prev_dup, word_start = True, False, False
            i += 1
            continue
        if ch == '"':
            in_double, prev_dup, word_start = True, False, False
            i += 1
            continue
        if ch == "&":
            nxt = command[i + 1] if i + 1 < n else ""
            if nxt == "&":  # `&&` logical AND: not a detach
                prev_dup, word_start = False, bash_word_starts
                i += 2
                continue
            if nxt == ">":  # `&>` / `&>>` redirect: not a detach
                prev_dup, word_start = False, bash_word_starts
                i += 1
                continue
            if prev_dup:  # `>&` / `<&` / `|&` descriptor-dup or pipe-stderr: not a detach
                prev_dup, word_start = False, bash_word_starts
                i += 1
                continue
            return "detach"  # an executable bare `&` control operator: a foreground detach
        prev_dup = ch in (">", "<", "|")
        word_start = bash_word_starts and ch in _ORCH_BASH_WORD_BREAKS
        i += 1
    # A scan that ended still inside an unbalanced quote could not prove a later `&` was quoted; it fails
    # toward the DENY (with its own reason) rather than silently allowing a possibly-real detach.
    return "unbalanced" if in_single or in_double else None


def _orch_foreground_detach_kind(command):
    """"detach" when either scan rule meets a bare `&`, else "unbalanced" when either ends inside a quote,
    else None. The historical rule and bash's `#` word-start rule are both run so that the bash rule only
    ADDS denies: a `#` after a character bash does not treat as a word break (a carriage return, a
    no-break space, a 0x1c separator, any other non-blank str.isspace() character) is not a comment to
    bash, so an `&` after it is scanned; a `#` after a metacharacter (`;#`) IS a comment to bash, so a
    quote in that comment no longer shifts the scan past a real `&` on the next line."""
    kinds = (_orch_foreground_scan(command, False), _orch_foreground_scan(command, True))
    if "detach" in kinds:
        return "detach"
    return "unbalanced" if "unbalanced" in kinds else None


def _orch_foreground_detach(command):
    """True when a foreground command carries an executable, unquoted, unescaped bare `&` control operator
    that detaches a child, launching asynchronous work the foreground tool call does not track. The bare
    detach `&` is distinguished from the shell forms that also carry an ampersand but do NOT detach: the
    `&&` logical-AND, the `&>` and `&>>` redirects, the `<&`, `>&`, and `|&` descriptor-duplication and
    pipe-stderr operators, any single-quoted, double-quoted, or backslash-escaped ampersand, and an `&`
    inside an unquoted, word-start `#` comment (comment text, not an operator). A dedicated quote- and
    escape-tracking scan is used, NOT _segments: that helper strips quote and escape provenance and
    classifies both `echo "&"` and `echo \\&` as an `&` separator, which would over-fire. It also drops a
    word-start `#` comment so a commented-out `&` does not prompt, but only to the END OF THAT LINE: a
    comment never suppresses a later line, so a real bare `&` on a subsequent line of a multi-line command
    is still caught rather than smuggled past. Where a `#` starts a comment is read under two rules, the
    historical one (after any str.isspace() character) and bash's (after a space, tab, newline, or
    metacharacter), and either rule's detach denies (_orch_foreground_detach_kind).

    A SCAN THAT ENDS INSIDE A QUOTE FAILS TOWARD DENY: a scan that ends still inside an unbalanced single
    or double quote cannot prove that a later `&` is quoted rather than an operator (an unbalanced quote,
    or a construct this scan does not model such as ANSI-C `$'...'` or locale `$"..."` quoting, can leave
    the scan `inside quotes` and skip a real trailing `&`), so it reports a detach (True -> DENY, with an
    unbalanced-quote reason) rather than allowing. This covers only a quote the scan still sees as open at
    the END: a quote it misreads in mid-string can leave it balanced but misaligned, which silently allows
    (the false-allow residual below). A genuinely balanced, quoted `&` is literal and correctly ignored.

    NARROW BY CONSTRUCTION: this scans for the accidental bare-operator case only, as its own quote
    tracking reads the outer level. Grammar it does not model (a here-document body, a nested shell string,
    an alias or function that renames a detacher, and runtime detachers such as nohup/setsid/disown/coproc)
    is a disclosed residual; where such a construct still leaves an unquoted bare `&`, or leaves the scan
    inside an unbalanced quote at the end, it errs toward the DENY, but a detacher that carries no bare `&`
    (setsid worker, a nested `bash -c '... &'`) is NOT caught here and is a silent-allow residual disclosed
    in the manifest.

    OVER-REFUSAL RESIDUAL (disclosed): a here-document body is scanned as CODE, not data, even under a
    quoted delimiter (<<'EOF'). A safe here-document whose body carries an unquoted `&` (`cat > f <<'EOF'`
    then `Fix A & B`) or an unbalanced apostrophe (`the user's file`) is therefore DENIED although nothing
    detaches. In the commit-message form wrapped in double quotes ("$(cat <<'EOF' ... EOF)") the body is
    read as double-quoted text, and each double quote in the body toggles that reading, so whether a body
    `&` is denied depends on where it falls relative to those quotes (`say "a & b"` is denied, `say "hi" &
    bye` is allowed). The remedy is to write the text to a file and pass the file. This misreading is NOT
    only in the safe direction: the same body quotes cause the quote-shift false-allow below.

    KNOWN FALSE-ALLOW RESIDUAL (confirmed against real bash, disclosed in the manifest, which lists the same
    cases): the scan reads only the outer quoting level, and its quote and comment tracking can diverge from
    bash's in further ways than those listed here, so this list is NOT complete. Known cases include a real
    detach that is NOT seen (1) when its `&` sits inside a command substitution or backtick wrapped in
    double quotes (echo "$(job &)"); (2) inside a string that eval or quote removal re-reads as code
    (e'v'al 'job &', {eval,} 'job &', \\eval 'job &'); (3) after an ANSI-C $'...' quote with an escaped
    quote that leaves the scan balanced but misaligned; (4) inside an arithmetic subscript that runs a
    substitution; (5) QUOTE SHIFT: after a quote character that bash reads as data but the scan reads as a
    quote, above all an apostrophe or double quote in a here-document body (quoted delimiter or not,
    including the double-quoted commit-message form), which flips the scan's quote state so that a LATER
    real bare `&` (between two here-documents, or before a second stray quote that rebalances the scan)
    reads as quoted text and is allowed; and (6) COMMENT SHIFT: after a `#` that follows a blank inside an unquoted ${...} parameter expansion,
    which bash reads as expansion text but the scan reads as a comment start, so a real bare `&` later on
    that line is skipped (echo ${x:- #} & job, echo ${line%% #*} & job)."""
    return _orch_foreground_detach_kind(command) is not None


# ROUND-2 FINDING 9: sinks that TRUNCATE their input, so a producer piped into one loses both its full
# output and (absent `pipefail`) its exit status. A subset of _CONSOLE_SINKS: cat/tee pass output through
# (tee can even durably capture to a real file), so only head/tail are truncating.
_ORCH_TRUNCATING_SINKS = frozenset(("head", "tail"))

# ROUND-7 (codex finding 5). Shell command-modifier wrappers a truncating sink can hide behind: a pipeline
# stage 'command head' / 'env head' / 'nice head' must resolve THROUGH the wrapper to the real sink. Each
# value-taking separated option of a wrapper is listed so its value token is skipped rather than mistaken
# for the command word. A literal leading backslash (alias-suppression '\head') is stripped when matching.
_ORCH_SINK_WRAPPERS = frozenset((
    "command", "env", "builtin", "exec", "nice", "nohup", "stdbuf", "time", "\\"))
_ORCH_WRAPPER_SEP_VALUE_OPTS = {
    "env": frozenset(("-u", "--unset", "-C", "--chdir", "-S", "--split-string")),
    "nice": frozenset(("-n", "--adjustment")),
    "stdbuf": frozenset(("-i", "--input", "-o", "--output", "-e", "--error")),
    "time": frozenset(("-o", "--output", "-f", "--format")),
    "exec": frozenset(("-a",)),
}


def _orch_effective_word_index(argv):
    """The INDEX in argv of the effective command word that _orch_effective_sink_word resolves (the same
    wrapper peeling, shared with the review dispatch pin), or None when there is no command word."""
    idx = _command_word_index(argv)   # skip leading env-assignments (FOO=bar)
    n = len(argv)
    guard = 0
    while idx < n and guard < n + 1:
        guard += 1
        word = argv[idx].lstrip("\\").rsplit("/", 1)[-1]
        if word not in _ORCH_SINK_WRAPPERS:
            return idx
        sep_value_opts = _ORCH_WRAPPER_SEP_VALUE_OPTS.get(word, frozenset())
        j = idx + 1
        while j < n:
            tok = argv[j]
            if word == "env" and _ENV_ASSIGN_RE.match(tok):
                j += 1; continue                       # env VAR=val assignment
            if tok == "--":
                j += 1; break                           # end of the wrapper's options: next token is the cmd
            if tok.startswith("-") and tok != "-":
                if tok in sep_value_opts and j + 1 < n:
                    j += 2                               # a separated value-taking option: skip option + value
                else:
                    j += 1                               # a flag or an attached-value option
                continue
            break                                        # the wrapped command word (or another wrapper)
        if j >= n:
            return None                                  # the wrapper consumed every token: no command word
        idx = j
    return idx if idx < n else None


def _orch_effective_sink_word(argv):
    """ROUND-7 (codex finding 5). The EFFECTIVE command word of a pipeline stage, resolved THROUGH leading
    shell command-modifier wrappers (command/env/builtin/exec/nice/nohup/stdbuf/time and a literal '\\'
    alias-suppression escape) so a truncating sink hidden behind one ('command head', 'env head',
    'nice -n0 head', 'stdbuf -oL tail') is still matched against _ORCH_TRUNCATING_SINKS. Leading
    env-assignments (FOO=bar) are skipped first, as _command_word does. For each recognized wrapper word the
    wrapper is peeled; then its OWN leading option/assignment tokens are skipped - env VAR=val assignments, a
    '--' end-of-options marker, and any '-'-led option (the known value-taking separated options of that
    wrapper skip their value too). Resolution STOPS, returning the current word's basename, at the first
    token that is neither a wrapper nor a skippable option/assignment, so an unmodelled option grammar
    degrades to the un-resolved word (a disclosed under-match residual, in the safe direction for a DENY
    guard - it never invents a false head/tail match on a non-sink command). Purely lexical."""
    idx = _orch_effective_word_index(argv)
    return "" if idx is None else argv[idx].lstrip("\\").rsplit("/", 1)[-1]


def _orch_json_kind(value):
    """A short JSON type phrase for a malformed-input deny message ('null', 'a string', 'an array')."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    return "an object" if isinstance(value, dict) else "a " + type(value).__name__


_ORCH_REQUIRE_REGISTRY_ENV = "AIQT_ORCH_REQUIRE_REGISTRY"
_ORCH_REQUIRE_REGISTRY_OFF_VALUES = ("", "0", "false", "no", "off")


def _orch_registry_required():
    """True when the adopter opted this session into REGISTRY-REQUIRED mode: the environment variable
    AIQT_ORCH_REQUIRE_REGISTRY set to anything but an explicit off value ("", "0", "false", "no", "off",
    case-insensitive in ASCII letters only; unset is off). The value is compared EXACTLY as set, with
    nothing stripped, so an off word with any added character (a space, tab, newline, or no-break space
    around it) is not an off value and reads as ON. Under it, an ABSENT orchestration registry DENIES every
    Bash call that passes the pre-scope checks instead of leaving orch_truncation_guard inert, and ABSENT
    includes a registry reachable only through a git-resolved toplevel (core.worktree) when git fails, so
    there a git failure alone denies; a registry entry the discovery probe cannot confirm
    (_ORCH_REG_CANNOT_EVALUATE: a `.aiqt` that is a regular file, a symlink, or a directory this process
    lacks search (execute) permission on, such as mode 0o600 or 0o000 for a process those modes bind (where
    O_PATH exists, mode 0o100 evaluates normally for its owner and for a process the mode bits do not bind,
    such as root, and cannot be evaluated by any other process; where O_PATH is unavailable, the O_RDONLY
    fallback open also needs read permission, so mode 0o100 cannot be evaluated by any process the mode bits
    bind, its owner included), or a
    first present registry name that is not a regular file or cannot be stat'ed) is not a registry
    either and denies the same way; the default (variable unset) is unchanged. An environment variable, not a pack config key, because every pack config
    surface (.aiqt/orchestration.local.json, .aiqt/orchestration.json, .aiqt/gensrc.json) is a per-repo file
    located by the same cwd-anchored lookup whose EMPTY result this mode exists to fail closed on, so a
    file-based key can never speak exactly when it is needed; the hook execution environment is the one
    channel independent of that lookup. A garbled or padded value reads as ON, the deny-safe direction for
    an explicitly configured strict mode."""
    value = os.environ.get(_ORCH_REQUIRE_REGISTRY_ENV)
    if value is None:
        return False
    return not (value.isascii() and value.lower() in _ORCH_REQUIRE_REGISTRY_OFF_VALUES)


def orch_truncation_guard(data):
    """trkasy/vrfdlv/nocncl, PreToolUse Bash, scoped to run_in_background dispatches. AIRTIGHT-NARROW: it
    performs NO shell parsing, so no lexical or quoting edge can fabricate a capture. A background dispatch
    is ALLOWED only when its command is a PLAIN command carrying no shell metacharacter and no shell reserved
    word (whose full stdout the harness binds to the TaskOutput completion signal); ANY shell metacharacter -
    a pipe, redirect, separator, quote, expansion, substitution, grouping, glob, comment, or newline - or a
    reserved word such as `coproc` (which backgrounds a coprocess) makes the capture unprovable by this
    guard. NO-ASK posture: an unprovable background dispatch is NOT a hazard (a dispatch that redirects or
    pipes its own output is a normal, legitimate form, and denying it would block the orchestrator's own
    fan-out), and the post-execution delivery-marker discipline is the backstop, so it ALLOWS with an
    informational note pointing at durable capture (redirect stdout to a real file, or make a real-file tee
    the final stage) rather than prompting. Whether the output actually reaches durable capture is a run-time
    property (the invoked program's own behaviour, or a platform limit such as the harness output ceiling)
    out of view here. A foreground call is in scope only for one NARROW case (L-GS1 / trkasy): shell syntax
    that DETACHES a child with a bare `&` launches asynchronous work the foreground tool call does not track,
    and a bare-& detach is never the right way to launch tracked work, so a readable foreground command
    carrying such an operator DENIES-and-educates (use the tracked background dispatch, or keep it foreground
    and wait); every other foreground call in registry scope remains out of scope (the harness returns its
    output directly; in registry-required mode a cwd with no registry is denied before this point).

    MALFORMED INPUT FAILS CLOSED (check-fails-closed-on-unreadable): a tool_input that is missing, null, or
    not a JSON object, a run_in_background that is present but not a real boolean (the string "true" is
    malformed, never read as foreground), and a command that is not a string are each DENIED with a
    reason naming the defect, never silently allowed. REGISTRY SCOPE (a disclosed residual, not a
    fail-closed case): the registry is this suite's scope declaration, located by the UNION of a walk of
    the cwd's physical ancestor chain with no-follow, descriptor-anchored lookups (_orch_registry_walk,
    with its post-walk concurrent-move recheck) and, where git resolves a toplevel for the cwd, that
    toplevel's registry too (_orch_git_toplevel_has_registry: core.worktree can point the work tree off
    the ancestor chain; by default a git discovery failure alone - no git binary, a dubious-ownership
    refusal, a broken config, a bare repository - still never denies, while in registry-required mode one
    that hides a registry reachable only through the git toplevel reads as absent and denies), so with NO registry entry on that chain and
    none at a git-resolved toplevel the guard is, by default, inert and allows every Bash call that
    passes the pre-scope checks below, while a chain or toplevel directory whose registry entry is present,
    unreadable, or invalid keeps it active. REGISTRY-REQUIRED MODE (opt-in, default
    unchanged): with the environment variable AIQT_ORCH_REQUIRE_REGISTRY set to anything but an explicit
    off value ('', '0', 'false', 'no', 'off', ASCII case-insensitive, matched exactly with nothing
    stripped, so a padded off word reads as ON), an ABSENT registry DENIES instead of
    leaving the guard inert (_orch_registry_required), and so does a registry discovery that cannot be
    evaluated (round 5): the nearest non-absent chain entry, or with none on the chain the git-resolved
    toplevel's entry, that the probe returns as _ORCH_REG_CANNOT_EVALUATE (a `.aiqt` that is a regular
    file, a symlink, or a directory without search (execute) permission, such as mode 0o600 or 0o000 for a
    process those modes bind, while mode 0o100 evaluates normally for its owner where O_PATH exists and for
    a process the mode bits do not bind, and cannot be evaluated by any other process (see
    _orch_dirfd_has_registry); a first present registry name that is not a regular file or
    whose no-follow stat faults) DENIES in that mode, never read as a registry, and so does a git
    toplevel that exists but cannot be opened as a directory (_ORCH_REG_TOPLEVEL_UNOPENABLE, round 6,
    with its own reason naming the toplevel), while by default each keeps the guard ACTIVE exactly as a
    present registry does (a git toplevel that does not exist is absent: inert by default, denied as an
    absent registry in that mode). PRE-SCOPE DENIES, checked BEFORE the
    registry scope and so in every session, orchestrated or not: a tool_name that is missing, null, empty,
    not a string, or carrying a NUL or any other control character; a cwd that is missing, null, empty, or
    not a string; and a string cwd whose registry walk cannot be carried out (a NUL in the path, a path
    that is not an existing directory this process can read and enter, an ancestor directory the walk
    cannot open or examine, or an ancestor chain that changed while the walk read it:
    _orch_registry_walk), each deny naming the defect and an action that repairs it. Only a plain
    non-Bash string tool_name, and a cwd whose completed walk and git-toplevel union find no registry,
    are out of scope (allow; in registry-required mode that cwd is denied instead)."""
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("trkasy")
    if not isinstance(tool_name, str) or not tool_name:
        # A present but empty or non-string tool_name cannot be matched, so it is not read as a non-Bash
        # tool (the wrtscp precedent): it is denied, never allowed out of scope.
        return _deny(
            "AIQT rule trkasy (track-launched-work) (fail-closed): malformed payload: tool_name is {}, not a "
            "non-empty string, so this guard cannot tell whether the call is a Bash call; it is denied "
            "rather than allowed unread (check-fails-closed-on-unreadable)."
            .format("empty" if tool_name == "" else _orch_json_kind(tool_name)),
            "AIQT guardrail: denied a PreToolUse call with an unreadable tool_name (rule trkasy, "
            "fail-closed).")
    if any(ord(ch) < 0x20 or 0x7f <= ord(ch) <= 0x9f for ch in tool_name):
        # A NUL or any other control character (C0, DEL, C1) never appears in a real tool name, so such a
        # name must not read as an ordinary non-Bash tool and take the out-of-scope allow unread (the
        # round-3 finding: a tool_name of "Bash" plus a NUL was allowed silently); it denies pre-scope.
        return _deny(
            "AIQT rule trkasy (track-launched-work) (fail-closed): malformed payload: tool_name contains a "
            "NUL or another control character, so it is not a real tool name and this guard cannot tell "
            "whether the call is a Bash call; it is denied rather than allowed out of scope "
            "(check-fails-closed-on-unreadable). Re-issue the call with the plain tool name.",
            "AIQT guardrail: denied a PreToolUse call whose tool_name carries a control character (rule "
            "trkasy, fail-closed).")
    if tool_name != "Bash":
        return _allow()
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        kind = "missing" if "cwd" not in data else ("empty" if cwd == "" else _orch_json_kind(cwd))
        return _deny(
            "AIQT rule trkasy (track-launched-work) (fail-closed): this Bash call's cwd is {}, not a "
            "non-empty string, so this guard cannot locate the session repository or its orchestration "
            "registry; it is denied rather than allowed unread (check-fails-closed-on-unreadable). Re-issue "
            "the call with a string cwd.".format(kind),
            "AIQT guardrail: denied a Bash call with no readable cwd (rule trkasy, fail-closed).")
    scope, found = _orch_truncation_scope(cwd)
    if scope == "fail":
        detail, fix = found
        return _deny(
            "AIQT rule trkasy (track-launched-work) (fail-closed): this Bash call's cwd {}, so this guard "
            "cannot walk the cwd's ancestor directories for the orchestration registry that scopes it; it "
            "is denied rather than read as out of scope (check-fails-closed-on-unreadable). {}"
            .format(detail, fix),
            "AIQT guardrail: denied a Bash call whose cwd could not be walked for an orchestration "
            "registry (rule trkasy, fail-closed).")
    if scope == "none":
        # No registry on the cwd's ancestor chain and none at a git-resolved toplevel (the UNION, round 4:
        # core.worktree can point the work tree and its registry off the ancestor chain, so
        # _orch_truncation_scope consults that toplevel too). BY DEFAULT git success can only add a deny
        # and a git failure alone never denies (the union leg reads False and the allow stands); in
        # registry-required mode that False is an ABSENT registry, so a git failure hiding a registry
        # reachable only through the git toplevel is denied below, and git success removes that deny.
        if _orch_registry_required():
            # REGISTRY-REQUIRED MODE (opt-in): the adopter set AIQT_ORCH_REQUIRE_REGISTRY, so an
            # absent registry fails closed instead of leaving the guard inert. Default unchanged.
            return _deny(
                "AIQT rule trkasy (track-launched-work) (registry-required mode): "
                "AIQT_ORCH_REQUIRE_REGISTRY is set, so an absent orchestration registry fails closed "
                "instead of leaving this guard inert, and no .aiqt/orchestration.local.json or "
                ".aiqt/orchestration.json was found on this cwd's ancestor chain or at a git-resolved "
                "toplevel. Commit the orchestration registry at the repository root, run from a "
                "directory under the orchestrated tree, or unset AIQT_ORCH_REQUIRE_REGISTRY to "
                "restore the default scoping (inert allow with no registry).",
                "AIQT guardrail: denied a Bash call in registry-required mode with no orchestration "
                "registry found (rule trkasy, fail-closed).")
        return _allow()  # not an orchestrated session: no registry on the chain or at a git toplevel
    if scope == "cannot-evaluate" and _orch_registry_required():
        # REGISTRY-REQUIRED MODE, round 5: the discovery found an entry it can neither rule out nor confirm
        # as a registry. By default that keeps the guard ACTIVE (deny-safe); in this mode a discovery fault
        # must not satisfy the registry requirement, so it denies. Default unchanged.
        return _deny(
            "AIQT rule trkasy (track-launched-work) (registry-required mode): "
            "AIQT_ORCH_REQUIRE_REGISTRY is set, so this guard must confirm an orchestration registry, and "
            "the candidate that decided could not be confirmed as one. That candidate is EITHER the nearest "
            "directory on this cwd's ancestor chain whose registry probe is not a clean not-present, OR, "
            "only when every directory on that chain probes as a clean not-present, the toplevel git "
            "resolves for this cwd, which can lie off the chain (core.worktree): so when every directory "
            "on the chain probes as a clean not-present (no .aiqt entry, or a real .aiqt directory this "
            "process can search holding neither registry name), the fault is at the git toplevel. At that candidate, its .aiqt is not a directory this process can "
            "open without following a symlink and search, or its first present registry name "
            "(orchestration.local.json, then orchestration.json) is not a regular file or cannot be "
            "examined. A registry this guard cannot evaluate is not a registry, so the call is denied "
            "rather than read as registry-present (check-fails-closed-on-unreadable). Make that .aiqt a "
            "real directory with search (execute) permission holding a regular registry file, remove the "
            "stray .aiqt entry, or unset "
            "AIQT_ORCH_REQUIRE_REGISTRY to restore the default scoping (where such an entry keeps this "
            "guard active).",
            "AIQT guardrail: denied a Bash call in registry-required mode whose orchestration registry "
            "could not be evaluated (rule trkasy, fail-closed).")
    if scope == "toplevel-unopenable" and _orch_registry_required():
        # REGISTRY-REQUIRED MODE, round 6: git names a toplevel for this cwd but this process cannot open it
        # as a directory, so its registry entry is never reached. That is a fault in the toplevel itself,
        # not in a .aiqt entry, so it carries its own reason and repair. Default unchanged (in scope).
        return _deny(
            "AIQT rule trkasy (track-launched-work) (registry-required mode): "
            "AIQT_ORCH_REQUIRE_REGISTRY is set, so this guard must confirm an orchestration registry; none "
            "is on this cwd's ancestor chain, and the toplevel git resolves for this cwd could not be "
            "opened as a directory, so its registry could not be looked up. A registry this guard cannot "
            "reach is not a registry, so the call is denied rather than read as registry-present "
            "(check-fails-closed-on-unreadable). Make the git toplevel (core.worktree) an existing "
            "directory this process can open, run from a directory under the orchestrated tree, or unset "
            "AIQT_ORCH_REQUIRE_REGISTRY to restore the default scoping (where such a toplevel keeps this "
            "guard active).",
            "AIQT guardrail: denied a Bash call in registry-required mode whose git toplevel could not be "
            "opened for the orchestration registry (rule trkasy, fail-closed).")
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        kind = "missing" if "tool_input" not in data else _orch_json_kind(tool_input)
        return _deny(
            "AIQT rule trkasy (track-launched-work): this Bash call's tool_input is {}, not a JSON object, "
            "so this guard cannot read its command or its run_in_background flag. A check that cannot read "
            "its input fails closed (check-fails-closed-on-unreadable): it is denied rather than allowed "
            "unread. Re-issue the call with a tool_input object carrying a string command.".format(kind),
            "AIQT guardrail: denied a Bash call whose tool_input is {}, not an object "
            "(fail-closed).".format(kind))
    command = tool_input.get("command")
    rib = tool_input.get("run_in_background", False)
    if not isinstance(rib, bool):
        return _deny(
            "AIQT rule trkasy (track-launched-work): this Bash call's run_in_background is {} ({}), not a "
            "boolean, so this guard cannot tell a background dispatch from a foreground call; it is denied "
            "rather than read as foreground (check-fails-closed-on-unreadable). Re-issue with "
            "run_in_background true or false, or omit it for a foreground call."
            .format(_orch_json_kind(rib), json.dumps(rib, default=str)[:40]),
            "AIQT guardrail: denied a Bash call whose run_in_background is not a boolean (fail-closed).")
    if not rib:
        # Foreground scope is narrow: a plain foreground call returns its output directly and is out of
        # scope, but a bare `&` detaches a child into untracked asynchronous work whose result and failure
        # are then lost. A bare-& detach is never the right way to launch tracked work (the tracked
        # background dispatch is), so it DENIES-and-educates: the caller self-corrects to run_in_background
        # (or waits in the foreground), which is what lets the dispatch ledger record it. An unreadable
        # (non-string) command fails closed; a non-detaching foreground command is out of scope (ALLOW).
        if not isinstance(command, str):
            return _deny(
                "AIQT rule trkasy (track-launched-work): this foreground Bash call's command is {}, not a "
                "string, so this guard cannot scan it for a bare '&' detach; it is denied rather than "
                "allowed unread (check-fails-closed-on-unreadable). Re-issue with a string command."
                .format("missing" if "command" not in tool_input else _orch_json_kind(command)),
                "AIQT guardrail: denied a foreground Bash call with no readable command string "
                "(fail-closed).")
        detach_kind = _orch_foreground_detach_kind(command)
        if detach_kind == "unbalanced":
            return _deny(
                "AIQT rule trkasy (track-launched-work): this foreground command ends with a single or "
                "double quote still open as this guard's scan reads it, so the scan cannot prove that a "
                "later '&' is quoted text rather than a bare detach; it is denied rather than allowed "
                "unread (check-fails-closed-on-unreadable). The open quote may be a real unbalanced quote, "
                "or one the scan misreads: an apostrophe or double quote in a here-document body (scanned "
                "as code even under a quoted delimiter) or an ANSI-C $'...' quote. Balance the quoting, or "
                "write the text to a file and pass the file.",
                "AIQT guardrail: denied a foreground command whose quoting this guard could not read to the "
                "end (rule trkasy, fail-closed).")
        if detach_kind == "detach":
            return _deny(
                "AIQT rule trkasy (track-launched-work): this foreground command detaches a child with a "
                "bare '&', launching asynchronous work this tool call does not track, so its result and "
                "failure would be lost; it is denied. Use the platform's tracked background dispatch "
                "(run_in_background) and collect its completion, or keep the command in the foreground and "
                "wait for it. If the detached result and completion are genuinely not needed, drop the '&' "
                "and run it foreground. If this '&' is not a detach at all (for example a bitwise AND in an "
                "arithmetic expansion, or an '&' in a here-document body, which this scan reads as code "
                "even under a quoted delimiter), write the text to a file and pass the file instead; the "
                "background-dispatch advice applies only to a real detach.",
                "AIQT guardrail: denied a foreground bare-& detach (untracked asynchronous work, rule "
                "trkasy); use the tracked background dispatch or run it in the foreground.")
        return _allow()  # foreground without a bare-& detach operator is out of scope by design
    if not isinstance(command, str) or not command:
        return _deny(
            "AIQT rule trkasy: a background dispatch carried no readable command string; failing closed.",
            "AIQT guardrail: denied an unreadable background dispatch (fail-closed).")
    if _ORCH_PLAIN_COMMAND_RE.fullmatch(command) and not (_ORCH_SHELL_KEYWORDS & set(command.split())):
        return _allow()  # no metacharacter and no reserved word: the full stdout reaches the harness capture
    # ROUND-2 FINDING 9: a background dispatch that pipes a PRODUCER into a TRUNCATING SINK (head/tail)
    # discards the producer's output AND its failure - the pipeline's completion signal binds to the sink's
    # truncated output and the pipeline exit is the sink's (0, absent `pipefail`), so a failing producer reads
    # as a clean, finished run (the exact false-clean vrfdlv/trkasy forbid: "a tracked deliverable whose
    # output is captured only through a truncating filter is not observable completion"). This is a proven
    # hazard, not a merely-unprovable capture, so it DENIES-and-educates (use full durable capture, or keep
    # it foreground) rather than allow-note. Detected lexically: any head/tail stage fed by an upstream `|`
    # producer. A parse error falls through to the unprovable allow-note below (nothing proven).
    try:
        _segs = _segments(command)
    except ValueError:
        _segs = None
    if _segs:
        for _i, (_argv, _sep) in enumerate(_segs):
            if _i == 0:
                continue
            if _segs[_i - 1][1] not in ("|", "|&"):
                continue  # this stage is not fed by a pipe from the previous stage
            # ROUND-7 (codex finding 5): resolve the sink THROUGH command-modifier wrappers (command/env/
            # nice/...), so '| command head' and '| env head' are caught, not just a bare '| head'.
            _cw = _orch_effective_sink_word(_argv)
            if _cw in _ORCH_TRUNCATING_SINKS:
                return _deny(
                    "AIQT rules trkasy/vrfdlv (track-launched-work / verifier-delivery-completeness): this "
                    "background dispatch pipes a producer into a truncating sink ('{}'), so the producer's "
                    "full output AND its exit status are discarded - the completion signal would bind to the "
                    "truncated output and a failing producer would read as a clean, finished run. It is "
                    "denied. Capture the producer's FULL output durably instead: redirect its own stdout to a "
                    "real file (producer > out.log) and read the file, or run it in the foreground and wait; "
                    "never bind a tracked dispatch's completion to a head/tail-truncated view.".format(_cw),
                    "AIQT guardrail: denied a background dispatch piped into a truncating sink ({}) that "
                    "discards the producer's output and failure (rules trkasy/vrfdlv); capture the full "
                    "output to a real file, or run it foreground.".format(_cw))
    return _allow_note(
        "AIQT guardrail (rules trkasy/vrfdlv): this background dispatch uses shell syntax (a pipe, redirect, "
        "separator, quote, expansion, or grouping), so this guard does not parse it to prove the full output "
        "is captured. It is allowed (denying it would block legitimate background dispatches that redirect "
        "or pipe their own output); to be sure the full output is durably captured, redirect the producer's "
        "own stdout to a real file or make a real-file tee the final stage. The post-execution "
        "delivery-marker discipline remains the backstop.")


_ORCH_LOOP_HEADERS = frozenset(("for", "while", "until"))
# A single leading loop reserved word is stripped to reach a construct segment's simple command: `do sleep`
# -> `sleep`, `until curl` -> `curl`. `done` is NOT stripped (it closes the loop, never prefixes a command).
_ORCH_LOOP_BODY_KEYWORDS = frozenset(("for", "while", "until", "do"))
_ORCH_POLL_PROBE_CMDS = frozenset(("gh", "curl"))

# Reserved words that make a loop span un-attributable to the single canonical poll shape: a conditional
# reserved word or a '!' negation, and brace grouping. Any of these appearing raw-unquoted in the loop span
# routes to 'indeterminate' (defer to the truncation guard's generic bare-& deny) rather than a match the walk
# cannot soundly justify.
_ORCH_POLL_FORBIDDEN = frozenset((
    "if", "then", "elif", "else", "fi", "case", "esac", "!", "{", "}"))


def _orch_poll_body_argv(argv):
    """The effective simple-command argv of a loop-construct segment, a single leading loop reserved word
    (for/while/until/do) removed, so the do-segment `["do", "sleep", "10"]` resolves to command word
    `sleep` and the until-header `["until", "curl", ...]` resolves to `curl`. `done` is left in place."""
    if argv and argv[0] in _ORCH_LOOP_BODY_KEYWORDS:
        return argv[1:]
    return argv


def _orch_token_actions_runs(token):
    """True when the token carries an `actions/runs` path component pair (a GitHub Actions run probe), as
    a bare `repos/o/r/actions/runs/1` or inside a URL. Bounded by `/` so `xactions/runsy` does not match."""
    parts = token.split("/")
    return any(parts[k] == "actions" and parts[k + 1] == "runs" for k in range(len(parts) - 1))


def _orch_raw_unquoted_words(raw):
    """The list, in order, of the FULLY-UNQUOTED, unescaped words in a single segment's raw source. A
    segment carries no unquoted separator (a separator ends the segment), so this scans words only, reusing
    the shared _read_word reader (which decides quoting from raw positions and raises on an unbalanced
    quote). A word is reported ONLY when its raw slice equals its decoded text, i.e. it carried NO quote or
    escape anywhere: argv_opaque does not flag plain quoting, so this raw-slice comparison is the sole sound
    signal that a token was written bare. A '#' at a word boundary begins a comment (the rest is not command
    text); a redirect operator and its one following target word are skipped so a target is not counted as a
    word. Returns [] on any scan error, which the caller treats as 'no reserved word here', the safe
    direction (the whole command already lexed cleanly before this is reached, so a valid segment does not
    error)."""
    words = []
    n = len(raw)
    i = 0
    try:
        while i < n:
            c = raw[i]
            if c in " \t":
                i += 1
                continue
            if c == "#":
                break                       # word-boundary comment: the rest is not command text
            if c in _METACHARS:
                # within a segment the only metacharacter is a redirect operator ('<'/'>', or '&>' the
                # lexer left in raw); skip the operator and one following target word so neither pollutes
                # the word list. A separator metacharacter never appears here (it would end the segment).
                _op, _kind, oplen = _match_operator(raw, i, n)
                i += oplen
                while i < n and raw[i] in " \t":
                    i += 1
                if i < n and raw[i] not in _METACHARS and raw[i] != "#":
                    _t, _o, started, _d, _lt, i = _read_word(raw, i, n)
                    if not started:
                        break
                continue
            text, _o, started, _d, _lt, j = _read_word(raw, i, n)
            if not started:
                break
            if raw[i:j] == text:            # no quote or escape anywhere in the word: written bare
                words.append(text)
            i = j
    except ValueError:
        return []
    return words


def _orch_body_cmd(seg):
    """(word, unquoted) for a loop segment's effective command word: basenamed, with a single leading loop
    reserved word (do/for/while/until) stripped as _orch_poll_body_argv does and a leading env-assignment
    prefix skipped, paired with whether that command-word TOKEN was written UNQUOTED in the raw source.
    Soundness of the sleep/probe conjuncts rests on this being the command WORD (an argument that merely
    spells 'sleep'/'gh' is not the command word, so it never fabricates a match); the raw-unquoted gate only
    tightens it further. ('', False) when the segment resolves to no command word."""
    argv = _orch_poll_body_argv(seg.argv)
    if not argv:
        return "", False
    idx = _command_word_index(argv)
    if idx >= len(argv):
        return "", False
    token = argv[idx]
    return token.rsplit("/", 1)[-1], token in _orch_raw_unquoted_words(seg.raw)


def _orch_poll_loop_at(segments, done_idx):
    """Judge the single loop that the clean bare-`&` `done` terminator at done_idx closes, the caller having
    already established that done_idx is that terminator (its raw is an unquoted `done`), is the sole bare-`&`
    detach, and is the command's final operator. Returns 'match' ONLY for an UNAMBIGUOUS single for/while/
    until loop whose body carries a raw-unquoted `sleep` command word and whose construct carries a status
    probe; 'indeterminate' for any structure this segment walk cannot soundly attribute to that ONE loop (a
    nested or extra loop keyword, a conditional reserved word, or brace grouping); and 'none' for a clean
    single loop that simply lacks the sleep or the probe. Loop keywords are counted from the RAW-UNQUOTED
    tokens, never a basenamed or quoted spelling, so an inner header that shares a segment with the outer
    `do` (e.g. `["do","for",...]`) is still counted and forces 'indeterminate' rather than borrowing the
    outer loop's probe."""
    words_by_seg = [_orch_raw_unquoted_words(segments[k].raw) for k in range(done_idx + 1)]
    n_header = sum(w in _ORCH_LOOP_HEADERS for words in words_by_seg for w in words)
    n_do = sum(w == "do" for words in words_by_seg for w in words)
    n_done = sum(w == "done" for words in words_by_seg for w in words)
    if not (n_header == 1 and n_do == 1 and n_done == 1):
        return "indeterminate"              # nested/extra loop keyword, or a keyword the walk cannot place
    header_idx = do_idx = None
    for k, words in enumerate(words_by_seg):
        if words and words[0] in _ORCH_LOOP_HEADERS:
            header_idx = k
        elif words and words[0] == "do":
            do_idx = k
    if header_idx is None or do_idx is None or not (header_idx < do_idx < done_idx):
        return "indeterminate"              # the one header/do is not in command position, or out of order
    for k in range(header_idx, done_idx + 1):
        if any(w in _ORCH_POLL_FORBIDDEN for w in words_by_seg[k]):
            return "indeterminate"          # a conditional reserved word or brace group in the loop span
    has_sleep = False
    for k in range(do_idx, done_idx + 1):
        word, unq = _orch_body_cmd(segments[k])
        if word == "sleep" and unq:
            has_sleep = True
            break
    has_probe = False
    for k in range(header_idx, done_idx + 1):
        word, unq = _orch_body_cmd(segments[k])
        if word in _ORCH_POLL_PROBE_CMDS and unq:
            has_probe = True
            break
        if any(tok == "--watch" or _orch_token_actions_runs(tok) for tok in segments[k].argv):
            has_probe = True
            break
    return "match" if (has_sleep and has_probe) else "none"


def _orch_bg_poll_loop(command):
    """THREE-VALUED classifier over the raw Bash command. 'match' ONLY for the UNAMBIGUOUS canonical shape:
    a SINGLE for/while/until loop closed by a bare-`&` `done` that is the command's FINAL operator, whose
    body carries a raw-unquoted `sleep` command word and whose construct carries a status probe (a raw-
    unquoted `gh`/`curl` command word, a `--watch` token, or an `actions/runs` path token; the probe set is a
    disclosed heuristic, not exhaustive). 'none' on a full parse that is simply not that shape and carries no
    ambiguity to defer: no bare `&`; a bare `&` that does not close a raw-unquoted `done` terminator; or a
    clean single loop that lacks the sleep or the probe. 'indeterminate' whenever the structure cannot be
    soundly attributed to one canonical loop, so the guard defers to the truncation guard's generic bare-`&`
    DENY-and-educate rather than risk a false match:
    an unparseable construct, subshell or C-style `(( ))` grouping, more than one bare `&`, a command
    trailing the bare-`&` `done`, a nested or extra loop keyword, a conditional reserved word, or brace
    grouping.

    Reserved words (for/while/until/do/done) are recognized ONLY as the EXACT raw-unquoted token, never a
    basename or a quoted spelling: because `_command_word` basenames and argv is quote-decoded, a bare `&`
    closing a command whose name was written quoted, or a path such as `/tmp/done`, is NOT read as the loop
    terminator (it classifies 'none'), and an inner loop header sharing a segment with the outer `do` is
    still counted (forcing 'indeterminate').

    The conservative posture is deliberate for a BLOCK guard: a false 'match' strands a session, whereas a
    'none'/'indeterminate' emits nothing and defers to orch_truncation_guard's generic bare-`&`
    DENY-and-educate on the same event. Residuals are disclosed in the manifest."""
    try:
        segments = _lex_command(command)
    except ValueError:
        return "indeterminate"              # heredoc/process-sub/unbalanced-quote/NUL/malformed-redirect
    if any(seg.sep_after in ("(", ")") for seg in segments):
        return "indeterminate"              # subshell or C-style for (( )) grouping: cannot attribute
    bare_amps = [i for i, seg in enumerate(segments) if seg.sep_after == "&"]
    if not bare_amps:
        return "none"                       # no bare-`&` detach at all
    if len(bare_amps) > 1:
        return "indeterminate"              # more than one detach: not the single canonical shape
    amp_idx = bare_amps[0]
    if segments[amp_idx].raw.strip() != "done":
        return "none"                       # the bare-`&` does not close a raw-unquoted `done` terminator
    if any(segments[k].argv for k in range(amp_idx + 1, len(segments))):
        return "indeterminate"              # a command trails the bare-`&` `done`: not the canonical shape
    return _orch_poll_loop_at(segments, amp_idx)


def orch_untracked_wait_loop(data):
    """trkasy, PreToolUse Bash: DENY a command that backgrounds a status-polling loop with a bare `&`. A
    detached child is not a harness-tracked task, so its completion cannot notify this session and the result
    is stranded while the session goes dark waiting on it. Registry-gated, but with a NARROWER scope than
    orch_truncation_guard: it roots via _orch_root (the session cwd's git-resolved toplevel ONLY, no
    ancestor walk and no union) and loads the registry there (_orch_registry), so it is inert where git
    resolves no toplevel or that toplevel has no registry, even where the truncation guard's ancestor walk
    finds one above or beside it. It never reads AIQT_ORCH_REQUIRE_REGISTRY, so registry-required mode does
    not change it: it stays inert wherever _orch_registry reports no registry at the git toplevel, in either
    mode. The truncation guard in that mode denies every call only where its own scope (the ancestor walk
    unioned with the git toplevel, _orch_truncation_scope) finds no registry or cannot confirm one, or where
    its pre-scope checks or its walk fail (those deny in every mode); where that walk finds
    a registry ABOVE a nested repository whose toplevel has none, the guard stays active (a plain call
    allows, a bare-& detach denies) while this component stays inert. NOT lease-gated: a
    bounded worker building a fire-and-forget poll is equally wrong. Fail-open (silent allow) on a non-Bash
    or absent tool, no git toplevel, an absent registry, or a non-string/empty command; a NUL, heredoc,
    unbalanced quote, or subshell-grouped detach classifies 'indeterminate' and emits nothing, deferring to
    the truncation guard's generic bare-`&` DENY-and-educate (or its open-quote deny) on the same event.
    Only a positive 'match' DENIES here. This is a specific companion to that generic deny, defence in
    depth on the same event: the truncation guard denies any bare-`&` detach it can read, this denies the
    specific untracked poll loop with a reason about the stranded poll."""
    if data.get("tool_name") != "Bash":
        return _allow()
    root = _orch_root(data)
    if root is None:
        return _allow()
    status, _reg = _orch_registry(root)
    if status == "absent":
        return _allow()                     # no registry at the git toplevel: inert (the truncation guard's walk may still find one above)
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return _allow()                     # unreadable/empty command: out of scope, fail-open
    if _orch_bg_poll_loop(command) == "match":
        return _deny(
            "AIQT rule trkasy (untracked wait loop): this command backgrounds a polling loop with a bare "
            "'&' (a for/while/until loop carrying sleep plus a status probe). A detached child is not a "
            "harness-tracked task: its completion cannot notify this session, so the result is stranded and "
            "the session goes dark waiting on it. Remove the trailing '&' and take ONE of these exits: (1) "
            "submit the same loop as a tracked background dispatch (run_in_background: true) and collect its "
            "completion; (2) run a bounded foreground watch and act on the observed result (for example: "
            "timeout <seconds> gh pr checks <ref> --watch); (3) schedule a tracked wake that re-invokes this "
            "session to re-check.",
            "AIQT guardrail: denied an untracked background wait loop; a bare-'&' poll cannot notify this "
            "session. Use run_in_background, a bounded foreground watch, or a tracked wake.")
    return _allow()                         # 'none' or 'indeterminate': emit nothing; truncation guard backstops


def orch_dispatch_ledger(data):
    """trkasy/recfst, PostToolUse (recorder, never blocks): append launch rows for background Bash and
    registry-declared dispatch tools, completion rows for TaskOutput reads. A failed write SURFACES
    as a non-blocking systemMessage (nocncl), never a swallowed error."""
    root = _orch_root(data)
    if root is None:
        return _allow()
    status, reg = _orch_registry(root)
    if status != "ok":
        return _allow()
    if not _orch_scope_live(reg, root, data.get("session_id")):
        return _allow()  # only the holder session writes the shared dispatch ledger (CX-M4b)
    tool = data.get("tool_name")
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    response = data.get("tool_response")
    row = None
    if tool == "TaskOutput":
        tid = tool_input.get("task_id") or tool_input.get("taskId")
        if isinstance(tid, str) and tid:
            row = {"event": "complete", "task_id": tid, "tool": tool, "wake": True}
        else:
            # expbnd (explicit-binding-over-ambient-context): an async result with no authoritative
            # identifier is SURFACED as unbound, never correlated to a dispatch by recency or arrival
            # order. Non-blocking (this recorder never blocks), so a genuinely id-less read is flagged
            # rather than silently accepted.
            return _allow_note("AIQT rule expbnd: this TaskOutput carried no task_id, so its "
                               "result is UNBOUND and cannot be tied to a dispatch; correlate it by the "
                               "dispatch's own id, never by which task completed most recently.")
    else:
        dispatch_tools = reg.get("dispatch_tools") if isinstance(
            reg.get("dispatch_tools"), list) else []
        is_dispatch = (tool == "Bash" and tool_input.get("run_in_background") is True) \
            or tool in dispatch_tools
        if is_dispatch:
            tid = None
            observed = False
            if isinstance(response, dict):
                for k in ("task_id", "taskId", "id"):
                    if isinstance(response.get(k), str) and response.get(k):
                        tid = response[k]
                        observed = True
                        break
            if tid is None:
                basis = json.dumps(tool_input, sort_keys=True)
                tid = "disp-" + __import__("hashlib").sha256(
                    (basis + _orch_now().isoformat()).encode("utf-8", "replace")).hexdigest()[:12]
            # CX-M7: only a REAL observed task id has a provable wake route; a synthesized id gets
            # wake=false so it can never be counted as a live task that excuses an idle.
            row = {"event": "launch", "task_id": tid, "tool": tool or "", "wake": observed}
    if row is None:
        return _allow()
    row["ts"] = _orch_now().isoformat()
    path = os.path.join(_orch_state_dir_for_root(root), "dispatch-ledger.jsonl")
    if not _orch_append_jsonl(path, row):
        return _allow_note("AIQT guardrail: the dispatch-ledger write failed; "
                           "the launched work may be invisible to the stop guard.")
    return _allow()


# --- review dispatch pin (vfxcmt) --------------------------------------------------------------------
# PreToolUse Bash, registry scoped. Inert unless the orchestration registry declares a `review_dispatch`
# binding (ORCHESTRATION.md, "The review dispatch binding"). A Bash call that names a declared dispatch
# command must be a provably plain dispatch (_rdp_plain_words), or it is UNVERIFIABLE. For a plain dispatch it
# reads the brief file passed as that command's one brief argument and reconciles the brief's column-0 labels against the repository BEFORE the
# command runs: an explicit review target; for a revision review, a full-length pin that resolves to exactly
# that commit and equals the authoritative task revision the adopter's authority command prints; the
# declared review paths equal to the pin's changed set (base derived from the commit's raw parent headers);
# and no uncommitted state over any declared path, except edits inside a checked-out submodule whose HEAD is
# at the pinned gitlink, which are not read. A refusal is a deny naming its reason; a cannot-evaluate
# is a deny prefixed UNVERIFIABLE: so it stays distinct. Every git probe runs through _review_git, which
# neutralizes replacement refs, grafts, pathspec magic and the commit-graph cache.
_RDP_KEYS = frozenset(("commands", "brief_option", "labels", "authority", "max_brief_bytes"))
_RDP_REQUIRED_KEYS = frozenset(("commands", "brief_option", "labels", "authority"))
_RDP_LABEL_KEYS = frozenset(("target", "revision", "path", "repo", "branch"))
_RDP_AUTHORITY_KEYS = frozenset(("argv", "timeout"))
_RDP_TARGETS = frozenset(("revision", "working-tree", "not-a-review"))
# Line boundaries to Unicode that are not physical newlines (VT, FF, FS, GS, RS, NEL, LINE and PARAGRAPH
# SEPARATOR): a brief carrying one is a cannot-evaluate, because it could hide a label inside another line.
_RDP_NONPHYSICAL = ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")
_RDP_DEFAULT_MAX_BRIEF = 1048576
# The whole decision runs inside one deadline that stays under the platform hook timeout (gen_hooks TIMEOUT,
# 10 seconds), so a slow authority or git probe ends in an UNVERIFIABLE deny, never in a killed hook.
_RDP_BUDGET = 8.0
_RDP_DEFAULT_AUTH_TIMEOUT = 5
_RDP_MAX_AUTH_TIMEOUT = 8
_RDP_GIT_TIMEOUT = 5
_RDP_JOIN_GRACE = 0.25   # how long past the deadline the caller waits for the worker's own verdict
_RDP_DEADLINE = [None]   # the monotonic deadline of the decision in progress, set by _rdp_decide
# Every file the hook reads is opened non-blocking (a FIFO or device returns at once and fails the regular
# file check made on the open descriptor) and never becomes a controlling terminal.
_RDP_OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_CLOEXEC", 0)
_RDP_MAX_LISTED = 20
_RDP_OID_LEN = {"sha1": 40, "sha256": 64}
# The ambient variables that move the repository, index, object store or history git reads. The hook's own
# git probes and the authority scrub every GIT_* variable; a dispatch inherits the session's environment,
# so with any of these set the verifier may read another revision than the one reconciled.
_RDP_AMBIENT_GIT = frozenset((
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_QUARANTINE_PATH", "GIT_NAMESPACE", "GIT_REPLACE_REF_BASE",
    "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE"))


def _rdp_overdue():
    """Whether the decision in progress has run past its deadline."""
    return _RDP_DEADLINE[0] is not None and time.monotonic() >= _RDP_DEADLINE[0]


def _rdp_check_deadline():
    """Raise TimeoutError (an OSError, so every read's handler reports a cannot-evaluate) once the decision
    in progress has run past its deadline. Called before each content read, so a slow or large file ends
    in an UNVERIFIABLE deny, never in an allow given after the budget."""
    if _rdp_overdue():
        raise TimeoutError("the check ran out of its {} second budget while reading".format(_RDP_BUDGET))


def _rdp_time_left(cap):
    """The seconds a subprocess of the decision in progress may take: cap, cut to what is left of the
    decision's deadline; None when the deadline has passed (the caller's cannot-evaluate)."""
    if _RDP_DEADLINE[0] is None:
        return cap
    left = _RDP_DEADLINE[0] - time.monotonic()
    return min(cap, left) if left > 0.05 else None


def _review_git(repo, *args, stdin=None):
    """One isolated git probe for the review dispatch pin: the _isolate_git_env scrub plus no optional
    locks, replacement refs and grafts disabled (so a `git replace --graft` or an info/grafts file cannot
    change the parents the base is derived from), literal pathspecs (a declared path is matched as a literal
    path, never as glob or magic), the commit-graph cache off, and the repository configuration that runs a
    program or caches working-tree state switched off on the command line (core.fsmonitor and
    core.untrackedCache), and the submodule settings that would hide a submodule change or recurse into
    one (diff.ignoreSubmodules, submodule.recurse; a .gitmodules `ignore` value is overridden by each
    diff read's own --ignore-submodules=none). Bytes in and out. Returns the CompletedProcess, or None when git could not be run,
    timed out, or the decision's deadline has passed (the caller's cannot-evaluate)."""
    timeout = _rdp_time_left(_RDP_GIT_TIMEOUT)
    if timeout is None:
        return None
    env = _isolate_git_env(dict(os.environ))
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_GRAFT_FILE"] = os.devnull
    env["GIT_LITERAL_PATHSPECS"] = "1"
    try:
        return subprocess.run(["git", "-C", repo, "-c", "core.commitGraph=false", "-c", "core.fsmonitor=false",
                               "-c", "core.untrackedCache=false", "-c", "diff.ignoreSubmodules=none",
                               "-c", "submodule.recurse=false", *args], input=stdin,
                              capture_output=True, timeout=timeout, env=env)
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


def _rdp_binding(reg):
    """The registry's review dispatch binding: (None, None) when undeclared, ("ok", cfg) when well formed,
    ("bad", detail) otherwise. Strict: an unknown key, a missing required key, or a wrong type is malformed."""
    if "review_dispatch" not in reg:
        return (None, None)
    raw = reg.get("review_dispatch")
    if not isinstance(raw, dict):
        return ("bad", "review_dispatch is {}, not an object".format(_orch_json_kind(raw)))
    unknown = sorted(set(raw) - _RDP_KEYS)
    if unknown:
        return ("bad", "review_dispatch has unknown keys {}".format(unknown))
    missing = sorted(_RDP_REQUIRED_KEYS - set(raw))
    if missing:
        return ("bad", "review_dispatch is missing {}".format(missing))
    commands = raw["commands"]
    if not isinstance(commands, list) or not commands or not all(
            isinstance(c, str) and c and "/" not in c and c.strip() == c for c in commands):
        return ("bad", "review_dispatch.commands is not a non-empty list of command basenames")
    option = raw["brief_option"]
    if not isinstance(option, str) or not option.startswith("-") or len(option) < 2 \
            or any(ch.isspace() or ch == "=" for ch in option):
        return ("bad", "review_dispatch.brief_option is not a single option word")
    labels = raw["labels"]
    if not isinstance(labels, dict) or set(labels) != _RDP_LABEL_KEYS:
        return ("bad", "review_dispatch.labels must declare exactly {}".format(sorted(_RDP_LABEL_KEYS)))
    values = list(labels.values())
    if not all(isinstance(v, str) and v and v.strip() == v and "\n" not in v and "\r" not in v
               for v in values):
        return ("bad", "review_dispatch.labels values must be non-empty single-line strings")
    if len(set(values)) != len(values) or any(
            a != b and a.startswith(b + " ") for a in values for b in values):
        return ("bad", "review_dispatch.labels values must be distinct")
    authority = raw["authority"]
    if not isinstance(authority, dict) or set(authority) - _RDP_AUTHORITY_KEYS or "argv" not in authority:
        return ("bad", "review_dispatch.authority must be an object with argv and an optional timeout")
    argv = authority["argv"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
        return ("bad", "review_dispatch.authority.argv is not a non-empty list of non-empty strings")
    timeout = authority.get("timeout", _RDP_DEFAULT_AUTH_TIMEOUT)
    if type(timeout) is not int or not 0 < timeout <= _RDP_MAX_AUTH_TIMEOUT:
        return ("bad", "review_dispatch.authority.timeout is not an integer from 1 to {}".format(
            _RDP_MAX_AUTH_TIMEOUT))
    max_bytes = raw.get("max_brief_bytes", _RDP_DEFAULT_MAX_BRIEF)
    if type(max_bytes) is not int or not 0 < max_bytes <= 16 * _RDP_DEFAULT_MAX_BRIEF:
        return ("bad", "review_dispatch.max_brief_bytes is not an integer from 1 to {}".format(
            16 * _RDP_DEFAULT_MAX_BRIEF))
    return ("ok", {"commands": frozenset(commands), "brief_option": option, "labels": dict(labels),
                   "argv": list(argv), "timeout": timeout, "max_brief_bytes": max_bytes})


def _rdp_brief_args(args, option):
    """The brief values in a dispatch's arguments: each value after a separate `option` word (None when it
    is the last word) and each `option=VALUE`. A long option also matches each abbreviation of it that a
    parser accepting abbreviations reads as it (`--brie` for `--brief`), and a short option also matches
    its attached form (`-bPATH`), so a second brief written that way is counted, never passed over."""
    names = {option}
    if option.startswith("--"):
        names.update(option[:k] for k in range(3, len(option)))
    out = []
    for k, tok in enumerate(args):
        head, eq, _value = tok.partition("=")
        if tok in names:
            out.append(args[k + 1] if k + 1 < len(args) else None)
        elif eq and head in names:
            out.append(tok[len(head) + 1:])
        elif len(option) == 2 and tok.startswith(option) and len(tok) > 2:
            out.append(tok[2:])
    return out


def _rdp_read_fd(fd, limit):
    """At most limit bytes read from fd, to end of file; TimeoutError once the decision's deadline has
    passed."""
    chunks = []
    size = 0
    while size < limit:
        _rdp_check_deadline()
        chunk = os.read(fd, min(limit - size, 1 << 20))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def _rdp_read_brief(path, max_bytes):
    """(text, None) for a readable brief, or (None, reason) for a cannot-evaluate: missing, a symlink, not a
    regular file, an I/O or permission error, over max_bytes, invalid UTF-8, a NUL, or a non-physical line
    boundary. One leading byte-order mark is dropped, so a brief saved with one still shows its first
    label at column 0. The brief is opened ONCE, non-blocking and without following a final symlink, and the type
    is checked on that open descriptor, so no swap can land between a check and the open, and a FIFO or
    device never blocks the hook."""
    try:
        fd = os.open(path, _RDP_OPEN_FLAGS | os.O_NOFOLLOW)
    except (OSError, ValueError) as exc:
        return (None, "the brief {} cannot be opened ({})".format(path, exc))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return (None, "the brief {} is not a regular file".format(path))
        blob = _rdp_read_fd(fd, max_bytes + 1)
    except OSError as exc:
        return (None, "the brief {} cannot be read ({})".format(path, exc))
    finally:
        os.close(fd)
    if len(blob) > max_bytes:
        return (None, "the brief {} is larger than max_brief_bytes ({})".format(path, max_bytes))
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        return (None, "the brief {} is not valid UTF-8 ({})".format(path, exc))
    if text.startswith("\ufeff"):
        text = text[1:]
    if "\x00" in text:
        return (None, "the brief {} contains a NUL".format(path))
    if any(ch in text for ch in _RDP_NONPHYSICAL):
        return (None, "the brief {} contains a line boundary that is not a physical newline".format(path))
    return (text, None)


def _rdp_labels(text, labels):
    """Map each label key to the list of its values: a label matches only at column 0, as the exact label
    followed by one space, and its value is the rest of that physical line."""
    found = {key: [] for key in labels}
    for line in text.splitlines():
        for key, label in labels.items():
            if line.startswith(label + " "):
                found[key].append(line[len(label) + 1:])
    return found


def _rdp_listing(items):
    """A capped, readable listing of paths for a deny message."""
    shown = [repr(p) for p in items[:_RDP_MAX_LISTED]]
    more = len(items) - len(shown)
    return ", ".join(shown) + (" and {} more".format(more) if more > 0 else "")


def _rdp_git_records(repo, *args):
    """The NUL-separated byte records of a git probe, or None on a failed or timed-out probe."""
    p = _review_git(repo, *args)
    if p is None or p.returncode != 0:
        return None
    return [rec for rec in p.stdout.split(b"\0") if rec]


def _rdp_resolve(repo, name):
    """The commit id name resolves to in repo, or None."""
    p = _review_git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", name + "^{commit}")
    if p is None or p.returncode != 0:
        return None
    return p.stdout.decode("ascii", "replace").strip() or None


def _rdp_blob_id(fmt, header_size, fd):
    """The git object id of a blob whose content is read from fd: header_size bytes exactly, or OSError
    (TimeoutError once the decision's deadline has passed)."""
    h = __import__("hashlib").new(fmt)
    h.update(b"blob %d\0" % header_size)
    size = 0
    while True:
        _rdp_check_deadline()
        chunk = os.read(fd, 1 << 20)
        if not chunk:
            break
        size += len(chunk)
        h.update(chunk)
    if size != header_size:
        raise OSError("the file changed size while it was read")
    return h.hexdigest()


def _rdp_worktree_entry(repo, relpath, fmt):
    """The (mode, object id) git would record for the working-tree entry at relpath (bytes, slash
    separated) under repo, computed here from the raw bytes, so no index flag (assume-unchanged,
    skip-worktree), stat cache, fsmonitor or configured filter takes part. None when nothing git would
    record is there (missing, or under a symlinked or non-directory component); ("dir", "") for a
    directory; ("other", "") for a FIFO, device or socket. Raises OSError when it cannot be read."""
    errno = __import__("errno")
    parts = relpath.split(b"/")
    _rdp_check_deadline()
    dirfd = os.open(repo, _ORCH_O_WALK | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    try:
        for name in parts[:-1]:
            try:
                sub = os.open(name, _ORCH_O_WALK | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                              dir_fd=dirfd)
            except OSError as exc:
                if exc.errno in (errno.ENOENT, errno.ENOTDIR, errno.ELOOP):
                    return None
                raise
            os.close(dirfd)
            dirfd = sub
        try:
            st = os.stat(parts[-1], dir_fd=dirfd, follow_symlinks=False)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                return None
            raise
        if stat.S_ISLNK(st.st_mode):
            target = os.readlink(parts[-1], dir_fd=dirfd)
            h = __import__("hashlib").new(fmt)
            h.update(b"blob %d\0" % len(target) + target)
            return ("120000", h.hexdigest())
        if stat.S_ISDIR(st.st_mode):
            return ("dir", "")
        if not stat.S_ISREG(st.st_mode):
            return ("other", "")
        fd = os.open(parts[-1], _RDP_OPEN_FLAGS | os.O_NOFOLLOW, dir_fd=dirfd)
        try:
            fst = os.fstat(fd)
            if not stat.S_ISREG(fst.st_mode):
                return ("other", "")
            return ("100755" if fst.st_mode & stat.S_IXUSR else "100644", _rdp_blob_id(fmt, fst.st_size, fd))
        finally:
            os.close(fd)
    finally:
        os.close(dirfd)


def _rdp_gitlink_entry(repo, relpath, want):
    """The working-tree state of a declared submodule path whose pinned entry is the gitlink want: want
    itself when the submodule is checked out at that commit or not checked out at all (an empty
    directory, which git also reads as unchanged), otherwise ("other", ""). None when git cannot say."""
    path = os.path.join(repo, os.fsdecode(relpath))
    try:
        if not os.listdir(path):
            return want
    except OSError:
        return ("other", "")
    p = _review_git(path, "rev-parse", "--show-toplevel", "HEAD")
    if p is None:
        return None
    lines = p.stdout.decode("utf-8", "surrogateescape").split("\n")
    if p.returncode != 0 or len(lines) < 2 or os.path.realpath(lines[0]) != os.path.realpath(path):
        return ("other", "")
    return ("160000", lines[1]) if lines[1] == want[1] else ("other", "")


def _rdp_reconcile(cfg, found, root, brief, repo_default, auth_dir=None):
    """Reconcile a revision-target brief against the repository: ("allow"|"note"|"deny"|"unverifiable",
    message). repo_default is the top level of the repository the dispatch runs in, used when the brief
    names no repository. The authority runs from auth_dir, the top level of the worktree whose registry
    binds the dispatch (root when None), so a relative authority argv is the registry's own, never a copy a
    linked or nested worktree supplies. The checks run in a fixed order and the first failure decides."""
    labels = cfg["labels"]
    pins = found["revision"]
    if not pins:
        return ("deny", "the brief targets a revision but has no {!r} line; a branch name or a "
                "description is not a pin, so add the full commit id under review".format(labels["revision"]))
    for key in ("revision", "repo", "branch"):
        if len(found[key]) > 1:
            return ("unverifiable", "the brief has {} {!r} lines; at most one is allowed".format(
                len(found[key]), labels[key]))
    pin = pins[0].strip()
    repo = repo_default
    if found["repo"]:
        repo = found["repo"][0].strip()
        if not os.path.isabs(repo):
            return ("unverifiable", "the {!r} value {!r} is not an absolute path".format(
                labels["repo"], repo))
        p = _review_git(repo, "rev-parse", "--show-toplevel")
        top = p.stdout[:-1].decode("utf-8", "surrogateescape") if p is not None and p.returncode == 0 \
            and p.stdout.endswith(b"\n") else None
        if not top or os.path.realpath(top) != os.path.realpath(repo):
            return ("unverifiable", "the {!r} value {!r} is not a repository top level".format(
                labels["repo"], repo))
    p = _review_git(repo, "rev-parse", "--show-object-format")
    fmt = p.stdout.decode("ascii", "replace").strip() if p is not None and p.returncode == 0 else None
    if fmt not in _RDP_OID_LEN:
        return ("unverifiable", "the object format of {} cannot be read".format(repo))
    width = _RDP_OID_LEN[fmt]
    if len(pin) != width or any(ch not in "0123456789abcdef" for ch in pin):
        return ("deny", "the pin {!r} is not a full {}-character lowercase {} commit id; a short "
                "id, a branch name or HEAD is not accepted".format(pin, width, fmt))
    if _rdp_resolve(repo, pin) != pin:
        return ("unverifiable", "the pin {} does not resolve to that commit in {}".format(pin, repo))
    auth_timeout = _rdp_time_left(cfg["timeout"])
    if auth_timeout is None:
        return ("unverifiable", "the check ran out of its {} second budget before the authority "
                "command".format(_RDP_BUDGET))
    try:
        # The _isolate_git_env scrub, as for every git probe: an ambient GIT_DIR or GIT_WORK_TREE cannot
        # point the authority at another repository.
        auth = subprocess.run(cfg["argv"] + [brief], capture_output=True, text=True, timeout=auth_timeout,
                              cwd=auth_dir or root, env=_isolate_git_env(dict(os.environ)))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return ("unverifiable", "the authority command failed to run ({})".format(exc))
    if auth.returncode != 0:
        return ("unverifiable", "the authority command exited {}".format(auth.returncode))
    out = auth.stdout or ""
    auth_lines = (out[:-1] if out.endswith("\n") else out).split("\n")
    if len(auth_lines) != 1 or len(auth_lines[0]) != width \
            or any(ch not in "0123456789abcdef" for ch in auth_lines[0]):
        return ("unverifiable", "the authority command did not print exactly one full commit id line")
    if auth_lines[0] != pin:
        return ("deny", "the pin {} is not the authoritative task revision ({})".format(
            pin, auth_lines[0]))
    p = _review_git(repo, "cat-file", "commit", pin)
    if p is None or p.returncode != 0:
        return ("unverifiable", "the raw commit {} cannot be read".format(pin))
    parents = []
    for line in p.stdout.split(b"\n"):
        if not line:
            break
        if line.startswith(b"parent "):
            parents.append(line[len(b"parent "):].decode("ascii", "replace"))
    for parent in parents:
        q = _review_git(repo, "cat-file", "-e", parent)
        if q is None or q.returncode != 0:
            return ("unverifiable", "the parent {} of {} is not in the repository (a shallow or "
                    "partial clone), so the base cannot be derived".format(parent, pin))
    if parents:
        base = parents[0]
    else:
        q = _review_git(repo, "hash-object", "-t", "tree", "--stdin", stdin=b"")
        base = q.stdout.decode("ascii", "replace").strip() if q is not None and q.returncode == 0 else ""
        if len(base) != width:
            return ("unverifiable", "the empty tree id cannot be computed in {}".format(repo))
    changed = _rdp_git_records(repo, "diff-tree", "-r", "-z", "--name-only", "--no-renames",
                               "--ignore-submodules=none", "--no-ext-diff", "--no-textconv", base, pin)
    if changed is None:
        return ("unverifiable", "the changed set of {} cannot be read".format(pin))
    declared = found["path"]
    if not declared:
        return ("deny", "the brief declares no {!r} line; list every path the review covers".format(
            labels["path"]))
    if not all(declared):
        return ("unverifiable", "the brief has an empty {!r} value".format(labels["path"]))
    declared_b = set(d.encode("utf-8") for d in declared)
    changed_b = set(changed)
    if declared_b != changed_b:
        parts = []
        missing = sorted(c.decode("utf-8", "replace") for c in changed_b - declared_b)
        extra = sorted(d.decode("utf-8", "replace") for d in declared_b - changed_b)
        if missing:
            parts.append("changed by {} but not declared: {}".format(pin, _rdp_listing(missing)))
        if extra:
            parts.append("declared but not changed by {}: {}".format(pin, _rdp_listing(extra)))
        return ("deny", "the declared review paths do not match the commit's changed set ({})".format(
            "; ".join(parts)))
    paths = sorted(declared)
    staged = _rdp_git_records(repo, "diff-index", "--cached", "-z", "--name-only", "--ignore-submodules=none",
                              pin, "--", *paths)
    if staged is None:
        return ("unverifiable", "the staged state of the declared paths cannot be read")
    if staged:
        return ("deny", "declared path {} is staged against the pin (checked in {}); commit it, or set {!r} "
                "to a worktree checked out at the pin".format(
                    _rdp_listing(sorted(d.decode("utf-8", "replace") for d in staged)), repo, labels["repo"]))
    # The working tree is compared with the pin BY CONTENT: each declared path's bytes (or symlink target)
    # are hashed here and compared with the pin's tree entry, so an assume-unchanged or skip-worktree flag,
    # core.ignoreStat, a stat cache, an fsmonitor answer or a configured filter cannot report a changed
    # file as clean. A path the pin deletes must be absent.
    tree = _rdp_git_records(repo, "ls-tree", "-r", "-z", "--full-tree", pin)
    if tree is None:
        return ("unverifiable", "the tree of {} cannot be read".format(pin))
    entries = {}
    for rec in tree:
        meta, tab, name = rec.partition(b"\t")
        fields = meta.split(b" ")
        if not tab or len(fields) != 3:
            return ("unverifiable", "the tree of {} cannot be parsed".format(pin))
        entries[name] = (fields[0].decode("ascii", "replace"), fields[2].decode("ascii", "replace"))
    # With core.fileMode false git itself ignores the executable bit of a working-tree file, so a file
    # whose content matches the pin and whose mode differs only in that bit is clean, as git status says.
    p = _review_git(repo, "config", "--bool", "core.fileMode")
    exec_bit_ignored = p is not None and p.returncode == 0 and p.stdout.strip() == b"false"
    differ = []
    for path in paths:
        key = path.encode("utf-8")
        want = entries.get(key)
        try:
            got = _rdp_worktree_entry(repo, key, fmt)
            if want is not None and want[0] == "160000" and got == ("dir", ""):
                got = _rdp_gitlink_entry(repo, key, want)
                if got is None:
                    return ("unverifiable", "the submodule at declared path {!r} cannot be read".format(path))
            elif want is None and got == ("dir", "") and any(name.startswith(key + b"/") for name in entries):
                # A file the pin replaces with a directory of its own entries: the path holds no file, as
                # the pin says; the entries under it are compared as declared paths of their own.
                got = None
        except (OSError, ValueError) as exc:
            return ("unverifiable", "the working-tree entry of declared path {!r} cannot be read ({})".format(
                path, exc))
        if exec_bit_ignored and want is not None and got is not None and want[1] == got[1] \
                and {want[0], got[0]} == {"100644", "100755"}:
            got = want
        if got != want:
            differ.append(path)
    if differ:
        return ("deny", "declared path {} differs from the pin in the working tree (compared by content in "
                "{}); commit it, or set {!r} to a worktree checked out at the pin".format(
                    _rdp_listing(differ), repo, labels["repo"]))
    if found["branch"]:
        branch = found["branch"][0].strip()
        tip = _rdp_resolve(repo, branch) if branch else None
        if tip != pin:
            return ("note", "AIQT rule vfxcmt: the review dispatch is pinned to {}, but the declared "
                    "branch {!r} {}; the review covers the pin, not the branch".format(
                        pin, branch, "resolves to {}".format(tip) if tip else "does not resolve"))
    return ("allow", "")


# The shared "provably plain" command specification, decided on the raw characters before any lexing. A
# command is provably plain only when ALL hold: (1) every character is printable ASCII, space to tilde (no
# tab, newline, carriage return, NUL or non-ASCII character, so no Unicode digit, homoglyph or invisible
# character); (2) no character of _RDP_PLAIN_FORBIDDEN appears outside a single-quoted segment; (3) a
# single-quoted segment is literal, a double-quoted segment holds no character of _RDP_PLAIN_FORBIDDEN, and
# an unterminated quote is not plain; (4) words are separated by spaces only, and the first word, the
# command, is a bare name of _RDP_COMMAND_WORD_CHARS (so it is unquoted and no assignment leads), or an
# absolute path whose directory is one of _RDP_PLAIN_DIRS, naming a program of the allowlist
# _RDP_PLAIN_COMMANDS (or, for this hook, a declared dispatch command, which the pin check judges). Rules 1
# and 2 exclude every expansion, substitution, glob, redirection, operator, comment and escape, so a plain
# command is exactly one simple command whose words are its literal text.
_RDP_PLAIN_FORBIDDEN = frozenset("$`\\;&|<>(){}[]*?!#~")
_RDP_COMMAND_WORD_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./-")
# Rule 4's allowlist of command words, compared exactly: git and opf, which can run other programs and so
# are judged by each hook's own semantic check, and programs that cannot run another program. A list of
# the programs that do run others was bypassed by a versioned interpreter path, so every other command
# word (a shell, an interpreter at any version, awk, sed, find, xargs, env, tar, make, sort, whose
# --compress-program runs a program, an editor, a wrapper) is not plain. Membership never makes a command
# safe by itself.
_RDP_PLAIN_COMMANDS = frozenset((
    "git", "opf", "ls", "cat", "echo", "printf", "pwd", "true", "false", "test", "head", "tail", "wc", "grep",
    "egrep", "fgrep", "diff", "cmp", "stat", "du", "df", "date", "basename", "dirname", "realpath", "readlink",
    "uniq", "cut", "tr", "mkdir", "rmdir", "touch", "cp", "mv", "rm", "ln", "chmod"))
# The only directories a command word written as a path may name; any other path (relative, ./ls,
# /tmp/x/ls) is not plain.
_RDP_PLAIN_DIRS = frozenset(("/usr/bin", "/bin", "/usr/local/bin", "/usr/sbin"))
# The shared vector table every implementation of the specification carries as self-test rows: (command,
# whether it is provably plain). `git log --output=f` is plain: an output option is each hook's own
# semantic check, not lexing.
_RDP_PLAIN_VECTORS = (
    ("git status", True), ("git commit -m 'fix: a; b'", True), ("ls -la docs/x.md", True),
    ("opf record --type finding", True), ("git log --output=f", True),
    ("git status; rm x", False), ("git $'re\\x00set'", False), ('git commit -m "$(id)"', False),
    ("python3 -c 'print(1)'", False), ("env GIT_DIR=x git log", False), ("GIT_DIR=x git log", False),
    ("git st*", False), ("git log \\\n", False), ("git log \u0661", False), ("bash -c 'x'", False),
    ("xargs git reset", False), ("/usr/bin/python3.14 -c 'x'", False), ("python3.14 -c 'x'", False),
    ("awk -f p", False), ("sed -n p f", False), ("find . -delete", False), ("tar -xf a", False),
    ("sort -S 4K --compress-program=bash f", False), ("./ls", False), ("/tmp/x/ls", False),
    ("nodejs -e x", False), ("/usr/bin/ls docs", True), ("rm notes.txt", True))


def _rdp_basename(word):
    """The command name a literal command word runs: its last path component."""
    return word.rsplit("/", 1)[-1]


def _rdp_plain_command(word, declared=()):
    """Whether the command word word is plain under rule 4: a bare name, or a path whose directory is one of
    _RDP_PLAIN_DIRS, naming a program of _RDP_PLAIN_COMMANDS (compared exactly) or one of the dispatch
    commands declared (compared without regard to case)."""
    directory, slash, name = word.rpartition("/")
    if slash and directory not in _RDP_PLAIN_DIRS:
        return False
    return name in _RDP_PLAIN_COMMANDS or name.casefold() in {d.casefold() for d in declared}


def _rdp_plain_words(command, declared=()):
    """(words, None) when command is provably plain under the shared specification above, words being the
    literal word values with the quotes removed; otherwise (None, reason). The verdict is taken on the raw
    characters: nothing is decoded and no escape or expansion is interpreted, and anything the
    specification does not name as plain is not plain. declared names the dispatch commands this hook
    also admits as command words, since the pin check is their semantic check."""
    if not isinstance(command, str):
        return (None, "the command is not a string")
    for ch in command:
        if not " " <= ch <= "~":
            return (None, "the character {!r} is not printable ASCII".format(ch))
    words = []
    word = None
    i = 0
    n = len(command)
    while i < n:
        c = command[i]
        if c == " ":
            if word is not None:
                words.append(word)
                word = None
            i += 1
            continue
        if c in "'\"":
            j = command.find(c, i + 1)
            if j < 0:
                return (None, "an unterminated quote")
            body = command[i + 1:j]
            bad = [ch for ch in body if ch in _RDP_PLAIN_FORBIDDEN]
            if c == '"' and bad:
                return (None, "a double-quoted segment holding {!r}".format(bad[0]))
            word = (word or "") + body
            i = j + 1
            continue
        if c in _RDP_PLAIN_FORBIDDEN:
            return (None, "the character {!r}".format(c))
        word = (word or "") + c
        i += 1
    if word is not None:
        words.append(word)
    head = command.lstrip(" ").split(" ", 1)[0]
    if not head or any(ch not in _RDP_COMMAND_WORD_CHARS for ch in head):
        return (None, "a command word that is not a bare name or path")
    if not _rdp_plain_command(head, declared):
        return (None, "the command word {!r} is not on the allowlist of plain programs".format(head))
    return (words, None)


def _rdp_mentions(command, commands):
    """The declared commands a command could name once bash removes its quoting, compared without regard to
    case: each name found in the raw text, or in the raw text with every quote, backslash, line
    continuation, dollar sign, brace and comma deleted (so a quote-split name, a brace list holding the
    name, and a variable set to the name and expanded all name it). Character deletions only: no quoting
    is decoded. An over-approximation: a mention in prose is found too."""
    flat = command.replace("\\\n", "")
    for ch in "'\"\\$,{}":
        flat = flat.replace(ch, "")
    texts = (command.casefold(), flat.casefold())
    return sorted(name for name in commands if any(name.casefold() in t for t in texts))


def _rdp_registry_dir(cwd):
    """Every directory carrying a registry on cwd's ancestors, nearest first (the resolved path, then the
    path as written, so a cwd that no longer exists still finds a registry above it), found with the
    truncation guard's no-follow registry probe: ("found", [dir, ...]), the list empty when no ancestor
    carries one, or ("fail", detail) when the walk cannot be made. Every registry is listed, not only the
    nearest, so a registry without a binding cannot hide one above it."""
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or "\x00" in cwd:
        return ("fail", "the session cwd {!r} is not an absolute path".format(cwd))
    chains = []
    for start in (os.path.realpath(cwd), os.path.normpath(cwd)):
        chain = [start]
        while os.path.dirname(chain[-1]) != chain[-1]:
            chain.append(os.path.dirname(chain[-1]))
        if chain not in chains:
            chains.append(chain)
    found = []
    for chain in chains:
        for path in chain:
            if path in found:
                continue
            try:
                fd = os.open(path, _ORCH_O_WALK | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as exc:
                return ("fail", "the ancestor {} cannot be opened ({})".format(path, exc))
            try:
                if _orch_dirfd_has_registry(fd):
                    found.append(path)
            finally:
                os.close(fd)
    return ("found", found)


def _rdp_withhold(tool_input, detail):
    """The policy for a registry state that cannot say which commands dispatch (a malformed registry or
    binding, a registry or main worktree that cannot be read or located): every Bash call, foreground or
    background, is withheld as UNVERIFIABLE, since any of them may be a dispatch. The registry is frozen
    against the assistant's own file tools too, so an operator repairs it."""
    return ("unverifiable", "{}; every Bash call is withheld until an operator repairs it".format(detail))


def _rdp_decide(data):
    """The review dispatch decision for one Bash payload: ("allow"|"note"|"deny"|"unverifiable", message).
    The whole decision runs inside the _RDP_BUDGET deadline, and in a worker thread the caller waits for
    only until that deadline: a read or probe blocked in the kernel (a slow or hung filesystem, a git or
    authority child) cannot hold the hook past it, and the decision is then UNVERIFIABLE. Each read also
    checks the deadline itself, and the worker stamps the moment it finished, so a decision that finishes
    late never allows, even one that finished after this caller cleared the shared deadline. A crash in the
    worker is raised here, so it still reaches main's fail-closed exit."""
    box = []
    clock = time.monotonic

    def _work():
        try:
            out = (True, _rdp_decide_within(data))
        except BaseException as exc:   # handed to the caller, which raises it
            out = (False, exc)
        box.append(out + (clock(),))
    deadline = clock() + _RDP_BUDGET
    _RDP_DEADLINE[0] = deadline
    try:
        worker = __import__("threading").Thread(target=_work, name="review-dispatch-pin", daemon=True)
        worker.start()
        worker.join(max(0.0, deadline - clock()) + _RDP_JOIN_GRACE)
    finally:
        _RDP_DEADLINE[0] = None
    if not box:
        return ("unverifiable", "the check did not finish within its {} second budget".format(_RDP_BUDGET))
    ok, value, finished = box[0]
    if not ok:
        raise value
    if value[0] in ("allow", "note") and finished >= deadline:
        return ("unverifiable", "the check finished past its {} second budget, so its result is not "
                "used".format(_RDP_BUDGET))
    return value


def _rdp_common_main(common):
    """For a linked worktree whose common git directory is common (an absolute path): (the main worktree's
    top level, None); (None, None) when common is a bare repository (core.bare true and no core.worktree),
    which has no main worktree and so no registry the search could miss; (None, detail) when the main
    worktree cannot be located (a separate git directory whose configuration names no core.worktree, or a
    configuration git cannot read)."""
    if os.path.basename(common) == ".git":
        return (os.path.dirname(common), None)
    config = os.path.join(common, "config")
    q = _review_git(common, "config", "--file", config, "--get", "core.worktree")
    if q is not None and q.returncode == 0:
        top = q.stdout.decode("utf-8", "surrogateescape").rstrip("\n")
        if top:
            return (os.path.normpath(os.path.join(common, top)), None)
    q = _review_git(common, "config", "--file", config, "--bool", "--get", "core.bare")
    if q is not None and q.returncode == 0 and q.stdout.strip() == b"true":
        return (None, None)
    return (None, "the session is a linked worktree of the git directory {}, whose main worktree cannot be "
            "located".format(common))


def _rdp_main_worktree(root):
    """For a linked worktree at root: (the main worktree's top level, None), or (None, detail) when root is
    a linked worktree whose main worktree cannot be located (_rdp_common_main). (None, None) when root is
    not a linked worktree, or is one of a bare repository. Raises OSError when git cannot say. The paths
    are asked for without --path-format (git before 2.31 does not know it and echoes it back as if it were
    a path) and resolved against root, the directory the probe runs in."""
    p = _review_git(root, "rev-parse", "--git-common-dir", "--git-dir")
    if p is None or p.returncode != 0:
        raise OSError("git cannot read the common git directory of {}".format(root))
    lines = p.stdout.decode("utf-8", "surrogateescape").split("\n")
    if len(lines) < 3 or not all(line and not line.startswith("-") for line in lines[:2]):
        raise OSError("git printed no common git directory for {}".format(root))
    common = os.path.normpath(os.path.join(root, lines[0]))
    if os.path.realpath(common) == os.path.realpath(os.path.join(root, lines[1])):
        return (None, None)
    return _rdp_common_main(common)


def _rdp_read_small(path):
    """The text of a small regular file read without following a final symlink and without blocking, or
    None when it is absent, not a regular file, unreadable or over 4096 bytes."""
    try:
        fd = os.open(path, _RDP_OPEN_FLAGS | os.O_NOFOLLOW)
    except (OSError, ValueError):
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        blob = _rdp_read_fd(fd, 4097)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(blob) > 4096:
        return None
    return blob.decode("utf-8", "surrogateescape")


def _rdp_gitfile_main(cwd):
    """Where git cannot be run or answer (a broken shared configuration, a failed or timed-out probe): the
    main worktree of the linked worktree holding cwd, read from the raw files git itself reads (the
    worktree's .git file names its git directory, whose commondir file names the common directory).
    (top, None) for a linked worktree whose main worktree is located; (None, None) when cwd is not in a
    linked worktree (the nearest .git is a directory, or a .git file whose git directory has no commondir,
    as a submodule's has not, or there is no .git at all) or is in one of a bare repository; (None, detail)
    when the raw files cannot say (an unreadable .git or commondir file, or a common directory whose main
    worktree cannot be located), which the caller withholds as a cannot-evaluate. A git directory that is
    gone but whose path is a main worktree's .git/worktrees/NAME still names that main worktree."""
    path = os.path.realpath(cwd)
    while True:
        dotgit = os.path.join(path, ".git")
        try:
            st = os.lstat(dotgit)
        except (FileNotFoundError, NotADirectoryError):
            st = None
        except OSError as exc:
            return (None, "the git metadata {} cannot be examined ({})".format(dotgit, exc))
        if st is not None:
            if stat.S_ISDIR(st.st_mode):
                return (None, None)
            text = _rdp_read_small(dotgit) if stat.S_ISREG(st.st_mode) else None
            if text is None or not text.startswith("gitdir: ") or not text[len("gitdir: "):].strip():
                return (None, "the git file {} cannot be read as a gitdir pointer".format(dotgit))
            gitdir = os.path.normpath(os.path.join(path, text[len("gitdir: "):].strip()))
            holder = os.path.dirname(gitdir)
            named = os.path.dirname(holder) if os.path.basename(holder) == "worktrees" and \
                os.path.basename(os.path.dirname(holder)) == ".git" else None
            try:
                os.lstat(os.path.join(gitdir, "commondir"))
            except (FileNotFoundError, NotADirectoryError):
                return (os.path.dirname(named), None) if named else (None, None)
            except OSError as exc:
                return (None, "the git directory {} cannot be examined ({})".format(gitdir, exc))
            common = _rdp_read_small(os.path.join(gitdir, "commondir"))
            if common is None or not common.strip():
                return (None, "the commondir file of {} cannot be read".format(gitdir))
            return _rdp_common_main(os.path.normpath(os.path.join(gitdir, common.strip())))
        parent = os.path.dirname(path)
        if parent == path:
            return (None, None)
        path = parent


def _rdp_raw_common(cwd):
    """Where git cannot resolve the session repository: the git directory a bound session protects
    (_rdp_names_common_dir), read from the raw files git itself reads on cwd's ancestors (the nearest .git
    directory, or the git directory a .git file names, or the common directory its commondir file names).
    (common, None) with common resolved; (None, None) when no ancestor carries .git, so there is no
    repository to protect; (None, detail) when the raw files cannot say, which refuses every command but a
    read."""
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or "\x00" in cwd:
        return (None, "the session cwd {!r} is not an absolute path".format(cwd))
    path = os.path.realpath(cwd)
    while True:
        dotgit = os.path.join(path, ".git")
        try:
            st = os.lstat(dotgit)
        except (FileNotFoundError, NotADirectoryError):
            st = None
        except OSError as exc:
            return (None, "the git metadata {} cannot be examined ({})".format(dotgit, exc))
        if st is not None:
            if stat.S_ISDIR(st.st_mode):
                return (os.path.realpath(dotgit), None)
            text = _rdp_read_small(dotgit) if stat.S_ISREG(st.st_mode) else None
            if text is None or not text.startswith("gitdir: ") or not text[len("gitdir: "):].strip():
                return (None, "the git file {} cannot be read as a gitdir pointer".format(dotgit))
            gitdir = os.path.normpath(os.path.join(path, text[len("gitdir: "):].strip()))
            common = _rdp_read_small(os.path.join(gitdir, "commondir"))
            if common is not None and common.strip():
                gitdir = os.path.normpath(os.path.join(gitdir, common.strip()))
            return (os.path.realpath(gitdir), None)
        parent = os.path.dirname(path)
        if parent == path:
            return (None, None)
        path = parent


def _rdp_decide_within(data):
    """_rdp_decide's body, run with the deadline set. The scope is the UNION of every way to the registry:
    the git-resolved top level, the main worktree of a linked worktree (through git, or through the raw
    .git and commondir files where git cannot run), and the cwd's ancestors. Git success can only ADD a
    binding, never remove one, so a repository configuration (core.worktree, a separate git directory) or a
    broken shared configuration cannot turn the check off, and a registry WITHOUT a binding never ends the
    search: every registry of the session repository and its main worktree is read, then every registry on
    the cwd's ancestors, nearest first. A binding found anywhere but the session repository's own registry
    (or its main worktree's) is FOREIGN: a dispatch under it is a cannot-evaluate, since the nested or
    redirected repository it would be checked in is not the one the registry binds. The registries on the
    cwd's ancestors are always read: where they bind and the git-resolved registries bind too, from a
    different directory (core.worktree moved the top level onto another bound tree, say), the cwd's binding
    still governs and the conflict is a cannot-evaluate: every command either binding would dispatch is
    withheld, never checked against the other repository, and every other command is judged under both.
    Each registry is read ONCE per check (one snapshot): the record comparison and the enforcement use that
    same read, and where an own registry has a record, the recorded binding is the one enforced."""
    cwd = data.get("cwd")
    tool_input = data.get("tool_input")
    root = _orch_root(data)
    withheld = None
    own = []
    snapshot = {}

    def scope(reg_dir):
        # The check's single read of the registries at reg_dir: a registry removed or rewritten after it
        # was compared with its record cannot change the binding this check enforces.
        if reg_dir not in snapshot:
            snapshot[reg_dir] = _rdp_scope(reg_dir)
        return snapshot[reg_dir]
    if root is not None:
        # The common git directory is resolved once: it holds the binding records and is the directory a
        # bound session protects. Where git cannot name it, no record can be read, so every Bash call is
        # withheld rather than judged as if the registry had never been bound.
        common, identity, why_common = _rdp_record_home(root)
        if common is None:
            return _rdp_withhold(tool_input, "{}, so the review dispatch binding record kept there cannot be "
                                 "read and a registry removed since it was recorded could not be seen".format(
                                     why_common))
        guard = (common, None)
        own.append(root)
        try:
            main_top, why = _rdp_main_worktree(root)
        except OSError as exc:
            # A failed or timed-out probe: the raw .git and commondir files still name the main worktree
            # of a linked worktree, or show root is not one; only where they cannot say is the scope unknown.
            main_top, why = _rdp_gitfile_main(root)
            why = "{}, and {}".format(exc, why) if why else None
        if main_top is not None:
            own.append(main_top)
        withheld = why
    elif isinstance(cwd, str) and os.path.isabs(cwd):
        # git cannot resolve the session repository: a linked worktree whose main worktree the raw files
        # cannot locate is a scope that cannot be known, withheld as a failed probe is.
        main_top, withheld = _rdp_gitfile_main(cwd)
        if main_top is not None:
            own.append(main_top)
    if root is None:
        # No record is read where git cannot resolve the session repository; the git directory the raw
        # files name is still protected, and one they cannot locate refuses every command but a read.
        guard = _rdp_raw_common(cwd)
    # Each own registry's binding is compared with its record (a binding seen first is recorded), so a
    # registry removed or rewritten since withholds every dispatch instead of switching the check off. The
    # main worktree's registry is keyed as the main worktree's own top level, so every worktree shares it.
    keyed = [(reg_dir, identity if reg_dir == root else ".", ".") for reg_dir in own]
    drift, recorded = (None, []) if root is None else _rdp_record_check(
        os.path.join(common, *_RDP_RECORD_DIR), keyed, scope)
    if drift == "withhold":
        return _rdp_withhold(tool_input, recorded)
    resolved = None
    for reg_dir in own:
        # A recorded binding is the one enforced; it matches the snapshot here, since a difference is a drift.
        rcfg = [c for c, d in recorded if d == reg_dir]
        binding, cfg = ("ok", rcfg[0]) if rcfg else scope(reg_dir)
        if binding is not None:
            resolved = (binding, cfg, reg_dir)
            break
    # The registries on cwd's ancestors are read whatever git resolved: git cannot resolve the session's top
    # level (no repository, a broken configuration, a refused ownership check, a deleted cwd), or resolves
    # one without a binding (a worktree or repository nested inside an orchestrated tree, or a top level
    # moved off the cwd's ancestors by core.worktree), or resolves one whose binding is not the cwd's own
    # (a top level moved onto another bound tree). None of these is proof of the session's scope: the
    # nearest binding on the cwd's ancestors governs.
    where, found = _rdp_registry_dir(cwd)
    if where == "fail":
        return _rdp_withhold(tool_input, "the session repository cannot be discovered and " + found)
    near = None
    for found_dir in found:
        binding, cfg = scope(found_dir)
        if binding is not None:
            near = (binding, cfg, found_dir)
            break
    if drift:
        # A registry changed since its binding was recorded: every dispatch a recorded or a live binding
        # declares is withheld, naming the change, and every other command is judged under each binding.
        judged = [("ok", rcfg, rdir) for rcfg, rdir in recorded] + [b for b in (near, resolved) if b is not None]
        for binding, cfg, reg_dir in judged:
            result = _rdp_bound(data, tool_input, binding, cfg, root, reg_dir, drift, guard)
            if result[0] != "allow":
                return result
        return ("allow", "")
    if near is not None and resolved is not None and \
            os.path.realpath(near[2]) != os.path.realpath(resolved[2]):
        conflict = ("the registry above the session cwd ({}) and the registry of the repository git resolves "
                    "({}) both bind review dispatch and are not the same registry, so the binding that governs "
                    "this dispatch, and the repository it is checked in, cannot be known".format(
                        near[2], resolved[2]))
        for binding, cfg, reg_dir in (near, resolved):
            result = _rdp_bound(data, tool_input, binding, cfg, root, reg_dir, conflict, guard)
            if result[0] != "allow":
                return result
        return ("allow", "")
    if resolved is not None:
        return _rdp_bound(data, tool_input, resolved[0], resolved[1], root, resolved[2], False, guard)
    if near is not None:
        foreign = root is not None and os.path.realpath(near[2]) != os.path.realpath(root)
        return _rdp_bound(data, tool_input, near[0], near[1], root, near[2], foreign, guard)
    if withheld:
        return _rdp_withhold(tool_input, withheld)
    return ("allow", "")


def _rdp_scope(reg_dir):
    """The binding of the registries at reg_dir as _rdp_binding gives it: (None, None) when there is no
    registry or none declares a binding, so the search goes on; ("bad", detail) for a registry that cannot
    be read or parsed; ("ok", cfg) for a well-formed binding. Each registry file is read on its own, the
    local one first, so a local registry WITHOUT a binding cannot hide the committed one's binding, as its
    whole-file precedence would for the other orchestration hooks."""
    for rel in _ORCH_REGISTRY_FILES:
        status, reg = _orch_registry(reg_dir, nofollow=True, files=(rel,))
        if status == "absent":
            continue
        if status != "ok":
            return ("bad", reg)
        binding, cfg = _rdp_binding(reg)
        if binding is not None:
            return (binding, cfg)
    return (None, None)


# The binding record. Predicting what a git command or a script does to the registry does not converge, so
# the defence is inverted: when the hook first sees a registry of the session repository (its top level, or
# its main worktree's) bind review dispatch, it records the binding in hook-owned state outside the work
# tree, in the repository's resolved common git directory (GIT_COMMON_DIR/aiqt/review-dispatch-binding/
# KEY.json, KEY the sha256 of the worktree's identity and the registry's path relative to its top level, so
# the record depends on no absolute path and moves with the repository; mode 0600, written without following
# a symbolic link). No git subcommand on the allowlist writes there (they write the work tree, the index,
# refs, the object store and fixed files), and a plain command whose operand resolves to that directory,
# inside it or (for a program other than git) to a directory holding it is refused in a bound session,
# whatever its spelling (_rdp_names_common_dir). From then on a registry that is missing, unreadable, without a binding, or binding
# differently from its record withholds every dispatch as UNVERIFIABLE until an operator restores the
# recorded binding or removes the record, so removing or rewriting the registry can never switch the check
# off; it can only block dispatch.
_RDP_RECORD_DIR = ("aiqt", "review-dispatch-binding")
_RDP_RECORD_MAX = 65536
_RDP_RECORD_VERSION = 2


def _rdp_binding_doc(cfg):
    """A well-formed binding (_rdp_binding's cfg) as the registry spells it, every default made explicit:
    the form a record stores, which _rdp_binding reads back to the same cfg."""
    return dict(commands=sorted(cfg["commands"]), brief_option=cfg["brief_option"],
                labels=dict(sorted(cfg["labels"].items())),
                authority=dict(argv=list(cfg["argv"]), timeout=cfg["timeout"]),
                max_brief_bytes=cfg["max_brief_bytes"])


def _rdp_binding_digest(cfg):
    """The sha256 of a binding's canonical JSON form (_rdp_binding_doc), so two registries spelling the same
    binding differently (key order, a default left out) have one digest."""
    blob = json.dumps(_rdp_binding_doc(cfg), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return __import__("hashlib").sha256(blob.encode("ascii")).hexdigest()


def _rdp_record_home(root):
    """The repository at root as its binding records know it: (common, identity, None), common the RESOLVED
    common git directory (every symbolic link followed; it holds the records and is the directory a bound
    session protects, _rdp_names_common_dir) and identity the worktree's git directory relative to it ("."
    for the main worktree, worktrees/NAME for a linked one); (None, None, detail) when git cannot name
    them, which the caller withholds, since a record it cannot find cannot be compared. Asked once per
    check, without --path-format (git before 2.31 does not know it) and resolved against root; git before
    2.5 echoes the --git-common-dir it does not know, and has no linked worktree, so its git directory is
    the common one."""
    p = _review_git(root, "rev-parse", "--git-common-dir", "--git-dir")
    if p is None or p.returncode != 0:
        return (None, None, "git cannot name the common git directory of {}".format(root))
    lines = p.stdout.decode("utf-8", "surrogateescape").split("\n")
    if len(lines) >= 2 and lines[0] == "--git-common-dir":
        lines = lines[1:2] + lines[1:]
    if len(lines) < 3 or not all(line and not line.startswith("-") for line in lines[:2]):
        return (None, None, "git printed no common git directory for {}".format(root))
    try:
        common = os.path.realpath(os.path.join(root, lines[0]))
        identity = os.path.relpath(os.path.realpath(os.path.join(root, lines[1])), common)
    except (OSError, ValueError) as exc:
        return (None, None, "the common git directory of {} cannot be resolved ({})".format(root, exc))
    if identity == ".." or identity.startswith("../"):
        return (None, None, "the git directory of {} lies outside its common git directory {}".format(
            root, common))
    return (common, identity, None)


def _rdp_record_path(home, identity, rel):
    """The record of the registry at rel, its path relative to the top level of the worktree whose identity
    is identity (_rdp_record_home), in home: a file named by the sha256 of that pair alone, so the record
    depends on no absolute path and moves with the repository (a renamed or moved repository keeps it)."""
    key = __import__("hashlib").sha256(json.dumps([identity, rel]).encode("utf-8", "surrogateescape"))
    return os.path.join(home, key.hexdigest() + ".json")


def _rdp_record_read(path):
    """The binding record at path, read without following a symbolic link and without blocking: (None, None)
    when there is none; ("ok", (digest, cfg)) when it is well formed (its binding valid by _rdp_binding and
    its digest that binding's); ("bad", detail) otherwise."""
    try:
        fd = os.open(path, _RDP_OPEN_FLAGS | os.O_NOFOLLOW)
    except (FileNotFoundError, NotADirectoryError):
        return (None, None)
    except (OSError, ValueError) as exc:
        return ("bad", "it cannot be opened ({})".format(exc))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ("bad", "it is not a regular file")
        blob = _rdp_read_fd(fd, _RDP_RECORD_MAX + 1)
    except OSError as exc:
        return ("bad", "it cannot be read ({})".format(exc))
    finally:
        os.close(fd)
    if len(blob) > _RDP_RECORD_MAX:
        return ("bad", "it is over {} bytes".format(_RDP_RECORD_MAX))
    try:
        doc = json.loads(blob.decode("utf-8"))
    except ValueError as exc:
        return ("bad", "it is not JSON ({})".format(exc))
    if not isinstance(doc, dict) or doc.get("version") != _RDP_RECORD_VERSION or \
            not isinstance(doc.get("digest"), str):
        return ("bad", "it is not a version {} binding record".format(_RDP_RECORD_VERSION))
    kind, cfg = _rdp_binding(dict(review_dispatch=doc.get("binding")))
    if kind != "ok":
        return ("bad", "its binding is malformed ({})".format(cfg))
    if _rdp_binding_digest(cfg) != doc["digest"]:
        return ("bad", "its digest is not its binding's")
    return ("ok", (doc["digest"], cfg))


def _rdp_record_write(home, path, identity, rel, cfg):
    """Record cfg as the binding of the registry at rel in the worktree identity, at path in home, unless a record is there
    already (a concurrent check wrote one first; the caller reads it back and compares). Each directory is
    made with mode 0700 and must be a directory, not a link to one; the record is written to a fresh
    temporary file (O_EXCL, O_NOFOLLOW, mode 0600) and linked into place, so a reader never sees a part of
    it. None when it is recorded, or the detail of the failure."""
    doc = dict(version=_RDP_RECORD_VERSION, worktree=identity, registry_path=rel,
               registry_files=list(_ORCH_REGISTRY_FILES), digest=_rdp_binding_digest(cfg),
               binding=_rdp_binding_doc(cfg))
    blob = (json.dumps(doc, sort_keys=True, indent=1, ensure_ascii=True) + "\n").encode("ascii")
    tmp = "{}.{}.tmp".format(path, os.urandom(8).hex())
    try:
        for folder in (os.path.dirname(home), home):
            try:
                os.mkdir(folder, 0o700)
            except FileExistsError:
                pass
            if not stat.S_ISDIR(os.lstat(folder).st_mode):
                return "{} is not a directory".format(folder)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                     0o600)
        try:
            view = memoryview(blob)
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
        try:
            os.link(tmp, path, follow_symlinks=False)
        except FileExistsError:
            pass
        finally:
            os.unlink(tmp)
    except (OSError, ValueError) as exc:
        return "it cannot be written ({})".format(exc)
    return None


def _rdp_record_check(home, own, scope):
    """The binding record check for the session repository, whose records are in home and whose own
    registries are own, (reg_dir, identity, rel) for each (the session worktree's top level, and its main
    worktree's): ("withhold", detail) when a record cannot be read, which leaves the recorded dispatch
    commands unknown; otherwise (drift, recorded), recorded a (cfg, reg_dir) pair for each record found,
    drift None when each own registry's live binding matches its record, or the text naming the change and
    the operator action when one does not. Each live binding is read through scope, the check's single
    snapshot of each registry, so the comparison and the enforcement see the same read. A binding seen with
    no record is recorded here (its first observation); one that cannot be recorded is a drift too, so a
    binding the hook cannot record never leaves the registry unguarded."""
    drifts, recorded, seen = [], [], set()
    for reg_dir, identity, rel in own:
        path = _rdp_record_path(home, identity, rel)
        if path in seen:
            continue
        seen.add(path)
        binding, cfg = scope(reg_dir)
        kind, rec = _rdp_record_read(path)
        if kind is None and binding == "ok":
            failed = _rdp_record_write(home, path, identity, rel, cfg)
            if failed is not None:
                drifts.append("the review_dispatch binding of the registry at {} cannot be recorded in "
                              "the binding record {}: {}; an operator makes that directory "
                              "writable".format(reg_dir, path, failed))
                continue
            kind, rec = _rdp_record_read(path)
        if kind is None:
            continue
        if kind == "bad":
            return ("withhold", "the review dispatch binding record {} of the registry at {} cannot "
                    "be used: {}; an operator removes it outside the session (rm {}), and the next "
                    "check records the binding then in force".format(path, reg_dir, rec, path))
        digest, rcfg = rec
        recorded.append((rcfg, reg_dir))
        if binding is None:
            change = "is missing or carries no review_dispatch binding"
        elif binding != "ok":
            change = "cannot be read or its binding is malformed ({})".format(cfg)
        elif _rdp_binding_digest(cfg) != digest:
            change = "binds review dispatch differently (digest {}) from the binding recorded (digest " \
                "{})".format(_rdp_binding_digest(cfg)[:12], digest[:12])
        else:
            continue
        drifts.append("the orchestration registry at {} {} since its binding was recorded in the "
                      "binding record {}, so a command may have removed or rewritten it; an operator "
                      "restores the recorded binding, or removes the record outside the session (rm {}) "
                      "so the next check records the binding then in force".format(reg_dir, change, path, path))
    return ("; ".join(drifts) or None, recorded)


def _rdp_bound(data, tool_input, binding, cfg, root, reg_dir, foreign, guard):
    """The decision in a session scoped by the binding of the registry at reg_dir. foreign is False, True
    (the registry is not the session repository's own), or the text of a conflict between registries;
    guard is the session repository's git directory to protect (_rdp_names_common_dir)."""
    if binding == "bad":
        # A malformed binding cannot say which commands dispatch.
        return _rdp_withhold(tool_input, "the orchestration registry or its review_dispatch binding is "
                             "malformed ({})".format(cfg))
    result = _rdp_judge(data, cfg, root, reg_dir, tool_input, foreign=foreign, guard=guard)
    if result[0] in ("allow", "note") and _rdp_overdue():
        # A read or probe that overran the deadline leaves a result that was not reached within the
        # budget: a cannot-evaluate, never an allow.
        return ("unverifiable", "the check ran past its {} second budget, so its result is not "
                "used".format(_RDP_BUDGET))
    return result


# The commands a plain call may run on a registry file or the .aiqt directory, because they only read
# (no option of theirs writes a file): programs of rule 4's allowlist (_RDP_PLAIN_COMMANDS), named by the
# command word's last component.
_RDP_REGISTRY_READERS = frozenset(("cat", "head", "tail", "wc", "ls", "stat", "grep", "cmp", "diff"))


def _rdp_word_values(folded):
    """The texts a casefolded plain word may hand a program as a path: the word itself, what follows each
    =, : or ) in it (an option value, a git pathspec after its magic), and, for a word of short options
    (one leading dash), what follows each of its letters (a value glued to -t, -C or a group such as -rt)."""
    values = {folded}
    values.update(folded[i + 1:] for i, ch in enumerate(folded) if ch in "=:)")
    if folded.startswith("-") and not folded.startswith("--"):
        values.update(folded[k:] for k in range(2, len(folded)))
    return [value for value in values if value]


def _rdp_component_names(component, names):
    """Whether one path component names one of names (casefolded): equal to it, or, holding a glob
    character (a quoted glob git or another program expands itself), matching it."""
    if any(ch in component for ch in "*?["):
        return any(fnmatch.fnmatchcase(name, component) for name in names)
    return component in names


def _rdp_names_registry(words, cwd=None):
    """The first word of a plain command that names an orchestration registry file (its file name anywhere
    in the word, without regard to case) or the .aiqt directory holding it; None when no word does, or the
    command only reads (_RDP_REGISTRY_READERS). The .aiqt directory is named by a value of the word
    (_rdp_word_values) whose last component, once normalized, or once resolved against the session cwd,
    is .aiqt or a glob matching it or a registry file; and by a word entering a .aiqt directory (-C
    .aiqt/core) in a command that also climbs with .., which can then reach it. A Bash call that writes,
    moves or removes the registry would switch the hook off for every later call."""
    if _rdp_basename(words[0]) in _RDP_REGISTRY_READERS:
        return None
    names = tuple(rel.rsplit("/", 1)[-1].casefold() for rel in _ORCH_REGISTRY_FILES)
    targets = names + (".aiqt",)
    bases = []
    if isinstance(cwd, str) and os.path.isabs(cwd):
        bases = [os.path.normpath(cwd), os.path.realpath(cwd)]
    enters = climbs = None
    for word in words[1:]:
        folded = word.casefold()
        if any(name in folded for name in names):
            return word
        for value in _rdp_word_values(folded):
            forms = [os.path.normpath(value)] + [os.path.normpath(os.path.join(b, value)) for b in bases]
            if any(_rdp_component_names(os.path.basename(form).casefold(), targets) for form in forms):
                return word
            parts = value.split("/")
            if ".." in parts:
                climbs = climbs or word
            if any(_rdp_component_names(part, (".aiqt",)) for part in parts):
                enters = enters or word
    if enters is not None and climbs is not None:
        return climbs
    return None


# The git configuration a plain git command may not change in a bound session: it moves the work tree
# (core.worktree), makes the repository bare (core.bare), or includes another configuration file that may
# set either. A separated git directory (--separate-git-dir) moves the repository the same way. Each would
# point git's top level away from the registry that binds the session.
_RDP_REPOSITORY_KEYS = ("core.worktree", "core.bare", "include.path", "includeif.", "--separate-git-dir")
# The git subcommands that only read: a plain git command running one of them (matched exactly, as git runs
# a built-in only under its exact name and an alias never hides one) treats every word as data, so neither
# a registry name nor a repository key in it is refused (git log -S core.worktree, git ls-files '*').
_RDP_GIT_READS = frozenset(("status", "log", "diff", "show", "rev-parse", "ls-files", "blame", "grep",
                            "cat-file"))
# git's global options before the subcommand: those taking the next word as their value (or, written
# long, a value after =), and those taking none. Any other leaves the subcommand unknown.
_RDP_GIT_GLOBAL_VALUED = frozenset(("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env"))
_RDP_GIT_GLOBAL_FLAGS = frozenset(("-p", "--paginate", "-P", "--no-pager", "--no-replace-objects", "--bare",
                                   "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
                                   "--icase-pathspecs", "--no-optional-locks"))
# The git subcommands a plain git command may run in a bound session, each matched exactly (git runs a
# built-in only under its exact name, and an alias never hides one). Every other subcommand (instaweb,
# difftool, mergetool, send-email, filter-branch, bisect, submodule, daemon, web--browse, the credential
# commands, help, an alias, an external git-NAME program found on PATH, and one behind a global option this
# hook does not know) is refused: no option table over git's open set of subcommands closes the programs
# they run (instaweb --httpd names its server's command). _RDP_GIT_INFO_OPTIONS are the global options
# that, as the only word after git, print a fact and run nothing.
_RDP_GIT_ALLOWED = frozenset((
    "status", "log", "diff", "show", "rev-parse", "ls-files", "ls-tree", "cat-file", "blame", "grep",
    "describe", "shortlog", "merge-base", "rev-list", "for-each-ref", "show-ref", "branch", "tag", "remote",
    "config", "add", "rm", "mv", "commit", "push", "fetch", "stash", "switch", "checkout", "restore", "reset",
    "merge", "rebase", "cherry-pick", "revert", "worktree", "clone"))
_RDP_GIT_INFO_OPTIONS = frozenset(("--version", "--exec-path", "--html-path", "--man-path", "--info-path"))
# The options that make a git command run a program, named in it or configured, whatever its subcommand
# (each matched abbreviated or not): an external diff (diff.external, a diff driver's command), a textconv
# filter, a signature check running gpg.program, grep's pager on the matches, rebase's exec lines,
# difftool's command, the upload, receive and archive programs of a remote, send-email's commands and
# server, and the global --exec-path=DIR, from which git runs its own programs (a bare --exec-path only
# prints it). A word naming an option of its own that shares a prefix with one of them (--text, --to,
# --cc, --filter) is not one. _RDP_GIT_SUB_PROGRAM_OPTIONS holds those of one subcommand only, read from
# git SUB -h (git 2.53) for each allowlisted subcommand: cat-file's smudge and clean filters (its --filter
# is an object filter); clone's --config, which sets configuration in the new repository; signing and
# signature verification, which run gpg.program (commit, merge, rebase, cherry-pick and revert --gpg-sign,
# tag --sign, --local-user and --verify, merge --verify-signatures, push --signed); a merge strategy, which
# may name a git-merge-NAME program on PATH (merge, rebase, cherry-pick and revert --strategy); patch and
# interactive modes, which run interactive.diffFilter (add, commit, checkout, restore, reset and stash
# --patch, add and commit --interactive); and commit and tag --trailer, which run trailer commands. The
# other allowlisted subcommands have no such option of their own (status, log, diff, show, rev-parse,
# ls-files, ls-tree, blame, grep, describe, shortlog, merge-base, rev-list, for-each-ref, show-ref, branch,
# remote, config, rm, mv, fetch, switch, worktree). Whether or not a subcommand has one, an option's value
# can also run gpg.program: a signature placeholder or atom in a format (--format, --pretty, shortlog
# --group) or a sort key (the --sort of for-each-ref, branch and tag), judged by _rdp_git_signature_value.
# git runs the editor by default (git commit without -m, git revert), so an option asking for it is not
# counted, as configuration is not.
_RDP_GIT_PROGRAM_OPTIONS = ("--ext-diff", "--textconv", "--show-signature", "--open-files-in-pager", "--exec",
                            "--extcmd", "--upload-pack", "--receive-pack", "--exec-path", "--sendmail-cmd",
                            "--to-cmd", "--cc-cmd", "--header-cmd", "--smtp-server")
_RDP_GIT_SUB_PROGRAM_OPTIONS = {
    "cat-file": ("--filters",), "clone": ("--config",), "commit": ("--gpg-sign", "--patch", "--interactive",
    "--trailer"), "tag": ("--sign", "--local-user", "--verify", "--trailer"), "merge": ("--gpg-sign",
    "--verify-signatures", "--strategy"), "rebase": ("--gpg-sign", "--strategy"), "cherry-pick": ("--gpg-sign",
    "--strategy"), "revert": ("--gpg-sign", "--strategy"), "push": ("--signed",), "add": ("--patch",
    "--interactive"), "checkout": ("--patch",), "restore": ("--patch",), "reset": ("--patch",),
    "stash": ("--patch",)}
_RDP_GIT_NOT_PROGRAM_OPTIONS = frozenset(("--text", "--filter", "--to", "--cc"))
# The subcommands whose grammar ends their options at a -- ([--] [<pathspec>...]), so a word after it is a
# path, never an option: on git 2.53, git SUB -- --bogus and git SUB WORD -- --bogus take --bogus as a
# path or revision for each of them (no unknown-option error), and git diff, log -p and show run no
# diff.external for -- --ext-diff while they do for --ext-diff. log, show, diff, rev-list and shortlog end
# at the first -- whatever comes before it (git log --grep -- x lacks a --grep value); the others end at a
# -- not taken as an option's value. The -- is read as ending them when the one scan over the words reaches
# it surely free: not taken by the word before it as a value (_rdp_git_takes_next, _rdp_git_takes_dashdash),
# unless that word was itself surely an option's value; a TRANSPORT::ADDRESS after it is still judged. Any
# other subcommand (stash, whose show passes its words to a diff, config, remote, worktree, cat-file, mv,
# cherry-pick, revert, merge-base and the rest) judges a word after -- as before, which only refuses.
_RDP_GIT_DASHDASH_ENDS = frozenset(("status", "log", "diff", "show", "rev-list", "shortlog", "ls-files",
                                    "blame", "grep", "add", "rm", "commit", "checkout", "restore", "reset"))
# The subcommands that run a command given as their words: bisect run, submodule foreach (also through
# submodule--helper) and filter-branch, whose filters and setup are shell text.
_RDP_GIT_PROGRAM_SUBCOMMANDS = {"bisect": "run", "submodule": "foreach", "submodule--helper": "foreach",
                                "filter-branch": None}
# For log, show and diff, an option asking for patch output, to which a textconv filter configured before
# the session applies: such a read gets the full checks.
_RDP_GIT_PATCH_OPTIONS = ("--patch", "--patch-with-raw", "--patch-with-stat", "--unified", "--cc", "--dd",
                          "--combined-all-paths", "--remerge-diff", "--diff-merges", "--word-diff",
                          "--word-diff-regex", "--color-words", "--function-context", "--binary")
# The short options of log, show and diff that ask for patch output (-p, -u, -U, -c, -m, -W, -L), found
# in a word of short options before any letter that takes a value: the rest of the word after one of
# _RDP_GIT_DIFF_VALUED is its value (-Sconfig searches for config), and after one of _RDP_GIT_DIFF_NEXT
# with nothing glued, so is the next word (-S -p searches for -p). _RDP_GIT_DIFF_NEXT_LONG holds the long
# options that take the next word when written without = (--grep -c, --author -p).
_RDP_GIT_PATCH_LETTERS = "pucmUWL"
_RDP_GIT_DIFF_VALUED = "SGOIlnMCBX"
_RDP_GIT_DIFF_NEXT = "SGOIln"
_RDP_GIT_DIFF_NEXT_LONG = frozenset((
    "--grep", "--author", "--committer", "--since", "--until", "--after", "--before", "--max-count", "--skip",
    "--min-age", "--max-age", "--exclude", "--glob", "--encoding", "--date", "--src-prefix", "--dst-prefix",
    "--line-prefix", "--diff-filter", "--find-object", "--ignore-matching-lines", "--anchored", "--skip-to",
    "--rotate-to", "--diff-algorithm", "--inter-hunk-context", "--ws-error-highlight"))
# The git config read actions, each with the least and most operands it takes; the options that may
# precede one, or a key read alone, without changing what it does; and those of them taking a value, glued
# after = or as the next word (--file PATH, --type bool).
# The short options of each subcommand, from git SUB -h (git 2.53): (the letters that run a program, the
# letters taking a value, after which the rest of the word is that value, and those of them taking the
# next word when nothing is glued to them), so -eOops is a pattern but -iO runs the pager, and git grep
# -e -O searches for -O; and the long options taking the next word when written without =, so git log
# --grep --ext-diff searches for --ext-diff. Only an option whose value git requires is listed: a word
# taken as a value is not judged, while a word wrongly judged only refuses. A subcommand not listed has
# no short option that runs a program or takes the next word. For blame and shortlog the letters taking the
# next word are those git 2.53 refused when given last for want of a value (_RDP_GIT_DASHDASH_TAKES reads
# them from here), and the letters taking the rest of the word add those whose value git takes only glued,
# found by git 2.53 reading blame -CS f and -MS f as a score and the file f, and refusing shortlog -wG.
_RDP_GIT_PROGRAM_LETTERS = {
    "grep": ("O", "efABCm", "efABCm"), "rebase": ("xsS", "CX", "CX"), "difftool": ("x", "t", ""),
    "clone": ("uc", "job", "job"), "tag": ("suv", "mF", "mF"), "commit": ("Sp", "FmcCtU", "FmcCtU"),
    "merge": ("sS", "XmF", "XmF"), "cherry-pick": ("S", "mX", "mX"), "revert": ("S", "mX", "mX"),
    "add": ("pi", "U", "U"), "checkout": ("p", "bBU", "bBU"), "restore": ("p", "sU", "sU"),
    "reset": ("p", "U", "U"), "stash": ("p", "", ""), "switch": ("", "cC", "cC"), "push": ("", "o", "o"),
    "fetch": ("", "jo", "jo"), "branch": ("", "u", "u"), "ls-files": ("", "xX", "xX"),
    "blame": ("", "GILOSCM", "GILOS"), "shortlog": ("", "GIOSlw", "GIOSl"),
    "log": ("", _RDP_GIT_DIFF_VALUED + "L", _RDP_GIT_DIFF_NEXT + "L"),
    "show": ("", _RDP_GIT_DIFF_VALUED + "L", _RDP_GIT_DIFF_NEXT + "L"),
    "diff": ("", _RDP_GIT_DIFF_VALUED, _RDP_GIT_DIFF_NEXT),
    "rev-list": ("", _RDP_GIT_DIFF_VALUED, _RDP_GIT_DIFF_NEXT)}
_RDP_GIT_NEXT_LONG = {
    "log": _RDP_GIT_DIFF_NEXT_LONG | frozenset(("--decorate-refs", "--decorate-refs-exclude")),
    "show": _RDP_GIT_DIFF_NEXT_LONG | frozenset(("--decorate-refs", "--decorate-refs-exclude")),
    "diff": _RDP_GIT_DIFF_NEXT_LONG, "rev-list": _RDP_GIT_DIFF_NEXT_LONG,
    "shortlog": (_RDP_GIT_DIFF_NEXT_LONG - frozenset(("--committer",))) | frozenset(("--group",)),
    "ls-files": frozenset(("--exclude", "--exclude-from", "--exclude-per-directory", "--with-tree", "--format")),
    "ls-tree": frozenset(("--format",)), "cat-file": frozenset(("--path", "--filter")),
    "blame": frozenset(("--diff-algorithm", "--ignore-rev", "--ignore-revs-file", "--contents")),
    "grep": frozenset(("--max-depth", "--context", "--before-context", "--after-context", "--threads",
                       "--max-count")),
    "describe": frozenset(("--candidates", "--match", "--exclude")),
    "for-each-ref": frozenset(("--count", "--format", "--start-after", "--exclude", "--sort", "--points-at",
                               "--merged", "--no-merged", "--contains", "--no-contains")),
    "branch": frozenset(("--set-upstream-to", "--contains", "--no-contains", "--merged", "--no-merged", "--sort",
                         "--points-at", "--format")),
    "tag": frozenset(("--message", "--file", "--cleanup", "--contains", "--no-contains", "--merged",
                      "--no-merged", "--sort", "--points-at", "--format")),
    "add": frozenset(("--unified", "--inter-hunk-context", "--chmod", "--pathspec-from-file")),
    "commit": frozenset(("--file", "--author", "--date", "--message", "--reedit-message", "--reuse-message",
                         "--fixup", "--squash", "--template", "--cleanup", "--unified", "--inter-hunk-context",
                         "--pathspec-from-file")),
    "push": frozenset(("--repo", "--recurse-submodules", "--push-option")),
    "fetch": frozenset(("--jobs", "--depth", "--shallow-since", "--shallow-exclude", "--deepen", "--refmap",
                        "--server-option", "--negotiation-tip", "--filter")),
    "switch": frozenset(("--create", "--force-create", "--conflict", "--orphan")),
    "checkout": frozenset(("--conflict", "--orphan", "--unified", "--inter-hunk-context", "--pathspec-from-file")),
    "restore": frozenset(("--source", "--conflict", "--unified", "--inter-hunk-context", "--pathspec-from-file")),
    "reset": frozenset(("--unified", "--inter-hunk-context", "--pathspec-from-file")),
    "merge": frozenset(("--cleanup", "--strategy-option", "--message", "--file", "--into-name")),
    "rebase": frozenset(("--onto", "--whitespace", "--empty", "--strategy-option")),
    "cherry-pick": frozenset(("--cleanup", "--mainline", "--strategy-option", "--empty")),
    "revert": frozenset(("--cleanup", "--mainline", "--strategy-option")),
    "clone": frozenset(("--jobs", "--template", "--reference", "--reference-if-able", "--origin", "--branch",
                        "--revision", "--depth", "--shallow-since", "--shallow-exclude", "--separate-git-dir",
                        "--ref-format", "--server-option", "--filter", "--bundle-uri")),
    "rm": frozenset(("--pathspec-from-file",))}
# For each subcommand of _RDP_GIT_DASHDASH_ENDS, its options that take a value git requires, which it reads
# from the next word when none is written with the option: (the long options, the short letters). Read
# from git SUB --help-all (git 2.53), which lists the hidden options too, for status (none), ls-files,
# grep, add, rm, commit, checkout, restore and reset, and for blame and shortlog, whose words git passes
# to the revision options when they are not their own, joined with those of log (their short letters are
# their own, not those of log: blame takes -L and -S by git blame -h and -G, -I and -O through the revision
# options, shortlog -G, -I, -O, -S and -l through the revision options, each found by git 2.53 refusing the
# letter given last for want of a value; blame -n and -l and shortlog -n take none, as shortlog --committer
# does); for log, show, diff and
# rev-list (whose -h lists no option) the git-log, git-show, git-diff and git-rev-list documentation
# pages, as _RDP_GIT_NEXT_LONG and the next-word letters of _RDP_GIT_PROGRAM_LETTERS hold them.
_RDP_GIT_REV_TAKES = (_RDP_GIT_NEXT_LONG["log"], _RDP_GIT_PROGRAM_LETTERS["log"][2])
_RDP_GIT_DASHDASH_TAKES = {
    "status": (frozenset(), ""), "log": _RDP_GIT_REV_TAKES,
    "show": (_RDP_GIT_NEXT_LONG["show"], _RDP_GIT_PROGRAM_LETTERS["show"][2]),
    "diff": (_RDP_GIT_NEXT_LONG["diff"], _RDP_GIT_PROGRAM_LETTERS["diff"][2]),
    "rev-list": (_RDP_GIT_NEXT_LONG["rev-list"], _RDP_GIT_PROGRAM_LETTERS["rev-list"][2]),
    "shortlog": ((_RDP_GIT_REV_TAKES[0] - frozenset(("--committer",))) | frozenset(("--group",)),
                 _RDP_GIT_PROGRAM_LETTERS["shortlog"][2]),
    "ls-files": (frozenset(("--exclude", "--exclude-from", "--exclude-per-directory", "--format",
                            "--with-tree")), "xX"),
    "blame": (_RDP_GIT_REV_TAKES[0] | frozenset(("--contents", "--diff-algorithm", "--ignore-rev",
                                                 "--ignore-revs-file")), _RDP_GIT_PROGRAM_LETTERS["blame"][2]),
    "grep": (frozenset(("--after-context", "--before-context", "--context", "--max-count", "--max-depth",
                        "--threads")), "ABCefm"),
    "add": (frozenset(("--chmod", "--inter-hunk-context", "--pathspec-from-file", "--unified")), "U"),
    "rm": (frozenset(("--pathspec-from-file",)), ""),
    "commit": (frozenset(("--author", "--cleanup", "--date", "--file", "--fixup", "--inter-hunk-context",
                          "--message", "--pathspec-from-file", "--reedit-message", "--reuse-message", "--squash",
                          "--template", "--trailer", "--unified")), "CFUcmt"),
    "checkout": (frozenset(("--conflict", "--inter-hunk-context", "--orphan", "--pathspec-from-file",
                            "--unified")), "BUb"),
    "restore": (frozenset(("--conflict", "--inter-hunk-context", "--pathspec-from-file", "--source",
                           "--unified")), "Us"),
    "reset": (frozenset(("--inter-hunk-context", "--pathspec-from-file", "--unified")), "U")}
# For each subcommand of _RDP_GIT_DASHDASH_TAKES, its options taking no value, or one only glued after =,
# that git 2.53 reads from a word naming (or abbreviating) one of its value-taking long options: git log
# --decorate is no --decorate-refs, git shortlog --summary and blame --root no revision option. Every
# other long word a value-taking option of the set begins with takes the next word or stops git with an
# error (an unknown or ambiguous option, or one missing its value), found by giving git 2.53 each such word
# last.
_RDP_GIT_DASHDASH_FREE = {
    "log": ("--decorate",), "show": ("--decorate",), "shortlog": ("--email", "--summary"),
    "blame": ("--abbrev", "--incremental", "--line-porcelain", "--minimal", "--root")}
# The long options, written whole, that take no value (or one only glued after =) for every subcommand of
# _RDP_GIT_DASHDASH_TAKES that accepts them: git 2.53 refused, as an unknown option, a word given after
# each (git log --stat --bogus), or, for diff, rev-list, shortlog and blame, which do not name the word they
# refuse, ran with the option alone and refused the word after it. Every other long option word written
# without = may take the next word as its value (_rdp_git_may_take): an option missing from the value tables
# (git 2.53 log, show, diff, rev-list, shortlog and blame take the next word for --word-diff-regex,
# --default, --output and --since-as-filter, among others), an abbreviation or a misspelling.
_RDP_GIT_FREE_LONG = frozenset((
    "--abbrev-commit", "--all-match", "--allow-empty", "--amend", "--binary", "--boundary", "--branch", "--cached",
    "--check", "--cherry-pick", "--color-words", "--count", "--date-order", "--decorate", "--deleted", "--detach",
    "--dry-run", "--email", "--exit-code", "--extended-regexp", "--files-with-matches", "--first-parent",
    "--fixed-strings", "--force", "--full-history", "--full-index", "--hard", "--ignore-all-space", "--ignore-case",
    "--ignore-space-change", "--ignored", "--intent-to-add", "--keep", "--left-right", "--line-number",
    "--merge-base", "--merges", "--minimal", "--mixed", "--modified", "--name-only", "--name-status",
    "--no-decorate", "--no-edit", "--no-ext-diff", "--no-index", "--no-merges", "--no-patch", "--no-renames",
    "--no-textconv", "--no-verify", "--no-walk", "--numbered", "--numstat", "--oneline", "--others", "--ours",
    "--patch", "--porcelain", "--quiet", "--raw", "--recursive", "--reverse", "--root", "--short", "--shortstat",
    "--show-email", "--show-stash", "--signoff", "--soft", "--staged", "--stat", "--summary", "--text", "--theirs",
    "--topo-order", "--update", "--verbose", "--word-diff", "--worktree"))
# The subcommands that reach a remote, where a URL written TRANSPORT::ADDRESS runs the remote helper
# git-remote-TRANSPORT found on PATH.
_RDP_GIT_TRANSPORTS = frozenset(("fetch", "push", "clone", "remote"))
_RDP_GIT_CONFIG_READS = {"--get": (1, 2), "--get-all": (1, 2), "--get-regexp": (1, 2), "--list": (0, 0),
                         "-l": (0, 0)}
_RDP_GIT_CONFIG_MODIFIERS = frozenset(("--local", "--global", "--system", "--worktree", "--show-origin",
                                       "--show-scope", "-z", "--null", "--name-only", "--includes",
                                       "--no-includes", "--bool", "--int", "--bool-or-int", "--path",
                                       "--expiry-date", "--fixed-value", "--no-type"))
_RDP_GIT_CONFIG_VALUED = frozenset(("--file", "-f", "--blob", "--type", "--default"))


def _rdp_git_subcommand(words):
    """(index, configured) for a plain git command: the index in words of its subcommand, None when a
    global option this hook does not know comes first or no subcommand follows; and whether a global
    option sets configuration for the call (-c, --config-env), which can make any subcommand run a
    program."""
    configured = False
    i = 1
    while i < len(words):
        word = words[i]
        if not word.startswith("-"):
            return (i, configured)
        name = word.split("=", 1)[0]
        configured = configured or name in ("-c", "--config-env")
        if word in _RDP_GIT_GLOBAL_VALUED:
            i += 2
        elif word in _RDP_GIT_GLOBAL_FLAGS or (name in _RDP_GIT_GLOBAL_VALUED and name.startswith("--") and
                                               name != word):
            i += 1
        else:
            return (None, configured)
    return (None, configured)


def _rdp_long_option(name, options):
    """Whether the option name (the part of a word before any =) is one of options or an abbreviation git
    may expand to one (--text is an option of its own, not --textconv)."""
    return len(name) > 2 and name.startswith("--") and name != "--text" and \
        any(option.startswith(name) for option in options)


def _rdp_git_takes_dashdash(sub, word):
    """Whether the option word, just before a -- given to the git subcommand sub, may take that -- as its
    value (_RDP_GIT_DASHDASH_TAKES): a long option written without = that is one of its value-taking options
    or an abbreviation of one, or a word of short options whose first value-taking letter ends it. A
    --opt=value word, a short letter with its value glued (-n5, -U3), a count (-1) and an option taking no
    value never take it; a word that is no option takes nothing, and an option of a subcommand without a
    set may take it."""
    if not word.startswith("-") or word == "-":
        return False
    if sub not in _RDP_GIT_DASHDASH_TAKES:
        return True
    longs, letters = _RDP_GIT_DASHDASH_TAKES[sub]
    if word.startswith("--"):
        return "=" not in word and any(option.startswith(word) for option in longs)
    for k, ch in enumerate(word[1:], 1):
        if ch in letters:
            return k == len(word) - 1
    return False


def _rdp_git_waits(sub, word):
    """Whether the long option word, given to the git subcommand sub, surely takes the next word as its
    value or stops git: written without = and beginning one of the value-taking long options of
    _RDP_GIT_DASHDASH_TAKES, it is none of the options taking no value that begin with it
    (_RDP_GIT_DASHDASH_FREE), so git either takes the next word (git commit --mess) or refuses the word."""
    return word.startswith("--") and sub in _RDP_GIT_DASHDASH_TAKES and _rdp_git_takes_dashdash(sub, word) and \
        not any(option.startswith(word) for option in _RDP_GIT_DASHDASH_FREE.get(sub, ()))


def _rdp_git_takes_next(sub, word):
    """Whether the option word surely takes the next word as its value when given to the git subcommand
    sub, by the tables the scan reads operands with: a long option of _RDP_GIT_NEXT_LONG written whole, or
    a word of short options whose first value-taking letter (_RDP_GIT_PROGRAM_LETTERS) takes the next word
    and ends it (git grep -e, git log -S); the rest of a word after any other value-taking letter is its
    value (-eOops, -n5)."""
    if word in _RDP_GIT_NEXT_LONG.get(sub, frozenset()):
        return True
    _letters, valued, nexts = _RDP_GIT_PROGRAM_LETTERS.get(sub, ("", "", ""))
    if len(word) > 1 and word.startswith("-") and not word.startswith("--"):
        for k, ch in enumerate(word[1:], 1):
            if ch in valued:
                return ch in nexts and k == len(word) - 1
    return False


def _rdp_git_may_take(sub, word):
    """Whether the option word may take the next word as its value when given to the git subcommand sub:
    it surely does (_rdp_git_takes_next), it may take a -- (_rdp_git_takes_dashdash), or, for a subcommand
    of _RDP_GIT_DASHDASH_TAKES, it is a long option written without = other than one of
    _RDP_GIT_FREE_LONG (git show --word-diff-regex --grep gives --grep as the regex)."""
    return _rdp_git_takes_next(sub, word) or _rdp_git_takes_dashdash(sub, word) or (
        sub in _RDP_GIT_DASHDASH_TAKES and word.startswith("--") and "=" not in word and
        word not in _RDP_GIT_FREE_LONG)


# A pretty-format signature placeholder (%G?, %GG, %GS, %GK, %GF, %GP, %GT), also with git's one modifier
# character between % and it (%+G?, %-GG, % GK; a padding or wrapping placeholder such as %<(5) or %w(9) is a
# placeholder of its own, so the signature one keeps its %), or a ref-filter signature atom, also of the
# commit a tag points to (%(signature), %(*signature:grade)): each makes git run gpg.program.
_RDP_GIT_SIGNATURE_FORMAT = re.compile(r"%[-+ ]?G|%\(\*?signature")
# A ref-filter sort key naming the signature atom, which git computes to sort by, running gpg.program:
# signature with git's prefixes (a leading - reverses, version: or v: sorts as versions, * reads the commit a
# tag points to) and modifiers (signature:grade, -v:*signature:signer). It is matched anywhere in the key, so
# a key only holding the word (contents:signature, which runs none) is refused too.
_RDP_GIT_SIGNATURE_SORT = re.compile(r"signature")
# The options, beyond --format and --pretty (a pretty format for log, show, rev-list, shortlog, blame and
# stash list, a ref-filter format for for-each-ref, branch and tag), whose value git reads as a format or a
# sort key, by subcommand, each with the pattern a value running gpg.program matches: the --sort of
# for-each-ref, branch and tag (a ref-filter sort key, given once per key), and shortlog --group, whose
# value git reads as a pretty format when it holds a % (also written format:FORMAT). Read from git SUB -h
# (git 2.53) for every allowlisted subcommand (log, show, diff and rev-list list none there; their format
# options are the revision options --format and --pretty) and run on git 2.53 with a gpg.program leaving a
# marker: the other format options take formats with no signature placeholder or atom. ls-files and
# ls-tree --format refuse a %G placeholder (exit 128, "element 'GG' does not start with '('") and
# %(signature); cat-file --batch, --batch-check and --batch-command refuse %(signature), and only they print
# %G as written. log --date=format: is a strftime format, and clone --ref-format names a ref storage format.
_RDP_GIT_SIGNATURE_VALUES = {
    "for-each-ref": (("--sort",), _RDP_GIT_SIGNATURE_SORT), "branch": (("--sort",), _RDP_GIT_SIGNATURE_SORT),
    "tag": (("--sort",), _RDP_GIT_SIGNATURE_SORT), "shortlog": (("--group",), _RDP_GIT_SIGNATURE_FORMAT)}


def _rdp_git_signature_value(sub, name):
    """The pattern a value of the option name (the part of a word before any =, abbreviated or not) given to
    the git subcommand sub matches when it makes git run gpg.program: _RDP_GIT_SIGNATURE_FORMAT for --format,
    --pretty and shortlog --group, _RDP_GIT_SIGNATURE_SORT for a --sort of _RDP_GIT_SIGNATURE_VALUES; None
    when the option takes no value covered here that can run gpg.program (git may still read its value as a
    format: cat-file --batch-check takes one, with no signature placeholder or atom)."""
    if _rdp_long_option(name, ("--format", "--pretty")):
        return _RDP_GIT_SIGNATURE_FORMAT
    options, pattern = _RDP_GIT_SIGNATURE_VALUES.get(sub, ((), None))
    return pattern if _rdp_long_option(name, options) else None


def _rdp_git_program_under(sub, before, after):
    """The first word that runs a program when the git subcommand sub is called with the words after,
    after the global options before; None when none does (_rdp_git_runs_program)."""
    for word in before:
        if word in ("-p", "--paginate"):
            return word
    run = _RDP_GIT_PROGRAM_SUBCOMMANDS.get(sub, False)
    if run is None or (run and run in after):
        return sub if run is None else run
    owned = _RDP_GIT_SUB_PROGRAM_OPTIONS.get(sub, ())
    letters, valued, _nexts = _RDP_GIT_PROGRAM_LETTERS.get(sub, ("", "", ""))
    # held: the word may be an option's value; data: it surely is one (the word before it, itself surely
    # free, surely takes it or stops git, _rdp_git_waits). A -- ends the options only when reached neither.
    # form: the pattern a word matches when, as the value of the format or sort option before it
    # (_rdp_git_signature_value), it runs gpg.program; judged before the word is skipped as that value.
    skip = ended = held = data = False
    form = None
    for word in after:
        if form is not None and form.search(word):
            return word
        name = word.split("=", 1)[0]
        if sub in _RDP_GIT_TRANSPORTS and _rdp_git_helper_url(word):
            return word
        if ended:
            continue
        if word == "--" and not skip and not held and sub in _RDP_GIT_DASHDASH_ENDS:
            # The end of the options (_RDP_GIT_DASHDASH_ENDS): every word after it is a path.
            ended = True
            continue
        # The next word: free after a value; surely a value after a surely free word that surely takes one,
        # an abbreviation of a value-taking option included (git commit --mess --mess -- -S gives the second
        # --mess as the message, and -- ends the options); possibly one after any word that may take one (a
        # word naming both a value-taking option and one taking none, an option of a word that may itself be
        # a value, a long option missing from the value tables: git show --word-diff-regex --grep --ext-diff
        # gives --grep as the regex and runs diff.external, _rdp_git_may_take). Only a word surely a value is
        # skipped unjudged (git commit --mess --gpg-sign gives --gpg-sign as the message); a word possibly
        # one is still judged, which only refuses.
        takes = _rdp_git_takes_next(sub, word)
        held, data = (False, False) if data else (
            _rdp_git_may_take(sub, word), (takes or _rdp_git_waits(sub, word)) and not held)
        if skip:
            # An option's operand is data, a format-looking one included (git log --grep --format=%G
            # searches for --format=%G); a URL operand was judged above, since git still reaches it.
            skip = False
            form = None
            continue
        form = _rdp_git_signature_value(sub, name)
        if form is not None and form.search(word):
            return word
        form = form if name == word else None
        if word == "--help":
            return word
        if name.startswith("--") and name not in _RDP_GIT_NOT_PROGRAM_OPTIONS and word != "--exec-path" and (
                _rdp_long_option(name, _RDP_GIT_PROGRAM_OPTIONS + owned)):
            return word
        skip = data
        if len(word) > 1 and word.startswith("-") and not word.startswith("--"):
            for ch in word[1:]:
                if ch in letters:
                    return word
                if ch in valued:
                    break
    return None


def _rdp_git_helper_url(word):
    """Whether a word (or the value of an option word, after =) is a URL written TRANSPORT::ADDRESS, for
    which git runs the remote helper git-remote-TRANSPORT found on PATH."""
    value = word.split("=", 1)[-1] if word.startswith("-") else word
    head, sep, _rest = value.partition("::")
    return bool(sep) and head != "" and all(ch.isascii() and (ch.isalnum() or ch in "+.-") for ch in head)


def _rdp_git_off_allowlist(words):
    """The word of a plain git command that names a subcommand off _RDP_GIT_ALLOWED (matched exactly), or
    the global option this hook does not know that hides the subcommand; None when the command is no git
    command, runs an allowlisted subcommand, or is git alone or with one of _RDP_GIT_INFO_OPTIONS only."""
    if _rdp_basename(words[0]).casefold() != "git":
        return None
    if len(words) == 1 or (len(words) == 2 and words[1] in _RDP_GIT_INFO_OPTIONS):
        return None
    at, _configured = _rdp_git_subcommand(words)
    if at is None:
        return next((word for word in words[1:] if word.startswith("-") and word not in _RDP_GIT_GLOBAL_VALUED
                     and word not in _RDP_GIT_GLOBAL_FLAGS and not (
                         word.startswith("--") and word.split("=", 1)[0] in _RDP_GIT_GLOBAL_VALUED)), words[-1])
    return None if words[at] in _RDP_GIT_ALLOWED else words[at]


def _rdp_git_runs_program(words):
    """The first word of a plain git command that makes it run a program, named in it or configured; None when
    the command is no git command or runs none that way: a global -p or --paginate (the pager), an option of
    _RDP_GIT_PROGRAM_OPTIONS or of the subcommand's _RDP_GIT_SUB_PROGRAM_OPTIONS (abbreviated or not), a
    signature placeholder or atom in a --format, --pretty or shortlog --group value
    (_RDP_GIT_SIGNATURE_FORMAT) or a signature sort key in a for-each-ref, branch or tag --sort value
    (_RDP_GIT_SIGNATURE_SORT), each running gpg.program, a short option of _RDP_GIT_PROGRAM_LETTERS (grep -O,
    rebase and difftool -x, clone -u and -c, commit -S and -p, tag -s, -u and -v), a --help after the
    subcommand (git help's viewer), a TRANSPORT::ADDRESS URL for a subcommand of _RDP_GIT_TRANSPORTS, or a
    subcommand of _RDP_GIT_PROGRAM_SUBCOMMANDS. Options are read with their operands first: a word taken as
    the value of an option before it (attached, or the next word after one of _RDP_GIT_PROGRAM_LETTERS or
    _RDP_GIT_NEXT_LONG) is no option, so git log --grep --ext-diff searches for --ext-diff. Every other word
    is judged up to a -- ending the options of a subcommand of _RDP_GIT_DASHDASH_ENDS, one the scan reaches
    surely free (the word before it may not take it as a value, or is surely a value itself:
    git grep -e -e -- -O), after which a word is a path (git diff --cached -- --ext-diff); for any other
    subcommand, a word after -- is judged too. Behind a global option this hook does not know, every word that
    may be the subcommand is tried."""
    if _rdp_basename(words[0]).casefold() != "git":
        return None
    at, _configured = _rdp_git_subcommand(words)
    candidates = [at] if at is not None else [j for j in range(1, len(words)) if not words[j].startswith("-")]
    for j in candidates:
        found = _rdp_git_program_under(words[j].casefold(), words[1:j], words[j + 1:])
        if found is not None:
            return found
    if at is None:
        return _rdp_git_program_under("", (), words[1:])
    return None


def _rdp_git_reads(words):
    """Whether a plain command is a git command that only reads: its subcommand is on _RDP_GIT_READS, or is
    config in a read form (_rdp_git_config_reads); no global option sets configuration, no word is an
    --output option (abbreviated or not), which writes a file, and the command runs no program by its
    options (_rdp_git_runs_program); and, for log, show and diff, no option asks for patch output
    (_RDP_GIT_PATCH_OPTIONS, or _RDP_GIT_PATCH_LETTERS in a word of short options, each option taken with
    its value, attached or the next word, so -Sconfig and --grep -p ask for none), up to the -- ending
    the options. Configuration set before the session can still make such a read run a program (git diff
    runs diff.external and textconv filters by default); see the residue."""
    if _rdp_basename(words[0]) != "git":
        return False
    at, configured = _rdp_git_subcommand(words)
    if at is None or configured or _rdp_git_runs_program(words) is not None:
        return False
    sub = words[at]
    if sub == "config":
        return _rdp_git_config_reads(words[at + 1:])
    if sub not in _RDP_GIT_READS:
        return False
    for word in words[at + 1:]:
        name = word.split("=", 1)[0]
        if len(name) > 2 and "--output".startswith(name):
            return False
    if sub not in ("log", "show", "diff"):
        return True
    # A word is skipped as a value only after an option surely taking it that no word before may take
    # (git log --default --grep -p asks for patch output, its --grep the value of --default), and a -- ends
    # the options only when no word before may take it (_rdp_git_may_take).
    skip = held = False
    for word in words[at + 1:]:
        if skip:
            skip = held = False
            continue
        if word == "--" and not held:
            break
        prev, held = held, _rdp_git_may_take(sub, word)
        name = word.split("=", 1)[0]
        if name.startswith("--"):
            if _rdp_long_option(name, _RDP_GIT_PATCH_OPTIONS):
                return False
            skip = name in _RDP_GIT_DIFF_NEXT_LONG and name == word and not prev
            continue
        if not word.startswith("-"):
            continue
        for k, ch in enumerate(word[1:], 1):
            if ch in _RDP_GIT_PATCH_LETTERS:
                return False
            if ch in _RDP_GIT_DIFF_VALUED:
                skip = ch in _RDP_GIT_DIFF_NEXT and k == len(word) - 1 and not prev
                break
    return True


def _rdp_git_config_reads(args):
    """Whether the words after git config only read, judged by position: modifiers only
    (_RDP_GIT_CONFIG_MODIFIERS, --type=TYPE, and _RDP_GIT_CONFIG_VALUED with their value, glued after = or
    the next word), then exactly one key (no option, and holding a dot, so no subcommand such as edit), the
    implicit read; or a read action of _RDP_GIT_CONFIG_READS followed by the number of operands it takes,
    none an option. Every other form may write: git config stops reading options at its first operand, so
    a trailing word is a value there (git config core.worktree get, and git config core.bare --local, set
    the key)."""
    i = 0
    while i < len(args):
        name = args[i].split("=", 1)[0]
        if args[i] in _RDP_GIT_CONFIG_MODIFIERS or (name in _RDP_GIT_CONFIG_VALUED and name != args[i] and
                                                    name.startswith("--")):
            i += 1
        elif args[i] in _RDP_GIT_CONFIG_VALUED:
            i += 2
        else:
            break
    if i >= len(args):
        return False
    if len(args) == i + 1 and not args[i].startswith("-"):
        return "." in args[i]
    if args[i] not in _RDP_GIT_CONFIG_READS:
        return False
    least, most = _RDP_GIT_CONFIG_READS[args[i]]
    operands = args[i + 1:]
    return least <= len(operands) <= most and not any(word.startswith("-") for word in operands)


def _rdp_git_configures(words):
    """The first word of a plain git command that sets git configuration; None when the command is no git
    command or sets none. Configuration can make any git command, a read among them, run a program
    (diff.external, a diff driver's command or textconv, core.pager and pager.*, core.editor,
    core.fsmonitor, core.hooksPath, an alias, a filter or merge driver, credential.helper, gpg.program), so
    no key list closes it: a -c or --config-env global, and a git config call in any form but a read
    (_rdp_git_config_reads), whatever its key, sets it. Behind a global option this hook does not know, a
    config, -c or --config-env word anywhere does."""
    if _rdp_basename(words[0]).casefold() != "git":
        return None
    at, _configured = _rdp_git_subcommand(words)
    if at is None:
        return next((word for word in words[1:] if word.casefold() == "config" or
                     word.split("=", 1)[0] in ("-c", "--config-env")), None)
    for word in words[1:at]:
        if word.split("=", 1)[0] in ("-c", "--config-env"):
            return word
    if words[at].casefold() == "config" and not _rdp_git_config_reads(words[at + 1:]):
        return words[at]
    return None


def _rdp_moves_repository(words):
    """The first word of a plain git command that may change where the repository or its work tree is; None
    when the command is no git command or changes neither: a word naming a key or option of
    _RDP_REPOSITORY_KEYS (compared without regard to case, anywhere in the word, so --separate-git-dir=PATH
    is found). A git config read changes nothing, and every other config call, like every -c or
    --config-env global, is refused before this (_rdp_git_configures). A git command that only reads
    (_rdp_git_reads) is not judged here."""
    if _rdp_basename(words[0]).casefold() != "git":
        return None
    at, _configured = _rdp_git_subcommand(words)
    if at is not None and words[at].casefold() == "config":
        return None
    for word in words[1:]:
        if any(key in word.casefold() for key in _RDP_REPOSITORY_KEYS):
            return word
    return None


# The programs of rule 4's allowlist that write no file, so they may name a git directory or a git
# configuration file: the registry readers and programs that only print or test.
_RDP_GIT_DIR_READERS = _RDP_REGISTRY_READERS | frozenset((
    "echo", "printf", "test", "pwd", "true", "false", "date", "basename", "dirname", "realpath", "readlink",
    "du", "df"))
# The last components of git's global and system configuration files (~/.gitconfig, /etc/gitconfig).
_RDP_GIT_CONFIG_FILES = (".gitconfig", "gitconfig")


def _rdp_names_git_dir(words, cwd=None):
    """The first word of a plain command that may write git configuration or hooks by path; None when no
    word does, or the command only reads (_RDP_GIT_DIR_READERS, or a git read, judged by the caller). A
    word does when a value of it (_rdp_word_values) holds a .git component (.git/config, .git/hooks,
    -C .git, a quoted glob matching it), ends in a global or system configuration file
    (_RDP_GIT_CONFIG_FILES) or holds git/config (the XDG file), or, from a cwd inside a .git directory, is
    relative; and a git --template option, which copies hooks into the git directory. A configuration
    file or hook copied, linked or moved into place would make a later git command, a read among them,
    run a program."""
    if _rdp_basename(words[0]) in _RDP_GIT_DIR_READERS:
        return None
    inside = False
    if isinstance(cwd, str) and os.path.isabs(cwd):
        inside = any(part.casefold() == ".git" for form in (os.path.normpath(cwd), os.path.realpath(cwd))
                     for part in form.split("/"))
    git = _rdp_basename(words[0]).casefold() == "git"
    for word in words[1:]:
        folded = word.casefold()
        if git and folded.split("=", 1)[0].startswith("--template"):
            return word
        for value in _rdp_word_values(folded):
            if inside and not os.path.isabs(value):
                return word
            parts = [part for part in os.path.normpath(value).split("/") if part]
            if any(_rdp_component_names(part, (".git",)) for part in parts):
                return word
            if parts and _rdp_component_names(parts[-1], _RDP_GIT_CONFIG_FILES):
                return word
            if any(parts[k:k + 2] == ["git", "config"] for k in range(len(parts))):
                return word
    return None


# The writers of rule 4's allowlist that never recurse into, move or remove a directory named as an
# operand, so a directory holding the common git directory may be one of their operands: touch sets its
# times, mkdir leaves an existing directory as it is (its -m applies only to a directory it makes), uniq
# writes one output file, and cut, tr, egrep and fgrep write only their standard output.
_RDP_HOLDER_SAFE = frozenset(("touch", "mkdir", "uniq", "cut", "tr", "egrep", "fgrep"))
# The options of the writers whose effect on a directory holding the common git directory depends on them
# (cp, mv and rm of GNU coreutils 9.7; ln of GNU coreutils and of the Rust coreutils a host may install;
# rmdir), read from each --help: a short option is LETTER, LETTER/NAME (no value) or LETTER:NAME (a value,
# glued or the next word); a long option is NAME, NAME: (a value, after = or the next word) or NAME= (a
# value only after =). A long option may be abbreviated to a unique prefix. A word that is no option of
# the table leaves the command unmodelled, and every directory holding the git directory among its words
# is refused.
_RDP_WRITER_OPTIONS = dict((
    ("cp", ("a/archive b d f H i l L n P p r/recursive R/recursive s S:suffix t:target-directory "
            "T/no-target-directory u v x Z",
            "archive attributes-only backup= copy-contents debug force interactive link dereference no-clobber "
            "no-dereference preserve= no-preserve: parents recursive reflink= remove-destination sparse: "
            "strip-trailing-slashes symbolic-link suffix: target-directory: no-target-directory update= "
            "verbose keep-directory-symlink one-file-system context= help version")),
    ("mv", ("b f i n u v Z S:suffix t:target-directory T/no-target-directory",
            "backup= debug exchange force interactive no-clobber no-copy strip-trailing-slashes suffix: "
            "target-directory: no-target-directory update= verbose context= help version")),
    ("ln", ("b d F f i L n P r s v h V S:suffix t:target-directory T/no-target-directory",
            "backup= directory force interactive logical no-dereference physical relative symbolic suffix: "
            "target-directory: no-target-directory verbose help version")),
    ("rm", ("f i I r/recursive R/recursive d/dir v",
            "force interactive= one-file-system no-preserve-root preserve-root= recursive dir verbose help "
            "version")),
    ("rmdir", ("p v h V", "ignore-fail-on-non-empty parents verbose help version"))))


def _rdp_writer_parse(name, args):
    """The words args of the writer name (_RDP_WRITER_OPTIONS) as its option parser reads them, options and
    operands in any order until --: (options, operands, targets, values), options the long names given,
    targets the -t values, values the (name, value) pairs of every other option given a value; None when
    the program is not in the table or a word is no option of it (an unknown, ambiguous or misused one)."""
    spec = _RDP_WRITER_OPTIONS.get(name)
    if spec is None:
        return None
    short = dict((token[0], (token[2:] or token[0], token[1:2] == ":")) for token in spec[0].split())
    longs = dict((token.rstrip(":="), token[-1] if token[-1] in ":=" else "") for token in spec[1].split())
    options, operands, targets, values = set(), [], [], []
    i, ended = 0, False
    while i < len(args):
        word = args[i]
        i += 1
        if ended or word == "-" or not word.startswith("-"):
            operands.append(word)
            continue
        if word == "--":
            ended = True
            continue
        if word.startswith("--"):
            key, eq, value = word[2:].partition("=")
            matches = [n for n in longs if n == key] or [n for n in longs if n.startswith(key)]
            if len(matches) != 1 or (eq and not longs[matches[0]]):
                return None
            key = matches[0]
            if longs[key] == ":" and not eq:
                if i >= len(args):
                    return None
                value, eq = args[i], "="
                i += 1
            options.add(key)
            if eq:
                (targets if key == "target-directory" else values).append((key, value))
            continue
        for k in range(1, len(word)):
            if word[k] not in short:
                return None
            key, valued = short[word[k]]
            options.add(key)
            if valued:
                value = word[k + 1:]
                if not value:
                    if i >= len(args):
                        return None
                    value = args[i]
                    i += 1
                (targets if key == "target-directory" else values).append((key, value))
                break
    return (options, operands, [value for _key, value in targets], values)


def _rdp_writer_reaches(name, parsed, words, relation):
    """The first word of the parsed (_rdp_writer_parse) cp, mv, ln, rm or rmdir command that can delete,
    move or recursively rewrite the common git directory, or write a path that is it or lies inside it;
    None when it cannot. relation(path) says whether path is the git directory or inside it ("inside"),
    a directory holding it ("holds"), or neither (None). A holding directory (an ancestor) is reached only
    by rm with -r, -R or -d (--recursive, --dir), rmdir of it, mv of it as a source (or as the destination
    of --exchange, which swaps the two), and cp of it as a source with -r, -R or -a (a copy elsewhere, from
    which a later copy back, or a hard link made with -l, would rewrite the git directory). Every path cp,
    mv and ln write (the destination itself under -T, else the destination joined to each source's last
    component, or to the whole source under cp --parents, and the working directory for ln with one
    operand) may be neither the git directory, inside it, nor a holding directory (x/. merges into the
    destination itself). ln never recurses (its -r is --relative), so its sources may hold the git
    directory. A word whose value is not modelled (a holding directory spelled as an option, which a
    POSIXLY_CORRECT parser reads as an operand, or a backup suffix holding a slash) is refused."""
    options, operands, targets, values = parsed
    for word in words[1:]:
        if word.startswith("-") and word not in operands and word not in targets and relation(word):
            return word
    for key, value in values:
        if relation(value) or (key == "suffix" and "/" in value):
            return value
    if name == "rmdir" or (name == "rm" and options & frozenset(("recursive", "dir"))):
        return next((word for word in operands if relation(word)), None)
    if name == "rm":
        return None
    if targets:
        dests, sources = targets, operands
    elif len(operands) >= 2:
        dests, sources = operands[-1:], operands[:-1]
    else:
        dests, sources = (["."], operands) if name == "ln" else ([], operands)
    if name == "mv" or (name == "cp" and options & frozenset(("recursive", "archive"))):
        hit = next((word for word in sources if relation(word)), None)
        if hit is not None:
            return hit
    if name == "mv" and "exchange" in options:
        hit = next((word for word in dests if relation(word)), None)
        if hit is not None:
            return hit
    for dest in dests:
        for source in sources:
            if "no-target-directory" in options:
                result = dest
            elif "parents" in options:
                result = os.path.join(dest, source.lstrip("/"))
            else:
                result = os.path.join(dest, os.path.basename(source.rstrip("/")))
            if relation(result):
                return source
    return None


def _rdp_names_common_dir(words, cwd, guard, dispatch=False):
    """The first word (with the reason, where it is not a path) of a plain command that may write, move or
    remove the session repository's common git directory, which holds the binding record, judged by where
    its words RESOLVE, whatever their spelling (a separated git directory not named .git, a symbolic link, a
    relative path); None when the command only reads (_RDP_GIT_DIR_READERS, or a git read, judged by the
    caller) or no word does. guard is (common, failure): common the resolved common git directory, or None
    when there is no repository to protect; failure the detail when it cannot be located, which refuses
    every command but a read. A word does when a value of it (_rdp_word_values), joined to the session cwd
    and taken both as written and with every symbolic link followed, compared without regard to case, is
    the common git directory or lies inside it. A directory HOLDING it is refused only where the program
    can delete, move or recursively rewrite it (_rdp_writer_reaches, for cp, mv, ln, rm and rmdir); a
    program of _RDP_HOLDER_SAFE never can, so writing a new file or directory into it (touch ./f, mkdir d,
    cp x .) is not refused, and any other program but git (chmod, whose mode alone can cut every path to
    the git directory, opf) is refused whenever a value of a word holds it. Git writes its own directory
    through no pathspec, so a git pathspec may name a parent (git add .), and a declared dispatch command
    (dispatch true) writes no git directory, so its words may name a holding directory (--workdir . or the
    repository root); a word that is the git directory or lies inside it is refused to both."""
    if _rdp_basename(words[0]) in _RDP_GIT_DIR_READERS:
        return None
    common, failure = guard
    if failure:
        return "{}: {}".format(words[0], failure)
    if common is None:
        return None
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return "{}: the session cwd {!r} is not an absolute path, so its words cannot be resolved".format(
            words[0], cwd)
    name = _rdp_basename(words[0])
    git = name.casefold() == "git"
    held = common.casefold().rstrip("/") + "/"

    def relation(value):
        joined = os.path.join(cwd, value)
        found = None
        for form in (os.path.normpath(joined), os.path.realpath(joined)):
            folded = form.casefold().rstrip("/") + "/"
            if folded.startswith(held):
                return "inside"
            if held.startswith(folded):
                found = "holds"
        return found
    holds = None
    for word in words[1:]:
        for value in _rdp_word_values(word):
            try:
                found = relation(value)
            except (OSError, ValueError) as exc:
                return "{}: it cannot be resolved ({})".format(word, exc)
            if found == "inside":
                return word
            if found == "holds" and holds is None:
                holds = word
    if git or dispatch or name in _RDP_HOLDER_SAFE:
        return None
    parsed = _rdp_writer_parse(name, words[1:])
    if parsed is None:
        return holds
    try:
        return _rdp_writer_reaches(name, parsed, words, relation)
    except (OSError, ValueError) as exc:
        return "{}: a path it writes cannot be resolved ({})".format(words[0], exc)


def _rdp_not_plain(cfg, names, why):
    """The UNVERIFIABLE message for a command that is not a provably plain command, or a plain command that
    names a declared dispatch command other than as its command word."""
    name = names[0] if names else sorted(cfg["commands"])[0]
    return ("unverifiable", "the command {} ({}), so the hook cannot tell whether it runs a review dispatch, "
            "which brief it reads or where; in a session whose registry binds review dispatch, every Bash "
            "call must be one plain command: a command word on the allowlist of plain programs, literal "
            "printable ASCII words only (a single-quoted segment, or a double-quoted one holding no shell "
            "character, may appear inside a word), no variable, glob, operator, redirection or second "
            "command; split the work into plain calls, and "
            "write a dispatch as {} [OPTIONS] {} PATH with the brief option last".format(
                "names the declared dispatch command " + name + " other than as a plain dispatch" if names
                else "is not plain", why, name, cfg["brief_option"]))


def _rdp_judge(data, cfg, root, reg_dir, tool_input, foreign=False, guard=(None, None)):
    """The decision for a Bash payload in a session with a well-formed binding. Every command that is not
    provably plain (_rdp_plain_words) is UNVERIFIABLE, whatever it names: a name assembled at run time
    (a variable joined to text, a glob, command output) cannot be seen in the raw text. A provably plain
    command whose command word is a declared command gets the pin check; a plain command that names a
    declared command elsewhere (_rdp_mentions) is UNVERIFIABLE, and one that names none is allowed. Names
    are compared without regard to case, since a case-insensitive filesystem runs ORCH-DISPATCH as
    orch-dispatch."""
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return ("unverifiable", "the Bash tool_input carries no command string")
    commands = cfg["commands"]
    words, why = _rdp_plain_words(command, commands)
    if words is None:
        return _rdp_not_plain(cfg, _rdp_mentions(command, commands), why)
    # A git command that runs a program by its options or subcommand can run any shell text, which no word
    # check sees (.aiqt inside a pager command), so it is refused whatever it names.
    program = _rdp_git_runs_program(words)
    if program is not None:
        return ("deny", "the git command runs a program ({}): an option or subcommand that runs a program "
                "named in it or configured (the pager of -p, --paginate or grep -O, an external diff, a "
                "textconv or smudge filter, a signature check, rebase or difftool -x, an upload, receive or "
                "archive program, an exec path, a signing or signature program, a merge strategy, a patch "
                "mode's diff filter, a trailer command, a remote helper, git help's viewer, bisect run, "
                "submodule foreach, filter-branch) can run shell "
                "text that writes, moves or removes the registry, so in a session whose registry binds "
                "review dispatch it is refused whatever it names, and an operator runs it outside the "
                "session".format(program))
    # A git subcommand off the allowlist may run a program by an option no table here holds (instaweb
    # --httpd), so it is refused whatever it names.
    off = _rdp_git_off_allowlist(words)
    if off is not None:
        return ("deny", "the git command runs no subcommand on the allowlist ({}): in a session whose "
                "registry binds review dispatch a git command may run only {}, since any other subcommand "
                "(instaweb, difftool, mergetool, send-email, filter-branch, bisect, submodule, daemon, "
                "web--browse, the credential commands, help, an alias, an external git-NAME program, or one "
                "behind a global option this hook does not know) can run a program whose shell text writes, "
                "moves or removes the registry, and an operator runs it outside the session".format(
                    off, ", ".join(sorted(_RDP_GIT_ALLOWED))))
    # A git command that only reads treats every word as data: it neither writes the registry nor moves
    # the repository, whatever its words name.
    reads = _rdp_git_reads(words)
    touched = None if reads else _rdp_names_registry(words, data.get("cwd"))
    if touched is not None:
        return ("deny", "the command names the orchestration registry ({}) that binds review dispatch in "
                "this session; a Bash call that may write, move or remove it would switch this check off, so "
                "only a read ({}) may name it, and an operator changes the registry outside the "
                "session".format(touched, ", ".join(sorted(_RDP_REGISTRY_READERS))))
    configures = _rdp_git_configures(words)
    if configures is not None:
        return ("deny", "the git command sets git configuration ({}); configuration can make any git "
                "command, a read among them, run a program (diff.external, a textconv or filter driver, "
                "core.pager, core.fsmonitor, core.hooksPath, an alias), so in a session whose registry binds "
                "review dispatch every git config write, whatever the key, and every -c or --config-env "
                "global is refused, and an operator changes git configuration outside the "
                "session".format(configures))
    gitdir = None if reads else _rdp_names_git_dir(words, data.get("cwd"))
    if gitdir is not None:
        return ("deny", "the command names a git directory, a git configuration file or a hook template "
                "({}); a configuration file or hook written there can make any git command, a read among "
                "them, run a program, so in a session whose registry binds review dispatch only a read may "
                "name one, and an operator changes them outside the session".format(gitdir))
    dispatch = _rdp_basename(words[0]).casefold() in {name.casefold() for name in commands}
    common = None if reads else _rdp_names_common_dir(words, data.get("cwd"), guard, dispatch)
    if common is not None:
        return ("deny", "the command may write, move or remove the repository's common git directory {} "
                "({}), which holds the review dispatch binding record; in a session whose registry binds "
                "review dispatch only a read may name it or a path inside it, and a directory holding "
                "it only a command that cannot delete, move or recursively rewrite that directory, however "
                "the path is spelled; an operator changes it outside the session".format(
                    guard[0] or "(which cannot be located)", common))
    moved = None if reads else _rdp_moves_repository(words)
    if moved is not None:
        return ("deny", "the git command changes where the repository or its work tree is ({}); in a session "
                "whose registry binds review dispatch, a moved top level would be checked against another "
                "registry, so an operator changes core.worktree, core.bare, configuration includes and "
                "separated git directories outside the session".format(moved))
    word = _rdp_basename(words[0])
    if word.casefold() not in {name.casefold() for name in commands}:
        # The one dispatch's own arguments may mention a declared name (in a brief path, say); a plain
        # command that is no dispatch may not, since it could run one (a find -exec, say).
        named = _rdp_mentions(" ".join(words), commands)
        if named:
            return _rdp_not_plain(cfg, named, "it names {} other than as the command word of a "
                                  "dispatch".format(named[0]))
        return ("allow", "")
    args = words[1:]
    option = cfg["brief_option"]
    if len(option) == 2 and any(a.startswith(option + "=") for a in args):
        return ("deny", "the {} dispatch writes the short brief option as {}=PATH, which a getopt parser "
                "reads as a value starting with an equals sign; write {} PATH".format(word, option, option))
    briefs = _rdp_brief_args(args, option)
    if len(briefs) != 1 or not briefs[0] or briefs[0] == "-":
        return ("deny", "the {} dispatch must pass exactly one brief file as {} PATH (found "
                "{})".format(word, option, len(briefs)))
    last = (len(args) >= 2 and args[-2] == option) or (len(option) > 2 and args[-1].startswith(option + "=")) \
        or (len(option) == 2 and args[-1].startswith(option) and args[-1] != option)
    if not last or "--" in args[:-1]:
        # A parser this hook does not know may take the brief from an alias or a grouped short option
        # (`-b`, `-xb`); given last, the declared option is the one a last-wins parser keeps.
        return ("deny", "the {} dispatch must give its brief last, as {} PATH with no word "
                "after it and no -- before it, so no other option word can replace it".format(word, option))
    cwd = data.get("cwd")
    if root is None:
        return ("unverifiable", "git cannot resolve the session repository from the cwd {!r}, so the "
                "{} dispatch cannot be checked".format(cwd, word))
    if isinstance(foreign, str):
        return ("unverifiable", "{}; the {} dispatch is withheld".format(foreign, word))
    if foreign:
        return ("unverifiable", "the session repository {} is not the repository of the registry that "
                "binds the {} dispatch ({}): a nested repository, or a top level moved by core.worktree, "
                "would supply its own commit and authority".format(root, word, reg_dir))
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return ("unverifiable", "the session cwd {!r} is not an absolute path, so the directory the "
                "{} dispatch runs in cannot be resolved".format(cwd, word))
    brief = briefs[0] if os.path.isabs(briefs[0]) else os.path.join(cwd, briefs[0])
    for form in (os.path.normpath(brief), os.path.realpath(brief)):
        if form.startswith(("/proc/", "/dev/")) or form in ("/proc", "/dev"):
            return ("unverifiable", "the brief {} is under /proc or /dev, which name a different file in "
                    "this hook's process than in the dispatcher's".format(brief))
    text, why = _rdp_read_brief(brief, cfg["max_brief_bytes"])
    if text is None:
        return ("unverifiable", why)
    found = _rdp_labels(text, cfg["labels"])
    targets = found["target"]
    if not targets:
        return ("deny", "the brief {} has no {!r} line; declare revision, working-tree or "
                "not-a-review".format(brief, cfg["labels"]["target"]))
    if len(targets) > 1:
        return ("unverifiable", "the brief {} has {} {!r} lines".format(
            brief, len(targets), cfg["labels"]["target"]))
    target = targets[0].strip()
    if target not in _RDP_TARGETS:
        return ("deny", "the brief {} declares the unknown target {!r}; use revision, "
                "working-tree or not-a-review".format(brief, target))
    if target != "revision":
        # the guard-events row is the only record of this departure from revision reconciliation, so a failed
        # append is reported on the note (the allow stands)
        warn = _orch_event_warn(reg_dir, "review-dispatch-pin", "allow-declared-target",
                                "{}: {}".format(brief, target))
        return ("note", "AIQT rule vfxcmt: the brief {} declares target {}, so no revision was "
                "reconciled; a review of committed work must pin it.{}".format(brief, target,
                                                                            _orch_warn_tail(warn)))
    ambient = sorted(k for k in os.environ if k in _RDP_AMBIENT_GIT)
    if ambient:
        return ("unverifiable", "the environment sets {}, which moves the repository, index, object store "
                "or history git reads; the {} dispatch runs with it, so the revision it reviews is not the "
                "one this hook reconciles in {}; unset it and dispatch again".format(
                    ", ".join(ambient), word, root))
    return _rdp_reconcile(cfg, found, root, brief, root, reg_dir)


def review_dispatch_pin(data):
    """vfxcmt, PreToolUse Bash: a review dispatch made through a registry-declared command pins an
    immutable, authoritative revision whose changed set is the declared review set and whose declared
    paths carry no uncommitted state (a checked-out submodule is compared by its HEAD only), BEFORE the
    dispatch runs. Inert without a review_dispatch binding; in a session a binding scopes, a Bash call that
    is not one provably plain command (its command word on the allowlist, or a declared dispatch command)
    is withheld, a plain one naming the registry is refused unless it only reads, a plain git command that
    sets configuration (every git config write, every -c or --config-env global) or may move the
    repository is refused, as is a plain command that may write into a git directory or a git
    configuration file (a read-only git subcommand's words taken as data, unless it carries an option that
    runs a program), and a malformed registry or binding, or a linked worktree whose main worktree cannot be located,
    withholds every Bash call.
    A refusal denies and names its reason; a cannot-evaluate denies with an UNVERIFIABLE: prefix; a
    declared non-revision target, or a branch label that does not resolve to the pin, is allowed with a
    note. A crash reaches main's PreToolUse fail-closed exit 2.
    Residual (disclosed in the manifest residue): a git command whose operand is spelled like an option
    after option-value consumption may be falsely denied (git stash push -m --patch, whose message --patch
    is judged as the patch option); a fail-safe refusal of a harmless form, never an allow.
    Hookless dispatchers run it as a preflight: `aiqt_hooks.py review_dispatch_pin` with the payload on
    stdin."""
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("vfxcmt")
    if tool_name != "Bash":
        return _allow()
    kind, message = _rdp_decide(data)
    if kind in ("deny", "unverifiable"):
        prefix = "UNVERIFIABLE: " if kind == "unverifiable" else ""
        return _deny("{}AIQT rule vfxcmt: review dispatch withheld: {}.".format(prefix, message),
                     "AIQT guardrail: {}review dispatch withheld (rule vfxcmt).".format(prefix))
    if kind == "note":
        return _allow_note(message)
    return _allow()


def orch_prompt_stamp(data):
    """tstamp/estsep, UserPromptSubmit (recorder, never blocks): stamp genuine human input from the
    clock, classify a prompt matching a registered wake as timer-originated, and inject the measured
    gap as context so quiet-duration reasoning uses a measured figure."""
    root = _orch_root(data)
    if root is None:
        return _allow()
    status, reg = _orch_registry(root)
    if status != "ok":
        return _allow()
    if not _orch_scope_live(reg, root, data.get("session_id")):
        return _allow()  # only the holder session stamps/resets the shared turn-state (CX-M4b)
    prompt = data.get("prompt")
    ts = _orch_turn_state(root) or {}
    digest = __import__("hashlib").sha256(
        (prompt or "").encode("utf-8", "replace")).hexdigest() if isinstance(prompt, str) else ""
    _wds = ts.get("wake_digests")
    _wds = _wds if isinstance(_wds, list) else []  # a non-list wake_digests never enables substring match
    timer_originated = digest and digest in _wds
    prev = _orch_parse_utc(ts.get("last_human_input_utc"))
    if not timer_originated:
        ts["last_human_input_utc"] = _orch_now().isoformat()
        ts["stop_denials"] = 0
        ts["schedule_denials"] = 0
        ts.pop("schedule_basis", None)
        unsaved = _orch_save_turn_state(root, ts)
        if unsaved is None:
            return _allow()
        # the stamp is this recorder's record; the prompt proceeds and the unwritten stamp is noted
        return _allow_note("AIQT guardrail: this prompt was read as genuine human input, but turn-state.json "
                           "could not be written ({}), so its time was not stamped and the denial counters were "
                           "not reset; record it manually (nocncl).".format(unsaved))
    # one-shot: consume the matched wake digest so a later prompt with identical text (including genuine
    # human input) is not perpetually misclassified as timer-originated (R2-CM4/CX-M7).
    wd = list(ts.get("wake_digests") or [])
    if digest in wd:
        wd.remove(digest)  # consume exactly ONE token, so a second identical wake is still recognized
    ts["wake_digests"] = wd
    unsaved = _orch_save_turn_state(root, ts)
    gap = "unknown (no prior stamp; an unknown duration authorizes nothing)"
    if prev is not None:
        gap = "{:.1f} minutes".format((_orch_now() - prev).total_seconds() / 60.0)
    context = ("[aiqt-orch] This prompt is TIMER-ORIGINATED (a registered wake), not human input. Measured "
               "gap since the last genuine human input: {}.".format(gap))
    if unsaved is not None:
        # the model reads the context line and the operator the systemMessage; neither promises how a later
        # prompt with the same text is classified
        warn = ("turn-state.json could not be written ({}), so consuming this wake's digest failed and how a "
                "later prompt with the same text is classified is uncertain: it may read as timer-originated "
                "or as genuine human input; record it manually (nocncl).".format(unsaved))
        return _context_note("UserPromptSubmit", context + " Additionally, " + warn,
                             "AIQT guardrail: this prompt was read as timer-originated, but " + warn)
    return (0, {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": context}}, None)


# The truncation guard's registry-required deny reasons for each scope it denies, as tools/orch_doctor.py
# and the resume audit report them (_orch_truncation_scope decides the scope exactly as the guard does).
_ORCH_STRICT_SCOPE_FINDINGS = dict((
    ("none", "no orchestration registry on this repository root's ancestor chain or at its git "
             "toplevel"),
    ("cannot-evaluate", "the candidate that decided cannot be confirmed as a registry; it is EITHER the "
                        "nearest directory on this repository root's ancestor chain whose registry probe "
                        "is not a clean not-present, OR, only when every directory on that chain probes as "
                        "a clean not-present, the root's git toplevel, which can lie off the chain "
                        "(core.worktree), so when every directory on the chain probes as a clean not-present "
                        "(no .aiqt entry, or a real .aiqt directory this process can search holding neither "
                        "registry name) the fault is at the git toplevel: at that "
                        "candidate, its .aiqt is not a directory openable without following a symlink and "
                        "searchable (execute permission) by this process, or its first present registry name "
                        "(orchestration.local.json, then orchestration.json) is not a regular file (a "
                        "symlinked registry file included) or cannot be examined"),
    ("toplevel-unopenable", "no registry on this repository root's ancestor chain, and its git toplevel "
                            "cannot be opened as a directory (it is present but not an openable "
                            "directory; a toplevel that does not exist reads as no registry there)"),
))


def _orch_guard_scope_report(root):
    """What orch_truncation_guard decides at its scope check for a Bash call whose cwd is the repository
    root, in the CURRENT mode: (report lines, denies). A cwd the walk cannot carry out denies in every mode;
    an absent or unconfirmable registry denies only in registry-required mode. Shared by
    tools/orch_doctor.py and _orch_resume_audit_findings, so both resume-barrier writers read one scope."""
    scope, found = _orch_truncation_scope(root)
    env = _ORCH_REQUIRE_REGISTRY_ENV
    if scope == "fail":
        return (["truncation guard: a Bash call from the repository root is denied in every mode: its "
                 "cwd %s" % found[0]], True)
    if _orch_registry_required() and scope in _ORCH_STRICT_SCOPE_FINDINGS:
        return (["truncation guard (registry-required mode, %s set to a value other than an off value): "
                 "a Bash call from the repository root is DENIED: %s"
                 % (env, _ORCH_STRICT_SCOPE_FINDINGS[scope])], True)
    if scope == "none":
        return (["truncation guard: inert for a Bash call from the repository root (no registry on its "
                 "ancestor chain or at its git toplevel; with registry-required mode enabled, %s set to a "
                 "value other than an off value, such as 1, it denies instead)" % env], False)
    if scope in _ORCH_STRICT_SCOPE_FINDINGS:
        # Default mode reads a discovery fault as present (the deny-safe direction): the guard is active
        # without a confirmed registry, which is reported as the fault it is, not as a registry found.
        return (["truncation guard: ACTIVE for a Bash call from the repository root because its registry "
                 "discovery hit a fault it reads as present, not because a registry was confirmed: %s "
                 "(with registry-required mode enabled, %s set to a value other than an off value, such as "
                 "1, it denies instead)" % (_ORCH_STRICT_SCOPE_FINDINGS[scope], env)], False)
    return (["truncation guard: ACTIVE for a Bash call from the repository root (a registry entry was "
             "found on its ancestor chain, which can lie above this repository, or at its git "
             "toplevel)"], False)


def _orch_resume_audit_findings(status, reg, root):
    """The ONE resume-audit finding list both resume-barrier writers (orch_resume_audit at SessionStart and
    tools/orch_doctor.py --resume-audit) arm or clear resume-barrier.json from (round 8), so neither clears
    a barrier the other armed for a condition that still holds, PROVIDED both run in the same mode: a
    registry the loader reports bad, the truncation guard's deny at its scope check for a Bash call from the
    root (_orch_guard_scope_report), then the resume probes (over an empty registry when it is bad). Empty
    means clean. The scope deny depends on the CURRENT process's AIQT_ORCH_REQUIRE_REGISTRY (a walk failure
    denies in every mode; an absent or unconfirmable registry scope only in registry-required mode), and
    the barrier does not record the mode, so a doctor run without the variable can clear a barrier a
    registry-required SessionStart armed for a scope deny. Clearing it does not allow any Bash call: the
    barrier only warns (stage BAKE) and the guard denies on its own scope check regardless."""
    findings = []
    if status == "bad":
        findings.append("the orchestration registry could not be read ({})".format(reg))
        reg = {}
    scope_lines, scope_denies = _orch_guard_scope_report(root)
    if scope_denies:
        findings.extend(scope_lines)
    findings.extend(_orch_resume_probes(reg, root))
    return findings


def _orch_resume_probes(reg, root):
    """The resume-audit probes (shared with tools/orch_doctor.py --resume-audit). Returns a list of
    finding strings; empty means the recorded state matches observed reality."""
    findings = []
    rec = reg.get("record") if isinstance(reg.get("record"), dict) else {}
    handoff_path = _orch_path(root, rec.get("handoff"))
    if handoff_path:
        try:
            with open(handoff_path, "r", encoding="utf-8", errors="replace") as fh:
                handoff = fh.read()
        except OSError as exc:
            findings.append("declared handoff unreadable ({}): cannot-evaluate, the barrier holds"
                            .format(exc))
            handoff = ""
        m = re.search(r"^Branch:\s*(\S+)\s*$", handoff, re.MULTILINE)
        if m:
            actual = _head_branch(root)
            if actual is not None and actual != m.group(1):
                findings.append("handoff names branch {} but HEAD is on {}".format(
                    m.group(1), actual))
        m = re.search(r"^Gate:\s*green\s*@\s*([0-9a-f]{7,40})\s*$", handoff, re.MULTILINE)
        if m:
            try:
                env = _isolate_git_env(dict(os.environ))
                head = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                                      capture_output=True, text=True, timeout=5, env=env)
                sha = head.stdout.strip() if head.returncode == 0 else ""
            except Exception:
                sha = ""
            if not sha or not sha.startswith(m.group(1)):
                findings.append("handoff gate marker cites commit {} but HEAD is {}".format(
                    m.group(1), sha[:12] or "unreadable"))
    for key in ("findings", "pending_decisions"):
        path = _orch_path(root, rec.get(key))
        if path:
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.read()
            except OSError as exc:
                findings.append("declared record surface {} unreadable ({}): cannot-evaluate"
                                .format(key, exc))
    lease = reg.get("lease") if isinstance(reg.get("lease"), dict) else None
    if lease:
        path = _orch_path(root, lease.get("path"))
        max_age = lease.get("max_age_hours")
        if path:
            try:
                st = os.stat(path)
                age = _orch_now().timestamp() - st.st_mtime
                if isinstance(max_age, (int, float)) and max_age > 0 and age > max_age * 3600:
                    findings.append("the orchestrator lease is stale (older than {}h)".format(max_age))
                if age < -_ORCH_CLOCK_SKEW:
                    # CONV4-G2 + CONV6-F: a FUTURE lease mtime (clock skew or tamper) is checked whenever
                    # the lease is stattable, NOT only when a max_age horizon happens to be configured.
                    findings.append("the orchestrator lease mtime is in the future (clock skew or tamper)")
            except FileNotFoundError:
                pass  # no lease yet is a legitimate resume state
            except OSError as exc:
                findings.append("declared lease unreadable ({}): cannot-evaluate".format(exc))
    findings.extend(_orch_merge_pending_findings(reg, root))
    findings.extend(_orch_validate_attestations(reg, root))
    findings.extend(_orch_pending_artefact_findings(root))
    return findings


def _orch_merge_pending_findings(reg, root):
    """The cheap in-hook layer of the record-drift check: an OPEN merge_pending row whose pr:N ref
    already has first-parent merge evidence is a divergence finding. tools/check_record_drift.py is
    the authoritative gate; this probe only feeds the resume audit."""
    rec = reg.get("record") if isinstance(reg.get("record"), dict) else {}
    path = _orch_path(root, rec.get("findings"))
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        return ["declared findings register unreadable ({}): cannot-evaluate".format(exc)]
    prs = set()
    for line in text.splitlines():
        if "merge_pending" in line:
            for m in re.finditer(r"\bpr:(\d+)\b", line):
                prs.add(m.group(1))
    if not prs:
        return []
    try:
        env = _isolate_git_env(dict(os.environ))
        log = subprocess.run(["git", "-C", root, "log", "--first-parent", "-n", "500",
                              "--format=%s"], capture_output=True, text=True, timeout=10, env=env)
        subjects = log.stdout if log.returncode == 0 else None
    except Exception:
        subjects = None
    if subjects is None:
        return ["merge evidence unreadable (git log failed): cannot-evaluate"]
    findings = []
    for n in sorted(prs):
        if re.search(r"\(#{}\)".format(re.escape(n)), subjects):
            findings.append("findings row still merge_pending on pr:{} but first-parent "
                            "history already carries its merge".format(n))
    return findings


def _orch_chained_rows(path, prefix):
    """Read a chained register (one JSON object per line; prev is the sha256 of the prior raw line,
    64*'0' for the first; seq is the line number; ids carry the family prefix) and return
    (rows, detail): rows is None when the file is unreadable or ANY line breaks the chain, shape,
    sequence, or id-prefix discipline, so a tampered or malformed register can never read as a
    smaller clean one. A compact inline mirror of the authoritative gate
    (tools/check_mistakes_register.py): the hooks script is standalone in adopter repos and cannot
    import tools/, so the CI gate remains the authoritative checker and this feeds only the audit."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        return (None, "unreadable ({})".format(exc))
    sha = __import__("hashlib").sha256
    rows, prev = [], None
    for n, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except ValueError:
            return (None, "line {} is not JSON".format(n))
        want = "0" * 64 if prev is None else sha(prev.encode("utf-8")).hexdigest()
        if not isinstance(row, dict) or row.get("prev") != want or row.get("seq") != n:
            return (None, "line {} breaks the chain or sequence".format(n))
        rid = row.get("id")
        if not isinstance(rid, str) or not rid.startswith(prefix):
            return (None, "line {} id {!r} lacks the {} prefix".format(n, rid, prefix))
        rows.append(row)
        prev = line
    return (rows, "")


def _orch_register_tip(lines):
    """('ok', seq, digest) for a register's current tip: seq is the line count and digest is the
    sha256 of the last line (the same tip identity tools/check_mistakes_register.py anchors), so a
    yield-time re-anchor can rebind a validated snapshot to the register on disk."""
    if not lines:
        return ("ok", 0, "0" * 64)
    return ("ok", len(lines), _orch_sha256_hex(lines[-1]))


def _orch_register_authority(root, path):
    """FIX 3: the append-only AUTHORITY for a chained register (a compact inline mirror of
    tools/check_mistakes_register.py's verify_append_only; the standalone hooks script cannot import
    tools/). Returns ('ok', seq, digest) when the register on disk is a verified append-only extension
    of an authority the actor cannot trivially forge, its git merge-base state with origin/HEAD when
    the register is tracked, else its <path>.anchor file, with seq/digest identifying the current tip;
    otherwise ('hold', detail, None), a cannot-evaluate the caller routes to a fail-closed HOLD, never
    a clean pass. RESIDUAL (disclosed): the register is itself actor-writable, and this authority only
    checks that the on-disk register is an append-only EXTENSION of its committed (merge-base) or
    anchored prefix. An actor with filesystem write ALONE can therefore APPEND rows past that prefix
    (uncommitted; git status M) and have them counted as the tip, so the git/anchor authority defends
    the register-SWAP and prefix-REWRITE classes but not an appended approved row (A2); the categorical
    residual is closed only by an operator-owned, assistant-non-writable register at the OS layer."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError as exc:
        return ("hold", "register unreadable ({})".format(exc), None)
    env = _isolate_git_env(dict(os.environ))
    rel = None
    try:
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(root))
    except (OSError, ValueError):
        rel = None
    if rel and not rel.startswith(os.pardir + os.sep) and rel != os.pardir:
        try:
            tracked = subprocess.run(["git", "-C", root, "ls-files", "--error-unmatch", rel],
                                     capture_output=True, timeout=10, env=env).returncode == 0
        except (OSError, subprocess.SubprocessError) as exc:
            return ("hold", "cannot determine register tracking ({})".format(exc), None)
        if tracked:
            try:
                base = subprocess.run(["git", "-C", root, "merge-base", "HEAD", "origin/HEAD"],
                                      capture_output=True, text=True, timeout=10, env=env)
                if base.returncode != 0 or not base.stdout.strip():
                    return ("hold", "no computable merge-base with origin/HEAD", None)
                ref = base.stdout.strip()
                old = subprocess.run(["git", "-C", root, "show", "{}:{}".format(ref, rel)],
                                     capture_output=True, text=True, timeout=10, env=env)
            except (OSError, subprocess.SubprocessError) as exc:
                return ("hold", "cannot read the register authority ({})".format(exc), None)
            if old.returncode != 0:
                return ("hold", "cannot read the register at its merge-base authority", None)
            old_lines = old.stdout.splitlines()
            if lines[:len(old_lines)] != old_lines:
                return ("hold",
                        "register is not an append-only extension of its merge-base state", None)
            return _orch_register_tip(lines)
    anchor = path + ".anchor"
    try:
        with open(anchor, "r", encoding="utf-8") as fh:
            a = json.load(fh)
        n, digest = int(a["seq"]), str(a["digest"])
    except (OSError, ValueError, KeyError, TypeError):
        return ("hold", "declared register has no readable anchor authority", None)
    if len(lines) < n:
        return ("hold", "register is shorter than its verified anchor (rows removed)", None)
    if n > 0 and _orch_sha256_hex(lines[n - 1]) != digest:
        return ("hold", "the anchored row was rewritten (tamper or reorder)", None)
    return _orch_register_tip(lines)


def _orch_pointer_resolves(root, sd, pointer):
    """True when a register row's check_ref/evidence pointer resolves to something real: an existing
    relative path contained under the repo root or the state dir, or a guard-events row (the
    "guard-events:<token>" form, matched as a whole token against recorded kind/detail). An absolute
    pointer is rejected (it would let any host file satisfy the check, grdinp). Relevance is NOT
    machine-judgable and is not judged here: a real but misappropriated integrity signal passes
    resolution, a disclosed residual that triage refutes."""
    if not isinstance(pointer, str) or not pointer.strip():
        return False
    if pointer.startswith("guard-events:"):
        token = pointer[len("guard-events:"):].strip()
        rows, _bad = _orch_read_jsonl(os.path.join(sd, "guard-events.jsonl"))
        if not token or rows is None:
            return False
        return any(_orch_token_present(token, "{} {}".format(r.get("kind", ""), r.get("detail", "")))
                   for r in rows)
    if os.path.isabs(pointer):
        return False
    for base in (root, sd):
        rb = os.path.realpath(base)
        cand = os.path.realpath(os.path.join(base, pointer))
        if (cand == rb or cand.startswith(rb.rstrip(os.sep) + os.sep)) and os.path.exists(cand):
            return True
    return False


def _orch_attestation_derive(reg, root, sd, rows):
    """Shared C.2 derivation over a chained attestation register's rows: return (refs, unsub,
    findings), where refs is the sorted set of blocker refs each covered by a fresh, APPROVED row
    whose integrity pointer resolves. The AUDIT (_orch_validate_attestations) and the yield-time
    reader (_orch_attestation_refs, FIX A1) run this SAME logic, so a validated snapshot's stored
    'refs' field is never authoritative: the yield reader recomputes refs from the re-anchored
    register content itself, and a snapshot forged with fabricated refs substantiates none of them.
    RESIDUAL (disclosed, A2): an actor with filesystem write to the register can still APPEND a valid
    approved row (uncommitted; git status M, past the committed or anchored prefix the authority
    checks) whose ref this derivation then substantiates; only an operator-owned, assistant-non-
    writable register closes that categorically."""
    latest = {}
    for row in rows:
        latest[row["id"]] = row
    ext_hours = _orch_validate("staleness", reg.get("staleness"))[1]["external_hours"]
    now = _orch_now()
    refs, unsub, findings = [], [], []
    for rid in sorted(latest):
        row = latest[rid]
        status = row.get("status")
        if status in ("declined", "superseded"):
            continue
        ref = row.get("ref")
        pointer = row.get("check_ref") or row.get("evidence")
        observed = _orch_parse_utc(row.get("ts"))
        age = (now - observed).total_seconds() if observed is not None else None
        fresh = age is not None and -_ORCH_CLOCK_SKEW <= age <= ext_hours * 3600
        if not isinstance(ref, str) or not ref.strip():
            unsub.append(rid)
            findings.append("attestation {} names no blocker ref; it attests nothing".format(rid))
        elif status not in _ORCH_ATTEST_APPROVED:
            unsub.append(rid)
            findings.append("attestation {} status {!r} is not approved ({}); it attests "
                            "nothing".format(rid, status,
                                             "/".join(sorted(_ORCH_ATTEST_APPROVED))))
        elif not _orch_pointer_resolves(root, sd, pointer):
            unsub.append(rid)
            findings.append("attestation {} pointer {!r} resolves to nothing; it attests "
                            "nothing and blocks nothing".format(rid, pointer))
        elif fresh:
            refs.append(ref)
    return (sorted(set(refs)), unsub, findings)


def _orch_validate_attestations(reg, root):
    """The C.2 validation pass, run at AUDIT CADENCE (the resume audit and tools/orch_doctor.py
    --resume-audit both reach it through _orch_resume_probes), never synchronously at yield time:
    verify the declared attestation register's chain and shape inline, verify each candidate row's
    pointer resolves, and write <state_dir>/attestations-validated.json for the yield-time reader
    (_orch_attestation_refs). A row whose pointer resolves to nothing is recorded unsubstantiated: it
    attests nothing and blocks nothing. Also verifies the pointer of every class systemic-lapse row
    in the declared mistakes register (the GD-127 lapse lifecycle: a lapse is a recorded fact whose
    machine-written integrity signal must resolve; it never converts into a self-authorization).
    Returns finding strings; a failed snapshot write is itself a finding, and the yield-time reader
    then holds (the fail-safe direction), never a clean pass."""
    findings = []
    sd = _orch_state_dir_for_root(root)
    at_path = _orch_path(root, reg.get("attestations"))
    snap_path = os.path.join(sd, "attestations-validated.json")
    if at_path:
        canonical = os.path.realpath(at_path)
        rows, detail = _orch_chained_rows(at_path, "AT-")
        astatus, aseq, adigest = _orch_register_authority(root, at_path)
        hold_snap = {"version": 2, "ts": _orch_now().isoformat(), "status": "unreadable",
                     "refs": [], "unsubstantiated": [], "register": canonical,
                     "seq": None, "digest": None}
        if rows is None:
            findings.append("declared attestation register fails its inline chain/shape check ({}); "
                            "the yield-time reader holds affected items (cannot-evaluate)"
                            .format(detail))
            snap = hold_snap
        elif astatus != "ok":
            findings.append("declared attestation register fails its append-only authority ({}); the "
                            "yield-time reader holds affected items (cannot-evaluate)".format(aseq))
            snap = hold_snap
        else:
            refs, unsub, dfindings = _orch_attestation_derive(reg, root, sd, rows)
            findings.extend(dfindings)
            snap = {"version": 2, "ts": _orch_now().isoformat(), "status": "ok",
                    "refs": refs, "unsubstantiated": unsub, "register": canonical,
                    "seq": aseq, "digest": adigest}
        ok_snap = snap.get("status") == "ok"
        if not ok_snap:
            # delete-then-hold: invalidate any prior fresh 'ok' snapshot BEFORE writing the hold, so a
            # failed write cannot leave a stale clean pass usable.
            try:
                os.remove(snap_path)
            except OSError:
                pass
        unsaved = _orch_write_json_atomic(snap_path, snap)
        if unsaved is not None:
            findings.append("the attestation snapshot could not be written ({}); invalidating any prior "
                            "snapshot so the yield-time reader holds (never a stale clean pass)".format(unsaved))
            try:
                os.remove(snap_path)  # a failed OK write must not leave a prior snapshot usable either
            except OSError:
                pass
    mr_path = _orch_path(root, reg.get("mistakes_register"))
    if mr_path:
        rows, detail = _orch_chained_rows(mr_path, "MR-")
        if rows is None:
            findings.append("declared mistakes register fails its inline chain/shape check ({}); "
                            "tools/check_mistakes_register.py is the authoritative gate"
                            .format(detail))
        else:
            for row in rows:
                if row.get("class") != "systemic-lapse":
                    continue
                pointer = row.get("check_ref") or row.get("evidence")
                if not _orch_pointer_resolves(root, sd, pointer):
                    findings.append("systemic-lapse row {} is unsubstantiated: its pointer {!r} "
                                    "resolves to no machine-written integrity signal; it blocks "
                                    "nothing and never converts into a clean close"
                                    .format(row.get("id"), pointer))
    return findings


def _orch_forced_exit_findings(sd):
    """C.4/FIX 5: surface EACH append-only forced-exit.jsonl row normally once. A companion
    forced-exit-surfaced.json records the keys already raised; a row whose key is not yet recorded
    becomes a finding, and the surfaced set advances only on a successful write. An unreadable log, a
    malformed line, or an unreadable/unwritable surfaced set re-fires next resume rather than losing
    the record (chkfcl: unreadable is a finding, never a silent skip; the advance is at-least-once)."""
    findings = []
    log = os.path.join(sd, "forced-exit.jsonl")
    rows, bad = _orch_read_jsonl(log)
    if rows is None:
        findings.append("pending forced-exit.jsonl present but unreadable: cannot-evaluate, held for "
                        "manual triage")
        return findings
    if bad:
        findings.append("{} malformed forced-exit.jsonl line(s): cannot-evaluate, held for manual "
                        "triage".format(bad))
    if not rows:
        return findings
    surfaced_path = os.path.join(sd, "forced-exit-surfaced.json")
    try:
        with open(surfaced_path, "r", encoding="utf-8") as fh:
            surfaced = set(json.load(fh).get("keys", []))
    except FileNotFoundError:
        surfaced = set()
    except (OSError, ValueError):
        findings.append("the forced-exit surfaced-set is unreadable: cannot-evaluate, held for manual "
                        "triage")
        return findings  # never re-surface blindly against an unreadable set
    fresh = [r for r in rows if r.get("key") not in surfaced]
    for r in fresh:
        ids = r.get("open_ids") if isinstance(r.get("open_ids"), list) else []
        findings.append("a prior exit was forced past open work (forced_unresolved at {}: {} open "
                        "id(s) {}); triage before continuing".format(
                            r.get("ts", "unknown"), len(ids),
                            ", ".join(str(i) for i in ids[:10]) or "unrecorded"))
    if fresh:
        all_keys = sorted({r.get("key") for r in rows if isinstance(r.get("key"), str)})
        # at-least-once: a failed advance re-fires these rows at the next audit, so its failure detail (a
        # temporary file left by a failed cleanup included) is not reported here
        _orch_write_json_atomic(surfaced_path, {"keys": all_keys})
    return findings


def _orch_pending_artefact_findings(root):
    """C.1/C.4 resume probes. escape-spoof.json is a single-shot artefact: raised, then renamed to
    escape-spoof.json.surfaced, which replaces any earlier .surfaced file, so that file holds only the
    latest sentinel whose rename succeeded; a failed rename leaves it at escape-spoof.json, raised again
    next resume, as an unreadable one is (chkfcl). A second sentinel recorded before this probe runs
    overwrites escape-spoof.json, so only the later one is raised from it. The lasting record of each
    ignored sentinel is its append-only guard-events.jsonl row of kind escape-spoof
    (_orch_record_escape_spoof) only where that append succeeded: a failed append was warned about in the
    output of the hook that ignored the sentinel (its banner, or the block reason of a denied Stop or
    TeammateIdle), which asks for a manual record, and no row exists for it. Forced exits are an
    append-only log surfaced via _orch_forced_exit_findings so multiple exits are each normally raised
    once (at least once if recording that one was raised fails) and never clobbered."""
    findings = []
    sd = _orch_state_dir_for_root(root)
    path = os.path.join(sd, "escape-spoof.json")
    try:
        if os.path.lexists(path):
            with open(path, "r", encoding="utf-8") as fh:
                rec = json.load(fh)
            rec = rec if isinstance(rec, dict) else {}
            findings.append("an actor-owned or malformed escape sentinel was ignored at {} ({})"
                            .format(rec.get("ts", "unknown"),
                                    rec.get("detail") or "no detail recorded"))
            try:
                os.replace(path, path + ".surfaced")
            except OSError:
                pass  # it will surface again next resume; never wedge SessionStart
    except (OSError, ValueError):
        findings.append("pending escape-spoof.json record present but unreadable: cannot-evaluate, "
                        "held for manual triage")
    findings.extend(_orch_forced_exit_findings(sd))
    return findings


def _orch_barrier_write(path, obj):
    """Replace the resume barrier file atomically; the one writer of resume-barrier.json (orch_resume_audit,
    orch_resume_barrier's warned flag, and tools/orch_doctor.py --resume-audit). It creates the state
    directory, creates a temporary file beside the barrier with O_CREAT|O_EXCL (open mode "x": mode 0o666
    less the umask, as open(path, "w") creates the barrier), writes the JSON, flushes and fsyncs it, closes
    it, then os.replace()s it onto the barrier path. It records whether this call created the temporary
    file: on any failure after that (the write, flush, fsync, the close at the end of the with block, or
    the replace) it unlinks the temporary file (an unlink that itself fails leaves it beside the barrier,
    never in its place), and it never unlinks a temporary name it did not create (an "x" open of a name
    that already exists raises FileExistsError first); then it re-raises, so the previous barrier file,
    armed or clear, or its absence, is left byte-identical; the caller decides what the error means. A
    process killed between the create and the replace (a hook timeout, for example) leaves its temporary
    file beside the barrier, and nothing removes it. A directory at the barrier path cannot be replaced:
    the replace raises IsADirectoryError and the directory stays until someone removes it. What it writes
    always fits the reader's bound (_orch_barrier_fit): a finding list too long for it is stored as its
    first findings plus one line counting the rest, and an object that still cannot fit raises ValueError
    before any file is created."""
    obj = _orch_barrier_fit(obj, os.path.dirname(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "{}.{}.{}.tmp".format(path, os.getpid(), os.urandom(8).hex())
    created = False
    try:
        with open(tmp, "x", encoding="utf-8") as fh:
            created = True
            json.dump(obj, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if created:
            _orch_unlink_quiet(tmp)
        raise


def _orch_unlink_quiet(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def orch_resume_audit(data):
    """sesres/recncl/cnclse, SessionStart (warn: the platform cannot block this event): reconcile the
    durable record against observed reality and ARM the resume barrier on divergence; a clean audit
    clears it. Arming and clearing are best-effort and atomic (_orch_barrier_write: a temporary file
    beside the barrier, flushed and fsynced, then os.replace): where the barrier cannot be written (its
    state directory cannot be created, the temporary file cannot be created or written, or the replace
    fails, for example XDG_STATE_HOME naming a regular file, or a full disk), the error is swallowed so
    SessionStart never wedges, the temporary file is removed, the previous barrier file (armed or clear)
    or its absence is left byte-identical, and an audit with findings still returns its warning naming
    them, which then says the barrier was not persisted (this audit did not arm it) and asks for a manual
    record instead of saying a re-run clears the barrier; a clean audit's failed clear adds no warning,
    since the PreToolUse barrier reads a barrier left armed as armed (noted once per arming where its warned
    flag can be written, otherwise on each mutation outside the record surfaces: one whose warned flag is
    already set stays armed and silent) and a directory in its place as armed, noted on each such mutation (where opening the forced-exit log fails other
    than as not-found, because the state directory cannot be searched or is not a directory, as with that
    regular file, the forced-exit probe adds its cannot-evaluate finding). The PreToolUse barrier (orch_resume_barrier) reads the file only where
    _orch_registry reads ok; tools/orch_doctor.py --resume-audit writes it through the same helper and
    does not swallow the error (its run ends with it, the previous barrier unchanged). Registry-scoped:
    silent where _orch_registry reads the root's registry as absent; a root it cannot examine reads bad, not absent (below). With a registry
    present it also arms on the truncation guard's deny at its scope check for a Bash call from the root
    (_orch_resume_audit_findings), so it is mode-sensitive: a root git resolves but the walk cannot carry
    out (one this process can enter but not read, for example) arms it in EVERY mode, and an absent or unconfirmable registry scope (a
    symlinked registry, for example) arms it in registry-required mode. Where _orch_root returns None
    (`if root is None: return _allow()`), this audit returns before it reads the registry, so it stays
    silent in both modes. _orch_root returns None for a session cwd that is missing, empty or not a string,
    without calling git, and wherever _recovery_toplevel returns None: git cannot run or exits non-zero (an
    unreadable or broken config, a dangling gitfile, a dubious-ownership refusal, no git binary, a timeout,
    or a cwd this process cannot enter), or its output cannot be decoded, is empty, or is not an absolute
    path. The truncation guard denies a Bash call whose cwd is missing, empty or not a string in every
    mode, before its scope check. For a Bash call from a non-empty string cwd where git resolves nothing,
    whether its scope check denies depends only on its own ancestor walk of that cwd and the mode (its git
    leg resolves nothing either); its other checks still read the call's tool_name, tool_input and command.
    Git can still resolve a toplevel this process cannot enter (core.worktree read from a session cwd
    inside the repository's git directory). Where that toplevel exists without search permission for this
    process, or is not a directory, _orch_registry's lstat of a registry path under it raises an OSError
    other than FileNotFoundError and returns bad, so this audit warns and (best-effort) arms the barrier in
    both modes, as it does for a regular file named .aiqt at any root; where that toplevel does not
    exist, the lstat raises FileNotFoundError for each registry name, _orch_registry
    returns absent (`if status == "absent": return _allow()`), and this audit stays silent in both modes."""
    root = _orch_root(data)
    if root is None:
        return _allow()
    status, reg = _orch_registry(root)
    if status == "absent":
        return _allow()
    barrier_path = os.path.join(_orch_state_dir_for_root(root), "resume-barrier.json")
    if status == "bad":
        _orch_append_jsonl(barrier_path + ".unused", {})  # no-op path probe; keep posture simple
    # The same finding list tools/orch_doctor.py --resume-audit writes (round 8: one barrier truth), so a
    # SessionStart run in the same mode never clears a barrier the doctor armed for a scope deny that still
    # holds; the barrier does not record the mode, so a run in the other mode can (_orch_resume_audit_findings).
    findings = _orch_resume_audit_findings(status, reg, root)
    unwritten = None
    try:
        _orch_barrier_write(barrier_path, {"active": bool(findings), "findings": findings,
                                           "ts": _orch_now().isoformat(), "warned": False})
    except (OSError, ValueError) as exc:
        # never wedge SessionStart: the previous barrier file (or its absence) is left unchanged. A failed
        # ARM is named in the warning below; a clean audit's failed CLEAR stays silent here, because the
        # PreToolUse barrier reads a barrier still armed as armed (noted once per arming where its warned flag
        # can be written, otherwise on each mutation; one already warned about stays silent) and a directory
        # in its place as armed (noted on each mutation)
        unwritten = type(exc).__name__
    if findings:
        tail = _orch_warn_tail(_orch_event_warn(root, "resume-audit", "findings",
                                                "; ".join(findings)[:1000]))
        if unwritten is None:
            nxt = ("then re-run 'python3 tools/orch_doctor.py --resume-audit' to clear the barrier; "
                   "acknowledgement alone does not clear it.")
        else:
            # a directory at the barrier path cannot be replaced by either audit, so it must be removed;
            # any other failure needs a state directory that can be created and written
            remedy = ("remove the directory at the barrier path {} (neither audit can replace a directory), "
                      "then re-run 'python3 tools/orch_doctor.py --resume-audit'".format(barrier_path)
                      if unwritten == "IsADirectoryError" else
                      "re-run 'python3 tools/orch_doctor.py --resume-audit' once the state directory can be "
                      "created and written")
            nxt = ("then {}. Additionally, the resume barrier could not be written ({}), so it was "
                   "not persisted: this audit did not arm it (any earlier barrier file is left unchanged); "
                   "record these findings manually (nocncl).".format(remedy, unwritten))
        return _stop_warn("AIQT guardrail (resume audit): the recorded state diverges from "
                          "observed reality: {}. Correct the record, or for a truncation guard finding "
                          "the condition it names (for example a directory's permissions, or the "
                          "registry's file type), {}{}".format("; ".join(findings), nxt, tail))
    return _allow()


_ORCH_BARRIER_KEYS = frozenset(("active", "findings", "warned", "ts"))


def _orch_barrier_well_formed(barrier):
    """True only for the documented barrier shape: a JSON object with a boolean "active" and a list of
    string "findings" (both required; a missing "findings" is malformed, never an empty list), an optional
    boolean "warned" and an optional string "ts", and no other key."""
    if not isinstance(barrier, dict) or not set(barrier) <= _ORCH_BARRIER_KEYS:
        return False
    if not isinstance(barrier.get("active"), bool) or "findings" not in barrier:
        return False
    found = barrier["findings"]
    if not (isinstance(found, list) and all(isinstance(f, str) for f in found)):
        return False
    if "warned" in barrier and not isinstance(barrier["warned"], bool):
        return False
    return "ts" not in barrier or isinstance(barrier["ts"], str)


_ORCH_BARRIER_MAX_BYTES = 64 * 1024  # _orch_barrier_write never stores more (_orch_barrier_fit); a larger file reads as armed
_ORCH_BARRIER_FINDING_CHARS = 4000  # a stored finding is cut here, so the first one always fits the bound


_ORCH_BARRIER_REST_RE = re.compile(
    r"([0-9]+) more finding\(s\) not stored here \(the barrier file is bounded")


def _orch_barrier_rest(count, sd):
    """The last line _orch_barrier_fit stores in place of the findings that do not fit (sd is the state
    directory holding the barrier). It points only at evidence that outlives the audit. A forced-exit
    finding is normally raised once (at least once: _orch_forced_exit_findings advances its surfaced set
    only where that write succeeds), so a later audit, the doctor's included, does not list it again once
    that write has succeeded (a failed write leaves it to be listed again); the line names
    forced-exit.jsonl, the append-only log that keeps every forced-exit record in full. An ignored escape
    sentinel is raised from escape-spoof.json, which _orch_pending_artefact_findings then renames to
    escape-spoof.json.surfaced: each later rename replaces that file, so it holds only the
    latest sentinel whose rename succeeded, and a failed rename leaves the sentinel at escape-spoof.json,
    where the next audit raises it again. So the line names guard-events.jsonl, whose append-only rows of
    kind escape-spoof (_orch_record_escape_spoof) are the lasting record of each sentinel whose row was
    written (a row that could not be written was warned about when the sentinel was ignored, with a
    request to record it manually, and no row exists for it), and says what the .surfaced file and
    escape-spoof.json hold. Every other finding is recomputed from the record by each audit, so
    'python3 tools/orch_doctor.py --resume-audit' prints it again while its condition holds."""
    return ("{} more finding(s) not stored here (the barrier file is bounded at {} bytes). A forced-exit "
            "finding is normally raised once (at least once if recording that it was raised fails): read "
            "every forced-exit record in full in {} (an ignored escape sentinel is likewise normally raised "
            "once; its lasting record is its row of kind escape-spoof in {} where that row was written, "
            "and a row that could not be written was warned about when the sentinel was ignored, with a "
            "request to record it manually; {} holds only the latest sentinel whose rename succeeded, and "
            "a failed rename leaves it at {}, where the next audit raises it again). Every other finding "
            "still present is printed again by 'python3 tools/orch_doctor.py --resume-audit'".format(
                count, _ORCH_BARRIER_MAX_BYTES, os.path.join(sd, "forced-exit.jsonl"),
                os.path.join(sd, "guard-events.jsonl"), os.path.join(sd, "escape-spoof.json.surfaced"),
                os.path.join(sd, "escape-spoof.json")))


def _orch_barrier_fit(obj, sd):
    """The object _orch_barrier_write stores, so that its JSON (json.dumps, ASCII) never exceeds
    _ORCH_BARRIER_MAX_BYTES, the bound _orch_barrier_read refuses past. An object that already fits is
    returned unchanged. Otherwise, for a dict whose "findings" is a list of strings, a copy keeps the
    leading findings (each cut to _ORCH_BARRIER_FINDING_CHARS characters, marked " (cut)") that fit
    together with one last line counting the findings not stored (_orch_barrier_rest), so the barrier
    stays armed, well-formed and readable and its warned flag can be recorded. A list that already ends
    with such a count line (one this function stored earlier; matched by _ORCH_BARRIER_REST_RE) is re-fit
    without it and its count is carried forward, so the new line counts every finding not stored, never
    the old line as one. Anything else that does not fit (another shape, or other keys too large on their
    own) raises ValueError."""
    if len(json.dumps(obj)) <= _ORCH_BARRIER_MAX_BYTES:
        return obj
    found = obj.get("findings") if isinstance(obj, dict) else None
    if not (isinstance(found, list) and all(isinstance(f, str) for f in found)):
        raise ValueError("the resume barrier does not fit its {}-byte bound".format(_ORCH_BARRIER_MAX_BYTES))
    carried = 0
    prior = _ORCH_BARRIER_REST_RE.match(found[-1]) if found else None
    if prior:
        carried, found = int(prior.group(1)), found[:-1]
    cut = [f if len(f) <= _ORCH_BARRIER_FINDING_CHARS else f[:_ORCH_BARRIER_FINDING_CHARS] + " (cut)"
           for f in found]
    # With k findings the list encodes as the empty object's size plus each finding's encoding plus two
    # bytes (", ") per separator; the rest line is reserved at its largest (no finding stored).
    size = len(json.dumps(dict(obj, findings=[]))) + len(json.dumps(_orch_barrier_rest(len(found) + carried,
                                                                                         sd)))
    kept = []
    for f in cut:
        size += len(json.dumps(f)) + 2
        if size > _ORCH_BARRIER_MAX_BYTES:
            break
        kept.append(f)
    rest = len(found) - len(kept) + carried
    fitted = dict(obj, findings=kept + ([_orch_barrier_rest(rest, sd)] if rest else []))
    if len(json.dumps(fitted)) > _ORCH_BARRIER_MAX_BYTES:
        raise ValueError("the resume barrier does not fit its {}-byte bound".format(_ORCH_BARRIER_MAX_BYTES))
    return fitted
_ORCH_BARRIER_DIRECTORY = "not a regular file: a directory"


def _orch_barrier_nonregular(path, opened=None):
    """The detail for a barrier path that opened as something other than a regular file:
    _ORCH_BARRIER_DIRECTORY only where os.lstat shows the entry itself is a directory (no audit can
    replace that) and, where the caller passes the fstat result of the opened descriptor (opened), that
    directory has the opened file's st_dev and st_ino (an entry swapped between the open and the lstat is
    never named a directory); otherwise "not a regular file" (a FIFO, a device, or a symlink to either or
    to a directory, which the writer's os.replace does replace, leaving the target intact). A UNIX socket
    never reaches here: its open fails with ENXIO, which _orch_barrier_read reads as ('bad', 'OSError')."""
    try:
        st = os.lstat(path)
    except OSError:
        return "not a regular file"
    if stat.S_ISDIR(st.st_mode) and (opened is None
                                     or (st.st_dev, st.st_ino) == (opened.st_dev, opened.st_ino)):
        return _ORCH_BARRIER_DIRECTORY
    return "not a regular file"


def _orch_barrier_read(path):
    """Read the resume barrier without waiting for a FIFO writer and without reading past the bound:
    ('absent', None), ('ok', the parsed JSON value) or ('bad', detail). The open is
    os.open(O_RDONLY | O_NONBLOCK | O_CLOEXEC), so a FIFO with no writer opens at once instead of waiting
    for one. It follows a symlink: a symlink to a regular file reads as that file (the writer's os.replace
    later replaces the link, not its target). A FileNotFoundError or NotADirectoryError from the open (a
    missing file, a dangling symlink, a state directory path through a regular file) is absent. A UNIX
    socket fails the open with ENXIO and is ('bad', 'OSError'). The opened descriptor is fstat'ed and
    anything not a regular file is bad without a read (_orch_barrier_nonregular names it). The read asks
    for at most _ORCH_BARRIER_MAX_BYTES + 1 bytes in all (65537: the 65536-byte bound plus one byte that
    detects a longer file) and a longer file is bad. The descriptor is closed exactly once on every path
    and never retried; a close that fails is bad, named by its type, so a close error never escapes to
    the dispatcher (which would fail closed). Any other exception (an OSError, a decode or JSON error, a
    RecursionError from deep nesting) is bad, named by its type. Not bounded here: the path lookup of the
    os.open (and of the os.lstat in _orch_barrier_nonregular) can stall on a hung mount for any file
    type, a regular file on a stalled filesystem can stall the read, and what opening a device node does
    is up to its driver; the hook timeout (10 seconds in hooks.json) bounds each such stall."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
    except (FileNotFoundError, NotADirectoryError):
        return ("absent", None)
    except IsADirectoryError:
        return ("bad", _orch_barrier_nonregular(path))  # a platform whose open refuses a directory
    except Exception as exc:
        return ("bad", type(exc).__name__)
    chunks, total, early, close_error = [], 0, None, None
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            early = ("bad", _orch_barrier_nonregular(path, opened))
        else:
            while total <= _ORCH_BARRIER_MAX_BYTES:
                chunk = os.read(fd, _ORCH_BARRIER_MAX_BYTES + 1 - total)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
    except Exception as exc:
        early = ("bad", type(exc).__name__)
    finally:
        try:
            os.close(fd)  # once: after a failed close the descriptor number may already be reused
        except OSError as exc:
            close_error = ("bad", type(exc).__name__)
    if early is not None:
        return early
    if close_error is not None:
        return close_error
    if total > _ORCH_BARRIER_MAX_BYTES:
        return ("bad", "larger than the {}-byte bound".format(_ORCH_BARRIER_MAX_BYTES))
    try:
        return ("ok", json.loads(b"".join(chunks).decode("utf-8")))
    except Exception as exc:  # any decode or parse failure (RecursionError from deep nesting too) is bad
        return ("bad", type(exc).__name__)


def orch_resume_barrier(data):
    """sesres/recncl, PreToolUse (stage BAKE: warn-first, blocks nothing yet): while the barrier is
    armed, surface the first mutation outside the allowlist. The record surfaces, the registry files,
    and the suite's own state directory stay writable, so the only exit, correcting the record, is
    never obstructed. The barrier is read by _orch_barrier_read: a non-blocking open (a FIFO with no
    writer does not wait for one), an fstat that refuses anything not a regular file before any read (so
    a FIFO or a device such as /dev/zero is never read), and a read of at most _ORCH_BARRIER_MAX_BYTES + 1
    bytes (65537: the bound plus one byte that detects a longer file); a close error is a bad result too.
    A barrier file that is absent (its open raises FileNotFoundError
    or NotADirectoryError, a dangling symlink included) reads as clear; a symlink to a regular file reads
    as that file. A barrier is well-formed only when it is a JSON object whose keys are "active"
    (required, a boolean), "findings" (required, never defaulted, a list of strings), and optionally
    "warned" (a boolean) and "ts" (a string), and no other key. One that exists but is not a regular file
    (a FIFO, a device such as /dev/zero, a directory, or a symlink to any of these; a UNIX socket, whose
    open fails with ENXIO, reads as OSError), is larger than the bound, cannot be read, closed or parsed
    for any reason (an OSError, a decode or JSON error, a
    RecursionError from deep nesting, or any other exception the parse raises), or is not well-formed (a
    truncated or partial write by an earlier writer, for example), reads as ARMED: every mutation outside
    the allowlist surfaces a note naming the file as unreadable or malformed with the reason (there is no
    readable "warned" flag to record, so it is not once per arming), and in BAKE that note blocks
    nothing, and a mutation on the allowlist is allowed after the same read. Not bounded by the reader:
    path lookup on a hung mount can stall its os.open (and the lstat naming a non-regular file, and the
    os.path.realpath calls below) for any file type, a regular file on a stalled filesystem can stall the
    read, and opening a device node does whatever its driver does; the hook timeout (10 seconds) bounds
    each such stall. It clears where 'python3
    tools/orch_doctor.py --resume-audit' or the next SessionStart audit replaces the file
    (_orch_barrier_write, whose os.replace replaces a FIFO, a socket, a device node or a symlink at the
    path and leaves a symlink's target intact), or where the user corrects or removes it (the state
    directory is on the allowlist); where the state directory cannot be searched, that replace fails as
    well, and the note persists until its permissions are restored. A directory at the barrier path
    itself (os.lstat shows a directory) cannot be replaced by either audit, so its note says to remove
    the directory; a symlink to a directory gets the generic note, since the audit replaces the link."""
    root = _orch_root(data)
    if root is None:
        return _allow()
    status, reg = _orch_registry(root)
    if status != "ok":
        return _allow()
    sd = _orch_state_dir_for_root(root)
    barrier_path = os.path.join(sd, "resume-barrier.json")
    status, barrier = _orch_barrier_read(barrier_path)
    if status == "absent":
        return _allow()
    unreadable = None
    if status == "bad":  # not a regular file, oversized, unreadable or unparseable: armed
        barrier, unreadable = {}, barrier
    elif not _orch_barrier_well_formed(barrier):
        barrier, unreadable = {}, "not a well-formed barrier object"
    elif not barrier["active"]:
        return _allow()
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    file_path = tool_input.get("file_path")
    if isinstance(file_path, str) and file_path:
        allow_prefixes = [sd]
        rec = reg.get("record") if isinstance(reg.get("record"), dict) else {}
        for key in ("findings", "pending_decisions", "handoff"):
            p = _orch_path(root, rec.get(key))
            if p:
                allow_prefixes.append(p)
        for rel in _ORCH_REGISTRY_FILES:
            allow_prefixes.append(os.path.join(root, *rel.split("/")))
        target = os.path.realpath(file_path)
        for p in allow_prefixes:
            rp = os.path.realpath(p)
            if target == rp or target.startswith(rp.rstrip(os.sep) + os.sep):
                return _allow()  # the exit path (fixing the record) is always writable
    if unreadable == _ORCH_BARRIER_DIRECTORY:
        return _allow_note(
            "AIQT guardrail (resume barrier, BAKE posture: surfacing, not blocking): the resume "
            "barrier file {} is unreadable or malformed (not a regular file: it is a directory), so it is "
            "read as armed and this mutation is outside the record surfaces. Neither 'python3 "
            "tools/orch_doctor.py --resume-audit' nor the SessionStart audit can replace a directory: "
            "remove the directory, then re-run 'python3 tools/orch_doctor.py --resume-audit'."
            .format(barrier_path))
    if unreadable is not None:
        return _allow_note(
            "AIQT guardrail (resume barrier, BAKE posture: surfacing, not blocking): the resume "
            "barrier file {} is unreadable or malformed ({}), so it is read as armed and this mutation "
            "is outside the record surfaces. Re-run 'python3 tools/orch_doctor.py --resume-audit' to "
            "replace it (a clean audit clears it), or correct or remove the file."
            .format(barrier_path, unreadable))
    if barrier.get("warned"):
        return _allow()  # surface once per arming, never a nag wall
    barrier["warned"] = True
    try:
        _orch_barrier_write(barrier_path, barrier)
    except (OSError, ValueError):
        pass  # the previous barrier is left unchanged; it surfaces again on the next mutation
    return _allow_note(
        "AIQT guardrail (resume barrier, BAKE posture: surfacing, not blocking): the resume "
        "audit found divergence ({}) and this mutation is outside the record surfaces. Correct the "
        "record first, then clear the barrier with 'python3 tools/orch_doctor.py --resume-audit'."
        .format("; ".join(barrier.get("findings") or [])[:500]))


# --- write-scope guard (EN-8, wrtscp) ----------------------------------------------------------------
# Confine the sole orchestrator's guarded-tool writes to a harness-set per-slice scope declaration, and
# hard-deny writes to frozen paths and to other repositories as a floor the declaration cannot lower.
# It reuses the orchestration substrate (_state_dir_from_registry for the registry-declared state_dir the
# declaration lives at, resolved from a single validated registry read, _orch_guard_event for the over-fire
# metric), the scrubbed git primitive
# (_recovery_toplevel), and the
# gensrc containment/loader idiom (_gensrc_within, the lstat->S_ISREG->byte-bound->UTF-8->JSON taxonomy).
# TWO arming inputs, each fail-open on ABSENCE only: the per-slice declaration (slice confinement) and the
# committed frozen floor (frozen denial). The structural other-repo/nested-repo denial applies to every
# covered write whose repository root resolves (a covered write whose root cannot be resolved is DENIED, not
# allowed); the frozen-floor denial fires whenever a floor is present, and an armed session additionally
# requires one, so an un-armed session with a genuinely-absent floor is inert there. Only slice confinement
# is fail-open on a missing declaration. Once armed, every cannot-evaluate resolves to DENY. See the
# write-scope residue in .aiqt/core/hooks/manifest.toml.
_WRTSCP_TOOLS = ("Write", "Edit", "MultiEdit")   # same matcher as gensrc_guard; MultiEdit carries one file_path
_WRTSCP_DECL_REL = "write-scope.json"            # under the registry-declared <state_dir> (harness-set, per
                                                 # slice; out of the slice tree by default, possibly in-tree)
_WRTSCP_FLOOR_REL = os.path.join(".aiqt", "frozen.json")  # in-tree committed floor (gen_manifest-generated)
_WRTSCP_VERSION = 1
_WRTSCP_MAX_BYTES = 64 * 1024                    # 64 KB: both artifacts are small; a larger one is malformed
# Hook-side ALWAYS-FROZEN entries (repo-root-relative, same grammar as the committed floor): frozen against
# the constrained actor's own covered Write/Edit/MultiEdit in BOTH regimes and INDEPENDENT of the committed
# .aiqt/frozen.json floor, so an absent, deleted, or permissive floor cannot unfreeze them and a permissive
# scope declaration cannot lower them. EVERY orchestration registry file in _ORCH_REGISTRY_FILES (the
# machine-local .aiqt/orchestration.local.json AND the committed .aiqt/orchestration.json) carries
# write-scope-adjacent GUARDRAIL CONFIGURATION: the `companion_stores` cross-repo allow-list and the
# yield-tool roster. Declaring a companion store is a scope-widening, guardrail-configuration change under the
# SAME human-authorization discipline as any other scope widening (guardrail-config-integrity), so the actor
# must not be able to self-declare a companion-store widening through a covered write; freezing the registry
# against its own covered writes closes that self-widening residual (raised independently by our own QA and
# the cleanlanguage adopter). BOTH registry files are frozen, not only the LOCAL one: _wrtscp_companion_stores
# reads companion_stores from EITHER file (via _orch_registry, which falls back to the committed
# .aiqt/orchestration.json when the LOCAL file is absent), so freezing only the LOCAL file left a covered
# Write of companion_stores into .aiqt/orchestration.json to take effect IN-SESSION (its cross-repo allow
# read at decision time) before the CI drift gate could ever see it - a self-widening bypass. Freezing every
# _ORCH_REGISTRY_FILES entry closes it, so no registry file that can source companion_stores/the yield-roster
# is writable via a covered tool; the committed file's authorized change still lands through the general
# Bash-write path (a `git`/editor write outside the Write|Edit|MultiEdit matcher) plus pull-request review and
# the drift gate, which IS the human-authorization path. The general Bash-write residual (a `sed -i`, a shell
# redirect, any write outside the Write|Edit|MultiEdit matcher) is UNCHANGED and stays the disclosed wrtscp
# residual; this hook-side freeze is defence in depth on the covered-tool path, not a categorical lock.
_WRTSCP_ALWAYS_FROZEN = tuple(("file", _rel) for _rel in _ORCH_REGISTRY_FILES)


def _wrtscp_parse_entry(raw):
    """Validate one path/tree entry in the shared floor/declaration grammar and return (kind, body) with
    kind 'file'|'tree' and body the entry minus any trailing '/', or None if malformed. The grammar mirrors
    the gensrc target grammar so the same containment reasoning applies: a repo-root-relative POSIX string;
    a trailing '/' marks a tree, otherwise an exact file; NO absolute path, backslash, empty/'.'/'..'
    component, control character, or wildcard (fnmatch ambiguity is rejected, per the EN-8 plan). Because
    an entry can carry no '..' component and is never absolute, it can never point outside the repo root it
    is joined onto (the 'pointing outside the repo' half of row 19 is enforced here, by construction)."""
    if not isinstance(raw, str) or not raw:
        return None
    if "\\" in raw or _is_absolute(raw):
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in raw):
        return None
    if any(ch in raw for ch in ("*", "?", "[")):
        return None
    is_tree = raw.endswith("/")
    body = raw[:-1] if is_tree else raw
    if not body or any(seg in ("", ".", "..") for seg in body.split("/")):
        return None
    return ("tree" if is_tree else "file", body)


def _wrtscp_lexical_tree_hit(file_path, root, root_c, floor):
    """CLAUDE-F2 (round-6). True when the LEXICAL (unresolved, symlink-NOT-followed) absolute file_path lies
    at or under a frozen-floor TREE entry, anchored on BOTH the raw session root and its realpath. This closes
    the symlink-below-a-tree-entry escape the realpath-based _wrtscp_target_matches cannot see: a symlink UNDER
    a frozen tree entry can make os.path.realpath(file_path) resolve OFF the entry's realpath subtree (even
    into a declared companion store), so the realpath match returns no-hit and the write is admitted, though
    its LEXICAL path names a location inside the frozen tree. The realpath match above still covers a symlinked
    ROOT; this lexical match covers a symlink strictly below the entry. Fail-closed by construction: it only
    ADDS denials for a write whose lexical name is inside a frozen tree (the safe direction) and never lowers
    the floor. File entries need no lexical companion (the realpath file-equality match already closes their
    symlink case, since both the target and the entry follow the same alias)."""
    lex = os.path.normpath(file_path)
    for kind, body in floor:
        if kind != "tree":
            continue
        for anchor in (root_c, root):
            if not anchor:
                continue
            entry = os.path.normpath(os.path.join(anchor, body))
            if lex == entry or lex.startswith(entry + os.sep):
                return True
    return False


def _wrtscp_read_json_artifact(path, max_bytes):
    """The shared lstat-before-open / S_ISREG / byte-bounded / strict-UTF-8 / strict-JSON reader for BOTH
    the out-of-tree declaration and the in-tree floor, in the _load_gensrc_registry idiom. Returns
    ('absent', None), ('bad', detail), or ('ok', obj). ABSENT is ONLY a clean lstat FileNotFoundError;
    EVERY other fault is BAD, never absent, so an unreadable input can never read as no-coverage
    (integ-check-fails-closed-on-unreadable): a non-regular file (symlink/FIFO/directory/device), a
    stat/read error, an oversize file (byte-bounded), a non-UTF-8 decode, malformed JSON, or a non-object.
    A delete race in the lstat->open window fails safe to BAD. Read AT DECISION TIME, never cached."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return ("absent", None)
    except OSError as exc:
        return ("bad", "could not be stat'd ({})".format(exc))
    if not stat.S_ISREG(st.st_mode):
        return ("bad", "is not a regular file (a symlink, FIFO, directory, ...)")
    try:
        with open(path, "rb") as handle:
            raw_bytes = handle.read(max_bytes + 1)
    except FileNotFoundError:
        return ("bad", "disappeared during the read (a concurrent change); failing safe")
    except OSError as exc:
        return ("bad", "could not be read ({})".format(exc))
    if len(raw_bytes) > max_bytes:
        return ("bad", "exceeds the {}-byte bound".format(max_bytes))
    try:
        raw = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ("bad", "is not valid UTF-8")
    try:
        obj = json.loads(raw)
    except ValueError:
        return ("bad", "is malformed JSON")
    if not isinstance(obj, dict):
        return ("bad", "is not a JSON object")
    return ("ok", obj)


def _load_write_scope(root):
    """Read <state_dir>/write-scope.json AT DECISION TIME (the harness-set per-slice declaration; it lives
    at the orchestration registry's declared state_dir, out of the slice tree BY DEFAULT but MAY be in-tree
    when the registry declares an in-tree state_dir, so the out-of-tree placement is a harness convention,
    not a guarantee) and return ('absent', None), ('bad', detail), or ('ok', {slice, worktree_root, allow}).
    `allow` is the parsed list of (kind, body) entries (possibly empty; an empty allow list is valid and
    means every covered in-repo write is out of scope). The version pin excludes JSON `true` (type(v) is
    int, not `== 1`, because True == 1 in Python). worktree_root is validated as a non-empty string here; its
    equality with the hook's own canonicalized root is checked in the handler (a stale/copied declaration is
    BAD there). ABSENT is the fail-open state (no slice confinement); every present-but-unreadable outcome is
    BAD. The registry that LOCATES the declaration is itself part of this read: a 'bad' (unreadable or
    malformed) registry is a cannot-evaluate returned as BAD here (fail-closed), never a silent XDG-default
    fallback that would report the declaration absent and DISARM confinement; likewise a registry that is
    'ok' but declares a PRESENT-but-malformed state_dir (a non-string or empty value, which _orch_path
    resolves to None) is BAD, while an ABSENT state_dir key legitimately selects the XDG default and stays
    allowed. The declaration directory is resolved from THIS validated registry result via the pure
    _state_dir_from_registry helper, so _load_write_scope performs exactly ONE registry read and never a
    second, independently-faulting one that could disarm the session; _orch_state_dir_for_root reads once and
    delegates to the same helper for its own other callers."""
    reg_status, reg = _orch_registry(root)
    if reg_status == "bad":
        return ("bad", "{}: the orchestration registry that locates the write-scope declaration is "
                       "unreadable or malformed, a cannot-evaluate that denies rather than silently "
                       "disarming confinement".format(reg))
    if reg_status == "ok" and isinstance(reg, dict) and "state_dir" in reg \
            and _orch_path(root, reg.get("state_dir")) is None:
        # A PRESENT state_dir key whose value is non-string or empty is a cannot-evaluate: the old code
        # returned None from _orch_path and silently fell back to the XDG default, disarming an armed
        # session. (An ABSENT state_dir key legitimately means "use the XDG default" and is not this case.)
        return ("bad", "the orchestration registry declares a malformed state_dir; a cannot-evaluate "
                       "denies rather than disarming confinement")
    # Resolve the declaration directory from the registry result WE ALREADY VALIDATED above, via the pure
    # helper: never re-read the registry here. A second read can fault independently (e.g. EACCES between the
    # two reads) and silently fall back to XDG, disarming the session (the TOCTOU fail-open). This keeps the
    # _orch_registry call above the ONE and only registry read performed by _load_write_scope.
    state_dir = _state_dir_from_registry(root, reg if reg_status == "ok" else None)
    path = os.path.join(state_dir, _WRTSCP_DECL_REL)
    status, obj = _wrtscp_read_json_artifact(path, _WRTSCP_MAX_BYTES)
    if status == "absent":
        return ("absent", None)
    if status == "bad":
        return ("bad", "the write-scope declaration {}".format(obj))
    version = obj.get("version")
    if type(version) is not int or version != _WRTSCP_VERSION:
        return ("bad", "the write-scope declaration has an unknown version (expected {})"
                       .format(_WRTSCP_VERSION))
    slice_name = obj.get("slice")
    if not isinstance(slice_name, str) or not slice_name:
        return ("bad", "the write-scope declaration carries no readable slice")
    worktree_root = obj.get("worktree_root")
    if not isinstance(worktree_root, str) or not worktree_root:
        return ("bad", "the write-scope declaration carries no readable worktree_root")
    allow_raw = obj.get("allow")
    if not isinstance(allow_raw, list):
        return ("bad", "the write-scope declaration carries no allow list")
    allow = []
    for item in allow_raw:
        entry = _wrtscp_parse_entry(item)
        if entry is None:
            return ("bad", "the write-scope declaration has a malformed allow entry")
        allow.append(entry)
    return ("ok", {"slice": slice_name, "worktree_root": worktree_root, "allow": allow})


def _load_frozen_floor(root):
    """Read <root>/.aiqt/frozen.json AT DECISION TIME (the committed, drift-gated floor generated by
    tools/gen_manifest.py from the ownership classification: the frozen class set {derived, manifest-self,
    archive}) and return ('absent', None), ('bad', detail), or ('ok', entries) where entries is the parsed
    list of (kind, body). An EMPTY frozen list is valid (an adopter with nothing frozen). ABSENT means no
    floor is declared (the frozen layer is inert un-armed; an armed session denies, so a Bash-deleted floor
    cannot silently downgrade an armed session); every present-but-unreadable outcome is BAD."""
    path = os.path.join(root, _WRTSCP_FLOOR_REL)
    status, obj = _wrtscp_read_json_artifact(path, _WRTSCP_MAX_BYTES)
    if status == "absent":
        return ("absent", None)
    if status == "bad":
        return ("bad", "the frozen floor {}".format(obj))
    version = obj.get("version")
    if type(version) is not int or version != _WRTSCP_VERSION:
        return ("bad", "the frozen floor has an unknown version (expected {})".format(_WRTSCP_VERSION))
    frozen_raw = obj.get("frozen")
    if not isinstance(frozen_raw, list):
        return ("bad", "the frozen floor carries no frozen list")
    frozen = []
    for item in frozen_raw:
        entry = _wrtscp_parse_entry(item)
        if entry is None:
            return ("bad", "the frozen floor has a malformed entry")
        frozen.append(entry)
    return ("ok", frozen)


def _wrtscp_target_matches(entries, target, root_c):
    """Whether the already-realpath'd absolute `target` matches any (kind, body) entry, joining each entry
    onto root_c and canonicalizing (the gensrc_match idiom). Returns True (matched), False (proven
    no-match), or None (a resolution/containment fault, so no-match cannot be proven; the caller fails
    closed when armed). A file entry matches on realpath equality; a tree entry matches when target is the
    tree root or lies under it by component-boundary containment (_gensrc_within), never a string prefix."""
    for kind, body in entries:
        try:
            entry_c = os.path.realpath(os.path.join(root_c, body))
        except (OSError, ValueError):
            return None
        if kind == "file":
            if entry_c == target:
                return True
        else:
            verdict = _gensrc_within(target, entry_c)
            if verdict == "err":
                return None
            if verdict == "in":
                return True
    return False


def _wrtscp_entry_wholly_frozen(kind, body, floor):
    """Whether a declaration allow entry (kind, body) is EQUAL TO or WHOLLY INSIDE some floor entry, judged
    by component-boundary containment on the DECLARED repo-relative bodies (a declaration-vs-declaration
    consistency check, no filesystem). Such an allow entry can never grant anything (everything it covers is
    frozen), so it makes the declaration malformed (row 19). A floor FILE wholly contains only an identical
    file; a floor TREE T wholly contains any entry whose body equals T or lies under it. An allow tree that
    merely CONTAINS a frozen subtree (allow '.aiqt/' over frozen '.aiqt/core/') is NOT wholly frozen and is
    legal; row 18 still denies the frozen targets inside it at write time."""
    for f_kind, f_body in floor:
        if f_kind == "file":
            if kind == "file" and body == f_body:
                return True
        else:
            if body == f_body or body.startswith(f_body + "/"):
                return True
    return False


def _wrtscp_nearest_existing_dir(target):
    """The nearest EXISTING ancestor directory of an absolute target path (target itself may be a new file
    in a not-yet-existing directory), or None if none resolves. Walks up to the filesystem root."""
    d = os.path.dirname(target)
    while True:
        try:
            if os.path.isdir(d):
                return d
        except OSError:
            return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _wrtscp_nested_repo(target, root_c):
    """True when the target sits in a NESTED or FOREIGN git repository under or beside the session root,
    False when it is the same repo, None on a resolution fault. Resolves the SCRUBBED git toplevel of the
    target's nearest existing ancestor directory (_recovery_toplevel, every ambient GIT_* removed) and
    compares it to root_c: a different toplevel is a nested/foreign repo (denied). This carries the
    grc_library_ref case and any absolute path escaping into a sibling checkout, generically."""
    anchor = _wrtscp_nearest_existing_dir(target)
    if anchor is None:
        return None
    top = _recovery_toplevel(anchor)
    if top is None:
        return None
    try:
        return os.path.realpath(top) != root_c
    except (OSError, ValueError):
        return None


def _wrtscp_companion_stores(root):
    """The adopter-declared companion-store repo roots, read AT DECISION TIME from the orchestration
    registry's `companion_stores` key (a list of absolute paths to git repo TOPLEVELS beside the session
    repo, e.g. the sole orchestrator's OWN durable store, which is by design a SECOND git repo next to the
    code repo). Returns a list of canonicalized (realpath'd) repo-root strings.

    This is ADOPTER-CONTROLLED CONFIG: the guard only HONOURS a validly-declared store, it never
    self-widens. An entry is honoured ONLY when it is a non-empty absolute string with no control character
    AND resolves to a real git toplevel that IS the declared root itself (its own `rev-parse
    --show-toplevel`, via the scrubbed _recovery_toplevel primitive, canonicalizes to the declared path).
    Per guard-input-soundness, a MALFORMED, unresolvable, or non-repo entry is fail-closed: it is silently
    dropped, so a cross-repo write it would have named still DENIES (the floor is never lowered on a bad
    declaration; a bad entry can only ever remove a would-be allow, never open a hole). A registry that is
    bad or absent, or a `companion_stores` that is absent or not a list, yields no stores, so cross-repo
    writes deny exactly as before. Read at decision time, never cached (the write-scope reader's discipline).
    Note the residual: this reads the registry, it does not police who WROTE the registry; the registry is
    the adopter/harness surface, exactly as `state_dir` already is (guardrail-config integrity of the
    registry file itself is the harness's, not this path guard's, to hold)."""
    reg_status, reg = _orch_registry(root)
    if reg_status != "ok" or not isinstance(reg, dict):
        return []
    raw = reg.get("companion_stores")
    if not isinstance(raw, list):
        return []
    stores = []
    for item in raw:
        if not isinstance(item, str) or not item or not _is_absolute(item):
            continue
        if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in item):
            continue
        try:
            item_c = os.path.realpath(item)
        except (OSError, ValueError):
            continue
        top = _recovery_toplevel(item_c)   # scrubbed rev-parse; None on a non-repo / unresolvable path
        if top is None:
            continue
        try:
            top_c = os.path.realpath(top)
        except (OSError, ValueError):
            continue
        if top_c != item_c:
            continue  # the declared path must BE a git repo root, not merely lie inside one
        stores.append(top_c)
    return stores


def _wrtscp_target_companion_store(target, stores):
    """The declared companion-store root the write target belongs to, by EXACT repo-root match, or None.
    The target's OWN resolved git toplevel must EQUAL a declared store root: never a prefix/substring, so a
    write into a repo NESTED inside a declared store (its own toplevel differs) does not match, nor does a
    sibling merely beside it. `target` is already realpath'd; its repo toplevel is resolved from the nearest
    existing ancestor via the scrubbed primitive (the _wrtscp_nested_repo idiom), so a not-yet-existing file
    still resolves. A target whose repo toplevel cannot be resolved returns None (no match), leaving the
    caller's cross-repo denial to stand (fail-closed)."""
    if not stores:
        return None
    anchor = _wrtscp_nearest_existing_dir(target)
    if anchor is None:
        return None
    top = _recovery_toplevel(anchor)
    if top is None:
        return None
    try:
        top_c = os.path.realpath(top)
    except (OSError, ValueError):
        return None
    return top_c if top_c in stores else None


def _wrtscp_deny(root, detail, reason, banner):
    """A write-scope DENY that also appends a guard-events row (the over-fire metric) when root is
    resolvable. A failed append adds the recording-failure warning to the deny reason and the banner and
    never alters the denial; with no resolvable root no row is attempted."""
    tail = _orch_warn_tail(_orch_event_warn(root, "wrtscp", "deny", detail) if root is not None else "")
    return _deny("AIQT rule wrtscp (write-scope): {}{}".format(reason, tail),
                 "AIQT guardrail: {} (rule wrtscp).{}".format(banner, tail))


def write_scope_guard(data):
    """wrtscp (write-scope), PreToolUse on Write|Edit|MultiEdit (EN-8): confine the sole orchestrator's
    guarded-tool writes to a harness-set per-slice scope declaration, and hard-deny writes to frozen paths
    and to other repositories as a floor the declaration cannot lower. It enforces septsk
    (separate-task-changes) as the pre-write realization of keeping unrelated work out of the slice change
    set, wowo01 (workers-produce-inert-data) as the apply-stage discipline over the sole writer, and exetgt
    (confirm-execution-target): the other-repo denial is its mechanical form for writes.

    TWO arming inputs, each fail-open on ABSENCE only. The per-slice declaration
    <state_dir>/write-scope.json (at the registry-declared state_dir, out of the slice tree by default) arms
    SLICE CONFINEMENT (absent -> un-armed, inert confinement; present -> armed, every uncertainty denies).
    The in-tree committed frozen floor .aiqt/frozen.json arms the FROZEN layer (absent -> the frozen layer is
    inert un-armed; present -> the floor is enforced). The structural other-repo/nested-repo denial applies
    to every covered write whose repository root resolves, and a covered write whose root CANNOT be resolved
    is denied (fail-closed), not allowed. The ONE sanctioned exception to the other-repo denial is an
    adopter-DECLARED COMPANION STORE: the orchestration registry's `companion_stores` names absolute git
    repo TOPLEVELS beside the session repo (e.g. the sole orchestrator's own durable store, a SECOND git repo
    by design), and a covered write whose target resolves - by EXACT repo-root match, never a prefix - into a
    declared store is ALLOWED and AUDITED (a wrtscp allow guard-event) rather than denied, so the single most
    routine legitimate cross-repo write no longer inverts to denying the audited path while the Bash residual
    stays open. The hook only HONOURS a validly-declared store, never self-widens: a malformed, unresolvable,
    non-repo, or non-root declaration is fail-closed (the cross-repo write still denies), and the exact-root
    match keeps a repo nested inside a declared store, or a sibling beside it, denied. The nested-in-session
    and frozen-floor denials are unchanged by a declaration. DECLARING A COMPANION STORE IS A
    GUARDRAIL-CONFIGURATION CHANGE: `companion_stores` widens where covered writes may land (a cross-repo
    scope widening), so it is governed by the SAME human-authorization discipline as any other scope widening
    (guardrail-config-integrity) - the harness/adopter sets it on the registry surface, never the constrained
    actor. For that reason the orchestration LOCAL registry (.aiqt/orchestration.local.json) is on the
    hook-side ALWAYS-FROZEN set (_WRTSCP_ALWAYS_FROZEN): a covered Write/Edit/MultiEdit to it is DENIED in
    BOTH regimes and independent of the committed floor, so the actor cannot self-declare a companion-store
    widening through a guarded tool; the general Bash-write path stays the disclosed residual below. The
    `companion_stores` declaration is honoured only inside a VERSION-1 registry OBJECT: _orch_registry reads
    a registry with a missing or non-1 `version` as BAD (a cannot-evaluate), so a declaration lacking
    `"version": 1` yields NO stores and the cross-repo write FAILS CLOSED (denies), never silently taking
    effect. The root is resolved FIRST, so this unresolvable-root denial
    precedes any relative-path handling (a non-git relative-path write denies, it does not defer); only once
    the root resolves does an un-armed relative path defer to the sibling absolute_paths hook. The
    frozen-floor denial fires whenever a floor is PRESENT, and an
    armed session additionally REQUIRES a floor (an armed session with a genuinely-absent floor denies); only
    an un-armed session with a genuinely-absent floor leaves the frozen layer inert, so the frozen denial is
    not unconditionally always-on. Slice confinement is fail-open on a missing declaration; the principled
    fail-open is genuine ABSENCE (of a declaration or floor: no confinement in effect, the same inert
    boundary gensrc and, by default, the orchestration suite use; its truncation guard denies an absent
    registry in the opt-in registry-required mode). A cannot-evaluate FAULT is not absence: a resolution
    or probe ERROR (an unresolvable session root, a root or target canonicalization fault, a containment
    fault, or a nested-repo probe fault) on a covered write DENIES whether or not the session is armed, and
    never allows an unverified write; and once armed every cannot-evaluate resolves to DENY. Out-of-scope is a DENY, never an ASK:
    an ASK would hand the confined actor the click-through on its own confinement and would wedge an
    unattended run; the remedy is the harness re-declaring the slice. The declaration is machine-set inert
    data, never authored by the assistant through a guarded tool; the residual Bash-write path to the state
    dir is defence in depth, not categorical (disclosed below).

    RESIDUAL (disclose-guard-residuals): a PATH guard, not a content judge; it decides where a write lands,
    never what it writes. Covers only PreToolUse Write/Edit/MultiEdit carrying a readable file_path.
    Bash-mediated writes (redirects, sed -i, cp, mv, tee, an editor launched in Bash), NotebookEdit,
    filesystem MCP tools, and any write outside the platform's hook path are NOT caught; the committed frozen
    floor .aiqt/frozen.json is a generated, drift-gated output backstopped at merge by the manifest-gen-drift
    gate (tools/gen_manifest.py --check), the gensrc-registry-drift gate (tools/gen_gensrc.py --check), and
    the independent manifest-integrity verifier (tools/check_manifest.py), but nothing backstops an
    uncommitted, out-of-scope-mutable, or cross-repository write through those channels. The declaration is
    machine-local and session-scoped (CI cannot observe it). Realpath closes symlink escapes present at check
    time but not a post-check swap, hard-link alias, or bind-mount change (TOCTOU); an OS-level write sandbox
    is the stronger overlapping control. The floor is enforced against the manifest CLASS set {derived,
    manifest-self, archive}. That floor's RUNTIME value is un-lowerable by the constrained actor through the
    guarded tools (a guarded Write/Edit/MultiEdit to .aiqt/frozen.json is itself on the floor and denied);
    but its DEFINITION (FROZEN_CLASSES in tools/gen_manifest.py) is generator source that lands through
    pull-request review PLUS the drift gate, which detects an INCONSISTENT floor (a committed frozen.json
    that does not match a fresh regeneration), NOT an unauthorized-but-internally-consistent redefinition of
    the class set followed by regeneration. An independent floor-authorization mechanism is out of this
    hook's scope. The floor proves membership in its declared selector set only, not that the generation-time
    class mapping is semantically complete."""
    if data.get("hook_event_name") != PRETOOL:
        return _hard_block("aiqt_hooks: write_scope_guard wired to unexpected event {!r}; failing closed"
                           .format(data.get("hook_event_name")))
    tool_name = data.get("tool_name")
    if tool_name is None:
        return _deny_missing_tool_name("wrtscp")             # row 3: the only deny with no root context
    if not isinstance(tool_name, str) or not tool_name:
        # A malformed tool_name is a malformed call; it cannot be matched, and it is not a legitimate write
        # in either regime. DENY (fail-closed), never a silent allow. (No root yet: no guard-events row.)
        return _wrtscp_deny(None, "malformed tool_name",
                            "the PreToolUse payload carried no readable tool_name, so the call cannot be "
                            "matched; a malformed guarded-tool call is not a legitimate write",
                            "denied a guarded-tool call with an unreadable tool_name")
    if tool_name not in _WRTSCP_TOOLS:
        return _allow()                                      # row 1: out of matcher (defensive)
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return _wrtscp_deny(None, "unreadable tool_input",
                            "the {} payload carried no readable tool_input, so the write target cannot be "
                            "determined; a malformed write is not legitimate in either regime"
                            .format(tool_name),
                            "denied a {} with an unreadable tool_input".format(tool_name))  # row 4
    file_path = tool_input.get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return _wrtscp_deny(None, "missing file_path",
                            "the {} payload carried no readable file_path, so the write target cannot be "
                            "determined; a malformed write is not legitimate in either regime"
                            .format(tool_name),
                            "denied a {} with no readable file_path".format(tool_name))     # row 4
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in file_path):
        return _wrtscp_deny(None, "control-char file_path",
                            "the {} payload file_path contains a control character (malformed input)"
                            .format(tool_name),
                            "denied a {} whose file_path carried a control character".format(tool_name))
    cwd = data.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        # A covered write whose session cwd is missing or unreadable has no resolvable repository root, so
        # containment cannot be evaluated at all; a covered write that cannot be cleared is DENIED
        # (fail-closed), never the old inert allow that let an unlocatable-root write through (row 5).
        return _wrtscp_deny(None, "unresolvable session root (no cwd)",
                            "the {} payload carried no readable cwd, so the session repository root cannot "
                            "be resolved and the write cannot be cleared for containment; a covered write "
                            "whose scope cannot be evaluated is denied".format(tool_name),
                            "denied a {} (session root unresolvable, no cwd)".format(tool_name))  # row 5
    # The scrubbed rev-parse primitive (every ambient GIT_* removed), so an ambient decoy repo cannot
    # redirect the reads; None means a non-git session, whose repository root is unresolvable, so a covered
    # write cannot be cleared for containment and is DENIED (fail-closed) rather than allowed.
    root = _recovery_toplevel(cwd)
    if root is None:
        return _wrtscp_deny(None, "unresolvable session root (non-git)",
                            "the {} session is not in a resolvable git repository, so the session repository "
                            "root cannot be resolved and the write cannot be cleared for containment; a "
                            "covered write whose scope cannot be evaluated is denied".format(tool_name),
                            "denied a {} (session root unresolvable, non-git)".format(tool_name))  # row 5
    decl_status, decl = _load_write_scope(root)
    armed = decl_status != "absent"
    try:
        root_c = os.path.realpath(root)
    except (OSError, ValueError):
        if armed:
            return _wrtscp_deny(root, "root canonicalization fault",
                                "the session repo root could not be canonicalized, so the write cannot be "
                                "cleared against the armed slice", "denied a {} (unresolvable repo root, "
                                "armed)".format(tool_name))                                  # row 15
        return _wrtscp_deny(root, "root canonicalization fault (cannot-evaluate)",
                            "the session repo root could not be canonicalized, so the write target's "
                            "containment could not be proven; a cannot-evaluate denies rather than allowing "
                            "an unverified write",
                            "denied a {} (repo root canonicalization fault)".format(tool_name))  # row 11 (fault -> deny)
    if not _is_absolute(file_path):
        # A relative file_path can only arrive via MultiEdit (abs-paths covers the rest). Un-armed EN-8
        # makes no cwd-trusting determination (the sibling absolute_paths hook owns the relative case);
        # armed, a target that cannot be pinned absolutely is a cannot-evaluate.
        if armed:
            return _wrtscp_deny(root, "relative file_path (armed)",
                                "the {} file_path is relative, so the write target cannot be pinned against "
                                "the armed slice; supply an absolute path".format(tool_name),
                                "denied a {} with a relative file_path (armed)".format(tool_name))  # row 14
        return _allow()                                                                     # row 6
    try:
        target = os.path.realpath(file_path)
    except (OSError, ValueError):
        if armed:
            return _wrtscp_deny(root, "target canonicalization fault",
                                "the {} target path could not be canonicalized, so it cannot be cleared "
                                "against the armed slice".format(tool_name),
                                "denied a {} (unresolvable target, armed)".format(tool_name))  # row 15
        return _wrtscp_deny(root, "target canonicalization fault (cannot-evaluate)",
                            "the {} target path could not be canonicalized, so its containment could not be "
                            "proven; a cannot-evaluate denies rather than allowing an unverified write"
                            .format(tool_name),
                            "denied a {} (target canonicalization fault)".format(tool_name))  # row 11 (fault -> deny)
    if armed and decl_status == "bad":
        return _wrtscp_deny(root, "declaration bad",
                            "{}; an armed session cannot clear a write against an unreadable declaration "
                            "(distinct from an absent one, which is inert)".format(decl),
                            "denied a {} because the write-scope declaration could not be read"
                            .format(tool_name))                                              # row 13
    if armed:  # decl_status == "ok": bind the declaration to THIS tree (a stale/copied/moved decl is BAD)
        try:
            decl_root_c = os.path.realpath(decl["worktree_root"])
        except (OSError, ValueError):
            decl_root_c = None
        if decl_root_c != root_c:
            return _wrtscp_deny(root, "worktree_root mismatch",
                                "the write-scope declaration's worktree_root does not resolve to this repo "
                                "(a stale, copied, or moved declaration), so it cannot confine this tree",
                                "denied a {} (declaration worktree_root mismatch)".format(tool_name))  # row 13
    # --- Structural other-repo / nested-repo denial, in BOTH regimes once the root resolves (rows 7, 16) ---
    within = _gensrc_within(target, root_c)
    if within == "err":
        if armed:
            return _wrtscp_deny(root, "containment fault",
                                "the write target's containment against this repo could not be resolved, so "
                                "it cannot be cleared against the armed slice",
                                "denied a {} (containment fault, armed)".format(tool_name))  # row 15
        return _wrtscp_deny(root, "containment fault (cannot-evaluate)",
                            "the write target's containment against this repo could not be resolved, so a "
                            "no-match could not be proven; a cannot-evaluate denies rather than allowing an "
                            "unverified write",
                            "denied a {} (containment fault)".format(tool_name))  # row 11 (fault -> deny)
    slice_name = decl["slice"] if armed else None
    # --- FROZEN DENIALS FIRST (round-2 finding 2): the ALWAYS-FROZEN registry set AND the committed frozen
    # floor are evaluated BEFORE the companion-store admission and the structural other-repo allow below, so a
    # target that is a frozen registry file, or on the floor, DENIES even when it resolves (via a symlink, or
    # a floor entry that resolves) OUTSIDE the session repo INTO a declared companion store. Were the
    # companion-store allow to run first, a symlinked .aiqt/orchestration.local.json (or a floor entry)
    # resolving into a declared store would win a HEAD ALLOW and bypass the freeze. Each layer fires in BOTH
    # regimes and independent of the other, so an absent/deleted/permissive .aiqt/frozen.json and a permissive
    # scope declaration alike cannot let a covered write reach a frozen registry file. COVERAGE (CLAUDE-F2):
    # for a FILE entry the realpath equality match closes the symlink case (target and entry follow the same
    # alias). For a floor TREE entry, coverage is enforced on BOTH the realpath'd target (a symlinked ROOT is
    # caught by _wrtscp_target_matches) AND the LEXICAL, unresolved file_path (_wrtscp_lexical_tree_hit,
    # below), so a symlink BELOW a frozen tree entry that would let os.path.realpath escape the entry's
    # subtree - even into a declared companion store - is denied by the lexical match rather than admitted. ---
    always_frozen_hit = _wrtscp_target_matches(_WRTSCP_ALWAYS_FROZEN, target, root_c)
    if always_frozen_hit is None:
        return _wrtscp_deny(root, "always-frozen containment fault",
                            "an always-frozen registry entry could not be resolved for containment, so a "
                            "no-match cannot be proven",
                            "denied a {} (always-frozen containment fault)".format(tool_name))
    if always_frozen_hit:
        return _wrtscp_deny(root, "frozen registry",
                            "the write target is an orchestration registry file ({}), which carries "
                            "guardrail configuration (the companion_stores cross-repo allow-list and the "
                            "yield-tool roster) read from EITHER .aiqt/orchestration.local.json or the "
                            "committed .aiqt/orchestration.json. Declaring a companion store is a "
                            "scope-widening, guardrail-configuration change under human authorization, so "
                            "every registry file is frozen against the actor's own covered writes in both "
                            "regimes (independent of the committed frozen floor, un-lowerable by a scope "
                            "declaration, and evaluated BEFORE the companion-store allow so a symlink into a "
                            "declared store cannot bypass it) and cannot be self-widened through a guarded "
                            "tool; set it through the harness/adopter orchestration-registry surface instead."
                            .format(target),
                            "denied a {} to a frozen orchestration registry file".format(tool_name))
    # --- Frozen-floor denial (rows 8, 9, 10, 17, 18): fires whenever a floor is PRESENT, and an armed
    # session additionally requires one; only an un-armed session with a genuinely-absent floor is inert ---
    floor_status, floor = _load_frozen_floor(root)
    if floor_status == "bad":
        return _wrtscp_deny(root, "floor bad",
                            "{}; a present-but-unreadable floor is a cannot-evaluate and denies (an absent "
                            "floor is inert un-armed)".format(floor),
                            "denied a {} because the frozen floor could not be read".format(tool_name))  # rows 9/17
    if floor_status == "absent":
        if armed:
            return _wrtscp_deny(root, "floor absent (armed)",
                                "an armed session requires a committed frozen floor (.aiqt/frozen.json; an "
                                "explicit empty list is valid), so a deleted floor cannot silently downgrade "
                                "an armed session",
                                "denied a {} because an armed session has no frozen floor".format(tool_name))  # row 17
        # Un-armed with no floor: the frozen layer is inert; the structural denials below still apply.
        # (Fall through to the un-armed ALLOW below, row 10 -> row 12.)
        floor = []
    else:
        on_floor = _wrtscp_target_matches(floor, target, root_c)
        if on_floor is None:
            return _wrtscp_deny(root, "floor containment fault",
                                "a frozen-floor entry could not be resolved for containment, so a "
                                "no-match cannot be proven",
                                "denied a {} (floor containment fault)".format(tool_name))   # rows 9/17 (fault)
        if on_floor:
            return _wrtscp_deny(root, "frozen target",
                                "the write target is on the frozen floor ({}): a generated output or frozen "
                                "rotation data, never hand-written through a guarded tool. The floor outranks "
                                "the scope declaration (deny over allow); edit the source and regenerate."
                                .format(target),
                                "denied a {} to a frozen path".format(tool_name))            # rows 8/18
        # CLAUDE-F2 (round-6): ALSO deny when the LEXICAL (unresolved) file_path lies inside a frozen TREE
        # entry, so a symlink BELOW the entry cannot let os.path.realpath escape the entry's coverage and be
        # admitted (e.g. as a companion-store write). The realpath match above covers a symlinked ROOT; this
        # lexical check closes the symlink-below-the-entry sibling. Fail-closed (adds denials only for a
        # lexical name inside a frozen tree), evaluated BEFORE the companion-store admission below.
        if _wrtscp_lexical_tree_hit(file_path, root, root_c, floor):
            return _wrtscp_deny(root, "frozen tree (lexical)",
                                "the write target's lexical path ({}) is inside a frozen-floor TREE entry; a "
                                "symlink below the entry must not let it resolve off the frozen tree and be "
                                "admitted. The floor outranks the scope declaration (deny over allow); edit "
                                "the source and regenerate.".format(file_path),
                                "denied a {} whose lexical path is inside a frozen tree".format(tool_name))
    # --- Structural other-repo / nested-repo denial, in BOTH regimes once the root resolves (rows 7, 16).
    # Reached only AFTER the frozen denials above, so the companion-store ALLOW can never override a frozen
    # registry file or a frozen-floor target that happens to resolve into a declared store. ---
    if within == "out":
        # SANCTIONED COMPANION-STORE path: a write into an adopter-DECLARED companion-store repo (an exact
        # repo-root match; e.g. the sole orchestrator's own durable store beside the code repo) is the single
        # most routine legitimate cross-repo write, so it is ALLOWED and AUDITED rather than denied. The match
        # is fail-closed by construction (only a well-formed declaration resolving to a real git toplevel is
        # honoured), so an UNDECLARED other repo, or a bad declaration, still falls through to the denial.
        store = _wrtscp_target_companion_store(target, _wrtscp_companion_stores(root))
        if store is not None:
            # CLAUDE-F2 (round-7): freeze the registry TRANSITIVELY. A declared companion store's OWN
            # orchestration registry file (.aiqt/orchestration.local.json / .aiqt/orchestration.json) carries
            # the SAME scope-widening guardrail configuration (its own companion_stores / yield-tool roster,
            # which _orch_registry reads next session when that store is the session root), so a covered write
            # to it completes a cross-session companion-store self-widening chain through the exact guarded-tool
            # path the root freeze exists to close. Resolve the always-frozen registry entries against the
            # DECLARED STORE root and DENY a match, so the freeze follows into every declared store, not only
            # the session repo. Legitimate NON-registry companion-store writes still allow+audit below.
            store_frozen_hit = _wrtscp_target_matches(_WRTSCP_ALWAYS_FROZEN, target, store)
            if store_frozen_hit is None:
                return _wrtscp_deny(root, "companion-store registry containment fault",
                                    "an always-frozen registry entry could not be resolved for containment "
                                    "against the declared companion store {}, so a no-match cannot be proven"
                                    .format(store),
                                    "denied a {} (companion-store registry containment fault)"
                                    .format(tool_name))
            if store_frozen_hit:
                return _wrtscp_deny(root, "frozen companion-store registry",
                                    "the write target is a DECLARED companion store's own orchestration "
                                    "registry file ({}), which carries the same scope-widening guardrail "
                                    "configuration (the companion_stores cross-repo allow-list and the "
                                    "yield-tool roster) as the session repo's registry. A covered write to it "
                                    "would complete a cross-session companion-store self-widening chain, so "
                                    "every declared store's registry is frozen transitively against the "
                                    "actor's own covered writes; set it through the harness/adopter "
                                    "orchestration-registry surface instead.".format(target),
                                    "denied a {} to a declared companion store's frozen orchestration "
                                    "registry file".format(tool_name))
            ev = _orch_event_warn(root, "wrtscp", "allow",
                                  "companion-store write to the declared store {} (target {})"
                                  .format(store, target))
            if ev:
                # the allow stands; its unwritten audit row is surfaced, never lost silently
                return _allow_note("AIQT guardrail: allowed a {} to the declared companion store {} "
                                   "(rule wrtscp). {}".format(tool_name, store, ev))
            return _allow()                                                                   # companion store
        return _wrtscp_deny(root, "outside toplevel",
                            "the write target resolves OUTSIDE this repository ({}); a guarded-tool write "
                            "landing outside the session repo is denied as a floor the scope declaration "
                            "cannot lower, UNLESS the target repo is declared a companion store. The "
                            "sanctioned path for a legitimate cross-repo write (such as the orchestrator's "
                            "own durable store) is to declare that repo's root in the orchestration "
                            "registry's `companion_stores`; writes into a declared companion store are "
                            "allowed and audited. An undeclared other repo stays denied.".format(target),
                            "denied a {} to a target outside this repository".format(tool_name))  # rows 7/16
    nested = _wrtscp_nested_repo(target, root_c)
    if nested is None:
        if armed:
            return _wrtscp_deny(root, "nested-repo probe fault",
                                "the write target's repository could not be resolved, so it cannot be "
                                "cleared against the armed slice",
                                "denied a {} (nested-repo probe fault, armed)".format(tool_name))  # row 15
        return _wrtscp_deny(root, "nested-repo probe fault (cannot-evaluate)",
                            "the write target's repository could not be resolved, so it could not be proven "
                            "the target is not in a nested or foreign repository; a cannot-evaluate denies "
                            "rather than allowing an unverified write",
                            "denied a {} (nested-repo probe fault)".format(tool_name))  # row 11 (fault -> deny)
    if nested:
        return _wrtscp_deny(root, "nested/foreign repo",
                            "the write target sits in a NESTED or FOREIGN git repository under or beside "
                            "this repo ({}); it is denied as a floor the scope declaration cannot lower. "
                            "Run the write from a session rooted in that repo.".format(target),
                            "denied a {} to a nested or foreign repository".format(tool_name))  # rows 7/16
    if not armed:
        return _allow()  # row 12: un-armed, structural cleared, not frozen -> no slice confinement in effect
    # --- ARMED slice confinement (rows 19, 20, 21) ---
    allow = decl["allow"]
    for kind, body in allow:
        if _wrtscp_entry_wholly_frozen(kind, body, floor):
            return _wrtscp_deny(root, "allow entry declares past the floor",
                                "the write-scope declaration for slice {!r} carries an allow entry that is "
                                "wholly on the frozen floor; a declaration cannot declare past the floor and "
                                "is surfaced as malformed rather than partially honoured".format(slice_name),
                                "denied a {} (declaration for slice {!r} declares past the frozen floor)"
                                .format(tool_name, slice_name))                              # row 19
    in_scope = _wrtscp_target_matches(allow, target, root_c)
    if in_scope is None:
        return _wrtscp_deny(root, "allow containment fault",
                            "an allow entry could not be resolved for containment, so in-scope cannot be "
                            "proven for slice {!r}".format(slice_name),
                            "denied a {} (allow containment fault, slice {!r})".format(tool_name, slice_name))  # row 15
    if in_scope:
        return _allow()  # row 20: in repo, not frozen, in the declared slice scope -> silent allow
    return _wrtscp_deny(root, "out of scope",
                        "the write target {} is not in the declared scope for slice {!r}. This slice's "
                        "writes are confined to its declaration; re-declare the slice through the harness to "
                        "widen scope (widening stays on the harness side, never a guarded-tool call)."
                        .format(target, slice_name),
                        "denied a {} outside the declared scope for slice {!r}"
                        .format(tool_name, slice_name))                                      # row 21


# --- dispatcher ---------------------------------------------------------------------------------------
HANDLERS = {
    "diff_wall_stop": diff_wall_stop,
    "diff_source_pretool": diff_source_pretool,
    "commit_identity": commit_identity,
    "absolute_paths": absolute_paths,
    "bash_absolute_paths": bash_absolute_paths,
    "git_explicit_binding": git_explicit_binding,
    "git_stash_ref": git_stash_ref,
    "git_discard": git_discard,
    "protected_line": protected_line,
    "branch_root": branch_root,
    "gate_weakening": gate_weakening,
    "commit_msg_subst": commit_msg_subst,
    "secrets_shift_left": secrets_shift_left,
    "gensrc_guard": gensrc_guard,
    "write_scope_guard": write_scope_guard,
    "orch_stop_guard": orch_stop_guard,
    "orch_teammate_idle": orch_teammate_idle,
    "orch_yield_tool": orch_yield_tool,
    "orch_ask_guard": orch_ask_guard,
    "orch_truncation_guard": orch_truncation_guard,
    "orch_untracked_wait_loop": orch_untracked_wait_loop,
    "orch_dispatch_ledger": orch_dispatch_ledger,
    "review_dispatch_pin": review_dispatch_pin,
    "orch_prompt_stamp": orch_prompt_stamp,
    "orch_resume_audit": orch_resume_audit,
    "orch_resume_barrier": orch_resume_barrier,
}

# Handler -> event class, so the dispatcher can decide its ERROR posture from the argv MODE alone,
# without reading the (possibly unreadable) payload. This is the load-bearing half of the fail-closed
# design: a Stop/SubagentStop handler must NEVER exit 2 ON AN ERROR PATH, because a hard Stop block could
# re-fire on the forced continuation and wedge the session (no stop_hook_active field, no documented loop
# bound), so on ANY error (unreadable stdin, JSON parse failure, non-dict payload, or a handler crash) it
# emits a non-blocking systemMessage warning and exits 0. A DELIBERATE backlog-deny is the intended
# exception (the documented Stop block mechanism, bounded by the loop cap); only a PreToolUse handler
# fails closed via exit 2 on error.
HANDLER_EVENT = {
    "diff_wall_stop": "Stop",
    "diff_source_pretool": PRETOOL,
    "commit_identity": PRETOOL,
    "absolute_paths": PRETOOL,
    "bash_absolute_paths": PRETOOL,
    "git_explicit_binding": PRETOOL,
    "git_stash_ref": PRETOOL,
    "git_discard": PRETOOL,
    "protected_line": PRETOOL,
    "branch_root": PRETOOL,
    "gate_weakening": PRETOOL,
    "commit_msg_subst": PRETOOL,
    "secrets_shift_left": PRETOOL,
    "gensrc_guard": PRETOOL,
    "write_scope_guard": PRETOOL,
    "orch_stop_guard": "Stop",
    "orch_teammate_idle": "TeammateIdle",
    "orch_yield_tool": PRETOOL,
    "orch_ask_guard": PRETOOL,
    "orch_truncation_guard": PRETOOL,
    "orch_untracked_wait_loop": PRETOOL,
    "orch_dispatch_ledger": "PostToolUse",
    "review_dispatch_pin": PRETOOL,
    "orch_prompt_stamp": "UserPromptSubmit",
    "orch_resume_audit": "SessionStart",
    "orch_resume_barrier": PRETOOL,
}


def _dispatcher_fail_open_warn(handler_name, detail):
    """A fail-open dispatcher-level error for a Stop/SubagentStop/SessionStart/TeammateIdle/
    UserPromptSubmit/PostToolUse handler: a non-blocking systemMessage on exit 0, so no error on
    these paths can wedge a session, trap a teammate, or block a human prompt. It prints the note and
    returns the exit code 0, so main returns it directly (a note constructor's call is only ever the
    direct value of a return)."""
    print(json.dumps({"systemMessage": (
        "AIQT guardrail: the {} check could not run ({}); surfacing a warning rather than blocking "
        "(non-blocking by design on this event).".format(handler_name, detail))}))
    return 0


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
    is_fail_open = HANDLER_EVENT[handler_name] in FAIL_OPEN_EVENTS
    if len(argv) != 1:
        detail = "expected exactly one mode argument, got {}".format(len(argv))
        if is_fail_open:
            # A Stop handler's ERROR path never exits 2 (a deliberate deny does, via the orchestration
            # stop guard), not even on a malformed invocation: WARN and exit 0.
            return _dispatcher_fail_open_warn(handler_name, "bad invocation: {}".format(detail))
        print("aiqt_hooks: {} ({}); failing closed".format(handler_name, detail), file=sys.stderr)
        return 2
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            raise ValueError("payload is not a JSON object")
    except Exception as exc:  # any failure to read the payload is an unreadable payload
        # Unreadable/malformed stdin, JSON parse error, UnicodeDecodeError, or a non-dict payload, and ALSO a
        # RecursionError (deeply nested JSON) or a MemoryError: the old narrow except let those escape as a
        # traceback with exit 1, which the platform treats as non-blocking. Every exception is named.
        detail = "{}: {}".format(type(exc).__name__, exc)
        if is_fail_open:
            # A Stop handler's ERROR path never exits 2: surface a non-blocking warning and exit 0, so no Stop
            # payload (including a bare '{' or any garbage) can ever wedge the session.
            return _dispatcher_fail_open_warn(handler_name, "unreadable payload: {}".format(detail))
        # A PreToolUse hook that cannot read its payload cannot clear the action, so it fails CLOSED.
        # exit 2 is the platform's blocking path; the diagnostic reaches Claude on stderr.
        print("aiqt_hooks: unreadable hook payload ({}); failing closed".format(detail), file=sys.stderr)
        return 2
    try:
        code, stdout_obj, stderr_text = HANDLERS[handler_name](data)
    except Exception as exc:  # a handler crash is an unreadable result
        if is_fail_open:
            # Same event-aware posture for a crash inside the Stop handler (e.g. the detector throws on a
            # pathological message): WARN and exit 0, never exit 2.
            return _dispatcher_fail_open_warn(
                handler_name, "handler crash: {}: {}".format(type(exc).__name__, exc))
        # A PreToolUse handler crash fails closed (block), not pass.
        print("aiqt_hooks: handler {} failed ({}: {}); failing closed".format(
            handler_name, type(exc).__name__, exc), file=sys.stderr)
        return 2
    if stdout_obj is not None:
        print(json.dumps(stdout_obj))
    if stderr_text:
        print(stderr_text, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
