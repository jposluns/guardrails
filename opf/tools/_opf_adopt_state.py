"""The homes-1 adoption control area and the bounded adoption state the doctor reads (OPF-SPEC 4.2, 11, 14.2, 17).

A store that has adopted (its `.working/imported/adoption/` home exists) registers, on homes 1, each ADMITTED
run AT FILE LEVEL: the paths its committed inventories list, the base inventory's rows and, once its
retirement is recorded, the retirement inventory's rows (the plan's recorded Move destinations).
C-CONTAINMENT recognizes exactly those files as OPF control area; every other path
under the adoption homes, the run's own evidence bundle, its adoption archive and the Move root included, is
graded as an unregistered path (spec 4.2, 17). A run is admitted from its COMMITTED, immutable adoption
evidence ALONE, the bundle under the adoption home and the run archive, which travel with every clone; the
machine-local journal is never read, so a clone without it grades exactly as the original store (spec 4.2).
Admission requires a VALID, sealed (canonically emitted) `inventory.toml` whose rows under the bundle,
the run archive and the shared Move root each hold the live bytes at their exact size and digest (a listed
member that is absent or differs refuses; an unlisted file under the run homes stays a graded finding; a
Move-root row is admitted only when the plan's own move rows record that exact destination, so a listed
Move-root file that no move row explains refuses), a plan that re-proves as a frozen plan/v2
(`_opf_adopt_apply.frozen_plan`), a canonical approval that binds it, a plan run id equal to the bundle
name (every record is bound to the run id of the directory it sits in; an inventory claiming another run's
paths is refused by the shared row validator), and a plan store identity equal to this in-repo store. The
whole proof detects ACCIDENTS: an interrupted apply, a hand edit that is non-canonical or that no longer
matches the listed bytes, a misplaced or stale record. It does not detect deliberate forgery, and a hand
edit that re-emits a canonical, self-consistent record is forgery, not an accident: a crafted
self-consistent bundle is outside the accident-detection model, consistent with the rest of OPF (spec 4.2).

The frozen retire, move and migrate sources under `.working/` of each admitted run (the non-occupying source
rows of its frozen plan) are bounded adoption state until their retirement is recorded (spec 11, 14.2): a
source whose live bytes still match its plan digest is `bounded` (reported as migration_incomplete, never
failed), one whose bytes differ is `drifted`, and one that is gone is `absent`; both of the latter fail at
required AND stay reported as migration_incomplete, because the approved source remains unresolved (spec 11).
A retirement is recorded only in committed evidence: a live, VALID, sealed retirement inventory in the
spec shape, the one the retirement-phase transaction derives from its own create ops (spec 4.2, 14.2).
That transaction creates only the plan's recorded Move destinations (retire and migrate preimages are
preserved at APPLY and claimed by the base inventory, spec 14.1 check 3, so the retirement record never
re-lists them), so the record holds one row per move row of the plan, occupying or not, whose recorded
Move destination (its `move-file` op's destination) lies beneath the Move root, naming that destination
at the moved source's plan digest, and nothing else. Those are the bytes the relocation writes: the
frozen live source of a non-occupying move, the committed archive copy of an occupying one. A plan
without such a move row has a record that lists no files. That shape binds the record to its phase: a
base inventory copied to the retirement name lists at least the plan and the approval, never Move
destinations, so it never reads as retired. A marker row whose path the base inventory also lists is a
path claimed twice (spec 4.2), and every listed destination must hold its recorded bytes live. Any other retirement evidence is CANNOT-EVALUATE, never read as retired. Before a recorded
retirement the Move destination does not exist (the retirement-phase transaction creates it), so a file
at a plan Move destination beneath the Move root, default or explicit, is then a finding and the
destination is not registered (an explicit destination outside the store is never graded); after it, the
destination is registered and byte-verified through the retirement inventory. A recorded retirement ends
the bounded state of the retire and move rows only. A migrate source retires through its import, which a
later activation reads, so it stays bounded until then.

Read-only and bounded by construction: the reader opens no journal and writes nothing. Every file read is
contained, no-follow, single-link and bounded by the 16 MiB per-file read cap
(`_opf_adopt_apply._read_live`); EVERY byte read counts against the READ_CEILING whole-call byte budget,
with a FAILED read charged exactly the bytes its chunk loop consumed before failing (a refusal before the
first byte charges nothing); an
exhausted budget refuses the next read before it happens, and one adoption_state
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
import os
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
    read, itself bounded by the per-file cap plus one chunk, exceeds the ceiling). A read that FAILS is
    charged exactly the bytes it consumed: the chunk loop below is `_journal._read_fd` with each chunk
    counted as soon as os.read returns it (the seam _journal's own self-test interposes on), so a refusal
    before the first byte (an absent parent, a symlink, a special or multiply-linked file) charges
    nothing, and a per-file-cap or mid-read failure charges every chunk it read, the crossing chunk
    included."""
    if budget[0] < 0:
        raise apply.AdoptApplyError("the adoption-state read ceiling of {} bytes is exhausted; {!r} is "
                                    "not read (fail-closed)".format(READ_CEILING, rel))
    consumed = [0]
    real_read_fd = _journal._read_fd

    def counting_read_fd(fd, cap=None):
        chunks = []
        total = 0
        while True:
            try:
                block = os.read(fd, _journal._READ_CHUNK)
            except OSError as exc:
                raise _journal.JournalError("read error on a contained file descriptor ({})".format(exc))
            if not block:
                return b"".join(chunks)
            total += len(block)
            consumed[0] += len(block)
            if cap is not None and total > cap:
                raise _journal.JournalError(
                    "contained file exceeds the {}-byte read cap (fail-closed)".format(cap))
            chunks.append(block)

    _journal._read_fd = counting_read_fd
    try:
        fst, data = apply._read_live(root_fd, rel)
    finally:
        _journal._read_fd = real_read_fd
        budget[0] -= consumed[0]
    if data is not None and budget[0] < 0:
        raise apply.AdoptApplyError("reading {!r} exceeds the adoption-state read ceiling of {} bytes; "
                                    "fail-closed".format(rel, READ_CEILING))
    return fst, data


