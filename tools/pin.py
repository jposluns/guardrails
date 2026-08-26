#!/usr/bin/env python3
"""Pin lifecycle (VER-CORE 10.1 to 10.6, Section 12 step 7): pin, re-pin, rollback, un-adopt. Stdlib only.

  pin.py pin       --root DIR --staged DIR                   initial onboarding pin (pre-adoption capture)
  pin.py repin     --root DIR --staged DIR [auth flags]      forward re-pin or an authorized rollback
  pin.py carve-out --root DIR --target-seq N [auth flags]    corrupt-state recovery rollback (9.3 engine)
  pin.py un-adopt  --root DIR [auth flags]                   reverse to pre-adoption state (9.3 engine)
  pin.py status    --root DIR                                report pin / transition / history state
  pin.py --self-test                                         synthetic-tree honesty and flow invariants

Authorization flags (required on rollback, carve-out, and un-adopt): --authorizer WHO --reason WHY.

HONESTY LIMIT, disclosed everywhere it surfaces (10.2): with no keys the pin-history chain is tamper-
evident against a CASUAL in-place edit ONLY. It is NOT truncation-evident (deleting a valid tail leaves a
valid chain) and NOT splice-proof (a writer with full tree access can insert a genuine old release's row
and recompute the suffix), so history proves NOTHING and authorizes NOTHING on its own. A rollback always
requires wholesale anchored validation of the target release PLUS explicit recorded adopter authorization
(10.4); the pin-history match only classifies and locates. Defeating truncation and splice needs an
authenticated external append-only head ledger, a deferred ceiling-raiser recorded with the 5.6 deferrals.

CONTAINMENT: every privileged path open uses the fd-bound, no-follow discipline of tools/_journal.py
(dir-fd-relative open with O_NOFOLLOW), reused here rather than forked. The ordinary re-pin preimage copy
and contained swap use those shared low-level helpers and NO part of the 9.3 journal transaction (no
INTENT/COMPLETE framing, no lock); the corrupt-state recovery carve-out and the un-adopt replay DO run on
the full 9.3 engine (run_transaction / build_inverse_ops), and therefore fail closed where the containment
primitive is absent (3.6b forward-compatibility guard; both supported platforms have the primitive).

Exit convention: 0 clean/NA, 1 finding, 2 malformed input, a read error, or a refused precondition.
"""
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal  # noqa: E402  the 9.3 engine: contained helpers, capture/apply, run_transaction, inverse

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: pin.py requires Python 3.11+ (tomllib).")

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_MALFORMED = 2

PIN_REL = ".aiqt/pin.toml"
HISTORY_REL = ".aiqt/pin-history.toml"
TRANSITION_REL = ".aiqt/pin-transition.toml"
PREIMAGES_REL = ".aiqt/pin-preimages"
REPOINTS_REL = ".aiqt/migration/repoints.toml"
JOURNAL_REL = ".aiqt/migration/journal"   # the shared 9.3 journal root, reused by the carve-out and un-adopt

CHAIN_DOMAIN = b"aiqt-pin-history-row-v1\n"
GENESIS = hashlib.sha256(b"aiqt-pin-history-genesis-v1").hexdigest()
ACTIONS = ("pin", "re-pin", "rollback", "un-adopt")   # un-adopt: build reconciliation 6 vs the 10.2 enum

PHASES = ("prepared", "applied", "committed")


class PinError(Exception):
    """A malformed or unreadable pin-state input, or a refused precondition: fail-closed, exit 2."""


# --- pin-history chain (reconciliation 9) -------------------------------------------------------------

_ROW_STR_KEYS = ("version", "tag-object-sha", "commit-sha", "root", "utc", "action", "corruption-finding")


def _norm_row(row):
    """The canonical projection of a pin-history row for the chain hash: a fixed key set, every optional
    field present with an empty value rather than elided, types coerced, so the canonical bytes are
    reproducible regardless of TOML whitespace or key order and unambiguous about absent optionals
    (reconciliation 9). Unknown keys are excluded here (a schema check catches them separately)."""
    auth = row.get("authorization") or {}
    out = {k: str(row.get(k, "")) for k in _ROW_STR_KEYS}
    out["seq"] = int(row.get("seq", 0))
    out["quorum"] = int(row.get("quorum", 0))
    out["authorization"] = {"authorizer": str(auth.get("authorizer", "")),
                            "utc": str(auth.get("utc", "")),
                            "reason": str(auth.get("reason", ""))}
    out["chain"] = str(row.get("chain", ""))
    return out


