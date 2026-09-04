#!/usr/bin/env python3
"""Derive-don't-hardcode gate for adopter-declared command surfaces. Offline, stdlib only, fail-closed.

A reusable command file (a slash-command template, a resume script, a handoff snippet) that hardcodes a
target-specific parameter such as a repository owner/name into a command carries an unsound input: reused
in a different checkout the command's own logic runs correctly against the WRONG target (grdinp), and the
retained literal was never re-verified against the concrete target (exetgt). This gate flags a LITERAL
value at an adopter-declared command sink so the adopter derives it at runtime instead.

The gate is ADOPTER-PARAMETERIZED: it ships NO command, option, repository name, or path of its own. The
adopter authors `.aiqt/derived-command-parameters.toml`, declaring which command surfaces to scan, which
command word and option name form each sink, and which reference variables count as a derived value. With
NO configuration the gate prints NOT APPLICABLE and exits clean; a present but unusable configuration
never does (fail-closed). This keeps the portable pack free of any project's own identifiers.

WHAT IT PROVES, AND WHAT IT DOES NOT. The gate claims only that a declared sink carries a non-literal
value (a reference to an allowed variable), not that the variable resolves at runtime to the correct
target. Proving the live value points at the intended checkout is exetgt's runtime confirmation, outside a
static scanner. Class c (partial): configured paths, command and option forms, wrappers, aliases, dynamic
construction, command substitution, and shell grammar this scanner does not model all bound its coverage;
the residue names that missed subset. To dogfood grdinp, the gate reconciles its own control inputs
against the real source before yielding a verdict: a command or option selector that matches nothing in
the scanned surface, a duplicate selector, or an overlapping path selector is a cannot-evaluate that fails
closed (exit 2), never a silent clean pass. Backslash-newline continuations are joined so a split sink is
seen whole, an unquoted word-start `#` comment is dropped, detection is anchored to the real command-word
position (a configured name used as an argument or in a comment is not mistaken for an invocation), and
bundled short-option clusters are matched.

Configuration schema (`.aiqt/derived-command-parameters.toml`), all fields REQUIRED and adopter-authored:

  format-version = 1                       the exact integer 1 (True and 1.0 do not pass)

  [[binding]]
  id = "repository-target"                 a stable, unique kebab-case identifier for this sink
  paths = ["path/to/file", "dir/**"]        repo-relative command surfaces: an exact file, or a directory
                                            prefix ending in /** (every readable regular file beneath it);
                                            each selector must match at least one readable regular file
  commands = ["sample-tool"]                command words whose option values are checked (basename match)
  options = ["--target"]                    the option names whose value must not be a literal
  reference-variables = ["CURRENT_TARGET"]  the variable names an allowed $VAR / ${VAR} reference may name

  check_derived_command_parameters.py               scan the surfaces the configuration declares
  check_derived_command_parameters.py --self-test   deterministic self-test (in-memory + a tempdir layer)

Exit convention (matches the repo's gates):
  0  clean, or a printed NOT APPLICABLE (no configuration present)
  1  a real finding (a literal value, or a missing value, at a declared sink)
  2  malformed or unreadable control input (a bad configuration, an escaping or unmatched path, a symlink
     or unreadable or non-UTF-8 declared surface), fail-closed: an input the gate cannot read never reads
     as clean.
"""
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_derived_command_parameters.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

