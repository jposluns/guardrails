#!/usr/bin/env python3
"""Migration engine for the OPT-IN adopter path (VER-CORE 9.1 to 9.3, Section 12 step 6). Stdlib only.

  migrate.py plan     --root DIR                          crosswalk connected components (units, 9.1)
  migrate.py cutover  --root DIR --staged DIR --unit ID   journaled transactional apply (9.2, 9.3)
  migrate.py recover  --root DIR                          reconcile an open journal (idempotent, 9.3)
  migrate.py status   --root DIR                          report journal / transaction state
  migrate.py --self-test                                  the MANDATORY crash-injection gate (9.3)

Exit convention: 0 clean/NA, 1 finding, 2 malformed input, a read error, or a refused precondition.

The engine consumes two interfaces owed by the adopter-experience spec and refuses without their evidence
(fail-closed, never a silent proceed): QUIESCENCE of the effective tree (a `quiescence.ok` marker the
adopter-experience hook drops in the staged unit) and the OFF-PATH placement of the staged unit (asserted
here: the staged tree must resolve outside the adopter root, so a cutover can never stage into a location
that auto-loads). It also fails closed before the lock on a platform without the race-free containment
primitive (3.6b). The 9.3 crash-safety model, the seven normative steps, and the recovery election live
in tools/_journal.py; this CLI wires the staged-unit contract, the lock reconcile, and the self-test.

Staged-unit contract (the off-path tree a verified, green step-2/3 build produced, 9.2):
  <staged>/quiescence.ok        the adopter-experience quiescence evidence (its presence is required)
  <staged>/plan.json            {"ops": [ ... ]} in DEPENDENCY ORDER (parents-before-children creates,
                                children-before-parents removes); each op is {"op", "path"[, "mode"]}
                                with op one of write|create|remove|mkdir|rmdir
  <staged>/payload/<path>       the exact new bytes for every write and create op
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: migrate.py requires Python 3.11+ (tomllib).")

JOURNAL_REL = ".aiqt/migration/journal"
CROSSWALK_REL = ".aiqt/migration/crosswalk.toml"


class RefuseError(Exception):
    """A refused precondition (missing quiescence evidence, an on-path staged tree, a bad staged plan):
    fail-closed, exit 2."""


# --- units: connected components of the crosswalk graph (9.1) ------------------------------------------

def components(crosswalk):
    """9.1: a UNIT is a connected component of the crosswalk graph over predecessor-successor edges, so a
    fold (many predecessors, one successor) or a split (one predecessor, many successors) moves as ONE
    unit. Union-find over the mapping rows; returns {root-key: [mapping-row, ...]} deterministically."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for m in crosswalk.get("mapping", []):
        union("p:" + m["predecessor-clause-id"], "s:" + m["successor-clause-id"])
    groups = {}
    for m in crosswalk.get("mapping", []):
        groups.setdefault(find("p:" + m["predecessor-clause-id"]), []).append(m)
    return groups


def _component_membership(rows):
    """The mapped predecessor and successor clause-id sets of one component (9.1), sorted and unique, as
    recorded in the cutover INTENT header and gated by check_crosswalk's whole-component-coverage leg."""
    return {"predecessors": sorted({m["predecessor-clause-id"] for m in rows}),
            "successors": sorted({m["successor-clause-id"] for m in rows})}


def load_crosswalk(root):
    path = root / CROSSWALK_REL
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise RefuseError("no crosswalk at {} (nothing to plan)".format(CROSSWALK_REL))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise RefuseError("cannot read {} ({})".format(CROSSWALK_REL, exc))


# --- staged-unit contract -----------------------------------------------------------------------------

def _read_staged_plan(staged):
    """Parse and validate <staged>/plan.json into an ordered op list; compute each op's poststate from
    the staged payload (content digest for write/create) so the INTENT records the intended result."""
    plan_path = staged / "plan.json"
    try:
        raw = plan_path.read_bytes()
    except OSError as exc:
        raise RefuseError("cannot read staged plan {} ({})".format(plan_path, exc))
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise RefuseError("staged plan.json is not valid JSON ({})".format(exc))
    if not isinstance(doc, dict) or not isinstance(doc.get("ops"), list) or not doc["ops"]:
        raise RefuseError("staged plan.json must carry a non-empty 'ops' array")
    ops = []
    for i, raw_op in enumerate(doc["ops"]):
        where = "plan.json ops[{}]".format(i)
        if not isinstance(raw_op, dict):
            raise RefuseError("{}: not an object".format(where))
        kind = raw_op.get("op")
        path = raw_op.get("path")
        if kind not in _journal.OP_KINDS:
            raise RefuseError("{}: op must be one of {}".format(where, "/".join(_journal.OP_KINDS)))
        if not isinstance(path, str) or not path:
            raise RefuseError("{}: path must be a non-empty string".format(where))
        op = {"op": kind, "path": path}
        if kind in ("write", "create"):
            payload = _read_payload(staged, path, where)
            op["poststate"] = {"kind": "file", "content-sha256": hashlib.sha256(payload).hexdigest()}
            if kind == "create":
                op["poststate"]["mode"] = _mode_of(raw_op, 0o644)
        elif kind == "mkdir":
            op["poststate"] = {"kind": "dir", "mode": _mode_of(raw_op, 0o755)}
        else:                                             # remove, rmdir
            op["poststate"] = {"kind": "absent"}
        ops.append(op)
    return ops


def _mode_of(raw_op, default):
    mode = raw_op.get("mode", default)
    if not isinstance(mode, int) or not 0 <= mode <= 0o7777:
        raise RefuseError("op mode must be an octal int in [0, 0o7777]")
    return mode


def _read_payload(staged, relpath, where):
    _journal._check_rel(relpath)                          # reject traversal in a staged payload path
    try:
        return (staged / "payload" / relpath).read_bytes()
    except OSError as exc:
        raise RefuseError("{}: missing staged payload for {!r} ({})".format(where, relpath, exc))


def _staged_reader(staged):
    def reader(op):
        return (staged / "payload" / op["path"]).read_bytes()
    return reader


def _assert_off_path(root, staged):
    """9.2: the staged unit must resolve OUTSIDE the adopter root, so a cutover can never promote from a
    location the tree auto-loads. Fail-closed on an on-path or equal staged tree."""
    r = root.resolve()
    s = staged.resolve()
    if s == r or r in s.parents:
        raise RefuseError("staged tree {} is inside the adopter root {} (must be off-path, 9.2)"
                          .format(s, r))


def _require_quiescence(staged):
    if not (staged / "quiescence.ok").is_file():
        raise RefuseError("staged unit carries no quiescence evidence (quiescence.ok); the engine "
                          "refuses a cutover outside proven quiescence (adopter-experience hook)")


# --- subcommands --------------------------------------------------------------------------------------

def _open_root_fd(root):
    # O_NOFOLLOW binds the adopter root itself against a final-component symlink swap (codex crash-safety
    # hardening): a root whose final component is a symlink is refused rather than followed off-tree.
    return os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _open_root_or_none(root):
    """Open the adopter root fd with O_NOFOLLOW; return (fd, None) on success or (None, message) so a
    symlinked or unreadable --root is REFUSED at the CLI (exit 2) rather than crashing with a traceback
    (fix #8: main keeps an unresolved abspath root, so O_NOFOLLOW here is the leg that actually refuses a
    symlinked final root component)."""
    try:
        return _open_root_fd(root), None
    except OSError as exc:
        return None, ("cannot open --root {} ({}); a symlinked or unreadable root is refused "
                      "(O_NOFOLLOW, 3.6b); fail-closed".format(root, exc))


def _validated_completed_cutover(txn_dir):
    """Fix #3 (C2 in all consumers): classify a transaction through the SINGLE validated terminal state
    machine and return its INTENT object ONLY when it is a genuinely COMPLETE cutover eligible for
    coverage-gating (and, in Step 7, reverse-replay): the frame sequence is exactly [INTENT, COMPLETE]
    (via classify_state, which runs _validate_terminal_agreement, so a mismatched-txn or invalid sequence
    is rejected) AND the INTENT header names kind == "cutover". Returns the INTENT dict for such a
    transaction, or None when it is not a completed cutover (a rolled-back, still-open, un-adopt, or other
    non-cutover terminal journal). JournalError (fail-closed) on a corrupt or invalid-sequence journal.
    Used by check_crosswalk's whole-component coverage gate; it exposes no un-adopt CLI (that is Step 7)."""
    if _journal.classify_state(txn_dir) != "complete":       # runs the C2 validator; raises on an invalid sequence
        return None
    frames, _torn, _ = _journal.read_frames(txn_dir)
    if [t for t, _ in frames] != [_journal.F_INTENT, _journal.F_COMPLETE]:
        return None
    intent = _journal._first(frames, _journal.F_INTENT)
    header = (intent or {}).get("header", {})
    if not isinstance(header, dict) or header.get("kind") != "cutover":
        return None
    return intent


