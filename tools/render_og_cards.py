#!/usr/bin/env python3
"""Render candidate link-preview cards from their compositions in tools/og-cards/ with headless Chrome, for the
owner's review. The served cards, site/assets/aiqt-og.png and opf/site/assets/opfiles-og.png (the og:image
tools/gen_draft_site.py names), are the owner-approved PNGs from pull request #973; this script does not
produce them and refuses to write over them.

  render_og_cards.py --out DIR      render every candidate card into DIR with /usr/bin/google-chrome
  render_og_cards.py --self-test    assert the output-path decision over tempdir fixtures; Chrome is not started
  render_og_cards.py [--self-test] --execution-report ABS_PATH
  any other argument list is refused with exit 2

DIR is required; each candidate is written as DIR/aiqt-og.png and DIR/opfiles-og.png. An output path that
resolves (every symlink followed, a relative path taken from the working directory, real paths compared) to
either served card or into site/assets/ or opf/site/assets/, or whose case-folded components, as typed
(after normpath) or fully resolved, contain site/assets or opf/site/assets as a run of whole components
anywhere (any checkout's served assets directory or anything inside one, not only this one's), is refused
with exit 2 before Chrome is started, and each card path is checked again just before it is written. The
output directory is also opened before any render and again by each write, without following a final
symlink; a missing output directory is created only after the nearest existing ancestor has been opened
and has passed every check, one mkdir at a time through the checked parent's descriptor, so a refused race
never leaves a directory inside a served assets directory. The opened directory is refused when its device
and inode match a served assets directory or an ancestor of one up to the repository root (never a
directory above the root), when any parent reached from that open descriptor (an openat of "..", no path
re-resolution, so a parent swapped in after the path checks cannot hide it) matches a served assets
directory, or when the path read back from the descriptor itself (/proc/self/fd, confirmed against the
descriptor's identity, so the name check binds to the opened object and not to the typed name) contains a
served assets directory name, which refuses a component swapped to another checkout's served assets after
the path checks; where that path cannot be read back, the write is refused (fail closed). An output
directory whose parents the identity walk cannot open (for example one under an ancestor that is
traversable but not readable where O_PATH is unavailable) is refused with the failure as the reason: the
walk fails closed even where a plain write would succeed. The write never opens the card name: the bytes
are renamed onto the card name from a new exclusively created temporary file, so a hard link or a planted
symlink at that name is replaced as a directory entry, never written through; the temporary file is
removed on any failure, an interrupt included (an interrupt can still leave a file descriptor, the
temporary file's or an O_PATH one held by the parent walk, open for the moment the interrupted process
takes to exit; no directory entry is left behind). After the rename lands the card, the opened directory
is checked again on the same descriptor; where that recheck refuses (for example the directory was renamed
into a served assets directory after the checks and before the write), the card is unlinked through the
descriptor and the write is refused. A window remains and is claimed nowhere narrower than this: a rename
of the output directory that lands after the recheck has read the directory, its parents and its
read-back path keeps the written card wherever the directory then sits, and nothing here removes it. The
O_NOFOLLOW on the directory open covers the final path component only: an existing output directory
reached through a symlinked parent is opened (the parent symlink is followed, and the opened directory is
then judged by its identity and read-back path), while a final component that is itself a symlink,
including a symlink that is the nearest existing ancestor of a missing path, is refused; so link/existing
can be accepted where link/missing is refused. A ".." among the typed components is folded into the
component before it only where that component is not a symlink; a ".." that would cross a symlink is
refused, because the typed fold and the kernel would then name different directories and the directory
the checks accept must be the directory the write opens. Each card is a 1200 by 627 PNG below 1 MB; a render
that gives another size or a larger file is refused (exit 1) and nothing is kept. Exit 2 when
/usr/bin/google-chrome is missing or not executable (checked before any render); a Chrome launch that then
fails (an OSError or a timeout from the subprocess) is reported and exits 1, like any other failed render.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: render_og_cards.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import errno
import os
import signal
import stat
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHROME = "/usr/bin/google-chrome"
WIDTH, HEIGHT = 1200, 627
MAX_BYTES = 1 << 20
# (composition under tools/og-cards/, the served card it is a candidate for, under the repository root). The
# served card is never written: a candidate is written under the caller's output directory with the served
# card's file name.
CARDS = (
    ("aiqt-og.html", "site/assets/aiqt-og.png"),
    ("opfiles-og.html", "opf/site/assets/opfiles-og.png"),
)
# The served assets directories, under the repository root: no candidate is written into either.
SERVED_ASSETS = ("site/assets", "opf/site/assets")


class Refused(Exception):
    """An output path that resolves to a served card or into a served assets directory, contains a served
    assets directory name among its components, or names a directory whose identity, or a parent's read
    from the open descriptor, is a served assets directory or an ancestor of one under the repository root."""


def _name_refusal(path):
    """None when the case-folded components of path, after normpath and with no symlink resolved, do not
    contain site/assets or opf/site/assets as a run of whole components, otherwise the plain reason it is
    refused. The comparison is textual, anywhere in the path and by whole components, so it refuses another
    checkout's served assets directories and anything inside one (x/site/assets/sub), which no real-path
    comparison against this checkout's ROOT can see; a component that merely shares the prefix
    (assets-candidates, mysite) does not match."""
    parts = tuple(part.casefold() for part in Path(os.path.normpath(os.fspath(path))).parts)
    for run in (("opf", "site", "assets"), ("site", "assets")):
        for start in range(len(parts) - len(run) + 1):
            if parts[start:start + len(run)] == run:
                return "{} contains the served assets directory name {}".format(path, "/".join(run))
    return None


def path_refusal(path):
    """None when path resolves outside both served cards and both served assets directories and neither
    path as typed nor its real path contains a served assets directory name among its components, otherwise
    the plain reason it is refused. Real paths are compared first: every symlink is resolved, and a
    relative path is taken from the working directory; the component comparison is textual, so a path into
    another checkout's site/assets or opf/site/assets is refused too, even reached through a symlink or
    with a trailing sub/.. that the typed form folds away before the components are read."""
    try:
        real = Path(os.path.realpath(path))
        served = [Path(os.path.realpath(ROOT / card)) for _composition, card in CARDS]
        assets = [Path(os.path.realpath(ROOT / directory)) for directory in SERVED_ASSETS]
    except (OSError, ValueError) as exc:
        return "{} cannot be resolved ({})".format(path, exc)
    if real in served:
        return "{} resolves to the served card {}".format(path, real)
    for directory in assets:
        if real.is_relative_to(directory):
            return "{} resolves into the served assets directory {}".format(path, directory)
    return _name_refusal(path) or _name_refusal(real)


def _assets_identities():
    """(directories, held): the (st_dev, st_ino) of each served assets directory under ROOT, then the same
    identities with every ancestor of each added, up to and including the repository root and never above
    it, so a directory that merely holds the repository is not refused; only identities that stat. A
    device-and-inode match refuses an output directory that is a served assets directory (or an ancestor
    holding one under the repository root) reached through a bind mount, a hard-linked alias or any other
    second path that no path comparison can see; the parents walked from an open descriptor are compared
    against the served assets directories alone."""
    directories = set()
    held = set()
    root = Path(os.path.realpath(ROOT))
    for served in SERVED_ASSETS:
        resolved = Path(os.path.realpath(ROOT / served))
        for candidate in [resolved] + list(resolved.parents):
            try:
                status = os.stat(candidate)
            except OSError:
                pass
            else:
                held.add((status.st_dev, status.st_ino))
                if candidate == resolved:
                    directories.add((status.st_dev, status.st_ino))
            if candidate == root:
                break
    return directories, held


# The parent walk opens each ".." with O_PATH where the platform has it: the identity read needs only
# fstat, and an ancestor of the output directory may be traversable but not readable.
_WALK_FLAGS = getattr(os, "O_PATH", os.O_RDONLY) | os.O_DIRECTORY


def _opened_dir_path(dir_fd):
    """The path of the directory open as dir_fd, read back from the descriptor itself (/proc/self/fd) and
    confirmed to name the same device and inode the descriptor holds, or None where the platform cannot
    give one or the read-back path no longer names the opened directory (a deleted directory included).
    The caller refuses on None: the name check must bind to the opened object, never to the typed name."""
    try:
        real = os.readlink("/proc/self/fd/{}".format(dir_fd))
    except OSError:
        return None
    if not os.path.isabs(real):
        return None
    try:
        status = os.stat(real)
    except OSError:
        return None
    opened = os.fstat(dir_fd)
    if (status.st_dev, status.st_ino) != (opened.st_dev, opened.st_ino):
        return None
    return real


def _opened_dir_refusal(dir_fd, directory):
    """None when the directory open as dir_fd is not a refused identity, no parent reached from that
    descriptor is one, and the path read back from the descriptor passes the served-assets name check,
    otherwise the plain reason. The directory itself is refused when its device and inode match a served
    assets directory or an ancestor of one up to the repository root; each parent, reached by an openat of
    ".." from the descriptor (never a path, so a parent swapped in after the path checks cannot redirect
    the walk), is refused when it matches a served assets directory, which refuses a write anywhere inside
    one. The walk ends at the filesystem root, where ".." is the directory itself. The identity checks
    only know this checkout's served assets, so the name check is then applied to the opened directory's
    own path, read back from the descriptor: a component swapped to another checkout's served assets after
    the typed-name checks is refused here, and a directory whose path cannot be read back is refused too
    (fail closed). An interrupt can leave one O_PATH walk descriptor open for the moment the process takes
    to exit; the walk never leaves a directory entry behind."""
    directories, held = _assets_identities()
    status = os.fstat(dir_fd)
    if (status.st_dev, status.st_ino) in held:
        return ("{} is a served assets directory or an ancestor of one under the repository root (same "
                "device and inode), whatever path reaches it".format(directory))
    fd = os.open("..", _WALK_FLAGS, dir_fd=dir_fd)
    try:
        while True:
            parent = os.fstat(fd)
            if (parent.st_dev, parent.st_ino) == (status.st_dev, status.st_ino):
                break
            if (parent.st_dev, parent.st_ino) in directories:
                return ("{} is inside a served assets directory (a parent has its device and inode), "
                        "whatever path reaches it".format(directory))
            status = parent
            above = os.open("..", _WALK_FLAGS, dir_fd=fd)
            previous, fd = fd, above
            os.close(previous)
    finally:
        os.close(fd)
    real = _opened_dir_path(dir_fd)
    if real is None:
        return ("the output directory {} cannot be read back from its open descriptor, so the name check "
                "cannot bind to the opened directory; refused (fail closed)".format(directory))
    return _name_refusal(real)


def _folded_directory(directory):
    """directory with each "." dropped and each ".." folded into the component typed before it, but only
    where that component is not a symlink (islink, which never follows the component itself): the folded
    path then names the same directory the kernel resolves, so the directory the checks accept is the
    directory the write opens. A ".." whose preceding component is a symlink raises Refused: the kernel
    resolves the symlink first and takes ".." from its target, so folding it textually would name a
    different directory than the kernel and than the real-path checks. A leading ".." with nothing typed
    before it, or one behind another kept "..", is kept for the kernel to resolve; on an absolute path it
    names the root itself and is dropped. The islink reads are by path at fold time, like every typed-path
    check here; the opened directory is still judged afterwards by its identity and read-back path."""
    path = Path(os.fspath(directory))
    anchor = path.anchor
    kept = []
    for part in (path.parts[1:] if anchor else path.parts):
        if part == ".":
            continue
        if part != "..":
            kept.append(part)
            continue
        if kept and kept[-1] != "..":
            prefix = Path(anchor).joinpath(*kept) if anchor else Path(*kept)
            if os.path.islink(prefix):
                raise Refused(
                    "{} takes .. across the symlink {}: the kernel resolves the symlink first and the "
                    "typed components do not, so they name different directories; refused so the checked "
                    "directory is the written directory".format(directory, prefix))
            kept.pop()
        elif not anchor:
            kept.append("..")
    if anchor:
        return Path(anchor).joinpath(*kept)
    return Path(*kept) if kept else Path(".")


def _entry_is_symlink(name, dir_fd):
    """True when the directory entry name under the directory open as dir_fd is a symlink, read with lstat
    through that descriptor (no path re-resolution, and the entry itself is never followed); False where
    the entry cannot be read."""
    try:
        return stat.S_ISLNK(os.lstat(name, dir_fd=dir_fd).st_mode)
    except OSError:
        return False


def _checked_dir_fd(directory):
    """directory opened with O_RDONLY | O_DIRECTORY | O_NOFOLLOW and already past _opened_dir_refusal,
    missing final components created one mkdir at a time through their parent's descriptor only after that
    parent has itself been opened and has passed every check, so nothing is ever created under a directory
    that fails the checks and a refused race leaves no directory behind. The typed path is folded by
    _folded_directory first, so a ".." that crosses a symlink is refused rather than folded into a
    directory the kernel would not resolve it to. O_NOFOLLOW covers only the component each open names: an
    existing directory behind a symlinked parent is opened and then judged by its identity and read-back
    path, while a final component that is itself a symlink, the nearest existing ancestor of a missing
    path included, is refused (the kernel reports it as ELOOP, or as ENOTDIR on some kernels; both are
    read as the symlink refusal, the entry confirmed with lstat and never followed). Raises Refused for a
    refused, symlinked or unwalkable directory and OSError for a plain open failure; the caller closes the
    returned descriptor, which is closed here on every failure."""
    missing = []
    probe = _folded_directory(directory)
    while True:
        try:
            fd = os.open(probe, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            break
        except FileNotFoundError:
            if probe.parent == probe:
                raise
            missing.append(probe.name)
            probe = probe.parent
        except OSError as exc:
            if exc.errno == errno.ELOOP or (exc.errno == errno.ENOTDIR and os.path.islink(probe)):
                raise Refused("{} is a symlink, not opened as the output directory".format(probe)) from exc
            raise
    try:
        while True:
            try:
                reason = _opened_dir_refusal(fd, probe)
            except OSError as exc:
                raise Refused(
                    "cannot walk the parents of the output directory {} ({}); an ancestor the walk cannot "
                    "open, for example one that is traversable but not readable where O_PATH is "
                    "unavailable, stops the identity walk, and the script fails closed even where a plain "
                    "write would succeed".format(probe, exc)) from exc
            if reason:
                raise Refused(reason)
            if not missing:
                return fd
            name = missing.pop()
            try:
                os.mkdir(name, 0o777, dir_fd=fd)
            except FileExistsError:
                pass
            try:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                if exc.errno == errno.ELOOP or (exc.errno == errno.ENOTDIR
                                                and _entry_is_symlink(name, fd)):
                    raise Refused("{} is a symlink, not opened as the output directory".format(
                        probe / name)) from exc
                raise
            previous, fd = fd, child
            os.close(previous)
            probe = probe / name
    except BaseException:
        os.close(fd)
        raise


def _out_dir_identity_refusal(out_dir):
    """The identity refusal for out_dir, opened exactly as each write opens it (any missing component
    created only through its checked parent's descriptor, so a refused directory is never created), or
    None when it is accepted; run before any render, so a refused output directory never starts Chrome. A
    directory that cannot be created, opened or walked is refused with the failure as the reason: an
    unwalkable ancestor fails closed, and a render could only fail the same way later."""
    try:
        dir_fd = _checked_dir_fd(Path(out_dir))
    except Refused as exc:
        return str(exc)
    except OSError as exc:
        return "cannot open the output directory {} ({})".format(out_dir, exc)
    os.close(dir_fd)
    return None


def card_targets(out_dir):
    """The candidate card path under out_dir for each composition, in CARDS order."""
    return [Path(out_dir) / Path(card).name for _composition, card in CARDS]


def output_refusal(out_dir):
    """None when out_dir and every candidate card path under it pass path_refusal, otherwise the reason."""
    if not isinstance(out_dir, str) or not out_dir:
        return "the output directory is empty"
    for path in [Path(out_dir)] + card_targets(out_dir):
        reason = path_refusal(path)
        if reason:
            return reason
    return None


def png_size(data):
    """(width, height) of PNG bytes, or None when they are not a PNG."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _unlink_temp(temp_name, dir_fd):
    """Remove the temporary card entry temp_name through dir_fd, quietly: a file the interrupted creation
    never reached (ENOENT) or cannot remove is not a second failure to report over the first."""
    try:
        os.unlink(temp_name, dir_fd=dir_fd)
    except OSError:
        pass


def _write_card(path, data):
    """Write data as the card at path without ever opening the card name for writing: the bytes go to a new
    temporary file created exclusively inside the directory and are renamed onto the card name, so a hard
    link or a symlink planted at that name (even between the last check and the write) is replaced as a
    directory entry, never followed or truncated. The directory is opened without following a final symlink
    and any missing component is created only through its checked parent's descriptor, after every refusal
    has passed, so a refused race never leaves a directory behind; the opened directory is refused when its
    identity, or a parent's reached from the open descriptor, is a served assets directory, when its own
    identity is an ancestor of one up to the repository root, or when the path read back from its
    descriptor contains a served assets directory name (another checkout's included). Raises Refused for a
    refused directory; returns an error string for a plain I/O failure; re-raises any other failure (an
    interrupt included) with the temporary file removed, the temporary file's creation itself covered by a
    cleanup that uses only its name (a descriptor can still be left open for the moment an interrupted
    process takes to exit); None when the card is written. After the rename, the opened directory is
    rechecked on the same descriptor (its identity, each parent reached from it, and its read-back path);
    a recheck that refuses, or that cannot be completed, unlinks the just-renamed card through the
    descriptor and raises Refused, so a directory renamed into a served assets directory between the
    checks and the rename does not keep the card. The window that remains: a rename of the directory that
    lands after the recheck has read the directory, its parents and its read-back path keeps the card
    wherever the directory then sits, and nothing here removes it."""
    directory = path.parent
    reason = _name_refusal(directory)
    if reason:
        raise Refused(reason)
    try:
        dir_fd = _checked_dir_fd(directory)
    except OSError as exc:
        return "cannot open the output directory {} ({})".format(directory, exc)
    try:
        temp_name = ".{}.{}.tmp".format(path.name, os.urandom(8).hex())
        fd = -1
        try:
            try:
                fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
                             dir_fd=dir_fd)
            except OSError as exc:
                return "cannot create a temporary card file in {} ({})".format(directory, exc)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
            fd, pending = -1, fd
            os.close(pending)
            os.replace(temp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except OSError as exc:
            _unlink_temp(temp_name, dir_fd)
            return "cannot write {} through its directory {} ({})".format(path.name, directory, exc)
        except BaseException:
            _unlink_temp(temp_name, dir_fd)
            raise
        finally:
            if fd != -1:
                os.close(fd)
        try:
            reason = _opened_dir_refusal(dir_fd, directory)
        except OSError as exc:
            reason = ("cannot walk the parents of the output directory {} when rechecked after the "
                      "write ({}); fails closed".format(directory, exc))
        if reason:
            try:
                os.unlink(path.name, dir_fd=dir_fd)
                removal = "the card was removed through the held directory descriptor"
            except OSError as exc:
                removal = ("the card could not be removed through the held directory descriptor "
                           "({})".format(exc))
            raise Refused(
                "the output directory {} was refused when rechecked on its open descriptor after the "
                "write ({}); {}".format(directory, reason, removal))
        return None
    finally:
        os.close(dir_fd)


def render(source, target):
    """Render one composition to target; an error string, or None when the card is written. Raises Refused,
    before Chrome is started and again just before the write, when target resolves to a served card or into
    a served assets directory, and from the write itself when the output directory is refused by name or by
    device and inode."""
    reason = path_refusal(target)
    if reason:
        raise Refused(reason)
    with tempfile.TemporaryDirectory(prefix="og-card-") as scratch:
        shot = Path(scratch) / "card.png"
        command = ["/usr/bin/google-chrome", "--headless", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                   "--allow-file-access-from-files", "--force-device-scale-factor=1",
                   "--user-data-dir=" + str(Path(scratch) / "profile"), "--virtual-time-budget=5000",
                   "--window-size={},{}".format(WIDTH, HEIGHT), "--screenshot=" + str(shot), source.as_uri()]
        try:
            subprocess.run(command, check=False, capture_output=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "cannot run {} ({})".format(CHROME, exc)
        try:
            data = shot.read_bytes()
        except OSError as exc:
            return "no screenshot from {} ({})".format(CHROME, exc)
    size = png_size(data)
    if size != (WIDTH, HEIGHT):
        return "{} rendered at {}, not {}x{}".format(source.name, size, WIDTH, HEIGHT)
    if len(data) >= MAX_BYTES:
        return "{} rendered to {} bytes, not below {}".format(source.name, len(data), MAX_BYTES)
    path = Path(target)
    reason = path_refusal(path)
    if reason:
        raise Refused(reason)
    error = _write_card(path, data)
    if error:
        return error
    print("wrote {} ({} bytes)".format(path, len(data)))
    return None


USAGE = "usage: render_og_cards.py --out DIR (a directory outside site/assets/ and opf/site/assets/)"


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    argv = list(argv)
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
            sys.path.append(str(Path(__file__).resolve().parent))
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    if len(argv) != 2 or argv[0] != "--out":
        print("error: unrecognized arguments {!r}; fail closed. {}".format(argv, USAGE), file=sys.stderr)
        return 2
    out_dir = argv[1]
    reason = output_refusal(out_dir)
    if reason:
        print("error: refused: {}. The served cards are the owner-approved PNGs and this script never writes "
              "them; name a scratch directory. Nothing rendered.".format(reason), file=sys.stderr)
        return 2
    if not os.access(CHROME, os.X_OK):
        print("error: {} is not an executable; nothing rendered".format(CHROME), file=sys.stderr)
        return 2
    reason = _out_dir_identity_refusal(out_dir)
    if reason:
        print("error: refused: {}. The served cards are the owner-approved PNGs and this script never writes "
              "them; name a scratch directory. Nothing rendered.".format(reason), file=sys.stderr)
        return 2
    code = 0
    for (composition, _card), target in zip(CARDS, card_targets(out_dir)):
        try:
            error = render(ROOT / "tools" / "og-cards" / composition, target)
        except Refused as exc:
            print("error: refused: {}. Nothing more rendered.".format(exc), file=sys.stderr)
            return 2
        if error:
            print("error: {}".format(error), file=sys.stderr)
            code = 1
    return code


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "og-cards-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _patched(name, value, function, *args):
    """function(*args) with this module's attribute name bound to value, restored afterwards."""
    module = sys.modules[__name__]
    saved = getattr(module, name)
    setattr(module, name, value)
    try:
        return function(*args)
    finally:
        setattr(module, name, saved)


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(argv)


def _fake_png():
    """The 33 leading bytes of a WIDTH by HEIGHT PNG: enough for png_size, far below MAX_BYTES."""
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", WIDTH, HEIGHT)
            + b"\x08\x06\x00\x00\x00")


def _refusal_cases(tmp):
    """{case: a call giving the decision's reason or None} for each path that must be refused. Each call
    looks path_refusal up at call time, so the guard-removed pass sees the decision removed."""
    served_aiqt, served_opfiles = (ROOT / card for _composition, card in CARDS)
    link_dir = Path(tmp) / "out-with-link"
    os.makedirs(link_dir)
    os.symlink(served_aiqt, link_dir / Path(served_aiqt).name)
    os.symlink(ROOT / "site" / "assets", Path(tmp) / "assets-link")
    relative = os.path.relpath(ROOT / "opf" / "site" / "assets" / "review")
    return {
        "served-aiqt": lambda: path_refusal(served_aiqt),
        "served-opfiles": lambda: path_refusal(served_opfiles),
        "assets-aiqt": lambda: output_refusal(str(ROOT / "site" / "assets" / "candidates")),
        "assets-opfiles": lambda: output_refusal(str(ROOT / "opf" / "site" / "assets" / "review")),
        "symlink-entry": lambda: output_refusal(str(link_dir)),
        "symlink-dir": lambda: output_refusal(str(Path(tmp) / "assets-link" / "new")),
        "relative": (lambda: output_refusal(relative) if not os.path.isabs(relative)
                     else "fixture path is not relative"),
        "assets-dir": lambda: output_refusal(str(ROOT / "site" / "assets")),
    }


def _self_test():
    with tempfile.TemporaryDirectory(prefix="og-cards-selftest-") as tmp:
        cases = _refusal_cases(tmp)
        # Each case refused; then, with path_refusal accepting every path (the guard removed), the same case
        # is accepted, so its refusal check fails without the decision.
        for case, name, removed in (
                ("served-aiqt", "served/aiqt-card-refused", "guard-removed/served/aiqt-card-refused"),
                ("served-opfiles", "served/opfiles-card-refused", "guard-removed/served/opfiles-card-refused"),
                ("assets-aiqt", "assets/aiqt-path-refused", "guard-removed/assets/aiqt-path-refused"),
                ("assets-opfiles", "assets/opfiles-path-refused", "guard-removed/assets/opfiles-path-refused"),
                ("symlink-entry", "symlink/out-entry-to-served-card-refused",
                 "guard-removed/symlink/out-entry-to-served-card-refused"),
                ("symlink-dir", "symlink/out-dir-into-assets-refused",
                 "guard-removed/symlink/out-dir-into-assets-refused"),
                ("relative", "relative/into-assets-refused", "guard-removed/relative/into-assets-refused"),
                ("assets-dir", "bound/assets-dir-itself-refused", "guard-removed/bound/assets-dir-itself-refused")):
            reason = cases[case]()
            check(name, isinstance(reason, str) and "resolves" in reason, reason)
            reason = _patched("path_refusal", lambda path: None, cases[case])
            check(removed, reason is None, reason)

        scratch = Path(tmp) / "scratch"
        reason = output_refusal(str(scratch))
        check("scratch/ordinary-dir-accepted", reason is None, reason)

        # The assets-directory end, compared by path component: the directory itself is refused (above); a
        # sibling sharing its name as a prefix is outside it and accepted; a path one level inside is refused.
        reason = output_refusal(str(ROOT / "site" / "assets-candidates"))
        check("bound/assets-prefix-sibling-accepted", reason is None, reason)
        reason = output_refusal(str(ROOT / "site" / "assets" / "x"))
        check("bound/assets-child-refused", isinstance(reason, str) and "served assets directory" in reason,
              reason)

        reason = output_refusal("")
        check("refuse/empty-out-refused", reason == "the output directory is empty", reason)

        got = [_main_exit(argv) for argv in ([], ["--out"], ["--bogus"], ["--self-test", "--bogus"],
                                             ["--out", str(scratch), "--bogus"],
                                             ["--execution-report", "relative/report"])]
        check("refuse/unrecognized-argument", got == [2] * 6, got)

        # main(): a refused output directory exits 2 before render (the only place Chrome is started) is
        # called; an ordinary scratch directory reaches render once per card, each under that directory.
        calls = []

        def _record(source, target):
            calls.append((source, target))
            return None

        got = _patched("render", _record, _main_exit, ["--out", str(ROOT / "site" / "assets")])
        check("main/refused-out-exit-2-before-chrome", got == 2 and calls == [], (got, calls))
        calls.clear()
        got = _patched("CHROME", sys.executable, _patched, "render", _record, _main_exit, ["--out", str(scratch)])
        check("main/scratch-out-reaches-render",
              got == 0 and [target for _source, target in calls] == card_targets(str(scratch)), (got, calls))

        # render(): over a fixture root, a card path that is a symlink to a served card is refused before
        # Chrome is started, and one that becomes such a symlink while Chrome runs is refused before the
        # write, leaving the fixture's served card unchanged.
        fixture = Path(tmp) / "root"
        served = fixture / CARDS[0][1]
        os.makedirs(served.parent)
        os.makedirs(fixture / SERVED_ASSETS[1])
        served.write_bytes(b"approved")
        out = Path(tmp) / "out"
        os.makedirs(out)
        target = out / served.name
        launched = []
        real_run = subprocess.run

        def _fake_chrome(command, **kwargs):
            launched.append(command)
            shot = [arg for arg in command if arg.startswith("--screenshot=")][0][len("--screenshot="):]
            Path(shot).write_bytes(_fake_png())
            if not os.path.lexists(target):
                os.symlink(served, target)

        def _render_to(card_target):
            try:
                return render(ROOT / "tools" / "og-cards" / CARDS[0][0], card_target)
            except Refused as exc:
                return exc

        def _render():
            return _render_to(target)

        subprocess.run = _fake_chrome
        try:
            got = _patched("ROOT", fixture, _render)
            check("render/recheck-refuses-before-write", isinstance(got, Refused) and len(launched) == 1
                  and served.read_bytes() == b"approved", (got, launched))
            launched.clear()
            got = _patched("ROOT", fixture, _render)
            check("render/recheck-refuses-before-chrome", isinstance(got, Refused) and launched == []
                  and served.read_bytes() == b"approved", (got, launched))
        finally:
            subprocess.run = real_run

        # The textual component refusal: another checkout's served assets directory, which no real-path
        # comparison against this checkout's ROOT can see, is refused by name, case-folded and by whole
        # components; a component that merely shares the prefix is not a match.
        other = Path(tmp) / "other-checkout" / "site" / "assets"
        os.makedirs(other)
        reasons = [output_refusal(str(other)), output_refusal(str(other) + os.sep),
                   output_refusal(str(Path(tmp) / "other-checkout" / "opf" / "SITE" / "Assets"))]
        check("refuse/other-checkout-assets-suffix",
              all(isinstance(reason, str) and "served assets directory name" in reason for reason in reasons),
              reasons)
        reason = _patched("path_refusal", lambda path: None, output_refusal, str(other))
        check("guard-removed/refuse/other-checkout-assets-suffix", reason is None, reason)
        reasons = [output_refusal(str(Path(tmp) / "mysite" / "assets")),
                   output_refusal(str(Path(tmp) / "site" / "assets2"))]
        check("bound/suffix-component-boundary-accepted", reasons == [None, None], reasons)

        # The same directory reached so that the typed components never name it: through a symlink, with a
        # trailing sub/.. the typed form folds away before its components are read (the refusal then comes
        # from the real path), and a path one level inside such a directory (the run is no longer at the
        # end); each is refused.
        os.symlink(Path(tmp) / "other-checkout" / "site", Path(tmp) / "lnk")
        reason = output_refusal(str(Path(tmp) / "lnk" / "assets"))
        check("refuse/symlinked-other-checkout-assets",
              isinstance(reason, str) and "served assets directory name" in reason, reason)
        reason = output_refusal(str(Path(tmp) / "lnk" / "assets" / "sub" / ".."))
        check("refuse/symlinked-other-checkout-assets-dotdot",
              isinstance(reason, str) and "served assets directory name" in reason, reason)
        reasons = [output_refusal(str(Path(tmp) / "x" / "site" / "assets" / "sub")),
                   output_refusal(os.path.join("site", "assets", "sub"))]
        check("refuse/inside-other-checkout-assets",
              all(isinstance(reason, str) and "served assets directory" in reason for reason in reasons),
              reasons)
        reason = _patched("path_refusal", lambda path: None, output_refusal, str(Path(tmp) / "lnk" / "assets"))
        check("guard-removed/refuse/symlinked-other-checkout-assets", reason is None, reason)

        # The write path: the bytes are renamed onto the card name from a new exclusive temporary file
        # through the directory's descriptor, so an entry planted at the card name is replaced, never
        # written through, and an output directory that is a served assets directory (or an ancestor of
        # one) under a second path is refused by device and inode.
        def _plain_chrome(command, **kwargs):
            launched.append(command)
            shot = [arg for arg in command if arg.startswith("--screenshot=")][0][len("--screenshot="):]
            Path(shot).write_bytes(_fake_png())

        hard_target = Path(tmp) / "hard-out" / served.name
        os.makedirs(hard_target.parent)
        os.link(served, hard_target)
        race_target = Path(tmp) / "race-out" / served.name
        os.makedirs(race_target.parent)

        def _plant_then_pass(path):
            # A symlink that appears while the checks report nothing, i.e. between the last check and
            # the write: the rename must replace it, never follow it.
            if not os.path.lexists(race_target):
                os.symlink(served, race_target)
            return None

        subprocess.run = _plain_chrome
        try:
            got = _patched("ROOT", fixture, _render_to, hard_target)
            check("write/hard-link-to-served-card-left-unchanged",
                  got is None and served.read_bytes() == b"approved" and os.stat(served).st_nlink == 1
                  and hard_target.read_bytes() == _fake_png(),
                  (got, served.read_bytes(), os.stat(served).st_nlink))
            got = _patched("ROOT", fixture, _patched, "path_refusal", _plant_then_pass, _render_to,
                           race_target)
            check("write/symlink-planted-after-last-check-left-unchanged",
                  got is None and served.read_bytes() == b"approved" and not os.path.islink(race_target)
                  and race_target.read_bytes() == _fake_png(), (got, served.read_bytes()))
            os.symlink(fixture / "site", Path(tmp) / "mid")
            alias_target = Path(tmp) / "mid" / "assets" / served.name
            got = _patched("ROOT", fixture, _patched, "path_refusal", lambda path: None, _render_to,
                           alias_target)
            check("write/aliased-assets-dir-refused-by-inode",
                  isinstance(got, Refused) and "device and inode" in str(got)
                  and served.read_bytes() == b"approved", (got, served.read_bytes()))
            ancestor_target = fixture / served.name
            got = _patched("ROOT", fixture, _render_to, ancestor_target)
            check("write/assets-ancestor-dir-refused-by-inode",
                  isinstance(got, Refused) and "device and inode" in str(got)
                  and not os.path.exists(ancestor_target), (got,))
        finally:
            subprocess.run = real_run

        # The write-time guards, driven through _write_card directly (no Chrome, and where noted with the
        # path checks stubbed out, as a race past them would leave things): a parent swapped in between the
        # last path check and the directory open redirects the open into a served assets subdirectory, and
        # the walk from the descriptor refuses it; a directory above the repository root is accepted (the
        # ancestor identities stop at the root); a directory named like served assets is refused before it
        # is created; a symlinked output directory is never followed; an interrupted write removes its
        # temporary file and re-raises.
        expected = _fake_png()
        inside = fixture / SERVED_ASSETS[0] / "sub"
        os.makedirs(inside)
        swap = Path(tmp) / "swap"
        os.makedirs(swap / "sub")

        def _write_to(card_path):
            try:
                return _write_card(card_path, expected)
            except Refused as exc:
                return exc

        def _swap_then_pass(path):
            if not os.path.islink(swap):
                os.rename(swap, Path(tmp) / "swapped")
                os.symlink(fixture / SERVED_ASSETS[0], swap)
            return None

        got = _patched("ROOT", fixture, _patched, "_name_refusal", _swap_then_pass, _write_to,
                       swap / "sub" / served.name)
        check("write/parent-swapped-into-assets-subdir-refused-by-inode",
              isinstance(got, Refused) and "device and inode" in str(got) and os.listdir(inside) == [],
              (got, os.listdir(inside)))

        got = _patched("ROOT", fixture, _write_to, Path(tmp) / served.name)
        check("bound/dir-above-root-accepted",
              got is None and (Path(tmp) / served.name).read_bytes() == expected, (got,))

        named = Path(tmp) / "elsewhere" / "site" / "assets"
        got = _patched("ROOT", fixture, _write_to, named / served.name)
        check("write/dir-named-assets-refused-by-name",
              isinstance(got, Refused) and "served assets directory name" in str(got)
              and not os.path.exists(named), (got,))

        plain = Path(tmp) / "plain-out"
        os.makedirs(plain)
        os.symlink(plain, Path(tmp) / "dirlink")
        got = _patched("ROOT", fixture, _write_to, Path(tmp) / "dirlink" / served.name)
        check("write/symlinked-out-dir-not-followed",
              isinstance(got, Refused) and "symlink" in str(got) and os.listdir(plain) == [],
              (got, os.listdir(plain)))

        clean_dir = Path(tmp) / "clean-out"
        os.makedirs(clean_dir)
        real_fsync = os.fsync

        def _interrupting_fsync(fd):
            raise KeyboardInterrupt

        os.fsync = _interrupting_fsync
        try:
            try:
                got = _patched("ROOT", fixture, _write_to, clean_dir / served.name)
            except KeyboardInterrupt:
                got = KeyboardInterrupt
        finally:
            os.fsync = real_fsync
        check("write/temp-removed-on-interrupt",
              got is KeyboardInterrupt and os.listdir(clean_dir) == [], (got, os.listdir(clean_dir)))

        # The checks that bind to the opened directory, not the typed name: a parent swapped to another
        # checkout's served assets after the typed-name check (whose identity no device-and-inode set can
        # know) is refused by the path read back from the opened descriptor; a directory whose path cannot
        # be read back is refused, and nothing is created for it; a refused race never leaves a directory
        # inside served assets, from the write or from the pre-render identity check; missing components
        # are still created for an accepted directory; an unwalkable ancestor fails closed with the cause
        # stated; an interrupt during the temporary file's own creation leaves no file behind.
        other_assets = Path(tmp) / "other" / "site" / "assets"
        os.makedirs(other_assets / "sub")
        swap_other = Path(tmp) / "swap-other"
        os.makedirs(swap_other / "sub")
        real_name_refusal = _name_refusal

        def _swap_to_other_then_real(swapped_path):
            if not os.path.islink(swap_other):
                os.rename(swap_other, Path(tmp) / "swapped-other")
                os.symlink(other_assets, swap_other)
            return real_name_refusal(swapped_path)

        got = _patched("ROOT", fixture, _patched, "_name_refusal", _swap_to_other_then_real, _write_to,
                       swap_other / "sub" / served.name)
        check("write/parent-swapped-into-other-checkout-assets-refused",
              isinstance(got, Refused) and "served assets directory name" in str(got)
              and os.listdir(other_assets / "sub") == [], (got, os.listdir(other_assets / "sub")))

        swap_missing = Path(tmp) / "swap-missing"
        os.makedirs(swap_missing)

        def _swap_to_assets_then_real(swapped_path):
            if not os.path.islink(swap_missing):
                os.rename(swap_missing, Path(tmp) / "swapped-missing")
                os.symlink(fixture / SERVED_ASSETS[0], swap_missing)
            return real_name_refusal(swapped_path)

        got = _patched("ROOT", fixture, _patched, "_name_refusal", _swap_to_assets_then_real, _write_to,
                       swap_missing / "newdir" / served.name)
        check("write/refused-race-creates-nothing-in-assets",
              isinstance(got, Refused) and "is a symlink, not opened as the output directory" in str(got)
              and not os.path.exists(fixture / SERVED_ASSETS[0] / "newdir"),
              (got, os.path.exists(fixture / SERVED_ASSETS[0] / "newdir")))

        missing_under_assets = fixture / SERVED_ASSETS[0] / "new-out"
        reason = _patched("ROOT", fixture, _out_dir_identity_refusal, str(missing_under_assets))
        check("identity/missing-dir-under-assets-not-created",
              isinstance(reason, str)
              and "is a served assets directory or an ancestor of one under the repository root" in reason
              and not os.path.exists(missing_under_assets),
              (reason, os.path.exists(missing_under_assets)))

        fresh = Path(tmp) / "fresh" / "a" / "b"
        got = _patched("ROOT", fixture, _write_to, fresh / served.name)
        check("write/missing-out-dir-created-through-checked-parents",
              got is None and (fresh / served.name).read_bytes() == expected, (got,))

        locked_out = Path(tmp) / "locked-out"
        os.makedirs(locked_out)
        real_os_open = os.open

        def _denying_parent_open(open_target, flags, *args, **kwargs):
            if open_target == ".." and kwargs.get("dir_fd") is not None:
                raise PermissionError(errno.EACCES, "Permission denied", "..")
            return real_os_open(open_target, flags, *args, **kwargs)

        os.open = _denying_parent_open
        try:
            reason = _patched("ROOT", fixture, _out_dir_identity_refusal, str(locked_out))
        finally:
            os.open = real_os_open
        check("walk/unsearchable-ancestor-fails-closed-with-cause",
              isinstance(reason, str) and "cannot walk the parents" in reason and "fails closed" in reason,
              reason)

        burst_dir = Path(tmp) / "burst-out"
        os.makedirs(burst_dir)

        def _interrupting_create(open_target, flags, *args, **kwargs):
            opened = real_os_open(open_target, flags, *args, **kwargs)
            if flags & os.O_EXCL:
                os.close(opened)
                raise KeyboardInterrupt
            return opened

        os.open = _interrupting_create
        try:
            try:
                got = _patched("ROOT", fixture, _write_to, burst_dir / served.name)
            except KeyboardInterrupt:
                got = KeyboardInterrupt
        finally:
            os.open = real_os_open
        check("write/temp-removed-on-interrupt-during-create",
              got is KeyboardInterrupt and os.listdir(burst_dir) == [], (got, os.listdir(burst_dir)))

        no_proc = Path(tmp) / "no-proc-out"
        real_readlink = os.readlink

        def _failing_readlink(*args, **kwargs):
            raise OSError(errno.ENOENT, "no descriptor path on this platform")

        os.readlink = _failing_readlink
        try:
            got = _patched("ROOT", fixture, _write_to, no_proc / served.name)
        finally:
            os.readlink = real_readlink
        check("write/descriptor-path-unreadable-fails-closed",
              isinstance(got, Refused) and "cannot be read back" in str(got) and not os.path.exists(no_proc),
              (got, os.path.exists(no_proc)))

        # The post-write recheck on the held descriptor: an accepted output directory renamed into a
        # served assets directory after the checks (here at the temporary-name moment, so the rename is
        # visible when the card is renamed into place) does not keep the card; the recheck refuses and the
        # card is unlinked through the descriptor. A rename landing after the recheck is the window the
        # docstrings state; it is not claimed closed and not tested here.
        moved_out = Path(tmp) / "moved-out"
        os.makedirs(moved_out)
        real_urandom = os.urandom

        def _rename_into_assets_urandom(count):
            if not os.path.lexists(fixture / SERVED_ASSETS[0] / "moved-out"):
                os.rename(moved_out, fixture / SERVED_ASSETS[0] / "moved-out")
            return real_urandom(count)

        os.urandom = _rename_into_assets_urandom
        try:
            got = _patched("ROOT", fixture, _write_to, moved_out / served.name)
        finally:
            os.urandom = real_urandom
        check("write/dir-renamed-into-assets-after-check-removed-and-refused",
              isinstance(got, Refused) and "rechecked on its open descriptor after the write" in str(got)
              and os.listdir(fixture / SERVED_ASSETS[0] / "moved-out") == [],
              (got, os.listdir(fixture / SERVED_ASSETS[0] / "moved-out")))

        # A ".." that crosses a symlink: the typed fold would name dd-out under the base while the kernel
        # (and the real-path checks) resolve deep/dd-out, so the path is refused and neither directory is
        # created; a ".." folded across a real directory names the same directory both ways and is
        # accepted, the card written where the kernel would put it.
        deep = Path(tmp) / "deep" / "inner"
        os.makedirs(deep)
        os.symlink(deep, Path(tmp) / "deep-link")
        got = _patched("ROOT", fixture, _write_to,
                       Path(tmp) / "deep-link" / ".." / "dd-out" / served.name)
        check("write/dotdot-across-symlink-refused",
              isinstance(got, Refused) and "takes .. across the symlink" in str(got)
              and not os.path.exists(Path(tmp) / "dd-out")
              and not os.path.exists(Path(tmp) / "deep" / "dd-out"),
              (got, os.path.exists(Path(tmp) / "dd-out")))
        got = _patched("ROOT", fixture, _write_to,
                       Path(tmp) / "deep" / "inner" / ".." / "dd-ok" / served.name)
        check("bound/dotdot-across-real-dir-accepted",
              got is None and (Path(tmp) / "deep" / "dd-ok" / served.name).read_bytes() == expected,
              (got,))

        # A symlink planted at a missing component between the parent's mkdir and the child open, on a
        # kernel that reports the O_NOFOLLOW | O_DIRECTORY open of a symlink as ENOTDIR (forced here so
        # the vector does not depend on the kernel): the entry is read back through the held descriptor as
        # a symlink and the write is refused like the first open's ELOOP, not reported as an I/O error.
        planted_parent = Path(tmp) / "planted-out"
        planted_victim = Path(tmp) / "planted-victim"
        os.makedirs(planted_parent)
        os.makedirs(planted_victim)
        real_mkdir = os.mkdir

        def _planting_mkdir(name, mode=0o777, *, dir_fd=None):
            if name == "planted" and dir_fd is not None:
                os.symlink(planted_victim, name, dir_fd=dir_fd)
            return real_mkdir(name, mode, dir_fd=dir_fd)

        def _enotdir_child_open(open_target, flags, *args, **kwargs):
            if open_target == "planted" and kwargs.get("dir_fd") is not None and flags & os.O_DIRECTORY:
                raise OSError(errno.ENOTDIR, "Not a directory", open_target)
            return real_os_open(open_target, flags, *args, **kwargs)

        os.mkdir = _planting_mkdir
        os.open = _enotdir_child_open
        try:
            got = _patched("ROOT", fixture, _write_to, planted_parent / "planted" / served.name)
        finally:
            os.open = real_os_open
            os.mkdir = real_mkdir
        check("write/symlink-planted-at-missing-component-refused",
              isinstance(got, Refused) and "is a symlink, not opened as the output directory" in str(got)
              and os.listdir(planted_victim) == [], (got, os.listdir(planted_victim)))

        # The documented O_NOFOLLOW scope, final component only: an existing directory behind a symlinked
        # parent is opened (and then judged by identity and read-back path), while the same symlink as the
        # nearest existing ancestor of a missing path is the component the open names and is refused.
        linked_real = Path(tmp) / "linked-real"
        os.makedirs(linked_real / "existing")
        os.symlink(linked_real, Path(tmp) / "linkdir")
        got = _patched("ROOT", fixture, _write_to, Path(tmp) / "linkdir" / "existing" / served.name)
        check("bound/symlinked-parent-existing-dir-opened",
              got is None and (linked_real / "existing" / served.name).read_bytes() == expected, (got,))
        got = _patched("ROOT", fixture, _write_to, Path(tmp) / "linkdir" / "missing" / served.name)
        check("bound/symlinked-final-missing-component-refused",
              isinstance(got, Refused) and "is a symlink, not opened as the output directory" in str(got)
              and not os.path.exists(linked_real / "missing"), (got,))

        # main(): an output directory whose identity is a served assets directory, reached under a name the
        # path checks cannot refuse, exits 2 before render (the only place Chrome is started) is called.
        calls.clear()
        got = _patched("ROOT", fixture, _patched, "CHROME", sys.executable, _patched, "path_refusal",
                       lambda path: None, _patched, "render", _record, _main_exit,
                       ["--out", str(Path(tmp) / "mid" / "assets")])
        check("main/identity-refused-out-exit-2-before-chrome", got == 2 and calls == [], (got, calls))

    if FAILURES:
        print("SELF-TEST FAIL: render_og_cards {} of {} check(s) failed".format(len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: render_og_cards {} checks (both served cards, a path inside each served assets "
          "directory, a symlink in the output directory to a served card, a symlinked directory into assets, a "
          "relative path into assets and the assets directory itself are each refused, and each is accepted "
          "with the decision removed; an ordinary scratch directory and a prefix-sharing sibling are accepted; "
          "an empty or missing output directory and an unrecognized argument exit 2; a refused output directory "
          "exits 2 before Chrome, by path and by directory identity; a card path redirected to a served card is "
          "refused before Chrome and again before the write; another checkout's assets directory is refused by "
          "its components anywhere in the path, as typed or resolved through a symlink or a folded sub/..; a "
          "hard link or a late-planted symlink at a card name is replaced by the rename, the served card "
          "unchanged; an aliased assets directory, an ancestor of one and a parent swapped into an assets "
          "subdirectory are refused by device and inode; a directory above the repository root is accepted; a "
          "directory named like served assets is refused before it is created; a symlinked output directory is "
          "never followed; an interrupted write removes its temporary file, the file's own creation included; "
          "a parent swapped to another checkout's served assets after the typed-name check is refused by the "
          "path read back from the opened descriptor, and a directory whose path cannot be read back is "
          "refused; a refused race creates no directory inside served assets, from the write or from the "
          "pre-render identity check, while missing components of an accepted directory are still created "
          "through checked parents; an ancestor the walk cannot open fails closed with the cause stated; "
          "a directory renamed into served assets after the checks is refused by the post-write recheck "
          "and the card removed through the held descriptor; a .. across a symlink is refused while one "
          "across a real directory is folded and accepted; a symlink planted at a missing component and "
          "reported as ENOTDIR is refused as a symlink, not an I/O error; an existing directory behind a "
          "symlinked parent is opened while that symlink as the nearest existing ancestor of a missing "
          "path is refused; Chrome is never started)".format(len(EXECUTED)))
    return 0


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This script reads the exit status of
# the browser it launches, so under an ignored SIGCHLD it would read a failing render as a pass; it
# refuses at its entry line instead, before main() runs.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)



if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("render_og_cards.py") or main())
