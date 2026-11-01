"""Structural gate for the OPF init later-slice obligation crosswalk (opf/spec/OPF-INIT-RECONCILIATION.md).

The opf-init-p0 gate residue in .aiqt/core/gates/manifest.toml lists the later OPF-INIT obligations it leaves
out of scope. This gate reads that list from the residue, requires it to equal the frozen roster below, and
then parses the crosswalk: every residue obligation has exactly one "## Obligation:" entry, no entry names an
obligation outside the roster, the Migrate/Move entry keeps each of its three "### Split:" children exactly
once, every entry carries an Owner and an Ownership, and an entry whose Disposition is "transferred" names as
its Successor a backlog id from the closed list in TODO.md at the repository root and an Owner other than
"none yet". An entry ends at the next heading that is not an entry heading, and at any line of these
shapes, each of which a Markdown reader can render as other structure: a setext underline or thematic break,
a line opening an HTML block, a blockquote, a list item marked other than "- " at the line start, an
indented list item, an indented code line, or a table row or table delimiter row (a level 1 or 2 heading,
or such a line, also ends the obligation a split sits under). Every other line is, to this gate, a field
line, a blank line or prose. An Owner, Ownership, Disposition or Successor line after that end and before
the next entry, before the first entry, or written as the text of such a structure line, belongs to no entry
and is refused. A heading other than an entry heading, a structure line, or a setext heading whose text
(block markers and HTML tags removed) begins with "Obligation" or "Split" followed by a colon, in any case,
cannot evaluate (exit 2). Headings, fields and TODO.md table rows inside a fenced code block, or inside an
HTML comment that opens its line, do not count; a "<!--" later in a line removes only the rest of that line
up to its "-->" and hides no later line. It checks
STRUCTURE only: it never reads a cited symbol, so a green run says nothing about whether any obligation is
implemented or whether a named successor accepted the work.

Exit 0 clean; exit 1 on a concrete finding (a crosswalk that parses to no usable entries is a finding, since
any text parses); exit 2 (cannot evaluate) when the manifest, the crosswalk or TODO.md is missing or is not
UTF-8, the crosswalk writes an entry in a form this gate does not read, the manifest does not parse, the
residue no longer has the shape this gate reads, or TODO.md lists no backlog id (in a standalone opf/ copy
TODO.md is absent, so the successor check cannot evaluate and the gate never passes there). The --self-test
is source-free (in-memory fixtures only), so it also runs in a standalone opf/ copy with no manifest beside
it; each fixture must be red with every check active and green with only its
own check removed.
Run: python3 -I -B opf/tools/check_opf_init_reconciliation.py [--self-test].
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_init_reconciliation.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import io
import re
import tomllib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path(".aiqt") / "core" / "gates" / "manifest.toml"
CROSSWALK = Path("opf") / "spec" / "OPF-INIT-RECONCILIATION.md"
BACKLOG = Path("TODO.md")
RESIDUE_GATE = "opf-init-p0"

# The residue sentence the obligation list sits in, and the list's own end.
RESIDUE_LEAD = "remain separate obligations awaiting maintainer design review: "
RESIDUE_END = ";"

# The ten obligations the residue names, normalized (_norm). Verb wiring and the CLI verbs stay separate
# entries until a maintainer decides their overlap.
OBLIGATIONS = (
    "target and control binding",
    "adoption classifier",
    "coordination",
    "keep-apply",
    "verb wiring",
    "git staging",
    "publication",
    "finalization",
    "cli verbs",
    "migrate/move",
)
SPLIT_PARENT = "migrate/move"
SPLIT_CHILDREN = ("import disposition", "move disposition", "whole-store relocation")
DISPOSITIONS = ("open", "retained", "transferred", "superseded")
# Owner values that name nobody, compared after casefold and a trailing-dot strip.
NO_OWNER = ("", "none", "none yet")

_OBLIGATION_RE = re.compile(r"^## Obligation: (.+?)\s*$")
_SPLIT_RE = re.compile(r"^### Split: (.+?)\s*$")
_FIELD_RE = re.compile(r"^- ([A-Za-z ]+): ?(.*?)\s*$")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]|$)")
# The fields the checks below read; one outside an entry is refused, never attached to the entry above.
READ_FIELDS = ("owner", "ownership", "disposition", "successor")
# Lines a Markdown reader can render as structure that is neither an ATX heading nor a "- " item. Each ends
# the entry above it, so a field below one belongs to no entry; the list closes the class by line shape, not
# by heading syntax.
_SETEXT = "setext underline or thematic break"
_STRUCTURE_FORMS = (
    (_SETEXT, re.compile(r"^ {0,3}(?:=+|-+|(?:[-*_][ \t]*){3,})[ \t]*$")),
    ("HTML block", re.compile(r"^ {0,3}<[A-Za-z/!?]")),
    ("blockquote", re.compile(r"^ {0,3}>")),
    ("list item not marked \"- \"", re.compile(r"^ {0,3}(?:[*+]|-(?=\t)|\d{1,9}[.)])(?:[ \t]|$)")),
    ("indented list item", re.compile(r"^[ \t]+(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")),
    ("indented code", re.compile(r"^(?: {4}| {0,3}\t)[ \t]*\S")),
    ("table row", re.compile(r"^ {0,3}(?:\||:?-+:?[ \t]*\|)")),
)
# Leading block markers (indent, blockquote, list, ATX hashes) and HTML tags, removed to read a line's text.
_MARKER_RE = re.compile(r"^[ \t]*(?:>|(?:[-*+]|\d{1,9}[.)]|#{1,6})(?=[ \t]|$))")
_TAG_RE = re.compile(r"<[^>]*>")
# Text that names an entry; a line other than an entry heading that renders it is a form this gate cannot read.
_ENTRY_WORD_RE = re.compile(r"(?:obligation|split)[ \t]*:", re.IGNORECASE)
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# A comment hides lines only when "<!--" opens the line (an HTML block); elsewhere it is inline.
_COMMENT_OPEN_RE = re.compile(r"^ {0,3}<!--")
_INLINE_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)")
# A backlog table row in TODO.md: "| ID | Status | Item | Tags |", one id per row.
_BACKLOG_ROW_RE = re.compile(r"^\| ([A-Z0-9]+(?:-[A-Z0-9]+)*) \|")


class CannotEvaluate(Exception):
    pass


def _norm(name):
    name = " ".join(name.split()).casefold()
    if name.startswith("the "):
        name = name[4:]
    return name


def _parse_manifest(manifest_text):
    try:
        return tomllib.loads(manifest_text)
    except (ValueError, RecursionError, MemoryError) as exc:
        # ValueError: tomllib.TOMLDecodeError and an over-long integer literal; RecursionError: deep
        # nesting and (CPython 3.14.8 on) a 1001-part key; MemoryError: a many-part key under a memory
        # limit. Each is an unparseable manifest, so the gate cannot evaluate.
        raise CannotEvaluate("gates manifest does not parse: {}".format(exc)) from None


def _gate_tables(doc):
    """The manifest's gate array. Any other value (an integer, float, bool, date, time, string or table) is a
    manifest this gate cannot read, never a TypeError."""
    gates = doc.get("gate")
    if not isinstance(gates, list):
        raise CannotEvaluate("gates manifest has no gate array (gate is {})".format(type(gates).__name__))
    return gates


def residue_obligations(manifest_text):
    """The obligation names the opf-init-p0 residue lists, normalized and in order."""
    doc = _parse_manifest(manifest_text)
    gates = [g for g in _gate_tables(doc) if isinstance(g, dict) and g.get("id") == RESIDUE_GATE]
    if len(gates) != 1 or not isinstance(gates[0].get("residue"), str):
        raise CannotEvaluate("gates manifest has no single {} gate with a residue".format(RESIDUE_GATE))
    residue = gates[0]["residue"]
    if residue.count(RESIDUE_LEAD) != 1:
        raise CannotEvaluate("the {} residue no longer carries the obligation list lead".format(RESIDUE_GATE))
    tail = residue.split(RESIDUE_LEAD, 1)[1]
    if RESIDUE_END not in tail:
        raise CannotEvaluate("the {} residue obligation list has no end".format(RESIDUE_GATE))
    listed = tail.split(RESIDUE_END, 1)[0].rstrip(".")
    # str.split always gives at least one part, so an empty list shows as one empty name.
    parts = [p.strip() for p in listed.split(",")]
    if parts[-1].startswith("and "):
        parts[-1] = parts[-1][4:]
    names = [_norm(p) for p in parts]
    if any(not n for n in names):
        raise CannotEvaluate("the {} residue obligation list is empty or malformed".format(RESIDUE_GATE))
    return names


def backlog_ids(todo_text):
    """The closed successor list: the first cell of each row of every TODO.md table headed "| ID |", read
    through _visible_lines, so a row inside a fenced code block or an HTML comment names no id. A TODO.md
    that lists no id cannot evaluate, so an unreadable backlog never lets a transferred entry pass."""
    ids = set()
    in_table = False
    for line in _visible_lines(todo_text):
        if line.startswith("| ID |"):
            in_table = True
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        m = _BACKLOG_ROW_RE.match(line)
        if in_table and m:
            ids.add(m.group(1))
    if not ids:
        raise CannotEvaluate("{} lists no backlog id, so a transferred entry's successor cannot be "
                             "checked".format(BACKLOG))
    return frozenset(ids)


def _visible_lines(text):
    """A Markdown text's lines minus fenced code blocks and HTML comments, so a heading, field or table row a
    reader does not see as one never counts. A fence closes on a line of the same character at least as
    long, and an unclosed fence hides the rest of the file. A backtick line whose info string holds a
    backtick (```x```) is inline code, not a fence opener (CommonMark). A comment that opens its line (up to
    three spaces, then "<!--") hides that line through the whole line holding its closing "-->", as an HTML
    block does, and an unclosed one hides the rest of the file. A "<!--" later in a line is inline: it is
    removed through its "-->" on that line, or through the end of the line when it does not close there, and
    hides no later line."""
    fence = None
    in_comment = False
    for line in text.split("\n"):
        if in_comment:
            in_comment = "-->" not in line
            continue
        m = _FENCE_RE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
                fence = None
            continue
        if m and m.group(1)[0] == "`" and "`" in m.group(2):
            m = None
        if m:
            fence = m.group(1)
            continue
        if _COMMENT_OPEN_RE.match(line):
            in_comment = "-->" not in line[line.find("<!--") + 4:]
            continue
        yield _INLINE_COMMENT_RE.sub("", line)


def _structure_form(line):
    """The name of the _STRUCTURE_FORMS shape this line has, or None."""
    for name, pattern in _STRUCTURE_FORMS:
        if pattern.match(line):
            return name
    return None


def _inner(text):
    """The text with its leading block markers and its HTML tags removed: the words a reader sees."""
    m = _MARKER_RE.match(text)
    while m:
        text = text[m.end():]
        m = _MARKER_RE.match(text)
    return _TAG_RE.sub("", text).strip()


def _refuse_entry_form(text, form):
    if _ENTRY_WORD_RE.match(_inner(text)):
        raise CannotEvaluate("{} writes an entry where this gate does not read one ({}): {!r}".format(
            CROSSWALK, form, text))


def parse_crosswalk(text):
    """[(level, name, fields, parent)]: level is "obligation" or "split", fields a dict of the entry's
    "- Field: value" lines (a repeated field keeps every value), parent the enclosing obligation. Any other
    ATX heading ends the entry above it (a level 1 or 2 heading also ends the obligation a split sits
    under), and so does any line of a _STRUCTURE_FORMS shape (which also ends that obligation), so a field
    below it attaches to no entry: a field this gate reads (READ_FIELDS) there, or before the first entry,
    or written as the text of such a line, is kept as a ("stray", field, {}, None) record for _check_stray
    to refuse. Raises CannotEvaluate when a heading other than an entry heading, a structure line, or the
    paragraph a setext underline turns into a heading names an entry ("Obligation:" or "Split:")."""
    entries = []
    parent = None
    fields = None
    paragraph = []
    for line in _visible_lines(text):
        prose, paragraph = paragraph, []
        m = _OBLIGATION_RE.match(line)
        if m:
            parent = _norm(m.group(1))
            fields = {}
            entries.append(("obligation", parent, fields, None))
            continue
        m = _SPLIT_RE.match(line)
        if m:
            fields = {}
            entries.append(("split", _norm(m.group(1)), fields, parent))
            continue
        m = _HEADING_RE.match(line)
        if m:
            _refuse_entry_form(line, "heading")
            fields = None
            if len(m.group(1)) <= 2:
                parent = None
            continue
        m = _FIELD_RE.match(line)
        if m:
            field = m.group(1).strip().casefold()
            if fields is not None:
                fields.setdefault(field, []).append(m.group(2))
            elif field in READ_FIELDS:
                entries.append(("stray", field, {}, None))
            continue
        form = _structure_form(line)
        if form:
            if prose and form == _SETEXT:
                _refuse_entry_form(" ".join(prose), form)
            _refuse_entry_form(line, form)
            fields = None
            parent = None
            m = _FIELD_RE.match("- " + _inner(line))
            if m and m.group(1).strip().casefold() in READ_FIELDS:
                entries.append(("stray", m.group(1).strip().casefold(), {}, None))
            continue
        if line.strip() and not line.startswith("- "):
            paragraph = prose + [line]
    return entries


def _fielded(entries):
    """The obligation and split entries, without the stray records. A stray record has no fields, so only the
    checks that refuse an absent field need this filter."""
    return [e for e in entries if e[0] != "stray"]


def _check_missing(entries, roster, backlog):
    names = [e[1] for e in entries if e[0] == "obligation"]
    return ["missing: residue obligation {!r} has no entry".format(n) for n in roster if n not in names]


def _check_duplicate(entries, roster, backlog):
    names = [e[1] for e in entries if e[0] == "obligation"]
    return ["duplicate: obligation {!r} has {} entries".format(n, names.count(n))
            for n in sorted(set(names)) if names.count(n) > 1]


def _check_unknown(entries, roster, backlog):
    return ["unknown: obligation {!r} is not in the residue".format(e[1])
            for e in entries if e[0] == "obligation" and e[1] not in roster]


def _check_split(entries, roster, backlog):
    children = [e[1] for e in entries if e[0] == "split" and e[3] == SPLIT_PARENT]
    found = ["split-child-missing: {!r} has no {!r} split".format(SPLIT_PARENT, c)
             for c in SPLIT_CHILDREN if c not in children]
    found += ["split-child-duplicate: {!r} split {!r} appears {} times".format(SPLIT_PARENT, c, children.count(c))
              for c in SPLIT_CHILDREN if children.count(c) > 1]
    found += ["split-unknown: split {!r} under {!r} is not a declared child".format(e[1], e[3])
              for e in entries if e[0] == "split" and (e[3] != SPLIT_PARENT or e[1] not in SPLIT_CHILDREN)]
    return found


def _check_disposition(entries, roster, backlog):
    found = []
    for level, name, fields, _parent in _fielded(entries):
        values = fields.get("disposition", [])
        if len(values) != 1 or values[0] not in DISPOSITIONS:
            found.append("disposition: {} {!r} needs exactly one Disposition from {}".format(
                level, name, "/".join(DISPOSITIONS)))
    return found


def _check_successor(entries, roster, backlog):
    found = []
    for level, name, fields, _parent in entries:
        if fields.get("disposition") == ["transferred"]:
            successors = fields.get("successor", [])
            if len(successors) != 1 or successors[0] not in backlog:
                found.append("transferred-successor: {} {!r} is transferred but its Successor is not exactly "
                             "one backlog id from {}: {!r}".format(level, name, BACKLOG, successors))
    return found


def _check_owner(entries, roster, backlog):
    found = []
    for level, name, fields, _parent in _fielded(entries):
        for field in ("owner", "ownership"):
            values = fields.get(field, [])
            if len(values) != 1 or not values[0]:
                found.append("owner: {} {!r} needs exactly one nonempty {}".format(level, name, field.capitalize()))
    return found


def _check_transferred_owner(entries, roster, backlog):
    found = []
    for level, name, fields, _parent in entries:
        owners = fields.get("owner", [])
        if fields.get("disposition") == ["transferred"] and any(
                o.casefold().rstrip(".").strip() in NO_OWNER for o in owners):
            found.append("transferred-owner: {} {!r} is transferred but its Owner names nobody".format(level, name))
    return found


def _check_stray(entries, roster, backlog):
    return ["stray: field {} sits under a heading that is not an entry, after or inside a line that ends an "
            "entry, or before the first entry, so it belongs to no entry".format(e[1].capitalize())
            for e in entries if e[0] == "stray"]


CHECKS = {
    "missing": _check_missing,
    "duplicate": _check_duplicate,
    "unknown": _check_unknown,
    "split": _check_split,
    "disposition": _check_disposition,
    "successor": _check_successor,
    "owner": _check_owner,
    "transferred-owner": _check_transferred_owner,
    "stray": _check_stray,
}


def findings(text, backlog, roster=OBLIGATIONS, disabled=frozenset()):
    entries = parse_crosswalk(text)
    out = []
    for name, check in CHECKS.items():
        if name not in disabled:
            out.extend(check(entries, roster, backlog))
    return out


def _read(rel):
    try:
        return (ROOT / rel).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CannotEvaluate("cannot read {}: {}".format(rel, exc))


def run_gate():
    try:
        listed = residue_obligations(_read(MANIFEST))
        text = _read(CROSSWALK)
        backlog = backlog_ids(_read(BACKLOG))
        crosswalk_found = findings(text, backlog)
    except CannotEvaluate as exc:
        sys.stderr.write("CANNOT EVALUATE check_opf_init_reconciliation: {}\n".format(exc))
        return 2
    found = []
    if tuple(listed) != OBLIGATIONS:
        found.append("residue drift: {} lists {!r}, the frozen roster is {!r}".format(
            MANIFEST, listed, list(OBLIGATIONS)))
    found.extend(crosswalk_found)
    for line in found:
        sys.stdout.write("FAIL check_opf_init_reconciliation: {}\n".format(line))
    if found:
        return 1
    sys.stdout.write("PASS check_opf_init_reconciliation: {} obligations, {} split children\n".format(
        len(OBLIGATIONS), len(SPLIT_CHILDREN)))
    return 0


# --- self-test ---------------------------------------------------------------------------------------

def _entry(name, disposition="open", successor="none", header="## Obligation: ", owner="none yet.",
           ownership="open."):
    return "{}{}\n\n- Owner: {}\n- Ownership: {}\n- Disposition: {}\n- Successor: {}\n\n".format(
        header, name, owner, ownership, disposition, successor)


def _transferred(name, successor="OPF-ADOPT-ORCH", header="## Obligation: ", owner="adoption work"):
    return _entry(name, "transferred", successor, header, owner, "transferred.")


_DISPLAY = {
    "keep-apply": "Keep-apply", "cli verbs": "CLI verbs", "migrate/move": "Migrate/Move",
}


def _good(drop=None, extra="", children=SPLIT_CHILDREN, override=None):
    parts = ["# Crosswalk\n\n"]
    for name in OBLIGATIONS:
        if name == drop:
            continue
        if override and name in override:
            parts.append(override[name])
        else:
            parts.append(_entry(_DISPLAY.get(name, name)))
        if name == SPLIT_PARENT:
            parts.extend(_entry(c, header="### Split: ") for c in children)
    parts.append(extra)
    return "".join(parts)


_MANIFEST_FIXTURE = (
    '[[gate]]\nid = "opf-init-p0"\nresidue = "Class c: the later OPF-INIT slices (P1 and beyond) are out of scope '
    'and remain separate obligations awaiting maintainer design review: target and control binding, the adoption '
    'classifier, coordination, Keep-apply, verb wiring, git staging, publication, finalization, the CLI verbs, and '
    'Migrate/Move; the contract fixtures drive no live adopter product repository."\n')

# Two backlog tables; prose and a row outside an "| ID |" table name no id.
_BACKLOG_FIXTURE = (
    "# TODO\n\n| ID | Status | Item | Tags |\n| --- | --- | --- | --- |\n"
    "| OPF-ADOPT-ORCH | Partly done | Adoption | `[public]` |\n\nOPF-ADOPTORCH in prose is not a row.\n\n"
    "| ID | Status | Item | Tags |\n| --- | --- | --- | --- |\n| OPF-IMPORT-COMPLETION | Planned | Import | `[x]` |\n\n"
    "| Name | Note |\n| XYZZY-NOT-REAL | a table with no ID header |\n")

# Two unparseable manifests: one tomllib refuses with RecursionError, one with TOMLDecodeError.
_DEEP_MANIFEST = "deep = " + "[" * 5000 + "]" * 5000 + "\n"
_INVALID_MANIFEST = "[[gate]\n"

# A gate value of each TOML type that is not an array of tables; the first eight raised TypeError when iterated.
_NON_LIST_GATES = (
    ("integer", "gate = 1\n"), ("float", "gate = 1.5\n"), ("bool", "gate = true\n"), ("date", "gate = 1979-05-27\n"),
    ("time", "gate = 07:32:00\n"), ("datetime", "gate = 1979-05-27T07:32:00Z\n"), ("inf", "gate = inf\n"),
    ("nan", "gate = nan\n"), ("string", 'gate = "x"\n'), ("table", "[gate]\nid = 1\n"), ("absent", "other = 1\n"),
)
_TYPEERROR_GATES = 8

# Successor values an open rule (anything but empty or "none") accepted; each must be refused.
_OPEN_RULE_SUCCESSORS = ("XYZZY-NOT-REAL", "none yet", "TBD", "-", "OPF-ADOPTORCH", "opf-adopt-orch")


def _unhandled_parse(manifest_text):
    """The manifest parse with its handler removed: the flip each unparseable-manifest vector turns red under."""
    return tomllib.loads(manifest_text)


def _unguarded_gate_tables(doc):
    """The gate lookup with its type check removed: the flip each non-list gate vector turns red under."""
    return doc.get("gate", [])


def _open_successor_rule(entries, roster, backlog):
    """The successor check as an open rule, any value but empty or "none" passing: the flip each closed-list
    vector turns red under."""
    return ["open-rule: {!r}".format(name) for _level, name, fields, _parent in entries
            if fields.get("disposition") == ["transferred"] and (
                len(fields.get("successor", [])) != 1
                or fields["successor"][0].strip().casefold() in ("", "none"))]


def _unguarded_backlog_ids(todo_text):
    """The backlog read with its empty-list refusal removed: the flip the no-id vector turns red under."""
    return frozenset(m.group(1) for m in map(_BACKLOG_ROW_RE.match, todo_text.split("\n")) if m)


def _files(manifest=_MANIFEST_FIXTURE, crosswalk=None, backlog=_BACKLOG_FIXTURE):
    texts = {MANIFEST: manifest, CROSSWALK: _good() if crosswalk is None else crosswalk}
    if backlog is not None:
        texts[BACKLOG] = backlog
    return texts


def _run(texts):
    """(exit code, stdout, stderr) of run_gate over in-memory files, an absent file reading as unreadable; an
    exception that escapes run_gate gives a None exit code."""
    def read(rel):
        if rel not in texts:
            raise CannotEvaluate("cannot read {}: absent".format(rel))
        return texts[rel]
    out, err = io.StringIO(), io.StringIO()
    try:
        with mock.patch.object(sys.modules[__name__], "_read", read), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_gate()
    except Exception:
        code = None
    return code, out.getvalue(), err.getvalue()


def _gate_cannot_evaluate(manifest_text, message="gates manifest does not parse: "):
    """True when run_gate over this manifest text and a clean crosswalk exits 2 with the named message; an
    exception that escapes run_gate is False."""
    code, _out, err = _run(_files(manifest=manifest_text))
    return code == 2 and err.startswith("CANNOT EVALUATE check_opf_init_reconciliation: " + message)


# Manifests whose residue this gate cannot read, each with the start of the refusal it must give. Without the
# guard each names, the first and third raise AttributeError and the fourth IndexError, the second and fifth
# pass on the first match (exit 0), and the last two read a wrong list (residue drift, exit 1).
_RESIDUE_SHAPES = (
    ("gate-entry-not-a-table", "gate = [1]\n", "gates manifest has no single "),
    ("two-residue-gates", _MANIFEST_FIXTURE * 2, "gates manifest has no single "),
    ("residue-not-a-string", '[[gate]]\nid = "opf-init-p0"\nresidue = 1\n', "gates manifest has no single "),
    ("no-lead", _MANIFEST_FIXTURE.replace("review: ", "review "), "the opf-init-p0 residue no longer carries "),
    ("two-leads", _MANIFEST_FIXTURE.replace("Migrate/Move;", "Migrate/Move; and " + RESIDUE_LEAD + "x;"),
     "the opf-init-p0 residue no longer carries "),
    ("no-end", _MANIFEST_FIXTURE.replace("Migrate/Move;", "Migrate/Move."),
     "the opf-init-p0 residue obligation list has no end"),
    ("empty-name", _MANIFEST_FIXTURE.replace("coordination, ", "coordination, , "),
     "the opf-init-p0 residue obligation list is empty or malformed"),
)

# TODO.md rows a reader does not see: a fenced block and an HTML comment, each holding an "| ID |" table.
_HIDDEN_BACKLOG = ("```\n| ID | Status |\n| OPF-FENCED | x |\n```\n"
                   "<!--\n| ID | Status |\n| OPF-COMMENTED | x |\n-->\n")


class _StubRoot:
    """A ROOT stand-in under which every file reads as `data` (bytes), or raises `data` (an exception)."""

    def __init__(self, data):
        self.data = data

    def __truediv__(self, rel):
        return self

    def read_bytes(self):
        if isinstance(self.data, BaseException):
            raise self.data
        return self.data


def _run_over_root(root):
    """(exit code, stderr) of run_gate reading every file through the real _read under `root`; an exception
    that escapes run_gate gives a None exit code."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with mock.patch.object(sys.modules[__name__], "ROOT", root), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_gate()
    except Exception:
        code = None
    return code, err.getvalue()


