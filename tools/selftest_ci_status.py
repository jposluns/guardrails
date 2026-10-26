#!/usr/bin/env python3
"""Behavioural self-test for tools/ci-status.sh.

Every case runs the real script in a throwaway git repository with controlled gh, date, and sleep
executables. The gh fixture emits paginated JSON for the script's real jq executable, separately for the
head_sha-filtered query, the unfiltered run listing, and the --wait pull-request probe's reads, and a
separate check extracts the runs expression from ci-status.sh and exercises it directly against crafted
JSON. Verdicts use the child's return code
and complete captured output, never a success token. The fixture repository's origin remote names the
repository the script must read; the gh fixture records every argv it receives and answers only for the
repository a case serves, so a case can pin which repository was queried, or that gh was never run. A
recording git fixture is always first on PATH: it logs every git argv, then runs the real git, so a
case can pin that option validation refused BEFORE any git read; a case that needs origin output real
git cannot be configured to give has the same fixture answer exactly that query instead. No case reaches the real
GitHub API: gh resolves to the fixture first on a controlled PATH, and the child environment is built
from nothing, so it carries no GH_TOKEN, GITHUB_TOKEN, or gh configuration.

  selftest_ci_status.py                              exit 0 on self-test pass, 1 on assertion failure
  selftest_ci_status.py --execution-report ABS_PATH  also write the executed check IDs as JSON,
                                                     finalized at interpreter exit

Once the checks start, main() ends every run through one rule, whatever ends it: a normal finish, a
recorded harness error, an uncaught exception, or SystemExit raised anywhere, including inside check().
The rule: exit 1 if any assertion failed, else 2 if any harness error was recorded (an uncaught
exception or SystemExit is recorded as one), else 0. Harness errors also include an unreadable or
malformed expectation manifest (a suite container that is not an array of
tables included), a ci-status.sh whose jq program cannot be extracted, a duplicate check id, and a
diagnostic or result that cannot be printed or flushed. The execution set is reconciled against the
manifest only when no check recorded a harness error, because an unevaluated check would also be
reported missing. Bad arguments, a Python older than 3.14 that can start this file, a missing
tomllib, and a refused arming of the execution-report finalizer are refused before any check runs, so
they exit 2. These exits hold while the tool's output (its diagnostic on stderr, and what it prints on
stdout) can be written and flushed. A failing output stream can change the exit status and can lose
output; a separate fix in progress addresses this. An older interpreter that cannot start this file
fails with Python's own error first, and that exit is Python's: 1 for a compile failure, which reads as
an assertion failure, or 2 for an interpreter predating -I when run with it.

Reporting can never escape or change the rule (under --execution-report, a refusal of the report
finalizer, described below, ends the run with 2 after reporting). An uncaught exception is recorded
before its text is rendered, and exception or path text in a diagnostic goes through _safe_text(),
which contains whatever str() or repr() raises (SystemExit included) and falls back to the type name.
Each print and each flush
of a result line (the FAIL header, each failure line, the stderr summary line, the PASS line and each
void line) is contained on its own and a failure is recorded as a harness error, so a fault on one line
or stream does not stop the later lines, on the same stream or the other. The verdict is read only after
both streams are flushed of earlier output; a PASS line is then the last output. The exit code is
computed once, after all reporting. A run without --execution-report ends with os._exit() on that
code, so the interpreter's own exit-time flush cannot replace it. A run with --execution-report ends
through _selftest_exit_report.exit_with() on that code instead (os._exit() would skip the report): at
interpreter exit its finalizer flushes both streams and writes the report, and every refusal of the
finalizer, a stream it cannot flush included, ends the run with 2 and writes no report
(tools/_selftest_exit_report.py), which the execution gate refuses.

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
    import os
    try:
        sys.stderr.write(
            "error: selftest_ci_status.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

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
    import os
    try:
        sys.stderr.write(
            "error: selftest_ci_status.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402

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
# The repository the fixture checkout's origin remote names, and so the one ci-status.sh must read.
FIXTURE_REPO = "fixture/repository"
FIXTURE_ORIGIN = "https://github.com/fixture/repository.git"
CHECKS_MANIFEST = ROOT / "tools" / "selftest_checks.toml"
SUITE_ID = "ci-status-behaviour-selftest"
# The workflow file every read of ci-status.sh requires a completed successful run of.
QUALITY_PATH = ".github/workflows/quality.yml"
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
# poll counter; every unfiltered page number read is appended to the scan log. Every argv is recorded
# first, and only the repository named by MOCK_GH_REPO is answered.
# The pull-request probe's reads are answered from "probes", a list of probe reads advanced by each
# read of the open pull-request listing (the last one repeats): a dict with "listing" (the slurped
# pages) and "records" (each pull request's own record by number; an absent number is Not Found),
# or with "error" (the listing read fails). Optionally, "listing_raw" replaces the listing with text
# served as it is, "raw_records" serves a number's read as the text given (invalid JSON, or two
# documents), and "record_errors" makes a number's read fail with the diagnostic given. The read of
# the pull requests associated with a commit serves "associated" (its records, on one page; none when
# absent), or "associated_raw" as text, or fails with "associated_error".
args = sys.argv[1:]
with open(os.environ["MOCK_GH_ARGV_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")
endpoint = next((arg for arg in args if arg.startswith("repos/")), "")
listing = "repos/" + os.environ["MOCK_GH_REPO"] + "/actions/runs"
scan_prefix = listing + "?per_page=100&page="
pulls = "repos/" + os.environ["MOCK_GH_REPO"] + "/pulls"
commits = "repos/" + os.environ["MOCK_GH_REPO"] + "/commits/"
associated = endpoint.startswith(commits) and endpoint.endswith("/pulls?per_page=100")
if endpoint == pulls + "?state=open&per_page=100" or endpoint.startswith(pulls + "/") or associated:
    paged = "--paginate" in args and "--slurp" in args
    with open(os.environ["MOCK_GH_PROBE_COUNTER"], "r", encoding="utf-8") as handle:
        probe_no = int(handle.read().strip())
    if endpoint == pulls + "?state=open&per_page=100" and paged:
        probe_no += 1
        with open(os.environ["MOCK_GH_PROBE_COUNTER"], "w", encoding="utf-8") as handle:
            handle.write(str(probe_no) + "\n")
    elif not (probe_no > 0 and ((associated and paged)
                                or (endpoint[len(pulls) + 1:].isdigit()
                                    and "--paginate" not in args and "--slurp" not in args))):
        print("gh fixture: pull-request query must list open pull requests with --paginate --slurp, "
              "then list a commit's associated pull requests with --paginate --slurp or read one "
              "listed pull request by number", file=sys.stderr)
        sys.exit(1)
    with open(os.environ["MOCK_GH_RESPONSE"], "r", encoding="utf-8") as handle:
        probes = json.load(handle)["probes"]
    probe = probes[min(probe_no - 1, len(probes) - 1)]
    if "error" in probe:
        print(probe["error"], file=sys.stderr)
        sys.exit(probe.get("exit", 1))
    if associated:
        if "associated_error" in probe:
            print(probe["associated_error"], file=sys.stderr)
            sys.exit(1)
        if "associated_raw" in probe:
            sys.stdout.write(probe["associated_raw"])
        else:
            print(json.dumps([probe.get("associated", [])]))
        sys.exit(0)
    if endpoint == pulls + "?state=open&per_page=100":
        if "listing_raw" in probe:
            sys.stdout.write(probe["listing_raw"])
        else:
            print(json.dumps(probe["listing"]))
        sys.exit(0)
    number = endpoint[len(pulls) + 1:]
    if number in probe.get("record_errors", {}):
        print(probe["record_errors"][number], file=sys.stderr)
        sys.exit(1)
    if number in probe.get("raw_records", {}):
        sys.stdout.write(probe["raw_records"][number])
        sys.exit(0)
    record = probe["records"].get(number)
    if record is None:
        print("gh: Not Found (HTTP 404)", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(record))
    sys.exit(0)
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

# Every sleep is also appended to the sleep log, so a case can pin the spacing of re-reads.
try:
    seconds = int(sys.argv[1])
    with open(os.environ["MOCK_CLOCK"], "r", encoding="utf-8") as handle:
        now = int(handle.read().strip())
    with open(os.environ["MOCK_CLOCK"], "w", encoding="utf-8") as handle:
        handle.write(str(now + seconds) + "\n")
    with open(os.environ["MOCK_SLEEP_LOG"], "a", encoding="utf-8") as handle:
        handle.write(str(seconds) + "\n")
except (IndexError, OSError, ValueError) as exc:
    print("sleep fixture: {}".format(exc), file=sys.stderr)
    sys.exit(1)
"""

