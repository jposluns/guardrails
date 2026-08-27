#!/usr/bin/env python3
"""Core adopter-side doctor assertions (VER-CORE Section 12 step 7, plan 4.3). Stdlib only.

  doctor.py [--root DIR]    run the read-only assertions against an install (default: cwd)
  doctor.py --self-test     synthetic-tree invariants for each assertion

Importable, parameterized assertion functions in the conformance.py re-orchestration model: the
adopter-experience doctor composes these; this thin CLI lets the pack repo self-test and an adopter run
them standalone. Default operation is READ-ONLY: no assertion writes, and any state-changing recovery
(re-pin, carve-out, un-adopt) is a SEPARATE explicit tools/pin.py invocation that carries its own
authorization, never the doctor's default.

Assertions (plan 4.3): state-completeness, pin-and-manifest, history-chain, open-journal, repoints, reverse-step, transition, unadopt-intent.

HONESTY LIMIT, disclosed in every chain message (10.2): with no keys the pin-history chain is tamper-
evident against a CASUAL in-place edit ONLY. It is NOT truncation-evident (deleting a valid tail leaves a
valid chain) and NOT splice-proof (a full-access writer can insert a genuine old row and recompute the
suffix), so it proves NOTHING and authorizes NOTHING on its own. The doctor's chain messages disclose this
limit rather than implying proof.

Exit convention: 0 clean/NA, 1 finding, 2 malformed input or read error. Total absence of all pin/adoption
state is NA ("not adopted"); PARTIAL state (a pin without history, history without a pin absent a terminal
un-adopt row, a missing referenced preimage, anything unreadable) is exit 2 MALFORMED, never NA.
"""
import hashlib
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal  # noqa: E402  contained helpers, is_terminal, JournalError
import pin        # noqa: E402  the single source of the chain canonicalization and the pin-state readers

PASS = "PASS"
FAIL = "FAIL"                 # a finding -> exit 1
NA = "NOT APPLICABLE"         # nothing to validate -> degrade, never fake a pass
MALFORMED = "MALFORMED"       # unreadable / partial state -> exit 2 (fail-closed)

_HONESTY = ("the pin-history chain is tamper-evident against a casual in-place edit of an interior "
            "(non-tail) row ONLY; it is NOT truncation-evident, NOT tail-edit-evident, and NOT splice-proof, "
            "so it proves nothing and authorizes nothing on its own (10.2); a rollback needs wholesale "
            "anchored target validation plus recorded authorization")


def chain_honesty_note():
    """The disclosure the chain messages carry, so a caller (and the self-test) can confirm the doctor
    discloses the limit rather than claiming proof."""
    return _HONESTY


class Result:
    __slots__ = ("aid", "status", "detail")

    def __init__(self, aid, status, detail=""):
        self.aid = aid
        self.status = status
        self.detail = detail


# --- presence / fail-closed gating --------------------------------------------------------------------

def _present(root_fd, relpath):
    """True/False presence of a contained regular file, fail-closed. A symlink or unreadable parent
    surfaces as a JournalError from the caller's read, mapped to MALFORMED."""
    st = _journal._lstat_contained(root_fd, relpath)
    return st is not None


def _nlink_ok(root_fd, relpath):
    """A pin-state file must be a plain regular file: a hardlink (st_nlink > 1) is rejected (plan 4.3)."""
    st = _journal._lstat_contained(root_fd, relpath)
    if st is None:
        return True
    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1


# --- the eight assertions -----------------------------------------------------------------------------

