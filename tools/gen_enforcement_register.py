#!/usr/bin/env python3
"""Generate the Guardrail Enforcement Register (GER-1): ENFORCEMENT.md and site/enforcement.html.

The register is the human-reviewable enforcement surface built ON the machine-readable enforceability
ledger (.aiqt/enforceability.json, authored by tools/gen_enforceability.py). For every one of the pack's
rules it shows the shipped mechanical control(s) linked to it, or an explicit status that enforcement is
not built yet (none), or that it is intended but not built (pending, with a one-line description of the
intended build). The register never RE-AUTHORS linkage: which gates and hooks cite a rule lives once, in
the ledger; the enforcement-roadmap TOML records only the human DECISION (status and pending intent) and
restates each enforced rule's linkage so the roadmap is reviewable standalone, and the generator
CROSS-VALIDATES that restatement against the ledger so the two can never fork.

The register reads every DISPLAY fact from the ledger and the roadmap. Each mechanism's display name is
its own namespaced ledger reference (gate:<id> / hook:<id>), rendered code-styled; there is no separately
authored mechanism label or summary. Class, default, platform, entry point, and the technical-limits
residual are read verbatim from the ledger, and the residual is quoted in full, not summarized.

DEFENSIBLE PROMISE (worded to claim exactly what holds, no more). The register contains no separately
authored mechanism summary, and the limitations it displays for each mechanism are the enforcement
ledger's own text, quoted verbatim. It does NOT claim "nothing is hand-authored": the rule titles, the
mechanism ids, the ledger residues and class letters, the roadmap decisions, and this generator's own
template prose (the explainer, the page copy, the class legend) remain authored upstream and are reviewed
as such.

HONEST BOUNDARY. An "enforced" status records LINKAGE, not complete coverage: at least one shipped gate or
hook cites the rule, and each mechanism carries its residual (what it does not catch) from the ledger. A
linked mechanism may cover only part of a rule's violation surface. "none" and "pending" both mean
enforcement is not built yet; "pending" adds the intended-build description.

  gen_enforcement_register.py           regenerate ENFORCEMENT.md and the whole site/enforcement.html page
  gen_enforcement_register.py --check   fail (exit 1) on output drift; exit 2 on a bad or contradictory input
  gen_enforcement_register.py --self-test  assert the generator's own fail-closed invariants on synthetic trees

Both views are WHOLE generated files. site/enforcement.html is composed from the shared themed site shell
(docs/_shell.html, the same chrome the other site pages use) with the register content substituted in, so
the ENTIRE page is generated and --check regenerates and compares the whole file: a hand edit anywhere on
the page, chrome included, fails --check. The register itself never re-authors any chrome.

Each mechanism's technical-limits residual is rendered VERBATIM on both views: in a fenced code block on
the Markdown view (the fence is computed longer than any backtick run in the residue, so no residue token
can break out) and inside a `<blockquote class="ledger-residual" data-mech="...">` on the site page (the
residue is HTML-escaped so a residue token shaped like a tag renders as text, not markup). After composing
the page the generator runs a generation-time VERBATIM check, per mechanism, over the generated source: it
requires the exact escaped ledger-residual block to be a byte substring of the composed HTML and the raw
residue to be the content of that mechanism's own Markdown fence, so a residue swallowed, altered, or
misplaced fails the generator's own run and --check, deterministically (fail-closed exit 2). The whole-page
byte-identity drift gate then binds the committed file to that checked source; there is no rendered-vs-static
assertion to attack, and the overclaim gate scans this page plainly (the residues are kept clean at source).

Exit convention: 0 clean (check-clean, or regeneration completed); 1 EXCLUSIVELY generated-output byte
drift; 2 for everything else: a missing, unreadable, non-UTF-8, or malformed input; a stale or
contradictory input (the committed ledger not matching a fresh regeneration; a roadmap status or mechanism
set contradicting the ledger's linkage); a malformed or incomplete site shell; a display-fidelity failure;
or a write error. A TOML/ledger contradiction is an input contradiction (exit 2), never ordinary
generated-output drift.
"""
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml, reconcile  # noqa: E402
from _standards import dir_present  # noqa: E402
from gen_rules import load_corpus  # noqa: E402
from gen_agents import sort_key  # noqa: E402  canonical AIQT-priority rule order (as gen_mappings uses)
import gen_enforceability  # noqa: E402  reuse build_ledger so the register can never accept a stale ledger
import gen_site  # noqa: E402  reuse the shared themed site shell and its validated token substitution

MD_REL = "ENFORCEMENT.md"
HTML_REL = "site/enforcement.html"
LEDGER_REL = ".aiqt/enforceability.json"
ROADMAP_REL = ".aiqt/core/enforcement-roadmap.toml"
RULES_DIR_REL = ".aiqt/core/rules"
SHELL_REL = gen_site.SHELL_REL  # docs/_shell.html: the shared themed site chrome

# The head/nav metadata this page substitutes into the shared shell, one value per shell field token.
# These are the enforcement page's own frontmatter, kept here (not in a TOML) because this page's body
# is generated from the ledger, not authored as Markdown. Each value is escaped for its shell sink by
# gen_site._escape_field, exactly as the Markdown site pages are.
PAGE_DESCRIPTION = ("Every rule and the shipped mechanical controls linked to it. An enforced status "
                    "records linkage, not complete coverage; none and pending both mean enforcement is "
                    "not built yet.")
PAGE_URL = "https://aiqt.ai/enforcement"
PAGE_FIELDS = {
    "title": "Guardrail Enforcement Register",
    "description": PAGE_DESCRIPTION,
    "canonical": PAGE_URL,
    "og-title": "Guardrail Enforcement Register",
    "og-description": PAGE_DESCRIPTION,
    "og-url": PAGE_URL,
}

# The generated-note comment carried at the top of the page body, so a reader who opens the source sees
# it is generated. It is an HTML comment (no visible text), so it never reaches the page's rendered copy.
PAGE_GENERATED_COMMENT = (
    "<!-- Generated by tools/gen_enforcement_register.py from the enforceability ledger, the enforcement "
    "roadmap, and the rule corpus. Do not edit by hand; change the source and regenerate. -->")

STATUSES = {"enforced", "none", "pending"}
# A mechanism reference is a closed namespaced form. Only gate: and hook: exist: the linkage ledger
# exposes gates[] and hooks[] and NO lint axis, so a well-formed lint: reference is deliberately rejected
# as cannot-evaluate (exit 2) rather than inferred from a gate's script name.
MECH_RE = re.compile(r"^(gate|hook):[a-z][a-z0-9-]*$")
CID_RE = re.compile(r"^[a-z0-9]{6,}$")
EN, EM = "–", "—"

# The lead-in that labels every mechanism's verbatim technical-limits disclosure on both views.
RESIDUAL_HEADING = "Technical limits (from the enforcement ledger)"

# The class-letter legend, single-sourced here so a class letter is never the only boundary signal on the
# page (codex's requirement). The legend renders only for the class letters actually present among the
# enforced mechanisms. The text is the enforceability grading rubric (gen_enforceability) in plain terms.
CLASS_LEGEND = (
    ("a", "deterministic gate: a machine predicate over committed artefacts, total for what it examines"),
    ("b", "runtime hook: a best-effort interception judging an action at the moment it runs"),
    ("c", "partial: a control that covers a recognizable subset of the surface"),
)

# The public explanatory paragraph carried inside BOTH generated views, so it cannot drift from the status
# semantics. One string, rendered as Markdown and as HTML text.
EXPLAINER = (
    "This register lists every rule and the shipped mechanical controls linked to it. An enforced status "
    "records linkage, not complete coverage: at least one shipped gate or hook cites the rule, and each "
    "mechanism's class and residual describe the boundary of what it checks. A linked mechanism may cover "
    "only part of a rule's violation surface. A status of none means enforcement has not been built yet; "
    "pending also means enforcement has not been built yet, and its description states the intended build. "
    "The technical limits shown for each mechanism are the enforcement ledger's own text, quoted verbatim "
    "and not summarized. The class letter is a maintainer assessment of the check's decision procedure, "
    "not a coverage score.")