def adoption_state(root_fd, machine_rel, *, in_repo):
    """The adoption state of the store beneath root_fd (an AdoptionState). Read-only; no journal is
    opened, so a clone without the machine-local journal grades exactly as the original store (spec 4.2).

    `.working/imported/adoption` absent: NOT_ADOPTING after exactly one lstat, so the legacy grading runs
    unchanged. Present: every run-id child is admitted or refused (_admit_run); an admitted run contributes
    the file paths its proven inventories list to `registered` (its recorded Move destinations only once
    its retirement is recorded, since the retirement-phase transaction is what creates them) and its
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
        try:
            retired, retired_registered = _retired(root_fd, run_id, plans[run_id], rows[run_id], budget)
        except apply.AdoptApplyError as exc:
            # Whether this run retired cannot be told, so none of its sources is bounded (each then grades as
            # an unregistered path) and the report cannot evaluate.
            cannot.append(_PREFIX + "adoption run {}: the retirement state cannot be evaluated ({}); "
                          "fail-closed".format(run_id, exc))
            continue
        if retired:
            registered.extend(retired_registered)
        else:
            # Before the recorded retirement the retirement-phase transaction has not run, so nothing
            # exists at a plan Move destination beneath the Move root (spec 14.2); an entry there is a
            # premature, unexplained record (a finding) and the destination is not registered.
            for dest in move_destinations(plans[run_id]):
                try:
                    dst = _journal._lstat_contained(root_fd, dest)
                except (_journal.JournalError, OSError) as exc:
                    cannot.append(_PREFIX + "cannot observe the Move destination {!r} of adoption run {} "
                                  "({}); fail-closed".format(dest, run_id, exc))
                    continue
                if dst is not None:
                    findings.append(_PREFIX + "{!r} exists before the recorded retirement of adoption run "
                                    "{}: the retirement-phase transaction creates the Move destination "
                                    "(spec 14.2), so an earlier entry there is unexplained".format(
                                        dest, run_id))
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
    # The [adoption] identity (spec 4.2, the run-binding ruling): the VALID grade above already proved
    # the identity well-formed and bound to this bundle's run id; the base inventory must also carry the
    # base phase, so an inventory copied from another phase refuses, and once the plan is proven below
    # its plan digest must be the proven plan's own.
    phase_id, claimed_digest = apply.inventory_identity(run_id, doc)
    if phase_id != apply.BASE_PHASE:
        return refuse("its inventory.toml carries the [adoption] identity of phase {!r}, not the base "
                      "phase (an inventory copied from another phase)".format(phase_id))
    # The sealed record (spec 4.2): only the canonical emission of its own rows is the committed inventory;
    # appended, reordered or hand-edited bytes refuse. This detects accidents, not deliberate forgery.
    try:
        sealed = apply.emit_inventory(run_id, [dict(row) for row in doc["file"]], None, claimed_digest)
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
    # digest; a listed member that is absent or differs refuses. A row under the shared Move root is
    # verified below, once the plan is proven, against the plan's own move rows.
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
    if claimed_digest != plan_doc.get("plan_digest"):
        return refuse("its inventory.toml [adoption] identity names plan digest {!r}, not its own proven "
                      "plan's (a misplaced or stale record)".format(claimed_digest))
    # A row under the shared Move root is committed evidence like any other (QA round 3): it is admitted
    # only when the proven plan's own move rows record that exact destination, and its live bytes must
    # hold the row's recorded size and digest, so a listed Move-root file that no move row of this run's
    # plan explains, a tampered one and a deleted one each refuse.
    dests = set(move_destinations(plan_doc))
    for rel in sorted(rows):
        if not rel.startswith(MOVED_ROOT + "/"):
            continue
        if rel not in dests:
            return refuse("its inventory.toml lists {!r} under the shared Move root, which no move row of "
                          "its plan records (a misplaced or stale record)".format(rel))
        row = rows[rel]
        try:
            fst, data = _read_budgeted(root_fd, rel, budget)
        except apply.AdoptApplyError as exc:
            return cant(str(exc))
        if fst is None:
            return refuse("{!r} is listed in its inventory.toml but absent".format(rel))
        if len(data) != row["size"] or apply._sha256(data) != row["sha256"]:
            return refuse("{!r} does not match its inventory.toml row (a tampered or stale record)".format(rel))
    return plan_doc, rows, [inv_rel] + sorted(rows), findings, cannot


def _retired(root_fd, run_id, plan_doc, base_rows, budget):
    """(recorded, registered): whether the run's retirement is recorded, and the file paths its retirement
    inventory registers, decided from COMMITTED evidence alone (never the journal). Recorded ONLY when a
    live, VALID, sealed `inventory-retirement.toml` holds the shape the retirement-phase transaction
    derives from its own create ops (spec 4.2, 14.2): one row per move row of the plan, occupying or not,
    whose recorded Move destination lies beneath the Move root, naming that destination at the moved
    source's plan digest (_move_creates), and nothing else; a plan without such a move row has a record
    that lists no files. Retire and migrate preimages are preserved at APPLY and claimed by the base
    inventory (spec 14.1 check 3), never re-listed here, and that shape binds the record to its phase: a
    base inventory copied to the retirement name lists at least the plan and the approval, never Move
    destinations. A marker row whose path the base inventory also lists is a path claimed twice, and every
    listed destination must hold its recorded bytes live. An absent marker is not retired. Every other
    state raises AdoptApplyError: whether the run retired is then unknown (CANNOT-EVALUATE), never read
    as retired."""
    rel = apply.inventory_rel(run_id, RETIREMENT_PHASE)
    fst, data = _read_budgeted(root_fd, rel, budget)
    if fst is None:
        return False, []
    doc = _toml(data, rel)
    graded = apply.validate_inventory(doc, run_id)
    if graded.status != store.VALID:
        raise apply.AdoptApplyError("{!r} is not a VALID inventory ({})".format(rel, "; ".join(graded.findings)))
    # The [adoption] identity (spec 4.2, the run-binding ruling): the VALID grade proved it well-formed
    # and bound to this bundle's run id; the record must also carry the retirement phase and the proven
    # plan's own digest, so an inventory copied from another run or phase, an empty record included,
    # never reads as retired.
    phase_id, claimed_digest = apply.inventory_identity(run_id, doc)
    if phase_id != RETIREMENT_PHASE:
        raise apply.AdoptApplyError("{!r} carries the [adoption] identity of phase {!r}, not the "
                                    "retirement phase (an inventory copied from another phase); whether "
                                    "the run retired is unknown".format(rel, phase_id))
    if claimed_digest != plan_doc.get("plan_digest"):
        raise apply.AdoptApplyError("{!r} names plan digest {!r} in its [adoption] identity, not the "
                                    "proven plan's own (a record of another run or plan); whether the "
                                    "run retired is unknown".format(rel, claimed_digest))
    if apply.emit_inventory(run_id, [dict(row) for row in doc["file"]], RETIREMENT_PHASE,
                            claimed_digest) != data:
        raise apply.AdoptApplyError("{!r} is not the sealed canonical inventory of its own rows (a hand "
                                    "edit, a reordering or stale bytes); whether the run retired is "
                                    "unknown".format(rel))
    # The shape half: the record lists EVERY recorded Move destination of the plan's move rows, occupying
    # or not, at the moved source's plan digest, so a record of a different run state, a drifted
    # relocation or an old-shape marker is never silently read as retired.
    dests = dict((dest, apply._plan_hex(digest)) for dest, digest in _move_creates(plan_doc).items())
    marker_rows = dict((row["path"], row) for row in doc["file"])
    for dest in sorted(dests):
        row = marker_rows.get(dest)
        if row is None or row["sha256"] != dests[dest]:
            raise apply.AdoptApplyError("{!r} does not record the Move destination {!r} at the moved "
                                        "source's plan digest, so it is not the inventory the plan's "
                                        "retirement-phase transaction derives (spec 14.2); whether the "
                                        "run retired is unknown".format(rel, dest))
    # The phase-binding half: the member set is EXACTLY the plan's recorded Move destinations (the only
    # files the retirement-phase transaction creates), so the base inventory copied to the retirement
    # name (it lists at least the plan and the approval) and an old-shape marker that re-lists archive
    # preimages never read as retired.
    for path in sorted(set(marker_rows) - set(dests)):
        raise apply.AdoptApplyError("{!r} lists {!r}, which is not a recorded Move destination of the "
                                    "plan's move rows; the retirement-phase transaction creates only "
                                    "those files and its derived inventory lists exactly them, so a "
                                    "misplaced, stale or copied record never reads as retired; whether "
                                    "the run retired is unknown".format(rel, path))
    # One claim per path (spec 4.2, QA round 5): every payload file is claimed by exactly one row, and the
    # apply-stage transaction never creates a Move destination, so a marker row whose path the base
    # inventory also lists, at the same or at different bytes, is a path claimed twice; it is never read
    # as retirement proof.
    for path in sorted(marker_rows):
        if path in base_rows:
            raise apply.AdoptApplyError("{!r} lists {!r}, which the base inventory also claims (a path "
                                        "claimed twice, spec 4.2); whether the run retired is "
                                        "unknown".format(rel, path))
    # The destinations the marker records are live committed evidence, byte for byte.
    for path in sorted(marker_rows):
        fst, live = _read_budgeted(root_fd, path, budget)
        if fst is None:
            raise apply.AdoptApplyError("the recorded Move destination {!r} its retirement inventory "
                                        "lists is absent; whether the run retired is unknown".format(path))
        if len(live) != marker_rows[path]["size"] or apply._sha256(live) != marker_rows[path]["sha256"]:
            raise apply.AdoptApplyError("the recorded Move destination {!r} does not match its retirement "
                                        "inventory row; whether the run retired is unknown".format(path))
    return True, [rel] + sorted(marker_rows)


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


def _move_creates(plan_doc):
    """{destination: plan digest} of the files one admitted plan's retirement-phase transaction creates
    (spec 14.2 Move): the `destination` of EVERY `move-file` op, occupying source or not, that lies beneath
    the Move root (the retained evidence its derived inventory lists; a destination outside the store is
    not evidence and gets no row), at the op's `source_digest`, the moved source's plan digest. Those are
    the bytes the relocation writes: the frozen live source for a non-occupying source, the committed
    archive copy (verified at that same digest) for an occupying one. The destination is read from the
    op, never from the source row's `preservation`, which names the adoption archive copy for an occupying
    source. Any other spelling contributes nothing (fail-closed: the path is then graded as unregistered)."""
    out = {}
    for op in plan_doc.get("ops", ()):
        if not isinstance(op, dict) or op.get("op") != "move-file":
            continue
        dest = op.get("destination")
        if isinstance(dest, str) and dest.startswith(MOVED_ROOT + "/"):
            out[dest] = op.get("source_digest")
    return out


def move_destinations(plan_doc):
    """The recorded Move destinations of one admitted plan's move rows, occupying or not (_move_creates).
    The retirement-phase transaction creates exactly these files (spec 14.2), so they are registered at
    file level only through the recorded retirement inventory that lists them; before that, an entry at
    one of them is a finding."""
    return sorted(_move_creates(plan_doc))


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
        return _hermetic(_self_test_checks)
    except Exception as exc:  # noqa: BLE001  final fail-closed backstop, never an uncaught exit-1 escape
        print("OPF-ADOPT-STATE SELF-TEST: harness error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return 2


def _hermetic(run):
    """Self-test only (QA round 6): run `run()` hermetically. The process environment is REPLACED, never
    inherited: PATH holds only the resolved git's directory and the platform default, HOME and
    XDG_CONFIG_HOME name a private empty directory (no global git config, hooks path, excludes file or
    attributes file reaches a fixture), GIT_CONFIG_NOSYSTEM drops the system git config wherever a git
    call carries it, every other ambient GIT_* variable is gone, TMPDIR pins the resolved temporary root,
    LC_ALL, LANG and TZ give child processes the C locale and the UTC zone, and the umask is 022. Only the
    environment changes for TZ and the locale: time.tzset() and locale.setlocale() are not called, so this
    process keeps its own zone and locale. The environment and the umask are restored afterwards."""
    import shutil
    import tempfile
    git = shutil.which("git")
    temp_root = tempfile.gettempdir()
    path = os.pathsep.join(([os.path.dirname(git)] if git else []) + [os.defpath])
    saved_env, saved_umask = dict(os.environ), os.umask(0o022)
    try:
        with tempfile.TemporaryDirectory(prefix="opf-adopt-state-home-") as home:
            os.environ.clear()
            os.environ.update(PATH=path, HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"),
                              GIT_CONFIG_NOSYSTEM="1", TMPDIR=temp_root, LC_ALL="C", LANG="C", TZ="UTC")
            return run()
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        os.umask(saved_umask)


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
    moved = store.moved_dest(move)   # the plan's recorded move destination (QA round 3)
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

    def freeze(root, head, nonce, srcs=None, decs=None, dests=None):
        """(run id, plan bytes, approval bytes) from the real planner and approval capture, or None.
        `dests` maps a move source to its explicit destination (spec 14.2); any other move takes the
        default."""
        srcs = sources if srcs is None else srcs
        decs = decisions if decs is None else decs
        dests = {} if dests is None else dests
        bindings = dict(schema.canonical_plan_bindings(), revision=head)
        views = sorted(v["target"] for v in legacy_manifest["views"].values())
        rows = [dict(op="init-store", store_root=".", members=[dict(
                    path=mrel + "/manifest.toml", digest=digest(manifest_text.encode("utf-8")))]),
                dict(op="render-views", store_root=".",
                     members=[dict(path=v, digest=digest(v.encode("utf-8"))) for v in views]),
                schema.enforcement_install_op(bindings["enforcement"])]
        sheet = dict(sources=sorted(srcs), targets=[".opf/hooks/pre-commit"] + sorted(dests.values()),
                     product="opf",
                     decisions=[dict(path=p, disposition=d, actor="fixture",
                                     **(dict(destination=dests[p]) if p in dests else {}))
                                for p, d in sorted(decs.items())],
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

        def plan_digest_of(plan_bytes):
            return tomllib.loads(plan_bytes.decode("utf-8"))["plan_digest"]

        pdig = dict((r, plan_digest_of(runs[r][0])) for r in runs)

        def emit_inv(rid_, rows, phase=None):
            """Fixture inventories carry the run's own [adoption] identity (spec 4.2): its run id, the
            phase (base when None) and its real frozen plan's own digest."""
            return apply.emit_inventory(rid_, rows, phase, pdig[rid_])
        home = apply.evidence_home_rel(rid)
        plan_rel, approval_rel = home + "/" + apply.PLAN_NAME, home + "/" + apply.APPROVAL_NAME
        imp_run = "imp-20260917T120000Z-0123456789abcdef"
        seq = [0]

        def listed(rid_):
            # The apply-stage creates (spec 14.1 check 3): the plan, the approval and the retirement
            # preimages preserved at APPLY for the retire and migrate rows; never a Move-root path (the
            # retirement-phase transaction is what creates the Move destination, spec 14.2).
            h = apply.evidence_home_rel(rid_)
            return [h + "/" + apply.PLAN_NAME, h + "/" + apply.APPROVAL_NAME,
                    apply.archive_rel(rid_, old), apply.archive_rel(rid_, note), apply.archive_rel(rid_, mig)]

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
            write(root, apply.inventory_rel(rid_), emit_inv(rid_, rows))
            commit(root, rid_)

        def bundle(root, rid_, plan_bytes=None, approval_bytes=None):
            h = apply.evidence_home_rel(rid_)
            write(root, h + "/" + apply.PLAN_NAME, plan_bytes if plan_bytes is not None else runs[rid_][0])
            write(root, h + "/" + apply.APPROVAL_NAME, approval_bytes if approval_bytes is not None else runs[rid_][1])
            for src in (old, note, mig):
                write(root, apply.archive_rel(rid_, src), sources[src])   # preserved at APPLY (14.1 check 3)
            relist(root, rid_)

        def retire(root, rid_, creates=None):
            """Record the run's retirement exactly as the retirement-phase transaction does (spec 14.2):
            the REAL compose path (apply.ApplyOps at the retirement phase) creates the plan's recorded
            Move destination from the frozen source bytes and seals the inventory DERIVED from its own
            create ops, so the record lists exactly the plan's Move destinations; the fixture publishes
            those staged bytes, plus the journal transaction a real apply would also leave (never read).
            `creates` lists (destination, bytes) for a plan other than the default fixture's one move."""
            fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                shell = apply.ApplyOps(fd, rid_, RETIREMENT_PHASE)
                for rel_, data in (((moved, sources[move]),) if creates is None else creates):
                    shell.create(rel_, data)
                shell.seal()
            finally:
                os.close(fd)
            check("retirement-shell-reproves", apply.check_apply_ops(
                rid_, RETIREMENT_PHASE, shell.ops, shell.staged) == [])
            for rel_, data in sorted(shell.staged.items()):
                write(root, rel_, data)
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
              and any(MOVED_ROOT in m and "unregistered" in m for m in rep.findings)
              and len(rep.findings) == 3 and rep.cannot == [])
        # Once the retirement is recorded the registered destination sits beside the stray: the stray
        # stays graded one by one, never silently covered by the Move root.
        retire(root, rid)
        rep = contain(root)
        check("retired-move-root-stray-still-graded",
              named(rep.findings, MOVED_ROOT + "/hide", "unregistered") and rep.cannot == [])
        # QA round 6: a stray DIRECTLY beside the registered destination, in that destination's own
        # directory, is graded by name at file level; the registered row beside it never covers it.
        root = tree()
        retire(root, rid)
        for src in (old, note, move):
            os.unlink(root / src)   # the retirement's pinned removals
        os.rmdir(root / note.rsplit("/", 1)[0])
        beside = moved.rsplit("/", 1)[0] + "/evil.sh"
        write(root, beside, b"evil\n")
        rep = contain(root)
        check("retired-stray-beside-move-destination-graded",
              named(rep.findings, beside, "unregistered") and len(rep.findings) == 1 and rep.cannot == [])

        # S2c (QA rounds 3 and 4): the committed inventories the vectors verify are the ones the REAL
        # compose path derives. The base: an ApplyOps apply-stage shell over an empty scratch root
        # creates every listed path with the fixture bytes and seals; check_apply_ops accepts the
        # finished list and the inventory it derives from its own create ops is byte-identical to the
        # fixture's, which admits unchanged. The retirement inventory of every retired fixture is
        # derived the same way (retire() composes the real retirement-phase ApplyOps create of the Move
        # destination and seals), so the doctor's shape check runs against the real writer's output.
        root = tree()
        scratch = base / "s2c"
        scratch.mkdir()
        sfd = os.open(str(scratch), os.O_RDONLY | os.O_DIRECTORY)
        try:
            shell = apply.ApplyOps(sfd, rid)
            for rel_ in listed(rid):
                shell.create(rel_, (root / rel_).read_bytes())
            shell.seal()
        finally:
            os.close(sfd)
        check("derived-inventory-is-the-committed-inventory",
              apply.check_apply_ops(rid, None, shell.ops, shell.staged) == []
              and shell.staged[apply.inventory_rel(rid)] == (root / apply.inventory_rel(rid)).read_bytes())
        rep = contain(root)
        check("derived-inventory-admits", rep.findings == [] and rep.cannot == [])

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
        # stays bounded. Recorded means committed evidence only (QA rounds 2 and 4): a sealed retirement
        # inventory in the shape the retirement-phase transaction derives from its own create ops, exactly
        # the plan's recorded Move destinations at the moved source's plan digest and nothing else, no
        # path the base inventory also claims, and live byte for byte; anything else is CANNOT-EVALUATE,
        # never read as retired.
        marker = apply.inventory_rel(rid, RETIREMENT_PHASE)
        root = tree()
        write(root, marker, emit_inv(rid, [], RETIREMENT_PHASE))
        rep = contain(root)
        check("retirement-marker-unproven-cannot", named(rep.cannot, marker) and bounded(rep) == [])
        os.unlink(root / old)
        os.unlink(root / note)
        rep = contain(root)
        check("unproven-retirement-never-hides-vanished-sources", named(rep.cannot, marker)
              and bounded(rep) == [])
        # A retire-only plan's record lists no files (the retirement-phase transaction of a plan without
        # a move row creates nothing but its own inventory): the SAME empty sealed marker reads as
        # retired at the unit seam once the plan carries no move row; the fixture plan always has one, so
        # the doctor-level vector above stays CANNOT.
        plan_doc_fix = tomllib.loads(runs[rid][0].decode("utf-8"))
        retire_only = dict(plan_doc_fix, sources=[dict(r) for r in plan_doc_fix["sources"]
                                                  if r.get("disposition") != "move"],
                           ops=[dict(r) for r in plan_doc_fix["ops"] if r.get("op") != "move-file"])
        unit_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
        try:
            unit = reader._retired(unit_fd, rid, retire_only, {}, [reader.READ_CEILING])
        finally:
            os.close(unit_fd)
        check("retire-only-plan-empty-record-retired", unit == (True, [marker]))
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
        # S10c (QA rounds 2 and 4): each half of the retirement shape has its own failing vector. Half 1,
        # the member set: a marker that does not name the plan's Move destination, the base inventory
        # misplaced at the retirement name, and an old-shape marker that re-lists the archive preimages
        # the base inventory already claims (spec 4.2: every payload file is claimed by exactly one row)
        # are each CANNOT, never read as retired.
        root = tree()
        retire(root, rid)
        write(root, marker, emit_inv(rid, [], RETIREMENT_PHASE))
        rep = contain(root)
        check("retirement-missing-destination-cannot", named(rep.cannot, marker, "destination")
              and bounded(rep) == [])
        root = tree()
        write(root, marker, (root / apply.inventory_rel(rid)).read_bytes())
        rep = contain(root)
        check("retirement-marker-is-base-inventory-cannot", named(rep.cannot, marker)
              and bounded(rep) == [])
        root = tree()
        write(root, marker, emit_inv(rid, [apply.inventory_row(
            apply.archive_rel(rid, src), sources[src]) for src in (old, note, move)], RETIREMENT_PHASE))
        rep = contain(root)
        check("retirement-preimage-shape-is-stale-cannot", named(rep.cannot, marker, "Move destination")
              and bounded(rep) == [])
        # QA round 3, phase binding: a base inventory CRAFTED to also list a live archive preimage of the
        # move row admits, and copying it unchanged to the retirement name must still be CANNOT, never
        # retired: the retirement record's member set is exactly the plan's Move destinations, and a
        # copied base lists at least the plan and the approval.
        root = tree()
        write(root, apply.archive_rel(rid, move), sources[move])
        relist(root, rid, listed(rid) + [apply.archive_rel(rid, move)])
        rep = contain(root)
        check("crafted-base-with-preimages-admits", rep.findings == [] and rep.cannot == []
              and bounded(rep) == sorted((old, note, mig, move)))
        write(root, marker, (root / apply.inventory_rel(rid)).read_bytes())
        rep = contain(root)
        check("retirement-marker-copied-crafted-base-cannot", named(rep.cannot, marker)
              and bounded(rep) == [])
        # The seal half has its own failing vector (QA round 3): a retirement marker whose bytes are not
        # the canonical emission of its own rows (a trailing comment) is CANNOT, never read as retired.
        root = tree()
        retire(root, rid)
        write(root, marker, (root / marker).read_bytes() + b"# note\n")
        rep = contain(root)
        check("retirement-marker-not-sealed-cannot", named(rep.cannot, marker, "sealed")
              and bounded(rep) == [])
        # Half 2, one claim per path (codex round 4, QA round 5): a marker row whose path a base inventory
        # row also claims is a path claimed twice (spec 4.2). A digest contradiction (a crafted base
        # listing the destination at live but wrong bytes while the marker carries the plan digest), a
        # size-only contradiction (the marker row 100 bytes larger at the same digest) and an agreeing
        # double claim (both rows at the same size and digest) are each CANNOT, never read as retired.
        root = tree()
        write(root, moved, b"wrong moved bytes\n")
        relist(root, rid, listed(rid) + [moved])
        write(root, marker, emit_inv(rid, [apply.inventory_row(moved, sources[move])], RETIREMENT_PHASE))
        rep = contain(root)
        check("retirement-contradicts-base-cannot", named(rep.cannot, marker, "claimed twice")
              and bounded(rep) == [])
        root = tree()
        write(root, moved, sources[move])
        relist(root, rid, listed(rid) + [moved])
        write(root, marker, emit_inv(rid, [dict(apply.inventory_row(moved, sources[move]),
                                                size=len(sources[move]) + 100)], RETIREMENT_PHASE))
        rep = contain(root)
        check("retirement-size-contradiction-cannot", named(rep.cannot, marker, "claimed twice")
              and bounded(rep) == [])
        root = tree()
        write(root, moved, sources[move])
        relist(root, rid, listed(rid) + [moved])
        write(root, marker, emit_inv(rid, [apply.inventory_row(moved, sources[move])], RETIREMENT_PHASE))
        for src in (old, note, move):
            os.unlink(root / src)
        rep = contain(root)
        check("retirement-path-claimed-twice-agreeing-cannot", named(rep.cannot, marker, "claimed twice")
              and bounded(rep) == [])
        # A row that is neither a recorded Move destination nor base-claimed is CANNOT (the shape allows
        # nothing else).
        root = tree()
        retire(root, rid)
        write(root, marker, emit_inv(rid, [
            apply.inventory_row(moved, sources[move]),
            apply.inventory_row(apply.archive_rel(rid, keep), sources[keep])], RETIREMENT_PHASE))
        rep = contain(root)
        check("retirement-foreign-row-cannot", named(rep.cannot, marker) and bounded(rep) == [])
        # The recorded destination is live committed evidence (QA round 4): after the recorded
        # retirement its bytes must equal the retirement row and the moved source's plan digest, so tampered
        # bytes, a deleted destination, and a marker whose row is self-consistent with wrong live bytes
        # but not at the plan digest are each CANNOT.
        root = tree()
        retire(root, rid)
        write(root, moved, b"evil\n")
        rep = contain(root)
        check("retirement-destination-tampered-cannot",
              any("does not match its retirement inventory row" in m and rid in m for m in rep.cannot)
              and bounded(rep) == [])
        root = tree()
        retire(root, rid)
        os.unlink(root / moved)
        rep = contain(root)
        check("retirement-destination-absent-cannot",
              any("absent" in m and rid in m for m in rep.cannot) and bounded(rep) == [])
        root = tree()
        write(root, moved, b"evil\n")
        write(root, marker, emit_inv(rid, [apply.inventory_row(moved, b"evil\n")], RETIREMENT_PHASE))
        rep = contain(root)
        check("retirement-destination-not-at-plan-digest-cannot",
              named(rep.cannot, marker, "plan digest") and bounded(rep) == [])
        # Before a recorded retirement the Move destination does not exist (QA round 4): a file there,
        # with or without a crafted base row claiming it, is a premature-record finding and the run's
        # sources stay bounded.
        root = tree()
        write(root, moved, sources[move])
        relist(root, rid, listed(rid) + [moved])
        rep = contain(root)
        check("crafted-base-moved-row-still-premature-finding",
              named(rep.findings, moved, "before the recorded retirement")
              and bounded(rep) == sorted((old, note, mig, move)) and rep.cannot == [])

        def state_of(root):
            fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
            try:
                return reader.adoption_state(fd, mrel, in_repo=True)
            finally:
                os.close(fd)

        # QA round 5: a Move destination is registered only once its retirement is recorded, never before
        # (the retirement-phase transaction is what creates it); after, it is registered through the
        # retirement inventory that lists it.
        root = tree()
        before = state_of(root)
        retire(root, rid)
        after = state_of(root)
        check("move-destination-registered-only-once-retired",
              moved not in before.registered and before.cannot == ()
              and moved in after.registered and after.cannot == ())

        # S10d (QA round 5): EVERY move row contributes its Move destination to the retirement record,
        # occupying or not. The real planner freezes a plan with an occupying move (a view target, archived
        # and removed at apply, its `preservation` naming the adoption archive copy) and a non-occupying
        # one, each to its default destination under the Move root; the retirement-phase ApplyOps shell
        # creates both destinations with the bytes the relocation writes (the committed archive copy, the
        # frozen live source) and seals the inventory derived from its own create ops; that record reads
        # as retired.
        occ = ".working/TODO.md"
        occ_sources = dict(((move, sources[move]), (occ, b"legacy todo\n")))
        occ_decisions = dict(((move, "move"), (occ, "move")))
        occ_root = base / "plan-occ"
        for rel, data in occ_sources.items():
            write(occ_root, rel, data)
        occ_head = apply._selftest_git_commit(occ_root)
        frozen_o = freeze(occ_root, occ_head, "0011223344556677", occ_sources, occ_decisions) if occ_head else None
        check("occupying-fixture-frozen-by-the-real-planner", frozen_o is not None)
        if frozen_o is not None:
            rid_o = frozen_o[0]
            pdig[rid_o] = plan_digest_of(frozen_o[1])
            plan_o = tomllib.loads(frozen_o[1].decode("utf-8"))
            occ_dest, occ_arch = store.moved_dest(occ), apply.archive_rel(rid_o, occ)
            rows_o = dict((r["path"], r) for r in plan_o["sources"])
            ops_o = dict((r["source"], r["destination"]) for r in plan_o["ops"] if r["op"] == "move-file")
            check("occupying-move-shape-from-the-real-planner",
                  rows_o[occ]["occupying"] is True and rows_o[occ]["preservation"] == occ_arch
                  and rows_o[move]["occupying"] is False and rows_o[move]["preservation"] == moved
                  and ops_o == dict(((occ, occ_dest), (move, moved)))
                  and reader.move_destinations(plan_o) == sorted((occ_dest, moved)))
            home_o = apply.evidence_home_rel(rid_o)
            base_o = [home_o + "/" + apply.PLAN_NAME, home_o + "/" + apply.APPROVAL_NAME, occ_arch]
            marker_o = apply.inventory_rel(rid_o, RETIREMENT_PHASE)

            def occ_tree():
                # After apply: the occupying source is archived and replaced by its managed view, the
                # non-occupying source stays live and frozen, and the base inventory lists the plan, the
                # approval and the archive copy.
                seq[0] += 1
                root_ = base / ("t" + str(seq[0]))
                write(root_, mrel + "/manifest.toml", manifest_text.encode("utf-8"))
                write(root_, move, sources[move])
                write(root_, occ, b"rendered view\n")
                write(root_, base_o[0], frozen_o[1])
                write(root_, base_o[1], frozen_o[2])
                write(root_, occ_arch, occ_sources[occ])
                relist(root_, rid_o, base_o)
                return root_

            def occ_retire(root_, creates):
                fd = os.open(str(root_), os.O_RDONLY | os.O_DIRECTORY)
                try:
                    shell = apply.ApplyOps(fd, rid_o, RETIREMENT_PHASE)
                    for rel_, data_ in creates:
                        shell.create(rel_, data_)
                    shell.seal()
                finally:
                    os.close(fd)
                ok = apply.check_apply_ops(rid_o, RETIREMENT_PHASE, shell.ops, shell.staged) == []
                for rel_, data_ in sorted(shell.staged.items()):
                    write(root_, rel_, data_)
                commit(root_, rid_o, RETIREMENT_PHASE)
                return ok, shell.staged[marker_o]

            root = occ_tree()
            rep = contain(root)
            check("occupying-run-admits-before-retirement", rep.findings == [] and rep.cannot == []
                  and bounded(rep) == [move])
            write(root, occ_dest, occ_sources[occ])
            rep = contain(root)
            check("occupying-destination-premature-finding",
                  named(rep.findings, occ_dest, "before the recorded retirement") and rep.cannot == [])
            root = occ_tree()
            ok, record_o = occ_retire(root, [(occ_dest, (root / occ_arch).read_bytes()),
                                             (moved, (root / move).read_bytes())])
            os.unlink(root / move)   # the relocation's pinned removal of the non-occupying source
            check("occupying-retirement-record-derived", ok and [r["path"] for r in tomllib.loads(
                record_o.decode("utf-8"))["file"]] == sorted((occ_dest, moved)))
            rep = contain(root)
            st_o = state_of(root)
            check("occupying-retirement-reads-retired", rep.findings == [] and rep.cannot == []
                  and bounded(rep) == [] and occ_dest in st_o.registered and moved in st_o.registered)
            # A record that omits the occupying move's destination is not the derived record: CANNOT.
            root = occ_tree()
            occ_retire(root, [(moved, (root / move).read_bytes())])
            os.unlink(root / move)
            rep = contain(root)
            check("occupying-destination-omitted-cannot",
                  named(rep.cannot, marker_o, repr(occ_dest), "Move destination") and bounded(rep) == [])

        # S10e (QA round 6): only a destination beneath the Move root is retained evidence. The real
        # planner freezes a plan whose moves name explicit destinations outside the store, one occupying
        # (a view target) and one not, beside a default move. The relocation writes the outside
        # destinations, which no inventory lists, and the retirement-phase ApplyOps shell creates the one
        # Move-root destination and seals; that record reads as retired. Without the Move-root filter in
        # _move_creates the outside destinations would be demanded as rows and the run could never retire.
        ext, ext_dest, occx_dest = ".working/EXT.md", "adopter/EXT.md", "adopter/TODO-old.md"
        x_sources = dict(((move, sources[move]), (occ, b"legacy todo\n"), (ext, b"external\n")))
        x_decisions = dict(((move, "move"), (occ, "move"), (ext, "move")))
        x_root = base / "plan-ext"
        for rel, data in x_sources.items():
            write(x_root, rel, data)
        x_head = apply._selftest_git_commit(x_root)
        frozen_x = freeze(x_root, x_head, "8899aabbccddeeff", x_sources, x_decisions,
                          dict(((ext, ext_dest), (occ, occx_dest)))) if x_head else None
        check("explicit-destination-fixture-frozen-by-the-real-planner", frozen_x is not None)
        if frozen_x is not None:
            rid_x = frozen_x[0]
            pdig[rid_x] = plan_digest_of(frozen_x[1])
            plan_x = tomllib.loads(frozen_x[1].decode("utf-8"))
            occx_arch = apply.archive_rel(rid_x, occ)
            rows_x = dict((r["path"], r) for r in plan_x["sources"])
            ops_x = dict((r["source"], r["destination"]) for r in plan_x["ops"] if r["op"] == "move-file")
            check("explicit-destination-shape-from-the-real-planner",
                  rows_x[occ]["occupying"] is True and rows_x[ext]["occupying"] is False
                  and ops_x == dict(((move, moved), (ext, ext_dest), (occ, occx_dest)))
                  and reader.move_destinations(plan_x) == [moved])
            home_x = apply.evidence_home_rel(rid_x)
            base_x = [home_x + "/" + apply.PLAN_NAME, home_x + "/" + apply.APPROVAL_NAME, occx_arch]
            seq[0] += 1
            root = base / ("t" + str(seq[0]))
            write(root, mrel + "/manifest.toml", manifest_text.encode("utf-8"))
            write(root, move, sources[move])
            write(root, ext, x_sources[ext])
            write(root, occ, b"rendered view\n")
            write(root, base_x[0], frozen_x[1])
            write(root, base_x[1], frozen_x[2])
            write(root, occx_arch, x_sources[occ])
            relist(root, rid_x, base_x)
            rep = contain(root)
            check("explicit-destination-run-admits-before-retirement", rep.findings == [] and rep.cannot == []
                  and bounded(rep) == [move] and named(rep.migration_incomplete, ext))
            write(root, ext_dest, x_sources[ext])
            write(root, occx_dest, x_sources[occ])
            retire(root, rid_x)   # the real retirement-phase shell: the one Move-root destination, sealed
            os.unlink(root / move)
            os.unlink(root / ext)
            rep = contain(root)
            st_x = state_of(root)
            check("explicit-destination-outside-store-reads-retired", rep.findings == []
                  and rep.cannot == [] and rep.migration_incomplete == [] and moved in st_x.registered
                  and ext_dest not in st_x.registered and occx_dest not in st_x.registered)

        # S10f (QA round 7): an EXPLICIT destination beneath the Move root is a Move destination like the
        # default one (spec 4.2, 14.2). The real planner freezes a plan moving one source to a custom path
        # under .working/archive/moved/ beside a default move; both destinations are recorded, a file at the
        # custom one before the retirement is a premature finding, and the real retirement-phase ApplyOps
        # shell creates both and seals a record that reads as retired, registering the custom destination at
        # file level only. Counting only the default destination in _move_creates fails the shape, premature and
        # retired checks here.
        cust_dest = MOVED_ROOT + "/custom/EXT.md"
        c_sources = dict(((move, sources[move]), (ext, x_sources[ext])))
        c_root = base / "plan-custom"
        for rel, data in c_sources.items():
            write(c_root, rel, data)
        c_head = apply._selftest_git_commit(c_root)
        frozen_c = freeze(c_root, c_head, "1122334455667788", c_sources,
                          dict(((move, "move"), (ext, "move"))), dict(((ext, cust_dest),))) if c_head else None
        check("move-root-explicit-destination-frozen-by-the-real-planner", frozen_c is not None)
        if frozen_c is not None:
            rid_c = frozen_c[0]
            pdig[rid_c] = plan_digest_of(frozen_c[1])
            plan_c = tomllib.loads(frozen_c[1].decode("utf-8"))
            ops_c = dict((r["source"], r["destination"]) for r in plan_c["ops"] if r["op"] == "move-file")
            check("move-root-explicit-destination-shape-from-the-real-planner",
                  ops_c == dict(((move, moved), (ext, cust_dest)))
                  and reader.move_destinations(plan_c) == sorted((cust_dest, moved)))
            home_c = apply.evidence_home_rel(rid_c)
            base_c = [home_c + "/" + apply.PLAN_NAME, home_c + "/" + apply.APPROVAL_NAME]

            def cust_tree():
                seq[0] += 1
                root_ = base / ("t" + str(seq[0]))
                write(root_, mrel + "/manifest.toml", manifest_text.encode("utf-8"))
                for rel_, data_ in c_sources.items():
                    write(root_, rel_, data_)
                write(root_, base_c[0], frozen_c[1])
                write(root_, base_c[1], frozen_c[2])
                relist(root_, rid_c, base_c)
                return root_

            root = cust_tree()
            rep = contain(root)
            check("move-root-explicit-destination-run-admits-before-retirement", rep.findings == []
                  and rep.cannot == [] and bounded(rep) == [move] and named(rep.migration_incomplete, ext))
            write(root, cust_dest, c_sources[ext])
            rep = contain(root)
            check("move-root-explicit-destination-premature-finding",
                  named(rep.findings, cust_dest, "before the recorded retirement") and rep.cannot == [])
            root = cust_tree()
            retire(root, rid_c, [(cust_dest, c_sources[ext]), (moved, c_sources[move])])
            os.unlink(root / move)
            os.unlink(root / ext)
            beside_c = MOVED_ROOT + "/custom/evil.sh"
            write(root, beside_c, b"evil\n")
            rep = contain(root)
            st_c = state_of(root)
            check("move-root-explicit-destination-reads-retired",
                  named(rep.findings, beside_c, "unregistered") and len(rep.findings) == 1 and rep.cannot == []
                  and rep.migration_incomplete == [] and cust_dest in st_c.registered
                  and moved in st_c.registered and beside_c not in st_c.registered)

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
        record("phase-inventory-without-base", both(drop(inventory), put(marker, emit_inv(rid, [], RETIREMENT_PHASE))),
               "cannot", "without inventory.toml")
        record("plan-not-listed", lambda root: relist(root, rid, [approval_rel]), "finding", "not listed")
        record("plan-listed-but-absent", drop(plan_rel), "finding", "absent")
        record("inventory-symlink", both(drop(inventory), lambda root: os.symlink("plan.toml", root / inventory)),
               "cannot")
        record("inventory-not-toml", put(inventory, b"not toml [\n"), "cannot", "malformed TOML")
        record("inventory-path-claimed-twice", put(inventory, apply.emit_checked(dict(
            format=store.EVIDENCE_INVENTORY_FORMAT,
            adoption=dict(run_id=rid, phase=apply.BASE_PHASE, plan_digest=pdig[rid]),
            file=[row, row])).encode("utf-8")), "cannot", "more than once")
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
        record("inventory-of-another-run", put(inventory, emit_inv(rid_b, [apply.inventory_row(
            apply.evidence_home_rel(rid_b) + "/" + apply.PLAN_NAME, runs[rid_b][0])])), "cannot",
            "names run")
        record("archive-row-absent", drop(apply.archive_rel(rid, mig)), "finding", "absent")
        record("archive-row-tampered", put(apply.archive_rel(rid, mig), b"evil\n"), "finding",
               "does not match")
        # QA rounds 3 and 4: a base inventory in the spec shape never lists a Move-root row (the
        # retirement-phase transaction is what creates the destination), but a crafted base that does
        # list one is still committed evidence: a listed Move-root file that no move row of this run's
        # plan explains, tampered bytes at the listed destination, and its deletion each refuse
        # admission.
        evil = MOVED_ROOT + "/hide/evil.sh"

        def plant_unexplained(root):
            write(root, evil, b"evil\n")
            relist(root, rid, listed(rid) + [evil])

        def craft_moved_base(root):
            write(root, moved, sources[move])
            relist(root, rid, listed(rid) + [moved])

        record("moved-row-unexplained", plant_unexplained, "finding", "no move row")
        record("moved-row-tampered", both(craft_moved_base, put(moved, b"evil\n")), "finding",
               "does not match")
        record("moved-row-absent", both(craft_moved_base, drop(moved)), "finding", "absent")

        # The run-binding ruling (PD-INVENTORY-RUN-BINDING option 1; codex round 6): every adoption
        # inventory carries its [adoption] identity (run id, phase, plan digest), written by the apply
        # side when it derives the inventory and checked by the doctor, so an inventory copied from
        # another run or phase, and one with a missing or malformed identity, never evaluates. Each
        # vector fails without the identity check: the unit seam uses the retire-only plan, whose empty
        # sealed record otherwise reads as retired (the round-6 codex reproduction).
        def unit_retired(root_):
            fd = os.open(str(root_), os.O_RDONLY | os.O_DIRECTORY)
            try:
                try:
                    return reader._retired(fd, rid, retire_only, {}, [reader.READ_CEILING]), None
                except apply.AdoptApplyError as exc:
                    return None, str(exc)
            finally:
                os.close(fd)

        # 1 (the round-6 reproduction): another run's EMPTY sealed retirement record, copied byte for
        # byte onto this run's retirement name, refuses at the unit seam and is CANNOT at the doctor,
        # so the vanished frozen retire sources stay visible rather than suppressed.
        root = tree()
        write(root, marker, emit_inv(rid_b, [], RETIREMENT_PHASE))
        os.unlink(root / old)
        os.unlink(root / note)
        unit, why = unit_retired(root)
        rep = contain(root)
        check("retirement-record-of-another-run-refused", unit is None and why is not None
              and "names run" in why and named(rep.cannot, marker) and bounded(rep) == [])
        # 2: another phase's identity at the retirement name (an empty completion-phase record copied
        # onto it) refuses, never read as retired.
        root = tree()
        write(root, marker, emit_inv(rid, [], "completion"))
        unit, why = unit_retired(root)
        rep = contain(root)
        check("retirement-record-of-another-phase-refused", unit is None and why is not None
              and "another phase" in why and named(rep.cannot, marker) and bounded(rep) == [])
        # 2a: a well-formed plan digest other than the proven plan's own at the retirement name (the
        # record of another plan) refuses, never read as retired: at the unit seam the retire-only plan's
        # empty record, and through the doctor the plan's own retirement shape re-emitted at that digest,
        # each of which otherwise reads as retired.
        foreign_digest = "sha256:" + "0" * 64
        root = tree()
        write(root, marker, apply.emit_inventory(rid, [], RETIREMENT_PHASE, foreign_digest))
        unit, why = unit_retired(root)
        check("retirement-record-foreign-plan-digest-refused", unit is None and why is not None
              and "plan digest" in why)
        root = tree()
        retire(root, rid)
        doc_ret = tomllib.loads((root / marker).read_bytes().decode("utf-8"))
        write(root, marker, apply.emit_inventory(rid, [dict(r) for r in doc_ret["file"]], RETIREMENT_PHASE,
                                                 foreign_digest))
        rep = contain(root)
        check("retirement-record-foreign-plan-digest-cannot", named(rep.cannot, marker, "plan digest")
              and bounded(rep) == [])
        # 2b: the base inventory's identity binds it to the base phase: the base re-emitted with the
        # retirement-phase identity (the same rows at the same plan digest) refuses admission.
        root = tree()
        doc_base = tomllib.loads((root / inventory).read_bytes().decode("utf-8"))
        write(root, inventory, apply.emit_inventory(rid, [dict(r) for r in doc_base["file"]],
                                                    RETIREMENT_PHASE, pdig[rid]))
        rep = contain(root)
        check("base-inventory-of-another-phase-refused",
              any(rid in m and "another phase" in m for m in rep.findings)
              and rep.cannot == [] and bounded(rep) == [])
        # 3: a missing identity (the pre-ruling record shape) and a malformed one (an extra key) are
        # each CANNOT, at the retirement name and at the base.
        root = tree()
        write(root, marker, apply.emit_checked(dict(format=store.EVIDENCE_INVENTORY_FORMAT,
                                                    file=[])).encode("utf-8"))
        unit, why = unit_retired(root)
        rep = contain(root)
        check("retirement-record-identity-missing-refused", unit is None and why is not None
              and "identity" in why and named(rep.cannot, marker) and bounded(rep) == [])
        record("inventory-identity-missing", put(inventory, apply.emit_checked(dict(
            format=store.EVIDENCE_INVENTORY_FORMAT,
            file=[dict(r) for r in doc_base["file"]])).encode("utf-8")), "cannot", "identity")
        record("inventory-identity-malformed", put(inventory, apply.emit_checked(dict(
            format=store.EVIDENCE_INVENTORY_FORMAT,
            adoption=dict(run_id=rid, phase=apply.BASE_PHASE, plan_digest=pdig[rid], note=1),
            file=[dict(r) for r in doc_base["file"]])).encode("utf-8")), "cannot", "identity")
        # 4: a base identity naming a plan digest other than the proven plan's own refuses admission.
        record("inventory-identity-foreign-plan-digest", put(inventory, apply.emit_inventory(
            rid, [dict(r) for r in doc_base["file"]], None, "sha256:" + "0" * 64)), "finding",
            "not its own proven")

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
        # The journal-free property is pinned at the contained-read seam (QA rounds 3 and 4):
        # component-wise opens never spell JOURNAL_REL, so an os.open spy can never see it; the spies
        # below record every contained lstat, read AND directory listing BY ITS RELATIVE PATH, so one
        # injected journal read or journal directory listing fails this vector.
        touched = []
        real_rc, real_ls = _journal._read_contained, _journal._lstat_contained
        real_list = _opf_check._list_contained

        def spy_read(fd, rel, **kwargs):
            touched.append(rel)
            return real_rc(fd, rel, **kwargs)

        def spy_lstat(fd, rel):
            touched.append(rel)
            return real_ls(fd, rel)

        def spy_list(fd, rel):
            touched.append(rel)
            return real_list(fd, rel)

        effect = None
        try:
            with mock.patch.object(apply, "journal_state", side_effect=AssertionError("journal consulted")), \
                    mock.patch.object(_journal, "open_journal_root_fd",
                                      side_effect=AssertionError("journal opened")), \
                    mock.patch.object(_journal, "classify_state", side_effect=AssertionError("journal read")), \
                    mock.patch.object(_journal, "read_frames", side_effect=AssertionError("journal read")), \
                    mock.patch.object(_journal, "_read_contained", spy_read), \
                    mock.patch.object(_opf_check, "_list_contained", spy_list), \
                    mock.patch.object(_journal, "_lstat_contained", spy_lstat), apply._composing():
                rep = contain(root)
        except (apply.AdoptApplyError, AssertionError) as exc:
            effect = exc
        check("read-only-no-effect", effect is None and rep.findings == [] and rep.cannot == [])
        check("journal-never-read", touched != [] and all(
            t != apply.JOURNAL_REL and not t.startswith(apply.JOURNAL_REL + "/") for t in touched))
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
        # QA rounds 3 and 4: bytes a FAILED read consumed count too, charged exactly: the first
        # inventory read crosses the small per-file cap having consumed its whole (single-chunk) read,
        # the call budget exhausts and the second run's read is refused before it happens.
        root = tree(adopted=(rid, rid_b))
        failed_reads = []
        with mock.patch.object(reader, "READ_CEILING", 8), \
                mock.patch.object(_journal, "_MAX_PRODUCT_READ_BYTES", 16), \
                mock.patch.object(apply, "_read_live",
                                  lambda fd, rel: failed_reads.append(rel) or real_read(fd, rel)):
            rep = contain(root)
        check("failed-read-charges-budget", len(failed_reads) == 1
              and any("exhausted" in m for m in rep.cannot))
        # QA round 4 (exact accounting, codex and claude): a refusal BEFORE the first byte charges
        # nothing, and a per-file-cap crossing charges exactly the bytes its chunk loop consumed (the
        # whole 10-byte file here), never the per-file cap.
        root = tree(adopted=())
        write(root, ".working/ten.md", b"0123456789")
        os.symlink("ten.md", root / ".working/link.md")
        charge_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
        try:
            b1 = [1024]
            try:
                reader._read_budgeted(charge_fd, ".working/link.md", b1)
                refused = False
            except apply.AdoptApplyError:
                refused = True
            check("failed-read-before-first-byte-charges-nothing", refused and b1 == [1024])
            b2 = [1024]
            with mock.patch.object(_journal, "_MAX_PRODUCT_READ_BYTES", 4):
                try:
                    reader._read_budgeted(charge_fd, ".working/ten.md", b2)
                    capped = None
                except apply.AdoptApplyError as exc:
                    capped = str(exc)
            check("failed-read-charged-exactly-consumed", capped is not None
                  and "read cap" in capped and b2 == [1024 - 10])
        finally:
            os.close(charge_fd)
        # The crossing read itself is reported (the check AFTER the read): a read that succeeds and lands
        # past the ceiling raises, even when it is the call's final read.
        root = tree(adopted=())
        write(root, ".working/ten.md", b"0123456789")
        cross_fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)
        try:
            crossing = [8]
            try:
                reader._read_budgeted(cross_fd, ".working/ten.md", crossing)
                crossed = None
            except apply.AdoptApplyError as exc:
                crossed = str(exc)
        finally:
            os.close(cross_fd)
        check("crossing-final-read-reported", crossed is not None and "exceeds" in crossed
              and crossing[0] == -2)

        # S18 (QA rounds 1 and 4): the move row, frozen by the real planner: bounded, drifted and
        # vanished grade exactly as retire does. Before the recorded retirement the plan's Move
        # destination does not exist, so a file there is a named premature finding; the destination is
        # registered, byte-verified, only through the recorded retirement inventory (S10).
        root = tree()
        rep = contain(root)
        check("move-source-bounded-no-premature-finding", rep.findings == [] and rep.cannot == []
              and named(rep.migration_incomplete, move))
        write(root, store.moved_dest(move), sources[move])
        rep = contain(root)
        check("premature-move-destination-finding",
              named(rep.findings, store.moved_dest(move), "before the recorded retirement")
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
            # Plumbing only (QA round 6): stage the whole tree, then write-tree, commit-tree and
            # update-ref, so no hook, template or signing runs; the system and global git config are
            # both dropped and the identity and date pinned, on top of the hermetic environment.
            git = shutil.which("git")
            if git is None:
                return False
            env = dict((k, v) for k, v in os.environ.items() if not k.startswith("GIT_"))
            env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_AUTHOR_NAME="fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                       GIT_COMMITTER_NAME="fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
                       GIT_AUTHOR_DATE="2026-01-01T00:00:00Z", GIT_COMMITTER_DATE="2026-01-01T00:00:00Z")
            where = str(root)
            try:
                subprocess.run([git, "-C", where, "add", "-A"], stdin=subprocess.DEVNULL, capture_output=True,
                               env=env, timeout=60, check=True)
                tree_id = subprocess.run([git, "-C", where, "write-tree"], stdin=subprocess.DEVNULL,
                                         capture_output=True, env=env, timeout=60,
                                         check=True).stdout.decode("ascii").strip()
                parent = subprocess.run([git, "-C", where, "rev-parse", "--verify", "HEAD^{commit}"],
                                        stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=60,
                                        check=True).stdout.decode("ascii").strip()
                made = subprocess.run([git, "-C", where, "commit-tree", "--no-gpg-sign", "-p", parent, "-m",
                                       "fixture", tree_id], stdin=subprocess.DEVNULL, capture_output=True,
                                      env=env, timeout=60, check=True).stdout.decode("ascii").strip()
                subprocess.run([git, "-C", where, "update-ref", "HEAD", made], stdin=subprocess.DEVNULL,
                               capture_output=True, env=env, timeout=60, check=True)
            except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
                return False
            return True

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
        # QA round 3, end to end: with the machine-local journal tree removed and that removal committed,
        # as in a clone that never carried it, the SAME real-doctor grading returns: admission never
        # requires the journal, pinned through validate_store itself, not only contain().
        clone_result = None
        if ok and result is not None:
            shutil.rmtree(e2e / apply.JOURNAL_REL.split("/")[0])
            if git_commit_all(e2e):
                res = store.resolve_store(e2e)
                if res.status == store.RESOLVED:
                    obs, _notes = _opf_observe.gather(res)
                    clone_result = _opf_check.validate_store(res, observations=obs)
        check("s16-journal-free-grades-identically", clone_result is not None
              and clone_result.status == store.VALID
              and clone_result.checks.get("C-CONTAINMENT") == "PASS"
              and len(clone_result.migration_incomplete) == 4
              and clone_result.findings == [] and clone_result.cannot_evaluate == [])

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
