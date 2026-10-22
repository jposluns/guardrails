#!/usr/bin/env python3
"""Check token-level parity between the local and CI quality-gate rosters.

The gate reads tools/run_all_checks.sh and the two CI workflows (CI_WORKFLOWS:
.github/workflows/quality.yml and .github/workflows/msg-leaks.yml) as data. It
executes none of them. Every path is absolute and derived from this file's
resolved repository root. The --self-test alone also runs a scratch copy of the runner
under bash, with stub python3 and gitleaks commands, to prove a failing gate is named
(GATE FAILED: <name> (exit <n>)) and listed (FAILED GATES: ...) before RESULT: FAIL.

Identity is the normalized command, including all script arguments. Python and shell
launcher words are removed. The recognized interpreter-only flags -I, -B, -E, -s,
-P, and -u, including glued forms such as -IB, do not affect identity. The value of a
runtime-derived flag (--base, --protected, --head) is masked as <ref:expression>,
preserving which expression supplied it, only in the CI workflow and only when it
is byte-for-byte one of the reviewed MASKED_REF_EXPRESSIONS AND its raw workflow
spelling, before shell de-quoting, is byte-for-byte that expression's one
reviewed spelling, its double-quoted form as quality.yml writes it; tokenization
removes quotes and escaping backslashes, so any other spelling that de-quotes
into a reviewed expression (a backslash-escaped, backslash-split,
adjacent-quoted or single-quoted one) hands the gate different bytes at runtime
and is cannot-evaluate (masked-spelling), never masked. A runtime-derived
value is exactly one that carries $ or a backquote: any other such value is
cannot-evaluate, never masked, a literal <ref:...> placeholder spelling included
(the placeholder is the OUTPUT of this masking, accepted back only from the
reviewed in-gate allowlist and its canonical identities, so a quoted <ref:...>
literal in a workflow never collapses into a masked identity), and a change
among the reviewed runtime spellings (including one making two refs identical,
a self-comparison) diverges; a value carrying neither character stays in
identity as a kept literal (a tilde, glob or brace word included), so two
different literals diverge. In the local runner every run_gate word is held to
a character allowlist that admits no expansion at all, so a runtime-derived
flag value there is refused, never masked. A ${{ }} GitHub expression anywhere
in a run: value is cannot-evaluate, not masked, read on the raw lines inside
the block scalar before shell-comment stripping (GitHub substitutes
expressions into the script text before the shell runs, comments included, so
a substituted value carrying a newline leaves the comment), so a whole-line or
trailing shell comment carrying one is refused too. The scalar extent is read
by a conservative subset of the YAML block-scalar rules: the content
indentation is the first non-empty line's; a leading blank line indented
past it is refused (scalar-blank-indent) because YAML rejects that layout,
while a later blank line stays inside the scalar wherever it falls; the
first non-empty line below the content indentation ends the scalar, a
comment line included, so a dedented YAML comment after the block is not
script text, and a dedented line that re-enters the step is refused
(step-structure); a tab on a scalar line is refused (yaml-tab) even where
YAML accepts one in content; and a folded run: scalar is refused
(folded-run), so the subset refuses, never passes, the layouts it does not
read. Every other argument remains
order-preserving and identity-relevant. Duplicates collapse because
comparison is set-based.

REQUIRED STATUS (multi-job shape). The CI member set is the union over every job,
so a gate moved from one job to another is still one member, and a gate dropped
from every job is still a local-only finding.
Job set: the rule reads the job set the way YAML does or refuses. Under jobs:, a
job id is read only from a plain key at indent 2 that GitHub accepts as a job id
(JOB_HEADER_RE) and that no YAML version reads as a boolean or null, and every
job's properties start at indent 4. A quoted, spaced, flow, complex or list key at
indent 2, a line at indent 1 or 3, a job body that starts deeper than indent 4, a
repeated top-level or job-level key, and a repeated job id are each cannot-evaluate
(exit 2), never skipped. Any top-level line ends the jobs: mapping.
Result masking: on any job, in a workflow of any size, each of continue-on-error,
uses, defaults and container (RESULT_MASKING_JOB_KEYS) is a required-status finding
(exit 1) whatever its value. The set is derived from GitHub's documented job-level
syntax (jobs.<job_id>.*). continue-on-error is documented as letting the run pass
when the job fails. uses hands the job's result to a reusable workflow this rule
does not read. defaults (run.shell) and container replace the program or image every
run: step executes in, so a gate command need not run at all. The other job keys
cannot turn a failing gate into a success result: if:, needs:, timeout-minutes,
concurrency and environment can only skip, cancel or fail a job, which Quality gates
reads as not success; a strategy matrix job's result is failure when any leg fails
(GitHub behaviour, not observed here); name, runs-on, permissions, outputs, env, services,
with and secrets do not decide a result (with and secrets apply only under uses).
The step-level keys that could (continue-on-error, if:, timeout-minutes, and any
shell: other than the exact line shell: bash) are outside the recognized step keys
and stay cannot-evaluate, as does a gate command chained with || true.
Step shell: every step with a run: key carries exactly one STEP_SHELL_LINE
(shell: bash). GitHub's workflow syntax reference documents a step-level shell
(jobs.<job_id>.steps[*].shell) as overriding the job's default, and a job's
default as overriding a workflow default of the same name (the most specific
default wins). That a step shell also overrides a workflow's defaults.run.shell
is inferred from those two statements, not documented for that case. Without
the line, a top-level defaults: run: shell: true {0} would make every run:
step, this gate's own included, exit 0 without running its command (inferred
from GitHub's custom-shell syntax, not observed on Actions). A run: step
without the line is a required-status finding (exit 1); a second shell: line
in one step and a shell: line on a step with no run: key are cannot-evaluate.
A top-level defaults: key stays outside TOP_LEVEL_KEYS (cannot-evaluate) and a
job-level one stays a result-masking finding.
The line closes the defaults.run.shell route only. It does not close
defaults.run.working-directory at workflow or job level: a step shell does not
override it, and every gate is invoked by a relative path (python3 -I -B
tools/...), so a defaults block can still choose the directory each gate's
script is resolved in, and with it which file runs, this gate's own
invocation included. That route stays refused only where the local runner
runs this gate.
Any step key at indent 8 before a job's first step item (run:, uses:, env:,
with:, shell: or another) is cannot-evaluate (exit 2); YAML rejects that shape.
Aggregator: a workflow with more than one job must route every job into the one
aggregated status that workflow reports (each CI_WORKFLOWS file reports its own
required status). Exactly one job is named "Quality gates"
(REQUIRED_STATUS_NAME); its job-level if: is always() (GitHub reads a skipped
required job as passing, so without always() a failed or cancelled needed job would
skip it and the status would read green); its needs: lists every other job exactly
once and nothing else; and the job, from the line after its id, matches
REQUIRED_STATUS_JOB line for line, ignoring blank and whole-line comment lines and
reading the needs: key with its nested lines as one slot. The pin is the logic
check: the pinned step exits 0 only when every needed result is the word success,
and the self-test runs the pinned run: body under bash with all-success, failure,
cancelled, skipped, unknown, empty and unset results, and once with its exit 1
deleted, so a pin that could pass a failure fails the self-test. An exact pin was
chosen over a semantic reading because this gate does not model bash: any edit to
that job, harmless or not, is a finding until the pin and the workflow change
together under review. Each departure is a required-status finding (exit 1). A
needs: entry outside the recognized spellings (a block list of plain job ids, a
one-line flow list, or one plain scalar) is cannot-evaluate. A one-job workflow is
its own status and carries no aggregator rule, only the result-masking rule.
Not checked: the branch-protection setting itself (that Quality gates and
Message/metadata leaks are both required checks, and that no other job reports
either name through an expression in its name:); GitHub's evaluation of
needs.*.result, if: always(), cancellation and matrix results, which is
documented behaviour this gate does not observe;
step-shell precedence over defaults.run.shell (documented over a job default,
inferred over a workflow default), also not observed; defaults.run.working-directory
(Step shell, above); and the
runner image, runs-on labels, services and environment values (a job or step env:
can set a variable such as BASH_ENV that changes what an accepted gate command
does). Inside a run: block the exact line exit 1 is accepted, because it can only
end a step red; every other exit spelling stays unclassified.
TWO WORKFLOWS. The CI side is the union of the named workflows in CI_WORKFLOWS:
quality.yml and msg-leaks.yml, which holds the message/metadata leak scan alone
so that only its one job re-runs on a PR title/body edit. A gate may run in
either file and is still one member. Each named workflow is required: a missing
or unreadable one is cannot-evaluate (exit 2), never an empty side. The
job-set, step-shell, result-masking and aggregator rules above apply to each
file on its own, so the one-job msg-leaks.yml is its own status; that branch
protection requires it beside Quality gates is not checked here. msg-leaks.yml
may run only that scan
and its self-test (MSG_LEAKS_WORKFLOW_MEMBERS): any other member there is a
relocation finding (exit 1), because a gate moved from quality.yml into
msg-leaks.yml leaves the aggregated Quality gates status silently and re-runs
on every PR metadata edit.
TRIGGER. The message/metadata leak scan (MSG_LEAKS_SCAN) reads the PR title and
body, so the workflow that runs it must list opened, synchronize, reopened and
edited (MSG_LEAKS_PULL_REQUEST_TYPES) in its pull_request types, and a workflow
that does not run it must not carry edited on pull_request or
pull_request_target (EDIT_EVENTS), because edited re-runs every job of that
workflow on a metadata-only change. Each departure is a trigger finding (exit
1). The rule reads the top-level on: block: an event key at indent 2 with no
value reads as GitHub's default types (opened, synchronize, reopened); types:
at indent 4 is a one-line flow list, one plain scalar, or a block list at
indent 6; and a one-line on: value naming events gives each its default types.
Every key this rule reads must be a PLAIN name whose colon is followed by a
space or ends the line: a quoted key, a key with whitespace before its colon,
a non-name spelling (an explicit '?' key, a tag such as !!str, or any other
character outside a plain name), and a colon directly followed by its value
(types:[...] or on:[...], which YAML reads as one plain scalar, never as a
key) are each cannot-evaluate (exit 2), on the top-level on: line itself, at
indent 2 and under an edit event alike (a bare on with no colon too; a plain
on: followed by a scalar, a one-line flow list, or a block of events stays
read), as are a
line at indent 2 with no colon, a first line under on: at any indent other
than 2, a line under an edit event before any indent-4 property (an event's
properties are read only at indent 4), a line at indent 1 or 3 anywhere in
on:, and under an EDIT_EVENTS key any other shape (a flow mapping, a repeated
event or types: key, an item that is not a plain name, or a sequence item at
indent 4, which YAML reads as an indentless sequence). NOT READ
(disclosed): the push trigger, and every filter or condition that can keep a
listed event from scheduling the workflow or a job: branches:,
branches-ignore:, paths:, paths-ignore:, and a job- or step-level if:. Any of
them can stop the scan workflow on an edited event; reachability is outside
token-parity scope (the reachability residual below).

Exit convention:
  0  extraction succeeded, every difference has an active exception, no exception is
     stale or reversed, and every mandatory member is present on both sides
  1  an evaluated parity finding, stale or reversed exception, missing mandatory
     member, or required-status finding
  2  cannot-evaluate, including an unreadable input, malformed control input, empty
     extraction, unsupported shell or YAML shape, unresolved command value, hidden
     action, circular runner invocation, or shadow-scan discrepancy

DISCLOSED RESIDUAL. This is token-level set parity only. It does not compare
environment values, operating systems, tool versions, execution order, multiplicity,
step placement across jobs, labels, or whether the shell harness propagates a child
failure. Interpreter-flag differences are intentionally removed from identity and are
owned by check_python_launcher_isolation.py. Scope is exactly the three named files
(the runner and CI_WORKFLOWS), so gates in other workflows, such as the scheduled
currency.yml, are not enumerated. Argument order is identity-relevant,
making a harmless reorder fail loud. Coordinated removal of both of this gate's own
invocations cannot be detected if nobody runs the remaining file manually. The repository
contracts (REPO_GATES) and the cited-standards contract's --self-test are
MANDATORY_MEMBERS, so removing one of them from both runners together is a
missing-mandatory finding, not a silent pass (claude PR #520 round 2 MEDIUM-1). The
extractors implement a disclosed shell and YAML subset; an unknown construct is
cannot-evaluate rather than a clean pass. Fail-closed cases include an unknown
top-level or job-level workflow key, an exit outside the exact terminal summary
blocks or the top-level directory-binding line before the first gate, unbalanced
if/fi nesting in the runner, a duplicate else or an empty then or else branch,
any character outside printable ASCII (0x20-0x7E) plus tab and newline in
any input file, job content without a job mapping, a run_gate()
dispatcher body outside its recognized shape, a run_gate() function definition
inside an if branch, a run_gate word (label or argument)
outside the gate-line character allowlist, a masked runtime-value flag in the
workflow whose expression is not reviewed, and a runner
assignment to failed or failed_names other than the top-level initializer before the
first gate or the gitleaks failure branch. A runner line holding $[ ], or naming
failed or failed_names inside $(( )) or inside a ${ } with a subscript or a colon, is
treated the same way, because bash can assign there.
The remaining runner scaffold is an ALLOWLIST of the exact line shapes the two real
runners use (tools/run_all_checks.sh and the adapted opf/tools/run_all_checks.sh),
not a blocklist of known-bad spellings. The only recognized [ ] tests are
if [ "$failed" -ne 0 ]; then, if [ "$notrun" -ne 0 ]; then, and the two exact
gitleaks lookup lines, so a -v operand (whose subscript can assign), any NAME[...]
subscript operand, a bare [ ] line, and every unlisted test form are unclassified.
set -uo pipefail or set -euo pipefail, and the directory-binding
cd "$(dirname "$0")/.." || exit 2 line, are accepted only at top level before the
first gate. A bare then or done is never a runner line, and else is accepted only
inside an open if block that holds at least one then-branch statement and is not
already in its else branch. Every other assignment, export or echo line must be
byte-for-byte one of the
finite scaffold lines the two real runners contain (RUNNER_SCAFFOLD_LINES, compared
after comment stripping and whitespace trimming), so a spelling the runners do not
contain is unclassified even when its value looks inert: bash evaluates a value
assigned to an integer special variable as arithmetic (RANDOM=failed=0 resets
failed), a compound array subscript assigns (x=([failed=0]=1)), and notrun=0 is
accepted only at top level before the first gate, where the real runner puts it,
because a later notrun=0 would hide the NOT RUN disclaimer. Behind that membership,
each expansion must still be a plain $NAME, ${NAME} or $? of a name the runner
itself sets or manages (failed, notrun, failed_names, name, rc, gitleaks_rc, HOME,
PATH): under set -u, a plain expansion of any other, possibly unset, variable
(x=$BASH_ENV) ends the runner mid-roster exactly as an abort expansion does. A
${...:?...} abort expansion is refused on ANY runner line, including inside a
runtime-value flag on a run_gate line, ahead of the gate-word character allowlist
that also refuses every expansion spelling there, because it exits the runner
mid-roster with a non-zero status when its variable is unset.
Deeper nested non-gate YAML (under on:, env:, with:, or strategy:) is structurally
recognized but not exhaustively schema-validated; the shadow scan still prevents a
tools/ gate from hiding there. Reachability is outside token-parity scope: a gate is
counted as declared regardless of an enclosing conditional, whether a job-level if:, a
shell conditional inside a run: block, or an if/fi in the local runner, that could keep
it from running; the comparison is of declared rosters, not reachable ones. Validating
a single gate's own argument semantics beyond the runtime expression is that gate's
responsibility, not this one's. Two disclosed masking/parse edges remain: tokenization
does not preserve quote type, so a $ inside single quotes is treated as runtime-derived
like a double-quoted one; and a duplicate run: key within one step is counted as two
members although YAML keeps one. The shadow scan has one soft edge: exotic quoting
outside the supported grammar could hide a tools/ string from comment stripping.
The runner's failure-state rules are lexical and shape-based: the runner is
validated as printable-ASCII lines against an exact line and word allowlist
with branch-state checks; the rules see line shapes and branch positions, not
reachability or runtime values. Before any line is trusted, EVERY input file
is refused whole if any character falls outside printable ASCII (0x20-0x7E)
plus tab and newline: Python's splitlines() also breaks a line at \v, \f,
\x1c, \x1d, \x1e, \x85, U+2028 and U+2029, and its strip() also removes
Unicode spaces such as U+00A0, while bash breaks lines only at \n and reads
each of those bytes as an ordinary word character, so outside that alphabet
the lines this gate validates are not the lines bash executes. Inside that
alphabet a line break is exactly \n for both. Decoding before screening
lost forbidden characters three separate times, one Python-side layer at a
time (splitlines(), the adapter's line split and newline rejoin, and
Path.read_text()'s universal-newline translation, which turns \r into \n
before any text screen can see it), so EVERY read of a runner or workflow
file, in the live check (run_paths), in self-test vector 26, and in
tools/selftest_git_fixture_env.py's roster and the core runner's
roster checks, goes through
the ONE byte-level reader (read_runner_text): the RAW BYTES are screened
against this same alphabet before any decode, so no decode-layer newline
translation or multi-byte spelling can erase a forbidden byte ahead of the
screen, and the accepted bytes are decoded strictly as ASCII, which maps
each byte to one character and translates no newline. A self-test vector
pins every read_text or splitlines attribute and every text-mode open()
call in this file, in tools/selftest_git_fixture_env.py and in its four
registered suite runners, as spelled,
against an exact per-function allowlist, so a new read path written
through those APIs turns the self-test red for review against the reader;
a read spelled another way (getattr, an alias) is outside that tripwire.
The standalone OPF runner, which vector 26 and the selftest roster hold to
this same grammar, is additionally screened on its RAW text inside the ONE
shared adapter (adapt_standalone_runner), before the adapter's own line
split and newline rejoin could erase a forbidden character. Every run_gate
line is screened by ONE character-allowlist rule over every word after quote
removal: the label must fully match [a-z0-9][a-z0-9-]* (every label in the two
real runners does) and every other word must fully match [A-Za-z0-9_./=:-]+, a
set holding no shell metacharacter, so a word carrying any of { } * ? [ ] ~ $
or a backquote, quote, backslash or whitespace is refused statically. That
refuses the brace, glob and tilde words bash expands with no $ in them
({d4,eval,"failed=0;"}, {dashes3,true}, {x,exit}, ~ and op[f]/tools/[or]* as
labels; {--self-test,} and * as arguments) as well as every $ and backquote
spelling the earlier two-character blocklist caught (${x=failed=0}$((x)),
$((notrun=0)), $BASH_ENV). The same rule admits no expansion in an argument, so
a runtime-derived --base, --protected or --head value is refused in the local
runner outright; the reviewed MASKED_REF_EXPRESSIONS apply only to the CI
workflow, where an assigning, aborting or otherwise unreviewed expression
(${x=failed=0}, ${stub_log?}, $((1/0))) stays cannot-evaluate rather than
masked, and a reviewed expression in any raw spelling other than its one
reviewed double-quoted form is cannot-evaluate too (masked-spelling). A
reviewed workflow expression still resolves only at runtime: its
variable can be empty (the flag then receives an empty value) or unset (each
reviewed step's run block sets -u, so the step then ends early with a non-zero
status); token-level parity sees neither. The ${...:?...} abort-expansion rule
still screens every runner line first, so a masked ${...:?...} abort spelling
is refused twice over; a self-test fixture pins that rule's own diagnostic and
fails without it. The if/else/fi structure is tracked per block: a duplicate
else and an empty then or else branch are each refused (bash refuses both, so
such a file never runs at all); a run_gate() { function definition is
accepted only at top level outside every if frame, because bash runs a
definition as one ordinary, successfully-exiting statement, so a definition
inside a branch both fills the branch and replaces $?, which this grammar
formerly counted as neither; the run_gate() definition must also precede
the first run_gate invocation, because bash resolves a function name at
invocation time, so a gate line ahead of the (or with no) definition calls
an undefined name and the runner continues without recording that gate as
run or failed; and the gitleaks-block lines (the PATH append,
the gitleaks_rc capture and the gitleaks exit-code echo) and the FAILED
GATES echo are accepted only inside their real blocks AND branches, as
notrun=0, set and cd are already
position-checked: the PATH append only in the HOME guard's then branch, which
has just proven HOME non-empty; gitleaks_rc=$? only as the first statement of
the gitleaks failure branch, where $? is still the gitleaks exit code; the
exit-code echo only after that capture in the same branch; and the FAILED
GATES echo only in the then branch of the TERMINAL failed check, the one
frame whose header line sits in the recognized terminal summary tail,
after the top-level failed_names initializer. A copy placed
anywhere else, including the HOME guard's else branch and the then branch
of a copied or nested failed-check frame, is refused, so an
accepted runner cannot end early under set -u through an unbound HOME,
gitleaks_rc or failed_names, and cannot echo a stale gitleaks exit code.
What the grammar accepts and still cannot see into are these four surfaces.
One: the content of a gate script the runner invokes. Two: the environment
the runner inherits, which can change what an accepted literal line resolves
to at runtime. Three: the runner-internal ORDER of the remaining accepted
literal lines beyond the position-checked ones. No non-position-checked
scaffold echo or assignment expands anything that can be unbound, so
relocating one rewords the log (a stray RESULT: PASS echo or a notrun=1
mid-roster) without touching an exit code; the recognized [ ] test lines do
expand failure state, so a copy relocated above its initializer still ends
the runner loudly under set -u before any gate, the same bounded early-exit
class as the closed HOME, gitleaks_rc and failed_names placements. A
divergence in this surface is loud, never a silent pass: the runner itself
exits early non-zero or rewords its summary, or, for a run_gate line moved
into a branch where it can be skipped, the grammar accepts the placement but
the self-test's runtime scenario sweep fails because the executed calls
differ from the declared roster. Four: any
bash parsing behaviour this hand-written grammar does not model beyond the
checks named above. One known instance: the if-frame push keys on the
literal "; then" spelling while gitleaks member detection is token-based, so
a header spelled with extra whitespace before then is counted as a member
without opening a frame; in place that fails closed here (unbalanced-if),
and bash refuses such a rearranged file at parse time before any gate runs.
Surfaces three and four are tracked as backlog item
RUNNER-STATIC-GRAMMAR-HARDENING.
The --self-test backs the rules at runtime with a scratch copy
of the live runner: no gate failing, gitleaks and leaks failing together, gitleaks failing alone,
and each registered gate failing alone. Those scenarios run in parallel, so every
executable stub is written and closed once before they start: a fork on a sibling
thread inherits every descriptor still open at that moment, and an exec of a stub
that any process holds open for writing fails ETXTBSY, exactly as _prepare_stubs
states. That catches a reset or early exit that fires
unconditionally or on one gate's failure in the stub environment. Residual masks
include harness detection (stub_* variables, BASH_ENV, or SECONDS), environments the
stubs do not produce (including gitleaks absent and the NOT RUN branch), and untested
combinations of failing gates. Harness detection can hide even a single-gate reset;
it does not require a combination of failures or the gitleaks NOT RUN branch.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_ci_parity.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import argparse
import re
import shlex
from collections import namedtuple
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATH = ROOT / "tools" / "run_all_checks.sh"
CI_PATH = ROOT / ".github" / "workflows" / "quality.yml"
LOCAL_SOURCE = "tools/run_all_checks.sh"
CI_SOURCE = ".github/workflows/quality.yml"
MSG_LEAKS_CI_PATH = ROOT / ".github" / "workflows" / "msg-leaks.yml"
MSG_LEAKS_CI_SOURCE = ".github/workflows/msg-leaks.yml"
CI_WORKFLOWS = ((CI_SOURCE, CI_PATH), (MSG_LEAKS_CI_SOURCE, MSG_LEAKS_CI_PATH))

# Runtime-derived values deliberately lose their concrete value while retaining the
# flag in identity. A new runtime-valued flag is not masked automatically, so it
# produces a visible divergence until this reviewed set is extended.
RUNTIME_VALUE_FLAGS = frozenset({"--base", "--protected", "--head"})

# The only runtime-derived expressions a masked flag value may carry, enumerated
# from the one reviewed surface that may carry them, .github/workflows/quality.yml.
# The local runner's gate-word allowlist admits no expansion, so this set never
# applies there: neither real runner passes a runtime-derived value, and a local
# spelling that tried would be refused as a non-literal gate word.
# Membership is byte-for-byte: bash can assign inside an
# expansion (${x=failed=0} assigns the string failed=0 to x, and a later $((x)) or
# ${PATH:x:0} evaluates it as arithmetic) or abort there (${x?} without a colon,
# $((1/0))), so an unreviewed expression is cannot-evaluate rather than masked, and a
# new runtime spelling stays refused until this reviewed set is extended.
# Membership is read on the de-quoted token; the RAW spelling screen in
# _mask_value additionally requires each member's one reviewed workflow
# spelling, its double-quoted form as quality.yml writes it.
MASKED_REF_EXPRESSIONS = frozenset({
    "origin/${GITHUB_REF_NAME}",
    "origin/${GITHUB_BASE_REF}",
    "$PUSH_BEFORE",
    "$PR_HEAD_SHA",
})

# The repository-local, non-shipped gates, each named; no general .github executable allowance.
# Each is also a MANDATORY_MEMBERS entry (the cited-standards contract together with its
# --self-test), so a both-runner removal is a missing-mandatory finding.
REPO_GATES = frozenset({
    ".github/check_cited_standards_contract.py",
    ".github/check_newtab_contract.py",
})

# The one status branch protection requires. In a workflow with more than one job,
# exactly one job carries this name, a job-level if: of always(), and a needs: list
# naming every other job exactly once (module docstring, REQUIRED STATUS).
REQUIRED_STATUS_NAME = "Quality gates"
JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

# A line at the job-key indent under jobs: is read as a job only when it is a plain
# key GitHub accepts as a job id (a letter or _ first, then letters, digits, _ or -)
# that no YAML version reads as a boolean or null. Every other line there (a quoted,
# spaced, flow, complex or list key) is cannot-evaluate, because YAML can read a job
# from it that this rule would not enumerate.
JOB_HEADER_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*):")
YAML_NON_STRING_KEYS = frozenset((
    "null", "true", "false", "yes", "no", "on", "off", "y", "n"))

# Job-level keys from GitHub's workflow syntax (jobs.<job_id>.*) that can let a job
# whose gate failed conclude success, so that needs.<job>.result, which the Quality
# gates step reads, need not carry the failure. Present on any job, each is a
# required-status finding whatever its value (module docstring, REQUIRED STATUS).
RESULT_MASKING_JOB_KEYS = dict((
    ("continue-on-error",
     "GitHub documents it as letting the run pass when the job fails, so "
     "the failure need not reach needs.<job>.result"),
    ("uses",
     "a reusable-workflow call concludes from jobs in a file this rule "
     "does not read, and any of them may carry continue-on-error"),
    ("defaults",
     "defaults.run.shell can replace the program every run: step executes, "
     "so a gate command need not run or fail its step"),
    ("container",
     "the container image supplies the shell every run: step executes in, "
     "so a gate command need not run or fail its step"),
))

# The one step-level shell: spelling accepted, required once on every step with a
# run: key (module docstring, Step shell). GitHub documents a step's shell: as
# overriding the job's default shell (over a workflow default it is inferred), so
# a defaults.run.shell cannot choose the shell a gate command runs under. It does
# not close defaults.run.working-directory (module docstring, Step shell).
STEP_SHELL_LINE = "shell: bash"

# The Quality gates job, pinned line for line after its job-id line. Blank and
# whole-line comment lines are ignored, and the needs: key with its nested lines is
# compared as the one slot line below, because needs: is checked against the job set
# instead. Any other departure (a deleted exit 1, a removed shell: bash, a changed
# success filter, an added key, step or continue-on-error) is a required-status
# finding. The self-test runs the pinned run: body under bash for every
# needed-result spelling it must refuse.
REQUIRED_STATUS_NEEDS_SLOT = "    needs: <every other job>"
REQUIRED_STATUS_JOB = (
    "    name: Quality gates",
    "    if: always()",
    REQUIRED_STATUS_NEEDS_SLOT,
    "    runs-on: ubuntu-latest",
    "    steps:",
    "      - name: Every quality job succeeded",
    "        " + STEP_SHELL_LINE,
    "        env:",
    "          RESULTS: ${{ join(needs.*.result, ' ') }}",
    "        run: |",
    "          set -euo pipefail",
    '          echo "Needed job results: ${RESULTS}"',
    '          rest="${RESULTS:-none}"',
    '          rest="${rest//success/}"',
    '          rest="${rest// /}"',
    '          if [ -n "$rest" ]; then',
    '            echo "Quality gates: FAILED, every needed job must conclude '
    'success"',
    "            exit 1",
    "          fi",
    '          echo "Quality gates: PASS, every needed job concluded success"',
)

MSG_LEAKS_SCAN = "tools/check_msg_leaks.py"  # the live scan (module docstring, TRIGGER)
# The only members msg-leaks.yml may run (module docstring, TWO WORKFLOWS): its own scan and that
# scan's self-test. Any other member there is a relocation finding, because that workflow's status
# is separate from the aggregated Quality gates and re-runs on every PR metadata edit, so a gate
# moved into it would leave the aggregated required check silently.
MSG_LEAKS_WORKFLOW_MEMBERS = frozenset(
    (MSG_LEAKS_SCAN, MSG_LEAKS_SCAN + " --self-test"))
EDIT_EVENTS = ("pull_request", "pull_request_target")
DEFAULT_PULL_REQUEST_TYPES = ("opened", "synchronize", "reopened")
MSG_LEAKS_PULL_REQUEST_TYPES = DEFAULT_PULL_REQUEST_TYPES + ("edited",)

ALLOWLIST = (
    {
        "side": "ci-only",
        "identity": "tools/check_msg_leaks.py",
        "reason": (
            "The live scan requires GITHUB_EVENT_NAME and GITHUB_EVENT_PATH; "
            "locally only its deterministic self-test runs."
        ),
        "backlog": "GD-123",
    },
    {
        "side": "ci-only",
        "identity": (
            "tools/check_release_cut.py --protected <ref:origin/${GITHUB_REF_NAME}> "
            "--base <ref:$PUSH_BEFORE>"
        ),
        "reason": (
            "The push CI job supplies its runtime-derived origin ref because "
            "origin/HEAD need not exist, plus the event's before OID to check "
            "every first-parent transition in the push. The bare leg runs locally and "
            "in pull_request CI, where GITHUB_BASE_REF binds the merge parent."
        ),
        "backlog": "GD-123",
    },
    {
        "side": "ci-only",
        "identity": "tools/check_version_monotonicity.py --base <ref:origin/${GITHUB_BASE_REF}>",
        "reason": (
            "CI derives the comparison base from trusted event and repository "
            "history context; the bare leg runs on both sides."
        ),
        "backlog": "GD-123",
    },
    {
        "side": "ci-only",
        "identity": "tools/check_version_monotonicity.py --base HEAD^",
        "reason": (
            "The CI else-branch uses the history-relative literal base HEAD^ "
            "when no pull-request target ref is available; there is no local "
            "--base leg, so this literal form is CI-only by design."
        ),
        "backlog": "GD-123",
    },
    {
        "side": "ci-only",
        "identity": (
            "tools/check_branch_root.py --protected <ref:origin/${GITHUB_BASE_REF}> --head <ref:$PR_HEAD_SHA> --max-lag 200"
        ),
        "reason": (
            "The pull_request CI job compares the PR head against its target "
            "branch, both runtime-derived from the event; a local run has no "
            "PR-head or target-ref context, so only the bare --max-lag staleness "
            "leg runs locally."
        ),
        "backlog": "GD-123",
    },
    {
        "side": "ci-only",
        "identity": "tools/check_branch_root.py --protected <ref:origin/${GITHUB_REF_NAME}> --max-lag 200",
        "reason": (
            "The push-to-main CI job self-checks the protected line against its "
            "runtime-derived origin ref; there is no local equivalent of the "
            "protected-ref comparison."
        ),
        "backlog": "GD-123",
    },
    {
        "side": "local-only",
        "identity": "tools/check_branch_root.py --max-lag 200",
        "reason": (
            "The bare staleness leg is the only branch-root form that runs "
            "locally; CI always supplies a runtime-derived --protected ref, so "
            "the bare form is local-only by design."
        ),
        "backlog": "GD-123",
    },
)

MANDATORY_MEMBERS = frozenset({
    # The repository contracts (claude PR #520 round 2 MEDIUM-1): mandatory, so removing
    # one from BOTH runners together is a missing-mandatory finding (exit 1), never a
    # silent pass; a one-sided removal is already a local-only or ci-only finding.
    ".github/check_cited_standards_contract.py",
    ".github/check_cited_standards_contract.py --self-test",
    ".github/check_newtab_contract.py",
    "tools/check_ci_parity.py",
    "tools/check_ci_parity.py --self-test",
    "tools/check_git_option_table.py",
    "tools/check_git_option_table.py --self-test",
})

INTERPRETERS = frozenset({"python", "python3"})
SHELLS = frozenset({"bash", "sh"})

# The run_gate() dispatcher body carries no gate invocations, so its lines are not
# extracted; but it is validated against this exact expected shape rather than skipped
# blindly, so malformed or unexpected function-body shell is cannot-evaluate, not a pass.
EXPECTED_RUN_GATE_BODY = (
    'local name="$1"; shift',
    'echo "--- ${name} ---"',
    'if "$@"; then :; else',
    "local rc=$?",
    "failed=1",
    'failed_names="${failed_names:+${failed_names}, }${name}"',
    'echo "GATE FAILED: ${name} (exit ${rc})"',
    "fi",
    "echo",
)

# Recognized structural keys, so a genuinely unknown key at these levels is
# cannot-evaluate rather than silently ignored (honouring the fail-closed guarantee).
# Deeper nested non-gate YAML (under on:, env:, with:, strategy:) is not exhaustively
# schema-validated; the shadow scan still prevents any tools/* gate from hiding there.
TOP_LEVEL_KEYS = frozenset({"name", "on", "permissions", "jobs"})
JOB_PROPERTY_KEYS = frozenset({
    "name", "runs-on", "steps", "strategy", "needs", "env", "permissions",
    "if", "timeout-minutes", "continue-on-error", "defaults", "outputs",
    "concurrency", "container", "services", "uses", "with", "secrets",
    "environment",
})

# opf/ accepted toward OPF-SELF-CONTAIN. The path boundary is an ALLOWLIST anchor
# (correct-by-construction), not a denylist lookbehind: the match must be preceded by
# start-of-string or one delimiter character that legitimately precedes a tool reference
# in the scanned shell/YAML (whitespace, quotes, ( ) , : = backtick ; [ ] { }), with an
# optional non-consumed leading "./". Because the allowlist admits only real delimiters,
# a mid-path segment is closed BY CONSTRUCTION: a parent leaves either a word char or a
# "/" before the match (evil/tools/, a/opf/tools/, xopf/tools/), and a dotted parent
# leaves a word char or "/" before the "./" (evil./tools/, evil/./tools/) -- none is a
# delimiter, so all are rejected. Two earlier denylist-lookbehind forms each leaked a new
# mid-path edge (the dot before "/"); the allowlist has no such gap to patch.
# DISCLOSED RESIDUAL: by construction a genuine tools/ or opf/tools/ reference glued
# behind a NON-delimiter prefix -- including a shell expansion such as ${DIR}/tools/x.py
# or a quote-glued "$ROOT"/tools/x.py -- stays OUTSIDE this shadow-scan net, because it
# is textually indistinguishable from the mid-path over-match class above, so no anchor
# can admit it while still rejecting evil/tools/; the covering control is that an actual
# gate invocation through such a value still fails closed at extraction (an unresolved
# command value / unclassified line), never a silent clean pass.
_TOOL_NONDELIM = r"[^\s'\"(),:=`;{}\[\]]"  # a char that is NOT a legitimate delimiter
TOOL_RE = re.compile(
    r"(?:(?<!" + _TOOL_NONDELIM + r")|(?<=(?<!" + _TOOL_NONDELIM + r")\./))"
    r"(?:(?:opf/)?tools/[A-Za-z0-9_.-]+\.(?:py|sh)|"
    + "|".join(re.escape(gate) for gate in sorted(REPO_GATES)) + r")\b"
)
PY_TARGET_RE = re.compile(r"^(?:opf/)?tools/[A-Za-z0-9_.-]+\.py$")
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
YAML_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*.*$")

Result = namedtuple("Result", "ok value code message")
Diagnostic = namedtuple("Diagnostic", "source line code message")
Extraction = namedtuple(
    "Extraction", "members origins diagnostics findings", defaults=((),))
Finding = namedtuple("Finding", "kind identity message")
ActiveException = namedtuple(
    "ActiveException", "side identity reason backlog")
Report = namedtuple(
    "Report",
    "code local_members ci_members local_only ci_only active findings diagnostics",
)


def _strip_comment(line):
    """Remove an unquoted comment while retaining quoted text for the shadow scan."""
    out = []
    quote = None
    escaped = False
    for char in line:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            out.append(char)
            escaped = True
            continue
        if quote:
            out.append(char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            out.append(char)
            quote = char
            continue
        if char == "#" and (not out or out[-1] in " \t"):
            # A comment starts only at a word boundary (line start or after
            # whitespace); a mid-word # is a literal, as Bash and YAML treat it.
            break
        out.append(char)
    return "".join(out).rstrip()


def _tokenize(code):
    """Tokenize one supported shell command, preserving control operators."""
    try:
        lexer = shlex.shlex(
            code, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return Result(True, list(lexer), "", "")
    except ValueError as exc:
        return Result(
            False,
            None,
            "shell-syntax",
            "cannot tokenize command ({})".format(exc),
        )


def _raw_lexemes(code, tokens):
    """Recover each token's RAW spelling (quotes and escaping backslashes
    kept) from a command _tokenize already split, by walking the same shlex
    grammar (posix, whitespace_split, punctuation_chars "|&;<>"). The walk
    must reproduce the de-quoted token list byte-for-byte; where it cannot,
    the result is None and normalize grants no masked identity (a
    missing raw spelling fails closed), so a divergence between the two
    readers can refuse a mask but never grant one."""
    raws = []
    values = []
    raw = []
    value = []
    in_word = False
    kind = None
    quote = None
    escaped = False

    def emit():
        nonlocal in_word, kind
        if in_word:
            raws.append("".join(raw))
            values.append("".join(value))
            raw.clear()
            value.clear()
        in_word = False
        kind = None

    for char in code:
        if escaped:
            # Outside quotes a backslash escapes any character; inside
            # double quotes shlex (posix) unescapes only the quote and the
            # backslash itself and keeps the backslash ahead of anything
            # else.
            raw.append(char)
            if quote == '"' and char not in ('"', "\\"):
                value.append("\\")
            value.append(char)
            escaped = False
            continue
        if quote is not None:
            if char == "\\" and quote == '"':
                raw.append(char)
                escaped = True
                continue
            raw.append(char)
            if char == quote:
                quote = None
            else:
                value.append(char)
            continue
        if char == "\\":
            if kind == "punct":
                emit()
            raw.append(char)
            escaped = True
            in_word = True
            kind = "word"
            continue
        if char in ('"', "'"):
            if kind == "punct":
                emit()
            raw.append(char)
            quote = char
            in_word = True
            kind = "word"
            continue
        if char in " \t":
            emit()
            continue
        if char in "|&;<>":
            if kind == "word":
                emit()
            raw.append(char)
            value.append(char)
            in_word = True
            kind = "punct"
            continue
        if kind == "punct":
            emit()
        raw.append(char)
        value.append(char)
        in_word = True
        kind = "word"

    if quote is not None or escaped:
        return None
    emit()
    if values != list(tokens):
        return None
    return raws


def _operator(token):
    return bool(token) and all(char in "|&;<>()`" for char in token)


def _interpreter_flag(token):
    """Recognize only the disclosed, valueless interpreter flag set."""
    return bool(re.fullmatch(r"-[IBEsPu]+", token))


def _is_runtime_value(value):
    """A runtime-derived flag value is exactly one carrying $ or a backquote
    after tokenization. A tilde, glob or brace spelling carries neither, so it
    is NOT runtime-derived here: it stays in identity as a kept literal and two
    different literals diverge (bash would expand those spellings, which is why
    the local runner's word allowlist refuses them outright)."""
    return "$" in value or "`" in value


def _mask_value(value, masked_ok=False, raw=None):
    """Canonicalize a runtime-value-flag value as a Result. A runtime-derived value
    (one carrying $ or a backquote) is masked to <ref:expression>, preserving which
    expression supplied it so a change among runtime spellings (including a
    self-comparison) diverges; a literal stays in identity. The expression must be
    byte-for-byte one of MASKED_REF_EXPRESSIONS: bash can assign or abort inside an
    expansion, so an unreviewed expression is refused, never masked. The value's
    RAW workflow spelling, before shell de-quoting, must also be byte-for-byte
    the one reviewed spelling of that expression, its double-quoted form as
    quality.yml writes it. Tokenization removes quotes and escaping
    backslashes, so a backslash-escaped, backslash-split, adjacent-quoted
    ("$PUSH""_BEFORE") or single-quoted ('$PUSH_BEFORE') spelling de-quotes
    into a reviewed value while bash hands the gate different bytes at runtime;
    raw is that spelling (None where the caller recovered none, refused the same
    way), and every spelling other than the reviewed one is cannot-evaluate
    (masked-spelling), never masked. A <ref:...>
    placeholder is the OUTPUT of this masking, accepted back only on the canonical
    path (masked_ok=True: _validate_allowlist re-normalizing the reviewed in-gate
    allowlist identities), where a placeholder of a reviewed expression passes
    through unchanged so the form is idempotent there, which the allowlist
    canonical-identity check relies on; no raw spelling exists or is read on that
    path. A workflow value gets no placeholder
    reading: a <ref:...> spelling carrying $ or a backquote is
    screened like any other value and refused, because no whole placeholder is in
    MASKED_REF_EXPRESSIONS, so a quoted <ref:...> literal never collapses into the
    masked identity of the expression it names; one carrying neither character
    stays a kept literal, which no masked identity can equal (every reviewed
    expression carries $)."""
    if (masked_ok and value.startswith("<ref:") and value.endswith(">")
            and value[len("<ref:"):-1] in MASKED_REF_EXPRESSIONS):
        return Result(True, value, "", "")
    if _is_runtime_value(value):
        if value not in MASKED_REF_EXPRESSIONS:
            return Result(
                False,
                None,
                "masked-value",
                "runtime-derived flag value {!r} is outside the reviewed "
                "MASKED_REF_EXPRESSIONS allowlist".format(value),
            )
        if raw != '"' + value + '"':
            return Result(
                False,
                None,
                "masked-spelling",
                "flag value spelled {!r} de-quotes into the reviewed "
                "expression {!r} but is not its one reviewed raw spelling "
                "{!r}; quote and backslash removal happens before this "
                "screen, so only the byte-for-byte reviewed workflow "
                "spelling earns the masked identity".format(
                    raw, value, '"' + value + '"'),
            )
        return Result(True, "<ref:" + value + ">", "", "")
    return Result(True, value, "", "")


def normalize(tokens, masked_ok=False, raws=None):
    """Return a canonical gate identity or a cannot-evaluate Result. masked_ok=True
    is the allowlist-identity path, the one place a <ref:...> placeholder of a
    reviewed expression is accepted back (_mask_value). raws, where given, holds
    each token's raw spelling as _raw_lexemes recovered it, consulted only to
    grant a masked identity; with no raws a runtime-derived value
    in the reviewed set is refused (masked-spelling), never masked, so the
    grant fails closed."""
    tokens = list(tokens)
    if raws is not None and len(raws) != len(tokens):
        raws = None
    if not tokens:
        return Result(False, None, "empty-command", "gate command is empty")

    index = 0
    command = tokens[index]
    if command in INTERPRETERS:
        index += 1
        while index < len(tokens) and tokens[index].startswith("-"):
            if not _interpreter_flag(tokens[index]):
                return Result(
                    False,
                    None,
                    "interpreter-option",
                    "unsupported interpreter option {!r}".format(
                        tokens[index]),
                )
            index += 1
    elif command in SHELLS:
        index += 1

    if index >= len(tokens):
        return Result(
            False, None, "missing-target", "gate command has no target")

    target = tokens[index]
    index += 1
    if target.startswith("./"):
        target = target[2:]

    if target == "gitleaks":
        if index >= len(tokens) or tokens[index] != "dir":
            return Result(
                False,
                None,
                "gitleaks-shape",
                "gitleaks gate must use the dir subcommand",
            )
        canonical = ["gitleaks"]
    else:
        if "$" in target or "`" in target:
            return Result(
                False,
                None,
                "dynamic-target",
                "gate target must be a literal tools/*.py path",
            )
        if (target not in REPO_GATES and not PY_TARGET_RE.fullmatch(target)
                or ".." in target.split("/")):
            return Result(
                False,
                None,
                "gate-target",
                "unsupported gate target {!r}; expected a literal "
                "tools/*.py path, {} or gitleaks dir".format(
                    target, " or ".join(sorted(REPO_GATES))),
            )
        canonical = [target]

    args = tokens[index:]
    raw_args = raws[index:] if raws is not None else None
    index = 0
    while index < len(args):
        token = args[index]

        if token in RUNTIME_VALUE_FLAGS:
            if index + 1 >= len(args):
                return Result(
                    False,
                    None,
                    "runtime-value",
                    "{} has no value".format(token),
                )
            value = args[index + 1]
            raw_value = (raw_args[index + 1]
                         if raw_args is not None else None)
            masked = _mask_value(value, masked_ok, raw_value)
            if not masked.ok:
                return masked
            canonical.extend((token, masked.value))
            index += 2
            continue

        matched_flag = None
        for flag in sorted(RUNTIME_VALUE_FLAGS):
            if token.startswith(flag + "="):
                matched_flag = flag
                break
        if matched_flag is not None:
            if token == matched_flag + "=":
                return Result(
                    False,
                    None,
                    "runtime-value",
                    "{} has an empty value".format(matched_flag),
                )
            value = token[len(matched_flag) + 1:]
            raw_value = None
            if raw_args is not None:
                raw_token = raw_args[index]
                prefix = matched_flag + "="
                # The reviewed equals-form spelling writes the flag and =
                # unquoted; quoting that reaches into them cannot be the
                # reviewed spelling, so the whole raw token (which never
                # matches a double-quoted expression) is passed instead.
                raw_value = (raw_token[len(prefix):]
                             if raw_token.startswith(prefix) else raw_token)
            masked = _mask_value(value, masked_ok, raw_value)
            if not masked.ok:
                return masked
            canonical.extend((matched_flag, masked.value))
            index += 1
            continue

        if (_operator(token) or "$(" in token
                or "$" in token or "`" in token):
            return Result(
                False,
                None,
                "dynamic-or-piped",
                "unmasked variable, substitution, pipeline, or redirection "
                "in gate command at {!r}".format(token),
            )
        canonical.append(token)
        index += 1

    # A whitespace-bearing token would make the space-joined identity ambiguous
    # (a quoted "a b" arg collides with two args a b), so it is outside the subset.
    if any(any(ch.isspace() for ch in token) for token in canonical):
        return Result(
            False,
            None,
            "whitespace-arg",
            "a gate argument contains whitespace, outside the parity grammar",
        )

    return Result(True, " ".join(canonical), "", "")


def _add_member(members, origins, line_number, normalized):
    members.add(normalized)
    script = normalized.split(" ", 1)[0]
    origins.setdefault(line_number, set()).add(script)


def _diagnostic(source, line, code, message):
    return Diagnostic(source, line, code, message)


def _dedupe_diagnostics(diagnostics):
    return tuple(sorted(
        set(diagnostics),
        key=lambda diagnostic: (
            diagnostic.source,
            diagnostic.line,
            diagnostic.code,
            diagnostic.message,
        ),
    ))


def _shadow_check(text, source, origins):
    """Require every uncommented tools path to be extracted on that same line."""
    diagnostics = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        code = _strip_comment(raw)
        if not code.strip():
            continue
        expected = origins.get(line_number, set())
        for path in TOOL_RE.findall(code):
            if path == "tools/run_all_checks.sh":
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "circular-runner",
                    "invoking or sourcing tools/run_all_checks.sh makes "
                    "parity circular",
                ))
            elif path not in expected:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "shadow-miss",
                    "tools path {!r} was not extracted as a gate on this "
                    "line".format(path),
                ))
    return diagnostics


