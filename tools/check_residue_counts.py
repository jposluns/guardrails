#!/usr/bin/env python3
"""Refuse a count phrase in a gate residue: a residue states what a gate checks, not how many checks it runs.

Run with: python3 -I -B tools/check_residue_counts.py [--root DIR]
          python3 -I -B tools/check_residue_counts.py --self-test
Exit: 0 clean; 1 a finding; 2 cannot evaluate.

The scan reads .aiqt/core/gates/manifest.toml, parses it as TOML, and reads the residue string of every
[[gate]] entry. A count phrase is a numeral written in ASCII digits (thousands may be grouped with commas)
followed by a check-type noun: check, vector, case, reversion, mutant, tree, row, flip or test, singular or
plural, in any letter case, either joined by a hyphen (a 121-mutant sweep) or after a space with at most one
word between them (45 trees, 4 red vectors). A numeral that follows a letter, digit, underscore, hash, colon,
period, hyphen or slash is a label or a version, not a count (#378, sig:15, step-2, 3.14), so it is not read.

ALLOWLIST below is the only exemption: one entry per residue, naming the gate id, the exact phrase the scan
matched in that residue and the reason that phrase states behaviour rather than a count (a boundary value
such as the row bound in 9999 and 10000 rows, or a permission mode that names a fixture). An entry exempts
that phrase in that residue only; the same phrase in another residue is still a finding, and an entry that
no longer matches a phrase in its residue, or names a gate the manifest does not hold, is a finding too.

Exit 1 lists each finding as the gate id and the phrase. Exit 2 (cannot evaluate) covers a manifest that
cannot be read, is not a regular file, is larger than MANIFEST_CAP bytes, is not UTF-8, does not parse as
TOML, holds no gate array of tables, or holds an entry whose id or residue is not a string; and an
unrecognized argument.

NOT CHECKED: a count written in words (two tests, seven probe-target mutants) or as a bare numeral with no
check-type noun after it (killed all 9, 26 of 26); a count with two or more words between the numeral and
the noun; a noun outside the list above (runs, suites, probes); and the residues of
.aiqt/core/hooks/manifest.toml, which describe hook controls rather than gate suites and are not read. The
scan matches the shape, not the meaning, so a numeral that is a count of something else but is followed by a
listed noun is a finding until it is reworded or carries an allowlist entry.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_residue_counts.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATES_MANIFEST_REL = ".aiqt/core/gates/manifest.toml"
# The gates manifest is a few hundred KiB; a file past this bound is not read (cannot evaluate).
MANIFEST_CAP = 16 * 1024 * 1024

NOUNS = ("check", "vector", "case", "reversion", "mutant", "tree", "row", "flip", "test")
_NOUN = r"(?:" + "|".join(NOUNS) + r")s?(?![A-Za-z0-9_])"
# A numeral not glued to a label or version, then a hyphen and the noun, or a space, at most one word and
# a space, and the noun. re.ASCII keeps \d to the ASCII digits.
COUNT_RE = re.compile(
    r"(?<![A-Za-z0-9_#:./-])\d+(?:,\d{3})*(?:-" + _NOUN + r"| (?:[A-Za-z][A-Za-z-]* )?" + _NOUN + r")",
    re.IGNORECASE | re.ASCII)

# (gate id, exact matched phrase, reason). Each reason says why the phrase states behaviour, not a count.
ALLOWLIST = (
    ("opf-release-history-selftest", "10000-row",
     "the inclusive row bound the history check enforces, a boundary value, not a number of checks"),
    ("opf-release-history-selftest", "10000 rows",
     "the accepted value at the row bound (9999 and 10000 rows valid, 10001 refused), a boundary value"),
    ("opf-adopt-observe-selftest", "0200 case",
     "0200 is the umask the fixture runs under, a permission mode naming the case, not a count"),
    ("opf-enforce-residuals", "000 vectors",
     "000 is the file mode the vectors set, a permission mode naming them, not a count"),
)


class CannotEvaluate(Exception):
    """The manifest cannot be read or does not have the shape the scan needs."""


def find_counts(text):
    """Every count phrase in text, in order, as the exact matched strings."""
    return [m.group(0) for m in COUNT_RE.finditer(text)]


def load_residues(path):
    """[(gate id, residue)] from the gates manifest at path; raises CannotEvaluate on any read or shape
    failure."""
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot be read ({})".format(path, type(exc).__name__))
    if not stat.S_ISREG(st.st_mode):
        raise CannotEvaluate("{}: not a regular file".format(path))
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MANIFEST_CAP + 1)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot be read ({})".format(path, type(exc).__name__))
    if len(raw) > MANIFEST_CAP:
        raise CannotEvaluate("{}: larger than {} bytes".format(path, MANIFEST_CAP))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CannotEvaluate("{}: not valid UTF-8".format(path))
    try:
        data = tomllib.loads(text)
    except RecursionError:
        raise CannotEvaluate("{}: nested too deeply to parse".format(path))
    except MemoryError:
        raise CannotEvaluate("{}: out of memory while parsing".format(path))
    except ValueError as exc:
        raise CannotEvaluate("{}: does not parse as TOML ({})".format(path, exc))
    gates = data.get("gate")
    if not isinstance(gates, list) or not gates:
        raise CannotEvaluate("{}: holds no [[gate]] array of tables".format(path))
    out = []
    for index, entry in enumerate(gates):
        if not isinstance(entry, dict):
            raise CannotEvaluate("{}: gate entry {} is not a table".format(path, index))
        gate_id = entry.get("id")
        residue = entry.get("residue")
        if not isinstance(gate_id, str) or not isinstance(residue, str):
            raise CannotEvaluate("{}: gate entry {} has no string id and residue".format(path, index))
        out.append((gate_id, residue))
    return out


def evaluate(residues, allowlist):
    """The findings for [(gate id, residue)] under allowlist, as a sorted list of message strings."""
    findings = []
    ids = {gate_id for gate_id, _ in residues}
    used = set()
    allowed = {(gate_id, phrase) for gate_id, phrase, _ in allowlist}
    for gate_id, residue in residues:
        for phrase in find_counts(residue):
            if (gate_id, phrase) in allowed:
                used.add((gate_id, phrase))
                continue
            findings.append("gate {}: residue carries the count phrase {!r}; state what the suite "
                            "checks instead".format(gate_id, phrase))
    for gate_id, phrase, reason in allowlist:
        if not isinstance(reason, str) or not reason.strip():
            findings.append("allowlist entry ({}, {!r}) gives no reason".format(gate_id, phrase))
        elif gate_id not in ids:
            findings.append("allowlist entry ({}, {!r}) names a gate the manifest does not hold"
                            .format(gate_id, phrase))
        elif (gate_id, phrase) not in used:
            findings.append("allowlist entry ({}, {!r}) matches no phrase in that residue (stale)"
                            .format(gate_id, phrase))
    return sorted(findings)


def run_check(path, allowlist):
    """(exit code, lines) for the manifest at path."""
    try:
        residues = load_residues(path)
    except CannotEvaluate as exc:
        return 2, ["CANNOT EVALUATE: {}".format(exc)]
    findings = evaluate(residues, allowlist)
    if findings:
        return 1, ["FAIL: " + item for item in findings]
    return 0, ["OK: no count phrase in the {} gate residues of {}".format(len(residues), path)]


def self_test():
    failures = []
    gate = "demo-gate"
    # Positive vectors: each must produce exactly the one phrase shown.
    positives = (
        ("A 121-mutant review-time sweep killed them.", "121-mutant"),
        ("the vector asserts at least 45 trees.", "45 trees"),
        ("It runs 12 checks.", "12 checks"),
        ("exactly 1 test runs", "1 test"),
        ("Further 4 red vectors: a doctor finding.", "4 red vectors"),
        ("7 reversions were shown", "7 reversions"),
        ("5 flips each", "5 flips"),
        ("2 rows refuse", "2 rows"),
        ("over 1,200 cases", "1,200 cases"),
        ("10 Tests pass", "10 Tests"),
        ("a 3-check suite", "3-check"),
        ("6 case-level vectors", "6 case-level vectors"),
        ("(9 flip)", "9 flip"),
        ("the 8-reversion pass", "8-reversion"),
        ("19 trees.", "19 trees"),
        ("a 30-row table", "30-row"),
    )
    for text, phrase in positives:
        got = find_counts(text)
        if got != [phrase]:
            failures.append("positive {!r}: expected [{!r}], got {!r}".format(text, phrase, got))
        if evaluate([(gate, text)], ()) == []:
            failures.append("positive {!r}: evaluate reported no finding".format(text))
    # Negative vectors: none may produce a phrase.
    negatives = (
        "check 5 (wiring) stays green",
        "exits 2 when it cannot evaluate",
        "the #378 close vectors",
        "the sig:15 abnormal vectors",
        "the step-2 revision",
        "Python 3.14 tests",
        "a 24-core host and 10 seconds",
        "at least 4 MiB",
        "two tests and seven mutants",
        "killed all 9, each by a vector",
        "5 rowsets and 3 testing hosts",
        "2 in a tree",
        "the a1-mutant sweep",
        "path v/2-row",
        "trees, rows and checks with no numeral",
    )
    for text in negatives:
        got = find_counts(text)
        if got:
            failures.append("negative {!r}: expected no phrase, got {!r}".format(text, got))
    # The allowlist exempts its phrase in its own residue only.
    allow = ((gate, "10000 rows", "the row bound, a boundary value"),)
    text = "9999 and 10000 rows valid, 10001 refused"
    if evaluate([(gate, text)], allow) != []:
        failures.append("allowlist: an exempted boundary phrase must not be a finding")
    if not evaluate([(gate, text), ("other-gate", "it holds 10000 rows")], allow):
        failures.append("allowlist: the phrase in another residue must still be a finding")
    if evaluate([(gate, "9999 and 10000 rows and 12 checks")], allow) == []:
        failures.append("allowlist: an unexempted count beside an exempted phrase must be a finding")
    if not evaluate([(gate, "no count here")], allow):
        failures.append("allowlist: a stale entry must be a finding")
    if not evaluate([(gate, text)], ((gate, "10000 rows", " "),)):
        failures.append("allowlist: an entry with a blank reason must be a finding")
    if not evaluate([("other-gate", "no count")], allow):
        failures.append("allowlist: an entry naming an absent gate must be a finding")
    # Whole runs over fixture manifests: clean, finding and every cannot-evaluate shape.
    with tempfile.TemporaryDirectory() as tmp:
        def fixture(name, data):
            target = os.path.join(tmp, name)
            with open(target, "wb") as handle:
                handle.write(data)
            return target

        clean = fixture("clean.toml", b'[[gate]]\nid = "a"\nresidue = "Checks the rows it reads."\n')
        dirty = fixture("dirty.toml", b'[[gate]]\nid = "a"\nresidue = "A 3-mutant sweep."\n')
        cases = (
            ("clean manifest", clean, 0),
            ("count in a residue", dirty, 1),
            ("missing manifest", os.path.join(tmp, "absent.toml"), 2),
            ("directory, not a file", tmp, 2),
            ("not UTF-8", fixture("latin.toml", b'[[gate]]\nid = "a"\nresidue = "caf\xe9"\n'), 2),
            ("not TOML", fixture("broken.toml", b'[[gate]\nid = "a"\n'), 2),
            ("nested too deeply", fixture("deep.toml", b"x = " + b"[" * 200000 + b"]" * 200000 + b"\n"), 2),
            ("no gate array", fixture("nogate.toml", b'[hook]\nid = "a"\n'), 2),
            ("empty gate array", fixture("emptygate.toml", b"gate = []\n"), 2),
            ("gate is a string", fixture("strgate.toml", b'gate = "a"\n'), 2),
            ("residue not a string", fixture("intres.toml", b'[[gate]]\nid = "a"\nresidue = 3\n'), 2),
            ("residue missing", fixture("nores.toml", b'[[gate]]\nid = "a"\n'), 2),
            ("entry not a table", fixture("notable.toml", b"gate = [1]\n"), 2),
        )
        for label, target, want in cases:
            code, lines = run_check(target, ())
            if code != want:
                failures.append("run {}: expected exit {}, got {} ({})".format(label, want, code, lines))
        link = os.path.join(tmp, "link.toml")
        try:
            os.symlink(clean, link)
        except OSError:
            link = None
        if link is not None:
            code, lines = run_check(link, ())
            if code != 2:
                failures.append("run symlink: expected exit 2, got {} ({})".format(code, lines))
    if failures:
        print("SELF-TEST FAIL:")
        for item in failures:
            print("  - " + item)
        return 1
    print("SELF-TEST OK: positive, negative, allowlist and cannot-evaluate vectors held")
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--self-test"]:
        return self_test()
    root = ROOT
    if len(args) == 2 and args[0] == "--root":
        root = Path(args[1])
    elif args:
        print("CANNOT EVALUATE: unrecognized arguments {!r}; only --self-test or --root DIR is accepted"
              .format(args))
        return 2
    code, lines = run_check(os.path.join(root, GATES_MANIFEST_REL), ALLOWLIST)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
