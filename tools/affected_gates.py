#!/usr/bin/env python3
"""Affected-gate selector: list every gate of tools/run_all_checks.sh whose declared inputs a change
touches, so the member checks run before a merge train are derived from the changed paths and never
chosen by hand.

  affected_gates.py --base REV [--head REV] [--json | --explain]
  affected_gates.py --self-test
  affected_gates.py [--self-test] --execution-report ABS_PATH   (the registered suite launch)
  affected_gates.py --observe-inputs [--gate NAME]... [--jobs N]

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

UNCOVERED PATHS. The selection only ever fails toward more: whenever it cannot show a gate is
unaffected, it selects it. A changed path outside every reviewed domain has no declaration to show
any gate unaffected, so it selects the full roster: every gate of the runner and, through them, every
suite of tools/selftest_checks.toml (each registered suite must be launched by a runner gate, or the
roster is cannot-evaluate), never a subset. Matching a whole-tree gate does not cover a path. The
uncovered paths are named on stderr, in the --explain reasons and in the JSON uncovered member.

OUTPUT. Default: the selected gate names, one per line, deduplicated, in runner order. --explain adds
the reasons (the matched path and declaration, whole-tree, always, full-roster). --json prints one
object (schema 1) with the resolved base, merge base and head, the changed paths, the uncovered
paths, the ordered selection with each gate's command variants and reasons, and diagnostics; on
failure its selected and uncovered members are null. Conditional gates keep their runner behaviour:
baseline-pin lists both variants and its probe, and is run by the runner's block, never as a bare
command.

OBSERVATION. The declarations are held against what the gates really read. --observe-inputs runs
every gate whose declaration narrows its inputs (neither whole-tree nor always; those two are selected
by any change, so no read of theirs can go uncovered) once, in a disposable clone of this tree built
from its index exactly (a staged deletion stays deleted, a staged rename moves; unstaged changes and
untracked files are not observed, so stage them first), under a Python audit hook
(sys.addaudithook) that records open, os.listdir, os.scandir and every process launch
(subprocess.Popen, os.exec, os.posix_spawn, os.spawn, os.system). Metadata reads raise no audit
event, so the same startup hook wraps os.stat, os.lstat, os.access and os.readlink (and through
them os.path.exists, isfile, isdir, getsize, getmtime, pathlib's exists, is_file, stat and resolve):
a file probed only for its existence, type, mode, size or timestamps is an input exactly like a file
opened for reading, and a probed directory is covered only when its declaration covers a file under
it or a new entry in it. The hook reaches the gate's Python children through a startup hook (a .pth
file) that the check writes only into its own throwaway virtual environment, whose interpreter
replaces python3 for the gate and comes first on PATH, so sys.executable and python3
children carry it and no other interpreter does. The check fails (exit 1) for a gate that, through any
of its Python processes, opens for reading (or probes) a repository path its declaration does not
match, lists a directory where a new entry would not be matched (or lists the repository root),
names a repository file on a launched command line its declaration does not match, launches a
non-Python child (or a Python child that skips site) in the repository or on one of its paths, or
does not exit 0 in the copy. A gate the check fails for is declared whole-tree unless its narrowed
inputs are corrected to cover every observed read. RESIDUAL, NOT MEASURED: the audit hook sees no
read of a non-Python child (git, bash, gitleaks, a shell); every gate that launches one in or on the
repository is therefore whole-tree, and a non-Python child that runs elsewhere and names no
repository path is trusted not to reach the tree. One run observes one input state: a read that only
other inputs would trigger is not seen, which is why a gate whose reads follow its input (it walks a
tree or reads paths its data names) is whole-tree. Reads through a directory descriptor (dir_fd) carry
no directory in the audit event and are judged only when their first component exists at the root;
bytecode caches (__pycache__) and a path with a .git component (.git internals, or a probe for a
work-tree marker such as tools/.git; git tracks no such path) are not inputs. A metadata read the
wrapped functions do not carry (os.DirEntry.stat after a recorded listing, os.fstat of a recorded
open, a C extension's own stat) is not seen; a probed path that is missing is held as a file, so a
directory later created at that path is not covered by a file glob. The hook keeps only paths inside the copy and writes
each distinct record once per process. A gate that runs past OBSERVE_GATE_TIMEOUT is stopped
through its process group and reported; a descendant that started a session of its own is not
stopped by the check.

EXIT. 0 selection complete (an empty change set included: the always gates are then the selection);
2 cannot evaluate, invalid invocation, or an invalid roster, suite registry or declaration (no gate
name is printed on stdout); 1 for a failed --self-test assertion or an --observe-inputs finding.
Callers check the exit code before reading the output.
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


def read_suites(root):
    """{suite id: runner} of the self-test registry at root, or CannotEvaluate."""
    raw = _read_bounded(Path(root) / SUITES_REL)
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CannotEvaluate("{} is not UTF-8".format(SUITES_REL))
    data, problem = _parse_toml(decoded)
    if problem is not None:
        raise CannotEvaluate("{}: {}".format(SUITES_REL, problem))
    rows = data.get("suite")
    if not isinstance(rows, list) or not rows:
        raise CannotEvaluate("{}: no [[suite]] rows".format(SUITES_REL))
    suites = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] \
                or not isinstance(row.get("runner"), str) or row["id"] in suites:
            raise CannotEvaluate("{}: suite row without one unique id and a runner".format(SUITES_REL))
        suites[row["id"]] = row["runner"]
    return suites


def check_suite_reach(gates, suites):
    """CannotEvaluate unless every registered suite is launched by a roster gate, so selecting every
    gate runs every check and every suite."""
    launched = set()
    for gate in gates:
        for argv in gate.variants:
            launched.update(argv[index + 1] for index, word in enumerate(argv[:-1]) if word == "--suite")
    missing = sorted(set(suites) - launched)
    if missing:
        raise CannotEvaluate("registered suite(s) no runner gate launches: {}".format(missing))


def read_inputs(root):
    """(gates, manifest) for the tree at root, or CannotEvaluate."""
    runner = _read_bounded(Path(root) / RUNNER_REL)
    text, diagnostic = check_ci_parity.read_runner_text(
        Path(root) / RUNNER_REL, RUNNER_REL, reader=lambda _path: runner)
    if diagnostic is not None:
        raise CannotEvaluate("{}: {}".format(RUNNER_REL, diagnostic.message))
    gates = parse_roster(text)
    check_suite_reach(gates, read_suites(root))
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
    """(selected [(gate, reasons)], uncovered paths). changes is a list of Change.

    A changed path outside every reviewed domain has no declaration that can show any gate
    unaffected, so it selects the whole roster: every gate, and through them every registered suite
    (read_inputs holds each suite to a roster gate). The selection only ever fails toward more."""
    uncovered = sorted(c.path for c in changes if not in_domain(c.path, manifest.domains))
    full = ["full-roster: {} lies outside every reviewed domain (no declaration covers it)".format(path)
            for path in uncovered]
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
    if full and len(selected) != len(gates):
        raise CannotEvaluate("a full-roster change selected {} of {} gates".format(len(selected), len(gates)))
    return selected, uncovered


# ---------------------------------------------------------------------------------------------
# Changes

def git_env(base_env=None):
    env = {k: v for k, v in (os.environ if base_env is None else base_env).items()
           if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
    return env


def _git(root, args, env, stdin_bytes=None):
    argv = ["git", "--no-optional-locks", "-c", "core.excludesFile=" + os.devnull,
            "-c", "core.quotePath=false", "-c", "diff.external=", "-C", str(root)] + list(args)
    try:
        if stdin_bytes is None:
            done = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env, check=False)
        else:
            done = subprocess.run(argv, input=stdin_bytes, stdout=subprocess.PIPE,
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
# Observation: the declarations held against what each gate really reads

# The startup hook the observation check writes into its own disposable virtual environment, and
# nowhere else: a .pth line imports it as site starts, which -I leaves running for a virtual
# environment's own site-packages, so every Python process launched through that environment's
# interpreter (sys.executable, or python3 found first on PATH) records its opens, directory listings
# and process launches. The log directory is read from the one file the check rewrites before each
# gate, so a child whose environment a gate scrubs still records. The hook imports nothing beyond
# os and _thread, never raises into the gate, and writes one repr() tuple per line.
OBSERVE_HOOK_MODULE = "_affected_gates_observe_hook"
OBSERVE_HOOK_TEMPLATE = '''\
"""Audit hook of tools/affected_gates.py --observe-inputs (a disposable environment only)."""
import sys


def _install():
    import os
    import _thread
    try:
        with open(@CURRENT@, "r", encoding="utf-8") as handle:
            log_dir, root = handle.read().split("\\n")[:2]
        fd = os.open(os.path.join(log_dir, "{}-{}.log".format(os.getpid(), os.urandom(8).hex())),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
    except (OSError, ValueError):
        return
    local = _thread._local()
    seen = set()
    inside = root + "/"

    def in_root(where, path):
        joined = os.path.normpath(os.path.join(where or "/", path))
        return joined == root or joined.startswith(inside)
    watched = frozenset(("open", "os.listdir", "os.scandir", "subprocess.Popen", "os.exec",
                         "os.posix_spawn", "os.spawn", "os.system"))

    def text(value):
        try:
            if isinstance(value, (str, bytes, os.PathLike)):
                return os.fsdecode(os.fspath(value))
        except Exception:
            pass
        return None

    def env_path(env):
        try:
            if env is None:
                return os.environ.get("PATH")
            value = env.get("PATH", env.get(b"PATH"))
            return text(value)
        except Exception:
            return None

    def emit(record):
        # One line per distinct record: a gate that reopens a file a thousand times logs it once.
        try:
            line = repr(record)
            if line in seen:
                return
            seen.add(line)
            os.write(fd, (line + "\\n").encode("utf-8", "surrogateescape"))
        except Exception:
            pass

    def cwd():
        try:
            return os.getcwd()
        except OSError:
            return None

    def importing():
        frame = sys._getframe(2)
        depth = 0
        while frame is not None and depth < 16:
            if frame.f_code.co_filename.startswith("<frozen importlib"):
                return True
            frame = frame.f_back
            depth += 1
        return False

    def hook(event, args):
        if event not in watched or getattr(local, "busy", False):
            return
        local.busy = True
        try:
            if event == "open":
                path = text(args[0])
                where = cwd()
                if path is not None and in_root(where, path):
                    emit(("open", 0, where, path, args[1] if isinstance(args[1], str) else None,
                          args[2] if isinstance(args[2], int) else None))
            elif event in ("os.listdir", "os.scandir"):
                path = "." if not args or args[0] is None else text(args[0])
                where = cwd()
                if path is not None and in_root(where, path) and not importing():
                    emit(("list", 0, where, path, False))
            elif event == "subprocess.Popen":
                executable, argv, where, env = args
                argv = [text(argv)] if isinstance(argv, (str, bytes)) else [text(a) for a in argv]
                emit(("launch", os.getpid(), cwd(), text(executable), argv,
                      None if where is None else text(where), env_path(env)))
            elif event in ("os.exec", "os.posix_spawn", "os.spawn"):
                path, argv = args[0], args[1]
                emit(("launch", os.getpid(), cwd(), text(path), [text(a) for a in argv], None,
                      env_path(args[2] if len(args) > 2 else None)))
            elif event == "os.system":
                emit(("launch", os.getpid(), cwd(), None, ["/bin/sh", "-c", text(args[0])], None, None))
        except Exception:
            pass
        finally:
            local.busy = False

    # Metadata reads (existence, type, mode, size, timestamps) raise no audit event: the os functions
    # they all reach are wrapped, and the fd-capability sets keep naming the wrappers so shutil and
    # os.fwalk keep their behaviour. An fd argument is skipped (its open was recorded).
    def probe(name, args, kwargs):
        if getattr(local, "busy", False):
            return
        local.busy = True
        try:
            value = args[0] if args else kwargs.get("path")
            if value is not None and not isinstance(value, int):
                path = text(value)
                where = cwd()
                if path is not None and in_root(where, path):
                    emit(("probe", 0, where, path, name))
        except Exception:
            pass
        finally:
            local.busy = False

    def wrap(name):
        original = getattr(os, name, None)
        if original is None:
            return

        def wrapper(*args, **kwargs):
            probe(name, args, kwargs)
            return original(*args, **kwargs)
        wrapper.__name__ = getattr(original, "__name__", name)
        wrapper.__qualname__ = wrapper.__name__
        wrapper.__doc__ = getattr(original, "__doc__", None)
        setattr(os, name, wrapper)
        for capability in ("supports_dir_fd", "supports_fd", "supports_follow_symlinks",
                           "supports_effective_ids"):
            members = getattr(os, capability, None)
            if isinstance(members, set) and original in members:
                members.add(wrapper)
    for name in ("stat", "lstat", "access", "readlink"):
        wrap(name)

    emit(("start", os.getpid(), cwd(), list(sys.orig_argv)))
    sys.addaudithook(hook)


_install()
del _install
'''
OBSERVE_PROBE_ENTRY = "observed-new-entry"
OBSERVE_GATE_TIMEOUT = 1800
OBSERVE_DEFAULT_JOBS = 8
# Interpreter options that skip site (so the startup hook): a Python child launched with one of them
# is unobserved, like a non-Python child.
OBSERVE_NO_SITE_FLAGS = frozenset("S")


def observed_gates(gates, manifest):
    """The gates whose declaration narrows their inputs: a whole-tree gate is selected by every
    changed path and an always gate by every change, so observing them can find no uncovered read."""
    return [g for g in gates if not (manifest.gates[g.name].whole_tree or manifest.gates[g.name].always)]


def _build_observer_env(parent, index):
    """(python, bin dir, current-gate file) of one disposable virtual environment carrying the hook."""
    venv = Path(parent) / "venv-{}".format(index)
    done = subprocess.run([sys.executable, "-I", "-m", "venv", "--without-pip", str(venv)],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=False)
    if done.returncode != 0:
        raise CannotEvaluate("cannot create the observation environment: {}".format(
            done.stderr.decode("utf-8", "replace")[:300]))
    bindir = venv / "bin"
    python = bindir / "python3"
    current = Path(parent) / "current-{}".format(index)
    current.write_text("", encoding="utf-8")
    probe = subprocess.run([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    site_dir = Path(probe.stdout.decode("utf-8", "replace").strip())
    if probe.returncode != 0 or not site_dir.is_dir() or Path(parent) not in site_dir.parents:
        raise CannotEvaluate("the observation environment has no site-packages of its own")
    (site_dir / (OBSERVE_HOOK_MODULE + ".py")).write_text(
        OBSERVE_HOOK_TEMPLATE.replace("@CURRENT@", repr(str(current))), encoding="utf-8")
    (site_dir / (OBSERVE_HOOK_MODULE + ".pth")).write_text("import " + OBSERVE_HOOK_MODULE + "\n",
                                                           encoding="utf-8")
    return python, bindir, current


def read_observations(log_dir):
    """Every record the hook wrote under log_dir."""
    records = []
    for entry in sorted(Path(log_dir).iterdir()):
        for line in entry.read_text(encoding="utf-8", errors="surrogateescape").splitlines():
            try:
                record = ast.literal_eval(line)
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                continue
            if isinstance(record, tuple) and record:
                records.append(record)
    return records


def _repo_rel(root, cwd, path):
    """The repository-relative form of path (resolved against cwd) inside root, "" for root itself,
    or None outside it."""
    if path is None:
        return None
    joined = os.path.normpath(os.path.join(cwd or "/", path))
    if joined == root:
        return ""
    if joined.startswith(root + "/"):
        return joined[len(root) + 1:]
    return None


def _python_options(argv):
    """The single-letter options of a Python command line before its script, -c or -m."""
    letters = set()
    words = list(argv[1:])
    index = 0
    while index < len(words):
        word = words[index]
        index += 1
        if not word or not word.startswith("-") or word == "-":
            break
        if word.startswith("--"):
            continue
        body = word[1:]
        for at, letter in enumerate(body):
            letters.add(letter)
            if letter in "cm":
                return letters
            if letter in "WX":
                if at + 1 == len(body):
                    index += 1
                break
    return letters


def observed_requirements(root, bindir, records):
    """(reads, listed directories, unobserved launches, {probed directory: files under it}) of one
    gate's records, repository-relative.

    A read is an open for reading or a metadata probe of a file (whether or not the path exists, since
    a gate that probes for a file depends on its absence too) or a repository path named on a launched
    command line; a metadata probe of a directory is kept apart with the files under it; directory opens
    carry no content and the import system's own directory listings are not the gate's. A launch is
    unobserved when its program is not a Python of the observation environment (or skips site), and it
    is recorded only when it runs in or names a path of the repository: its reads are invisible."""
    root = os.path.realpath(str(root))
    bindir = str(bindir)
    reads, listed, unobserved, probed = set(), set(), [], set()

    def kept(rel, path):
        # git never tracks a path with a .git component, so no change can name one: a probe for a
        # repository marker (tools/.git while finding the work tree's root) is not an input.
        if not rel or ".git" in rel.split("/") or "__pycache__" in rel.split("/"):
            return False
        # A relative name whose first component the repository root does not hold was resolved
        # against a directory descriptor the audit event does not carry (shutil.rmtree, os.fwalk,
        # dir_fd opens); a root-level name outside every domain selects the whole roster anyway.
        if not os.path.isabs(path) and not os.path.lexists(os.path.join(root, rel.split("/")[0])):
            return False
        return True
    for record in records:
        kind = record[0]
        if kind == "open" and len(record) == 6:
            _k, _pid, cwd, path, mode, flags = record
            if isinstance(mode, str):
                reading = "r" in mode or "+" in mode
            else:
                reading = isinstance(flags, int) and (flags & os.O_ACCMODE) in (os.O_RDONLY, os.O_RDWR)
            if isinstance(flags, int) and flags & getattr(os, "O_DIRECTORY", 0):
                reading = False
            rel = _repo_rel(root, cwd, path)
            if reading and kept(rel, path) and not os.path.isdir(os.path.join(root, rel)):
                reads.add(rel)
        elif kind == "probe" and len(record) == 5:
            _k, _pid, cwd, path, _function = record
            rel = _repo_rel(root, cwd, path)
            if kept(rel, path):
                where = os.path.join(root, rel)
                if os.path.isdir(where) and not os.path.islink(where):
                    probed.add(rel)
                else:
                    reads.add(rel)
        elif kind == "list" and len(record) == 5:
            _k, _pid, cwd, path, importing = record
            rel = _repo_rel(root, cwd, path)
            if not importing and rel is not None and (rel == "" or kept(rel, path)):
                listed.add(rel)
        elif kind == "launch" and len(record) == 7:
            _k, _pid, cwd, executable, argv, where, path_env = record
            argv = [a for a in argv if isinstance(a, str)]
            run_cwd = cwd if where is None else os.path.join(cwd or "/", where)
            program = executable or (argv[0] if argv else None)
            if program is None:
                continue
            if "/" not in program:
                found = shutil.which(program, path=path_env if path_env is not None else os.defpath)
                program = found or program
            program = os.path.normpath(os.path.join(run_cwd or "/", program))
            words = argv[1:] if argv else []
            observed = (os.path.dirname(program) == bindir
                        and not (OBSERVE_NO_SITE_FLAGS & _python_options(argv)))
            effective = run_cwd
            if os.path.basename(program) == "git" and "-C" in words:
                at = words.index("-C")
                if at + 1 < len(words):
                    effective = os.path.join(run_cwd or "/", words[at + 1])
            names = []
            for word in words + [program]:
                if not word or word.startswith("-"):
                    continue
                rel = _repo_rel(root, effective, word)
                if rel and kept(rel, word) and os.path.isfile(os.path.join(root, rel)):
                    names.append(rel)
            reads.update(names)
            if observed or words == ["--version"]:
                continue
            in_repo = _repo_rel(root, effective, ".") is not None
            if in_repo or names or any(_repo_rel(root, run_cwd, w) is not None for w in words
                                       if w and w.startswith("/")):
                unobserved.append(" ".join([os.path.basename(program)] + words)[:200])
    directories = {}
    for rel in probed:
        under = []
        for dirpath, dirnames, filenames in os.walk(os.path.join(root, rel)):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
            here = _repo_rel(root, None, dirpath)
            under.extend(here + "/" + f for f in filenames)
        directories[rel] = tuple(sorted(under))
    return reads, listed, unobserved, directories


def uncovered_observations(manifest, name, reads, listed, unobserved, probed=None):
    """The findings of one gate: every observed read, metadata probe or listing its declaration does
    not cover. probed maps each metadata-probed directory to the files under it: the directory's
    existence and type change only through a change under it, so it is covered when a declared input
    matches a file under it or a new entry in it."""
    decl = manifest.gates[name]
    if decl.whole_tree or decl.always:
        return []
    patterns = [rx for _p, rx in decl.inputs] + [rx for _p, rx in manifest.full_roster]
    findings = []
    for rel in sorted(reads):
        if not any(rx.fullmatch(rel) for rx in patterns):
            findings.append("reads {}".format(rel))
    for rel, under in sorted((probed or {}).items()):
        if not any(rx.fullmatch(path) for rx in patterns
                   for path in under + (rel + "/" + OBSERVE_PROBE_ENTRY,)):
            findings.append("probes {}/ (no declared input lies under it)".format(rel))
    for rel in sorted(listed):
        if rel == "":
            findings.append("lists the repository root (its reads depend on input: whole-tree)")
        elif not any(rx.fullmatch(rel + "/" + OBSERVE_PROBE_ENTRY) for rx in patterns):
            findings.append("lists {}/ (a new entry there is not covered)".format(rel))
    for launch in sorted(set(unobserved)):
        findings.append("launches a child the audit hook cannot see, in or on the repository "
                        "(whole-tree): {}".format(launch))
    return findings


def _index_records(root):
    """The NUL-terminated `ls-files --stage` records of root's index, or CannotEvaluate."""
    out, _err = _git(root, ["ls-files", "-z", "--stage"], git_env())
    records = [record for record in out.split(b"\0") if record]
    for record in records:
        meta, tab, _path = record.partition(b"\t")
        fields = meta.split(b" ")
        if not tab or len(fields) != 3:
            raise CannotEvaluate("unrecognized index record {!r}".format(record[:200]))
        if fields[2] != b"0":
            raise CannotEvaluate("the index holds unmerged entries")
    return records