GENERATED_NOTE = (
    "This file is generated from the enforceability ledger, the enforcement roadmap, and the rule corpus "
    "by tools/gen_enforcement_register.py. Do not edit it by hand; change the source and regenerate.")

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata only.
# BOTH views are WHOLE generated files (kind file). site/enforcement.html is composed from the shared site
# shell docs/_shell.html plus the ledger-derived content, so the whole page is generated and drift-gated;
# its sources therefore include docs/_shell.html alongside the ledger, the enforcement roadmap, and the
# rule corpus. ENFORCEMENT.md is the Markdown view over the same non-shell sources.
GENSRC_OUTPUTS = (
    {"target": "ENFORCEMENT.md", "kind": "file",
     "sources": (".aiqt/enforceability.json", ".aiqt/core/enforcement-roadmap.toml",
                 ".aiqt/core/rules/"),
     "regenerate": "python3 tools/gen_enforcement_register.py"},
    {"target": "site/enforcement.html", "kind": "file",
     "sources": ("docs/_shell.html", ".aiqt/enforceability.json",
                 ".aiqt/core/enforcement-roadmap.toml", ".aiqt/core/rules/"),
     "regenerate": "python3 tools/gen_enforcement_register.py"},
)


# --- input loading and validation ---------------------------------------------------------------------

def _no_ctrl(value, where):
    """Reject an en dash, em dash, control character, or an embedded newline in a SINGLE-LINE display string
    that renders into a Markdown TABLE CELL (a rule title, a status word, a corpus id, a pending row's
    description). Fail-closed (ValueError -> exit 2), so a bad source surfaces here rather than as a red site
    or dash gate on the generated output. A tab or newline in a Markdown table cell would break the row, so
    this guard is deliberately strict; a mechanism RESIDUE renders into a multi-line sink instead and uses
    _residue_display, which permits newlines."""
    if EN in value or EM in value:
        raise ValueError("{}: contains an en/em dash: {!r}".format(where, value))
    for ch in value:
        if ch != " " and (ord(ch) < 0x20 or ord(ch) == 0x7f):
            raise ValueError("{}: contains a control character or newline: {!r}".format(where, value))
    return value


def _residue_display(value, where):
    """Validate a mechanism RESIDUE for its actual sinks, which are NOT a table cell: it renders into a
    fenced Markdown code block (render_md) and an HTML blockquote (render_html), both of which carry
    multi-line, multi-paragraph text safely. So a residue MAY contain newlines (the two enforced hook
    residues git-stash-ref and protected-line-guard are legitimately multi-paragraph). What is still
    rejected: an en/em dash (the house no-dash convention, also caught by the whole-corpus dash gate) and
    any control character OTHER than newline (a raw NUL, backspace, tab, or carriage return has no faithful
    rendering in either sink). Newline is the only control character permitted; each view renders it safely,
    the fenced block by lengthening its fence past any backtick run (_fence_for) and the blockquote by
    HTML-escaping the text under a white-space:pre-wrap style so the line breaks show. Fail-closed
    (ValueError -> exit 2) so a bad residue surfaces here, exactly as _no_ctrl does for a table cell."""
    if EN in value or EM in value:
        raise ValueError("{}: contains an en/em dash: {!r}".format(where, value))
    for ch in value:
        if ch != "\n" and (ord(ch) < 0x20 or ord(ch) == 0x7f):
            raise ValueError("{}: contains a control character other than newline: {!r}".format(where, value))
    return value


def _req_str(table, key, where, allow_empty=False):
    """Require the field be a string; unless allow_empty, require it be non-empty AFTER stripping
    whitespace, so a blank or whitespace-only value fails closed (a validator that accepted '   ' as a
    status or description would render an empty display cell). A non-string short-circuits before
    the strip, so a bool/int/None is rejected without a type error."""
    value = table.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError("{}: field '{}' must be a non-empty string".format(where, key))
    return value


def _req_version_one(data, name):
    """Require version be exactly the integer 1, fail-closed. A plain '!= 1' test is NOT fail-closed:
    Python treats True == 1, so a TOML boolean 'version = true' would slip through, and this also rejects
    a float 1.0 (isinstance(1.0, int) is False). A cannot-evaluate version is exit 2, never a clean pass."""
    version = data.get("version")
    if not (isinstance(version, int) and not isinstance(version, bool) and version == 1):
        raise ValueError("{}: version must be exactly the integer 1".format(name))


def load_ledger(root):
    """Regenerate the enforceability ledger in memory and require BYTE identity with the committed file,
    then return the parsed object. A stale or hand-edited committed ledger is an input contradiction
    (ValueError -> exit 2), so the register can never render over a ledger the manifests no longer produce.
    build_ledger raises ValueError/OSError on a malformed or unreadable corpus/manifest input."""
    import json
    fresh = gen_enforceability.build_ledger(root)
    committed_path = root / LEDGER_REL
    try:
        committed = committed_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError("cannot read the committed ledger {} ({})".format(LEDGER_REL, exc))
    if committed != fresh:
        raise ValueError("committed {} is stale: it does not match a fresh regeneration; run "
                         "tools/gen_enforceability.py".format(LEDGER_REL))
    return json.loads(fresh)


def ledger_index(ledger):
    """From the ledger build: cid -> entry, and ref -> control-display-info (deduped; a control cited by
    several rules is identical everywhere). Each rule's namespaced linkage is sorted(gate refs) then
    sorted(hook refs), which is total lexical order because 'gate:' < 'hook:'."""
    by_cid = {}
    controls = {}
    linkage = {}
    for entry in ledger["rules"]:
        cid = entry["corpus-id"]
        by_cid[cid] = entry
        refs = []
        for gate in entry["gates"]:
            ref = "gate:" + gate["id"]
            refs.append(ref)
            controls[ref] = {"kind": "gate", "id": gate["id"], "class": gate["class"],
                             "default": gate["default"], "platform": gate["platform"],
                             "entry": gate["script"], "residue": gate["residue"]}
        for hook in entry["hooks"]:
            ref = "hook:" + hook["id"]
            refs.append(ref)
            matcher = hook.get("matcher")
            entry_point = "{} on {}".format(hook["event"], matcher) if matcher else hook["event"]
            controls[ref] = {"kind": "hook", "id": hook["id"], "class": hook["class"],
                             "default": hook["default"], "platform": hook["platform"],
                             "entry": entry_point, "residue": hook["residue"]}
        linkage[cid] = sorted(refs)
    return by_cid, controls, linkage


