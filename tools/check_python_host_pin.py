#!/usr/bin/env python3
"""Python host-patch pin gate (PYTHON-HOST-PIN): the single source .aiqt/python-host.toml names the exact
CPython patch release the maintainers' host runs, and the host-patch CI jobs run their suites on exactly
that release.

Every other job in .github/workflows/quality.yml pins python-version '3.14', which resolves to a 3.14 patch
release the runner provides (setup-python takes a cached 3.14 without checking for a newer patch release;
check-latest is off), while the maintainers' host runs one fixed distribution patch release, so a change can
pass every local suite and fail only in CI on a patch-release difference. The host-patch jobs (HOST_JOBS)
run the suites a newer patch release most often breaks on the host's own patch release, beside the '3.14'
jobs of quality.yml, which stay required and unchanged. A passing local suite on the host patch then
predicts a required CI leg, and a failure only on a '3.14' leg is diagnostic evidence of a patch-release
difference, not proof that the change is innocent. This gate keeps each host-patch job whole and the
statements of that patch release in agreement.

Legs, in order:
  source       the single source is a regular UTF-8 file that parses and carries exactly the keys
               format-version (the integer 1) and python-host-patch (a "MAJOR.MINOR.PATCH" string with no
               leading zero and at most four digits in each component, PATCH_RE); anything else is
               cannot-evaluate, and so is a source that tomllib refuses with a RecursionError (deep nesting)
               or a bare ValueError (an over-long integer), and a component that does not convert.
  jobs         .github/workflows/quality.yml carries each HOST_JOBS job, and each is whole (JOB_RULES):
                 job-keys    the job carries no key but name, runs-on and steps (an if:, continue-on-error:,
                             env:, container: or strategy: can skip, mask or move what it runs);
                 runner      runs-on is RUNNER, ubuntu-24.04: the setup-python versions manifest offers a
                             patch release only for named images, so the job names its image;
                 setup       exactly one step uses setup-python; it carries no key but name, uses and with:,
                             its with: holds only python-version, and that is the one line in the job that
                             names python-version (a job-level env: or another key never counts as the pin);
                 pin         that python-version is the host patch;
                 gate-step   the step right after the setup-python step is the Python host pin step, an
                             executable step (no key but name and run) whose run: is GATE_RUN;
                 suite-step  an executable step after the setup-python step runs the job's suite,
                             HOST_SUITES (opf-host-patch: the OPF tooling self-test;
                             git-fixture-env-host-patch: the git-fixture-env behaviour self-test).
               So removing, moving, renaming or disabling (if:, continue-on-error:) a host-patch job, its
               setup-python step, its Python host pin step or its suite step is a finding, and so is
               another runner. Jobs are read by a line model: under the top-level jobs: key, a job is a
               plain key at indent 2 and its content sits at indent 4 or deeper; any other line there, and a
               repeated job id, is cannot-evaluate. Inside a HOST_JOBS job, each line is a block mapping
               entry, or a "- " list item holding one, at indent 10 or less: a plain key, a colon, then
               nothing, a one-line quoted scalar or a plain scalar that opens no flow, block scalar, anchor,
               alias or tag (ENTRY_RE). Any other line (a run: | block among them), a repeated key and a line
               the indentation does not place are cannot-evaluate, and so is a line (a comment included)
               that names python-version in any case and is not one strict pin line (PIN_LINE_RE).
  interpreter  the running interpreter (sys.version_info) is the host patch, a final release. A difference
               is a finding (exit 1): run locally, it reports that this interpreter is not the committed
               host patch, so a passing local suite does not predict the host-patch CI jobs, and
               tools/run_all_checks.sh FAILS there; run in a host-patch CI job, it reports that the job did
               not run the pinned release.

  check_python_host_pin.py              run every leg over this repository
  check_python_host_pin.py --self-test  fixture trees for every leg and every job rule, plus red-on-revert:
                                        for each finding leg, a copy of this gate loaded through importlib
                                        with that leg's call removed passes the fixture the intact gate
                                        refuses; for each job rule, a copy with that rule removed from
                                        JOB_RULES passes each of that rule's cases; and a copy whose source
                                        parse maps only TOMLDecodeError lets a parser RecursionError escape

Exit convention: 0 every leg passes; 1 a finding; 2 usage or cannot-evaluate (a missing, unreadable,
non-regular, symlinked, non-UTF-8 or malformed source or workflow, a line the line model cannot place or
read, a pin line outside the strict form, unavailable temporary storage).

DISCLOSED RESIDUAL. The jobs leg reads only quality.yml and only the HOST_JOBS jobs, and judges only their
keys, runner, setup-python step, Python host pin step and suite step: another step (the checkout, or one
that starts another interpreter) runs as written, and what the suites test is theirs. The grammar of the
whole workflow, the setup-python uses: shape and every other pin belong to the python-floor gate
(tools/check_python_floor.py), which accepts a pin of a patch release only inside the HOST_JOBS jobs of
quality.yml and only when it equals this source, and holds every other python-version in any workflow to
the floor exactly. GitHub's runtime is not modelled: when the runner image retires or the host patch has no
build for it, setup-python fails the job, closed and never on another patch release; ubuntu-26.04 has no
3.14.4 build, so the move off ubuntu-24.04 is one reviewed change of RUNNER, both runs-on lines and, if
needed, the host patch. The interpreter leg judges only the interpreter that runs this gate; in CI it runs
only in the HOST_JOBS jobs. Equal patch releases are not equal builds: a distribution build's downstream
patches and the system libraries an interpreter links (OpenSSL, SQLite, expat, zlib and others) can differ
between the host and the runner, and this gate does not compare them. It does not check that the committed
patch is what the host runs: when the host interpreter changes, the maintainer updates the source and both
host-patch pins in one reviewed change, and until then every local run fails on the interpreter leg. CI
cost: the two jobs run the OPF tooling self-test and the git-fixture-env suite a second time in every run
of the workflow, and an edit to a pull request's title or body starts a run (the workflow runs on edited).

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
# The step right after a host-patch job's setup-python step: this gate, which fails there when the job does
# not run the host patch.
GATE_RUN = "python3 -I -B tools/check_python_host_pin.py"
# The runner image of each host-patch job. The setup-python versions manifest offers a patch release only
# for named images (3.14.4 for ubuntu-22.04 and ubuntu-24.04, not ubuntu-26.04), so ubuntu-latest fails the
# jobs once it moves to an image the host patch has no build for.
RUNNER = "ubuntu-24.04"
JOB_KEYS = {"name", "runs-on", "steps"}
SETUP_KEYS = {"name", "uses", "with"}
RUN_KEYS = {"name", "run"}
# The rules of the jobs leg; the self-test removes each from a copy of this gate to show its cases red on
# revert.
JOB_RULES = frozenset({"job-keys", "runner", "setup", "pin", "gate-step", "suite-step"})
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


def _entry(job, number, line):
    """(line number, indentation, key column, list item, key, value) for one content line of a host-patch
    job, or CannotEvaluate for a line ENTRY_RE does not read."""
    match = ENTRY_RE.fullmatch(line)
    plain = None if match is None else match.group("plain")
    if match is None or (plain is not None and (": " in plain or plain.endswith(":"))):
        raise CannotEvaluate("{}:{}: a line in the host-patch job {} that is not a block mapping entry with "
                             "a one-line scalar value (a block scalar, flow, anchor, alias or tag among "
                             "them); this job model cannot read it".format(WORKFLOW_REL, number, job))
    indent, dash = len(match.group("indent")), match.group("dash") or ""
    if indent > MAX_INDENT:
        raise CannotEvaluate("{}:{}: a line in the host-patch job {} nested deeper than a step input; this "
                             "job model cannot read it".format(WORKFLOW_REL, number, job))
    value = next((group for group in match.group("single", "double", "plain") if group is not None), None)
    if value is not None:
        value = _Scalar(value)
        value.line = number
    return number, indent, indent + len(dash), bool(dash), match.group("key"), value


def _job_tree(job, entries):
    """The block mapping of a host-patch job from its entries (_entry), or CannotEvaluate for an entry the
    indentation does not place or a repeated key. MAX_INDENT bounds the nesting, so the recursion is
    shallow."""
    position = [0]

    def refuse(entry, why):
        raise CannotEvaluate("{}:{}: {} in the host-patch job {}; this job model cannot read it".format(
            WORKFLOW_REL, entry[0], why, job))

    def node(entry):
        if entry[5] is not None:
            return entry[5]
        if position[0] < len(entries) and entries[position[0]][1] > entry[2]:
            child = entries[position[0]]
            return sequence(child[1]) if child[3] else mapping(child[2], False)
        return None

    def mapping(column, item):
        # item: the first entry is the "- " line that opens this mapping as a list item.
        out = {}
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
                found.append("{} carries {}:, which can skip, mask or move what it runs (a host-patch job "
                             "carries only name, runs-on and steps)".format(where, key))
    if "runner" in JOB_RULES and tree.get("runs-on") != RUNNER:
        found.append("{} runs on {!r}, not {!r}: the setup-python versions manifest offers the host patch "
                     "only for named images, so the job names its image".format(
                         where, tree.get("runs-on"), RUNNER))
    steps = tree.get("steps")
    if not isinstance(steps, list) or not steps:
        return found + ["{} carries no steps list".format(where)]
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


def job_findings(root, patch):
    """Each HOST_JOBS job of WORKFLOW_REL is whole (JOB_RULES) and pins the host patch."""
    want = _patch_text(patch)
    entries = {job: [] for job in HOST_JOBS}
    seen, in_jobs, job = set(), False, None
    for number, line in enumerate(_read_text(root / WORKFLOW_REL).split("\n"), 1):
        line = line[:-1] if line.endswith("\r") else line
        stripped = line.strip(" \t")
        if not stripped:
            continue
        comment = stripped.startswith("#")
        if not comment and not line.startswith((" ", "\t")):
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
                continue
            if not line.startswith("    "):
                raise CannotEvaluate("{}:{}: a line under jobs: that is neither a job id at indent 2 nor job "
                                     "content at indent 4 or deeper; this line model cannot place it".format(
                                         WORKFLOW_REL, number))
        if job not in entries:
            continue
        if PIN_KEY in line.lower() and (comment or PIN_LINE_RE.fullmatch(line) is None):
            raise CannotEvaluate("{}:{}: names python-version in the host-patch job {} but is not one strict "
                                 "pin line; write it as python-version: '{}'".format(
                                     WORKFLOW_REL, number, job, want))
        if not comment:
            entries[job].append(_entry(job, number, line))
    findings = []
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


def evaluate(root, version):
    """Run every leg over root, judging the interpreter version; return (exit code, report lines)."""
    try:
        patch = load_source(root)
        findings = []
        findings.extend(job_findings(root, patch))
        findings.extend(interpreter_findings(patch, version))
    except CannotEvaluate as exc:
        return 2, ["CANNOT EVALUATE: {}".format(exc)]
    if findings:
        return 1, ["FAIL: " + finding for finding in findings]
    return 0, ["PASS: python host patch {} ({}): the host-patch jobs {} in {} each run on {}, pin it in "
               "their one setup-python step, run this gate right after it and run their suite, and this "
               "interpreter is Python {}".format(_patch_text(patch), SOURCE_REL, ", ".join(HOST_JOBS),
                                                 WORKFLOW_REL, RUNNER, _patch_text(version[:3]))]


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


def check(name, got, want):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


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
            "        with:\n          fetch-depth: 0\n\n".format(job, RUNNER) + SETUP_STEP + GATE_STEP
            + _suite_step(job))


def _workflow(edits=(), omit=()):
    """quality.yml: a floor job, then each host-patch job not in omit with its edits applied in order; an
    edit (job, old, new) replaces the one occurrence of old in that job."""
    jobs = [FLOOR_JOB]
    for job in HOST_JOBS:
        if job in omit:
            continue
        text = _host_job(job)
        for name, old, new in edits:
            if name == job:
                if text.count(old) != 1:
                    raise CannotEvaluate("self-test edit {!r} occurs {} times in {}".format(
                        old, text.count(old), job))
                text = text.replace(old, new)
        jobs.append(text)
    return "name: Quality\n\non: push\n\njobs:\n" + "".join(jobs)


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
    parse_key(src, pos) -> (pos, key) raises _ParserMismatch."""
    def parse_key(*args, **kwargs):
        calls.append(len(args))
        try:
            result = real(*args, **kwargs)
        except TypeError as exc:
            raise _ParserMismatch("calling it raised TypeError: {}".format(exc))
        if not (isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], tuple)):
            raise _ParserMismatch("it returned {} rather than (pos, key)".format(type(result).__name__))
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


