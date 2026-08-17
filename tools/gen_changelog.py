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
from _gen_common import repo_root, load_toml, reconcile  # noqa: E402


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


def main():
    check = "--check" in sys.argv[1:]
    root = repo_root()
    try:
        data = load_toml(root / "changelog.toml")
    except (OSError, ValueError) as exc:
        print("error: cannot read changelog.toml: {}".format(exc))
        return 2
    try:
        md = render_md(data)
        version_text = latest_version(data) + "\n"
    except (KeyError, IndexError, TypeError) as exc:
        print("error: changelog.toml is missing or misuses a key: {}".format(exc))
        return 2
    drift = False
    if reconcile(root / "CHANGELOG.md", md, check):
        print("drift: CHANGELOG.md"); drift = True
    if reconcile(root / "VERSION", version_text, check):
        print("drift: VERSION"); drift = True
    if check and drift:
        print("run tools/gen_changelog.py to regenerate")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
