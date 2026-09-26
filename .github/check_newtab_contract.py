#!/usr/bin/env python3
"""Pin this repository's parsed NEWTAB declaration, outside the portable gate.

This guards declaration drift, not environment overrides or edits to this assertion.
"""
import sys
import tomllib
from pathlib import Path


def main():
    declaration = Path(__file__).resolve().parents[1] / ".aiqt/newtab.toml"
    try:
        actual = tomllib.loads(declaration.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("error: cannot read NEWTAB repository declaration: {}".format(exc), file=sys.stderr)
        return 2
    expected = {"roots": ["site", "opf/site"]}
    if actual != expected:
        print("FAIL: NEWTAB repository declaration: expected {!r}, got {!r}".format(
            expected, actual), file=sys.stderr)
        return 1
    print("PASS: NEWTAB repository declaration equals {!r}".format(expected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