def _claim_recover_lock(journal_root, root_fd):
    """Atomically claim the journal lock for recovery (fix #3, hardened for C1 and E4). Returns 'acquired'
    when this process now owns the lock (the lock was absent, or a confirmed-dead stale lock was reconciled
    and broken), or 'possibly-live' when a lock whose owner may still be alive holds it (never seized, the
    concurrency-lease rule). The confirmed-dead case is SERIALIZED under a kernel arbitration lock
    (reconcile_and_claim_stale, C1+E4): it re-reads the CURRENT owner, and per spec 1262 breaks the stale
    lock ONLY after reconciling every journal to a terminal state while RETAINING the stale lease, never
    unlinking a lock a concurrent recoverer already re-acquired live. Fail-closed (JournalError) on an
    unreadable lock, a lost O_EXCL race, or a journal that does not reconcile to terminal."""
    owner = _journal.read_lock_owner(journal_root)
    if owner is None:
        _journal.acquire_lock(journal_root, session_id="recover")
        return "acquired"
    if not _journal.owner_confirmed_dead(owner):
        return "possibly-live"
    # Confirmed dead: reconcile-then-break under the kernel arbitration lock (E4, spec 1262): the stale
    # lease is the recovery claim, retained until every journal validates terminal, and only then broken.
    return _journal.reconcile_and_claim_stale(journal_root, root_fd, session_id="recover")


def do_plan(root):
    cw = load_crosswalk(root)
    groups = components(cw)
    print("units (connected components of the crosswalk graph): {}".format(len(groups)))
    for key in sorted(groups):
        preds = sorted({m["predecessor-clause-id"] for m in groups[key]})
        succs = sorted({m["successor-clause-id"] for m in groups[key]})
        print("  unit {}: predecessors={} successors={}".format(key, preds, succs))
    return 0


def do_cutover(root, staged, unit):
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    # E3 (spec 1291/1300 posture): open and VALIDATE --root FIRST (unresolved abspath, O_NOFOLLOW), before
    # ANY root-relative read, write, or lock, so a symlinked root is refused (exit 2) with no crosswalk read,
    # no journal dir created, and no lock left behind.
    root_fd, err = _open_root_or_none(root)                # fix #8 / E3: refuse a symlinked root at the CLI
    if err:
        print("error: {}".format(err), file=sys.stderr)
        return 2
    try:
        try:
            _assert_off_path(root, staged)
            _require_quiescence(staged)
            ops = _read_staged_plan(staged)
            # C7: bind the cutover to a REAL connected component of the crosswalk (9.1). Reject an unknown
            # --unit BEFORE locking (exit 2), and record the component's mapped predecessor/successor
            # clause-id set in the INTENT header so check_crosswalk can gate whole-component coverage.
            cw = load_crosswalk(root)
            groups = components(cw)
            if unit not in groups:
                raise RefuseError("unknown --unit {!r}: not a connected component of the crosswalk; run "
                                  "`plan` to list units (9.1)".format(unit))
            member = _component_membership(groups[unit])
        except RefuseError as exc:
            print("error: {}; fail-closed".format(exc), file=sys.stderr)
            return 2
        journal_root = root / JOURNAL_REL
        journal_root.mkdir(parents=True, exist_ok=True)
        txn_id = "{}.{}.{}".format(_slug(unit), os.getpid(), time.time_ns())
        try:
            _journal.acquire_lock(journal_root, session_id="cutover")
        except _journal.JournalError as exc:
            print("error: {}; fail-closed".format(exc), file=sys.stderr)
            return 2
        header = {"unit": unit, "kind": "cutover",
                  "component-predecessors": member["predecessors"],
                  "component-successors": member["successors"]}
        txn_dir = journal_root / txn_id
        try:
            _journal.run_transaction(root_fd, journal_root, txn_id, header, ops,
                                     _staged_reader(staged), session_id="cutover")
        except _journal.JournalError as exc:
            return _settle_failed_transaction(journal_root, txn_dir, exc, "cutover")
        _journal.release_lock(journal_root)
    finally:
        os.close(root_fd)
    print("cutover complete: unit {} txn {}".format(unit, txn_id))
    return 0


def _settle_failed_transaction(journal_root, txn_dir, exc, what):
    """A JournalError escaped run_transaction. Classify the VALIDATED journal state via the C2 state
    machine (classify_state), never a bare is_terminal that reads True on a NO-INTENT journal and so
    falsely reports a pre-INTENT failure as 'rolled back' (C4). Three cases:
      (a) 'nothing-opened' (pre-INTENT / capture-phase failure, nothing applied): release the lock and
          report 'aborted before opening (no change applied)', NOT rolled back, exit 2;
      (b) 'rolled-back' ([INTENT,RIP,RC] terminal rollback, tree back at its prestate): release the lock
          and report the clean rollback, exit 2;
      (c) otherwise ('open': the rollback itself failed, or an unreadable/invalid journal): RETAIN the
          lock, leave the transaction open for a later `recover`, claim no rollback, exit 2."""
    try:
        state = _journal.classify_state(txn_dir)
    except _journal.JournalError:
        state = "open"                                    # unreadable/invalid journal: fail-closed, retain lock
    if state == "nothing-opened":
        _journal.release_lock(journal_root)
        print("error: {} aborted before opening (no change applied) ({}); fail-closed".format(what, exc),
              file=sys.stderr)
    elif state == "rolled-back":
        _journal.release_lock(journal_root)
        print("error: {} aborted and rolled back ({}); fail-closed".format(what, exc), file=sys.stderr)
    else:
        print("error: {} FAILED and the rollback did not complete ({}); the transaction is left open "
              "under the retained lock for `recover`; fail-closed".format(what, exc), file=sys.stderr)
    return 2


def do_recover(root):
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    # E3: open and VALIDATE --root FIRST (O_NOFOLLOW), before ANY root-relative read or lock, so a symlinked
    # root is refused (exit 2) before the journal is inspected or any lock is claimed.
    root_fd, err = _open_root_or_none(root)                # fix #8 / E3: refuse a symlinked root at the CLI
    if err:
        print("error: {}".format(err), file=sys.stderr)
        return 2
    try:
        journal_root = root / JOURNAL_REL
        if not journal_root.is_dir():
            print("recover: no journal at {} (nothing to recover)".format(JOURNAL_REL))
            return 0
        # Atomically CLAIM the lock before touching any transaction (fix #3): acquire an absent lock, or
        # (E4) reconcile-then-break a confirmed-dead stale lock under the arbitration lock; a possibly-live
        # owner is NEVER seized. Only the lock THIS recover owns is released, after every txn is terminal.
        try:
            claim = _claim_recover_lock(journal_root, root_fd)
        except _journal.JournalError as exc:
            print("error: {}; fail-closed".format(exc), file=sys.stderr)
            return 2
        if claim == "possibly-live":
            owner = _journal.read_lock_owner(journal_root)
            print("recover: journal lock is held by a possibly-live owner (pid {}); NOT seized"
                  .format((owner or {}).get("pid")), file=sys.stderr)
            return 1
        outcomes = {}
        for txn_dir in _txn_dirs(journal_root):
            try:
                outcomes[txn_dir.name] = _journal.recover(txn_dir, root_fd)
            except _journal.JournalError as exc:
                print("error: cannot recover {} ({}); fail-closed".format(txn_dir.name, exc),
                      file=sys.stderr)
                return 2                                   # lock RETAINED: the journal is not terminal
        _journal.release_lock(journal_root)               # release only the lock THIS recover owns
        if outcomes:
            for name, outcome in sorted(outcomes.items()):
                print("recover: txn {} -> {}".format(name, outcome))
        else:
            print("recover: no transactions to recover")
        return 0
    finally:
        os.close(root_fd)


def do_status(root):
    journal_root = root / JOURNAL_REL
    if not journal_root.is_dir():
        print("status: not adopted (no journal)")
        return 0
    open_txns = []
    for txn_dir in _txn_dirs(journal_root):
        try:
            terminal = _journal.is_terminal(txn_dir)
        except _journal.JournalError as exc:
            print("error: corrupt journal {} ({}); fail-closed".format(txn_dir.name, exc),
                  file=sys.stderr)
            return 2
        state = "terminal" if terminal else "OPEN"
        print("status: txn {} -> {}".format(txn_dir.name, state))
        if not terminal:
            open_txns.append(txn_dir.name)
    lock = _journal.read_lock_owner(journal_root)
    if lock is not None:
        print("status: journal lock held by pid {}".format(lock.get("pid")))
    if open_txns:
        print("status: {} OPEN transaction(s) need recovery".format(len(open_txns)), file=sys.stderr)
        return 1
    return 0


