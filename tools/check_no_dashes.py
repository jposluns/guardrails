#!/usr/bin/env python3
"""Fail on en dashes and em dashes in Markdown prose and the standards id-manifests.

The project's writing style forbids both; hyphens, commas, colons, semicolons,
and parentheses are the sanctioned substitutes. Markdown and the crosswalk
manifests under .aiqt/standards/ are scanned (the manifest titles are public
crosswalk text); this Python file is not, so it may name the characters in its
own source without flagging itself.
"""
import sys
from pathlib import Path

EN_DASH = "–"
EM_DASH = "—"
SKIP_DIRS = {".git", "node_modules", "__pycache__"}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = []
    paths = sorted(root.rglob("*.md"))
    std_dir = root / ".aiqt" / "standards"
    if std_dir.is_dir():
        paths += sorted(std_dir.glob("*.toml"))
    for path in paths:
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            print(f"SKIP (not utf-8): {path.relative_to(root)}")
            continue
        for number, line in enumerate(lines, 1):
            for char, name in ((EN_DASH, "en dash"), (EM_DASH, "em dash")):
                if char in line:
                    column = line.index(char) + 1
                    findings.append(
                        f"{path.relative_to(root)}:{number}:{column}: {name}"
                    )
    if findings:
        print(f"FAIL: {len(findings)} forbidden dash character(s) found")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print("PASS: no en dashes or em dashes in Markdown or standards manifests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