def _findings_or_none(text, backlog, disabled=frozenset()):
    """findings(), or None when it raises, so a parser that crashes fails its vector instead of the run."""
    try:
        return findings(text, backlog, disabled=disabled)
    except Exception:
        return None


def _owner_after(separator):
    """The clean crosswalk with its last entry's Owner moved below `separator`: green only when the gate lets
    that Owner attach to the entry above the separator."""
    last = _entry(SPLIT_CHILDREN[-1], header="### Split: ")
    kept = "".join(line for line in last.splitlines(True) if not line.startswith("- Owner:"))
    return _good().replace(last, kept) + separator + "- Owner: none yet.\n"


def _with_line(name, line):
    """The clean crosswalk with `line` placed in entry `name` above its fields."""
    return _good(override={name: _entry(name).replace("\n\n- Owner:", "\n\n" + line + "\n\n- Owner:", 1)})


# Lines that end the entry above them, each with the _STRUCTURE_FORMS shape that holds it red.
_ENDING_LINES = (
    ("setext-dash", "Appendix\n---\n\n", _SETEXT),
    ("setext-equals", "Appendix\n===\n\n", _SETEXT),
    ("html-heading", "<h2>Appendix</h2>\n\n", "HTML block"),
    ("blockquote-heading", "> ## Appendix\n\n", "blockquote"),
    ("indented-code", "    Appendix\n\n", "indented code"),
    ("table-row", "| Appendix |\n\n", "table row"),
    ("table-delimiter-row", "Name | Note\n--- | ---\n\n", "table row"),
)

