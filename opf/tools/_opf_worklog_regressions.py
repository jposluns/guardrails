#!/usr/bin/env python3
"""Bounded regressions for the PR-1 intake boundary.

The fixture replaces contained filesystem primitives, not the loader or TOML
parser. Upgrade additionally uses isolated on-disk stores and the real CLI.
Permission failures are injected at read time so these tests also work
as root. Production callers and their exception translations still execute.

Doctor intentionally stops after manifest failure: C-MANIFEST owns the original
findings, and an INVALID manifest produces one C-RECORDS cannot-evaluate naming
the unread active source. It no longer grades an unread ledger as empty. A later
intake failure also stops traversal before archive reads. Independently invoked
archive intake retains its fixed legacy shape and has no manifest contract.
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
import _opf_write_guard

M = "m"
LEGACY = "m/worklog.toml"
ARCHIVE = "m/archive/2026/worklog.toml"
BODY = (b'id = "WL-1"\ndate = "2026-01-01T00:00:00Z"\n'
        b'actor = {kind = "maintainer"}\nkind = "fixed"\nsummary = "x"\n')
LEDGER = b"schema = 1\n[[entry]]\n" + BODY


class _Fixture:
    def __init__(self, gen=1):
        import _opf_emit
        import _opf_init
        self.manifest = _opf_store.tomllib.loads(_opf_init.build_manifest())
        if gen != 1:
            self.manifest["opf"]["worklog"] = gen
        self.files = {
            "m/manifest.toml": _opf_emit.emit_checked(self.manifest).encode(),
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
            for p in list(self.files) + sorted(self.dirs)
            if p.startswith(prefix) and p != prefix.rstrip("/")))

    def list_contained(self, fd, rel):
        path = self.path(fd, rel)
        if path not in self.dirs:
            return None, None
        prefix = path + "/"
        return (sorted(p[len(prefix):] for p in self.dirs
                       if p.startswith(prefix) and "/" not in p[len(prefix):]),
                sorted(p[len(prefix):] for p in self.files
                       if p.startswith(prefix) and "/" not in p[len(prefix):]))

    def __enter__(self):
        self.stack = contextlib.ExitStack()
        # Real descriptors keep close/dup ownership honest; all fixture data stays in memory.
        self.root = Path(__file__).resolve().parent
        self.fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.stack.callback(os.close, self.fd)
        self.res = SimpleNamespace(status=_opf_store.RESOLVED, store_root=self.root,
                                   pointer_source="default", machine_rel=M)
        for obj, name, value in (
                (_opf_check, "_list_contained", self.list_contained),
                (_opf_check, "_open_store_root_fd", self.open_root),
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


def _doctor_leaf(fx, archive=False):
    rep = _opf_check._Report()
    _opf_check._gather_worklog(
        fx.fd, ARCHIVE if archive else LEGACY, frozenset(), rep, required=not archive,
        machine_rel=None if archive else M)
    return rep.cannot + rep.findings


def _helper_readers(fx):
    return {
        "loader": lambda: _error(lambda: wl.load_worklog_at(fx.fd, M)),
        "views": lambda: _error(lambda: _opf_views._load_worklog(
            fx.fd, LEGACY, frozenset(), [])),
        "import": lambda: _error(lambda: _opf_import._worklog_ids(fx.fd, M)),
        "changelog": lambda: _opf_changelog._load_inputs(fx.res, fx.root)[-1],
        "absorb": lambda: (_opf_absorb.evaluate(fx.root).findings or [None])[0],
    }


def _doctor_entry(fx, supported_profiles=None):
    rep = _opf_check._Report()
    fx.profile_scope = _opf_check._validate_opened_store(
        fx.fd, None, M, supported_profiles, _opf_check._normalize_observations(None)[0],
        None, "default", True, rep)
    fx.manifest_findings = rep.by_check.get("C-MANIFEST", [])
    return rep.cannot + rep.findings


def _doctor_public(fx, supported_profiles=None):
    result = _opf_check.validate_store(fx.res, supported_profiles)
    fx.profile_scope = (result.evaluated_profiles, result.unevaluated_profiles)
    fx.manifest_findings = result.by_check.get("C-MANIFEST", [])
    return result.cannot_evaluate + result.findings


def _manifest_readers(fx, supported_profiles=None):
    """Public boundaries. The leaf compatibility tests above are not matrix columns."""
    import _opf_init
    for name in _opf_init.INDEX_TYPES:
        fx.files.setdefault(M + "/" + name + ".index.toml", b"schema = 1\nrecord = []\n")
    fx.files.setdefault(M + "/counters.toml", _opf_init.build_counters().encode())
    fx.files.setdefault(ARCHIVE, LEDGER)
    fx.files.setdefault(M + "/archive/2026/archive.toml", b"schema = 1\n")
    return {
        "loader": lambda: _error(lambda: wl.load_worklog(fx.res)),
        "views": lambda: _error(lambda: _opf_views.plan_views(fx.fd, M)),
        "plan_views": lambda: _error(lambda: _opf_views.plan_views(fx.fd, M)),
        "changelog": lambda: (_opf_changelog.evaluate(fx.root).findings or [None])[0],
        "absorb": lambda: (_opf_absorb.evaluate(fx.root).findings or [None])[0],
        "doctor": lambda: _doctor_entry(fx, supported_profiles),
    }


def _manifest_read_regressions(check):
    """Fail the loader's manifest read after a validated generation-1 resolution.

    Doctor and views own one initial manifest model; fault that first read.
    Other wrappers still enter standalone intake after resolution, so inject
    there. Late changes to a shared model are covered by the snapshot cases.
    """
    import _opf_init

    # Literal diagnostics from 32fcfbc2dba710eab3d03e53ad77fa754461fe50:
    # _opf_store.py:550-563; _opf_changelog.py:469-478 (also absorb);
    # _opf_views.py:157-195,1582-1583; _opf_check.py:469-477,2436-2437.
    # These are manifest diagnostics: the baseline worklog-only helpers did
    # not themselves read the manifest.
    expected = {
        "loader": "cannot read m/manifest.toml (fixture unreadable)",
        "changelog": "cannot read m/manifest.toml (fixture unreadable)",
        "absorb": "cannot read m/manifest.toml (fixture unreadable)",
        "views": "cannot read m/manifest.toml (fixture unreadable)",
        "plan_views": "cannot read m/manifest.toml (fixture unreadable)",
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
            armed, attempted = caller in ("views", "plan_views", "doctor"), []
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

            readers = _manifest_readers(fx)
            with patch.object(_journal, "_read_contained", side_effect=fail_read), \
                    patch.object(wl, "load_worklog_at",
                                 side_effect=load_after_resolution) as intake:
                actual = readers[caller]()
            check(label, actual == baseline
                  and intake.call_count == (0 if caller in ("views", "plan_views", "doctor") else 1)
                  and attempted == [manifest_rel])


def _manifest_diagnostics(data, validation):
    """32fcfbc manifest translations.

    _opf_views.plan_views; _opf_changelog._load_inputs (also absorb);
    _opf_import._worklog_ids (helper column only);
    _opf_check._validate_opened_store. Loader uses U1's findings.
    """
    message = "; ".join(validation.findings)
    expected = {
        "loader": message,
        "views": "manifest is not valid: " + message,
        "changelog": ("manifest.toml does not validate against the manifest schema: "
                      + message + " (fail-closed, spec 4.5/9)"),
        "doctor": (["m/manifest.toml: " + message]
                   if validation.status == _opf_store.CANNOT_EVALUATE else
                   ["active worklog source under m is not evaluated: m/manifest.toml failed manifest "
                    "validation (see C-MANIFEST)"]
                   + ["manifest: " + finding for finding in validation.findings]),
        "import": "store manifest is not VALID ({}: {})".format(validation.status, message),
    }
    expected["absorb"] = expected["changelog"]
    expected["plan_views"] = expected["views"]
    return expected


def _manifest_intake_regressions(check):
    """The post-resolution intake class, not just the last reported sibling.

    Oracle: 32fcfbc2dba710eab3d03e53ad77fa754461fe50, _opf_store.py
    _read_toml_contained/load_manifest/validate_manifest; _opf_views.py
    _read_raw_and_parsed/plan_views; _opf_changelog.py _load_inputs;
    _opf_check.py _validate_opened_store.
    The old worklog-only helpers did not read a manifest: loader uses the
    store manifest oracle. Non-table parser results are defensive seam tests.
    Views had no single-link or 1-MiB store cap: those cases deliberately keep
    U1's fail-closed refusal, NOT a claim of baseline diagnostic parity.
    Intentional multi-fault divergence from 32fcfbc: a manifest failure now
    outranks an absent, unreadable, or unparseable worklog ledger. Baseline read that ledger
    first; reproducing its precedence would violate the absolute prohibition on
    probing a worklog source after manifest failure. Version absence still wins
    over manifest absence/schema failure using the version already read; manifest
    read/parse failures retain their baseline priority over version absence.
    Primitive faults model the reader's branches; this is not a filesystem
    race/hardlink enforcement test. Production parsing and translation run,
    except for defensive non-table and deterministic parser-limit seams.
    """
    import _opf_emit
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
        ("opf-empty", "read", b"[opf]\n", identify),
        ("standard-absent", "read", b'[opf]\nlayout = "inline"\n', identify),
        ("standard-wrong", "read", b'[opf]\nstandard = "other"\nlayout = "inline"\n', identify),
        ("standard-wrong-type", "read", b'[opf]\nstandard = 7\nlayout = "inline"\n', identify),
    ]
    for mode, bits in (("symlink", stat.S_IFLNK), ("fifo", stat.S_IFIFO),
                       ("socket", stat.S_IFSOCK), ("directory", stat.S_IFDIR),
                       ("block-device", stat.S_IFBLK), ("char-device", stat.S_IFCHR)):
        cases.append((mode, "stat", SimpleNamespace(st_mode=bits, st_size=0), exotic))

    from _opf_manifest_regressions import MANIFEST_CALLERS, manifest_cases
    with _Fixture() as fx:
        check("F1-manifest-caller-columns",
              set(_manifest_readers(fx)) == set(MANIFEST_CALLERS))
    for name, data, validation, control in manifest_cases(check):
        cases.append(("generated-" + name, "validation", (data, control), "; ".join(validation.findings)))

    for mode, phase, value, message in cases:
        expected = dict.fromkeys(("loader", "views", "changelog", "absorb"), message)
        expected["doctor"] = ["cannot read {}: {}".format(p, message)]
        if mode == "absent":
            expected.update(
                loader=p + " vanished after discovery",
                views=p + " vanished after discovery",
                changelog="manifest.toml is absent from the resolved store (a required input; fail-closed, spec 9)",
                doctor=[p + " is absent (the store manifest is required; spec 4.5)"])
            expected["absorb"] = expected["changelog"]
        elif phase == "validation" or mode in (
                "top-level-type", "opf-absent", "opf-not-table", "opf-empty",
                "standard-absent", "standard-wrong", "standard-wrong-type"):
            data = (_opf_store.tomllib.loads(value.decode()) if phase == "read" else
                    value[0] if phase == "validation" else value)
            control = value[1] if phase == "validation" else None
            expected = _manifest_diagnostics(data, _opf_store.validate_manifest(data, control))
            del expected["import"]  # Retired public staging (C2); F5 keeps the helper column.
        elif message == exotic:
            expected["views"] = (p + " is present but is not a regular file "
                                 "(a FIFO, device, socket, or directory; fail-closed, never opened)")
        elif mode == "parse-recursion":
            expected["views"] = "cannot parse m/manifest.toml (fixture nesting limit)"

        expected["plan_views"] = expected["views"]
        check("F1-manifest-{}-caller-columns".format(mode),
              set(expected) == set(MANIFEST_CALLERS))
        version_first = (mode == "absent" or phase == "validation"
                         or mode in ("top-level-type", "opf-absent", "opf-not-table",
                                     "opf-empty", "standard-absent", "standard-wrong",
                                     "standard-wrong-type"))

        control = value[1] if phase == "validation" else None
        if control is not None:
            # Only doctor accepts supported_profiles. Never change base-only intake.
            validation = _opf_store.validate_manifest(data, control)
            expected = {"doctor": (
                ["m/manifest.toml: " + message]
                if validation.status == _opf_store.CANNOT_EVALUATE else
                ["active worklog source under m is not evaluated: m/manifest.toml failed manifest "
                 "validation (see C-MANIFEST)"]
                + ["manifest: " + finding for finding in validation.findings])}
        for caller, baseline in expected.items():
            label = "F1-manifest-{}-{}".format(mode, caller)
            with _Fixture() as fx:
                fx.files[p] = _opf_init.build_manifest().encode("utf-8")
                observed = _opf_store._read_toml_contained(fx.fd, p)
                check(label + "-validated",
                      fx.res.status == _opf_store.RESOLVED
                      and _opf_store.validate_manifest(observed).status == _opf_store.VALID
                      and wl.generation(observed) == 1 and "worklog" not in observed["opf"])
                armed, attempted = caller in ("views", "plan_views", "doctor") or control is not None, []
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
                    if probe(fd, rel):
                        if mode == "hardlink" and not kwargs.get("require_single_link"):
                            return fx.read(fd, rel, **kwargs)
                        if phase == "read":
                            return result(value), fx.lstat(fd, rel)
                        if phase == "validation" and isinstance(value[0], dict):
                            return _opf_emit.emit_checked(value[0]).encode(), fx.lstat(fd, rel)
                    return fx.read(fd, rel, **kwargs)

                def parse(raw, **kwargs):
                    if armed and phase in ("parse", "validation") and raw == fx.files[p].decode("utf-8"):
                        return result(value[0] if phase == "validation" else value)
                    return original_parse(raw, **kwargs)

                def load_after_resolution(*args, **kwargs):
                    nonlocal armed
                    armed = True
                    return original_load(*args, **kwargs)

                source_reads = []
                original_read = read

                def instrumented_read(fd, rel, **kwargs):
                    if armed:
                        source_reads.append(fx.path(fd, rel))
                    return original_read(fd, rel, **kwargs)

                readers = _manifest_readers(fx, control)
                with patch.object(_journal, "_lstat_contained", side_effect=lstat), \
                        patch.object(_journal, "_read_contained", side_effect=instrumented_read), \
                        patch.object(_opf_store.tomllib, "loads", side_effect=parse), \
                        patch.object(wl, "load_worklog_at", side_effect=load_after_resolution) as intake:
                    actual = readers[caller]()
                    # Keep the single-fault observation before the multi-fault runs.
                    single_attempted = list(attempted)
                    single_intake = intake.call_count
                    if caller in ("changelog", "absorb"):
                        for secondary in ("version-absent", "worklog-absent",
                                          "worklog-unreadable"):
                            path = M + "/version.toml" if secondary == "version-absent" else LEGACY
                            saved = fx.files.pop(path)
                            if secondary == "worklog-unreadable":
                                fx.files[path] = PermissionError("fixture ledger unreadable")
                            attempted.clear()
                            armed = False
                            intake.reset_mock()
                            try:
                                multi = readers[caller]()
                            finally:
                                fx.files[path] = saved
                            wanted = (
                                "version.toml is absent from the resolved store (a required "
                                "input; fail-closed, spec 6.1)"
                                if secondary == "version-absent" and version_first else baseline)
                            check(label + "-" + secondary,
                                  multi == wanted and intake.call_count == 1
                                  and bool(attempted) and set(attempted) == {p})
                    attempted = single_attempted
                check(label, actual == baseline
                      and single_intake == (0 if caller in ("views", "plan_views", "doctor")
                                           or control is not None else 1)
                      and bool(attempted) and set(attempted) == {p})
                check(label + "-no-active-read", not any(
                    path == LEGACY or path.startswith(M + "/worklog/") for path in source_reads))
                check(label + "-no-archive-read", not any(
                    path.startswith(M + "/archive/") and path.endswith("/worklog.toml")
                    for path in source_reads))
                check(label + "-diagnostic", actual == baseline)
                check(label + "-manifest-only",
                      bool(attempted) and set(attempted) == {p})




def _entry_point_census(check):
    """Reconcile the first public boundaries against production source calls.

    Follow private callers back from both worklog-loading APIs, stopping at each
    public boundary. CLI/operation wrappers above those boundaries are delegated
    coverage. This static census covers literal module/function calls, not import
    aliases, dynamic dispatch, subprocess invocations, or generic tree copies.
    """
    import ast
    nodes = {}
    for path in Path(__file__).resolve().parent.iterdir():
        if (path.suffix != ".py" or not path.stem.startswith("_opf_")
                or path.stem.endswith("_regressions")):
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if (isinstance(node, ast.FunctionDef) and node.name != "main"
                    and not node.name.startswith("self_test")):
                nodes[path.stem + "." + node.name] = node
    reverse = {}
    for key, node in nodes.items():
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            fun = call.func
            if isinstance(fun, ast.Name):
                target = key.rsplit(".", 1)[0] + "." + fun.id
            elif isinstance(fun, ast.Attribute) and isinstance(fun.value, ast.Name):
                target = fun.value.id + "." + fun.attr
            else:
                continue
            if target in nodes:
                reverse.setdefault(target, set()).add(key)
    pending = ["_opf_worklog.load_worklog_at", "_opf_worklog.load_archive_worklog_at"]
    boundaries, seen = set(pending), set()
    while pending:
        target = pending.pop()
        if target in seen:
            continue
        seen.add(target)
        for caller in reverse.get(target, ()):
            if caller.rsplit(".", 1)[1].startswith("_"):
                pending.append(caller)
            else:
                boundaries.add(caller)
    # Every member has a public-entry case below, except the explicitly named
    # archive loader: it has no manifest contract and retains F1 archive cases.
    check("F2f-public-entry-census", boundaries == {
        "_opf_absorb.evaluate", "_opf_changelog.evaluate", "_opf_check.validate_store",
        "_opf_views.plan_views", "_opf_worklog.load_worklog", "_opf_worklog.load_worklog_at",
        "_opf_worklog.load_archive_worklog_at"})


def _entry_point_regressions(check):
    """First-intake failures, complementing the matrix's later loader failures.

    Instrument the contained read primitive, including archive destinations; never
    substitute a validator or give a profile control to a base-only consumer.
    These are deterministic intake failures, not concurrent filesystem race tests.
    """
    import _opf_emit
    from _opf_manifest_regressions import manifest_cases

    p = M + "/manifest.toml"
    for name, data, validation, control in manifest_cases(check):
        with _Fixture() as fx:
            readers = _manifest_readers(fx, control)
            readers.update({key: (lambda run=run: run()[1])
                            for key, run in _outer_readers(fx).items()})
            readers.update(
                loader_at=lambda: _error(lambda: wl.load_worklog_at(fx.fd, M)),
                doctor_public=lambda: _doctor_public(fx, control))
            if control is not None:
                readers = {key: readers[key] for key in ("doctor", "doctor_public")}
            original_parse = _opf_store.tomllib.loads
            if isinstance(data, dict):
                fx.files[p] = _opf_emit.emit_checked(data).encode()

            def parse(raw, **kwargs):
                # TOML cannot express a non-table root: retain that defensive seam.
                if not isinstance(data, dict) and raw == fx.files[p].decode():
                    return data
                return original_parse(raw, **kwargs)

            for caller, run in readers.items():
                label = "F2f-entry-{}-{}".format(name, caller)
                reads = []

                def read(fd, rel, **kwargs):
                    reads.append(fx.path(fd, rel))
                    return fx.read(fd, rel, **kwargs)

                with patch.object(_journal, "_read_contained", side_effect=read), \
                        patch.object(_opf_store.tomllib, "loads", side_effect=parse):
                    actual = run()
                messages = actual if isinstance(actual, list) else [actual]
                check(label + "-manifest-diagnostic", all(
                    any(isinstance(message, str) and finding in message for message in messages)
                    for finding in validation.findings))
                check(label + "-manifest-read", p in reads)
                check(label + "-no-active-read", not any(
                    path == LEGACY or path.startswith(M + "/worklog/") for path in reads))
                check(label + "-no-archive-read", not any(
                    path.startswith(M + "/archive/") and path.endswith("/worklog.toml")
                    for path in reads))
                if caller in ("doctor", "doctor_public"):
                    check(label + "-manifest-attribution", all(
                        any(finding in message for message in fx.manifest_findings)
                        for finding in validation.findings))
                    declared = data.get("profiles", {}) if isinstance(data, dict) else {}
                    declared = set(declared) if isinstance(declared, dict) else set()
                    scope = ([], []) if validation.status == _opf_store.CANNOT_EVALUATE else (
                        sorted(declared - set(validation.unevaluated_profiles)),
                        list(validation.unevaluated_profiles))
                    check(label + "-profile-scope", fx.profile_scope == scope)

    # Positive reachability witnesses: a healthy manifest must reach the active
    # source through each matrix entry; doctor's walk must reach the archive too.
    # Failing the active read prevents the staging entry from publishing a run.
    with _Fixture() as fx:
        for caller, run in _manifest_readers(fx).items():
            reads = []

            def read(fd, rel, **kwargs):
                path = fx.path(fd, rel)
                reads.append(path)
                if path == LEGACY:
                    raise PermissionError("fixture active-read witness")
                return fx.read(fd, rel, **kwargs)

            with patch.object(_journal, "_read_contained", side_effect=read):
                run()
            check("F2f-reachable-active-" + caller, LEGACY in reads)
            if caller == "doctor":
                check("F2f-reachable-archive-doctor", ARCHIVE in reads)


def _doctor_snapshot_regressions(check):
    """A late filesystem change cannot replace this run's manifest or scope."""
    import copy
    import _opf_emit
    modes = ("invalid", "generation", "parse", "unreadable", "absent",
             "profile", "major", "addition", "removal", "routing")
    for phase in ("records", "planner", "view-worklog"):
        for mode in modes:
            with _Fixture() as fx:
                _manifest_readers(fx)
                fx.manifest["profiles"] = {
                    "demo": {"version": "1.0.0", "base_compat": ">=1.2.0"},
                    "opaque": {"version": "2.0.0", "base_compat": "<0.0.1"}}
                path = M + "/manifest.toml"
                fx.files[path] = _opf_emit.emit_checked(fx.manifest).encode()
                baseline = _opf_check.validate_store(fx.res, {"demo": [1], "opaque": [1]})
                late = copy.deepcopy(fx.manifest)
                if mode == "invalid":
                    late["junk"] = {}
                elif mode == "generation":
                    late["opf"]["worklog"] = 2
                elif mode == "profile":
                    late["profiles"]["demo"]["base_compat"] = "<0.0.1"
                elif mode == "major":
                    late["profiles"]["demo"]["version"] = "2.0.0"
                elif mode == "addition":
                    late["profiles"]["added"] = {"version": "1.0.0"}
                elif mode == "removal":
                    del late["profiles"]["demo"]
                elif mode == "routing":
                    late["views"] = {}
                    late["vendors"] = {"registered": ["x-late"]}
                payload = _opf_emit.emit_checked(late).encode()
                if mode == "parse":
                    payload = b"not TOML ["
                elif mode == "unreadable":
                    payload = PermissionError("late manifest denied")
                reads, models = [], []
                armed = in_plan = False
                real_load, real_plan = wl.load_worklog_at, _opf_views.plan_views

                def read(fd, rel, **kwargs):
                    if fx.path(fd, rel) == path:
                        reads.append(path)
                    return fx.read(fd, rel, **kwargs)

                def change():
                    nonlocal armed
                    armed = True
                    if mode == "absent":
                        fx.files.pop(path, None)
                    else:
                        fx.files[path] = payload

                def intake(*args, **kwargs):
                    models.append(kwargs.get("manifest_model"))
                    if phase == "records" or (phase == "view-worklog" and in_plan):
                        change()
                    return real_load(*args, **kwargs)

                def plan(*args, **kwargs):
                    nonlocal in_plan
                    in_plan = True
                    models.append(kwargs.get("manifest_model"))
                    if phase == "planner":
                        change()
                    return real_plan(*args, **kwargs)

                with patch.object(_journal, "_read_contained", side_effect=read), \
                        patch.object(wl, "load_worklog_at", side_effect=intake), \
                        patch.object(_opf_views, "plan_views", side_effect=plan):
                    result = _opf_check.validate_store(
                        fx.res, {"demo": iter([1]), "opaque": iter([1])})
                label = "F2l-snapshot-" + phase + "-" + mode
                check(label + "-one-read", armed and reads == [path])
                check(label + "-one-model", len(models) == 3 and models[0] is not None
                      and all(model is models[0] for model in models))
                check(label + "-verdict", all(
                    getattr(result, field) == getattr(baseline, field)
                    for field in _opf_check.StoreValidation.__slots__))
                check(label + "-scope", result.evaluated_profiles == ["demo"]
                      and result.unevaluated_profiles == ["opaque"])

    # Standalone entries validate the same full scope, including iterator controls.
    for caller in ("loader", "planner", "view-worklog"):
        with _Fixture() as fx:
            _manifest_readers(fx)
            fx.manifest["profiles"] = {"demo": {
                "version": "1.0.0", "base_compat": "<0.0.1"}}
            fx.files[M + "/manifest.toml"] = _opf_emit.emit_checked(fx.manifest).encode()
            scope = {"demo": iter([1])}
            calls = {
                "loader": lambda: wl.load_worklog_at(fx.fd, M, supported_profiles=scope),
                "planner": lambda: _opf_views.plan_views(fx.fd, M, supported_profiles=scope),
                "view-worklog": lambda: _opf_views._load_worklog(
                    fx.fd, LEGACY, frozenset(), [], supported_profiles=scope)}
            check("F2l-standalone-scope-" + caller,
                  "does not admit the base spec_version" in (_error(calls[caller]) or ""))


