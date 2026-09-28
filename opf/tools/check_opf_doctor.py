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
unparseable manifest); and 0 (NOT APPLICABLE) on a non-adopter root. The enforcement-pack CI floor rides the
same fixtures: `doctor --require-store` keeps 0 on the clean store and turns the non-adopter root into 2, and
the shipped recipe opf/enforcement/ci/opf-ci.sh (doctor --require-store, then render --check) exits 0 on the
clean store and 2 on the non-adopter root. Recipe-CONTRACT vectors hold the recipe itself durable:
a RECORDING STUB tool asserts the exact invocation order and arguments (doctor --require-store, then render
--check, each over the given root, nothing else), per-step exit propagation (a doctor 1/2 stops the recipe
before render; a render 1/2 is the recipe's exit), and abnormal-status normalization for BOTH steps (a child
status of 3, 126 or 137, a genuine signal death, and a non-executable or missing OPF_PYTHON each become the
recipe's cannot-evaluate 2, never a forwarded out-of-vocabulary status, a doctor abnormality stopping before
render); the usage guard (a surplus operand) exits 2 with NO step launched; the recipe run by a RELATIVE
path under a hostile CDPATH naming a decoy pack still resolves its own directory and returns the true
verdict; a committed clean store with ONE declared, planner-populated view red-flags end to end once the
view is edited (render --check 1, doctor 1, recipe 1) while both recipe runs leave every file and directory
outside .git/ identical and the ref set unchanged (the read-only claim, held as a check); and the GitHub
Actions template is held to its own stated discipline (no non-comment line carries continue-on-error or a
status-masking `|| true`). The stub exists because the real tool cannot isolate the render step: a
drifted view fails doctor's own C-VIEW-DRIFT too, so only the stub proves the render invocation is still
present, ordered, exactly argued, and forwarded. Each committed fixture is `git init` +
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


def _run_doctor(root, capture, extra=()):
    """Run `opf.py doctor --root <root>` isolated (-I -B) and forward its exit code UNMASKED: doctor's own
    0/1/2 (0 VALID, 1 INVALID, 2 CANNOT-EVALUATE) IS the verdict. An unexpected non-0/1/2 status (a signal
    death surfacing as a negative return, or any abnormal code) is clamped to a cannot-evaluate (exit 2),
    never read as a verdict. A child-LAUNCH failure (an OS refusal such as BlockingIOError under RLIMIT_NPROC
    pressure) is caught here and mapped to exit 2, never propagated as the gate's own exit 1. In the live leg
    (capture False) the child's stdout and stderr are inherited so the operator sees doctor's report /
    NOT APPLICABLE; in the self-test leg (capture True) both are discarded so a passing leg stays quiet.
    `extra` appends doctor flags after the root (the self-test's CI-floor `--require-store` vectors)."""
    tools_dir = Path(__file__).resolve().parent
    stdout = subprocess.PIPE if capture else None
    stderr = subprocess.DEVNULL if capture else None
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", str(tools_dir / "opf.py"), "doctor", "--root", str(root), *extra],
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
    """Isolate fixture configuration and restore the caller even on failure."""
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-selftest-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return _self_test_isolated()


