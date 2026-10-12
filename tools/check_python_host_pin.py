#!/usr/bin/env python3
"""Python host-patch pin gate (PYTHON-HOST-PIN): the single source .aiqt/python-host.toml names the exact
CPython patch release the maintainers' host runs, and the host-patch CI jobs pin exactly that release.

Every other job in .github/workflows/quality.yml pins python-version '3.14', which the runner resolves to
the newest 3.14 patch release, while the maintainers' host runs one fixed distribution patch release, so a
change can pass every local suite and fail only in CI on a patch-release difference. The host-patch jobs
(HOST_JOBS) run the suites a newer patch release most often breaks on the host's own patch release, beside
the '3.14' jobs, which stay required and unchanged: a passing local suite then predicts a required CI leg,
and a failure only on a '3.14' leg points at the patch release, not the change. This gate keeps the
statements of that patch release in agreement.

Legs, in order:
  source       the single source is a regular UTF-8 file that parses and carries exactly the keys
               format-version (the integer 1) and python-host-patch (a "MAJOR.MINOR.PATCH" string with no
               leading zero, PATCH_RE); anything else is cannot-evaluate, and so is a source that tomllib
               refuses with a RecursionError (deep nesting) or a bare ValueError (an over-long integer).
  pins         .github/workflows/quality.yml carries each HOST_JOBS job, and each carries exactly one
               python-version pin, equal to the host patch. Jobs are read by a line model: under the
               top-level jobs: key, a job is a plain key at indent 2 and its content sits at indent 4 or
               deeper; any other line there, and a repeated job id, is cannot-evaluate. Inside a HOST_JOBS
               job, a line (a comment included) that names python-version in any case and is not one strict
               pin line (PIN_LINE_RE) is cannot-evaluate.
  interpreter  the running interpreter (sys.version_info) is the host patch, a final release. A difference
               is a finding (exit 1): run locally, it reports that this interpreter has drifted from the
               committed host patch, so a passing local suite no longer predicts the host-patch CI jobs;
               run in a host-patch CI job, it reports that the job did not run the pinned release.

  check_python_host_pin.py              run every leg over this repository
  check_python_host_pin.py --self-test  fixture trees for every leg, plus red-on-revert: for each finding
                                        leg, a copy of this gate loaded through importlib with that leg's
                                        call removed passes the fixture the intact gate refuses, and a
                                        copy whose source parse maps only TOMLDecodeError lets a parser
                                        RecursionError escape

Exit convention: 0 every leg passes; 1 a finding; 2 usage or cannot-evaluate (a missing, unreadable,
non-regular, symlinked, non-UTF-8 or malformed source or workflow, a line the line model cannot place, a
pin line outside the strict form, unavailable temporary storage).

DISCLOSED RESIDUAL. The pins leg reads only quality.yml and only the HOST_JOBS jobs. The grammar of the
whole workflow, the setup-python step shape and every other pin belong to the python-floor gate
(tools/check_python_floor.py), which accepts a workflow pin of a patch release only when it equals this
source. The interpreter leg judges only the interpreter that runs this gate. In CI it runs only in the
HOST_JOBS jobs (in a '3.14' job the newest patch release runs by design), so a runner image that ignores
the pin for another step or job, a step that starts another interpreter, and a job outside HOST_JOBS are
not seen. Equal patch releases are not equal builds: a distribution build's downstream patches and the
system libraries an interpreter links (OpenSSL, SQLite, expat, zlib and others) can differ between the
host and the runner, and this gate does not compare them. It does not check that the committed patch is
what the host runs: when the host interpreter changes, the maintainer updates the source (a reviewed
change that also moves the workflow pins), and until then the interpreter leg reports the drift on each
local run.

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
# The host-patch jobs of WORKFLOW_REL: each runs on the host patch and carries exactly one pin of it.
HOST_JOBS = ("opf-host-patch", "git-fixture-env-host-patch")
PATCH_RE = re.compile(r"([1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
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


class CannotEvaluate(Exception):
    """An input this gate cannot read or interpret; reported as exit 2, never as a pass."""


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
        raise CannotEvaluate("{}: python-host-patch must be a \"MAJOR.MINOR.PATCH\" string, got {!r}".format(
            SOURCE_REL, text))
    return tuple(int(part) for part in match.groups())


def _patch_text(patch):
    return "%d.%d.%d" % tuple(patch)


def pin_findings(root, patch):
    """Each HOST_JOBS job of WORKFLOW_REL carries exactly one strict pin, equal to the host patch."""
    want = _patch_text(patch)
    pins = {job: [] for job in HOST_JOBS}
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
        if job not in pins or PIN_KEY not in line.lower():
            continue
        match = None if comment else PIN_LINE_RE.fullmatch(line)
        if match is None:
            raise CannotEvaluate("{}:{}: names python-version in the host-patch job {} but is not one strict "
                                 "pin line; write it as python-version: '{}'".format(
                                     WORKFLOW_REL, number, job, want))
        value = next(group for group in match.group("plain", "single", "double") if group is not None)
        pins[job].append((number, value))
    findings = []
    for name in HOST_JOBS:
        if name not in seen:
            findings.append("{}: carries no job {} (a host-patch job, HOST_JOBS in "
                            "tools/check_python_host_pin.py)".format(WORKFLOW_REL, name))
            continue
        if len(pins[name]) != 1:
            findings.append("{}: the host-patch job {} carries {} python-version pins (want exactly one, "
                            "{!r})".format(WORKFLOW_REL, name, len(pins[name]), want))
        for number, value in pins[name]:
            if value != want:
                findings.append("{}:{}: python-version {!r} in the host-patch job {} differs from the host "
                                "patch {!r} that {} names".format(WORKFLOW_REL, number, value, name, want,
                                                                  SOURCE_REL))
    return findings


def interpreter_findings(patch, version):
    """The running interpreter, version = (major, minor, micro, releaselevel), is the host patch, final."""
    if tuple(version[:3]) == tuple(patch) and version[3] == "final":
        return []
    return ["the running interpreter is Python {}.{}.{} ({}), but {} names the host patch {}: a passing "
            "local suite no longer predicts the host-patch CI jobs; run on Python {}, or, when the host "
            "interpreter has changed, update {} and the host-patch pins in {} together".format(
                *version[:4], SOURCE_REL, _patch_text(patch), _patch_text(patch), SOURCE_REL, WORKFLOW_REL)]


def evaluate(root, version):
    """Run every leg over root, judging the interpreter version; return (exit code, report lines)."""
    try:
        patch = load_source(root)
        findings = []
        findings.extend(pin_findings(root, patch))
        findings.extend(interpreter_findings(patch, version))
    except CannotEvaluate as exc:
        return 2, ["CANNOT EVALUATE: {}".format(exc)]
    if findings:
        return 1, ["FAIL: " + finding for finding in findings]
    return 0, ["PASS: python host patch {} ({}): the host-patch jobs {} pin it in {}, and this interpreter "
               "is Python {}".format(_patch_text(patch), SOURCE_REL, ", ".join(HOST_JOBS), WORKFLOW_REL,
                                     _patch_text(version[:3]))]


# ---------------------------------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------------------------------

FAILURES = []
EXECUTED = []
# Red-on-revert: each finding leg's call in evaluate(), removed in a copy of this gate.
REVERT_CALLS = (("pins", "pin_findings(root, patch)"),
                ("interpreter", "interpreter_findings(patch, version)"))
HOST = (3, 14, 4, "final")
SOURCE_TEXT = "format-version = 1\npython-host-patch = \"3.14.4\"\n"
DEFAULT_JOBS = (("floor", "'3.14'"), ("opf-host-patch", "'3.14.4'"),
                ("git-fixture-env-host-patch", "'3.14.4'"))


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


def _job(name, pin):
    """One job; pin is the text after the python-version key, or None for a job with no setup step."""
    text = "  {}:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n".format(name)
    if pin is not None:
        text += ("      - uses: actions/setup-python@v5\n        with:\n"
                 "          python-version: {}\n".format(pin))
    return text + "      - run: python3 -I -B tools/x.py\n"


def _fixture(base, source=SOURCE_TEXT, jobs=DEFAULT_JOBS, workflow=None):
    """A tree with the single source (absent when source is None) and quality.yml built from jobs, or
    the given workflow text."""
    root = Path(tempfile.mkdtemp(prefix="tree-", dir=base))
    if source is not None:
        _write(root, SOURCE_REL, source)
    if workflow is None:
        workflow = "name: Quality\n\non: push\n\njobs:\n" + "".join(_job(name, pin) for name, pin in jobs)
    _write(root, WORKFLOW_REL, workflow)
    return root


def _has(lines, marker):
    return any(marker in line for line in lines)


def _jobs(**pins):
    """DEFAULT_JOBS with the named host jobs pinned otherwise (underscores for hyphens)."""
    return tuple((name, pins.get(name.replace("-", "_"), pin)) for name, pin in DEFAULT_JOBS)


def _key_depth_bounded(real):
    """tomllib's key parser, made to raise RecursionError on a key of more than 1000 dotted parts, as a
    release that bounds key depth does."""
    def parse_key(src, pos):
        pos, key = real(src, pos)
        if len(key) > 1000:
            raise RecursionError("injected: a key of more than 1000 dotted parts")
        return pos, key
    return parse_key


def _parse_refusal(evaluate_tree, root, bound):
    """(exit code, whether the report says the source does not parse) for evaluate_tree over root at HOST,
    with tomllib's key parser bounded when bound is true; "raised NAME" when the exception escapes."""
    real = tomllib._parser.parse_key
    if bound:
        tomllib._parser.parse_key = _key_depth_bounded(real)
    try:
        code, lines = evaluate_tree(root, HOST)
    except (RecursionError, ValueError) as exc:
        return "raised " + type(exc).__name__
    finally:
        tomllib._parser.parse_key = real
    return code, _has(lines, SOURCE_REL + ": does not parse")


