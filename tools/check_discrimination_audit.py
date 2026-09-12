#!/usr/bin/env python3
"""Discrimination-audit gate (v1): prove every NEGATIVE self-test fixture genuinely exercises the guard it
claims (kill the D1MASK class).

A NEGATIVE fixture is a `check(name, cond)` whose `cond` asserts a NON-CLEAN verdict (`.status == INVALID`
/ `== CANNOT_EVALUATE`, a `bool(<guard>(...))`, an `is not None` finding, a non-zero exit). Its coverage
claim is: "with the production guard for defect D present, this input is judged non-clean BECAUSE of D." The
D1MASK class is a negative fixture whose verdict actually rests on an INCIDENTAL invalidity (a missing
`schema` marker, a secondary guard that short-circuits) rather than on D, so it stays non-clean even when
D's guard is reverted and would silently miss a real regression in D. codex found these recurringly across
OPF hardening rounds 18 to 21.

For every marked negative fixture this gate reverts the SPECIFIC production guard it targets, in isolation,
in a subprocess over a disposable scratch copy, and asserts the fixture RED (flips from its passing
non-clean verdict to a failing one). A fixture that does NOT red under its guard revert is masked
(non-discriminating) and is a finding. This is the mechanical, recurrence-proof form of the executed-proof
durable-half rule (10-QUALI-change-carries-check) applied to the whole negative-fixture population.

MECHANISM (grounded on tools/check_gensrc_failclose.py and tools/check_selftest_execution.py):
  - MAPPING is annotation-primary: inline `# audit-guard: <id[,id...]> neuter=<op>` markers at guard sites,
    AST-located (anchored to the statement immediately below the marker, never a line number), plus a
    (b)-style completeness backstop, a static negative-fixture classifier, so an unmarked negative fixture
    is caught too. A classifier-ambiguous fixture is cannot-evaluate unless the author tags it
    `# audit-fixture: negative` / `# audit-fixture: positive` on the check() line (fail closed, never a
    guess: 10-ACCUR-guard-input-soundness).
  - NEUTER is a closed vocabulary applied to the located AST node, then `ast.unparse`d and re-parsed as a
    sanity gate: `block-to-pass` (the compound statement's body becomes `pass`), `predicate-false` /
    `predicate-true` (the if/while test becomes a constant), `return-swap=<FROM>-><TO>` (a returned name is
    swapped). A guard the vocabulary cannot express is cannot-evaluate, never a guessed neuter.
  - ISOLATION borrows check_gensrc_failclose: a pristine TEMPLATE copy of tools/ made once; each run copies
    the template into its own fresh disposable holder, writes the (unparsed baseline or neutered) module,
    and runs `python3 -I -B tools/<module> --outcome-report <abs>` as a subprocess with a sanitized,
    pinned env (per-call HOME/TMPDIR), a per-run timeout, and a total-runtime deadline. The BASELINE is the
    UNPARSED-but-not-neutered module (not the raw pristine file), so the ONLY difference between the
    baseline run and a neutered run is the single neutered node and an `ast.unparse` round-trip cannot be
    misattributed as a flip. A before/after manifest proves the REAL tree is byte-and-mode identical.
  - JUDGEMENT is by the structured `--outcome-report` (tools/_audit_outcome.py), never by grepping output
    (10-QUALI-lightweight-verifier-workers). A report that is missing, malformed, wrong-suite, or
    duplicate-keyed is cannot-evaluate, never a pass.

  check_discrimination_audit.py                      sweep every enrolled suite in the roster (full)
  check_discrimination_audit.py --suite <id>         one enrolled suite (static reconcile + dynamic flip)
  check_discrimination_audit.py --suite <id> --only-marked <cid>[,<cid>...]
                                                     dynamic-flip proof for the named marked fixtures only
                                                     (skips the corpus-wide completeness reconcile; the
                                                     phase-2 sweep is the full run above)
  check_discrimination_audit.py --self-test          synthetic mini-suites assert this gate's own invariants

EXIT CONVENTION (matches the repo's gates):
  0  every claimed negative fixture reds under its annotated guard revert; static reconcile clean (full
     mode); real tree unchanged.
  1  a real finding: a masked fixture (a claimed id did not red), a negative fixture with no marker, or a
     marker naming a non-negative/unknown id.
  2  cannot-evaluate: a classifier-ambiguous fixture; a mis-declared marker or a neuter the vocabulary
     cannot apply; a non-green baseline; a malformed/missing outcome report; a scratch/subprocess/timeout/
     cleanup failure; or a real-tree change during the run.
When both a confirmed finding and a cannot-evaluate condition are present the exit is 1 (the confirmed live
defect is the higher-signal outcome), every condition still printed; a real-tree integrity or cleanup
failure overrides to 2.

DISCLOSED RESIDUALS. The gate proves each claimed negative fixture is LOAD-BEARING for its named guard, not
that the guard is correct, and does not check positive fixtures or grade severity. Attribution is
per-fixture: over-kill (a neuter that also reds fixtures the marker did not claim) is NOT a finding, only
that the CLAIMED ids red. v1 cardinality is one primary discriminating guard per negative fixture; a
genuinely multi-guard fixture is split or its primary guard named. The static classifier is closed and
conservative and fails closed on any `cond` it cannot map, so a negative fixture the classifier mislabels
POSITIVE and that carries no marker is the residual false-negative (a mislabelled negative WITH a marker is
caught by the flip). The subprocess is corruption/hermeticity isolation, not an OS security sandbox: the
gate runs the project's OWN trusted self-tests, so an adversarial in-suite generator reaching outside its
sandbox is out of scope and left to code review, exactly as check_gensrc_failclose discloses; the
before/after manifest and containment re-validation detect a real-tree write after the fact, they do not
prevent an arbitrary side effect. `ast.unparse` round-trip is validated by re-parse and by the
unparsed-pristine baseline; a module that does not round-trip green is cannot-evaluate, surfaced, never
skipped.
"""
import sys

# Importing this gate (and, in --self-test, driving synthetic modules) must write no .pyc into the real
# tools/ tree; the subprocess env also carries PYTHONDONTWRITEBYTECODE=1.
sys.dont_write_bytecode = True

import ast  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import stat  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

# The enrolled suites: suite id -> the module (repo-relative) whose `check()` choke point carries the
# _audit_outcome seam. The roster is DATA, so widening scope corpus-wide is adding a row, not
# re-architecting (DESIGN section 1.3). v1 core enrols only _opf_schema (the first module given the seam
# and a proof set of audit-guard markers); _opf_release and _opf_check join in the phase-2 sweep once they
# carry the seam and their markers.
ROSTER = (
    {"suite": "opf-schema", "module": "tools/_opf_schema.py"},
)

