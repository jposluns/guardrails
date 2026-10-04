#!/usr/bin/env python3
"""Behaviour selftest for the shared render/reconcile driver (tools/opf_render.py).

Locks the two behaviours OPF-1 B1 must preserve so a regression is caught, not shipped. Each probe is
built to FAIL if its fix is reverted, so the selftest is the durable guard the fix carries.

  1. Per-target ordering on a degraded input (MAJOR-1). With a mixed target set (a stale whole-file
     target followed by a block target whose page is missing or markerless), the driver must reconcile
     and report the EARLIER target before the later target's materialization failure aborts the run,
     exactly as the hand-written gen_roadmap.py did against origin/main:
       - --check mode: `drift: <file>` prints FIRST, then the block target's error line, exit 2.
       - write mode: the earlier file IS rewritten before the abort, then the error line, exit 2.
     A build-all-then-reconcile-all driver loses that drift line and that write; those probes catch it.
     A companion probe locks the render-first phase: a schema error on a LATER target must abort before
     an earlier target is reconciled, so no premature drift line leaks.

  2. Fail-closed target materialization (MAJOR-2). targets is materialized once (tuple), so a one-shot
     iterator is not exhausted by the render phase and then silently skipped by the reconcile phase
     (a fail-open returning a clean 0 while reconciling nothing), and an empty target set raises rather
     than passing clean.

Every fixture is synthetic and assembled in a tempdir; opf_render.repo_root is redirected to it so no
real file is read or written. Exit convention matches the repo's selftests: 0 pass, 1 fail, 2 error.
"""
import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import opf_render  # noqa: E402
from opf_render import run_generator, FileTarget, BlockTarget  # noqa: E402

SOURCE = "source.toml"
BEGIN = "<!-- ROADMAP:BEGIN (generated) -->"
END = "<!-- ROADMAP:END -->"


def _fresh(_data):
    return "FRESH\n"


def _raise_schema(_data):
    raise KeyError("stage")  # the roadmap schema-error class (a missing key)


def _run(root, argv, targets, schema_excs=(KeyError, TypeError)):
    """Run the driver against `root` with stdout captured; return (exit_code, stdout_text). repo_root is
    redirected to `root` for the call and always restored."""
    saved = opf_render.repo_root
    opf_render.repo_root = lambda: root
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = run_generator(argv, source=SOURCE, targets=targets,
                               regen_hint="run the generator to regenerate", schema_excs=schema_excs)
    finally:
        opf_render.repo_root = saved
    return rc, buf.getvalue()


