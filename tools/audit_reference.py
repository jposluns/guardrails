#!/usr/bin/env python3
"""Trivial reference audit: proves the QA harness works end to end (QA foundation).

This ships NO substantive assurance logic. It is the ONE audit that exercises the whole harness so the
plumbing is proven before any real audit is built on it: it discovers a surface through the config seam
(tools/_qa_adapter.py), and emits a verdict in the shared result contract. It audits the gate ROSTER
surface (a required surface that IS present in this repo), so the harness's end-to-end path returns PASS.

Verdict mapping:
  gate roster resolves (>=1 gate)  -> PASS (evidence: the located gate paths)
  gate roster absent / unreadable  -> UNVERIFIABLE (missing evidence never reads as PASS)

Exit codes (normal mode): 0 PASS, 1 FAIL, 2 UNVERIFIABLE/config-error. `--digest` is the ADVISORY,
report-only mode the runner calls: it prints the compact surface digest plus this audit's line and exits 0
whatever the audit finds (advisory, never gating). In every mode a bad argument or a config error exits 2
before the mode runs, and so does the floor guard on a Python older than 3.14 that can start this file.
These exits hold while the tool's output (its diagnostic on stderr, and what it prints on stdout) can be
written and flushed. A failing output stream can change the exit status and can lose output; a separate
fix in progress addresses this. An older interpreter that cannot start this file fails with Python's own
error and status first. stdlib only. `--self-test` proves PASS on a present roster and UNVERIFIABLE on an
absent one (removing the missing-evidence guard fails it).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: audit_reference.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

from pathlib import Path

# Import the sibling _qa_adapter WITHOUT placing this script's own directory AHEAD of the stdlib on
# sys.path. Under `python3 -I` a sys.path insertion at index 0 would let a sibling json.py or hashlib.py
# (each `raise SystemExit(0)`) shadow a stdlib import and silently neuter this gate before its self-test
# runs. Appending keeps stdlib precedence (a stdlib import still resolves from the stdlib first) while
# still resolving our own sibling module from this directory. The check_python_launcher_isolation gate
# enforces this by flagging a reintroduced index-0 insertion in this file.
sys.path.append(str(Path(__file__).resolve().parent))
import _qa_adapter as qa  # noqa: E402
from _gen_common import precheck_special_files  # noqa: E402  SPECIAL-FILE-PRECHECK


AUDIT_ID = "reference"


def run_audit(cfg, root):
    """Discover the gate_roster surface and map its availability to a result. The reference audit's
    property is simply 'the gate roster resolved', so it inherits the adapter's missing-evidence guard:
    an absent roster is UNVERIFIABLE, never PASS."""
    surface = qa.discover("gate_roster", cfg, root)
    if surface["status"] == qa.PASS:
        return qa.make_result(AUDIT_ID, qa.PASS, "gate roster discovered: " + surface["detail"],
                              surface="gate_roster", required=surface["required"],
                              kind=surface["kind"], evidence=surface["evidence"])
    # Any non-PASS surface status (UNVERIFIABLE for an absent/unreadable roster, SKIP if it were
    # optional-disabled) carries through as a non-PASS audit result, so a missing roster can never
    # surface as a passing reference audit.
    status = qa.UNVERIFIABLE if surface["status"] == qa.SKIP else surface["status"]
    return qa.make_result(AUDIT_ID, status, "gate roster not available: " + surface["detail"],
                          surface="gate_roster", required=surface["required"],
                          kind=surface["kind"], evidence=surface["evidence"])


_EXIT = {qa.PASS: 0, qa.FAIL: 1, qa.UNVERIFIABLE: 2, qa.SKIP: 2}


def _resolve(explicit):
    return qa.resolve_config(explicit=explicit)


def main():
    # SPECIAL-FILE-PRECHECK runs FIRST, before the config resolution below reads any repository
    # file (.aiqt/assurance.toml above all), so neither the digest nor the normal audit path can block
    # on a plain read of a FIFO planted in the tree.
    precheck_special_files(Path(__file__).resolve().parents[1])
    argv = sys.argv[1:]
    # ARGUMENT VALIDATION precedes the --self-test dispatch: a malformed --config operand (empty, an
    # =-joined empty value, a next-flag that must not be swallowed as the path, or a duplicate) is a loud
    # error, never a silent self-test run that exits 0.
    try:
        explicit = qa.config_arg(argv)  # --config with no operand is a loud error, never a silent skip
    except ValueError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    # UNKNOWN-OPTION REJECTION also precedes the --self-test dispatch: an unrecognized option (a misspelled
    # --self-testx or --digestx, a stray flag) is a loud exit 2, never a silent fall-through to a default path
    # that would exit 0 without running the self-test or digest the caller asked for.
    unknown = qa.reject_unknown_options(argv, {"--self-test", "--digest"})
    if unknown is not None:
        print("error: unrecognized option {!r}".format(unknown), file=sys.stderr)
        return 2
    # The loud PINNED-source resolution runs BEFORE the --self-test dispatch, so a pinned-but-absent or
    # pinned-but-empty config (an explicit --config PATH that does not exist, or AIQT_ASSURANCE_CONFIG set
    # to "") is a loud exit 2 even under --self-test, rather than a silent self-test that exits 0 on a
    # config the caller pinned. resolve_config raises on such a pinned source; with none it walks to the
    # nearest/portable config (never an error).
    try:
        cfg, root, prov = _resolve(explicit)
    except qa.ConfigError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    if "--self-test" in argv:
        return _self_test()

    if "--digest" in argv:
        # ADVISORY report-only digest: the surface board plus this audit's verdict line, never gating.
        surfaces = qa.discover_all(cfg, root)
        result = run_audit(cfg, root)
        extra = ["reference audit: [{}] {}".format(result["status"], result["summary"])]
        print("config: {}".format(prov))
        print(qa.render_digest(surfaces, extra_lines=extra))
        return 0

    result = run_audit(cfg, root)
    print(qa.emit(result))
    return _EXIT[result["status"]]


# --- self-test --------------------------------------------------------------------------------------
_CHILD_MARKER_FAILURE = ("the subprocess-spawning cases (4 to 7) NOT RUN "
                         "(AIQT_QA_SELFTEST_CHILD=1 inherited at this top-level "
                         "--self-test entry); failing closed")


def _child_selftest_failures(output):
    """The '  - ' failure lines of a child --self-test output, exactly as printed."""
    return [line[4:] for line in output.splitlines() if line.startswith("  - ")]


# round 11 (decided 7): the reviewed child-acceptance if-chain, root arm first; the
# acceptance arm must be the FINAL arm of exactly this chain. Round 12 (decided 4):
# each arm is (test, body shape), so the arm BODIES are pinned as well as the tests.
_CHILD_ACCEPTANCE_CHAIN = (
    ("proc is None", "pass"),
    ("'SELF-TEST' not in proc.stdout + proc.stderr", "failures.append(one argument)"),
    ("not _exact_child_verdict(proc)", "failures.append(one argument)"),
)


def _child_acceptance_arm_shape(body):
    """The reviewed shape of one arm body: exactly pass, or exactly one
    failures.append call with one positional argument; anything else is reported
    with its unparsed text, so a neutralised, redirected or extended body differs."""
    import ast
    if len(body) == 1 and isinstance(body[0], ast.Pass):
        return "pass"
    if (len(body) == 1 and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Call)
            and ast.unparse(body[0].value.func) == "failures.append"
            and len(body[0].value.args) == 1 and not body[0].value.keywords):
        return "failures.append(one argument)"
    return "unreviewed body: " + "; ".join(ast.unparse(statement) for statement in body)


def _child_acceptance_chain_failure(source):
    """A failure line unless the child-acceptance if-chain in source is EXACTLY the
    reviewed one (round 11, decided 7, STRUCTURAL, by AST): every if/elif chain that
    ends at the exact-set acceptance test must be a suffix of the three reviewed
    arms and all three suffixes must be present, each arm compared by its test AND
    its body shape (round 12, decided 4: the root arm is exactly pass and each
    failure arm is exactly one failures.append call with one argument). So an arm
    inserted before the acceptance arm, a rewritten or duplicated arm, an
    acceptance arm detached from its guards, a neutralised or redirected
    failures.append in any arm body, and an else after the acceptance arm (round 13,
    decided 2: it runs on the accepted path and could clear every earlier failure)
    each fail by name; the round-10 textual count accepted an inserted arm. The
    append's message text is not compared."""
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return "the child-acceptance chain cannot be parsed ({}); failing closed".format(exc)
    chains = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        chain = [(ast.unparse(node.test), _child_acceptance_arm_shape(node.body))]
        cursor = node
        while len(cursor.orelse) == 1 and isinstance(cursor.orelse[0], ast.If):
            cursor = cursor.orelse[0]
            chain.append((ast.unparse(cursor.test), _child_acceptance_arm_shape(cursor.body)))
        if chain[-1][0] == _CHILD_ACCEPTANCE_CHAIN[-1][0]:
            if cursor.orelse:
                # round 13 (decided 2): the acceptance arm is the final arm with no
                # else; an else is recorded with its text, so the chain differs
                chain.append(("else", "unreviewed else: " + "; ".join(
                    ast.unparse(statement) for statement in cursor.orelse)))
            chains.append(tuple(chain))
    expected = [tuple(_CHILD_ACCEPTANCE_CHAIN[i:])
                for i in range(len(_CHILD_ACCEPTANCE_CHAIN))]
    if sorted(chains) != sorted(expected):
        return ("the child-acceptance arm is not the final arm of exactly the "
                "reviewed if-chain (got {!r}); the exact-failure-set comparison is "
                "not proven wired at the acceptance arm; failing closed".format(chains))
    return None


