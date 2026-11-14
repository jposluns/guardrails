#!/usr/bin/env python3
"""Shared fail-closed tree walk for the OPF tooling.

The OPF tree's own copy of the parent repository's tools/_walk.py, so an OPF tool can walk a tree
without importing from the parent tools/ directory once opf/ is its own repository. walk_files is the
same function, line for line; the parent copy is unchanged, and so is every parent caller's walk
(the parent leak gate gains only a self-test case that compares the OPF leak gate copy with it). This
copy adds the Python floor guard and a --self-test of its own.

Uses os.walk(onerror=raise), NOT Path.rglob: rglob SILENTLY yields nothing on an existing-but-unlistable
directory (it suppresses the traversal OSError), so a scanner would skip an unreadable subtree and still
report clean, a fail-open a security gate must never have. os.walk with a raising onerror surfaces the
read error so the caller can fail closed (exit 2). Skip-dirs are pruned in place, so the walk never
descends into (or fails on) .git/node_modules/__pycache__ etc.

  _walk.py --self-test   build throwaway trees and check the walk against them

The self-test holds: the files a clean walk yields (pruning, a skipped .git file, the suffix filter);
walk errors raised, never returned as an empty or partial result (a root that is a file, an error
while a directory is being listed); an absent and an unreadable directory each raised as its own
error (FileNotFoundError and PermissionError, neither an instance of the other), at the root and
below it, through a real chmod 000 directory and through a substituted os.scandir, so the unreadable
case is checked under uid 0 too, where a chmod 000 directory stays readable and that one real vector
is reported SKIPPED, never passed; a pruned unreadable directory never listed; directory symlinks
reported in symlink_dirs and never followed (a loop included), file and dangling symlinks yielded as
files and not reported, a pruned symlink neither reported nor yielded; and descriptor cleanup, the
set of this process's open descriptors unchanged after a full walk, a walk that raised while a
directory was open, and a walk closed or dropped after its first file. Descriptors are read from
/proc/self/fd (or /dev/fd); where neither exists those vectors are reported SKIPPED, never passed.

Exit: 0 every vector passed (a SKIPPED vector is named), 1 a vector failed, 2 the self-test could not
run (a usage error, or its throwaway tree could not be built).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: _walk.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import errno
import gc
import os
import shutil
import tempfile
from pathlib import Path


def walk_files(root, skip_dirs=frozenset(), suffixes=None, symlink_dirs=None):
    """Yield files under root (Path objects), fail-closed. Directories whose name is in skip_dirs are
    pruned (not descended), and a FILE whose name is in skip_dirs is skipped too (a git worktree's `.git`
    is a file, not a dir). suffixes, if given, keeps only files with those extensions (e.g. {".md"}).
    symlink_dirs, if given as a list, receives every directory SYMLINK the walk meets (os.walk never
    follows one: followlinks stays False), so a caller can name the subtree behind such a link instead
    of losing it silently; with the default None the behaviour is unchanged.
    Raises OSError if a directory that must be walked cannot be listed (caller converts to exit 2)."""
    def _raise(exc):
        raise exc
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, onerror=_raise):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        if symlink_dirs is not None:
            for d in dirnames:
                entry = Path(dirpath) / d
                if entry.is_symlink():
                    symlink_dirs.append(entry)
        for fn in filenames:
            # Also skip a FILE whose name is in skip_dirs: in a git worktree `.git` is a file (a pointer),
            # not a directory, and it is tool metadata that must not be scanned, exactly like the .git dir.
            if fn in skip_dirs:
                continue
            p = Path(dirpath) / fn
            if suffixes is None or p.suffix in suffixes:
                yield p


# ---------------------------------------------------------------------------------------------------
# Self-test. Everything below runs only under --self-test; walk_files above is the whole public API.
# ---------------------------------------------------------------------------------------------------

_SKIP = frozenset({".git", "node_modules", "__pycache__"})


class _SelfTestError(Exception):
    """The self-test's own tree could not be built: cannot evaluate (exit 2), never a pass."""