def _disposable_copy(root, target):
    """A clone of root at target whose index and working tree are root's index exactly, sharing
    root's object store read-only. The clone is made without a checkout, its index is written from
    root's index records and the working tree is checked out from that index alone, so nothing of
    HEAD survives that the index does not hold: a staged deletion stays deleted and a staged rename
    moves. Unstaged changes and untracked files are not part of the copy."""
    env = git_env()
    done = subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", "--no-hardlinks",
                           str(root), str(target)],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env=env, check=False)
    if done.returncode != 0:
        raise CannotEvaluate("cannot clone the disposable copy: {}".format(
            done.stderr.decode("utf-8", "replace").strip()[:300]))
    records = _index_records(root)
    _git(target, ["read-tree", "--empty"], env)
    if records:
        _git(target, ["update-index", "-z", "--index-info"], env, b"\0".join(records) + b"\0")
    _git(target, ["checkout-index", "--all", "--force"], env)
    copied = _index_records(target)
    if copied != records:
        raise CannotEvaluate("the disposable copy's index differs from the source index")


def _observe_one(gate, copy, python, bindir, current, work, index):
    """(exit code or None on timeout, records) of one gate run once in copy under the hook."""
    log_dir = Path(work) / "log-{}".format(index)
    tmp = Path(work) / "tmp-{}".format(index)
    log_dir.mkdir()
    tmp.mkdir()
    current.write_text("{}\n{}".format(log_dir, os.path.realpath(str(copy))), encoding="utf-8")
    argv = list(gate.variants[-1])
    if argv[0] != "python3":
        raise CannotEvaluate("gate {!r} is not a python3 launch; declare it whole-tree".format(gate.name))
    argv[0] = str(python)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"PATH": str(bindir) + os.pathsep + env.get("PATH", os.defpath), "TMPDIR": str(tmp),
                "PYTHONDONTWRITEBYTECODE": "1"})
    with open(Path(work) / "out-{}.txt".format(index), "wb") as sink:
        proc = subprocess.Popen(argv, cwd=str(copy), stdin=subprocess.DEVNULL, stdout=sink,
                                stderr=subprocess.STDOUT, env=env, start_new_session=True)
        try:
            code = proc.wait(timeout=OBSERVE_GATE_TIMEOUT)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, 9)
            proc.wait()
            code = None
    current.write_text("", encoding="utf-8")
    records = read_observations(log_dir)
    tail = (Path(work) / "out-{}.txt".format(index)).read_bytes()[-1500:].decode("utf-8", "replace")
    shutil.rmtree(log_dir, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return code, records, tail


def observe_inputs(root, names, jobs, out):
    """Run each narrowed gate (or the named ones) once in a disposable copy under the audit hook and
    report every read, listing or launch its declaration does not cover. 0 when none, 1 otherwise."""
    import concurrent.futures
    import queue
    gates, manifest = read_inputs(root)
    targets = observed_gates(gates, manifest)
    if names:
        unknown = sorted(set(names) - {g.name for g in gates})
        if unknown:
            raise CannotEvaluate("no such gate {}".format(unknown))
        targets = [g for g in gates if g.name in names]
    # A hex-only name: a gate may refuse a temporary root holding a character Markdown renders (an
    # underscore, which tempfile.mkdtemp's random part can carry).
    import secrets
    work = Path(tempfile.gettempdir()) / "affected-gates-observe-{}".format(secrets.token_hex(8))
    work.mkdir(mode=0o700)
    try:
        jobs = max(1, min(jobs, len(targets)))
        slots = queue.Queue()
        for slot in range(jobs):
            copy = work / "copy-{}".format(slot)
            _disposable_copy(root, copy)
            python, bindir, current = _build_observer_env(work, slot)
            slots.put((copy, python, bindir, current))

        def observe(item):
            index, gate = item
            slot = slots.get()
            try:
                copy, python, bindir, current = slot
                code, records, tail = _observe_one(gate, copy, python, bindir, current, work, index)
                reads, listed, unobserved, probed = observed_requirements(copy, bindir, records)
                return gate, code, uncovered_observations(manifest, gate.name, reads, listed, unobserved,
                                                          probed), len(records), tail
            finally:
                slots.put(slot)
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(observe, enumerate(targets)))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    failed = 0
    for gate, code, findings, count, tail in results:
        if code != 0:
            findings = ["exited {} in the disposable copy, so its observation is incomplete; output ends: "
                        "{}".format("by timeout" if code is None else code,
                                    " | ".join(tail.strip().splitlines()[-8:]))] + findings
        if count == 0:
            findings.append("no record was observed (the startup hook did not run)")
        if findings:
            failed += 1
            out.write("UNCOVERED: {}\n".format(gate.name))
            for finding in findings:
                out.write("  {}\n".format(finding))
    if failed:
        out.write("FAIL: {} of {} observed gate(s) read, list or launch repository paths their declaration "
                  "does not cover\n".format(failed, len(results)))
        return 1
    out.write("PASS: {} gate(s) observed under the audit hook; every read, listing and launch of a "
              "repository path is covered by its declaration (NOT MEASURED: reads of non-Python children "
              "that run outside the repository and name none of its paths)\n".format(len(results)))
    return 0


