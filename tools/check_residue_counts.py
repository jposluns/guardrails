#!/usr/bin/env python3
"""Refuse a count phrase in a gate residue: a residue states what a gate checks, not how many checks it runs.

Run with: python3 -I -B tools/check_residue_counts.py [--root DIR]
          python3 -I -B tools/check_residue_counts.py --self-test
Exit: 0 clean; 1 a finding; 2 cannot evaluate.

The scan reads .aiqt/core/gates/manifest.toml, parses it as TOML, and reads the residue string of every
[[gate]] entry. A count phrase is a numeral written in ASCII digits (thousands may be grouped with commas)
followed by a check-type noun: check, vector, case, reversion, mutant, tree, row, flip or test, singular or
plural, in any letter case, either joined by a hyphen (a 121-mutant sweep) or after a space with at most one
word between them (45 trees, 4 red vectors). A numeral that follows an ASCII letter or digit, an underscore,
hash, colon, period, hyphen or slash is a label or a version, not a count (#378, sig:15, step-2, 3.14), so it
is not read. No match starts at a comma group of a grouped numeral either (the 200 of #1,200 or 3.1,200), so
a label written with grouped thousands (#1,200 tests, check 1,200 tests) is not read in part. A comma group
is exactly three digits: a numeral after a digit and a comma that is not three digits is read (1,2345 tests
reports 2345 tests, as rows,12 tests reports 12 tests). A numeral that follows the whole word check, case,
row or test and exactly one space is a label too (check 4 red vectors names the red vectors of check 4), so
it is not read either; the word may be in any letter case.

ALLOWLIST below is the only exemption. Each entry names the gate id, the exact phrase the scan matched in
that residue, how many occurrences of that phrase in that residue it exempts, and the reason that phrase
states behaviour rather than a count of checks run (a boundary value such as the row bound in 9999 and
10000 rows, a permission mode that names a fixture, or a threshold the suite asserts). The phrase is
compared whole, never as a substring, and the entry exempts exactly that many occurrences in that residue:
one more occurrence is a finding, one fewer makes the entry stale, which is a finding too, and the same
phrase in another residue is still a finding. An entry that names a gate the manifest does not hold, gives
no reason, gives a count that is not a positive int, or repeats the gate id and phrase of an earlier entry
is a finding. The count does not say which occurrence is the reasoned one: rewording that drops the reasoned
occurrence and adds the same phrase elsewhere in the same residue as a real count, keeping the total, passes.
An entry is keyed on the gate id, so two [[gate]] entries sharing an id would share its count; this scan does
not refuse a repeated id, since tools/gen_enforceability.py --check refuses that manifest (exit 2).

Exit 1 lists each finding as the gate id and the phrase. Exit 2 (cannot evaluate) covers a manifest that
cannot be read, is not a regular file, is larger than MANIFEST_CAP bytes, is not UTF-8, does not parse as
TOML, holds no gate array of tables, or holds an entry whose id or residue is not a string; and an
unrecognized argument.

NOT CHECKED: a count written in words (two tests, seven probe-target mutants, forty-five trees) or as a bare
numeral with no check-type noun after it (killed all 9, 26 of 26); a count with two or more words between
the numeral and the noun, or with a middle word that is not ASCII letters and hyphens alone (a parenthesis,
a comma, a digit or an accented letter in it); a noun outside the list above (runs, suites, probes,
assertions); a hyphenated range, whose first numeral is followed by a hyphen and a numeral rather than the
noun and whose second numeral follows a hyphen (1-2 tests, 3-5 vectors); spacing other than exactly one
ASCII space between the numeral, the middle word and the noun (two spaces, a tab, a line break or a no-break
space); digits other than the ASCII ones (Arabic-Indic or fullwidth digits); a numeral after the word check,
case, row or test, which is read as a label even where it is a count (test 12 checks), including after a
hyphenated compound that ends in one of those words, which is read as that word (re-test 4 cases, unit-test
12 cases); and the residues of .aiqt/core/hooks/manifest.toml, which describe hook controls rather than gate
suites and are not read. The scan matches the shape, not the meaning, so a numeral that is a count of
something else but is followed by a listed noun is a finding until it is reworded or carries an allowlist
entry.
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

import contextlib
import io
import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATES_MANIFEST_REL = ".aiqt/core/gates/manifest.toml"
# The gates manifest is a few hundred KiB; a file past this bound is not parsed (cannot evaluate).
MANIFEST_CAP = 16 * 1024 * 1024

NOUNS = ("check", "vector", "case", "reversion", "mutant", "tree", "row", "flip", "test")
_NOUN = r"(?:" + "|".join(NOUNS) + r")s?(?![A-Za-z0-9_])"
# Words after which a numeral is a label (check 4, case 3), not a count. One fixed-width lookbehind per
# word, since a lookbehind cannot hold alternatives of different widths; \b makes it the whole word.
LABEL_WORDS = ("check", "case", "row", "test")
_NOT_AFTER_LABEL_WORD = "".join(r"(?<!\b" + word + r" )" for word in LABEL_WORDS)
# No match starts at a comma group of a grouped numeral (the 200 of #1,200): a digit and a comma, then
# exactly three digits and no further digit. Every comma group of a grouped numeral has that shape, so a
# labelled grouped numeral is never read by its tail; a numeral of any other length after a digit and a
# comma (the 2345 of 1,2345) is not a comma group and is read.
_NOT_IN_GROUP = r"(?!(?<=\d,)\d{3}(?!\d))"
# A numeral not glued to a label or version, not inside a grouped numeral and not after a label word, then
# a hyphen and the noun, or a space, at most one word and a space, and the noun. re.ASCII keeps \d to the
# ASCII digits.
COUNT_RE = re.compile(
    r"(?<![A-Za-z0-9_#:./-])" + _NOT_IN_GROUP + _NOT_AFTER_LABEL_WORD + r"\d+(?:,\d{3})*(?:-" + _NOUN
    + r"| (?:[A-Za-z][A-Za-z-]* )?" + _NOUN + r")",
    re.IGNORECASE | re.ASCII)

# (gate id, exact matched phrase, occurrences exempted in that residue, reason). Each reason says why the
# phrase states behaviour, not a count of checks run.
ALLOWLIST = (
    ("opf-release-history-selftest", "10000-row", 1,
     "the inclusive row bound the history check enforces, a boundary value, not a number of checks"),
    ("opf-release-history-selftest", "10000 rows", 1,
     "the accepted value at the row bound (9999 and 10000 rows valid, 10001 refused), a boundary value"),
    ("opf-adopt-observe-selftest", "0200 case", 1,
     "0200 is the umask the fixture runs under, a permission mode naming the case, not a count"),
    ("opf-enforce-residuals", "000 vectors", 1,
     "000 is the file mode the vectors set, a permission mode naming them, not a count"),
    # The same residue's floors of 100 accepting and 100 refusing runs need no entry: runs is not a listed
    # noun, so the scan does not match them, and an entry for them would be stale.
    ("opf-adopt-complete-selftest", "45 trees", 1,
     "at least 45 trees is the floor the producer parity vector asserts (trees >= 45 in the "
     "check-2-rebuild-producer-parity check), a behavioural threshold the suite enforces on the trees "
     "it builds, not a number of checks run"),
)


class CannotEvaluate(Exception):
    """The manifest cannot be read or does not have the shape the scan needs."""


def find_counts(text):
    """Every count phrase in text, in order, as the exact matched strings."""
    return [m.group(0) for m in COUNT_RE.finditer(text)]


def load_residues(path, cap=MANIFEST_CAP):
    """[(gate id, residue)] from the gates manifest at path, parsed only when it holds at most cap bytes;
    raises CannotEvaluate on any read or shape failure."""
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot be read ({})".format(path, type(exc).__name__))
    if not stat.S_ISREG(st.st_mode):
        raise CannotEvaluate("{}: not a regular file".format(path))
    try:
        with open(path, "rb") as handle:
            raw = handle.read(cap + 1)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot be read ({})".format(path, type(exc).__name__))
    if len(raw) > cap:
        raise CannotEvaluate("{}: larger than {} bytes".format(path, cap))
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
    """The findings for [(gate id, residue)] under allowlist, as a sorted list of message strings.

    allowlist holds (gate id, phrase, occurrences, reason) entries. An entry exempts exactly that many
    occurrences of that exact phrase in that gate's residue; any further occurrence is a finding, and fewer
    occurrences than the entry names make it stale."""
    findings = []
    ids = {gate_id for gate_id, _ in residues}
    seen = {}
    for gate_id, residue in residues:
        for phrase in find_counts(residue):
            seen[(gate_id, phrase)] = seen.get((gate_id, phrase), 0) + 1
    exempt = {}
    named = set()
    for gate_id, phrase, count, reason in allowlist:
        key = (gate_id, phrase)
        if key in named:
            findings.append("allowlist entry ({}, {!r}) repeats an earlier entry".format(gate_id, phrase))
            continue
        named.add(key)
        if type(count) is not int or count < 1:
            findings.append("allowlist entry ({}, {!r}) gives the count {!r}, not a positive int"
                            .format(gate_id, phrase, count))
        elif not isinstance(reason, str) or not reason.strip():
            findings.append("allowlist entry ({}, {!r}) gives no reason".format(gate_id, phrase))
        elif gate_id not in ids:
            findings.append("allowlist entry ({}, {!r}) names a gate the manifest does not hold"
                            .format(gate_id, phrase))
        else:
            exempt[key] = count
            if seen.get(key, 0) < count:
                findings.append("allowlist entry ({}, {!r}) exempts a count of {} but that residue holds "
                                "{} (stale)".format(gate_id, phrase, count, seen.get(key, 0)))
    for (gate_id, phrase), found in seen.items():
        allowed = exempt.get((gate_id, phrase), 0)
        if found <= allowed:
            continue
        if allowed:
            findings.append("gate {}: residue carries the count phrase {!r} {} times and the allowlist "
                            "exempts {}; state what the suite checks instead"
                            .format(gate_id, phrase, found, allowed))
        else:
            findings.append("gate {}: residue carries the count phrase {!r}; state what the suite "
                            "checks instead".format(gate_id, phrase))
    return sorted(findings)


def run_check(path, allowlist, cap=MANIFEST_CAP):
    """(exit code, lines) for the manifest at path."""
    try:
        residues = load_residues(path, cap)
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
        # A label word only counts as a whole word: here check, test, case and row end longer words.
        ("precheck 4 tests", "4 tests"),
        ("subtest 2 cases", "2 cases"),
        ("showcase 3 tests", "3 tests"),
        ("narrow 2 checks", "2 checks"),
        # A grouped numeral is read whole; a numeral after a comma that is not a comma group is read.
        ("the 1,234,567 rows", "1,234,567 rows"),
        ("rows,12 tests", "12 tests"),
        ("1,2345 tests", "2345 tests"),
        ("1,23 tests", "23 tests"),
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
        # A numeral after the word check, case, row or test is a label, in any letter case.
        "Further check 4 red vectors: a doctor finding.",
        "Check 4 vectors",
        "case 3 tests",
        "row 2 checks",
        "test 7 cases",
        # A numeral after a hyphen or an underscore is a label, even with a noun after it.
        "the step-2 tests",
        "the x_2 tests",
        # Only ASCII digits are read (re.ASCII): Arabic-Indic and fullwidth digits are not.
        "١٢ checks",
        "１２ checks",
        # A labelled grouped numeral is not read by its tail: no match starts at a comma group.
        "#1,200 tests",
        "check 1,200 tests",
        "test 1,200 cases",
        "v1,200 checks",
        "step-1,200 tests",
        "sig:1,200 rows",
        "issue #12,345 vectors",
        "section 3.1,200 trees",
        "the #12,345,678 vectors",
        "the #1,200-row table",
    )
    for text in negatives:
        got = find_counts(text)
        if got:
            failures.append("negative {!r}: expected no phrase, got {!r}".format(text, got))
    # The allowlist exempts exactly the named number of occurrences of its exact phrase in its own
    # residue, and an entry that is stale, malformed or repeated is a finding.
    reason = "the row bound, a boundary value"
    allow = ((gate, "10000 rows", 1, reason),)
    allow_two = ((gate, "10000 rows", 2, reason),)
    text = "9999 and 10000 rows valid, 10001 refused"

    def count_finding(gate_id, phrase):
        return ("gate {}: residue carries the count phrase {!r}; state what the suite checks instead"
                .format(gate_id, phrase))

    def excess_finding(phrase, found, allowed):
        return ("gate {}: residue carries the count phrase {!r} {} times and the allowlist exempts {}; "
                "state what the suite checks instead".format(gate, phrase, found, allowed))

    def stale_finding(count, held):
        return ("allowlist entry ({}, '10000 rows') exempts a count of {} but that residue holds {} "
                "(stale)".format(gate, count, held))

    allow_cases = [
        ("an exempted boundary phrase at its count", [(gate, text)], allow, []),
        ("the phrase in another residue", [(gate, text), ("other-gate", "it holds 10000 rows")], allow,
         [count_finding("other-gate", "10000 rows")]),
        ("an unexempted count beside an exempted phrase", [(gate, "9999 and 10000 rows and 12 checks")],
         allow, [count_finding(gate, "12 checks")]),
        ("one occurrence over the count",
         [(gate, "9999 and 10000 rows VALID; the suite runs 10000 rows of checks")], allow,
         [excess_finding("10000 rows", 2, 1)]),
        ("one occurrence under the count", [(gate, "no count here")], allow, [stale_finding(1, 0)]),
        ("a count of two, at it", [(gate, "10000 rows and 10000 rows")], allow_two, []),
        ("a count of two, one under it", [(gate, text)], allow_two, [stale_finding(2, 1)]),
        ("a count of two, one over it", [(gate, "10000 rows, 10000 rows and 10000 rows")], allow_two,
         [excess_finding("10000 rows", 3, 2)]),
        ("a matched phrase that holds the entry's phrase",
         [(gate, "9999 and 10000 rows valid; 110000 rows refused")], allow,
         [count_finding(gate, "110000 rows")]),
        ("a matched phrase inside the entry's phrase", [(gate, "10000 rows valid and 0 rows refused")],
         allow, [count_finding(gate, "0 rows")]),
        ("a blank reason", [(gate, text)], ((gate, "10000 rows", 1, " "),),
         ["allowlist entry ({}, '10000 rows') gives no reason".format(gate),
          count_finding(gate, "10000 rows")]),
        ("an absent gate", [("other-gate", "no count")], allow,
         ["allowlist entry ({}, '10000 rows') names a gate the manifest does not hold".format(gate)]),
        ("a repeated entry", [(gate, text)], allow + allow,
         ["allowlist entry ({}, '10000 rows') repeats an earlier entry".format(gate)]),
    ]
    for bad in (0, -1, True, "1", 1.0, None):
        allow_cases.append((
            "the count {!r}".format(bad), [(gate, text)], ((gate, "10000 rows", bad, reason),),
            ["allowlist entry ({}, '10000 rows') gives the count {!r}, not a positive int".format(gate, bad),
             count_finding(gate, "10000 rows")]))
    for label, residues, entries, want in allow_cases:
        got = evaluate(residues, entries)
        if got != sorted(want):
            failures.append("allowlist {}: expected {!r}, got {!r}".format(label, sorted(want), got))
    # The command line accepts --self-test alone, or --root and one directory; anything else is
    # cannot-evaluate before any manifest is read.
    for argv in (["--bogus"], ["--root"], ["--self-test", "--root"], ["--root", "DIR", "extra"]):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        if code != 2 or not buffer.getvalue().startswith("CANNOT EVALUATE: unrecognized arguments"):
            failures.append("argv {!r}: expected exit 2 naming the unrecognized arguments, got {} ({!r})"
                            .format(argv, code, buffer.getvalue()))
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
        # The size cap: a manifest one byte past MANIFEST_CAP is not parsed, and at a cap set to the clean
        # fixture's size it is read exactly at the cap and one byte under it, and refused one byte over.
        with open(clean, "rb") as handle:
            clean_bytes = handle.read()
        pad = MANIFEST_CAP + 1 - len(clean_bytes) - 2
        oversized = fixture("oversized.toml", clean_bytes + b"#" + b"x" * pad + b"\n")
        cap_cases = (
            ("one byte over MANIFEST_CAP", oversized, MANIFEST_CAP, 2),
            ("one byte over the cap", clean, len(clean_bytes) - 1, 2),
            ("exactly at the cap", clean, len(clean_bytes), 0),
            ("one byte under the cap", clean, len(clean_bytes) + 1, 0),
        )
        for label, target, cap, want in cap_cases:
            code, lines = run_check(target, (), cap)
            if code != want:
                failures.append("size {}: expected exit {}, got {} ({})".format(label, want, code, lines))
        if os.path.getsize(oversized) != MANIFEST_CAP + 1:
            failures.append("size: the oversized fixture is not MANIFEST_CAP + 1 bytes")
    if failures:
        print("SELF-TEST FAIL:")
        for item in failures:
            print("  - " + item)
        return 1
    print("SELF-TEST OK: positive, negative, allowlist, argument, size-cap and cannot-evaluate vectors held")
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
