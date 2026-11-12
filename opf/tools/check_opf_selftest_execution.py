#!/usr/bin/env python3
"""OPF-local execution-set gate for registered self-test suites: prove every registered check of an OPF
suite RAN, not merely that the suite went green, from the OPF root alone (no parent repository needed).

This is the OPF-local adaptation of the parent repository's tools/check_selftest_execution.py; its
child contract, report format and static layer are the same, and the parent file's module docstring
states them in full (fail-closed child contract, disclosed residuals). What differs:

  * ROOT is the OPF root, the directory above this script's own tools/ directory, whatever the working
    directory; it is confirmed by the presence of VERSION and tools/run_all_checks.sh there (a
    standalone opf/ copy has no .git, so the parent's .git proxy does not apply).
  * The expectation manifest is the OPF-local registry tools/selftest_checks.toml under the OPF root,
    never the parent repository's registry. An absent, unreadable, malformed or EMPTY registry is
    cannot-evaluate (exit 2), never a pass.
  * There is ONE launch roster, the OPF runner tools/run_all_checks.sh (the OPF workflow runs that
    runner, and check_opf_init_contract.py checks that it does). Every registered row needs its launch
    line there, exactly
        run_gate "<name>" python3 -I -B "$here/check_opf_selftest_execution.py" --suite <id>
    on a line that is not a comment; the line is split with shlex (no comment stripping), so a trailing
    comment, an extra argument or a near-miss id launches nothing. The check is LEXICAL: it proves the
    line is present, not that it runs or passes.
  * The child shim imports tools/_selftest_exit_report.py from beside the runner, so an OPF runner uses
    the OPF-local copy of that module.

  check_opf_selftest_execution.py --suite <id>   run the registered suite and reconcile its execution set
  check_opf_selftest_execution.py --registry     validate the whole registry without launching: schema
                                                 (a non-empty [[suite]] array), every runner present and
                                                 contained, every row's static check() set reconciled
                                                 (the group rule for shared runners), and every row
                                                 launched from the OPF runner
  check_opf_selftest_execution.py --self-test    synthetic OPF roots and fake runners assert the legs fire

Exit: 0 the execution set matches exactly AND the child passed (for --registry, every row holds); 1 a
real finding (a missing or extra executed id, or a set-complete child that reported failures); 2 usage
or cannot-evaluate (as in the parent gate, plus an unconfirmable OPF root).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_selftest_execution.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import contextlib
import dis
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import types
import unicodedata
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_opf_selftest_execution.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

MANIFEST_TOP_KEYS = {"format-version", "suite"}
SUITE_ROW_KEYS = {"id", "runner", "expected-check-ids"}
# The one optional row key: literal arguments forwarded to the runner's launch ahead of the gate's own
# --execution-report pair (_row_args_problem holds the accepted shape).
SUITE_ROW_OPTIONAL_KEYS = {"args"}
# One forwarded argument: printable ASCII from a closed set (no whitespace, quote, shell, glob or
# expansion character), starting with a letter or digit, or with one or two dashes and then one (so
# no leading '/', '.', '@' or '~', and no bare '-' or '--' end-of-options marker).
ROW_ARG_RE = re.compile(r"(?:--?)?[A-Za-z0-9][A-Za-z0-9._,:=/+-]*")
ROW_ARG_MAX = 200
# The gate's own evidence option: no forwarded argument may name it, attach a value to it, or
# abbreviate it.
REPORT_OPTION = "--execution-report"
# The OPF-local registry and the one launch roster, both relative to the OPF root; the files that
# confirm a directory is an OPF root; and the exact launch argv a roster line must carry after
# run_gate and its name (the id follows it).
MANIFEST_REL = "tools/selftest_checks.toml"
LAUNCH_ROSTER = "tools/run_all_checks.sh"
ROOT_MARKERS = ("VERSION", "tools/run_all_checks.sh")
LAUNCH_ARGV = ("python3", "-I", "-B", "$here/check_opf_selftest_execution.py", "--suite")
# A suite id is one launch-line argument in both rosters and the one CLI argument after --suite, so
# it comes from a CLOSED grammar: a letter or digit first, then letters, digits, '.', '_' and '-'
# only. An id outside it is refused BY NAME, so a space-joined roster identity can never tokenize a
# registered id as more than one argument, and an id can never spell an option (no leading '-').
SUITE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
REPORT_KEYS = {"format_version", "suite", "check_ids", "exit_code", "finalized"}
REPORT_FORMAT_VERSION = 2
# Per suite id, the exact bytes (a bytes value) the child's WHOLE error stream must equal on a run that
# is a verdict; a suite with no entry must leave the stream empty, and any other byte is a fault, never
# a pass. No OPF suite declares any.
DECLARED_STDERR = {}


def _cannot(msg):
    """Print a cannot-evaluate finding (the caller returns 2). A pending, missing, ambiguous, or
    malformed input is unverified, never a pass."""
    print("CANNOT EVALUATE: {}".format(msg), file=sys.stderr)


def _has_ctrl(text):
    """True when text carries a control or format character, decided by this one shared predicate:
    Unicode category Cc (C0, DEL and C1), Cf (the format characters, the bidi controls among them),
    Zl (U+2028) or Zp (U+2029)."""
    return any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in text)


def _manifest_suites(manifest_path):
    """The strictly validated [[suite]] rows of the expectation manifest, or None after printing the
    violation (the caller exits 2). Present-but-unparseable is a refusing failure naming the record,
    never silent absence."""
    try:
        st = os.lstat(manifest_path)
    except OSError as exc:
        _cannot("expectation manifest {} unreadable: {}".format(manifest_path, exc))
        return None
    if not stat.S_ISREG(st.st_mode):
        _cannot("expectation manifest {} is not a regular file (a symlink or directory is refused)"
                .format(manifest_path))
        return None
    try:
        with open(manifest_path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        _cannot("expectation manifest {} unreadable: {}".format(manifest_path, exc))
        return None
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table; neither is a TOMLDecodeError.
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        _cannot("expectation manifest {} is not valid TOML: {}".format(manifest_path, exc))
        return None
    return _manifest_rows(data, manifest_path)


def _manifest_rows(data, manifest_path):
    """The strictly validated [[suite]] rows of an already-parsed expectation manifest, or None after
    printing the violation (the caller exits 2). Shared with the entry-guard gate, so both gates hold the
    registry to one schema."""
    if set(data) != MANIFEST_TOP_KEYS:
        _cannot("{}: top-level keys must be exactly {} (got {})".format(
            manifest_path, sorted(MANIFEST_TOP_KEYS), sorted(data)))
        return None
    if type(data["format-version"]) is not int or data["format-version"] != 1:
        _cannot("{}: format-version must be exactly the integer 1".format(manifest_path))
        return None
    suites = data["suite"]
    if not isinstance(suites, list) or not suites:
        _cannot("{}: [[suite]] must be a non-empty array of tables".format(manifest_path))
        return None
    seen_ids = set()
    for index, row in enumerate(suites):
        where = "{}: suite[{}]".format(manifest_path, index)
        if (not isinstance(row, dict) or not SUITE_ROW_KEYS <= set(row)
                or not set(row) <= SUITE_ROW_KEYS | SUITE_ROW_OPTIONAL_KEYS):
            _cannot("{}: keys must be exactly {} (optionally also {})".format(
                where, sorted(SUITE_ROW_KEYS), sorted(SUITE_ROW_OPTIONAL_KEYS)))
            return None
        sid = row["id"]
        if not isinstance(sid, str) or not SUITE_ID_RE.fullmatch(sid):
            _cannot("{}: id {!r} must match the closed suite-id grammar (a letter or digit first, "
                    "then letters, digits, '.', '_' and '-' only); the id is a single "
                    "launch-line argument, so any other name is refused".format(where, sid))
            return None
        if sid in seen_ids:
            _cannot("{}: duplicate suite id {!r}".format(where, sid))
            return None
        seen_ids.add(sid)
        runner = row["runner"]
        if (not isinstance(runner, str) or not runner or _has_ctrl(runner) or os.path.isabs(runner)
                or ".." in Path(runner).parts):
            _cannot("{}: runner must be a repo-relative path with no '..' and no control or "
                    "format character".format(where))
            return None
        ids = row["expected-check-ids"]
        if not isinstance(ids, list) or not ids:
            _cannot("{}: expected-check-ids must be a non-empty array".format(where))
            return None
        row_seen = set()
        for cid in ids:
            if not isinstance(cid, str) or not cid or _has_ctrl(cid):
                _cannot("{}: check id {!r} must be a non-empty string with no control or format "
                        "character".format(where, cid))
                return None
            if cid in row_seen:
                _cannot("{}: duplicate expected check id {!r}".format(where, cid))
                return None
            row_seen.add(cid)
        if "args" in row:
            problem = _row_args_problem(row["args"])
            if problem is not None:
                _cannot("{}: {}".format(where, problem))
                return None
    return suites


def _row_args_problem(args):
    """None when args is a forwardable argument list, else the reason it is refused: a non-empty array
    of strings, each at most ROW_ARG_MAX characters and matching ROW_ARG_RE, none naming, carrying a
    value for, or abbreviating the gate's own REPORT_OPTION (a runner resolving a unique prefix, as
    argparse does, would otherwise read it as that option). Lexical only: it says nothing about what
    any other accepted argument means to a given runner; a runner that then writes no report is a
    missing report, cannot-evaluate."""
    if not isinstance(args, list) or not args:
        return "args must be a non-empty array of strings (omit the key for no arguments)"
    for arg in args:
        if type(arg) is not str:
            return "args element {!r} is not a string".format(arg)
        if len(arg) > ROW_ARG_MAX or not ROW_ARG_RE.fullmatch(arg):
            return ("args element {!r} is not a literal argument (at most {} characters from "
                    "[A-Za-z0-9._,:=/+-], a letter or digit first, or one or two dashes and then "
                    "one)".format(arg, ROW_ARG_MAX))
        name = arg.split("=", 1)[0]
        if name.startswith("--") and (REPORT_OPTION.startswith(name) or name.startswith(REPORT_OPTION)):
            return ("args element {!r} names, abbreviates, or attaches a value to the gate's own {} "
                    "option".format(arg, REPORT_OPTION))
    return None


def _runner_path(root, runner):
    """The runner resolved inside the repo as a regular non-symlink file, or None after printing (the
    caller exits 2 and NEVER launches)."""
    path = Path(root) / runner
    try:
        st = os.lstat(path)
    except OSError as exc:
        _cannot("runner {} unreadable: {}".format(path, exc))
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        _cannot("runner {} is not a regular non-symlink file".format(path))
        return None
    real_root = os.path.realpath(root)
    try:
        contained = os.path.commonpath([os.path.realpath(path), real_root]) == real_root
    except ValueError:
        contained = False
    if not contained:
        _cannot("runner {} escapes the repo root {}".format(path, root))
        return None
    return path


def _runner_group(root, suites, row):
    """The rows whose runner resolves to the same path as row's (row included, in manifest order), or
    None after printing (the caller exits 2 and NEVER launches). Membership is by resolved path, so
    two spellings of one runner form one group. Every row's runner is resolved STRICTLY
    (os.path.realpath(strict=True)): a missing path, a non-directory component, a symlink loop or a
    permission error leaves that row's membership undecidable, so it is cannot-evaluate, never an
    assumed non-member (a non-strict resolution swallows each of them and returns a lexical guess)."""
    resolved = {}
    for other in suites:
        try:
            resolved[other["id"]] = os.path.realpath(Path(root) / other["runner"], strict=True)
        except (OSError, ValueError, RuntimeError) as exc:
            _cannot("suite {}: cannot decide which rows share its runner, because the runner {} of "
                    "suite {} does not resolve: {}".format(
                        row["id"], other["runner"], other["id"], exc))
            return None
    return [other for other in suites if resolved[other["id"]] == resolved[row["id"]]]


def _unlaunched_rows(root, suites):
    """The diagnostic lines naming each registered row whose launch line is missing from the OPF runner
    (none when every row is launched), or None after printing (the caller exits 2 and NEVER launches)
    when the runner is not a regular non-symlink file, cannot be read as ASCII, or holds a run_gate
    line shlex cannot split. A line counts as a row's launch line only when it is not a comment and
    splits to EXACTLY run_gate, one name, then LAUNCH_ARGV and the id, so a trailing comment, any
    further token or a near-miss id launches nothing. Read-only and LEXICAL."""
    path = Path(root) / LAUNCH_ROSTER
    try:
        st = os.lstat(path)
    except OSError as exc:
        _cannot("launch roster {} unreadable: {}".format(path, exc))
        return None
    if not stat.S_ISREG(st.st_mode):
        _cannot("launch roster {} is not a regular file (a symlink or directory is refused)".format(path))
        return None
    try:
        with open(path, "rb") as handle:
            text = handle.read().decode("ascii")
    except (OSError, UnicodeDecodeError) as exc:
        _cannot("launch roster {} unreadable as ASCII: {}".format(path, exc))
        return None
    launched_ids = set()
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if not stripped.startswith("run_gate "):
            continue
        try:
            argv = shlex.split(stripped, comments=False, posix=True)
        except ValueError as exc:
            _cannot("launch roster {} line {} cannot be split: {}".format(LAUNCH_ROSTER, number, exc))
            return None
        if (len(argv) == len(LAUNCH_ARGV) + 3 and argv[0] == "run_gate" and argv[1]
                and tuple(argv[2:-1]) == LAUNCH_ARGV and argv[-1]):
            launched_ids.add(argv[-1])
    lines = []
    for other in suites:
        if other["id"] not in launched_ids:
            lines.append("  suite {} has no launch line (run_gate \"<name>\" {} {}) in {}".format(
                other["id"], " ".join(LAUNCH_ARGV), other["id"], LAUNCH_ROSTER))
    return lines


def _group_problems(group, source_set):
    """The group rule for two or more rows that share one runner, as diagnostic lines (none when it
    holds): every row carries args, no two rows carry the same args, the rows' expected sets are
    pairwise disjoint, and their union equals the runner's static check() set exactly."""
    lines, launches, owners = [], {}, {}
    for row in group:
        if "args" not in row:
            lines.append("  suite {} shares its runner but carries no args, so its launch cannot "
                         "differ from the others'".format(row["id"]))
        else:
            launch = tuple(row["args"])
            if launch in launches:
                lines.append("  suites {} and {} carry the same args {}".format(
                    launches[launch], row["id"], list(launch)))
            else:
                launches[launch] = row["id"]
        for cid in row["expected-check-ids"]:
            owners.setdefault(cid, []).append(row["id"])
    for cid in sorted(owners):
        if len(owners[cid]) > 1:
            lines.append("  overlap: {} registered by suites {}".format(cid, ", ".join(owners[cid])))
    for cid in sorted(source_set - set(owners)):
        lines.append("  gap: source check registered by no suite of the group: {}".format(cid))
    for cid in sorted(set(owners) - source_set):
        lines.append("  extra: registered id has no source check(): {}".format(cid))
    return lines


