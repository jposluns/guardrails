#!/usr/bin/env python3
"""Vectors for the premise-review evaluator's read side (opf/tools/_opf_premise_gate.py).

  check_premise_gate.py --self-test  build fixture stores in a tempdir and assert every row
  check_premise_gate.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2 (this draft registers only the self-test; no
  gate or live mode is wired yet, and the evaluator never allows a store it has not fully read)

Each input/* and history/* outcome row asserts one evaluation outcome (the exit, for a refusal
its cause token, and, where another guard refuses the same fixture under the same cause, enough
of the detail text to tell the two refusals apart), and its flip/* companion repeats the same
fixture with that row's one guard removed (a module attribute replaced in process through
unittest.mock; nothing is executed from text) and asserts that asserted outcome CHANGES, so a
build that loses the guard turns the row red.
Each flip removes exactly the guard its row's comment names, so a red flip points at that guard.
The *-first-line rows pin the rendered first line of a result another row already asserts and
carry no flip; input/archive-span-huge also carries no flip (it pins the arithmetic span read, a
property of the reconciliation itself, not a removable guard) and runs its evaluation in a child
process under a memory limit and a wall-clock timeout, so an expanding build dies or stalls
there, never in this runner, and a timed-out child fails that row. The
evaluator is loaded from its repository path with importlib; the fixtures are synthetic stores
under a tempdir, and no live store is read or written.
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
import subprocess
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


def _toml_value(value):
    """One Python value as a TOML inline value; only the shapes these fixtures use."""
    if isinstance(value, str):
        return _toml_str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join("{} = {}".format(key, _toml_value(item))
                               for key, item in value.items()) + "}"
    raise TypeError("unsupported fixture value {!r}".format(value))


def _append_toml_rows(path, table, rows):
    """Append [[table]] blocks for rows the Fixture serializers cannot express (the imported
    envelope's unrecorded and import tables)."""
    parts = []
    for row in rows:
        parts.append("\n[[{}]]\n".format(table))
        for key, value in row.items():
            parts.append("{} = {}\n".format(key, _toml_value(value)))
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("".join(parts))


# Envelope-valid imported rows, mirroring _opf_schema's own self-test fixtures, so the rows that
# refuse them pin the misplacement alone: each row passes the section 8.3 imported envelope.
IMPORT_RUN = "imp-20260812T091402Z-0123456789abcdef"
IMPORT_TABLE = {"source": ".working/imported/import/" + IMPORT_RUN + "/originals/DECISIONS.md",
                "source_sha256": "0123456789abcdef" * 4, "run": IMPORT_RUN,
                "imported_at": "2026-08-12T09:14:02Z"}


def imported_worklog_row(**over):
    row = {"id": "imported:WL-3", "type": "worklog", "status": "recorded",
           "title": "Legacy fix entry", "date": "2024-11-02T00:00:00Z", "kind": "fixed",
           "summary": "Fixed the stale view",
           "actor": {"kind": "importer", "id": "import-assistant"},
           "unrecorded": [{"field": "detail", "reason": "not_applicable"}],
           "import": dict(IMPORT_TABLE)}
    row.update(over)
    return row


def imported_decision_row():
    return {"id": "imported:PD-1", "type": "pending_decision", "status": "decided",
            "title": "Keep the legacy ledger read-only", "created_at": "2025-03-01T10:00:00Z",
            "actor": {"kind": "importer", "id": "import-assistant"},
            "decision": "The legacy ledger stays read-only", "decided_by": "maintainer",
            "unrecorded": [{"field": "updated_at", "reason": "not_recorded_in_source"},
                           {"field": "decided_at", "reason": "unparsed"}],
            "import": dict(IMPORT_TABLE, span=[0, 120],
                           history=[{"field": "decided_at", "value": "2025-03-02"}],
                           unparsed=["Decided at the March sync; no time recorded."])}


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

    def bucket(self, name, archive_text, entries=None):
        """One rotation-archive bucket: its archive.toml, and (when `entries` is given, an empty
        list included) a worklog.toml holding those rows."""
        bucket = self.machine / "archive" / name
        os.makedirs(bucket, exist_ok=True)
        with open(bucket / "archive.toml", "w", encoding="utf-8") as handle:
            handle.write(archive_text)
        if entries is not None:
            side = Fixture(self.root)
            side.entries = list(entries)
            with open(bucket / "worklog.toml", "w", encoding="utf-8") as handle:
                handle.write(side._worklog_toml())

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
            expect_head_wl=None, expect_detail=None):
    """One vector: the plain outcome, then the SAME fixture with the row's guard removed.

    `expect_detail`, where given, is a substring the refusal detail must carry, for a row whose
    fixture another guard would also refuse under the same cause: the detail then tells this
    row's guard from the other, and losing only this row's guard still turns the row.
    Returns ((plain_ok, plain_detail), (red_ok, red_detail), plain_result); the caller records
    both through check() with this row's literal ids. unittest.mock is imported here, after the
    execution report is armed: its import registers an atexit callback of its own, and the
    report finalizer must be the process's first registration."""
    from unittest import mock

    def matches(result):
        return (result.exit_code == expect_exit and result.cause == expect_cause
                and (expect_detail is None or expect_detail in (result.detail or "")))

    got = gate.evaluate(fixture_path, item, expect_head_wl=expect_head_wl)
    plain = (matches(got), (got.exit_code, got.cause, got.detail))
    attr, replacement = flip
    with mock.patch.object(gate, attr, replacement):
        flipped = gate.evaluate(fixture_path, item, expect_head_wl=expect_head_wl)
    turned = not matches(flipped)
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

        # input/worklog-unreadable: a directory sits where the active ledger should be, while the
        # whole unit history sits rotated in a declared archive span. The flip removes the one
        # guard that keeps a failed active read from becoming an empty ledger, so the archived
        # history alone would grade the unit.
        os.makedirs(base / "wl")
        broken = Fixture(base / "wl").unit()
        rotated = list(broken.entries)
        broken.entries = []
        broken_path = broken.write()
        broken.bucket("2026", 'schema = 1\n[[worklog_moved]]\nspan = ["WL-1", "WL-2"]\n'
                      'destination = "archive/2026/worklog.toml"\n', rotated)
        os.mkdir(Path(broken_path) / ".working" / "toml" / "worklog.toml.d")
        os.rename(Path(broken_path) / ".working" / "toml" / "worklog.toml",
                  Path(broken_path) / ".working" / "toml" / "worklog.gone")
        os.rename(Path(broken_path) / ".working" / "toml" / "worklog.toml.d",
                  Path(broken_path) / ".working" / "toml" / "worklog.toml")
        plain, red, _got = _vector(gate, broken_path, 2, "worklog-unreadable",
                                   ("_worklog_read_failed",
                                    lambda _exc: {"schema": 1, "entry": []}),
                                   lambda res: res.exit_code == 0)
        check("input/worklog-unreadable", plain[0], plain[1])
        check("flip/input/worklog-unreadable", red[0], red[1])

        # input/archive-bucket-unreadable: a bucket whose archive.toml is a directory. The flip
        # swallows the refused archive.toml read into a default empty archive manifest.
        os.makedirs(base / "arch")
        arch = Fixture(base / "arch").unit()
        arch_path = arch.write()
        os.makedirs(Path(arch_path) / ".working" / "toml" / "archive" / "2026" / "archive.toml")
        real_archive_toml = gate._read_archive_toml

        def swallow_archive_toml(root_fd, rel, snapshot, required=True, real=real_archive_toml):
            try:
                return real(root_fd, rel, snapshot, required)
            except gate.PremiseGateCannot:
                return {"schema": 1}

        plain, red, _got = _vector(gate, arch_path, 2, "archive-unreadable",
                                   ("_read_archive_toml", swallow_archive_toml),
                                   lambda res: res.exit_code == 0)
        check("input/archive-bucket-unreadable", plain[0], plain[1])
        check("flip/input/archive-bucket-unreadable", red[0], red[1])

        # input/archive-undeclared-worklog: a bucket carries a worklog.toml (here an empty one)
        # that its archive.toml does not declare; it must never be silently unread.
        os.makedirs(base / "undecl")
        undecl = Fixture(base / "undecl").unit()
        undecl_path = undecl.write()
        undecl.bucket("2026", "schema = 1\n", entries=[])
        plain, red, _got = _vector(gate, undecl_path, 2, "archive-unreadable",
                                   ("_require_declared_worklog", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/archive-undeclared-worklog", plain[0], plain[1])
        check("flip/input/archive-undeclared-worklog", red[0], red[1])

        # input/archive-span-row-empty: a worklog_moved row must carry every required key with a
        # valid value; an empty row is never a subset-pass.
        os.makedirs(base / "spanrow")
        spanrow = Fixture(base / "spanrow").unit()
        spanrow_path = spanrow.write()
        spanrow.bucket("2026", "schema = 1\nworklog_moved = [{}]\n")
        plain, red, _got = _vector(gate, spanrow_path, 2, "archive-unreadable",
                                   ("_require_span_row", lambda _row, _bucket, _dest: ()),
                                   lambda res: res.exit_code == 0)
        check("input/archive-span-row-empty", plain[0], plain[1])
        check("flip/input/archive-span-row-empty", red[0], red[1])

        # input/archive-span-inverted: a span whose low end is above its high end (the boundary
        # is lo <= hi, both ends inclusive; ["WL-1", "WL-1"] is the smallest valid span).
        os.makedirs(base / "spaninv")
        spaninv = Fixture(base / "spaninv").unit()
        spaninv_path = spaninv.write()
        spaninv.bucket("2026", 'schema = 1\n[[worklog_moved]]\nspan = ["WL-2", "WL-1"]\n'
                       'destination = "archive/2026/worklog.toml"\n')
        plain, red, _got = _vector(gate, spaninv_path, 2, "archive-unreadable",
                                   ("_require_span_row", lambda _row, _bucket, _dest: ()),
                                   lambda res: res.exit_code == 0)
        check("input/archive-span-inverted", plain[0], plain[1])
        check("flip/input/archive-span-inverted", red[0], red[1])

        # input/archive-span-mismatch: the bucket declares WL-5..WL-6 but its worklog.toml holds
        # WL-5 only; the declared span must match what is read.
        os.makedirs(base / "spanmis")
        spanmis = Fixture(base / "spanmis").unit()
        spanmis_path = spanmis.write()
        rotated_side = Fixture(base / "spanmis")
        rotated_side.entry("WL-5", summary="rotated bookkeeping")
        spanmis.bucket("2026", 'schema = 1\n[[worklog_moved]]\nspan = ["WL-5", "WL-6"]\n'
                       'destination = "archive/2026/worklog.toml"\n', rotated_side.entries)
        plain, red, _got = _vector(gate, spanmis_path, 2, "archive-unreadable",
                                   ("_require_span_match", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/archive-span-mismatch", plain[0], plain[1])
        check("flip/input/archive-span-mismatch", red[0], red[1])

        # input/archive-span-huge: a tiny archive.toml declaring span WL-1..WL-100000000000 is a
        # definite exit-2 refusal, never a memory crash: the span is reconciled from its two
        # bounds, never expanded. Run in a child process under a memory limit so a build that
        # expands the span dies in the child instead of this runner; the row carries no flip (the
        # arithmetic read is the property under test, not a removable guard).
        os.makedirs(base / "spanhuge")
        spanhuge = Fixture(base / "spanhuge").unit()
        spanhuge_path = spanhuge.write()
        spanhuge.bucket("2026", 'schema = 1\n[[worklog_moved]]\n'
                        'span = ["WL-1", "WL-100000000000"]\n'
                        'destination = "archive/2026/worklog.toml"\n', entries=[])
        child = ("import resource, sys, importlib.util\n"
                 "resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)\n"
                 "spec = importlib.util.spec_from_file_location('_opf_premise_gate', sys.argv[1])\n"
                 "module = importlib.util.module_from_spec(spec)\n"
                 "spec.loader.exec_module(module)\n"
                 "result = module.evaluate(sys.argv[2], sys.argv[3])\n"
                 "print(result.exit_code, result.cause)\n")
        try:
            proc = subprocess.run([sys.executable, "-I", "-B", "-c", child,
                                   str(ROOT / GATE_REL), spanhuge_path, ITEM],
                                  capture_output=True, text=True, timeout=60)
            huge_ok = proc.returncode == 0 and proc.stdout.strip() == "2 archive-unreadable"
            huge_detail = (proc.returncode, proc.stdout[-500:], proc.stderr[-500:])
        except subprocess.TimeoutExpired as exc:
            # A child that stalls past its wall-clock bound (a build that expands the span into
            # work instead of memory) fails this row, never this runner.
            huge_ok = False
            huge_detail = "evaluation child exceeded its {} s timeout".format(exc.timeout)
        check("input/archive-span-huge", huge_ok, huge_detail)

        # input/archive-span-overlap: two declared spans share WL-6; a WL number lives in exactly
        # one rotated span, so overlap is refused with its own detail (the choice the archive
        # reconciliation in opf/tools/_opf_check.py makes). Without the overlap test this fixture
        # is still refused under the same cause by the width-sum reconciliation (3 numbers read,
        # widths summing to 4), so the row asserts the overlap detail text, and the flip removes
        # only the overlap test: the surviving reconciliation then refuses with the width-sum
        # detail instead, turning the row red.
        os.makedirs(base / "spanov")
        spanov = Fixture(base / "spanov").unit()
        spanov_path = spanov.write()
        spanov_side = Fixture(base / "spanov")
        for number in (5, 6, 7):
            spanov_side.entry("WL-{}".format(number), summary="rotated bookkeeping")
        spanov.bucket("2026", 'schema = 1\n[[worklog_moved]]\nspan = ["WL-5", "WL-6"]\n'
                      'destination = "archive/2026/worklog.toml"\n[[worklog_moved]]\n'
                      'span = ["WL-6", "WL-7"]\ndestination = "archive/2026/worklog.toml"\n',
                      spanov_side.entries)

        def _read_wl_numbers(entries):
            read = set()
            for entry in entries:
                ref = (gate._opf_worklog._valid_wl_ref(entry.get("id"))
                       if isinstance(entry, dict) else None)
                if ref is not None:
                    read.add(ref[0])
            return read

        def span_match_no_overlap(declared, entries, bucket):
            # _require_span_match with only its overlap refusal removed; the membership walk and
            # the width-sum count still run.
            read = _read_wl_numbers(entries)
            spans = sorted(declared)
            position = 0
            for number in sorted(read):
                while position < len(spans) and spans[position][1] < number:
                    position += 1
                if position >= len(spans) or number < spans[position][0]:
                    raise gate.PremiseGateCannot(
                        "archive-unreadable",
                        "{}: archived worklog WL-{} is outside every declared rotated "
                        "span".format(bucket, number))
            if len(read) != sum(high - low + 1 for low, high in spans):
                raise gate.PremiseGateCannot(
                    "archive-unreadable",
                    bucket + ": the declared rotated spans do not match the WL numbers read "
                    "from its worklog.toml")

        plain, red, _got = _vector(gate, spanov_path, 2, "archive-unreadable",
                                   ("_require_span_match", span_match_no_overlap),
                                   lambda res: res.cause == "archive-unreadable"
                                   and "overlap" not in res.detail,
                                   expect_detail="overlap")
        check("input/archive-span-overlap", plain[0], plain[1])
        check("flip/input/archive-span-overlap", red[0], red[1])

        # input/archive-wl-outside-spans: the bucket worklog holds WL-5 and WL-9 while the spans
        # declare WL-5..WL-6, so the distinct-number count matches the span widths and only the
        # membership test catches WL-9 outside every declared span. The flip removes only that
        # membership test; the overlap refusal and the width-sum count still run and pass, so the
        # out-of-span WL-9 rides into history and the store is allowed.
        os.makedirs(base / "spanout")
        spanout = Fixture(base / "spanout").unit()
        spanout_path = spanout.write()
        spanout_side = Fixture(base / "spanout")
        for number in (5, 9):
            spanout_side.entry("WL-{}".format(number), summary="rotated bookkeeping")
        spanout.bucket("2026", 'schema = 1\n[[worklog_moved]]\nspan = ["WL-5", "WL-6"]\n'
                       'destination = "archive/2026/worklog.toml"\n', spanout_side.entries)

        def span_match_no_membership(declared, entries, bucket):
            # _require_span_match with only its membership test removed; the overlap refusal and
            # the width-sum count still run.
            read = _read_wl_numbers(entries)
            spans = sorted(declared)
            for previous, current in zip(spans, spans[1:]):
                if current[0] <= previous[1]:
                    raise gate.PremiseGateCannot(
                        "archive-unreadable",
                        "{}: worklog_moved spans WL-{}..WL-{} and WL-{}..WL-{} overlap".format(
                            bucket, previous[0], previous[1], current[0], current[1]))
            if len(read) != sum(high - low + 1 for low, high in spans):
                raise gate.PremiseGateCannot(
                    "archive-unreadable",
                    bucket + ": the declared rotated spans do not match the WL numbers read "
                    "from its worklog.toml")

        plain, red, _got = _vector(gate, spanout_path, 2, "archive-unreadable",
                                   ("_require_span_match", span_match_no_membership),
                                   lambda res: res.exit_code == 0)
        check("input/archive-wl-outside-spans", plain[0], plain[1])
        check("flip/input/archive-wl-outside-spans", red[0], red[1])

        # input/archive-span-foreign-destination: a well-formed span whose destination is not the
        # declaring bucket's own worklog file. The flip removes the own-destination guard.
        os.makedirs(base / "spandest")
        spandest = Fixture(base / "spandest").unit()
        spandest_path = spandest.write()
        spandest_side = Fixture(base / "spandest")
        spandest_side.entry("WL-5", summary="rotated bookkeeping")
        spandest.bucket("2026", 'schema = 1\n[[worklog_moved]]\nspan = ["WL-5", "WL-5"]\n'
                        'destination = "archive/2026/other.toml"\n', spandest_side.entries)
        plain, red, _got = _vector(gate, spandest_path, 2, "archive-unreadable",
                                   ("_require_own_destination", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/archive-span-foreign-destination", plain[0], plain[1])
        check("flip/input/archive-span-foreign-destination", red[0], red[1])

        # input/archive-span-suffixed-bound: a span bound with a suffix (WL-5.abcd) is refused,
        # never parsed down to its number. The flip replaces the bound guard with a lax parser
        # that strips the suffix.
        os.makedirs(base / "spansuf")
        spansuf = Fixture(base / "spansuf").unit()
        spansuf_path = spansuf.write()
        spansuf_side = Fixture(base / "spansuf")
        spansuf_side.entry("WL-5", summary="rotated bookkeeping")
        spansuf.bucket("2026", 'schema = 1\n[[worklog_moved]]\nspan = ["WL-5", "WL-5.abcd"]\n'
                       'destination = "archive/2026/worklog.toml"\n', spansuf_side.entries)

        def lax_bounds(bounds, _bucket):
            return tuple(int(str(bound).split("-", 1)[1].split(".", 1)[0]) for bound in bounds)

        plain, red, _got = _vector(gate, spansuf_path, 2, "archive-unreadable",
                                   ("_require_span_bounds", lax_bounds),
                                   lambda res: res.exit_code == 0)
        check("input/archive-span-suffixed-bound", plain[0], plain[1])
        check("flip/input/archive-span-suffixed-bound", red[0], red[1])

        # input/archive-spans-without-worklog: a bucket declares rotated spans but carries no
        # worklog.toml at all. The flip removes the declared/present agreement guard.
        os.makedirs(base / "spannofile")
        spannofile = Fixture(base / "spannofile").unit()
        spannofile_path = spannofile.write()
        spannofile.bucket("2026", 'schema = 1\n[[worklog_moved]]\nspan = ["WL-5", "WL-5"]\n'
                          'destination = "archive/2026/worklog.toml"\n')
        plain, red, _got = _vector(gate, spannofile_path, 2, "archive-unreadable",
                                   ("_require_declared_worklog", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/archive-spans-without-worklog", plain[0], plain[1])
        check("flip/input/archive-spans-without-worklog", red[0], red[1])

        # input/archive-stat-oserror: the archive-directory stat raises a bare OSError (the
        # contained walker's own descriptor duplication can); the evaluator maps it to the
        # archive cause instead of crashing. The flip treats the failed stat as an absent archive.
        from unittest import mock
        real_lstat = gate._journal._lstat_contained

        def lstat_oserror(fd, rel, _real=real_lstat):
            if rel.endswith("/archive"):
                raise OSError(9, "fixture dup failure")
            return _real(fd, rel)

        with mock.patch.object(gate._journal, "_lstat_contained", lstat_oserror):
            plain, red, got = _vector(gate, good_path, 2, "archive-unreadable",
                                      ("_archive_stat_failed", lambda _exc: None),
                                      lambda res: res.exit_code == 0)
        check("input/archive-stat-oserror",
              plain[0] and "fixture dup failure" in got.detail, (plain[1], got.detail))
        check("flip/input/archive-stat-oserror", red[0], red[1])

        # input/moved-record-seated-twice: a moved id seated twice at its declared destination.
        # The flip weakens the exactly-once seat to first-match.
        os.makedirs(base / "seat")
        seat = Fixture(base / "seat").unit()
        seat_path = seat.write()
        mover = Fixture(base / "seat")
        moved_row = mover.record("backlog_item", "BI-9", "active", actor="maintainer")
        seat.bucket("2026", 'schema = 1\n[[moved]]\nid = "BI-9"\ntype = "backlog_item"\n'
                    'destination = "archive/2026/backlog_item.toml"\n')
        with open(seat.machine / "archive" / "2026" / "backlog_item.toml", "w",
                  encoding="utf-8") as handle:
            handle.write(mover._record_toml([moved_row, moved_row]))
        plain, red, _got = _vector(gate, seat_path, 2, "archive-unreadable",
                                   ("_require_seated_once",
                                    lambda seated, _ident, _dest: seated[0]),
                                   lambda res: res.exit_code == 0)
        check("input/moved-record-seated-twice", plain[0], plain[1])
        check("flip/input/moved-record-seated-twice", red[0], red[1])

        # input/importer-entry-invalid: an importer-authored worklog entry must validate before
        # the firewall may hold it out of live history; a bad row is record-invalid, never a skip.
        os.makedirs(base / "impent")
        impent = Fixture(base / "impent").unit()
        impent.entry("WL-9", actor="importer", kind="no-such-kind", summary="imported history")
        plain, red, _got = _vector(gate, impent.write(), 2, "record-invalid",
                                   ("_validate_clean_record", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/importer-entry-invalid", plain[0], plain[1])
        check("flip/input/importer-entry-invalid", red[0], red[1])

        # input/importer-record-invalid: the same rule for an importer-authored index record.
        os.makedirs(base / "imprec")
        imprec = Fixture(base / "imprec").unit()
        imprec.record("finding", "FN-1", "open", actor="importer", links=[relates(ITEM)],
                      omit=("title",))
        plain, red, _got = _vector(gate, imprec.write(), 2, "record-invalid",
                                   ("_validate_clean_record", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/importer-record-invalid", plain[0], plain[1])
        check("flip/input/importer-record-invalid", red[0], red[1])

        # input/imported-row-in-clean-worklog: an envelope-valid `imported:` worklog row in the
        # clean active ledger is record-invalid: imported worklog rows live only in
        # worklog.imported.toml (spec 4.2), never a clean worklog. The flip removes the
        # misplacement refusal, so the row is validated against the imported envelope, held out
        # as history, and the store is allowed: exactly the lenient read the refusal prevents.
        os.makedirs(base / "impwl")
        impwl = Fixture(base / "impwl").unit()
        impwl_path = impwl.write()
        _append_toml_rows(Path(impwl_path) / ".working" / "toml" / "worklog.toml", "entry",
                          [imported_worklog_row()])
        plain, red, _got = _vector(gate, impwl_path, 2, "record-invalid",
                                   ("_refuse_imported_in_worklog", _noop),
                                   lambda res: res.exit_code == 0,
                                   expect_detail="in a clean worklog")
        check("input/imported-row-in-clean-worklog", plain[0], plain[1])
        check("flip/input/imported-row-in-clean-worklog", red[0], red[1])

        # input/imported-row-duplicated: the same envelope-valid imported row seated twice in the
        # clean ledger is refused at its first appearance; duplication never launders the series
        # (the imported number line carries no clean duplicate-wl seat to catch it). The flip
        # removes the misplacement refusal, and BOTH rows are validated and held out, so the
        # store is allowed.
        os.makedirs(base / "impdup")
        impdup = Fixture(base / "impdup").unit()
        impdup_path = impdup.write()
        _append_toml_rows(Path(impdup_path) / ".working" / "toml" / "worklog.toml", "entry",
                          [imported_worklog_row(), imported_worklog_row()])
        plain, red, _got = _vector(gate, impdup_path, 2, "record-invalid",
                                   ("_refuse_imported_in_worklog", _noop),
                                   lambda res: res.exit_code == 0,
                                   expect_detail="in a clean worklog")
        check("input/imported-row-duplicated", plain[0], plain[1])
        check("flip/input/imported-row-duplicated", red[0], red[1])

        # input/imported-row-registered-vendor: an envelope-valid imported row carrying an
        # x-<vendor> table the manifest registers is still record-invalid in a clean worklog.
        # The flip removes the misplacement refusal, and the row is then held out, not refused:
        # that outcome is reachable only because the imported validation receives the manifest's
        # registered vendors the way the doctor's imported read passes them, so the registered
        # extension table does not fail the envelope.
        os.makedirs(base / "impvend")
        impvend = Fixture(base / "impvend").unit()
        impvend_path = impvend.write()
        vend_manifest = Path(impvend_path) / ".working" / "toml" / "manifest.toml"
        with open(vend_manifest, "r", encoding="utf-8") as handle:
            vend_text = handle.read()
        with open(vend_manifest, "w", encoding="utf-8") as handle:
            handle.write(vend_text.replace("registered = []", 'registered = ["x-acme"]', 1))
        _append_toml_rows(Path(impvend_path) / ".working" / "toml" / "worklog.toml", "entry",
                          [imported_worklog_row(**{"x-acme": {"lane": "history"}})])
        plain, red, _got = _vector(gate, impvend_path, 2, "record-invalid",
                                   ("_refuse_imported_in_worklog", _noop),
                                   lambda res: res.exit_code == 0,
                                   expect_detail="in a clean worklog")
        check("input/imported-row-registered-vendor", plain[0], plain[1])
        check("flip/input/imported-row-registered-vendor", red[0], red[1])

        # input/imported-row-in-clean-index: an envelope-valid imported record in a clean
        # <type>.index.toml is record-invalid: imported records live only in
        # <type>.imported.index.toml (spec 4.2). The flip removes the misplacement refusal.
        os.makedirs(base / "impidx")
        impidx = Fixture(base / "impidx").unit()
        impidx_path = impidx.write()
        _append_toml_rows(Path(impidx_path) / ".working" / "toml" / "pending_decision.index.toml",
                          "record", [imported_decision_row()])
        plain, red, _got = _vector(gate, impidx_path, 2, "record-invalid",
                                   ("_refuse_imported_in_records", _noop),
                                   lambda res: res.exit_code == 0,
                                   expect_detail="imported-series id imported:PD-1")
        check("input/imported-row-in-clean-index", plain[0], plain[1])
        check("flip/input/imported-row-in-clean-index", red[0], red[1])

        # input/imported-row-in-archive: an envelope-valid imported record enumerated as moved
        # and seated once at its archive destination is record-invalid: the imported series never
        # rotates, so the record archive never holds one (spec 12). The flip removes the
        # misplacement refusal, and the archived row is validated and held out instead.
        os.makedirs(base / "imparch")
        imparch = Fixture(base / "imparch").unit()
        imparch_path = imparch.write()
        imparch.bucket("2026", 'schema = 1\n[[moved]]\nid = "imported:PD-1"\n'
                       'type = "pending_decision"\n'
                       'destination = "archive/2026/pending_decision.toml"\n')
        with open(imparch.machine / "archive" / "2026" / "pending_decision.toml", "w",
                  encoding="utf-8") as handle:
            handle.write("schema = 1\n")
        _append_toml_rows(imparch.machine / "archive" / "2026" / "pending_decision.toml",
                          "record", [imported_decision_row()])
        plain, red, _got = _vector(gate, imparch_path, 2, "record-invalid",
                                   ("_refuse_imported_in_records", _noop),
                                   lambda res: res.exit_code == 0,
                                   expect_detail="imported-series id imported:PD-1")
        check("input/imported-row-in-archive", plain[0], plain[1])
        check("flip/input/imported-row-in-archive", red[0], red[1])

        # input/worklog-suffixed-id: a suffixed clean worklog id (WL-5.abcd) is outside the
        # complete id grammar `<NS>-<n>` (spec 8.2), so the clean schema refuses the row as
        # record-invalid; it is never parsed down to its number the way the span-bound and
        # expected-head rows also refuse. The flip removes the clean validation, and the row
        # then counts as its number and the store is allowed.
        os.makedirs(base / "suffid")
        suffid = Fixture(base / "suffid").unit()
        suffid.entry("WL-5.abcd", summary="suffixed row")
        plain, red, _got = _vector(gate, suffid.write(), 2, "record-invalid",
                                   ("_validate_clean_record", _noop),
                                   lambda res: res.exit_code == 0,
                                   expect_detail="not a well-formed")
        check("input/worklog-suffixed-id", plain[0], plain[1])
        check("flip/input/worklog-suffixed-id", red[0], red[1])

        # input/worklog-not-schema-1: the active ledger parses as TOML but is not the declared
        # schema-1 {schema, entry} shape.
        os.makedirs(base / "shape")
        shape = Fixture(base / "shape").unit()
        shape_path = shape.write()
        shape_ledger = Path(shape_path) / ".working" / "toml" / "worklog.toml"
        with open(shape_ledger, "r", encoding="utf-8") as handle:
            shape_text = handle.read()
        with open(shape_ledger, "w", encoding="utf-8") as handle:
            handle.write(shape_text.replace("schema = 1\n", "schema = 2\n", 1))
        plain, red, _got = _vector(gate, shape_path, 2, "worklog-unreadable",
                                   ("_require_worklog_shape", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/worklog-not-schema-1", plain[0], plain[1])
        check("flip/input/worklog-not-schema-1", red[0], red[1])

        # input/empty-worklog: a ledger with no WL-series entry at all is unread history, never a
        # zero head. The flip grades the empty read as evaluable history.
        os.makedirs(base / "emptywl")
        emptywl = Fixture(base / "emptywl")
        emptywl.record("backlog_item", ITEM, "active", actor="maintainer")
        plain, red, _got = _vector(gate, emptywl.write(), 2, "worklog-unreadable",
                                   ("_require_wl_head", lambda seen: max(seen, default=0)),
                                   lambda res: res.cause == "no-start-entry")
        check("input/empty-worklog", plain[0], plain[1])
        check("flip/input/empty-worklog", red[0], red[1])

        # input/flow-entry-unrelated: a flow line names the item but its entry does not link
        # relates to it.
        os.makedirs(base / "unrel")
        unrel = Fixture(base / "unrel")
        unrel.record("backlog_item", ITEM, "active", actor="maintainer")
        unrel.entry("WL-1", detail="opf-record create {} open".format(ITEM), kind="added",
                    summary="created " + ITEM, links=[relates(ITEM)])
        unrel.entry("WL-2", detail=START_DETAIL)
        plain, red, _got = _vector(gate, unrel.write(), 2, "flow-line-malformed",
                                   ("_require_flow_relates", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/flow-entry-unrelated", plain[0], plain[1])
        check("flip/input/flow-entry-unrelated", red[0], red[1])

        # input/start-missing-fields: a start entry without its required tier= field.
        os.makedirs(base / "startf")
        startf = Fixture(base / "startf")
        startf.record("backlog_item", ITEM, "active", actor="maintainer")
        startf.entry("WL-1", detail="opf-record create {} open".format(ITEM), kind="added",
                     summary="created " + ITEM, links=[relates(ITEM)])
        startf.entry("WL-2", detail=START_DETAIL.replace(" tier=light", ""),
                     links=[relates(ITEM)])
        plain, red, _got = _vector(gate, startf.write(), 2, "flow-line-malformed",
                                   ("_require_start_fields", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/start-missing-fields", plain[0], plain[1])
        check("flip/input/start-missing-fields", red[0], red[1])

        # input/index-not-schema-1: a record index that is not the schema-1 {schema, record} shape.
        os.makedirs(base / "idx")
        idx = Fixture(base / "idx").unit()
        idx_path = idx.write()
        idx_index = Path(idx_path) / ".working" / "toml" / "backlog_item.index.toml"
        with open(idx_index, "r", encoding="utf-8") as handle:
            idx_text = handle.read()
        with open(idx_index, "w", encoding="utf-8") as handle:
            handle.write(idx_text.replace("schema = 1\n", "schema = 2\n", 1))
        plain, red, _got = _vector(gate, idx_path, 2, "index-unreadable",
                                   ("_require_index_shape", _noop),
                                   lambda res: res.exit_code == 0)
        check("input/index-not-schema-1", plain[0], plain[1])
        check("flip/input/index-not-schema-1", red[0], red[1])

        # input/head-not-canonical: a suffixed expected head is a usage refusal, never parsed
        # leniently down to its number.
        def lax_head(value):
            return None if value is None else int(value.split("-", 1)[1].split(".", 1)[0])

        plain, red, _got = _vector(gate, good_path, 2, "usage",
                                   ("_parse_expected_head", lax_head),
                                   lambda res: res.exit_code == 0, expect_head_wl="WL-2.abcd")
        check("input/head-not-canonical", plain[0], plain[1])
        check("flip/input/head-not-canonical", red[0], red[1])

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
    print("SELF-TEST PASS: check_premise_gate {} checks (an unresolved store, an unreadable, "
          "empty or mis-shaped worklog, an unreadable archive bucket or failed archive stat, an "
          "undeclared bucket worklog, a malformed, inverted, mismatched, overlapping, suffixed, "
          "foreign-destination or file-less rotated span, a huge span refused arithmetically in "
          "a memory-limited child, a WL number outside every declared span, a twice-seated "
          "moved record, an invalid record (importer-authored rows and a suffixed worklog id "
          "included), a misplaced imported-series row (in the clean worklog, duplicated, "
          "vendor-extended, in a clean index and in the record archive, each envelope-valid), "
          "a mis-shaped index, a duplicate WL, an unknown flow event, a malformed "
          "scope=, a verdict without rev=, an unrelated flow entry, a start entry missing its "
          "fields, a missing start entry, a missing item, a finding without its create entry, a "
          "moved or non-canonical expected head and a unit with any finding each refuse with "
          "their cause; a started unit, a baseline round, failed rounds and ignored importer "
          "flow lines each allow; every outcome row turns red under the flip that removes the "
          "guard it names, and the *-first-line and huge-span rows pin their behaviour without "
          "a flip)".format(len(EXECUTED)))
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
