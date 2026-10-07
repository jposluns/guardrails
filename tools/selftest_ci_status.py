#!/usr/bin/env python3
"""Behavioural self-test for tools/ci-status.sh.

Every case runs the real script in a throwaway git repository with controlled gh, date, and sleep
executables. The gh fixture emits paginated JSON for the script's real jq executable, separately for the
head_sha-filtered query and the unfiltered run listing, and a separate check extracts that expression
from ci-status.sh and exercises it directly against crafted JSON. Verdicts use the child's return code
and complete captured output, never a success token.

  selftest_ci_status.py                              exit 0 on self-test pass, 1 on assertion failure
  selftest_ci_status.py --execution-report ABS_PATH  also write the executed check IDs as JSON

Once the checks start, main() ends every run through one rule, whatever ends it: a normal finish, a
recorded harness error, an uncaught exception, or SystemExit raised anywhere, including inside check().
The rule: exit 1 if any assertion failed, else 2 if any harness error was recorded (an uncaught
exception or SystemExit is recorded as one), else 0. Harness errors also include a failed report
write, an unreadable or malformed expectation manifest (a suite container that is not an array of
tables included), a ci-status.sh whose jq program cannot be extracted, a duplicate check id, and a
diagnostic or result that cannot be printed or flushed. The execution set is reconciled against the
manifest only when no check recorded a harness error, because an unevaluated check would also be
reported missing. Bad arguments, a Python older than 3.14, and a
missing tomllib are refused before any check runs, so they always exit 2.

Reporting can never escape or change the rule. An uncaught exception is recorded before its text is
rendered, and exception or path text in a diagnostic goes through _safe_text(), which contains whatever
str() or repr() raises (SystemExit included) and falls back to the type name. Each print and each flush
of a result line (the FAIL header, each failure line, the stderr summary line, the PASS line and each
void line) is contained on its own and a failure is recorded as a harness error, so a fault on one line
or stream does not stop the later lines, on the same stream or the other. The verdict is read only after
both streams are flushed of earlier output; a PASS line is then the last output. The exit code is
computed once, after all reporting, and the script ends with os._exit() on that code, so the
interpreter's own exit-time flush cannot replace it.

What a reader of stdout can rely on: only the exit code is authoritative. If printing or flushing the
PASS line fails, the run exits 2 and then writes the void line (any PASS line above is void) to stdout
and to stderr. On stdout the void line is written together with a line break before it, so it always
starts a line of its own even when the PASS write stopped mid-line; when the PASS line was complete, a
blank line comes before it. Those void lines are best effort: a stream that rejects them, or that cannot
be written at all, shows no void line. So a stdout may end with a PASS text, whole or cut off, and no
void line after it while the run exits 2, and a stdout that cannot be written at all, or that was
already closed when the run started (None in Python), shows nothing; the exit code still follows the
rule. A PASS text on stdout that a later line voids is not a pass.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: selftest_ci_status.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import contextlib
import io
import json
import os
import stat
import subprocess
import tempfile
import time
import traceback
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    sys.stderr.write(
        "error: selftest_ci_status.py cannot import tomllib, part of the Python standard library; "
        "this installation is incomplete. Nothing was run (cannot evaluate).\n")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_PATH = "/usr/bin:/bin"
GIT = "/usr/bin/git"
JQ = "/usr/bin/jq"
# The F-367 argv pins, mirroring _git_fixture_env._NO_AUTO_MAINTENANCE: an unpinned fixture
# commit spawns the DETACHED `git maintenance run --auto` child, which once its task
# thresholds are met can repack or prune this fixture's .git/objects after the commit
# returned, racing the TemporaryDirectory teardown (and any later read). Spliced into the
# fixture git argv, NOT into base_env, so the environment ci-status.sh receives through
# invoke() stays exactly as before.
NO_AUTO_MAINTENANCE = ["-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                       "-c", "maintenance.auto=false"]
SCRIPT = ROOT / "tools" / "ci-status.sh"
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
SUITE_ID = "ci-status-behaviour-selftest"
# The fixture commit's committer date and the fixture clock. The script scans the unfiltered listing
# back to min(committer date, now) minus its 24-hour margin; these mirror that arithmetic.
COMMIT_TIME = 1789990000
NOW = 1790000000
MARGIN = 86400
LOWER_BOUND = COMMIT_TIME - MARGIN
MAX_PAGES = 50
FAILURES = []
HARNESS_ERRORS = []
EXECUTED = []
_EXECUTED_SET = set()

FAKE_GH = r"""import json
import os
import sys

# A poll is either a list of pages served as the head_sha query's pages (the unfiltered listing then
# serves the same runs as one consistent listing, 100 per page), a dict with "filtered" and "scan" page
# lists or "scan_every_page" (one page served for every page number), or a dict with "error" (first
# query fails) or "scan_error" (only the unfiltered listing fails). Only the head_sha query advances the
# poll counter; every unfiltered page number read is appended to the scan log.
args = sys.argv[1:]
endpoint = next((arg for arg in args if arg.startswith("repos/")), "")
listing = "repos/fixture/repository/actions/runs"
scan_prefix = listing + "?per_page=100&page="
if (endpoint.startswith(listing + "?head_sha=") and endpoint.endswith("&per_page=100")
        and "--paginate" in args and "--slurp" in args):
    source = "filtered"
elif (endpoint.startswith(scan_prefix) and endpoint[len(scan_prefix):].isdigit()
        and "--paginate" not in args and "--slurp" not in args):
    source = "scan"
    page_no = int(endpoint[len(scan_prefix):])
else:
    print("gh fixture: query must request head_sha with per_page=100 and --paginate --slurp, "
          "or one unfiltered page with only per_page and page", file=sys.stderr)
    sys.exit(1)
with open(os.environ["MOCK_GH_COUNTER"], "r", encoding="utf-8") as handle:
    call = int(handle.read().strip())
if source == "filtered":
    call += 1
    with open(os.environ["MOCK_GH_COUNTER"], "w", encoding="utf-8") as handle:
        handle.write(str(call) + "\n")
elif call == 0:
    print("gh fixture: unfiltered listing requested before the head_sha query", file=sys.stderr)
    sys.exit(1)
else:
    with open(os.environ["MOCK_GH_SCAN_LOG"], "a", encoding="utf-8") as handle:
        handle.write(str(page_no) + "\n")
with open(os.environ["MOCK_GH_RESPONSE"], "r", encoding="utf-8") as handle:
    polls = json.load(handle)["polls"]
poll = polls[min(call - 1, len(polls) - 1)]
if isinstance(poll, dict) and "error" in poll:
    print(poll["error"], file=sys.stderr)
    sys.exit(poll.get("exit", 1))
if source == "filtered":
    pages = poll.get("filtered") if isinstance(poll, dict) else poll
    if not isinstance(pages, list) or not pages:
        print("gh fixture: malformed poll", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(pages))
    sys.exit(0)
if isinstance(poll, dict) and "scan_error" in poll:
    print(poll["scan_error"], file=sys.stderr)
    sys.exit(1)
if isinstance(poll, dict) and "scan_every_page" in poll:
    print(json.dumps(poll["scan_every_page"]))
    sys.exit(0)
if isinstance(poll, dict):
    pages = poll.get("scan")
else:
    runs = [run for item in poll for run in item["workflow_runs"]]
    pages = [{"total_count": len(runs), "workflow_runs": runs[start:start + 100]}
             for start in range(0, max(len(runs), 1), 100)]
if not isinstance(pages, list) or page_no < 1 or page_no > len(pages):
    print("gh fixture: no unfiltered page {}".format(page_no), file=sys.stderr)
    sys.exit(1)
print(json.dumps(pages[page_no - 1]))
"""

FAKE_DATE = r"""import os
import sys

if sys.argv[1:] == ["+%s"]:
    with open(os.environ["MOCK_CLOCK"], "r", encoding="utf-8") as handle:
        print(handle.read().strip())
elif sys.argv[1:] == ["-u", "+%H:%M:%SZ"]:
    print("00:00:00Z")
