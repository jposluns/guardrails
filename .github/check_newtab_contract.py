#!/usr/bin/env python3
"""Pin this repository's parsed NEWTAB declaration, outside the portable gate.

This guards declaration drift, not environment overrides or edits to this assertion.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# Import the shared helpers WITHOUT placing this script's own directory ahead of the stdlib (the
# check_internal_names idiom): append keeps stdlib precedence under `python3 -I`.
sys.path.append(str(REPO / "tools"))
from _gen_common import load_toml, precheck_special_files  # noqa: E402  D-400-SPECIAL-FILE-PRECHECK


def main():
    # D-400-SPECIAL-FILE-PRECHECK: refuse a tree holding a special file or hostile symlink before any
    # read; load_toml's non-blocking reader then refuses a FIFO at the declaration itself as defence in
    # depth, so this gate can never block on a plain read.
    declaration = precheck_special_files(REPO) / ".aiqt/newtab.toml"
    try:
        actual = load_toml(declaration)
    except (OSError, ValueError) as exc:
        print("error: cannot read NEWTAB repository declaration: {}".format(exc), file=sys.stderr)
        return 2
    expected = dict(roots=["site", "opf/site"])
    if actual != expected:
        print("FAIL: NEWTAB repository declaration: expected {!r}, got {!r}".format(
            expected, actual), file=sys.stderr)
        return 1
    print("PASS: NEWTAB repository declaration equals {!r}".format(expected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