def _open_fds():
    """The names of this process's open descriptors, or None where the platform lists none."""
    for fd_dir in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(fd_dir):
            return frozenset(os.listdir(fd_dir))
    return None


def _rel(root, paths):
    return sorted(p.relative_to(root).as_posix() for p in paths)


def _outcome(thunk):
    """('ok', value) or ('raised', exception) for thunk(); only OSError counts as raised."""
    try:
        return ("ok", thunk())
    except OSError as exc:
        return ("raised", exc)


class _ScandirFault:
    """A stand-in for os.scandir: listing a directory in fail_at raises its error before any
    descriptor opens; listing one in break_after opens the real directory, yields its first entry,
    then raises OSError(EIO) with that real descriptor still open. Every other path is listed as is."""

    def __init__(self, real, fail_at=None, break_after=None):
        self.real = real
        self.fail_at = dict(fail_at or {})
        self.break_after = set(break_after or ())

    def __call__(self, path="."):
        key = os.fspath(path)
        if key in self.fail_at:
            code = self.fail_at[key]
            raise OSError(code, os.strerror(code), key)
        if key in self.break_after:
            return _BreakingIterator(self.real(path), key)
        return self.real(path)


class _BreakingIterator:
    def __init__(self, inner, key):
        self.inner = inner
        self.key = key
        self.given = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.inner.close()
        return False

    def __iter__(self):
        return self

    def __next__(self):
        if self.given >= 1:
            raise OSError(errno.EIO, os.strerror(errno.EIO), self.key)
        self.given += 1
        return next(self.inner)

    def close(self):
        self.inner.close()


def _with_scandir(stand_in, thunk):
    """thunk() with os.scandir replaced by stand_in (os.walk reads that module global on each call),
    the real os.scandir restored on every path."""
    real = os.scandir
    os.scandir = stand_in
    try:
        return _outcome(thunk)
    finally:
        os.scandir = real


def _build(base):
    """The fixture tree under base, and the files a clean walk with _SKIP and no suffix filter yields."""
    root = base / "tree"
    try:
        for d in ("a", "a/b", "c", "node_modules", "a/__pycache__", "linked"):
            (root / d).mkdir(parents=True)
        for f in ("top.md", "a/one.md", "a/two.txt", "a/b/three.md", "c/four.py", ".git",
                  "node_modules/hidden.md", "a/__pycache__/x.pyc", "linked/behind.md"):
            (root / f).write_text("x\n", encoding="utf-8")
        (root / "dir-link").symlink_to(root / "linked", target_is_directory=True)
        (root / "a" / "loop").symlink_to(root / "a", target_is_directory=True)
        (root / "file-link.md").symlink_to(root / "top.md")
        (root / "dangling").symlink_to(root / "no-such-target")
        (root / "node_modules" / "pruned-link").symlink_to(root / "linked", target_is_directory=True)
    except OSError as exc:
        raise _SelfTestError("cannot build the fixture tree under {}: {}".format(base, exc)) from exc
    expected = ["a/b/three.md", "a/one.md", "a/two.txt", "c/four.py", "dangling", "file-link.md",
                "linked/behind.md", "top.md"]
    return root, expected


