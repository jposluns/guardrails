#!/usr/bin/env python3
"""OPF render-drift gate: `opf render --check` over the adopter store at the repository root.

A thin gate wrapper in the check_*.py family (deliberately NOT gen_*.py: an OPF render targets an adopter
--root with no fixed repo-relative target, so it gates as a self-test plus a live leg, exactly the posture
opf.py itself documents). The live leg runs `opf.py render --check --root <repo-root>` and forwards the
child's exit code UNMASKED (gate discipline: no truncating sink, no `|| true`, no status-masking trailer).
The repo root is DERIVED by walking up from the gate's OWN source location and REQUIRING a real repo
marker (.git) to be found; unlike _gen_common.repo_root() it never falls back to the current directory, so
a run from an unrelated directory with no .git fails closed (exit 2) rather than silently checking the
WRONG root and returning a false 0 (guard-input-soundness).

This repository is not a DevProcess adopter, so the live leg prints render's own NOT APPLICABLE and exits 0,
spec-honest like the crosswalk/doctor legs in run_all_checks.sh; the day this repo adopts, the same leg
gates real view drift with no change. The exit contract is render's own and identical here: 0 clean, 1
drift, 2 cannot-evaluate; an unexpected child status is clamped to 2 (fail-closed). Residual
(disclose-guard-residuals): the live leg reads a child exit 1 as drift, so a hypothetical uncaught crash in
the render child (Python exit 1) would be reported as drift rather than a cannot-evaluate. This is disclosed
rather than parsed out, to keep the child's exit UNMASKED and its output streamed to the console unaltered;
render's own internal failures already fail closed to exit 2 upstream of this leg.

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


def _run_render_check(root, capture):
    """Run `opf.py render --check --root <root>` isolated (-I -B) and return its exit code, unmasked.
    An unexpected (non 0/1/2) child status is clamped to EXIT_ERROR, never read as clean."""
    tools_dir = Path(__file__).resolve().parent
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if capture else {}
    proc = subprocess.run(
        [sys.executable, "-I", "-B", str(tools_dir / "opf.py"),
         "render", "--check", "--root", str(root)],
        **kwargs)
    rc = proc.returncode
    return rc if rc in (EXIT_OK, EXIT_DRIFT, EXIT_ERROR) else EXIT_ERROR


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
            'spec_version = "1.0.0"\n'
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
            expect("broken-store", _run_render_check(broken, capture=True), EXIT_ERROR)

            # Non-adopter root (no .working/) -> 0 (NOT APPLICABLE).
            empty = base / "empty"
            empty.mkdir()
            expect("not-adopted-root", _run_render_check(empty, capture=True), EXIT_OK)

            # No repository root discoverable from the gate's own anchor -> exit 2 (cannot-evaluate),
            # never a silent fall-through to cwd that would return a false 0 (FIX 1 regression). Anchor a
            # synthetic tools/ dir with no .git at or above it inside our own tempdir; _run_gate must refuse
            # it. Its own diagnostic goes to stderr, which we suppress so a passing leg stays quiet.
            norepo_anchor = base / "norepo" / "tools"
            norepo_anchor.mkdir(parents=True)
            with contextlib.redirect_stderr(io.StringIO()):
                expect("no-repo-root", _run_gate(norepo_anchor), EXIT_ERROR)
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
              "0 NOT APPLICABLE end to end; no-repo-root -> 2; status contract 1=assertion 2=harness)")
    return rc


def _run_gate(anchor):
    """Establish the repository root by walking up from the gate's OWN source location `anchor`, REQUIRING a
    real repo marker (.git) to be found, then run the live render-drift check against it. Unlike
    _gen_common.repo_root() (which falls back to Path.cwd() when no .git ancestor exists, so a run from an
    unrelated directory would silently check the WRONG root and return a false 0), a root that cannot be
    established here is a cannot-evaluate (exit 2), never a fall-through to the current directory."""
    anchor = Path(anchor).resolve()
    root = None
    for cand in [anchor, *anchor.parents]:
        if (cand / ".git").exists():
            root = cand
            break
    if root is None:
        print("check_opf_drift: cannot evaluate: no repository root (.git) found at or above the gate's "
              "own location {}; refusing to fall back to the current directory".format(anchor),
              file=sys.stderr)
        return EXIT_ERROR
    return _run_render_check(root, capture=False)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return _self_test()
    if args:
        print("check_opf_drift: unexpected argument(s): {}".format(" ".join(args)), file=sys.stderr)
        return EXIT_ERROR
    return _run_gate(Path(__file__).resolve().parent)


if __name__ == "__main__":
    sys.exit(main())
