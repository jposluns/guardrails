#!/usr/bin/env python3
"""Behavioural self-test for tools/ci-status.sh.

Every case runs the real script in a throwaway git repository with controlled gh, date, and sleep
executables. The gh fixture emits paginated JSON for the script's real jq executable, and a separate check
extracts that expression from ci-status.sh and exercises it directly against crafted JSON. Verdicts
use the child's return code and complete captured output, never a success token.

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
SYSTEM_PATH = "/usr/bin:/bin"
GIT = "/usr/bin/git"
JQ = "/usr/bin/jq"
SCRIPT = ROOT / "tools" / "ci-status.sh"
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
SUITE_ID = "ci-status-behaviour-selftest"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()

FAKE_GH = r"""import json
import os
import sys

args = sys.argv[1:]
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
if not isinstance(poll, list) or not poll:
    print("gh fixture: malformed poll", file=sys.stderr)
    sys.exit(1)

endpoint = next((arg for arg in args if arg.startswith("repos/")), "")
if "?head_sha=" not in endpoint or "&per_page=100" not in endpoint:
    print("gh fixture: query must request head_sha with per_page=100", file=sys.stderr)
    sys.exit(1)
pages = poll if "--paginate" in args else poll[:1]
payload = pages if "--slurp" in args else pages[0]
print(json.dumps(payload))
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


def workflow_run(run_id, status, conclusion, name, head_sha):
    return {
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


def jq_program():
    source = SCRIPT.read_text(encoding="utf-8")
    prefix = '      jq --arg requested_sha "$SHA" -r \'\n'
    suffix = "'\n  } 2>&1\n}"
    if source.count(prefix) != 1:
        raise ValueError("ci-status.sh must contain exactly one jq program")
    start = source.index(prefix) + len(prefix)
    end = source.index(suffix, start)
    return source[start:end]


def run_jq(program, pages, requested_sha):
    return subprocess.run(
        [JQ, "--arg", "requested_sha", requested_sha, "-r", program],
        input=json.dumps(pages), text=True,
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
        subprocess.run([GIT, "init", "-q", "-b", "main", str(self.repo)],
                       check=True, capture_output=True, timeout=30, env=self.base_env)
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run([GIT, "-C", str(self.repo), "add", "seed.txt"],
                       check=True, capture_output=True, timeout=30, env=self.base_env)
        subprocess.run(
            [GIT, "-C", str(self.repo), "-c", "user.name=Selftest",
             "-c", "user.email=selftest@example.invalid", "-c", "commit.gpgsign=false",
             "commit", "-q", "-m", "seed"],
            check=True, capture_output=True, timeout=30, env=self.base_env)
        result = subprocess.run(
            [GIT, "-C", str(self.repo), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=30, env=self.base_env,
        )
        self.head_sha = result.stdout.strip()
        self.response = base / "responses.json"
        self.counter = base / "calls.txt"
        self.clock = base / "clock.txt"
        for name, body in (("gh", FAKE_GH), ("date", FAKE_DATE), ("sleep", FAKE_SLEEP)):
            executable = self.bin / name
            executable.write_text("#!{}\n{}".format(sys.executable, body), encoding="utf-8")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    def invoke(self, polls, wait=False, timeout="900"):
        self.response.write_text(json.dumps({"polls": polls}), encoding="utf-8")
        self.counter.write_text("0\n", encoding="utf-8")
        self.clock.write_text("100000\n", encoding="utf-8")
        env = dict(self.base_env)
        env["PATH"] = str(self.bin) + os.pathsep + SYSTEM_PATH
        env["MOCK_GH_RESPONSE"] = str(self.response)
        env["MOCK_GH_COUNTER"] = str(self.counter)
        env["MOCK_CLOCK"] = str(self.clock)
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
        try:
            program = jq_program()
            conflict = run_jq(program, [page([success_a, conflict_a], 1)], head_sha)
            mismatched = run_jq(program, [page([success_a], 2)], head_sha)
            differing = run_jq(
                program, [page([success_a], 1), page([success_a], 2)], head_sha)
            successful = run_jq(program, [page([success_b, success_a], 2)], head_sha)
            too_many = run_jq(program, [page(overfull, 101)], head_sha)
            wrong_target = run_jq(program, [page([wrong_head], 1)], head_sha)
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
            )
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            direct_result = "jq-filter setup failed: {}".format(exc)
        check("ci/jq-filter-direct-cases", direct_result,
              (True, True, True, True, True, True))

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