def _self_test_isolated():
    """Build synthetic COMMITTED stores and assert the 0/1/2/0 doctor contract end to end through opf.py.
    Returns 0 clean, 1 on a failing ASSERTION, 2 on a HARNESS error (a fixture could not be built or read, or
    git is absent -> SKIP clean 0). git is required because the tracked/prior observations are derived from a
    committed HEAD; without a real commit the clean store cannot produce a VALID verdict."""
    import contextlib
    import hashlib
    import io
    import shutil
    import tempfile

    import _opf_store     # noqa: E402  the store-tree / machine-store name constants
    import _opf_emit      # noqa: E402  the canonical TOML emitter (the fixture bodies are emitted, never hand-built)
    import _opf_release   # noqa: E402  span coverage digests for the version ledger
    import _opf_changelog  # noqa: E402  freeze digests for the changelog gates
    import _opf_views     # noqa: E402  the U4 view planner (the drifted-view fixture populates through it)

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

    def _run_ci_recipe(root, tool=None, extra_env=None, extra_args=(), cwd=None, recipe=None):
        """Run the shipped CI recipe (opf/enforcement/ci/opf-ci.sh) over `root` with this interpreter as
        OPF_PYTHON, returning its exit status unmasked. By default OPF_TOOL is popped so the recipe
        exercises its default pack-relative opf.py path; `tool` sets OPF_TOOL instead (the recipe-contract
        vectors point it at the recording stub), and `extra_env` adds entries (the stub's log path and
        per-verb exit codes, an OPF_PYTHON override for the launch-failure vectors, or a hostile CDPATH).
        `extra_args` appends operands after the root (the usage-guard vector), `cwd` sets the child's
        working directory, and `recipe` substitutes a recipe path passed VERBATIM (the CDPATH vector runs
        a COPY by a RELATIVE path; the default stays the shipped recipe, absolute). A missing `sh`, a
        missing shipped recipe, or a launch failure raises OSError (a harness error, exit 2 via
        _classify)."""
        sh = shutil.which("sh")
        if sh is None:
            raise OSError("sh not found on PATH; the CI recipe cannot be run")
        shipped = Path(__file__).resolve().parent.parent / "enforcement" / "ci" / "opf-ci.sh"
        if not shipped.is_file():
            raise OSError("the CI recipe {} is missing".format(shipped))
        env = dict(os.environ, OPF_PYTHON=sys.executable)
        env.pop("OPF_TOOL", None)
        if tool is not None:
            env["OPF_TOOL"] = str(tool)
        if extra_env:
            env.update(extra_env)
        argv = [os.path.abspath(sh), str(shipped) if recipe is None else str(recipe), str(root)]
        argv += [str(a) for a in extra_args]
        proc = subprocess.run(argv, env=env, cwd=None if cwd is None else str(cwd),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return proc.returncode

    def _run_render_check(root):
        """Run `opf.py render --check --root <root>` isolated (-I -B) with output discarded, returning its
        exit status unmasked: the recipe's second step in isolation, so the drifted-view vector witnesses
        the render verdict itself, not only the recipe's composition of it. A launch failure raises OSError
        (a harness error, exit 2 via _classify)."""
        tools_dir = Path(__file__).resolve().parent
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-B", str(tools_dir / "opf.py"), "render", "--check",
                 "--root", str(root)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise OSError("could not launch the render child for root {} ({})".format(root, exc))
        return proc.returncode

    def _tree_digest(root, home):
        """A snapshot asserting the recipe's read-only claim, three-legged: every file under `root`
        outside .git/ (relpath -> sha256), the SORTED DIRECTORY LISTING outside .git/ (a created
        directory, even an empty one, is a write the file digests alone cannot see), and the full
        `git for-each-ref` output (a written ref, e.g. a planted tag, is a repository write even though
        its bytes live under .git/). .git/ file CONTENTS are excluded because the doctor step's
        observation gather runs read-only git commands whose internal bookkeeping is not a store write;
        the ref leg covers the .git/ writes that ARE store-visible. A failing for-each-ref probe raises
        OSError (a harness error, exit 2 via _classify)."""
        digests = {}
        dirs = []
        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = [d for d in dirnames if d != ".git"]
            dirs.append(str(Path(dirpath).relative_to(root)))
            for fname in filenames:
                p = Path(dirpath) / fname
                digests[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        try:
            proc = subprocess.run([git, "--no-replace-objects", "-C", str(root), "for-each-ref"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=_git_env(home), timeout=_GIT_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OSError("git for-each-ref could not run in {} ({})".format(root, exc))
        if proc.returncode != 0:
            raise OSError("git for-each-ref failed in {} (rc {})".format(root, proc.returncode))
        return digests, sorted(dirs), proc.stdout

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

            # The CI floor (enforcement pack, U16). --require-store leaves the clean store's verdict at 0 (an
            # over-broad flag that refused every store fails this) and turns the non-adopter root into 2
            # (reverting the flag's NOT-ADOPTED branch returns 0 there, failing it).
            expect("clean-store-require-store", _run_doctor(clean, capture=True, extra=("--require-store",)),
                   EXIT_OK)
            expect("not-adopted-root-require-store",
                   _run_doctor(empty, capture=True, extra=("--require-store",)), EXIT_ERROR)
            # The shipped portable recipe over the same roots, through its default pack-relative opf.py path:
            # 0 on the clean store (doctor 0, then render --check 0) and 2 on the non-adopter root (the
            # recipe's doctor step carries --require-store; dropping it lets the recipe reach render --check,
            # which reports NOT APPLICABLE and exits 0, failing this case).
            expect("ci-recipe-clean-store", _run_ci_recipe(clean), EXIT_OK)
            expect("ci-recipe-not-adopted-root", _run_ci_recipe(empty), EXIT_ERROR)

            # --- The recipe CONTRACT vectors (U16). A RECORDING STUB stands in for opf.py: it
            # appends its argv (after the script path) to OPF_STUB_LOG, one unit-separator-joined line per
            # invocation, and exits with the per-verb status OPF_STUB_RC_<VERB> (default 0); a `sig:<N>`
            # value makes it die by that signal instead (a GENUINE signal death, not an exit). The stub is
            # what makes these assertions possible: with the REAL tool, doctor and render agree over every
            # store this suite can build (a drifted view fails doctor's own C-VIEW-DRIFT too), so no real
            # fixture can red a recipe whose render step was deleted, reordered, re-flagged (--write), or
            # masked by a trailing `exit 0`; the stub isolates each step and reds all four mutations. -----
            stub = base / "stub_opf.py"
            stub.write_text(
                "import os, sys\n"
                "with open(os.environ['OPF_STUB_LOG'], 'a', encoding='utf-8') as log:\n"
                "    log.write(chr(31).join(sys.argv[1:]) + chr(10))\n"
                "verb = sys.argv[1].upper() if len(sys.argv) > 1 else 'NONE'\n"
                "rc = os.environ.get('OPF_STUB_RC_' + verb, '0')\n"
                "if rc.startswith('sig:'):\n"
                "    os.kill(os.getpid(), int(rc[4:]))\n"
                "sys.exit(int(rc))\n",
                encoding="utf-8")
            stub_serial = [0]

            def _run_recipe_stubbed(rc_doctor=0, rc_render=0):
                """Run the recipe over the clean store with the stub as OPF_TOOL; returns (exit status,
                the recorded invocations as argv lists)."""
                stub_serial[0] += 1
                log = base / "stub-log-{}".format(stub_serial[0])
                rc = _run_ci_recipe(clean, tool=stub, extra_env=dict(
                    OPF_STUB_LOG=str(log), OPF_STUB_RC_DOCTOR=str(rc_doctor),
                    OPF_STUB_RC_RENDER=str(rc_render)))
                calls = []
                if log.is_file():
                    calls = [line.split(chr(31)) for line in
                             log.read_text(encoding="utf-8").splitlines()]
                return rc, calls

            # Exact invocation order and arguments: doctor --require-store first, render --check second,
            # each over the given root, and NOTHING else. Deleting the render line, swapping the order,
            # substituting `render --write`, or changing any flag reds this vector.
            expect("ci-recipe-order-and-args", _run_recipe_stubbed(), (EXIT_OK, [
                ["doctor", "--require-store", "--root", str(clean)],
                ["render", "--check", "--root", str(clean)]]))
            # Render-failure propagation: doctor 0 + render 1/2 must exit 1/2 (a deleted render line or a
            # trailing status-masking `exit 0` returns 0 here and reds).
            rc, calls = _run_recipe_stubbed(rc_render=1)
            expect("ci-recipe-render-finding-propagates",
                   (rc, [c[0] for c in calls]), (EXIT_FINDING, ["doctor", "render"]))
            rc, calls = _run_recipe_stubbed(rc_render=2)
            expect("ci-recipe-render-error-propagates",
                   (rc, [c[0] for c in calls]), (EXIT_ERROR, ["doctor", "render"]))
            # Doctor-failure propagation: doctor 1/2 is the recipe's exit and render is NEVER reached
            # (stop at the first failure; a render run over a store doctor just red-flagged proves
            # nothing and could mask the doctor verdict).
            rc, calls = _run_recipe_stubbed(rc_doctor=1)
            expect("ci-recipe-doctor-finding-stops",
                   (rc, [c[0] for c in calls]), (EXIT_FINDING, ["doctor"]))
            rc, calls = _run_recipe_stubbed(rc_doctor=2)
            expect("ci-recipe-doctor-error-stops",
                   (rc, [c[0] for c in calls]), (EXIT_ERROR, ["doctor"]))
            # Launch-failure normalization: a step that cannot launch at all (a missing interpreter, the
            # shell's own 127) is normalized to the recipe's cannot-evaluate 2, never surfaced as an
            # out-of-vocabulary status the caller could misread.
            expect("ci-recipe-missing-interpreter",
                   _run_ci_recipe(clean, extra_env=dict(OPF_PYTHON=str(base / "no-such-python"))),
                   EXIT_ERROR)
            # A NON-EXECUTABLE interpreter (the shell's own 126) is the launch failure's sibling and
            # must normalize the same way; a passthrough that special-cases only 127 forwards it.
            nonexec = base / "nonexec-python"
            nonexec.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")  # never made executable: 126 by construction
            expect("ci-recipe-nonexec-interpreter",
                   _run_ci_recipe(clean, extra_env=dict(OPF_PYTHON=str(nonexec))), EXIT_ERROR)
            # Abnormal-status normalization for BOTH steps: any child status outside the 0/1/2 verdict
            # vocabulary (a tool bug's 3, a 126/137 launch or kill status leaking through, a genuine
            # signal death) must surface as the recipe's cannot-evaluate 2, never forwarded as if it
            # were a verdict and never masked to 0. A doctor abnormality stops before render (the same
            # short-circuit the 1/2 vectors assert); a render abnormality follows both steps.
            for bad in ("3", "126", "137", "sig:15"):
                rc, calls = _run_recipe_stubbed(rc_doctor=bad)
                expect("ci-recipe-doctor-abnormal-{}".format(bad),
                       (rc, [c[0] for c in calls]), (EXIT_ERROR, ["doctor"]))
                rc, calls = _run_recipe_stubbed(rc_render=bad)
                expect("ci-recipe-render-abnormal-{}".format(bad),
                       (rc, [c[0] for c in calls]), (EXIT_ERROR, ["doctor", "render"]))
            # The usage guard: a surplus operand is a usage error (exit 2) and NO step runs (the stub
            # log is never created), so a deleted guard cannot silently check the first operand and
            # ignore the rest.
            usage_log = base / "stub-log-usage"
            rc = _run_ci_recipe(clean, tool=stub, extra_env=dict(OPF_STUB_LOG=str(usage_log)),
                                extra_args=("surplus",))
            expect("ci-recipe-usage-surplus-operand",
                   (rc, usage_log.exists()), (EXIT_ERROR, False))
            # The CDPATH regression vector: the recipe resolves its own directory with `CDPATH= cd`; a
            # plain `cd` under a hostile CDPATH PRINTS the resolved directory (corrupting `here` with a
            # second output line) and can resolve into the CDPATH entry instead of the script's real
            # directory, breaking the default pack-relative OPF_TOOL path. Run a COPY of the recipe by
            # a RELATIVE path from the copy's root, with CDPATH naming a decoy that also holds
            # opf/enforcement/ci and with the stub at the copy's default opf/tools/opf.py; the fixed
            # recipe resolves its true directory and returns the true verdict (0, both steps recorded),
            # while a reverted plain `cd` resolves into the decoy and reds this vector.
            cdroot = base / "cdpath-copy"
            (cdroot / "opf" / "enforcement" / "ci").mkdir(parents=True)
            shutil.copyfile(
                str(Path(__file__).resolve().parent.parent / "enforcement" / "ci" / "opf-ci.sh"),
                str(cdroot / "opf" / "enforcement" / "ci" / "opf-ci.sh"))
            (cdroot / "opf" / "tools").mkdir(parents=True)
            shutil.copyfile(str(stub), str(cdroot / "opf" / "tools" / "opf.py"))
            decoy = base / "cdpath-decoy"
            (decoy / "opf" / "enforcement" / "ci").mkdir(parents=True)
            cd_log = base / "stub-log-cdpath"
            rc = _run_ci_recipe(clean, extra_env=dict(OPF_STUB_LOG=str(cd_log), CDPATH=str(decoy)),
                                cwd=cdroot, recipe="opf/enforcement/ci/opf-ci.sh")
            cd_calls = []
            if cd_log.is_file():
                cd_calls = [line.split(chr(31)) for line in
                            cd_log.read_text(encoding="utf-8").splitlines()]
            expect("ci-recipe-relative-path-hostile-cdpath",
                   (rc, [c[0] for c in cd_calls]), (EXIT_OK, ["doctor", "render"]))
            # The GitHub Actions template is held to its own stated discipline (a template nothing runs
            # in this repository would otherwise drift as prose): no NON-COMMENT line may carry
            # continue-on-error, and none may mask a step's status with a `|| true` trailer. Comment
            # lines are excluded because the template's own comment names the forbidden key.
            workflow = (Path(__file__).resolve().parent.parent / "enforcement" / "ci"
                        / "github-actions.yml")
            wf_lines = [" ".join(ln.split()) for ln in
                        workflow.read_text(encoding="utf-8").splitlines()
                        if not ln.lstrip().startswith("#")]
            expect("workflow-no-continue-on-error",
                   [ln for ln in wf_lines if "continue-on-error" in ln], [])
            expect("workflow-no-status-masking-trailer",
                   [ln for ln in wf_lines if "|| true" in ln or "||true" in ln], [])

            # --- The recipe end to end over a REAL drifted view (U16): a committed clean store
            # with ONE declared view, populated through the U4 engine's own public planner
            # (_opf_views.plan_views, the check_opf_drift fixture idiom; never hand-built), then edited and
            # re-committed. First witness the fixture is genuinely clean through BOTH recipe steps (0),
            # then witness the drift red-flags end to end: render --check itself exits 1, doctor exits 1
            # (C-VIEW-DRIFT is part of validate_store), and the recipe forwards the finding (1). Both
            # recipe runs leave the files, directories and refs unchanged: the read-only claim, held as
            # a check. ------------------------------------------------------------------------------
            viewed = base / "viewed"
            viewed.mkdir()
            machine = clean_machine()
            machine["manifest.toml"]["views"] = {"WORKLOG.md": {
                "kind": "deterministic", "sources": ["worklog"],
                "target": "{}/{}".format(_opf_store.WORKING_DIRNAME, "WORKLOG.md")}}
            _write_machine(viewed, machine)
            _write_product(viewed)
            machine_rel = "{}/{}".format(_opf_store.WORKING_DIRNAME, _opf_store.DEFAULT_MACHINE_SUBDIR)
            fd = os.open(str(viewed), os.O_RDONLY)
            try:
                for _name, _scope, dest_rel, text in _opf_views.plan_views(fd, machine_rel):
                    (viewed / dest_rel).write_text(text, encoding="utf-8")
            finally:
                os.close(fd)
            _git(viewed, home, "init")
            _git(viewed, home, "add", "-A")
            _git(viewed, home, "commit", "-m", "seed store with a declared view")
            before = _tree_digest(viewed, home)
            expect("ci-recipe-clean-viewed-store", _run_ci_recipe(viewed), EXIT_OK)
            expect("ci-recipe-clean-run-read-only", _tree_digest(viewed, home) == before, True)
            view_target = viewed / _opf_store.WORKING_DIRNAME / "WORKLOG.md"
            view_target.write_text(view_target.read_text(encoding="utf-8") + "drifted line\n",
                                   encoding="utf-8")
            _git(viewed, home, "add", "-A")
            _git(viewed, home, "commit", "-m", "commit the drifted view")
            expect("drifted-view-render-check", _run_render_check(viewed), EXIT_FINDING)
            expect("drifted-view-doctor", _run_doctor(viewed, capture=True), EXIT_FINDING)
            before = _tree_digest(viewed, home)
            expect("ci-recipe-drifted-viewed-store", _run_ci_recipe(viewed), EXIT_FINDING)
            expect("ci-recipe-drifted-run-read-only", _tree_digest(viewed, home) == before, True)

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
              "end; --require-store -> 0 clean / 2 NOT-ADOPTED; CI recipe -> 0 clean / 2 NOT-ADOPTED; "
              "recipe contract -> exact step order and arguments (doctor --require-store then render "
              "--check), per-step 0/1/2 propagation with doctor failures stopping before render, abnormal "
              "statuses (3/126/137, a signal death, a non-executable or missing interpreter) normalized "
              "to 2 for BOTH steps with a doctor abnormality stopping before render, a surplus operand a "
              "usage 2 with no step run, a relative invocation under a hostile CDPATH -> the true "
              "verdict, a committed drifted view -> 1 end to end (render --check, doctor, "
              "recipe), both recipe runs read-only (files and directories outside .git/ identical, refs "
              "unchanged), the workflow template free of non-comment continue-on-error and `|| true`; "
              "child-launch failure -> 2; no-repo-root -> 2; invalid-git-marker -> 2; "
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
