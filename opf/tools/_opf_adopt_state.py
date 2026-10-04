"""The homes-1 adoption control area and the bounded adoption state the doctor reads (OPF-SPEC 4.2, 11, 14.2, 17).

A store that has adopted (its `.working/imported/adoption/` home exists) registers, on homes 1, each ADMITTED
run: its evidence bundle `.working/imported/adoption/<run-id>/`, its adoption archive
`.working/archive/adoption/<run-id>/`, and the shared Move root `.working/archive/moved/`. C-CONTAINMENT
recognizes them by containment as OPF control area and does not re-enumerate them (spec 4.2, 17). A run is
admitted only when its bundle record re-proves: a VALID `inventory.toml` listing `plan.toml` and
`approval.toml` at their exact size and digest, a plan that re-proves as a frozen plan/v2
(`_opf_adopt_apply.frozen_plan`), a canonical approval that binds it, a plan run id equal to the bundle
name, and a plan store identity equal to this in-repo store. Every other path under the control area stays
an unregistered path, graded exactly as before.

The frozen retire, move and migrate sources under `.working/` of each admitted run (the non-occupying source
rows of its frozen plan) are bounded adoption state until their retirement is recorded (spec 11, 14.2): a
source whose live bytes still match its plan digest is `bounded` (reported as migration_incomplete, never
failed), one whose bytes differ is `drifted`, and one that is gone is `absent`; both of the latter fail at
required. A retirement is recorded when the run bundle carries a VALID retirement-phase inventory; it ends
the bounded state of the retire and move rows only. A migrate source retires through its import, which a
later activation reads, so it stays bounded until then.

Read-only by construction: the reader never opens the adoption journal (spec 4.2: doctor MUST NOT consult a
journal), never reads an archive copy or a payload (on homes 1 the completion checks carry those digests,
spec 17), and writes nothing. Every file read is contained, no-follow, single-link and bounded by the
journal 16 MiB read cap (`_opf_adopt_apply._read_live`). A store without the adoption home costs exactly
one lstat and keeps the legacy grading unchanged. C-EVIDENCE-ENUM stays inactive on homes 1.

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
# entry beneath them other than an admitted run home is still graded.
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


def adoption_state(root_fd, machine_rel, *, in_repo):
    """The adoption state of the store beneath root_fd (an AdoptionState). Read-only and journal-free.

    `.working/imported/adoption` absent: NOT_ADOPTING after exactly one lstat, so the legacy grading runs
    unchanged. Present: every run-id child is admitted or refused (_admit_run); an admitted run contributes
    its homes to `registered` and its frozen sources to `frozen`, each graded against its plan digest. A
    refused run record is a finding (the run homes are then graded as unregistered paths); an input that
    cannot be read, or a plan that does not describe this store, is a cannot-evaluate."""
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
    findings, cannot, runs, plans = [], [], [], {}
    # A file or a non-run-id child is never admitted; the containment walk grades it as unregistered.
    for name in subdirs or ():
        if not apply.is_run_id(name):
            continue
        plan_doc, run_findings, run_cannot = _admit_run(root_fd, name, machine_rel, in_repo)
        findings.extend(run_findings)
        cannot.extend(run_cannot)
        if plan_doc is not None:
            runs.append(name)
            plans[name] = plan_doc
    registered = []
    for run_id in runs:
        registered.extend((apply.evidence_home_rel(run_id), apply._archive_root(run_id)))
    if runs:
        registered.append(MOVED_ROOT)
    claims = {}
    for run_id in runs:
        try:
            sources = frozen_sources(plans[run_id], _retired(root_fd, run_id))
        except apply.AdoptApplyError as exc:
            # Whether this run retired cannot be told, so none of its sources is bounded (each then grades as
            # an unregistered path) and the report cannot evaluate.
            cannot.append(_PREFIX + "adoption run {}: the retirement state cannot be evaluated ({}); "
                          "fail-closed".format(run_id, exc))
            continue
        for path, (hexdigest, disposition) in sources.items():
            claims.setdefault(path, []).append((run_id, hexdigest, disposition))
    frozen = {}
    for path in sorted(claims):
        if len(claims[path]) > 1:
            cannot.append(_PREFIX + "{!r} is a frozen source of more than one unretired adoption run ({}); "
                          "the bounded state is ambiguous (fail-closed)".format(
                              path, ", ".join(c[0] for c in claims[path])))
            continue
        run_id, hexdigest, disposition = claims[path][0]
        try:
            grade, detail = grade_frozen(root_fd, path, hexdigest)
        except apply.AdoptApplyError as exc:
            cannot.append(_PREFIX + "frozen {} source {!r} of adoption run {} cannot be graded ({}); "
                          "fail-closed".format(disposition, path, run_id, exc))
            grade, detail = "cannot", str(exc)
        frozen[path] = FrozenSource(hexdigest, run_id, disposition, grade, detail)
    return _state(runs, registered, frozen, findings, cannot)


def _admit_run(root_fd, run_id, machine_rel, in_repo):
    """(plan_doc or None, findings, cannot) for one adoption run bundle. The run is admitted only when its
    durable record re-proves (see the module introduction): a refused record is a finding naming the reason;
    an unreadable or malformed input, and a plan that describes another store, are cannot-evaluate."""
    findings, cannot = [], []
    bundle = apply.evidence_home_rel(run_id)
    tail = "; the run is not admitted, so its homes are graded as unregistered paths"

    def refuse(reason):
        findings.append(_PREFIX + "adoption run {} is not admitted: {}{}".format(run_id, reason, tail))
        return None, findings, cannot

    def cant(reason):
        cannot.append(_PREFIX + "adoption run {} cannot be evaluated: {}{} (fail-closed)".format(
            run_id, reason, tail))
        return None, findings, cannot

    inv_rel = apply.inventory_rel(run_id)
    try:
        fst, data = apply._read_live(root_fd, inv_rel)
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
    rows = dict((row["path"], row) for row in doc["file"])
    blobs = {}
    for name in (apply.PLAN_NAME, apply.APPROVAL_NAME):
        rel = "{}/{}".format(bundle, name)
        row = rows.get(rel)
        if row is None:
            return refuse("{!r} is not listed in its inventory.toml".format(rel))
        try:
            fst, data = apply._read_live(root_fd, rel)
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
    return plan_doc, findings, cannot


def _retired(root_fd, run_id):
    """True when the run bundle carries a VALID retirement-phase inventory, False when it is absent. Raises
    AdoptApplyError when it is unreadable, malformed or not VALID: whether the run retired is then unknown."""
    rel = apply.inventory_rel(run_id, RETIREMENT_PHASE)
    fst, data = apply._read_live(root_fd, rel)
    if fst is None:
        return False
    graded = apply.validate_inventory(_toml(data, rel), run_id)
    if graded.status != store.VALID:
        raise apply.AdoptApplyError("{!r} is not a VALID inventory ({})".format(rel, "; ".join(graded.findings)))
    return True


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


def grade_frozen(root_fd, path, hexdigest):
    """(grade, detail) of one frozen source: `bounded` (live bytes equal the plan digest), `drifted` or
    `absent`. A symlink, special file, multiply-linked file or a file over the read cap raises
    AdoptApplyError (cannot-evaluate), never a guess."""
    fst, data = apply._read_live(root_fd, path)
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
    sources = {old: b"old rules\n", note: b"old note\n", mig: b"to migrate\n", keep: b"kept\n"}
    decisions = {old: "retire", note: "retire", mig: "migrate", keep: "keep"}
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

        def relist(root, rid_, paths=None):
            rows = [apply.inventory_row(rel, (root / rel).read_bytes()) for rel in (listed(rid_) if paths is None
                                                                                     else paths)]
            write(root, apply.inventory_rel(rid_), apply.emit_inventory(rid_, rows))

        def bundle(root, rid_, plan_bytes=None, approval_bytes=None):
            h = apply.evidence_home_rel(rid_)
            write(root, h + "/" + apply.PLAN_NAME, plan_bytes if plan_bytes is not None else runs[rid_][0])
            write(root, h + "/" + apply.APPROVAL_NAME, approval_bytes if approval_bytes is not None else runs[rid_][1])
            write(root, apply.archive_rel(rid_, mig), sources[mig])
            write(root, moved, b"moved\n")
            relist(root, rid_)

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
        legacy = sorted((".working/MIG.md", ".working/OLD.md", ".working/archive", ".working/imported",
                         ".working/notes"))
        check("legacy-not-adopting-one-lstat-no-read", seen == [ADOPTION_HOME] and reads == [])
        check("legacy-findings-unchanged", rep.findings == [unregistered.format(p) for p in legacy]
              and rep.cannot == [] and rep.triage == [])
        check("legacy-residuals-unchanged", rep.residuals == list(_opf_check._RESIDUALS))
        check("legacy-no-migration-incomplete", rep.migration_incomplete == [])

        # S2 + S4: an admitted run registers its bundle, archive and Move root; its frozen sources are bounded.
        root = tree()
        rep = contain(root)
        check("admitted-run-registered", rep.findings == [] and rep.cannot == [])
        check("frozen-sources-bounded", bounded(rep) == sorted((old, note, mig)) and not named(
            rep.migration_incomplete, keep))
        check("adopting-residual-disclosed", rep.residuals == list(_opf_check._RESIDUALS)
              + list(_opf_check._ADOPTING_RESIDUALS))
        result = rep.result()
        check("store-validation-carries-migration-incomplete",
              result.migration_incomplete == rep.migration_incomplete and len(result.migration_incomplete) == 3)
        # bounded at a clean start as much as at a substantiated partial (spec 11), never triage.
        write(root, ".working/imports/{}/plan.toml".format(imp_run), b"x = 1\n")
        rep = contain(root, status="partial")
        check("bounded-under-partial", bounded(rep) == sorted((old, note, mig)) and rep.findings == []
              and not any(named(rep.triage, p) for p in sources))

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
              and not named(rep.migration_incomplete, old))
        write(root, ".working/imports/{}/plan.toml".format(imp_run), b"x = 1\n")
        rep = contain(root, status="partial")
        check("drifted-under-partial-is-finding", named(rep.findings, old, "drifted")
              and not named(rep.triage, old) and named(rep.migration_incomplete, mig))

        # S6: a vanished frozen source before its retirement is a finding (ruling R-c).
        root = tree()
        os.unlink(root / old)
        rep = contain(root)
        check("vanished-source-finding", named(rep.findings, old, "vanished") and len(rep.findings) == 1)

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

        # S10: a recorded retirement ends the bounded state of retire rows; a migrate source stays bounded.
        marker = apply.inventory_rel(rid, RETIREMENT_PHASE)
        root = tree()
        write(root, marker, apply.emit_inventory(rid, []))
        rep = contain(root)
        check("retired-retire-source-graded", named(rep.findings, old, "unregistered")
              and named(rep.findings, ".working/notes", "unregistered") and bounded(rep) == [mig])
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

        # S15: read-only and journal-free: no write effect, no path under .aiqt opened, the tree unchanged.
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
                    mock.patch.object(os, "open", spy), apply._composing():
                rep = contain(root)
        except (apply.AdoptApplyError, AssertionError) as exc:
            effect = exc
        check("read-only-no-effect", effect is None and rep.findings == [] and rep.cannot == [])
        check("journal-never-opened", opened != [] and not any(".aiqt" in p for p in opened))
        check("tree-unchanged", snapshot(root) == before)

    if failures:
        print("OPF-ADOPT-STATE SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked[0]))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("OPF-ADOPT-STATE SELF-TEST: PASS ({} adoption-state checks)".format(checked[0]))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args or "--selftest" in args:
        return self_test()
    print("usage: _opf_adopt_state.py --self-test (a library module; C-CONTAINMENT in `opf doctor` reads it)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
