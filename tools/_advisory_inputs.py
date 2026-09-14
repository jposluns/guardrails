"""Shared input collector for the WARN-only advisory detectors: best-effort heuristic advisory; the
authoritative behavior is the code and the --self-test.

It enumerates and reads the declared Python surfaces (direct *.py entries under required directories)
under no-follow directory descriptors, so a symlinked directory component or a non-regular selected entry
is refused as a located cannot-evaluate rather than followed. Each selected file is opened relative to the
retained directory descriptor and read from that same descriptor, so the path serves only as a diagnostic
label after acquisition. A missing primitive, a missing or unreadable component, a symlink, a wrong type,
a read failure, or an unsupported descriptor operation is turned into a located input issue; the caller
maps any issue to exit 2.

Potential limitations: concurrent directory membership changes, concurrent file-content changes, hard-link
provenance, mount topology, and namespace changes. Revision-bound review requires a separately established
immutable snapshot.
"""
import os
import stat


class SourceInput:
    # Input collection
    __slots__ = ("relpath", "raw")

    def __init__(self, relpath, raw):
        self.relpath = relpath
        self.raw = raw


class InputIssue:
    # Diagnostics
    __slots__ = ("relpath", "phase", "detail")

    def __init__(self, relpath, phase, detail):
        self.relpath = relpath
        self.phase = phase
        self.detail = detail


class CollectionResult:
    # Input collection
    __slots__ = ("inputs", "issues")

    def __init__(self, inputs, issues):
        self.inputs = tuple(inputs)
        self.issues = tuple(issues)


class _LocatedInputError(Exception):
    # Diagnostics
    def __init__(self, relpath, phase, detail):
        super().__init__(detail)
        self.relpath = relpath
        self.phase = phase
        self.detail = detail


_READ_CHUNK = 1 << 20


def _capabilities_present():
    # Resolver
    if not all(hasattr(os, a) for a in ("O_RDONLY", "O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")):
        return False
    if os.open not in os.supports_dir_fd:
        return False
    if os.scandir not in os.supports_fd:
        return False
    return True


def _validate_root(root):
    # Resolver
    s = os.fspath(root)
    if not isinstance(s, str):
        raise _LocatedInputError(str(s), "root", "root path is not a text path")
    if "\x00" in s:
        raise _LocatedInputError(s, "root", "root path contains a NUL byte")
    if not os.path.isabs(s):
        raise _LocatedInputError(s, "root", "root path is not absolute")
    return s


def _validate_rel(rel):
    # Resolver
    if "\x00" in rel:
        raise _LocatedInputError(rel, "descent", "required path contains a NUL byte")
    if rel.startswith("/"):
        raise _LocatedInputError(rel, "descent", "required path is not relative")
    parts = rel.split("/")
    for comp in parts:
        if comp in ("", ".", ".."):
            raise _LocatedInputError(rel, "descent", "required path has an empty or dotted component")
    return parts


def _open_dir_confirm(name, parent_fd, where, phase):
    # Resolver
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise _LocatedInputError(where, phase, "cannot open directory component {!r}: {}".format(name, exc))
    try:
        st = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        raise _LocatedInputError(where, phase, "cannot fstat directory component {!r}: {}".format(name, exc))
    if not stat.S_ISDIR(st.st_mode):
        os.close(fd)
        raise _LocatedInputError(where, phase, "component {!r} is not a directory".format(name))
    return fd


def _open_root(root_str):
    # Resolver
    parts = [p for p in root_str.split("/") if p != ""]
    fd = _open_dir_confirm("/", None, root_str, "root")
    for comp in parts:
        nxt = _open_dir_confirm(comp, fd, root_str, "root")
        os.close(fd)
        fd = nxt
    return fd


def _descend(root_fd, rel):
    # Scope traversal
    parts = _validate_rel(rel)
    fd = os.dup(root_fd)
    try:
        for comp in parts:
            nxt = _open_dir_confirm(comp, fd, rel, "descent")
            os.close(fd)
            fd = nxt
    except BaseException:
        os.close(fd)
        raise
    return fd


def _scandir_names(dir_fd, rel):
    # Scope traversal
    names = []
    try:
        with os.scandir(dir_fd) as it:
            for entry in it:
                names.append(entry.name)
    except OSError as exc:
        raise _LocatedInputError(rel, "scandir", "cannot enumerate required directory: {}".format(exc))
    return sorted(names)


