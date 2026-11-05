#!/usr/bin/env python3
"""Validate every standards-mapping id in the rule corpus against its vendored, pinned manifest.

Offline, stdlib only: no network, no dependency outside the repo. This is the crosswalk's no-fabrication
gate. An id a rule cites in a `map-<key>` frontmatter list must appear verbatim in that framework's
manifest under .aiqt/standards/, or CI fails, so a fabricated or typo'd public mapping id cannot ship.
The mapping KEYS are already constrained by gen_rules (MAP_KEYS is derived from the same manifests); this
gate checks the VALUES.

  check_mappings.py              exit 0 clean, 1 mapping finding, 2 malformed manifest or corpus (fail-closed)
  check_mappings.py --self-test  assert the withdrawn-id vectors over tempdir fixtures
  check_mappings.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

An id its manifest marks withdrawn (status = "withdrawn" on its [[id]]) stays a recognized id, but a
rule mapping to it is a finding; a status value other than "withdrawn" fails closed.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_mappings.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from _standards import load_manifests, validate_mappings, ManifestError  # noqa: E402
import gen_rules  # noqa: E402  (reuse the one frontmatter parser + full corpus validation)


def run_gate():
    root = repo_root()
    try:
        manifests = load_manifests(root / ".aiqt" / "standards", root=root)
    except (ManifestError, OSError) as exc:
        # OSError too: an unreadable manifest file, or an existing-but-unlistable standards dir
        # (load_manifests raises rather than reading it as empty), is a read error, not a clean skip.
        # Fail closed (exit 2) rather than escape as a traceback, matching conformance.py's C4.
        print("error: {}".format(exc))
        return 2
    src_dir = root / ".aiqt" / "core" / "rules"
    if not src_dir.is_dir():
        # Fail closed: an absent corpus must not read as "0 mappings, all clean". rglob on a missing
        # dir yields nothing, which would otherwise pass; make the anomaly explicit.
        print("error: rule corpus not found at {}".format(src_dir))
        return 2
    try:
        corpus = gen_rules.load_corpus(src_dir)
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2

    findings, mapped, tight, broad = validate_mappings(corpus, manifests)

    if findings:
        print("FAIL: {} mapping issue(s):".format(len(findings)))
        for line in sorted(findings):
            print("  {}".format(line))
        return 1
    n_rules = sum(1 for _s, fm, _r in corpus if any(k.startswith("map-") for k in fm))
    print("PASS: {} mapping id(s): {} tight, {} broad, across {} rule(s), validate against "
          "{} manifest(s)".format(mapped, tight, broad, n_rules, len(manifests)))
    return 0


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "mappings-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


# A synthetic framework "fx": ST01 active, ST02 carrying the [[id]] fields under test, ST03 active.
_FIXTURE_HEAD = ('map-key = "map-fx"\nname = "Self-test fx"\npublisher = "AIQT self-test"\n'
                 'edition = "2026"\nkind = "control"\nstatus = "stable"\ncitation-unit = "control"\n'
                 'id-pattern = "ST[0-9]{2}"\nsource-artefact = "self-test fixture"\n'
                 'retrieved = "2026-01-01"\ncatalogue = "full"\n'
                 '[[id]]\ncode = "ST01"\ntitle = "one"\n[[id]]\ncode = "ST02"\ntitle = "two"\n')
_FIXTURE_TAIL = '[[id]]\ncode = "ST03"\ntitle = "three"\n'
_WITHDRAWN = 'status = "withdrawn"\nincorporated-into = "ST03"\n'


def _case(st02_fields, mapped):
    """(exit code, stdout) of the gate over a tempdir root whose one manifest gives ST02 the fields
    st02_fields and whose one synthetic rule maps the ids in mapped under map-fx-tight."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory(prefix="mappings-selftest-") as tmp:
        root = Path(tmp)
        os.makedirs(root / ".aiqt" / "standards")
        os.makedirs(root / ".aiqt" / "core" / "rules")
        with open(root / ".aiqt" / "standards" / "fx.toml", "w", encoding="utf-8") as handle:
            handle.write(_FIXTURE_HEAD + st02_fields + _FIXTURE_TAIL)
        corpus = [(Path("fixture-rule.md"), {"map-fx-tight": list(mapped)}, "fixture-rule.md")]
        saved_root, saved_load = globals()["repo_root"], gen_rules.load_corpus
        globals()["repo_root"] = lambda: root
        gen_rules.load_corpus = lambda _src: corpus
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = run_gate()
        finally:
            globals()["repo_root"], gen_rules.load_corpus = saved_root, saved_load
    return code, out.getvalue()


def _self_test():
    code, out = _case(_WITHDRAWN, ["ST02"])
    check("refuse/mapping-to-withdrawn-id", code == 1 and "fixture-rule.md: map-fx-tight: id 'ST02' is "
          "withdrawn in Self-test fx 2026 (incorporated into ST03)" in out, (code, out))

    code, out = _case("", ["ST02"])
    check("pass/same-id-not-withdrawn", code == 0 and out.startswith("PASS:"), (code, out))

    code, out = _case(_WITHDRAWN, ["ST01", "ST03"])
    check("pass/withdrawn-id-unmapped", code == 0 and out.startswith("PASS:"), (code, out))

    for name, fields in (("refuse/malformed-status-value", 'status = "retired"\n'),
                         ("refuse/malformed-status-type", 'status = 1\n'),
                         ("refuse/malformed-status-empty", 'status = ""\n'),
                         ("refuse/incorporated-without-withdrawn", 'incorporated-into = "ST03"\n'),
                         ("refuse/incorporated-unknown-id", 'status = "withdrawn"\nincorporated-into = "ST09"\n'),
                         ("refuse/incorporated-not-string",
                          'status = "withdrawn"\nincorporated-into = ["ST03"]\n')):
        code, out = _case(fields, ["ST01"])
        check(name, code == 2 and out.startswith("error: fx.toml:"), (code, out))

    got = [_main_exit(argv) for argv in (["--bogus"], ["--self-test", "--bogus"],
                                         ["--execution-report", "relative/report"])]
    check("refuse/unrecognized-argument", got == [2, 2, 2], got)

    if FAILURES:
        print("SELF-TEST FAIL: check_mappings {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_mappings {} checks (a mapping to a withdrawn id fails naming the rule, key "
          "and id; the same id not withdrawn passes; a withdrawn id no rule maps passes; a status other than "
          "\"withdrawn\", and an incorporated-into without it, naming no active id or not a string, fail "
          "closed; an unrecognized argument exits 2)".format(len(EXECUTED)))
    return 0


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    saved = sys.argv
    sys.argv = [saved[0]] + list(argv)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main()
    finally:
        sys.argv = saved


def main():
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv not in ([], ["--self-test"]):
        print("error: unrecognized arguments {!r}; fail closed".format(argv), file=sys.stderr)
        return 2
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    return run_gate()


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
