#!/usr/bin/env python3
"""Worklog storage intake and WL reference grammar.

Only the manifest selects the active shape. Archive buckets keep their legacy
envelope. This module loads data; record/schema, release ownership of aliases,
history, and doctor gates remain their callers' responsibility. In particular,
a generation-2 load is not a generation-2 doctor VALID verdict.

Directory reads are contained and no-follow, including reads after enumeration.
They are not an atomic snapshot against a concurrent regular-file replacement;
writers still need the store lease. The mint suffix is not an integrity check.
Identical additions at the same path can merge to one file; this loader cannot
recover the lost second fact (the no-nonce residual).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_worklog.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import os
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _journal          # noqa: E402
import _opf_store        # noqa: E402

LEGACY_NAME = "worklog.toml"
DIRECTORY_NAME = "worklog"
_WL_REF = re.compile(r"WL-([1-9][0-9]*)(?:\.([0-9a-f]{4}))?\Z")


class WorklogError(_opf_store.StoreError):
    """An unreadable, ambiguous, or malformed worklog intake; fail closed."""


class ManifestReadError(WorklogError):
    """Preserve the manifest reader's text and identify its input for doctor."""

    def __init__(self, relpath, cause):
        super().__init__(str(cause))
        self.relpath = relpath


class ManifestShapeError(WorklogError):
    """Absent or unidentifiable manifest; callers retain their legacy wording.

    This is not a generation-1 selection. The manifest-selected active source
    must not be probed after this exception. Explicit archive reads use a fixed
    legacy shape independently; the enclosing doctor traversal stops on manifest
    failure. The default text matches load_manifest's findings.
    """

    def __init__(self, relpath, manifest):
        self.relpath = relpath
        self.missing = manifest is None
        message = (relpath + " vanished after discovery" if self.missing else
                   "; ".join(_opf_store.validate_manifest(manifest).findings))
        super().__init__(message)


class ManifestValidationError(WorklogError):
    """A present manifest failed validation; do not probe its active source.

    Explicit archive reads have a fixed legacy shape and no manifest contract.
    Doctor stops its enclosing traversal before archive intake on this failure.
    """

    def __init__(self, relpath, manifest, validation):
        self.relpath = relpath
        self.manifest = manifest
        self.status = validation.status
        self.findings = tuple(validation.findings)
        super().__init__("; ".join(self.findings))


class ManifestModel:
    """One validation and profile scope for a descriptor-bound intake.

    Doctor passes this same object to every manifest consumer. File replacement
    after this read belongs to a later run; other store files are not a snapshot.
    Consumers treat data as read-only.
    """

    def __init__(self, root_fd, machine_rel, data, supported_profiles=None):
        self.root_fd, self.machine_rel, self.data = root_fd, machine_rel, data
        self.validation = _opf_store.validate_manifest(data, supported_profiles)
        profiles = data.get("profiles") if isinstance(data, dict) else None
        declared = {k for k in profiles if isinstance(k, str)} if isinstance(profiles, dict) else set()
        self.unevaluated_profiles = tuple(self.validation.unevaluated_profiles)
        self.evaluated_profiles = tuple(sorted(declared - set(self.unevaluated_profiles)))

    def require_valid(self, root_fd, machine_rel):
        if (root_fd, machine_rel) != (self.root_fd, self.machine_rel):
            raise WorklogError("manifest model belongs to a different intake")
        if self.validation.status != _opf_store.VALID or self.validation.findings:
            raise ManifestValidationError(
                machine_rel + "/" + _opf_store.MANIFEST_NAME, self.data, self.validation)
        return self.data


def read_manifest_model_at(root_fd, machine_rel, *, supported_profiles=None):
    """Read the generation authority once, refusing intake failure before routing."""
    relpath = machine_rel + "/" + _opf_store.MANIFEST_NAME
    try:
        manifest = _opf_store._read_toml_contained(root_fd, relpath)
    except _opf_store.StoreError as exc:
        raise ManifestReadError(relpath, exc) from exc
    if manifest is None:
        raise ManifestShapeError(relpath, manifest)
    model = ManifestModel(root_fd, machine_rel, manifest, supported_profiles)
    model.require_valid(root_fd, machine_rel)
    return model


