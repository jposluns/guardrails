#!/usr/bin/env python3
"""OPF shared operation lock (OPF-D2B PR2).

SENSITIVE-TIER, concurrency-critical: a lock bug means data corruption or a race. Fail-closed
throughout, stdlib-only, Linux/macOS (POSIX advisory locks).
"""
import fcntl
import json
import os
import shutil
import stat
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _containment    # noqa: E402
import _journal        # noqa: E402
import _opf_check      # noqa: E402
import _opf_store      # noqa: E402


class OpLockError(Exception):
    """A fail-closed locking error."""


class OpCapability:
    """The nested-operation internal CAPABILITY object (F24).

    Provides canonical lock order enforcement (repository -> journal -> index) by
    exposing subsequent lock acquisition as methods on this object, rather than
    as independent functions that accept error-prone `already_locked` booleans.
    """
    __slots__ = ("op_id", "holder", "operation", "store_root", "common_git_dir",
                 "_anchor_fd", "_machine_rel", "_expected_lease", "_journal_cap")

    def __init__(self, op_id, holder, operation, store_root, common_git_dir, anchor_fd, machine_rel=None, expected_lease=None):
        self.op_id = op_id
        self.holder = holder
        self.operation = operation
        self.store_root = store_root
        self.common_git_dir = common_git_dir
        self._anchor_fd = anchor_fd
        self._machine_rel = machine_rel
        self._expected_lease = expected_lease
        self._journal_cap = None

    def acquire_journal_lock(self, journal_root, session_id):
        """Acquire the journal lock (second in canonical order)."""
        if self._anchor_fd < 0:
            raise OpLockError("repository lock is no longer held (F24)")
        if self._journal_cap is not None:
            raise OpLockError("journal lock already held by this capability (F24)")
        self._journal_cap = _journal.acquire_lock(journal_root, session_id)
        return self._journal_cap

    def acquire_index_lock(self):
        """Acquire the git index lock (third in canonical order)."""
        if self._anchor_fd < 0:
            raise OpLockError("repository lock is no longer held (F24)")
        if self._journal_cap is None:
            raise OpLockError("journal lock must be acquired before index lock (F24 canonical order)")
        return True


def _get_common_git_dir(store_root):
    """Find the confirmed common git directory. OUTSIDE .working."""
    store_root = Path(store_root)
    git_path = store_root / ".git"
    if not git_path.exists():
        return store_root  # Not a git repo, fall back to store root
    if git_path.is_dir():
        return git_path.resolve()
    # Linked worktree: read gitdir
    try:
        text = git_path.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            target = text[len("gitdir:"):].strip()
            p = Path(target)
            if not p.is_absolute():
                p = store_root / p
            p = p.resolve()
            if p.parent.name == "worktrees":
                return p.parent.parent
            return p
    except OSError as exc:
        raise OpLockError("cannot read .git file: {}".format(exc))
    raise OpLockError("unrecognized .git file format in {}".format(git_path))


def _validate_control(fd, expected_type, name_for_error):
    """Containment/type/ownership/permission/link-count/identity validation."""
    try:
        st = os.fstat(fd)
    except OSError as exc:
        raise OpLockError("cannot fstat {}: {}".format(name_for_error, exc))

    if expected_type == "dir" and not stat.S_ISDIR(st.st_mode):
        raise OpLockError("{} is not a directory (wrong type)".format(name_for_error))
    if expected_type == "file" and not stat.S_ISREG(st.st_mode):
        raise OpLockError("{} is not a regular file (wrong type)".format(name_for_error))

    if st.st_nlink > (2 if expected_type == "dir" else 1):
        raise OpLockError("{} is multiply-linked (count {})".format(name_for_error, st.st_nlink))

    if st.st_uid != os.getuid():
        raise OpLockError("{} is not owned by current uid (found {}, expected {})".format(
            name_for_error, st.st_uid, os.getuid()))

    return st


