#!/usr/bin/env python3
"""Bounded regressions for the PR-1 intake boundary.

The fixture replaces contained filesystem primitives, not the loader or TOML
parser. Upgrade additionally uses isolated on-disk stores and the real CLI.
Permission failures are injected at read time so these tests also work
as root. Production callers and their exception translations still execute.
"""
import contextlib
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import _journal
import _opf_absorb
import _opf_changelog
import _opf_check
import _opf_import
import _opf_ingest
import _opf_store
import _opf_views
import _opf_worklog as wl

M = "m"
LEGACY = "m/worklog.toml"
ARCHIVE = "m/archive/2026/worklog.toml"
BODY = (b'id = "WL-1"\ndate = "2026-01-01T00:00:00Z"\n'
        b'actor = {kind = "maintainer"}\nkind = "fixed"\nsummary = "x"\n')
LEDGER = b"schema = 1\n[[entry]]\n" + BODY


class _Fixture:
    def __init__(self, gen=1):
        self.manifest = {"opf": {"standard": "opf", "layout": "inline", "worklog": gen}}
        self.files = {
            "m/manifest.toml": ('[opf]\nstandard = "opf"\nlayout = "inline"\n'
                                'worklog = {}\n'.format(gen)).encode(),
            "m/version.toml": b"schema = 1\n",
            "CHANGELOG.md": b"",
        }
        self.dirs = {"m", "m/archive", "m/archive/2026"}
        if gen == 1:
            self.files[LEGACY] = LEDGER
        else:
            self.dirs.add("m/worklog")
            self.files["m/worklog/WL-1.toml"] = BODY
        self.prefixes = {}

    def path(self, fd, rel):
        return self.prefixes.get(fd, "") + rel

    def lstat(self, fd, rel):
        path = self.path(fd, rel)
        if path in self.dirs:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_size=0)
        if path in self.files:
            raw = self.files[path]
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600,
                                   st_size=0 if isinstance(raw, Exception) else len(raw))
        return None

    def read(self, fd, rel, **_kwargs):
        raw = self.files[self.path(fd, rel)]
        if isinstance(raw, Exception):
            raise raw
        return raw, self.lstat(fd, rel)

    def open_root(self, *_args):
        out = os.dup(self.fd)
        self.prefixes[out] = ""
        return out

    def open_dir(self, fd, rel):
        path = self.path(fd, rel)
        if path not in self.dirs:
            raise _journal.JournalError("fixture directory absent: " + path)
        out = os.dup(self.fd)
        self.prefixes[out] = path + "/"
        return out

    def listdir(self, fd):
        prefix = self.prefixes[fd]
        return list(dict.fromkeys(
            p[len(prefix):].split("/", 1)[0]
            for p in self.files if p.startswith(prefix)))

    def __enter__(self):
        self.stack = contextlib.ExitStack()
        # Real descriptors keep close/dup ownership honest; all fixture data stays in memory.
        self.root = Path(__file__).resolve().parent
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.stack.callback(os.close, self.fd)
        self.res = SimpleNamespace(status=_opf_store.RESOLVED, store_root=self.root,
                                   pointer_source="default", machine_rel=M)
        for obj, name, value in (
                (_journal, "_lstat_contained", self.lstat),
                (_journal, "_read_contained", self.read),
                (_journal, "_open_dir_contained", self.open_dir),
                (os, "listdir", self.listdir),
                (_opf_store, "_open_store_root_fd", self.open_root),
                (_opf_store, "_open_root_fd", self.open_root),
                (_opf_store, "resolve_store", lambda *_: self.res)):
            self.stack.enter_context(patch.object(obj, name, value))
        return self

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


def _error(call):
    try:
        call()
    except Exception as exc:
        return str(exc)
    return None


def _doctor(fx, archive=False):
    rep = _opf_check._Report()
    _opf_check._gather_worklog(
        fx.fd, ARCHIVE if archive else LEGACY, frozenset(), rep, required=not archive,
        machine_rel=None if archive else M)
    return rep.cannot


