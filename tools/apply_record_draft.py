#!/usr/bin/env python3
"""Guarded single-target application of a drafted record: write it only when the stale pending-claim
check passes on the exact bytes that will be written.

  apply_record_draft.py --draft PATH --target REL    exit 0 written; 1 withheld (stale claim);
                                                     2 withheld (incomplete evaluation or usage)

REL is a repo-relative path declared as a surface in .aiqt/pending-pr-claims.toml. The draft is read
ONCE; those bytes are judged by tools/check_pending_pr_claims.py (live pull request state) and those
same bytes are written. The destination's preimage (its SHA-256, or its absence) is taken before the
judgement and taken again just before the write: a changed preimage withholds the write and asks for a
re-evaluation. The destination's parent directory is opened component by component from the root
without following symlinks (a symlinked or missing parent is refused, exit 2) and that descriptor is
held: every read and the write are relative to it, and a parent that no longer resolves to the held
directory just before the write withholds it. The write goes to a temporary file in that directory,
takes the existing target's permission bits (a new target gets 0666 less the umask), is flushed to
disk, and replaces the destination with os.replace. Exit 1 or 2 leaves the destination bytes
unchanged.

RESIDUALS (disclosed): pull request state can change between the read and the write, and the API read
and the file write are not one transaction; a writer that changes the destination after the second
preimage read and before os.replace is not detected. A parent directory swapped after the last check
is not followed (the write stays in the held directory), but the write then lands outside the
target's current path. Ownership, ACLs and extended attributes of the target are not carried over. This tool applies ONE target; a multi-file
application must validate the whole proposed set first and use the host's own transaction mechanism.
The private draft applier and the commit hook owner are not wired to this tool by this change.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: apply_record_draft.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import os
import stat
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pr_state  # noqa: E402
import check_pending_pr_claims as claims  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _open_parent(root, target):
    """(directory descriptor, final name) for the target's parent directory, opened component by
    component from root with O_NOFOLLOW: a symlinked (or missing) parent component is an InputError.
    The caller owns and closes the descriptor; every later read and write is relative to it."""
    parts = Path(target).parts
    if not parts:
        raise claims.InputError("{!r}: names no file".format(target))
    try:
        fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise claims.InputError("{}: the root cannot be opened ({})".format(root, exc.strerror))
    for part in parts[:-1]:
        try:
            child = os.open(part, _DIR_FLAGS, dir_fd=fd)
        except OSError as exc:
            os.close(fd)
            raise claims.InputError("{}: parent component {!r} is not a real directory (a symlink is "
                                    "refused; {})".format(target, part, exc.strerror))
        os.close(fd)
        fd = child
    return fd, parts[-1]


def _same_directory(fd, root, target):
    """True when target's parent, walked again from root without following symlinks, is still the
    directory fd holds."""
    try:
        again, _name = _open_parent(root, target)
    except claims.InputError:
        return False
    try:
        held, now = os.fstat(fd), os.fstat(again)
        return (held.st_dev, held.st_ino) == (now.st_dev, now.st_ino)
    finally:
        os.close(again)


def _preimage(dir_fd, name, label):
    """(SHA-256 hex, permission bits) of the destination, or (None, None) when it is absent;
    InputError when it is not a regular file or cannot be read. Read relative to dir_fd, never
    following a symlink."""
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=dir_fd)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise claims.InputError("{}: cannot be read; a symlink is refused ({})".format(label, exc.strerror))
    try:
        st = os.fstat(handle)
        if not stat.S_ISREG(st.st_mode):
            raise claims.InputError("{}: is not a regular file".format(label))
        with os.fdopen(handle, "rb", closefd=False) as source:
            data = source.read(claims.MAX_BYTES + 1)
    finally:
        os.close(handle)
    if len(data) > claims.MAX_BYTES:
        raise claims.InputError("{}: larger than {} bytes".format(label, claims.MAX_BYTES))
    return hashlib.sha256(data).hexdigest(), stat.S_IMODE(st.st_mode)


def apply(root, draft, target, transport=None, git_runner=_pr_state.run_git_origin, after_judge=None):
    """The exit code; the target is written only on 0. after_judge is a test seam called between the
    judgement and the second preimage read."""
    if os.path.isabs(target) or ".." in Path(target).parts:
        print("CANNOT EVALUATE: target {!r} must be a repo-relative path with no '..'".format(target),
              file=sys.stderr)
        return 2
    try:
        data = claims._read_bytes(Path(draft), draft)
        dir_fd, name = _open_parent(root, target)
    except claims.InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    try:
        return _apply_at(root, target, data, dir_fd, name, transport, git_runner, after_judge)
    finally:
        os.close(dir_fd)


def _apply_at(root, target, data, dir_fd, name, transport, git_runner, after_judge):
    try:
        before, mode = _preimage(dir_fd, name, target)
    except claims.InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    code = claims.judge_draft_bytes(root, target, data, transport, git_runner)
    if code != 0:
        print("WITHHELD: {} was not written (check exit {})".format(target, code), file=sys.stderr)
        return code
    if after_judge is not None:
        after_judge()
    if not _same_directory(dir_fd, root, target):
        print("WITHHELD: the parent directory of {} changed or became a symlink after it was opened; "
              "re-run to re-evaluate".format(target), file=sys.stderr)
        return 2
    try:
        now, mode = _preimage(dir_fd, name, target)
    except claims.InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    if now != before:
        print("WITHHELD: {} changed after the draft was judged; re-run to re-evaluate".format(target),
              file=sys.stderr)
        return 2
    temp = ".draft-{}-{}".format(os.getpid(), os.urandom(8).hex())
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        # A new target gets the ordinary creation mode (0666 less the umask); an existing one keeps
        # its permission bits.
        handle = os.open(temp, flags, 0o666, dir_fd=dir_fd)
    except OSError as exc:
        print("CANNOT EVALUATE: {} could not be written ({})".format(target, exc.strerror), file=sys.stderr)
        return 2
    try:
        with os.fdopen(handle, "wb") as out:
            if mode is not None:
                os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except OSError as exc:
        try:
            os.unlink(temp, dir_fd=dir_fd)
        except OSError:
            pass
        print("CANNOT EVALUATE: {} could not be written ({})".format(target, exc.strerror), file=sys.stderr)
        return 2
    try:
        os.fsync(dir_fd)
    except OSError:
        pass  # the replace is done; a directory sync is best effort
    print("WRITTEN: {} ({} bytes, judged and written from one read)".format(target, len(data)))
    return 0


def main():
    argv = sys.argv[1:]
    if len(argv) == 4 and argv[0] == "--draft" and argv[2] == "--target":
        return apply(ROOT, argv[1], argv[3])
    print("error: usage: apply_record_draft.py --draft PATH --target REL; fail closed", file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The judgement starts gh and git and reads
# their exit status (subprocess), so under an ignored SIGCHLD a failing read would read as a pass; the
# guard runs at the entry line, before main() dispatches.
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
    sys.exit(_child_status_refusal_exit("apply_record_draft.py") or main())