def _contains_unsafe_substitution(code):
    return "$(" in code or "`" in code


def _safe_simple(tokens):
    return not any(_operator(token) for token in tokens)


FAILURE_STATE_WORD_RE = re.compile(r"(?<![A-Za-z0-9_])failed(?:_names)?(?![A-Za-z0-9_])")

# ONE rule for every word of every run_gate line, applied to the shlex
# quote-removed tokens ahead of any other gate handling: a strict character
# ALLOWLIST with no shell metacharacters at all. Earlier rounds each blocked
# one expansion construct ($ and backquote last); bash also expands brace
# ({a,b}), glob (*, ?, [..]) and tilde (~) words that carry no $, so only a
# full-word allowlist is literal-only. The label alphabet is
# [a-z0-9][a-z0-9-]*, which every label in the two real runners already
# matches. The argument alphabet [A-Za-z0-9_./=:-]+ is derived from the
# complete word inventory of the two real runners' gate lines (letters,
# digits, _ . / and -) plus = and :, to which bash gives no expansion or
# globbing meaning inside an argument word; every brace, glob, tilde, dollar,
# backquote, quote, backslash, whitespace or other metacharacter spelling
# falls outside both alphabets and is refused statically.
GATE_LABEL_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
GATE_WORD_RE = re.compile(r"[A-Za-z0-9_./=:-]+")

# The only runner lines outside run_gate() that may assign failure state after the
# initializers; extract_local accepts each once, inside the gitleaks failure branch.
GITLEAKS_FAILURE_UPDATES = (
    "failed=1",
    'failed_names="${failed_names:+${failed_names}, }secrets (gitleaks)"',
)


# The only test lines the two real runners use outside run_gate(); every other
# [ ... ] test, bare or behind if, is outside the allowlist. This exact-shape set is
# what rejects a -v operand (whose subscript can assign), any NAME[...] subscript
# operand, and every unlisted operator or operand form.
# The HOME guard is the only block in which the real runner appends the
# user-local gitleaks directory to PATH; extract_local records its frame so
# the PATH append is accepted only in that guard's then branch, the one
# branch that has proven HOME non-empty.
HOME_GUARD_LINE = (
    'if ! command -v gitleaks >/dev/null 2>&1 && '
    '[ -n "${HOME:-}" ] && '
    '[ -x "$HOME/.local/bin/gitleaks" ]; then')

# The terminal failed check: its then branch is the one place the FAILED
# GATES echo is accepted, the way the gitleaks-block lines are bound to
# their real blocks. extract_local marks a frame as the failed check only
# when its header line sits in the recognized terminal summary tail, so a
# copied or nested frame spelling this same header does not host the echo.
FAILED_CHECK_LINE = 'if [ "$failed" -ne 0 ]; then'

RUNNER_TEST_LINES = frozenset({
    FAILED_CHECK_LINE,
    'if [ "$notrun" -ne 0 ]; then',
    HOME_GUARD_LINE,
    'if command -v gitleaks >/dev/null 2>&1; then',
})

# The gitleaks-block lines are position-checked the way notrun=0 and cd
# already are, never accepted by bare membership: each is accepted only
# inside its real block AND branch. Placed anywhere else, the PATH append
# outside the HOME guard's then branch and the exit-code echo without a
# prior gitleaks_rc capture each end the runner mid-roster under set -u
# through an unbound variable, and a gitleaks_rc=$? that is not the first
# statement of the gitleaks failure branch captures an unrelated or already
# replaced exit status.
PATH_APPEND_LINE = 'PATH="$PATH:$HOME/.local/bin"'
GITLEAKS_RC_LINE = "gitleaks_rc=$?"
GITLEAKS_FAILED_ECHO = (
    'echo "GATE FAILED: secrets (gitleaks) (exit ${gitleaks_rc})"')
# The FAILED GATES echo expands ${failed_names}, so it receives the same
# treatment: accepted only in the then branch of the TERMINAL failed check
# (the FAILED_CHECK_LINE frame whose header sits in the terminal summary
# tail), after the top-level failed_names initializer.
# Relocated above that initializer, ${failed_names} is unbound and set -u
# ends the runner at that line before the first gate.
FAILED_GATES_ECHO = 'echo "FAILED GATES: ${failed_names}"'

# The only non-gate, non-test scaffold lines the two real runners contain outside
# run_gate(), the failure-state initializers and updates, and the position-checked
# set/cd/notrun=0/exit shapes handled separately: enumerated from
# tools/run_all_checks.sh and from opf/tools/run_all_checks.sh as
# tools/selftest_git_fixture_env.py adapts it (its validated directory binding and
# terminal exit 0 are removed there). Acceptance is exact membership, never an
# assignment/export/echo pattern, so a spelling outside this finite set is
# unclassified: bash evaluates a value assigned to RANDOM, SRANDOM, OPTIND or
# HISTCMD as arithmetic (RANDOM=failed=0 resets failed), a compound array subscript
# assigns (x=([failed=0]=1)), and an expansion of an unset variable aborts under
# set -u (x=$BASH_ENV, echo "$stub_log"). notrun=0 is accepted only at top level
# before the first gate, because a later reset would hide the NOT RUN disclaimer.
# The gitleaks-block lines (PATH_APPEND_LINE, GITLEAKS_RC_LINE and
# GITLEAKS_FAILED_ECHO) and FAILED_GATES_ECHO are deliberately NOT members
# here: extract_local accepts each of them only inside its real block and
# branch.
RUNNER_SCAFFOLD_LINES = frozenset({
    # tools/run_all_checks.sh
    "export PYTHONDONTWRITEBYTECODE=1",
    "notrun=1",
    "echo",
    'echo "--- secrets (gitleaks) ---"',
    'echo "PASS: gitleaks found no leaks"',
    'echo "NOT RUN: gitleaks is not on PATH locally. CI still runs it, so this is a gap"',
    'echo "  in THIS run only, not in the pipeline. Install it to close the gap:"',
    'echo "  see the pinned version and checksum in .github/workflows/quality.yml"',
    'echo "RESULT: FAIL"',
    'echo "RESULT: PASS, but one or more gates did NOT RUN locally (see above)"',
    'echo "RESULT: PASS"',
    # opf/tools/run_all_checks.sh (adapted): its distinct summary echoes
    'echo "OPF STANDALONE SUBSET: FAILED"',
    'echo "OPF STANDALONE SUBSET: OK"',
})

# Exact executable suffixes that may contain exits. The standalone roster adapter
# validates and removes its final exit 0 and directory binding before extraction.
TERMINAL_SUMMARIES = (
    (
        'if [ "$failed" -ne 0 ]; then',
        'echo "FAILED GATES: ${failed_names}"',
        'echo "RESULT: FAIL"',
        "exit 1",
        "fi",
        'if [ "$notrun" -ne 0 ]; then',
        'echo "RESULT: PASS, but one or more gates did NOT RUN locally (see above)"',
        "exit 0",
        "fi",
        'echo "RESULT: PASS"',
    ),
    (
        'if [ "$failed" -ne 0 ]; then',
        'echo "FAILED GATES: ${failed_names}"',
        'echo "OPF STANDALONE SUBSET: FAILED"',
        "exit 1",
        "fi",
        'echo "OPF STANDALONE SUBSET: OK"',
    ),
)


def _expansion_spans(code, opener, open_char, close_char):
    """Yield each balanced span of code starting with opener; an unclosed one runs to the end."""
    start = code.find(opener)
    while start != -1:
        depth = 0
        end = len(code)
        for index in range(start + len(opener) - 1, len(code)):
            if code[index] == open_char:
                depth += 1
            elif code[index] == close_char:
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        yield code[start:end]
        start = code.find(opener, start + 1)


def _failure_state_expansion(code):
    """Return whether code may assign failure state through a bash expansion."""
    if "$[" in code:
        return True
    if any(FAILURE_STATE_WORD_RE.search(span)
           for span in _expansion_spans(code, "$((", "(", ")")):
        return True
    return any(
        ("[" in span or ":" in span) and FAILURE_STATE_WORD_RE.search(span)
        for span in _expansion_spans(code, "${", "{", "}")
    )


def _abort_expansion(code):
    """Return whether code carries a ${...:?...} abort expansion. When the named
    variable is unset (the disclosed harness-detection trigger), that expansion exits
    the runner mid-roster with a non-zero status, losing the remaining gates and the
    FAILED GATES summary; no line of either real runner uses one. This check runs
    before the run_gate branch, so it also refuses an abort carried inside a
    runtime-value flag, ahead of the gate-word character allowlist that also
    refuses every expansion spelling there, and ahead of the
    MASKED_REF_EXPRESSIONS screen that refuses every unreviewed expression on the
    CI path; a vector 26 fixture pins this rule's own diagnostic and fails
    without it."""
    return any(":?" in span for span in _expansion_spans(code, "${", "{", "}"))


# The only expansion forms a scaffold line may carry: a plain $NAME, ${NAME} or $?.
# Subscripts, offsets, case or :? operators, $(( )), $( ), $[ ] and backquotes are
# all outside it, because bash can assign or abort inside them. The NAME must also
# be one the runner itself sets or manages (name and rc are run_gate() locals,
# gitleaks_rc is set in the gitleaks failure branch, HOME and PATH feed the
# gitleaks lookup): under set -u, a plain expansion of any other, possibly unset,
# variable (x=$BASH_ENV, echo "$stub_log") ends the runner mid-roster with a
# non-zero status. Exact-line membership in RUNNER_SCAFFOLD_LINES already implies
# both properties; this screen is belt and braces behind it, so a future scaffold
# addition cannot widen the expansion surface unreviewed.
_RUNNER_SET_NAMES = frozenset({
    "failed", "notrun", "failed_names", "name", "rc", "gitleaks_rc",
    "HOME", "PATH", "?",
})
_PLAIN_EXPANSION_RE = re.compile(
    r"\$(?:(?P<special>\?)|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"|\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)\})")


def _runner_set_expansions(code):
    names = []

    def _collect(match):
        names.append(match.group("name") or match.group("braced") or "?")
        return ""

    remainder = _PLAIN_EXPANSION_RE.sub(_collect, code)
    return ("`" not in code and "$" not in remainder
            and all(name in _RUNNER_SET_NAMES for name in names))


# Python and bash disagree on text boundaries outside printable ASCII:
# str.splitlines() also breaks a line at \v, \f, \x1c, \x1d, \x1e, \x85,
# U+2028 and U+2029, and str.strip() also removes Unicode spaces such as
# U+00A0, while bash breaks lines only at \n and reads each of those bytes as
# an ordinary word character (and YAML parsers differ on \x85, U+2028 and
# U+2029 too). A character outside printable ASCII (0x20-0x7E) plus tab and
# newline therefore refuses the whole file, before any line is split or
# trusted; NUL and CR fall outside the alphabet as well. Both real runners
# and the live workflow are pure printable ASCII plus tab and newline.
_TEXT_FORMAT_RE = re.compile(r"[^\t\n\x20-\x7e]")


def _text_format_diagnostic(text, source):
    match = _TEXT_FORMAT_RE.search(text)
    if match is None:
        return None
    return _diagnostic(
        source,
        text.count("\n", 0, match.start()) + 1,
        "text-format",
        "character {!r} is outside printable ASCII (0x20-0x7E) plus tab and "
        "newline; Python and bash (and YAML parsers) disagree on line and "
        "word boundaries beyond that set, so the file is refused before any "
        "line is trusted".format(match.group()),
    )


# The standalone OPF runner is held to the same grammar as the local runner
# after ONE adaptation, shared by vector 26 and
# tools/selftest_git_fixture_env.py: the validated directory binding and the
# terminal exit 0 are removed and the "$here/ paths are rebased. The
# character screen runs FIRST, on the RAW text: splitlines() below breaks a
# line at the very characters the screen exists to refuse (\v, \f, \x1c,
# \x1d, \x1e, \x85, U+2028, U+2029), and the newline rejoin would erase
# them, so screening only the adapted text would validate lines bash never
# runs.
STANDALONE_SOURCE = "opf/tools/run_all_checks.sh"
STANDALONE_BINDING = 'here="$(cd "$(dirname "$0")" && pwd)" || exit 2'


# ONE byte-level reader for every runner or workflow file this module or
# tools/selftest_git_fixture_env.py reads. Decoding before screening lost
# forbidden characters three separate times, one Python-side layer at a
# time (splitlines(), the adapter's split-and-rejoin, and
# Path.read_text()'s universal-newline translation, which turns \r into \n
# before any text screen can see it), so the screen runs on the RAW BYTES:
# any byte outside printable ASCII (0x20-0x7E) plus tab and newline
# refuses the file before any decode, and the accepted bytes are decoded
# strictly as ASCII, which maps each byte to one character and translates
# no newline. The mirrored text-level screen (_TEXT_FORMAT_RE) stays for
# text that arrives already decoded (fixtures, the adapter).
_BYTE_FORMAT_RE = re.compile(rb"[^\t\n\x20-\x7e]")


def read_runner_text(path, source, reader=None):
    """Read a runner or workflow file as RAW BYTES, refuse any byte outside
    printable ASCII (0x20-0x7E) plus tab and newline, and decode the
    accepted bytes strictly as ASCII with no newline translation. Returns
    (text, diagnostic); exactly one of the two is None."""
    if reader is None:
        reader = Path.read_bytes
    try:
        data = reader(path)
    except OSError as exc:
        return None, _diagnostic(
            source,
            0,
            "read-error",
            "cannot read {} ({})".format(path, type(exc).__name__),
        )
    if not isinstance(data, bytes):
        return None, _diagnostic(
            source,
            0,
            "read-type",
            "reader for {} did not return bytes".format(path),
        )
    match = _BYTE_FORMAT_RE.search(data)
    if match is not None:
        return None, _diagnostic(
            source,
            data.count(b"\n", 0, match.start()) + 1,
            "text-format",
            "byte {!r} is outside printable ASCII (0x20-0x7E) plus tab and "
            "newline; the raw bytes are screened before any decode, so no "
            "newline translation or multi-byte spelling can erase a "
            "forbidden byte ahead of the screen".format(match.group()),
        )
    return data.decode("ascii"), None


def adapt_standalone_runner(text):
    """Return (adapted_text, diagnostic) for the standalone OPF runner;
    exactly one of the two is None. The raw text is screened against the
    character allowlist before any splitlines()."""
    format_diagnostic = _text_format_diagnostic(text, STANDALONE_SOURCE)
    if format_diagnostic is not None:
        return None, format_diagnostic
    lines = text.splitlines()
    if lines.count(STANDALONE_BINDING) != 1 or lines[-1:] != ["exit 0"]:
        return None, _diagnostic(
            STANDALONE_SOURCE,
            0,
            "standalone-scaffold",
            "unsupported standalone runner scaffold: expected exactly one "
            "directory-binding line and a terminal exit 0",
        )
    lines[lines.index(STANDALONE_BINDING)] = (
        "# validated standalone directory binding")
    lines[-1] = "# validated terminal exit"
    return "\n".join(lines).replace('"$here/', '"opf/tools/'), None