class _CheckIdVisitor(ast.NodeVisitor):
    """Resolve the first argument of every DIRECT call to the bare name check() to string-literal
    ids. Two patterns only: a string constant, or a Name bound by an enclosing for over a tuple/list
    of tuple/list rows carrying a string literal at that Name's target position. Anything else is a
    fail-closed error, never a guess: a Load of the bare name check that is not the callee of a call
    this visitor resolves (an alias such as renamed = check would carry a call site beyond this
    scan's reach) and a Name id inside a comprehension (whose generator binding the lexical
    for-stack does not model, so an enclosing for could mis-attribute it) are both refused. ast
    carries no parent links, so the visitor keeps its own stack of the enclosing For nodes, a
    comprehension depth, and the id() of every callee Name it resolved."""

    def __init__(self, runner_path):
        self.runner_path = runner_path
        self.for_stack = []
        self.comp_depth = 0
        self.callee_names = set()   # id() of each Name node resolved as a direct check() callee
        self.found = []      # (check id, line) per literal site or per resolved loop row
        self.errors = []

    def visit_For(self, node):
        self.for_stack.append(node)
        self.generic_visit(node)
        self.for_stack.pop()

    def _visit_comprehension(self, node):
        self.comp_depth += 1
        self.generic_visit(node)
        self.comp_depth -= 1

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    def visit_Name(self, node):
        if (node.id == "check" and isinstance(node.ctx, ast.Load)
                and id(node) not in self.callee_names):
            self.errors.append(
                "check referenced outside a direct call at {}:{}; the static layer resolves only "
                "direct check(...) call sites".format(self.runner_path, node.lineno))

    def visit_Call(self, node):
        is_check = isinstance(node.func, ast.Name) and node.func.id == "check"
        if is_check:
            # Marked BEFORE the generic descent reaches it, so visit_Name does not flag the very
            # reference this call resolves.
            self.callee_names.add(id(node.func))
        self.generic_visit(node)
        if not is_check:
            return
        arg = node.args[0] if node.args else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            self.found.append((arg.value, node.lineno))
            return
        if isinstance(arg, ast.Name) and self.comp_depth:
            self.errors.append(
                "check() id resolved inside a comprehension at {}:{}; unresolvable".format(
                    self.runner_path, node.lineno))
            return
        rows = self._loop_rows(arg.id) if isinstance(arg, ast.Name) else None
        if rows is None:
            self.errors.append(
                "unresolvable check id at {}:{}; every check id must be a string literal or a "
                "for-loop literal".format(self.runner_path, node.lineno))
        else:
            self.found.extend(rows)

    def _loop_rows(self, name):
        """The (id, line) rows the innermost enclosing for binds to Name <name>, or None when no
        enclosing for binds it as a tuple target over all-literal rows at its position, or when the
        literal iterable is empty (a statically empty loop would otherwise resolve its check() site
        to zero ids and silently drop it)."""
        for loop in reversed(self.for_stack):
            target = loop.target
            if isinstance(target, ast.Name):
                if target.id == name:
                    return None
                continue
            if not isinstance(target, ast.Tuple):
                continue
            names = [elt.id if isinstance(elt, ast.Name) else None for elt in target.elts]
            if name not in names:
                continue
            pos = names.index(name)
            if not isinstance(loop.iter, (ast.Tuple, ast.List)):
                return None
            rows = []
            for row in loop.iter.elts:
                if (not isinstance(row, (ast.Tuple, ast.List)) or len(row.elts) != len(names)
                        or not isinstance(row.elts[pos], ast.Constant)
                        or not isinstance(row.elts[pos].value, str)):
                    return None
                rows.append((row.elts[pos].value, row.lineno))
            return rows if rows else None
        return None


