#!/usr/bin/env python3
"""Behavioural self-test for tools/ci-status.sh.

Every case runs the real script in a throwaway git repository with controlled gh, date, and sleep
executables. The gh fixture emits paginated JSON for the script's real jq executable, separately for the
head_sha-filtered query and the unfiltered run listing, and a separate check extracts that expression
from ci-status.sh and exercises it directly against crafted JSON. Verdicts use the child's return code
and complete captured output, never a success token.

  selftest_ci_status.py                              exit 0 on self-test pass, 1 on assertion failure
  selftest_ci_status.py --execution-report ABS_PATH  also write the executed check IDs as JSON

Exit 2 is a harness/setup error, including bad arguments, a failed report write, an unreadable,
malformed, or suite-missing expectation manifest, or a ci-status.sh whose jq program cannot be extracted.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: selftest_ci_status.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import json
import os
import stat
import subprocess
import tempfile
import time
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
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def harness_error(check_id, detail):
    message = "{}: not evaluated: {}".format(check_id, detail)
    HARNESS_ERRORS.append(message)
    print("SELF-TEST HARNESS ERROR: " + message, file=sys.stderr)


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
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc),
              file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(
        SUITE_ID, CHECKS_MANIFEST), file=sys.stderr)
    return None


def _write_report(report_path):
    if report_path is None:
        return True
    try:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump({"format_version": 1, "suite": SUITE_ID, "check_ids": EXECUTED}, handle)
            handle.write("\n")
    except OSError as exc:
        print("SELF-TEST HARNESS ERROR: cannot write execution report {}: {}".format(
            report_path, exc), file=sys.stderr)
        return False
    return True


def main(report_path=None):
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
            direct_result = "jq-filter setup failed: {}".format(exc)
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
                short_middle_result = "jq-filter setup failed: {}".format(exc)
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
                step_result = "jq-step setup failed: {}".format(exc)
            check("ci/jq-step-direct-cases", step_result,
                  ((0, "stop"), (0, "more"), (0, "stop"), True, True, True, True, True))

    if not _write_report(report_path):
        return 2
    if HARNESS_ERRORS:
        print("SELF-TEST HARNESS ERROR: {} check(s) not evaluated; see the labelled errors above "
              "(cannot evaluate)".format(len(HARNESS_ERRORS)), file=sys.stderr)
        return 2
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} unique checks executed; execution set reconciled against "
          "tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


def _parse_argv(argv):
    if not argv:
        return None
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return argv[1]
    print("usage: selftest_ci_status.py [--execution-report ABS_PATH] "
          "(the report path must be absolute)", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    sys.exit(main(_parse_argv(sys.argv[1:])))