def _read_regular_file(name, dir_fd, relpath):
    # Input collection
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise _LocatedInputError(relpath, "open", "cannot open selected file: {}".format(exc))
    try:
        try:
            st = os.fstat(fd)
        except OSError as exc:
            raise _LocatedInputError(relpath, "open", "cannot fstat selected file: {}".format(exc))
        if not stat.S_ISREG(st.st_mode):
            raise _LocatedInputError(relpath, "open", "selected file is not a regular file")
        chunks = []
        try:
            while True:
                block = os.read(fd, _READ_CHUNK)
                if not block:
                    break
                chunks.append(block)
        except OSError as exc:
            raise _LocatedInputError(relpath, "read", "cannot read selected file: {}".format(exc))
        except (RecursionError, MemoryError) as exc:
            raise _LocatedInputError(relpath, "read",
                                     "selected file exceeds capacity (cannot evaluate): {}"
                                     .format(type(exc).__name__))
        return b"".join(chunks)
    finally:
        os.close(fd)


def read_source_file(path):
    """Read one required file's bytes with race-free, no-follow resolution on every path component: the
    parent directory is resolved component-by-component under no-follow directory descriptors (the same
    traversal collect_python_inputs uses via _open_root), then the final file is opened relative to that
    parent descriptor with O_NOFOLLOW and confirmed to be a regular file. Any symlinked component (parent
    or final) or a non-regular target is refused as a located input error (the caller maps it to exit 2).
    Best-effort heuristic advisory; the authoritative behavior is the code and the --self-test."""
    s = os.fspath(path)
    if "\x00" in s:
        raise _LocatedInputError(s, "open", "required path contains a NUL byte")
    parent = os.path.dirname(s) or "."
    name = os.path.basename(s)
    if not name:
        raise _LocatedInputError(s, "open", "required path has no file component")
    if not _capabilities_present():
        raise _LocatedInputError(s, "capability",
                                 "the platform lacks a required no-follow descriptor primitive "
                                 "(cannot evaluate)")
    # A relative parent is resolved against the current directory (preserving the prior contract) into an
    # absolute path, then walked component-by-component under no-follow descriptors; abspath only prepends
    # the working directory and normalizes, so every real component (symlink included) is still confirmed
    # by the no-follow traversal below.
    pfd = _open_root(os.path.abspath(parent))
    try:
        return _read_regular_file(name, pfd, s)
    finally:
        os.close(pfd)


def _collect_one_dir(root_fd, rel, inputs, issues):
    # Input collection
    try:
        dir_fd = _descend(root_fd, rel)
    except _LocatedInputError as exc:
        issues.append(InputIssue(exc.relpath, exc.phase, exc.detail))
        return
    try:
        try:
            names = _scandir_names(dir_fd, rel)
        except _LocatedInputError as exc:
            issues.append(InputIssue(exc.relpath, exc.phase, exc.detail))
            return
        for name in names:
            if not name.endswith(".py"):
                continue
            relpath = rel + "/" + name
            try:
                raw = _read_regular_file(name, dir_fd, relpath)
            except _LocatedInputError as exc:
                issues.append(InputIssue(exc.relpath, exc.phase, exc.detail))
                continue
            inputs.append(SourceInput(relpath, raw))
    finally:
        os.close(dir_fd)


def collect_python_inputs(root, required_dirs):
    """Collect the direct *.py inputs under each required directory, descriptor-bound. Returns a
    CollectionResult whose issues (if any) the caller maps to exit 2. Best-effort heuristic advisory; the
    authoritative behavior is the code and the --self-test."""
    inputs = []
    issues = []
    if not _capabilities_present():
        for rel in required_dirs:
            issues.append(InputIssue(rel, "capability",
                                     "the platform lacks a required no-follow descriptor primitive "
                                     "(cannot evaluate)"))
        return CollectionResult(inputs, issues)
    try:
        root_str = _validate_root(root)
    except _LocatedInputError as exc:
        issues.append(InputIssue(exc.relpath, exc.phase, exc.detail))
        return CollectionResult(inputs, issues)
    try:
        root_fd = _open_root(root_str)
    except _LocatedInputError as exc:
        issues.append(InputIssue(exc.relpath, exc.phase, exc.detail))
        return CollectionResult(inputs, issues)
    try:
        for rel in required_dirs:
            _collect_one_dir(root_fd, rel, inputs, issues)
    finally:
        os.close(root_fd)
    inputs.sort(key=lambda si: si.relpath)
    issues.sort(key=lambda ii: (ii.relpath, ii.phase, ii.detail))
    return CollectionResult(inputs, issues)
