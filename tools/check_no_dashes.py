#!/usr/bin/env python3
"""Fail on en dashes and em dashes in Markdown prose and the standards id-manifests.

The project's writing style forbids both; hyphens, commas, colons, semicolons,
and parentheses are the sanctioned substitutes. Markdown and the crosswalk
manifests under .aiqt/standards/ are scanned (the manifest titles are public
crosswalk text); this Python file is not, so it may name the characters in its
own source without flagging itself.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
from _standards import dir_present  # noqa: E402  fail-closed absence probe (raises on an unreadable parent)

EN_DASH = "–"
EM_DASH = "—"
SKIP_DIRS = {".git", "node_modules", "__pycache__"}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = []
    try:
        paths = sorted(walk_files(root, SKIP_DIRS, suffixes={".md", ".mdc"}))
        std_dir = root / ".aiqt" / "standards"
        if dir_present(std_dir):  # raises on an unreadable .aiqt parent -> caught below as exit 2
            paths += sorted(walk_files(std_dir, suffixes={".toml"}))
        # The generated root NOTICE and its attribution source are pack-authoring artefacts an adopter
        # may not vendor, so guard on existence: absent = skip, present = scan. os.stat raises EACCES on
        # an unreadable file (unlike exists()/is_file(), which swallow it), so a present-but-unreadable
        # file surfaces below as exit 2, fail-closed like the rest of this scan.
        for extra in (root / "NOTICE", root / ".aiqt" / "attribution.toml"):
            try:
                os.stat(extra)
            except FileNotFoundError:
                continue
            paths.append(extra)
        for path in paths:
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
    except OSError as exc:
        print(f"error: cannot scan the tree ({exc}); fail-closed", file=sys.stderr)
        return 2
    if findings:
        print(f"FAIL: {len(findings)} forbidden dash character(s) found")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print("PASS: no en dashes or em dashes in Markdown or standards manifests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