def _readers(fx):
    return {
        "loader": lambda: _error(lambda: wl.load_worklog_at(fx.fd, M)),
        "views": lambda: _error(lambda: _opf_views._load_worklog(
            fx.fd, LEGACY, frozenset(), [])),
        "import": lambda: _error(lambda: _opf_import._worklog_ids(fx.fd, M)),
        "changelog": lambda: _opf_changelog._load_inputs(fx.res, fx.root)[-1],
        "absorb": lambda: (_opf_absorb.evaluate(fx.root).findings or [None])[0],
    }


def _manifest_read_regressions(check):
    """Fail the loader's manifest read after a validated generation-1 resolution.

    Import reads the manifest before entering the loader, so arm the fault only
    at loader entry. A blanket manifest fault would pass there even with the
    loader's old wrapper restored and would not discriminate this regression.
    """
    import _opf_init

    # Literal diagnostics from 32fcfbc2dba710eab3d03e53ad77fa754461fe50:
    # _opf_store.py:550-563; _opf_changelog.py:469-478 (also absorb);
    # _opf_views.py:157-195,1582-1583; _opf_import.py:486-497,933-934;
    # _opf_check.py:469-477,2436-2437. These are manifest diagnostics:
    # the baseline worklog-only helpers did not themselves read the manifest.
    expected = {
        "loader": "cannot read m/manifest.toml (fixture unreadable)",
        "changelog": "cannot read m/manifest.toml (fixture unreadable)",
        "absorb": "cannot read m/manifest.toml (fixture unreadable)",
        "views": "cannot read m/manifest.toml (fixture unreadable)",
        "import": "cannot read m/manifest.toml (fixture unreadable)",
        "doctor": ["cannot read m/manifest.toml: cannot read m/manifest.toml (fixture unreadable)"],
    }
    for caller, baseline in expected.items():
        label = "F1-baseline-manifest-unreadable-" + caller
        with _Fixture() as fx:
            manifest_rel = M + "/" + _opf_store.MANIFEST_NAME
            fx.files[manifest_rel] = _opf_init.build_manifest().encode("utf-8")
            observed = _opf_store._read_toml_contained(fx.fd, manifest_rel)
            check(label + "-validated",
                  fx.res.status == _opf_store.RESOLVED
                  and _opf_store.validate_manifest(observed).status == _opf_store.VALID
                  and wl.generation(observed) == 1
                  and "worklog" not in observed["opf"])
            armed, attempted = False, []
            original_load = wl.load_worklog_at

            def fail_read(fd, rel, **kwargs):
                path = fx.path(fd, rel)
                if armed:
                    attempted.append(path)
                    if path == manifest_rel:
                        raise PermissionError("fixture unreadable")
                return fx.read(fd, rel, **kwargs)

            def load_after_resolution(*args, **kwargs):
                nonlocal armed
                armed = True
                return original_load(*args, **kwargs)

            readers = _readers(fx)
            readers["doctor"] = lambda: _doctor(fx)
            with patch.object(_journal, "_read_contained", side_effect=fail_read), \
                    patch.object(wl, "load_worklog_at",
                                 side_effect=load_after_resolution) as intake:
                actual = readers[caller]()
            check(label, actual == baseline and intake.call_count == 1
                  and attempted == [manifest_rel])


