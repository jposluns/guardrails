#!/usr/bin/env python3
"""Pin this repository's cited-standards corpus and whole gate invocation, outside the portable gate.

tools/check_cited_standards.py falls back to its built-in default corpus (the rule files alone) when a config
has no corpus line, scans whatever tree --root names, and its self-test vectors carry their own corpus, so
none of that notices when this repository stops scanning the OPF prompt pack or redirects the scan. This
assertion reads .aiqt/cited-standards.toml and fails (exit 1) unless its corpus is present, names the
rule-file glob, and expands, through the gate's own expand_corpus, to every member opf/prompt-pack/pack.toml
lists; and unless every cited-standards gate line other than the exact self-test line, in both
tools/run_all_checks.sh and .github/workflows/quality.yml (read with check_ci_parity's extractors, whose
grammar already pins the launcher words), is exactly the one permitted invocation,
tools/check_cited_standards.py --config .aiqt/cited-standards.toml, with nothing added, so an appended
--root or any other extra argument is a finding even when both runners carry the same drifted line.
pack.toml is validated by its owning validator (opf/tools/check_opf_prompt_pack.py, imported, never
re-implemented); a pack that validator refuses, a missing or malformed config or runner, a corpus glob
the gate cannot expand, a pack.toml re-read that fails after validation, an argument list other than
none or --self-test alone (a usage error), an unexpected exception while the check runs, or an import
failure of a collaborator module (guarded at module level, so a renamed collaborator symbol exits 2,
never a traceback exit 1), is cannot-evaluate (exit 2). The no-corpus-line finding is a clearer-message
duplicate: with that check removed, the member reconciliation below still fails on the built-in default
corpus; it is kept because its message names the fallback.

--self-test (registered in both runners) drives the real entry point, main, over fixture vectors and
asserts main's exit code and its diagnostics for every finding and every cannot-evaluate path above:
among them the --root redirect regression, the mixed cases (the permitted line plus a forbidden one
that sorts after it, and one that sorts before it, in each runner alone and in both, so a loop over
only the first or only the last sorted gate line is red), a self-test line with extra arguments (live,
never excluded), the workflow-side extraction diagnostic, the post-validation pack.toml re-read failure
(the owning validator stubbed to accept: in production both reads see the same bytes, so only a race
reaches that handler), the usage error, the unexpected-exception backstop, the self-test harness
backstop (a harness failure surfaces as exit 2 through main --self-test, never a false 0) and, in a
fresh process checked with the shared exit/stdout/stderr assertions over a scratch copy whose stub
collaborators rename validate_pack away, the import-time guard; every vector but that fresh-process one
drives main in process; an edit that disables one of these checks, or routes a failing branch of main
to exit 0, is red.
This guards declaration and wiring drift, not environment overrides, a member the gate scans and reads
short, or the content of recorded exclusions. Removing this contract (or its self-test, or the newtab
contract) from both runners together is a missing-mandatory finding in tools/check_ci_parity.py.
"""
import contextlib
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(1, str(ROOT / "opf" / "tools"))
try:
    from check_cited_standards import CannotEvaluate, expand_corpus, load_config  # noqa: E402
    from check_ci_parity import (  # noqa: E402
        EXPECTED_RUN_GATE_BODY, extract_ci, extract_local, read_runner_text)
    from check_opf_prompt_pack import PACK_FORMAT, VALID, compute_digest, validate_pack  # noqa: E402
except Exception as exc:  # noqa: BLE001  an unimportable collaborator is cannot-evaluate, never a finding
    print("error: cited-standards repository contract: cannot evaluate: cannot import a collaborator "
          "module ({!r})".format(exc), file=sys.stderr)
    sys.exit(2)

CONFIG = ".aiqt/cited-standards.toml"
PACK = "opf/prompt-pack"
RULE_GLOB = ".aiqt/core/rules/*.md"
GATE = "tools/check_cited_standards.py"
# The one permitted live invocation, as a check_ci_parity member identity (the extractors already pin
# and remove the launcher words and interpreter flags): --config CONFIG and NOTHING else.
PERMITTED = GATE + " --config " + CONFIG
SELF_TEST_LINE = GATE + " --self-test"
RUNNERS = (("tools/run_all_checks.sh", extract_local), (".github/workflows/quality.yml", extract_ci))