# The outcome sentinels the negative-fixture classifier keys on (the OPF VALID/INVALID/CANNOT_EVALUATE
# outcome model, imported by name into every in-scope suite).
_NEGATIVE_STATUS = frozenset({"INVALID", "CANNOT_EVALUATE"})
_POSITIVE_STATUS = frozenset({"VALID"})

# A guard-site marker: `# audit-guard: <id>[,<id>...] neuter=<op>`. Anchored to the statement immediately
# below it (AST-located), never a line number.
_MARKER_RE = re.compile(r"^\s*#\s*audit-guard:\s*(?P<ids>.+?)\s+neuter=(?P<op>\S+)\s*$")
# A classifier override on a `check(...)` line: `# audit-fixture: negative|positive`.
_FIXTURE_TAG_RE = re.compile(r"#\s*audit-fixture:\s*(?P<kind>negative|positive)\b")
# A return-swap op: `return-swap=<FROM>-><TO>`.
_RETURN_SWAP_RE = re.compile(r"^return-swap=(?P<frm>[A-Za-z_][A-Za-z0-9_]*)->(?P<to>[A-Za-z_][A-Za-z0-9_]*)$")
_SIMPLE_OPS = frozenset({"block-to-pass", "predicate-false", "predicate-true"})

IGNORE_DIRS = {".git", "__pycache__", ".venv", "node_modules"}
PER_RUN_TIMEOUT = 120  # seconds per child self-test run; a hung run is cannot-evaluate, never a hang
TOTAL_TIMEOUT = 1800   # seconds backstop for the whole sweep; exceeding it is cannot-evaluate
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_now = time.monotonic            # module-level indirection so the self-test can inject a deadline clock
_mkdtemp = tempfile.mkdtemp      # module-level indirection so the self-test can inject a setup failure


class _DeadlineExceeded(Exception):
    """The total-runtime backstop elapsed mid-sweep. Mapped to cannot-evaluate (exit 2)."""


class _NeuterError(Exception):
    """A marker cannot be applied: it is mis-declared, does not sit above a neuterable statement, or names
    a neuter the closed vocabulary cannot express. Mapped to cannot-evaluate (exit 2)."""


class _ReportError(Exception):
    """A child outcome report is missing, malformed, wrong-suite, or duplicate-keyed. Mapped to
    cannot-evaluate (exit 2): a run that did not deliver its structured verdict evidence is no verdict."""


# --------------------------------------------------------------------------- annotation scanning (AST)

class _Marker:
    """One `# audit-guard:` marker, resolved to the source line and the neuter op it declares."""

    def __init__(self, ids, op, frm, to, marker_line, stmt_line):
        self.ids = ids              # the claimed check id(s)
        self.op = op                # block-to-pass / predicate-false / predicate-true / return-swap
        self.frm = frm              # return-swap FROM, else None
        self.to = to                # return-swap TO, else None
        self.marker_line = marker_line  # 1-based line of the comment
        self.stmt_line = stmt_line      # 1-based line of the statement it anchors to


def _parse_op(op_raw):
    """Return (op, frm, to) for a recognized neuter op, or raise _NeuterError. The vocabulary is closed;
    an unrecognized op fails closed rather than guessing (DESIGN 3.3)."""
    if op_raw in _SIMPLE_OPS:
        return op_raw, None, None
    match = _RETURN_SWAP_RE.match(op_raw)
    if match:
        return "return-swap", match.group("frm"), match.group("to")
    raise _NeuterError("unrecognized neuter op {!r} (closed vocabulary: {}, return-swap=<FROM>-><TO>)"
                       .format(op_raw, ", ".join(sorted(_SIMPLE_OPS))))


def _statement_start_lines(tree):
    """Map every statement node's start line to that node, keeping the OUTERMOST statement when several
    share a start line (so a marker anchors to the compound statement it precedes, not a nested child).
    ast.walk yields parents before children, so a first-wins insert keeps the outermost."""
    starts = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and node.lineno not in starts:
            starts[node.lineno] = node
    return starts


def scan_markers(source):
    """Scan `source` for `# audit-guard:` markers and resolve each to the statement immediately below it.
    Returns (markers, errors, attempted_ids): a mis-declared op or a marker not sitting directly above a
    statement is an error string (cannot-evaluate), never silently dropped. `attempted_ids` is every id
    named on a well-formed id list (even where the op or the anchor later fails), so the completeness
    reconcile does not ALSO report a fixture whose author DID try to mark it as merely unmarked (that would
    turn a single mis-declared marker into both a cannot-evaluate and a spurious finding)."""
    markers, errors, attempted_ids = [], [], set()
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return [], ["source does not parse: {}".format(exc)], attempted_ids
    starts = _statement_start_lines(tree)
    for index, line in enumerate(source.splitlines(), start=1):
        match = _MARKER_RE.match(line)
        if not match:
            continue
        raw_ids = [part.strip() for part in match.group("ids").split(",")]
        ids = [cid for cid in raw_ids if cid]
        if not ids or len(ids) != len(raw_ids):
            errors.append("audit-guard marker at line {}: malformed id list {!r}"
                          .format(index, match.group("ids")))
            continue
        attempted_ids.update(ids)
        try:
            op, frm, to = _parse_op(match.group("op"))
        except _NeuterError as exc:
            errors.append("audit-guard marker at line {}: {}".format(index, exc))
            continue
        stmt_line = index + 1
        if stmt_line not in starts:
            errors.append("audit-guard marker at line {} does not sit directly above a statement "
                          "(nothing starts on line {})".format(index, stmt_line))
            continue
        markers.append(_Marker(ids, op, frm, to, index, stmt_line))
    return markers, errors, attempted_ids


# --------------------------------------------------------------------------- negative-fixture classifier

def _is_name(node, names):
    return isinstance(node, ast.Name) and node.id in names


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def _is_zero(node):
    return isinstance(node, ast.Constant) and node.value == 0 and not isinstance(node.value, bool)


def _is_literal(node):
    return isinstance(node, (ast.Constant, ast.List, ast.Tuple, ast.Dict, ast.Set))