def _valid_wl_ref(value):
    """Return (positive number, suffix or None), or None. WL-only extension."""
    if not isinstance(value, str):
        return None
    match = _WL_REF.fullmatch(value)
    if match is None:
        return None
    try:
        return int(match[1]), match[2]
    except ValueError:
        return None                       # the same integer-size bound as the schema


def parse_worklog_filename(name):
    """Return the WL reference shape, or None; strip only the final .toml."""
    if not isinstance(name, str) or not name.endswith(".toml"):
        return None
    return _valid_wl_ref(name[:-len(".toml")])


def generation(manifest):
    """[opf].worklog is independent of homes; absence alone means generation 1."""
    opf = manifest.get("opf") if isinstance(manifest, dict) else None
    if not isinstance(opf, dict):
        raise WorklogError("cannot determine worklog generation: [opf] is absent or malformed")
    value = opf.get("worklog", 1)
    if type(value) is not int or value not in (1, 2):
        raise WorklogError("[opf].worklog must be the integer 1 or 2")
    if value > _opf_store.SUPPORTED_WORKLOG:
        raise WorklogError("[opf].worklog = {} is not supported by this build "
                           "(maximum supported worklog generation: {})".format(
                               value, _opf_store.SUPPORTED_WORKLOG))
    return value


def source_relpath(machine_rel, manifest):
    """The one declared active source; never infer the shape from the filesystem."""
    return machine_rel + "/" + (DIRECTORY_NAME if generation(manifest) == 2 else LEGACY_NAME)


def mint_suffix(entry):
    """Mint-time content hash; id and aliases do not participate. Never recomputed by a gate."""
    from _opf_release import _canonical, ReleaseError
    if not isinstance(entry, dict):
        raise WorklogError("cannot mint a suffix from a non-table entry")
    try:
        content = {k: v for k, v in entry.items() if k not in ("id", "aliases")}
        return hashlib.sha256(("opf-worklog-mint-v1\n" + _canonical(content)).encode("utf-8")).hexdigest()[:4]
    except (ReleaseError, UnicodeError) as exc:
        raise WorklogError("cannot mint a worklog suffix ({})".format(exc))


def build_alias_map(entries):
    """Map live ids AND aliases to entries, refusing every full-token collision.

    Pass active plus archive entries to check that whole population. No suffix-only
    uniqueness rule exists. Released ownership needs the ledger and is checked by
    the later schema/doctor work, not inferred from a canonical id here.
    """
    if not isinstance(entries, (list, tuple)):
        raise WorklogError("worklog alias input must be an entry array")
    result = {}
    for entry in entries:
        shape = _valid_wl_ref(entry.get("id")) if isinstance(entry, dict) else None
        if shape is None:
            raise WorklogError("worklog alias input contains an invalid WL id")
        ident = entry["id"]
        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list) or ("aliases" in entry and shape[1] is not None):
            raise WorklogError("aliases must be an array on a canonical entry")
        for alias in aliases:
            parsed = _valid_wl_ref(alias)
            if parsed is None or parsed[1] is None:
                raise WorklogError("worklog aliases must be provisional WL references")
        for ref in [ident] + aliases:
            if ref in result:
                raise WorklogError("duplicate worklog reference {!r}; full-id collision is fatal".format(ref))
            result[ref] = entry
    return result


def _read_document(root_fd, relpath):
    """Preserve the contained reader's exceptions for each legacy caller to translate."""
    return _opf_store._read_toml_contained(root_fd, relpath, with_raw=True)