FAKE_GIT = r"""import json
import os
import subprocess
import sys

# Always first on PATH through invoke(): every git argv the script runs is recorded, so a case can
# assert that option validation refused BEFORE any git read, exactly as the gh argv log pins the API
# side. For a case that passes git_remote to invoke() it also answers exactly
# `git remote get-url --all origin` with the bytes and exit status the case sets, which real git cannot be
# configured to produce; every other git command runs the real git with the same arguments, standard
# streams, and exit status.
args = sys.argv[1:]
with open(os.environ["MOCK_GIT_ARGV_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\n")
if args == ["remote", "get-url", "--all", "origin"] and "MOCK_GIT_REMOTE_STDOUT" in os.environ:
    sys.stdout.write(os.environ["MOCK_GIT_REMOTE_STDOUT"])
    sys.exit(int(os.environ["MOCK_GIT_REMOTE_RC"]))
sys.exit(subprocess.run([os.environ["MOCK_GIT_REAL"]] + args).returncode)
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


def workflow_run(run_id, status, conclusion, name, head_sha, created=COMMIT_TIME + 600,
                 event=None, pull_requests=None, path=QUALITY_PATH):
    # event and pull_requests appear only when given: without --require-base the script never reads
    # them, and the pre-option fixtures prove that by omitting them entirely. Every read requires a
    # completed successful quality.yml run, so path defaults to that file; path=None omits the key.
    run = {
        "created_at": iso(created),
        "id": run_id,
        "head_sha": head_sha,
        "status": status,
        "conclusion": conclusion,
        "name": name,
        "html_url": "https://example.invalid/runs/{}".format(run_id),
    }
    if event is not None:
        run["event"] = event
    if pull_requests is not None:
        run["pull_requests"] = pull_requests
    if path is not None:
        run["path"] = path
    return run


def page(runs, total_count=None):
    return {"total_count": len(runs) if total_count is None else total_count,
            "workflow_runs": runs}


def consistent_scan(pages):
    runs = [run for item in pages for run in item["workflow_runs"]]
    return [page(runs[start:start + 100], len(runs)) for start in range(0, max(len(runs), 1), 100)]


def pull(number, head_sha, base="main", mergeable=True, state="open"):
    # A pull request's own record; the listing serves it without .mergeable, which it does not carry.
    return {"number": number, "state": state, "head": {"sha": head_sha}, "base": {"ref": base},
            "mergeable": mergeable}


def closed_pull(number, head_sha, base="main", merged=True):
    # A record of the pull requests associated with a commit: closed, with a merge time when merged.
    return {"number": number, "state": "closed", "head": {"sha": head_sha}, "base": {"ref": base},
            "merged_at": "2026-10-15T12:00:00Z" if merged else None}


def probe_read(*pulls, records=None, raw_records=None, record_errors=None, associated=None):
    # One read of the pull-request probe: the listing holds the given pull requests on one page, and
    # each one's own read serves it in full, unless records replaces that by number (raw_records and
    # record_errors, by number too, serve raw text or a failed read instead; see FAKE_GH). associated
    # holds the records of the pull requests associated with the commit (none by default).
    listed = [{key: value for key, value in item.items() if key != "mergeable"} for item in pulls]
    served = {str(item["number"]): item for item in pulls}
    served.update(records or {})
    return {"listing": [listed], "records": served, "raw_records": raw_records or {},
            "record_errors": record_errors or {}, "associated": associated or []}


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
         "--arg", "require_base", "", "--arg", "require_workflow", QUALITY_PATH,
         program],
        input=stdin, text=True,
        capture_output=True, timeout=10,
        env={"PATH": SYSTEM_PATH, "LC_ALL": "C", "TZ": "UTC"},
    )


class Fixture:
    def __init__(self, base):
        self.repo = base / "repo"
        self.bin = base / "bin"
        self.git_bin = base / "git-bin"
        self.home = base / "home"
        self.repo.mkdir()
        self.bin.mkdir()
        self.git_bin.mkdir()
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
        self.set_remote("origin", FIXTURE_ORIGIN)
        self.response = base / "responses.json"
        self.counter = base / "calls.txt"
        self.probe_counter = base / "probe-calls.txt"
        self.probe_reads = 0
        self.clock = base / "clock.txt"
        self.sleep_log = base / "sleeps.txt"
        self.sleeps = []
        self.scan_log = base / "scan-pages.txt"
        self.scan_pages = []
        self.argv_log = base / "gh-argv.txt"
        self.gh_argv = []
        self.git_argv_log = base / "git-argv.txt"
        self.git_argv = []
        for directory, name, body in ((self.bin, "gh", FAKE_GH), (self.bin, "date", FAKE_DATE),
                                      (self.bin, "sleep", FAKE_SLEEP), (self.git_bin, "git", FAKE_GIT)):
            executable = directory / name
            executable.write_text("#!{}\n{}".format(sys.executable, body), encoding="utf-8")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    def set_remote(self, name, *urls):
        # Replace the named remote's URLs; with no URL the remote is removed. Removing an absent remote
        # fails, which is ignored: the add that follows is checked.
        subprocess.run([GIT, "-C", str(self.repo)] + NO_AUTO_MAINTENANCE + ["remote", "remove", name],
                       capture_output=True, timeout=30, env=self.base_env)
        if urls:
            subprocess.run([GIT, "-C", str(self.repo)] + NO_AUTO_MAINTENANCE
                           + ["remote", "add", name, urls[0]],
                           check=True, capture_output=True, timeout=30, env=self.base_env)
        for url in urls[1:]:
            subprocess.run([GIT, "-C", str(self.repo)] + NO_AUTO_MAINTENANCE
                           + ["remote", "set-url", "--add", name, url],
                           check=True, capture_output=True, timeout=30, env=self.base_env)

    def reads_only(self, repo):
        # True when the last invocation ran gh at least once and every gh call read repo's runs.
        prefix = "repos/{}/actions/runs?".format(repo)
        return bool(self.gh_argv) and all(
            any(arg.startswith(prefix) for arg in argv) for argv in self.gh_argv)

    def invoke(self, polls, wait=False, timeout="900", clock=NOW, override=FIXTURE_REPO,
               served=FIXTURE_REPO, git_remote=None, extra_args=(), probes=None):
        # override is CI_STATUS_REPO (None leaves it unset); served is the only repository gh answers for;
        # git_remote, when given, is the (stdout, exit status) the fixture git gives for the origin's URLs.
        # probes are the pull-request probe's reads (see FAKE_GH); by default every read finds one open
        # pull request at this head into main that GitHub reports MERGEABLE.
        if probes is None:
            probes = [probe_read(pull(1, self.head_sha))]
        self.response.write_text(json.dumps({"polls": polls, "probes": probes}), encoding="utf-8")
        self.counter.write_text("0\n", encoding="utf-8")
        self.probe_counter.write_text("0\n", encoding="utf-8")
        self.scan_log.write_text("", encoding="utf-8")
        self.sleep_log.write_text("", encoding="utf-8")
        self.argv_log.write_text("", encoding="utf-8")
        self.git_argv_log.write_text("", encoding="utf-8")
        self.clock.write_text("{}\n".format(clock), encoding="utf-8")
        env = dict(self.base_env)
        env["PATH"] = (str(self.git_bin) + os.pathsep + str(self.bin)
                       + os.pathsep + SYSTEM_PATH)
        env["MOCK_GIT_REAL"] = GIT
        env["MOCK_GIT_ARGV_LOG"] = str(self.git_argv_log)
        env["MOCK_GH_RESPONSE"] = str(self.response)
        env["MOCK_GH_COUNTER"] = str(self.counter)
        env["MOCK_GH_PROBE_COUNTER"] = str(self.probe_counter)
        env["MOCK_CLOCK"] = str(self.clock)
        env["MOCK_SLEEP_LOG"] = str(self.sleep_log)
        env["MOCK_GH_SCAN_LOG"] = str(self.scan_log)
        env["MOCK_GH_ARGV_LOG"] = str(self.argv_log)
        env["MOCK_GH_REPO"] = served
        if git_remote is not None:
            env["MOCK_GIT_REMOTE_STDOUT"] = git_remote[0]
            env["MOCK_GIT_REMOTE_RC"] = str(git_remote[1])
        if override is not None:
            env["CI_STATUS_REPO"] = override
        env["CI_STATUS_TIMEOUT"] = timeout
        command = [str(SCRIPT), "HEAD"]
        if wait:
            command.append("--wait")
        command.extend(extra_args)
        result = subprocess.run(command, cwd=self.repo, env=env, text=True,
                                capture_output=True, timeout=30)
        calls = int(self.counter.read_text(encoding="utf-8").strip())
        self.probe_reads = int(self.probe_counter.read_text(encoding="utf-8").strip())
        self.sleeps = [int(line) for line in self.sleep_log.read_text(encoding="utf-8").split()]
        self.scan_pages = [int(line) for line in self.scan_log.read_text(encoding="utf-8").split()]
        self.gh_argv = [json.loads(line) for line in self.argv_log.read_text(encoding="utf-8").splitlines()]
        self.git_argv = [json.loads(line)
                         for line in self.git_argv_log.read_text(encoding="utf-8").splitlines()]
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
                "completed\tsuccess\t-\t-\tmatch\t{}\t{}\t{}\t{}".format(
                    QUALITY_PATH, run["id"], run["name"], run["html_url"])
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
                == (0, "completed\tsuccess\t-\t-\tmatch\t" + QUALITY_PATH + "\t101\tWeb generator health\t"
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

        # The exact TIMEOUT line: the read that ends the wait can come after the deadline (the poll
        # sleep and the probe precede it), so the message says only what the latest read found.
        rc, output, _calls = fixture.invoke([[page([])]], wait=True, timeout="0")
        check("ci/no-run-wait-timeout-exit1",
              (rc, [line for line in output.splitlines() if line.startswith("RESULT: TIMEOUT")]),
              (1, ["RESULT: TIMEOUT; the latest workflow-run read found no workflow run for "
                   + head_sha + "."]))

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

        # Round 2 MAJOR: a run ID listed twice by one source, in any shape other than the page shift merged
        # by the checks below, means offset pagination shifted between page reads in a way that may hide
        # another run: a scan page [ok, ok] with total_count 2 must not be green, two copies of another
        # commit's run must not read as "no run", a duplicate on full pages that the age bound ended must
        # not pass, and neither may a duplicate within the filtered source. Each is re-read once in
        # one-shot mode, then exit 2.
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
                 "head_sha-filtered")):
            rc, output, calls = fixture.invoke(polls)
            duplicates.append(
                (rc, calls, "duplicate run id within the {} listing".format(source) in output,
                 "no workflow run registered" in output))
        check("ci/duplicate-run-id-fail-closed", duplicates, [(2, 2, True, False)] * 4)

        # TOOL-CI-STATUS-DUP-RUN-ID (A): runs created between two page reads push the previous page's
        # trailing rows onto the next page. Repeated rows that are the next page's leading rows, identical
        # in every field, and no more than the rise in total_count are merged, not an API error, so a read
        # that merged them reports what it saw: this commit's failed run repeated across a short last page
        # fails on the FIRST read, reported once; this commit's not-terminal run beside a repeat on full
        # pages ended by the age bound is not green on the first read; and an all-success merged read is
        # re-read once, each of this commit's two repeated runs reported once per read, and a clean re-read
        # passes.
        recent = [workflow_run(8200 + index, "completed", "success", "Recent run {}".format(index),
                               "e" * 40) for index in range(200)]
        failed_row = "Repository quality checks: completed / failure"
        shifted_note = "shifted between page reads, so this all-success read is incomplete"
        shifted_read = {"filtered": [page([])],
                        "scan": [page(busy[:98] + [success_a, success_b], 150),
                                 page([success_a, success_b] + busy[98:148], 152)]}
        clean_read = {"filtered": [page([])],
                      "scan": [page(busy[:98] + [success_a, success_b], 150), page(busy[98:148], 150)]}
        merged = []
        rc, output, calls = fixture.invoke([{
            "filtered": [page([success_a, failed_b])],
            "scan": [page(busy[:99] + [failed_b], 150), page([failed_b] + busy[99:149], 151)]}])
        merged.append((rc, calls, output.count(failed_row), "duplicate run id" in output))
        rc, output, calls = fixture.invoke([{
            "filtered": [page([success_a, pending_b])],
            "scan": [page(recent[:100], 400), page(recent[99:199], 401), page(old_commits, 401)]}])
        merged.append((rc, calls, fixture.scan_pages, "not terminal" in output,
                       "duplicate run id" in output))
        rc, output, calls = fixture.invoke([shifted_read, clean_read])
        merged.append((rc, calls, output.count("Web generator health: completed / success"),
                       output.count("Repository quality checks: completed / success"),
                       shifted_note + "; re-reading once" in output, "duplicate run id" in output))
        check("ci/scan-identical-shift-repeat-merged", merged,
              [(1, 1, 1, False), (1, 1, [1, 2, 3], True, False), (0, 2, 2, 2, True, False)])

        # PR #486 QA round 1 MEDIUM-1: a run moving down across a page boundary offsets one moving up, so
        # the repeat stays within the rise while the run that moved up is skipped. Page 1 holds runs
        # 1001-1100 (total_count 320); before page 2 is read one run is created at the head, this commit's
        # failed run 1151 moves above the boundary, and run 1021 moves below the scanned range, so page 2
        # (total_count 321) starts with a repeat of run 1100 and run 1151 is on no page read; page 3 is
        # older than the bound, and the filtered source drops the failed run. The parent commit of this
        # fix exits 0 here. A merged read is never a pass: one-shot mode re-reads it once and a second
        # merged all-success read is exit 2; a re-read that sees run 1151 fails; under --wait a merged
        # read never settles, and one between clean reads restarts the settle count.
        first_rows = [workflow_run(1001 + index, "completed", "success", "Listing run {}".format(index),
                                   "e" * 40) for index in range(100)]
        later_rows = [workflow_run(run_id, "completed", "success", "Listing run {}".format(run_id),
                                   "e" * 40) for run_id in range(1101, 1202) if run_id != 1151]
        moved_failure = workflow_run(1151, "completed", "failure", "Moved quality run", head_sha)
        offsetting_read = {"filtered": [page([success_a])],
                           "scan": [page(first_rows, 320), page([first_rows[99]] + later_rows[:99], 321),
                                    page(old_commits, 321)]}
        settled_read = {"filtered": [page([success_a])],
                        "scan": [page([moved_failure] + first_rows[:20] + first_rows[21:], 321),
                                 page(later_rows[:100], 321), page(old_commits, 321)]}
        never_passes = []
        rc, output, calls = fixture.invoke([offsetting_read])
        never_passes.append((rc, calls, "ERROR: the unfiltered listing " + shifted_note in output,
                             "duplicate run id" in output))
        rc, output, calls = fixture.invoke([offsetting_read, settled_read])
        never_passes.append((rc, calls, "Moved quality run: completed / failure" in output))
        rc, output, calls = fixture.invoke([shifted_read], wait=True, timeout="90")
        never_passes.append((rc, calls, "TIMEOUT after 90s; the unfiltered listing " + shifted_note in output))
        rc, output, calls = fixture.invoke([clean_read] * 4 + [shifted_read] + [clean_read] * 5, wait=True)
        never_passes.append((rc, calls))
        check("ci/scan-shift-never-passes", never_passes,
              [(2, 2, True, False), (1, 2, True), (1, 7, True), (0, 10)])

        # (B): a repeated row that differs from the row it repeats stays an API error, re-read once in
        # one-shot mode, then exit 2: another commit's run whose status, conclusion, or URL changed, and
        # this commit's run whose creation time changed (no other check compares creation times).
        conflicting = []
        for earlier, later in (
                (busy[99], dict(busy[99], status="in_progress", conclusion=None)),
                (busy[99], dict(busy[99], conclusion="failure")),
                (busy[99], dict(busy[99], html_url="https://example.invalid/runs/other")),
                (success_b, dict(success_b, created_at=iso(COMMIT_TIME + 601)))):
            rc, output, calls = fixture.invoke([{
                "filtered": [page([success_a])],
                "scan": [page(busy[:99] + [earlier], 150), page([later] + busy[100:], 151)]}])
            conflicting.append((rc, calls, "duplicate run id within the unfiltered listing" in output))
        check("ci/scan-conflicting-shift-repeat-fail-closed", conflicting, [(2, 2, True)] * 4)

        # (C): a repeat that the rise in total_count does not cover stays an API error, on a short last
        # page whose row count matches total_count and on full pages ended by the age bound; so does a
        # repeat of a row that is not the earlier page's trailing row, and repeated rows in another order
        # than the earlier page's trailing rows.
        unexplained = []
        for scan in ([page(busy[:100], 151), page(busy[99:], 151)],
                     [page(recent[:100], 400), page(recent[99:199], 400), page(old_commits, 400)],
                     [page(busy[:100], 150), page([busy[98]] + busy[100:], 151)],
                     [page(busy[:100], 150), page([busy[99], busy[98]] + busy[100:], 152)]):
            rc, output, calls = fixture.invoke([{"filtered": [page([success_a])], "scan": scan}])
            unexplained.append(
                (rc, calls, "duplicate run id within the unfiltered listing" in output))
        check("ci/scan-unexplained-shift-repeat-fail-closed", unexplained, [(2, 2, True)] * 4)

        # A scan that reached the end of the listing reconciles the rows it read (a merged repeat counts as
        # the run created at the head that pushed it down) against total_count even when every page's own
        # count is in range.
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

        # Under --require-base BRANCH a run counts only when its
        # event is pull_request and the pull request GitHub recorded on the run at its creation
        # names BRANCH as its base (pull_requests[].base.ref). Both runs conform here, so the
        # verdict stays green; without the option the same fields go unread, so a run recorded
        # against another base still passes a plain check.
        require = ("--require-base", "main")
        on_main = [{"base": {"ref": "main"}}]
        quality_path = QUALITY_PATH
        leaks_path = ".github/workflows/msg-leaks.yml"
        pr_quality = workflow_run(901, "completed", "success", "Quality", head_sha,
                                  event="pull_request", pull_requests=on_main,
                                  path=quality_path)
        pr_leaks = workflow_run(902, "completed", "success", "Message leaks", head_sha,
                                event="pull_request", pull_requests=on_main,
                                path=leaks_path)
        stale_base = workflow_run(903, "completed", "success", "Quality", head_sha,
                                  event="pull_request",
                                  pull_requests=[{"base": {"ref": "dev"}}],
                                  path=quality_path)
        met = []
        rc, output, _calls = fixture.invoke([[page([pr_quality, pr_leaks])]], extra_args=require)
        met.append((rc, "Quality" in output and "Message leaks" in output))
        rc, _output, _calls = fixture.invoke([[page([stale_base])]])
        met.append((rc, True))
        check("ci/require-base-met-green", met, [(0, True)] * 2)

        # A green run recorded against another base (a pre-retarget run) is refused by name in
        # one-shot and --wait mode alike: a run's recorded base never changes, so the refusal is
        # immediate (one read), never a settle or a timeout.
        unmet_note = "do not satisfy --require-base main"
        retargeted = []
        for wait in (False, True):
            rc, output, calls = fixture.invoke([[page([stale_base, pr_leaks])]],
                                               wait=wait, extra_args=require)
            retargeted.append((rc, calls, unmet_note in output,
                               "its recorded base is dev" in output))
        check("ci/require-base-retargeted-run-refused", retargeted, [(1, 1, True, True)] * 2)

        # A push run is not pull_request evidence, and a run with no (or a baseless) recorded pull
        # request has no establishable base: each is refused by name, never a pass.
        push_main = workflow_run(904, "completed", "success", "Quality", head_sha,
                                 event="push", pull_requests=[], path=quality_path)
        orphaned = workflow_run(905, "completed", "success", "Quality", head_sha,
                                event="pull_request", pull_requests=[], path=quality_path)
        baseless = workflow_run(906, "completed", "success", "Quality", head_sha,
                                event="pull_request", pull_requests=[{"base": {"ref": ""}}],
                                path=quality_path)
        unproven = []
        for run, named in ((push_main, "not pull_request"),
                           (orphaned, "cannot be established"),
                           (baseless, "cannot be established")):
            rc, output, _calls = fixture.invoke([[page([run])]], extra_args=require)
            unproven.append((rc, unmet_note in output, named in output))
        check("ci/require-base-unproven-run-refused", unproven, [(1, True, True)] * 3)

        # EVERY run of the commit must satisfy the requirement: a conforming green sibling must not
        # stand in for a pre-retarget run (skipping nonconforming runs would pass this read).
        rc, output, _calls = fixture.invoke([[page([stale_base, pr_quality, pr_leaks])]],
                                            extra_args=require)
        check("ci/require-base-sibling-cannot-stand-in",
              (rc, "its recorded base is dev" in output), (1, True))

        # The option needs a usable branch name, refused before any git or API read: an empty value
        # would silently mean "no requirement" (the fail-open boundary), and a hyphen-led one is a
        # swallowed flag, not a branch.
        value_refused = []
        for extra in (("--require-base",), ("--require-base", ""), ("--require-base", "--wait")):
            rc, output, _calls = fixture.invoke([[page([pr_quality])]], extra_args=extra)
            value_refused.append((rc, fixture.gh_argv, fixture.git_argv,
                                  "needs a branch name" in output))
        check("ci/require-base-value-refused", value_refused, [(2, [], [], True)] * 3)

        # Under the option a record of this commit without a usable event or pull_requests field is
        # a malformed run record (API error, exit 2), never a verdict; mixed recorded bases on one
        # run are unmet (exit 1), not green.
        no_event = workflow_run(907, "completed", "success", "Quality", head_sha,
                                pull_requests=on_main, path=quality_path)
        not_array = workflow_run(908, "completed", "success", "Quality", head_sha,
                                 event="pull_request", pull_requests=7, path=quality_path)
        no_base_obj = workflow_run(909, "completed", "success", "Quality", head_sha,
                                   event="pull_request", pull_requests=[{"number": 5}],
                                   path=quality_path)
        mixed = workflow_run(910, "completed", "success", "Quality", head_sha,
                             event="pull_request",
                             pull_requests=[{"base": {"ref": "main"}},
                                            {"base": {"ref": "dev"}}],
                             path=quality_path)
        shapes = []
        for run in (no_event, not_array, no_base_obj):
            rc, output, _calls = fixture.invoke([[page([run])]], extra_args=require)
            shapes.append((rc, "malformed workflow run record" in output))
        rc, output, _calls = fixture.invoke([[page([mixed])]], extra_args=require)
        shapes.append((rc, unmet_note in output))
        check("ci/require-base-malformed-fail-closed", shapes, [(2, True)] * 3 + [(1, True)])

        # A conforming run set alone is not Quality
        # evidence. After a retarget to main the only run on the head can be the msg-leaks run
        # (quality.yml filters pull_request on branches: [main] and does not list edited), so a
        # lone conforming sibling formerly read green with no Quality run at all. Under
        # --require-base at least one completed successful conforming run of the workflow file
        # .github/workflows/quality.yml (--require-workflow's default) is required; its absence
        # is unmet (exit 1, naming the missing workflow file) in one-shot and --wait mode alike.
        # Report-once refuses on its single snapshot; --wait keeps polling through
        # the settle window first (a qualifying run can be registered late on this same head; a
        # re-applying the merge-train label schedules one), so the refusal comes only once the run
        # set has been stable for
        # five observations and still holds no qualifying run.
        missing_quality = ("no completed successful run of the workflow file "
                           + quality_path)
        leaks_only = []
        for wait in (False, True):
            rc, output, calls = fixture.invoke([[page([pr_leaks])]],
                                               wait=wait, extra_args=require)
            leaks_only.append((rc, calls, missing_quality in output))
        check("ci/require-base-quality-run-required", leaks_only,
              [(1, 1, True), (1, 5, True)])

        # --require-workflow PATH overrides the default: a conforming run of the workflow FILE
        # at PATH is then the required evidence, and its absence names PATH, never quality.yml.
        other_path = ".github/workflows/other.yml"
        pr_other = workflow_run(911, "completed", "success", "Other gate", head_sha,
                                event="pull_request", pull_requests=on_main, path=other_path)
        override = []
        rc, _output, _calls = fixture.invoke(
            [[page([pr_other, pr_leaks])]],
            extra_args=require + ("--require-workflow", other_path))
        override.append((rc, True))
        rc, output, _calls = fixture.invoke(
            [[page([pr_quality, pr_leaks])]],
            extra_args=require + ("--require-workflow", other_path))
        override.append((rc, "run of the workflow file " + other_path in output))
        check("ci/require-workflow-override", override, [(0, True), (1, True)])

        # --require-workflow is validated like --require-base, as each value is consumed (an
        # invalid FIRST value is refused although a valid one follows), before any git or API
        # read, and the git and gh argv logs both pin that order; alone (without
        # --require-base) it is refused rather than silently ignored, and a repeated option is
        # refused, never last-wins.
        wf_refused = []
        for extra, needle in (
                (require + ("--require-workflow",), "needs a workflow path"),
                (require + ("--require-workflow", ""), "needs a workflow path"),
                (require + ("--require-workflow", " "), "needs a workflow path"),
                (require + ("--require-workflow", "--wait"), "needs a workflow path"),
                (require + ("--require-workflow", "",
                            "--require-workflow", quality_path), "needs a workflow path"),
                (require + ("--require-workflow", quality_path,
                            "--require-workflow", quality_path), "more than once"),
                (("--require-workflow", quality_path), "only with --require-base")):
            rc, output, _calls = fixture.invoke([[page([pr_quality])]], extra_args=extra)
            wf_refused.append((rc, fixture.gh_argv == [], fixture.git_argv == [],
                               needle in output))
        check("ci/require-workflow-value-refused", wf_refused, [(2, True, True, True)] * 7)

        # Each --require-base value is validated AS
        # IT IS CONSUMED (a later value cannot repair or replace an earlier one), a
        # whitespace-only value is refused before any git or API read, and a repeated
        # --require-base is refused (exit 2), never last-wins. The git argv log is
        # asserted too, so validation moved behind a git read turns these red.
        consumed = []
        for extra, needle in (
                (("--require-base", "", "--require-base", "main"), "needs a branch name"),
                (("--require-base", "--wait", "--require-base", "main"),
                 "needs a branch name"),
                (("--require-base", " "), "needs a branch name"),
                (("--require-base", " \t "), "needs a branch name"),
                (("--require-base", "main", "--require-base", "main"), "more than once")):
            rc, output, _calls = fixture.invoke([[page([pr_quality])]], extra_args=extra)
            consumed.append((rc, fixture.gh_argv == [], fixture.git_argv == [],
                             needle in output))
        check("ci/require-base-value-consumed", consumed, [(2, True, True, True)] * 5)

        # A malformed run record is a property of the
        # stored evidence, not a retryable API condition, so it exits 2 under --wait too (one
        # poll, never a timeout misread as a verdict).
        malformed_wait = []
        for run in (no_event, not_array, no_base_obj):
            rc, output, calls = fixture.invoke([[page([run])]], wait=True,
                                               extra_args=require)
            malformed_wait.append((rc, calls,
                                   "malformed workflow run record" in output,
                                   "TIMEOUT" in output))
        check("ci/require-base-malformed-fail-closed-wait", malformed_wait,
              [(2, 1, True, False)] * 3)

        # The two sources disagreeing about a run's
        # recorded base or event is a conflicting duplicate record (exit 2 after the one-shot
        # re-read), never a pass: dropping event, path or pull_requests from the cross-source
        # projection would let the filtered record's conforming base win while the scan recorded
        # another.
        scan_dev = workflow_run(912, "completed", "success", "Quality", head_sha,
                                event="pull_request",
                                pull_requests=[{"base": {"ref": "dev"}}],
                                path=quality_path)
        filt_main = workflow_run(912, "completed", "success", "Quality", head_sha,
                                 event="pull_request", pull_requests=on_main,
                                 path=quality_path)
        filt_push = workflow_run(912, "completed", "success", "Quality", head_sha,
                                 event="push", pull_requests=on_main, path=quality_path)
        filt_other = dict(filt_main, path=other_path)
        conflicts = []
        for filtered_run, scanned_run in ((filt_main, scan_dev),
                                          (scan_dev, filt_main),
                                          (filt_push, filt_main),
                                          (filt_main, filt_other)):
            rc, output, calls = fixture.invoke(
                [{"filtered": [page([filtered_run])], "scan": [page([scanned_run])]}],
                extra_args=require)
            conflicts.append(
                (rc, calls,
                 "conflicting duplicate workflow-run records" in output))
        check("ci/require-base-cross-source-conflict", conflicts, [(2, 2, True)] * 4)

        # The required run is bound to its
        # workflow FILE (the run's recorded path), never its display name: any workflow can
        # carry name: Quality, so a conforming same-named run from another file must never
        # satisfy the requirement, in one-shot and --wait mode alike (exit 1, never 0), while
        # the correct file does (exit 0).
        impostor = workflow_run(913, "completed", "success", "Quality", head_sha,
                                event="pull_request", pull_requests=on_main,
                                path=".github/workflows/unrelated.yml")
        bound = []
        rc, output, calls = fixture.invoke([[page([impostor, pr_leaks])]], extra_args=require)
        bound.append((rc, calls, missing_quality in output))
        rc, output, calls = fixture.invoke([[page([impostor, pr_leaks])]], wait=True,
                                           extra_args=require)
        bound.append((rc, calls, missing_quality in output))
        rc, output, calls = fixture.invoke([[page([pr_quality, pr_leaks])]], wait=True,
                                           extra_args=require)
        bound.append((rc, calls, missing_quality in output))
        check("ci/require-workflow-file-binding", bound,
              [(1, 1, True), (1, 5, True), (0, 5, False)])

        # Under --require-base, as in every mode, a run record whose workflow path is missing, empty
        # or not a string is a malformed run record (exit 2), in one-shot and --wait mode
        # alike: without a usable path the required-workflow match cannot be evaluated, and
        # reading such a run as some other file would be the name-collision fail-open again.
        no_path = workflow_run(914, "completed", "success", "Quality", head_sha,
                               event="pull_request", pull_requests=on_main, path=None)
        pathless = []
        for run in (no_path,
                    workflow_run(915, "completed", "success", "Quality", head_sha,
                                 event="pull_request", pull_requests=on_main, path=""),
                    workflow_run(916, "completed", "success", "Quality", head_sha,
                                 event="pull_request", pull_requests=on_main, path=7)):
            rc, output, _calls = fixture.invoke([[page([run])]], extra_args=require)
            pathless.append((rc, "malformed workflow run record" in output))
        rc, output, calls = fixture.invoke([[page([no_path])]], wait=True, extra_args=require)
        pathless.append((rc, calls, "malformed workflow run record" in output,
                         "TIMEOUT" in output))
        check("ci/require-base-path-missing-fail-closed", pathless,
              [(2, True)] * 3 + [(2, 1, True, False)])

        # A required-workflow run REGISTERED LATE must not
        # end the wait at once: the first poll holds only the conforming msg-leaks run, the
        # qualifying Quality run appears from the second poll on (the shape re-applying the
        # merge-train label produces
        # on the SAME head), and the wait settles green instead of refusing on poll one.
        polls = [[page([pr_leaks])]] + [[page([pr_leaks, pr_quality])]] * 5
        rc, _output, calls = fixture.invoke(polls, wait=True, extra_args=require)
        check("ci/require-base-wait-late-quality-run", (rc, calls), (0, 6))

        # The required-workflow match reads the run's
        # RAW JSON .path inside jq, never Bash's @tsv-escaped path column. An earlier revision
        # compared the escaped column with the unescaped required path, so two DIFFERENT paths
        # that escape alike (a recorded real tab versus a requested literal backslash-t) read as
        # equal, and a path holding a tab, newline, carriage return or backslash never equalled
        # itself. Both directions are pinned, from either listing alone, in one-shot and --wait
        # mode alike.
        lookalike = ".github/workflows/a\\tb.yml"  # a literal backslash, then t
        tab_path = ".github/workflows/a\tb.yml"  # a real tab
        control_path = ".github/workflows/a\tb\nc\rd\\e.yml"
        tab_run = workflow_run(917, "completed", "success", "Quality", head_sha,
                               event="pull_request", pull_requests=on_main, path=tab_path)
        control_run = workflow_run(918, "completed", "success", "Quality", head_sha,
                                   event="pull_request", pull_requests=on_main,
                                   path=control_path)
        unsatisfied = "no completed successful run of the workflow file"
        raw_match = []
        for run, requested in ((tab_run, lookalike), (control_run, control_path)):
            for polls in ([{"filtered": [page([run])], "scan": [page([])]}],
                          [{"filtered": [page([])], "scan": [page([run])]}]):
                for wait in (False, True):
                    rc, output, _calls = fixture.invoke(
                        polls, wait=wait,
                        extra_args=require + ("--require-workflow", requested))
                    raw_match.append((rc, unsatisfied in output))
        check("ci/require-workflow-raw-path-match", raw_match,
              [(1, True)] * 4 + [(0, False)] * 4)

        # Under --require-base a malformed record is
        # refused from EITHER source alone, a malformed sibling beside a valid required run
        # included, in one-shot and --wait mode alike (exit 2, one read, never a timeout). The
        # vectors above feed every malformed record through both listings at once, so they alone
        # would let a broken scan-source (or filtered-source) validation hide behind the other
        # source's.
        bad_sibling = workflow_run(919, "completed", "success", "Sibling", head_sha,
                                   event="pull_request", pull_requests=on_main, path=None)
        single_source = []
        for polls in (
                [{"filtered": [page([no_path])], "scan": [page([])]}],
                [{"filtered": [page([])], "scan": [page([no_path])]}],
                [{"filtered": [page([pr_quality, bad_sibling])],
                  "scan": [page([pr_quality])]}],
                [{"filtered": [page([pr_quality])],
                  "scan": [page([pr_quality, bad_sibling])]}]):
            for wait in (False, True):
                rc, output, calls = fixture.invoke(polls, wait=wait, extra_args=require)
                single_source.append((rc, calls, "malformed workflow run record" in output,
                                      "TIMEOUT" in output))
        check("ci/require-base-single-source-malformed", single_source,
              [(2, 1, True, False)] * 8)

        # Without --require-base too, a run of the commit whose workflow path is missing, empty or
        # not a string (an object, a number) is a malformed run record (exit 2, one read, never a
        # timeout) naming the run, in one-shot and --wait mode alike, even beside a valid quality.yml
        # run: whether it is the required workflow file cannot be evaluated. A plain read formerly
        # validated no path, so such a sibling beside a valid Quality run read green (exit 0).
        pathless_note = "malformed workflow run record: run(s) 920 of this commit record no workflow path"
        plain_pathless = []
        for raw_path in (None, "", {"x": 1}, 7):
            pathological = workflow_run(920, "completed", "success", "Web generator health",
                                        head_sha, path=raw_path)
            for wait in (False, True):
                rc, output, calls = fixture.invoke([[page([pathological, success_b])]], wait=wait)
                plain_pathless.append((rc, calls, pathless_note in output, "TIMEOUT" in output))
        check("ci/plain-mode-path-malformed-refused", plain_pathless, [(2, 1, True, False)] * 8)

        # The plain-mode path refusal holds from EITHER listing alone, and names every such run once:
        # the vectors above feed the malformed run through both listings at once, so they alone would
        # let a path check on one listing (or one confined to --require-base again) hide behind the
        # other listing's.
        empty_path = workflow_run(920, "completed", "success", "Web generator health", head_sha,
                                  path="")
        object_path = workflow_run(921, "completed", "success", "Sibling", head_sha, path={"x": 1})
        single_pathless = []
        for polls, note in (
                ([{"filtered": [page([success_b, empty_path])], "scan": [page([success_b])]}],
                 pathless_note),
                ([{"filtered": [page([success_b])], "scan": [page([success_b, empty_path])]}],
                 pathless_note),
                ([{"filtered": [page([success_b, object_path, empty_path])],
                   "scan": [page([empty_path, success_b, object_path])]}],
                 "malformed workflow run record: run(s) 920, 921 of this commit record no workflow path")):
            for wait in (False, True):
                rc, output, calls = fixture.invoke(polls, wait=wait)
                single_pathless.append((rc, calls, note in output, "TIMEOUT" in output))
        check("ci/plain-mode-path-single-source-refused", single_pathless,
              [(2, 1, True, False)] * 6)

        # Without --require-base too, a head whose only run is its msg-leaks.yml run (a member head,
        # which the merge lane never labels) has no Quality evidence: it reads as missing (exit 1,
        # naming quality.yml), never as passing, in one-shot mode on its single read and under --wait
        # once the settle window still holds no qualifying run. So does a head whose run named
        # Quality comes from another workflow file.
        plain_leaks = workflow_run(922, "completed", "success", "Message/metadata leaks", head_sha,
                                   path=leaks_path)
        plain_impostor = workflow_run(923, "completed", "success", "Quality", head_sha,
                                      path=".github/workflows/unrelated.yml")
        required = []
        for runs in ([plain_leaks], [plain_impostor, plain_leaks]):
            for wait in (False, True):
                rc, output, calls = fixture.invoke([[page(runs)]], wait=wait)
                required.append((rc, calls, missing_quality + " on this head" in output))
        check("ci/plain-quality-run-required", required, [(1, 1, True), (1, 5, True)] * 2)

        # A push run of quality.yml (a commit on main) satisfies the plain read, and so does a
        # quality.yml run that only the unfiltered listing shows beside the leaks run the head_sha
        # filter returned: the scan keeps each run's path. A quality.yml run registered after the
        # first --wait poll settles green on the same head.
        plain_push = workflow_run(924, "completed", "success", "Quality", head_sha,
                                  event="push", pull_requests=[])
        missing_refusal = "RESULT: " + missing_quality
        satisfied = []
        rc, output, calls = fixture.invoke([[page([plain_push, plain_leaks])]])
        satisfied.append((rc, calls, missing_refusal in output))
        rc, output, calls = fixture.invoke([{"filtered": [page([plain_leaks])],
                                             "scan": [page([plain_leaks, success_b])]}])
        satisfied.append((rc, calls, missing_refusal in output))
        polls = [[page([plain_leaks])]] + [[page([plain_leaks, success_b])]] * 5
        rc, output, calls = fixture.invoke(polls, wait=True)
        satisfied.append((rc, calls, missing_refusal in output))
        check("ci/plain-quality-run-satisfied", satisfied,
              [(0, 1, False), (0, 1, False), (0, 6, False)])

        # A second positional rev is refused (exit 2) before any git or API
        # read, never read last-wins: a stray positional that resolves (a branch name, say)
        # would silently change which commit is checked.
        rc, output, _calls = fixture.invoke([[page([pr_quality])]], extra_args=("HEAD",))
        check("ci/rev-repeated-refused",
              (rc, fixture.gh_argv == [], fixture.git_argv == [],
               "a repeated rev is refused" in output), (2, True, True, True))

        # The git argv recorder is proven to log real
        # git reads on a run that does read git, so the fixture.git_argv == [] assertions above
        # cannot pass vacuously under a recorder that stopped logging (or a script that reached
        # git around the PATH fixture).
        rc, _output, _calls = fixture.invoke([[page([pr_quality])]], extra_args=require)
        check("ci/git-argv-recorder-positive-control",
              (rc,
               any(argv[:3] == ["rev-parse", "--verify", "--quiet"]
                   for argv in fixture.git_argv),
               any(argv[:3] == ["remote", "get-url", "--all"] for argv in fixture.git_argv)),
              (0, True, True))

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

        # FIX-CISTATUS-R1: the repository read is the one this checkout's origin remote names, never a
        # built-in name. The parent of this fix read a fixed repository whatever the origin was, so a copied,
        # forked, or adopter checkout reported another repository's runs as its own. Each case serves (gh
        # answers for) the repository a wrong rule would read, so only the right rule passes: a derived case
        # serves the origin's repository and must read only it; a refused case serves the repository the
        # refused input names and must exit 2 before running gh at all.
        adopter = "adopter-org/adopter-repo"
        green = [[page([success_a])]]
        derived = []
        for url, override in (("https://github.com/adopter-org/adopter-repo", None),
                              ("https://github.com/adopter-org/adopter-repo.git", None),
                              ("https://github.com/adopter-org/adopter-repo.git", adopter)):
            fixture.set_remote("origin", url)
            rc, _output, _calls = fixture.invoke(green, override=override, served=adopter)
            derived.append((rc, fixture.reads_only(adopter)))
        check("ci/repo-derived-https", derived, [(0, True)] * 3)

        derived = []
        for url in ("git@github.com:adopter-org/adopter-repo.git", "git@github.com:adopter-org/adopter-repo",
                    "ssh://git@github.com/adopter-org/adopter-repo.git",
                    "ssh://git@github.com/adopter-org/adopter-repo"):
            fixture.set_remote("origin", url)
            rc, _output, _calls = fixture.invoke(green, override=None, served=adopter)
            derived.append((rc, fixture.reads_only(adopter)))
        check("ci/repo-derived-ssh", derived, [(0, True)] * 4)

        # CI_STATUS_REPO only confirms the derived name, compared exactly: another repository, the same
        # name in another case, and the name with .git are each refused, although gh would answer for it.
        fixture.set_remote("origin", "https://github.com/adopter-org/adopter-repo.git")
        refused = []
        for override in ("upstream-org/upstream-repo", "Adopter-Org/adopter-repo", adopter + ".git"):
            rc, output, _calls = fixture.invoke(green, override=override, served=override)
            refused.append((rc, fixture.gh_argv,
                            "refusing to read another repository's CI runs" in output))
        check("ci/repo-override-mismatch-refused", refused, [(2, [], True)] * 3)

        # No origin remote: refused with and without CI_STATUS_REPO (which is never a fallback), and when
        # another remote names a readable repository.
        absent = []
        fixture.set_remote("origin")
        for override in (None, FIXTURE_REPO):
            rc, output, _calls = fixture.invoke(green, override=override)
            absent.append((rc, fixture.gh_argv, "has no origin remote" in output))
        fixture.set_remote("upstream", FIXTURE_ORIGIN)
        rc, output, _calls = fixture.invoke(green, override=None)
        absent.append((rc, fixture.gh_argv, "has no origin remote" in output))
        fixture.set_remote("upstream")
        check("ci/repo-absent-remote-refused", absent, [(2, [], True)] * 3)

        # Each URL below breaks a rule of the strict parse (host, scheme, user name or credentials, port, path
        # shape, or a name's characters), most of them while otherwise matching an accepted form, and must be
        # refused. The name length rules are pinned by ci/repo-name-length-bounds below.
        malformed = []
        for url in ("https://github.com/adopter-org",
                    "https://github.com/adopter-org/adopter-repo/extra",
                    "https://github.com/adopter-org/adopter-repo/",
                    "https://github.com/adopter-org/.git",
                    "https://github.com/adopter-org/..",
                    "https://github.com/-adopter/adopter-repo",
                    "https://github.com/adopter-org/adopter repo",
                    "https://github.example/adopter-org/adopter-repo",
                    "https://github.com.example/adopter-org/adopter-repo",
                    "http://github.com/adopter-org/adopter-repo",
                    "https://user:secret@github.com/adopter-org/adopter-repo",
                    "https://github.com:443/adopter-org/adopter-repo",
                    "ssh://git@github.com:22/adopter-org/adopter-repo",
                    "git@github.example:adopter-org/adopter-repo",
                    "github.com/adopter-org/adopter-repo",
                    "/srv/git/adopter-org/adopter-repo.git"):
            fixture.set_remote("origin", url)
            rc, output, _calls = fixture.invoke(green, override=None, served=adopter)
            malformed.append((url, rc, fixture.gh_argv, "origin remote URL is not one of" in output,
                              "secret" in output))
        check("ci/repo-malformed-url-refused", malformed,
              [(url, 2, [], True, False) for url, *_rest in malformed])

        # An origin with two URLs names no single repository, whichever of them gh would answer for.
        fixture.set_remote("origin", "https://github.com/adopter-org/adopter-repo.git",
                           "git@github.com:upstream-org/upstream-repo.git")
        ambiguous = []
        for served in (adopter, "upstream-org/upstream-repo"):
            rc, output, _calls = fixture.invoke(green, override=None, served=served)
            ambiguous.append((rc, fixture.gh_argv, "has more than one URL" in output))
        check("ci/repo-ambiguous-remote-refused", ambiguous, [(2, [], True)] * 2)

        # FIX-515-R2: git ends each URL it prints with a line feed, and command substitution strips every
        # trailing one, so a URL that itself ends in a line feed, or a valid URL followed by a newline-only
        # URL, read as one valid URL. Read byte for byte, both are refused before gh runs, although gh would
        # answer for the repository they name.
        line_break = []
        for urls in (("https://github.com/adopter-org/adopter-repo.git\n",),
                     ("https://github.com/adopter-org/adopter-repo.git", "\n")):
            fixture.set_remote("origin", *urls)
            rc, output, _calls = fixture.invoke(green, override=None, served=adopter)
            line_break.append((rc, fixture.gh_argv, "has more than one URL" in output))
        check("ci/repo-origin-line-break-refused", line_break, [(2, [], True)] * 2)

        # A failing git remote get-url (an exit other than 0 or 2) is cannot-evaluate, whatever it printed:
        # real git exits 128 on a url key with no value, and the fixture git prints a valid URL, then exits 1.
        unreadable = []
        config = fixture.repo / ".git" / "config"
        saved_config = config.read_text(encoding="utf-8")
        fixture.set_remote("origin")
        config.write_text(config.read_text(encoding="utf-8") + '[remote "origin"]\n\turl\n', encoding="utf-8")
        rc, output, _calls = fixture.invoke(green, override=None, served=adopter)
        config.write_text(saved_config, encoding="utf-8")
        unreadable.append((rc, fixture.gh_argv, "git could not read the origin remote URL" in output))
        printed = "https://github.com/adopter-org/adopter-repo.git\n"
        rc, output, _calls = fixture.invoke(green, override=None, served=adopter, git_remote=(printed, 1))
        unreadable.append((rc, fixture.gh_argv, "git remote get-url exited 1)" in output))
        check("ci/repo-origin-unreadable-refused", unreadable, [(2, [], True)] * 2)

        # An origin read that succeeds with no URL (no output, or one empty line, which real git does not
        # produce) is the no-origin refusal, never a parse of the empty string.
        empty = []
        for stdout in ("", "\n"):
            rc, output, _calls = fixture.invoke(green, override=None, git_remote=(stdout, 0))
            empty.append((rc, fixture.gh_argv,
                          "has no origin remote, or its origin remote has no URL" in output))
        check("ci/repo-origin-empty-refused", empty, [(2, [], True)] * 2)

        # The name length bounds: the longest owner (39) and repository name (100) are read, and one character
        # more is refused before gh runs, although gh would answer for it.
        bounds = []
        for owner, name in (("a" * 39, "adopter-repo"), ("adopter-org", "r" * 100),
                            ("a" * 40, "adopter-repo"), ("adopter-org", "r" * 101)):
            fixture.set_remote("origin", "https://github.com/{}/{}.git".format(owner, name))
            rc, output, _calls = fixture.invoke(green, override=None, served=owner + "/" + name)
            bounds.append((rc, fixture.reads_only(owner + "/" + name), fixture.gh_argv == [],
                           "origin remote URL is not one of" in output))
        check("ci/repo-name-length-bounds", bounds,
              [(0, True, False, False)] * 2 + [(2, False, True, True)] * 2)

        # The CI_STATUS_REPO refusal names only the derived repository and never echoes the override, which is
        # unvalidated: neither a credential-bearing value nor one whose line break would forge a diagnostic
        # line appears in the output, although gh would answer for the derived repository.
        fixture.set_remote("origin", "https://github.com/adopter-org/adopter-repo.git")
        not_echoed = []
        for override in ("https://user:secret@github.com/" + adopter,
                         adopter + "\nFORGED: diagnostic line", "upstream-org/upstream-repo"):
            rc, output, _calls = fixture.invoke(green, override=override, served=adopter)
            not_echoed.append((rc, fixture.gh_argv, "does not equal adopter-org/adopter-repo," in output,
                               override in output, "secret" in output, "FORGED" in output))
        check("ci/repo-override-mismatch-not-echoed", not_echoed, [(2, [], True, False, False, False)] * 3)
        fixture.set_remote("origin", FIXTURE_ORIGIN)

        # THE PULL-REQUEST PROBE: under --wait, a head with no open pull request, whose associated pull
        # requests show none with this head merged into the base, is refused (exit 1, no runs read) at
        # the last of the probe's 12 reads, naming the cause, however green its runs read: no open pull
        # request at all, or only one whose head is another commit. The refusal claims only what the
        # associated pull requests showed, and only that no new pull_request run will start while that
        # holds, never that an earlier run cannot finish.
        green_polls = [[page([success_a])]]
        no_pr = []
        for probes in ([probe_read()], [probe_read(pull(2, "e" * 40))]):
            rc, output, calls = fixture.invoke(green_polls, wait=True, probes=probes)
            no_pr.append((rc, calls, fixture.probe_reads,
                          "no open pull request has {} as its head now, and the pull requests associated "
                          "with {} show none with this head merged into main".format(head_sha, head_sha)
                          in output,
                          "no new pull_request run will start for it while that holds; a run that "
                          "started earlier may still finish" in output))
        check("ci/wait-pr-probe-no-open-pull-request", no_pr, [(1, 0, 12, True, True)] * 2)

        # The base is the --require-base value, else main: an open pull request at this head into dev is
        # refused by name under a plain --wait, waited on under --require-base dev, and a sibling at the
        # same head into main is enough under a plain --wait.
        into_dev = pull(7, head_sha, base="dev")
        dev_quality = workflow_run(921, "completed", "success", "Quality", head_sha,
                                   event="pull_request", pull_requests=[{"base": {"ref": "dev"}}],
                                   path=quality_path)
        based = []
        rc, output, calls = fixture.invoke(green_polls, wait=True, probes=[probe_read(into_dev)])
        based.append((rc, calls, "no open pull request for {} targets main now (#7 into dev), and the "
                      "pull requests associated with {} show none with this head merged into main, so "
                      "no new pull_request run into main will start for it while that holds; a run that "
                      "started earlier may still finish".format(head_sha, head_sha) in output))
        rc, output, calls = fixture.invoke([[page([dev_quality])]], wait=True,
                                           probes=[probe_read(into_dev)],
                                           extra_args=("--require-base", "dev"))
        based.append((rc, calls, "refusing to wait" in output))
        rc, output, calls = fixture.invoke(green_polls, wait=True,
                                           probes=[probe_read(into_dev, pull(8, head_sha))])
        based.append((rc, calls, "refusing to wait" in output))
        check("ci/wait-pr-probe-base-not-main", based, [(1, 0, True), (0, 5, False), (0, 5, False)])

        # A pull request GitHub reports CONFLICTING is refused at once when the associated pull requests,
        # read on the same probe read, show no merge into main at this head (here they hold none at all),
        # before the first poll and mid-wait alike: the probe runs before every poll, and here the head
        # turns CONFLICTING after the first poll, as it does when another pull request merges. One
        # MERGEABLE pull request into main at this head is enough beside a CONFLICTING sibling.
        conflicted = pull(9, head_sha, mergeable=False)
        conflicts = []
        for polls, probes, timeout in (
                (green_polls, [probe_read(conflicted)], "900"),
                ([[page([pending_b])]], [probe_read(pull(9, head_sha)), probe_read(conflicted)], "60")):
            rc, output, calls = fixture.invoke(polls, wait=True, timeout=timeout, probes=probes)
            conflicts.append((rc, calls, fixture.probe_reads,
                              "pull request #9 CONFLICTING with main now, and the pull requests "
                              "associated with {} show none with this head merged into main, so no new "
                              "pull_request run will start for it while that holds; a run that started "
                              "earlier may still finish".format(head_sha) in output))
        rc, output, calls = fixture.invoke(green_polls, wait=True,
                                           probes=[probe_read(conflicted, pull(10, head_sha))])
        conflicts.append((rc, calls, fixture.probe_reads, "CONFLICTING" in output))
        check("ci/wait-pr-probe-conflicting", conflicts,
              [(1, 0, 1, True), (1, 1, 2, True), (0, 5, 5, False)])

        # UNKNOWN is re-read 5 seconds apart, at most 12 reads per probe. MERGEABLE on the 11th read (just
        # inside the bound) or the 12th (exactly at it) waits, and the wait settles green (one more read
        # per later poll); UNKNOWN still on the 12th read is cannot-evaluate (exit 2) with no 13th read
        # and no runs read, although a 13th would have read MERGEABLE (just outside).
        undecided = probe_read(pull(11, head_sha, mergeable=None))
        bounded = []
        for unknown_reads in (10, 11, 12):
            rc, output, calls = fixture.invoke(
                green_polls, wait=True,
                probes=[undecided] * unknown_reads + [probe_read(pull(11, head_sha))])
            bounded.append((rc, calls, fixture.probe_reads,
                            "pull request #11 as UNKNOWN" in output,
                            "still after 12 reads 5s apart" in output))
        check("ci/wait-pr-probe-unknown-bound", bounded,
              [(0, 5, 15, True, False), (0, 5, 16, True, False), (2, 0, 12, True, True)])

        # A read refused as not accessible or not found in gh's own form (the listing, or one pull
        # request's own read), and a malformed listing or record, are cannot-evaluate (exit 2) at once:
        # one read, no runs read. Any other read failure is re-read within the same bound: once, then a
        # MERGEABLE read waits; at every read, exit 2 at the bound.
        listed_only = {"listing": probe_read(pull(12, head_sha))["listing"], "records": {}}
        failures = []
        for probes in ([{"error": "gh: Resource not accessible by personal access token (HTTP 403)"}],
                       [{"error": "gh: Not Found (HTTP 404)"}],
                       [listed_only],
                       [{"listing": [[{"number": "12"}]], "records": {}}],
                       [probe_read(pull(12, head_sha),
                                   records={"12": pull(12, head_sha, mergeable="yes")})],
                       [{"error": "gh: HTTP 502"}, probe_read(pull(12, head_sha))],
                       [{"error": "gh: HTTP 502"}]):
            rc, output, calls = fixture.invoke(green_polls, wait=True, probes=probes)
            failures.append((rc, calls, fixture.probe_reads, "cannot evaluate" in output))
        check("ci/wait-pr-probe-read-failure", failures,
              [(2, 0, 1, True)] * 5 + [(0, 5, 6, False), (2, 0, 12, True)])

        # A pull request that changed between the listing and its own read (here it closed) is re-read,
        # never waited on: the later reads find no open pull request at this head, and the last refuses.
        rc, output, calls = fixture.invoke(
            green_polls, wait=True,
            probes=[probe_read(pull(13, head_sha), records={"13": pull(13, head_sha, state="closed")}),
                    probe_read()])
        check("ci/wait-pr-probe-changed-between-reads",
              (rc, calls, fixture.probe_reads,
               "changed between the listing and its own read" in output,
               "no open pull request has" in output), (1, 0, 12, True, True))

        # The probe runs before every poll under --wait (one read per poll for a MERGEABLE head), and
        # never in report-once mode, which reads no pull request even when none is open.
        every = []
        rc, _output, calls = fixture.invoke(green_polls, wait=True)
        every.append((rc, calls, fixture.probe_reads))
        rc, _output, calls = fixture.invoke(green_polls, probes=[probe_read()])
        every.append((rc, calls, fixture.probe_reads,
                      any("/pulls" in arg for argv in fixture.gh_argv for arg in argv)))
        check("ci/wait-pr-probe-every-poll-not-report-once", every, [(0, 5, 5), (0, 1, 0, False)])

        # Each pull request's own read must answer with exactly one record numbered as requested,
        # checked on its own before the reads are combined: a record of another pull request (a
        # MERGEABLE #8 for the read of #7), two records in one response (a CONFLICTING #7, then a
        # MERGEABLE #8), and an array holding the record are each a malformed response naming the read,
        # cannot-evaluate (exit 2) at once: one read, no runs read.
        identities = []
        for served in ({"records": {"7": pull(8, head_sha)}},
                       {"raw_records": {"7": json.dumps(pull(7, head_sha, mergeable=False))
                                        + json.dumps(pull(8, head_sha))}},
                       {"records": {"7": [pull(7, head_sha)]}}):
            rc, output, calls = fixture.invoke(green_polls, wait=True,
                                               probes=[probe_read(pull(7, head_sha), **served)])
            identities.append((rc, calls, fixture.probe_reads,
                               "the read of pull request #7 is not exactly one well-formed record of #7"
                               in output))
        check("ci/wait-pr-probe-record-identity", identities, [(2, 0, 1, True)] * 3)

        # A response gh returned that is not valid JSON (the listing, one pull request's own read, or the
        # read of the pull requests associated with the commit) is a failed read, labelled unparsable
        # where jq fails to parse it and re-read, as the runs reads under --wait re-read one: once, then
        # a MERGEABLE read waits; at every read, cannot-evaluate (exit 2) at the bound. It is never a
        # malformed response.
        unparsable = []
        for first in ({"listing_raw": "[[{", "records": {}},
                      probe_read(pull(12, head_sha), raw_records={"12": "<html>Bad Gateway</html>"}),
                      dict(probe_read(), associated_raw="<html>Bad Gateway</html>")):
            for probes in ([first, probe_read(pull(12, head_sha))], [first]):
                rc, output, calls = fixture.invoke(green_polls, wait=True, probes=probes)
                unparsable.append((rc, calls, fixture.probe_reads, "jq: parse error" in output,
                                   "unparsable pull-request response" in output,
                                   "malformed pull-request response" in output))
        check("ci/wait-pr-probe-not-json", unparsable,
              [(0, 5, 6, True, True, False), (2, 0, 12, True, True, False)] * 3)

        # Not found is gh's own line for an HTTP 404, "gh: Not Found (HTTP 404)" or "gh: HTTP 404",
        # matched whole: cannot-evaluate (exit 2) at once, also when gh writes another line after it.
        # Any other text is a failed read, re-read once before a MERGEABLE read waits: a 502 whose
        # diagnostic names pulls/4041, "HTTP 4041", "HTTP 404" inside a 502's line, a 502 whose message
        # says Not Found, and "HTTP 404" followed by more text on its line.
        gateway = "gh: HTTP 502: Bad Gateway (https://api.github.com/repos/fixture/repository/pulls/4041)"
        statuses = []
        for probes in ([probe_read(pull(4041, head_sha), record_errors={"4041": gateway}),
                        probe_read(pull(4041, head_sha))],
                       [{"error": "gh: HTTP 4041"}, probe_read(pull(12, head_sha))],
                       [{"error": "gh: HTTP 502: upstream replied HTTP 404"}, probe_read(pull(12, head_sha))],
                       [{"error": "gh: Bad Gateway: upstream replied Not Found (HTTP 502)"},
                        probe_read(pull(12, head_sha))],
                       [{"error": "gh: HTTP 404 (https://api.github.com/repos/fixture/repository/pulls)"},
                        probe_read(pull(12, head_sha))],
                       [{"error": "gh: HTTP 404"}],
                       # Coverage of a rule the script already had before lines were matched whole (it
                       # also refused any text naming Not Found at once): this vector passes there too.
                       [{"error": "gh: Not Found (HTTP 404)\ngh: This API operation needs the \"repo\" "
                                  "scope. To request it, run:  gh auth refresh -h github.com -s repo"}]):
            rc, output, calls = fixture.invoke(green_polls, wait=True, probes=probes)
            statuses.append((rc, calls, fixture.probe_reads, "cannot evaluate" in output))
        check("ci/wait-pr-probe-not-found-status", statuses, [(0, 5, 6, False)] * 5 + [(2, 0, 1, True)] * 2)

        # gh's own lines for the other statuses no re-read changes are cannot-evaluate (exit 2) at once,
        # on the listing and on the associated read alike: 401 (bad credentials, authentication
        # required, or no message), 403 not accessible, and 403 SAML enforcement. Another 403 (a rate
        # limit), a 502 whose message says bad credentials, and a 502 line carrying the SAML form are
        # failed reads, re-read once before a MERGEABLE read waits. So is text naming "Resource not
        # accessible" that is not gh's whole 403 line: without its status, or inside a 502's line.
        saml = ("gh: Resource protected by organization SAML enforcement. You must grant your Personal "
                "Access token access to this organization. (HTTP 403)")
        permanent = []
        for probes in ([{"error": "gh: Bad credentials (HTTP 401)"}],
                       [{"error": "gh: Requires authentication (HTTP 401)"}],
                       [{"error": "gh: HTTP 401"}],
                       [{"error": saml}],
                       # Coverage of a rule the script already had before lines were matched whole (it
                       # also refused any text naming Resource not accessible at once): passes there too.
                       [{"error": "gh: Resource not accessible by integration (HTTP 403)"}],
                       [dict(probe_read(), associated_error=saml)],
                       [dict(probe_read(), associated_error="gh: Resource not accessible by personal "
                                                           "access token (HTTP 403)")],
                       # Coverage of a rule the script already had (any other 403 was re-read there
                       # too): this vector passes there too.
                       [{"error": "gh: API rate limit exceeded for user ID 1. (HTTP 403)"},
                        probe_read(pull(12, head_sha))],
                       # Coverage of a rule the script already had (a 401 message inside another line
                       # was re-read there too): this vector passes there too.
                       [{"error": "gh: Bad Gateway: upstream replied Bad credentials (HTTP 502)"},
                        probe_read(pull(12, head_sha))],
                       [dict(probe_read(), associated_error="gh: HTTP 502: " + saml),
                        probe_read(pull(12, head_sha))],
                       [dict(probe_read(), associated_error="gh: Bad credentials (HTTP 401)")],
                       [dict(probe_read(), associated_error="gh: Requires authentication (HTTP 401)")],
                       [dict(probe_read(), associated_error="gh: HTTP 401")],
                       [{"error": "gh: Resource not accessible by personal access token"},
                        probe_read(pull(12, head_sha))],
                       [{"error": "gh: HTTP 502: upstream replied Resource not accessible by integration "
                                  "(HTTP 403)"},
                        probe_read(pull(12, head_sha))]):
            rc, output, calls = fixture.invoke(green_polls, wait=True, probes=probes)
            permanent.append((rc, calls, fixture.probe_reads, "cannot evaluate" in output))
        check("ci/wait-pr-probe-permanent-forms", permanent,
              [(2, 0, 1, True)] * 7 + [(0, 5, 6, False)] * 3 + [(2, 0, 1, True)] * 3 + [(0, 5, 6, False)] * 2)

        # Combining the reads keeps only a pull request still open at this head into the base: one listed
        # into main whose own read, though MERGEABLE, shows it retargeted to dev, or at another head,
        # changed between the reads, so it is re-read, never waited on; the next read refuses (#14 into
        # dev), at once here because its read of the associated pull requests succeeds and shows no
        # merge (where that read fails at every read, the probe is cannot-evaluate, exit 2, at its 12th
        # read: see ci/wait-pr-probe-associated-read), or the last read refuses (no open pull request at
        # this head).
        kept = []
        for record, then, cause in ((pull(14, head_sha, base="dev"), pull(14, head_sha, base="dev"),
                                     "targets main now (#14 into dev)"),
                                    (pull(14, "e" * 40), pull(14, "e" * 40),
                                     "no open pull request has {} as its head now".format(head_sha))):
            rc, output, calls = fixture.invoke(
                green_polls, wait=True,
                probes=[probe_read(pull(14, head_sha), records={"14": record}), probe_read(then)])
            kept.append((rc, calls, fixture.probe_reads,
                         "changed between the listing and its own read" in output, cause in output))
        check("ci/wait-pr-probe-merge-keeps-base", kept, [(1, 0, 2, True, True), (1, 0, 12, True, True)])

        # A listing of no pages at all is malformed, cannot-evaluate (exit 2) at once, never read as no
        # open pull request; one empty page is the empty listing, refused as no open pull request at
        # the last read.
        empty = []
        for listing in ([], [[]]):
            rc, output, calls = fixture.invoke(green_polls, wait=True,
                                               probes=[{"listing": listing, "records": {}}])
            empty.append((rc, calls, fixture.probe_reads, "malformed pull-request listing" in output))
        check("ci/wait-pr-probe-empty-page-list", empty, [(2, 0, 1, True), (1, 0, 12, False)])

        # Re-reads are 5 seconds apart: two UNKNOWN reads, then a MERGEABLE one, sleep 5 seconds twice
        # before the first poll; the 15-second poll sleeps then follow until the wait settles green.
        rc, _output, calls = fixture.invoke(green_polls, wait=True,
                                            probes=[undecided, undecided, probe_read(pull(11, head_sha))])
        check("ci/wait-pr-probe-retry-spacing", (rc, calls, fixture.probe_reads, fixture.sleeps),
              (0, 5, 7, [5, 5, 15, 15, 15, 15]))

        # One listed record per clause of the record check, each otherwise an open pull request at this
        # head into main (not an object; number not a number, 0, -1, or 15.5; state not a string; head
        # not an object; head SHA not a string; base not an object; base branch not a string, or
        # empty), and one single read per clause of the .mergeable check (absent, or the string "true"):
        # each is refused by the jq program's own check, cannot-evaluate (exit 2) at once. A single read
        # is refused by its own check, naming the read, before the combined reads' check could.
        well_formed = {"number": 15, "state": "open", "head": {"sha": head_sha}, "base": {"ref": "main"}}
        clauses = []
        for item in ("#15", dict(well_formed, number="15"), dict(well_formed, number=0),
                     dict(well_formed, number=-1), dict(well_formed, number=15.5),
                     dict(well_formed, state=1), dict(well_formed, head=head_sha),
                     dict(well_formed, head={"sha": 15}), dict(well_formed, base="main"),
                     dict(well_formed, base={"ref": 15}), dict(well_formed, base={"ref": ""})):
            rc, output, calls = fixture.invoke(green_polls, wait=True,
                                               probes=[{"listing": [[item]], "records": {}}])
            clauses.append((rc, calls, fixture.probe_reads, "malformed pull-request listing" in output))
        for record in (well_formed, pull(15, head_sha, mergeable="true")):
            rc, output, calls = fixture.invoke(
                green_polls, wait=True, probes=[probe_read(pull(15, head_sha), records={"15": record})])
            clauses.append((rc, calls, fixture.probe_reads,
                            "malformed pull-request response: the read of pull request #15 is not exactly "
                            "one well-formed record of #15" in output))
        check("ci/wait-pr-probe-malformed-clauses", clauses, [(2, 0, 1, True)] * 13)

        # The listing's own clauses, each reached by a response jq parses: two documents in one response
        # (a page list holding a pull request at this head, then an empty one), a page that is an object
        # holding such a pull request rather than an array of them, and a pull request numbered 1e300,
        # which jq prints as 1E+300 and the script refuses before it builds a URL from it. Each is
        # cannot-evaluate (exit 2) at once, although the pull request it carries reads MERGEABLE.
        # Controls guarding rules the script already had before it read the associated pull requests:
        # every vector here passes there too, so this check guards those rules and does not show that
        # change.
        listed_at_head = {key: value for key, value in pull(24, head_sha).items() if key != "mergeable"}
        listing_malformed = "malformed pull-request response: the open pull-request listing"
        shapes = []
        for probe, marker in (
                (dict(probe_read(pull(24, head_sha)),
                      listing_raw=json.dumps([[listed_at_head]]) + json.dumps([[]])), listing_malformed),
                (dict(probe_read(pull(24, head_sha)), listing=[{"24": listed_at_head}]), listing_malformed),
                ({"listing": [[dict(listed_at_head, number=1e300)]], "records": {}},
                 "unexpected pull-request probe output")):
            rc, output, calls = fixture.invoke(green_polls, wait=True, probes=[probe])
            shapes.append((rc, calls, fixture.probe_reads, marker in output))
        check("ci/wait-pr-probe-listing-shape", shapes, [(2, 0, 1, True)] * 3)

        # No open pull request at this head is re-read like UNKNOWN, 5 seconds apart, at most 12 reads
        # per probe, because a listing read just after a push can still show the pull request's old
        # head: the head appearing on the 3rd read waits and settles green, and so does it appearing on
        # the 11th (just inside the bound) or the 12th (exactly at it); still absent at the 12th is
        # refused (exit 1) with no 13th read, although a 13th would have read MERGEABLE (just outside).
        old_head = probe_read(pull(23, "e" * 40))
        appeared = probe_read(pull(23, head_sha))
        reread = []
        for absent_reads in (2, 10, 11, 12):
            rc, output, calls = fixture.invoke(green_polls, wait=True,
                                               probes=[old_head] * absent_reads + [appeared])
            reread.append((rc, calls, fixture.probe_reads, fixture.sleeps, "refusing to wait" in output))
        check("ci/wait-pr-probe-none-reread", reread,
              [(0, 5, 7, [5] * 2 + [15] * 4, False), (0, 5, 15, [5] * 10 + [15] * 4, False),
               (0, 5, 16, [5] * 11 + [15] * 4, False), (1, 0, 12, [5] * 11, True)])

        # A pull request merged into the base with this head, read when no open pull request at this
        # head into the base reads MERGEABLE, is not refused: the probe says so and stops, and the runs
        # decide as for any other wait. MERGEABLE on three probes, then merged on the fourth (as
        # auto-merge can do once the checks pass, within the settle window): the wait settles green on
        # the runs, with no fifth probe. Merged after one green poll, then a failed run: exit 1 on the
        # run. Merged at the first probe, with no run ever registered: TIMEOUT (exit 1). The merge is
        # never a pass. An open pull request at this head into dev only, beside the merge into main, is
        # no refusal either: the wait settles green on the runs. The commit's associated pull requests
        # are read by this commit's SHA, once: never on a read that finds a MERGEABLE pull request.
        merged_read = probe_read(associated=[closed_pull(21, head_sha)])
        mergeable_read = probe_read(pull(21, head_sha))
        associated_argv = ["api", "repos/{}/commits/{}/pulls?per_page=100".format(FIXTURE_REPO, head_sha),
                           "--paginate", "--slurp"]
        merged = []
        for polls, probes, timeout in ((green_polls, [mergeable_read] * 3 + [merged_read], "900"),
                                       ([[page([success_a])], [page([failed_b])]],
                                        [mergeable_read, merged_read], "900"),
                                       ([[page([])]], [merged_read], "60"),
                                       (green_polls, [probe_read(pull(26, head_sha, base="dev"),
                                                                 associated=[closed_pull(21, head_sha)])],
                                        "900")):
            rc, output, calls = fixture.invoke(polls, wait=True, timeout=timeout, probes=probes)
            merged.append((rc, calls, fixture.probe_reads,
                           "the pull requests associated with {} show pull request #21 with this head "
                           "merged into main".format(head_sha) in output,
                           "refusing to wait" in output,
                           sum(argv == associated_argv for argv in fixture.gh_argv)))
        check("ci/wait-pr-probe-merged-into-base", merged,
              [(0, 5, 4, True, False, 1), (1, 2, 2, True, False, 1), (1, 5, 1, True, False, 1),
               (0, 5, 1, True, False, 1)])

        # A pull request at this head closed without a merge, one merged into another base (dev, under a
        # plain --wait), one merged into main whose head was another commit (this commit an earlier one
        # of it), and a record still open although it carries a merge time each keep the refusal:
        # MERGEABLE on three probes, then that on every read of the fourth probe, refused (exit 1) at
        # its 12th read, with no runs read after the third poll.
        unmerged = []
        for record in (closed_pull(22, head_sha, merged=False), closed_pull(22, head_sha, base="dev"),
                       closed_pull(22, "e" * 40), dict(closed_pull(22, head_sha), state="open")):
            rc, output, calls = fixture.invoke(
                green_polls, wait=True,
                probes=[probe_read(pull(22, head_sha))] * 3 + [probe_read(associated=[record])])
            unmerged.append((rc, calls, fixture.probe_reads,
                             "no open pull request has {} as its head now, and the pull requests "
                             "associated with {} show none with this head merged into "
                             "main".format(head_sha, head_sha) in output,
                             "with this head merged into main; the pull-request probe stops" in output))
        check("ci/wait-pr-probe-closed-unmerged", unmerged, [(1, 3, 15, True, False)] * 4)

        # The associated read's records are checked like the listing's: one clause each of the merge
        # time check (absent, not a string, or empty) and of the record check (number not a number), and
        # a response of no pages at all, are each cannot-evaluate (exit 2) at once, never read as merged
        # or unmerged. A failed associated read other than a permanent form is re-read: once, then a
        # MERGEABLE read waits; at every read, with no open pull request listed, or only one into
        # another base (#7 into dev), cannot-evaluate (exit 2) at the 12th read with no runs read, never
        # the exit 1 refusal of no open pull request or of another base. That second case is a control
        # guarding a rule the script already had before the associated read also followed a
        # CONFLICTING, UNKNOWN or changed read (a listing into another base was already followed by
        # it): it passes there too, so it does not show that change.
        merged_record = closed_pull(25, head_sha)
        associated_malformed = ("malformed pull-request response: the pull requests associated with "
                                "{}".format(head_sha))
        associated_cases = []
        for probe in (probe_read(associated=[{key: value for key, value in merged_record.items()
                                              if key != "merged_at"}]),
                      probe_read(associated=[dict(merged_record, merged_at=1)]),
                      probe_read(associated=[dict(merged_record, merged_at="")]),
                      probe_read(associated=[dict(merged_record, number="25")]),
                      dict(probe_read(), associated_raw="[]")):
            rc, output, calls = fixture.invoke(green_polls, wait=True, probes=[probe])
            associated_cases.append((rc, calls, fixture.probe_reads, associated_malformed in output))
        rc, output, calls = fixture.invoke(
            green_polls, wait=True,
            probes=[dict(probe_read(), associated_error="gh: HTTP 502"), probe_read(pull(25, head_sha))])
        associated_cases.append((rc, calls, fixture.probe_reads, "cannot evaluate" in output))
        for listed in (probe_read(), probe_read(pull(7, head_sha, base="dev"))):
            rc, output, calls = fixture.invoke(
                green_polls, wait=True, probes=[dict(listed, associated_error="gh: HTTP 502")])
            associated_cases.append((rc, calls, fixture.probe_reads,
                                     "cannot evaluate: the pull-request read failed" in output))
        check("ci/wait-pr-probe-associated-read", associated_cases,
              [(2, 0, 1, True)] * 5 + [(0, 5, 6, False)] + [(2, 0, 12, True)] * 2)

        # A pull request merged into main with this head, read in the associated pull requests, lets the
        # runs decide also when the listing still holds an open pull request at this head into main that
        # does not read MERGEABLE: a listing read just after the merge that still shows the merged #27
        # open, though its own read shows it closed; a CONFLICTING sibling #28 beside the merged #21;
        # and an UNKNOWN sibling #29 beside it. Each says so, stops probing after its first read, and
        # settles green on the runs. Without that read each ends cannot-evaluate (exit 2) after 12
        # reads, or is refused (exit 1) at once.
        # Controls, where the associated read shows no merge into main at this head: #27 closed without
        # a merge, a sibling merged into dev, and one merged at another head each keep the outcome the
        # listing and own reads give (changed or UNKNOWN at every read, cannot-evaluate at the 12th; a
        # CONFLICTING #28 refused at once), reading the associated pull requests on every read; against
        # the script before this rule they differ only in the associated reads counted (and in the
        # CONFLICTING refusal's text, which now names the merge). An associated read that fails at every
        # read beside the CONFLICTING #28 is a failed read, cannot-evaluate (exit 2) at the 12th read,
        # never the refusal, which that script gave at once. A MERGEABLE #30 beside the merged #21 keeps
        # its path: the probe never reads the associated pull requests, and runs before every poll, as
        # it did there. That last vector is a control guarding an older rule: it passes against the
        # script before this rule too, so it does not show that change.
        stale = pull(27, head_sha, state="closed", mergeable=None)
        conflicted_sibling = pull(28, head_sha, mergeable=False)
        unknown_sibling = pull(29, head_sha, mergeable=None)
        merged_sibling = closed_pull(21, head_sha)
        merged_note = "with this head merged into main; the pull-request probe stops"
        shown = "the pull requests associated with {} show pull request ".format(head_sha)
        beside = []
        for probe, marker in (
                (probe_read(pull(27, head_sha), records={"27": stale},
                            associated=[closed_pull(27, head_sha)]), shown + "#27 " + merged_note),
                (probe_read(conflicted_sibling, associated=[merged_sibling]),
                 shown + "#21 " + merged_note),
                (probe_read(unknown_sibling, associated=[merged_sibling]),
                 shown + "#21 " + merged_note),
                (probe_read(pull(27, head_sha), records={"27": stale},
                            associated=[closed_pull(27, head_sha, merged=False)]),
                 "cannot evaluate: the open pull request into main for this head changed between the "
                 "listing and its own read, still after 12 reads"),
                (probe_read(conflicted_sibling, associated=[closed_pull(21, head_sha, base="dev")]),
                 "pull request #28 CONFLICTING with main now, and the pull requests associated with "
                 "{} show none with this head merged into main".format(head_sha)),
                (probe_read(unknown_sibling, associated=[closed_pull(21, "e" * 40)]),
                 "pull request #29 as UNKNOWN (not yet computed), still after 12 reads"),
                (dict(probe_read(conflicted_sibling), associated_error="gh: HTTP 502"),
                 "cannot evaluate: the pull-request read failed (raw: gh: HTTP 502), still after 12 "
                 "reads"),
                (probe_read(pull(30, head_sha), associated=[merged_sibling]), merged_note)):
            rc, output, calls = fixture.invoke(green_polls, wait=True, probes=[probe])
            beside.append((rc, calls, fixture.probe_reads,
                           sum(argv == associated_argv for argv in fixture.gh_argv), marker in output))
        check("ci/wait-pr-probe-merged-beside-candidate", beside,
              [(0, 5, 1, 1, True)] * 3
              + [(2, 0, 12, 12, True), (1, 0, 1, 1, True), (2, 0, 12, 12, True), (2, 0, 12, 12, True),
                 (0, 5, 5, 0, False)])

        # A merge time is a timestamp as GitHub writes one, YYYY-MM-DDTHH:MM:SSZ: a record closed into
        # main at this head whose .merged_at is any other string is a malformed associated record,
        # cannot-evaluate (exit 2) at once, never a merge that lets the runs decide (exit 0 on green
        # runs). Text ("not-a-timestamp", "x"), a date without a time, an offset other than Z (+00:00
        # and +02:00; GitHub's REST documentation states it writes every timestamp in UTC with Z, not
        # verified live), fractional seconds, a trailing newline, and a calendar time that does not exist
        # (February 29 of 2026, an hour of 24), each refused by the read back through fromdate and
        # todate; a year of one digit (1-01-01T00:00:00Z, which todate writes back unchanged), refused
        # by the form alone; and "not-a-timestamp" beside a CONFLICTING #28, where it would also have
        # let the runs decide. Controls, with a valid timestamp: 2026-10-15T12:00:00Z and the leap day
        # 2024-02-29T23:59:59Z each let the runs decide, and the note names only the merged pull
        # request, with no claim about others open at this head (here the CONFLICTING #28).
        merged_at_cases = []
        merged_only = ("the pull requests associated with {} show pull request #21 with this head merged "
                       "into main; the pull-request probe stops for the rest of this wait".format(head_sha))
        for merged_at, listed in (("not-a-timestamp", ()), ("x", ()), ("2026-10-15", ()),
                                  ("2026-10-15T12:00:00+00:00", ()), ("2026-10-15T14:00:00+02:00", ()),
                                  ("2026-10-15T12:00:00.000Z", ()), ("2026-10-15T12:00:00Z\n", ()),
                                  ("2026-02-29T12:00:00Z", ()), ("2026-10-15T24:00:00Z", ()),
                                  ("1-01-01T00:00:00Z", ()),
                                  ("not-a-timestamp", (pull(28, head_sha, mergeable=False),)),
                                  ("2026-10-15T12:00:00Z", ()), ("2024-02-29T23:59:59Z", ()),
                                  ("2026-10-15T12:00:00Z", (pull(28, head_sha, mergeable=False),))):
            record = dict(closed_pull(21, head_sha), merged_at=merged_at)
            rc, output, calls = fixture.invoke(
                green_polls, wait=True, probes=[probe_read(*listed, associated=[record])])
            merged_at_cases.append((rc, calls, fixture.probe_reads, associated_malformed in output,
                                    merged_only in output))
        check("ci/wait-pr-probe-merged-at-timestamp", merged_at_cases,
              [(2, 0, 1, True, False)] * 11 + [(0, 5, 1, False, True)] * 3)

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
    _finalisation_cases(). A report_path arms the execution-report finalizer before the try block, so
    a refused arm exits 2 before any check runs.
    """
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    try:
        run_checks()
        _reconcile()
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