def _classify_compare(node):
    if len(node.ops) != 1:
        return "ambiguous"
    op = node.ops[0]
    left, right = node.left, node.comparators[0]
    if isinstance(op, ast.Eq):
        if _is_name(left, _NEGATIVE_STATUS) or _is_name(right, _NEGATIVE_STATUS):
            return "negative"
        if _is_name(left, _POSITIVE_STATUS) or _is_name(right, _POSITIVE_STATUS):
            return "positive"
        if _is_zero(left) or _is_zero(right):
            return "positive"                       # == 0: a clean exit / control
        if _is_literal(left) or _is_literal(right):
            return "positive"                       # equality to an expected value: a control
        return "ambiguous"
    if isinstance(op, ast.NotEq):
        if _is_name(left, _POSITIVE_STATUS) or _is_name(right, _POSITIVE_STATUS):
            return "negative"                       # != VALID: a non-clean verdict
        if _is_zero(left) or _is_zero(right):
            return "negative"                       # != 0: a non-zero exit
        return "ambiguous"
    if isinstance(op, ast.Is):
        if _is_none(right):
            return "positive"                       # is None: no error/finding
        return "ambiguous"
    if isinstance(op, ast.IsNot):
        if _is_none(right):
            return "negative"                       # is not None: a finding/error present
        return "ambiguous"
    return "ambiguous"


def classify_cond(node):
    """Classify a `check()` cond AST as 'negative' (asserts a non-clean verdict), 'positive' (asserts a
    clean verdict / a control), or 'ambiguous' (the classifier cannot map it; fail closed to
    cannot-evaluate unless the author tags it). Closed and conservative on purpose: this is the gate's
    input-soundness surface (DESIGN 2.5)."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = classify_cond(node.operand)
        if inner == "negative":
            return "positive"
        if inner == "positive":
            return "negative"
        if isinstance(node.operand, (ast.Call, ast.Name, ast.Attribute)):
            return "positive"                       # not findings / not guard(...): asserts absence
        return "ambiguous"
    if isinstance(node, ast.BoolOp):
        kinds = {classify_cond(value) for value in node.values}
        if "ambiguous" in kinds:
            return "ambiguous"
        if kinds == {"negative"}:
            return "negative"
        if kinds == {"positive"}:
            return "positive"
        return "ambiguous"                          # a mixed positive/negative combination
    if isinstance(node, ast.Compare):
        return _classify_compare(node)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "bool":
            return "negative"                       # bool(<guard>(...)): truthiness of a finding
        return "ambiguous"
    return "ambiguous"


class _CheckVisitor(ast.NodeVisitor):
    """Collect every DIRECT `check(<str-literal>, <cond>)` call site: (id, cond node, line). A non-literal
    id is left to the caller as an unclassifiable/ambiguous site."""

    def __init__(self):
        self.found = []          # (id, cond_node, line)
        self.non_literal = []    # line numbers of check() sites whose id is not a string literal

    def visit_Call(self, node):
        self.generic_visit(node)
        if not (isinstance(node.func, ast.Name) and node.func.id == "check"):
            return
        arg = node.args[0] if node.args else None
        cond = node.args[1] if len(node.args) > 1 else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            self.found.append((arg.value, cond, node.lineno))
        else:
            self.non_literal.append(node.lineno)


def classify_checks(source):
    """Classify every `check(<literal>, <cond>)` site. Returns (kinds, errors): kinds maps id ->
    'negative'/'positive'/'ambiguous' (after applying any `# audit-fixture:` tag on the site's line), and
    errors carries a duplicate id or a non-literal id site (each cannot-evaluate). Honours the author tag
    only where the classifier could not decide, so a tag can resolve ambiguity but never contradicts a
    definite classification silently."""
    kinds, errors = {}, []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return {}, ["source does not parse: {}".format(exc)]
    lines = source.splitlines()
    tags = {}
    for index, line in enumerate(lines, start=1):
        match = _FIXTURE_TAG_RE.search(line)
        if match:
            tags[index] = match.group("kind")
    visitor = _CheckVisitor()
    visitor.visit(tree)
    for line in visitor.non_literal:
        errors.append("check() at line {} has a non-literal id; cannot classify (fail closed)".format(line))
    for cid, cond, line in visitor.found:
        if cid in kinds:
            errors.append("duplicate check id {!r} at line {}".format(cid, line))
            continue
        kind = "ambiguous" if cond is None else classify_cond(cond)
        if kind == "ambiguous" and line in tags:
            kind = tags[line]
        kinds[cid] = kind
    return kinds, errors


# --------------------------------------------------------------------------- AST neuter

def _apply_neuter(tree, marker):
    """Mutate `tree` in place: apply `marker`'s neuter to the statement starting at marker.stmt_line.
    Raises _NeuterError when the marked statement is not of a shape the op can neuter (fail closed, never
    a guessed neuter: DESIGN 3.3)."""
    starts = _statement_start_lines(tree)
    node = starts.get(marker.stmt_line)
    if node is None:
        raise _NeuterError("no statement starts at line {}".format(marker.stmt_line))
    if marker.op == "block-to-pass":
        if not isinstance(node, (ast.If, ast.For, ast.While, ast.With)) or not getattr(node, "body", None):
            raise _NeuterError("block-to-pass at line {} does not mark a compound statement with a body"
                               .format(marker.stmt_line))
        node.body = [ast.Pass()]
        return
    if marker.op in ("predicate-false", "predicate-true"):
        if not isinstance(node, (ast.If, ast.While)):
            raise _NeuterError("{} at line {} does not mark an if/while test".format(marker.op,
                                                                                     marker.stmt_line))
        node.test = ast.Constant(marker.op == "predicate-true")
        return
    if marker.op == "return-swap":
        if not isinstance(node, ast.Return) or node.value is None:
            raise _NeuterError("return-swap at line {} does not mark a return of a value"
                               .format(marker.stmt_line))
        swapped = _swap_names(node.value, marker.frm, marker.to)
        if swapped == 0:
            raise _NeuterError("return-swap at line {} found no reference to {!r} to swap"
                               .format(marker.stmt_line, marker.frm))
        return
    raise _NeuterError("unrecognized neuter op {!r}".format(marker.op))


def _swap_names(root, frm, to):
    """Replace every Name(id==frm) under `root` with Name(id==to), returning the count replaced. Rewrites
    the Name in place (id assignment) so parent references stay valid."""
    count = 0
    for node in ast.walk(root):
        if isinstance(node, ast.Name) and node.id == frm:
            node.id = to
            count += 1
    return count


def neutered_source(source, marker):
    """Parse `source`, apply `marker`'s neuter, and return the re-`unparse`d module source. Re-parses the
    result as a sanity gate (a failed round-trip is _NeuterError). Raises _NeuterError on any mis-declared
    marker or un-applicable neuter."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise _NeuterError("source does not parse: {}".format(exc))
    _apply_neuter(tree, marker)
    ast.fix_missing_locations(tree)
    try:
        out = ast.unparse(tree)
    except Exception as exc:  # noqa: BLE001  an unparse failure is a mis-neuter, fail closed
        raise _NeuterError("ast.unparse failed after neuter at line {}: {}".format(marker.stmt_line, exc))
    try:
        ast.parse(out)
    except (SyntaxError, ValueError) as exc:
        raise _NeuterError("neutered module does not re-parse at line {}: {}".format(marker.stmt_line, exc))
    return out


def unparsed_source(source):
    """Return the module `ast.unparse`d with NO neuter: the baseline the neutered runs are diffed against,
    so an unparse round-trip is never misattributed as a flip. Raises _NeuterError on a round-trip
    failure."""
    try:
        tree = ast.parse(source)
        out = ast.unparse(tree)
        ast.parse(out)
    except (SyntaxError, ValueError) as exc:
        raise _NeuterError("pristine module does not round-trip through ast.unparse: {}".format(exc))
    return out


# --------------------------------------------------------------------------- isolation + subprocess run

def _sanitized_env(overrides=None):
    """A minimal env for a child self-test: PYTHONDONTWRITEBYTECODE=1 plus a small allowlist. Ambient
    GIT_*, PYTHON*, PYTHONPATH and everything else are stripped so an inherited env cannot perturb the
    verdict (10-QUALI-test-hermeticity). HOME/TMPDIR are supplied per-call through `overrides`."""
    env = {"PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "SYSTEMROOT", "PATHEXT"):
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    if "PATH" not in env:
        env["PATH"] = os.defpath
    if overrides:
        env.update(overrides)
    return env


def _copy_ignore(dir_path, names):
    """copytree ignore callable: drop ignore-dirs and any symlink/special node so the sandbox carries only
    real files and directories."""
    ignored = set()
    for name in names:
        if name in IGNORE_DIRS:
            ignored.add(name)
            continue
        try:
            st = os.lstat(os.path.join(dir_path, name))
        except OSError:
            ignored.add(name)
            continue
        if stat.S_ISLNK(st.st_mode) or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode)):
            ignored.add(name)
    return ignored


