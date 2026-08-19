#!/usr/bin/env python3
"""Generate the /disclosure matrix page and root DISCLOSURE.md from disclosure.toml.

Single source of truth for the two public disclosure faces, so they cannot diverge (the gen_roadmap.py
pattern). The overclaim guard is STRUCTURAL, not a lint: every [[row]] must carry a non-empty claim, a
non-empty limitation, and at least one evidence link, so a claim can never render without its paired
limitation and its pointer. The load step fails closed (exit 2) before anything renders on:

  1. an unknown key (top level, row, or evidence entry): a typo like `limitations` can never silently
     drop a guard;
  2. a row missing a non-empty id/topic/claim/limitation or a non-empty evidence array;
  3. an href that is neither site-internal ("/...") nor external ("https://..."): check_site.py then
     validates every internal target and anchor;
  4. a duplicate id, or an id that is not [a-z0-9-]+ (it becomes the HTML id disclosure-<id>);
  5. an en/em dash in any emitted text (check_no_dashes.py does not scan root toml, so this is the dash
     gate for disclosure.toml, exactly as gen_mappings.py:_no_dash is for the manifests);
  6. the literal placeholder marker "[[ARCHITECT" in any emitted text (defense in depth: unapproved
     scaffolding can never render to the public page);
  7. a `pending` key present but empty (a malformed row, not "no marker").

Byte-reproducible: no wall-clock timestamp is embedded, so --check is a stable drift gate.

  gen_disclosure.py            regenerate site/disclosure.html and DISCLOSURE.md
  gen_disclosure.py --check    exit 1 if either is out of date; exit 2 on malformed input
  gen_disclosure.py --self-test  assert each fail-closed guard refuses its malformed fixture
"""
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml, replace_block, reconcile  # noqa: E402

EN, EM = "–", "—"
PLACEHOLDER = "[[ARCHITECT"
ID_RE = re.compile(r"^[a-z0-9-]+$")
ALLOWED_TOP = {"title", "note", "site_base", "row"}
ALLOWED_ROW = {"id", "topic", "claim", "limitation", "evidence", "pending"}
ALLOWED_EV = {"text", "href"}
DEFAULT_BASE = "https://aiqt.ai"


def _text(value):        # element text content
    return html.escape(value, quote=False)


def _attr(value):        # attribute value (must escape quotes)
    return html.escape(value, quote=True)


def _md_text(value):     # markdown link text: neutralize brackets and backslashes
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _md_href(value):     # markdown link destination: angle-bracket it when it holds spaces or parens
    if any(ch in value for ch in " ()"):
        return "<" + value.replace("\\", "\\\\").replace("<", "\\<").replace(">", "\\>") + ">"
    return value