def _reconcile():
    # Only a run in which every check was evaluated is reconciled: a check that recorded a harness error
    # never executed, and would also be reported missing.
    if HARNESS_ERRORS:
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
            exit_text = "uncaught RuntimeError: <unprintable RuntimeError>"
            value_text = "uncaught _UnprintableError: _UnprintableError()"
            unprintable_path = _UnprintablePath(str(absent))
            # (seed a failure first?, checks run, manifest, code, stdout texts, stderr texts,
            # and optionally "stream:fault" from faults, several joined by "+", then the stdout PASS state
            # when not the default: "pass" for code 0, else "none")
            for fail, run, path, code, out_texts, err_texts, *faulting in (
                    (False, passes, own_only, 0, ["SELF-TEST PASS"], []),
                    (True, passes, own_only, 1, [seeded], []),
                    (True, recorded, own_only, 1, [seeded], ["seeded/not-evaluated"]),
                    (False, recorded, not_evaluated, 2, [], ["seeded/not-evaluated"]),
                    (True, duplicated, own_only, 1, [seeded, duplicate],
                     [own + ": duplicate check id"]),
                    (False, duplicated_passing, own_only, 2, [], [own + ": duplicate check id"]),
                    (False, unrepresentable, own_only, 1,
                     [own + ": got <unprintable _UnrepresentableValue>, want 0"], []),
                    (False, passes, never_run, 1, ["execution-set/missing: seeded/never-run"], []),
                    (True, passes, absent, 1, [seeded], ["cannot read"]),
                    (False, passes, absent, 2, [], ["cannot read"]),
                    (True, passes, scalar_suite, 1, [seeded], ["missing or malformed suite"]),
                    (False, passes, scalar_suite, 2, [], ["missing or malformed suite"]),
                    (True, exits, own_only, 1, [seeded], ["uncaught SystemExit: 0"]),
                    (False, exits, own_only, 2, [], ["uncaught SystemExit: 0"]),
                    (True, raises, own_only, 1, [seeded], ["uncaught TimeoutExpired"]),
                    (True, passes, _FaultingPath(), 1, [seeded], ["uncaught RuntimeError"]),
                    (False, passes, _FaultingPath(), 2, [], ["uncaught RuntimeError"]),
                    (True, exits_on_text, own_only, 1, [seeded], [exit_text]),
                    (False, exits_on_text, own_only, 2, [], [exit_text]),
                    (True, unprintable, own_only, 1, [seeded], [value_text]),
                    (False, unprintable, own_only, 2, [], [value_text]),
                    # The PASS print fails: the void line still reaches stderr.
                    (False, passes, own_only, 2, [], [failed] + void, "stdout:write"),
                    (True, passes, own_only, 1, [], [failed], "stdout:write"),
                    (True, recorded, own_only, 1, [], ["seeded/not-evaluated", both_fail],
                     "stdout:write"),
                    (False, raises, own_only, 2, [], [], "stderr:write"),
                    (False, duplicated, own_only, 1, [duplicate], [], "stderr:write"),
                    (False, nameless, own_only, 2, [], ["uncaught <unnamed type>"]),
                    (False, passes, unprintable_path, 2, [],
                     ["cannot read <unprintable _UnprintablePath>: [Errno 2]"]),
                    # The PASS line's own flush fails: exit 2, and the void line follows the PASS text.
                    (False, passes, own_only, 2, void, [failed] + void, "stdout:flush", "void"),
                    # Output the checks left pending cannot be flushed: no PASS line at all.
                    (False, noisy_out, own_only, 2, [], [failed, "(cannot evaluate)"],
                     "stdout:flush"),
                    # The last stderr flush is attempted too: its failure follows the summary line.
                    (False, noisy_err, own_only, 2, [],
                     ["(cannot evaluate)\nSELF-TEST HARNESS ERROR: " + failed], "stderr:flush"),
                    # The failure list cannot flush: the stderr summary line still follows.
                    (True, passes, own_only, 1, [seeded], [failed, both_fail], "stdout:flush"),
                    # A failing stderr flush, pending or not, does not stop the failure list on stdout.
                    (True, duplicated, own_only, 1, [seeded, duplicate],
                     [own + ": duplicate check id", both_fail], "stderr:flush"),
                    (True, passes, own_only, 1, [seeded], [failed], "stderr:flush-always"),
                    # A stdout closed before the run discards the PASS line; the code is still 0.
                    (False, passes, own_only, 0, [], [], "stdout:closed", "none"),
                    # The PASS line's line break is refused after its text was written: the void line
                    # still starts a line of its own.
                    (False, passes, own_only, 2, [], [failed] + void, "stdout:newline-once", "void"),
                    # A refused FAIL header or failure line does not stop the failure lines after it.
                    (True, passes, own_only, 1, ["  - " + seeded], [failed, both_fail],
                     "stdout:refuse-header"),
                    (True, duplicated, own_only, 1, ["SELF-TEST FAIL:\n", duplicate],
                     [failed, both_fail], "stdout:refuse-seeded"),
                    # A refused stderr summary line does not stop the last stderr flush delivering the
                    # diagnostic that records it, in either branch.
                    (True, recorded, own_only, 1, [seeded], ["seeded/not-evaluated", failed],
                     "stderr:refuse-summary-buffered"),
                    (False, recorded, not_evaluated, 2, [], ["seeded/not-evaluated", failed],
                     "stderr:refuse-summary-buffered"),
                    # Buffered streams: each result line reaches the reader only through its flush.
                    (False, passes, own_only, 0, ["SELF-TEST PASS"], [], "stdout:buffered"),
                    (True, passes, own_only, 1, ["SELF-TEST FAIL:\n  - " + seeded], [],
                     "stdout:buffered"),
                    (False, recorded, not_evaluated, 2, [], ["(cannot evaluate)"],
                     "stderr:buffered"),
                    (True, recorded, own_only, 1, [seeded], [both_fail], "stderr:buffered"),
                    # The PASS flush fails once: the void line's own flush delivers both lines.
                    (False, passes, own_only, 2, void, [failed] + void, "stdout:buffered-flush-once",
                     "void"),
                    # The stderr void line reaches the reader only through the last stderr flush.
                    (False, passes, own_only, 2, [], [failed] + void,
                     "stdout:write+stderr:buffered"),
                    # A refused stderr void line does not stop the last stderr flush.
                    (False, passes, own_only, 2, void, [failed],
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
                        got = main(None, run)
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


def _end(code, report_path=None):
    # A run that armed the execution-report finalizer ends through exit_with(), which records the code
    # main() computed and raises it; at interpreter exit the finalizer flushes both streams and writes
    # the report, and every refusal of the finalizer (a stream it cannot flush included) ends the run
    # with 2 and no report (tools/_selftest_exit_report.py). os._exit would skip the finalizer.
    if report_path is not None:
        _selftest_exit_report.exit_with(code)
    # Any other run: os._exit, because _report() has flushed both streams or recorded each flush that
    # failed, and the interpreter's exit-time flush must not replace the code main() computed (a failing
    # final flush would otherwise end the run with 120). _finalisation_cases() runs this ending in a
    # child whose stdout cannot flush.
    os._exit(code)


if __name__ == "__main__":
    _REPORT_PATH = _parse_argv(sys.argv[1:])
    _end(main(_REPORT_PATH), _REPORT_PATH)
