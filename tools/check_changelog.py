#!/usr/bin/env python3
"""Require a changelog entry on any change that touches tracked content.

Fails closed: if the diff cannot be determined, the gate blocks rather than
passes, because ambiguity is not approval.

Usage: check_changelog.py [base-ref]   (default: origin/main)
"""
import subprocess
import sys
from pathlib import Path

CHANGELOG = "CHANGELOG.md"
# Paths whose change does not require an entry.
EXEMPT_PREFIXES = ()


def run(args):
    return subprocess.run(args, capture_output=True, text=True)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"

    merge_base = run(["git", "-C", str(root), "merge-base", base, "HEAD"])
    if merge_base.returncode != 0:
        print(f"FAIL: cannot determine merge base against {base}")
        print(merge_base.stderr.strip())
        return 1
    ref = merge_base.stdout.strip()

    diff = run(["git", "-C", str(root), "diff", "--name-only", ref, "HEAD"])
    if diff.returncode != 0:
        print("FAIL: cannot determine the changed-file list")
        print(diff.stderr.strip())
        return 1

    changed = [p for p in diff.stdout.splitlines() if p.strip()]
    if not changed:
        print(f"PASS: no changes against {base}")
        return 0

    substantive = [
        p for p in changed if not p.startswith(EXEMPT_PREFIXES)
    ]
    if not substantive:
        print("PASS: only working-state files changed; no entry required")
        return 0

    if CHANGELOG in changed:
        print(f"PASS: {len(substantive)} tracked file(s) changed and "
              f"{CHANGELOG} carries an entry")
        return 0

    print(f"FAIL: {len(substantive)} tracked file(s) changed without a "
          f"{CHANGELOG} entry")
    for path in substantive[:20]:
        print(f"  {path}")
    if len(substantive) > 20:
        print(f"  ... and {len(substantive) - 20} more")
    print("\nEvery change carries an entry. A terse one-sentence entry is "
          "sufficient for ancillary changes; there is no skip path.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
