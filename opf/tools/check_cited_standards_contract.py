#!/usr/bin/env python3
"""Pin OPF's cited-standards corpus and its gate invocation in the OPF runner, outside the portable gate.

tools/check_cited_standards.py (OPF's own copy of the gate) scans whatever corpus its config names under
whatever tree --root names, and its self-test vectors carry their own corpus, so none of that notices when
OPF stops scanning a prompt-pack member or the runner redirects the scan. This contract reads
tools/cited-standards.toml (paths relative to the OPF root, the directory above this file) and fails
(exit 1) unless its corpus is present and expands, through the gate's own expand_corpus, to every member
prompt-pack/pack.toml lists; and unless every cited-standards gate line of tools/run_all_checks.sh other
than the exact self-test line is exactly the one permitted invocation, the gate with --config
"$here/cited-standards.toml" and nothing else, so an appended --root or any other extra argument is a
finding. A run_gate line names the gate when one of its words, after shell quote removal, has the
basename check_cited_standards.py. pack.toml is validated by its owning validator
(tools/check_opf_prompt_pack.py, imported, never re-implemented); a pack that validator refuses, a
missing or malformed config or runner, a runner line that cannot be split into words, a corpus glob the
gate cannot expand, a pack.toml re-read that fails after validation, an argument list other than none or
--self-test alone (a usage error), an unexpected exception while the check runs, or an import failure of
a collaborator module (guarded at module level, so a renamed collaborator symbol exits 2, never a
traceback exit 1), is cannot-evaluate (exit 2).

Every collaborator sits beside this file in OPF's tools directory, so the contract runs in an OPF tree
with no parent repository present. The parent repository's .github/check_cited_standards_contract.py
keeps the rule-file corpus assertion for the parent's own config, and it still checks prompt-pack
coverage too: both contracts check that coverage until the parent's half is retired, this one over
OPF's own config and runner.

--self-test drives the real entry point, main, over fixture vectors and asserts main's exit code and its
diagnostics for every finding and every cannot-evaluate path above, among them the --root redirect, the
permitted line plus a forbidden one sorting after it and one sorting before it (so a loop over only the
first or only the last sorted gate line is red), a self-test line with extra arguments (live, never
excluded), the post-validation pack.toml re-read failure (the owning validator stubbed to accept), the
usage error, the unexpected-exception backstop, the self-test harness backstop and, in a fresh process
over a scratch copy whose stub collaborator renames validate_pack away, the import-time guard.
This guards declaration and wiring drift, not environment overrides, a member the gate scans and reads
short, or the content of recorded exclusions. It reads the OPF runner's text; it does not run the runner.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_cited_standards_contract.py requires Python 3.14 or newer; this is Python %d.%d.%d "
            "(%s). Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import hashlib
import io
import os
import shlex
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from check_cited_standards import CannotEvaluate, expand_corpus, load_config  # noqa: E402
    from check_opf_prompt_pack import PACK_FORMAT, VALID, compute_digest, validate_pack  # noqa: E402
except Exception:  # noqa: BLE001  an unimportable collaborator is cannot-evaluate, never a finding
    import os
    try:
        sys.stderr.write("error: OPF cited-standards contract: cannot evaluate: cannot import a collaborator "
                         "module (check_cited_standards or check_opf_prompt_pack beside this file)\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

CONFIG = "tools/cited-standards.toml"
PACK = "prompt-pack"
RUNNER = "tools/run_all_checks.sh"
GATE_NAME = "check_cited_standards.py"
LAUNCH = ["python3", "-I", "-B", "$here/" + GATE_NAME]
# The one permitted live invocation, as the words after the run_gate label: --config and NOTHING else.
PERMITTED = LAUNCH + ["--config", "$here/cited-standards.toml"]
SELF_TEST = LAUNCH + ["--self-test"]


def corpus_findings(root):
    """(findings, errors) for the config's corpus against the validated pack.toml member list."""
    try:
        globs, _exclusions = load_config(root / CONFIG)
        data = tomllib.loads((root / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError, MemoryError, CannotEvaluate) as exc:
        # tomllib refuses deep nesting with RecursionError, a many-part key under a memory limit with
        # MemoryError and an over-long integer with a bare ValueError: each is cannot-evaluate.
        return [], ["cannot read {}: {}".format(CONFIG, exc)]
    if "corpus" not in data:
        return ["{} has no corpus line, so the gate falls back to its built-in default and no "
                "prompt-pack member is scanned".format(CONFIG)], []
    # The owning validator, never a re-implementation: a pack it refuses is cannot-evaluate here,
    # because the member list below cannot be trusted then.
    status, guard, detail = validate_pack(root / PACK)
    if status != VALID:
        return [], ["{}/pack.toml: the owning validator (tools/check_opf_prompt_pack.py) refuses the "
                    "pack: {} [{}] {}".format(PACK, status, guard, detail)]
    try:
        manifest = tomllib.loads((root / PACK / "pack.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError, MemoryError) as exc:
        return [], ["cannot read {}/pack.toml: {}".format(PACK, exc)]
    members = ["{}/{}".format(PACK, row["path"]) for row in manifest["members"]]
    try:
        scanned = expand_corpus(root, globs)
    except CannotEvaluate as exc:
        return [], ["{} corpus cannot be expanded: {}".format(CONFIG, exc)]
    missed = sorted(set(members) - set(scanned))
    if missed:
        return ["{} corpus misses {} member(s) {}/pack.toml lists: {}".format(
            CONFIG, len(missed), PACK, ", ".join(missed))], []
    return [], []


def gate_lines(text):
    """(sorted word lists after the label of every run_gate line naming the gate, errors)."""
    found, errors = [], []
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if not stripped.startswith("run_gate "):
            continue
        try:
            words = shlex.split(stripped, comments=True)
        except ValueError as exc:
            errors.append("{}:{}: cannot split the run_gate line into words: {}".format(RUNNER, number, exc))
            continue
        if any(word.rsplit("/", 1)[-1] == GATE_NAME for word in words):
            found.append(words[2:])
    return sorted(found), errors


def wiring_findings(root):
    """(findings, errors) for the cited-standards gate lines of the OPF runner."""
    try:
        text = (root / RUNNER).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [], ["cannot read {}: {}".format(RUNNER, exc)]
    lines, errors = gate_lines(text)
    if errors:
        return [], errors
    live = [words for words in lines if words != SELF_TEST]
    if not live:
        return ["{} runs no cited-standards gate line besides its self-test".format(RUNNER)], []
    return ["{}: cited-standards gate line is not the one permitted invocation {!r} (--config and "
            "nothing else): {}".format(RUNNER, " ".join(PERMITTED), " ".join(words))
            for words in live if words != PERMITTED], []


def evaluate_root(root):
    """(findings, errors) for the whole contract at root."""
    findings, errors = corpus_findings(root)
    more_findings, more_errors = wiring_findings(root)
    return findings + more_findings, errors + more_errors


# ---- self-test -------------------------------------------------------------------------------------

_FIXTURE_CONFIG = 'format-version = 1\ncorpus = ["prompt-pack/**/*.md"]\n'
_FIXTURE_MEMBERS = (("adopt/a.md", "# adopt\n\nPlain prose.\n"),
                    ("flow/SKILL.md", "# skill\n\nPlain prose.\n"))
_SELF_LINE = 'run_gate "cited-standards-selftest" python3 -I -B "$here/check_cited_standards.py" --self-test'
_LIVE = 'run_gate "cited-standards" python3 -I -B "$here/check_cited_standards.py" '


def _fixture_pack_manifest(fmt=PACK_FORMAT):
    """A pack.toml for the fixture members, valid under the owning validator unless fmt departs."""
    rows = [dict(path=path, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
            for path, text in _FIXTURE_MEMBERS]
    lines = ['format = "{}"'.format(fmt), 'version = "1.0.0"',
             'digest = "{}"'.format(compute_digest("1.0.0", rows))]
    for row in rows:
        lines += ["", "[[members]]", 'path = "{}"'.format(row["path"]),
                  'sha256 = "{}"'.format(row["sha256"])]
    return "\n".join(lines) + "\n"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _runner(*gate_lines_text):
    if not gate_lines_text:
        gate_lines_text = (_SELF_LINE, _LIVE + '--config "$here/cited-standards.toml"')
    body = ["#!/usr/bin/env bash", "set -uo pipefail", "run_gate() {", '  "$@"', "}",
            "# run_gate \"commented\" python3 -I -B \"$here/check_cited_standards.py\" --root x"]
    return "\n".join(body + list(gate_lines_text)) + "\n"


def _build_tree(root):
    _write(root / CONFIG, _FIXTURE_CONFIG)
    for path, text in _FIXTURE_MEMBERS:
        _write(root / PACK / path, text)
    _write(root / PACK / "pack.toml", _fixture_pack_manifest())
    _write(root / RUNNER, _runner())


def _config_with(corpus):
    return "format-version = 1\ncorpus = [{}]\n".format(", ".join('"{}"'.format(g) for g in corpus))


def _runner_with(*lines):
    return lambda root: _write(root / RUNNER, _runner(*lines))


_PERMITTED_LINE = _LIVE + '--config "$here/cited-standards.toml"'


def _vectors():
    """(name, mutate(root), want exit code of main, needle in main's stderr, wanted needle hits)."""
    return (
        ("valid corpus and wiring", lambda root: None, 0, None, None),
        ("config missing", lambda root: os.remove(root / CONFIG), 2, "cannot read " + CONFIG, None),
        ("config malformed", lambda root: _write(root / CONFIG, "corpus = [\n"), 2, "cannot read " + CONFIG,
         None),
        ("config without a corpus line", lambda root: _write(root / CONFIG, "format-version = 1\n"),
         1, "has no corpus line", None),
        ("corpus misses a pack member",
         lambda root: _write(root / CONFIG, _config_with(("prompt-pack/adopt/*.md",))),
         1, "misses 1 member(s)", None),
        ("corpus glob cannot be expanded",
         lambda root: _write(root / CONFIG, _config_with(("prompt-pack/**/*.md", "absent/*.md"))),
         2, "cannot be expanded", None),
        ("pack manifest refused: members-only manifest",
         lambda root: _write(root / PACK / "pack.toml", '[[members]]\npath = "flow/SKILL.md"\n'),
         2, "owning validator", None),
        ("pack manifest refused: unsupported format",
         lambda root: _write(root / PACK / "pack.toml", _fixture_pack_manifest(fmt="opf.prompt-pack/v999")),
         2, "owning validator", None),
        ("runner missing", lambda root: os.remove(root / RUNNER), 2, "cannot read " + RUNNER, None),
        ("runner line that cannot be split", _runner_with(_PERMITTED_LINE, _LIVE + '--config "unclosed'),
         2, "cannot split the run_gate line", None),
        ("no live gate line besides the self-test", _runner_with(_SELF_LINE),
         1, "runs no cited-standards gate line", 1),
        ("gate line drops --config", _runner_with(_SELF_LINE, _LIVE.rstrip()), 1, "permitted invocation", 1),
        ("gate line respells the config path",
         _runner_with(_SELF_LINE, _LIVE + '--config "$here/../tools/cited-standards.toml"'),
         1, "permitted invocation", 1),
        ("--root appended to the permitted line",
         _runner_with(_SELF_LINE, _PERMITTED_LINE + " --root decoy"), 1, "permitted invocation", 1),
        ("gate reached through another directory",
         _runner_with(_SELF_LINE, 'run_gate "cited-standards" python3 -I -B "$here/x/check_cited_standards.py" '
                      '--config "$here/cited-standards.toml"'), 1, "permitted invocation", 1),
        ("self-test line with extra arguments is live, not excluded",
         _runner_with(_SELF_LINE, _PERMITTED_LINE, _LIVE + "--self-test --root decoy"),
         1, "permitted invocation", 1),
        ("permitted line plus a forbidden line sorting after it",
         _runner_with(_SELF_LINE, _PERMITTED_LINE, _PERMITTED_LINE + " --root decoy"),
         1, "permitted invocation", 1),
        ("permitted line plus a forbidden line sorting before it",
         _runner_with(_SELF_LINE, _PERMITTED_LINE, _LIVE + '--config "$here/a.toml"'),
         1, "permitted invocation", 1),
    )


def self_test():
    """Entry-point fixture vectors for each finding and cannot-evaluate path; harness errors are 2."""
    _child_status_refusal_exit("check_cited_standards_contract.py")
    try:
        return _self_test_vectors()
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false 0
        print("OPF cited-standards contract self-test: cannot evaluate: unexpected error "
              "({!r})".format(exc), file=sys.stderr)
        return 2


def _run_main(root, argv=()):
    """Run the real entry point, main, capturing its exit code, its stdout and its stderr."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv), root=root)
    return code, out.getvalue(), err.getvalue()


def _case_problems(code, stdout_text, stderr_text, want_code, needle, want_hits):
    """The assertions every vector shares: main's exit code, its stderr lines, its PASS line."""
    lines = stderr_text.splitlines()
    hits = len([line for line in lines if needle in line]) if needle else 0
    problems = []
    if code != want_code:
        problems.append("main returned {}, expected {}".format(code, want_code))
    if needle is not None and hits == 0:
        problems.append("no stderr line carries {!r}".format(needle))
    if want_hits is not None and hits != want_hits:
        problems.append("{!r} carried by {} stderr line(s), expected {}".format(needle, hits, want_hits))
    passed = "PASS: OPF cited-standards contract:" in stdout_text
    if want_code == 0 and not passed:
        problems.append("stdout lacks the contract PASS line")
    if want_code != 0 and passed:
        problems.append("stdout carries the contract PASS line despite exit {}".format(want_code))
    return problems, lines


class _ExplodingRoot:
    """A root whose first use raises, so main's fail-closed backstop must catch and return 2."""

    def __truediv__(self, other):
        raise RuntimeError("contract self-test: synthetic unexpected error")


def _accept_any_pack(_pack_dir):
    """The re-read vector's stub owning validator: accept whatever pack.toml is on disk."""
    return (VALID, "members", "stubbed by the contract self-test")


_STUB_CITED = ("class CannotEvaluate(Exception):\n    pass\n\n\ndef expand_corpus(root, globs):\n"
               "    return []\n\n\ndef load_config(path):\n    return (), ()\n")
# validate_pack is deliberately renamed away, so importing it from this stub must fail.
_STUB_PACK = ("PACK_FORMAT = 'opf.prompt-pack/v1'\nVALID = 'VALID'\n\n\ndef compute_digest(version, rows):\n"
              "    return ''\n\n\ndef validate_pack_renamed(pack_dir):\n"
              "    raise AssertionError('renamed away by the import-failure vector')\n")


def _import_failure_process(root):
    """A fresh process over a scratch copy of this script beside stub collaborators whose owning
    validator renames validate_pack away: the import guard must exit 2, never a traceback exit 1."""
    script = root / "tools" / "check_cited_standards_contract.py"
    _write(script, Path(__file__).resolve().read_text(encoding="utf-8"))
    _write(root / "tools" / GATE_NAME, _STUB_CITED)
    _write(root / "tools" / "check_opf_prompt_pack.py", _STUB_PACK)
    return subprocess.run([sys.executable, "-I", "-B", str(script)],
                          capture_output=True, text=True, timeout=120)


def _self_test_vectors():
    global validate_pack, _self_test_vectors
    failures = []
    count = 0

    def record(name, problems, lines):
        nonlocal count
        count += 1
        print("{} {}".format("PASS" if not problems else "FAIL", name))
        if problems:
            failures.append("{}: {}; lines: {!r}".format(name, "; ".join(problems), lines))

    with tempfile.TemporaryDirectory(prefix="opf-cited-standards-contract-selftest-") as tmp:
        for index, (name, mutate, want_code, needle, want_hits) in enumerate(_vectors()):
            root = Path(tmp) / "case{}".format(index)
            _build_tree(root)
            mutate(root)
            code, out_text, err_text = _run_main(root)
            record(name, *_case_problems(code, out_text, err_text, want_code, needle, want_hits))

        for name, argv in (("usage error: an unknown argument", ("--root", "decoy")),
                           ("usage error: --self-test with an extra argument", ("--self-test", "decoy"))):
            code, out_text, err_text = _run_main(Path(tmp), argv=argv)
            record(name, *_case_problems(code, out_text, err_text, 2, "usage:", None))

        code, out_text, err_text = _run_main(_ExplodingRoot())
        record("unexpected exception inside the check is caught and returned as 2",
               *_case_problems(code, out_text, err_text, 2, "unexpected error", None))

        real_vectors = _self_test_vectors

        def _exploding_vectors():
            raise RuntimeError("contract self-test: synthetic harness failure")

        _self_test_vectors = _exploding_vectors
        try:
            code, out_text, err_text = _run_main(Path(tmp), argv=("--self-test",))
        finally:
            _self_test_vectors = real_vectors
        problems, lines = _case_problems(code, out_text, err_text, 2,
                                         "self-test: cannot evaluate: unexpected error", None)
        if "SELF-TEST PASS" in out_text:
            problems.append("stdout carries a SELF-TEST PASS line despite the harness failure")
        record("harness failure inside the self-test is caught and returned as 2", problems, lines)

        root = Path(tmp) / "case-pack-reread"
        _build_tree(root)
        _write(root / PACK / "pack.toml", "members = [\n")
        real_validate_pack = validate_pack
        validate_pack = _accept_any_pack
        try:
            code, out_text, err_text = _run_main(root)
        finally:
            validate_pack = real_validate_pack
        record("pack.toml re-read fails after the owning validator accepted it",
               *_case_problems(code, out_text, err_text, 2, "cannot read {}/pack.toml".format(PACK), None))

        completed = _import_failure_process(Path(tmp) / "case-import-failure")
        record("import-time failure of a collaborator is cannot-evaluate (exit 2)",
               *_case_problems(completed.returncode, completed.stdout, completed.stderr,
                               2, "cannot import a collaborator module", None))

    if failures:
        print("SELF-TEST FAIL ({} of {} contract vector(s)):".format(len(failures), count))
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} OPF cited-standards contract vector(s) passed".format(count))
    return 0


def main(argv=None, root=ROOT):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return self_test()
    if args:
        print("usage: check_cited_standards_contract.py [--self-test]", file=sys.stderr)
        return 2
    try:
        findings, errors = evaluate_root(root)
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false 0
        print("error: OPF cited-standards contract: unexpected error ({!r}); cannot "
              "evaluate".format(exc), file=sys.stderr)
        return 2
    for line in errors:
        print("error: OPF cited-standards contract: {}".format(line), file=sys.stderr)
    for line in findings:
        print("FAIL: OPF cited-standards contract: {}".format(line), file=sys.stderr)
    if errors:
        return 2
    if findings:
        return 1
    print("PASS: OPF cited-standards contract: {} covers every member {}/pack.toml (validated by "
          "tools/check_opf_prompt_pack.py) lists, and every cited-standards gate line in {} is exactly "
          "{!r}".format(CONFIG, PACK, RUNNER, " ".join(PERMITTED)))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The self-test reads the exit status of a
# child it starts (the import-failure vector), so under an ignored SIGCHLD a failing child would read as a
# pass. The guard runs as the first statement of self_test() and at the entry line before main(); the
# entry line comes after the --self-test statement that opf.py requires first in the __main__ block. An
# import of this module runs nothing.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(_child_status_refusal_exit("check_cited_standards_contract.py") or main())
