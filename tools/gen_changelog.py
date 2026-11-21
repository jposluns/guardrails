#!/usr/bin/env python3
"""Generate the user-facing public CHANGELOG.md and the single-source VERSION file from changelog.toml.

The public changelog carries ONLY user-facing release notes; the detailed per-change record is private.
changelog.toml is the SINGLE SOURCE OF TRUTH for the pack's SemVer: each [[release]] carries a `version`,
and the root VERSION file is GENERATED here, so the version can never live in two hand-maintained places.
Releases are append-only (array order oldest to newest); the latest release is the last table in the array.

Between release cuts changelog.toml also carries an optional [unreleased] table: `version` (the planned next
release, a bare X.Y.Z), an optional `title` and optional `items`. While it is present, VERSION is that
version plus the fixed `-dev` suffix (for example `1.1.1-dev`) and CHANGELOG.md gains an "Unreleased"
section after the newest release. Without it (a cut or tag tree), VERSION is the latest release's version.
The release cut folds [unreleased] into a new last [[release]] and deletes it (RELEASING.md step 1).
  gen_changelog.py           regenerate CHANGELOG.md and VERSION
  gen_changelog.py --check    fail (exit 1) if either is out of date; exit 2 on error
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_changelog.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# opf/tools/ carries the ONE shared version grammar (_semver): the -dev suffix and the [unreleased] shape check.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "opf" / "tools"))
from opf_render import run_generator, FileTarget  # noqa: E402
from _semver import DEV_SUFFIX, unreleased_problem  # noqa: E402

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
GENSRC_OUTPUTS = (
    {"target": "CHANGELOG.md", "kind": "file",
     "sources": ("changelog.toml",), "regenerate": "python3 tools/gen_changelog.py"},
    {"target": "VERSION", "kind": "file",
     "sources": ("changelog.toml",), "regenerate": "python3 tools/gen_changelog.py"},
)


def unreleased(data):
    """The [unreleased] table, or None when changelog.toml has none. A malformed table raises TypeError
    (the generator's schema error, exit 2), so a bad declaration never reaches VERSION or CHANGELOG.md."""
    if "unreleased" not in data:
        return None
    problem = unreleased_problem(data["unreleased"])
    if problem is not None:
        raise TypeError("changelog.toml " + problem)
    return data["unreleased"]


def render_md(data):
    lines = ["# " + data["title"], "", data["note"], ""]
    for rel in data["release"]:
        head = rel["title"]
        if rel.get("version"):
            head = "{}: {}".format(rel["version"], head)
        if rel.get("date"):
            head += " ({})".format(rel["date"])
        lines.append("## " + head)
        lines.append("")
        for item in rel.get("items", []):
            lines.append("- " + item)
        lines.append("")
    # The work collected toward the next release, after the newest release (the file reads oldest to newest).
    # It is labelled as unreleased, never as a published version.
    table = unreleased(data)
    if table is not None:
        head = "Unreleased (toward {})".format(table["version"])
        if table.get("title"):
            head += ": " + table["title"]
        while lines and lines[-1] == "":
            lines.pop()  # exactly one blank line before the section, even after a release with no items
        lines += ["", "## " + head, ""]
        for item in table.get("items", []):
            lines.append("- " + item)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def latest_version(data):
    """The version of the latest release (the last table in the append-only array). Raises KeyError if
    the latest release has no version, so a release that omits the required version field fails closed at
    generation rather than emitting a blank VERSION."""
    return data["release"][-1]["version"]


def render_version(data):
    """VERSION: the [unreleased] version plus the fixed -dev suffix between cuts, else the latest release."""
    latest = latest_version(data)
    table = unreleased(data)
    if table is not None:
        return table["version"] + DEV_SUFFIX + "\n"
    return latest + "\n"


def main():
    return run_generator(
        sys.argv[1:],
        source="changelog.toml",
        targets=(
            FileTarget("CHANGELOG.md", render_md),
            FileTarget("VERSION", render_version),
        ),
        regen_hint="run tools/gen_changelog.py to regenerate",
        schema_excs=(KeyError, IndexError, TypeError),
        prog="gen_changelog.py",
    )


if __name__ == "__main__":
    sys.exit(main())
