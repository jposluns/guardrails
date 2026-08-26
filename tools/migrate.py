#!/usr/bin/env python3
"""Migration engine for the OPT-IN adopter path (VER-CORE 9.1 to 9.3, Section 12 step 6). Stdlib only.

  migrate.py plan     --root DIR                          crosswalk connected components (units, 9.1)
  migrate.py cutover  --root DIR --staged DIR --unit ID   journaled transactional apply (9.2, 9.3)
  migrate.py recover  --root DIR                          reconcile an open journal (idempotent, 9.3)
  migrate.py status   --root DIR                          report journal / transaction state
  migrate.py un-adopt --root DIR --txn ID                 reverse-replay one terminal migration txn (10.6)
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

MIGRATION_REL = ".aiqt/migration"
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
    return os.open(str(root), os.O_RDONLY | os.O_DIRECTORY)


def _claim_recover_lock(journal_root):
    """Atomically claim the journal lock for recovery (fix #3). Returns 'acquired' when this process now
    owns the lock (the lock was absent, or a confirmed-dead stale lock was broken and re-acquired), or
    'possibly-live' when a lock whose owner may still be alive holds it (never seized, the concurrency-
    lease rule). Fail-closed (JournalError) on an unreadable lock or a lost stale-break race (another
    recover re-created the lock first, so acquire_lock's O_EXCL fails rather than seizing it)."""
    owner = _journal.read_lock_owner(journal_root)
    if owner is None:
        _journal.acquire_lock(journal_root, session_id="recover")
        return "acquired"
    if not _journal.owner_confirmed_dead(owner):
        return "possibly-live"
    _journal.break_stale_lock(journal_root, owner)        # confirmed dead: break then re-acquire (O_EXCL)
    _journal.acquire_lock(journal_root, session_id="recover")
    return "acquired"


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
    try:
        _assert_off_path(root, staged)
        _require_quiescence(staged)
        ops = _read_staged_plan(staged)
    except RefuseError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    journal_root = root / JOURNAL_REL
    journal_root.mkdir(parents=True, exist_ok=True)
    txn_id = "{}.{}.{}".format(_slug(unit), os.getpid(), time.time_ns())
    root_fd = _open_root_fd(root)
    try:
        try:
            _journal.acquire_lock(journal_root, session_id="cutover")
        except _journal.JournalError as exc:
            print("error: {}; fail-closed".format(exc), file=sys.stderr)
            return 2
        header = {"unit": unit, "kind": "cutover"}
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
    """A JournalError escaped run_transaction: either an apply/prestate failure whose rollback reached a
    terminal ROLLBACK-COMPLETE, or a rollback that ITSELF failed (a _restore_preimage error) and left the
    journal non-terminal. Distinguish the two by the DURABLE terminal state (is_terminal), never by
    assumption (fix #4). If a terminal rollback was published, the tree is back at its prestate: release
    the lock and report the clean rollback, exit 2. If NOT (the rollback failed or the journal is
    unreadable), the transaction is left open and fail-closed under the RETAINED lock for a later recover,
    no rollback is claimed, exit 2."""
    try:
        terminal = _journal.is_terminal(txn_dir)
    except _journal.JournalError:
        terminal = False                                  # unreadable journal: not a confirmed rollback
    if terminal:
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
    journal_root = root / JOURNAL_REL
    if not journal_root.is_dir():
        print("recover: no journal at {} (nothing to recover)".format(JOURNAL_REL))
        return 0
    # Atomically CLAIM the lock before touching any transaction (fix #3): acquire an absent lock, or break
    # a confirmed-dead stale lock and re-acquire it (O_EXCL); a possibly-live owner is NEVER seized. Only
    # the lock THIS recover owns is released, after every transaction reaches a terminal state.
    try:
        claim = _claim_recover_lock(journal_root)
    except _journal.JournalError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if claim == "possibly-live":
        owner = _journal.read_lock_owner(journal_root)
        print("recover: journal lock is held by a possibly-live owner (pid {}); NOT seized"
              .format((owner or {}).get("pid")), file=sys.stderr)
        return 1
    root_fd = _open_root_fd(root)
    outcomes = {}
    try:
        for txn_dir in _txn_dirs(journal_root):
            try:
                outcomes[txn_dir.name] = _journal.recover(txn_dir, root_fd)
            except _journal.JournalError as exc:
                print("error: cannot recover {} ({}); fail-closed".format(txn_dir.name, exc),
                      file=sys.stderr)
                return 2                                   # lock RETAINED: the journal is not terminal
    finally:
        os.close(root_fd)
    _journal.release_lock(journal_root)                   # release only the lock THIS recover owns
    if outcomes:
        for name, outcome in sorted(outcomes.items()):
            print("recover: txn {} -> {}".format(name, outcome))
    else:
        print("recover: no transactions to recover")
    return 0


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


def do_unadopt(root, txn_id):
    """Reverse-replay ONE terminal migration transaction (the 10.6 engine primitive). Builds the inverse
    ops from the source transaction's INTENT and replays them as a NEW journaled transaction whose data
    comes from the source's retained preimages, so the reversal is itself crash-durable. Cross-
    transaction ordering, authorization, repoint inversion, and pin.toml removal are Section 12 step 7
    (pin.py), NOT this VC-6 slice; this exposes only the tree-reversal primitive."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    journal_root = root / JOURNAL_REL
    src = journal_root / _slug(txn_id)
    if not src.is_dir():
        print("error: no transaction {} to reverse; fail-closed".format(txn_id), file=sys.stderr)
        return 2
    try:
        frames, torn, _ = _journal.read_frames(src)
    except _journal.JournalError as exc:
        print("error: cannot read {} ({}); fail-closed".format(txn_id, exc), file=sys.stderr)
        return 2
    types = [t for t, _ in frames]
    if _journal.F_COMPLETE not in types:
        print("error: {} is not a COMPLETE (forward-applied) transaction; only a completed cutover can "
              "be reversed; fail-closed".format(txn_id), file=sys.stderr)
        return 2
    intent = _journal._first(frames, _journal.F_INTENT)
    try:
        inverse = _journal.build_inverse_ops(intent["ops"])
    except _journal.JournalError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    def reader(iop):
        source_op = iop["_source"]
        return (src / "preimages" / source_op["prestate"]["payload"]).read_bytes()

    new_txn = "{}.unadopt.{}".format(_slug(txn_id), time.time_ns())
    root_fd = _open_root_fd(root)
    try:
        try:
            _journal.acquire_lock(journal_root, session_id="un-adopt")
        except _journal.JournalError as exc:
            print("error: {}; fail-closed".format(exc), file=sys.stderr)
            return 2
        txn_dir = journal_root / new_txn
        try:
            _journal.run_transaction(root_fd, journal_root, new_txn,
                                     {"kind": "un-adopt", "reverses": txn_id}, inverse, reader,
                                     session_id="un-adopt")
        except _journal.JournalError as exc:
            return _settle_failed_transaction(journal_root, txn_dir, exc, "un-adopt")
        _journal.release_lock(journal_root)
    finally:
        os.close(root_fd)
    print("un-adopt complete: reversed {} as {} (archives are permanent and untouched, 8.2)"
          .format(txn_id, new_txn))
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
    if cmd in ("plan", "cutover", "recover", "status", "un-adopt") and root is None:
        print("error: --root DIR is required", file=sys.stderr)
        return 2
    root = Path(root).resolve() if root else None
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
    if cmd == "un-adopt":
        txn = _arg(args, "--txn")
        if not txn:
            print("error: un-adopt requires --txn ID", file=sys.stderr)
            return 2
        return do_unadopt(root, txn)
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
# Three synthetic off-path trees exercise flat files, a nested directory create, and a nested directory
# remove, so reverse-dependency ordering and domain-separated post-states are covered in both directions.
# A final un-adopt round-trip proves the 10.6 reverse-replay primitive returns post to pre.

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
            if _run(["cutover", "--root", str(broot), "--staged", str(bstaged), "--unit", "u1"]) != 0:
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
                rc = _run(["cutover", "--root", str(iroot), "--staged", str(istaged), "--unit", "u1"],
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
                if _run(["cutover", "--root", str(iroot), "--staged", str(istaged), "--unit", "u1"],
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
        #     ELECT ROLLBACK and land on the prestate EXACTLY (never the poststate), exercising the post-
        #     restore preimage digest check on the rollback path.
        for case in _CASES:
            pre, _post = baselines[case]
            for i, op in enumerate(_CASES[case]["ops"]):
                if op["op"] not in ("write", "create"):
                    continue
                point = "torn-payload:{}".format(i)
                iroot = _build_case_root(tmp / (case + "-tp") / "op-{}".format(i) / "root", case)
                istaged = _build_staged(tmp / (case + "-tp") / "op-{}".format(i) / "staged", case)
                rc = _run(["cutover", "--root", str(iroot), "--staged", str(istaged), "--unit", "u1"],
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

        # (D) un-adopt round-trip: a completed cutover, then reverse-replay, returns post to pre.
        for case in _CASES:
            uroot = _build_case_root(tmp / (case + "-unadopt") / "root", case)
            ustaged = _build_staged(tmp / (case + "-unadopt") / "staged", case)
            pre = _snapshot(uroot)
            if _run(["cutover", "--root", str(uroot), "--staged", str(ustaged), "--unit", "u1"]) != 0:
                failures.append("{} un-adopt: setup cutover expected exit 0".format(case))
                continue
            txn = _latest_txn(uroot)
            if txn is None:
                failures.append("{} un-adopt: no completed txn to reverse".format(case))
                continue
            if _run(["un-adopt", "--root", str(uroot), "--txn", txn]) != 0:
                failures.append("{} un-adopt: reverse-replay expected exit 0".format(case))
            if _snapshot(uroot) != pre:
                failures.append("{} un-adopt: reverse-replay did not return post to pre".format(case))
            checked += 1

        # (E) Fail-closed preconditions: missing quiescence evidence, and an on-path staged tree.
        froot = _build_case_root(tmp / "fc" / "root", "flat-files")
        fstaged = _build_staged(tmp / "fc" / "staged", "flat-files")
        (fstaged / "quiescence.ok").unlink()
        if _run(["cutover", "--root", str(froot), "--staged", str(fstaged), "--unit", "u1"]) != 2:
            failures.append("missing quiescence evidence expected exit 2 (refused)")
        checked += 1
        onpath = _build_case_root(tmp / "onpath" / "root", "flat-files")
        instaged = _build_staged(onpath / "inside-staged", "flat-files")
        if _run(["cutover", "--root", str(onpath), "--staged", str(instaged), "--unit", "u1"]) != 2:
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
        _run(["cutover", "--root", str(croot), "--staged", str(cstaged), "--unit", "u1"])
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
            if _run(["cutover", "--root", str(iroot), "--staged", str(istaged), "--unit", "u1"],
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
        _run(["cutover", "--root", str(aroot), "--staged", str(astaged), "--unit", "u1"],
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
                rc = do_cutover(Path(rbroot).resolve(), rbstaged.resolve(), "u1")
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: crash-injection recovery proven from the journal alone across {} scenarios "
          "over 3 synthetic off-path trees (flat files, nested create, nested remove). Every cutover-"
          "phase kill point (after-lock, each preimage, after-preimages, torn INTENT, after INTENT, each "
          "apply, torn COMPLETE, after COMPLETE) recovers the tree to EXACTLY the prestate or the "
          "verified poststate, terminal and idempotent (a second recover is a no-op); mid-rollback and "
          "torn-rollback-marker crashes still land the prestate; a torn payload mid-write rolls back to "
          "the prestate exactly; the un-adopt reverse-replay returns post to pre; and missing quiescence "
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
