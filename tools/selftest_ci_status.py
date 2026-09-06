#!/usr/bin/env python3
"""Behavioural self-test for tools/ci-status.sh.

Every case runs the real script in a throwaway git repository with controlled gh and sleep executables.
The gh fixture emulates the pre-GD-162 .workflow_runs[0] expression as well as the paginated TSV contract,
so the multi-run, late-created-run, failed-second-run, and second-page cases discriminate against the old
implementation. Verdicts use the child's return code and complete captured output, never a success token.

  selftest_ci_status.py                              exit 0 on self-test pass, 1 on assertion failure
  selftest_ci_status.py --execution-report ABS_PATH  also write the executed check IDs as JSON

Exit 2 is a harness/setup error, including bad arguments, a failed report write, or an unreadable,
malformed, or suite-missing expectation manifest.
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    sys.exit("error: selftest_ci_status.py requires Python 3.11+ (tomllib).")

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "ci-status.sh"
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
SUITE_ID = "ci-status-behaviour-selftest"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()

FAKE_GH = r"""#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
try:
    jq_index = args.index("--jq")
    expression = args[jq_index + 1]
except (ValueError, IndexError):
    print("gh fixture: missing --jq expression", file=sys.stderr)
    sys.exit(1)

with open(os.environ["MOCK_GH_COUNTER"], "r", encoding="utf-8") as handle:
    call = int(handle.read().strip())
call += 1
with open(os.environ["MOCK_GH_COUNTER"], "w", encoding="utf-8") as handle:
    handle.write(str(call) + "\n")
with open(os.environ["MOCK_GH_RESPONSE"], "r", encoding="utf-8") as handle:
    polls = json.load(handle)["polls"]
poll = polls[min(call - 1, len(polls) - 1)]
if isinstance(poll, dict) and "error" in poll:
    print(poll["error"], file=sys.stderr)
    sys.exit(poll.get("exit", 1))

pages = poll
old_expression = ".workflow_runs[0]" in expression
if old_expression:
    runs = pages[0]["workflow_runs"]
    if not runs:
        print("__NORUN__")
    else:
        run = runs[0]
        print("{}|{}|{}|{}".format(
            run.get("status"), run.get("conclusion") or "-",
            run.get("name"), run.get("html_url")))
    sys.exit(0)

endpoint = next((arg for arg in args if arg.startswith("repos/")), "")
compact_expression = "".join(expression.split())
tsv_contract = ('[(.status//"-"),(.conclusion//"-"),(.id|tostring),'
                '.name,.html_url]|@tsv')
if ("--paginate" not in args or "--slurp" not in args
        or "&per_page=100" not in endpoint or "?head_sha=" not in endpoint
        or tsv_contract not in compact_expression):
    print("gh fixture: query must paginate and emit the status-first TSV contract", file=sys.stderr)
    sys.exit(1)
if not isinstance(pages, list) or not pages:
    print("gh fixture: malformed workflow-runs response", file=sys.stderr)
    sys.exit(1)
totals = []
all_runs = []
for page in pages:
    if (not isinstance(page, dict) or type(page.get("total_count")) is not int
            or page["total_count"] < 0 or not isinstance(page.get("workflow_runs"), list)):
        print("gh fixture: malformed workflow-runs response", file=sys.stderr)
        sys.exit(1)
    totals.append(page["total_count"])
    all_runs.extend(page["workflow_runs"])
if len(set(totals)) != 1:
    print("gh fixture: inconsistent paginated workflow-runs snapshot", file=sys.stderr)
    sys.exit(1)

unique = {}
for run in all_runs:
    if (not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] <= 0
            or (run.get("status") is not None
                and (not isinstance(run["status"], str) or not run["status"]))
            or (run.get("conclusion") is not None
                and (not isinstance(run["conclusion"], str) or not run["conclusion"]))
            or not isinstance(run.get("name"), str) or not run["name"]
            or not isinstance(run.get("html_url"), str) or not run["html_url"]):
        print("gh fixture: malformed workflow run record", file=sys.stderr)
        sys.exit(1)
    unique[run["id"]] = run
if len(unique) != totals[0]:
    print("gh fixture: inconsistent paginated workflow-runs snapshot", file=sys.stderr)
    sys.exit(1)
