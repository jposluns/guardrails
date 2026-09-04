#!/usr/bin/env python3
"""Execution-set gate for registered self-test suites (chgchk/evgcmp): prove every registered check
RAN, not merely that the suite went green.

A present-but-unreached check() call (dead code after an early return, a misindented block, a loop that
no longer iterates) is indistinguishable from a passing one when judged by the suite's exit code alone.
This gate closes that with three layers. First, a STATIC pre-launch reconciliation resolves every
check() call site in the runner's SOURCE to literal ids (a string constant, or a for loop over literal
rows; anything else is a fail-closed error, and a duplicate source id is refused) and requires that set
to equal the manifest exactly, so a check that is both unreachable and unregistered, or a dead
duplicate-id source alias, is refused before any child runs. Second, the suite's check() choke point
records every check id actually reached, the child emits them as a structured JSON report, and this
gate reconciles that report, as an exact set, against the hand-authored expectation manifest
tools/selftest_checks.toml. Third, the child carries its own in-run self-guard. The manifest is
authored from SOURCE REVIEW and is NEVER regenerated from a runtime capture (a captured manifest would
drop an accidentally-unreachable check from both sides of the comparison and stay green, the exact
failure this gate exists to catch); there is deliberately no accept, update, or baseline command.

  check_selftest_execution.py --suite <id>   run the registered suite and reconcile its execution set
  check_selftest_execution.py --self-test    synthetic manifests and fake runners assert every leg fires

The child is launched [sys.executable, -I, -B, <runner>, --execution-report, <private abs path>] with
cwd at the repo root and a git-neutral environment (every ambient GIT_* variable dropped;
GIT_CONFIG_GLOBAL and GIT_CONFIG_SYSTEM pinned to os.devnull, GIT_CONFIG_NOSYSTEM=1;
core.excludesFile and core.attributesFile overridden to os.devnull through injected
GIT_CONFIG_COUNT/KEY/VALUE entries, neutralizing the global ignore and attributes surfaces the
pinned config files do not cover), so ambient git
configuration cannot steer the child's git subprocesses; its full stdout and stderr are forwarded
UNFILTERED to this gate's own streams (no
grep, no truncation), and its verdict is judged by its real return code plus the strict report
reconcile, never by its prose. A report that is missing, truncated, malformed, wrong-suite, non-regular,
or carrying a duplicate or wrong-typed entry is CANNOT-EVALUATE, never a pass, whatever the child's exit
code; completeness is never inferred from output volume or from the absence of a reported problem.

Exit convention: 0 the execution set matches exactly AND the child passed; 1 a real finding (a missing
or extra check id, or a set-complete child that itself reported assertion failures); 2 usage or
cannot-evaluate (an unreadable, malformed, or suite-missing expectation manifest; a runner that is
absent, non-regular, a symlink, or escapes the repo; runner source that does not parse or carries an
unresolvable, aliased (a non-call check reference), comprehension-bound, or duplicate check id; a
static source-to-manifest set mismatch; an unconfirmable repo
root; unavailable temp storage; a launch failure; a child return code outside {0, 1}; or an invalid
report).

DISCLOSED RESIDUAL: this gate proves INVOCATION IDENTITY only. It does not prove an invoked assertion is
discriminating (a constant-true check counts as executed), carries no mutation sensitivity, sees nothing
outside the instrumented check() helper (a direct FAILURES.append, or a suite not registered in the
manifest), and cannot distinguish a legitimate from an illegitimate coordinated deletion of a check and
its manifest row in one reviewed change; the expectation manifest is trusted hand-authored input whose
schema is machine-validated here but whose completeness rests on source review. The static layer proves
source-to-manifest SET EQUALITY, not reachability (a dead literal call still counts as a source site;
proving execution is the runtime layer's job), and resolves only DIRECT call sites of the bare name
check with string-literal or for-loop-literal ids: a non-call reference to that bare name (an alias
would carry a call site beyond the scan), an id Name bound inside a comprehension (whose scope the
lexical for-stack does not model), and a statically empty literal loop (whose check() site would
otherwise resolve to no id at all) are refused fail-closed, never silently resolved. An invocation
route that never names check as a bare Name in Load context (getattr, exec, an import alias, or a
Store, with, or except rebinding of the name) is invisible to this layer and is caught, if at all,
by the runtime set reconcile; a route that re-executes an already-registered id whose direct call
site is dead passes both set layers and is bounded only by the non-discrimination residual (a
constant-true check counts as executed). The remaining
lexical binding (the innermost enclosing for) does not model function boundaries; the runtime
reconcile still proves the executed set independently. Repo-root confirmation is a .git-existence
proxy anchored to this gate's own file, not a full git-identity check; and the static scan and the
launch read the runner's source at two moments, so a concurrent same-user writer between them is
outside this repo's sole-orchestrator threat model (the runtime layer still reconciles what
actually ran). The child runs un-timed
(parity with the roster's other selftest steps; the CI job timeout is the outer bound).
"""
import ast
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_selftest_execution.py requires Python 3.11+ (tomllib).")