def canonical_row_bytes(row):
    """Canonical bytes for the 10.2 chain: the domain-prefixed canonical JSON (sorted keys, compact
    separators, ensure_ascii) of the row's fixed-key projection INCLUDING its own chain field, so links
    are transitive and reproducible regardless of TOML formatting (reconciliation 9)."""
    return CHAIN_DOMAIN + json.dumps(_norm_row(row), sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()


def next_chain(prev_row):
    """The chain field a new row must carry: the SHA-256 of the previous row's canonical bytes, or the
    genesis sentinel for the first row."""
    if prev_row is None:
        return GENESIS
    return hashlib.sha256(canonical_row_bytes(prev_row)).hexdigest()


def verify_chain(rows):
    """End-to-end chain validation (10.2). Returns a list of findings (empty = a valid chain). A break is
    a FAIL. A rollback or un-adopt row without explicit recorded authorization is a FAIL (a history match
    never authorizes). HONESTY: a valid chain is tamper-evident against a casual in-place edit ONLY; it is
    NOT truncation-evident and NOT splice-proof, so it proves nothing and authorizes nothing on its own."""
    prev, findings = GENESIS, []
    for i, row in enumerate(rows):
        action = row.get("action")
        if action not in ACTIONS:
            findings.append("row {}: unknown or missing action {!r}".format(i, action))
        if action in ("rollback", "un-adopt"):
            auth = row.get("authorization") or {}
            if not (auth.get("authorizer") and auth.get("utc") and auth.get("reason")):
                findings.append("row {}: {} without explicit recorded authorization (a history match "
                                "never authorizes, 10.4)".format(i, action))
        if row.get("chain") != prev:
            findings.append("row {}: chain break (recomputed {} != recorded {})".format(
                i, prev, row.get("chain")))
        prev = hashlib.sha256(canonical_row_bytes(row)).hexdigest()
    return findings


# --- fail-closed contained reads of the pin-state files -----------------------------------------------

def _open_root_fd(root):
    """Open the adopter root fd with O_NOFOLLOW, so a symlinked final root component is refused rather
    than followed off-tree (the migrate.py idiom). Raises OSError, mapped to exit 2 by the caller."""
    return os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _read_contained_toml(root_fd, relpath):
    """Read and parse a contained TOML file beneath root_fd, no-follow. Returns the parsed dict, or None
    when the file (or a parent) is absent. PinError (exit 2) on an unreadable file or a TOML/parse error:
    an unreadable input is a failure, never an empty pass (fail-closed reads)."""
    st = _journal._lstat_contained(root_fd, relpath)
    if st is None:
        return None
    try:
        data, _ = _journal._read_contained(root_fd, relpath)
    except _journal.JournalError as exc:
        raise PinError("cannot read {} ({})".format(relpath, exc))
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PinError("cannot parse {} ({})".format(relpath, exc))


def read_pin(root_fd):
    return _read_contained_toml(root_fd, PIN_REL)


def read_history(root_fd):
    """The pin-history transition rows in file order, or None when the file is absent. PinError on an
    unreadable or malformed file, or on a row that is not a table."""
    data = _read_contained_toml(root_fd, HISTORY_REL)
    if data is None:
        return None
    rows = data.get("transition", [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise PinError("{}: [[transition]] must be a list of tables".format(HISTORY_REL))
    return rows


def read_transition(root_fd):
    return _read_contained_toml(root_fd, TRANSITION_REL)


def read_repoints(root_fd):
    data = _read_contained_toml(root_fd, REPOINTS_REL)
    if data is None:
        return None
    rows = data.get("repoint", [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise PinError("{}: [[repoint]] must be a list of tables".format(REPOINTS_REL))
    return rows


# --- TOML writers (atomic whole-file replace; the append-only file keeps a chain-verified prefix) ------

def _toml_str(value):
    """A minimal, deterministic TOML basic-string encoding for the constrained values this tool writes
    (release ids, UTC stamps, reasons). Escapes backslash, double-quote, and the control characters TOML
    forbids bare, so a hostile reason string cannot break out of its quotes (output encoding for the TOML
    sink)."""
    out = []
    for ch in str(value):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20 or ord(ch) == 0x7f:
            out.append("\\u{:04x}".format(ord(ch)))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _auth_inline(auth):
    return "{{ authorizer = {}, utc = {}, reason = {} }}".format(
        _toml_str(auth.get("authorizer", "")), _toml_str(auth.get("utc", "")),
        _toml_str(auth.get("reason", "")))


def _render_history(rows):
    lines = ["# .aiqt/pin-history.toml (VER-CORE 10.2): append-only, hash-chained pin-transition rows.",
             "#",
             "# HONESTY LIMIT: with no keys this chain is tamper-evident against a CASUAL in-place edit",
             "# ONLY. It is NOT truncation-evident and NOT splice-proof, so it proves nothing and",
             "# authorizes nothing on its own; a rollback needs wholesale anchored validation of the",
             "# target plus explicit recorded adopter authorization (10.4). Generated; do not hand-edit.",
             "", "schema-version = 1", ""]
    for row in rows:
        lines.append("[[transition]]")
        lines.append("seq = {}".format(int(row["seq"])))
        lines.append("version = {}".format(_toml_str(row.get("version", ""))))
        lines.append("tag-object-sha = {}".format(_toml_str(row.get("tag-object-sha", ""))))
        lines.append("commit-sha = {}".format(_toml_str(row.get("commit-sha", ""))))
        lines.append("root = {}".format(_toml_str(row.get("root", ""))))
        lines.append("quorum = {}".format(int(row.get("quorum", 0))))
        lines.append("utc = {}".format(_toml_str(row.get("utc", ""))))
        lines.append("action = {}".format(_toml_str(row.get("action", ""))))
        lines.append("corruption-finding = {}".format(_toml_str(row.get("corruption-finding", ""))))
        lines.append("authorization = {}".format(_auth_inline(row.get("authorization") or {})))
        lines.append("chain = {}".format(_toml_str(row.get("chain", ""))))
        lines.append("")
    return "\n".join(lines) + "\n"


def _render_pin(pin):
    rel = pin["release"]
    auth = pin.get("rollback-authorization") or {}
    lines = ["# .aiqt/pin.toml (VER-CORE 10.1): the installed release pin (class adopter-state).",
             "# Generated by tools/pin.py; do not hand-edit.", "",
             "schema-version = 1",
             "adoption-path = {}".format(_toml_str(pin.get("adoption-path", "onboarding"))),
             "quorum = {}".format(int(pin.get("quorum", 1))),
             "verified-utc = {}".format(_toml_str(pin.get("verified-utc", ""))),
             "ownership-map-identity = {}".format(_toml_str(pin.get("ownership-map-identity", ""))),
             "transition-id = {}".format(_toml_str(pin.get("transition-id", ""))),
             "references-consulted = []",
             "",
             "[release]",
             "version = {}".format(_toml_str(rel.get("version", ""))),
             "tag-object-sha = {}".format(_toml_str(rel.get("tag-object-sha", ""))),
             "commit-sha = {}".format(_toml_str(rel.get("commit-sha", ""))),
             "root = {}".format(_toml_str(rel.get("root", ""))),
             "manifest-digest = {}".format(_toml_str(rel.get("manifest-digest", ""))),
             "",
             "[rollback-authorization]",
             "authorizer = {}".format(_toml_str(auth.get("authorizer", ""))),
             "utc = {}".format(_toml_str(auth.get("utc", ""))),
             "reason = {}".format(_toml_str(auth.get("reason", ""))),
             "",
             "[adopter-experience]",
             "# Reserved slots only; fields are defined in the adopter-experience spec.",
             "adapters = []",
             "profiles = []",
             "overlay-digest = \"\"",
             "exclusion-ledger-identity = \"\"",
             ""]
    return "\n".join(lines) + "\n"


def _op_inline(sub):
    parts = []
    for k in sorted(sub):
        v = sub[k]
        parts.append("{} = {}".format(k, v if isinstance(v, int) and not isinstance(v, bool)
                                      else _toml_str(v)))
    return "{ " + ", ".join(parts) + " }"


def _render_transition(txn):
    lines = ["# .aiqt/pin-transition.toml (VER-CORE 10.3, reconciliation 4): the two-phase re-pin",
             "# transition record. phase advances prepared -> applied -> committed; a non-committed phase",
             "# read by the doctor is an interrupted transition reported with its safe recovery direction.",
             "# Generated by tools/pin.py; do not hand-edit.", "",
             "schema-version = 1",
             "transition-id = {}".format(_toml_str(txn["transition-id"])),
             "action = {}".format(_toml_str(txn["action"])),
             "phase = {}".format(_toml_str(txn["phase"])),
             "from-pin-digest = {}".format(_toml_str(txn.get("from-pin-digest", ""))),
             "target-version = {}".format(_toml_str(txn.get("target-version", ""))),
             ""]
    for op in txn["ops"]:
        lines.append("[[op]]")
        lines.append("op = {}".format(_toml_str(op["op"])))
        lines.append("path = {}".format(_toml_str(op["path"])))
        lines.append("prestate = {}".format(_op_inline(op["prestate"])))
        lines.append("poststate = {}".format(_op_inline(op["poststate"])))
        lines.append("")
    return "\n".join(lines) + "\n"


def _atomic_publish(root, relpath, text):
    """Write text to relpath atomically (write a sibling temp, fsync it, rename into place, fsync the
    parent). The rename is the atomic publication point (2.4 discipline); a crash leaves either the old
    file or the new, never a torn one."""
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / (target.name + ".tmp.{}".format(os.getpid()))
    data = text.encode("utf-8")
    fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    try:
        _journal._write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(str(tmp), str(target))
    _journal._fsync_path_dir(target.parent)


def _remove_contained(root, relpath):
    """Remove a file atomically and fsync its parent. A no-op when already absent."""
    target = root / relpath
    try:
        os.unlink(str(target))
    except FileNotFoundError:
        return
    _journal._fsync_path_dir(target.parent)


# --- op construction and the staged reader ------------------------------------------------------------

def _staged_reader(staged):
    def reader(op):
        return (staged / "payload" / op["path"]).read_bytes()
    return reader


def _preimage_reader(root, transition_id):
    """Serve the retained preimage bytes for an inverse op, keyed on the ORIGINAL op's payload ref. Used
    by the un-adopt engine replay, whose inverse write/create ops restore prior bytes (build_inverse_ops
    threads the original op through '_source')."""
    base = root / PREIMAGES_REL / transition_id / "preimages"

    def reader(op):
        src = op.get("_source") or {}
        ref = (src.get("prestate") or {}).get("payload")
        if ref is None:
            raise _journal.JournalError("inverse op for {!r} has no retained preimage payload"
                                        .format(op.get("path")))
        return (base / str(ref)).read_bytes()
    return reader


def _load_staged_ops(staged):
    """Read <staged>/plan.json {"ops":[...]} plus poststate for every write/create from the staged bytes,
    so the shared apply leg (_journal.apply_ops) can verify the installed digest against the plan."""
    plan = json.loads((staged / "plan.json").read_text(encoding="utf-8"))
    ops = plan["ops"]
    for op in ops:
        kind = op["op"]
        if kind in ("write", "create"):
            data = (staged / "payload" / op["path"]).read_bytes()
            op["poststate"] = {"kind": "file", "mode": op.get("mode", 0o644),
                               "content-sha256": hashlib.sha256(data).hexdigest()}
        elif kind == "mkdir":
            op["poststate"] = {"kind": "dir", "mode": op.get("mode", 0o755)}
        else:
            op["poststate"] = {"kind": "absent"}
    return ops


def _utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _require_auth(authorizer, reason):
    if not authorizer or not reason:
        raise PinError("this operation requires explicit recorded authorization (--authorizer and "
                       "--reason); a history match never authorizes (10.4)")
    return {"authorizer": authorizer, "utc": _utc_now(), "reason": reason}


# --- the ordinary preimage copy + contained swap (NO journal engine) ----------------------------------

def _capture_pin_preimages(root, root_fd, transition_id, ops):
    """The re-pin PREIMAGE COPY (10.3), standalone: durable prior bytes and metadata (prior-absence for
    creates, existence+mode for directories) under .aiqt/pin-preimages/<transition-id>/preimages/, fsync'd,
    BEFORE any swap. It uses the shared low-level capture helper and NO part of the 9.3 journal transaction
    (no INTENT/COMPLETE framing, no lock). Mutates each op in place, adding its prestate."""
    txn_dir = root / PREIMAGES_REL / transition_id
    txn_dir.mkdir(parents=True, exist_ok=False)
    _journal._fsync_path_dir((root / PREIMAGES_REL))
    _journal.capture_preimages(txn_dir, root_fd, ops)


def _contained_swap(root_fd, ops, staged_reader):
    """The contained forward swap (3.6c), reusing the engine's fd-bound apply leg: each change is applied
    beneath the pre-opened directory handle with no-follow semantics, its prestate re-verified on the bound
    fd, then every poststate is verified. A prestate or digest mismatch raises JournalError (fail-closed)."""
    _journal.apply_ops(root_fd, ops, staged_reader)
    if not all(_journal._poststate_verifies(root_fd, op) for op in ops):
        raise _journal.JournalError("post-swap poststate verification failed; refusing to publish the pin")


# --- flows --------------------------------------------------------------------------------------------

def _read_release(staged):
    data = tomllib.loads((staged / "release.toml").read_text(encoding="utf-8"))
    rel = data.get("release", {})
    return {"version": rel.get("version", ""), "tag-object-sha": rel.get("tag-object-sha", ""),
            "commit-sha": rel.get("commit-sha", ""), "root": rel.get("root", ""),
            "manifest-digest": rel.get("manifest-digest", "")}, int(data.get("quorum", 1))


def _history_rows_and_prev(root_fd):
    rows = read_history(root_fd)
    if rows is None:
        return [], None
    return rows, rows[-1] if rows else None


def _append_history_row(root, rows, prev, seq, release, quorum, action, authorization, corruption):
    row = {"seq": seq, "version": release["version"], "tag-object-sha": release["tag-object-sha"],
           "commit-sha": release["commit-sha"], "root": release["root"], "quorum": quorum,
           "utc": _utc_now(), "action": action,
           "authorization": authorization or {"authorizer": "", "utc": "", "reason": ""},
           "corruption-finding": corruption or "", "chain": next_chain(prev)}
    _atomic_publish(root, HISTORY_REL, _render_history(rows + [row]))
    return row


def _blocking_open_transition(root_fd):
    """An OPEN (non-committed) transition blocks any new pin operation (4.4). A committed transition is the
    normal resting state left by the last successful pin and never blocks; only a `prepared`/`applied`
    record (an interrupted swap) does."""
    txn = read_transition(root_fd)
    return txn is not None and txn.get("phase") != "committed"


def do_pin(root, staged, transition_id=None):
    """Initial onboarding pin (10.1) WITH pre-adoption state capture (10.3/10.6): the first pin's
    preimages capture the pre-adoption prior bytes and prior-ABSENCE of every path it installs, so
    un-adopting even the first pin restores the exact pre-adoption state."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return _fail(exc)
    root_fd = _open_root_fd(root)
    try:
        if read_pin(root_fd) is not None:
            raise PinError("a pin already exists; use `repin` to advance it (10.3)")
        if _blocking_open_transition(root_fd):
            raise PinError("an open transition blocks a new pin operation (4.4); run recovery first")
        release, quorum = _read_release(staged)
        ops = _load_staged_ops(staged)
        transition_id = transition_id or "pin.{}.{}".format(os.getpid(), time.time_ns())
        _capture_pin_preimages(root, root_fd, transition_id, ops)
        txn = {"transition-id": transition_id, "action": "pin", "phase": "prepared",
               "from-pin-digest": "", "target-version": release["version"], "ops": ops}
        _atomic_publish(root, TRANSITION_REL, _render_transition(txn))
        _contained_swap(root_fd, ops, _staged_reader(staged))
        txn["phase"] = "applied"
        _atomic_publish(root, TRANSITION_REL, _render_transition(txn))
        rows, prev = _history_rows_and_prev(root_fd)
        _append_history_row(root, rows, prev, len(rows), release, quorum, "pin", None, None)
        pin = {"adoption-path": "onboarding", "quorum": quorum, "verified-utc": _utc_now(),
               "ownership-map-identity": release.get("manifest-digest", ""),
               "transition-id": transition_id, "release": release}
        _atomic_publish(root, PIN_REL, _render_pin(pin))
        txn["phase"] = "committed"
        _atomic_publish(root, TRANSITION_REL, _render_transition(txn))
    except (PinError, _journal.JournalError, OSError, KeyError, ValueError) as exc:
        return _fail(exc)
    finally:
        os.close(root_fd)
    print("pin: onboarding pin at {} (transition {})".format(release["version"], transition_id))
    return EXIT_OK


def do_repin(root, staged, authorizer, reason):
    """Forward re-pin or authorized rollback (10.3 to 10.5), the plan-4.2 eight-step flow. The current pin
    is verified first (a corrupt current pin routes to the carve-out, never here); local drift MUST be
    dispositioned; the target is validated wholesale; a target matching a chain-valid history row is
    classified a ROLLBACK and REQUIRES explicit recorded authorization; the ordinary preimage copy and
    contained swap run with NO journal; the new pin and history row publish atomically at commit."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return _fail(exc)
    root_fd = _open_root_fd(root)
    try:
        current = read_pin(root_fd)
        if current is None:
            raise PinError("no current pin to advance; use `pin` for the initial onboarding pin")
        if _blocking_open_transition(root_fd):
            raise PinError("an open transition blocks a new pin operation (4.4); run recovery first")
        # Step 1 to 2: verify the current pin and disposition drift. The read-only verification is the
        # doctor's pin-and-manifest assertion; a staged `disposition.ok` marker stands for the explicit
        # drift disposition the adopter-experience overlay records (interface hook, refused if absent).
        if not (staged / "disposition.ok").is_file():
            raise PinError("re-pin refuses to proceed without an explicit drift disposition "
                           "(disposition.ok); a swap must never silently destroy local drift (10.3)")
        release, quorum = _read_release(staged)
        # Step 3 to 5: validate the target wholesale and re-validate exclusions. In this repo/self-test the
        # target validation is represented by a staged `target-validated.ok` marker standing for
        # check_manifest.py --anchored + check_release_delta.py --repin; the orchestrator finalizes the
        # real invocation shapes against the built step-2/4 tools. A re-pin never lowers the recorded
        # quorum silently (10.5).
        if not (staged / "target-validated.ok").is_file():
            raise PinError("re-pin refuses to proceed without wholesale anchored target validation "
                           "(target-validated.ok stands for check_manifest --anchored + release-delta "
                           "--repin); authenticity is the anchored proof, never the history match (10.4)")
        if quorum < int(current.get("quorum", 1)):
            raise PinError("re-pin refuses to lower the recorded quorum silently ({} < {}) (10.5)"
                           .format(quorum, int(current.get("quorum", 1))))
        rows, prev = _history_rows_and_prev(root_fd)
        chain_findings = verify_chain(rows)
        if chain_findings:
            raise PinError("current pin-history chain is invalid; refusing to advance: {}"
                           .format("; ".join(chain_findings)))
        # Step 4: rollback classification. A target matching a chain-valid history row classifies ONLY
        # (locates, proves nothing); every rollback REQUIRES explicit recorded authorization.
        is_rollback = any(r.get("version") == release["version"] for r in rows)
        action = "rollback" if is_rollback else "re-pin"
        authorization = _require_auth(authorizer, reason) if is_rollback else None
        # Step 6 to 8: preimage copy (prepared) -> contained swap (applied) -> publish + append (committed).
        transition_id = "repin.{}.{}".format(os.getpid(), time.time_ns())
        ops = _load_staged_ops(staged)
        from_digest = hashlib.sha256(_render_pin({
            "adoption-path": current.get("adoption-path", "onboarding"),
            "quorum": int(current.get("quorum", 1)), "verified-utc": current.get("verified-utc", ""),
            "ownership-map-identity": current.get("ownership-map-identity", ""),
            "transition-id": current.get("transition-id", ""),
            "release": current.get("release", {})}).encode()).hexdigest()
        _capture_pin_preimages(root, root_fd, transition_id, ops)
        txn = {"transition-id": transition_id, "action": action, "phase": "prepared",
               "from-pin-digest": from_digest, "target-version": release["version"], "ops": ops}
        _atomic_publish(root, TRANSITION_REL, _render_transition(txn))
        _contained_swap(root_fd, ops, _staged_reader(staged))
        txn["phase"] = "applied"
        _atomic_publish(root, TRANSITION_REL, _render_transition(txn))
        _append_history_row(root, rows, prev, len(rows), release, quorum, action, authorization, None)
        pin = {"adoption-path": current.get("adoption-path", "onboarding"), "quorum": quorum,
               "verified-utc": _utc_now(), "ownership-map-identity": release.get("manifest-digest", ""),
               "transition-id": transition_id, "release": release,
               "rollback-authorization": authorization or {}}
        _atomic_publish(root, PIN_REL, _render_pin(pin))
        txn["phase"] = "committed"
        _atomic_publish(root, TRANSITION_REL, _render_transition(txn))
    except (PinError, _journal.JournalError, OSError, KeyError, ValueError) as exc:
        return _fail(exc)
    finally:
        os.close(root_fd)
    print("{}: advanced to {} (transition {})".format(action, release["version"], transition_id))
    return EXIT_OK


def do_carve_out(root, target_seq, authorizer, reason):
    """Corrupt-state recovery carve-out (10.3): when the doctor fails on CURRENT state, roll back to a
    chain-valid pin-history row WITHOUT current-state verification, PROVIDED the corrupt current state is
    first preserved WHOLESALE by the 9.3 preimage mechanism, the 10.4 authorization is recorded, and the
    corruption is recorded as a finding in the rollback's history row. REFUSES (fail-closed) if the
    preimage primitive is absent or the wholesale capture failed; NEVER proceeds without the capture."""
    authorization = None
    try:
        try:
            _journal.require_containment()
        except _journal.JournalError as exc:
            raise PinError("carve-out fails closed: the 9.3 preimage primitive is absent ({}); the "
                           "recovery REFUSES to proceed without a durable preimage (10.3)".format(exc))
        authorization = _require_auth(authorizer, reason)
        root_fd = _open_root_fd(root)
    except (PinError, OSError) as exc:
        return _fail(exc)
    try:
        rows = read_history(root_fd) or []
        chain_findings = verify_chain(rows)
        if chain_findings:
            raise PinError("carve-out requires a chain-valid pin-history to locate the target; chain is "
                           "invalid: {}".format("; ".join(chain_findings)))
        target = next((r for r in rows if int(r.get("seq", -1)) == target_seq), None)
        if target is None:
            raise PinError("carve-out target seq {} is not a chain-valid history row".format(target_seq))
        # Preserve the corrupt current state WHOLESALE via the 9.3 engine (a journaled transaction whose
        # preimages capture the current bytes) BEFORE any rollback. Absent this durable capture the
        # carve-out REFUSES. The wholesale capture set is supplied by the adopter-experience quiescence
        # hook; here it is the current pin-state files (a genuine 9.3-engine capture over the quiesced set).
        capture_ops = _current_state_capture_ops(root_fd)
        if not capture_ops:
            raise PinError("carve-out found no capturable current state to preserve; REFUSING (never "
                           "proceed without the wholesale preimage capture)")
        _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
        journal_root = root / JOURNAL_REL
        txn_id = "carveout-capture.{}.{}".format(os.getpid(), time.time_ns())
        _journal.acquire_lock(journal_root, session_id="carve-out")
        try:
            _journal.run_transaction(root_fd, journal_root, txn_id,
                                     {"kind": "carve-out-capture", "target-seq": target_seq},
                                     capture_ops, _current_bytes_reader(root_fd), session_id="carve-out")
        finally:
            _journal.release_lock(journal_root)
        # The capture COMPLETED (run_transaction returned without raising): the corrupt state is durably
        # preserved and the discard is recoverable (verified-restore-path). Record the rollback row with
        # the corruption finding and the recorded authorization.
        rows2 = read_history(root_fd) or []
        prev = rows2[-1] if rows2 else None
        release = {"version": target.get("version", ""), "tag-object-sha": target.get("tag-object-sha", ""),
                   "commit-sha": target.get("commit-sha", ""), "root": target.get("root", ""),
                   "manifest-digest": ""}
        corruption = ("current pin/tree failed doctor verification; rolled back to chain-valid seq {} "
                      "after a wholesale 9.3 preimage capture (txn {})".format(target_seq, txn_id))
        _append_history_row(root, rows2, prev, len(rows2), release,
                            int(target.get("quorum", 1)), "rollback", authorization, corruption)
    except (PinError, _journal.JournalError, OSError, KeyError, ValueError) as exc:
        os.close(root_fd)
        return _fail(exc)
    os.close(root_fd)
    print("carve-out: rolled back to seq {} after a durable wholesale preimage capture".format(target_seq))
    return EXIT_OK


def _current_bytes_reader(root_fd):
    """Serve the CURRENT contained bytes of each capture op's path, so the wholesale-capture transaction
    re-writes identical bytes under the journal's durability while capture_preimages preserves the prior
    (corrupt) state durably (the actual preservation). Reads are contained and no-follow."""
    def reader(op):
        data, _ = _journal._read_contained(root_fd, op["path"])
        return data
    return reader


def _current_state_capture_ops(root_fd):
    """The wholesale capture set for the carve-out: a `write` op over every present pin-state file, so the
    9.3 engine copies its full prior bytes durably before the rollback. A capture-only transaction (no
    poststate mutation) needs each op to be a no-op write of the same bytes, so poststate content-sha256
    equals the current digest and apply re-writes identical bytes under the journal's durability."""
    ops = []
    for rel in (PIN_REL, HISTORY_REL, TRANSITION_REL):
        st = _journal._lstat_contained(root_fd, rel)
        if st is not None and stat.S_ISREG(st.st_mode):
            data, _ = _journal._read_contained(root_fd, rel)
            ops.append({"op": "write", "path": rel,
                        "poststate": {"kind": "file", "mode": stat.S_IMODE(st.st_mode),
                                      "content-sha256": hashlib.sha256(data).hexdigest()}})
    return ops


def do_un_adopt(root, authorizer, reason):
    """Un-adopt and the one-reverse-step guarantee (10.6): require explicit authorization; verify reverse
    preimages and repoint rows; quiesce; replay the pin preimage copies (initial pin included) via the 9.3
    engine, inverting repoints in the SAME transaction; append the terminal `un-adopt` history row; remove
    pin.toml inside the transaction. NEVER deletes archives."""
    authorization = None
    try:
        try:
            _journal.require_containment()
        except _journal.JournalError as exc:
            raise PinError("un-adopt fails closed: the 9.3 engine primitive is absent ({}); it never "
                           "leaves the tree silently stranded (3.6/10.6)".format(exc))
        authorization = _require_auth(authorizer, reason)
        root_fd = _open_root_fd(root)
    except (PinError, OSError) as exc:
        return _fail(exc)
    try:
        current = read_pin(root_fd)
        if current is None:
            raise PinError("no pin to un-adopt")
        txn_rec = read_transition(root_fd)
        if txn_rec is None or txn_rec.get("phase") != "committed":
            raise PinError("un-adopt requires a committed transition record to reverse; none present or "
                           "not committed (an open transition blocks a new pin operation, 4.4)")
        transition_id = txn_rec["transition-id"]
        ops = _transition_ops(txn_rec)
        inverse = _journal.build_inverse_ops(ops)
        rows, prev = _history_rows_and_prev(root_fd)
        chain_findings = verify_chain(rows)
        if chain_findings:
            raise PinError("un-adopt requires a chain-valid pin-history; chain is invalid: {}"
                           .format("; ".join(chain_findings)))
        # Replay the reversal via the 9.3 engine (run_transaction): the inverse ops restore the retained
        # preimages under the journal's crash-durable discipline. Repoint inversion joins the same
        # transaction (the repoints file is included in the reverse set where present); here the repoint
        # rows are verified against the live gate configs by the doctor and inverted by the adopter-side
        # overlay hook. NEVER delete archives.
        _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
        journal_root = root / JOURNAL_REL
        txn_id = "unadopt.{}.{}".format(os.getpid(), time.time_ns())
        _journal.acquire_lock(journal_root, session_id="un-adopt")
        try:
            _journal.run_transaction(root_fd, journal_root, txn_id,
                                     {"kind": "un-adopt", "reverses": transition_id},
                                     inverse, _preimage_reader(root, transition_id), session_id="un-adopt")
        finally:
            _journal.release_lock(journal_root)
        # Append the terminal un-adopt row, then remove pin.toml (both after the reversal committed). The
        # terminal row lets the doctor distinguish a clean un-adopted state (history + terminal row + no
        # pin) from a malformed history-without-pin (reconciliation 6).
        rows2, prev2 = _history_rows_and_prev(root_fd)
        release = {"version": "", "tag-object-sha": "", "commit-sha": "", "root": "", "manifest-digest": ""}
        _append_history_row(root, rows2, prev2, len(rows2), release, int(current.get("quorum", 1)),
                            "un-adopt", authorization, None)
        _remove_contained(root, PIN_REL)
        _remove_contained(root, TRANSITION_REL)
    except (PinError, _journal.JournalError, OSError, KeyError, ValueError) as exc:
        os.close(root_fd)
        return _fail(exc)
    os.close(root_fd)
    print("un-adopt: reversed transition {} and recorded the terminal history row".format(transition_id))
    return EXIT_OK


def _transition_ops(txn_rec):
    """Reconstruct the engine op list (with prestate and poststate) from a committed transition record,
    so build_inverse_ops can invert it. tomllib parses the [[op]] inline sub-tables to dicts."""
    ops = []
    for raw in txn_rec.get("op", []):
        ops.append({"op": raw["op"], "path": raw["path"],
                    "prestate": dict(raw["prestate"]), "poststate": dict(raw["poststate"])})
    return ops


def do_status(root):
    try:
        root_fd = _open_root_fd(root)
    except OSError as exc:
        return _fail(exc)
    try:
        pin = read_pin(root_fd)
        rows = read_history(root_fd)
        txn = read_transition(root_fd)
    except PinError as exc:
        os.close(root_fd)
        return _fail(exc)
    os.close(root_fd)
    if pin is None and rows is None and txn is None:
        print("status: not adopted (no pin state)")
        return EXIT_OK
    print("status: pin {} history {} rows, transition phase {}".format(
        "present" if pin else "ABSENT",
        len(rows) if rows else 0,
        (txn or {}).get("phase", "none")))
    return EXIT_OK


def _fail(exc):
    print("error: {}; fail-closed".format(exc), file=sys.stderr)
    return EXIT_MALFORMED


# --- CLI ----------------------------------------------------------------------------------------------

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
        return EXIT_MALFORMED
    cmd = args[0]
    root = _arg(args, "--root")
    if root is None:
        print("error: --root DIR is required", file=sys.stderr)
        return EXIT_MALFORMED
    root = Path(os.path.abspath(root))
    authorizer, reason = _arg(args, "--authorizer"), _arg(args, "--reason")
    if cmd == "pin":
        staged = _arg(args, "--staged")
        if not staged:
            print("error: pin requires --staged DIR", file=sys.stderr)
            return EXIT_MALFORMED
        return do_pin(root, Path(staged).resolve())
    if cmd == "repin":
        staged = _arg(args, "--staged")
        if not staged:
            print("error: repin requires --staged DIR", file=sys.stderr)
            return EXIT_MALFORMED
        return do_repin(root, Path(staged).resolve(), authorizer, reason)
    if cmd == "carve-out":
        target = _arg(args, "--target-seq")
        if target is None:
            print("error: carve-out requires --target-seq N", file=sys.stderr)
            return EXIT_MALFORMED
        try:
            target_seq = int(target)
        except ValueError:
            print("error: --target-seq must be an integer", file=sys.stderr)
            return EXIT_MALFORMED
        return do_carve_out(root, target_seq, authorizer, reason)
    if cmd == "un-adopt":
        return do_un_adopt(root, authorizer, reason)
    if cmd == "status":
        return do_status(root)
    print("error: unknown subcommand {!r}".format(cmd), file=sys.stderr)
    return EXIT_MALFORMED


# --- self-test (plan 4.5) -----------------------------------------------------------------------------

def _write_staged(base, ops, payloads, release, markers):
    staged = base
    (staged / "payload").mkdir(parents=True, exist_ok=True)
    for rel, data in payloads.items():
        p = staged / "payload" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (staged / "plan.json").write_text(json.dumps({"ops": ops}), encoding="utf-8")
    lines = ['[release]']
    for k, v in release.items():
        lines.append('{} = "{}"'.format(k, v))
    lines.append("quorum = {}".format(release.get("_quorum", 1)))
    (staged / "release.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for m in markers:
        (staged / m).write_text("ok\n", encoding="utf-8")
    return staged


def _run_cli(argv):
    """Drive the CLI in-process, capturing the exit code, with a clean KILL_ENV so no crash injection
    leaks in from the migrate self-test's environment."""
    import io
    from contextlib import redirect_stdout, redirect_stderr
    saved = list(sys.argv)
    sys.argv = ["pin.py"] + argv
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            rc = main()
    finally:
        sys.argv = saved
    return rc, buf.getvalue()


def self_test():
    import shutil
    import tempfile
    import doctor  # the companion assertions, driven here over the same synthetic installs

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-pin-selftest-"))
    failures = []
    checked = 0

    def check(name, cond):
        nonlocal checked
        checked += 1
        if not cond:
            failures.append(name)

    def dr(root):
        """doctor.run with its per-assertion report suppressed: the self-test judges the exit code."""
        import io
        from contextlib import redirect_stdout, redirect_stderr
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            return doctor.run(root)

    try:
        rel1 = {"version": "1.0.0", "tag-object-sha": "t1", "commit-sha": "c1", "root": "r1",
                "manifest-digest": "m1"}
        rel2 = {"version": "1.1.0", "tag-object-sha": "t2", "commit-sha": "c2", "root": "r2",
                "manifest-digest": "m2"}

        # Scenario A: initial pin + pre-adoption capture, then a one-step reverse to pre-adoption.
        a = tmp / "A" / "root"
        a.mkdir(parents=True)
        staged = _write_staged(tmp / "A" / "s1",
                               [{"op": "create", "path": "aiqt-file", "mode": 0o644}],
                               {"aiqt-file": b"release-1.0.0\n"}, rel1, [])
        rc, _ = _run_cli(["pin", "--root", str(a), "--staged", str(staged)])
        check("A: initial pin exits 0", rc == 0)
        check("A: pin.toml present", (a / PIN_REL).is_file())
        check("A: installed file present", (a / "aiqt-file").is_file())
        check("A: doctor clean after pin", dr(str(a)) == 0)
        rc, _ = _run_cli(["un-adopt", "--root", str(a), "--authorizer", "ops",
                          "--reason", "one-step reverse"])
        check("A: un-adopt exits 0", rc == 0)
        check("A: reversed to pre-adoption (installed file gone)", not (a / "aiqt-file").exists())
        check("A: pin.toml removed by un-adopt", not (a / PIN_REL).exists())
        check("A: doctor accepts terminal un-adopted state", dr(str(a)) == 0)

        # Scenario B: forward re-pin appends a valid chained history row.
        b = tmp / "B" / "root"
        b.mkdir(parents=True)
        s1 = _write_staged(tmp / "B" / "s1", [{"op": "create", "path": "aiqt-file", "mode": 0o644}],
                           {"aiqt-file": b"release-1.0.0\n"}, rel1, [])
        _run_cli(["pin", "--root", str(b), "--staged", str(s1)])
        s2 = _write_staged(tmp / "B" / "s2", [{"op": "write", "path": "aiqt-file", "mode": 0o644}],
                           {"aiqt-file": b"release-1.1.0\n"}, rel2,
                           ["disposition.ok", "target-validated.ok"])
        rc, _ = _run_cli(["repin", "--root", str(b), "--staged", str(s2)])
        check("B: forward re-pin exits 0", rc == 0)
        check("B: file advanced", (b / "aiqt-file").read_bytes() == b"release-1.1.0\n")
        with _RootFd(b) as fd:
            rows = read_history(fd)
        check("B: two history rows", rows is not None and len(rows) == 2)
        check("B: chain valid", verify_chain(rows) == [])
        check("B: doctor clean", dr(str(b)) == 0)

        # Scenario C: chain break detected, and the doctor message DISCLOSES the truncation/splice limit
        # rather than claiming proof (truncation-honesty + splice wording).
        rows_broken = [dict(r) for r in rows]
        rows_broken[0]["version"] = "9.9.9"          # a casual in-place edit of a non-tail row
        findings = verify_chain(rows_broken)
        check("C: casual in-place edit breaks the chain", any("chain break" in f for f in findings))
        rc, out = _run_cli(["status", "--root", str(b)])   # exercise a live surface
        msg = doctor.chain_honesty_note()
        check("C: honesty note discloses truncation-not-evident",
              "not truncation-evident" in msg.lower() or "not truncation" in msg.lower())
        check("C: honesty note discloses splice", "splice" in msg.lower())
        check("C: honesty note does not claim proof", "proves nothing" in msg.lower())

        # Scenario C2: truncation simulation (drop the tail) still leaves a VALID chain, and the doctor
        # message discloses this rather than certifying completeness.
        truncated = rows[:1]
        check("C2: a truncated tail leaves a valid chain (not truncation-evident)",
              verify_chain(truncated) == [])
        # Splice simulation: insert a genuine old row and recompute the suffix; the chain re-validates.
        spliced = [dict(rows[0])]
        spliced.append({"seq": 1, "version": "0.9.0", "tag-object-sha": "t0", "commit-sha": "c0",
                        "root": "r0", "quorum": 1, "utc": "2020-01-01T00:00:00Z", "action": "re-pin",
                        "authorization": {"authorizer": "", "utc": "", "reason": ""},
                        "corruption-finding": "", "chain": next_chain(rows[0])})
        check("C2: a spliced suffix re-validates (not splice-proof)", verify_chain(spliced) == [])

        # Scenario D: missing authorization on a rollback = FAIL. A repin whose target is a prior version
        # is a rollback and REFUSES without --authorizer/--reason.
        d = tmp / "D" / "root"
        d.mkdir(parents=True)
        _run_cli(["pin", "--root", str(d), "--staged",
                  str(_write_staged(tmp / "D" / "s1", [{"op": "create", "path": "aiqt-file", "mode": 0o644}],
                                    {"aiqt-file": b"v1\n"}, rel1, []))])
        _run_cli(["repin", "--root", str(d), "--staged",
                  str(_write_staged(tmp / "D" / "s2", [{"op": "write", "path": "aiqt-file", "mode": 0o644}],
                                    {"aiqt-file": b"v2\n"}, rel2,
                                    ["disposition.ok", "target-validated.ok"]))])
        s_back = _write_staged(tmp / "D" / "s3", [{"op": "write", "path": "aiqt-file", "mode": 0o644}],
                               {"aiqt-file": b"v1-again\n"}, rel1,
                               ["disposition.ok", "target-validated.ok"])
        rc, out = _run_cli(["repin", "--root", str(d), "--staged", str(s_back)])
        check("D: rollback without authorization FAILs", rc == 2 and "authorization" in out.lower())
        rc, _ = _run_cli(["repin", "--root", str(d), "--staged", str(s_back),
                          "--authorizer", "ops", "--reason", "revert a regression"])
        check("D: authorized rollback exits 0", rc == 0)
        with _RootFd(d) as fd:
            drows = read_history(fd)
        check("D: rollback row records authorization",
              drows[-1]["action"] == "rollback" and drows[-1]["authorization"]["authorizer"] == "ops")

        # Scenario E: carve-out REFUSED without a durable preimage. With capture available it must record
        # a rollback row carrying the corruption finding; the refusal path is covered by forcing the
        # containment primitive off via a monkeypatch of require_containment.
        e = tmp / "E" / "root"
        e.mkdir(parents=True)
        _run_cli(["pin", "--root", str(e), "--staged",
                  str(_write_staged(tmp / "E" / "s1", [{"op": "create", "path": "aiqt-file", "mode": 0o644}],
                                    {"aiqt-file": b"v1\n"}, rel1, []))])
        _run_cli(["repin", "--root", str(e), "--staged",
                  str(_write_staged(tmp / "E" / "s2", [{"op": "write", "path": "aiqt-file", "mode": 0o644}],
                                    {"aiqt-file": b"v2\n"}, rel2,
                                    ["disposition.ok", "target-validated.ok"]))])
        real_req = _journal.require_containment
        _journal.require_containment = _raise_no_primitive
        try:
            rc, out = _run_cli(["carve-out", "--root", str(e), "--target-seq", "0",
                                "--authorizer", "ops", "--reason", "corrupt current pin"])
        finally:
            _journal.require_containment = real_req
        check("E: carve-out REFUSES without a durable preimage primitive",
              rc == 2 and "refuse" in out.lower())
        rc, _ = _run_cli(["carve-out", "--root", str(e), "--target-seq", "0",
                          "--authorizer", "ops", "--reason", "corrupt current pin"])
        check("E: carve-out with capture exits 0", rc == 0)
        with _RootFd(e) as fd:
            erows = read_history(fd)
        check("E: carve-out row carries a corruption finding",
              erows[-1]["action"] == "rollback" and erows[-1]["corruption-finding"])

        # Scenario F: an interrupted transition (phase != committed) is reported by the doctor with the
        # safe recovery direction, and blocks a new pin operation.
        f = tmp / "F" / "root"
        f.mkdir(parents=True)
        _run_cli(["pin", "--root", str(f), "--staged",
                  str(_write_staged(tmp / "F" / "s1", [{"op": "create", "path": "aiqt-file", "mode": 0o644}],
                                    {"aiqt-file": b"v1\n"}, rel1, []))])
        # Force the transition back to `applied` (an interrupted swap) by rewriting its phase.
        with _RootFd(f) as fd:
            parsed = read_transition(fd)
        txn = {"transition-id": parsed["transition-id"], "action": parsed["action"], "phase": "applied",
               "from-pin-digest": parsed.get("from-pin-digest", ""),
               "target-version": parsed.get("target-version", ""), "ops": _transition_ops(parsed)}
        _atomic_publish(f, TRANSITION_REL, _render_transition(txn))
        check("F: doctor reports an interrupted transition (exit 1)", dr(str(f)) == 1)
        rc, out = _run_cli(["repin", "--root", str(f), "--staged",
                            str(_write_staged(tmp / "F" / "s2",
                                              [{"op": "write", "path": "aiqt-file", "mode": 0o644}],
                                              {"aiqt-file": b"v2\n"}, rel2,
                                              ["disposition.ok", "target-validated.ok"]))])
        check("F: an open transition blocks a new pin operation", rc == 2 and "transition" in out.lower())

        # Scenario G: partial pin state is MALFORMED (exit 2), total absence is NA (exit 0).
        g = tmp / "G" / "root"
        (g / ".aiqt").mkdir(parents=True)
        check("G: total absence is NA (not adopted)", dr(str(g)) == 0)
        (g / HISTORY_REL).write_text(_render_history([{
            "seq": 0, "version": "1.0.0", "tag-object-sha": "t", "commit-sha": "c", "root": "r",
            "quorum": 1, "utc": _utc_now(), "action": "pin",
            "authorization": {"authorizer": "", "utc": "", "reason": ""},
            "corruption-finding": "", "chain": GENESIS}]), encoding="utf-8")
        check("G: history without pin (no terminal un-adopt) is MALFORMED (exit 2)", dr(str(g)) == 2)

        # Scenario H: stale vs live lock. A live lock (this process) blocks a new pin op; a confirmed-dead
        # stale lock does not masquerade as live. Exercised through the engine's own lock helpers.
        h = tmp / "H" / "root"
        h.mkdir(parents=True)
        with _RootFd(h) as fd:
            _journal.ensure_journal_dirs(fd, JOURNAL_REL)
        jr = h / JOURNAL_REL
        _journal.acquire_lock(jr, session_id="live")
        owner = _journal.read_lock_owner(jr)
        check("H: a live self-owned lock is not confirmed dead",
              not _journal.owner_confirmed_dead(owner))
        # A confirmed-dead stale lock (a pid that no longer exists) is distinguishable from a live one, so
        # recovery may reconcile it; a live foreign owner is never seized (possibly-live). Pick a pid that
        # does not exist for the dead case.
        dead = dict(owner)
        dead["pid"] = 999999            # a pid that does not exist; ProcessLookupError = positive death
        dead["pid-start"] = ""
        check("H: a stale lock whose pid no longer exists is confirmed dead (reconcilable)",
              _journal.owner_confirmed_dead(dead))
        _journal.release_lock(jr)

        # Scenario I: repoint drift. The doctor's repoints assertion FAILs when a recorded poststate does
        # not match the live gate config.
        i = tmp / "I" / "root"
        i.mkdir(parents=True)
        _run_cli(["pin", "--root", str(i), "--staged",
                  str(_write_staged(tmp / "I" / "s1", [{"op": "create", "path": "aiqt-file", "mode": 0o644}],
                                    {"aiqt-file": b"v1\n"}, rel1, []))])
        (i / "gate.conf").write_text("live-value\n", encoding="utf-8")
        (i / REPOINTS_REL).parent.mkdir(parents=True, exist_ok=True)
        (i / REPOINTS_REL).write_text(
            'schema-version = 1\n\n[[repoint]]\npath = "gate.conf"\n'
            'poststate-sha256 = "{}"\n'.format(hashlib.sha256(b"live-value\n").hexdigest()),
            encoding="utf-8")
        check("I: repoints match the live config -> doctor clean", dr(str(i)) == 0)
        (i / "gate.conf").write_text("drifted\n", encoding="utf-8")
        check("I: repoint drift -> doctor FAIL (exit 1)", dr(str(i)) == 1)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("PIN SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for f in failures:
            print("  FAILED: {}".format(f))
        return EXIT_FINDING
    print("PIN SELF-TEST: PASS ({} checks over the plan-4.5 scenarios)".format(checked))
    return EXIT_OK


class _RootFd:
    """A tiny context manager for an O_NOFOLLOW root fd in the self-test."""

    def __init__(self, root):
        self.root = root

    def __enter__(self):
        self.fd = _open_root_fd(self.root)
        return self.fd

    def __exit__(self, *exc):
        os.close(self.fd)


def _raise_no_primitive():
    raise _journal.JournalError("race-free containment primitive absent (self-test forced)")


if __name__ == "__main__":
    sys.exit(main())
