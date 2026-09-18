#!/usr/bin/env python3
"""OPF store-integrity gate: `opf doctor` over the adopter store at the repository root.

A thin gate wrapper in the check_*.py family (deliberately NOT gen_*.py: an OPF verb targets an adopter
--root with no fixed repo-relative target, so it gates as a self-test plus a live leg, exactly the posture
opf.py and check_opf_drift.py document). The live leg runs `opf.py doctor --root <repo-root>` and forwards
the child's exit code UNMASKED (doctor's exit code IS the verdict: _opf_check.exit_code over validate_store,
0 VALID / 1 INVALID / 2 CANNOT-EVALUATE), clamping any unexpected (non-0/1/2) status to a cannot-evaluate
(exit 2). It preserves gate discipline throughout (no truncating sink, no `|| true`, no status-masking
trailer).

The repo root is DERIVED by CONFIRMING the repository IDENTITY of the gate's OWN source location through a
local git probe (git -C <gate-dir> rev-parse --show-toplevel, with the ambient Git environment scrubbed and
the git executable resolved via shutil.which then absolutized with os.path.abspath, anchoring a relative
which() result to the cwd so the launched probe is pinned absolute against exec-time re-resolution), exactly
as check_opf_drift.py does, NOT by the mere PRESENCE of a `.git`
entry and NOT via _gen_common.repo_root() (which falls back to the current directory and would silently check
the WRONG root, a guard-input-soundness regression). A run whose root cannot be confirmed (git missing, a
launch failure, an invalid/garbage gitfile, or a toplevel that does not contain the gate) fails closed
(exit 2) rather than returning a false 0.

This repository is not an OPFiles adopter, so the live leg prints doctor's own NOT APPLICABLE and exits 0,
spec-honest like the render-drift/crosswalk/doctor legs in run_all_checks.sh; the day this repo adopts, the
same leg gates real store integrity with no change.

--self-test builds SYNTHETIC COMMITTED stores in a tempdir and asserts that 0/1/2/0 contract END TO END
through opf.py: 0 on a clean, validate_store-VALID committed store; 1 after a one-field working-tree mutation
of an immutable record body (a resurrection finding against the committed prior); 2 on a broken store (an
unparseable manifest); and 0 (NOT APPLICABLE) on a non-adopter root. Each committed fixture is `git init` +
`git add` + `git commit`ed so the `tracked` and `prior` observations _opf_observe.gather derives from HEAD are
real. The clean store is built through the OPF helpers' own canonical emitters and digesters (never
hand-built), the same construction _opf_check's own self-test proves VALID. Offline, stdlib only, fail-closed,
launched isolated (-I -B). The tempdir is removed in a finally (test-hermeticity). When git is not on PATH the
self-test SKIPs clean (a committed HEAD is required for the tracked/prior observations).
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for the self-test's sibling imports below

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_ERROR = 2


def _run_doctor(root, capture):
    """Run `opf.py doctor --root <root>` isolated (-I -B) and forward its exit code UNMASKED: doctor's own
    0/1/2 (0 VALID, 1 INVALID, 2 CANNOT-EVALUATE) IS the verdict. An unexpected non-0/1/2 status (a signal
    death surfacing as a negative return, or any abnormal code) is clamped to a cannot-evaluate (exit 2),
    never read as a verdict. A child-LAUNCH failure (an OS refusal such as BlockingIOError under RLIMIT_NPROC
    pressure) is caught here and mapped to exit 2, never propagated as the gate's own exit 1. In the live leg
    (capture False) the child's stdout and stderr are inherited so the operator sees doctor's report /
    NOT APPLICABLE; in the self-test leg (capture True) both are discarded so a passing leg stays quiet."""
    tools_dir = Path(__file__).resolve().parent
    stdout = subprocess.PIPE if capture else None
    stderr = subprocess.DEVNULL if capture else None
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(tools_dir / "opf.py"), "doctor", "--root", str(root)],
            stdout=stdout, stderr=stderr)
    except OSError as exc:
        print("check_opf_doctor: cannot evaluate: could not launch the doctor child for root {} ({}); no "
              "child ran, so no integrity verdict exists".format(root, exc), file=sys.stderr)
        return EXIT_ERROR
    rc = proc.returncode
    if rc in (EXIT_OK, EXIT_FINDING, EXIT_ERROR):
        return rc
    print("check_opf_doctor: cannot evaluate: the doctor child for root {} returned an unexpected status {} "
          "(a signal death or an abnormal exit); it is not a 0/1/2 integrity verdict".format(root, rc),
          file=sys.stderr)
    return EXIT_ERROR


def _self_test():
    """Build synthetic COMMITTED stores and assert the 0/1/2/0 doctor contract end to end through opf.py.
    Returns 0 clean, 1 on a failing ASSERTION, 2 on a HARNESS error (a fixture could not be built or read, or
    git is absent -> SKIP clean 0). git is required because the tracked/prior observations are derived from a
    committed HEAD; without a real commit the clean store cannot produce a VALID verdict."""
    import contextlib
    import io
    import shutil
    import tempfile

    import _opf_store     # noqa: E402  the store-tree / machine-store name constants
    import _opf_emit      # noqa: E402  the canonical TOML emitter (the fixture bodies are emitted, never hand-built)
    import _opf_release   # noqa: E402  span coverage digests for the version ledger
    import _opf_changelog  # noqa: E402  freeze digests for the changelog gates

    git = shutil.which("git")
    git = os.path.abspath(git) if git else None
    if git is None:
        print("check_opf_doctor self-test: SKIP (git not found on PATH; the committed-store fixtures the "
              "tracked/prior observations need cannot be built)")
        return EXIT_OK

    def _classify(body):
        """Map a self-test `body` (a no-arg callable returning its assertion-failure list, raising OSError on
        a HARNESS error) to the documented status: 0 clean, 1 on a failing assertion, 2 on a harness error."""
        try:
            failures = body()
        except OSError as exc:
            print("check_opf_doctor self-test: harness error: {}".format(exc), file=sys.stderr)
            return EXIT_ERROR
        if failures:
            for f in failures:
                print("check_opf_doctor self-test: FAIL: {}".format(f), file=sys.stderr)
            return EXIT_FINDING
        return EXIT_OK

    # --- the VALID-store fixture, built through the OPF helpers' own canonical emitters (the exact
    # construction _opf_check's own self-test proves VALID); never hand-built bytes. ---------------------
    TS = "2026-06-01T00:00:00Z"

    def wl(n):
        return {"id": "WL-{}".format(n), "date": "2026-06-{:02d}T00:00:00Z".format((n % 27) + 1),
                "actor": {"kind": "maintainer"}, "kind": "added", "summary": "w{}".format(n)}

    def envelope(rid, rtype, status, **over):
        r = {"id": rid, "type": rtype, "status": status, "title": "t",
             "created_at": TS, "updated_at": TS, "actor": {"kind": "maintainer"}}
        r.update(over)
        return r

    def bi(n, status):
        return envelope("BI-{}".format(n), "backlog_item", status)

    def dn(n, bi_id, title="t"):
        return envelope("DN-{}".format(n), "done", "recorded", title=title,
                        links=[{"rel": "receipt_of", "id": bi_id}])

    def ho(n):
        return envelope("HO-{}".format(n), "handoff", "current")

    def idx(records):
        return {"schema": 1, "record": records}

    def counters(**over):
        c = {"BI": 2, "DN": 1, "WL": 4, "FN": 0, "PD": 0, "AD": 0, "BL": 0, "HO": 1, "RF": 0,
             "CN": 0, "MD": 0, "PP": 0}
        c.update(over)
        return {"schema": 1, "counters": c}

    def base_manifest():
        types = {t: {"namespace": ns} for t, ns in (
            ("backlog_item", "BI"), ("done", "DN"), ("worklog", "WL"), ("finding", "FN"),
            ("pending_decision", "PD"), ("handoff", "HO"), ("reference", "RF"),
            ("autonomous_decision", "AD"), ("block", "BL"),
            ("contribution", "CN"), ("maintainer_decision", "MD"), ("preference_pattern", "PP"))}
        return {
            "opf": {"standard": "opf", "spec_version": _opf_store.SUPPORTED_SPEC_VERSION,
                           "layout": "inline",
                           "posture": "required", "import_status": "none"},
            "store": {"sync_target": ""},
            "types": types,
            "vendors": {"registered": []},
            "archive": {"period": "year"},
        }

    full_wl = {n: wl(n) for n in (1, 2, 3, 4)}
    dig12 = _opf_release.compute_span_digest(full_wl, (1, 2))
    dig34 = _opf_release.compute_span_digest(full_wl, (3, 4))

    def rel_row(v, a, b, dig):
        return {"version": v, "date": TS, "worklog_span": ["WL-{}".format(a), "WL-{}".format(b)],
                "coverage_digest": dig}

    def make_changelog(published_tokens):
        lines = ["# Changelog", "", "## unreleased", ""]
        for tok in published_tokens:
            lines += ["## {}".format(tok), "", "- {} notes".format(tok), ""]
        return "\n".join(lines) + "\n"

    def freeze_digests(changelog_text, tokens):
        entries, _f = _opf_changelog._changelog_entries(changelog_text)
        emap = {}
        for tok, text in entries:
            emap.setdefault(tok, []).append(text)
        return {tok: _opf_changelog.freeze_digest(emap[tok][0]) for tok in tokens}

    clean_changelog = make_changelog(["1.1.0", "1.0.0"])
    _digs = freeze_digests(clean_changelog, ["1.1.0", "1.0.0"])
    clean_version = {
        "schema": 1,
        "release": [rel_row("1.0.0", 1, 2, dig12), rel_row("1.1.0", 3, 4, dig34)],
        "summary": [{"covers": "1.0.0", "status": "published", "digest": _digs["1.0.0"]},
                    {"covers": "1.1.0", "status": "published", "digest": _digs["1.1.0"]},
                    {"covers": "unreleased", "status": "working"}]}

    def clean_machine(done_title="t"):
        return {
            "manifest.toml": base_manifest(),
            "counters.toml": counters(),
            "backlog_item.index.toml": idx([bi(1, "done"), bi(2, "open")]),
            "done.index.toml": idx([dn(1, "BI-1", title=done_title)]),
            "finding.index.toml": idx([]),
            "pending_decision.index.toml": idx([]),
            "handoff.index.toml": idx([ho(1)]),
            "reference.index.toml": idx([]),
            "autonomous_decision.index.toml": idx([]),
            "block.index.toml": idx([]),
            "contribution.index.toml": idx([]),
            "maintainer_decision.index.toml": idx([]),
            "preference_pattern.index.toml": idx([]),
            "worklog.toml": {"schema": 1, "entry": [wl(3), wl(4)]},
            "version.toml": clean_version,
            "archive/2026/archive.toml": {"schema": 1, "moved": [],
                                          "worklog_moved": [{"span": ["WL-1", "WL-2"],
                                                             "destination": "archive/2026/worklog.toml"}]},
            "archive/2026/worklog.toml": {"schema": 1, "entry": [wl(1), wl(2)]},
        }

    def _write_machine(root, machine):
        mdir = Path(root) / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
        for rel, doc in machine.items():
            p = mdir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(doc if isinstance(doc, str) else _opf_emit.emit(doc), encoding="utf-8")

    def _write_product(root):
        (Path(root) / "VERSION").write_text("1.1.0\n", encoding="utf-8")
        (Path(root) / "CHANGELOG.md").write_text(clean_changelog, encoding="utf-8")

    def _git_env(home):
        # The fixture git env: the module _scrubbed_env allowlist (drops every ambient GIT_ variable,
        # neutralizes global/system config, disables the prompt and optional locks, pins the locale), with
        # HOME overridden to the fixture home so a commit reads no host config or hooks. Identity is supplied
        # per-call via -c below. Mirrors _opf_observe's _setup_env over _scrubbed_env.
        env = _scrubbed_env()
        env["HOME"] = str(home)
        return env

    def _git(cwd, home, *args):
        # --no-replace-objects so a replacement ref cannot substitute the bytes a git data command reads;
        # a bounded timeout so a hung fixture call fails SAFE to a harness error (exit 2), never hangs.
        cmd = [git, "--no-replace-objects", "-C", str(cwd),
               "-c", "user.email=opf@example.invalid", "-c", "user.name=OPF Self Test",
               "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"] + list(args)
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=_git_env(home), timeout=_GIT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise OSError("git {} timed out after {}s in {}".format(" ".join(args), _GIT_TIMEOUT_S, cwd))
        if proc.returncode != 0:
            raise OSError("git {} failed in {} (rc {}): {}".format(
                " ".join(args), cwd, proc.returncode, (proc.stderr or b"").decode("utf-8", "replace").strip()))

    def _commit_store(root, home, machine):
        _write_machine(root, machine)
        _write_product(root)
        _git(root, home, "init")
        _git(root, home, "add", "-A")
        _git(root, home, "commit", "-m", "seed store")

    def _doctor_suite():
        """Build synthetic COMMITTED stores and assert the 0/1/2/0 doctor contract end to end through opf.py.
        Returns the list of assertion failures; raises OSError if a fixture cannot be built (a harness error,
        which _classify maps to exit 2)."""
        failures = []

        def expect(label, got, want):
            if got != want:
                failures.append("{}: got {}, expected {}".format(label, got, want))

        base = Path(tempfile.mkdtemp(prefix="opf-doctor-selftest-")).resolve()
        home = base / "home"
        home.mkdir()
        try:
            # Clean, committed, validate_store-VALID store -> 0.
            clean = base / "clean"
            clean.mkdir()
            _commit_store(clean, home, clean_machine())
            expect("clean-store", _run_doctor(clean, capture=True), EXIT_OK)

            # A one-field working-tree mutation of an IMMUTABLE done record body (title t -> tampered),
            # UNCOMMITTED, so the committed prior digest disagrees -> a C-HISTORY-RESURRECTION finding ->
            # INVALID -> 1. The prior observation (from HEAD) still carries the original body.
            mutated = base / "mutated"
            mutated.mkdir()
            _commit_store(mutated, home, clean_machine())
            _write_machine(mutated, {"done.index.toml": idx([dn(1, "BI-1", title="tampered")])})
            expect("mutated-store", _run_doctor(mutated, capture=True), EXIT_FINDING)

            # Broken store: a discovered but unparseable manifest -> 2 (cannot-evaluate). No commit needed:
            # resolution fails closed before any observation is gathered.
            broken = base / "broken"
            (broken / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR).mkdir(parents=True)
            (broken / _opf_store.WORKING_DIRNAME / _opf_store.DEFAULT_MACHINE_SUBDIR
             / _opf_store.MANIFEST_NAME).write_text("this is not valid toml {{{\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                expect("broken-store", _run_doctor(broken, capture=True), EXIT_ERROR)

            # Non-adopter root (no .working/) -> 0 (NOT APPLICABLE).
            empty = base / "empty"
            empty.mkdir()
            expect("not-adopted-root", _run_doctor(empty, capture=True), EXIT_OK)

            # Child-LAUNCH failure -> exit 2 (cannot-evaluate), never a false verdict. Inject an OSError at the
            # launch call (an OS refusal to fork under RLIMIT_NPROC pressure surfaces as BlockingIOError);
            # subprocess.run is restored in a finally so no later leg runs under the injection.
            real_run = subprocess.run
            def _refuse_launch(*_a, **_k):
                raise BlockingIOError("simulated fork refusal (RLIMIT_NPROC)")
            subprocess.run = _refuse_launch
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    expect("child-launch-failure", _run_doctor(clean, capture=True), EXIT_ERROR)
            finally:
                subprocess.run = real_run

            # No repository root discoverable from the gate's own anchor -> exit 2, never a silent cwd
            # fall-through returning a false 0. Anchor a synthetic tools/ dir with no .git at or above it.
            norepo_anchor = base / "norepo" / "tools"
            norepo_anchor.mkdir(parents=True)
            with contextlib.redirect_stderr(io.StringIO()):
                expect("no-repo-root", _run_gate(norepo_anchor), EXIT_ERROR)

            # Invalid/garbage .git marker at the gate's own dir -> exit 2, never a false NOT-APPLICABLE 0: a
            # bare existence test would accept the stray gitfile as a root; the git probe rejects it (git -C
            # <dir> rev-parse --show-toplevel returns 128 on an invalid gitfile).
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
            shutil.rmtree(str(base), ignore_errors=True)
        return failures

    # Guard the gate's OWN status contract both ways, so the documented assertion(1)-vs-harness(2) distinction
    # is a check that reds if it regresses (change-carries-check), not merely prose.
    def _raise_harness_error():
        raise OSError("simulated harness error")

    contract = []
    with contextlib.redirect_stderr(io.StringIO()):
        if _classify(_raise_harness_error) != EXIT_ERROR:
            contract.append("harness-error body did not map to exit 2")
        if _classify(lambda: ["simulated failing assertion"]) != EXIT_FINDING:
            contract.append("failing-assertion body did not map to exit 1")
        if _classify(lambda: []) != EXIT_OK:
            contract.append("clean body did not map to exit 0")
    if contract:
        for c in contract:
            print("check_opf_doctor self-test: FAIL: status-contract: {}".format(c), file=sys.stderr)
        return EXIT_FINDING

    rc = _classify(_doctor_suite)
    if rc == EXIT_OK:
        print("check_opf_doctor self-test: PASS (opf doctor returns 0 on a clean committed store / 1 after an "
              "immutable-body mutation vs the committed prior / 2 on a broken store / 0 NOT APPLICABLE, end to "
              "end; child-launch failure -> 2; no-repo-root -> 2; invalid-git-marker -> 2; "
              "relative-toplevel probe -> None (exit 2); "
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
    timeout. A missing git, a launch failure, a timeout, a nonzero return (an invalid/garbage `.git`
    gitfile yields git's own exit 128), or output that is empty or not an absolute path (git emits an absolute
    toplevel, so a relative value is malformed) all return None -- the caller maps that to a cannot-evaluate
    (exit 2), never a false pass. Mirrors check_opf_drift._git_toplevel (the sibling gate),
    never _gen_common.repo_root() (which falls back to cwd)."""
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
    `anchor` through a local git probe (ambient Git env scrubbed, the git executable resolved via shutil.which
    and absolutized with os.path.abspath), then run the live
    doctor check against it. A root established only by the PRESENCE of a `.git` entry is not enough: a stray
    or garbage `.git` file beside the gate satisfies a bare existence test yet is NOT a real repository root,
    so the gate would anchor to the wrong directory, find no `.working/`, and return a FALSE NOT-APPLICABLE 0.
    So the root is CONFIRMED by the git probe; if it cannot be established (git missing, a launch failure, a
    nonzero/garbage-gitfile result, or malformed output) or the resolved toplevel does not contain or parent
    the gate's own tools dir, that is a cannot-evaluate (exit 2), never a fall-through to the current directory
    and never a false NOT-APPLICABLE 0."""
    anchor = Path(anchor).resolve()
    root = _git_toplevel(anchor)
    if root is None or not (root == anchor or root in anchor.parents):
        print("check_opf_doctor: cannot evaluate: could not confirm a real repository root for the gate's "
              "own location {} via a git probe (git -C ... rev-parse --show-toplevel); a stray or invalid "
              ".git marker is not a repository root, and the gate refuses to fall back to the current "
              "directory or return a false NOT-APPLICABLE".format(anchor), file=sys.stderr)
        return EXIT_ERROR
    return _run_doctor(root, capture=False)


def main(argv=None):
    # Final class-width backstop: any residual, unforeseen error path routes to a located cannot-evaluate
    # (exit 2), so no checked input yields a false-0 or an uncaught exit-1 escape. KeyboardInterrupt and
    # SystemExit are BaseException (not Exception) and stay uncaught; the defined 0/1/2 forwarding for cases
    # that DID resolve is preserved.
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["--self-test"]:
            return _self_test()
        if args:
            print("check_opf_doctor: unexpected argument(s): {}".format(" ".join(args)), file=sys.stderr)
            return EXIT_ERROR
        return _run_gate(Path(__file__).resolve().parent)
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false-0 or uncaught exit-1
        print("check_opf_doctor: cannot evaluate: unexpected error in the store-integrity gate ({!r}); "
              "failing closed to exit 2".format(exc), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
