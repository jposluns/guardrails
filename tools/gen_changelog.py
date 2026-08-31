#!/usr/bin/env python3
"""Generate the user-facing public CHANGELOG.md and the single-source VERSION file from changelog.toml.

The public changelog carries ONLY user-facing release notes; the detailed per-change record is private.
changelog.toml is the SINGLE SOURCE OF TRUTH for the pack's SemVer: each [[release]] carries a `version`,
and the root VERSION file is GENERATED here from the latest release, so the version can never live in two
hand-maintained places. Releases are append-only (array order oldest to newest); the latest release is
the last table in the array, and its version is what VERSION carries.
  gen_changelog.py           regenerate CHANGELOG.md and VERSION
  gen_changelog.py --check    fail (exit 1) if either is out of date; exit 2 on error
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from opf_render import run_generator, FileTarget  # noqa: E402

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
GENSRC_OUTPUTS = (
    {"target": "CHANGELOG.md", "kind": "file",
     "sources": ("changelog.toml",), "regenerate": "python3 tools/gen_changelog.py"},
    {"target": "VERSION", "kind": "file",
     "sources": ("changelog.toml",), "regenerate": "python3 tools/gen_changelog.py"},
)


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
    return "\n".join(lines).rstrip() + "\n"


def latest_version(data):
    """The pack SemVer VERSION carries: the version of the latest release (the last table in the
    append-only array). Raises KeyError if the latest release has no version, so a release that omits
    the required version field fails closed at generation rather than emitting a blank VERSION."""
    return data["release"][-1]["version"]


def render_version(data):
    return latest_version(data) + "\n"


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
    )


if __name__ == "__main__":
    sys.exit(main())