def corpus_findings(root):
    """(findings, errors) for the config's corpus against the validated pack.toml member list."""
    try:
        globs, _exclusions = load_config(root / CONFIG)
        data = tomllib.loads((root / CONFIG).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError, MemoryError, CannotEvaluate) as exc:
        # tomllib refuses deep nesting and (CPython 3.14.8 on) a 1001-part key with RecursionError, a
        # many-part key under a memory limit with MemoryError and an over-long integer with a bare
        # ValueError: each is cannot-evaluate (exit 2), never a traceback.
        return [], ["cannot read {}: {}".format(CONFIG, exc)]
    if "corpus" not in data:
        # A clearer-message duplicate: with this check removed, the member reconciliation below still
        # fails, because the built-in default corpus misses every pack member. Kept for the message.
        return ["{} has no corpus line, so the gate falls back to its built-in default (the rule files "
                "alone) and no prompt-pack member is scanned".format(CONFIG)], []
    findings = []
    if RULE_GLOB not in globs:
        findings.append("{} corpus does not name the rule-file glob {!r}: {!r}".format(CONFIG, RULE_GLOB, list(globs)))
    # The owning validator, never a re-implementation: a pack it refuses (a missing, unknown or
    # malformed key, an unsupported format, digest or member drift, an unreadable or unparseable
    # manifest) is cannot-evaluate here, because the member list below cannot be trusted then.
    status, guard, detail = validate_pack(root / PACK)
    if status != VALID:
        return findings, ["{}/pack.toml: the owning validator (opf/tools/check_opf_prompt_pack.py) "
                          "refuses the pack: {} [{}] {}".format(PACK, status, guard, detail)]
    try:
        manifest = tomllib.loads((root / PACK / "pack.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError, MemoryError) as exc:
        # The same tomllib refusals as the config read above, each cannot-evaluate.
        return findings, ["cannot read {}/pack.toml: {}".format(PACK, exc)]
    members = ["{}/{}".format(PACK, row["path"]) for row in manifest["members"]]
    try:
        scanned = expand_corpus(root, globs)
    except CannotEvaluate as exc:
        return findings, ["{} corpus cannot be expanded: {}".format(CONFIG, exc)]
    missed = sorted(set(members) - set(scanned))
    if missed:
        findings.append("{} corpus misses {} member(s) {}/pack.toml lists: {}".format(
            CONFIG, len(missed), PACK, ", ".join(missed)))
    return findings, []


def wiring_findings(root):
    """(findings, errors) for the cited-standards gate lines of both runners."""
    findings, errors = [], []
    for source, extract in RUNNERS:
        text, diagnostic = read_runner_text(root / source, source)
        if diagnostic is not None:
            errors.append("cannot read {}: {}".format(source, diagnostic.message))
            continue
        extraction = extract(text)
        if extraction.diagnostics:
            errors.append("cannot extract the gates of {}: {}".format(
                source, "; ".join(d.message for d in extraction.diagnostics)))
            continue
        lines = sorted(member for member in extraction.members
                       if member.split(" ")[0] == GATE and member != SELF_TEST_LINE)
        if not lines:
            findings.append("{} runs no cited-standards gate line besides its self-test".format(source))
        for member in lines:
            if member != PERMITTED:
                findings.append("{}: cited-standards gate line is not the one permitted invocation "
                                "{!r} (--config {} and nothing else): {}".format(
                                    source, PERMITTED, CONFIG, member))
    return findings, errors


def evaluate_root(root):
    """(findings, errors) for the whole contract at root."""
    findings, errors = corpus_findings(root)
    more_findings, more_errors = wiring_findings(root)
    return findings + more_findings, errors + more_errors


# ---- self-test -------------------------------------------------------------------------------------

_FIXTURE_CONFIG = 'format-version = 1\ncorpus = [".aiqt/core/rules/*.md", "opf/prompt-pack/**/*.md"]\n'
_FIXTURE_MEMBERS = (("adopt/a.md", "# adopt\n\nPlain prose.\n"),
                    ("flow/SKILL.md", "# skill\n\nPlain prose.\n"))


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


def _local_runner(gate_args="--config " + CONFIG, gate_lines=None):
    # The exact dispatcher body check_ci_parity validates fixture runners against.
    body = ["#!/usr/bin/env bash", "set -uo pipefail", "failed=0", 'failed_names=""',
            "run_gate() {"] + ["  " + line for line in EXPECTED_RUN_GATE_BODY] + ["}"]
    if gate_lines is None:
        gate_lines = ['run_gate "cited-standards-selftest" python3 -I -B {} --self-test'.format(GATE),
                      'run_gate "cited-standards" python3 -I -B {} {}'.format(GATE, gate_args).rstrip()]
    return "\n".join(body + list(gate_lines)) + "\n"


def _ci_runner(gate_args="--config " + CONFIG, gate_lines=None):
    body = ["name: Test", "jobs:", "  quality:", "    runs-on: ubuntu-latest", "    steps:",
            "      - uses: actions/checkout@v4"]
    if gate_lines is None:
        gate_lines = ["      - name: Cited standards self-test",
                      "        run: python3 -I -B {} --self-test".format(GATE),
                      "      - name: Cited standards",
                      "        run: python3 -I -B {} {}".format(GATE, gate_args).rstrip()]
    return "\n".join(body + list(gate_lines)) + "\n"


def _build_tree(root):
    _write(root / CONFIG, _FIXTURE_CONFIG)
    _write(root / ".aiqt/core/rules/r1.md", "# rule\n\nPlain prose.\n")
    for path, text in _FIXTURE_MEMBERS:
        _write(root / PACK / path, text)
    _write(root / PACK / "pack.toml", _fixture_pack_manifest())
    _write(root / "tools/run_all_checks.sh", _local_runner())
    _write(root / ".github/workflows/quality.yml", _ci_runner())


def _config_with(corpus):
    return "format-version = 1\ncorpus = [{}]\n".format(
        ", ".join('"{}"'.format(glob) for glob in corpus))


def _both_runners(gate_args):
    def build(root):
        _write(root / "tools/run_all_checks.sh", _local_runner(gate_args))
        _write(root / ".github/workflows/quality.yml", _ci_runner(gate_args))
    return build


def _no_live_lines(root):
    _write(root / "tools/run_all_checks.sh", _local_runner(gate_lines=(
        'run_gate "cited-standards-selftest" python3 -I -B {} --self-test'.format(GATE),)))
    _write(root / ".github/workflows/quality.yml", _ci_runner(gate_lines=(
        "      - name: Cited standards self-test",
        "        run: python3 -I -B {} --self-test".format(GATE),)))


def _decoy_redirect(root):
    """codex PR #520 round 2 MAJOR regression: a valid intended pack, a clean decoy tree, and
    --root decoy appended to the gate line in BOTH runners, so token parity between the runners
    holds while the scan is redirected; the whole-invocation pin must still find both lines."""
    _write(root / "decoy/.aiqt/core/rules/r1.md", "# rule\n\nPlain prose.\n")
    _write(root / "decoy/opf/prompt-pack/adopt/a.md", "# adopt\n\nPlain prose.\n")
    _both_runners("--config {} --root decoy".format(CONFIG))(root)


def _mixed_runners(local_mixed, ci_mixed, forbidden=None):
    """codex PR #520 round 3 MEDIUM: the permitted line PLUS a forbidden line, in one runner alone
    or in both; a loop that checks only the first extracted member accepts these. The default
    forbidden --root line sorts after the permitted line; claude PR #520 round 4 MEDIUM passes a
    config respelling that sorts before it, so a loop over only the last line is red as well."""
    if forbidden is None:
        forbidden = "--config {} --root decoy".format(CONFIG)

    def build(root):
        local_lines = ['run_gate "cited-standards-selftest" python3 -I -B {} --self-test'.format(GATE),
                       'run_gate "cited-standards" python3 -I -B {} --config {}'.format(GATE, CONFIG)]
        if local_mixed:
            local_lines.append(
                'run_gate "cited-standards-decoy" python3 -I -B {} {}'.format(GATE, forbidden))
        ci_lines = ["      - name: Cited standards self-test",
                    "        run: python3 -I -B {} --self-test".format(GATE),
                    "      - name: Cited standards",
                    "        run: python3 -I -B {} --config {}".format(GATE, CONFIG)]
        if ci_mixed:
            ci_lines += ["      - name: Cited standards decoy",
                         "        run: python3 -I -B {} {}".format(GATE, forbidden)]
        _write(root / "tools/run_all_checks.sh", _local_runner(gate_lines=tuple(local_lines)))
        _write(root / ".github/workflows/quality.yml", _ci_runner(gate_lines=tuple(ci_lines)))
    return build


def _selftest_line_with_extras(root):
    """claude PR #520 round 3 MINOR: only the EXACT self-test line is excluded; a line that merely
    carries --self-test plus more arguments is live, compared, and found not permitted, so widening
    the exclusion to any line carrying --self-test goes red."""
    extras = "--self-test --root decoy"
    _write(root / "tools/run_all_checks.sh", _local_runner(gate_lines=(
        'run_gate "cited-standards-selftest" python3 -I -B {} --self-test'.format(GATE),
        'run_gate "cited-standards" python3 -I -B {} --config {}'.format(GATE, CONFIG),
        'run_gate "cited-standards-decoy" python3 -I -B {} {}'.format(GATE, extras))))
    _write(root / ".github/workflows/quality.yml", _ci_runner(gate_lines=(
        "      - name: Cited standards self-test",
        "        run: python3 -I -B {} --self-test".format(GATE),
        "      - name: Cited standards",
        "        run: python3 -I -B {} --config {}".format(GATE, CONFIG),
        "      - name: Cited standards decoy",
        "        run: python3 -I -B {} {}".format(GATE, extras))))


def _workflow_extraction_diagnostic(root):
    """claude PR #520 round 3 MINOR: an extraction diagnostic on the WORKFLOW side alone (the
    local-runner vector cannot discriminate a workflow-only disable): an unsupported step key."""
    _write(root / ".github/workflows/quality.yml", _ci_runner(gate_lines=(
        "      - name: Cited standards",
        "        run: python3 -I -B {} --config {}".format(GATE, CONFIG),
        "        working-directory: decoy")))


def _vectors():
    """(name, mutate(root), want exit code of main, needle in main's stderr, wanted needle hits)."""
    return (
        ("valid corpus and wiring", lambda root: None, 0, None, None),
        ("config missing", lambda root: os.remove(root / CONFIG), 2,
         "cannot read " + CONFIG, None),
        ("config malformed", lambda root: _write(root / CONFIG, "corpus = [\n"), 2,
         "cannot read " + CONFIG, None),
        ("config without a corpus line", lambda root: _write(root / CONFIG, "format-version = 1\n"),
         1, "has no corpus line", None),
        ("corpus drops the rule-file glob",
         lambda root: _write(root / CONFIG, _config_with(("opf/prompt-pack/**/*.md",))),
         1, "does not name the rule-file glob", None),
        ("corpus misses a pack member",
         lambda root: _write(root / CONFIG, _config_with((RULE_GLOB, "opf/prompt-pack/adopt/*.md"))),
         1, "misses 1 member(s)", None),
        ("corpus glob cannot be expanded",
         lambda root: _write(root / CONFIG,
                             _config_with((RULE_GLOB, "opf/prompt-pack/**/*.md", "absent/*.md"))),
         2, "cannot be expanded", None),
        # codex PR #520 round 2 MEDIUM: a manifest the owning validator refuses is cannot-evaluate.
        ("pack manifest refused: members-only manifest",
         lambda root: _write(root / PACK / "pack.toml", '[[members]]\npath = "flow/SKILL.md"\n'),
         2, "owning validator", None),
        ("pack manifest refused: unsupported format",
         lambda root: _write(root / PACK / "pack.toml",
                             _fixture_pack_manifest(fmt="opf.prompt-pack/v999")),
         2, "owning validator", None),
        ("local runner missing", lambda root: os.remove(root / "tools/run_all_checks.sh"), 2,
         "cannot read tools/run_all_checks.sh", None),
        ("workflow missing", lambda root: os.remove(root / ".github/workflows/quality.yml"), 2,
         "cannot read .github/workflows/quality.yml", None),
        ("runner extraction diagnostic",
         lambda root: _write(root / "tools/run_all_checks.sh", _local_runner(gate_lines=(
             'run_gate "cited-standards" python3 -I -B {} --config {}'.format(GATE, CONFIG),
             'run_gate "BadLabel" python3 -I -B tools/x.py',))),
         2, "cannot extract the gates of tools/run_all_checks.sh", None),
        ("no live gate line besides the self-test", _no_live_lines,
         1, "runs no cited-standards gate line", 2),
        ("gate line drops --config", _both_runners(""), 1, "permitted invocation", 2),
        ("gate line respells the config path",
         _both_runners("--config ./" + CONFIG), 1, "permitted invocation", 2),
        ("--root decoy appended in both runners despite token parity", _decoy_redirect,
         1, "permitted invocation", 2),
        ("--root opf appended in both runners", _both_runners("--config {} --root opf".format(CONFIG)),
         1, "permitted invocation", 2),
        ("workflow-side extraction diagnostic", _workflow_extraction_diagnostic,
         2, "cannot extract the gates of .github/workflows/quality.yml", None),
        ("self-test line with extra arguments is live, not excluded", _selftest_line_with_extras,
         1, "permitted invocation", 2),
        # codex PR #520 round 3 MEDIUM: the permitted line plus a forbidden one, per runner and both.
        ("permitted line plus a forbidden --root line in the local runner alone",
         _mixed_runners(True, False), 1, "permitted invocation", 1),
        ("permitted line plus a forbidden --root line in the workflow alone",
         _mixed_runners(False, True), 1, "permitted invocation", 1),
        ("permitted line plus a forbidden --root line in both runners",
         _mixed_runners(True, True), 1, "permitted invocation", 2),
        # claude PR #520 round 4 MEDIUM: the forbidden --root lines above all sort AFTER the
        # permitted line, so they miss a loop over only the LAST sorted gate line; this forbidden
        # config respelling (./ before .aiqt) sorts BEFORE it and catches that loop.
        ("permitted line plus a forbidden line sorting before it in the local runner alone",
         _mixed_runners(True, False, forbidden="--config ./" + CONFIG), 1, "permitted invocation", 1),
        ("permitted line plus a forbidden line sorting before it in the workflow alone",
         _mixed_runners(False, True, forbidden="--config ./" + CONFIG), 1, "permitted invocation", 1),
        ("permitted line plus a forbidden line sorting before it in both runners",
         _mixed_runners(True, True, forbidden="--config ./" + CONFIG), 1, "permitted invocation", 2),
    )


def self_test():
    """Entry-point fixture vectors for each finding and cannot-evaluate path; harness errors are 2."""
    try:
        return _self_test_vectors()
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false 0
        print("cited-standards repository contract self-test: cannot evaluate: unexpected error "
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
    if want_code == 0 and "PASS: cited-standards repository contract:" not in stdout_text:
        problems.append("stdout lacks the contract PASS line")
    if want_code != 0 and "PASS: cited-standards repository contract:" in stdout_text:
        problems.append("stdout carries the contract PASS line despite exit {}".format(want_code))
    return problems, lines


class _ExplodingRoot:
    """A root whose first use raises, so main's fail-closed backstop must catch and return 2."""

    def __truediv__(self, other):
        raise RuntimeError("contract self-test: synthetic unexpected error")


def _accept_any_pack(_pack_dir):
    """The re-read vector's stub owning validator: accept whatever pack.toml is on disk."""
    return (VALID, "members", "stubbed by the contract self-test")


_STUB_CITED = (
    "class CannotEvaluate(Exception):\n"
    "    pass\n"
    "\n"
    "\n"
    "def expand_corpus(root, globs):\n"
    "    return []\n"
    "\n"
    "\n"
    "def load_config(path):\n"
    "    return (), ()\n")

_STUB_PARITY = (
    "EXPECTED_RUN_GATE_BODY = ()\n"
    "\n"
    "\n"
    "def extract_ci(text):\n"
    "    raise AssertionError('unused in the import-failure vector')\n"
    "\n"
    "\n"
    "def extract_local(text):\n"
    "    raise AssertionError('unused in the import-failure vector')\n"
    "\n"
    "\n"
    "def read_runner_text(path, source, reader=None):\n"
    "    raise AssertionError('unused in the import-failure vector')\n")

# validate_pack is deliberately renamed away, so importing it from this stub must fail.
_STUB_PACK = (
    "PACK_FORMAT = 'opf.prompt-pack/v1'\n"
    "VALID = 'VALID'\n"
    "\n"
    "\n"
    "def compute_digest(version, rows):\n"
    "    return ''\n"
    "\n"
    "\n"
    "def validate_pack_renamed(pack_dir):\n"
    "    raise AssertionError('renamed away by the import-failure vector')\n")


def _import_failure_process(root):
    """claude PR #520 round 3 MINOR: a fresh process over a scratch copy of this script beside stub
    collaborator modules whose owning validator renames validate_pack away; the copy's module-level
    import fails, and the import guard must exit 2 with the cannot-evaluate message, never a
    traceback exit 1."""
    script = root / ".github" / "check_cited_standards_contract.py"
    _write(script, Path(__file__).resolve().read_text(encoding="utf-8"))
    _write(root / "tools" / "check_cited_standards.py", _STUB_CITED)
    _write(root / "tools" / "check_ci_parity.py", _STUB_PARITY)
    _write(root / "opf" / "tools" / "check_opf_prompt_pack.py", _STUB_PACK)
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

    with tempfile.TemporaryDirectory(prefix="cited-standards-contract-selftest-") as tmp:
        for index, (name, mutate, want_code, needle, want_hits) in enumerate(_vectors()):
            root = Path(tmp) / "case{}".format(index)
            _build_tree(root)
            mutate(root)
            code, out_text, err_text = _run_main(root)
            problems, lines = _case_problems(code, out_text, err_text, want_code, needle, want_hits)
            record(name, problems, lines)

        # main's usage-error path: any argument list other than none or --self-test alone is 2.
        for name, argv in (
                ("usage error: an unknown argument", ("--root", "decoy")),
                ("usage error: --self-test with an extra argument", ("--self-test", "decoy"))):
            code, out_text, err_text = _run_main(Path(tmp), argv=argv)
            problems, lines = _case_problems(code, out_text, err_text, 2, "usage:", None)
            record(name, problems, lines)

        # main's unexpected-exception backstop: the first use of the root raises.
        code, out_text, err_text = _run_main(_ExplodingRoot())
        problems, lines = _case_problems(code, out_text, err_text, 2, "unexpected error", None)
        record("unexpected exception inside the check is caught and returned as 2", problems, lines)

        # codex PR #520 round 4 MINOR: the self-test harness backstop: a harness failure inside
        # _self_test_vectors must surface as exit 2 through main --self-test, never a false 0 or
        # a SELF-TEST PASS line (red when the backstop returns 0).
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

        # The post-validation pack.toml re-read failure: in production the owning validator and the
        # re-read see the same bytes, so only a race reaches that handler; the vector stubs the
        # module-level validate_pack to accept while pack.toml is unparseable, restoring it always.
        root = Path(tmp) / "case-pack-reread"
        _build_tree(root)
        _write(root / PACK / "pack.toml", "members = [\n")
        real_validate_pack = validate_pack
        validate_pack = _accept_any_pack
        try:
            code, out_text, err_text = _run_main(root)
        finally:
            validate_pack = real_validate_pack
        problems, lines = _case_problems(
            code, out_text, err_text, 2, "cannot read {}/pack.toml".format(PACK), None)
        record("pack.toml re-read fails after the owning validator accepted it", problems, lines)

        # The import-time guard, in a fresh process over a scratch copy (a renamed collaborator
        # symbol is cannot-evaluate, never a traceback exit 1).
        completed = _import_failure_process(Path(tmp) / "case-import-failure")
        # codex PR #520 round 4 MINOR: the shared exit/stdout/stderr assertions apply here too,
        # so a contract PASS line printed before the import guard exits 2 is red.
        problems, lines = _case_problems(completed.returncode, completed.stdout, completed.stderr,
                                         2, "cannot import a collaborator module", None)
        record("import-time failure of a collaborator is cannot-evaluate (exit 2)", problems, lines)

    if failures:
        print("SELF-TEST FAIL ({} of {} contract vector(s)):".format(len(failures), count))
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} contract vector(s) ({} driven through main, plus the import-time "
          "subprocess vector), including the --root redirect regression, the mixed "
          "permitted-plus-forbidden lines in both sort orders and the usage, backstop, re-read, "
          "harness-failure and import-failure paths, passed".format(count, count - 1))
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
        print("error: cited-standards repository contract: unexpected error ({!r}); cannot "
              "evaluate".format(exc), file=sys.stderr)
        return 2
    for line in errors:
        print("error: cited-standards repository contract: {}".format(line), file=sys.stderr)
    for line in findings:
        print("FAIL: cited-standards repository contract: {}".format(line), file=sys.stderr)
    if errors:
        return 2
    if findings:
        return 1
    print("PASS: cited-standards repository contract: {} names {!r} and covers every member "
          "{}/pack.toml (validated by opf/tools/check_opf_prompt_pack.py) lists, and every "
          "cited-standards gate line in both runners is exactly {!r}".format(
              CONFIG, RULE_GLOB, PACK, PERMITTED))
    return 0


if __name__ == "__main__":
    sys.exit(main())
