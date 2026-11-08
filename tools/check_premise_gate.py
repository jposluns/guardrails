#!/usr/bin/env python3
"""Vectors for the premise-review evaluator's read side (opf/tools/_opf_premise_gate.py).

  check_premise_gate.py --self-test  build fixture stores in a tempdir and assert every row
  check_premise_gate.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2 (this draft registers only the self-test; no
  gate or live mode is wired yet, and the evaluator never allows a store it has not fully read)

Each input/* and history/* row asserts one evaluation outcome (the exit and, for a refusal, its
cause token), and its flip/* companion repeats the same fixture with that row's one guard removed
(a module attribute replaced in process through unittest.mock; nothing is executed from text) and
asserts the outcome CHANGES, so a build that loses the guard turns the row red. The evaluator is
loaded from its repository path with importlib; the fixtures are synthetic stores under a
tempdir, and no live store is read or written.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_premise_gate.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE_REL = "opf/tools/_opf_premise_gate.py"

# A minimal VALID generation-1 manifest, byte-identical to the one `opf init` builds at this
# revision; embedded so this runner imports no store tooling of its own.
MANIFEST_TEXT = '''[archive]
period = "year"

[deliverables]

[deliverables."CHANGELOG.md"]
kind = "curated"
target = "CHANGELOG.md"

[modules]
concurrent_operation = false
delivery_assurance = false
governance = false
operational_policy = false

[opf]
import_status = "none"
layout = "inline"
posture = "required"
spec_version = "1.2.0"
standard = "opf"

[providers]

[providers.generic-git-remote]
handler = "builtin"
roles = ["sync"]

[providers.local-directory]
handler = "builtin"
roles = ["create", "sync"]

[store]
sync_target = ""

[types]

[types.autonomous_decision]
namespace = "AD"

[types.backlog_item]
namespace = "BI"

[types.block]
namespace = "BL"

[types.contribution]
namespace = "CN"

[types.done]
namespace = "DN"

[types.finding]
namespace = "FN"

[types.handoff]
namespace = "HO"

[types.maintainer_decision]
namespace = "MD"

[types.pending_decision]
namespace = "PD"

[types.preference_pattern]
namespace = "PP"

[types.reference]
namespace = "RF"

[types.worklog]
namespace = "WL"

[unmanaged]
paths = []

[vendors]
registered = []

[views]

[views."BACKLOG.md"]
kind = "composed"
sources = ["backlog_item", "block"]
target = ".working/BACKLOG.md"

[views."BLOCKS.md"]
kind = "composed"
sources = ["block"]
target = ".working/BLOCKS.md"

[views."CONTRIBUTIONS.md"]
kind = "composed"
sources = ["contribution"]
target = ".working/CONTRIBUTIONS.md"

[views."DECISIONS.md"]
kind = "composed"
sources = ["pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern"]
target = ".working/DECISIONS.md"

[views."DECISIONS.toml"]
kind = "projection"
sources = ["pending_decision", "autonomous_decision", "maintainer_decision", "preference_pattern"]
target = ".working/DECISIONS.toml"

[views."DONE.md"]
kind = "composed"
sources = ["done"]
target = ".working/DONE.md"

[views."FINDINGS.md"]
kind = "composed"
sources = ["finding"]
target = ".working/FINDINGS.md"

[views."HANDOFF.md"]
kind = "composed"
sources = ["handoff"]
target = ".working/HANDOFF.md"

[views."PIPELINE.md"]
kind = "composed"
sources = ["backlog_item", "block"]
target = ".working/PIPELINE.md"

[views."REFERENCES.md"]
kind = "composed"
sources = ["reference"]
target = ".working/REFERENCES.md"

[views."TODO.md"]
kind = "composed"
sources = ["backlog_item", "block"]
target = ".working/TODO.md"

[views."VERSION.md"]
kind = "deterministic"
sources = ["version"]
target = ".working/VERSION.md"

[views."WORKLOG.md"]
kind = "deterministic"
sources = ["worklog"]
target = ".working/WORKLOG.md"
'''

DATE = "2026-01-01T00:00:00Z"
ITEM = "BI-1"
START_DETAIL = ("flow start BI-1 status=drafting branch=unit-bi-1 lane=advance-1 scope=src "
                "tier=light\nnext apply the draft")


def _load_gate():
    """The evaluator module, loaded from its repository path (never executed from text)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_opf_premise_gate", str(ROOT / GATE_REL))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _toml_str(value):
    out = ['"']
    for ch in value:
        if ch in ('"', "\\"):
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ord(ch) < 0x20:
            out.append("\\u{:04X}".format(ord(ch)))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _links_toml(links):
    return "[" + ", ".join(
        '{{rel = "{}", id = "{}"}}'.format(link["rel"], link["id"]) for link in links) + "]"