# Read fields under a non-entry heading written with another list marker, each with the shape that holds it red.
_MARKED_FIELDS = (
    ("star", "* Owner: none yet.\n", "list item not marked \"- \""),
    ("plus", "+ Owner: none yet.\n", "list item not marked \"- \""),
    ("ordered", "1. Owner: none yet.\n", "list item not marked \"- \""),
    ("nested-dash", "  - Owner: none yet.\n", "indented list item"),
)

# A second coordination entry written in a form this gate does not read as an entry heading: each cannot
# evaluate, and each passes (exit 0) once _ENTRY_WORD_RE matches nothing.
_ENTRY_FORMS = (
    ("setext", "Obligation: coordination\n===\n"),
    ("html", "<h2>Obligation: coordination</h2>\n"),
    ("blockquote", "> ## Obligation: coordination\n"),
    ("indented-heading", " ## Obligation: coordination\n"),
    ("indented-code", "    ## Obligation: coordination\n"),
    ("star-item", "* Obligation: coordination\n"),
)

# One vector per part of _HEADING_RE: (label, crosswalk text, whether it is red, the pattern without that part).
_HEADING_PARTS = (
    ("indent", _owner_after("   ## Appendix\n\n"), True, r"^(#{1,6})(?:[ \t]|$)"),
    ("indent-cap", _good().replace("### Split: move", "    ### Notes\n\n### Split: move"), True,
     r"^ *(#{1,6})(?:[ \t]|$)"),
    ("space-separator", _owner_after("## Appendix\n\n"), True, r"^ {0,3}(#{1,6})(?:\t|$)"),
    ("tab-separator", _owner_after("##\tAppendix\n\n"), True, r"^ {0,3}(#{1,6})(?: |$)"),
    ("bare-end", _owner_after("##\n\n"), True, r"^ {0,3}(#{1,6})[ \t]"),
    ("separator-required", _with_line("publication", "#5 is tracked elsewhere."), False, r"^ {0,3}(#{1,6})"),
    ("six-hash-heading", _owner_after("###### Appendix\n\n"), True, r"^ {0,3}(#{1,5})(?:[ \t]|$)"),
    ("six-hash-cap", _with_line("publication", "####### not a heading"), False, r"^ {0,3}(#+)(?:[ \t]|$)"),
)