def load_worklog_at(root_fd, machine_rel, *, required=True, with_raw=False, read_legacy=None,
                    on_legacy_conflict=None, supported_profiles=None, manifest_model=None):
    """Load only the manifest-selected source beneath an already-resolved descriptor.

    with_raw preserves legacy bytes; generation 2 uses a length-framed stream in
    numeric (number, suffix) order. read_legacy preserves the view reader's policy.
    required=False allows only an absent generation-1 ledger.

    Standalone readers refuse the opposite shape. Doctor alone supplies
    on_legacy_conflict: it already grades the generation-1 directory as INVALID
    in C-CONTAINMENT, and must still inspect the legacy ledger and its views.
    This callback cannot change source selection or permit a generation-2 conflict.
    """
    try:
        if manifest_model is None:
            manifest_model = read_manifest_model_at(
                root_fd, machine_rel, supported_profiles=supported_profiles)
        manifest = manifest_model.require_valid(root_fd, machine_rel)
        gen = generation(manifest)
        rel = source_relpath(machine_rel, manifest)
        other = machine_rel + "/" + (LEGACY_NAME if gen == 2 else DIRECTORY_NAME)
        if _journal._lstat_contained(root_fd, other) is not None:
            if gen == 1 and on_legacy_conflict is not None:
                on_legacy_conflict(other)
            else:
                raise WorklogError("{} conflicts with the manifest-selected worklog shape".format(other))
    except WorklogError:
        raise
    except (_opf_store.StoreError, _journal.JournalError, OSError, RecursionError) as exc:
        raise WorklogError("cannot load worklog ({})".format(exc))

    if gen == 1:
        # Outside the generation-2 wrapper: views, doctor, import, and changelog
        # retain their original exception translation, including archive intake.
        got = (read_legacy or _read_document)(root_fd, rel)
        if got is None:
            if required:
                raise WorklogError("{} is absent (the active worklog ledger is required)".format(rel))
            return None
        return got if with_raw else got[1]
    try:
        dfd = _journal._open_dir_contained(root_fd, rel)
        try:
            entries, blobs = [], []
            names = os.listdir(dfd)
            for name in names:
                if parse_worklog_filename(name) is None:
                    raise WorklogError("{}/{} is not a worklog filename".format(rel, name))
            for name in sorted(names, key=lambda name: (
                    parse_worklog_filename(name)[0], parse_worklog_filename(name)[1] or "")):
                got = _read_document(dfd, name)
                if got is None:
                    raise WorklogError("{}/{} vanished during enumeration".format(rel, name))
                raw, entry = got
                if entry.get("id") != name[:-len(".toml")]:
                    raise WorklogError("{}/{}: filename and id differ".format(rel, name))
                # A per-record body is flat, never a legacy envelope.
                if any(key in entry for key in ("schema", "entry", "record")):
                    raise WorklogError("{}/{} is not a flat worklog entry".format(rel, name))
                entries.append(entry)
                encoded = name.encode("utf-8")
                blobs.extend((str(len(encoded)).encode("ascii") + b":" + encoded,
                              str(len(raw)).encode("ascii") + b":" + raw))
            build_alias_map(entries)
            data = {"schema": 1, "entry": entries}
            return (b"".join(blobs), data) if with_raw else data
        finally:
            os.close(dfd)
    except WorklogError:
        raise
    except (_opf_store.StoreError, _journal.JournalError, OSError, RecursionError) as exc:
        raise WorklogError("cannot load worklog ({})".format(exc))


def load_archive_worklog_at(root_fd, relpath):
    """An explicitly named archive bucket retains the legacy envelope (M7)."""
    got = _read_document(root_fd, relpath)
    return None if got is None else got[1]


def load_worklog(store):
    """load_worklog(resolved_store) -> {schema, entry[]}; no layout probing."""
    if store.status != _opf_store.RESOLVED:
        raise WorklogError("cannot load worklog from an unresolved store")
    try:
        fd = _opf_store._open_store_root_fd(store.store_root, store.pointer_source != "default")
        try:
            return load_worklog_at(fd, store.machine_rel)
        finally:
            os.close(fd)
    except OSError as exc:
        raise WorklogError("cannot open worklog store ({})".format(exc))


def self_test():
    from unittest.mock import patch
    from _opf_worklog_regressions import self_test as regressions
    result = regressions()
    # Explicit in-memory activation only; no environment variable or CLI bypass.
    with patch.object(_opf_store, "SUPPORTED_WORKLOG", 2):
        return max(result, _self_test())


