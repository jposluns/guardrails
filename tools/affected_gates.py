#!/usr/bin/env python3
"""Affected-gate selector: list every gate of tools/run_all_checks.sh whose declared inputs a change
touches, so the member checks run before a merge train are derived from the changed paths and never
chosen by hand.

  affected_gates.py --base REV [--head REV] [--json | --explain]
  affected_gates.py --self-test
  affected_gates.py [--self-test] --execution-report ABS_PATH   (the registered suite launch)

The selector lists gates; it runs none of them and certifies nothing. The full runner stays the
authority for a merge: rerun the selection after a rebase or any input change, and keep the train's
full run required before merge.

ROSTER. The gate roster is read from tools/run_all_checks.sh through the parity gate's own validated
loader (check_ci_parity.extract_local); the runner is never sourced. Any loader diagnostic is
cannot-evaluate. The ordered records are then read from the same text: every run_gate line, the one
reviewed baseline-pin probe block as ONE logical gate (baseline-pin) carrying both of its command
variants and its probe prerequisite, and the inline gitleaks scan as the logical gate
"secrets (gitleaks)". The set of command identities so read must equal the loader's member set
exactly, so an executable check this reader did not account for is cannot-evaluate, and a gate name
repeated outside the baseline block is cannot-evaluate too.

DECLARATIONS. tools/affected_gates.toml declares, for every runner gate and for no other name, its
inputs: a list of globs (inputs), whole-tree = true (any changed path selects it) and/or always = true
(selected whatever changed: prerequisites, the runner's mandatory member checks, and gates whose
state no changed path represents, such as git history, installed tools or the host). Each entry
carries a reason. The declaration set must equal the roster exactly: a gate with no declaration, or
a declaration naming no gate, is cannot-evaluate, never an empty input set. full-roster globs (the
runner, this selector, its declarations and the roster loader) select every gate. domain rows name
the reviewed path domains (a directory tree, written with a trailing slash, or one root-level file).

GLOBS. Repository-relative POSIX paths, matched case-sensitively as whole strings: * and ? stay
inside one path component, a ** component spans zero or more components; no character classes.
Deleted paths are matched as strings; a path need not exist.

CHANGES. REV and HEAD resolve to commits, and the comparison is their unique merge base to HEAD,
unioned (without --head) with the staged changes, the unstaged changes and the untracked files that
the repository's own ignore rules do not exclude. With --head REV the comparison is that merge base
to REV only (no working tree), for judging a past change. Every git call is checked, NUL-delimited,
--no-renames (both sides of a rename are changed paths), with external diff and textconv off, the
inherited GIT_* environment dropped, the global and system git configuration and the user-wide
excludes file suppressed, and nothing is fetched. Additions, deletions, mode changes and type
changes are all changed paths; a submodule (gitlink) change selects the whole roster. A missing or
ambiguous revision, a shallow repository, no or several merge bases, an unmerged index entry, a
failed git call, an unreadable declaration and a path that is not strict UTF-8 are cannot-evaluate.
NOT MEASURED: ignored files and the external environment are not part of the change set, so a
selection is no evidence that those inputs are clean.

UNKNOWN PATHS. Every changed path must lie in a reviewed domain before any selection is released.
Matching a whole-tree gate does not cover a path: a path outside every domain is cannot-evaluate even
though the whole-tree gates would select themselves. There is no switch that allows it.

OUTPUT. Default: the selected gate names, one per line, deduplicated, in runner order. --explain adds
the reasons (the matched path and declaration, whole-tree, always, full-roster). --json prints one
object (schema 1) with the resolved base, merge base and head, the changed paths, the ordered
selection with each gate's command variants and reasons, and diagnostics; on failure its selected
member is null. Conditional gates keep their runner behaviour: baseline-pin lists both variants and
its probe, and is run by the runner's block, never as a bare command.

EXIT. 0 selection complete (an empty change set included: the always gates are then the selection);
2 cannot evaluate, invalid invocation, unknown path, or an invalid roster or declaration (no gate
name is printed on stdout); 1 only for a failed --self-test assertion. Callers check the exit code
before reading the output.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: affected_gates.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import contextlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections import namedtuple
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: affected_gates.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_ci_parity  # noqa: E402  (the validated runner loader; never a second parser)

ROOT = Path(__file__).resolve().parents[1]
RUNNER_REL = "tools/run_all_checks.sh"
MANIFEST_REL = "tools/affected_gates.toml"
SUITES_REL = "tools/selftest_checks.toml"
SELF_REL = "tools/affected_gates.py"
SCHEMA = 1
MANIFEST_FORMAT = 1
READ_LIMIT = 4 * 1024 * 1024
GITLEAKS_GATE = "secrets (gitleaks)"
BASELINE_GATE = "baseline-pin"
SUITE_GATE_SCRIPT = "tools/check_selftest_execution.py"
MANIFEST_TOP_KEYS = frozenset({"format-version", "full-roster", "domain", "gate"})
GATE_KEYS = frozenset({"inputs", "whole-tree", "always", "reason"})
DOMAIN_KEYS = frozenset({"path", "owner"})
GITLINK_MODE = "160000"
NULL_SHA_RE = re.compile(r"0+")
RAW_HEADER_RE = re.compile(r":([0-7]{6}) ([0-7]{6}) ([0-9a-f]+) ([0-9a-f]+) ([ACDMTUX])[0-9]*")
HEX_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
GLOB_COMPONENT_RE = re.compile(r"[A-Za-z0-9._*?@+ -]+")

Gate = namedtuple("Gate", "name line kind variants prerequisite")
Declaration = namedtuple("Declaration", "inputs whole_tree always reason")
Manifest = namedtuple("Manifest", "full_roster domains gates")
Change = namedtuple("Change", "path sources gitlink")


class CannotEvaluate(Exception):
    """An input the selector cannot judge: exit 2, never a partial selection."""


def _read_bounded(path):
    """The bytes of one regular, non-symlink file, at most READ_LIMIT of them, or CannotEvaluate."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        raise CannotEvaluate("cannot stat {} ({})".format(path, type(exc).__name__))
    if not stat.S_ISREG(st.st_mode):
        raise CannotEvaluate("{} is not a regular file".format(path))
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise CannotEvaluate("cannot open {} ({})".format(path, type(exc).__name__))
    try:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(READ_LIMIT + 1)
    except OSError as exc:
        raise CannotEvaluate("cannot read {} ({})".format(path, type(exc).__name__))
    if len(data) > READ_LIMIT:
        raise CannotEvaluate("{} is larger than {} bytes".format(path, READ_LIMIT))
    return data


