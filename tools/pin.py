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
INTENT/COMPLETE framing, no lock); the onboarding un-adopt and the recover reversal are the SAME CONTAINED
reverse swap (10.3/10.6), with NO 9.3 journal and NO lock. The corrupt-state carve-out and forward re-pin are
DEFERRED at this release (they refuse fail-closed); a migration cutover journal is DETECTED (it blocks a pin)
but is reconciled by the deferred migration tool, never here.

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
import _journal  # noqa: E402  the 9.3 engine: contained fd-bound helpers (open/read/lstat/apply/is_terminal)

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
MIGRATION_REL = ".aiqt/migration"          # the adopter-created migration-state namespace (journal, repoints, crosswalk)
UNADOPT_REL = ".aiqt/pin-unadopt.toml"     # the un-adopt INTENT marker (class adopter-state): written before the reverse swap, removed last

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


def read_unadopt(root_fd):
    """The un-adopt INTENT record, or None when absent. Its presence means an un-adopt is in progress (or
    was interrupted) and `recover` must complete it before any new pin operation (10.6)."""
    return _read_contained_toml(root_fd, UNADOPT_REL)


def _render_unadopt(intent):
    """Render the un-adopt INTENT: the transition being reversed, the target version, quorum, UTC, and the
    authorization, so recover can complete an interrupted un-adopt under the same authorization it began."""
    lines = ["# .aiqt/pin-unadopt.toml (VER-CORE 10.6): the un-adopt INTENT, written BEFORE the reverse",
             "# swap so recover can COMPLETE an interrupted un-adopt idempotently; removed LAST. Generated;",
             "# do not hand-edit.", "",
             "schema-version = 1",
             "transition-id = {}".format(_toml_str(intent["transition-id"])),
             "target-version = {}".format(_toml_str(intent.get("target-version", ""))),
             "quorum = {}".format(int(intent.get("quorum", 1))),
             "utc = {}".format(_toml_str(intent.get("utc", ""))),
             "authorization = {}".format(_auth_inline(intent.get("authorization") or {})),
             ""]
    return "\n".join(lines) + "\n"


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


