#!/usr/bin/env python3
"""Compare installed git branch/tag help with the hook's static option table.

Run with: python3 -I -B tools/check_git_option_table.py [--root DIR]
          python3 -I -B tools/check_git_option_table.py --self-test
Exit: 0 clean within scope; 1 drift finding; 2 cannot-evaluate.

Primary input is git <sub> -h, stdout plus stderr, status 129. Completion
adds required value-form checks in a fresh isolated repository. Unavailable,
empty or malformed completion is cannot-evaluate. No hook code is imported.

Residuals: only branch/tag and help-visible options are enumerated. Hidden
options and unadvertised negations can escape membership checking. Unchanged
spellings can change semantics without detection. The maintained role catalog
can itself drift. Help-format changes can defeat enumeration; recognizable
malformations fail closed, but the sanity floor cannot prove completeness.
NOCOMPLETE options lack the helper value-form layer. Role verdict profiles
cover the operand shapes documented below, not every command combination.
This does not verify the classifier algorithm, dynamic table mutation, or
the provenance of the git executable selected from PATH.
"""
import argparse
import ast
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT = 30
SUBCOMMANDS = ("branch", "tag")
SOURCE_REL = ".aiqt/core/hooks/scripts/aiqt_hooks.py"
MIN_OPTIONS = 5
LONG_NAME = re.compile(r"--[a-z][a-z0-9-]*\Z")
SHORT_NAME = re.compile(r"-[A-Za-z0-9]\Z")
HELP_OPTION = re.compile(
    r"(?<![\w-])(?:--(\[no-\])?([a-z][a-z0-9-]*)|(-[A-Za-z0-9]))"
    r"(?=$|[\s,=\[<\]|)])"
)
SHORT_FIELDS = {
    "short_write": "W", "short_list": "L",
    "short_read": "R", "short_optnum": "N",
}
LONG_ROLES = frozenset("WLVFDOR")
VALUE_ROLES = frozenset("WFD")

# Derived from _git_ref_cmd_mutating and _short_cluster_verdict in SOURCE_REL.
# Columns: OPTION; OPTION name; OPTION value name; OPTION=value;
# OPTION=value name; OPTION -- name. True is MUTATING, False is READ.
# No unconditional R/W mapping exists: consuming a value or enabling list mode
# can hide a creation operand. L/V share verdicts; N shares these bare-option
# verdicts but additionally consumes attached decimal digits in short clusters.
ROLE_VERDICTS = {
    "W": (True, True, True, True, True, True),
    "L": (False, False, False, True, True, False),
    "V": (False, False, False, True, True, False),
    "F": (False, False, False, False, False, False),
    "D": (True, False, True, False, True, True),
    "O": (False, True, True, False, True, True),
    "R": (False, True, True, True, True, True),
    "N": (False, False, False, True, True, False),
}