def assert_history_chain(root_fd, root):
    """verify_chain end-to-end plus terminal-row agreement with the current pin (or the clean un-adopted
    state, reconciliation 6). Every message discloses the truncation/splice honesty limit."""
    try:
        rows = pin.read_history(root_fd)
        current = pin.read_pin(root_fd)
    except pin.PinError as exc:
        return Result("history-chain", MALFORMED, str(exc))
    if rows is None:
        return Result("history-chain", NA, "no pin-history installed")
    findings = pin.verify_chain(rows)
    if findings:
        return Result("history-chain", FAIL, "{}; {}".format("; ".join(findings), _HONESTY))
    if not rows:
        return Result("history-chain", FAIL, "pin-history has zero rows; {}".format(_HONESTY))
    last = rows[-1]
    if last.get("action") == "un-adopt":
        if current is not None:
            return Result("history-chain", FAIL,
                          "terminal un-adopt row but a pin is still present; {}".format(_HONESTY))
        return Result("history-chain", PASS,
                      "chain valid over {} row(s); terminal un-adopt agrees with the absent pin ({})"
                      .format(len(rows), _HONESTY))
    if current is None:
        return Result("history-chain", MALFORMED,
                      "history present without a pin and no terminal un-adopt row (partial state); {}"
                      .format(_HONESTY))
    pin_version = (current.get("release") or {}).get("version")
    if last.get("version") != pin_version:
        return Result("history-chain", FAIL,
                      "terminal row version {!r} disagrees with the pin {!r}; {}"
                      .format(last.get("version"), pin_version, _HONESTY))
    return Result("history-chain", PASS,
                  "chain valid over {} row(s); terminal row agrees with the pin ({})"
                  .format(len(rows), _HONESTY))


def assert_pin_and_manifest(root_fd, root):
    """The 4.2-partition pin vs tree check over the concern-3 adopter-installed extent. Concern-2
    namespace paths are never treated as set-equality members. The pin is schema-checked, its hardlink/
    symlink integrity is enforced, and every path the committed transition installed is confirmed present
    as a plain regular file or directory in the tree. The full manifest set-equality over the concern-3
    extent is owed to the adopter-experience spec and reported as an owed leg, never faked as proven."""
    try:
        current = pin.read_pin(root_fd)
    except pin.PinError as exc:
        return Result("pin-and-manifest", MALFORMED, str(exc))
    if current is None:
        return Result("pin-and-manifest", NA, "no pin installed")
    if not _nlink_ok(root_fd, pin.PIN_REL):
        return Result("pin-and-manifest", FAIL, "pin.toml is not a plain regular file (hardlink?)")
    release = current.get("release") or {}
    if not release.get("version") or not release.get("root"):
        return Result("pin-and-manifest", MALFORMED, "pin.release lacks a version or root (schema)")
    try:
        txn = pin.read_transition(root_fd)
    except pin.PinError as exc:
        return Result("pin-and-manifest", MALFORMED, str(exc))
    installed = 0
    if txn is not None and txn.get("phase") == "committed":
        for op in txn.get("op", []):
            kind, path = op.get("op"), op.get("path")
            post = op.get("poststate") or {}
            if kind in ("create", "write"):
                st = _journal._lstat_contained(root_fd, path)
                if st is None or not stat.S_ISREG(st.st_mode):
                    return Result("pin-and-manifest", FAIL,
                                  "pin installs {!r} but it is absent or not a regular file".format(path))
                if st.st_nlink != 1:
                    return Result("pin-and-manifest", FAIL,
                                  "installed {!r} is a hardlink (st_nlink={}); a pin-installed path must be a "
                                  "plain regular file".format(path, st.st_nlink))
                want_sha = post.get("content-sha256")
                if not want_sha:
                    return Result("pin-and-manifest", MALFORMED,
                                  "transition op for {!r} lacks a recorded content digest".format(path))
                try:
                    data, _ = _journal._read_contained(root_fd, path)
                except _journal.JournalError as exc:
                    return Result("pin-and-manifest", MALFORMED,
                                  "cannot read installed path {!r} ({})".format(path, exc))
                if hashlib.sha256(data).hexdigest() != want_sha:
                    return Result("pin-and-manifest", FAIL,
                                  "installed {!r} does not match the recorded pin digest (in-place drift "
                                  "or tamper)".format(path))
                want_mode = post.get("mode")
                if want_mode is None:
                    return Result("pin-and-manifest", MALFORMED,
                                  "transition op for {!r} lacks a recorded mode".format(path))
                if stat.S_IMODE(st.st_mode) != int(want_mode):
                    return Result("pin-and-manifest", FAIL,
                                  "installed {!r} mode {} differs from the recorded pin mode {}".format(
                                      path, oct(stat.S_IMODE(st.st_mode)), oct(int(want_mode))))
                installed += 1
            elif kind == "mkdir":
                st = _journal._lstat_contained(root_fd, path)
                if st is None or not stat.S_ISDIR(st.st_mode):
                    return Result("pin-and-manifest", FAIL,
                                  "pin installs directory {!r} but it is absent".format(path))
                want_mode = post.get("mode")
                if want_mode is None:
                    return Result("pin-and-manifest", MALFORMED,
                                  "transition op for directory {!r} lacks a recorded mode".format(path))
                if stat.S_IMODE(st.st_mode) != int(want_mode):
                    return Result("pin-and-manifest", FAIL,
                                  "installed directory {!r} mode {} differs from the recorded mode {}".format(
                                      path, oct(stat.S_IMODE(st.st_mode)), oct(int(want_mode))))
                installed += 1
    return Result("pin-and-manifest", PASS,
                  "pin schema valid; {} installed path(s) verified present and digest-matched against the "
                  "recorded pin; full concern-3 set-equality is owed to the deferred adopter-experience "
                  "extent roster".format(installed))