def _source_check_ids(runner_path):
    """The statically resolved check ids of every check() call site in the runner's source, as
    (sorted id list, None); or (None, error) on a source that does not parse, an unresolvable id, or
    a duplicate call-site id. Fail closed: no id is guessed, and source-site uniqueness is required
    (a dead duplicate-id alias would otherwise hide behind its reachable twin in the runtime set)."""
    try:
        source = Path(runner_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, "runner {} unreadable as UTF-8 source: {}".format(runner_path, exc)
    try:
        tree = ast.parse(source, filename=str(runner_path))
    except (SyntaxError, ValueError) as exc:
        return None, "runner {} does not parse: {}".format(runner_path, exc)
    visitor = _CheckIdVisitor(runner_path)
    visitor.visit(tree)
    if visitor.errors:
        return None, "; ".join(visitor.errors)
    first_seen = {}
    for cid, line in visitor.found:
        if cid in first_seen:
            return None, "duplicate source check id {!r} at lines {} and {} in {}".format(
                cid, first_seen[cid], line, runner_path)
        first_seen[cid] = line
    return sorted(first_seen), None


def _forward(blob, stream):
    """Forward the child's captured bytes IN FULL to our own stream: no filter, no truncation. Bytes go
    to the raw buffer where one exists; under an in-process capture (the self-test's StringIO) there is
    no buffer, so the bytes are decoded with replacement, which substitutes only invalid sequences and
    never drops content."""
    if not blob:
        return
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        stream.flush()
        buffer.write(blob)
        buffer.flush()
    else:
        stream.write(blob.decode("utf-8", errors="replace"))


def _reject_dup_keys(pairs):
    """json object_pairs_hook: a duplicate member is a malformed report, refused, so a doctored
    report cannot carry two check_ids members and have the last one silently win."""
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate JSON object member {!r}".format(key))
        obj[key] = value
    return obj


def _read_report(report_path, suite_id, returncode):
    """The report's check-id list, strictly validated, or None after printing (the caller exits 2). A
    child exit 0 never overrides an invalid report; a run that did not deliver its agreed structured
    verdict evidence is no verdict. Only the FINALIZED report (tools/_selftest_exit_report.py, written
    at interpreter exit after the child's cleanup, its exit_code the status the child then exited
    with) is accepted; an in-band format-1 report is refused."""
    try:
        st = os.lstat(report_path)
    except OSError:
        _cannot("no execution report at {}; a run that did not deliver its report is no verdict"
                .format(report_path))
        return None
    if not stat.S_ISREG(st.st_mode):
        _cannot("execution report {} is not a regular file (a symlink or directory is refused)"
                .format(report_path))
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=_reject_dup_keys)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError and the duplicate-member rejection above.
        _cannot("execution report {} unreadable or malformed: {}".format(report_path, exc))
        return None
    if not isinstance(data, dict) or set(data) != REPORT_KEYS:
        _cannot("execution report {}: keys must be exactly {}".format(report_path, sorted(REPORT_KEYS)))
        return None
    if type(data["format_version"]) is not int or data["format_version"] != REPORT_FORMAT_VERSION:
        _cannot("execution report {}: format_version must be exactly the integer {} (the report "
                "finalized at exit)".format(report_path, REPORT_FORMAT_VERSION))
        return None
    if data["finalized"] is not True:
        _cannot("execution report {}: finalized must be exactly true".format(report_path))
        return None
    if type(data["exit_code"]) is not int or data["exit_code"] != returncode:
        _cannot("execution report {}: exit_code {!r} is not the child's exit status {}".format(
            report_path, data["exit_code"], returncode))
        return None
    if data["suite"] != suite_id:
        _cannot("execution report {}: suite {!r} is not the requested suite {!r}".format(
            report_path, data["suite"], suite_id))
        return None
    ids = data["check_ids"]
    if not isinstance(ids, list) or not all(isinstance(cid, str) and cid for cid in ids):
        _cannot("execution report {}: check_ids must be a list of non-empty strings".format(report_path))
        return None
    if len(set(ids)) != len(ids):
        dupes = sorted(cid for cid in set(ids) if ids.count(cid) > 1)
        _cannot("execution report {}: duplicate observed check ids {}".format(report_path, dupes))
        return None
    return ids