def _manifest_intake_regressions(check):
    """The post-resolution intake class, not just the last reported sibling.

    Oracle: 32fcfbc2dba710eab3d03e53ad77fa754461fe50, _opf_store.py
    _read_toml_contained/load_manifest/validate_manifest; _opf_views.py
    _read_raw_and_parsed/plan_views; _opf_import.py _require_inline_layout;
    _opf_changelog.py _load_inputs; _opf_check.py _validate_opened_store.
    The old worklog-only helpers did not read a manifest: loader uses the
    store manifest oracle. Non-table parser results are defensive seam tests.
    Views had no single-link or 1-MiB store cap: those cases deliberately keep
    U1's fail-closed refusal, NOT a claim of baseline diagnostic parity.
    Primitive faults model the reader's branches; this is not a filesystem
    race/hardlink enforcement test. Production parsing and translation run,
    except for defensive non-table and deterministic parser-limit seams.
    """
    import _opf_init

    p = "m/manifest.toml"
    identify = "[opf].standard is absent or is not 'opf' (not identifiably a opf store)"
    exotic = p + " is present but is not a regular file (an exotic entry; fail-closed, never opened)"
    # Each row is (mode, fault boundary, injected value, literal U1 diagnostic).
    cases = [
        ("absent", "stat", None, None),
        ("stat-permission", "stat", PermissionError("fixture stat denied"),
         "cannot stat m/manifest.toml (fixture stat denied)"),
        ("stat-journal", "stat", _journal.JournalError("fixture parent refused"),
         "cannot stat m/manifest.toml (fixture parent refused)"),
        ("unreadable", "read", PermissionError("fixture unreadable"),
         "cannot read m/manifest.toml (fixture unreadable)"),
        ("read-oserror", "read", OSError("fixture I/O error"),
         "cannot read m/manifest.toml (fixture I/O error)"),
        ("read-journal", "read", _journal.JournalError("fixture contained read refused"),
         "cannot read m/manifest.toml (fixture contained read refused)"),
        ("vanished-after-stat", "read",
         _journal.JournalError("cannot read contained file 'm/manifest.toml' (fixture vanished)"),
         "cannot read m/manifest.toml (cannot read contained file 'm/manifest.toml' (fixture vanished))"),
        ("parent-symlink", "stat",
         _journal.JournalError("cannot open contained directory component 'm' of 'm/manifest.toml' (fixture symlink)"),
         "cannot stat m/manifest.toml (cannot open contained directory component 'm' of 'm/manifest.toml' (fixture symlink))"),
        ("raced-symlink", "read",
         _journal.JournalError("cannot read contained file 'm/manifest.toml' (fixture symlink)"),
         "cannot read m/manifest.toml (cannot read contained file 'm/manifest.toml' (fixture symlink))"),
        ("raced-special", "read",
         _journal.JournalError("contained path 'm/manifest.toml' is not a regular file"),
         "cannot read m/manifest.toml (contained path 'm/manifest.toml' is not a regular file)"),
        ("hardlink", "read",
         _journal.JournalError("contained control file 'm/manifest.toml' has 2 hard links; refusing to read a "
                              "multiply-linked control file (a hardlink to an out-of-tree victim, never "
                              "our singly-linked control file)"),
         "cannot read m/manifest.toml (contained control file 'm/manifest.toml' has 2 hard links; refusing to read a "
         "multiply-linked control file (a hardlink to an out-of-tree victim, never our singly-linked control file))"),
        ("oversized", "size", 1048577,
         "m/manifest.toml is 1048577 bytes, over the 1048576-byte store-read cap (fail-closed)"),
        ("raced-store-cap", "read", b"#" + b" " * 1048576,
         "m/manifest.toml read 1048577 bytes, over the 1048576-byte store-read cap "
         "(a raced swap or growth past the pre-open size; fail-closed)"),
        ("stream-cap", "read",
         _journal.JournalError("contained file exceeds the 16777216-byte read cap (fail-closed)"),
         "cannot read m/manifest.toml (contained file exceeds the 16777216-byte read cap (fail-closed))"),
        ("malformed", "read", b"not TOML [",
         "cannot parse m/manifest.toml (Expected '=' after a key in a key/value pair (at line 1, column 5))"),
        ("non-utf8", "read", b"\xff",
         "cannot parse m/manifest.toml ('utf-8' codec can't decode byte 0xff in position 0: invalid start byte)"),
        ("parse-value", "parse", ValueError("fixture integer limit"),
         "cannot parse m/manifest.toml (fixture integer limit)"),
        ("parse-recursion", "parse", RecursionError("fixture nesting limit"),
         "cannot parse m/manifest.toml (input nesting is too deep; present but unparseable): fixture nesting limit"),
        ("top-level-type", "parse", [], "manifest is not a table"),
        ("opf-absent", "read", b"schema = 1\n", identify),
        ("opf-not-table", "read", b"opf = 1\n", identify),
    ]
    for mode, bits in (("symlink", stat.S_IFLNK), ("fifo", stat.S_IFIFO),
                       ("socket", stat.S_IFSOCK), ("directory", stat.S_IFDIR),
                       ("block-device", stat.S_IFBLK), ("char-device", stat.S_IFCHR)):
        cases.append((mode, "stat", SimpleNamespace(st_mode=bits, st_size=0), exotic))

    for mode, phase, value, message in cases:
        expected = dict.fromkeys(("loader", "views", "import", "changelog", "absorb"), message)
        expected["doctor"] = ["cannot read {}: {}".format(p, message)]
        if mode == "absent":
            expected.update(
                loader=p + " vanished after discovery",
                views=p + " vanished after discovery",
                import_=p + ": the store manifest is absent; the storage layout cannot be determined (spec 9)",
                changelog="manifest.toml is absent from the resolved store (a required input; fail-closed, spec 9)",
                doctor=[p + " is absent (the store manifest is required; spec 4.5)"])
            expected["import"] = expected.pop("import_")
            expected["absorb"] = expected["changelog"]
        elif mode in ("top-level-type", "opf-absent", "opf-not-table"):
            expected["views"] = "manifest is not valid: " + message
            expected["changelog"] = ("manifest.toml does not validate against the manifest schema: "
                                     + message + " (fail-closed, spec 4.5/9)")
            expected["absorb"] = expected["changelog"]
            expected["doctor"] = [p + ": " + message]
            expected["import"] = (
                "store manifest is not VALID (CANNOT-EVALUATE: manifest is not a table)"
                if mode == "top-level-type" else
                "m/manifest.toml: storage layout None is unsupported; U7's inline active-store readers stage only "
                "an `inline`-layout store (spec 9), so a non-inline layout is fail-closed (never a "
                "partial inline read that would miss per-record ids or admit a phantom target)")
        elif message == exotic:
            expected["views"] = (p + " is present but is not a regular file "
                                 "(a FIFO, device, socket, or directory; fail-closed, never opened)")
        elif mode == "parse-recursion":
            expected["views"] = "cannot parse m/manifest.toml (fixture nesting limit)"

        for caller, baseline in expected.items():
            label = "F1-manifest-{}-{}".format(mode, caller)
            with _Fixture() as fx:
                fx.files[p] = _opf_init.build_manifest().encode("utf-8")
                observed = _opf_store._read_toml_contained(fx.fd, p)
                check(label + "-validated",
                      fx.res.status == _opf_store.RESOLVED
                      and _opf_store.validate_manifest(observed).status == _opf_store.VALID
                      and wl.generation(observed) == 1 and "worklog" not in observed["opf"])
                armed, attempted = False, []
                original_load = wl.load_worklog_at
                original_parse = _opf_store.tomllib.loads

                def result(item):
                    if isinstance(item, Exception):
                        raise item
                    return item

                def probe(fd, rel):
                    path = fx.path(fd, rel)
                    if armed:
                        attempted.append(path)
                    return armed and path == p

                def lstat(fd, rel):
                    if probe(fd, rel):
                        if phase == "stat":
                            return result(value)
                        if phase == "size":
                            return SimpleNamespace(st_mode=stat.S_IFREG, st_size=value)
                    return fx.lstat(fd, rel)

                def read(fd, rel, **kwargs):
                    if probe(fd, rel) and phase == "read":
                        return result(value), fx.lstat(fd, rel)
                    return fx.read(fd, rel, **kwargs)

                def parse(raw, **kwargs):
                    if armed and phase == "parse" and raw == fx.files[p].decode("utf-8"):
                        return result(value)
                    return original_parse(raw, **kwargs)

                def load_after_resolution(*args, **kwargs):
                    nonlocal armed
                    armed = True
                    return original_load(*args, **kwargs)

                readers = _readers(fx)
                readers["doctor"] = lambda: _doctor(fx)
                with patch.object(_journal, "_lstat_contained", side_effect=lstat), \
                        patch.object(_journal, "_read_contained", side_effect=read), \
                        patch.object(_opf_store.tomllib, "loads", side_effect=parse), \
                        patch.object(wl, "load_worklog_at", side_effect=load_after_resolution) as intake:
                    actual = readers[caller]()
                check(label, actual == baseline and intake.call_count == 1
                      and bool(attempted) and set(attempted) == {p})


