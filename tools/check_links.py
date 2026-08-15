#!/usr/bin/env python3
"""Fail on repository-internal Markdown links whose target does not exist.

Only relative links are checked. External URLs, mailto links, and pure anchors
are out of scope: this gate answers "does the path exist", not "is the URL live".
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
EXTERNAL = ("http://", "https://", "mailto:", "tel:", "#")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = []
    for path in sorted(root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            findings.append(f"{path.relative_to(root)}: cannot read as utf-8 ({exc.__class__.__name__})")
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for target in LINK.findall(line):
                target = target.split()[0].strip()
                if not target or target.startswith(EXTERNAL):
                    continue
                clean = unquote(target.split("#", 1)[0])
                if not clean:
                    continue
                resolved = (path.parent / clean).resolve()
                if not resolved.exists():
                    findings.append(
                        f"{path.relative_to(root)}:{number}: broken link -> {target}"
                    )
    if findings:
        print(f"FAIL: {len(findings)} broken internal link(s)")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print("PASS: all internal Markdown links resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