def _raise_oserror(exc):
    raise exc


def _tree_manifest(root):
    """Map every path under root (excluding IGNORE_DIRS, following no symlink) to a metadata tuple, so the
    REAL tree can be proven unchanged before and after the sweep (the check_gensrc_failclose shape)."""
    manifest = {}
    root = str(root)
    for dirpath, dirnames, filenames in os.walk(root, onerror=_raise_oserror):
        kept = []
        for name in dirnames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                st = os.lstat(full)
                manifest[os.path.relpath(full, root)] = ("symlink", stat.S_IMODE(st.st_mode),
                                                         os.readlink(full))
                continue
            if name in IGNORE_DIRS:
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if name in IGNORE_DIRS:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            st = os.lstat(full)
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISLNK(st.st_mode):
                manifest[rel] = ("symlink", mode, os.readlink(full))
            elif stat.S_ISREG(st.st_mode):
                with open(full, "rb") as handle:
                    manifest[rel] = ("file", mode, hashlib.sha256(handle.read()).hexdigest())
            else:
                manifest[rel] = ("special", mode, stat.S_IFMT(st.st_mode))
    return manifest


def _remaining_timeout(deadline):
    """Seconds left until the total-runtime deadline, clamped to PER_RUN_TIMEOUT. Raises _DeadlineExceeded
    once the backstop has elapsed, so no child launches with a non-positive timeout and an overrun is
    cannot-evaluate, never a fail-open pass."""
    remaining = deadline - _now()
    if remaining <= 0:
        raise _DeadlineExceeded()
    return min(PER_RUN_TIMEOUT, remaining)


def _read_report(report_path, suite_id):
    """Read and strictly validate a child outcome report; raise _ReportError on anything malformed. A
    duplicate JSON member, a wrong suite, a bad shape, or a bad value is no verdict, never a pass."""

    def reject_dup(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate JSON member {!r}".format(key))
            obj[key] = value
        return obj

    try:
        st = os.lstat(report_path)
    except OSError:
        raise _ReportError("no outcome report at {} (a run that did not deliver its report is no verdict)"
                           .format(report_path))
    if not stat.S_ISREG(st.st_mode):
        raise _ReportError("outcome report {} is not a regular file".format(report_path))
    try:
        with open(report_path, "r", encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=reject_dup)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise _ReportError("outcome report {} unreadable or malformed: {}".format(report_path, exc))
    if not isinstance(data, dict) or set(data) != {"format_version", "suite", "checks"}:
        raise _ReportError("outcome report {}: keys must be exactly format_version, suite, checks"
                           .format(report_path))
    if type(data["format_version"]) is not int or data["format_version"] != 1:
        raise _ReportError("outcome report {}: format_version must be the integer 1".format(report_path))
    if data["suite"] != suite_id:
        raise _ReportError("outcome report {}: suite {!r} is not the requested suite {!r}"
                           .format(report_path, data["suite"], suite_id))
    checks = data["checks"]
    if not isinstance(checks, dict):
        raise _ReportError("outcome report {}: checks must be an object".format(report_path))
    for cid, value in checks.items():
        if not isinstance(cid, str) or not cid or value not in ("passed", "failed"):
            raise _ReportError("outcome report {}: check {!r}={!r} is malformed".format(
                report_path, cid, value))
    return checks


def run_module(module_src, module_basename, template_tools, suite_id, deadline, cleanup_errors):
    """Copy the template tools/ tree to a fresh disposable holder, write `module_src` as the module under
    test, run `python3 -I -B tools/<module> --outcome-report <abs>` as a subprocess with a sanitized,
    per-call HOME/TMPDIR env, and return the validated outcome-report checks map. Raises _ReportError,
    _DeadlineExceeded, subprocess.TimeoutExpired, or OSError; the caller maps all to cannot-evaluate."""
    holder = Path(_mkdtemp(prefix="aiqt-disc-audit-"))
    try:
        tools = holder / "tools"
        shutil.copytree(template_tools, tools, symlinks=False, ignore=_copy_ignore)
        target = tools / module_basename
        # O_NOFOLLOW + O_TRUNC: a copied module is a plain regular file; refuse to follow a symlink.
        fd = os.open(target, os.O_WRONLY | os.O_TRUNC | _O_NOFOLLOW)
        try:
            os.write(fd, module_src.encode("utf-8"))
        finally:
            os.close(fd)
        home = holder / "home"
        tmp = holder / "tmp"
        home.mkdir()
        tmp.mkdir()
        report_path = holder / "outcome-report.json"
        timeout = _remaining_timeout(deadline)
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(tools / module_basename),
             "--outcome-report", str(report_path)],
            cwd=str(holder), env=_sanitized_env({"HOME": str(home), "TMPDIR": str(tmp)}),
            capture_output=True, timeout=timeout, shell=False)
        # The child self-test returns 0 (clean) or 1 (a check failed); a neuter makes a check fail, so 1 is
        # expected and fine. Any other code (2, a crash, a signal) means the module could not run its
        # suite, so its report cannot be trusted: cannot-evaluate.
        if proc.returncode not in (0, 1):
            raise _ReportError("child self-test exited {} (a harness error is no verdict); stderr: {}"
                               .format(proc.returncode,
                                       proc.stderr.decode("utf-8", errors="replace")[-400:]))
        return _read_report(str(report_path), suite_id)
    finally:
        try:
            shutil.rmtree(holder)
        except OSError as exc:
            cleanup_errors.append("holder cleanup failed for {} ({})".format(holder, exc))