def _outer_readers(fx):
    import io

    def render(mode):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            rc = _opf_views.render([mode, "--root", str(fx.root)])
        return rc, output.getvalue()

    return {"render_check": lambda: render("--check"),
            "render_write": lambda: render("--write")}


def _outer_entry_regressions(check):
    """Outer public wrappers have distinct initial intake and later loader paths."""
    for caller in ("render_check", "render_write"):
        for mode in ("reachable", "invalid", "generation", "parse", "unreadable"):
            with _Fixture() as fx:
                _manifest_readers(fx)
                run = _outer_readers(fx)[caller]
                real_load = wl.load_worklog_at
                reads = []
                armed = False

                def intake(*args, **kwargs):
                    nonlocal armed
                    armed = True
                    _install_late_fault(fx, mode)
                    return real_load(*args, **kwargs)

                def read(fd, rel, **kwargs):
                    path = fx.path(fd, rel)
                    if armed:
                        reads.append(path)
                    if path == LEGACY:
                        raise PermissionError("fixture active-read witness")
                    return fx.read(fd, rel, **kwargs)

                with patch.object(wl, "load_worklog_at", side_effect=intake) as entered, \
                        patch.object(_journal, "_read_contained", side_effect=read):
                    rc, message = run()
                label = "F2h-outer-{}-{}".format(caller, mode)
                shared = caller in ("render_check", "render_write")
                check(label + "-reached-loader",
                      entered.call_count >= 1 if shared or mode == "reachable"
                      else entered.call_count == 1)
                check(label + "-refused", rc == 2 and _late_fault_text(
                    "reachable" if shared else mode) in message)
                if shared or mode == "reachable":
                    check(label + "-active-witness", LEGACY in reads)
                    if shared:
                        check(label + "-snapshot", M + "/manifest.toml" not in reads)
                else:
                    check(label + "-manifest-only", reads == [M + "/manifest.toml"])