def _stderr_fault(blob, suite_id):
    """The child's WHOLE error stream compared byte for byte with the suite's declared bytes (empty when
    the suite declares none): None on equality, else a description of the first differing byte. No
    decoding, line splitting, repetition, or reordering is applied, and a declaration that is not a
    bytes value is itself a fault. A fault the child survived (an atexit callback, a destructor, a
    thread) that writes to the error stream reaches only this stream, so it is never a pass; a fault
    that writes nothing (the disclosed late-teardown residual class) is invisible here."""
    allowed = DECLARED_STDERR.get(suite_id, b"")
    if type(allowed) is not bytes:
        return "the declared stderr for suite {!r} is not a bytes value".format(suite_id)
    if blob == allowed:
        return None
    at = next((i for i, (got, want) in enumerate(zip(blob, allowed)) if got != want),
              min(len(blob), len(allowed)))
    return "stderr differs from the {} declared bytes at byte {}: {!r}".format(
        len(allowed), at, blob[at:at + 200])


def run_suite(root, suite_id):
    """Validate the manifest, statically reconcile the runner's source check() set against it BEFORE
    any launch (the whole set for a single-row runner; for rows that share a runner, the group
    partition), require a launch line for every registered row in the OPF runner, then launch
    the registered runner with the row's args and a private report path, forward its full output,
    require an error stream byte-identical to the suite's declared bytes (empty unless declared), and
    reconcile the executed set from the report finalized at the child's exit. Returns the gate exit
    code."""
    root = Path(root)
    manifest_path = root / MANIFEST_REL
    suites = _manifest_suites(manifest_path)
    if suites is None:
        return 2
    row = next((r for r in suites if r["id"] == suite_id), None)
    if row is None:
        _cannot("no suite {!r} registered in {}".format(suite_id, manifest_path))
        return 2
    expected = set(row["expected-check-ids"])
    runner = _runner_path(root, row["runner"])
    if runner is None:
        return 2
    group = _runner_group(root, suites, row)
    if group is None:
        return 2
    source_ids, error = _source_check_ids(runner)
    if error is not None:
        _cannot(error)
        return 2
    source_set = set(source_ids)
    if len(group) > 1:
        problems = _group_problems(group, source_set)
        if problems:
            _cannot("suite {}: the {} rows of {} sharing its runner do not partition the runner's "
                    "static check() set; rows that share a runner need pairwise distinct args, "
                    "pairwise disjoint expected sets, and a union equal to the source set exactly "
                    "before any launch".format(suite_id, len(group), manifest_path))
            for line in problems:
                print(line, file=sys.stderr)
            return 2
    elif source_set != expected:
        _cannot("suite {}: the runner's static check() set does not match {}; the manifest must "
                "mirror the source exactly before any launch".format(suite_id, manifest_path))
        for cid in sorted(source_set - expected):
            print("  source check not registered: {}".format(cid), file=sys.stderr)
        for cid in sorted(expected - source_set):
            print("  registered id has no source check(): {}".format(cid), file=sys.stderr)
        return 2
    unlaunched = _unlaunched_rows(root, suites)
    if unlaunched is None:
        return 2
    if unlaunched:
        _cannot("suite {}: not every row of {} has a launch line in the OPF runner; a registered "
                "row that is never launched leaves its ids unexecuted, so no suite is launched until "
                "every row is".format(suite_id, manifest_path))
        for line in unlaunched:
            print(line, file=sys.stderr)
        return 2
    try:
        private = tempfile.mkdtemp(prefix="opf-selftest-exec-")
    except OSError as exc:
        _cannot("cannot create the private report directory: {}".format(exc))
        return 2
    try:
        report_path = os.path.join(private, "execution-report.json")
        if os.path.lexists(report_path):
            _cannot("report path {} already exists before launch".format(report_path))
            return 2
        # The fresh EMPTY per-launch home the child's HOME and XDG_CONFIG_HOME pin to (see the
        # child-env comment below): under `private`, so the finally's rmtree removes it too.
        git_home = os.path.join(private, "home")
        try:
            os.mkdir(git_home)
        except OSError as exc:
            _cannot("cannot create the neutral child home directory: {}".format(exc))
            return 2
        # The bootstrap shim: the helper module beside the runner installs the observation hooks
        # BEFORE any runner statement (import-time included), then runs the runner as __main__ with
        # the [<runner>, <row args>..., --execution-report, <path>] argv it expects (_bootstrap_main
        # documents the shim's own argv contract; the row's args, validated by _row_args_problem,
        # follow the report path here and are absent for a row with no args key). A raw launch of
        # the runner would leave its import-time and pre-arm code outside the hooks, a
        # pre-arm thread gap.
        bootstrap = ("import sys; sys.path.insert(0, sys.argv[1]); "
                     "import _selftest_exit_report; _selftest_exit_report._bootstrap_main()")
        command = [sys.executable, "-I", "-B", "-c", bootstrap,
                   str(runner.parent), str(runner), report_path] + list(row.get("args", ()))
        # Git-neutral, interpreter-neutral child environment. The git side has two layers. Layer
        # one, GIT_*: drop every ambient GIT_*
        # variable, then pin the global and system config surfaces to os.devnull, so a hostile
        # GIT_CONFIG_GLOBAL or core.hooksPath cannot make the child's git subprocesses run an
        # external hook or read a decoy repository (the runner's fixtures set their git identity
        # inline with -c, which outranks the injected entries below, so commits still work).
        # GIT_CONFIG_GLOBAL covers only the global CONFIG file: git reads the global ignore and
        # attributes surfaces from XDG_CONFIG_HOME/git/{ignore,attributes} defaults even with no
        # config file, so core.excludesFile and core.attributesFile are overridden to os.devnull
        # through git's environment-config mechanism (GIT_CONFIG_COUNT/KEY/VALUE, set AFTER the
        # drop loop so they survive it). Layer two, HOME/XDG_CONFIG_HOME: the GIT_* pins alone are
        # NOT transitive, because the child imports aiqt_hooks, whose recovery-snapshot code scrubs
        # every GIT_*-prefixed variable before its own git subprocesses (the allowlist posture the
        # inert-data rule requires), dropping the layer-one pins and re-exposing ~/.gitconfig and
        # XDG_CONFIG_HOME/git/{config,ignore,attributes} to every descendant git call; HOME and
        # XDG_CONFIG_HOME are not GIT_-prefixed, so that scrub cannot strip them, and pinning both
        # at the fresh empty git_home keeps the per-user config, ignore, and attributes surfaces
        # empty for EVERY descendant call, scrubbed or not (layer one stays as defence in depth for
        # the immediate child; the system-wide config and attributes residual for a scrubbing
        # descendant is disclosed in the module docstring). The drop loop also strips every ambient
        # PYTHON*-prefixed variable: -I isolates only the immediate child, which passes its
        # environment on, and the runner launches interpreter grandchildren, so a hostile
        # PYTHONPATH would otherwise run an attacker sitecustomize module in them. Unlike the GIT_*
        # pins this drop IS transitive, because nothing downstream re-derives or re-sets a PYTHON*
        # variable, so no descendant interpreter ever sees one; the runner's grandchild launch
        # sites carry -I -B as well, defence in depth for a direct developer run. This hardens the
        # GATE-launched child only; the runner's own direct-invocation hermeticity is tracked
        # separately.
        child_env = dict(os.environ)
        for key in list(child_env):
            if key.startswith("GIT_") or key.startswith("PYTHON"):
                del child_env[key]
        child_env["GIT_CONFIG_GLOBAL"] = os.devnull
        child_env["GIT_CONFIG_SYSTEM"] = os.devnull
        child_env["GIT_CONFIG_NOSYSTEM"] = "1"
        child_env["GIT_CONFIG_COUNT"] = "2"
        child_env["GIT_CONFIG_KEY_0"] = "core.excludesFile"
        child_env["GIT_CONFIG_VALUE_0"] = os.devnull
        child_env["GIT_CONFIG_KEY_1"] = "core.attributesFile"
        child_env["GIT_CONFIG_VALUE_1"] = os.devnull
        child_env["HOME"] = git_home
        child_env["XDG_CONFIG_HOME"] = git_home
        try:
            child = subprocess.run(command, cwd=str(root), capture_output=True, env=child_env)
        except OSError as exc:
            _cannot("cannot launch {}: {}".format(command, exc))
            return 2
        _forward(child.stdout, sys.stdout)
        _forward(child.stderr, sys.stderr)
        if child.returncode not in (0, 1):
            _cannot("child exited {} (a harness error or signal is no verdict)".format(child.returncode))
            return 2
        fault = _stderr_fault(child.stderr, suite_id)
        if fault is not None:
            _cannot("suite {}: the child's error stream carries a fault ({}); a run that faulted, "
                    "even after its last check, is no verdict".format(suite_id, fault))
            return 2
        observed = _read_report(report_path, suite_id, child.returncode)
        if observed is None:
            return 2
        missing = sorted(expected - set(observed))
        extra = sorted(set(observed) - expected)
        if missing or extra:
            print("FAIL: suite {} execution set does not match {}".format(suite_id, manifest_path))
            for cid in missing:
                print("  missing (registered, never executed): {}".format(cid))
            for cid in extra:
                print("  extra (executed, not registered): {}".format(cid))
            return 1
        if child.returncode == 1:
            print("FAIL: suite {}: execution set complete; child reported assertion failures "
                  "(see its output above)".format(suite_id))
            return 1
        digest = hashlib.sha256(("\n".join(sorted(observed)) + "\n").encode("utf-8")).hexdigest()
        print("PASS: suite {} executed {} unique registered checks; set-sha256={}".format(
            suite_id, len(observed), digest))
        return 0
    finally:
        shutil.rmtree(private, ignore_errors=True)