# _preimage_reader removed in round 3: the onboarding reverse is a CONTAINED reverse swap (10.3/10.6)
# with no retained-byte reads, so no absolute-path preimage reader exists (closes the B9 reader leg).


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
    """Read and VALIDATE the staged release record (guard-input-soundness on do_pin's DIRECT input, RB9):
    every identity field must be a non-empty string and quorum a positive int, with NO coercion. A
    malformed or empty release record is a fail-closed PinError (exit 2), never a pin published over empty
    identity."""
    data = tomllib.loads((staged / "release.toml").read_text(encoding="utf-8"))
    rel = data.get("release", {})
    if not isinstance(rel, dict):
        raise PinError("release.toml [release] must be a table")
    out = {}
    for key in ("version", "tag-object-sha", "commit-sha", "root", "manifest-digest"):
        val = rel.get(key)
        if not isinstance(val, str) or not val:
            raise PinError("release.toml [release].{} must be a non-empty string".format(key))
        out[key] = val
    quorum = data.get("quorum", 1)
    if not isinstance(quorum, int) or isinstance(quorum, bool) or quorum < 1:
        raise PinError("release.toml quorum must be a positive integer")
    return out, quorum


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
            if stat.S_ISLNK(est.st_mode):
                return True                               # a symlinked journal entry is never followed (block)
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
        if read_unadopt(root_fd) is not None:
            raise PinError("an un-adopt is in progress (pin-unadopt.toml present); run `recover` to complete "
                           "it before a new pin operation")
        _hist = read_history(root_fd)
        if _hist and _hist[-1].get("action") != "un-adopt":
            raise PinError("a dangling pin-history is present with no live pin (last action {!r}, not a "
                           "terminal un-adopt); the state is malformed - run `recover` or `doctor` before "
                           "onboarding".format(_hist[-1].get("action")))
        release, quorum = _read_release(staged)
        ops = _load_staged_ops(staged)
        for op in ops:
            if op.get("op") not in ("create", "mkdir"):
                raise PinError("onboarding pin installs new paths only; op {!r} on {!r} is not create/mkdir "
                               "(overwriting or removing an existing path is an adopter-experience overlay "
                               "concern, deferred at this release)".format(op.get("op"), op.get("path")))
            _p = op.get("path") or ""
            if _p == ".aiqt" or _p.startswith(".aiqt/"):
                raise PinError("onboarding pin refuses to install into the .aiqt/ adopter-state namespace "
                               "(op path {!r}); the pin manages that namespace itself".format(_p))
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
    """Un-adopt and the one-reverse-step guarantee (10.6): reverse the ONBOARDING pin to pre-adoption via the
    CONTAINED reverse swap (NO 9.3 journal and NO lock; the journal engine is the deferred migration path's,
    not onboarding's). Crash-safe via a durable un-adopt INTENT written BEFORE the reversal, so `recover`
    completes an interrupted un-adopt idempotently. Requires explicit authorization. Refuses if coupled gate
    re-points are recorded (overlay inversion deferred) or an un-adopt is already in progress. NEVER deletes
    archives."""
    authorization = None
    try:
        try:
            _journal.require_containment()
        except _journal.JournalError as exc:
            raise PinError("un-adopt fails closed: the race-free containment primitive is absent ({}); it "
                           "never leaves the tree silently stranded (3.6/10.6)".format(exc))
        authorization = _require_auth(authorizer, reason)
        root_fd = _open_root_fd(root)
    except (PinError, OSError) as exc:
        return _fail(exc)
    try:
        if _blocking_open_journal(root, root_fd):
            raise PinError("an open migration cutover journal blocks un-adopt (9.3/4.4); resolve it with the "
                           "migration tool first (un-adopt must not race a live cutover)")
        if read_unadopt(root_fd) is not None:
            raise PinError("an un-adopt is already in progress (pin-unadopt.toml present); run `recover` to "
                           "complete it before starting another")
        current = read_pin(root_fd)
        if current is None:
            raise PinError("no pin to un-adopt")
        if read_repoints(root_fd):                # a NON-EMPTY repoint list; an empty/absent file is fine
            raise PinError("un-adopt with recorded coupled gate re-points is deferred at this release: "
                           "inverting live gate re-points (10.6) is the adopter-experience overlay's "
                           "responsibility, not enacted at 1.0.0; refusing rather than leave re-points "
                           "un-inverted")
        txn_rec = read_transition(root_fd)
        if txn_rec is None or txn_rec.get("phase") != "committed":
            raise PinError("un-adopt requires a committed transition record to reverse; none present or "
                           "not committed (an open transition blocks a new pin operation, 4.4)")
        chain_findings = verify_chain(read_history(root_fd) or [])
        if chain_findings:
            raise PinError("un-adopt requires a chain-valid pin-history; chain is invalid: {}"
                           .format("; ".join(chain_findings)))
        ops = _transition_ops(txn_rec)
        # Publish the durable un-adopt INTENT BEFORE any reversal, so a crash at any later point is COMPLETED
        # by `recover` (10.6), never stranded. Then complete the un-adopt idempotently.
        intent = {"transition-id": txn_rec["transition-id"],
                  "target-version": txn_rec.get("target-version", ""),
                  "quorum": int(current.get("quorum", 1)), "utc": _utc_now(),
                  "authorization": authorization}
        _atomic_publish(root, UNADOPT_REL, _render_unadopt(intent))
        _complete_un_adopt(root, root_fd, ops, intent)
    except (PinError, _journal.JournalError, OSError, KeyError, ValueError) as exc:
        os.close(root_fd)
        return _fail(exc)
    os.close(root_fd)
    print("un-adopt: reversed transition {} to pre-adoption and recorded the terminal history row"
          .format(txn_rec["transition-id"]))
    return EXIT_OK


def _transition_ops(txn_rec):
    """Reconstruct the engine op list (with prestate and poststate) from a committed transition record,
    so build_inverse_ops can invert it. tomllib parses the [[op]] inline sub-tables to dicts."""
    ops = []
    for raw in txn_rec.get("op", []):
        ops.append({"op": raw["op"], "path": raw["path"],
                    "prestate": dict(raw["prestate"]), "poststate": dict(raw["poststate"])})
    return ops