if not unique:
    print("__NORUN__")
    sys.exit(0)

def escape_tsv(value):
    return (str(value).replace("\\", "\\\\").replace("\t", "\\t")
            .replace("\r", "\\r").replace("\n", "\\n"))

for run_id in sorted(unique):
    run = unique[run_id]
    fields = [run.get("status") or "-", run.get("conclusion") or "-", run_id,
              run["name"], run["html_url"]]
    print("\t".join(escape_tsv(value) for value in fields))
"""

FAKE_SLEEP = """#!/usr/bin/env bash
exit 0
"""


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def workflow_run(run_id, status, conclusion, name):
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "name": name,
        "html_url": "https://example.invalid/runs/{}".format(run_id),
    }


def page(runs, total_count=None):
    return {"total_count": len(runs) if total_count is None else total_count,
            "workflow_runs": runs}


class Fixture:
    def __init__(self, base):
        self.repo = base / "repo"
        self.bin = base / "bin"
        self.repo.mkdir()
        self.bin.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True, timeout=30)
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "seed.txt"],
                       check=True, capture_output=True, timeout=30)
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=Selftest",
             "-c", "user.email=selftest@example.invalid", "-c", "commit.gpgsign=false",
             "commit", "-q", "-m", "seed"],
            check=True, capture_output=True, timeout=30)
        self.response = base / "responses.json"
        self.counter = base / "calls.txt"
        gh = self.bin / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        sleep = self.bin / "sleep"
        sleep.write_text(FAKE_SLEEP, encoding="utf-8")
        sleep.chmod(sleep.stat().st_mode | stat.S_IXUSR)

    def invoke(self, polls, wait=False, timeout="30"):
        self.response.write_text(json.dumps({"polls": polls}), encoding="utf-8")
        self.counter.write_text("0\n", encoding="utf-8")
        env = os.environ.copy()
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["MOCK_GH_RESPONSE"] = str(self.response)
        env["MOCK_GH_COUNTER"] = str(self.counter)
        env["CI_STATUS_REPO"] = "fixture/repository"
        env["CI_STATUS_TIMEOUT"] = timeout
        command = [str(SCRIPT), "HEAD"]
        if wait:
            command.append("--wait")
        result = subprocess.run(command, cwd=self.repo, env=env, text=True,
                                capture_output=True, timeout=30)
        calls = int(self.counter.read_text(encoding="utf-8").strip())
        return result.returncode, result.stdout + result.stderr, calls


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
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
    success_a = workflow_run(101, "completed", "success", "Web generator health")
    pending_b = workflow_run(202, "in_progress", None, "Repository quality checks")
    success_b = workflow_run(202, "completed", "success", "Repository quality checks")
    failed_b = workflow_run(202, "completed", "failure", "Repository quality checks")

    with tempfile.TemporaryDirectory(prefix="ci-status-selftest-") as raw:
        fixture = Fixture(Path(raw))

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
            303, "in_progress", None, "trap|completed|success\tcompleted\tsuccess\nnext")
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

        rc, output, _calls = fixture.invoke(
            [[page([success_a], 2), page([failed_b], 2)]])
        check("ci/pagination-all-runs",
              (rc, "Repository quality checks" in output and "failure" in output), (1, True))

        success_c = workflow_run(303, "completed", "success", "Replacement workflow")
        polls = [[page([success_a])], [page([success_a])]] + [[page([success_c])]] * 5
        rc, _output, calls = fixture.invoke(polls, wait=True)
        check("ci/settle-set-change-resets", (rc, calls), (0, 7))

        queued_a = workflow_run(101, "queued", None, "Web generator health")
        polls = ([[page([success_a])]] * 2 + [[page([queued_a])]]
                 + [[page([success_a])]] * 5)
        rc, _output, calls = fixture.invoke(polls, wait=True)
        check("ci/settle-nonterminal-resets", (rc, calls), (0, 8))

        rc, _output, _calls = fixture.invoke(
            [[page([success_a], 3), page([success_b], 3)]])
        check("ci/pagination-race-not-green", rc, 2)

    if not _write_report(report_path):
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