def _txn_dirs(journal_root):
    out = []
    for entry in sorted(Path(journal_root).iterdir()):
        if entry.is_dir():
            out.append(entry)
    return out


def _slug(value):
    return "".join(ch if (ch.isalnum() or ch in "-._") else "_" for ch in str(value))[:120] or "unit"


# --- main ---------------------------------------------------------------------------------------------

def _arg(args, name):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1]
    return None


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = args[0]
    root = _arg(args, "--root")
    if cmd in ("plan", "cutover", "recover", "status") and root is None:
        print("error: --root DIR is required", file=sys.stderr)
        return 2
    # fix #8: keep an ABSOLUTE but UNRESOLVED root (os.path.abspath does not follow symlinks), so a
    # symlinked final --root component survives to _open_root_fd's O_NOFOLLOW and is refused there rather
    # than silently followed off-tree by an early resolve().
    root = Path(os.path.abspath(root)) if root else None
    if cmd == "plan":
        return do_plan(root)
    if cmd == "cutover":
        staged, unit = _arg(args, "--staged"), _arg(args, "--unit")
        if not staged or not unit:
            print("error: cutover requires --staged DIR and --unit ID", file=sys.stderr)
            return 2
        return do_cutover(root, Path(staged).resolve(), unit)
    if cmd == "recover":
        return do_recover(root)
    if cmd == "status":
        return do_status(root)
    print("error: unknown subcommand {!r}".format(cmd), file=sys.stderr)
    return 2


# --- self-test: the mandatory crash-injection matrix (9.3, spec lines 1354 to 1359) -------------------
# For EVERY kill point, a cutover runs in a real subprocess that os._exit(137)s exactly there, leaving
# only what was already fsync'd; a fresh process then runs `recover` (twice, proving idempotency) and the
# harness asserts the tree byte-compares to EXACTLY the prestate or the verified poststate, that the
# journal is terminal, and that the second recover is a no-op. Both directions are proven from the
# journal alone: the roll-FORWARD election (crash after the last apply, before COMPLETE) and rollback
# (crash mid-apply, plus injected crashes mid-rollback and mid-publication of both rollback markers).
# A torn-payload mid-write kill crashes DURING a data-file payload write, leaving a partially-written
# payload that can never satisfy poststate verification, so recovery must elect rollback and land the
# prestate EXACTLY (never an op-boundary poststate), exercising the post-restore preimage digest check.
# Four synthetic off-path cases over three tree structures exercise flat files, a nested directory
# create, a nested directory remove, and a umask-reduced-mode remove variant, so reverse-dependency
# ordering, domain-separated post-states, and directory mode-resume are covered in both directions.
# A final block exercises the INERT 10.6 reverse-replay primitive (build_inverse_ops) directly over a
# completed cutover's INTENT ops; the un-adopt CLI/workflow is Section 12 step 7, not this step-6 slice.

_CASES = {
    "flat-files": {
        "pre_files": {"dataA": (b"old-A\n", 0o644), "dataB": (b"old-B\n", 0o644)},
        "pre_dirs": {},
        "payload": {"dataA": b"new-A\n", "dataC": b"new-C\n"},
        "ops": [{"op": "write", "path": "dataA"},
                {"op": "create", "path": "dataC", "mode": 0o644},
                {"op": "remove", "path": "dataB"}],
    },
    "nested-create": {
        "pre_files": {},
        "pre_dirs": {},
        "payload": {"d/e/f": b"leaf\n"},
        "ops": [{"op": "mkdir", "path": "d", "mode": 0o755},
                {"op": "mkdir", "path": "d/e", "mode": 0o755},
                {"op": "create", "path": "d/e/f", "mode": 0o644}],
    },
    "nested-remove": {
        "pre_files": {"d/e/f": (b"leaf\n", 0o644)},
        "pre_dirs": {"d": 0o755, "d/e": 0o755},
        "payload": {},
        "ops": [{"op": "remove", "path": "d/e/f"},
                {"op": "rmdir", "path": "d/e"},
                {"op": "rmdir", "path": "d"}],
    },
    # A removed directory (d/e) carries a mode with a bit the umask clears, so a rollback that recreates it
    # exercises the rmdir-undo mode-resume: a crash between the mkdir and the chmod leaves a umask-reduced
    # mode that a fresh recover must re-apply to its exact prestate (fix #1 torn-mode test, block H).
    "mode-remove": {
        "pre_files": {"d/e/f": (b"leaf\n", 0o644)},
        "pre_dirs": {"d": 0o755, "d/e": 0o770},
        "payload": {},
        "ops": [{"op": "remove", "path": "d/e/f"},
                {"op": "rmdir", "path": "d/e"},
                {"op": "rmdir", "path": "d"}],
    },
}


_SELFTEST_UNIT = "p:p"   # the components() key of the one-mapping crosswalk each case ships (C7 binding)


def _write_selftest_crosswalk(root):
    """Ship a minimal crosswalk (one mapping p->s) so do_cutover's C7 component binding resolves the
    self-test unit; components() keys the single component 'p:p'. It lives under .aiqt, which _snapshot
    excludes, so it never perturbs a pre/post tree comparison."""
    cw = root / CROSSWALK_REL
    cw.parent.mkdir(parents=True, exist_ok=True)
    cw.write_text('schema-version = 1\n\n[[mapping]]\n'
                  'predecessor-clause-id = "p"\nsuccessor-clause-id = "s"\n', encoding="utf-8")


def _build_case_root(base, case):
    root = base
    root.mkdir(parents=True, exist_ok=True)
    spec = _CASES[case]
    for rel, mode in spec["pre_dirs"].items():
        d = root / rel
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, mode)
    for rel, (data, mode) in spec["pre_files"].items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
        os.chmod(f, mode)
    _write_selftest_crosswalk(root)                       # C7: a real component for the cutover to bind to
    return root


def _build_staged(base, case):
    staged = base
    (staged / "payload").mkdir(parents=True, exist_ok=True)
    (staged / "quiescence.ok").write_text("ok\n", encoding="utf-8")
    for rel, data in _CASES[case]["payload"].items():
        p = staged / "payload" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (staged / "plan.json").write_text(json.dumps({"ops": _CASES[case]["ops"]}), encoding="utf-8")
    return staged


def _snapshot(root):
    """A stable snapshot of the data tree (paths -> mode+digest for files, mode for dirs), EXCLUDING the
    .aiqt control namespace, whose journal legitimately changes. Comparable across runs."""
    root = Path(root)
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        if ".aiqt" in dp.relative_to(root).parts:
            dirnames[:] = []
            continue
        for d in list(dirnames):
            full = dp / d
            if full.relative_to(root).parts[0] == ".aiqt":
                continue
            out.add((str(full.relative_to(root)), "dir", stat_mode(full)))
        for f in filenames:
            full = dp / f
            out.add((str(full.relative_to(root)), "file", stat_mode(full),
                     hashlib.sha256(full.read_bytes()).hexdigest()))
    return frozenset(out)


def stat_mode(path):
    import stat as _stat
    return _stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)


def _kill_points(case):
    n = len(_CASES[case]["ops"])
    points = ["after-lock"]
    points += ["after-preimage-{}".format(i) for i in range(n)]
    points += ["after-preimages", "torn:INTENT", "after-publish-INTENT"]
    points += ["after-apply-{}".format(i) for i in range(n)]
    points += ["torn:COMPLETE", "after-publish-COMPLETE"]
    return points


def _rollback_kill_points(n):
    return (["torn:ROLLBACK-IN-PROGRESS"]
            + ["after-restore-{}".format(i) for i in range(n)]
            + ["torn:ROLLBACK-COMPLETE"])