# _op_at_prestate removed in round 5: unused after the contained reverse-swap redesign (dead code).

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


def _reverse_swap(root, root_fd, ops):
    """The ONBOARDING reverse swap (10.3/10.6): reverse a pin-transition's create/mkdir ops by CONTAINED
    removal in reverse-dependency order, with NO 9.3 journal and NO lock (the journal engine is the migration
    path's, not onboarding's). TORN-SAFE and idempotent: for each op whose prestate is prior-absence, if the
    target still EXISTS (any content, so a torn/partial create is cleanly removed) it is unlinked (file) or
    rmdir'd (directory); an already-absent target is skipped. A re-run reverses only what remains. Refuses a
    non-absence prestate (a write/remove op needs a preimage and is not an onboarding op; do_pin restricts
    onboarding to create/mkdir). A directory that is unexpectedly non-empty is left in place (never destroy
    adopter content), a disclosed leniency."""
    for op in reversed(ops):
        path = op["path"]
        pre = op.get("prestate") or {}
        if pre.get("kind") != "absent":
            raise PinError("reverse-swap: {!r} prestate is {!r}, not prior-absence; the onboarding reverse "
                           "handles create/mkdir only (a write/remove op needs a preimage and is deferred)"
                           .format(path, pre.get("kind")))
        st = _journal._lstat_contained(root_fd, path)
        if st is None:
            continue                                      # already at prior-absence
        pfd, name = _journal._open_parent(root_fd, path)
        try:
            if stat.S_ISDIR(st.st_mode):
                try:
                    os.rmdir(name, dir_fd=pfd)
                except FileNotFoundError:
                    pass                                  # already removed (idempotent re-run)
                except OSError as exc:
                    raise PinError("reverse-swap: cannot remove pin-created directory {!r} ({}); it holds "
                                   "content the pin did not install - resolve that content before completing "
                                   "the reverse (never silently succeed nor delete adopter content)"
                                   .format(path, exc))
            else:
                try:
                    os.unlink(name, dir_fd=pfd)
                except FileNotFoundError:
                    pass
            os.fsync(pfd)
        finally:
            os.close(pfd)


def _trim_last_pin_row(root, root_fd, txn):
    """Restore the pin-history to its pre-transition state during a rollback: drop the single `pin` row THIS
    transition appended (identified as the LAST row iff it is a `pin` action for the transition's target
    version), keeping every prior row (a re-adoption after un-adopt leaves [pin, un-adopt, pin...]). An empty
    result removes the file. If the last row is not this transition's (a crash before the row was appended,
    or any other shape), the history is left unchanged, never dropping a foreign row (fail-closed-safe)."""
    rows = read_history(root_fd)
    if not rows:
        return
    last = rows[-1]
    if last.get("action") == "pin" and last.get("version") == txn.get("target-version"):
        remaining = rows[:-1]
        if remaining:
            _atomic_publish(root, HISTORY_REL, _render_history(remaining))
        else:
            _remove_contained(root, HISTORY_REL)


def _complete_un_adopt(root, root_fd, ops, intent):
    """Complete an un-adopt idempotently from the durable intent (10.6): reverse the onboarding ops
    (contained, torn-safe), append the terminal `un-adopt` history row if it is not already the last row,
    remove pin.toml and the transition record, sweep the spent preimages, and remove the intent marker LAST.
    Safe to re-run at ANY crash point: every step is idempotent, so recover completes an interrupted un-adopt
    to the same terminal state."""
    _reverse_swap(root, root_fd, ops)
    rows, prev = _history_rows_and_prev(root_fd)
    if not rows or rows[-1].get("action") != "un-adopt":
        release = {"version": "", "tag-object-sha": "", "commit-sha": "", "root": "", "manifest-digest": ""}
        _append_history_row(root, rows, prev, len(rows), release, int(intent.get("quorum", 1)),
                            "un-adopt", intent.get("authorization"), None)
    _remove_contained(root, PIN_REL)
    _remove_contained(root, TRANSITION_REL)
    _sweep_orphan_preimages(root, root_fd)
    _remove_contained(root, UNADOPT_REL)


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


