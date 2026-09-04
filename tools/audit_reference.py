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
    if "--self-test" in argv:
        return _self_test()
    explicit = None
    if "--config" in argv:
        i = argv.index("--config")
        if i + 1 < len(argv):
            explicit = argv[i + 1]
    try:
        cfg, root, prov = _resolve(explicit)
    except qa.ConfigError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: the reference audit returns PASS end to end on a present gate roster (with "
          "located evidence), UNVERIFIABLE on an absent one (missing evidence never reads as PASS), and "
          "emits valid result-contract JSON.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
