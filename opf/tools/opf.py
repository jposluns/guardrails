#!/usr/bin/env python3
"""opf: the OPFiles (OPF) reference-tooling dispatcher (OPF core-tooling, skeleton from U1).

  opf.py --self-test                run every registered OPF helper self-test (Linux CI leg)
  opf.py <verb> [--root DIR] ...    a store verb (default --root: the cwd product repository root)

This is the dispatcher the OPF core-tooling units grow into. U1 lands it with the store-side helper
self-test wired in; store verbs that have not yet landed are recognized names that report
NOT-YET-IMPLEMENTED and fail closed (exit 2) until their unit lands, so a stub can never read as a
passing operation. `render` HAS landed (PR-A): the `opf render` CLI requires exactly one of
`--check | --write` (a bare `render` is a usage error, exit 2); `--check` is the read-only drift check
(forwarding to the U4 engine) and `--write` (VC-4/PR-C) is the mutating half: it gathers the inert git-derived
observations caller-side (_opf_observe.gather) and hands them to the U4 engine, which composes the EXISTING U6
`validate_store` store-integrity gate and permits writing when source integrity holds. Post-write validation
checks source integrity and regenerated outputs; other deliverable failures can remain. `doctor` HAS landed: `opf doctor
[--root DIR]` RESOLVES the store, gathers the inert git-derived observations (_opf_observe.gather: tracked,
actual_remote, prior), and runs the U6 `validate_store` store-integrity engine over them, returning that
engine's 0/1/2 contract (a NOT-ADOPTED root reports NOT APPLICABLE and exits 0). Doctor is read-only; its
observation gather is the caller-side git seam validate_store itself never touches. `upgrade` HAS landed
(spec 9.2): `opf upgrade [--root DIR] [--homes-plan]`. With `--homes-plan`, it prints the homes-generation
migration plan read-only and exits without upgrading. Otherwise it is the in-place, additive, idempotent
upgrade from 1.0.0 or 1.1.0 to 1.2.0. The 1.1.0 schema delta changes only spec_version; declared views
are then regenerated, so a stale committed view can change. The 1.0.0 path also applies the earlier
schema delta
(base-table and discovery-token rename, decision_support retirement, type and view declarations,
DECISIONS.md source widening, counters, and missing indexes). Neither creates init.toml provenance.
It refuses a store above the tooling spec. When migrating a 1.0.0 or 1.1.0 store, its preconditions
include readable, canonical manifest/counters matching a recognised origin shape, cleanliness over
planned schema/render destinations and index collision candidates (including ignored files there),
and acquisition of its lease. An untracked or ignored lease is excepted from the cleanliness check
and handled separately by lease acquisition. It then applies the schema delta,
renders declared views, and requires a full doctor VALID before offering the uncommitted change for
review and merge. A store already at the
tooling spec_version is a byte no-op when doctor-VALID and exits 2 otherwise; a NOT-ADOPTED root reports
NOT APPLICABLE and exits 0. `import` HAS
landed (OPF-IMPORT-VERB): `opf import [--root DIR] (--scan --set FILE | --plan --set FILE | --review
<run-id> --actor NAME (--decisions FILE | --interactive) | --apply <run-id>)` wires the reserved verb onto
the U7 operation layer (_opf_import scan/plan/review/apply). Exactly one mode is required; `--scan` renders
the canonical inventory to stdout writing nothing, `--plan` stages a candidate run, `--review` captures an
attributed acceptance.json (no live-store write), and `--apply` wires onto the PR-C apply-promotion
(the journaled, verified-restore cutover that promotes an accepted run). Each mode maps the operation layer's
0/1/2 verdict to the CLI exit contract. Unlike the applicability-probe siblings, import is a REQUESTED
operation: an unresolved / NOT-ADOPTED root fails cannot-evaluate (exit 2) with a "run `opf init` first"
message rather than reporting NOT APPLICABLE (divergence D7).

`init` HAS landed: `opf init [--root DIR]` creates validated store sources, a pointer, and a starter
`CHANGELOG.md` when none exists, without git writes or rendering.
`absorb` HAS landed: `opf absorb [--root DIR] [--covers TOKEN] [--freeze-digest]`
prints a changelog draft or freeze digest without writing files.
`record` HAS landed (spec 8.8): `opf record create` and `opf record worklog-append` author one record or
worklog entry through one journaled publication, then render and require doctor VALID, leaving the change
uncommitted; `opf record transition` and `opf record done-with-receipt` fail closed as not yet implemented.

Adopter-rooted, like doctor.py/migrate.py/conformance.py: an OPF verb operates on a PRODUCT repository
root named by --root (default: the cwd), never on this pack's own tree via `_gen_common.repo_root()`.
The pack is a readable non-adopter root, so a live `opf.py render --root . --check` here reports NOT
APPLICABLE; the assurance rides the `--self-test` leg over synthetic stores (spec-honest, mirroring the
crosswalk/doctor/migrate legs in run_all_checks.sh).

Deliberately NOT named tools/gen_*.py: the generated-source registry (gen_gensrc.py) discovers gen_*.py
and validates fixed repo-relative targets, but OPF renders into an adopter --root with no fixed
repo-relative target, so this family gates as self-tests instead (the U1 build-plan section 5 rule).

Launched isolated (-I -B) per the Python-launcher-isolation gate; sibling helpers are imported through
the sys.path insert idiom the repo's tools share.
"""
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for the guarded _opf_* helper bootstrap below

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_MALFORMED = 2


def _bootstrap():
    """Import the non-stdlib _opf_* helper modules the dispatch and self-test legs use, binding each to a
    module global. Called FIRST in main() so a broken or PARTIAL install -- a helper that cannot be imported
    (ImportError) or read (OSError) -- maps to a located cannot-evaluate (EXIT_MALFORMED / 2), never an
    uncaught ImportError that Python would surface as its default exit 1 and that a direct `opf render
    --check` would then read as a false DRIFT (the exit-1=drift conflation, reached here BEFORE the render
    dispatcher's own fail-closed handler). Only ImportError and OSError (the broken/partial-install signals)
    are caught; a broader error propagates rather than being masked as bootstrap. The imports moved OFF module
    top for exactly this reason -- an eager top-level import failed before main()'s contract could apply.
    Idempotent: a re-import of an already-loaded module is a cheap no-op, so main() may call it on every
    invocation. Returns EXIT_OK on success, or EXIT_MALFORMED with a located diagnostic naming the helper
    that could not be brought in."""
    global _opf_store, _opf_schema, _opf_release, _opf_changelog, _opf_check
    global _opf_emit, _opf_views, _opf_fuzz, _opf_import, _opf_importers, _opf_observe, _opf_absorb
    global _opf_ingest, _opf_worklog, _opf_write_guard, _opf_record
    try:
        import _opf_worklog     # manifest-selected worklog intake + WL reference grammar
        import _opf_store       # U1: store resolution + discovery + manifest base/profile schema
        import _opf_schema      # U2: record envelope + baseline type schemas + status/transition + counters
        import _opf_release     # U3: version.toml + worklog.toml + span tiling + coverage digests + release cut
        import _opf_changelog   # U5: changelog range-coverage + freeze gates over version.toml + CHANGELOG.md
        import _opf_check       # U6: store-level integrity validator (validate_store; engine for opf doctor)
        import _opf_emit        # U8: the constrained-subset TOML emitter (canonical, byte-canon-clean)
        import _opf_views       # U4: deterministic view generators + the closed transform vocabulary
        import _opf_fuzz        # adversarial input-hardening proof (membership/type-guard class closure)
        import _opf_import      # U7: import staging (module + self-test; the live import verb is wired below)
        import _opf_importers   # MIG-PR2: the shared import layer (deterministic importers + loss accounting)
        import _opf_ingest      # MIG-PR3: root-ingest detect + the disposition planner (plan_ingest)
        import _opf_observe     # PR-B: caller-side git-derived observations for the doctor verb (validate_store)
        import _opf_absorb      # OPF-CHANGELOG-ABSORB: read-only CHANGELOG.md drafter (composes on U5)
        import _opf_write_guard  # the in-place writers' shared cleanliness gate and single-writer lease
        import _opf_record      # OPF-RECORD: the record-authoring verb (spec 8.8)
    except ImportError as exc:
        print("opf: cannot bootstrap: {} (cannot evaluate)".format(exc.name or exc), file=sys.stderr)
        return EXIT_MALFORMED
    except OSError as exc:
        print("opf: cannot bootstrap: a helper module could not be read ({!r}) (cannot evaluate)".format(
            exc), file=sys.stderr)
        return EXIT_MALFORMED
    return EXIT_OK

def _aggregator_self_test():
    """Guard the aggregator's fail-closed return-vocabulary check (MAJOR 3). A helper returning a value
    OUTSIDE the {0,1,2} int vocabulary must fail the aggregate CLOSED (a non-zero worst), never be
    admitted as clean because a bool or float compares equal to an allowed int (False == 0, True == 1,
    0.0 == 0). Returns 0 clean, 1 on a failure. Registered below so `opf.py --self-test` exercises it;
    the store legs did not, letting a helper returning False produce an aggregate exit 0."""
    ok = True
    # A helper returning False (bool, == 0) must NOT aggregate to clean.
    if run_self_tests((("synthetic-false", lambda: False),)) == EXIT_OK:
        ok = False
    # A helper returning 0.0 (float, == 0) must NOT aggregate to clean.
    if run_self_tests((("synthetic-zero-float", lambda: 0.0),)) == EXIT_OK:
        ok = False
    # An out-of-range int (3) must NOT aggregate to clean.
    if run_self_tests((("synthetic-three", lambda: 3),)) == EXIT_OK:
        ok = False
    # A genuine clean int (0) still aggregates to clean: the check does not over-reject.
    if run_self_tests((("synthetic-zero", lambda: 0),)) != EXIT_OK:
        ok = False
    if not ok:
        print("opf aggregator self-test: FAIL (fail-closed vocabulary check admitted a bad return)",
              file=sys.stderr)
        return EXIT_FINDING
    print("opf aggregator self-test: PASS (fail-closed on non-int / out-of-range helper returns)")
    return EXIT_OK


def _watchdog_timer_case(label, mode):
    """Private subprocess fixture: its timers belong to this process alone."""
    import contextlib
    import io
    import os
    import signal
    import time
    from unittest.mock import patch

    if _bootstrap() != EXIT_OK:
        return EXIT_MALFORMED
    functions = {
        "changelog": _opf_changelog.self_test, "views": _opf_views.self_test,
        "store": _opf_store.self_test, "check": _opf_check.self_test,
        "emit": _opf_emit.self_test,
        "window": lambda: 0 if _opf_emit.run_bounded(
            lambda: (time.sleep(2), "RETURNED")[1], timeout_s=10) == "RETURNED" else 1}
    which = {"virtual": signal.ITIMER_VIRTUAL, "prof": signal.ITIMER_PROF}.get(
        mode, signal.ITIMER_REAL)
    signum = {signal.ITIMER_REAL: signal.SIGALRM,
              signal.ITIMER_VIRTUAL: signal.SIGVTALRM,
              signal.ITIMER_PROF: signal.SIGPROF}[which]
    fired = []
    def handler(sig, frame):
        fired.append(sig)
    signal.signal(signum, handler)
    if mode == "expiry":
        # This private subprocess must deliver the expiry even if its launcher blocked SIGALRM.
        # Set the fixture's initial mask before arming/snapshotting; never change the launcher.
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signum})
    interval = 1800.0 if mode == "periodic" else 0.0
    value = 0.0 if mode == "inactive" else (1.0 if mode == "expiry" else 3600.0)
    signal.setitimer(which, value, interval)
    if mode == "pending":
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
        signal.raise_signal(signal.SIGALRM)
    before = signal.getitimer(which)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
    pending = signal.sigpending()
    pid, mutations = os.getpid(), []

    shields = []

    def forbid_parent(name, original):
        def call(*args, **kwargs):
            # An empty SIG_BLOCK is only a mask query. The supervision layer's
            # OWN cancellation masking (fix 2w) is sanctioned, verified as an
            # exact pair: a SIG_BLOCK of {SIGINT, SIGTERM} followed by a
            # SIG_SETMASK restoring the exact mask captured at the block; any
            # other mutation, and any unbalanced or inexact restore, is a
            # borrowed-state violation.
            query = (name == "pthread_sigmask" and len(args) == 2
                     and args[0] == signal.SIG_BLOCK and not args[1] and not kwargs)
            sanctioned = False
            if (name == "pthread_sigmask" and len(args) == 2 and not kwargs
                    and args[0] == signal.SIG_BLOCK
                    and args[1] == {signal.SIGINT, signal.SIGTERM}):
                sanctioned = True
                shields.append(original(signal.SIG_BLOCK, set()))
            elif (name == "pthread_sigmask" and len(args) == 2 and not kwargs
                    and args[0] == signal.SIG_SETMASK and shields
                    and args[1] == shields[-1]):
                sanctioned = True
                shields.pop()
            if os.getpid() == pid and not (query or sanctioned):
                mutations.append(name)
                raise AssertionError("caller signal mutation: " + name)
            return original(*args, **kwargs)
        return call

    output = io.StringIO()
    with contextlib.ExitStack() as stack:
        for name in ("setitimer", "alarm", "signal", "pthread_sigmask"):
            stack.enter_context(patch.object(
                signal, name, forbid_parent(name, getattr(signal, name))))
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            rc = functions[label]()
    after = signal.getitimer(which)
    ok = (rc == EXIT_OK and not mutations and not shields and after[1] == before[1]
          and signal.getsignal(signum) is handler
          and signal.pthread_sigmask(signal.SIG_BLOCK, set()) == mask)
    if mode == "expiry":
        ok = ok and after == (0.0, 0.0) and fired == [signum]
    elif mode == "inactive":
        ok = ok and before == after == (0.0, 0.0) and not fired
    else:
        # No syscall can reset the countdown or phase; no narrow wall-clock
        # assertion. CPU timers continue in their own kernel clock domains.
        ok = ok and 0 < after[0] <= before[0] and not fired
    if mode == "pending":
        ok = ok and signal.SIGALRM in pending and signal.SIGALRM in signal.sigpending()
    if not ok:
        print(label, mode, rc, before, after, mutations, fired, output.getvalue())
    # No save/restore or teardown mutation: the subprocess exits with its timer.
    return EXIT_OK if ok else EXIT_FINDING


def _watchdog_isolation_self_test():
    """Exercise actual callers in fresh processes, without borrowing our timer."""
    import subprocess
    import signal
    from _opf_emit import run_status_owned
    # These fixtures own their children's statuses; never borrow the caller's disposition.
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        print("opf watchdog isolation: FAIL (unowned SIGCHLD disposition)")
        return EXIT_FINDING
    if _bootstrap() != EXIT_OK:
        return EXIT_MALFORMED
    required = ("ITIMER_REAL", "ITIMER_VIRTUAL", "ITIMER_PROF", "pthread_sigmask", "sigpending")
    if not hasattr(os, "fork") or not all(hasattr(signal, name) for name in required):
        print("opf watchdog isolation: FAIL (required POSIX facilities unavailable)")
        return EXIT_FINDING
    cases = [(label, mode) for label in ("changelog", "views", "store", "check", "emit")
             for mode in ("inactive", "single", "periodic", "virtual", "prof", "pending")]
    cases.append(("window", "expiry"))
    failed = False
    for label, mode in cases:
        code = ("import sys; sys.path.insert(0, " + repr(str(Path(__file__).resolve().parent))
                + "); import opf; return opf._watchdog_timer_case("
                + repr(label) + ", " + repr(mode) + ")")
        try:
            result = run_status_owned([sys.executable, "-I", "-B", "-c", code],
                                    fixture_id="timer/" + label + "/" + mode,
                                    capture_output=True, text=True, timeout=180)
            ok = result.returncode == EXIT_OK
            detail = result.stdout + result.stderr
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            ok, detail = False, str(exc)
        except subprocess.TimeoutExpired:
            ok, detail = False, "fixture exceeded its 180s process bound"
        if not ok:
            failed = True
            print("opf watchdog isolation: FAIL", label, mode, detail, file=sys.stderr)
    for name in ("_fixture_setpgid", "_fixture_owned_fds", "release_fd"):
        if hasattr(_opf_emit, name) or hasattr(_opf_emit._FixtureProcess, name):
            failed = True
            print("opf watchdog isolation: FAIL (deleted supervision machinery still "
                  "present: " + name + ")", file=sys.stderr)
    if hasattr(_opf_store, "snapshot_caller_alarm") or hasattr(_opf_store, "restore_caller_alarm"):
        failed = True
        print("opf watchdog isolation: FAIL (borrow helper still present)", file=sys.stderr)
    if not failed:
        print("opf watchdog isolation: PASS (caller timers and signal state untouched)")
    return EXIT_FINDING if failed else EXIT_OK


def _watchdog_deadline_case(mode):
    """Private subprocess: at-fork hooks cannot be unregistered, so never install them in the runner."""
    import signal
    import time
    from unittest.mock import patch
    import _opf_emit
    import json
    import tempfile

    real_fork, real_pipe = os.fork, os.pipe
    started_r, started_w = real_pipe()
    caller = os.getpid()
    # The enclosing run_status_owned deadline rescues a regressed run_bounded.
    # It owns the full tree, so this fixture never races it with another signal.
    child, pipe = [], []

    def fork():
        pid = real_fork()
        if pid:
            child.append(pid)
        return pid

    def capture_pipe():
        # Caller-side only: the guardian also calls os.pipe (the subject
        # acknowledgment pipe), and capturing that pair in the guardian's copy
        # would hand the thunk the ack descriptors instead of run_bounded's pipe.
        pair = real_pipe()
        if os.getpid() == caller:
            pipe[:] = pair
        return pair

    def stall():
        while True:
            time.sleep(60)

    def startup():
        os.write(started_w, b"started")
        if mode == "delayed-start":
            time.sleep(2)
        else:
            stall()

    def thunk():
        if mode in ("pipe-stall", "exit-stall", "transient-census"):
            # Defeat the independent child timer deliberately: exercise the PARENT deadline.
            signal.setitimer(signal.ITIMER_REAL, 0)
            os.write(started_w, b"started")
            os.write(pipe[1], b"BUFFERED-TOKEN")
            if mode == "exit-stall":
                os.close(pipe[1])                       # EOF before exit must not permit an unbounded wait
            stall()
        return "RETURNED"

    if mode in ("delayed-start", "stuck-start"):
        os.register_at_fork(after_in_child=startup)
    real_signal = _opf_emit._fixture_signal
    real_drain, real_children = _opf_emit._fixture_drain, _opf_emit._fixture_children
    empty_since = [None]
    events = tempfile.TemporaryFile()

    def record(row):
        os.write(events.fileno(), (json.dumps(row) + "\n").encode("ascii"))

    def cancel(pid, signum, *args, **kwargs):
        sent = real_signal(pid, signum, *args, **kwargs)
        if sent and signum == signal.SIGKILL:
            record(["cancel", time.monotonic()])
        return sent

    def drain(*args, **kwargs):
        try:
            return real_drain(*args, **kwargs)
        finally:
            record(["drain", time.monotonic(), kwargs["deadline"]])

    def census():
        if mode == "transient-census":
            if empty_since[0] is None:
                empty_since[0] = time.monotonic()
            if time.monotonic() - empty_since[0] < 1.6:
                return []
        return real_children()

    result, elapsed, reaped = None, None, False
    try:
        start = time.monotonic()
        with patch.object(os, "fork", fork), patch.object(os, "pipe", capture_pipe), \
                patch.object(_opf_emit, "_fixture_signal", cancel), \
                patch.object(_opf_emit, "_fixture_drain", drain), \
                patch.object(_opf_emit, "_fixture_children", census):
            # The thunk writes started_w and the guardian-side drain/cancel hooks
            # write the events file: both must be DECLARED under the fd allowlist.
            result = _opf_emit.run_bounded(thunk, timeout_s=0.25,
                                           keep_fds=(started_w, events.fileno()))
        finished = time.monotonic()
        elapsed = finished - start
        events.seek(0)
        rows = [json.loads(line) for line in events]
        cancellations = [row[1] for row in rows if row[0] == "cancel"]
        drains = [row for row in rows if row[0] == "drain"]
        cancelled = min(cancellations) if cancellations else None
        # Execution latency ends at the observed signal, never at cleanup end.
        execution_ok = cancelled is not None and 0.25 <= cancelled - start < 1.5
        # Cleanup is checked against its own declared deadline. One second is
        # allowed separately for receipt delivery and parent scheduling.
        cleanup_limit = max([row[2] for row in drains] or
                            [(cancelled or start) + _opf_emit._FIXTURE_CLEANUP_GRACE])
        cleanup_ok = (all(row[1] <= row[2] for row in drains)
                      and finished <= cleanup_limit + 1)
        if mode == "transient-census":
            # The old combined 1.5 s assertion rejects this passing cleanup.
            assert elapsed > 1.5 and drains, (elapsed, rows)
        # The helper owns cleanup. A diagnostic never sends another signal.
        reaped = _opf_emit._fixture_child_reaped(child[0])
        os.set_blocking(started_r, False)
        reached = os.read(started_r, 200) == b"started"
        ok = (result == "TIMEOUT" and execution_ok and cleanup_ok
              and reached and reaped)
    finally:
        os.close(started_r)
        os.close(started_w)
        events.close()
    print("opf watchdog deadline:", mode, "PASS" if ok else "FAIL",
          result, "execution", None if cancelled is None else cancelled - start,
          "total", elapsed, "cleanup", cleanup_ok, "reaped", reaped)
    return EXIT_OK if ok else EXIT_FINDING


def _watchdog_safety_case(mode):
    """Private process: exercise ownership, descriptor bounds, and the reap discriminator."""
    import errno
    import signal
    import time
    from unittest.mock import patch
    import _opf_emit

    real_pipe, real_fork, real_wait, real_waitid = os.pipe, os.fork, os.waitpid, os.waitid
    pipes, children, kills, stolen = [], [], [], []
    saved = signal.getsignal(signal.SIGCHLD)

    def pipe():
        pair = real_pipe()
        pipes.extend(pair)
        return pair

    def fork():
        pid = real_fork()
        if pid:
            children.append(pid)
        return pid

    def reaper(*_):
        try:
            pid, _status = real_wait(-1, os.WNOHANG)
            if pid:
                stolen.append(pid)
        except ChildProcessError:
            pass

    def steal_wait(pid, options):
        if options == os.WNOHANG:
            stolen.append(real_wait(pid, 0)[0])
        return real_wait(pid, options)                  # real ECHILD after a competing reap

    def steal_cleanup(*args):
        stolen.append(real_wait(children[0], 0)[0])
        return real_waitid(*args)                       # real ECHILD at the cleanup ownership probe

    def closed():
        for fd in pipes:
            try:
                os.fstat(fd)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    continue
                raise
            return False
        return True

    if mode == "lost-cleanup":
        return _watchdog_completion_case("no-signal-echild")

    if mode == "liveness-permission":
        real_kill = os.kill
        def denied_probe(pid, sig):
            if sig == 0:
                raise PermissionError("recycled PID")
            return real_kill(pid, sig)
        with patch.object(os, "kill", denied_probe):
            return _watchdog_deadline_case("pipe-stall")

    if mode == "missing-reap":
        original = _opf_emit.run_bounded
        def skip_reap(child):
            # Close the control channel but leave the guardian unreaped: the
            # bounded-close collector is exactly what this mutant removes.
            child.control.close()
            child.peer.close()
        def skip_poll(child):
            return None
        def mutant(*args, **kwargs):
            with patch.object(_opf_emit._FixtureProcess, "close", skip_reap), \
                    patch.object(_opf_emit._FixtureProcess, "poll", skip_poll):
                return original(*args, **kwargs)
        with patch.object(_opf_emit, "run_bounded", mutant):
            return (EXIT_OK if _watchdog_deadline_case("pipe-stall") == EXIT_FINDING
                    else EXIT_FINDING)

    with patch.object(os, "pipe", pipe), patch.object(os, "fork", fork):
        if mode in ("ignored-chld", "reaper-chld", "lost-reap", "lost-cleanup"):
            disposition = (signal.SIG_IGN if mode == "ignored-chld"
                           else reaper if mode == "reaper-chld" else signal.SIG_DFL)
            signal.signal(signal.SIGCHLD, disposition)
            try:
                from contextlib import ExitStack
                with ExitStack() as stack:
                    stack.enter_context(patch.object(
                        os, "kill", lambda *args: kills.append(("kill", args))))
                    stack.enter_context(patch.object(
                        os, "killpg", lambda *args: kills.append(("killpg", args))))
                    if mode == "lost-reap":
                        stack.enter_context(patch.object(os, "waitpid", steal_wait))
                    elif mode == "lost-cleanup":
                        stack.enter_context(patch.object(os, "waitpid", return_value=(0, 0)))
                        stack.enter_context(patch.object(os, "waitid", steal_cleanup))
                    result = _opf_emit.run_bounded(lambda: "OK", timeout_s=0.1)
                expected = ("SETUP-ERROR:ChildStatusUnavailable:" if mode.startswith("lost-")
                            else "SETUP-ERROR:ChildOwnership")
                matches = result.startswith(expected) if mode.startswith("lost-") else result == expected
                ok = (matches and not kills and closed()
                      and signal.getsignal(signal.SIGCHLD) == disposition)
                ok = ok and (stolen == children and len(children) == 1 if mode.startswith("lost-")
                             else not children and not pipes)
            finally:
                signal.signal(signal.SIGCHLD, saved)
        elif mode == "high-fd":
            fds = []
            try:
                while len(fds) <= 1024 or fds[-1] <= 1024:
                    fds.append(os.open(os.devnull, os.O_RDONLY))
                result = _opf_emit.run_bounded(lambda: "OK", timeout_s=1)
                ok = result == "OK" and min(pipes) > 1024 and closed()
            finally:
                for fd in fds:
                    os.close(fd)
        elif mode == "huge-timeout":
            result = _opf_emit.run_bounded(lambda: "UNBOUNDED", timeout_s=10**400)
            ok = result.startswith("SETUP-ERROR:") and not pipes and not children and closed()
        elif mode == "fork-error":
            with patch.object(os, "fork", side_effect=RuntimeError("fork setup")):
                result = _opf_emit.run_bounded(lambda: "UNBOUNDED")
            ok = result == "SETUP-ERROR:RuntimeError:fork setup" and len(pipes) == 2 and closed()
        elif mode == "poll-error":
            import select
            real_poll, calls = select.poll, []
            def poll_fault():
                calls.append(None)
                if len(calls) == 2:  # collection, after guardian startup
                    raise RuntimeError("poll setup")
                return real_poll()
            with patch.object(select, "poll", poll_fault):
                try:
                    _opf_emit.run_bounded(lambda: "OK")
                except RuntimeError:
                    result = "ERROR"
                else:
                    result = "RETURNED"
            reaped = False
            try:
                real_wait(children[0], os.WNOHANG)
            except ChildProcessError:
                reaped = True
            ok = result == "ERROR" and reaped and closed()
        else:
            raise ValueError("unknown watchdog safety case: " + mode)
    print("opf watchdog safety:", mode, "PASS" if ok else "FAIL", result, kills)
    return EXIT_OK if ok else EXIT_FINDING


def _watchdog_launcher_case(label, disposition):
    """Inject exit-37 subjects into both watchdog matrices and the completion launcher."""
    import contextlib
    import io
    import signal
    import subprocess
    from unittest.mock import patch

    from _opf_emit import run_status_owned

    def shared():
        try:
            run_status_owned([sys.executable, "-I", "-B", "-c", "raise SystemExit(37)"],
                             fixture_id="launcher/exit-37", check=True, capture_output=True, timeout=5)
        except (RuntimeError, subprocess.CalledProcessError):
            return EXIT_FINDING
        return EXIT_OK

    runner = {"isolation": _watchdog_isolation_self_test,
              "regression": _watchdog_regression_self_test, "shared": shared}[label]
    import _opf_emit
    real_run = _opf_emit._run_fixture_process
    statuses = []

    def failure(*args, **kwargs):
        result = real_run([sys.executable, "-I", "-B", "-c", "raise SystemExit(37)"],
                          timeout=5)
        statuses.append(result.returncode)
        if kwargs.get("check") and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, result.args)
        return result

    saved = signal.getsignal(signal.SIGCHLD)
    hostile = signal.SIG_IGN if disposition == "ignored" else lambda *_: None
    try:
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        with patch.object(_opf_emit, "_run_fixture_process", failure), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            control = runner()
            control_ok = control == EXIT_FINDING and bool(statuses) and set(statuses) == {37}
            statuses.clear()
            signal.signal(signal.SIGCHLD, hostile)
            result = runner()
        ok = (control_ok and result == EXIT_FINDING and not statuses
              and signal.getsignal(signal.SIGCHLD) == hostile)
    finally:
        signal.signal(signal.SIGCHLD, saved)
    print("opf watchdog launcher:", label, disposition, "PASS" if ok else "FAIL",
          "refused", result, "launched statuses", statuses)
    return EXIT_OK if ok else EXIT_FINDING


