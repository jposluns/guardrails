#!/usr/bin/env python3
"""OPF source-only init gate, exercised exclusively through isolated temporary fixtures.

Both the default entry and --self-test run the fixture suite. There is no live-adopter mutation
leg and no root option. Unlike doctor fixtures, init fixtures need no committed HEAD: initialization
creates sources, leaving staging, committing, and rendering to the adopter.

The dedicated gate launches opf.py isolated (-I -B). The dispatcher self-test reuses _suite with
its own main callable. Fixtures are created beneath a fresh temporary directory and removed afterward.
Missing git, unusable temporary storage, or a harness failure returns 2, never a clean skip.

Exit convention: 0 observed assertions pass; 1 an assertion fails; 2 cannot evaluate the harness.
Coverage is representative: it does not exhaust concurrent namespace changes or filesystem failures.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_opf_init.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import contextlib
import io
import json
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_ERROR = 2

# The first-parent scan limit, worded identically in the init refusal, the successful init output
# and OPF-QUICKSTART.md (QA round 2 MAJOR 1). Asserted literally against whitespace-normalized
# text, so the earlier "unmerged side branch" wording, which missed a store created and deleted
# on a side branch that WAS merged, fails every site check.
_SIDELINE_LIMIT = ("a store that never reached a tree on HEAD's first-parent line (for example "
                   "one created and deleted on a side branch, merged or not) is not detected")


# The current-path limit (QA round 3 MINOR 4), worded identically at the same three sites and in
# the docstring: the pathspecs are the root's PRESENT prefix and a pathspec walk follows no rename.
_PATH_LIMIT = ("the scan checks the root's current path only (a store committed under another "
               "directory, for example before a rename, is not detected)")


def _states_sideline_limit(text):
    flat = " ".join(text.split())
    return _SIDELINE_LIMIT in flat and "unmerged" not in flat


def _states_path_limit(text):
    return _PATH_LIMIT in " ".join(text.split())


def _limit_address_space():
    """preexec_fn for a child that probes a FIFO: a 4 GiB address-space cap (RLIMIT_AS), so a
    regression that reads the path cannot exhaust host memory; the caller's timeout bounds time."""
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (4 << 30, 4 << 30))


def _run_init(argv):
    """Run the real dispatcher isolated; preserve its output for discriminating assertions."""
    script = Path(__file__).resolve().parent / "opf.py"
    proc = subprocess.run(
        [sys.executable, "-I", "-B", str(script)] + list(argv),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
    if proc.returncode not in (EXIT_OK, EXIT_FINDING, EXIT_ERROR):
        raise OSError("opf child returned unexpected status {}".format(proc.returncode))
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


def _snapshot(root):
    """Read the complete small fixture tree, including hidden files, links, and empty directories.

    A .git directory (top-level or nested) is excluded and never descended into: ambient git
    bookkeeping (for example .git/index, which init's read-only git status / ls-files refreshes)
    is not part of the working-tree state the preservation checks assert.
    """
    result = {}

    def walk(directory):
        for path in sorted(directory.iterdir()):
            if path.name == ".git":
                continue
            st = path.lstat()
            relpath = path.relative_to(root).as_posix()
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISDIR(st.st_mode):
                result[relpath] = ("directory", mode)
                walk(path)
            elif stat.S_ISREG(st.st_mode):
                result[relpath] = ("file", mode, path.read_bytes())
            elif stat.S_ISLNK(st.st_mode):
                result[relpath] = ("symlink", os.readlink(path))
            else:
                result[relpath] = ("special", st.st_mode)

    walk(root)
    return result


def _suite(invoke):
    """Isolate fixture configuration and restore the caller even on failure."""
    import tempfile
    from unittest.mock import patch
    with tempfile.TemporaryDirectory(prefix="opf-selftest-home-") as home:
        with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                        GIT_CONFIG_NOSYSTEM="1"):
            return _suite_isolated(invoke)


