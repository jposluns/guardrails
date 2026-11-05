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
absent one (removing the missing-evidence guard fails it). `--self-test --self-test-child` is the internal
child mode the self-test's own launches pass: it omits the cases that launch this tool again, names them
on a SELF-TEST NOT RUN line and exits 1, never printing the PASS line; `--self-test-child` without
`--self-test` is a loud exit 2.
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
    unknown = qa.reject_unknown_options(argv, {"--self-test", "--digest", qa.SELFTEST_CHILD_OPTION})
    if unknown is not None:
        print("error: unrecognized option {!r}".format(unknown), file=sys.stderr)
        return 2
    # The internal child-mode option only qualifies --self-test; alone it is a loud error, never a default run.
    child = qa.SELFTEST_CHILD_OPTION in argv
    if child and "--self-test" not in argv:
        print("error: {} is only valid with --self-test".format(qa.SELFTEST_CHILD_OPTION), file=sys.stderr)
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
        return _self_test(child)

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
# The self-test cases that launch this tool again. A child-mode run (qa.SELFTEST_CHILD_OPTION, an argument,
# never an environment variable) omits exactly these, so a regressed build whose child re-enters the
# self-test cannot spawn nested children, and names them on qa.selftest_not_run_line instead of passing.
_SELF_TEST_LAUNCH_CASES = (
    "4 --config validated before the --self-test dispatch",
    "5 pinned config resolved before the --self-test dispatch",
    "6 unknown option refused",
    "7 hostile sibling does not shadow a stdlib import",
    "8 child mode omits exactly these cases",
    "9 executed failure beside omitted cases fails",
)