def extract_local(text):
    """Extract normalized members from tools/run_all_checks.sh."""
    source = LOCAL_SOURCE
    diagnostics = []
    members = set()
    origins = {}

    if not isinstance(text, str):
        return Extraction(
            frozenset(),
            {},
            (_diagnostic(
                source, 0, "input-type", "input is not text"),),
        )
    format_diagnostic = _text_format_diagnostic(text, source)
    if format_diagnostic is not None:
        return Extraction(frozenset(), {}, (format_diagnostic,))

    code_lines = [(number, _strip_comment(raw).strip())
                  for number, raw in enumerate(text.splitlines(), 1)]
    executable = [(number, code) for number, code in code_lines if code]
    terminal_exits = set()
    terminal_summary_lines = set()
    for summary in TERMINAL_SUMMARIES:
        tail = executable[-len(summary):]
        if tuple(code for number, code in tail) == summary:
            terminal_exits.update(number for number, code in tail
                                  if code.startswith("exit "))
            # The line numbers of the TERMINAL summary tail: the one
            # failed-check frame that may host the FAILED GATES echo is
            # identified by its header sitting here, so a copied or nested
            # frame spelling the same header elsewhere never qualifies.
            terminal_summary_lines.update(number for number, code in tail)

    in_function = False
    function_defined = False
    function_body = []
    if_stack = []
    if_unbalanced = False
    failure_initializers = {
        "failed": "failed=0",
        "failed_names": 'failed_names=""',
    }
    initialized = set()
    gitleaks_updates = set()
    for line_number, raw in enumerate(text.splitlines(), 1):
        if line_number == 1 and raw == "#!/usr/bin/env bash":
            continue

        code = _strip_comment(raw)
        stripped = code.strip()
        if not stripped:
            continue

        if in_function:
            if stripped == "}":
                in_function = False
                if tuple(function_body) != EXPECTED_RUN_GATE_BODY:
                    diagnostics.append(_diagnostic(
                        source,
                        line_number,
                        "run-gate-body",
                        "run_gate() body is outside the recognized dispatcher "
                        "shape; refusing to skip unvalidated function content",
                    ))
                function_body = []
            else:
                function_body.append(stripped)
            continue
        if stripped == "run_gate() {":
            if if_stack:
                # bash runs a function definition as one ordinary,
                # successfully-exiting statement, so a definition inside a
                # branch both fills the branch and replaces $?; skipping it
                # uncounted formerly let a copied definition ahead of
                # gitleaks_rc=$? stale that capture, and made a
                # definition-only branch read as empty. The definition is
                # accepted only at top level outside every if frame, where
                # the real runners put it.
                frame = if_stack[-1]
                frame["else_count" if frame["in_else"] else "then_count"] += 1
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "function-position",
                    "run_gate() function definition inside an if branch; a "
                    "definition is accepted only at top level, because bash "
                    "runs it as an ordinary successful statement, filling "
                    "the branch and replacing $?",
                ))
            else:
                # Only a top-level definition satisfies the
                # definition-before-first-invocation check below; an
                # in-branch definition is already refused above.
                function_defined = True
            in_function = True
            function_body = []
            continue

        # Track if/else/fi branch structure for failure-state updates, the
        # branch-position-checked scaffold lines and the top-level summary
        # blocks. Each frame counts the statements of its open branch, so a
        # duplicate else, an empty then or else branch (both of which bash
        # refuses outright) and a position-checked line in the wrong branch
        # are refused with their own diagnostics. A fi with no open if
        # underflows: record it rather than clamp it away.
        if stripped == "fi":
            if not if_stack:
                if_unbalanced = True
            else:
                frame = if_stack.pop()
                branch = "else" if frame["in_else"] else "then"
                if frame[branch + "_count"] == 0:
                    diagnostics.append(_diagnostic(
                        source,
                        line_number,
                        "branch-structure",
                        "empty {} branch at fi; bash refuses an if block "
                        "with an empty branch, so this is not a shell file "
                        "bash would run".format(branch),
                    ))
        elif stripped.endswith("; then"):
            if if_stack:
                # The whole if block is one statement of the enclosing branch.
                frame = if_stack[-1]
                frame["else_count" if frame["in_else"] else "then_count"] += 1
            if_stack.append({
                "in_else": False,
                "then_count": 0,
                "else_count": 0,
                "home_guard": stripped == HOME_GUARD_LINE,
                "failed_check": (stripped == FAILED_CHECK_LINE
                                 and line_number in terminal_summary_lines),
                "gitleaks": False,
                "gitleaks_rc_set": False,
            })
        elif stripped == "else":
            if if_stack:
                frame = if_stack[-1]
                if frame["in_else"]:
                    diagnostics.append(_diagnostic(
                        source,
                        line_number,
                        "branch-structure",
                        "duplicate else in one if block; bash refuses it, "
                        "so this is not a shell file bash would run",
                    ))
                    continue
                if frame["then_count"] == 0:
                    diagnostics.append(_diagnostic(
                        source,
                        line_number,
                        "branch-structure",
                        "else after an empty then branch; bash refuses an "
                        "if block with an empty branch, so this is not a "
                        "shell file bash would run",
                    ))
                frame["in_else"] = True
        elif if_stack:
            # Any other line is one statement of the innermost open branch;
            # the count also decides gitleaks_rc=$? first-statement placement.
            frame = if_stack[-1]
            frame["else_count" if frame["in_else"] else "then_count"] += 1

        if stripped.endswith("\\"):
            diagnostics.append(_diagnostic(
                source,
                line_number,
                "line-continuation",
                "shell line continuations are outside the supported grammar",
            ))
            continue

        tokenized = _tokenize(stripped)
        if not tokenized.ok:
            diagnostics.append(_diagnostic(
                source,
                line_number,
                tokenized.code,
                tokenized.message,
            ))
            continue
        tokens = tokenized.value

        # bash assigns inside $[ ], $(( )), a ${ } subscript, and a ${ } offset or
        # length, so a line other than a recognized update must not name failure
        # state there, and $[ ] is refused outright. Check quote-removed tokens too:
        # bash removes quotes in arithmetic subscripts and offsets (fai""led=0).
        if (stripped not in GITLEAKS_FAILURE_UPDATES
                and (_failure_state_expansion(stripped)
                     or _failure_state_expansion(" ".join(tokens)))):
            diagnostics.append(_diagnostic(
                source,
                line_number,
                "failure-state-assignment",
                "{!r} may assign failure state through an expansion".format(stripped),
            ))
            continue

        # A ${...:?...} abort expansion is refused on ANY runner line, checked on the
        # quote-removed tokens too; see _abort_expansion. The gitleaks failure updates
        # carry :+ only, so the exemption above never reaches here.
        if _abort_expansion(stripped) or _abort_expansion(" ".join(tokens)):
            diagnostics.append(_diagnostic(
                source,
                line_number,
                "unclassified-line",
                "{!r} carries a ${{...:?...}} abort expansion, outside the "
                "disclosed parity grammar".format(stripped),
            ))
            continue

        if tokens and tokens[0] == "run_gate":
            if not function_defined:
                # bash resolves a function name at invocation time, so a
                # gate line ahead of the (or with no) top-level run_gate()
                # definition calls an undefined name: bash reports the
                # missing command and continues without recording the gate
                # as run or failed, while the roster still reads complete.
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "function-order",
                    "run_gate invoked before its top-level definition; "
                    "bash resolves a function name at invocation time, so "
                    "this gate line calls an undefined name and the runner "
                    "continues without recording the gate",
                ))
            if len(tokens) < 3:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "run-gate-shape",
                    "run_gate needs a label and command",
                ))
                continue
            # ONE rule covers every word of the line, on the quote-removed
            # tokens: a strict character allowlist with no shell
            # metacharacters (GATE_LABEL_RE for the label, GATE_WORD_RE for
            # every other word). Bash expands the line before run_gate runs,
            # and brace ({a,b}), glob (*, ?, [..]) and tilde words expand
            # with no $ in them: an expanded label shifts "$@" and runs a
            # different command, and an expanded argument changes what the
            # gate receives, so any word outside its alphabet is refused.
            if not GATE_LABEL_RE.fullmatch(tokens[1]):
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "run-gate-label",
                    "run_gate label {!r} does not fully match "
                    "[a-z0-9][a-z0-9-]*, the only label alphabet the real "
                    "runners contain; a label outside it can expand before "
                    "run_gate runs".format(tokens[1]),
                ))
                continue
            unsafe = [word for word in tokens[2:]
                      if not GATE_WORD_RE.fullmatch(word)]
            if unsafe:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "run-gate-word",
                    "run_gate word {!r} does not fully match "
                    "[A-Za-z0-9_./=:-]+; a gate word must carry no shell "
                    "metacharacter, expansion, quote or whitespace".format(
                        unsafe[0]),
                ))
                continue
            normalized = normalize(tokens[2:])
            if normalized.ok:
                _add_member(
                    members, origins, line_number, normalized.value)
            else:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    normalized.code,
                    normalized.message,
                ))
            continue

        if (len(tokens) >= 4
                and tokens[0] == "if"
                and tokens[-2:] == [";", "then"]
                and tokens[1].lstrip("./") == "gitleaks"):
            if stripped.endswith("; then"):
                if_stack[-1]["gitleaks"] = True
            unsafe = [word for word in tokens[1:-2]
                      if not GATE_WORD_RE.fullmatch(word)]
            if unsafe:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "run-gate-word",
                    "gitleaks gate word {!r} does not fully match "
                    "[A-Za-z0-9_./=:-]+; a gate word must carry no shell "
                    "metacharacter, expansion, quote or whitespace".format(
                        unsafe[0]),
                ))
                continue
            normalized = normalize(tokens[1:-2])
            if normalized.ok:
                _add_member(
                    members, origins, line_number, normalized.value)
            else:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    normalized.code,
                    normalized.message,
                ))
            continue

        # Failure state may be initialized once at top level before any gate, then
        # assigned here only in the gitleaks failure branch (the dispatcher body is
        # validated whole). These rules are lexical and shape-based: together with the
        # exact-line scaffold membership below they reject direct assignments, every
        # assignment, export or echo spelling the real runners do not contain, every
        # non-plain or unlisted-name expansion, every run_gate word outside the
        # gate-line character allowlist (every expanded label, every brace, glob or
        # tilde word, and every runtime-derived flag value, reviewed or not), and
        # every unlisted test shape, not a
        # reset smuggled through content they accept (a gate script, the inherited
        # environment). The
        # self-test exercises the declared scenarios; harness detection and untested
        # environments or failure combinations remain outside its runtime coverage.
        assignments = tokens[1:] if tokens[:1] == ["export"] else tokens
        variable = assignments[0].split("=", 1)[0] if assignments else ""
        if variable in failure_initializers and "=" in assignments[0]:
            initial = (
                not if_stack and not members and variable not in initialized
                and stripped == failure_initializers[variable]
            )
            gitleaks_update = (
                bool(if_stack) and if_stack[-1]["gitleaks"]
                and if_stack[-1]["in_else"]
                and variable not in gitleaks_updates
                and stripped in GITLEAKS_FAILURE_UPDATES
            )
            if initial:
                initialized.add(variable)
            elif gitleaks_update:
                gitleaks_updates.add(variable)
            else:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "failure-state-assignment",
                    "unexpected assignment to {}; failure state must not be reset "
                    "or overwritten".format(variable),
                ))
            continue

        # ALLOWLIST scaffold: a non-gate line must BE one of the exact lines the two
        # real runners contain (RUNNER_SCAFFOLD_LINES plus fi, a position-checked
        # else, and the position-checked set, cd, notrun=0 and terminal-exit
        # shapes), never
        # merely match an assignment, export or echo pattern. Anything else,
        # including any [ ] test outside RUNNER_TEST_LINES (bare or behind if), an
        # assignment such as RANDOM=failed=0 or x=([failed=0]=1) whose value bash
        # would evaluate, and an expansion of a name the runner does not set
        # (x=$BASH_ENV), is unclassified rather than accepted.
        scaffold = False
        if stripped in ("set -uo pipefail", "set -euo pipefail"):
            # Only where the real runners put it: top level, before any gate, where
            # a late -e cannot silently end a partially failed roster.
            scaffold = not if_stack and not members
        elif stripped == 'cd "$(dirname "$0")/.." || exit 2':
            # Only where the real runner puts it: top level, before any gate. A
            # second binding mid-roster would move the remaining gates to the
            # parent of the repository root when $0 is relative.
            scaffold = not if_stack and not members
        elif stripped == "notrun=0":
            # Only where the real runner puts it: top level, before any gate. A
            # later notrun=0 would clear the NOT RUN state and hide its disclaimer.
            scaffold = not if_stack and not members
        elif stripped == PATH_APPEND_LINE:
            # Only where the real runner puts it: the HOME guard's then branch,
            # which has just proven HOME non-empty. The guard's else branch
            # runs exactly when that proof failed, and outside the guard $HOME
            # may be unset; in both places set -u ends the runner mid-roster.
            in_home_guard = bool(if_stack) and if_stack[-1]["home_guard"]
            if in_home_guard and if_stack[-1]["in_else"]:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "branch-structure",
                    "the PATH append sits in the HOME guard's else branch, "
                    "which runs only when the guard has NOT proven HOME "
                    "non-empty; under set -u an unbound HOME there ends "
                    "the runner mid-roster",
                ))
                continue
            scaffold = in_home_guard
        elif stripped == GITLEAKS_RC_LINE:
            # Only where the real runner puts it: the first statement of the
            # gitleaks failure branch, where $? is still the gitleaks exit
            # code. Any earlier statement in that branch replaces $?, and any
            # other placement captures an unrelated status; either way the
            # line vouches for a variable the exit-code echo below then
            # trusts. This line was already counted into its branch above,
            # so first-statement means a count of exactly one.
            in_failure = (bool(if_stack) and if_stack[-1]["gitleaks"]
                          and if_stack[-1]["in_else"])
            if in_failure and if_stack[-1]["else_count"] != 1:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "branch-structure",
                    "gitleaks_rc=$? is not the first statement of the "
                    "gitleaks failure branch, so $? is no longer the "
                    "gitleaks exit code",
                ))
                continue
            scaffold = in_failure
            if scaffold:
                if_stack[-1]["gitleaks_rc_set"] = True
        elif stripped == GITLEAKS_FAILED_ECHO:
            # Only after gitleaks_rc is captured in the same failure branch.
            # Anywhere else ${gitleaks_rc} is unbound, and set -u then ends
            # the runner mid-roster, losing the remaining gates and the
            # FAILED GATES list.
            scaffold = (bool(if_stack) and if_stack[-1]["gitleaks"]
                        and if_stack[-1]["in_else"]
                        and if_stack[-1]["gitleaks_rc_set"])
        elif stripped == FAILED_GATES_ECHO:
            # Only where the real runners put it: the then branch of the
            # TERMINAL failed check, the frame whose header sits in the
            # terminal summary tail, after the top-level failed_names
            # initializer. A copied or nested frame spelling the same
            # header is never the terminal frame, so its echo stays
            # unclassified. Anywhere earlier,
            # ${failed_names} can be unbound, and set -u then ends the
            # runner at this line, losing the whole roster.
            scaffold = (bool(if_stack) and if_stack[-1]["failed_check"]
                        and not if_stack[-1]["in_else"]
                        and "failed_names" in initialized)
        elif stripped in RUNNER_SCAFFOLD_LINES:
            # Exact membership decides; the named-expansion screen is belt and
            # braces so a future scaffold addition cannot widen the expansion
            # surface unreviewed.
            scaffold = (_runner_set_expansions(stripped)
                        and _runner_set_expansions(" ".join(tokens)))
        elif stripped == "fi":
            # A top-level stray fi is recorded as an underflow above and refused
            # through the unbalanced-if diagnostic.
            scaffold = True
        elif stripped == "else":
            # Only inside an open if block, where the real runners put it; a
            # duplicate else in the same block is refused above. A bare then
            # or done is not a line either real runner contains, so neither
            # is accepted anywhere.
            scaffold = bool(if_stack)
        elif line_number in terminal_exits and len(if_stack) == 1:
            scaffold = True
        elif stripped in RUNNER_TEST_LINES:
            scaffold = True

        if not scaffold:
            diagnostics.append(_diagnostic(
                source,
                line_number,
                "unclassified-line",
                "shell line is outside the disclosed parity grammar: "
                "{!r}".format(stripped),
            ))

    if in_function:
        diagnostics.append(_diagnostic(
            source,
            0,
            "function-span",
            "run_gate function is not closed",
        ))

    if if_stack or if_unbalanced:
        diagnostics.append(_diagnostic(
            source,
            0,
            "unbalanced-if",
            "unbalanced if/fi nesting; the shell file is not well-formed",
        ))

    for variable in sorted(set(failure_initializers) - initialized):
        diagnostics.append(_diagnostic(
            source,
            0,
            "failure-state-assignment",
            "missing initial {}".format(failure_initializers[variable]),
        ))

    diagnostics.extend(_shadow_check(text, source, origins))
    return Extraction(
        frozenset(members),
        origins,
        _dedupe_diagnostics(diagnostics),
    )


def _yaml_feature_error(code):
    if code in ("---", "..."):
        return "multi-document YAML is outside the supported subset"
    if (re.search(
            r"(?:^|[\s:\-])(?:&|\*)[A-Za-z_][A-Za-z0-9_-]*",
            code)
            or "<<:" in code):
        return (
            "YAML anchors, aliases, and merge keys are outside the "
            "supported subset"
        )
    if re.search(r":\s*![A-Za-z_]", code):
        return "YAML tags are outside the supported subset"
    return None


def _classify_ci_command(
        command,
        source,
        line_number,
        block,
        members,
        origins,
        diagnostics):
    stripped = command.strip()
    if not stripped or stripped.startswith("#"):
        return

    if "${{" in stripped:
        diagnostics.append(_diagnostic(
            source,
            line_number,
            "workflow-expression",
            "a GitHub expression inside run: is not statically evaluable",
        ))
        return

    if "tools/run_all_checks.sh" in stripped:
        diagnostics.append(_diagnostic(
            source,
            line_number,
            "circular-runner",
            "invoking or sourcing tools/run_all_checks.sh makes parity "
            "circular",
        ))
        return

    tokenized = _tokenize(stripped)
    if not tokenized.ok:
        diagnostics.append(_diagnostic(
            source,
            line_number,
            tokenized.code,
            tokenized.message,
        ))
        return
    tokens = tokenized.value

    if (tokens
            and (tokens[0] in INTERPRETERS
                 or tokens[0] in SHELLS
                 or tokens[0].lstrip("./") == "gitleaks")):
        normalized = normalize(
            tokens, raws=_raw_lexemes(stripped, tokens))
        if normalized.ok:
            _add_member(
                members, origins, line_number, normalized.value)
        else:
            diagnostics.append(_diagnostic(
                source,
                line_number,
                normalized.code,
                normalized.message,
            ))
        return

    benign = False
    if (not block
            and stripped
            == "git config --global core.autocrlf true"):
        benign = True
    elif block and stripped == "set -euo pipefail":
        benign = True
    elif (block
            and len(tokens) == 1
            and ASSIGN_RE.fullmatch(tokens[0])
            and not _contains_unsafe_substitution(stripped)):
        benign = True
    elif (block
            and tokens
            and tokens[0] in ("curl", "tar")
            and _safe_simple(tokens)):
        benign = True
    elif (block
            and re.fullmatch(
                r"echo .+ \| sha256sum -c -", stripped)):
        benign = True
    elif (block
            and tokens
            and tokens[0] == "echo"
            and _safe_simple(tokens)):
        benign = True
    elif (block
            and tokens
            and tokens[0] == "if"
            and len(tokens) >= 4
            and tokens[1] == "["
            and tokens[-2:] == [";", "then"]
            and _safe_simple(tokens[:-2])):
        benign = not _contains_unsafe_substitution(stripped)
    elif (block
            and stripped
            == 'elif git rev-parse --verify --quiet "HEAD^" '
               '>/dev/null; then'):
        benign = True
    elif block and stripped in ("else", "fi"):
        benign = True
    elif block and stripped == "exit 1":
        # Only a failing exit: it can end a step red, never green.
        benign = True
    elif (block
            and tokens[:2] == ["git", "rev-parse"]
            and _safe_simple(tokens)):
        benign = True

    if not benign:
        diagnostics.append(_diagnostic(
            source,
            line_number,
            "unclassified-command",
            "workflow command is outside the disclosed parity grammar: "
            "{!r}".format(stripped),
        ))


def _classify_uses(value, source, line_number, diagnostics):
    value = value.strip()
    if "${{" in value:
        diagnostics.append(_diagnostic(
            source,
            line_number,
            "dynamic-uses",
            "uses: value is dynamic",
        ))
    elif not re.fullmatch(
            r"actions/(?:checkout|setup-python)@[^\s]+", value):
        diagnostics.append(_diagnostic(
            source,
            line_number,
            "unknown-uses",
            "uses: {!r} may hide a gate and is not an allowed "
            "infrastructure action".format(value),
        ))


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _needs_ids(job, fields, source, diagnostics):
    """Return the job ids a job's needs: names, in order, or None when absent."""
    entry = fields.get("needs")
    if entry is None:
        return None
    line_number, value = entry
    items = fields.get("needs-items", [])
    raw = []
    if value:
        if items:
            diagnostics.append(_diagnostic(
                source,
                items[0][0],
                "needs-shape",
                "job {!r} needs: has both a value and list items".format(job),
            ))
        if value.startswith("[") and value.endswith("]"):
            raw = [(line_number, part) for part in value[1:-1].split(",")]
        else:
            raw = [(line_number, value)]
    else:
        for item_number, item in items:
            if not item.startswith("- "):
                diagnostics.append(_diagnostic(
                    source,
                    item_number,
                    "needs-shape",
                    "job {!r} needs: line {!r} is not a list item".format(
                        job, item),
                ))
                continue
            raw.append((item_number, item[2:]))
    ids = []
    for item_number, part in raw:
        name = _unquote(part.strip())
        if not JOB_ID_RE.fullmatch(name):
            diagnostics.append(_diagnostic(
                source,
                item_number,
                "needs-shape",
                "job {!r} needs: entry {!r} is not a plain job id".format(
                    job, part.strip()),
            ))
            continue
        ids.append(name)
    return ids


def _result_masking_findings(job_ids, job_fields):
    """Return a finding for every RESULT_MASKING_JOB_KEYS key on any job."""
    findings = []
    for job in job_ids:
        for line_number, key in job_fields[job].get("masking", ()):
            findings.append(Finding(
                "required-status",
                job,
                "job {!r} carries {}: at line {}; {}, so a failed gate can "
                "leave the required status green".format(
                    job, key, line_number, RESULT_MASKING_JOB_KEYS[key]),
            ))
    return findings


def _step_shell_findings(steps):
    """Return a finding for every step with a run: key and no STEP_SHELL_LINE;
    the message names the workflow holding the step (module docstring, Step
    shell)."""
    findings = []
    for step in steps:
        if step["run"] and not step["shell"]:
            findings.append(Finding(
                "required-status",
                step["job"],
                "step {!r} at line {} in job {!r} of {} has a run: key "
                "without {}; a workflow or job defaults.run.shell would then "
                "choose the program its command runs under, so the gate need "
                "not run or fail its step".format(
                    step["name"], step["line"], step["job"], step["source"],
                    STEP_SHELL_LINE),
            ))
    return findings


def _aggregator_shape_findings(aggregator, lines, start):
    """Compare the job whose id is on lines[start] with REQUIRED_STATUS_JOB.

    Blank and whole-line comment lines are skipped, and a needs: key at the job
    property indent, with every more-indented line under it, reads as
    REQUIRED_STATUS_NEEDS_SLOT. Any other line is compared exactly."""
    body = []
    in_needs = False
    for index in range(start + 1, len(lines)):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent <= 2:
            break
        if in_needs and indent > 4:
            continue
        in_needs = indent == 4 and stripped.startswith("needs:")
        body.append(
            (index + 1, REQUIRED_STATUS_NEEDS_SLOT if in_needs else raw))
    for position in range(max(len(body), len(REQUIRED_STATUS_JOB))):
        got = body[position] if position < len(body) else None
        want = (REQUIRED_STATUS_JOB[position]
                if position < len(REQUIRED_STATUS_JOB) else None)
        if got is None or got[1] != want:
            return [Finding(
                "required-status",
                aggregator,
                "job {!r} departs from the pinned Quality gates job "
                "(REQUIRED_STATUS_JOB) at {}: got {!r}, expected {!r}; only "
                "the pinned step is known to fail unless every needed "
                "result is success".format(
                    aggregator,
                    "the end of the job" if got is None
                    else "line {}".format(got[0]),
                    None if got is None else got[1].strip(),
                    None if want is None else want.strip()),
            )]
    return []


def _required_status_findings(
        job_ids, job_fields, lines, source, diagnostics):
    """Flag a result-masking key on any job; in a multi-job workflow, also
    require one job named REQUIRED_STATUS_NAME, shaped exactly as
    REQUIRED_STATUS_JOB, whose needs: lists every other job exactly once
    (module docstring, REQUIRED STATUS)."""
    findings = _result_masking_findings(job_ids, job_fields)
    if len(job_ids) < 2:
        return findings
    named = [
        job for job in job_ids
        if _unquote(job_fields[job].get("name", (0, ""))[1])
        == REQUIRED_STATUS_NAME
    ]
    if len(named) != 1:
        findings.append(Finding(
            "required-status",
            REQUIRED_STATUS_NAME,
            "a workflow with more than one job needs exactly one job named "
            "{!r}, the one required status; found {}".format(
                REQUIRED_STATUS_NAME, len(named)),
        ))
        return findings
    aggregator = named[0]
    if job_fields[aggregator].get("if", (0, ""))[1] != "always()":
        findings.append(Finding(
            "required-status",
            aggregator,
            "job {!r} must carry if: always(); otherwise a failed or "
            "cancelled needed job skips it, and GitHub reads a skipped "
            "required job as passing".format(aggregator),
        ))
    findings.extend(_aggregator_shape_findings(
        aggregator, lines, job_fields[aggregator]["start"]))
    needs = _needs_ids(aggregator, job_fields[aggregator], source, diagnostics)
    needs = [] if needs is None else needs
    others = [job for job in job_ids if job != aggregator]
    for job in others:
        if job not in needs:
            findings.append(Finding(
                "required-status",
                job,
                "job {!r} is not in the needs: of {!r}, so its failure "
                "cannot turn the required status red".format(job, aggregator),
            ))
    for job in sorted(set(needs)):
        if job not in others:
            findings.append(Finding(
                "required-status",
                job,
                "the needs: of {!r} names {!r}, which is not another job in "
                "this workflow".format(aggregator, job),
            ))
        if needs.count(job) > 1:
            findings.append(Finding(
                "required-status",
                job,
                "the needs: of {!r} lists {!r} more than once".format(
                    aggregator, job),
            ))
    return findings


def _edit_event_types(lines, source, diagnostics):
    """Return {event: types} for each EDIT_EVENTS trigger of the top-level on:.

    The shapes read are those the module docstring lists (TRIGGER); any
    other shape under an edit event appends a trigger-shape diagnostic."""
    events = {}
    typed = set()

    def refuse(line_number, message):
        diagnostics.append(_diagnostic(
            source, line_number, "trigger-shape", message))

    def names(line_number, value):
        if value.startswith("[") and value.endswith("]"):
            parts = [_unquote(part.strip()) for part in value[1:-1].split(",")]
        else:
            parts = [_unquote(value)]
        if not all(JOB_ID_RE.fullmatch(part) for part in parts):
            refuse(line_number, "{!r} is not a plain name or a one-line list "
                   "of plain names".format(value))
            return ()
        return tuple(parts)

    def is_on_spelling(key):
        # The spellings THIS HELPER recognizes as naming the top-level on
        # key: plain, quoted with plain quotes around the two literal
        # characters, a one-line tag such as !!str before it, a one-line
        # explicit '?' key, or one with whitespace around it. Each
        # non-plain spelling among these is refused below, never read.
        # Spellings YAML also reads as this key but that this
        # helper does NOT recognize fall through here (a double-quoted
        # escape spelling such as "o\x6e", an anchored key such as &a on,
        # a verbatim-tag form, a multi-line explicit '?' key, and a BOM
        # before on); the strict top-level parser refuses each of them
        # (unknown-top-key, yaml-feature or text-format, exit 2), never
        # reads them. The 35v vectors pin each end to end.
        candidate = key.strip()
        if candidate[:1] == "?" and candidate[1:2] in ("", " "):
            candidate = candidate[1:].strip()
        if candidate[:1] == "!":
            candidate = candidate.partition(" ")[2].strip()
        return _unquote(candidate) == "on"

    start = None
    for index, raw in enumerate(lines):
        code = _strip_comment(raw)
        if code[:1] in ("", " "):
            continue
        stripped = code.strip()
        key, colon, raw_rest = stripped.partition(":")
        if not is_on_spelling(key):
            continue
        # The top-level on: header is held to the same strict key
        # rule as every key below it, BEFORE its value is read: a bare on (no
        # colon anywhere on the line), a non-plain spelling, and a colon with
        # no space after it each hide or change the trigger block YAML and
        # GitHub read, so each is cannot-evaluate, never a pass and never an
        # IndexError.
        if not colon:
            refuse(index + 1, "top-level line {!r} names on without a colon "
                   "(a bare scalar or an explicit '?' key), so this rule "
                   "cannot read the on: block".format(stripped))
            return events
        if key != "on":
            refuse(index + 1, "top-level on: key {!r} is not the plain "
                   "spelling on (a quoted key, a tag such as !!str, an "
                   "explicit '?' key, and whitespace before the colon are "
                   "refused)".format(key))
            return events
        if raw_rest and not raw_rest.startswith(" "):
            refuse(index + 1, "on: has no space after its colon; YAML reads "
                   "the line as one plain scalar, not a key")
            return events
        start = index
        break
    if start is None:
        return events
    value = _strip_comment(lines[start]).split(":", 1)[1].strip()
    if value:
        for event in names(start + 1, value):
            if event in EDIT_EVENTS:
                events[event] = DEFAULT_PULL_REQUEST_TYPES
        return events
    event = None
    in_types = False
    seen_child = False
    property_seen = False
    for index in range(start + 1, len(lines)):
        code = _strip_comment(lines[index])
        if not code.strip():
            continue
        indent = len(code) - len(code.lstrip(" "))
        if indent == 0:
            break
        stripped = code.strip()
        key, colon, raw_rest = stripped.partition(":")
        # YAML reads a colon as a mapping indicator only when a space follows
        # it or it ends the line; types:[...] is one plain scalar, not a key.
        spaced = not raw_rest or raw_rest.startswith(" ")
        rest = raw_rest.strip()
        if not seen_child:
            seen_child = True
            if indent != 2:
                refuse(index + 1, "the first line under on: is at indent "
                       "{}; the on: block's events are read only at indent "
                       "2".format(indent))
        if indent in (1, 3):
            refuse(index + 1, "line {!r} under on: is at indent {}; events "
                   "are read only at indent 2".format(stripped, indent))
        elif indent == 2:
            in_types = False
            property_seen = False
            event = None
            if not colon:
                refuse(index + 1, "line {!r} at indent 2 under on: has no "
                       "colon, so YAML does not read it as an event "
                       "key".format(stripped))
            elif _unquote(key) != key:
                refuse(index + 1, "quoted on: key {!r}".format(key))
            elif key != key.rstrip():
                refuse(index + 1, "on: key {!r} holds whitespace before its "
                       "colon".format(key))
            elif not JOB_ID_RE.fullmatch(key):
                refuse(index + 1, "on: key {!r} is not a plain name (an "
                       "explicit '?' key, a tag such as !!str, and every "
                       "other non-plain spelling are refused)".format(key))
            elif not spaced:
                refuse(index + 1, "on: key {!r} has no space after its "
                       "colon; YAML reads the line as one plain scalar, not "
                       "a key".format(key))
            else:
                event = key if key in EDIT_EVENTS else None
                if event is not None:
                    if event in events:
                        refuse(index + 1, "on: declares {} more than "
                               "once".format(event))
                    elif rest:
                        refuse(index + 1, "{}: value {!r} is outside the "
                               "recognized trigger shapes".format(
                                   event, rest))
                    events[event] = DEFAULT_PULL_REQUEST_TYPES
        elif event is None:
            continue
        elif indent == 4:
            in_types = False
            property_seen = True
            if stripped.startswith("- "):
                refuse(index + 1, "sequence item {!r} at indent 4 under {} "
                       "(an indentless sequence); block-list items are read "
                       "only at indent 6 under types:".format(stripped, event))
            elif not colon:
                refuse(index + 1, "line {!r} under {} has no key".format(
                    stripped, event))
            elif _unquote(key) != key:
                refuse(index + 1, "quoted key {!r} under {}".format(
                    key, event))
            elif key != key.rstrip():
                refuse(index + 1, "key {!r} under {} holds whitespace before "
                       "its colon".format(key, event))
            elif not JOB_ID_RE.fullmatch(key):
                refuse(index + 1, "key {!r} under {} is not a plain name (an "
                       "explicit '?' key, a tag such as !!str, and every "
                       "other non-plain spelling are refused)".format(
                           key, event))
            elif not spaced:
                refuse(index + 1, "key {!r} under {} has no space after its "
                       "colon; YAML reads the line as one plain scalar, not "
                       "a key".format(key, event))
            else:
                in_types = key == "types"
                if in_types:
                    if event in typed:
                        refuse(index + 1, "{} declares types: more than "
                               "once".format(event))
                    typed.add(event)
                    events[event] = names(index + 1, rest) if rest else ()
                    in_types = not rest
        elif indent == 6 and in_types and stripped.startswith("- "):
            events[event] += names(index + 1, stripped[2:].strip())
        elif not in_types and indent > 4 and property_seen:
            continue
        else:
            refuse(index + 1, "line {!r} under {} is outside the recognized "
                   "trigger shapes".format(stripped, event))
    return events


def _trigger_findings(lines, members, source, diagnostics):
    """Require pull_request edited exactly where the message/metadata leak
    scan runs (module docstring, TRIGGER)."""
    events = _edit_event_types(lines, source, diagnostics)
    if MSG_LEAKS_SCAN in members:
        types = events.get("pull_request", ())
        missing = [kind for kind in MSG_LEAKS_PULL_REQUEST_TYPES
                   if kind not in types]
        if not missing:
            return []
        return [Finding(
            "trigger",
            source,
            "{} runs {} but its pull_request types lack {}, so the scan does "
            "not re-run on every pull request change it must see (edited is "
            "a PR title/body change)".format(
                source, MSG_LEAKS_SCAN, ", ".join(missing)),
        )]
    return [Finding(
        "trigger",
        source,
        "{} carries {} type edited but does not run {}, so a PR title/body "
        "edit re-runs every job of this workflow; only the workflow that runs "
        "that scan triggers on edited".format(source, event, MSG_LEAKS_SCAN),
    ) for event in EDIT_EVENTS if "edited" in events.get(event, ())]