def _watchdog_completion_case(mode):
    """Run each irreversible audit-hook/disposition experiment in its own fixture process."""
    import json
    import signal
    import subprocess
    import tempfile
    import threading
    from unittest.mock import patch
    import _opf_emit as emit

    command = [sys.executable, "-I", "-B", "-c", "return 0"]

    def launch(code="return 0", **kwargs):
        return emit.run_status_owned([*command[:4], code],
                                     fixture_id="completion/" + mode, timeout=10, **kwargs)

    def refuses(error, call):
        try:
            call()
        except error:
            return
        raise AssertionError("fixture was accepted: " + mode)

    if mode == "guardian-error":
        import errno
        caller, real_pidfd = os.getpid(), emit._fixture_pidfd

        def fail_pidfd(target):
            if os.getpid() != caller:
                raise OSError(errno.EIO, "injected guardian exception")
            return real_pidfd(target)

        def diagnose():
            try:
                launch()
            except emit.ChildStatusUnavailable as exc:
                message = str(exc)
                assert "OSError" in message and '"errno": 5' in message, message
                assert "injected guardian exception" in message, message
                assert "raw wait status=32000" in message and "exitcode=125" in message, message
                return
            raise AssertionError("guardian failure was accepted")

        with patch.object(emit, "_fixture_pidfd", fail_pidfd):
            diagnose()
            # Flip the caller's reader back to the old evidence-discarding path.
            with patch.object(emit._FixtureProcess, "_read_report",
                              side_effect=emit.ChildStatusUnavailable("fixture guardian failed")):
                refuses(AssertionError, diagnose)
        with patch.object(emit, "_fixture_subreaper",
                          side_effect=OSError(errno.EIO, "injected guardian exception")):
            diagnose()  # before READY must carry the same evidence
    elif mode == "bounded-diagnostics":
        def check():
            result = emit.run_bounded(lambda: "OK", timeout_s=2)
            assert result.startswith("SETUP-ERROR:ChildStatusUnavailable:"), result
            for text in ("OSError", '"errno": 5', "QA15-CAUSE",
                         "raw wait status=32000", "exitcode=125"):
                assert text in result, result

        for boundary in ("_fixture_subreaper", "_fixture_close_all_except"):
            with patch.object(emit, boundary, side_effect=OSError(5, "QA15-CAUSE")):
                check()
                with patch.object(emit, "_bounded_setup_error",
                                  side_effect=lambda exc: "SETUP-ERROR:" + type(exc).__name__):
                    refuses(AssertionError, check)
        # Also retain evidence from close(), not only startup and poll().
        real_close = emit._FixtureProcess.close
        def fail_close(child):
            real_close(child)
            raise emit.ChildStatusUnavailable("raw wait status=32000; QA15-CLOSE")
        def check_close():
            result = emit.run_bounded(lambda: "OK", timeout_s=2)
            assert "QA15-CLOSE" in result and "raw wait status=32000" in result, result
        with patch.object(emit._FixtureProcess, "close", fail_close):
            check_close()
            with patch.object(emit, "_bounded_setup_error",
                              side_effect=lambda exc: "SETUP-ERROR:" + type(exc).__name__):
                refuses(AssertionError, check_close)
    elif mode == "cleanup-budget":
        import time
        # A controlled clock discriminates the remaining-deadline policy without
        # making the test itself depend on host scheduling.
        def policy():
            with patch.object(time, "monotonic", return_value=100):
                assert emit._fixture_cleanup_deadline(120) == 120
                assert emit._fixture_cleanup_deadline(99) == 100 + emit._FIXTURE_CLEANUP_GRACE
                assert emit._fixture_cleanup_deadline() == 100 + emit._FIXTURE_CLEANUP_GRACE
        policy()
        with patch.object(emit, "_fixture_cleanup_deadline",
                          side_effect=lambda deadline=None: time.monotonic() + 5):
            refuses(AssertionError, policy)
        # Observe forwarding and error-path reuse in the real guardian. Fail the
        # first drain; let the second perform real cleanup under the SAME bound.
        with tempfile.TemporaryDirectory(prefix="opf-budget-") as budget_dir:
            # The instrumented budget/drain run in the GUARDIAN, which does not
            # inherit this caller's descriptors (fd allowlist): log by PATH.
            log_path = Path(budget_dir, "log")
            def append(entry):
                with open(log_path, "a", encoding="ascii") as sink:
                    sink.write(entry + "\n")
            real_drain = emit._fixture_drain
            attempts = []
            real_budget = emit._fixture_cleanup_deadline
            def budget(execution_deadline=None):
                bound = real_budget(execution_deadline)
                append(json.dumps([execution_deadline, bound]))
                return bound
            def retry(subject, subject_fd=None, *, deadline=None):
                attempts.append(deadline)
                append(repr(deadline))
                if len(attempts) == 1:
                    raise OSError(5, "QA15-RETRY")
                return real_drain(subject, subject_fd, deadline=deadline)
            with patch.object(emit, "_fixture_drain", retry), \
                    patch.object(emit, "_fixture_cleanup_deadline", budget):
                refuses(emit.ChildStatusUnavailable, launch)
            bounds = [json.loads(line)
                      for line in log_path.read_text(encoding="ascii").splitlines()]
            assert len(bounds) == 3 and bounds[0][0] is not None, bounds
            assert bounds[0][1] == bounds[1] == bounds[2], bounds
        # Empty census never licenses completion, even when its budget expires.
        with patch.object(os, "waitid", return_value=None), \
                patch.object(emit, "_fixture_signal", return_value=True), \
                patch.object(emit, "_fixture_children", return_value=[]), \
                patch.object(time, "monotonic", side_effect=[100, 102]), \
                patch.object(time, "sleep"):
            refuses(emit.ChildStatusUnavailable,
                    lambda: emit._fixture_drain(123, deadline=101))
    elif mode == "deadline-flips":
        real_run = emit.run_bounded
        def late(thunk, **kwargs):
            return real_run(thunk, **dict(kwargs, timeout_s=2))
        with patch.object(emit, "run_bounded", late):
            assert _watchdog_deadline_case("pipe-stall") == EXIT_FINDING
        import time
        with patch.object(emit, "_fixture_cleanup_deadline",
                          side_effect=lambda deadline=None: time.monotonic() - 1):
            assert _watchdog_deadline_case("pipe-stall") == EXIT_FINDING
        real_signal, first = emit._fixture_signal, [True]
        def census_only(*args, **kwargs):
            if first[0]:
                first[0] = False
                return False
            return real_signal(*args, **kwargs)
        with patch.object(emit, "_fixture_signal", census_only):
            assert _watchdog_deadline_case("transient-census") == EXIT_FINDING
    elif mode == "subject-setup":
        real_subreaper, real_setsid = emit._fixture_subreaper, os.setsid
        in_guardian = [False]
        def mark():
            real_subreaper()
            in_guardian[0] = True
        def fail_subject():
            if in_guardian[0]:
                raise OSError(5, "QA15-SUBJECT")
            return real_setsid()

        def check():
            with tempfile.TemporaryFile() as err:
                saved = os.dup(2)
                try:
                    os.dup2(err.fileno(), 2)
                    with patch.object(emit, "_fixture_subreaper", mark), \
                            patch.object(os, "setsid", fail_subject):
                        assert emit.run_bounded(lambda: "OK", timeout_s=2) == "CHILD-DIED"
                finally:
                    os.dup2(saved, 2)
                    os.close(saved)
                err.seek(0)
                diagnostic = err.read()
                assert b"Traceback" in diagnostic and b"OSError: [Errno 5] QA15-SUBJECT" in diagnostic
        check()
        import traceback
        with patch.object(traceback, "print_exc"):
            refuses(AssertionError, check)
    elif mode == "empty-children":
        real_read, real_drain = Path.read_text, emit._fixture_drain

        def empty(path, *args, **kwargs):
            if str(path).endswith("/children"):
                return ""
            return real_read(path, *args, **kwargs)

        first = [True]
        def old_census(subject, subject_fd=None, **kwargs):
            if first[0]:
                first[0] = False
                children = Path("/proc/self/task/{}/children".format(os.getpid()))
                if not children.read_text(encoding="ascii").split():
                    raise emit.ChildStatusUnavailable("child census disagrees with waitid")
            return real_drain(subject, subject_fd, **kwargs)

        with patch.object(Path, "read_text", empty):
            # Flip: the old immediate-fatal census rejects this exact stimulus.
            with patch.object(emit, "_fixture_drain", old_census):
                refuses(emit.ChildStatusUnavailable, launch)
            assert launch().returncode == 0
        # A transiently empty PPID census also needs a retry, never a clean pass.
        first, real_children = [True], emit._fixture_children
        def transient():
            if first[0]:
                first[0] = False
                return []
            return real_children()
        with patch.object(emit, "_fixture_children", transient):
            assert launch().returncode == 0
    elif mode == "audit-ignore":
        # A hostile os.fork audit hook can no longer flip SIGCHLD from the fork
        # itself: the fork runs on the private launcher thread, where CPython
        # refuses signal.signal outright (the old hijack is dead by construction).
        # The surviving window is the caller (main) thread between the
        # precondition check and GO: the hook arms on os.fork and the flip lands
        # at the READY read, where the launch-window re-check must refuse it.
        events = []

        def ignore(event, args):
            if event == "os.fork":
                events.append(event)

        sys.addaudithook(ignore)
        real_read = os.read

        def hijack(fd, size):
            if events and signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL:
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            return real_read(fd, size)

        with patch.object(os, "read", hijack):
            refuses(emit.ChildStatusUnavailable, lambda: launch("import os; os._exit(37)"))
        assert events and signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN
    elif mode == "reaper":
        real_wait = emit._fixture_wait
        for code in ("return 0", "import os; os._exit(37)"):
            stolen = []

            def compete(pid, flags):
                if not stolen:
                    worker = threading.Thread(target=lambda: stolen.append(os.waitpid(pid, 0)))
                    worker.start()
                    worker.join(5)
                    assert not worker.is_alive(), "reaper fixture exceeded its bound"
                return real_wait(pid, flags)

            with patch.object(emit, "_fixture_wait", compete):
                refuses(emit.ChildStatusUnavailable, lambda: launch(code))
            assert len(stolen) == 1 and os.waitstatus_to_exitcode(stolen[0][1]) == 0
            assert signal.getsignal(signal.SIGCHLD) == signal.SIG_DFL
    elif mode in ("nonce", "fixture-id", "status"):
        real_run = emit._run_fixture_process
        seen = []

        def alter(argv, **kwargs):
            payload = json.loads(argv[-1])
            seen.append(payload[0])
            if mode == "status":
                record = "\nOPF-FIXTURE " + json.dumps(payload[:2]) + "\n"
                argv = [*command[:4], "import sys; sys.stdout.write(" + repr(record)
                        + "); sys.stdout.flush(); raise SystemExit(37)"]
            else:
                payload[0 if mode == "nonce" else 1] += "-wrong"
                argv = [*argv[:-1], json.dumps(payload)]
            return real_run(argv, **kwargs)

        with patch.object(emit, "_run_fixture_process", alter):
            for _ in range(2):
                refuses(subprocess.CalledProcessError if mode == "status" else emit.FixtureIncomplete,
                        launch)
        assert len(set(seen)) == 2, "each launch needs a fresh nonce"
    elif mode == "early-exit":
        real_run = emit._run_fixture_process
        with tempfile.TemporaryDirectory(prefix="opf-site-exit-") as directory:
            Path(directory, "sitecustomize.py").write_text(
                "import os\nprint('EARLY', flush=True)\nos._exit(0)\n", encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=directory)
            result = launch("print('RAN'); return 0", env=env)
            assert result.stdout == b"RAN\n"

            def unisolated(argv, **kwargs):
                return real_run([arg for arg in argv if arg != "-I"], **kwargs)

            # Removing isolation really reaches sitecustomize; the missing completion
            # must still refuse its zero exit. This also discriminates the record check.
            with patch.object(emit, "_run_fixture_process", unisolated):
                refuses(emit.FixtureIncomplete, lambda: launch(env=env))
        refuses(emit.FixtureIncomplete, lambda: launch("import os; os._exit(0)"))
    elif mode == "premature-exit":
        launch("return 0")  # normal-return control
        for code in ("raise SystemExit()", "raise SystemExit(0)",
                     "raise RuntimeError('before postconditions')"):
            refuses(emit.FixtureIncomplete, lambda: launch(code))
        # CLI exit-status subjects have an explicit, separately supervised path.
        launch("raise SystemExit(2)", process_fixture=True, expected_returncode=2)
    elif mode in ("nested-timeout", "nested-cancel"):
        import time
        with tempfile.TemporaryDirectory(prefix="opf-tree-") as directory:
            markers = [Path(directory, name) for name in ("subject", "descendant")]
            # Deliberate fork/session escape is a tree-cleanup stimulus, not a verdict.
            subject = ("import os, time; from pathlib import Path; "
                       "pid = os.fork(); "
                       "os.setsid() if pid == 0 else None; "
                       "Path(" + repr(directory) + ", "
                       "'descendant' if pid == 0 else 'subject').write_text(str(os.getpid())); "
                       "time.sleep(60)")
            real_wait = emit._fixture_wait
            observed = []

            def observe(pid, flags):
                if all(path.exists() for path in markers) and not observed:
                    observed.extend(int(path.read_text()) for path in markers)
                    if mode == "nested-cancel":
                        raise RuntimeError("cancel with nested subject running")
                return real_wait(pid, flags)

            with patch.object(emit, "_fixture_wait", observe):
                refuses(subprocess.TimeoutExpired if mode == "nested-timeout" else RuntimeError,
                        lambda: emit.run_status_owned(
                            [*command[:4], subject], fixture_id="tree/" + mode,
                            process_fixture=True, timeout=2))
            assert len(observed) == 2, "nested subject never started"
            for pid in observed:
                assert not Path("/proc", str(pid)).exists(), "descendant survived/unreaped"
    elif mode == "no-signal-echild":
        import time
        # The immediately-exiting subject leaves the exited guardian as the raw
        # zombie stimulus for the ownership boundary.
        child = emit._FixtureProcess(time.monotonic() + 5, subject=lambda: None)
        pid = child.start()
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        # Positive control: an owned leader permits signalling.
        with patch.object(os, "kill") as kill:
            assert emit._fixture_signal(pid, signal.SIGKILL, group=False)
            assert kill.called
        os.waitpid(pid, 0)
        child.collected = True  # deliberate external collection of the guardian
        child.close()
        # Negative control covers numeric, group AND pidfd paths after real ECHILD.
        with patch.object(os, "getpgid", return_value=pid), \
                patch.object(os, "kill") as kill, patch.object(os, "killpg") as killpg, \
                patch.object(signal, "pidfd_send_signal") as pidfd_signal:
            assert emit._fixture_signal(pid, signal.SIGKILL) is False
            assert emit._fixture_signal(pid, signal.SIGKILL, 123) is False
            assert not kill.called and not killpg.called and not pidfd_signal.called
    elif mode == "cleanup-cancel":
        real_wait = emit._fixture_wait
        seen = []

        def cancel(pid, flags):
            if not seen:
                seen.append(pid)
                raise RuntimeError("cancel collection")
            return real_wait(pid, flags)

        with patch.object(emit, "_fixture_wait", cancel):
            refuses(RuntimeError, lambda: launch("import time; time.sleep(60)"))
        pid = seen[0]
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            waited = None
        assert waited is None, "cancelled collector abandoned its fixture"
    elif mode == "cleanup-reaped":
        import time
        child = emit._FixtureProcess(time.monotonic() + 5, subject=lambda: None)
        pid = child.start()                   # deliberate zombie stimulus, not a test verdict
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
        with patch.object(os, "kill") as kill:
            assert emit._fixture_child_reaped(pid) is False
        child.collected = True  # the assertion above deliberately consumed its status
        child.close()
        assert not kill.called, "cleanup signalled a PID after its probe reaped it"
        assert emit._fixture_child_reaped(pid) is True
        with patch.object(os, "waitpid", side_effect=OSError(5, "fixture EIO")), \
                patch.object(os, "kill") as kill:
            refuses(emit.ChildStatusUnavailable, lambda: emit._fixture_child_reaped(pid))
        assert not kill.called
    elif mode == "overdue-success":
        import time
        # QA16 F1: an exit first observed after the deadline is TIMEOUT, whatever
        # order the guardian and the collector were scheduled in. The guardian
        # stops itself before supervision; the collector is gated before its
        # first poll; the subject finishes only after the recorded deadline has
        # decisively expired. Barriers order every step: no wall-clock race.
        caller = os.getpid()
        with tempfile.TemporaryDirectory(prefix="opf-overdue-") as directory:
            release = Path(directory, "release")
            guardian_file = Path(directory, "guardian")
            recorded = []
            real_init = emit._FixtureProcess.__init__

            def record_init(child, deadline, **kwargs):
                recorded.append(deadline)
                return real_init(child, deadline, **kwargs)

            real_ack = emit._fixture_ack_subject

            def stop_guardian(fd):
                # The subject is RELEASED (receipt sent, ownership acknowledged)
                # before the guardian stops, so it can complete while the guardian
                # is not supervising.
                real_ack(fd)
                if os.getpid() != caller:
                    scratch = Path(directory, "guardian.tmp")
                    scratch.write_text(str(os.getpid()), encoding="ascii")
                    scratch.rename(guardian_file)  # atomic: never a partial PID
                    os.kill(os.getpid(), signal.SIGSTOP)

            gate = threading.Event()
            real_poll = emit._FixtureProcess.poll

            def gated_poll(child):
                if os.getpid() == caller and not gate.is_set():
                    assert gate.wait(30), "collector gate was never released"
                return real_poll(child)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            body = ("import os, time\n"
                    "bound = time.monotonic() + 30\n"
                    "while not os.path.exists(" + repr(str(release)) + "):\n"
                    "    if time.monotonic() >= bound:\n"
                    "        return 1\n"
                    "    time.sleep(0.005)\n"
                    "return 0")
            outcome = []

            def run():
                try:
                    outcome.append(("returned", emit.run_status_owned(
                        [*command[:4], body], fixture_id="completion/" + mode, timeout=1)))
                except BaseException as exc:  # noqa: BLE001 - the verdict channel
                    outcome.append(("raised", exc))

            worker = threading.Thread(target=run, daemon=True)
            with patch.object(emit._FixtureProcess, "__init__", record_init), \
                    patch.object(emit._FixtureProcess, "poll", gated_poll), \
                    patch.object(emit, "_fixture_ack_subject", stop_guardian):
                worker.start()
                try:
                    bound = time.monotonic() + 30
                    while not guardian_file.exists():
                        assert time.monotonic() < bound, "guardian never reached its stop point"
                        time.sleep(0.005)
                    gpid = int(guardian_file.read_text(encoding="ascii"))
                    await_state(gpid, {"T"}, "guardian did not stop")
                    assert recorded, "the fixture deadline was not observed"
                    while time.monotonic() < recorded[0] + 0.2:
                        time.sleep(0.005)
                    release.write_text("go", encoding="ascii")
                    children = Path("/proc", str(gpid), "task", str(gpid), "children")
                    subject = int(children.read_text(encoding="ascii").split()[0])
                    await_state(subject, {"Z"}, "subject did not finish after release")
                    os.kill(gpid, signal.SIGCONT)
                    await_state(gpid, {"Z", None}, "guardian did not exit after resume")
                finally:
                    gate.set()
                worker.join(30)
            assert not worker.is_alive(), "the collector never returned"
            assert outcome and outcome[0][0] == "raised" and isinstance(
                outcome[0][1], subprocess.TimeoutExpired), (
                "an overdue completion was accepted as success: " + repr(outcome))
    elif mode == "launch-ownership":
        import time
        # QA16 F2 / QA17 F3: fork-to-ownership is uninterruptible by interpreter
        # construction -- the fork runs on a private launcher thread, where CPython
        # never raises asynchronous signal exceptions -- so a cancellation delivered
        # at ANY caller-side bytecode still reaps the guardian to ECHILD. The old
        # single-statement packing was broken between CALL and STORE_ATTR by a real
        # SIGINT; no statement shape can close that window, only thread ownership.
        real_fork = os.fork
        fork_threads = []

        def observing_fork():
            fork_threads.append(threading.current_thread() is threading.main_thread())
            return real_fork()

        injected = []

        def interrupt(frame, event, arg):
            if event != "call" or frame.f_code.co_name != "_start":
                return None

            def local(frame, event, arg):
                if event == "line" and not injected:
                    target = frame.f_locals.get("self")
                    # Once the launcher has recorded the fork result, the next
                    # caller bytecode is exactly where a real SIGINT would land.
                    if target is not None and target.pid is not None:
                        injected.append(target.pid)
                        raise KeyboardInterrupt("cancel after launch, before READY")
                return local

            return local

        child = emit._FixtureProcess(time.monotonic() + 30, subject=lambda: None)
        with patch.object(os, "fork", observing_fork):
            sys.settrace(interrupt)
            try:
                refuses(KeyboardInterrupt, child.start)
            finally:
                sys.settrace(None)
        assert fork_threads == [False], "the fork did not run on a private launcher thread"
        assert injected, "the launch window was never reached"
        gpid = injected[0]
        assert emit._fixture_child_reaped(gpid) is True, "cancellation leaked the guardian"

        # Flip: run the fork INLINE on the caller thread (bypassing the parked
        # launcher) and the old interruptible fork-to-store window comes back --
        # a cancellation between the fork and its store leaks a guardian that
        # close() can never know.
        forked = []

        def inline_interrupt(frame, event, arg):
            if event != "call" or frame.f_code.co_name != "_launch_fork":
                return None

            def local(frame, event, arg):
                if event == "line" and not forked:
                    pid = frame.f_locals.get("pid")
                    if pid and frame.f_locals["self"].pid is None:
                        forked.append(pid)
                        raise KeyboardInterrupt("cancel between fork and its store")
                return local

            return local

        flip_child = emit._FixtureProcess(time.monotonic() + 30, subject=lambda: None)
        sys.settrace(inline_interrupt)
        try:
            refuses(KeyboardInterrupt, flip_child._launch_fork)
        finally:
            sys.settrace(None)
        assert forked, "the inline flip never reached the fork boundary"
        leaked_pid = forked[0]
        assert flip_child.pid is None, "the inline flip still recorded ownership"
        leaked = True
        try:
            os.waitid(os.P_PID, leaked_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            leaked = False
        if leaked:
            os.kill(leaked_pid, signal.SIGKILL)  # hygiene for the demonstrated leak
            os.waitpid(leaked_pid, 0)
        assert leaked, "the inline flip did not reproduce the interruptible window"
        flip_child.close()
    elif mode == "launch-cancel":
        import time
        # QA18 codex F1: a cancellation delivered while the launcher thread is
        # being CREATED (inside threading.Thread.start, before its bootstrap runs)
        # must neither be replaced by a cleanup RuntimeError, nor leak the control
        # sockets, nor leak a guardian. The launcher is parked at construction and
        # released by flags, so close() never joins an unstarted thread, and a
        # cancelled construction aborts the launch and re-raises the cancellation.
        real_start = threading.Thread.start

        def open_fds():
            live = set()
            for name in os.listdir("/proc/self/fd"):
                try:
                    os.fstat(int(name))
                except OSError:
                    continue
                live.add(int(name))
            return live

        def no_children():
            try:
                os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                return True
            return False

        def interrupted_start(thread):
            if thread.name == "opf-fixture-launcher":
                # A REAL SIGINT: the pending KeyboardInterrupt materializes on the
                # next main-thread bytecode, at launcher-creation time.
                signal.raise_signal(signal.SIGINT)
            return real_start(thread)

        def cancelled_run():
            # The caught exception is RETURNED (its traceback pins the half-built
            # fixture), so descriptor parity below proves the machinery itself
            # released everything, not a later refcount finalizer.
            try:
                emit.run_bounded(lambda: "UNREACHED", timeout_s=5)
            except KeyboardInterrupt as exc:
                return exc
            except BaseException as exc:  # noqa: BLE001 - the verdict channel
                raise AssertionError("the cancellation was replaced: " + repr(exc))
            raise AssertionError("the cancellation was swallowed")

        assert no_children(), "children live before the launch-cancel probe"
        # Prime the one-time guardian-dependency preload (a first ctypes import
        # keeps a libffi mapping descriptor open for the process's life) so the
        # descriptor parity below measures ONLY what a cancelled launch leaks.
        emit._fixture_preload()
        before = open_fds()
        with patch.object(threading.Thread, "start", interrupted_start):
            held = cancelled_run()
        assert open_fds() == before, "a cancelled launch leaked descriptors"
        assert no_children(), "a cancelled launch leaked a child"
        held = None

        # Thread exhaustion is the SETUP-ERROR sentinel (the launcher analog of
        # the pinned fork-error discipline), never a raw cleanup RuntimeError.
        def exhausted_start(thread):
            if thread.name == "opf-fixture-launcher":
                raise RuntimeError("can't start new thread")
            return real_start(thread)

        with patch.object(threading.Thread, "start", exhausted_start):
            result = emit.run_bounded(lambda: "UNREACHED", timeout_s=5)
        assert result == "SETUP-ERROR:RuntimeError:can't start new thread", result
        assert open_fds() == before and no_children(), "thread exhaustion leaked"

        # Flip: without the construction abort, the cancellation leaks this
        # call's control sockets and report.
        with patch.object(emit, "_fixture_abort_launch", lambda fixture: None):
            with patch.object(threading.Thread, "start", interrupted_start):
                held = cancelled_run()
        leaked = sorted(open_fds() - before)
        assert leaked, "the flip did not reproduce the construction leak"
        held = None  # hygiene: releasing the fixture reclaims the demonstrated leak
        assert open_fds() == before, "the flip hygiene did not complete"
    elif mode == "fd-hygiene-total":
        import errno
        import time
        # QA16 MINOR-1 + QA17 F4/MINOR-2: EVERY undeclared caller descriptor is
        # closed before a subject can run -- a bare pipe, a plain open file, an
        # UNSTARTED sibling call's endpoints (the construction window: there is no
        # publication step left to race), and a started sibling's -- while this
        # call's declared keep_fds survive and stay usable. Identity, not mere
        # openness: a swept number may be legitimately reused inside the subject.
        bare_r, bare_w = os.pipe()
        keep_r, keep_w = os.pipe()
        plain = tempfile.TemporaryFile()
        unstarted = emit._FixtureProcess(time.monotonic() + 30, subject=lambda: None)
        started = emit._FixtureProcess(time.monotonic() + 30,
                                       subject=lambda: time.sleep(60))
        started.start()
        undeclared = dict((("bare-r", bare_r), ("bare-w", bare_w),
                           ("plain-file", plain.fileno()),
                           ("unstarted-control", unstarted.control.fileno()),
                           ("unstarted-peer", unstarted.peer.fileno()),
                           ("unstarted-report", unstarted.report.fileno()),
                           ("started-control", started.control.fileno()),
                           ("started-report", started.report.fileno())))
        identity = dict((name, (os.fstat(fd).st_dev, os.fstat(fd).st_ino))
                        for name, fd in undeclared.items())
        try:
            def probe():
                leaked = []
                for name, fd in sorted(undeclared.items()):
                    try:
                        stat = os.fstat(fd)
                    except OSError as exc:
                        if exc.errno != errno.EBADF:
                            raise
                        continue
                    if (stat.st_dev, stat.st_ino) == identity[name]:
                        leaked.append(name)
                if leaked:
                    return "LEAKED:" + " ".join(leaked)
                # Positive control: the declared keep_fds are still usable.
                os.write(keep_w, b"K")
                return "CLEAN" if os.read(keep_r, 1) == b"K" else "KEPT-PIPE-BROKEN"

            result = emit.run_bounded(probe, timeout_s=10, keep_fds=(keep_r, keep_w))
            assert result == "CLEAN", result
            # The sweep is guardian-side only: the caller's descriptors stay open.
            os.write(bare_w, b"S")
            assert os.read(bare_r, 1) == b"S", "the bare pipe was closed in the caller"
            # Flip: without the close-all sweep every undeclared descriptor leaks.
            with patch.object(emit, "_fixture_close_all_except", lambda keep: None):
                flipped = emit.run_bounded(probe, timeout_s=10, keep_fds=(keep_r, keep_w))
            assert flipped.startswith("LEAKED:"), flipped
            for name in undeclared:
                assert name in flipped, (name, flipped)
        finally:
            started.close()
            unstarted.close()
            plain.close()
            for fd in (bare_r, bare_w, keep_r, keep_w):
                os.close(fd)
    elif mode == "nested-keep":
        import time
        # QA17 F2, migrated to the explicit-contract refusal: a caller descriptor a
        # NESTED run_bounded thunk relies on is a deterministic ERROR:OSError unless
        # declared through keep_fds, in which case it works -- never a
        # sometimes-working reuse of a stale registry entry.
        with tempfile.TemporaryDirectory(prefix="opf-nested-keep-") as directory:
            probe_path = Path(directory, "probe")
            probe_path.write_text("x", encoding="utf-8")

            def outer(declared):
                fd = os.open(str(probe_path), os.O_RDONLY)
                try:
                    def inner():
                        os.fstat(fd)
                        return "OPEN"
                    return emit.run_bounded(inner, timeout_s=10,
                                            keep_fds=(fd,) if declared else ())
                finally:
                    os.close(fd)

            undeclared = emit.run_bounded(lambda: outer(False), timeout_s=30)
            assert undeclared == "ERROR:OSError", undeclared
            declared = emit.run_bounded(lambda: outer(True), timeout_s=30)
            assert declared == "OPEN", declared
            # Flip: no-op the sweep and the undeclared descriptor leaks into the
            # nested subject.
            with patch.object(emit, "_fixture_close_all_except", lambda keep: None):
                leaked = emit.run_bounded(lambda: outer(False), timeout_s=30)
            assert leaked == "OPEN", leaked
    elif mode == "fd-census":
        import errno
        caller = os.getpid()
        # QA18 codex F4: without an authoritative /proc/self/fd census the sweep
        # cannot be proven complete (a soft-RLIMIT bound does not enumerate
        # descriptors already open above it), so the guardian must REFUSE startup,
        # never run the subject behind a partial sweep.
        spare = os.open(os.devnull, os.O_RDONLY)
        try:
            identity = (os.fstat(spare).st_dev, os.fstat(spare).st_ino)

            def probe():
                try:
                    stat = os.fstat(spare)
                except OSError:
                    return "SWEPT"
                return "LEAKED" if (stat.st_dev, stat.st_ino) == identity else "SWEPT"

            real_listdir = os.listdir

            def denied(path="."):
                # Guardian-side only: the caller's own censuses stay usable.
                if os.getpid() != caller and str(path) == "/proc/self/fd":
                    raise PermissionError(errno.EACCES, "injected census denial")
                return real_listdir(path)

            with patch.object(os, "listdir", denied):
                result = emit.run_bounded(probe, timeout_s=10)
                assert result.startswith("SETUP-ERROR:ChildStatusUnavailable:"), result
                assert "descriptor census unavailable" in result, result
                # Flip: sweeping past the failed census runs the subject with the
                # caller's descriptors intact.
                with patch.object(emit, "_fixture_close_all_except", lambda keep: None):
                    flipped = emit.run_bounded(probe, timeout_s=10)
                assert flipped == "LEAKED", flipped
            # Positive control: with the census available the same undeclared
            # descriptor never reaches the subject.
            assert emit.run_bounded(probe, timeout_s=10) == "SWEPT"
        finally:
            os.close(spare)
    elif mode == "pdeathsig":
        # D2: the subject arms PR_SET_PDEATHSIG(SIGKILL) before any subject code
        # runs (partial coverage for the wedged-guardian residual; the behavioral
        # leg rides the subject-receipt case). PR_GET_PDEATHSIG reads it back.
        def query():
            import ctypes
            value = ctypes.c_int(-1)
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(2, ctypes.byref(value), 0, 0, 0) != 0:  # PR_GET_PDEATHSIG
                return "PRCTL-ERROR"
            return str(value.value)

        assert emit.run_bounded(query, timeout_s=10) == str(int(signal.SIGKILL))
        # Flip: without the arm the subject reports no parent-death signal (the
        # flag is cleared on fork, so an inherited value can never mask the flip).
        with patch.object(emit, "_fixture_pdeathsig", lambda: None):
            assert emit.run_bounded(query, timeout_s=10) == "0"
    elif mode == "subject-gc":
        # QA18 gemini F3: the guardian disables gc for its own forked-heap safety,
        # but the SUBJECT runs arbitrary test code and must get the collector back
        # before its callable runs, or every cycle it builds leaks for the
        # callable's whole life.
        def probe():
            import gc
            return "GC-ON" if gc.isenabled() else "GC-OFF"

        assert emit.run_bounded(probe, timeout_s=10) == "GC-ON"
        # Flip: without the re-enable the subject inherits the disabled collector.
        with patch.object(emit, "_fixture_enable_gc", lambda: None):
            assert emit.run_bounded(probe, timeout_s=10) == "GC-OFF"
    elif mode == "guardian-preload":
        import time
        # QA18 codex F5: the guardian forks from the launcher thread, so a cold
        # guardian-side import could block on an import or module lock some other
        # caller thread held at fork time. Construction preloads the whole guardian
        # dependency set, making every guardian-side import a lock-free
        # sys.modules hit; the documented restriction that remains is thunk-side.
        # QA19 F4: socket.send_fds/recv_fds cold-import array inside their own
        # bodies (importing socket alone does not load it), so the receipt path
        # needs array preloaded too.
        def pop_cold():
            popped = dict()
            for name in list(sys.modules):
                if name in ("ctypes", "array") or name.startswith("ctypes."):
                    popped[name] = sys.modules.pop(name)
            return popped

        popped = pop_cold()
        try:
            child = emit._FixtureProcess(time.monotonic() + 5, subject=lambda: None)
            try:
                missing = [name for name in emit._FIXTURE_GUARDIAN_MODULES
                           if name not in sys.modules]
                assert not missing, missing
                assert "array" in sys.modules, \
                    "the receipt path's send_fds/recv_fds array import stayed cold"
            finally:
                child.close()
            # Flip: without the preload the guardian dependency set stays cold.
            pop_cold()
            with patch.object(emit, "_fixture_preload", lambda: None):
                flip_child = emit._FixtureProcess(time.monotonic() + 5,
                                                  subject=lambda: None)
                try:
                    assert "ctypes" not in sys.modules, "the flip still preloaded"
                    assert "array" not in sys.modules, "the flip still preloaded array"
                finally:
                    flip_child.close()
        finally:
            for name, module in popped.items():
                sys.modules.setdefault(name, module)
    elif mode == "subject-receipt":
        import time
        caller = os.getpid()
        # The receipt (subject pid + pidfd) is sent between the subject fork and
        # supervision and stays BUFFERED on the control socket: the caller collects
        # it even after the guardian's death, so escalation always knows the
        # subject. The subject's PR_SET_PDEATHSIG(SIGKILL) partial coverage is
        # observed behaviorally on the same stimulus (D2).
        with tempfile.TemporaryDirectory(prefix="opf-receipt-") as directory:
            guardian_file = Path(directory, "guardian")
            real_send = emit._fixture_send_subject

            def wedge(peer, subject, subject_fd, send=True):
                if send:
                    real_send(peer, subject, subject_fd)
                if os.getpid() != caller:
                    scratch = Path(directory, "guardian.tmp")
                    scratch.write_text(str(os.getpid()), encoding="ascii")
                    scratch.rename(guardian_file)  # atomic: never a partial PID
                    os.kill(os.getpid(), signal.SIGSTOP)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def launch_wedged(hook):
                guardian_file.unlink(missing_ok=True)
                child = emit._FixtureProcess(time.monotonic() + 3600,
                                             subject=lambda: time.sleep(3600))
                with patch.object(emit, "_fixture_send_subject", hook):
                    child.start()
                bound = time.monotonic() + 30
                while not guardian_file.exists():
                    assert time.monotonic() < bound, "the guardian never reached its stop point"
                    time.sleep(0.005)
                gpid = int(guardian_file.read_text(encoding="ascii"))
                assert gpid == child.pid, "the hook stopped an unexpected process"
                await_state(gpid, ("T",), "the guardian did not stop")
                subject = int(Path("/proc", str(gpid), "task", str(gpid), "children")
                              .read_text(encoding="ascii").split()[0])
                return child, subject

            # Leg 1: guardian SIGKILLed while wedged -> the buffered receipt still
            # arrives, and the subject dies without running its callable (pdeathsig,
            # and independently the acknowledgment EOF refusal).
            child, subject = launch_wedged(wedge)
            os.kill(child.pid, signal.SIGKILL)
            failures = []
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    failures.append(str(exc))
            assert child.subject_pid == subject, (child.subject_pid, subject)
            assert failures, "a SIGKILLed guardian was read as clean"
            # A dead orphan reparents to the nearest subreaper ancestor (the
            # outer guardian, under the regression runner) and lingers as a
            # zombie until that ancestor's drain: Z is dead, not surviving.
            await_state(subject, (None, "Z"), "pdeathsig did not kill the orphaned subject")

            # Flip: skip the send -> escalation degrades to guardian-only and the
            # refusal must say the subject cleanup is INCOMPLETE, never claim a
            # killed tree. The unacknowledged subject (pdeathsig coverage also
            # removed) still refuses by ITSELF on the acknowledgment EOF once the
            # escalation SIGKILLs the wedged guardian: no manual hygiene remains.
            def skip_send(peer, subject, subject_fd):
                wedge(peer, subject, subject_fd, send=False)

            with patch.object(emit, "_fixture_pdeathsig", lambda: None):
                child, subject = launch_wedged(skip_send)
            failures = []
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    failures.append(str(exc))
            assert child.subject_pid is None, "a skipped send still delivered a receipt"
            assert failures and "escalat" in failures[0], failures
            assert "incomplete" in failures[0].lower(), failures
            await_state(subject, (None, "Z"),
                        "the unacknowledged subject outlived its guardian")
    elif mode == "subject-ack":
        import time
        caller = os.getpid()
        # QA18 codex F2: the subject may not run user code (so cannot create
        # descendants) before the guardian has delivered its receipt and
        # acknowledged ownership; a guardian that dies before the acknowledgment
        # must yield a subject that refuses to run, even without pdeathsig.
        with tempfile.TemporaryDirectory(prefix="opf-ack-") as directory:
            guardian_file = Path(directory, "guardian")
            marker = Path(directory, "marker")
            real_ack = emit._fixture_ack_subject

            def wedge_ack(fd):
                # Wedge AFTER the receipt send, BEFORE the acknowledgment.
                if os.getpid() != caller:
                    scratch = Path(directory, "guardian.tmp")
                    scratch.write_text(str(os.getpid()), encoding="ascii")
                    scratch.rename(guardian_file)  # atomic: never a partial PID
                    os.kill(os.getpid(), signal.SIGSTOP)
                real_ack(fd)

            def subject_body():
                marker.write_text("ran", encoding="ascii")
                import time as clock
                clock.sleep(3600)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def launch_wedged():
                guardian_file.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                child = emit._FixtureProcess(time.monotonic() + 3600,
                                             subject=subject_body)
                with patch.object(emit, "_fixture_ack_subject", wedge_ack):
                    child.start()
                bound = time.monotonic() + 30
                while not guardian_file.exists():
                    assert time.monotonic() < bound, "the guardian never stopped"
                    time.sleep(0.005)
                gpid = int(guardian_file.read_text(encoding="ascii"))
                assert gpid == child.pid, "the hook stopped an unexpected process"
                await_state(gpid, ("T",), "the guardian did not stop")
                subject = int(Path("/proc", str(gpid), "task", str(gpid), "children")
                              .read_text(encoding="ascii").split()[0])
                return child, subject

            # An unacknowledged subject refuses on guardian death by ITSELF:
            # pdeathsig is removed so only the acknowledgment EOF can end it.
            with patch.object(emit, "_fixture_pdeathsig", lambda: None):
                child, subject = launch_wedged()
                os.kill(child.pid, signal.SIGKILL)
                await_state(subject, (None, "Z"),
                            "an unacknowledged subject survived its guardian")
                assert not marker.exists(), "the subject ran before the ack"
                failures = []
                with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))
                assert child.subject_pid == subject, (child.subject_pid, subject)
                assert failures, "a SIGKILLed guardian was read as clean"

            # Flip: bypass the subject-side gate and the pre-receipt window is
            # back: the subject runs its callable while the guardian is wedged
            # short of the acknowledgment.
            with patch.object(emit, "_fixture_pdeathsig", lambda: None), \
                    patch.object(emit, "_fixture_await_ack", lambda fd: None):
                child, subject = launch_wedged()
                bound = time.monotonic() + 30
                while not marker.exists():
                    assert time.monotonic() < bound, "the flip subject never ran"
                    time.sleep(0.005)
                os.kill(child.pid, signal.SIGKILL)
                failures = []
                with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))
                assert failures, "the flip close was read as clean"
                await_state(subject, (None, "Z"), "the flip cleanup did not complete")
    elif mode == "subject-orphan":
        import time
        # QA18 codex F3: pdeathsig is not retroactive, so a subject first scheduled
        # after its guardian died arms too late; it must detect the reparenting and
        # refuse to run. Independently, close() must kill and prove a
        # receipt-identified subject on ANY guardian failure, not only after an
        # escalation. The delayed schedule is modeled as a file-gated wait, not a
        # SIGSTOP: a pre-setsid subject stopped in the dying guardian's group would
        # get the kernel's orphaned-group SIGHUP+SIGCONT, an environment-dependent
        # coincidence (hosts ignoring SIGHUP keep the orphan alive) that this
        # regression must not depend on.
        with tempfile.TemporaryDirectory(prefix="opf-orphan-") as directory:
            waiting = Path(directory, "waiting")
            release = Path(directory, "release")
            marker = Path(directory, "marker")
            real_pdeathsig = emit._fixture_pdeathsig

            def hold(arm):
                # Subject-side: publish the pid, wait for the release gate BEFORE
                # the (optional) arm, arm only after the gate opens.
                import time as clock
                scratch = Path(directory, "waiting.tmp")
                scratch.write_text(str(os.getpid()), encoding="ascii")
                scratch.rename(waiting)  # atomic: never a partial PID
                bound = clock.monotonic() + 120
                while not release.exists():
                    if clock.monotonic() >= bound:
                        os._exit(96)  # the gate never opened: fail loudly
                    clock.sleep(0.005)
                if arm:
                    real_pdeathsig()

            def subject_body():
                marker.write_text("ran", encoding="ascii")
                import time as clock
                clock.sleep(3600)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def launch_held(arm=True):
                import select
                waiting.unlink(missing_ok=True)
                release.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                child = emit._FixtureProcess(time.monotonic() + 3600,
                                             subject=subject_body)
                with patch.object(emit, "_fixture_pdeathsig", lambda: hold(arm)):
                    child.start()
                bound = time.monotonic() + 30
                while not waiting.exists():
                    assert time.monotonic() < bound, "the subject never reached its gate"
                    time.sleep(0.005)
                subject = int(waiting.read_text(encoding="ascii"))
                # The gated subject publishes its pid BEFORE the guardian sends the
                # receipt: wait for the receipt to be buffered on the control socket
                # before any leg kills the guardian, so every leg exercises a
                # DELIVERED receipt (the no-receipt path is subject-receipt's leg).
                assert select.select([child.control], [], [], 30)[0], \
                    "the subject receipt never arrived"
                return child, subject

            def cancel(child, failures):
                with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))

            # Leg 1: the guardian dies while the subject is still gated pre-arm;
            # the released subject arms, sees a changed parent, and refuses.
            child, subject = launch_held()
            os.kill(child.pid, signal.SIGKILL)
            await_state(child.pid, ("Z",), "the killed guardian did not exit")
            release.write_text("go", encoding="ascii")
            await_state(subject, (None, "Z"), "the orphaned subject kept running")
            assert not marker.exists(), "an orphaned subject ran its callable"
            failures = []
            cancel(child, failures)
            assert child.subject_pid == subject, (child.subject_pid, subject)
            assert failures, "a SIGKILLed guardian was read as clean"

            # Leg 2: same stimulus, subject NEVER released: the guardian failure is
            # collected without an escalation, and close() itself must kill the
            # receipt-identified subject and prove its exit.
            child, subject = launch_held()
            os.kill(child.pid, signal.SIGKILL)
            failures = []
            cancel(child, failures)
            assert failures, "a SIGKILLed guardian was read as clean"
            await_state(subject, (None, "Z"),
                        "close() left the live subject on a guardian failure")
            assert not marker.exists(), "the gated subject ran its callable"

            # Flip A: without the parent check (and with pdeathsig disarmed, which
            # a post-death arm cannot help anyway) the released orphan runs its
            # callable: the QA18 codex F3 defect, reproduced.
            with patch.object(emit, "_fixture_check_parent", lambda expected: None):
                child, subject = launch_held(arm=False)
                os.kill(child.pid, signal.SIGKILL)
                await_state(child.pid, ("Z",), "the killed guardian did not exit")
                release.write_text("go", encoding="ascii")
                bound = time.monotonic() + 30
                while not marker.exists():
                    assert time.monotonic() < bound, "the flip orphan never ran"
                    time.sleep(0.005)
                failures = []
                cancel(child, failures)  # close() still kills the receipt subject
                assert failures, "the flip close was read as clean"
                await_state(subject, (None, "Z"), "the flip cleanup did not complete")

            # Flip B: without the close-side subject kill, a live subject survives
            # a guardian failure and close() names the surviving pid.
            child, subject = launch_held()
            os.kill(child.pid, signal.SIGKILL)
            failures = []
            with patch.object(emit, "_fixture_escalate_subject",
                              lambda subject, fd: None):
                cancel(child, failures)
            assert failures and "could not confirm subject exit" in failures[0], failures
            assert state(subject) not in (None, "Z"), \
                "flip: the subject died without its kill"
            os.kill(subject, signal.SIGKILL)  # hygiene for the demonstrated leak
            await_state(subject, (None, "Z"), "the flip hygiene did not complete")
    elif mode == "escalation-subject":
        import time
        # QA16 MINOR-2 + QA17 F1: cancelling a wedged guardian is bounded AND kills
        # and observes the receipt-identified subject tree -- freeze the guardian,
        # then the subject's group and pidfd, then the guardian -- never a stranded
        # sleeper the test has to clean up. The same-group descendant must die with
        # the subject; descendants that LEAVE the group are the documented
        # wedged-guardian residual, out of contract here.
        def subject_tree():
            import time as clock
            if os.fork() == 0:
                clock.sleep(3600)          # same-group descendant
            clock.sleep(3600)              # subject: outlives everything unless cancelled

        def state(target):
            try:
                stat = Path("/proc", str(target), "stat").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                return None
            return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

        def children_of(owner):
            pids = []
            for entry in os.listdir("/proc"):
                if entry.isdecimal():
                    try:
                        stat = Path("/proc", entry, "stat").read_bytes()
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    if int(stat.rsplit(b")", 1)[1].split()[1]) == owner:
                        pids.append(int(entry))
            return pids

        def await_child(owner, note):
            bound = time.monotonic() + 30
            while True:
                assert time.monotonic() < bound, note
                listed = children_of(owner)
                if listed:
                    return listed[0]
                time.sleep(0.005)

        def exercise(flip):
            from contextlib import ExitStack
            with ExitStack() as stack:
                stack.enter_context(patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0))
                if flip:
                    # Flip: no-op the escalation's subject-kill step (and the
                    # subject's pdeathsig partial coverage, which must not rescue
                    # the flip) -> the subject tree must survive and the
                    # disappearance proof must fail.
                    stack.enter_context(patch.object(
                        emit, "_fixture_escalate_subject",
                        lambda subject, fd, **license: None))
                    stack.enter_context(patch.object(
                        emit, "_fixture_pdeathsig", lambda: None))
                child = emit._FixtureProcess(time.monotonic() + 3600, subject=subject_tree)
                pid = child.start()
                subject = await_child(pid, "the guardian subject never appeared")
                descendant = await_child(subject, "the subject descendant never appeared")
                # A stopped guardian models a cleanup that cannot make progress: it
                # can never exit, so an unbounded close() would block until resumed.
                os.kill(pid, signal.SIGSTOP)
                bound = time.monotonic() + 30
                while state(pid) != "T":
                    assert time.monotonic() < bound, "the guardian did not stop"
                    time.sleep(0.005)
                sequence, failures = [], []
                real_pidfd_signal, real_killpg = signal.pidfd_send_signal, os.killpg

                def rec_pidfd(fd, signum, *args):
                    sequence.append(("pidfd", fd, signum))
                    return real_pidfd_signal(fd, signum, *args)

                def rec_killpg(pgid, signum):
                    sequence.append(("killpg", pgid, signum))
                    return real_killpg(pgid, signum)

                def cancel():
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))
                    except BaseException as exc:  # noqa: BLE001 - the verdict channel
                        failures.append("unexpected: " + repr(exc))

                worker = threading.Thread(target=cancel, daemon=True)
                begun = time.monotonic()
                with patch.object(signal, "pidfd_send_signal", rec_pidfd), \
                        patch.object(os, "killpg", rec_killpg):
                    worker.start()
                    worker.join(20)
                    blocked = worker.is_alive()
                    if blocked:
                        os.kill(pid, signal.SIGCONT)  # unwedge the leak before failing
                        worker.join(30)
                elapsed = time.monotonic() - begun
                assert not blocked, "close() blocked past its bounded cleanup budget"
                assert failures and "escalat" in failures[0], failures
                assert emit._fixture_child_reaped(pid) is True
                assert elapsed < 15, elapsed
                return pid, subject, descendant, sequence, failures

        pid, subject, descendant, sequence, failures = exercise(flip=False)
        # Kill order: freeze first, then the subject's group, then the guardian's.
        assert sequence and sequence[0][0] == "pidfd" and sequence[0][2] == signal.SIGSTOP, sequence
        killpg_kills = [(target, index) for index, (op, target, signum) in enumerate(sequence)
                        if op == "killpg" and signum == signal.SIGKILL]
        subject_kills = [index for target, index in killpg_kills if target == subject]
        guardian_kills = [index for target, index in killpg_kills if target == pid]
        assert subject_kills, (sequence, subject)
        assert not guardian_kills or subject_kills[0] < guardian_kills[0], sequence
        # The whole tree is observed dead: no manual sleeper hygiene remains in
        # this test. A dead orphan reparents to the nearest subreaper ancestor
        # (the outer guardian, under the regression runner) and lingers as a
        # zombie until that ancestor's drain: Z is dead, not surviving.
        dead = (None, "Z")
        bound = time.monotonic() + 30
        while state(subject) not in dead or state(descendant) not in dead:
            assert time.monotonic() < bound, (state(subject), state(descendant))
            time.sleep(0.005)
        # Flip: with the subject-kill step removed the subject tree survives and
        # close() itself names the surviving pid.
        pid, subject, descendant, sequence, failures = exercise(flip=True)
        assert state(subject) not in dead, "flip: the subject died without its kill step"
        assert "could not confirm subject exit" in failures[0], failures
        os.killpg(subject, signal.SIGKILL)  # clean up the deliberately-leaked tree
        bound = time.monotonic() + 30
        while state(subject) not in dead or state(descendant) not in dead:
            assert time.monotonic() < bound, "the flip cleanup did not complete"
            time.sleep(0.005)
    elif mode == "escalate-reaped":
        import time
        from contextlib import ExitStack
        caller = os.getpid()
        # QA18 gemini F1: escalation must killpg the receipt-identified subject
        # group even when the guardian already REAPED the subject: the held pidfd
        # pins the pid (and so the pgid) against recycling, and a reaped subject
        # can leave same-group descendants that only the group kill can reach.
        with tempfile.TemporaryDirectory(prefix="opf-reaped-") as directory:
            wedged = Path(directory, "wedged")
            grandchild_file = Path(directory, "grandchild")

            def subject_body():
                # Fork a same-group descendant, then exit: the guardian reaps this
                # subject while the descendant lives on.
                pid = os.fork()
                if pid == 0:
                    import time as clock
                    clock.sleep(3600)
                scratch = Path(directory, "grandchild.tmp")
                scratch.write_text(str(pid), encoding="ascii")
                scratch.rename(grandchild_file)  # atomic: never a partial PID

            real_drain = emit._fixture_drain

            def reap_then_wedge(subject, subject_fd=None, *, deadline=None):
                # Guardian-side: reap the exited subject, then wedge BEFORE any
                # group kill: the state a drain stalled mid-cleanup leaves behind.
                if os.getpid() != caller:
                    os.waitpid(subject, 0)
                    scratch = Path(directory, "wedged.tmp")
                    scratch.write_text(str(os.getpid()), encoding="ascii")
                    scratch.rename(wedged)  # atomic: never a partial PID
                    os.kill(os.getpid(), signal.SIGSTOP)
                return real_drain(subject, subject_fd, deadline=deadline)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def exercise(flip):
                wedged.unlink(missing_ok=True)
                grandchild_file.unlink(missing_ok=True)
                child = emit._FixtureProcess(time.monotonic() + 3600,
                                             subject=subject_body)
                with patch.object(emit, "_fixture_drain", reap_then_wedge):
                    child.start()
                bound = time.monotonic() + 30
                while not (wedged.exists() and grandchild_file.exists()):
                    assert time.monotonic() < bound, "the drain wedge was not reached"
                    time.sleep(0.005)
                gpid = int(wedged.read_text(encoding="ascii"))
                assert gpid == child.pid, "the hook stopped an unexpected process"
                await_state(gpid, ("T",), "the guardian did not stop")
                grandchild = int(grandchild_file.read_text(encoding="ascii"))
                assert state(grandchild) not in (None, "Z"), "the descendant died early"
                failures = []
                with ExitStack() as stack:
                    stack.enter_context(
                        patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0))
                    if flip:
                        # Flip: skip the group kill for the already-reaped subject
                        # (the old probe-gated early return): the descendant must
                        # survive again.
                        stack.enter_context(patch.object(
                            emit, "_fixture_escalate_subject",
                            lambda subject, fd, **license: None))
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))
                assert failures and "escalat" in failures[0], failures
                assert child.subject_pid is not None, "the subject receipt was lost"
                return child.subject_pid, grandchild

            subject, grandchild = exercise(flip=False)
            await_state(grandchild, (None, "Z"),
                        "escalation stranded the reaped subject's same-group descendant")
            subject, grandchild = exercise(flip=True)
            assert state(grandchild) not in (None, "Z"), \
                "flip: the descendant died without the group kill"
            os.killpg(subject, signal.SIGKILL)  # hygiene for the demonstrated leak
            await_state(grandchild, (None, "Z"), "the flip hygiene did not complete")
    elif mode == "poll-collected":
        import time
        # QA19 F1: a guardian failure FIRST collected by poll() -- the layer's
        # primary collection path (run_bounded and _run_fixture_process poll on
        # every loop iteration) -- must still get close()'s receipt kill,
        # disappearance proof and loud refusal: poll() records the failure and
        # close() re-raises it after addressing the subject. pdeathsig is
        # disarmed so only close()'s own kill can end the orphaned subject tree.
        with tempfile.TemporaryDirectory(prefix="opf-pollfail-") as directory:
            descendant_file = Path(directory, "descendant")

            def subject_body():
                import time as clock
                pid = os.fork()
                if pid == 0:
                    clock.sleep(3600)          # same-group descendant
                scratch = Path(directory, "descendant.tmp")
                scratch.write_text(str(pid), encoding="ascii")
                scratch.rename(descendant_file)  # atomic: never a partial PID
                clock.sleep(3600)              # the subject outlives its guardian

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def launch_killed():
                # Launch, wait for the running subject tree, then SIGKILL the
                # guardian and collect it through poll(), never through close().
                descendant_file.unlink(missing_ok=True)
                with patch.object(emit, "_fixture_pdeathsig", lambda: None):
                    child = emit._FixtureProcess(time.monotonic() + 3600,
                                                 subject=subject_body)
                    child.start()
                bound = time.monotonic() + 30
                while not descendant_file.exists():
                    assert time.monotonic() < bound, "the subject tree never appeared"
                    time.sleep(0.005)
                descendant = int(descendant_file.read_text(encoding="ascii"))
                subject = int(Path("/proc", str(child.pid), "task", str(child.pid),
                                   "children").read_text(encoding="ascii").split()[0])
                os.kill(child.pid, signal.SIGKILL)
                polled = []
                bound = time.monotonic() + 30
                while True:
                    try:
                        status = child.poll()
                    except emit.ChildStatusUnavailable as exc:
                        polled.append(str(exc))
                        break
                    assert status is None, status
                    assert time.monotonic() < bound, \
                        "poll() never collected the killed guardian"
                    time.sleep(0.005)
                assert child.collected, "poll() did not record the collection"
                assert "guardian failed" in polled[0], polled
                return child, subject, descendant

            child, subject, descendant = launch_killed()
            # Fix 2w: the pin-first receipt kill already ran AT COLLECTION time
            # (the subject was live, so the group kill ran pinned); close()
            # still proves the exit and re-raises the recorded failure.
            assert child._subject_kill == "pinned", child._subject_kill
            closed = []
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    closed.append(str(exc))
            assert closed and "guardian failed" in closed[0], \
                ("close() did not re-raise the poll-recorded failure", closed)
            assert child.subject_pid == subject, (child.subject_pid, subject)
            # A dead orphan reparents to the nearest subreaper ancestor (the
            # outer guardian, under the regression runner) and lingers as a
            # zombie until that ancestor's drain: Z is dead, not surviving.
            await_state(subject, (None, "Z"),
                        "close() left the poll-collected failure's subject running")
            await_state(descendant, (None, "Z"),
                        "close() stranded the subject's same-group descendant")

            # Flip: erase everything fix 2w acts on -- no collection-time kill,
            # no recorded failure, and a FORGED validated status (the pre-fix
            # close believed exactly this state) -> close() returns SILENTLY
            # and the subject tree survives.
            with patch.object(emit._FixtureProcess, "_address_failed_subject",
                              lambda fixture: None):
                child, subject, descendant = launch_killed()
            child._failure = None
            child.status = 0
            child.unresolved = False
            closed = []
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    closed.append(str(exc))
            assert not closed, closed
            assert state(subject) not in (None, "Z"), \
                "flip: the subject died without close()'s kill"
            assert state(descendant) not in (None, "Z"), \
                "flip: the descendant died without close()'s kill"
            os.killpg(subject, signal.SIGKILL)  # hygiene for the demonstrated leak
            await_state(subject, (None, "Z"), "the flip hygiene did not complete")
            await_state(descendant, (None, "Z"), "the flip hygiene did not complete")
    elif mode == "close-cancel":
        import time
        # QA19 F2 + QA20 codex F2 / gemini / claude F2 (masked design, fix 2w):
        # a real SIGINT landing anywhere inside close()'s ownership decision,
        # transfer, collection or cleanup stays PENDING (SIGINT and SIGTERM are
        # masked from close()'s first statement) and is delivered only after
        # the sequence completes: close() never raises the interrupt itself,
        # the guardian and subject tree are cleaned or handed to a live owner
        # FIRST, a refusal survives (directly or as the delivered interrupt's
        # context), and the caller's prior mask is restored exactly. Windows:
        # (1) the completion wait, fork unrecorded; (2) the gap before the
        # launcher release; (3) entering the ownership transfer; (4) the
        # collection's bounded reap wait; (5) the collection entry boundary.
        real_fork = os.fork
        cancellation = {signal.SIGINT, signal.SIGTERM}
        masked_at = {}
        real_abandon = emit._FixtureProcess._abandon_unfinished_launch
        real_finish = emit._FixtureProcess._finish_close

        def open_fds():
            live = set()
            for name in os.listdir("/proc/self/fd"):
                try:
                    os.fstat(int(name))
                except OSError:
                    continue
                live.add(int(name))
            return live

        def no_children():
            try:
                os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                return True
            return False

        def state(target):
            try:
                stat = Path("/proc", str(target), "stat").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                return None
            return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

        def await_state(target, wanted, note):
            bound = time.monotonic() + 30
            while state(target) not in wanted:
                assert time.monotonic() < bound, note
                time.sleep(0.005)

        def await_subject(child):
            bound = time.monotonic() + 30
            while True:
                listed = Path("/proc", str(child.pid), "task", str(child.pid),
                              "children").read_text(encoding="ascii").split()
                if listed:
                    return int(listed[0])
                assert time.monotonic() < bound, "the subject never appeared"
                time.sleep(0.005)

        def observe_abandon(fixture):
            masked_at["transfer"] = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            return real_abandon(fixture)

        def observe_finish(fixture):
            masked_at["collect"] = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            return real_finish(fixture)

        def interrupt_wait(child):
            # A REAL SIGINT raised inside the masked sequence: the kernel keeps
            # it pending until close() restores the caller's mask.
            real_wait, fired = child._launched.wait, []

            def wait(timeout=None):
                if not fired:
                    fired.append(True)
                    signal.raise_signal(signal.SIGINT)
                return real_wait(timeout)

            child._launched.wait = wait

        def drive(child):
            # close() under a pending injected SIGINT: the sequence completes
            # FIRST and the interrupt arrives after it; a refusal survives
            # directly or on the delivered interrupt's context chain.
            before_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            refusals, delivered = [], []
            try:
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    refusals.append(str(exc))
                bound = time.monotonic() + 10
                while time.monotonic() < bound:
                    pass
                raise AssertionError("the pending interrupt was never delivered")
            except KeyboardInterrupt as exc:
                delivered.append(True)
                context = exc.__context__
                while context is not None and not refusals:
                    if isinstance(context, emit.ChildStatusUnavailable):
                        refusals.append(str(context))
                        break
                    context = context.__context__
            assert delivered, "the pending interrupt was never delivered"
            assert signal.pthread_sigmask(signal.SIG_BLOCK, set()) == before_mask, \
                "close() did not restore the exact prior mask"
            return refusals

        gate = threading.Event()

        def wedged_fork():
            if threading.current_thread().name == "opf-fixture-launcher":
                assert gate.wait(60), "the fork gate was never released"
            return real_fork()

        emit._fixture_preload()
        assert no_children(), "children live before the close-cancel probe"
        before = open_fds()

        # Leg 1: fork unrecorded (wedged), SIGINT pending from inside the
        # completion wait -> the wait runs its full bounded budget, the launch
        # is abandoned UNDER THE LAUNCH LOCK (masked, observed), the ownership
        # refusal survives, and the launcher collects its own fork.
        with patch.object(os, "fork", wedged_fork), \
                patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0), \
                patch.object(emit._FixtureProcess, "_abandon_unfinished_launch",
                             observe_abandon):
            child = emit._FixtureProcess(time.monotonic() + 0.1, subject=lambda: None)
            refuses(TimeoutError, child._start)
            interrupt_wait(child)
            launcher = child._launcher
            refusals = drive(child)
            assert refusals and "ownership unknown" in refusals[0], refusals
            assert child._abandoned, "the interrupted close did not abandon the launch"
            assert cancellation <= masked_at["transfer"], \
                ("the ownership transfer ran unmasked", masked_at)
            gate.set()
            launcher.join(30)
            assert not launcher.is_alive(), "the abandoned launcher never finished"
            assert child.pid is not None, "the launcher lost its fork"
            assert emit._fixture_child_reaped(child.pid) is True, \
                "the abandoned launcher did not collect its own fork"
        assert open_fds() == before, "the interrupted close leaked descriptors"
        assert no_children(), "the interrupted close leaked a child"

        # Leg 2: SIGINT pending from the gap BEFORE the launcher release ->
        # the launcher is released (never parked), the cancelled launch never
        # forks, and the interrupt lands only after close() returned.
        child = emit._FixtureProcess(time.monotonic() + 30, subject=lambda: None)
        launcher = child._launcher
        real_set, fired = child._go.set, []

        def raising_set():
            if not fired:
                fired.append(True)
                signal.raise_signal(signal.SIGINT)
            return real_set()

        child._go.set = raising_set
        refusals = drive(child)
        assert not refusals, refusals
        launcher.join(30)
        assert not launcher.is_alive(), "the gap cancellation left the launcher parked"
        assert fired and child.pid is None, "the cancelled launch forked anyway"
        assert open_fds() == before and no_children(), "the gap cancellation leaked"

        # Leg 3: launch recorded, SIGINT pending from the completion wait ->
        # close() stays the owner and the WHOLE collection (EOF, drain,
        # receipt validation), masked and observed, completes before the
        # interrupt lands.
        with patch.object(emit._FixtureProcess, "_finish_close", observe_finish):
            child = emit._FixtureProcess(time.monotonic() + 30,
                                         subject=lambda: time.sleep(3600))
            child.start()
            gpid = child.pid
            subject = await_subject(child)
            interrupt_wait(child)
            refusals = drive(child)
        assert not refusals, refusals
        assert cancellation <= masked_at["collect"], \
            ("the collection sequence ran unmasked", masked_at)
        assert child.collected and emit._fixture_child_reaped(gpid) is True, \
            "the interrupted owner did not collect its guardian"
        assert child.status is not None and child.cleaned, \
            "the masked collection did not finish validating"
        await_state(subject, (None, "Z"), "the interrupted owner left the subject")
        assert open_fds() == before and no_children(), "the interrupted owner leaked"

        # Leg 4: SIGINT pending from inside the collection's bounded reap wait
        # -> the reap loop keeps running, collection and validation complete,
        # the interrupt lands after.
        child = emit._FixtureProcess(time.monotonic() + 3600,
                                     subject=lambda: time.sleep(3600))
        child.start()
        gpid = child.pid
        subject = await_subject(child)
        real_waitpid, fired = os.waitpid, []

        def interrupted_waitpid(wpid, flags):
            if not fired and wpid == gpid:
                fired.append(True)
                signal.raise_signal(signal.SIGINT)
            return real_waitpid(wpid, flags)

        with patch.object(os, "waitpid", interrupted_waitpid):
            refusals = drive(child)
        assert fired, "the reap wait was never reached"
        assert not refusals, refusals
        assert child.collected and child.status is not None \
            and emit._fixture_child_reaped(gpid) is True, \
            "the interrupted collection did not reap and validate the guardian"
        await_state(subject, (None, "Z"), "the interrupted collection left the subject")
        assert open_fds() == before and no_children(), "the interrupted collection leaked"

        # Leg 5: SIGINT raised ENTERING the ownership transfer (the QA20 codex
        # F2 gap, before _abandoned is recorded) -> masked, the transfer still
        # records the abandonment under the lock before the interrupt can land.
        gate.clear()

        def inject_abandon(fixture):
            signal.raise_signal(signal.SIGINT)
            return real_abandon(fixture)

        with patch.object(os, "fork", wedged_fork), \
                patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0), \
                patch.object(emit._FixtureProcess, "_abandon_unfinished_launch",
                             inject_abandon):
            child = emit._FixtureProcess(time.monotonic() + 0.1, subject=lambda: None)
            refuses(TimeoutError, child._start)
            launcher = child._launcher
            refusals = drive(child)
            assert refusals and "ownership unknown" in refusals[0], refusals
            assert child._abandoned, \
                "the transfer-entry interrupt lost the abandonment record"
            gate.set()
            launcher.join(30)
            assert not launcher.is_alive(), "the abandoned launcher never finished"
            assert emit._fixture_child_reaped(child.pid) is True, \
                "the abandoned launcher did not collect its own fork"
        assert open_fds() == before and no_children(), "the transfer interrupt leaked"

        # Flip A: report the abandonment WITHOUT recording it under the launch
        # lock (the pre-fix close left _abandoned unset) -> the launcher
        # completes with nothing telling it to collect, and the guardian leaks.
        gate.clear()
        with patch.object(os, "fork", wedged_fork), \
                patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
            child = emit._FixtureProcess(time.monotonic() + 0.1, subject=lambda: None)
            refuses(TimeoutError, child._start)
            interrupt_wait(child)
            launcher = child._launcher
            with patch.object(emit._FixtureProcess, "_abandon_unfinished_launch",
                              lambda child: True):
                refusals = drive(child)
            assert refusals and "ownership unknown" in refusals[0], refusals
            assert not child._abandoned, "the flip still recorded the abandonment"
            gate.set()
            launcher.join(30)
            assert not launcher.is_alive(), "the flip launcher never finished"
            assert child.pid is not None, "the flip launcher lost its fork"
            assert emit._fixture_child_reaped(child.pid) is False, \
                "the flip did not reproduce the owner-less guardian"
            os.kill(child.pid, signal.SIGKILL)  # hygiene for the demonstrated leak
            os.waitpid(child.pid, 0)
            emit._fixture_abort_launch(child)   # hygiene: releases control/peer/report
            if child.pidfd is not None:
                os.close(child.pidfd)
                child.pidfd = None
        assert open_fds() == before and no_children(), "the flip hygiene did not complete"

        # Flip B: REMOVE the masking (no-op the mask seam) -> the injected
        # SIGINT materializes INSIDE close() again: the sequence runs unmasked
        # (observed at the collection entry) and close() itself raises the
        # KeyboardInterrupt instead of completing before delivery.
        with patch.object(emit, "_fixture_mask_cancellation",
                          lambda: signal.pthread_sigmask(signal.SIG_BLOCK, set())), \
                patch.object(emit._FixtureProcess, "_finish_close", observe_finish):
            child = emit._FixtureProcess(time.monotonic() + 30,
                                         subject=lambda: time.sleep(3600))
            child.start()
            gpid = child.pid
            subject = await_subject(child)
            interrupt_wait(child)
            refuses(KeyboardInterrupt, child.close)
        assert not (cancellation & masked_at["collect"]), \
            ("the flip still masked the collection", masked_at)
        assert child.collected and emit._fixture_child_reaped(gpid) is True, \
            "the flip backstops did not collect the guardian"
        await_state(subject, (None, "Z"), "the flip backstops left the subject")
        assert open_fds() == before and no_children(), "the flip hygiene did not complete"

        # Fail closed: without pthread_sigmask the fixture REFUSES to launch at
        # all rather than ever run an unmaskable close()/poll() sequence.
        with patch.object(emit, "_fixture_mask_available", lambda: False):
            refuses(emit.ChildStatusUnavailable,
                    lambda: emit._FixtureProcess(time.monotonic() + 5,
                                                 subject=lambda: None))
        assert open_fds() == before and no_children(), "the refused launch leaked"
    elif mode == "escalate-degraded":
        import time
        from contextlib import ExitStack
        caller = os.getpid()
        # QA19 F3: with no pidfd available the escalation can neither freeze the
        # guardian nor address the pid-only receipt's subject, so the refusal
        # must say guardian-only/unverified -- never claim a frozen guardian or
        # a killed subject tree it could not address.
        with tempfile.TemporaryDirectory(prefix="opf-degraded-") as directory:
            wedged = Path(directory, "wedged")
            real_ack = emit._fixture_ack_subject

            def wedge_ack(fd):
                # Wedge AFTER the pid-only receipt send, BEFORE the acknowledgment:
                # the unacknowledged subject refuses by itself on guardian death.
                if os.getpid() != caller:
                    scratch = Path(directory, "wedged.tmp")
                    scratch.write_text(str(os.getpid()), encoding="ascii")
                    scratch.rename(wedged)  # atomic: never a partial PID
                    os.kill(os.getpid(), signal.SIGSTOP)
                real_ack(fd)

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def by_pid(child_self, failure):
                # The pre-fix suffix chose by subject_pid and always claimed a
                # frozen guardian and a killed subject tree.
                if child_self.subject_pid is not None:
                    raise emit.ChildStatusUnavailable(
                        str(failure) + "; after bounded-close escalation (guardian "
                        "frozen, subject tree killed, guardian SIGKILL)") from failure
                raise failure

            def exercise(flip):
                wedged.unlink(missing_ok=True)
                with ExitStack() as stack:
                    stack.enter_context(patch.object(emit, "_fixture_pidfd",
                                                     lambda pid: None))
                    stack.enter_context(patch.object(emit, "_fixture_ack_subject",
                                                     wedge_ack))
                    child = emit._FixtureProcess(time.monotonic() + 3600,
                                                 subject=lambda: time.sleep(3600))
                    child.start()
                bound = time.monotonic() + 30
                while not wedged.exists():
                    assert time.monotonic() < bound, \
                        "the guardian never reached its stop point"
                    time.sleep(0.005)
                gpid = int(wedged.read_text(encoding="ascii"))
                assert gpid == child.pid, "the hook stopped an unexpected process"
                await_state(gpid, ("T",), "the guardian did not stop")
                subject = int(Path("/proc", str(gpid), "task", str(gpid), "children")
                              .read_text(encoding="ascii").split()[0])
                failures = []
                with ExitStack() as stack:
                    stack.enter_context(patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0))
                    if flip:
                        stack.enter_context(patch.object(
                            emit._FixtureProcess, "_escalation_refusal", by_pid))
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        failures.append(str(exc))
                assert failures and "escalat" in failures[0], failures
                assert child.subject_pid == subject, (child.subject_pid, subject)
                await_state(subject, (None, "Z"),
                            "the unacknowledged subject outlived the degraded escalation")
                return failures[0]

            message = exercise(flip=False)
            assert "subject cleanup unverified" in message, message
            assert "guardian not frozen" in message, message
            assert "subject tree killed" not in message, message
            # Flip: the by-pid suffix claims the frozen guardian and killed tree
            # this degraded escalation never performed.
            flipped = exercise(flip=True)
            assert "subject tree killed" in flipped, \
                ("the flip did not reproduce the false claim", flipped)
    elif mode == "poll-masked":
        import time
        # QA20 codex F1 + claude F1 (masked design, fix 2w): poll()'s whole
        # collection-to-failure-recording step runs MASKED and kills the
        # receipt-identified subject pin-first AT COLLECTION time; a validation
        # that still dies inside that window leaves an UNRESOLVED collection
        # (guardian reaped, status never validated, no failure recorded) that
        # close() treats as a failure of its own, and the last-resort interrupt
        # collector addresses the subject of a collected-but-uncleaned guardian
        # instead of returning early. On a pidfd-absent host the un-escalated
        # re-raise names the unverifiable cleanup (QA20 claude F3).
        cancellation = {signal.SIGINT, signal.SIGTERM}
        with tempfile.TemporaryDirectory(prefix="opf-pollmask-") as directory:
            descendant_file = Path(directory, "descendant")

            def subject_body():
                import time as clock
                pid = os.fork()
                if pid == 0:
                    clock.sleep(3600)          # same-group descendant
                scratch = Path(directory, "descendant.tmp")
                scratch.write_text(str(pid), encoding="ascii")
                scratch.rename(descendant_file)  # atomic: never a partial PID
                clock.sleep(3600)              # the subject outlives its guardian

            def state(target):
                try:
                    stat = Path("/proc", str(target), "stat").read_bytes()
                except (FileNotFoundError, ProcessLookupError):
                    return None
                return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

            def await_state(target, wanted, note):
                bound = time.monotonic() + 30
                while state(target) not in wanted:
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            def launch_killed(patches=()):
                # Launch, wait for the running subject tree, then SIGKILL the
                # guardian; the caller collects through poll(), never close().
                from contextlib import ExitStack
                descendant_file.unlink(missing_ok=True)
                with ExitStack() as stack:
                    stack.enter_context(
                        patch.object(emit, "_fixture_pdeathsig", lambda: None))
                    for target, name, value in patches:
                        stack.enter_context(patch.object(target, name, value))
                    child = emit._FixtureProcess(time.monotonic() + 3600,
                                                 subject=subject_body)
                    child.start()
                bound = time.monotonic() + 30
                while not descendant_file.exists():
                    assert time.monotonic() < bound, "the subject tree never appeared"
                    time.sleep(0.005)
                descendant = int(descendant_file.read_text(encoding="ascii"))
                subject = int(Path("/proc", str(child.pid), "task", str(child.pid),
                                   "children").read_text(encoding="ascii").split()[0])
                os.kill(child.pid, signal.SIGKILL)
                return child, subject, descendant

            def poll_failure(child):
                refusals = []
                bound = time.monotonic() + 30
                while not refusals:
                    assert time.monotonic() < bound, "poll never collected"
                    try:
                        assert child.poll() is None
                    except emit.ChildStatusUnavailable as exc:
                        refusals.append(str(exc))
                        break
                    time.sleep(0.005)
                return refusals

            def close_grace(child):
                closed = []
                with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0):
                    try:
                        child.close()
                    except emit.ChildStatusUnavailable as exc:
                        closed.append(str(exc))
                return closed

            # Leg 1: a SIGINT raised INSIDE the recording step stays pending:
            # poll() still records the failure, kills the subject tree
            # pin-first at collection time, and the interrupt lands only after
            # the masked step restored the caller's mask.
            child, subject, descendant = launch_killed()
            captured = []
            real_read = emit._FixtureProcess._read_report

            def read_hook(fixture, raw):
                captured.append(signal.pthread_sigmask(signal.SIG_BLOCK, set()))
                signal.raise_signal(signal.SIGINT)
                return real_read(fixture, raw)

            refusals, delivered = [], []
            with patch.object(emit._FixtureProcess, "_read_report", read_hook):
                bound = time.monotonic() + 30
                try:
                    while not refusals:
                        assert time.monotonic() < bound, "poll never collected"
                        try:
                            assert child.poll() is None
                        except emit.ChildStatusUnavailable as exc:
                            refusals.append(str(exc))
                            spin = time.monotonic() + 10
                            while time.monotonic() < spin:
                                pass
                            raise AssertionError(
                                "the pending interrupt was never delivered")
                        time.sleep(0.005)
                except KeyboardInterrupt as exc:
                    delivered.append(True)
                    context = exc.__context__
                    while context is not None and not refusals:
                        if isinstance(context, emit.ChildStatusUnavailable):
                            refusals.append(str(context))
                            break
                        context = context.__context__
            assert delivered, "the pending interrupt was never delivered"
            assert captured and cancellation <= captured[0], \
                ("the recording step ran unmasked", captured)
            assert refusals and "guardian failed" in refusals[0], refusals
            assert child.collected and child._failure is not None, \
                "the masked step did not record the failure"
            assert child._subject_kill == "pinned", child._subject_kill
            assert child.subject_pid == subject, (child.subject_pid, subject)
            # The collection-time kill addressed the tree BEFORE close().
            await_state(subject, (None, "Z"),
                        "poll() left the failed guardian's subject running")
            await_state(descendant, (None, "Z"),
                        "poll() stranded the subject's same-group descendant")
            closed = close_grace(child)
            assert closed and "guardian failed" in closed[0], closed

            # Leg 2: a validation that dies BETWEEN collection and recording
            # (the pre-mask-flag residual, modeled with a synthetic
            # BaseException) leaves an UNRESOLVED collection: the last-resort
            # interrupt collector still kills the subject tree, and close()
            # treats the unresolved state as a failure and re-raises loudly.
            class Cancelled(BaseException):
                pass

            def poll_cancelled(child):
                with patch.object(emit._FixtureProcess, "_read_report",
                                  side_effect=Cancelled()):
                    bound = time.monotonic() + 30
                    while True:
                        assert time.monotonic() < bound, "poll never collected"
                        try:
                            assert child.poll() is None
                        except Cancelled:
                            break
                        time.sleep(0.005)

            child, subject, descendant = launch_killed()
            poll_cancelled(child)
            assert child.collected and child._failure is None \
                and child.status is None and child.unresolved, \
                "the interrupted validation did not leave an unresolved collection"
            assert state(subject) not in (None, "Z"), \
                "the unresolved leg lost its live subject early"
            child._interrupt_collect()  # the backstop's collected-but-uncleaned path
            await_state(subject, (None, "Z"),
                        "the interrupt collector left the unresolved subject running")
            await_state(descendant, (None, "Z"),
                        "the interrupt collector stranded the descendant")
            closed = close_grace(child)
            assert closed and "supervision unresolved" in closed[0], closed

            # Flip: forge a VALIDATED status (the state the pre-fix close
            # believed blindly) -> close() is silent, the tree survives.
            child, subject, descendant = launch_killed()
            poll_cancelled(child)
            child.status = 0
            child.unresolved = False
            closed = close_grace(child)
            assert not closed, closed
            assert state(subject) not in (None, "Z"), \
                "flip: the subject died without close()'s kill"
            os.killpg(subject, signal.SIGKILL)  # hygiene for the demonstrated leak
            await_state(subject, (None, "Z"), "the flip hygiene did not complete")
            await_state(descendant, (None, "Z"), "the flip hygiene did not complete")

            # Leg 3 (QA20 claude F3): pidfd-absent host -- a pid-only receipt's
            # poll-collected failure re-raises through close() NAMING the
            # unverifiable cleanup, and the known subject pid is never
            # group-killed unpinned: the surviving tree is the documented
            # residual, cleaned here by hygiene.
            child, subject, descendant = launch_killed(
                patches=((emit, "_fixture_pidfd", lambda pid: None),))
            refusals = poll_failure(child)
            assert refusals and "guardian failed" in refusals[0], refusals
            assert child.subject_pid == subject and child.subject_pidfd is None, \
                (child.subject_pid, child.subject_pidfd)
            closed = close_grace(child)
            assert closed and "guardian failed" in closed[0], closed
            assert "WITHOUT a subject pidfd" in closed[0] \
                and "subject cleanup unverified" in closed[0], closed
            assert "subject tree killed" not in closed[0], closed
            assert state(subject) not in (None, "Z"), \
                "an unpinned pid-only subject was signalled anyway"
            os.killpg(subject, signal.SIGKILL)  # hygiene for the documented residual
            await_state(subject, (None, "Z"), "the residual hygiene did not complete")
            await_state(descendant, (None, "Z"), "the residual hygiene did not complete")

            # Flip: the pre-fix bare re-raise names nothing.
            def bare(fixture, failure):
                raise failure

            child, subject, descendant = launch_killed(
                patches=((emit, "_fixture_pidfd", lambda pid: None),))
            refusals = poll_failure(child)
            assert refusals, refusals
            closed = []
            with patch.object(emit, "_FIXTURE_CLEANUP_GRACE", 1.0), \
                    patch.object(emit._FixtureProcess, "_unverified_refusal", bare):
                try:
                    child.close()
                except emit.ChildStatusUnavailable as exc:
                    closed.append(str(exc))
            assert closed and "subject cleanup unverified" not in closed[0], closed
            os.killpg(subject, signal.SIGKILL)  # hygiene for the demonstrated blindness
            await_state(subject, (None, "Z"), "the flip hygiene did not complete")
            await_state(descendant, (None, "Z"), "the flip hygiene did not complete")
    elif mode == "unpinned-kill":
        import time
        # QA20 claude F1: NEVER signal a pid or group whose ownership is not
        # pinned -- a held pidfd does not pin the pid NUMBER once the process
        # is reaped, only unreapability does. Pin-first: a live leader is
        # frozen through its pidfd and only then group-killed; a reaped leader
        # licenses NO group kill unless a frozen subreaper guardian still
        # parents a group member (the census pin, which keeps QA18 gemini F1's
        # reaped-subject descendants reachable); the pidfd SIGKILL always
        # runs; a skipped group kill is reported unpinned for disclosure.
        recorded = []
        real_killpg, real_pidfd_signal = os.killpg, signal.pidfd_send_signal

        def rec_killpg(pgid, signum):
            recorded.append(("killpg", pgid, signum))
            return real_killpg(pgid, signum)

        def rec_pidfd(fd, signum, *args):
            recorded.append(("pidfd", fd, signum))
            return real_pidfd_signal(fd, signum, *args)

        def state(target):
            try:
                stat = Path("/proc", str(target), "stat").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                return None
            return stat.rsplit(b")", 1)[1].split()[0].decode("ascii")

        def await_state(target, wanted, note):
            bound = time.monotonic() + 30
            while state(target) not in wanted:
                assert time.monotonic() < bound, note
                time.sleep(0.005)

        # Leg 1: LIVE leader -> the freeze pins pid AND pgid; the group kill
        # runs pinned, and the freeze strictly precedes it.
        leader = os.fork()
        if leader == 0:
            os.setsid()
            time.sleep(3600)
            os._exit(0)
        bound = time.monotonic() + 30
        while True:
            try:
                if os.getpgid(leader) == leader:
                    break
            except ProcessLookupError:
                pass
            assert time.monotonic() < bound, "the leader never took its group"
            time.sleep(0.005)
        fd = os.pidfd_open(leader)
        with patch.object(os, "killpg", rec_killpg), \
                patch.object(signal, "pidfd_send_signal", rec_pidfd):
            assert emit._fixture_escalate_subject(leader, fd) is True, \
                "a live leader did not pin its group"
        assert recorded[0] == ("pidfd", fd, signal.SIGSTOP), recorded
        assert ("killpg", leader, signal.SIGKILL) in recorded, recorded
        os.waitpid(leader, 0)
        os.close(fd)

        # Leg 2: REAPED leader, no census license -> the group kill is SKIPPED
        # (nothing pins the freed number against reuse) and reported unpinned;
        # the pidfd kill still runs against the pinned identity.
        leader = os.fork()
        if leader == 0:
            os._exit(0)
        fd = os.pidfd_open(leader)
        os.waitpid(leader, 0)  # reaped: the number is free to be recycled
        recorded.clear()
        with patch.object(os, "killpg", rec_killpg), \
                patch.object(signal, "pidfd_send_signal", rec_pidfd):
            assert emit._fixture_escalate_subject(leader, fd) is False, \
                "a reaped leader was reported pinned"
        assert not [entry for entry in recorded if entry[0] == "killpg"], \
            ("an unpinned group was signalled", recorded)
        assert ("pidfd", fd, signal.SIGKILL) in recorded, recorded

        # Flip: the pre-fix unconditional kill signals the FREED number (the
        # wrong-owner defect), demonstrated against a record-only killpg.
        def unconditional(subject, subject_fd):
            try:
                os.killpg(subject, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                signal.pidfd_send_signal(subject_fd, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

        recorded.clear()
        with patch.object(os, "killpg",
                          lambda pgid, signum: recorded.append(("killpg", pgid, signum))):
            unconditional(leader, fd)
        assert ("killpg", leader, signal.SIGKILL) in recorded, \
            "the flip did not reproduce the unpinned group kill"
        os.close(fd)

        # Leg 3: reaped leader, frozen-guardian census -> a surviving
        # same-group descendant, adopted by the stopped subreaper guardian,
        # pins the group and the kill still reaches it (QA18 gemini F1
        # preserved under the pin rule).
        with tempfile.TemporaryDirectory(prefix="opf-pin-") as directory:
            leader_file = Path(directory, "leader")
            grandchild_file = Path(directory, "grandchild")
            frozen_file = Path(directory, "frozen")
            opened = Path(directory, "opened")

            guardian = os.fork()
            if guardian == 0:
                try:
                    import ctypes
                    libc = ctypes.CDLL(None, use_errno=True)
                    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
                        os._exit(125)
                    leader = os.fork()
                    if leader == 0:
                        os.setsid()
                        grandchild = os.fork()
                        if grandchild == 0:
                            time.sleep(3600)  # same-group descendant
                            os._exit(0)
                        scratch = Path(directory, "grandchild.tmp")
                        scratch.write_text(str(grandchild), encoding="ascii")
                        scratch.rename(grandchild_file)
                        time.sleep(3600)      # until the guardian kills it
                        os._exit(0)
                    scratch = Path(directory, "leader.tmp")
                    scratch.write_text(str(leader), encoding="ascii")
                    scratch.rename(leader_file)
                    bound = time.monotonic() + 30
                    while not opened.exists():  # the caller holds the pidfd now
                        if time.monotonic() >= bound:
                            os._exit(125)
                        time.sleep(0.005)
                    os.kill(leader, signal.SIGKILL)
                    os.waitpid(leader, 0)       # REAPED: the number is unpinned
                    scratch = Path(directory, "frozen.tmp")
                    scratch.write_text("stopping", encoding="ascii")
                    scratch.rename(frozen_file)
                    os.kill(os.getpid(), signal.SIGSTOP)
                    os._exit(0)
                except BaseException:
                    os._exit(125)

            def await_file(path, note):
                bound = time.monotonic() + 30
                while not path.exists():
                    assert time.monotonic() < bound, note
                    time.sleep(0.005)

            await_file(leader_file, "the model leader never appeared")
            leader = int(leader_file.read_text(encoding="ascii"))
            fd = os.pidfd_open(leader)
            await_file(grandchild_file, "the model grandchild never appeared")
            grandchild = int(grandchild_file.read_text(encoding="ascii"))
            scratch = Path(directory, "opened.tmp")
            scratch.write_text("y", encoding="ascii")
            scratch.rename(opened)
            await_file(frozen_file, "the model guardian never froze")
            await_state(guardian, ("T",), "the model guardian did not stop")
            assert state(grandchild) not in (None, "Z"), "the descendant died early"
            recorded.clear()
            with patch.object(os, "killpg", rec_killpg), \
                    patch.object(signal, "pidfd_send_signal", rec_pidfd):
                assert emit._fixture_escalate_subject(
                    leader, fd, guardian_pid=guardian) is True, \
                    "the frozen-guardian census did not pin the group"
            assert ("killpg", leader, signal.SIGKILL) in recorded, recorded
            await_state(grandchild, (None, "Z"),
                        "the census-pinned kill stranded the descendant")
            os.close(fd)
            os.kill(guardian, signal.SIGCONT)
            os.waitpid(guardian, 0)
    else:
        raise AssertionError("unknown completion fixture: " + mode)
    print("opf completion:", mode, "PASS")
    return EXIT_OK


def _watchdog_overlap_case(mode):
    """Fix the fork/close interleaving; no sleep decides which caller finishes first."""
    import select
    import threading
    from unittest.mock import patch
    import _opf_emit as emit

    caller = os.getpid()
    paused, resume = threading.Event(), threading.Event()
    sibling_started = threading.Event()
    release_r, release_w = os.pipe()
    entered_r, entered_w = os.pipe()
    real_start = emit._FixtureProcess.start
    results, owners, errors = {}, {}, []

    def start(owner):
        pid = real_start(owner)
        if os.getpid() == caller:
            name = threading.current_thread().name
            owners[name] = owner
            if name == "A":
                if mode == "success":
                    # A's entire tree has exited successfully, with bytes buffered,
                    # but its caller still holds wfd. B must fork in this window.
                    ended = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
                    assert ended.si_code == os.CLD_EXITED and ended.si_status == 0
                paused.set()
                assert resume.wait(10), "A was not resumed"
            else:
                sibling_started.set()  # B's guardian has inherited A's writer
        return pid

    def blocked():
        os.write(entered_w, b"R")
        assert os.read(release_r, 1) == b"G"
        return "B-OK"

    def call(name):
        try:
            thunk = (lambda: "A-OK") if name == "A" else blocked
            timeout = 5 if name == "A" else 15
            if mode == "timeout":
                thunk = blocked if name == "A" else (lambda: "B-OK")
                timeout = 1 if name == "A" else 15
            # The blocked thunk handshakes over caller pipes: under the fd
            # allowlist they must be DECLARED to survive into the subject.
            results[name] = emit.run_bounded(thunk, timeout_s=timeout,
                                             keep_fds=(release_r, entered_w))
        except BaseException as exc:
            errors.append((name, repr(exc)))

    a = threading.Thread(target=call, args=("A",), name="A", daemon=True)
    b = threading.Thread(target=call, args=("B",), name="B", daemon=True)
    try:
        with patch.object(emit._FixtureProcess, "start", start):
            try:
                a.start()
                assert paused.wait(10), "A did not reach its pre-close barrier"
                b.start()
                assert sibling_started.wait(10), "B did not fork while A held wfd"
                ready, _, _ = select.select([entered_r], [], [], 10)
                assert ready and os.read(entered_r, 1) == b"R", "blocked thunk never ran"
                resume.set()
                a.join(10)
                assert not a.is_alive(), "A waited beyond its execution/cleanup budget"
            finally:
                resume.set()
                # B cannot finish the success case until A has returned. On the
                # old EOF-gated collector this forces A to report a false TIMEOUT.
                os.write(release_w, b"G")
                a.join(10)
                if b.ident is not None:
                    b.join(20)
        assert not a.is_alive() and not b.is_alive(), "overlap workers survived"
        assert not errors, errors
        assert set(owners) == {"A", "B"}, owners
        assert all(owner.collected and emit._fixture_child_reaped(owner.pid)
                   for owner in owners.values()), "overlap guardian not collected"
        if mode == "success":
            assert owners["A"].status == 0 and not owners["A"].timed_out
        expected = {"A": "A-OK" if mode == "success" else "TIMEOUT", "B": "B-OK"}
        print("opf watchdog overlap:", mode, results, "expected", expected)
        assert results == expected, results
    finally:
        os.close(release_r)
        os.close(release_w)
        os.close(entered_r)
        os.close(entered_w)
    return EXIT_OK


def _watchdog_regression_self_test():
    """R10: startup/collection/exit bounds and the registered runner under hostile inherited state."""
    import signal
    import subprocess
    from _opf_emit import run_status_owned
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        print("opf watchdog regressions: FAIL (unowned SIGCHLD disposition)")
        return EXIT_FINDING
    if not hasattr(os, "register_at_fork") or not hasattr(signal, "pthread_sigmask"):
        print("opf watchdog regressions: FAIL (required POSIX facilities unavailable)")
        return EXIT_FINDING
    prefix = ("import sys; sys.path.insert(0, " + repr(str(Path(__file__).resolve().parent))
              + "); import opf; ")
    cases = [(mode, prefix + "return opf._watchdog_deadline_case(" + repr(mode) + ")", 10)
             for mode in ("delayed-start", "stuck-start", "pipe-stall", "exit-stall",
                          "transient-census")]
    cases.extend(("overlap-" + mode,
                  prefix + "return opf._watchdog_overlap_case(" + repr(mode) + ")", 60)
                 for mode in ("success", "timeout"))
    cases.extend((mode, prefix + "return opf._watchdog_safety_case(" + repr(mode) + ")", 15)
                 for mode in ("ignored-chld", "reaper-chld", "lost-reap", "lost-cleanup",
                              "high-fd", "huge-timeout", "fork-error", "poll-error", "missing-reap",
                              "liveness-permission"))
    cases.extend((label + "-" + disposition,
                  prefix + "return opf._watchdog_launcher_case("
                  + repr(label) + ", " + repr(disposition) + ")", 30)
                 for label in ("isolation", "regression", "shared")
                 for disposition in ("ignored", "handler"))
    cases.extend(("completion-" + mode,
                  prefix + "return opf._watchdog_completion_case(" + repr(mode) + ")", 40)
                 for mode in ("audit-ignore", "reaper", "nonce", "fixture-id", "status",
                              "early-exit", "cleanup-reaped", "cleanup-cancel", "premature-exit",
                              "nested-timeout", "nested-cancel", "no-signal-echild",
                              "guardian-error", "empty-children", "bounded-diagnostics",
                              "cleanup-budget", "deadline-flips", "subject-setup",
                              "overdue-success", "launch-ownership", "launch-cancel",
                              "fd-hygiene-total", "nested-keep", "fd-census",
                              "subject-gc", "guardian-preload", "subject-receipt",
                              "subject-ack", "subject-orphan", "pdeathsig",
                              "escalation-subject", "escalate-reaped",
                              "poll-collected", "close-cancel",
                              "escalate-degraded", "poll-masked",
                              "unpinned-kill"))
    # Resolve the real registration, without recursively invoking this regression runner.
    # Both inherited disposition and mask are hostile; the outer runner's state is untouched.
    cases.append(("blocked-isolation", prefix
                  + "import signal; signal.signal(signal.SIGALRM, signal.SIG_IGN); "
                  + "signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM}); "
                  + "rc = opf._bootstrap(); "
                  + "return rc if rc else dict(opf._self_tests())['opf-watchdog-isolation']()",
                  31 * 180 + 30))                       # the isolation matrix's full budget plus launch margin
    failed = False
    for label, code, timeout in cases:
        try:
            result = run_status_owned([sys.executable, "-I", "-B", "-c", code],
                                    fixture_id="watchdog/" + label,
                                    capture_output=True, text=True, timeout=timeout)
            ok, detail = result.returncode == EXIT_OK, result.stdout + result.stderr
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            ok, detail = False, str(exc)
        except subprocess.TimeoutExpired:
            ok, detail = False, "fixture exceeded its independent process bound"
        print("opf watchdog regression:", label, "PASS" if ok else "FAIL", detail)
        failed = failed or not ok
    return EXIT_FINDING if failed else EXIT_OK


def _cmd_render(rest):
    """`opf render [--root DIR] (--check | --write)`: the store render verb.

    The READ-ONLY `--check` half forwards to the U4 engine `_opf_views.render`, whose 0/1/2 contract is
    exactly the required one (0 clean, 1 drift, 2 cannot-evaluate; a NOT-ADOPTED root reports NOT APPLICABLE
    and exits 0, the pack's own `--root .` case). The mutating `--write` half (VC-4/PR-C) gathers the inert
    git-derived observations caller-side (_opf_observe.gather over the RESOLVED store, exactly as doctor does)
    and hands them to the same engine, which composes the store-integrity gate and permits the write only
    when source integrity is sound, printing the findings/cannot-evaluates and returning 2 (writing nothing)
    otherwise, so
    a write can never read as a silent no-op. Exactly one of `--check`/`--write` is required: a bare
    `opf render` is a usage error (a preview never defaults into a write). The parser is the house fail-closed
    idiom (unknown token, an empty or option-looking or duplicate --root value -> exit 2), matching
    _opf_views.render's own parser."""
    root = None
    mode = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("--check", "--write"):
            if mode is not None:
                print("opf render: give exactly one of --check / --write", file=sys.stderr)
                return EXIT_MALFORMED
            mode = "check" if tok == "--check" else "write"
            i += 1
        elif tok == "--root":
            if i + 1 >= len(rest):
                print("opf render: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf render: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf render: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf render: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    if mode is None:
        print("opf render: give exactly one of --check / --write", file=sys.stderr)
        return EXIT_MALFORMED
    if mode == "write":
        # The mutating --write half composes the U6 store-integrity gate in the U4 engine. Resolve here to
        # gather the inert git-derived observations (tracked, actual_remote, prior) the gate consumes; the
        # engine re-resolves (idempotent) and owns the NOT-ADOPTED (0) / non-resolved (2) messaging and the
        # gate itself, so a NOT-ADOPTED or unresolved root needs no observations and never writes.
        argv = ["--write"] if root is None else ["--root", root, "--write"]
        try:
            res = _opf_store.resolve_store(Path(os.path.abspath(root if root is not None else ".")))
        except Exception as exc:  # noqa: BLE001  a resolver escape is cannot-evaluate, never a write
            print("opf render: cannot evaluate: unexpected error resolving the store ({!r}); failing closed "
                  "to exit 2".format(exc), file=sys.stderr)
            return EXIT_MALFORMED
        obs = None
        if res.status == _opf_store.RESOLVED:
            try:
                obs, notes = _opf_observe.gather(res)
            except Exception as exc:  # noqa: BLE001  a gather escape must not become a silent write; fail closed
                print("opf render: cannot evaluate: unexpected error gathering git observations ({!r}); "
                      "failing closed to exit 2".format(exc), file=sys.stderr)
                return EXIT_MALFORMED
            for note in notes:
                # Surface each honest observation gap so the gate's cannot-evaluate reads as an explained
                # disclosure, not silent store corruption (the doctor idiom).
                print("opf render: note: {}".format(note))
        try:
            return _opf_views.render(argv, observations=obs)
        except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
            print("opf render: cannot evaluate: unexpected error in the render write ({!r}); failing closed "
                  "to exit 2".format(exc), file=sys.stderr)
            return EXIT_MALFORMED
    argv = ["--check"] if root is None else ["--root", root, "--check"]
    # Class-width backstop: the render dispatch forwards the U4 engine's defined 0/1/2 contract unchanged;
    # any residual, unforeseen error from it routes to a located cannot-evaluate (exit 2), never an uncaught
    # exit-1 escape. KeyboardInterrupt/SystemExit are BaseException and stay uncaught.
    try:
        return _opf_views.render(argv)
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
        print("opf render: cannot evaluate: unexpected error in the render check ({!r}); failing closed to "
              "exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED


def _cmd_absorb(rest):
    """`opf absorb [--root DIR] [--covers TOKEN] [--freeze-digest]`: the read-only CHANGELOG.md drafter.

    Forwards to the OPF-CHANGELOG-ABSORB engine `_opf_absorb.run`, whose 0/2 contract is exactly the
    required one (0 draft or NOT APPLICABLE, 2 cannot-evaluate; a NOT-ADOPTED root reports NOT APPLICABLE and
    exits 0, the pack's own `--root .` case). The verb is READ-ONLY: the draft (or, with `--freeze-digest`,
    the freeze digest of the curated entry) goes to stdout and the framing to stderr, and the engine writes
    nothing, so there is no stage-then-promote surface. The parser is the house fail-closed idiom (unknown
    token, an empty or option-looking or duplicate --root/--covers value -> exit 2), matching _cmd_render."""
    root = None
    covers = None
    freeze = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--freeze-digest":
            freeze = True
            i += 1
        elif tok == "--root":
            if i + 1 >= len(rest):
                print("opf absorb: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf absorb: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf absorb: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        elif tok == "--covers":
            if i + 1 >= len(rest):
                print("opf absorb: --covers requires a value", file=sys.stderr)
                return EXIT_MALFORMED
            if covers is not None:
                print("opf absorb: --covers given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf absorb: --covers requires a non-empty value, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            covers = val
            i += 2
        else:
            print("opf absorb: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    covers_val = covers if covers is not None else _opf_absorb.UNRELEASED
    mode = "freeze-digest" if freeze else "draft"
    # Class-width backstop: the dispatch forwards the engine's defined 0/2 contract unchanged; any residual
    # error routes to a located cannot-evaluate (exit 2), never an uncaught exit-1 escape.
    try:
        return _opf_absorb.run(root if root is not None else ".", covers_val, mode)
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
        print("opf absorb: cannot evaluate: unexpected error in the drafter ({!r}); failing closed to "
              "exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED


def _doctor_report(result):
    """Print a CONCISE doctor report: the ordered per-check verdict map, then the findings and
    cannot-evaluates, then a one-line residual count. NO diff-style dump (no-console-diff-dumps): this is a
    structured verdict list, not a wall of before/after lines."""
    print("opf doctor: store integrity: {}".format(result.status))
    for cid, verdict in result.checks.items():
        print("  {}: {}".format(cid, verdict))
    for f in result.findings:
        print("  FINDING: {}".format(f))
    for c in result.cannot_evaluate:
        print("  CANNOT-EVALUATE: {}".format(c))
    if result.triage:
        print("  partial-import triage entries: {}".format(len(result.triage)))
    print("  residuals (disclosed by-design, not gradeable): {}".format(len(result.residuals)))


def _cmd_doctor(rest):
    """`opf doctor [--root DIR]`: the store-integrity verb.

    Doctor RESOLVES the store at --root (default: the cwd product repository root), gathers the inert
    git-derived observations (_opf_observe.gather: tracked, actual_remote, prior), and runs the U6 whole-store
    integrity engine (_opf_check.validate_store) over them, returning that engine's own 0/1/2 contract via
    _opf_check.exit_code (0 VALID, 1 INVALID, 2 CANNOT-EVALUATE). A NOT-ADOPTED root reports NOT APPLICABLE
    and exits 0 (the pack's own `--root .` case, mirroring render); any other non-RESOLVED status is a located
    cannot-evaluate (exit 2). Doctor is READ-ONLY: it makes no store change (SECI-preview-has-no-side-effects);
    the observation gather is git reads only. The parser is the house fail-closed idiom (unknown token, an
    empty or option-looking or duplicate --root value -> exit 2), matching _cmd_render's --root loop. Every
    residual escape from the resolver, the git gather, or the engine fails closed to exit 2 (never a false
    verdict), the same class-width backstop the render dispatch carries."""
    root = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--root":
            if i + 1 >= len(rest):
                print("opf doctor: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf doctor: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf doctor: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf doctor: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    root = root if root is not None else "."
    try:
        res = _opf_store.resolve_store(Path(os.path.abspath(root)))
    except Exception as exc:  # noqa: BLE001  fail-closed: a resolver escape is cannot-evaluate, never a verdict
        print("opf doctor: cannot evaluate: unexpected error resolving the store at {!r} ({!r}); failing "
              "closed to exit 2".format(root, exc), file=sys.stderr)
        return EXIT_MALFORMED
    if res.status == _opf_store.NOT_ADOPTED:
        print("opf doctor: NOT APPLICABLE ({})".format(res.detail))
        return EXIT_OK
    if res.status != _opf_store.RESOLVED:
        print("opf doctor: cannot evaluate: {}".format(res.detail), file=sys.stderr)
        return EXIT_MALFORMED
    try:
        obs, notes = _opf_observe.gather(res)
    except Exception as exc:  # noqa: BLE001  a gather escape must not become a false verdict; fail closed
        print("opf doctor: cannot evaluate: unexpected error gathering git observations ({!r}); failing "
              "closed to exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    for note in notes:
        # Surface each honest observation gap so an adopter's CI does not misread it as store corruption.
        print("opf doctor: note: {}".format(note))
    try:
        result = _opf_check.validate_store(res, observations=obs)
    except Exception as exc:  # noqa: BLE001  the engine contracts never to raise; a residual escape fails closed
        print("opf doctor: cannot evaluate: unexpected error validating the store ({!r}); failing closed to "
              "exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    _doctor_report(result)
    return _opf_check.exit_code(result)


_INIT_MAX_ENTRIES = 4096
_INIT_MAX_DEPTH = 32
_INIT_MAX_NAME_BYTES = 1 << 20


def _init_kind(st):
    if stat.S_ISDIR(st.st_mode):
        return "directory"
    if stat.S_ISREG(st.st_mode):
        return "file"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    return "special"


def _init_inventory(root_fd):
    """Inventory .working without following links, including hidden entries and empty directories.

    Bounds cover entry count, accumulated path bytes, and directory depth. An exceeded bound or
    unreadable entry produces an explicitly incomplete report and refuses initialization. This is
    an observation, not a filesystem snapshot: concurrent changes after enumeration remain possible.
    """
    journal = _opf_store._journal
    report = {"entries": [], "complete": False}
    name_bytes = 0

    def add(relpath, st):
        nonlocal name_bytes
        name_bytes += len(os.fsencode(relpath))
        if (len(report["entries"]) >= _INIT_MAX_ENTRIES
                or name_bytes > _INIT_MAX_NAME_BYTES):
            raise RuntimeError("foreign inventory exceeds entry/path-byte bounds")
        report["entries"].append({"path": relpath, "kind": _init_kind(st)})

    def walk(fd, prefix, depth):
        with os.scandir(fd) as entries:
            for entry in entries:
                relpath = prefix + "/" + entry.name
                st = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                add(relpath, st)
                if stat.S_ISDIR(st.st_mode):
                    if depth >= _INIT_MAX_DEPTH:
                        raise RuntimeError("foreign inventory exceeds directory-depth bound")
                    child_fd = os.open(
                        entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        walk(child_fd, relpath, depth + 1)
                    finally:
                        os.close(child_fd)

    try:
        working = _opf_store.WORKING_DIRNAME
        st = journal._lstat_at(root_fd, working)
        if st is not None:
            if not stat.S_ISDIR(st.st_mode):
                add(working, st)
            else:
                working_fd = os.open(
                    working, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                try:
                    walk(working_fd, working, 0)
                finally:
                    os.close(working_fd)
        report["complete"] = True
    except Exception as exc:  # noqa: BLE001  an incomplete inventory never licenses a write
        report["error"] = ascii(exc)
    report["entries"].sort(key=lambda row: row["path"])
    return report


def _init_git(git, root, args):
    """Reuse the existing explicit -C, scrubbed-environment, timeout-bounded git boundary."""
    result = _opf_observe._run_git(git, root, args)
    if not result.completed or result.rc != 0:
        raise RuntimeError("git preflight/read failed at {!r}: {}".format(
            str(root), result.err))
    return result.out


def _init_repo(root):
    """Confirm a real non-bare worktree at or above root, without requiring a commit."""
    git = _opf_observe._git_path()
    if git is None:
        raise RuntimeError("git preflight: git not found on PATH")
    args = ["rev-parse", "--is-inside-work-tree", "--is-bare-repository", "--show-toplevel"]
    raw = _init_git(git, root, args)
    lines = raw.split(b"\n")
    if (len(lines) != 4 or lines[:2] != [b"true", b"false"]
            or lines[-1] != b"" or not os.path.isabs(os.fsdecode(lines[2]))):
        raise RuntimeError("git preflight: root is not a confirmed non-bare worktree")
    repo = Path(os.path.abspath(os.fsdecode(lines[2])))
    if root != repo and repo not in root.parents:
        raise RuntimeError("git preflight: reported repository does not contain root")
    repo_fd = _opf_store._open_dir_nofollow(repo)
    os.close(repo_fd)
    if _init_git(git, repo, args) != raw:
        raise RuntimeError("git preflight: repository identity changed during confirmation")
    return git, repo


def _init_untracked(git, repo, root, paths):
    """Read the index, refusing unreadable state or already-tracked planned destinations.

    Checking .working also detects tracked sources whose working-tree copies were deleted. Those
    paths cannot honestly be described as newly untracked sources. No HEAD observation is needed.
    """
    prefix = root.relative_to(repo)
    scoped = sorted(str(prefix / path) for path in paths)
    tracked = _init_git(
        git, repo, ["--literal-pathspecs", "ls-files", "--cached", "-z", "--"] + scoped)
    if tracked:
        raise RuntimeError("planned destination already git-tracked: {!r}".format(
            os.fsdecode(tracked)))


def _ignore_file_candidates(prefix, paths):
    """Repo-relative .gitignore paths git consults when deciding whether the planned destinations are
    ignored: one per ancestor directory from the repository root down to each destination's own directory.
    A .gitignore in directory D governs paths under D, so every ancestor directory of a planned path is a
    candidate ignore source (git add reads them all)."""
    dirs = set()
    for path in paths:
        for parent in (prefix / path).parents:
            dirs.add(parent)
    candidates = set()
    for directory in dirs:
        posix = directory.as_posix()
        candidates.add(".gitignore" if posix == "." else posix + "/.gitignore")
    return sorted(candidates)


def _init_unignored(git, repo, root, paths):
    """Refuse a planned destination git would ignore: an ignored store cannot be staged or discovered.

    check-ignore reports at least one ignored path with rc 0, none ignored with rc 1, and an error
    with rc >= 128. The rc drives the three-way decision; the output is only for the diagnostic. Paths
    are passed as arguments (git check-ignore accepts neither -z without --stdin, which the run helper has
    no channel for, nor --literal-pathspecs, which it rejects as unsupported magic). Each scoped path is
    given a literal "./" prefix so a leading colon or other pathspec-magic sigil in an adopter-supplied
    --root prefix is read as a path, not as magic: without it a ":name/..." prefix has its colon consumed
    as an empty magic signature and the check silently bypasses (rc 1, the store looks unignored), while a
    recognized short-magic letter over-refuses (rc >= 128). The prefix is a plain string because Path
    would normalize "./x" back to "x". The output is then one path per line over the controlled,
    newline-free internal destinations.

    OPF-D2B: the probe runs under _opf_observe._run_git_config_discovery, which lets git DISCOVER the
    adopter's real global and system configuration through its default locations (unlike the default scrubbed
    observer environment, which neutralizes it), so a store ignored ONLY by the adopter's global or system
    core.excludesFile is caught here, matching what the adopter's own `git add` would honour
    (guard-input-soundness). Env-based config overrides are dropped and trace/fsmonitor are forced off, so no
    reachable configuration can turn the read-only probe into a write or a launched process. Lazy fetching is
    forced off so a missing indexed ignore blob cannot trigger a promisor fetch that would reach
    core.sshCommand. DISCLOSED RESIDUAL (disclose-guard-residuals): this rc-only check reads ignore rules
    exactly as git add does when git add can read them too. For an ignore input git cannot READ (a
    permission-denied core.excludesFile, ~/.config/git/ignore, or .git/info/exclude), git add is equally unable
    to read it and stages the destination, so reading not-ignored here matches git add and the store is never
    silently ignored. The one input where that parity BREAKS is a skip-worktree (or otherwise unmaterialized)
    .gitignore whose blob is absent in a partial clone: this probe forces lazy fetch off and reads no-rule, but
    the adopter's own git add fetches the blob and can then ignore the store. That case is NOT left as a
    residual: after an rc-1 result the availability of every applicable indexed .gitignore blob is checked
    (_opf_observe.indexed_ignore_availability), and an unavailable one is a cannot-evaluate that REFUSES rather
    than passing (guard-input-soundness). A fail-closed treatment of the unreadable-FILE arms above is
    deliberately not attempted, because git and the caller resolve config paths differently and refusing there
    would over-refuse a case git add itself skips. Called directly
    (not via _init_git, which raises on rc != 0) because rc 1 is the success case here; a timeout, launch
    failure, or unexpected rc fails closed and refuses. Matching `git add`, a benign git diagnostic on an
    rc-1 (not-ignored) result is not itself a refusal.
    """
    prefix = root.relative_to(repo)
    scoped = sorted("./" + str(prefix / path) for path in paths)
    result = _opf_observe._run_git_config_discovery(
        git, repo, ["check-ignore", "--"] + scoped)
    if not result.completed:
        raise RuntimeError(
            "git preflight: could not evaluate ignore status for planned destinations ({})".format(
                result.err))
    if result.rc == 0:
        ignored = [p for p in os.fsdecode(result.out).splitlines() if p]
        raise RuntimeError("planned destination is git-ignored: {!r}".format(ignored))
    if result.rc != 1:
        raise RuntimeError("git preflight: check-ignore failed (rc={}): {}".format(
            result.rc, result.err))
    # rc 1 (not ignored) is sound only when every applicable .gitignore is answerable WITHOUT a promisor
    # fetch. In a partial clone a skip-worktree (or otherwise unmaterialized) .gitignore whose blob is absent
    # reads as no-rule here (lazy fetch is forced off), yet the adopter's own `git add` fetches that blob and
    # can then silently ignore the store; an available blob is read identically by both (parity) and a
    # checked-out .gitignore is read from disk by both. So a missing indexed ignore blob is a cannot-evaluate
    # we refuse, not a clean pass (guard-input-soundness).
    unavailable = _opf_observe.indexed_ignore_availability(
        git, repo, _ignore_file_candidates(root.relative_to(repo), paths))
    if unavailable:
        raise RuntimeError(
            "git preflight: an indexed .gitignore blob is unavailable in this partial clone, so ignore "
            "status cannot be determined; `git add` would fetch it and could silently ignore the store "
            "({}). Check out or fetch the blob and retry.".format(sorted(unavailable)))


def _init_same_root(root, root_fd):
    check_fd = _opf_store._open_dir_nofollow(root)
    try:
        current = os.fstat(check_fd)
        opened = os.fstat(root_fd)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise RuntimeError("root changed since its contained directory was opened")
    finally:
        os.close(check_fd)


def _init_create(root_fd, relpath, data):
    """Create through the existing shared O_EXCL primitive; never replace or remove an entry.

    _journal._recreate_file uses descriptor-relative O_CREAT|O_EXCL|O_NOFOLLOW, writes and fsyncs
    the new inode, and performs no rollback. A write failure can therefore leave a partial file.
    Parent handles prevent symlink redirection; concurrent directory renames are not serialized.
    """
    journal = _opf_store._journal
    pfd, name = journal._open_parent(root_fd, relpath)
    try:
        try:
            journal._recreate_file(pfd, name, data, 0o644)
            os.fsync(pfd)
        except Exception as exc:
            raise RuntimeError("create-only publication refused {!r}: {!r}".format(
                relpath, exc)) from exc
    finally:
        os.close(pfd)


def _init_observed(root_fd, directories, payloads):
    """Report the planned paths beneath the opened root, without inferring entry ownership."""
    journal = _opf_store._journal
    rows = []
    for relpath in list(directories) + list(payloads):
        row = {"path": relpath}
        try:
            st = journal._lstat_contained(root_fd, relpath)
            if st is None:
                row["state"] = "absent"
            elif relpath in directories:
                row["state"] = _init_kind(st)
            elif not stat.S_ISREG(st.st_mode):
                row["state"] = _init_kind(st)
            elif st.st_size != len(payloads[relpath]):
                row["state"] = "different-size"
                row["bytes"] = st.st_size
            else:
                pfd, name = journal._open_parent(root_fd, relpath)
                try:
                    data, opened = journal._read_at(
                        pfd, name, relpath, cap=len(payloads[relpath]) + 1)
                finally:
                    os.close(pfd)
                row["state"] = (
                    "matches-payload" if opened.st_nlink == 1 and data == payloads[relpath]
                    else "different-content-or-link-count")
        except Exception as exc:  # noqa: BLE001  unknown is never reported as absent or complete
            row["state"] = "cannot-evaluate"
            row["error"] = ascii(exc)
        rows.append(row)
    return rows


def _cmd_init(rest):
    """Create validated store sources and a pointer, without git writes or rendering.

    Preflight is read-only. Publication is create-only and deliberately not transactional: a
    later failure reports observed planned paths and leaves them for review. Enumeration, root
    identity checks, and final rereads do not serialize concurrent writers or directory renames.
    No lock, lease, rollback, adoption policy, or whole-store success verdict is supplied here.
    """
    root = None
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--root":
            if i + 1 >= len(rest):
                print("opf init: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf init: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf init: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        else:
            print("opf init: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    root = root if root is not None else "."
    root_fd = None
    directories = []
    payloads = {}
    publishing = False
    stage = "preflight"
    try:
        import shlex
        import _opf_init

        journal = _opf_store._journal
        journal.require_containment()
        root = Path(os.path.abspath(root))
        root_fd = _opf_store._open_dir_nofollow(root)
        git, repo = _init_repo(root)

        inventory = _init_inventory(root_fd)
        if inventory["entries"] or not inventory["complete"]:
            print(json.dumps(dict(inventory, event="foreign-content", root=str(root)),
                             sort_keys=True))
        if not inventory["complete"]:
            raise RuntimeError("foreign .working inventory incomplete; refusing initialization")

        # Resolution detects stores through either pointer and through default discovery. Inventory
        # remains independent so malformed stores and foreign content also receive a concrete report.
        res = _opf_store.resolve_store(root)
        pointers = [
            name for name in (_opf_store.POINTER_REL, _opf_store.LOCAL_POINTER_REL)
            if journal._lstat_at(root_fd, name) is not None
        ]
        if pointers:
            raise RuntimeError("existing pointer(s): {}".format(", ".join(pointers)))
        if res.status == _opf_store.RESOLVED:
            raise RuntimeError("existing store: {}".format(res.detail))
        if inventory["entries"]:
            raise RuntimeError("foreign .working content; {}; refusing initialization".format(
                res.detail))
        if res.status != _opf_store.NOT_ADOPTED:
            # The resolver also refuses an existing, empty .working directory. Do not reinterpret a
            # CANNOT-EVALUATE result as permission to initialize a partial store.
            raise RuntimeError("store resolution refused: {}".format(res.detail))

        stage = "building and validating source payloads"
        working = _opf_store.WORKING_DIRNAME
        machine = working + "/" + _opf_store.DEFAULT_MACHINE_SUBDIR
        documents = [
            (_opf_store.MANIFEST_NAME, _opf_init.build_manifest()),
            (_opf_check.COUNTERS_NAME, _opf_init.build_counters()),
            (_opf_check.VERSION_NAME, _opf_init.build_version()),
            (_opf_check.WORKLOG_NAME, _opf_init.build_worklog()),
        ]
        documents.extend(
            (name + _opf_check.INDEX_SUFFIX, _opf_init.build_index(name))
            for name in _opf_init.INDEX_TYPES)
        payloads = {
            machine + "/" + name: text.encode("utf-8") for name, text in documents
        }
        payloads[_opf_store.POINTER_REL] = _opf_emit.emit_checked(
            {"store": {"target": "dir:."}}).encode("utf-8")
        if journal._lstat_at(root_fd, "CHANGELOG.md") is None:
            payloads["CHANGELOG.md"] = b"# Changelog\n"
        directories = [working, machine]

        stage = "checking the destination inventory"
        for relpath in directories + list(payloads):
            journal._check_rel(relpath)
            if journal._lstat_contained(root_fd, relpath) is not None:
                raise RuntimeError("destination already exists: {!r}".format(relpath))
        if journal._lstat_at(root_fd, _opf_store.LOCAL_POINTER_REL) is not None:
            raise RuntimeError("existing pointer: " + _opf_store.LOCAL_POINTER_REL)
        index_paths = set(payloads) | {working, _opf_store.LOCAL_POINTER_REL}
        _init_untracked(git, repo, root, index_paths)
        # The ignore check is scoped to the destinations init actually CREATES (store dirs plus the
        # committed pointer, the machine sources, and the optional CHANGELOG); the local pointer that
        # index_paths carries for _init_untracked's prior-state detection is a path init never creates
        # and adopters legitimately gitignore, so it must not make init refuse.
        _init_unignored(git, repo, root, set(directories) | set(payloads))
        _init_same_root(root, root_fd)

        publishing = True
        for relpath in directories:
            stage = "creating directory " + relpath
            pfd, name = journal._open_parent(root_fd, relpath)
            try:
                os.mkdir(name, 0o755, dir_fd=pfd)
                os.fsync(pfd)
            finally:
                os.close(pfd)
        for relpath, data in payloads.items():
            stage = "creating " + relpath
            _init_same_root(root, root_fd)
            _init_create(root_fd, relpath, data)

        stage = "observing published source paths"
        observed = _init_observed(root_fd, directories, payloads)
        if any(row["state"] != (
                "directory" if row["path"] in directories else "matches-payload")
               for row in observed):
            raise RuntimeError("published paths do not match the validated payloads")
        final_inventory = _init_inventory(root_fd)
        expected_working = {machine} | {
            path for path in payloads if path.startswith(working + "/")
        }
        if (not final_inventory["complete"]
                or {row["path"] for row in final_inventory["entries"]} != expected_working):
            print(json.dumps(dict(final_inventory, event="post-publish-inventory", root=str(root)),
                             sort_keys=True))
            raise RuntimeError("working inventory changed during publication")
        if journal._lstat_at(root_fd, _opf_store.LOCAL_POINTER_REL) is not None:
            raise RuntimeError("local pointer appeared during publication")
        _init_same_root(root, root_fd)
        _init_untracked(git, repo, root, index_paths)

        print("opf init: store SOURCES created and {} pointer written.".format(
            _opf_store.POINTER_REL))
        print(json.dumps({"event": "created", "root": str(root), "paths": list(payloads)},
                         sort_keys=True))
        print("opf init: these created paths are NOT yet git-tracked (final index read).")
        print("Review the created files, then stage the reviewed paths:")
        print("  git -C {} --literal-pathspecs add -- {}".format(
            shlex.quote(str(root)), " ".join(shlex.quote(path) for path in payloads)))
        print("opf init: ignore eligibility, including the global and system core.excludesFile, was checked")
        print("  before creation; a later ignore or config change can still affect staging (git add -f).")
        print("Commit the reviewed init paths, then materialize the Markdown views:")
        print("  opf render --write --root {}".format(shlex.quote(str(root))))
        print("opf init: exit 0 means valid sources were created; tracking and rendering are pending.")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  includes InitError and residual I/O/import errors
        print("opf init: cannot evaluate at {} during {}: {}; exit 2".format(
            ascii(str(root)), stage, ascii(exc)), file=sys.stderr)
        if publishing and root_fd is not None:
            try:
                _init_same_root(root, root_fd)
                binding = "same-root"
            except Exception as binding_exc:
                binding = "cannot-confirm-root: " + ascii(binding_exc)
            print(json.dumps({
                "event": "partial-publication",
                "root": str(root),
                "scope": "opened-root-descriptor",
                "root_binding": binding,
                "paths": _init_observed(root_fd, directories, payloads),
            }, sort_keys=True), file=sys.stderr)
            print("opf init: publication may be partial; review the observed state. No rollback performed.",
                  file=sys.stderr)
        else:
            print("opf init: preflight refused; no publication attempted.", file=sys.stderr)
        return EXIT_MALFORMED
    finally:
        if root_fd is not None:
            _opf_store._journal._close_fd_quietly(root_fd)


# --- opf upgrade: the store-schema upgrade to the tooling spec_version (spec 9.2) ---------------------

# The single 1.0.0 -> 1.1.0 upgrade this build implements. maintainer_decision and preference_pattern
# baseline (they were module-tier in 1.0.0); contribution is net-new; the decision_support module is
# retired (spec 8.1 note, spec 9.2). The delta is enumerated so the postcondition can assert the model
# diff equals EXACTLY it and nothing else (fail-closed on any stray change).
# _UPGRADE_TO is the literal the tooling implements; _cmd_upgrade asserts it equals the live
# _opf_store.SUPPORTED_SPEC_VERSION (bound only after _bootstrap), so a future spec bump cannot let this
# constant silently drift from the roster.
_UPGRADE_FROM = "1.0.0"
# OPF-D2B PR3a (PD-D2B-PR3-SCHEMA decision 5): base spec 1.2.0 admits the managed bootstrap provenance
# `.working/toml/init.toml` a coupled init writes. The 1.1.0 -> 1.2.0 schema delta is the spec_version
# bump ALONE: a 1.1.0 (D2a) store gains no init.toml (no provenance is ever fabricated for it), and a 1.0.0
# store takes the full 1.0.0 schema delta straight to 1.2.0. Declared views are then regenerated.
_UPGRADE_MID = "1.1.0"
_UPGRADE_TO = "1.2.0"
_UPGRADE_NEW_TYPES = ("contribution", "maintainer_decision", "preference_pattern")
_UPGRADE_RETIRED_MODULE = "decision_support"
_UPGRADE_NEW_VIEWS = ("CONTRIBUTIONS.md", "DECISIONS.toml")
# The 1.0.0 -> 1.1.0 delta also WIDENS the existing DECISIONS.md composed view's source set: the two new
# baseline decision types join the two 1.0.0 sources, so the migrated view matches the 1.1.0 required set
# (spec 9.2). Without this, a genuine 1.0.0 store's 2-source DECISIONS.md stays 2-source after the delta and
# fails the render/doctor gate. DECISIONS.toml is a NET-NEW view (in _UPGRADE_NEW_VIEWS) and already carries
# the full four-source set from NAMED_VIEWS.
_UPGRADE_WIDENED_VIEW = "DECISIONS.md"
_UPGRADE_VIEW_FROM_SOURCES = ("pending_decision", "autonomous_decision")

# FIX5 forward-drift PIN (guard-input-soundness): the 1.0.0-valid [modules] vocabulary is the four 1.0.0
# baseline modules PLUS the one module this 1.0.0 -> 1.1.0 delta retires (decision_support); their union is
# exactly the merge-base (1c90fbb) _opf_store.KNOWN_MODULES. _upgrade_plan DERIVES that set from the LIVE
# _opf_store.KNOWN_MODULES at the point of use, which is correct today but UNPINNED: a future KNOWN_MODULES
# edit would silently shift what counts as a valid 1.0.0 module. This FROZEN set pins the intended 1.0.0
# vocabulary (a plain literal, since _opf_store is bound only after _bootstrap); _upgrade_plan reconciles
# the live derivation against it and fails closed on any divergence, and check_opf_upgrade.py binds the two,
# so a future drift fails a gate rather than re-vocabularying the check unseen.
_VALID_1_0_0_MODULES = frozenset({
    "governance", "delivery_assurance", "operational_policy", "concurrent_operation", "decision_support"})


class _UpgradeError(Exception):
    """A fail-closed upgrade refusal carrying the operator-facing reason (mapped to exit 2)."""


def _upgrade_read_bytes(root_fd, relpath, control=False):
    """Read a contained store file's raw bytes no-follow; None when the path is absent. A control file
    (the manifest) is read singly-linked (a hardlink to an out-of-tree victim is refused)."""
    journal = _opf_store._journal
    try:
        data, _st = journal._read_contained(root_fd, relpath, require_single_link=control)
    except journal.JournalError as exc:
        text = str(exc)
        if "cannot read contained file" in text and ("No such file" in text or "FileNotFound" in text):
            return None
        raise _UpgradeError("cannot read {!r} ({})".format(relpath, exc))
    return data


def _upgrade_replace(root_fd, relpath, data):
    """Atomically replace an EXISTING contained regular file with `data` (bytes), no-follow, preserving
    the destination's mode: a fresh O_EXCL temp beneath the same parent fd is written and atomically
    renamed over the entry (never an O_TRUNC of the name), the same reopen-TOCTOU-safe idiom as the U4
    view writer, minus its render write-gate flag (this is the schema-upgrade writer, not a render)."""
    journal = _opf_store._journal
    pfd, name = journal._open_parent(root_fd, relpath)
    try:
        st = journal._lstat_at(pfd, name)
        if st is None or not stat.S_ISREG(st.st_mode):
            raise _UpgradeError("refusing to rewrite {!r}: destination is not an existing regular file "
                                "(a symlink or special file is never followed; fail-closed)".format(relpath))
        tmpname = ".{}.opf-upgrade.{}.{}".format(name, os.getpid(), os.urandom(8).hex())
        fd = os.open(tmpname, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=pfd)
        renamed = False
        try:
            try:
                os.fchmod(fd, stat.S_IMODE(st.st_mode))
                journal._write_all(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(tmpname, name, src_dir_fd=pfd, dst_dir_fd=pfd)
            renamed = True
            os.fsync(pfd)
        finally:
            if not renamed:
                try:
                    os.unlink(tmpname, dir_fd=pfd)
                except OSError:
                    pass
    finally:
        os.close(pfd)


def _upgrade_create_index(root_fd, relpath, data):
    """Create a missing empty index file create-only (O_EXCL, no-follow); an already-present index is
    LEFT untouched (governance-enabled MD/PP whose records are preserved byte-for-byte, spec 9.2).
    Returns True when this call created the file, False when it already existed."""
    journal = _opf_store._journal
    pfd, name = journal._open_parent(root_fd, relpath)
    try:
        if journal._lstat_at(pfd, name) is not None:
            return False
        journal._recreate_file(pfd, name, data, 0o644)
        os.fsync(pfd)
        return True
    finally:
        os.close(pfd)


def _upgrade_plan(manifest_model, counters_model):
    """Apply EXACTLY the spec-9.2 1.0.0 -> 1.1.0 allowed delta to the parsed manifest and counters models,
    returning (new_manifest, new_counters, added_namespaces, origin). It validates UP FRONT the 1.0.0-shape
    preconditions it can cheaply check on the PARSED models -- the required/optional table shapes, the base
    table token and spec_version, KNOWN module keys with boolean values, the module<->baseline-type coupling,
    and the pre-declared type/view rows -- and REFUSES any that fail (a _UpgradeError, fail-closed). It is NOT
    a complete 1.0.0 doctor: a 1.0.0 store invalid in a way these preconditions do not inspect (e.g. a missing
    type row, or a type carrying a wrong or non-normative namespace) is caught AFTER mutation by the render /
    final-doctor gate; recovery restores tracked paths from HEAD and removes upgrade-created files
    within the checked scope -- the no-complete-pre-doctor
    residual disclosed on _cmd_upgrade. It is ORIGIN-AWARE: a governance-
    or decision_support-enabled 1.0.0 store already declares maintainer_decision / preference_pattern as a
    module-tier row (G1/G2), so the delta ADDS only the now-baseline types not already present and preserves
    a pre-declared row untouched; DECISIONS.md and the decision_support [modules] key are both OPTIONAL at
    1.0.0 (G3/G4), so an origin that omits them is migrated without inventing them. `_upgrade_postcondition`
    asserts the manifest AND counters model diffs equal EXACTLY the allowed delta (value-exact), so a stray
    mutation, a flipped retained boolean, a mutated retained row, or a lost high-water can never slip
    through."""
    import copy
    # The 1.0.0 input carries the RETIRED base table [devprocess] (PRIOR_STANDARD_TOKEN); the OPFiles
    # rebrand (1.1.0) renames it to [opf] (STANDARD_TOKEN) as part of this same allowed delta (spec 9.2).
    _prior_base = _opf_store.PRIOR_STANDARD_TOKEN
    # [devprocess] and [types] are REQUIRED for a migratable 1.0.0 store. [modules] and [views] are
    # OPTIONAL at 1.0.0: the merge-base doctor grades an ABSENT table valid-empty (_validate_modules(None)
    # / _validate_views(None) return CLEAN), so an origin that omits either migrates AS IF EMPTY rather
    # than being refused; a PRESENT-but-not-a-dict table stays a malformed refusal.
    for table in (_prior_base, "types"):
        if not isinstance(manifest_model.get(table), dict):
            raise _UpgradeError("manifest [{}] table is missing or malformed; not a store this "
                                "upgrade can migrate (fail-closed)".format(table))
    for table in ("modules", "views"):
        present = manifest_model.get(table)
        if present is not None and not isinstance(present, dict):
            raise _UpgradeError("manifest [{}] table is present but malformed (not a table); not a store "
                                "this upgrade can migrate (fail-closed)".format(table))
    if _opf_store.STANDARD_TOKEN in manifest_model:
        raise _UpgradeError("manifest already carries the renamed base table [{}]; store is not a clean "
                            "1.0.0 baseline (fail-closed)".format(_opf_store.STANDARD_TOKEN))
    if manifest_model[_prior_base].get("spec_version") != _UPGRADE_FROM:
        raise _UpgradeError("manifest spec_version is not {!r}; no known upgrade path".format(_UPGRADE_FROM))
    if manifest_model[_prior_base].get("standard") != _prior_base:
        raise _UpgradeError("manifest [{}].standard is not {!r}; not a store this upgrade migrates "
                            "(fail-closed)".format(_prior_base, _prior_base))
    if not isinstance(counters_model.get("counters"), dict):
        raise _UpgradeError("counters.toml [counters] table is missing or malformed (fail-closed)")

    modules = manifest_model.get("modules") or {}
    types = manifest_model["types"]
    views = manifest_model.get("views") or {}

    # --- ORIGIN FACTS (G1-G4): what this specific 1.0.0 origin family already carries. ---------------
    pre_declared = frozenset(t for t in _UPGRADE_NEW_TYPES if t in types)
    ds_key_present = _UPGRADE_RETIRED_MODULE in modules
    decisions_declared = _UPGRADE_WIDENED_VIEW in views
    origin = {"pre_declared": pre_declared, "ds_key_present": ds_key_present,
              "decisions_declared": decisions_declared}

    # --- PRECONDITIONS: refuse only a genuinely INVALID 1.0.0 input, fail-closed. --------------------
    # [modules] keys are OPTIONAL at 1.0.0 (default off, G3), so decision_support MAY be absent. Every
    # PRESENT module value must be a boolean: the merge-base doctor's _validate_modules grades a non-boolean
    # module value 1.0.0-INVALID, so a non-boolean on ANY module (not only the retired decision_support --
    # e.g. governance = "x") is refused upfront, fail-closed, keeping the docstring's up-front-validation
    # claim honest rather than silently carrying it through to a post-mutation
    # doctor failure. A doctor-VALID 1.0.0 store has only boolean module values, so this never rejects valid
    # input; every module key otherwise rides through untouched (the postcondition asserts value-exact).
    # Every [modules] KEY must be a KNOWN 1.0.0 module name (matching the merge-base _validate_modules, which
    # grades an unknown module key 1.0.0-INVALID). The 1.0.0-valid module vocabulary is the CURRENT baseline
    # module set PLUS the one module this delta RETIRES (decision_support), whose union is exactly the merge-
    # base KNOWN_MODULES; derived from the authoritative KNOWN_MODULES (guard-input-soundness), never a hard-
    # coded list. An unknown key (e.g. [modules].unknown_present) is refused UPFRONT, fail-closed, rather than
    # carried through to a post-mutation doctor failure -- keeping the docstring's up-front-validation claim
    # honest (a MISSING type row or WRONG namespace stays the render/doctor gate's job, disclosed on _cmd_upgrade).
    # FIX5: reconcile the LIVE derivation against the frozen pin (forward-drift guard). A KNOWN_MODULES edit
    # that shifts the 1.0.0 module vocabulary fails HERE (fail-closed), rather than silently re-scoping this
    # 1.0.0-validity check; the pin, not the live union, is then the vocabulary the check uses.
    _derived_1_0_0_modules = frozenset(_opf_store.KNOWN_MODULES) | {_UPGRADE_RETIRED_MODULE}
    if _derived_1_0_0_modules != _VALID_1_0_0_MODULES:
        raise _UpgradeError(
            "the derived 1.0.0 module vocabulary {} drifted from the pinned set {}; reconcile "
            "_VALID_1_0_0_MODULES with _opf_store.KNOWN_MODULES before upgrading (forward-drift pin, "
            "fail-closed)".format(sorted(_derived_1_0_0_modules), sorted(_VALID_1_0_0_MODULES)))
    _valid_1_0_0_modules = _VALID_1_0_0_MODULES
    for _mname in sorted(modules):
        if _mname not in _valid_1_0_0_modules:
            raise _UpgradeError("manifest [modules].{} is not a known 1.0.0 module (known: {}); not a valid "
                                "1.0.0 store (fail-closed)".format(
                                    _mname, ", ".join(sorted(_valid_1_0_0_modules))))
        if not isinstance(modules[_mname], bool):
            raise _UpgradeError("manifest [modules].{} is not a boolean; not a valid 1.0.0 store "
                                "(fail-closed)".format(_mname))
    # A now-baseline type PRE-DECLARED by a 1.0.0 module tier (G1/G2). contribution: no 1.0.0 tier
    # introduced it, so its presence is an impossible 1.0.0 shape -> always refuse. maintainer_decision:
    # only a governance-enabled store carried it; preference_pattern: only a decision_support-enabled
    # store; a pre-declared row whose gating module is not enabled is a module-inconsistent (1.0.0-INVALID)
    # shape, so refusing it keeps fail-closed on genuinely-invalid input. Each pre-declared row must be
    # EXACTLY {namespace = <normative>} (the 1.0.0 closed TYPE_KEYS + normative-namespace rule, G2).
    _pre_gate = {"contribution": None, "maintainer_decision": "governance",
                 "preference_pattern": _UPGRADE_RETIRED_MODULE}
    for tname in sorted(pre_declared):
        gate = _pre_gate[tname]
        if gate is None:
            raise _UpgradeError("manifest pre-declares baseline type {!r}, which no 1.0.0 module tier "
                                "introduced; an impossible 1.0.0 shape (fail-closed)".format(tname))
        if modules.get(gate) is not True:
            raise _UpgradeError("manifest declares type {!r} while its 1.0.0 module {!r} is not enabled; "
                                "a module-inconsistent 1.0.0 shape (fail-closed)".format(tname, gate))
        if types.get(tname) != {"namespace": _opf_store.BASELINE_TYPES[tname]}:
            raise _UpgradeError("manifest [types.{}] is not the exact 1.0.0 baseline row (namespace = "
                                "{!r}); not a clean 1.0.0 shape (fail-closed)".format(
                                    tname, _opf_store.BASELINE_TYPES[tname]))
    # SYMMETRIC module-coupling precondition (C-ROSTER, mirroring the merge-base 1.0.0 doctor). The loop
    # above refuses a DECLARED now-baseline type whose gating 1.0.0 module is not enabled; this refuses the
    # CONVERSE -- a 1.0.0 module that gates a now-baseline type (maintainer_decision<-governance,
    # preference_pattern<-decision_support) is ENABLED while that type's [types] row is ABSENT. Such an
    # origin is module-inconsistent, so the merge-base 1.0.0 doctor grades it NOT-VALID; without this the
    # delta would SILENTLY CURE it by adding the now-baseline row and exit 0, contradicting the docstring's
    # up-front-validation claim (this coupling IS one of the cheap parsed-model checks it promises). Refuse
    # it upfront, unmutated.
    for tname, gate in sorted(_pre_gate.items()):
        if gate is not None and modules.get(gate) is True and tname not in pre_declared:
            raise _UpgradeError("manifest enables 1.0.0 module {!r} but omits its required type {!r}; a "
                                "module-inconsistent 1.0.0 shape the delta must not silently cure "
                                "(fail-closed)".format(gate, tname))
    # The two NET-NEW 1.1.0 views could not exist at 1.0.0 (no 1.0.0 view renderer), so a store
    # pre-declaring either is 1.0.0-INVALID; refuse.
    for vname in _UPGRADE_NEW_VIEWS:
        if vname in views:
            raise _UpgradeError("manifest already declares view {!r}; store is not a clean 1.0.0 "
                                "baseline (fail-closed)".format(vname))
    # DECISIONS.md is OPTIONAL at 1.0.0 (G4): a store may omit it and stay valid, so an absent view is
    # neither widened nor created (M2). When DECLARED, its sources must be EXACTLY the two 1.0.0 decision
    # sources as a LIST with no duplicates and all strings (a doctor-VALID 1.0.0 store may order the pair
    # either way but never duplicates it, G4).
    if decisions_declared:
        _dv_old = views.get(_UPGRADE_WIDENED_VIEW)
        _srcs = _dv_old.get("sources") if isinstance(_dv_old, dict) else None
        if not (isinstance(_dv_old, dict) and isinstance(_srcs, list)
                and all(isinstance(s, str) for s in _srcs)
                and len(_srcs) == len(set(_srcs))
                and set(_srcs) == set(_UPGRADE_VIEW_FROM_SOURCES)):
            raise _UpgradeError("manifest [views.{!r}] is not the 1.0.0 baseline composed view (sources "
                                "must be the two 1.0.0 decision sources {}, no duplicates); not a clean "
                                "1.0.0 baseline (fail-closed)".format(
                                    _UPGRADE_WIDENED_VIEW, sorted(_UPGRADE_VIEW_FROM_SOURCES)))

    # --- DELTA APPLICATION (spec 9.2). --------------------------------------------------------------
    new_manifest = copy.deepcopy(manifest_model)
    # Rename the base table [devprocess] -> [opf] and its discovery token, and bump spec_version, all in
    # the one allowed delta (spec 9.2). The table body is otherwise carried over unchanged.
    _base = new_manifest.pop(_prior_base)
    _base["standard"] = _opf_store.STANDARD_TOKEN
    _base["spec_version"] = _UPGRADE_TO
    new_manifest[_opf_store.STANDARD_TOKEN] = _base
    if ds_key_present:
        del new_manifest["modules"][_UPGRADE_RETIRED_MODULE]
    for tname in _UPGRADE_NEW_TYPES:
        if tname not in pre_declared:
            new_manifest["types"][tname] = {"namespace": _opf_store.BASELINE_TYPES[tname]}
    # An origin that OMITTED [views] entirely (R2) migrates AS IF EMPTY: the table is created here to
    # carry the two net-new 1.1.0 views (an absent [modules] stays absent -- the delta only ever REMOVES a
    # modules key, so migrate-as-empty is a no-op there and no empty table is invented).
    new_manifest.setdefault("views", {})
    for vname in _UPGRADE_NEW_VIEWS:
        kind, sources, _renderer = _opf_views.NAMED_VIEWS[vname]
        new_manifest["views"][vname] = {
            "kind": kind,
            "sources": list(sources),
            "target": "{}/{}".format(_opf_store.WORKING_DIRNAME, vname),
        }
    # Widen the existing DECISIONS.md composed view to the 1.1.0 required source set ONLY when it is
    # declared (spec 9.2 widens "the existing DECISIONS.md composed view"; an absent view has no existence
    # to widen, G4). Set `sources` to the canonical ordered required set from NAMED_VIEWS so the migrated
    # row is byte-identical to a freshly initialized 1.1.0 store's row.
    if decisions_declared:
        new_manifest["views"][_UPGRADE_WIDENED_VIEW]["sources"] = list(
            _opf_views.NAMED_VIEWS[_UPGRADE_WIDENED_VIEW][1])

    new_counters = copy.deepcopy(counters_model)
    added = []
    for tname in _UPGRADE_NEW_TYPES:
        ns = _opf_store.BASELINE_TYPES[tname]
        if ns not in new_counters["counters"]:
            new_counters["counters"][ns] = 0
            added.append(ns)

    # POSTCONDITION (spec 9.2), value-exact: the manifest AND counters model diffs equal EXACTLY the
    # allowed delta and nothing else. Factored pure so it is directly unit-testable (m1).
    _upgrade_postcondition(manifest_model, new_manifest, counters_model, new_counters, origin)
    return new_manifest, new_counters, added, origin


def _upgrade_postcondition(old_manifest, new_manifest, old_counters, new_counters, origin):
    """The spec-9.2 upgrade postcondition, factored PURE so it is directly unit-testable (m1). It recomputes
    the EXPECTED new models from the OLD models plus the origin facts and compares wholesale, value-exact,
    with a per-table failure message for diagnosability. Raises _UpgradeError (fail-closed) on any
    divergence, so a stray mutation, a flipped retained module boolean, a mutated retained [types] row, a
    reordered or duplicated view source, or a lowered/raised/lost counter high-water can never slip through
    (fixes the set-only / key-set-only comparisons this replaced)."""
    import copy
    _prior_base = _opf_store.PRIOR_STANDARD_TOKEN
    _tok = _opf_store.STANDARD_TOKEN
    pre_declared = origin["pre_declared"]
    ds_key_present = origin["ds_key_present"]
    decisions_declared = origin["decisions_declared"]

    # Base table: renamed [devprocess] -> [opf], standard token flipped and spec_version bumped, every
    # other base key carried over value-exact.
    if _prior_base in new_manifest or _tok not in new_manifest:
        raise _UpgradeError("upgrade postcondition failed: base table not renamed [{}] -> [{}]".format(
            _prior_base, _tok))
    expected_base = dict(old_manifest[_prior_base])
    expected_base["standard"] = _tok
    expected_base["spec_version"] = _UPGRADE_TO
    if new_manifest.get(_tok) != expected_base:
        raise _UpgradeError("upgrade postcondition failed: base table [{}] changed beyond the standard-"
                            "token rename and spec_version bump".format(_tok))

    # [modules]: exactly the retired decision_support key removed when it was present, every other key
    # (name AND boolean value) carried over value-exact -- so a flipped retained boolean now refuses.
    expected_modules = {k: v for k, v in (old_manifest.get("modules") or {}).items()
                        if not (ds_key_present and k == _UPGRADE_RETIRED_MODULE)}
    if new_manifest.get("modules", {}) != expected_modules:
        raise _UpgradeError("upgrade postcondition failed: [modules] is not exactly the origin table with "
                            "the retired {!r} key removed".format(_UPGRADE_RETIRED_MODULE))

    # [types]: exactly the origin rows plus the now-baseline rows NOT already pre-declared, value-exact --
    # so a mutated namespace or a stray key in a retained row now refuses, and a pre-declared row is
    # admitted exactly.
    expected_types = dict(old_manifest["types"])
    for tname in _UPGRADE_NEW_TYPES:
        if tname not in pre_declared:
            expected_types[tname] = {"namespace": _opf_store.BASELINE_TYPES[tname]}
    if new_manifest["types"] != expected_types:
        raise _UpgradeError("upgrade postcondition failed: [types] is not exactly the origin rows plus the "
                            "added baseline rows")

    # [views]: origin rows, plus the two constructed new rows, plus (when declared) DECISIONS.md with its
    # sources replaced by the exact canonical ordered required list; value-exact, so the sources assertion
    # is order- AND duplicate-exact.
    expected_views = copy.deepcopy(old_manifest.get("views") or {})
    for vname in _UPGRADE_NEW_VIEWS:
        kind, sources, _renderer = _opf_views.NAMED_VIEWS[vname]
        expected_views[vname] = {
            "kind": kind,
            "sources": list(sources),
            "target": "{}/{}".format(_opf_store.WORKING_DIRNAME, vname),
        }
    if decisions_declared:
        expected_views[_UPGRADE_WIDENED_VIEW] = dict(expected_views[_UPGRADE_WIDENED_VIEW])
        expected_views[_UPGRADE_WIDENED_VIEW]["sources"] = list(
            _opf_views.NAMED_VIEWS[_UPGRADE_WIDENED_VIEW][1])
    if new_manifest["views"] != expected_views:
        raise _UpgradeError("upgrade postcondition failed: [views] is not exactly the origin rows plus the "
                            "two new views and the widened DECISIONS.md")

    # Every OTHER top-level table (store, providers, deliverables, archive, unmanaged, vendors, profiles,
    # ...) is byte-identical: value-exact deep equality over the whole remaining table set.
    _handled = {"modules", "types", "views", _prior_base, _tok}
    for table in set(old_manifest) | set(new_manifest):
        if table in _handled:
            continue
        if old_manifest.get(table) != new_manifest.get(table):
            raise _UpgradeError("upgrade postcondition failed: table [{}] changed but is not in the allowed "
                                "delta (fail-closed)".format(table))

    # Counters (m1): every existing high-water preserved value-exact, EXACTLY the CN/MD/PP namespaces
    # ABSENT from the origin added as zeros, the schema key and any other content untouched.
    if not isinstance(old_counters.get("counters"), dict):
        raise _UpgradeError("upgrade postcondition failed: origin counters [counters] table malformed")
    expected_counters = copy.deepcopy(old_counters)
    for tname in _UPGRADE_NEW_TYPES:
        ns = _opf_store.BASELINE_TYPES[tname]
        if ns not in expected_counters["counters"]:
            expected_counters["counters"][ns] = 0
    if new_counters != expected_counters:
        raise _UpgradeError("upgrade postcondition failed: counters is not exactly the origin high-waters "
                            "with the missing CN/MD/PP namespaces added as zeros")


def _upgrade_plan_minor(manifest_model, counters_model):
    """The 1.1.0 -> 1.2.0 allowed delta (spec 9.2, OPF-D2B PR3a): the [opf].spec_version bump ALONE.
    Preconditions (fail-closed): the current [opf] base table (never the retired [devprocess]) declaring
    standard "opf" and spec_version 1.1.0, and a [counters] table. No init.toml provenance is created
    (none is ever fabricated for an existing store), no type, view, module, index, or counter changes,
    and the postcondition asserts the manifest diff is EXACTLY the version field and the counters model
    is unchanged. Returns (new_manifest, new_counters, added_namespaces, origin) like _upgrade_plan."""
    import copy
    base = manifest_model.get(_opf_store.STANDARD_TOKEN)
    if not isinstance(base, dict) or _opf_store.PRIOR_STANDARD_TOKEN in manifest_model:
        raise _UpgradeError("manifest carries no [{}] base table (or still carries the retired [{}]); "
                            "not a {} store this upgrade migrates (fail-closed)".format(
                                _opf_store.STANDARD_TOKEN, _opf_store.PRIOR_STANDARD_TOKEN,
                                _UPGRADE_MID))
    if base.get("standard") != _opf_store.STANDARD_TOKEN or base.get("spec_version") != _UPGRADE_MID:
        raise _UpgradeError("manifest [{}] is not a {} base; no known upgrade path (fail-closed)".format(
            _opf_store.STANDARD_TOKEN, _UPGRADE_MID))
    if not isinstance(counters_model.get("counters"), dict):
        raise _UpgradeError("counters.toml [counters] table is missing or malformed (fail-closed)")
    new_manifest = copy.deepcopy(manifest_model)
    new_manifest[_opf_store.STANDARD_TOKEN]["spec_version"] = _UPGRADE_TO
    new_counters = copy.deepcopy(counters_model)
    expected = copy.deepcopy(manifest_model)
    expected[_opf_store.STANDARD_TOKEN] = dict(expected[_opf_store.STANDARD_TOKEN],
                                               spec_version=_UPGRADE_TO)
    if new_manifest != expected or new_counters != counters_model:
        raise _UpgradeError("upgrade postcondition failed: the {} -> {} delta is the spec_version bump "
                            "alone".format(_UPGRADE_MID, _UPGRADE_TO))
    origin = {"pre_declared": frozenset(), "ds_key_present": False, "decisions_declared": None,
              "from": _UPGRADE_MID}
    return new_manifest, new_counters, [], origin


def _cmd_upgrade(rest):
    """`opf upgrade [--root DIR]`: the in-place, additive, idempotent store-schema upgrade to the tooling
    spec_version {to} (spec 9.2). Two origins are supported: a 1.1.0 store takes the 1.1.0 -> {to} schema
    delta, changing only spec_version (no init.toml provenance is fabricated); a 1.0.0 store takes the
    full schema delta below, straight to {to}. Declared views are then regenerated, so a stale
    committed view can change.
    It RESOLVES the store at --root and refuses a store above the tooling spec. When migrating a 1.0.0
    or 1.1.0 store, its preconditions include readable, canonical manifest/counters matching a recognised
    origin shape, cleanliness over planned schema/render destinations and index collision candidates
    (including ignored files there), and acquisition of its lease. An untracked or ignored lease is
    excepted from the cleanliness check and handled separately by lease acquisition.
    It then applies EXACTLY the allowed delta as
    a model regeneration through the canonical new-document emitter (bump spec_version; drop the retired
    decision_support module WHERE PRESENT; add each contribution/maintainer_decision/preference_pattern type
    row NOT already declared by an enabled 1.0.0 module; add the two new view rows; WIDEN the DECISIONS.md
    composed view WHERE DECLARED; extend counters with zeros for any missing CN/MD/PP namespace,
    preserving existing high-waters; create the missing empty indexes, skipping any that already exist),
    RENDERS the declared views, and requires a full doctor VALID before offering the uncommitted change. It is
    ORIGIN-AWARE: a governance- or decision_support-enabled 1.0.0 store, and a store that omits the
    optional decision_support key or the DECISIONS.md view, each migrate correctly (spec 9.2, G1-G4).
    Before ANY write it enforces two fail-closed preconditions:
    PLANNED-DESTINATION CLEANLINESS (store and product roots), including ignored files, over planned
    destinations and collisions (HEAD preserves pre-existing tracked content,
    SECA-verified-restore-path), with an untracked or ignored lease handled separately, and a
    SINGLE-WRITER LEASE it claims atomically and holds across the mutation, render, and final doctor (spec
    5.7). It NEVER commits: the adopter reviews and merges. A store already at {to} returns without those
    migration preconditions: doctor-VALID yields a byte no-op (exit 0), otherwise it exits 2 without
    writing. A NOT-ADOPTED root is NOT APPLICABLE (exit 0), any other non-resolved status a located
    cannot-evaluate (exit 2). Two disclosed residuals: a killed run leaves the lease, which is
    spec-conformant (present only while held; a leftover is released through operator reconciliation, spec
    5.7) and is what the EEXIST refusal covers; and the lease is not made observable at a sync target
    before writes (spec 5.7) because this build has no sync runtime, so the guarantee is single-host
    single-writer. A third disclosed residual: this build has no pre-doctor for a 1.0.0 or 1.1.0 origin,
    so an older store invalid in a way the origin preconditions do not inspect fails only AFTER mutation
    (at the render or the final doctor). Under the single-writer contract, recovery restores pre-existing
    tracked content from HEAD and removes upgrade-created files within the checked store/product scope;
    pre-existing untracked or ignored content in that scope refuses before mutation.""".format(
        to=_UPGRADE_TO)
    root = None
    homes_plan = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--root":
            if i + 1 >= len(rest):
                print("opf upgrade: --root requires a directory argument", file=sys.stderr)
                return EXIT_MALFORMED
            if root is not None:
                print("opf upgrade: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = rest[i + 1]
            if val == "" or val.startswith("-"):
                print("opf upgrade: --root requires a non-empty directory argument, not {!r}".format(val),
                      file=sys.stderr)
                return EXIT_MALFORMED
            root = val
            i += 2
        elif tok == "--homes-plan":
            if homes_plan:
                print("opf upgrade: --homes-plan given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            homes_plan = True
            i += 1
        else:
            print("opf upgrade: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED
    root = root if root is not None else "."
    try:
        if homes_plan:
            # Deliberately lazy: existing schema upgrades and other verbs do not
            # load the new planner. The complete text is formed before printing.
            import _opf_homes_migrate
            try:
                text = _opf_homes_migrate.plan_homes_migration(root)
            except _opf_homes_migrate.MigrationPlanError as exc:
                raise _UpgradeError(
                    "homes-plan cannot evaluate: {}".format(exc)) from exc
            print(text, end="")
            return EXIT_OK
        return _upgrade_run(root)
    except (_UpgradeError, _opf_write_guard.WriteGuardError) as exc:
        print("opf upgrade: refused: {}; exit 2".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    except Exception as exc:  # noqa: BLE001  class-width fail-closed backstop, never a false success
        print("opf upgrade: cannot evaluate: unexpected error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return EXIT_MALFORMED


def _upgrade_doctor(root):
    """Resolve the store at `root`, gather its git-derived observations, and run the full offline doctor
    (`validate_store`). Returns the _opf_check validate result (its `.status` is `_opf_store.VALID` on a
    clean store). Raises _UpgradeError, fail-closed, when the store no longer resolves."""
    dres = _opf_store.resolve_store(Path(os.path.abspath(root)))
    if dres.status != _opf_store.RESOLVED:
        raise _UpgradeError("the store at {!r} no longer resolves ({}); fail-closed".format(root, dres.detail))
    dobs, _dnotes = _opf_observe.gather(dres)
    return _opf_check.validate_store(dres, observations=dobs)


# --- STEP 3/4 upgrade preconditions: store cleanliness and the single-writer lease -------------------

_UPGRADE_NO_WHOLE_TREE = ("Never run a whole-tree restore (git restore . / git reset --hard): it would "
                          "destroy unrelated uncommitted work. Scope every recovery to the affected "
                          "store and product paths.")
# The planned scope the shared cleanliness gate names in its dirty-tree refusal (_opf_write_guard.check_clean).
_UPGRADE_CLEAN_SCOPE = "schema and render destinations and index collision candidates"


def _upgrade_partial_recovery_text(res, manifest_model):
    """A previous interrupted run has no trustworthy write plan in this process. Inspect under the
    resolved store root, but do not prescribe a subtree restore: it could discard unmanaged owner work."""
    import shlex
    r = shlex.quote(str(res.store_root))
    w = shlex.quote(_opf_store.WORKING_DIRNAME)
    try:
        scope = _upgrade_write_scope(res.machine_rel, manifest_model, True, _UPGRADE_NEW_TYPES)
        candidates = "Candidate store destinations under {} (current manifest; inspect only): {}.".format(
            r, " ".join(shlex.quote(p) for p in scope["store"]))
        if scope["product"]:
            product_root = res.product_root if res.product_root is not None else res.store_root
            candidates += "\nCandidate product destinations under {} (inspect only): {}.".format(
                shlex.quote(str(product_root)), " ".join(shlex.quote(p) for p in scope["product"]))
    except Exception as exc:  # Candidate advice must not hide the doctor findings.
        candidates = "Cannot derive candidate destinations from the current manifest: {}.".format(exc)
    return ("Inspect the store subtree (git -C {r} --literal-pathspecs status --ignored=matching "
            "--untracked-files=all -- {w}). Identify the earlier run's planned destinations and reconcile "
            "intervening owner edits before restoring individual tracked paths or removing files proven "
            "upgrade-created. Exclude unmanaged paths and the lease; reconcile a leftover lease only after "
            "confirming no run is live (spec 5.7). Reconcile product targets separately, then re-run. "
            "{no}\n{candidates} These candidates do not prove the earlier write scope or which files were "
            "created; reconcile them against the earlier run and owner edits before recovery.".format(
                r=r, w=w, no=_UPGRADE_NO_WHOLE_TREE, candidates=candidates))


def _upgrade_recovery_text(store_root, product_root, created_relpaths, product_relpaths, store_relpaths):
    """Recovery advice for a post-mutation failure (render or doctor): the planned set is KNOWN, and with the
    step-3 cleanliness precondition (including ignored files) and the single-writer contract, HEAD
    preserves pre-existing tracked content in the checked scope; remove upgrade-created files to recover
    previously absent paths. Inspect first for intervening owner edits. The DISTINCT roots are threaded
    (explicit-binding-over-ambient-context): tracked store paths
    in the explicit plan, and the upgrade-created untracked files, are recovered under the STORE root (where
    `.working` lives); a declared product-scope target rendered this run is recovered under the PRODUCT root.
    The two roots differ for a RELOCATED store (the pointer resolves `.working` to a store separate from the
    product tree), where using one root for both would aim the `.working` restore at the wrong repository.
    Never a whole-tree restore."""
    import shlex
    r = shlex.quote(str(store_root))
    w = " ".join(shlex.quote(p) for p in sorted(store_relpaths))
    tracked = " ".join(shlex.quote(p) for p in sorted(set(store_relpaths) - set(created_relpaths)))
    lines = ["opf upgrade: the uncommitted change is left for review; recover it scoped to the paths this run "
             "planned (tracked content was clean against HEAD; untracked and ignored content was refused). "
             "Confirm no opf run is live (spec 5.7), then inspect for intervening owner edits:"]
    if tracked:
        lines.append("  restore tracked store paths: git -C {} --literal-pathspecs restore --staged "
                     "--worktree -- {}".format(r, tracked))
    if created_relpaths:
        lines.append("  remove only these previously absent files if this run created them: rm -- " + " ".join(
            shlex.quote(os.path.join(str(store_root), p)) for p in sorted(created_relpaths)))
    pr = shlex.quote(str(product_root))
    for p in sorted(product_relpaths):
        lines.append("  product target rendered: git -C {} --literal-pathspecs restore --staged --worktree "
                     "-- {} (or remove it if this run created it)".format(pr, shlex.quote(p)))
    lines.append("  inspect first: git -C {} --literal-pathspecs status --ignored=matching "
                 "--untracked-files=all -- {}".format(r, w))
    lines.append("  " + _UPGRADE_NO_WHOLE_TREE)
    return "\n".join(lines)


def _upgrade_write_scope(machine_rel, manifest_model, counters_changed, index_types):
    """Plan the destinations of the schema delta and the subsequent declared-view render.
    Index candidates are included for collision checks even when create-only will leave them alone.
    Use the POST-delta manifest: it includes newly introduced views. The shared planner
    (_opf_write_guard.plan_write_scope) adds every declared render destination and refuses a declaration
    colliding with a managed destination before mutation, rather than licensing an overwrite. The lease
    is checked separately and is never a restore/staging target."""
    store = ["{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)]
    if counters_changed:
        store.append("{}/{}".format(machine_rel, _opf_check.COUNTERS_NAME))
    indexes = tuple("{}/{}{}".format(machine_rel, t, _opf_check.INDEX_SUFFIX) for t in index_types)
    store.extend(indexes)
    scope = _opf_write_guard.plan_write_scope(machine_rel, manifest_model, store, "upgrade")
    scope["indexes"] = indexes
    return scope


def _upgrade_run(root):
    import shlex
    import tomllib
    if _UPGRADE_TO != _opf_store.SUPPORTED_SPEC_VERSION:
        raise _UpgradeError("upgrade target {!r} does not match the tooling spec_version {!r}; refusing to "
                            "run a stale upgrade path (fail-closed)".format(
                                _UPGRADE_TO, _opf_store.SUPPORTED_SPEC_VERSION))
    try:
        # `opf upgrade` is the ONE caller that also accepts the retired 1.0.0 discovery token, so a legacy
        # [devprocess] store still resolves for migration (spec 9.2); every other tool keeps the sole
        # current-token discovery.
        res = _opf_store.resolve_store(
            Path(os.path.abspath(root)),
            accept_tokens=(_opf_store.STANDARD_TOKEN, _opf_store.PRIOR_STANDARD_TOKEN))
    except Exception as exc:  # noqa: BLE001  a resolver escape is cannot-evaluate, never a mutation
        raise _UpgradeError("unexpected error resolving the store at {!r} ({!r})".format(root, exc))
    if res.status == _opf_store.NOT_ADOPTED:
        print("opf upgrade: NOT APPLICABLE ({})".format(res.detail))
        return EXIT_OK
    if res.status != _opf_store.RESOLVED:
        print("opf upgrade: cannot evaluate: {}".format(res.detail), file=sys.stderr)
        return EXIT_MALFORMED

    machine_rel = res.machine_rel
    manifest_rel = "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)
    counters_rel = "{}/{}".format(machine_rel, _opf_check.COUNTERS_NAME)
    root_fd = _opf_store._open_dir_nofollow(res.store_root)
    recovery = None
    try:
        manifest_bytes = _upgrade_read_bytes(root_fd, manifest_rel, control=True)
        counters_bytes = _upgrade_read_bytes(root_fd, counters_rel)
        if manifest_bytes is None or counters_bytes is None:
            raise _UpgradeError("store manifest or counters is absent; not a resolvable store to upgrade")
        try:
            manifest_model = tomllib.loads(manifest_bytes.decode("utf-8"))
            counters_model = tomllib.loads(counters_bytes.decode("utf-8"))
        except (UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise _UpgradeError("store manifest or counters does not parse as TOML ({})".format(exc))

        # spec_version triage: current is an idempotent byte no-op; above-tooling fails closed; only the
        # single known 1.0.0 origin proceeds. The base table is [opf] on an already-migrated store and the
        # retired [devprocess] on a legacy 1.0.0 store, so read whichever the manifest carries.
        _base_model = manifest_model.get(_opf_store.STANDARD_TOKEN)
        if not isinstance(_base_model, dict):
            _base_model = manifest_model.get(_opf_store.PRIOR_STANDARD_TOKEN)
        # Worklog activation is independent of spec_version. Refuse before even
        # the cleanliness probe or lease can write; a later render fence is too late.
        # Normalize only the retired base-table name for the shared loader check.
        try:
            _opf_worklog.generation({"opf": _base_model})
        except _opf_worklog.WorklogError as exc:
            raise _UpgradeError(str(exc)) from exc
        sv = _base_model.get("spec_version") if isinstance(_base_model, dict) else None
        if sv == _UPGRADE_TO:
            # F2: a store already at the target is a no-op ONLY when it is genuinely doctor-VALID at 1.1.0.
            # A partial or interrupted migration leaves spec_version == 1.1.0 with an incomplete delta;
            # trusting the version marker alone would report false success over a broken store. Re-validate
            # and fail closed on anything short of VALID, so a partial migration is never reported complete.
            result = _upgrade_doctor(root)
            if result.status == _opf_store.VALID:
                print("opf upgrade: store is already at spec_version {} and doctor-VALID; nothing to "
                      "upgrade (no-op).".format(_UPGRADE_TO))
                return EXIT_OK
            print("opf upgrade: store declares spec_version {} but is NOT doctor-VALID: a partial or "
                  "interrupted migration is never reported complete (fail-closed, spec 9.2). {} Run "
                  "`opf doctor --root {}` for the findings, exit 2.".format(
                      _UPGRADE_TO, _upgrade_partial_recovery_text(res, manifest_model), root), file=sys.stderr)
            _doctor_report(result)
            return EXIT_MALFORMED
        try:
            sv_tuple = tuple(int(p) for p in sv.split(".")) if isinstance(sv, str) else None
        except ValueError:
            sv_tuple = None
        if sv_tuple is not None and sv_tuple > tuple(int(p) for p in _UPGRADE_TO.split(".")):
            raise _UpgradeError("store declares spec_version {!r} ABOVE the {} this tooling implements; "
                                "a newer store is never downgraded (fail-closed)".format(sv, _UPGRADE_TO))
        minor = sv == _UPGRADE_MID

        # PRECONDITION (spec 9.2): re-emitting the UNCHANGED parsed model reproduces the on-disk bytes
        # exactly, proving the file is canonical and comment-free so the bounded rewrite loses nothing.
        if _opf_emit.emit_checked(manifest_model).encode("utf-8") != manifest_bytes:
            raise _UpgradeError("manifest is not in canonical new-document form (hand-edited or comment-"
                                "bearing); refusing a model rewrite that could lose content (fail-closed)")
        if _opf_emit.emit_checked(counters_model).encode("utf-8") != counters_bytes:
            raise _UpgradeError("counters.toml is not in canonical new-document form; refusing (fail-closed)")

        new_manifest, new_counters, added_ns, origin = (
            _upgrade_plan_minor if minor else _upgrade_plan)(manifest_model, counters_model)
        origin_version = _UPGRADE_MID if minor else _UPGRADE_FROM
        new_manifest_bytes = _opf_emit.emit_checked(new_manifest).encode("utf-8")
        new_counters_bytes = _opf_emit.emit_checked(new_counters).encode("utf-8")

        # STEP 3: derive the scope from this delta and its POST-delta render declarations. The same
        # destinations drive the cleanliness gate, index creation, recovery, and staging advice.
        write_scope = _upgrade_write_scope(
            machine_rel, new_manifest, new_counters_bytes != counters_bytes,
            () if minor else _UPGRADE_NEW_TYPES)
        _opf_write_guard.check_clean(res, write_scope, "upgrade", _UPGRADE_CLEAN_SCOPE)
        product_targets = write_scope["product"]
        created_relpaths = []
        for relpath in write_scope["store"]:
            pfd, name = _opf_store._journal._open_parent(root_fd, relpath)
            try:
                entry = _opf_store._journal._lstat_at(pfd, name)
                if entry is None:
                    created_relpaths.append(relpath)
                elif not stat.S_ISREG(entry.st_mode):
                    raise _UpgradeError("upgrade destination {!r} is not a regular file "
                                        "(fail-closed)".format(relpath))
            finally:
                os.close(pfd)
        # DISTINCT roots for the recovery/staging advice (R1): `.working` lives under the STORE root, product-
        # scope targets under the PRODUCT root; the two differ for a RELOCATED store.
        recovery_store_root = res.store_root
        recovery_product_root = res.product_root if res.product_root is not None else res.store_root
        absent_product_targets = []
        for relpath in product_targets:
            try:
                os.lstat(os.path.join(str(recovery_product_root), relpath))
            except FileNotFoundError:
                absent_product_targets.append(relpath)
        _opf_write_guard.check_ignored(recovery_store_root, created_relpaths, "upgrade")
        _opf_write_guard.check_ignored(recovery_product_root, absent_product_targets, "upgrade")
        # STEP 4 (M4): claim the single-writer lease atomically, then hold it across mutation, render, and
        # the final doctor; release it in the finally covering every exit after acquisition, EXCEPT the
        # success path releases FIRST (R5) so no success is reported over a still-held / failed-to-release
        # lease. `released` records that the success path already released, so the finally does not re-release.
        lease_payload = _opf_write_guard.acquire_lease(root_fd, machine_rel, "upgrade")
        recovery = (recovery_store_root, recovery_product_root, created_relpaths,
                    product_targets, write_scope["store"])
        released = False
        try:
            # Apply: rewrite manifest + counters (canonical bytes), create the missing empty indexes. The
            # 1.1.0 origin rewrites the manifest alone (its delta is the spec_version bump).
            _upgrade_replace(root_fd, manifest_rel, new_manifest_bytes)
            if new_counters_bytes != counters_bytes:
                _upgrade_replace(root_fd, counters_rel, new_counters_bytes)
            empty_index = _opf_emit.emit_checked(
                {"schema": _opf_schema.SUPPORTED_SCHEMA, "record": []}).encode("utf-8")
            created_indexes = []
            for idx_rel in write_scope["indexes"]:
                if _upgrade_create_index(root_fd, idx_rel, empty_index):
                    created_indexes.append(Path(idx_rel).name[:-len(_opf_check.INDEX_SUFFIX)])

            # Render the declared views (materializes the two new views and re-renders DECISIONS.md), then
            # require a full doctor VALID before offering the uncommitted change. Both run over the mutated
            # (uncommitted) tree, under the held lease (containment-clean; the mid-run doctor stays VALID).
            render_argv = ["--root", root, "--write"]
            try:
                rres = _opf_store.resolve_store(Path(os.path.abspath(root)))
                robs, _notes = _opf_observe.gather(rres) if rres.status == _opf_store.RESOLVED else (None, [])
                rc = _opf_views.render(render_argv, observations=robs)
            except Exception as exc:  # noqa: BLE001  a render escape must not read as a clean upgrade
                raise _UpgradeError("view render after the schema delta failed ({!r}); the uncommitted change is "
                                    "left for review".format(exc))
            if rc != EXIT_OK:
                print("opf upgrade: cannot evaluate: view render after the schema delta did not complete "
                      "cleanly (rc={}); exit 2.".format(rc), file=sys.stderr)
                print(_upgrade_recovery_text(recovery_store_root, recovery_product_root, created_relpaths,
                                             product_targets, write_scope["store"]), file=sys.stderr)
                recovery = None  # Already printed, even if the finally's lease release fails.
                return EXIT_MALFORMED

            result = _upgrade_doctor(root)
            if result.status != _opf_store.VALID:
                print("opf upgrade: the upgraded store is NOT doctor-VALID; refusing to offer the change "
                      "(fail-closed, spec 9.2). Run `opf doctor --root {}` for the findings, exit 2.".format(
                          root), file=sys.stderr)
                print(_upgrade_recovery_text(recovery_store_root, recovery_product_root, created_relpaths,
                                             product_targets, write_scope["store"]), file=sys.stderr)
                recovery = None  # Already printed, even if the finally's lease release fails.
                _doctor_report(result)
                return EXIT_MALFORMED

            # R5: release the single-writer lease BEFORE emitting the success report. A release failure
            # surfaces as exit 2 (never swallowed), and success is never printed over a still-held lease.
            # `released` is set FIRST so the finally never double-releases (a failed release legitimately
            # leaves the lease as a spec-conformant leftover for operator reconciliation).
            released = True
            # Doctor has validated the payload. A failed release can mean another holder is live;
            # automatic rollback advice is unsafe and is not required to make this payload valid.
            recovery = None
            try:
                _opf_write_guard.release_lease(root_fd, machine_rel, lease_payload, "upgrade")
            except BaseException:
                print("opf upgrade: the store reached doctor-VALID before lease release, but release "
                      "failed. Confirm no opf run is live (spec 5.7) and reconcile the lease before "
                      "any further action; no restore/removal commands are offered.", file=sys.stderr)
                raise
            print("opf upgrade: store schema upgraded {} -> {} and doctor-VALID (uncommitted, NOT staged or committed)."
                  .format(origin_version, _UPGRADE_TO))
            print(json.dumps({
                "event": "upgraded", "root": str(root), "from": origin_version, "to": _UPGRADE_TO,
                "created_indexes": sorted(created_indexes), "added_counters": sorted(added_ns),
                "pre_declared_types": sorted(origin["pre_declared"]),
                "decisions_view": "unchanged" if minor else (
                    "widened" if origin["decisions_declared"] else "not-declared")},
                sort_keys=True))
            print("opf upgrade: regenerated views reflect working-tree store content, including uncommitted "
                  "source edits outside the cleanliness scope. Before committing, review those edits and "
                  "commit the intended sources with their views, or set them aside and regenerate the views.")
            print("opf upgrade: review the uncommitted changes, then stage and commit the planned destinations "
                  "(never `add -A`, which would sweep in unrelated work). The commands use -f for "
                  "these named destinations because the safety probes neutralize global/system config "
                  "and core.excludesFile (including default HOME/XDG global ignores). They read "
                  "working-tree .gitignore files and .git/info/exclude; the absent-path probe does "
                  "not read indexed ignore rules:")
            print("  git -C {} --literal-pathspecs add -f -- {}".format(
                shlex.quote(str(recovery_store_root)),
                " ".join(shlex.quote(p) for p in write_scope["store"])))
            for _pt in product_targets:
                print("  git -C {} --literal-pathspecs add -f -- {}".format(
                    shlex.quote(str(recovery_product_root)), shlex.quote(_pt)))
            print("opf upgrade: exit 0 means the store is valid at {}; committing is the adopter's own "
                  "step.".format(_UPGRADE_TO))
            return EXIT_OK
        finally:
            if not released:
                # R5/FIX1: release the lease on every non-success exit. When a mid-run failure is ALREADY
                # propagating (a render/doctor escape after the manifest+counters were rewritten, which
                # carries its own "uncommitted change is left for review" recovery advice) and the release then
                # ALSO fails (its lease-replaced never-seize WriteGuardError), the release error must NOT
                # DISPLACE that original exception: the operator still needs the mid-run recovery advice, so
                # the lease-replaced note is surfaced ALONGSIDE it, never in place of it (exit 2 preserved,
                # peer lease left, never seized). A propagating KeyboardInterrupt/SystemExit is likewise not
                # masked by a release failure.
                pending = sys.exc_info()[1]
                if pending is None:
                    # A `return` (or normal fall-through) is passing through with no in-flight exception: a
                    # release failure legitimately becomes the surfaced outcome (exit 2), exactly as before.
                    _opf_write_guard.release_lease(root_fd, machine_rel, lease_payload, "upgrade")
                else:
                    try:
                        _opf_write_guard.release_lease(root_fd, machine_rel, lease_payload, "upgrade")
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except Exception as rel_exc:  # noqa: BLE001  surfaced, never displaces the original
                        print("opf upgrade: additionally, releasing the upgrade lease failed ({}); the peer "
                              "lease is LEFT in place (never seized, spec 5.7) and the original failure above "
                              "still governs (exit 2).".format(rel_exc), file=sys.stderr)
                        # returning from the except lets `pending` resume propagating (the finally completes
                        # without raising a new exception), so _cmd_upgrade surfaces the original refusal.
    except BaseException:
        # Cover every escape after acquisition, including writes, render/doctor and lease release.
        # Preserve the original exception and the existing never-seize release handling.
        if recovery is not None:
            print(_upgrade_recovery_text(*recovery), file=sys.stderr)
        raise
    finally:
        os.close(root_fd)


def _import_exit(verdict):
    """Map an operation-layer verdict (_opf_import CLEAN/FINDING/CANNOT_EVALUATE) to the CLI 0/1/2 exit
    contract, fail-closed: a verdict outside {0, 1, 2} (a first-party contract violation) is exit 2, never
    a false clean. The verdict constants are numerically the exit codes, but the mapping is explicit so a
    future divergence cannot silently pass an out-of-range value through as clean."""
    if verdict == _opf_import.CLEAN:
        return EXIT_OK
    if verdict == _opf_import.FINDING:
        return EXIT_FINDING
    return EXIT_MALFORMED   # CANNOT_EVALUATE, or any unexpected value, fails closed


def _import_read_set(path):
    """Read the `--set` import-set manifest (a TOML file), fail-closed. Returns (sources, proposals): a
    non-empty list of relative source-path strings and the optional inert model-proposal tables (a list, or
    None). The manifest is CALLER input, not a store artefact, so it may live outside the store and is read
    directly; a missing, unreadable, or malformed manifest is a ValueError (the caller maps it to a
    cannot-evaluate exit 2, never a silent nothing-to-do). Shape (surfaced for maintainer sign-off,
    PD-OPF-IMPORT-VERB-APPLY-SEAMS): `schema = 1`, `source = ["rel/path", ...]`, optional `[[proposal]]`
    rows in the _opf_import proposal keyset {source_path, span, suggested_state, note}. This reader
    validates each proposal row's STRUCTURE fail-closed (a closed keyset, a non-empty str source_path, a
    two-int span, a str suggested_state, an optional str note); a structurally-malformed row is a ValueError
    the caller maps to exit 2, consistently for `--scan` and `--plan`, so a malformed --set FILE cannot be
    silently ignored by one mode and forwarded by the other. The operation layer (`_validate_proposals`)
    still owns the SEMANTICS as a finding (exit 1): source_path must name a scanned source (the
    contained-relpath / confinement discipline), span must lie within [0, size], and suggested_state must be
    a mapping-state member; those checks are not duplicated here. Proposals are consumed only by `--plan`
    (they are recorded verbatim in the review surface); `--scan` enumerates sources and ignores any proposal
    rows, but still rejects a structurally-malformed --set FILE at read time."""
    import tomllib
    try:
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
    except FileNotFoundError:
        raise ValueError("--set manifest not found: {}".format(path))
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError("--set manifest unreadable or malformed ({}): {}".format(path, exc))
    if not (isinstance(doc, dict) and type(doc.get("schema")) is int and doc.get("schema") == 1):
        raise ValueError("--set manifest must be a TOML table carrying `schema = 1` (an integer 1, not a "
                         "bool or float)")
    extra = set(doc) - {"schema", "source", "proposal"}
    if extra:
        raise ValueError("--set manifest carries unknown key(s): {} (a set is a closed {{schema, source, "
                         "proposal}})".format(", ".join(sorted(extra))))
    sources = doc.get("source")
    if not (isinstance(sources, list) and sources and all(isinstance(s, str) and s for s in sources)):
        raise ValueError("--set manifest `source` must be a non-empty array of source-path strings")
    proposals = doc.get("proposal")
    if proposals is not None and not isinstance(proposals, list):
        raise ValueError("--set manifest `proposal` must be an array of proposal tables when present")
    # Validate each proposal row's STRUCTURE fail-closed (a malformed --set FILE is exit 2, consistently for
    # scan and plan). The operation layer (`_validate_proposals`) still owns the SEMANTICS: source_path names
    # a scanned source, span lies within [0, size], suggested_state is a mapping-state member (each a
    # finding, exit 1). Row shape here is the _opf_import proposal keyset {source_path, span,
    # suggested_state, note}: a closed keyset, a non-empty str source_path, a two-int span, a str
    # suggested_state, and an optional str note. The semantic checks are NOT duplicated here.
    for idx, row in enumerate(proposals or []):
        where = "--set manifest `proposal`[{}]".format(idx)
        if not isinstance(row, dict):
            raise ValueError("{} must be a table".format(where))
        extra_row = set(row) - {"source_path", "span", "suggested_state", "note"}
        if extra_row:
            raise ValueError("{} carries unknown key(s): {} (a proposal row is a closed {{source_path, "
                             "span, suggested_state, note}})".format(where, ", ".join(sorted(extra_row))))
        if not (isinstance(row.get("source_path"), str) and row.get("source_path")):
            raise ValueError("{} `source_path` must be a non-empty string".format(where))
        span = row.get("span")
        if not (isinstance(span, list) and len(span) == 2 and all(type(x) is int for x in span)):
            raise ValueError("{} `span` must be a list of exactly two integers".format(where))
        if not isinstance(row.get("suggested_state"), str):
            raise ValueError("{} `suggested_state` must be a string".format(where))
        if "note" in row and not isinstance(row.get("note"), str):
            raise ValueError("{} `note` must be a string when present".format(where))
    return sources, proposals


def _import_read_worksheet(path):
    """Read the `--dispositions` triaged worksheet (a TOML file) for the root-ingest planner (MIG-PR3),
    fail-closed. Returns the parsed dict UNCHANGED: the SHAPE/vocabulary/digest validation is owned by
    `_opf_ingest.validate_worksheet` (the single worksheet authority) and the semantics by `plan_ingest`,
    never duplicated here. The file is CALLER input (it may live outside the store); a missing, unreadable,
    or malformed worksheet is a ValueError the caller maps to cannot-evaluate exit 2, never a silent
    nothing-to-do (mirrors `_import_read_set`'s read-boundary discipline)."""
    import tomllib
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise ValueError("--dispositions worksheet not found: {}".format(path))
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError("--dispositions worksheet unreadable or malformed ({}): {}".format(path, exc))


def _import_read_options(path):
    """Read the `--ingest-options` companion (a TOML file) for the root-ingest planner (MIG-PR3),
    fail-closed. Returns the parsed dict UNCHANGED: the SHAPE validation is owned by
    `_opf_ingest.validate_options` and the semantic binding by `plan_ingest`, never duplicated here. The file
    is CALLER input (it may live outside the store); a missing, unreadable, or malformed options file is a
    ValueError the caller maps to cannot-evaluate exit 2, never a silent nothing-to-do."""
    import tomllib
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        raise ValueError("--ingest-options file not found: {}".format(path))
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError("--ingest-options file unreadable or malformed ({}): {}".format(path, exc))


def _import_decode_decisions(raw, run_id):
    """Decode the closed ordinary or ingest envelope without accepting any decision implicitly."""
    import _opf_import as imp
    doc = imp._strict_json(raw)
    if not isinstance(doc, dict) or type(doc.get("schema")) is not int or doc["schema"] not in (1, 2):
        raise ValueError("--decisions requires integer schema 1 or 2")
    keys = {"schema", "run_id", "decisions"} | ({"ingest"} if doc["schema"] == 2 else set())
    if set(doc) != keys or doc["run_id"] != run_id or not isinstance(doc["decisions"], list):
        raise ValueError("--decisions envelope keys, run binding, or decisions array are invalid")
    if doc["schema"] == 2:
        block = doc["ingest"]
        if not (isinstance(block, dict) and set(block) == {"format", "binding", "units"}
                and block["format"] == imp.INGEST_ACCEPTANCE_BLOCK
                and isinstance(block["binding"], dict) and isinstance(block["units"], list)):
            raise ValueError("--decisions ingest block is malformed")
        return doc
    return doc["decisions"]


def _import_read_decisions(path, run_id):
    """Read the `--decisions` batch file (canonical JSON), fail-closed. Returns the decisions list. The file
    is CALLER input (it may live outside the store); its envelope (surfaced for maintainer sign-off,
    PD-OPF-IMPORT-VERB-APPLY-SEAMS) is `{"schema": 1, "run_id": ..., "decisions": [ {fragment_id, decision,
    origin, proposed_state, note}, ... ]}`. `run_id` MUST equal the CLI `--review` operand
    (explicit-binding-over-ambient-context: the file is bound to the exact run under review, never trusted
    to name a different one). Each decision table's own shape is validated at the operation layer
    (`review_import`), never here. A missing/unreadable/malformed file, a schema or run-id mismatch, or a
    non-list `decisions` is a ValueError (cannot-evaluate exit 2). A schema-2 ingest envelope is
    decoded by _import_decode_decisions: bounded, duplicate-key and non-finite refusing, closed."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError("--decisions file unreadable or malformed ({}): {}".format(path, exc))
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        # A deeply-nested --decisions JSON raises RecursionError from json.loads (not fh.read); catch it at
        # the reader so it fails closed with a LOCATED message (R8-F1 read-boundary parity with
        # _import_read_set's tomllib.load guard), never only at _cmd_import's outer backstop.
        raise ValueError("--decisions file is not valid JSON or is too deeply nested ({}): {}".format(
            path, exc))
    # Only a schema-2 ingest envelope takes the bounded, strict decoder; an ordinary schema-1 file keeps
    # the unbounded read, lenient decode, and located messages below.
    if isinstance(doc, dict) and type(doc.get("schema")) is int and doc.get("schema") == 2:
        try:
            return _import_decode_decisions(raw, run_id)
        except (ValueError, RecursionError) as exc:
            raise ValueError("--decisions file unreadable or malformed ({}): {}".format(path, exc))
    if not (isinstance(doc, dict) and type(doc.get("schema")) is int and doc.get("schema") == 1):
        raise ValueError("--decisions file must be a JSON object carrying \"schema\": 1 (an integer 1, not "
                         "a bool or float)")
    if doc.get("run_id") != run_id:
        raise ValueError("--decisions file run_id {!r} does not match the --review run-id {!r}; the "
                         "decisions file is bound to the exact run under review".format(
                             doc.get("run_id"), run_id))
    decisions = doc.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("--decisions file \"decisions\" must be an array")
    extra = set(doc) - {"schema", "run_id", "decisions"}
    if extra:
        raise ValueError("--decisions file carries unknown key(s): {} (the envelope is a closed {{schema, "
                         "run_id, decisions}})".format(", ".join(sorted(extra))))
    return decisions


def _cmd_import_review_aid(rest):
    """Read-only template and stale-acceptance comparison; no decision is copied."""
    import argparse
    parser = argparse.ArgumentParser(prog="opf import")
    parser.add_argument("--root", default=".")
    parser.add_argument("--review", required=True)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--show-review", action="store_true")
    choice.add_argument("--diff-review", metavar="OLD_RUN")
    try:
        args = parser.parse_args(rest)
        result = _opf_import.ingest_review_aid(os.path.abspath(args.root), args.review, args.diff_review)
        print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True))
        return EXIT_OK
    except SystemExit as exc:
        return exc.code
    except Exception as exc:
        print("opf import: review aid cannot be evaluated ({})".format(
            _opf_import._ingest_md_escape(str(exc))), file=sys.stderr)
        return EXIT_MALFORMED


def _cmd_import(rest):
    """`opf import [--root DIR] (--scan --set FILE | --plan --set FILE | --review <run-id> --actor NAME
    (--decisions FILE | --interactive) | --apply <run-id>)`: the store import verb.

    Wires the reserved import grammar onto the U7 operation layer (_opf_import scan/plan/review/apply); it
    adds NO operation-layer behaviour. EXACTLY ONE mode is required: a bare `opf import` or two modes is a
    usage error (exit 2). The parser is the house fail-closed idiom (unknown token, an empty or
    option-looking or duplicate value -> exit 2), matching _cmd_render's --root loop; each run-id operand is
    validated against the U7 run-id grammar as an identifier BEFORE any path use.

    Modes and the 0/1/2 exit contract (0 clean, 1 finding, 2 cannot-evaluate), read straight from the
    operation-layer verdict via _import_exit:
      --scan  --set FILE : scan_import; renders the canonical inventory TOML to stdout; ZERO writes.
                           (0 enumerated; 2 unreadable/malformed --set or unresolved store)
      --plan  --set FILE : plan_import; stages a candidate run and prints the run id, the store-relative
                           report path, and the migration-incomplete signal. (0 staged, quarantine-heavy
                           still 0 as the artefact signals review need; 1 a plan finding; 2 unreadable
                           input / malformed set / unresolved store)
      --review RUN --actor NAME (--decisions FILE | --interactive) : review_import (batch) or
                           review_import_interactive (a non-TTY --interactive is refused, exit 2); writes
                           acceptance.json, NO live-store write. (0 captured; 1 a decisions finding; 2 an
                           unresolved/missing/not-promotion-ready run, missing actor, unreadable decisions
                           file, or non-TTY --interactive)
      --apply RUN        : apply_import (PR-C, the real fail-closed promotion): promotes an accepted staged
                           run to the active store (0 promoted / verified no-op; 1 a reject or composition
                           finding; 2 not-promotion-ready / unverifiable / indeterminate). Mutates the store
                           only through the journaled, verified-restore cutover.

    D7 (verb-family precedent, deliberate divergence from the sibling verbs): every import mode requires a
    RESOLVED, initialized store; an unresolved / NOT-ADOPTED root is exit 2 with the operation layer's "run
    `opf init` first" message, NOT the NOT-APPLICABLE exit 0 that doctor/render/upgrade report on a
    non-adopter root. Those siblings are applicability probes; import is a REQUESTED operation whose
    precondition (an adopted store) failed, so it fails cannot-evaluate rather than reporting not-applicable.

    `now` is read from the clock (timestamp-from-clock) and `run_nonce` from os.urandom, both injected into
    the operation layer (the deterministic run id composes them). Every residual escape (a resolver/gather/
    operation escape, an unreadable --set/--decisions file) fails closed to exit 2 (never a false 0 or an
    uncaught exit-1), the same class-width backstop render/doctor carry."""
    if "--show-review" in rest or "--diff-review" in rest:
        return _cmd_import_review_aid(rest)
    import datetime

    root = None
    mode = None
    run_id = None
    set_file = None
    actor = None
    decisions_file = None
    interactive = False
    dispositions_file = None      # MIG-PR3: the triaged worksheet for the root-ingest --plan form
    ingest_options_file = None    # MIG-PR3: the companion --ingest-options for the root-ingest --plan form
    include = []                  # MIG-PR3: repeatable --include globs for the root-ingest --plan form

    def _need_value(flag, idx):
        if idx + 1 >= len(rest):
            print("opf import: {} requires an argument".format(flag), file=sys.stderr)
            return None
        val = rest[idx + 1]
        if val == "" or val.startswith("-"):
            print("opf import: {} requires a non-empty argument, not {!r}".format(flag, val),
                  file=sys.stderr)
            return None
        return val

    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok in ("--scan", "--plan"):
            if mode is not None:
                print("opf import: give exactly one mode (--scan / --plan / --review / --apply)",
                      file=sys.stderr)
                return EXIT_MALFORMED
            mode = tok[2:]
            i += 1
        elif tok in ("--review", "--apply"):
            if mode is not None:
                print("opf import: give exactly one mode (--scan / --plan / --review / --apply)",
                      file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            mode = tok[2:]
            run_id = val
            i += 2
        elif tok == "--root":
            if root is not None:
                print("opf import: --root given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            root = val
            i += 2
        elif tok == "--set":
            if set_file is not None:
                print("opf import: --set given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            set_file = val
            i += 2
        elif tok == "--actor":
            if actor is not None:
                print("opf import: --actor given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            actor = val
            i += 2
        elif tok == "--decisions":
            if decisions_file is not None:
                print("opf import: --decisions given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            decisions_file = val
            i += 2
        elif tok == "--interactive":
            if interactive:
                print("opf import: --interactive given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            interactive = True
            i += 1
        elif tok == "--dispositions":
            if dispositions_file is not None:
                print("opf import: --dispositions given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            dispositions_file = val
            i += 2
        elif tok == "--ingest-options":
            if ingest_options_file is not None:
                print("opf import: --ingest-options given more than once", file=sys.stderr)
                return EXIT_MALFORMED
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            ingest_options_file = val
            i += 2
        elif tok == "--include":
            val = _need_value(tok, i)
            if val is None:
                return EXIT_MALFORMED
            include.append(val)
            i += 2
        else:
            print("opf import: unrecognized argument {!r}".format(tok), file=sys.stderr)
            return EXIT_MALFORMED

    # Mode-combination validation (fail-closed: an unsupported flag for the chosen mode is a usage error).
    ingest_form = dispositions_file is not None or ingest_options_file is not None
    if mode is None:
        print("opf import: give exactly one mode (--scan / --plan / --review / --apply)", file=sys.stderr)
        return EXIT_MALFORMED
    if mode == "scan":
        if set_file is None:
            print("opf import: --scan requires --set FILE", file=sys.stderr)
            return EXIT_MALFORMED
        if ingest_form or include:
            print("opf import: --dispositions / --ingest-options / --include are valid only with the "
                  "root-ingest --plan form", file=sys.stderr)
            return EXIT_MALFORMED
        if actor is not None or decisions_file is not None or interactive:
            print("opf import: --actor / --decisions / --interactive are valid only with --review",
                  file=sys.stderr)
            return EXIT_MALFORMED
    elif mode == "plan":
        # --plan accepts EXACTLY ONE of two sub-forms: the declared-set import (--set FILE) XOR the
        # root-ingest disposition planner (--dispositions FILE --ingest-options FILE [--include ...]).
        if bool(set_file) == bool(ingest_form):
            print("opf import: --plan requires exactly one of --set FILE (declared-set import) or "
                  "--dispositions FILE --ingest-options FILE (root-ingest planner)", file=sys.stderr)
            return EXIT_MALFORMED
        if ingest_form and (dispositions_file is None or ingest_options_file is None):
            print("opf import: the root-ingest --plan form requires BOTH --dispositions FILE and "
                  "--ingest-options FILE", file=sys.stderr)
            return EXIT_MALFORMED
        if include and not ingest_form:
            print("opf import: --include is valid only with the root-ingest --plan form", file=sys.stderr)
            return EXIT_MALFORMED
        if actor is not None or decisions_file is not None or interactive:
            print("opf import: --actor / --decisions / --interactive are valid only with --review",
                  file=sys.stderr)
            return EXIT_MALFORMED
    elif mode == "review":
        if set_file is not None or ingest_form or include:
            print("opf import: --set / --dispositions / --ingest-options / --include are not valid with "
                  "--review", file=sys.stderr)
            return EXIT_MALFORMED
        if actor is None:
            print("opf import: --review requires --actor NAME", file=sys.stderr)
            return EXIT_MALFORMED
        if bool(decisions_file) == bool(interactive):
            print("opf import: --review requires exactly one of --decisions FILE / --interactive",
                  file=sys.stderr)
            return EXIT_MALFORMED
    else:   # apply
        if (set_file is not None or actor is not None or decisions_file is not None or interactive
                or ingest_form or include):
            print("opf import: --apply takes only a <run-id>", file=sys.stderr)
            return EXIT_MALFORMED

    # A run-id operand is an identifier: validate it against the U7 run-id grammar BEFORE any path use
    # (guard-input-soundness), so a value outside the grammar is a located cannot-evaluate, never a path.
    if run_id is not None and not _opf_import._RUN_ID_RE.match(run_id):
        print("opf import: run-id {!r} does not match the run-id grammar imp-<UTCSTAMP>Z-<hash16> "
              "(fail-closed)".format(run_id), file=sys.stderr)
        return EXIT_MALFORMED

    root_abs = os.path.abspath(root if root is not None else ".")
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        if mode == "scan":
            sources, _proposals = _import_read_set(set_file)
            res = _opf_import.scan_import(root_abs, sources)
            if res.verdict == _opf_import.CLEAN:
                sys.stdout.write(_opf_emit.emit_checked(res.inventory))
                return EXIT_OK
            for f in res.findings:
                print("opf import: {}".format(f), file=sys.stderr)
            return _import_exit(res.verdict)
        if mode == "plan":
            if dispositions_file is not None:            # root-ingest disposition planner (MIG-PR3)
                worksheet = _import_read_worksheet(dispositions_file)
                options = _import_read_options(ingest_options_file)
                res = _opf_ingest.plan_ingest(root_abs, worksheet, options, include=include or None,
                                              now=now, run_nonce=os.urandom(16).hex())
            else:                                        # existing declared-set importer
                sources, proposals = _import_read_set(set_file)
                res = _opf_import.plan_import(root_abs, sources, proposals=proposals, now=now,
                                              run_nonce=os.urandom(16).hex())
            if res.verdict == _opf_import.CLEAN:
                print("opf import: plan staged: run {}; report {}; migration_incomplete={}".format(
                    res.run_id, res.report_rel, res.migration_incomplete))
                return EXIT_OK
            for f in res.findings:
                print("opf import: {}".format(f), file=sys.stderr)
            return _import_exit(res.verdict)
        if mode == "review":
            if interactive:
                res = _opf_import.review_import_interactive(root_abs, run_id, actor=actor, now=now)
            else:
                decisions = _import_read_decisions(decisions_file, run_id)
                if isinstance(decisions, dict):
                    res = _opf_import.review_import(root_abs, run_id, actor=actor, now=now,
                                                    decisions=decisions["decisions"], ingest=decisions["ingest"])
                else:
                    res = _opf_import.review_import(root_abs, run_id, actor=actor, decisions=decisions, now=now)
            if res.verdict == _opf_import.CLEAN:
                print("opf import: acceptance captured: run {}; acceptance {}; {} decision(s)".format(
                    res.run_id, res.acceptance_rel, res.decisions_count))
                return EXIT_OK
            for f in res.findings:
                print("opf import: {}".format(f), file=sys.stderr)
            return _import_exit(res.verdict)
        # mode == "apply": every import mode requires a RESOLVED, initialized store (D7). Resolve first via
        # the operation layer's shared init-first precondition (single-sourced; the message is NOT
        # re-authored here) so a NOT-ADOPTED root reports "run `opf init` first" at exit 2, consistent with
        # scan / plan / review, rather than a promotion-specific message. An adopted store forwards to the
        # now-real apply_import (PR-C): a run with no acceptance is not promotion-ready -> exit 2 mutating
        # nothing, and a reviewed run promotes.
        try:
            _opf_import._resolve_store_for_review(root_abs)
        except _opf_import._StageError as exc:
            print("opf import: {}".format(exc.message), file=sys.stderr)
            return _import_exit(exc.verdict)
        res = _opf_import.apply_import(root_abs, run_id, now=now)
        if res.verdict == _opf_import.CLEAN:
            print("opf import: promotion {}: run {}".format(res.outcome, run_id))
            return EXIT_OK
        for f in res.findings:
            print("opf import: {}".format(f), file=sys.stderr)
        return _import_exit(res.verdict)
    except ValueError as exc:
        # A fail-closed --set / --decisions read error: cannot-evaluate (exit 2), never a silent skip.
        print("opf import: cannot evaluate: {}".format(exc), file=sys.stderr)
        return EXIT_MALFORMED
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false verdict or uncaught exit-1
        print("opf import: cannot evaluate: unexpected error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return EXIT_MALFORMED


def _cmd_record(rest):
    """`opf record <subcommand> ...` (spec 8.8): the record-authoring verb. `create` and `worklog-append`
    run the shared journaled operation sequence in _opf_record (byte-reproduction precondition, one id claim
    through the allocation seam, allowed-delta postcondition, cleanliness gate and lease, one journaled
    publication, render, final doctor VALID, lease release before the report); `transition` and
    `done-with-receipt` are recognized and fail closed as not yet implemented. Exit 0 recorded (left
    uncommitted), exit 2 every refusal or cannot-evaluate; exit 1 is not used. Unlike the applicability-probe
    siblings a NOT-ADOPTED root is a cannot-evaluate (a requested operation, like import)."""
    try:
        return _opf_record.cli(rest)
    except Exception as exc:  # noqa: BLE001  class-width fail-closed backstop, never a false success
        print("opf record: cannot evaluate: unexpected error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return EXIT_MALFORMED


def _cli_self_test():
    """Guard the dispatcher's render, doctor, import, record, and source-only init routes.
    Render/doctor cases below judge return codes; init also checks payload validation, refusal reasons,
    and preservation through check_opf_init._suite(main). Each case captures stdout and stderr.
    Cases: an unknown verb, no args, and every not-yet-wired KNOWN_VERB fail closed (exit 2); a bare `render`,
    both flags together, an unrecognized render flag, a `--root` with no value, and an empty `--root` are usage
    errors (exit 2); `render --check` and `render --write` FORWARD to the U4 engine -- a NOT-ADOPTED root
    returns 0 for each (the wiring discriminator: reverting the render wiring routes it to the fail-closed
    KNOWN_VERBS branch and returns 2, failing this case; render --write is never run against a mutating
    adopter store here, only NOT-ADOPTED / garbage synthetic roots) and a garbage store returns 2. For
    `doctor`: bad-flag / usage
    cases (a `--root` with no value, an unknown flag) fail closed (exit 2); a NOT-ADOPTED root returns 0 (the
    doctor wiring discriminator: reverting the doctor route routes `doctor` to the fail-closed KNOWN_VERBS
    branch and returns 2, failing this case); a garbage store returns 2. The render clean/drift 0/1
    discrimination rides check_opf_drift.py --self-test, and the doctor clean(0)/mutation(1) discrimination
    over a validate_store-VALID COMMITTED store rides check_opf_doctor.py --self-test, each driving the same
    wiring end to end. Returns 0 clean, 1 on a failure, 2 on a harness error.

    HARNESS fail-close (FIX 2): the fixture SETUP (tempfile.mkdtemp) and the fixture I/O (directory creation
    and writes) are the harness surface; an OSError from any of them is caught and returned as a located
    cannot-evaluate (exit 2), never allowed to escape uncaught (which Python would surface as exit 1). A final
    broad backstop routes any other residual error to exit 2 as well. Discriminating coverage injects an
    OSError at mkdtemp and at directory creation and asserts each routes to exit 2 (change-carries-check)."""
    import io
    import shutil
    import tempfile
    import contextlib

    try:
        failures = []

        def expect(argv, want):
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    got = main(list(argv))
            except BaseException as exc:                # a dispatcher crash is itself a failure
                failures.append("{!r} raised {!r}".format(argv, exc))
                return
            if got != want:
                failures.append("{!r} returned {!r} (expected {})".format(argv, got, want))

        # Routing cases that need no store on disk.
        expect([], EXIT_MALFORMED)
        expect(["frobnicate"], EXIT_MALFORMED)
        for verb in KNOWN_VERBS:
            if verb not in ("init", "import", "render", "doctor", "upgrade", "absorb", "record"):
                expect([verb], EXIT_MALFORMED)          # a known but not-yet-wired verb fails closed
        expect(["render"], EXIT_MALFORMED)              # bare: exactly one of --check/--write required
        expect(["render", "--check", "--write"], EXIT_MALFORMED)   # both flags refused
        expect(["render", "--bogus"], EXIT_MALFORMED)   # unknown render flag
        expect(["render", "--root"], EXIT_MALFORMED)    # --root needs a value
        expect(["render", "--check", "--root", ""], EXIT_MALFORMED)   # empty root refused

        # doctor verb ROUTING (PR-B), judged on exit code only. Bad-flag / usage cases need no store on disk.
        expect(["doctor", "--root"], EXIT_MALFORMED)    # --root needs a value
        expect(["doctor", "--check", "--root", ""], EXIT_MALFORMED)   # unknown doctor flag (and empty root)
        expect(["doctor", "--bogus"], EXIT_MALFORMED)   # unknown doctor flag

        # absorb verb ROUTING (OPF-CHANGELOG-ABSORB), judged on exit code only. These grammar cases fail
        # closed in the parser BEFORE any store resolution, so they need no store on disk. The NOT-ADOPTED
        # (0) / garbage (2) discrimination over a real root rides _fixture_leg below (reverting the absorb
        # dispatch routes these to the fail-closed KNOWN_VERBS branch, returning 2 where 0 is expected).
        expect(["absorb", "--root"], EXIT_MALFORMED)             # --root needs a value
        expect(["absorb", "--root", ""], EXIT_MALFORMED)         # empty root refused
        expect(["absorb", "--covers"], EXIT_MALFORMED)           # --covers needs a value
        expect(["absorb", "--covers", ""], EXIT_MALFORMED)       # empty covers refused
        expect(["absorb", "--bogus"], EXIT_MALFORMED)            # unknown arg

        # import verb ROUTING (OPF-IMPORT-VERB), judged on exit code only. These grammar cases fail closed in
        # the parser BEFORE any store resolution, so they need no store on disk. A bare `import` and every
        # malformed combination is a usage error (exit 2); the CLEAN 0 / FINDING 1 discrimination over a real
        # store rides _import_leg below (and reverting the import dispatch routes these to the fail-closed
        # KNOWN_VERBS branch, returning 2 where 0/1 is expected -- the wiring discriminator).
        _VALID_RID = "imp-20260101T000000Z-0123456789abcdef"   # syntactically valid; names no staged run
        expect(["import"], EXIT_MALFORMED)                       # bare: exactly one mode required
        expect(["import", "--root", "."], EXIT_MALFORMED)        # --root but no mode
        expect(["import", "--set", "s.toml"], EXIT_MALFORMED)    # --set but no mode
        expect(["import", "--scan", "--plan", "--set", "s.toml"], EXIT_MALFORMED)  # two modes
        expect(["import", "--scan"], EXIT_MALFORMED)             # --scan requires --set
        expect(["import", "--plan"], EXIT_MALFORMED)             # --plan requires --set
        expect(["import", "--scan", "--set"], EXIT_MALFORMED)    # --set needs a value
        expect(["import", "--scan", "--set", "s.toml", "--actor", "x"], EXIT_MALFORMED)  # actor only on review
        expect(["import", "--review"], EXIT_MALFORMED)           # --review needs a <run-id>
        expect(["import", "--review", _VALID_RID], EXIT_MALFORMED)   # missing --actor
        expect(["import", "--review", _VALID_RID, "--actor", "x"], EXIT_MALFORMED)  # neither decisions/interactive
        expect(["import", "--review", _VALID_RID, "--actor", "x", "--decisions", "d.json",
                "--interactive"], EXIT_MALFORMED)                # both decisions and interactive
        expect(["import", "--review", _VALID_RID, "--actor", "", "--interactive"], EXIT_MALFORMED)  # empty actor
        expect(["import", "--review", "not-a-run-id", "--actor", "x", "--interactive"], EXIT_MALFORMED)  # bad rid
        expect(["import", "--apply", _VALID_RID, "--actor", "x"], EXIT_MALFORMED)   # --apply takes only a run-id
        expect(["import", "--apply", "not-a-run-id"], EXIT_MALFORMED)   # bad run-id grammar
        expect(["import", "--bogus", "--scan", "--set", "s.toml"], EXIT_MALFORMED)  # unknown arg
        expect(["import", "--root"], EXIT_MALFORMED)             # --root needs a value

        # record verb ROUTING (OPF-RECORD), judged on exit code only. These grammar cases fail closed in the
        # parser BEFORE any store resolution, so they need no store on disk; the recorded 0 / refusal 2
        # discrimination over real stores rides check_opf_record.py --self-test.
        expect(["record"], EXIT_MALFORMED)                       # bare: a subcommand is required
        expect(["record", "frobnicate"], EXIT_MALFORMED)         # unknown subcommand
        expect(["record", "transition", "BI-1", "done"], EXIT_MALFORMED)   # recognized, not yet implemented
        expect(["record", "done-with-receipt", "BI-1"], EXIT_MALFORMED)    # recognized, not yet implemented
        expect(["record", "create"], EXIT_MALFORMED)             # missing --type/--title/--actor
        expect(["record", "create", "--root"], EXIT_MALFORMED)   # --root needs a value
        expect(["record", "worklog-append", "--kind", "added", "--summary", "s", "--actor", "importer"],
               EXIT_MALFORMED)                                   # an importer never authors through record

        def _fixture_leg():
            """Build the on-disk fixtures and drive render --check over them. Assertion outcomes are recorded
            in `failures`; returns None on success or EXIT_MALFORMED on a HARNESS error. The tempdir creation
            and every fixture directory/file write are the harness surface: an OSError from any of them is a
            located cannot-evaluate (exit 2), never an uncaught escape that Python would surface as exit 1."""
            try:
                base = tempfile.mkdtemp(prefix="opf-cli-selftest-")
            except OSError as exc:
                print("opf cli self-test: harness error: could not create the fixture tempdir ({})".format(
                    exc), file=sys.stderr)
                return EXIT_MALFORMED
            try:
                try:
                    # A NOT-ADOPTED root (no .working/): render --check FORWARDS to the U4 engine and returns
                    # 0 (NOT APPLICABLE) -- the wiring discriminator (an unwired render verb returns 2 here).
                    not_adopted = os.path.join(base, "not-adopted")
                    os.mkdir(not_adopted)
                    # A garbage store (a discovered but unparseable manifest): render --check fails closed
                    # (exit 2).
                    broken = os.path.join(base, "broken")
                    os.makedirs(os.path.join(broken, ".working", "toml"))
                    with open(os.path.join(broken, ".working", "toml", "manifest.toml"),
                              "w", encoding="utf-8") as fh:
                        fh.write("this is not valid toml {{{\n")
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build a fixture store ({})".format(
                        exc), file=sys.stderr)
                    return EXIT_MALFORMED
                expect(["render", "--check", "--root", not_adopted], EXIT_OK)
                expect(["render", "--check", "--root", broken], EXIT_MALFORMED)
                # render --write over synthetic roots ONLY (never a mutating adopter store, per the no-live-
                # write-in-CI posture): a NOT-ADOPTED root is NOT APPLICABLE and returns 0 writing nothing
                # (the write-wiring discriminator; an unwired --write routes to the fail-closed KNOWN_VERBS
                # branch and returns 2), and a garbage store fails closed (exit 2: gather + the U6 gate
                # refuse). The VALID/INVALID gate discrimination over a whole-store-valid fixture with
                # injected observations rides _opf_views.self_test end to end.
                expect(["render", "--write", "--root", not_adopted], EXIT_OK)
                expect(["render", "--write", "--root", broken], EXIT_MALFORMED)
                # doctor over the same synthetic roots: a NOT-ADOPTED root reports NOT APPLICABLE and returns
                # 0 -- the wiring discriminator (reverting the doctor route sends `doctor` to the fail-closed
                # KNOWN_VERBS branch, which returns 2 here, failing this case); a garbage store fails closed
                # (exit 2). The clean(0)/mutation(1) discrimination over a validate_store-VALID COMMITTED store
                # rides check_opf_doctor.py --self-test end to end (a committed HEAD is needed for the prior
                # observation), mirroring how the render clean/drift 0/1 rides check_opf_drift.py --self-test.
                expect(["doctor", "--root", not_adopted], EXIT_OK)
                expect(["doctor", "--root", broken], EXIT_MALFORMED)
                # upgrade over the same synthetic roots: a NOT-ADOPTED root reports NOT APPLICABLE and
                # returns 0 -- the wiring discriminator (reverting the upgrade route sends `upgrade` to the
                # fail-closed KNOWN_VERBS branch, which returns 2 here, failing this case); a garbage store
                # fails closed (exit 2). The full 1.0.0 -> 1.1.0 migration discrimination (schema delta,
                # canonical-bytes precondition, doctor-VALID gate, idempotence, and the above-tooling refusal)
                # rides check_opf_upgrade.py --self-test end to end over a byte-pinned committed 1.0.0 store.
                expect(["upgrade", "--root", not_adopted], EXIT_OK)
                expect(["upgrade", "--root", broken], EXIT_MALFORMED)
                # absorb over the same synthetic roots: a NOT-ADOPTED root reports NOT APPLICABLE and returns
                # 0 -- the wiring discriminator (reverting the absorb route sends `absorb` to the fail-closed
                # KNOWN_VERBS branch, which returns 2 here, failing this case); a garbage store fails closed
                # (exit 2). The OK-draft discrimination over a resolved store rides _opf_absorb.self_test end
                # to end (it builds its own valid synthetic stores).
                expect(["absorb", "--root", not_adopted], EXIT_OK)
                expect(["absorb", "--root", broken], EXIT_MALFORMED)
            finally:
                shutil.rmtree(base, ignore_errors=True)
            return None

        def _import_leg():
            """Build a VALID synthetic store and drive the import round-trip end to end, judged on exit
            codes AND observable side effects. Returns None on success or EXIT_MALFORMED on a harness
            (fixture I/O) error. Each 0/1 vector is a deliberate flip: reverting the import dispatch routes
            it to the fail-closed KNOWN_VERBS branch (returning 2 where 0/1 is expected). Vectors: an
            unresolved store -> 2 with the init-first message (D7); --scan over a valid store -> 0 AND the
            store tree byte-unchanged (pure read); --plan -> 0 staging one run; --review with an INCOMPLETE
            decisions file -> 1 and NO acceptance.json; --review with a COMPLETE decisions file -> 0 and a
            schema-valid acceptance.json; --review --interactive over a non-TTY stdin -> 2 (interactive
            front-end refusal); --apply over an UNREVIEWED staged run -> 2 (now-real apply_import finds no
            acceptance.json) AND the store byte-unchanged (nothing promoted; a reviewed run promotes)."""
            import tomllib

            def tree_snapshot(rootdir):
                snap = {}
                for dirpath, _dirs, files in os.walk(rootdir):
                    for name in files:
                        p = os.path.join(dirpath, name)
                        with open(p, "rb") as fh:
                            snap[os.path.relpath(p, rootdir)] = fh.read()
                return snap

            try:
                ibase = tempfile.mkdtemp(prefix="opf-cli-import-")
            except OSError as exc:
                print("opf cli self-test: harness error: could not create the import fixture tempdir "
                      "({})".format(exc), file=sys.stderr)
                return EXIT_MALFORMED
            try:
                try:
                    store = os.path.join(ibase, "store")
                    working = os.path.join(store, ".working")
                    machine = os.path.join(working, "toml")
                    os.makedirs(machine)
                    manifest = "\n".join([
                        "[opf]", 'standard = "opf"',
                        'spec_version = "{}"'.format(_opf_store.SUPPORTED_SPEC_VERSION),
                        'layout = "inline"', 'posture = "required"', 'import_status = "none"',
                        "", "[store]", 'sync_target = ""',
                        "", "[modules]", "governance = true",
                        "", "[types.backlog_item]", 'namespace = "BI"',
                        "", "[vendors]", 'registered = []', "",
                    ]) + "\n"
                    with open(os.path.join(machine, "manifest.toml"), "w", encoding="utf-8") as fh:
                        fh.write(manifest)
                    with open(os.path.join(machine, "counters.toml"), "w", encoding="utf-8") as fh:
                        fh.write("schema = 1\n\n[counters]\nBI = 0\nLF = 0\nWL = 0\n")
                    with open(os.path.join(store, "a.txt"), "w", encoding="utf-8") as fh:
                        fh.write("first source body\n")
                    with open(os.path.join(store, "b.txt"), "w", encoding="utf-8") as fh:
                        fh.write("second source body\n")
                    set_file = os.path.join(ibase, "set.toml")
                    with open(set_file, "w", encoding="utf-8") as fh:
                        fh.write('schema = 1\nsource = ["a.txt", "b.txt"]\n')
                    not_adopted = os.path.join(ibase, "not-adopted")
                    os.mkdir(not_adopted)
                except OSError as exc:
                    print("opf cli self-test: harness error: could not build the import fixture "
                          "({})".format(exc), file=sys.stderr)
                    return EXIT_MALFORMED

                # 1: unresolved store -> exit 2 AND the init-first message (D7): assert code AND message.
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["import", "--scan", "--set", set_file, "--root", not_adopted])
                if rc != EXIT_MALFORMED or "opf init" not in buf.getvalue():
                    failures.append("import --scan over a NOT-ADOPTED root: rc={!r} (expected 2 + an "
                                    "init-first message)".format(rc))

                # 2: --scan over a valid store -> 0 AND the store tree byte-unchanged (scan writes nothing).
                before = tree_snapshot(store)
                expect(["import", "--scan", "--set", set_file, "--root", store], EXIT_OK)
                if tree_snapshot(store) != before:
                    failures.append("import --scan mutated the store tree (scan must be a pure read)")

                # 3: --plan over a valid store -> 0, staging exactly one run dir under .working/imports/.
                expect(["import", "--plan", "--set", set_file, "--root", store], EXIT_OK)
                imports_dir = os.path.join(working, "imports")
                run_ids = sorted(os.listdir(imports_dir)) if os.path.isdir(imports_dir) else []
                if len(run_ids) != 1:
                    failures.append("import --plan staged {} run(s), expected exactly 1".format(
                        len(run_ids)))
                    return None
                rid = run_ids[0]
                run_dir = os.path.join(imports_dir, rid)

                # Build the decisions from the staged inventory + mappings: join fragment_id (inventory) to
                # the AUTHORITATIVE origin/state (mappings) by (source_path, span). _validate_review_decisions
                # requires the decision to echo the staged origin/proposed_state, so a changed baseline
                # classification would break this vector loudly (a change detector), never silently.
                with open(os.path.join(run_dir, "inventory.toml"), "rb") as fh:
                    inv = tomllib.load(fh)
                with open(os.path.join(run_dir, "mappings.toml"), "rb") as fh:
                    maps = tomllib.load(fh)
                meta = {(m["source_path"], tuple(m["span"])): (m["origin"], m["state"])
                        for m in maps["mapping"]}
                all_decisions = []
                for frag in inv["fragment"]:
                    origin, state = meta[(frag["source_path"], tuple(frag["span"]))]
                    all_decisions.append({"fragment_id": frag["fragment_id"], "decision": "accept",
                                          "origin": origin, "proposed_state": state, "note": ""})
                if len(all_decisions) != 2:
                    failures.append("staged plan carried {} fragment(s), expected 2".format(
                        len(all_decisions)))
                    return None
                complete = os.path.join(ibase, "complete.json")
                with open(complete, "w", encoding="utf-8") as fh:
                    json.dump({"schema": 1, "run_id": rid, "decisions": all_decisions}, fh)
                incomplete = os.path.join(ibase, "incomplete.json")
                with open(incomplete, "w", encoding="utf-8") as fh:
                    json.dump({"schema": 1, "run_id": rid, "decisions": all_decisions[:1]}, fh)
                acceptance = os.path.join(run_dir, "acceptance.json")

                # 4: --review with an INCOMPLETE decisions file -> 1 (finding), NO acceptance.json written.
                expect(["import", "--review", rid, "--actor", "tester", "--decisions", incomplete,
                        "--root", store], EXIT_FINDING)
                if os.path.exists(acceptance):
                    failures.append("import --review with an incomplete decisions file wrote "
                                    "acceptance.json (a finding must write nothing)")

                # 5: --review with a COMPLETE decisions file -> 0 and a schema-valid acceptance.json present.
                expect(["import", "--review", rid, "--actor", "tester", "--decisions", complete,
                        "--root", store], EXIT_OK)
                if not os.path.isfile(acceptance):
                    failures.append("import --review (complete) did not write acceptance.json")
                else:
                    with open(acceptance, "rb") as fh:
                        acc = json.load(fh)
                    if acc.get("format") != "opf.import.acceptance/v1" or acc.get("run_id") != rid:
                        failures.append("acceptance.json is not a valid opf.import.acceptance/v1 bound to "
                                        "the run")

                # 6: --review --interactive over a NON-TTY stdin -> 2 (routes to the interactive front-end,
                # which refuses a non-TTY). Swap stdin to a StringIO so the vector is hermetic regardless of
                # the test process's real stdin (test-hermeticity).
                real_stdin = sys.stdin
                sys.stdin = io.StringIO("")
                try:
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        rc = main(["import", "--review", rid, "--actor", "tester", "--interactive",
                                   "--root", store])
                finally:
                    sys.stdin = real_stdin
                if rc != EXIT_MALFORMED:
                    failures.append("import --review --interactive over a non-TTY: rc={!r} (expected "
                                    "2)".format(rc))

                # 7: --apply over the REVIEWED staged run (PR-C, apply is now REAL). This minimal fixture
                # store declares only [types.backlog_item], so the assembled candidate is NOT doctor-
                # composable and apply's D4 composition gate aborts fail-closed -> EXIT_FINDING (1), nothing
                # promoted, and the store tree is byte-unchanged (the abort precedes any publication write).
                # This CONSCIOUSLY replaces the former deferred-stub exit-2 pin (change-carries-check): the
                # deferred stub returned 2 for every run, so a real exit-1 composition abort flips it red.
                store_before = tree_snapshot(store)
                expect(["import", "--apply", rid, "--root", store], EXIT_FINDING)
                if tree_snapshot(store) != store_before:
                    failures.append("import --apply (composition abort) mutated the store (must mutate nothing)")

                # 8 (F1 schema bool/float-slip, R5-F2 class at the new CLI readers): a --set whose schema is
                # a bool (True == 1) or a float (1.0 == 1) is a MALFORMED file -> exit 2, never accepted.
                # Flip: dropping the `type(...) is int` guard accepts both and --scan returns 0.
                set_true = os.path.join(ibase, "set-schema-true.toml")
                with open(set_true, "w", encoding="utf-8") as fh:
                    fh.write('schema = true\nsource = ["a.txt"]\n')
                expect(["import", "--scan", "--set", set_true, "--root", store], EXIT_MALFORMED)
                set_float = os.path.join(ibase, "set-schema-float.toml")
                with open(set_float, "w", encoding="utf-8") as fh:
                    fh.write('schema = 1.0\nsource = ["a.txt"]\n')
                expect(["import", "--scan", "--set", set_float, "--root", store], EXIT_MALFORMED)
                # The --decisions reader carries the same class: a bool schema is malformed -> exit 2.
                dec_true = os.path.join(ibase, "dec-schema-true.json")
                with open(dec_true, "w", encoding="utf-8") as fh:
                    json.dump({"schema": True, "run_id": rid, "decisions": []}, fh)
                expect(["import", "--review", rid, "--actor", "tester", "--decisions", dec_true,
                        "--root", store], EXIT_MALFORMED)

                # 9 (F2 --set proposal-row STRUCTURE, fail-closed consistently for scan AND plan): a
                # structurally-malformed --set is exit 2 for BOTH modes. Flip: without the row-structure
                # validation, --scan silently ignores the proposal (0) while --plan forwards it to
                # _validate_proposals as a finding (1) -- the exit-code inconsistency this closes.
                set_badprop = os.path.join(ibase, "set-badprop.toml")   # scalars where tables are required
                with open(set_badprop, "w", encoding="utf-8") as fh:
                    fh.write('schema = 1\nsource = ["a.txt"]\nproposal = [5, 7]\n')
                expect(["import", "--scan", "--set", set_badprop, "--root", store], EXIT_MALFORMED)
                expect(["import", "--plan", "--set", set_badprop, "--root", store], EXIT_MALFORMED)
                set_misskey = os.path.join(ibase, "set-misskey.toml")   # a row missing source_path
                with open(set_misskey, "w", encoding="utf-8") as fh:
                    fh.write('schema = 1\nsource = ["a.txt"]\n\n[[proposal]]\n'
                             'span = [0, 1]\nsuggested_state = "mapped"\n')
                expect(["import", "--scan", "--set", set_misskey, "--root", store], EXIT_MALFORMED)
                expect(["import", "--plan", "--set", set_misskey, "--root", store], EXIT_MALFORMED)
                set_badspan = os.path.join(ibase, "set-badspan.toml")   # span not a two-int list
                with open(set_badspan, "w", encoding="utf-8") as fh:
                    fh.write('schema = 1\nsource = ["a.txt"]\n\n[[proposal]]\n'
                             'source_path = "a.txt"\nspan = [0, 1, 2]\nsuggested_state = "mapped"\n')
                expect(["import", "--plan", "--set", set_badspan, "--root", store], EXIT_MALFORMED)

                # 10 (F2 reader/op-layer SPLIT proof): a STRUCTURALLY-valid proposal that is SEMANTICALLY
                # invalid (span beyond the source size) passes the reader and is a _validate_proposals
                # FINDING -> exit 1 via --plan, NEVER a reader exit 2. This proves the reader owns STRUCTURE
                # while the operation layer still owns SEMANTICS (span bounds, state vocab, confinement).
                set_oob = os.path.join(ibase, "set-oob-span.toml")
                with open(set_oob, "w", encoding="utf-8") as fh:
                    fh.write('schema = 1\nsource = ["a.txt"]\n\n[[proposal]]\n'
                             'source_path = "a.txt"\nspan = [0, 100000]\nsuggested_state = "mapped"\n')
                expect(["import", "--plan", "--set", set_oob, "--root", store], EXIT_FINDING)

                # 11 (F3 --apply init-first parity, D7): --apply <valid-rid> on a NOT-ADOPTED root reports the
                # init-first message at exit 2, not the deferred-promotion stub message. Flip: without the
                # CLI-side store resolution, --apply calls the stub unconditionally and a NOT-ADOPTED root
                # gets the deferred message with no "opf init".
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["import", "--apply", _VALID_RID, "--root", not_adopted])
                if rc != EXIT_MALFORMED or "opf init" not in buf.getvalue():
                    failures.append("import --apply over a NOT-ADOPTED root: rc={!r} (expected 2 + an "
                                    "init-first message)".format(rc))
                # On an ADOPTED store naming NO staged run, --apply (PR-C, real) loads the run fail-closed,
                # finds no such promotion-ready run, and reports "not promotion-ready" at exit 2, mutating
                # nothing (the store tree is byte-unchanged; the journal/lock infrastructure under .aiqt/ adds
                # no files once the lock is released). This replaces the former deferred-stub message pin.
                store_before_apply = tree_snapshot(store)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    rc = main(["import", "--apply", _VALID_RID, "--root", store])
                if rc != EXIT_MALFORMED or "not promotion-ready" not in buf.getvalue():
                    failures.append("import --apply over an ADOPTED store (unknown run): rc={!r} (expected "
                                    "2 + a not-promotion-ready message)".format(rc))
                if tree_snapshot(store) != store_before_apply:
                    failures.append("import --apply (adopted, unknown run) mutated the store")

                # 12 (F4 read-boundary RecursionError parity, R8-F1 class): a deeply-nested --decisions JSON
                # raises RecursionError from json.loads (not fh.read). The reader catches it and fails closed
                # AT THE reader with a LOCATED message -> exit 2, never escaping only to _cmd_import's outer
                # backstop. The C json scanner's nesting depth is a platform constant, so the property is
                # driven HERMETICALLY by patching json.loads to raise RecursionError for one call (restored in
                # a finally; the same stdlib-injection idiom as _expect_harness below). Flip: dropping
                # RecursionError from the parse except lets it escape and the located "--decisions file"
                # reader message is absent from the output.
                real_loads = json.loads

                def _boom_loads(*_a, **_k):
                    raise RecursionError("maximum recursion depth exceeded (simulated deep --decisions JSON)")

                json.loads = _boom_loads
                try:
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                        rc = main(["import", "--review", rid, "--actor", "tester", "--decisions", complete,
                                   "--root", store])
                finally:
                    json.loads = real_loads
                if rc != EXIT_MALFORMED or "--decisions file" not in buf.getvalue():
                    failures.append("import --review with a RecursionError-raising decisions JSON: rc={!r} "
                                    "(expected 2 + a located reader message)".format(rc))

                # 13 (R2-F5 --decisions envelope CLOSED-KEYSET, class-width parity with the --set envelope
                # + proposal rows): an otherwise-VALID --decisions file (schema 1, matching run_id, list
                # decisions) carrying an EXTRA top-level key -- even one nested deeply -- is a MALFORMED
                # envelope -> exit 2, never silently accepted. Flip: without the closed-keyset check the
                # extra-key file exits 0 (the exact round-2 bug). The SAME content WITHOUT the extra key
                # still reviews at exit 0 (vector 5 above proved `complete` -> 0), so this isolates the
                # extra key alone as the discriminator.
                dec_extra = os.path.join(ibase, "dec-extra-key.json")
                junk = "x"
                for _ in range(40):
                    junk = {"n": junk}
                with open(dec_extra, "w", encoding="utf-8") as fh:
                    json.dump({"schema": 1, "run_id": rid, "decisions": all_decisions, "extra": junk}, fh)
                expect(["import", "--review", rid, "--actor", "tester", "--decisions", dec_extra,
                        "--root", store], EXIT_MALFORMED)
            finally:
                shutil.rmtree(ibase, ignore_errors=True)
            return None

        harness_rc = _fixture_leg()
        if harness_rc is not None:
            return harness_rc
        import_rc = _import_leg()
        if import_rc is not None:
            return import_rc

        # Discriminating harness-path coverage (FIX 2): an injected OSError at fixture SETUP (mkdtemp) and at
        # fixture I/O (directory creation) must each route to the located cannot-evaluate (exit 2), never
        # escape uncaught (which Python surfaces as exit 1). Judged on the returned code only; each probe
        # restores the patched callable in a finally so no later leg runs under the injection.
        def _refuse(*_a, **_k):
            raise OSError("simulated harness I/O refusal")

        def _expect_harness(label, obj, attr):
            real = getattr(obj, attr)
            setattr(obj, attr, _refuse)
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = _fixture_leg()
            finally:
                setattr(obj, attr, real)
            if rc != EXIT_MALFORMED:
                failures.append("harness {}: _fixture_leg returned {!r} (expected {})".format(
                    label, rc, EXIT_MALFORMED))

        _expect_harness("mkdtemp-oserror", tempfile, "mkdtemp")
        _expect_harness("makedirs-oserror", os, "makedirs")

        # The same fixture vectors drive this in-process dispatcher and the dedicated gate's
        # isolated child. A refusal must name its reason and preserve the fixture, not merely return 2.
        import check_opf_init
        init_rc = check_opf_init._suite(main)
        if init_rc == EXIT_MALFORMED:
            return EXIT_MALFORMED
        if init_rc != EXIT_OK:
            failures.append("source-only init fixture vectors failed")

        if failures:
            for f in failures:
                print("opf cli self-test: FAIL: {}".format(f), file=sys.stderr)
            return EXIT_FINDING
        print("opf cli self-test: PASS (verb routing: unknown/unwired verbs and render/doctor/import usage "
              "errors fail closed; render --check forwards to the U4 engine; doctor resolves + validates a "
              "store, NOT-ADOPTED -> 0 and a garbage store -> 2; import wires scan/plan/review/apply onto "
              "the U7 operation layer -- an unresolved store -> 2 init-first, --scan -> 0 writing nothing, "
              "--plan -> 0 staging a run, --review incomplete -> 1 and complete -> 0 with a valid "
              "acceptance.json, non-TTY --interactive -> 2, and --apply over a reviewed run on a non-doctor-"
              "composable fixture -> 1 (composition abort) and over an unknown run -> 2 not-promotion-ready, "
              "each mutating nothing; fixture-setup and fixture-I/O OSError fail closed to exit 2)")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  final fail-closed backstop, never an uncaught exit-1 escape
        print("opf cli self-test: harness error: unexpected error ({!r}); failing closed to exit 2".format(
            exc), file=sys.stderr)
        return EXIT_MALFORMED


# Registered helper self-tests, run by `opf.py --self-test`. Each is (label, callable) returning a
# 0/1/2 exit code (0 clean, 1 finding, 2 cannot-evaluate). Later units append their own helper here.
# Built by a function rather than a module-level tuple because the _opf_* helpers it references are bound by
# _bootstrap() inside main(), not at module import; it is called after _bootstrap() has run.
def _self_tests():
    """Return the registered (label, callable) helper self-tests run by `opf.py --self-test`. Called after
    _bootstrap() has bound the _opf_* helpers, so every referenced helper is present."""
    return (
    ("opf-store", _opf_store.self_test),
    ("opf-schema", _opf_schema.self_test),
    ("opf-release", _opf_release.self_test),
    ("opf-worklog", _opf_worklog.self_test),
    ("opf-changelog", _opf_changelog.self_test),
    ("opf-emit", _opf_emit.self_test),
    ("opf-views", _opf_views.self_test),
    ("opf-import", _opf_import.self_test),
    ("opf-importers", _opf_importers.self_test),
    ("opf-observe", _opf_observe.self_test),
    ("opf-absorb", _opf_absorb.self_test),
    ("opf-record", _opf_record.self_test),
    ("opf-fuzz", _opf_fuzz.self_test),
    ("opf-check", _opf_check.self_test),
    ("opf-watchdog-isolation", _watchdog_isolation_self_test),
    ("opf-watchdog-regressions", _watchdog_regression_self_test),
    ("opf-aggregator", _aggregator_self_test),
    ("opf-cli", _cli_self_test),
)

# The spec's command vocabulary (spec 1). Each lands in its own unit; until then a verb fails closed.
KNOWN_VERBS = ("init", "import", "doctor", "render", "migrate", "sync", "upgrade", "absorb", "record")


# Helper self-tests that pin sys.set_int_max_str_digits(4300) inside a fixture and MUST restore the ambient
# value in a finally (the round-7 int-limit hermeticity work). run_self_tests guards that RESTORE half below.
_INT_LIMIT_SELF_TESTS = frozenset({"opf-release", "opf-emit", "opf-schema", "opf-fuzz", "opf-import"})


def run_self_tests(tests=None):
    """Keep caller HOME/XDG out of fixture reads, including in-process production helpers."""
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-selftest-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return run_self_tests_isolated(tests)


def run_self_tests_isolated(tests=None):
    """Run every registered helper self-test in order, forwarding each result. The aggregate exit code
    is the WORST outcome (2 cannot-evaluate > 1 finding > 0 clean): one degraded or failing helper fails
    the whole leg, never masked by a later clean one. With no explicit `tests`, the registered set is built
    by _self_tests() at call time (after _bootstrap() has bound the _opf_* helpers), never a module-level
    default that would need those helpers imported at module top.

    Int-limit hermeticity guard (finding 8-4): each helper in _INT_LIMIT_SELF_TESTS pins the int-string
    conversion limit to 4300 inside its fixtures and must RESTORE the ambient value afterward. That restore
    had no fails-if-reverted check: under the DEFAULT ambient (already 4300) a dropped restore leaves 4300
    and is invisible. So around each such helper we set a distinct SENTINEL limit (!= 4300 and != the real
    ambient) and, after it runs, require the limit to STILL be that sentinel before restoring the real
    ambient; a dropped restore in any of those helpers leaves 4300 != sentinel and fails the leg closed.
    These helpers are hermetic w.r.t. the ambient int-limit by construction (they pin their own 4300), so
    running them under the sentinel is exactly the hostile-ambient contract they already satisfy."""
    if tests is None:
        tests = _self_tests()
    worst = EXIT_OK
    _idlimit_orig = sys.get_int_max_str_digits()
    _idlimit_sentinel = 271828 if _idlimit_orig != 271828 else 314159   # distinct from 4300 AND from ambient
    for label, fn in tests:
        print("== opf self-test: {} ==".format(label))
        _guard_idlimit = label in _INT_LIMIT_SELF_TESTS
        if _guard_idlimit:
            sys.set_int_max_str_digits(_idlimit_sentinel)
            try:
                code = fn()
            finally:
                _idlimit_after = sys.get_int_max_str_digits()
                sys.set_int_max_str_digits(_idlimit_orig)   # restore the real ambient regardless of outcome
            if _idlimit_after != _idlimit_sentinel:
                print("opf self-test: {} left sys.get_int_max_str_digits at {} (expected the sentinel {}); a "
                      "dropped int-limit restore is a hermeticity leak, failing closed (finding 8-4)".format(
                          label, _idlimit_after, _idlimit_sentinel), file=sys.stderr)
                worst = EXIT_MALFORMED
        else:
            code = fn()
        if not (type(code) is int and code in (EXIT_OK, EXIT_FINDING, EXIT_MALFORMED)):
            # A helper whose return is not an int of exactly {0,1,2} is itself a fault: fail closed (the
            # worst outcome) rather than letting an unrecognized code read as clean. `type(code) is int`
            # deliberately EXCLUDES bool (a subclass of int, where False == 0 and True == 1) and float
            # (0.0 == 0), so a helper returning False or 0.0 can never be admitted as a clean pass.
            print("opf self-test: {} returned out-of-range code {!r}; failing closed".format(label, code),
                  file=sys.stderr)
            worst = EXIT_MALFORMED
        elif code == EXIT_MALFORMED:
            worst = EXIT_MALFORMED
        elif code == EXIT_FINDING and worst != EXIT_MALFORMED:
            worst = EXIT_FINDING
    return worst


def main(argv=None):
    # Guarded helper bootstrap FIRST: a broken or partial install (an unimportable/unreadable _opf_* helper)
    # maps to a located cannot-evaluate (exit 2), never an uncaught ImportError escaping as Python's default
    # exit 1 that a direct `opf render --check` would read as a false drift. Idempotent, so the repeated
    # main() calls in the CLI self-test cost nothing once the helpers are loaded.
    rc = _bootstrap()
    if rc != EXIT_OK:
        return rc
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return run_self_tests()
    if not args or args[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return EXIT_MALFORMED
    verb = args[0]
    rest = args[1:]
    if verb == "render":
        return _cmd_render(rest)
    if verb == "doctor":
        return _cmd_doctor(rest)
    if verb == "init":
        return _cmd_init(rest)
    if verb == "upgrade":
        return _cmd_upgrade(rest)
    if verb == "import":
        return _cmd_import(rest)
    if verb == "absorb":
        return _cmd_absorb(rest)
    if verb == "record":
        return _cmd_record(rest)
    if verb in KNOWN_VERBS:
        # A recognized verb whose unit has not landed: fail closed (exit 2), never a silent success, so
        # a stub is never mistaken for a completed operation.
        print("opf {}: not yet implemented in this build (fail-closed)".format(verb), file=sys.stderr)
        return EXIT_MALFORMED
    print("opf: unknown verb {!r}; known verbs: {}".format(verb, ", ".join(KNOWN_VERBS)), file=sys.stderr)
    return EXIT_MALFORMED


if __name__ == "__main__":
    sys.exit(main())