def relates(target):
    return {"rel": "relates", "id": target}


class Fixture:
    """One synthetic generation-1 store under its own product root."""

    def __init__(self, tmp):
        self.root = Path(tmp)
        self.machine = self.root / ".working" / "toml"
        self.entries = []
        self.records = {t: [] for t in ("backlog_item", "finding", "pending_decision",
                                        "maintainer_decision", "autonomous_decision")}

    def entry(self, ident, detail=None, actor="assistant", kind="infra",
              summary="bookkeeping", links=()):
        row = {"id": ident, "date": DATE, "actor": {"kind": actor}, "kind": kind,
               "summary": summary, "links": [dict(link) for link in links]}
        if detail is not None:
            row["detail"] = detail
        self.entries.append(row)
        return row

    def record(self, rtype, ident, status, actor="assistant", links=(), extra=(), omit=()):
        row = {"id": ident, "type": rtype, "status": status, "title": "fixture " + ident,
               "created_at": DATE, "updated_at": DATE, "actor": {"kind": actor},
               "links": [dict(link) for link in links]}
        for key, value in extra:
            row[key] = value
        for key in omit:
            del row[key]
        self.records[rtype].append(row)
        return row

    def unit(self):
        """The common clean unit: its active item, the item's create entry, and `flow start`."""
        self.record("backlog_item", ITEM, "active", actor="maintainer")
        self.entry("WL-1", detail="opf-record create {} open".format(ITEM), kind="added",
                   summary="created " + ITEM, links=[relates(ITEM)])
        self.entry("WL-2", detail=START_DETAIL, links=[relates(ITEM)])
        return self

    def _record_toml(self, rows):
        parts = ["schema = 1\n"]
        for row in rows:
            parts.append("\n[[record]]\n")
            for key in ("id", "type", "status", "title", "created_at", "updated_at"):
                if key in row:
                    parts.append('{} = {}\n'.format(key, _toml_str(row[key])))
            parts.append('actor = {{kind = {}}}\n'.format(_toml_str(row["actor"]["kind"])))
            if "links" in row:
                parts.append("links = {}\n".format(_links_toml(row["links"])))
        return "".join(parts)

    def _worklog_toml(self):
        parts = ["schema = 1\n"]
        for row in self.entries:
            parts.append("\n[[entry]]\n")
            parts.append('id = {}\n'.format(_toml_str(row["id"])))
            parts.append('date = {}\n'.format(_toml_str(row["date"])))
            parts.append('actor = {{kind = {}}}\n'.format(_toml_str(row["actor"]["kind"])))
            parts.append('kind = {}\n'.format(_toml_str(row["kind"])))
            parts.append('summary = {}\n'.format(_toml_str(row["summary"])))
            if "detail" in row:
                parts.append('detail = {}\n'.format(_toml_str(row["detail"])))
            parts.append("links = {}\n".format(_links_toml(row["links"])))
        return "".join(parts)

    def write(self):
        os.makedirs(self.machine, exist_ok=True)
        with open(self.machine / "manifest.toml", "w", encoding="utf-8") as handle:
            handle.write(MANIFEST_TEXT)
        with open(self.machine / "worklog.toml", "w", encoding="utf-8") as handle:
            handle.write(self._worklog_toml())
        for rtype, rows in self.records.items():
            with open(self.machine / (rtype + ".index.toml"), "w", encoding="utf-8") as handle:
                handle.write(self._record_toml(rows))
        return str(self.root)


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id
# record; the execution gate reconciles the finalized report against the registry.
SUITE_ID = "premise-gate-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _vector(gate, fixture_path, expect_exit, expect_cause, flip, flipped_ok, item=ITEM,
            expect_head_wl=None):
    """One vector: the plain outcome, then the SAME fixture with the row's guard removed.

    Returns ((plain_ok, plain_detail), (red_ok, red_detail), plain_result); the caller records
    both through check() with this row's literal ids. unittest.mock is imported here, after the
    execution report is armed: its import registers an atexit callback of its own, and the
    report finalizer must be the process's first registration."""
    from unittest import mock
    got = gate.evaluate(fixture_path, item, expect_head_wl=expect_head_wl)
    plain = (got.exit_code == expect_exit and got.cause == expect_cause,
             (got.exit_code, got.cause, got.detail))
    attr, replacement = flip
    with mock.patch.object(gate, attr, replacement):
        flipped = gate.evaluate(fixture_path, item, expect_head_wl=expect_head_wl)
    turned = not (flipped.exit_code == expect_exit and flipped.cause == expect_cause)
    red = (turned and flipped_ok(flipped), (flipped.exit_code, flipped.cause, flipped.detail))
    return plain, red, got


