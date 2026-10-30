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
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_derived_command_parameters.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_derived_command_parameters.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
from _gen_common import precheck_special_files, read_source_bytes  # noqa: E402  SPECIAL-FILE-PRECHECK

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
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
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
        # read_source_bytes, never a plain read: the surface walk picks up WHATEVER sits under the
        # declared prefix (the D-400 precheck walks ignored directories too, but it certifies only
        # what stood at walk time, never a later drop-in), so a FIFO here is the named refusal
        # (SourceReadRefused -> GateError, exit 2), never a blocking read (F-CORPUS-FIFO-HANG class).
        raw = read_source_bytes(path)
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
# synthetic tree (real reads, real containment) runs where a writable tempdir exists; where it (or a case's
# symlink) cannot be made, each unrun case is a NAMED NOT RUN failure, never a skip beside a PASS, and a run
# whose only failures are NOT RUN exits 3, a nonzero cannot-evaluate. No wall clock, no randomness, no network.

def _run_quiet(root):
    import io
    from contextlib import redirect_stderr, redirect_stdout
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(root)


def _not_run(failures, case, cause):
    """Record a self-test case that could not run as a NAMED failure (a counted cannot-evaluate that
    fails the run), never a printed note beside a PASS: coverage that did not run is not coverage."""
    failures.append("NOT RUN: {}: {}; coverage that did not run FAILS the self-test, never a silent "
                    "PASS".format(case, cause))


_E2E_CASES = "every end-to-end case over a real synthetic tree"
_SYMLINK_CASE = "e2e (m) the symlink declared-surface fail-closed case"
_DANGLING_CASE = "e2e (n) the dangling-symlink configuration fail-closed case"


def _recorded(output, entry):
    """True when entry heads a bullet on the SELF-TEST FAIL list in output: a recorded failure prints as
    "  - <entry>" after that header, so a NOT RUN line that was only printed (never recorded) does not
    count."""
    _head, header, listed = output.partition("SELF-TEST FAIL:\n")
    return bool(header) and "\n  - " + entry in "\n" + listed


