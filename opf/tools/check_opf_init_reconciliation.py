"""Structural gate for the OPF init later-slice obligation crosswalk (opf/spec/OPF-INIT-RECONCILIATION.md).

The opf-init-p0 gate residue in .aiqt/core/gates/manifest.toml lists the later OPF-INIT obligations it leaves
out of scope. This gate reads that list from the residue, requires it to equal the frozen roster below, and
then parses the crosswalk: every residue obligation has exactly one "## Obligation:" entry, no entry names an
obligation outside the roster, the Migrate/Move entry keeps each of its three "### Split:" children exactly
once, every entry carries an Owner and an Ownership, and an entry whose Disposition is "transferred" names as
its Successor a backlog id from the closed list in TODO.md at the repository root and an Owner other than
"none yet". Headings and fields inside a fenced code block or an HTML comment do not count. It checks
STRUCTURE only: it never reads a cited symbol, so a green run says nothing about whether any obligation is
implemented or whether a named successor accepted the work.

Exit 0 clean; exit 1 on a concrete finding (a crosswalk that parses to no usable entries is a finding, since
any text parses); exit 2 (cannot evaluate) when the manifest, the crosswalk or TODO.md is missing or is not
UTF-8, the manifest does not parse, the residue no longer has the shape this gate reads, or TODO.md lists no
backlog id (in a standalone opf/ copy TODO.md is absent, so the successor check cannot evaluate and the gate
never passes there). The --self-test is source-free (in-memory fixtures only), so it also runs in a standalone
opf/ copy with no manifest beside it; each fixture must be red with every check active and green with only its
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
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
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
    parts = [p.strip() for p in listed.split(",")]
    if parts and parts[-1].startswith("and "):
        parts[-1] = parts[-1][4:]
    names = [_norm(p) for p in parts]
    if not names or any(not n for n in names):
        raise CannotEvaluate("the {} residue obligation list is empty or malformed".format(RESIDUE_GATE))
    return names


def backlog_ids(todo_text):
    """The closed successor list: the first cell of each row of every TODO.md table headed "| ID |". A TODO.md
    that lists no id cannot evaluate, so an unreadable backlog never lets a transferred entry pass."""
    ids = set()
    in_table = False
    for line in todo_text.split("\n"):
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
    """The crosswalk's lines minus fenced code blocks and HTML comments, so a heading or field a reader does
    not see as one never counts. A fence closes on a line of the same character at least as long, and an
    unclosed fence hides the rest of the file. A comment hides from "<!--" through the whole line holding its
    closing "-->" (as a Markdown comment block does); text before "<!--" on its opening line stays."""
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
        if m:
            fence = m.group(1)
            continue
        start = line.find("<!--")
        if start >= 0:
            in_comment = "-->" not in line[start + 4:]
            line = line[:start]
        yield line


def parse_crosswalk(text):
    """[(level, name, fields, parent)]: level is "obligation" or "split", fields a dict of the entry's
    "- Field: value" lines (a repeated field keeps every value), parent the enclosing obligation."""
    entries = []
    parent = None
    for line in _visible_lines(text):
        m = _OBLIGATION_RE.match(line)
        if m:
            parent = _norm(m.group(1))
            entries.append(("obligation", parent, {}, None))
            continue
        m = _SPLIT_RE.match(line)
        if m:
            entries.append(("split", _norm(m.group(1)), {}, parent))
            continue
        if line.startswith("## ") or line.startswith("# "):
            parent = None
            continue
        m = _FIELD_RE.match(line)
        if m and entries:
            entries[-1][2].setdefault(m.group(1).strip().casefold(), []).append(m.group(2))
    return entries


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
    for level, name, fields, _parent in entries:
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
    for level, name, fields, _parent in entries:
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


CHECKS = {
    "missing": _check_missing,
    "duplicate": _check_duplicate,
    "unknown": _check_unknown,
    "split": _check_split,
    "disposition": _check_disposition,
    "successor": _check_successor,
    "owner": _check_owner,
    "transferred-owner": _check_transferred_owner,
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
    except CannotEvaluate as exc:
        sys.stderr.write("CANNOT EVALUATE check_opf_init_reconciliation: {}\n".format(exc))
        return 2
    found = []
    if tuple(listed) != OBLIGATIONS:
        found.append("residue drift: {} lists {!r}, the frozen roster is {!r}".format(
            MANIFEST, listed, list(OBLIGATIONS)))
    found.extend(findings(text, backlog))
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

    backlog = backlog_ids(_BACKLOG_FIXTURE)
    expect(backlog == {"OPF-ADOPT-ORCH", "OPF-IMPORT-COMPLETION"}, "backlog/ids-from-id-tables-only")
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

    # Each fixture: (label, crosswalk text, the one check that must hold it red).
    fixtures = [("missing", _good(drop="git staging"), "missing")]
    for hidden, label in (("```\n{}```\n", "backtick-fence"), ("~~~\n{}```\n", "unclosed-fence"),
                          ("<!--\n{}-->\n", "comment"), ("<!--\n{}", "unclosed-comment")):
        fixtures.append(("missing-hidden-" + label,
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
    for owner in ("none yet.", "None yet", "none"):
        fixtures.append(("transferred-owner-" + owner.replace(" ", "-"), _good(override={
            "finalization": _transferred("finalization", owner=owner)}), "transferred-owner"))
    for label, text, check in fixtures:
        red = findings(text, backlog)
        expect(bool(red), "red/{}".format(label))
        expect(findings(text, backlog, disabled=frozenset({check})) == [], "check-removed-green/{}".format(label))
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
    sys.exit(main())
