#!/usr/bin/env python3
"""Python host-patch pin gate (PYTHON-HOST-PIN): the single source .aiqt/python-host.toml names the exact
CPython patch release the maintainers' host runs, and the host-patch CI jobs run their suites on exactly
that release.

Every other job in .github/workflows/quality.yml that sets up Python pins python-version '3.14' (the
Quality gates job, which aggregates the others, sets up none). With check-latest off, its default,
setup-python may resolve that pin to a 3.14 already on the runner rather than the newest patch release,
while the maintainers' host runs one fixed distribution patch release, so a change can pass every local
suite and fail only in CI on a patch-release difference. The host-patch jobs (HOST_JOBS) run the suites a
newer patch release most often breaks on the host's own patch release, beside the '3.14' jobs of
quality.yml, which stay required and unchanged. A passing local suite on the host patch then predicts a
required CI leg, and a failure only on a '3.14' leg is diagnostic evidence of a patch-release difference,
not proof that the change is innocent. This gate keeps the workflow's top level and each host-patch job
whole and the statements of that patch release in agreement.

Legs, in order:
  source       the single source is a regular UTF-8 file that parses and carries exactly the keys
               format-version (the integer 1) and python-host-patch (a "MAJOR.MINOR.PATCH" string with no
               leading zero and at most four digits in each component, PATCH_RE); anything else is
               cannot-evaluate, and so is a source that tomllib refuses with a RecursionError (deep nesting)
               or a bare ValueError (an over-long integer), and a component that does not convert.
  jobs         .github/workflows/quality.yml carries each HOST_JOBS job, and its top level and each such
               job are whole (JOB_RULES):
                 workflow-keys  the workflow carries no top-level key but name, on, permissions and jobs
                                (TOP_KEYS): a top-level defaults: or env: changes how every run step of
                                every job executes (defaults: run: shell: true {0} makes each step exit 0
                                without running its command);
                 job-keys       the job carries no key but name, runs-on and steps (an if:,
                                continue-on-error:, env:, defaults:, container: or strategy: can skip, mask,
                                move or change what it runs);
                 runner         runs-on is RUNNER, ubuntu-24.04: the setup-python versions manifest lists a
                                patch release's Linux builds by Ubuntu version, so the job names its image;
                 checkout       the first step is the checkout step as written (uses: CHECKOUT_USES and a
                                with: of exactly CHECKOUT_INPUTS, no other key), and no other step uses a
                                checkout action: a ref:, repository: or path: input, or a second checkout,
                                can put another tree under the gate and the suite;
                 step-env       no step of the job carries env:: a step env: changes what an otherwise
                                accepted run: command does (a BASH_ENV value, or a write to GITHUB_PATH
                                or GITHUB_ENV through it), so env: on any step, held or extra, is a
                                finding, and the job-keys rule keeps refusing a job-level env:;
                 setup          exactly one step uses setup-python; it carries no key but name, uses and
                                with:, its with: holds only python-version, and that is the one line in the
                                job that names python-version (a job-level env: or another key never counts
                                as the pin);
                 pin            that python-version is the host patch;
                 gate-step      the step right after the setup-python step is the Python host pin step, an
                                executable step (no key but name and run) whose run: is GATE_RUN;
                 suite-step     an executable step after the setup-python step runs the job's suite,
                                HOST_SUITES (opf-host-patch: the OPF tooling self-test;
                                git-fixture-env-host-patch: the git-fixture-env behaviour self-test);
                 outside-judge  the judge step holds its place: exactly one step outside the HOST_JOBS
                                jobs runs this gate with --jobs-only (JOBS_ONLY_RUN) as its own run:
                                command, that step carries no key but name and run, no non-comment
                                line of a HOST_JOBS job names --jobs-only, and no job that carries a
                                judge step, nor any step of such a job, carries env: (a job-level env:
                                can silence, in CI, every step of the carrying job; an env: on the
                                judge step the judge itself; and an env: on an earlier step every
                                later step of the job, through a GITHUB_ENV write, leaving the jobs
                                leg without its one CI run outside the host-patch jobs; an env: on a
                                step after the judge does not reach it, and is refused as policy so
                                the judge's job stays env-free). Each job outside HOST_JOBS any
                                non-comment line of which names the --jobs-only flag or this gate's
                                path (GATE_PATH) is read whole with the same line model as the
                                host-patch jobs (control characters refused, then _entry / _job_tree),
                                so a line of it this model cannot read (a job property at an
                                unexpected indent, a quoted or explicit key, a block or folded scalar,
                                a flow mapping, an anchor or an alias among them) is cannot-evaluate,
                                never skipped; a command-shaped value that is not a step's own run:
                                property (a with: input or a job-level run: among them) never counts
                                as the judge; and every step whose parsed run: equals the judge
                                command counts, whatever its spelling (a quoted value or a tab before
                                it among them), so a doubled judge in a spelling this model reads is a
                                finding, never a silent pass.
               So a top-level key outside TOP_KEYS, another runner or checkout, an env: on a
               host-patch job or on any of its steps, removing or moving a
               host-patch job, its setup-python step, its Python host pin step or its suite step, changing a
               job id or the command a held step runs, and disabling a held step (if:, continue-on-error:) are
               each a finding. A display name (name:) is not held: it changes neither what a step runs nor how
               the Quality gates job reads a job's result, which is by job id. Jobs are read by a line model.
               A line that still carries a carriage return once its one trailing CR is removed, or carries
               U+0085, U+2028 or U+2029 (LINE_BREAKS, the set the python-floor gate refuses), is
               cannot-evaluate: a YAML reader may break the line there and read a key this model does not see.
               A line of a host-patch job or of a judge-naming job, the job's header line included,
               carrying a form feed or a vertical tab (CONTROL_CHARS) is also cannot-evaluate: neither
               is a YAML line break, and this model refuses every such line rather than modelling which
               of them another reader accepts.
               A top-level line is a plain key, a colon, then nothing or a value (TOP_RE); any other top-level
               line (a quoted or explicit key, a flow mapping, a document marker) and a repeated top-level key
               are cannot-evaluate. Under the top-level jobs: key, a job is a plain key at indent 2 and its
               content sits at indent 4 or deeper; any other line there, and a repeated job id, is
               cannot-evaluate. Inside a HOST_JOBS job, each line is a block mapping entry, or a "- " list
               item holding one, at indent 10 or less: a plain key, a colon, then nothing, a one-line quoted
               scalar or a plain scalar that opens no flow, block scalar, anchor, alias or tag (ENTRY_RE). Any
               other line (a run: | block among them), a repeated key and a line the indentation does not
               place are cannot-evaluate, and so is a line (a comment included) that names python-version in
               any case and is not one strict pin line (PIN_LINE_RE).
  interpreter  the running interpreter (sys.version_info) is the host patch, a final release. A difference
               is a finding (exit 1): run locally, it reports that this interpreter is not the committed
               host patch, so a passing local suite does not predict the host-patch CI jobs, and
               tools/run_all_checks.sh FAILS there; run in a host-patch CI job, it reports that the job did
               not run the pinned release.

  check_python_host_pin.py              run every leg over this repository
  check_python_host_pin.py --jobs-only  run the source and jobs legs only and skip the interpreter leg:
                                        the CI step of the registry job of quality.yml, which runs
                                        OUTSIDE the host-patch jobs this gate judges, so an edit to
                                        those jobs cannot silence every CI run of the jobs leg, and
                                        which pins the floor '3.14', not the host patch, so the
                                        interpreter leg cannot run there (the outside-judge rule holds
                                        that step's placement and the freedom from env: of its job and
                                        that job's every step)
  check_python_host_pin.py --self-test  fixture trees for every leg and every job rule, plus red-on-revert:
                                        for each finding leg, a copy of this gate loaded through importlib
                                        with that leg's call removed passes the fixture the intact gate
                                        refuses; for each job rule, a copy with that rule removed from
                                        JOB_RULES passes each of that rule's cases; a copy without the
                                        line-break refusal reads each line-break case; a copy without
                                        the control-character refusal reads each form-feed and
                                        vertical-tab case; a copy whose source parse maps only
                                        TOMLDecodeError lets a parser RecursionError escape; and the
                                        --jobs-only CLI wiring runs through main() on a pristine copy,
                                        with a copy that stops forwarding the flag and a copy whose main
                                        returns 0 without evaluating each red

Exit convention: 0 every leg passes; 1 a finding; 2 usage or cannot-evaluate (a missing, unreadable,
non-regular, symlinked, non-UTF-8 or malformed source or workflow, a workflow line carrying a line-break
character, a line of a host-patch job or of a judge-naming job, their accepted job headers included,
carrying a form feed or a vertical tab, a top-level line that is not a plain key, a repeated top-level
key, a line the line model cannot place or read, a line of a job naming the --jobs-only judge or this
gate's path that the job model cannot read, a pin line outside the strict form, unavailable temporary
storage).

DISCLOSED RESIDUAL. The jobs leg reads only quality.yml: its top-level keys; in the HOST_JOBS jobs, their
keys, runner, checkout step, the env: of every step, setup-python step, Python host pin step and suite step;
and, outside those jobs, only the jobs naming the --jobs-only judge command's flag or this gate's path,
each read whole with the same job model: the judge step's placement and shape (no key but name and run)
and the env: of every job that carries a judge step and of every step of such a job
(outside-judge). Another step in a host-patch job is only partly held: this gate refuses its env:
(step-env) and a non-comment line naming --jobs-only (outside-judge), and accepts a step-level shell:,
working-directory: or if: on it, which changes what, where or whether that step runs; and what the suites
test is theirs: a step whose run: command starts another interpreter, appends to GITHUB_PATH or GITHUB_ENV,
changes the checked-out tree or uses a local or composite action is not judged here.
tools/check_ci_parity.py is the second holder for those edits: its step grammar accepts only name:, uses:,
run:, env: and with: as step keys, so a step-level shell:, working-directory: or if: is cannot-evaluate for
it (measured at this commit on an extra host-patch-job step carrying each of the three: this gate passed,
check_ci_parity refused), and in every job it refuses a uses: of an action other than actions/checkout and
actions/setup-python and a run: command outside its parity grammar. In CI the jobs leg runs inside the
host-patch jobs (their Python host pin steps) and once outside them, as the --jobs-only judge step of the
registry job, and the outside-judge rule holds that placement, so moving the judge into a host-patch job,
removing it, doubling it in any spelling the job model reads (a quoted run: value or a tab before the
value among them), demoting it to a value that is not a step's own run: property, putting a key beyond
name and run on the judge step, or an env: on a job that carries a judge or on any of that job's steps is
a finding, and a judge-naming job's line the job model cannot read (a re-indented job body, a quoted env
key, or a judge spelled as a block or folded scalar, a flow mapping, an anchor or an alias among them) is
cannot-evaluate, never skipped; the host-patch
jobs' own Python host pin steps report each in CI while those steps still run, and
tools/run_all_checks.sh reports each in every local run. What stays CI-blind is an edit that leaves no CI run of
the jobs leg at all. A top-level defaults: or env: silences, in CI, every run step of every job, the judge
step included, so CI cannot report it; this gate (workflow-keys) and check_ci_parity refuse it where they
run outside that workflow, in tools/run_all_checks.sh. An edit that silences every step of both host-patch
jobs (a job-level env:, a BASH_ENV value, in each) and with it silences or removes the judge (a
job-level env: on the registry job, which sits inside check_ci_parity's disclosed environment-value
residual, which that gate's docstring owns; an env: on any step of the registry job, the judge step itself
or an earlier step whose GITHUB_ENV write reaches it; or removing the judge step
together with its tools/run_all_checks.sh member, which keeps check_ci_parity's set parity) leaves no CI
run of the jobs leg, so CI cannot report it either; each such pairing is refused by this gate's jobs leg
run outside CI, in tools/run_all_checks.sh, which owns that surface, as a finding or, for a spelling the
job model cannot read (a re-indented registry body or a quoted env key among them), as cannot-evaluate (a
job-level defaults:, container:,
continue-on-error: or uses: on the registry job is a check_ci_parity finding, reported in
tools/run_all_checks.sh when CI is silenced). The grammar of the whole workflow,
the setup-python uses: shape and every other pin
belong to the python-floor gate (tools/check_python_floor.py), which accepts a pin of a patch release only
inside the HOST_JOBS jobs of quality.yml and only when it equals this source, and holds every other
python-version in any workflow to the floor exactly. GitHub's runtime is not modelled: when the runner image
retires or the host patch has no build for it, setup-python fails the job, closed and never on another patch
release. On 2026-10-12 the setup-python versions manifest (actions/python-versions) listed Linux x64 builds of
3.14.4 for Ubuntu 22.04 and 24.04 only, so the move off ubuntu-24.04 is one reviewed change of RUNNER, both
runs-on lines and, if needed, the host patch. The interpreter leg judges only the interpreter that runs this
gate; in CI it runs only in the HOST_JOBS jobs. Equal patch releases are not equal builds: a distribution
build's downstream patches and the system libraries an interpreter links (OpenSSL, SQLite, expat, zlib and
others) can differ between the host and the runner, and this gate does not compare them. It does not check
that the committed patch is what the host runs: when the host interpreter changes, the maintainer updates the
source and both host-patch pins in one reviewed change, and until then every local run fails on the
interpreter leg. CI cost: the two jobs run the OPF tooling self-test and the git-fixture-env suite a second
time in every run of the workflow, and an edit to a pull request's title or body starts a run (the workflow
runs on edited).

Run this gate isolated: python3 -I -B tools/check_python_host_pin.py
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_python_host_pin.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import importlib.util
import io
import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REL = ".aiqt/python-host.toml"
SOURCE_KEYS = {"format-version", "python-host-patch"}
WORKFLOW_REL = ".github/workflows/quality.yml"
# The host-patch jobs of WORKFLOW_REL, each with the suite it exists to run (the run: text of one executable
# step). tools/check_python_floor.py accepts a host-patch pin only inside these jobs; its HOST_JOBS names the
# same jobs, which the self-test holds.
HOST_SUITES = {
    "opf-host-patch": "python3 -I -B opf/tools/opf.py --self-test",
    "git-fixture-env-host-patch":
        "python3 -I -B tools/check_selftest_execution.py --suite git-fixture-env-selftest",
}
HOST_JOBS = tuple(HOST_SUITES)
# The path this gate runs as: a job outside HOST_JOBS any non-comment line of which names it or
# JOBS_ONLY_FLAG is judge-naming, and is read whole with the strict job model (_judge_findings).
GATE_PATH = "tools/check_python_host_pin.py"
# The step right after a host-patch job's setup-python step: this gate, which fails there when the job does
# not run the host patch.
GATE_RUN = "python3 -I -B " + GATE_PATH
# The runner image of each host-patch job. The setup-python versions manifest lists a patch release's Linux
# builds by Ubuntu version (on 2026-10-12, x64 builds of 3.14.4 for 22.04 and 24.04 only), so ubuntu-latest
# would fail the jobs once it moves to an image the host patch has no build for.
RUNNER = "ubuntu-24.04"
# The top-level keys the workflow may carry: a defaults: or env: block changes how every run step of every
# job executes.
TOP_KEYS = ("name", "on", "permissions", "jobs")
JOB_KEYS = {"name", "runs-on", "steps"}
# The first step of each host-patch job, as written: another ref, repository or path can put another tree
# under the gate and the suite.
CHECKOUT_USES = "actions/checkout@v4"
CHECKOUT_KEYS = {"uses", "with"}
CHECKOUT_INPUTS = {"fetch-depth": "0", "fetch-tags": "true"}
SETUP_KEYS = {"name", "uses", "with"}
RUN_KEYS = {"name", "run"}
# The rules of the jobs leg; the self-test removes each from a copy of this gate to show its cases red on
# revert.
JOB_RULES = frozenset({"workflow-keys", "job-keys", "runner", "checkout", "step-env", "setup", "pin",
                       "gate-step", "suite-step", "outside-judge"})
# The jobs leg run outside the host-patch jobs: the one step of quality.yml outside HOST_JOBS that runs
# this gate with --jobs-only (in the committed workflow, the registry job's judge step). The outside-judge
# rule holds its placement: no non-comment host-patch-job line names the flag, exactly one step outside
# those jobs carries the command as its own run: property and no key but name and run, and no job that
# carries a judge step, nor any step of such a job, carries env:. A job any non-comment line of which
# names the flag or GATE_PATH is read whole with the strict job model (_entry / _job_tree); whether a
# command-shaped value is a step's run: is judged on that model, never textually, and a line of such a
# job that model cannot read is cannot-evaluate, never skipped.
JOBS_ONLY_FLAG = "--jobs-only"
JOBS_ONLY_RUN = GATE_RUN + " " + JOBS_ONLY_FLAG
# Control characters refused in any line of a host-patch job or of a judge-naming job, the job's header
# line included: neither is a YAML line break (LINE_BREAKS), and before this refusal some such lines
# passed this line model while others (a header comment carrying one) were accepted or cannot-evaluate
# by position, so the refusal holds every one of them out rather than modelling which.
CONTROL_CHARS = {"\f": "a form feed", "\v": "a vertical tab"}
# MAJOR.MINOR.PATCH, no leading zero, at most four digits in each component.
PATCH_RE = re.compile(r"([1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")
PIN_KEY = "python-version"
# The one accepted spelling of a pin line, the python-floor gate's strict form: optional space indentation,
# an optional `- ` list marker, the bare key in lower case, optional spaces, a colon, one or more spaces,
# exactly one plain, single-quoted or double-quoted scalar of [A-Za-z0-9._+-] characters, then optional
# whitespace and an optional `#` comment.
PIN_LINE_RE = re.compile(
    r" *(?:- +)?python-version *: +"
    r"(?:(?P<plain>[A-Za-z0-9._+-]+)|'(?P<single>[A-Za-z0-9._+-]+)'|\"(?P<double>[A-Za-z0-9._+-]+)\")"
    r"(?:[ \t]+#.*|[ \t]*)")
# A top-level line of the workflow: a plain key, a colon, then nothing or a value.
TOP_RE = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*) *:(?:[ \t].*)?")
# Characters a YAML reader may read as a line break that the "\n" split does not: a carriage return left
# inside a line, and the next-line, line-separator and paragraph-separator characters a YAML 1.1 reader also
# breaks on. The python-floor gate refuses the same set (LINE_BREAKS there), which the self-test holds.
LINE_BREAKS = {"\r": "a carriage return", "\x85": "U+0085 (next line)",
               "\u2028": "U+2028 (line separator)", "\u2029": "U+2029 (paragraph separator)"}
# A job id under jobs:, a plain key at indent 2, then nothing but an optional comment.
JOB_RE = re.compile(r"  (?P<id>[A-Za-z_][A-Za-z0-9_-]*) *:(?:[ \t]+#.*|[ \t]*)")
# One line of a host-patch job: a block mapping entry, optionally a "- " list item, whose value is nothing,
# a one-line quoted scalar (no inner quote, no backslash) or a plain scalar that opens no flow, block
# scalar, anchor, alias or tag, then an optional comment.
ENTRY_RE = re.compile(
    r"(?P<indent> *)(?P<dash>- +)?(?P<key>[A-Za-z_][A-Za-z0-9_-]*) *:"
    r"(?:[ \t]+(?:'(?P<single>[^']*)'|\"(?P<double>[^\"\\]*)\"|(?P<plain>[A-Za-z0-9_.$/~=+][^#]*?)))?"
    r"(?:[ \t]+#.*)?[ \t]*")
# The deepest indentation a host-patch job line may have: a step input under with:.
MAX_INDENT = 10


class CannotEvaluate(Exception):
    """An input this gate cannot read or interpret; reported as exit 2, never as a pass."""


class _Scalar(str):
    """A scalar value of a host-patch job, with the number of the line it sits on."""
    line = 0


class _Mapping(dict):
    """A block mapping of a job, with the number of the line each of its keys sits on (lines)."""

    def __init__(self):
        super().__init__()
        self.lines = {}


def _read_text(path):
    """Read a regular, non-symlink UTF-8 file, or raise CannotEvaluate."""
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CannotEvaluate("{}: cannot stat: {}".format(path, exc))
    if not stat.S_ISREG(mode):
        raise CannotEvaluate("{}: not a regular file".format(path))
    try:
        return Path(path).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CannotEvaluate("{}: cannot read as UTF-8: {}".format(path, exc))


def load_source(root):
    """Parse and schema-check the single source; return the host patch as (major, minor, micro)."""
    try:
        data = tomllib.loads(_read_text(root / SOURCE_REL))
    except tomllib.TOMLDecodeError as exc:
        raise CannotEvaluate("{}: does not parse: {}".format(SOURCE_REL, exc))
    # tomllib also raises a BARE ValueError on an integer literal past CPython's int-string limit, and a
    # RecursionError on a deeply nested array or inline table or, in a release that bounds key depth, on a
    # key of more than 1000 dotted parts: each is cannot-evaluate, never a traceback.
    except (ValueError, RecursionError) as exc:
        raise CannotEvaluate("{}: does not parse ({}): {}".format(SOURCE_REL, type(exc).__name__, exc))
    if set(data) != SOURCE_KEYS:
        raise CannotEvaluate("{}: keys {} (want exactly {})".format(
            SOURCE_REL, sorted(data), sorted(SOURCE_KEYS)))
    if type(data["format-version"]) is not int or data["format-version"] != 1:
        raise CannotEvaluate("{}: format-version must be the integer 1".format(SOURCE_REL))
    text = data["python-host-patch"]
    match = PATCH_RE.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise CannotEvaluate("{}: python-host-patch must be a \"MAJOR.MINOR.PATCH\" string with at most four "
                             "digits in each component, got {!r:.60}".format(SOURCE_REL, text))
    # PATCH_RE bounds each component, so this conversion holds; a failure is still cannot-evaluate.
    try:
        return tuple(int(part) for part in match.groups())
    except ValueError as exc:
        raise CannotEvaluate("{}: python-host-patch {!r:.60} does not convert to integers: {}".format(
            SOURCE_REL, text, exc))


def _patch_text(patch):
    return "%d.%d.%d" % tuple(patch)


def _entry(job, number, line, where=None):
    """(line number, indentation, key column, list item, key, value) for one content line of a host-patch
    job or of a job naming the --jobs-only judge (where, defaulting to the host-patch wording, names the
    job in each refusal), or CannotEvaluate for a line ENTRY_RE does not read."""
    where = "the host-patch job " + job if where is None else where
    match = ENTRY_RE.fullmatch(line)
    plain = None if match is None else match.group("plain")
    if match is None or (plain is not None and (": " in plain or plain.endswith(":"))):
        raise CannotEvaluate("{}:{}: a line in {} that is not a block mapping entry with "
                             "a one-line scalar value (a block scalar, flow, anchor, alias or tag among "
                             "them); this job model cannot read it".format(WORKFLOW_REL, number, where))
    indent, dash = len(match.group("indent")), match.group("dash") or ""
    if indent > MAX_INDENT:
        raise CannotEvaluate("{}:{}: a line in {} nested deeper than a step input; this "
                             "job model cannot read it".format(WORKFLOW_REL, number, where))
    value = next((group for group in match.group("single", "double", "plain") if group is not None), None)
    if value is not None:
        value = _Scalar(value)
        value.line = number
    return number, indent, indent + len(dash), bool(dash), match.group("key"), value


def _job_tree(job, entries, where=None):
    """The block mapping of a host-patch job or of a job naming the --jobs-only judge from its entries
    (_entry), or CannotEvaluate for an entry the indentation does not place or a repeated key. MAX_INDENT
    bounds the nesting, so the recursion is shallow."""
    where = "the host-patch job " + job if where is None else where
    position = [0]

    def refuse(entry, why):
        raise CannotEvaluate("{}:{}: {} in {}; this job model cannot read it".format(
            WORKFLOW_REL, entry[0], why, where))

    def node(entry):
        if entry[5] is not None:
            return entry[5]
        if position[0] < len(entries) and entries[position[0]][1] > entry[2]:
            child = entries[position[0]]
            return sequence(child[1]) if child[3] else mapping(child[2], False)
        return None

    def mapping(column, item):
        # item: the first entry is the "- " line that opens this mapping as a list item.
        out = _Mapping()
        while position[0] < len(entries):
            entry = entries[position[0]]
            if not item and entry[1] < column:
                break
            if not item and (entry[3] or entry[2] != column):
                refuse(entry, "a line the indentation does not place")
            item = False
            position[0] += 1
            if entry[4] in out:
                refuse(entry, "a repeated key " + entry[4])
            out[entry[4]] = node(entry)
            out.lines[entry[4]] = entry[0]
        return out

    def sequence(indent):
        out = []
        while position[0] < len(entries):
            entry = entries[position[0]]
            if entry[1] < indent:
                break
            if not entry[3] or entry[1] != indent:
                refuse(entry, "a line the indentation does not place")
            out.append(mapping(entry[2], True))
        return out

    return mapping(4, False)


def _count_key(node, key):
    """How many mapping entries named key the tree node holds, at any depth."""
    if isinstance(node, dict):
        return sum((name == key) + _count_key(value, key) for name, value in node.items())
    if isinstance(node, list):
        return sum(_count_key(item, key) for item in node)
    return 0


def _executes(step, command):
    """Whether a step runs command and nothing can skip or mask it: no key but name and run."""
    return step.get("run") == command and set(step) <= RUN_KEYS


def _host_job_findings(job, tree, want):
    """The findings of the JOB_RULES rules for one host-patch job, tree its block mapping."""
    where = "{}: the host-patch job {}".format(WORKFLOW_REL, job)
    found = []
    if "job-keys" in JOB_RULES:
        for key in tree:
            if key not in JOB_KEYS:
                found.append("{} carries {}:, which can skip, mask, move or change what it runs (a host-patch "
                             "job carries only name, runs-on and steps)".format(where, key))
    if "runner" in JOB_RULES and tree.get("runs-on") != RUNNER:
        found.append("{} runs on {!r}, not {!r}: the setup-python versions manifest lists the host patch's "
                     "Linux builds by Ubuntu version, so the job names its image".format(
                         where, tree.get("runs-on"), RUNNER))
    steps = tree.get("steps")
    if not isinstance(steps, list) or not steps:
        return found + ["{} carries no steps list".format(where)]
    if "checkout" in JOB_RULES:
        first = steps[0]
        if not (first.get("uses") == CHECKOUT_USES and set(first) == CHECKOUT_KEYS
                and first.get("with") == CHECKOUT_INPUTS):
            found.append("{}: its first step is not the checkout step as written (uses: {} with only {}, no "
                         "other key): another ref, repository or path can put another tree under the gate "
                         "and the suite".format(where, CHECKOUT_USES, ", ".join(
                             "{}: {}".format(key, value) for key, value in CHECKOUT_INPUTS.items())))
        others = [index for index, step in enumerate(steps) if index
                  and isinstance(step.get("uses"), str) and "checkout" in step["uses"].lower()]
        if others:
            found.append("{} carries another checkout step (step {}), which can put another tree under the "
                         "gate and the suite".format(where, others[0] + 1))
    if "step-env" in JOB_RULES:
        for index, step in enumerate(steps):
            if "env" in step:
                found.append("{} sets env: on step {}: a step env: changes what an otherwise accepted "
                             "run: command does (a BASH_ENV value, or a write to GITHUB_PATH or "
                             "GITHUB_ENV through it); no step of a host-patch job carries env:".format(
                                 where, index + 1))
    setups = [index for index, step in enumerate(steps)
              if isinstance(step.get("uses"), str) and "setup-python" in step["uses"].lower()]
    if "setup" in JOB_RULES:
        if len(setups) != 1:
            found.append("{} carries {} setup-python steps (want exactly one)".format(where, len(setups)))
        else:
            setup = steps[setups[0]]
            for key in setup:
                if key not in SETUP_KEYS:
                    found.append("{} sets {}: on its setup-python step, which can skip it or leave another "
                                 "interpreter in use".format(where, key))
            inputs = setup.get("with")
            if not isinstance(inputs, dict) or set(inputs) != {PIN_KEY}:
                found.append("{}: the with: of its setup-python step holds {} (want python-version alone)"
                             .format(where, sorted(inputs) if isinstance(inputs, dict) else inputs))
        pins = _count_key(tree, PIN_KEY)
        if pins != 1:
            found.append("{} carries {} python-version pins (want exactly one, the with: input of its "
                         "setup-python step; a job-level env: or another key never counts)".format(
                             where, pins))
    if len(setups) != 1:
        # The pin and the step order are judged from the one setup-python step.
        return found
    inputs = steps[setups[0]].get("with")
    value = inputs.get(PIN_KEY) if isinstance(inputs, dict) else None
    if "pin" in JOB_RULES and isinstance(value, str) and value != want:
        found.append("{}:{}: python-version {!r} in the host-patch job {} differs from the host patch {!r} "
                     "that {} names".format(WORKFLOW_REL, value.line, str(value), job, want, SOURCE_REL))
    after = setups[0] + 1
    if "gate-step" in JOB_RULES and not (after < len(steps) and _executes(steps[after], GATE_RUN)):
        found.append("{}: the step right after its setup-python step is not the Python host pin step, an "
                     "executable step (no key but name and run) that runs {}".format(where, GATE_RUN))
    if "suite-step" in JOB_RULES and not any(_executes(step, HOST_SUITES[job]) for step in steps[after:]):
        found.append("{} does not run its suite: no executable step (no key but name and run) after its "
                     "setup-python step runs {}".format(where, HOST_SUITES[job]))
    return found


def _judge_findings(host_judges, other_lines, other_raw):
    """The outside-judge rule over the whole workflow: host_judges the (job, line number) of each
    non-comment HOST_JOBS line naming JOBS_ONLY_FLAG, other_lines the non-comment (line number, line) of
    each job outside HOST_JOBS, other_raw every line of each such job, its accepted header and comment
    lines included. Exactly one step outside the host-patch jobs carries JOBS_ONLY_RUN as its own parsed
    run: property, that step carries no key but name and run, no non-comment host-patch-job line names
    the flag, and no job that carries a judge step, nor any step of such a job, carries env: (in CI a
    job-level env: such as a BASH_ENV value can silence every step of the carrying job, an env: on the
    judge step the judge itself, and an env: on an earlier step every later step of the job, through a
    GITHUB_ENV write; an env: on a step after the judge does not reach it and is refused as policy, so
    the judge's job stays env-free). Each job outside HOST_JOBS any non-comment line of which names
    JOBS_ONLY_FLAG or GATE_PATH is read whole with the host-patch jobs' own line model
    (_refuse_control_chars over every line, then _entry / _job_tree), so a line of it that model cannot
    read (a job property at an unexpected indent, a quoted or explicit key, a block or folded scalar, a
    flow mapping, an anchor or an alias among them) is cannot-evaluate, never skipped; a command-shaped
    value that is not a step's own run: property (a with: input or a job-level run: among them) never
    counts as the judge; and every step whose parsed run: equals JOBS_ONLY_RUN counts, whatever its
    spelling (a quoted value or a tab before it among them), so a doubled judge in a spelling this model
    reads is a finding, never a silent pass."""
    found = []
    for job, number in host_judges:
        found.append("{}:{}: names {} inside the host-patch job {}: the jobs leg's outside run never "
                     "sits inside a host-patch job it judges".format(
                         WORKFLOW_REL, number, JOBS_ONLY_FLAG, job))
    judges, trees = [], {}
    for job, lines in other_lines.items():
        if not any(JOBS_ONLY_FLAG in line or GATE_PATH in line for _, line in lines):
            continue
        where = "the job {}, which names the --jobs-only judge or this gate".format(job)
        for number, line in other_raw[job]:
            _refuse_control_chars(job, number, line, where)
        trees[job] = _job_tree(job, [_entry(job, number, line, where) for number, line in lines], where)
        steps = trees[job].get("steps")
        for index, step in enumerate(steps if isinstance(steps, list) else ()):
            if step.get("run") == JOBS_ONLY_RUN:
                judges.append((job, index))
    if len(judges) != 1:
        found.append("{}: {} steps outside the host-patch jobs run {} as their own run: command (want "
                     "exactly one: the one CI run of the jobs leg that an edit inside the host-patch "
                     "jobs cannot silence)".format(WORKFLOW_REL, len(judges), JOBS_ONLY_RUN))
    carriers = {}
    for job, index in judges:
        carriers.setdefault(job, []).append(index)
    for job, indexes in carriers.items():
        tree = trees[job]
        if "env" in tree:
            found.append("{}:{}: the job {} carries the --jobs-only judge step and sets a job-level "
                         "env:, which in CI can silence every step of that job, the judge step "
                         "included".format(WORKFLOW_REL, tree.lines["env"], job))
        for position, step in enumerate(tree["steps"]):
            if position in indexes:
                for key in sorted(set(step) - RUN_KEYS - {"env"}):
                    found.append("{}:{}: the step that carries the --jobs-only judge carries {}:, "
                                 "which can skip, mask or change what it runs (the judge step carries "
                                 "no key but name and run)".format(WORKFLOW_REL, step.lines[key], key))
            if "env" not in step:
                continue
            number = step.lines["env"]
            if position in indexes:
                found.append("{}:{}: the step that carries the --jobs-only judge sets env:, which in "
                             "CI can change or silence what it runs (a BASH_ENV value among "
                             "them)".format(WORKFLOW_REL, number))
            elif position < max(indexes):
                found.append("{}:{}: the job {} carries the --jobs-only judge and sets env: on step "
                             "{}: a step env: changes what that step's run: command does, and a write "
                             "to GITHUB_ENV or GITHUB_PATH through it reaches every later step of the "
                             "job, the judge step included".format(
                                 WORKFLOW_REL, number, job, position + 1))
            else:
                found.append("{}:{}: the job {} carries the --jobs-only judge and sets env: on step "
                             "{}, after the judge step: that env: does not reach the judge, and this "
                             "gate refuses env: on every step of the judge's job as policy".format(
                                 WORKFLOW_REL, number, job, position + 1))
    return found


def _refuse_control_chars(job, number, line, where=None):
    """Refuse a form feed or a vertical tab (CONTROL_CHARS) in a line of a host-patch job (where None)
    or of a judge-naming job (where naming it), the job's accepted header line included: neither is a
    YAML line break, so this line model reads the line whole, and the refusal holds every such line out
    rather than modelling what another reader does with it."""
    where = "the host-patch job " + job if where is None else where
    for char, label in CONTROL_CHARS.items():
        if char in line:
            raise CannotEvaluate("{}:{}: carries {} in {}; this line model does "
                                 "not accept it".format(WORKFLOW_REL, number, label, where))


def job_findings(root, patch):
    """The top level of WORKFLOW_REL and each HOST_JOBS job in it are whole (JOB_RULES), and each such job
    pins the host patch."""
    want = _patch_text(patch)
    entries = {job: [] for job in HOST_JOBS}
    host_judges, other_lines, other_raw = [], {}, {}
    seen, tops, in_jobs, job = set(), {}, False, None
    for number, line in enumerate(_read_text(root / WORKFLOW_REL).split("\n"), 1):
        line = line[:-1] if line.endswith("\r") else line
        for char, label in LINE_BREAKS.items():
            if char in line:
                raise CannotEvaluate("{}:{}: carries {}, which a YAML reader may read as a line break; this "
                                     "line model splits only on newline and cannot read it".format(
                                         WORKFLOW_REL, number, label))
        stripped = line.strip(" \t")
        if not stripped:
            continue
        comment = stripped.startswith("#")
        if not comment and not line.startswith((" ", "\t")):
            top = TOP_RE.fullmatch(line)
            if top is None:
                raise CannotEvaluate("{}:{}: a top-level line that is not a plain key (a quoted or explicit "
                                     "key, a flow mapping or a document marker among them); this line model "
                                     "cannot read it".format(WORKFLOW_REL, number))
            key = top.group("key")
            if key in tops:
                raise CannotEvaluate("{}:{}: the top-level key {} repeats line {}; this line model cannot "
                                     "tell which YAML keeps".format(WORKFLOW_REL, number, key, tops[key]))
            tops[key] = number
            in_jobs, job = line.split("#", 1)[0].rstrip(" \t") == "jobs:", None
            continue
        if in_jobs and not comment:
            header = JOB_RE.fullmatch(line)
            if header is not None:
                job = header.group("id")
                if job in seen:
                    raise CannotEvaluate("{}:{}: the job id {} repeats; this line model cannot tell which "
                                         "job YAML keeps".format(WORKFLOW_REL, number, job))
                seen.add(job)
                if job in entries:
                    _refuse_control_chars(job, number, line)
                else:
                    other_raw.setdefault(job, []).append((number, line))
                continue
            if not line.startswith("    "):
                raise CannotEvaluate("{}:{}: a line under jobs: that is neither a job id at indent 2 nor job "
                                     "content at indent 4 or deeper; this line model cannot place it".format(
                                         WORKFLOW_REL, number))
        if job not in entries:
            if job is not None:
                other_raw.setdefault(job, []).append((number, line))
                if not comment:
                    other_lines.setdefault(job, []).append((number, line))
            continue
        _refuse_control_chars(job, number, line)
        if PIN_KEY in line.lower() and (comment or PIN_LINE_RE.fullmatch(line) is None):
            raise CannotEvaluate("{}:{}: names python-version in the host-patch job {} but is not one strict "
                                 "pin line; write it as python-version: '{}'".format(
                                     WORKFLOW_REL, number, job, want))
        if not comment:
            if JOBS_ONLY_FLAG in line:
                host_judges.append((job, number))
            entries[job].append(_entry(job, number, line))
    findings = []
    if "workflow-keys" in JOB_RULES:
        for key, number in tops.items():
            if key not in TOP_KEYS:
                findings.append("{}:{}: carries the top-level key {}:, which can change how every run step "
                                "of every job executes (a defaults: or env: block among them); the workflow "
                                "carries only {}".format(WORKFLOW_REL, number, key, ", ".join(TOP_KEYS)))
    if "outside-judge" in JOB_RULES:
        findings.extend(_judge_findings(host_judges, other_lines, other_raw))
    for name in HOST_JOBS:
        if name not in seen:
            findings.append("{}: carries no job {} (a host-patch job, HOST_JOBS in "
                            "tools/check_python_host_pin.py)".format(WORKFLOW_REL, name))
            continue
        findings.extend(_host_job_findings(name, _job_tree(name, entries[name]), want))
    return findings


def interpreter_findings(patch, version):
    """The running interpreter, version = (major, minor, micro, releaselevel), is the host patch, final."""
    if tuple(version[:3]) == tuple(patch) and version[3] == "final":
        return []
    return ["the running interpreter is Python {}.{}.{} ({}), but {} names the host patch {}: a passing "
            "local suite does not predict the host-patch CI jobs; run on Python {}, or, when the host "
            "interpreter has changed, update {} and the host-patch pins in {} together".format(
                *version[:4], SOURCE_REL, _patch_text(patch), _patch_text(patch), SOURCE_REL, WORKFLOW_REL)]


def evaluate(root, version, jobs_only=False):
    """Run every leg over root, judging the interpreter version; return (exit code, report lines).
    jobs_only (--jobs-only) runs the source and jobs legs and skips the interpreter leg: the CI step
    outside the host-patch jobs runs on the floor pin, not the host patch, so the interpreter leg
    cannot run there."""
    try:
        patch = load_source(root)
        findings = []
        findings.extend(job_findings(root, patch))
        if not jobs_only:
            findings.extend(interpreter_findings(patch, version))
    except CannotEvaluate as exc:
        return 2, ["CANNOT EVALUATE: {}".format(exc)]
    if findings:
        return 1, ["FAIL: " + finding for finding in findings]
    report = ("PASS: python host patch {} ({}): {} carries only the top-level keys {}, its host-patch jobs "
              "{} each run on {}, carry env: on no job key or step, check out as written, pin it in their "
              "one setup-python step, run this gate right after it and run their suite, and one step "
              "outside them carries the jobs leg's --jobs-only judge as its own run: command with no key "
              "but name and run, its job and every step of that job free of env:".format(
                  _patch_text(patch), SOURCE_REL, WORKFLOW_REL, ", ".join(TOP_KEYS), ", ".join(HOST_JOBS),
                  RUNNER))
    if jobs_only:
        return 0, [report + " (--jobs-only: the interpreter leg did not run)"]
    return 0, [report + ", and this interpreter is Python {}".format(_patch_text(version[:3]))]


# ---------------------------------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------------------------------

FAILURES = []
EXECUTED = []
# Red-on-revert: each finding leg's call in evaluate(), removed in a copy of this gate.
REVERT_CALLS = (("jobs", "job_findings(root, patch)"),
                ("interpreter", "interpreter_findings(patch, version)"))
HOST = (3, 14, 4, "final")
SOURCE_TEXT = "format-version = 1\npython-host-patch = \"3.14.4\"\n"
OPF, GFE = HOST_JOBS
PIN = "python-version: '3.14.4'"
FLOOR_JOB = ("  floor:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
             "      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.14'\n"
             "      - run: python3 -I -B tools/x.py\n")
SETUP_STEP = "      - uses: actions/setup-python@v5\n        with:\n          " + PIN + "\n\n"
GATE_STEP = "      - name: Python host pin\n        run: " + GATE_RUN + "\n\n"
JUDGE_STEP = ("      - name: Python host pin (jobs leg, outside the host-patch jobs)\n"
              "        run: " + JOBS_ONLY_RUN + "\n")
REGISTRY_JOB = ("  registry:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
                "      - uses: actions/setup-python@v5\n        with:\n          python-version: '3.14'\n"
                + JUDGE_STEP)
HEAD = "name: Quality\n\non: push\n\npermissions:\n  contents: read\n\njobs:\n"


def check(name, got, want):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


class _FakeSys:
    """A stand-in for the sys module main() reads: a chosen version_info and swallowed output streams."""

    def __init__(self, version):
        self.version_info = version
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()


def _write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def _copy(base, name, source):
    """A module named name, loaded through importlib from source written under base."""
    _write(base, name + ".py", source)
    spec = importlib.util.spec_from_file_location(name, base / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _suite_step(job):
    return "      - name: Suite\n        run: " + HOST_SUITES[job] + "\n"


def _host_job(job):
    """A whole host-patch job, laid out as quality.yml lays it out."""
    return ("  {0}:\n    name: {0}\n    runs-on: {1}\n    steps:\n      - uses: actions/checkout@v4\n"
            "        with:\n          fetch-depth: 0\n          fetch-tags: true\n\n".format(job, RUNNER)
            + SETUP_STEP + GATE_STEP + _suite_step(job))


def _edited(text, edits, part):
    """text with each edit (part, old, new) for this part applied in order, each replacing the one
    occurrence of old."""
    for name, old, new in edits:
        if name == part:
            if text.count(old) != 1:
                raise CannotEvaluate("self-test edit {!r} occurs {} times in {}".format(
                    old, text.count(old), part or "the top level"))
            text = text.replace(old, new)
    return text


def _workflow(edits=(), omit=()):
    """quality.yml: HEAD, a floor job, each host-patch job not in omit, then a registry job carrying the
    --jobs-only judge step (part name "registry" for edits, omitted when omit names it), with the edits
    applied in order; an edit (job, old, new) replaces the one occurrence of old in that job, or in HEAD
    when job is None."""
    jobs = [_edited(HEAD, edits, None), FLOOR_JOB]
    jobs.extend(_edited(_host_job(job), edits, job) for job in HOST_JOBS if job not in omit)
    if "registry" not in omit:
        jobs.append(_edited(REGISTRY_JOB, edits, "registry"))
    return "".join(jobs)


def _fixture(base, source=SOURCE_TEXT, workflow=None):
    """A tree with the single source (absent when source is None) and quality.yml: _workflow() unless the
    workflow text is given."""
    root = Path(tempfile.mkdtemp(prefix="tree-", dir=base))
    if source is not None:
        _write(root, SOURCE_REL, source)
    _write(root, WORKFLOW_REL, _workflow() if workflow is None else workflow)
    return root


def _has(lines, marker):
    return any(marker in line for line in lines)


def _outcome(evaluate_tree, root, version, marker):
    """(exit code, whether a report line holds marker) for evaluate_tree over root, or "raised NAME" when a
    parser exception escapes it."""
    try:
        code, lines = evaluate_tree(root, version)
    except (RecursionError, ValueError) as exc:
        return "raised " + type(exc).__name__
    return code, _has(lines, marker)


class _ParserMismatch(Exception):
    """tomllib's private key parser does not fit the parse_key(src, pos) -> (pos, key) the injection
    wraps."""


def _key_depth_bounded(real, calls):
    """tomllib's key parser, made to raise RecursionError on a key of more than 1000 dotted parts, as a
    release that bounds key depth does. calls records each call; a call or a result that does not fit
    parse_key(src, pos) -> (pos, key), pos an int within src and key a tuple of str, raises
    _ParserMismatch."""
    def parse_key(*args, **kwargs):
        calls.append(len(args))
        try:
            result = real(*args, **kwargs)
        except TypeError as exc:
            raise _ParserMismatch("calling it raised TypeError: {}".format(exc))
        src = args[0] if args else kwargs.get("src")
        if not (isinstance(result, tuple) and len(result) == 2):
            raise _ParserMismatch("it returned {} rather than (pos, key)".format(type(result).__name__))
        if not (isinstance(src, str) and type(result[0]) is int and 0 <= result[0] <= len(src)):
            raise _ParserMismatch("it returned the position {!r:.40}, not an int within the source".format(
                result[0]))
        if not (isinstance(result[1], tuple) and all(isinstance(part, str) for part in result[1])):
            raise _ParserMismatch("it returned the key {!r:.60}, not a tuple of str".format(result[1]))
        if len(result[1]) > 1000:
            raise RecursionError("injected: a key of more than 1000 dotted parts")
        return result
    return parse_key


def _parse_refusal(evaluate_tree, root, bound):
    """(exit code, whether the report says the source does not parse) for evaluate_tree over root at HOST,
    with tomllib's key parser bounded when bound is true; "raised NAME" when the exception escapes. The bound
    patches a private tomllib function: on a release where it is missing, has another shape or is never
    called, the result is a text naming that cause, so the case fails cleanly rather than crashing."""
    parser = getattr(tomllib, "_parser", None)
    real = getattr(parser, "parse_key", None)
    if bound and not callable(real):
        return ("tomllib._parser.parse_key is missing or not callable on this release, so the injected "
                "key-depth case cannot run")
    calls = []
    if bound:
        parser.parse_key = _key_depth_bounded(real, calls)
    try:
        result = _outcome(evaluate_tree, root, HOST, SOURCE_REL + ": does not parse")
    except _ParserMismatch as exc:
        result = "tomllib._parser.parse_key does not fit the injection on this release: {}".format(exc)
    finally:
        if bound:
            parser.parse_key = real
    if bound and not calls and not isinstance(result, str):
        return "tomllib._parser.parse_key was never called, so the injected key-depth case did not run"
    return result


# A key parser whose (pos, key) result is malformed, each with the words of the mismatch it must raise: the
# bounded parser names each as a mismatch, never a traceback, never the expected refusal.
MALFORMED_RESULTS = {
    "none-position": (lambda src, pos, key: (None, key), "not an int within the source"),
    "negative-position": (lambda src, pos, key: (-1, key), "not an int within the source"),
    "past-end-position": (lambda src, pos, key: (len(src) + 1, key), "not an int within the source"),
    "bool-position": (lambda src, pos, key: (True, key), "not an int within the source"),
    "non-str-key-part": (lambda src, pos, key: (pos, key + (1,)), "not a tuple of str"),
}


def _malformed_refusal(refusal, damage, marker):
    """True when refusal(), run with tomllib's key parser replaced by one whose result damage(src, pos, key)
    rewrites, returns a text naming the mismatch marker words; otherwise what it returned or raised."""
    parser = getattr(tomllib, "_parser", None)
    real = getattr(parser, "parse_key", None)
    if not callable(real):
        return "tomllib._parser.parse_key is missing or not callable on this release"

    def parse_key(src, pos):
        pos, key = real(src, pos)
        return damage(src, pos, key)
    parser.parse_key = parse_key
    try:
        got = refusal()
    except Exception as exc:
        got = "raised " + type(exc).__name__
    finally:
        parser.parse_key = real
    return True if isinstance(got, str) and "does not fit the injection" in got and marker in got else got


def _job_cases():
    """Each jobs-leg finding case: (check id, edits, omitted jobs, a marker of its finding, and the one
    JOB_RULES rule whose removal lets it pass, or None where no one rule decides it:
    jobs/second-pin-line-finding and jobs/job-env-pin-finding are each refused by two overlapping rules,
    jobs/jobs-only-moved-and-host-jobs-env-finding and jobs/jobs-only-step-env-and-host-jobs-env-finding
    are each refused by both the outside-judge and job-keys rules, and pins/host-job-missing-finding is
    refused by the unconditional missing-job check in job_findings, which no JOB_RULES rule
    controls; a None case runs no rule-removal copy, so it is red through its exit code and finding
    marker alone)."""
    suite = {job: _suite_step(job) for job in HOST_JOBS}
    gate_name = "      - name: Python host pin\n"
    suite_name = "      - name: Suite\n"
    setup_uses = "      - uses: actions/setup-python@v5\n"
    checkout_uses = "      - uses: actions/checkout@v4\n"
    fetch_tags = "          fetch-tags: true\n"
    runs = "    runs-on: "
    second = "  second:\n    runs-on: ubuntu-latest\n    steps:\n"
    decoy = ("  decoy:\n    runs-on: ubuntu-latest\n    steps:\n"
             "      - name: Decoy judge\n        run: " + JOBS_ONLY_RUN + "\n        if: false\n")
    return (
        ("pins/host-job-missing-finding", (), (GFE,), "carries no job " + GFE, None),
        ("pins/newer-patch-finding", ((OPF, PIN, "python-version: '3.14.8'"),), (),
         "python-version '3.14.8' in the host-patch job opf-host-patch differs", "pin"),
        ("pins/floor-pin-in-host-job-finding", ((GFE, PIN, "python-version: '3.14'"),), (),
         "python-version '3.14' in the host-patch job git-fixture-env-host-patch differs", "pin"),
        ("pins/host-job-without-pin-finding", ((OPF, "        with:\n          " + PIN + "\n", ""),), (),
         "opf-host-patch carries 0 python-version pins", "setup"),
        ("pins/host-job-two-pins-finding", ((OPF, SETUP_STEP, SETUP_STEP + SETUP_STEP),), (),
         "opf-host-patch carries 2 setup-python steps", "setup"),
        ("jobs/setup-step-removed-finding", ((GFE, SETUP_STEP, ""),), (),
         GFE + " carries 0 setup-python steps", "setup"),
        # A pin among the checkout inputs: the setup and checkout rules both refuse it.
        ("jobs/second-pin-line-finding",
         ((OPF, "          fetch-depth: 0\n", "          fetch-depth: 0\n          " + PIN + "\n"),), (),
         "opf-host-patch carries 2 python-version pins", None),
        ("jobs/setup-step-second-pin-finding", ((GFE, setup_uses, setup_uses + "        " + PIN + "\n"),), (),
         GFE + " carries 2 python-version pins", "setup"),
        ("jobs/setup-step-if-finding", ((GFE, setup_uses, setup_uses + "        if: false\n"),), (),
         "sets if: on its setup-python step", "setup"),
        ("jobs/setup-step-extra-input-finding",
         ((OPF, "          " + PIN + "\n", "          " + PIN + "\n          update-environment: false\n"),),
         (), "holds ['python-version', 'update-environment']", "setup"),
        # A reviewer reproduction: the setup-python step and the gate step removed, and a job-level env:
        # that names python-version in their place.
        ("jobs/job-env-pin-finding",
         ((GFE, SETUP_STEP + GATE_STEP, ""), (GFE, runs, "    env:\n      " + PIN + "\n" + runs)), (),
         GFE + " carries 0 setup-python steps", None),
        ("jobs/gate-step-removed-finding", ((OPF, GATE_STEP, ""),), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/gate-step-moved-finding",
         ((GFE, GATE_STEP, ""), (GFE, suite[GFE], suite[GFE] + "\n" + GATE_STEP)), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/gate-step-other-command-finding",
         ((OPF, "run: " + GATE_RUN, "run: python3 tools/check_python_host_pin.py"),), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/gate-step-if-finding", ((GFE, gate_name, gate_name + "        if: false\n"),), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/gate-step-continue-on-error-finding",
         ((OPF, gate_name, gate_name + "        continue-on-error: true\n"),), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/step-before-gate-finding",
         ((GFE, GATE_STEP, "      - run: pip install other-python\n\n" + GATE_STEP),), (),
         "is not the Python host pin step", "gate-step"),
        ("jobs/opf-suite-removed-finding", ((OPF, suite[OPF], ""),), (),
         "opf-host-patch does not run its suite", "suite-step"),
        ("jobs/git-fixture-env-suite-removed-finding", ((GFE, suite[GFE], ""),), (),
         GFE + " does not run its suite", "suite-step"),
        ("jobs/suite-renamed-finding",
         ((GFE, "--suite git-fixture-env-selftest", "--suite python-floor-selftest"),), (),
         GFE + " does not run its suite", "suite-step"),
        ("jobs/suite-before-setup-finding",
         ((OPF, suite[OPF], ""), (OPF, SETUP_STEP, suite[OPF] + "\n" + SETUP_STEP)), (),
         "opf-host-patch does not run its suite", "suite-step"),
        ("jobs/suite-step-if-finding", ((GFE, suite_name, suite_name + "        if: false\n"),), (),
         GFE + " does not run its suite", "suite-step"),
        ("jobs/suite-step-continue-on-error-finding",
         ((OPF, suite_name, suite_name + "        continue-on-error: true\n"),), (),
         "opf-host-patch does not run its suite", "suite-step"),
        ("jobs/job-if-finding", ((OPF, runs, "    if: false\n" + runs),), (),
         "opf-host-patch carries if:", "job-keys"),
        ("jobs/job-continue-on-error-finding", ((GFE, runs, "    continue-on-error: true\n" + runs),), (),
         GFE + " carries continue-on-error:", "job-keys"),
        ("jobs/job-container-finding", ((OPF, runs, "    container: python:3.14.8\n" + runs),), (),
         "opf-host-patch carries container:", "job-keys"),
        ("jobs/runs-on-latest-finding", ((OPF, runs + RUNNER, runs + "ubuntu-latest"),), (),
         "runs on 'ubuntu-latest', not 'ubuntu-24.04'", "runner"),
        ("jobs/runs-on-newer-image-finding", ((GFE, runs + RUNNER, runs + "ubuntu-26.04"),), (),
         "runs on 'ubuntu-26.04', not 'ubuntu-24.04'", "runner"),
        # A reviewer reproduction: a workflow-level shell default that runs no step's command.
        ("jobs/workflow-defaults-echo-finding",
         ((None, "jobs:\n", "defaults:\n  run:\n    shell: echo {0}\n\njobs:\n"),), (),
         "carries the top-level key defaults:", "workflow-keys"),
        ("jobs/workflow-defaults-true-finding",
         ((None, "jobs:\n", "defaults:\n  run:\n    shell: \"true {0}\"\n\njobs:\n"),), (),
         "carries the top-level key defaults:", "workflow-keys"),
        ("jobs/workflow-defaults-after-jobs-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + "\ndefaults:\n  run:\n    shell: echo {0}\n"),), (),
         "carries the top-level key defaults:", "workflow-keys"),
        ("jobs/workflow-env-finding", ((None, "jobs:\n", "env:\n  FOO: bar\n\njobs:\n"),), (),
         "carries the top-level key env:", "workflow-keys"),
        ("jobs/job-defaults-finding",
         ((OPF, runs, "    defaults:\n      run:\n        shell: echo {0}\n" + runs),), (),
         "opf-host-patch carries defaults:", "job-keys"),
        # Reviewer reproductions: an extra, otherwise unheld step whose env: makes BASH_ENV append to
        # GITHUB_PATH before an otherwise accepted command runs, and the GITHUB_ENV variant.
        ("jobs/step-env-github-path-finding",
         ((OPF, GATE_STEP, GATE_STEP + "      - name: Extra check\n        run: " + GATE_RUN
           + " --self-test\n        env:\n"
           + "          BASH_ENV: $(printf /review-probe >> \"$GITHUB_PATH\")\n\n"),), (),
         "opf-host-patch sets env: on step 4", "step-env"),
        ("jobs/step-env-github-env-finding",
         ((GFE, GATE_STEP, GATE_STEP + "      - name: Extra check\n        run: " + GATE_RUN
           + " --self-test\n        env:\n"
           + "          BASH_ENV: $(printf REVIEW_PROBE=1 >> \"$GITHUB_ENV\")\n\n"),), (),
         GFE + " sets env: on step 4", "step-env"),
        # A reviewer reproduction: a job-level env: (BASH_ENV) in BOTH host-patch jobs, which in CI
        # silences every step of those jobs, this gate's own steps included; job-keys keeps refusing it.
        ("jobs/job-env-bash-env-both-jobs-finding",
         ((OPF, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs),
          (GFE, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs)), (),
         "carries env:", "job-keys"),
        # The outside judge: relocating the --jobs-only step into a host-patch job, removing it (or its
        # whole job), doubling it, and an env: on the step or on the job that carries it are each refused
        # by the outside-judge rule.
        ("jobs/jobs-only-into-host-job-finding",
         (("registry", JUDGE_STEP, ""),
          (OPF, suite[OPF], suite[OPF] + "\n      - name: Python host pin jobs leg\n        run: "
           + JOBS_ONLY_RUN + "\n")), (),
         "names --jobs-only inside the host-patch job opf-host-patch", "outside-judge"),
        ("jobs/jobs-only-step-removed-finding", (("registry", JUDGE_STEP, ""),), (),
         "0 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-job-omitted-finding", (), ("registry",),
         "0 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-step-twice-finding", (("registry", JUDGE_STEP, JUDGE_STEP + JUDGE_STEP),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-step-env-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + "        env:\n          BASH_ENV: '$(: > \"$0\")'\n"),), (),
         "the step that carries the --jobs-only judge sets env:", "outside-judge"),
        ("jobs/jobs-only-job-env-finding",
         (("registry", "    runs-on: ", "    env:\n      BASH_ENV: '$(: > \"$0\")'\n    runs-on: "),), (),
         "sets a job-level env:", "outside-judge"),
        # Reviewer reproductions: a command-shaped value that is not a step's own run: property never
        # counts as the judge (a with: input of the registry checkout step, and a job-level run:), and
        # an env: on an earlier step of the judge's job (whose GITHUB_ENV write reaches the judge step)
        # is a finding.
        ("jobs/jobs-only-with-input-decoy-finding",
         (("registry", JUDGE_STEP, ""),
          ("registry", "      - uses: actions/checkout@v4\n",
           "      - uses: actions/checkout@v4\n        with:\n          run: " + JOBS_ONLY_RUN + "\n")),
         (), "0 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-job-level-run-finding",
         (("registry", JUDGE_STEP, ""),
          ("registry", "    runs-on: ", "    run: " + JOBS_ONLY_RUN + "\n    runs-on: ")), (),
         "0 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-earlier-step-env-finding",
         (("registry", "      - uses: actions/setup-python@v5\n",
           "      - uses: actions/setup-python@v5\n        env:\n          BASH_ENV: '$(: > \"$0\")'\n"),),
         (), "sets env: on step 2", "outside-judge"),
        # Reviewer reproductions: the relocation above with a job-level env: (BASH_ENV) in both
        # host-patch jobs, and an env: on the judge step with the same host-job env:; the outside-judge
        # and job-keys rules both refuse each.
        ("jobs/jobs-only-moved-and-host-jobs-env-finding",
         (("registry", JUDGE_STEP, ""),
          (OPF, suite[OPF], suite[OPF] + "\n      - name: Python host pin jobs leg\n        run: "
           + JOBS_ONLY_RUN + "\n"),
          (OPF, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs),
          (GFE, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs)), (),
         "names --jobs-only inside the host-patch job", None),
        ("jobs/jobs-only-step-env-and-host-jobs-env-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + "        env:\n          BASH_ENV: '$(: > \"$0\")'\n"),
          (OPF, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs),
          (GFE, runs, "    env:\n      BASH_ENV: '$(: > \"$0\")'\n" + runs)), (),
         "the step that carries the --jobs-only judge sets env:", None),
        # Round-6 reviewer reproductions: the canonical registry judge KEPT, and a second judge added in
        # a spelling the job model reads (a quoted run: value, a tab before the value): each parses to
        # the judge command, so the doubled judge is a finding, never a silent pass.
        ("jobs/jobs-only-single-quoted-second-judge-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + second + "      - run: '" + JOBS_ONLY_RUN + "'\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-double-quoted-second-judge-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + second + "      - run: \"" + JOBS_ONLY_RUN + "\"\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        ("jobs/jobs-only-tab-value-second-judge-finding",
         (("registry", JUDGE_STEP,
           JUDGE_STEP + second + "      - run:\t" + JOBS_ONLY_RUN + "\n"),), (),
         "2 steps outside the host-patch jobs run", "outside-judge"),
        # The judge step carries no key but name and run: any other key on it (an if: among them) can
        # skip, mask or change what it runs, so it is a finding.
        ("jobs/jobs-only-step-if-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + "        if: false\n"),), (),
         "the step that carries the --jobs-only judge carries if:", "outside-judge"),
        # An env: on a step after the judge does not reach it; the refusal is policy, so the judge's
        # job stays env-free, and the finding says so by position.
        ("jobs/jobs-only-later-step-env-finding",
         (("registry", JUDGE_STEP, JUDGE_STEP + "      - name: Later step\n        run: echo done\n"
           "        env:\n          SAMPLE: value\n"),), (),
         "sets env: on step 4, after the judge step", "outside-judge"),
        # A reviewer reproduction (round 6, combined): the registry judge re-quoted, a job-level env:
        # on the registry job, and a decoy job carrying the canonical judge under if: false. The quoted
        # judge still parses and counts, so the doubled judge, the env: on its job and the if: on the
        # decoy judge step are each findings.
        ("jobs/jobs-only-requoted-judge-env-and-decoy-finding",
         (("registry", "run: " + JOBS_ONLY_RUN + "\n", "run: '" + JOBS_ONLY_RUN + "'\n"),
          ("registry", "    runs-on: ", "    env:\n      SAMPLE: value\n    runs-on: "),
          ("registry", "'" + JOBS_ONLY_RUN + "'\n", "'" + JOBS_ONLY_RUN + "'\n" + decoy)), (),
         "sets a job-level env:", "outside-judge"),
        ("jobs/checkout-ref-finding", ((OPF, fetch_tags, fetch_tags + "          ref: main\n"),), (),
         "opf-host-patch: its first step is not the checkout step as written", "checkout"),
        ("jobs/checkout-repository-finding",
         ((GFE, fetch_tags, fetch_tags + "          repository: other/repo\n"),), (),
         GFE + ": its first step is not the checkout step as written", "checkout"),
        ("jobs/checkout-path-finding", ((OPF, fetch_tags, fetch_tags + "          path: other\n"),), (),
         "opf-host-patch: its first step is not the checkout step as written", "checkout"),
        ("jobs/checkout-other-ref-of-action-finding",
         ((GFE, "actions/checkout@v4", "actions/checkout@main"),), (),
         GFE + ": its first step is not the checkout step as written", "checkout"),
        ("jobs/checkout-step-if-finding", ((OPF, checkout_uses, checkout_uses + "        if: false\n"),), (),
         "opf-host-patch: its first step is not the checkout step as written", "checkout"),
        ("jobs/second-checkout-finding",
         ((GFE, GATE_STEP, GATE_STEP + checkout_uses + "        with:\n          ref: main\n\n"),), (),
         GFE + " carries another checkout step (step 4)", "checkout"),
    )


def _self_test_cases(base):
    rules = _copy(base, "python_host_pin_rules", _read_text(Path(__file__).resolve()))
    check("fixture/clean-tree-passes", evaluate(_fixture(base), HOST)[0], 0)

    for check_id, source in (
            ("source/missing-cannot-evaluate", None),
            ("source/malformed-cannot-evaluate", "python-host-patch = \n"),
            ("source/extra-key-cannot-evaluate", SOURCE_TEXT + "python-floor = \"3.14\"\n"),
            ("source/missing-key-cannot-evaluate", "format-version = 1\n"),
            ("source/string-format-version-cannot-evaluate",
             "format-version = \"1\"\npython-host-patch = \"3.14.4\"\n"),
            ("source/minor-only-cannot-evaluate", "format-version = 1\npython-host-patch = \"3.14\"\n"),
            ("source/leading-zero-cannot-evaluate", "format-version = 1\npython-host-patch = \"3.14.04\"\n"),
            ("source/suffix-cannot-evaluate", "format-version = 1\npython-host-patch = \"3.14.4rc1\"\n"),
            ("source/number-cannot-evaluate", "format-version = 1\npython-host-patch = 3.14\n"),
            ("source/not-utf8-cannot-evaluate", b"format-version = 1\npython-host-patch = \"3.14.4\"\n\xff")):
        code, lines = evaluate(_fixture(base, source=source), HOST)
        check(check_id, (code, _has(lines, SOURCE_REL)), (2, True))
    root = _fixture(base, source=None)
    (root / SOURCE_REL).mkdir(parents=True)
    code, lines = evaluate(root, HOST)
    check("source/directory-cannot-evaluate", (code, _has(lines, "not a regular file")), (2, True))
    root = _fixture(base, source=None)
    _write(root, "elsewhere.toml", SOURCE_TEXT)
    (root / SOURCE_REL).parent.mkdir(parents=True, exist_ok=True)
    os.symlink(root / "elsewhere.toml", root / SOURCE_REL)
    code, lines = evaluate(root, HOST)
    check("source/symlink-cannot-evaluate", (code, _has(lines, "not a regular file")), (2, True))
    # tomllib refuses some sources outside TOMLDecodeError: a RecursionError on deep nesting (a real payload,
    # far past any default recursion limit) and on a key of more than 1000 dotted parts (INJECTED: the
    # running release may not bound key depth, so its key parser is patched to raise as one that does), and
    # a bare ValueError on an over-long integer literal. Each is cannot-evaluate; an exception that escapes
    # evaluate() is recorded as raised, so each case is red when its mapping is removed.
    for check_id, extra, bound in (
            ("source/deep-nesting-cannot-evaluate", "x = " + "[" * 10000 + "]" * 10000 + "\n", False),
            ("source/long-dotted-key-cannot-evaluate", ".".join(["k"] * 1001) + " = 1\n", True),
            ("source/long-integer-cannot-evaluate", "x = " + "1" * 5000 + "\n", False)):
        check(check_id, _parse_refusal(evaluate, _fixture(base, source=SOURCE_TEXT + extra), bound),
              (2, True))
    # A key parser whose result breaks the (pos, key) contract: the bounded parser names the mismatch.
    root = _fixture(base)
    for check_id, name in (
            ("source/parser-none-position-named-failure", "none-position"),
            ("source/parser-negative-position-named-failure", "negative-position"),
            ("source/parser-past-end-position-named-failure", "past-end-position"),
            ("source/parser-bool-position-named-failure", "bool-position"),
            ("source/parser-non-str-key-part-named-failure", "non-str-key-part")):
        check(check_id, _malformed_refusal(lambda: _parse_refusal(evaluate, root, True),
                                           *MALFORMED_RESULTS[name]), True)
    # A version component: PATCH_RE bounds it at four digits, and a component that still does not convert
    # is cannot-evaluate. Each case also runs the copy with the bound removed: a five-digit component then
    # loads (a finding, so the bound is what refuses it), and a 5,000-digit quoted component, past the
    # int-string limit, is still refused by the conversion mapping, never a raised ValueError.
    five = _fixture(base, source="format-version = 1\npython-host-patch = \"3.14.10000\"\n")
    long = _fixture(base, source="format-version = 1\npython-host-patch = \"3.14." + "1" * 5000 + "\"\n")
    rules.PATCH_RE = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
    try:
        check("source/five-digit-component-cannot-evaluate",
              (_outcome(evaluate, five, HOST, "at most four digits"), rules.evaluate(five, HOST)[0]),
              ((2, True), 1))
        check("source/long-quoted-component-cannot-evaluate",
              (_outcome(evaluate, long, HOST, "at most four digits"),
               _outcome(rules.evaluate, long, HOST, "does not convert to integers")), ((2, True), (2, True)))
    finally:
        rules.PATCH_RE = PATCH_RE

    root = _fixture(base)
    (root / WORKFLOW_REL).unlink()
    code, lines = evaluate(root, HOST)
    check("pins/workflow-missing-cannot-evaluate", (code, _has(lines, "quality.yml: cannot stat")), (2, True))
    # Each finding case is red with the intact gate and, but for a case more than one rule refuses, green in
    # the copy with its one rule removed from JOB_RULES: each rule is red on revert.
    cases = _job_cases()
    if {case[4] for case in cases if case[4] is not None} != JOB_RULES:
        raise CannotEvaluate("a JOB_RULES rule has no finding case")
    for check_id, edits, omit, marker, rule in cases:
        root = _fixture(base, workflow=_workflow(edits, omit))
        code, lines = evaluate(root, HOST)
        got, want = (code, _has(lines, marker)), (1, True)
        if rule is not None:
            rules.JOB_RULES = JOB_RULES - {rule}
            try:
                got, want = got + (rules.evaluate(root, HOST)[0],), want + (0,)
            finally:
                rules.JOB_RULES = JOB_RULES
        check(check_id, got, want)
    for check_id, pin in (
            ("pins/plain-pin-passes", "3.14.4"),
            ("pins/double-quoted-pin-passes", "\"3.14.4\""),
            ("pins/trailing-comment-passes", "'3.14.4'  # the host patch")):
        check(check_id, evaluate(_fixture(base, workflow=_workflow(((OPF, PIN, "python-version: " + pin),))),
                                 HOST)[0], 0)
    check("jobs/extra-step-after-gate-passes", evaluate(_fixture(base, workflow=_workflow(((
        OPF, GATE_STEP, GATE_STEP + "      - name: Python host pin self-test\n        run: " + GATE_RUN
        + " --self-test\n\n"),))), HOST)[0], 0)
    check("jobs/comment-line-passes", evaluate(_fixture(base, workflow=_workflow(((
        GFE, "    steps:\n", "    # the steps\n    steps:\n"),))), HOST)[0], 0)
    check("pins/other-job-pin-ignored-passes", evaluate(_fixture(base, workflow=_workflow().replace(
        "python-version: '3.14'\n", "python-version: '3.13'\n")), HOST)[0], 0)
    root = _fixture(base)
    _write(root, WORKFLOW_REL, (root / WORKFLOW_REL).read_bytes().replace(b"\n", b"\r\n"))
    check("pins/crlf-workflow-passes", evaluate(root, HOST)[0], 0)
    strict = "is not one strict pin line"
    for check_id, pin in (
            ("pins/expression-pin-cannot-evaluate", "${{ matrix.python }}"),
            ("pins/flow-pin-cannot-evaluate", "['3.14.4']"),
            ("pins/upper-case-key-cannot-evaluate", "'3.14.4'\n          PYTHON-VERSION: '3.14.8'"),
            ("pins/comment-naming-key-cannot-evaluate",
             "'3.14.4'\n      # python-version: '3.14.8'"),
            ("pins/version-file-cannot-evaluate",
             "'3.14.4'\n          python-version-file: .python-version")):
        code, lines = evaluate(_fixture(base, workflow=_workflow(((OPF, PIN, "python-version: " + pin),))),
                               HOST)
        check(check_id, (code, _has(lines, strict)), (2, True))
    model = "this job model cannot read it"
    for check_id, edits, marker in (
            ("jobs/block-scalar-run-cannot-evaluate",
             ((OPF, "run: " + HOST_SUITES[OPF], "run: |\n          " + HOST_SUITES[OPF]),), model),
            ("jobs/flow-runner-cannot-evaluate", ((GFE, "runs-on: " + RUNNER, "runs-on: [" + RUNNER + "]"),),
             model),
            ("jobs/colon-in-plain-value-cannot-evaluate",
             ((OPF, "    name: " + OPF + "\n", "    name: a: b\n"),), model),
            ("jobs/repeated-key-cannot-evaluate",
             ((OPF, "    runs-on: ", "    runs-on: ubuntu-latest\n    runs-on: "),),
             "a repeated key runs-on"),
            ("jobs/misplaced-line-cannot-evaluate", ((GFE, "    steps:\n", "    steps:\n     x: 1\n"),),
             "the indentation does not place"),
            ("jobs/deep-line-cannot-evaluate",
             ((OPF, "          fetch-depth: 0\n", "          fetch-depth: 0\n            x: 1\n"),),
             "nested deeper than a step input")):
        code, lines = evaluate(_fixture(base, workflow=_workflow(edits)), HOST)
        check(check_id, (code, _has(lines, marker)), (2, True))
    for check_id, workflow, marker in (
            ("pins/repeated-job-cannot-evaluate",
             "jobs:\n" + _host_job(OPF) + _host_job(GFE) + _host_job(OPF),
             "the job id opf-host-patch repeats"),
            ("pins/tab-indent-cannot-evaluate", "jobs:\n\topf-host-patch:\n",
             "this line model cannot place it"),
            ("pins/quoted-job-id-cannot-evaluate", "jobs:\n  \"opf-host-patch\":\n",
             "this line model cannot place it"),
            ("pins/indent-three-cannot-evaluate", "jobs:\n" + _host_job(OPF) + "   x: 1\n",
             "this line model cannot place it"),
            ("pins/repeated-top-level-key-cannot-evaluate", _workflow() + "jobs:\n" + FLOOR_JOB,
             "the top-level key jobs repeats"),
            ("pins/repeated-permissions-cannot-evaluate", _workflow() + "permissions:\n  contents: write\n",
             "the top-level key permissions repeats"),
            ("pins/quoted-top-level-key-cannot-evaluate",
             _workflow(((None, "jobs:\n", "\"defaults\":\n  run:\n    shell: echo {0}\n\njobs:\n"),)),
             "a top-level line that is not a plain key"),
            ("pins/explicit-top-level-key-cannot-evaluate",
             _workflow(((None, "jobs:\n", "? defaults\n: {run: {shell: echo {0}}}\n\njobs:\n"),)),
             "a top-level line that is not a plain key")):
        code, lines = evaluate(_fixture(base, workflow=workflow), HOST)
        check(check_id, (code, _has(lines, marker)), (2, True))
    # A line-break character hides a line from the "\n" split: here an if: false on the suite step, written
    # with a tab after its colon. Each is cannot-evaluate, and a copy of this gate without the refusal reads
    # the hidden text as the step's name and passes (red on revert).
    source = _read_text(Path(__file__).resolve())
    anchor = "for char, label in " + "LINE_BREAKS.items():"
    if source.count(anchor) != 1:
        raise CannotEvaluate("red-on-revert anchor for the line-break refusal occurs {} times".format(
            source.count(anchor)))
    breaks = _copy(base, "python_host_pin_without_line_breaks",
                   source.replace(anchor, "for char, label in ():"))
    suite_name = "      - name: Suite\n"
    for check_id, char in (
            ("jobs/carriage-return-cannot-evaluate", "\r"),
            ("jobs/next-line-cannot-evaluate", "\x85"),
            ("jobs/line-separator-cannot-evaluate", "\u2028"),
            ("jobs/paragraph-separator-cannot-evaluate", "\u2029")):
        root = _fixture(base, workflow=_workflow(((OPF, suite_name, suite_name[:-1] + char
                                                   + "        if:\tfalse\n"),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "carries " + LINE_BREAKS[char]), breaks.evaluate(root, HOST)[0]),
              (2, True, 0))
    # A form feed or a vertical tab in a host-patch-job line: cannot-evaluate (CONTROL_CHARS), and a copy
    # of this gate without the refusal reads each line as part of the job and passes (red on revert).
    anchor = "for char, label in " + "CONTROL_CHARS.items():"
    if source.count(anchor) != 1:
        raise CannotEvaluate("red-on-revert anchor for the control-character refusal occurs {} "
                             "times".format(source.count(anchor)))
    controls = _copy(base, "python_host_pin_without_control_chars",
                     source.replace(anchor, "for char, label in ():"))
    for check_id, char in (
            ("jobs/form-feed-cannot-evaluate", "\f"),
            ("jobs/vertical-tab-cannot-evaluate", "\v")):
        root = _fixture(base, workflow=_workflow(((OPF, "    name: " + OPF + "\n",
                                                   "    name: " + OPF + char + " tooling\n"),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "carries " + CONTROL_CHARS[char]),
                         controls.evaluate(root, HOST)[0]), (2, True, 0))

    # A form feed or a vertical tab in an accepted host-patch-job header line: cannot-evaluate before
    # the header's continue, and the copy without the refusal reads each header, its comment included,
    # and passes (red on revert).
    for check_id, job, char in (
            ("jobs/opf-header-form-feed-cannot-evaluate", OPF, "\f"),
            ("jobs/opf-header-vertical-tab-cannot-evaluate", OPF, "\v"),
            ("jobs/git-fixture-env-header-form-feed-cannot-evaluate", GFE, "\f"),
            ("jobs/git-fixture-env-header-vertical-tab-cannot-evaluate", GFE, "\v")):
        root = _fixture(base, workflow=_workflow(((job, "  " + job + ":\n",
                                                   "  " + job + ": #" + char + "\n"),)))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "carries " + CONTROL_CHARS[char]),
                         controls.evaluate(root, HOST)[0]), (2, True, 0))

    # A form feed or a vertical tab in a line of the judge-naming job, its accepted header and its
    # comment lines included: cannot-evaluate, as for the host-patch jobs, and the copy without the
    # refusal reads the job and passes (red on revert).
    for check_id, edits, char in (
            ("jobs/jobs-only-header-form-feed-cannot-evaluate",
             (("registry", "  registry:\n", "  registry: #\f\n"),), "\f"),
            ("jobs/jobs-only-comment-form-feed-cannot-evaluate",
             (("registry", "    steps:\n", "    # judge\f notes\n    steps:\n"),), "\f"),
            ("jobs/jobs-only-name-vertical-tab-cannot-evaluate",
             (("registry", "Python host pin (jobs leg", "Python host pin\v (jobs leg"),), "\v")):
        root = _fixture(base, workflow=_workflow(edits))
        code, lines = evaluate(root, HOST)
        check(check_id, (code, _has(lines, "carries " + CONTROL_CHARS[char]),
                         controls.evaluate(root, HOST)[0]), (2, True, 0))

    # The job naming the --jobs-only judge is read with the host-patch jobs' own line model: a job
    # property at an unexpected indent (the job body re-indented by two spaces with a job-level env:, a
    # reviewer reproduction; its setup-python with: dropped so every line stays within MAX_INDENT and
    # the placement refusal is what fires), a quoted key YAML reads as env (on the job and on the judge
    # step) and an explicit ? key are each cannot-evaluate, and the copy with the outside-judge rule
    # removed never reads that job and passes (red on revert). Round-6 reviewer reproductions keep the
    # canonical registry judge and add a second job spelling the judge a way this model cannot read (a
    # quoted run key, a literal and a folded block scalar, a flow-mapping step, an anchor and an
    # alias): each is selected by its judge-naming line and refused, never skipped.
    judge_env = "      BASH_ENV: '$(: > \"$0\")'\n"
    trimmed = REGISTRY_JOB.replace("        with:\n          python-version: '3.14'\n", "")
    reindented = ("  registry:\n      env:\n  " + judge_env
                  + "".join("  " + text + "\n" for text in trimmed.split("\n", 1)[1].splitlines()))
    quoted_job_env = '    "env":\n' + judge_env + "    runs-on: "
    quoted_step_env = JUDGE_STEP + '        "env":\n    ' + judge_env
    explicit_key = "    ? env\n    : silencer\n    runs-on: "
    second = "  second:\n    runs-on: ubuntu-latest\n    steps:\n"
    for check_id, edits, marker in (
            ("jobs/jobs-only-job-reindented-cannot-evaluate",
             (("registry", REGISTRY_JOB, reindented),), "the indentation does not place"),
            ("jobs/jobs-only-job-quoted-env-cannot-evaluate",
             (("registry", "    runs-on: ", quoted_job_env),), "not a block mapping entry"),
            ("jobs/jobs-only-step-quoted-env-cannot-evaluate",
             (("registry", JUDGE_STEP, quoted_step_env),), "not a block mapping entry"),
            ("jobs/jobs-only-job-explicit-key-cannot-evaluate",
             (("registry", "    runs-on: ", explicit_key),), "not a block mapping entry"),
            ("jobs/jobs-only-quoted-key-second-judge-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + '      - "run": ' + JOBS_ONLY_RUN + "\n"),),
             "not a block mapping entry"),
            ("jobs/jobs-only-literal-block-second-judge-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - run: |\n          " + JOBS_ONLY_RUN + "\n"),),
             "not a block mapping entry"),
            ("jobs/jobs-only-folded-block-second-judge-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - run: >-\n          " + JOBS_ONLY_RUN + "\n"),),
             "not a block mapping entry"),
            ("jobs/jobs-only-flow-step-second-judge-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - {name: judge, run: " + JOBS_ONLY_RUN + "}\n"),),
             "not a block mapping entry"),
            ("jobs/jobs-only-anchor-alias-second-judge-cannot-evaluate",
             (("registry", JUDGE_STEP,
               JUDGE_STEP + second + "      - run: &judge " + JOBS_ONLY_RUN + "\n"
               "      - run: *judge\n"),),
             "not a block mapping entry")):
        root = _fixture(base, workflow=_workflow(edits))
        code, lines = evaluate(root, HOST)
        rules.JOB_RULES = JOB_RULES - {"outside-judge"}
        try:
            removed = rules.evaluate(root, HOST)[0]
        finally:
            rules.JOB_RULES = JOB_RULES
        check(check_id, (code, _has(lines, marker), removed), (2, True, 0))

    for check_id, version, shown in (
            ("interpreter/newer-patch-finding", (3, 14, 8, "final"), "Python 3.14.8 (final)"),
            ("interpreter/older-patch-finding", (3, 14, 3, "final"), "Python 3.14.3 (final)"),
            ("interpreter/candidate-finding", (3, 14, 4, "candidate"), "Python 3.14.4 (candidate)"),
            ("interpreter/other-minor-finding", (3, 15, 4, "final"), "Python 3.15.4 (final)")):
        code, lines = evaluate(_fixture(base), version)
        check(check_id, (code, _has(lines, "the running interpreter is " + shown)), (1, True))

    # The --jobs-only leg (the CI step of the registry job, outside the host-patch jobs): it skips
    # the interpreter leg, so it can run on the floor pin, and it still runs the jobs leg, so a
    # job-level env: placed in both host-patch jobs (which in CI silences every step of those jobs,
    # this gate's own steps included) is refused from outside them. Red on revert: a jobs_only that no
    # longer skips the interpreter leg fails the first check, and one that no longer runs the jobs leg
    # fails the second.
    check("jobs/jobs-only-skips-interpreter-leg",
          (evaluate(_fixture(base), (3, 14, 8, "final"), jobs_only=True)[0],
           evaluate(_fixture(base), (3, 14, 8, "final"))[0]), (0, 1))
    env_edit = "    env:\n      BASH_ENV: '$(: > \"$0\")'\n    runs-on: "
    root = _fixture(base, workflow=_workflow(((OPF, "    runs-on: ", env_edit),
                                              (GFE, "    runs-on: ", env_edit))))
    code, lines = evaluate(root, HOST, jobs_only=True)
    check("jobs/jobs-only-still-refuses-host-job-env", (code, _has(lines, "carries env:")), (1, True))

    # The --jobs-only CLI wiring: main() itself, on a pristine copy of this gate with its ROOT and sys
    # replaced, forwards the flag (the interpreter leg skipped on a mismatched interpreter, the jobs leg
    # still run on a damaged host-patch job). Red on revert: a copy whose main no longer forwards the
    # flag (jobs_only=False) fails the clean tree on the interpreter leg, and a copy whose main returns 0
    # without evaluating passes the damaged tree the intact main refuses.
    cli = _copy(base, "python_host_pin_cli", source)
    clean = _fixture(base)
    damaged = _fixture(base, workflow=_workflow(((OPF, "    runs-on: ", env_edit),
                                                 (GFE, "    runs-on: ", env_edit))))
    cli.ROOT, cli.sys = clean, _FakeSys((3, 14, 8, "final", 0))
    check("cli/jobs-only-skips-interpreter-leg", (cli.main(["--jobs-only"]), cli.main([])), (0, 1))
    cli.ROOT = damaged
    check("cli/jobs-only-refuses-damaged-host-job", cli.main(["--jobs-only"]), 1)
    anchor = "jobs_only=" + "bool(argv)"
    if source.count(anchor) != 1:
        raise CannotEvaluate("red-on-revert anchor for the CLI forwarding occurs {} times".format(
            source.count(anchor)))
    forced = _copy(base, "python_host_pin_cli_without_forwarding",
                   source.replace(anchor, "jobs_only=" + "False"))
    forced.ROOT, forced.sys = clean, _FakeSys((3, 14, 8, "final", 0))
    check("cli/forwarding-mutant-fails-clean-tree-red", forced.main(["--jobs-only"]), 1)
    anchor = 'if not argv or argv == ["--jobs-' + 'only"]:'
    if source.count(anchor) != 1:
        raise CannotEvaluate("red-on-revert anchor for the CLI dispatch occurs {} times".format(
            source.count(anchor)))
    silent = _copy(base, "python_host_pin_cli_silent_pass",
                   source.replace(anchor, "if argv:\n        return 0\n    " + anchor))
    silent.ROOT, silent.sys = damaged, _FakeSys((3, 14, 4, "final", 0))
    check("cli/silent-pass-mutant-passes-damaged-tree-red", silent.main(["--jobs-only"]), 0)

    try:
        real = load_source(ROOT) + ("final",)
    except CannotEvaluate as exc:
        real = None
        FAILURES.append("real/source: {}".format(exc))
    if real is not None:
        check("real/tree-passes-at-its-host-patch", evaluate(ROOT, real)[0], 0)
    # The python-floor gate accepts a host-patch pin only inside these jobs of this workflow, read from this
    # source in this form, and refuses these line-break characters: its copies of those names must be these.
    floor = _copy(base, "python_floor_names", _read_text(ROOT / "tools" / "check_python_floor.py"))
    check("real/floor-gate-agrees",
          (tuple(floor.HOST_JOBS), floor.HOST_WORKFLOW_REL, floor.HOST_SOURCE_REL,
           floor.HOST_PATCH_RE.pattern, floor.LINE_BREAKS),
          (HOST_JOBS, WORKFLOW_REL, SOURCE_REL, PATCH_RE.pattern, LINE_BREAKS))
    _red_on_revert(base)


def _red_on_revert(base):
    """Each finding leg red on its fixture with the intact gate, which names that leg's own finding, and
    green on the same fixture in a copy of this gate, loaded through importlib, with that leg's call
    removed."""
    source = _read_text(Path(__file__).resolve())
    cases = {"jobs": (dict(workflow=_workflow(((OPF, PIN, "python-version: '3.14.8'"),))), HOST,
                      "differs from the host patch"),
             "interpreter": ({}, (3, 14, 8, "final"), "the running interpreter is Python 3.14.8")}
    for leg, call in REVERT_CALLS:
        line = "findings.extend(" + call + ")"
        if source.count(line) != 1:
            raise CannotEvaluate("red-on-revert anchor for the {} leg occurs {} times".format(
                leg, source.count(line)))
        module = _copy(base, "python_host_pin_without_" + leg, source.replace(line, "findings.extend(())"))
        kwargs, version, marker = cases[leg]
        root = _fixture(base, **kwargs)
        code, lines = evaluate(root, version)
        check("revert/{}-leg".format(leg), (code, _has(lines, marker), module.evaluate(root, version)[0]),
              (1, True, 0))
    # The parse mapping: a copy whose load_source maps only TOMLDecodeError lets the injected key-depth
    # RecursionError escape, which the intact gate reports as cannot-evaluate.
    anchor = "except (ValueError, " + "RecursionError) as exc:"
    if source.count(anchor) != 1:
        raise CannotEvaluate("red-on-revert anchor for the parse mapping occurs {} times".format(
            source.count(anchor)))
    module = _copy(base, "python_host_pin_without_parse_mapping", source.replace(anchor, "except () as exc:"))
    root = _fixture(base, source=SOURCE_TEXT + ".".join(["k"] * 1001) + " = 1\n")
    check("revert/parse-mapping",
          (_parse_refusal(evaluate, root, True), _parse_refusal(module.evaluate, root, True)),
          ((2, True), "raised RecursionError"))


def self_test():
    try:
        with tempfile.TemporaryDirectory(prefix="python-host-pin-selftest-") as raw:
            _self_test_cases(Path(raw))
    except (CannotEvaluate, OSError) as exc:
        print("SELF-TEST HARNESS ERROR: {}".format(exc), file=sys.stderr)
        return 2
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} checks executed (each finding leg and each job rule red on its fixture and "
          "green with it removed)".format(len(EXECUTED)))
    return 0


def main(argv):
    if not argv or argv == ["--jobs-only"]:
        code, lines = evaluate(ROOT, tuple(sys.version_info[:4]), jobs_only=bool(argv))
        for line in lines:
            print(line, file=sys.stderr if code == 2 else sys.stdout)
        return code
    if argv == ["--self-test"]:
        return self_test()
    print("usage: check_python_host_pin.py [--jobs-only | --self-test]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