def _relocation_findings(members, source):
    """msg-leaks.yml may run only the message/metadata leak scan and its
    self-test (module docstring, TWO WORKFLOWS): its status is separate from
    the aggregated Quality gates, so any other member there is a gate the
    aggregated required check silently lost."""
    if source != MSG_LEAKS_CI_SOURCE:
        return []
    return [Finding(
        "relocation",
        identity,
        "{} runs {!r}, which is not the message/metadata leak scan or its "
        "self-test: a gate moved into this workflow leaves the aggregated "
        "Quality gates status silently and re-runs on every PR metadata "
        "edit".format(source, identity),
    ) for identity in sorted(members - MSG_LEAKS_WORKFLOW_MEMBERS)]


def extract_ci(text, source=CI_SOURCE):
    """Extract normalized members from one CI_WORKFLOWS file in the supported
    workflow subset; source labels its diagnostics and findings."""
    diagnostics = []
    members = set()
    origins = {}

    if not isinstance(text, str):
        return Extraction(
            frozenset(),
            {},
            (_diagnostic(
                source, 0, "input-type", "input is not text"),),
        )
    format_diagnostic = _text_format_diagnostic(text, source)
    if format_diagnostic is not None:
        return Extraction(frozenset(), {}, (format_diagnostic,))

    lines = text.splitlines()
    in_jobs = False
    in_steps = False
    current_mapping = None
    current_job = None
    saw_jobs = False
    job_ids = []
    job_fields = {}
    last_job_key = None
    top_keys = set()
    refused_job = False
    job_body_started = False
    steps = []
    current_step = None
    index = 0

    while index < len(lines):
        raw = lines[index]
        line_number = index + 1

        if "\t" in raw:
            diagnostics.append(_diagnostic(
                source,
                line_number,
                "yaml-tab",
                "tabs are outside the supported YAML subset",
            ))
            index += 1
            continue

        code = _strip_comment(raw)
        if not code.strip():
            index += 1
            continue

        stripped = code.strip()
        indent = len(code) - len(code.lstrip(" "))

        feature_error = _yaml_feature_error(stripped)
        if feature_error:
            diagnostics.append(_diagnostic(
                source,
                line_number,
                "yaml-feature",
                feature_error,
            ))

        if indent == 0:
            # Any top-level line closes the jobs: mapping, as it does in YAML.
            in_jobs = False
            in_steps = False
            current_mapping = None
            current_job = None
            refused_job = False
            top_key = stripped.split(":", 1)[0] if ":" in stripped else stripped
            if top_key in top_keys:
                # YAML keeps one value for a repeated key; this rule would
                # read both, so the repeat is refused rather than merged.
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "duplicate-key",
                    "top-level key {!r} is declared more than once".format(
                        top_key),
                ))
            top_keys.add(top_key)
            if stripped == "jobs:":
                in_jobs = True
                saw_jobs = True
            elif top_key not in TOP_LEVEL_KEYS:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "unknown-top-key",
                    "top-level line {!r} is outside the recognized workflow "
                    "keys".format(stripped),
                ))
            index += 1
            continue

        if in_steps and indent <= 4:
            in_steps = False
            current_mapping = None

        if in_jobs and refused_job and indent > 2:
            # The body of a refused job line: already cannot-evaluate.
            index += 1
            continue

        if in_jobs and indent in (1, 3):
            diagnostics.append(_diagnostic(
                source,
                line_number,
                "job-key-indent",
                "line {!r} under jobs: is at indent {}; job ids are read "
                "only at indent 2 and job properties only at indent "
                "4".format(stripped, indent),
            ))
            index += 1
            continue

        if in_jobs and indent == 2:
            in_steps = False
            current_mapping = None
            last_job_key = None
            job_body_started = False
            header = JOB_HEADER_RE.fullmatch(stripped)
            if (header is None
                    or header.group(1).lower() in YAML_NON_STRING_KEYS):
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "job-key-shape",
                    "line {!r} at the job-id indent under jobs: is not a "
                    "plain job id key; YAML can read a job from it that this "
                    "rule would not enumerate, so it is refused, not "
                    "skipped".format(stripped),
                ))
                current_job = None
                refused_job = True
                index += 1
                continue
            refused_job = False
            current_job = stripped
            if stripped[:-1] in job_fields:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "duplicate-job",
                    "job id {!r} is declared more than once".format(
                        stripped[:-1]),
                ))
            else:
                job_ids.append(stripped[:-1])
                job_fields[stripped[:-1]] = dict(start=index, keys=set())
            index += 1
            continue

        if in_jobs and indent >= 4 and current_job is None:
            diagnostics.append(_diagnostic(
                source,
                line_number,
                "orphan-job-content",
                "job content appears without a job mapping; the workflow "
                "structure is not well-formed",
            ))
            index += 1
            continue

        if (in_jobs
                and current_job is not None
                and indent > 4
                and not job_body_started):
            diagnostics.append(_diagnostic(
                source,
                line_number,
                "job-body-indent",
                "the first line of job {!r} is at indent {}; job properties "
                "are read only at indent 4".format(current_job[:-1], indent),
            ))
            refused_job = True
            index += 1
            continue

        if in_jobs and indent == 4:
            job_body_started = True
            job_key = stripped.split(":", 1)[0] if ":" in stripped else stripped
            seen = job_fields[current_job[:-1]]["keys"]
            if job_key in seen:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "duplicate-key",
                    "job {!r} declares {!r} more than once".format(
                        current_job[:-1], job_key),
                ))
            seen.add(job_key)
            if job_key in RESULT_MASKING_JOB_KEYS:
                job_fields[current_job[:-1]].setdefault(
                    "masking", []).append((line_number, job_key))

        if in_jobs and indent == 4 and stripped == "steps:":
            in_steps = True
            current_mapping = None
            current_step = None
            last_job_key = None
            index += 1
            continue

        if in_jobs and not in_steps and indent == 4:
            job_key = stripped.split(":", 1)[0] if ":" in stripped else stripped
            if job_key not in JOB_PROPERTY_KEYS:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "unknown-job-key",
                    "job-level line {!r} is outside the recognized job "
                    "properties".format(stripped),
                ))
            last_job_key = job_key
            if job_key in ("name", "if", "needs"):
                job_fields[current_job[:-1]][job_key] = (
                    line_number,
                    stripped.split(":", 1)[1].strip()
                    if ":" in stripped else "",
                )
            index += 1
            continue

        if not in_steps:
            if last_job_key == "needs" and indent == 6:
                job_fields[current_job[:-1]].setdefault(
                    "needs-items", []).append((line_number, stripped))
            if re.match(r"(?:-\s+)?run:", stripped):
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "run-outside-steps",
                    "run: appears outside a job steps list",
                ))
            if re.match(r"(?:-\s+)?uses:", stripped):
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "uses-outside-steps",
                    "job-level uses: may hide a gate",
                ))
            index += 1
            continue

        if indent == 6 and stripped.startswith("- "):
            current_mapping = None
            item = stripped[2:]
            current_step = dict(
                source=source, job=current_job[:-1], line=line_number,
                name=item[5:].strip() if item.startswith("name:") else item,
                run=False, shell=[])
            steps.append(current_step)
            if item.startswith("name:") and item[5:].strip():
                index += 1
                continue
            if item.startswith("uses:"):
                _classify_uses(
                    item[5:], source, line_number, diagnostics)
                index += 1
                continue
            diagnostics.append(_diagnostic(
                source,
                line_number,
                "step-shape",
                "step must begin with a non-empty name: or uses:",
            ))
            index += 1
            continue

        if indent == 8:
            current_mapping = None

            if current_step is None:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "step-key",
                    "step key {!r} appears before the job's first step "
                    "item".format(stripped),
                ))
                index += 1
                continue

            if stripped in ("env:", "with:"):
                current_mapping = stripped[:-1]
                index += 1
                continue

            if stripped == STEP_SHELL_LINE:
                current_step["shell"].append(line_number)
                index += 1
                continue

            if stripped.startswith("uses:"):
                _classify_uses(
                    stripped[5:], source, line_number, diagnostics)
                index += 1
                continue

            if stripped.startswith("run:"):
                value = stripped[4:].strip()
                current_step["run"] = True

                if value in (">", ">-", ">+"):
                    diagnostics.append(_diagnostic(
                        source,
                        line_number,
                        "folded-run",
                        "folded run: scalars are outside the supported "
                        "subset",
                    ))
                    index += 1
                    continue

                if value in ("|", "|-", "|+"):
                    index += 1
                    content_indent = None
                    leading_blank = None
                    while index < len(lines):
                        body_raw = lines[index]
                        body_number = index + 1

                        if "\t" in body_raw:
                            diagnostics.append(_diagnostic(
                                source,
                                body_number,
                                "yaml-tab",
                                "tabs are outside the supported YAML subset",
                            ))
                            index += 1
                            continue

                        if not body_raw.strip():
                            # A whitespace-only line after the first
                            # content line is inside the scalar wherever
                            # it falls; ahead of it, YAML rejects one
                            # indented past the content indentation the
                            # first content line sets, so
                            # the deepest leading blank is held and
                            # judged there.
                            if content_indent is None:
                                blank_indent = (
                                    len(body_raw)
                                    - len(body_raw.lstrip(" ")))
                                if (leading_blank is None
                                        or blank_indent > leading_blank[0]):
                                    leading_blank = (
                                        blank_indent, body_number)
                            index += 1
                            continue

                        body_indent = (
                            len(body_raw)
                            - len(body_raw.lstrip(" "))
                        )
                        if content_indent is None:
                            if body_indent <= 8:
                                # No content line at all: the scalar is
                                # empty and this line is outside it.
                                break
                            # YAML detects a literal scalar's content
                            # indentation from its FIRST non-empty line;
                            # every non-empty line at or
                            # beyond it is scalar content, comment
                            # spellings included.
                            content_indent = body_indent
                            if (leading_blank is not None
                                    and leading_blank[0] > content_indent):
                                # YAML rejects a leading blank line with
                                # more spaces than the first content line
                                # does, so the layout is refused,
                                # never read as scalar content.
                                diagnostics.append(_diagnostic(
                                    source,
                                    leading_blank[1],
                                    "scalar-blank-indent",
                                    "a leading blank line indented past "
                                    "the scalar content indentation is a "
                                    "layout YAML rejects",
                                ))
                        if body_indent < content_indent:
                            # The first non-empty line below the content
                            # indentation ends the scalar, a comment line
                            # included, so a dedented YAML comment after
                            # the block is outside the script text; the
                            # outer loop re-reads this line, and a layout
                            # YAML rejects is refused there
                            # (step-structure), never passed.
                            break

                        if "${{" in body_raw:
                            # The RAW scalar line, before shell-comment
                            # stripping: GitHub substitutes
                            # ${{ }} into the script text before the shell
                            # runs, comments included, so a substituted
                            # value carrying a newline leaves the comment.
                            diagnostics.append(_diagnostic(
                                source,
                                body_number,
                                "workflow-expression",
                                "a GitHub expression inside run: is not "
                                "statically evaluable",
                            ))
                            index += 1
                            continue

                        body_code = _strip_comment(body_raw)
                        if not body_code.strip():
                            index += 1
                            continue

                        _classify_ci_command(
                            body_code.strip(),
                            source,
                            body_number,
                            True,
                            members,
                            origins,
                            diagnostics,
                        )
                        index += 1
                    continue

                if not value:
                    diagnostics.append(_diagnostic(
                        source,
                        line_number,
                        "empty-run",
                        "run: must have a scalar value",
                    ))
                else:
                    _classify_ci_command(
                        value,
                        source,
                        line_number,
                        False,
                        members,
                        origins,
                        diagnostics,
                    )
                index += 1
                continue

            diagnostics.append(_diagnostic(
                source,
                line_number,
                "step-key",
                "unsupported step key or shape: {!r}".format(
                    stripped),
            ))
            index += 1
            continue

        if indent >= 10 and current_mapping in ("env", "with"):
            if not YAML_KEY_RE.fullmatch(stripped):
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "mapping-entry",
                    "malformed env:/with: entry",
                ))
            else:
                value = stripped.split(":", 1)[1].strip()
                if value.startswith(("[", "{")):
                    diagnostics.append(_diagnostic(
                        source,
                        line_number,
                        "flow-yaml",
                        "flow YAML is outside env:/with: support",
                    ))
                feature_error = _yaml_feature_error(stripped)
                if feature_error:
                    diagnostics.append(_diagnostic(
                        source,
                        line_number,
                        "yaml-feature",
                        feature_error,
                    ))
            index += 1
            continue

        diagnostics.append(_diagnostic(
            source,
            line_number,
            "step-structure",
            "line is outside the supported step structure: "
            "{!r}".format(stripped),
        ))
        index += 1

    if not saw_jobs:
        diagnostics.append(_diagnostic(
            source,
            0,
            "jobs-missing",
            "workflow has no top-level jobs: mapping",
        ))

    for step in steps:
        if step["shell"][1:]:
            diagnostics.append(_diagnostic(
                source,
                step["shell"][1],
                "duplicate-key",
                "step {!r} declares shell: more than once".format(
                    step["name"]),
            ))
        if step["shell"] and not step["run"]:
            diagnostics.append(_diagnostic(
                source,
                step["shell"][0],
                "step-key",
                "shell: applies only to a step with a run: key",
            ))

    findings = _required_status_findings(
        job_ids, job_fields, lines, source, diagnostics)
    findings.extend(_trigger_findings(lines, members, source, diagnostics))
    findings.extend(_relocation_findings(members, source))
    findings.extend(_step_shell_findings(steps))
    diagnostics.extend(_shadow_check(text, source, origins))
    return Extraction(
        frozenset(members),
        origins,
        _dedupe_diagnostics(diagnostics),
        tuple(findings),
    )


def _validate_allowlist(rows):
    diagnostics = []
    entries = []
    seen = set()

    for number, row in enumerate(rows, 1):
        where = "entry {}".format(number)
        required_keys = {
            "side", "identity", "reason", "backlog"}

        if not isinstance(row, dict) or set(row) != required_keys:
            diagnostics.append(_diagnostic(
                "allowlist",
                number,
                "allowlist-shape",
                "{} must have exactly side, identity, reason, "
                "backlog".format(where),
            ))
            continue

        side = row["side"]
        identity = row["identity"]
        reason = row["reason"]
        backlog = row["backlog"]

        if side not in ("ci-only", "local-only"):
            diagnostics.append(_diagnostic(
                "allowlist",
                number,
                "allowlist-side",
                "{} has unknown side {!r}".format(where, side),
            ))

        if (not isinstance(identity, str)
                or not identity
                or identity != identity.strip()
                or "  " in identity):
            diagnostics.append(_diagnostic(
                "allowlist",
                number,
                "allowlist-identity",
                "{} identity is not a non-empty canonical "
                "string".format(where),
            ))
        else:
            try:
                identity_tokens = shlex.split(
                    identity, posix=True)
            except ValueError as exc:
                diagnostics.append(_diagnostic(
                    "allowlist",
                    number,
                    "allowlist-identity",
                    "{} identity cannot be tokenized ({})".format(
                        where, exc),
                ))
            else:
                normalized = normalize(identity_tokens, masked_ok=True)
                if (not normalized.ok
                        or normalized.value != identity):
                    diagnostics.append(_diagnostic(
                        "allowlist",
                        number,
                        "allowlist-identity",
                        "{} identity is not canonical".format(
                            where),
                    ))

        if (not isinstance(reason, str)
                or not reason.strip()
                or reason != reason.strip()):
            diagnostics.append(_diagnostic(
                "allowlist",
                number,
                "allowlist-reason",
                "{} reason must be a trimmed non-empty "
                "string".format(where),
            ))

        if (not isinstance(backlog, str)
                or not backlog.strip()
                or backlog != backlog.strip()):
            diagnostics.append(_diagnostic(
                "allowlist",
                number,
                "allowlist-backlog",
                "{} backlog must be a trimmed non-empty "
                "string".format(where),
            ))

        if isinstance(identity, str):
            if identity in seen:
                diagnostics.append(_diagnostic(
                    "allowlist",
                    number,
                    "allowlist-duplicate",
                    "duplicate identity {!r}".format(identity),
                ))
            seen.add(identity)

        if (side in ("ci-only", "local-only")
                and isinstance(identity, str)
                and isinstance(reason, str)
                and isinstance(backlog, str)):
            entries.append(ActiveException(
                side, identity, reason, backlog))

    return tuple(entries), _dedupe_diagnostics(diagnostics)


def reconcile(local_extraction, ci_extraction, allowlist=ALLOWLIST):
    """Compare extracted sets and reconcile the embedded exceptions."""
    entries, allow_diagnostics = _validate_allowlist(allowlist)

    local = frozenset(local_extraction.members)
    ci = frozenset(ci_extraction.members)
    diagnostics = _dedupe_diagnostics(
        list(local_extraction.diagnostics)
        + list(ci_extraction.diagnostics)
        + list(allow_diagnostics)
    )

    if not local:
        diagnostics = _dedupe_diagnostics(
            list(diagnostics)
            + [_diagnostic(
                LOCAL_SOURCE,
                0,
                "empty-extraction",
                "no local gate members were extracted",
            )]
        )
    if not ci:
        diagnostics = _dedupe_diagnostics(
            list(diagnostics)
            + [_diagnostic(
                CI_SOURCE,
                0,
                "empty-extraction",
                "no CI gate members were extracted",
            )]
        )

    if diagnostics:
        return Report(
            2, local, ci, (), (), (), (), diagnostics)

    local_only = tuple(sorted(local - ci))
    ci_only = tuple(sorted(ci - local))
    active = []
    findings = []
    waived = set()

    for entry in sorted(
            entries, key=lambda item: (item.side, item.identity)):
        in_local = entry.identity in local
        in_ci = entry.identity in ci
        active_as_declared = (
            entry.side == "ci-only" and not in_local and in_ci
        ) or (
            entry.side == "local-only" and in_local and not in_ci
        )

        if active_as_declared:
            active.append(entry)
            waived.add((entry.side, entry.identity))
        elif in_local and in_ci:
            findings.append(Finding(
                "stale-allowlist",
                entry.identity,
                "{} exception is stale because the member is "
                "present on both sides".format(entry.side),
            ))
        elif not in_local and not in_ci:
            findings.append(Finding(
                "stale-allowlist",
                entry.identity,
                "{} exception is stale because the member is "
                "present on neither side".format(entry.side),
            ))
        else:
            actual = "local-only" if in_local else "ci-only"
            findings.append(Finding(
                "wrong-direction-allowlist",
                entry.identity,
                "declared {}, observed {}".format(
                    entry.side, actual),
            ))

    for identity in local_only:
        if ("local-only", identity) not in waived:
            findings.append(Finding(
                "local-only",
                identity,
                "present locally only with no active exception",
            ))

    for identity in ci_only:
        if ("ci-only", identity) not in waived:
            findings.append(Finding(
                "ci-only",
                identity,
                "present in CI only with no active exception",
            ))

    for identity in sorted(MANDATORY_MEMBERS):
        if identity not in local:
            findings.append(Finding(
                "missing-mandatory",
                identity,
                "mandatory member is absent from the local runner",
            ))
        if identity not in ci:
            findings.append(Finding(
                "missing-mandatory",
                identity,
                "mandatory member is absent from CI",
            ))

    findings.extend(ci_extraction.findings)

    findings = tuple(sorted(
        findings,
        key=lambda finding: (
            finding.kind,
            finding.identity,
            finding.message,
        ),
    ))

    return Report(
        1 if findings else 0,
        local,
        ci,
        local_only,
        ci_only,
        tuple(sorted(
            active,
            key=lambda entry: (entry.side, entry.identity),
        )),
        findings,
        (),
    )


def merge_ci(extractions):
    """Union (source, extraction) pairs into the one CI side (module
    docstring, TWO WORKFLOWS). Origins are keyed (source, line), because
    line numbers repeat across files."""
    members = set()
    origins = {}
    diagnostics = []
    findings = []
    for source, extraction in extractions:
        members.update(extraction.members)
        for line_number, scripts in extraction.origins.items():
            origins[(source, line_number)] = set(scripts)
        diagnostics.extend(extraction.diagnostics)
        findings.extend(extraction.findings)
    return Extraction(
        frozenset(members),
        origins,
        _dedupe_diagnostics(diagnostics),
        tuple(findings),
    )


def evaluate(local_text, ci_text, allowlist=ALLOWLIST, extra_ci=()):
    """Reconcile the runner with the CI side: ci_text is quality.yml, and
    extra_ci holds a (source, text) pair for each further workflow."""
    return reconcile(
        extract_local(local_text),
        merge_ci([(CI_SOURCE, extract_ci(ci_text))] + [
            (source, extract_ci(text, source)) for source, text in extra_ci]),
        allowlist,
    )


def run_paths(
        local_path,
        ci_path,
        allowlist=ALLOWLIST,
        reader=None,
        extra_ci_paths=CI_WORKFLOWS[1:]):
    """Read every required absolute path (the runner, quality.yml and each
    (source, path) in extra_ci_paths) and return a structured Report.

    Every file goes through read_runner_text, the ONE byte-level reader: the
    raw bytes are screened before any decode, and the accepted bytes are
    decoded strictly as ASCII with no newline translation."""
    diagnostics = []
    texts = []

    for source, path in (
        (LOCAL_SOURCE, local_path),
        (CI_SOURCE, ci_path),
    ) + tuple(extra_ci_paths):
        if not isinstance(path, Path) or not path.is_absolute():
            diagnostics.append(_diagnostic(
                source,
                0,
                "absolute-path",
                "input path must be absolute",
            ))
            texts.append(None)
            continue

        text, diagnostic = read_runner_text(path, source, reader=reader)
        if diagnostic is None:
            texts.append(text)
        else:
            diagnostics.append(diagnostic)
            texts.append(None)

    entries, allow_diagnostics = _validate_allowlist(allowlist)
    diagnostics.extend(allow_diagnostics)

    if diagnostics:
        return Report(
            2,
            frozenset(),
            frozenset(),
            (),
            (),
            (),
            (),
            _dedupe_diagnostics(diagnostics),
        )

    validated_allowlist = tuple(
        entry._asdict() for entry in entries)
    return evaluate(
        texts[0], texts[1], validated_allowlist,
        tuple((source, text) for (source, _path), text
              in zip(extra_ci_paths, texts[2:])))


def render(report):
    """Render a deterministic human-readable report."""
    if report.code == 2:
        lines = [
            "CANNOT EVALUATE: CI parity was not determined.",
        ]
        for diagnostic in report.diagnostics:
            if diagnostic.line:
                location = "{}:{}".format(
                    diagnostic.source, diagnostic.line)
            else:
                location = diagnostic.source
            lines.append(
                "  {} [{}] {}".format(
                    location,
                    diagnostic.code,
                    diagnostic.message,
                )
            )
        lines.append(
            "PARTIAL, NOT A VERDICT: local={} member(s), "
            "CI={} member(s).".format(
                len(report.local_members),
                len(report.ci_members),
            )
        )
        return "\n".join(lines)

    lines = [
        "RAW DIFFERENCES:",
        "  local-only:",
    ]
    if report.local_only:
        lines.extend(
            "    " + identity
            for identity in report.local_only
        )
    else:
        lines.append("    (none)")

    lines.append("  ci-only:")
    if report.ci_only:
        lines.extend(
            "    " + identity
            for identity in report.ci_only
        )
    else:
        lines.append("    (none)")

    lines.append("ACTIVE EXCEPTIONS:")
    if report.active:
        for entry in report.active:
            lines.append(
                "  {} {} [{}]: {}".format(
                    entry.side,
                    entry.identity,
                    entry.backlog,
                    entry.reason,
                )
            )
    else:
        lines.append("  (none)")

    if report.findings:
        lines.append("FINDINGS:")
        for finding in report.findings:
            lines.append(
                "  {} {}: {}".format(
                    finding.kind,
                    finding.identity,
                    finding.message,
                )
            )
        lines.append(
            "FAIL: CI parity found {} finding(s).".format(
                len(report.findings))
        )
    else:
        lines.append(
            "PASS: local and CI gate member sets reconcile "
            "({} local, {} CI, {} active exception(s)).".format(
                len(report.local_members),
                len(report.ci_members),
                len(report.active),
            )
        )

    lines.append(
        "SCOPE: token-level set parity only; see the module "
        "docstring for residual coverage."
    )
    return "\n".join(lines)


# In-memory reversion of the failure-naming change, used only as the vector-26 flip.
SILENT_RUNNER_REVERT = (
    ('  if "$@"; then :; else\n'
     '    local rc=$?\n'
     '    failed=1\n'
     '    failed_names="${failed_names:+${failed_names}, }${name}"\n'
     '    echo "GATE FAILED: ${name} (exit ${rc})"\n'
     '  fi\n',
     '  if "$@"; then :; else failed=1; fi\n'),
    ('    gitleaks_rc=$?\n'
     '    failed=1\n'
     '    failed_names="${failed_names:+${failed_names}, }secrets (gitleaks)"\n'
     '    echo "GATE FAILED: secrets (gitleaks) (exit ${gitleaks_rc})"\n',
     '    failed=1\n'),
    ('  echo "FAILED GATES: ${failed_names}"\n', ""),
)

# Stub gates for runner_naming_problems. Both record their calls; python3 fails only
# the selected command, and gitleaks exits with the requested status. python3 is also a
# shell function that bash loads through BASH_ENV before the runner starts; it shadows
# the executable, saving a process per gate, and the executable remains the fallback.
# _prepare_stubs writes them once, before the scenario pool starts; vector 27 pins that.
_STUB_PYTHON3 = """#!/bin/sh
printf '%s\\n' "python3 $*" >> "$stub_log" || exit 2
if [ "$*" = "$stub_fail_command" ]; then
  exit 3
fi
exit 0
"""
_STUB_PYTHON3_FUNCTION = """python3() {
  printf '%s\\n' "python3 $*" >> "$stub_log" || return 2
  if [ "$*" = "$stub_fail_command" ]; then
    return 3
  fi
  return 0
}
"""
_STUB_GITLEAKS = """#!/bin/sh
printf '%s\\n' "gitleaks $*" >> "$stub_log" || exit 2
exit "$stub_gitleaks_rc"
"""


def _prepare_stubs(root):
    """Write the stub gates once under root; return (bin directory, BASH_ENV file).

    Call this before any scenario thread starts and share the result read-only. execve
    of a file that any process holds open for writing fails ETXTBSY (bash reports exit
    126), and a child forked on another thread holds every descriptor open at that
    moment until it execs, so a stub written while scenarios run can be caught open by
    a sibling's fork. Scenario trees therefore hold only data that is never executed:
    the runner, which bash reads as its script argument, and the call log.
    """
    functions = root / "stubs.bash"
    functions.write_text(_STUB_PYTHON3_FUNCTION, encoding="utf-8")
    functions.chmod(0o400)
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name, body in (("python3", _STUB_PYTHON3), ("gitleaks", _STUB_GITLEAKS)):
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o500)
    bin_dir.chmod(0o500)
    return bin_dir, functions


def _run_runner_copy(text, stubs, fail_command="", gitleaks_rc=0):
    """Run text as tools/run_all_checks.sh in a scratch tree; return (rc, stdout lines, calls).

    stubs is the shared (bin directory, BASH_ENV file) pair from _prepare_stubs; nothing
    this writes is executed.
    """
    import os
    import shutil
    import subprocess
    import tempfile

    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError("bash not found")
    bin_dir, functions = stubs
    with tempfile.TemporaryDirectory(prefix="ci-parity-runner-") as tmp:
        root = Path(tmp)
        (root / "tools").mkdir()
        runner = root / "tools" / "run_all_checks.sh"
        runner.write_text(text, encoding="utf-8")
        log = root / "calls.log"
        log.write_text("", encoding="utf-8")
        env = dict(
            PATH=str(bin_dir) + os.pathsep + os.defpath,
            BASH_ENV=str(functions),
            HOME=tmp,
            LC_ALL="C",
            stub_log=str(log),
            stub_fail_command=fail_command,
            stub_gitleaks_rc=str(gitleaks_rc),
        )
        proc = subprocess.run(
            [bash, "--noprofile", "--norc", str(runner)],
            cwd=tmp, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60)
        calls = log.read_text(encoding="utf-8").splitlines()
    return proc.returncode, proc.stdout.splitlines(), calls


def _naming_scenarios(text):
    """Build the runtime scenarios; the module docstring discloses residual masks."""
    registered = []
    for raw in text.splitlines():
        tokens = shlex.split(raw, comments=True)
        if tokens[:1] == ["run_gate"] and len(tokens) >= 3:
            registered.append((tokens[1], " ".join(tokens[3:])))
    scenarios = [
        ("passing", "", 0, ()),
        ("combined failure", "-I -B tools/check_leaks.py", 1,
         (("secrets (gitleaks)", 1), ("leaks", 3))),
        ("gitleaks only", "", 1, (("secrets (gitleaks)", 1),)),
    ]
    # Each registered gate fails alone, so a mask conditional on one gate's failure is
    # exercised wherever it sits. Gates sharing a command fail together.
    for command in dict.fromkeys(command for name, command in registered):
        failing = tuple((name, 3) for name, other in registered if other == command)
        scenarios.append(("{} alone".format(failing[0][0]), command, 0, failing))
    return scenarios


def runner_naming_problems(text, first_only=False):
    """Return the failure-naming problems of runner text; empty means failures are named.

    first_only stops after the first batch of scenarios with a problem, for a fixture
    that only needs to be caught.
    """
    import tempfile
    from concurrent.futures import ThreadPoolExecutor

    roster = []
    registered = []
    for raw in text.splitlines():
        tokens = shlex.split(raw, comments=True)
        if tokens[:1] == ["run_gate"] and len(tokens) >= 3:
            name, command = tokens[1], tokens[2:]
            if command[0] != "python3":
                return ["runner stub does not support {!r}".format(command)]
            registered.append((name, " ".join(command[1:])))
            roster.append((name, " ".join(command)))
        elif tokens[:2] == ["if", "gitleaks"]:
            roster.append(("secrets (gitleaks)", " ".join(tokens[1:-1]).rstrip(";")))
    if not registered:
        return ["runner has no registered gates"]
    expected_calls = [command for name, command in roster]
    expected_headers = ["--- {} ---".format(name) for name, command in roster]
    scenarios = _naming_scenarios(text)
    problems = []
    workers = 4
    with tempfile.TemporaryDirectory(prefix="ci-parity-stubs-") as shared:
        # Every executable exists, closed, before the pool does; see _prepare_stubs.
        try:
            stubs = _prepare_stubs(Path(shared))
        except OSError as exc:
            return ["runner stubs could not be prepared: {!r}".format(exc)]

        def run(scenario):
            try:
                return _run_runner_copy(text, stubs, scenario[1], scenario[2])
            except Exception as exc:  # no bash, a scratch-tree error, or a timeout
                return exc

        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for start in range(0, len(scenarios), workers):
                    batch = scenarios[start:start + workers]
                    for scenario, outcome in zip(batch, pool.map(run, batch)):
                        problems.extend(_scenario_problems(
                            scenario, outcome, expected_calls, expected_headers))
                    if first_only and problems:
                        break
        finally:
            stubs[0].chmod(0o700)  # so the shared directory can be removed
    return problems


def _scenario_problems(scenario, outcome, expected_calls, expected_headers):
    """Return the problems of one runner_naming_problems scenario's outcome."""
    label, command, gitleaks_rc, failures = scenario
    if isinstance(outcome, Exception):
        return ["{}: runner copy did not complete: {!r}".format(label, outcome)]
    rc, lines, calls = outcome
    problems = []
    expected_rc = 1 if failures else 0
    if rc != expected_rc:
        problems.append("{}: exit {}, expected {}".format(label, rc, expected_rc))
    named = [line for line in lines if line.startswith("GATE FAILED:")]
    expected_named = [
        "GATE FAILED: {} (exit {})".format(name, status)
        for name, status in failures
    ]
    if named != expected_named:
        problems.append("{}: named {!r}, expected {!r}".format(
            label, named, expected_named))
    summaries = [line for line in lines if line.startswith("FAILED GATES:")]
    expected_summaries = (
        ["FAILED GATES: " + ", ".join(name for name, status in failures)]
        if failures else []
    )
    ending = expected_summaries + ["RESULT: FAIL" if failures else "RESULT: PASS"]
    if summaries != expected_summaries or lines[-len(ending):] != ending:
        problems.append("{}: unexpected summary or result {!r}".format(
            label, lines[-len(ending):]))
    headers = [line for line in lines if line.startswith("--- ")]
    if calls != expected_calls or headers != expected_headers:
        problems.append("{}: calls or headers differ from the declared roster".format(
            label))
    return problems


