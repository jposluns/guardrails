#!/usr/bin/env python3
"""Reference-facts consistency gate: hand-authored copies of manifest-derived facts must agree with the
manifests. Stdlib only, offline.

It covers the fact copies that have NO generator to derive them, plus the closed-set parity that a
generator alone cannot assert:
  - the publisher-family parenthetical in the site/mappings meta descriptions and in the disclosure.toml
    standards-mappings claim (recomputed from the manifests' publishers through an explicit alias table);
  - the schema enum table in .aiqt/standards/README.md (the kind, status, and catalogue rows) against the
    loader's closed sets KINDS/STATUSES/CATALOGUES;
  - closed-set parity between _standards.KINDS/STATUSES and their programmatic copies (gen_mappings
    RELATION/RELATION_PROSE/STATUS_PILL/STATUS_DESC and the currency POLICY);
  - the deferred-frameworks negative check (no vendored framework is still listed "Not yet vendored");
  - the subset-denominator invariant, checked structure-aware wherever the coverage of a curated-subset
    manifest is rendered: the registry row (name and coverage in one <tr>), the reverse view (name in a
    <summary>, coverage in a later <p> of the same <details>), AND the machine-readable JSON export (a
    subset framework must not carry an ids_total edition denominator). The gen_mappings branch is the
    primary control; this is defence in depth against a hand-edited page or export.

BEST-EFFORT RESIDUAL (disclosed per the pack's own rule): this gate checks the specific fact copies
enumerated above; it is not a semantic-equivalence prover for arbitrary prose, and `catalogue = "full"` is
a reviewed declaration this gate trusts, not a fact it can prove offline (no full edition is vendored to
compare against). It fails closed on an unreadable/malformed input and on a new publisher family that has
no alias, so a stale copy cannot ship silently.

  check_reference_facts.py              exit 0 clean, 1 finding, 2 unreadable/missing input (fail-closed)
  check_reference_facts.py --self-test
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402
from _standards import load_manifests, ManifestError, KINDS, STATUSES, CATALOGUES  # noqa: E402
from gen_mappings import RELATION, RELATION_PROSE, STATUS_PILL, STATUS_DESC  # noqa: E402
from check_standards_currency import POLICY  # noqa: E402

# Publisher -> the short family token the public parentheticals use. Deliberately explicit: a NEW publisher
# family fails closed here (exit 2 via ManifestError) until an alias is chosen, so a family can never ship
# with the parentheticals silently stale.
FAMILY_ALIAS = {"OWASP Foundation": "OWASP", "NIST": "NIST", "MITRE": "MITRE ATLAS",
                "ISO/IEC": "ISO/IEC", "Cloud Security Alliance": "CSA"}

PAREN = re.compile(r"frameworks \(([^)]*)\)")
N_OF_M = re.compile(r"\b\d+ of \d+\b")


def _read(path):
    """Fail-closed read: a surface this gate is contracted to check that is missing or unreadable is an
    error (exit 2 via OSError), never a silent skip."""
    if not path.is_file():
        raise OSError("required surface missing: {}".format(path))
    return path.read_text(encoding="utf-8")


def expected_families(manifests):
    fams = set()
    for m in manifests.values():
        alias = FAMILY_ALIAS.get(m.publisher)
        if alias is None:
            raise ManifestError("publisher {!r} has no family alias in check_reference_facts; add one and "
                                "update the public parentheticals".format(m.publisher))
        fams.add(alias)
    return fams


def check_parenthetical(text, where, fams, findings):
    hits = PAREN.findall(text)
    if not hits:
        findings.append("{}: no 'frameworks (...)' parenthetical found".format(where))
        return
    for hit in hits:
        got = {tok.strip() for tok in hit.split(",") if tok.strip()}
        if got != fams:
            findings.append("{}: parenthetical {{{}}} != publisher families {{{}}}".format(
                where, ", ".join(sorted(got)), ", ".join(sorted(fams))))


def check_meta(root, fams, findings):
    text = _read(root / "site" / "mappings.html")
    metas = [ln for ln in text.splitlines()
             if 'name="description"' in ln or 'property="og:description"' in ln]
    if len(metas) != 2:
        findings.append("site/mappings.html: expected 2 description metas, found {}".format(len(metas)))
    for ln in metas:
        check_parenthetical(ln, "site/mappings.html meta", fams, findings)


def check_disclosure(root, fams, findings):
    data = load_toml(root / "disclosure.toml")
    rows = [r for r in data.get("row", []) if r.get("id") == "standards-mappings"]
    if len(rows) != 1:
        findings.append("disclosure.toml: expected one standards-mappings row, found {}".format(len(rows)))
        return
    check_parenthetical(rows[0].get("claim", ""), "disclosure.toml standards-mappings claim", fams,
                        findings)


def check_enum_table(root, findings):
    text = _read(root / ".aiqt" / "standards" / "README.md")
    for field, expect in (("kind", KINDS), ("status", STATUSES), ("catalogue", CATALOGUES)):
        row = re.search(r"^\| `{}` \| (.+) \|$".format(field), text, re.M)
        if not row:
            findings.append("standards README: no schema row for `{}`".format(field))
            continue
        got = set(re.findall(r"`([a-z-]+)`", row.group(1)))
        if got != expect:
            findings.append("standards README: `{}` row lists {{{}}}, loader allows {{{}}}".format(
                field, ", ".join(sorted(got)), ", ".join(sorted(expect))))


def check_parity(findings):
    """Assert the programmatic closed-set copies still equal the loader's KINDS/STATUSES. The dicts are
    imported live, so a stale or superset key is an explicit finding rather than a dormant desync (a
    missing key already fails loud at render, a superset key is what this catches)."""
    for name, got, expect in (("gen_mappings.RELATION", set(RELATION), KINDS),
                              ("gen_mappings.RELATION_PROSE", set(RELATION_PROSE), KINDS),
                              ("gen_mappings.STATUS_PILL", set(STATUS_PILL), STATUSES),
                              ("gen_mappings.STATUS_DESC", set(STATUS_DESC), STATUSES),
                              ("check_standards_currency.POLICY", set(POLICY), STATUSES)):
        if got != expect:
            findings.append("{} keys {{{}}} != {{{}}}".format(
                name, ", ".join(sorted(got)), ", ".join(sorted(expect))))


def check_deferred(root, manifests, findings):
    text = _read(root / ".aiqt" / "standards" / "README.md")
    m = re.search(r"Not yet vendored.*?(?:\n\n|\Z)", text, re.S)
    if not m:
        return  # no deferred paragraph is a legitimate state, not a missing surface
    para = m.group(0)
    for man in manifests.values():
        if man.name in para:
            findings.append("standards README: vendored framework {!r} still listed as not yet "
                            "vendored".format(man.name))


def check_subset_denominator(root, manifests, findings):
    """Defence in depth for the catalogue invariant: a subset manifest must never appear with an 'N of M'
    edition denominator anywhere it is rendered (the gen_mappings branch is the primary control; this
    catches a hand-edited page).

    Structure-aware, not same-line: the two render sites place the name and the coverage in different
    places. A registry row keeps both in one <tr>, but the reverse view renders the name in a <summary>
    and the coverage in a later <p> of the same <details>, so a same-line scan would miss a subset "N of
    M" there. The check splits the page into <tr> blocks and <details> blocks and associates a subset name
    with a denominator across the block, never by line proximity."""
    text = _read(root / "site" / "mappings.html")
    subset_names = {man.name for man in manifests.values() if man.catalogue == "subset"}
    # Registry rows: the name cell and the coverage cell live in one <tr>. A subset row must read
    # "N referenced (curated subset)", never "N of M".
    for row in re.findall(r"<tr\b.*?</tr>", text, re.S | re.I):
        if not N_OF_M.search(row):
            continue
        for name in subset_names:
            if name in row:
                findings.append("site/mappings.html: subset manifest {!r} rendered with an edition "
                                "denominator in a registry row".format(name))
    # Reverse view: the framework name is in the <summary>; its coverage is in a later <p> of the same
    # <details>. Match on the summary so a forward-view block (a rule-title summary) is not caught, then
    # scan the whole block so the name and the denominator are associated across the line break.
    for block in re.findall(r"<details\b.*?</details>", text, re.S | re.I):
        summ = re.search(r"<summary>(.*?)</summary>", block, re.S | re.I)
        if not summ:
            continue
        head = summ.group(1)
        for name in subset_names:
            if name in head and N_OF_M.search(block):
                findings.append("site/mappings.html: subset manifest {!r} rendered with an edition "
                                "denominator in the reverse view".format(name))


def check_json_subset_denominator(root, manifests, findings):
    """The catalogue invariant in the machine-readable export: a curated-subset framework in
    site/downloads/mappings.json must not carry an edition denominator (ids_total); only a full-edition
    manifest does. Fail-closed read (exit 2) on a missing/unreadable/invalid export; a subset key absent
    from the export, or one carrying a non-null ids_total, is a finding (exit 1)."""
    text = _read(root / "site" / "downloads" / "mappings.json")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise OSError("site/downloads/mappings.json is not valid JSON: {}".format(exc))
    frameworks = data.get("frameworks") if isinstance(data, dict) else None
    if not isinstance(frameworks, dict):
        raise OSError("site/downloads/mappings.json: expected a top-level 'frameworks' object")
    for man in manifests.values():
        if man.catalogue != "subset":
            continue
        stem = man.map_key[4:]  # strip the "map-" prefix; the JSON keys frameworks by stem
        entry = frameworks.get(stem)
        if entry is None:
            findings.append("site/downloads/mappings.json: subset framework {!r} ({}) missing from "
                            "frameworks".format(man.name, stem))
            continue
        if not isinstance(entry, dict):
            raise OSError("site/downloads/mappings.json: framework {!r} entry is not an object "
                          "(malformed export)".format(stem))
        if "ids_total" in entry:
            findings.append("site/downloads/mappings.json: subset framework {!r} carries an edition "
                            "denominator (ids_total={!r})".format(man.name, entry.get("ids_total")))


def run(root):
    findings = []
    try:
        manifests = load_manifests(root / ".aiqt" / "standards")
        if not manifests:
            print("error: no manifests under .aiqt/standards/; fail-closed", file=sys.stderr)
            return 2
        fams = expected_families(manifests)
        check_meta(root, fams, findings)
        check_disclosure(root, fams, findings)
        check_enum_table(root, findings)
        check_parity(findings)
        check_deferred(root, manifests, findings)
        check_subset_denominator(root, manifests, findings)
        check_json_subset_denominator(root, manifests, findings)
    except (ManifestError, OSError, ValueError, KeyError) as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} reference-fact issue(s):".format(len(findings)))
        for line in sorted(findings):
            print("  " + line)
        return 1
    print("PASS: reference facts agree with the manifests ({} manifest(s), {} families)".format(
        len(manifests), len(fams)))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(repo_root())


# --- self-test ----------------------------------------------------------------------------------------
# Deterministic fixtures assembled in a tempdir (the check_standards_currency/gen_gensrc pattern), each
# driving the real run()/checks so the mechanism under test is never mocked:
#   1. a clean fixture passes (exit 0),
#   2. a meta parenthetical missing one family -> finding (exit 1),
#   3. a disclosure claim naming a family with no manifest -> finding,
#   4. a schema `kind` row missing a kind -> finding,
#   5. a parity break (a POLICY key removed via a patched dict) -> finding,
#   6. a manifest whose name appears in the "Not yet vendored" paragraph -> finding,
#   7. a subset manifest rendered "3 of 3" in the fixture registry row -> finding (the CWE regression),
#   8. a missing site/mappings.html -> exit 2 (fail-closed, not a skip),
#   9. an unreadable standards dir (chmod 0) -> exit 2 via load_manifests/ensure_listable,
#  10. a manifest without `catalogue`, and one with `catalogue = "partial"` -> ManifestError exit 2
#      (the loader end of the invariant),
#  11. a publisher with no FAMILY_ALIAS entry -> exit 2,
#  12. a subset reverse-view <p> coverage mutated to "N of M" -> finding (the structure-aware case: the
#      name is in the <summary>, the denominator in a later <p>, so a same-line scan would miss it),
#  13. a subset framework carrying ids_total in the JSON export -> finding (the export invariant),
#  14. a missing site/downloads/mappings.json -> exit 2 (fail-closed, not a skip).
# (1, 7, 12, 13 are the invariant-bearing cases; the rest complete the fail-closed contract.)

_MANIFEST = (
    'map-key = "map-{k}"\nname = "{name}"\npublisher = "{pub}"\nedition = "{ed}"\n'
    'kind = "{kind}"\nstatus = "stable"\ncatalogue = "{cat}"\ncitation-unit = "control"\n'
    'id-pattern = "ST[0-9]{{2}}"\nsource-artefact = "self-test fixture"\nretrieved = "2026-01-01"\n'
    '[[id]]\ncode = "ST01"\ntitle = "one"\n[[id]]\ncode = "ST02"\ntitle = "two"\n'
    '[[id]]\ncode = "ST03"\ntitle = "three"\n'
)

_README = (
    "# Standards id-manifests\n\n## Manifest schema\n\n"
    "| field | meaning |\n| --- | --- |\n"
    "| `kind` | `risk` \\| `control` \\| `guidance` \\| `technique` (relation wording) |\n"
    "| `status` | `stable` \\| `beta` \\| `snapshot` (edition stability) |\n"
    "| `catalogue` | `full` \\| `subset` (complete edition or curated) |\n\n"
    "Not yet vendored (their keys stay inert until a manifest lands): Google SAIF and OWASP SCVS.\n"
)

# Two meta descriptions, a two-row registry, and a two-block reverse view mirroring the real render (the
# name in a <summary>, the coverage in a later <p>). The subset row and subset reverse block use the
# honest "curated subset" form; the full block legitimately reads "3 of 3".
_PAGE = (
    '<!doctype html>\n'
    '<meta name="description" content="A crosswalk to frameworks (ISO/IEC, OWASP). Titles only.">\n'
    '<meta property="og:description" content="A crosswalk to frameworks (ISO/IEC, OWASP). Titles only.">\n'
    '<tr><td>Fixture Full Framework</td><td>OWASP Foundation</td><td>1.0</td><td>3 of 3</td></tr>\n'
    '<tr><td>Fixture Subset Framework</td><td>ISO/IEC</td><td>2023</td>'
    '<td>2 referenced (curated subset)</td></tr>\n'
    '      <details class="more">\n'
    '        <summary>Fixture Full Framework (1.0)</summary>\n'
    '        <div class="inner">\n'
    '          <p>supports control. 3 of 3 identifiers referenced.</p>\n'
    '        </div>\n'
    '      </details>\n'
    '      <details class="more">\n'
    '        <summary>Fixture Subset Framework (2023)</summary>\n'
    '        <div class="inner">\n'
    '          <p>aligns with guidance. 2 identifiers referenced from a curated subset of the '
    'edition.</p>\n'
    '        </div>\n'
    '      </details>\n'
)

# The JSON export: a full framework carries ids_total, the subset omits it (the catalogue invariant in
# machine-readable form). Keys are the framework stems (map-key without the "map-" prefix).
_JSON = (
    '{\n'
    '  "frameworks": {\n'
    '    "full": {"name": "Fixture Full Framework", "catalogue": "full", "ids_cited": 3, '
    '"ids_total": 3},\n'
    '    "subset": {"name": "Fixture Subset Framework", "catalogue": "subset", "ids_cited": 2}\n'
    '  },\n'
    '  "mappings": []\n'
    '}\n'
)

_DISCLOSURE = (
    'title = "d"\nnote = "n"\nsite_base = "https://example.org"\n\n'
    '[[row]]\nid = "standards-mappings"\ntopic = "Standards mappings"\n'
    'claim = "A crosswalk to identifiers in frameworks (ISO/IEC, OWASP), each pinned."\n'
    'limitation = "A mapping asserts a relationship, not certification."\n'
    'evidence = [\n  { text = "How", href = "/mappings#methodology" },\n]\n'
)


def _write_fixture(base, manifests=None, readme=None, page=None, disclosure=None, json_export=None):
    """Write a complete clean fixture tree, overridable per component for a case's single mutation.
    `manifests` is a list of (stem, name, publisher, edition, kind, catalogue)."""
    if manifests is None:
        manifests = [("full", "Fixture Full Framework", "OWASP Foundation", "1.0", "control", "full"),
                     ("subset", "Fixture Subset Framework", "ISO/IEC", "2023", "guidance", "subset")]
    std = base / ".aiqt" / "standards"
    std.mkdir(parents=True)
    for stem, name, pub, ed, kind, cat in manifests:
        (std / (stem + ".toml")).write_text(
            _MANIFEST.format(k=stem, name=name, pub=pub, ed=ed, kind=kind, cat=cat), encoding="utf-8")
    (std / "README.md").write_text(readme if readme is not None else _README, encoding="utf-8")
    site = base / "site"
    site.mkdir(parents=True)
    (site / "mappings.html").write_text(page if page is not None else _PAGE, encoding="utf-8")
    downloads = site / "downloads"
    downloads.mkdir()
    (downloads / "mappings.json").write_text(
        json_export if json_export is not None else _JSON, encoding="utf-8")
    (base / "disclosure.toml").write_text(
        disclosure if disclosure is not None else _DISCLOSURE, encoding="utf-8")


def self_test_main():
    import io
    import os
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    def run_quiet(root):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-reference-facts-gate-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    skipped = []
    unreadable_std = None
    try:
        # 1. clean fixture passes.
        clean = tmp / "clean"
        _write_fixture(clean)
        if run_quiet(clean) != 0:
            failures.append("clean fixture expected exit 0")

        # 2. a meta parenthetical missing one family -> finding.
        meta = tmp / "meta"
        _write_fixture(meta, page=_PAGE.replace("frameworks (ISO/IEC, OWASP)", "frameworks (OWASP)", 1))
        if run_quiet(meta) != 1:
            failures.append("meta parenthetical missing a family expected exit 1")

        # 3. a disclosure claim naming a family with no manifest -> finding.
        disc = tmp / "disc"
        _write_fixture(disc, disclosure=_DISCLOSURE.replace(
            "frameworks (ISO/IEC, OWASP)", "frameworks (ISO/IEC, OWASP, NIST)"))
        if run_quiet(disc) != 1:
            failures.append("disclosure claim naming an absent family expected exit 1")

        # 4. a schema `kind` row missing a kind -> finding.
        enum = tmp / "enum"
        _write_fixture(enum, readme=_README.replace(
            "`risk` \\| `control` \\| `guidance` \\| `technique`", "`risk` \\| `control` \\| `guidance`"))
        if run_quiet(enum) != 1:
            failures.append("schema kind row missing a kind expected exit 1")

        # 4b. the schema `catalogue` row missing a value -> finding (guards catalogue-row coverage).
        cat_enum = tmp / "cat_enum"
        _write_fixture(cat_enum, readme=_README.replace(
            "`full` \\| `subset` (complete edition or curated)", "`full` (complete edition or curated)"))
        if run_quiet(cat_enum) != 1:
            failures.append("schema catalogue row missing a value expected exit 1")

        # 5. a parity break: temporarily remove a POLICY key via a patched module global, then restore.
        parity = tmp / "parity"
        _write_fixture(parity)
        module = sys.modules[__name__]
        saved = module.POLICY
        module.POLICY = {k: v for k, v in saved.items() if k != "beta"}
        try:
            rc = run_quiet(parity)
        finally:
            module.POLICY = saved
        if rc != 1:
            failures.append("a POLICY parity break expected exit 1")

        # 6. a manifest name in the "Not yet vendored" paragraph -> finding.
        deferred = tmp / "deferred"
        _write_fixture(deferred, readme=_README.replace(
            "Google SAIF and OWASP SCVS", "Google SAIF, OWASP SCVS, and Fixture Full Framework"))
        if run_quiet(deferred) != 1:
            failures.append("a vendored name in the deferred paragraph expected exit 1")

        # 7. a subset manifest rendered "3 of 3" -> finding (the CWE regression case).
        denom = tmp / "denom"
        _write_fixture(denom, page=_PAGE.replace(
            "<td>2 referenced (curated subset)</td>", "<td>3 of 3</td>"))
        if run_quiet(denom) != 1:
            failures.append("a subset manifest rendered 'N of M' expected exit 1")

        # 8. a missing site/mappings.html -> exit 2 (fail-closed, not a skip).
        nopage = tmp / "nopage"
        _write_fixture(nopage)
        (nopage / "site" / "mappings.html").unlink()
        if run_quiet(nopage) != 2:
            failures.append("a missing site/mappings.html expected exit 2 (fail-closed)")

        # 9. an unreadable standards dir (chmod 0) -> exit 2. Skipped where the runner bypasses DAC
        #    (root), observed via os.access, as the sibling gates do.
        unread = tmp / "unread"
        _write_fixture(unread)
        unreadable_std = unread / ".aiqt" / "standards"
        os.chmod(unreadable_std, 0)
        if os.access(unreadable_std, os.R_OK):
            skipped.append("9 unreadable-standards-dir")
        elif run_quiet(unread) != 2:
            failures.append("an unreadable standards dir expected exit 2 (fail-closed)")
        os.chmod(unreadable_std, 0o755)
        unreadable_std = None

        # 10. a manifest without `catalogue`, and one with `catalogue = "partial"` -> exit 2.
        nocat = tmp / "nocat"
        _write_fixture(nocat)
        (nocat / ".aiqt" / "standards" / "full.toml").write_text(
            _MANIFEST.format(k="full", name="Fixture Full Framework", pub="OWASP Foundation",
                             ed="1.0", kind="control", cat="full").replace(
                'catalogue = "full"\n', ""), encoding="utf-8")
        if run_quiet(nocat) != 2:
            failures.append("a manifest without catalogue expected exit 2 (fail-closed)")
        badcat = tmp / "badcat"
        _write_fixture(badcat)
        (badcat / ".aiqt" / "standards" / "full.toml").write_text(
            _MANIFEST.format(k="full", name="Fixture Full Framework", pub="OWASP Foundation",
                             ed="1.0", kind="control", cat="partial"), encoding="utf-8")
        if run_quiet(badcat) != 2:
            failures.append("a manifest with catalogue='partial' expected exit 2 (fail-closed)")

        # 11. a publisher with no FAMILY_ALIAS entry -> exit 2.
        noalias = tmp / "noalias"
        _write_fixture(noalias, manifests=[
            ("full", "Fixture Full Framework", "Unlisted Publisher", "1.0", "control", "full")])
        if run_quiet(noalias) != 2:
            failures.append("a publisher with no family alias expected exit 2 (fail-closed)")

        # 12. a subset reverse-view coverage mutated to "N of M" -> finding. The denominator lands in the
        #     reverse-view <p>, on a different line from the framework name in the <summary>, so this
        #     fails only because the check is structure-aware, not same-line.
        revsub = tmp / "revsub"
        _write_fixture(revsub, page=_PAGE.replace(
            "2 identifiers referenced from a curated subset of the edition", "2 of 61"))
        if run_quiet(revsub) != 1:
            failures.append("a subset reverse-view coverage of 'N of M' expected exit 1")

        # 13. a subset framework carrying ids_total in the JSON export -> finding (the export invariant).
        jsondenom = tmp / "jsondenom"
        _write_fixture(jsondenom, json_export=_JSON.replace(
            '"catalogue": "subset", "ids_cited": 2',
            '"catalogue": "subset", "ids_cited": 2, "ids_total": 61'))
        if run_quiet(jsondenom) != 1:
            failures.append("a subset framework carrying ids_total in the JSON export expected exit 1")

        # 13b. a subset framework entry that is valid JSON but NOT an object (a string) -> exit 2
        #      (malformed structure fails closed, not a clean pass).
        jsonstr = tmp / "jsonstr"
        _write_fixture(jsonstr, json_export=_JSON.replace(
            '"subset": {"name": "Fixture Subset Framework", "catalogue": "subset", "ids_cited": 2}',
            '"subset": "malformed-but-valid-json"'))
        if run_quiet(jsonstr) != 2:
            failures.append("a non-object subset framework entry expected exit 2 (fail-closed)")

        # 14. a missing site/downloads/mappings.json -> exit 2 (fail-closed, not a skip).
        nojson = tmp / "nojson"
        _write_fixture(nojson)
        (nojson / "site" / "downloads" / "mappings.json").unlink()
        if run_quiet(nojson) != 2:
            failures.append("a missing mappings.json expected exit 2 (fail-closed)")
    finally:
        if unreadable_std is not None:
            os.chmod(unreadable_std, 0o755)  # restore even on an unexpected early exit
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    note = ("" if not skipped else
            " NOTE: skipped {} case(s) the runner cannot exercise (chmod-0 still readable): {}"
            .format(len(skipped), ", ".join(skipped)))
    print("SELF-TEST PASS: a clean fixture passes; a meta/disclosure parenthetical mismatch, a schema-enum "
          "gap, a closed-set parity break, a vendored name in the deferred paragraph, a subset manifest "
          "rendered with an edition denominator in the registry row or the reverse-view <p>, and a subset "
          "framework carrying ids_total in the JSON export each report a finding (exit 1); and a missing "
          "page, a missing JSON export, an unreadable standards dir, a missing/invalid catalogue, and an "
          "unaliased publisher all fail closed (exit 2)" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
