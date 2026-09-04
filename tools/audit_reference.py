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
report-only mode the runner calls: it prints the compact surface digest plus this audit's line and always
exits 0 (advisory, never gating). stdlib only. `--self-test` proves PASS on a present roster and
UNVERIFIABLE on an absent one (removing the missing-evidence guard fails it).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
          "--self-test).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
