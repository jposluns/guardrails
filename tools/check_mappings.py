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
rule mapping to it is a finding; a status value other than "withdrawn", and an [[id]] key outside the
closed set _standards.ID_KEYS, fail closed. The self-test also drives every generator that renders a
mapping id (gen_mappings, gen_draft_site's mappings data and gen_rules), each of which must refuse a
withdrawn id through the same _standards.withdrawn_finding test.
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


def _case(st02_fields, mapped, st03_fields=""):
    """(exit code, stdout) of the gate over a tempdir root whose one manifest gives ST02 the fields
    st02_fields (and ST03 st03_fields) and whose one synthetic rule maps the ids in mapped under
    map-fx-tight."""
    import contextlib
    import io
    with tempfile.TemporaryDirectory(prefix="mappings-selftest-") as tmp:
        root = Path(tmp)
        os.makedirs(root / ".aiqt" / "standards")
        os.makedirs(root / ".aiqt" / "core" / "rules")
        with open(root / ".aiqt" / "standards" / "fx.toml", "w", encoding="utf-8") as handle:
            handle.write(_FIXTURE_HEAD + st02_fields + _FIXTURE_TAIL + st03_fields)
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


# The one synthetic rule the generator vectors render: a security rule mapping ST02 under map-fx-tight.
_RULE_NAME = "fixture-rule.md"
_RULE_SOURCE = ("---\ncorpus-id: selfmp1\norigin: pack\nfamily: security\nfacet: SECI\n"
                "slug: mappings-selftest-rule\nmap-fx-tight: [ST02]\n---\n\n# Mappings self-test rule\n\n"
                "A fixture rule.\n")
_RULE_OUT = Path(".claude") / "rules" / "security" / "SECI-mappings-selftest-rule.md"


def _generators(st02_fields):
    """{name: (exit code, output, what the generator left at its output)} for each generator that renders
    a mapping id, over a tempdir root whose manifest gives ST02 the fields st02_fields and whose one rule
    (on disk) maps ST02: gen_mappings.run (exit, stderr, the CSV export or None), gen_rules.run (exit,
    stdout, the generated rule or None) and gen_draft_site's mappings data (0 or 2 for a refused page, the
    refusal or the rendered identifiers, None)."""
    import contextlib
    import io
    import gen_draft_site
    import gen_mappings
    from _standards import map_keys
    results = {}
    with tempfile.TemporaryDirectory(prefix="mappings-selftest-gen-") as tmp:
        root = Path(tmp)
        for parts in ((".aiqt", "standards"), (".aiqt", "core", "rules"), ("site", "downloads")):
            os.makedirs(root.joinpath(*parts))
        with open(root / ".aiqt" / "standards" / "fx.toml", "w", encoding="utf-8") as handle:
            handle.write(_FIXTURE_HEAD + st02_fields + _FIXTURE_TAIL)
        with open(root / ".aiqt" / "core" / "rules" / _RULE_NAME, "w", encoding="utf-8") as handle:
            handle.write(_RULE_SOURCE)
        # gen_rules binds MAP_KEYS at import from the real repository; the fixture's key comes from its own
        # manifest, as conformance.py rebinds it for an install.
        saved = (gen_rules.MAP_KEYS, gen_rules.SEQ_KEYS, gen_draft_site.ROOT, gen_draft_site._MAPPINGS_DATA)
        gen_rules.MAP_KEYS = map_keys(root)
        gen_rules.SEQ_KEYS = {"secondary"} | gen_rules.MAP_KEYS
        try:
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = gen_mappings.run(root, False)
            csv_path = root / "site" / "downloads" / "mappings.csv"
            results["gen_mappings"] = (code, err.getvalue(),
                                       csv_path.read_text(encoding="utf-8") if csv_path.exists() else None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = gen_rules.run(root, False)
            rule_path = root / _RULE_OUT
            results["gen_rules"] = (code, out.getvalue(),
                                    rule_path.read_text(encoding="utf-8") if rule_path.exists() else None)
            gen_draft_site.ROOT, gen_draft_site._MAPPINGS_DATA = root, None
            try:
                rows, _reg = gen_draft_site._mappings_data()
                results["gen_draft_site"] = (0, " ".join(row["identifier"] for row in rows), None)
            except gen_draft_site.RefusedPage as exc:
                results["gen_draft_site"] = (2, str(exc), None)
        finally:
            (gen_rules.MAP_KEYS, gen_rules.SEQ_KEYS, gen_draft_site.ROOT,
             gen_draft_site._MAPPINGS_DATA) = saved
    return results


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

    # The successor must be an active id: neither the withdrawn id itself nor another withdrawn id.
    code, out = _case('status = "withdrawn"\nincorporated-into = "ST02"\n', ["ST01"])
    check("refuse/incorporated-into-self", code == 2 and "error: fx.toml: incorporated-into for 'ST02' "
          "names 'ST02', which is not an active id" in out, (code, out))
    code, out = _case('status = "withdrawn"\nincorporated-into = "ST03"\n', ["ST01"], 'status = "withdrawn"\n')
    check("refuse/incorporated-into-withdrawn", code == 2 and "error: fx.toml: incorporated-into for 'ST02' "
          "names 'ST03', which is not an active id" in out, (code, out))

    # The status value is compared exactly: no case or whitespace folding.
    got = [_case(fields, ["ST01"]) for fields in ('status = "Withdrawn"\n', 'status = "WITHDRAWN"\n')]
    check("refuse/status-case-variant", all(code == 2 and "status for 'ST02' must be one of withdrawn" in out
                                            for code, out in got), got)
    got = [_case(fields, ["ST01"]) for fields in ('status = " withdrawn"\n', 'status = "withdrawn "\n',
                                                 'status = "withdrawn\\n"\n')]
    check("refuse/status-whitespace-variant", all(code == 2 and "status for 'ST02' must be one of withdrawn"
                                                  in out for code, out in got), got)

    # An [[id]] key outside the closed set fails closed naming the file, the id and the key, so a
    # misspelt or recased field never loads as an active id and is never dropped silently.
    for name, fields, key in (("refuse/unknown-id-key-recased-status", 'Status = "withdrawn"\n', "Status"),
                              ("refuse/unknown-id-key-withdrawn-flag", 'withdrawn = true\n', "withdrawn"),
                              ("refuse/unknown-id-key-state", 'state = "withdrawn"\n', "state"),
                              ("refuse/unknown-id-key-underscore-successor",
                               'status = "withdrawn"\nincorporated_into = "ST03"\n', "incorporated_into")):
        code, out = _case(fields, ["ST02"])
        check(name, code == 2 and "error: fx.toml: [[id]] 'ST02' has unknown key(s) '{}'".format(key) in out,
              (code, out))

    # Every generator that renders a mapping id refuses a withdrawn one (exit 2, naming the rule, the key
    # and the id) with nothing written, and renders the same id when it is not withdrawn.
    refusal = ("fixture-rule.md: map-fx-tight: id 'ST02' is withdrawn in Self-test fx 2026 (incorporated "
               "into ST03)")
    refused = _generators(_WITHDRAWN)
    code, err, written = refused["gen_mappings"]
    check("refuse/gen-mappings-withdrawn-id", code == 2 and refusal in err and written is None,
          (code, err, written))
    code, out, written = refused["gen_rules"]
    check("refuse/gen-rules-withdrawn-id", code == 2 and refusal in out and written is None, (code, out, written))
    code, out, _none = refused["gen_draft_site"]
    check("refuse/gen-draft-site-withdrawn-id", code == 2 and refusal in out, (code, out))
    rendered = _generators("")
    check("pass/generators-render-same-id-not-withdrawn",
          rendered["gen_mappings"][0] == 0 and ",ST02," in (rendered["gen_mappings"][2] or "")
          and rendered["gen_rules"][0] == 0 and "map-fx-tight: [ST02]" in (rendered["gen_rules"][2] or "")
          and rendered["gen_draft_site"][:2] == (0, "ST02"), rendered)

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
          "\"withdrawn\" (a case or whitespace variant included), an [[id]] key outside the closed set, and an "
          "incorporated-into without it, naming no active id (itself or another withdrawn id) or not a string, "
          "fail closed; gen_mappings, gen_draft_site and gen_rules refuse a withdrawn id with nothing written "
          "and render it when active; an unrecognized argument exits 2)".format(len(EXECUTED)))
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
