"""Structural gate for the OPF init later-slice obligation crosswalk (opf/spec/OPF-INIT-RECONCILIATION.md).

The opf-init-p0 gate residue in .aiqt/core/gates/manifest.toml lists the later OPF-INIT obligations it leaves
out of scope. This gate reads that list from the residue, requires it to equal the frozen roster below, and
then parses the crosswalk: every residue obligation has exactly one "## Obligation:" entry, no entry names an
obligation outside the roster, the Migrate/Move entry keeps each of its three "### Split:" children exactly
once, every entry carries an Owner and an Ownership, and an entry whose Disposition is "transferred" names as
its Successor a backlog id from the closed list in TODO.md at the repository root and an Owner other than
"none yet". It reads the crosswalk through a closed list of line shapes, each starting at the line start:
a blank line, an entry heading ("## Obligation: " or "### Split: " and a name), any other ATX heading (one to
six "#" and a space, a tab or the line end), a field line ("- Name: value"), an ordered item ("1. text"),
a line indented exactly to the text of the item line just above it, continuing that item, and prose. The
text of a heading, item, continuation or prose line must open with a letter, a digit that starts no list
marker, "(", a quote, a backtick or an option name such as --self-test, must hold no raw HTML outside a
code span, and must not begin with "Obligation", "Split", "Owner", "Ownership", "Disposition" or "Successor"
and a colon, in any case, once emphasis, code and escape marks are removed. Any other line cannot evaluate
(exit 2): a blockquote, a list item marked other than "- ", a table row, a setext underline or thematic
break, an HTML line, an indented line that continues no item, a fence or comment opened by an indented
line, text after the "-->" closing a comment that opens its line, and a carriage return or other control
character. An entry ends at the next heading that is not an entry heading (a level 1 or 2 heading also
ends the obligation a split sits under); prose and ordered items end no entry. An Owner, Ownership,
Disposition or Successor field line after that end and before the next entry, or before the first entry,
belongs to no entry and is refused. Lines inside a fenced code block, or inside an HTML comment that opens
its line, are not read, and the same holds for TODO.md table rows; a "<!--" later in a line removes only the
rest of that line up to its "-->" and hides no later line. A field's value is read from its first line
only. It checks STRUCTURE only: it never reads a cited symbol, so a green run says nothing about whether any
obligation is implemented or whether a named successor accepted the work.

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
# The fields the checks below read; one outside an entry is refused, never attached to the entry above.
READ_FIELDS = ("owner", "ownership", "disposition", "successor")
# The crosswalk is read through a closed list of line shapes; any other line cannot evaluate. Besides the
# entry headings and field lines above: a blank line, an ATX heading at the line start, an ordered item
# "<digits>. text" at the line start, a line indented exactly to the text of the item line just above it (or
# of the line it continues), and plain prose at the line start.
_HEADING_RE = re.compile(r"^(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_ORDERED_RE = re.compile(r"^(\d{1,9}\. )(.*)$")
# The text of a heading, item, continuation or prose line must open with a letter, a digit that does not start
# an ordered-list marker, "(", a quote, a backtick (a fence never reaches here), or one or two hyphens before a
# letter (an option name such as --self-test); every mark a reader can render as structure is outside this list.
_PLAIN_RE = re.compile(r"(?![0-9]{1,9}[.)](?:[ \t]|$))(?:[^\W_]|[(\"'`]|--?[^\W_])")
# Raw HTML, looked for outside single-line code spans: a reader can render a tag as structure.
_HTML_RE = re.compile(r"<[A-Za-z/!?]")
_CODE_SPAN_RE = re.compile(r"(`+).*?\1")
# Text that names an entry or a read field, after emphasis, code and escape marks are removed: outside an entry
# heading or a field line it is a form this gate cannot read.
_NAMES_RE = re.compile(r"(?:obligation|split|owner|ownership|disposition|successor)[ \t]*:", re.IGNORECASE)
_FORMATTING_RE = re.compile(r"[*_`\\~]")
# A carriage return (a line ending to a Markdown reader, not to this gate's line split) or another control
# character other than a tab, including the Unicode line and paragraph separators.
_CONTROL_RE = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f\\x85\\u2028\\u2029]")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# A comment hides lines only when "<!--" opens the line (an HTML block); elsewhere it is inline.
_COMMENT_OPEN_RE = re.compile(r"^ {0,3}<!--")
_INLINE_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)")
# A fence or comment opened by an indented line may sit inside a list item, where an unindented line closes
# it; this gate hides through the closing line, so the crosswalk refuses the indented opener instead.
_INDENTED_OPENER_RE = re.compile(r"^[ \t]+(?:`{3}|~{3}|<!--)")
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
    for _number, line in _visible_lines(todo_text):
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


def _visible_lines(text, strict=False):
    """A Markdown text's lines minus fenced code blocks and HTML comments, so a heading, field or table row a
    reader does not see as one never counts. A fence closes on a line of the same character at least as
    long, and an unclosed fence hides the rest of the file. A backtick line whose info string holds a
    backtick (```x```) is inline code, not a fence opener (CommonMark). A comment that opens its line (up to
    three spaces, then "<!--") hides that line through the whole line holding its closing "-->", as an HTML
    block does, and an unclosed one hides the rest of the file. A "<!--" later in a line is inline: it is
    removed through its "-->" on that line, or through the end of the line when it does not close there, and
    hides no later line. Yields (line number, line). With strict set (the crosswalk), raises CannotEvaluate
    on a control character (_CONTROL_RE), on a fence or comment opened by an indented line, and on text after
    the "-->" that closes a line-opening comment: that text is part of an HTML block a reader renders."""
    if strict:
        m = _CONTROL_RE.search(text)
        if m:
            number = text.count("\n", 0, m.start()) + 1
            raise _unread(number, text.split("\n")[number - 1], "a carriage return or other control character")
    fence = None
    in_comment = False
    for number, line in enumerate(text.split("\n"), 1):
        if in_comment:
            in_comment = "-->" not in line
            if strict and not in_comment:
                _refuse_remainder(number, line, line.split("-->", 1)[1])
            continue
        m = _FENCE_RE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
                fence = None
            continue
        if m and m.group(1)[0] == "`" and "`" in m.group(2):
            m = None
        if (m or _COMMENT_OPEN_RE.match(line)) and strict and _INDENTED_OPENER_RE.match(line):
            raise _unread(number, line, "a fence or comment opened by an indented line")
        if m:
            fence = m.group(1)
            continue
        if _COMMENT_OPEN_RE.match(line):
            rest = line[line.find("<!--") + 4:]
            in_comment = "-->" not in rest
            if strict and not in_comment:
                _refuse_remainder(number, line, rest.split("-->", 1)[1])
            continue
        yield number, _INLINE_COMMENT_RE.sub("", line)


def _unread(number, line, reason):
    return CannotEvaluate("{} line {} is not a shape this gate reads ({}): {!r}".format(
        CROSSWALK, number, reason, line))


def _refuse_remainder(number, line, remainder):
    if remainder.strip():
        raise _unread(number, line, "text after the \"-->\" that closes a comment")


def _refuse_indented(number, line):
    raise _unread(number, line, "an indented line that does not continue the item line or line above it")


def _read_text(number, line, text):
    """Refuses the text of a heading, item, continuation or prose line unless it is plain: it opens as
    _PLAIN_RE allows, holds no raw HTML outside a code span, and does not name an entry or a read field."""
    if not _PLAIN_RE.match(text):
        raise _unread(number, line, "text that opens with a mark a reader can render as structure")
    if _HTML_RE.search(_CODE_SPAN_RE.sub("", text)):
        raise _unread(number, line, "raw HTML")
    if _NAMES_RE.match(_FORMATTING_RE.sub("", text).lstrip()):
        raise _unread(number, line, "an entry or read field named outside an entry heading or field line")


def parse_crosswalk(text):
    """[(level, name, fields, parent)]: level is "obligation" or "split", fields a dict of the entry's
    "- Field: value" lines (a repeated field keeps every value), parent the enclosing obligation. Every
    visible line must be one of the closed list of shapes this gate reads (see _HEADING_RE); any other line
    raises CannotEvaluate. Any heading other than an entry heading ends the entry above it (a level 1 or 2
    heading also ends the obligation a split sits under), so a field below it attaches to no entry: a field
    this gate reads (READ_FIELDS) there, or before the first entry, is kept as a ("stray", field, {}, None)
    record for _check_stray to refuse. Prose and ordered items end no entry."""
    entries = []
    parent = None
    fields = None
    width = None
    for number, line in _visible_lines(text, strict=True):
        # width: the indent a line needs to continue the item or continuation line just above it.
        item, width = width, None
        if not line.strip():
            continue
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
            if m.group(2):
                _read_text(number, line, m.group(2))
            fields = None
            if len(m.group(1)) <= 2:
                parent = None
            continue
        m = _FIELD_RE.match(line)
        if m:
            if _HTML_RE.search(_CODE_SPAN_RE.sub("", m.group(2))):
                raise _unread(number, line, "raw HTML")
            field = m.group(1).strip().casefold()
            if fields is not None:
                fields.setdefault(field, []).append(m.group(2))
            elif field in READ_FIELDS:
                entries.append(("stray", field, {}, None))
            width = 2
            continue
        m = _ORDERED_RE.match(line)
        if m:
            _read_text(number, line, m.group(2))
            width = len(m.group(1))
            continue
        if line[0] in " \t":
            if item and line[:item] == " " * item and line[item] not in " \t":
                _read_text(number, line, line[item:])
                width = item
            else:
                _refuse_indented(number, line)
            continue
        _read_text(number, line, line)
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
    return ["stray: field {} sits under a heading that is not an entry, or before the first entry, so it "
            "belongs to no entry".format(e[1].capitalize())
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


def _ignore(*_args):
    return None


# Each rule a crosswalk line is refused under, with the module attribute and the stand-in that removes it.
_RULE_FLIPS = {
    "plain": ("_PLAIN_RE", re.compile("")),
    "html": ("_HTML_RE", re.compile(r"(?!)")),
    "names": ("_NAMES_RE", re.compile(r"(?!)")),
    "indent": ("_refuse_indented", _ignore),
    "control": ("_CONTROL_RE", re.compile(r"(?!)")),
    "remainder": ("_refuse_remainder", _ignore),
    "opener": ("_INDENTED_OPENER_RE", re.compile(r"(?!)")),
}

# Crosswalks holding a line outside the shapes this gate reads: (label, text, the rules that refuse it). Each
# cannot evaluate, and each passes (exit 0) with exactly those rules removed. An _owner_after vector passes
# because its Owner then attaches to the entry above; a _good(extra=...) vector because its lines read as prose.
_UNREAD_SHAPES = (
    ("setext-dash", _owner_after("Appendix\n---\n\n"), {"plain"}),
    ("setext-equals", _owner_after("Appendix\n===\n\n"), {"plain"}),
    ("thematic-break", _owner_after("***\n\n"), {"plain"}),
    ("html-heading", _owner_after("<h2>Appendix</h2>\n\n"), {"plain", "html"}),
    ("blockquote-heading", _owner_after("> ## Appendix\n\n"), {"plain"}),
    ("indented-code", _owner_after("    Appendix\n\n"), {"indent"}),
    ("indented-heading", _owner_after("   ## Appendix\n\n"), {"indent"}),
    ("indented-heading-in-split", _good().replace("### Split: move", "    ### Notes\n\n### Split: move"),
     {"indent"}),
    ("table-row", _owner_after("| Appendix |\n\n"), {"plain"}),
    ("table-delimiter-row", _owner_after("Name | Note\n--- | ---\n\n"), {"plain"}),
    ("table-row-field", _good(extra="| Note |\n| --- |\n| Owner: someone |\n"), {"plain"}),
    ("table-row-entry", _good(extra="| Note |\n| --- |\n| Obligation: coordination |\n"), {"plain"}),
    ("list-item-heading", _owner_after("- ## Appendix\n\n"), {"plain"}),
    ("list-item-html-heading", _owner_after("- <h2>Appendix</h2>\n\n"), {"plain", "html"}),
    ("list-item-blockquote", _owner_after("- > Appendix\n\n"), {"plain"}),
    ("list-item-entry-heading", _good(extra="- ## Obligation: coordination\n"), {"plain"}),
    ("continued-item-heading", _owner_after("- Note: x\n  ## Appendix\n\n"), {"plain"}),
    ("star-field", _good(extra="## Appendix\n\n* Owner: none yet.\n"), {"plain", "names"}),
    ("plus-field", _good(extra="## Appendix\n\n+ Owner: none yet.\n"), {"plain"}),
    ("ordered-field", _good(extra="## Appendix\n\n1. Owner: none yet.\n"), {"names"}),
    ("paren-ordered-field", _good(extra="## Appendix\n\n1) Owner: none yet.\n"), {"plain"}),
    ("tab-dash-field", _good(extra="## Appendix\n\n-\tOwner: none yet.\n"), {"plain"}),
    ("nested-dash-field", _good(extra="## Appendix\n\n  - Owner: none yet.\n"), {"indent"}),
    ("prose-field", _good(extra="Owner: none yet.\n"), {"names"}),
    ("raw-html-in-prose", _owner_after("A note <h2>Appendix</h2>\n\n"), {"html"}),
    ("raw-html-in-field", _good(extra="- Note: x <h2>Appendix</h2>\n"), {"html"}),
    ("comment-remainder", _owner_after("<!-- x --><h2>Appendix</h2>\n\n"), {"remainder"}),
    ("comment-remainder-closing-line", _owner_after("<!-- x\n--><h2>Appendix</h2>\n\n"), {"remainder"}),
    ("indented-fence-opener", _good(extra="- Note: x\n  ```\n## Obligation: coordination\n"), {"opener"}),
    ("indented-comment-opener", _good(extra="- Note: x\n  <!-- x\n## Obligation: coordination\n-->\n"),
     {"opener"}),
    ("crlf-setext", _owner_after("Appendix\r\n---\r\n\r\n"), {"control", "plain"}),
    ("crlf-bare-heading", _owner_after("##\r\n\r\n"), {"control", "plain"}),
    ("lone-carriage-return", _owner_after("A note\r## Appendix\n\n"), {"control"}),
    ("hash-prose", _with_line("publication", "#5 is tracked elsewhere."), {"plain"}),
    ("seven-hashes", _with_line("publication", "####### not a heading"), {"plain"}),
    ("entry-setext", _good(extra="Obligation: coordination\n===\n"), {"names", "plain"}),
    ("entry-setext-indented-continuation", _good(extra="Obligation: coordination\n    continued\n===\n"),
     {"names", "indent", "plain"}),
    ("entry-html", _good(extra="<h2>Obligation: coordination</h2>\n"), {"plain", "html"}),
    ("entry-blockquote", _good(extra="> ## Obligation: coordination\n"), {"plain"}),
    ("entry-indented-heading", _good(extra=" ## Obligation: coordination\n"), {"indent"}),
    ("entry-indented-code", _good(extra="    ## Obligation: coordination\n"), {"indent"}),
    ("entry-star-item", _good(extra="* Obligation: coordination\n"), {"plain", "names"}),
    ("entry-other-case", _good(extra="## obligation: coordination\n"), {"names"}),
    ("entry-emphasis", _good(extra="## **Obligation:** coordination\n"), {"plain", "names"}),
    ("entry-emphasis-setext", _good(extra="**Obligation:** coordination\n===\n"), {"plain", "names"}),
    ("entry-escaped", _good(extra="## Obligation\\: coordination\n"), {"names"}),
)

# Headings that end the entry above them: the Owner below each is a stray field (exit 1).
_ENDING_HEADINGS = (
    ("space-separator", "## Appendix\n\n"),
    ("tab-separator", "##\tAppendix\n\n"),
    ("bare", "##\n\n"),
    ("six-hashes", "###### Appendix\n\n"),
    ("level-1", "# Appendix\n\n"),
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
    # The same shape after each other heading: red, and green once the heading reads as prose.
    for label, heading in _ENDING_HEADINGS:
        text = _owner_after(heading)
        expect(_run(_files(crosswalk=text))[0] == 1, "stray/{}-heading-owner-fails-gate".format(label))
        with mock.patch.object(module, "_HEADING_RE", re.compile(r"(?!)")), \
                mock.patch.object(module, *_RULE_FLIPS["plain"]):
            expect(_findings_or_none(text, backlog) == [], "stray/{}-heading-green-as-prose".format(label))
    # Each line outside the shapes this gate reads cannot evaluate, and passes once the rules that refuse it are
    # removed; every rule is the only one refusing at least one vector.
    for label, text, rules in _UNREAD_SHAPES:
        code, _out, err = _run(_files(crosswalk=text))
        expect(code == 2 and " is not a shape this gate reads " in err, "unread/{}-cannot-evaluate".format(label))
        with contextlib.ExitStack() as stack:
            for rule in rules:
                stack.enter_context(mock.patch.object(module, *_RULE_FLIPS[rule]))
            expect(_run(_files(crosswalk=text))[0] == 0, "unread/{}-passes-without-its-rules".format(label))
    expect({min(r) for _label, _text, r in _UNREAD_SHAPES if len(r) == 1} == set(_RULE_FLIPS),
           "unread/every-rule-alone-refuses-a-vector")
    # A "<!--" inside a line hides no later line: the heading below it ends the entry, and an entry below it
    # counts. Both turn green or red the other way when any "<!--" opens a comment.
    inline_heading = _owner_after("A note <!-- x\n## Appendix -->\n")
    inline_entry = _good(drop="git staging", extra="A note <!-- x\n\n" + _entry("git staging"))
    expect(_run(_files(crosswalk=inline_heading))[0] == 1, "inline-comment/heading-below-ends-entry")
    expect(_findings_or_none(inline_entry, backlog) == [], "inline-comment/entry-below-counts")
    with mock.patch.object(module, "_COMMENT_OPEN_RE", re.compile(r".*?<!--")):
        expect(_findings_or_none(inline_heading, backlog) == [], "inline-comment/heading-hidden-when-opener-anywhere")
        expect(bool(_findings_or_none(inline_entry, backlog)), "inline-comment/entry-hidden-when-opener-anywhere")
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
    fixtures += [("stray-after-" + label + "-heading", _good(extra=heading + "- Successor: OPF-ADOPT-ORCH\n"), "stray")
                 for label, heading in _ENDING_HEADINGS]
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