def _not_run_vectors(failures):
    """Red-on-revert vectors for the skips that printed SELF-TEST PASS (PARTIAL) and exited 0. Nested
    self-test runs inject an unavailable temp directory (mkdtemp raises OSError), a failing symlink for case
    (m) alone, for case (n) alone (the other case's symlink made for real), and for both; each run must exit
    exactly 3 (cannot evaluate) AND carry every unrun case's NOT RUN bullet on its failure list. The
    single-case runs matter: in the both-cases run the sibling's recorded failure alone makes the exit
    nonzero, so a call site that only printed its NOT RUN line would pass there. A final mixed run adds one
    definite failure beside the unavailable temp directory and must exit 1: a definite failure outranks a
    NOT RUN entry, never the cannot-evaluate exit 3."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    from unittest.mock import patch

    def _unavailable(*_args, **_kwargs):
        raise OSError("injected: unavailable")

    def _unavailable_for(link_name):
        # Fail only the symlink whose link is named link_name ("resume.md" is case (m)'s declared surface,
        # "derived-command-parameters.toml" case (n)'s configuration); any other symlink is made for real.
        def _symlink(link, target):
            if link.name == link_name:
                raise OSError("injected: unavailable")
            return Path.symlink_to(link, target)
        return _symlink

    want_m = "NOT RUN: {}: no symlink support".format(_SYMLINK_CASE)
    want_n = "NOT RUN: {}: no symlink support".format(_DANGLING_CASE)
    want_e2e = "NOT RUN: {}: no writable temp directory".format(_E2E_CASES)
    for label, kwargs, wants in (
            ("no-tempdir", {"_mkdtemp": _unavailable}, (want_e2e,)),
            ("no-symlink (m) only", {"_symlink": _unavailable_for("resume.md")}, (want_m,)),
            ("no-symlink (n) only", {"_symlink": _unavailable_for("derived-command-parameters.toml")},
             (want_n,)),
            ("no-symlink", {"_symlink": _unavailable}, (want_m, want_n))):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = self_test_main(_vectors=False, **kwargs)
        missing = [want for want in wants if not _recorded(out.getvalue(), want)]
        if rc != 3 or missing:
            failures.append("(NOT RUN vector {}) must exit 3 (cannot evaluate) AND record {!r} on the failure "
                            "list, never a PASS (PARTIAL) exit 0, a definite-failure exit 1 or a bare printed "
                            "line (got exit {})".format(label, missing or wants, rc))

    # The mixed run: the unavailable temp directory records the e2e NOT RUN entry, and a mis-graded
    # even backslash run (the real helper for every other input) records exactly one definite failure.
    real_ends = _ends_with_continuation

    def _misgraded(text):
        return text == "a \\\\" or real_ends(text)

    definite = "_ends_with_continuation mis-graded an odd/even trailing backslash run"
    out = io.StringIO()
    with patch.object(sys.modules[__name__], "_ends_with_continuation", _misgraded), \
            redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = self_test_main(_vectors=False, _mkdtemp=_unavailable)
    missing = [want for want in (want_e2e, definite) if not _recorded(out.getvalue(), want)]
    if rc != 1 or missing:
        failures.append("(NOT RUN vector mixed) a definite failure beside a NOT RUN entry must exit 1 AND "
                        "record {!r} on the failure list, never the cannot-evaluate exit 3 (got exit {})"
                        .format(missing or (want_e2e, definite), rc))


_SENTINEL_FAILURE = ("the injected unavailable-fixture cases ((w)) and the inherited-sentinel "
                     "regression ((x)) NOT RUN (AIQT_DERIVED_SELFTEST_CHILD=1 inherited at "
                     "this top-level --self-test entry); failing closed")


def self_test_main(_mkdtemp=None, _symlink=None, _vectors=True,  # noqa: C901  a flat sequence of cases
                   selftest_child=False):
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

    # --- end-to-end layer over a real synthetic tree (a NOT RUN failure where no tempdir exists) --------
    import shutil
    import tempfile

    symlink = _symlink or Path.symlink_to
    try:
        base_tmp = Path((_mkdtemp or tempfile.mkdtemp)(prefix="aiqt-derived-cmd-selftest-"))
    except OSError as exc:
        base_tmp = None
        _not_run(failures, _E2E_CASES, "no writable temp directory ({})".format(exc))

    if base_tmp is not None:
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

            # (e2) an integer literal past CPython's 4300-digit int-string limit makes tomllib raise a BARE
            #      ValueError (not TOMLDecodeError); it must still fail closed exit 2, never escape as a
            #      traceback (F-TOML-BARE-VALUEERROR-CLASS). The digit limit is pinned to the default 4300
            #      (test-hermeticity) and restored in finally.
            #      A parser overflow (tomllib raises RecursionError, not a ValueError) must fail closed the same
            #      way, and the refusal must carry the injected overflow (the parse handler mapped it).
            #      The overflow is INJECTED (tomllib.loads and tomllib.load raise RecursionError on a marked,
            #      otherwise valid input) rather than provoked by a deeply nested body: the depth at which
            #      tomllib overflows is an interpreter limit, so a fixed body overflows under one recursion
            #      limit and parses (or trips an unrelated refusal) under another.
            import io
            from contextlib import redirect_stderr, redirect_stdout
            real_loads, real_load = tomllib.loads, tomllib.load

            def overflowing_loads(text, **kwargs):
                if "injected-overflow" in text:
                    raise RecursionError("injected parser overflow")
                return real_loads(text, **kwargs)

            tomllib.loads = overflowing_loads
            tomllib.load = lambda fp, **kwargs: overflowing_loads(fp.read().decode("utf-8"), **kwargs)
            try:
                for big_tag, big_value in (("over-long-int", "9" * 4400),
                                           ("parser-overflow", "1  # injected-overflow")):
                    r = _fresh(big_tag, good_cfg.replace("format-version = 1", "format-version = " + big_value),
                               {"cmd/resume.md": "sample-tool --target $CURRENT_TARGET\n"})
                    _prev_digits = sys.get_int_max_str_digits()
                    sys.set_int_max_str_digits(4300)
                    big_err = io.StringIO()
                    try:
                        with redirect_stdout(io.StringIO()), redirect_stderr(big_err):
                            rc_big = run(r)
                    except (ValueError, RecursionError) as exc:
                        rc_big = "a bare {} escaped".format(type(exc).__name__)
                    finally:
                        sys.set_int_max_str_digits(_prev_digits)
                    if rc_big != 2:
                        failures.append("e2e: {} expected fail-closed exit 2, got {}".format(big_tag, rc_big))
                    elif big_tag == "parser-overflow" and "injected parser overflow" not in big_err.getvalue():
                        failures.append("e2e: parser-overflow refused without the parse handler's finding: {!r}"
                                        .format(big_err.getvalue()))
            finally:
                tomllib.loads, tomllib.load = real_loads, real_load

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

            # (m) a symlink declared surface fails closed exit 2 (a NOT RUN failure without symlink support).
            r = _fresh("symlink", good_cfg, {"cmd/real.md": "sample-tool --target $CURRENT_TARGET\n"})
            link = r / "cmd" / "resume.md"
            try:
                symlink(link, r / "cmd" / "real.md")
            except (OSError, NotImplementedError) as exc:
                _not_run(failures, _SYMLINK_CASE, "no symlink support ({})".format(exc))
            else:
                if _run_quiet(r) != 2:
                    failures.append("e2e: a symlink declared surface expected fail-closed exit 2")

            # (n) a present-but-dangling symlink CONFIGURATION fails closed exit 2, never NOT APPLICABLE
            # (Path.exists() follows the link and would otherwise read the broken link as absent).
            r = base_tmp / "dangling-config"
            (r / ".aiqt").mkdir(parents=True, exist_ok=True)
            cfg_link = r / ".aiqt" / "derived-command-parameters.toml"
            try:
                symlink(cfg_link, r / ".aiqt" / "does-not-exist.toml")
            except (OSError, NotImplementedError) as exc:
                _not_run(failures, _DANGLING_CASE, "no symlink support ({})".format(exc))
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

            # (w) #400 re-cut round 7 (codex 5): each unavailable REQUIRED fixture must
            #     yield the NONZERO cannot-evaluate exit (3), never PASS (PARTIAL) exit 0:
            #     one injected child per condition (refused tempdir; refused symlink for
            #     the declared-surface case; refused symlink for the dangling-symlink
            #     configuration case), each run through the real wiring. The children are
            #     marked by ARGUMENT, not only by the environment sentinel they inherit: an
            #     inherited sentinel at a top-level entry is a recorded NOT RUN failure
            #     (#400 re-cut round 8, decided 2), never a silent skip.
            import os as _os
            import subprocess
            if selftest_child or not _vectors:
                # the deliberate child of the injection and regression runners below, or a nested
                # in-process run of _not_run_vectors: its own copies of cases (w) and (x) stay
                # skipped BY ARGUMENT, which an inherited environment cannot forge (#400 re-cut
                # round 8, decided 2); the top-level run still spawns them
                pass
            elif _os.environ.get("AIQT_DERIVED_SELFTEST_CHILD") == "1":
                # #400 re-cut round 8 (decided 2): an INHERITED child sentinel at a
                # top-level --self-test entry is a recorded NOT RUN failure (nonzero),
                # never a silent omission of cases (w) and (x); the recursion guard still
                # holds (this branch spawns nothing)
                failures.append(_SENTINEL_FAILURE)
            else:
                childenv = dict(_os.environ, AIQT_DERIVED_SELFTEST_CHILD="1")
                toolsdir = str(Path(__file__).resolve().parent)
                injections = (
                    ("tempdir",
                     "_real_mkdtemp = tempfile.mkdtemp\n"
                     "def _refused(*args, **kwargs):\n"
                     "    if str(kwargs.get('prefix', '')).startswith('aiqt-derived-cmd-selftest-'):\n"
                     "        raise OSError('injected: no writable temp directory')\n"
                     "    return _real_mkdtemp(*args, **kwargs)\n"
                     "tempfile.mkdtemp = _refused\n",
                     "NOT RUN: {}: no writable temp directory".format(_E2E_CASES)),
                    ("surface-symlink",
                     "_real_symlink = os.symlink\n"
                     "def _refused(target, *args, **kwargs):\n"
                     "    if str(target).endswith('real.md'):\n"
                     "        raise OSError('injected: no symlink support')\n"
                     "    return _real_symlink(target, *args, **kwargs)\n"
                     "os.symlink = _refused\n",
                     "NOT RUN: {}: no symlink support".format(_SYMLINK_CASE)),
                    ("config-symlink",
                     "_real_symlink = os.symlink\n"
                     "def _refused(target, *args, **kwargs):\n"
                     "    if str(target).endswith('does-not-exist.toml'):\n"
                     "        raise OSError('injected: no symlink support')\n"
                     "    return _real_symlink(target, *args, **kwargs)\n"
                     "os.symlink = _refused\n",
                     "NOT RUN: {}: no symlink support".format(_DANGLING_CASE)),
                )
                # Each child passes _vectors=False: its injected refusal would refuse the fixtures of
                # the in-process NOT RUN vectors too, which the top-level run already executes.
                for tag, patch, marker in injections:
                    runner = base_tmp / ("cannot-evaluate-" + tag + ".py")
                    runner.write_text(
                        "import sys, os, tempfile\n"
                        + "sys.path.insert(0, " + repr(toolsdir) + ")\n"
                        + patch
                        + "import check_derived_command_parameters as mod\n"
                        + "sys.exit(mod.self_test_main(selftest_child=True, _vectors=False))\n",
                        encoding="utf-8")
                    proc = subprocess.run([sys.executable, "-I", "-B", str(runner)],
                                          capture_output=True, text=True, env=childenv,
                                          timeout=600)
                    out = proc.stdout + proc.stderr
                    if proc.returncode != 3 or "CANNOT EVALUATE" not in out or marker not in out:
                        failures.append("injected unavailable fixture ({}) expected the nonzero "
                                        "cannot-evaluate exit 3 naming {!r}, got rc={} "
                                        "tail={!r}".format(tag, marker, proc.returncode,
                                                               out[-200:]))

                # (x) #400 re-cut round 8 (decided 2, and decided 4: this vector guards the
                #     PARENT wiring, not a helper): run the REAL script entry with the
                #     sentinel inherited and no child argument; it must fail rc 1 with
                #     EXACTLY the recorded NOT RUN failure, so a silent top-level skip of
                #     case (w) can never return
                proc = subprocess.run([sys.executable, "-I", "-B",
                                       str(Path(__file__).resolve()), "--self-test"],
                                      capture_output=True, text=True, env=childenv,
                                      timeout=600)
                sentinel_failures = [line[4:] for line in
                                     (proc.stdout + proc.stderr).splitlines()
                                     if line.startswith("  - ")]
                if proc.returncode != 1 or sentinel_failures != [_SENTINEL_FAILURE]:
                    failures.append("an inherited child sentinel at a top-level --self-test "
                                    "entry expected rc 1 with exactly the recorded NOT RUN "
                                    "failure, got rc={} failures={!r}".format(
                                        proc.returncode, sentinel_failures[-6:]))
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if _vectors:
        _not_run_vectors(failures)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        if all(f.startswith("NOT RUN: ") for f in failures):
            # An unavailable required fixture is a NONZERO cannot-evaluate result (exit 3), never a PASS
            # (PARTIAL) exit 0 that could ship a defect detectable only by the omitted cases; a definite
            # failure beside a NOT RUN entry outranks it (exit 1).
            print("SELF-TEST CANNOT EVALUATE: NOT RUN (required fixtures unavailable); failing closed (exit 3)")
            return 3
        return 1
    core = ("tokenization stays total on unbalanced quotes and drops an unquoted word-start comment, value "
            "classification credits only an allowed bare/braced/double-quoted reference (never a "
            "single-quoted, non-allowed, or substituted value), detection is anchored to the real "
            "command-word position (an argument or commented occurrence does not fire) past a leading "
            "env-assignment, command words match by basename (not by containment), bundled short-option "
            "clusters are matched, backslash-newline continuations join a split sink, the argument window "
            "is bounded, telemetry records the matched selectors, and multiple sinks are all checked")
    print("SELF-TEST PASS: {}; and the end-to-end layer holds (NOT APPLICABLE, clean pass, literal "
          "finding, malformed-version/duplicate-id/empty-scope/absolute/escaping/unmatched/non-UTF-8/"
          "symlink-surface/dangling-symlink-config fail-closed, directory-prefix scanning, selector "
          "reconciliation of an unmatched-command/unmatched-option/duplicate-path/overlapping-path/"
          "duplicate-option control failing closed, a line-continuation split sink scanned whole, a "
          "commented-out literal not firing, and a bundled short-option value scanned); an unavailable "
          "temp directory or symlink support is a NOT RUN failure (exit 3), never a PASS (PARTIAL), and a "
          "definite failure beside a NOT RUN entry exits 1".format(core))
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
    root = precheck_special_files(Path(__file__).resolve().parents[1])
    return run(root)


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This entry point reads a
# child's exit status (the git queries of the special-file precheck in tools/_gen_common.py
# among them), so under an ignored SIGCHLD it would read a failing child as a pass; it refuses
# at its entry line instead, before main() runs.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("check_derived_command_parameters.py") or main())
