#!/usr/bin/env python3
"""Generate the /disclosure matrix page and root DISCLOSURE.md from disclosure.toml.

Single source of truth for the two public disclosure faces, so they cannot diverge (the gen_roadmap.py
pattern). The overclaim guard is STRUCTURAL, not a lint: every [[row]] must carry a non-empty claim, a
non-empty limitation, and at least one evidence link, so a claim can never render without its paired
limitation and its pointer. The load step fails closed (exit 2) before anything renders on:

  1. an unknown key (top level, row, or evidence entry): a typo like `limitations` can never silently
     drop a guard;
  2. a row missing a non-empty id/topic/claim/limitation or a non-empty evidence array;
  3. an href that is not a well-formed site-internal path or external HTTPS URL: the href is PARSED,
     not prefix-matched, so a hostless "https://" and a scheme-relative "//host/x" both fail closed
     (a prefix match let them render). Backslashes and control/whitespace characters are rejected up
     front, so a browser-normalized "/\\evil" or "/\\/evil" open redirect cannot slip past the
     leading-slash test; any port on an external URL must be numeric. An internal link is one leading
     slash with no scheme and no netloc; an external link is https with a non-empty host. check_site.py
     then validates every internal target and anchor;
  4. a duplicate id, or an id that is not [a-z0-9-]+ (it becomes the HTML id disclosure-<id>);
  5. an en/em dash in any emitted text (check_no_dashes.py does not scan root toml, so this is the dash
     gate for disclosure.toml, exactly as gen_mappings.py:_no_dash is for the manifests);
  6. the literal placeholder marker "[[ARCHITECT" in any emitted text (defense in depth: unapproved
     scaffolding can never render to the public page);
  7. a `pending` key present but empty (a malformed row, not "no marker").
  8. a missing or malformed [[code]]/[[noncode]] shipped-path declaration: both arrays required and
     non-empty, every row a table with only its allowed keys and a non-empty whitespace-free prefix
     that is unique across both arrays, and on a [[code]] row a guarded plain-language label;
  9. a what-it-is-made-of claim without the [[CODE-PATHS]] marker, or the marker anywhere outside a
     claim or limitation (title, note, site_base, topic, pending, evidence text and href, a declared
     prefix, and a code label all refuse it, each asserted by a marker-naming diagnostic). The
     marker renders to the shipped-code list built from the [[code]] labels
     (declaration order, distinct labels once, joined with semicolons), so the public sentence and
     the release accounting in tools/check_reference_facts.py share one declared source.

Byte-reproducible: no wall-clock timestamp is embedded, so --check is a stable drift gate.

  gen_disclosure.py            regenerate site/disclosure.html and DISCLOSURE.md
  gen_disclosure.py --check    exit 1 if either is out of date; exit 2 on malformed input
  gen_disclosure.py --self-test  assert each fail-closed guard refuses its malformed fixture
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_disclosure.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import html
import re
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import (is_external_url, linked_dir_failures, repo_root, load_toml, replace_block,  # noqa: E402
                         reconcile, target_dir_present)

EN, EM = "–", "—"
PLACEHOLDER = "[[ARCHITECT"
ID_RE = re.compile(r"^[a-z0-9-]+$")
ALLOWED_TOP = {"title", "note", "site_base", "row", "code", "noncode"}
ALLOWED_ROW = {"id", "topic", "claim", "limitation", "evidence", "pending"}
ALLOWED_EV = {"text", "href"}
DEFAULT_BASE = "https://aiqt.ai"

# The what-it-is-made-of claim carries this marker; validate() replaces it with the shipped-code list
# built from the [[code]] labels, so the public sentence and the release accounting share one declared
# source (tools/check_reference_facts.py imports shipped_declaration below and checks the same rows
# against the release manifest). The marker is REQUIRED in that claim, allowed in any claim or
# limitation, and refused anywhere else.
MARKER = "[[CODE-PATHS]]"
CODE_ROW_ID = "what-it-is-made-of"
ALLOWED_CODE = {"prefix", "label"}
ALLOWED_NONCODE = {"prefix"}

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces. site/disclosure.html is a generated block
# inside a hand-authored page (the disclosure markers), so it is recorded as kind block.
GENSRC_OUTPUTS = (
    {"target": "DISCLOSURE.md", "kind": "file",
     "sources": ("disclosure.toml",), "regenerate": "python3 tools/gen_disclosure.py"},
    {"target": "site/disclosure.html", "kind": "block",
     "sources": ("disclosure.toml",), "regenerate": "python3 tools/gen_disclosure.py"},
)


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


def _check_href(href, where):
    """Fail closed unless href is a well-formed site-internal path or external HTTPS URL. Parse it
    (urlsplit), never prefix-match: a prefix check on 'https://' or '/' admits a hostless 'https://'
    and a scheme-relative '//host/x'. An internal link is a single leading slash with no scheme and
    no netloc; an external link is https with a non-empty host and, if a port is present, a numeric
    one. Everything else raises ValueError.

    Backslashes and control/whitespace characters are rejected UP FRONT, before the path/netloc
    tests: a browser normalizes a backslash to '/', so a path like '/\\evil' or '/\\/evil' is a
    scheme-relative '//evil' open redirect in disguise that the leading-slash test alone would pass;
    a control or whitespace character has no place in a static href and can hide such a payload."""
    if "\\" in href:
        raise ValueError(
            "{}: href {!r} contains a backslash (a disguised scheme-relative redirect); "
            "use '/' in an internal path".format(where, href))
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f or ch.isspace() for ch in href):
        raise ValueError(
            "{}: href {!r} contains a control or whitespace character".format(where, href))
    parts = urlsplit(href)
    if parts.scheme:
        if parts.scheme.lower() != "https" or not parts.hostname:
            raise ValueError(
                "{}: href {!r} must be an https URL with a host, or a site-internal '/path'".format(
                    where, href))
        try:
            parts.port  # accessing a non-numeric port raises ValueError; reject it fail-closed
        except ValueError:
            raise ValueError(
                "{}: href {!r} has a non-numeric port".format(where, href)) from None
    elif parts.netloc:
        # a scheme-relative "//host/x" has no scheme but a netloc: not an internal path, fail closed
        raise ValueError("{}: href {!r} is scheme-relative (//host); use https:// or '/path'".format(
            where, href))
    elif not (href.startswith("/") and not href.startswith("//")):
        raise ValueError(
            "{}: href {!r} must be an internal '/path' or an external https URL".format(where, href))
    return href


def shipped_declaration(data):
    """Validated ([[code]] rows, [[noncode]] rows) of the shipped-path declaration, or ValueError.
    Each row carries a non-empty, whitespace-free path prefix, unique across BOTH arrays (a path is
    classified by the LONGEST declared prefix that starts it, so declaring a tree and a subtree is
    unambiguous and no ordering rule exists); each [[code]] row carries a plain-language label under
    the _guard rules, with the marker itself refused. Both arrays are required and non-empty: the
    public sentence renders from [[code]], and the release accounting needs both. Exercised directly
    by --self-test and imported by tools/check_reference_facts.py, so the prose and the gate cannot
    validate two different shapes."""
    if not isinstance(data, dict):
        raise ValueError("top level: expected a table")
    out, seen = [], set()
    for key, allowed in (("code", ALLOWED_CODE), ("noncode", ALLOWED_NONCODE)):
        rows_in = data.get(key)
        if not isinstance(rows_in, list) or not rows_in or not all(isinstance(r, dict) for r in rows_in):
            raise ValueError("{}: expected a non-empty array of [[{}]] tables".format(key, key))
        rows = []
        for pos, row in enumerate(rows_in, 1):
            where = "{} row {}".format(key, pos)
            unknown = set(row) - allowed
            if unknown:
                raise ValueError("{}: unknown key(s): {}".format(where, ", ".join(sorted(unknown))))
            prefix = row.get("prefix")
            if not isinstance(prefix, str) or not prefix or any(ch.isspace() for ch in prefix):
                raise ValueError("{}: prefix must be a non-empty string with no whitespace".format(where))
            if MARKER in prefix:
                raise ValueError("{}: the {} marker belongs only in a row claim or limitation".format(
                    where, MARKER))
            if prefix in seen:
                raise ValueError("{}: duplicate prefix {!r}".format(where, prefix))
            seen.add(prefix)
            entry = {"prefix": prefix}
            if key == "code":
                label = _guard(row.get("label"), where + " label")
                if MARKER in label:
                    raise ValueError("{}: label contains the {} marker".format(where, MARKER))
                entry["label"] = label
            rows.append(entry)
        out.append(rows)
    return out[0], out[1]


def code_paths_list(code_rows):
    """The shipped-code list the marker renders to: the [[code]] labels in declaration order, each
    distinct label once (two prefixes may share one label and read as one list item), joined with
    semicolons because the labels themselves hold commas."""
    labels = []
    for row in code_rows:
        if row["label"] not in labels:
            labels.append(row["label"])
    if len(labels) == 1:
        return labels[0]
    return "; ".join(labels[:-1]) + "; and " + labels[-1]


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
            _check_href(href, "{} evidence[{}] href".format(where, ei))
            evidence.append({"text": etext, "href": href})
        pending = None
        if "pending" in row:
            pending = _guard(row.get("pending"), where + " pending")
        rows.append({"id": rid, "topic": topic, "claim": claim, "limitation": limitation,
                     "evidence": evidence, "pending": pending})
    code_rows, _ = shipped_declaration(data)
    listing = code_paths_list(code_rows)
    for where, value in (("title", title), ("note", note), ("site_base", site_base)):
        if MARKER in value:
            raise ValueError("{}: the {} marker belongs only in a row claim or limitation".format(
                where, MARKER))
    made_of_has_marker = False
    for row in rows:
        for field in ("topic", "pending"):
            if row[field] and MARKER in row[field]:
                raise ValueError("row {} {}: the {} marker belongs only in a claim or limitation".format(
                    row["id"], field, MARKER))
        for ev in row["evidence"]:
            if MARKER in ev["text"] or MARKER in ev["href"]:
                raise ValueError("row {} evidence: the {} marker belongs only in a claim or "
                                 "limitation".format(row["id"], MARKER))
        if row["id"] == CODE_ROW_ID and MARKER in row["claim"]:
            made_of_has_marker = True
        row["claim"] = row["claim"].replace(MARKER, listing)
        row["limitation"] = row["limitation"].replace(MARKER, listing)
    if not made_of_has_marker:
        raise ValueError("row {}: a claim carrying the {} marker is required, so the public "
                         "shipped-code list renders only from the [[code]] declaration".format(
                             CODE_ROW_ID, MARKER))
    return title, note, site_base, rows


def render_site(rows):
    label = '<b style="color:var(--ink)">{}:</b>'
    cards, last = [], len(rows) - 1
    for idx, row in enumerate(rows):
        style = ' style="margin-bottom:1.1rem"' if idx < last else ""
        def _ev_anchor(ev):
            href = ev["href"]
            ext = ' target="_blank" rel="noopener noreferrer"' if is_external_url(href) else ""
            return '<a href="{}"{}>{}</a>'.format(_attr(href), ext, _text(ev["text"]))
        links = " ".join(_ev_anchor(ev) for ev in row["evidence"])
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


# A well-formed disclosure.toml and page for the tree the linked-directory vectors render: the same
# declaration as the in-memory good fixture below, in TOML.
_TREE_TOML = (
    'title = "T"\nnote = "N"\nsite_base = "https://aiqt.ai"\n\n'
    '[[code]]\nprefix = "tools/"\nlabel = "the tooling under tools/"\n\n'
    '[[code]]\nprefix = "site/"\nlabel = "the JavaScript under site/"\n\n'
    '[[noncode]]\nprefix = "README.md"\n\n'
    '[[row]]\nid = "what-it-is-made-of"\ntopic = "A"\nclaim = "C: ' + MARKER + '."\nlimitation = "L"\n'
    'evidence = [{ text = "e", href = "/evidence#release" }]\n'
)
_TREE_PAGE = ("<main>\n      <!-- DISCLOSURE:BEGIN (generated) -->\n      placeholder\n"
              "      <!-- DISCLOSURE:END -->\n</main>\n")


def _write_tree(root):
    """A synthetic tree with disclosure.toml and site/disclosure.html, for run(root, check)."""
    root = Path(root)
    (root / "disclosure.toml").write_text(_TREE_TOML, encoding="utf-8")
    (root / "site").mkdir()
    (root / "site" / "disclosure.html").write_text(_TREE_PAGE, encoding="utf-8")


def _self_test():
    good = {
        "title": "T", "note": "N", "site_base": "https://aiqt.ai",
        "code": [{"prefix": "tools/", "label": "the tooling under tools/"},
                 {"prefix": "site/", "label": "the JavaScript under site/"}],
        "noncode": [{"prefix": "README.md"}],
        "row": [{"id": "what-it-is-made-of", "topic": "A", "claim": "C: " + MARKER + ".",
                 "limitation": "L",
                 "evidence": [{"text": "e", "href": "/evidence#release"}]}],
    }
    # (label, mutated data[, expected diagnostic substring]) for every guard class. Every marker
    # vector carries a marker-naming expected substring, so the docstring claim that each marker
    # refusal is asserted by a marker-naming diagnostic is pinned by the assertions themselves.
    bad = [
        ("unknown top key", {**good, "extra": 1}),
        ("unknown row key", {**good, "row": [{**good["row"][0], "limitations": "x"}]}),
        ("missing claim", {**good, "row": [{k: v for k, v in good["row"][0].items() if k != "claim"}]}),
        ("empty limitation", {**good, "row": [{**good["row"][0], "limitation": "   "}]}),
        ("missing evidence", {**good, "row": [{k: v for k, v in good["row"][0].items() if k != "evidence"}]}),
        ("empty evidence array", {**good, "row": [{**good["row"][0], "evidence": []}]}),
        ("evidence empty href", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": ""}]}]}),
        ("bad href scheme", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": "ftp://x"}]}]}),
        ("hostless https href", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": "https://"}]}]}),
        ("scheme-relative href", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": "//evil.example/x"}]}]}),
        ("scheme-relative host-only href", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": "//cdn.example"}]}]}),
        ("backslash-redirect href", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": "/\\evil.example"}]}]}),
        ("backslash-slash-redirect href", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": "/\\/evil.example"}]}]}),
        ("control-char href", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": "/ok\x01path"}]}]}),
        ("non-numeric port href", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": "https://host:notaport/x"}]}]}),
        ("hostless relative href", {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": "evidence#x"}]}]}),
        ("bad id chars", {**good, "row": [{**good["row"][0], "id": "Bad Id"}]}),
        ("duplicate id", {**good, "row": [good["row"][0], good["row"][0]]}),
        ("en dash", {**good, "row": [{**good["row"][0], "claim": "a" + EN + "b"}]}),
        ("placeholder", {**good, "row": [{**good["row"][0], "topic": PLACEHOLDER + "-D7]]"}]}),
        ("empty pending", {**good, "row": [{**good["row"][0], "pending": ""}]}),
        ("empty row array", {**good, "row": []}),
        ("missing code declaration", {k: v for k, v in good.items() if k != "code"}),
        ("empty code declaration", {**good, "code": []}),
        ("non-table code row", {**good, "code": ["tools/"]}),
        ("code row unknown key", {**good, "code": [{**good["code"][0], "extra": 1}, good["code"][1]]}),
        ("code row empty prefix", {**good, "code": [{"prefix": "", "label": "x"}, good["code"][1]]}),
        ("code row whitespace prefix", {**good, "code": [{"prefix": "to ols/", "label": "x"}, good["code"][1]]}),
        ("code row missing label", {**good, "code": [{"prefix": "tools/"}, good["code"][1]]}),
        ("code row dashed label", {**good, "code": [{"prefix": "tools/", "label": "a" + EN + "b"}, good["code"][1]]}),
        ("marker inside a code label", {**good, "code": [{"prefix": "tools/", "label": "x " + MARKER}, good["code"][1]]},
         "label contains the " + MARKER + " marker"),
        ("duplicate prefix across arrays", {**good, "noncode": [{"prefix": "tools/"}]}),
        ("missing noncode declaration", {k: v for k, v in good.items() if k != "noncode"}),
        ("noncode row with a label", {**good, "noncode": [{"prefix": "README.md", "label": "x"}]}),
        ("made-of claim without the marker", {**good, "row": [{**good["row"][0], "claim": "C"}]},
         "a claim carrying the " + MARKER + " marker is required"),
        ("marker in a topic", {**good, "row": [{**good["row"][0], "topic": "T " + MARKER}]},
         "the " + MARKER + " marker belongs only in a claim or limitation"),
        ("marker in the note", {**good, "note": "N " + MARKER},
         "the " + MARKER + " marker belongs only in a row claim or limitation"),
        ("marker in the title", {**good, "title": "T " + MARKER},
         "the " + MARKER + " marker belongs only in a row claim or limitation"),
        ("marker in site_base", {**good, "site_base": "https://aiqt.ai/" + MARKER},
         "the " + MARKER + " marker belongs only in a row claim or limitation"),
        ("marker in a pending", {**good, "row": [{**good["row"][0], "pending": "P " + MARKER}]},
         "the " + MARKER + " marker belongs only in a claim or limitation"),
        ("marker in an evidence text", {**good, "row": [{**good["row"][0], "evidence": [
         {"text": "e " + MARKER, "href": "/evidence#release"}]}]},
         "the " + MARKER + " marker belongs only in a claim or limitation"),
        ("marker in an evidence href", {**good, "row": [{**good["row"][0], "evidence": [
         {"text": "e", "href": "/evidence" + MARKER}]}]},
         "the " + MARKER + " marker belongs only in a claim or limitation"),
        ("marker in a code prefix", {**good, "code": [{"prefix": "tools/" + MARKER, "label": "x"},
         good["code"][1]]},
         "the " + MARKER + " marker belongs only in a row claim or limitation"),
        ("marker in a noncode prefix", {**good, "noncode": [{"prefix": "README.md" + MARKER}]},
         "the " + MARKER + " marker belongs only in a row claim or limitation"),
    ]
    failures = []
    for entry in bad:
        label, data = entry[0], entry[1]
        expect = entry[2] if len(entry) > 2 else None
        try:
            validate(data)
            failures.append("MALFORMED INPUT ACCEPTED: {}".format(label))
        except ValueError as exc:
            if expect is not None and expect not in str(exc):
                failures.append("{}: refused with {!r}; expected the diagnostic to say {!r}".format(
                    label, str(exc), expect))
    # Positive cases: a good internal link and a good external https link must both validate.
    good_hrefs = [
        ("good internal link", "/evidence#release"),
        ("good external https link", "https://github.com/jposluns/guardrails/blob/main/CHANGELOG.md"),
    ]
    for label, href in good_hrefs:
        variant = {**good, "row": [{**good["row"][0], "evidence": [{"text": "e", "href": href}]}]}
        try:
            validate(variant)
        except ValueError as exc:
            failures.append("well-formed {} REFUSED: {}".format(label, exc))
    try:
        title, note, site_base, rows = validate(good)
        render_site(rows)
        render_md(title, note, site_base, rows)
        want = "C: the tooling under tools/; and the JavaScript under site/."
        if rows[0]["claim"] != want or MARKER in rows[0]["claim"]:
            failures.append("marker substitution rendered {!r}, expected {!r}".format(
                rows[0]["claim"], want))
    except ValueError as exc:
        failures.append("well-formed fixture REFUSED: {}".format(exc))
    # The limitation substitution is behaviour of its own: a limitation carrying the marker must
    # render the same shipped-code list the claim does (deleting the limitation replace line is a
    # mutant this case turns red).
    try:
        lim_fixture = {**good, "row": [{**good["row"][0], "limitation": "L: " + MARKER + "."}]}
        _, _, _, lim_rows = validate(lim_fixture)
        want_lim = "L: the tooling under tools/; and the JavaScript under site/."
        if lim_rows[0]["limitation"] != want_lim or MARKER in lim_rows[0]["limitation"]:
            failures.append("limitation marker substitution rendered {!r}, expected {!r}".format(
                lim_rows[0]["limitation"], want_lim))
    except ValueError as exc:
        failures.append("a limitation carrying the marker REFUSED: {}".format(exc))
    # site/ above the page symlinked outside the root fails closed (exit 2) in --check and write mode with the
    # page there untouched and never read: the page there has no markers, and each run prints the link
    # refusal, not the marker error. Symlinked inside the root, it is read and the page rewritten.
    import shutil
    import tempfile
    try:
        tmp = tempfile.mkdtemp(prefix="aiqt-gen-disclosure-selftest-")
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    try:
        failures.extend(linked_dir_failures(tmp, "site", _write_tree, run, "site", "site/disclosure.html",
                                            unread=lambda p: p.write_text("no markers\n", encoding="utf-8")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("FAIL: gen_disclosure self-test")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: gen_disclosure self-test ({} refusal classes, {} good hrefs, 2 happy paths, a site/ symlinked "
          "outside the root refused unread and one inside it followed)".format(len(bad), len(good_hrefs)))
    return 0


def run(root, check):
    """Reconcile DISCLOSURE.md and the DISCLOSURE block on site/disclosure.html under root. Exit 0 in sync, 1
    on drift (check mode), 2 on malformed input, a missing page or missing markers, or a site/ that cannot be
    read as a directory inside the repository. Parameterized on root so the self-test drives it against
    synthetic trees, never the real repo."""
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
    page = root / "site" / "disclosure.html"
    # The directory probe reconcile runs (target_dir_present), for the page before anything is written or read:
    # a site/ that is there but cannot be read as a directory inside the repository exits 2 here, so the page
    # is never read through it and DISCLOSURE.md is not rewritten either.
    target_dir_present(page, root)
    drift = False
    if reconcile(root / "DISCLOSURE.md", md, check, root):
        print("drift: DISCLOSURE.md")
        drift = True
    if not page.exists():
        print("error: site/disclosure.html not found (expected generated target)", file=sys.stderr)
        return 2
    try:
        new_html = replace_block(page.read_text(encoding="utf-8"), "DISCLOSURE", site_inner)
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    if reconcile(page, new_html, check, root):
        print("drift: site/disclosure.html")
        drift = True
    if check and drift:
        print("run tools/gen_disclosure.py to regenerate")
        return 1
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return _self_test()
    return run(repo_root(), "--check" in args)


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This entry point reads a
# child's exit status (the git queries of the special-file precheck in tools/_gen_common.py
# among them), so under an ignored SIGCHLD it would read a failing child as a pass; it refuses
# at its entry line instead, before main() runs.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("gen_disclosure.py") or main())