def _validate_unadopt_intent(intent, txn_rec, pin_present):
    """Validate a durable un-adopt INTENT before recover acts on it (guard-input-soundness): a well-formed
    record with a non-empty transition-id, complete authorization, and a positive-int quorum, that BINDS to
    the live state - either its transition-id matches the present pin-transition, or the transition AND the
    pin are both already absent (the un-adopt passed pin+transition removal, only terminal-row/intent cleanup
    remains). Any other shape is forged or inconsistent and is refused fail-closed, so recover never mutates
    on an unvalidated marker (a genuine crash always leaves a well-formed, authorized intent)."""
    if not isinstance(intent, dict):
        raise PinError("recover: un-adopt intent is not a table; refusing (guard-input-soundness)")
    tid = intent.get("transition-id")
    if not isinstance(tid, str) or not tid:
        raise PinError("recover: un-adopt intent lacks a valid transition-id; refusing")
    auth = intent.get("authorization")
    if not isinstance(auth, dict):
        raise PinError("recover: un-adopt intent authorization is not a table; refusing")
    for _k in ("authorizer", "utc", "reason"):
        if not isinstance(auth.get(_k), str) or not auth.get(_k):
            raise PinError("recover: un-adopt intent authorization.{} must be a non-empty string; refusing "
                           "(a recovery never fabricates an unauthorized reversal, 10.4)".format(_k))
    q = intent.get("quorum", 1)
    if not isinstance(q, int) or isinstance(q, bool) or q < 1:
        raise PinError("recover: un-adopt intent quorum is not a positive integer; refusing")
    if txn_rec is not None:
        if txn_rec.get("transition-id") != tid:
            raise PinError("recover: un-adopt intent transition-id {!r} does not match the present transition "
                           "{!r}; refusing a forged/mismatched intent".format(tid, txn_rec.get("transition-id")))
    elif pin_present:
        raise PinError("recover: un-adopt intent with a live pin but no matching transition record "
                       "(inconsistent/forged); refusing")


# _recover_open_journal removed in round 4: VC-7 DETECTS+BLOCKS an open migration journal
# (do_pin / do_recover / doctor) but does NOT recover it - reconciling a migration journal is
# the deferred migration tool's job, and recovering it here reached the engine's absolute-path
# preimage restore (an off-tree write). Detection uses is_terminal read-only (bounded R2).