# Independent safety catalog, not copied or inferred at runtime from the hook.
# Each entry selects the expected classifier role profile from git semantics:
# W: mutation/control, conservatively immediate MUTATING; L/V: list/verify;
# F: filter that implies listing and consumes a value; D: required display value
# without list mode; O: optional attached value; R: no value or list mode;
# N: optional attached decimal and list mode. W over-roles always remain safe.
# These are safety profiles, not claims that every accepted git invocation has
# that effect. For example show-current is conservatively R, and cancelled
# actions are W. Display modifiers and create-reflog do not imply list mode.
#
# B: git v2.53.0 builtin/branch.c, cmd_branch options and action dispatch:
# https://raw.githubusercontent.com/git/git/v2.53.0/builtin/branch.c
# T: upstream builtin/tag.c, cmd_tag options and action dispatch:
# https://raw.githubusercontent.com/git/git/master/builtin/tag.c
# T was corroborated against installed 2.53.0 help spellings, not pinned source
# bytes for that build; maintainers should pin/review it when updating the catalog.
# P: git v2.53.0 parse-options.c negation and parse-options.h option macros:
# https://raw.githubusercontent.com/git/git/v2.53.0/parse-options.c
# https://raw.githubusercontent.com/git/git/v2.53.0/parse-options.h
ROLE_CATALOG = {
    ("branch", "--verbose"): "R",  # B: verbosity.
    ("branch", "--no-verbose"): "R",  # B/P: reset verbosity.
    ("branch", "-v"): "R",  # B: verbosity.
    ("branch", "--quiet"): "R",  # B: messages.
    ("branch", "--no-quiet"): "R",  # B/P: reset quiet.
    ("branch", "-q"): "R",  # B: messages.
    ("branch", "--track"): "W",  # B: tracking configuration.
    ("branch", "--no-track"): "W",  # B/P: tracking configuration.
    ("branch", "-t"): "W",  # B: tracking configuration.
    ("branch", "--set-upstream-to"): "W",  # B: upstream configuration.
    ("branch", "--no-set-upstream-to"): "W",  # B/P: upstream control.
    ("branch", "-u"): "W",  # B: upstream configuration.
    ("branch", "--unset-upstream"): "W",  # B: removes upstream.
    ("branch", "--no-unset-upstream"): "W",  # B/P: upstream control.
    ("branch", "--color"): "O",  # B: display.
    ("branch", "--no-color"): "R",  # B/P: display.
    ("branch", "--remotes"): "R",  # B: selection.
    ("branch", "-r"): "R",  # B: selection.
    ("branch", "--contains"): "F",  # B: filter.
    ("branch", "--no-contains"): "F",  # B: inverse filter.
    ("branch", "--abbrev"): "O",  # B: display.
    ("branch", "--no-abbrev"): "R",  # B/P: display.
    ("branch", "--all"): "R",  # B: selection.
    ("branch", "-a"): "R",  # B: selection.
    ("branch", "--delete"): "W",  # B: deletion.
    ("branch", "--no-delete"): "W",  # B/P: action cancellation.
    ("branch", "-d"): "W",  # B: deletion.
    ("branch", "-D"): "W",  # B: forced deletion.
    ("branch", "--move"): "W",  # B: rename.
    ("branch", "--no-move"): "W",  # B/P: action cancellation.
    ("branch", "-m"): "W",  # B: rename.
    ("branch", "-M"): "W",  # B: forced rename.
    ("branch", "--omit-empty"): "R",  # B: display.
    ("branch", "--no-omit-empty"): "R",  # B/P: display.
    ("branch", "--copy"): "W",  # B: copy.
    ("branch", "--no-copy"): "W",  # B/P: action cancellation.
    ("branch", "-c"): "W",  # B: copy.
    ("branch", "-C"): "W",  # B: forced copy.
    ("branch", "--list"): "L",  # B: list mode.
    ("branch", "--no-list"): "W",  # B/P: cancels list.
    ("branch", "-l"): "L",  # B: list mode.
    ("branch", "--show-current"): "R",  # B: display.
    ("branch", "--no-show-current"): "W",  # B/P: cancels action.
    ("branch", "--create-reflog"): "R",  # B: creation modifier.
    ("branch", "--no-create-reflog"): "R",  # B/P: creation modifier.
    ("branch", "--edit-description"): "W",  # B: configuration edit.
    ("branch", "--no-edit-description"): "W",  # B/P: action cancellation.
    ("branch", "--force"): "W",  # B: mutation override.
    ("branch", "--no-force"): "W",  # B/P: mutation control.
    ("branch", "-f"): "W",  # B: mutation override.
    ("branch", "--merged"): "F",  # B: filter.
    ("branch", "--no-merged"): "F",  # B: inverse filter.
    ("branch", "--column"): "O",  # B: display.
    ("branch", "--no-column"): "R",  # B/P: display.
    ("branch", "--sort"): "D",  # B: sorting.
    ("branch", "--no-sort"): "R",  # B/P: sorting.
    ("branch", "--points-at"): "F",  # B: filter.
    ("branch", "--no-points-at"): "W",  # B/P: clears filter.
    ("branch", "--ignore-case"): "R",  # B: matching.
    ("branch", "--no-ignore-case"): "R",  # B/P: matching.
    ("branch", "-i"): "R",  # B: matching.
    ("branch", "--recurse-submodules"): "W",  # B: recursive creation.
    ("branch", "--no-recurse-submodules"): "W",  # B/P: creation control.
    ("branch", "--format"): "D",  # B: formatting.
    ("branch", "--no-format"): "R",  # B/P: formatting.
    ("tag", "--list"): "L",  # T: list mode.
    ("tag", "-l"): "L",  # T: list mode.
    ("tag", "-n"): "N",  # T: list message lines.
    ("tag", "--delete"): "W",  # T: deletion.
    ("tag", "-d"): "W",  # T: deletion.
    ("tag", "--verify"): "V",  # T: verification.
    ("tag", "-v"): "L",  # T: verification.
    ("tag", "--annotate"): "W",  # T: tag creation.
    ("tag", "--no-annotate"): "W",  # T/P: creation control.
    ("tag", "-a"): "W",  # T: tag creation.
    ("tag", "--message"): "W",  # T: tag content.
    ("tag", "-m"): "W",  # T: tag content.
    ("tag", "--file"): "W",  # T: tag content.
    ("tag", "--no-file"): "W",  # T/P: content control.
    ("tag", "-F"): "W",  # T: tag content.
    ("tag", "--trailer"): "W",  # T: tag content.
    ("tag", "--edit"): "W",  # T: editor.
    ("tag", "--no-edit"): "W",  # T/P: editor control.
    ("tag", "-e"): "W",  # T: editor.
    ("tag", "--sign"): "W",  # T: signed creation.
    ("tag", "--no-sign"): "W",  # T/P: signing control.
    ("tag", "-s"): "W",  # T: signed creation.
    ("tag", "--cleanup"): "W",  # T: content processing.
    ("tag", "--no-cleanup"): "W",  # T/P: content processing.
    ("tag", "--local-user"): "W",  # T: signing identity.
    ("tag", "--no-local-user"): "W",  # T/P: signing control.
    ("tag", "-u"): "W",  # T: signing identity.
    ("tag", "--force"): "W",  # T: replacement.
    ("tag", "--no-force"): "W",  # T/P: replacement control.
    ("tag", "-f"): "W",  # T: replacement.
    ("tag", "--create-reflog"): "R",  # T: creation modifier.
    ("tag", "--no-create-reflog"): "R",  # T/P: creation modifier.
    ("tag", "--column"): "O",  # T: display.
    ("tag", "--no-column"): "R",  # T/P: display.
    ("tag", "--contains"): "F",  # T: filter.
    ("tag", "--no-contains"): "F",  # T: inverse filter.
    ("tag", "--merged"): "F",  # T: filter.
    ("tag", "--no-merged"): "F",  # T: inverse filter.
    ("tag", "--omit-empty"): "R",  # T: display.
    ("tag", "--no-omit-empty"): "R",  # T/P: display.
    ("tag", "--sort"): "D",  # T: sorting.
    ("tag", "--no-sort"): "R",  # T/P: sorting.
    ("tag", "--points-at"): "F",  # T: filter.
    ("tag", "--no-points-at"): "W",  # T/P: clears filter.
    ("tag", "--format"): "D",  # T: formatting.
    ("tag", "--no-format"): "R",  # T/P: formatting.
    ("tag", "--color"): "O",  # T: display.
    ("tag", "--no-color"): "R",  # T/P: display.
    ("tag", "--ignore-case"): "R",  # T: matching.
    ("tag", "--no-ignore-case"): "R",  # T/P: matching.
    ("tag", "-i"): "R",  # T: matching.
}


