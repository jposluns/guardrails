#!/usr/bin/env python3
"""Shared fail-closed tree walk for the repo-wide scanners (secrets, leaks, dashes, links, site).

Uses os.walk(onerror=raise), NOT Path.rglob: rglob SILENTLY yields nothing on an existing-but-unlistable
directory (it suppresses the traversal OSError), so a scanner would skip an unreadable subtree and still
report clean, a fail-open a security gate must never have. os.walk with a raising onerror surfaces the
read error so the caller can fail closed (exit 2). Skip-dirs are pruned in place, so the walk never
descends into (or fails on) .git/node_modules/__pycache__ etc.
"""
import os
from pathlib import Path


def walk_files(root, skip_dirs=frozenset(), suffixes=None):
    """Yield files under root (Path objects), fail-closed. Directories whose name is in skip_dirs are
    pruned (not descended), and a FILE whose name is in skip_dirs is skipped too (a git worktree's `.git`
    is a file, not a dir). suffixes, if given, keeps only files with those extensions (e.g. {".md"}).
    Raises OSError if a directory that must be walked cannot be listed (caller converts to exit 2)."""
    def _raise(exc):
        raise exc
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, onerror=_raise):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            # Also skip a FILE whose name is in skip_dirs: in a git worktree `.git` is a file (a pointer),
            # not a directory, and it is tool metadata that must not be scanned, exactly like the .git dir.
            if fn in skip_dirs:
                continue
            p = Path(dirpath) / fn
            if suffixes is None or p.suffix in suffixes:
                yield p
