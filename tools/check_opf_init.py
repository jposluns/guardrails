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
    """Read the complete small fixture tree, including hidden files, links, and empty directories."""
    result = {}

    def walk(directory):
        for path in sorted(directory.iterdir()):
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
            result = _opf_observe._run_git(git, root, args)
            if not result.completed or result.rc != 0:
                raise OSError("fixture git failed at {!r}: {}".format(str(root), result.err))
            return result.out

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
                                      "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_NOSYSTEM")}
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
                check("second init preserves source and git bytes", _snapshot(clean) == before)

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