def validate_registry(root):
    """Validate the whole OPF registry WITHOUT launching any suite: the strict schema (an empty
    [[suite]] array refuses), every row's runner present, regular and contained, every row's static
    check() set reconciled (a single-row runner's set equals its row exactly; rows that share a runner
    partition it), and every row launched from the OPF runner. Returns 0, or 2 after printing."""
    root = Path(root)
    manifest_path = root / MANIFEST_REL
    suites = _manifest_suites(manifest_path)
    if suites is None:
        return 2
    total = 0
    for row in suites:
        runner = _runner_path(root, row["runner"])
        if runner is None:
            return 2
        group = _runner_group(root, suites, row)
        if group is None:
            return 2
        source_ids, error = _source_check_ids(runner)
        if error is not None:
            _cannot(error)
            return 2
        source_set = set(source_ids)
        expected = set(row["expected-check-ids"])
        if len(group) > 1:
            problems = _group_problems(group, source_set)
        else:
            problems = (["  source check not registered: {}".format(cid)
                         for cid in sorted(source_set - expected)]
                        + ["  registered id has no source check(): {}".format(cid)
                           for cid in sorted(expected - source_set)])
        if problems:
            _cannot("suite {}: the runner's static check() set does not match {}".format(
                row["id"], manifest_path))
            for line in problems:
                print(line, file=sys.stderr)
            return 2
        total += len(expected)
    unlaunched = _unlaunched_rows(root, suites)
    if unlaunched is None:
        return 2
    if unlaunched:
        _cannot("not every row of {} has a launch line in {}".format(manifest_path, LAUNCH_ROSTER))
        for line in unlaunched:
            print(line, file=sys.stderr)
        return 2
    print("PASS: registry {} holds {} suite(s), {} registered check id(s); every row reconciled and "
          "launched".format(MANIFEST_REL, len(suites), total))
    return 0


