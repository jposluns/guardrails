#!/usr/bin/env python3
"""OPF root-ingest detection gate (OPF-MIGRATE MIG-PR1): structural + invariant validation of the
read-only detection engine and its disposition worksheet.

A thin gate in the check_opf_*.py family (deliberately NOT gen_*.py: root-ingest targets an adopter --root
with no fixed repo-relative target, so it gates as a --self-test leg over synthetic stores PLUS a
NOT-APPLICABLE live leg, exactly the posture opf.py / check_opf_doctor.py / check_opf_import.py document).
It owns an explicit registry of checks over the worksheet `_opf_ingest.detect` produces AND over the
detector's coverage, and it fails CLOSED on any fixture or artefact it cannot read (an unreadable input is
a failure, never nothing-to-check). Verdicts are judged by the gate's own termination status, never grepped.

Checks (each with a fail-without-it discriminator exercised by --self-test):
  - worksheet-structure  : detect over a clean synthetic store returns verdict 0 with a worksheet whose
                           closed top-level keyset (the opf-ingest-dispositions-v1 format token, the schema
                           marker, the row array, and the recorded worksheet_digest) holds.
  - worksheet-schema-vocab : every row is the closed keyset and its disposition / origin / scope are the
                           closed vocabularies exactly; a vocab or keyset violation is a finding.
  - worksheet-digest     : the recorded worksheet_digest recomputes over the row set (determinism /
                           integrity anchor); a mutated payload fails the recompute.
  - detection-completeness : an INDEPENDENT re-enumeration of the store `.working/` scope confirms every
                           non-OPF-managed file appears in the worksheet exactly once; a planted store-scope
                           file the worksheet omits FAILS this check (the load-bearing coverage guarantee).
  - detect-writes-nothing : the synthetic store is byte-identical after detect (SECI-preview-has-no-side-
                           effects); detect stages no run and mutates nothing at all.
  - managed-path-exclusion : an OPF-managed file (the manifest, a control ledger, a file under
                           `.working/imports/`, an already-declared `[unmanaged]` path, a relocated (`dir:`)
                           store's whole resolved subtree, or a store pointer control file) does NOT appear
                           as a detected row (this catches over-detection, which completeness alone does
                           not); and a CLEAN detection's worksheet always passes `validate_worksheet`.
  - module-self-test     : _opf_ingest.self_test() == 0 (the engine's own unit invariants run wherever this
                           CI-registered gate runs).

Disclosed coverage limits (part of the gate, not a footnote, per disclose-guard-residuals): the SEMANTIC
correctness of a disposition (which files should be kept, migrated, or moved) is a human decision, gate-
blind; there is no acceptance / actor authenticity in PR1 (review is MIG-PR4); and the importer layer, plan
composition, apply promotion, and verb wiring that later slices add are out of this gate's scope. The gate
asserts representative synthetic-store scenarios with exact verdicts, not detect's whole input space.

This repository is not an OPFiles adopter (it has no store), and root-ingest wires no verb in this slice,
so there is no live detection to run: the live leg prints NOT APPLICABLE and exits 0, spec-honest like the
doctor / import legs; the assurance rides the --self-test leg over synthetic stores. Offline, stdlib only,
fail-closed, launched isolated (-I -B). The tempdir is removed in a finally (test-hermeticity).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for the self-test's sibling imports below

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_ERROR = 2


def _self_test():
    """Build synthetic stores and assert every registered check PASSes on a clean detection and FINDINGs on
    its own discriminator, plus delegate to the engine's own unit suite. Returns 0 clean, 1 on a failing
    assertion, 2 on a harness error (a fixture could not be built)."""
    import shutil
    import tempfile

    import _opf_ingest as ing
    import _opf_store

    failures = []

    def expect(label, cond):
        if not cond:
            failures.append(label)

    def manifest_text(extra_top=""):
        lines = [
            "[opf]", 'standard = "opf"',
            'spec_version = "{}"'.format(_opf_store.SUPPORTED_SPEC_VERSION),
            'layout = "inline"', 'posture = "required"', 'import_status = "none"',
            "", "[store]", 'sync_target = ""',
            "", "[modules]", "governance = true",
            "", "[types.backlog_item]", 'namespace = "BI"',
            "", "[vendors]", 'registered = []',
        ]
        if extra_top:
            lines += ["", extra_top]
        return "\n".join(lines) + "\n"

    base = Path(tempfile.mkdtemp(prefix="opf-ingest-gate-selftest-")).resolve()
    counter = [0]

    def build_store(strays=None, manifest_extra="", product=None):
        counter[0] += 1
        root = base / "case-{:02d}".format(counter[0])
        machine = root / ".working" / "toml"
        machine.mkdir(parents=True)
        (machine / "manifest.toml").write_text(manifest_text(manifest_extra), encoding="utf-8")
        (machine / "counters.toml").write_text(
            "schema = 1\n\n[counters]\nBI = 0\nLF = 0\nWL = 0\n", encoding="utf-8")
        for rel, text in (strays or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        for rel, text in (product or {}).items():   # product-root-relative (inline: same tree as strays)
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return root

    def build_relocated(subdir="ops", strays=None, product=None, manifest_extra=""):
        """A RELOCATED store: a committed `.opf.toml` at the product root names `dir:<subdir>`, so the store
        resolves at `<root>/<subdir>/.working/toml` (store_root != product_root). `strays` are store-relative
        (under `<subdir>/.working/`); `product` are product-root-relative. Returns root."""
        counter[0] += 1
        root = base / "reloc-{:02d}".format(counter[0])
        machine = root / subdir / ".working" / "toml"
        machine.mkdir(parents=True)
        (machine / "manifest.toml").write_text(manifest_text(manifest_extra), encoding="utf-8")
        (machine / "counters.toml").write_text(
            "schema = 1\n\n[counters]\nBI = 0\nLF = 0\nWL = 0\n", encoding="utf-8")
        (root / _opf_store.POINTER_REL).write_text(
            '[store]\ntarget = "dir:{}"\n'.format(subdir), encoding="utf-8")
        for rel, text in (strays or {}).items():
            p = root / subdir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        for rel, text in (product or {}).items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        return root

    def independent_store_files(root):
        """An INDEPENDENT re-enumeration of the store `.working/` scope (os.walk, NOT detect's contained
        walk), excluding the machine subdir subtree and the reserved `.working/imports/` tree, returning the
        set of store-relative regular-file paths. The detection-completeness check rests on this being
        derived without the detector."""
        working = root / ".working"
        machine = working / "toml"
        imports = working / "imports"
        out = set()
        for dirpath, _dirs, names in os.walk(str(working)):
            dp = Path(dirpath)
            if dp == machine or machine in dp.parents or dp == imports or imports in dp.parents:
                continue
            for name in names:
                out.add(str((dp / name).relative_to(root)))
        return out

    def snapshot(root):
        out = {}
        for dirpath, _dirs, names in os.walk(str(root)):
            for name in names:
                fp = Path(dirpath) / name
                out[str(fp.relative_to(root))] = fp.read_bytes()
        return out

    try:
        # --- worksheet-structure -------------------------------------------------------------------
        root = build_store(strays={".working/stray.md": "one", ".working/sub/deep.txt": "two"})
        r = ing.detect(root)
        ws = r.worksheet
        expect("worksheet-structure",
               r.verdict == ing.CLEAN
               and set(ws) == {"format", "schema", "row", "worksheet_digest"}
               and ws["format"] == ing.WORKSHEET_FORMAT and ws["schema"] == ing.SCHEMA
               and isinstance(ws["row"], list) and ws["worksheet_digest"])
        # discriminator: a worksheet with a wrong format token fails validation. It carries an HONESTLY
        # recomputed digest over its own mutated payload (F2: isolate the format check), so the finding is
        # the format mismatch itself, not a masking digest-recompute mismatch; dropping the format check
        # would turn this discriminator GREEN.
        broken_payload = {"format": "wrong", "schema": ws["schema"], "row": ws["row"]}
        broken_honest = "sha256:" + ing._opf_import._sha256_hex(ing._worksheet_bytes(broken_payload))
        expect("worksheet-structure-flip",
               ing.validate_worksheet(dict(broken_payload, worksheet_digest=broken_honest)) != [])

        # --- worksheet-schema-vocab ----------------------------------------------------------------
        expect("worksheet-schema-vocab-clean", ing.validate_worksheet(ws) == [])
        # an out-of-vocabulary origin is a finding. The discriminator carries an HONESTLY recomputed digest
        # over its own mutated payload (F2: isolate the vocab check), so the finding is the origin violation
        # itself, not a masking digest-recompute mismatch; dropping the origin check turns it GREEN.
        vocab_payload = {"format": ws["format"], "schema": ws["schema"],
                         "row": [dict(ws["row"][0], origin="not-an-origin")]}
        vocab_honest = "sha256:" + ing._opf_import._sha256_hex(ing._worksheet_bytes(vocab_payload))
        expect("worksheet-schema-vocab-flip",
               ing.validate_worksheet(dict(vocab_payload, worksheet_digest=vocab_honest)) != [])
        # discriminator (F5): a bool/float schema that compares == the version (True == 1 == 1.0), with an
        # HONESTLY recomputed digest, is still rejected type-strictly; a bare `!= SCHEMA` comparison accepts it.
        sch_payload = {"format": ws["format"], "schema": True, "row": ws["row"]}
        sch_honest = "sha256:" + ing._opf_import._sha256_hex(ing._worksheet_bytes(sch_payload))
        expect("worksheet-schema-type-flip",
               ing.validate_worksheet(dict(sch_payload, worksheet_digest=sch_honest)) != [])

        # --- worksheet-digest ----------------------------------------------------------------------
        drifted = dict(ws)
        drifted["worksheet_digest"] = "sha256:" + "0" * 64
        expect("worksheet-digest-clean", ing.validate_worksheet(ws) == [])
        expect("worksheet-digest-flip", ing.validate_worksheet(drifted) != [])

        # --- detection-completeness (LOAD-BEARING) -------------------------------------------------
        detected_store = {row["source_path"] for row in r.rows if row["scope"] == "store"}
        independent = independent_store_files(root)
        expect("detection-completeness-clean", detected_store == independent and independent)
        # discriminator: a worksheet that OMITS a planted store-scope file no longer covers the
        # independent enumeration (an omitted file is caught, not silently absorbed).
        omitted = {p for p in detected_store if p != ".working/stray.md"}
        expect("detection-completeness-flip", omitted != independent)

        # --- detect-writes-nothing -----------------------------------------------------------------
        root2 = build_store(strays={".working/x.md": "x"})
        before = snapshot(root2)
        ing.detect(root2)
        expect("detect-writes-nothing", snapshot(root2) == before)

        # --- managed-path-exclusion ----------------------------------------------------------------
        root3 = build_store(
            strays={".working/imports/imp-x/frames.log": "run", ".working/declared.md": "kept",
                    ".working/real.md": "found"},
            manifest_extra='[unmanaged]\npaths = [".working/declared.md"]')
        r3 = ing.detect(root3)
        paths3 = {row["source_path"] for row in r3.rows}
        expect("managed-path-exclusion",
               r3.verdict == ing.CLEAN and ".working/real.md" in paths3
               and ".working/toml/manifest.toml" not in paths3
               and ".working/toml/counters.toml" not in paths3
               and ".working/imports/imp-x/frames.log" not in paths3
               and ".working/declared.md" not in paths3)
        # discriminator (F1): an --include naming a MANAGED product-root path (a view target, a deliverable
        # target, or a declared [unmanaged] path) yields NO declared row for it: excluded wherever it falls.
        root4 = build_store(
            strays={"report.md": "R", "out.pdf": "D", "keep.md": "K", "loose.md": "L"},
            manifest_extra=('[views.main]\nkind = "composed"\nsources = ["docs"]\ntarget = "report.md"\n\n'
                            '[deliverables.d1]\nkind = "curated"\ntarget = "out.pdf"\n\n'
                            '[unmanaged]\npaths = ["keep.md"]'))
        r4 = ing.detect(root4, include=["report.md", "out.pdf", "keep.md", "loose.md"])
        decl4 = {row["source_path"] for row in r4.rows if row["scope"] == "declared"}
        expect("declared-scope-managed-exclusion",
               r4.verdict == ing.CLEAN and "loose.md" in decl4
               and "report.md" not in decl4 and "out.pdf" not in decl4 and "keep.md" not in decl4)
        # discriminator (F2): a non-canonical [unmanaged].paths spelling still excludes the store file it
        # names; a literal-string comparison would let the aliased path be detected as a stray.
        root5 = build_store(strays={".working/kept.md": "k", ".working/real.md": "r"},
                            manifest_extra='[unmanaged]\npaths = ["./.working/kept.md"]')
        r5 = ing.detect(root5)
        paths5 = {row["source_path"] for row in r5.rows}
        expect("non-canonical-managed-exclusion",
               r5.verdict == ing.CLEAN and ".working/real.md" in paths5
               and ".working/kept.md" not in paths5)
        # discriminator (round-3 F1): a declared-unmanaged DIRECTORY covers its whole subtree by containment
        # (matching _opf_check.managed_file / _under_any), so a file UNDER it is excluded (never read) in
        # BOTH store and declared scope; an exact-match exclusion would emit (and read) each child.
        root5b = build_store(
            strays={".working/legacy-dir/kept.md": "k", ".working/real.md": "r"},
            product={"docs/legacy/old.md": "o", "docs/keep.md": "K"},
            manifest_extra='[unmanaged]\npaths = [".working/legacy-dir", "docs/legacy"]')
        r5b = ing.detect(root5b, include=["docs/legacy/old.md", "docs/keep.md"])
        paths5b = {row["source_path"] for row in r5b.rows}
        expect("unmanaged-directory-covers-subtree",
               r5b.verdict == ing.CLEAN and ".working/real.md" in paths5b and "docs/keep.md" in paths5b
               and ".working/legacy-dir/kept.md" not in paths5b and "docs/legacy/old.md" not in paths5b)
        # discriminator (round-2 F1): a RELOCATED store (`.opf.toml` -> `dir:ops`, so store_root at
        # `ops/.working/`) does not leak its own machine / imports / stray subtree into declared scope; the
        # exclusion is derived from the RESOLVED store location, so naming the relocated store scope via
        # --include is a FINDING and the store subtree never appears as declared rows.
        root6 = build_relocated(
            strays={".working/toml/extra.toml": "m", ".working/imports/imp-x/frames.log": "run",
                    ".working/stray.md": "s"},
            product={"readme.md": "R"})
        r6 = ing.detect(root6, include=["ops/.working/*"])
        r6ok = ing.detect(root6, include=["readme.md", "*"])
        r6decl = sorted(row["source_path"] for row in r6ok.rows if row["scope"] == "declared")
        expect("relocated-store-scope-exclusion",
               r6.verdict == ing.FINDING and r6.rows == []
               and r6ok.verdict == ing.CLEAN and r6decl == ["readme.md"])
        # discriminator (round-2 C1): the store pointer control files (`.opf.toml` / `.opf.local.toml`) at
        # the product root are managed wherever they fall; an --include naming one yields no declared row.
        root7 = build_relocated(product={"readme.md": "R"})
        (root7 / _opf_store.LOCAL_POINTER_REL).write_text('[store]\ntarget = "dir:ops"\n', encoding="utf-8")
        r7 = ing.detect(root7, include=[".opf.toml", ".opf.local.toml", "readme.md"])
        decl7 = {row["source_path"] for row in r7.rows if row["scope"] == "declared"}
        expect("pointer-control-files-exclusion",
               r7.verdict == ing.CLEAN and decl7 == {"readme.md"})
        # discriminator (round-2 F2): a detected path the worksheet validator would reject as non-contained
        # (a `:` in its second character) is a LOCATED CANNOT-EVALUATE, never a CLEAN worksheet that then
        # fails validate_worksheet, so a CLEAN detection's worksheet ALWAYS validates.
        root8 = build_store(product={"a:b.md": "x"})
        r8 = ing.detect(root8, include=["*"])
        expect("clean-worksheet-always-validates",
               r8.verdict == ing.CANNOT_EVALUATE and any("a:b.md" in msg for msg in r8.findings))

        # --- module-self-test delegation -----------------------------------------------------------
        expect("module-self-test", ing.self_test() == 0)
    except OSError as exc:
        print("check_opf_ingest self-test: harness error: {}".format(exc), file=sys.stderr)
        shutil.rmtree(str(base), ignore_errors=True)
        return EXIT_ERROR
    finally:
        shutil.rmtree(str(base), ignore_errors=True)

    if failures:
        for f in failures:
            print("check_opf_ingest self-test: FAIL: {}".format(f), file=sys.stderr)
        return EXIT_FINDING
    print("check_opf_ingest self-test: PASS (worksheet-structure/schema-vocab/digest each PASS on a clean "
          "detection and FINDING on its discriminator, including a type-strict schema flip; "
          "detection-completeness matches an independent re-enumeration and catches an omitted store-scope "
          "file; detect-writes-nothing; managed-path exclusion (by subtree containment) catches "
          "over-detection, a managed path an --include names, a non-canonical managed spelling, a "
          "declared-unmanaged DIRECTORY's whole subtree unread, a relocated (`dir:`) store's whole resolved "
          "subtree, and the store pointer control files; a CLEAN detection's worksheet always validates; "
          "the engine module self-test is green)")
    return EXIT_OK


def main(argv=None):
    # Final class-width backstop: any residual, unforeseen error routes to a located cannot-evaluate (exit
    # 2), never a false-0 or an uncaught exit-1 escape. KeyboardInterrupt/SystemExit stay uncaught.
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["--self-test"]:
            return _self_test()
        if args:
            print("check_opf_ingest: unexpected argument(s): {}".format(" ".join(args)), file=sys.stderr)
            return EXIT_ERROR
        # Live leg: root-ingest wires no verb in this slice (MIG-PR1) and this repository is not an OPFiles
        # adopter, so there is no store to detect live. NOT APPLICABLE, exit 0 (the doctor/import non-adopter
        # posture); the assurance rides the --self-test leg over synthetic stores.
        print("check_opf_ingest: NOT APPLICABLE (this repository is not an OPFiles adopter and root-ingest "
              "wires no verb in this slice, so there is no live detection to run; the --self-test leg "
              "carries the assurance)")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false-0 or uncaught exit-1
        print("check_opf_ingest: cannot evaluate: unexpected error in the detection gate ({!r}); failing "
              "closed to exit 2".format(exc), file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
