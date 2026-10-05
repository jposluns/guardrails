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
clean store and 2 on the non-adopter root, in both the one-operand and the documented NO-OPERAND arity
(root defaults to the recipe's current directory, exercised over a controlled working directory).
Recipe-CONTRACT vectors hold the recipe itself durable: a RECORDING STUB tool asserts the exact invocation
order and arguments (doctor --require-store, then render --check, each over the given root, nothing else)
for the one-operand and the no-operand invocation (root `.`, with stubbed doctor/render failures forwarded,
so a zero-operand short-circuit ahead of the steps cannot stay green), per-step exit propagation (a doctor
1/2 stops the recipe before render; a render 1/2 is the recipe's exit), abnormal-status normalization for
BOTH steps (a child status of 3, 126, 127 or 137 and a genuine signal death each become the recipe's
cannot-evaluate 2, never a forwarded out-of-vocabulary status, a doctor abnormality stopping before render),
and launch-failure normalization for BOTH steps (a missing or non-executable OPF_PYTHON fails the doctor
step to 2, and an interpreter that VANISHES after a passing doctor fails the render step's own launch to 2);
the usage guard (a surplus operand) exits 2 with NO step launched; the recipe run by a RELATIVE
path under a hostile CDPATH naming a decoy pack still resolves its own directory and returns the true
verdict; a committed clean store with ONE declared, planner-populated view red-flags end to end once the
view is edited (render --check 1, doctor 1, recipe 1) while both recipe runs leave the read-only snapshot
unchanged, covering EXACTLY: every entry under the root with only the TOP-LEVEL .git directory pruned (a
nested .git directory below the root is walked like any other entry), by lstat kind, mode, content digest
and symlink target; the directory set; the root directory's and the .git/hooks directory's OWN lstat
records (so a chmod of either reds); the ref set; the LOCAL git configuration; the .git/hooks tree; the
INDEX as git sees it (`git ls-files -s -z`, so an update-index --force-remove or rm --cached reds); and
.git/info/exclude; while HEAD itself (a detach or symbolic-ref retarget), the object store (including
objects/info/alternates), reflogs and other .git metadata files (e.g. info/attributes, description) stay
UNCOVERED by that snapshot; each covered leg is held red by its own per-write mutant fixture (a planted
tag for the ref set, a local-config write, a hook-file write, a file content edit, a symlink plant and
retarget, a file chmod, the two index writes, the exclude write, a nested .git write, the root and
hooks-directory chmods, and an empty mkdir; the directory-set leg is pure defence in depth, every walked
directory being already an entry record, so its mkdir fixture is equally held by the entry leg, and the entry
records' kind component is likewise redundant with their lstat mode, whose file-type bits already encode it,
so no fixture isolates it). The abnormal
sig:15 vectors run AGAIN with SIGTERM inherited as SIG_IGN (a preexec in the recipe launch), so removing
the recording stub's SIG_DFL restore reds the ordinary suite. And the GitHub Actions template is held to
EXACT TEXT over a BYTE GATE: the file is read as RAW BYTES and any byte outside printable ASCII
(0x20-0x7E) plus newline (0x0A) is REFUSED before any comment or blank-line reduction (a tab, a CR, a
BOM, a form feed, a NUL, a DEL and any other control or non-ASCII byte red the gate, closing the
Unicode-whitespace bypass where
Python's broad str whitespace read a U+00A0-led line as a comment that YAML, whose whitespace is only
space and tab, reads as verdict-masking scalar content); the survivor is decoded STRICTLY as ASCII, and
only then must its text with full-line comments and blank lines removed (and nothing else normalized)
equal the module's canonical constant _WORKFLOW_CANONICAL verbatim, so ANY added or changed key
(shell:, env:, defaults:, if:, continue-on-error:, quoted, space-padded or aliased, at step, job or
workflow level, or trailed by an inline "#" comment), any run: continuation line or trailer, a flow
mapping, a second step, or a rewritten on: block reds it, while full-line ASCII comments stay free to
change; each reported bypass spelling, the inline-"#" key, and each refused byte class is held red by its
own mutant fixture, and a sweep plants every refused byte singly. The stub exists because the real tool
cannot
isolate the render step: a
drifted view fails doctor's own C-VIEW-DRIFT too, so only the stub proves the render invocation is still
present, ordered, exactly argued, and forwarded. Each committed fixture is `git init` +
`git add` + `git commit`ed so the `tracked` and `prior` observations _opf_observe.gather derives from HEAD are
real. The clean store is built through the OPF helpers' own canonical emitters and digesters (never
hand-built), the same construction _opf_check's own self-test proves VALID. Offline, stdlib only, fail-closed,
launched isolated (-I -B). The tempdir is removed in a finally (test-hermeticity). When git is not on PATH the
self-test SKIPs clean (a committed HEAD is required for the tracked/prior observations).

The pre-commit floor (opf/enforcement/precommit: the staged-snapshot hook and its per-clone installer, which
writes a pre-commit stub into the clone's untracked hooks directory and never sets core.hooksPath) rides
fixtures of the same kind, each holding a copy of the pack at its pack path. The installer writes the stub,
a rerun is a no-op, and other hooks are kept; it refuses (2, nothing changed) any core.hooksPath, the
tracked pack directory included, an existing pre-commit hook, an unreadable pack hook or hooks directory
(unprivileged user only) and a hooks path that is not a directory; an inherited GIT_DIR naming another
repository cannot redirect it, and a missing dirname run from another repository's pack directory installs
nowhere (2). Inherited GIT_TRACE and GIT_TRACE2 destinations naming another repository's tracked file leave
it unchanged, for the installer and for the hook. A stub that does not read back (no cmp) is removed, with
the hooks directory the run created. The destination is confined to the clone's common git directory: a
hooks directory that is a symbolic link (to another repository's hooks directory, or to a tracked directory
of the working tree) and a git directory inside any worktree's top level other than as its own .git
directory are refused with nothing written there: inside the working tree the installer runs from, inside
the main working tree when it runs from a linked worktree, inside a linked worktree when it runs from the
main one, and equal to the top level (`gitdir: .`); a git directory outside every working tree
(--separate-git-dir) installs. Also refused: a core.worktree a linked worktree reads naming a directory that
holds the git directory, a git directory inside the main worktree's core.worktree from a linked worktree
(one outside it installs), a registry entry naming a top level of /, a linked worktree at the hooks
directory, a hand-written .git file naming the git directory, a linked worktree moved by hand, an empty
core.worktree (a stand-in git answers one), and a termination signal mid-install, which rolls back. A
refusal after rollback names what is at the hooks directory, the stub path and the private directory by
file type: a signal just after the ln or the final rmdir leaves nothing and says so; one just after the
hooks directory's or the private directory's mkdir leaves that empty directory and names it; one that
arrives before the hooks directory's mkdir leaves no hooks directory and names none; a stub the rollback
cannot read is named, not taken as gone; a hooks directory that cannot be searched leaves the paths in it
named as not known. A mkdir that fails because a directory arrived first leaves that directory and its
contents, and a private directory an earlier run left is refused and kept. The main worktree's core.worktree is checked with no .git entry at that top
level. A core.hooksPath only another linked worktree reads (its config.worktree, or
an includeIf onbranch: for its branch) is refused, and so is a worktree registry that cannot be read
(unprivileged user only). The existing stub is compared byte for byte: one more trailing newline, or a
symbolic link to the exact text, is refused. A write that fails partway (a zero file-size limit) leaves no
stub and no temporary file, and a trace2 target the kept global configuration names writes nothing. A link
to another repository's hooks directory, or a directory, that arrives at the stub path just before ln is
refused and kept, with nothing this run wrote left in either repository. A refusal names a backslash in a
path as it is. A post-checkout and a post-merge a branch commits in the pack directory
do not run on checkout or merge. From a linked worktree under a path with spaces the stub lands in the
common hooks directory and the worktree's commits are checked; the stub refuses a commit whose working tree
lacks the pack hook. Through real `git commit` runs over an installed fixture: a clean staged
change commits; a staged tamper of an immutable record body is refused by doctor's resurrection finding over
the snapshot (the hook output must carry that finding, so a refusal for another reason does not satisfy the
vector), with HEAD unmoved, while the same commit in a clone without the hook succeeds; the staged snapshot,
not the working tree, is what is checked (a staged tamper with restored working-tree bytes is refused, an
unstaged tamper does not block a clean staged change); `git commit PATH` and `git commit -a` are checked
against the temporary index git names in GIT_INDEX_FILE; an unborn branch commits a clean store; and a
dedicated sync target equal to the clone's remote stays VALID in the snapshot. Under a recording stub tool,
run by hand: exact order and arguments (doctor --require-store, then render --check, over one snapshot root
under TMPDIR), each launched -I -B; the clone left unchanged over the read-only snapshot described above; a doctor
finding stopping before render, a render error forwarded, an out-of-vocabulary status normalized to 2 and a
surplus operand a usage 2 with no step run; and every snapshot directory removed on exit. Fail closed, each a
named 2: the installer refuses a hooks directory it cannot read (run only for an unprivileged user, as root
reads any directory) or that is not a directory, and a git below the 2.32 floor; it writes the stub and no
configuration when core.hooksPath is supplied only by the environment; the hook stops before any step under a
git below the floor or when the GIT_ variable names cannot be read from the environment (no sed), and exits 2
when the snapshot removal fails (no rm), and a missing dirname stops the hook before any step. The disclosed
CI residual is held as behaviour and text: a staged tamper the hook refuses, committed with --no-verify,
passes the CI recipe, and the shipped files say so; git cherry-pick records the same tamper with the stub
installed and no hook run, and the shipped files name the operations git commits without the hook.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_opf_doctor.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import os
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for the self-test's sibling imports below

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_ERROR = 2

# The GitHub Actions template's canonical EFFECTIVE text: the shipped file's whole text with
# full-line comments and blank lines removed, and NOTHING else normalized (no whitespace, quote or
# case folding). The self-test first BYTE-GATES the shipped file (raw bytes; anything outside
# printable ASCII plus newline REFUSES, so a tab, a CR, a BOM or any control or non-ASCII byte
# reds before any reduction), then holds opf/enforcement/ci/github-actions.yml to EXACT equality
# with this constant, so any added or changed non-comment line reds the gate while the template's
# full-line ASCII comment lines stay free to change. Editing the template deliberately means
# updating this constant in the same change.
_WORKFLOW_CANONICAL = """\
name: OPF
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  opf:
    name: OPF store integrity
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: '3.14'
      - name: OPF CI floor (doctor --require-store, then render --check)
        run: sh opf/enforcement/ci/opf-ci.sh ."""


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
    import signal
    import stat
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
               "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main",
               # F-367: no DETACHED auto-gc/auto-maintenance may outlive a fixture commit and
               # churn .git while a later read or the teardown rmtree traverses it.
               "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"] + list(args)
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

    def _run_ci_recipe(root, tool=None, extra_env=None, extra_args=(), cwd=None, recipe=None,
                       preexec=None):
        """Run the shipped CI recipe (opf/enforcement/ci/opf-ci.sh) over `root` with this interpreter as
        OPF_PYTHON, returning its exit status unmasked. A `root` of None omits the ROOT operand entirely
        (the recipe's documented no-operand invocation: ROOT defaults to the child's current directory,
        so `cwd` supplies the root under test). By default OPF_TOOL is popped so the recipe
        exercises its default pack-relative opf.py path; `tool` sets OPF_TOOL instead (the recipe-contract
        vectors point it at the recording stub), and `extra_env` adds entries (the stub's log path and
        per-verb exit codes, an OPF_PYTHON override for the launch-failure vectors, or a hostile CDPATH).
        `extra_args` appends operands after the root (the usage-guard vector), `cwd` sets the child's
        working directory, and `recipe` substitutes a recipe path passed VERBATIM (the CDPATH vector runs
        a COPY by a RELATIVE path; the default stays the shipped recipe, absolute). `preexec` is
        forwarded to subprocess.run as preexec_fn (the SIGTERM-inherited-as-SIG_IGN vectors ignore
        SIGTERM in the child before exec). A missing `sh`, a
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
        argv = [os.path.abspath(sh), str(shipped) if recipe is None else str(recipe)]
        if root is not None:
            argv.append(str(root))
        argv += [str(a) for a in extra_args]
        proc = subprocess.run(argv, env=env, cwd=None if cwd is None else str(cwd),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              preexec_fn=preexec)
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
        """A snapshot asserting the recipe's read-only claim. It covers EXACTLY these legs: (1) every
        entry under `root` recorded through os.lstat (relpath -> kind, mode, and a regular file's
        sha256 or a symlink's TARGET, never followed, so a chmod, a planted symlink, or a swapped
        entry kind is a visible write; the kind component is redundant with the mode, whose
        S_IFMT file-type bits already encode it, so it adds no reach), with ONLY the top-level
        .git directory pruned; a .git
        directory anywhere BELOW the root is walked and recorded like any other entry; (2) the SORTED
        DIRECTORY LISTING under the same pruning (pure defence in depth: every walked directory is
        already an entry record, so this leg adds redundancy, not reach); (3) the lstat records of the
        root directory ITSELF and
        of the .git/hooks directory ITSELF (a chmod of either is a write the walks alone cannot see);
        (4) the full `git for-each-ref` output (a written ref, e.g. a planted tag, is a repository
        write even though its bytes live under .git/); (5) the LOCAL repository configuration
        (`git config --local --list -z`: doctor's observation gather READS repository config, so a
        config write is store-visible even though it lives under .git/); (6) the .git/hooks tree
        recorded the same lstat way (a planted or edited hook is store-side behaviour); (7) the INDEX
        as git sees it (`git ls-files -s -z`: stage, mode, blob id and path per tracked entry, so an
        `update-index --force-remove` or `rm --cached` is a visible write); and (8) the
        .git/info/exclude file's lstat record. NOT covered, so a write there does NOT red this
        snapshot: HEAD itself (a detach or a symbolic-ref retarget), the object store including
        objects/info/alternates, reflogs, and other .git metadata files (e.g. info/attributes,
        description, FETCH_HEAD). A failing git probe raises OSError (a harness error, exit 2 via
        _classify)."""
        def _lstat_entry(path):
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                return ("link", st.st_mode, os.readlink(path))
            if stat.S_ISREG(st.st_mode):
                with open(path, "rb") as fh:
                    return ("file", st.st_mode, hashlib.sha256(fh.read()).hexdigest())
            return ("other", st.st_mode, "")

        def _lstat_entry_or_absent(path):
            # A recorded ABSENCE, so deleting the entry (itself a write) still flips the snapshot to
            # a mismatch (exit 1) rather than crashing the probe to a harness exit 2.
            try:
                return _lstat_entry(path)
            except FileNotFoundError:
                return ("absent", 0, "")

        entries = {".": _lstat_entry(str(root))}
        dirs = []
        for dirpath, dirnames, filenames in os.walk(str(root)):
            if dirpath == str(root):
                dirnames[:] = [d for d in dirnames if d != ".git"]
            dirs.append(str(Path(dirpath).relative_to(root)))
            for name in dirnames + filenames:
                p = Path(dirpath) / name
                entries[str(p.relative_to(root))] = _lstat_entry(str(p))

        def _git_probe(*args):
            try:
                # F-367: the same pins as _git above, in option position, so even these
                # read-only probes can never spawn a detached auto-gc/auto-maintenance
                # child that outlives the digest and churns .git.
                proc = subprocess.run([git, "--no-replace-objects", "-C", str(root),
                                       "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                                       "-c", "maintenance.auto=false"] + list(args),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      env=_git_env(home), timeout=_GIT_TIMEOUT_S)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise OSError("git {} could not run in {} ({})".format(" ".join(args), root, exc))
            if proc.returncode != 0:
                raise OSError("git {} failed in {} (rc {})".format(" ".join(args), root, proc.returncode))
            return proc.stdout

        refs = _git_probe("for-each-ref")
        config = _git_probe("config", "--local", "--list", "-z")
        index = _git_probe("ls-files", "-s", "-z")
        hooks_dir = os.path.join(str(root), ".git", "hooks")
        hooks = {".": _lstat_entry_or_absent(hooks_dir)}
        for dirpath, dirnames, filenames in os.walk(hooks_dir):
            for name in dirnames + filenames:
                path = os.path.join(dirpath, name)
                hooks[os.path.relpath(path, hooks_dir)] = _lstat_entry(path)
        exclude = _lstat_entry_or_absent(os.path.join(str(root), ".git", "info", "exclude"))
        return entries, sorted(dirs), refs, config, hooks, index, exclude

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
            # The documented NO-OPERAND invocation (the recipe's `${1:-.}`: ROOT defaults to the child's
            # current directory) must hold the SAME floor over a controlled working directory, because
            # the operand-supplying vectors above cannot see a zero-operand short-circuit (e.g. a
            # planted `[ "$#" -eq 0 ] && exit 0`): 0 on the clean store, 2 on the non-adopter directory.
            expect("ci-recipe-no-operand-clean-store", _run_ci_recipe(None, cwd=clean), EXIT_OK)
            expect("ci-recipe-no-operand-not-adopted-root", _run_ci_recipe(None, cwd=empty), EXIT_ERROR)

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
                "import os, signal, sys\n"
                "with open(os.environ['OPF_STUB_LOG'], 'a', encoding='utf-8') as log:\n"
                "    log.write(chr(31).join(sys.argv[1:]) + chr(10))\n"
                "with open(os.environ['OPF_STUB_LOG'] + '.flags', 'a', encoding='utf-8') as log:\n"
                "    log.write('{} {}'.format(sys.flags.isolated, sys.flags.dont_write_bytecode) + chr(10))\n"
                "verb = sys.argv[1].upper() if len(sys.argv) > 1 else 'NONE'\n"
                "rc = os.environ.get('OPF_STUB_RC_' + verb, '0')\n"
                "if rc.startswith('sig:'):\n"
                "    signal.signal(int(rc[4:]), signal.SIG_DFL)\n"
                "    os.kill(os.getpid(), int(rc[4:]))\n"
                "sys.exit(int(rc))\n",
                encoding="utf-8")
            stub_serial = [0]

            def _run_recipe_stubbed(rc_doctor=0, rc_render=0, no_operand=False, preexec=None):
                """Run the recipe over the clean store with the stub as OPF_TOOL; returns (exit status,
                the recorded invocations as argv lists). With `no_operand` the ROOT operand is omitted
                and the clean store becomes the child's WORKING DIRECTORY instead (the recipe's
                documented default), so the recorded --root is the literal `.`. `preexec` is
                forwarded to the recipe launch (the SIGTERM-inherited-as-SIG_IGN vectors)."""
                stub_serial[0] += 1
                log = base / "stub-log-{}".format(stub_serial[0])
                rc = _run_ci_recipe(None if no_operand else clean,
                                    cwd=clean if no_operand else None, tool=stub, preexec=preexec,
                                    extra_env=dict(
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
            # Both steps launch the tool isolated (-I -B): the stub records sys.flags.isolated and
            # sys.flags.dont_write_bytecode per invocation, so dropping either flag reds this vector.
            flags_log = base / "stub-log-{}.flags".format(stub_serial[0])
            expect("ci-recipe-isolated-launch",
                   flags_log.read_text(encoding="utf-8").splitlines() if flags_log.is_file() else [],
                   ["1 1", "1 1"])
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
            # The NO-OPERAND arity under the stub: the exact order and arguments hold with the default
            # root `.` (nothing else), and a stubbed doctor or render failure is forwarded, so a
            # zero-operand short-circuit ahead of the steps cannot stay green here either.
            expect("ci-recipe-no-operand-order-and-args", _run_recipe_stubbed(no_operand=True),
                   (EXIT_OK, [["doctor", "--require-store", "--root", "."],
                              ["render", "--check", "--root", "."]]))
            rc, calls = _run_recipe_stubbed(rc_doctor=1, no_operand=True)
            expect("ci-recipe-no-operand-doctor-finding-stops",
                   (rc, [c[0] for c in calls]), (EXIT_FINDING, ["doctor"]))
            rc, calls = _run_recipe_stubbed(rc_render=2, no_operand=True)
            expect("ci-recipe-no-operand-render-error-propagates",
                   (rc, [c[0] for c in calls]), (EXIT_ERROR, ["doctor", "render"]))
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
            # Launch failure of the SECOND step: doctor launches and PASSES, then the interpreter
            # vanishes, so the failure (the shell's own 127) arises at render's OWN launch. The
            # missing/non-executable vectors above fail before doctor ever runs, so only this vector
            # witnesses launch-failure normalization on the render step; a passthrough special-casing
            # render's 127 (mapping it to 0) reds here. The vanishing interpreter is a scratch symlink
            # to this interpreter that the stand-in tool unlinks while handling the doctor verb.
            vanishing = base / "vanishing-python"
            os.symlink(sys.executable, str(vanishing))
            vanish_tool = base / "vanishing_stub.py"
            vanish_tool.write_text(
                "import os, sys\n"
                "if sys.argv[1:2] == ['doctor']:\n"
                "    os.unlink(os.environ['OPF_PYTHON'])\n"
                "sys.exit(0)\n",
                encoding="utf-8")
            expect("ci-recipe-render-launch-failure",
                   _run_ci_recipe(clean, tool=vanish_tool,
                                  extra_env=dict(OPF_PYTHON=str(vanishing))), EXIT_ERROR)
            # Abnormal-status normalization for BOTH steps: any child status outside the 0/1/2 verdict
            # vocabulary (a tool bug's 3, a 126/127/137 launch or kill status leaking through as a
            # child EXIT status, a genuine signal death) must surface as the recipe's cannot-evaluate
            # 2, never forwarded as if it were a verdict and never masked to 0 (a passthrough
            # special-casing 127 reds on that value for either step). A doctor abnormality stops before
            # render (the same short-circuit the 1/2 vectors assert); a render abnormality follows both
            # steps.
            for bad in ("3", "126", "127", "137", "sig:15"):
                rc, calls = _run_recipe_stubbed(rc_doctor=bad)
                expect("ci-recipe-doctor-abnormal-{}".format(bad),
                       (rc, [c[0] for c in calls]), (EXIT_ERROR, ["doctor"]))
                rc, calls = _run_recipe_stubbed(rc_render=bad)
                expect("ci-recipe-render-abnormal-{}".format(bad),
                       (rc, [c[0] for c in calls]), (EXIT_ERROR, ["doctor", "render"]))
            # The sig:15 vectors AGAIN with SIGTERM inherited as SIG_IGN (set by a preexec in the
            # recipe launch; a non-interactive sh keeps an entry-ignored signal ignored, and the stub
            # inherits it). The stub must restore SIG_DFL before killing itself for its death to be a
            # GENUINE signal death; without that restore the ignored kill falls through to
            # int('sig:15') and the stub exits 1 instead, so deleting the stub's
            # signal.signal(..., SIG_DFL) line reds these two vectors in the ORDINARY suite (the
            # regression guard the plain sig:15 vectors above cannot provide).
            def _inherit_sigterm_ignored():
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            rc, calls = _run_recipe_stubbed(rc_doctor="sig:15", preexec=_inherit_sigterm_ignored)
            expect("ci-recipe-doctor-abnormal-sig:15-inherited-ignored",
                   (rc, [c[0] for c in calls]), (EXIT_ERROR, ["doctor"]))
            rc, calls = _run_recipe_stubbed(rc_render="sig:15", preexec=_inherit_sigterm_ignored)
            expect("ci-recipe-render-abnormal-sig:15-inherited-ignored",
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
            # in this repository would otherwise drift as prose) by EXACT TEXT over a BYTE GATE, not a
            # line pattern: the file is read as RAW BYTES and any byte outside printable ASCII
            # (0x20-0x7E) plus newline (0x0A) REFUSES before any comment or blank-line reduction, the
            # survivor is decoded strictly as ASCII, and only then must the
            # template's whole text with full-line comments and blank lines removed (and NOTHING else
            # normalized) equal _WORKFLOW_CANONICAL verbatim. Any added key (shell:, env:,
            # defaults:, if:, continue-on-error:, quoted, space-padded or aliased, at step, job or
            # workflow level), any run: continuation line or trailer, a flow mapping, a second step, or
            # a rewritten on: block therefore reds; only full-line ASCII comments and blank lines stay
            # free to change. Editing the template deliberately means updating the constant in the same
            # change. Each bypass spelling reported against the older line-pattern assertion, a key
            # trailed by an inline "#" comment, and each refused byte class (the reported
            # U+00A0-led 'comment' continuation that YAML reads as verdict-masking scalar content,
            # a tab, a CR, a BOM, a form feed, a NUL, a DEL), is held red below by its own mutant
            # fixture.
            workflow = (Path(__file__).resolve().parent.parent / "enforcement" / "ci"
                        / "github-actions.yml")

            def _wf_effective(data):
                # Defined over RAW BYTES first: Python's str whitespace is a SUPERSET of YAML's
                # (str.strip removes U+00A0 and friends; str.splitlines also splits U+0085,
                # U+2028 and U+2029), so a str-level reduction can read a Unicode-whitespace-led
                # "#" line as a comment that YAML reads as verdict-masking scalar content. Any
                # byte outside printable ASCII (0x20-0x7E) plus newline (0x0A) therefore REFUSES
                # before any comment or blank-line stripping (that covers a tab, a CR, a BOM and
                # every other control or non-ASCII byte); the survivor is decoded STRICTLY as
                # ASCII, split on "\n" alone, and a line is dropped only when it is empty or all
                # SPACES or when its first non-space character is "#".
                bad = sorted(set(b for b in data if b != 0x0A and not 0x20 <= b <= 0x7E))
                if bad:
                    return ("REFUSED: byte(s) outside printable ASCII plus newline: "
                            + " ".join("0x" + format(b, "02x") for b in bad))
                text = data.decode("ascii")  # cannot fail after the byte gate; strict by intent
                return "\n".join(ln for ln in text.split("\n")
                                 if ln.strip(" ") and not ln.lstrip(" ").startswith("#"))

            expect("workflow-exact-effective-text",
                   _wf_effective(workflow.read_bytes()), _WORKFLOW_CANONICAL)
            expect("workflow-comment-and-blank-lines-free",
                   _wf_effective(("# a template comment may change freely\n\n"
                                  + _WORKFLOW_CANONICAL).encode("ascii")),
                   _WORKFLOW_CANONICAL)
            # The mutant fixtures: every bypass spelling reported against the older line-pattern
            # assertion, applied to the canonical text; each effective text must DIFFER from the
            # canonical (red). A no-op replace leaves the mutant equal to the canonical, so a stale
            # needle fails its own fixture (fail-closed). The inline-comment key holds the comment
            # filter to FULL-LINE comments: a filter weakened to drop any line containing "#" would
            # reduce "if: false # x" away and leave the canonical, so that fixture reds it.
            wf_run = "        run: sh opf/enforcement/ci/opf-ci.sh ."
            wf_step = "      - name: OPF CI floor (doctor --require-store, then render --check)"
            wf_job = "    runs-on: ubuntu-latest"
            wf_mutants = (
                ("run-continuation-trailer", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n          || true")),
                ("run-inline-trailer", _WORKFLOW_CANONICAL.replace(wf_run, wf_run + " || true")),
                ("second-run-step", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n      - name: mask\n        run: echo skipped")),
                ("second-step-flow-mapping", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n      - {name: mask, run: 'true'}")),
                ("step-shell-exit-zero", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + '\n        shell: bash -c "{0}; exit 0"')),
                ("step-shell-true", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n        shell: true {0}")),
                ("step-env-opf-python", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n        env:\n          OPF_PYTHON: 'true'")),
                ("step-env-opf-tool", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n        env:\n          OPF_TOOL: /dev/null")),
                ("workflow-env-opf-python", _WORKFLOW_CANONICAL.replace(
                    "name: OPF\non:", "name: OPF\nenv:\n  OPF_PYTHON: 'true'\non:")),
                ("job-defaults-flow", _WORKFLOW_CANONICAL.replace(
                    wf_job, wf_job + "\n    defaults: {run: {shell: 'true {0}'}}")),
                ("job-defaults-block", _WORKFLOW_CANONICAL.replace(
                    wf_job, wf_job + "\n    defaults:\n      run:\n        shell: 'true {0}'")),
                ("step-plain-if", _WORKFLOW_CANONICAL.replace(wf_run, wf_run + "\n        if: false")),
                ("step-single-quoted-if", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n        'if': false")),
                ("step-double-quoted-if", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + '\n        "if": false')),
                ("step-space-padded-if", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n        if : false")),
                ("step-if-inline-comment", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n        if: false # x")),
                ("job-double-quoted-if", _WORKFLOW_CANONICAL.replace(
                    wf_job, wf_job + '\n    "if": false')),
                ("step-quoted-continue-on-error", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + '\n        "continue-on-error": true')),
                ("alias-if-key", _WORKFLOW_CANONICAL.replace(
                    "permissions:", "x-key: &k if\npermissions:").replace(
                    wf_run, wf_run + "\n        *k : false")),
                ("on-workflow-dispatch-only", _WORKFLOW_CANONICAL.replace(
                    "on:\n  pull_request:\n  push:\n    branches: [main]",
                    "on:\n  workflow_dispatch:")),
                ("preceding-github-script-step", _WORKFLOW_CANONICAL.replace(
                    wf_step, "      - uses: actions/github-script@v7\n        with:\n"
                    "          script: overwrite opf-ci.sh\n" + wf_step)),
            )
            for wf_label, wf_mutant in wf_mutants:
                expect("workflow-mutant-reds-{}".format(wf_label),
                       _wf_effective(wf_mutant.encode("ascii")) == _WORKFLOW_CANONICAL, False)
            # Byte-gate mutant fixtures: each plants a byte outside printable ASCII plus newline
            # and must be REFUSED outright (unequal to the canonical AND flagged as a refusal,
            # never reduced to an equal text). The first two are the reported bypass: a run:
            # continuation led by Unicode whitespace (U+00A0, U+3000) and then "#", which the
            # older str-based reduction stripped as a comment while YAML, whose whitespace is
            # only space and tab, reads it as scalar content whose "|| true" masks the step's
            # verdict. The tab, CRLF and BOM plants pin those bytes; the form-feed and NUL plants
            # (each before "#" on a space-led comment line) pin the other C0 controls, and the DEL
            # plant pins 0x7F, so a gate narrowed to tab, CR and non-ASCII alone passes them to the
            # reduction unrefused and reds. The sweep after them plants EVERY byte outside printable
            # ASCII plus newline, one at a time, before "#" on the same kind of line, so a gate that
            # exempts any single such byte reds and names it (a strict-decode error on a passed
            # non-ASCII byte counts as unrefused, never a crash that would swallow the suite).
            wf_byte_mutants = (
                ("unicode-nbsp-comment-continuation", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n          \u00a0# || true").encode("utf-8")),
                ("unicode-ideographic-space-comment-continuation", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n          \u3000# || true").encode("utf-8")),
                ("tab-led-comment-line", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n\t# a tab-led comment").encode("utf-8")),
                ("crlf-line-endings", _WORKFLOW_CANONICAL.replace("\n", "\r\n").encode("utf-8")),
                ("utf8-bom-prefix", b"\xef\xbb\xbf" + _WORKFLOW_CANONICAL.encode("utf-8")),
                ("form-feed-led-comment-line", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n          \x0c# || true").encode("ascii")),
                ("nul-led-comment-line", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n          \x00# || true").encode("ascii")),
                ("del-led-comment-line", _WORKFLOW_CANONICAL.replace(
                    wf_run, wf_run + "\n          \x7f# || true").encode("ascii")),
            )
            for wf_label, wf_data in wf_byte_mutants:
                wf_eff = _wf_effective(wf_data)
                expect("workflow-mutant-refused-{}".format(wf_label),
                       (wf_eff != _WORKFLOW_CANONICAL, wf_eff.startswith("REFUSED")),
                       (True, True))

            def _wf_refused(data):
                try:
                    return _wf_effective(data).startswith("REFUSED")
                except UnicodeDecodeError:
                    return False

            wf_plant = _WORKFLOW_CANONICAL.replace(wf_run, wf_run + "\n          ").encode("ascii")
            expect("workflow-byte-gate-refuses-every-outside-byte",
                   ["0x" + format(b, "02x") for b in range(256)
                    if b != 0x0A and not 0x20 <= b <= 0x7E
                    and not _wf_refused(wf_plant + bytes([b]) + b"# || true")], [])

            # --- The recipe end to end over a REAL drifted view (U16): a committed clean store
            # with ONE declared view, populated through the U4 engine's own public planner
            # (_opf_views.plan_views, the check_opf_drift fixture idiom; never hand-built), then edited and
            # re-committed. First witness the fixture is genuinely clean through BOTH recipe steps (0),
            # then witness the drift red-flags end to end: render --check itself exits 1, doctor exits 1
            # (C-VIEW-DRIFT is part of validate_store), and the recipe forwards the finding (1). Both
            # recipe runs leave the WHOLE _tree_digest snapshot unchanged (entries by lstat kind, mode,
            # content and symlink target; directories; the root and .git/hooks directory records; refs;
            # local git config; the .git/hooks tree; the index; and .git/info/exclude): the read-only
            # claim, held as a check. --
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

            # Read-only-snapshot per-write mutant fixtures: one write per covered snapshot leg
            # (the first six are the reported bypasses of the older snapshot), applied directly
            # to the fixture tree; each must CHANGE the
            # digest (red). A FRESH before is taken per vector, so the writes are cumulative and
            # never undone (the viewed store is not used again; the tempdir is removed in the
            # finally). The two index writes red via the ls-files leg, the nested .git write via the
            # top-level-only .git prune, the exclude write via the info/exclude leg, and the two
            # chmods via the root's and the hooks directory's own lstat records. The remaining legs
            # are each held by their own write: a planted tag via the ref set, a config write via
            # the local configuration, a hook file via the .git/hooks tree walk, a content edit via
            # the entry CONTENT DIGEST alone (kind and mode unchanged), a symlink plant via the SET
            # of entry keys (a new relpath, so it reds even with every entry record reduced to its
            # key), its retarget via the entry TARGET alone, a file chmod via the entry MODE alone,
            # and an empty mkdir via the directory listing (which the entry records subsume, so
            # that leg is redundancy, not reach). The entry KIND component is redundant as well:
            # the lstat mode's file-type bits already encode it, so no write changes the kind
            # without changing the mode, and no fixture can isolate it.
            def _snapshot_sees(label, mutate):
                before_m = _tree_digest(viewed, home)
                mutate()
                expect("readonly-snapshot-sees-{}".format(label),
                       _tree_digest(viewed, home) != before_m, True)

            def _chmod_flip(path):
                os.chmod(path, stat.S_IMODE(os.lstat(path).st_mode) ^ 0o010)

            def _nested_git_write():
                # exist_ok, and an own filename: if a hostile recipe already planted this nested
                # .git, the read-only vectors above are already red; this fixture then still runs
                # (adding its own file) instead of crashing the suite to a harness 2 that would
                # swallow the recorded failures.
                nested = viewed / _opf_store.WORKING_DIRNAME / ".git"
                nested.mkdir(exist_ok=True)
                (nested / "wrote-fixture").write_text("x\n", encoding="utf-8")

            def _exclude_append():
                with open(str(viewed / ".git" / "info" / "exclude"), "a", encoding="utf-8") as fh:
                    fh.write("VERSION\n")

            _snapshot_sees("index-update-index-force-remove",
                           lambda: _git(viewed, home, "update-index", "--force-remove",
                                        "{}/{}".format(machine_rel, _opf_store.MANIFEST_NAME)))
            _snapshot_sees("index-rm-cached",
                           lambda: _git(viewed, home, "rm", "-q", "--cached", "VERSION"))
            _snapshot_sees("nested-git-dir-write", _nested_git_write)
            _snapshot_sees("git-info-exclude-write", _exclude_append)
            _snapshot_sees("root-dir-chmod", lambda: _chmod_flip(str(viewed)))
            _snapshot_sees("hooks-dir-chmod",
                           lambda: _chmod_flip(str(viewed / ".git" / "hooks")))
            _snapshot_sees("ref-write",
                           lambda: _git(viewed, home, "tag", "opf-selftest-tag"))
            _snapshot_sees("local-config-write",
                           lambda: _git(viewed, home, "config", "--local", "opf.selftest", "wrote"))
            _snapshot_sees("hook-file-write",
                           lambda: (viewed / ".git" / "hooks" / "pre-commit").write_text(
                               "#!/bin/sh\nexit 0\n", encoding="utf-8"))
            _snapshot_sees("file-content-edit",
                           lambda: (viewed / "VERSION").write_text("0.0.0-selftest\n",
                                                                   encoding="utf-8"))
            _snapshot_sees("file-chmod", lambda: _chmod_flip(str(viewed / "VERSION")))
            _snapshot_sees("symlink-plant",
                           lambda: os.symlink("VERSION", str(viewed / "selftest-link")))

            def _symlink_retarget():
                # Same kind and (constant) symlink mode; only the recorded TARGET changes, so
                # this write is visible through the target component alone.
                os.remove(str(viewed / "selftest-link"))
                os.symlink("selftest-retargeted", str(viewed / "selftest-link"))

            _snapshot_sees("symlink-retarget", _symlink_retarget)
            _snapshot_sees("empty-dir-mkdir",
                           lambda: (viewed / "selftest-empty-dir").mkdir())

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

    def _precommit_suite():
        """The pre-commit floor (enforcement pack, spec 1.3.0 (draft) 14.1: staged-snapshot pre-commit checks
        with the per-clone installation residual disclosed). Each fixture is a committed clean store holding
        a copy of opf/enforcement/precommit at its pack path, with OPF_TOOL pointing at this opf.py and
        TMPDIR at a controlled directory. Returns the list of assertion failures; raises OSError if a fixture
        cannot be built (a harness error, exit 2 via _classify)."""
        failures = []

        def expect(label, got, want):
            if got != want:
                failures.append("pre-commit: {}: got {}, expected {}".format(label, got, want))

        pack = Path(__file__).resolve().parent.parent / "enforcement" / "precommit"
        if not (pack / "pre-commit").is_file() or not (pack / "install.sh").is_file():
            raise OSError("the pre-commit pack {} is incomplete".format(pack))
        rel = "opf/enforcement/precommit"
        base = Path(tempfile.mkdtemp(prefix="opf-precommit-selftest-")).resolve()
        home = base / "home"
        home.mkdir()
        snaps = base / "tmp"
        snaps.mkdir()
        stub = base / "stub_opf.py"
        stub.write_text(
            "import os, sys\n"
            "with open(os.environ['OPF_STUB_LOG'], 'a', encoding='utf-8') as log:\n"
            "    log.write(chr(31).join(sys.argv[1:]) + chr(10))\n"
            "with open(os.environ['OPF_STUB_LOG'] + '.flags', 'a', encoding='utf-8') as log:\n"
            "    log.write('{} {}'.format(sys.flags.isolated, sys.flags.dont_write_bytecode) + chr(10))\n"
            "verb = sys.argv[1].upper() if len(sys.argv) > 1 else 'NONE'\n"
            "sys.exit(int(os.environ.get('OPF_STUB_RC_' + verb, '0')))\n",
            encoding="utf-8")

        def _env(extra=None):
            # The fixture git env (every ambient GIT_ variable dropped, global and system config
            # neutralized, HOME the fixture home) plus the pack's overrides.
            env = _git_env(home)
            env.update(OPF_PYTHON=sys.executable, OPF_TOOL=str(Path(__file__).resolve().parent / "opf.py"),
                       TMPDIR=str(snaps))
            if extra:
                env.update(extra)
            return env

        def _run(args, cwd, extra=None):
            # Run a pack script under sh and return its exit status. A missing sh is a harness error.
            sh = shutil.which("sh")
            if sh is None:
                raise OSError("sh not found on PATH; the pre-commit pack cannot be run")
            argv = [os.path.abspath(sh)] + list(args)
            try:
                proc = subprocess.run(argv, cwd=str(cwd), env=_env(extra),
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise OSError("could not run {} in {} ({})".format(args, cwd, exc))
            return proc.returncode

        def _fixture(name, machine=None, remote=None, commit=True):
            root = base / name
            root.mkdir()
            _write_machine(root, machine if machine is not None else clean_machine())
            _write_product(root)
            shutil.copytree(str(pack), str(root / rel))
            _git(root, home, "init")
            if remote is not None:
                _git(root, home, "remote", "add", "origin", remote)
            if commit:
                _git(root, home, "add", "-A")
                _git(root, home, "commit", "-m", "seed store")
            return root

        def _install(root, extra=None):
            return _run([str(root / rel / "install.sh")], base, extra)

        def _git_out(root, *args):
            proc = subprocess.run([git, "--no-replace-objects", "-C", str(root), "-c", "gc.auto=0",
                                   "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"] + list(args),
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=_git_env(home), timeout=_GIT_TIMEOUT_S)
            return proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else None

        def _hookspath(root):
            return _git_out(root, "config", "--get", "core.hooksPath")

        def _stub(root):
            # The text of the installed pre-commit stub in the clone's untracked hooks directory, or None.
            path = root / ".git" / "hooks" / "pre-commit"
            if not path.is_file() or not os.access(str(path), os.X_OK):
                return None
            return path.read_text(encoding="utf-8")

        def _stub_ok(root):
            # True when the stub is installed and names the pack hook at its pack path.
            text = _stub(root)
            return text is not None and ("hook='./" + rel + "/pre-commit'") in text

        def _head(root):
            return _git_out(root, "rev-parse", "-q", "--verify", "HEAD")

        def _commit(root, *args):
            # A real `git commit`, so git itself runs the installed hook with its own GIT_INDEX_FILE.
            # Returns (exit status, combined output).
            try:
                proc = subprocess.run([git, "--no-replace-objects", "-C", str(root),
                                       "-c", "user.email=opf@example.invalid", "-c", "user.name=OPF Self Test",
                                       "-c", "commit.gpgsign=false", "-c", "gc.auto=0",
                                       "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                                       "commit", "-q", "-m", "change"] + list(args),
                                      cwd=str(root), env=_env(), stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, timeout=300)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise OSError("could not run git commit in {} ({})".format(root, exc))
            return proc.returncode, proc.stdout.decode("utf-8", "replace")

        def _refused(root, *args):
            # (refused, HEAD unchanged, refused BY doctor's resurrection finding over the snapshot) for one
            # commit attempt; a refusal for any other reason (a cannot-evaluate, say) is not this vector.
            before = _head(root)
            rc, text = _commit(root, *args)
            return rc != EXIT_OK, _head(root) == before, "FINDING: C-HISTORY-RESURRECTION" in text

        def _committed(root, *args):
            # (exit status, HEAD moved) for one commit attempt.
            before = _head(root)
            rc, _text = _commit(root, *args)
            return rc, _head(root) != before

        def _tamper(root):
            _write_machine(root, {"done.index.toml": idx([dn(1, "BI-1", title="tampered")])})

        def _untamper(root):
            _write_machine(root, {"done.index.toml": idx([dn(1, "BI-1")])})

        done_rel = "{}/{}/done.index.toml".format(_opf_store.WORKING_DIRNAME,
                                                     _opf_store.DEFAULT_MACHINE_SUBDIR)
        # A git that reports 2.31.0 (below the 2.32 floor) and otherwise runs the real git.
        old_bin = base / "old-git-bin"
        old_bin.mkdir()
        (old_bin / "git").write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = --version ]; then echo 'git version 2.31.0'; exit 0; fi\n"
            "exec \"$OPF_REAL_GIT\" \"$@\"\n", encoding="utf-8")
        os.chmod(str(old_bin / "git"), 0o755)
        old_git_env = dict(PATH=str(old_bin) + os.pathsep + _env().get("PATH", os.defpath), OPF_REAL_GIT=git)

        hook_tools = ("git", "dirname", "mktemp", "env", "sed", "mkdir", "rm")
        # ls is not run by the installer; it is here so an installer that still lists the hooks directory
        # with it is judged on the command a vector removes, not on a missing ls.
        install_tools = ("git", "dirname", "env", "sed", "cat", "cmp", "chmod", "ln", "rm", "rmdir", "mkdir", "ls")

        def _path_without(missing, needed=hook_tools, tag="hook"):
            # A PATH holding only the external commands the script runs (`needed`), less `missing`.
            tools = base / ("bin-" + tag + "-without-" + missing)
            tools.mkdir()
            for tool in needed:
                if tool == missing:
                    continue
                found = shutil.which(tool)
                if found is None:
                    raise OSError(tool + " not found on PATH; the hook's tool set cannot be built")
                os.symlink(os.path.abspath(found), str(tools / tool))
            return dict(PATH=str(tools))

        try:
            # --- The installer. It writes the pre-commit stub into the clone's untracked hooks directory and
            # sets no core.hooksPath (0); a rerun is a no-op (0). ---
            inst = _fixture("install")
            expect("install-writes-stub", (_install(inst), _stub_ok(inst), _hookspath(inst)), (EXIT_OK, True, None))
            first = _stub(inst)
            expect("install-rerun-no-op", (_install(inst), _stub(inst) == first), (EXIT_OK, True))
            # A core.hooksPath set to any value (git would run no hook from the hooks directory) is refused
            # (2) and left as it was, including the tracked pack directory a former install set.
            other = _fixture("install-other")
            _git(other, home, "config", "core.hooksPath", "elsewhere")
            expect("install-refuses-other-hookspath", (_install(other), _hookspath(other), _stub(other)),
                   (EXIT_ERROR, "elsewhere", None))
            former = _fixture("install-former")
            _git(former, home, "config", "core.hooksPath", rel)
            expect("install-refuses-pack-hookspath", (_install(former), _hookspath(former), _stub(former)),
                   (EXIT_ERROR, rel, None))
            # A pre-commit hook already in the hooks directory, which the stub would replace, is refused and
            # left as it was; any other hook there is kept and the stub installs beside it.
            planted = _fixture("install-planted")
            (planted / ".git" / "hooks").mkdir(exist_ok=True)
            (planted / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            expect("install-refuses-existing-hook",
                   (_install(planted), (planted / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")),
                   (EXIT_ERROR, "#!/bin/sh\nexit 0\n"))
            kept = _fixture("install-kept")
            (kept / ".git" / "hooks").mkdir(exist_ok=True)
            (kept / ".git" / "hooks" / "post-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            expect("install-keeps-other-hooks",
                   (_install(kept), _stub_ok(kept),
                    (kept / ".git" / "hooks" / "post-commit").read_text(encoding="utf-8")),
                   (EXIT_OK, True, "#!/bin/sh\nexit 0\n"))
            # An inherited GIT_DIR naming another repository cannot redirect the install: it lands in the
            # clone that holds the installer, and the other repository is untouched.
            target = _fixture("install-target")
            decoy = _fixture("install-decoy")
            expect("install-ignores-ambient-git-dir",
                   (_install(target, dict(GIT_DIR=str(decoy / ".git"))), _stub_ok(target), _stub(decoy)),
                   (EXIT_OK, True, None))
            # A surplus operand is a usage error.
            expect("install-usage", _run([str(inst / rel / "install.sh"), "x"], base), EXIT_ERROR)
            # Root reads any file and directory, so the two unreadable vectors run only for an unprivileged
            # user. A pack hook the stub could not read is refused with no stub written; a hooks directory
            # that cannot be read is refused and the hook in it left as it was.
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                unread_pack = _fixture("install-unreadable-pack-hook")
                os.chmod(str(unread_pack / rel / "pre-commit"), 0o311)
                try:
                    expect("install-refuses-unreadable-pack-hook", (_install(unread_pack), _stub(unread_pack)),
                           (EXIT_ERROR, None))
                finally:
                    os.chmod(str(unread_pack / rel / "pre-commit"), 0o755)
                unread = _fixture("install-unreadable-hooks")
                unread_hooks = unread / ".git" / "hooks"
                unread_hooks.mkdir(exist_ok=True)
                (unread_hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
                os.chmod(str(unread_hooks), 0o111)
                try:
                    rc = _install(unread)
                finally:
                    os.chmod(str(unread_hooks), 0o755)
                expect("install-refuses-unreadable-hooks-dir",
                       (rc, (unread_hooks / "pre-commit").read_text(encoding="utf-8")),
                       (EXIT_ERROR, "#!/bin/sh\nexit 1\n"))
            # A hooks path that is not a directory is refused and left as it was.
            notdir = _fixture("install-hooks-not-dir")
            shutil.rmtree(str(notdir / ".git" / "hooks"), ignore_errors=True)
            (notdir / ".git" / "hooks").write_text("", encoding="utf-8")
            expect("install-refuses-hooks-not-dir", (_install(notdir), (notdir / ".git" / "hooks").is_file()),
                   (EXIT_ERROR, True))
            # A core.hooksPath supplied only by the environment (a `git -c` or alias caller) is dropped, not
            # read as configuration: the stub installs and no configuration is written.
            envcfg = _fixture("install-env-config")
            env_only = dict(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="core.hooksPath", GIT_CONFIG_VALUE_0=rel)
            expect("install-ignores-env-config", (_install(envcfg, env_only), _stub_ok(envcfg), _hookspath(envcfg)),
                   (EXIT_OK, True, None))
            # A git older than the 2.32 floor is refused (2) with no stub written.
            oldgit = _fixture("install-old-git")
            expect("install-refuses-old-git", (_install(oldgit, old_git_env), _stub(oldgit)), (EXIT_ERROR, None))
            # A missing dirname fails closed (2): run from inside another repository's pack directory, the
            # installer neither installs there nor in its own clone.
            nodir = _fixture("install-no-dirname")
            nodir_cwd = _fixture("install-no-dirname-cwd")
            rc = _run([str(nodir / rel / "install.sh")], nodir_cwd / rel,
                      _path_without("dirname", install_tools, "install"))
            expect("install-dirname-missing-refused",
                   (rc, _stub(nodir), _stub(nodir_cwd), _hookspath(nodir_cwd)), (EXIT_ERROR, None, None, None))
            # Inherited git trace destinations are dropped before the first git call: a trace file that is
            # another repository's tracked file is left byte for byte as it was, on a first install and on
            # the already-installed rerun.
            traced = _fixture("install-trace-target")
            (traced / "TRACED.md").write_text("traced\n", encoding="utf-8")
            _git(traced, home, "add", "TRACED.md")
            _git(traced, home, "commit", "-q", "--no-verify", "-m", "traced")
            trace_file = str(traced / "TRACED.md")
            trace_env = dict(GIT_TRACE=trace_file, GIT_TRACE_SETUP=trace_file, GIT_TRACE_PERFORMANCE=trace_file,
                             GIT_TRACE2=trace_file, GIT_TRACE2_EVENT=trace_file, GIT_TRACE2_PERF=trace_file)
            tracing = _fixture("install-traced")
            expect("install-git-trace-scrubbed",
                   (_install(tracing, trace_env), _install(tracing, trace_env), _stub_ok(tracing),
                    (traced / "TRACED.md").read_text(encoding="utf-8")),
                   (EXIT_OK, EXIT_OK, True, "traced\n"))
            # A stub that does not read back (no cmp) is rolled back: the stub is removed, and so is the
            # hooks directory when this run created it.
            readback = _fixture("install-readback")
            expect("install-readback-failure-rolls-back",
                   (_install(readback, _path_without("cmp", install_tools, "install")),
                    (readback / ".git" / "hooks" / "pre-commit").exists(), _hookspath(readback)),
                   (EXIT_ERROR, False, None))
            readback_dir = _fixture("install-readback-dir")
            shutil.rmtree(str(readback_dir / ".git" / "hooks"))
            expect("install-readback-failure-removes-created-dir",
                   (_install(readback_dir, _path_without("cmp", install_tools, "install-dir")),
                    (readback_dir / ".git" / "hooks").exists()),
                   (EXIT_ERROR, False))
            # The destination is confined to the clone's own common git directory. A hooks directory that is
            # a symbolic link is refused (2) with nothing written through it: one naming another repository's
            # hooks directory, and one naming a tracked directory of the clone's own working tree.
            via = _fixture("install-hooks-link-other")
            via_target = _fixture("install-hooks-link-target")
            (via_target / ".git" / "hooks").mkdir(exist_ok=True)
            shutil.rmtree(str(via / ".git" / "hooks"), ignore_errors=True)
            os.symlink(str(via_target / ".git" / "hooks"), str(via / ".git" / "hooks"))
            expect("install-refuses-hooks-link-to-other-repo",
                   (_install(via), (via_target / ".git" / "hooks" / "pre-commit").exists()), (EXIT_ERROR, False))
            tracked = _fixture("install-hooks-link-tracked")
            (tracked / "hookdir").mkdir()
            (tracked / "hookdir" / "README.md").write_text("tracked\n", encoding="utf-8")
            _git(tracked, home, "add", "hookdir")
            _git(tracked, home, "commit", "-q", "--no-verify", "-m", "tracked hook directory")
            shutil.rmtree(str(tracked / ".git" / "hooks"), ignore_errors=True)
            os.symlink(str(tracked / "hookdir"), str(tracked / ".git" / "hooks"))
            expect("install-refuses-hooks-link-to-tracked-dir",
                   (_install(tracked), (tracked / "hookdir" / "pre-commit").exists()), (EXIT_ERROR, False))
            # A git directory inside the working tree under no .git component (git tracks its files) is
            # refused with no stub written there.
            inner = _fixture("install-inner-gitdir")
            os.rename(str(inner / ".git"), str(inner / "gd"))
            (inner / ".git").write_text("gitdir: " + str(inner / "gd") + "\n", encoding="utf-8")
            expect("install-refuses-gitdir-in-worktree",
                   (_install(inner), (inner / "gd" / "hooks" / "pre-commit").exists()), (EXIT_ERROR, False))
            # A core.hooksPath that only another linked worktree reads is refused (2), with no stub written:
            # its own config.worktree under extensions.worktreeConfig, and an includeIf onbranch: for the
            # branch it has checked out.
            scoped = _fixture("install-wt-scoped")
            _git(scoped, home, "config", "extensions.worktreeConfig", "true")
            _git(scoped, home, "worktree", "add", "-q", "-b", "side", str(base / "install wt scoped"))
            _git(base / "install wt scoped", home, "config", "--worktree", "core.hooksPath", str(base / "elsewhere"))
            expect("install-refuses-other-worktree-hookspath", (_install(scoped), _stub(scoped)), (EXIT_ERROR, None))
            onbranch = _fixture("install-wt-onbranch")
            (base / "onbranch.inc").write_text("[core]\n\thooksPath = elsewhere\n", encoding="utf-8")
            _git(onbranch, home, "config", "includeIf.onbranch:side.path", str(base / "onbranch.inc"))
            _git(onbranch, home, "worktree", "add", "-q", "-b", "side", str(base / "install wt onbranch"))
            expect("install-refuses-onbranch-worktree-hookspath", (_install(onbranch), _stub(onbranch)),
                   (EXIT_ERROR, None))
            # The existing hook is compared byte for byte: a stub with one more trailing newline, and a
            # symbolic link to a file holding the exact stub text, are not taken as installed (2, kept).
            trailing = _fixture("install-trailing-newline")
            expect("install-trailing-first", _install(trailing), EXIT_OK)
            trailing_hook = trailing / ".git" / "hooks" / "pre-commit"
            trailing_hook.write_text(_stub(trailing) + "\n", encoding="utf-8")
            trailing_text = trailing_hook.read_text(encoding="utf-8")
            expect("install-refuses-stub-with-extra-newline",
                   (_install(trailing), trailing_hook.read_text(encoding="utf-8")), (EXIT_ERROR, trailing_text))
            linkstub = _fixture("install-stub-link")
            expect("install-stub-link-first", _install(linkstub), EXIT_OK)
            linkstub_hook = linkstub / ".git" / "hooks" / "pre-commit"
            (linkstub / "STUB.sh").write_text(_stub(linkstub), encoding="utf-8")
            os.chmod(str(linkstub / "STUB.sh"), 0o755)
            os.remove(str(linkstub_hook))
            os.symlink(str(linkstub / "STUB.sh"), str(linkstub_hook))
            expect("install-refuses-stub-symlink", (_install(linkstub), os.path.islink(str(linkstub_hook))),
                   (EXIT_ERROR, True))
            # A stub write that fails partway leaves nothing behind (a zero file-size limit makes the write
            # fail): no pre-commit, no temporary file.
            partial = _fixture("install-partial-write")
            rc = _run(["-c", 'ulimit -f 0 && sh "$0"', str(partial / rel / "install.sh")], base)
            expect("install-partial-write-leaves-nothing",
                   (rc, sorted(n for n in os.listdir(str(partial / ".git" / "hooks"))
                               if n == "pre-commit" or n.startswith(".opf-"))), (EXIT_ERROR, []))
            # A trace2 target the kept global configuration names is overridden: another repository's
            # tracked file is left byte for byte as it was.
            trace_cfg = base / "trace2.gitconfig"
            trace_cfg.write_text("[trace2]\n\teventTarget = " + trace_file + "\n\tnormalTarget = " + trace_file
                                 + "\n\tperfTarget = " + trace_file + "\n", encoding="utf-8")
            cfg_traced = _fixture("install-config-traced")
            expect("install-config-trace2-overridden",
                   (_install(cfg_traced, dict(GIT_CONFIG_GLOBAL=str(trace_cfg))), _stub_ok(cfg_traced),
                    (traced / "TRACED.md").read_text(encoding="utf-8")),
                   (EXIT_OK, True, "traced\n"))
            # The trackable-destination check covers every worktree of the clone, not only the one the
            # installer runs from. A git directory inside the main working tree, installed from a linked
            # worktree, is refused (2) with no stub written there.
            inner_main = _fixture("install-inner-gitdir-main")
            inner_lw = base / "install inner gitdir linked"
            os.rename(str(inner_main / ".git"), str(inner_main / "gd"))
            (inner_main / ".git").write_text("gitdir: gd\n", encoding="utf-8")
            _git(inner_main, home, "worktree", "add", "-q", "-b", "lw", str(inner_lw))
            expect("install-refuses-gitdir-in-main-from-linked-worktree",
                   (_install(inner_lw), (inner_main / "gd" / "hooks" / "pre-commit").exists()), (EXIT_ERROR, False))
            # A git directory inside a linked worktree's top level, installed from the main worktree whose
            # own top level does not hold it, is refused (2) with no stub written there.
            outer_main = _fixture("install-gitdir-in-linked")
            outer_lw = base / "install gitdir in linked wt"
            _git(outer_main, home, "worktree", "add", "-q", "-b", "lw", str(outer_lw))
            os.rename(str(outer_main / ".git"), str(outer_lw / "gd"))
            (outer_main / ".git").write_text("gitdir: " + str(outer_lw / "gd") + "\n", encoding="utf-8")
            outer_entries = os.listdir(str(outer_lw / "gd" / "worktrees"))
            if len(outer_entries) != 1:
                raise OSError("the linked worktree entry of {} could not be found".format(outer_main))
            (outer_lw / ".git").write_text("gitdir: " + str(outer_lw / "gd" / "worktrees" / outer_entries[0]) + "\n",
                                           encoding="utf-8")
            expect("install-refuses-gitdir-in-linked-worktree",
                   (_install(outer_main), (outer_lw / "gd" / "hooks" / "pre-commit").exists()), (EXIT_ERROR, False))
            # A git directory AT the top level (its .git file reads `gitdir: .`, so the common git directory
            # equals the top level) is refused (2) with no stub written there.
            flat = _fixture("install-gitdir-at-top")
            for name in os.listdir(str(flat / ".git")):
                os.rename(str(flat / ".git" / name), str(flat / name))
            os.rmdir(str(flat / ".git"))
            (flat / ".git").write_text("gitdir: .\n", encoding="utf-8")
            expect("install-refuses-gitdir-equals-top",
                   (_install(flat), (flat / "hooks" / "pre-commit").exists()), (EXIT_ERROR, False))
            # A git directory outside every working tree (--separate-git-dir) still installs from the main
            # worktree (0), and the stub lands in that git directory's hooks.
            sep_wt = _fixture("install-separate", commit=False)
            sep_git = base / "install-separate.git"
            os.rename(str(sep_wt / ".git"), str(sep_git))
            (sep_wt / ".git").write_text("gitdir: " + str(sep_git) + "\n", encoding="utf-8")
            _git(sep_wt, home, "add", "-A")
            _git(sep_wt, home, "commit", "-q", "--no-verify", "-m", "seed store")
            expect("install-separate-gitdir-from-main",
                   (_install(sep_wt), (sep_git / "hooks" / "pre-commit").is_file()), (EXIT_OK, True))
            # A worktree registry that cannot be read (it would list as empty) is refused (2), with no stub
            # written, while a linked worktree it holds sets core.hooksPath. Root reads it, so this is an
            # unprivileged-user vector.
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                unreg = _fixture("install-unreadable-registry")
                _git(unreg, home, "config", "extensions.worktreeConfig", "true")
                _git(unreg, home, "worktree", "add", "-q", "-b", "side", str(base / "install unreadable registry wt"))
                _git(base / "install unreadable registry wt", home, "config", "--worktree", "core.hooksPath",
                     str(base / "elsewhere"))
                os.chmod(str(unreg / ".git" / "worktrees"), 0o311)
                try:
                    rc = _install(unreg)
                finally:
                    os.chmod(str(unreg / ".git" / "worktrees"), 0o755)
                expect("install-refuses-unreadable-worktree-registry", (rc, _stub(unreg)), (EXIT_ERROR, None))
            # Something that arrives at the stub path between the last check and ln (a stand-in ln makes it
            # arrive, then runs the real ln): a link to another repository's hooks directory, and a
            # directory. Each is refused (2) and kept; nothing this run wrote is left in either repository.
            real_ln = shutil.which("ln")
            real_mkdir = shutil.which("mkdir")
            if real_ln is None or real_mkdir is None:
                raise OSError("ln or mkdir not found on PATH; the placement race vectors cannot be built")
            race_target = _fixture("install-race-target")
            (race_target / ".git" / "hooks").mkdir(exist_ok=True)
            for kind in ("link", "dir"):
                racer = _fixture("install-race-" + kind)
                (racer / ".git" / "hooks").mkdir(exist_ok=True)
                race_env = _path_without("ln", install_tools, "install-race-" + kind)
                shim = Path(race_env["PATH"]) / "ln"
                arrive = ('"$OPF_QA_REAL_LN" -s -- "$OPF_QA_TARGET" "$last" || exit 1\n' if kind == "link"
                          else '"$OPF_QA_REAL_MKDIR" -- "$last" || exit 1\n')
                shim.write_text("#!/bin/sh\nfor last in \"$@\"; do :; done\n" + arrive
                                + 'exec "$OPF_QA_REAL_LN" "$@"\n', encoding="utf-8")
                os.chmod(str(shim), 0o755)
                race_env.update(OPF_QA_REAL_LN=os.path.abspath(real_ln), OPF_QA_REAL_MKDIR=os.path.abspath(real_mkdir),
                                OPF_QA_TARGET=str(race_target / ".git" / "hooks"))
                race_stub = racer / ".git" / "hooks" / "pre-commit"
                rc = _install(racer, race_env)
                kept = os.path.islink(str(race_stub)) if kind == "link" else (
                    race_stub.is_dir() and not os.path.islink(str(race_stub)) and os.listdir(str(race_stub)) == [])
                expect("install-placement-race-" + kind + "-leaves-nothing",
                       (rc, kept, sorted(n for n in os.listdir(str(racer / ".git" / "hooks")) if n.startswith(".opf-")),
                        [n for n in os.listdir(str(race_target / ".git" / "hooks"))
                         if n.startswith(".opf-") or n == "pre-commit"]),
                       (EXIT_ERROR, True, [], []))
            # A path that names a backslash is printed as it is (printf, not echo, whose operand some shells
            # read for escapes): the refusal names a core.hooksPath of a\tb with its backslash.
            slashed = _fixture("install-backslash")
            _git(slashed, home, "config", "core.hooksPath", "a\\tb")
            slashed_err = base / "install-backslash.err"
            rc = _run(["-c", 'sh "$0" 2> "$1"', str(slashed / rel / "install.sh"), str(slashed_err)], base)
            expect("install-message-keeps-backslash",
                   (rc, "core.hooksPath is set to a\\tb " in slashed_err.read_text(encoding="utf-8", errors="replace")),
                   (EXIT_ERROR, True))
            # A core.worktree a linked worktree reads (its config.worktree under extensions.worktreeConfig)
            # that names a directory holding the common git directory is checked like any top level:
            # installed from the main worktree, it is refused (2) with no stub written there.
            cw_root = base / "install core worktree"
            cw_root.mkdir()
            cw_main = _fixture("install core worktree/main")
            cw_lw = cw_root / "linked"
            _git(cw_main, home, "worktree", "add", "-q", "-b", "lw", str(cw_lw))
            os.rename(str(cw_main / ".git"), str(cw_root / "gd"))
            (cw_main / ".git").write_text("gitdir: " + str(cw_root / "gd") + "\n", encoding="utf-8")
            cw_entries = os.listdir(str(cw_root / "gd" / "worktrees"))
            if len(cw_entries) != 1:
                raise OSError("the linked worktree entry of " + str(cw_main) + " could not be found")
            (cw_lw / ".git").write_text("gitdir: " + str(cw_root / "gd" / "worktrees" / cw_entries[0]) + "\n",
                                        encoding="utf-8")
            _git(cw_main, home, "config", "extensions.worktreeConfig", "true")
            _git(cw_lw, home, "config", "--worktree", "core.worktree", str(cw_root))
            expect("install-refuses-gitdir-in-linked-core-worktree",
                   (_install(cw_main), (cw_root / "gd" / "hooks" / "pre-commit").exists()), (EXIT_ERROR, False))
            # Run from a linked worktree, the main worktree's top level is the one a core.worktree on the
            # common git directory sets: a git directory outside it installs (0), one inside it is refused
            # (2), with no stub written there.
            scw_wt = _fixture("install-separate-core-worktree", commit=False)
            scw_git = base / "install-separate-core-worktree.git"
            os.rename(str(scw_wt / ".git"), str(scw_git))
            (scw_wt / ".git").write_text("gitdir: " + str(scw_git) + "\n", encoding="utf-8")
            _git(scw_wt, home, "add", "-A")
            _git(scw_wt, home, "commit", "-q", "--no-verify", "-m", "seed store")
            _git(scw_wt, home, "config", "core.worktree", str(scw_wt))
            scw_lw = base / "install separate core worktree linked"
            _git(scw_wt, home, "worktree", "add", "-q", "-b", "lw", str(scw_lw))
            expect("install-separate-core-worktree-from-linked",
                   (_install(scw_lw), (scw_git / "hooks" / "pre-commit").is_file()), (EXIT_OK, True))
            icw = _fixture("install-inner-core-worktree")
            os.rename(str(icw / ".git"), str(icw / "gd"))
            (icw / ".git").write_text("gitdir: gd\n", encoding="utf-8")
            _git(icw, home, "config", "core.worktree", str(icw))
            icw_lw = base / "install inner core worktree linked"
            _git(icw, home, "worktree", "add", "-q", "-b", "lw", str(icw_lw))
            expect("install-refuses-gitdir-in-main-core-worktree-from-linked",
                   (_install(icw_lw), (icw / "gd" / "hooks" / "pre-commit").exists()), (EXIT_ERROR, False))
            # The same with the main worktree's .git file removed, so that only the core.worktree on the
            # common git directory names the top level holding it (no .git entry there for the walk to see):
            # run from a linked worktree, it is refused (2) with no stub written there.
            icwo = _fixture("install-inner-core-worktree-only")
            os.rename(str(icwo / ".git"), str(icwo / "gd"))
            (icwo / ".git").write_text("gitdir: gd\n", encoding="utf-8")
            _git(icwo, home, "config", "core.worktree", str(icwo))
            icwo_lw = base / "install inner core worktree only linked"
            _git(icwo, home, "worktree", "add", "-q", "-b", "lw", str(icwo_lw))
            os.unlink(str(icwo / ".git"))
            expect("install-refuses-gitdir-in-main-core-worktree-only-from-linked",
                   (_install(icwo_lw), (icwo / "gd" / "hooks" / "pre-commit").exists()), (EXIT_ERROR, False))
            # An empty core.worktree is refused (2), not taken as unset, with no stub written. Git 2.53 will
            # not run with one, so a stand-in git answers an empty core.worktree for each linked worktree
            # entry (read under extensions.worktreeConfig) and runs the real git otherwise.
            ecw = _fixture("install-empty-core-worktree")
            _git(ecw, home, "worktree", "add", "-q", "-b", "lw", str(base / "install empty core worktree wt"))
            _git(ecw, home, "config", "extensions.worktreeConfig", "true")
            ecw_bin = base / "bin-empty-core-worktree"
            ecw_bin.mkdir()
            (ecw_bin / "git").write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "    --git-dir=*/worktrees/*)\n"
                "        if [ \"$2\" = config ] && [ \"$3\" = --get ] && [ \"$4\" = core.worktree ]; then echo; exit 0; fi ;;\n"
                "esac\n"
                "exec \"$OPF_REAL_GIT\" \"$@\"\n", encoding="utf-8")
            os.chmod(str(ecw_bin / "git"), 0o755)
            ecw_env = dict(PATH=str(ecw_bin) + os.pathsep + _env().get("PATH", os.defpath), OPF_REAL_GIT=git)
            expect("install-refuses-empty-core-worktree", (_install(ecw, ecw_env), _stub(ecw)), (EXIT_ERROR, None))
            # A linked worktree whose registry entry names a top level of / (its gitdir file reads /.git)
            # holds every path, so the install is refused (2) with no stub written.
            rooted = _fixture("install-root-top")
            _git(rooted, home, "worktree", "add", "-q", "-b", "lw", str(base / "install root top wt"))
            rooted_entries = os.listdir(str(rooted / ".git" / "worktrees"))
            if len(rooted_entries) != 1:
                raise OSError("the linked worktree entry of " + str(rooted) + " could not be found")
            (rooted / ".git" / "worktrees" / rooted_entries[0] / "gitdir").write_text("/.git\n", encoding="utf-8")
            expect("install-refuses-root-top-level", (_install(rooted), _stub(rooted)), (EXIT_ERROR, None))
            # A linked worktree AT the hooks directory (its top level equals the hooks directory) is refused
            # (2), and that worktree gains no pre-commit file.
            hookwt = _fixture("install-hooks-worktree")
            shutil.rmtree(str(hookwt / ".git" / "hooks"))
            _git(hookwt, home, "worktree", "add", "-q", "-b", "h", str(hookwt / ".git" / "hooks"))
            expect("install-refuses-hooks-dir-as-worktree",
                   (_install(hookwt), (hookwt / ".git" / "hooks" / "pre-commit").exists()), (EXIT_ERROR, False))
            # A working tree the registry does not name (a .git file written by hand naming a git directory
            # inside another working tree), and a linked worktree moved by hand so that its registry entry
            # names a path it has left, are found by their .git entries: each is refused (2), no stub.
            unreg_a = _fixture("install-unregistered-a")
            os.rename(str(unreg_a / ".git"), str(unreg_a / "gd"))
            (unreg_a / ".git").write_text("gitdir: gd\n", encoding="utf-8")
            unreg_b = base / "install-unregistered-b"
            unreg_b.mkdir()
            (unreg_b / ".git").write_text("gitdir: " + str(unreg_a / "gd") + "\n", encoding="utf-8")
            _git(unreg_b, home, "checkout", "-q", "--", ".")
            expect("install-refuses-gitdir-in-unregistered-worktree",
                   (_run([str(unreg_b / rel / "install.sh")], base), (unreg_a / "gd" / "hooks" / "pre-commit").exists()),
                   (EXIT_ERROR, False))
            moved_main = _fixture("install-moved-linked")
            moved_lw = base / "install moved linked wt"
            moved_to = base / "install moved linked wt2"
            _git(moved_main, home, "worktree", "add", "-q", "-b", "lw", str(moved_lw))
            os.rename(str(moved_main / ".git"), str(moved_lw / "gd"))
            moved_entries = os.listdir(str(moved_lw / "gd" / "worktrees"))
            if len(moved_entries) != 1:
                raise OSError("the linked worktree entry of " + str(moved_main) + " could not be found")
            os.rename(str(moved_lw), str(moved_to))
            (moved_main / ".git").write_text("gitdir: " + str(moved_to / "gd") + "\n", encoding="utf-8")
            (moved_to / ".git").write_text("gitdir: " + str(moved_to / "gd" / "worktrees" / moved_entries[0]) + "\n",
                                           encoding="utf-8")
            expect("install-refuses-gitdir-in-moved-worktree",
                   (_install(moved_main), (moved_to / "gd" / "hooks" / "pre-commit").exists()), (EXIT_ERROR, False))
            # A termination signal during the install (a stand-in chmod runs the real one, then sends TERM
            # to the installer) runs the rollback: 2, no stub and no private directory left.
            real_chmod = shutil.which("chmod")
            if real_chmod is None:
                raise OSError("chmod not found on PATH; the signal vector cannot be built")
            signalled = _fixture("install-signalled")
            sig_env = _path_without("chmod", install_tools, "install-signal")
            sig_shim = Path(sig_env["PATH"]) / "chmod"
            sig_shim.write_text('#!/bin/sh\n"$OPF_QA_REAL_CHMOD" "$@" || exit 1\nkill -TERM "$PPID"\n',
                                encoding="utf-8")
            os.chmod(str(sig_shim), 0o755)
            sig_env.update(OPF_QA_REAL_CHMOD=os.path.abspath(real_chmod))
            rc = _install(signalled, sig_env)
            expect("install-signal-rolls-back",
                   (rc, sorted(n for n in os.listdir(str(signalled / ".git" / "hooks"))
                               if n == "pre-commit" or n.startswith(".opf-"))), (EXIT_ERROR, []))
            # A refusal after rollback names what is at the hooks directory and, while that is a directory, at
            # the stub path and the private directory, by file type. The installer runs from a shell that
            # records its process id and then execs it (so the private directory's name is known); the last
            # line of its error output is the refusal.
            real_rmdir = shutil.which("rmdir")
            real_cmp = shutil.which("cmp")
            if real_rmdir is None or real_cmp is None:
                raise OSError("rmdir or cmp not found on PATH; the signal vectors cannot be built")
            sh_path = shutil.which("sh")
            if sh_path is None:
                raise OSError("sh not found on PATH; the signal vectors cannot be built")

            def _install_seen(root, env, label):
                err = base / (label + ".err")
                pid = base / (label + ".pid")
                rc = _run(["-c", 'printf "%s\\n" "$$" > "$3" && exec "$2" "$0" 2> "$1"', str(root / rel / "install.sh"),
                           str(err), os.path.abspath(sh_path), str(pid)], base, env)
                lines = err.read_text(encoding="utf-8", errors="replace").strip().split("\n")
                return rc, lines, ".opf-pre-commit-install." + pid.read_text(encoding="utf-8").strip()

            def _seen(reason, root, priv, hooks_kind, stub_kind="nothing", priv_kind="nothing"):
                hooks_dir = root / ".git" / "hooks"
                text = "opf-pre-commit install: refused: " + reason + "; after the removal, what is there: "
                text += str(hooks_dir) + ": " + hooks_kind
                if hooks_kind == "a directory":
                    text += "; " + str(hooks_dir / "pre-commit") + ": " + stub_kind
                    text += "; " + str(hooks_dir / priv) + ": " + priv_kind
                return text

            def _opf_names(root):
                hooks_dir = root / ".git" / "hooks"
                if not hooks_dir.is_dir():
                    return None
                return sorted((n, sorted(os.listdir(str(hooks_dir / n))) if (hooks_dir / n).is_dir() else "file")
                              for n in os.listdir(str(hooks_dir)) if n == "pre-commit" or n.startswith(".opf-"))

            def _shimmed(tag, tools):
                env = _path_without(next(iter(tools)), install_tools, tag)
                for tool, (real, body) in tools.items():
                    shim = Path(env["PATH"]) / tool
                    if shim.exists() or shim.is_symlink():
                        os.remove(str(shim))
                    shim.write_text("#!/bin/sh\n" + body, encoding="utf-8")
                    os.chmod(str(shim), 0o755)
                    env["OPF_QA_REAL_" + tool.upper()] = os.path.abspath(real)
                return env

            # A signal that lands just after a write (the stand-in runs the real command, then sends TERM; the
            # installer runs the trap as soon as that command returns). After the ln or the final rmdir it
            # leaves no stub and no private directory and names nothing at either. After a mkdir, the trap
            # runs before the directory is recorded as this run's, so that one empty directory is left and
            # named (the disclosed instant).
            for label, tool, real, when in (("hooks-mkdir", "mkdir", real_mkdir, '[ "$1" = -- ]'),
                                            ("private-mkdir", "mkdir", real_mkdir, '[ "$1" = -m ]'),
                                            ("ln", "ln", real_ln, ":"),
                                            ("rmdir", "rmdir", real_rmdir, ":")):
                after = _fixture("install-signal-after-" + label)
                after_hooks = after / ".git" / "hooks"
                if label == "hooks-mkdir":
                    if after_hooks.exists():
                        shutil.rmtree(str(after_hooks))
                else:
                    after_hooks.mkdir(exist_ok=True)
                after_env = _shimmed("install-signal-after-" + label, {tool: (real, (
                    '"$OPF_QA_REAL_' + tool.upper() + '" "$@" || exit $?\n' + when
                    + ' && kill -TERM "$PPID"\nexit 0\n'))})
                rc, lines, priv = _install_seen(after, after_env, "install-signal-after-" + label)
                outcome = ("names-the-directory" if label.endswith("mkdir") else "leaves-nothing")
                expect("install-signal-after-" + label + "-" + outcome,
                       (rc, _opf_names(after), lines[-1]),
                       (EXIT_ERROR, [(priv, [])] if label == "private-mkdir" else [],
                        _seen("stopped by a signal", after, priv, "a directory",
                              priv_kind="a directory" if label == "private-mkdir" else "nothing")))
            # A mkdir that fails because a directory arrived after the check (the stand-in makes it, then runs
            # the real mkdir) leaves that directory and its contents alone and names it: the hooks directory,
            # and a private directory holding a file at the temporary stub's name.
            for label, when, arrive in (
                    ("hooks", '[ "$1" = -- ]', '"$OPF_QA_REAL_MKDIR" -- "$2"'),
                    ("private", '[ "$1" = -m ]',
                     '"$OPF_QA_REAL_MKDIR" -- "$4" && printf x > "$4/.opf-pre-commit-stub.${4##*.}"')):
                arrived = _fixture("install-mkdir-arrival-" + label)
                arrived_hooks = arrived / ".git" / "hooks"
                if label == "hooks":
                    if arrived_hooks.exists():
                        shutil.rmtree(str(arrived_hooks))
                else:
                    arrived_hooks.mkdir(exist_ok=True)
                arrived_env = _shimmed("install-mkdir-arrival-" + label, {"mkdir": (real_mkdir, (
                    when + " && { " + arrive + ' || exit 1; }\nexec "$OPF_QA_REAL_MKDIR" "$@"\n'))})
                rc, lines, priv = _install_seen(arrived, arrived_env, "install-mkdir-arrival-" + label)
                expect("install-failed-" + label + "-mkdir-keeps-arrived-dir",
                       (rc, _opf_names(arrived), lines[-1]),
                       (EXIT_ERROR, [(priv, [".opf-pre-commit-stub." + priv.rsplit(".", 1)[1]])] if label == "private"
                        else [],
                        _seen("could not create the " + ("hooks directory " + str(arrived_hooks) if label == "hooks"
                                                         else "private directory " + str(arrived_hooks / priv)),
                              arrived, priv, "a directory",
                              priv_kind="a directory" if label == "private" else "nothing")))
            # A signal after the final rmdir, with the stub unreadable to the rollback's comparison once the
            # temporary file is gone (a stand-in cmp fails then): the stub is kept and named as a regular
            # file, not taken as gone.
            unread = _fixture("install-signal-unreadable-stub")
            (unread / ".git" / "hooks").mkdir(exist_ok=True)
            unread_env = _shimmed("install-signal-unreadable-stub", {
                "rmdir": (real_rmdir, '"$OPF_QA_REAL_RMDIR" "$@" || exit $?\nkill -TERM "$PPID"\n'),
                "cmp": (real_cmp, 'case "$4" in\n*/pre-commit)\n'
                        '    for t in "${4%/*}"/.opf-pre-commit-install.*/.opf-pre-commit-stub.*; do\n'
                        '        [ -e "$t" ] && exec "$OPF_QA_REAL_CMP" "$@"\n    done\n    exit 2 ;;\nesac\n'
                        'exec "$OPF_QA_REAL_CMP" "$@"\n')})
            rc, lines, priv = _install_seen(unread, unread_env, "install-signal-unreadable-stub")
            expect("install-signal-unreadable-stub-named",
                   (rc, _opf_names(unread), lines[-1]),
                   (EXIT_ERROR, [("pre-commit", "file")],
                    _seen("stopped by a signal", unread, priv, "a directory", stub_kind="a regular file")))
            # A signal that arrives before the hooks directory's mkdir makes it (the stand-in sends TERM and
            # fails without making it): no hooks directory, and the refusal, alone on the error output, names
            # nothing there.
            before = _fixture("install-signal-before-hooks-mkdir")
            if (before / ".git" / "hooks").exists():
                shutil.rmtree(str(before / ".git" / "hooks"))
            before_env = _shimmed("install-signal-before-hooks-mkdir", {"mkdir": (real_mkdir, (
                '[ "$1" = -- ] && { kill -TERM "$PPID"; exit 1; }\nexec "$OPF_QA_REAL_MKDIR" "$@"\n'))})
            rc, lines, priv = _install_seen(before, before_env, "install-signal-before-hooks-mkdir")
            expect("install-signal-before-hooks-mkdir-names-nothing",
                   (rc, (before / ".git" / "hooks").exists(), lines),
                   (EXIT_ERROR, False, [_seen("stopped by a signal", before, priv, "nothing")]))
            # A private directory an earlier run left at this run's name (the shell that execs the installer
            # makes it, holding a file) is refused and kept with its contents; no stub is written.
            stale = _fixture("install-stale-private-dir")
            stale_hooks = stale / ".git" / "hooks"
            stale_hooks.mkdir(exist_ok=True)
            stale_err = base / "install-stale-private-dir.err"
            rc = _run(["-c", 'd="$3/.opf-pre-commit-install.$$" && "$4" -- "$d" && printf x > "$d/keep" && '
                       'exec "$2" "$0" 2> "$1"', str(stale / rel / "install.sh"), str(stale_err),
                       os.path.abspath(sh_path), str(stale_hooks), os.path.abspath(real_mkdir)], base)
            stale_names = _opf_names(stale)
            expect("install-refuses-stale-private-dir",
                   (rc, [(n, k) for n, k in stale_names] if stale_names else stale_names,
                    "is already there (an earlier run left it; remove it)"
                    in stale_err.read_text(encoding="utf-8", errors="replace")),
                   (EXIT_ERROR, [(n, ["keep"]) for n, k in (stale_names or [])
                                 if n.startswith(".opf-pre-commit-install.")] or ["missing"], True))
            # A hooks directory that cannot be searched when the rollback runs (the stand-in chmod makes it
            # so, then sends TERM) leaves the stub path and the private directory named as not known, not
            # as nothing (unprivileged user only: root searches any directory).
            if hasattr(os, "geteuid") and os.geteuid() != 0:
                blind = _fixture("install-signal-unsearchable-hooks")
                blind_hooks = blind / ".git" / "hooks"
                blind_hooks.mkdir(exist_ok=True)
                blind_env = _shimmed("install-signal-unsearchable-hooks", {"chmod": (real_chmod, (
                    '"$OPF_QA_REAL_CHMOD" "$@" || exit 1\n"$OPF_QA_REAL_CHMOD" 600 "${2%/*/*}" || exit 1\n'
                    'kill -TERM "$PPID"\n'))})
                try:
                    rc, lines, priv = _install_seen(blind, blind_env, "install-signal-unsearchable-hooks")
                finally:
                    os.chmod(str(blind_hooks), 0o755)
                unknown = "not known (the directory " + str(blind_hooks) + " cannot be searched)"
                expect("install-signal-unsearchable-hooks-not-known",
                       (rc, lines[-1]),
                       (EXIT_ERROR, _seen("stopped by a signal", blind, priv, "a directory", stub_kind=unknown,
                                          priv_kind=unknown)))
            # A branch cannot add a hook: a post-checkout and a post-merge committed in the pack directory on
            # another branch do not run on checkout or merge, since no tracked directory is a hooks path.
            branchy = _fixture("branch-hooks")
            expect("branch-hooks-install", _install(branchy), EXIT_OK)
            marker = base / "branch-hook-ran"
            _git(branchy, home, "checkout", "-q", "-b", "evil")
            for name in ("post-checkout", "post-merge"):
                (branchy / rel / name).write_text(
                    "#!/bin/sh\necho " + name + " >> '" + str(marker) + "'\n", encoding="utf-8")
                os.chmod(str(branchy / rel / name), 0o755)
                _git(branchy, home, "add", rel + "/" + name)
            _git(branchy, home, "commit", "-q", "--no-verify", "-m", "branch hooks")
            _git(branchy, home, "checkout", "-q", "main")
            _git(branchy, home, "checkout", "-q", "evil")
            _git(branchy, home, "checkout", "-q", "main")
            _git(branchy, home, "merge", "-q", "--ff-only", "evil")
            expect("branch-added-hooks-not-run", marker.exists(), False)
            # A linked worktree under a path with spaces: the stub lands in the common hooks directory, no
            # configuration is written, and commits in the worktree are checked.
            linked = _fixture("linked repo")
            linked_wt = base / "linked wt"
            _git(linked, home, "worktree", "add", "-q", "-b", "side", str(linked_wt))
            expect("linked-install", (_install(linked_wt), _stub_ok(linked), _hookspath(linked_wt),
                                      (linked / ".git" / "config.worktree").exists()),
                   (EXIT_OK, True, None, False))
            (linked_wt / "README.md").write_text("readme\n", encoding="utf-8")
            _git(linked_wt, home, "add", "README.md")
            expect("linked-commit-clean-passes", _committed(linked_wt), (EXIT_OK, True))
            _tamper(linked_wt)
            _git(linked_wt, home, "add", done_rel)
            expect("linked-commit-tamper-refused", _refused(linked_wt), (True, True, True))
            # --- End to end through git commit, over the installed fixture. A clean staged change commits.
            (inst / "README.md").write_text("readme\n", encoding="utf-8")
            _git(inst, home, "add", "README.md")
            expect("commit-clean-passes", _committed(inst), (EXIT_OK, True))
            # A staged tamper of an immutable record body is refused and HEAD does not move. The same
            # commit in a clone WITHOUT the hook succeeds, so it is the hook that refuses it.
            _tamper(inst)
            _git(inst, home, "add", done_rel)
            expect("commit-staged-tamper-refused", _refused(inst), (True, True, True))
            bare = _fixture("no-hook")
            _tamper(bare)
            _git(bare, home, "add", done_rel)
            expect("commit-staged-tamper-without-hook-passes", _committed(bare), (EXIT_OK, True))
            # The STAGED snapshot is checked, not the working tree: a staged tamper whose working-tree bytes
            # were restored is still refused ...
            _untamper(inst)
            expect("commit-staged-tamper-clean-worktree-refused", _refused(inst), (True, True, True))
            # ... and an unstaged working-tree tamper does not block a clean staged change.
            _git(inst, home, "reset", "-q", "--", done_rel)
            _tamper(inst)
            (inst / "NOTES.md").write_text("notes\n", encoding="utf-8")
            _git(inst, home, "add", "NOTES.md")
            expect("commit-unstaged-tamper-ignored", _committed(inst), (EXIT_OK, True))
            # `git commit PATH` and `git commit -a` commit a temporary index that git names in
            # GIT_INDEX_FILE; the hook checks that index, so both are refused over the working-tree tamper.
            expect("commit-path-temp-index-refused", _refused(inst, "--", done_rel), (True, True, True))
            expect("commit-all-temp-index-refused", _refused(inst, "-a"), (True, True, True))
            # An unborn branch (no commit yet) has an honestly empty history: a clean store commits.
            unborn = _fixture("unborn", commit=False)
            expect("unborn-install", _install(unborn), EXIT_OK)
            _git(unborn, home, "add", "-A")
            expect("commit-unborn-clean-passes", _committed(unborn), (EXIT_OK, True))
            # The snapshot carries the remote: a store whose manifest names a dedicated sync target equal to
            # the clone's remote is VALID, and the same snapshot without that remote is INVALID.
            url = "https://example.invalid/org/store.git"
            synced = clean_machine()
            synced["manifest.toml"] = dict(synced["manifest.toml"], store={"sync_target": url})
            remote = _fixture("remote", machine=synced, remote=url)
            expect("remote-doctor-valid", _run_doctor(remote, capture=True), EXIT_OK)
            expect("remote-install", _install(remote), EXIT_OK)
            (remote / "README.md").write_text("readme\n", encoding="utf-8")
            _git(remote, home, "add", "README.md")
            expect("commit-remote-sync-target-passes", _committed(remote), (EXIT_OK, True))
            # The stub fails closed when the working tree being committed has no pack hook (a branch that
            # predates the pack, say): the commit is refused rather than passing silently.
            nopack = _fixture("stub-no-pack")
            expect("stub-no-pack-install", _install(nopack), EXIT_OK)
            os.remove(str(nopack / rel / "pre-commit"))
            (nopack / "README.md").write_text("readme\n", encoding="utf-8")
            _git(nopack, home, "add", "README.md")
            rc, moved = _committed(nopack)
            expect("stub-missing-pack-hook-refused", (rc != EXIT_OK, moved), (True, False))
            # The disclosed CI residual, held as behaviour and as text: a history violation the hook refuses,
            # committed past it with --no-verify, passes the CI recipe (doctor's prior there is that same
            # commit), and the shipped files say so rather than calling CI the floor for it.
            bypass = _fixture("bypass")
            expect("bypass-install", _install(bypass), EXIT_OK)
            _tamper(bypass)
            _git(bypass, home, "add", done_rel)
            expect("bypass-hook-refuses", _refused(bypass), (True, True, True))
            expect("bypass-no-verify-commits", _committed(bypass, "--no-verify"), (EXIT_OK, True))
            expect("bypass-ci-recipe-misses-history", _run_ci_recipe(bypass), EXIT_OK)
            # The disclosed operations residual, held as behaviour: with the stub installed, git cherry-pick
            # records the same history violation without running any pre-commit hook.
            picked = _fixture("cherry-pick")
            expect("cherry-pick-install", _install(picked), EXIT_OK)
            _git(picked, home, "checkout", "-q", "-b", "side")
            _tamper(picked)
            _git(picked, home, "add", done_rel)
            _git(picked, home, "commit", "-q", "--no-verify", "-m", "tamper")
            _git(picked, home, "checkout", "-q", "main")
            before = _head(picked)
            try:
                proc = subprocess.run([git, "--no-replace-objects", "-C", str(picked),
                                       "-c", "user.email=opf@example.invalid", "-c", "user.name=OPF Self Test",
                                       "-c", "commit.gpgsign=false", "-c", "gc.auto=0",
                                       "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                                       "cherry-pick", "side"],
                                      env=_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      timeout=300)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise OSError("could not run git cherry-pick in {} ({})".format(picked, exc))
            expect("cherry-pick-skips-hook", (proc.returncode, _head(picked) != before), (EXIT_OK, True))
            hook_text = " ".join((pack / "pre-commit").read_text(encoding="utf-8").replace("#", " ").split())
            inst_text = " ".join((pack / "install.sh").read_text(encoding="utf-8").replace("#", " ").split())
            expect("residuals-disclosed", (
                "CI does not catch a history violation committed past this hook" in hook_text,
                "C-HISTORY-RESURRECTION, C-HISTORY-APPEND-ONLY, C-HISTORY-COUNTERS" in hook_text,
                "The hook runs checked-out code" in hook_text,
                "Operations git commits without running any pre-commit hook: cherry-pick, revert, rebase"
                in hook_text,
                "no pre-commit hook for cherry-pick, revert, rebase, am or a merge that commits without stopping"
                in inst_text,
                "so a missing hook is never silent" in hook_text,
                "It never sets core.hooksPath" in inst_text,
                "Requires git 2.32 or later" in hook_text,
                "CI (../ci/opf-ci.sh) does not close these for the history checks" in inst_text,
                "runs this hook before each commit" in hook_text,
                "A missing hook is silent" in hook_text,
                "CI stays the shared floor" in hook_text,
                "is the floor that every change meets" in inst_text,
                "CI is the shared floor" in inst_text,
                "or an includeIf onbranch: for a branch checked out later) is not seen" in inst_text),
                (True, True, True, True, True, True, True, True, True, False, False, False, False, False, True))

            # --- The hook's contract, run by hand under a recording stub tool. ---
            serial = [0]

            def _stubbed(root, rc_doctor=0, rc_render=0, args=(), extra=None):
                serial[0] += 1
                log = base / "stub-log-{}".format(serial[0])
                env = dict(OPF_TOOL=str(stub), OPF_STUB_LOG=str(log),
                           OPF_STUB_RC_DOCTOR=str(rc_doctor), OPF_STUB_RC_RENDER=str(rc_render))
                env.update(extra or dict())
                rc = _run([rel + "/pre-commit"] + list(args), root, env)
                calls = []
                if log.is_file():
                    calls = [line.split(chr(31)) for line in log.read_text(encoding="utf-8").splitlines()]
                flags_log = Path(str(log) + ".flags")
                flags = flags_log.read_text(encoding="utf-8").splitlines() if flags_log.is_file() else []
                return rc, calls, flags

            # Exact order and arguments (doctor --require-store, then render --check, both over ONE snapshot
            # root under TMPDIR), each launched isolated (-I -B), leaving the clone unchanged.
            stubbed = _fixture("stubbed")
            snap_before = _tree_digest(stubbed, home)
            rc, calls, flags = _stubbed(stubbed)
            roots = [c[-1] for c in calls]
            snap_root = Path(roots[0]) if roots else None
            expect("hook-order-and-args", (rc, [c[:-1] for c in calls]),
                   (EXIT_OK, [["doctor", "--require-store", "--root"], ["render", "--check", "--root"]]))
            expect("hook-snapshot-root", (len(set(roots)), snap_root is not None and snap_root.name == "tree"
                                          and snap_root.parent.parent == snaps), (1, True))
            expect("hook-isolated-launch", flags, ["1 1", "1 1"])
            expect("hook-read-only", _tree_digest(stubbed, home) == snap_before, True)
            # Per-step propagation: a doctor finding stops before render; a render error is the exit; an
            # out-of-vocabulary status is normalized to 2; a surplus operand is a usage 2 with no step run.
            rc, calls, _f = _stubbed(stubbed, rc_doctor=1)
            expect("hook-doctor-finding-stops", (rc, [c[0] for c in calls]), (EXIT_FINDING, ["doctor"]))
            rc, calls, _f = _stubbed(stubbed, rc_render=2)
            expect("hook-render-error-propagates", (rc, [c[0] for c in calls]),
                   (EXIT_ERROR, ["doctor", "render"]))
            rc, calls, _f = _stubbed(stubbed, rc_doctor=3)
            expect("hook-abnormal-status-normalized", (rc, [c[0] for c in calls]), (EXIT_ERROR, ["doctor"]))
            rc, calls, _f = _stubbed(stubbed, args=("x",))
            expect("hook-usage", (rc, calls), (EXIT_ERROR, []))
            # Every snapshot is removed on exit.
            expect("hook-snapshots-removed", sorted(os.listdir(str(snaps))), [])
            # Fail closed, each before any step: a git older than the 2.32 floor, and an environment whose
            # GIT_ variable names cannot be read (no sed), so the snapshot is never built still bound to
            # the hook environment.
            rc, calls, _f = _stubbed(stubbed, extra=old_git_env)
            expect("hook-old-git-refused", (rc, calls), (EXIT_ERROR, []))
            rc, calls, _f = _stubbed(stubbed, extra=_path_without("sed"))
            expect("hook-env-enumeration-failure-refused", (rc, calls), (EXIT_ERROR, []))
            # A missing dirname fails closed (2) before any step, rather than resolving to the launching
            # directory.
            rc, calls, _f = _stubbed(stubbed, extra=_path_without("dirname"))
            expect("hook-dirname-missing-refused", (rc, calls), (EXIT_ERROR, []))
            # Inherited git trace destinations are dropped before the first git call: another repository's
            # tracked file they name is left as it was, and both steps run.
            rc, calls, _f = _stubbed(stubbed, extra=trace_env)
            expect("hook-git-trace-scrubbed",
                   (rc, [c[0] for c in calls], (traced / "TRACED.md").read_text(encoding="utf-8")),
                   (EXIT_OK, ["doctor", "render"], "traced\n"))
            # A snapshot removal that fails (no rm) exits 2 after passing steps; the directory it names is
            # then removed here.
            rc, calls, _f = _stubbed(stubbed, extra=_path_without("rm"))
            left = sorted(os.listdir(str(snaps)))
            expect("hook-cleanup-failure-refused", (rc, [c[0] for c in calls], len(left)),
                   (EXIT_ERROR, ["doctor", "render"], 1))
            for name in left:
                shutil.rmtree(str(snaps / name), ignore_errors=True)
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

    rc = max(_classify(_doctor_suite), _classify(_precommit_suite))
    if rc == EXIT_OK:
        print("check_opf_doctor self-test: PASS (opf doctor returns 0 on a clean committed store / 1 after an "
              "immutable-body mutation vs the committed prior / 2 on a broken store / 0 NOT APPLICABLE, end to "
              "end; --require-store -> 0 clean / 2 NOT-ADOPTED; CI recipe -> 0 clean / 2 NOT-ADOPTED, in "
              "the one-operand and the documented no-operand arity (root `.`, the child's working "
              "directory); recipe contract -> exact step order and arguments (doctor --require-store "
              "then render --check) for both arities with stubbed step failures forwarded, per-step "
              "0/1/2 propagation with doctor failures stopping before render, abnormal statuses "
              "(3/126/127/137, a signal death) normalized to 2 for BOTH steps with a doctor abnormality "
              "stopping before render, launch failures -> 2 for BOTH steps (a missing or non-executable "
              "interpreter before doctor, an interpreter vanishing before render), a surplus operand a "
              "usage 2 with no step run, a relative invocation under a hostile CDPATH -> the true "
              "verdict, a committed drifted view -> 1 end to end (render --check, doctor, "
              "recipe), both recipe runs read-only over EXACTLY this snapshot (every entry under the "
              "root with only the top-level .git pruned, nested .git dirs walked, by lstat "
              "kind/mode/content/target; the directory set; the root and .git/hooks directory "
              "records; refs; local git config; the .git/hooks tree; the index via ls-files -s -z; "
              "and .git/info/exclude; HEAD itself, the object store including alternates, reflogs "
              "and other .git metadata stay uncovered), per-write mutant fixtures red on each "
              "covered snapshot leg (ref, local config, hook file, the two index writes, exclude, "
              "nested .git, content edit, symlink plant and retarget, file chmod, root and "
              "hooks-dir chmods, empty mkdir; the directory-set leg is redundancy the entry "
              "records subsume), the sig:15 abnormal vectors repeated with SIGTERM inherited "
              "ignored (the stub's SIG_DFL restore guarded), the workflow template BYTE-GATED "
              "(raw bytes; any byte outside printable ASCII plus newline refused before any "
              "reduction: tab, CR, BOM, any control or non-ASCII byte) then equal by EXACT "
              "TEXT to the canonical constant after dropping only ASCII comment and blank lines "
              "(any other edit reds, per-spelling and per-byte-class mutant fixtures red, "
              "a sweep refuses every outside byte singly); "
              "child-launch failure -> 2; no-repo-root -> 2; invalid-git-marker -> 2; "
              "relative-toplevel probe -> None (exit 2); "
              "git executable absolutized (relative which() -> absolute argv[0]); "
              "status contract 1=assertion 2=harness; pre-commit floor: installer writes a stub in the "
              "untracked hooks directory, sets no core.hooksPath, keeps other hooks, and refuses any "
              "hooksPath (another linked worktree's included), an existing pre-commit hook (compared byte "
              "for byte, never through a symlink), a symlinked hooks destination, a git directory in any "
              "worktree's top level (from a linked worktree, from the main one, and equal to the top level) "
              "while a separate git directory installs, a core.worktree top level (linked and main, from a linked worktree, the main one with no .git entry), an empty core.worktree, a top level of /, a worktree at the hooks directory, an unregistered or moved working tree, a signal rolled back with what is left named by file type (after chmod, ln and the last rmdir nothing; after either mkdir that empty directory; before the hooks mkdir no directory; an unreadable stub named; an unsearchable hooks directory named not known), a directory arriving at either mkdir kept, a stale private directory refused and kept, an unreadable worktree registry, an "
              "unreadable pack hook and an ambient GIT_DIR redirect; no dirname, inherited GIT_TRACE*, a "
              "configured trace2 target, read-back rollback, a partial write left nowhere, a link or "
              "directory arriving at the stub path refused and kept with nothing left, a backslash kept in "
              "a refusal, "
              "branch-added hooks not run, "
              "linked worktree with spaces, stub refuses a missing pack hook, cherry-pick residual held; "
              "git commit over the staged snapshot refuses a staged tamper by "
              "doctor's finding, ignores an unstaged one, checks GIT_INDEX_FILE for commit PATH and -a, "
              "passes an unborn branch and a remote sync target; stubbed hook order, -I -B, read-only, "
              "propagation, normalization, usage and snapshot cleanup; fail closed on an unreadable hooks "
              "directory, an environment-only hooksPath, git below 2.32, an unreadable environment, a "
              "missing dirname and a failed cleanup; the --no-verify history bypass CI misses is disclosed "
              "and held)")
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