MANIFEST_TOP_KEYS = {"format-version", "suite"}
SUITE_ROW_KEYS = {"id", "runner", "expected-check-ids"}
REPORT_KEYS = {"format_version", "suite", "check_ids"}


def _cannot(msg):
    """Print a cannot-evaluate finding (the caller returns 2). A pending, missing, ambiguous, or
    malformed input is unverified, never a pass."""
    print("CANNOT EVALUATE: {}".format(msg), file=sys.stderr)


def _has_ctrl(text):
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in text)


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
    except tomllib.TOMLDecodeError as exc:
        _cannot("expectation manifest {} is not valid TOML: {}".format(manifest_path, exc))
        return None
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
        if not isinstance(row, dict) or set(row) != SUITE_ROW_KEYS:
            _cannot("{}: keys must be exactly {}".format(where, sorted(SUITE_ROW_KEYS)))
            return None
        sid = row["id"]
        if not isinstance(sid, str) or not sid or _has_ctrl(sid):
            _cannot("{}: id must be a non-empty control-character-free string".format(where))
            return None
        if sid in seen_ids:
            _cannot("{}: duplicate suite id {!r}".format(where, sid))
            return None
        seen_ids.add(sid)
        runner = row["runner"]
        if (not isinstance(runner, str) or not runner or os.path.isabs(runner)
                or ".." in Path(runner).parts):
            _cannot("{}: runner must be a repo-relative path with no '..'".format(where))
            return None
        ids = row["expected-check-ids"]
        if not isinstance(ids, list) or not ids:
            _cannot("{}: expected-check-ids must be a non-empty array".format(where))
            return None
        row_seen = set()
        for cid in ids:
            if not isinstance(cid, str) or not cid or _has_ctrl(cid):
                _cannot("{}: check id {!r} must be a non-empty control-character-free string"
                        .format(where, cid))
                return None
            if cid in row_seen:
                _cannot("{}: duplicate expected check id {!r}".format(where, cid))
                return None
            row_seen.add(cid)
    return suites


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


def _read_report(report_path, suite_id):
    """The report's check-id list, strictly validated, or None after printing (the caller exits 2). A
    child exit 0 never overrides an invalid report; a run that did not deliver its agreed structured
    verdict evidence is no verdict."""
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
    if type(data["format_version"]) is not int or data["format_version"] != 1:
        _cannot("execution report {}: format_version must be exactly the integer 1".format(report_path))
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