def _parse_toml(text):
    """(document, None) or (None, reason): every parse failure is handled here."""
    try:
        return tomllib.loads(text), None
    except (RecursionError, MemoryError, ValueError) as exc:
        return None, "{}: {}".format(type(exc).__name__, exc)


# ---------------------------------------------------------------------------------------------
# Roster

def _code_lines(text):
    return [(number, check_ci_parity._strip_comment(raw).strip())
            for number, raw in enumerate(text.splitlines(), 1)]


def parse_roster(text):
    """The ordered gate records of runner text, or CannotEvaluate."""
    extraction = check_ci_parity.extract_local(text)
    if extraction.diagnostics:
        raise CannotEvaluate("runner refused by the parity loader: " + "; ".join(
            "line {} {}: {}".format(d.line, d.code, d.message) for d in extraction.diagnostics))
    executable = [(number, code) for number, code in _code_lines(text) if code]
    probe_lines, probe_count = check_ci_parity._baseline_probe_lines(executable)
    probe_set = frozenset(probe_lines)
    gates = []
    by_name = {}
    identities = set()
    prerequisite = None
    for number, code in executable:
        if number in probe_set and code == check_ci_parity.BASELINE_PROBE_LAUNCH:
            prerequisite = tuple(check_ci_parity._tokenize(code).value)
            continue
        tokenized = check_ci_parity._tokenize(code)
        if not tokenized.ok or not tokenized.value:
            continue
        tokens = tokenized.value
        if tokens[0] == "run_gate":
            if len(tokens) < 3:
                raise CannotEvaluate("line {}: run_gate without a label and command".format(number))
            name, argv = tokens[1], tuple(tokens[2:])
            kind = "baseline-block" if number in probe_set else "run_gate"
        elif (tokens[0] == "if" and len(tokens) >= 4 and tokens[-2:] == [";", "then"]
              and tokens[1].lstrip("./") == "gitleaks"):
            name, argv, kind = GITLEAKS_GATE, tuple(tokens[1:-2]), "inline-gitleaks"
        else:
            continue
        normalized = check_ci_parity.normalize(list(argv))
        if not normalized.ok:
            raise CannotEvaluate("line {}: {}".format(number, normalized.message))
        identities.add(normalized.value)
        if name in by_name:
            index = by_name[name]
            if kind != "baseline-block" or gates[index].kind != "baseline-block":
                raise CannotEvaluate("line {}: gate name {!r} repeated".format(number, name))
            old = gates[index]
            gates[index] = old._replace(variants=old.variants + (argv,))
            continue
        by_name[name] = len(gates)
        gates.append(Gate(name, number, kind, (argv,), None))
    if identities != set(extraction.members):
        raise CannotEvaluate("runner checks not accounted for by the roster reader: {}".format(
            sorted(set(extraction.members) ^ identities)))
    if probe_count == 1:
        if BASELINE_GATE not in by_name or prerequisite is None:
            raise CannotEvaluate("the baseline-pin probe block holds no baseline-pin gate")
        index = by_name[BASELINE_GATE]
        if len(gates[index].variants) != 2:
            raise CannotEvaluate("baseline-pin must carry exactly its two block variants")
        gates[index] = gates[index]._replace(prerequisite=prerequisite)
    if not gates:
        raise CannotEvaluate("the runner holds no gate")
    return tuple(gates)


# ---------------------------------------------------------------------------------------------
# Declarations

def _valid_glob(pattern):
    if not isinstance(pattern, str) or not pattern or pattern.startswith("/") or pattern.endswith("/"):
        return False
    for component in pattern.split("/"):
        if component in ("", ".", ".."):
            return False
        if "**" in component and component != "**":
            return False
        if not GLOB_COMPONENT_RE.fullmatch(component):
            return False
    return True


def glob_regex(pattern):
    """The compiled whole-path matcher of one validated glob (module docstring, GLOBS)."""
    parts = pattern.split("/")
    out = []
    for index, component in enumerate(parts):
        last = index == len(parts) - 1
        if component == "**":
            out.append("(?:[^/]+(?:/[^/]+)*)?" if last else "(?:[^/]+/)*")
            continue
        piece = "".join("[^/]*" if ch == "*" else "[^/]" if ch == "?" else re.escape(ch)
                        for ch in component)
        out.append(piece if last else piece + "/")
    return re.compile("".join(out), re.DOTALL)


def _globs(value, where):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CannotEvaluate("{}: must be a list of strings".format(where))
    bad = [item for item in value if not _valid_glob(item)]
    if bad:
        raise CannotEvaluate("{}: invalid glob(s) {}".format(where, bad))
    if len(set(value)) != len(value):
        raise CannotEvaluate("{}: repeated glob".format(where))
    return tuple((item, glob_regex(item)) for item in value)