def _run(argv, kill=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if kill is not None:
        env[_journal.KILL_ENV] = kill
    else:
        env.pop(_journal.KILL_ENV, None)
    return subprocess.call([sys.executable, os.path.abspath(__file__)] + argv, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _all_terminal(root):
    journal_root = Path(root) / JOURNAL_REL
    if not journal_root.is_dir():
        return True
    for txn_dir in _txn_dirs(journal_root):
        try:
            if not _journal.is_terminal(txn_dir):
                return False
        except _journal.JournalError:
            return False
    return not (journal_root / "lock").exists()


def self_test():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stderr

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-migrate-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2

    failures = []
    checked = 0
    try:
        # Per-case pre/post baselines, derived from a real clean cutover (never hand-written).
        baselines = {}
        for case in _CASES:
            broot = _build_case_root(tmp / (case + "-base") / "root", case)
            bstaged = _build_staged(tmp / (case + "-base") / "staged", case)
            pre = _snapshot(broot)
            if _run(["cutover", "--root", str(broot), "--staged", str(bstaged), "--unit", _SELFTEST_UNIT]) != 0:
                failures.append("{}: clean cutover expected exit 0".format(case))
            post = _snapshot(broot)
            baselines[case] = (pre, post)
            if pre == post:
                failures.append("{}: pre and post snapshots are identical (case is trivial)".format(case))

        # (A) Cutover-phase kill matrix: crash at each point, recover twice, assert pre-or-post + terminal.
        for case in _CASES:
            pre, post = baselines[case]
            for point in _kill_points(case):
                iroot = _build_case_root(tmp / case / point.replace(":", "_") / "root", case)
                istaged = _build_staged(tmp / case / point.replace(":", "_") / "staged", case)
                rc = _run(["cutover", "--root", str(iroot), "--staged", str(istaged), "--unit", _SELFTEST_UNIT],
                          kill=point)
                if rc != 137:
                    failures.append("{} @ {}: cutover expected to die (137), got {}".format(case, point, rc))
                    continue
                if _run(["recover", "--root", str(iroot)]) != 0:
                    failures.append("{} @ {}: first recover expected exit 0".format(case, point))
                if _run(["recover", "--root", str(iroot)]) != 0:
                    failures.append("{} @ {}: second recover (idempotency) expected exit 0"
                                    .format(case, point))
                state = _snapshot(iroot)
                if state not in (pre, post):
                    failures.append("{} @ {}: recovered tree is neither prestate nor verified poststate"
                                    .format(case, point))
                elif not _all_terminal(iroot):
                    failures.append("{} @ {}: journal not terminal after recovery".format(case, point))
                checked += 1

        # (B) Rollback-phase injected crashes: crash the cutover mid-apply, then crash the RECOVER at each
        #     rollback point (mid-restore and mid-publication of both rollback markers); a final clean
        #     recover must still land the tree on the prestate, terminal and idempotent.
        for case in _CASES:
            pre, _post = baselines[case]
            n = len(_CASES[case]["ops"])
            mid = "after-apply-{}".format(max(0, n - 2))   # partial apply: at least one op left undone
            for rpoint in _rollback_kill_points(n):
                iroot = _build_case_root(tmp / (case + "-rb") / rpoint.replace(":", "_") / "root", case)
                istaged = _build_staged(tmp / (case + "-rb") / rpoint.replace(":", "_") / "staged", case)
                if _run(["cutover", "--root", str(iroot), "--staged", str(istaged), "--unit", _SELFTEST_UNIT],
                        kill=mid) != 137:
                    failures.append("{} rb {}: setup cutover expected to die at {}".format(case, rpoint, mid))
                    continue
                rrc = _run(["recover", "--root", str(iroot)], kill=rpoint)
                if rrc != 137:
                    failures.append("{} rb {}: injected recover expected to die (137), got {}"
                                    .format(case, rpoint, rrc))
                if _run(["recover", "--root", str(iroot)]) != 0:
                    failures.append("{} rb {}: clean recover expected exit 0".format(case, rpoint))
                if _run(["recover", "--root", str(iroot)]) != 0:
                    failures.append("{} rb {}: second clean recover expected exit 0".format(case, rpoint))
                state = _snapshot(iroot)
                if state != pre:
                    failures.append("{} rb {}: rollback did not restore the prestate".format(case, rpoint))
                elif not _all_terminal(iroot):
                    failures.append("{} rb {}: journal not terminal after rollback".format(case, rpoint))
                checked += 1

        # (C) Torn payload mid-write: crash DURING a data-file payload write, leaving a torn (partially-
        #     written) payload. A torn payload can never satisfy poststate verification, so recovery must
        #     ELECT ROLLBACK and land on the prestate EXACTLY (never the poststate), exercising the
        #     pre-restore preimage digest check on the rollback path (_journal.py verifies the preimage
        #     hashes to the recorded prestate digest immediately BEFORE it rewrites the prior bytes).
        for case in _CASES:
            pre, _post = baselines[case]
            for i, op in enumerate(_CASES[case]["ops"]):
                if op["op"] not in ("write", "create"):
                    continue
                point = "torn-payload:{}".format(i)
                iroot = _build_case_root(tmp / (case + "-tp") / "op-{}".format(i) / "root", case)
                istaged = _build_staged(tmp / (case + "-tp") / "op-{}".format(i) / "staged", case)
                rc = _run(["cutover", "--root", str(iroot), "--staged", str(istaged), "--unit", _SELFTEST_UNIT],
                          kill=point)
                if rc != 137:
                    failures.append("{} @ {}: cutover expected to die (137), got {}".format(case, point, rc))
                    continue
                if _run(["recover", "--root", str(iroot)]) != 0:
                    failures.append("{} @ {}: first recover expected exit 0".format(case, point))
                if _run(["recover", "--root", str(iroot)]) != 0:
                    failures.append("{} @ {}: second recover (idempotency) expected exit 0"
                                    .format(case, point))
                state = _snapshot(iroot)
                if state != pre:
                    failures.append("{} @ {}: a torn payload must roll back to the prestate exactly"
                                    .format(case, point))
                elif not _all_terminal(iroot):
                    failures.append("{} @ {}: journal not terminal after recovery".format(case, point))
                checked += 1

        # (D) INERT 10.6 reverse-replay primitive (build_inverse_ops): over a real completed cutover's
        #     INTENT ops, it inverts each op in REVERSE dependency order (write->write prior bytes,
        #     create->remove, remove->create, mkdir->rmdir, rmdir->mkdir). The un-adopt CLI/workflow is
        #     Section 12 step 7 (spec 1586-1590), NOT this step-6 slice, which exposes no un-adopt
        #     subcommand; here only the inert primitive Step 7 builds on is covered.
        _INVERSE_KIND = {"write": "write", "create": "remove", "remove": "create",
                         "mkdir": "rmdir", "rmdir": "mkdir"}
        for case in _CASES:
            uroot = _build_case_root(tmp / (case + "-inverse") / "root", case)
            ustaged = _build_staged(tmp / (case + "-inverse") / "staged", case)
            if _run(["cutover", "--root", str(uroot), "--staged", str(ustaged), "--unit", _SELFTEST_UNIT]) != 0:
                failures.append("{} inverse: setup cutover expected exit 0".format(case))
                continue
            txn = _latest_txn(uroot)
            if txn is None:
                failures.append("{} inverse: no completed cutover txn to invert".format(case))
                continue
            frames, _torn, _good = _journal.read_frames(uroot / JOURNAL_REL / txn)
            src_ops = (_journal._first(frames, _journal.F_INTENT) or {}).get("ops", [])
            inverse = _journal.build_inverse_ops(src_ops)
            expected = [(_INVERSE_KIND[o["op"]], o["path"]) for o in reversed(src_ops)]
            if [(iop["op"], iop["path"]) for iop in inverse] != expected:
                failures.append("{} inverse: build_inverse_ops must invert each op in reverse dependency "
                                "order".format(case))
            checked += 1

        # (E) Fail-closed preconditions: missing quiescence evidence, and an on-path staged tree.
        froot = _build_case_root(tmp / "fc" / "root", "flat-files")
        fstaged = _build_staged(tmp / "fc" / "staged", "flat-files")
        (fstaged / "quiescence.ok").unlink()
        if _run(["cutover", "--root", str(froot), "--staged", str(fstaged), "--unit", _SELFTEST_UNIT]) != 2:
            failures.append("missing quiescence evidence expected exit 2 (refused)")
        checked += 1
        onpath = _build_case_root(tmp / "onpath" / "root", "flat-files")
        instaged = _build_staged(onpath / "inside-staged", "flat-files")
        if _run(["cutover", "--root", str(onpath), "--staged", str(instaged), "--unit", _SELFTEST_UNIT]) != 2:
            failures.append("an on-path staged tree expected exit 2 (refused)")
        checked += 1

        # (F) The possibly-live lock is never seized: a lock owned by THIS (live) process blocks recover.
        lroot = _build_case_root(tmp / "livelock" / "root", "flat-files")
        (lroot / JOURNAL_REL).mkdir(parents=True, exist_ok=True)
        _journal.acquire_lock(lroot / JOURNAL_REL, session_id="live")
        if _run(["recover", "--root", str(lroot)]) != 1:
            failures.append("recover against a possibly-live lock expected exit 1 (never seized)")
        _journal.release_lock(lroot / JOURNAL_REL)
        checked += 1

        # (G) A mid-log (non-tail) corrupt frame is a FAIL, never skipped: status fails closed (exit 2).
        croot = _build_case_root(tmp / "corrupt" / "root", "flat-files")
        cstaged = _build_staged(tmp / "corrupt" / "staged", "flat-files")
        _run(["cutover", "--root", str(croot), "--staged", str(cstaged), "--unit", _SELFTEST_UNIT])
        ctxn = _latest_txn(croot)
        log = croot / JOURNAL_REL / ctxn / "frames.log"
        raw = bytearray(log.read_bytes())
        raw[0:1] = b"Z"                                    # break the FIRST (non-tail) frame's magic
        log.write_bytes(bytes(raw))
        if _run(["status", "--root", str(croot)]) != 2:
            failures.append("a mid-log corrupt frame expected status exit 2 (fail-closed, never skipped)")
        checked += 1

        # (H) FIX #1 rmdir-undo MODE-RESUME: crash a rollback's directory recreation BETWEEN the mkdir and
        #     the chmod (the new torn-mode kill), leaving it at a umask-reduced mode; a fresh recover must
        #     RE-APPLY the exact prestate mode (idempotent mode-resume). umask is pinned so the crash state
        #     is observably reduced (the property under test): mkdir(0o770) yields 0o750 until the chmod.
        old_umask = os.umask(0o022)
        try:
            case = "mode-remove"
            pre, _post = baselines[case]
            nops = len(_CASES[case]["ops"])
            mid = "after-apply-{}".format(nops - 2)         # d/e/f and d/e removed, d not yet: rolls back
            iroot = _build_case_root(tmp / "mode-resume" / "root", case)
            istaged = _build_staged(tmp / "mode-resume" / "staged", case)
            if _run(["cutover", "--root", str(iroot), "--staged", str(istaged), "--unit", _SELFTEST_UNIT],
                    kill=mid) != 137:
                failures.append("mode-resume: setup cutover expected to die at {}".format(mid))
            elif _run(["recover", "--root", str(iroot)], kill="torn-mode:1") != 137:
                failures.append("mode-resume: recover expected to die mid mkdir->chmod (torn-mode:1)")
            else:
                if _run(["recover", "--root", str(iroot)]) != 0:
                    failures.append("mode-resume: clean recover expected exit 0")
                if _run(["recover", "--root", str(iroot)]) != 0:
                    failures.append("mode-resume: second recover (idempotency) expected exit 0")
                if _snapshot(iroot) != pre:
                    failures.append("mode-resume: recover must re-apply the recreated directory's exact "
                                    "prestate mode (mode-resume)")
                elif not _all_terminal(iroot):
                    failures.append("mode-resume: journal not terminal after recovery")
            checked += 1
        finally:
            os.umask(old_umask)

        # (I) FIX #1 FAIL-CLOSED: a non-directory where a mkdir/rmdir restore expects a directory raises
        #     JournalError (never a silent skip), matching the write/remove non-regular branch (-> exit 2
        #     when a CLI maps it, as do_recover does).
        nd = _build_case_root(tmp / "nondir" / "root", "flat-files")
        ndfd = os.open(str(nd), os.O_RDONLY | os.O_DIRECTORY)
        try:
            (nd / "collide").write_bytes(b"not-a-dir\n")    # a regular file where a directory is expected
            for kind, prestate in (("mkdir", {"kind": "absent"}),
                                   ("rmdir", {"kind": "dir", "mode": 0o755})):
                try:
                    _journal._restore_preimage(nd / "txn", ndfd,
                                               {"op": kind, "path": "collide", "prestate": prestate})
                    failures.append("{}-undo on a non-directory must raise JournalError".format(kind))
                except _journal.JournalError:
                    pass
        finally:
            os.close(ndfd)
        checked += 1

        # (F2) FIX #3 OWNERSHIP-CHECKED RELEASE: a lock NOT owned by this process is never unlinked.
        jr2 = tmp / "foreignlock" / JOURNAL_REL
        jr2.mkdir(parents=True)
        (jr2 / "lock").write_bytes(json.dumps(
            {"uid": os.getuid(), "pid": os.getpid(), "session": "foreign", "pid-start": "not-ours",
             "utc": "2026-01-01T00:00:00Z"}, sort_keys=True).encode())
        _journal.release_lock(jr2)
        if not (jr2 / "lock").exists():
            failures.append("release_lock must never delete a lock this process does not own")
        checked += 1

        # (F3) FIX #3 CLAIM: recover claims an ABSENT lock before touching txns and releases it after the
        #      txn reaches a terminal state.
        aroot = _build_case_root(tmp / "absentlock" / "root", "flat-files")
        astaged = _build_staged(tmp / "absentlock" / "staged", "flat-files")
        an = len(_CASES["flat-files"]["ops"])
        _run(["cutover", "--root", str(aroot), "--staged", str(astaged), "--unit", _SELFTEST_UNIT],
             kill="after-apply-{}".format(max(0, an - 2)))  # crash mid-apply: an OPEN txn plus a stale lock
        (aroot / JOURNAL_REL / "lock").unlink()             # remove the lock: recover must claim the absent one
        if _run(["recover", "--root", str(aroot)]) != 0:
            failures.append("recover must claim an absent lock and recover the open txn (exit 0)")
        if (aroot / JOURNAL_REL / "lock").exists():
            failures.append("recover must release the lock it claimed after reaching a terminal state")
        checked += 1

        # (J) FIX #4: a rollback that ITSELF fails must not release the lock or claim "rolled back". A plan
        #     whose apply fails (a duplicate create) triggers rollback; an injected preimage-restore failure
        #     leaves the journal NON-TERMINAL. do_cutover must retain the lock, leave the txn open, exit 2,
        #     and never report a completed rollback.
        rbroot = _build_case_root(tmp / "rbfail" / "root", "flat-files")
        rbstaged = tmp / "rbfail" / "staged"
        (rbstaged / "payload").mkdir(parents=True)
        (rbstaged / "quiescence.ok").write_text("ok\n", encoding="utf-8")
        (rbstaged / "payload" / "dupe").write_bytes(b"dup\n")
        (rbstaged / "plan.json").write_text(json.dumps({"ops": [
            {"op": "create", "path": "dupe", "mode": 0o644},
            {"op": "create", "path": "dupe", "mode": 0o644}]}), encoding="utf-8")
        orig_restore = _journal._restore_preimage

        def _boom(*_a, **_k):
            raise _journal.JournalError("injected preimage-restore failure")

        buf = io.StringIO()
        _journal._restore_preimage = _boom
        try:
            with redirect_stderr(buf):
                rc = do_cutover(Path(rbroot).resolve(), rbstaged.resolve(), _SELFTEST_UNIT)
        finally:
            _journal._restore_preimage = orig_restore
        jr = Path(rbroot) / JOURNAL_REL
        nonterminal = False
        for td in _txn_dirs(jr):
            try:
                if not _journal.is_terminal(td):
                    nonterminal = True
            except _journal.JournalError:
                nonterminal = True
        if rc != 2:
            failures.append("fix4: do_cutover on a failed rollback must exit 2")
        if not (jr / "lock").exists():
            failures.append("fix4: do_cutover must RETAIN the lock when the rollback itself failed")
        if not nonterminal:
            failures.append("fix4: a failed rollback must leave the journal non-terminal")
        if "rolled back" in buf.getvalue():
            failures.append("fix4: a failed rollback must not be reported as 'rolled back'")
        _journal.release_lock(jr)                           # clean up the lock we deliberately left retained
        checked += 1

        # (K) FIX #2: a mismatched-txn terminal frame (a stray/crafted frame) fails closed (exit 2). Both
        #     frames are checksum-valid; recovery rejects the disagreement rather than trusting it.
        mroot = _build_case_root(tmp / "mismatch" / "root", "flat-files")
        mtxn = mroot / JOURNAL_REL / "sometxn"
        mtxn.mkdir(parents=True)
        _journal.publish(mtxn, _journal.F_INTENT, {"txn": "A", "header": {}, "ops": []})
        _journal.publish(mtxn, _journal.F_COMPLETE, {"txn": "B"})
        if _run(["recover", "--root", str(mroot)]) != 2:
            failures.append("fix2: a mismatched-txn terminal frame must fail closed (exit 2)")
        checked += 1

        # (L) C2 accepted-sequence state machine: sequences the OLD ad-hoc checks let through are now
        #     rejected by the explicit accepted-sequence set, from the SAME validator recover() and
        #     is_terminal() use. TWO NULL-TXN INTENTs (the old `intent_txn is not None` sentinel missed
        #     them) and RC-BEFORE-RIP (the old check only required RIP "somewhere", never before RC).
        for label, frames_spec in (
                ("two null-txn INTENTs",
                 [(_journal.F_INTENT, {"txn": None, "header": {}, "ops": []}),
                  (_journal.F_INTENT, {"txn": None, "header": {}, "ops": []})]),
                ("RC before RIP",
                 [(_journal.F_INTENT, {"txn": "A", "header": {}, "ops": []}),
                  (_journal.F_RC, {"txn": "A"}),
                  (_journal.F_RIP, {"txn": "A"})])):
            stxn = tmp / "c2" / label.replace(" ", "_")
            stxn.mkdir(parents=True)
            for ftype, obj in frames_spec:
                _journal.publish(stxn, ftype, obj)
            try:
                _journal.classify_state(stxn)
                failures.append("C2: {} must be rejected by the state machine".format(label))
            except _journal.JournalError:
                pass
            checked += 1
        # is_terminal invokes the SAME validator (C2): an INTENT then RC (no preceding RIP) is not an
        # accepted sequence, so is_terminal fails closed there too, classifying identically to recover.
        itxn = tmp / "c2-isterm"
        itxn.mkdir(parents=True)
        _journal.publish(itxn, _journal.F_INTENT, {"txn": "A", "header": {}, "ops": []})
        _journal.publish(itxn, _journal.F_RC, {"txn": "A"})
        try:
            _journal.is_terminal(itxn)
            failures.append("C2: is_terminal must reject an invalid frame sequence (same validator)")
        except _journal.JournalError:
            pass
        checked += 1

        # (M) C3: COMPLETE means the poststate was installed. A staged payload whose bytes do NOT match the
        #     INTENT poststate content-sha256 (the staged tree changed after planning) must FAIL CLOSED and
        #     roll back to the prestate, never publishing a false COMPLETE.
        c3root = _build_case_root(tmp / "c3" / "root", "flat-files")   # has dataA = old-A\n
        c3jr = c3root / JOURNAL_REL
        c3jr.mkdir(parents=True, exist_ok=True)
        c3fd = os.open(str(c3root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            planned = {"op": "write", "path": "dataA",
                       "poststate": {"kind": "file",
                                     "content-sha256": hashlib.sha256(b"PLANNED-A\n").hexdigest()}}
            pre3 = _snapshot(c3root)
            try:
                _journal.run_transaction(c3fd, c3jr, "c3txn", {"unit": "x", "kind": "cutover"},
                                         [planned], lambda op: b"MUTATED-A\n", session_id="c3")
                failures.append("C3: a staged/INTENT digest mismatch must raise (no false COMPLETE)")
            except _journal.JournalError:
                pass
            ftypes = [t for t, _ in _journal.read_frames(c3jr / "c3txn")[0]]
            if _journal.F_COMPLETE in ftypes:
                failures.append("C3: a digest mismatch must NEVER publish COMPLETE")
            if _snapshot(c3root) != pre3:
                failures.append("C3: a digest mismatch must roll back to the prestate exactly")
        finally:
            os.close(c3fd)
        checked += 1

        # (N) C4: a PRE-INTENT / capture-phase failure (a write op on an absent path fails in
        #     capture_preimages, before any INTENT) reports 'aborted before opening', NOT 'rolled back',
        #     and RELEASES the lock (is_terminal would have read True on the no-INTENT journal and falsely
        #     claimed a rollback).
        c4root = _build_case_root(tmp / "c4" / "root", "flat-files")
        c4staged = tmp / "c4" / "staged"
        (c4staged / "payload").mkdir(parents=True)
        (c4staged / "quiescence.ok").write_text("ok\n", encoding="utf-8")
        (c4staged / "payload" / "ghost").write_bytes(b"nope\n")
        (c4staged / "plan.json").write_text(json.dumps({"ops": [{"op": "write", "path": "ghost"}]}),
                                            encoding="utf-8")
        buf4 = io.StringIO()
        with redirect_stderr(buf4):
            rc4 = do_cutover(c4root.resolve(), c4staged.resolve(), _SELFTEST_UNIT)
        out4 = buf4.getvalue()
        if rc4 != 2:
            failures.append("C4: a pre-INTENT capture failure must exit 2")
        if (c4root / JOURNAL_REL / "lock").exists():
            failures.append("C4: a pre-INTENT failure must RELEASE the lock")
        if "aborted before opening" not in out4:
            failures.append("C4: a pre-INTENT failure must report 'aborted before opening'")
        if "rolled back" in out4:
            failures.append("C4: a pre-INTENT failure must NOT be reported as rolled back")
        checked += 1

        # (O) C1: reconcile_and_claim_stale never unlinks a live current lock. Seed a LIVE lock owned by THIS
        #     process, then attempt a stale-break: it re-reads the current owner under the arbitration lock,
        #     finds it live, and refuses (possibly-live), leaving the live lock intact. The old code
        #     re-confirmed the STALE owner and would have unlinked whatever lock was there (the race).
        c1jr = tmp / "c1" / JOURNAL_REL
        c1jr.mkdir(parents=True)
        c1fd = os.open(str(tmp / "c1"), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _journal.acquire_lock(c1jr, session_id="live-holder")
            res1 = _journal.reconcile_and_claim_stale(c1jr, c1fd, session_id="recover")
            if res1 != "possibly-live":
                failures.append("C1: reconcile_and_claim_stale must report possibly-live for a live lock")
            if not (c1jr / "lock").exists():
                failures.append("C1: reconcile_and_claim_stale must NEVER unlink a live current lock")
            owner1 = _journal.read_lock_owner(c1jr)
            if not (owner1 and owner1.get("pid") == os.getpid()):
                failures.append("C1: the live current lock owner must be left unchanged")
            _journal.release_lock(c1jr)
        finally:
            os.close(c1fd)
        checked += 1

        # (P) C7: an unknown --unit (not a connected component of the crosswalk) is REJECTED before locking
        #     (exit 2), leaving no journal lock behind.
        ukroot = _build_case_root(tmp / "unknown-unit" / "root", "flat-files")
        ukstaged = _build_staged(tmp / "unknown-unit" / "staged", "flat-files")
        if _run(["cutover", "--root", str(ukroot), "--staged", str(ukstaged),
                 "--unit", "no-such-component"]) != 2:
            failures.append("C7: an unknown --unit must be rejected (exit 2)")
        if (ukroot / JOURNAL_REL / "lock").exists():
            failures.append("C7: an unknown --unit must be rejected BEFORE locking")
        checked += 1

        # (Q) codex hardening: _open_root_fd binds the adopter root with O_NOFOLLOW, so a root whose final
        #     component is a symlink is refused rather than followed off-tree; a real directory still opens.
        realdir = tmp / "nofollow" / "real"
        realdir.mkdir(parents=True)
        linkdir = tmp / "nofollow" / "link"
        os.symlink(str(realdir), str(linkdir))
        try:
            os.close(_open_root_fd(linkdir))
            failures.append("codex: _open_root_fd must refuse a symlinked root (O_NOFOLLOW)")
        except OSError:
            pass
        os.close(_open_root_fd(realdir))
        checked += 1

        # (R) FIX #1 directory-mode DURABILITY: crash a rollback's directory recreation immediately AFTER
        #     its exact mode is set and the directory's OWN fd is fsync'd (the durability point, a kill that
        #     exists only on the fixed durable path). A fresh recover must still land the exact prestate
        #     mode, terminal and idempotent. Without the durable dir-fsync the kill point is absent and the
        #     injected recover never dies (rc != 137), so this fails closed against a regression.
        old_umask = os.umask(0o022)
        try:
            case = "mode-remove"
            pre, _post = baselines[case]
            nops = len(_CASES[case]["ops"])
            mid = "after-apply-{}".format(nops - 2)
            iroot = _build_case_root(tmp / "dir-durable" / "root", case)
            istaged = _build_staged(tmp / "dir-durable" / "staged", case)
            if _run(["cutover", "--root", str(iroot), "--staged", str(istaged), "--unit", _SELFTEST_UNIT],
                    kill=mid) != 137:
                failures.append("dir-durable: setup cutover expected to die at {}".format(mid))
            elif _run(["recover", "--root", str(iroot)], kill="torn-dirsync:1") != 137:
                failures.append("dir-durable: recover expected to die AFTER the dir-mode durability fsync "
                                "(torn-dirsync:1); the durable dir-fsync path is missing")
            else:
                if _run(["recover", "--root", str(iroot)]) != 0:
                    failures.append("dir-durable: clean recover expected exit 0")
                if _run(["recover", "--root", str(iroot)]) != 0:
                    failures.append("dir-durable: second recover (idempotency) expected exit 0")
                if _snapshot(iroot) != pre:
                    failures.append("dir-durable: recover must land the recreated directory's exact durable "
                                    "prestate mode")
                elif not _all_terminal(iroot):
                    failures.append("dir-durable: journal not terminal after recovery")
            checked += 1
        finally:
            os.umask(old_umask)

        # (U) FIX #8: through the REAL CLI, a symlinked --root is REFUSED (exit 2), not followed. main keeps
        #     an unresolved abspath so _open_root_fd's O_NOFOLLOW refuses the symlinked final component; an
        #     early resolve() would have followed it off-tree.
        ureal = _build_case_root(tmp / "cli-nofollow" / "real", "flat-files")
        ustg = _build_staged(tmp / "cli-nofollow" / "staged", "flat-files")
        ulink = tmp / "cli-nofollow" / "link"
        os.symlink(str(ureal), str(ulink))
        if _run(["cutover", "--root", str(ulink), "--staged", str(ustg), "--unit", _SELFTEST_UNIT]) != 2:
            failures.append("fix8: a symlinked --root must be refused at the CLI (exit 2)")
        if (ureal / JOURNAL_REL / "lock").exists():
            failures.append("fix8: a refused symlinked root must not leave a journal lock")
        checked += 1

        # (V) HARDENING: _verify_staged_digest rejects a missing / non-string / uppercase / non-64-hex
        #     expected digest immediately (never a silent skip that would install an undescribed payload);
        #     a well-formed matching digest still passes.
        for bad in ({"kind": "file"}, {"kind": "file", "content-sha256": None},
                    {"kind": "file", "content-sha256": "A" * 64},
                    {"kind": "file", "content-sha256": "zz"}):
            try:
                _journal._verify_staged_digest({"path": "x", "poststate": bad}, b"data")
                failures.append("hardening: a malformed poststate content-sha256 ({!r}) must raise"
                                .format(bad))
            except _journal.JournalError:
                pass
        _journal._verify_staged_digest(
            {"path": "x", "poststate": {"kind": "file",
                                        "content-sha256": hashlib.sha256(b"data").hexdigest()}}, b"data")
        checked += 1

        # (W) HARDENING: a non-dict lock JSON fails closed in read_lock_owner (never an AttributeError in
        #     owner_confirmed_dead / _owner_is_current); a symlinked lock.break is refused by O_NOFOLLOW.
        wjr = tmp / "nondict-lock" / JOURNAL_REL
        wjr.mkdir(parents=True)
        (wjr / "lock").write_bytes(b"[1, 2, 3]")            # a JSON array, not an object
        try:
            _journal.read_lock_owner(wjr)
            failures.append("hardening: a non-dict lock JSON must fail closed (JournalError)")
        except _journal.JournalError:
            pass
        ajr = tmp / "arb-symlink" / JOURNAL_REL
        ajr.mkdir(parents=True)
        os.symlink(str(tmp / "arb-symlink" / "elsewhere"), str(ajr / "lock.break"))
        awfd = os.open(str(tmp / "arb-symlink"), os.O_RDONLY | os.O_DIRECTORY)
        try:
            _journal.reconcile_and_claim_stale(ajr, awfd, session_id="recover")
            failures.append("hardening: a symlinked lock.break must be refused (O_NOFOLLOW, fail-closed)")
        except _journal.JournalError:
            pass
        finally:
            os.close(awfd)
        checked += 1

        # (B2) malformed lock identity fails closed to possibly-live (spec 1262 to 1265): a valid JSON
        #      object with a LIVE pid but a NON-STRING truthy pid-start (e.g. 1), or a BOOLEAN pid (bool is
        #      an int subclass), must NEVER read as confirmed-dead, which would let recovery unlink a LIVE
        #      owner's lock. owner_confirmed_dead defensively returns False even handed the malformed dict;
        #      read_lock_owner rejects the malformed lock (JournalError); and recover leaves it in place.
        live_pid = os.getpid()
        if _journal.owner_confirmed_dead(
                {"uid": os.getuid(), "pid": live_pid, "pid-start": 1, "session": "x"}):
            failures.append("B2: a live pid with a non-string pid-start must read as possibly-live (never seized)")
        if _journal.owner_confirmed_dead({"uid": os.getuid(), "pid": True, "pid-start": ""}):
            failures.append("B2: a boolean pid must read as possibly-live (never seized)")
        b2root = tmp / "malformed-lock"
        b2jr = b2root / JOURNAL_REL
        b2jr.mkdir(parents=True)
        (b2jr / "lock").write_bytes(json.dumps(
            {"uid": os.getuid(), "pid": live_pid, "pid-start": 1, "session": "x"}).encode())
        try:
            _journal.read_lock_owner(b2jr)
            failures.append("B2: a lock with a non-string pid-start must fail closed (JournalError)")
        except _journal.JournalError:
            pass
        if _run(["recover", "--root", str(b2root)]) == 0:
            failures.append("B2: recover over a malformed live lock must not report success")
        if not (b2jr / "lock").exists():
            failures.append("B2: recover must NEVER unlink a malformed (possibly-live) lock")
        checked += 1

        # (B2b) FIX F1: pid-start must be EMPTY or the canonical /proc decimal start time. A live pid with a
        #       truthy STRING pid-start that is NOT a decimal (garbage, or whitespace-padded) would otherwise
        #       differ from the real start time and read as CONFIRMED-DEAD, seizing a LIVE owner's lock. It
        #       must read possibly-live: owner_confirmed_dead returns False on the malformed dict, read_lock_
        #       owner rejects the lock (JournalError), and recover leaves it in place. An EMPTY pid-start with
        #       a live pid stays possibly-live (the disclosed non-Linux/unreadable case).
        for bad_start in ("garbage", " 12345 ", "12 34", "0x1f"):
            if _journal.owner_confirmed_dead(
                    {"uid": os.getuid(), "pid": live_pid, "pid-start": bad_start, "session": "x"}):
                failures.append("B2b: a live pid with a non-decimal pid-start ({!r}) must read as "
                                "possibly-live (never seized)".format(bad_start))
        if _journal.owner_confirmed_dead({"uid": os.getuid(), "pid": live_pid, "pid-start": "", "session": "x"}):
            failures.append("B2b: an empty pid-start with a live pid must stay possibly-live (never seized)")
        b2broot = tmp / "malformed-lock-str"
        b2bjr = b2broot / JOURNAL_REL
        b2bjr.mkdir(parents=True)
        (b2bjr / "lock").write_bytes(json.dumps(
            {"uid": os.getuid(), "pid": live_pid, "pid-start": "garbage", "session": "x"}).encode())
        try:
            _journal.read_lock_owner(b2bjr)
            failures.append("B2b: a lock with a non-decimal pid-start must fail closed (JournalError)")
        except _journal.JournalError:
            pass
        if _run(["recover", "--root", str(b2broot)]) == 0:
            failures.append("B2b: recover over a malformed-pid-start live lock must not report success")
        if not (b2bjr / "lock").exists():
            failures.append("B2b: recover must NEVER unlink a malformed (possibly-live) lock")
        checked += 1

        # (B2c) FIX F2: acquire_lock publishes via a direct O_CREAT|O_EXCL|O_WRONLY open; a SECOND concurrent
        #       acquire against the same journal root fails closed (FileExistsError -> JournalError), proving
        #       the O_EXCL create is the mutual-exclusion point (one open transaction at a time).
        xjr = tmp / "dup-acquire" / JOURNAL_REL
        xjr.mkdir(parents=True)
        _journal.acquire_lock(xjr, session_id="first")
        try:
            _journal.acquire_lock(xjr, session_id="second")
            failures.append("B2c: a second concurrent acquire must fail closed (JournalError)")
        except _journal.JournalError:
            pass
        _journal.release_lock(xjr)
        checked += 1

        # (B2d) COMPLETE owner-identity validation (spec 1262: UID, PID, session, and UTC): the FULL owner
        #       record is validated BEFORE any liveness call, so NO malformed field can let a LIVE owner read
        #       as confirmed-dead and be seized (possibly-live-never-seized, spec 1262 to 1265). Each variant
        #       below carries a LIVE pid yet must (i) read possibly-live via owner_confirmed_dead, (ii) fail
        #       closed in read_lock_owner (JournalError), and (iii) never be seized by recover (exit != 0, the
        #       lock left in place): (a) an OVERLONG/out-of-range decimal pid-start ("9"*1000) that the old
        #       unbounded ^[0-9]+$ gate accepted and that, differing from the real /proc start time, read as
        #       confirmed-DEAD and seized a live owner; (b) a missing or empty session; (c) a missing or empty
        #       utc; (d) a non-int/boolean pid. real_start is this live process's genuine canonical start (or
        #       "" off Linux), so (b) and (c) are otherwise well-formed and isolate the session/utc defect.
        real_start = _journal._pid_start(live_pid)          # the genuine canonical start (or "" off Linux)
        base_utc = "2026-01-01T00:00:00Z"
        malformed_owners = [
            ("overlong-pid-start",
             {"uid": os.getuid(), "pid": live_pid, "pid-start": "9" * 1000, "session": "x", "utc": base_utc}),
            ("empty-session",
             {"uid": os.getuid(), "pid": live_pid, "pid-start": real_start, "session": "", "utc": base_utc}),
            ("missing-session",
             {"uid": os.getuid(), "pid": live_pid, "pid-start": real_start, "utc": base_utc}),
            ("empty-utc",
             {"uid": os.getuid(), "pid": live_pid, "pid-start": real_start, "session": "x", "utc": ""}),
            ("missing-utc",
             {"uid": os.getuid(), "pid": live_pid, "pid-start": real_start, "session": "x"}),
            ("boolean-pid",
             {"uid": os.getuid(), "pid": True, "pid-start": "", "session": "x", "utc": base_utc}),
            ("non-int-pid",
             {"uid": os.getuid(), "pid": "1234", "pid-start": "", "session": "x", "utc": base_utc}),
        ]
        for label, owner in malformed_owners:
            if _journal.owner_confirmed_dead(owner):
                failures.append("B2d/{}: a live owner with a malformed identity must read possibly-live "
                                "(never seized)".format(label))
        for idx, (label, owner) in enumerate(malformed_owners):
            mroot = tmp / ("malformed-owner-{}".format(idx))
            mjr = mroot / JOURNAL_REL
            mjr.mkdir(parents=True)
            (mjr / "lock").write_bytes(json.dumps(owner, sort_keys=True).encode())
            try:
                _journal.read_lock_owner(mjr)
                failures.append("B2d/{}: read_lock_owner must fail closed on a malformed lock "
                                "(JournalError)".format(label))
            except _journal.JournalError:
                pass
            if _run(["recover", "--root", str(mroot)]) == 0:
                failures.append("B2d/{}: recover over a malformed live lock must not report success"
                                .format(label))
            if not (mjr / "lock").exists():
                failures.append("B2d/{}: recover must NEVER unlink a malformed (possibly-live) lock"
                                .format(label))
        checked += 1

        # (E1) The remove/rmdir prestate check and the mutation bind to the SAME pre-opened parent handle,
        #      so an ANCESTOR SWAP injected between the bind and the check cannot redirect the unlinkat/rmdir
        #      (spec 1291/1300). The swap is injected by wrapping _open_parent to rename the real parent
        #      aside and a decoy (matching the prestate) into its NAME on the first bind; the fd-bound check
        #      then sees the REAL (drifted) target and REFUSES, where a re-walk from root would have
        #      validated the decoy and clobbered the real target.
        for kind, mk_real, mk_decoy, prestate in (
                ("remove",
                 lambda d: ((d / "t").write_bytes(b"DRIFTED\n"), os.chmod(d / "t", 0o644)),
                 lambda d: ((d / "t").write_bytes(b"REAL\n"), os.chmod(d / "t", 0o644)),
                 {"kind": "file", "mode": 0o644, "sha256": hashlib.sha256(b"REAL\n").hexdigest()}),
                ("rmdir",
                 lambda d: ((d / "t").mkdir(), os.chmod(d / "t", 0o700)),
                 lambda d: ((d / "t").mkdir(), os.chmod(d / "t", 0o755)),
                 {"kind": "dir", "mode": 0o755})):
            e1 = tmp / ("e1-" + kind)
            (e1 / "P").mkdir(parents=True)
            (e1 / "D").mkdir()
            mk_real(e1 / "P")
            mk_decoy(e1 / "D")
            e1fd = os.open(str(e1), os.O_RDONLY | os.O_DIRECTORY)
            orig_open_parent = _journal._open_parent
            e1state = {"swapped": False}

            def _swapping_open_parent(root_fd, relpath, _orig=orig_open_parent, _e1=e1, _st=e1state):
                res = _orig(root_fd, relpath)
                if not _st["swapped"]:
                    _st["swapped"] = True
                    os.rename(str(_e1 / "P"), str(_e1 / "P_old"))   # real parent aside (pfd already bound to it)
                    os.rename(str(_e1 / "D"), str(_e1 / "P"))       # decoy into the real parent's NAME
                return res

            _journal._open_parent = _swapping_open_parent
            raised = False
            try:
                _journal.apply_ops(e1fd, [{"op": kind, "path": "P/t", "prestate": prestate}], lambda op: b"")
            except _journal.JournalError:
                raised = True
            finally:
                _journal._open_parent = orig_open_parent
                os.close(e1fd)
            if not raised:
                failures.append("E1/{}: an ancestor swap between check and mutate must be caught by the "
                                "fd-bound prestate check".format(kind))
            if not (e1 / "P_old" / "t").exists():
                failures.append("E1/{}: the fd-bound mutation must not clobber the real target validated "
                                "against a decoy".format(kind))
            checked += 1

        # (E3) root-validation order: a symlinked --root is refused (exit 2) BEFORE any root-relative read,
        #      write, or lock, in cutover AND recover: no journal dir is created and no lock is left. Without
        #      the fix, cutover mkdir's the journal dir and recover claims/leaves a lock before the root is
        #      ever validated.
        e3real = _build_case_root(tmp / "e3" / "real", "flat-files")
        e3staged = _build_staged(tmp / "e3" / "staged", "flat-files")
        e3link = tmp / "e3" / "link"
        os.symlink(str(e3real), str(e3link))
        if _run(["cutover", "--root", str(e3link), "--staged", str(e3staged), "--unit", _SELFTEST_UNIT]) != 2:
            failures.append("E3: cutover via a symlinked --root must be refused (exit 2)")
        if (e3real / JOURNAL_REL).exists():
            failures.append("E3: cutover via a symlinked --root must create NO journal dir")
        if _run(["recover", "--root", str(e3link)]) != 2:
            failures.append("E3: recover via a symlinked --root must be refused (exit 2)")
        if (e3real / JOURNAL_REL / "lock").exists():
            failures.append("E3: recover via a symlinked --root must leave no lock")
        checked += 1

        # (E4) a confirmed-DEAD stale lock is NOT broken until reconciliation establishes terminal state
        #      (spec 1262). Over an UNRECONCILABLE journal (an invalid frame sequence recover() rejects), the
        #      stale lease with the DEAD owner MUST remain (fail-closed, exit 2). Without reconcile-before-
        #      break, the dead lock is unlinked and replaced by this recover's own lock before reconciliation.
        e4root = _build_case_root(tmp / "e4" / "root", "flat-files")
        e4jr = e4root / JOURNAL_REL
        e4jr.mkdir(parents=True, exist_ok=True)
        badtxn = e4jr / "badtxn"
        badtxn.mkdir()
        _journal.publish(badtxn, _journal.F_INTENT, {"txn": "A", "header": {}, "ops": []})
        _journal.publish(badtxn, _journal.F_RC, {"txn": "A"})    # INTENT then RC (no RIP): recover() rejects
        dead = subprocess.Popen([sys.executable, "-c", "pass"])   # a pid that is confirmed dead once reaped
        dead.wait()
        (e4jr / "lock").write_bytes(json.dumps(
            {"uid": os.getuid(), "pid": dead.pid, "session": "dead", "pid-start": "",
             "utc": "2026-01-01T00:00:00Z"}, sort_keys=True).encode())
        if _run(["recover", "--root", str(e4root)]) != 2:
            failures.append("E4: recover over an unreconcilable journal must fail closed (exit 2)")
        owner4 = _journal.read_lock_owner(e4jr)
        if not (owner4 and owner4.get("pid") == dead.pid):
            failures.append("E4: a stale lock must NOT be broken until reconciliation establishes terminal "
                            "state (the dead owner's lease must remain when a journal fails to reconcile)")
        checked += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: crash-injection recovery proven from the journal alone across {} scenarios "
          "over 4 synthetic off-path cases across 3 tree structures (flat files, nested create, nested "
          "remove, plus a umask-reduced-mode remove variant). Every cutover-"
          "phase kill point (after-lock, each preimage, after-preimages, torn INTENT, after INTENT, each "
          "apply, torn COMPLETE, after COMPLETE) recovers the tree to EXACTLY the prestate or the "
          "verified poststate, terminal and idempotent (a second recover is a no-op); mid-rollback and "
          "torn-rollback-marker crashes still land the prestate; a torn payload mid-write rolls back to "
          "the prestate exactly; the inert 10.6 reverse-replay primitive inverts a completed cutover's "
          "ops in reverse dependency order; and missing quiescence "
          "evidence, an on-path staged tree, a possibly-live lock, and a "
          "mid-log corrupt frame each fail closed.".format(checked))
    return 0


def _latest_txn(root):
    journal_root = Path(root) / JOURNAL_REL
    best = None
    if not journal_root.is_dir():
        return None
    for txn_dir in _txn_dirs(journal_root):
        if "unadopt" in txn_dir.name:
            continue
        try:
            if _journal.F_COMPLETE in [t for t, _ in _journal.read_frames(txn_dir)[0]]:
                best = txn_dir.name
        except _journal.JournalError:
            continue
    return best


if __name__ == "__main__":
    sys.exit(main())