def _self_test(child=False):
    import shutil
    import tempfile

    failures = []
    # A case that launches this tool again runs only in a full run (child false); a child-mode run
    # records it as omitted instead.
    ran, omitted = [], []

    def launches(case):
        (omitted if child else ran).append(case)
        return not child

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
        #    silent self-test run that exits 0. Each child that asks for --self-test carries the child-mode
        #    option, so a regressed build (which would re-enter the self-test) spawns no nested children.
        import os
        import subprocess
        selfpath = str(Path(__file__).resolve())
        if launches("4 --config validated before the --self-test dispatch"):
            proc = subprocess.run([sys.executable, "-I", "-B", selfpath,
                                   "--config", "--self-test", qa.SELFTEST_CHILD_OPTION], capture_output=True,
                                  text=True)
            if proc.returncode != 2:
                failures.append("main did not validate --config before dispatching --self-test "
                                "(expected loud exit 2, got {})".format(proc.returncode))
        # 5. DISCRIMINATING (finding-5: pinned config resolves BEFORE --self-test dispatch): a subprocess
        #    given `--config <absent> --self-test`, or `AIQT_ASSURANCE_CONFIG="" --self-test`, exits 2
        #    (the loud pinned-source resolution), never 0. Moving the resolve back AFTER the --self-test
        #    dispatch lets the self-test run and exit 0 on a config the caller pinned, failing these.
        if launches("5 pinned config resolved before the --self-test dispatch"):
            proc = subprocess.run([sys.executable, "-I", "-B", selfpath,
                                   "--config", str(tmp / "no-such-config.toml"), "--self-test",
                                   qa.SELFTEST_CHILD_OPTION], capture_output=True, text=True)
            if proc.returncode != 2:
                failures.append("pinned absent --config did not exit 2 under --self-test (pinned resolution "
                                "must precede the self-test dispatch), got {}".format(proc.returncode))
            proc = subprocess.run([sys.executable, "-I", "-B", selfpath, "--self-test", qa.SELFTEST_CHILD_OPTION],
                                  capture_output=True, text=True,
                                  env=dict(os.environ, AIQT_ASSURANCE_CONFIG=""))
            if proc.returncode != 2:
                failures.append("present-but-empty AIQT_ASSURANCE_CONFIG did not exit 2 under --self-test, "
                                "got {}".format(proc.returncode))
        # 6. DISCRIMINATING (finding-3: an unknown option is a LOUD exit 2, never a silent default path):
        #    a subprocess given a misspelled --self-testx or a stray --bogus exits 2, never 0 by silently
        #    running the default audit path (which would let --self-testx masquerade as --self-test in CI
        #    parity). The child runs with cwd=tmp, whose tools/check_example.py makes the default audit
        #    resolve the gate roster to a PASS (exit 0) under portable defaults, so removing the
        #    reject_unknown_options guard deterministically lets --self-testx fall through and exit 0. The
        #    child-mode option is matched exactly: a near miss of it is unknown, and the option itself
        #    without --self-test is a loud exit 2, never the default audit.
        if launches("6 unknown option refused"):
            for badarg in ("--self-testx", "--bogus", qa.SELFTEST_CHILD_OPTION + "x",
                           qa.SELFTEST_CHILD_OPTION + "=1", qa.SELFTEST_CHILD_OPTION):
                proc = subprocess.run([sys.executable, "-I", "-B", selfpath, badarg],
                                      capture_output=True, text=True, cwd=str(tmp))
                if proc.returncode != 2:
                    failures.append("unknown option {!r} expected a loud exit 2, got {}".format(
                        badarg, proc.returncode))

        # 7. DISCRIMINATING (finding-6: a hostile sibling json.py/hashlib.py must NOT shadow a stdlib
        #    import and neuter this gate). Copy this script and _qa_adapter beside a hostile json.py and
        #    hashlib.py (each `raise SystemExit(0)`) and run `--self-test` isolated: the self-test must
        #    actually RUN (it emits its SELF-TEST marker). Reverting the sys.path.append import fix to an
        #    index-0 insertion lets the sibling shadow _qa_adapter's `import json` under -I and silently
        #    exit 0 before the self-test runs, so no marker appears. The child runs in child mode, so it
        #    omits this (and the other launch) case rather than recursing.
        if launches("7 hostile sibling does not shadow a stdlib import"):
            sib = Path(tempfile.mkdtemp(prefix="aiqt-audit-reference-sibling-"))
            try:
                srcdir = Path(__file__).resolve().parent
                shutil.copy(str(srcdir / "audit_reference.py"), str(sib / "audit_reference.py"))
                shutil.copy(str(srcdir / "_qa_adapter.py"), str(sib / "_qa_adapter.py"))
                for hostile in ("json.py", "hashlib.py"):
                    (sib / hostile).write_text("raise SystemExit(0)\n", encoding="utf-8")
                proc = subprocess.run([sys.executable, "-I", "-B", str(sib / "audit_reference.py"),
                                       "--self-test", qa.SELFTEST_CHILD_OPTION], capture_output=True, text=True)
                if "SELF-TEST" not in (proc.stdout + proc.stderr):
                    failures.append("a hostile sibling json.py/hashlib.py shadowed a stdlib import and "
                                    "prevented the self-test from running (finding-6): rc={} out={!r} "
                                    "err={!r}".format(proc.returncode, proc.stdout, proc.stderr))
            finally:
                shutil.rmtree(sib, ignore_errors=True)

        # 8. DISCRIMINATING (child mode is an argument, never the environment): a child given
        #    `--self-test --self-test-child` omits exactly the cases that launch this tool again, prints the
        #    NOT RUN line naming them and exits 1, never the PASS line, with AIQT_QA_SELFTEST_CHILD absent,
        #    empty, "0" or "1" in its environment alike. A gate that read the variable again, or a child
        #    mode that printed PASS or exited 0, fails this.
        if launches("8 child mode omits exactly these cases"):
            want = qa.selftest_not_run_line(list(_SELF_TEST_LAUNCH_CASES))
            for inherited in (None, "", "0", "1"):
                childenv = {k: v for k, v in os.environ.items() if k != "AIQT_QA_SELFTEST_CHILD"}
                if inherited is not None:
                    childenv["AIQT_QA_SELFTEST_CHILD"] = inherited
                try:
                    proc = subprocess.run([sys.executable, "-I", "-B", selfpath, "--self-test",
                                           qa.SELFTEST_CHILD_OPTION], capture_output=True, text=True,
                                          env=childenv, timeout=300)
                except subprocess.TimeoutExpired:
                    failures.append("a child-mode self-test (AIQT_QA_SELFTEST_CHILD={!r}) timed out".format(
                        inherited))
                    continue
                lines = proc.stdout.splitlines()
                if (proc.returncode != 1 or [ln for ln in lines if ln.startswith("SELF-TEST")] != [want]):
                    failures.append("a child-mode self-test (AIQT_QA_SELFTEST_CHILD={!r}) must exit 1 with "
                                    "only the NOT RUN line {!r}, got rc={} out={!r} err={!r}".format(
                                        inherited, want, proc.returncode, proc.stdout[-600:],
                                        proc.stderr[-600:]))

        # 9. DISCRIMINATING (an executed failure beside omitted cases is a failure): a child-mode run in
        #    which an executed case fails (run_audit pointed at an absent root, so case 1 reads
        #    UNVERIFIABLE) exits 1 and prints SELF-TEST FAIL with that failure as well as the NOT RUN line,
        #    never the PASS line. A report that let the omission stand in for the failure fails this.
        if launches("9 executed failure beside omitted cases fails"):
            toolsdir = str(Path(__file__).resolve().parent)
            probe = ("import sys; sys.path.append({tools!r}); import audit_reference as ar; "
                     "real = ar.run_audit; ar.run_audit = lambda cfg, root: real(cfg, root / 'no-such-root'); "
                     "sys.exit(ar._self_test(True))").format(tools=toolsdir)
            want = qa.selftest_not_run_line(list(_SELF_TEST_LAUNCH_CASES))
            try:
                proc = subprocess.run([sys.executable, "-I", "-B", "-c", probe], capture_output=True,
                                      text=True, timeout=300)
                lines = proc.stdout.splitlines()
                if (proc.returncode != 1 or "SELF-TEST FAIL:" not in lines or want not in lines
                        or any(ln.startswith("SELF-TEST PASS") for ln in lines)
                        or not any(ln.startswith("  - present roster expected PASS") for ln in lines)):
                    failures.append("a child-mode self-test with an executed failure must exit 1 with SELF-TEST "
                                    "FAIL naming it and the NOT RUN line, got rc={} out={!r} err={!r}".format(
                                        proc.returncode, proc.stdout[-600:], proc.stderr[-600:]))
            except subprocess.TimeoutExpired:
                failures.append("the child-mode self-test with an executed failure timed out")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Every case that launches this tool again is accounted for: run in a full run, omitted in child mode.
    if (omitted if child else ran) != list(_SELF_TEST_LAUNCH_CASES):
        failures.append("the {} run accounted for the launch cases {!r}, expected {!r}".format(
            "child-mode" if child else "full", omitted if child else ran, list(_SELF_TEST_LAUNCH_CASES)))

    return qa.selftest_report(failures, omitted, (
          "SELF-TEST PASS: the reference audit returns PASS end to end on a present gate roster (with "
          "located evidence), UNVERIFIABLE on an absent one (missing evidence never reads as PASS), "
          "emits valid result-contract JSON, and runs --config operand validation, unknown-option rejection, "
          "and the loud pinned-source resolution before the --self-test dispatch (a malformed operand, an "
          "unrecognized option such as --self-testx, or a pinned-but-absent/empty config exits 2 even under "
          "--self-test); and a hostile sibling json.py/hashlib.py beside the script does NOT shadow a stdlib "
          "import (the sibling module is imported via sys.path.append, keeping stdlib precedence), so the "
          "self-test still runs under -I rather than being silently neutered; and a child-mode run "
          "(--self-test-child, an argument, never an environment variable) omits exactly the cases that "
          "launch this tool again, names them on a NOT RUN line and exits 1, beside an executed failure too, "
          "never printing this line."))


if __name__ == "__main__":
    sys.exit(main())