# --------------------------------------------------------------------------- the audit

def _baseline_checks(source, module_basename, template_tools, suite_id, cache, deadline, cleanup_errors):
    """The UNPARSED-pristine baseline outcome for a module, cached per module. Every check must be
    'passed'; a non-green baseline is cannot-evaluate (a flip would be unattributable). Raises the narrow
    isolation/neuter/report errors; the caller maps them to cannot-evaluate."""
    if module_basename in cache:
        return cache[module_basename]
    baseline_src = unparsed_source(source)
    checks = run_module(baseline_src, module_basename, template_tools, suite_id, deadline, cleanup_errors)
    failing = sorted(cid for cid, value in checks.items() if value != "passed")
    if failing:
        raise _ReportError("baseline (unparsed pristine) is not all-green ({} failing, e.g. {}); a flip "
                           "would be unattributable".format(len(failing), failing[:3]))
    cache[module_basename] = checks
    return checks


def _audit_markers(source, module_basename, template_tools, suite_id, markers, kinds, baseline_cache,
                   deadline, violations, cannot, cleanup_errors):
    """Run the deliberate flip for each marker: neuter its guard, run the suite, and assert every claimed
    id flips from baseline 'passed' to 'failed'/absent. A claimed id still 'passed' is a masked-fixture
    finding; a marker naming an unknown or non-negative id is a finding; a mis-declared marker or a neuter
    the vocabulary cannot apply is cannot-evaluate. `kinds` may be None in --only-marked proof mode, in
    which case the negative-classification cross-check is skipped for ids absent from a (not computed) map
    but a known positive id is still refused when `kinds` is provided."""
    try:
        baseline = _baseline_checks(source, module_basename, template_tools, suite_id, baseline_cache,
                                    deadline, cleanup_errors)
    except _NeuterError as exc:
        cannot.append("{}: {}".format(module_basename, exc))
        return
    except (_ReportError, subprocess.TimeoutExpired, OSError) as exc:
        cannot.append("{}: baseline run failed ({})".format(module_basename, exc))
        return

    for marker in markers:
        # Validate the claimed ids against the classification where we have it.
        for cid in marker.ids:
            if kinds is not None:
                if cid not in kinds:
                    violations.append("{}: audit-guard at line {} names unknown check id {!r}"
                                      .format(module_basename, marker.marker_line, cid))
                elif kinds[cid] == "positive":
                    violations.append("{}: audit-guard at line {} names positive (non-negative) check id "
                                      "{!r}".format(module_basename, marker.marker_line, cid))
            if baseline.get(cid) not in ("passed", None):
                # A claimed id that FAILS in the green baseline cannot exist (baseline is all-green); a
                # claimed id ABSENT from the baseline means the marker names an id no reached check emits.
                pass
        try:
            neutered = neutered_source(source, marker)
        except _NeuterError as exc:
            cannot.append("{}: audit-guard at line {}: {}".format(module_basename, marker.marker_line, exc))
            continue
        try:
            checks = run_module(neutered, module_basename, template_tools, suite_id, deadline,
                                cleanup_errors)
        except (_ReportError, subprocess.TimeoutExpired, OSError) as exc:
            cannot.append("{}: neutered run for audit-guard at line {} failed ({})"
                          .format(module_basename, marker.marker_line, exc))
            continue
        for cid in marker.ids:
            base_val = baseline.get(cid)
            if base_val != "passed":
                cannot.append("{}: audit-guard at line {} claims id {!r} which is not a passing baseline "
                              "check (baseline: {!r}); cannot prove a flip".format(
                                  module_basename, marker.marker_line, cid, base_val))
                continue
            neut_val = checks.get(cid)          # absent = the neuter made it unreachable = not-passing
            if neut_val == "passed":
                violations.append("{}: MASKED fixture {!r}: it did not red under the {} revert of the guard "
                                  "at line {} (its non-clean verdict rests on something other than that "
                                  "guard)".format(module_basename, cid, marker.op, marker.stmt_line))


