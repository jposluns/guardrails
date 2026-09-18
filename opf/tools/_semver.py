#!/usr/bin/env python3
"""The bare-SemVer parser, the ONE shared source (OPF-SELF-CONTAIN).

This is the single implementation of the pack's `MAJOR.MINOR.PATCH` grammar (policy R5). It was extracted
from `check_versions._parse` so the OPF tooling under `opf/tools/` is dependency-closed (imports nothing
upward into `tools/`), while AIQT's `tools/check_versions.py` re-imports `_parse` (and `SEMVER`) from here,
keeping ONE source with no fork. Offline, stdlib only, pure.
"""
import re

# Bare SemVer only: no leading zeros, no pre-release/build identifiers (policy R5). The digit class is the
# explicit ASCII [0-9], never `\d`, and the pattern is compiled with re.ASCII (belt-and-suspenders): `\d`
# matches Unicode decimal digits, so `1٢.0.0` (an Arabic-Indic two) matched `[1-9]\d*` and int() then
# read it as 12, letting a non-ASCII-digit version pass every SemVer parse path. ASCII-only closes that gap.
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$", re.ASCII)


def _parse(version):
    """Return the (major, minor, patch) int tuple for a well-formed version, or None if malformed. Uses
    fullmatch, not match: the `$`-anchored SEMVER pattern otherwise accepts a trailing newline (Python's
    `$` matches before a final newline), so `_parse("1.0.0\\n")` was truthy. fullmatch requires the whole
    string to match, closing that trailing-whitespace acceptance. This helper feeds every version
    comparison the release gates make, so the tightening is load-bearing; no legitimate caller passes
    trailing whitespace.

    The int() conversions are guarded: a component within the SemVer grammar can still exceed CPython's
    integer-string-conversion digit limit (default 4300) and raise ValueError. An oversized component is
    malformed input, so it returns None (a cannot-evaluate that every caller already handles as a
    fail-closed malformation) rather than propagating a ValueError up through the release gates."""
    m = SEMVER.fullmatch(version)
    if m is None:
        return None
    try:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