def _run_vectors():
    """Run every vector; return 0 when all passed, 1 when any failed (2 is raised as _SelfTestError)."""
    failures = []
    skipped = []
    passed = 0

    def check(name, ok, detail=""):
        nonlocal passed
        if ok:
            passed += 1
            print("PASS walk/{}".format(name))
        else:
            failures.append(name)
            print("FAIL walk/{}{}".format(name, ": " + detail if detail else ""))

    def skip(name, why):
        skipped.append(name)
        print("SKIPPED walk/{} ({}); not passed".format(name, why))

    def raised(outcome, kind, code):
        return (outcome[0] == "raised" and isinstance(outcome[1], kind)
                and outcome[1].errno == code)

    def show(outcome):
        if outcome[0] == "raised":
            return "raised {}: {}".format(type(outcome[1]).__name__, outcome[1])
        return "returned {!r}".format(outcome[1])

    try:
        base = Path(tempfile.mkdtemp(prefix="opf-walk-selftest-"))
    except OSError as exc:
        raise _SelfTestError("cannot create a temporary directory: {}".format(exc)) from exc
    restore = []
    try:
        root, expected = _build(base)

        # --- the clean walk ---
        got = _rel(root, walk_files(root, skip_dirs=_SKIP))
        check("clean-tree", got == expected, "yielded {}".format(got))
        got = _rel(root, walk_files(root, skip_dirs=_SKIP, suffixes={".md"}))
        want = [p for p in expected if p.endswith(".md")]
        check("suffix-filter", got == want, "yielded {}".format(got))
        got = _rel(root / "a", walk_files(root / "a", skip_dirs=_SKIP))
        check("pruned-dir-not-descended", "__pycache__/x.pyc" not in got
              and got == ["b/three.md", "one.md", "two.txt"], "yielded {}".format(got))
        got = _rel(root, walk_files(root, skip_dirs=frozenset()))
        check("skip-file-named-git", ".git" not in _rel(root, walk_files(root, skip_dirs=_SKIP))
              and ".git" in got, "the unpruned walk yielded {}".format(got))

        # --- walk errors: raised, never an empty or partial result ---
        out = _outcome(lambda: list(walk_files(root / "top.md")))
        check("error-root-is-a-file", raised(out, NotADirectoryError, errno.ENOTDIR), show(out))
        out = _with_scandir(_ScandirFault(os.scandir, break_after={os.fspath(root / "c")}),
                            lambda: list(walk_files(root, skip_dirs=_SKIP)))
        check("error-while-listing-raised", out[0] == "raised" and out[1].errno == errno.EIO
              and not isinstance(out[1], (FileNotFoundError, PermissionError)), show(out))

        # --- an absent directory and an unreadable one, each its own error ---
        out_absent = _outcome(lambda: list(walk_files(root / "no-such-dir")))
        check("absent-root-raised", raised(out_absent, FileNotFoundError, errno.ENOENT)
              and not isinstance(out_absent[1], PermissionError), show(out_absent))
        sub = os.fspath(root / "a" / "b")
        out_gone = _with_scandir(_ScandirFault(os.scandir, fail_at={sub: errno.ENOENT}),
                                 lambda: list(walk_files(root, skip_dirs=_SKIP)))
        check("absent-subdir-raised", raised(out_gone, FileNotFoundError, errno.ENOENT)
              and not isinstance(out_gone[1], PermissionError), show(out_gone))
        out_denied = _with_scandir(_ScandirFault(os.scandir, fail_at={sub: errno.EACCES}),
                                   lambda: list(walk_files(root, skip_dirs=_SKIP)))
        check("unreadable-subdir-raised", raised(out_denied, PermissionError, errno.EACCES)
              and not isinstance(out_denied[1], FileNotFoundError), show(out_denied))
        out_root = _with_scandir(_ScandirFault(os.scandir, fail_at={os.fspath(root): errno.EACCES}),
                                 lambda: list(walk_files(root, skip_dirs=_SKIP)))
        check("unreadable-root-raised", raised(out_root, PermissionError, errno.EACCES)
              and not isinstance(out_root[1], FileNotFoundError), show(out_root))
        check("absent-and-unreadable-distinct",
              out_absent[0] == out_denied[0] == "raised"
              and type(out_absent[1]) is not type(out_denied[1])
              and out_absent[1].errno != out_denied[1].errno,
              "absent {}; unreadable {}".format(show(out_absent), show(out_denied)))

        locked = root / "c"
        os.chmod(locked, 0)
        restore.append(locked)
        if os.access(locked, os.R_OK):
            skip("unreadable-subdir-real", "a chmod 000 directory stays readable here (uid 0)")
            skip("unreadable-pruned-real", "a chmod 000 directory stays readable here (uid 0)")
        else:
            out = _outcome(lambda: list(walk_files(root, skip_dirs=_SKIP)))
            check("unreadable-subdir-real", raised(out, PermissionError, errno.EACCES), show(out))
            out = _outcome(lambda: _rel(root, walk_files(root, skip_dirs=_SKIP | {"c"})))
            check("unreadable-pruned-real",
                  out == ("ok", [p for p in expected if not p.startswith("c/")]), show(out))
        os.chmod(locked, 0o700)
        restore.remove(locked)

        # --- directory symlinks reported, never followed ---
        links = []
        got = _rel(root, walk_files(root, skip_dirs=_SKIP, symlink_dirs=links))
        check("symlink-dirs-reported", _rel(root, links) == ["a/loop", "dir-link"],
              "reported {}".format(_rel(root, links)))
        check("symlink-dirs-not-followed", got == expected, "yielded {}".format(got))
        check("symlink-default-unchanged",
              _rel(root, walk_files(root, skip_dirs=_SKIP)) == got)
        check("symlink-file-and-dangling-yielded-not-reported",
              "file-link.md" in got and "dangling" in got
              and not {"file-link.md", "dangling"} & set(_rel(root, links)))
        check("symlink-pruned-not-reported", "node_modules/pruned-link" not in _rel(root, links))
        links = []
        got = _rel(root / "a" / "loop",
                   walk_files(root / "a" / "loop", skip_dirs=_SKIP, symlink_dirs=links))
        check("symlink-root-walked-once", got == ["b/three.md", "one.md", "two.txt"]
              and _rel(root, links) == ["a/loop/loop"], "yielded {}, reported {}".format(
                  got, _rel(root, links)))

        # --- descriptor cleanup ---
        before = _open_fds()
        if before is None:
            for name in ("fds-full-walk", "fds-after-error", "fds-after-close", "fds-after-drop"):
                skip(name, "no /proc/self/fd or /dev/fd to list")
        else:
            list(walk_files(root, skip_dirs=_SKIP, symlink_dirs=[]))
            after = _open_fds()
            check("fds-full-walk", after == before, "left open {}".format(sorted(after - before)))
            _with_scandir(_ScandirFault(os.scandir, break_after={os.fspath(root / "a")}),
                          lambda: list(walk_files(root, skip_dirs=_SKIP)))
            after = _open_fds()
            check("fds-after-error", after == before, "left open {}".format(sorted(after - before)))
            closed_walk = walk_files(root, skip_dirs=_SKIP)
            next(closed_walk)
            closed_walk.close()
            after = _open_fds()
            check("fds-after-close", after == before, "left open {}".format(sorted(after - before)))
            dropped_walk = walk_files(root, skip_dirs=_SKIP)
            next(dropped_walk)
            del dropped_walk
            gc.collect()
            after = _open_fds()
            check("fds-after-drop", after == before, "left open {}".format(sorted(after - before)))
    finally:
        for path in restore:
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
        shutil.rmtree(base, ignore_errors=True)

    print("walk self-test: {} passed, {} failed, {} skipped{}".format(
        passed, len(failures), len(skipped),
        " (" + ", ".join(skipped) + ")" if skipped else ""))
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    return 0


def self_test():
    """Run every vector; return 0 when all passed, 1 when any failed, 2 when the self-test could not run."""
    try:
        return _run_vectors()
    except _SelfTestError as exc:
        sys.stderr.write("walk self-test cannot evaluate: {}\n".format(exc))
        return 2


def main(argv):
    if argv != ["--self-test"]:
        sys.stderr.write("usage: _walk.py --self-test (cannot evaluate: arguments {!r})\n".format(argv))
        return 2
    return self_test()


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main(sys.argv[1:]))
