#!/usr/bin/env python3
"""OPF record-name grammar and uniqueness checks (OPF-SPEC section 8.3, the clean-series `name` field):
pure, version-independent and INERT. No command, writer, view or doctor check calls this module yet; it
ships ahead of them, reads no store and writes nothing.

valid_name(value) returns True exactly when `value` is a str of 3 to 64 characters that matches
NAME_PATTERN (ASCII capital letters, digits and single hyphens, starting with a letter and ending with a
letter or digit) and does not match ID_SHAPE, the clean ID shape `<NS>-<digits>`. The patterns end in \\Z,
never $, so a trailing line ending is refused, and their classes are the explicit ranges A-Z and 0-9, so a
non-ASCII lookalike is refused; re.ASCII is set as well, though these classes need no flag to refuse one.
A name that breaks the grammar is refused, never corrected: nothing here changes its case, trims it or
rewrites it.

duplicate_names(records) takes a list of records (mappings), the active indexes and the record archive
together, and returns a sorted list of (name, ids) pairs, one for each name that more than one record
carries, with that name's record IDs sorted, so a clash is reported without choosing an owner. A record
with no `name` key is skipped. A `name` value that is not a str, or a record that is not a mapping, raises
TypeError rather than being skipped, so a malformed input is never read as clean.

NOT CHECKED HERE: the no-change rule against the prior snapshot, whether a record may carry a name at all
(imported records and worklog entries carry none), reading the store, and the archive's readability; the
doctor's C-NAMES check and the record writer own those once the tooling implements base 2.0.0.

Exit convention (the sibling OPF units): 0 clean, 1 a failed check, 2 cannot-evaluate.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _opf_names.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import re
from collections.abc import Mapping

NAME_MIN = 3
NAME_MAX = 64
NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*\Z", re.ASCII)
ID_SHAPE = re.compile(r"[A-Z]{2}-[0-9]+\Z", re.ASCII)


def valid_name(value):
    """True exactly when `value` is a well-formed record name (spec section 8.3); never raises."""
    if type(value) is not str:
        return False
    if not NAME_MIN <= len(value) <= NAME_MAX:
        return False
    if NAME_PATTERN.match(value) is None:
        return False
    return ID_SHAPE.match(value) is None


def duplicate_names(records):
    """Return [(name, [ids...]), ...] for every name more than one record carries, both levels sorted."""
    seen = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise TypeError("a record must be a mapping, not {}".format(type(record).__name__))
        if "name" not in record:
            continue
        name = record["name"]
        if type(name) is not str:
            raise TypeError("a record name must be a str, not {}".format(type(name).__name__))
        seen.setdefault(name, []).append(str(record.get("id")))
    return [(name, sorted(ids)) for name, ids in sorted(seen.items()) if len(ids) > 1]


def self_test():
    """Pure in-memory vectors: no file, process, network or clock. Each length bound is shown just inside,
    exactly at and just outside. Exit 0 all pass, 1 a failed check, 2 a harness error (fail-closed)."""
    results = []

    def check(name, ok):
        results.append((name, bool(ok)))

    try:
        check("names-constants-pinned", NAME_MIN == 3 and NAME_MAX == 64)
        # Length bounds: 2 is outside, 3 at the lower bound, 4 inside; 63 inside, 64 at the upper
        # bound, 65 outside.
        for length, want in ((2, False), (3, True), (4, True), (63, True), (64, True), (65, False)):
            check("names-length-{}".format(length), valid_name("A" * length) is want)
        check("names-length-64-hyphenated", valid_name("A" + "-B" * 31 + "C") is True
              and len("A" + "-B" * 31 + "C") == 64)
        check("names-length-65-hyphenated", valid_name("A" + "-B" * 32) is False
              and len("A" + "-B" * 32) == 65)
        # The ID shape is reserved; a three-letter prefix is not an ID shape.
        check("names-id-shape-md-7", valid_name("MD-7") is False)
        check("names-id-shape-md-07", valid_name("MD-07") is False)
        check("names-id-shape-md-0", valid_name("MD-0") is False)
        check("names-mdx-7-valid", valid_name("MDX-7") is True)
        check("names-md-7a-valid", valid_name("MD-7A") is True)
        # A digit after the first letter is allowed, and a digit in the prefix, a one-letter
        # prefix or a missing hyphen each falls outside the ID shape, so each of these is a name.
        check("names-first-segment-digit", valid_name("R2D2-X") is True)
        check("names-digit-prefix-not-id-shape", valid_name("A1-7") is True)
        check("names-one-letter-prefix-not-id-shape", valid_name("A-7") is True)
        check("names-no-hyphen-not-id-shape", valid_name("MD7") is True)
        check("names-example-valid", valid_name("INKWARM-DEFAULT") is True)
        # Hyphens: leading, trailing and doubled are refused.
        check("names-leading-hyphen", valid_name("-INKWARM") is False)
        check("names-trailing-hyphen", valid_name("INKWARM-") is False)
        check("names-doubled-hyphen", valid_name("INK--WARM") is False)
        # Must start with a letter.
        check("names-leading-digit", valid_name("7INKWARM") is False)
        # Case, line endings, lookalikes, colons and whitespace.
        check("names-lowercase", valid_name("inkwarm-default") is False)
        check("names-mixed-case", valid_name("Inkwarm-Default") is False)
        check("names-trailing-newline", valid_name("INKWARM-DEFAULT\n") is False)
        check("names-trailing-cr", valid_name("INKWARM-DEFAULT\r") is False)
        check("names-cyrillic-lookalike", valid_name("INKWАRM") is False)
        check("names-fullwidth-lookalike", valid_name("INKWARM－DEFAULT") is False)
        check("names-dotted-i-lookalike", valid_name("İNKWARM") is False)
        check("names-colon", valid_name("IMPORTED:INKWARM") is False)
        check("names-space", valid_name("INK WARM") is False)
        check("names-empty", valid_name("") is False)
        # Never corrected and never coerced: a non-str is simply not a name.
        check("names-non-str", not any(valid_name(v) for v in (None, 7, b"INKWARM", ["INKWARM"])))

        # Uniqueness over the active indexes and the archive together.
        records = [
            {"id": "MD-7", "name": "INKWARM-DEFAULT"},
            {"id": "BI-3", "name": "PAPER-ROLL"},
            {"id": "BI-9"},
            {"id": "DO-2", "name": "INKWARM-DEFAULT"},
            {"id": "BI-12", "name": "INKWARM-DEFAULT"},
        ]
        check("names-unique-clean", duplicate_names(records[:3]) == [])
        check("names-duplicate-across-types",
              duplicate_names(records) == [("INKWARM-DEFAULT", ["BI-12", "DO-2", "MD-7"])])
        check("names-unnamed-skipped", duplicate_names([{"id": "BI-1"}, {"id": "BI-2"}]) == [])
        check("names-empty-list", duplicate_names([]) == [])
        # Exact comparison: names differing only in case are distinct strings, not one name.
        check("names-case-distinct",
              duplicate_names([{"id": "BI-1", "name": "ABC"}, {"id": "BI-2", "name": "abc"}]) == [])
        for label, bad in (("non-mapping", ["BI-1"]), ("non-str-name", [{"id": "BI-1", "name": 7}])):
            try:
                duplicate_names(bad)
                refused = False
            except TypeError:
                refused = True
            check("names-refuses-" + label, refused)
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never an uncaught exit-1 escape
        print("OPF-NAMES SELF-TEST: harness error ({!r}); failing closed to exit 2".format(exc),
              file=sys.stderr)
        return 2
    failed = [name for name, ok in results if not ok]
    for name in failed:
        print("FAIL: " + name, file=sys.stderr)
    print("OPF-NAMES SELF-TEST: {} checks, {} failed".format(len(results), len(failed)))
    return 1 if failed else 0


def main():
    print("usage: _opf_names.py --self-test (a library module: valid_name(value), duplicate_names(records))",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
