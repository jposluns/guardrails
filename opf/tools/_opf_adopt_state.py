"""The homes-1 adoption control area and the bounded adoption state the doctor reads (OPF-SPEC 4.2, 11, 14.2, 17).

A store that has adopted (its `.working/imported/adoption/` home exists) registers, on homes 1, each ADMITTED
run AT FILE LEVEL: the paths its committed inventories list, plus the recorded Move destinations of its
non-occupying move rows. C-CONTAINMENT recognizes exactly those files as OPF control area; every other path
under the adoption homes, the run's own evidence bundle, its adoption archive and the Move root included, is
graded as an unregistered path (spec 4.2, 17). A run is admitted from its COMMITTED, immutable adoption
evidence ALONE, the bundle under the adoption home and the run archive, which travel with every clone; the
machine-local journal is never read, so a clone without it grades exactly as the original store (spec 4.2).
Admission requires a VALID, sealed (canonically emitted) `inventory.toml` whose rows under the bundle and
the run archive each hold the live bytes at their exact size and digest (a listed member that is absent or
differs refuses; an unlisted file under the run homes stays a graded finding), a plan that re-proves as a
frozen plan/v2 (`_opf_adopt_apply.frozen_plan`), a canonical approval that binds it, a plan run id equal to
the bundle name (every record is bound to the run id of the directory it sits in; an inventory claiming
another run's paths is refused by the shared row validator), and a plan store identity equal to this
in-repo store. The whole proof detects ACCIDENTS, an interrupted apply, a hand edit, a misplaced or stale
record, never deliberate forgery: a crafted self-consistent bundle is outside the accident-detection model,
consistent with the rest of OPF (spec 4.2).

The frozen retire, move and migrate sources under `.working/` of each admitted run (the non-occupying source
rows of its frozen plan) are bounded adoption state until their retirement is recorded (spec 11, 14.2): a
source whose live bytes still match its plan digest is `bounded` (reported as migration_incomplete, never
failed), one whose bytes differ is `drifted`, and one that is gone is `absent`; both of the latter fail at
required AND stay reported as migration_incomplete, because the approved source remains unresolved (spec 11).
A retirement is recorded only in committed evidence: a live, VALID, sealed retirement inventory consistent
with the plan (it records the retirement preimage of every non-occupying retire and move row at the plan
digest, and each preimage it adds holds exactly those bytes in the run archive) and consistent with the base
inventory (no row contradicts a base row, and a row neither record explains refuses). Any other retirement
evidence is CANNOT-EVALUATE, never read as retired. A recorded retirement ends the bounded state of the
retire and move rows only. A migrate source retires through its import, which a later activation reads, so
it stays bounded until then.

Read-only and bounded by construction: the reader opens no journal and writes nothing. Rows under the shared
Move root are registered by name only and their bytes are NOT verified here (the section 14.1 completion
checks are the intended verifier). Every file read is contained, no-follow, single-link and bounded by the
16 MiB per-file read cap (`_opf_adopt_apply._read_live`); EVERY byte read counts against the READ_CEILING
whole-call byte budget, an exhausted budget refuses the next read before it happens, and one adoption_state
call additionally refuses past RUN_CEILING run directories or SOURCE_CEILING frozen sources
(CANNOT-EVALUATE, fail-closed). A store without the adoption home costs exactly one lstat and keeps the
legacy grading unchanged. C-EVIDENCE-ENUM stays inactive on homes 1.

Offline, stdlib only, fail-closed. It lives under `opf/tools/` and imports ONLY sibling `opf/tools/`
modules, so the standalone-closure property holds.

Exit convention (the repo gates and the sibling OPF units): 0 clean, 1 a finding, 2 cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: _opf_adopt_state.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import collections
import stat
import tomllib
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal                   # noqa: E402
import _opf_adopt_apply as apply  # noqa: E402
import _opf_store as store        # noqa: E402

# The adoption homes, each derived from the public homes constructors (pinned by the self-test).
ADOPTION_HOME = "{}/{}".format(store.IMPORTED_REL, apply.KIND)
ARCHIVE_ADOPTION = "{}/{}".format(store.ARCHIVE_REL, apply.KIND)
MOVED_ROOT = apply._MOVED_ROOT
# The control-area ancestors C-CONTAINMENT walks as managed namespaces while a store adopts, so that every
# entry beneath them other than a registered file is still graded.
ADOPTION_NAMESPACES = (store.IMPORTED_REL, ADOPTION_HOME, store.ARCHIVE_REL, ARCHIVE_ADOPTION)
# The roots an [unmanaged] declaration may not equal, contain or lie within while the store adopts (spec 14.2).
CONTROL_ROOTS = (store.IMPORTED_REL, store.ARCHIVE_REL)

# The phase of the retirement-stage inventory. PENDING PIN: this literal is the fallback until the
# retirement-stage constant `_opf_adopt_apply.RETIREMENT_PHASE` lands; the self-test vector
# `retirement-phase-pinned-to-apply-constant` then requires the two to be equal.
RETIREMENT_PHASE = "retirement"

FROZEN_DISPOSITIONS = ("retire", "move", "migrate")
# The dispositions whose bounded state the recorded retirement ends (a migrate source retires by import).
RETIRED_BY_RETIREMENT = ("retire", "move")

# Fail-closed ceilings of ONE adoption_state call (an adversarial tree cannot make the doctor read without
# bound): run directories read, frozen sources graded, and total bytes read through the contained reader.
RUN_CEILING = 128
SOURCE_CEILING = 4096
READ_CEILING = 64 * 1024 * 1024

FrozenSource = collections.namedtuple("FrozenSource", ("digest", "run_id", "disposition", "grade", "detail"))
AdoptionState = collections.namedtuple(
    "AdoptionState", ("adopting", "runs", "registered", "frozen", "findings", "cannot"))
NOT_ADOPTING = AdoptionState(False, (), (), types.MappingProxyType({}), (), ())

_PREFIX = "C-CONTAINMENT: "


def _state(runs, registered, frozen, findings, cannot):
    return AdoptionState(True, tuple(runs), tuple(registered), types.MappingProxyType(dict(frozen)),
                         tuple(findings), tuple(cannot))


def _toml(data, rel):
    """Parse bytes read through _read_live as TOML, or raise AdoptApplyError naming the path."""
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as exc:   # UnicodeDecodeError and TOMLDecodeError included
        raise apply.AdoptApplyError("{!r} is unreadable or malformed TOML ({})".format(rel, exc))


def _list(root_fd, rel):
    import _opf_check
    try:
        return _opf_check._list_contained(root_fd, rel)
    except store.StoreError as exc:
        raise apply.AdoptApplyError("cannot list {!r} ({})".format(rel, exc))


def _read_budgeted(root_fd, rel, budget):
    """apply._read_live under the whole-reader byte budget: EVERY byte the adoption state reads counts
    against READ_CEILING, and an exhausted budget refuses BEFORE the next read (fail-closed), so no tree
    makes the doctor read without bound and nothing is read past exhaustion (at most the one crossing
    read, itself under the per-file cap, exceeds the ceiling)."""
    if budget[0] < 0:
        raise apply.AdoptApplyError("the adoption-state read ceiling of {} bytes is exhausted; {!r} is "
                                    "not read (fail-closed)".format(READ_CEILING, rel))
    fst, data = apply._read_live(root_fd, rel)
    if data is not None:
        budget[0] -= len(data)
        if budget[0] < 0:
            raise apply.AdoptApplyError("reading {!r} exceeds the adoption-state read ceiling of {} bytes; "
                                        "fail-closed".format(rel, READ_CEILING))
    return fst, data


def adoption_state(root_fd, machine_rel, *, in_repo):
    """The adoption state of the store beneath root_fd (an AdoptionState). Read-only; no journal is
    opened, so a clone without the machine-local journal grades exactly as the original store (spec 4.2).

    `.working/imported/adoption` absent: NOT_ADOPTING after exactly one lstat, so the legacy grading runs
    unchanged. Present: every run-id child is admitted or refused (_admit_run); an admitted run contributes
    the file paths its proven inventories list (plus its recorded Move destinations) to `registered` and its
    frozen sources to `frozen`, each graded against its plan digest. A refused run record is a finding (the
    run homes are then graded as unregistered paths); an input that cannot be read, a plan that does not
    describe this store, or an exceeded ceiling is a cannot-evaluate."""
    try:
        st = _journal._lstat_contained(root_fd, ADOPTION_HOME)
    except (_journal.JournalError, OSError) as exc:
        return _state((), (), {}, (), [_PREFIX + "cannot observe the adoption home {!r} ({}); fail-closed".format(
            ADOPTION_HOME, exc)])
    if st is None:
        return NOT_ADOPTING
    if not stat.S_ISDIR(st.st_mode):
        return _state((), (), {}, (), [_PREFIX + "the adoption home {!r} is present but is not a directory (a "
                                       "symlink or file); fail-closed".format(ADOPTION_HOME)])
    try:
        subdirs, _files = _list(root_fd, ADOPTION_HOME)
    except apply.AdoptApplyError as exc:
        return _state((), (), {}, (), [_PREFIX + "{}; fail-closed".format(exc)])
    # A file or a non-run-id child is never admitted; the containment walk grades it as unregistered.
    run_names = [name for name in subdirs or () if apply.is_run_id(name)]
    if len(run_names) > RUN_CEILING:
        return _state((), (), {}, (), [_PREFIX + "the adoption home {!r} holds {} run directories, over the "
                                       "adoption-state ceiling of {}; fail-closed".format(
                                           ADOPTION_HOME, len(run_names), RUN_CEILING)])
    budget = [READ_CEILING]
    findings, cannot, runs, plans, rows, registered = [], [], [], {}, {}, []
    for name in run_names:
        plan_doc, run_rows, run_registered, run_findings, run_cannot = _admit_run(
            root_fd, name, machine_rel, in_repo, budget)
        findings.extend(run_findings)
        cannot.extend(run_cannot)
        if plan_doc is not None:
            runs.append(name)
            plans[name] = plan_doc
            rows[name] = run_rows
            registered.extend(run_registered)
    claims = {}
    for run_id in runs:
        registered.extend(move_destinations(plans[run_id]))
        try:
            retired, retired_registered = _retired(root_fd, run_id, plans[run_id], rows[run_id], budget)
        except apply.AdoptApplyError as exc:
            # Whether this run retired cannot be told, so none of its sources is bounded (each then grades as
            # an unregistered path) and the report cannot evaluate.
            cannot.append(_PREFIX + "adoption run {}: the retirement state cannot be evaluated ({}); "
                          "fail-closed".format(run_id, exc))
            continue
        registered.extend(retired_registered)
        for path, (hexdigest, disposition) in frozen_sources(plans[run_id], retired).items():
            claims.setdefault(path, []).append((run_id, hexdigest, disposition))
    if len(claims) > SOURCE_CEILING:
        cannot.append(_PREFIX + "the admitted adoption runs claim {} frozen sources, over the "
                      "adoption-state ceiling of {}; no source is graded (fail-closed)".format(
                          len(claims), SOURCE_CEILING))
        claims = {}
    frozen = {}
    for path in sorted(claims):
        if len(claims[path]) > 1:
            cannot.append(_PREFIX + "{!r} is a frozen source of more than one unretired adoption run ({}); "
                          "the bounded state is ambiguous (fail-closed)".format(
                              path, ", ".join(c[0] for c in claims[path])))
            continue
        run_id, hexdigest, disposition = claims[path][0]
        try:
            grade, detail = grade_frozen(root_fd, path, hexdigest, budget)
        except apply.AdoptApplyError as exc:
            cannot.append(_PREFIX + "frozen {} source {!r} of adoption run {} cannot be graded ({}); "
                          "fail-closed".format(disposition, path, run_id, exc))
            grade, detail = "cannot", str(exc)
        frozen[path] = FrozenSource(hexdigest, run_id, disposition, grade, detail)
    return _state(runs, sorted(set(registered)), frozen, findings, cannot)


def _admit_run(root_fd, run_id, machine_rel, in_repo, budget):
    """(plan_doc or None, inventory rows by path, registered, findings, cannot) for one adoption run
    bundle. The run is admitted only from its committed evidence, the bundle and the run archive re-proving
    the sealed inventory (see the module introduction): a refused record is a finding naming the reason; an
    unreadable or malformed input, and a plan that describes another store, are cannot-evaluate.
    `registered` is the file paths the proven inventory lists, plus the inventory itself; never a
    directory, never a whole tree."""
    findings, cannot = [], []
    bundle = apply.evidence_home_rel(run_id)
    tail = "; the run is not admitted, so its homes are graded as unregistered paths"

    def refuse(reason):
        findings.append(_PREFIX + "adoption run {} is not admitted: {}{}".format(run_id, reason, tail))
        return None, {}, (), findings, cannot

    def cant(reason):
        cannot.append(_PREFIX + "adoption run {} cannot be evaluated: {}{} (fail-closed)".format(
            run_id, reason, tail))
        return None, {}, (), findings, cannot

    inv_rel = apply.inventory_rel(run_id)
    try:
        fst, data = _read_budgeted(root_fd, inv_rel, budget)
    except apply.AdoptApplyError as exc:
        return cant(str(exc))
    if fst is None:
        try:
            _subdirs, files = _list(root_fd, bundle)
        except apply.AdoptApplyError as exc:
            return cant(str(exc))
        phases = sorted(f for f in files or () if store.is_evidence_inventory_name(f))
        if phases:
            return cant("phase inventory {} without inventory.toml (spec 4.2)".format(", ".join(phases)))
        return refuse("{!r} is absent".format(inv_rel))
    try:
        doc = _toml(data, inv_rel)
    except apply.AdoptApplyError as exc:
        return cant(str(exc))
    graded = apply.validate_inventory(doc, run_id)
    if graded.status == store.CANNOT_EVALUATE:
        return cant("{!r}: {}".format(inv_rel, "; ".join(graded.findings)))
    if graded.status != store.VALID:
        return refuse("{!r}: {}".format(inv_rel, "; ".join(graded.findings)))
    # The sealed record (spec 4.2): only the canonical emission of its own rows is the committed inventory;
    # appended, reordered or hand-edited bytes refuse. This detects accidents, not deliberate forgery.
    try:
        sealed = apply.emit_inventory(run_id, [dict(row) for row in doc["file"]])
    except apply.AdoptApplyError as exc:
        return cant(str(exc))
    if sealed != data:
        return refuse("its inventory.toml is not the sealed canonical inventory of its own rows (a hand "
                      "edit, a reordering or stale bytes)")
    rows = dict((row["path"], row) for row in doc["file"])
    blobs = {}
    for name in (apply.PLAN_NAME, apply.APPROVAL_NAME):
        rel = "{}/{}".format(bundle, name)
        row = rows.get(rel)
        if row is None:
            return refuse("{!r} is not listed in its inventory.toml".format(rel))
        try:
            fst, data = _read_budgeted(root_fd, rel, budget)
        except apply.AdoptApplyError as exc:
            return cant(str(exc))
        if fst is None:
            return refuse("{!r} is listed in its inventory.toml but absent".format(rel))
        if len(data) != row["size"] or apply._sha256(data) != row["sha256"]:
            return refuse("{!r} does not match its inventory.toml row (a tampered or stale record)".format(rel))
        try:
            _toml(data, rel)
        except apply.AdoptApplyError as exc:
            return cant(str(exc))
        blobs[name] = data
    # The run archive and every other bundle member match the inventory (committed evidence, spec 4.2):
    # each listed row under the bundle or the run archive holds the live bytes at its recorded size and
    # digest; a listed member that is absent or differs refuses. A row under the shared Move root stays
    # registered by name only (the module introduction).
    arch = apply._archive_root(run_id) + "/"
    read_above = {"{}/{}".format(bundle, apply.PLAN_NAME), "{}/{}".format(bundle, apply.APPROVAL_NAME)}
    for rel in sorted(rows):
        if rel in read_above or not (rel.startswith(bundle + "/") or rel.startswith(arch)):
            continue
        row = rows[rel]
        try:
            fst, data = _read_budgeted(root_fd, rel, budget)
        except apply.AdoptApplyError as exc:
            return cant(str(exc))
        if fst is None:
            return refuse("{!r} is listed in its inventory.toml but absent".format(rel))
        if len(data) != row["size"] or apply._sha256(data) != row["sha256"]:
            return refuse("{!r} does not match its inventory.toml row (a tampered or stale record)".format(rel))
    try:
        plan_doc = apply.frozen_plan(blobs[apply.PLAN_NAME])
        approval = apply._canonical_toml(blobs[apply.APPROVAL_NAME], "the approval")
    except apply.AdoptApplyError as exc:
        return refuse(str(exc))
    bound = apply.approval_findings(approval, plan_doc)
    if bound:
        return refuse("its approval does not bind its plan: {}".format("; ".join(bound)))
    if plan_doc.get("run_id") != run_id:
        return refuse("its plan names run {!r}, not the bundle run (a misplaced or stale record)".format(
            plan_doc.get("run_id")))
    ident = plan_doc.get("store")
    ident = ident if isinstance(ident, dict) else {}
    if in_repo is not True or ident.get("store_root") != "." or ident.get("machine_rel") != machine_rel:
        return cant("its plan describes the store at root {!r} with machine store {!r}, not this store "
                    "(machine store {!r}, in-repo {}): the plan does not describe this store".format(
                        ident.get("store_root"), ident.get("machine_rel"), machine_rel, in_repo))
    return plan_doc, rows, [inv_rel] + sorted(rows), findings, cannot


def _retired(root_fd, run_id, plan_doc, base_rows, budget):
    """(recorded, registered): whether the run's retirement is recorded, and the file paths its retirement
    inventory registers, decided from COMMITTED evidence alone (never the journal). Recorded ONLY when a
    live, VALID, sealed `inventory-retirement.toml` is consistent with the plan (it records the retirement
    preimage of every non-occupying retire and move row at the plan digest, and each preimage it adds holds
    exactly those bytes in the run archive) and with the base inventory (no row contradicts a base row, and
    a row neither record explains refuses). An absent marker is not retired. Every other state raises
    AdoptApplyError: whether the run retired is then unknown (CANNOT-EVALUATE), never read as retired."""
    rel = apply.inventory_rel(run_id, RETIREMENT_PHASE)
    fst, data = _read_budgeted(root_fd, rel, budget)
    if fst is None:
        return False, []
    doc = _toml(data, rel)
    graded = apply.validate_inventory(doc, run_id)
    if graded.status != store.VALID:
        raise apply.AdoptApplyError("{!r} is not a VALID inventory ({})".format(rel, "; ".join(graded.findings)))
    if apply.emit_inventory(run_id, [dict(row) for row in doc["file"]]) != data:
        raise apply.AdoptApplyError("{!r} is not the sealed canonical inventory of its own rows (a hand "
                                    "edit, a reordering or stale bytes); whether the run retired is "
                                    "unknown".format(rel))
    # The plan consistency half: the marker records the retirement preimage of EVERY non-occupying retire
    # and move row at its plan digest, so a drifted source is never silently read as retired.
    preimages, moved_rows = {}, {}
    for row in plan_doc.get("sources", ()):
        if row.get("disposition") not in RETIRED_BY_RETIREMENT or row.get("occupying") is not False:
            continue
        path = row.get("path")
        if not (isinstance(path, str) and path.startswith(store.WORKING_DIRNAME + "/")):
            continue
        hexdigest = apply._plan_hex(row.get("digest"))
        preimages[apply.archive_rel(run_id, path)] = hexdigest
        dest = row.get("preservation")
        if row.get("disposition") == "move" and isinstance(dest, str):
            moved_rows[dest] = hexdigest
    marker_rows = dict((row["path"], row) for row in doc["file"])
    for pre in sorted(preimages):
        row = marker_rows.get(pre)
        if row is None or row["sha256"] != preimages[pre]:
            raise apply.AdoptApplyError("{!r} does not record the retirement preimage {!r} at its plan "
                                        "digest, so it is not consistent with the plan's retire and move "
                                        "rows (spec 14.2); whether the run retired is unknown".format(rel, pre))
    # The base consistency half: no marker row contradicts a base inventory row, and a row in neither
    # record, other than a plan-recorded preimage or Move destination, refuses.
    for path in sorted(marker_rows):
        row, base = marker_rows[path], base_rows.get(path)
        if base is not None:
            if row["size"] != base["size"] or row["sha256"] != base["sha256"]:
                raise apply.AdoptApplyError("{!r} contradicts the base inventory row of {!r}; whether the "
                                            "run retired is unknown".format(rel, path))
            continue
        if preimages.get(path) == row["sha256"] or moved_rows.get(path) == row["sha256"]:
            continue
        raise apply.AdoptApplyError("{!r} lists {!r}, which neither the base inventory nor the plan's "
                                    "retire and move rows record; whether the run retired is "
                                    "unknown".format(rel, path))
    # The preimages the marker adds are live committed evidence in the run archive, byte for byte.
    for path in sorted(marker_rows):
        if path in base_rows or not path.startswith(apply._archive_root(run_id) + "/"):
            continue
        fst, live = _read_budgeted(root_fd, path, budget)
        if fst is None:
            raise apply.AdoptApplyError("the retirement preimage {!r} its retirement inventory records is "
                                        "absent; whether the run retired is unknown".format(path))
        if len(live) != marker_rows[path]["size"] or apply._sha256(live) != marker_rows[path]["sha256"]:
            raise apply.AdoptApplyError("the retirement preimage {!r} does not match its retirement "
                                        "inventory row; whether the run retired is unknown".format(path))
    return True, [rel] + [row["path"] for row in doc["file"]]


def frozen_sources(plan_doc, retired):
    """{path: (sha256 hex, disposition)} of the frozen retire, move and migrate sources under `.working/` of
    one admitted plan: its non-occupying rows (an occupying source is archived and removed at apply). A keep
    row is never frozen; it needs its own [unmanaged] registration. Once the retirement is recorded the
    retire and move rows leave the set; a migrate row stays until its import retires it."""
    out = {}
    for row in plan_doc.get("sources", ()):
        disposition = row.get("disposition")
        if disposition not in FROZEN_DISPOSITIONS or row.get("occupying") is not False:
            continue
        if retired and disposition in RETIRED_BY_RETIREMENT:
            continue
        path = row.get("path")
        if isinstance(path, str) and path.startswith(store.WORKING_DIRNAME + "/"):
            out[path] = (apply._plan_hex(row.get("digest")), disposition)
    return out


def move_destinations(plan_doc):
    """The recorded Move destinations of one admitted plan's non-occupying move rows: their `preservation`
    paths beneath the Move root, registered at file level beside the inventory-listed paths. Any other
    spelling registers nothing (fail-closed: the path is then graded as unregistered)."""
    out = []
    for row in plan_doc.get("sources", ()):
        if row.get("disposition") != "move" or row.get("occupying") is not False:
            continue
        dest = row.get("preservation")
        if isinstance(dest, str) and dest.startswith(MOVED_ROOT + "/"):
            out.append(dest)
    return out


def grade_frozen(root_fd, path, hexdigest, budget=None):
    """(grade, detail) of one frozen source: `bounded` (live bytes equal the plan digest), `drifted` or
    `absent`. A symlink, special file, multiply-linked file, a file over the read cap, or an exhausted
    read budget raises AdoptApplyError (cannot-evaluate), never a guess."""
    if budget is None:
        budget = [READ_CEILING]
    fst, data = _read_budgeted(root_fd, path, budget)
    if fst is None:
        return "absent", "it is absent before its retirement is recorded (a vanished frozen source)"
    if apply._sha256(data) != hexdigest:
        return "drifted", ("its live bytes no longer match its plan digest (a drifted source; a fresh plan is "
                           "the remedy)")
    return "bounded", "its live bytes still match its plan digest"


def self_test():
    """Fail-closed vectors over throwaway temporary product trees: the adoption bundles carry plans the real
    planner froze and approvals the real capture produced, and every verdict is read from the C-CONTAINMENT
    report the doctor itself builds. Every write lands under its own TemporaryDirectory."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("OPF-ADOPT-STATE SELF-TEST: containment unavailable ({}); cannot evaluate".format(exc),
              file=sys.stderr)
        return 2
    try:
        return _self_test_checks()
    except Exception as exc:  # noqa: BLE001  final fail-closed backstop, never an uncaught exit-1 escape
        print("OPF-ADOPT-STATE SELF-TEST: harness error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return 2


def _self_test_checks():
    import copy
    import datetime
    import hashlib
    import os
    import shutil
    import subprocess
    import tempfile
    from unittest import mock
    import _opf_adopt as schema
    import _opf_adopt_plan as planner
    import _opf_adopt_state as reader   # the copy _opf_check imports (this file may run as __main__)
    import _opf_check
    import _opf_init

    failures = []
    checked = [0]

    def check(name, cond):
        checked[0] += 1
        if not cond:
            failures.append(name)

    now = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
    mrel = ".working/toml"
    old, note, mig, keep = ".working/OLD.md", ".working/notes/old.md", ".working/MIG.md", ".working/KEEP.md"
    move = ".working/MOVE.md"
    sources = {old: b"old rules\n", note: b"old note\n", mig: b"to migrate\n", keep: b"kept\n",
               move: b"to move\n"}
    decisions = {old: "retire", note: "retire", mig: "migrate", keep: "keep", move: "move"}
    moved = MOVED_ROOT + "/legacy/moved.md"
    manifest_text = _opf_init.build_manifest()
    legacy_manifest = tomllib.loads(manifest_text)
    manifest = dict(copy.deepcopy(legacy_manifest), unmanaged=dict(paths=[keep]))
    unregistered = ("C-CONTAINMENT: unregistered path {!r} at the store location is neither OPF-managed nor "
                    "enumerated as unmanaged (spec 14.2/11)")

    def digest(data):
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def write(root, rel, data):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def freeze(root, head, nonce):
        """(run id, plan bytes, approval bytes) from the real planner and approval capture, or None."""
        bindings = dict(schema.canonical_plan_bindings(), revision=head)
        views = sorted(v["target"] for v in legacy_manifest["views"].values())
        rows = [dict(op="init-store", store_root=".", members=[dict(
                    path=mrel + "/manifest.toml", digest=digest(manifest_text.encode("utf-8")))]),
                dict(op="render-views", store_root=".",
                     members=[dict(path=v, digest=digest(v.encode("utf-8"))) for v in views]),
                schema.enforcement_install_op(bindings["enforcement"])]
        sheet = dict(sources=sorted(sources), targets=[".opf/hooks/pre-commit"], product="opf",
                     decisions=[dict(path=p, disposition=d, actor="fixture") for p, d in sorted(decisions.items())],
                     ops=rows, bindings=bindings)
        obs = planner.investigate(root, sources=sheet["sources"], targets=sheet["targets"])
        if obs.status != store.VALID:
            return None
        sheet["expected_observation_digest"] = tomllib.loads(obs.observation.decode("utf-8"))["observation_digest"]
        res = planner.plan(root, now=now, run_nonce=nonce, **copy.deepcopy(sheet))
        if res.status != store.VALID:
            return None
        approval = apply.capture_approval(root, res.plan, sheet, "adopter", now)
        return tomllib.loads(res.plan.decode("utf-8"))["run_id"], res.plan, approval

    with tempfile.TemporaryDirectory(prefix="opf-adopt-state-") as temp:
        base = Path(temp).resolve()
        plan_root = base / "plan"
        for rel, data in sources.items():
            write(plan_root, rel, data)
        head = apply._selftest_git_commit(plan_root)
        frozen_a = freeze(plan_root, head, "0123456789abcdef") if head else None
        frozen_b = freeze(plan_root, head, "fedcba9876543210") if head else None
        check("fixture-plans-frozen-by-the-real-planner", frozen_a is not None and frozen_b is not None)
        if frozen_a is None or frozen_b is None:
            print("OPF-ADOPT-STATE SELF-TEST: the planner fixture cannot be built; cannot evaluate", file=sys.stderr)
            return 2
        runs = {frozen_a[0]: frozen_a[1:], frozen_b[0]: frozen_b[1:]}
        rid, rid_b = frozen_a[0], frozen_b[0]
        home = apply.evidence_home_rel(rid)
        plan_rel, approval_rel = home + "/" + apply.PLAN_NAME, home + "/" + apply.APPROVAL_NAME
        imp_run = "imp-20260917T120000Z-0123456789abcdef"
        seq = [0]

        def listed(rid_):
            h = apply.evidence_home_rel(rid_)
            return [h + "/" + apply.PLAN_NAME, h + "/" + apply.APPROVAL_NAME, apply.archive_rel(rid_, mig), moved]

        def commit(root, rid_, phase=None, complete=True):
            """(Re)record the (run, phase) adoption transaction in the machine-local journal, exactly as
            a real apply leaves it. The doctor never reads it (committed evidence only); the fixtures keep
            it so the clone vectors can remove it and prove identical grading. complete=False leaves the
            transaction OPEN (an interrupted apply)."""
            txn = rid_ if phase is None else "{}.{}".format(rid_, phase)
            txn_dir = root / apply.JOURNAL_REL / txn
            if txn_dir.exists():
                shutil.rmtree(txn_dir)
            txn_dir.mkdir(parents=True, exist_ok=True)
            inv = root / apply.inventory_rel(rid_, phase)
            fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                jr = _journal.open_journal_root_fd(fd, apply.JOURNAL_REL)
                try:
                    _journal._create_frames_excl(jr, txn)
                    _journal.publish(jr, txn, _journal.F_INTENT, dict(txn=txn, ops=[dict(
                        op="create", path=apply.inventory_rel(rid_, phase),
                        poststate={"content-sha256": hashlib.sha256(inv.read_bytes()).hexdigest()})]))
                    if complete:
                        _journal.publish(jr, txn, _journal.F_COMPLETE, dict(txn=txn))
                finally:
                    _journal._close_fd_quietly(jr)
            finally:
                os.close(fd)

        def relist(root, rid_, paths=None):
            rows = [apply.inventory_row(rel, (root / rel).read_bytes()) for rel in (listed(rid_) if paths is None
                                                                                     else paths)]
            write(root, apply.inventory_rel(rid_), apply.emit_inventory(rid_, rows))
            commit(root, rid_)

        def bundle(root, rid_, plan_bytes=None, approval_bytes=None):
            h = apply.evidence_home_rel(rid_)
            write(root, h + "/" + apply.PLAN_NAME, plan_bytes if plan_bytes is not None else runs[rid_][0])
            write(root, h + "/" + apply.APPROVAL_NAME, approval_bytes if approval_bytes is not None else runs[rid_][1])
            write(root, apply.archive_rel(rid_, mig), sources[mig])
            write(root, moved, b"moved\n")
            relist(root, rid_)

        def retire(root, rid_, dispositions=(old, note, move), extra_rows=None):
            """Record the run's retirement in committed evidence: the retirement preimage of each retire
            and move row in the run archive, and the sealed retirement inventory listing them at the plan
            digests, plus the journal transaction a real apply would also leave (never read)."""
            rows = []
            for src in dispositions:
                pre = apply.archive_rel(rid_, src)
                write(root, pre, sources[src])
                rows.append(apply.inventory_row(pre, sources[src]))
            rows.extend(extra_rows or [])
            write(root, apply.inventory_rel(rid_, RETIREMENT_PHASE), apply.emit_inventory(rid_, rows))
            commit(root, rid_, RETIREMENT_PHASE)

        def tree(adopted=(None,)):
            seq[0] += 1
            root = base / "t{}".format(seq[0])
            write(root, mrel + "/manifest.toml", manifest_text.encode("utf-8"))
            for rel, data in sources.items():
                write(root, rel, data)
            for rid_ in adopted:
                bundle(root, rid if rid_ is None else rid_)
            return root

        def contain(root, status="none", model=None, in_repo=True):
            rep = _opf_check._Report()
            rep.ran("C-CONTAINMENT")
            fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                _opf_check._check_containment(fd, mrel, manifest if model is None else model, status, rep,
                                              in_repo=in_repo)
            finally:
                os.close(fd)
            return rep

        def named(msgs, path, *words):
            return any(repr(path) in m and all(w in m for w in words) for m in msgs)

        def bounded(rep):
            return sorted(p for p in sources if named(rep.migration_incomplete, p))

        def snapshot(root):
            return dict((str(p.relative_to(root)), p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                        for p in sorted(root.rglob("*")))

        # Homes pins: every adoption home is the public constructor spelling, never re-spelled here.
        check("homes-derived-from-constructors", home.rsplit("/", 1)[0] == ADOPTION_HOME
              and apply._archive_root(rid).rsplit("/", 1)[0] == ARCHIVE_ADOPTION
              and store.moved_dest("x").rsplit("/", 1)[0] == MOVED_ROOT)
        # PENDING PIN: when `_opf_adopt_apply.RETIREMENT_PHASE` lands, the fallback literal must equal it.
        check("retirement-phase-pinned-to-apply-constant",
              RETIREMENT_PHASE == getattr(apply, "RETIREMENT_PHASE", RETIREMENT_PHASE)
              and apply.inventory_rel(rid, RETIREMENT_PHASE) == home + "/inventory-retirement.toml")

        # S0 legacy: no adoption home -> one lstat, no bundle read, the legacy findings and residuals exactly.
        root = tree(adopted=())
        write(root, ".working/archive/stray.md", b"x\n")
        write(root, ".working/imported/x.txt", b"x\n")
        seen, reads = [], []
        lstat, read_live = _journal._lstat_contained, apply._read_live
        with mock.patch.object(_journal, "_lstat_contained", lambda fd, rel: seen.append(rel) or lstat(fd, rel)), \
                mock.patch.object(apply, "_read_live", lambda fd, rel: reads.append(rel) or read_live(fd, rel)):
            rep = contain(root)
        legacy = sorted((".working/MIG.md", ".working/MOVE.md", ".working/OLD.md", ".working/archive",
                         ".working/imported", ".working/notes"))
        check("legacy-not-adopting-one-lstat-no-read", seen == [ADOPTION_HOME] and reads == [])
        check("legacy-findings-unchanged", rep.findings == [unregistered.format(p) for p in legacy]
              and rep.cannot == [] and rep.triage == [])
        check("legacy-residuals-unchanged", rep.residuals == list(_opf_check._RESIDUALS))
        check("legacy-no-migration-incomplete", rep.migration_incomplete == [])

        # S2 + S4: an admitted run registers its bundle, archive and Move root; its frozen sources are bounded.
        root = tree()
        rep = contain(root)
        check("admitted-run-registered", rep.findings == [] and rep.cannot == [])
        check("frozen-sources-bounded", bounded(rep) == sorted((old, note, mig, move)) and not named(
            rep.migration_incomplete, keep))
        check("adopting-residual-disclosed", rep.residuals == list(_opf_check._RESIDUALS)
              + list(_opf_check._ADOPTING_RESIDUALS))
        result = rep.result()
        check("store-validation-carries-migration-incomplete",
              result.migration_incomplete == rep.migration_incomplete and len(result.migration_incomplete) == 4)
        # bounded at a clean start as much as at a substantiated partial (spec 11). In an ADOPTING store the
        # partial triage downgrade does not apply (ruling R-a; QA round 1): every unenumerated path stays a
        # finding at required; the legacy triage posture is for legacy (non-adopting) stores only.
        imp_rel = ".working/imports/{}/plan.toml".format(imp_run)
        write(root, imp_rel, b"x = 1\n")
        write(root, ".working/OTHER.md", b"x\n")
        rep = contain(root, status="partial")
        check("bounded-under-partial", bounded(rep) == sorted((old, note, mig, move))
              and not any(named(rep.triage, p) for p in sources))
        check("adopting-partial-strays-still-findings", rep.triage == []
              and named(rep.findings, ".working/OTHER.md", "unregistered")
              and named(rep.findings, imp_rel, "unregistered"))

        # S2b (QA round 1): registration is at FILE level only; a stray beside the registered files under
        # the bundle, the adoption archive and the Move root is graded, never silently covered.
        root = tree()
        write(root, home + "/stray.md", b"x\n")
        write(root, apply._archive_root(rid) + "/stray.md", b"x\n")
        write(root, MOVED_ROOT + "/hide/evil.sh", b"evil\n")
        rep = contain(root)
        check("admitted-homes-strays-graded", named(rep.findings, home + "/stray.md", "unregistered")
              and named(rep.findings, apply._archive_root(rid) + "/stray.md", "unregistered")
              and named(rep.findings, MOVED_ROOT + "/hide", "unregistered") and len(rep.findings) == 3
              and rep.cannot == [])

        # S3: every other path under the control area is still graded.
        root = tree()
        strays = (".working/imported/x.txt", ".working/archive/stray.md", ".working/imported/adoption/junk",
                  ".working/imported/import/{}/f.toml".format(imp_run),
                  apply.archive_rel(rid_b, "f.md"), ".working/imported/adoption/not-a-run/f.md")
        for rel in strays:
            write(root, rel, b"x\n")
        rep = contain(root)
        graded = (".working/imported/x.txt", ".working/archive/stray.md", ".working/imported/adoption/junk",
                  ".working/imported/import", apply._archive_root(rid_b), ".working/imported/adoption/not-a-run")
        check("control-area-strays-graded", all(named(rep.findings, p, "unregistered") for p in graded)
              and len(rep.findings) == len(graded))

        # S4b: a sibling of a frozen source under its ancestor directory is graded one by one.
        root = tree()
        write(root, ".working/notes/stray.md", b"x\n")
        rep = contain(root)
        check("ancestor-sibling-graded", rep.findings == [unregistered.format(".working/notes/stray.md")]
              and named(rep.migration_incomplete, note))

        # S5: drift is a finding naming the path; S5b: even under a substantiated partial.
        root = tree()
        write(root, old, b"old rules, edited\n")
        rep = contain(root)
        check("drifted-source-finding", named(rep.findings, old, "drifted") and len(rep.findings) == 1
              and named(rep.migration_incomplete, old, "unresolved"))
        write(root, ".working/imports/{}/plan.toml".format(imp_run), b"x = 1\n")
        rep = contain(root, status="partial")
        check("drifted-under-partial-is-finding", named(rep.findings, old, "drifted")
              and not named(rep.triage, old) and named(rep.migration_incomplete, mig))

        # S6: a vanished frozen source before its retirement is a finding (ruling R-c).
        root = tree()
        os.unlink(root / old)
        rep = contain(root)
        check("vanished-source-finding", named(rep.findings, old, "vanished") and len(rep.findings) == 1
              and named(rep.migration_incomplete, old, "unresolved"))

        # S7: a symlinked or a hard-linked frozen source cannot be evaluated.
        root = tree()
        os.unlink(root / old)
        os.symlink("MIG.md", root / old)
        rep = contain(root)
        check("symlinked-source-cannot", rep.cannot != [] and not named(rep.migration_incomplete, old))
        root = tree()
        os.link(root / old, root / "second-name")
        rep = contain(root)
        check("hardlinked-source-cannot", named(rep.cannot, old) and not named(rep.migration_incomplete, old))

        # S8: a path the plan does not enumerate is a finding (no blanket exemption while adopting).
        root = tree()
        write(root, ".working/OTHER.md", b"x\n")
        rep = contain(root)
        check("unenumerated-path-finding", rep.findings == [unregistered.format(".working/OTHER.md")])

        # S9: a keep row is never frozen; without its [unmanaged] registration it is a finding.
        rep = contain(tree(), model=legacy_manifest)
        check("keep-row-not-frozen", rep.findings == [unregistered.format(keep)])

        # S10: a recorded retirement ends the bounded state of the retire AND move rows; a migrate source
        # stays bounded. Recorded means committed evidence only (QA round 2): a sealed retirement inventory
        # consistent with the plan (every retire and move preimage at its plan digest, live in the run
        # archive) and with the base inventory; anything else is CANNOT-EVALUATE, never read as retired.
        marker = apply.inventory_rel(rid, RETIREMENT_PHASE)
        root = tree()
        write(root, marker, apply.emit_inventory(rid, []))
        rep = contain(root)
        check("retirement-marker-unproven-cannot", named(rep.cannot, marker) and bounded(rep) == [])
        os.unlink(root / old)
        os.unlink(root / note)
        rep = contain(root)
        check("unproven-retirement-never-hides-vanished-sources", named(rep.cannot, marker)
              and bounded(rep) == [])
        root = tree()
        retire(root, rid)
        rep = contain(root)
        check("retired-retire-source-graded", named(rep.findings, old, "unregistered")
              and named(rep.findings, ".working/notes", "unregistered")
              and named(rep.findings, move, "unregistered") and bounded(rep) == [mig]
              and rep.cannot == [])
        # S4c: the ancestor of a frozen source that is gone is graded as one unregistered entry, even empty.
        os.unlink(root / note)
        rep = contain(root)
        check("empty-ancestor-graded", named(rep.findings, ".working/notes", "unregistered"))
        # S10b: a malformed or unreadable retirement inventory cannot be evaluated, never read as unretired.
        write(root, marker, b"not toml [\n")
        rep = contain(root)
        check("retirement-marker-malformed-cannot", named(rep.cannot, marker) and bounded(rep) == [])
        os.unlink(root / marker)
        os.symlink("inventory.toml", root / marker)
        rep = contain(root)
        check("retirement-marker-symlink-cannot", rep.cannot != [] and bounded(rep) == [])
        # S10c (QA round 2): each half of the retirement consistency has its own failing vector. Half 1,
        # plan consistency: a marker missing one retire or move preimage row, and the base inventory
        # misplaced at the retirement name, are each CANNOT, never read as retired.
        root = tree()
        retire(root, rid, dispositions=(old, note))
        rep = contain(root)
        check("retirement-missing-preimage-cannot", named(rep.cannot, marker, "preimage")
              and bounded(rep) == [])
        root = tree()
        write(root, marker, (root / apply.inventory_rel(rid)).read_bytes())
        rep = contain(root)
        check("retirement-marker-is-base-inventory-cannot", named(rep.cannot, marker)
              and bounded(rep) == [])
        # Half 2, base consistency: a marker row contradicting a base inventory row, and a row neither
        # the base inventory nor the plan records, are each CANNOT.
        root = tree()
        retire(root, rid, extra_rows=[dict(path=plan_rel, size=len(runs[rid][0]), sha256="0" * 64)])
        rep = contain(root)
        check("retirement-contradicts-base-cannot", named(rep.cannot, marker, "contradicts")
              and bounded(rep) == [])
        root = tree()
        retire(root, rid, extra_rows=[dict(path=apply.archive_rel(rid, keep), size=len(sources[keep]),
                                           sha256=hashlib.sha256(sources[keep]).hexdigest())])
        rep = contain(root)
        check("retirement-foreign-row-cannot", named(rep.cannot, marker) and bounded(rep) == [])
        # The recorded preimages are live committed evidence: a tampered preimage is CANNOT.
        root = tree()
        retire(root, rid)
        write(root, apply.archive_rel(rid, old), b"evil\n")
        rep = contain(root)
        check("retirement-preimage-tampered-cannot", any("preimage" in m and rid in m for m in rep.cannot)
              and bounded(rep) == [])

        # S11: every record case refuses admission; the run homes are then graded and nothing is bounded.
        def record(name, mutate, verdict, *words):
            root = tree()
            mutate(root)
            rep = contain(root)
            msgs = rep.findings if verdict == "finding" else rep.cannot
            check("record-" + name, any(rid in m and all(w in m for w in words) for m in msgs)
                  and named(rep.findings, ".working/imported", "unregistered") and bounded(rep) == []
                  and (verdict == "cannot" or rep.cannot == []))

        def drop(rel):
            return lambda root: os.unlink(root / rel)

        def put(rel, data, relisted=False):
            def go(root):
                if (root / rel).is_symlink() or (root / rel).exists():
                    os.unlink(root / rel)
                write(root, rel, data)
                if relisted:
                    relist(root, rid)
            return go

        def both(*steps):
            def go(root):
                for step in steps:
                    step(root)
            return go

        inventory = apply.inventory_rel(rid)
        row = apply.inventory_row(plan_rel, runs[rid][0])
        unbound = apply.emit_checked(dict(tomllib.loads(runs[rid][1].decode("utf-8")),
                                          plan_digest="sha256:" + "0" * 64)).encode("utf-8")
        record("missing-inventory", drop(inventory), "finding", "is absent")
        record("phase-inventory-without-base", both(drop(inventory), put(marker, apply.emit_inventory(rid, []))),
               "cannot", "without inventory.toml")
        record("plan-not-listed", lambda root: relist(root, rid, [approval_rel]), "finding", "not listed")
        record("plan-listed-but-absent", drop(plan_rel), "finding", "absent")
        record("inventory-symlink", both(drop(inventory), lambda root: os.symlink("plan.toml", root / inventory)),
               "cannot")
        record("inventory-not-toml", put(inventory, b"not toml [\n"), "cannot", "malformed TOML")
        record("inventory-path-claimed-twice", put(inventory, apply.emit_checked(dict(
            format=store.EVIDENCE_INVENTORY_FORMAT, file=[row, row])).encode("utf-8")), "cannot", "more than once")
        record("legacy-ingest-inventory", put(inventory, apply.emit_checked(dict(
            format="opf.ingest.evidence-inventory/v1", file=[])).encode("utf-8")), "finding", "legacy-ingest")
        record("plan-not-decodable", put(plan_rel, b"\xff\xfe", relisted=True), "cannot", "malformed TOML")
        record("plan-bytes-differ-from-row", put(plan_rel, runs[rid][0] + b"\n"), "finding", "tampered")
        record("plan-not-canonical", put(plan_rel, runs[rid][0] + b"# note\n", relisted=True), "finding",
               "canonical")
        record("approval-not-binding", put(approval_rel, unbound, relisted=True), "finding", "does not bind")
        def misplace(root, src_rid, dst_rid):
            """Place the record of `src_rid` under the bundle of `dst_rid` (a misplaced or stale record)."""
            write(root, plan_rel, runs[src_rid][0])
            write(root, approval_rel, runs[src_rid][1])
            relist(root, dst_rid)

        record("plan-run-id-not-bundle", lambda root: misplace(root, rid_b, rid), "finding",
               "names run")
        # QA round 2 (the admission proof is committed evidence only): a hand-edited (unsealed)
        # inventory, an inventory misplaced from another run, and a run archive that does not match the
        # inventory each fail closed; the journal grants and denies nothing (the S19 clone vectors).
        record("inventory-not-sealed",
               lambda root: (root / inventory).write_bytes((root / inventory).read_bytes() + b"\n"),
               "finding", "sealed")
        record("inventory-of-another-run", put(inventory, apply.emit_inventory(rid_b, [apply.inventory_row(
            apply.evidence_home_rel(rid_b) + "/" + apply.PLAN_NAME, runs[rid_b][0])])), "cannot",
            "cannot claim")
        record("archive-row-absent", drop(apply.archive_rel(rid, mig)), "finding", "absent")
        record("archive-row-tampered", put(apply.archive_rel(rid, mig), b"evil\n"), "finding",
               "does not match")

        # Stale identity: a plan that does not describe this in-repo store cannot be evaluated.
        root = tree()
        for name, flag in (("not-in-repo", False), ("topology-unknown", None)):
            rep = contain(root, in_repo=flag)
            check("identity-" + name + "-cannot", any(rid in m and "does not describe this store" in m
                                                     for m in rep.cannot) and bounded(rep) == [])
        fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
        try:
            st = reader.adoption_state(fd, ".working/other", in_repo=True)
        finally:
            os.close(fd)
        check("identity-machine-rel-cannot", st.runs == () and any("does not describe this store" in m
                                                                     for m in st.cannot))

        # S12: one path frozen by two unretired admitted runs is ambiguous.
        rep = contain(tree(adopted=(rid, rid_b)))
        check("double-claim-cannot", named(rep.cannot, old, "more than one") and not named(
            rep.migration_incomplete, old))

        # S13: while adopting, an [unmanaged] declaration in the control area collides and covers nothing.
        root = tree()
        write(root, ".working/archive/stray.md", b"x\n")
        model = dict(copy.deepcopy(manifest), unmanaged=dict(paths=[keep, ".working/archive"]))
        rep = contain(root, model=model)
        check("unmanaged-control-area-collides", named(rep.findings, ".working/archive", "covers nothing")
              and named(rep.findings, ".working/archive/stray.md", "unregistered") and len(rep.findings) == 2)
        model = dict(copy.deepcopy(manifest), unmanaged=dict(paths=[keep, home + "/notes"]))
        rep = contain(root, model=model)
        check("unmanaged-within-control-area-collides", named(rep.findings, home + "/notes", "covers nothing"))
        root = tree(adopted=())
        write(root, ".working/archive/stray.md", b"x\n")
        rep = contain(root, model=dict(copy.deepcopy(manifest), unmanaged=dict(paths=[keep, ".working/archive"])))
        check("legacy-unmanaged-archive-unchanged", not any("covers nothing" in m for m in rep.findings)
              and not named(rep.findings, ".working/archive/stray.md"))

        # S14: a fault anywhere inside the reader is a named cannot-evaluate, never the legacy grading.
        root = tree()
        with mock.patch.object(reader, "adoption_state", side_effect=RuntimeError("injected")):
            rep = contain(root)
        check("reader-fault-cannot", any("adoption state cannot be read" in m and "injected" in m
                                         for m in rep.cannot))

        # S15: read-only and journal-free (QA round 2): the doctor opens no journal at all (committed
        # evidence only); journal_state, the partial-status surface, is never consulted, there is no
        # write effect, and the tree, the journal included, is byte-unchanged.
        root = tree()
        write(root, apply.JOURNAL_REL + "/txn/intent.toml", b"x = 1\n")
        before = snapshot(root)
        opened = []
        real_open = os.open

        def spy(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        effect = None
        try:
            with mock.patch.object(apply, "journal_state", side_effect=AssertionError("journal consulted")), \
                    mock.patch.object(_journal, "open_journal_root_fd",
                                      side_effect=AssertionError("journal opened")), \
                    mock.patch.object(_journal, "classify_state", side_effect=AssertionError("journal read")), \
                    mock.patch.object(_journal, "read_frames", side_effect=AssertionError("journal read")), \
                    mock.patch.object(os, "open", spy), apply._composing():
                rep = contain(root)
        except (apply.AdoptApplyError, AssertionError) as exc:
            effect = exc
        check("read-only-no-effect", effect is None and rep.findings == [] and rep.cannot == [])
        check("journal-never-opened", opened != [] and all(apply.JOURNAL_REL not in p for p in opened))
        check("tree-unchanged", snapshot(root) == before)

        # S17 (QA round 1): the fail-closed ceilings. Each is patched small over a tree that otherwise reads
        # clean, so every vector fails without its enforcement.
        root = tree()
        with mock.patch.object(reader, "RUN_CEILING", 0):
            rep = contain(root)
        check("run-ceiling-cannot", any("run" in m and "ceiling" in m for m in rep.cannot)
              and bounded(rep) == [])
        root = tree()
        with mock.patch.object(reader, "SOURCE_CEILING", 1):
            rep = contain(root)
        check("source-ceiling-cannot", any("frozen sources" in m and "ceiling" in m for m in rep.cannot)
              and bounded(rep) == [])
        root = tree(adopted=(rid, rid_b))
        reads = []
        real_read = apply._read_live
        with mock.patch.object(reader, "READ_CEILING", 8), \
                mock.patch.object(apply, "_read_live",
                                  lambda fd, rel: reads.append(rel) or real_read(fd, rel)):
            rep = contain(root)
        check("read-ceiling-cannot", any("read ceiling" in m for m in rep.cannot))
        # QA round 2: an exhausted budget refuses BEFORE reading, so the one crossing read is the last.
        check("exhausted-budget-stops-reading", len(reads) == 1)

        # S18 (QA round 1): the move row, frozen by the real planner: bounded, drifted and vanished grade
        # exactly as retire does, and its recorded Move destination is registered at file level.
        root = tree()
        write(root, store.moved_dest(move), sources[move])
        rep = contain(root)
        check("move-destination-registered", rep.findings == [] and rep.cannot == []
              and named(rep.migration_incomplete, move))
        root = tree()
        write(root, move, b"to move, edited\n")
        rep = contain(root)
        check("moved-source-drift-finding", named(rep.findings, move, "drifted")
              and named(rep.migration_incomplete, move, "unresolved"))
        root = tree()
        os.unlink(root / move)
        rep = contain(root)
        check("moved-source-vanished-finding", named(rep.findings, move, "vanished")
              and named(rep.migration_incomplete, move, "unresolved"))

        # S19 (QA round 2, the ruling): the proof is committed evidence ONLY, so a clone, which carries
        # the bundle and the run archive but no machine-local journal (spec 4.2), grades exactly as the
        # original store, retirement included.
        root = tree()
        write(root, store.moved_dest(move), sources[move])
        original = contain(root)
        shutil.rmtree(root / ".aiqt")
        cloned = contain(root)
        check("clone-without-journal-grades-identically", original.findings == [] and original.cannot == []
              and (cloned.findings, cloned.cannot, cloned.triage, cloned.migration_incomplete)
              == (original.findings, original.cannot, original.triage, original.migration_incomplete))
        root = tree()
        retire(root, rid)
        shutil.rmtree(root / ".aiqt")
        rep = contain(root)
        check("clone-retirement-recorded-from-evidence", rep.cannot == [] and bounded(rep) == [mig])

        # S16 (QA round 1): end to end through the real doctor engine: a real scaffolded store (opf init,
        # opf render --write), real git observations, the adoption bundle with its COMMITTED transaction,
        # and validate_store returns VALID carrying the bounded sources as migration_incomplete.
        import contextlib
        import io
        import _opf_observe
        import opf as _opf_cli

        def git_commit_all(root):
            git = shutil.which("git")
            if git is None:
                return False
            env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
            env.update(GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                       GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
                       GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
            add = subprocess.run([git, "-C", str(root), "add", "-A"], stdin=subprocess.DEVNULL,
                                 capture_output=True, env=env, timeout=60)
            done = subprocess.run([git, "-C", str(root), "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                                   "-c", "maintenance.auto=false", "-c", "commit.gpgsign=false", "commit",
                                   "-q", "-m", "fixture"], stdin=subprocess.DEVNULL, capture_output=True,
                                  env=env, timeout=60)
            return add.returncode == 0 and done.returncode == 0

        e2e = base / "e2e"
        e2e.mkdir()
        quiet = io.StringIO()
        ok = apply._selftest_git_commit(e2e) is not None
        with contextlib.redirect_stdout(quiet):
            ok = ok and _opf_cli.main(["init", "--root", str(e2e)]) == 0
        ok = ok and git_commit_all(e2e)
        with contextlib.redirect_stdout(quiet):
            ok = ok and _opf_cli.main(["render", "--write", "--root", str(e2e)]) == 0
        for rel, data in sources.items():
            if rel != keep:
                write(e2e, rel, data)
        bundle(e2e, rid)
        ok = ok and git_commit_all(e2e)
        result = None
        if ok:
            res = store.resolve_store(e2e)
            if res.status == store.RESOLVED:
                obs, _notes = _opf_observe.gather(res)
                result = _opf_check.validate_store(res, observations=obs)
        check("s16-end-to-end-doctor-valid", result is not None and result.status == store.VALID
              and result.checks.get("C-CONTAINMENT") == "PASS" and len(result.migration_incomplete) == 4
              and result.findings == [] and result.cannot_evaluate == [])

    if failures:
        print("OPF-ADOPT-STATE SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ADOPT-STATE SELF-TEST: PASS ({} adoption-state checks)".format(checked[0]))
    return 0


def main():
    print("usage: _opf_adopt_state.py --self-test (a library module; C-CONTAINMENT in `opf doctor` reads it)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