def assert_open_journal(root_fd, root):
    """FAIL on any non-terminal cutover transaction until recovered (the 9.3 gate line). An unreadable or
    invalid-sequence journal is MALFORMED (fail-closed)."""
    jr = _journal._lstat_contained(root_fd, pin.JOURNAL_REL)
    if jr is None:
        return Result("open-journal", NA, "no cutover journal installed")
    if not stat.S_ISDIR(jr.st_mode):
        return Result("open-journal", MALFORMED, "cutover journal path is not a directory")
    journal_root = Path(root) / pin.JOURNAL_REL
    open_txns = []
    try:
        pfd, name = _journal._open_parent(root_fd, pin.JOURNAL_REL)
    except (OSError, _journal.JournalError) as exc:
        return Result("open-journal", MALFORMED, "cannot open the journal ({})".format(exc))
    try:
        jfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
    except OSError as exc:
        os.close(pfd)
        return Result("open-journal", MALFORMED, "cannot open the journal ({})".format(exc))
    try:
        for entry in sorted(os.listdir(jfd)):
            est = os.stat(entry, dir_fd=jfd, follow_symlinks=False)
            if stat.S_ISLNK(est.st_mode):
                return Result("open-journal", MALFORMED,
                              "a symlinked journal entry {!r} is refused, not followed".format(entry))
            if not stat.S_ISDIR(est.st_mode):
                continue                                  # a regular file (e.g. the lock) is not a transaction dir
            if not _journal.is_terminal(journal_root / entry):
                open_txns.append(entry)
    except _journal.JournalError as exc:
        return Result("open-journal", MALFORMED, "corrupt journal ({})".format(exc))
    except OSError as exc:
        return Result("open-journal", MALFORMED, "cannot read the journal ({})".format(exc))
    finally:
        os.close(jfd)
        os.close(pfd)
    if open_txns:
        return Result("open-journal", FAIL,
                      "{} open transaction(s) block any new pin operation until recovered: {}"
                      .format(len(open_txns), ", ".join(open_txns)))
    return Result("open-journal", PASS, "no open cutover transaction")


def assert_repoints(root_fd, root):
    """Recorded repoint poststates vs the live gate configs (10.6 coupled gate re-points). A recorded
    poststate that no longer matches the live file is drift and FAILs; an unreadable repoints file or a
    referenced config is MALFORMED (fail-closed)."""
    try:
        rows = pin.read_repoints(root_fd)
    except pin.PinError as exc:
        return Result("repoints", MALFORMED, str(exc))
    if rows is None:
        return Result("repoints", NA, "no repoints recorded")
    drift = []
    for r in rows:
        path, want = r.get("path"), r.get("poststate-sha256")
        if not path or not want:
            return Result("repoints", MALFORMED, "a repoint row lacks path or poststate-sha256")
        st = _journal._lstat_contained(root_fd, path)
        if st is None or not stat.S_ISREG(st.st_mode):
            return Result("repoints", MALFORMED,
                          "repoint target {!r} is absent or not a regular file".format(path))
        try:
            data, _ = _journal._read_contained(root_fd, path)
        except _journal.JournalError as exc:
            return Result("repoints", MALFORMED, "cannot read repoint target {!r} ({})".format(path, exc))
        if hashlib.sha256(data).hexdigest() != want:
            drift.append(path)
    if drift:
        return Result("repoints", FAIL,
                      "recorded repoint poststate(s) no longer match the live config: {}"
                      .format(", ".join(drift)))
    return Result("repoints", PASS, "all {} repoint(s) match the live config".format(len(rows)))


