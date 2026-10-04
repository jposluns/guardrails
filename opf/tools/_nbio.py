"""Shared NON-BLOCKING, fstat-checked read helpers (D-400-SPECIAL-FILE-PRECHECK defence in
depth; QA round 7, codex B1): every converted raw read site routes through ONE of these, so a FIFO,
socket or device planted at (or behind a symlink at) any path a gate or self-test reads is REFUSED
by name instead of blocking the reader forever. O_NONBLOCK makes the open of a FIFO with no writer
return at once; the opened descriptor is fstat-checked and anything but a regular file raises
OSError (fail-closed, the callers' existing OSError arms map it); on a regular file every helper is
byte- and semantics-identical to the raw call it replaces (symlinks are followed exactly as the raw
call followed them; decode, newline and buffering behaviour are the stdlib's own). Two identical
copies exist, tools/_nbio.py and opf/tools/_nbio.py, because the standalone OPF pack may not import
tools/ (check_opf_standalone_closure). Stdlib only."""
import io
import os
import stat

_EXTRA_FLAGS = getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)


def _open_checked(path, flags):
    """os.open(path, flags | O_NONBLOCK), fstat-checked: anything but a regular file is refused as
    OSError (never a blocking open or read); the descriptor is closed on every refusal path and
    returned otherwise. A path no path call accepts (an embedded NUL) is OSError too, never a raw
    ValueError out of os.open."""
    try:
        fd = os.open(path, flags | getattr(os, "O_NONBLOCK", 0) | _EXTRA_FLAGS)
    except ValueError as exc:
        raise OSError("not a usable path ({0}): {1!r}".format(exc, path)) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("refused, not a regular file (a FIFO, device, socket, or directory); a "
                          "plain read of it could block forever: {0!r}".format(path))
    except BaseException:
        os.close(fd)
        raise
    return fd


def read_bytes_nb(path):
    """path.read_bytes() through one O_NONBLOCK, fstat-checked descriptor; identical bytes on a
    regular file, OSError (never a hang) on anything else."""
    fd = _open_checked(path, os.O_RDONLY)
    try:
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


def read_text_nb(path, encoding=None, errors=None, newline=None):
    """path.read_text(...) through read_bytes_nb: decode, errors and newline behaviour are exactly
    Path.read_text's (TextIOWrapper defaults), so a regular file reads identically."""
    wrapper = io.TextIOWrapper(io.BytesIO(read_bytes_nb(path)), encoding=encoding, errors=errors,
                               newline=newline)
    with wrapper:
        return wrapper.read()


_MODE_BASE_FLAGS = dict((
    ("r", os.O_RDONLY),
    ("r+", os.O_RDWR),
    ("+r", os.O_RDWR),
    ("w", os.O_WRONLY | os.O_CREAT | os.O_TRUNC),
    ("w+", os.O_RDWR | os.O_CREAT | os.O_TRUNC),
    ("+w", os.O_RDWR | os.O_CREAT | os.O_TRUNC),
    ("a", os.O_WRONLY | os.O_CREAT | os.O_APPEND),
    ("a+", os.O_RDWR | os.O_CREAT | os.O_APPEND),
    ("+a", os.O_RDWR | os.O_CREAT | os.O_APPEND),
    ("x", os.O_WRONLY | os.O_CREAT | os.O_EXCL),
    ("x+", os.O_RDWR | os.O_CREAT | os.O_EXCL),
    ("+x", os.O_RDWR | os.O_CREAT | os.O_EXCL),
))


def open_nb(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
    """builtin open(...) through one O_NONBLOCK, fstat-checked descriptor: same file-object type,
    text/binary and newline semantics as open() on a regular file, OSError (never a hang) on a FIFO,
    device, socket or directory. An already-open integer descriptor is wrapped as open() wraps it
    (its open already happened; wrapping cannot block). Unsupported open() extras (closefd=False,
    opener=) are deliberately absent: no converted site uses them, and a new site should use the
    shared helpers or carry its own per-site reason."""
    if isinstance(file, int):
        return os.fdopen(file, mode, buffering, encoding, errors, newline)
    if not isinstance(mode, str):
        raise OSError("unsupported open mode {0!r}".format(mode))
    base = "".join(ch for ch in mode if ch not in "btU")
    flags = _MODE_BASE_FLAGS.get(base)
    if flags is None:
        raise OSError("unsupported open mode {0!r}".format(mode))
    fd = _open_checked(os.fspath(file), flags)
    try:
        return os.fdopen(fd, mode, buffering, encoding, errors, newline)
    except BaseException:
        os.close(fd)
        raise