def _root_problem(root):
    """None when root carries every ROOT_MARKERS file as a regular file, else the reason it does not."""
    for rel in ROOT_MARKERS:
        try:
            st = os.lstat(Path(root) / rel)
        except OSError:
            return "no {} at {}".format(rel, root)
        if not stat.S_ISREG(st.st_mode):
            return "{} at {} is not a regular file".format(rel, root)
    return None


# -------------------------------------------------------------------------- the hermetic self-test

_DEMO_IDS = ("a/one", "a/two", "a/three")
_DEMO_LAUNCH = 'run_gate "demo-exec" python3 -I -B "$here/check_opf_selftest_execution.py" --suite demo\n'
_DEMO_PROBE = 'run_gate "child-status" python3 -I -B "$here/_child_status.py"\n'
_DEMO_MANIFEST = ('format-version = 1\n\n[[suite]]\nid = "demo"\nrunner = "tools/fake_runner.py"\n'
                  'expected-check-ids = ["a/one", "a/two", "a/three"]\n')
_DEMO_REPORTING = ("import sys\nfrom pathlib import Path\n"
                   "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
                   "import _selftest_exit_report\nEXECUTED = []\n"
                   "def check(cid):\n    EXECUTED.append(cid)\n"
                   "report = sys.argv[sys.argv.index('--execution-report') + 1]\n"
                   "_selftest_exit_report.arm(report, 'demo', EXECUTED)\n")
