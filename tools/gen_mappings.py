#!/usr/bin/env python3
"""Generate the public /mappings crosswalk page and its CSV/JSON exports from the rule corpus and the
pinned standards manifests.

Single source of truth: each rule's `map-<framework>-<fit>` frontmatter (validated by check_mappings)
plus the manifests under .aiqt/standards/ (the no-fabrication id source). The page, the CSV, and the JSON
are all DERIVED from one flat list of (rule, framework, id) rows, so the forward view, the reverse view,
the registry, and the two exports can never disagree. No second frontmatter or manifest parser: load_corpus
and load_manifests are reused verbatim.

Byte-reproducible: no wall-clock timestamp is embedded (each manifest's own `retrieved` date carries the
true freshness), so --check is a stable drift gate. A defensive `cid in manifest.id_set` recheck means a
fabricated id can never be rendered even if this runs before check_mappings; an emitted title carrying an
en/em dash fails closed here rather than as a red site-integrity gate later.

  gen_mappings.py           regenerate site/mappings.html and the two exports under site/downloads/
  gen_mappings.py --check   exit 1 if any of the three is out of date; exit 2 on malformed input
"""
import csv
import html
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, replace_block, reconcile  # noqa: E402
from _standards import load_manifests, ManifestError, natkey  # noqa: E402
import gen_rules  # noqa: E402  (reuse the one frontmatter parser + full corpus validation)
from gen_agents import sort_key  # noqa: E402  (canonical AIQT priority order for the rules)

# How the public page words each relation, keyed by the manifest's `kind`. One dict, used by every view
# and both exports, so the wording cannot diverge between them.
RELATION = {"risk": "addresses risk", "control": "supports control",
            "guidance": "aligns with guidance", "technique": "mitigates technique"}
# Reuse the existing site status pills (no colour-only encoding: the word carries the meaning).
STATUS_PILL = {"stable": "now", "beta": "next", "snapshot": "idea"}
BLOB = "https://github.com/jposluns/guardrails/blob/main/.claude/rules/{}"
NOTICE_URL = "https://github.com/jposluns/guardrails/blob/main/NOTICE"
EN, EM = "–", "—"

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces. site/mappings.html is a generated block inside
# a hand-authored page (the mappings markers), so it is recorded as kind block.
GENSRC_OUTPUTS = (
    {"target": "site/mappings.html", "kind": "block",
     "sources": (".aiqt/standards/", ".aiqt/core/rules/"),
     "regenerate": "python3 tools/gen_mappings.py"},
    {"target": "site/downloads/mappings.csv", "kind": "file",
     "sources": (".aiqt/standards/", ".aiqt/core/rules/"),
     "regenerate": "python3 tools/gen_mappings.py"},
    {"target": "site/downloads/mappings.json", "kind": "file",
     "sources": (".aiqt/standards/", ".aiqt/core/rules/"),
     "regenerate": "python3 tools/gen_mappings.py"},
)


def _text(value):        # element text content
    return html.escape(value, quote=False)


def _attr(value):        # attribute value (must escape quotes)
    return html.escape(value, quote=True)


def _no_dash(value, where):
    """Fail closed (exit 2 via ValueError) if emitted text carries an en/em dash, so a future manifest
    edit surfaces here rather than as a red site-integrity gate on the generated HTML."""
    if EN in value or EM in value:
        raise ValueError("{}: emitted text contains an en/em dash: {!r}".format(where, value))
    return value


def rule_title(path):
    """The rule's display title: the first body '# ' heading. There is deliberately no frontmatter title
    key (gen_rules._check_keys rejects unknown keys), so the H1 is the only source. Fail closed if none."""
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    body = text[end + 5:] if end != -1 else text
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        if stripped:
            break
    raise ValueError("{}: no body '# ' heading to use as the rule title".format(path.name))


def build_rows(corpus, manifests):
    """One flat row per (rule, framework, id) mapping, in canonical rule order then framework/fit key
    order then the manifest's natural id order. Every downstream artifact is derived from this list."""
    rows = []
    for src, fm, rel in corpus:
        keys = sorted(k for k in fm if k.startswith("map-"))
        if not keys:
            continue
        title = _no_dash(rule_title(src), src.name)
        for key in keys:
            base = key[:-6]            # strip the 6-char fit suffix ("-tight"/"-broad")
            fit = key[-5:]             # "tight" or "broad"
            manifest = manifests.get(base)
            if manifest is None:
                # Unreachable while MAP_KEYS is derived from the same manifests; a defensive guard for a
                # hand-edited or out-of-sync tree, matching validate_mappings.
                raise ValueError("{}: {}: no manifest under .aiqt/standards/ for this key".format(
                    src.name, key))
            for cid in fm[key]:
                if cid not in manifest.id_set:
                    # Defensive: check_mappings is the authoritative id gate, but never render a
                    # fabricated id even if this runs out of order.
                    raise ValueError("{}: {}: id '{}' is not in {} {}".format(
                        src.name, key, cid, manifest.name, manifest.edition))
                rows.append({
                    "rule_id": str(fm["corpus-id"]),
                    "rule_title": title,
                    "rule_source": BLOB.format(rel),
                    "framework": base[4:],                 # strip the "map-" prefix -> stem
                    "framework_name": _no_dash(manifest.name, manifest.map_key),
                    "publisher": manifest.publisher,
                    "edition": _no_dash(manifest.edition, manifest.map_key),
                    "kind": manifest.kind,
                    "relation": RELATION[manifest.kind],
                    "status": manifest.status,
                    "fit": fit,
                    "identifier": cid,
                    "identifier_title": _no_dash(manifest.titles[cid], manifest.map_key),
                })
    return rows


