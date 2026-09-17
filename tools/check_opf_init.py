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
import contextlib
import io
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_ERROR = 2


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
    """Run parser, publication, refusal, and preservation vectors against fresh fixtures."""
    try:
        import tomllib
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
                namespaces = (frozenset(_opf_store.BASELINE_TYPES.values())
                              | frozenset(_opf_store.IMPORTER_TYPES.values()))
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
            result = _opf_observe._run_git(git, root, args, allow_lazy_fetch=True)
            if not result.completed or result.rc != 0:
                raise OSError("fixture git failed at {!r}: {}".format(str(root), result.err))
            return result.out

        def git_input(root, args, data):
            # A stdin-fed fixture git call (git update-index --index-info reads the index entry from
            # stdin); _run_git has no stdin channel. Runs under _opf_observe._scrubbed_env (PATH and the
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
            saved_env = {name: os.environ.get(name)
                         for name in ("HOME", "XDG_CONFIG_HOME", "XDG_CONFIG_DIRS",
                                      "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
                                      "GIT_CONFIG_COUNT", "GIT_CONFIG",
                                      "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_NOSYSTEM",
                                      "GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE",
                                      "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR")}
            # Even a broken parser that ignores --root defaults into this isolated, non-git directory.
            # Restore the caller's cwd before TemporaryDirectory removes the fixture.
            saved_cwd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.environ["HOME"] = str(home)
                os.environ.pop("XDG_CONFIG_HOME", None)
                os.environ.pop("XDG_CONFIG_DIRS", None)
                os.environ.pop("GIT_CONFIG_GLOBAL", None)
                os.environ.pop("GIT_CONFIG_SYSTEM", None)
                os.environ.pop("GIT_CONFIG_COUNT", None)
                os.environ.pop("GIT_CONFIG", None)
                os.environ.pop("GIT_CONFIG_PARAMETERS", None)
                os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
                # Drop any inherited index/dir/object redirection so no fixture git call (the subprocess
                # init, and git_input's stdin-fed writes above all) inherits a caller's GIT_INDEX_FILE and
                # mutates an external index (test-hermeticity); saved above, restored in the finally below.
                os.environ.pop("GIT_INDEX_FILE", None)
                os.environ.pop("GIT_DIR", None)
                os.environ.pop("GIT_WORK_TREE", None)
                os.environ.pop("GIT_OBJECT_DIRECTORY", None)
                os.environ.pop("GIT_COMMON_DIR", None)
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
                for name, value in saved_env.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
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
