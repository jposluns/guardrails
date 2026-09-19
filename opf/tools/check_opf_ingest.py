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

    def symlink_supported():
        """True when this platform + filesystem can create a symlink, PROBED ONCE with a throwaway dangling
        link under `base`. A genuinely unsupported platform returns False so a symlink vector is SKIPPED; on a
        supported platform a later fixture `os.symlink` failure (e.g. EACCES) then RAISES and is caught by the
        harness-error path (EXIT_ERROR), so a fixture-setup error can never silently skip a symlink
        discriminator and let it gain zero coverage (codex-8 #3)."""
        probe = base / ".symlink-probe"
        try:
            os.symlink("target", str(probe))
        except OSError:
            return False
        os.unlink(str(probe))
        return True

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
        # discriminator (F1 + F-8.1): an --include naming a MANAGED product-root path (a declared [unmanaged]
        # path `keep.md` or a PUBLIC deliverable target `CHANGELOG.md`) yields NO declared row for it. Per
        # F-8.1 a ROGUE view target (`report.md`, unrecognized name) and a NON-PUBLIC deliverable target
        # (`out.pdf`) are NOT managed and so ARE detected (agreeing with C-CONTAINMENT, which grades them
        # strays); reverting F-8.1 (covering the RAW target) would wrongly EXCLUDE `report.md` / `out.pdf`.
        root4 = build_store(
            strays={"report.md": "R", "out.pdf": "D", "keep.md": "K", "loose.md": "L", "CHANGELOG.md": "C"},
            manifest_extra=('[views.main]\nkind = "composed"\nsources = ["docs"]\ntarget = "report.md"\n\n'
                            '[deliverables.d1]\nkind = "curated"\ntarget = "out.pdf"\n\n'
                            '[deliverables."CHANGELOG.md"]\nkind = "curated"\ntarget = "CHANGELOG.md"\n\n'
                            '[unmanaged]\npaths = ["keep.md"]'))
        r4 = ing.detect(root4, include=["report.md", "out.pdf", "keep.md", "loose.md", "CHANGELOG.md"])
        decl4 = {row["source_path"] for row in r4.rows if row["scope"] == "declared"}
        expect("declared-scope-managed-exclusion",
               r4.verdict == ing.CLEAN and "loose.md" in decl4
               and "keep.md" not in decl4 and "CHANGELOG.md" not in decl4
               and "report.md" in decl4 and "out.pdf" in decl4)
        # F-8.1 STORE-scope agreement: the view/deliverable covered set mirrors the checker authority, so a
        # ROGUE view name laundering a store target (`.working/rogue.md`) is DETECTED (not covered), a
        # WELL-FORMED view (`TODO.md` -> `.working/TODO.md`) is covered, a REBOUND view covers its SPEC
        # destination while the rebound target is a stray, and a NON-PUBLIC deliverable target is detected.
        rogue = build_store(strays={".working/rogue.md": "x", ".working/real.md": "r"},
                            manifest_extra=('[views.rogue]\nkind = "composed"\nsources = ["backlog_item"]\n'
                                            'target = ".working/rogue.md"'))
        rr = ing.detect(rogue); rr_p = {row["source_path"] for row in rr.rows}
        expect("f81-rogue-view-name-launder-detected",
               rr.verdict == ing.CLEAN and ".working/rogue.md" in rr_p and ".working/real.md" in rr_p)
        wf = build_store(strays={".working/TODO.md": "t", ".working/real.md": "r"},
                         manifest_extra=('[views."TODO.md"]\nkind = "composed"\nsources = ["backlog_item"]\n'
                                         'target = ".working/TODO.md"'))
        wr = ing.detect(wf); wr_p = {row["source_path"] for row in wr.rows}
        expect("f81-wellformed-view-target-covered",
               wr.verdict == ing.CLEAN and ".working/TODO.md" not in wr_p and ".working/real.md" in wr_p)
        rb = build_store(strays={".working/TODO.md": "t", ".working/evil.md": "e", ".working/real.md": "r"},
                         manifest_extra=('[views."TODO.md"]\nkind = "composed"\nsources = ["backlog_item"]\n'
                                         'target = ".working/evil.md"'))
        rbr = ing.detect(rb); rbr_p = {row["source_path"] for row in rbr.rows}
        expect("f81-rebound-view-covers-spec-dest-and-rebind-is-stray",
               rbr.verdict == ing.CLEAN and ".working/TODO.md" not in rbr_p
               and ".working/evil.md" in rbr_p and ".working/real.md" in rbr_p)
        nd = build_store(strays={".working/d.md": "d", ".working/real.md": "r"},
                         manifest_extra='[deliverables.d1]\nkind = "curated"\ntarget = ".working/d.md"')
        ndr = ing.detect(nd); ndr_p = {row["source_path"] for row in ndr.rows}
        expect("f81-nonpublic-deliverable-target-detected",
               ndr.verdict == ing.CLEAN and ".working/d.md" in ndr_p and ".working/real.md" in ndr_p)
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
        # BOTH store and declared scope; an exact-match exclusion would emit (and read) each child. The
        # declared include is a BROAD glob with a real hit (`docs/keep.md`), not a pattern naming the covered
        # `docs/legacy` subtree directly (which is now a CANNOT-EVALUATE, R7-1): the pruned `docs/legacy`
        # yields no `docs/legacy/old.md` row while `docs/keep.md` is detected.
        root5b = build_store(
            strays={".working/legacy-dir/kept.md": "k", ".working/real.md": "r"},
            product={"docs/legacy/old.md": "o", "docs/keep.md": "K"},
            manifest_extra='[unmanaged]\npaths = [".working/legacy-dir", "docs/legacy"]')
        r5b = ing.detect(root5b, include=["docs/*"])
        paths5b = {row["source_path"] for row in r5b.rows}
        expect("unmanaged-directory-covers-subtree",
               r5b.verdict == ing.CLEAN and ".working/real.md" in paths5b and "docs/keep.md" in paths5b
               and ".working/legacy-dir/kept.md" not in paths5b and "docs/legacy/old.md" not in paths5b)
        # discriminator (R7-1): THREE-VALUED no-match, separating existence from exclusion SOUNDLY (no
        # literal-ancestor existence proxy). An --include naming an ABSENT declared-unmanaged path (no unread
        # content) is a no-match FINDING; one that could match within a PRESENT covered subtree detection
        # never reads is CANNOT-EVALUATE (neither match nor no-match is knowable across the no-read boundary),
        # whether a literal descendant (round-7 returned a false CLEAN) or a wildcard reaching the covered
        # dir (round-7 returned a false no-match FINDING). Both flip on reverting the derivation.
        root5c = build_store(product={"live.md": "L"},
                             manifest_extra='[unmanaged]\npaths = ["qa-absent-legacy"]')
        r5c = ing.detect(root5c, include=["qa-absent-legacy/*.md"])
        expect("declared-absent-covered-include-is-finding",
               r5c.verdict == ing.FINDING and r5c.rows == [])
        root5d = build_store(product={"present-legacy/old.md": "o", "live.md": "L"},
                             manifest_extra='[unmanaged]\npaths = ["present-legacy"]')
        # literal descendant of a PRESENT covered dir -> CANNOT-EVALUATE (round-7: false CLEAN)
        r5d_lit = ing.detect(root5d, include=["present-legacy/old.md"])
        # wildcard that reaches into the covered dir its literal prefix does not name -> CANNOT-EVALUATE
        # (round-7: false no-match FINDING that would withhold the store scope)
        r5d_wild = ing.detect(root5d, include=["*/old.md"])
        expect("declared-present-covered-include-cannot-evaluate",
               r5d_lit.verdict == ing.CANNOT_EVALUATE
               and any("present-legacy" in f for f in r5d_lit.findings)
               and r5d_wild.verdict == ing.CANNOT_EVALUATE)
        # a purely READ-SPACE no-match (no covered subtree reachable) stays a FINDING (no over-widening).
        expect("read-space-no-match-is-finding",
               ing.detect(root5d, include=["nope/*.md"]).verdict == ing.FINDING)
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
        # discriminator (F1: bind each exclusion to its correct resolved root): a store relocated UNDER the
        # product's own `.working/` (`dir:.working/ops`) resolves (store_root at `.working/ops`), so its
        # PRODUCT-relative store-root control prefix is `.working/ops/.aiqt`. That prefix must NOT enter the
        # STORE-scope covered set, or a store stray whose STORE-relative path is `.working/ops/.aiqt/foo.md`
        # collides with it and is silently suppressed. The fix returns the control prefixes SEPARATELY
        # (declared scope only); a plain store stray alongside is the live-sibling flip.
        root7b = build_relocated(subdir=".working/ops",
                                 strays={".working/ops/.aiqt/foo.md": "collide", ".working/plain.md": "p"})
        r7b = ing.detect(root7b)
        store7b = {row["source_path"] for row in r7b.rows if row["scope"] == "store"}
        expect("nested-store-control-prefix-no-false-suppression",
               r7b.verdict == ing.CLEAN and ".working/plain.md" in store7b
               and ".working/ops/.aiqt/foo.md" in store7b)
        # discriminator (round-7 MAJOR, F1): RE-ANCHOR the store-relative managed set for DECLARED scope on
        # a relocated (`dir:ops`) store. A store-relative `notes` / `report.md` binds to the resolved STORE
        # root (`ops/notes` / `ops/report.md`) for the product-root scope, so (i) a raw un-anchored entry no
        # longer SUPPRESSES a real product stray at `notes/` / `report.md`, and (ii) the store-side path is
        # EXCLUDED and never READ. Both entry types (unmanaged + target), both directions. Inline is identity.
        rc_u = build_relocated(manifest_extra='[unmanaged]\npaths = ["notes"]',
                               product={"notes/todo.md": "stray", "ops/notes/legacy.md": "storeside",
                                        "readme.md": "R"})
        ru_sup = ing.detect(rc_u, include=["notes/*", "readme.md"])
        ru_sup_decl = {row["source_path"] for row in ru_sup.rows if row["scope"] == "declared"}
        ru_read = ing.detect(rc_u, include=["ops/notes/*", "readme.md"])
        expect("reanchor-unmanaged-suppression-and-not-read",
               ru_sup.verdict == ing.CLEAN and "notes/todo.md" in ru_sup_decl
               and ru_read.verdict == ing.CANNOT_EVALUATE
               and not any(row["source_path"] == "ops/notes/legacy.md" for row in ru_read.rows))
        # F-8.1: a NON-PUBLIC deliverable target covers NOTHING (deliverables have no name authority; the
        # checker grades a store-scope deliverable target as a stray), so on a `dir:ops` store NEITHER the
        # product-root `report.md` NOR the store-side `ops/report.md` is suppressed -- both are DETECTED,
        # agreeing with the checker. Reverting F-8.1 (covering + re-anchoring the raw deliverable target)
        # would EXCLUDE the store-side `ops/report.md`. The re-anchoring MECHANISM stays exercised by the
        # [unmanaged] directions above (the surviving store-relative covered set).
        rc_t = build_relocated(manifest_extra='[deliverables.d1]\nkind = "curated"\ntarget = "report.md"',
                               product={"report.md": "stray", "ops/report.md": "storeside", "readme.md": "R"})
        rt = ing.detect(rc_t, include=["report.md", "ops/report.md", "readme.md"])
        rt_decl = {row["source_path"] for row in rt.rows if row["scope"] == "declared"}
        expect("nonpublic-deliverable-target-not-covered-reloc",
               rt.verdict == ing.CLEAN and "report.md" in rt_decl and "ops/report.md" in rt_decl)
        # PUBLIC deliverable target (product-root in every topology, spec 5.8) is NOT re-anchored, so the
        # real product-root `CHANGELOG.md` stays excluded on a relocated store (re-anchoring it would detect
        # it as a stray).
        rc_p = build_relocated(
            manifest_extra='[deliverables."CHANGELOG.md"]\nkind = "curated"\ntarget = "CHANGELOG.md"',
            product={"CHANGELOG.md": "cl", "readme.md": "R"})
        rp = ing.detect(rc_p, include=["CHANGELOG.md", "readme.md"])
        rp_decl = {row["source_path"] for row in rp.rows if row["scope"] == "declared"}
        expect("public-target-not-reanchored", rp.verdict == ing.CLEAN and rp_decl == {"readme.md"})
        # discriminator (round-2 F2): a detected path the worksheet validator would reject as non-contained
        # (a `:` in its second character) is a LOCATED CANNOT-EVALUATE, never a CLEAN worksheet that then
        # fails validate_worksheet, so a CLEAN detection's worksheet ALWAYS validates.
        root8 = build_store(product={"a:b.md": "x"})
        r8 = ing.detect(root8, include=["*"])
        expect("clean-worksheet-always-validates",
               r8.verdict == ing.CANNOT_EVALUATE and any("a:b.md" in msg for msg in r8.findings))
        # discriminator (F-MAJOR premise shift): the store-root control / VCS subtrees (`.aiqt/import`,
        # `.aiqt/import/journal`, `.aiqt/import-archive`, and `.git`) are OPF-managed / VCS content the
        # declared product-root scope must neither emit as rows NOR read, exactly as
        # _opf_import._assemble_preview drops `.git`/`.aiqt` at the store root; the whole `.aiqt` subtree is
        # excluded, so every apply / migration ops / journal / archive tree nesting under it is covered by
        # construction. Without the store-root control exclusion they leak as declared rows (content-read).
        root9 = build_store(product={
            ".aiqt/import/journal/txn/frames.log": "j", ".aiqt/import-archive/imp-x/acceptance.json": "a",
            ".git/config": "g", "readme.md": "R"})
        r9 = ing.detect(root9, include=["*"])
        ctl9 = {row["source_path"] for row in r9.rows}
        expect("store-root-control-trees-exclusion",
               r9.verdict == ing.CLEAN and "readme.md" in ctl9
               and not any(p == d or p.startswith(d + "/")
                           for d in _opf_store.STORE_ROOT_CONTROL_DIRS for p in ctl9))
        # single-authority set-equality (G): ingest holds NO control-dir literal of its own and derives the
        # store-root control exclusion from the ONE authority _opf_store.STORE_ROOT_CONTROL_DIRS, the SAME
        # tuple _opf_import._assemble_preview drops at the store root. The prefixes ingest actually excludes
        # for an inline store (product root == store root, so the prefixes are the bare dir names) must EQUAL
        # that authority as a SET, so a reintroduced or divergent literal fails closed.
        inline_root = build_store()
        inline_res = _opf_store.resolve_store(inline_root)
        expect("control-dirs-single-authority-set-equality",
               set(ing._store_root_control_prefixes(inline_res)) == set(_opf_store.STORE_ROOT_CONTROL_DIRS))
        # drift-catcher: `.aiqt` is the control umbrella every import-layer ops / journal / archive constant
        # must nest under (and the imports run tree stays under `.working/`), so the `.aiqt` subtree exclusion
        # covers them by construction; a relocation out from under `.aiqt`, or dropping `.aiqt` from the single
        # authority, silently un-covers it, and this assertion catches that drift at source.
        umbrella = ".aiqt"
        expect("covered-derivation-matches-import-authority",
               umbrella in _opf_store.STORE_ROOT_CONTROL_DIRS
               and all(rel == umbrella or rel.startswith(umbrella + "/")
                       for rel in (ing._opf_import.IMPORT_OPS_REL, ing._opf_import.IMPORT_JOURNAL_REL,
                                   ing._opf_import.IMPORT_ARCHIVE_REL))
               and ing._opf_import.IMPORTS_REL.startswith(_opf_store.WORKING_DIRNAME + "/"))
        # discriminator (F1: traverse-before-exclude): a covered (excluded) DIRECTORY is PRUNED before
        # descent, never entered, so an unreadable / exotic entry inside it cannot block detection with a
        # spurious CANNOT-EVALUATE. A symlink inside a declared-unmanaged directory makes the pre-fix walk
        # (which entered then excluded only at the result stage) fail closed; pruning detects the live
        # sibling CLEAN. The include is `live.md` alone (a covered-subtree pattern like `legacy/*` is now a
        # CANNOT-EVALUATE, R7-1, masking this DIRECTORY-prune vector). Symlink support is platform-gated via
        # `symlink_supported()`: an unsupported platform skips the vector, but on a supported platform a
        # fixture-setup os.symlink error RAISES and fails the harness rather than silently skipping (codex-8 #3).
        root10 = build_store(strays={".working/real.md": "r"},
                             product={"legacy/keep.md": "k", "live.md": "L"},
                             manifest_extra='[unmanaged]\npaths = ["legacy"]')
        if symlink_supported():
            os.symlink(str(root10 / ".working" / "toml" / "manifest.toml"),
                       str(root10 / "legacy" / "link.md"))
            r10 = ing.detect(root10, include=["live.md"])
            p10 = {row["source_path"] for row in r10.rows}
            expect("excluded-subtree-pruned-before-descent",
                   r10.verdict == ing.CLEAN and "live.md" in p10
                   and not any(p.startswith("legacy/") for p in p10))
        # discriminator (F2): a covered entry that is ITSELF a symlink (a declared-unmanaged SYMLINK) is
        # pruned BEFORE the exotic-entry refusal, never read and never refused (spec 14.2); the round-7 walk
        # refused it and blocked the whole detect with a spurious CANNOT-EVALUATE. A NON-covered exotic entry
        # still fails closed. Reverting the pre-refusal prune makes `legacy` fail closed again. A fixture-setup
        # os.symlink error fails the harness (codex-8 #3), never a silent skip.
        root10b = build_store(product={"live.md": "L"}, manifest_extra='[unmanaged]\npaths = ["legacy"]')
        if symlink_supported():
            os.symlink(str(root10b / ".working" / "toml" / "manifest.toml"), str(root10b / "legacy"))
            r10b = ing.detect(root10b, include=["live.md"])
            p10b = {row["source_path"] for row in r10b.rows}
            expect("covered-symlink-entry-pruned-not-refused",
                   r10b.verdict == ing.CLEAN and "live.md" in p10b and "legacy" not in p10b)
        # discriminator (F-8.2): a PRESENT covered SYMLINK-TO-DIR is recorded as existing, so an --include
        # BENEATH it is a CANNOT-EVALUATE at PARITY with the real-directory case, never a false no-match
        # FINDING across the no-read boundary. Reverting F-8.2 makes the symlink case a false FINDING while the
        # real-dir control stays CANNOT-EVALUATE.
        root10c = build_store(product={"legacy_real/old.md": "o", "live.md": "L"},
                              manifest_extra='[unmanaged]\npaths = ["legacy"]')
        if symlink_supported():
            os.symlink(str(root10c / "legacy_real"), str(root10c / "legacy"))
            sb = ing.detect(root10c, include=["legacy/old.md"])
            root10d = build_store(product={"legacy/old.md": "o", "live.md": "L"},
                                  manifest_extra='[unmanaged]\npaths = ["legacy"]')
            rbd = ing.detect(root10d, include=["legacy/old.md"])   # real covered dir control
            expect("covered-symlink-beneath-cannot-evaluate-parity",
                   sb.verdict == ing.CANNOT_EVALUATE and any("legacy" in f for f in sb.findings)
                   and rbd.verdict == ing.CANNOT_EVALUATE and sb.verdict == rbd.verdict)
        # discriminator (F2): validate_worksheet rejects a source_path the CONTAINED READER
        # (_journal._check_rel) rejects (a control char slips past _is_contained_relpath), with an HONESTLY
        # recomputed digest isolating the reader check; dropping it turns this GREEN.
        ctl_payload = {"format": ws["format"], "schema": ws["schema"],
                       "row": [dict(ws["row"][0], source_path=".working/x\ty.md")]}
        ctl_honest = "sha256:" + ing._opf_import._sha256_hex(ing._worksheet_bytes(ctl_payload))
        expect("validate-reader-unreadable-source-path-flip",
               ing.validate_worksheet(dict(ctl_payload, worksheet_digest=ctl_honest)) != [])
        # discriminator (F3): a non-string top-level key is a refusing finding, never a TypeError from
        # sorting / joining the unknown-key set.
        nonstr = dict(ws)
        nonstr[1] = "x"
        expect("validate-nonstring-key-flip", ing.validate_worksheet(nonstr) != [])

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
          "detection and FINDING on its discriminator, including a type-strict schema flip, a source_path "
          "the contained reader rejects, and a non-string top-level key; detection-completeness matches an "
          "independent re-enumeration and catches an omitted store-scope file; detect-writes-nothing; "
          "managed-path exclusion (by subtree containment) catches over-detection, a managed path an "
          "--include names, a non-canonical managed spelling, a declared-unmanaged DIRECTORY's whole subtree "
          "unread, a relocated (`dir:`) store's whole resolved subtree, the store pointer control files, and "
          "the store-root `.aiqt` / `.git` control / VCS trees derived from the import layer's own drop-set "
          "authority (drift-checked against the import constants), applied to declared scope only so a store "
          "nested under the product's own `.working/` cannot suppress a store stray, and RE-ANCHORED at the "
          "resolved store root for declared scope on a relocated store (the [unmanaged] entries, suppression "
          "+ unmanaged-read directions) while a PUBLIC deliverable target stays product-relative; the view / "
          "deliverable covered set MIRRORS the checker authority so a rogue view NAME cannot launder a store "
          "path, a REBIND covers the spec destination not the raw target, and a non-public deliverable target "
          "is a detectable stray (ingest and C-CONTAINMENT agree; F-8.1); the no-match derivation is "
          "THREE-VALUED (an ABSENT covered path and a read-space no-match are findings, a pattern that could "
          "match within a PRESENT covered subtree detection never reads is CANNOT-EVALUATE, never a false "
          "clean or a false no-match); a covered DIRECTORY, and a covered entry that is ITSELF a symlink, are "
          "pruned before descent / refusal so an unreadable / exotic entry inside or as an excluded entry "
          "never blocks detection, with a present covered symlink-to-dir yielding a CANNOT-EVALUATE beneath "
          "it at parity with the real-dir case (F-8.2); a CLEAN detection's worksheet always validates; the "
          "engine module self-test is green)")
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