def registry_rows(manifests, rows):
    """Every manifest, in map-key order (matches the manifest dir), with its cited/total id coverage."""
    cited = {}
    for row in rows:
        cited.setdefault(row["framework"], set()).add(row["identifier"])
    out = []
    for map_key in sorted(manifests):
        manifest = manifests[map_key]
        stem = map_key[4:]
        out.append({
            "framework": stem,
            "name": _no_dash(manifest.name, map_key),
            "publisher": manifest.publisher,
            "edition": _no_dash(manifest.edition, map_key),
            "kind": manifest.kind,
            "relation": RELATION[manifest.kind],
            "status": manifest.status,
            "citation_unit": manifest.citation_unit,
            "source_artefact": manifest.source_artefact,
            "retrieved": manifest.retrieved,
            "ids_cited": len(cited.get(stem, ())),
            "ids_total": len(manifest.ids),
        })
    return out


def render_coverage(reg, rows):
    n_frameworks = sum(1 for r in reg if r["ids_cited"])
    n_rules = len({r["rule_id"] for r in rows})
    n_ids = len(rows)  # (rule, identifier) mapping pairs
    n_distinct = len({(r["framework"], r["identifier"]) for r in rows})
    return (
        '      <p class="lead">The crosswalk carries <strong>{ids}</strong> mappings from '
        '<strong>{rules}</strong> of the pack\'s rules to <strong>{distinct}</strong> identifiers '
        'across <strong>{fw}</strong> frameworks. Every mapped identifier is validated against a '
        'pinned-edition manifest before it can ship; the counts here are generated from that live '
        'state, never hand-entered.</p>'.format(
            ids=n_ids, distinct=n_distinct, fw=n_frameworks, rules=n_rules))


def render_registry(reg):
    head = (
        '      <div class="tablewrap">\n'
        '        <table class="dtable">\n'
        '          <thead><tr><th>Framework</th><th>Publisher</th><th>Edition</th>'
        '<th>Relation</th><th>Edition stability</th><th>Identifiers referenced</th></tr></thead>\n'
        '          <tbody>')
    body = []
    for r in reg:
        pill = STATUS_PILL[r["status"]]
        body.append(
            '            <tr><td>{name}</td><td>{pub}</td><td>{edition}</td><td>{relation}</td>'
            '<td><span class="pill {pill}">{status}</span></td>'
            '<td>{cited} of {total}</td></tr>'.format(
                name=_text(r["name"]), pub=_text(r["publisher"]), edition=_text(r["edition"]),
                relation=_text(r["relation"]), pill=_attr(pill), status=_text(r["status"]),
                cited=r["ids_cited"], total=r["ids_total"]))
    tail = '          </tbody>\n        </table>\n      </div>'
    return "\n".join([head, *body, tail])


def _group_ordered(rows, key):
    """Group rows preserving first-seen order of the key (rows already arrive in canonical order)."""
    groups = {}
    for row in rows:
        groups.setdefault(row[key], []).append(row)
    return groups


def render_forward(rows):
    """By rule: each rule discloses its mappings, grouped by framework, ids under each. Native
    <details>/<summary> so it is keyboard- and screen-reader-operable with no JS and no new CSS."""
    out = []
    for _rid, rrows in _group_ordered(rows, "rule_id").items():
        title = rrows[0]["rule_title"]
        source = rrows[0]["rule_source"]
        parts = [
            '      <details class="more">',
            '        <summary>{}</summary>'.format(_text(title)),
            '        <div class="inner">',
            '          <p><a href="{}">View this rule on GitHub</a></p>'.format(_attr(source)),
            '          <ul>']
        for _fw, frows in _group_ordered(rrows, "framework").items():
            head = frows[0]
            parts.append('            <li>{name} ({edition}): {relation}'.format(
                name=_text(head["framework_name"]), edition=_text(head["edition"]),
                relation=_text(head["relation"])))
            parts.append('              <ul>')
            for row in frows:
                parts.append('                <li>{cid}: {title} ({fit})</li>'.format(
                    cid=_text(row["identifier"]), title=_text(row["identifier_title"]),
                    fit=_text(row["fit"])))
            parts.append('              </ul>')
            parts.append('            </li>')
        parts += ['          </ul>', '        </div>', '      </details>']
        out.append("\n".join(parts))
    return "\n".join(out)


