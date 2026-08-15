#!/usr/bin/env python3
"""Generate the user-facing public CHANGELOG.md from changelog.toml.

The public changelog carries ONLY user-facing release notes; the detailed per-change record is private.
  gen_changelog.py           regenerate CHANGELOG.md
  gen_changelog.py --check    fail (exit 1) if out of date; exit 2 on error
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml, reconcile  # noqa: E402


def render_md(data):
    lines = ["# " + data["title"], "", data["note"], ""]
    for rel in data["release"]:
        head = rel["title"]
        if rel.get("date"):
            head += " ({})".format(rel["date"])
        lines.append("## " + head)
        lines.append("")
        for item in rel.get("items", []):
            lines.append("- " + item)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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
    except (KeyError, TypeError) as exc:
        print("error: changelog.toml is missing or misuses a key: {}".format(exc))
        return 2
    if reconcile(root / "CHANGELOG.md", md, check):
        print("drift: CHANGELOG.md")
        if check:
            print("run tools/gen_changelog.py to regenerate")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