def self_test():
    failures = []
    total = 0

    def expect(cond, label):
        nonlocal total
        total += 1
        if not cond:
            failures.append(label)

    module = sys.modules[__name__]
    expect(residue_obligations(_MANIFEST_FIXTURE) == list(OBLIGATIONS), "residue/parse-roster")
    expect(residue_obligations(_MANIFEST_FIXTURE.replace("Migrate/Move;", "Migrate/Move.;")) == list(OBLIGATIONS),
           "residue/trailing-dot-stripped")
    for bad, label in ((_MANIFEST_FIXTURE.replace("review: ", "review "), "residue/no-lead"),
                       ('[[gate]]\nid = "other"\nresidue = "x"\n', "residue/no-gate"),
                       ("[[gate]\n", "residue/unparseable")):
        try:
            residue_obligations(bad)
        except CannotEvaluate:
            expect(True, label)
        except Exception:
            expect(False, label)
        else:
            expect(False, label)
    dropped = _MANIFEST_FIXTURE.replace("git staging, ", "")
    expect(residue_obligations(dropped) != list(OBLIGATIONS), "residue/drop-detected")
    code, out, _err = _run(_files(manifest=dropped))
    expect(code == 1 and "FAIL check_opf_init_reconciliation: residue drift: " in out, "residue/drift-fails-gate")
    for bad, label in ((_DEEP_MANIFEST, "deeply-nested"), (_INVALID_MANIFEST, "invalid")):
        expect(_gate_cannot_evaluate(bad), "unparseable-manifest/{}".format(label))
        with mock.patch.object(module, "_parse_manifest", _unhandled_parse):
            expect(not _gate_cannot_evaluate(bad), "unparseable-manifest/{}-red-without-handler".format(label))
    for index, (label, bad) in enumerate(_NON_LIST_GATES):
        expect(_gate_cannot_evaluate(bad, "gates manifest has no gate array"), "non-list-gate/{}".format(label))
        if index < _TYPEERROR_GATES:
            with mock.patch.object(module, "_gate_tables", _unguarded_gate_tables):
                expect(_run(_files(manifest=bad))[0] is None, "non-list-gate/{}-red-without-guard".format(label))
    for label, bad, message in _RESIDUE_SHAPES:
        expect(_gate_cannot_evaluate(bad, message), "residue-shape/{}".format(label))
    for label, data in (("not-utf8", b"\xff"), ("os-error", FileNotFoundError("absent"))):
        code, err = _run_over_root(_StubRoot(data))
        expect(code == 2 and "cannot read " in err, "read/{}-cannot-evaluate".format(label))

    backlog = backlog_ids(_BACKLOG_FIXTURE)
    expect(backlog == {"OPF-ADOPT-ORCH", "OPF-IMPORT-COMPLETION"}, "backlog/ids-from-id-tables-only")
    expect(backlog_ids(_BACKLOG_FIXTURE + _HIDDEN_BACKLOG) == backlog, "backlog/hidden-rows-name-no-id")
    code, _out, err = _run(_files(backlog="# TODO\n\n" + _HIDDEN_BACKLOG))
    expect(code == 2 and "lists no backlog id" in err, "backlog/only-hidden-ids-cannot-evaluate")
    expect(_run(_files())[0] == 0, "gate/clean-passes")
    code, _out, err = _run(_files(backlog=None))
    expect(code == 2 and "cannot read TODO.md" in err, "backlog/absent-cannot-evaluate")
    no_ids = "# TODO\n\nNo table here; OPF-ADOPT-ORCH in prose.\n"
    code, _out, err = _run(_files(backlog=no_ids))
    expect(code == 2 and "lists no backlog id" in err, "backlog/no-id-cannot-evaluate")
    with mock.patch.object(module, "backlog_ids", _unguarded_backlog_ids):
        expect(_run(_files(backlog=no_ids))[0] == 0, "backlog/no-id-passes-without-refusal")

    expect(findings(_good(), backlog) == [], "clean/no-findings")
    transferred = _good(override={"verb wiring": _transferred("verb wiring")})
    expect(findings(transferred, backlog) == [], "clean/transferred-with-backlog-successor")
    expect(bool(findings(transferred, frozenset({"OPF-IMPORT-COMPLETION"}))),
           "flip/transferred-red-when-successor-leaves-backlog")
    for hidden, label in (("```\n{}```\n", "backtick-fence"), ("~~~~ text\n{}~~~~\n", "tilde-fence"),
                          ("<!--\n{}-->\n", "comment")):
        expect(findings(_good(extra=hidden.format(_entry("coordination"))), backlog) == [],
               "clean/hidden-duplicate-ignored/{}".format(label))
    expect(findings(_good(drop="git staging", extra="```\nx\n```\n" + _entry("git staging")), backlog) == [],
           "clean/entry-after-closed-fence-counts")
    expect(findings(_good(override={"coordination": _entry("coordination") + "<!-- a note -->\n"}), backlog) == [],
           "clean/entry-after-closed-comment-counts")
    expect(_findings_or_none(_good(drop="git staging", extra="```x```\n\n" + _entry("git staging")), backlog) == [],
           "clean/inline-code-line-opens-no-fence")
    expect(_findings_or_none(_good().replace("### Split: move", "### Notes\n\nA note.\n\n### Split: move"),
                             backlog) == [], "clean/level-3-heading-keeps-split-parent")
    expect(_findings_or_none(_good(extra="## Appendix\n\n- Note: unread.\n"), backlog) == [],
           "clean/unread-field-outside-entry-ignored")
    # The reported shape: the last entry lacks its Owner and a field under "## Appendix" supplied it.
    expect(_run(_files(crosswalk=_owner_after("## Appendix\n\n")))[0] == 1, "stray/appendix-owner-fails-gate")
    # The same shape after each other line that ends an entry: red, and green once that shape is not read.
    for label, separator, form in _ENDING_LINES:
        text = _owner_after(separator)
        expect(_run(_files(crosswalk=text))[0] == 1, "stray/{}-owner-fails-gate".format(label))
        with mock.patch.object(module, "_STRUCTURE_FORMS", tuple(f for f in _STRUCTURE_FORMS if f[0] != form)):
            expect(_findings_or_none(text, backlog) == [], "stray/{}-green-without-its-shape".format(label))
    for label, line, form in _MARKED_FIELDS:
        text = _good(extra="## Appendix\n\n" + line)
        with mock.patch.object(module, "_STRUCTURE_FORMS", tuple(f for f in _STRUCTURE_FORMS if f[0] != form)):
            expect(_findings_or_none(text, backlog) == [], "stray/marked-{}-green-without-its-shape".format(label))
    for label, form in _ENTRY_FORMS:
        text = _good(extra=form + "\nSee above.\n")
        code, _out, err = _run(_files(crosswalk=text))
        expect(code == 2 and " writes an entry " in err, "entry-form/{}-cannot-evaluate".format(label))
        with mock.patch.object(module, "_ENTRY_WORD_RE", re.compile(r"(?!)")):
            expect(_run(_files(crosswalk=text))[0] == 0, "entry-form/{}-passes-without-refusal".format(label))
    # A "<!--" inside a line hides no later line: the heading below it ends the entry, and an entry below it
    # counts. Both turn green or red the other way when any "<!--" opens a comment.
    inline_heading = _owner_after("A note <!-- x\n## Appendix -->\n")
    inline_entry = _good(drop="git staging", extra="A note <!-- x\n\n" + _entry("git staging"))
    expect(_run(_files(crosswalk=inline_heading))[0] == 1, "inline-comment/heading-below-ends-entry")
    expect(_findings_or_none(inline_entry, backlog) == [], "inline-comment/entry-below-counts")
    with mock.patch.object(module, "_COMMENT_OPEN_RE", re.compile(r".*?<!--")):
        expect(_findings_or_none(inline_heading, backlog) == [], "inline-comment/heading-hidden-when-opener-anywhere")
        expect(bool(_findings_or_none(inline_entry, backlog)), "inline-comment/entry-hidden-when-opener-anywhere")
    for label, text, red, mutant in _HEADING_PARTS:
        expect(bool(_findings_or_none(text, backlog)) == red, "heading-part/{}".format(label))
        with mock.patch.object(module, "_HEADING_RE", re.compile(mutant)):
            flipped = _findings_or_none(text, backlog)
        expect(flipped is not None and bool(flipped) != red, "heading-part/{}-flips-without-it".format(label))
    ownership = _good(extra="## Appendix\n\n- Ownership: open.\n")
    expect(bool(_findings_or_none(ownership, backlog)), "stray/ownership-refused")
    with mock.patch.object(module, "READ_FIELDS", tuple(f for f in READ_FIELDS if f != "ownership")):
        expect(_findings_or_none(ownership, backlog) == [], "stray/ownership-green-without-member")

    # Each fixture: (label, crosswalk text, the one check that must hold it red).
    fixtures = [("missing", _good(drop="git staging"), "missing")]
    for hidden, label in (("```\n{}```\n", "backtick-fence"), ("~~~\n{}```\n", "unclosed-fence"),
                          ("<!--\n{}-->\n", "comment"), ("<!--\n{}", "unclosed-comment")):
        fixtures.append(("missing-hidden-" + label,
                         _good(drop="git staging", extra=hidden.format(_entry("git staging"))), "missing"))
    # A line that does not close the open fence: another character, a shorter run, or an info string; the
    # entry after it stays hidden until the real close. A tilde fence may carry a backtick in its info string.
    for hidden, label in (("~~~\n```\n{}~~~\n", "other-character"), ("````\n```\n{}````\n", "shorter-run"),
                          ("```\n``` x\n{}```\n", "info-string"), ("~~~ `x`\n{}~~~\n", "tilde-backtick-info")):
        fixtures.append(("missing-fence-not-closed-" + label,
                         _good(drop="git staging", extra=hidden.format(_entry("git staging"))), "missing"))
    fixtures.append(("duplicate", _good(extra=_entry("coordination")), "duplicate"))
    fixtures.append(("unknown", _good(extra=_entry("store upgrade")), "unknown"))
    fixtures += [("split-missing-" + c.replace(" ", "-"),
                  _good(children=tuple(x for x in SPLIT_CHILDREN if x != c)), "split") for c in SPLIT_CHILDREN]
    fixtures.append(("split-duplicate", _good(children=SPLIT_CHILDREN + (SPLIT_CHILDREN[0],)), "split"))
    fixtures.append(("split-under-other-parent", _good(override={
        "coordination": _entry("coordination") + _entry(SPLIT_CHILDREN[0], header="### Split: ")}), "split"))
    fixtures.append(("split-after-unrelated-heading", _good(
        children=SPLIT_CHILDREN[:2], extra="## Appendix\n\n" + _entry(SPLIT_CHILDREN[2], header="### Split: ")),
        "split"))
    fixtures.append(("disposition-absent", _good(override={
        "publication": "## Obligation: publication\n\n- Owner: none yet.\n- Ownership: open.\n\n"}), "disposition"))
    fixtures.append(("disposition-unknown-value", _good(override={"publication": _entry("publication", "closed")}),
                     "disposition"))
    fixtures.append(("disposition-duplicate", _good(override={
        "publication": _entry("publication") + "- Disposition: open\n\n"}), "disposition"))
    for successor in _OPEN_RULE_SUCCESSORS + ("none", "", "None"):
        fixtures.append(("transferred-successor-" + (successor or "empty"),
                         _good(override={"finalization": _transferred("finalization", successor)}), "successor"))
    fixtures.append(("transferred-successor-duplicate", _good(override={
        "finalization": _transferred("finalization") + "- Successor: OPF-ADOPT-ORCH\n\n"}), "successor"))
    fixtures.append(("transferred-split-child", _good().replace(
        _entry("move disposition", header="### Split: "),
        _transferred("move disposition", "none", header="### Split: ")), "successor"))
    for field in ("Owner", "Ownership"):
        fixtures.append((field.casefold() + "-absent", _good(override={"publication": "".join(
            line for line in _entry("publication").splitlines(True) if not line.startswith("- " + field + ":"))}),
            "owner"))
    fixtures.append(("owner-empty", _good(override={"publication": _entry("publication", owner="")}), "owner"))
    fixtures.append(("ownership-duplicate", _good(override={
        "publication": _entry("publication") + "- Ownership: open.\n\n"}), "owner"))
    fixtures.append(("split-unknown-child", _good(children=SPLIT_CHILDREN + ("store upgrade",)), "split"))
    # " none" (two spaces after the colon) needs the strip; the comment hides an owner a reader never sees.
    for label, owner in (("none-yet.", "none yet."), ("None-yet", "None yet"), ("none", "none"),
                         ("space-none", " none"), ("commented", "none yet. <!-- adoption work -->")):
        fixtures.append(("transferred-owner-" + label, _good(override={
            "finalization": _transferred("finalization", owner=owner)}), "transferred-owner"))
    fixtures.append(("stray-under-appendix", _good(extra="## Appendix\n\n- Successor: OPF-ADOPT-ORCH\n"), "stray"))
    fixtures.append(("stray-before-first-entry", _good().replace("# Crosswalk\n\n", "# Crosswalk\n\n- Owner: x\n\n"),
                     "stray"))
    fixtures.append(("stray-under-level-3-heading", _good(override={
        "publication": _entry("publication") + "### Notes\n\n- Disposition: open\n\n"}), "stray"))
    fixtures += [("stray-after-" + label, _good(extra=separator + "- Successor: OPF-ADOPT-ORCH\n"), "stray")
                 for label, separator, _form in _ENDING_LINES]
    fixtures += [("stray-marked-" + label, _good(extra="## Appendix\n\n" + line), "stray")
                 for label, line, _form in _MARKED_FIELDS]
    fixtures.append(("stray-after-inline-comment", _good(
        extra="A note <!-- x\n## Appendix -->\n- Successor: OPF-ADOPT-ORCH\n"), "stray"))
    fixtures.append(("stray-ownership", _good(extra="## Appendix\n\n- Ownership: open.\n"), "stray"))
    for label, text, check in fixtures:
        red = _findings_or_none(text, backlog)
        expect(bool(red), "red/{}".format(label))
        expect(_findings_or_none(text, backlog, disabled=frozenset({check})) == [],
               "check-removed-green/{}".format(label))
    expect({f[2] for f in fixtures} == set(CHECKS), "every-check-has-a-fixture")
    with mock.patch.dict(CHECKS, {"successor": _open_successor_rule}):
        for successor in _OPEN_RULE_SUCCESSORS:
            text = _good(override={"finalization": _transferred("finalization", successor)})
            expect(findings(text, backlog) == [], "flip/open-successor-rule-accepts/{}".format(successor))

    for label in failures:
        sys.stdout.write("SELF-TEST FAIL check_opf_init_reconciliation: {}\n".format(label))
    if failures:
        return 1
    sys.stdout.write("PASS check_opf_init_reconciliation self-test ({} checks)\n".format(total))
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        return self_test()
    if argv:
        sys.stderr.write("usage: check_opf_init_reconciliation.py [--self-test]\n")
        return 2
    return run_gate()


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