def load_manifest(data):
    """A validated Manifest from a parsed declaration document, or CannotEvaluate."""
    if not isinstance(data, dict):
        raise CannotEvaluate("declarations: not a table")
    unknown = set(data) - MANIFEST_TOP_KEYS
    if unknown or set(data) != MANIFEST_TOP_KEYS:
        raise CannotEvaluate("declarations: top-level keys must be exactly {} (got {})".format(
            sorted(MANIFEST_TOP_KEYS), sorted(data)))
    if data["format-version"] != MANIFEST_FORMAT or isinstance(data["format-version"], bool):
        raise CannotEvaluate("declarations: format-version must be {}".format(MANIFEST_FORMAT))
    full = _globs(data["full-roster"], "full-roster")
    if not full:
        raise CannotEvaluate("full-roster: must not be empty")
    domains = []
    if not isinstance(data["domain"], list) or not data["domain"]:
        raise CannotEvaluate("domain: must be a non-empty array of tables")
    for row in data["domain"]:
        if not isinstance(row, dict) or set(row) != DOMAIN_KEYS:
            raise CannotEvaluate("domain row must have exactly {}: {!r}".format(sorted(DOMAIN_KEYS), row))
        path, owner = row["path"], row["owner"]
        if not isinstance(owner, str) or not owner.strip():
            raise CannotEvaluate("domain {!r}: owner must be a non-empty string".format(path))
        if not isinstance(path, str) or not _valid_glob(path.rstrip("/")) or "*" in path or "?" in path:
            raise CannotEvaluate("domain {!r}: must be a literal directory/ or root-level file".format(path))
        if not path.endswith("/") and "/" in path:
            raise CannotEvaluate("domain {!r}: a file domain must be root-level".format(path))
        if path in (d for d in domains):
            raise CannotEvaluate("domain {!r}: repeated".format(path))
        domains.append(path)
    gates = {}
    if not isinstance(data["gate"], dict) or not data["gate"]:
        raise CannotEvaluate("gate: must be a non-empty table")
    for name, entry in data["gate"].items():
        where = "gate {!r}".format(name)
        if not isinstance(entry, dict):
            raise CannotEvaluate("{}: must be a table".format(where))
        unknown = set(entry) - GATE_KEYS
        if unknown:
            raise CannotEvaluate("{}: unknown key(s) {}".format(where, sorted(unknown)))
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise CannotEvaluate("{}: reason must be a non-empty string".format(where))
        whole, always = entry.get("whole-tree", False), entry.get("always", False)
        if not isinstance(whole, bool) or not isinstance(always, bool):
            raise CannotEvaluate("{}: whole-tree and always must be booleans".format(where))
        inputs = _globs(entry.get("inputs", []), where + " inputs")
        if not (inputs or whole or always):
            raise CannotEvaluate("{}: declares no input (an empty declaration is never accepted)".format(where))
        gates[name] = Declaration(inputs, whole, always, reason)
    return Manifest(full, tuple(domains), gates)


def check_roster_equality(gates, manifest):
    """CannotEvaluate unless every roster gate has one declaration and every declaration a gate."""
    names = [gate.name for gate in gates]
    missing = [name for name in names if name not in manifest.gates]
    extra = sorted(set(manifest.gates) - set(names))
    if missing or extra:
        raise CannotEvaluate("declarations differ from the runner roster: missing {}; no such gate {}".format(
            missing, extra))


def read_inputs(root):
    """(gates, manifest) for the tree at root, or CannotEvaluate."""
    runner = _read_bounded(Path(root) / RUNNER_REL)
    text, diagnostic = check_ci_parity.read_runner_text(
        Path(root) / RUNNER_REL, RUNNER_REL, reader=lambda _path: runner)
    if diagnostic is not None:
        raise CannotEvaluate("{}: {}".format(RUNNER_REL, diagnostic.message))
    gates = parse_roster(text)
    raw = _read_bounded(Path(root) / MANIFEST_REL)
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CannotEvaluate("{} is not UTF-8".format(MANIFEST_REL))
    data, problem = _parse_toml(decoded)
    if problem is not None:
        raise CannotEvaluate("{}: {}".format(MANIFEST_REL, problem))
    manifest = load_manifest(data)
    check_roster_equality(gates, manifest)
    return gates, manifest


# ---------------------------------------------------------------------------------------------
# Selection

def in_domain(path, domains):
    return any(path.startswith(d) if d.endswith("/") else path == d for d in domains)


def select(gates, manifest, changes):
    """(selected [(gate, reasons)], unknown paths). changes is a list of Change."""
    unknown = sorted(c.path for c in changes if not in_domain(c.path, manifest.domains))
    if unknown:
        return None, unknown
    full = []
    for change in changes:
        if change.gitlink:
            full.append("submodule change {}".format(change.path))
        for pattern, rx in manifest.full_roster:
            if rx.fullmatch(change.path):
                full.append("full-roster: {} matches {}".format(change.path, pattern))
    selected = []
    for gate in gates:
        decl = manifest.gates[gate.name]
        reasons = list(full)
        if decl.always:
            reasons.append(decl.reason if decl.reason.startswith("always") else "always: " + decl.reason)
        if decl.whole_tree and changes:
            reasons.append("whole-tree: {} changed path(s)".format(len(changes)))
        for change in changes:
            for pattern, rx in decl.inputs:
                if rx.fullmatch(change.path):
                    reasons.append("input: {} matches {}".format(change.path, pattern))
        if reasons:
            selected.append((gate, reasons))
    return selected, []


# ---------------------------------------------------------------------------------------------
# Changes

def git_env(base_env=None):
    env = {k: v for k, v in (os.environ if base_env is None else base_env).items()
           if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    return env


def _git(root, args, env):
    argv = ["git", "--no-optional-locks", "-c", "core.excludesFile=" + os.devnull,
            "-c", "core.quotePath=false", "-c", "diff.external=", "-C", str(root)] + list(args)
    try:
        done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, env=env, check=False)
    except OSError as exc:
        raise CannotEvaluate("cannot run git ({})".format(type(exc).__name__))
    if done.returncode != 0:
        raise CannotEvaluate("git {} failed (exit {}): {}".format(
            args[0], done.returncode, done.stderr.decode("utf-8", "replace").strip()[:400]))
    return done.stdout, done.stderr


def _resolve(root, rev, env):
    if not isinstance(rev, str) or not rev or rev.startswith("-"):
        raise CannotEvaluate("revision {!r} is not accepted".format(rev))
    out, err = _git(root, ["rev-parse", "--verify", "--end-of-options", rev + "^{commit}"], env)
    if err.strip():
        raise CannotEvaluate("revision {!r} is ambiguous or warned: {}".format(
            rev, err.decode("utf-8", "replace").strip()[:400]))
    value = out.decode("ascii", "replace").strip()
    if not HEX_RE.fullmatch(value):
        raise CannotEvaluate("revision {!r} did not resolve to one commit".format(rev))
    return value


def _decode_path(raw):
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        raise CannotEvaluate("changed path {!r} is not strict UTF-8".format(raw))