def _self_test_cases(base):
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

    root = _fixture(base)
    (root / WORKFLOW_REL).unlink()
    code, lines = evaluate(root, HOST)
    check("pins/workflow-missing-cannot-evaluate", (code, _has(lines, "quality.yml: cannot stat")), (2, True))
    code, lines = evaluate(_fixture(base, jobs=_jobs(opf_host_patch="'3.14.8'")), HOST)
    check("pins/newer-patch-finding",
          (code, _has(lines, "python-version '3.14.8' in the host-patch job opf-host-patch differs")),
          (1, True))
    code, lines = evaluate(_fixture(base, jobs=_jobs(git_fixture_env_host_patch="'3.14'")), HOST)
    check("pins/floor-pin-in-host-job-finding",
          (code, _has(lines, "python-version '3.14' in the host-patch job git-fixture-env-host-patch")),
          (1, True))
    code, lines = evaluate(_fixture(base, jobs=DEFAULT_JOBS[:2]), HOST)
    check("pins/host-job-missing-finding", (code, _has(lines, "carries no job git-fixture-env-host-patch")),
          (1, True))
    code, lines = evaluate(_fixture(base, jobs=_jobs(opf_host_patch=None)), HOST)
    check("pins/host-job-without-pin-finding", (code, _has(lines, "opf-host-patch carries 0 python-version")),
          (1, True))
    code, lines = evaluate(_fixture(base, jobs=_jobs(
        opf_host_patch="'3.14.4'\n      - uses: actions/setup-python@v5\n        with:\n"
        "          python-version: '3.14.4'")), HOST)
    check("pins/host-job-two-pins-finding", (code, _has(lines, "opf-host-patch carries 2 python-version")),
          (1, True))
    for check_id, pin in (
            ("pins/plain-pin-passes", "3.14.4"),
            ("pins/double-quoted-pin-passes", "\"3.14.4\""),
            ("pins/trailing-comment-passes", "'3.14.4'  # the host patch")):
        check(check_id, evaluate(_fixture(base, jobs=_jobs(opf_host_patch=pin)), HOST)[0], 0)
    check("pins/other-job-pin-ignored-passes",
          evaluate(_fixture(base, jobs=(("floor", "'3.13'"),) + DEFAULT_JOBS[1:]), HOST)[0], 0)
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
        code, lines = evaluate(_fixture(base, jobs=_jobs(opf_host_patch=pin)), HOST)
        check(check_id, (code, _has(lines, strict)), (2, True))
    for check_id, workflow, marker in (
            ("pins/repeated-job-cannot-evaluate", "jobs:\n" + _job("opf-host-patch", "'3.14.4'")
             + _job("git-fixture-env-host-patch", "'3.14.4'") + _job("opf-host-patch", "'3.14.8'"),
             "the job id opf-host-patch repeats"),
            ("pins/tab-indent-cannot-evaluate", "jobs:\n\topf-host-patch:\n",
             "this line model cannot place it"),
            ("pins/quoted-job-id-cannot-evaluate", "jobs:\n  \"opf-host-patch\":\n",
             "this line model cannot place it"),
            ("pins/indent-three-cannot-evaluate", "jobs:\n" + _job("opf-host-patch", "'3.14.4'")
             + "   x: 1\n", "this line model cannot place it")):
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
    _red_on_revert(base)


def _red_on_revert(base):
    """Each finding leg red on its fixture with the intact gate, which names that leg's own finding, and
    green on the same fixture in a copy of this gate, loaded through importlib, with that leg's call
    removed."""
    source = _read_text(Path(__file__).resolve())
    cases = {"pins": (dict(jobs=_jobs(opf_host_patch="'3.14.8'")), HOST, "differs from the host patch"),
             "interpreter": ({}, (3, 14, 8, "final"), "the running interpreter is Python 3.14.8")}
    for leg, call in REVERT_CALLS:
        line = "findings.extend(" + call + ")"
        if source.count(line) != 1:
            raise CannotEvaluate("red-on-revert anchor for the {} leg occurs {} times".format(
                leg, source.count(line)))
        name = "python_host_pin_without_" + leg
        _write(base, name + ".py", source.replace(line, "findings.extend(())"))
        spec = importlib.util.spec_from_file_location(name, base / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
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
    name = "python_host_pin_without_parse_mapping"
    _write(base, name + ".py", source.replace(anchor, "except () as exc:"))
    spec = importlib.util.spec_from_file_location(name, base / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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
    print("SELF-TEST PASS: {} checks executed (each finding leg red on its fixture and green with its call "
          "removed)".format(len(EXECUTED)))
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