def assert_reverse_step(root_fd, root):
    """Every preimage the committed transition references exists and hashes correctly (backs the 10.6
    one-reverse-step guarantee). A missing or mismatched preimage is MALFORMED (a partial/unreadable
    reversal input), fail-closed."""
    try:
        txn = pin.read_transition(root_fd)
    except pin.PinError as exc:
        return Result("reverse-step", MALFORMED, str(exc))
    if txn is None:
        return Result("reverse-step", NA, "no transition to reverse")
    tid = txn.get("transition-id")
    checked = 0
    for op in txn.get("op", []):
        pre = op.get("prestate") or {}
        ref = pre.get("payload")
        if ref in (None, ""):
            continue                                     # prior-absence / directory prestate: no payload
        relpath = "{}/{}/preimages/{}".format(pin.PREIMAGES_REL, str(tid), str(ref))
        try:
            data, _ = _journal._read_contained(root_fd, relpath)
        except _journal.JournalError as exc:
            return Result("reverse-step", MALFORMED,
                          "referenced preimage {} is missing/unreadable ({})".format(ref, exc))
        if hashlib.sha256(data).hexdigest() != pre.get("sha256"):
            return Result("reverse-step", MALFORMED,
                          "preimage {} does not match its recorded prestate digest".format(ref))
        checked += 1
    return Result("reverse-step", PASS,
                  "{} referenced preimage(s) present and digest-matched".format(checked))


def assert_transition(root_fd, root):
    """An interrupted pin-transition (phase != committed) is reported with the safe recovery direction and
    FAILs (it blocks any new pin operation, 4.4). The doctor is READ-ONLY: it names the recovery, it does
    not perform it (that is a separate explicit pin.py invocation with authorization)."""
    try:
        txn = pin.read_transition(root_fd)
    except pin.PinError as exc:
        return Result("transition", MALFORMED, str(exc))
    if txn is None:
        return Result("transition", NA, "no open transition")
    phase = txn.get("phase")
    if phase == "committed":
        return Result("transition", PASS, "transition {} is committed".format(txn.get("transition-id")))
    if phase == "prepared":
        return Result("transition", FAIL,
                      "transition {} interrupted at PREPARED (no swap applied); safe recovery direction: "
                      "REVERSE (discard the transition, retain the prior pin) via an explicit recovery "
                      "invocation".format(txn.get("transition-id")))
    if phase == "applied":
        return Result("transition", FAIL,
                      "transition {} interrupted at APPLIED (swap applied, pin not published); recovery "
                      "direction: FORWARD to finish the commit or REVERSE to restore the preimages, via an "
                      "explicit recovery invocation".format(txn.get("transition-id")))
    return Result("transition", MALFORMED, "transition carries an unknown phase {!r}".format(phase))