def run_suite(root, suite_id):
    """Validate the manifest, statically reconcile the runner's source check() set against it BEFORE
    any launch, then launch the registered runner with a private report path, forward its full
    output, and reconcile the executed set. Returns the gate exit code."""
    root = Path(root)
    manifest_path = root / "tools" / "selftest_checks.toml"
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
    source_ids, error = _source_check_ids(runner)
    if error is not None:
        _cannot(error)
        return 2
    source_set = set(source_ids)
    if source_set != expected:
        _cannot("suite {}: the runner's static check() set does not match {}; the manifest must "
                "mirror the source exactly before any launch".format(suite_id, manifest_path))
        for cid in sorted(source_set - expected):
            print("  source check not registered: {}".format(cid), file=sys.stderr)
        for cid in sorted(expected - source_set):
            print("  registered id has no source check(): {}".format(cid), file=sys.stderr)
        return 2
    try:
        private = tempfile.mkdtemp(prefix="aiqt-selftest-exec-")
    except OSError as exc:
        _cannot("cannot create the private report directory: {}".format(exc))
        return 2
    try:
        report_path = os.path.join(private, "execution-report.json")
        if os.path.lexists(report_path):
            _cannot("report path {} already exists before launch".format(report_path))
            return 2
        command = [sys.executable, "-I", "-B", str(runner), "--execution-report", report_path]
        # Git-neutral child environment: drop every ambient GIT_* variable, then pin the global and
        # system config surfaces to os.devnull, so a hostile GIT_CONFIG_GLOBAL or core.hooksPath
        # cannot make the child's git subprocesses run an external hook or read a decoy repository
        # (the runner's fixtures set their git identity inline with -c, which outranks the injected
        # entries below, so commits still work). GIT_CONFIG_GLOBAL covers only the global CONFIG
        # file: git reads the global ignore and attributes surfaces from
        # XDG_CONFIG_HOME/git/{ignore,attributes} defaults even with no config file, so
        # core.excludesFile and core.attributesFile are overridden to os.devnull through git's
        # environment-config mechanism (GIT_CONFIG_COUNT/KEY/VALUE, set AFTER the drop loop so they
        # survive it). This hardens the GATE-launched child only; the runner's own direct-invocation
        # hermeticity is tracked separately (F-249).
        child_env = dict(os.environ)
        for key in list(child_env):
            if key.startswith("GIT_"):
                del child_env[key]
        child_env["GIT_CONFIG_GLOBAL"] = os.devnull
        child_env["GIT_CONFIG_SYSTEM"] = os.devnull
        child_env["GIT_CONFIG_NOSYSTEM"] = "1"
        child_env["GIT_CONFIG_COUNT"] = "2"
        child_env["GIT_CONFIG_KEY_0"] = "core.excludesFile"
        child_env["GIT_CONFIG_VALUE_0"] = os.devnull
        child_env["GIT_CONFIG_KEY_1"] = "core.attributesFile"
        child_env["GIT_CONFIG_VALUE_1"] = os.devnull
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
        observed = _read_report(report_path, suite_id)
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


# -------------------------------------------------------------------------- the hermetic self-test

GOOD_IDS = ["a/one", "a/two", "a/three"]


def _manifest_text(ids=None, runner="tools/fake_runner.py", header="format-version = 1"):
    rendered = ", ".join('"{}"'.format(cid) for cid in (GOOD_IDS if ids is None else ids))
    return (header + '\n\n[[suite]]\nid = "demo"\nrunner = "' + runner
            + '"\nexpected-check-ids = [' + rendered + ']\n')


def _report_body(ids, rc, suite="demo"):
    return ("json.dump({{'format_version': 1, 'suite': {suite!r}, 'check_ids': {ids!r}}}, "
            "open(report, 'w'))\nsys.exit({rc})\n".format(suite=suite, ids=ids, rc=rc))


def _raw_body(raw, rc=0):
    return "open(report, 'w').write({raw!r})\nsys.exit({rc})\n".format(raw=raw, rc=rc)