else:
    print("date fixture: unsupported arguments", file=sys.stderr)
    sys.exit(1)
"""

FAKE_SLEEP = r"""import os
import sys

try:
    seconds = int(sys.argv[1])
    with open(os.environ["MOCK_CLOCK"], "r", encoding="utf-8") as handle:
        now = int(handle.read().strip())
    with open(os.environ["MOCK_CLOCK"], "w", encoding="utf-8") as handle:
        handle.write(str(now + seconds) + "\n")
except (IndexError, OSError, ValueError) as exc:
    print("sleep fixture: {}".format(exc), file=sys.stderr)
    sys.exit(1)
"""


def check(name, got, want):
    if name in _EXECUTED_SET:
        _duplicate_check_id(name)
    else:
        _EXECUTED_SET.add(name)
        EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {}, want {}".format(name, _safe_text(repr, got), _safe_text(repr, want)))


def _duplicate_check_id(name):
    # Recorded, not raised, and check() still evaluates the duplicate's own assertion; the "duplicate"
    # finalisation case pins both through two real check() calls.
    _record_harness_error("{}: duplicate check id".format(name))


def harness_error(check_id, detail):
    _record_harness_error("{}: not evaluated: {}".format(check_id, detail))


def _record_harness_error(message):
    # Every harness error is recorded here, before it is printed; a failing print is contained, so it
    # neither escapes nor erases the record.
    HARNESS_ERRORS.append(message)
    _contained(print, "SELF-TEST HARNESS ERROR: " + message, file=sys.stderr)


def _contained(action, *args, **kwargs):
    """Run one diagnostic action; whatever it raises, SystemExit included, is recorded, never raised."""
    try:
        action(*args, **kwargs)
    except BaseException as exc:
        HARNESS_ERRORS.append("diagnostic output failed: " + _type_name(exc))
        try:
            print("SELF-TEST HARNESS ERROR: diagnostic output failed: " + _type_name(exc), file=sys.stderr)
        except BaseException:  # the stream that failed may be stderr itself; the record above stands
            pass


def _type_name(obj):
    # type(obj).__name__ can itself run code (a metaclass property); the fallback is a constant.
    try:
        name = type(obj).__name__
        if type(name) is str:
            return name
    except BaseException:
        pass
    return "<unnamed type>"


def _safe_text(render, obj):
    """render(obj) (str or repr), or the type name if that raises anything, SystemExit included."""
    try:
        text = render(obj)
        if type(text) is str:
            return text
    except BaseException:
        pass
    return "<unprintable {}>".format(_type_name(obj))


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def workflow_run(run_id, status, conclusion, name, head_sha, created=COMMIT_TIME + 600):
    return {
        "created_at": iso(created),
        "id": run_id,
        "head_sha": head_sha,
        "status": status,
        "conclusion": conclusion,
        "name": name,
        "html_url": "https://example.invalid/runs/{}".format(run_id),
    }


def page(runs, total_count=None):
    return {"total_count": len(runs) if total_count is None else total_count,
            "workflow_runs": runs}


def consistent_scan(pages):
    runs = [run for item in pages for run in item["workflow_runs"]]
    return [page(runs[start:start + 100], len(runs)) for start in range(0, max(len(runs), 1), 100)]


def jq_program():
    source = SCRIPT.read_text(encoding="utf-8")
    prefix = "CI_STATUS_JQ='\n"
    suffix = "'\n\nci_jq()"
    if source.count(prefix) != 1:
        raise ValueError("ci-status.sh must contain exactly one jq program")
    start = source.index(prefix) + len(prefix)
    end = source.index(suffix, start)
    return source[start:end]


def run_jq(program, pages, requested_sha, scan_pages=None, mode="verdict"):
    # Verdict mode slurps two documents: the head_sha-filtered pages, then the scan's pages (by default
    # one consistent listing of the same runs). Step mode slurps one raw unfiltered page (pages).
    if mode == "step":
        stdin = json.dumps(pages) + "\n"
    else:
        scan = consistent_scan(pages) if scan_pages is None else scan_pages
        stdin = json.dumps(pages) + "\n" + json.dumps(scan) + "\n"
    return subprocess.run(
        [JQ, "-s", "-r", "--arg", "mode", mode, "--arg", "requested_sha", requested_sha,
         "--argjson", "lower_bound", str(LOWER_BOUND), "--argjson", "max_pages", str(MAX_PAGES),
         program],
        input=stdin, text=True,
        capture_output=True, timeout=10,
        env={"PATH": SYSTEM_PATH, "LC_ALL": "C", "TZ": "UTC"},
    )


class Fixture:
    def __init__(self, base):
        self.repo = base / "repo"
        self.bin = base / "bin"
        self.home = base / "home"
        self.repo.mkdir()
        self.bin.mkdir()
        self.home.mkdir()
        self.base_env = {
            "PATH": SYSTEM_PATH,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home),
            "LC_ALL": "C",
            "TZ": "UTC",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        subprocess.run([GIT] + NO_AUTO_MAINTENANCE + ["init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True, timeout=30, env=self.base_env)
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run([GIT, "-C", str(self.repo)] + NO_AUTO_MAINTENANCE + ["add", "seed.txt"],
                       check=True, capture_output=True, timeout=30, env=self.base_env)
        commit_env = dict(self.base_env)
        commit_env["GIT_COMMITTER_DATE"] = "{} +0000".format(COMMIT_TIME)
        commit_env["GIT_AUTHOR_DATE"] = "{} +0000".format(COMMIT_TIME)
        subprocess.run(
            [GIT, "-C", str(self.repo)] + NO_AUTO_MAINTENANCE
            + ["-c", "user.name=Selftest",
               "-c", "user.email=selftest@example.invalid", "-c", "commit.gpgsign=false",
               "commit", "-q", "-m", "seed"],
            check=True, capture_output=True, timeout=30, env=commit_env)
        result = subprocess.run(
            [GIT, "-C", str(self.repo)] + NO_AUTO_MAINTENANCE + ["rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=30, env=self.base_env,
        )
        self.head_sha = result.stdout.strip()
        self.response = base / "responses.json"
        self.counter = base / "calls.txt"
        self.clock = base / "clock.txt"
        self.scan_log = base / "scan-pages.txt"
        self.scan_pages = []
        for name, body in (("gh", FAKE_GH), ("date", FAKE_DATE), ("sleep", FAKE_SLEEP)):
            executable = self.bin / name
            executable.write_text("#!{}\n{}".format(sys.executable, body), encoding="utf-8")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    def invoke(self, polls, wait=False, timeout="900", clock=NOW):
        self.response.write_text(json.dumps({"polls": polls}), encoding="utf-8")
        self.counter.write_text("0\n", encoding="utf-8")
        self.scan_log.write_text("", encoding="utf-8")
        self.clock.write_text("{}\n".format(clock), encoding="utf-8")
        env = dict(self.base_env)
        env["PATH"] = str(self.bin) + os.pathsep + SYSTEM_PATH
        env["MOCK_GH_RESPONSE"] = str(self.response)
        env["MOCK_GH_COUNTER"] = str(self.counter)
        env["MOCK_CLOCK"] = str(self.clock)
        env["MOCK_GH_SCAN_LOG"] = str(self.scan_log)
        env["CI_STATUS_REPO"] = "fixture/repository"
        env["CI_STATUS_TIMEOUT"] = timeout
        command = [str(SCRIPT), "HEAD"]
        if wait:
            command.append("--wait")
        result = subprocess.run(command, cwd=self.repo, env=env, text=True,
                                capture_output=True, timeout=30)
        calls = int(self.counter.read_text(encoding="utf-8").strip())
        self.scan_pages = [int(line) for line in self.scan_log.read_text(encoding="utf-8").split()]
        return result.returncode, result.stdout + result.stderr, calls


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    # ValueError and RecursionError too: tomllib raises a BARE ValueError (not TOMLDecodeError) on an
    # integer literal past CPython's 4300-digit int-string limit, and a RecursionError (a RuntimeError)
    # on a deeply nested array or inline table (F-TOML-BARE-VALUEERROR-CLASS).
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        _record_harness_error("cannot read {}: {}".format(
            _safe_text(str, CHECKS_MANIFEST), _safe_text(str, exc)))
        return None
    # The suite container is validated before it is iterated: `suite = 1` is a malformed manifest.
    suites = data.get("suite")
    for row in suites if isinstance(suites, list) else ():
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids):
                return set(ids)
            break
    _record_harness_error("missing or malformed suite {!r} in {}".format(
        SUITE_ID, _safe_text(str, CHECKS_MANIFEST)))
    return None


def _write_report(report_path):
    if report_path is None:
        return
    try:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump({"format_version": 1, "suite": SUITE_ID, "check_ids": EXECUTED}, handle)
            handle.write("\n")
    except OSError as exc:
        _record_harness_error("cannot write execution report {}: {}".format(
            _safe_text(str, report_path), _safe_text(str, exc)))


def _run_checks():
    with tempfile.TemporaryDirectory(prefix="ci-status-selftest-") as raw:
        fixture = Fixture(Path(raw))
        head_sha = fixture.head_sha
        success_a = workflow_run(
            101, "completed", "success", "Web generator health", head_sha)
        pending_b = workflow_run(
            202, "in_progress", None, "Repository quality checks", head_sha)
        success_b = workflow_run(
            202, "completed", "success", "Repository quality checks", head_sha)
        failed_b = workflow_run(
            202, "completed", "failure", "Repository quality checks", head_sha)

        conflict_a = workflow_run(101, "in_progress", None, "Web generator health", head_sha)
        overfull = [workflow_run(1000 + index, "completed", "success",
                                 "Run {}".format(index), head_sha)
                    for index in range(101)]
        wrong_head = dict(success_a, head_sha="f" * 40)
        # A full first page of the unfiltered listing holding only other commits' runs, so this
        # commit's run sits on page 2: outside the first page of the response.
        other_commits = [workflow_run(5000 + index, "completed", "success",
                                      "Other commit run {}".format(index), "e" * 40)
                         for index in range(100)]
        program = None
        try:
            program = jq_program()
            conflict = run_jq(program, [page([success_a, conflict_a], 1)], head_sha)
            mismatched = run_jq(program, [page([success_a], 2)], head_sha)
            differing = run_jq(
                program, [page([success_a], 2), page([success_b], 3)], head_sha)
            successful = run_jq(program, [page([success_b, success_a], 2)], head_sha)
            too_many = run_jq(program, [page(overfull, 101)], head_sha)
            wrong_target = run_jq(program, [page([wrong_head], 1)], head_sha)
            scan_found = run_jq(program, [page([])], head_sha,
                                [page(other_commits, 101), page([success_a], 101)])
            scan_short = run_jq(program, [page([])], head_sha, [page(other_commits, 101)])
            expected_rows = "\n".join(
                "completed\tsuccess\t{}\t{}\t{}".format(
                    run["id"], run["name"], run["html_url"])
                for run in (success_a, success_b)
            ) + "\n"
            direct_result = (
                conflict.returncode != 0
                and "conflicting duplicate workflow-run records" in conflict.stderr,
                mismatched.returncode != 0
                and "inconsistent paginated workflow-runs snapshot" in mismatched.stderr,
                differing.returncode != 0
                and "inconsistent paginated workflow-runs snapshot" in differing.stderr,
                (successful.returncode, successful.stdout) == (0, expected_rows),
                too_many.returncode != 0
                and "malformed workflow-runs response" in too_many.stderr,
                wrong_target.returncode != 0
                and "malformed workflow run record" in wrong_target.stderr,
                (scan_found.returncode, scan_found.stdout)
                == (0, "completed\tsuccess\t101\tWeb generator health\t"
                    "https://example.invalid/runs/101\n"),
                scan_short.returncode != 0
                and "incomplete unfiltered workflow-runs listing" in scan_short.stderr,
            )
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            direct_result = "jq-filter setup failed: " + _safe_text(str, exc)
        if program is None:
            harness_error("ci/jq-filter-direct-cases",
                          "cannot extract the jq program from {}: {}".format(SCRIPT, direct_result))
        else:
            check("ci/jq-filter-direct-cases", direct_result,
                  (True, True, True, True, True, True, True, True))

        rc, _output, _calls = fixture.invoke([[page([wrong_head])]])
        check("ci/mismatched-head-sha-exit2", rc, 2)

        rc, output, _calls = fixture.invoke([[page([success_a, pending_b])]])
        check("ci/multi-pending-not-green", rc, 1)

        polls = [
            [page([success_a])],
            [page([success_a, pending_b])],
        ] + [[page([success_a, success_b])]] * 5
        rc, _output, calls = fixture.invoke(polls, wait=True)
        check("ci/late-run-no-premature-green", (rc, calls), (0, 7))

        rc, output, _calls = fixture.invoke([[page([success_a, success_b])]])
        check("ci/all-success-green",
              (rc, "Web generator health" in output and "Repository quality checks" in output),
              (0, True))

        rc, output, _calls = fixture.invoke([[page([success_a, failed_b])]])
        check("ci/failed-run-exit1", rc, 1)
        check("ci/failed-run-named",
              "Repository quality checks" in output and "failure" in output, True)

        injected = workflow_run(
            303, "in_progress", None, "trap|completed|success\tcompleted\tsuccess\nnext",
            head_sha)
        rc, _output, _calls = fixture.invoke([[page([injected])]])
        check("ci/name-delimiter-cannot-green", rc, 1)

        rc, output, _calls = fixture.invoke([[page([])]])
        check("ci/no-run-once-exit2",
              (rc, "no workflow run registered" in output), (2, True))

        rc, _output, _calls = fixture.invoke([[page([])]], wait=True, timeout="0")
        check("ci/no-run-wait-timeout-exit1", rc, 1)

        rc, output, _calls = fixture.invoke(
            [{"error": "gh: Resource not accessible by personal access token", "exit": 1}])
        check("ci/not-accessible-exit2", (rc, "could not read workflow runs" in output), (2, True))

        rc, output, _calls = fixture.invoke(
            [{"error": "gh: Not Found (HTTP 404)", "exit": 1}])
        check("ci/not-found-exit2", (rc, "could not read workflow runs" in output), (2, True))

        permanent_wait_results = []
        for message in (
                "gh: Resource not accessible by personal access token",
                "gh: Not Found (HTTP 404)"):
            rc, output, calls = fixture.invoke(
                [{"error": message, "exit": 1}], wait=True, timeout="30")
            permanent_wait_results.append(
                (rc, calls, "could not read workflow runs" in output))
        check("ci/permanent-api-errors-wait-exit2", permanent_wait_results,
              [(2, 1, True), (2, 1, True)])

        not_found_name = workflow_run(
            404, "completed", "success", "Not Found regression", head_sha)
        rc, output, _calls = fixture.invoke([[page([not_found_name])]])
        check("ci/display-not-found-not-api-error",
              (rc, "Not Found regression" in output), (0, True))

        rc, output, _calls = fixture.invoke(
            [{"error": "__NORUN__", "exit": 1}])
        check("ci/nonzero-norun-is-api-error",
              (rc, "could not read workflow runs" in output), (2, True))

        rc, output, _calls = fixture.invoke(
            [[page([success_a], 2), page([failed_b], 2)]])
        check("ci/pagination-all-runs",
              (rc, "Repository quality checks" in output and "failure" in output), (1, True))

        success_c = workflow_run(
            303, "completed", "success", "Replacement workflow", head_sha)
        polls = [[page([success_a])], [page([success_a])]] + [[page([success_c])]] * 5
        rc, _output, calls = fixture.invoke(polls, wait=True)
        check("ci/settle-set-change-resets", (rc, calls), (0, 7))

        queued_a = workflow_run(101, "queued", None, "Web generator health", head_sha)
        polls = ([[page([success_a])]] * 2 + [[page([queued_a])]]
                 + [[page([success_a])]] * 5)
        rc, _output, calls = fixture.invoke(polls, wait=True)
        check("ci/settle-nonterminal-resets", (rc, calls), (0, 8))

        rc, output, calls = fixture.invoke(
            [[page([success_a])]] * 5, wait=True, timeout="59")
        check("ci/settle-deadline-strict",
              (rc, calls, "TIMEOUT" in output), (1, 5, True))

        # This is a detectable total-count mismatch, not the same-count replacement race that
        # non-atomic offset pagination cannot exclude.
        rc, _output, _calls = fixture.invoke(
            [[page([success_a], 3), page([success_b], 3)]])
        check("ci/pagination-count-mismatch-not-green", rc, 2)

        # FIX-CI-STATUS-MISSES-COMPLETED-RUN: the head_sha filter answered zero runs while the
        # unfiltered listing held this commit's completed/success run on its SECOND page. The run
        # must be found and reported, never "no workflow run registered".
        rc, output, _calls = fixture.invoke([{
            "filtered": [page([])],
            "scan": [page(other_commits, 101), page([success_a], 101)]}])
        check("ci/scan-finds-run-beyond-first-page",
              (rc, "Web generator health" in output, "no workflow run registered" in output),
              (0, True, False))

        # A failed run missing from the head_sha filter but present beyond the first page of the
        # unfiltered listing must still fail the check, not leave only its green sibling.
        rc, output, _calls = fixture.invoke([{
            "filtered": [page([success_a])],
            "scan": [page(other_commits, 102), page([success_a, failed_b], 102)]}])
        check("ci/scan-finds-dropped-failure",
              (rc, "Repository quality checks" in output and "failure" in output), (1, True))

        # The unfiltered listing failing or stopping short is an API error (fail closed), both when
        # the filter found nothing and when it found only green runs.
        more_commits = [workflow_run(6000 + index, "completed", "success",
                                     "More commit run {}".format(index), "e" * 40)
                        for index in range(40)]
        scan_closed = []
        for filtered in ([page([])], [page([success_a])]):
            rc, output, _calls = fixture.invoke([{
                "filtered": filtered, "scan_error": "gh: HTTP 502"}])
            scan_closed.append((rc, "could not read workflow runs" in output))
            rc, output, _calls = fixture.invoke([{
                "filtered": filtered,
                "scan": [page(other_commits, 250), page(more_commits, 250)]}])
            scan_closed.append((rc, "incomplete unfiltered workflow-runs listing" in output))
        check("ci/scan-unanswered-fail-closed", scan_closed, [(2, True)] * 4)

        # The two sources disagreeing about one run's state is an API error, never a verdict; one-shot
        # mode re-reads once first (MINOR 4), so a persistent disagreement costs two head_sha queries.
        rc, output, calls = fixture.invoke([{
            "filtered": [page([success_a, success_b])],
            "scan": [page([success_a, failed_b])]}])
        check("ci/scan-source-conflict-fail-closed",
              (rc, "conflicting duplicate workflow-run records" in output, calls), (2, True, 2))

        # A run changing state between the two sources' reads settles on the one-shot re-read.
        rc, output, calls = fixture.invoke([
            {"filtered": [page([success_a, pending_b])], "scan": [page([success_a, success_b])]},
            [page([success_a, success_b])]])
        check("ci/oneshot-conflict-retried-once",
              (rc, calls, "re-reading once" in output), (0, 2, True))

        # The scan is bounded: a full page holding only runs created before the lower bound ends it,
        # so page 2 (absent from the fixture, an error if read) is never requested.
        old_commits = [workflow_run(7000 + index, "completed", "success",
                                    "Old run {}".format(index), "e" * 40,
                                    created=LOWER_BOUND - 60 - index)
                       for index in range(100)]
        rc, _output, _calls = fixture.invoke([{
            "filtered": [page([success_a])], "scan": [page(old_commits, 900)]}])
        check("ci/scan-stops-at-lower-bound", (rc, fixture.scan_pages), (0, [1]))

        # The 24-hour margin: a run of this commit created two hours BEFORE its committer date (a
        # committer clock running ahead) is still read, beyond a page of runs inside the margin.
        skewed = [workflow_run(7200 + index, "completed", "success", "Skew run {}".format(index),
                               "e" * 40, created=COMMIT_TIME - 3600 - index)
                  for index in range(100)]
        early_failure = dict(failed_b, created_at=iso(COMMIT_TIME - 7200))
        rc, output, _calls = fixture.invoke([{
            "filtered": [page([success_a])], "scan": [page(skewed, 101), page([early_failure], 101)]}])
        check("ci/scan-margin-covers-clock-skew",
              (rc, fixture.scan_pages, "failure" in output), (1, [1, 2], True))

        # Round 3: pin the margin and the strict comparison at the bound. Only the scan sees this
        # commit's green run, on page 2, so the verdict is green only if page 1 does not end the scan.
        # (a) Skew of about 23 hours: page 1 holds runs created between 23 and 24 hours before the
        # committer date, so a margin of 3600 or 82800 seconds stops after page 1 and reports no run.
        # (b) Page 1's newest run is created exactly AT the lower bound and the rest before it, so
        # replacing `<` with `<=` in the stop rule stops after page 1 and reports no run.
        near_day = [workflow_run(7600 + index, "completed", "success", "Near day run {}".format(index),
                                 "e" * 40, created=COMMIT_TIME - 82800 - 60 - index)
                    for index in range(100)]
        at_bound = [workflow_run(7800 + index, "completed", "success", "At bound run {}".format(index),
                                 "e" * 40, created=LOWER_BOUND - index)
                    for index in range(100)]
        boundary = []
        for first_page, created in ((near_day, COMMIT_TIME - 82800 - 300),
                                    (at_bound, LOWER_BOUND - 200)):
            rc, output, _calls = fixture.invoke([{
                "filtered": [page([])],
                "scan": [page(first_page, 101), page([dict(success_a, created_at=iso(created))], 101)]}])
            boundary.append((rc, fixture.scan_pages, "Web generator health" in output,
                             "no workflow run registered" in output))
        check("ci/scan-margin-boundary-pinned", boundary, [(0, [1, 2], True, False)] * 2)

        # A committer date in the future is clamped to now before the margin is applied.
        clock = COMMIT_TIME - 2 * MARGIN
        recent = [workflow_run(7400 + index, "completed", "success", "Recent run {}".format(index),
                               "e" * 40, created=clock - 1800 - index)
                  for index in range(100)]
        late_failure = dict(failed_b, created_at=iso(clock - 3600))
        rc, output, _calls = fixture.invoke([{
            "filtered": [page([success_a])], "scan": [page(recent, 101), page([late_failure], 101)]}],
            clock=clock)
        check("ci/scan-future-commit-date-clamped",
              (rc, fixture.scan_pages, "failure" in output), (1, [1, 2], True))

        # A scan that never reaches the bound fails closed after the page limit.
        rc, output, _calls = fixture.invoke([{
            "filtered": [page([success_a])], "scan_every_page": page(other_commits, 100000)}])
        check("ci/scan-unbounded-fail-closed",
              (rc, len(fixture.scan_pages), "not bounded within 50 pages" in output), (2, MAX_PAGES, True))

        # MINOR 1 / MAJOR A: a deletion ahead of the page boundary shifts this commit's run off page 2
        # unseen; total_count falling between the pages is the only trace, and it must fail closed.
        rc, output, calls = fixture.invoke([{
            "filtered": [page([success_a])],
            "scan": [page(other_commits, 150), page(other_commits[:49], 149)]}])
        check("ci/scan-shrinking-listing-fail-closed",
              (rc, calls, "listing shrank during the scan" in output), (2, 2, True))

        # MAJOR B: run 101 green at this SHA in the filtered source but failed at another SHA in the
        # scan. Identities are reconciled BEFORE selecting this commit's runs.
        moved = dict(success_a, head_sha="e" * 40, conclusion="failure")
        identity_conflicts = []
        for polls in ([{"filtered": [page([success_a])], "scan": [page([moved])]}],
                      [{"filtered": [page([success_b])], "scan": [page([moved, success_a])]}]):
            rc, output, _calls = fixture.invoke(polls)
            identity_conflicts.append((rc, "conflicting duplicate workflow-run records" in output))
        check("ci/scan-head-sha-conflict-fail-closed", identity_conflicts, [(2, True)] * 2)

        # Ordering jitter: a page holding SOME runs below the bound does not end the scan; only a page
        # holding nothing but such runs does.
        mixed = other_commits[:99] + [dict(old_commits[0])]
        rc, output, _calls = fixture.invoke([{
            "filtered": [page([success_a])], "scan": [page(mixed, 101), page([failed_b], 101)]}])
        check("ci/scan-mixed-page-continues",
              (rc, fixture.scan_pages, "failure" in output), (1, [1, 2], True))

        # MEDIUM C: an unidentifiable scan record (empty head_sha, bad id, bad created_at) is an API
        # error even beside a valid green run, and never "no run".
        other = workflow_run(9000, "completed", "success", "Other", "e" * 40)
        malformed_scan = []
        for bad in (dict(other, head_sha=""), dict(other, id=0),
                    dict(other, created_at="yesterday")):
            for filtered in ([page([success_a])], [page([])]):
                rc, output, _calls = fixture.invoke([{
                    "filtered": filtered, "scan": [page([success_a, bad])]}])
                malformed_scan.append(
                    (rc, "malformed workflow run record" in output,
                     "no workflow run registered" in output))
        check("ci/scan-malformed-identity-fail-closed", malformed_scan, [(2, True, False)] * 6)

        # A record of this commit's run seen by only one source is validated in full.
        malformed_run_seen = []
        rc, output, _calls = fixture.invoke([{
            "filtered": [page([success_a])], "scan": [page([success_a, dict(success_b, name="")])]}])
        malformed_run_seen.append((rc, "malformed workflow run record" in output))
        rc, output, _calls = fixture.invoke([{
            "filtered": [page([dict(success_a, name="")])], "scan": [page([])]}])
        malformed_run_seen.append((rc, "malformed workflow run record" in output))
        check("ci/single-source-malformed-run-fail-closed", malformed_run_seen, [(2, True)] * 2)

        # Round 2 MAJOR: completeness counts unique run IDs, not rows. A run ID listed twice by one source
        # means offset pagination shifted between page reads and some other run went unseen: a scan page
        # [ok, ok] with total_count 2 must not be green, two copies of another commit's run must not read
        # as "no run", a duplicate on full pages that the age bound ended must not pass, and neither may
        # a duplicate within the filtered source. Each is re-read once in one-shot mode, then exit 2.
        old_tail = other_commits[1:] + [dict(old_commits[0])]
        busy = [workflow_run(8000 + index, "completed", "success", "Busy run {}".format(index),
                             "e" * 40) for index in range(150)]
        duplicates = []
        for polls, source in (
                ([{"filtered": [page([success_a])], "scan": [page([success_a, success_a])]}],
                 "unfiltered"),
                ([{"filtered": [page([])], "scan": [page([other, other])]}], "unfiltered"),
                ([{"filtered": [page([success_a])],
                   "scan": [page(old_tail, 300), page(old_commits, 300)]}], "unfiltered"),
                ([{"filtered": [page([success_a, success_a], 1)], "scan": [page([success_a])]}],
                 "head_sha-filtered"),
                # Round 3: a run created between the two page reads, on the first read and on the
                # re-read alike, repeats runs[99] on page 2. Disclosed availability cost: exit 2.
                ([{"filtered": [page([success_a])],
                   "scan": [page(busy[:100], 150), page(busy[99:], 151)]}], "unfiltered")):
            rc, output, calls = fixture.invoke(polls)
            duplicates.append(
                (rc, calls, "duplicate run id within the {} listing".format(source) in output,
                 "no workflow run registered" in output))
        check("ci/duplicate-run-id-fail-closed", duplicates, [(2, 2, True, False)] * 5)

        # A scan that reached the end of the listing reconciles unique run IDs against total_count even
        # when every page's own count is in range.
        rc, output, _calls = fixture.invoke([{
            "filtered": [page([success_a])], "scan": [page([success_a], 2)]}])
        check("ci/scan-end-unique-count-fail-closed",
              (rc, "incomplete unfiltered workflow-runs listing" in output), (2, True))

        # Round 2 MEDIUM: every scan page's total_count must cover the records up to and including it,
        # full or short and whatever ended the scan: a single full page of runs older than the bound with
        # total_count 0, a full second page below 200, and a full first page below 100 before a
        # consistent short page. A rising count over full pages ended by the bound stays green.
        overruns = []
        for scan in ([page(old_commits, 0)],
                     [page(other_commits, 101), page(old_commits, 150)],
                     [page(other_commits, 50), page([success_a], 101)]):
            rc, output, _calls = fixture.invoke([{"filtered": [page([success_a])], "scan": scan}])
            overruns.append((rc, "incomplete unfiltered workflow-runs listing" in output))
        rc, _output, _calls = fixture.invoke([{
            "filtered": [page([success_a])],
            "scan": [page(other_commits, 150), page(old_commits, 250)]}])
        overruns.append((rc, fixture.scan_pages))
        check("ci/scan-page-count-below-position-fail-closed", overruns,
              [(2, True)] * 3 + [(0, [1, 2])])

        # A short page before the last is incomplete even when every total_count covers its position
        # and the page after it ends the scan at the age bound. The script's loop stops at any short
        # page, so only the verdict program can be handed this shape: it is checked there directly.
        if program is None:
            harness_error("ci/scan-short-middle-page-fail-closed", "no jq program was extracted")
        else:
            try:
                short_middle = run_jq(program, [page([success_a])], head_sha,
                                      [page(other_commits[:50], 300), page(old_commits, 300)])
                short_middle_result = (
                    short_middle.returncode != 0
                    and "incomplete unfiltered workflow-runs listing" in short_middle.stderr)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                short_middle_result = "jq-filter setup failed: " + _safe_text(str, exc)
            check("ci/scan-short-middle-page-fail-closed", short_middle_result, True)

        # Round 2 MINOR: created_at must be calendar-valid (formats back to the same string). February
        # 31st matches the pattern and parses, so only the round trip rejects it; an hour of 24 is
        # refused by the parser itself. A null created_at is caught only by the type test: the round
        # trip's catch yields null, which equals it, and a short page never reaches the stop rule.
        bad_dates = []
        for created_at in ("2026-02-31T00:00:00Z", "2026-01-01T24:00:00Z", None):
            for filtered in ([page([success_a])], [page([])]):
                rc, output, _calls = fixture.invoke([{
                    "filtered": filtered,
                    "scan": [page([success_a, dict(other, created_at=created_at)])]}])
                bad_dates.append(
                    (rc, "malformed workflow run record" in output,
                     "no workflow run registered" in output))
        check("ci/scan-created-at-calendar-valid", bad_dates, [(2, True, False)] * 6)

        # A failure seen ONLY by the head_sha query is still reported.
        rc, output, _calls = fixture.invoke([{
            "filtered": [page([success_a, failed_b])], "scan": [page([success_a])]}])
        check("ci/filtered-only-failure-reported",
              (rc, "Repository quality checks" in output and "failure" in output), (1, True))

        if program is None:
            harness_error("ci/jq-step-direct-cases", "no jq program was extracted")
        else:
            try:
                steps = (
                    run_jq(program, page([success_a]), head_sha, mode="step"),
                    run_jq(program, page(other_commits, 300), head_sha, mode="step"),
                    run_jq(program, page(old_commits, 300), head_sha, mode="step"),
                    run_jq(program, page([dict(other, head_sha="")]), head_sha, mode="step"),
                    run_jq(program, [page([success_a])], head_sha,
                           [page(other_commits, 100)] * (MAX_PAGES + 1)),
                    run_jq(program, [page([success_a])], head_sha,
                           [page([success_a, dict(other, head_sha="")])]),
                    run_jq(program, [page([success_a])], head_sha,
                           [page(other_commits[:50], 150), page(old_commits, 150)]),
                    run_jq(program, page([dict(other, created_at="2026-02-31T00:00:00Z")]), head_sha,
                           mode="step"),
                )
                step_result = (
                    (steps[0].returncode, steps[0].stdout.split("\n")[0]),
                    (steps[1].returncode, steps[1].stdout.split("\n")[0]),
                    (steps[2].returncode, steps[2].stdout.split("\n")[0]),
                    steps[3].returncode != 0 and "malformed workflow run record" in steps[3].stderr,
                    steps[4].returncode != 0 and "not bounded within the page limit" in steps[4].stderr,
                    steps[5].returncode != 0 and "malformed workflow run record" in steps[5].stderr,
                    steps[6].returncode != 0
                    and "incomplete unfiltered workflow-runs listing" in steps[6].stderr,
                    steps[7].returncode != 0 and "malformed workflow run record" in steps[7].stderr,
                )
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                step_result = "jq-step setup failed: " + _safe_text(str, exc)
            check("ci/jq-step-direct-cases", step_result,
                  ((0, "stop"), (0, "more"), (0, "stop"), True, True, True, True, True))

    # Round 7: main() must print every collected failure and map the exit code by the module docstring's
    # rule on every path. Each case runs a nested main() on seeded state and restores the real state.
    # Round 8: an exception or a stream that raises while being reported (SystemExit included) is
    # contained and recorded, and a duplicate id whose assertions pass still exits 2.
    # Round 9: a fault on one stream does not stop the other stream's output, no unsuccessful case
    # leaves an unflagged PASS line, and the os._exit() ending runs in a child whose stdout cannot
    # flush.
    # Round 10: each result line's print and flush is contained on its own, and each containment has a
    # case that fails without it; a PASS write cut off mid-line is followed by the void line on a line of
    # its own, read line by line.
    _finalise_check(*_finalisation_cases())


def _finalise_check(got, want):
    # The one check() call site the finalisation cases also drive, inside their nested main(), so a
    # seeded duplicate goes through the real check() without a second source id for the static layer.
    check("ci/finalise-harness-error-keeps-failures", got, want)


def main(report_path=None, run_checks=_run_checks):
    """Run the checks and the reconciliation, then return the exit code by the module docstring's rule.

    An exception or SystemExit that ends the try block, raised anywhere including inside check(), is
    recorded as a harness error; when the try block finishes normally, ending it records nothing beyond
    what the checks and the reconciliation recorded. _report() then prints the result with each print
    and flush of a result line contained on its own, every failure recorded and none raised. The last
    statement computes the code from the records alone: any assertion failure -> 1; else any harness
    error, an uncaught exception or SystemExit included -> 2; else 0. run_checks is replaced only by
    _finalisation_cases().
    """
    try:
        run_checks()
        _reconcile(report_path)
    except BaseException as exc:  # SystemExit and KeyboardInterrupt too: none may skip the report
        _record_uncaught(exc)
    _contained(_report)
    return _exit_code()


def _record_uncaught(exc):
    # Recorded BEFORE any exception text is formatted: str(), repr() and the traceback run the
    # exception's own code, which may raise anything, SystemExit included.
    name = _type_name(exc)
    HARNESS_ERRORS.append("uncaught " + name)
    _contained(_describe_uncaught, name, exc)


def _describe_uncaught(name, exc):
    text = _safe_text(str, exc)
    if text.startswith("<unprintable "):
        text = _safe_text(repr, exc)
    print("SELF-TEST HARNESS ERROR: uncaught {}: {}".format(name, text), file=sys.stderr)
    traceback.print_exception(exc, file=sys.stderr)


def _reconcile(report_path):
    # Only a run in which every check was evaluated is reconciled: a check that recorded a harness error
    # never executed, and would also be reported missing. A failed report write does not stop it.
    reconcile = not HARNESS_ERRORS
    _write_report(report_path)
    if not reconcile:
        return
    expected = _expected_check_ids()
    if expected is None:
        return
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))


_PASS_VOID = ("SELF-TEST HARNESS ERROR: the run failed while or after reporting PASS; any PASS line "
              "above is void (cannot evaluate)")
# A line break in the same write as the stdout void text, so that text starts a line of its own even
# when the PASS write stopped mid-line.
_PASS_VOID_STDOUT = "\n" + _PASS_VOID


def _report():
    # Prints the result and computes nothing. Each print and flush is contained on its own, so a fault on
    # one stream does not stop the other stream's output, and a result that cannot be delivered is
    # recorded as a harness error before _exit_code() runs. Output the checks left pending is flushed
    # first: a failure there still turns the verdict, so it runs before the verdict is read.
    _contained(_flush, sys.stdout)
    _contained(_flush, sys.stderr)
    if not FAILURES and not HARNESS_ERRORS:
        _report_pass()
        return
    if FAILURES:
        _contained(print, "SELF-TEST FAIL:")
        for failure in FAILURES:  # each line on its own: a refused line does not stop the next one
            _contained(print, "  - " + failure)
        _contained(_flush, sys.stdout)
        if HARNESS_ERRORS:
            _contained(print, "SELF-TEST HARNESS ERROR: see the labelled errors above; the assertion "
                       "failures make this run a FAIL", file=sys.stderr)
    else:
        _contained(print, "SELF-TEST HARNESS ERROR: see the labelled errors above (cannot evaluate)",
                   file=sys.stderr)
    _contained(_flush, sys.stderr)


def _report_pass():
    # Nothing is recorded yet, and the PASS line's own print and flush are the last steps that can still
    # turn the verdict. If either fails, the run exits 2 and the PASS text may already be out, whole or
    # cut off mid-line, so a void line follows it, best effort, on both streams; on stdout it starts
    # with a line break.
    _contained(print, "SELF-TEST PASS: {} unique checks executed; execution set reconciled against "
               "tools/selftest_checks.toml".format(len(EXECUTED)))
    _contained(_flush, sys.stdout)
    if HARNESS_ERRORS:
        _contained(print, _PASS_VOID_STDOUT)
        _contained(_flush, sys.stdout)
        _contained(print, _PASS_VOID, file=sys.stderr)
        _contained(_flush, sys.stderr)


def _flush(stream):
    if stream is not None:  # a stream closed before the run started is None and has nothing to flush
        stream.flush()


def _exit_code():
    # The single exit-code rule, read from the records only.
    if FAILURES:
        return 1
    if HARNESS_ERRORS:
        return 2
    return 0


class _ExitOnCompare:
    """A got value whose comparison raises SystemExit(0): a SystemExit raised from inside check()."""

    def __ne__(self, other):
        raise SystemExit(0)


class _ExitOnText:
    """An exception argument whose str() and repr() raise SystemExit(0)."""

    def __str__(self):
        raise SystemExit(0)

    __repr__ = __str__


class _UnprintableError(Exception):
    """An exception whose str() raises ValueError; its repr() still works."""

    def __str__(self):
        raise ValueError("seeded formatting fault")


class _UnrepresentableValue:
    """A failing got value whose repr() raises SystemExit(0)."""

    def __repr__(self):
        raise SystemExit(0)


class _ExitOnWrite(io.StringIO):
    """An output stream whose every write raises SystemExit(0)."""

    def write(self, text):
        raise SystemExit(0)


class _FlushFault(io.StringIO):
    """An output stream that keeps what is written and whose flush() raises SystemExit(0) while anything
    is pending: a buffered stream whose delivery fails. Built with pending=True, every flush raises."""

    def __init__(self, pending=False):
        super().__init__()
        self.pending = pending

    def write(self, text):
        self.pending = True
        return super().write(text)

    def flush(self):
        if self.pending:
            raise SystemExit(0)


class _SeededStream(io.StringIO):
    """An output stream with seeded faults, each raising SystemExit(0). A write whose text contains
    refuse raises and keeps nothing; with times set, only the first times such writes do. A buffered
    stream holds written text until flush() delivers it, and getvalue() returns the delivered text only:
    the first flush_faults flushes with text held raise and deliver nothing, keeping it held."""

    def __init__(self, refuse=None, times=None, buffered=False, flush_faults=0):
        super().__init__()
        self.refuse, self.times, self.buffered, self.flush_faults = refuse, times, buffered, flush_faults
        self.held = []

    def write(self, text):
        if self.refuse is not None and self.refuse in text and self.times != 0:
            if self.times is not None:
                self.times -= 1
            raise SystemExit(0)
        if self.buffered:
            self.held.append(text)
            return len(text)
        return super().write(text)

    def flush(self):
        if self.held and self.flush_faults:
            self.flush_faults -= 1
            raise SystemExit(0)
        super().write("".join(self.held))
        self.held.clear()


class _FaultingPath:
    """A manifest path whose os.fspath() raises: an uncaught exception during the reconciliation."""

    def __fspath__(self):
        raise RuntimeError("seeded reconciliation fault")


class _UnprintablePath:
    """A manifest path that names a missing file and whose str() raises SystemExit(0)."""

    def __init__(self, path):
        self.path = path

    def __fspath__(self):
        return self.path

    def __str__(self):
        raise SystemExit(0)


class _NamelessType(type):
    """A metaclass whose classes' __name__ raises SystemExit(0)."""

    @property
    def __name__(cls):
        raise SystemExit(0)