def _upgrade_preflight_regressions(check, fence):
    """Exercise both upgrade origins through the CLI, with real git/store bytes.

    Refusal snapshots include every non-git path and the git index. A separate
    lease spy detects even an acquire/release cycle that leaves no final bytes.
    No production I/O or validator is replaced in the end-to-end cases.
    """
    import io
    import shutil
    import subprocess
    import sys
    import tempfile
    import tomllib

    import _opf_emit
    import _opf_init
    import check_opf_upgrade as fixtures
    import opf

    if opf._bootstrap() != 0:
        raise RuntimeError("upgrade regression bootstrap failed")
    git = shutil.which("git", path=os.defpath)
    if git is None:
        raise RuntimeError("upgrade regression requires git")
    cli = Path(__file__).resolve().with_name("opf.py")

    def snapshot(root):
        # os.walk's onerror prevents an unreadable subtree passing as empty.
        def unreadable(exc):
            raise exc
        result = {}
        for parent, dirs, files in os.walk(root, onerror=unreadable):
            if Path(parent) == root:
                dirs.remove(".git")
            for name in dirs + files:
                path = Path(parent) / name
                rel = path.relative_to(root).as_posix()
                mode = path.lstat().st_mode
                if stat.S_ISDIR(mode):
                    result[rel] = ("dir",)
                elif stat.S_ISREG(mode):
                    result[rel] = ("file", path.read_bytes())
                else:
                    raise RuntimeError("unexpected fixture path: " + rel)
        return result, (root / ".git/index").read_bytes()

    with tempfile.TemporaryDirectory(prefix="opf-worklog-upgrade-") as temporary:
        base = Path(temporary).resolve()
        home = base / "home"
        home.mkdir()
        # No inherited GIT_* redirection, config, hooks, or author identity.
        env = {"PATH": os.defpath, "HOME": str(home), "XDG_CONFIG_HOME": str(home),
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
               "LC_ALL": "C", "TZ": "UTC", "TMPDIR": str(base)}

        def git_call(root, *args):
            subprocess.run(
                [git, "-C", str(root), "-c", "init.templateDir=",
                 "-c", "init.defaultBranch=main", "-c", "core.hooksPath=" + str(home),
                 "-c", "commit.gpgSign=false", "-c", "user.name=OPF fixture",
                 "-c", "user.email=fixture@example.invalid", *args],
                env=env, cwd=base, check=True, capture_output=True, timeout=30)

        for origin in ("1.0.0", "1.1.0"):
            for unsupported in (True, False):
                label = "F5-upgrade-" + origin
                root = base / (origin + ("-future" if unsupported else "-legacy"))
                machine = root / ".working/toml"
                machine.mkdir(parents=True)
                if origin == "1.0.0":
                    # Frozen legacy bytes shared with the existing upgrade gate.
                    manifest = tomllib.loads(fixtures._FIX_MANIFEST)
                    counters = fixtures._FIX_COUNTERS
                    indexes = fixtures._FIX_INDEX_TYPES
                    token = "devprocess"
                else:
                    manifest = tomllib.loads(_opf_init.build_manifest())
                    manifest["opf"]["spec_version"] = origin
                    counters = _opf_init.build_counters()
                    indexes = _opf_init.INDEX_TYPES
                    token = "opf"
                if unsupported:
                    manifest[token]["worklog"] = 2
                    (machine / "worklog").mkdir()
                else:
                    check(label + "-legacy-key-absent", "worklog" not in manifest[token])
                    (machine / "worklog.toml").write_text(fixtures._FIX_WORKLOG, encoding="utf-8")
                (machine / "manifest.toml").write_text(_opf_emit.emit_checked(manifest), encoding="utf-8")
                (machine / "counters.toml").write_text(counters, encoding="utf-8")
                (machine / "version.toml").write_text(fixtures._FIX_VERSION, encoding="utf-8")
                for name in indexes:
                    (machine / (name + ".index.toml")).write_text(fixtures._FIX_INDEX, encoding="utf-8")
                (root / _opf_store.POINTER_REL).write_text('[store]\ntarget = "dir:."\n', encoding="utf-8")
                (root / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
                git_call(root, "init")
                git_call(root, "--literal-pathspecs", "add", "--", ".working",
                         _opf_store.POINTER_REL, "CHANGELOG.md")
                git_call(root, "commit", "-m", "seed upgrade regression")
                before, index_before = snapshot(root)
                if unsupported:
                    with patch.dict(os.environ, env, clear=True), \
                            patch.object(opf, "_upgrade_acquire_lease",
                                         side_effect=opf._UpgradeError("lease reached")) as lease, \
                            contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(io.StringIO()):
                        message = _error(lambda: opf._upgrade_run(str(root)))
                    check(label + "-before-lease", message == fence and lease.call_count == 0)
                proc = subprocess.run(
                    [sys.executable, "-I", "-B", str(cli), "upgrade", "--root", str(root)],
                    env=env, cwd=base, capture_output=True, text=True, timeout=120)
                after, index_after = snapshot(root)
                if unsupported:
                    check(label + "-refuses-generation", proc.returncode == 2
                          and fence in proc.stderr)
                    check(label + "-store-byte-identical", after == before)
                    check(label + "-git-index-byte-identical", index_after == index_before)
                else:
                    upgraded = tomllib.loads((machine / "manifest.toml").read_text(encoding="utf-8"))
                    check(label + "-legacy-succeeds", proc.returncode == 0
                          and "doctor-VALID" in proc.stdout
                          and upgraded["opf"]["spec_version"] == _opf_store.SUPPORTED_SPEC_VERSION)
                    check(label + "-legacy-generation-one", "worklog" not in upgraded["opf"]
                          and wl.generation(upgraded) == 1)


def self_test():
    failures, checks = [], []

    def check(name, ok):
        checks.append(name)
        if not ok:
            failures.append(name)

    # Baseline 32fcfbc2dba710eab3d03e53ad77fa754461fe50:
    # _opf_views.py:230-245; _opf_check.py:469-488,1132-1156;
    # _opf_import.py:486-506,896-919; _opf_changelog.py:451-555.
    # Literal expected bytes, independently read from that revision's translators.
    missing = {
        "views": "declared source m/worklog.toml is missing (the worklog ledger must exist)",
        "doctor": ["m/worklog.toml is absent (the active worklog ledger is required; spec 6.2)"],
        "import": None,
        "changelog": "worklog.toml is absent from the resolved store (a required input; fail-closed, spec 6.2)",
    }
    parse = "cannot parse m/worklog.toml (Expected '=' after a key in a key/value pair (at line 1, column 5))"
    unreadable = "cannot read m/worklog.toml (fixture unreadable)"
    for label, raw, expected in (
            ("missing", None, missing),
            ("malformed", b"not TOML [", {
                "views": parse, "doctor": ["cannot read m/worklog.toml: " + parse],
                "import": parse, "changelog": parse}),
            ("unreadable", PermissionError("fixture unreadable"), {
                "views": unreadable, "doctor": ["cannot read m/worklog.toml: " + unreadable],
                "import": unreadable, "changelog": unreadable})):
        with _Fixture() as fx:
            if raw is None:
                del fx.files[LEGACY]
            else:
                fx.files[LEGACY] = raw
            readers = _readers(fx)
            readers["doctor"] = lambda: _doctor(fx)
            for caller, baseline in expected.items():
                check("F1-baseline-" + label + "-" + caller, readers[caller]() == baseline)
            # Absorb delegates to the same intake and must preserve its bytes too.
            check("F1-baseline-" + label + "-absorb", readers["absorb"]() == expected["changelog"])
            if raw is not None:
                check("F1-baseline-" + label + "-loader", readers["loader"]() == expected["changelog"])
            else:
                check("F1-optional-loader-missing", wl.load_worklog_at(fx.fd, M, required=False) is None)

    _manifest_read_regressions(check)
    _manifest_intake_regressions(check)

    archive_parse = ("cannot parse m/archive/2026/worklog.toml "
                     "(Expected '=' after a key in a key/value pair (at line 1, column 5))")
    archive_unreadable = "cannot read m/archive/2026/worklog.toml (fixture unreadable)"
    for label, raw, message in (
            ("missing", None, None),
            ("malformed", b"not TOML [", archive_parse),
            ("unreadable", PermissionError("fixture unreadable"), archive_unreadable)):
        with _Fixture() as fx:
            if raw is not None:
                fx.files[ARCHIVE] = raw
            check("F1-baseline-archive-" + label + "-loader",
                  _error(lambda: wl.load_archive_worklog_at(fx.fd, ARCHIVE)) == message)
            expected = [] if message is None else ["cannot read m/archive/2026/worklog.toml: " + message]
            check("F1-baseline-archive-" + label + "-doctor", _doctor(fx, archive=True) == expected)

    for ref in ("WL-1\u0662", "WL-1\u0662.abcd", "WL-1\uff12.abcd"):
        check("F3-ascii-ref-" + ref, wl._valid_wl_ref(ref) is None)
        check("F3-ascii-filename-" + ref, wl.parse_worklog_filename(ref + ".toml") is None)

    fence = ("[opf].worklog = 2 is not supported by this build "
             "(maximum supported worklog generation: 1)")
    check("F5-shipped-ceiling", _opf_store.SUPPORTED_WORKLOG == 1)
    with _Fixture(2) as fx:
        for caller, read in _readers(fx).items():
            check("F5-production-" + caller, read() == fence)
        check("F5-production-doctor", _doctor(fx) == [fence])
        mv = _opf_store.validate_manifest(fx.manifest)
        check("F5-production-manifest", mv.status == _opf_store.CANNOT_EVALUATE
              and mv.findings == [fence])

    _upgrade_preflight_regressions(check, fence)

    with patch.object(_opf_store, "SUPPORTED_WORKLOG", 2):
        for gen in (1, 2):
            with _Fixture(gen) as fx:
                fx.files[LEGACY] = LEDGER
                fx.dirs.add("m/worklog")
                conflict = ("m/" + ("worklog" if gen == 1 else "worklog.toml")
                            + " conflicts with the manifest-selected worklog shape")
                for caller, read in _readers(fx).items():
                    check("F2-mixed-{}-{}".format(gen, caller), read() == conflict)
                if gen == 1:
                    # The doctor's explicit policy still reads the manifest-selected ledger.
                    check("F2-doctor-legacy-intake", _doctor(fx) == [])
                    check("F2-doctor-legacy-view", _opf_views._load_worklog(
                        fx.fd, LEGACY, frozenset(), [],
                        on_legacy_conflict=_opf_check._worklog_legacy_conflict)[0] == LEDGER)
                else:
                    check("F2-doctor-gen2-conflict", _doctor(fx) == [conflict])

        with _Fixture(2) as fx:
            check("F5-explicit-test-activation", wl.load_worklog_at(fx.fd, M)["entry"][0]["id"] == "WL-1")
            # Behavioral route assertions fail at their own name, before any unrelated
            # ledger/manifest validation can mask a bypass.
            sentinel = "fixture worklog route"
            with patch.object(wl, "load_worklog_at", side_effect=wl.WorklogError(sentinel)) as intake:
                check("F3-changelog-route",
                      _opf_changelog._load_inputs(fx.res, fx.root)[-1] == sentinel
                      and intake.call_count == 1)
            with patch.object(wl, "load_worklog_at", side_effect=wl.WorklogError(sentinel)) as intake:
                check("F3-absorb-route", _opf_absorb.evaluate(fx.root).findings == [sentinel]
                      and intake.call_count == 1)
            with patch.object(wl, "load_worklog_at", side_effect=wl.WorklogError(sentinel)) as intake:
                check("F3-doctor-route", _doctor(fx) == [sentinel] and intake.call_count == 1)

            fx.files["m/worklog/WL-10.toml"] = BODY.replace(b"WL-1", b"WL-10")
            fx.files["m/worklog/WL-2.abcd.toml"] = BODY.replace(b"WL-1", b"WL-2.abcd")
            fx.files["m/worklog/WL-2.0001.toml"] = BODY.replace(b"WL-1", b"WL-2.0001")
            fx.files["m/worklog/WL-2.toml"] = BODY.replace(b"WL-1", b"WL-2")
            want = ["WL-1", "WL-2", "WL-2.0001", "WL-2.abcd", "WL-10"]
            raw, data = wl.load_worklog_at(fx.fd, M, with_raw=True)
            check("F4-numeric-suffix-order", [e["id"] for e in data["entry"]] == want)
            want_raw = b""
            for ident in want:
                name = (ident + ".toml").encode()
                body = fx.files["m/worklog/" + name.decode()]
                want_raw += str(len(name)).encode() + b":" + name
                want_raw += str(len(body)).encode() + b":" + body
            check("F4-source-byte-order", raw == want_raw)
            message = _error(lambda: _opf_import._worklog_ids(fx.fd, M))
            check("F7-selected-display-path",
                  message is not None and message.startswith("m/worklog: worklog does not satisfy"))

        with _Fixture(2) as fx:
            # Valid TOML and a matching id: only the filename grammar should reject it.
            del fx.files["m/worklog/WL-1.toml"]
            fx.files["m/worklog/WL-1.TOML"] = BODY
            check("F3-directory-closure",
                  _error(lambda: wl.load_worklog_at(fx.fd, M))
                  == "m/worklog/WL-1.TOML is not a worklog filename")

        # Exercise the doctor's directory walk, not only its pure leaf classifier.
        for gen in (1, 2):
            with _Fixture(gen) as fx:
                rep = _opf_check._Report()
                def listing(_fd, rel, _rep):
                    return {
                        ".working": (["toml"], []),
                        ".working/toml": (["worklog"], []),
                        ".working/toml/worklog": ([], ["WL-1.toml"]),
                    }[rel]
                with patch.object(_opf_check, "_list_dir", listing):
                    _opf_check._check_containment(fx.fd, ".working/toml", fx.manifest, "clean", rep)
                if gen == 1:
                    check("F2-doctor-containment-classification", not rep.cannot
                          and any("unregistered path '.working/toml/worklog'" in x for x in rep.findings))
                else:
                    check("F3-doctor-directory-route", not rep.cannot and not rep.findings)

    with _Fixture() as fx:
        bad = {"opf": {"worklog": "bad", "layout": "inline"}}
        cls = _opf_check.classify_containment(bad, M)
        check("F6-generation-classification", not cls.malformed and bool(cls.worklog_errors))
        msg = _error(lambda: _opf_ingest._managed_paths(fx.res, bad))
        check("F6-ingest-attribution", msg is not None
              and "worklog generation cannot be evaluated" in msg and "[unmanaged]" not in msg)

    for name in failures:
        print("OPF-WORKLOG REGRESSION: FAIL " + name)
    if not failures:
        print("OPF-WORKLOG REGRESSION: PASS ({} boundary assertions)".format(len(checks)))
    return int(bool(failures))
