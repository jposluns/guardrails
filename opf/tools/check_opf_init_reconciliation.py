"""Structural gate for the OPF init later-slice obligation crosswalk (opf/spec/OPF-INIT-RECONCILIATION.md).

The opf-init-p0 gate residue in .aiqt/core/gates/manifest.toml lists the later OPF-INIT obligations it leaves
out of scope. This gate reads that list from the residue, requires it to equal the frozen roster below, and
then parses the crosswalk: every residue obligation has exactly one "## Obligation:" entry, no entry names an
obligation outside the roster, the Migrate/Move entry keeps each of its three "### Split:" children exactly
once, and an entry whose Disposition is "transferred" names a Successor. It checks STRUCTURE only: it never
reads a cited symbol, so a green run says nothing about whether any obligation is implemented.

Exit 0 clean; exit 1 on a concrete finding; exit 2 (cannot evaluate) when the manifest or the crosswalk is
missing, unreadable or unparseable, or the residue no longer has the shape this gate reads. The --self-test is source-free
(in-memory fixtures only), so it also runs in a standalone opf/ copy with no manifest beside it; each fixture
must be red with every check active and green with only its own check removed.
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
NO_SUCCESSOR = ("", "none")

_OBLIGATION_RE = re.compile(r"^## Obligation: (.+?)\s*$")
_SPLIT_RE = re.compile(r"^### Split: (.+?)\s*$")
_FIELD_RE = re.compile(r"^- ([A-Za-z ]+): ?(.*?)\s*$")


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


def residue_obligations(manifest_text):
    """The obligation names the opf-init-p0 residue lists, normalized and in order."""
    doc = _parse_manifest(manifest_text)
    gates =[g for g in doc.get("gate", []) if isinstance(g, dict) and g.get("id") == RESIDUE_GATE]
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


def parse_crosswalk(text):
    """[(level, name, fields, parent)]: level is "obligation" or "split", fields a dict of the entry's
    "- Field: value" lines (a repeated field keeps every value), parent the enclosing obligation."""
    entries = []
    parent = None
    for line in text.split("\n"):
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


def _check_missing(entries, roster):
    names = [e[1] for e in entries if e[0] == "obligation"]
    return ["missing: residue obligation {!r} has no entry".format(n) for n in roster if n not in names]


def _check_duplicate(entries, roster):
    names = [e[1] for e in entries if e[0] == "obligation"]
    return ["duplicate: obligation {!r} has {} entries".format(n, names.count(n))
            for n in sorted(set(names)) if names.count(n) > 1]


def _check_unknown(entries, roster):
    return ["unknown: obligation {!r} is not in the residue".format(e[1])
            for e in entries if e[0] == "obligation" and e[1] not in roster]


def _check_split(entries, roster):
    children = [e[1] for e in entries if e[0] == "split" and e[3] == SPLIT_PARENT]
    found = ["split-child-missing: {!r} has no {!r} split".format(SPLIT_PARENT, c)
             for c in SPLIT_CHILDREN if c not in children]
    found += ["split-child-duplicate: {!r} split {!r} appears {} times".format(SPLIT_PARENT, c, children.count(c))
              for c in SPLIT_CHILDREN if children.count(c) > 1]
    found += ["split-unknown: split {!r} under {!r} is not a declared child".format(e[1], e[3])
              for e in entries if e[0] == "split" and (e[3] != SPLIT_PARENT or e[1] not in SPLIT_CHILDREN)]
    return found


def _check_disposition(entries, roster):
    found = []
    for level, name, fields, _parent in entries:
        values = fields.get("disposition", [])
        if len(values) != 1 or values[0] not in DISPOSITIONS:
            found.append("disposition: {} {!r} needs exactly one Disposition from {}".format(
                level, name, "/".join(DISPOSITIONS)))
    return found


def _check_successor(entries, roster):
    found = []
    for level, name, fields, _parent in entries:
        if fields.get("disposition") == ["transferred"]:
            successors = fields.get("successor", [])
            if len(successors) != 1 or successors[0].strip().casefold() in NO_SUCCESSOR:
                found.append("transferred-without-successor: {} {!r} is transferred but names no "
                             "Successor".format(level, name))
    return found


CHECKS = {
    "missing": _check_missing,
    "duplicate": _check_duplicate,
    "unknown": _check_unknown,
    "split": _check_split,
    "disposition": _check_disposition,
    "successor": _check_successor,
}


def findings(text, roster=OBLIGATIONS, disabled=frozenset()):
    entries = parse_crosswalk(text)
    out = []
    for name, check in CHECKS.items():
        if name not in disabled:
            out.extend(check(entries, roster))
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
    except CannotEvaluate as exc:
        sys.stderr.write("CANNOT EVALUATE check_opf_init_reconciliation: {}\n".format(exc))
        return 2
    found = []
    if tuple(listed) != OBLIGATIONS:
        found.append("residue drift: {} lists {!r}, the frozen roster is {!r}".format(
            MANIFEST, listed, list(OBLIGATIONS)))
    found.extend(findings(text))
    for line in found:
        sys.stdout.write("FAIL check_opf_init_reconciliation: {}\n".format(line))
    if found:
        return 1
    sys.stdout.write("PASS check_opf_init_reconciliation: {} obligations, {} split children\n".format(
        len(OBLIGATIONS), len(SPLIT_CHILDREN)))
    return 0


# --- self-test ---------------------------------------------------------------------------------------

def _entry(name, disposition="open", successor="none", header="## Obligation: "):
    return "{}{}\n\n- Disposition: {}\n- Successor: {}\n\n".format(header, name, disposition, successor)


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

# Two unparseable manifests: one tomllib refuses with RecursionError, one with TOMLDecodeError.
_DEEP_MANIFEST = "deep = " + "[" * 5000 + "]" * 5000 + "\n"
_INVALID_MANIFEST = "[[gate]\n"


def _unhandled_parse(manifest_text):
    """The manifest parse with its handler removed: the flip each unparseable-manifest vector turns red under."""
    return tomllib.loads(manifest_text)


def _gate_cannot_evaluate(manifest_text):
    """True when run_gate over this manifest text and a clean crosswalk exits 2 with the named parse message;
    an exception that escapes run_gate is False."""
    texts = {MANIFEST: manifest_text, CROSSWALK: _good()}
    err = io.StringIO()
    try:
        with mock.patch.object(sys.modules[__name__], "_read", texts.__getitem__), \
                contextlib.redirect_stderr(err):
            code = run_gate()
    except Exception:
        return False
    return code == 2 and err.getvalue().startswith(
        "CANNOT EVALUATE check_opf_init_reconciliation: gates manifest does not parse: ")


def self_test():
    failures = []
    total = 0

    def expect(cond, label):
        nonlocal total
        total += 1
        if not cond:
            failures.append(label)

    expect(residue_obligations(_MANIFEST_FIXTURE) == list(OBLIGATIONS), "residue/parse-roster")
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
    for bad, label in ((_DEEP_MANIFEST, "deeply-nested"), (_INVALID_MANIFEST, "invalid")):
        expect(_gate_cannot_evaluate(bad), "unparseable-manifest/{}".format(label))
        with mock.patch.object(sys.modules[__name__], "_parse_manifest", _unhandled_parse):
            expect(not _gate_cannot_evaluate(bad), "unparseable-manifest/{}-red-without-handler".format(label))

    expect(findings(_good()) == [], "clean/no-findings")
    expect(findings(_good(override={"verb wiring": _entry("verb wiring", "transferred", "a named successor")})) == [],
           "clean/transferred-with-successor")

    # Each fixture: (label, crosswalk text, the one check that must hold it red).
    fixtures = [("missing", _good(drop="git staging"), "missing")]
    fixtures.append(("duplicate", _good(extra=_entry("coordination")), "duplicate"))
    fixtures.append(("unknown", _good(extra=_entry("store upgrade")), "unknown"))
    fixtures += [("split-missing-" + c.replace(" ", "-"),
                  _good(children=tuple(x for x in SPLIT_CHILDREN if x != c)), "split") for c in SPLIT_CHILDREN]
    fixtures.append(("split-duplicate", _good(children=SPLIT_CHILDREN + (SPLIT_CHILDREN[0],)), "split"))
    fixtures.append(("disposition-absent", _good(override={"publication": "## Obligation: publication\n\n"}),
                     "disposition"))
    for successor in ("none", "", "None"):
        fixtures.append(("transferred-successor-" + (successor or "empty"),
                         _good(override={"finalization": _entry("finalization", "transferred", successor)}),
                         "successor"))
    fixtures.append(("transferred-split-child", _good().replace(
        _entry("move disposition", header="### Split: "),
        _entry("move disposition", "transferred", "none", header="### Split: ")), "successor"))
    for label, text, check in fixtures:
        red = findings(text)
        expect(bool(red), "red/{}".format(label))
        expect(findings(text, disabled=frozenset({check})) == [], "check-removed-green/{}".format(label))
    expect({f[2] for f in fixtures} == set(CHECKS), "every-check-has-a-fixture")

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
