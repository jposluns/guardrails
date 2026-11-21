#!/usr/bin/env python3
"""The bare-SemVer parser, the ONE shared source (OPF-SELF-CONTAIN).

This is the single implementation of the pack's `MAJOR.MINOR.PATCH` grammar (policy R5). It was extracted
from `check_versions._parse` so the OPF tooling under `opf/tools/` is dependency-closed (imports nothing
upward into `tools/`), while AIQT's `tools/check_versions.py` re-imports `_parse` (and `SEMVER`) from here,
keeping ONE source with no fork. Offline, stdlib only, pure.

It also holds the one tree-version grammar (`parse_tree_version`): between release cuts the root VERSION file
and the manifest release-version carry the planned next version with the fixed `-dev` suffix, `X.Y.Z-dev`,
and at a cut or tag the bare `X.Y.Z`. That grammar is closed: no counter, no other suffix, no build metadata.
Every other version (release rows, tags, declarations, opf-version, born-release) keeps the bare grammar.
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


# The fixed development suffix. Generators append it to the authored [unreleased] version; it is never
# authored, and it carries no counter, so every development build of one planned version shares one string.
DEV_SUFFIX = "-dev"
# The tree-version grammar: exactly a bare X.Y.Z, or a bare X.Y.Z followed by DEV_SUFFIX. Same ASCII digit
# class and re.ASCII flag as SEMVER; matched with fullmatch, so a trailing newline is refused.
TREE_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-dev)?$", re.ASCII)


def parse_tree_version(text):
    """Return ((major, minor, patch), is_dev) for exactly `X.Y.Z` or `X.Y.Z-dev`, else None.

    Anything else is None: a non-string, `-dev.0`, `-DEV`, `-rc1`, `+dev`, a leading zero, surrounding or
    trailing whitespace (a trailing newline included). The int() conversions are guarded as in _parse."""
    if type(text) is not str:
        return None
    m = TREE_VERSION.fullmatch(text)
    if m is None:
        return None
    try:
        parsed = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return parsed, m.group(4) is not None


def tree_precedence(parsed_tree_version):
    """The SemVer precedence key of a parse_tree_version result: X.Y.Z-dev sorts below X.Y.Z, and both
    below any greater X.Y.Z, so 1.0.5 < 1.1.1-dev < 1.1.1 < 1.1.2-dev."""
    triple, is_dev = parsed_tree_version
    return triple + (0 if is_dev else 1,)


# The optional changelog.toml [unreleased] table: the planned next version (bare X.Y.Z, required) plus an
# optional title and optional items, the release notes collected before the cut folds them into a release.
UNRELEASED_KEYS = frozenset({"version", "title", "items"})


def unreleased_problem(table):
    """Return why a changelog.toml [unreleased] table is malformed, or None when its shape is valid.

    Shape only: a table whose keys are a subset of version, title and items; version present and a bare
    X.Y.Z (the -dev suffix is generated, never authored); title, when present, a nonblank string; items,
    when present, an array of strings. Whether version is above the latest release is the caller's
    comparison, because callers report it with their own exit code."""
    if not isinstance(table, dict):
        return "[unreleased] must be a table"
    extra = sorted(str(key) for key in set(table) - UNRELEASED_KEYS)
    if extra:
        return "[unreleased] carries unsupported key(s) {} (allowed: items, title, version)".format(
            ", ".join(extra))
    if "version" not in table:
        return "[unreleased] has no version"
    value = table["version"]
    if not isinstance(value, str) or _parse(value) is None:
        return ("[unreleased] version {!r} is not a bare SemVer X.Y.Z (the {} suffix is generated, never "
                "authored)".format(value, DEV_SUFFIX))
    if "title" in table and not (isinstance(table["title"], str) and table["title"].strip()):
        return "[unreleased] title must be a nonblank string"
    if "items" in table and not (isinstance(table["items"], list)
                                 and all(isinstance(item, str) for item in table["items"])):
        return "[unreleased] items must be an array of strings"
    return None