def _noop(*_args, **_kwargs):
    return None


def _self_test():
    gate = _load_gate()

    with tempfile.TemporaryDirectory(prefix="premise-gate-selftest-") as tmp:
        base = Path(tmp)

        # The good unit every defective fixture starts from; also the history/start-only row.
        os.makedirs(base / "good")
        good = Fixture(base / "good").unit()
        good_path = good.write()

        # input/no-store: an empty directory resolves to no store. Its flip resolves every call
        # against the good store instead, so the row catches a resolver that stops refusing.
        os.makedirs(base / "empty")
        real_resolve = gate._resolve

        def resolve_good(_store, real=real_resolve, target=good_path):
            return real(target)

        plain, red, got = _vector(gate, str(base / "empty"), 2, "store-unresolved",
                                  ("_resolve", resolve_good), lambda res: res.exit_code == 0)
        check("input/no-store", plain[0], plain[1])
        check("flip/input/no-store", red[0], red[1])
        check("input/no-store-first-line",
              gate.render(got, ITEM).startswith("premise-gate: cannot-evaluate BI-1\n"),
              gate.render(got, ITEM))

        # input/worklog-unreadable: a directory sits where the active ledger should be. The flip
        # substitutes the good unit's parsed entries for the failed read (a loader that treats an
        # unreadable source as available history).
        os.makedirs(base / "wl")
        broken = Fixture(base / "wl").unit()
        broken_path = broken.write()
        os.mkdir(Path(broken_path) / ".working" / "toml" / "worklog.toml.d")
        os.rename(Path(broken_path) / ".working" / "toml" / "worklog.toml",
                  Path(broken_path) / ".working" / "toml" / "worklog.gone")
        os.rename(Path(broken_path) / ".working" / "toml" / "worklog.toml.d",
                  Path(broken_path) / ".working" / "toml" / "worklog.toml")

        def fabricate(*_args, entries=good.entries, **_kwargs):
            return {"schema": 1, "entry": [dict(entry) for entry in entries]}

        plain, red, _got = _vector(gate, broken_path, 2, "worklog-unreadable",
                                   ("_load_worklog_history", fabricate),
                                   lambda res: res.exit_code == 0)
        check("input/worklog-unreadable", plain[0], plain[1])
        check("flip/input/worklog-unreadable", red[0], red[1])

        # input/archive-bucket-unreadable: a bucket whose archive.toml is a directory. The flip
        # skips the archive read wholesale.
        os.makedirs(base / "arch")
        arch = Fixture(base / "arch").unit()
        arch_path = arch.write()
        os.makedirs(Path(arch_path) / ".working" / "toml" / "archive" / "2026" / "archive.toml")
        plain, red, _got = _vector(gate, arch_path, 2, "archive-unreadable",
                                   ("_read_archive", lambda *_a, **_k: ([], {})),
                                   lambda res: res.exit_code == 0)
        check("input/archive-bucket-unreadable", plain[0], plain[1])
        check("flip/input/archive-bucket-unreadable", red[0], red[1])

        # input/invalid-finding-record: a finding row with no title fails schema validation. With
        # validation removed the read completes and the draft's no-predicate refusal shows instead.
        os.makedirs(base / "inv")
        inv = Fixture(base / "inv").unit()
        inv.record("finding", "FN-1", "open", links=[relates(ITEM)], omit=("title",))
        inv.entry("WL-3", detail="opf-record create FN-1 open", kind="added",
                  summary="created FN-1", links=[relates("FN-1")])
        plain, red, _got = _vector(gate, inv.write(), 2, "record-invalid",
                                   ("_validate_clean_record", _noop),
                                   lambda res: res.cause == "not-evaluated")
        check("input/invalid-finding-record", plain[0], plain[1])
        check("flip/input/invalid-finding-record", red[0], red[1])

        # input/duplicate-wl: two entries on one WL number.
        os.makedirs(base / "dup")
        dup = Fixture(base / "dup").unit()
        dup.entry("WL-9", summary="first seat")
        dup.entry("WL-9", summary="second seat")
        plain, red, _got = _vector(gate, dup.write(), 2, "duplicate-wl",
                                   ("_require_fresh_wl", _noop), lambda res: res.exit_code == 0)
        check("input/duplicate-wl", plain[0], plain[1])
        check("flip/input/duplicate-wl", red[0], red[1])

        # input/unknown-flow-event: an event outside the section 5 set. The flip turns the
        # refusal into a silent skip (returns a false value instead of raising).
        os.makedirs(base / "ev")
        ev = Fixture(base / "ev").unit()
        ev.entry("WL-9", detail="flow bogus BI-1 status=drafting", links=[relates(ITEM)])

        def skip_unknown(event, events=gate.EVENTS):
            return event in events

        plain, red, _got = _vector(gate, ev.write(), 2, "unknown-flow-event",
                                   ("_require_known_event", skip_unknown),
                                   lambda res: res.exit_code == 0)
        check("input/unknown-flow-event", plain[0], plain[1])
        check("flip/input/unknown-flow-event", red[0], red[1])

        # input/malformed-scope: %41 escapes a byte the encoding leaves literal.
        os.makedirs(base / "scope")
        scope = Fixture(base / "scope")
        scope.record("backlog_item", ITEM, "active", actor="maintainer")
        scope.entry("WL-1", detail="opf-record create {} open".format(ITEM), kind="added",
                    summary="created " + ITEM, links=[relates(ITEM)])
        scope.entry("WL-2", detail=START_DETAIL.replace("scope=src", "scope=a%41"),
                    links=[relates(ITEM)])
        plain, red, _got = _vector(gate, scope.write(), 2, "flow-line-malformed",
                                   ("_check_scope", _noop), lambda res: res.exit_code == 0)
        check("input/malformed-scope", plain[0], plain[1])
        check("flip/input/malformed-scope", red[0], red[1])

        # input/verdict-missing-rev: a verdict entry without rev=.
        os.makedirs(base / "rev")
        rev = Fixture(base / "rev").unit()
        rev.entry("WL-3", detail="flow verdict BI-1 status=verifying phase=discovery "
                  "result=failed\nnext re-issue the round", links=[relates(ITEM)])
        plain, red, _got = _vector(gate, rev.write(), 2, "flow-line-malformed",
                                   ("_require_verdict_fields", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/verdict-missing-rev", plain[0], plain[1])
        check("flip/input/verdict-missing-rev", red[0], red[1])

        # input/no-start-entry: the item exists with no flow entry at all.
        os.makedirs(base / "nostart")
        nostart = Fixture(base / "nostart")
        nostart.record("backlog_item", ITEM, "active", actor="maintainer")
        nostart.entry("WL-1", detail="opf-record create {} open".format(ITEM), kind="added",
                      summary="created " + ITEM, links=[relates(ITEM)])
        plain, red, _got = _vector(gate, nostart.write(), 2, "no-start-entry",
                                   ("_require_start", _noop), lambda res: res.exit_code == 0)
        check("input/no-start-entry", plain[0], plain[1])
        check("flip/input/no-start-entry", red[0], red[1])

        # input/item-missing: a full flow history whose item is absent from the backlog index.
        os.makedirs(base / "noitem")
        noitem = Fixture(base / "noitem")
        noitem.entry("WL-1", detail="opf-record create {} open".format(ITEM), kind="added",
                     summary="created " + ITEM, links=[relates(ITEM)])
        noitem.entry("WL-2", detail=START_DETAIL, links=[relates(ITEM)])
        plain, red, _got = _vector(gate, noitem.write(), 2, "item-missing",
                                   ("_require_item", _noop), lambda res: res.exit_code == 0)
        check("input/item-missing", plain[0], plain[1])
        check("flip/input/item-missing", red[0], red[1])

        # input/finding-without-create-entry: a valid finding with no clean create entry. With
        # the guard removed the draft's no-predicate refusal shows instead.
        os.makedirs(base / "nocreate")
        nocreate = Fixture(base / "nocreate").unit()
        nocreate.record("finding", "FN-1", "open", links=[relates(ITEM)])
        plain, red, _got = _vector(gate, nocreate.write(), 2, "finding-create-missing",
                                   ("_require_finding_create", _noop),
                                   lambda res: res.cause == "not-evaluated")
        check("input/finding-without-create-entry", plain[0], plain[1])
        check("flip/input/finding-without-create-entry", red[0], red[1])

        # input/head-moved: the caller expected WL-1 but the store head is WL-2.
        plain, red, _got = _vector(gate, good_path, 2, "head-moved",
                                   ("_require_expected_head", _noop),
                                   lambda res: res.exit_code == 0, expect_head_wl="WL-1")
        check("input/head-moved", plain[0], plain[1])
        check("flip/input/head-moved", red[0], red[1])

        # history/start-only: a started unit with no round and no finding is allowed. The flip
        # drops `start` from the event set, so the entry reads as an unknown event.
        no_start_events = tuple(event for event in gate.EVENTS if event != "start")
        plain, red, got = _vector(gate, good_path, 0, None,
                                  ("EVENTS", no_start_events), lambda res: res.exit_code == 2)
        check("history/start-only", plain[0], plain[1])
        check("flip/history/start-only", red[0], red[1])
        check("history/start-only-first-line",
              gate.render(got, ITEM).startswith(
                  "premise-gate: allow BI-1 head=WL-2 snapshot=") and got.snapshot is not None
              and len(got.snapshot) == 64, gate.render(got, ITEM))

        # history/baseline-only: one non-failed DISCOVERY verdict, still no finding. The flip
        # removes `fixing` from the verdict status set.
        os.makedirs(base / "baseline")
        baseline = Fixture(base / "baseline").unit()
        baseline.entry("WL-3", detail="flow apply BI-1 status=verifying\nnext verify",
                       links=[relates(ITEM)])
        baseline.entry("WL-4", detail="flow verdict BI-1 status=fixing phase=discovery "
                       "result=findings rev=0123abc\nnext fix", links=[relates(ITEM)])
        verdict_no_fixing = dict(gate.EVENT_STATUS)
        verdict_no_fixing["verdict"] = frozenset({"verifying", "merging"})
        plain, red, _got = _vector(gate, baseline.write(), 0, None,
                                   ("EVENT_STATUS", verdict_no_fixing),
                                   lambda res: res.exit_code == 2)
        check("history/baseline-only", plain[0], plain[1])
        check("flip/history/baseline-only", red[0], red[1])

        # history/failed-rounds-only: failed rounds are reliability, never a window and never a
        # refusal. The flip removes `verifying` from the verdict status set.
        os.makedirs(base / "failed")
        failed = Fixture(base / "failed").unit()
        for number in (3, 4):
            failed.entry("WL-{}".format(number),
                         detail="flow verdict BI-1 status=verifying phase=discovery "
                         "result=failed rev=0123abc\nnext re-issue", links=[relates(ITEM)])
        verdict_no_verifying = dict(gate.EVENT_STATUS)
        verdict_no_verifying["verdict"] = frozenset({"fixing", "merging"})
        plain, red, _got = _vector(gate, failed.write(), 0, None,
                                   ("EVENT_STATUS", verdict_no_verifying),
                                   lambda res: res.exit_code == 2)
        check("history/failed-rounds-only", plain[0], plain[1])
        check("flip/history/failed-rounds-only", red[0], red[1])

        # history/importer-flow-lines-ignored: an importer-authored entry whose detail would be a
        # grammar refusal is history, never live state. The flip removes the firewall.
        os.makedirs(base / "importer")
        importer = Fixture(base / "importer").unit()
        importer.entry("WL-9", detail="flow bogus BI-1 status=nonsense", actor="importer",
                       summary="imported history", links=[relates(ITEM)])
        plain, red, _got = _vector(gate, importer.write(), 0, None,
                                   ("_ignored_by_firewall", lambda _ident, _kind: False),
                                   lambda res: res.exit_code == 2)
        check("history/importer-flow-lines-ignored", plain[0], plain[1])
        check("flip/history/importer-flow-lines-ignored", red[0], red[1])

        # history/finding-present-not-evaluated: with any finding on the unit this draft refuses
        # allowance (no recurrence predicate is wired). The flip removes that refusal, the exact
        # fail-open a later predicate must never reintroduce.
        os.makedirs(base / "finding")
        finding = Fixture(base / "finding").unit()
        finding.record("finding", "FN-1", "open", links=[relates(ITEM)])
        finding.entry("WL-3", detail="opf-record create FN-1 open", kind="added",
                      summary="created FN-1", links=[relates("FN-1")])
        plain, red, got = _vector(gate, finding.write(), 2, "not-evaluated",
                                  ("_require_predicate_ready", _noop),
                                  lambda res: res.exit_code == 0)
        check("history/finding-present-not-evaluated", plain[0], plain[1])
        check("flip/history/finding-present-not-evaluated", red[0], red[1])
        check("history/finding-present-first-line",
              gate.render(got, ITEM).startswith("premise-gate: cannot-evaluate BI-1\ncause: "
                                                "not-evaluated"), gate.render(got, ITEM))

    if FAILURES:
        print("SELF-TEST FAIL: check_premise_gate {} of {} check(s) failed".format(
            len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_premise_gate {} checks (an unresolved store, an unreadable "
          "worklog, an unreadable archive bucket, an invalid record, a duplicate WL, an unknown "
          "flow event, a malformed scope=, a verdict without rev=, a missing start entry, a "
          "missing item, a finding without its create entry, a moved head and a unit with any "
          "finding each refuse with their cause; a started unit, a baseline round, failed rounds "
          "and ignored importer flow lines each allow; every row turns red under the flip that "
          "removes its guard)".format(len(EXECUTED)))
    return 0


def main():
    argv = sys.argv[1:]
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted
    # only in that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv != ["--self-test"]:
        print("error: only --self-test is supported by this draft; arguments {!r} refused "
              "(cannot evaluate)".format(argv), file=sys.stderr)
        return 2
    if report_path is not None:
        # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
        sys.path.append(str(Path(__file__).resolve().parent))
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    return _self_test()


if __name__ == "__main__":
    _code = main()
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