def load_roadmap(path, ledger_cids, linkage):
    """Parse and fully validate the enforcement roadmap; return {cid: {status, mechanisms, description?}}.
    Cross-validated against the ledger: the corpus-id set must equal the ledger's, and every row's status
    and mechanisms must agree with the ledger's linkage. All failures fail closed (ValueError -> exit 2)."""
    data = load_toml(path)
    name = path.name
    _req_version_one(data, name)
    extra = set(data) - {"version", "rule"}
    if extra:
        raise ValueError("{}: unknown top-level key(s): {}".format(name, ", ".join(sorted(extra))))
    rules = data.get("rule")
    if not isinstance(rules, list) or not rules:
        raise ValueError("{}: at least one [[rule]] entry is required".format(name))
    out = {}
    prev = None
    for row in rules:
        if not isinstance(row, dict):
            raise ValueError("{}: every [[rule]] must be a table".format(name))
        cid = _req_str(row, "corpus-id", "{}: [[rule]]".format(name))
        where = "{}: [[rule]] {}".format(name, cid)
        if not CID_RE.match(cid):
            raise ValueError("{}: corpus-id must match ^[a-z0-9]{{6,}}$".format(where))
        if cid in out:
            raise ValueError("{}: duplicate corpus-id".format(where))
        if prev is not None and cid < prev:
            raise ValueError("{}: rows must be in ascending corpus-id order (after {})".format(where, prev))
        prev = cid
        allowed = {"corpus-id", "status", "mechanisms", "description"}
        unknown = set(row) - allowed
        if unknown:
            raise ValueError("{}: unknown key(s): {}".format(where, ", ".join(sorted(unknown))))
        status = _req_str(row, "status", where)
        if status not in STATUSES:
            raise ValueError("{}: status must be one of {}".format(where, "/".join(sorted(STATUSES))))
        mechanisms = row.get("mechanisms")
        if not isinstance(mechanisms, list) or not all(isinstance(m, str) for m in mechanisms):
            raise ValueError("{}: mechanisms must be a list of strings (it may be empty)".format(where))
        for mech in mechanisms:
            if not MECH_RE.match(mech):
                raise ValueError("{}: mechanism {!r} is not a supported gate:/hook: reference (a lint: or "
                                 "other axis is unsupported: the ledger exposes no lint axis)".format(
                                     where, mech))
        if mechanisms != sorted(set(mechanisms)):
            raise ValueError("{}: mechanisms must be unique and sorted".format(where))
        has_desc = "description" in row
        if has_desc:
            desc = _req_str(row, "description", where)
            _no_ctrl(desc, "{}: description".format(where))
        if cid not in ledger_cids:
            raise ValueError("{}: corpus-id is not in the ledger (an orphan roadmap row)".format(where))
        ledger_refs = linkage[cid]
        # Status invariants, cross-checked against the ledger's own linkage.
        if ledger_refs:  # the ledger links >=1 control: the rule is enforced
            if status != "enforced":
                raise ValueError("{}: the ledger links {} control(s) to this rule, so status must be "
                                 "'enforced', not {!r}".format(where, len(ledger_refs), status))
            if mechanisms != ledger_refs:
                raise ValueError("{}: enforced mechanisms {} do not match the ledger linkage {}".format(
                    where, mechanisms, ledger_refs))
            if has_desc:
                raise ValueError("{}: an enforced row must not carry a description".format(where))
        else:  # prose-only in the ledger: not built, so none or pending only
            if status == "enforced":
                raise ValueError("{}: the ledger links no control to this rule, so it cannot be "
                                 "'enforced'".format(where))
            if mechanisms:
                raise ValueError("{}: a {} row must have an empty mechanisms list".format(where, status))
            if status == "pending" and not has_desc:
                raise ValueError("{}: a pending row requires a non-empty description of the intended "
                                 "build".format(where))
            if status == "none" and has_desc:
                raise ValueError("{}: a none row must not carry a description".format(where))
        out[cid] = {"status": status, "mechanisms": mechanisms,
                    "description": row.get("description")}
    roadmap_cids = set(out)
    missing = ledger_cids - roadmap_cids
    if missing:
        raise ValueError("{}: missing roadmap row(s) for ledger corpus-id(s): {}".format(
            name, ", ".join(sorted(missing))))
    # extra rows already rejected above (each cid must be in the ledger)
    return out


# --- rendering ----------------------------------------------------------------------------------------

def _fence_for(residue):
    """The backtick fence for a residue's Markdown code block: at least three backticks, and always one
    longer than the longest backtick run inside the residue, the standard collision-proof form, so no
    residue token can close the fence early. A newline is legitimate inside the block (a multi-line
    residue); any other control character is rejected upstream by _residue_display before it reaches here."""
    longest = max((len(run) for run in re.findall(r"`+", residue)), default=0)
    return "`" * max(3, longest + 1)


def _md_cell(value):
    """A Markdown table cell: escape a pipe and a backslash so the cell cannot break the row. Newlines and
    dashes are already rejected upstream by _no_ctrl on every display string."""
    return value.replace("\\", "\\\\").replace("|", "\\|")


def _rows_in_order(corpus, roadmap):
    """The rules in canonical AIQT-priority order (gen_agents.sort_key), corpus-id as the tie-breaker, so
    the order is deterministic and human-facing. Returns [(cid, title, fm)]."""
    ordered = sorted(corpus, key=lambda item: (sort_key(item[1]), str(item[1]["corpus-id"])))
    out = []
    for src, fm, _rel in ordered:
        cid = str(fm["corpus-id"])
        out.append((cid, rule_title(src), fm))
    return out


def rule_title(path):
    """The rule's display title: the first body '# ' heading (the same convention gen_mappings uses; there
    is deliberately no frontmatter title key). Fail closed if none."""
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


def _status_word(status):
    return {"enforced": "Enforced", "pending": "Pending", "none": "None"}[status]


def _classes_present(enforced_union, controls):
    return {controls[ref]["class"] for ref in enforced_union}


def _enforced_cell_md(mechanisms, controls):
    parts = []
    for ref in mechanisms:
        parts.append("`{}`, class {}".format(ref, controls[ref]["class"]))
    return "; ".join(parts)


# The corpus/ledger-derived VERBATIM content fields a mechanism contributes to the page, as
# (display label, control key) pairs in render order. SINGLE-SOURCED here so the render functions
# (render_md / render_html) and the authoritative content enumerator (page_content_strings, which the
# overclaim gate consumes) iterate the SAME field set and cannot fork on which fields ship: a field added
# to the page is added here once and flows to both render and the source-side overclaim scan. The "entry"
# field is the mechanism's entry point, which for a hook is "<event> on <matcher>", so the hook matcher is
# a verbatim page-bound string carried through this field BY CONSTRUCTION (the round-10 escape channel).
_MECH_FIELDS = (
    ("Platform", "platform"),
    ("Default", "default"),
    ("Entry point", "entry"),
    ("Class", "class"),
)


def render_md(rows, roadmap, controls, enforced_union):
    counts = {"enforced": 0, "pending": 0, "none": 0}
    for cid, _title, _fm in rows:
        counts[roadmap[cid]["status"]] += 1
    lines = ["# Guardrail Enforcement Register", "", GENERATED_NOTE, "", EXPLAINER, "",
             "## Summary", "", "| Status | Rules |", "|---|---:|",
             "| Enforced | {} |".format(counts["enforced"]),
             "| Pending | {} |".format(counts["pending"]),
             "| None | {} |".format(counts["none"]), "",
             "## Rules", "",
             "| Rule | Corpus ID | Status | How enforced or intended |",
             "|---|---|---|---|"]
    for cid, title, _fm in rows:
        row = roadmap[cid]
        status = row["status"]
        if status == "enforced":
            how = _enforced_cell_md(row["mechanisms"], controls)
        elif status == "pending":
            how = row["description"]
        else:
            how = "Enforcement has not been built yet."
        lines.append("| {} | `{}` | {} | {} |".format(
            _md_cell(_no_ctrl(title, "rule title {}".format(cid))), cid, _status_word(status),
            _md_cell(how)))
    lines += ["", "## Mechanisms", ""]
    classes_present = _classes_present(enforced_union, controls)
    lines += ["Class letters:", ""]
    for letter, desc in CLASS_LEGEND:
        if letter in classes_present:
            lines.append("- `{}`: {}".format(letter, desc))
    lines.append("")
    for ref in sorted(enforced_union):
        ctrl = controls[ref]
        residue = _residue_display(ctrl["residue"], "residue {}".format(ref))
        fence = _fence_for(residue)
        lines += ["### `{}`".format(ref), ""]
        for label, key in _MECH_FIELDS:
            lines.append("- {}: `{}`".format(label, ctrl[key]))
        lines += ["", "{}:".format(RESIDUAL_HEADING), "",
                  fence, residue, fence, ""]
    return "\n".join(lines).rstrip() + "\n"


def _t(value):   # HTML element text
    return html.escape(value, quote=False)


def _a(value):   # HTML attribute value
    return html.escape(value, quote=True)


