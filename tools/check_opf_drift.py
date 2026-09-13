#!/usr/bin/env python3
"""OPF render-drift gate: `opf render --check` over the adopter store at the repository root.

A thin gate wrapper in the check_*.py family (deliberately NOT gen_*.py: an OPF render targets an adopter
--root with no fixed repo-relative target, so it gates as a self-test plus a live leg, exactly the posture
opf.py itself documents). The live leg runs `opf.py render --check --root <repo-root>` and forwards the
child's exit code UNMASKED (gate discipline: no truncating sink, no `|| true`, no status-masking trailer).
The repo root is DERIVED via _gen_common.repo_root(), never hardcoded (the derive-don't-hardcode rule).

This repository is not a DevProcess adopter, so the live leg prints render's own NOT APPLICABLE and exits 0,
spec-honest like the crosswalk/doctor legs in run_all_checks.sh; the day this repo adopts, the same leg
gates real view drift with no change. The exit contract is render's own and identical here: 0 clean, 1
drift, 2 cannot-evaluate; an unexpected child status is clamped to 2 (fail-closed).

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gen_common  # noqa: E402  repo_root(): derive the target, never hardcode it

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
    1 on a failing assertion, 2 on a harness error."""
    import shutil
    import tempfile

    # Reuse the U4 engine's helpers to build a VALID synthetic store (the U4 fixture idiom).
    import _opf_store  # noqa: E402
    import _opf_views  # noqa: E402

    failures = []

    def expect(label, got, want):
        if got != want:
            failures.append("{}: got {}, expected {}".format(label, got, want))

    def build_clean_store(root):
        """Create a VALID empty-state store at `root` (the U4 empty-store shape) and populate its views
        through _opf_views.plan_views, so `opf render --check` is clean by construction."""
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
    finally:
        shutil.rmtree(base, ignore_errors=True)

    if failures:
        for f in failures:
            print("check_opf_drift self-test: FAIL: {}".format(f), file=sys.stderr)
        return EXIT_ERROR
    print("check_opf_drift self-test: PASS (opf render --check returns 0 clean / 1 drift / 2 broken / "
          "0 NOT APPLICABLE end to end)")
    return EXIT_OK


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return _self_test()
    if args:
        print("check_opf_drift: unexpected argument(s): {}".format(" ".join(args)), file=sys.stderr)
        return EXIT_ERROR
    root = _gen_common.repo_root()
    return _run_render_check(root, capture=False)


if __name__ == "__main__":
    sys.exit(main())