def assert_state_completeness(root_fd, root):
    """The partial-state fail-closed leg (plan 4.4): given that adoption state is NOT totally absent (the
    caller already screened the NA case), a pin present WITHOUT a pin-history is MALFORMED, and a lone
    transition or lone preimages namespace with no pin AND no history is MALFORMED. The history-without-pin
    case (and the clean terminal un-adopt exception) is judged by assert_history_chain. Only TOTAL absence
    is NA; a partial state is exit 2, never NA."""
    try:
        pin_present = _present(root_fd, pin.PIN_REL)
        history_present = _present(root_fd, pin.HISTORY_REL)
        txn_present = _present(root_fd, pin.TRANSITION_REL)
    except _journal.JournalError as exc:
        return Result("state-completeness", MALFORMED, str(exc))
    preimages_present = _journal._lstat_contained(root_fd, pin.PREIMAGES_REL) is not None
    migration_present = _journal._lstat_contained(root_fd, pin.MIGRATION_REL) is not None
    if pin_present and not history_present:
        return Result("state-completeness", MALFORMED,
                      "a pin is present without a pin-history (partial state); exit 2, never NA (4.4)")
    if pin_present and not txn_present:
        return Result("state-completeness", MALFORMED,
                      "a pin is present without its pin-transition record (partial/tampered state); the "
                      "installed-path digest verification depends on it; exit 2, never NA (4.4)")
    if not pin_present and (txn_present or preimages_present or migration_present):
        return Result("state-completeness", MALFORMED,
                      "transition/preimage/migration state present with no live pin to own it (a partial or "
                      "orphaned state); exit 2, never NA (4.4)")
    return Result("state-completeness", PASS, "pin-state file set is complete for its stage")


def assert_unadopt_intent(root_fd, root):
    """An un-adopt INTENT present means an un-adopt is in progress or was interrupted; it FAILs (exit 1) with
    the recovery direction until `recover` completes it idempotently (10.6). Absent = NA; unreadable = MALFORMED."""
    try:
        intent = pin.read_unadopt(root_fd)
    except pin.PinError as exc:
        return Result("unadopt-intent", MALFORMED, str(exc))
    if intent is None:
        return Result("unadopt-intent", NA, "no un-adopt in progress")
    return Result("unadopt-intent", FAIL,
                  "an un-adopt is in progress or interrupted (transition {}); run `recover` to complete it"
                  .format(intent.get("transition-id")))


ASSERTIONS = (assert_state_completeness, assert_pin_and_manifest, assert_history_chain,
              assert_open_journal, assert_repoints, assert_reverse_step, assert_transition,
              assert_unadopt_intent)


# --- orchestration ------------------------------------------------------------------------------------

def _adoption_absent(root_fd):
    """True only when EVERY pin/adoption-state input is totally absent (the NA "not adopted" case). Any
    single present input makes the state non-absent, so a partial state routes to the assertions where it
    is caught as MALFORMED rather than read as a clean NA."""
    for rel in (pin.PIN_REL, pin.HISTORY_REL, pin.TRANSITION_REL, pin.UNADOPT_REL):
        if _present(root_fd, rel):
            return False
    for rel in (pin.PREIMAGES_REL, pin.MIGRATION_REL):
        if _journal._lstat_contained(root_fd, rel) is not None:
            return False
    return True


def run(root):
    """Run every assertion under root read-only and return the aggregate exit code. Precedence: any
    MALFORMED -> 2; else any FAIL -> 1; else 0. A --root that does not exist or is not a directory is a
    fail-closed MALFORMED (exit 2): a typo'd root must never read as an all-absent clean run."""
    root = Path(root)
    if not root.exists():
        print("doctor: --root path does not exist: {}".format(root), file=sys.stderr)
        return 2
    if not root.is_dir():
        print("doctor: --root path is not a directory: {}".format(root), file=sys.stderr)
        return 2
    try:
        root_fd = pin._open_root_fd(root)
    except OSError as exc:
        print("doctor: cannot open --root {} ({}); fail-closed".format(root, exc), file=sys.stderr)
        return 2
    try:
        try:
            absent = _adoption_absent(root_fd)
        except _journal.JournalError as exc:
            print("doctor: cannot read the pin-state namespace ({}); fail-closed".format(exc),
                  file=sys.stderr)
            return 2
        print("AIQT doctor: {}".format(root.resolve()))
        if absent:
            print("  -- not adopted (no pin/adoption state); NOT APPLICABLE")
            return 0
        results = []
        for fn in ASSERTIONS:
            try:
                results.append(fn(root_fd, root))
            except _journal.JournalError as exc:
                results.append(Result(fn.__name__, MALFORMED,
                                      "contained-path error (a symlink or traversal was refused): {}".format(exc)))
    finally:
        os.close(root_fd)
    for r in results:
        line = "  {:<17} {}".format(r.aid, r.status)
        if r.detail:
            line += " - {}".format(r.detail)
        print(line)
    if any(r.status == MALFORMED for r in results):
        return 2
    if any(r.status == FAIL for r in results):
        return 1
    return 0