class _NamelessError(Exception, metaclass=_NamelessType):
    """An exception whose type name cannot be read."""


# Run by _finalisation_cases() in a child: the real main() and _end(), with a stdout whose writes go
# straight to the pipe and whose every flush raises once anything has been written, as the flush of a
# buffered stream whose delivery fails does.
_END_DRIVER = """import os
import sys

sys.path.insert(0, sys.argv[1])
import selftest_ci_status as selftest


class Output:
    pending = False

    def write(self, text):
        self.pending = True
        os.write(1, text.encode("utf-8"))
        return len(text)

    def flush(self):
        if self.pending:
            raise OSError(5, "seeded flush fault")


selftest.CHECKS_MANIFEST = sys.argv[2]
sys.stdout = Output()
selftest._end(selftest.main(None, lambda: selftest._finalise_check(0, 0)))
"""


def _pass_state(text):
    # Read line by line, as a reader of stdout would: "none": no line holds PASS text; "void": a later
    # line than the last one that does is exactly the void line; "pass": no such line follows it.
    lines = text.split("\n")
    marks = [at for at, line in enumerate(lines) if "SELF-TEST PASS" in line]
    if not marks:
        return "none"
    return "void" if _PASS_VOID in lines[marks[-1] + 1:] else "pass"


def _finalisation_cases():
    """Run a nested main() on seeded state per case.

    Return (results, wants): per case, the exit code, the expected stdout and stderr texts that are
    absent, the stdout PASS state (see _pass_state) and whether stderr holds a PASS text, against the
    case's code, nothing absent, its PASS state and False. A buffered stream's text is what its flushes
    delivered. Every unsuccessful case expects no PASS text, or one followed by the void line where the
    PASS line's own delivery fails. The last case runs the real main() and _end() in a child whose
    stdout cannot flush.
    """
    global CHECKS_MANIFEST
    saved = (list(FAILURES), list(HARNESS_ERRORS), list(EXECUTED), set(_EXECUTED_SET), CHECKS_MANIFEST)
    own = "ci/finalise-harness-error-keeps-failures"
    seeded = "seeded/assertion: got 1, want 2"
    duplicate = "{}: got 'duplicate', want 'wanted'".format(own)

    def passes():
        _finalise_check(0, 0)

    def duplicated():
        _finalise_check(0, 0)
        _finalise_check("duplicate", "wanted")

    def duplicated_passing():
        _finalise_check(0, 0)
        _finalise_check(0, 0)

    def unrepresentable():
        _finalise_check(_UnrepresentableValue(), 0)

    def recorded():
        _finalise_check(0, 0)
        harness_error("seeded/not-evaluated", "seeded harness error")

    def exits():
        _finalise_check(_ExitOnCompare(), 0)

    def raises():
        _finalise_check(0, 0)
        raise subprocess.TimeoutExpired("fixture", 30)

    def exits_on_text():
        _finalise_check(0, 0)
        raise RuntimeError(_ExitOnText())

    def unprintable():
        _finalise_check(0, 0)
        raise _UnprintableError()

    def nameless():
        _finalise_check(0, 0)
        raise _NamelessError()

    def noisy_out():
        _finalise_check(0, 0)
        print("seeded check output")

    def noisy_err():
        _finalise_check(0, 0)
        print("seeded check output", file=sys.stderr)

    faults = {"write": _ExitOnWrite, "flush": _FlushFault, "flush-always": lambda: _FlushFault(True),
              "closed": lambda: None,
              "newline-once": lambda: _SeededStream(refuse="\n", times=1),
              "refuse-header": lambda: _SeededStream(refuse="SELF-TEST FAIL"),
              "refuse-seeded": lambda: _SeededStream(refuse="  - " + seeded),
              "refuse-summary-buffered": lambda: _SeededStream(refuse="see the labelled errors",
                                                               buffered=True),
              "refuse-void-buffered": lambda: _SeededStream(refuse=_PASS_VOID, buffered=True),
              "buffered": lambda: _SeededStream(buffered=True),
              "buffered-flush-once": lambda: _SeededStream(buffered=True, flush_faults=1)}
    void = [_PASS_VOID]
    failed = "diagnostic output failed: SystemExit"
    both_fail = "the assertion failures make this run a FAIL"

    results, wants = [], []
    try:
        with tempfile.TemporaryDirectory(prefix="ci-status-finalise-") as raw:
            base = Path(raw)

            def manifest(name, ids=None, text=None):
                path = base / name
                if text is None:
                    text = "[[suite]]\nid = {}\nexpected-check-ids = {}\n".format(
                        json.dumps(SUITE_ID), json.dumps(ids))
                path.write_text(text, encoding="utf-8")
                return path

            own_only = manifest("own.toml", [own])
            never_run = manifest("never-run.toml", [own, "seeded/never-run"])
            not_evaluated = manifest("not-evaluated.toml", [own, "seeded/not-evaluated"])
            scalar_suite = manifest("scalar-suite.toml", text="suite = 1\n")
            absent = base / "absent" / "selftest_checks.toml"
            bad_report = str(base / "absent" / "report.json")
            exit_text = "uncaught RuntimeError: <unprintable RuntimeError>"
            value_text = "uncaught _UnprintableError: _UnprintableError()"
            unprintable_path = _UnprintablePath(str(absent))
            # (seed a failure first?, checks run, report path, manifest, code, stdout texts, stderr texts,
            # and optionally "stream:fault" from faults, several joined by "+", then the stdout PASS state
            # when not the default: "pass" for code 0, else "none")
            for fail, run, report, path, code, out_texts, err_texts, *faulting in (
                    (False, passes, None, own_only, 0, ["SELF-TEST PASS"], []),
                    (True, passes, None, own_only, 1, [seeded], []),
                    (True, recorded, None, own_only, 1, [seeded], ["seeded/not-evaluated"]),
                    (False, recorded, None, not_evaluated, 2, [], ["seeded/not-evaluated"]),
                    (True, duplicated, None, own_only, 1, [seeded, duplicate],
                     [own + ": duplicate check id"]),
                    (False, duplicated_passing, None, own_only, 2, [], [own + ": duplicate check id"]),
                    (False, unrepresentable, None, own_only, 1,
                     [own + ": got <unprintable _UnrepresentableValue>, want 0"], []),
                    (False, passes, bad_report, never_run, 1, ["execution-set/missing: seeded/never-run"],
                     ["cannot write execution report"]),
                    (False, passes, bad_report, own_only, 2, [], ["cannot write execution report"]),
                    (True, passes, None, absent, 1, [seeded], ["cannot read"]),
                    (False, passes, None, absent, 2, [], ["cannot read"]),
                    (True, passes, None, scalar_suite, 1, [seeded], ["missing or malformed suite"]),
                    (False, passes, None, scalar_suite, 2, [], ["missing or malformed suite"]),
                    (True, exits, None, own_only, 1, [seeded], ["uncaught SystemExit: 0"]),
                    (False, exits, None, own_only, 2, [], ["uncaught SystemExit: 0"]),
                    (True, raises, None, own_only, 1, [seeded], ["uncaught TimeoutExpired"]),
                    (True, passes, None, _FaultingPath(), 1, [seeded], ["uncaught RuntimeError"]),
                    (False, passes, None, _FaultingPath(), 2, [], ["uncaught RuntimeError"]),
                    (True, exits_on_text, None, own_only, 1, [seeded], [exit_text]),
                    (False, exits_on_text, None, own_only, 2, [], [exit_text]),
                    (True, unprintable, None, own_only, 1, [seeded], [value_text]),
                    (False, unprintable, None, own_only, 2, [], [value_text]),
                    # The PASS print fails: the void line still reaches stderr.
                    (False, passes, None, own_only, 2, [], [failed] + void, "stdout:write"),
                    (True, passes, None, own_only, 1, [], [failed], "stdout:write"),
                    (True, recorded, None, own_only, 1, [], ["seeded/not-evaluated", both_fail],
                     "stdout:write"),
                    (False, raises, None, own_only, 2, [], [], "stderr:write"),
                    (False, duplicated, None, own_only, 1, [duplicate], [], "stderr:write"),
                    (False, nameless, None, own_only, 2, [], ["uncaught <unnamed type>"]),
                    (False, passes, None, unprintable_path, 2, [],
                     ["cannot read <unprintable _UnprintablePath>: [Errno 2]"]),
                    # The PASS line's own flush fails: exit 2, and the void line follows the PASS text.
                    (False, passes, None, own_only, 2, void, [failed] + void, "stdout:flush", "void"),
                    # Output the checks left pending cannot be flushed: no PASS line at all.
                    (False, noisy_out, None, own_only, 2, [], [failed, "(cannot evaluate)"],
                     "stdout:flush"),
                    # The last stderr flush is attempted too: its failure follows the summary line.
                    (False, noisy_err, None, own_only, 2, [],
                     ["(cannot evaluate)\nSELF-TEST HARNESS ERROR: " + failed], "stderr:flush"),
                    # The failure list cannot flush: the stderr summary line still follows.
                    (True, passes, None, own_only, 1, [seeded], [failed, both_fail], "stdout:flush"),
                    # A failing stderr flush, pending or not, does not stop the failure list on stdout.
                    (True, duplicated, None, own_only, 1, [seeded, duplicate],
                     [own + ": duplicate check id", both_fail], "stderr:flush"),
                    (True, passes, None, own_only, 1, [seeded], [failed], "stderr:flush-always"),
                    # A stdout closed before the run discards the PASS line; the code is still 0.
                    (False, passes, None, own_only, 0, [], [], "stdout:closed", "none"),
                    # The PASS line's line break is refused after its text was written: the void line
                    # still starts a line of its own.
                    (False, passes, None, own_only, 2, [], [failed] + void, "stdout:newline-once", "void"),
                    # A refused FAIL header or failure line does not stop the failure lines after it.
                    (True, passes, None, own_only, 1, ["  - " + seeded], [failed, both_fail],
                     "stdout:refuse-header"),
                    (True, duplicated, None, own_only, 1, ["SELF-TEST FAIL:\n", duplicate],
                     [failed, both_fail], "stdout:refuse-seeded"),
                    # A refused stderr summary line does not stop the last stderr flush delivering the
                    # diagnostic that records it, in either branch.
                    (True, recorded, None, own_only, 1, [seeded], ["seeded/not-evaluated", failed],
                     "stderr:refuse-summary-buffered"),
                    (False, recorded, None, not_evaluated, 2, [], ["seeded/not-evaluated", failed],
                     "stderr:refuse-summary-buffered"),
                    # Buffered streams: each result line reaches the reader only through its flush.
                    (False, passes, None, own_only, 0, ["SELF-TEST PASS"], [], "stdout:buffered"),
                    (True, passes, None, own_only, 1, ["SELF-TEST FAIL:\n  - " + seeded], [],
                     "stdout:buffered"),
                    (False, recorded, None, not_evaluated, 2, [], ["(cannot evaluate)"],
                     "stderr:buffered"),
                    (True, recorded, None, own_only, 1, [seeded], [both_fail], "stderr:buffered"),
                    # The PASS flush fails once: the void line's own flush delivers both lines.
                    (False, passes, None, own_only, 2, void, [failed] + void, "stdout:buffered-flush-once",
                     "void"),
                    # The stderr void line reaches the reader only through the last stderr flush.
                    (False, passes, None, own_only, 2, [], [failed] + void,
                     "stdout:write+stderr:buffered"),
                    # A refused stderr void line does not stop the last stderr flush.
                    (False, passes, None, own_only, 2, void, [failed],
                     "stdout:flush+stderr:refuse-void-buffered", "void")):
                FAILURES[:] = [seeded] if fail else []
                HARNESS_ERRORS[:] = []
                EXECUTED[:] = []
                _EXECUTED_SET.clear()
                CHECKS_MANIFEST = path
                streams = {"stdout": io.StringIO(), "stderr": io.StringIO()}
                if faulting:
                    for spec in faulting[0].split("+"):
                        stream, _, fault = spec.partition(":")
                        streams[stream] = faults[fault]()
                out, err = streams["stdout"], streams["stderr"]
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        got = main(report, run)
                    except BaseException as exc:  # anything escaping main() is a failure here
                        got = ("escaped", type(exc).__name__)
                out_text = "" if out is None else out.getvalue()
                err_text = "" if err is None else err.getvalue()
                results.append((got, [text for text in out_texts if text not in out_text],
                                [text for text in err_texts if text not in err_text],
                                _pass_state(out_text), "SELF-TEST PASS" in err_text))
                state = faulting[1] if len(faulting) > 1 else "pass" if code == 0 else "none"
                wants.append((code, [], [], state, False))

            # The os._exit() ending: a child runs the real main() and _end() on a passing run whose stdout
            # cannot flush. The exit-time flush of sys.exit() would end it with 120 instead of 2.
            driver = base / "end_driver.py"
            driver.write_text(_END_DRIVER, encoding="utf-8")
            child = subprocess.run(
                [sys.executable, "-I", "-B", str(driver), str(ROOT / "tools"), str(own_only)],
                capture_output=True, text=True, env={"PATH": SYSTEM_PATH}, timeout=120, check=False)
            results.append((child.returncode, [text for text in void if text not in child.stdout],
                            [text for text in ["diagnostic output failed: OSError"]
                             if text not in child.stderr],
                            _pass_state(child.stdout), "SELF-TEST PASS" in child.stderr))
            wants.append((2, [], [], "void", False))
    finally:
        FAILURES[:], HARNESS_ERRORS[:], EXECUTED[:] = saved[0], saved[1], saved[2]
        _EXECUTED_SET.clear()
        _EXECUTED_SET.update(saved[3])
        CHECKS_MANIFEST = saved[4]
    return results, wants


def _parse_argv(argv):
    if not argv:
        return None
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return argv[1]
    print("usage: selftest_ci_status.py [--execution-report ABS_PATH] "
          "(the report path must be absolute)", file=sys.stderr)
    sys.exit(2)


def _end(code):
    # os._exit: _report() has flushed both streams or recorded each flush that failed, and the
    # interpreter's exit-time flush must not replace the code main() computed (a failing final flush
    # would otherwise end the run with 120). _finalisation_cases() runs this ending in a child whose
    # stdout cannot flush.
    os._exit(code)


if __name__ == "__main__":
    _end(main(_parse_argv(sys.argv[1:])))
