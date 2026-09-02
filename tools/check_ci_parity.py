#!/usr/bin/env python3
"""Check token-level parity between the local and CI quality-gate rosters.

The gate reads tools/run_all_checks.sh and .github/workflows/quality.yml as data. It
does not execute either file. Both paths are absolute and derived from this file's
resolved repository root.

Identity is the normalized command, including all script arguments. Python and shell
launcher words are removed. The recognized interpreter-only flags -I, -B, -E, -s,
-P, and -u, including glued forms such as -IB, do not affect identity. The value of a
runtime-derived flag (--base, --protected, --head) is masked as <ref> only when it
carries a shell expansion or a GitHub expression; a literal value stays in identity, so
two different literals diverge. Every other argument remains order-preserving and
identity-relevant. Duplicates collapse because comparison is set-based.

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
top-level or job-level workflow key, a top-level unconditional exit that would strand
later local gates, unbalanced if/fi nesting in the runner, job content without a job
mapping, and a run_gate() dispatcher body outside its recognized shape.
Deeper nested non-gate YAML (under on:, env:, with:, or strategy:) is structurally
recognized but not exhaustively schema-validated; the shadow scan still prevents a
tools/ gate from hiding there. Reachability is outside token-parity scope: a gate is
counted as declared regardless of an enclosing conditional, whether a job-level if:, a
shell conditional inside a run: block, or an if/fi in the local runner, that could keep
it from running; the comparison is of declared rosters, not reachable ones. Because a
runtime-derived value is masked to <ref>, a change among runtime spellings, including
one that makes two masked refs identical (a self-comparison), is not distinguished;
validating a single gate's own argument semantics is that gate's responsibility, not
this one's. The shadow scan has one soft edge: exotic quoting outside the supported
grammar could hide a tools/ string from comment stripping.
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
        "identity": "tools/check_version_monotonicity.py --base <ref>",
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
            "tools/check_branch_root.py --protected <ref> --head <ref> --max-lag 200"
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
        "identity": "tools/check_branch_root.py --protected <ref> --max-lag 200",
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
})

INTERPRETERS = frozenset({"python", "python3"})
SHELLS = frozenset({"bash", "sh"})

# The run_gate() dispatcher body carries no gate invocations, so its lines are not
# extracted; but it is validated against this exact expected shape rather than skipped
# blindly, so malformed or unexpected function-body shell is cannot-evaluate, not a pass.
EXPECTED_RUN_GATE_BODY = (
    'local name="$1"; shift',
    'echo "--- ${name} ---"',
    'if "$@"; then :; else failed=1; fi',
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

TOOL_RE = re.compile(r"\btools/[A-Za-z0-9_.-]+\.(?:py|sh)\b")
PY_TARGET_RE = re.compile(r"^tools/[A-Za-z0-9_.-]+\.py$")
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
        if (not PY_TARGET_RE.fullmatch(target)
                or ".." in target.split("/")):
            return Result(
                False,
                None,
                "gate-target",
                "unsupported gate target {!r}; expected a literal "
                "tools/*.py path or gitleaks dir".format(target),
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
            canonical.extend(
                (token, "<ref>" if _is_runtime_value(value) else value))
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
            canonical.extend(
                (matched_flag, "<ref>" if _is_runtime_value(value) else value))
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

    in_function = False
    function_body = []
    if_depth = 0
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

        # Track if/fi nesting so a bare exit is scaffold only inside a conditional
        # block; a top-level unconditional exit makes later gates unreachable.
        if stripped == "fi":
            if_depth = max(0, if_depth - 1)
        elif stripped.endswith("; then"):
            if_depth += 1

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

        if tokens and tokens[0] == "run_gate":
            if len(tokens) < 3:
                diagnostics.append(_diagnostic(
                    source,
                    line_number,
                    "run-gate-shape",
                    "run_gate needs a label and command",
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

        scaffold = False
        if stripped in ("set -uo pipefail", "set -euo pipefail"):
            scaffold = True
        elif stripped == 'cd "$(dirname "$0")/.." || exit 2':
            scaffold = True
        elif (tokens and tokens[0] == "export"
                and len(tokens) == 2
                and ASSIGN_RE.fullmatch(tokens[1])):
            scaffold = not _contains_unsafe_substitution(tokens[1])
        elif len(tokens) == 1 and ASSIGN_RE.fullmatch(tokens[0]):
            scaffold = not _contains_unsafe_substitution(tokens[0])
        elif tokens and tokens[0] == "echo":
            scaffold = (
                _safe_simple(tokens)
                and not _contains_unsafe_substitution(stripped)
            )
        elif stripped in ("else", "fi", "then"):
            scaffold = True
        elif re.fullmatch(r"exit [0-9]+", stripped) and if_depth > 0:
            scaffold = True
        elif (tokens and tokens[0] == "if"
                and len(tokens) >= 4
                and tokens[-2:] == [";", "then"]):
            if (tokens[1] == "["
                    and _safe_simple(tokens[:-2])
                    and not _contains_unsafe_substitution(stripped)):
                scaffold = True
            elif stripped in (
                'if ! command -v gitleaks >/dev/null 2>&1 && '
                '[ -n "${HOME:-}" ] && '
                '[ -x "$HOME/.local/bin/gitleaks" ]; then',
                'if command -v gitleaks >/dev/null 2>&1; then',
            ):
                scaffold = True
        elif (tokens and tokens[0] == "["
                and _safe_simple(tokens)
                and not _contains_unsafe_substitution(stripped)):
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

    if if_depth != 0:
        diagnostics.append(_diagnostic(
            source,
            0,
            "unbalanced-if",
            "unbalanced if/fi nesting; the shell file is not well-formed",
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


def self_test():
    failures = []
    count = 0
    common = (
        "python3 -I -B tools/check_ci_parity.py --self-test",
        "python3 -I -B tools/check_ci_parity.py",
    )

    def local_fixture(commands=(), tail=()):
        lines = [
            "#!/usr/bin/env bash",
            "set -uo pipefail",
            "failed=0",
            "run_gate() {",
            '  local name="$1"; shift',
            '  echo "--- ${name} ---"',
            '  if "$@"; then :; else failed=1; fi',
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
        '  echo "PASS"',
        "else",
        "  failed=1",
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
                "tools/check_version_monotonicity.py --base <ref>"
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
                "tools/check_branch_root.py --protected <ref> "
                "--head <ref> --max-lag 200"
            ),
            "reason": "fixture baseline",
            "backlog": "T-2",
        },
        {
            "side": "ci-only",
            "identity": (
                "tools/check_branch_root.py --protected <ref> --max-lag 200"
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
        "run_gate() {",
        '  local name="$1"; shift',
        '  echo "--- ${name} ---"',
        '  eval "$INJECT"',
        '  if "$@"; then :; else failed=1; fi',
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
