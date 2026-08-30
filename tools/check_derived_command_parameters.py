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
the residue names that missed subset.

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
    separator with no surrounding space stays inside its token."""
    tokens = []
    i, n = 0, len(line)
    while i < n:
        if line[i].isspace():
            i += 1
            continue
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


def scan_line(line, commands, options, ref_vars):
    """Scan one line for a declared sink and return a list of (option, kind) findings, kind in
    {'literal', 'missing'}. For each token whose basename is a configured command, the tokens AFTER it up
    to the next shell separator or the next configured command word are its argument window; a configured
    option in that window, in separated (--opt value) or attached (--opt=value) form, has its value
    classified. A reference produces no finding; a literal or a missing/empty value does. Multiple command
    occurrences on one line are each scanned. Best-effort (class c): the argument window is lexical, not a
    real parse of pipelines or grouping."""
    findings = []
    tokens = split_tokens(line)
    n = len(tokens)
    for idx, token in enumerate(tokens):
        if command_basename(token) not in commands:
            continue
        j = idx + 1
        while j < n:
            tok = tokens[j]
            if tok in _SEPARATORS or command_basename(tok) in commands:
                break
            matched_option = None
            attached_value = None
            for option in options:
                if tok == option:
                    matched_option = option
                    break
                if tok.startswith(option + "="):
                    matched_option = option
                    attached_value = tok[len(option) + 1:]
                    break
            if matched_option is None:
                j += 1
                continue
            if attached_value is not None:
                if attached_value == "":
                    findings.append((matched_option, "missing"))
                elif classify_value(attached_value, ref_vars) == "literal":
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
    return findings


# --- configuration and surface loading ---------------------------------------------------------------

def _req_str_list(table, key, where, allow_empty=False):
    value = table.get(key)
    if not isinstance(value, list) or (not value and not allow_empty):
        raise GateError("{}: {!r} must be a non-empty array of strings".format(where, key))
    for element in value:
        if not isinstance(element, str) or not element.strip():
            raise GateError("{}: {!r} contains a non-string or empty element".format(where, key))
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


def scan_file(root, path, binding):
    """Scan one declared surface file for a binding's sinks. Read fail-closed: an OSError or a non-UTF-8
    file is exit 2 (an unreadable declared surface is never a clean skip). Returns a list of finding
    strings naming the binding id, the repo-relative path and line, and the option, WITHOUT echoing the
    literal value (it may be sensitive)."""
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
    for lineno, line in enumerate(text.splitlines(), 1):
        for option, kind in scan_line(line, commands, binding["options"], ref_vars):
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
            files = []
            for selector in binding["paths"]:
                files.extend(resolve_surface(root, selector))
            for path in sorted(set(files)):
                scanned += 1
                findings.extend(scan_file(root, path, binding))
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
            r = _fresh("clean", good_cfg, {"cmd/resume.md": "run sample-tool --target $CURRENT_TARGET\n"})
            if _run_quiet(r) != 0:
                failures.append("e2e: a derived surface expected exit 0")

            # (c) a hardcoded literal fails exit 1.
            r = _fresh("literal", good_cfg, {"cmd/resume.md": "run sample-tool --target sample-org/repo\n"})
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
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    core = ("tokenization stays total on unbalanced quotes, value classification credits only an allowed "
            "bare/braced/double-quoted reference (never a single-quoted, non-allowed, or substituted "
            "value), command words match by basename (not by containment), the argument window is bounded, "
            "and multiple sinks are all checked")
    if skipped:
        print("SELF-TEST PASS (PARTIAL): {}; SKIPPED (UNVERIFIED this run): {}".format(
            core, "; ".join(skipped)))
    else:
        print("SELF-TEST PASS: {}; and the end-to-end layer holds (NOT APPLICABLE, clean pass, literal "
              "finding, malformed-version/duplicate-id/empty-scope/absolute/escaping/unmatched/non-UTF-8/"
              "symlink-surface/dangling-symlink-config fail-closed, and directory-prefix scanning)"
              .format(core))
    return 0


def _parse_args(argv):
    self_test = False
    for arg in argv:
        if arg == "--self-test":
            self_test = True
        else:
            print("usage: check_derived_command_parameters.py [--self-test]", file=sys.stderr)
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