def render_html(rows, roadmap, controls, enforced_union):
    counts = {"enforced": 0, "pending": 0, "none": 0}
    for cid, _title, _fm in rows:
        counts[roadmap[cid]["status"]] += 1
    out = []
    out.append('        <p>{}</p>'.format(_t(EXPLAINER)))
    out.append('        <p class="lead">Summary: '
               '<strong>{e}</strong> enforced, <strong>{p}</strong> pending, '
               '<strong>{n}</strong> none.</p>'.format(
                   e=counts["enforced"], p=counts["pending"], n=counts["none"]))
    out.append('        <div class="tablewrap">')
    out.append('          <table class="dtable">')
    out.append('            <thead><tr><th>Rule</th><th>Corpus ID</th><th>Status</th>'
               '<th>How enforced or intended</th></tr></thead>')
    out.append('            <tbody>')
    for cid, title, _fm in rows:
        row = roadmap[cid]
        status = row["status"]
        if status == "enforced":
            cells = []
            for ref in row["mechanisms"]:
                ctrl = controls[ref]
                cells.append('<a href="#mechanism-{anchor}"><code>{ref}</code></a>, class {cls}'
                             .format(anchor=_a("{}-{}".format(ctrl["kind"], ctrl["id"])),
                                     ref=_t(ref), cls=_t(ctrl["class"])))
            how = "; ".join(cells)
        elif status == "pending":
            how = _t(row["description"])
        else:
            how = "Enforcement has not been built yet."
        out.append('            <tr id="rule-{cid}"><td>{title}</td><td><code>{cid}</code></td>'
                   '<td>{status}</td><td>{how}</td></tr>'.format(
                       cid=_a(cid), title=_t(title), status=_t(_status_word(status)), how=how))
    out.append('            </tbody>')
    out.append('          </table>')
    out.append('        </div>')
    out.append('        <h2>Mechanisms</h2>')
    classes_present = _classes_present(enforced_union, controls)
    out.append('        <ul class="class-legend">')
    for letter, desc in CLASS_LEGEND:
        if letter in classes_present:
            out.append('          <li>Class <code>{}</code>: {}</li>'.format(_t(letter), _t(desc)))
    out.append('        </ul>')
    for ref in sorted(enforced_union):
        ctrl = controls[ref]
        residue = _residue_display(ctrl["residue"], "residue {}".format(ref))
        anchor = "mechanism-{}-{}".format(ctrl["kind"], ctrl["id"])
        out.append('        <details class="more" id="{anchor}">'.format(anchor=_a(anchor)))
        out.append('          <summary><code>{ref}</code></summary>'.format(ref=_t(ref)))
        out.append('          <div class="inner">')
        out.append('            <ul>')
        for label, key in _MECH_FIELDS:
            out.append('              <li>{}: <code>{}</code></li>'.format(label, _t(ctrl[key])))
        out.append('            </ul>')
        # The technical-limits residual is rendered VERBATIM from the ledger inside a labelled blockquote:
        # the residue is element text (HTML-escaped by _t), so a residue token shaped like a tag renders
        # as visible text, not markup. The data-mech attribute names the control this quotation declares.
        # The block carries no marketing exemption (round 7 removed the carve-out): the residue is kept
        # marketing-clean at its manifest source, so the overclaim gate scans this page plainly like any
        # other. The generation-time verbatim check below asserts this exact block ships byte-for-byte.
        # A multi-line residue keeps its newlines here verbatim; the .ledger-residual style is white-space:
        # pre-wrap (site/styles.css), so a multi-paragraph residue renders with its line breaks intact.
        out.append('            <p class="ledger-residual-label">{}</p>'.format(_t(RESIDUAL_HEADING)))
        out.append('            <blockquote class="ledger-residual" data-mech="{mech}">{text}</blockquote>'
                   .format(mech=_a(ref), text=_t(residue)))
        out.append('          </div>')
        out.append('        </details>')
    inner = "\n".join(out)
    # Reject an en/em dash anywhere in the emitted block (defence in depth over the per-field checks).
    if EN in inner or EM in inner:
        raise ValueError("emitted HTML block contains an en/em dash")
    return inner


# --- generation-time verbatim check -------------------------------------------------------------------
# Round 7 (GER-1) replaced the earlier "re-parse the emitted HTML and compare rendered visible text"
# fidelity assertion (which was static-DOM fidelity, not rendered-visible fidelity, and codex showed a
# CSS-generated ::after content could diverge from it) with a plain BYTE check over the generated source:
# each mechanism's residue is emitted by escaping the residue string into a fixed block shape, so asserting
# that exact block appears byte-for-byte in the composed HTML (and the residue verbatim in the mechanism's
# own Markdown fence) establishes the residue shipped intact. The whole-page byte-identity drift gate then
# binds the committed file to this checked generated source, so no separate rendered-vs-static assertion is
# needed. A missing or altered residue is a fail-closed ValueError (exit 2).


def _md_mechanism_fence(md_text, ref):
    """The residue text inside `ref`'s OWN Markdown fence: locate the mechanism's '### `ref`' heading, then
    the first fenced code block after it (a run of >=3 backticks alone on a line, closed by an identical
    run), and return the fenced content. This binds the residue to the mechanism's own fence, so a residue
    that appears ANYWHERE ELSE in the file cannot satisfy fidelity (F-330 fix 3). Returns None if the
    mechanism's heading or its fence is not found before the next mechanism heading."""
    heading = "### `{}`".format(ref)
    lines = md_text.split("\n")
    fence_line = re.compile(r"^`{3,}$")
    # Locate the mechanism heading only OUTSIDE fenced code blocks. A PRIOR mechanism's multi-line residue is
    # rendered verbatim inside its own fence and may contain a line that equals a later mechanism's
    # '### `ref`' heading; a fence-unaware lines.index() would select that in-fence occurrence and extract the
    # wrong (or no) content, falsely rejecting valid input (exit 2, over-rejection). Track fence open/close on
    # the same exact-marker basis the extraction below uses (a fence opens on a >=3-backtick line alone and
    # closes on an identical line, so a shorter backtick run inside a longer fence does not close it), and
    # skip lines inside a fence while scanning for the heading.
    start = None
    fence_marker = None
    for idx, line in enumerate(lines):
        if fence_marker is not None:
            if line == fence_marker:
                fence_marker = None
            continue
        if fence_line.match(line):
            fence_marker = line
            continue
        if line == heading:
            start = idx
            break
    if start is None:
        return None
    j = start + 1
    while j < len(lines) and not fence_line.match(lines[j]):
        if lines[j].startswith("### `"):  # reached the next mechanism without a fence for this one
            return None
        j += 1
    if j >= len(lines):
        return None
    fence = lines[j]
    content = []
    k = j + 1
    while k < len(lines) and lines[k] != fence:
        content.append(lines[k])
        k += 1
    if k >= len(lines):  # unterminated fence
        return None
    return "\n".join(content)


def _assert_display_fidelity(html_page, md_text, controls, enforced_union):
    """Fail-closed (ValueError -> exit 2) unless every enforced mechanism's residue is emitted VERBATIM in
    both views. This is a BYTE check over the generated source, not a re-parse of rendered HTML: the block
    is produced by escaping the residue into a fixed shape, so requiring that exact block to be a substring
    of the composed HTML establishes the residue shipped intact and unaltered; the Markdown side requires
    the residue to be the content of that mechanism's OWN fence (not merely present somewhere in the file).
    The whole-page byte-identity drift gate then binds the committed file to this checked source, so a
    rendering bug cannot ship a page whose bytes look complete but whose residue was swallowed or altered."""
    for ref in sorted(enforced_union):
        residue = controls[ref]["residue"]
        block = ('<blockquote class="ledger-residual" data-mech="{mech}">{text}</blockquote>'
                 .format(mech=_a(ref), text=_t(residue)))
        if block not in html_page:
            raise ValueError("display fidelity: the residue for {} is not emitted verbatim in its own "
                             "HTML ledger-residual block".format(ref))
        if _md_mechanism_fence(md_text, ref) != residue:
            raise ValueError("display fidelity: the residue for {} is not verbatim inside its own Markdown "
                             "fence".format(ref))


# --- driver -------------------------------------------------------------------------------------------