def _suite_isolated(invoke):
    """Run parser, publication, refusal, and preservation vectors against fresh fixtures."""
    try:
        import tomllib
        from pathlib import PureWindowsPath
        import _opf_check
        import _opf_observe
        import _opf_release
        import _opf_schema
        import _opf_store
        import _opf_views

        failures = []
        checked = []

        def check(label, condition):
            checked.append(label)
            if not condition:
                failures.append(label)

        def call(argv):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                rc = invoke(list(argv))
            return rc, output.getvalue()

        def run(root):
            return call(["init", "--root", str(root)])

        def good(result):
            return result.status == _opf_store.VALID and not result.findings

        working = _opf_store.WORKING_DIRNAME
        machine_name = _opf_store.DEFAULT_MACHINE_SUBDIR
        # Independent roster authority: the baseline type registry, excluding the worklog ledger.
        index_types = sorted(set(_opf_store.BASELINE_TYPES) - {"worklog"})
        source_names = {
            _opf_store.MANIFEST_NAME, _opf_check.COUNTERS_NAME,
            _opf_check.VERSION_NAME, _opf_check.WORKLOG_NAME,
        } | {name + _opf_check.INDEX_SUFFIX for name in index_types}

        def valid_sources(root):
            machine = root / working / machine_name
            try:
                if set(path.name for path in (root / working).iterdir()) != {machine_name}:
                    return False
                if set(path.name for path in machine.iterdir()) != source_names:
                    return False
                if any(not stat.S_ISREG((machine / name).lstat().st_mode) for name in source_names):
                    return False
                docs = {
                    name: tomllib.loads((machine / name).read_text(encoding="utf-8"))
                    for name in source_names
                }
                if not good(_opf_store.validate_manifest(docs[_opf_store.MANIFEST_NAME])):
                    return False
                # Spec 8.1: a fresh store carries no legacy_fragment (LF) counter, so the
                # baseline roster is exact and any importer namespace is a finding.
                namespaces = frozenset(_opf_store.BASELINE_TYPES.values())
                high, findings = _opf_schema.validate_counters(
                    docs[_opf_check.COUNTERS_NAME], known_namespaces=namespaces)
                if findings or high != {namespace: 0 for namespace in namespaces}:
                    return False
                if not good(_opf_release.validate_version(docs[_opf_check.VERSION_NAME])):
                    return False
                if not good(_opf_release.validate_worklog(docs[_opf_check.WORKLOG_NAME])):
                    return False
                root_fd = _opf_store._open_dir_nofollow(root)
                try:
                    for name in index_types:
                        filename = name + _opf_check.INDEX_SUFFIX
                        if docs[filename] != {"schema": _opf_schema.SUPPORTED_SCHEMA, "record": []}:
                            return False
                        _raw, records = _opf_views._load_records(
                            root_fd, working + "/" + machine_name + "/" + filename,
                            name, set(), set())
                        if records:
                            return False
                finally:
                    os.close(root_fd)
                pointer = tomllib.loads(
                    (root / _opf_store.POINTER_REL).read_text(encoding="utf-8"))
                return pointer == {"store": {"target": "dir:."}}
            except (FileNotFoundError, ValueError, UnicodeError, _opf_views.ViewsError):
                return False

        git = _opf_observe._git_path()
        if git is None:
            raise OSError("git not found on PATH")

        def git_call(root, args):
            # Fixture SETUP reproduces the adopter's REAL repository, so it permits lazy fetch
            # (allow_lazy_fetch=True): a partial-clone checkout, or a real `git add` that fetches an absent
            # blob through the promisor, is legitimate fixture behaviour. Production OBSERVATIONS keep
            # _run_git's default (lazy fetch suppressed), which the object-reading probes under test exercise
            # directly (e.g. _opf_observe._show_toml / _run_git below, without this flag).
            # F-367: config_overrides (git's environment-config mechanism, command-scope
            # precedence) pins automatic maintenance OFF, so no DETACHED auto-gc can outlive a
            # fixture commit and churn .git while a ground-truth copytree or a later read
            # traverses it.
            result = _opf_observe._run_git(
                git, root, args, allow_lazy_fetch=True,
                config_overrides=[("gc.auto", "0"), ("gc.autoDetach", "false"),
                                  ("maintenance.auto", "false")])
            if not result.completed or result.rc != 0:
                raise OSError("fixture git failed at {!r}: {}".format(str(root), result.err))
            return result.out

        def git_input(root, args, data):
            # A stdin-fed fixture git call (git update-index --index-info reads the index entry from
            # stdin). Runs under _opf_observe._scrubbed_env (PATH and the
            # isolated HOME carried over, global/system config neutralized, and EVERY ambient GIT_* variable
            # dropped), so an inherited GIT_INDEX_FILE / GIT_DIR / GIT_WORK_TREE / GIT_OBJECT_DIRECTORY /
            # GIT_COMMON_DIR cannot redirect this write to a caller's external index or repository; it stays
            # hermetic like the other fixture calls, which all route through the same scrub (test-hermeticity).
            proc = subprocess.run([git, "-C", str(root)] + list(args), input=data,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=_opf_observe._scrubbed_env())
            if proc.returncode != 0:
                raise OSError("fixture git (stdin) failed at {!r}: {}".format(
                    str(root), proc.stderr.decode("utf-8", "replace")))
            return proc.stdout

        with tempfile.TemporaryDirectory(prefix="opf-init-gate-") as temporary:
            base = Path(temporary).resolve()
            outside = _opf_observe._run_git(git, base, ["rev-parse", "--show-toplevel"])
            if not outside.completed or not _opf_observe._is_no_repo(outside):
                raise OSError("temporary fixture parent is not a confirmed non-git location")

            def make_git(name, bare=False):
                root = base / name
                root.mkdir()
                args = ["-c", "init.templateDir=", "-c", "init.defaultBranch=main", "init"]
                if bare:
                    args.append("--bare")
                git_call(root, args)
                return root

            # Isolate git's default ignore discovery ($XDG_CONFIG_HOME/git/ignore, or ~/.config/git/ignore
            # when XDG is unset) from the caller's real HOME: a host personal git-ignore matching .working/,
            # .opf.toml, or CHANGELOG.md would otherwise spuriously fail the positive vectors. HOME reaches
            # every fixture git call and the in-process init via _opf_observe._scrubbed_env, and the
            # subprocess init via inherited os.environ; the saved values are restored in the finally below.
            home = base / "home"
            home.mkdir()
            saved_env = dict(os.environ)
            # Even a broken parser that ignores --root defaults into this isolated, non-git directory.
            # Restore the caller's cwd before TemporaryDirectory removes the fixture.
            saved_cwd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
            try:
                # Use the OPF-local allowlist before mutating os.environ, retaining PATH.
                # The subprocess init must not inherit an unenumerated GIT_* selector.
                fixture_env = dict(_opf_observe._scrubbed_env(), HOME=str(home))
                os.environ.clear()
                os.environ.update(fixture_env)
                os.chdir(base)
                parser_root = base / "parser"
                parser_root.mkdir()
                parser_before = _snapshot(parser_root)
                parser_vectors = (
                    (["--bogus"], "unrecognized argument"),
                    (["--root"], "--root requires a directory argument"),
                    (["--root", ""], "non-empty directory argument"),
                    (["--root", "--bogus"], "non-empty directory argument"),
                    (["--root", str(parser_root), "--root", str(parser_root)],
                     "--root given more than once"),
                )
                for rest, reason in parser_vectors:
                    rc, output = call(["init"] + rest)
                    check("parser " + repr(rest), rc == EXIT_ERROR and reason in output)
                check("parser fixture preserved", _snapshot(parser_root) == parser_before)

                clean = make_git("clean")
                git_before = _snapshot(clean / ".git")
                rc, output = run(clean)
                check("empty git root succeeds", rc == EXIT_OK)
                check("created sources parse and validate", valid_sources(clean))
                check("completion is source-only",
                      "SOURCES created" in output and "NOT yet git-tracked" in output
                      and "git " in output and "Commit" in output
                      and "opf render --write" in output)
                check("no git mutation", _snapshot(clean / ".git") == git_before)
                check("no root VERSION", not (clean / "VERSION").exists())
                check("heading-only changelog",
                      (clean / "CHANGELOG.md").is_file()
                      and (clean / "CHANGELOG.md").read_bytes() == b"# Changelog\n")
                created = [
                    json.loads(line) for line in output.splitlines() if line.startswith("{")
                ]
                expected_paths = {
                    working + "/" + machine_name + "/" + name for name in source_names
                } | {_opf_store.POINTER_REL, "CHANGELOG.md"}
                check("completion enumerates created paths", any(
                    row.get("event") == "created"
                    and row.get("root") == str(clean)
                    and set(row.get("paths", [])) == expected_paths
                    for row in created))

                before = _snapshot(clean)
                rc, output = run(clean)
                check("second init names existing state",
                      rc == EXIT_ERROR and "existing pointer" in output
                      and _opf_store.POINTER_REL in output)
                check("second init preserves source", _snapshot(clean) == before)

                foreign = make_git("foreign")
                (foreign / working / ".empty" / "nested").mkdir(parents=True)
                (foreign / working / ".hidden").write_bytes(b"foreign\n")
                victim = base / "victim"
                victim.write_bytes(b"untouched\n")
                (foreign / working / "link").symlink_to(victim)
                before = _snapshot(foreign)
                rc, output = run(foreign)
                reports = [
                    json.loads(line) for line in output.splitlines() if line.startswith("{")
                ]
                expected_inventory = {
                    (working + "/.empty", "directory"),
                    (working + "/.empty/nested", "directory"),
                    (working + "/.hidden", "file"),
                    (working + "/link", "symlink"),
                }
                check("foreign content is refused with reason",
                      rc == EXIT_ERROR and "foreign .working content" in output)
                check("foreign inventory is surfaced", any(
                    row.get("event") == "foreign-content"
                    and row.get("root") == str(foreign)
                    and row.get("complete") is True
                    and {(entry["path"], entry["kind"]) for entry in row.get("entries", [])}
                    == expected_inventory for row in reports))
                check("foreign tree preserved", _snapshot(foreign) == before)
                check("foreign link target preserved", victim.read_bytes() == b"untouched\n")

                nongit = base / "non-git"
                nongit.mkdir()
                before = _snapshot(nongit)
                rc, output = run(nongit)
                check("non-git refused with reason", rc == EXIT_ERROR and "git preflight" in output)
                check("non-git untouched", _snapshot(nongit) == before)

                bare = make_git("bare", bare=True)
                before = _snapshot(bare)
                rc, output = run(bare)
                check("bare git refused with reason", rc == EXIT_ERROR and "git preflight" in output)
                check("bare git untouched", _snapshot(bare) == before)

                for number, pointer in enumerate(
                        (_opf_store.POINTER_REL, _opf_store.LOCAL_POINTER_REL)):
                    target = make_git("pointer-" + str(number))
                    (target / pointer).write_bytes(b"malformed {{{\n")
                    before = _snapshot(target)
                    rc, output = run(target)
                    check(pointer + " refused",
                          rc == EXIT_ERROR and "existing pointer" in output and pointer in output)
                    check(pointer + " preserved", _snapshot(target) == before)

                empty_working = make_git("empty-working")
                (empty_working / working).mkdir()
                before = _snapshot(empty_working)
                rc, output = run(empty_working)
                check("empty partial store refused",
                      rc == EXIT_ERROR and "store resolution refused" in output)
                check("empty partial store preserved", _snapshot(empty_working) == before)

                changelog = make_git("existing-changelog")
                (changelog / "CHANGELOG.md").write_bytes(b"Existing changelog.\n")
                rc, _output = run(changelog)
                check("existing changelog permits source initialization",
                      rc == EXIT_OK and valid_sources(changelog))
                check("existing changelog preserved",
                      (changelog / "CHANGELOG.md").read_bytes() == b"Existing changelog.\n")

                # Symlinked root, containment-isolated: the symlink target is a REAL directory INSIDE
                # the same git repo, and --root names the sibling symlink. _init_repo's repo-containment
                # check therefore passes (git reports the enclosing repo, which lexically contains the
                # symlink path), leaving _open_dir_nofollow's O_NOFOLLOW on the final component as the
                # ONLY guard that can refuse. A fresh separate repo as target would instead be caught by
                # the containment check, so the no-follow protection would stay unexercised.
                symlink_repo = make_git("symlink-repo")
                symlink_target = symlink_repo / "realdir"
                symlink_target.mkdir()
                (symlink_target / "keep").write_bytes(b"target\n")
                linked = symlink_repo / "linkdir"
                linked.symlink_to(symlink_target, target_is_directory=True)
                before = _snapshot(symlink_target)
                rc, output = run(linked)
                check("symlink root refused",
                      rc == EXIT_ERROR and "preflight refused" in output)
                check("symlink root target preserved",
                      _snapshot(symlink_target) == before)

                # A destination tracked in the index but absent from the working tree (the deleted-copy
                # case _init_untracked exists for): commit it, then remove the working copy. With the
                # file left present the earlier existence/resolution checks would fire first, so removing
                # the working copy routes the refusal through _init_untracked's index read; init must
                # refuse rather than publish over a tracked-but-deleted path.
                tracked = make_git("tracked-dest")
                tracked_machine = tracked / working / machine_name
                tracked_machine.mkdir(parents=True)
                (tracked_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(tracked, ["--literal-pathspecs", "add", "--",
                                   working + "/" + machine_name + "/" + _opf_store.MANIFEST_NAME])
                git_call(tracked, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "seed"])
                shutil.rmtree(tracked / working)
                before = _snapshot(tracked)
                rc, output = run(tracked)
                check("tracked destination refused",
                      rc == EXIT_ERROR and "planned destination already git-tracked" in output)
                check("tracked destination preserved", _snapshot(tracked) == before)

                # A planned destination under a .gitignore rule: init must refuse, because an ignored
                # store cannot be staged (git add silently no-ops on an ignored path) or discovered.
                ignored = make_git("ignored-target")
                (ignored / ".gitignore").write_bytes(working.encode("ascii") + b"/\n")
                git_call(ignored, ["--literal-pathspecs", "add", "--", ".gitignore"])
                git_call(ignored, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "seed"])
                before = _snapshot(ignored)
                rc, output = run(ignored)
                check("ignored destination refused",
                      rc == EXIT_ERROR and "git-ignored" in output)
                check("ignored destination preserved", _snapshot(ignored) == before)

                # Local pointer gitignored -> SUCCESS: the machine-local pointer is a path init NEVER
                # creates and adopters legitimately gitignore, so an ignore rule naming only it must not
                # block init. This proves the ignore check is scoped to the CREATED destinations; it
                # FAILS if the check is passed index_paths (which carry the local pointer).
                local_ignored = make_git("local-pointer-ignored")
                (local_ignored / ".gitignore").write_bytes(
                    _opf_store.LOCAL_POINTER_REL.encode("ascii") + b"\n")
                git_call(local_ignored, ["--literal-pathspecs", "add", "--", ".gitignore"])
                git_call(local_ignored, ["-c", "user.email=t@t", "-c", "user.name=t",
                                         "commit", "-m", "seed"])
                rc, output = run(local_ignored)
                check("local-pointer-ignored init succeeds",
                      rc == EXIT_OK and valid_sources(local_ignored))

                # Magic-named ignored root -> REFUSE: a --root whose repo-relative prefix begins with a
                # pathspec-magic sigil (a leading colon). A repo-local rule ignores that subdir's store
                # tree, so init must refuse. Without the literal "./" prefix in _init_unignored the colon
                # is consumed as an empty magic signature, the check bypasses (rc 1), and init would
                # wrongly succeed; this vector discriminates the ./ neutralization.
                magic = make_git("magic-root")
                magic_sub = magic / ":magic"
                magic_sub.mkdir()
                (magic_sub / "keep").write_bytes(b"target\n")
                (magic / ".gitignore").write_bytes(b"/:magic/" + working.encode("ascii") + b"\n")
                git_call(magic, ["--literal-pathspecs", "add", "--", ".gitignore"])
                git_call(magic, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "seed"])
                before = _snapshot(magic_sub)
                rc, output = run(magic_sub)
                check("magic-named ignored root refused",
                      rc == EXIT_ERROR and "git-ignored" in output)
                check("magic-named ignored root preserved", _snapshot(magic_sub) == before)

                # OPF-D2B: a destination ignored ONLY by the adopter's GLOBAL core.excludesFile (no repo-local
                # rule) must be refused. The pre-D2B check neutralized the global config, so init wrongly
                # succeeded; the config-discovery probe now reads the adopter's ~/.gitconfig through HOME.
                # DISCRIMINATOR: this fails (init succeeds, no "git-ignored") against the pre-D2B check. The
                # global config is written into the isolated HOME only for THIS vector and removed after, so
                # it cannot ignore .working/ for the positive vectors that follow.
                global_ignored = make_git("global-excludes-target")
                gexcludes = base / "d2b-global-excludes"
                gexcludes.write_bytes(working.encode("ascii") + b"/\n")
                gitconfig = home / ".gitconfig"
                gitconfig.write_bytes(
                    b"[core]\n\texcludesFile = " + str(gexcludes).encode("ascii") + b"\n")
                gi_before = _snapshot(global_ignored)
                try:
                    rc, output = run(global_ignored)
                finally:
                    gitconfig.unlink()
                check("global-excludesFile ignored destination refused",
                      rc == EXIT_ERROR and "git-ignored" in output)
                check("global-excludesFile ignored destination preserved",
                      _snapshot(global_ignored) == gi_before)

                # OPF-D2B: a config that would make the read-only probe WRITE a trace file (trace2.eventTarget
                # in the adopter's ~/.gitconfig) must produce NO write: the probe forces GIT_TRACE2* off in
                # its environment (a command-line -c cannot, since trace2 reads its config before -c). Init
                # proceeds normally and the trace target is never created. DISCRIMINATOR for the trace-off fix.
                trace_target = make_git("trace2-target")
                tracefile = base / "d2b-trace2-out.json"
                tconfig = home / ".gitconfig"
                tconfig.write_bytes(
                    b"[trace2]\n\teventTarget = " + str(tracefile).encode("ascii") + b"\n")
                try:
                    rc, output = run(trace_target)
                finally:
                    tconfig.unlink()
                check("trace2 config does not make the probe write a file", not tracefile.exists())
                check("trace2 config does not break a clean init",
                      rc == EXIT_OK and valid_sources(trace_target))

                # Defence in depth: after a successful init, the staged-and-committed store resolves
                # end to end, at the standard machine subdir. resolve_store reads the working tree, so
                # the commit is not required for resolution; it is included to exercise the realistic
                # adopter flow (init -> stage -> commit -> resolvable).
                resolvable = make_git("resolve-store")
                rc, output = run(resolvable)
                check("resolve-store init succeeds", rc == EXIT_OK)
                git_call(resolvable, ["--literal-pathspecs", "add", "-A"])
                git_call(resolvable, ["-c", "user.email=t@t", "-c", "user.name=t",
                                      "commit", "-m", "init"])
                resolution = _opf_store.resolve_store(resolvable)
                check("resolve-store resolves committed store",
                      resolution.status == _opf_store.RESOLVED
                      and resolution.machine_rel == working + "/" + machine_name)

                # QA round 3 (MEDIUM, MINOR 2): every ancestry refusal vector compares the
                # COMPLETE output with text built here, from the commit ids and paths this test
                # computes itself, never by substring. The builders spell out each diagnostic
                # in full, so any change to the refusal wording, its escaping (ascii, not repr),
                # its remedy command (shlex quoting, --literal-pathspecs, the restored paths) or
                # its stated limits fails every vector that reaches it.
                history_stage = "checking git history for a prior store"

                def inert_escape(ch):
                    code = ord(ch)
                    if code < 0x100:
                        return "\\x%02x" % code
                    if code < 0x10000:
                        return "\\u%04x" % code
                    return "\\U%08x" % code

                def inert_path(path):
                    # An independent spelling of the inert escape: every character outside
                    # the fixed safe set becomes a Python \x / \u / \U escape, so "$", "`",
                    # every quote and every space never reach a printed detail line raw.
                    safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                               "0123456789./-_+,:@%=")
                    return "".join(ch if ch in safe else inert_escape(ch) for ch in path)

                def inert_prose(text):
                    # The prose form admits the space and parentheses only, so sentences
                    # stay readable; quotes, "$", "`", the backslash, ";" and every control
                    # character are escaped, so no printed JSON string or exception text can
                    # carry a live substitution under a shell's double-quote reading (QA
                    # round 5).
                    safe = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                               "0123456789./-_+,:@%= ()")
                    return "".join(ch if ch in safe else inert_escape(ch) for ch in text)

                def restore_command(repo, commit, paths):
                    return ("  git -C " + shlex.quote(str(repo))
                            + " --no-replace-objects --literal-pathspecs checkout "
                            + commit + " -- "
                            + " ".join(shlex.quote(path) for path in paths))

                def prior_store_output(root, repo, commit, restored, shell_ready,
                                       extra=None, gaps=None):
                    rel = os.path.relpath(str(root), str(repo)).replace(os.sep, "/")
                    rel = "" if rel == "." else rel + "/"
                    paths = [rel + name for name in restored]
                    extra_commit = extra[0] if extra else None
                    extra_paths = [rel + name for name in extra[1]] if extra else []
                    gap_paths = [rel + name for name in gaps] if gaps else []
                    if shell_ready:
                        if extra:
                            remedy = ("restore the store paths the restore commands below "
                                      "name, from the commits they name (this history "
                                      "deleted the store across more than one commit, so "
                                      "one command cannot restore every store path)")
                            label = ("shell-ready; it restores only the store paths its "
                                     "commit holds, and the additional command below "
                                     "restores the other named store path as its own "
                                     "commit's tree holds it")
                        else:
                            remedy = ("restore the store paths the restore command below "
                                      "names, from that commit")
                            label = ("shell-ready; it restores the named store paths as "
                                     "that commit's tree holds them")
                        restore = ["opf init: restore command (" + label + "):",
                                   restore_command(repo, commit, paths)]
                        if extra:
                            restore += [
                                "opf init: additional restore command (shell-ready; it "
                                "restores the remaining store path from the newest "
                                "first-parent tree that holds it):",
                                restore_command(repo, extra_commit, extra_paths)]
                    else:
                        remedy = ("restore the store paths the lines below name, from the "
                                  + ("commits" if extra else "commit")
                                  + " named below (no command is printed: a path in it "
                                  "would hold a non-printable character)")
                        restore = (
                            ["opf init: restore command withheld: a path in it holds a "
                             "non-printable character, and an escaped command could still "
                             "carry a live shell substitution, so no command is printed. "
                             "Restore each path named below from the commit named above it, "
                             "in the repository named below, by your own means, reading each "
                             "escaped path as a Python string literal in which every "
                             "character outside A-Za-z0-9 . / - _ + , : @ % = is written as "
                             "an escape:",
                             "opf init:   repository: " + inert_path(str(repo)),
                             "opf init:   commit: " + commit]
                            + ["opf init:   restore path: " + inert_path(path)
                               for path in paths])
                        if extra:
                            restore += (
                                ["opf init:   additional commit: " + extra_commit]
                                + ["opf init:   additional restore path: " + inert_path(path)
                                   for path in extra_paths])
                    if gaps:
                        remedy += (", and the restore is PARTIAL (the missing-store-path "
                                   "lines below name what no named commit's tree holds)")
                        restore += (
                            ["opf init: the restore is PARTIAL: each store path below was "
                             "deleted in an earlier commit, so no commit named above holds "
                             "it and no command or path named above brings it back; restore "
                             "each from an older first-parent commit by your own means, or "
                             "re-adopt with opf adopt:"]
                            + ["opf init:   missing store path: " + inert_path(path)
                               for path in gap_paths])
                    return (
                        "opf init: REFUSED at " + inert_path(str(root)) + " during "
                        + history_stage
                        + ": a prior store exists in this repository's git history: commit "
                        + commit + " on HEAD's first-parent line holds "
                        + inert_path(rel + ".opf.toml") + " or a store manifest "
                        + inert_path(rel + ".working/<subdir>/manifest.toml")
                        + " (spec 4.3, 4.5), and spec 8.2 forbids restarting its counters at "
                        "zero (record ids would be reissued). Remedy: " + remedy
                        + ", or re-adopt the ancestry "
                        "with opf adopt. Plain opf init refuses whenever this scan of "
                        "HEAD's first-parent line finds a prior store, and " + _SIDELINE_LIMIT
                        + " (adoption across such side lines is opf adopt's authority); "
                        + _PATH_LIMIT + "; exit 2\n"
                        + "\n".join(restore) + "\n"
                        "opf init: preflight refused; no publication attempted.\n")

                def cannot_evaluate_output(root, message, kind="RuntimeError"):
                    # The exception text is prose-inert-escaped at the print (QA round 5),
                    # and the exception TYPE is printed by name around the escaped text.
                    return ("opf init: cannot evaluate at " + inert_path(str(root))
                            + " during " + history_stage + ": " + kind + "("
                            + inert_prose(message) + "); exit 2\n"
                            "opf init: preflight refused; no publication attempted.\n")

                def grafts_message(found, grafts_path):
                    return ("cannot evaluate prior-store ancestry: found " + found
                            + " at the legacy grafts path " + ascii(grafts_path)
                            + "; git consults that path in every history walk, where grafts it "
                            "can read may rewrite parent links (--no-replace-objects does not "
                            "neutralize them), and this scan never interprets grafts, so a prior "
                            "store, whose counters spec 8.2 forbids restarting, cannot be ruled "
                            "out; convert or remove it (git replace --convert-graft-file turns a "
                            "grafts file into replace refs, which this scan already ignores) and "
                            "retry")

                def head_of(repo):
                    return git_call(repo, ["rev-parse", "HEAD"]).decode("ascii").strip()

                def store_contents(root):
                    # Bytes and link targets only: git checkout writes modes from the umask,
                    # which init's own create modes need not match.
                    return dict((path, entry[-1]) for path, entry in _snapshot(root).items()
                                if entry[0] != "directory")

                if shutil.which("sh") is None:
                    raise OSError("no POSIX sh on PATH to run the printed restore command")

                def run_restore(output):
                    # Runs every printed restore line through a real POSIX shell, in the
                    # printed order, from a directory outside the repository (each command
                    # carries its own -C); None when no command line was printed at all, 0
                    # only when every printed command succeeded.
                    lines = [line for line in output.splitlines() if line.startswith("  git -C ")]
                    if not lines:
                        return None
                    for line in lines:
                        proc = subprocess.run(["sh", "-c", line.strip()], cwd=str(base),
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              timeout=120)
                        if proc.returncode != 0:
                            return proc.returncode
                    return 0

                def sh_inert_probe(output, tag):
                    # QA round 5 (requirement A): EVERY line init prints must be shell-inert.
                    # Runs the WHOLE output, then each line alone, through the real POSIX sh
                    # from a fresh EMPTY directory, and returns the sorted union of entries
                    # created there across both runs (expected: none). The fixed prose may
                    # make sh print not-found or syntax errors; those run nothing. What must
                    # never happen is a name-derived substitution running (the PWNED
                    # sentinel) or a redirect creating an entry. Shell-ready "  git -C ..."
                    # lines execute against the fixture their -C names, by design; they
                    # create nothing in the probe directory.
                    probe = base / ("inert-probe-" + tag)
                    probe.mkdir()
                    subprocess.run(["sh"], input=output.encode("utf-8"), cwd=str(probe),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   timeout=120)
                    created = {entry.name for entry in probe.iterdir()}
                    for line in output.splitlines():
                        subprocess.run(["sh", "-c", line], cwd=str(probe),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       timeout=120)
                    return sorted(created | {entry.name for entry in probe.iterdir()})

                # SPEC 8.2 ancestry (fix-init-ancestry): plain init must never restart counters
                # over a store git HISTORY shows existed. Reproduction: init, commit the store,
                # raise a counter high-water and commit, `git rm -r .working .opf.toml` and
                # commit, then init again. The unfixed init passed (rc 0) and re-zeroed every
                # namespace, so record ids could be reissued; it must refuse (exit 2), NAME the
                # newest first-parent commit whose tree held the store (the counter-bump commit,
                # not the deletion commit), and give the remedy (restore, or `opf adopt`).
                # DISCRIMINATOR: rc 0 against the pre-fix init.
                ancestry = make_git("ancestry-prior-store")
                rc, output = run(ancestry)
                check("ancestry fixture first init succeeds", rc == EXIT_OK)
                git_call(ancestry, ["--literal-pathspecs", "add", "-A"])
                git_call(ancestry, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "store"])
                counters_path = ancestry / working / machine_name / _opf_check.COUNTERS_NAME
                seeded = counters_path.read_text(encoding="utf-8").replace("BI = 0", "BI = 7")
                if "BI = 7" not in seeded:
                    raise OSError("ancestry fixture: counters.toml carried no BI = 0 to raise")
                counters_path.write_text(seeded, encoding="utf-8")
                git_call(ancestry, ["--literal-pathspecs", "add", "-A"])
                git_call(ancestry, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "BI high-water 7"])
                held_commit = git_call(ancestry, ["rev-parse", "HEAD"]).decode("ascii").strip()
                git_call(ancestry, ["rm", "-r", "-q", "--",
                                    working, _opf_store.POINTER_REL])
                git_call(ancestry, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop store"])
                before = _snapshot(ancestry)
                rc, output = run(ancestry)
                check("ancestry: re-init over a deleted committed store refused",
                      rc == EXIT_ERROR and "prior store" in output)
                check("ancestry: refusal names the newest holding commit", held_commit in output)
                check("ancestry: refusal gives the adopt remedy", "opf adopt" in output)
                # QA round 1 MINOR: a DEFINITE prior-store finding is reported as REFUSED in
                # its own words, never under the generic cannot-evaluate prefix (exit stays 2).
                check("ancestry: definite finding reported as REFUSED, not cannot-evaluate",
                      "opf init: REFUSED" in output and "cannot evaluate" not in output)
                # QA round 2 MAJOR 1: the refusal states the first-parent limit exactly
                # (merged or not). DISCRIMINATOR: mutant M-refusal-unmerged (the round-1
                # "only ever lived on an unmerged side branch" refusal wording) fails this.
                check("ancestry: refusal states the exact first-parent limit",
                      _states_sideline_limit(output))
                check("ancestry: refusal states the current-path limit", _states_path_limit(output))
                # QA round 3 MEDIUM: the COMPLETE refusal, built independently. DISCRIMINATORS:
                # M-remedy-allows ("forbids restarting its counters at zero" reworded),
                # M-remedy-drop-path (a restored path dropped from the command),
                # M-no-literal-pathspecs and an appended raw U+202E all fail (M-repr-for-ascii
                # cannot fail here, where every path is ASCII: the cafe vector below kills it).
                check("ancestry: the complete refusal text, built independently",
                      output == prior_store_output(ancestry, ancestry, held_commit,
                                                   [".opf.toml", ".working"], True))
                check("ancestry: refusing init wrote nothing", _snapshot(ancestry) == before)

                # SPEC 8.2 ancestry, fail closed: a SHALLOW clone truncates the first-parent
                # history, so it cannot prove no prior store existed -- the clipped commits of
                # THIS fixture do hold one, and the pathspec walk stops silently at the shallow
                # boundary -- so init is a cannot-evaluate refusal, never a silent pass.
                # DISCRIMINATOR: rc 0 (the truncation hides the store from an ungated scan)
                # against a scan without the shallow gate.
                shallow_prior = base / "ancestry-shallow"
                git_call(base, ["-c", "protocol.file.allow=always", "clone",
                                "--depth", "1",
                                "file://" + str(ancestry), str(shallow_prior)])
                rc, output = run(shallow_prior)
                check("ancestry: shallow history refused as cannot-evaluate",
                      rc == EXIT_ERROR and "SHALLOW" in output and "unshallow" in output)
                check("ancestry: the complete shallow refusal text",
                      output == cannot_evaluate_output(shallow_prior, (
                          "cannot evaluate prior-store ancestry: this repository is SHALLOW, "
                          "so the first-parent history of HEAD is truncated and a prior store, "
                          "whose counters spec 8.2 forbids restarting, cannot be ruled out; "
                          "unshallow the clone (git fetch --unshallow) and retry")))

                # SPEC 8.2 ancestry scope (first-parent line): a store that existed ONLY on a
                # side branch and was deleted there before the merge never reached a
                # first-parent tree, so the mainline being initialized never held its counters;
                # the scan follows the first-parent line (the same line the adoption seed is
                # proven on, _opf_init_operation decision 6) and init PROCEEDS -- side-line
                # ancestry is the section 14 adoption investigation concern. (A store that WAS
                # merged to the mainline is caught: the merge commit tree holds it on the
                # first-parent line.)
                sideline = make_git("ancestry-sideline")
                (sideline / "seed.txt").write_bytes(b"base\n")
                git_call(sideline, ["--literal-pathspecs", "add", "-A"])
                git_call(sideline, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "base"])
                git_call(sideline, ["checkout", "-q", "-b", "feature"])
                side_machine = sideline / working / machine_name
                side_machine.mkdir(parents=True)
                (side_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (sideline / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:."\n')
                git_call(sideline, ["--literal-pathspecs", "add", "-A"])
                git_call(sideline, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "store on side"])
                git_call(sideline, ["rm", "-r", "-q", "--",
                                    working, _opf_store.POINTER_REL])
                git_call(sideline, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop store on side"])
                git_call(sideline, ["checkout", "-q", "main"])
                git_call(sideline, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "merge", "-q", "--no-ff", "-m",
                                    "merge feature", "feature"])
                # The side branch IS merged (the case the round-1 "unmerged" wording missed).
                check("ancestry-sideline fixture: the store's side branch is merged into HEAD",
                      _opf_observe._run_git(git, sideline, ["merge-base", "--is-ancestor",
                                                            "feature", "HEAD"]).rc == 0)
                rc, output = run(sideline)
                check("ancestry: side-branch-only store does not block init (first-parent scope)",
                      rc == EXIT_OK and valid_sources(sideline))
                # QA round 1: the first-parent limit is disclosed WHERE USERS SEE IT -- the
                # successful init output -- not only in the docstring. QA round 2 MAJOR 1: in
                # exact words, since this very fixture's store sat on a MERGED side branch.
                # DISCRIMINATOR: mutant M-output-unmerged (the round-1 output lines) fails.
                check("ancestry: success output states the exact first-parent limit",
                      "first-parent line only" in output and _states_sideline_limit(output))
                check("ancestry: success output states the current-path limit",
                      _states_path_limit(output))
                # The quickstart's statement of the same limit (QA round 2 MAJOR 1), read the
                # way check_opf_homes reads it. DISCRIMINATOR: the round-1 quickstart wording.
                quickstart = Path(__file__).resolve().parents[1] / "spec" / "OPF-QUICKSTART.md"
                check("ancestry: OPF-QUICKSTART.md states the exact first-parent limit",
                      _states_sideline_limit(quickstart.read_text(encoding="utf-8")))
                check("ancestry: OPF-QUICKSTART.md states the current-path limit",
                      _states_path_limit(quickstart.read_text(encoding="utf-8")))

                # SPEC 8.2 ancestry, POINTER-ONLY marker (spec 4.3 relocated store): history
                # holds ONLY the committed pointer .opf.toml (no .working tree at all), then
                # deletes it. Only the :(literal) pointer pathspec and tree_holds_store's
                # pointer arm can find it, so this fixture is the independent discriminator
                # for both: mutant M-pointer-spec (the :(literal) pathspec dropped) passes
                # the scan (rc 0), and mutant M-pointer-arm (the pointer arm disabled)
                # degrades the verdict to refusing-rather-than-guessing without the
                # prior-store finding; either way the assertions below fail.
                ptr_only = make_git("ancestry-pointer-only")
                (ptr_only / "seed.txt").write_bytes(b"base\n")
                git_call(ptr_only, ["--literal-pathspecs", "add", "-A"])
                git_call(ptr_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "base"])
                (ptr_only / _opf_store.POINTER_REL).write_bytes(
                    b'[store]\ntarget = "dir:../elsewhere"\n')
                git_call(ptr_only, ["--literal-pathspecs", "add", "-A"])
                git_call(ptr_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "pointer only"])
                ptr_commit = git_call(ptr_only, ["rev-parse", "HEAD"]).decode("ascii").strip()
                git_call(ptr_only, ["rm", "-q", "--", _opf_store.POINTER_REL])
                git_call(ptr_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop pointer"])
                rc, output = run(ptr_only)
                check("ancestry: pointer-only prior store refused",
                      rc == EXIT_ERROR and "prior store exists" in output)
                check("ancestry: pointer-only refusal names the holding commit",
                      ptr_commit in output)
                # The restore command names only what that commit holds: git checkout fails
                # outright on a pathspec the commit lacks, so a command naming .working too
                # would restore nothing. DISCRIMINATOR: M-restore-both (every store path named
                # whatever the commit holds) fails the text and the restore run.
                check("ancestry: the complete pointer-only refusal text (pointer restored alone)",
                      output == prior_store_output(ptr_only, ptr_only, ptr_commit,
                                                   [".opf.toml"], True))
                check("ancestry: the printed pointer-only restore command restores the pointer",
                      run_restore(output) == 0
                      and (ptr_only / ".opf.toml").read_bytes()
                      == b'[store]\ntarget = "dir:../elsewhere"\n')

                # SPEC 8.2 ancestry, MANIFEST-ONLY marker (spec 4.3 default location, pointer
                # never committed), at the DEFAULT machine subdir: only the :(glob) manifest
                # pathspec and tree_holds_store's manifest listing can find it. Independent
                # discriminator for both: mutant M-manifest-spec (the :(glob) pathspec
                # dropped) passes the scan (rc 0), and mutant M-manifest-arm (the listing
                # arm disabled) loses the prior-store finding.
                mf_only = make_git("ancestry-manifest-only")
                (mf_only / "seed.txt").write_bytes(b"base\n")
                git_call(mf_only, ["--literal-pathspecs", "add", "-A"])
                git_call(mf_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "base"])
                mf_machine = mf_only / working / machine_name
                mf_machine.mkdir(parents=True)
                (mf_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(mf_only, ["--literal-pathspecs", "add", "-A"])
                git_call(mf_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "manifest only"])
                mf_commit = git_call(mf_only, ["rev-parse", "HEAD"]).decode("ascii").strip()
                git_call(mf_only, ["rm", "-r", "-q", "--", working])
                git_call(mf_only, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop manifest"])
                rc, output = run(mf_only)
                check("ancestry: manifest-only prior store refused",
                      rc == EXIT_ERROR and "prior store exists" in output)
                check("ancestry: manifest-only refusal names the holding commit",
                      mf_commit in output)
                check("ancestry: the complete manifest-only refusal text (.working alone, "
                      "its missing counters.toml named as a PARTIAL restore)",
                      output == prior_store_output(
                          mf_only, mf_only, mf_commit, [".working"], True,
                          gaps=[working + "/" + machine_name + "/"
                                + _opf_check.COUNTERS_NAME]))

                # The same manifest-only shape under a RENAMED machine subdir: spec 4.4/4.5
                # admit any single-level subdir name, so the glob (and the listing arm) must
                # match .working/<any>/manifest.toml, not just the default name.
                mf_renamed = make_git("ancestry-manifest-renamed")
                (mf_renamed / "seed.txt").write_bytes(b"base\n")
                git_call(mf_renamed, ["--literal-pathspecs", "add", "-A"])
                git_call(mf_renamed, ["-c", "user.email=t@t", "-c", "user.name=t",
                                      "commit", "-m", "base"])
                ren_machine = mf_renamed / working / "custom-machine"
                ren_machine.mkdir(parents=True)
                (ren_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(mf_renamed, ["--literal-pathspecs", "add", "-A"])
                git_call(mf_renamed, ["-c", "user.email=t@t", "-c", "user.name=t",
                                      "commit", "-m", "renamed-machine manifest"])
                ren_commit = git_call(mf_renamed, ["rev-parse", "HEAD"]).decode("ascii").strip()
                git_call(mf_renamed, ["rm", "-r", "-q", "--", working])
                git_call(mf_renamed, ["-c", "user.email=t@t", "-c", "user.name=t",
                                      "commit", "-m", "drop renamed store"])
                rc, output = run(mf_renamed)
                check("ancestry: renamed-machine-subdir manifest refused",
                      rc == EXIT_ERROR and "prior store exists" in output)
                check("ancestry: renamed-machine refusal names the holding commit",
                      ren_commit in output)
                check("ancestry: the complete renamed-machine refusal text (its missing "
                      "counters.toml named as a PARTIAL restore)",
                      output == prior_store_output(
                          mf_renamed, mf_renamed, ren_commit, [".working"], True,
                          gaps=[working + "/custom-machine/" + _opf_check.COUNTERS_NAME]))

                # SPEC 8.2 ancestry, GRAFT fail-closed (QA round 1 MAJOR): a legacy
                # .git/info/grafts line naming the deletion commit WITH NO PARENTS cuts the
                # parent link inside every rev-list walk (--no-replace-objects neutralizes
                # replace refs, NOT grafts), so the pre-fix scan saw no store-path commit,
                # re-init passed (rc 0), and counters restarted at zero over the BI = 7
                # high-water this history holds. A grafts file that exists is a
                # cannot-evaluate REFUSAL, never interpreted (the adoption reader's stance);
                # removing it restores the ordinary prior-store refusal, proving the graft
                # was the only thing hiding the store. DISCRIMINATOR: rc 0 and a re-zeroed
                # counters.toml against a scan that lets git resolve grafts.
                grafted = make_git("ancestry-graft")
                rc, output = run(grafted)
                check("ancestry graft fixture first init succeeds", rc == EXIT_OK)
                git_call(grafted, ["--literal-pathspecs", "add", "-A"])
                git_call(grafted, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "store"])
                g_counters = grafted / working / machine_name / _opf_check.COUNTERS_NAME
                g_seeded = g_counters.read_text(encoding="utf-8").replace("BI = 0", "BI = 7")
                if "BI = 7" not in g_seeded:
                    raise OSError("graft fixture: counters.toml carried no BI = 0 to raise")
                g_counters.write_text(g_seeded, encoding="utf-8")
                git_call(grafted, ["--literal-pathspecs", "add", "-A"])
                git_call(grafted, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "BI high-water 7"])
                g_held = head_of(grafted)
                git_call(grafted, ["rm", "-r", "-q", "--",
                                   working, _opf_store.POINTER_REL])
                git_call(grafted, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop store"])
                g_drop = git_call(grafted, ["rev-parse", "HEAD"]).decode("ascii").strip()
                g_file = grafted / ".git" / "info" / "grafts"
                # init.templateDir= leaves fixtures without .git/info; a real adopter repo has it.
                g_file.parent.mkdir(exist_ok=True)
                g_file.write_bytes((g_drop + "\n").encode("ascii"))
                before = _snapshot(grafted)
                rc, output = run(grafted)
                check("ancestry: grafted-away history refused as cannot-evaluate",
                      rc == EXIT_ERROR and "grafts" in output and "cannot evaluate" in output)
                check("ancestry: grafted re-init wrote nothing (counters never re-zeroed)",
                      _snapshot(grafted) == before)
                # QA round 2 MINOR 3: the grafts refusal states exactly what it found, for
                # every kind of entry, never that the entry "rewrites parent links" (an empty
                # file or a dangling link rewrites nothing). DISCRIMINATOR: mutant
                # M-grafts-found (one fixed description for every kind) fails the kind checks.
                check("ancestry: grafts refusal names a regular file and its size",
                      "found a regular file of {} byte(s)".format(len(g_drop) + 1) in output
                      and "may rewrite parent links" in output)
                check("ancestry: the complete grafts refusal text (regular file)",
                      output == cannot_evaluate_output(grafted, grafts_message(
                          "a regular file of " + str(len(g_drop) + 1) + " byte(s)",
                          str(g_file))))
                g_file.unlink()
                g_file.write_bytes(b"")
                rc, output = run(grafted)
                check("ancestry: an EMPTY grafts file refuses, named as 0 bytes",
                      rc == EXIT_ERROR and output == cannot_evaluate_output(
                          grafted, grafts_message("a regular file of 0 byte(s)", str(g_file))))
                g_file.unlink()
                g_file.symlink_to("absent-grafts-target")
                rc, output = run(grafted)
                check("ancestry: a DANGLING grafts link refuses, named with its target",
                      rc == EXIT_ERROR and output == cannot_evaluate_output(
                          grafted, grafts_message("a symbolic link to 'absent-grafts-target'",
                                                  str(g_file))))
                g_file.unlink()
                g_file.mkdir()
                rc, output = run(grafted)
                check("ancestry: a grafts DIRECTORY refuses, named by kind",
                      rc == EXIT_ERROR and output == cannot_evaluate_output(
                          grafted, grafts_message("an entry of kind directory", str(g_file))))
                g_file.rmdir()
                # QA round 3 MINOR 3: the "special" kind arm, through a FIFO at the grafts
                # path. lstat never opens it, but a regression that read the path would block
                # on the FIFO, so this vector always runs init in a child with a timeout and an
                # address-space limit, never in this process. DISCRIMINATOR: M-special-kind
                # (the else arm's description changed) fails the complete text.
                os.mkfifo(g_file)
                fifo_proc = subprocess.run(
                    [sys.executable, "-I", "-B", str(Path(__file__).resolve().parent / "opf.py"),
                     "init", "--root", str(grafted)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120,
                    preexec_fn=_limit_address_space)
                check("ancestry: a grafts FIFO refuses, named as an entry of kind special",
                      fifo_proc.returncode == EXIT_ERROR
                      and fifo_proc.stdout + fifo_proc.stderr == cannot_evaluate_output(
                          grafted, grafts_message("an entry of kind special", str(g_file))))
                g_file.unlink()
                rc, output = run(grafted)
                check("ancestry: grafts file removed, the prior store is found again",
                      rc == EXIT_ERROR and output == prior_store_output(
                          grafted, grafted, g_held, [".opf.toml", ".working"], True))

                # QA round 2 MINOR 4: the info/grafts PATH arms, pinned. git prints the
                # grafts path of a separate git directory as an absolute path, so a git
                # directory name carrying a non-UTF-8 byte, or a newline, reaches the shape
                # checks. DISCRIMINATORS: mutant M-undecodable-arm (lenient decode, no
                # refusal) lstat()s a mangled path, reads it as absent and passes (rc 0);
                # mutant M-malformed-arm (shape check removed) passes the same way (rc 0).
                def make_separate(name, gitdir_name):
                    sep_root = base / name
                    sep_root.mkdir()
                    git_call(sep_root, ["-c", "init.templateDir=", "-c", "init.defaultBranch=main",
                                        "init", "--separate-git-dir",
                                        str(base / os.fsdecode(gitdir_name))])
                    (sep_root / "seed.txt").write_bytes(b"base\n")
                    git_call(sep_root, ["--literal-pathspecs", "add", "-A"])
                    git_call(sep_root, ["-c", "user.email=t@t", "-c", "user.name=t",
                                        "commit", "-m", "base"])
                    return sep_root

                undecodable = make_separate("ancestry-undecodable-gitdir", b"gitdir-\xff")
                rc, output = run(undecodable)
                check("ancestry: undecodable info/grafts path refused, never read as absent",
                      rc == EXIT_ERROR and "cannot evaluate" in output
                      and "undecodable info/grafts path" in output)
                check("ancestry: the complete undecodable-grafts-path refusal text",
                      output == cannot_evaluate_output(undecodable, (
                          "git history preflight: undecodable info/grafts path "
                          + ascii(os.fsencode(str(base)) + b"/gitdir-\xff/info/grafts\n")
                          + "; refusing")))
                newline_dir = make_separate("ancestry-newline-gitdir", b"gitdir-\nx")
                rc, output = run(newline_dir)
                check("ancestry: malformed (multi-line) info/grafts path refused",
                      rc == EXIT_ERROR and "cannot evaluate" in output
                      and "malformed info/grafts path" in output)
                check("ancestry: the complete multi-line-grafts-path refusal text",
                      output == cannot_evaluate_output(newline_dir, (
                          "git history preflight: malformed info/grafts path "
                          + ascii(os.fsencode(str(base)) + b"/gitdir-\nx/info/grafts\n")
                          + "; refusing")))

                # The lstat-failure arm: .git/info is a regular FILE, so lstat of info/grafts
                # fails with NotADirectoryError; the refusal states that exact failure.
                # DISCRIMINATOR: mutant M-lstat-arm (any OSError read as absent) passes (rc 0).
                info_file = make_git("ancestry-info-not-a-directory")
                (info_file / "seed.txt").write_bytes(b"base\n")
                git_call(info_file, ["--literal-pathspecs", "add", "-A"])
                git_call(info_file, ["-c", "user.email=t@t", "-c", "user.name=t",
                                     "commit", "-m", "base"])
                (info_file / ".git" / "info").write_bytes(b"not a directory\n")
                rc, output = run(info_file)
                check("ancestry: an uninspectable grafts path refuses, naming the lstat failure",
                      rc == EXIT_ERROR and "lstat of the legacy grafts path" in output
                      and "NotADirectoryError" in output)
                info_grafts = str(info_file / ".git" / "info" / "grafts")
                try:
                    os.lstat(info_grafts)
                    info_failure = None
                except OSError as exc:
                    info_failure = exc
                check("ancestry: the complete lstat-failure refusal text",
                      isinstance(info_failure, NotADirectoryError)
                      and output == cannot_evaluate_output(info_file, (
                          "cannot evaluate prior-store ancestry: lstat of the legacy grafts "
                          "path " + ascii(info_grafts) + " failed (" + ascii(info_failure)
                          + "), which this scan does not take as proof that no grafts file is "
                          "present; grafts that git can read at that path may rewrite parent "
                          "links in every history walk and hide a prior store whose counters "
                          "spec 8.2 forbids restarting; refusing")))

                # The POSIX-separator helper, pinned on POSIX hosts: a Windows-flavoured prefix
                # must still yield "/" pathspecs. DISCRIMINATOR: mutant M-as-posix-to-str
                # (str(Path) instead of as_posix) yields backslashes and fails.
                import opf
                if opf._bootstrap() != EXIT_OK:
                    raise OSError("opf helper modules could not be imported")
                check("ancestry: history pathspecs use POSIX separators for a Windows prefix",
                      opf._init_history_rels(PureWindowsPath("sub", "dir"))
                      == ("sub/dir/" + _opf_store.POINTER_REL, "sub/dir/" + working))

                # QA round 4 MINOR 4: the docstring's statement of the current-path limit,
                # pinned like the three user-facing sites (the refusal, the success output and
                # OPF-QUICKSTART.md), so the fourth copy cannot drift from them silently.
                check("ancestry: the _init_no_prior_store docstring states the current-path "
                      "limit in the shared words",
                      _states_path_limit(opf._init_no_prior_store.__doc__))

                # QA round 3 MINOR 3: the missing-newline and NUL arms of the info/grafts shape
                # check. Real git always ends the line with a newline and cannot print a NUL in
                # a path, so these answers come from a stubbed _run_git (a commit-holding,
                # unshallow history whose rev-list finds nothing), driving the scan directly.
                # DISCRIMINATORS: M-endswith-arm (the endswith test removed) strips the last
                # path character, lstats an absent path and PASSES (no exception);
                # M-nul-arm (the NUL test removed) reaches lstat, which raises ValueError
                # (embedded null character) instead of the refusal; both fail the exact text.
                from unittest import mock

                def stubbed_scan(grafts_answer):
                    calls = []

                    def fake_run_git(git_arg, repo_arg, args, **kwargs):
                        calls.append(list(args))
                        answers = [
                            (["rev-parse", "--verify", "--quiet", "HEAD"], b"0" * 40 + b"\n"),
                            (["rev-parse", "--is-shallow-repository"], b"false\n"),
                            (["rev-parse", "--git-path", "info/grafts"], grafts_answer),
                        ]
                        for expected_args, out in answers:
                            if list(args) == expected_args:
                                return _opf_observe._GitOutcome(True, 0, out, "")
                        if list(args[:1]) == ["rev-list"]:
                            return _opf_observe._GitOutcome(True, 0, b"", "")
                        return _opf_observe._GitOutcome(False, None, b"", "unexpected git call")

                    stub_repo = base / "stub-repo"
                    with mock.patch.object(opf._opf_observe, "_run_git", fake_run_git):
                        try:
                            opf._init_no_prior_store(git, stub_repo, stub_repo)
                            return None, calls
                        except Exception as exc:  # noqa: BLE001  the type is part of the verdict
                            return (type(exc), str(exc)), calls

                absent_grafts = os.fsencode(str(base / "stub-absent" / "info" / "grafts"))
                verdict, calls = stubbed_scan(absent_grafts)
                check("ancestry: a grafts path answer missing its newline refuses, before any walk",
                      verdict == (RuntimeError, "git history preflight: malformed info/grafts "
                                  "path " + ascii(absent_grafts) + "; refusing")
                      and calls[-1] == ["rev-parse", "--git-path", "info/grafts"])
                nul_grafts = os.fsencode(str(base / "stub-absent")) + b"/in\0fo/grafts\n"
                verdict, calls = stubbed_scan(nul_grafts)
                check("ancestry: a grafts path answer holding a NUL refuses, before any walk",
                      verdict == (RuntimeError, "git history preflight: malformed info/grafts "
                                  "path " + ascii(nul_grafts) + "; refusing")
                      and calls[-1] == ["rev-parse", "--git-path", "info/grafts"])
                verdict, calls = stubbed_scan(absent_grafts + b"\n")
                check("ancestry: the stubbed scan passes a well-formed absent grafts path",
                      verdict is None and calls[-1][:1] == ["rev-list"])

                # QA round 2 MINOR 2, QA round 3 MEDIUM 1 and MINOR 2, QA round 4 MINOR 2:
                # path-derived text in the refusal. Each fixture commits a store at a sub-root
                # with a hostile or non-ASCII name, deletes the store (CHANGELOG.md stays), and
                # re-inits there; the complete output is compared with text built here.
                # Non-printable names (C0 controls ESC and BEL; format characters U+202E and
                # U+200B; the C1 control U+009B) must never reach the terminal raw, and NO
                # command is printed for them at all (the round-3 ascii()-escaped command could
                # keep a substitution live in some quotings): the refusal carries only inert
                # withheld-command detail lines. Printable names (cafe with U+00E9, and
                # it's$HOME, whose quote and $ a shell would act on) get a shell-ready
                # shlex-quoted command, which is run through a real POSIX shell and must
                # restore the committed store byte for byte.
                # DISCRIMINATORS: M-refusal-raw (paths formatted without ascii()) and
                # M-no-printable-gate (shlex.quote for every path, so raw controls in the
                # command) fail the non-printable vectors, as does M-ascii-fallback (the
                # round-3 escaped command printed instead of the withheld detail lines);
                # M-repr-for-ascii (repr keeps a printable U+00E9 raw in the descriptive
                # mentions) and M-ascii-in-command (a Python-quoted command, which bash cannot
                # use for cafe) fail the cafe vector; M-no-shlex (paths unquoted) and
                # M-ascii-in-command (Python's double quotes let the shell expand $HOME) fail
                # the it's$HOME vector.
                def sub_root_refusal(fixture, name):
                    repo = make_git(fixture)
                    sub = repo / name
                    sub.mkdir()
                    first_rc, first_output = run(sub)
                    git_call(repo, ["--literal-pathspecs", "add", "-A"])
                    git_call(repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "store"])
                    commit = head_of(repo)
                    held = store_contents(sub)
                    git_call(repo, ["--literal-pathspecs", "rm", "-r", "-q", "--",
                                    name + "/.working", name + "/.opf.toml"])
                    git_call(repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop store"])
                    sub_rc, sub_output = run(sub)
                    return (repo, sub, commit, held, first_rc, first_output,
                            sub_rc, sub_output)

                def raw_free(text):
                    return all(ch.isprintable() or ch == "\n" for ch in text)

                format_name = "f" + chr(0x202E) + "evil" + chr(0x200B) + "zw" + chr(0x9B) + "c1"
                for label, fixture, name in (
                        ("C0 controls ESC and BEL", "ancestry-control-chars", "a\x1b[31mred\x07b"),
                        ("format characters U+202E and U+200B and the C1 control U+009B",
                         "ancestry-format-chars", format_name)):
                    (repo, sub, commit, held, first_rc, _first,
                     rc, output) = sub_root_refusal(fixture, name)
                    check("ancestry " + label + ": fixture first init succeeds",
                          first_rc == EXIT_OK)
                    check("ancestry " + label + ": refused with the complete text, the "
                          "command withheld and the details inert-escaped",
                          rc == EXIT_ERROR and output == prior_store_output(
                              sub, repo, commit, [".opf.toml", ".working"], False))
                    check("ancestry " + label + ": no raw non-printable character in the output",
                          raw_free(output))
                    check("ancestry " + label + ": no runnable restore line is printed",
                          not any(line.startswith("  git")
                                  for line in output.splitlines()))

                for label, fixture, name in (
                        ("printable non-ASCII cafe", "ancestry-cafe", "caf" + chr(0xE9)),
                        ("shell-active it's$HOME", "ancestry-shell-chars", "it's$HOME")):
                    (repo, sub, commit, held, first_rc, _first,
                     rc, output) = sub_root_refusal(fixture, name)
                    check("ancestry " + label + ": fixture first init succeeds",
                          first_rc == EXIT_OK)
                    check("ancestry " + label + ": refused with the complete text and a "
                          "shell-ready restore command",
                          rc == EXIT_ERROR and output == prior_store_output(
                              sub, repo, commit, [".opf.toml", ".working"], True))
                    check("ancestry " + label + ": the printed restore command, run by a POSIX "
                          "shell, restores the committed store",
                          run_restore(output) == 0 and store_contents(sub) == held)

                # QA round 4 MINOR 2 and QA round 5 MAJOR (treated as security fixes): the
                # refusal must print NOTHING a shell would execute, on ANY line. The round-3
                # fallback printed an ascii()-escaped command, and for a name holding a
                # single quote and no double quote, ascii() yields a DOUBLE-quoted Python
                # literal in which $(touch PWNED) stays live under POSIX double-quote
                # semantics: pasting that line created PWNED. The round-4 vector then probed
                # only the remedy block, while the REFUSED line itself still interpolated the
                # root with ascii() (QA round 5 MAJOR: for a name holding both quote kinds
                # ascii() emits a single-quoted literal whose embedded backslash-quote ends
                # the shell's quote early and re-arms the substitution). Now every
                # interpolation is inert-escaped, no command line is printed for a
                # non-printable path, the WHOLE output carries no "$" or "`" at all, and
                # sh_inert_probe runs the complete output, and separately each of its lines,
                # through a real POSIX shell from an EMPTY directory, asserting nothing was
                # created and no payload ran. DISCRIMINATORS: M-ascii-fallback (the round-3
                # escaped command printed) creates PWNED in the empty directory; a safe set
                # widened to admit "$" fails the no-dollar check; an ascii()-formatted
                # REFUSED line fails the complete text and leaves "$" in the output.
                subst_name = "x'$(touch PWNED)\x07"
                (repo, sub, commit, held, first_rc, first_out,
                 rc, output) = sub_root_refusal("ancestry-subst", subst_name)
                check("ancestry substitution name: fixture first init succeeds",
                      first_rc == EXIT_OK)
                check("ancestry substitution name: refused with the complete withheld text",
                      rc == EXIT_ERROR and output == prior_store_output(
                          sub, repo, commit, [".opf.toml", ".working"], False))
                check("ancestry substitution name: no raw non-printable character in the output",
                      raw_free(output))
                check("ancestry substitution name: the whole output is the withheld form, "
                      "with no command line and no substitution character anywhere",
                      "opf init: restore command withheld" in output
                      and not any(line.startswith("  git")
                                  for line in output.splitlines())
                      and "$" not in output and "`" not in output)
                check("ancestry substitution name: the complete refusal output run through "
                      "sh in an empty directory, whole and line by line, created nothing "
                      "and ran no payload",
                      sh_inert_probe(output, "subst-refusal") == []
                      and not (repo / "PWNED").exists()
                      and not (sub / "PWNED").exists())
                check("ancestry substitution name: the successful first-init output is "
                      "shell-inert the same way",
                      sh_inert_probe(first_out, "subst-success") == []
                      and not (repo / "PWNED").exists()
                      and not (sub / "PWNED").exists())

                # QA round 4 MINOR 3: the REPOSITORY-path half of the printable gate, with the
                # root at the repository top and every restored path printable, so only
                # str(repo) can trip the gate. A gate reading the restored paths alone would
                # label this remedy shell-ready and print the raw ESC control character.
                # DISCRIMINATOR: M-gate-ignores-repo ([str(repo)] dropped from the gate)
                # prints a shell-ready command with a raw ESC and fails both checks.
                ctl_repo = make_git("ancestry-repo\x1b[31mred")
                rc, output = run(ctl_repo)
                check("ancestry control-character repository: fixture first init succeeds",
                      rc == EXIT_OK)
                git_call(ctl_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(ctl_repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "store"])
                ctl_commit = head_of(ctl_repo)
                git_call(ctl_repo, ["rm", "-r", "-q", "--",
                                    working, _opf_store.POINTER_REL])
                git_call(ctl_repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                    "commit", "-m", "drop store"])
                rc, output = run(ctl_repo)
                check("ancestry control-character repository: refused with the complete "
                      "withheld text, the repository path alone tripping the gate",
                      rc == EXIT_ERROR and output == prior_store_output(
                          ctl_repo, ctl_repo, ctl_commit,
                          [".opf.toml", ".working"], False))
                check("ancestry control-character repository: no raw non-printable character "
                      "and no runnable restore line in the output",
                      raw_free(output)
                      and not any(line.startswith("  git")
                                  for line in output.splitlines()))

                # QA round 4 MEDIUM (replacement refs): the printed command carries
                # --no-replace-objects, the neutralization every scan read already runs
                # under, because git checkout honours replacement refs: without the flag a
                # replacement ref from the named commit to one with different store bytes
                # makes a shell-ready command exit 0 while writing the SUBSTITUTED bytes.
                # Fixture: commit the store, commit a substituted-counters variant on a side
                # branch, delete the store on main, install git replace from the store commit
                # to the variant, re-init. The scan ignores the replacement and names the real
                # store commit; the printed command must restore that commit's ORIGINAL
                # bytes. The potency probe shows a flagless checkout of the same commit really
                # follows the replacement in this fixture, so the byte comparison
                # DISCRIMINATES M-no-replace-flag (the flag dropped from the printed command).
                repl = make_git("ancestry-replace")
                rc, output = run(repl)
                check("ancestry replacement fixture first init succeeds", rc == EXIT_OK)
                git_call(repl, ["--literal-pathspecs", "add", "-A"])
                git_call(repl, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "store"])
                repl_commit = head_of(repl)
                repl_original = store_contents(repl)
                git_call(repl, ["checkout", "-q", "-b", "substituted"])
                repl_counters = repl / working / machine_name / _opf_check.COUNTERS_NAME
                repl_sub = repl_counters.read_text(encoding="utf-8").replace("BI = 0", "BI = 9")
                if "BI = 9" not in repl_sub:
                    raise OSError("replacement fixture: counters.toml carried no BI = 0 "
                                  "to substitute")
                repl_counters.write_text(repl_sub, encoding="utf-8")
                git_call(repl, ["--literal-pathspecs", "add", "-A"])
                git_call(repl, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "substituted counters"])
                repl_bad = head_of(repl)
                git_call(repl, ["checkout", "-q", "main"])
                git_call(repl, ["rm", "-r", "-q", "--",
                                working, _opf_store.POINTER_REL])
                git_call(repl, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "drop store"])
                git_call(repl, ["replace", repl_commit, repl_bad])
                rc, output = run(repl)
                check("ancestry: a replacement ref changes neither the scan nor the complete "
                      "refusal text",
                      rc == EXIT_ERROR and output == prior_store_output(
                          repl, repl, repl_commit, [".opf.toml", ".working"], True))
                # Potency: git_input runs raw git (no --no-replace-objects), so this checkout
                # resolves the store commit THROUGH the replacement and writes the variant.
                git_input(repl, ["--literal-pathspecs", "checkout", repl_commit, "--",
                                 _opf_store.POINTER_REL, working], b"")
                check("ancestry replacement fixture potency: a flagless checkout of the same "
                      "commit restores the substituted bytes",
                      repl_counters.read_text(encoding="utf-8") == repl_sub
                      and store_contents(repl) != repl_original)
                check("ancestry: the printed restore command restores the ORIGINAL store "
                      "bytes with the replacement ref still installed",
                      run_restore(output) == 0 and store_contents(repl) == repl_original)

                # QA round 4 MEDIUM 1 (split deletion): .working deleted in one commit and
                # the pointer in a later one left the round-3 remedy restoring the pointer
                # ALONE while presenting that one command as the store restore: the named
                # commit (the final deletion's first parent) holds only .opf.toml, whose
                # dir:. target, counters included, stayed deleted after an exit-0 command.
                # Now the refusal labels the first command incomplete by itself and prints an
                # ADDITIONAL command restoring .working from the newest first-parent tree
                # that holds it (here the counter-bump commit); running both restores the
                # whole store, high-water included. DISCRIMINATOR: M-no-split-probe (the
                # probe removed) prints the round-3 single pointer-only command labelled as
                # the store restore and fails the text and the byte comparison.
                split = make_git("ancestry-split-working-first")
                rc, output = run(split)
                check("ancestry split-deletion fixture first init succeeds", rc == EXIT_OK)
                git_call(split, ["--literal-pathspecs", "add", "-A"])
                git_call(split, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "store"])
                split_counters = split / working / machine_name / _opf_check.COUNTERS_NAME
                split_seeded = split_counters.read_text(encoding="utf-8").replace(
                    "BI = 0", "BI = 7")
                if "BI = 7" not in split_seeded:
                    raise OSError("split fixture: counters.toml carried no BI = 0 to raise")
                split_counters.write_text(split_seeded, encoding="utf-8")
                git_call(split, ["--literal-pathspecs", "add", "-A"])
                git_call(split, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "BI high-water 7"])
                split_full = head_of(split)
                split_original = store_contents(split)
                git_call(split, ["rm", "-r", "-q", "--", working])
                git_call(split, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop working first"])
                split_ptr = head_of(split)
                git_call(split, ["rm", "-q", "--", _opf_store.POINTER_REL])
                git_call(split, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop pointer second"])
                rc, output = run(split)
                check("ancestry split deletion: the complete refusal names both commits and "
                      "labels the pointer-only command incomplete by itself",
                      rc == EXIT_ERROR and output == prior_store_output(
                          split, split, split_ptr, [".opf.toml"], True,
                          extra=(split_full, [".working"])))
                check("ancestry split deletion: running both printed commands restores the "
                      "whole store, high-water counters included",
                      run_restore(output) == 0
                      and store_contents(split) == split_original)

                # The same split in the OTHER order (pointer deleted first), exercising the
                # pointer arm of the probe: the named commit holds .working alone and the
                # additional command restores .opf.toml.
                split2 = make_git("ancestry-split-pointer-first")
                rc, output = run(split2)
                check("ancestry pointer-first split fixture first init succeeds",
                      rc == EXIT_OK)
                git_call(split2, ["--literal-pathspecs", "add", "-A"])
                git_call(split2, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "store"])
                split2_full = head_of(split2)
                split2_original = store_contents(split2)
                git_call(split2, ["rm", "-q", "--", _opf_store.POINTER_REL])
                git_call(split2, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "drop pointer first"])
                split2_wrk = head_of(split2)
                git_call(split2, ["rm", "-r", "-q", "--", working])
                git_call(split2, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "drop working second"])
                rc, output = run(split2)
                check("ancestry pointer-first split: the complete refusal restores .working "
                      "first and the pointer through the additional command",
                      rc == EXIT_ERROR and output == prior_store_output(
                          split2, split2, split2_wrk, [".working"], True,
                          extra=(split2_full, [".opf.toml"])))
                check("ancestry pointer-first split: running both printed commands restores "
                      "the whole store",
                      run_restore(output) == 0
                      and store_contents(split2) == split2_original)

                # QA round 5 MAJOR (requirement B): a split deletion INSIDE .working.
                # counters.toml (BI = 7) is deleted one commit before the rest of the store,
                # so the newest tree holding both markers has a .working WITHOUT counters:
                # the remedy must never be labelled as restoring the store, must say the
                # restore is PARTIAL, and must name the missing counters path.
                # DISCRIMINATOR: the round-4 code printed one command under the remedy
                # "restore the store from that commit", exited 0 and left the spec 8.2
                # high-water deleted with no disclosure; it fails the complete text and the
                # phrase bans below.
                inside = make_git("ancestry-split-inside-counters")
                rc, output = run(inside)
                check("ancestry counters-first inside-split fixture first init succeeds",
                      rc == EXIT_OK)
                git_call(inside, ["--literal-pathspecs", "add", "-A"])
                git_call(inside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "store"])
                in_counters = inside / working / machine_name / _opf_check.COUNTERS_NAME
                in_seeded = in_counters.read_text(encoding="utf-8").replace("BI = 0", "BI = 7")
                if "BI = 7" not in in_seeded:
                    raise OSError("inside-split fixture: counters.toml carried no BI = 0")
                in_counters.write_text(in_seeded, encoding="utf-8")
                git_call(inside, ["--literal-pathspecs", "add", "-A"])
                git_call(inside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "BI high-water 7"])
                in_missing = working + "/" + machine_name + "/" + _opf_check.COUNTERS_NAME
                git_call(inside, ["rm", "-q", "--", in_missing])
                git_call(inside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "drop counters inside working"])
                in_partial = head_of(inside)
                git_call(inside, ["rm", "-r", "-q", "--", working, _opf_store.POINTER_REL])
                git_call(inside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                  "commit", "-m", "drop the rest"])
                rc, output = run(inside)
                check("ancestry counters-first inside-split: the complete refusal labels the "
                      "restore PARTIAL and names the missing counters path",
                      rc == EXIT_ERROR and output == prior_store_output(
                          inside, inside, in_partial, [".opf.toml", ".working"], True,
                          gaps=[in_missing]))
                check("ancestry counters-first inside-split: no remedy is labelled as "
                      "restoring the store",
                      "restore the store from" not in output
                      and "restores the rest" not in output
                      and "opf init: the restore is PARTIAL" in output
                      and "missing store path: " + in_missing in output)
                check("ancestry counters-first inside-split: the printed command runs clean "
                      "yet provably does not bring the counters back, as disclosed",
                      run_restore(output) == 0 and not in_counters.exists()
                      and (inside / _opf_store.POINTER_REL).is_file())

                # The same split INSIDE .working at the MANIFEST, under a three-commit outer
                # split, pinning the holder-is-the-hit arm (QA round 5 MEDIUM 3, mutant
                # always-parent): manifest.toml deleted first, .working second, .opf.toml
                # third. The probe for .working must name the manifest-less tree itself (the
                # newest first-parent tree that holds it), never that commit's parent, and
                # the remedy must say the restore is PARTIAL naming the manifest.
                # DISCRIMINATORS: mutant always-parent (holder taken from the hit's parent
                # even when the hit's own tree holds the marker) names the full-store commit
                # and fails the complete text; a scan without working_gaps presents the
                # manifest-less .working with no PARTIAL disclosure and fails it too.
                minside = make_git("ancestry-split-inside-manifest")
                rc, output = run(minside)
                check("ancestry manifest-first inside-split fixture first init succeeds",
                      rc == EXIT_OK)
                git_call(minside, ["--literal-pathspecs", "add", "-A"])
                git_call(minside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "store"])
                m_missing = working + "/" + machine_name + "/" + _opf_store.MANIFEST_NAME
                git_call(minside, ["rm", "-q", "--", m_missing])
                git_call(minside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop manifest inside working"])
                m_partial = head_of(minside)
                git_call(minside, ["rm", "-r", "-q", "--", working])
                git_call(minside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop working"])
                m_ptr = head_of(minside)
                git_call(minside, ["rm", "-q", "--", _opf_store.POINTER_REL])
                git_call(minside, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "drop pointer"])
                rc, output = run(minside)
                check("ancestry manifest-first inside-split: the additional command names "
                      "the manifest-less tree itself and the refusal says PARTIAL naming "
                      "the manifest",
                      rc == EXIT_ERROR and output == prior_store_output(
                          minside, minside, m_ptr, [".opf.toml"], True,
                          extra=(m_partial, [".working"]), gaps=[m_missing]))
                check("ancestry manifest-first inside-split: both printed commands run "
                      "clean, restore the counters, and provably not the manifest, as "
                      "disclosed",
                      run_restore(output) == 0
                      and (minside / working / machine_name
                           / _opf_check.COUNTERS_NAME).is_file()
                      and not (minside / m_missing).exists())

                # QA round 5 MEDIUM 3 (mutants no-withheld-extra and withheld-commit-
                # singular): a NON-printable hostile name combined with an outer split
                # deletion, so the withheld form must carry the ADDITIONAL commit and path
                # detail lines and the plural "commits" remedy. The name also holds both
                # quote kinds, $(...), a backquote, ";" and "#" (requirement A), and the
                # complete refusal output and the successful first-init output both go
                # through sh_inert_probe. DISCRIMINATORS: dropping the withheld extra block
                # loses the two additional detail lines; hardcoding the singular "commit"
                # fails the plural remedy; both fail the complete text.
                wname = "w'$(touch PWNED)" + '"' + "`;#" + "\x07"
                wrepo = make_git("ancestry-withheld-split")
                wsub = wrepo / wname
                wsub.mkdir()
                rc, wfirst = run(wsub)
                check("ancestry withheld-split fixture first init succeeds", rc == EXIT_OK)
                git_call(wrepo, ["--literal-pathspecs", "add", "-A"])
                git_call(wrepo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "store"])
                wfull = head_of(wrepo)
                git_call(wrepo, ["--literal-pathspecs", "rm", "-q", "--",
                                 wname + "/" + _opf_store.POINTER_REL])
                git_call(wrepo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop pointer first"])
                wmid = head_of(wrepo)
                git_call(wrepo, ["--literal-pathspecs", "rm", "-r", "-q", "--",
                                 wname + "/" + working])
                git_call(wrepo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop working second"])
                rc, output = run(wsub)
                check("ancestry withheld-split: the complete withheld refusal carries the "
                      "additional commit and path lines and the plural commits remedy",
                      rc == EXIT_ERROR and output == prior_store_output(
                          wsub, wrepo, wmid, [".working"], False,
                          extra=(wfull, [".opf.toml"])))
                check("ancestry withheld-split: the additional lines name the holder and "
                      "the plural remedy names the commits",
                      "opf init:   additional commit: " + wfull in output
                      and "opf init:   additional restore path: " in output
                      and "from the commits named below" in output)
                check("ancestry withheld-split: no raw non-printable character, no command "
                      "line, no substitution character anywhere in the output",
                      raw_free(output)
                      and not any(line.startswith("  git")
                                  for line in output.splitlines())
                      and "$" not in output and "`" not in output)
                check("ancestry withheld-split: refusal and first-init outputs are "
                      "shell-inert through sh, whole and line by line, and no payload ran",
                      sh_inert_probe(output, "withheld-split-refusal") == []
                      and sh_inert_probe(wfirst, "withheld-split-success") == []
                      and not (wrepo / "PWNED").exists()
                      and not (wsub / "PWNED").exists())

                # QA round 5 MAJOR (requirement A, the orchestrator reproduction): a
                # PRINTABLE name holding BOTH quote kinds plus $(...), a backquote payload,
                # ";" and "#". ascii() rendered such a name as a single-quoted Python literal
                # whose embedded backslash-quote ended the shell's quote early, so pasting
                # the REFUSED line itself executed the embedded payload. Every line of the
                # refusal (shell-ready branch) and of the successful first init must now be
                # shell-inert: the probes run the whole output and each single line through
                # the real POSIX sh from an empty directory. DISCRIMINATOR: an
                # ascii()-interpolated REFUSED line or a raw JSON root creates PWNED.
                bq_name = "b'$(touch PWNED)" + '"' + "`touch PWNED`;#"
                (bq_repo, bq_sub, bq_commit, bq_held, first_rc, bq_first,
                 rc, output) = sub_root_refusal("ancestry-both-quotes", bq_name)
                check("ancestry both-quotes name: fixture first init succeeds",
                      first_rc == EXIT_OK)
                check("ancestry both-quotes name: refused with the complete text and a "
                      "shell-ready restore command",
                      rc == EXIT_ERROR and output == prior_store_output(
                          bq_sub, bq_repo, bq_commit, [".opf.toml", ".working"], True))
                check("ancestry both-quotes name: refusal and first-init outputs are "
                      "shell-inert through sh, whole and line by line, and no payload ran",
                      sh_inert_probe(output, "both-quotes-refusal") == []
                      and sh_inert_probe(bq_first, "both-quotes-success") == []
                      and not (bq_repo / "PWNED").exists()
                      and not (bq_sub / "PWNED").exists())
                check("ancestry both-quotes name: the printed restore command, run by a "
                      "POSIX shell, still restores the committed store",
                      run_restore(output) == 0 and store_contents(bq_sub) == bq_held)
                bq_rows = [json.loads(line) for line in bq_first.splitlines()
                           if line.startswith("{")]
                check("ancestry both-quotes name: the created-event JSON carries the root "
                      "inert-escaped, never raw",
                      any(row.get("event") == "created"
                          and row.get("root") == inert_prose(str(bq_sub))
                          for row in bq_rows)
                      and all("$(" not in json.dumps(row) and "`" not in json.dumps(row)
                              for row in bq_rows))

                # The cannot-evaluate arm under the same hostile-name class (requirement A):
                # a grafts entry in a repository whose own name holds both quote kinds and
                # the payload, so the printed exception text interpolates the hostile grafts
                # path. The exception text is prose-inert-escaped at the print; the complete
                # output must carry no raw "$" or "`" and be shell-inert.
                ce_repo = make_git("ancestry-ce'$(touch PWNED)" + '"' + "`;#")
                (ce_repo / "seed.txt").write_bytes(b"base\n")
                git_call(ce_repo, ["--literal-pathspecs", "add", "-A"])
                git_call(ce_repo, ["-c", "user.email=t@t", "-c", "user.name=t",
                                   "commit", "-m", "base"])
                ce_grafts = ce_repo / ".git" / "info" / "grafts"
                ce_grafts.parent.mkdir(exist_ok=True)
                ce_grafts.write_bytes(b"x\n")
                rc, output = run(ce_repo)
                check("ancestry both-quotes cannot-evaluate: the complete grafts refusal "
                      "text with the hostile path inert-escaped",
                      rc == EXIT_ERROR and output == cannot_evaluate_output(
                          ce_repo, grafts_message("a regular file of 2 byte(s)",
                                                  str(ce_grafts))))
                check("ancestry both-quotes cannot-evaluate: no raw substitution character "
                      "and shell-inert through sh, whole and line by line",
                      "$" not in output and "`" not in output
                      and sh_inert_probe(output, "both-quotes-cannot-evaluate") == []
                      and not (ce_repo / "PWNED").exists())

                # QA round 5 MEDIUM 3 (mutant no-parentless-arm): the split probe's hit is a
                # PARENTLESS commit whose tree lacks a real pointer (it committed a DIRECTORY
                # at the pointer path), so the arm must conclude no pointer ever existed and
                # print the single .working command with no additional disclosure.
                # DISCRIMINATOR: without the parentless continue, the arm asks for the root
                # commit's first parent, gets an empty object id, and degrades the refusal
                # to a cannot-evaluate; the complete-text check fails.
                pl = make_git("ancestry-parentless-pointer-dir")
                pl_sub = pl / "sub"
                pl_fake = pl_sub / _opf_store.POINTER_REL
                pl_fake.mkdir(parents=True)
                (pl_fake / "x").write_bytes(b"not a pointer\n")
                (pl_sub / "seed.txt").write_bytes(b"keeps the sub-root non-empty\n")
                git_call(pl, ["--literal-pathspecs", "add", "-A"])
                git_call(pl, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "parentless pointer-path directory"])
                pl_machine = pl_sub / working / machine_name
                pl_machine.mkdir(parents=True)
                (pl_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(pl, ["--literal-pathspecs", "add", "-A"])
                git_call(pl, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "manifest beside the directory"])
                pl_holder = head_of(pl)
                git_call(pl, ["rm", "-r", "-q", "--", "sub/" + working,
                              "sub/" + _opf_store.POINTER_REL])
                git_call(pl, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop both"])
                rc, output = run(pl_sub)
                check("ancestry parentless pointer-path directory: refused with the single "
                      ".working command, no additional disclosure, PARTIAL for counters",
                      rc == EXIT_ERROR and output == prior_store_output(
                          pl_sub, pl, pl_holder, [".working"], True,
                          gaps=[working + "/" + machine_name + "/"
                                + _opf_check.COUNTERS_NAME]))
                check("ancestry parentless pointer-path directory: the refusal stays the "
                      "definite prior-store finding",
                      "prior store exists" in output and "cannot evaluate" not in output
                      and "additional" not in output)

                # QA round 5 MEDIUM 3 (mutant no-nonstore-holder-arm): the split probe's hit
                # is a DELETION whose first parent's tree holds only a committed DIRECTORY at
                # the pointer path (a non-store change), so the arm must conclude there is no
                # restorable pointer and print the single .working command.
                # DISCRIMINATOR: without the non-store-holder continue, the probe prints a
                # bogus additional command restoring the directory as if it were the pointer;
                # the complete-text check fails.
                ns = make_git("ancestry-nonstore-holder")
                ns_sub = ns / "sub"
                ns_fake = ns_sub / _opf_store.POINTER_REL
                ns_fake.mkdir(parents=True)
                (ns_fake / "x").write_bytes(b"not a pointer\n")
                (ns_sub / "seed.txt").write_bytes(b"keeps the sub-root non-empty\n")
                git_call(ns, ["--literal-pathspecs", "add", "-A"])
                git_call(ns, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "pointer-path directory"])
                git_call(ns, ["rm", "-r", "-q", "--", "sub/" + _opf_store.POINTER_REL])
                git_call(ns, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop the directory"])
                ns_machine = ns_sub / working / machine_name
                ns_machine.mkdir(parents=True)
                (ns_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(ns, ["--literal-pathspecs", "add", "-A"])
                git_call(ns, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "manifest after the directory era"])
                ns_holder = head_of(ns)
                git_call(ns, ["rm", "-r", "-q", "--", "sub/" + working])
                git_call(ns, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "drop working"])
                rc, output = run(ns_sub)
                check("ancestry non-store holder: refused with the single .working command "
                      "and no bogus additional pointer command",
                      rc == EXIT_ERROR and output == prior_store_output(
                          ns_sub, ns, ns_holder, [".working"], True,
                          gaps=[working + "/" + machine_name + "/"
                                + _opf_check.COUNTERS_NAME]))
                check("ancestry non-store holder: no additional command or path is disclosed",
                      "additional" not in output)

                # SPEC 8.2 ancestry, --first-parent is LOAD-BEARING: a merge built with
                # commit-tree whose tree IS the storeless side tree (TREESAME to its side
                # parent) drops the mainline store. On HEAD's first-parent line that merge
                # is the newest store-path change and its first parent held the store, so
                # init REFUSES naming the mainline store commit; an UNSCOPED rev-list walk
                # instead simplifies the merge to its TREESAME side parent, never visits the
                # mainline store commits, sees no store-path commit at all, and would PASS.
                # DISCRIMINATOR: rc 0 against mutant M-first-parent (the flag dropped).
                fp = make_git("ancestry-first-parent")
                (fp / "seed.txt").write_bytes(b"base\n")
                git_call(fp, ["--literal-pathspecs", "add", "-A"])
                git_call(fp, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "base"])
                fp_base = git_call(fp, ["rev-parse", "HEAD"]).decode("ascii").strip()
                fp_machine = fp / working / machine_name
                fp_machine.mkdir(parents=True)
                (fp_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                (fp / _opf_store.POINTER_REL).write_bytes(b'[store]\ntarget = "dir:."\n')
                git_call(fp, ["--literal-pathspecs", "add", "-A"])
                git_call(fp, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "store on main"])
                fp_store = git_call(fp, ["rev-parse", "HEAD"]).decode("ascii").strip()
                git_call(fp, ["checkout", "-q", "-b", "side", fp_base])
                (fp / "side.txt").write_bytes(b"side\n")
                git_call(fp, ["--literal-pathspecs", "add", "-A"])
                git_call(fp, ["-c", "user.email=t@t", "-c", "user.name=t",
                              "commit", "-m", "side work"])
                fp_side = git_call(fp, ["rev-parse", "HEAD"]).decode("ascii").strip()
                fp_tree = git_call(fp, ["rev-parse",
                                        fp_side + "^{tree}"]).decode("ascii").strip()
                fp_merge = git_call(fp, ["-c", "user.email=t@t", "-c", "user.name=t",
                                         "commit-tree", fp_tree, "-p", fp_store,
                                         "-p", fp_side, "-m",
                                         "merge drops store"]).decode("ascii").strip()
                git_call(fp, ["checkout", "-q", "main"])
                git_call(fp, ["reset", "--hard", "-q", fp_merge])
                rc, output = run(fp)
                check("ancestry: TREESAME-to-side merge dropping the mainline store refused",
                      rc == EXIT_ERROR and "prior store exists" in output)
                check("ancestry: first-parent refusal names the mainline store commit",
                      fp_store in output)
                check("ancestry: the complete first-parent refusal text (its missing "
                      "counters.toml named as a PARTIAL restore)",
                      output == prior_store_output(
                          fp, fp, fp_store, [".opf.toml", ".working"], True,
                          gaps=[working + "/" + machine_name + "/"
                                + _opf_check.COUNTERS_NAME]))

                # _init_glob_escape is LOAD-BEARING (no false refusal): a store committed at
                # the sibling prefix weX/ must not block init at the metacharacter-named
                # root we[X]/ -- the :(glob) manifest pathspec escapes the bracket class, so
                # the name matches only the literal directory we[X]. DISCRIMINATOR: rc 2
                # (the unescaped glob we[X]/... matches the weX store history, a false
                # refusal) against mutant M-glob-escape (an identity _init_glob_escape).
                near = make_git("ancestry-glob-escape")
                (near / "seed.txt").write_bytes(b"base\n")
                git_call(near, ["--literal-pathspecs", "add", "-A"])
                git_call(near, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "base"])
                sib_machine = near / "weX" / working / machine_name
                sib_machine.mkdir(parents=True)
                (sib_machine / _opf_store.MANIFEST_NAME).write_bytes(b"x = 1\n")
                git_call(near, ["--literal-pathspecs", "add", "-A"])
                git_call(near, ["-c", "user.email=t@t", "-c", "user.name=t",
                                "commit", "-m", "sibling store at weX"])
                bracket_init = near / "we[X]"
                bracket_init.mkdir()
                rc, output = run(bracket_init)
                check("ancestry: metacharacter-named root unaffected by sibling weX store",
                      rc == EXIT_OK and valid_sources(bracket_init))

                # The refuse-rather-than-guessing arm is LOAD-BEARING: a committed DIRECTORY
                # named .opf.toml is a store-path change whose tree holds no store
                # identifier (the pointer must be a BLOB), and after its deletion neither
                # the deletion commit's tree nor its first parent's holds one; init refuses
                # in those words rather than inventing a prior-store finding. DISCRIMINATOR:
                # mutant M-guess-arm (the arm removed) reports a definite "prior store
                # exists" instead.
                guess = make_git("ancestry-guess-arm")
                (guess / "seed.txt").write_bytes(b"base\n")
                git_call(guess, ["--literal-pathspecs", "add", "-A"])
                git_call(guess, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "base"])
                fake_dir = guess / _opf_store.POINTER_REL
                fake_dir.mkdir()
                (fake_dir / "x").write_bytes(b"not a pointer\n")
                git_call(guess, ["--literal-pathspecs", "add", "-A"])
                git_call(guess, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "directory named like the pointer"])
                git_call(guess, ["rm", "-r", "-q", "--", _opf_store.POINTER_REL])
                git_call(guess, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop the directory"])
                guess_drop = head_of(guess)
                rc, output = run(guess)
                check("ancestry: non-store change at a store path refuses rather than guessing",
                      rc == EXIT_ERROR and "refusing rather than guessing" in output
                      and "prior store exists" not in output)
                check("ancestry: the complete refuse-rather-than-guessing text",
                      output == cannot_evaluate_output(guess, (
                          "git history preflight: commit " + guess_drop + " changed a store "
                          "path yet neither its tree nor its first parent's holds a store "
                          "identifier; refusing rather than guessing")))

                # SPEC 8.2 ancestry, no over-refusal: a history of ordinary commits that never
                # held a store identifier proceeds (the no-hit arm; the empty-git-root vector
                # above already covers the unborn-HEAD arm, which also proceeds).
                plain_history = make_git("ancestry-plain-history")
                (plain_history / "seed.txt").write_bytes(b"s\n")
                git_call(plain_history, ["--literal-pathspecs", "add", "-A"])
                git_call(plain_history, ["-c", "user.email=t@t", "-c", "user.name=t",
                                         "commit", "-m", "seed"])
                rc, output = run(plain_history)
                check("ancestry: committed history without a store proceeds",
                      rc == EXIT_OK and valid_sources(plain_history))

                # QA round 3 MINOR 4: the CURRENT-PATH limit, pinned. A store committed at old/
                # on the first-parent line, deleted there, and the directory then renamed to
                # new/ (git mv): its counters did reach a first-parent tree, under another
                # directory, and the scan, whose pathspecs are new/'s present prefix and follow
                # no rename, does not detect it, so init PROCEEDS, and its output states that
                # limit. DISCRIMINATOR: M-any-prefix (the pointer pathspec widened to any
                # directory, a stand-in for a scan that looks beyond the current path) refuses
                # here; a scan extended that way must update every limit statement with it.
                moved = make_git("ancestry-renamed-root")
                old_root = moved / "old"
                old_root.mkdir()
                rc, output = run(old_root)
                check("ancestry renamed-root fixture first init succeeds", rc == EXIT_OK)
                git_call(moved, ["--literal-pathspecs", "add", "-A"])
                git_call(moved, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "store at old"])
                old_commit = head_of(moved)
                git_call(moved, ["rm", "-r", "-q", "--", "old/" + working,
                                 "old/" + _opf_store.POINTER_REL])
                git_call(moved, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "drop store at old"])
                git_call(moved, ["mv", "old", "new"])
                git_call(moved, ["-c", "user.email=t@t", "-c", "user.name=t",
                                 "commit", "-m", "rename old to new"])
                check("ancestry renamed-root fixture: a HEAD ancestor held the store at old/",
                      _opf_observe._run_git(git, moved, [
                          "cat-file", "-e", old_commit + ":old/" + _opf_store.POINTER_REL]).rc == 0
                      and _opf_observe._run_git(git, moved, [
                          "merge-base", "--is-ancestor", old_commit, "HEAD"]).rc == 0)
                rc, output = run(moved / "new")
                check("ancestry: a store committed under the root's former name is not detected "
                      "(current-path limit)",
                      rc == EXIT_OK and valid_sources(moved / "new"))
                check("ancestry: that success output states the current-path limit",
                      _states_path_limit(output) and _states_sideline_limit(output))

                # OPF-D2B round 6: in a PARTIAL clone a skip-worktree .gitignore whose blob is ABSENT locally
                # reads as no-rule under the no-lazy-fetch probe, but the adopter's own `git add` fetches that
                # blob and can then silently ignore the store. That divergence is a cannot-evaluate init must
                # REFUSE (guard-input-soundness), not pass. DISCRIMINATOR: without the availability check init
                # returns rc 0 and creates a store git add would skip.
                promisor_src = make_git("d2b-promisor-src")
                (promisor_src / ".gitignore").write_bytes(working.encode("ascii") + b"/\n")
                git_call(promisor_src, ["--literal-pathspecs", "add", ".gitignore"])
                git_call(promisor_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                        "commit", "-m", "seed"])
                git_call(promisor_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(promisor_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                src_url = "file://" + str(promisor_src)

                missing_blob = base / "d2b-missing-blob"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(missing_blob)])
                git_call(missing_blob, ["read-tree", "HEAD"])
                git_call(missing_blob, ["update-index", "--skip-worktree", ".gitignore"])
                mb_before = _snapshot(missing_blob)
                rc, output = run(missing_blob)
                check("partial-clone missing ignore-blob refused",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("partial-clone missing ignore-blob preserved",
                      _snapshot(missing_blob) == mb_before)

                # No over-refusal: a partial clone whose .gitignore is CHECKED OUT (blob materialized) and
                # does not match the store is read from disk like any full clone; init proceeds normally.
                unrelated_src = make_git("d2b-unrelated-src")
                (unrelated_src / ".gitignore").write_bytes(b"unrelated-only/\n")
                git_call(unrelated_src, ["--literal-pathspecs", "add", ".gitignore"])
                git_call(unrelated_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                         "commit", "-m", "seed"])
                git_call(unrelated_src, ["config", "uploadpack.allowFilter", "true"])
                materialized = base / "d2b-materialized"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "file://" + str(unrelated_src), str(materialized)])
                rc, output = run(materialized)
                check("partial-clone materialized .gitignore init proceeds",
                      rc == EXIT_OK and valid_sources(materialized))

                # No regression: when the (skip-worktree) .gitignore blob IS available locally, the probe reads
                # it from the index exactly as git add, so a store it ignores is still refused as git-ignored --
                # the available-blob parity the round-6 check preserves.
                avail_blob = base / "d2b-available-blob"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", src_url, str(avail_blob)])
                git_call(avail_blob, ["update-index", "--skip-worktree", ".gitignore"])
                (avail_blob / ".gitignore").unlink()
                ab_before = _snapshot(avail_blob)
                rc, output = run(avail_blob)
                check("available skip-worktree ignore-blob still refused as git-ignored",
                      rc == EXIT_ERROR and "git-ignored" in output)
                check("available skip-worktree ignore-blob preserved",
                      _snapshot(avail_blob) == ab_before)

                # OPF-D2B round 7 / FINDING 2: the materialized-file skip must NOT follow symlinks. A
                # skip-worktree .gitignore whose blob is absent and whose worktree path is a SYMLINK to a
                # readable regular file is opened by git with O_NOFOLLOW: the open fails and git falls back
                # to the index blob, which `git add` fetches and then ignores the store. is_file() followed
                # the link and wrongly skipped it as materialized (false PASS); the no-follow lstat classifies
                # the symlink as not-materialized and REFUSES. DISCRIMINATOR: init returns rc 0 (creates a
                # store git add would skip) against the is_file() version.
                symlink_blob = base / "d2b-symlink-gitignore"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(symlink_blob)])
                git_call(symlink_blob, ["read-tree", "HEAD"])
                git_call(symlink_blob, ["update-index", "--skip-worktree", ".gitignore"])
                (base / "d2b-symlink-target").write_bytes(b"readable regular file\n")
                (symlink_blob / ".gitignore").symlink_to(base / "d2b-symlink-target")
                sb_before = _snapshot(symlink_blob)
                rc, output = run(symlink_blob)
                check("symlink .gitignore over absent blob refused",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("symlink .gitignore fixture preserved", _snapshot(symlink_blob) == sb_before)

                # OPF-D2B round 7 / FINDING 3a: an ordinary stage-0 entry WITHOUT skip-worktree is one git
                # never index-reads (read_skip_worktree_file_from_index requires the flag), so an absent blob
                # for it cannot make `git add` ignore the store. The round-6 check refused ANY unmaterialized
                # entry with an absent blob (over-refusal); init must now PROCEED. DISCRIMINATOR: rc 2 against
                # the mode-only version.
                nonswt = base / "d2b-nonskipworktree"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(nonswt)])
                git_call(nonswt, ["read-tree", "HEAD"])
                rc, output = run(nonswt)
                check("non-skip-worktree absent ignore-blob proceeds",
                      rc == EXIT_OK and valid_sources(nonswt))

                # OPF-D2B round 7 / FINDING 3b: a DIRECTORY at the worktree .gitignore path is opened by git
                # successfully (its read then fails) and never index-read, so an absent blob cannot change what
                # `git add` ignores. is_file() was false and the round-6 check refused (over-refusal); the
                # no-follow classification treats a directory as not-a-fallback and PROCEEDS. DISCRIMINATOR:
                # rc 2 against the mode-only version.
                dir_at_path = base / "d2b-dir-at-gitignore"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(dir_at_path)])
                git_call(dir_at_path, ["read-tree", "HEAD"])
                git_call(dir_at_path, ["update-index", "--skip-worktree", ".gitignore"])
                (dir_at_path / ".gitignore").mkdir()
                rc, output = run(dir_at_path)
                check("directory at .gitignore path proceeds",
                      rc == EXIT_OK and valid_sources(dir_at_path))

                # OPF-D2B round 7 / FINDING 3c: an unmerged (stage-2-only) entry has no stage-0 entry, so
                # git's index-read lookup (index_name_pos, which searches stage 0) finds nothing and never
                # reads it; an absent stage-2 blob cannot make `git add` ignore the store. The round-6 check
                # keyed on mode alone and refused (over-refusal); init must now PROCEED. DISCRIMINATOR: rc 2
                # against the stage-blind version.
                unmerged = base / "d2b-unmerged-gitignore"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(unmerged)])
                git_call(unmerged, ["read-tree", "HEAD"])
                orig_blob = git_call(
                    promisor_src, ["rev-parse", "HEAD:.gitignore"]).decode("ascii").strip()
                git_call(unmerged, ["rm", "--cached", "-q", ".gitignore"])
                git_input(unmerged, ["update-index", "--index-info"],
                          ("100644 " + orig_blob + " 2\t.gitignore\n").encode("ascii"))
                rc, output = run(unmerged)
                check("stage-2-only unmerged .gitignore proceeds",
                      rc == EXIT_OK and valid_sources(unmerged))

                # OPF-D2B round 7 / FINDING 1b: candidate .gitignore paths must be matched as LITERAL
                # pathspecs. A store rooted at a directory literally named "[x]" yields the candidate
                # "[x]/.gitignore"; without --literal-pathspecs git expanded the bracket character-class and
                # matched an UNRELATED indexed "x/.gitignore" (a skip-worktree absent blob), a false refusal.
                # As a literal pathspec it matches nothing and init PROCEEDS. DISCRIMINATOR: rc 2 against the
                # non-literal version.
                bracket_src = make_git("d2b-bracket-src")
                (bracket_src / "x").mkdir()
                (bracket_src / "x" / ".gitignore").write_bytes(b"unrelated-only/\n")
                git_call(bracket_src, ["--literal-pathspecs", "add", "-A"])
                git_call(bracket_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                       "commit", "-m", "seed"])
                git_call(bracket_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(bracket_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                bracket = base / "d2b-bracket"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", "file://" + str(bracket_src), str(bracket)])
                git_call(bracket, ["read-tree", "HEAD"])
                git_call(bracket, ["--literal-pathspecs", "update-index", "--skip-worktree",
                                   "x/.gitignore"])
                bracket_root = bracket / "[x]"
                bracket_root.mkdir()
                rc, output = run(bracket_root)
                check("bracket-named store root proceeds (literal-pathspec candidate)",
                      rc == EXIT_OK and valid_sources(bracket_root))

                # OPF-D2B round 7 / FINDING 1a: a candidate whose repo-relative prefix begins with a pathspec
                # magic sigil (a leading colon) must be a LITERAL pathspec. A store rooted at ":magic" yields
                # the candidate ":magic/.gitignore"; without --literal-pathspecs the leading colon is read as
                # an empty magic signature, the listing is empty, and the ignoring skip-worktree absent blob
                # is MISSED (false PASS: a store `git add` would fetch-and-ignore is created). As a literal
                # pathspec the entry is found and init REFUSES. DISCRIMINATOR: rc 0 against the non-literal
                # version.
                colon_src = make_git("d2b-colon-src")
                (colon_src / ":magic").mkdir()
                (colon_src / ":magic" / ".gitignore").write_bytes(working.encode("ascii") + b"/\n")
                git_call(colon_src, ["--literal-pathspecs", "add", "-A"])
                git_call(colon_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                     "commit", "-m", "seed"])
                git_call(colon_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(colon_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                colon = base / "d2b-colon"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", "file://" + str(colon_src), str(colon)])
                git_call(colon, ["read-tree", "HEAD"])
                git_call(colon, ["--literal-pathspecs", "update-index", "--skip-worktree",
                                 ":magic/.gitignore"])
                colon_root = colon / ":magic"
                colon_root.mkdir()
                cr_before = _snapshot(colon_root)
                rc, output = run(colon_root)
                check("colon-magic candidate refused (literal-pathspec finds ignoring blob)",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("colon-magic store fixture preserved", _snapshot(colon_root) == cr_before)

                # OPF-D2B / D1 (BLOCKER): an UNREADABLE directory at the worktree .gitignore path. git's
                # own O_RDONLY|O_NOFOLLOW open of a chmod-000 directory FAILS (EACCES), so git falls back to
                # the index blob and the adopter's `git add` fetches it and then ignores the store. The
                # round-7 check exempted EVERY directory (false PASS); the git-faithful worktree open
                # classifies an unreadable directory as a fallback and REFUSES. DISCRIMINATOR: rc 0 (creates
                # a store git add would skip) against the exempt-all-directories version. (The readable
                # directory -> PROCEED direction is the "directory at .gitignore path" fixture above.)
                unread_dir = base / "d2b-unreadable-dir"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(unread_dir)])
                git_call(unread_dir, ["read-tree", "HEAD"])
                git_call(unread_dir, ["update-index", "--skip-worktree", ".gitignore"])
                ud_gi = unread_dir / ".gitignore"
                ud_gi.mkdir()
                os.chmod(str(ud_gi), 0o000)
                try:
                    rc, output = run(unread_dir)
                finally:
                    os.chmod(str(ud_gi), 0o755)   # restore so cleanup can traverse the fixture
                check("unreadable directory at .gitignore path refused",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("unreadable directory at .gitignore path preserved",
                      not (unread_dir / working).exists()
                      and not (unread_dir / _opf_store.POINTER_REL).exists()
                      and list(ud_gi.iterdir()) == [])

                # OPF-D2B / D2 (BLOCKER): a SYMLINK-mode (120000) index .gitignore. git reads the symlink
                # blob's target text as ignore patterns, so a committed symlink ".gitignore" -> ".working/"
                # in a partial clone (worktree path absent, skip-worktree) makes the adopter's `git add`
                # fetch the symlink blob and ignore the store. The round-7 check skipped mode 120000 (false
                # PASS); the fix reads a symlink-mode blob as an ignore source and REFUSES. DISCRIMINATOR:
                # rc 0 against the regular-file-mode-only version.
                symmode_src = make_git("d2b-symlink-mode-src")
                os.symlink(working + "/", str(symmode_src / ".gitignore"))   # committed as mode 120000
                git_call(symmode_src, ["--literal-pathspecs", "add", "--", ".gitignore"])
                git_call(symmode_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                       "commit", "-m", "seed"])
                git_call(symmode_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(symmode_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                symmode = base / "d2b-symlink-mode"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", "file://" + str(symmode_src), str(symmode)])
                git_call(symmode, ["read-tree", "HEAD"])
                git_call(symmode, ["update-index", "--skip-worktree", ".gitignore"])
                sm_before = _snapshot(symmode)
                rc, output = run(symmode)
                check("symlink-mode index .gitignore refused",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("symlink-mode index .gitignore preserved", _snapshot(symmode) == sm_before)

                # OPF-D2B / D4 (MAJOR): a --literal-pathspecs ls-files still matches DESCENDANTS of a
                # candidate. A store whose candidate is ".gitignore" matches an unrelated indexed
                # ".gitignore/data" (here ".gitignore" is a committed DIRECTORY holding "data"), which the
                # round-7 check treated as an ignore source and REFUSED. A descendant is not a file git ever
                # reads as ignore patterns; the exact-membership filter drops it and init PROCEEDS
                # (git add stages the store, the ".gitignore/data" blob being irrelevant to ignore
                # evaluation). DISCRIMINATOR: rc 2 (false refusal) against the descendant-matching version.
                descendant_src = make_git("d2b-descendant-src")
                (descendant_src / ".gitignore").mkdir()
                (descendant_src / ".gitignore" / "data").write_bytes(working.encode("ascii") + b"/\n")
                git_call(descendant_src, ["--literal-pathspecs", "add", "-A"])
                git_call(descendant_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                          "commit", "-m", "seed"])
                git_call(descendant_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(descendant_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                descendant = base / "d2b-descendant"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", "file://" + str(descendant_src), str(descendant)])
                git_call(descendant, ["read-tree", "HEAD"])
                git_call(descendant, ["--literal-pathspecs", "update-index", "--skip-worktree",
                                      ".gitignore/data"])
                rc, output = run(descendant)
                check("descendant .gitignore/data proceeds (exact-membership candidate)",
                      rc == EXIT_OK and valid_sources(descendant))

                # OPF-D2B round 8 / D5 (BLOCKER): a stage-0 skip-worktree GITLINK-mode (160000) index
                # .gitignore whose OID is a promisor-fetchable BLOB. git's fallback
                # (read_skip_worktree_file_from_index -> do_read_blob) is MODE-BLIND: for a skip-worktree
                # entry whose worktree open fails it reads the entry's OID and applies the object as ignore
                # patterns whenever it is a blob, never consulting the index mode. So this fabricated
                # gitlink-mode entry slips a mode allowlist, yet the adopter's own `git add` fetches the blob
                # and silently ignores the store (confirmed ground truth). The round-8 mode allowlist
                # (regular-file / symlink only) treated mode 160000 as not-an-ignore-source and PASSED (false
                # pass, rc 0, a store git add would skip); the mode-blind check refuses on the unavailable OID
                # regardless of mode. DISCRIMINATOR: rc 0 against the mode-allowlist version.
                gitlink = base / "d2b-gitlink-blob"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(gitlink)])
                git_call(gitlink, ["read-tree", "HEAD"])
                gitlink_blob = git_call(
                    promisor_src, ["rev-parse", "HEAD:.gitignore"]).decode("ascii").strip()
                git_call(gitlink, ["rm", "--cached", "-q", ".gitignore"])
                git_input(gitlink, ["update-index", "--index-info"],
                          ("160000 " + gitlink_blob + " 0\t.gitignore\n").encode("ascii"))
                git_call(gitlink, ["update-index", "--skip-worktree", ".gitignore"])
                gl_before = _snapshot(gitlink)
                rc, output = run(gitlink)
                check("gitlink-mode index .gitignore over fetchable blob refused",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("gitlink-mode index .gitignore preserved", _snapshot(gitlink) == gl_before)

                # OPF-D2B round 8 / D5 control (no over-refusal): the SAME gitlink-mode (160000) skip-worktree
                # entry, but with a real DIRECTORY at the worktree .gitignore path, as a valid initialized
                # submodule has. git's O_NOFOLLOW open of the directory SUCCEEDS, so git never index-reads the
                # OID and `git add` cannot ignore the store through it; init must PROCEED. This proves the
                # refusal is gated by the worktree open FAILING, not by the gitlink mode, so a valid submodule
                # is not blanket-refused.
                gitlink_dir = base / "d2b-gitlink-dir"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", src_url, str(gitlink_dir)])
                git_call(gitlink_dir, ["read-tree", "HEAD"])
                git_call(gitlink_dir, ["rm", "--cached", "-q", ".gitignore"])
                git_input(gitlink_dir, ["update-index", "--index-info"],
                          ("160000 " + gitlink_blob + " 0\t.gitignore\n").encode("ascii"))
                git_call(gitlink_dir, ["update-index", "--skip-worktree", ".gitignore"])
                (gitlink_dir / ".gitignore").mkdir()
                rc, output = run(gitlink_dir)
                check("gitlink-mode with directory at worktree path proceeds (no over-refusal)",
                      rc == EXIT_OK and valid_sources(gitlink_dir))

                # OPF-D2B round 9 / R9-1 (MAJOR over-refusal): the availability check must engage ONLY in a
                # PARTIAL clone. In a FULL clone git CANNOT lazy-fetch, so an absent indexed OID never causes a
                # silent fetch-and-ignore: `git add` stages the store (an absent submodule COMMIT is not a
                # blob, so do_read_blob stages nothing to ignore) rather than ignoring it. Here a REAL
                # submodule named ".gitignore" is sparse-omitted in a FULL clone (Codex's exact repro): its
                # gitlink entry is stage-0 skip-worktree ("S"), its worktree path is absent, and its commit OID
                # is absent from the superproject's object DB -- yet there is NO promisor remote. The round-9
                # HEAD reached cat-file on the absent commit OID and REFUSED (rc 2, a pure over-refusal); the
                # partial-clone gate returns [] for a full clone and init PROCEEDS, matching `git add`
                # (confirmed ground truth: `git add -- .working` stages, rc 0, check-ignore rc 1).
                # DISCRIMINATOR: rc 2 (false refusal) against the ungated round-9 HEAD.
                r9_submod_src = make_git("d2b-r9-submodule-src")
                (r9_submod_src / "keep").write_bytes(b"submodule content\n")
                git_call(r9_submod_src, ["--literal-pathspecs", "add", "-A"])
                git_call(r9_submod_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                         "commit", "-m", "subseed"])
                r9_super_src = make_git("d2b-r9-super-src")
                git_call(r9_super_src, ["-c", "protocol.file.allow=always", "submodule", "add",
                                        "file://" + str(r9_submod_src), ".gitignore"])
                git_call(r9_super_src, ["-c", "user.email=t@t", "-c", "user.name=t",
                                        "commit", "-m", "add sub as .gitignore"])
                r9_full = base / "d2b-r9-fullclone-omitted"
                git_call(base, ["-c", "protocol.file.allow=always", "clone",
                                "file://" + str(r9_super_src), str(r9_full)])
                # A FULL clone has no promisor remote; sparse-checkout omits the .gitignore submodule so its
                # worktree path is absent and skip-worktree is set, leaving the absent commit OID as what the
                # per-entry check would reach were it not gated on partial-clone-ness.
                git_input(r9_full, ["sparse-checkout", "set", "--no-cone", "--stdin"],
                          b"/*\n!/.gitignore\n")
                rc, output = run(r9_full)
                check("full-clone sparse-omitted submodule .gitignore proceeds (partial-clone gate)",
                      rc == EXIT_OK and valid_sources(r9_full))

                # OPF-D2B round 9 / R9-1 companion: an INITIALIZED submodule at ".gitignore" in a FULL
                # RECURSIVE clone, its directory chmod 000. git's O_RDONLY|O_NOFOLLOW open of the unreadable
                # directory FAILS, so the per-entry check would fall back to the gitlink commit OID -- which is
                # absent from the superproject's object DB (the submodule's objects live under .git/modules) --
                # and the round-9 HEAD REFUSED. But it is a FULL clone (no promisor), so `git add` cannot
                # lazy-fetch and stages the store (do_read_blob rejects the non-blob commit); the partial-clone
                # gate returns [] and init PROCEEDS. DISCRIMINATOR: rc 2 against the ungated round-9 HEAD.
                r9_rec = base / "d2b-r9-recursive-chmod000"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--recurse-submodules",
                                "file://" + str(r9_super_src), str(r9_rec)])
                git_call(r9_rec, ["update-index", "--skip-worktree", ".gitignore"])
                r9_rec_gi = r9_rec / ".gitignore"
                os.chmod(str(r9_rec_gi), 0o000)
                try:
                    rc, output = run(r9_rec)
                finally:
                    os.chmod(str(r9_rec_gi), 0o755)   # restore so cleanup can traverse the fixture
                check("full-clone chmod-000 initialized submodule .gitignore proceeds (partial-clone gate)",
                      rc == EXIT_OK and valid_sources(r9_rec))

                # OPF-D2B round 11 / R10-1 + R10-2: _is_partial_clone must match git 2.53.0's OWN promisor
                # registration (repo_has_promisor_remote): git lazy-fetches a missing object iff ANY of
                # (a) a remote.*.partialclonefilter is PRESENT (value-blind, overrides promisor=false),
                # (b) a remote.*.promisor is boolean-TRUE, or (c) extensions.partialClone is present (no
                # format gate: git honours it at core.repositoryformatversion=0 too, confirmed empirically).
                # Each fixture below builds a blobless clone with an ABSENT skip-worktree .gitignore blob
                # (the store-ignoring src), strips the promisor config the clone wrote, then sets exactly the
                # keys under test; the availability check must REFUSE only when git add would fetch-and-ignore.
                def d2b_stripped_partial(name):
                    """A blobless partial clone (absent skip-worktree .gitignore blob) with ALL promisor
                    config stripped, so each scenario sets exactly the registration keys under test."""
                    dest = base / name
                    git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                    "--no-checkout", src_url, str(dest)])
                    git_call(dest, ["read-tree", "HEAD"])
                    git_call(dest, ["update-index", "--skip-worktree", ".gitignore"])
                    for key in ("remote.origin.promisor", "remote.origin.partialclonefilter",
                                "extensions.partialClone"):
                        rr = _opf_observe._run_git(git, dest, ["config", "--unset-all", key])
                        if not rr.completed or rr.rc not in (0, 5):  # rc 5 = key already absent, tolerable
                            raise OSError("fixture --unset-all {} failed at {!r}: {}".format(
                                key, str(dest), rr.err))
                    return dest

                # R10-1 (BLOCKER): partialclonefilter PRESENT, promisor unset, no extension. The filter
                # registers the promisor by presence, so `git add` lazy-fetches the absent blob and ignores
                # the store (ground truth PARTIAL); init must REFUSE. The round-10 promisor-only probe read
                # this as a FULL clone (no promisor line) and PROCEEDED. DISCRIMINATOR: rc 0 (creates a store
                # git add would skip) against the round-10 HEAD.
                filt_only = d2b_stripped_partial("d2b-filter-only")
                git_call(filt_only, ["config", "remote.origin.partialclonefilter", "blob:none"])
                fo_before = _snapshot(filt_only)
                rc, output = run(filt_only)
                check("partialclonefilter-only partial clone refused (R10-1)",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("partialclonefilter-only fixture preserved", _snapshot(filt_only) == fo_before)

                # R10-2 (MAJOR over-refusal): promisor=false alone, no filter, no extension. A false promisor
                # does NOT register, so git CANNOT lazy-fetch: `git add` stages the store (ground truth FULL);
                # init must PROCEED. The round-10 probe matched the promisor=false LINE by presence and
                # REFUSED. DISCRIMINATOR: rc 2 (false refusal) against the round-10 HEAD.
                prom_false = d2b_stripped_partial("d2b-promisor-false")
                git_call(prom_false, ["config", "remote.origin.promisor", "false"])
                rc, output = run(prom_false)
                check("promisor=false-only clone proceeds (R10-2 no over-refusal)",
                      rc == EXIT_OK and valid_sources(prom_false))

                # filter OVERRIDES promisor=false: the filter's presence registers the promisor even with a
                # sibling promisor=false, so `git add` lazy-fetches (ground truth PARTIAL); init must REFUSE.
                # (Locks the value-blind filter semantics: it would break if the filter check became
                # value-sensitive or a promisor=false were read as de-registering.)
                filt_pf = d2b_stripped_partial("d2b-filter-promfalse")
                git_call(filt_pf, ["config", "remote.origin.partialclonefilter", "blob:none"])
                git_call(filt_pf, ["config", "remote.origin.promisor", "false"])
                fpf_before = _snapshot(filt_pf)
                rc, output = run(filt_pf)
                check("partialclonefilter overrides promisor=false: refused",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("filter-over-promfalse fixture preserved", _snapshot(filt_pf) == fpf_before)

                # extensions.partialClone OVERRIDES promisor=false (format 1): the extension registers the
                # named default promisor after the config parse, so `git add` lazy-fetches (ground truth
                # PARTIAL); init must REFUSE.
                ext_pf = d2b_stripped_partial("d2b-ext-promfalse")
                git_call(ext_pf, ["config", "core.repositoryformatversion", "1"])
                git_call(ext_pf, ["config", "extensions.partialClone", "origin"])
                git_call(ext_pf, ["config", "remote.origin.promisor", "false"])
                epf_before = _snapshot(ext_pf)
                rc, output = run(ext_pf)
                check("extensions.partialClone overrides promisor=false (format 1): refused",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("ext-over-promfalse fixture preserved", _snapshot(ext_pf) == epf_before)

                # extensions.partialClone at core.repositoryformatversion=0: the empirically-settled case.
                # git 2.53.0 HONOURS the extension at format 0 (handle_extension_v0 historical compat),
                # confirmed by isolated repro: `git add` lazy-fetches the absent blob and ignores the store
                # (ground truth PARTIAL), so init must REFUSE. Asserting REFUSE both matches git and is the
                # fail-closed direction; this fixture would fail against a (rejected) format>=1-gated
                # implementation, which would wrongly PROCEED.
                ext_v0 = d2b_stripped_partial("d2b-ext-format0")
                git_call(ext_v0, ["config", "extensions.partialClone", "origin"])
                git_call(ext_v0, ["config", "core.repositoryformatversion", "0"])
                ev0_before = _snapshot(ext_v0)
                rc, output = run(ext_v0)
                check("extensions.partialClone at format 0 refused (git honours the extension at v0)",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("ext-format0 fixture preserved", _snapshot(ext_v0) == ev0_before)

                # OPF-D2B round 12 / R11-1 (BLOCKER, fail-open false pass): a promisor remote whose NAME
                # carries non-UTF-8 bytes (promisor=true, no filter, no extension). The round-11 loop
                # enumerated the promisor key byte-preserving (b"remote.up\xffstream.promisor\x00") but then
                # re-queried it via a "replace"-decoded key ("remote.up�stream.promisor"), which asks git
                # for a DIFFERENT (nonexistent) key and returns rc 1 (not found); the round-11 code read that
                # rc 1 as "not a true promisor" and returned FULL, so the availability check was skipped and
                # init PROCEEDED (rc 0) even though `git add` lazy-fetches through the real up\xffstream
                # promisor and IGNORES the store. The fix fails closed: an enumerated promisor key that cannot
                # cleanly re-query (non-UTF-8 name, or any re-query rc != 0) is a cannot-determine -> partial.
                # DISCRIMINATOR: rc 0 (creates a store git add would skip) against the round-11 HEAD.
                nonutf8 = d2b_stripped_partial("d2b-nonutf8-promisor")
                git_call(nonutf8, ["config", "remote.origin.promisor", "true"])
                nu_cfg = nonutf8 / ".git" / "config"
                nu_raw = nu_cfg.read_bytes()
                if b'[remote "origin"]' not in nu_raw:
                    raise OSError("R11-1 fixture: expected a [remote \"origin\"] section to rename")
                nu_cfg.write_bytes(nu_raw.replace(b'[remote "origin"]', b'[remote "up\xffstream"]'))
                # Ground truth in a byte-exact COPY (git add mutates the index and can fetch): the real
                # `git add -A` fetches through the non-UTF-8 promisor and IGNORES the store, so .working is NOT
                # staged and check-ignore reports it ignored. This is the hazard the refusal below guards.
                nu_gt = base / "d2b-nonutf8-groundtruth"
                shutil.copytree(str(nonutf8), str(nu_gt))
                (nu_gt / working).mkdir()
                (nu_gt / working / "f").write_bytes(b"store\n")
                git_call(nu_gt, ["-c", "protocol.file.allow=always", "add", "-A"])
                nu_staged = git_call(nu_gt, ["--literal-pathspecs", "ls-files", "--", working])
                nu_ign = _opf_observe._run_git(git, nu_gt, ["check-ignore", working + "/f"])
                check("R11-1 ground truth: git add fetches via the non-UTF-8 promisor and ignores the store",
                      nu_staged.strip() == b"" and nu_ign.completed and nu_ign.rc == 0)
                nu_before = _snapshot(nonutf8)
                rc, output = run(nonutf8)
                check("non-UTF-8-named promisor remote refused (R11-1)",
                      rc == EXIT_ERROR and "indexed .gitignore blob is unavailable" in output)
                check("non-UTF-8-named promisor fixture preserved", _snapshot(nonutf8) == nu_before)

                # OPF-D2B / D3 (fixture hermeticity): the stdin-fed fixture git helper must build its OWN
                # scrubbed environment, so an inherited GIT_INDEX_FILE (or GIT_DIR / GIT_WORK_TREE /
                # GIT_OBJECT_DIRECTORY / GIT_COMMON_DIR) cannot redirect its write to a caller's external
                # index. We create a genuine external repo index, point GIT_INDEX_FILE at it, run an
                # index-info write through git_input, and assert the external index is BYTE-UNCHANGED.
                # DISCRIMINATOR: against the os.environ.copy() helper the update-index below lands in the
                # external index and its bytes change (the suite would silently mutate caller state, and a
                # stage-2 fixture could report PASS while writing an inherited external index).
                guard_repo = make_git("d3-index-guard")
                (guard_repo / "seed").write_bytes(b"seed\n")
                git_call(guard_repo, ["--literal-pathspecs", "add", "--", "seed"])
                guard_blob = git_call(guard_repo, ["rev-parse", ":seed"]).decode("ascii").strip()
                external_repo = make_git("d3-external-repo")
                (external_repo / "ext").write_bytes(b"external\n")
                git_call(external_repo, ["--literal-pathspecs", "add", "--", "ext"])
                external_index = external_repo / ".git" / "index"
                external_before = external_index.read_bytes()
                saved_index_file = os.environ.get("GIT_INDEX_FILE")
                os.environ["GIT_INDEX_FILE"] = str(external_index)
                try:
                    git_input(guard_repo, ["update-index", "--index-info"],
                              ("100644 " + guard_blob + " 0\tseed2\n").encode("ascii"))
                finally:
                    if saved_index_file is None:
                        os.environ.pop("GIT_INDEX_FILE", None)
                    else:
                        os.environ["GIT_INDEX_FILE"] = saved_index_file
                check("git_input ignores an inherited GIT_INDEX_FILE (external index byte-unchanged)",
                      external_index.read_bytes() == external_before)

                # OPF-FSMONITOR (F-OPF-INIT-FSMONITOR-EXEC): _run_git must suppress a repository-configured
                # core.fsmonitor, so an untrusted repo cannot obtain CODE EXECUTION when the adopter runs
                # `opf init`. init's non-probe git calls route through _init_git -> _opf_observe._run_git;
                # before the fix _run_git omitted `-c core.fsmonitor=false`, so git LAUNCHED the repo's
                # configured fsmonitor program during an index refresh in the init path. The D2B ignore probe
                # already suppressed fsmonitor via _run_git_config_discovery; this closes the parallel
                # _run_git path. We commit a seed, configure a marker-touching fsmonitor, run init, and assert
                # the marker was NOT created (the monitor process never ran). DISCRIMINATOR: against the
                # pre-fix _run_git the marker IS created (fsmonitor executed) even though init still exits 0.
                fsmon = make_git("fsmonitor-exec")
                (fsmon / "seed.txt").write_bytes(b"seed\n")
                git_call(fsmon, ["--literal-pathspecs", "add", "--", "seed.txt"])
                git_call(fsmon, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed"])
                fsmon_marker = base / "fsmonitor-marker"
                fsmon_hook = base / "fsmonitor-evil.sh"
                fsmon_hook.write_text(
                    "#!/bin/sh\ntouch {}\nexit 0\n".format(shlex.quote(str(fsmon_marker))),
                    encoding="utf-8")
                os.chmod(str(fsmon_hook), 0o755)
                git_call(fsmon, ["config", "core.fsmonitor", str(fsmon_hook)])
                rc, output = run(fsmon)
                check("init suppresses a repo-configured core.fsmonitor (no process launched)",
                      not fsmon_marker.exists())
                check("init still succeeds with core.fsmonitor configured",
                      rc == EXIT_OK and valid_sources(fsmon))

                # OPF-OBSERVE-LAZYFETCH-EXEC (F-OPF-OBSERVE-LAZYFETCH-EXEC): an object-reading OPF
                # observation must never trigger a partial-clone LAZY FETCH, because the fetch EXECUTES the
                # repository-configured core.sshCommand -- the same code-execution class as the fsmonitor
                # vector above, reached instead through git's promisor machinery. _show_toml reads a committed
                # TOML via `git show HEAD:<path>` under _opf_observe._run_git; before the fix _scrubbed_env
                # neither set GIT_NO_LAZY_FETCH nor kept an ambient one (the allowlist scrub STRIPS it), so a
                # `show` of an ABSENT blob in a blobless clone lazy-fetched through origin and RAN
                # core.sshCommand. We commit seed.toml, make a blobless partial clone (blob absent, promisor
                # registered by the clone), repoint origin at ssh with a marker-touching core.sshCommand, then
                # call _show_toml and assert the marker was NOT created (no fetch/exec) and the read reports
                # the object unavailable via the existing omit-plus-note path (unchanged for a PRESENT
                # object). Hermetic and offline: ssh://example.invalid never connects -- git spawns
                # core.sshCommand before any network I/O, and the marker script exits without connecting.
                # DISCRIMINATOR: against the round-1 _run_git env the marker IS created (the ssh command ran).
                lazy_src = make_git("lazyfetch-src")
                (lazy_src / "seed.toml").write_bytes(b'name = "x"\n')
                git_call(lazy_src, ["--literal-pathspecs", "add", "--", "seed.toml"])
                git_call(lazy_src, ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "seed"])
                git_call(lazy_src, ["config", "uploadpack.allowFilter", "true"])
                git_call(lazy_src, ["config", "uploadpack.allowAnySHA1InWant", "true"])
                lazy_clone = base / "lazyfetch-clone"
                git_call(base, ["-c", "protocol.file.allow=always", "clone", "--filter=blob:none",
                                "--no-checkout", "file://" + str(lazy_src), str(lazy_clone)])
                lazy_marker = base / "lazyfetch-marker"
                lazy_hook = base / "lazyfetch-ssh.sh"
                lazy_hook.write_text(
                    "#!/bin/sh\ntouch {}\nexit 0\n".format(shlex.quote(str(lazy_marker))),
                    encoding="utf-8")
                os.chmod(str(lazy_hook), 0o755)
                git_call(lazy_clone, ["remote", "set-url", "origin", "ssh://example.invalid/repo"])
                git_call(lazy_clone, ["config", "ssh.variant", "ssh"])
                git_call(lazy_clone, ["config", "core.sshCommand", str(lazy_hook)])
                lazy_notes = []
                lazy_data = _opf_observe._show_toml(git, lazy_clone, "", "seed.toml", "seed", lazy_notes)
                check("observe _show_toml on an absent blob does not lazy-fetch/exec core.sshCommand",
                      not lazy_marker.exists())
                check("observe _show_toml on an absent blob omits the prior with a note",
                      lazy_data is None and any("seed" in n and ("absent" in n or "unreadable" in n)
                                                for n in lazy_notes))
            finally:
                os.environ.clear()
                os.environ.update(saved_env)
                try:
                    os.fchdir(saved_cwd)
                finally:
                    os.close(saved_cwd)

        if failures:
            for label in failures:
                print("check_opf_init: FAIL: " + label, file=sys.stderr)
            return EXIT_FINDING
        print("check_opf_init: PASS ({} executed fixture assertions)".format(len(checked)))
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  missing resources and harness errors cannot skip clean
        print("check_opf_init: cannot evaluate fixture suite: {}; exit 2".format(ascii(exc)),
              file=sys.stderr)
        return EXIT_ERROR


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args not in ([], ["--self-test"]):
            print("usage: check_opf_init.py [--self-test]", file=sys.stderr)
            return EXIT_ERROR
        return _suite(_run_init)
    except Exception as exc:  # noqa: BLE001  final class-width fail-closed backstop
        print("check_opf_init: cannot evaluate: {}; exit 2".format(ascii(exc)), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