def parse_raw_diff(blob, source):
    """Changes from `git diff --raw -z --no-renames` output, or CannotEvaluate."""
    fields = blob.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise CannotEvaluate("{}: malformed raw diff".format(source))
    changes = []
    for header, raw_path in zip(fields[0::2], fields[1::2]):
        match = RAW_HEADER_RE.fullmatch(header.decode("ascii", "replace"))
        if match is None or not raw_path:
            raise CannotEvaluate("{}: unrecognized raw diff record {!r}".format(source, header))
        if match.group(5) in "UX":
            raise CannotEvaluate("{}: unmerged or unknown entry {!r}".format(source, raw_path))
        gitlink = GITLINK_MODE in (match.group(1), match.group(2))
        changes.append(Change(_decode_path(raw_path), (source,), gitlink))
    return changes


def collect_changes(root, base, head_rev=None, env=None):
    """(info dict, [Change]) for the comparison in the module docstring, or CannotEvaluate."""
    env = git_env() if env is None else env
    out, _err = _git(root, ["rev-parse", "--is-shallow-repository"], env)
    if out.strip() != b"false":
        raise CannotEvaluate("shallow or unreadable history: merge base cannot be trusted")
    base_id = _resolve(root, base, env)
    head_id = _resolve(root, "HEAD" if head_rev is None else head_rev, env)
    out, _err = _git(root, ["merge-base", "--all", base_id, head_id], env)
    bases = out.decode("ascii", "replace").split()
    if len(bases) != 1 or not HEX_RE.fullmatch(bases[0]):
        raise CannotEvaluate("expected one merge base of {} and {}, found {}".format(
            base_id, head_id, len(bases)))
    diff = ["diff", "--raw", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", "--abbrev=40",
            "--ignore-submodules=none"]
    changes = parse_raw_diff(_git(root, diff + [bases[0], head_id, "--"], env)[0], "committed")
    if head_rev is None:
        out, _err = _git(root, ["ls-files", "-z", "--unmerged"], env)
        if out:
            raise CannotEvaluate("the index holds unmerged entries")
        changes += parse_raw_diff(_git(root, diff + ["--cached", head_id, "--"], env)[0], "staged")
        changes += parse_raw_diff(_git(root, diff + ["--"], env)[0], "unstaged")
        out, _err = _git(root, ["ls-files", "-z", "--others", "--exclude-standard"], env)
        for raw_path in [p for p in out.split(b"\0") if p]:
            changes.append(Change(_decode_path(raw_path), ("untracked",), False))
    merged = {}
    for change in changes:
        if change.path in merged:
            old = merged[change.path]
            merged[change.path] = Change(change.path, tuple(sorted(set(old.sources + change.sources))),
                                         old.gitlink or change.gitlink)
        else:
            merged[change.path] = change
    info = {"base": base_id, "merge_base": bases[0], "head": head_id,
            "mode": "worktree" if head_rev is None else "commit"}
    return info, [merged[p] for p in sorted(merged)]


# ---------------------------------------------------------------------------------------------
# Command line

def run(root, base, head_rev, form, out, err, env=None):
    """The selection exit code (0 or 2), output written to out and diagnostics to err."""
    info = {"base": None, "merge_base": None, "head": None, "mode": None}
    changes = []
    try:
        gates, manifest = read_inputs(root)
        info, changes = collect_changes(root, base, head_rev, env)
        selected, unknown = select(gates, manifest, changes)
        if unknown:
            raise CannotEvaluate("changed path(s) outside every reviewed domain: {}".format(unknown))
    except CannotEvaluate as exc:
        if form == "json":
            out.write(json.dumps(dict(info, schema=SCHEMA, status="cannot-evaluate", selected=None,
                                      changed=[c.path for c in changes],
                                      diagnostics=[str(exc)]), indent=1, sort_keys=True) + "\n")
        print("CANNOT EVALUATE: {}".format(exc), file=err)
        return 2
    if form == "json":
        out.write(json.dumps(dict(
            info, schema=SCHEMA, status="ok", diagnostics=[],
            changed=[{"path": c.path, "sources": list(c.sources), "submodule": c.gitlink} for c in changes],
            selected=[{"gate": g.name, "line": g.line, "kind": g.kind,
                       "variants": [list(v) for v in g.variants],
                       "prerequisite": None if g.prerequisite is None else list(g.prerequisite),
                       "reasons": reasons} for g, reasons in selected]),
            indent=1, sort_keys=True) + "\n")
    else:
        for gate, reasons in selected:
            out.write(gate.name + "\n")
            if form == "explain":
                if gate.prerequisite is not None:
                    out.write("  prerequisite: {}\n".format(" ".join(gate.prerequisite)))
                for reason in reasons:
                    out.write("  {}\n".format(reason))
    return 0


def _parse_cli(argv):
    """(mode, options) or None for an invalid invocation."""
    if argv and argv[0] == "--self-test" and len(argv) == 1:
        return "self-test", None
    options = {"base": None, "head": None, "form": "names"}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("--base", "--head") and index + 1 < len(argv):
            key = arg[2:]
            if options[key] is not None:
                return None
            options[key] = argv[index + 1]
            index += 2
        elif arg in ("--json", "--explain") and options["form"] == "names":
            options["form"] = arg[2:]
            index += 1
        else:
            return None
    if options["base"] is None:
        return None
    return "select", options


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    argv = list(argv)
    report_path = None
    if (len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1])
            and argv[:-2] in ([], ["--self-test"])):
        report_path, argv = argv[-1], ["--self-test"]
    parsed = _parse_cli(argv)
    if parsed is None:
        print("error: usage: affected_gates.py --base REV [--head REV] [--json | --explain] | --self-test",
              file=sys.stderr)
        return 2
    mode, options = parsed
    if mode == "self-test":
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return self_test()
    return run(ROOT, options["base"], options["head"], options["form"], sys.stdout, sys.stderr)


# ---------------------------------------------------------------------------------------------
# Self-test

SUITE_ID = "affected-gates-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _real_inputs():
    try:
        return read_inputs(ROOT), None
    except CannotEvaluate as exc:
        return (None, None), str(exc)


def _selected_names(gates, manifest, paths):
    selected, unknown = select(gates, manifest, [Change(p, ("committed",), False) for p in paths])
    return None if selected is None else [g.name for g, _r in selected]


def _narrowed(manifest, name):
    """manifest with gate name's declaration narrowed to its own script alone (a mutation)."""
    gates = dict(manifest.gates)
    gates[name] = Declaration((("tools/check_" + name.replace("-", "_") + ".py",
                                glob_regex("tools/check_" + name.replace("-", "_") + ".py")),),
                              False, False, "narrowed by the self-test")
    return manifest._replace(gates=gates)