_DEMO_ALL = 'check("a/one")\ncheck("a/two")\ncheck("a/three")\n'


def _demo_root(base, manifest=_DEMO_MANIFEST, runner=None, roster=None):
    """A synthetic OPF root under base: VERSION, the runner roster, the registry (omitted when manifest
    is None), the report module beside the fake runner, and the fake runner itself."""
    root = Path(base)
    (root / "tools").mkdir(parents=True)
    (root / "VERSION").write_text("0.0.0\n", encoding="utf-8")
    (root / LAUNCH_ROSTER).write_text(
        "#!/usr/bin/env bash\n" + (_DEMO_PROBE + _DEMO_LAUNCH if roster is None else roster), encoding="utf-8")
    if manifest is not None:
        (root / MANIFEST_REL).write_text(manifest, encoding="utf-8")
    report_module = Path(__file__).resolve().parent / "_selftest_exit_report.py"
    (root / "tools" / "_selftest_exit_report.py").write_text(
        report_module.read_text(encoding="utf-8"), encoding="utf-8")
    body = (_DEMO_REPORTING + _DEMO_ALL + "_selftest_exit_report.exit_with(0)\n") if runner is None else runner
    (root / "tools" / "fake_runner.py").write_text(body, encoding="utf-8")
    return root


def _quiet(call, *args):
    """call(*args) with both streams captured; returns (exit code, stdout text, stderr text)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = call(*args)
    return code, out.getvalue(), err.getvalue()


def self_test():
    """Synthetic OPF roots and fake runners, run through run_suite and validate_registry in process (the
    fake runners run as real children through the bootstrap shim). Each vector names the leg it pins."""
    failures = []
    count = 0
    reporting = _DEMO_REPORTING
    vectors = (
        # (label, mode, manifest, runner, roster, expected exit, text the streams must carry)
        ("good suite", "suite", _DEMO_MANIFEST, None, None, 0, "PASS: suite demo executed 3"),
        ("good registry", "registry", _DEMO_MANIFEST, None, None, 0, "PASS: registry"),
        ("empty registry refuses (suite)", "suite", "format-version = 1\nsuite = []\n", None, None, 2,
         "must be a non-empty array"),
        ("empty registry refuses (registry)", "registry", "format-version = 1\nsuite = []\n", None, None, 2,
         "must be a non-empty array"),
        ("absent registry refuses", "registry", None, None, None, 2, "expectation manifest"),
        ("malformed registry refuses", "registry", "format-version = [\n", None, None, 2, "not valid TOML"),
        ("unknown suite refuses", "suite-other", _DEMO_MANIFEST, None, None, 2, "no suite 'other'"),
        ("bare exit 0 without a report refuses", "suite", _DEMO_MANIFEST,
         "import sys\ndef check(cid):\n    pass\n" + _DEMO_ALL + "sys.exit(0)\n", None, 2,
         "no execution report"),
        ("dead registered check is a missing finding", "suite", _DEMO_MANIFEST,
         reporting + 'check("a/one")\ncheck("a/two")\nif len(EXECUTED) > 5:\n    check("a/three")\n'
         "_selftest_exit_report.exit_with(0)\n", None, 1, "missing (registered, never executed): a/three"),
        ("complete set with a failing child is a finding", "suite", _DEMO_MANIFEST,
         reporting + _DEMO_ALL + "_selftest_exit_report.exit_with(1)\n", None, 1, "child reported assertion"),
        ("unregistered source check refuses before launch (suite)", "suite", _DEMO_MANIFEST,
         reporting + _DEMO_ALL + 'check("a/four")\n_selftest_exit_report.exit_with(0)\n', None, 2,
         "source check not registered: a/four"),
        ("unregistered source check refuses (registry)", "registry", _DEMO_MANIFEST,
         reporting + _DEMO_ALL + 'check("a/four")\n_selftest_exit_report.exit_with(0)\n', None, 2,
         "source check not registered: a/four"),
        ("row with no launch line refuses (suite)", "suite", _DEMO_MANIFEST, None, _DEMO_PROBE, 2,
         "has no launch line"),
        ("row with no launch line refuses (registry)", "registry", _DEMO_MANIFEST, None, _DEMO_PROBE, 2,
         "has no launch line"),
        ("commented launch line launches nothing", "registry", _DEMO_MANIFEST, None,
         _DEMO_PROBE + "# " + _DEMO_LAUNCH, 2, "has no launch line"),
        ("launch line with a trailing comment launches nothing", "registry", _DEMO_MANIFEST, None,
         _DEMO_PROBE + _DEMO_LAUNCH.rstrip("\n") + " # note\n", 2, "has no launch line"),
        ("launch line with a near-miss id launches nothing", "registry", _DEMO_MANIFEST, None,
         _DEMO_PROBE + _DEMO_LAUNCH.replace("--suite demo", "--suite demo2"), 2, "has no launch line"),
        ("launch line through the parent gate launches nothing", "registry", _DEMO_MANIFEST, None,
         _DEMO_PROBE + _DEMO_LAUNCH.replace("$here/check_opf_selftest_execution.py",
                                            "$here/../../tools/check_selftest_execution.py"), 2,
         "has no launch line"),
    )
    for label, mode, manifest, runner, roster, expected, needle in vectors:
        count += 1
        with tempfile.TemporaryDirectory(prefix="opf-selftest-exec-st-") as tmp:
            root = _demo_root(Path(tmp) / "opf", manifest, runner, roster)
            if mode == "registry":
                code, out, err = _quiet(validate_registry, root)
            else:
                code, out, err = _quiet(run_suite, root, "demo" if mode == "suite" else "other")
        if code != expected or needle not in out + err:
            failures.append("{}: expected exit {} carrying {!r}, got {}: {}".format(
                label, expected, needle, code, (out + err)[-400:]))
    count += 1
    with tempfile.TemporaryDirectory(prefix="opf-selftest-exec-st-") as tmp:
        root = _demo_root(Path(tmp) / "opf")
        if _root_problem(root) is not None:
            failures.append("root confirmation: a complete synthetic root was refused")
        (root / "VERSION").unlink()
        if _root_problem(root) is None:
            failures.append("root confirmation: a root without VERSION was confirmed")
    count += 1
    if _root_problem(Path(__file__).resolve().parent.parent) is not None:
        failures.append("root confirmation: this gate's own OPF root was refused")
    for failure in failures:
        print("FAIL: " + failure)
    if failures:
        print("SELF-TEST FAIL: check_opf_selftest_execution {} of {} vectors failed".format(len(failures), count))
        return 1
    print("SELF-TEST PASS: check_opf_selftest_execution {} vectors (an empty, absent or malformed registry "
          "refuses; a bare exit 0 without a report refuses; dead, unregistered and unlaunched checks are "
          "findings)".format(count))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        return self_test()
    if argv == ["--registry"] or (len(argv) == 2 and argv[0] == "--suite" and argv[1]):
        # Anchored to this file, never the cwd, so a run from any working directory validates this OPF
        # tree and no other.
        root = Path(__file__).resolve().parent.parent
        problem = _root_problem(root)
        if problem is not None:
            _cannot("cannot confirm the gate's own OPF root ({})".format(problem))
            return 2
        if argv == ["--registry"]:
            return validate_registry(root)
        return run_suite(root, argv[1])
    print("usage: check_opf_selftest_execution.py --suite <id> | --registry | --self-test", file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in tools/_child_status.py (under the OPF
# root), which the parent repository's tools/check_child_status_coverage.py pins this copy to by AST.
# Inline, not imported, so this gate stays one file with no import beyond the report module its children
# load. This file launches every registered suite and reads each child's exit status, so under an ignored
# SIGCHLD it would read a failing suite as a pass; it refuses at its entry line instead, before main()
# runs.
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
    sys.exit(_child_status_refusal_exit("check_opf_selftest_execution.py") or main())