def audit_suite(real_root, suite_id, module_rel, only_marked=None):
    """Audit one enrolled suite. `only_marked` (a set of check ids) runs the dynamic flip for JUST those
    markers and skips the corpus-wide completeness reconcile (the proof mode); None runs the full audit
    (static reconcile + every marker). Returns the gate exit code (0/1/2). The real tree is never
    written."""
    real_root = Path(real_root).resolve()
    module_path = real_root / module_rel
    module_basename = os.path.basename(module_rel)
    try:
        source = module_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print("cannot-evaluate: module {} unreadable ({}); fail-closed".format(module_rel, exc),
              file=sys.stderr)
        return 2

    markers, marker_errors, attempted_ids = scan_markers(source)
    kinds, class_errors = classify_checks(source)

    violations, cannot, cleanup_errors = [], list(marker_errors), []
    if only_marked is None:
        cannot.extend(class_errors)
    unexpected = None
    work = None
    deadline = _now() + TOTAL_TIMEOUT

    # In proof mode, keep only the markers whose id set intersects the requested ids, and refuse a
    # requested id that no marker claims (a mis-pointed proof request is cannot-evaluate, never a vacuous
    # pass).
    if only_marked is not None:
        claimed = set()
        for marker in markers:
            claimed.update(marker.ids)
        missing = sorted(only_marked - claimed)
        if missing:
            cannot.append("{}: --only-marked names id(s) no audit-guard marker claims: {}"
                          .format(module_rel, missing))
        markers = [m for m in markers if set(m.ids) & only_marked]
        kinds_for_cross = kinds if not class_errors else None
    else:
        kinds_for_cross = kinds

    try:
        before = _tree_manifest(real_root / "tools")
    except OSError as exc:
        print("cannot-evaluate: cannot snapshot the real tools tree ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2

    try:
        try:
            work = Path(_mkdtemp(prefix="aiqt-disc-audit-template-"))
            template_tools = work / "tools"
            shutil.copytree(real_root / "tools", template_tools, symlinks=False, ignore=_copy_ignore)
        except (OSError, shutil.Error) as exc:
            cannot.append("template sandbox setup failed ({})".format(exc))
        else:
            # Full mode: the completeness reconcile (every negative fixture has a marker; no marker names an
            # unknown/positive id) BEFORE the dynamic flip, mirroring check_selftest_execution's pre-launch
            # static reconcile.
            if only_marked is None:
                _static_reconcile(module_rel, markers, attempted_ids, kinds, class_errors, violations,
                                  cannot)
            baseline_cache = {}
            try:
                _audit_markers(source, module_basename, template_tools, suite_id, markers, kinds_for_cross,
                               baseline_cache, deadline, violations, cannot, cleanup_errors)
                if _now() > deadline:
                    raise _DeadlineExceeded()
            except _DeadlineExceeded:
                cannot.append("total-runtime backstop of {}s exceeded; the audit did not finish "
                              "(fail-closed)".format(TOTAL_TIMEOUT))
    except Exception as exc:  # noqa: BLE001  an unexpected error is fail-closed, never a silent pass
        unexpected = exc
    finally:
        cleanup_error = None
        if work is not None:
            try:
                shutil.rmtree(work)
            except OSError as exc:
                cleanup_error = exc
        try:
            after = _tree_manifest(real_root / "tools")
        except OSError as exc:
            after = None
            integrity_error = exc
        else:
            integrity_error = None

    if unexpected is not None:
        print("cannot-evaluate: unexpected error during the audit ({}); fail-closed".format(unexpected),
              file=sys.stderr)
    if integrity_error is not None or after is None:
        print("cannot-evaluate: cannot re-read the real tree after the audit ({}); fail-closed"
              .format(integrity_error), file=sys.stderr)
        return 2
    if after != before:
        print("cannot-evaluate: the real tree CHANGED during the audit; fail-closed (this must never "
              "happen: the gate neuters only disposable copies)", file=sys.stderr)
        return 2
    if cleanup_error is not None:
        print("cannot-evaluate: template work-dir cleanup failed ({}); fail-closed".format(cleanup_error),
              file=sys.stderr)
        return 2
    if cleanup_errors:
        print("cannot-evaluate: {} disposable holder cleanup(s) failed; fail-closed"
              .format(len(cleanup_errors)), file=sys.stderr)
        for line in cleanup_errors:
            print("  " + line, file=sys.stderr)
        return 2

    scope = "marked fixtures {}".format(sorted(only_marked)) if only_marked is not None else "full suite"
    print("audited suite {!r} ({}), {} marker(s)".format(suite_id, scope, len(markers)))
    if violations:
        print("FAIL: {} discrimination finding(s)".format(len(violations)))
        for line in violations:
            print("  " + line)
        for line in cannot:
            print("  (also could not evaluate) " + line)
        return 1
    if unexpected is not None or cannot:
        print("CANNOT-EVALUATE: {} condition(s) could not be soundly evaluated".format(len(cannot)))
        for line in cannot:
            print("  " + line)
        return 2
    print("PASS: every claimed negative fixture reds under its annotated guard revert")
    return 0


def _static_reconcile(module_rel, markers, attempted_ids, kinds, class_errors, violations, cannot):
    """The completeness backstop (full mode): every NEGATIVE fixture has at least one marker; every marker
    id resolves to a real negative fixture. A classifier error (a duplicate or non-literal id) is
    cannot-evaluate. Mirrors check_selftest_execution's static source-to-manifest reconcile. An id named on
    a mis-declared marker (in `attempted_ids`) is already surfaced as a cannot-evaluate, so it is not ALSO
    reported here as unmarked."""
    if class_errors:
        cannot.extend("{}: {}".format(module_rel, err) for err in class_errors)
    ambiguous = sorted(cid for cid, kind in kinds.items() if kind == "ambiguous")
    for cid in ambiguous:
        cannot.append("{}: negative-fixture classifier cannot categorize {!r}; tag it "
                      "'# audit-fixture: negative|positive' (fail closed)".format(module_rel, cid))
    real_marked = set()
    for marker in markers:
        real_marked.update(marker.ids)
    # A fixture is "covered" for the unmarked check if a well-formed OR a mis-declared marker names it (a
    # mis-declared marker is already a cannot-evaluate; do not also count its fixture as unmarked).
    covered = real_marked | set(attempted_ids)
    negatives = {cid for cid, kind in kinds.items() if kind == "negative"}
    for cid in sorted(negatives - covered):
        violations.append("{}: negative fixture {!r} has no discrimination guard annotation "
                          "(add a '# audit-guard: {} neuter=<op>' marker at its guard)"
                          .format(module_rel, cid, cid))
    # The unknown/non-negative check runs only over WELL-FORMED markers: a mis-declared marker is already a
    # cannot-evaluate and must not additionally raise a finding (which would flip the exit to 1).
    for cid in sorted(real_marked):
        if cid not in kinds:
            violations.append("{}: audit-guard marker names unknown check id {!r}".format(module_rel, cid))
        elif kinds[cid] == "positive":
            violations.append("{}: audit-guard marker names positive (non-negative) check id {!r}"
                              .format(module_rel, cid))


def sweep(real_root):
    """Full audit of every enrolled suite in the roster. Returns the worst exit code (2 beats 1 beats 0)."""
    worst = 0
    for row in ROSTER:
        code = audit_suite(real_root, row["suite"], row["module"], only_marked=None)
        if code == 2 or (code == 1 and worst == 0):
            worst = code
    return worst


def _repo_root():
    """This gate's own repo root, anchored to its file, never the cwd (an exported copy must refuse to
    validate whatever tree the cwd happens to sit in)."""
    root = Path(__file__).resolve().parent.parent
    return root


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv == ["--self-test"]:
        return self_test()
    root = _repo_root()
    if not (root / ".git").exists():
        print("cannot-evaluate: cannot confirm the gate's own repo root (no .git at {})".format(root),
              file=sys.stderr)
        return 2
    if not argv:
        return sweep(root)
    # --suite <id> [--only-marked <cid>[,<cid>...]]
    if argv[0] == "--suite" and len(argv) >= 2 and argv[1]:
        suite_id = argv[1]
        row = next((r for r in ROSTER if r["suite"] == suite_id), None)
        if row is None:
            print("cannot-evaluate: no suite {!r} enrolled in the roster".format(suite_id), file=sys.stderr)
            return 2
        rest = argv[2:]
        only_marked = None
        if rest:
            if rest[0] == "--only-marked" and len(rest) == 2 and rest[1]:
                only_marked = {cid.strip() for cid in rest[1].split(",") if cid.strip()}
                if not only_marked:
                    print("usage: --only-marked <cid>[,<cid>...]; fail-closed", file=sys.stderr)
                    return 2
            else:
                print("usage: --suite <id> [--only-marked <cid>[,<cid>...]]; fail-closed", file=sys.stderr)
                return 2
        return audit_suite(root, suite_id, row["module"], only_marked=only_marked)
    print("usage: check_discrimination_audit.py [--suite <id> [--only-marked <cid>[,...]]] | --self-test",
          file=sys.stderr)
    return 2


# ============================================================================ self-test
# Synthetic mini-suites assembled in a tempdir, each a tiny module with its own check()/self_test()/
# _audit_outcome seam and a couple of guards, driven by the REAL audit_suite() over a REAL subprocess (no
# mock of the mechanism under test). Every fixture is synthetic; no real personal data
# (SECP-synthetic-fixture-data). The legs assert (DESIGN 4.2):
#   (a) a CONFORMANT suite (its negative fixture reds under its annotated guard) passes (exit 0);
#   (b) a MASKED fixture (its INVALID rests on an incidental guard that fires first) is caught (exit 1) -
#       the whole point of the gate;
#   (c) an UNMARKED negative fixture is caught by the static reconcile (exit 1);
#   (d) a marker naming a POSITIVE id is caught (exit 1);
#   (e) an AMBIGUOUS cond is cannot-evaluate (exit 2), resolvable by an author tag (then 0);
#   (f) a mis-declared marker / un-applicable neuter is cannot-evaluate (exit 2);
#   (g) a non-green baseline is cannot-evaluate (exit 2);
#   (h) a per-run timeout overrun is cannot-evaluate (exit 2), never a fail-open 0;
#   (i) the synthetic real tree is byte-and-mode identical after a normal run AND after a run whose neuter
#       raises;
#   (j) predicate-false and return-swap neuters each red a real conformant fixture (exit 0);
#   (k) --only-marked runs just the named markers (an unmarked sibling negative does not fail it).

# A synthetic in-scope module template. It mirrors the OPF check(name, cond) idiom and the _audit_outcome
# seam. Two production guards (a primary and an incidental) and a small self_test whose fixtures target
# them. `{extra_import}` lets a leg point the seam import at the copied tools/ dir.
_SYNTH_HEADER = '''import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _audit_outcome import OutcomeReport

INVALID = "INVALID"
VALID = "VALID"
CANNOT_EVALUATE = "CANNOT_EVALUATE"

'''


def _synth_module(body):
    """Assemble a synthetic in-scope module from its self_test body plus the shared header/footer that
    wire the _audit_outcome seam and the 0/1 exit."""
    return _SYNTH_HEADER + body


def self_test():  # noqa: C901  a long but flat sequence of independent legs
    import contextlib
    import io

    failures = []

    def expect(label, got, want):
        if got != want:
            failures.append("{}: got {!r}, want {!r}".format(label, got, want))

    try:
        base = Path(tempfile.mkdtemp(prefix="aiqt-disc-audit-st-"))
    except OSError as exc:
        print("cannot-evaluate: self-test temp storage unavailable ({})".format(exc), file=sys.stderr)
        return 2
    counter = [0]

    def build(module_src):
        """A synthetic repo root: tools/ carrying the module under test, a copy of _audit_outcome.py, and a
        .git marker so the gate's repo-root anchor resolves."""
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        (root / "tools").mkdir(parents=True)
        (root / ".git").mkdir()
        (root / "tools" / "synth_suite.py").write_text(module_src, encoding="utf-8")
        seam = Path(__file__).resolve().parent / "_audit_outcome.py"
        shutil.copy2(seam, root / "tools" / "_audit_outcome.py")
        return root

    def run(root, suite="synth", module="tools/synth_suite.py", only_marked=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = audit_suite(root, suite, module, only_marked=only_marked)
        return code, out.getvalue(), err.getvalue()

    # A CONFORMANT suite: the negative fixture 'neg-primary' asserts CANNOT_EVALUATE and its verdict rests
    # SOLELY on the marked primary guard, so block-to-pass reds it. A positive control 'pos-ok'.
    conformant = _synth_module('''def guard(x):
    # audit-guard: neg-primary neuter=block-to-pass
    if x == "bad":
        return CANNOT_EVALUATE
    return VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    check("neg-primary", guard("bad") == CANNOT_EVALUATE)
    check("pos-ok", guard("fine") == VALID)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')

    try:
        # (a) conformant -> full audit passes
        code, out, err = run(build(conformant))
        expect("st/conformant-passes", code, 0)
        expect("st/conformant-pass-line", "PASS: every claimed negative fixture reds" in out, True)

        # (b) MASKED: 'neg-masked' asserts CANNOT_EVALUATE, but its verdict rests on the INCIDENTAL guard
        # (missing schema) that fires FIRST; the marker points at the primary guard, whose revert leaves it
        # CANNOT_EVALUATE (still red-less) -> caught as a masked finding.
        masked = _synth_module('''def guard(x, schema):
    if schema != 1:
        return CANNOT_EVALUATE          # incidental guard fires first
    # audit-guard: neg-masked neuter=block-to-pass
    if x == "bad":
        return CANNOT_EVALUATE          # the intended primary guard
    return VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    # the fixture forgets to set schema=1, so its CANNOT_EVALUATE rests on the incidental guard, not the
    # primary one the marker claims -> masked.
    check("neg-masked", guard("bad", 0) == CANNOT_EVALUATE)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        code, out, _err = run(build(masked))
        expect("st/masked-caught", code, 1)
        expect("st/masked-names-fixture", "MASKED fixture 'neg-masked'" in out, True)

        # (c) an UNMARKED negative fixture -> static reconcile finding (exit 1)
        unmarked = _synth_module('''def guard(x):
    if x == "bad":
        return CANNOT_EVALUATE
    return VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    check("neg-unmarked", guard("bad") == CANNOT_EVALUATE)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        code, out, _err = run(build(unmarked))
        expect("st/unmarked-caught", code, 1)
        expect("st/unmarked-named", "has no discrimination guard annotation" in out, True)

        # (d) a marker naming a POSITIVE id -> finding (exit 1)
        marks_positive = _synth_module('''def guard(x):
    # audit-guard: pos-control neuter=block-to-pass
    if x == "bad":
        return CANNOT_EVALUATE
    return VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    check("pos-control", guard("fine") == VALID)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        code, out, _err = run(build(marks_positive))
        expect("st/marker-positive-caught", code, 1)
        expect("st/marker-positive-named", "positive (non-negative) check id 'pos-control'" in out, True)

        # (e) an AMBIGUOUS cond -> cannot-evaluate (exit 2); tagging it resolves to a real audit (0)
        ambiguous = _synth_module('''def guard(x):
    # audit-guard: amb neuter=block-to-pass
    if x == "bad":
        return CANNOT_EVALUATE
    return VALID


def opaque(x):
    return guard(x) == CANNOT_EVALUATE


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    verdict = opaque("bad")
    check("amb", verdict)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        code, out, _err = run(build(ambiguous))
        expect("st/ambiguous-cannot-eval", code, 2)
        expect("st/ambiguous-named", "classifier cannot categorize 'amb'" in out, True)
        tagged = ambiguous.replace('check("amb", verdict)',
                                   'check("amb", verdict)  # audit-fixture: negative')
        code, _out, _err = run(build(tagged))
        expect("st/ambiguous-tag-resolves", code, 0)

        # (f) a mis-declared marker (an unknown neuter op) -> cannot-evaluate (exit 2)
        misdeclared = conformant.replace("neuter=block-to-pass", "neuter=make-it-green")
        code, out, _err = run(build(misdeclared))
        expect("st/misdeclared-op-cannot-eval", code, 2)
        expect("st/misdeclared-op-named", "unrecognized neuter op" in out, True)

        # (f2) an un-applicable neuter: predicate-false marking a non-if statement -> cannot-evaluate
        unapplicable = _synth_module('''def guard(x):
    # audit-guard: neg-primary neuter=predicate-false
    return CANNOT_EVALUATE if x == "bad" else VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    check("neg-primary", guard("bad") == CANNOT_EVALUATE)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        code, out, _err = run(build(unapplicable))
        expect("st/unapplicable-neuter-cannot-eval", code, 2)
        expect("st/unapplicable-neuter-named", "does not mark an if/while test" in out, True)

        # (g) a non-green baseline -> cannot-evaluate (exit 2): a fixture that fails in the pristine suite
        nongreen = _synth_module('''def guard(x):
    # audit-guard: neg-primary neuter=block-to-pass
    if x == "bad":
        return CANNOT_EVALUATE
    return VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    check("neg-primary", guard("bad") == CANNOT_EVALUATE)
    check("already-red", guard("bad") == VALID)   # a POSITIVE assertion that is FALSE -> baseline red
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        code, out, _err = run(build(nongreen))
        expect("st/nongreen-baseline-cannot-eval", code, 2)
        expect("st/nongreen-baseline-named", "not all-green" in out, True)

        # (h) a per-run timeout overrun -> cannot-evaluate (exit 2). The neuter (block-to-pass) removes an
        # early-return guard so the neutered run spins; a tiny PER_RUN_TIMEOUT makes the neutered run time
        # out while the baseline is fast. Restored in a finally.
        spin = _synth_module('''import time


def guard(x):
    # audit-guard: neg-spin neuter=block-to-pass
    if x == "bad":
        return CANNOT_EVALUATE
    time.sleep(10)
    return VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    check("neg-spin", guard("bad") == CANNOT_EVALUATE)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        global PER_RUN_TIMEOUT
        saved_timeout = PER_RUN_TIMEOUT
        PER_RUN_TIMEOUT = 2
        try:
            code, _out, _err = run(build(spin))
        finally:
            PER_RUN_TIMEOUT = saved_timeout
        expect("st/timeout-cannot-eval", code, 2)

        # (i) the synthetic real tree is byte-AND-mode identical after a normal run AND after a run whose
        # neuter RAISES. A neuter that raises: block-to-pass marking a bare expression statement.
        raising = _synth_module('''def guard(x):
    if x == "bad":
        return CANNOT_EVALUATE
    return VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    # audit-guard: neg-primary neuter=block-to-pass
    check("neg-primary", guard("bad") == CANNOT_EVALUATE)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        root = build(raising)
        before = _tree_manifest(root / "tools")
        code, _out, _err = run(root)
        after = _tree_manifest(root / "tools")
        expect("st/raising-neuter-cannot-eval", code, 2)   # block-to-pass on a call stmt is un-applicable
        expect("st/real-tree-unchanged-after-raise", before, after)
        root2 = build(conformant)
        before2 = _tree_manifest(root2 / "tools")
        run(root2)
        after2 = _tree_manifest(root2 / "tools")
        expect("st/real-tree-unchanged-normal", before2, after2)

        # (j) predicate-false and return-swap each red a real conformant fixture (exit 0)
        pred_false = _synth_module('''def guard(x):
    # audit-guard: neg-pred neuter=predicate-false
    if x == "bad":
        return CANNOT_EVALUATE
    return VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    check("neg-pred", guard("bad") == CANNOT_EVALUATE)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        code, _out, _err = run(build(pred_false))
        expect("st/predicate-false-reds", code, 0)

        ret_swap = _synth_module('''def guard(x):
    if x == "bad":
        # audit-guard: neg-ret neuter=return-swap=CANNOT_EVALUATE->VALID
        return CANNOT_EVALUATE
    return VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    check("neg-ret", guard("bad") == CANNOT_EVALUATE)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        code, _out, _err = run(build(ret_swap))
        expect("st/return-swap-reds", code, 0)

        # (k) --only-marked runs just the named markers: a suite with one MARKED discriminating negative and
        # one UNMARKED negative sibling passes in proof mode (the unmarked sibling is not enumerated), while
        # the SAME suite fails the full audit (the unmarked sibling is a finding).
        mixed = _synth_module('''def guard(x):
    # audit-guard: neg-marked neuter=block-to-pass
    if x == "bad":
        return CANNOT_EVALUATE
    return VALID


def other(x):
    if x == "bad":
        return CANNOT_EVALUATE
    return VALID


def self_test():
    _outcome = OutcomeReport("synth")
    failures = []

    def check(name, cond):
        if not cond:
            failures.append(name)
        _outcome.record(name, bool(cond))

    check("neg-marked", guard("bad") == CANNOT_EVALUATE)
    check("neg-unmarked-sibling", other("bad") == CANNOT_EVALUATE)
    _outcome.write()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(self_test())
''')
        code, _out, _err = run(build(mixed), only_marked={"neg-marked"})
        expect("st/only-marked-proof-passes", code, 0)
        code, _out, _err = run(build(mixed))
        expect("st/only-marked-full-fails", code, 1)
        # a --only-marked id that no marker claims -> cannot-evaluate
        code, _out, _err = run(build(mixed), only_marked={"no-such-id"})
        expect("st/only-marked-unknown-cannot-eval", code, 2)

    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: the discrimination-audit gate passes a conformant suite, catches a masked "
          "fixture (the D1MASK class), catches an unmarked negative fixture and a marker naming a positive "
          "id, treats an ambiguous cond as cannot-evaluate (resolvable by an author tag), refuses a "
          "mis-declared or un-applicable neuter and a non-green baseline, treats a per-run timeout as "
          "cannot-evaluate (never a fail-open pass), leaves the real tree byte-and-mode identical after a "
          "normal run and after a neuter that raises, reds a real fixture under predicate-false and "
          "return-swap neuters, and scopes --only-marked to the named markers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