def _guard(value, where):
    """A required, emitted string: present, a non-empty non-whitespace str, no placeholder, no dash.
    Fail closed (ValueError) otherwise, so main() exits 2 rather than rendering a malformed value."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{}: missing or empty required text".format(where))
    if PLACEHOLDER in value:
        raise ValueError("{}: contains the unapproved placeholder marker {!r}".format(where, PLACEHOLDER))
    if EN in value or EM in value:
        raise ValueError("{}: emitted text contains an en/em dash: {!r}".format(where, value))
    return value


def validate(data):
    """Return the validated (title, note, site_base, rows) or raise ValueError. rows is a list of dicts
    with a validated id/topic/claim/limitation, an evidence list of {text, href}, and an optional
    pending string. This is the single fail-closed guard, exercised directly by --self-test."""
    if not isinstance(data, dict):
        raise ValueError("top level: expected a table")
    unknown = set(data) - ALLOWED_TOP
    if unknown:
        raise ValueError("top level: unknown key(s): {}".format(", ".join(sorted(unknown))))
    title = _guard(data.get("title"), "title")
    note = _guard(data.get("note"), "note")
    site_base = data.get("site_base", DEFAULT_BASE)
    _guard(site_base, "site_base")
    rows_in = data.get("row")
    if not isinstance(rows_in, list) or not rows_in:
        raise ValueError("row: expected a non-empty array of [[row]] tables")
    rows, seen_ids = [], set()
    for pos, row in enumerate(rows_in, 1):
        if not isinstance(row, dict):
            raise ValueError("row {}: expected a table".format(pos))
        unknown = set(row) - ALLOWED_ROW
        if unknown:
            raise ValueError("row {}: unknown key(s): {}".format(pos, ", ".join(sorted(unknown))))
        rid = _guard(row.get("id"), "row {} id".format(pos))
        if not ID_RE.match(rid):
            raise ValueError("row {}: id {!r} must match [a-z0-9-]+".format(pos, rid))
        if rid in seen_ids:
            raise ValueError("row {}: duplicate id {!r}".format(pos, rid))
        seen_ids.add(rid)
        where = "row {} ({})".format(pos, rid)
        topic = _guard(row.get("topic"), where + " topic")
        claim = _guard(row.get("claim"), where + " claim")
        limitation = _guard(row.get("limitation"), where + " limitation")
        ev_in = row.get("evidence")
        if not isinstance(ev_in, list) or not ev_in:
            raise ValueError("{}: evidence must be a non-empty array of links".format(where))
        evidence = []
        for ei, ev in enumerate(ev_in, 1):
            if not isinstance(ev, dict):
                raise ValueError("{}: evidence[{}] must be a table".format(where, ei))
            unknown = set(ev) - ALLOWED_EV
            if unknown:
                raise ValueError("{}: evidence[{}] unknown key(s): {}".format(
                    where, ei, ", ".join(sorted(unknown))))
            etext = _guard(ev.get("text"), "{} evidence[{}] text".format(where, ei))
            href = _guard(ev.get("href"), "{} evidence[{}] href".format(where, ei))
            if not (href.startswith("/") or href.startswith("https://")):
                raise ValueError("{}: evidence[{}] href {!r} must start with '/' or 'https://'".format(
                    where, ei, href))
            evidence.append({"text": etext, "href": href})
        pending = None
        if "pending" in row:
            pending = _guard(row.get("pending"), where + " pending")
        rows.append({"id": rid, "topic": topic, "claim": claim, "limitation": limitation,
                     "evidence": evidence, "pending": pending})
    return title, note, site_base, rows


def render_site(rows):
    label = '<b style="color:var(--ink)">{}:</b>'
    cards, last = [], len(rows) - 1
    for idx, row in enumerate(rows):
        style = ' style="margin-bottom:1.1rem"' if idx < last else ""
        links = " ".join('<a href="{}">{}</a>'.format(_attr(ev["href"]), _text(ev["text"]))
                         for ev in row["evidence"])
        parts = [
            '      <div class="card"{}>'.format(style),
            '        <h3 id="disclosure-{}">{}</h3>'.format(_attr(row["id"]), _text(row["topic"])),
            '        <p>{} {}</p>'.format(label.format("Claim"), _text(row["claim"])),
            '        <p>{} {}</p>'.format(label.format("Limitation"), _text(row["limitation"])),
            '        <p>{} {}</p>'.format(label.format("Evidence"), links),
        ]
        if row["pending"]:
            parts.append('        <p><span class="evidence-label">{}</span></p>'.format(
                _text(row["pending"])))
        parts.append('      </div>')
        cards.append("\n".join(parts))
    return "\n".join(cards)


def render_md(title, note, site_base, rows):
    def dest(href):
        return href if href.startswith("https://") else site_base + href
    lines = ["# " + title, "", note, ""]
    for row in rows:
        lines.append("## " + row["topic"])
        lines.append("")
        lines.append("Claim: " + row["claim"])
        lines.append("")
        lines.append("Limitation: " + row["limitation"])
        lines.append("")
        links = " ".join("[{}]({})".format(_md_text(ev["text"]), _md_href(dest(ev["href"])))
                         for ev in row["evidence"])
        lines.append("Evidence: " + links)
        lines.append("")
        if row["pending"]:
            lines.append("Pending: " + row["pending"])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _self_test():
    good = {
        "title": "T", "note": "N", "site_base": "https://aiqt.ai",
        "row": [{"id": "a", "topic": "A", "claim": "C", "limitation": "L",
                 "evidence": [{"text": "e", "href": "/evidence#release"}]}],
    }
    # (label, mutated data, must fail) for every guard class.
    bad = [
        ("unknown top key", {**good, "extra": 1}),
        ("unknown row key", {**good, "row": [{**good["row"][0], "limitations": "x"}]}),
        ("missing claim", {**good, "row": [{k: v for k, v in good["row"][0].items() if k != "claim"}]}),
        ("empty limitation", {**good, "row": [{**good["row"][0], "limitation": "   "}]}),
        ("missing evidence", {**good, "row": [{k: v for k, v in good["row"][0].items() if k != "evidence"}]}),
        ("empty evidence array", {**good, "row": [{**good["row"][0], "evidence": []}]}),
        ("evidence empty href", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": ""}]}]}),
        ("bad href scheme", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": "ftp://x"}]}]}),
        ("bad id chars", {**good, "row": [{**good["row"][0], "id": "Bad Id"}]}),
        ("duplicate id", {**good, "row": [good["row"][0], good["row"][0]]}),
        ("en dash", {**good, "row": [{**good["row"][0], "claim": "a" + EN + "b"}]}),
        ("placeholder", {**good, "row": [{**good["row"][0], "topic": PLACEHOLDER + "-D7]]"}]}),
        ("empty pending", {**good, "row": [{**good["row"][0], "pending": ""}]}),
        ("empty row array", {**good, "row": []}),
    ]
    failures = []
    for label, data in bad:
        try:
            validate(data)
            failures.append("MALFORMED INPUT ACCEPTED: {}".format(label))
        except ValueError:
            pass
    try:
        title, note, site_base, rows = validate(good)
        render_site(rows)
        render_md(title, note, site_base, rows)
    except ValueError as exc:
        failures.append("well-formed fixture REFUSED: {}".format(exc))
    if failures:
        print("FAIL: gen_disclosure self-test")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: gen_disclosure self-test ({} refusal classes, 1 happy path)".format(len(bad)))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return _self_test()
    check = "--check" in args
    root = repo_root()
    try:
        data = load_toml(root / "disclosure.toml")
    except (OSError, ValueError) as exc:
        print("error: cannot read disclosure.toml: {}".format(exc), file=sys.stderr)
        return 2
    try:
        title, note, site_base, rows = validate(data)
        site_inner = render_site(rows)
        md = render_md(title, note, site_base, rows)
    except ValueError as exc:
        print("error: disclosure.toml: {}".format(exc), file=sys.stderr)
        return 2
    drift = False
    if reconcile(root / "DISCLOSURE.md", md, check):
        print("drift: DISCLOSURE.md")
        drift = True
    page = root / "site" / "disclosure.html"
    if not page.exists():
        print("error: site/disclosure.html not found (expected generated target)", file=sys.stderr)
        return 2
    try:
        new_html = replace_block(page.read_text(encoding="utf-8"), "DISCLOSURE", site_inner)
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    if reconcile(page, new_html, check):
        print("drift: site/disclosure.html")
        drift = True
    if check and drift:
        print("run tools/gen_disclosure.py to regenerate")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