# Process-wide audit hook (CPython cannot remove one), inert unless a recorder is
# registered in _WRITE_RECORDERS; the same shape as tools/selftest_git_fixture_env.py.
_WRITE_RECORDERS = []
_WRITE_AUDIT_INSTALLED = False


def _dispatch_write_audit(event, args):
    for recorder in tuple(_WRITE_RECORDERS):
        recorder(event, args)


def _stubs_prepared_before_pool(text, red_command):
    """Return the stub-ordering evidence of one runner_naming_problems(text) call.

    DETERMINISTIC guard for the ETXTBSY ordering in runner_naming_problems (no stress,
    no timing), in the style of the config/executables-prepared-before-pool control in
    tools/selftest_git_fixture_env.py: every file a scenario executes must be written
    and chmodded on the MAIN thread before the scenario pool is constructed. A CPython
    audit hook records every open-for-write, chmod, rename, link and symlink with its
    thread and whether the pool exists yet; just before each scenario starts bash,
    inside the pool, a snapshot lists the executable files in its stub PATH entry and
    in its scenario tree. Writing stubs per scenario again turns this red on every run,
    whether or not a fork happens to race a write. The scenario failing red_command
    chmods the shared gitleaks stub from inside the pool and must be flagged, so a
    recorder that sees nothing cannot pass; the required-stub and
    every-executable-was-seen legs keep the rest from passing vacuously.
    File-descriptor-only operations (os.fchmod, a write through an inherited
    descriptor) are outside what these audit events name, as is an os.open with
    dir_fd (the open audit event carries no dir_fd argument, so a relative path
    opened that way stays working-directory-resolved and is not matched to the
    stub) and a write made by a child process (a cp or install run in a
    subprocess); only the never-seen-written leg catches a stub that no Python
    code wrote. A dir_fd-relative path is resolved through /proc/self/fd only for
    the os.chmod, os.rename, os.link and os.symlink events, and only where that
    pseudo-filesystem exists; without it such a path stays working-directory-
    resolved, the same residual class as the descriptor-only operations.

    Returns (pool constructed, scenarios observed, distinct stub directories, required
    stubs missing, executables never seen written, executables in scenario trees, and
    sorted (name, event, count) writes to an executable after the pool was constructed,
    counted so the red chmod cannot mask a real one).
    """
    import collections
    import concurrent.futures
    import os
    import subprocess
    import threading
    from unittest.mock import patch

    global _WRITE_AUDIT_INSTALLED
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    target = {"open": 0, "os.chmod": 0, "os.rename": 1, "os.link": 1, "os.symlink": 1}
    # Index of the dir_fd argument that scopes each event's recorded path (the dst
    # path for rename and link); open events carry no dir_fd.
    dir_fd_index = {"os.chmod": 2, "os.rename": 3, "os.link": 3, "os.symlink": 2}
    main_thread = threading.main_thread().ident
    real_run = subprocess.run
    pool_started = []
    events = []
    snapshots = []

    def record(event, args):
        if event not in target or (event == "open" and not args[2] & write_flags):
            return
        path = args[target[event]]
        if isinstance(path, int):
            return
        path = os.fsdecode(path)
        index = dir_fd_index.get(event)
        if not os.path.isabs(path) and index is not None and index < len(args):
            dir_fd = args[index]
            if isinstance(dir_fd, int) and dir_fd >= 0:
                try:
                    path = os.path.join(
                        os.readlink("/proc/self/fd/%d" % dir_fd), path)
                except OSError:
                    pass  # no /proc: disclosed residual, path stays cwd-resolved
        events.append((os.path.abspath(path), event,
                       threading.get_ident() == main_thread and not pool_started))

    class MarkedPool(concurrent.futures.ThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            pool_started.append(True)
            super().__init__(*args, **kwargs)

    def executables(top):
        found = set()
        for folder, _, names in os.walk(top):
            for name in names:
                path = os.path.join(folder, name)
                if os.path.isfile(path) and os.stat(path).st_mode & 0o111:
                    found.add(os.path.abspath(path))
        return found

    def snapshot_run(argv, **kwargs):
        env = kwargs["env"]
        stub_dir = env["PATH"].split(os.pathsep)[0]
        found = executables(stub_dir)
        snapshots.append((stub_dir, found, executables(kwargs["cwd"])))
        if env["stub_fail_command"] == red_command:
            for path in found:
                if os.path.basename(path) == "gitleaks":
                    os.chmod(path, os.stat(path).st_mode & 0o7777)
        return real_run(argv, **kwargs)

    if not _WRITE_AUDIT_INSTALLED:
        sys.addaudithook(_dispatch_write_audit)
        _WRITE_AUDIT_INSTALLED = True
    _WRITE_RECORDERS.append(record)
    try:
        with patch.object(concurrent.futures, "ThreadPoolExecutor", MarkedPool), \
                patch.object(subprocess, "run", snapshot_run):
            runner_naming_problems(text)
    finally:
        _WRITE_RECORDERS.remove(record)
    required = {"python3", "gitleaks"}
    found = set().union(*(item[1] for item in snapshots))
    written = {path for path, _, _ in events}
    return (
        bool(pool_started),
        len(snapshots),
        len({item[0] for item in snapshots}),
        sorted(set().union(*(required - {os.path.basename(p) for p in item[1]}
                             for item in snapshots))),
        sorted({os.path.basename(p) for p in found - written}),
        sorted({os.path.basename(p) for item in snapshots for p in item[2]}),
        sorted((name, event, n) for (name, event), n in collections.Counter(
            (os.path.basename(p), event)
            for p, event, early in events if p in found and not early).items()),
    )


def self_test():
    failures = []
    count = 0
    common = (
        "python3 -I -B tools/check_ci_parity.py --self-test",
        "python3 -I -B tools/check_ci_parity.py",
        "python3 -I -B tools/check_git_option_table.py --self-test",
        "python3 -I -B tools/check_git_option_table.py",
        # The mandatory repository contracts: every matched fixture roster carries them,
        # as the two real runners do.
        "python3 -I -B .github/check_cited_standards_contract.py --self-test",
        "python3 -I -B .github/check_cited_standards_contract.py",
        "python3 -I -B .github/check_newtab_contract.py",
    )

    def local_fixture(commands=(), tail=()):
        lines = [
            "#!/usr/bin/env bash",
            "set -uo pipefail",
            "failed=0",
            'failed_names=""',
            "run_gate() {",
            '  local name="$1"; shift',
            '  echo "--- ${name} ---"',
            '  if "$@"; then :; else',
            "    local rc=$?",
            "    failed=1",
            '    failed_names="${failed_names:+${failed_names}, }${name}"',
            '    echo "GATE FAILED: ${name} (exit ${rc})"',
            "  fi",
            "  echo",
            "}",
        ]
        for number, command in enumerate(commands, 1):
            lines.append(
                'run_gate "gate{}" {}'.format(
                    number, command))
        lines.extend(tail)
        return "\n".join(lines) + "\n"

    def ci_fixture(commands=(), extra_steps=()):
        lines = [
            "name: Test",
            "jobs:",
            "  quality:",
            "    runs-on: ubuntu-latest",
            "    steps:",
            "      - uses: actions/checkout@v4",
        ]
        for number, command in enumerate(commands, 1):
            lines.extend((
                "      - name: Gate {}".format(number),
                "        shell: bash",
                "        run: " + command,
            ))
        lines.extend(extra_steps)
        return "\n".join(lines) + "\n"

    def case(
            name,
            report,
            want_code,
            kinds=(),
            local_only=None,
            ci_only=None,
            active=None):
        nonlocal count
        count += 1

        if report.code != want_code:
            failures.append(
                "{}: code {}, expected {}\n{}".format(
                    name, report.code, want_code, render(report))
            )

        got_kinds = {
            finding.kind for finding in report.findings}
        if not set(kinds).issubset(got_kinds):
            failures.append(
                "{}: finding kinds {}, expected at least {}".format(
                    name,
                    sorted(got_kinds),
                    sorted(kinds),
                )
            )

        if (local_only is not None
                and set(report.local_only) != set(local_only)):
            failures.append(
                "{}: local-only {}, expected {}".format(
                    name, report.local_only, local_only)
            )

        if (ci_only is not None
                and set(report.ci_only) != set(ci_only)):
            failures.append(
                "{}: ci-only {}, expected {}".format(
                    name, report.ci_only, ci_only)
            )

        if active is not None:
            got_active = {
                (entry.side, entry.identity)
                for entry in report.active
            }
            if got_active != set(active):
                failures.append(
                    "{}: active exceptions differ".format(name))

    both = common + (
        "python3 -I -B tools/a.py",
        "python3 -I -B tools/a.py --self-test",
    )
    case(
        "01 matched live and self-test",
        evaluate(
            local_fixture(both),
            ci_fixture(both),
            (),
        ),
        0,
    )

    def drop(member):
        """The common roster without the one command whose identity is member."""
        return tuple(c for c in common if c != "python3 -I -B " + member)

    case("01b repository gates matched",
         evaluate(local_fixture(common), ci_fixture(common), ()), 0)
    for repo_gate in sorted(REPO_GATES):
        case("01c repository gate local-only: " + repo_gate,
             evaluate(local_fixture(common), ci_fixture(drop(repo_gate)), ()),
             1, ("local-only", "missing-mandatory"), (repo_gate,), ())
        case("01d repository gate ci-only: " + repo_gate,
             evaluate(local_fixture(drop(repo_gate)), ci_fixture(common), ()),
             1, ("ci-only", "missing-mandatory"), (), (repo_gate,))
        case("01e repository gate shadow scan: " + repo_gate,
             evaluate(local_fixture(common, ("echo " + repo_gate,)), ci_fixture(common), ()),
             2)
        # The runner's echo line is refused by its own grammar too; a workflow step name is
        # not, so only the shadow scan's match on this repository gate refuses it.
        case("01g repository gate shadow scan in a step name: " + repo_gate,
             evaluate(local_fixture(common), ci_fixture(common, (
                 "      - name: Run " + repo_gate,
                 "        run: python3 -I -B tools/check_ci_parity.py",
             )), ()), 2)
    # claude PR #520 round 2 MEDIUM-1: each mandatory repository-contract member removed
    # from BOTH runners together is a missing-mandatory finding, never a silent pass. The
    # member list is spelled out rather than derived from MANDATORY_MEMBERS, so reverting
    # the contracts out of that set turns these vectors red instead of silently skipping.
    for member in (
            ".github/check_cited_standards_contract.py",
            ".github/check_cited_standards_contract.py --self-test",
            ".github/check_newtab_contract.py"):
        count += 1
        if member not in MANDATORY_MEMBERS:
            failures.append(
                "01h {} is not in MANDATORY_MEMBERS; a both-runner removal of a "
                "repository contract would be undetected again".format(member))
        case("01h repository contract removed from both runners: " + member,
             evaluate(local_fixture(drop(member)), ci_fixture(drop(member)), ()),
             1, ("missing-mandatory",), (), ())
    unknown = "python3 -I -B .github/unrecognized.py"
    case("01f unknown repository executable refused",
         evaluate(local_fixture(common + (unknown,)),
                  ci_fixture(common + (unknown,)), ()), 2)

    fail_without_change = evaluate(
        local_fixture(common + (
            "python3 -I -B tools/local.py",)),
        ci_fixture(common),
        (),
    )
    case(
        "02 fail-without-change local-only",
        fail_without_change,
        1,
        ("local-only",),
        ("tools/local.py",),
        (),
    )

    case(
        "03 ci-only",
        evaluate(
            local_fixture(common),
            ci_fixture(common + (
                "python3 tools/ci.py",)),
            (),
        ),
        1,
        ("ci-only",),
        (),
        ("tools/ci.py",),
    )

    case(
        "04 self-test leg is distinct",
        evaluate(
            local_fixture(common + (
                "python3 tools/a.py",
                "python3 tools/a.py --self-test",
            )),
            ci_fixture(common + (
                "python3 tools/a.py",)),
            (),
        ),
        1,
        ("local-only",),
        ("tools/a.py --self-test",),
        (),
    )

    waiver = ({
        "side": "ci-only",
        "identity": "tools/ci.py",
        "reason": "fixture context",
        "backlog": "T-1",
    },)

    case(
        "05 active allowlist",
        evaluate(
            local_fixture(common),
            ci_fixture(common + (
                "python3 tools/ci.py",)),
            waiver,
        ),
        0,
        (),
        (),
        ("tools/ci.py",),
        (("ci-only", "tools/ci.py"),),
    )

    case(
        "06 stale allowlist present both",
        evaluate(
            local_fixture(common + (
                "python3 tools/ci.py",)),
            ci_fixture(common + (
                "python3 tools/ci.py",)),
            waiver,
        ),
        1,
        ("stale-allowlist",),
    )

    case(
        "07 stale allowlist gone",
        evaluate(
            local_fixture(common),
            ci_fixture(common),
            waiver,
        ),
        1,
        ("stale-allowlist",),
    )

    case(
        "08 wrong-direction allowlist",
        evaluate(
            local_fixture(common + (
                "python3 tools/ci.py",)),
            ci_fixture(common),
            waiver,
        ),
        1,
        ("wrong-direction-allowlist", "local-only"),
    )

    gitleaks_tail = (
        "if gitleaks dir . --no-banner --redact "
        "--exit-code 1; then",
        '  echo "PASS: gitleaks found no leaks"',
        "else",
        "  gitleaks_rc=$?",
        "  failed=1",
        '  failed_names="${failed_names:+${failed_names}, }secrets (gitleaks)"',
        '  echo "GATE FAILED: secrets (gitleaks) (exit ${gitleaks_rc})"',
        "fi",
    )
    case(
        "09 normalization equivalences",
        evaluate(
            local_fixture(
                common + (
                    "python3 -I -B tools/a.py",),
                gitleaks_tail,
            ),
            ci_fixture(common + (
                "python3 tools/a.py",
                "./gitleaks dir . --no-banner --redact "
                "--exit-code 1",
            )),
            (),
        ),
        0,
    )

    local_version = local_fixture(common + (
        "python3 tools/check_version_monotonicity.py",))
    ci_version_lines = ci_fixture(common).rstrip().splitlines()
    ci_version_lines.extend((
        "      - name: Version",
        "        shell: bash",
        "        run: |",
        "          set -euo pipefail",
        '          if [ "$EVENT_NAME" = "pull_request" ]; then',
        "            python3 -I -B "
        "tools/check_version_monotonicity.py "
        '--base "origin/${GITHUB_BASE_REF}"',
        '          elif git rev-parse --verify --quiet "HEAD^" '
        ">/dev/null; then",
        "            python3 -I -B "
        "tools/check_version_monotonicity.py "
        '--base "HEAD^"',
        "          else",
        "            python3 -I -B "
        "tools/check_version_monotonicity.py",
        "          fi",
    ))
    base_waiver = (
        {
            "side": "ci-only",
            "identity": (
                "tools/check_version_monotonicity.py --base <ref:origin/${GITHUB_BASE_REF}>"
            ),
            "reason": "fixture baseline",
            "backlog": "T-2",
        },
        {
            "side": "ci-only",
            "identity": (
                "tools/check_version_monotonicity.py --base HEAD^"
            ),
            "reason": "fixture baseline: HEAD^ is a kept literal",
            "backlog": "T-2",
        },
    )
    case(
        "10 runtime base masking",
        evaluate(
            local_version,
            "\n".join(ci_version_lines) + "\n",
            base_waiver,
        ),
        0,
    )

    protected_ci_lines = ci_fixture(common).rstrip().splitlines()
    protected_ci_lines.extend((
        "      - name: Branch root",
        "        shell: bash",
        "        run: |",
        "          set -euo pipefail",
        '          if [ "$EVENT_NAME" = "pull_request" ]; then',
        "            python3 -I -B tools/check_branch_root.py "
        '--protected "origin/${GITHUB_BASE_REF}" '
        '--head "$PR_HEAD_SHA" --max-lag 200',
        "          else",
        "            python3 -I -B tools/check_branch_root.py "
        '--protected "origin/${GITHUB_REF_NAME}" --max-lag 200',
        "          fi",
    ))
    protected_local = local_fixture(common + (
        "python3 tools/check_branch_root.py --max-lag 200",))
    protected_waivers = (
        {
            "side": "ci-only",
            "identity": (
                "tools/check_branch_root.py "
                "--protected <ref:origin/${GITHUB_BASE_REF}> "
                "--head <ref:$PR_HEAD_SHA> --max-lag 200"
            ),
            "reason": "fixture baseline",
            "backlog": "T-2",
        },
        {
            "side": "ci-only",
            "identity": (
                "tools/check_branch_root.py --protected <ref:origin/${GITHUB_REF_NAME}> --max-lag 200"
            ),
            "reason": "fixture baseline",
            "backlog": "T-2",
        },
        {
            "side": "local-only",
            "identity": "tools/check_branch_root.py --max-lag 200",
            "reason": "fixture baseline",
            "backlog": "T-2",
        },
    )
    case(
        "10b runtime protected/head masking",
        evaluate(
            protected_local,
            "\n".join(protected_ci_lines) + "\n",
            protected_waivers,
        ),
        0,
    )

    case(
        "10c literal masked-flag values diverge",
        evaluate(
            local_fixture(common + (
                "python3 tools/check_versions.py --base localref",)),
            ci_fixture(common + (
                "python3 -I -B tools/check_versions.py --base OTHERref",)),
            (),
        ),
        1,
    )

    tampered_body_local = "\n".join((
        "#!/usr/bin/env bash",
        "set -uo pipefail",
        "failed=0",
        'failed_names=""',
        "run_gate() {",
        '  local name="$1"; shift',
        '  echo "--- ${name} ---"',
        '  eval "$INJECT"',
        '  if "$@"; then :; else',
        "    local rc=$?",
        "    failed=1",
        '    failed_names="${failed_names:+${failed_names}, }${name}"',
        '    echo "GATE FAILED: ${name} (exit ${rc})"',
        "  fi",
        "  echo",
        "}",
        "run_gate \"gate1\" python3 tools/a.py",
    )) + "\n"
    case(
        "10d tampered run_gate body is cannot-evaluate",
        evaluate(
            tampered_body_local,
            ci_fixture(common + ("python3 tools/a.py",)),
            (),
        ),
        2,
    )

    silent_body_local = "\n".join((
        "#!/usr/bin/env bash",
        "set -uo pipefail",
        "failed=0",
        'failed_names=""',
        "run_gate() {",
        '  local name="$1"; shift',
        '  echo "--- ${name} ---"',
        '  if "$@"; then :; else failed=1; fi',
        "  echo",
        "}",
        "run_gate \"gate1\" python3 tools/a.py",
    )) + "\n"
    case(
        "10d2 silent run_gate body (names no failing gate) is cannot-evaluate",
        evaluate(
            silent_body_local,
            ci_fixture(common + ("python3 tools/a.py",)),
            (),
        ),
        2,
    )

    case(
        "10e top-level exit is cannot-evaluate",
        evaluate(
            local_fixture(common + ("python3 tools/a.py",), ("exit 0",)),
            ci_fixture(common + ("python3 tools/a.py",)),
            (),
        ),
        2,
    )

    unknown_top_ci = "\n".join((
        "name: Test",
        "bogus_top_key: value",
        "jobs:",
        "  quality:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - name: Gate 1",
        "        run: python3 tools/a.py",
    )) + "\n"
    case(
        "10f unknown top-level key is cannot-evaluate",
        evaluate(
            local_fixture(common + ("python3 tools/a.py",)),
            unknown_top_ci,
            (),
        ),
        2,
    )

    unknown_job_ci = "\n".join((
        "name: Test",
        "jobs:",
        "  quality:",
        "    runs-on: ubuntu-latest",
        "    bogus_job_key: nope",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - name: Gate 1",
        "        run: python3 tools/a.py",
    )) + "\n"
    case(
        "10g unknown job-level key is cannot-evaluate",
        evaluate(
            local_fixture(common + ("python3 tools/a.py",)),
            unknown_job_ci,
            (),
        ),
        2,
    )

    case(
        "10h mid-word hash does not hide a gate",
        evaluate(
            local_fixture(
                common + ("python3 tools/a.py",),
                ("echo prefix#not-comment; python3 tools/hidden.py",)),
            ci_fixture(common + ("python3 tools/a.py",)),
            (),
        ),
        2,
    )

    case(
        "10i unbalanced if/fi is cannot-evaluate",
        evaluate(
            local_fixture(
                common + ("python3 tools/a.py",),
                ("if [ 1 -eq 1 ]; then",)),
            ci_fixture(common + ("python3 tools/a.py",)),
            (),
        ),
        2,
    )

    orphan_steps_ci = "\n".join((
        "name: Test",
        "jobs:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - name: Gate 1",
        "        run: python3 tools/a.py",
    )) + "\n"
    case(
        "10j steps without a job mapping is cannot-evaluate",
        evaluate(
            local_fixture(common + ("python3 tools/a.py",)),
            orphan_steps_ci,
            (),
        ),
        2,
    )

    selfcmp_ci = "\n".join((
        "name: Test",
        "jobs:",
        "  quality:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - name: Branch root",
        "        shell: bash",
        '        run: python3 tools/check_branch_root.py '
        '--protected "$PR_HEAD_SHA" --head "$PR_HEAD_SHA"',
    )) + "\n"
    selfcmp_waiver = ({
        "side": "ci-only",
        "identity": (
            "tools/check_branch_root.py "
            "--protected <ref:origin/${GITHUB_BASE_REF}> --head <ref:$PR_HEAD_SHA>"
        ),
        "reason": "distinct-ref form is the reviewed CI shape",
        "backlog": "T-2",
    },)
    case(
        "10k masked self-comparison diverges from the distinct-ref waiver",
        evaluate(
            local_fixture(common + ("python3 tools/check_branch_root.py",)),
            selfcmp_ci,
            selfcmp_waiver,
        ),
        1,
    )

    case(
        "10l extra fi (underflow) is cannot-evaluate",
        evaluate(
            local_fixture(common + ("python3 tools/a.py",), ("fi",)),
            ci_fixture(common + ("python3 tools/a.py",)),
            (),
        ),
        2,
    )

    case(
        "10m whitespace-bearing gate argument is cannot-evaluate",
        evaluate(
            local_fixture(common + (
                'python3 tools/a.py --label "x y"',)),
            ci_fixture(common + ("python3 tools/a.py",)),
            (),
        ),
        2,
    )

    case(
        "11 dynamic script target",
        evaluate(
            local_fixture(common + (
                "python3 -I -B tools/${g}.py",)),
            ci_fixture(common),
            (),
        ),
        2,
    )

    case(
        "12 unclassified executable",
        evaluate(
            local_fixture(
                common, ("npx some-scanner .",)),
            ci_fixture(common),
            (),
        ),
        2,
    )

    case(
        "13 gate piped to sink",
        evaluate(
            local_fixture(common + (
                "python3 tools/x.py | tee log",)),
            ci_fixture(common),
            (),
        ),
        2,
    )

    folded = "\n".join((
        "name: Test",
        "jobs:",
        "  quality:",
        "    runs-on: ubuntu-latest",
        "    steps:",
        "      - name: Folded",
        "        run: >",
        "          python3 -I -B "
        "tools/check_ci_parity.py",
    )) + "\n"
    case(
        "14 folded run block",
        evaluate(local_fixture(common), folded, ()),
        2,
    )

    case(
        "15 unknown uses",
        evaluate(
            local_fixture(common),
            ci_fixture(
                common,
                ("      - uses: some/action@v1",),
            ),
            (),
        ),
        2,
    )

    case(
        "16 circular local runner",
        evaluate(
            local_fixture(common),
            ci_fixture(common + (
                "bash tools/run_all_checks.sh",)),
            (),
        ),
        2,
    )

    case(
        "17 empty extraction",
        evaluate(
            local_fixture(()),
            ci_fixture(common),
            (),
        ),
        2,
    )

    case(
        "18 shadow scan discrepancy",
        evaluate(
            local_fixture(
                common,
                ("echo tools/check_shadow.py",),
            ),
            ci_fixture(common + (
                "python3 tools/check_shadow.py",)),
            (),
        ),
        2,
    )

    case(
        "19 mandatory self-members",
        evaluate(
            local_fixture(("python3 tools/a.py",)),
            ci_fixture(("python3 tools/a.py",)),
            (),
        ),
        1,
        ("missing-mandatory",),
    )

    # Both rosters carry the ci-parity mandatory members but omit the git-option-table pair and the
    # three repository-contract members, so the only findings are missing-mandatory for those five
    # (rosters are otherwise matched, no local/ci-only drift).
    git_option_absent = (
        "python3 -I -B tools/check_ci_parity.py --self-test",
        "python3 -I -B tools/check_ci_parity.py",
    )
    case(
        "19b git-option-table mandatory members",
        evaluate(
            local_fixture(git_option_absent),
            ci_fixture(git_option_absent),
            (),
        ),
        1,
        ("missing-mandatory",),
    )

    def raising_reader(exc):
        def reader(_path):
            raise exc
        return reader

    case(
        "20a missing input",
        run_paths(
            Path("/local"),
            Path("/ci"),
            (),
            raising_reader(FileNotFoundError()),
        ),
        2,
    )

    case(
        "20b unreadable input",
        run_paths(
            Path("/local"),
            Path("/ci"),
            (),
            raising_reader(PermissionError()),
        ),
        2,
    )

    case(
        "20c invalid UTF-8 input",
        run_paths(
            Path("/local"),
            Path("/ci"),
            (),
            lambda _path: b"\xff",
        ),
        2,
    )

    duplicate = waiver + ({
        "side": "local-only",
        "identity": "tools/ci.py",
        "reason": "duplicate",
        "backlog": "T-3",
    },)
    case(
        "21a duplicate allowlist identity",
        evaluate(
            local_fixture(common),
            ci_fixture(common),
            duplicate,
        ),
        2,
    )

    empty_reason = ({
        "side": "ci-only",
        "identity": "tools/ci.py",
        "reason": "",
        "backlog": "T-1",
    },)
    case(
        "21b empty allowlist reason",
        evaluate(
            local_fixture(common),
            ci_fixture(common),
            empty_reason,
        ),
        2,
    )

    bad_side = ({
        "side": "sometimes",
        "identity": "tools/ci.py",
        "reason": "bad side",
        "backlog": "T-1",
    },)
    case(
        "21c unknown allowlist side",
        evaluate(
            local_fixture(common),
            ci_fixture(common),
            bad_side,
        ),
        2,
    )

    composite_waiver = ({
        "side": "ci-only",
        "identity": "tools/waived.py",
        "reason": "fixture waiver",
        "backlog": "T-1",
    },)
    composite = evaluate(
        local_fixture(common + (
            "python3 tools/local.py",)),
        ci_fixture(common + (
            "python3 tools/ci.py",
            "python3 tools/waived.py",
        )),
        composite_waiver,
    )
    expected_render = "\n".join((
        "RAW DIFFERENCES:",
        "  local-only:",
        "    tools/local.py",
        "  ci-only:",
        "    tools/ci.py",
        "    tools/waived.py",
        "ACTIVE EXCEPTIONS:",
        "  ci-only tools/waived.py [T-1]: fixture waiver",
        "FINDINGS:",
        "  ci-only tools/ci.py: present in CI only with no "
        "active exception",
        "  local-only tools/local.py: present locally only "
        "with no active exception",
        "FAIL: CI parity found 2 finding(s).",
        "SCOPE: token-level set parity only; see the module "
        "docstring for residual coverage.",
    ))
    count += 1
    if render(composite) != expected_render:
        failures.append(
            "22 renderer determinism mismatch:\n{}".format(
                render(composite))
        )

    if (fail_without_change.code != 1
            or "tools/local.py"
            not in fail_without_change.local_only):
        failures.append(
            "fail-without-the-change vector did not produce "
            "its required failure"
        )

    # OPF-SELF-CONTAIN grammar-widening (change-carries-check): the widened PY_TARGET_RE/TOOL_RE
    # must treat an opf/tools/*.py gate as a first-class member. Recognized on both sides parity
    # holds; recognized on one side only parity still fails. Without the widening an opf/tools/*.py
    # path is not a valid gate target and is not extracted as a member, so both vectors return code 2
    # (cannot-evaluate) with gate-target and shadow-miss diagnostics rather than a silent pass; the
    # widening turns that cannot-evaluate into a real parity success or a real mismatch. Both vectors
    # therefore fail without the change.
    case(
        "23 opf/tools recognized, parity holds both sides",
        evaluate(
            local_fixture(both + ("python3 -I -B opf/tools/b.py",)),
            ci_fixture(both + ("python3 -I -B opf/tools/b.py",)),
            (),
        ),
        0,
    )
    case(
        "24 opf/tools one-sided mismatch still fails",
        evaluate(
            local_fixture(common + ("python3 -I -B opf/tools/b.py",)),
            ci_fixture(common),
            (),
        ),
        1,
        ("local-only",),
        ("opf/tools/b.py",),
        (),
    )

    # OPF-SELF-CONTAIN anchoring (change-carries-check): TOOL_RE uses an ALLOWLIST path
    # boundary, so a mid-path segment must not over-match, including a dotted parent or a
    # nested "./". This vector fails if the anchor is reverted to the loose \b form or to
    # either earlier denylist-lookbehind form, both of which extract a path from a dotted
    # parent such as evil./tools/x.py; the allowlist form must still accept a legitimate
    # path-start reference.
    count += 1
    for midpath in (
            "evil/tools/x.py", "evil/opf/tools/x.py",
            "evil./tools/x.py", "evil./opf/tools/x.py",
            "evil/./tools/x.py", "a/opf/tools/x.py",
            "opf/opf/tools/x.py", "xopf/tools/x.py",
            "dir.name/tools/x.py"):
        extracted = TOOL_RE.findall(midpath)
        if extracted:
            failures.append(
                "25 anchored TOOL_RE must not extract from mid-path "
                "{!r}, got {!r}".format(midpath, extracted)
            )
    for good, want in (
            ("tools/x.py", "tools/x.py"),
            ("opf/tools/x.sh", "opf/tools/x.sh"),
            ("./opf/tools/x.py", "opf/tools/x.py")):
        extracted = TOOL_RE.findall(good)
        if extracted != [want]:
            failures.append(
                "25 anchored TOOL_RE must still accept {!r} as {!r}, "
                "got {!r}".format(good, want, extracted)
            )

    # Failure naming (change-carries-check): a scratch copy of the LIVE runner must name
    # combined failures, gitleaks failing alone, and each registered gate failing alone
    # before RESULT: FAIL. Calls and headers must match the declared roster in each
    # scenario. The flip reverts the naming in memory and must be caught, so this vector
    # fails without the change. Static fixtures must be rejected as failure-state
    # assignments. Runtime fixtures skip the static check and must be caught by the
    # runner run: each masks a failure only when one gate fails alone, except the
    # skipped gate, which only the roster comparison sees.
    count += 1
    live_runner, live_read_diagnostic = read_runner_text(
        LOCAL_PATH, LOCAL_SOURCE)
    if live_read_diagnostic is not None:
        failures.append("26 cannot read live runner: {!r}".format(
            live_read_diagnostic))
    else:
        live_extraction = extract_local(live_runner)
        if live_extraction.diagnostics:
            failures.append("26 live runner: {!r}".format(live_extraction.diagnostics))
        # The standalone OPF runner, adapted through the ONE shared
        # adapter tools/selftest_git_fixture_env.py also uses (validated
        # directory binding and terminal exit 0 removed, $here rebased),
        # must give zero diagnostics under the same grammar.
        opf_runner, opf_read_diagnostic = read_runner_text(
            ROOT / "opf" / "tools" / "run_all_checks.sh",
            STANDALONE_SOURCE)
        if opf_read_diagnostic is not None:
            failures.append(
                "26 cannot read standalone runner: {!r}".format(
                    opf_read_diagnostic))
        else:
            adapted, adapter_diagnostic = adapt_standalone_runner(opf_runner)
            if adapter_diagnostic is not None:
                failures.append(
                    "26 standalone runner adapter: {!r}".format(
                        adapter_diagnostic))
            else:
                adapted_diagnostics = extract_local(adapted).diagnostics
                if adapted_diagnostics:
                    failures.append(
                        "26 adapted standalone runner: {!r}".format(
                            adapted_diagnostics))
            # codex qa9 MAJOR-1: the adapter screens the RAW text, so a
            # forbidden character cannot vanish in its splitlines() and
            # newline rejoin before extract_local's own screen. A form feed
            # after a comment hides the next line from bash (the gate never
            # runs) while a split-then-rejoin would turn it into a newline
            # the grammar accepts. Fails without the raw-text screen.
            opf_form_feed = opf_runner.replace(
                '\nrun_gate "opf-homes-selftest"',
                '\n# hidden\frun_gate "opf-homes-selftest"', 1)
            if opf_form_feed == opf_runner:
                failures.append("26 standalone byte fixture drift")
            else:
                ff_adapted, ff_diagnostic = adapt_standalone_runner(
                    opf_form_feed)
                if (ff_diagnostic is None
                        or ff_diagnostic.code != "text-format"):
                    failures.append(
                        "26 form feed in the standalone runner was not "
                        "refused on the raw text")
        def mutate(*edits):
            """Apply (old, new) edits to the live runner; None if an old text is not unique."""
            mutant = live_runner
            for old, new in edits:
                if mutant.count(old) != 1:
                    return None
                mutant = mutant.replace(old, new, 1)
            return mutant

        gate_lines = [line + "\n" for line in live_runner.splitlines()
                      if line.startswith("run_gate ")]
        registered_names = {shlex.split(line, comments=True)[1] for line in gate_lines}
        registered_commands = {
            " ".join(shlex.split(line, comments=True)[3:]) for line in gate_lines}
        isolated_names = {name for scenario in _naming_scenarios(live_runner)[3:]
                          for name, status in scenario[3]}
        if isolated_names != registered_names:
            failures.append(
                "26 incomplete isolated sweep: missing={!r}, extra={!r}".format(
                    sorted(registered_names - isolated_names),
                    sorted(isolated_names - registered_names)))
        if len(gate_lines) < 3:
            # The fixtures below need three gates; each then reports drift.
            gate_lines = ["run_gate missing\n"] * 3

        def after_gate(index, body, alone=True):
            """Insert body after a gate line, if alone guarded to that gate failing alone."""
            line = gate_lines[index]
            if alone:
                name = shlex.split(line, comments=True)[1]
                body = 'if [ "$failed_names" = "{}" ]; then\n{}fi\n'.format(name, body)
            return line, line + body

        gitleaks = 'echo "--- secrets (gitleaks) ---"'
        middle = len(gate_lines) // 2
        static_fixtures = (
            ("failed reset before gitleaks",
             mutate((gitleaks, "failed=0\n" + gitleaks))),
            ("failed reset inside gitleaks NOT RUN branch",
             mutate(("  notrun=1\n", "  failed=0\n  notrun=1\n"))),
            ("failed_names reset before gitleaks",
             mutate((gitleaks, 'failed_names=""\n' + gitleaks))),
            ("failed reset inside gitleaks failure branch",
             mutate(("    gitleaks_rc=$?\n", "    failed=0\n    gitleaks_rc=$?\n"))),
            ("initial failed=0 moved below a gate",
             mutate(("\nfailed=0\n", "\n"), after_gate(0, "failed=0\n", alone=False))),
            ("initial failed=0 moved inside if [ 1 ]",
             mutate(("\nfailed=0\n", "\nif [ 1 ]; then\n  failed=0\nfi\n"))),
        ) + tuple(
            ("expansion " + spelling, mutate((gitleaks, spelling + "\n" + gitleaks)))
            for spelling in (
                "x=${failed:0:failed=0}",
                "x=$[failed=0]",
                "x=$[failed_names=0]",
                "echo ${PATH[failed=0]}",
                'x=${PATH[fai""led=0]}',
                'x=${PATH:fai""led=0:0}',
                'x=${PATH:0:fai""led=0}',
                'echo ${PATH[fai""led_names=0]}',
                "export x=${PATH:failed=0:0}",
                "[ $((failed=0)) -eq 0 ]",
                "[ $[1] -eq 1 ]",
            )
        )
        for name, mutant in static_fixtures:
            if mutant is None:
                failures.append("26 reset fixture drift: " + name)
                continue
            diagnostics = extract_local(mutant).diagnostics
            if not any(item.code == "failure-state-assignment" for item in diagnostics):
                failures.append("26 reset was not rejected: " + name)
        summary = 'if [ "$failed" -ne 0 ]; then\n'
        exit_fixtures = (
            ("stub_log mask",
             mutate((summary,
                     'if [ -z "${stub_log:-}" ]; then\n'
                     '  if [ "$failed" -ne 0 ]; then\n'
                     '    echo "RESULT: PASS"\n'
                     '    exit 0\n'
                     '  fi\n'
                     'fi\n' + summary))),
            ("exit inside another if",
             mutate(after_gate(0, "  exit 0\n"))),
            ("summary copied before gates finish",
             mutate((gitleaks, live_runner[live_runner.index(summary):] + gitleaks))),
            ("wrong terminal exit",
             mutate(("  exit 1\n", "  exit 0\n"))),
            ("gate after summary",
             live_runner + 'run_gate "late" python3 -I -B tools/check_leaks.py\n'),
        )
        for name, mutant in exit_fixtures:
            if mutant is None:
                failures.append("26 exit fixture drift: " + name)
            elif not any(item.code == "unclassified-line"
                         for item in extract_local(mutant).diagnostics):
                failures.append("26 unexpected exit was not rejected: " + name)
        # qa4/qa5 allowlist rejections: each spelling resets failure state, detects
        # the harness, or ends the run early through a line no real runner contains;
        # the exact-line allowlist must refuse every one as unclassified. The
        # arithmetic assignments (bash evaluates a value assigned to RANDOM, SRANDOM
        # or OPTIND as arithmetic), the compound subscript, the notrun reset and the
        # unset-variable expansions fail without the exact-line membership change:
        # the former pattern rules accepted them all. The masked-flag abort fixture
        # fails without the ${...:?...} rule, which screens a masked runtime-value
        # flag ahead of the MASKED_REF_EXPRESSIONS allowlist. The structural words
        # and the directory binding are position-checked: no real runner line is a
        # bare then, done or top-level else, and the binding sits at top level
        # before the first gate.
        allowlist_fixtures = (
            ("-v subscript reset in a bare test",
             mutate((gitleaks, "[ -v 'PATH[failed=0]' ]\n" + gitleaks))),
            ("-v quote-split subscript reset behind if",
             mutate((gitleaks,
                     "if [ -v 'PATH[fai\"\"led=0]' ]; then\n"
                     "echo masked\n"
                     "fi\n" + gitleaks))),
            ("unlisted binary test operand",
             mutate((gitleaks, "[ 1 -eq 'failed=0' ]\n" + gitleaks))),
            ("bare test in an allowed if shape",
             mutate((gitleaks, '[ "$failed" -ne 0 ]\n' + gitleaks))),
            ("unlisted if test form",
             mutate((gitleaks,
                     'if [ -z "${stub_log:-}" ]; then\necho masked\nfi\n'
                     + gitleaks))),
            ("set below the first gate",
             mutate((gitleaks, "set -euo pipefail\n" + gitleaks))),
            ("set inside a conditional",
             mutate((summary,
                     'if [ "$notrun" -ne 0 ]; then\nset -uo pipefail\nfi\n'
                     + summary))),
            ("abort expansion in an assignment",
             mutate((gitleaks, "x=${stub_log:?}\n" + gitleaks))),
            ("abort expansion with a message",
             mutate((gitleaks, "x=${stub_log:?harness}\n" + gitleaks))),
            ("abort expansion in an echo",
             mutate((gitleaks, 'echo "${stub_log:?}"\n' + gitleaks))),
            ("offset expansion outside the allowlist",
             mutate((gitleaks, "x=${PATH:0:1}\n" + gitleaks))),
            ("arithmetic assignment reset via RANDOM",
             mutate((gitleaks, "RANDOM=failed=0\n" + gitleaks))),
            ("arithmetic assignment reset via SRANDOM",
             mutate((gitleaks, "SRANDOM=failed=0\n" + gitleaks))),
            ("arithmetic assignment reset via OPTIND",
             mutate((gitleaks, "OPTIND=failed=0\n" + gitleaks))),
            ("exported arithmetic assignment reset",
             mutate((gitleaks, "export RANDOM=failed=0\n" + gitleaks))),
            ("compound array subscript reset",
             mutate((gitleaks, "x=([failed=0]=1)\n" + gitleaks))),
            ("harness-conditional arithmetic reset",
             mutate((gitleaks,
                     'RANDOM="failed=SECONDS>30?0:failed"\n' + gitleaks))),
            ("harness-conditional arithmetic reset inside an allowed test",
             mutate((summary,
                     summary + 'RANDOM="failed=SECONDS>30?0:failed"\nfi\n'
                     + summary))),
            ("notrun reset below a gate",
             mutate((gitleaks, "notrun=0\n" + gitleaks))),
            ("unset-variable expansion in an assignment",
             mutate((gitleaks, "x=$BASH_ENV\n" + gitleaks))),
            ("unset-variable expansion in an echo",
             mutate((gitleaks, 'echo "$stub_log"\n' + gitleaks))),
            ("abort expansion masked inside a runtime-value flag",
             mutate((gitleaks,
                     'run_gate "abort-probe" python3 -I -B tools/check_leaks.py '
                     "--base ${stub_log:?}\n" + gitleaks))),
            ("bare then outside an if line",
             mutate((gitleaks, "then\n" + gitleaks))),
            ("stray top-level else",
             mutate((gitleaks, "else\n" + gitleaks))),
            ("loop keyword outside the grammar",
             mutate((gitleaks, "done\n" + gitleaks))),
            ("directory rebinding below the first gate",
             mutate((gitleaks, 'cd "$(dirname "$0")/.." || exit 2\n' + gitleaks))),
        )
        for name, mutant in allowlist_fixtures:
            if mutant is None:
                failures.append("26 allowlist fixture drift: " + name)
            elif not any(item.code == "unclassified-line"
                         for item in extract_local(mutant).diagnostics):
                failures.append("26 allowlist did not reject: " + name)
        # The offset-carried indirect reset (y=failed=0 then x=${PATH:y:0})
        # was a disclosed lexical residual; the allowlist now refuses the offset
        # expansion shape outright.
        indirect = mutate((gitleaks, "y=failed=0\nx=${PATH:y:0}\n" + gitleaks))
        if indirect is None or not any(
                item.code == "unclassified-line"
                for item in extract_local(indirect).diagnostics):
            failures.append("26 offset-carried indirect reset was not rejected")
        # qa6 label rejections: each reported spelling smuggles an assignment or an
        # abort into the run_gate label, the channel the round-6 review found open.
        # Each fails without the GATE_LABEL_RE character-allowlist fullmatch.
        label_fixtures = (
            ("assign-default label with arithmetic carrier",
             mutate((gitleaks,
                     'run_gate "${x=failed=0}$((x))" '
                     "python3 -I -B tools/check_leaks.py\n" + gitleaks))),
            ("assign-default label with offset carrier",
             mutate((gitleaks,
                     'run_gate "${y=failed=0}${PATH:y:0}" '
                     "python3 -I -B tools/check_leaks.py\n" + gitleaks))),
            ("arithmetic notrun reset label",
             mutate((gitleaks,
                     'run_gate "$((notrun=0))" '
                     "python3 -I -B tools/check_leaks.py\n" + gitleaks))),
            ("unset-variable expansion label",
             mutate((gitleaks,
                     'run_gate "$BASH_ENV" '
                     "python3 -I -B tools/check_leaks.py\n" + gitleaks))),
            ("colonless abort expansion label",
             mutate((gitleaks,
                     'run_gate "${stub_log?}" '
                     "python3 -I -B tools/check_leaks.py\n" + gitleaks))),
            ("in-place rewrite of an existing label",
             mutate(('run_gate "qa-advisory-digest"',
                     'run_gate "qa-advisory-digest'
                     '${z=failed=failed*(SECONDS<31)}${PATH:z:0}"'))),
        )
        for name, mutant in label_fixtures:
            if mutant is None:
                failures.append("26 label fixture drift: " + name)
            elif not any(item.code == "run-gate-label"
                         for item in extract_local(mutant).diagnostics):
                failures.append("26 expanded label was not rejected: " + name)
        # qa7 label rejections: brace, glob and tilde labels expand with no $
        # in them, so the previous two-character blocklist was not
        # literal-only; the GATE_LABEL_RE fullmatch refuses each statically.
        # Each fails without the character-allowlist change.
        qa7_labels = (
            ("brace label resetting failed", '{d4,eval,"failed=0;"}'),
            ("brace label splitting into two gates", "{dashes3,true}"),
            ("brace label exiting mid-roster", "{x,exit}"),
            ("tilde label", "~"),
        )
        for name, label in qa7_labels:
            mutant = mutate((gitleaks,
                             "run_gate " + label + " python3 -I -B "
                             "tools/check_no_dashes.py\n" + gitleaks))
            if mutant is None:
                failures.append("26 label fixture drift: " + name)
            elif not any(item.code == "run-gate-label"
                         for item in extract_local(mutant).diagnostics):
                failures.append(
                    "26 non-literal label was not rejected: " + name)
        glob_label = mutate((
            'run_gate "dashes"    python3 -I -B tools/check_no_dashes.py',
            "run_gate op[f]/tools/[or]* python3 -I -B tools/check_no_dashes.py"))
        if glob_label is None:
            failures.append("26 label fixture drift: glob label")
        elif not any(item.code == "run-gate-label"
                     for item in extract_local(glob_label).diagnostics):
            failures.append("26 glob label was not rejected")
        # qa7 argument rejections: a brace or glob ARGUMENT expands the same
        # way, so GATE_WORD_RE screens every non-label word too.
        qa7_words = (
            ("brace argument",
             'run_gate "brace-args" python3 -I -B tools/check_secrets.py '
             "{--self-test,}\n"),
            ("glob argument",
             'run_gate "glob-args" python3 -I -B tools/check_secrets.py *\n'),
        )
        for name, line in qa7_words:
            mutant = mutate((gitleaks, line + gitleaks))
            if mutant is None:
                failures.append("26 word fixture drift: " + name)
            elif not any(item.code == "run-gate-word"
                         for item in extract_local(mutant).diagnostics):
                failures.append(
                    "26 expanding argument was not rejected: " + name)
        # qa7 placement rejections: the gitleaks-block lines are accepted
        # only in their real blocks; each copy below sits mid-roster, or
        # ahead of the gitleaks_rc capture, and is refused as unclassified.
        placement_fixtures = (
            ("gitleaks exit-code echo outside the failure branch",
             mutate((gitleaks,
                     'echo "GATE FAILED: secrets (gitleaks) '
                     '(exit ${gitleaks_rc})"\n' + gitleaks))),
            ("gitleaks_rc capture outside the failure branch",
             mutate((gitleaks, "gitleaks_rc=$?\n" + gitleaks))),
            ("PATH append outside the HOME guard",
             mutate((gitleaks,
                     'PATH="$PATH:$HOME/.local/bin"\n' + gitleaks))),
            ("exit-code echo ahead of the gitleaks_rc capture",
             mutate(("    gitleaks_rc=$?\n    failed=1\n",
                     '    echo "GATE FAILED: secrets (gitleaks) '
                     '(exit ${gitleaks_rc})"\n    failed=1\n'))),
        )
        for name, mutant in placement_fixtures:
            if mutant is None:
                failures.append("26 placement fixture drift: " + name)
            elif not any(item.code == "unclassified-line"
                         for item in extract_local(mutant).diagnostics):
                failures.append(
                    "26 misplaced scaffold was not rejected: " + name)
        # qa8 MAJOR-1 byte fixtures: Python's splitlines() and strip() see
        # line and word boundaries bash does not (\v, \f, \x85, U+2028,
        # U+00A0 and friends), so the static grammar formerly judged lines
        # bash never ran; one such spelling hid the NOT RUN result line at
        # runtime. The text-format character allowlist now refuses the whole
        # file; each fixture fails without it, on the runner and on the
        # workflow.
        dashes_line = (
            'run_gate "dashes"    python3 -I -B tools/check_no_dashes.py')
        byte_fixtures = (
            ("form feed as a line break (qa8 P1)", "\f",
             mutate(("\n  notrun=1", "\fnotrun=1"))),
            ("vertical tab as a line break (qa8 P1b)", "\v",
             mutate(("\n  notrun=1", "\vnotrun=1"))),
            ("U+2028 as a line break (qa8 P1c)", "\u2028",
             mutate(("\n  notrun=1", "\u2028notrun=1"))),
            ("U+0085 as a line break", "\x85",
             mutate(("\n  notrun=1", "\x85notrun=1"))),
            ("trailing no-break space (qa8 N1)", "\u00a0",
             mutate(("  notrun=1\n", "  notrun=1\u00a0\n"))),
            ("leading no-break space (qa8 N2)", "\u00a0",
             mutate(("  notrun=1\n", "  \u00a0notrun=1\n"))),
            ("gate hidden in a form-feed comment (qa8 P2)", "\f",
             mutate((dashes_line, "# note\f" + dashes_line))),
            ("failure update split by a form feed (qa8 P3)", "\f",
             mutate(("    gitleaks_rc=$?\n    failed=1\n",
                     "    gitleaks_rc=$?\ffailed=1\n"))),
        )
        for name, bad, mutant in byte_fixtures:
            if mutant is None:
                failures.append("26 byte fixture drift: " + name)
            elif not any(item.code == "text-format"
                         for item in extract_local(mutant).diagnostics):
                failures.append(
                    "26 non-ASCII runner character was not refused: " + name)
            ci_mutant = ci_fixture(common).replace(
                "name: Test", "name: Test" + bad, 1)
            if not any(item.code == "text-format"
                       for item in extract_ci(ci_mutant).diagnostics):
                failures.append(
                    "26 non-ASCII workflow character was not refused: "
                    + name)
        # qa8 MINOR-1, qa8 MINOR-2 and codex MINOR-3 branch fixtures:
        # if/else/fi state is tracked per block, so a duplicate else, an
        # empty then or else branch, the PATH append in the HOME guard's
        # else branch (where HOME is unproven) and a gitleaks_rc=$? that is
        # not the first statement of the gitleaks failure branch (where $?
        # is no longer the gitleaks exit code) are each refused. Each
        # fixture fails without the branch-state tracking.
        path_append_block = '  PATH="$PATH:$HOME/.local/bin"\nfi'
        branch_fixtures = (
            ("duplicate else in the gitleaks block",
             mutate(("  else\n    gitleaks_rc=$?",
                     "  else\n  else\n    gitleaks_rc=$?"))),
            ("empty gitleaks then branch",
             mutate(('    echo "PASS: gitleaks found no leaks"\n', ""))),
            ("empty else branch added to the HOME guard",
             mutate((path_append_block,
                     '  PATH="$PATH:$HOME/.local/bin"\nelse\nfi'))),
            ("PATH append in the HOME guard else branch",
             mutate((path_append_block,
                     '  echo\nelse\n  PATH="$PATH:$HOME/.local/bin"\nfi'))),
            ("statement ahead of the gitleaks_rc capture",
             mutate(("  else\n    gitleaks_rc=$?",
                     "  else\n    echo\n    gitleaks_rc=$?"))),
        )
        for name, mutant in branch_fixtures:
            if mutant is None:
                failures.append("26 branch fixture drift: " + name)
            elif not any(item.code == "branch-structure"
                         for item in extract_local(mutant).diagnostics):
                failures.append(
                    "26 branch structure was not rejected: " + name)
        # codex qa9 MINOR-1: bash runs a function definition as one
        # ordinary, successfully-exiting statement, so a definition inside
        # an if branch both fills the branch and replaces $?; skipped
        # uncounted, a copy of the runner's own run_gate() definition ahead
        # of gitleaks_rc=$? kept the capture "first" while bash handed it
        # the definition's status (0, never the gitleaks exit code), and a
        # definition-only branch was refused as empty although bash accepts
        # it. Both shapes are refused as function-position; each fixture
        # fails without the in-branch refusal.
        function_start = live_runner.index("run_gate() {")
        run_gate_function = live_runner[
            function_start:
            live_runner.index("\n}\n", function_start) + len("\n}\n")]
        function_fixtures = (
            ("function definition ahead of the gitleaks_rc capture",
             mutate(("    gitleaks_rc=$?\n",
                     run_gate_function + "    gitleaks_rc=$?\n"))),
            ("function definition as a branch's only statement",
             mutate(('    echo "PASS: gitleaks found no leaks"\n',
                     run_gate_function))),
        )
        for name, mutant in function_fixtures:
            if mutant is None:
                failures.append("26 function fixture drift: " + name)
            elif not any(item.code == "function-position"
                         for item in extract_local(mutant).diagnostics):
                failures.append(
                    "26 in-branch function definition was not refused: "
                    + name)
        # codex qa10 MINOR-1: bash resolves a function name at invocation
        # time, so the unchanged run_gate() definition moved BELOW the
        # first gate line leaves that gate calling an undefined name; bash
        # reports the missing command and continues, recording no gate
        # failure, while the static roster still read as complete with
        # zero diagnostics. The definition is now required before the
        # first invocation. Both fixtures fail without the order check.
        order_fixtures = (
            ("definition moved below the first gate",
             mutate((run_gate_function, ""),
                    (gate_lines[0], gate_lines[0] + run_gate_function))),
            ("definition removed entirely",
             mutate((run_gate_function, ""))),
        )
        for name, mutant in order_fixtures:
            if mutant is None:
                failures.append("26 function order fixture drift: " + name)
            elif not any(item.code == "function-order"
                         for item in extract_local(mutant).diagnostics):
                failures.append(
                    "26 run_gate invocation before its definition was not "
                    "refused: " + name)
        # claude qa9 MINOR-2 (D1): the FAILED GATES echo expands
        # ${failed_names}, so it is position-checked like the
        # gitleaks-block lines: accepted only in the then branch of the
        # terminal failed check, after the top-level initializer. Relocated
        # to top level ahead of the initializers, ${failed_names} is
        # unbound and set -u ends the runner at that line before the first
        # gate. Fails without the position check.
        relocated_echo = mutate((
            "export PYTHONDONTWRITEBYTECODE=1\n",
            "export PYTHONDONTWRITEBYTECODE=1\n"
            'echo "FAILED GATES: ${failed_names}"\n'))
        if relocated_echo is None:
            failures.append("26 relocation fixture drift: FAILED GATES echo")
        elif not any(item.code == "unclassified-line"
                     for item in extract_local(relocated_echo).diagnostics):
            failures.append("26 relocated FAILED GATES echo was not refused")
        # claude qa10 MINOR-1: the FAILED GATES echo is bound to the
        # TERMINAL failed-check frame, the one whose header sits in the
        # terminal summary tail. A copied frame mid-roster and a frame
        # nested in the gitleaks failure branch spell the same header, are
        # valid bash, and formerly hosted the echo with zero diagnostics
        # after the initializers. Both fixtures fail without the
        # terminal-frame keying.
        copied_frame = mutate((gitleaks,
                               FAILED_CHECK_LINE + "\n"
                               + "  " + FAILED_GATES_ECHO + "\n"
                               + "fi\n" + gitleaks))
        nested_frame = mutate(("    gitleaks_rc=$?\n",
                               "    gitleaks_rc=$?\n"
                               "    " + FAILED_CHECK_LINE + "\n"
                               "      " + FAILED_GATES_ECHO + "\n"
                               "    fi\n"))
        for name, mutant in (
                ("copied failed-check frame mid-roster", copied_frame),
                ("failed-check frame nested in the gitleaks failure "
                 "branch", nested_frame)):
            if mutant is None:
                failures.append(
                    "26 failed-check frame fixture drift: " + name)
            elif not any(item.code == "unclassified-line"
                         for item in extract_local(mutant).diagnostics):
                failures.append(
                    "26 FAILED GATES echo in a non-terminal failed-check "
                    "frame was not refused: " + name)
        # qa6 masked-value rejections, now behind two independent screens. In
        # the LOCAL runner the word allowlist refuses every $-carrying gate
        # word outright (run-gate-word), so a masked flag value never reaches
        # _mask_value there; those assertions fail without the character
        # allowlist. The MASKED_REF_EXPRESSIONS screen still guards the CI
        # path (normalize on a workflow run: command), where each spelling
        # assigns failure state through bash's value evaluation, aborts
        # mid-step, or expands an unreviewed variable; the normalize
        # assertions fail without that screen.
        masked_fixtures = (
            ("assign-default masked value with offset carrier",
             "python3 -I -B tools/check_version_monotonicity.py "
             '--base "${x=failed=0}${PATH:x:0}"'),
            ("two-stage masked assignment across flags",
             "python3 -I -B tools/check_leaks.py "
             '--base "${x=failed=0}" --head "$((x))"'),
            ("masked failed_names assignment",
             "python3 -I -B tools/check_leaks.py "
             '--base "${x=failed_names=0}"'),
            ("harness-conditional colon-free masked assignment",
             "python3 -I -B tools/check_leaks.py "
             '--base "${z=failed=failed*(SECONDS<31)}"'),
            ("colonless abort expansion in a masked value",
             "python3 -I -B tools/check_leaks.py "
             '--base "${stub_log?}"'),
            ("arithmetic abort in a masked value",
             "python3 -I -B tools/check_leaks.py "
             '--head "$((1/0))"'),
            ("unreviewed plain expansion in a masked value",
             "python3 -I -B tools/check_leaks.py "
             '--base "$BASH_ENV"'),
        )
        for name, command in masked_fixtures:
            tokenized = _tokenize(command)
            normalized = normalize(tokenized.value) if tokenized.ok else None
            if (normalized is None or normalized.ok
                    or normalized.code != "masked-value"):
                failures.append(
                    "26 unreviewed masked value was not rejected: " + name)
            mutant = mutate((gitleaks,
                             'run_gate "masked-probe" ' + command + "\n"
                             + gitleaks))
            if mutant is None:
                failures.append("26 masked fixture drift: " + name)
            elif not any(item.code == "run-gate-word"
                         for item in extract_local(mutant).diagnostics):
                failures.append(
                    "26 local masked flag word was not refused: " + name)
        # MINOR-2 (qa7): neither real runner passes a runtime-derived flag
        # value, so even a REVIEWED masked expression is refused in the
        # local runner by the word allowlist; the same spelling still masks
        # in the CI workflow, the only surface MASKED_REF_EXPRESSIONS
        # applies to.
        local_reviewed = mutate((gitleaks,
                                 'run_gate "masked-probe" python3 -I -B '
                                 'tools/check_leaks.py --base "$PUSH_BEFORE"\n'
                                 + gitleaks))
        if local_reviewed is None:
            failures.append(
                "26 masked fixture drift: local reviewed expression")
        elif not any(item.code == "run-gate-word"
                     for item in extract_local(local_reviewed).diagnostics):
            failures.append("26 local reviewed masked flag was not refused")
        ci_reviewed = extract_ci(ci_fixture(common + (
            'python3 -I -B tools/check_leaks.py --base "$PUSH_BEFORE"',)))
        if ci_reviewed.diagnostics:
            failures.append(
                "26 CI reviewed masked expression was refused: {!r}".format(
                    ci_reviewed.diagnostics))
        elif ("tools/check_leaks.py --base <ref:$PUSH_BEFORE>"
              not in ci_reviewed.members):
            failures.append("26 CI reviewed expression did not mask")
        # Sweep completeness is asserted on what runner_naming_problems EXECUTES
        # (through the same subprocess.run seam vector 27 patches) AND on what it
        # EVALUATES: a canary appended by the patched _scenario_problems must come
        # back in the returned problem list for every scenario, so each scenario's
        # outcome demonstrably reaches the problem check and the result of that
        # check reaches the caller. Require both the executed and the evaluated
        # set to equal the pairs the declared roster demands: a passing run and
        # gitleaks alone (fail command empty), the fixed combined gitleaks+leaks
        # scenario, and every distinct registered command failing alone.
        # Truncating or filtering the scenario list at the call site, or
        # discarding some scenarios' evaluated outcomes while still executing
        # them, turns this red.
        import subprocess
        from unittest.mock import patch
        executed = []
        real_run = subprocess.run
        real_scenario_problems = _scenario_problems

        def recording_run(argv, **kwargs):
            env = kwargs["env"]
            executed.append((env["stub_fail_command"], env["stub_gitleaks_rc"]))
            return real_run(argv, **kwargs)

        def canary_scenario_problems(scenario, outcome, *args):
            return (real_scenario_problems(scenario, outcome, *args)
                    + [("canary", scenario[1], str(scenario[2]))])

        with patch.object(sys.modules[__name__], "_scenario_problems",
                          canary_scenario_problems), \
                patch.object(subprocess, "run", recording_run):
            live_problems = runner_naming_problems(live_runner)
        evaluated = {item[1:] for item in live_problems
                     if isinstance(item, tuple) and item[:1] == ("canary",)}
        for problem in live_problems:
            if not (isinstance(problem, tuple) and problem[:1] == ("canary",)):
                failures.append("26 live runner: " + problem)
        want_executed = ({("", "0"), ("", "1"),
                          ("-I -B tools/check_leaks.py", "1")}
                         | {(command, "0") for command in registered_commands})
        if set(executed) != want_executed:
            failures.append(
                "26 executed sweep incomplete: missing={!r}, extra={!r}".format(
                    sorted(want_executed - set(executed)),
                    sorted(set(executed) - want_executed)))
        if evaluated != want_executed:
            failures.append(
                "26 evaluated sweep incomplete: missing={!r}, extra={!r}".format(
                    sorted(want_executed - evaluated),
                    sorted(evaluated - want_executed)))
        runtime_fixtures = (
            ("reset right after the first gate",
             mutate(after_gate(0, "failed=0\n", alone=False))),
            ("reset right after the last gate",
             mutate(after_gate(-1, "  failed=0\n"))),
            ("substring-length reset after the second gate",
             mutate(after_gate(1, "  x=${failed:0:failed=0}\n"))),
            ("$[ ] reset after a middle gate",
             mutate(after_gate(middle, "  x=$[failed=0]\n"))),
            ("early RESULT: PASS exit after a middle gate",
             mutate(after_gate(middle + 1, '  echo "RESULT: PASS"\n  exit 0\n'))),
            ("last gate skipped after an earlier failure",
             mutate((gate_lines[-1],
                     'if [ "$failed" -eq 0 ]; then\n' + gate_lines[-1] + "fi\n"))),
        )
        for name, mutant in runtime_fixtures:
            if mutant is None:
                failures.append("26 runtime fixture drift: " + name)
            elif not runner_naming_problems(mutant, first_only=True):
                failures.append("26 runtime fixture was not caught: " + name)
        silent_runner = live_runner
        for new, old in SILENT_RUNNER_REVERT:
            if silent_runner.count(new) != 1:
                failures.append(
                    "26 flip fixture drift: {!r} not found exactly once".format(new))
            silent_runner = silent_runner.replace(new, old, 1)
        if not runner_naming_problems(silent_runner, first_only=True):
            failures.append(
                "26 flip: a runner that names no failing gate was not caught")

    # Stub ordering (ETXTBSY): runner_naming_problems writes every executable stub once,
    # on the main thread, before its scenario pool exists, into one directory shared by
    # all five scenarios, and no scenario tree holds an executable. The red scenario's
    # in-pool chmod must be the only late write, so per-scenario stubs fail this vector.
    count += 1
    ordering = _stubs_prepared_before_pool(
        local_fixture(
            ("python3 -I -B tools/clean.py", "python3 -I -B tools/red.py"),
            ("if gitleaks dir . --no-banner; then :; else failed=1; fi",)),
        "-I -B tools/red.py")
    want = (True, 5, 1, [], [], [], [("gitleaks", "os.chmod", 1)])
    if ordering != want:
        failures.append(
            "27 executable stubs prepared before the pool: got {!r}, expected {!r}".format(
                ordering, want))

    # codex qa10 MAJOR-1: Path.read_text() translates a carriage return
    # into a newline (universal newlines) BEFORE any text screen can run,
    # so a forbidden byte planted in a runner ON DISK formerly vanished on
    # its way into the grammar: a CR-hidden gate line read as a clean
    # extra gate and the full self-test stayed green. read_runner_text
    # screens the RAW BYTES of a real file before any decode, so every
    # fixture below is written to disk and read back through the reader.
    # Each fails without the byte-level screen; the two CR fixtures also
    # fail against any decode-first reader, whose newline translation
    # erases the byte ahead of a text screen (the pinned "byte " message
    # prefix distinguishes the raw-byte screen from the text screen). The
    # reader must also hand accepted bytes back unchanged.
    count += 1
    import tempfile
    with tempfile.TemporaryDirectory(prefix="ci-parity-reader-") as tmp:
        scratch_dir = Path(tmp)
        reader_fixtures = (
            ("carriage return hiding a gate behind a comment",
             b'# hidden\rrun_gate "cr" python3 -I -B tools/a.py\n', 1),
            ("CRLF line ending", b"notrun=0\r\n", 1),
            ("form feed as a line break", b"# note\fnotrun=1\n", 1),
            ("U+2028 line separator bytes",
             ("# note" + chr(0x2028) + "notrun=1\n").encode("utf-8"), 1),
            ("non-UTF-8 byte", b"\xffnotrun=0\n", 1),
            ("NUL byte", b"notrun=0\x00\n", 1),
            ("second-line carriage return", b"notrun=0\n# x\rexit 0\n", 2),
        )
        for number, (name, data, want_line) in enumerate(reader_fixtures):
            fixture = scratch_dir / "fixture-{}".format(number)
            fixture.write_bytes(data)
            got_text, got_diagnostic = read_runner_text(
                fixture, LOCAL_SOURCE)
            if (got_text is not None
                    or got_diagnostic is None
                    or got_diagnostic.code != "text-format"
                    or got_diagnostic.line != want_line
                    or not got_diagnostic.message.startswith("byte ")):
                failures.append(
                    "28 forbidden byte on disk was not refused by the "
                    "byte-level reader: " + name)
        clean = b"#!/usr/bin/env bash\nnotrun=0\n\techo ok\n"
        fixture = scratch_dir / "clean"
        fixture.write_bytes(clean)
        got_text, got_diagnostic = read_runner_text(fixture, LOCAL_SOURCE)
        if (got_diagnostic is not None or got_text is None
                or got_text.encode("ascii") != clean):
            failures.append(
                "28 clean ASCII bytes did not round-trip unchanged "
                "through the byte-level reader")

    # codex qa10 MAJOR-1 (class closure): every read of runner or workflow
    # text must go through read_runner_text, the ONE byte-level reader.
    # This structural vector parses this file,
    # tools/selftest_git_fixture_env.py and its four registered suite
    # runners and pins EVERY read_text or
    # splitlines attribute and every text-mode open() call, as spelled, to
    # an exact per-function site count, so a new, moved or removed read
    # written through those APIs turns this vector red and forces review
    # against the reader. Every pinned site reads no runner or workflow
    # text under the parity guarantee: the sites parse Python sources for
    # ast (the F-367 maintenance-pin scan included), read harness and stub
    # call logs, write the
    # selftest report, read the config-member timeout control's pid file
    # and /proc stat, split the timeout controls' captured diagnostics,
    # split an in-memory citation string from an in-module table,
    # or split text the reader or a screen has already
    # validated. A read
    # spelled another way (getattr, an alias) is outside this tripwire, as
    # the module docstring discloses. This vector fails without the
    # change: the former Path.read_text() calls on the runners and the
    # workflow (vector 26, _registered_selftests, _roster_checks) are not
    # in this allowlist.
    count += 1
    import ast
    def _read_api_sites(tree):
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        def enclosing(node):
            scope = parents.get(node)
            while scope is not None:
                if isinstance(scope, (ast.FunctionDef,
                                      ast.AsyncFunctionDef)):
                    return scope.name
                scope = parents.get(scope)
            return "<module>"
        sites = {}
        for node in ast.walk(tree):
            kind = None
            if (isinstance(node, ast.Attribute)
                    and node.attr in ("read_text", "splitlines")):
                kind = node.attr
            elif (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "open"):
                mode = ""
                if (len(node.args) > 1
                        and isinstance(node.args[1], ast.Constant)):
                    mode = node.args[1].value
                for keyword in node.keywords:
                    if (keyword.arg == "mode"
                            and isinstance(keyword.value, ast.Constant)):
                        mode = keyword.value.value
                if not (isinstance(mode, str) and "b" in mode):
                    kind = "open-text"
            if kind is not None:
                site = (enclosing(node), kind)
                sites[site] = sites.get(site, 0) + 1
        return sites
    allowed_read_sites = {
        "tools/check_ci_parity.py": {
            ("_naming_scenarios", "splitlines"): 1,
            ("_run_runner_copy", "read_text"): 1,
            ("_run_runner_copy", "splitlines"): 2,
            ("_shadow_check", "splitlines"): 1,
            ("adapt_standalone_runner", "splitlines"): 1,
            ("extract_ci", "splitlines"): 1,
            ("extract_local", "splitlines"): 2,
            ("runner_naming_problems", "splitlines"): 1,
            ("self_test", "splitlines"): 3,
        },
        "tools/selftest_git_fixture_env.py": {
            ("_archive_reads_use_caller_env", "read_text"): 1,
            ("_binding_calls", "read_text"): 1,
            ("_caller_env_archive_only", "read_text"): 1,
            ("_calls_any", "read_text"): 1,
            # The suite's own source, parsed for ast to classify the
            # registry-lock acquisitions in _close_members: no runner
            # text. The path is _close_members.__code__.co_filename, the
            # file that defined it, so no caller input reaches the read.
            # Disclosed residual: this entry pins only the per-function
            # count of text-mode open() calls, not the path the one call
            # reads, so a reviewed count with a changed read target stays
            # inside the allowlist.
            ("_close_lock_sites", "open-text"): 1,
            ("_maintenance_pin_scan", "read_text"): 1,
            # /proc/<pid>/stat, the timeout control's liveness probe.
            ("_pid_state", "open-text"): 1,
            ("_registered_selftests", "splitlines"): 1,
            ("_require_wrapper_observed", "splitlines"): 1,
            # Not a read at all: splits an in-memory string, a
            # later-measurement citation from the in-module table (a
            # mutable dict meant to grow, not a frozen constant) padded
            # at both ends, to require exactly one nonblank line. No
            # file, runner or workflow text is opened or read at this
            # site.
            ("_roster_figures", "splitlines"): 1,
            ("_scrub_scoped_first", "read_text"): 1,
            # The suite's own source again, parsed for ast to classify the
            # kill-sweep call sites in _config_results (_sweep_call_sites,
            # the same reviewed pattern as _close_lock_sites above): no
            # runner text, and the path is _config_results' recorded
            # co_filename, so no caller input reaches the read. The same
            # disclosed residual applies: the pin is the per-function count
            # of text-mode open() calls, not the read target.
            ("_sweep_call_sites", "open-text"): 1,
            ("_system_pin_probe", "splitlines"): 1,
            ("prepare", "read_text"): 1,
            ("run", "splitlines"): 1,
        },
        "tools/selftest_git_fixture_env_core.py": {
            ("_manifest_extra_setup_failures", "read_text"): 1,
            # The member-timeout control's fixture pid file: no runner text.
            ("_member_timeout_controls", "read_text"): 1,
            # The cleanup-failure control's captured diagnostic lines: no runner text.
            ("_member_timeout_controls", "splitlines"): 1,
            ("_system_pin_checks", "read_text"): 1,
        },
        # The two poison-only lanes spell no read_text, splitlines or text-mode open() site;
        # a first one added there drifts from this empty allowlist and forces review.
        "tools/selftest_git_fixture_env_config_combined.py": {},
        "tools/selftest_git_fixture_env_config_malformed.py": {},
        "tools/selftest_git_fixture_env_config_system.py": {
            ("_opf_home_lifecycles", "read_text"): 1,
        },
    }
    for relative, allowed in sorted(allowed_read_sites.items()):
        got_sites = _read_api_sites(
            ast.parse((ROOT / relative).read_bytes().decode("utf-8")))
        if got_sites != allowed:
            failures.append(
                "29 {}: read-API sites drifted from the reviewed "
                "allowlist; route any runner or workflow read through "
                "read_runner_text and re-pin this vector: got {!r}, "
                "allowed {!r}".format(
                    relative,
                    sorted(got_sites.items()),
                    sorted(allowed.items())))

    # 30 REQUIRED STATUS (multi-job shape): the member set is the union over
    # every job, and a workflow with more than one job routes every job into
    # one always() job named Quality gates. Each red fixture passes without
    # the required-status rule (the member sets reconcile), so it fails
    # without the change.
    def multi_job_ci(aggregator, first=("python3 tools/a.py",),
                     second=("python3 tools/b.py",), run_tail=()):
        lines = ["name: Test", "jobs:"]
        for job, commands in (("one", common + first), ("two", second)):
            lines.extend((
                "  " + job + ":",
                "    name: Job " + job,
                "    runs-on: ubuntu-latest",
                "    steps:",
                "      - uses: actions/checkout@v4",
            ))
            for number, command in enumerate(commands, 1):
                lines.extend((
                    "      - name: Gate {}".format(number),
                    "        shell: bash",
                    "        run: " + command,
                ))
        lines.extend(aggregator)
        lines.extend(REQUIRED_STATUS_JOB[
            REQUIRED_STATUS_JOB.index(REQUIRED_STATUS_NEEDS_SLOT) + 1:])
        lines.extend(run_tail)
        return "\n".join(lines) + "\n"

    aggregator_head = ("  quality:", "    name: Quality gates")
    always = ("    if: always()",)
    needs_both = ("    needs:", "      - one", "      - two")
    multi_local = local_fixture(
        common + ("python3 tools/a.py", "python3 tools/b.py"))
    good_multi = multi_job_ci(aggregator_head + always + needs_both)
    case("30a every job routed into an always() Quality gates job passes",
         evaluate(multi_local, good_multi, ()), 0)
    moved = evaluate(
        multi_local,
        multi_job_ci(aggregator_head + always + needs_both,
                     first=("python3 tools/a.py", "python3 tools/b.py")),
        ())
    case("30b a member present in two jobs is counted once", moved, 0)
    if ("tools/b.py" not in moved.ci_members
            or len(moved.ci_members) != len(common) + 2):
        failures.append(
            "30b moved member was not counted exactly once: {!r}".format(
                sorted(moved.ci_members)))
    case("30c a member dropped from every job is a local-only finding",
         evaluate(multi_local,
                  multi_job_ci(aggregator_head + always + needs_both,
                               second=("python3 tools/a.py --self-test",)),
                  ()),
         1, kinds=("local-only", "ci-only"))
    case("30d a job missing from needs: is a required-status finding",
         evaluate(multi_local,
                  multi_job_ci(aggregator_head + always
                               + ("    needs:", "      - one")), ()),
         1, kinds=("required-status",))
    case("30e a Quality gates job without if: always() is a finding",
         evaluate(multi_local,
                  multi_job_ci(aggregator_head + needs_both), ()),
         1, kinds=("required-status",))
    case("30f if: !cancelled() is not always() and is a finding",
         evaluate(multi_local,
                  multi_job_ci(aggregator_head
                               + ("    if: ${{ !cancelled() }}",)
                               + needs_both), ()),
         1, kinds=("required-status",))
    case("30g no job named Quality gates is a finding",
         evaluate(multi_local,
                  multi_job_ci(("  quality:", "    name: Aggregate")
                               + always + needs_both), ()),
         1, kinds=("required-status",))
    case("30h a needs: entry naming no other job is a finding",
         evaluate(multi_local,
                  multi_job_ci(aggregator_head + always + needs_both
                               + ("      - quality",)), ()),
         1, kinds=("required-status",))
    case("30i a duplicate needs: entry is a finding",
         evaluate(multi_local,
                  multi_job_ci(aggregator_head + always + needs_both
                               + ("      - two",)), ()),
         1, kinds=("required-status",))
    case("30j a one-line flow needs: list is recognized",
         evaluate(multi_local,
                  multi_job_ci(aggregator_head + always
                               + ("    needs: [one, two]",)), ()),
         0)
    case("30k a needs: entry that is not a plain job id is cannot-evaluate",
         evaluate(multi_local,
                  multi_job_ci(aggregator_head + always + needs_both
                               + ("      - ${{ matrix.job }}",)), ()),
         2)
    case("30l exit 0 in a run block stays unclassified",
         evaluate(multi_local,
                  multi_job_ci(aggregator_head + always + needs_both,
                               run_tail=("          exit 0",)), ()),
         2)
    case("30m a one-job workflow carries no required-status rule",
         evaluate(local_fixture(common), ci_fixture(common), ()), 0)

    def diagnostic_case(name, report, want):
        case(name, report, 2)
        got = {diagnostic.code for diagnostic in report.diagnostics}
        if want not in got:
            failures.append("{}: diagnostics {}, expected {!r}".format(
                name, sorted(got), want))

    def finding_case(name, report, want_code, needle):
        case(name, report, want_code, kinds=("required-status",))
        if not any(needle in finding.message
                   for finding in report.findings
                   if finding.kind == "required-status"):
            failures.append(
                "{}: no required-status finding mentions {!r}".format(
                    name, needle))

    def mutated(text, old, new):
        if text.count(old) != 1:
            failures.append(
                "fixture edit {!r} does not match once".format(old))
        return text.replace(old, new)

    two_block = (
        "  two:\n"
        "    name: Job two\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Gate 1\n"
        "        shell: bash\n"
        "        run: python3 tools/b.py\n"
    )
    one_need = multi_job_ci(
        aggregator_head + always + ("    needs:", "      - one"))

    # 31 JOB ENUMERATION: the rule reads the job set the way YAML does or
    # refuses. Before the change the quoted and spaced keys with job two left
    # out of needs: (31a to 31c), the indent-1 line (31i) and the repeated
    # steps: key (31l) passed with exit 0; the boolean, digit-led and flow keys
    # were read as other jobs or not at all, and the shifted bodies were
    # refused under unrelated diagnostics. Each vector names the diagnostic it
    # expects, so removing any one of these rules fails its own vector. 31m
    # was a false finding: a key under a later top-level on: read as a job.
    for label, header in (
            ("31a a double-quoted job key", '  "two":'),
            ("31b a single-quoted job key", "  'two':"),
            ("31c a spaced job key", "  two :"),
            ("31d a job key YAML reads as a boolean", "  on:"),
            ("31e a job key GitHub refuses as an id", "  2two:")):
        diagnostic_case(
            label + " is cannot-evaluate, never skipped",
            evaluate(multi_local,
                     mutated(one_need, "\n  two:\n",
                             "\n" + header + "\n"),
                     ()),
            "job-key-shape")
    diagnostic_case(
        "31f a one-line flow job is cannot-evaluate",
        evaluate(multi_local,
                 mutated(one_need, two_block,
                         "  two: {name: Job two, runs-on: ubuntu-latest}\n"),
                 ()),
        "job-key-shape")
    finding_case(
        "31g control: a plain job key with a comment is read as a job",
        evaluate(multi_local,
                 mutated(one_need, "\n  two:\n", "\n  two:  # gates\n"),
                 ()),
        1, "'two' is not in the needs:")
    two_lines = two_block.split("\n")
    diagnostic_case(
        "31h a job body at indent 3 is cannot-evaluate",
        evaluate(multi_local,
                 mutated(one_need, two_block, "\n".join(
                     [two_lines[0]] + [line[1:] for line in two_lines[1:]])),
                 ()),
        "job-key-indent")
    diagnostic_case(
        "31i a line at indent 1 under jobs: is cannot-evaluate",
        evaluate(multi_local,
                 mutated(good_multi, "jobs:\n", "jobs:\n extra: x\n"),
                 ()),
        "job-key-indent")
    diagnostic_case(
        "31j a job body that starts deeper than indent 4 is cannot-evaluate",
        evaluate(multi_local,
                 mutated(one_need, two_block, "\n".join(
                     [two_lines[0]]
                     + ["  " + line if line else line
                        for line in two_lines[1:]])),
                 ()),
        "job-body-indent")
    diagnostic_case(
        "31k a repeated jobs: key is cannot-evaluate",
        evaluate(multi_local,
                 good_multi + "jobs:\n" + two_block.replace("two", "three"),
                 ()),
        "duplicate-key")
    diagnostic_case(
        "31l a repeated job-level steps: key is cannot-evaluate",
        evaluate(multi_local,
                 mutated(good_multi, two_block, two_block
                         + "    steps:\n"
                         + "      - uses: actions/checkout@v4\n"),
                 ()),
        "duplicate-key")
    case("31m a key under a later top-level on: is not read as a job",
         evaluate(multi_local, good_multi + "on:\n  push:\n", ()), 0)

    # 32 RESULT-MASKING KEYS: continue-on-error, uses, defaults and container
    # on any job are each a required-status finding whatever their value,
    # one-job workflows included. Each red fixture passed before the change.
    # The step-level siblings (continue-on-error, if:, a shell: other than
    # shell: bash, and a gate chained with || true) were already
    # cannot-evaluate through the step-key allowlist and the shell grammar;
    # 32j to 32m pin that.
    for label, line, needle in (
            ("32a continue-on-error: true", "    continue-on-error: true",
             "continue-on-error"),
            ("32b continue-on-error: false", "    continue-on-error: false",
             "continue-on-error"),
            ("32c an expression continue-on-error",
             "    continue-on-error: ${{ matrix.experimental }}",
             "continue-on-error"),
            ("32d a one-line defaults: run shell",
             "    defaults: {run: {shell: bash {0}}}", "defaults"),
            ("32e a container:", "    container: node:20", "container")):
        finding_case(
            label + " on a work job is a finding",
            evaluate(multi_local,
                     mutated(good_multi, "    name: Job two\n",
                             "    name: Job two\n" + line + "\n"),
                     ()),
            1, needle)
    finding_case(
        "32f continue-on-error on the Quality gates job is a finding",
        evaluate(multi_local,
                 multi_job_ci(aggregator_head + always
                              + ("    continue-on-error: true",)
                              + needs_both), ()),
        1, "continue-on-error")
    finding_case(
        "32g a reusable-workflow uses: job is a finding",
        evaluate(local_fixture(common + ("python3 tools/a.py",)),
                 mutated(good_multi, two_block,
                         "  two:\n"
                         "    uses: ./.github/workflows/gates.yml\n"),
                 ()),
        1, "uses")
    finding_case(
        "32h continue-on-error in a one-job workflow is a finding",
        evaluate(local_fixture(common),
                 mutated(ci_fixture(common), "    runs-on: ubuntu-latest\n",
                         "    continue-on-error: true\n"
                         "    runs-on: ubuntu-latest\n"),
                 ()),
        1, "continue-on-error")
    case("32i control: timeout-minutes and strategy on a work job pass",
         evaluate(multi_local,
                  mutated(good_multi, "    name: Job two\n",
                          "    name: Job two\n"
                          "    timeout-minutes: 30\n"
                          "    strategy:\n"
                          "      fail-fast: false\n"
                          "      matrix:\n"
                          "        os: [ubuntu-latest]\n"),
                  ()),
         0)
    for label, line in (
            ("32j step-level continue-on-error",
             "        continue-on-error: true"),
            ("32k a step-level if:", "        if: false"),
            ("32l a step-level shell:", "        shell: bash {0}")):
        diagnostic_case(
            label + " is cannot-evaluate",
            evaluate(multi_local,
                     mutated(good_multi, "        run: python3 tools/b.py\n",
                             "        run: python3 tools/b.py\n"
                             + line + "\n"),
                     ()),
            "step-key")
    case("32m a gate chained with || true is cannot-evaluate",
         evaluate(multi_local,
                  mutated(good_multi, "run: python3 tools/b.py\n",
                          "run: python3 tools/b.py || true\n"),
                  ()),
         2)

    # 33 QUALITY GATES LOGIC: the job is pinned line for line
    # (REQUIRED_STATUS_JOB), so a change that could let it exit 0 while a
    # needed result is not success is a finding. Each red fixture passed
    # before the change.
    for label, old, new in (
            ("33a a deleted exit 1", "            exit 1\n", ""),
            ("33b a success filter that strips every result",
             'rest="${rest//success/}"', 'rest="${rest//[a-z]/}"'),
            ("33c an empty result read as success",
             'rest="${RESULTS:-none}"', 'rest="${RESULTS:-success}"'),
            ("33d an inverted test", 'if [ -n "$rest" ]',
             'if [ -z "$rest" ]'),
            ("33e if: ${{ always() }}", "    if: always()\n",
             "    if: ${{ always() }}\n"),
            ("33f an added step",
             "concluded success\"\n",
             "concluded success\"\n"
             "      - uses: actions/checkout@v4\n")):
        finding_case(
            label + " in Quality gates is a finding",
            evaluate(multi_local, mutated(good_multi, old, new), ()),
            1, "REQUIRED_STATUS_JOB")
    diagnostic_case(
        "33g a step-level continue-on-error in Quality gates is "
        "cannot-evaluate",
        evaluate(multi_local,
                 mutated(good_multi,
                         "      - name: Every quality job succeeded\n",
                         "      - name: Every quality job succeeded\n"
                         "        continue-on-error: true\n"),
                 ()),
        "step-key")
    case("33h control: comments and blank lines in Quality gates pass",
         evaluate(multi_local,
                  mutated(good_multi, "    name: Quality gates\n",
                          "    name: Quality gates\n\n    # the status\n"),
                  ()),
         0)

    # 33i The pinned run: body itself, run under bash with the options
    # GitHub documents for shell: bash (bash --noprofile --norc -eo pipefail
    # {0}; here through -c, not a script file), exits 0 only when every
    # needed result is success. A body without its exit 1 passes a failure,
    # so this vector fails if the pin loses the line. The pinned body sets
    # -euo pipefail itself, so it cannot show that the launch supplies them;
    # the options vector after it runs a failing pipeline with no set line.
    import os
    import shutil
    import subprocess
    body_at = REQUIRED_STATUS_JOB.index("        run: |") + 1
    pinned_body = tuple(line[10:] for line in REQUIRED_STATUS_JOB[body_at:])
    if any(not line.startswith(" " * 10)
           for line in REQUIRED_STATUS_JOB[body_at:]):
        failures.append("33i the pinned run: body is not indented 10")
    bash = shutil.which("bash", path=os.defpath)

    def aggregate(body, results):
        env = dict(PATH=os.defpath, LC_ALL="C")
        if results is not None:
            env["RESULTS"] = results
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c",
             "\n".join(body) + "\n"],
            env=env, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=60).returncode

    if bash is None:
        failures.append("33i bash not found on " + os.defpath)
    else:
        for results, want in (
                (" ".join(("success",) * 10), 0),
                ("success", 0),
                ("failure", 1),
                ("cancelled", 1),
                ("skipped", 1),
                ("success failure", 1),
                ("success cancelled success", 1),
                ("success skipped", 1),
                ("neutral", 1),
                ("", 1),
                (None, 1)):
            count += 1
            got = aggregate(pinned_body, results)
            if got != want:
                failures.append(
                    "33i pinned body with RESULTS={!r}: exit {}, "
                    "expected {}".format(results, got, want))
        count += 1
        if aggregate(tuple(line for line in pinned_body
                           if line.strip() != "exit 1"),
                     "success failure") != 0:
            failures.append(
                "33i the deleted-exit-1 body did not pass a failure, so the "
                "scenarios above do not discriminate")
        # 33i options: a failing pipeline followed by a succeeding command,
        # with no set line of its own, exits nonzero only when the launch
        # carries both -e and -o pipefail, so dropping either option from
        # aggregate() fails this vector. The control runs the same body with
        # neither option and requires exit 0, so the body itself is not what
        # fails.
        options_body = ("false | true", "true")
        count += 1
        if aggregate(options_body, None) == 0:
            failures.append(
                "33i a failing pipeline with no set line exited 0, so the "
                "launch does not apply -e and -o pipefail")
        count += 1
        if subprocess.run(
                ["bash", "--noprofile", "--norc", "-c",
                 "\n".join(options_body) + "\n"],
                env=dict(PATH=os.defpath, LC_ALL="C"),
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=60).returncode != 0:
            failures.append(
                "33i the options body failed with neither -e nor -o "
                "pipefail, so the options vector does not discriminate")

    # 34 STEP SHELL: every run: step carries exactly one shell: bash, so a
    # workflow or job defaults.run.shell cannot choose the program a gate runs
    # under. Before the change any step-level shell: was cannot-evaluate, so a
    # pinned line could not be held at all; 34a to 34c are findings only
    # through STEP_SHELL_LINE and REQUIRED_STATUS_JOB, and 34e, 34f and 34h
    # only through the step-shell diagnostics. 34d and 34g pin refusals that
    # held before the change, so a looser shell or top-level grammar fails them.
    work_shell = "        shell: bash\n        run: python3 tools/b.py\n"
    finding_case(
        "34a a work step without shell: bash is a finding",
        evaluate(multi_local,
                 mutated(good_multi, work_shell,
                         "        run: python3 tools/b.py\n"),
                 ()),
        1, "without shell: bash")
    gates_step = "      - name: Every quality job succeeded\n"
    for needle in ("REQUIRED_STATUS_JOB", "without shell: bash"):
        finding_case(
            "34b the Quality gates step without shell: bash is a finding "
            "({})".format(needle),
            evaluate(multi_local,
                     mutated(good_multi, gates_step + "        shell: bash\n",
                             gates_step),
                     ()),
            1, needle)
    finding_case(
        "34c a block run: step without shell: bash in a one-job workflow is "
        "a finding",
        evaluate(local_fixture(common + ("python3 tools/a.py",)),
                 ci_fixture(common, ("      - name: Block",
                                     "        run: |",
                                     "          python3 tools/a.py")),
                 ()),
        1, "without shell: bash")
    for label, line in (
            ("34d1 shell: sh", "        shell: sh"),
            ("34d2 a custom shell true {0}", "        shell: true {0}"),
            ("34d3 a quoted shell: 'bash'", "        shell: 'bash'"),
            ("34d4 shell: bash -e {0}", "        shell: bash -e {0}"),
            ("34d5 shell: pwsh", "        shell: pwsh")):
        diagnostic_case(
            label + " in place of shell: bash is cannot-evaluate",
            evaluate(multi_local,
                     mutated(good_multi, work_shell,
                             line + "\n        run: python3 tools/b.py\n"),
                     ()),
            "step-key")
    diagnostic_case(
        "34e a repeated shell: bash in one step is cannot-evaluate",
        evaluate(multi_local,
                 mutated(good_multi, work_shell,
                         "        shell: bash\n" + work_shell),
                 ()),
        "duplicate-key")
    diagnostic_case(
        "34f shell: bash on a step with no run: key is cannot-evaluate",
        evaluate(local_fixture(common),
                 ci_fixture(common, ("      - uses: actions/checkout@v4",
                                     "        shell: bash")),
                 ()),
        "step-key")
    diagnostic_case(
        "34g a top-level defaults: run: shell: true {0} is cannot-evaluate",
        evaluate(multi_local,
                 mutated(good_multi, "jobs:\n",
                         "defaults:\n  run:\n    shell: true {0}\n"
                         "jobs:\n"),
                 ()),
        "unknown-top-key")
    two_steps = "    name: Job two\n    runs-on: ubuntu-latest\n    steps:\n"
    diagnostic_case(
        "34h a shell: bash line before the first step of a job is "
        "cannot-evaluate, not read into the previous job's last step",
        evaluate(multi_local,
                 mutated(good_multi, two_steps,
                         two_steps + "        shell: bash\n"),
                 ()),
        "step-key")
    case("34i control: a comment after shell: bash passes",
         evaluate(multi_local,
                  mutated(good_multi, work_shell,
                          "        shell: bash  # pinned\n"
                          "        run: python3 tools/b.py\n"),
                  ()),
         0)
    # 34j A step key before a job's first step item is cannot-evaluate (YAML
    # rejects that shape). Before the change each case passed with exit 0: a
    # run: there added its members and drew no shell: finding.
    for label, lines_in in (
            ("34j1 a run: line", "        run: python3 tools/b.py\n"),
            ("34j2 a block run: |",
             "        run: |\n          python3 tools/b.py\n"),
            ("34j3 a uses: line", "        uses: actions/checkout@v4\n"),
            ("34j4 an env: mapping", "        env:\n          A: b\n")):
        diagnostic_case(
            label + " before the first step of a job is cannot-evaluate",
            evaluate(multi_local,
                     mutated(good_multi, two_steps, two_steps + lines_in),
                     ()),
            "step-key")

    # 35 TWO WORKFLOWS: the msg-leaks.yml split (module docstring, TWO
    # WORKFLOWS and TRIGGER). Relabelled from a second 34 series so 34 above
    # names only the step-shell vectors.
    msg_scan = "python3 -I -B " + MSG_LEAKS_SCAN
    msg_selftest = msg_scan + " --self-test"
    msg_waiver = ({
        "side": "ci-only",
        "identity": MSG_LEAKS_SCAN,
        "reason": "the live scan needs the CI event",
        "backlog": "T-34",
    },)
    local_msg = local_fixture(common + (msg_selftest,))

    def with_on(text, on_block):
        return text.replace("name: Test\n", "name: Test\non:\n" + on_block, 1)

    def with_on_header(text, header):
        # Insert a raw top-level header (no on: line is added), for vectors
        # that exercise the on: header spelling itself.
        return text.replace("name: Test\n", "name: Test\n" + header, 1)

    def pr_types(types):
        return ("  pull_request:\n    branches: [main]\n    types: " + types
                + "\n  push:\n    branches: [main]\n")

    defaults = pr_types("[opened, synchronize, reopened]")
    edited = pr_types("[opened, synchronize, reopened, edited]")
    quality_side = with_on(ci_fixture(common), defaults)
    scan_text = ci_fixture((msg_selftest, msg_scan))
    msg_side = ((MSG_LEAKS_CI_SOURCE, with_on(scan_text, edited)),)

    def scan_case(name, quality, scan, want_code, kinds=(), local_only=None,
                  diagnostic=None):
        # diagnostic pins the refusal BRANCH, not just the exit code: a shape
        # two branches can refuse stays red when its own branch is disabled.
        report = evaluate(local_msg, quality, msg_waiver, scan)
        case(name, report, want_code, kinds, local_only)
        if diagnostic is not None and not any(
                item.code == "trigger-shape" and diagnostic in item.message
                for item in report.diagnostics):
            failures.append(
                "{}: no trigger-shape diagnostic holds {!r}: {!r}".format(
                    name, diagnostic, report.diagnostics))

    case("35a two workflows: the scan alone in msg-leaks.yml, with edited",
         evaluate(local_msg, quality_side, msg_waiver, msg_side), 0,
         active={("ci-only", MSG_LEAKS_SCAN)})
    scan_case("35b msg-leaks.yml dropped: the scan self-test is local-only",
              quality_side, (), 1, ("local-only", "stale-allowlist"),
              (MSG_LEAKS_SCAN + " --self-test",))
    scan_case("35c edited back on quality.yml is a trigger finding",
              with_on(ci_fixture(common), edited), msg_side, 1, ("trigger",))
    scan_case("35d msg-leaks.yml without edited is a trigger finding",
              quality_side, ((MSG_LEAKS_CI_SOURCE, with_on(scan_text, defaults)),),
              1, ("trigger",))
    scan_case("35e msg-leaks.yml with no pull_request trigger is a trigger "
              "finding", quality_side, ((MSG_LEAKS_CI_SOURCE, scan_text),),
              1, ("trigger",))
    block_list = "  pull_request:\n    types:\n      - opened\n      - synchronize\n"
    scan_case("35f a block-list edited item on quality.yml is a trigger finding",
              with_on(ci_fixture(common),
                      block_list + "      - reopened\n      - edited\n"),
              msg_side, 1, ("trigger",))
    scan_case("35g a block list without edited on quality.yml passes",
              with_on(ci_fixture(common), block_list + "      - reopened\n"),
              msg_side, 0)
    scan_case("35h a block list with all four types on msg-leaks.yml passes",
              quality_side,
              ((MSG_LEAKS_CI_SOURCE, with_on(
                  scan_text, block_list + "      - reopened\n      - edited\n")),),
              0)
    scan_case("35i edited on pull_request_target of quality.yml is a trigger "
              "finding",
              with_on(ci_fixture(common), "  pull_request_target:\n    types: [edited]\n"),
              msg_side, 1, ("trigger",))
    scan_case("35j a one-line on: list gives msg-leaks.yml default types only",
              quality_side,
              ((MSG_LEAKS_CI_SOURCE, scan_text.replace(
                  "name: Test\n", "name: Test\non: [push, pull_request]\n", 1)),),
              1, ("trigger",))
    scan_case("35k a one-line on: list on quality.yml passes (no edited)",
              ci_fixture(common).replace(
                  "name: Test\n", "name: Test\non: [push, pull_request]\n", 1),
              msg_side, 0)
    scan_case("35l the scan and edited in one workflow pass (the rule follows "
              "the scan, not a file name)",
              with_on(ci_fixture(common + (msg_selftest, msg_scan)), edited),
              (), 0)
    for label, shape in (
            ("a flow mapping", "  pull_request: {types: [edited]}\n"),
            ("a quoted event key", "  \"pull_request\":\n    types: [edited]\n"),
            ("an indent-3 line", "  pull_request:\n   types: [edited]\n"),
            ("a repeated types: key",
             "  pull_request:\n    types: [opened]\n    types: [edited]\n"),
            ("a repeated event",
             "  pull_request:\n  pull_request:\n    types: [edited]\n"),
            ("a non-name list item",
             "  pull_request:\n    types: [opened, {x: edited}]\n"),
            ("an indent-5 item under types:",
             "  pull_request:\n    types:\n     - edited\n")):
        scan_case("35m a trigger shape outside the subset is cannot-evaluate: "
                  + label, with_on(ci_fixture(common), shape), msg_side, 2)

    def without_msg_workflow(path):
        if path == MSG_LEAKS_CI_PATH:
            raise FileNotFoundError(str(path))
        return path.read_bytes()

    missing_msg = run_paths(LOCAL_PATH, CI_PATH, ALLOWLIST, without_msg_workflow)
    case("35n a missing msg-leaks.yml is cannot-evaluate, never an empty side",
         missing_msg, 2)
    count += 1
    if not any(item.source == MSG_LEAKS_CI_SOURCE and item.code == "read-error"
               for item in missing_msg.diagnostics):
        failures.append("35n the missing workflow is not named: {!r}".format(
            missing_msg.diagnostics))
    labelled = evaluate(local_msg, quality_side, msg_waiver, (
        (MSG_LEAKS_CI_SOURCE, with_on(scan_text, edited) + "bogus: 1\n"),))
    count += 1
    if not any(item.source == MSG_LEAKS_CI_SOURCE
               and item.code == "unknown-top-key"
               for item in labelled.diagnostics):
        failures.append("35o a msg-leaks.yml diagnostic does not carry its own "
                        "source: {!r}".format(labelled.diagnostics))

    # Quoted keys, whitespace before a colon, indentless sequences
    # and keyless lines under an edit event are refused (exit 2), on the
    # workflow that runs the scan and on the one that does not alike. The
    # first three spellings formerly read as a key they are not, so GitHub
    # saw a types: list this rule did not; a keyless line is refused because
    # YAML reads it as a scalar, as an explicit '?' key entry (the one
    # keyless form YAML does read as a key, exercised under 35r), or as a
    # document error, never as a plain key this rule could follow, so this
    # rule cannot know what GitHub would run there.
    edited_flow = "[opened, synchronize, reopened, edited]"
    for label, shape in (
            ("a quoted types: key",
             '  pull_request:\n    "types": ' + edited_flow + "\n"),
            ("whitespace before the types: colon",
             "  pull_request:\n    types : " + edited_flow + "\n"),
            ("whitespace before the event colon",
             "  pull_request :\n    types: " + edited_flow + "\n"),
            ("an indentless sequence under types:",
             "  pull_request:\n    types:\n    - opened\n    - synchronize\n"
             "    - reopened\n    - edited\n"),
            ("a keyless line under the event",
             "  pull_request:\n    frobnicate\n")):
        scan_case("35p quality.yml trigger shape is cannot-evaluate: " + label,
                  with_on(ci_fixture(common), shape), msg_side, 2)
        scan_case("35p msg-leaks.yml trigger shape is cannot-evaluate: "
                  + label, quality_side,
                  ((MSG_LEAKS_CI_SOURCE, with_on(scan_text, shape)),), 2)

    # Shapes an earlier strict parser still read permissively,
    # each hiding or inventing a types: list. A colon-less event key and an
    # explicit '?' key are not read as keys by this rule while YAML reads the
    # block differently; a tagged key and a colon with no space after it are
    # read by YAML as a key and a plain scalar this rule formerly classified
    # the other way around; and a non-standard indent for the on: block or an
    # event's properties hid the whole block from this rule while GitHub
    # still reads it. Each is now cannot-evaluate on both workflows.
    # Each 35r vector also requires its OWN branch's diagnostic,
    # as 35s does: the exit code alone cannot tell two refusal branches
    # apart, so a branch whose removal re-routes its shape to another refusal
    # formerly stayed green.
    for label, shape, named in (
            ("a colon-less event key at indent 2",
             "  pull_request\n    types: " + edited_flow + "\n",
             "has no colon"),
            ("the on: block's children at indent 4",
             "    pull_request:\n      types: " + edited_flow + "\n",
             "the first line under on: is at indent 4"),
            ("event properties at indent 6",
             "  pull_request:\n      branches: [main]\n      types: "
             + edited_flow + "\n",
             "outside the recognized trigger shapes"),
            ("an explicit '?' key",
             "  ? pull_request\n  : types: " + edited_flow + "\n",
             "has no colon"),
            ("a !!str tag on the types: key",
             "  pull_request:\n    !!str types: " + edited_flow + "\n",
             "key '!!str types' under pull_request is not a plain name"),
            ("types: with no space after its colon",
             "  pull_request:\n    types:" + edited_flow + "\n",
             "key 'types' under pull_request has no space after its colon")):
        scan_case("35r quality.yml trigger shape is cannot-evaluate: " + label,
                  with_on(ci_fixture(common), shape), msg_side, 2,
                  diagnostic=named)
        scan_case("35r msg-leaks.yml trigger shape is cannot-evaluate: "
                  + label, quality_side,
                  ((MSG_LEAKS_CI_SOURCE, with_on(scan_text, shape)),), 2,
                  diagnostic=named)

    # The indent-4 sequence branch is pinned on its own named
    # diagnostic. Dropping the branch still refuses the line (it has no
    # colon), so the 35p exit code alone cannot tell the two refusals apart;
    # the message can.
    indentless = evaluate(
        local_msg,
        with_on(ci_fixture(common),
                "  pull_request:\n    types:\n    - edited\n"),
        msg_waiver, msg_side)
    count += 1
    if not any(item.code == "trigger-shape"
               and "indentless sequence" in item.message
               for item in indentless.diagnostics):
        failures.append(
            "35s the indent-4 sequence item is not refused by the named "
            "indentless-sequence branch: {!r}".format(indentless.diagnostics))

    # The top-level on: header itself is
    # held to the strict key rule BEFORE its value is read: on:[...] with no
    # space after the colon formerly passed clean (YAML reads that line as
    # one plain scalar, so GitHub sees no such trigger block), and a bare on
    # raised an IndexError. Each is a trigger-shape cannot-evaluate on both
    # workflows, pinned to the header branch's own diagnostic.
    # EXACTLY SIX compatibility controls follow, three per
    # workflow. A refusal of a read spelling would turn each control's exit
    # to 2. Only the msg-leaks.yml block-spelling control ALSO pins that the
    # value stays read: a silently skipped on: header flips its exit 0 to 1
    # (its edited goes unread), while every other control reads the same
    # under a skipped header (on quality.yml the scalar and flow list read
    # as no edited either way, and the block control keeps exit 1 through
    # the msg-leaks.yml side's own missing-edited finding; on msg-leaks.yml
    # the scalar and flow list lack edited either way). So the six controls
    # establish NOT REFUSED each, and the skip discrimination rests on the
    # msg-leaks.yml block control (shown red under a skipped-header mutant).
    for label, header, named in (
            ("a flow list with no space after on:",
             "on:[pull_request]\n",
             "on: has no space after its colon"),
            ("a bare on with no colon",
             "on\n",
             "names on without a colon"),
            ("a !!str tag on the on: key",
             "!!str on:\n" + edited,
             "is not the plain spelling on"),
            ("a quoted on: key",
             '"on":\n' + edited,
             "is not the plain spelling on"),
            ("whitespace before the on: colon",
             "on :\n" + edited,
             "is not the plain spelling on"),
            ("an explicit '?' on key",
             "? on\n: [pull_request]\n",
             "names on without a colon")):
        scan_case("35t quality.yml on: header is cannot-evaluate: " + label,
                  with_on_header(ci_fixture(common), header), msg_side, 2,
                  diagnostic=named)
        scan_case("35t msg-leaks.yml on: header is cannot-evaluate: " + label,
                  quality_side,
                  ((MSG_LEAKS_CI_SOURCE, with_on_header(scan_text, header)),),
                  2, diagnostic=named)
    scan_case("35t a plain scalar on: value on quality.yml is not refused "
              "(no edited: pass)",
              with_on_header(ci_fixture(common), "on: push\n"), msg_side, 0)
    scan_case("35t a one-line flow list after on: on quality.yml is not "
              "refused (no edited: pass)",
              with_on_header(ci_fixture(common), "on: [push, pull_request]\n"),
              msg_side, 0)
    scan_case("35t a block on: value on quality.yml stays read (edited there "
              "is still a trigger finding)",
              with_on(ci_fixture(common), edited), msg_side, 1, ("trigger",))
    scan_case("35t a plain scalar on: value on msg-leaks.yml is not refused "
              "(missing edited: a trigger finding)",
              quality_side,
              ((MSG_LEAKS_CI_SOURCE, with_on_header(scan_text, "on: push\n")),),
              1, ("trigger",))
    scan_case("35t a one-line flow list after on: on msg-leaks.yml is not "
              "refused (default types lack edited: a trigger finding)",
              quality_side,
              ((MSG_LEAKS_CI_SOURCE,
                with_on_header(scan_text, "on: [push, pull_request]\n")),),
              1, ("trigger",))
    scan_case("35t a block on: value with edited on msg-leaks.yml stays read "
              "(pass)", quality_side, msg_side, 0)

    # Spellings YAML also reads as the
    # top-level on key but that the trigger rule's is_on_spelling does not
    # recognize are refused by the strict top-level parser, never read: each
    # is exit 2 END TO END with the refusing layer's own diagnostic
    # (unknown-top-key, yaml-feature or text-format) and no trigger-shape
    # diagnostic, so the trigger rule's narrower recognition opens no route
    # around the gate.
    for label, header, named_code in (
            ("a double-quoted escape spelling of on",
             "\"o\\x6e\":\n" + edited, "unknown-top-key"),
            ("an anchored on: key", "&a on:\n" + edited, "yaml-feature"),
            ("a verbatim-tag on: key",
             "!<tag:yaml.org,2002:str> on:\n" + edited, "unknown-top-key"),
            ("a multi-line explicit '?' on key",
             "?\n  on\n: [pull_request]\n", "unknown-top-key"),
            ("a byte-order mark before on:",
             "\ufeffon:\n" + edited, "text-format")):
        outer = evaluate(local_msg, with_on_header(ci_fixture(common), header),
                         msg_waiver, msg_side)
        case("35v quality.yml unrecognized on: spelling refused by the outer "
             "parser: " + label, outer, 2)
        count += 1
        if not any(item.code == named_code for item in outer.diagnostics):
            failures.append("35v {}: no {} diagnostic: {!r}".format(
                label, named_code, outer.diagnostics))
        if any(item.code == "trigger-shape" for item in outer.diagnostics):
            failures.append("35v {}: refused by the trigger rule instead of "
                            "the outer parser: {!r}".format(
                                label, outer.diagnostics))

    # The indent-2 on-key refusal branches are discriminated by
    # their own diagnostics (dropping one re-routes its shape to another
    # refusal branch, so the exit code alone stayed green).
    for label, shape, named in (
            ("a !!str tag on the event key",
             "  !!str pull_request:\n    types: " + edited_flow + "\n",
             "on: key '!!str pull_request' is not a plain name"),
            ("an event key with no space after its colon",
             "  pull_request:{types: [edited]}\n",
             "on: key 'pull_request' has no space after its colon"),
            ("a quoted event key",
             '  "pull_request":\n    types: ' + edited_flow + "\n",
             "quoted on: key"),
            ("whitespace before the event colon",
             "  pull_request :\n    types: " + edited_flow + "\n",
             "on: key 'pull_request ' holds whitespace before its colon")):
        scan_case("35u quality.yml indent-2 on: key is cannot-evaluate: "
                  + label, with_on(ci_fixture(common), shape), msg_side, 2,
                  diagnostic=named)
        scan_case("35u msg-leaks.yml indent-2 on: key is cannot-evaluate: "
                  + label, quality_side,
                  ((MSG_LEAKS_CI_SOURCE, with_on(scan_text, shape)),), 2,
                  diagnostic=named)

    # A gate moved from quality.yml into
    # msg-leaks.yml is a relocation finding (the CI side is the union of both
    # files, so set parity alone would stay green while the aggregated
    # Quality gates status silently lost the gate); the scan itself and its
    # self-test are the allowed members, which 35a already holds green.
    moved = "python3 -I -B tools/check_git_option_table.py"
    scan_case("35q a gate moved from quality.yml into msg-leaks.yml is a "
              "relocation finding",
              with_on(ci_fixture(tuple(
                  item for item in common if item != moved)), defaults),
              ((MSG_LEAKS_CI_SOURCE, with_on(
                  ci_fixture((msg_selftest, msg_scan, moved)), edited)),),
              1, ("relocation",))

    # The step-shell rule is pinned on msg-leaks.yml by its own
    # vector. ci_fixture writes shell: bash into every generated command
    # step (not its checkout step or appended extra_steps), so before this
    # vector no case removed the line on the msg-leaks.yml side, and a
    # mutant applying _step_shell_findings to quality.yml alone passed every
    # vector. The findings must be exactly the one step-shell finding naming
    # msg-leaks.yml, so an extra finding of another kind cannot satisfy this
    # vector either.
    shell_dropped = evaluate(
        local_msg, quality_side, msg_waiver,
        ((MSG_LEAKS_CI_SOURCE, with_on(
            scan_text.replace("        " + STEP_SHELL_LINE + "\n", "", 1),
            edited)),))
    case("35w a msg-leaks.yml run step without shell: bash is a finding",
         shell_dropped, 1, ("required-status",))
    count += 1
    expected_shell = [
        finding for finding in shell_dropped.findings
        if finding.kind == "required-status"
        and MSG_LEAKS_CI_SOURCE in finding.message
        and "without " + STEP_SHELL_LINE in finding.message]
    if len(shell_dropped.findings) != 1 or len(expected_shell) != 1:
        failures.append(
            "35w expected exactly one step-shell finding naming {}: "
            "{!r}".format(MSG_LEAKS_CI_SOURCE, shell_dropped.findings))

    # A ${{ }} GitHub expression
    # anywhere in the RAW run: value is cannot-evaluate (workflow-expression),
    # read before shell-comment stripping: GitHub substitutes expressions
    # into the script text before the shell runs, comments included, so a
    # substituted value carrying a newline leaves the comment. Before the
    # change the three comment shapes below each passed with exit 0.
    pr_title = "${{ github.event.pull_request.title }}"
    for label, block_lines, on_quality in (
            ("a whole-line comment in a run: block",
             ("          # " + pr_title, "          echo ok"), True),
            ("a trailing comment on a run: block line",
             ("          echo ok # " + pr_title,), True),
            ("a comment-only run: block on msg-leaks.yml",
             ("          # " + pr_title,), False)):
        expr_step = ("      - name: Expression carrier",
                     "        " + STEP_SHELL_LINE,
                     "        run: |") + block_lines
        if on_quality:
            expr_report = evaluate(
                local_msg, with_on(ci_fixture(common, expr_step), defaults),
                msg_waiver, msg_side)
            expr_source = CI_SOURCE
        else:
            expr_report = evaluate(
                local_msg, quality_side, msg_waiver,
                ((MSG_LEAKS_CI_SOURCE, with_on(
                    ci_fixture((msg_selftest, msg_scan), expr_step),
                    edited)),))
            expr_source = MSG_LEAKS_CI_SOURCE
        case("35x a GitHub expression in a run: shell comment is "
             "cannot-evaluate: " + label, expr_report, 2)
        count += 1
        if not any(item.source == expr_source
                   and item.code == "workflow-expression"
                   for item in expr_report.diagnostics):
            failures.append(
                "35x {}: no workflow-expression diagnostic from {}: "
                "{!r}".format(label, expr_source, expr_report.diagnostics))
    inline_expr = evaluate(
        local_msg,
        with_on(ci_fixture(common + ("echo " + pr_title,)), defaults),
        msg_waiver, msg_side)
    case("35x control: an inline run: expression stays refused", inline_expr,
         2)
    count += 1
    if not any(item.code == "workflow-expression"
               for item in inline_expr.diagnostics):
        failures.append(
            "35x control: no workflow-expression diagnostic: {!r}".format(
                inline_expr.diagnostics))

    # A <ref:...> placeholder is
    # the OUTPUT of the masking; a workflow value is screened like any other,
    # so a placeholder spelling carrying $ is cannot-evaluate (masked-value),
    # never masked, and a quoted <ref:...> literal never collapses into the
    # masked identity of the reviewed expression it names. Before the change
    # both spellings below reconciled with exit 0 against the waiver,
    # byte-identical to the identity the reviewed "$PUSH_BEFORE" masks to,
    # while handing the gate a different byte string at runtime. The reviewed
    # allowlist identities are the one place a placeholder is still accepted
    # back (_validate_allowlist normalizes with masked_ok=True); the control
    # pins that path together with the reviewed CI spelling.
    leak_waiver = ({
        "side": "ci-only",
        "identity": "tools/check_leaks.py --base <ref:$PUSH_BEFORE>",
        "reason": "fixture baseline: the scan reads the push base in CI",
        "backlog": "T-34",
    },)
    for label, spelling in (
            ("a double-quoted placeholder literal",
             "--base \"<ref:$PUSH_BEFORE>\""),
            ("a single-quoted placeholder literal",
             "--base '<ref:$PUSH_BEFORE>'")):
        spoofed = evaluate(
            local_fixture(common),
            ci_fixture(common + (
                "python3 -I -B tools/check_leaks.py " + spelling,)),
            leak_waiver)
        case("35y a literal placeholder flag value is cannot-evaluate: "
             + label, spoofed, 2)
        count += 1
        if not any(item.code == "masked-value"
                   for item in spoofed.diagnostics):
            failures.append(
                "35y {}: no masked-value diagnostic: {!r}".format(
                    label, spoofed.diagnostics))
    case("35y control: the reviewed expression still masks and reconciles "
         "with the placeholder waiver",
         evaluate(
             local_fixture(common),
             ci_fixture(common + (
                 "python3 -I -B tools/check_leaks.py "
                 "--base \"$PUSH_BEFORE\"",)),
             leak_waiver),
         0, active={(
             "ci-only", "tools/check_leaks.py --base <ref:$PUSH_BEFORE>")})

    # The equals form
    # --flag=value runs through its own branch of normalize, which the
    # space-form 35y vectors never reach; a mutant passing masked_ok=True
    # there accepted a placeholder literal while the whole 35 series stayed
    # green. The two negatives and the reviewed-expression positive pin that
    # branch.
    for label, spelling in (
            ("a double-quoted equals-form placeholder literal",
             "--base=\"<ref:$PUSH_BEFORE>\""),
            ("a single-quoted equals-form placeholder literal",
             "--base='<ref:$PUSH_BEFORE>'")):
        spoofed = evaluate(
            local_fixture(common),
            ci_fixture(common + (
                "python3 -I -B tools/check_leaks.py " + spelling,)),
            leak_waiver)
        case("35y an equals-form literal placeholder flag value is "
             "cannot-evaluate: " + label, spoofed, 2)
        count += 1
        if not any(item.code == "masked-value"
                   for item in spoofed.diagnostics):
            failures.append(
                "35y {}: no masked-value diagnostic: {!r}".format(
                    label, spoofed.diagnostics))
    case("35y control: the reviewed expression in equals form masks and "
         "reconciles with the placeholder waiver",
         evaluate(
             local_fixture(common),
             ci_fixture(common + (
                 "python3 -I -B tools/check_leaks.py "
                 "--base=\"$PUSH_BEFORE\"",)),
             leak_waiver),
         0, active={(
             "ci-only", "tools/check_leaks.py --base <ref:$PUSH_BEFORE>")})

    # A masked identity is
    # granted only when the value's RAW workflow spelling, before shell
    # de-quoting, is byte-for-byte the reviewed double-quoted spelling. shlex
    # removes quotes and escaping backslashes, so before the change every
    # spelling below de-quoted into the reviewed $PUSH_BEFORE, collapsed into
    # the waived masked identity and reconciled with exit 0, while bash hands
    # the gate different bytes at runtime (a literal $PUSH_BEFORE for the
    # escaped and single-quoted spellings, an expansion of $PUSH_ for the
    # backslash-split name, and an expansion of $PUSH for the other split
    # ones). Each is now cannot-evaluate (masked-spelling), in space
    # and equals form; the 35y controls keep the reviewed spelling passing
    # in both forms.
    for label, spelling in (
            ("an escaped dollar", "\\$PUSH_BEFORE"),
            ("a backslash-split name", "$PUSH_\\BEFORE"),
            ("adjacent double-quoted parts", "\"$PUSH\"\"_BEFORE\""),
            ("a double-quoted part with a bare tail", "\"$PUSH\"_BEFORE"),
            ("an escaped underscore", "$PUSH\\_BEFORE"),
            ("a single-quoted dollar with a bare tail", "'$'PUSH_BEFORE"),
            ("a single-quoted spelling", "'$PUSH_BEFORE'")):
        for form, flag_arg in (("space", "--base " + spelling),
                               ("equals", "--base=" + spelling)):
            unreviewed = evaluate(
                local_fixture(common),
                ci_fixture(common + (
                    "python3 -I -B tools/check_leaks.py " + flag_arg,)),
                leak_waiver)
            case("35z an unreviewed raw spelling of a reviewed expression "
                 "is cannot-evaluate ({} form): {}".format(form, label),
                 unreviewed, 2)
            count += 1
            if not any(item.code == "masked-spelling"
                       for item in unreviewed.diagnostics):
                failures.append(
                    "35z {} ({} form): no masked-spelling diagnostic: "
                    "{!r}".format(label, form, unreviewed.diagnostics))

    # The raw-spelling screen
    # fails closed where no raw spelling reached it: normalize drops a
    # raws list whose length disagrees with the token list, and
    # _mask_value refuses a reviewed runtime value whose raw spelling is
    # None. The 35z vectors above always hand normalize a recovered raw
    # spelling, so a mutant granting the mask when raw is None (raw is
    # not None and raw != ...) kept the whole suite green; these
    # negatives pin the missing-raws and mismatched-raws refusals in
    # both flag forms, and the disagreeing-walk vector pins that
    # _raw_lexemes returns None rather than raws another tokenization
    # produced.
    # The shorter list alone
    # caught a removed length guard only through an IndexError, so a
    # longer list carrying the reviewed spelling at the index of the flag
    # now pins the guard through its assertion; the reviewed list of the
    # same length is the control that earns the mask, and an IndexError
    # is recorded as a failure instead of ending the run.
    for form, raw_tokens in (
            ("space", ["python3", "tools/check_leaks.py", "--base",
                       "$PUSH_BEFORE"]),
            ("equals", ["python3", "tools/check_leaks.py",
                        "--base=$PUSH_BEFORE"])):
        # The reviewed workflow spelling double-quotes the flag value.
        flag, equals, value = raw_tokens[-1].rpartition("=")
        reviewed = raw_tokens[:-1] + [flag + equals + "\"" + value + "\""]
        granted = normalize(raw_tokens, raws=reviewed)
        count += 1
        if (not granted.ok or granted.value
                != "tools/check_leaks.py --base <ref:" + value + ">"):
            failures.append(
                "35z normalize control ({} form): the reviewed raw "
                "spelling must earn the masked identity, got ok={} "
                "code={!r}".format(form, granted.ok, granted.code))
        for label, raws in (
                ("no raws given", None),
                ("a shorter raws list", []),
                ("a longer raws list carrying the reviewed spelling",
                 reviewed + ["--self-test"])):
            count += 1
            try:
                refused = normalize(raw_tokens, raws=raws)
            except IndexError as exc:
                failures.append(
                    "35z normalize ({} form, {}): indexed past the raws "
                    "list ({}); the length guard must drop a mismatched "
                    "list".format(form, label, exc))
                continue
            if refused.ok or refused.code != "masked-spelling":
                failures.append(
                    "35z normalize without raw evidence ({} form, {}): "
                    "expected a masked-spelling refusal, got ok={} "
                    "code={!r}".format(form, label, refused.ok,
                                       refused.code))
    count += 1
    if _raw_lexemes("--base $PUSH_BEFORE",
                    ["--base", "DIVERGENT"]) is not None:
        failures.append(
            "35z _raw_lexemes: a walk that cannot reproduce the de-quoted "
            "token list must return None, never raws from another "
            "tokenization")

    # The raw ${{ }} screen
    # reads the run: block scalar as YAML does, from its content indentation
    # (the first non-empty line) to its end (the first non-empty line below
    # that indentation), not from a fixed threshold of 8. A dedented YAML
    # comment after the block is outside the scalar (before the change it
    # was refused at any indent above 8); a comment inside the scalar is
    # script text wherever the content indentation sits; and a dedented line
    # that re-enters the step is a layout YAML rejects, refused by the outer
    # loop, never passed.
    def block_step(block_lines, indicator="|"):
        return (("      - name: Block carrier",
                 "        " + STEP_SHELL_LINE,
                 "        run: " + indicator) + block_lines)

    dedented_comment = evaluate(
        local_msg,
        with_on(ci_fixture(common, block_step(
            ("            echo ok", "          # " + pr_title))), defaults),
        msg_waiver, msg_side)
    case("36 a YAML comment dedented below the run: block content is "
         "outside the scalar (pass)", dedented_comment, 0)
    count += 1
    if any(item.code == "workflow-expression"
           for item in dedented_comment.diagnostics):
        failures.append(
            "36 dedented comment: unexpected workflow-expression "
            "diagnostic: {!r}".format(dedented_comment.diagnostics))
    for label, block_lines in (
            ("a comment at the content indentation of 12",
             ("            echo ok", "            # " + pr_title)),
            ("a comment at the content indentation of 9",
             ("         echo ok", "         # " + pr_title))):
        inside = evaluate(
            local_msg,
            with_on(ci_fixture(common, block_step(block_lines)), defaults),
            msg_waiver, msg_side)
        case("36 a comment inside the scalar carries the expression: "
             + label, inside, 2)
        count += 1
        if not any(item.code == "workflow-expression"
                   for item in inside.diagnostics):
            failures.append(
                "36 {}: no workflow-expression diagnostic: {!r}".format(
                    label, inside.diagnostics))
    reentry = evaluate(
        local_msg,
        with_on(ci_fixture(common, block_step(
            ("            echo ok", "          # a dedented comment",
             "            echo again"))), defaults),
        msg_waiver, msg_side)
    case("36 content after a dedented comment is a layout YAML rejects, "
         "refused", reentry, 2)
    count += 1
    if not any(item.code == "step-structure"
               for item in reentry.diagnostics):
        failures.append(
            "36 dedented re-entry: no step-structure diagnostic: "
            "{!r}".format(reentry.diagnostics))

    # YAML rejects a leading
    # blank line in a literal block scalar carrying more spaces than the
    # first content line (the auto-detected content indentation), so the
    # reader refuses that layout (scalar-blank-indent) instead of
    # skipping the blank and passing text YAML does not parse; before
    # the change each refused vector below passed with exit 0. A leading
    # blank at or below the content indentation, and every blank after
    # the first content line, stays inside the scalar unchanged.
    for label, indicator, blank in (
            ("a | scalar, blank one space past the content indentation",
             "|", " " * 13),
            ("a |- scalar, blank two spaces past the content "
             "indentation", "|-", " " * 14),
            ("a |+ scalar, blank far past the content indentation",
             "|+", " " * 20)):
        over = evaluate(
            local_msg,
            with_on(ci_fixture(common, block_step(
                (blank, "            echo ok"), indicator)), defaults),
            msg_waiver, msg_side)
        case("37 an over-indented leading blank in a run: block is a "
             "layout YAML rejects, refused: " + label, over, 2)
        count += 1
        if not any(item.code == "scalar-blank-indent"
                   for item in over.diagnostics):
            failures.append(
                "37 {}: no scalar-blank-indent diagnostic: {!r}".format(
                    label, over.diagnostics))
    for label, indicator, blank in (
            ("a | scalar, blank exactly at the content indentation",
             "|", " " * 12),
            ("a |- scalar, blank one space below the content "
             "indentation", "|-", " " * 11),
            ("a |+ scalar, an empty leading line", "|+", "")):
        at_or_below = evaluate(
            local_msg,
            with_on(ci_fixture(common, block_step(
                (blank, "            echo ok"), indicator)), defaults),
            msg_waiver, msg_side)
        case("37 control: a leading blank at or below the content "
             "indentation stays inside the scalar: " + label,
             at_or_below, 0)
        count += 1
        if any(item.code == "scalar-blank-indent"
               for item in at_or_below.diagnostics):
            failures.append(
                "37 control {}: unexpected scalar-blank-indent "
                "diagnostic: {!r}".format(
                    label, at_or_below.diagnostics))

    # The reader holds the
    # deepest leading blank, so the order of the leading blanks never
    # decides the outcome. Each vector below puts a blank past the content
    # indentation and a blank at column 0 ahead of the first content line,
    # one order each; before these vectors a mutant keeping only the first
    # leading blank passed the second order, and one keeping only the last
    # passed the first, each with the suite green.
    for label, blanks in (
            ("the deeper blank first", (" " * 13, "")),
            ("the deeper blank last", ("", " " * 13))):
        two_blanks = evaluate(
            local_msg,
            with_on(ci_fixture(common, block_step(
                blanks + ("            echo ok",))), defaults),
            msg_waiver, msg_side)
        case("37 two leading blanks, one past the content indentation, are "
             "refused in either order: " + label, two_blanks, 2)
        count += 1
        if not any(item.code == "scalar-blank-indent"
                   for item in two_blanks.diagnostics):
            failures.append(
                "37 two leading blanks ({}): no scalar-blank-indent "
                "diagnostic: {!r}".format(label, two_blanks.diagnostics))

    # YAML keeps a blank after
    # the first content line inside the scalar however deep it is, so the
    # reader judges leading blanks only; this control holds a later blank
    # indented past the content indentation accepted.
    later_blank = evaluate(
        local_msg,
        with_on(ci_fixture(common, block_step(
            ("            echo ok", " " * 20, "            echo again"))),
            defaults),
        msg_waiver, msg_side)
    case("37 control: a blank after the first content line, indented past "
         "the content indentation, stays inside the scalar", later_blank, 0)
    count += 1
    if any(item.code == "scalar-blank-indent"
           for item in later_blank.diagnostics):
        failures.append(
            "37 control later blank: unexpected scalar-blank-indent "
            "diagnostic: {!r}".format(later_blank.diagnostics))

    # The YAML feature screen
    # reads an anchor or alias spelling only after whitespace, a colon
    # or a hyphen, so it refuses the space-separated inline *name and
    # &name flag spellings, while the equals form bypasses it:
    # --base=*main survives extraction as a kept literal that diverges
    # (exit 1, no extraction diagnostic), and --base=&main splits on the
    # shell control character into an empty flag value, refused
    # (exit 2). The residue states each outcome; these vectors pin them.
    for label, flag_arg, wanted in (
            ("a space-separated alias spelling", "--base *main",
             "yaml-feature"),
            ("a space-separated anchor spelling", "--base &main",
             "yaml-feature"),
            ("an equals-form anchor spelling", "--base=&main",
             "runtime-value")):
        screened = evaluate(
            local_fixture(common),
            ci_fixture(common + (
                "python3 -I -B tools/check_leaks.py " + flag_arg,)),
            leak_waiver)
        case("38 an inline anchor or alias flag spelling is refused: "
             + label, screened, 2)
        count += 1
        if not any(item.code == wanted
                   for item in screened.diagnostics):
            failures.append(
                "38 {}: no {} diagnostic: {!r}".format(
                    label, wanted, screened.diagnostics))
    glob_literal = evaluate(
        local_fixture(common),
        ci_fixture(common + (
            "python3 -I -B tools/check_leaks.py --base=*main",)),
        ())
    case("38 an equals-form glob flag value stays a kept literal that "
         "diverges", glob_literal, 1, kinds=("ci-only",),
         ci_only={"tools/check_leaks.py --base *main"})
    count += 1
    if glob_literal.diagnostics:
        failures.append(
            "38 equals-form glob literal: unexpected diagnostics: "
            "{!r}".format(glob_literal.diagnostics))

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1

    print(
        "SELF-TEST PASS: {} deterministic CI-parity vectors, "
        "including fail-without-the-change, passed".format(count)
    )
    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description=(
            "Compare local and GitHub Actions quality-gate members."
        )
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run deterministic in-memory self-tests",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(
        sys.argv[1:] if argv is None else argv)
    if args.self_test:
        return self_test()

    report = run_paths(LOCAL_PATH, CI_PATH, extra_ci_paths=CI_WORKFLOWS[1:])
    print(render(report))
    return report.code


if __name__ == "__main__":
    raise SystemExit(main())