_REFERENCE_CACHE = {}


def _references(root, rel):
    """The top-level module names one Python file imports by a literal import statement (cached)."""
    cache_key = (str(root), rel)
    if cache_key not in _REFERENCE_CACHE:
        names = set()
        try:
            tree = ast.parse(_read_bounded(Path(root) / rel).decode("utf-8"))
        except (CannotEvaluate, SyntaxError, UnicodeDecodeError, ValueError, RecursionError, MemoryError):
            tree = None
        for node in ast.walk(tree) if tree is not None else ():
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module.split(".")[0])
        _REFERENCE_CACHE[cache_key] = frozenset(names)
    return _REFERENCE_CACHE[cache_key]


def _import_closure(root, scripts):
    """Repository files the scripts reach through literal local import statements, resolved beside the
    importing file, then in tools/ and opf/tools/ (the directories the gates put on sys.path). A module
    loaded by a computed path or launched as a subprocess is outside this closure; the declarations name
    those inputs from the static review."""
    root = Path(root)
    seen, queue = set(), list(scripts)
    while queue:
        rel = queue.pop()
        if rel in seen:
            continue
        seen.add(rel)
        if not rel.endswith(".py"):
            continue
        here = Path(rel).parent
        names = _references(root, rel)
        for name in names:
            candidates = [str(here / (name + ".py")), "tools/" + name + ".py", "opf/tools/" + name + ".py"]
            for candidate in candidates:
                path = root / candidate
                if not candidate.startswith("/") and ".." not in candidate.split("/") \
                        and path.is_file() and not path.is_symlink():
                    queue.append(candidate)
                    break
    return seen


def _gate_scripts(root, gate, suites):
    scripts = set()
    for argv in gate.variants + ((gate.prerequisite,) if gate.prerequisite else ()):
        for index, word in enumerate(argv):
            if word.endswith(".py") and (Path(root) / word).is_file():
                scripts.add(word)
            if word == "--suite" and index + 1 < len(argv) and argv[index + 1] in suites:
                scripts.add(suites[argv[index + 1]])
                scripts.add(SUITES_REL)
    return scripts


def _suite_runners(root):
    data, problem = _parse_toml(_read_bounded(Path(root) / SUITES_REL).decode("utf-8"))
    if problem is not None:
        raise CannotEvaluate(problem)
    return {row["id"]: row["runner"] for row in data.get("suite", [])}


def _uncovered_imports(root, gates, manifest):
    """{gate: [closure file no declaration of it matches]} for every narrowed gate."""
    suites = _suite_runners(root)
    problems = {}
    for gate in gates:
        decl = manifest.gates[gate.name]
        if decl.whole_tree or decl.always:
            continue
        closure = _import_closure(root, _gate_scripts(root, gate, suites))
        full = [rx for _p, rx in manifest.full_roster]
        missing = sorted(path for path in closure
                         if not any(rx.fullmatch(path) for _p, rx in decl.inputs)
                         and not any(rx.fullmatch(path) for rx in full))
        if missing:
            problems[gate.name] = missing
    return problems


def _tracked_paths(root):
    out, _err = _git(root, ["ls-files", "-z"], git_env())
    return [_decode_path(p) for p in out.split(b"\0") if p]


def _fixture_git(repo, args, env):
    argv = ["git", "-c", "user.name=Selftest", "-c", "user.email=selftest@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", "-C", str(repo)] + list(args)
    done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, check=False)
    if done.returncode != 0:
        raise CannotEvaluate("fixture git {} failed: {}".format(args, done.stderr[:300]))
    return done.stdout.decode("utf-8", "replace").strip()


def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)


def _fixture_repo(parent, name, env, runner_text=None, manifest_text=None):
    """A git repository holding a copy of the selector, its loader, the runner and the declarations,
    plus a few ordinary files, committed once; returns (path, base commit)."""
    repo = Path(parent) / name
    for rel in (SELF_REL, "tools/check_ci_parity.py"):
        _write(repo / rel, _read_bounded(ROOT / rel))
    _write(repo / RUNNER_REL, runner_text if runner_text is not None else _read_bounded(ROOT / RUNNER_REL))
    _write(repo / MANIFEST_REL,
           manifest_text if manifest_text is not None else _read_bounded(ROOT / MANIFEST_REL))
    _write(repo / "tools/check_release_build.py", "# fixture\nVALUE = 1\n")
    _write(repo / "tools/selftest_aiqt_hooks.py", "def helper():\n    return 1\n")
    _write(repo / ".aiqt/standards/README.md", "# Fixture\n\nText.\n")
    _write(repo / "docs/evidence.md", "# Evidence\n")
    _write(repo / ".gitignore", "ignored.log\n")
    _fixture_git(repo, ["init", "-q"], env)
    _fixture_git(repo, ["add", "-A"], env)
    _fixture_git(repo, ["commit", "-q", "-m", "base"], env)
    return repo, _fixture_git(repo, ["rev-parse", "HEAD"], env)


def _launch(repo, args, env):
    """(exit code, stdout, stderr) of the copied selector run in repo."""
    done = subprocess.run([sys.executable, "-I", "-B", str(Path(repo) / SELF_REL)] + list(args),
                          cwd=str(repo), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, check=False)
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def _json_selection(stdout):
    try:
        doc = json.loads(stdout)
    except ValueError:
        return None
    return doc