def render_reverse(rows, reg):
    """By framework/id: each framework discloses each cited id and the rules that cite it. Only cited ids
    are listed (labelling an uncited id 'reviewed, does not apply' would assert a review that never
    happened); the registry cited/total column states coverage honestly instead."""
    by_fw = _group_ordered(rows, "framework")
    out = []
    for r in reg:
        stem = r["framework"]
        frows = by_fw.get(stem)
        if not frows:
            continue
        parts = [
            '      <details class="more">',
            '        <summary>{name} ({edition})</summary>'.format(
                name=_text(r["name"]), edition=_text(r["edition"])),
            '        <div class="inner">',
            '          <p>{relation}. {cited} of {total} identifiers referenced.</p>'.format(
                relation=_text(r["relation"]), cited=r["ids_cited"], total=r["ids_total"]),
            '          <ul>']
        by_id = {}
        for row in frows:
            by_id.setdefault(row["identifier"], []).append(row)
        for cid in sorted(by_id, key=natkey):
            idrows = by_id[cid]
            parts.append('            <li>{cid}: {title}'.format(
                cid=_text(cid), title=_text(idrows[0]["identifier_title"])))
            parts.append('              <ul>')
            for row in idrows:
                parts.append(
                    '                <li><a href="{src}">{title}</a> ({fit})</li>'.format(
                        src=_attr(row["rule_source"]), title=_text(row["rule_title"]),
                        fit=_text(row["fit"])))
            parts.append('              </ul>')
            parts.append('            </li>')
        parts += ['          </ul>', '        </div>', '      </details>']
        out.append("\n".join(parts))
    return "\n".join(out)


def render_csv(rows):
    """Flat, one row per (rule, id) pair: the join key GRC ingest wants. Unix line endings for
    byte-reproducibility across platforms."""
    fields = ["rule_id", "rule_title", "rule_source", "framework", "framework_name",
              "edition", "relation", "fit", "identifier", "identifier_title"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def render_json(reg, rows):
    """Framework registry once plus a flat mappings array (the registry is not repeated per row). No
    wall-clock field, so --check stays reproducible."""
    frameworks = {}
    for r in reg:
        frameworks[r["framework"]] = {
            "name": r["name"], "publisher": r["publisher"], "edition": r["edition"],
            "kind": r["kind"], "relation": r["relation"], "status": r["status"],
            "citation_unit": r["citation_unit"], "source_artefact": r["source_artefact"],
            "retrieved": r["retrieved"], "ids_cited": r["ids_cited"], "ids_total": r["ids_total"],
        }
    mappings = [{
        "rule_id": row["rule_id"], "rule_title": row["rule_title"], "rule_source": row["rule_source"],
        "framework": row["framework"], "relation": row["relation"], "fit": row["fit"],
        "identifier": row["identifier"], "identifier_title": row["identifier_title"],
    } for row in rows]
    doc = {"frameworks": frameworks, "mappings": mappings}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def main():
    check = "--check" in sys.argv[1:]
    root = repo_root()
    try:
        manifests = load_manifests(root / ".aiqt" / "standards")
        src_dir = root / ".aiqt" / "core" / "rules"
        if not src_dir.is_dir():
            print("error: rule corpus not found at {}".format(src_dir), file=sys.stderr)
            return 2
        corpus = gen_rules.load_corpus(src_dir)
        corpus.sort(key=lambda item: sort_key(item[1]))
        rows = build_rows(corpus, manifests)
    except (ManifestError, ValueError, OSError, KeyError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    reg = registry_rows(manifests, rows)

    page = root / "site" / "mappings.html"
    if not page.exists():
        print("error: site/mappings.html not found (expected generated target)", file=sys.stderr)
        return 2
    try:
        text = page.read_text(encoding="utf-8")
        text = replace_block(text, "COVERAGE", render_coverage(reg, rows))
        text = replace_block(text, "REGISTRY", render_registry(reg))
        text = replace_block(text, "FORWARD", render_forward(rows))
        text = replace_block(text, "REVERSE", render_reverse(rows, reg))
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2

    drift = False
    if reconcile(page, text, check):
        print("drift: site/mappings.html")
        drift = True
    if reconcile(root / "site" / "downloads" / "mappings.csv", render_csv(rows), check):
        print("drift: site/downloads/mappings.csv")
        drift = True
    if reconcile(root / "site" / "downloads" / "mappings.json", render_json(reg, rows), check):
        print("drift: site/downloads/mappings.json")
        drift = True
    if check and drift:
        print("run tools/gen_mappings.py to regenerate")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