CONFIG_REL = ".aiqt/derived-command-parameters.toml"
ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")           # a kebab-case binding identifier
VAR_REF_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")  # a $VAR or ${VAR} reference (anchored below)
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")       # a NAME=value assignment preceding a command word
BINDING_KEYS = frozenset(("id", "paths", "commands", "options", "reference-variables"))
_SEPARATORS = frozenset(("|", "||", "&&", "&", ";", "(", ")", "|&"))


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Caught at run() and reported as exit 2
    (fail-closed): an unreadable configuration or declared surface is never treated as clean."""


# --- pure logic (always run in --self-test): tokenization and value classification -------------------

def split_tokens(line):
    """Whitespace-split one line into tokens, keeping a single- or double-quoted run (quotes retained) as
    one token so a quoted value stays intact. DELIBERATELY TOTAL: an unclosed quote takes the rest of the
    line as one token rather than raising, so a prose apostrophe or an unbalanced quote in a declared file
    can never crash the scan (there is no unparseable-line branch; the scan is best-effort by design, per
    the residue). This is not a shell parser: it does not expand, and it groups only on whitespace, so a
    separator with no surrounding space stays inside its token. An unquoted, word-start `#` begins a comment
    that runs to end of line and is dropped, so a commented-out sink is not scanned; a `#` inside a token
    (a#b) or inside quotes ('#') is not a comment and stays in its token."""
    tokens = []
    i, n = 0, len(line)
    while i < n:
        if line[i].isspace():
            i += 1
            continue
        if line[i] == "#":  # an unquoted, word-start comment: the rest of the line is dropped
            break
        buf = []
        while i < n and not line[i].isspace():
            ch = line[i]
            if ch in "'\"":
                close = line.find(ch, i + 1)
                if close == -1:  # unclosed quote: take the rest of the line, do not raise
                    buf.append(line[i:])
                    i = n
                    break
                buf.append(line[i:close + 1])
                i = close + 1
            else:
                buf.append(ch)
                i += 1
        tokens.append("".join(buf))
    return tokens


def command_basename(token):
    """The basename of a command token, so an absolute or relative path to the tool still resolves
    (/usr/bin/sample-tool -> sample-tool). A quoted token is not a bare command word, so it returns ''."""
    if not token or token[0] in "'\"":
        return ""
    return token.rsplit("/", 1)[-1]


def classify_value(token, ref_vars):
    """Classify an option's value token as 'reference' (a derived, allowed variable reference) or
    'literal'. A bare $VAR / ${VAR}, or the SAME inside double quotes (shell still expands it), whose name
    is in ref_vars is a reference; everything else is a literal, including a single-quoted '$VAR' (the
    shell does not expand it), a bare owner/repo string, and a command substitution $(...) (v1 does not
    credit it, disclosed in the residue)."""
    inner = token
    if len(inner) >= 2 and inner[0] == '"' and inner[-1] == '"':
        inner = inner[1:-1]
    match = VAR_REF_RE.fullmatch(inner)
    if match and match.group(1) in ref_vars:
        return "reference"
    return "literal"


def match_option(tok, option):
    """Match one argument token against one declared option, returning ('separated', None) when the value
    is the NEXT token, ('attached', value) when the value is carried in the same token, or None for no
    match. A LONG option (--name) matches tok == '--name' (separated) or '--name=value' (attached). A SHORT
    option (a dash and a single character, -x) matches that letter inside a single-dash cluster (-x, -ax,
    -axVALUE): the letters before it are other short flags, and whatever FOLLOWS it in the cluster is its
    attached value, or, when it is the last letter, the value is the next token (shell short-option-with-
    argument semantics). A token beginning with '--' is never read as a short cluster. This handles bundled
    short options so a declared option inside a cluster is not slipped past (best-effort class c: it does
    not resolve which earlier cluster letters themselves take a value)."""
    if len(option) == 2 and option[0] == "-" and option[1] != "-":
        if not tok.startswith("-") or tok.startswith("--"):
            return None
        pos = tok.find(option[1], 1)
        if pos == -1:
            return None
        remainder = tok[pos + 1:]
        return ("attached", remainder) if remainder else ("separated", None)
    if tok == option:
        return ("separated", None)
    if tok.startswith(option + "="):
        return ("attached", tok[len(option) + 1:])
    return None


def scan_line(line, commands, options, ref_vars, telemetry=None):
    """Scan one logical line for a declared sink and return a list of (option, kind) findings, kind in
    {'literal', 'missing'}. Detection is ANCHORED to the real command-word position: a token is a command
    word only at the start of the line or immediately after a shell separator, after skipping any leading
    NAME=value assignments, so a configured command name appearing as an ARGUMENT (echo sample-tool ...) or
    inside a comment is not mistaken for an invocation. When the command word's basename is configured, the
    tokens AFTER it up to the next shell separator are its argument window; a configured option in that
    window, in separated (--opt value), attached (--opt=value), or bundled short (-xt value) form, has its
    value classified. A reference produces no finding; a literal or a missing/empty value does. Multiple
    command occurrences on one line are each scanned. When telemetry is given, the command basenames and
    option names actually matched are recorded into it, so run() can reconcile the config's selectors
    against the real source (grdinp). Best-effort (class c): the argument window is lexical, not a real
    parse of pipelines or grouping, and a wrapper (sudo/env) in command-word position is not unwrapped."""
    findings = []
    tokens = split_tokens(line)
    n = len(tokens)
    idx = 0
    at_command_word = True  # the start of the line is a command-word position
    while idx < n:
        tok = tokens[idx]
        if tok in _SEPARATORS:
            at_command_word = True
            idx += 1
            continue
        if not at_command_word:
            idx += 1
            continue
        if ENV_ASSIGN_RE.match(tok):
            idx += 1  # a leading NAME=value assignment precedes the command word
            continue
        at_command_word = False
        base = command_basename(tok)
        if base not in commands:
            idx += 1
            continue
        if telemetry is not None:
            telemetry["commands"].add(base)
        j = idx + 1
        while j < n and tokens[j] not in _SEPARATORS:
            matched_option = None
            form = value = None
            for option in options:
                match = match_option(tokens[j], option)
                if match is not None:
                    matched_option = option
                    form, value = match
                    break
            if matched_option is None:
                j += 1
                continue
            if telemetry is not None:
                telemetry["options"].add(matched_option)
            if form == "attached":
                if value == "":
                    findings.append((matched_option, "missing"))
                elif classify_value(value, ref_vars) == "literal":
                    findings.append((matched_option, "literal"))
                j += 1
                continue
            # separated form: the value is the next token, unless it is absent, a separator, or itself an
            # option-like token (a missing value for this option).
            if j + 1 >= n or tokens[j + 1] in _SEPARATORS or tokens[j + 1].startswith("-"):
                findings.append((matched_option, "missing"))
                j += 1
                continue
            if classify_value(tokens[j + 1], ref_vars) == "literal":
                findings.append((matched_option, "literal"))
            j += 2
        idx = j
    return findings


# --- configuration and surface loading ---------------------------------------------------------------

def _req_str_list(table, key, where, allow_empty=False):
    value = table.get(key)
    if not isinstance(value, list) or (not value and not allow_empty):
        raise GateError("{}: {!r} must be a non-empty array of strings".format(where, key))
    seen = set()
    for element in value:
        if not isinstance(element, str) or not element.strip():
            raise GateError("{}: {!r} contains a non-string or empty element".format(where, key))
        # A duplicate selector is a malformed control (grdinp): it double-counts a match and cannot be
        # reconciled soundly against the source, so fail closed rather than silently dedup and pass.
        if element in seen:
            raise GateError("{}: {!r} contains a duplicate element {!r}".format(where, key, element))
        seen.add(element)
    return value


def load_config(path):
    """Parse and fully validate the adopter configuration into a list of binding dicts. Fail-closed
    (GateError -> exit 2) on any malformed input: an unreadable or unparseable file, an unknown top-level
    key, a version that is not the exact integer 1, no bindings, a binding that is not a table, an unknown
    or missing binding field, an empty roster, a malformed id, or a duplicate id. An absent file is the
    caller's NOT APPLICABLE case, handled before this loader."""
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise GateError("cannot read the configuration {} ({})".format(CONFIG_REL, exc))
    except tomllib.TOMLDecodeError as exc:
        raise GateError("configuration {} does not parse ({})".format(CONFIG_REL, exc))
    extra = set(data) - {"format-version", "binding"}
    if extra:
        raise GateError("configuration {}: unknown top-level key(s): {}".format(
            CONFIG_REL, ", ".join(sorted(extra))))
    version = data.get("format-version")
    if version is not True and version == 1 and isinstance(version, int):
        pass  # the exact integer 1
    else:
        raise GateError("configuration {}: format-version must be the exact integer 1".format(CONFIG_REL))
    bindings = data.get("binding")
    if not isinstance(bindings, list) or not bindings:
        raise GateError("configuration {}: at least one [[binding]] is required".format(CONFIG_REL))
    seen_ids = set()
    out = []
    for entry in bindings:
        if not isinstance(entry, dict):
            raise GateError("configuration {}: every [[binding]] must be a table".format(CONFIG_REL))
        missing = BINDING_KEYS - set(entry)
        if missing:
            raise GateError("configuration {}: a [[binding]] is missing key(s): {}".format(
                CONFIG_REL, ", ".join(sorted(missing))))
        unknown = set(entry) - BINDING_KEYS
        if unknown:
            raise GateError("configuration {}: a [[binding]] has unknown key(s): {}".format(
                CONFIG_REL, ", ".join(sorted(unknown))))
        bid = entry.get("id")
        if not isinstance(bid, str) or not ID_RE.match(bid):
            raise GateError("configuration {}: binding id {!r} is not a kebab-case identifier".format(
                CONFIG_REL, bid))
        if bid in seen_ids:
            raise GateError("configuration {}: duplicate binding id {!r}".format(CONFIG_REL, bid))
        seen_ids.add(bid)
        where = "configuration {} binding {!r}".format(CONFIG_REL, bid)
        out.append({
            "id": bid,
            "paths": _req_str_list(entry, "paths", where),
            "commands": _req_str_list(entry, "commands", where),
            "options": _req_str_list(entry, "options", where),
            "reference-variables": _req_str_list(entry, "reference-variables", where),
        })
    return out


def resolve_surface(root, selector):
    """Resolve one path selector to the list of readable regular files it names, fail-closed. A selector is
    an exact repo-relative file path, or a directory prefix ending in /** (every regular file beneath it).
    Every resolved path must stay inside root (an absolute or escaping selector fails closed), must not be
    a symlink, and must decode as UTF-8 when read by the caller; a selector matching zero files fails
    closed (an unmatched declared surface is never 'nothing to scan'). Returns a sorted list of Paths."""
    root_resolved = root.resolve()
    if Path(selector).is_absolute():
        raise GateError("configuration {}: path selector {!r} is absolute; a repo-relative selector is "
                        "required".format(CONFIG_REL, selector))
    if selector.endswith("/**"):
        prefix = selector[:-3]
        base = (root / prefix)
        if not base.resolve().is_relative_to(root_resolved):
            raise GateError("configuration {}: path selector {!r} escapes the repository".format(
                CONFIG_REL, selector))
        if base.is_symlink() or not base.is_dir():
            raise GateError("configuration {}: directory-prefix selector {!r} is not a directory".format(
                CONFIG_REL, selector))
        try:
            found = sorted(walk_files(base, {".git", "__pycache__"}))
        except OSError as exc:
            raise GateError("configuration {}: cannot list surface {!r} ({})".format(
                CONFIG_REL, selector, exc))
        files = []
        for path in found:
            if path.is_symlink():
                raise GateError("configuration {}: surface {!r} contains a symlink {}".format(
                    CONFIG_REL, selector, path.relative_to(root)))
            files.append(path)
        if not files:
            raise GateError("configuration {}: path selector {!r} matched no readable regular file".format(
                CONFIG_REL, selector))
        return files
    target = root / selector
    if not target.resolve().is_relative_to(root_resolved):
        raise GateError("configuration {}: path selector {!r} escapes the repository".format(
            CONFIG_REL, selector))
    if target.is_symlink():
        raise GateError("configuration {}: path selector {!r} is a symlink".format(CONFIG_REL, selector))
    if not target.is_file():
        raise GateError("configuration {}: path selector {!r} matched no readable regular file".format(
            CONFIG_REL, selector))
    return [target]


def _ends_with_continuation(text):
    """True when text ends with an ODD run of backslashes, i.e. a backslash-newline line continuation (an
    even run is an escaped backslash, not a continuation)."""
    count = 0
    k = len(text) - 1
    while k >= 0 and text[k] == "\\":
        count += 1
        k -= 1
    return count % 2 == 1


def join_continuations(text):
    """Yield (lineno, logical_line) pairs, joining backslash-newline continuations so a sink split across a
    continuation is scanned whole (a command word split from its option, or an option from its value, is
    otherwise missed and reads clean). lineno is the physical line where the logical line STARTS. The
    trailing continuation backslash is removed and the next physical line appended directly, matching shell
    backslash-newline semantics."""
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        start = i + 1
        buf = lines[i]
        while _ends_with_continuation(buf) and i + 1 < n:
            buf = buf[:-1] + lines[i + 1]
            i += 1
        yield start, buf
        i += 1


def scan_file(root, path, binding, telemetry):
    """Scan one declared surface file for a binding's sinks. Read fail-closed: an OSError or a non-UTF-8
    file is exit 2 (an unreadable declared surface is never a clean skip). Returns a list of finding
    strings naming the binding id, the repo-relative path and line, and the option, WITHOUT echoing the
    literal value (it may be sensitive). telemetry accumulates the commands and options actually matched,
    so run() can reconcile the config's selectors against the real source."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise GateError("configuration {} binding {!r}: cannot read declared surface {} ({})".format(
            CONFIG_REL, binding["id"], path.relative_to(root), exc))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise GateError("configuration {} binding {!r}: declared surface {} is not valid UTF-8".format(
            CONFIG_REL, binding["id"], path.relative_to(root)))
    rel = path.relative_to(root).as_posix()
    commands = set(binding["commands"])
    ref_vars = set(binding["reference-variables"])
    findings = []
    for lineno, line in join_continuations(text):
        for option, kind in scan_line(line, commands, binding["options"], ref_vars, telemetry):
            if kind == "literal":
                findings.append("{}:{}: binding {!r} option {!r} carries a hardcoded literal value; derive "
                                "it from the authoritative source or reference an allowed variable".format(
                                    rel, lineno, binding["id"], option))
            else:
                findings.append("{}:{}: binding {!r} option {!r} has a missing or empty value (a "
                                "cannot-evaluate for the non-literal property; fail-closed as a finding)"
                                .format(rel, lineno, binding["id"], option))
    return findings


def run(root):
    """Scan the declared surfaces under root. Returns the exit code 0/1/2. An absent configuration is NOT
    APPLICABLE (exit 0); a present configuration is loaded, its surfaces resolved and scanned."""
    config_path = root / CONFIG_REL
    try:
        # A symlinked configuration is fail-closed, never followed: Path.exists() follows the link, so a
        # dangling symlink would otherwise read as absent (NOT APPLICABLE), and a link to a real file has no
        # containment guarantee (load_config would open it wherever it points). This mirrors the symlink
        # fail-closed stance resolve_surface takes for declared surfaces. Only a genuinely absent path (not a
        # symlink and not present) is the NOT APPLICABLE case.
        if config_path.is_symlink():
            raise GateError("configuration {} is a symlink; a symlinked configuration is not "
                            "followed".format(CONFIG_REL))
        if not config_path.exists():
            print("NOT APPLICABLE: no {} configuration; the derive-don't-hardcode gate is adopter-declared "
                  "and inert without one".format(CONFIG_REL))
            return 0
        bindings = load_config(config_path)
        findings = []
        scanned = 0
        for binding in bindings:
            # Resolve each path selector separately and detect overlap: a file produced by more than one
            # selector (an exact duplicate, or an exact path also swept by a directory prefix such as
            # ["cmd/run.sh", "cmd/**"]) is a malformed control that would double-scan, so fail closed.
            files = []
            seen_files = set()
            for selector in binding["paths"]:
                for path in resolve_surface(root, selector):
                    if path in seen_files:
                        raise GateError("configuration {} binding {!r}: path selector {!r} overlaps another "
                                        "selector at {}".format(CONFIG_REL, binding["id"], selector,
                                                                path.relative_to(root)))
                    seen_files.add(path)
                    files.append(path)
            telemetry = {"commands": set(), "options": set()}
            for path in sorted(files):
                scanned += 1
                findings.extend(scan_file(root, path, binding, telemetry))
            # Reconcile the config's command and option selectors against the real scanned source (grdinp):
            # a selector that matches NOTHING (a typo, or a stale surface) cannot be evaluated, so it is a
            # cannot-evaluate that fails closed (exit 2), never a silent clean pass.
            unmatched_commands = [c for c in binding["commands"] if c not in telemetry["commands"]]
            if unmatched_commands:
                raise GateError("configuration {} binding {!r}: command selector(s) {} matched no command "
                                "word in the scanned surface (cannot-evaluate)".format(
                                    CONFIG_REL, binding["id"], ", ".join(map(repr, unmatched_commands))))
            unmatched_options = [o for o in binding["options"] if o not in telemetry["options"]]
            if unmatched_options:
                raise GateError("configuration {} binding {!r}: option selector(s) {} matched no option in "
                                "the scanned surface (cannot-evaluate)".format(
                                    CONFIG_REL, binding["id"], ", ".join(map(repr, unmatched_options))))
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    print("scanned {} declared surface file(s) across {} binding(s)".format(scanned, len(bindings)))
    if findings:
        print("FAIL: {} derive-don't-hardcode finding(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: every declared command sink carries a derived (non-literal) value")
    return 0


# --- self-test ---------------------------------------------------------------------------------------
# The tokenization and value-classification cases are pure and ALWAYS run. An end-to-end layer over a real
# synthetic tree (real reads, real containment) runs where a writable tempdir exists and is reported
# PARTIAL where not. No wall clock, no randomness, no network.

def _run_quiet(root):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(root)


def self_test_main():  # noqa: C901  a flat sequence of independent cases
    failures = []
    commands = {"sample-tool"}
    options = ["--target"]
    ref_vars = {"CURRENT_TARGET"}

    def kinds(line):
        return [k for _opt, k in scan_line(line, commands, options, ref_vars)]

    # Literal separated and attached values fail; an allowed reference (bare, braced, or double-quoted)
    # passes; a single-quoted variable-like string and a command substitution are literals.
    if kinds("sample-tool --target sample-project/repo") != ["literal"]:
        failures.append("a literal separated value was not flagged")
    if kinds("sample-tool --target=sample-project/repo") != ["literal"]:
        failures.append("a literal --option=value was not flagged")
    if kinds("sample-tool --target $CURRENT_TARGET"):
        failures.append("an allowed bare variable reference was flagged")
    if kinds("sample-tool --target ${CURRENT_TARGET}"):
        failures.append("an allowed braced variable reference was flagged")
    if kinds('sample-tool --target "$CURRENT_TARGET"'):
        failures.append("an allowed double-quoted variable reference was flagged")
    if kinds("sample-tool --target '$CURRENT_TARGET'") != ["literal"]:
        failures.append("a single-quoted variable-like string was not flagged as a literal")
    if kinds("sample-tool --target $(resolve-target)") != ["literal"]:
        failures.append("a command substitution was not flagged (v1 does not credit it)")
    if kinds("sample-tool --target $OTHER_VAR") != ["literal"]:
        failures.append("a reference to a NON-allowed variable was not flagged as a literal")

    # A missing value fails, in both separated (end of line, or a following option) and attached forms.
    if kinds("sample-tool --target") != ["missing"]:
        failures.append("a missing separated value at end of line was not flagged")
    if kinds("sample-tool --target --other x") != ["missing"]:
        failures.append("a missing value before another option was not flagged")
    if kinds("sample-tool --target=") != ["missing"]:
        failures.append("an empty attached value was not flagged")

    # An unrelated command, and an unrelated option on a configured command, both pass; a command name that
    # merely CONTAINS the configured name does not match; a path to the tool does match by basename.
    if kinds("other-tool --target sample-project/repo"):
        failures.append("an unrelated command word was scanned")
    if kinds("sample-tool --other sample-project/repo"):
        failures.append("an unrelated option was flagged")
    if kinds("sample-toolkit --target sample-project/repo"):
        failures.append("a command name merely containing the configured name matched")
    if kinds("/usr/bin/sample-tool --target sample-project/repo") != ["literal"]:
        failures.append("a path to the configured command did not match by basename")

    # Multiple occurrences on one line are all checked; a separator bounds the argument window so the second
    # command's option is attributed to it, not the first.
    if kinds("sample-tool --target lit-a | sample-tool --target lit-b") != ["literal", "literal"]:
        failures.append("multiple command occurrences were not all checked")

    # A prose apostrophe or an unbalanced quote never crashes the tokenizer (it is total).
    try:
        kinds("don't run sample-tool here")
    except Exception as exc:  # noqa: BLE001  any raise is the failure under test
        failures.append("an unbalanced quote raised instead of tokenizing best-effort ({})".format(exc))

    # classify_value directly, including the double-quote strip and the non-allowed name.
    if classify_value("$CURRENT_TARGET", ref_vars) != "reference":
        failures.append("classify_value missed a bare allowed reference")
    if classify_value("'$CURRENT_TARGET'", ref_vars) != "literal":
        failures.append("classify_value credited a single-quoted reference")
    if classify_value("owner/repo", ref_vars) != "literal":
        failures.append("classify_value credited a bare literal")

    # (finding C) command-word anchoring and comment stripping: a configured command name used as an
    # ARGUMENT to another command, or inside an unquoted word-start comment, is not an invocation and must
    # not fire. Without the fix each of these FALSE-FIRES a literal.
    if kinds("echo sample-tool --target owner/repo"):
        failures.append("a configured command used as an argument (not a command word) fired")
    if kinds("# sample-tool --target owner/repo"):
        failures.append("a fully commented-out sink fired")
    if kinds("sample-tool --target $CURRENT_TARGET # sample-tool --target owner/repo") != []:
        failures.append("a trailing comment carrying a literal sink fired")
    # a leading NAME=value assignment precedes the command word and is skipped, so the sink is still seen.
    if kinds("FOO=bar sample-tool --target owner/repo") != ["literal"]:
        failures.append("a command word behind a leading env-assignment was not anchored")
    # a `#` inside a token (not word-start) is not a comment.
    if kinds("sample-tool --target a#b/repo") != ["literal"]:
        failures.append("a mid-token '#' was wrongly treated as a comment")

    # (finding D) bundled short-option clusters: a declared short option inside a cluster (-xt for -x -t)
    # is still matched, in both separated (value is the next token) and attached (-tVALUE) forms. Without
    # the fix the cluster slips past tok==option / tok.startswith(option+'=') and the literal is missed.
    def kinds_short(line):
        return [k for _opt, k in scan_line(line, commands, ["-t"], ref_vars)]
    if kinds_short("sample-tool -xt owner/repo") != ["literal"]:
        failures.append("a bundled short option with a separated literal value was not matched")
    if kinds_short("sample-tool -xtowner/repo") != ["literal"]:
        failures.append("a bundled short option with an attached literal value was not matched")
    if kinds_short("sample-tool -xt $CURRENT_TARGET"):
        failures.append("a bundled short option with an allowed reference value was flagged")
    if kinds_short("sample-tool -t owner/repo") != ["literal"]:
        failures.append("a bare short option with a separated literal value was not matched")
    if kinds_short("sample-tool -xr owner/repo"):
        failures.append("a cluster not containing the declared short option was matched")

    # (finding A) telemetry records exactly the commands and options actually matched, so run() can
    # reconcile the config's selectors against the real source.
    telem = {"commands": set(), "options": set()}
    scan_line("sample-tool --target owner/repo", commands, options, ref_vars, telem)
    if telem["commands"] != {"sample-tool"} or telem["options"] != {"--target"}:
        failures.append("scan_line telemetry did not record the matched command and option")
    telem = {"commands": set(), "options": set()}
    scan_line("echo not-a-sink here", commands, options, ref_vars, telem)
    if telem["commands"] or telem["options"]:
        failures.append("scan_line telemetry recorded a match where the command word was not configured")

    # (finding B) backslash-newline continuations join so a sink split across the break is seen whole; the
    # logical line's reported number is where it STARTS. Without the join the split sink is missed.
    joined = list(join_continuations("sample-tool \\\n  --target owner/repo\n"))
    if joined != [(1, "sample-tool   --target owner/repo")]:
        failures.append("join_continuations did not join a command split from its option at the start line")
    if not _ends_with_continuation("a \\") or _ends_with_continuation("a \\\\"):
        failures.append("_ends_with_continuation mis-graded an odd/even trailing backslash run")

    # --- end-to-end layer over a real synthetic tree (runs where a writable tempdir exists) -------------
    import shutil
    import tempfile

    skipped = []
    try:
        base_tmp = Path(tempfile.mkdtemp(prefix="aiqt-derived-cmd-selftest-"))
    except OSError:
        base_tmp = None

    if base_tmp is None:
        skipped.append("all end-to-end cases (no writable temp directory)")
    else:
        def _fresh(tag, config, surface_files):
            r = base_tmp / tag
            (r / ".aiqt").mkdir(parents=True, exist_ok=True)
            (r / ".aiqt" / "derived-command-parameters.toml").write_text(config, encoding="utf-8")
            for rel, body in surface_files.items():
                p = r / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(body, bytes):
                    p.write_bytes(body)
                else:
                    p.write_text(body, encoding="utf-8")
            return r

        good_cfg = ('format-version = 1\n\n[[binding]]\nid = "repository-target"\n'
                    'paths = ["cmd/resume.md"]\ncommands = ["sample-tool"]\noptions = ["--target"]\n'
                    'reference-variables = ["CURRENT_TARGET"]\n')
        try:
            # (a) absent configuration -> NOT APPLICABLE exit 0.
            r = base_tmp / "absent"
            r.mkdir(parents=True, exist_ok=True)
            if _run_quiet(r) != 0:
                failures.append("e2e: an absent configuration expected NOT APPLICABLE exit 0")

            # (b) a clean derived surface passes.
            r = _fresh("clean", good_cfg, {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 0:
                failures.append("e2e: a derived surface expected exit 0")

            # (c) a hardcoded literal fails exit 1.
            r = _fresh("literal", good_cfg, {"cmd/resume.md": "sample-tool --target sample-org/repo\n"})
            if _run_quiet(r) != 1:
                failures.append("e2e: a hardcoded literal expected exit 1")

            # (d) an unknown format-version fails closed exit 2.
            r = _fresh("badver", good_cfg.replace("format-version = 1", "format-version = 2"),
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: an unknown format-version expected fail-closed exit 2")

            # (e) a boolean version (TOML true) fails closed exit 2.
            r = _fresh("boolver", good_cfg.replace("format-version = 1", "format-version = true"),
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: a boolean format-version expected fail-closed exit 2")

            # (f) a duplicate binding id fails closed exit 2.
            dup_cfg = good_cfg + ('\n[[binding]]\nid = "repository-target"\npaths = ["cmd/resume.md"]\n'
                                  'commands = ["sample-tool"]\noptions = ["--target"]\n'
                                  'reference-variables = ["CURRENT_TARGET"]\n')
            r = _fresh("dup", dup_cfg, {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: a duplicate binding id expected fail-closed exit 2")

            # (g) an empty paths roster fails closed exit 2.
            r = _fresh("empty-scope", good_cfg.replace('paths = ["cmd/resume.md"]', "paths = []"),
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: an empty paths roster expected fail-closed exit 2")

            # (h) an absolute path selector fails closed exit 2.
            r = _fresh("abs-path", good_cfg.replace('paths = ["cmd/resume.md"]', 'paths = ["/etc/passwd"]'),
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: an absolute path selector expected fail-closed exit 2")

            # (i) a traversing (escaping) path selector fails closed exit 2.
            r = _fresh("escape", good_cfg.replace('paths = ["cmd/resume.md"]', 'paths = ["../outside.md"]'),
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: a traversing path selector expected fail-closed exit 2")

            # (j) an unmatched path selector fails closed exit 2.
            r = _fresh("unmatched", good_cfg.replace('paths = ["cmd/resume.md"]', 'paths = ["cmd/none.md"]'),
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: an unmatched path selector expected fail-closed exit 2")

            # (k) a non-UTF-8 declared surface fails closed exit 2.
            r = _fresh("nonutf8", good_cfg, {"cmd/resume.md": b"\xff\xfe not utf-8"})
            if _run_quiet(r) != 2:
                failures.append("e2e: a non-UTF-8 declared surface expected fail-closed exit 2")

            # (l) a directory-prefix selector scans every file beneath it; a literal in one fails exit 1.
            r = _fresh("prefix", good_cfg.replace('paths = ["cmd/resume.md"]', 'paths = ["cmd/**"]'),
                       {"cmd/a.md": "sample-tool --target $CURRENT_TARGET\n",
                        "cmd/b.md": "sample-tool --target sample-org/repo\n"})
            if _run_quiet(r) != 1:
                failures.append("e2e: a directory-prefix selector did not scan a literal beneath it")

            # (m) a symlink declared surface fails closed exit 2 (where the platform supports symlinks).
            r = _fresh("symlink", good_cfg, {"cmd/real.md": "sample-tool --target $CURRENT_TARGET\n"})
            link = r / "cmd" / "resume.md"
            try:
                link.symlink_to(r / "cmd" / "real.md")
            except (OSError, NotImplementedError):
                skipped.append("the symlink fail-closed case (no symlink support)")
            else:
                if _run_quiet(r) != 2:
                    failures.append("e2e: a symlink declared surface expected fail-closed exit 2")

            # (n) a present-but-dangling symlink CONFIGURATION fails closed exit 2, never NOT APPLICABLE
            # (Path.exists() follows the link and would otherwise read the broken link as absent).
            r = base_tmp / "dangling-config"
            (r / ".aiqt").mkdir(parents=True, exist_ok=True)
            cfg_link = r / ".aiqt" / "derived-command-parameters.toml"
            try:
                cfg_link.symlink_to(r / ".aiqt" / "does-not-exist.toml")
            except (OSError, NotImplementedError):
                skipped.append("the dangling-symlink configuration case (no symlink support)")
            else:
                if _run_quiet(r) != 2:
                    failures.append("e2e: a dangling-symlink configuration expected fail-closed exit 2")

            # (o) SELECTOR RECONCILIATION (finding A): a command selector that matches nothing in the
            # scanned surface (a typo) is a cannot-evaluate, exit 2, not a silent clean pass.
            r = _fresh("typo-command", good_cfg.replace('commands = ["sample-tool"]', 'commands = ["typo-tool"]'),
                       {"cmd/resume.md": "sample-tool --target sample-org/repo\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: a command selector matching nothing expected cannot-evaluate exit 2")

            # (p) an option selector that matches nothing (a typo) is likewise a cannot-evaluate, exit 2.
            r = _fresh("typo-option", good_cfg.replace('options = ["--target"]', 'options = ["--typo"]'),
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: an option selector matching nothing expected cannot-evaluate exit 2")

            # (q) a duplicate path selector is a malformed control, exit 2.
            r = _fresh("dup-path", good_cfg.replace('paths = ["cmd/resume.md"]',
                                                    'paths = ["cmd/resume.md", "cmd/resume.md"]'),
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: a duplicate path selector expected fail-closed exit 2")

            # (r) an overlapping path selector (an exact path also swept by a directory prefix) is a
            # malformed control that would double-scan, exit 2.
            r = _fresh("overlap-path", good_cfg.replace('paths = ["cmd/resume.md"]',
                                                        'paths = ["cmd/resume.md", "cmd/**"]'),
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: an overlapping path selector expected fail-closed exit 2")

            # (s) a duplicate option selector is a malformed control, exit 2.
            r = _fresh("dup-option", good_cfg.replace('options = ["--target"]',
                                                      'options = ["--target", "--target"]'),
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 2:
                failures.append("e2e: a duplicate option selector expected fail-closed exit 2")

            # (t) LINE-CONTINUATION (finding B): a sink whose command word is split from its option/value
            # by a backslash-newline is joined and seen whole, so the literal fails exit 1 (without the
            # join it is missed and reads clean).
            r = _fresh("continuation", good_cfg,
                       {"cmd/resume.md": "sample-tool \\\n  --target sample-org/repo\n"})
            if _run_quiet(r) != 1:
                failures.append("e2e: a sink split across a line continuation was not scanned whole")

            # (u) COMMENT + ANCHORING (finding C): a real derived sink plus a commented-out literal sink is
            # clean, exit 0 (without the comment strip the comment's literal false-fires exit 1).
            r = _fresh("comment", good_cfg,
                       {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"
                                         "# sample-tool --target sample-org/repo\n"})
            if _run_quiet(r) != 0:
                failures.append("e2e: a commented-out literal sink false-fired")

            # (v) BUNDLED SHORT OPTION (finding D): a declared short option inside a cluster (-xt) carries
            # the literal target value, exit 1 (without cluster handling it is missed and reads clean).
            r = _fresh("short-cluster", good_cfg.replace('options = ["--target"]', 'options = ["-t"]'),
                       {"cmd/resume.md": "sample-tool -xt sample-org/repo\n"})
            if _run_quiet(r) != 1:
                failures.append("e2e: a bundled short-option literal value was not scanned")
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    core = ("tokenization stays total on unbalanced quotes and drops an unquoted word-start comment, value "
            "classification credits only an allowed bare/braced/double-quoted reference (never a "
            "single-quoted, non-allowed, or substituted value), detection is anchored to the real "
            "command-word position (an argument or commented occurrence does not fire) past a leading "
            "env-assignment, command words match by basename (not by containment), bundled short-option "
            "clusters are matched, backslash-newline continuations join a split sink, the argument window "
            "is bounded, telemetry records the matched selectors, and multiple sinks are all checked")
    if skipped:
        print("SELF-TEST PASS (PARTIAL): {}; SKIPPED (UNVERIFIED this run): {}".format(
            core, "; ".join(skipped)))
    else:
        print("SELF-TEST PASS: {}; and the end-to-end layer holds (NOT APPLICABLE, clean pass, literal "
              "finding, malformed-version/duplicate-id/empty-scope/absolute/escaping/unmatched/non-UTF-8/"
              "symlink-surface/dangling-symlink-config fail-closed, directory-prefix scanning, selector "
              "reconciliation of an unmatched-command/unmatched-option/duplicate-path/overlapping-path/"
              "duplicate-option control failing closed, a line-continuation split sink scanned whole, a "
              "commented-out literal not firing, and a bundled short-option value scanned)"
              .format(core))
    return 0


def _parse_args(argv):
    self_test = False
    for arg in argv:
        if arg == "--self-test":
            self_test = True
        else:
            print("usage: check_derived_command_parameters.py [--self-test]; fail-closed", file=sys.stderr)
            return None
    return (self_test,)


def main():
    parsed = _parse_args(sys.argv[1:])
    if parsed is None:
        return 2
    (self_test,) = parsed
    if self_test:
        return self_test_main()
    root = Path(__file__).resolve().parents[1]
    return run(root)


if __name__ == "__main__":
    sys.exit(main())
