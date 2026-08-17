#!/usr/bin/env python3
"""Fail on repository-internal Markdown links whose target does not exist.

Only relative links are checked. External URLs, mailto links, and pure anchors
are out of scope: this gate answers "does the path exist", not "is the URL live".
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
EXTERNAL = ("http://", "https://", "mailto:", "tel:", "#")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = []
    try:
        for path in sorted(walk_files(root, SKIP_DIRS, suffixes={".md"})):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # a Markdown file that is not utf-8 is a real docs-integrity finding, not a fail-closed
                # read error; keep it a finding (exit 1). An OSError (unreadable) fails closed below.
                findings.append(f"{path.relative_to(root)}: cannot read as utf-8 (UnicodeDecodeError)")
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
    except OSError as exc:
        print(f"error: cannot scan the tree ({exc}); fail-closed", file=sys.stderr)
        return 2
    if findings:
        print(f"FAIL: {len(findings)} broken internal link(s)")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print("PASS: all internal Markdown links resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