def _exact_child_verdict(proc):
    # #400 re-cut round 9 (round-8 claude MINOR 1): the ONE wiring that BOTH the
    # sibling-child acceptance branch and the mutant rejection evaluate, so weakening
    # the parent predicate (for example to rc 1 plus a NOT RUN substring anywhere) is
    # the same mutation the child-mutant vector rejects: the weakened verdict ACCEPTS
    # the mutant child and that vector turns red.
    return _child_marker_acceptable(proc.returncode, proc.stdout + proc.stderr)


def _child_marker_acceptable(returncode, output):
    """True only when an isolated child --self-test failed with EXACTLY the inherited-marker
    NOT RUN failure (#400 re-cut round 7, codex 4: accepting any rc-1 output with a NOT RUN
    substring let an independently failing child pass the parent, so the parent compares the
    child's exact failure set)."""
    return returncode == 1 and _child_selftest_failures(output) == [_CHILD_MARKER_FAILURE]


def _self_test():
    import shutil
    import tempfile

    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-audit-reference-selftest-"))
    try:
        cfg_present = {"surfaces": {"gate_roster": {"adapter": "glob-roster", "required": True,
                                                    "enabled": True, "dir": "tools",
                                                    "pattern": "check_*.py"}}}
        # 1. A present gate roster -> the harness returns PASS end to end.
        (tmp / "tools").mkdir()
        (tmp / "tools" / "check_example.py").write_text("# gate\n", encoding="utf-8")
        r = run_audit(cfg_present, tmp)
        if r["status"] != qa.PASS:
            failures.append("present roster expected PASS, got {}".format(r["status"]))
        if not r["evidence"]:
            failures.append("a PASS reference result must carry located evidence, got none")

        # 2. DISCRIMINATING: an ABSENT gate roster -> UNVERIFIABLE, never PASS. Removing the
        #    missing-evidence guard (in the adapter or here) would let this read PASS, failing this case.
        empty = Path(tempfile.mkdtemp(prefix="aiqt-audit-reference-empty-"))
        try:
            r = run_audit(cfg_present, empty)
            if r["status"] != qa.UNVERIFIABLE:
                failures.append("absent roster expected UNVERIFIABLE, got {} (missing evidence must never "
                                "read as PASS)".format(r["status"]))
        finally:
            shutil.rmtree(empty, ignore_errors=True)

        # 3. Every emitted result serializes as valid contract JSON with an in-set status.
        line = qa.emit(run_audit(cfg_present, tmp))
        import json
        obj = json.loads(line)
        if obj["status"] not in qa.STATUSES:
            failures.append("emitted result carried an out-of-set status {!r}".format(obj["status"]))
        if obj["schema"] != qa.RESULT_SCHEMA:
            failures.append("emitted result carried the wrong schema tag {!r}".format(obj["schema"]))

        # 4. DISCRIMINATING (arg validation precedes --self-test dispatch in main): a subprocess given
        #    `--config --self-test` exits 2 (a loud argument error via the shared config_arg), never a
        #    silent self-test run that exits 0. A recursion sentinel keeps a regressed build (which would
        #    re-enter the self-test) from spawning nested children.
        import os
        import subprocess
        if os.environ.get("AIQT_QA_SELFTEST_CHILD") != "1":
            selfpath = str(Path(__file__).resolve())
            childenv = dict(os.environ, AIQT_QA_SELFTEST_CHILD="1")
            proc = subprocess.run([sys.executable, "-I", "-B", selfpath,
                                   "--config", "--self-test"], capture_output=True, text=True,
                                  env=childenv)
            if proc.returncode != 2:
                failures.append("main did not validate --config before dispatching --self-test "
                                "(expected loud exit 2, got {})".format(proc.returncode))
            # 5. DISCRIMINATING (finding-5: pinned config resolves BEFORE --self-test dispatch): a subprocess
            #    given `--config <absent> --self-test`, or `AIQT_ASSURANCE_CONFIG="" --self-test`, exits 2
            #    (the loud pinned-source resolution), never 0. Moving the resolve back AFTER the --self-test
            #    dispatch lets the self-test run and exit 0 on a config the caller pinned, failing these.
            proc = subprocess.run([sys.executable, "-I", "-B", selfpath,
                                   "--config", str(tmp / "no-such-config.toml"), "--self-test"],
                                  capture_output=True, text=True, env=childenv)
            if proc.returncode != 2:
                failures.append("pinned absent --config did not exit 2 under --self-test (pinned resolution "
                                "must precede the self-test dispatch), got {}".format(proc.returncode))
            proc = subprocess.run([sys.executable, "-I", "-B", selfpath, "--self-test"],
                                  capture_output=True, text=True,
                                  env=dict(childenv, AIQT_ASSURANCE_CONFIG=""))
            if proc.returncode != 2:
                failures.append("present-but-empty AIQT_ASSURANCE_CONFIG did not exit 2 under --self-test, "
                                "got {}".format(proc.returncode))
            # 6. DISCRIMINATING (finding-3: an unknown option is a LOUD exit 2, never a silent default path):
            #    a subprocess given a misspelled --self-testx or a stray --bogus exits 2, never 0 by silently
            #    running the default audit path (which would let --self-testx masquerade as --self-test in CI
            #    parity). The child runs with cwd=tmp, whose tools/check_example.py makes the default audit
            #    resolve the gate roster to a PASS (exit 0) under portable defaults, so removing the
            #    reject_unknown_options guard deterministically lets --self-testx fall through and exit 0.
            for badarg in ("--self-testx", "--bogus"):
                proc = subprocess.run([sys.executable, "-I", "-B", selfpath, badarg],
                                      capture_output=True, text=True, env=childenv, cwd=str(tmp))
                if proc.returncode != 2:
                    failures.append("unknown option {!r} expected a loud exit 2, got {}".format(
                        badarg, proc.returncode))

            # 7. DISCRIMINATING (finding-6: a hostile sibling json.py/hashlib.py must NOT shadow a stdlib
            #    import and neuter this gate). Copy this script and _qa_adapter beside a hostile json.py and
            #    hashlib.py (each `raise SystemExit(0)`) and run `--self-test` isolated: the self-test must
            #    actually RUN (it emits its SELF-TEST marker). Reverting the sys.path.append import fix to an
            #    index-0 insertion lets the sibling shadow _qa_adapter's `import json` under -I and silently
            #    exit 0 before the self-test runs, so no marker appears. The child carries the sentinel so it
            #    skips this (and the other subprocess) case rather than recursing.
            # The copies live under <fixture>/tools/ so the copy's main() resolves its repository
            # root (parents[1] of the script) to the FIXTURE root and its D-400 special-file precheck
            # walks only that hermetic tree, never the system temp directory; _gen_common.py rides
            # along because the gate imports precheck_special_files from it.
            sib = Path(tempfile.mkdtemp(prefix="aiqt-audit-reference-sibling-")) / "tools"
            try:
                sib.mkdir()
                srcdir = Path(__file__).resolve().parent
                shutil.copy(str(srcdir / "audit_reference.py"), str(sib / "audit_reference.py"))
                shutil.copy(str(srcdir / "_qa_adapter.py"), str(sib / "_qa_adapter.py"))
                shutil.copy(str(srcdir / "_gen_common.py"), str(sib / "_gen_common.py"))
                for hostile in ("json.py", "hashlib.py"):
                    (sib / hostile).write_text("raise SystemExit(0)\n", encoding="utf-8")
                try:
                    # #400 re-cut round 9 (round-8 claude MINOR 2): a hanging child is a
                    # recorded failure, never a stalled suite
                    proc = subprocess.run([sys.executable, "-I", "-B",
                                           str(sib / "audit_reference.py"),
                                           "--self-test"], capture_output=True, text=True,
                                          env=childenv, timeout=600)
                except subprocess.TimeoutExpired:
                    failures.append("the isolated child --self-test exceeded its 600 s "
                                    "timeout; failing closed")
                    proc = None
                if proc is None:
                    pass
                elif "SELF-TEST" not in (proc.stdout + proc.stderr):
                    failures.append("a hostile sibling json.py/hashlib.py shadowed a stdlib import and "
                                    "prevented the self-test from running (finding-6): rc={} out={!r} "
                                    "err={!r}".format(proc.returncode, proc.stdout, proc.stderr))
                # #400 re-cut round 6 (codex 4): the copy ran with the child marker set, so
                # its own --self-test must RECORD the omitted subprocess cases as a NOT RUN
                # failure (rc 1); round 7 (codex MEDIUM 4) compares the child's EXACT failure
                # set, so an additional independent child failure turns this parent red; this
                # doubles as the vector for the inherited-marker branch below.
                elif not _exact_child_verdict(proc):
                    failures.append("the isolated child --self-test (marker inherited) expected "
                                    "rc 1 with EXACTLY the inherited-marker NOT RUN failure, "
                                    "got rc={} failures={!r}".format(
                                        proc.returncode,
                                        _child_selftest_failures(
                                            proc.stdout + proc.stderr)[-6:]))
            finally:
                shutil.rmtree(sib.parent, ignore_errors=True)

            # 7b. DISCRIMINATING (#400 re-cut round 7, codex 4): a child copy MUTATED to
            #     append one independent extra failure beside the inherited-marker line must
            #     be REJECTED by the exact-set comparison above; the round-6 acceptance
            #     (rc 1 plus a NOT RUN substring anywhere) passed it.
            mutsib = Path(tempfile.mkdtemp(prefix="aiqt-audit-reference-mutant-")) / "tools"
            try:
                mutsib.mkdir()
                srcdir = Path(__file__).resolve().parent
                shutil.copy(str(srcdir / "_qa_adapter.py"), str(mutsib / "_qa_adapter.py"))
                shutil.copy(str(srcdir / "_gen_common.py"), str(mutsib / "_gen_common.py"))
                original = (srcdir / "audit_reference.py").read_text(encoding="utf-8")
                # round 10 (#400 re-cut, r8 claude MINOR 1 closed at the CALL SITE too): the
                # recursion guard keeps a call-site mutant from exercising itself (a copy
                # runs with the child marker set and spawns nothing), so the acceptance arm
                # at case 7 is pinned STRUCTURALLY by AST (round 11, decided 7): the
                # acceptance arm must be the final arm of exactly the reviewed
                # if-chain, so a rewritten arm AND an arm inserted before it go red.
                chain_failure = _child_acceptance_chain_failure(original)
                if chain_failure:
                    failures.append(chain_failure)
                # round 11 (decided 7) DISCRIMINATING: the round-8 weakened arm
                # INSERTED before the acceptance arm must be refused by the structural
                # pin; the needle is built by concatenation so it matches only the
                # real arm, never this vector text.
                needle = "                elif not _exact_child_verdict" + "(proc):"
                weak_arm = ("                elif proc.returncode == 1 and "
                            "\"NOT RUN\" in (proc.stdout + proc.stderr):\n"
                            "                    pass\n")
                inserted = original.replace(needle, weak_arm + needle, 1)
                if inserted == original or not _child_acceptance_chain_failure(inserted):
                    failures.append("an arm inserted before the child-acceptance arm "
                                    "was not refused by the structural chain pin; "
                                    "failing closed")
                # round 12 (decided 4) DISCRIMINATING: the acceptance arm's BODY is
                # pinned too: its failures.append neutralised into a no-op call must be
                # refused by name (built by concatenation, so it matches only the real
                # arm body, never this vector text).
                body_needle = needle + "\n                    failures." + "append("
                if original.count(body_needle) != 1:
                    failures.append("the neutralised-body vector could not anchor the "
                                    "child-acceptance arm body exactly once; failing closed")
                else:
                    neutral_failure = _child_acceptance_chain_failure(original.replace(
                        body_needle, needle + "\n                    (lambda *a, **k: None)(", 1))
                    if not neutral_failure or "unreviewed body" not in neutral_failure:
                        failures.append("a neutralised failures.append in the child-acceptance "
                                        "arm was not refused by name by the structural chain "
                                        "pin; failing closed")
                # round 13 (decided 2) DISCRIMINATING: an else appended after the
                # acceptance arm runs on the accepted path and could clear every earlier
                # failure, so it must be refused by name; the anchor is the finally that
                # closes the chain (built by concatenation, so it matches only the real
                # chain end, never this vector text).
                else_anchor = ("\n            finally:\n                shutil.rmtree("
                               + "sib.parent, ignore_errors=True)")
                if original.count(else_anchor) != 1:
                    failures.append("the trailing-else vector could not anchor the end of the "
                                    "child-acceptance chain exactly once; failing closed")
                else:
                    else_failure = _child_acceptance_chain_failure(original.replace(
                        else_anchor, "\n                else:\n                    failures.clear()"
                        + else_anchor, 1))
                    if not else_failure or "unreviewed else" not in else_failure:
                        failures.append("an else after the child-acceptance arm was not refused "
                                        "by name by the structural chain pin; failing closed")
                # built by concatenation so this literal does not match itself
                anchor = "failures.append(" + "_CHILD_MARKER_FAILURE)"
                if original.count(anchor) != 1:
                    failures.append("the child-mutant vector could not anchor the marker branch "
                                    "exactly once; the exact-failure-set comparison is "
                                    "UNVERIFIED this run; failing closed")
                else:
                    (mutsib / "audit_reference.py").write_text(
                        original.replace(anchor, anchor + "; failures.append("
                                         + "'injected independent child regression')"),
                        encoding="utf-8")
                    try:
                        # #400 re-cut round 9 (round-8 claude MINOR 2): a hanging mutant
                        # child is a recorded failure, never a stalled suite
                        proc = subprocess.run(
                            [sys.executable, "-I", "-B",
                             str(mutsib / "audit_reference.py"), "--self-test"],
                            capture_output=True, text=True, env=childenv, timeout=600)
                    except subprocess.TimeoutExpired:
                        failures.append("the mutant child --self-test exceeded its 600 s "
                                        "timeout; failing closed")
                        proc = None
                    mutant_expected = [_CHILD_MARKER_FAILURE,
                                       "injected independent child regression"]
                    mutant_failures = ([] if proc is None else
                                       _child_selftest_failures(proc.stdout + proc.stderr))
                    if proc is None:
                        pass
                    elif proc.returncode != 1 or mutant_failures != mutant_expected:
                        # #400 re-cut round 8 (decided 3): the mutant must first REACH its
                        # designated failing state (exit 1 with EXACTLY the inherited-marker
                        # failure plus the injected regression); a crash, an import failure,
                        # a missing diagnostic or an extra failure leaves the mutation
                        # evidence UNVERIFIED and is a failed mutation test, never an
                        # accepted rejection
                        failures.append("the child mutant did not reach its designated "
                                        "failing state (rc 1 with exactly the "
                                        "inherited-marker failure plus the injected "
                                        "regression): rc={} failures={!r}; failing "
                                        "closed".format(proc.returncode,
                                                        mutant_failures[-6:]))
                    elif _exact_child_verdict(proc):
                        failures.append("a child mutant carrying one independent extra failure "
                                        "beside the inherited-marker line was ACCEPTED; the "
                                        "parent must compare the child's exact failure set")
            finally:
                shutil.rmtree(mutsib.parent, ignore_errors=True)
        else:
            # #400 re-cut round 6 (codex 4): an INHERITED child marker at a top-level
            # --self-test entry silently omitted cases 4 to 7; now a recorded NOT RUN
            # failure (the recursion guard still holds: this run spawns nothing).
            failures.append(_CHILD_MARKER_FAILURE)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: the reference audit returns PASS end to end on a present gate roster (with "
          "located evidence), UNVERIFIABLE on an absent one (missing evidence never reads as PASS), "
          "emits valid result-contract JSON, and runs --config operand validation, unknown-option rejection, "
          "and the loud pinned-source resolution before the --self-test dispatch (a malformed operand, an "
          "unrecognized option such as --self-testx, or a pinned-but-absent/empty config exits 2 even under "
          "--self-test); and a hostile sibling json.py/hashlib.py beside the script does NOT shadow a stdlib "
          "import (the sibling module is imported via sys.path.append, keeping stdlib precedence), so the "
          "self-test still runs under -I rather than being silently neutered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