def _self_test():
    """Discriminators for each PR-1 rule; filesystem fixtures never touch a live store."""
    import copy
    import tempfile
    from unittest.mock import patch
    from _opf_release import coverage_digest
    failures, checks = [], []

    def check(name, ok):
        checks.append(name)
        if not ok:
            failures.append(name)

    def refused(call):
        try:
            call()
        except WorklogError:
            return True
        return False

    check("canonical", _valid_wl_ref("WL-12") == (12, None))
    check("provisional", _valid_wl_ref("WL-12.abcd") == (12, "abcd"))
    for bad in (None, [], True, "WL-0", "WL-01", "WL-0.abcd", "WL-1.ABCD",
                "WL-1.abc", "WL-1.abcde", "BI-1.abcd", "WL-1.abcd\n", "WL-1..abcd"):
        check("bad-ref-" + repr(bad), _valid_wl_ref(bad) is None)
    check("final-extension", parse_worklog_filename("WL-12.abcd.toml") == (12, "abcd"))
    for bad in ("WL-1.toml.bak", "WL-1.abcd.toml.toml", "../WL-1.toml", "WL-1.TOML"):
        check("bad-filename-" + bad, parse_worklog_filename(bad) is None)
    from _opf_schema import _valid_id_shape
    check("other-id-grammar-unchanged", _valid_id_shape("BI-1.abcd") is None
          and _valid_id_shape("WL-1.abcd") is None)
    for homes in (1, 2):
        check("independent-generation-" + str(homes), generation({"opf": {"homes": homes}}) == 1
              and generation({"opf": {"homes": homes, "worklog": 2}}) == 2)
    for bad in (True, False, "2", 2.0, 0, 3, None, []):
        check("bad-generation-" + repr(bad), refused(lambda: generation({"opf": {"worklog": bad}})))
    check("missing-manifest", refused(lambda: generation(None)))
    entry = {"id": "WL-1", "date": "2026-01-01T00:00:00Z", "actor": {"kind": "maintainer"},
             "kind": "fixed", "summary": "x"}
    before = copy.deepcopy(entry)
    # Literal canonical payload, independently specified; a prefix or serializer drift turns this red.
    payload = 'opf-worklog-mint-v1\n{"actor":{"kind":"maintainer"},"date":"2026-01-01T00:00:00Z","kind":"fixed","summary":"x"}'
    check("mint-vector", mint_suffix(entry) == hashlib.sha256(payload.encode()).hexdigest()[:4])
    check("mint-identity-excluded", mint_suffix(dict(entry, id="WL-99", aliases=["WL-4.abcd"]))
          == mint_suffix(entry) and entry == before)
    check("mint-key-order", mint_suffix(dict(reversed(list(entry.items())))) == mint_suffix(entry))
    check("mint-invalid", refused(lambda: mint_suffix([])))
    live = dict(entry, id="WL-2.abcd")
    released = dict(entry, aliases=["WL-2.abcd"])
    check("alias-resolves", build_alias_map([released])["WL-2.abcd"] is released)
    check("same-suffix-benign", len(build_alias_map([live, dict(entry, id="WL-3.abcd")])) == 2)
    for name, rows in (
            ("live-live", [live, copy.deepcopy(live)]),
            ("live-alias", [live, released]), ("alias-live", [released, live]),
            ("alias-alias", [released, dict(entry, id="WL-9", aliases=["WL-2.abcd"])]),
            ("alias-repeat", [dict(entry, aliases=["WL-2.abcd", "WL-2.abcd"])]),
            ("canonical-alias", [dict(entry, aliases=["WL-2"])]),
            ("provisional-owner", [dict(live, aliases=[])]),
            ("alias-not-array", [dict(entry, aliases="WL-2.abcd")])):
        check(name, refused(lambda: build_alias_map(rows)))
    # A real content-hash collision at equal n is fatal, while raising n resolves it.
    seen = {}
    for i in range(65537):
        candidate = dict(entry, summary="collision-" + str(i))
        suffix = mint_suffix(candidate)
        if suffix in seen:
            first = dict(seen[suffix], id="WL-2." + suffix)
            second = dict(candidate, id=first["id"])
            check("real-collision", refused(lambda: build_alias_map([first, second])))
            second["id"] = "WL-3." + suffix
            check("remint-raises-n", len(build_alias_map([first, second])) == 2)
            break
        seen[suffix] = candidate
    else:
        check("real-collision-found", False)

    with tempfile.TemporaryDirectory(prefix="opf-worklog-") as td:
        root = Path(td)
        machine = root / ".working/custom"
        machine.mkdir(parents=True)
        manifest = machine / "manifest.toml"
        import _opf_init
        valid_manifest = _opf_init.build_manifest()
        manifest.write_text(valid_manifest, encoding="utf-8")
        legacy = machine / LEGACY_NAME
        raw = b'# retained bytes\nschema = 1\n[[entry]]\nid = "WL-1"\ndate = "2026-01-01T00:00:00Z"\nactor = {kind = "maintainer"}\nkind = "fixed"\nsummary = "x"\n'
        legacy.write_bytes(raw)
        fd = os.open(td, os.O_RDONLY | os.O_DIRECTORY)
        try:
            old = load_worklog_at(fd, ".working/custom")
            check("legacy-model", old == {"schema": 1, "entry": [entry]})
            check("legacy-bytes", load_worklog_at(fd, ".working/custom", with_raw=True)[0] == raw)
            resolved = _opf_store.resolve_store(root)
            check("resolved-entry-point", load_worklog(resolved) == old)
            import _opf_views
            import _opf_check
            # The consumers import `_opf_worklog`; under a direct-script run this module is `__main__`, a
            # distinct module object, so the spy must sit on the module they actually call through.
            import _opf_worklog as consumed
            with patch.object(consumed, "load_worklog_at", wraps=consumed.load_worklog_at) as intake:
                check("view-intake", _opf_views._load_worklog(
                    fd, ".working/custom/worklog.toml", frozenset(), []) == (raw, [entry])
                      and intake.call_count == 1)
                # The id-space reader is doctor's active-worklog gather (the retired import
                # engine's _worklog_ids was the other one); it must route through this intake.
                rep = _opf_check._Report()
                check("doctor-intake", _opf_check._gather_worklog(
                    fd, ".working/custom/worklog.toml", frozenset(), rep, True,
                    machine_rel=".working/custom") == {1: entry}
                      and not rep.cannot and not rep.findings and intake.call_count == 2)
            for gen in (1, 2):
                cls = _opf_check.classify_containment(
                    {"opf": {"worklog": gen, "layout": "inline"}}, ".working/custom")
                check("classification-" + str(gen),
                      cls.managed_file(".working/custom/worklog.toml") == (gen == 1)
                      and cls.managed_file(".working/custom/worklog/WL-1.abcd.toml") == (gen == 2)
                      and not cls.managed_file(".working/custom/worklog/WL-1.ABCD.toml")
                      and not cls.managed_file(".working/custom/worklog.index.toml"))
            digest = coverage_digest(old["entry"])
            directory = machine / DIRECTORY_NAME
            directory.mkdir()
            check("gen1-mixed-refused", refused(lambda: load_worklog_at(fd, ".working/custom")))
            manifest.write_text(valid_manifest.replace('[opf]\n', '[opf]\nworklog = 2\n'),
                                encoding="utf-8")
            check("gen2-mixed-refused", refused(lambda: load_worklog_at(fd, ".working/custom")))
            legacy.unlink()
            check("empty-directory", load_worklog_at(fd, ".working/custom") == {"schema": 1, "entry": []})
            archive = machine / "archive" / "2026"
            archive.mkdir(parents=True)
            (archive / LEGACY_NAME).write_bytes(raw)
            check("archive-stays-legacy", load_archive_worklog_at(
                fd, ".working/custom/archive/2026/worklog.toml") == old)
            body = raw.split(b"[[entry]]\n", 1)[1]
            record = directory / "WL-1.toml"
            record.write_bytes(body)
            new = load_worklog_at(fd, ".working/custom")
            check("canonical-tail-preserved", new == old and coverage_digest(new["entry"]) == digest)
            provisional = directory / "WL-2.abcd.toml"
            provisional.write_bytes(body.replace(b'WL-1', b'WL-2.abcd'))
            check("provisional-loaded", len(load_worklog_at(fd, ".working/custom")["entry"]) == 2)
            provisional.write_bytes(body.replace(b'WL-1', b'WL-2.abcd').replace(b'summary = "x"', b'summary = "corrected"'))
            check("suffix-not-recomputed", load_worklog_at(fd, ".working/custom")["entry"][1]["summary"] == "corrected")
            record.write_bytes(body + b'aliases = ["WL-2.abcd"]\n')
            check("loaded-alias-collision", refused(lambda: load_worklog_at(fd, ".working/custom")))
            record.write_bytes(body)
            provisional.write_bytes(body.replace(b'WL-1', b'WL-3.abcd'))
            check("filename-binding", refused(lambda: load_worklog_at(fd, ".working/custom")))
            provisional.unlink()
            record.write_bytes(b'id = "WL-1"\nschema = 1\n')
            check("flat-body", refused(lambda: load_worklog_at(fd, ".working/custom")))
            record.write_bytes(b"not TOML [")
            check("malformed-toml", refused(lambda: load_worklog_at(fd, ".working/custom")))
            record.unlink()
            record.mkdir()
            check("subdirectory", refused(lambda: load_worklog_at(fd, ".working/custom")))
            record.rmdir()
            record.symlink_to(manifest)
            check("record-symlink", refused(lambda: load_worklog_at(fd, ".working/custom")))
            record.unlink()
            os.mkfifo(record)
            check("fifo", refused(lambda: load_worklog_at(fd, ".working/custom")))
            record.unlink()
            bad = directory / "WL-1.TOML"
            bad.write_text('id = "WL-1"\n', encoding="utf-8")
            check("directory-closure", refused(lambda: load_worklog_at(fd, ".working/custom")))
            bad.unlink()
            with patch.object(os, "listdir", side_effect=PermissionError("fixture")):
                check("unreadable-directory", refused(lambda: load_worklog_at(fd, ".working/custom")))
            directory.rmdir()
            check("gen2-no-fallback", refused(lambda: load_worklog_at(fd, ".working/custom", required=False)))
            directory.symlink_to(machine, target_is_directory=True)
            check("directory-symlink", refused(lambda: load_worklog_at(fd, ".working/custom")))
            directory.unlink()
            manifest.write_text(valid_manifest, encoding="utf-8")
            check("legacy-optional-absence", load_worklog_at(fd, ".working/custom", required=False) is None)
            check("legacy-required-absence", refused(lambda: load_worklog_at(fd, ".working/custom")))
            manifest.unlink()
            check("no-manifest-no-probe", refused(lambda: load_worklog_at(fd, ".working/custom")))
            manifest.write_text("[opf\n", encoding="utf-8")
            check("malformed-manifest", refused(lambda: load_worklog_at(fd, ".working/custom")))
            # A manifest whose parse overflows (tomllib raises RecursionError) is refused as a WorklogError carrying
            # the store parse handler's finding (_opf_store._parse_store_toml), never a traceback.
            # The overflow is INJECTED (tomllib.loads and tomllib.load raise RecursionError on a marked,
            # otherwise valid input) rather than provoked by a deeply nested body: the depth at which tomllib
            # overflows is an interpreter limit, so a fixed body overflows under one recursion limit and parses
            # (or trips an unrelated refusal) under another.
            import tomllib
            manifest.write_text(valid_manifest + "# injected-overflow\n", encoding="utf-8")
            real_loads, real_load = tomllib.loads, tomllib.load

            def overflowing_loads(text, **kwargs):
                if "injected-overflow" in text:
                    raise RecursionError("injected parser overflow")
                return real_loads(text, **kwargs)

            tomllib.loads = overflowing_loads
            tomllib.load = lambda fp, **kwargs: overflowing_loads(fp.read().decode("utf-8"), **kwargs)
            try:
                load_worklog_at(fd, ".working/custom")
                overflow = "loaded"
            except WorklogError as exc:
                overflow = str(exc)
            except RecursionError:
                overflow = "escaped RecursionError"
            finally:
                tomllib.loads, tomllib.load = real_loads, real_load
            check("manifest-parse-overflow",
                  "nesting is too deep; present but unparseable): injected parser overflow" in overflow)
        finally:
            os.close(fd)
    if failures:
        for name in failures:
            print("OPF-WORKLOG SELF-TEST: FAIL " + name)
        return 1
    print("OPF-WORKLOG SELF-TEST: PASS ({} loader, grammar, mint, and alias checks)".format(len(checks)))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print("usage: _opf_worklog.py --self-test (a library module; no live mode)", file=sys.stderr)
    sys.exit(2)