def _selftest(root, failures):
    (root / SOURCE).write_text("probe = 1\n", encoding="utf-8")

    def stale_file():
        """A whole-file target whose on-disk copy (STALE) differs from what it renders (FRESH)."""
        (root / "ROADMAP.md").write_text("STALE\n", encoding="utf-8")
        return FileTarget("ROADMAP.md", _fresh)

    def page(with_markers):
        (root / "site").mkdir(exist_ok=True)
        body = (BEGIN + "\nOLD\n      " + END + "\n") if with_markers else "no markers here\n"
        (root / "site" / "page.html").write_text(body, encoding="utf-8")

    missing_err = "error: site/page.html not found (expected generated target)\n"
    marker_err = "error: markers for ROADMAP not found in the page\n"

    # (a) MAJOR-1, --check, missing page: the earlier stale file's drift line prints FIRST, then the
    #     block target's not-found error, exit 2. A build-all-first driver would drop the drift line.
    tgt = stale_file()
    if (root / "site" / "page.html").exists():
        (root / "site" / "page.html").unlink()
    rc, out = _run(root, ["--check"], (tgt, BlockTarget("site/page.html", "ROADMAP", _fresh)))
    if (rc, out) != (2, "drift: ROADMAP.md\n" + missing_err):
        failures.append("check/missing-page: expected exit 2 with 'drift: ROADMAP.md' before the "
                        "not-found error, got exit {!r} stdout {!r}".format(rc, out))
    if (root / "ROADMAP.md").read_text(encoding="utf-8") != "STALE\n":
        failures.append("check/missing-page: --check must not write the stale file")

    # (b) MAJOR-1, write mode, missing page: the earlier file IS rewritten (FRESH) before the abort;
    #     write mode prints no drift line (reconcile writes silently), then the not-found error, exit 2.
    #     A build-all-first driver would leave ROADMAP.md STALE (the lost write side-effect).
    tgt = stale_file()
    rc, out = _run(root, [], (tgt, BlockTarget("site/page.html", "ROADMAP", _fresh)))
    if (rc, out) != (2, missing_err):
        failures.append("write/missing-page: expected exit 2 and only the not-found error, got exit "
                        "{!r} stdout {!r}".format(rc, out))
    if (root / "ROADMAP.md").read_text(encoding="utf-8") != "FRESH\n":
        failures.append("write/missing-page: the earlier file must be written before the later target's "
                        "failure aborts the run (build-all-first would leave it STALE)")

    # (c) MAJOR-1, --check, markerless page: same ordering, with the markers-not-found message.
    tgt = stale_file()
    page(with_markers=False)
    rc, out = _run(root, ["--check"], (tgt, BlockTarget("site/page.html", "ROADMAP", _fresh)))
    if (rc, out) != (2, "drift: ROADMAP.md\n" + marker_err):
        failures.append("check/markerless: expected exit 2 with 'drift: ROADMAP.md' before the markers "
                        "error, got exit {!r} stdout {!r}".format(rc, out))

    # (d) Render-first phase: a schema error on a LATER target aborts before the EARLIER target is
    #     reconciled, so no premature drift line leaks. A naive per-target build-then-reconcile would
    #     print 'drift: ROADMAP.md' before hitting the schema error.
    tgt = stale_file()
    rc, out = _run(root, ["--check"], (tgt, FileTarget("VERSION", _raise_schema)))
    if rc != 2 or out != "error: {} is missing or misuses a key: 'stage'\n".format(SOURCE):
        failures.append("render-first: a later schema error must abort before the earlier target is "
                        "reconciled (no leaked drift line), got exit {!r} stdout {!r}".format(rc, out))

    # (e) Healthy both-drift ordering: two stale file targets both report drift in declaration order,
    #     then the regen hint, exit 1. Locks per-target drift-line order (the CHANGELOG-then-VERSION shape).
    (root / "A.md").write_text("STALE\n", encoding="utf-8")
    (root / "B.md").write_text("STALE\n", encoding="utf-8")
    rc, out = _run(root, ["--check"], (FileTarget("A.md", _fresh), FileTarget("B.md", _fresh)))
    if (rc, out) != (1, "drift: A.md\ndrift: B.md\nrun the generator to regenerate\n"):
        failures.append("both-drift order: expected 'drift: A.md' then 'drift: B.md' then the hint, exit "
                        "1, got exit {!r} stdout {!r}".format(rc, out))

    # (f) MAJOR-2, one-shot iterator: a stale target passed as a one-shot iterator must still be
    #     reconciled (drift reported, exit 1), NOT exhausted by the render phase and silently skipped.
    #     Without tuple() materialization this returns a fail-open exit 0 with no drift line.
    (root / "VERSION").write_text("STALE\n", encoding="utf-8")
    rc, out = _run(root, ["--check"], iter([FileTarget("VERSION", _fresh)]))
    if (rc, out) != (1, "drift: VERSION\nrun the generator to regenerate\n"):
        failures.append("one-shot iterator: a one-shot iterator target must be reconciled, not exhausted "
                        "and silently skipped, got exit {!r} stdout {!r}".format(rc, out))

    # (g) MAJOR-2, empty target set: fail closed (raise), never a clean 0 over nothing.
    try:
        _run(root, ["--check"], iter([]))
    except ValueError:
        pass
    else:
        failures.append("empty targets: an empty target set must raise (fail closed), not pass clean")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-opf-render-selftest-"))
    failures = []
    cleanup_error = None
    try:
        _selftest(tmp, failures)
    finally:
        try:
            shutil.rmtree(tmp)  # no ignore_errors: a selftest cleanup failure must surface, not hide
        except OSError as exc:
            cleanup_error = exc
    if cleanup_error is not None:
        print("SELF-TEST ERROR: could not remove the self-test tempdir ({}); fail-closed"
              .format(cleanup_error), file=sys.stderr)
        return 2
    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: the driver reconciles and reports each target in declaration order, so an "
          "earlier target's drift line prints and its file is written before a later target's "
          "materialization failure aborts the run (--check and write, missing-page and markerless); a "
          "schema error on a later target aborts before an earlier target is reconciled; a one-shot "
          "iterator target is reconciled rather than silently skipped; and an empty target set fails "
          "closed by raising")
    return 0


if __name__ == "__main__":
    sys.exit(main())
