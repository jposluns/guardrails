#!/usr/bin/env python3
"""Two-phase importer that vendors the MITRE CWE weakness catalogue into .aiqt/standards/cwe.toml.

This is AUTHORING tooling, not a CI gate: CI validates the committed cwe.toml through the existing
standards gates and never reaches the network. The importer is split so the one network step is
separable from the deterministic render, and the render is reproducible from staged bytes:

  python3 tools/import_cwe.py acquire --staging-dir <abs dir>            # network; authoring-time only
  python3 tools/import_cwe.py render  --staging-dir <abs dir> \\
      --output <repo>/.aiqt/standards/cwe.toml                          # offline, deterministic
  python3 tools/import_cwe.py --self-test                               # offline fixtures

Stdlib only (10-QUALI-minimize-dependencies): urllib.request for https, hashlib for provenance,
zipfile.open() streaming the single expected member (never extractall), xml.etree.ElementTree with
iterative clearing for bounded memory, tomllib for the round-trip re-load, json for TOML string
escaping and the provenance sidecar.

Fail-closed at every step (10-INTEG-check-fails-closed-on-unreadable, 10-ACCUR-guard-input-soundness):
a DOCTYPE prologue, an unexpected zip member, a wrong edition, a namespace mismatch yielding zero
elements, an unknown status string, a count that does not reconcile against MITRE's published figure,
a dash or control character in a title, or a TOML round-trip failure each ABORTS rather than writing.

Pin-after-observe (never pin-from-recall): the EXPECTED_* constants below were observed from the 4.20
artefact and MITRE's published figure at first acquisition (recorded in the private reference report),
so a re-run is deterministic and a changed pinned artefact halts and surfaces rather than shipping
silently.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: import_cwe requires Python 3.11+ (tomllib).")

# --- Pinned provenance and expectations for CWE List Version 4.20 (observed, not recalled) ----------
SOURCE_URL = "https://cwe.mitre.org/data/xml/cwec_v4.20.xml.zip"
COUNT_URL = "https://cwe.mitre.org/data/index.html"          # publishes "Total Weaknesses: <n>"
SOURCE_HOST = "cwe.mitre.org"
EXPECTED_MEMBER = "cwec_v4.20.xml"
EXPECTED_VERSION = "4.20"
NS = "http://cwe.mitre.org/cwe-7"                            # the document's default namespace
EXPECTED_ZIP_SHA256 = "3976f599e5e5200219a3108bb896d06e2a88fbb293369e1883cb423a5e9d7d50"
EXPECTED_XML_SHA256 = "1f5a78bd62e00f86436b4fe32d5034a57e8f0da88e4063b2072b664ae510912e"

# The complete CWE schema StatusEnumeration (cwe_schema_v7.3.xsd, the schema the 4.20 document
# references), read at acquisition, NOT recalled. An entry status outside this set is a schema change
# the importer must not silently absorb: it aborts and reports for a deliberate decision.
SCHEMA_STATUSES = frozenset({"Deprecated", "Draft", "Incomplete", "Obsolete", "Stable", "Usable"})
# The live set kept in the catalogue (allowlist, not denylist: an unknown future status becomes a hard
# abort above, never silent inclusion). Deprecated and Obsolete are the excluded, non-live statuses.
LIVE_STATUSES = frozenset({"Draft", "Incomplete", "Stable", "Usable"})
EXCLUDED_STATUSES = SCHEMA_STATUSES - LIVE_STATUSES

# Observed counts for 4.20: 969 Weakness elements, of which 25 are Deprecated -> 944 live. MITRE's
# published "Total Weaknesses" headline (index.html) is 944, i.e. it EQUALS the live count (it excludes
# Deprecated/Obsolete), the reading recorded in the manifest header and the provenance sidecar.
EXPECTED_TOTAL_ELEMENTS = 969
EXPECTED_ACTIVE = 944
EXPECTED_PUBLISHED_TOTAL = 944  # MITRE's published "Total Weaknesses" headline for 4.20 (== live count)

PROVENANCE_NAME = "cwe-provenance.json"
CODE_RE = re.compile(r"^CWE-\d+$")
EN_DASH = "–"
EM_DASH = "—"


class ImportError_(Exception):
    """A fail-closed importer abort. Carries a human-readable reason to stderr; never a silent write."""


# --- small helpers ---------------------------------------------------------------------------------
def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _https_get(url):
    """GET over https to the pinned CWE host only. Any other scheme or host is refused (SECC-egress
    -destinations): the importer talks to cwe.mitre.org and nowhere a redirect or argument could point."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != SOURCE_HOST:
        raise ImportError_("refusing a non-https or off-host URL: {!r}".format(url))
    req = urllib.request.Request(url, headers={"User-Agent": "aiqt-cwe-importer/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # nosec - host+scheme pinned above
        if resp.status != 200:
            raise ImportError_("{} returned HTTP {}".format(url, resp.status))
        final = urllib.parse.urlparse(resp.geturl())
        if final.scheme != "https" or final.hostname != SOURCE_HOST:
            raise ImportError_("a redirect left the pinned host: {!r}".format(resp.geturl()))
        return resp.read()


def _extract_member(zip_bytes):
    """Return (member_name, xml_bytes) for the single expected member, streamed via open() (never
    extractall). Abort on anything but exactly one regular member named EXPECTED_MEMBER."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        names = [i.filename for i in infos]
        if names != [EXPECTED_MEMBER]:
            raise ImportError_("archive members {!r} != expected [{!r}]".format(names, EXPECTED_MEMBER))
        with zf.open(EXPECTED_MEMBER) as handle:
            return EXPECTED_MEMBER, handle.read()


def _refuse_doctype(xml_bytes):
    """Refuse a document that declares a DOCTYPE or an entity before the root (XXE / entity-expansion
    guard). ElementTree carries no custom resolver, but a DOCTYPE prologue is refused outright."""
    prologue = xml_bytes[:4096].decode("utf-8", "replace").lower()
    if "<!doctype" in prologue or "<!entity" in prologue:
        raise ImportError_("document declares a DOCTYPE or entity in its prologue; refused")


def _published_count(index_html):
    """Extract MITRE's published 'Total Weaknesses: <n>' figure. Require exactly one unambiguous match;
    missing, duplicated, or contradictory count text is a hard failure (10-ACCUR-corroborate-external
    -claims): the reconciliation only means something if the source figure is unambiguous."""
    matches = re.findall(r"Total\s+Weaknesses\s*:\s*</b>\s*<span[^>]*>\s*(\d+)\s*</span>", index_html)
    if not matches:
        matches = re.findall(r"Total\s+Weaknesses\s*:\D{0,40}?(\d{2,5})", index_html)
    uniq = sorted(set(matches))
    if len(uniq) != 1:
        raise ImportError_("could not read a single 'Total Weaknesses' figure (got {!r})".format(uniq))
    return int(uniq[0])


# --- parse and filter ------------------------------------------------------------------------------
def _parse_weaknesses(xml_bytes, min_elements=100):
    """Return (version, date, rows, total_elements, status_breakdown). rows is the LIVE set only:
    [(int_id, code, title), ...]. Namespaced matching against the document's own default namespace, so
    an unqualified parse cannot silently yield zero. Aborts on a status outside the schema set, a
    malformed id, an empty title, or a duplicate id. min_elements guards against an absurdly small
    extraction on the real path (namespace mismatch); the offline fixtures pass a small floor."""
    _refuse_doctype(xml_bytes)
    root = ET.fromstring(xml_bytes)  # no external resolver; DOCTYPE already refused
    if root.tag != "{{{}}}Weakness_Catalog".format(NS):
        raise ImportError_("root is {!r}, not a namespaced Weakness_Catalog".format(root.tag))
    version = root.get("Version")
    if version != EXPECTED_VERSION:
        raise ImportError_("catalog Version {!r} != expected {!r}".format(version, EXPECTED_VERSION))
    catalog_date = root.get("Date") or ""
    container = root.find("{{{}}}Weaknesses".format(NS))
    if container is None:
        raise ImportError_("no <Weaknesses> container found (namespace mismatch?)")
    # ONLY direct <Weakness> children of <Weaknesses>; Categories/Views are sibling containers, excluded
    # by construction. Structure (Chain/Composite) is NOT filtered: those are Weakness entries and belong.
    elements = container.findall("{{{}}}Weakness".format(NS))
    if len(elements) < min_elements:
        raise ImportError_("only {} Weakness elements parsed; refusing an absurdly small extraction"
                           .format(len(elements)))
    breakdown = {}
    rows = []
    for el in elements:
        status = el.get("Status")
        if status not in SCHEMA_STATUSES:
            raise ImportError_("unknown status {!r} on CWE-{} (not in the schema set {}); refusing"
                               .format(status, el.get("ID"), sorted(SCHEMA_STATUSES)))
        breakdown[status] = breakdown.get(status, 0) + 1
        if status not in LIVE_STATUSES:
            continue
        raw_id = el.get("ID")
        if not raw_id or not raw_id.isdigit():
            raise ImportError_("non-numeric weakness ID {!r}".format(raw_id))
        code = "CWE-" + raw_id
        if not CODE_RE.fullmatch(code):
            raise ImportError_("id {!r} does not match ^CWE-\\d+$".format(code))
        title = (el.get("Name") or "").strip()
        if not title:
            raise ImportError_("empty Name on {}".format(code))
        if EN_DASH in title or EM_DASH in title:
            raise ImportError_("title for {} contains an en/em dash: {!r}".format(code, title))
        if any(ord(ch) < 0x20 for ch in title):
            raise ImportError_("title for {} contains a control character: {!r}".format(code, title))
        rows.append((int(raw_id), code, title))
    seen = set()
    for _n, code, _t in rows:
        if code in seen:
            raise ImportError_("duplicate id {}".format(code))
        seen.add(code)
    rows.sort(key=lambda r: r[0])                # ascending integer id == natural sort of CWE-<n>
    return version, catalog_date, rows, len(elements), breakdown


def _reconcile_count(active, total_elements, published):
    """Require MITRE's published figure to equal EXACTLY one of the two computed counts; record which.
    Neither -> a hard abort and a surfaced finding, never a delta left standing
    (10-TRUST-reconcile-record-against-reality)."""
    if published == active:
        return "active (MITRE's Total Weaknesses excludes Deprecated/Obsolete)"
    if published == total_elements:
        return "total (MITRE's Total Weaknesses includes every Weakness element)"
    raise ImportError_(
        "published Total Weaknesses {} matches neither active {} nor total {}; STOP and reconcile"
        .format(published, active, total_elements))


# --- phases ----------------------------------------------------------------------------------------
def acquire(staging_dir):
    staging = Path(staging_dir)
    staging.mkdir(parents=True, exist_ok=True)
    print("[acquire] GET {}".format(SOURCE_URL))
    zip_bytes = _https_get(SOURCE_URL)
    zip_sha = _sha256(zip_bytes)
    if zip_sha != EXPECTED_ZIP_SHA256:
        raise ImportError_("zip sha256 {} != pinned {}; the pinned artefact changed, STOP"
                           .format(zip_sha, EXPECTED_ZIP_SHA256))
    member, xml_bytes = _extract_member(zip_bytes)
    xml_sha = _sha256(xml_bytes)
    if xml_sha != EXPECTED_XML_SHA256:
        raise ImportError_("xml sha256 {} != pinned {}; the pinned artefact changed, STOP"
                           .format(xml_sha, EXPECTED_XML_SHA256))
    version, catalog_date, rows, total_elements, breakdown = _parse_weaknesses(xml_bytes)
    active = len(rows)
    if total_elements != EXPECTED_TOTAL_ELEMENTS or active != EXPECTED_ACTIVE:
        raise ImportError_("counts (total {}, active {}) != pinned (total {}, active {}); STOP"
                           .format(total_elements, active, EXPECTED_TOTAL_ELEMENTS, EXPECTED_ACTIVE))
    print("[acquire] GET {}".format(COUNT_URL))
    published = _published_count(_https_get(COUNT_URL).decode("utf-8", "replace"))
    reading = _reconcile_count(active, total_elements, published)
    (staging / member).write_bytes(xml_bytes)
    provenance = {
        "source_url": SOURCE_URL,
        "count_url": COUNT_URL,
        "member": member,
        "zip_sha256": zip_sha,
        "xml_sha256": xml_sha,
        "version": version,
        "catalog_date": catalog_date,
        "total_weakness_elements": total_elements,
        "active_after_filter": active,
        "status_breakdown": dict(sorted(breakdown.items())),
        "published_total_weaknesses": published,
        "count_reading": reading,
        "retrieved": date.today().isoformat(),
    }
    (staging / PROVENANCE_NAME).write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print("[acquire] version {} date {} | {} elements, {} live | published {} -> reading: {}"
          .format(version, catalog_date, total_elements, active, published, reading))
    print("[acquire] staged {} and {}".format(member, PROVENANCE_NAME))
    return provenance


def _render_toml(rows, prov):
    """Render the cwe.toml text from the live rows plus the provenance dict. Deterministic, LF-only."""
    breakdown = ", ".join("{} {}".format(v, k) for k, v in prov["status_breakdown"].items())
    header = [
        "# MITRE CWE: id manifest for the AIQT standards crosswalk. GENERATED by tools/import_cwe.py",
        "# from the version-pinned MITRE XML archive; do not hand-edit, regenerate.",
        "# kind=weakness, status=snapshot: pinned to a content edition. catalogue=full: the complete",
        "# live (non-deprecated, non-obsolete) CWE 4.20 Weakness set. See README.md.",
        "# source-url: {}".format(prov["source_url"]),
        "# source-zip-sha256: {}".format(prov["zip_sha256"]),
        "# source-xml-sha256: {}".format(prov["xml_sha256"]),
        "# counts: {} Weakness elements ({}); {} live after excluding Deprecated/Obsolete."
        .format(prov["total_weakness_elements"], breakdown, prov["active_after_filter"]),
        "# published figure {} at {}; reading: {}."
        .format(prov["published_total_weaknesses"], prov["count_url"], prov["count_reading"]),
    ]
    fields = [
        'map-key = "map-cwe"',
        'name = "MITRE CWE"',
        'publisher = "MITRE"',
        'edition = "{}"'.format(prov["version"]),
        'kind = "weakness"',
        'status = "snapshot"',
        'catalogue = "full"',
        'citation-unit = "weakness"',
        'id-pattern = "^CWE-\\\\d+$"',
        'source-artefact = "CWE List Version {} (cwec_v{}.xml, Weakness entries; Deprecated/Obsolete '
        'excluded)"'.format(prov["version"], prov["version"]),
        'retrieved = "{}"'.format(prov["retrieved"]),
        'url = "https://cwe.mitre.org/"',
    ]
    lines = header + [""] + fields + [""]
    for _n, code, title in rows:
        lines.append("[[id]]")
        lines.append('code = "{}"'.format(code))
        lines.append("title = " + json.dumps(title, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def render(staging_dir, output, *, expected_member=EXPECTED_MEMBER,
           expected_xml_sha256=EXPECTED_XML_SHA256, expected_total=EXPECTED_TOTAL_ELEMENTS,
           expected_active=EXPECTED_ACTIVE, expected_source_url=SOURCE_URL,
           expected_count_url=COUNT_URL, expected_published=EXPECTED_PUBLISHED_TOTAL, min_elements=100):
    staging = Path(staging_dir)
    prov_path = staging / PROVENANCE_NAME
    if not prov_path.is_file():
        raise ImportError_("no {} in staging; run acquire first".format(PROVENANCE_NAME))
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    # The provenance sidecar is claimant-controlled, so every load-bearing field is pinned to the
    # module constants rather than trusted from the record (10-ACCUR-guard-input-soundness).
    if prov.get("member") != expected_member:
        raise ImportError_("staged provenance member {!r} != pinned {!r}"
                           .format(prov.get("member"), expected_member))
    for key, pinned in (("source_url", expected_source_url), ("count_url", expected_count_url)):
        if prov.get(key) != pinned:
            raise ImportError_("provenance {} {!r} != pinned {!r}".format(key, prov.get(key), pinned))
    xml_path = staging / prov["member"]
    xml_bytes = xml_path.read_bytes()
    xml_sha = _sha256(xml_bytes)
    if xml_sha != expected_xml_sha256:
        raise ImportError_("staged {} sha256 {} != pinned {}; re-acquire from the pinned source"
                           .format(prov["member"], xml_sha, expected_xml_sha256))
    version, _date, rows, total_elements, breakdown = _parse_weaknesses(xml_bytes, min_elements=min_elements)
    active = len(rows)
    if total_elements != expected_total or active != expected_active:
        raise ImportError_("staged XML yields total={}, active={}; pinned total={}, active={}; re-acquire"
                           .format(total_elements, active, expected_total, expected_active))
    published = prov.get("published_total_weaknesses")
    if not isinstance(published, int) or isinstance(published, bool) or published != expected_published:
        raise ImportError_("provenance published_total_weaknesses {!r} != pinned {}"
                           .format(published, expected_published))
    reading = _reconcile_count(active, total_elements, expected_published)
    retrieved = prov.get("retrieved")
    if not isinstance(retrieved, str):
        raise ImportError_("provenance retrieved {!r} is not a string".format(retrieved))
    try:
        _rdate = date.fromisoformat(retrieved)
    except ValueError:
        raise ImportError_("provenance retrieved {!r} is not a valid ISO calendar date".format(retrieved))
    if _rdate < date.fromisoformat(_date):
        raise ImportError_("provenance retrieved {} precedes the catalogue release date {}"
                           .format(retrieved, _date))
    # Render provenance from AUTHORITATIVE values (computed XML facts + pinned constants), never the
    # claimant-controlled sidecar (10-ACCUR-guard-input-soundness): the sidecar cannot forge the
    # rendered edition, shas, counts, or reading, and a non-ISO retrieved is refused (TOML-injection guard).
    trusted = {
        "source_url": expected_source_url,
        "count_url": expected_count_url,
        "zip_sha256": EXPECTED_ZIP_SHA256,
        "xml_sha256": xml_sha,
        "version": version,
        "total_weakness_elements": total_elements,
        "active_after_filter": active,
        "status_breakdown": dict(sorted(breakdown.items())),
        "published_total_weaknesses": expected_published,
        "count_reading": reading,
        "retrieved": retrieved,
    }
    text = _render_toml(rows, trusted)
    # Round-trip: parse the rendered TOML, then a real load_manifests over a temp dir, BEFORE writing.
    parsed = tomllib.loads(text)
    if len(parsed.get("id", [])) != active:
        raise ImportError_("rendered [[id]] count {} != live rows {}".format(len(parsed.get("id", [])), active))
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(out.parent), prefix=".cwe-candidate-", suffix=".toml")
    os.close(fd)
    candidate = Path(tmp_name)
    try:
        candidate.write_text(text, encoding="utf-8", newline="\n")
        _verify_with_loader(candidate)
        os.replace(str(candidate), str(out))
    except BaseException:
        candidate.unlink(missing_ok=True)
        raise
    print("[render] wrote {} ({} live weakness rows, edition {})".format(out, active, version))


def _verify_with_loader(toml_path):
    """Load the just-written manifest through the repo's own load_manifests (full validation: pattern,
    uniqueness, natural sort, kind/status/catalogue closed sets) before declaring it written."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _standards import load_manifests, ManifestError  # noqa: E402
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-cwe-loadcheck-"))
    try:
        shutil.copy(toml_path, tmp / "cwe.toml")
        try:
            manifests = load_manifests(tmp)
        except ManifestError as exc:
            raise ImportError_("load_manifests rejected the rendered manifest: {}".format(exc))
        man = manifests.get("map-cwe")
        if man is None or man.catalogue != "full" or man.kind != "weakness":
            raise ImportError_("loaded manifest is not the expected map-cwe/full/weakness")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- self-test (offline, fixture-driven) -----------------------------------------------------------
def _fixture(version=EXPECTED_VERSION, extra="", doctype=False):
    doc = '<?xml version="1.0" encoding="UTF-8"?>'
    if doctype:
        doc += '<!DOCTYPE Weakness_Catalog>'
    doc += '<Weakness_Catalog Name="CWE" Version="{}" Date="2026-04-30" xmlns="{}">'.format(version, NS)
    doc += '<Weaknesses>'
    doc += '<Weakness ID="59" Name="Improper Link Resolution" Status="Draft"/>'
    doc += '<Weakness ID="363" Name="Race Condition Enabling Link Following" Status="Stable"/>'
    doc += '<Weakness ID="1" Name="A Usable Weakness" Status="Usable"/>'
    doc += '<Weakness ID="700" Name="A Deprecated Weakness" Status="Deprecated"/>'
    doc += '<Weakness ID="701" Name="An Obsolete Weakness" Status="Obsolete"/>'
    doc += extra
    doc += '</Weaknesses>'
    doc += '<Categories><Category ID="1000" Name="A Category" Status="Draft"/></Categories>'
    doc += '<Views><View ID="2000" Name="A View" Status="Draft"/></Views>'
    doc += '</Weakness_Catalog>'
    return doc.encode("utf-8")


def _expect_fail(label, fn):
    try:
        fn()
    except ImportError_:
        print("  ok (fails closed): {}".format(label))
        return
    raise SystemExit("SELF-TEST FAIL: {} should have failed closed".format(label))


def self_test():
    # Happy path: 3 live (Draft/Stable/Usable), Category/View/Deprecated/Obsolete excluded, sorted.
    _v, _d, rows, total, breakdown = _parse_weaknesses(_fixture(), min_elements=1)
    codes = [c for _n, c, _t in rows]
    assert codes == ["CWE-1", "CWE-59", "CWE-363"], codes
    assert total == 5, total
    assert breakdown == {"Deprecated": 1, "Draft": 1, "Obsolete": 1, "Stable": 1, "Usable": 1}, breakdown
    print("  ok (happy path): 3 live rows, natural-sorted, non-live and non-Weakness excluded")

    _expect_fail("absurdly small extraction (namespace mismatch proxy)",
                 lambda: _parse_weaknesses(_fixture(), min_elements=100))
    _expect_fail("unknown status",
                 lambda: _parse_weaknesses(_fixture(extra='<Weakness ID="9" Name="X" Status="Bogus"/>'),
                                           min_elements=1))
    _expect_fail("duplicate id",
                 lambda: _parse_weaknesses(_fixture(extra='<Weakness ID="59" Name="Dup" Status="Draft"/>'),
                                           min_elements=1))
    _expect_fail("wrong edition", lambda: _parse_weaknesses(_fixture(version="9.99"), min_elements=1))
    _expect_fail("DOCTYPE prologue", lambda: _parse_weaknesses(_fixture(doctype=True), min_elements=1))
    _expect_fail("en dash in a title",
                 lambda: _parse_weaknesses(_fixture(extra='<Weakness ID="9" Name="Bad'
                                          + EN_DASH + 'Title" Status="Draft"/>'), min_elements=1))
    _expect_fail("empty title",
                 lambda: _parse_weaknesses(_fixture(extra='<Weakness ID="9" Name="" Status="Draft"/>'),
                                           min_elements=1))
    _expect_fail("unexpected zip member",
                 lambda: _extract_member(_zip_of({"wrong.xml": b"x"})))
    _expect_fail("count reconciles to neither total nor active",
                 lambda: _reconcile_count(944, 969, 111))
    # off-by-one: a published figure equal to active-1 must not reconcile
    _expect_fail("off-by-one published count", lambda: _reconcile_count(3, 5, 2))
    assert _reconcile_count(944, 969, 944).startswith("active"), "944 must read as active"
    assert _reconcile_count(944, 969, 969).startswith("total"), "969 must read as total"
    # published-count parser: one unambiguous match required
    assert _published_count('<b>Total Weaknesses: </b> <span class="red">944</span>') == 944
    _expect_fail("no published count", lambda: _published_count("<html>nothing</html>"))
    _expect_fail("contradictory published counts",
                 lambda: _published_count('Total Weaknesses: <span>944</span>'
                                          'Total Weaknesses: <span>111</span>'))
    import shutil
    # render end-to-end: pins enforced, forged bytes rejected, destination preserved on failure.
    _stage = Path(tempfile.mkdtemp(prefix="aiqt-cwe-render-"))
    try:
        _xml = _fixture()
        (_stage / EXPECTED_MEMBER).write_bytes(_xml)
        _prov = {"member": EXPECTED_MEMBER, "xml_sha256": _sha256(_xml), "version": EXPECTED_VERSION,
                 "total_weakness_elements": 5, "active_after_filter": 3,
                 "published_total_weaknesses": 3, "source_url": SOURCE_URL, "count_url": COUNT_URL,
                 "status_breakdown": {"Deprecated": 1, "Draft": 1, "Obsolete": 1, "Stable": 1, "Usable": 1},
                 "count_reading": "active", "retrieved": "2026-04-30", "zip_sha256": _sha256(b"z")}
        (_stage / PROVENANCE_NAME).write_text(json.dumps(_prov), encoding="utf-8")
        _out = _stage / "cwe.toml"
        render(_stage, _out, expected_xml_sha256=_sha256(_xml), expected_total=5, expected_active=3,
               expected_published=3, min_elements=1)
        assert _out.is_file() and 'catalogue = "full"' in _out.read_text(encoding="utf-8")
        print("  ok (render happy path): fixture rendered and loader-verified")
        # forged bytes vs the REAL pinned sha -> rejected, no output written
        _expect_fail("render rejects staged bytes against the pinned sha",
                     lambda: render(_stage, _stage / "forged.toml"))
        assert not (_stage / "forged.toml").exists(), "no output on pin rejection"
        # wrong pinned active count -> rejected before any write; destination sentinel preserved
        _dest = _stage / "dest.toml"; _dest.write_text("SENTINEL", encoding="utf-8")
        _expect_fail("render fails closed on a count mismatch",
                     lambda: render(_stage, _dest, expected_xml_sha256=_sha256(_xml),
                                    expected_total=5, expected_active=999, min_elements=1))
        assert _dest.read_text(encoding="utf-8") == "SENTINEL", "destination preserved on count rejection"
        # loader-stage rejection AFTER the candidate write -> atomic replace must not touch the destination
        _dest2 = _stage / "dest2.toml"; _dest2.write_text("KEEP", encoding="utf-8")
        global _verify_with_loader
        _orig_vwl = _verify_with_loader
        def _boom(_p):
            raise ImportError_("forced loader rejection")
        _verify_with_loader = _boom
        try:
            _expect_fail("render fails closed when the loader rejects the candidate",
                         lambda: render(_stage, _dest2, expected_xml_sha256=_sha256(_xml),
                                        expected_total=5, expected_active=3, expected_published=3,
                                        min_elements=1))
            assert _dest2.read_text(encoding="utf-8") == "KEEP", "destination preserved on loader rejection"
            assert not list(_stage.glob(".cwe-candidate-*.toml")), "candidate cleaned up on failure"
        finally:
            _verify_with_loader = _orig_vwl
        print("  ok (render adversarial): pin, count, and loader rejections all fail closed and preserve the destination")
        # forged sidecar metadata must NOT reach the rendered output (render emits from computed facts)
        _forged = dict(_prov)
        _forged.update({"version": "9.99-FORGED", "xml_sha256": "deadbeef", "zip_sha256": "cafe",
                        "total_weakness_elements": -42, "active_after_filter": 1,
                        "status_breakdown": {"Draft": 999}, "count_reading": "BOGUS"})
        (_stage / PROVENANCE_NAME).write_text(json.dumps(_forged), encoding="utf-8")
        _outf = _stage / "cwe-forged.toml"
        render(_stage, _outf, expected_xml_sha256=_sha256(_xml), expected_total=5, expected_active=3,
               expected_published=3, min_elements=1)
        _r = _outf.read_text(encoding="utf-8")
        assert 'edition = "4.20"' in _r and "9.99-FORGED" not in _r, "forged edition must not ship"
        assert _sha256(_xml) in _r and "deadbeef" not in _r, "forged xml sha must not ship"
        assert "-42" not in _r and "BOGUS" not in _r and "999" not in _r, "forged counts/reading must not ship"
        print("  ok (metadata authoritative): a forged sidecar's version/sha/counts never reach the render")
        # a non-ISO retrieved fails closed (blocks a TOML string breakout through that field)
        _inj = dict(_prov); _inj["retrieved"] = "2026-01-01\"\ninjected = \"x"
        (_stage / PROVENANCE_NAME).write_text(json.dumps(_inj), encoding="utf-8")
        _expect_fail("render rejects a non-ISO retrieved (TOML-injection guard)",
                     lambda: render(_stage, _stage / "cwe-inj.toml", expected_xml_sha256=_sha256(_xml),
                                    expected_total=5, expected_active=3, expected_published=3,
                                    min_elements=1))
        # forged published headline must fail closed (pinned to the MITRE 4.20 figure)
        _fp = dict(_prov); _fp["published_total_weaknesses"] = 969
        (_stage / PROVENANCE_NAME).write_text(json.dumps(_fp), encoding="utf-8")
        _expect_fail("render rejects a forged published headline",
                     lambda: render(_stage, _stage / "cwe-fp.toml", expected_xml_sha256=_sha256(_xml),
                                    expected_total=5, expected_active=3, expected_published=3, min_elements=1))
        # retrieved before the catalogue release date fails closed
        _rb = dict(_prov); _rb["retrieved"] = "1900-01-01"
        (_stage / PROVENANCE_NAME).write_text(json.dumps(_rb), encoding="utf-8")
        _expect_fail("render rejects a retrieved date before the release date",
                     lambda: render(_stage, _stage / "cwe-rb.toml", expected_xml_sha256=_sha256(_xml),
                                    expected_total=5, expected_active=3, expected_published=3, min_elements=1))
        # a shaped-but-impossible calendar date fails closed
        _ri = dict(_prov); _ri["retrieved"] = "2026-99-99"
        (_stage / PROVENANCE_NAME).write_text(json.dumps(_ri), encoding="utf-8")
        _expect_fail("render rejects an impossible calendar date",
                     lambda: render(_stage, _stage / "cwe-ri.toml", expected_xml_sha256=_sha256(_xml),
                                    expected_total=5, expected_active=3, expected_published=3, min_elements=1))
        (_stage / PROVENANCE_NAME).write_text(json.dumps(_prov), encoding="utf-8")
    finally:
        shutil.rmtree(_stage, ignore_errors=True)
    print("SELF-TEST: PASS")


def _zip_of(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import the MITRE CWE weakness catalogue.")
    parser.add_argument("--self-test", action="store_true", help="run offline fixture self-test")
    sub = parser.add_subparsers(dest="cmd")
    pa = sub.add_parser("acquire", help="download and stage the pinned archive (network)")
    pa.add_argument("--staging-dir", required=True)
    pr = sub.add_parser("render", help="render cwe.toml from staged bytes (offline)")
    pr.add_argument("--staging-dir", required=True)
    pr.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.self_test:
            self_test()
            return 0
        if args.cmd == "acquire":
            acquire(args.staging_dir)
            return 0
        if args.cmd == "render":
            render(args.staging_dir, args.output)
            return 0
        parser.print_help()
        return 2
    except ImportError_ as exc:
        print("error: {}; fail-closed (nothing written)".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