def do_recover(root):
    """Reconcile any interrupted pin operation (10.3/10.6), idempotently and from on-disk state alone. In
    order: an interrupted UN-ADOPT (pin-unadopt.toml present) -> complete it; a committed or absent pin-
    transition -> nothing to recover (sweeping orphan preimages from a pre-intent crash, but NEVER while a
    pin is live); an APPLIED transition whose pin + terminal history row already agree -> roll FORWARD (mark
    committed); otherwise (prepared, or applied-but-incomplete) roll BACK via the contained reverse swap,
    trimming this transition's history row (restoring a prior un-adopted state on a re-adoption) and removing
    the partial pin/transition/preimages. Only an initial-pin transition is recoverable here. Never touches
    archives. Idempotent."""
    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        return _fail(exc)
    try:
        root_fd = _open_root_fd(root)
    except OSError as exc:
        return _fail(exc)
    try:
        if _blocking_open_journal(root, root_fd):
            raise PinError("recover: an open migration cutover journal is present; VC-7 does not reconcile "
                           "migration journals (that is the deferred migration tool's job); resolve it there")
        intent = read_unadopt(root_fd)
        if intent is not None:
            txn_rec = read_transition(root_fd)
            _validate_unadopt_intent(intent, txn_rec, read_pin(root_fd) is not None)
            ops = _transition_ops(txn_rec) if txn_rec is not None else []
            _complete_un_adopt(root, root_fd, ops, intent)
            os.close(root_fd)
            print("recover: completed an interrupted un-adopt (transition {})"
                  .format(intent.get("transition-id")))
            return EXIT_OK
        txn = read_transition(root_fd)
        if txn is None:
            preimages_present = _journal._lstat_contained(root_fd, PREIMAGES_REL) is not None
            _h = read_history(root_fd)
            dangling_pin = bool(_h) and _h[-1].get("action") != "un-adopt"
            if preimages_present and (read_pin(root_fd) is not None or dangling_pin):
                raise PinError("recover: preimages are present alongside a live pin or a dangling (non-"
                               "terminal) pin-history but no transition record (a tampered/partial state); "
                               "refusing to sweep reversal preimages that may still be needed; restore the "
                               "transition record or `un-adopt`")
            removed = _sweep_orphan_preimages(root, root_fd) if preimages_present else 0
            os.close(root_fd)
            if removed:
                print("recover: no transition record; swept the orphan preimage store left by a crash "
                      "before the transition was published")
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
        _reverse_swap(root, root_fd, ops)
        _trim_last_pin_row(root, root_fd, txn)
        _remove_contained(root, PIN_REL)
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
        rc, out = _run_cli(["recover", "--root", str(k)])
        check("T12: recover REFUSES an open migration journal (VC-7 does not reconcile it) exit 2",
              rc == 2 and "migration" in out.lower())

        # ===== ROUND 3 blocker regressions =====
        # T15 [RB1]: a TORN payload mid-write -> recover removes the torn create, clean (was: stranded).
        t15 = tmp / "T15" / "root"; t15.mkdir(parents=True)
        s15 = _write_staged(tmp / "T15" / "s", onop, onpay, rel1, [])
        rc = crash(["pin", "--root", str(t15), "--staged", str(s15)], "torn-payload:0")
        check("T15: torn-payload crash killed the child", rc == 137)
        check("T15: a torn file is on disk (partial create)", (t15 / "aiqt-file").exists())
        rc, _ = _run_cli(["recover", "--root", str(t15)])
        check("T15: recover exits 0 on a torn payload", rc == 0)
        check("T15: recover removed the torn create", not (t15 / "aiqt-file").exists())
        check("T15: doctor clean after recover", dr(str(t15)) == 0)

        # T16 [RB9]: a malformed (empty-field) release record -> pin REFUSES, nothing published.
        t16 = tmp / "T16" / "root"; t16.mkdir(parents=True)
        badrel = {"version": "", "tag-object-sha": "t", "commit-sha": "c", "root": "r", "manifest-digest": "m"}
        s16 = _write_staged(tmp / "T16" / "s", onop, onpay, badrel, [])
        rc, out = _run_cli(["pin", "--root", str(t16), "--staged", str(s16)])
        check("T16: pin REFUSES a malformed release (empty version) exit 2", rc == 2)
        check("T16: no pin published on a malformed release", not (t16 / PIN_REL).exists())

        # T17 [RB7d]: an onboarding plan with a write op -> pin REFUSES (create/mkdir only).
        t17 = tmp / "T17" / "root"; t17.mkdir(parents=True)
        s17 = _write_staged(tmp / "T17" / "s", [{"op": "write", "path": "aiqt-file", "mode": 0o644}],
                            onpay, rel1, [])
        rc, out = _run_cli(["pin", "--root", str(t17), "--staged", str(s17)])
        check("T17: pin REFUSES a write op in an onboarding plan exit 2", rc == 2 and "create/mkdir" in out)

        # T18 [RB7]: a directory-mode change on an installed mkdir -> doctor FAIL.
        t18 = tmp / "T18" / "root"; t18.mkdir(parents=True)
        s18 = _write_staged(tmp / "T18" / "s",
                            [{"op": "mkdir", "path": "d", "mode": 0o750},
                             {"op": "create", "path": "d/f", "mode": 0o644}],
                            {"d/f": b"x\n"}, rel1, [])
        rc, _ = _run_cli(["pin", "--root", str(t18), "--staged", str(s18)])
        check("T18: pin with a dir exits 0 and doctor clean", rc == 0 and dr(str(t18)) == 0)
        os.chmod(t18 / "d", 0o777)
        check("T18: doctor FAILs a directory-mode change", dr(str(t18)) == 1)
        os.chmod(t18 / "d", 0o750)

        # T19 [RB6/BL-4]: deleting the transition record must NOT let the doctor pass over a tampered file,
        # and recover must NOT sweep a live pin's preimages.
        t19 = tmp / "T19" / "root"; t19.mkdir(parents=True)
        _run_cli(["pin", "--root", str(t19), "--staged",
                  str(_write_staged(tmp / "T19" / "s", onop, onpay, rel1, []))])
        (t19 / TRANSITION_REL).unlink()
        (t19 / "aiqt-file").write_bytes(b"tampered\n")
        check("T19: doctor is MALFORMED (not clean) with the transition deleted", dr(str(t19)) == 2)
        rc, out = _run_cli(["recover", "--root", str(t19)])
        check("T19: recover REFUSES to sweep a live pin's preimages exit 2", rc == 2)
        check("T19: the pin and its preimages survive the refused recover",
              (t19 / PIN_REL).is_file() and (t19 / PREIMAGES_REL).exists())

        # T20 [RB2]: an interrupted un-adopt (intent present) -> doctor FAILs, recover COMPLETES it.
        t20 = tmp / "T20" / "root"; t20.mkdir(parents=True)
        _run_cli(["pin", "--root", str(t20), "--staged",
                  str(_write_staged(tmp / "T20" / "s", onop, onpay, rel1, []))])
        with _RootFd(t20) as fd:
            tr = read_transition(fd)
        intent = {"transition-id": tr["transition-id"], "target-version": tr.get("target-version", ""),
                  "quorum": 1, "utc": _utc_now(),
                  "authorization": {"authorizer": "ops", "utc": _utc_now(), "reason": "reverse"}}
        _atomic_publish(t20, UNADOPT_REL, _render_unadopt(intent))     # simulate a crash right after the intent
        check("T20: doctor FAILs an interrupted un-adopt", dr(str(t20)) == 1)
        rc, _ = _run_cli(["recover", "--root", str(t20)])
        check("T20: recover completes the un-adopt exit 0", rc == 0)
        check("T20: file gone, pin gone, intent gone after completion",
              not (t20 / "aiqt-file").exists() and not (t20 / PIN_REL).exists()
              and not (t20 / UNADOPT_REL).exists())
        check("T20: doctor clean (terminal un-adopted) after recover", dr(str(t20)) == 0)
        rc, _ = _run_cli(["recover", "--root", str(t20)])
        check("T20: recover idempotent after completion", rc == 0)

        # T21 [RB5]: a crashed RE-ADOPTION (pin -> un-adopt -> pin) recovers to the prior un-adopted state.
        t21 = tmp / "T21" / "root"; t21.mkdir(parents=True)
        _run_cli(["pin", "--root", str(t21), "--staged",
                  str(_write_staged(tmp / "T21" / "s1", onop, onpay, rel1, []))])
        _run_cli(["un-adopt", "--root", str(t21), "--authorizer", "ops", "--reason", "reverse"])
        _run_cli(["pin", "--root", str(t21), "--staged",
                  str(_write_staged(tmp / "T21" / "s2", onop, onpay, rel1, []))])   # re-adoption (succeeds)
        with _RootFd(t21) as fd:
            tr2 = read_transition(fd)
            hrows = read_history(fd)
        check("T21: re-adoption gives a 3-row history [pin,un-adopt,pin]",
              hrows is not None and len(hrows) == 3)
        applied = {"transition-id": tr2["transition-id"], "action": tr2["action"], "phase": "applied",
                   "from-pin-digest": tr2.get("from-pin-digest", ""),
                   "target-version": tr2.get("target-version", ""), "ops": _transition_ops(tr2)}
        _atomic_publish(t21, TRANSITION_REL, _render_transition(applied))    # simulate a crashed re-adoption
        _remove_contained(t21, PIN_REL)
        rc, _ = _run_cli(["recover", "--root", str(t21)])
        check("T21: recover exits 0 on a crashed re-adoption", rc == 0)
        with _RootFd(t21) as fd:
            hrows2 = read_history(fd)
        check("T21: recover trimmed to the prior [pin,un-adopt] history (2 rows)",
              hrows2 is not None and len(hrows2) == 2 and hrows2[-1]["action"] == "un-adopt")
        check("T21: no pin, no installed file after recover; doctor clean",
              not (t21 / PIN_REL).exists() and not (t21 / "aiqt-file").exists() and dr(str(t21)) == 0)

        # T22 [RB4]: a symlinked journal ENTRY -> doctor open-journal MALFORMED (never followed).
        t22 = tmp / "T22" / "root"; (t22 / JOURNAL_REL).mkdir(parents=True)
        out22 = tmp / "T22-out"; out22.mkdir()
        os.symlink(str(out22), str(t22 / JOURNAL_REL / "txn-sym"))
        check("T22: doctor is MALFORMED on a symlinked journal entry (not followed)", dr(str(t22)) == 2)

        # ===== ROUND 4 blocker regressions =====
        # T23 [X3-1]: recover REFUSES a forged/authless/mismatched un-adopt intent (never mutates on it).
        t23 = tmp / "T23" / "root"; t23.mkdir(parents=True)
        _run_cli(["pin", "--root", str(t23), "--staged",
                  str(_write_staged(tmp / "T23" / "s", onop, onpay, rel1, []))])
        with _RootFd(t23) as fd:
            tr23 = read_transition(fd)
        forged = {"transition-id": tr23["transition-id"], "target-version": tr23.get("target-version", ""),
                  "quorum": 1, "utc": _utc_now(), "authorization": {"authorizer": "", "utc": "", "reason": ""}}
        _atomic_publish(t23, UNADOPT_REL, _render_unadopt(forged))
        rc, out = _run_cli(["recover", "--root", str(t23)])
        check("T23: recover REFUSES an authless un-adopt intent exit 2", rc == 2 and "authorization" in out.lower())
        check("T23: pin+file survive the refused recover", (t23 / PIN_REL).is_file() and (t23 / "aiqt-file").exists())
        mism = dict(forged); mism["transition-id"] = "pin.forged.999"
        mism["authorization"] = {"authorizer": "ops", "utc": _utc_now(), "reason": "x"}
        _atomic_publish(t23, UNADOPT_REL, _render_unadopt(mism))
        rc, out = _run_cli(["recover", "--root", str(t23)])
        check("T23: recover REFUSES a mismatched-transition intent exit 2", rc == 2)
        check("T23: pin intact after the mismatched refusal", (t23 / PIN_REL).is_file())

        # T24 [X3-2]: un-adopt SURFACES (never silently succeeds) when adopter content sits under a pin dir.
        t24 = tmp / "T24" / "root"; t24.mkdir(parents=True)
        s24 = _write_staged(tmp / "T24" / "s", [{"op": "mkdir", "path": "d", "mode": 0o755},
                                                {"op": "create", "path": "d/f", "mode": 0o644}],
                            {"d/f": b"x\n"}, rel1, [])
        _run_cli(["pin", "--root", str(t24), "--staged", str(s24)])
        (t24 / "d" / "adopter.txt").write_text("mine\n", encoding="utf-8")
        rc, out = _run_cli(["un-adopt", "--root", str(t24), "--authorizer", "ops", "--reason", "reverse"])
        check("T24: un-adopt SURFACES a non-removable pin-created dir exit 2", rc == 2)
        check("T24: the adopter file is preserved (never deleted)", (t24 / "d" / "adopter.txt").exists())
        check("T24: un-adopt did not falsely succeed (intent+pin remain for retry)",
              (t24 / PIN_REL).is_file() and (t24 / UNADOPT_REL).is_file())

        # T25 [X3-3]: do_pin REFUSES over a dangling non-terminal history (pin.toml deleted).
        t25 = tmp / "T25" / "root"; t25.mkdir(parents=True)
        _run_cli(["pin", "--root", str(t25), "--staged",
                  str(_write_staged(tmp / "T25" / "s1", onop, onpay, rel1, []))])
        (t25 / PIN_REL).unlink()
        rc, out = _run_cli(["pin", "--root", str(t25), "--staged",
                            str(_write_staged(tmp / "T25" / "s2", onop, onpay, rel1, []))])
        check("T25: do_pin REFUSES over a dangling non-terminal history exit 2",
              rc == 2 and "malformed" in out.lower())

        # T26 [X3-6]: a hardlinked installed file -> doctor FAIL.
        t26 = tmp / "T26" / "root"; t26.mkdir(parents=True)
        _run_cli(["pin", "--root", str(t26), "--staged",
                  str(_write_staged(tmp / "T26" / "s", onop, onpay, rel1, []))])
        check("T26: doctor clean after pin", dr(str(t26)) == 0)
        os.link(str(t26 / "aiqt-file"), str(t26 / "hardlink-copy"))
        check("T26: doctor FAILs a hardlinked installed file", dr(str(t26)) == 1)
        (t26 / "hardlink-copy").unlink()
        check("T26: doctor clean after removing the hardlink", dr(str(t26)) == 0)

        # T27 [X3-9]: a standalone .aiqt/migration file with no pin -> MALFORMED, not NA.
        t27 = tmp / "T27" / "root"; (t27 / MIGRATION_REL).mkdir(parents=True)
        (t27 / MIGRATION_REL / "crosswalk.toml").write_text("x = 1\n", encoding="utf-8")
        check("T27: a standalone migration file (no pin) is MALFORMED, not NA", dr(str(t27)) == 2)

        # ===== ROUND 5 blocker regressions =====
        # T28 [F1]: orphan preimages over a TERMINAL un-adopt history -> recover SWEEPS (not stranded).
        t28 = tmp / "T28" / "root"; t28.mkdir(parents=True)
        _run_cli(["pin", "--root", str(t28), "--staged",
                  str(_write_staged(tmp / "T28" / "s", onop, onpay, rel1, []))])
        _run_cli(["un-adopt", "--root", str(t28), "--authorizer", "ops", "--reason", "reverse"])
        (t28 / PREIMAGES_REL / "pin.orphan.1" / "preimages").mkdir(parents=True)   # crashed-re-adoption orphan
        check("T28: doctor MALFORMED on orphan preimages over a terminal history", dr(str(t28)) == 2)
        rc, out = _run_cli(["recover", "--root", str(t28)])
        check("T28: recover SWEEPS the orphan (not stranded) exit 0", rc == 0)
        check("T28: preimages gone; doctor clean (terminal un-adopted)",
              not (t28 / PREIMAGES_REL).exists() and dr(str(t28)) == 0)

        # T29 [F3]: un-adopt BLOCKS on an open migration journal (consistency with do_pin/do_recover).
        t29 = tmp / "T29" / "root"; t29.mkdir(parents=True)
        _run_cli(["pin", "--root", str(t29), "--staged",
                  str(_write_staged(tmp / "T29" / "s", onop, onpay, rel1, []))])
        with _RootFd(t29) as fd:
            _journal.ensure_journal_dirs(fd, JOURNAL_REL)
        (t29 / JOURNAL_REL / "txn-open").mkdir()
        _journal.publish(t29 / JOURNAL_REL / "txn-open", _journal.F_INTENT,
                         {"txn": "txn-open", "header": {}, "ops": []})
        rc, out = _run_cli(["un-adopt", "--root", str(t29), "--authorizer", "ops", "--reason", "x"])
        check("T29: un-adopt BLOCKS on an open migration journal exit 2", rc == 2 and "journal" in out.lower())

        # T30 [F4]: a symlinked .aiqt/migration -> doctor MALFORMED, NOT an uncaught traceback/crash.
        t30 = tmp / "T30" / "root"; (t30 / ".aiqt").mkdir(parents=True)
        out30 = tmp / "T30-out"; out30.mkdir()
        os.symlink(str(out30), str(t30 / MIGRATION_REL))
        check("T30: doctor is MALFORMED (not a crash) on a symlinked .aiqt/migration", dr(str(t30)) == 2)

        # T31 [F7]: do_pin REFUSES an op targeting the .aiqt/ adopter-state namespace.
        t31 = tmp / "T31" / "root"; t31.mkdir(parents=True)
        s31 = _write_staged(tmp / "T31" / "s", [{"op": "create", "path": ".aiqt/evil", "mode": 0o644}],
                            {".aiqt/evil": b"x\n"}, rel1, [])
        rc, out = _run_cli(["pin", "--root", str(t31), "--staged", str(s31)])
        check("T31: do_pin REFUSES an op targeting .aiqt/ exit 2", rc == 2 and ".aiqt" in out)
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