def _install_late_fault(fx, mode):
    import _opf_emit
    if mode == "invalid":
        fx.files[M + "/manifest.toml"] = _opf_emit.emit_checked(dict(fx.manifest, junk={})).encode()
    elif mode == "generation":
        data = dict(fx.manifest, opf=dict(fx.manifest["opf"], worklog=2))
        fx.files[M + "/manifest.toml"] = _opf_emit.emit_checked(data).encode()
    elif mode == "parse":
        fx.files[M + "/manifest.toml"] = b"not TOML ["
    elif mode == "unreadable":
        fx.files[M + "/manifest.toml"] = PermissionError("fixture late intake denied")


def _late_fault_text(mode):
    return {"reachable": "fixture active-read witness",
            "invalid": "unknown top-level table(s): junk",
            "generation": "[opf].worklog = 2 is not supported",
            "parse": "cannot parse m/manifest.toml",
            "unreadable": "fixture late intake denied"}[mode]


def _upgrade_preflight_regressions(check, fence):
    """Exercise both upgrade origins through the CLI, with real git/store bytes.

    Refusal snapshots include every non-git path and the git index. A separate
    lease spy detects even an acquire/release cycle that leaves no final bytes.
    No production I/O or validator is replaced in the end-to-end cases.
    """
    import io
    import shutil
    import sys
    import tempfile
    import tomllib

    import _opf_emit
    import _opf_init
    import check_opf_upgrade as fixtures
    import subprocess
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
            # F-367: pin automatic maintenance off in option position, so no detached
            # `git maintenance run --auto` / `git gc --auto` child can outlive the
            # commit and race this fixture temporary directory cleanup.
            _opf_emit.run_status_owned(
                [git, "-C", str(root), "-c", "init.templateDir=",
                 "-c", "init.defaultBranch=main", "-c", "core.hooksPath=" + str(home),
                 "-c", "commit.gpgSign=false", "-c", "user.name=OPF fixture",
                 "-c", "user.email=fixture@example.invalid",
                 "-c", "gc.auto=0", "-c", "gc.autoDetach=false",
                 "-c", "maintenance.auto=false", *args],
                fixture_id="upgrade-git/" + root.name + "/" + args[0], process_fixture=True,
                env=env, cwd=base, check=True, capture_output=True, timeout=150)

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
                try:
                    git_call(root, "init")
                    git_call(root, "--literal-pathspecs", "add", "--", ".working",
                             _opf_store.POINTER_REL, "CHANGELOG.md")
                    git_call(root, "commit", "-m", "seed upgrade regression")
                except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as exc:
                    print(label + "-git-setup", str(exc))
                    check(label + "-git-setup", False)
                    continue
                before, index_before = snapshot(root)
                if unsupported:
                    with patch.dict(os.environ, env, clear=True), \
                            patch.object(_opf_write_guard, "acquire_lease",
                                         side_effect=opf._UpgradeError("lease reached")) as lease, \
                            contextlib.redirect_stdout(io.StringIO()), \
                            contextlib.redirect_stderr(io.StringIO()):
                        message = _error(lambda: opf._upgrade_run(str(root)))
                    check(label + "-before-lease", message == fence and lease.call_count == 0)
                try:
                    proc = _opf_emit.run_status_owned(
                        [sys.executable, "-I", "-B", str(cli), "upgrade", "--root", str(root)],
                        fixture_id=label + ("/future" if unsupported else "/legacy"),
                        expected_returncode=2 if unsupported else 0, process_fixture=True,
                        env=env, cwd=base, capture_output=True, text=True, timeout=120)
                except (RuntimeError, subprocess.SubprocessError, OSError, ValueError) as exc:
                    print(label + "-cli", str(exc))
                    check(label + "-cli", False)
                    continue
                after, index_after = snapshot(root)
                if unsupported:
                    check(label + "-refuses-generation", proc.returncode == 0
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


def _round5_regressions(check):
    """Full doctor attribution/probe boundary, plus the import refusal contract."""
    import _opf_emit

    for fault in ("absent", "unparseable", "bad-kind"):
        with _Fixture() as fx:
            _manifest_readers(fx)  # Include indexes and an archive worklog.
            fx.manifest["junk"] = {}
            fx.files[M + "/manifest.toml"] = _opf_emit.emit_checked(fx.manifest).encode()
            if fault == "absent":
                del fx.files[LEGACY]
            elif fault == "unparseable":
                fx.files[LEGACY] = b"not TOML ["
            else:
                fx.files[LEGACY] = LEDGER.replace(b'kind = "fixed"', b'kind = "unknown"')
            probes = []

            def watch(method):
                def run(fd, rel, *args, **kwargs):
                    probes.append(fx.path(fd, rel))
                    return method(fd, rel, *args, **kwargs)
                return run

            with patch.object(_journal, "_lstat_contained", watch(fx.lstat)), \
                    patch.object(_journal, "_read_contained", watch(fx.read)), \
                    patch.object(_journal, "_open_dir_contained", watch(fx.open_dir)), \
                    patch.object(_opf_check, "_list_contained", watch(fx.list_contained)):
                result = _opf_check.validate_store(fx.res)
            label = "F2g-doctor-" + fault
            finding = "manifest: unknown top-level table(s): junk"
            check(label + "-manifest-once",
                  (result.findings + result.cannot_evaluate).count(finding) == 1
                  and result.by_check.get("C-MANIFEST") == [finding])
            check(label + "-records-cannot",
                  result.checks["C-RECORDS"] == _opf_store.CANNOT_EVALUATE
                  and result.by_check.get("C-RECORDS") == [
                      "active worklog source under m is not evaluated: m/manifest.toml failed "
                      "manifest validation (see C-MANIFEST)"])
            check(label + "-no-worklog-probe", bool(probes) and not any(
                p == LEGACY or p == M + "/worklog" or p.startswith(M + "/worklog/")
                or (p.startswith(M + "/archive/") and p.endswith("/worklog.toml"))
                for p in probes))
            check(label + "-dependent-checks-cannot", all(
                result.checks[c] == _opf_store.CANNOT_EVALUATE
                for c in ("C-ID-SPACE", "C-CHANGELOG-GATES", "C-ARCHIVE-ENUM")))

    with _Fixture() as fx:
        # Defensive seam: production only raises this exception for absence.
        # A future non-missing raise must still refuse, never fall through to data.
        exc = wl.ManifestShapeError(M + "/manifest.toml", {})
        with patch.object(wl, "read_manifest_at", side_effect=exc):
            message = _error(lambda: _opf_import._worklog_ids(fx.fd, M))
        check("F2g-import-shape-refusal", message ==
              "m/manifest.toml: the store manifest is absent; the storage layout cannot be determined (spec 9)")


def self_test():
    failures, checks = [], []

    def check(name, ok):
        checks.append(name)
        if not ok:
            failures.append(name)

    _round5_regressions(check)

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
            readers = _helper_readers(fx)
            readers["doctor"] = lambda: _doctor_leaf(fx)
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
    _entry_point_census(check)
    _entry_point_regressions(check)
    _doctor_snapshot_regressions(check)
    _outer_entry_regressions(check)

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
            check("F1-baseline-archive-" + label + "-doctor", _doctor_leaf(fx, archive=True) == expected)

    for ref in ("WL-1\u0662", "WL-1\u0662.abcd", "WL-1\uff12.abcd"):
        check("F3-ascii-ref-" + ref, wl._valid_wl_ref(ref) is None)
        check("F3-ascii-filename-" + ref, wl.parse_worklog_filename(ref + ".toml") is None)

    fence = ("[opf].worklog = 2 is not supported by this build "
             "(maximum supported worklog generation: 1)")
    check("F5-shipped-ceiling", _opf_store.SUPPORTED_WORKLOG == 1)
    with _Fixture(2) as fx:
        expected = _manifest_diagnostics(fx.manifest, _opf_store.validate_manifest(fx.manifest))
        for caller, read in _helper_readers(fx).items():
            check("F5-production-" + caller, read() == expected[caller])
        check("F5-production-doctor", _doctor_leaf(fx) == expected["doctor"])
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
                for caller, read in _helper_readers(fx).items():
                    check("F2-mixed-{}-{}".format(gen, caller), read() == conflict)
                if gen == 1:
                    # The doctor's explicit policy still reads the manifest-selected ledger.
                    check("F2-doctor-legacy-intake", _doctor_leaf(fx) == [])
                    check("F2-doctor-legacy-view", _opf_views._load_worklog(
                        fx.fd, LEGACY, frozenset(), [],
                        on_legacy_conflict=_opf_check._worklog_legacy_conflict)[0] == LEDGER)
                else:
                    check("F2-doctor-gen2-conflict", _doctor_leaf(fx) == [conflict])

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
                check("F3-doctor-route", _doctor_leaf(fx) == [sentinel] and intake.call_count == 1)

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