def _job_cases():
    """Each jobs-leg finding case: (check id, edits, omitted jobs, a marker of its finding, and the one
    JOB_RULES rule whose removal lets it pass, or None for a case more than one rule refuses)."""
    suite = {job: _suite_step(job) for job in HOST_JOBS}
    gate_name = "      - name: Python host pin\n"
    suite_name = "      - name: Suite\n"
    setup_uses = "      - uses: actions/setup-python@v5\n"
    runs = "    runs-on: "
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
        ("jobs/second-pin-line-finding",
         ((OPF, "          fetch-depth: 0\n", "          fetch-depth: 0\n          " + PIN + "\n"),), (),
         "opf-host-patch carries 2 python-version pins", "setup"),
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
             "this line model cannot place it")):
        code, lines = evaluate(_fixture(base, workflow=workflow), HOST)
        check(check_id, (code, _has(lines, marker)), (2, True))

    for check_id, version, shown in (
            ("interpreter/newer-patch-finding", (3, 14, 8, "final"), "Python 3.14.8 (final)"),
            ("interpreter/older-patch-finding", (3, 14, 3, "final"), "Python 3.14.3 (final)"),
            ("interpreter/candidate-finding", (3, 14, 4, "candidate"), "Python 3.14.4 (candidate)"),
            ("interpreter/other-minor-finding", (3, 15, 4, "final"), "Python 3.15.4 (final)")):
        code, lines = evaluate(_fixture(base), version)
        check(check_id, (code, _has(lines, "the running interpreter is " + shown)), (1, True))

    try:
        real = load_source(ROOT) + ("final",)
    except CannotEvaluate as exc:
        real = None
        FAILURES.append("real/source: {}".format(exc))
    if real is not None:
        check("real/tree-passes-at-its-host-patch", evaluate(ROOT, real)[0], 0)
    # The python-floor gate accepts a host-patch pin only inside these jobs of this workflow, read from this
    # source in this form: its copies of those names must be these.
    floor = _copy(base, "python_floor_names", _read_text(ROOT / "tools" / "check_python_floor.py"))
    check("real/floor-gate-agrees",
          (tuple(floor.HOST_JOBS), floor.HOST_WORKFLOW_REL, floor.HOST_SOURCE_REL,
           floor.HOST_PATCH_RE.pattern), (HOST_JOBS, WORKFLOW_REL, SOURCE_REL, PATCH_RE.pattern))
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
    if not argv:
        code, lines = evaluate(ROOT, tuple(sys.version_info[:4]))
        for line in lines:
            print(line, file=sys.stderr if code == 2 else sys.stdout)
        return code
    if argv == ["--self-test"]:
        return self_test()
    print("usage: check_python_host_pin.py [--self-test]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