def self_test():
    """Synthetic roots, manifests, and fake runners in a private tempdir; children launched
    [sys.executable, -I, -B]; verdicts asserted on returned codes and captured output, never by
    grepping the real suite. The tempdir is removed in a finally."""
    failures = []

    def expect(label, got, want):
        if got != want:
            failures.append("{}: got {!r}, want {!r}".format(label, got, want))

    try:
        base = Path(tempfile.mkdtemp(prefix="aiqt-selftest-exec-st-"))
    except OSError as exc:
        _cannot("self-test temp storage unavailable: {}".format(exc))
        return 2
    counter = [0]

    def dead_checks(ids):
        """A statically present but never-executed check() block: the fake runner writes its report
        directly, so its source satisfies the pre-launch static reconcile without a live choke
        point."""
        return "if False:\n" + "".join("    check({!r}, 0, 0)\n".format(cid) for cid in ids)

    def build(manifest_text, runner_body=None, source_block=None):
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        (root / "tools").mkdir(parents=True)
        (root / "tools" / "selftest_checks.toml").write_text(manifest_text, encoding="utf-8")
        if runner_body is not None:
            (root / "tools" / "fake_runner.py").write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "here = os.path.dirname(os.path.abspath(__file__))\n"
                "open(os.path.join(here, 'launched.marker'), 'w').close()\n"
                "report = sys.argv[2]\n"
                + (dead_checks(GOOD_IDS) if source_block is None else source_block)
                + runner_body, encoding="utf-8")
        return root

    def run(root, suite_id="demo"):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_suite(root, suite_id)
        return code, out.getvalue(), err.getvalue()

    def launched(root):
        return (root / "tools" / "launched.marker").exists()

    try:
        # 1: exact match -> 0 with the machine-derived PASS line
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0))
        code, out, _err = run(root)
        expect("st/exact-match-passes", code, 0)
        expect("st/pass-line-shape",
               "PASS: suite demo executed 3 unique registered checks; set-sha256=" in out, True)

        # 2: reordered report -> still 0 (set comparison; report order is diagnostics only)
        code, _out, _err = run(build(_manifest_text(), _report_body(list(reversed(GOOD_IDS)), 0)))
        expect("st/reorder-passes", code, 0)

        # 3: one id omitted -> 1, named under missing, nothing under extra
        code, out, _err = run(build(_manifest_text(), _report_body(["a/one", "a/two"], 0)))
        expect("st/omit-fails", code, 1)
        expect("st/omit-names-missing", "missing (registered, never executed): a/three" in out, True)
        expect("st/omit-no-extra", "extra (executed, not registered)" in out, False)

        # 4: one extra id -> 1, named under extra
        code, out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS + ["a/rogue"], 0)))
        expect("st/extra-fails", code, 1)
        expect("st/extra-named", "extra (executed, not registered): a/rogue" in out, True)

        # 5: duplicate observed id in the report -> 2
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS + ["a/one"], 0)))
        expect("st/dup-observed-2", code, 2)

        # 6: duplicate expected id in the manifest -> 2, and the child is NEVER launched
        root = build(_manifest_text(ids=["a/one", "a/one"]), _report_body(GOOD_IDS, 0))
        code, _out, _err = run(root)
        expect("st/dup-expected-2", code, 2)
        expect("st/dup-expected-no-launch", launched(root), False)

        # 7: malformed expectation manifests, each 2 BEFORE any launch
        for label, text in (
                ("bad-toml", "format-version = \n"),
                ("extra-top-key", _manifest_text() + "\nstray = 1\n"),
                ("missing-row-key",
                 'format-version = 1\n\n[[suite]]\nid = "demo"\nexpected-check-ids = ["a/one"]\n'),
                ("extra-row-key", _manifest_text().replace('runner = ', 'note = "x"\nrunner = ')),
                ("non-string-id", _manifest_text().replace('"a/three"', '3')),
                ("control-char-id", _manifest_text().replace('"a/three"', '"a/\\u0001"')),
                ("empty-id-list", _manifest_text(ids=[])),
                ("bool-format-version", _manifest_text(header="format-version = true"))):
            root = build(text, _report_body(GOOD_IDS, 0))
            code, _out, _err = run(root)
            expect("st/manifest-{}-2".format(label), code, 2)
            expect("st/manifest-{}-no-launch".format(label), launched(root), False)

        # 8: child exits 0 but writes no report -> 2, never a pass
        code, _out, _err = run(build(_manifest_text(), "sys.exit(0)\n"))
        expect("st/missing-report-2", code, 2)

        # 9: malformed reports -> 2, whatever the child's exit code
        for label, raw in (
                ("truncated-json", '{"format_version": 1, "suite": "demo", "check_ids": ["a/one"'),
                ("missing-key", json.dumps({"format_version": 1, "check_ids": GOOD_IDS})),
                ("extra-key", json.dumps(
                    {"format_version": 1, "suite": "demo", "check_ids": GOOD_IDS, "count": 3})),
                ("ids-not-list", json.dumps(
                    {"format_version": 1, "suite": "demo", "check_ids": "a/one"})),
                ("bool-format-version", json.dumps(
                    {"format_version": True, "suite": "demo", "check_ids": GOOD_IDS})),
                ("dup-member", '{"format_version": 1, "suite": "demo", "check_ids": ["a/wrong"], '
                 '"check_ids": ["a/one", "a/two", "a/three"]}')):
            code, _out, _err = run(build(_manifest_text(), _raw_body(raw)))
            expect("st/report-{}-2".format(label), code, 2)

        # 10: wrong-suite report -> 2
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0, suite="other")))
        expect("st/wrong-suite-2", code, 2)

        # 11: non-regular report: a directory, then a symlink to a valid payload -> 2
        code, _out, _err = run(build(_manifest_text(), "os.mkdir(report)\nsys.exit(0)\n"))
        expect("st/report-dir-2", code, 2)
        code, _out, _err = run(build(_manifest_text(), (
            "open(report + '.real', 'w').write(json.dumps({'format_version': 1, 'suite': 'demo', "
            "'check_ids': ['a/one', 'a/two', 'a/three']}))\n"
            "os.symlink(report + '.real', report)\nsys.exit(0)\n")))
        expect("st/report-symlink-2", code, 2)

        # 12: child rc 1 with a complete report -> 1, never masked to 0
        code, out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 1)))
        expect("st/child-fail-complete-set-1", code, 1)
        expect("st/child-fail-stated",
               "execution set complete; child reported assertion failures" in out, True)

        # 13: child rc 0 with an incomplete report: missing ids -> 1 naming them; truncated JSON -> 2
        code, out, _err = run(build(_manifest_text(), _report_body(["a/one"], 0)))
        expect("st/rc0-missing-1", code, 1)
        expect("st/rc0-missing-named", "missing (registered, never executed): a/two" in out, True)
        code, _out, _err = run(build(_manifest_text(), _raw_body('{"format_version": 1, ')))
        expect("st/rc0-truncated-2", code, 2)

        # 14: child rc 2 -> 2 (a harness error is no verdict)
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 2)))
        expect("st/child-rc2-2", code, 2)

        # 15: runner mismatch -> 2, no launch: escaping, non-regular, and symlinked runners
        root = build(_manifest_text(runner="../../outside.py"), _report_body(GOOD_IDS, 0))
        code, _out, _err = run(root)
        expect("st/runner-escape-2", code, 2)
        expect("st/runner-escape-no-launch", launched(root), False)
        root = build(_manifest_text(runner="tools"), _report_body(GOOD_IDS, 0))
        code, _out, _err = run(root)
        expect("st/runner-non-regular-2", code, 2)
        expect("st/runner-non-regular-no-launch", launched(root), False)
        root = build(_manifest_text(runner="tools/link_runner.py"), _report_body(GOOD_IDS, 0))
        os.symlink("fake_runner.py", str(root / "tools" / "link_runner.py"))
        code, _out, _err = run(root)
        expect("st/runner-symlink-2", code, 2)
        expect("st/runner-symlink-no-launch", launched(root), False)

        # 16: the motivating near-miss: a GREEN child that never executed one registered check fails,
        # naming exactly that id; its complete-report twin passes
        code, out, _err = run(build(_manifest_text(), _report_body(["a/one", "a/two"], 0)))
        expect("st/near-miss-caught", code, 1)
        expect("st/near-miss-names-id", "missing (registered, never executed): a/three" in out, True)
        expect("st/near-miss-names-only-it", out.count("missing (registered, never executed)"), 1)
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0)))
        expect("st/near-miss-twin-passes", code, 0)

        # 17 (FIX 1): the static source-to-manifest reconcile refuses, BEFORE any launch, a source
        # check absent from the manifest (even an unreachable one), a registered id with no source
        # site, a dead duplicate-id alias, and an unresolvable dynamic id; the loop-literal pattern
        # resolves, and a clean source==manifest twin still passes
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS + ["a/dead"]))
        code, _out, err = run(root)
        expect("st/static-unregistered-2", code, 2)
        expect("st/static-unregistered-named", "source check not registered: a/dead" in err, True)
        expect("st/static-unregistered-no-launch", launched(root), False)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(["a/one", "a/two"]))
        code, _out, err = run(root)
        expect("st/static-sourceless-2", code, 2)
        expect("st/static-sourceless-named",
               "registered id has no source check(): a/three" in err, True)
        expect("st/static-sourceless-no-launch", launched(root), False)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS + ["a/one"]))
        code, _out, err = run(root)
        expect("st/static-dup-alias-2", code, 2)
        expect("st/static-dup-alias-named", "duplicate source check id 'a/one'" in err, True)
        expect("st/static-dup-alias-no-launch", launched(root), False)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS)
                     + 'if False:\n    check("x-%s" % label, 0, 0)\n')
        code, _out, err = run(root)
        expect("st/static-dynamic-id-2", code, 2)
        expect("st/static-dynamic-id-named", "unresolvable check id at" in err, True)
        expect("st/static-dynamic-id-no-launch", launched(root), False)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0), source_block=(
            'if False:\n'
            '    for check_id, other in (("a/one", 1), ("a/two", 2)):\n'
            '        check(check_id, 0, 0)\n'
            '    check("a/three", 0, 0)\n'))
        code, _out, _err = run(root)
        expect("st/static-loop-literal-passes", code, 0)
        code, _out, _err = run(build(_manifest_text(), _report_body(GOOD_IDS, 0)))
        expect("st/static-clean-twin-passes", code, 0)

        # 18 (FIX 3): an exported copy of this gate run from outside a git checkout refuses to guess
        # its target instead of validating whatever tree the cwd happens to sit in
        exported = base / "exported"
        exported.mkdir()
        gate_copy = exported / "check_selftest_execution.py"
        gate_copy.write_text(Path(__file__).resolve().read_text(encoding="utf-8"),
                             encoding="utf-8")
        proc = subprocess.run([sys.executable, "-I", "-B", str(gate_copy), "--suite", "demo"],
                              capture_output=True)
        expect("st/exported-no-git-2", proc.returncode, 2)
        expect("st/exported-no-git-named",
               "cannot confirm the gate's own repo root"
               in proc.stderr.decode("utf-8", errors="replace"), True)

        # 19 (FIX 4): unavailable temp storage for the private report directory is cannot-evaluate,
        # never a finding, and the child is never launched; the pinned tempdir is restored after
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0))
        real_tempdir = tempfile.tempdir
        tempfile.tempdir = str(base / "no-such-dir" / "nested")
        try:
            code, _out, _err = run(root)
        finally:
            tempfile.tempdir = real_tempdir
        expect("st/mkdtemp-oserror-2", code, 2)
        expect("st/mkdtemp-oserror-no-launch", launched(root), False)

        # 20 (round 3, FIX A): a reference to the bare name check outside a direct call (an alias
        # that could carry a call site beyond the static scan) is refused fail-closed, no launch
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS)
                     + 'renamed = check\nif False:\n    renamed("a/ghost", 0, 0)\n')
        code, _out, err = run(root)
        expect("st/static-alias-ref-2", code, 2)
        expect("st/static-alias-ref-named", "referenced outside a direct call" in err, True)
        expect("st/static-alias-ref-no-launch", launched(root), False)

        # 21 (round 3, FIX B): a check() id Name bound inside a comprehension is refused fail-closed
        # (the lexical for-stack does not model comprehension scope, so an enclosing for could
        # mis-attribute it); a literal-id check() inside a comprehension still resolves
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS) + (
                         'if False:\n'
                         '    for check_id, other in (("a/one", 1), ("a/two", 2)):\n'
                         '        [check(check_id, 0, 0) for check_id in ("a/one", "a/rogue")]\n'))
        code, _out, err = run(root)
        expect("st/static-comp-id-2", code, 2)
        expect("st/static-comp-id-named", "inside a comprehension" in err, True)
        expect("st/static-comp-id-no-launch", launched(root), False)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(["a/one", "a/two"])
                     + 'if False:\n    [check("a/three", 0, 0) for _ in (1,)]\n')
        code, _out, _err = run(root)
        expect("st/static-comp-literal-passes", code, 0)

        # 22 (round 3, FIX C; round 4, FIX 3): the child is launched under a git-neutral
        # environment: every ambient GIT_* variable is dropped, the config surfaces pin to
        # os.devnull, and core.excludesFile and core.attributesFile are overridden to os.devnull by
        # injected GIT_CONFIG_* entries (the global ignore and attributes surfaces the pinned config
        # files do not cover), so a poisoned GIT_CONFIG_GLOBAL (for example a core.hooksPath naming
        # an attacker hook) or a hostile global ignore never reaches the child; the fake runner
        # dumps the GIT_* environment it actually saw, asserted as the exact injected set
        root = build(_manifest_text(),
                     "git_env = dict((k, v) for k, v in os.environ.items() "
                     "if k.startswith('GIT_'))\n"
                     "open(os.path.join(here, 'git-env.json'), 'w').write(json.dumps(git_env))\n"
                     + _report_body(GOOD_IDS, 0))
        saved_env = dict((k, os.environ.get(k)) for k in ("GIT_CONFIG_GLOBAL", "GIT_DIR"))
        os.environ["GIT_CONFIG_GLOBAL"] = str(base / "poisoned-gitconfig")
        os.environ["GIT_DIR"] = str(base / "decoy-git")
        try:
            code, _out, _err = run(root)
        finally:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        expect("st/git-env-passes", code, 0)
        env_dump = root / "tools" / "git-env.json"
        expect("st/git-env-dump-written", env_dump.exists(), True)
        if env_dump.exists():
            expect("st/git-env-neutralized", json.loads(env_dump.read_text(encoding="utf-8")),
                   dict(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_COUNT="2",
                        GIT_CONFIG_KEY_0="core.excludesFile", GIT_CONFIG_VALUE_0=os.devnull,
                        GIT_CONFIG_KEY_1="core.attributesFile", GIT_CONFIG_VALUE_1=os.devnull))

        # 23 (round 4, FIX 1): a statically EMPTY literal loop is refused fail-closed, no launch: an
        # empty iterable resolves its check() site to zero ids, so without the refusal the site
        # would be silently invisible (the exact "loop that no longer iterates" failure)
        root = build(_manifest_text(), _report_body(GOOD_IDS, 0),
                     source_block=dead_checks(GOOD_IDS)
                     + 'for check_id, other in ():\n    check(check_id, 0, 0)\n')
        code, _out, err = run(root)
        expect("st/static-empty-loop-2", code, 2)
        expect("st/static-empty-loop-named", "unresolvable check id at" in err, True)
        expect("st/static-empty-loop-no-launch", launched(root), False)

        # Round 3 (FIX E note): leg 19 witnesses run_suite's mkdtemp guard; self_test's own base
        # tempdir guard above cannot be witnessed from inside this self-test without circularity,
        # and the runner's FAILURES-before-report-write-OSError ordering has no cheap hermetic
        # witness from here, so both remain manual-flip verifications, disclosed rather than forced
        # into a fragile test.
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: the execution-set gate passes an exact or reordered set, names a missing and "
          "an extra id, refuses duplicate observed and expected ids (the latter with no launch), refuses "
          "a malformed expectation manifest before any launch, treats a missing, malformed, wrong-suite, "
          "duplicate-member, or non-regular report as no verdict, never masks a failing child behind a "
          "complete set, refuses a child harness error, refuses an escaping, non-regular, or symlinked "
          "runner without launching, reconciles the runner's static check() source set against the "
          "manifest before any launch (refusing an unregistered source check, a registered id with no "
          "source site, a dead duplicate-id alias, an unresolvable dynamic id, a statically empty "
          "literal loop, a non-call reference to "
          "the bare name check, and a comprehension-bound id, while the loop-literal pattern and a "
          "literal id inside a comprehension resolve and a clean twin passes), launches the child under "
          "a git-neutral environment (ambient GIT_* dropped, config surfaces pinned to os.devnull, "
          "global ignore and attributes overridden to os.devnull by injected config), "
          "treats unavailable temp storage as "
          "cannot-evaluate, refuses to guess its repo root when run outside a checkout, and catches the "
          "near-miss (a green child that never executed a registered check) while its complete-report "
          "twin passes")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 2 and argv[0] == "--suite" and argv[1]:
        # Anchored to this file, never the cwd: an exported copy run from inside another checkout
        # must refuse rather than validate the wrong tree. A worktree's .git is a file, so .exists().
        root = Path(__file__).resolve().parent.parent
        if not (root / ".git").exists():
            _cannot("cannot confirm the gate's own repo root (no .git at {})".format(root))
            return 2
        return run_suite(root, argv[1])
    print("usage: check_selftest_execution.py --suite <id> | --self-test", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