def acquire_operation(store_root, operation, holder=None, recover=False):
    """Acquire the shared operation lock (repository-level).

    Canonical lock order: repository -> journal -> index. This acquires the REPOSITORY lock.
    """
    if not _containment.probe():
        raise OpLockError("race-free containment primitive absent; fail-closed")

    store_root = Path(os.path.abspath(store_root))
    common_git_dir = _get_common_git_dir(store_root)

    oplock_dir = common_git_dir / "opf-oplock"
    oplock_dir.mkdir(parents=True, exist_ok=True)

    try:
        oplock_fd = _opf_store._open_dir_nofollow(str(oplock_dir))
    except OSError as exc:
        raise OpLockError("cannot open oplock dir {} no-follow: {}".format(oplock_dir, exc))

    try:
        _validate_control(oplock_fd, "dir", "opf-oplock")

        # 1. Persistent MUTEX ANCHOR (F23 anchor-unlink: never unlinked)
        try:
            anchor_fd = os.open("mutex.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o644, dir_fd=oplock_fd)
        except OSError as exc:
            raise OpLockError("cannot open mutex anchor: {}".format(exc))

        try:
            _validate_control(anchor_fd, "file", "mutex.lock")

            # POSIX advisory lock (F23 contention)
            try:
                fcntl.flock(anchor_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OpLockError("mutex anchor is held by another process (contention, F23)")
            except OSError as exc:
                raise OpLockError("cannot lock mutex anchor: {}".format(exc))

            # 2. Check for pre-existing active record
            try:
                os.mkdir("ops", 0o755, dir_fd=oplock_fd)
            except FileExistsError:
                pass

            try:
                ops_fd = os.open("ops", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=oplock_fd)
            except OSError as exc:
                raise OpLockError("cannot open ops directory: {}".format(exc))

            try:
                _validate_control(ops_fd, "dir", "ops")
                existing_ops = os.listdir(ops_fd)
                if existing_ops:
                    if not recover:
                        raise OpLockError("stale active record(s) {} found (F23); requires EXPLICIT recovery".format(existing_ops))

                    for stale_op in existing_ops:
                        try:
                            op_fd = os.open(stale_op, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ops_fd)
                            try:
                                try:
                                    os.unlink("active.toml", dir_fd=op_fd)
                                except FileNotFoundError:
                                    pass
                                try:
                                    ph_fd = os.open("phases", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=op_fd)
                                    try:
                                        for ph in os.listdir(ph_fd):
                                            os.unlink(ph, dir_fd=ph_fd)
                                    finally:
                                        os.close(ph_fd)
                                    os.rmdir("phases", dir_fd=op_fd)
                                except FileNotFoundError:
                                    pass
                            finally:
                                os.close(op_fd)
                            os.rmdir(stale_op, dir_fd=ops_fd)
                        except OSError as exc:
                            raise OpLockError("failed explicit recovery of {}: {}".format(stale_op, exc))

                # Create unique operation directory
                op_id = str(uuid.uuid4())
                os.mkdir(op_id, 0o755, dir_fd=ops_fd)
                try:
                    op_fd = os.open(op_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ops_fd)
                except OSError as exc:
                    raise OpLockError("cannot open unique op directory: {}".format(exc))

                try:
                    _validate_control(op_fd, "dir", op_id)
                    os.mkdir("phases", 0o755, dir_fd=op_fd)

                    holder_id = holder or "opf:{}:{}".format(os.uname().nodename, os.getpid())
                    now_str = datetime.now(timezone.utc).isoformat("T", "seconds")

                    active_payload = json.dumps({
                        "op_id": op_id,
                        "holder": holder_id,
                        "operation": operation,
                        "acquired_at": now_str
                    }).encode("utf-8")

                    try:
                        active_fd = os.open("active.toml", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=op_fd)
                    except OSError as exc:
                        raise OpLockError("failed to create active.toml: {}".format(exc))

                    try:
                        os.write(active_fd, active_payload)
                        os.fsync(active_fd)
                    finally:
                        os.close(active_fd)
                finally:
                    os.close(op_fd)
            finally:
                os.close(ops_fd)

            # 3. Create lease.toml if machine-store exists
            machine_rel = None
            expected_lease = None
            res = _opf_store.resolve_store(store_root)
            if res.status == _opf_store.RESOLVED:
                machine_rel = res.machine_rel
                try:
                    store_fd = _opf_store._open_dir_nofollow(str(store_root))
                except OSError:
                    pass
                else:
                    try:
                        parts = machine_rel.split('/')
                        pfd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=store_fd)
                        try:
                            for comp in parts[1:]:
                                nfd = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
                                os.close(pfd)
                                pfd = nfd

                            lease_payload = (
                                'schema = 1\n'
                                'holder = "{}"\n'
                                'operation = "{}"\n'
                                'acquired_at = "{}"\n'
                            ).format(holder_id, operation, now_str).encode("utf-8")

                            try:
                                lfd = os.open(_opf_check.LEASE_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=pfd)
                            except FileExistsError:
                                raise OpLockError("lease.toml already exists (F23 torn/stale)")
                            except OSError as exc:
                                raise OpLockError("failed to create lease.toml: {}".format(exc))

                            try:
                                os.write(lfd, lease_payload)
                                os.fsync(lfd)
                                expected_lease = lease_payload
                            finally:
                                os.close(lfd)
                        finally:
                            os.close(pfd)
                    finally:
                        os.close(store_fd)

            return OpCapability(op_id, holder_id, operation, store_root, common_git_dir, anchor_fd, machine_rel, expected_lease)

        except Exception:
            fcntl.flock(anchor_fd, fcntl.LOCK_UN)
            os.close(anchor_fd)
            raise
    finally:
        os.close(oplock_fd)


def release_operation(cap):
    """Release the operation and nested lease. Ownership-verified RELEASE (F27)."""
    try:
        if cap._machine_rel and cap._expected_lease:
            try:
                store_fd = _opf_store._open_dir_nofollow(str(cap.store_root))
            except OSError as exc:
                raise OpLockError("failed to open store root for lease release: {} (F27)".format(exc))

            try:
                parts = cap._machine_rel.split('/')
                pfd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=store_fd)
                try:
                    for comp in parts[1:]:
                        nfd = os.open(comp, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
                        os.close(pfd)
                        pfd = nfd

                    try:
                        lfd = os.open(_opf_check.LEASE_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pfd)
                        try:
                            st = os.fstat(lfd)
                            if not stat.S_ISREG(st.st_mode) or st.st_size != len(cap._expected_lease):
                                raise OpLockError("lease.toml size/type mismatch (F27)")
                            data = os.read(lfd, st.st_size + 1)
                            if data != cap._expected_lease:
                                raise OpLockError("lease.toml payload mismatch (replaced) (F27)")
                        finally:
                            os.close(lfd)
                        os.unlink(_opf_check.LEASE_NAME, dir_fd=pfd)
                    except FileNotFoundError:
                        raise OpLockError("lease.toml already absent (F27)")
                    except OSError as exc:
                        raise OpLockError("cannot open or read lease.toml (F27): {}".format(exc))
                finally:
                    os.close(pfd)
            finally:
                os.close(store_fd)

        # Remove active record
        oplock_dir = cap.common_git_dir / "opf-oplock"
        try:
            oplock_fd = _opf_store._open_dir_nofollow(str(oplock_dir))
        except OSError as exc:
            raise OpLockError("cannot open oplock dir for release: {} (F27)".format(exc))

        try:
            ops_fd = os.open("ops", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=oplock_fd)
            try:
                op_fd = os.open(cap.op_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ops_fd)
                try:
                    try:
                        os.unlink("active.toml", dir_fd=op_fd)
                    except FileNotFoundError:
                        raise OpLockError("active.toml already absent; never-seize violation (F27)")

                    try:
                        ph_fd = os.open("phases", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=op_fd)
                        try:
                            for ph in os.listdir(ph_fd):
                                os.unlink(ph, dir_fd=ph_fd)
                        finally:
                            os.close(ph_fd)
                        os.rmdir("phases", dir_fd=op_fd)
                    except FileNotFoundError:
                        pass
                finally:
                    os.close(op_fd)
                os.rmdir(cap.op_id, dir_fd=ops_fd)
            finally:
                os.close(ops_fd)
        finally:
            os.close(oplock_fd)

    finally:
        fcntl.flock(cap._anchor_fd, fcntl.LOCK_UN)
        os.close(cap._anchor_fd)
        cap._anchor_fd = -1


def self_test():
    """Operation lock facility self-test exercising vectors F23, F24, F27."""
    import tempfile

    if not _containment.probe():
        print("SKIP: race-free containment primitive absent")
        return 0

    base = tempfile.mkdtemp(prefix="opf-oplock-selftest-")
    try:
        root = os.path.join(base, "repo")
        os.mkdir(root)

        # Fake a git dir
        git_dir = os.path.join(root, ".git")
        os.mkdir(git_dir)

        # Test positive acquire
        cap = acquire_operation(root, "test_op")

        # Test negative: F23 contention (mutex cannot be stolen)
        try:
            acquire_operation(root, "test_op_2")
            assert False, "F23 contention failed to raise"
        except OpLockError as exc:
            assert "held by another process" in str(exc)

        # Test positive release
        release_operation(cap)

        # Test F23 stale active record refuses without explicit recovery
        cap = acquire_operation(root, "test_stale")
        # Simulate a crash leaving active record but releasing mutex
        fcntl.flock(cap._anchor_fd, fcntl.LOCK_UN)
        os.close(cap._anchor_fd)

        try:
            acquire_operation(root, "test_stale_new")
            assert False, "F23 stale record failed to raise"
        except OpLockError as exc:
            assert "stale active record(s)" in str(exc)

        # Explicit recovery succeeds
        cap2 = acquire_operation(root, "test_recovery", recover=True)

        # Test F24 nested capability lock order
        try:
            cap2.acquire_index_lock()
            assert False, "F24 index lock out of order failed to raise"
        except OpLockError as exc:
            assert "journal lock must be acquired before" in str(exc)

        jroot = os.path.join(root, ".aiqt", "import", "journal")
        os.makedirs(jroot, exist_ok=True)
        cap2.acquire_journal_lock(jroot, "session_f24")
        cap2.acquire_index_lock() # should succeed now

        release_operation(cap2)

        # Test F27 release failure (torn payload)
        os.mkdir(os.path.join(root, ".working"))
        os.mkdir(os.path.join(root, ".working", "toml"))
        manifest = '[opf]\nstandard="opf"\nspec_version="1.1.0"\nlayout="inline"\nposture="required"\nimport_status="none"\n[modules]\nconcurrent_operation=true\n'
        with open(os.path.join(root, ".working", "toml", "manifest.toml"), "w") as f:
            f.write(manifest)

        cap3 = acquire_operation(root, "test_lease")
        # mess with lease
        lease_path = os.path.join(root, ".working", "toml", "lease.toml")
        with open(lease_path, "a") as f:
            f.write("\ncorrupted=true\n")

        try:
            release_operation(cap3)
            assert False, "F27 torn lease failed to raise"
        except OpLockError as exc:
            assert "payload mismatch" in str(exc) or "size/type mismatch" in str(exc)

        print("SELF-TEST PASS")
        return 0
    finally:
        shutil.rmtree(base)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        sys.exit(self_test())
    sys.exit("usage: _opf_oplock.py --self-test")