def _page_content(html_inner):
    """The page body substituted into the shell's `{{content}}`: a generated-note comment, the page head
    band, and the register section wrapping the rendered inner. The head band and section wrapper are the
    only page structure this generator authors; every other chrome byte comes from the shared shell."""
    return "\n".join([
        PAGE_GENERATED_COMMENT,
        "",
        '<div class="wrap pagehead">',
        '  <p class="eyebrow">Enforcement register</p>',
        '  <h1>Guardrail Enforcement Register</h1>',
        '  <p class="lead">Which rules have shipped mechanical enforcement, and which do not yet. This page is',
        '    generated from the enforceability ledger, so what it claims about linkage is always what the pack',
        '    actually ships.</p>',
        '</div>',
        "",
        '<section id="register">',
        '  <div class="wrap">',
        html_inner,
        "  </div>",
        "</section>",
    ])


def compose_page(root, html_inner):
    """Compose the WHOLE site/enforcement.html from the shared themed shell (docs/_shell.html) and the
    register content, so the entire page is generated and drift-gated. The shell is validated first (its
    field tokens present exactly once and each sitting in the HTML sink its escaping assumes), so a
    malformed or incomplete shell fails closed (SchemaError -> exit 2) rather than emitting a broken page.
    Substitution is a single pass over the shell (gen_site's substitution contract), so a substituted
    value is never re-scanned for another token. Reuses the shared shell and gen_site's own escaping and
    substitution rather than duplicating any chrome here."""
    shell = (root / SHELL_REL).read_text(encoding="utf-8")  # OSError/UnicodeError -> fail-closed in caller
    gen_site._validate_shell_placeholders(shell, SHELL_REL)
    gen_site._validate_token_sinks(shell, SHELL_REL)
    substitutions = {"content": _page_content(html_inner)}
    for field, (token, sink) in gen_site.FIELD_TO_TOKEN.items():
        substitutions[token[2:-2]] = gen_site._escape_field(PAGE_FIELDS[field], sink)

    def repl(match):
        name = match.group(1)
        if name in substitutions:
            return substitutions[name]
        raise ValueError("{}: shell carries an unexpected placeholder {}".format(SHELL_REL, match.group(0)))

    return gen_site._TOKEN_RE.sub(repl, shell)


def build_views(root):
    """Return (md_text, html_page). html_page is the WHOLE composed site/enforcement.html. Raises
    ValueError/OSError/gen_site.SchemaError on any malformed, unreadable, stale, or contradictory input
    (including a bad site shell or a display-fidelity failure); the caller maps those to exit 2."""
    rules_dir = root / RULES_DIR_REL
    if not dir_present(rules_dir):
        raise ValueError("cannot build the register: no {} to load".format(rules_dir))
    ledger = load_ledger(root)
    by_cid, controls, linkage = ledger_index(ledger)
    ledger_cids = set(by_cid)
    roadmap = load_roadmap(root / ROADMAP_REL, ledger_cids, linkage)
    enforced_union = set()
    for cid, row in roadmap.items():
        if row["status"] == "enforced":
            enforced_union.update(row["mechanisms"])
    # Defensive display-name uniqueness guard: the display name IS the ledger reference (identity, no
    # normalization), so a duplicate is structurally unreachable today. The check stays fail-closed so any
    # future normalizing transformation inherits a collision guard instead of a silent merge (ValueError
    # -> exit 2), the existing idiom.
    display_names = sorted(enforced_union)
    if len(set(display_names)) != len(display_names):
        raise ValueError("duplicate mechanism display name emitted")
    corpus = load_corpus(rules_dir)
    rows = _rows_in_order(corpus, roadmap)
    md_text = render_md(rows, roadmap, controls, enforced_union)
    html_inner = render_html(rows, roadmap, controls, enforced_union)
    html_page = compose_page(root, html_inner)
    _assert_display_fidelity(html_page, md_text, controls, enforced_union)
    return md_text, html_page


def page_content_strings(root):
    """Every corpus/ledger-derived VERBATIM content string the enforcement register page interpolates,
    recomputed IN MEMORY from the corpus, the manifests, and the roadmap (guard-input-soundness), as
    (channel, key, raw) tuples. This is the AUTHORITATIVE, single-sourced set the render functions draw
    from (via the shared _MECH_FIELDS) and the overclaim gate's source-side scan consumes, so the checked
    set cannot fork from what the page renders (FIX 1, GER-1 round 11: the round-8/round-10 class where a
    hand-maintained channel list in check_overclaim drifted from the generator, letting the rule TITLE and
    the hook MATCHER ship unscanned). One channel per verbatim interpolation the page emits:
      - "title"       each rule's display title (the corpus '# ' heading render_* puts in the Rule cell)
      - "corpus-id"   each rule's corpus id (the code-styled Corpus ID cell / anchor stem)
      - "mech-id"     each enforced mechanism's namespaced reference (gate:<id> / hook:<id>)
      - one channel per _MECH_FIELDS entry ("platform", "default", "entry", "class"): the metadata the
        mechanism block emits verbatim. "entry" is the entry point, which carries the hook matcher
        ("<event> on <matcher>") and the gate script path.
      - "residue"     each enforced mechanism's ledger residue (rendered verbatim in both views)
      - "description" each pending rule's roadmap description (the How cell for a pending row)
    Every value is the RAW string (NOT whitespace-collapsed) so a downstream invisible/non-ASCII reject
    judges the exact bytes the page ships. Scoped to what the page RENDERS: only mechanisms that appear in
    an enforced row (enforced_union) contribute mechanism channels, exactly as render_md / render_html
    iterate them. build_ledger / load_roadmap / load_corpus raise ValueError/OSError on a malformed or
    unreadable input, which the caller lets propagate to a fail-closed exit 2."""
    import json
    rules_dir = root / RULES_DIR_REL
    ledger = json.loads(gen_enforceability.build_ledger(root))
    by_cid, controls, linkage = ledger_index(ledger)
    roadmap = load_roadmap(root / ROADMAP_REL, set(by_cid), linkage)
    enforced_union = set()
    for cid, row in roadmap.items():
        if row["status"] == "enforced":
            enforced_union.update(row["mechanisms"])
    rows = _rows_in_order(load_corpus(rules_dir), roadmap)
    out = []
    for cid, title, _fm in rows:
        out.append(("title", cid, title))
        out.append(("corpus-id", cid, cid))
        row = roadmap[cid]
        if row["status"] == "pending" and row["description"]:
            out.append(("description", cid, row["description"]))
    for ref in sorted(enforced_union):
        ctrl = controls[ref]
        out.append(("mech-id", ref, ref))
        for _label, key in _MECH_FIELDS:
            out.append((key, ref, ctrl[key]))
        out.append(("residue", ref, ctrl["residue"]))
    return out


