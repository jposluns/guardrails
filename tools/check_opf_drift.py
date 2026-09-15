#!/usr/bin/env python3
"""OPF render-drift gate: `opf render --check` over the adopter store at the repository root.

A thin gate wrapper in the check_*.py family (deliberately NOT gen_*.py: an OPF render targets an adopter
--root with no fixed repo-relative target, so it gates as a self-test plus a live leg, exactly the posture
opf.py itself documents). The live leg runs `opf.py render --check --root <repo-root>` and CLASSIFIES the
child's outcome by the render check's drift MARKER, not by the bare child exit code (the marker mapping is
below); it preserves gate discipline throughout (no truncating sink, no `|| true`, no status-masking
trailer).
The repo root is DERIVED by CONFIRMING the repository IDENTITY of the gate's OWN source location through a
local git probe (git -C <gate-dir> rev-parse --show-toplevel, with the ambient Git environment scrubbed and
the git executable resolved via shutil.which then absolutized with os.path.abspath, anchoring a relative
which() result to the cwd so the launched probe is pinned absolute against exec-time re-resolution), not by
the mere PRESENCE of a `.git` entry: a stray or garbage `.git`
marker beside the gate would satisfy a bare existence test yet is not a real repository root. Unlike
_gen_common.repo_root() it never falls back to the current directory, so a run whose root cannot be
confirmed (git missing, a launch failure, an invalid/garbage gitfile, or a toplevel that does not contain
the gate) fails closed (exit 2) rather than silently checking the WRONG root and returning a false 0
(guard-input-soundness).

This repository is not a DevProcess adopter, so the live leg prints render's own NOT APPLICABLE and exits 0,
spec-honest like the crosswalk/doctor legs in run_all_checks.sh; the day this repo adopts, the same leg
gates real view drift with no change. The gate's verdict uses render's own 0/1/2 vocabulary (0 clean, 1
drift, 2 cannot-evaluate) but is assigned from the drift MARKER, not the bare child exit code. The mapping is
three-way: a clean child (exit 0) is a PASS (0), and its stdout is NOT inspected for exit 0 because a clean
render prints nothing; a child that exits with the drift code (1) AND carries the render check's drift MARKER
on stdout (a `drift:` sentinel line, which `opf render --check` prints per drifted target) is DRIFT (1); and
EVERY OTHER outcome routes to cannot-evaluate (exit 2). This closes the exit-1=drift conflation at the
INTERPRETATION layer rather than per failure mode: a child that exits 1 WITHOUT the drift marker (a
module-import crash before opf.py's handler, an uncaught exception, or any abnormal exit-1 path) produced no
render drift verdict and is a cannot-evaluate, never a false drift; a child-LAUNCH failure (an OS refusal
such as BlockingIOError under RLIMIT_NPROC pressure) is caught and reported as a cannot-evaluate rather than
escaping as the gate's own exit 1; and an unexpected or abnormal child status (a non 0/1/2 code, a signal
death surfacing as a negative return code, or empty/garbled output) is a cannot-evaluate too. The child's
stdout is surfaced to the console in the live leg; render's own internal failures fail closed to exit 2
upstream of this leg.

--self-test builds SYNTHETIC adopter stores in a tempdir and asserts that 0/1/2 contract END TO END through
opf.py: 0 on a clean populated store, 1 after a view is edited, 2 on a broken store, and 0 (NOT APPLICABLE)
on a non-adopter root. The clean store's views are populated through the U4 engine's own public planner
(_opf_views.plan_views), never hand-built, so a clean render is clean by construction. Offline, stdlib only,
fail-closed, launched isolated (-I -B). The tempdir is removed in a finally (test-hermeticity).
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for the self-test's sibling imports below

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

# The render check's POSITIVE drift signal. `opf render --check` prints one `drift: <path>` line to STDOUT
# per drifted target (see _opf_views.render / opf_render), then a regenerate hint, and exits 1. Errors go to
# STDERR. The gate recognizes genuine DRIFT by this marker rather than by the bare exit code, so a child that
# exits 1 for any OTHER reason (a module-import crash, an uncaught exception) cannot masquerade as drift. The
# marker is matched at a line's START so an incidental mid-line occurrence cannot spoof it.
DRIFT_MARKER = "drift:"


def _has_drift_marker(text):
    """True when `text` (the render child's captured stdout) carries the render check's drift sentinel: at
    least one line BEGINNING with DRIFT_MARKER, the `drift: <path>` line _opf_views.render / opf_render emit
    per drifted target. Matched at line start so an incidental mid-line occurrence cannot masquerade as it."""
    return any(line.startswith(DRIFT_MARKER) for line in text.splitlines())


def _run_render_check(root, capture):
    """Run `opf.py render --check --root <root>` isolated (-I -B) and CLASSIFY its outcome by a POSITIVE
    drift signal, never by the exit code alone. The gate reports DRIFT (exit 1) ONLY on a CONFIRMED drift:
    the child exited with the drift code (1) AND its stdout carries the render check's drift MARKER (a
    `drift:` sentinel line). EVERY other outcome routes to cannot-evaluate (EXIT_ERROR), never a false drift
    1. This closes the exit-1=drift conflation at the INTERPRETATION layer rather than per failure mode: a
    child that exits 1 WITHOUT the drift marker (a module-import crash before opf.py's handler, an uncaught
    exception, or any abnormal exit-1 path) produced NO render drift verdict, so it is a cannot-evaluate, not
    drift; a child-LAUNCH failure (an OS refusal such as BlockingIOError/OSError when a fork is refused under
    RLIMIT_NPROC pressure) is caught HERE; and exit 2, an unexpected non-0/1/2 status, a signal death
    (negative return), or empty/garbled output is a cannot-evaluate. Only a clean child (exit 0) is clean,
    and its stdout is NOT inspected for exit 0 because a clean render prints nothing; the marker is examined
    only to CONFIRM a drift (exit 1), never to qualify the clean pass.

    stdout is CAPTURED so the marker can be recognized (decoded with errors='replace', so garbled bytes
    route to cannot-evaluate rather than crashing the gate). In the live leg (capture False) it is re-emitted
    to the console after the run so the operator still sees the child's `drift:`/regenerate output, and the
    child's stderr is inherited (streamed) there; in the self-test leg (capture True) stdout is inspected but
    not re-emitted and stderr is discarded, so a passing leg stays quiet."""
    tools_dir = Path(__file__).resolve().parent
    err_sink = subprocess.DEVNULL if capture else None
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(tools_dir / "opf.py"),
             "render", "--check", "--root", str(root)],
            stdout=subprocess.PIPE, stderr=err_sink)
    except OSError as exc:
        # An OS refusal AT LAUNCH (BlockingIOError et al., e.g. fork refused under RLIMIT_NPROC) escapes
        # before any return code exists. Map it to a located cannot-evaluate (exit 2), never let it
        # propagate and exit the gate 1, which would read as a false DRIFT verdict though no child ran.
        print("check_opf_drift: cannot evaluate: could not launch the render child for root {} ({}); "
              "no child ran, so no drift verdict exists".format(root, exc), file=sys.stderr)
        return EXIT_ERROR
    out = (proc.stdout or b"").decode("utf-8", "replace")
    if not capture and out:
        sys.stdout.write(out)   # surface the child's stdout to the console in the live gate leg
    rc = proc.returncode
    if rc == EXIT_OK:
        return EXIT_OK
    if rc == EXIT_DRIFT and _has_drift_marker(out):
        return EXIT_DRIFT
    # Every other outcome is a cannot-evaluate, NOT drift: exit 1 without the drift marker (an import crash,
    # an uncaught exception, or a non-render child), exit 2, an unexpected non-0/1/2 status, or a signal
    # death surfacing as a negative return code. This is the interpretation-layer close of the exit-1=drift
    # conflation: DRIFT is recognized positively, never inferred from a bare exit code.
    print("check_opf_drift: cannot evaluate: the render child for root {} did not produce a confirmed clean "
          "(exit 0) or confirmed-drift (exit 1 with a '{}' marker line) result (child exit {}); a child that "
          "exits 1 without the drift marker -- a module-import crash, an uncaught exception, or a launch "
          "failure -- is not drift".format(root, DRIFT_MARKER, rc), file=sys.stderr)
    return EXIT_ERROR


def _self_test():
    """Build synthetic stores and assert the 0/1/2 contract end to end through opf.py. Returns 0 clean,
    1 on a failing drift ASSERTION, 2 on a HARNESS error (a fixture could not be built or read)."""
    import contextlib
    import io
    import shutil
    import tempfile

    # Reuse the U4 engine's helpers to build a render-clean synthetic store (the U4 fixture idiom).
    import _opf_store  # noqa: E402
    import _opf_views  # noqa: E402

    def _classify(body):
        """Run a self-test `body` (a no-arg callable that returns its list of assertion failures and raises
        OSError for a HARNESS error) and map it to the documented status: 0 clean, 1 on a failing drift
        ASSERTION, 2 on a HARNESS error. This is the single place the assertion(1)-vs-harness(2) distinction
        the docstring promises is implemented."""
        try:
            failures = body()
        except OSError as exc:
            print("check_opf_drift self-test: harness error: {}".format(exc), file=sys.stderr)
            return EXIT_ERROR
        if failures:
            for f in failures:
                print("check_opf_drift self-test: FAIL: {}".format(f), file=sys.stderr)
            return EXIT_DRIFT
        return EXIT_OK

    def build_clean_store(root):
        """Create a RENDER-CLEAN empty-state store at `root` (the U4 empty-store shape) and populate its
        views through _opf_views.plan_views, so `opf render --check` is clean by construction. It is
        render-clean, not fully store-valid: validate_store would report it missing module registrations,
        indexes, counters, and CHANGELOG, which are out of scope for a render-drift fixture."""
        machine = Path(root) / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
        machine.mkdir(parents=True)
        types_block = "".join(
            '[types.{}]\nnamespace = "{}"\n'.format(name, ns)
            for name, ns in _opf_store.BASELINE_TYPES.items())
        views_block = "".join(
            _opf_views._view(name, kind, list(sources))
            for name, (kind, sources, _renderer) in _opf_views.NAMED_VIEWS.items())
        manifest = (
            "[devprocess]\n"
            'standard = "devprocess"\n'
            'spec_version = "' + _opf_store.SUPPORTED_SPEC_VERSION + '"\n'
            'layout = "inline"\n'
            'posture = "required"\n'
            'import_status = "none"\n'
            "\n"
            "[store]\n"
            'sync_target = ""\n'
            "\n"
            "[modules]\n"
            "governance = true\n"
            "operational_policy = true\n"
            "concurrent_operation = true\n"
            "\n"
            + types_block
            + "\n[vendors]\nregistered = []\n\n"
            + views_block)
        (machine / _opf_store.MANIFEST_NAME).write_text(manifest, encoding="utf-8")
        for name in _opf_store.BASELINE_TYPES:
            if name != "worklog":
                (machine / "{}.index.toml".format(name)).write_text("schema = 1\n", encoding="utf-8")
        (machine / "worklog.toml").write_text("schema = 1\n", encoding="utf-8")
        import _opf_release  # noqa: E402  coverage_digest for the one seeded release
        (machine / "version.toml").write_text(
            "schema = 1\n\n[[release]]\n"
            'version = "0.1.0"\n'
            'date = "2026-01-01T00:00:00Z"\n'
            "worklog_span = []\n"
            'coverage_digest = "{}"\n'.format(_opf_release.coverage_digest([])),
            encoding="utf-8")
        # Populate the views through the engine's own public planner (never hand-built): open the store
        # root fd, plan every declared view, and write each planned target at its spec destination.
        machine_rel = "{}/{}".format(_opf_store.WORKING_DIRNAME, _opf_store.DEFAULT_MACHINE_SUBDIR)
        fd = os.open(str(root), os.O_RDONLY)
        try:
            for _name, _scope, dest_rel, text in _opf_views.plan_views(fd, machine_rel):
                (Path(root) / dest_rel).write_text(text, encoding="utf-8")
        finally:
            os.close(fd)

    def _drift_suite():
        """Build synthetic stores and assert the 0/1/2 render contract end to end through opf.py. Returns
        the list of assertion failures; raises OSError if a fixture cannot be built or read (a harness
        error, which _classify maps to exit 2)."""
        failures = []

        def expect(label, got, want):
            if got != want:
                failures.append("{}: got {}, expected {}".format(label, got, want))

        base = Path(tempfile.mkdtemp(prefix="opf-drift-selftest-")).resolve()
        try:
            # Clean populated store -> 0.
            clean = base / "clean"
            clean.mkdir()
            build_clean_store(clean)
            expect("clean-store", _run_render_check(clean, capture=True), EXIT_OK)

            # Same store with one view edited -> 1 (drift).
            drifted = base / "drifted"
            drifted.mkdir()
            build_clean_store(drifted)
            todo = drifted / _opf_store.WORKING_DIRNAME / "TODO.md"
            todo.write_text(todo.read_text(encoding="utf-8") + "drifted line\n", encoding="utf-8")
            expect("drifted-store", _run_render_check(drifted, capture=True), EXIT_DRIFT)

            # Broken store: a discovered but unparseable manifest -> 2 (cannot-evaluate).
            broken = base / "broken"
            (broken / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR).mkdir(parents=True)
            (broken / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
             / _opf_store.MANIFEST_NAME).write_text("this is not valid toml {{{\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):   # cannot-evaluate leg emits a located
                expect("broken-store", _run_render_check(broken, capture=True), EXIT_ERROR)  # diagnostic

            # Non-adopter root (no .working/) -> 0 (NOT APPLICABLE).
            empty = base / "empty"
            empty.mkdir()
            expect("not-adopted-root", _run_render_check(empty, capture=True), EXIT_OK)

            # Child-LAUNCH failure -> exit 2 (cannot-evaluate), never a false drift 1 (FIX 1 regression,
            # subsuming the NB-4 child-crash residual). Inject an OSError at the launch call: an OS refusal
            # to fork (RLIMIT_NPROC pressure) surfaces as BlockingIOError/OSError from subprocess.run, which
            # must be caught and mapped to EXIT_ERROR, never propagate and exit the gate 1. Its diagnostic
            # goes to stderr, suppressed here so a passing leg stays quiet; subprocess.run is restored in a
            # finally so no later leg runs under the injected failure.
            real_run = subprocess.run
            def _refuse_launch(*_a, **_k):
                raise BlockingIOError("simulated fork refusal (RLIMIT_NPROC)")
            subprocess.run = _refuse_launch
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    expect("child-launch-failure", _run_render_check(clean, capture=True), EXIT_ERROR)
            finally:
                subprocess.run = real_run

            # Child exit 1 WITHOUT the drift marker -> cannot-evaluate (exit 2), NEVER a false drift 1 (the
            # r5 fix: the exit-1=drift conflation closed at the interpretation layer). A module-import crash
            # or any uncaught exception in the render child exits 1 with a traceback on STDERR and NO `drift:`
            # line on STDOUT; the exit-code-only reading would call that drift. Stub subprocess.run to return
            # exit 1 with marker-free stdout and assert the gate returns EXIT_ERROR, not EXIT_DRIFT. Restored
            # in a finally so no later leg runs under the stub; its diagnostic goes to stderr, suppressed.
            real_run_nm = subprocess.run
            def _exit1_no_marker(*_a, **_k):
                return subprocess.CompletedProcess(
                    args=[], returncode=EXIT_DRIFT,
                    stdout=b"Traceback (most recent call last):\n"
                           b"ImportError: simulated child module-import crash (no drift line)\n")
            subprocess.run = _exit1_no_marker
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    expect("child-exit1-no-marker", _run_render_check(clean, capture=True), EXIT_ERROR)
            finally:
                subprocess.run = real_run_nm

            # No repository root discoverable from the gate's own anchor -> exit 2 (cannot-evaluate),
            # never a silent fall-through to cwd that would return a false 0 (FIX 1 regression). Anchor a
            # synthetic tools/ dir with no .git at or above it inside our own tempdir; _run_gate must refuse
            # it. Its own diagnostic goes to stderr, which we suppress so a passing leg stays quiet.
            norepo_anchor = base / "norepo" / "tools"
            norepo_anchor.mkdir(parents=True)
            with contextlib.redirect_stderr(io.StringIO()):
                expect("no-repo-root", _run_gate(norepo_anchor), EXIT_ERROR)

            # Invalid/garbage .git marker at the gate's own dir -> exit 2 (cannot-evaluate), never a false
            # NOT-APPLICABLE 0 (FIX 1 regression). A bare existence test would accept this stray gitfile as
            # a repository root, anchor the gate to a dir with no .working/, and pass 0 while the real root
            # went unchecked; the git probe rejects it (git -C <dir> rev-parse --show-toplevel returns 128
            # on an invalid gitfile format). Its diagnostic goes to stderr, suppressed so a passing leg
            # stays quiet.
            badmarker_anchor = base / "badmarker" / "tools"
            badmarker_anchor.mkdir(parents=True)
            (badmarker_anchor / ".git").write_text("not a git marker\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                expect("invalid-git-marker", _run_gate(badmarker_anchor), EXIT_ERROR)

            # git emits a RELATIVE toplevel (e.g. `.`) -> _git_toplevel returns None -> exit 2
            # (cannot-evaluate), never a false NOT-APPLICABLE 0 (B1 regression). `git rev-parse
            # --show-toplevel` emits an ABSOLUTE path in the normal case; a relative value is malformed and
            # must fail closed. Without the isabs guard, Path(".").resolve() resolves against the CWD and can
            # pass the caller's containment check, yielding a false 0. Stub subprocess.run to emit a relative
            # toplevel and assert the probe returns None; restored in a finally so no later leg is affected.
            real_run_rel = subprocess.run
            def _relative_toplevel(*_a, **_k):
                return subprocess.CompletedProcess(args=[], returncode=EXIT_OK, stdout=".\n")
            subprocess.run = _relative_toplevel
            try:
                expect("relative-toplevel", _git_toplevel(norepo_anchor), None)
            finally:
                subprocess.run = real_run_rel

            # The git EXECUTABLE is absolutized (os.path.abspath) so a relative which() result (a relative
            # PATH entry) still launches an ABSOLUTE git, pinning the child against exec-time re-resolution
            # (mirrors _opf_observe._git_path). Stub shutil.which to a RELATIVE "git" and capture the command
            # _git_toplevel launches; assert its git argv[0] is absolute. Both stubs restored in a finally so
            # no later leg is affected.
            real_which_abs = shutil.which
            real_run_abs = subprocess.run
            launched = {}
            def _relative_which(*_a, **_k):
                return "git"
            def _capture_run(cmd, *_a, **_k):
                launched["cmd"] = cmd
                return subprocess.CompletedProcess(args=cmd, returncode=EXIT_OK, stdout="/toplevel\n")
            shutil.which = _relative_which
            subprocess.run = _capture_run
            try:
                _git_toplevel(norepo_anchor)
                expect("abspath-git-executable", os.path.isabs(launched.get("cmd", [""])[0]), True)
            finally:
                shutil.which = real_which_abs
                subprocess.run = real_run_abs
        finally:
            shutil.rmtree(base, ignore_errors=True)
        return failures

    # Guard the gate's OWN status contract BOTH ways, so the documented assertion(1)-vs-harness(2)
    # distinction is a check that reds if it regresses (change-carries-check), not merely prose. The probes
    # deliberately emit diagnostics, so their stderr is suppressed here.
    def _raise_harness_error():
        raise OSError("simulated harness error")

    contract = []
    with contextlib.redirect_stderr(io.StringIO()):
        if _classify(_raise_harness_error) != EXIT_ERROR:
            contract.append("harness-error body did not map to exit 2")
        if _classify(lambda: ["simulated failing assertion"]) != EXIT_DRIFT:
            contract.append("failing-assertion body did not map to exit 1")
        if _classify(lambda: []) != EXIT_OK:
            contract.append("clean body did not map to exit 0")
    if contract:
        for c in contract:
            print("check_opf_drift self-test: FAIL: status-contract: {}".format(c), file=sys.stderr)
        return EXIT_DRIFT

    rc = _classify(_drift_suite)
    if rc == EXIT_OK:
        print("check_opf_drift self-test: PASS (opf render --check returns 0 clean / 1 drift / 2 broken / "
              "0 NOT APPLICABLE end to end; drift recognized by the 'drift:' marker, not the bare exit code; "
              "child exit 1 without the drift marker -> 2 (cannot-evaluate); no-repo-root -> 2; "
              "invalid-git-marker -> 2; relative-toplevel probe -> None (exit 2); "
              "git executable absolutized (relative which() -> absolute argv[0]); "
              "status contract 1=assertion 2=harness)")
    return rc


# A per-git-call runtime bound (SECA resource-bounds): a hung or pathological git probe fails SAFE to a
# cannot-evaluate (exit 2) rather than blocking the gate indefinitely. Mirrors _opf_observe._GIT_TIMEOUT_S.
_GIT_TIMEOUT_S = 30


def _scrubbed_env():
    """Build the minimal, allowlist environment every git call runs under. Every ambient `GIT_`-prefixed
    variable is DROPPED (an inherited GIT_DIR/GIT_WORK_TREE/GIT_CONFIG/GIT_OBJECT_DIRECTORY could otherwise
    rebind the call to a DIFFERENT repository, inject configuration, or redirect object lookup); only PATH
    and HOME are carried over. The few variables git genuinely needs to run non-interactively and free of
    ambient configuration are then RE-APPLIED: global and system config neutralized to os.devnull, the
    system config search disabled, the terminal prompt disabled, optional locks turned off (read-only), and
    the locale pinned so output is deterministic."""
    env = {}
    for name in ("PATH", "HOME"):
        val = os.environ.get(name)
        if val is not None:
            env[name] = val
    # Re-apply only what git needs, config injection neutralized (allowlist stance).
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["LC_ALL"] = "C"
    return env


def _git_toplevel(anchor):
    """Confirm the repository IDENTITY of `anchor` (the gate's own tools dir) through a local git probe bound
    to that location, returning the resolved repository toplevel Path, or None if it cannot be established.
    The probe is `git --no-replace-objects -C <anchor> rev-parse --show-toplevel`, hardened to the same
    standard as the sibling _opf_observe git boundary: `--no-replace-objects` so a replacement ref cannot
    substitute the bytes a read returns, an ALLOWLIST-scrubbed environment (every GIT_-prefixed variable
    dropped, so an inherited GIT_DIR/GIT_WORK_TREE/etc. cannot bind the probe to a DIFFERENT repository; only
    PATH/HOME carried over, config-neutralizing vars re-applied), the git executable resolved via shutil.which
    and absolutized with os.path.abspath (a relative which() result anchored to the cwd, pinning the launched
    probe against exec-time re-resolution; the which()-time PATH-shadowing residual remains), and a bounded
    timeout. A missing git, a launch failure, a timeout, a nonzero return (an invalid/
    garbage `.git` gitfile yields git's own exit 128), or output that is empty or not an absolute path (git
    emits an absolute toplevel, so a relative value is malformed) all return None -- the caller maps that to a
    cannot-evaluate (exit 2), never a false pass."""
    import shutil
    git = shutil.which("git")
    git = os.path.abspath(git) if git else None
    if git is None:
        return None
    try:
        proc = subprocess.run(
            [git, "--no-replace-objects", "-C", str(anchor), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=_scrubbed_env(),
            text=True, timeout=_GIT_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    if not os.path.isabs(out):
        return None
    try:
        return Path(out).resolve()
    except OSError:
        return None


def _run_gate(anchor):
    """Establish the repository root by CONFIRMING the repository IDENTITY of the gate's OWN location
    `anchor` through a local git probe (git -C <anchor> rev-parse --show-toplevel, ambient Git env scrubbed,
    the git executable resolved via shutil.which and absolutized with os.path.abspath), then run the live
    render-drift check against it. A root established only by the
    PRESENCE of a `.git` entry is not enough: a stray or garbage `.git` file beside the gate (an invalid
    gitfile) satisfies a bare existence test yet is NOT a real repository root, so the gate would anchor to
    the wrong directory, find no `.working/`, and return a FALSE NOT-APPLICABLE 0 while the real drift at the
    true root went unchecked. So the root is CONFIRMED by the git probe; if the probe cannot establish it
    (git missing, a launch failure, a nonzero/garbage-gitfile result, or malformed output) or the resolved
    toplevel does not contain or parent the gate's own tools dir, that is a cannot-evaluate (exit 2), never a
    fall-through to the current directory and never a false NOT-APPLICABLE 0."""
    anchor = Path(anchor).resolve()
    root = _git_toplevel(anchor)
    if root is None or not (root == anchor or root in anchor.parents):
        print("check_opf_drift: cannot evaluate: could not confirm a real repository root for the gate's "
              "own location {} via a git probe (git -C ... rev-parse --show-toplevel); a stray or invalid "
              ".git marker is not a repository root, and the gate refuses to fall back to the current "
              "directory or return a false NOT-APPLICABLE".format(anchor), file=sys.stderr)
        return EXIT_ERROR
    return _run_render_check(root, capture=False)


def main(argv=None):
    # Final class-width backstop: any residual, unforeseen error path routes to a located cannot-evaluate
    # (exit 2), so no checked input yields a false-0 or an uncaught exit-1 escape. KeyboardInterrupt and
    # SystemExit are BaseException (not Exception) and stay uncaught, and the defined clean(0)/drift(1)/
    # cannot-evaluate(2) forwarding for cases that DID resolve is preserved. The located message keeps the
    # failure debuggable rather than silently masked.
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["--self-test"]:
            return _self_test()
        if args:
            print("check_opf_drift: unexpected argument(s): {}".format(" ".join(args)), file=sys.stderr)
            return EXIT_ERROR
        return _run_gate(Path(__file__).resolve().parent)
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false-0 or uncaught exit-1
        print("check_opf_drift: cannot evaluate: unexpected error in the render-drift gate ({!r}); failing "
              "closed to exit 2".format(exc), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