def _self_test_roster(gates, manifest, error):
    runner_text = _read_bounded(ROOT / RUNNER_REL).decode("ascii")
    check("live/roster-and-declarations-read", error is None and gates is not None, error)
    if gates is None:
        return
    names = [g.name for g in gates]
    check("live/child-status-first", names[:1] == ["child-status"], names[:3])
    check("live/declarations-equal-roster", set(names) == set(manifest.gates) and len(names) == len(set(names)),
          sorted(set(names) ^ set(manifest.gates)))
    baseline = [g for g in gates if g.name == BASELINE_GATE]
    check("roster/baseline-one-logical-gate",
          len(baseline) == 1 and baseline[0].kind == "baseline-block" and len(baseline[0].variants) == 2
          and baseline[0].prerequisite is not None and "--probe-local-base" in baseline[0].prerequisite
          and "--local-base" in baseline[0].variants[0] and "--local-base" not in baseline[0].variants[1],
          baseline)
    leak = [g for g in gates if g.name == GITLEAKS_GATE]
    check("roster/inline-gitleaks-gate",
          len(leak) == 1 and leak[0].kind == "inline-gitleaks" and leak[0].variants[0][:2] == ("gitleaks", "dir"),
          leak)
    order = [g.line for g in gates]
    check("roster/runner-order-kept", order == sorted(order), order[:5])

    def refused(text):
        try:
            parse_roster(text)
        except CannotEvaluate:
            return True
        return False
    anchor = 'run_gate "secrets"   python3 -I -B tools/check_secrets.py\n'
    check("roster/unsupported-syntax-exit-2", anchor in runner_text and refused(runner_text.replace(
        anchor, anchor + "for g in a b; do run_gate \"$g\" python3 -I -B tools/check_secrets.py; done\n")))
    check("roster/duplicate-name-exit-2", refused(runner_text.replace(
        anchor, anchor + 'run_gate "secrets" python3 -I -B tools/check_leaks.py\n')))
    check("roster/malformed-invocation-exit-2", refused(runner_text.replace(anchor, anchor + 'run_gate "lonely"\n')))
    check("roster/sourced-file-exit-2", refused(runner_text.replace(anchor, anchor + ". tools/extra_gates.sh\n")))

    def manifest_refused(mutate):
        data, _problem = _parse_toml(_read_bounded(ROOT / MANIFEST_REL).decode("utf-8"))
        mutate(data)
        try:
            check_roster_equality(gates, load_manifest(data))
        except CannotEvaluate:
            return True
        return False
    check("manifest/missing-gate-fails", manifest_refused(lambda d: d["gate"].pop("links")))
    check("manifest/extra-declaration-fails", manifest_refused(
        lambda d: d["gate"].__setitem__("no-such-gate", {"whole-tree": True, "reason": "x"})))
    check("manifest/empty-declaration-fails", manifest_refused(
        lambda d: d["gate"].__setitem__("links", {"reason": "x"})))
    check("manifest/unknown-key-fails", manifest_refused(
        lambda d: d["gate"]["links"].__setitem__("input", ["x"])))
    check("manifest/bad-glob-fails", manifest_refused(
        lambda d: d["gate"]["mappings"].__setitem__("inputs", ["../outside"])))
    check("manifest/missing-domains-fails", manifest_refused(lambda d: d.pop("domain")))


def _self_test_selection(gates, manifest):
    if gates is None:
        return
    every = [g.name for g in gates]
    # The three misses the declarations must catch, written here independently of the declarations
    # under test: a comment change in the release-build gate, a test-helper change in the hooks
    # self-test, and a Markdown change under .aiqt/.
    sel = _selected_names(gates, manifest, ["tools/check_release_build.py"])
    check("miss/release-build-comment-selects-review-labels", sel is not None and "review-labels" in sel, sel)
    sel = _selected_names(gates, manifest, ["tools/selftest_aiqt_hooks.py"])
    check("miss/hooks-test-helper-selects-dangerous-api", sel is not None and "dangerous-api" in sel, sel)
    sel = _selected_names(gates, manifest, [".aiqt/core/hooks/ORCHESTRATION.md"])
    check("miss/aiqt-markdown-selects-links", sel is not None and "links" in sel, sel)
    # Each row fails once its own mapping is narrowed to the gate's script alone (a mutation).
    for check_id, path, name in (
            ("flip/review-labels-missed-without-mapping", "tools/check_release_build.py", "review-labels"),
            ("flip/dangerous-api-missed-without-mapping", "tools/selftest_aiqt_hooks.py", "dangerous-api"),
            ("flip/links-missed-without-mapping", ".aiqt/core/hooks/ORCHESTRATION.md", "links")):
        flipped = _selected_names(gates, _narrowed(manifest, name), [path])
        check(check_id, flipped is not None and name not in flipped, flipped)
    # Lower bounds of the change classes, also independent of the declarations under test.
    for check_id, path, required in (
            ("lower-bound/rule-corpus", ".aiqt/core/rules/quali-smallest-correct-change.md",
             ("clauses", "clauses-manifest-sources", "instruction-budget", "instruction-budget-change-shape",
              "placement", "mappings", "links", "dashes", "internal-names", "review-labels")),
            ("lower-bound/tools-python", "tools/check_versions.py",
             ("release-cut-selftest", "python-floor", "child-status", "child-status-coverage",
              "dangerous-api", "versions")),
            ("lower-bound/site", "site/index.html", ("site", "links", "overclaim")),
            ("lower-bound/opf-site", "opf/site/index.html", ("site", "links", "overclaim")),
            ("lower-bound/opf-tools", "opf/tools/_opf_oplock.py",
             ("opf-tooling-selftest-core", "opf-tooling-selftest-adopt", "opf-tooling-selftest-watchdog",
              "opf-oplock-selftest"))):
        sel = _selected_names(gates, manifest, [path]) or []
        check(check_id, all(name in sel for name in required), [name for name in required if name not in sel])
    for check_id, path in (("full-roster/runner", RUNNER_REL), ("full-roster/selector", SELF_REL),
                           ("full-roster/declarations", MANIFEST_REL),
                           ("full-roster/roster-loader", "tools/check_ci_parity.py")):
        check(check_id, _selected_names(gates, manifest, [path]) == every)
    sub, _unknown = select(gates, manifest, [Change("opf/vendor", ("committed",), True)])
    check("full-roster/submodule-change", [g.name for g, _r in (sub or [])] == every)
    always = [g.name for g in gates if manifest.gates[g.name].always]
    check("empty/only-always-gates", _selected_names(gates, manifest, []) == always and "child-status" in always,
          always[:5])
    _sel, unknown = select(gates, manifest, [Change("newdir/notes.md", ("untracked",), False)])
    check("unknown/new-directory-refused", _sel is None and unknown == ["newdir/notes.md"], unknown)
    whole = [n for n in every if manifest.gates[n].whole_tree]
    check("unknown/whole-tree-match-does-not-cover", whole and _sel is None, whole[:3])
    first = _selected_names(gates, manifest, ["site/index.html", "tools/check_versions.py"])
    second = _selected_names(gates, manifest, ["tools/check_versions.py", "site/index.html"])
    check("output/deterministic-runner-order", first == second and first == [n for n in every if n in first])
    check("glob/star-stays-in-component", glob_regex("site/*.html").fullmatch("site/index.html") is not None
          and glob_regex("site/*.html").fullmatch("site/a/index.html") is None)
    check("glob/double-star-spans", glob_regex("opf/**").fullmatch("opf/a/b/c.py") is not None
          and glob_regex("**/x.md").fullmatch("x.md") is not None
          and glob_regex("a/**/b").fullmatch("a/b") is not None
          and glob_regex("tools/*.py").fullmatch("Tools/x.py") is None)
    raw = (b":100644 160000 " + b"1" * 40 + b" " + b"2" * 40 + b" T\0opf/vendor\0"
           + b":100644 100755 " + b"3" * 40 + b" " + b"3" * 40 + b" M\0tools/x.sh\0")
    parsed = parse_raw_diff(raw, "committed")
    check("raw/gitlink-and-mode-change", [(c.path, c.gitlink) for c in parsed]
          == [("opf/vendor", True), ("tools/x.sh", False)], parsed)
    try:
        parse_raw_diff(b":100644 100644 " + b"1" * 40 + b" " + b"2" * 40 + b" U\0a\0", "staged")
        refused = False
    except CannotEvaluate:
        refused = True
    check("raw/unmerged-record-refused", refused)


