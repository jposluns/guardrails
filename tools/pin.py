#!/usr/bin/env python3
"""Pin lifecycle (VER-CORE 10.1 to 10.6, Section 12 step 7): pin, re-pin, rollback, un-adopt. Stdlib only.

  pin.py pin       --root DIR --staged DIR                   initial onboarding pin (pre-adoption capture)
  pin.py recover   --root DIR                                reconcile an interrupted pin-transition (10.3)
  pin.py un-adopt  --root DIR [auth flags]                   reverse the onboarding pin to pre-adoption (10.6)
  pin.py status    --root DIR                                report pin / transition / history state
  pin.py repin     --root DIR ...                            DEFERRED at 1.0.0 (anchored validation, 10.4/10.5)
  pin.py carve-out --root DIR ...                            DEFERRED at 1.0.0 (adopter-experience quiescence)
  pin.py --self-test                                         adversarial synthetic-tree flow invariants

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

_ROW_SCHEMA = (("seq", int), ("version", str), ("tag-object-sha", str), ("commit-sha", str),
               ("root", str), ("quorum", int), ("utc", str), ("action", str),
               ("corruption-finding", str), ("chain", str))
_AUTH_KEYS = ("authorizer", "utc", "reason")


def _validate_row_schema(row, index):
    """Strict schema for a pin-history row (reconciliation 9, hardened for B8): the row is a table carrying
    EXACTLY the fixed key set (plus the authorization sub-table), every field of its declared TYPE with NO
    coercion, action in the enum, and seq equal to the row index. Rejecting coercion is what closes the
    quorum=1 vs quorum="1" canonical-collision (an interior type edit is a schema FAIL, not a silent
    equivalence); rejecting unknown keys stops a smuggled field riding along uncanonicalized. Raises
    PinError (fail-closed) on any violation."""
    if not isinstance(row, dict):
        raise PinError("history row {}: not a table".format(index))
    allowed = {k for k, _ in _ROW_SCHEMA} | {"authorization"}
    unknown = set(row) - allowed
    if unknown:
        raise PinError("history row {}: unknown key(s) {}".format(index, sorted(unknown)))
    for key, typ in _ROW_SCHEMA:
        if key not in row:
            raise PinError("history row {}: missing required key {!r}".format(index, key))
        val = row[key]
        # bool is a subclass of int in Python; an int field must not accept True/False.
        if typ is int and (not isinstance(val, int) or isinstance(val, bool)):
            raise PinError("history row {}: {!r} must be an integer, not {}"
                           .format(index, key, type(val).__name__))
        if typ is str and not isinstance(val, str):
            raise PinError("history row {}: {!r} must be a string, not {}"
                           .format(index, key, type(val).__name__))
    if row["seq"] != index:
        raise PinError("history row {}: seq {} does not equal the row index {}"
                       .format(index, row["seq"], index))
    if row["action"] not in ACTIONS:
        raise PinError("history row {}: unknown action {!r}".format(index, row["action"]))
    auth = row.get("authorization")
    if not isinstance(auth, dict):
        raise PinError("history row {}: authorization must be a table".format(index))
    unknown_auth = set(auth) - set(_AUTH_KEYS)
    if unknown_auth:
        raise PinError("history row {}: authorization has unknown key(s) {}"
                       .format(index, sorted(unknown_auth)))
    for key in _AUTH_KEYS:
        if not isinstance(auth.get(key, ""), str):
            raise PinError("history row {}: authorization.{} must be a string".format(index, key))


def _canonical_projection(row):
    """The canonical projection of a SCHEMA-VALIDATED row for the chain hash: the fixed key set with every
    value at its validated native type (NO str()/int() coercion, so an int and a string that stringify
    alike stay distinct) and the authorization sub-table with its three fixed string fields, present with
    empty defaults rather than elided (reconciliation 9)."""
    auth = row.get("authorization") or {}
    out = {key: row[key] for key, _ in _ROW_SCHEMA}
    out["authorization"] = {"authorizer": auth.get("authorizer", ""), "utc": auth.get("utc", ""),
                            "reason": auth.get("reason", "")}
    return out


def canonical_row_bytes(row):
    """Canonical bytes for the 10.2 chain: the domain-prefixed canonical JSON (sorted keys, compact
    separators, ensure_ascii) of the row's schema-validated typed projection INCLUDING its own chain field,
    so links are transitive and reproducible regardless of TOML formatting, and an int vs string type edit
    changes the bytes (JSON encodes 1 and "1" differently). The row MUST already be schema-validated."""
    return CHAIN_DOMAIN + json.dumps(_canonical_projection(row), sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=True).encode()


def next_chain(prev_row):
    """The chain field a new row must carry: the SHA-256 of the previous row's canonical bytes, or the
    genesis sentinel for the first row."""
    if prev_row is None:
        return GENESIS
    return hashlib.sha256(canonical_row_bytes(prev_row)).hexdigest()


def verify_chain(rows):
    """End-to-end chain validation (10.2): each row is schema-validated (strict types, no coercion, known
    keys, seq==index) THEN its recorded chain field is checked against the recomputed hash of the previous
    row. A schema violation or a chain break is a FAIL. A rollback or un-adopt row without explicit recorded
    authorization is a FAIL (a history match never authorizes). Returns a list of findings (empty = valid).
    HONESTY: a valid chain is tamper-evident against a casual in-place edit ONLY; it is NOT truncation-
    evident and NOT splice-proof, so it proves nothing and authorizes nothing on its own."""
    prev, findings = GENESIS, []
    for i, row in enumerate(rows):
        try:
            _validate_row_schema(row, i)
        except PinError as exc:
            findings.append(str(exc))
            return findings                                # a schema-invalid row cannot be canonicalized
        if row["action"] in ("rollback", "un-adopt"):
            auth = row.get("authorization") or {}
            if not (auth.get("authorizer") and auth.get("utc") and auth.get("reason")):
                findings.append("row {}: {} without explicit recorded authorization (a history match "
                                "never authorizes, 10.4)".format(i, row["action"]))
        if row["chain"] != prev:
            findings.append("row {}: chain break (recomputed {} != recorded {})".format(
                i, prev, row["chain"]))
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


def _ensure_parent_dirs(root_fd, relpath):
    """Create the parent directory chain of relpath CONTAINED (no-follow, dir-fd-relative, idempotent), so
    a symlinked ancestor cannot redirect a subsequent publish off-tree (B9). A no-op for a single-component
    relpath (its parent is root)."""
    parts = relpath.split("/")
    if len(parts) > 1:
        _journal.ensure_journal_dirs(root_fd, "/".join(parts[:-1]))


def _atomic_publish(root, relpath, text):
    """Write text to relpath atomically AND contained (B9): the parent is resolved through held no-follow
    dir fds, the sibling temp is created via openat O_CREAT|O_EXCL|O_NOFOLLOW, fsync'd, renameat'd into
    place (the atomic publication point, 2.4 discipline), and the parent fd fsync'd; a symlinked parent
    component or target is refused rather than followed. A crash leaves either the old file or the new,
    never a torn one."""
    data = text.encode("utf-8")
    root_fd = _open_root_fd(root)
    try:
        _ensure_parent_dirs(root_fd, relpath)
        pfd, name = _journal._open_parent(root_fd, relpath)
    finally:
        os.close(root_fd)
    tmpname = name + ".tmp.{}".format(os.getpid())
    try:
        fd = os.open(tmpname, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644, dir_fd=pfd)
        try:
            _journal._write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmpname, name, src_dir_fd=pfd, dst_dir_fd=pfd)
        os.fsync(pfd)
    finally:
        os.close(pfd)


def _remove_contained(root, relpath):
    """Remove a contained file (no-follow, dir-fd-relative) and fsync its parent fd (B9). A no-op when the
    file or a parent component is already absent; a symlinked parent component is refused, not followed."""
    root_fd = _open_root_fd(root)
    try:
        pfd, name = _journal._open_parent(root_fd, relpath)
    except (OSError, _journal.JournalError):
        os.close(root_fd)
        return
    os.close(root_fd)
    try:
        try:
            os.unlink(name, dir_fd=pfd)
        except FileNotFoundError:
            return
        os.fsync(pfd)
    finally:
        os.close(pfd)


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
    """The onboarding/re-pin PREIMAGE COPY (10.3), standalone: durable prior bytes and metadata (prior-
    absence for creates, existence + mode for directories) under .aiqt/pin-preimages/<transition-id>/,
    fsync'd, BEFORE any swap. The .aiqt/pin-preimages hierarchy and the per-transition directory are created
    CONTAINED (no-follow, dir-fd-relative), so a symlinked .aiqt/pin-preimages cannot redirect the preimage
    store off-tree (B9); the engine's capture_preimages then writes into that verified-real directory. Uses
    NO part of the 9.3 journal transaction (no INTENT/COMPLETE framing, no lock). Mutates each op in place,
    adding its prestate. Refuses (fail-closed) if the per-transition directory already exists."""
    _journal.ensure_journal_dirs(root_fd, PREIMAGES_REL)
    pfd, name = _journal._open_parent(root_fd, "{}/{}".format(PREIMAGES_REL, transition_id))
    try:
        try:
            os.mkdir(name, 0o755, dir_fd=pfd)
        except FileExistsError:
            raise PinError("preimage transition dir {!r} already exists; refusing to reuse".format(name))
        os.fsync(pfd)
    finally:
        os.close(pfd)
    _journal.capture_preimages(root / PREIMAGES_REL / transition_id, root_fd, ops)


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


def _blocking_open_journal(root, root_fd):
    """True if the migration cutover journal holds any OPEN (non-terminal) transaction, which blocks a new
    pin operation until recovered (the 9.3 gate line / 4.4). Enumerated through a no-follow dir fd,
    rejecting a symlinked journal root or a symlinked entry (containment, B9); an unreadable or corrupt
    journal fails closed (blocks). A journal holding only terminal transactions does not block."""
    st = _journal._lstat_contained(root_fd, JOURNAL_REL)
    if st is None:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return True                                       # a non-directory journal path is malformed: block
    journal_root = root / JOURNAL_REL
    try:
        pfd, name = _journal._open_parent(root_fd, JOURNAL_REL)
    except (OSError, _journal.JournalError):
        return True
    try:
        jfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
    except OSError:
        os.close(pfd)
        return True
    try:
        for entry in os.listdir(jfd):
            est = os.stat(entry, dir_fd=jfd, follow_symlinks=False)
            if stat.S_ISDIR(est.st_mode) and not _journal.is_terminal(journal_root / entry):
                return True
        return False
    except (OSError, _journal.JournalError):
        return True                                       # fail closed on any read/parse error
    finally:
        os.close(jfd)
        os.close(pfd)


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
            raise PinError("an open pin-transition blocks a new pin operation (4.4); run `recover` first")
        if _blocking_open_journal(root, root_fd):
            raise PinError("an open migration cutover journal blocks a new pin operation (9.3/4.4); "
                           "recover it first")
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


def do_repin(root):
    """Forward re-pin and authorized rollback (10.3 to 10.5). DEFERRED at this release. These flows REQUIRE
    wholesale anchored validation of the target (10.4/10.5: check_manifest --anchored recomputing the ROOT
    at the recorded quorum, plus check_release_delta --repin), and that anchored validation is not enacted
    at 1.0.0 (the 5.7 anchored-recording branch is a deferred no-op and check_release_delta --repin is a
    fail-closed stub). Per guard-input-soundness and fail-closed, re-pin CANNOT validate a target here, so it
    REFUSES rather than advance the pin on a caller-creatable marker: authenticity is the anchored proof,
    never a marker or a history match (10.4). The flow lands when the anchored validators do (the adopter-
    experience release). Use `pin` for the initial onboarding pin, `un-adopt` to reverse it, or `recover` to
    reconcile an interrupted transition."""
    return _fail(PinError(
        "re-pin is deferred at this release: its required wholesale anchored target validation "
        "(check_manifest --anchored + check_release_delta --repin, 10.4/10.5) is not enacted at 1.0.0, so "
        "re-pin cannot validate a target and refuses rather than trust an unvalidated marker"))


def do_carve_out(root, target_seq):
    """Corrupt-state recovery carve-out (10.3). DEFERRED at this release. The carve-out must FIRST preserve
    the corrupt current state WHOLESALE (the quiesced affected tree) before any rollback (10.3, and the
    verified-restore-path rule), and that wholesale affected-tree set is supplied by the adopter-experience
    quiescence hook, which is not enacted at 1.0.0. A capture that is not wholesale is not a verified restore
    path, so the carve-out REFUSES rather than preserve a partial, non-restorable state or fabricate a
    corruption finding it never observed. It lands with the adopter-experience quiescence layer."""
    return _fail(PinError(
        "carve-out is deferred at this release: it must preserve the corrupt current state wholesale (the "
        "quiesced affected tree) before any rollback (10.3), and that wholesale set is supplied by the "
        "adopter-experience quiescence hook, not enacted at 1.0.0; refusing rather than capture a partial, "
        "non-restorable state"))


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
        if read_repoints(root_fd) is not None:
            raise PinError("un-adopt with recorded coupled gate re-points is deferred at this release: "
                           "inverting live gate re-points (10.6) is the adopter-experience overlay's "
                           "responsibility, not enacted at 1.0.0; refusing rather than leave re-points "
                           "un-inverted")
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


def _op_at_prestate(root_fd, op):
    """True if the op's target currently matches its recorded PRESTATE (the op was NOT applied): a create/
    mkdir prestate is absence; a write/remove file prestate is the prior mode + digest; a dir prestate is
    the prior mode. Fail-closed: an unreadable target is treated as not-at-prestate."""
    pre = op.get("prestate") or {}
    kind = pre.get("kind")
    st = _journal._lstat_contained(root_fd, op["path"])
    if kind == "absent":
        return st is None
    if kind == "dir":
        return st is not None and stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == pre.get("mode")
    if kind == "file":
        if st is None or not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != pre.get("mode"):
            return False
        try:
            data, _ = _journal._read_contained(root_fd, op["path"])
        except _journal.JournalError:
            return False
        return hashlib.sha256(data).hexdigest() == pre.get("sha256")
    return False


def _rmtree_contained(pfd, name):
    """Remove 'name' beneath parent fd pfd, depth-first, dir-fd-relative and no-follow: a symlink or a
    non-directory at any level is unlinked, never descended; a directory is emptied through its own
    O_NOFOLLOW fd and then rmdir'd. Contained cleanup of our own pin-preimages namespace (B9)."""
    try:
        st = os.stat(name, dir_fd=pfd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(st.st_mode):
        os.unlink(name, dir_fd=pfd)
        return
    dfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
    try:
        for child in os.listdir(dfd):
            _rmtree_contained(dfd, child)
    finally:
        os.close(dfd)
    os.rmdir(name, dir_fd=pfd)


def _sweep_orphan_preimages(root, root_fd):
    """Remove every preimage transition subdir under .aiqt/pin-preimages, contained and no-follow, fsyncing
    the parent. Preimages are captured BEFORE the transition record is published, so a subdir left with no
    live transition is a leftover from a crash before that publish (nothing was applied); removing it returns
    the state to clean. Called by recover after a reversal (all preimages spent) and when there is no
    transition record at all (a pre-intent crash). Returns the count removed."""
    st = _journal._lstat_contained(root_fd, PREIMAGES_REL)
    if st is None:
        return 0
    try:
        pfd, name = _journal._open_parent(root_fd, PREIMAGES_REL)
    except (OSError, _journal.JournalError):
        return 0
    try:
        _rmtree_contained(pfd, name)
        os.fsync(pfd)
    finally:
        os.close(pfd)
    return 1


def _reverse_pin_transition(root, root_fd, transition_id, ops):
    """Reverse the APPLIED subset of an interrupted pin-transition's ops from the retained preimages, via
    the crash-durable 9.3 engine, leaving the tree at the prior (pre-transition) state. Each op still at its
    prestate is left untouched; any op in neither its prestate nor its poststate is MALFORMED (fail-closed),
    so a partial swap recovers deterministically. Idempotent: re-running reverses only what remains applied.
    An initial onboarding pin installs create/mkdir ops, so their inverses are contained remove/rmdir; the
    preimage reader serves original bytes only for a write inverse, which the onboarding path never
    produces."""
    applied = [op for op in ops if _journal._poststate_verifies(root_fd, op)]
    for op in ops:
        if op not in applied and not _op_at_prestate(root_fd, op):
            raise PinError("recover: {!r} is in an unexpected state (neither the applied poststate nor the "
                           "original prestate); fail-closed".format(op.get("path")))
    if not applied:
        return
    inverse = _journal.build_inverse_ops(applied)
    _journal.ensure_journal_dirs(root_fd, JOURNAL_REL)
    journal_root = root / JOURNAL_REL
    txn_id = "recover.{}.{}".format(os.getpid(), time.time_ns())
    _journal.acquire_lock(journal_root, session_id="recover")
    try:
        _journal.run_transaction(root_fd, journal_root, txn_id,
                                 {"kind": "recover-reverse", "reverses": transition_id},
                                 inverse, _preimage_reader(root, transition_id), session_id="recover")
    finally:
        _journal.release_lock(journal_root)


def _pin_transition_complete(root_fd, txn, ops):
    """True if a pin.toml plus a chain-valid terminal history row already exist and agree with an APPLIED
    transition, so it is a fully-published pin missing only its committed marker; recovery then rolls
    FORWARD (mark committed) rather than tearing down a successful pin."""
    current = read_pin(root_fd)
    if current is None:
        return False
    rows = read_history(root_fd)
    if not rows or verify_chain(rows):
        return False
    last = rows[-1]
    if last.get("version") != (current.get("release") or {}).get("version"):
        return False
    if last.get("version") != txn.get("target-version"):
        return False
    return all(_journal._poststate_verifies(root_fd, op) for op in ops)


def _recover_open_journal(root, root_fd):
    """Reconcile every OPEN migration-cutover journal transaction (from a crashed un-adopt or a crashed
    recover reversal) to a terminal state via the 9.3 engine's journal-alone recovery (both directions,
    idempotent). Enumerated contained (no-follow), skipping a symlinked or non-directory entry. A no-op when
    no journal or no open transaction is present."""
    st = _journal._lstat_contained(root_fd, JOURNAL_REL)
    if st is None or not stat.S_ISDIR(st.st_mode):
        return
    journal_root = root / JOURNAL_REL
    try:
        pfd, name = _journal._open_parent(root_fd, JOURNAL_REL)
    except (OSError, _journal.JournalError):
        return
    try:
        jfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
    except OSError:
        os.close(pfd)
        return
    try:
        for entry in sorted(os.listdir(jfd)):
            est = os.stat(entry, dir_fd=jfd, follow_symlinks=False)
            if not stat.S_ISDIR(est.st_mode):
                continue
            if not _journal.is_terminal(journal_root / entry):
                _journal.recover(journal_root / entry, root_fd)
    finally:
        os.close(jfd)
        os.close(pfd)


def do_recover(root):
    """Reconcile an interrupted pin-transition (10.3 recovery). A committed or absent transition needs no
    recovery. An APPLIED transition whose pin + terminal history row already exist and agree is rolled
    FORWARD (mark committed), preserving a successful pin. Otherwise (prepared, or applied-but-incomplete)
    the transition is rolled BACK: the applied swap ops are reversed from the retained preimages, then the
    partial pin/history/transition/preimages are removed, returning the tree to the prior state so `pin` may
    be re-run. Only an initial-pin transition is recoverable here (re-pin/rollback are deferred at 1.0.0).
    Never touches archives. Idempotent: a re-run after a clean recover finds no transition and is a no-op."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return _fail(exc)
    try:
        root_fd = _open_root_fd(root)
    except OSError as exc:
        return _fail(exc)
    try:
        _recover_open_journal(root, root_fd)
        txn = read_transition(root_fd)
        if txn is None:
            removed = _sweep_orphan_preimages(root, root_fd)
            os.close(root_fd)
            if removed:
                print("recover: no transition record; swept {} orphan preimage set(s) left by a crash "
                      "before the transition was published".format(removed))
            else:
                print("recover: no transition record; nothing to recover")
            return EXIT_OK
        phase = txn.get("phase")
        if phase == "committed":
            os.close(root_fd)
            print("recover: transition {} is committed; nothing to recover"
                  .format(txn.get("transition-id")))
            return EXIT_OK
        if phase not in ("prepared", "applied"):
            raise PinError("transition carries an unknown phase {!r}; cannot recover".format(phase))
        if txn.get("action") != "pin":
            raise PinError("recover supports an interrupted initial-pin transition only; action {!r} is "
                           "deferred at this release".format(txn.get("action")))
        transition_id = txn["transition-id"]
        ops = _transition_ops(txn)
        if phase == "applied" and _pin_transition_complete(root_fd, txn, ops):
            committed = dict(txn)
            committed["phase"] = "committed"
            committed["ops"] = ops
            _atomic_publish(root, TRANSITION_REL, _render_transition(committed))
            os.close(root_fd)
            print("recover: transition {} was fully applied; rolled FORWARD to committed"
                  .format(transition_id))
            return EXIT_OK
        _reverse_pin_transition(root, root_fd, transition_id, ops)
        rows = read_history(root_fd)
        if rows is not None and len(rows) > 1:
            raise PinError("recover: refusing to reverse over a multi-row pin-history (unexpected at 1.0.0 "
                           "where re-pin is deferred); manual review needed")
        _remove_contained(root, PIN_REL)
        _remove_contained(root, HISTORY_REL)
        _remove_contained(root, TRANSITION_REL)
        _sweep_orphan_preimages(root, root_fd)
    except (PinError, _journal.JournalError, OSError, KeyError, ValueError) as exc:
        os.close(root_fd)
        return _fail(exc)
    os.close(root_fd)
    print("recover: transition {} reversed to the prior state; re-run `pin` to retry".format(transition_id))
    return EXIT_OK


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
        return do_repin(root)
    if cmd == "carve-out":
        target = _arg(args, "--target-seq")
        try:
            target_seq = int(target) if target is not None else -1
        except ValueError:
            print("error: --target-seq must be an integer", file=sys.stderr)
            return EXIT_MALFORMED
        return do_carve_out(root, target_seq)
    if cmd == "recover":
        return do_recover(root)
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
    """Adversarial synthetic-tree flow invariants (B10 root-cause fix: the r1 suite was too shallow and hid
    B1-B9). Each scenario asserts the FAIL/refuse path first, so the guard is proven to bite, then the clean
    path. Real subprocess crash-injection (the engine KILL hook) exercises the recovery command."""
    import io
    import shutil
    import subprocess
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr
    import doctor  # companion assertions, driven over the same synthetic installs

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
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            return doctor.run(root)

    def crash(argv, kill_label):
        env = dict(os.environ)
        env[_journal.KILL_ENV] = kill_label
        return subprocess.run([sys.executable, os.path.abspath(__file__)] + argv,
                              env=env, capture_output=True).returncode

    rel1 = {"version": "1.0.0", "tag-object-sha": "t1", "commit-sha": "c1", "root": "r1",
            "manifest-digest": "m1"}
    onop = [{"op": "create", "path": "aiqt-file", "mode": 0o644}]
    onpay = {"aiqt-file": b"release-1.0.0\n"}

    def row(seq, quorum, chain, action="pin", version="1.0.0", auth=None):
        return {"seq": seq, "version": version, "tag-object-sha": "t", "commit-sha": "c", "root": "r",
                "quorum": quorum, "utc": "2020-01-01T00:00:00Z", "action": action,
                "authorization": auth or {"authorizer": "", "utc": "", "reason": ""},
                "corruption-finding": "", "chain": chain}

    try:
        # ---- T1/T2/T14: initial pin real; doctor catches a byte-mutation and a mode-change ----
        a = tmp / "A" / "root"; a.mkdir(parents=True)
        s = _write_staged(tmp / "A" / "s", onop, onpay, rel1, [])
        rc, _ = _run_cli(["pin", "--root", str(a), "--staged", str(s)])
        check("T1a: initial pin exits 0", rc == 0)
        check("T1a: installed file present", (a / "aiqt-file").read_bytes() == b"release-1.0.0\n")
        check("T14: doctor clean after a real pin (recorded digest == installed)", dr(str(a)) == 0)
        (a / "aiqt-file").write_bytes(b"tampered\n")
        check("T1: doctor FAILs a direct byte mutation of an installed file", dr(str(a)) == 1)
        (a / "aiqt-file").write_bytes(b"release-1.0.0\n")
        check("T1: doctor clean again after restore", dr(str(a)) == 0)
        os.chmod(a / "aiqt-file", 0o600)
        check("T2: doctor FAILs a mode change of an installed file", dr(str(a)) == 1)
        os.chmod(a / "aiqt-file", 0o644)
        check("T2: doctor clean again after mode restore", dr(str(a)) == 0)

        # ---- T7: un-adopt one-reverse-step restores pre-adoption ----
        rc, _ = _run_cli(["un-adopt", "--root", str(a), "--authorizer", "ops", "--reason", "reverse"])
        check("T7: un-adopt exits 0", rc == 0)
        check("T7: installed file gone (prior-absence restored)", not (a / "aiqt-file").exists())
        check("T7: pin.toml removed by un-adopt", not (a / PIN_REL).exists())
        check("T7: doctor accepts the terminal un-adopted state", dr(str(a)) == 0)

        # ---- T5: forward re-pin is DEFERRED and REFUSES; a forged marker cannot bypass ----
        b = tmp / "B" / "root"; b.mkdir(parents=True)
        _run_cli(["pin", "--root", str(b), "--staged",
                  str(_write_staged(tmp / "B" / "s1", onop, onpay, rel1, []))])
        rel2 = {"version": "1.1.0", "tag-object-sha": "t2", "commit-sha": "c2", "root": "r2",
                "manifest-digest": "m2"}
        s2 = _write_staged(tmp / "B" / "s2", [{"op": "write", "path": "aiqt-file", "mode": 0o644}],
                           {"aiqt-file": b"release-1.1.0\n"}, rel2,
                           ["disposition.ok", "target-validated.ok"])   # forged markers
        rc, out = _run_cli(["repin", "--root", str(b), "--staged", str(s2)])
        check("T5: re-pin REFUSES (deferred) exit 2", rc == 2 and "deferred" in out.lower())
        check("T5: a forged marker does NOT advance the pin (file unchanged)",
              (b / "aiqt-file").read_bytes() == b"release-1.0.0\n")
        with _RootFd(b) as fd:
            check("T5: still exactly one history row (no re-pin appended)", len(read_history(fd)) == 1)

        # ---- T6: carve-out is DEFERRED and REFUSES; no rollback row fabricated ----
        rc, out = _run_cli(["carve-out", "--root", str(b), "--target-seq", "0"])
        check("T6: carve-out REFUSES (deferred) exit 2", rc == 2 and "deferred" in out.lower())
        with _RootFd(b) as fd:
            check("T6: no rollback row fabricated", len(read_history(fd)) == 1)

        # ---- T8: un-adopt with recorded repoints is DEFERRED and REFUSES; pin intact ----
        (b / REPOINTS_REL).parent.mkdir(parents=True, exist_ok=True)
        (b / REPOINTS_REL).write_text(
            'schema-version = 1\n\n[[repoint]]\npath = "gate.conf"\npoststate-sha256 = "{}"\n'.format(
                hashlib.sha256(b"x").hexdigest()), encoding="utf-8")
        rc, out = _run_cli(["un-adopt", "--root", str(b), "--authorizer", "ops", "--reason", "x"])
        check("T8: un-adopt with repoints REFUSES (deferred) exit 2", rc == 2 and "deferred" in out.lower())
        check("T8: pin intact after the refused un-adopt", (b / PIN_REL).is_file())
        (b / REPOINTS_REL).unlink()

        # ---- T3: crash during preimage capture (before the transition) -> recover sweeps + clean ----
        c = tmp / "C" / "root"; c.mkdir(parents=True)
        sc = _write_staged(tmp / "C" / "s", onop, onpay, rel1, [])
        rc = crash(["pin", "--root", str(c), "--staged", str(sc)], "after-preimages")
        check("T3: injected crash after preimages killed the child", rc == 137)
        check("T3: preimages present, no transition, no pin, no swap",
              (c / PREIMAGES_REL).exists() and not (c / TRANSITION_REL).exists()
              and not (c / PIN_REL).exists() and not (c / "aiqt-file").exists())
        check("T3: doctor flags the orphan preimage state (not NA, not clean)", dr(str(c)) != 0)
        rc, _ = _run_cli(["recover", "--root", str(c)])
        check("T3: recover exits 0", rc == 0)
        check("T3: recover swept the orphan preimages (back to pre-adoption)",
              not (c / PREIMAGES_REL).exists())
        check("T3: doctor clean (not adopted) after recover", dr(str(c)) == 0)
        rc, _ = _run_cli(["recover", "--root", str(c)])
        check("T3: recover is idempotent (no-op second run)", rc == 0)
        rc, _ = _run_cli(["pin", "--root", str(c), "--staged",
                          str(_write_staged(tmp / "C" / "s2", onop, onpay, rel1, []))])
        check("T3: pin succeeds after a clean recover", rc == 0 and dr(str(c)) == 0)

        # ---- T4: crash mid-swap (op 0 applied, op 1 not; transition 'prepared') -> recover rolls back ----
        d = tmp / "D" / "root"; d.mkdir(parents=True)
        twop = [{"op": "create", "path": "f1", "mode": 0o644},
                {"op": "create", "path": "f2", "mode": 0o644}]
        sd = _write_staged(tmp / "D" / "s", twop, {"f1": b"one\n", "f2": b"two\n"}, rel1, [])
        rc = crash(["pin", "--root", str(d), "--staged", str(sd)], "after-apply-0")
        check("T4: injected crash mid-swap killed the child", rc == 137)
        check("T4: a partial swap is present (f1 installed, f2 not)",
              (d / "f1").exists() and not (d / "f2").exists())
        check("T4: doctor flags the interrupted transition", dr(str(d)) != 0)
        rc, _ = _run_cli(["recover", "--root", str(d)])
        check("T4: recover exits 0 on a partial swap", rc == 0)
        check("T4: recover reversed the partial swap (f1 removed)", not (d / "f1").exists())
        check("T4: doctor clean after recover", dr(str(d)) == 0)

        # ---- roll-FORWARD: an 'applied' transition with a complete pin is finished, not torn down ----
        e = tmp / "E" / "root"; e.mkdir(parents=True)
        _run_cli(["pin", "--root", str(e), "--staged",
                  str(_write_staged(tmp / "E" / "s", onop, onpay, rel1, []))])
        with _RootFd(e) as fd:
            parsed = read_transition(fd)
        applied_txn = {"transition-id": parsed["transition-id"], "action": parsed["action"],
                       "phase": "applied", "from-pin-digest": parsed.get("from-pin-digest", ""),
                       "target-version": parsed.get("target-version", ""),
                       "ops": _transition_ops(parsed)}
        _atomic_publish(e, TRANSITION_REL, _render_transition(applied_txn))
        check("roll-forward: doctor FAILs an applied (non-committed) transition", dr(str(e)) == 1)
        rc, out = _run_cli(["recover", "--root", str(e)])
        check("roll-forward: recover rolls FORWARD (exit 0, 'FORWARD')", rc == 0 and "forward" in out.lower())
        check("roll-forward: pin + file preserved", (e / PIN_REL).is_file()
              and (e / "aiqt-file").read_bytes() == b"release-1.0.0\n")
        check("roll-forward: doctor clean after roll-forward", dr(str(e)) == 0)

        # ---- T9: chain type-coercion + strict schema (synthetic histories) ----
        r0 = row(0, 1, GENESIS)
        r1 = row(1, 1, next_chain(r0), version="1.1.0")
        check("T9: a valid 2-row chain verifies", verify_chain([r0, r1]) == [])
        check("T9: quorum as a string is a schema FAIL (no coercion collision)",
              any("integer" in f for f in verify_chain([row(0, "1", GENESIS)])))
        r0_edit = dict(r0); r0_edit["quorum"] = 2
        check("T9: an interior quorum edit breaks the chain",
              any("chain break" in f for f in verify_chain([r0_edit, r1])))
        r0_extra = dict(r0); r0_extra["smuggled"] = "x"
        check("T9: an unknown row key is a schema FAIL",
              any("unknown key" in f for f in verify_chain([r0_extra])))
        check("T9: seq != row index is a schema FAIL",
              any("seq" in f for f in verify_chain([row(5, 1, GENESIS)])))

        # ---- honesty note discloses the truncation/splice limit; residuals demonstrated ----
        note = doctor.chain_honesty_note().lower()
        check("honesty: not-truncation-evident disclosed", "not truncation-evident" in note)
        check("honesty: splice disclosed", "splice" in note)
        check("honesty: proves-nothing disclosed", "proves nothing" in note)
        check("honesty: a truncated tail leaves a valid chain", verify_chain([r0]) == [])
        check("honesty: a spliced suffix re-validates",
              verify_chain([dict(r0), row(1, 1, next_chain(r0), version="0.9.0", action="re-pin")]) == [])

        # ---- T13: partial state MALFORMED; total absence NA ----
        g = tmp / "G" / "root"; (g / ".aiqt").mkdir(parents=True)
        check("T13: total absence is NA (exit 0)", dr(str(g)) == 0)
        (g / HISTORY_REL).write_text(_render_history([row(0, 1, GENESIS)]), encoding="utf-8")
        check("T13: history without a pin (no terminal un-adopt) is MALFORMED (exit 2)", dr(str(g)) == 2)

        # ---- T10: a symlinked pin-preimages dir -> pin REFUSES (write-path containment) ----
        h = tmp / "H" / "root"; (h / ".aiqt").mkdir(parents=True)
        outside = tmp / "H-outside"; outside.mkdir()
        os.symlink(str(outside), str(h / PREIMAGES_REL))
        rc, out = _run_cli(["pin", "--root", str(h), "--staged",
                            str(_write_staged(tmp / "H" / "s", onop, onpay, rel1, []))])
        check("T10: pin REFUSES when pin-preimages is a symlink (containment) exit 2", rc == 2)
        check("T10: nothing written off-tree via the symlink", not any(outside.iterdir()))

        # ---- T11: a symlinked journal root is refused, not followed ----
        jj = tmp / "J" / "root"; (jj / ".aiqt" / "migration").mkdir(parents=True)
        outside2 = tmp / "J-outside"; outside2.mkdir()
        os.symlink(str(outside2), str(jj / JOURNAL_REL))
        with _RootFd(jj) as fd:
            check("T11: a symlinked journal root is treated as blocking, not followed",
                  _blocking_open_journal(jj, fd) is True)

        # ---- T12: an OPEN journal blocks a new pin and is not read as NA; recover reconciles it ----
        k = tmp / "K" / "root"; k.mkdir(parents=True)
        with _RootFd(k) as fd:
            _journal.ensure_journal_dirs(fd, JOURNAL_REL)
        txn_open = k / JOURNAL_REL / "txn-open"
        txn_open.mkdir()
        _journal.publish(txn_open, _journal.F_INTENT,
                         {"txn": "txn-open", "header": {}, "ops": []})   # valid INTENT, no COMPLETE = OPEN
        with _RootFd(k) as fd:
            check("T12: a present migration journal is NOT read as clean absence (B7)",
                  not doctor._adoption_absent(fd))
        check("T12: doctor FAILs on the open journal", dr(str(k)) != 0)
        rc, out = _run_cli(["pin", "--root", str(k), "--staged",
                            str(_write_staged(tmp / "K" / "s", onop, onpay, rel1, []))])
        check("T12: a new pin is BLOCKED by the open journal exit 2", rc == 2 and "journal" in out.lower())
        rc, _ = _run_cli(["recover", "--root", str(k)])
        check("T12: recover reconciles the open journal (exit 0)", rc == 0)
        check("T12: doctor clean after the journal is reconciled", dr(str(k)) == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("PIN SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for f in failures:
            print("  FAILED: {}".format(f))
        return EXIT_FINDING
    print("PIN SELF-TEST: PASS ({} adversarial checks over the Path-B flow invariants)".format(checked))
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