# --- self-test ----------------------------------------------------------------------------------------

def self_test():
    """The doctor's own honesty invariants over synthetic installs. The end-to-end assertion coverage is
    driven from pin.py --self-test (which builds real installs and calls doctor.run over them); here the
    doctor confirms its standalone invariants: the honesty note discloses the limit and never claims proof,
    a broken chain FAILs, a partial state is MALFORMED, and a clean absence is NA."""
    import json
    import tempfile
    import shutil

    try:
        _journal.require_containment()
    except _journal.JournalError as exc:
        print("SELF-TEST ERROR: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    note = chain_honesty_note().lower()
    failures = []
    checked = 0

    def check(name, cond):
        nonlocal checked
        checked += 1
        if not cond:
            failures.append(name)

    check("honesty note discloses not-truncation-evident", "not truncation-evident" in note)
    check("honesty note discloses splice", "splice" in note)
    check("honesty note does not claim proof", "proves nothing" in note)

    tmp = Path(tempfile.mkdtemp(prefix="aiqt-doctor-selftest-"))
    try:
        # Clean absence -> NA (exit 0).
        empty = tmp / "empty"
        empty.mkdir()
        check("clean absence is NA (exit 0)", run(str(empty)) == 0)

        # A pin without history -> partial state -> MALFORMED (exit 2).
        partial = tmp / "partial"
        (partial / ".aiqt").mkdir(parents=True)
        (partial / pin.PIN_REL).write_text(pin._render_pin({
            "adoption-path": "onboarding", "quorum": 1, "verified-utc": "2020-01-01T00:00:00Z",
            "ownership-map-identity": "m", "transition-id": "t",
            "release": {"version": "1.0.0", "tag-object-sha": "t", "commit-sha": "c", "root": "r",
                        "manifest-digest": "m"}}), encoding="utf-8")
        check("a pin without history is MALFORMED (exit 2)", run(str(partial)) == 2)

        # A broken chain (two rows, second edited) -> FAIL (exit 1). Build a pin + matching two-row history.
        broken = tmp / "broken"
        (broken / ".aiqt").mkdir(parents=True)
        r0 = {"seq": 0, "version": "1.0.0", "tag-object-sha": "t1", "commit-sha": "c1", "root": "r1",
              "quorum": 1, "utc": "2020-01-01T00:00:00Z", "action": "pin",
              "authorization": {"authorizer": "", "utc": "", "reason": ""},
              "corruption-finding": "", "chain": pin.GENESIS}
        r1 = {"seq": 1, "version": "1.1.0", "tag-object-sha": "t2", "commit-sha": "c2", "root": "r2",
              "quorum": 1, "utc": "2020-01-02T00:00:00Z", "action": "re-pin",
              "authorization": {"authorizer": "", "utc": "", "reason": ""},
              "corruption-finding": "", "chain": pin.next_chain(r0)}
        r0["version"] = "9.9.9"                          # casual in-place edit of a NON-tail (interior) row;
        # r1.chain was computed over the ORIGINAL r0, so recomputing over the edited r0 breaks at row 1.
        # (Editing the tail row would NOT be detected: the tip is unprotected, the disclosed honesty limit.)
        # A pin-ABSENT history with a broken interior chain: state-completeness passes (history present,
        # no pin) and the chain break surfaces as a FAIL (exit 1). A pin PRESENT without its transition
        # record is now MALFORMED (RB6), so this fixture omits the pin to isolate the chain-FAIL invariant.
        (broken / pin.HISTORY_REL).write_text(pin._render_history([r0, r1]), encoding="utf-8")
        rc = run(str(broken))
        check("a broken chain FAILs (exit 1)", rc == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("DOCTOR SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for f in failures:
            print("  FAILED: {}".format(f))
        return 1
    print("DOCTOR SELF-TEST: PASS ({} standalone invariant checks)".format(checked))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test()
    root = pin._arg(args, "--root") or "."
    return run(root)


if __name__ == "__main__":
    sys.exit(main())