def _self_test_live_coverage(gates, manifest):
    if gates is None:
        return
    try:
        problems = _uncovered_imports(ROOT, gates, manifest)
    except CannotEvaluate as exc:
        problems = {"<cannot evaluate>": str(exc)}
    check("live/import-closure-covered", not problems, problems)
    try:
        tracked = _tracked_paths(ROOT)
    except CannotEvaluate as exc:
        tracked = None
        detail = str(exc)
    else:
        detail = [p for p in tracked if not in_domain(p, manifest.domains)][:10]
    check("live/tracked-paths-in-domains", tracked is not None and not detail, detail)


def _self_test_git(work):
    env = git_env()
    env["HOME"] = str(work)
    env["XDG_CONFIG_HOME"] = str(work)
    for key in [k for k in env if k.startswith("PYTHON")]:
        del env[key]
    repo, base = _fixture_repo(work, "r1", env)
    _write(repo / "tools/check_release_build.py", "# fixture\n# a comment\nVALUE = 1\n")
    _fixture_git(repo, ["commit", "-q", "-am", "comment"], env)
    code, out, err = _launch(repo, ["--base", base], env)
    names = out.split("\n")
    check("git/committed-change-selected", code == 0 and "review-labels" in names and not err, (code, err[:300]))
    code2, out2, _err = _launch(repo, ["--base", base], env)
    check("git/deterministic-output", (code2, out2) == (code, out))
    # --head names the base commit itself: an empty comparison, so no whole-tree gate is selected.
    code, out, _err = _launch(repo, ["--base", base, "--head", "HEAD~1"], env)
    check("git/head-option-commit-only", code == 0 and "links" in names and "links" not in out.split("\n")
          and "child-status" in out.split("\n"), out[:200])

    _write(repo / "tools/selftest_aiqt_hooks.py", "def helper():\n    return 2\n")
    _fixture_git(repo, ["add", "tools/selftest_aiqt_hooks.py"], env)
    _write(repo / "docs/evidence.md", "# Evidence\n\nMore.\n")
    _write(repo / ".aiqt/core/hooks/ORCHESTRATION.md", "# New\n")
    _write(repo / "ignored.log", "noise\n")
    _write(repo / "docs/odd name\nwith newline.md", "x\n")
    code, out, _err = _launch(repo, ["--base", base, "--json"], env)
    doc = _json_selection(out) or {}
    changed = {c["path"]: c["sources"] for c in doc.get("changed") or []}
    check("git/staged-unstaged-untracked-included",
          code == 0 and changed.get("tools/selftest_aiqt_hooks.py") == ["staged"]
          and changed.get("docs/evidence.md") == ["unstaged"]
          and changed.get(".aiqt/core/hooks/ORCHESTRATION.md") == ["untracked"], changed)
    check("git/ignored-file-excluded", code == 0 and "ignored.log" not in changed, sorted(changed))
    check("git/unusual-filename-kept", "docs/odd name\nwith newline.md" in changed, sorted(changed))
    selected = [s["gate"] for s in doc.get("selected") or []]
    check("git/three-misses-selected-end-to-end",
          all(name in selected for name in ("review-labels", "dangerous-api", "links")), selected)
    baseline = [s for s in doc.get("selected") or [] if s["gate"] == BASELINE_GATE]
    check("git/json-carries-variants-and-heads",
          doc.get("schema") == SCHEMA and doc.get("base") == base and doc.get("head")
          and (not baseline or (len(baseline[0]["variants"]) == 2 and baseline[0]["prerequisite"])),
          {k: doc.get(k) for k in ("schema", "base", "head")})

    _write(repo / "newdir/notes.md", "x\n")
    code, out, err = _launch(repo, ["--base", base], env)
    check("git/unknown-path-exit-2-no-selection", code == 2 and out == "" and "newdir/notes.md" in err,
          (code, out[:100], err[:200]))
    code, out, _err = _launch(repo, ["--base", base, "--json"], env)
    doc = _json_selection(out) or {}
    check("git/unknown-path-json-unavailable", code == 2 and doc.get("selected", 0) is None
          and doc.get("status") == "cannot-evaluate", doc.get("status"))
    (repo / "newdir/notes.md").unlink()
    (repo / "newdir").rmdir()

    repo2, base2 = _fixture_repo(work, "r2", env)
    _fixture_git(repo2, ["mv", "tools/selftest_aiqt_hooks.py", "docs/moved.py"], env)
    _fixture_git(repo2, ["rm", "-q", "tools/check_release_build.py"], env)
    _fixture_git(repo2, ["commit", "-q", "-m", "move"], env)
    code, out, _err = _launch(repo2, ["--base", base2, "--json"], env)
    doc = _json_selection(out) or {}
    changed = [c["path"] for c in doc.get("changed") or []]
    selected = [s["gate"] for s in doc.get("selected") or []]
    check("git/rename-both-sides", code == 0 and "tools/selftest_aiqt_hooks.py" in changed
          and "docs/moved.py" in changed, changed)
    check("git/deletion-selects", code == 0 and "tools/check_release_build.py" in changed
          and "release-build-selftest" in selected, selected[:20])

    code, out, err = _launch(repo2, ["--base", "no-such-revision"], env)
    check("git/invalid-base-exit-2", code == 2 and out == "", (code, err[:200]))
    code, out, err = _launch(repo2, ["--base", "--all"], env)
    check("git/option-shaped-base-exit-2", code == 2 and out == "", (code, err[:200]))
    _fixture_git(repo2, ["tag", "amb"], env)
    _fixture_git(repo2, ["branch", "amb", base2], env)
    code, out, err = _launch(repo2, ["--base", "amb"], env)
    check("git/ambiguous-revision-exit-2", code == 2 and out == "", (code, err[:200]))

    hostile = dict(env, GIT_DIR=str(repo / ".git"), GIT_WORK_TREE=str(repo))
    code, out, _err = _launch(repo2, ["--base", base2, "--json"], hostile)
    doc = _json_selection(out) or {}
    check("git/inherited-git-environment-dropped", code == 0 and doc.get("base") == base2, (code, doc.get("base")))

    repo3, base3 = _fixture_repo(work, "r3", env)
    code, out, _err = _launch(repo3, ["--base", base3], env)
    always = [n for n in out.split("\n") if n]
    check("git/empty-change-set-exit-0", code == 0 and always and "child-status" in always
          and "links" not in always, always[:5])
    _write(repo3 / MANIFEST_REL, _read_bounded(repo3 / MANIFEST_REL) + b"\n")
    code, out, _err = _launch(repo3, ["--base", base3], env)
    check("git/declaration-change-selects-roster", code == 0 and "links" in out.split("\n")
          and "opf-oplock-selftest" in out.split("\n"), out[:200])
    _write(repo3 / MANIFEST_REL, b"format-version = [\n")
    code, out, _err = _launch(repo3, ["--base", base3], env)
    check("git/unreadable-declarations-exit-2", code == 2 and out == "", code)
    (repo3 / MANIFEST_REL).unlink()
    code, out, _err = _launch(repo3, ["--base", base3], env)
    check("git/missing-declarations-exit-2", code == 2 and out == "", code)

    repo4, base4 = _fixture_repo(work, "r4", env)
    _fixture_git(repo4, ["checkout", "-q", "-b", "side"], env)
    _write(repo4 / "docs/evidence.md", "side\n")
    _fixture_git(repo4, ["commit", "-q", "-am", "side"], env)
    _fixture_git(repo4, ["checkout", "-q", "main"], env)
    _write(repo4 / "docs/evidence.md", "main\n")
    _fixture_git(repo4, ["commit", "-q", "-am", "main"], env)
    subprocess.run(["git", "-c", "user.name=S", "-c", "user.email=s@example.invalid", "-C", str(repo4),
                    "merge", "-q", "side"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, env=env, check=False)
    code, out, err = _launch(repo4, ["--base", base4], env)
    check("git/unmerged-index-exit-2", code == 2 and out == "" and "unmerged" in err, (code, err[:200]))

    shallow = work / "r5"
    _fixture_git(work, ["clone", "-q", "--depth", "1", "file://" + str(repo2), str(shallow)], env)
    code, out, err = _launch(shallow, ["--base", "HEAD"], env)
    check("git/shallow-history-exit-2", code == 2 and out == "" and "shallow" in err, (code, err[:200]))

    bad = work / "r6"
    _write(bad / SELF_REL, _read_bounded(ROOT / SELF_REL))
    _write(bad / "tools/check_ci_parity.py", _read_bounded(ROOT / "tools/check_ci_parity.py"))
    _write(bad / RUNNER_REL, _read_bounded(ROOT / RUNNER_REL))
    _write(bad / MANIFEST_REL, _read_bounded(ROOT / MANIFEST_REL))
    code, out, err = _launch(bad, ["--base", "HEAD"], env)
    check("git/git-failure-exit-2", code == 2 and out == "", (code, err[:200]))

    repo7, base7 = _fixture_repo(work, "r7", env)
    _write(repo7 / os.fsdecode(b"docs/bad-\xff.md"), "x\n")
    code, out, err = _launch(repo7, ["--base", base7], env)
    check("git/non-utf8-path-exit-2", code == 2 and out == "", (code, err[:200]))


def _self_test_cli():
    got = []
    for argv in (["--bogus"], [], ["--base"], ["--base", "a", "--base", "b"], ["--base", "a", "--json", "--explain"],
                 ["--self-test", "--base", "a"], ["--execution-report", "relative/report"]):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got.append(main(argv))
    check("cli/invalid-invocation-exit-2", got == [2] * len(got), got)


def self_test():
    gates_manifest, error = _real_inputs()
    gates, manifest = gates_manifest
    _self_test_roster(gates, manifest, error)
    _self_test_selection(gates, manifest)
    _self_test_live_coverage(gates, manifest)
    _self_test_cli()
    work = Path(tempfile.mkdtemp(prefix="affected-gates-selftest-"))
    try:
        try:
            _self_test_git(work)
        except CannotEvaluate as exc:
            # The remaining git rows never run, so the execution reconcile fails too.
            FAILURES.append("git fixture harness: {}".format(exc))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if FAILURES:
        print("SELF-TEST FAIL: affected_gates {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: affected_gates {} checks (the roster is read through the parity loader with "
          "baseline-pin as one gate and the inline gitleaks scan named; declarations equal the roster; the "
          "three named misses are selected and each is missed once its mapping is narrowed; unknown paths, "
          "bad revisions, shallow history, unmerged entries, git failures and non-UTF-8 paths exit 2 with no "
          "selection)".format(len(EXECUTED)))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This tool starts git and reads its exit
# status (subprocess), so under an ignored SIGCHLD a failing git would read as a pass; the guard runs at
# the entry line, before main() dispatches.
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
    sys.exit(_child_status_refusal_exit("affected_gates.py") or main())