def run(root, check):
    try:
        md_text, html_page = build_views(root)
    except (ValueError, OSError, gen_site.SchemaError) as exc:
        print("error: cannot build the enforcement register ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2
    drift = False
    try:
        # reconcile fail-closes with SystemExit(2) on an unreadable or invalid-UTF-8 existing target or a
        # write error; map that to a returned exit 2 so the code is uniform with the input-error path above.
        if reconcile(root / MD_REL, md_text, check):
            print("drift: {} is out of date; run tools/gen_enforcement_register.py".format(MD_REL),
                  file=sys.stderr)
            drift = True
        if reconcile(root / HTML_REL, html_page, check):
            print("drift: {} is out of date; run tools/gen_enforcement_register.py".format(HTML_REL),
                  file=sys.stderr)
            drift = True
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    if drift:
        return 1
    if not check:
        print("wrote {} and {}".format(MD_REL, HTML_REL))
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run(repo_root(), "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Proves the generator's own fail-closed invariants against synthetic trees, so this generator never
# becomes an ungated one. A conformant tree carries a mini corpus, hooks and gates manifests, the two
# roster files, a freshly-built ledger, an enforcement roadmap covering every rule (enforced, none, and
# pending all present), and the two output shells. Each mutated tree asserts the expected exit code; every
# mutation runs against its OWN fresh copy. The synthetic residues carry a tag-shaped token, an ampersand,
# and a backtick run, so the escaping, the fence-lengthening, and the display-fidelity assertion are all
# exercised on a conformant generation.

_APEX = """---
corpus-id: apex01
origin: pack
family: aiqt
apex: true
slug: project-integrity
---
# Project integrity

The apex rule for the self-test corpus.
"""


def _rule_src(cid, facet, slug):
    return ("---\ncorpus-id: {}\norigin: pack\nfamily: aiqt\ntier: 10\nfacet: {}\nslug: {}\n---\n"
            "# Title of {}\n\nA rule for the enforcement-register self-test corpus.\n".format(
                cid, facet, slug, cid))


# The alpha gate residue carries a tag-shaped token (`--conflict[=<style>]`), an ampersand, and a single
# backtick, so a conformant render exercises HTML escaping and the display-fidelity assertion (the token
# must survive to the block's visible text, not swallow it). The hook residue is MULTI-PARAGRAPH (a blank
# line between two paragraphs) AND carries a THREE-backtick run: the multi-paragraph text exercises the
# newline-permitting _residue_display and the multi-line round-trip in both views (the single-line _no_ctrl
# guard would have wrongly rejected it, so a conformant generation fails without the fix), and the
# three-backtick run forces the Markdown fence to lengthen to four backticks to wrap it without breaking out.
_ALPHA_RESIDUE = ("A self-test drift gate carrying a `--conflict[=<style>]` token & an ampersand, so the "
                  "HTML escaping and the display-fidelity assertion are exercised.")
_HOOK_RESIDUE = ("A self-test hook on rule cc carrying a ```triple-backtick``` run, so the Markdown fence "
                 "must lengthen past three backticks to wrap it.\n\nA SECOND PARAGRAPH separated by a blank "
                 "line, so a multi-line multi-paragraph residue is exercised end to end: it must render and "
                 "round-trip verbatim in the fenced code block and the HTML blockquote.")

_HOOKS = """[plugin]
name = "aiqt-selftest-hooks"
version = "0.1.0"
description = "Self-test plugin."
author-name = "Self Test"
author-email = "selftest@example.invalid"
homepage = "https://example.invalid"

[[hook]]
id = "hook-one"
rules = ["rulecc"]
platform = "claude-code"
event = "PreToolUse"
matcher = "Bash"
handler = "h_one"
default = "block"
class = "b"
residue = '''{residue}'''
""".format(residue=_HOOK_RESIDUE)

_GATES = """[[gate]]
id = "gate-alpha"
script = "tools/g_alpha.py"
rules = ["ruleaa", "rulebb"]
platform = "ci"
default = "block"
class = "a"
residue = "{residue}"

[[gate]]
id = "gate-empty"
script = "tools/g_empty.py"
rules = []
platform = "ci"
default = "block"
class = "a"
residue = "A self-test explicit-empty gate."
""".format(residue=_ALPHA_RESIDUE)

_ROSTER_SH = """#!/usr/bin/env bash
# a self-test roster mirror
run_gate "alpha-selftest" python3 -I -B tools/g_alpha.py --self-test
run_gate "alpha" python3 -I -B tools/g_alpha.py --check
run_gate "empty" python3 -I -B tools/g_empty.py
"""

_ROSTER_YML = """name: Quality
jobs:
  quality:
    steps:
      # a self-test CI roster
      - run: python3 -I -B tools/g_alpha.py --self-test
      - run: python3 -I -B tools/g_alpha.py --check
      - run: python3 -I -B tools/g_empty.py
"""

# ruleaa + rulebb -> gate-alpha (enforced); rulecc -> hook-one (enforced); ruledd + apex01 -> prose-only.
# ruledd is left none, apex01 is set pending, so all three statuses appear.
_ROADMAP = """version = 1

[[rule]]
corpus-id = "apex01"
status = "pending"
mechanisms = []
description = "A self-test intended build for the apex rule."

[[rule]]
corpus-id = "ruleaa"
status = "enforced"
mechanisms = ["gate:gate-alpha"]

[[rule]]
corpus-id = "rulebb"
status = "enforced"
mechanisms = ["gate:gate-alpha"]

[[rule]]
corpus-id = "rulecc"
status = "enforced"
mechanisms = ["hook:hook-one"]

[[rule]]
corpus-id = "ruledd"
status = "none"
mechanisms = []
"""

# A minimal but VALID shared site shell (docs/_shell.html) for the synthetic tree: each field token
# present exactly once and in the HTML sink its escaping assumes (title in element text, the rest in
# quoted attributes), plus the body `{{content}}`, so gen_site's shell validators pass. The generator
# composes the whole site/enforcement.html from this, so a chrome edit to the generated page drifts.
_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{title}}</title>
<meta name="description" content="{{description}}">
<link rel="canonical" href="{{canonical}}">
<meta property="og:title" content="{{og_title}}">
<meta property="og:description" content="{{og_description}}">
<meta property="og:url" content="{{og_url}}">
</head>
<body>
<main id="main">

{{content}}

</main>
</body>
</html>
"""


def _build(base):
    """A conformant synthetic tree. Reuses gen_enforceability to write a matching ledger, so the register's
    own ledger byte-identity check has a clean baseline."""
    rules = base / ".aiqt" / "core" / "rules"
    rules.mkdir(parents=True)
    (rules / "00-project-integrity.md").write_text(_APEX, encoding="utf-8")
    (rules / "rule-aa.md").write_text(_rule_src("ruleaa", "TRUST", "selftest-rule-aa"), encoding="utf-8")
    (rules / "rule-bb.md").write_text(_rule_src("rulebb", "INTEG", "selftest-rule-bb"), encoding="utf-8")
    (rules / "rule-cc.md").write_text(_rule_src("rulecc", "QUALI", "selftest-rule-cc"), encoding="utf-8")
    (rules / "rule-dd.md").write_text(_rule_src("ruledd", "ACCUR", "selftest-rule-dd"), encoding="utf-8")
    hooks_dir = base / ".aiqt" / "core" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "manifest.toml").write_text(_HOOKS, encoding="utf-8")
    gates_dir = base / ".aiqt" / "core" / "gates"
    gates_dir.mkdir(parents=True)
    (gates_dir / "manifest.toml").write_text(_GATES, encoding="utf-8")
    tools = base / "tools"
    tools.mkdir(parents=True)
    (tools / "g_alpha.py").write_text("# self-test gate script\n", encoding="utf-8")
    (tools / "g_empty.py").write_text("# self-test gate script\n", encoding="utf-8")
    (tools / "run_all_checks.sh").write_text(_ROSTER_SH, encoding="utf-8")
    workflows = base / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "quality.yml").write_text(_ROSTER_YML, encoding="utf-8")
    # Write a matching ledger via the reused builder, so load_ledger's byte-identity check passes.
    (base / ".aiqt" / "enforceability.json").write_text(
        gen_enforceability.build_ledger(base), encoding="utf-8")
    (base / ".aiqt" / "core" / "enforcement-roadmap.toml").write_text(_ROADMAP, encoding="utf-8")
    docs = base / "docs"
    docs.mkdir(parents=True)
    (docs / "_shell.html").write_text(_SHELL, encoding="utf-8")
    # site/ must exist for the generator to write the whole site/enforcement.html into it.
    (base / "site").mkdir(parents=True)
    return base


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def run_quiet(root, check):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    def replace_in(path, old, new):
        text = path.read_text(encoding="utf-8")
        if old not in text:
            failures.append("self-test setup: {!r} not found in {}".format(old, path.name))
        path.write_text(text.replace(old, new), encoding="utf-8")

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-enforcement-register-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    ROADMAP = ".aiqt/core/enforcement-roadmap.toml"
    GATES = ".aiqt/core/gates/manifest.toml"
    try:
        # (a) Conformant tree: generate (exit 0), then re-check drift-clean (exit 0).
        good = _build(tmp / "good")
        if run_quiet(good, check=False) != 0:
            failures.append("conformant tree: generation expected exit 0")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant tree: regeneration expected drift-clean exit 0")
        md = good / MD_REL
        if not md.is_file():
            failures.append("conformant tree: expected {} to be written".format(MD_REL))
        else:
            body = md.read_text(encoding="utf-8")
            for token in ("Enforced | 3", "Pending | 1", "None | 1", "`gate:gate-alpha`",
                          "`hook:hook-one`", "Enforcement has not been built yet.",
                          "### `gate:gate-alpha`", RESIDUAL_HEADING,
                          "Class letters:", _ALPHA_RESIDUE, _HOOK_RESIDUE):
                if token not in body:
                    failures.append("conformant tree: {!r} missing from ENFORCEMENT.md".format(token))
            # The three-backtick hook residue must be wrapped in a FOUR-backtick fence (lengthened past
            # the run) so it cannot break out of its code block.
            if "````" not in body:
                failures.append("conformant tree: expected a lengthened four-backtick fence in the Markdown")
            # MULTI-LINE RESIDUE: the hook residue is multi-paragraph, so it carries a blank line, and it
            # must survive verbatim (blank line and all) inside its OWN Markdown fence. This is the
            # fail-without-the-fix assertion: the old single-line _no_ctrl guard rejected any newline, so a
            # conformant generation would have failed exit 2 rather than producing this round-tripped body.
            if "\n" not in _HOOK_RESIDUE:
                failures.append("self-test setup: the hook residue must be multi-line to exercise the fix")
            if _md_mechanism_fence(body, "hook:hook-one") != _HOOK_RESIDUE:
                failures.append("conformant tree: the multi-line hook residue did not round-trip verbatim "
                                "inside its own Markdown fence")
        html_page = (good / HTML_REL).read_text(encoding="utf-8")
        # The multi-line residue is also emitted verbatim (newlines intact, HTML-escaped) in its blockquote.
        if _HOOK_RESIDUE not in html_page:
            failures.append("conformant tree: the multi-line hook residue is not emitted verbatim in the "
                            "HTML page")
        if "id=\"rule-apex01\"" not in html_page or "id=\"mechanism-gate-gate-alpha\"" not in html_page:
            failures.append("conformant tree: expected stable rule/mechanism anchors in the HTML page")
        # The verbatim residual disclosure is present on the page: the labelled blockquote carries the
        # data-mech for each mechanism, and the tag-shaped residue token survives as visible text (escaped),
        # not as markup, so the display-fidelity assertion passed on a conformant generation.
        for token in ('<blockquote class="ledger-residual" data-mech="gate:gate-alpha">',
                      '<blockquote class="ledger-residual" data-mech="hook:hook-one">',
                      "--conflict[=&lt;style&gt;]", RESIDUAL_HEADING):
            if token not in html_page:
                failures.append("conformant tree: {!r} missing from the generated page".format(token))
        # The whole page is composed from the shared shell, so the chrome (the substituted <title>) and
        # the page head band are present, not just the register block.
        for chrome_token in ("<title>Guardrail Enforcement Register</title>",
                             "<p class=\"eyebrow\">Enforcement register</p>"):
            if chrome_token not in html_page:
                failures.append("conformant tree: {!r} missing from the generated page".format(chrome_token))

        # (a2) DISPLAY-FIDELITY fails closed: feed the assertion a doctored page whose one residual block is
        # truncated, and assert it raises ValueError (the rendering-destroying-bug guard). Rebuild the
        # controls/enforced set the same way build_views does.
        ledger = load_ledger(good)
        _by_cid, controls, linkage = ledger_index(ledger)
        roadmap = load_roadmap(good / ROADMAP_REL, set(_by_cid), linkage)
        enforced_union = set()
        for cid, r in roadmap.items():
            if r["status"] == "enforced":
                enforced_union.update(r["mechanisms"])
        md_text = md.read_text(encoding="utf-8")
        # Truncate the alpha residue inside its block on the page (drop a distinctive tail token).
        doctored = html_page.replace("an ampersand, so the HTML escaping and the display-fidelity "
                                     "assertion are exercised.", "an ampersand.", 1)
        if doctored == html_page:
            failures.append("fidelity test setup: expected a residue tail to truncate")
        try:
            _assert_display_fidelity(doctored, md_text, controls, enforced_union)
            failures.append("display fidelity: a truncated residual block must fail closed (ValueError)")
        except ValueError:
            pass
        # And a doctored MARKDOWN (residue absent from the fence) must also fail closed.
        try:
            _assert_display_fidelity(html_page, md_text.replace(_ALPHA_RESIDUE, "gone", 1),
                                     controls, enforced_union)
            failures.append("display fidelity: a residue absent from the Markdown must fail closed")
        except ValueError:
            pass
        # (a3) BYTE-EXACT block: content APPENDED inside a residual block (byte-complete residue plus
        # trailing marketing) must fail, because the exact escaped block is then no longer a substring.
        appended = html_page.replace("assertion are exercised.",
                                     "assertion are exercised. Added marketing.", 1)
        if appended == html_page:
            failures.append("fidelity test setup: expected the alpha residue tail to append")
        try:
            _assert_display_fidelity(appended, md_text, controls, enforced_union)
            failures.append("display fidelity: appended content in a block must fail exact-equality (fix 3)")
        except ValueError:
            pass
        # (a4) FENCE-SCOPING: a residue removed from its OWN Markdown fence but present elsewhere in the
        # file must still fail, because the check binds the residue to that mechanism's own fence.
        md_moved = md_text.replace(_ALPHA_RESIDUE, "gone", 1) + "\n\nprose: " + _ALPHA_RESIDUE + "\n"
        try:
            _assert_display_fidelity(html_page, md_moved, controls, enforced_union)
            failures.append("display fidelity: a residue outside its own fence must fail (fix 3)")
        except ValueError:
            pass

        # (a4b) FENCE-UNAWARE HEADING (fenced-heading-not-confused): a PRIOR mechanism's multi-line residue,
        # rendered verbatim inside its own fence, may contain a line that equals a LATER mechanism's
        # '### `ref`' heading. The heading lookup must skip fenced blocks; a fence-unaware lines.index() would
        # select the in-fence occurrence and extract the wrong content (here it runs off the end and returns
        # None), falsely rejecting a valid page (exit 2, over-rejection). MUTATION: reverting the lookup to
        # lines.index(heading) makes this return None instead of beta's real residue -> this fails.
        fenced_md = "\n".join([
            "### `gate:alpha`",
            "````",
            "Alpha residue line one.",
            "### `gate:beta`",           # a heading-like line that is part of ALPHA's fenced residue
            "Alpha residue line two.",
            "````",
            "",
            "### `gate:beta`",           # beta's REAL heading, outside any fence
            "```",
            "Beta real residue.",
            "```",
        ])
        if _md_mechanism_fence(fenced_md, "gate:beta") != "Beta real residue.":
            failures.append("fence-unaware heading: the heading lookup must skip fenced blocks so a prior "
                            "residue containing a heading-like line does not misdirect extraction (fix 3)")

        # (b) A drifted ENFORCEMENT.md fails --check (exit 1).
        if md.is_file():
            md.write_text(md.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            if run_quiet(good, check=True) != 1:
                failures.append("drifted {} expected exit 1".format(MD_REL))

        # (b2) A hand edit to the generated page CHROME (outside the register content) fails --check,
        # proving the WHOLE page is generated and drift-gated, not merely a block within it.
        chrome = _build(tmp / "chrome-edit")
        if run_quiet(chrome, check=False) != 0:
            failures.append("chrome-edit tree: generation expected exit 0")
        page_path = chrome / HTML_REL
        if not page_path.is_file():
            failures.append("chrome-edit tree: expected {} to be generated".format(HTML_REL))
        else:
            page_text = page_path.read_text(encoding="utf-8")
            edited = page_text.replace(
                "<title>Guardrail Enforcement Register</title>",
                "<title>Hand edited chrome</title>", 1)
            if edited == page_text:
                failures.append("chrome-edit tree: expected a <title> chrome line to edit")
            else:
                page_path.write_text(edited, encoding="utf-8")
                if run_quiet(chrome, check=True) != 1:
                    failures.append("a hand edit to the generated page chrome must fail --check (exit 1)")

        def expect2(name, cid_setup):
            tree = _build(tmp / name)
            cid_setup(tree)
            if run_quiet(tree, check=True) != 2:
                failures.append("{}: expected exit 2 (fail-closed)".format(name))

        # (c) Unknown top-level key in the roadmap.
        expect2("roadmap-top-key",
                lambda t: (t / ROADMAP).write_text("bogus = 1\n" + (t / ROADMAP).read_text("utf-8"),
                                                   encoding="utf-8"))
        # (d) Unknown row key in the roadmap.
        expect2("roadmap-row-key",
                lambda t: replace_in(t / ROADMAP, 'status = "none"',
                                     'status = "none"\nbogus = 1'))
        # (e) Missing roadmap row (drop ruledd).
        expect2("roadmap-missing-row",
                lambda t: (t / ROADMAP).write_text(
                    (t / ROADMAP).read_text("utf-8").replace(
                        '\n[[rule]]\ncorpus-id = "ruledd"\nstatus = "none"\nmechanisms = []\n', "\n"),
                    encoding="utf-8"))
        # (f) Extra roadmap row (a corpus-id not in the ledger).
        expect2("roadmap-extra-row",
                lambda t: (t / ROADMAP).write_text(
                    (t / ROADMAP).read_text("utf-8") +
                    '\n[[rule]]\ncorpus-id = "zzghost"\nstatus = "none"\nmechanisms = []\n',
                    encoding="utf-8"))
        # (g) An enforced row whose mechanisms disagree with the ledger linkage.
        expect2("roadmap-wrong-mech",
                lambda t: replace_in(t / ROADMAP, 'mechanisms = ["hook:hook-one"]',
                                     'mechanisms = ["gate:gate-alpha"]'))
        # (h) An enforced status on a prose-only baseline (ruledd).
        expect2("roadmap-enforced-prose",
                lambda t: replace_in(t / ROADMAP,
                                     'corpus-id = "ruledd"\nstatus = "none"\nmechanisms = []',
                                     'corpus-id = "ruledd"\nstatus = "enforced"\n'
                                     'mechanisms = ["gate:gate-alpha"]'))
        # (i) A none status on a linked baseline (ruleaa).
        expect2("roadmap-none-linked",
                lambda t: replace_in(t / ROADMAP,
                                     'corpus-id = "ruleaa"\nstatus = "enforced"\n'
                                     'mechanisms = ["gate:gate-alpha"]',
                                     'corpus-id = "ruleaa"\nstatus = "none"\nmechanisms = []'))
        # (j) A none row carrying a description.
        expect2("roadmap-none-desc",
                lambda t: replace_in(t / ROADMAP,
                                     'corpus-id = "ruledd"\nstatus = "none"\nmechanisms = []',
                                     'corpus-id = "ruledd"\nstatus = "none"\nmechanisms = []\n'
                                     'description = "should not be here"'))
        # (k) A pending row missing its description.
        expect2("roadmap-pending-nodesc",
                lambda t: replace_in(t / ROADMAP,
                                     'status = "pending"\nmechanisms = []\n'
                                     'description = "A self-test intended build for the apex rule."',
                                     'status = "pending"\nmechanisms = []'))
        # (l) A pending row carrying a mechanism.
        expect2("roadmap-pending-mech",
                lambda t: replace_in(t / ROADMAP,
                                     'status = "pending"\nmechanisms = []\n'
                                     'description = "A self-test intended build for the apex rule."',
                                     'status = "pending"\nmechanisms = ["gate:gate-alpha"]\n'
                                     'description = "A self-test intended build for the apex rule."'))
        # (m) An unsupported lint: reference.
        expect2("roadmap-lint-ref",
                lambda t: replace_in(t / ROADMAP, 'mechanisms = ["hook:hook-one"]',
                                     'mechanisms = ["lint:hook-one"]'))
        # (q) A stale committed ledger (mutate it so it no longer matches a fresh build).
        expect2("stale-ledger",
                lambda t: (t / ".aiqt" / "enforceability.json").write_text(
                    (t / ".aiqt" / "enforceability.json").read_text("utf-8") + "\n", encoding="utf-8"))
        # (r) An en dash in a ledger residue fails closed via _residue_display (residues permit newlines
        # but never a dash: the house no-dash convention still holds for the multi-line sink).
        expect2("residue-dash",
                lambda t: replace_in(t / GATES, "an ampersand,", "an en – dash,"))
        # (r2) A NEWLINE in a genuine TABLE-CELL field (a pending row's description) is STILL rejected by
        # _no_ctrl: the residue relaxation permits newlines only in the multi-line residue sink, never in a
        # single-line Markdown table cell (a title, status, corpus id, or description), where a newline
        # would break the row. This is the companion to the multi-line-residue round-trip above.
        expect2("desc-newline",
                lambda t: replace_in(
                    t / ROADMAP,
                    'description = "A self-test intended build for the apex rule."',
                    'description = "First line.\\nSecond line."'))
        # (r3) A raw CONTROL character (a tab) in a residue is still rejected by _residue_display: only a
        # newline is a permitted control character in the multi-line sink; a tab has no faithful rendering.
        expect2("residue-tab",
                lambda t: replace_in(t / GATES, "an ampersand,", "a\ttab,"))
        # (s) A site shell missing the {{content}} token fails closed (the shell validator refuses it).
        expect2("shell-missing-content",
                lambda t: (t / "docs" / "_shell.html").write_text(
                    (t / "docs" / "_shell.html").read_text("utf-8").replace("{{content}}", ""),
                    encoding="utf-8"))
        # (t) An invalid-UTF-8 generated Markdown target fails closed (exit 2), via reconcile's decode.
        expect2("md-invalid-utf8",
                lambda t: (t / MD_REL).write_bytes(b"\xff\xfe not utf-8"))
        # (u) An invalid-UTF-8 generated HTML target fails closed (exit 2): the whole page is a generated
        # file now, so reconcile decodes and refuses a corrupt one just as it does the Markdown view.
        expect2("html-invalid-utf8",
                lambda t: (t / HTML_REL).write_bytes(b"\xff\xfe not utf-8"))
        # (v) A boolean roadmap version fails closed: 'version = true' must NOT satisfy the version check
        # (Python treats True == 1, which a plain '!= 1' test would have let through).
        expect2("roadmap-bool-version",
                lambda t: replace_in(t / ROADMAP, "version = 1", "version = true"))
        # (x) A whitespace-only pending description fails closed: a blank (stripped-empty) description is
        # not a description, so a pending row carrying only whitespace does not satisfy the requirement.
        expect2("roadmap-pending-ws-desc",
                lambda t: replace_in(
                    t / ROADMAP,
                    'description = "A self-test intended build for the apex rule."',
                    'description = "   "'))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant tree generates and regenerates drift-clean with enforced, none, "
          "and pending all present, stable anchors, the composed page chrome, the class legend, and each "
          "mechanism's ledger residual rendered VERBATIM (a tag-shaped token survives as visible text, a "
          "three-backtick run is wrapped in a lengthened fence, and a MULTI-PARAGRAPH residue round-trips "
          "verbatim inside its own Markdown fence and its HTML blockquote); the display-fidelity assertion "
          "fails closed on a truncated residual block or a residue missing from the Markdown; a drifted "
          "Markdown target AND a hand edit to the generated page chrome each fail --check (exit 1); and an "
          "unknown roadmap top-level or row key, a missing or extra roadmap row, an enforced/none/mechanism "
          "mismatch against the ledger linkage, a none row with a description, a pending row missing its "
          "description or carrying a mechanism or carrying only whitespace, a NEWLINE in a table-cell "
          "description, an unsupported lint: reference, a stale committed ledger, an en dash or a raw tab "
          "in a residue, a boolean roadmap version, a site shell missing its content token, and an "
          "invalid-UTF-8 generated Markdown or HTML target all fail closed (exit 2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