class CannotEvaluate(RuntimeError):
    """A required input cannot answer the check."""


def _literal(node):
    """Only dictionaries, strings, sequences and literal frozenset calls."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Dict):
        result = {}
        for key_node, value_node in zip(node.keys, node.values):
            if key_node is None:
                raise CannotEvaluate("dictionary unpacking is not a literal")
            key = _literal(key_node)
            if not isinstance(key, str):
                raise CannotEvaluate("dictionary key must be a string")
            if key in result:
                raise CannotEvaluate("duplicate _GIT_REF_SPECS key {!r}".format(key))
            result[key] = _literal(value_node)
        return result
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(_literal(item) for item in node.elts)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "frozenset" and not node.keywords
            and len(node.args) <= 1):
        items = _literal(node.args[0]) if node.args else ()
        if not isinstance(items, (str, tuple)):
            raise CannotEvaluate("frozenset requires a literal string or sequence")
        if any(not isinstance(item, str) for item in items):
            raise CannotEvaluate("frozenset entries must be strings")
        if len(items) != len(set(items)):
            raise CannotEvaluate("duplicate frozenset member")
        return frozenset(items)
    raise CannotEvaluate("unsupported literal node {}".format(type(node).__name__))


def parse_hook(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise CannotEvaluate("hook AST: {}".format(exc))
    bindings = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "_GIT_REF_SPECS"
        and isinstance(node.ctx, (ast.Store, ast.Del))
    ]
    assignments = [
        node for node in tree.body if isinstance(node, ast.Assign)
        and len(node.targets) == 1 and node.targets[0] in bindings
    ]
    if len(bindings) != 1 or len(assignments) != 1:
        raise CannotEvaluate("expected one module-level _GIT_REF_SPECS assignment")
    try:
        specs = _literal(assignments[0].value)
    except RecursionError as exc:
        raise CannotEvaluate("hook literal nesting: {}".format(exc))
    if not isinstance(specs, dict) or set(specs) != set(SUBCOMMANDS):
        raise CannotEvaluate("_GIT_REF_SPECS must contain exactly branch and tag")
    result = {}
    for sub in SUBCOMMANDS:
        spec = specs[sub]
        if not isinstance(spec, dict) or set(spec) != {"long", *SHORT_FIELDS}:
            raise CannotEvaluate("{}: malformed hook fields".format(sub))
        longs = spec["long"]
        if not isinstance(longs, dict) or not longs:
            raise CannotEvaluate("{}: missing/empty long map".format(sub))
        roles = {}
        for option, role in longs.items():
            if (not LONG_NAME.fullmatch(option) or not isinstance(role, str)
                    or role not in LONG_ROLES):
                raise CannotEvaluate("{}: malformed role for {!r}".format(sub, option))
            roles[option] = role
        for field, role in SHORT_FIELDS.items():
            chars = spec[field]
            if not isinstance(chars, frozenset):
                raise CannotEvaluate("{}: {} must be frozenset".format(sub, field))
            for char in chars:
                option = "-" + char
                if not SHORT_NAME.fullmatch(option) or option in roles:
                    raise CannotEvaluate("{}: malformed/overlapping short {!r}".format(sub, char))
                roles[option] = role
        result[sub] = roles
    return result


def parse_help(sub, status, stdout, stderr):
    text = stdout + "\n" + stderr
    if status != 129 or not re.search(r"^usage: git " + sub + r"\b", text, re.M):
        raise CannotEvaluate("{}: PRIMARY expected usage and exit 129, got {}".format(sub, status))
    options = set()

    def collect(declaration, strict=False):
        matches = list(HELP_OPTION.finditer(declaration))
        if strict:
            if not matches or matches[0].start() != 0:
                raise CannotEvaluate("{}: PRIMARY malformed row {!r}".format(sub, declaration))
            starts = {match.start() for match in matches}
            for token in re.finditer(r"(?<![\w-])-", declaration):
                if token.start() not in starts:
                    raise CannotEvaluate("{}: PRIMARY unparsed spelling {!r}".format(
                        sub, declaration))
        for match in matches:
            negative, name, short = match.groups()
            if short:
                options.add(short)
            else:
                options.add("--" + name)
                if negative:
                    options.add("--no-" + name)

    rows = 0
    synopsis = False
    for line in text.splitlines():
        if re.match(r"^(?:usage:|   or:) git " + sub + r"\b", line):
            synopsis = True
        if synopsis:
            if not line.strip():
                synopsis = False
            else:
                collect(line)
                continue
        if not line.lstrip().startswith("-"):
            continue
        if not line.startswith("    -"):
            raise CannotEvaluate("{}: PRIMARY unrecognized option row {!r}".format(sub, line))
        # Commas join aliases even when padding would otherwise end a column.
        declaration = re.sub(r",\s+(?=-)", ", ", line.strip())
        declaration = re.split(r"\s{2,}", declaration, maxsplit=1)[0]
        collect(declaration, strict=True)
        rows += 1
    if rows < MIN_OPTIONS or len(options) < MIN_OPTIONS:
        raise CannotEvaluate("{}: PRIMARY empty/below-floor option table".format(sub))
    return options


def parse_completion(stdout):
    tokens = stdout.split()
    if not tokens:
        raise CannotEvaluate("VALUE-FORM empty completion")
    required = set()
    for token in tokens:
        if token == "--":
            continue
        option = token[:-1] if token.endswith("=") else token
        if not LONG_NAME.fullmatch(option):
            raise CannotEvaluate("malformed completion token {!r}".format(token))
        if token.endswith("="):
            required.add(option)
    if not required:
        raise CannotEvaluate("VALUE-FORM completion has no value forms")
    return required


def compare(sub, enumerated, hook, catalog, required=()):
    issues = []
    for option in sorted(set(enumerated) | set(required)):
        role = hook.get(option)
        if role is None:
            issues.append((1, "{} {}: MEMBERSHIP missing from hook".format(sub, option)))
        known = catalog.get((sub, option))
        if known not in ROLE_VERDICTS:
            issues.append((2, "{} {}: UNKNOWN ROLE; independent catalog review required".format(
                sub, option)))
        if role is not None and role not in ROLE_VERDICTS:
            issues.append((2, "{} {}: UNKNOWN HOOK ROLE {!r}".format(sub, option, role)))
        elif (known in ROLE_VERDICTS and role in ROLE_VERDICTS and role != "W"
              and ROLE_VERDICTS[known] != ROLE_VERDICTS[role]):
            issues.append((1, "{} {}: MIS-ROLE catalog {}, hook {}".format(
                sub, option, known, role)))
    # The secondary source only adds checks, including any helper-only value form.
    for option in sorted(required):
        role = hook.get(option)
        if role is not None and role not in ROLE_VERDICTS:
            continue
        if role not in VALUE_ROLES:
            issues.append((1, "{} {}: VALUE-FORM requires_arg=True, hook {}".format(
                sub, option, role if role is not None else "MISSING")))
    return issues


def verdict(issues):
    return max((code for code, _ in issues), default=0)


def _git_env(cwd):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CEILING_DIRECTORIES": str(Path(cwd).parent),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
        "LC_ALL": "C",
    })
    return env


def _git(executable, cwd, env, *args):
    try:
        return subprocess.run(
            [executable, *args], cwd=cwd, env=env, capture_output=True,
            text=True, encoding="utf-8", errors="strict", timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise CannotEvaluate("git {}: {}".format(" ".join(args), exc))


def init_repo(executable, cwd, env):
    # Explicit target; no inherited templates or GIT_* repository selection.
    result = _git(executable, cwd, env, "init", "-q", "--template=", str(cwd))
    if result.returncode != 0:
        raise CannotEvaluate("scratch git init failed: {}".format(result.stderr.strip()))


def value_forms(executable, cwd, env, sub):
    result = _git(executable, cwd, env, sub, "--git-completion-helper")
    if result.returncode != 0:
        raise CannotEvaluate("{}: VALUE-FORM helper exit {}".format(sub, result.returncode))
    return parse_completion(result.stdout)


def run(root):
    issues = []
    try:
        source_path = root.resolve(strict=True) / SOURCE_REL
        hook = parse_hook(source_path.read_text(encoding="utf-8"))
        found = shutil.which("git")
        if found is None:
            raise CannotEvaluate("git executable not found")
        executable = str(Path(found).resolve(strict=True))
        with tempfile.TemporaryDirectory(prefix="aiqt-git-options-") as cwd:
            env = _git_env(cwd)
            init_repo(executable, cwd, env)
            version = _git(executable, cwd, env, "--version")
            if version.returncode != 0 or not re.fullmatch(
                    r"git version [^\r\n]+", version.stdout.strip()):
                raise CannotEvaluate("git --version unavailable or malformed")
            print("git-option-table: {} ({})".format(version.stdout.strip(), executable))
            print("hook source: {}".format(source_path))
            for sub in SUBCOMMANDS:
                try:
                    help_result = _git(executable, cwd, env, sub, "-h")
                    enumerated = parse_help(
                        sub, help_result.returncode, help_result.stdout, help_result.stderr)
                except CannotEvaluate as exc:
                    issues.append((2, str(exc)))
                    continue
                required = None
                try:
                    required = value_forms(executable, cwd, env, sub)
                except CannotEvaluate as exc:
                    issues.append((2, "{}: {}".format(sub, exc)))
                issues.extend(compare(sub, enumerated, hook[sub], ROLE_CATALOG, required or ()))
                print("{}: PRIMARY spellings: {}".format(sub, " ".join(sorted(enumerated))))
                if required is not None:
                    print("{}: helper requires_arg: {}".format(sub, " ".join(sorted(required)) or "(none)"))
    except (CannotEvaluate, OSError, UnicodeError, RuntimeError) as exc:
        issues.append((2, str(exc)))
    for code, detail in issues:
        print("{}: git-option-table: {}".format("ERROR" if code == 2 else "VIOLATION", detail),
              file=sys.stderr)
    code = verdict(issues)
    if code == 0:
        print("PASS: git-option-table: enumerated membership and catalog checks; see residuals.")
    return code


def self_test():
    failures = []

    def case(label, expected, probe):
        try:
            got = probe()
        except CannotEvaluate:
            got = 2
        if got != expected:
            failures.append("{}: expected {}, got {}".format(label, expected, got))

    def check(label, expected, e, h, c, required=()):
        case(label, expected, lambda: verdict(compare("branch", e, h, c, required)))

    def catalog(**roles):
        return {("branch", "--" + name): role for name, role in roles.items()}

    # a: Add the missing exact spelling to H.
    check("a fail", 1, {"--x"}, {}, catalog(x="R"))
    check("a repair", 0, {"--x"}, {"--x": "R"}, catalog(x="R"))
    # b: Add the independently reviewed spelling to C and H.
    check("b fail", 2, {"--future"}, {}, {})
    check("b repair", 0, {"--future"}, {"--future": "W"}, catalog(future="W"))
    check("b known-to-H-only", 2, {"--future"}, {"--future": "W"}, {})
    # c: Change H's delete role from R to W.
    check("c fail", 1, {"--delete"}, {"--delete": "R"}, catalog(delete="W"))
    check("c repair", 0, {"--delete"}, {"--delete": "W"}, catalog(delete="W"))
    # d: Change H's non-consuming role from R to D; W also safely short-circuits.
    required = parse_completion("--format=")
    check("d fail", 1, {"--format"}, {"--format": "R"}, catalog(format="D"), required)
    check("d repair", 0, {"--format"}, {"--format": "D"}, catalog(format="D"), required)
    check("d conservative", 0, {"--format"}, {"--format": "W"}, catalog(format="D"), required)
    # e: Extra H members and conservative W over-roles do not cause findings.
    check("e superset", 0, {"--list"}, {"--list": "L", "--future-known": "W"},
          catalog(list="L"))
    check("e conservative", 0, {"--list"}, {"--list": "W"}, catalog(list="L"))
    # f: Restore a complete fixture table, exercising stdout and stderr paths.
    good_help = (
        "usage: git branch [<options>]\n\n"
        "    -l, --list           list\n"
        "    --[no-]force         force\n"
        "    --format <format>    format\n"
        "    --quiet              quiet\n"
        "    --verbose            verbose\n"
    )

    def help_code(status, out, err=""):
        parse_help("branch", status, out, err)
        return 0

    case("f empty", 2, lambda: help_code(129, ""))
    case("f floor", 2, lambda: help_code(129, "usage: git branch\n    --list    list\n"))
    case("f repair stdout", 0, lambda: help_code(129, good_help))
    case("f repair stderr", 0, lambda: help_code(129, "", good_help))
    case("f wrong status", 2, lambda: help_code(0, good_help))
    case("f malformed spelling", 2, lambda: help_code(129, good_help + "    --BAD    bad\n"))
    parsed = parse_help("branch", 129, good_help, "")
    if parsed != {"-l", "--list", "--force", "--no-force", "--format", "--quiet", "--verbose"}:
        failures.append("f: wrong exact spelling expansion")
    # g: Remove a duplicate or replace malformed syntax with this valid literal.
    spec = (
        "{'long': {'--list': 'L'}, 'short_write': frozenset(), "
        "'short_list': frozenset('l'), 'short_read': frozenset(), "
        "'short_optnum': frozenset()}"
    )
    good_source = "_GIT_REF_SPECS = {'branch': " + spec + ", 'tag': " + spec + "}"

    def source_code(source):
        parse_hook(source)
        return 0

    case("g duplicate nested", 2, lambda: source_code(
        good_source.replace("'--list': 'L'", "'--list': 'L', '--list': 'W'", 1)))
    case("g duplicate sub", 2, lambda: source_code(
        good_source.replace("'tag':", "'branch':")))
    case("g malformed AST", 2, lambda: source_code("_GIT_REF_SPECS = {"))
    case("g missing", 2, lambda: source_code("OTHER = {}"))
    case("g duplicate assignment", 2, lambda: source_code(good_source + "\n" + good_source))
    case("g executable expression", 2, lambda: source_code(
        good_source.replace("frozenset()", "__import__('os')", 1)))
    case("g bad role", 2, lambda: source_code(good_source.replace("'L'", "'X'", 1)))
    case("g repair", 0, lambda: source_code(good_source))
    # h: Add the full write spelling; a read prefix must not satisfy membership.
    e = {"--list", "--list-rewrite"}
    c = {("branch", "--list"): "L", ("branch", "--list-rewrite"): "W"}
    check("h fail", 1, e, {"--list": "L"}, c)
    check("h repair", 0, e, {"--list": "L", "--list-rewrite": "W"}, c)
    # Short membership and write-role coverage use the same comparator.
    check("short missing", 1, {"-d"}, {}, {("branch", "-d"): "W"})
    check("short mis-role", 1, {"-d"}, {"-d": "R"}, {("branch", "-d"): "W"})
    check("short repair", 0, {"-d"}, {"-d": "W"}, {("branch", "-d"): "W"})
    case("empty completion", 2, lambda: (parse_completion(""), 0)[1])
    case("no value forms", 2, lambda: (parse_completion("--list --"), 0)[1])
    case("malformed completion", 2, lambda: (parse_completion("--bad???"), 0)[1])

    # R2-1: real catalog entries, not fixture copies of the hook table.
    check("r2 quiet R->D", 1, {"--quiet"}, {"--quiet": "D"}, ROLE_CATALOG)
    check("r2 format D->F", 1, {"--format"}, {"--format": "F"}, ROLE_CATALOG)
    check("r2 quiet correct", 0, {"--quiet"}, {"--quiet": "R"}, ROLE_CATALOG)
    check("r2 format correct", 0, {"--format"}, {"--format": "D"}, ROLE_CATALOG)
    check("r2 conservative W", 0, {"--quiet"}, {"--quiet": "W"}, ROLE_CATALOG)
    check("r2 unmapped role", 2, {"--quiet"}, {"--quiet": "X"}, ROLE_CATALOG)

    check("r2 helper-only unknown option", 2, set(), {"--future": "W"}, {}, {"--future"})
    check("r2 helper-only wrong role", 1, set(), {"--format": "F"},
          ROLE_CATALOG, {"--format"})

    # R2-3: parser -> comparator, including stdout, stderr and synopsis paths.
    baseline = {
        "-l": "L", "--list": "L", "--force": "W", "--no-force": "W",
        "--format": "D", "--quiet": "R", "--verbose": "R",
        "--delete": "W", "--no-delete": "W",
    }

    def short_help_code(text, roles, stderr=False):
        parsed = parse_help("branch", 129, "" if stderr else text, text if stderr else "")
        return verdict(compare("branch", parsed, roles, ROLE_CATALOG))

    reordered = good_help + "    --[no-]delete,  -d    delete\n"
    case("r2 reordered missing short", 1, lambda: short_help_code(reordered, baseline))
    case("r2 reordered short repair", 0, lambda: short_help_code(
        reordered, dict(baseline, **{"-d": "W"}), stderr=True))
    case("r2 reordered short mis-role", 1, lambda: short_help_code(
        reordered, dict(baseline, **{"-d": "R"})))
    aliases = good_help + "    -d,  -D, --[no-]delete    delete\n"
    case("r2 multiple short aliases", 1, lambda: short_help_code(
        aliases, dict(baseline, **{"-d": "W"})))
    case("r2 unknown short alias", 2, lambda: short_help_code(
        good_help + "    --quiet,  -Z    quiet\n", baseline))
    synopsis = good_help.replace("[<options>]", "[-d]")
    case("r2 synopsis missing short", 1, lambda: short_help_code(synopsis, baseline))
    case("r2 synopsis unknown short", 2, lambda: short_help_code(
        good_help.replace("[<options>]", "[-Z]"), baseline))

    # R2-2: run the production path against a generated hook fixture and real
    # git. Require the value diagnostic so MIS-ROLE cannot mask a skipped leg.
    def completion_code():
        specs = []
        for sub in SUBCOMMANDS:
            roles = {opt: role for (cmd, opt), role in ROLE_CATALOG.items() if cmd == sub}
            longs = {opt: role for opt, role in roles.items() if opt.startswith("--")}
            longs["--format"] = "R"
            fields = ["'long': " + repr(longs)]
            for field, role in SHORT_FIELDS.items():
                chars = "".join(sorted(opt[1:] for opt, known in roles.items()
                                       if SHORT_NAME.fullmatch(opt) and known == role))
                fields.append("{!r}: frozenset({!r})".format(field, chars))
            specs.append("{!r}: {{{}}}".format(sub, ", ".join(fields)))
        fixture = "_GIT_REF_SPECS = {" + ", ".join(specs) + "}\n"
        with tempfile.TemporaryDirectory(prefix="aiqt-git-options-test-") as cwd:
            root = Path(cwd)
            path = root / SOURCE_REL
            path.parent.mkdir(parents=True)
            path.write_text(fixture, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = run(root)
            if code != 1:
                return code
            for sub in SUBCOMMANDS:
                if "{} --format: VALUE-FORM".format(sub) not in err.getvalue():
                    return 0
        return 1

    case("r2 initialized helper format D->R", 1, completion_code)
    if failures:
        print("SELF-TEST FAIL:\n  " + "\n  ".join(failures))
        return 1
    print("SELF-TEST PASS: a-h, R2 role profiles, short help and initialized git helpers.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check installed git branch/tag option-table drift.")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--self-test", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    if args.self_test:
        if args.root is not None:
            parser.print_usage(sys.stderr)
            return 2
        return self_test()
    try:
        root = args.root if args.root is not None else Path(__file__).resolve().parent.parent
        return run(root)
    except (OSError, RuntimeError) as exc:
        print("ERROR: git-option-table cannot evaluate: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
