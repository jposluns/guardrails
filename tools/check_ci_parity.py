#!/usr/bin/env python3
"""Check token-level parity between the local and CI quality-gate rosters.

The gate reads tools/run_all_checks.sh and .github/workflows/quality.yml as data. It
does not execute either file. Both paths are absolute and derived from this file's
resolved repository root. The --self-test alone also runs a scratch copy of the runner
under bash, with stub python3 and gitleaks commands, to prove a failing gate is named
(GATE FAILED: <name> (exit <n>)) and listed (FAILED GATES: ...) before RESULT: FAIL.

Identity is the normalized command, including all script arguments. Python and shell
launcher words are removed. The recognized interpreter-only flags -I, -B, -E, -s,
-P, and -u, including glued forms such as -IB, do not affect identity. The value of a
runtime-derived flag (--base, --protected, --head) is masked as <ref:expression>,
preserving which expression supplied it, only in the CI workflow and only when it
carries a shell expansion that is byte-for-byte one of the reviewed
MASKED_REF_EXPRESSIONS (any other expansion there is cannot-evaluate, never masked),
so a change among runtime spellings (including one making two refs identical, a
self-comparison) diverges; a literal value stays in identity, so two different
literals diverge. In the local runner every run_gate word is held to a character
allowlist that admits no expansion at all, so a runtime-derived flag value there is
refused, never masked. A ${{ }} GitHub expression inside a
gate command is cannot-evaluate, not masked. Every other argument remains
order-preserving and identity-relevant. Duplicates collapse because comparison is
set-based.

Exit convention:
  0  extraction succeeded, every difference has an active exception, no exception is
     stale or reversed, and both mandatory self-members are present
  1  an evaluated parity finding, stale or reversed exception, or missing mandatory
     self-member
  2  cannot-evaluate, including an unreadable input, malformed control input, empty
     extraction, unsupported shell or YAML shape, unresolved command value, hidden
     action, circular runner invocation, or shadow-scan discrepancy

DISCLOSED RESIDUAL. This is token-level set parity only. It does not compare
environment values, operating systems, tool versions, execution order, multiplicity,
step placement across jobs, labels, or whether the shell harness propagates a child
failure. Interpreter-flag differences are intentionally removed from identity and are
owned by check_python_launcher_isolation.py. Scope is exactly the two named files, so
gates in other workflows are not enumerated. Argument order is identity-relevant,
making a harmless reorder fail loud. Coordinated removal of both of this gate's own
invocations cannot be detected if nobody runs the remaining file manually. The
extractors implement a disclosed shell and YAML subset; an unknown construct is
cannot-evaluate rather than a clean pass. Fail-closed cases include an unknown
top-level or job-level workflow key, an exit outside the exact terminal summary
blocks or the top-level directory-binding line before the first gate, unbalanced
if/fi nesting in the runner, job content without a job mapping, a run_gate()
dispatcher body outside its recognized shape, a run_gate word (label or argument)
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
inside an open if block. Every other assignment, export or echo line must be
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
The runner's failure-state rules are lexical and shape-based: they validate each
line against the allowlist, not reachability or runtime values. Every run_gate
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
masked. A reviewed workflow expression still resolves only at runtime: its
variable can be empty (the flag then receives an empty value) or unset (each
reviewed step's run block sets -u, so the step then ends early with a non-zero
status); token-level parity sees neither. The ${...:?...} abort-expansion rule
still screens every runner line first, so a masked ${...:?...} abort spelling
is refused twice over; a self-test fixture pins that rule's own diagnostic and
fails without it. The gitleaks-block lines (the PATH append, the gitleaks_rc
capture and the gitleaks exit-code echo) are accepted only inside their real
blocks, as notrun=0, set and cd already are, so a copy placed mid-roster
cannot end the runner early under set -u through an unbound HOME or
gitleaks_rc. What the grammar accepts and still cannot see into is exactly two
surfaces: the content of a gate script the runner invokes, and the environment
the runner inherits, which can change what an accepted literal line resolves
to at runtime.
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
import argparse
import re
import shlex
import sys
from collections import namedtuple
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_PATH = ROOT / "tools" / "run_all_checks.sh"
CI_PATH = ROOT / ".github" / "workflows" / "quality.yml"
LOCAL_SOURCE = "tools/run_all_checks.sh"
CI_SOURCE = ".github/workflows/quality.yml"

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
MASKED_REF_EXPRESSIONS = frozenset({
    "origin/${GITHUB_REF_NAME}",
    "origin/${GITHUB_BASE_REF}",
    "$PUSH_BEFORE",
    "$PR_HEAD_SHA",
})

# One repository-local, non-shipped gate; no general .github executable allowance.
REPO_GATE = ".github/check_newtab_contract.py"

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
    r"(?:(?:opf/)?tools/[A-Za-z0-9_.-]+\.(?:py|sh)|" + re.escape(REPO_GATE) + r")\b"
)
PY_TARGET_RE = re.compile(r"^(?:opf/)?tools/[A-Za-z0-9_.-]+\.py$")
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
YAML_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*.*$")

Result = namedtuple("Result", "ok value code message")
Diagnostic = namedtuple("Diagnostic", "source line code message")
Extraction = namedtuple("Extraction", "members origins diagnostics")
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


def _operator(token):
    return bool(token) and all(char in "|&;<>()`" for char in token)


def _interpreter_flag(token):
    """Recognize only the disclosed, valueless interpreter flag set."""
    return bool(re.fullmatch(r"-[IBEsPu]+", token))


def _is_runtime_value(value):
    """A runtime-derived flag value carries a shell expansion or a GitHub expression.
    A literal value has neither and stays in identity, so two different literals diverge."""
    return "$" in value or "`" in value


def _mask_value(value):
    """Canonicalize a runtime-value-flag value as a Result. A runtime-derived value is
    masked to <ref:expression>, preserving which expression supplied it so a change
    among runtime spellings (including a self-comparison) diverges; a literal stays in
    identity. The expression must be byte-for-byte one of MASKED_REF_EXPRESSIONS: bash
    can assign or abort inside an expansion, so an unreviewed expression is refused,
    never masked. An already-masked placeholder whose expression is reviewed passes
    through unchanged so the form is idempotent, which the allowlist canonical-identity
    check relies on."""
    if value.startswith("<ref:") and value.endswith(">"):
        expression = value[len("<ref:"):-1]
        if expression not in MASKED_REF_EXPRESSIONS:
            return Result(
                False,
                None,
                "masked-value",
                "masked expression {!r} is outside the reviewed "
                "MASKED_REF_EXPRESSIONS allowlist".format(expression),
            )
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
        return Result(True, "<ref:" + value + ">", "", "")
    return Result(True, value, "", "")


def normalize(tokens):
    """Return a canonical gate identity or a cannot-evaluate Result."""
    tokens = list(tokens)
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
        if (target != REPO_GATE and not PY_TARGET_RE.fullmatch(target)
                or ".." in target.split("/")):
            return Result(
                False,
                None,
                "gate-target",
                "unsupported gate target {!r}; expected a literal "
                "tools/*.py path, {} or gitleaks dir".format(target, REPO_GATE),
            )
        canonical = [target]

    args = tokens[index:]
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
            masked = _mask_value(value)
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
            masked = _mask_value(value)
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
# user-local gitleaks directory to PATH; extract_local records its depth so
# the PATH append is accepted nowhere else.
HOME_GUARD_LINE = (
    'if ! command -v gitleaks >/dev/null 2>&1 && '
    '[ -n "${HOME:-}" ] && '
    '[ -x "$HOME/.local/bin/gitleaks" ]; then')

RUNNER_TEST_LINES = frozenset({
    'if [ "$failed" -ne 0 ]; then',
    'if [ "$notrun" -ne 0 ]; then',
    HOME_GUARD_LINE,
    'if command -v gitleaks >/dev/null 2>&1; then',
})

# The gitleaks-block lines are position-checked the way notrun=0 and cd
# already are, never accepted by bare membership: each is accepted only
# inside its real block. Placed anywhere else, the PATH append outside the
# HOME guard and the exit-code echo without a prior gitleaks_rc capture each
# end the runner mid-roster under set -u through an unbound variable, and a
# stray gitleaks_rc=$? captures an unrelated exit status.
PATH_APPEND_LINE = 'PATH="$PATH:$HOME/.local/bin"'
GITLEAKS_RC_LINE = "gitleaks_rc=$?"
GITLEAKS_FAILED_ECHO = (
    'echo "GATE FAILED: secrets (gitleaks) (exit ${gitleaks_rc})"')

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
# GITLEAKS_FAILED_ECHO) are deliberately NOT members here: extract_local
# accepts each of them only inside its real block.
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
    'echo "FAILED GATES: ${failed_names}"',
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
    if "\x00" in text or "\r" in text:
        diagnostics.append(_diagnostic(
            source,
            0,
            "text-format",
            "input contains NUL or carriage-return bytes",
        ))

    code_lines = [(number, _strip_comment(raw).strip())
                  for number, raw in enumerate(text.splitlines(), 1)]
    executable = [(number, code) for number, code in code_lines if code]
    terminal_exits = set()
    for summary in TERMINAL_SUMMARIES:
        tail = executable[-len(summary):]
        if tuple(code for number, code in tail) == summary:
            terminal_exits.update(number for number, code in tail
                                  if code.startswith("exit "))

    in_function = False
    function_body = []
    if_depth = 0
    if_unbalanced = False
    failure_initializers = {
        "failed": "failed=0",
        "failed_names": 'failed_names=""',
    }
    initialized = set()
    gitleaks_depth = None
    gitleaks_else = False
    gitleaks_updates = set()
    gitleaks_rc_set = False
    home_guard_depth = None
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
            in_function = True
            function_body = []
            continue

        # Track nesting for failure-state updates and top-level summary blocks.
        # A fi with no open if underflows: record it rather than clamp it away.
        if stripped == "fi":
            if if_depth == gitleaks_depth:
                gitleaks_depth = None
                gitleaks_else = False
            if if_depth == home_guard_depth:
                home_guard_depth = None
            if if_depth == 0:
                if_unbalanced = True
            else:
                if_depth -= 1
        elif stripped.endswith("; then"):
            if_depth += 1
            if stripped == HOME_GUARD_LINE:
                home_guard_depth = if_depth
        elif stripped == "else" and if_depth == gitleaks_depth:
            gitleaks_else = True

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
            gitleaks_depth = if_depth
            gitleaks_else = False
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
                if_depth == 0 and not members and variable not in initialized
                and stripped == failure_initializers[variable]
            )
            gitleaks_update = (
                if_depth == gitleaks_depth and gitleaks_else
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
            scaffold = if_depth == 0 and not members
        elif stripped == 'cd "$(dirname "$0")/.." || exit 2':
            # Only where the real runner puts it: top level, before any gate. A
            # second binding mid-roster would move the remaining gates to the
            # parent of the repository root when $0 is relative.
            scaffold = if_depth == 0 and not members
        elif stripped == "notrun=0":
            # Only where the real runner puts it: top level, before any gate. A
            # later notrun=0 would clear the NOT RUN state and hide its disclaimer.
            scaffold = if_depth == 0 and not members
        elif stripped == PATH_APPEND_LINE:
            # Only where the real runner puts it: inside the HOME guard, which
            # has just proven HOME non-empty. Outside that guard $HOME may be
            # unset, and set -u then ends the runner mid-roster.
            scaffold = (home_guard_depth is not None
                        and if_depth == home_guard_depth)
        elif stripped == GITLEAKS_RC_LINE:
            # Only where the real runner puts it: the gitleaks failure branch,
            # where $? is the gitleaks exit code. Anywhere else the line
            # captures an unrelated status and vouches for a variable the
            # exit-code echo below then trusts.
            scaffold = if_depth == gitleaks_depth and gitleaks_else
            if scaffold:
                gitleaks_rc_set = True
        elif stripped == GITLEAKS_FAILED_ECHO:
            # Only after gitleaks_rc is captured in the same failure branch.
            # Anywhere else ${gitleaks_rc} is unbound, and set -u then ends
            # the runner mid-roster, losing the remaining gates and the
            # FAILED GATES list.
            scaffold = (if_depth == gitleaks_depth and gitleaks_else
                        and gitleaks_rc_set)
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
            # Only inside an open if block, where the real runners put it. A bare
            # then or done is not a line either real runner contains, so neither
            # is accepted anywhere.
            scaffold = if_depth > 0
        elif line_number in terminal_exits and if_depth == 1:
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

    if if_depth != 0 or if_unbalanced:
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
        normalized = normalize(tokens)
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


def extract_ci(text):
    """Extract normalized members from the supported quality.yml subset."""
    source = CI_SOURCE
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
    if "\x00" in text or "\r" in text:
        diagnostics.append(_diagnostic(
            source,
            0,
            "text-format",
            "input contains NUL or carriage-return bytes",
        ))

    lines = text.splitlines()
    in_jobs = False
    in_steps = False
    current_mapping = None
    current_job = None
    saw_jobs = False
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

        if stripped == "jobs:" and indent == 0:
            in_jobs = True
            saw_jobs = True
            in_steps = False
            index += 1
            continue

        if indent == 0:
            top_key = stripped.split(":", 1)[0] if ":" in stripped else stripped
            if top_key not in TOP_LEVEL_KEYS:
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

        if (in_jobs
                and indent == 2
                and re.fullmatch(
                    r"[A-Za-z0-9_-]+:", stripped)):
            in_steps = False
            current_mapping = None
            current_job = stripped
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

        if in_jobs and indent == 4 and stripped == "steps:":
            in_steps = True
            current_mapping = None
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
            index += 1
            continue

        if not in_steps:
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

            if stripped in ("env:", "with:"):
                current_mapping = stripped[:-1]
                index += 1
                continue

            if stripped.startswith("uses:"):
                _classify_uses(
                    stripped[5:], source, line_number, diagnostics)
                index += 1
                continue

            if stripped.startswith("run:"):
                value = stripped[4:].strip()

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

                        body_code = _strip_comment(body_raw)
                        if not body_code.strip():
                            index += 1
                            continue

                        body_indent = (
                            len(body_code)
                            - len(body_code.lstrip(" "))
                        )
                        if body_indent <= 8:
                            break

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

    diagnostics.extend(_shadow_check(text, source, origins))
    return Extraction(
        frozenset(members),
        origins,
        _dedupe_diagnostics(diagnostics),
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
                normalized = normalize(identity_tokens)
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


def evaluate(local_text, ci_text, allowlist=ALLOWLIST):
    return reconcile(
        extract_local(local_text),
        extract_ci(ci_text),
        allowlist,
    )


def _read_utf8(path, reader):
    try:
        data = reader(path)
    except OSError as exc:
        return Result(
            False,
            None,
            "read-error",
            "cannot read {} ({})".format(
                path, type(exc).__name__),
        )

    if not isinstance(data, bytes):
        return Result(
            False,
            None,
            "read-type",
            "reader for {} did not return bytes".format(path),
        )

    try:
        return Result(True, data.decode("utf-8"), "", "")
    except UnicodeDecodeError:
        return Result(
            False,
            None,
            "utf8",
            "{} is not UTF-8".format(path),
        )


def run_paths(
        local_path,
        ci_path,
        allowlist=ALLOWLIST,
        reader=None):
    """Read both required absolute paths and return a structured Report."""
    if reader is None:
        reader = lambda path: path.read_bytes()

    diagnostics = []
    texts = []

    for source, path in (
        (LOCAL_SOURCE, local_path),
        (CI_SOURCE, ci_path),
    ):
        if not isinstance(path, Path) or not path.is_absolute():
            diagnostics.append(_diagnostic(
                source,
                0,
                "absolute-path",
                "input path must be absolute",
            ))
            texts.append(None)
            continue

        result = _read_utf8(path, reader)
        if result.ok:
            texts.append(result.value)
        else:
            diagnostics.append(_diagnostic(
                source,
                0,
                result.code,
                result.message,
            ))
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
        texts[0], texts[1], validated_allowlist)


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

    repo_command = "python3 -I -B " + REPO_GATE
    case("01b repository gate matched",
         evaluate(local_fixture(common + (repo_command,)),
                  ci_fixture(common + (repo_command,)), ()), 0)
    case("01c repository gate local-only",
         evaluate(local_fixture(common + (repo_command,)), ci_fixture(common), ()),
         1, ("local-only",), (REPO_GATE,), ())
    case("01d repository gate ci-only",
         evaluate(local_fixture(common), ci_fixture(common + (repo_command,)), ()),
         1, ("ci-only",), (), (REPO_GATE,))
    case("01e repository gate shadow scan",
         evaluate(local_fixture(common, ("echo " + REPO_GATE,)), ci_fixture(common), ()),
         2)
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

    # Both rosters carry the ci-parity mandatory members but omit the git-option-table pair, so the only
    # findings are missing-mandatory for those two (rosters are otherwise matched, no local/ci-only drift).
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
    try:
        live_runner = LOCAL_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        failures.append("26 cannot read live runner: {!r}".format(exc))
    else:
        live_extraction = extract_local(live_runner)
        if live_extraction.diagnostics:
            failures.append("26 live runner: {!r}".format(live_extraction.diagnostics))
        # The standalone OPF runner, adapted exactly as
        # tools/selftest_git_fixture_env.py adapts it (validated directory
        # binding and terminal exit 0 removed, $here rebased), must also give
        # zero diagnostics under the same grammar.
        try:
            opf_runner = (
                ROOT / "opf" / "tools" / "run_all_checks.sh"
            ).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(
                "26 cannot read standalone runner: {!r}".format(exc))
        else:
            opf_binding = (
                'here="$(cd "$(dirname "$0")" && pwd)" || exit 2')
            opf_lines = opf_runner.splitlines()
            if opf_lines.count(opf_binding) != 1 or opf_lines[-1] != "exit 0":
                failures.append("26 standalone runner scaffold drift")
            else:
                opf_lines[opf_lines.index(opf_binding)] = "# validated binding"
                opf_lines[-1] = "# validated terminal exit"
                adapted = "\n".join(opf_lines).replace(
                    '"$here/', '"opf/tools/')
                adapted_diagnostics = extract_local(adapted).diagnostics
                if adapted_diagnostics:
                    failures.append(
                        "26 adapted standalone runner: {!r}".format(
                            adapted_diagnostics))
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

    report = run_paths(LOCAL_PATH, CI_PATH)
    print(render(report))
    return report.code


if __name__ == "__main__":
    raise SystemExit(main())
