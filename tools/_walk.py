#!/usr/bin/env python3
"""Shared fail-closed tree walk for the repo-wide scanners (secrets, leaks, dashes, links, site).

Uses os.walk(onerror=raise), NOT Path.rglob: rglob SILENTLY yields nothing on an existing-but-unlistable
directory (it suppresses the traversal OSError), so a scanner would skip an unreadable subtree and still
report clean, a fail-open a security gate must never have. os.walk with a raising onerror surfaces the
read error so the caller can fail closed (exit 2). Skip-dirs are pruned in place, so the walk never
descends into (or fails on) .git/node_modules/__pycache__ etc.
"""
import io
import os
import stat
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


def read_text_nonblocking(path, encoding="utf-8"):
    """path.read_text(encoding=...), through ONE O_NONBLOCK descriptor: the open of a FIFO with no
    writer returns at once instead of blocking, and a descriptor that fstat says is not a regular file
    is refused as OSError, so a scanner that reads WHATEVER its walk meets (including a git-ignored
    path, which the D-400 special-file precheck deliberately does not walk) can never block forever on
    a special file; it fails closed loudly instead. A symlink is followed exactly as read_text follows
    it, bytes and decode behaviour (strict utf-8, universal newlines) are read_text's, and on a regular
    file O_NONBLOCK is a no-op, so the result is byte-identical to path.read_text(encoding=...)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
                     | getattr(os, "O_BINARY", 0))
    except ValueError as exc:  # a path no path call accepts (an embedded NUL): the callers' OSError arm
        raise OSError("{}: refused, not a usable path ({})".format(path, exc)) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("{}: refused, not a regular file (a FIFO, device, socket, or directory); a "
                          "plain read of it could block forever".format(path))
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    return io.TextIOWrapper(io.BytesIO(b"".join(chunks)), encoding=encoding).read()