# ---------------------------------------------------------------------------------------------
# Command line

def run(root, base, head_rev, form, out, err, env=None):
    """The selection exit code (0 or 2), output written to out and diagnostics to err."""
    info = {"base": None, "merge_base": None, "head": None, "mode": None}
    changes = []
    try:
        gates, manifest = read_inputs(root)
        info, changes = collect_changes(root, base, head_rev, env)
        selected, uncovered = select(gates, manifest, changes)
    except CannotEvaluate as exc:
        if form == "json":
            out.write(json.dumps(dict(info, schema=SCHEMA, status="cannot-evaluate", selected=None,
                                      changed=[c.path for c in changes], uncovered=None,
                                      diagnostics=[str(exc)]), indent=1, sort_keys=True) + "\n")
        print("CANNOT EVALUATE: {}".format(exc), file=err)
        return 2
    if uncovered:
        print("NOTE: changed path(s) outside every reviewed domain select the full roster: {}".format(
            uncovered), file=err)
    if form == "json":
        out.write(json.dumps(dict(
            info, schema=SCHEMA, status="ok", diagnostics=[], uncovered=uncovered,
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
    if argv and argv[0] == "--observe-inputs":
        observe = {"gates": [], "jobs": OBSERVE_DEFAULT_JOBS}
        rest, seen_jobs = argv[1:], False
        while rest:
            if rest[0] == "--gate" and len(rest) > 1 and rest[1] and rest[1] not in observe["gates"]:
                observe["gates"].append(rest[1])
            elif rest[0] == "--jobs" and len(rest) > 1 and rest[1].isdigit() and int(rest[1]) > 0 \
                    and not seen_jobs:
                observe["jobs"], seen_jobs = int(rest[1]), True
            else:
                return None
            rest = rest[2:]
        return "observe", observe
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
        print("error: usage: affected_gates.py --base REV [--head REV] [--json | --explain] | --self-test"
              " | --observe-inputs [--gate NAME]... [--jobs N]", file=sys.stderr)
        return 2
    mode, options = parsed
    if mode == "observe":
        try:
            return observe_inputs(ROOT, options["gates"], options["jobs"], sys.stdout)
        except CannotEvaluate as exc:
            print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
            return 2
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
    return read_suites(root)


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
    for rel in (SELF_REL, "tools/check_ci_parity.py", SUITES_REL):
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
    # A path no declaration covers selects the whole roster, never a subset (and never nothing), alone
    # or beside a narrowly selected path.
    sel, uncovered = select(gates, manifest, [Change("newdir/notes.md", ("untracked",), False)])
    check("uncovered/path-selects-full-roster", sel is not None and [g.name for g, _r in sel] == every
          and uncovered == ["newdir/notes.md"]
          and all(any("newdir/notes.md" in r for r in reasons) for _g, reasons in sel),
          (uncovered, None if sel is None else len(sel)))
    sel, uncovered = select(gates, manifest, [Change("site/index.html", ("committed",), False),
                                              Change("NEWFILE", ("untracked",), False)])
    check("uncovered/mixed-change-selects-full-roster",
          sel is not None and [g.name for g, _r in sel] == every and uncovered == ["NEWFILE"], uncovered)
    try:
        suites = read_suites(ROOT)
    except CannotEvaluate as exc:
        suites = {"<cannot evaluate>": str(exc)}
    launched = {argv[i + 1] for g in gates for argv in g.variants for i, w in enumerate(argv[:-1])
                if w == "--suite"}
    check("uncovered/full-roster-reaches-every-suite", bool(suites) and set(suites) <= launched,
          sorted(set(suites) - launched))
    try:
        check_suite_reach(gates, dict(suites, **{"unlaunched-suite": "tools/x.py"}))
        refused = False
    except CannotEvaluate:
        refused = True
    check("uncovered/unlaunched-suite-refused", refused)
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


def _self_test_regressions(gates, manifest):
    """The three under-declarations the observation check found, written here independently of the
    declarations under test, each failing once its own mapping is narrowed to the gate's script."""
    if gates is None:
        return
    for check_id, path, required in (
            ("regress/github-contract-selects-entry-guard", ".github/check_newtab_contract.py",
             ("entry-guard", "entry-guard-selftest")),
            ("regress/baseline-palette-selects-entry-guard", ".baseline/check_palette.py",
             ("entry-guard", "entry-guard-selftest")),
            ("regress/registry-selects-direct-selftests", SUITES_REL,
             ("site-scope-selftest", "ci-build-bash-selftest")),
            ("regress/finalizer-selects-execution-selftest", "tools/_selftest_exit_report.py",
             ("selftest-execution-selftest",))):
        sel = _selected_names(gates, manifest, [path]) or []
        check(check_id, all(name in sel for name in required), [n for n in required if n not in sel])
    for check_id, path, name in (
            ("regress/entry-guard-missed-without-mapping", ".github/check_newtab_contract.py", "entry-guard"),
            ("regress/site-scope-missed-without-mapping", SUITES_REL, "site-scope-selftest"),
            ("regress/execution-selftest-missed-without-mapping", "tools/_selftest_exit_report.py",
             "selftest-execution-selftest")):
        flipped = _selected_names(gates, _narrowed(manifest, name), [path])
        check(check_id, flipped is not None and name not in flipped, flipped)


OBSERVE_FIXTURE_GATE = '''\
import os
import subprocess
import sys
open("data/read.txt", encoding="utf-8").read()
try:
    open("data/missing.toml", "rb")
except FileNotFoundError:
    pass
os.listdir("listed")
open("written.out", "w").close()
subprocess.run([sys.executable, "-I", "-c", "open('data/child.txt').read()"], check=True)
subprocess.run(["python3", "-I", "-B", "child_script.py"], check=True)
subprocess.run([sys.executable, "-I", "-S", "-c", "pass"], check=True)
subprocess.run(["sh", "-c", "true"], check=True)
subprocess.run(["git", "--version"], check=True, stdout=subprocess.DEVNULL)
from pathlib import Path
if not Path("data/required.txt").is_file():
    sys.exit(3)
os.path.getsize("data/sized.txt")
os.access("data/access.txt", os.R_OK)
os.path.isdir("data/sub")
os.path.exists("data/.git")
'''


def _self_test_observation(work):
    """The audit hook and the comparison over a fixture tree: opens, listings and launches are recorded
    in the gate and in its Python children (through sys.executable and PATH), a child that skips site
    and a non-Python child in the tree are unobserved, and only an uncovered read is reported."""
    fixture = Path(work) / "observe-tree"
    _write(fixture / "gate.py", OBSERVE_FIXTURE_GATE)
    _write(fixture / "child_script.py", "open('data/script-read.txt').read()\n")
    for rel in ("data/read.txt", "data/child.txt", "data/script-read.txt", "listed/one.md",
                "data/required.txt", "data/sized.txt", "data/access.txt", "data/sub/inner.txt"):
        _write(fixture / rel, "x\n")
    try:
        python, bindir, current = _build_observer_env(work, "selftest")
    except CannotEvaluate as exc:
        # The observation rows never run, so the execution reconcile fails too.
        FAILURES.append("observation fixture harness: {}".format(exc))
        return
    log_dir = Path(work) / "observe-log"
    log_dir.mkdir()
    current.write_text("{}\n{}".format(log_dir, os.path.realpath(str(fixture))), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "PYTHON"))}
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", os.defpath)
    done = subprocess.run([str(python), "-I", "-B", "gate.py"], cwd=str(fixture), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False)
    current.write_text("", encoding="utf-8")
    records = read_observations(log_dir)
    reads, listed, unobserved, probed = observed_requirements(fixture, bindir, records)
    check("observe/hook-records-gate-and-children", done.returncode == 0
          and {"gate.py", "data/read.txt", "data/missing.toml", "data/child.txt", "child_script.py",
               "data/script-read.txt"} <= reads, (done.returncode, sorted(reads), done.stderr[-300:]))
    check("observe/write-only-open-not-a-read", "written.out" not in reads, sorted(reads))
    check("observe/listing-recorded-import-listings-not", listed == {"listed"}, sorted(listed))
    check("observe/unobserved-children-named",
          any(u.startswith("sh -c") for u in unobserved) and any("-S" in u for u in unobserved)
          and not any("--version" in u for u in unobserved) and len(set(unobserved)) == 2, unobserved)
    gate = "fixture-gate"
    def manifest_of(entry):
        return Manifest(manifest_full, (), {gate: entry})
    manifest_full = ((MANIFEST_REL, glob_regex(MANIFEST_REL)),)
    narrow = Declaration(tuple((p, glob_regex(p)) for p in ("gate.py", "child_script.py", "data/**")),
                         False, False, "fixture")
    findings = uncovered_observations(manifest_of(narrow), gate, reads, listed, [])
    check("observe/uncovered-listing-reported", findings == ["lists listed/ (a new entry there is not covered)"],
          findings)
    wide = narrow._replace(inputs=narrow.inputs + (("listed/*", glob_regex("listed/*")),))
    check("observe/covered-declaration-passes", uncovered_observations(manifest_of(wide), gate, reads, listed, [])
          == [], uncovered_observations(manifest_of(wide), gate, reads, listed, []))
    short = narrow._replace(inputs=narrow.inputs[:2] + (("listed/*", glob_regex("listed/*")),))
    findings = uncovered_observations(manifest_of(short), gate, reads, listed, [])
    check("observe/uncovered-read-reported", "reads data/read.txt" in findings
          and "reads data/missing.toml" in findings, findings)
    # A file the gate reads only for metadata (existence, size, access) and a directory it only probes
    # are inputs: recorded through the wrapped os functions, and reported when the declaration misses them.
    check("observe/metadata-probes-recorded",
          {"data/required.txt", "data/sized.txt", "data/access.txt"} <= reads
          and probed.get("data/sub") == ("data/sub/inner.txt",), (sorted(reads), probed))
    check("observe/git-component-probe-not-an-input", "data/.git" not in reads, sorted(reads))
    findings = uncovered_observations(manifest_of(short), gate, reads, listed, [], probed)
    check("observe/metadata-only-dependency-reported",
          {"reads data/required.txt", "reads data/sized.txt", "reads data/access.txt",
           "probes data/sub/ (no declared input lies under it)"} <= set(findings), findings)
    check("observe/probed-directory-covered-by-file-under-it",
          uncovered_observations(manifest_of(wide), gate, reads, listed, [], probed) == [],
          uncovered_observations(manifest_of(wide), gate, reads, listed, [], probed))
    findings = uncovered_observations(manifest_of(wide), gate, reads, listed, unobserved)
    check("observe/unobserved-launch-needs-whole-tree", len(findings) == 2
          and all("whole-tree" in f for f in findings), findings)
    whole = Declaration((), True, False, "fixture")
    check("observe/whole-tree-declaration-covers",
          uncovered_observations(manifest_of(whole), gate, reads, listed | {""}, unobserved) == [])
    check("observe/root-listing-needs-whole-tree",
          any("repository root" in f for f in uncovered_observations(manifest_of(wide), gate, set(), {""}, [])))
    check("observe/system-python-unaffected", subprocess.run(
        [sys.executable, "-I", "-c", "import sys; sys.exit(1 if '{}' in sys.modules else 0)".format(
            OBSERVE_HOOK_MODULE)], stdin=subprocess.DEVNULL, check=False).returncode == 0)


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

    every = [g.name for g in read_inputs(repo)[0]]
    _write(repo / "newdir/notes.md", "x\n")
    code, out, err = _launch(repo, ["--base", base], env)
    check("git/uncovered-path-selects-full-roster",
          code == 0 and out.split("\n")[:-1] == every and "newdir/notes.md" in err,
          (code, len(out.split("\n")) - 1, len(every), err[:200]))
    code, out, _err = _launch(repo, ["--base", base, "--json"], env)
    doc = _json_selection(out) or {}
    check("git/uncovered-path-json-full-roster", code == 0 and doc.get("status") == "ok"
          and doc.get("uncovered") == ["newdir/notes.md"]
          and [s["gate"] for s in doc.get("selected") or []] == every,
          (code, doc.get("status"), doc.get("uncovered")))
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
    _write(bad / SUITES_REL, _read_bounded(ROOT / SUITES_REL))
    code, out, err = _launch(bad, ["--base", "HEAD"], env)
    check("git/git-failure-exit-2", code == 2 and out == "", (code, err[:200]))

    repo7, base7 = _fixture_repo(work, "r7", env)
    _write(repo7 / os.fsdecode(b"docs/bad-\xff.md"), "x\n")
    code, out, err = _launch(repo7, ["--base", base7], env)
    check("git/non-utf8-path-exit-2", code == 2 and out == "", (code, err[:200]))

    # The observation copy is the index exactly: a staged deletion stays deleted (even when the file
    # is back in the working tree, untracked), a staged rename moves, a staged edit is the staged text.
    repo8 = work / "r8"
    for rel in ("gone.txt", "kept-untracked.txt", "old.txt", "edit.txt", "same.txt"):
        _write(repo8 / rel, rel + "\n")
    _fixture_git(repo8, ["init", "-q"], env)
    _fixture_git(repo8, ["add", "-A"], env)
    _fixture_git(repo8, ["commit", "-q", "-m", "base"], env)
    _fixture_git(repo8, ["rm", "-q", "gone.txt"], env)
    _fixture_git(repo8, ["rm", "-q", "--cached", "kept-untracked.txt"], env)
    _fixture_git(repo8, ["mv", "old.txt", "new.txt"], env)
    _write(repo8 / "edit.txt", "staged\n")
    _fixture_git(repo8, ["add", "edit.txt"], env)
    _write(repo8 / "edit.txt", "unstaged\n")
    _write(repo8 / "untracked.txt", "x\n")
    copy8 = work / "r8-copy"
    try:
        _disposable_copy(repo8, copy8)
        present = sorted(p.name for p in copy8.iterdir() if p.name != ".git")
        edit = (copy8 / "edit.txt").read_text(encoding="utf-8")
        problem = None
    except (CannotEvaluate, OSError) as exc:
        present, edit, problem = None, None, str(exc)
    check("observe/copy-is-the-index-exactly",
          present == ["edit.txt", "new.txt", "same.txt"] and edit == "staged\n",
          (present, edit, problem))


def _self_test_cli():
    got = []
    for argv in (["--bogus"], [], ["--base"], ["--base", "a", "--base", "b"], ["--base", "a", "--json", "--explain"],
                 ["--self-test", "--base", "a"], ["--execution-report", "relative/report"],
                 ["--observe-inputs", "--jobs", "0"], ["--observe-inputs", "--gate"],
                 ["--observe-inputs", "--base", "a"], ["--self-test", "--observe-inputs"]):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got.append(main(argv))
    check("cli/invalid-invocation-exit-2", got == [2] * len(got), got)


def self_test():
    gates_manifest, error = _real_inputs()
    gates, manifest = gates_manifest
    _self_test_roster(gates, manifest, error)
    _self_test_selection(gates, manifest)
    _self_test_regressions(gates, manifest)
    _self_test_live_coverage(gates, manifest)
    _self_test_cli()
    work = Path(tempfile.mkdtemp(prefix="affected-gates-selftest-"))
    try:
        _self_test_observation(work)
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
          "three named misses are selected and each is missed once its mapping is narrowed; a path no "
          "declaration covers selects the full roster; bad revisions, shallow history, unmerged entries, git "
          "failures and non-UTF-8 paths exit 2 with no selection; the observation copy is the index exactly "
          "and metadata-only reads are held by the declarations)".format(len(EXECUTED)))
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
