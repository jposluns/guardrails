#!/usr/bin/env python3
"""Execution-set gate for registered self-test suites (chgchk/evgcmp): prove every registered check
RAN, not merely that the suite went green.

A present-but-unreached check() call (dead code after an early return, a misindented block, a loop that
no longer iterates) is indistinguishable from a passing one when judged by the suite's exit code alone.
This gate closes that: the suite's check() choke point records every check id actually reached, the
child emits them as a structured JSON report, and this gate reconciles that report, as an exact set,
against the hand-authored expectation manifest tools/selftest_checks.toml. The manifest is authored from
SOURCE REVIEW and is NEVER regenerated from a runtime capture (a captured manifest would drop an
accidentally-unreachable check from both sides of the comparison and stay green, the exact failure this
gate exists to catch); there is deliberately no accept, update, or baseline command.

  check_selftest_execution.py --suite <id>   run the registered suite and reconcile its execution set
  check_selftest_execution.py --self-test    synthetic manifests and fake runners assert every leg fires

The child is launched [sys.executable, -I, -B, <runner>, --execution-report, <private abs path>] with
cwd at the repo root; its full stdout and stderr are forwarded UNFILTERED to this gate's own streams (no
grep, no truncation), and its verdict is judged by its real return code plus the strict report
reconcile, never by its prose. A report that is missing, truncated, malformed, wrong-suite, non-regular,
or carrying a duplicate or wrong-typed entry is CANNOT-EVALUATE, never a pass, whatever the child's exit
code; completeness is never inferred from output volume or from the absence of a reported problem.

Exit convention: 0 the execution set matches exactly AND the child passed; 1 a real finding (a missing
or extra check id, or a set-complete child that itself reported assertion failures); 2 usage or
cannot-evaluate (an unreadable, malformed, or suite-missing expectation manifest; a runner that is
absent, non-regular, a symlink, or escapes the repo; a launch failure; a child return code outside
{0, 1}; or an invalid report).

DISCLOSED RESIDUAL: this gate proves INVOCATION IDENTITY only. It does not prove an invoked assertion is
discriminating (a constant-true check counts as executed), carries no mutation sensitivity, sees nothing
outside the instrumented check() helper (a direct FAILURES.append, or a suite not registered in the
manifest), and cannot distinguish a legitimate from an illegitimate coordinated deletion of a check and
its manifest row in one reviewed change; the expectation manifest is trusted hand-authored input whose
schema is machine-validated here but whose completeness rests on source review. The child runs un-timed
(parity with the roster's other selftest steps; the CI job timeout is the outer bound).
"""
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

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
            data = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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
    """Validate the manifest, launch the registered runner with a private report path, forward its full
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
    private = tempfile.mkdtemp(prefix="aiqt-selftest-exec-")
    try:
        report_path = os.path.join(private, "execution-report.json")
        if os.path.lexists(report_path):
            _cannot("report path {} already exists before launch".format(report_path))
            return 2
        command = [sys.executable, "-I", "-B", str(runner), "--execution-report", report_path]
        try:
            child = subprocess.run(command, cwd=str(root), capture_output=True)
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

    base = Path(tempfile.mkdtemp(prefix="aiqt-selftest-exec-st-"))
    counter = [0]

    def build(manifest_text, runner_body=None):
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
                "report = sys.argv[2]\n" + runner_body, encoding="utf-8")
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
                    {"format_version": True, "suite": "demo", "check_ids": GOOD_IDS}))):
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
          "or non-regular report as no verdict, never masks a failing child behind a complete set, "
          "refuses a child harness error, refuses an escaping, non-regular, or symlinked runner without "
          "launching, and catches the near-miss (a green child that never executed a registered check) "
          "while its complete-report twin passes")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 2 and argv[0] == "--suite" and argv[1]:
        return run_suite(repo_root(), argv[1])
    print("usage: check_selftest_execution.py --suite <id> | --self-test", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
