"""Self-test harness for the #378 close vectors in the tools that carry a local copy of
_close_fd_propagating and _close_fd_yielding (check_footer, check_gensrc_failclose, check_overclaim,
check_release_cut, gen_crosswalk) or of _close_fd_propagating alone (import_cwe). It is a copy of the harness at the end of opf/tools/_journal.py, kept
here so those tools' --self-test runs without opf/tools present; keep the two in step. Each tool passes its
own module namespace, so the vectors and flips exercise that tool's helper copy and its own site.
check_release_cut compares this whole file: the harness byte for byte against _journal's, and what comes
ahead of it (this docstring and the two imports) against the exact bytes it records.

Imported only by a self-test; nothing here runs on a production path."""

import os
import sys


class _StSentinel(Exception):
    """The in-flight exception a masking vector raises: not an OSError, so no site's `except OSError`
    converts it, and it must reach the caller as the same object."""


class _StCloseFault:
    """While active, the FIRST close of an ARMED descriptor fails the way close(2) documents for Linux:
    the number is released first (a real close), an unrelated descriptor then takes it (the reuse another
    thread may make the moment the number is free), and only then does the close raise EIO (or the errno
    the arm names). Every other close is the real one. `fired` records, per injected failure, the
    descriptor's fstat taken BEFORE the close (None if it was already closed): a vector whose fault never
    fired, or fired on a closed descriptor, proves nothing and is red (NOFIRE). The reuser is a pipe end
    put on the number with dup2, inline or, armed with thread=True, by a real second thread that takes
    the number while the close is still in progress and checks afterwards that it still owns it; settle()
    reports each reuser that lost its number (REUSE). While `watch` is set, every later os.close of a
    released number by the faulting thread, and every call that inspects it by number (_ST_WATCHED: os.stat
    given the number itself, os.fstat, os.fstatvfs, os.lseek, os.get_inheritable, os.isatty, fcntl.fcntl,
    fcntl.flock, fcntl.lockf, fcntl.ioctl), is recorded in `probes` (PROBE)."""

    def __init__(self, watch=True):
        import errno
        import threading
        self.armed = {}
        self.fired = []
        self.probes = []
        self.lost = []
        self.watch = watch
        self.err = OSError(errno.EIO, "self-test injected close failure")
        self._close = os.close
        self._fstat = os.fstat
        fcntl = _st_fcntl()
        self._watched = [(module, name, getattr(module, name)) for module, names in ((os, _ST_WATCHED[0]),
                         (fcntl, _ST_WATCHED[1])) if module for name in names if hasattr(module, name)]
        self._threading = threading
        self._released = set()
        self._faulting = None
        self._inline = []
        self._threads = []

    def arm(self, fd, errnum=None, thread=False):
        if errnum is not None:
            self.err = OSError(errnum, "self-test injected close failure")
        state = None
        if thread:
            ev = self._threading.Event
            state = {"fd": None, "ident": None, "owned": None, "started": ev(), "go": ev(), "ready": ev(),
                     "done": ev()}
            worker = self._threading.Thread(target=self._reuse_in_thread, args=(state,), daemon=True)
            worker.start()
            state["started"].wait(10)
            self._threads.append((worker, state))
        self.armed[fd] = state
        return fd

    def _reuse_in_thread(self, state):
        """The second thread: its pipe is opened BEFORE the number is free, so dup2 is what puts it there."""
        rfd, wfd = os.pipe()
        try:
            state["started"].set()
            state["go"].wait(10)
            if state["fd"] is not None:
                os.dup2(rfd, state["fd"])
                st = self._fstat(state["fd"])
                state["ident"] = (st.st_dev, st.st_ino)
        finally:
            self._close(rfd)
            self._close(wfd)
            state["ready"].set()
        if state["ident"] is None or not state["done"].wait(10):
            return
        try:
            st = self._fstat(state["fd"])
            state["owned"] = (st.st_dev, st.st_ino) == state["ident"]
        except OSError:
            state["owned"] = False
        if state["owned"]:
            self._close(state["fd"])                      # the second thread releases what it still owns

    def _fake_close(self, fd):
        if fd in self._released and self.watch and self._threading.get_ident() == self._faulting:
            self.probes.append(("close", fd))
        if fd not in self.armed:
            return self._close(fd)
        state = self.armed.pop(fd)
        try:
            self.fired.append(self._fstat(fd))
        except OSError:
            self.fired.append(None)
        if self.fired[-1] is not None:
            self._faulting = self._threading.get_ident()
            if state is None:
                rfd, wfd = os.pipe()                      # opened before the release, never on fd itself
                try:
                    self._close(fd)
                    self._released.add(fd)
                    os.dup2(rfd, fd)
                    st = self._fstat(fd)
                    self._inline.append((fd, (st.st_dev, st.st_ino)))
                finally:
                    self._close(rfd)
                    self._close(wfd)
            else:
                self._close(fd)
                self._released.add(fd)
                state["fd"] = fd
                state["go"].set()
                if not state["ready"].wait(10) or state["ident"] is None:
                    self.lost.append((fd, "the second thread never took the number"))
        raise self.err

    def _watcher(self, name, real):
        """The stand-in for one watched call: it records a released number it is given, then calls the real
        one. Only an int is a number (os.stat of a path, or fcntl of a file object, is not one)."""
        def watched(fd, *args, **kwargs):
            if type(fd) is int and fd in self._released and self.watch \
                    and self._threading.get_ident() == self._faulting:
                self.probes.append((name, fd))
            return real(fd, *args, **kwargs)
        return watched

    def settle(self):
        """After the call: confirm each reuser still owns its number, then release it. Returns the losses."""
        for fd, ident in self._inline:
            try:
                st = self._fstat(fd)
                owned = (st.st_dev, st.st_ino) == ident
            except OSError:
                owned = False
            if owned:
                self._close(fd)
            else:
                self.lost.append((fd, "the inline reuser lost the number"))
        for worker, state in self._threads:
            state["go"].set()                             # an unfired arm: the thread exits untouched
            state["done"].set()
            worker.join(10)
            if state["ident"] is not None and not state["owned"]:
                self.lost.append((state["fd"], "the second thread lost the number"))
        self._inline, self._threads = [], []
        return self.lost

    def __enter__(self):
        os.close = self._fake_close
        self._stand_ins = []
        for module, name, real in self._watched:
            watched = self._watcher(name, real)
            setattr(module, name, watched)
            for table in _st_supports(module):
                if real in table:                         # a capability probe (os.stat in
                    table.add(watched)                    # os.supports_dir_fd) still finds the call
                    self._stand_ins.append((table, watched))
        return self

    def __exit__(self, *exc_info):
        os.close = self._close
        for module, name, real in self._watched:
            setattr(module, name, real)
        for table, watched in self._stand_ins:
            table.discard(watched)
        return False


# The calls _StCloseFault watches, from os and from fcntl, each taking the descriptor number first. Not
# watched: calls that use a number without inspecting it (read, write, dup, fsync, fchmod, ...), a number
# passed as dir_fd= to a path call, and a number reached through a wrapping object (os.fdopen, socket).
_ST_WATCHED = (("stat", "fstat", "fstatvfs", "lseek", "get_inheritable", "isatty"),
               ("fcntl", "flock", "lockf", "ioctl"))


def _st_supports(module):
    """The capability sets of `module` (os.supports_dir_fd and its kin) a stand-in must join while active."""
    tables = (getattr(module, name, None) for name in ("supports_dir_fd", "supports_fd",
                                                        "supports_follow_symlinks", "supports_effective_ids"))
    return [table for table in tables if isinstance(table, set)]


def _st_fcntl():
    """The fcntl module, or None where it does not exist (it is POSIX-only): there nothing can call
    fcntl.fcntl, flock, lockf or ioctl, so there is nothing to watch there, and the fcntl probe flip (F)
    is not run."""
    try:
        import fcntl
    except ImportError:
        return None
    return fcntl


class _StCensusError(RuntimeError):
    """The descriptor census could not read a descriptor: cannot-evaluate, never a closed descriptor."""


def _st_fd_table():
    """The open descriptors below 1024 and the file each names, so a leak is found even when its number
    is reused by a different file. Each is keyed on (st_dev, st_ino, anonymous-inode kind): anonymous-inode
    descriptors of many kinds share one (st_dev, st_ino), so the kind, read from the descriptor's /proc/self/fd
    link ("anon_inode:[eventpoll]", "anon_inode:[eventfd]"; None for any other file), is what tells an epoll
    descriptor from an eventfd put at its number. Residual: two anonymous-inode descriptors of the same kind
    share every field, so one replaced at its number by another of its own kind still reads as unchanged; and
    where /proc/self/fd does not exist (not Linux, or Linux without /proc mounted) every link read is ENOENT,
    so the kind is None throughout and the table is keyed on (st_dev, st_ino) alone. Only EBADF on the fstat
    reads as closed, and only ENOENT on the link read (the number has no entry: closed since its fstat) reads
    as no kind; any other read error raises _StCensusError naming the descriptor, never omitting it as closed
    (a leak check fails closed on input it cannot read)."""
    import errno
    table = {}
    for fd in range(1024):
        try:
            st = os.fstat(fd)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                continue
            raise _StCensusError("descriptor census cannot evaluate descriptor {}: {!r}".format(fd, exc))
        try:
            link = os.readlink("/proc/self/fd/{}".format(fd))
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise _StCensusError("descriptor census cannot evaluate descriptor {}: {!r}".format(fd, exc))
            link = ""
        table[fd] = (st.st_dev, st.st_ino, link if link.startswith("anon_inode:") else None)
    return table


def _st_anon_reuse(table):
    """(detected, shared) for an anonymous-inode replacement, or None where there is no epoll or eventfd (not
    Linux): a held epoll descriptor is in the baseline, then an eventfd is put at its number (dup2).
    `detected` is whether `table()` reads that as a change; `shared` is whether the two have one (st_dev,
    st_ino), where a table of those alone reads the replacement as unchanged and only the anonymous-inode
    kind tells them apart. Every descriptor it opens is closed once on every path: `held` is owned from the
    dup on, under the finally that also covers the epoll's own close, so a raising close leaves nothing open."""
    import select
    if not hasattr(select, "epoll") or not hasattr(os, "eventfd"):
        return None
    poll = select.epoll()
    held = None
    try:
        try:
            held = os.dup(poll.fileno())
        finally:
            poll.close()
        before = os.fstat(held)
        baseline = table()
        other = os.eventfd(0)
        try:
            os.dup2(other, held)
        finally:
            os.close(other)
        after = os.fstat(held)
        shared = (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
        return table() != baseline, shared
    finally:
        if held is not None:
            os.close(held)


def _st_close_run(call, masking, expect, watch=True):
    """Run one vector: call(fault) drives the site with the fault active. Returns its problems, each
    "TAG: detail": NOFIRE (the fault did not fire on an open descriptor), REUSE (the descriptor that took
    the released number no longer owns it: the number was closed again), PROBE (the faulting thread closed
    the released number, or inspected it by a call _ST_WATCHED names, after the failed close; checked only
    while `watch`),
    LEAK (a descriptor the call opened is still open), MASKED (the injected close error replaced the in-flight
    exception), SILENT (a normal-path failing close did not raise), WRONG (any other outcome). `masking` is
    True where an exception is in flight, False on a normal path whose close error must raise, and None on
    a quiet teardown path that must swallow it. Empty means green."""
    before = _st_fd_table()
    fault = _StCloseFault(watch)
    raised = None
    try:
        with fault:
            call(fault)
    except Exception as exc:  # noqa: BLE001  every outcome is classified below
        raised = exc
    lost = fault.settle()
    after = _st_fd_table()
    problems = []
    if len(fault.fired) != 1 or fault.fired[0] is None:
        problems.append("NOFIRE: injected close failures {}".format(fault.fired))
    if lost:
        problems.append("REUSE: a released number was closed again under its new owner {}".format(lost))
    if fault.probes:
        problems.append("PROBE: a released number was touched again {}".format(fault.probes))
    # A deliberately leaked number is reported and left open: the harness records no opens, so it cannot
    # prove the number is still the tested call's own. Another thread may have reused it (a released
    # number included) through os.open, builtin open() or any other route, so closing it could close
    # someone else's descriptor (#378 P1).
    leaked = sorted(fd for fd, ident in after.items() if before.get(fd) != ident)
    if leaked:
        problems.append("LEAK: descriptor(s) {} survived the failing close".format(leaked))
    if masking is None:
        if raised is not None:
            problems.append("WRONG: expected the quiet close to swallow its error, got {!r}".format(raised))
    elif masking:
        if raised is fault.err:
            problems.append("MASKED: the injected close error replaced the in-flight exception")
        elif raised is None or not expect(raised):
            problems.append("WRONG: expected the in-flight exception, got {!r}".format(raised))
    elif raised is None:
        problems.append("SILENT: the failing normal-path close did not raise")
    elif raised is not fault.err:
        problems.append("WRONG: expected the injected close error, got {!r}".format(raised))
    return problems


def _st_close_check(ns, vectors):
    """Run each vector green, then under each flip it names, requiring the flip turn it red by its own
    assertion alone. `ns` is the namespace whose close helpers the sites resolve at call time. A vector is
    (label, masking, flips, call, expect). The flips: A, _close_fd_yielding replaced by
    _close_fd_propagating, so every site is back to propagating (red: MASKED); B, the helper always quiet
    (red: SILENT); C, the caller-frame check removed (red: SILENT); R (RECLOSE), the pre-P1 bodies of
    _close_fd_propagating and _close_fd_quietly put back, which fstat the number after a failed close and
    close it again when it looks open (red: REUSE alone, so this leg runs with the PROBE watch off); P, an
    fstat probe after the failed close with no second close (red: PROBE); F, the same probe made with
    fcntl.fcntl(fd, F_GETFD) (red: PROBE; run where fcntl exists); S, _close_fd_quietly replaced by
    the propagating close, so a cleanup loop stops at the first failing close (red: LEAK and WRONG).
    Returns (failures, runs)."""
    prop = ns.get("_close_fd_propagating")

    def quiet(fd):
        try:
            prop(fd)
        except OSError:
            pass

    def frameless(fd):
        if sys.exc_info()[2] is None:
            prop(fd)
            return
        try:
            prop(fd)
        except OSError:
            pass

    def reclose(swallow):
        def body(fd):
            try:
                os.close(fd)
                return
            except OSError as exc:
                first = exc
            try:
                os.fstat(fd)
            except OSError:
                if swallow:
                    return
                raise first
            try:
                os.close(fd)                              # the retry close(2) warns against
            except OSError:
                pass
            if not swallow:
                raise first
        return body

    def probe(swallow, touch=lambda fd: os.fstat(fd)):
        def body(fd):
            try:
                os.close(fd)
                return
            except OSError as exc:
                first = exc
            try:
                touch(fd)
            except OSError:
                pass
            if not swallow:
                raise first
        return body

    fcntl = _st_fcntl()

    flips = {"A": ({"_close_fd_yielding": prop}, ("MASKED",), True),
             "B": ({"_close_fd_yielding": quiet}, ("SILENT",), True),
             "C": ({"_close_fd_yielding": frameless}, ("SILENT",), True),
             "R": ({"_close_fd_propagating": reclose(False), "_close_fd_quietly": reclose(True)}, ("REUSE",),
                   False),
             "P": ({"_close_fd_propagating": probe(False), "_close_fd_quietly": probe(True)}, ("PROBE",),
                   True),
             "F": ({"_close_fd_propagating": probe(False, lambda fd: fcntl.fcntl(fd, fcntl.F_GETFD)),
                    "_close_fd_quietly": probe(True, lambda fd: fcntl.fcntl(fd, fcntl.F_GETFD))}, ("PROBE",),
                   True),
             "S": ({"_close_fd_quietly": prop}, ("LEAK", "WRONG"), True)}
    failures, runs = [], 0
    for label, masking, want, call, expect in vectors:
        if fcntl is None:
            want = want.replace("F", "")
        runs += 1
        got = _st_close_run(call, masking, expect)
        if got:
            failures.append("{}: {}".format(label, "; ".join(got)))
        for flip in want:
            swaps, tags, watch = flips[flip]
            real = {name: ns[name] for name in swaps if name in ns}
            ns.update({name: swaps[name] for name in real})
            try:
                red = _st_close_run(call, masking, expect, watch)
            finally:
                ns.update(real)
            runs += 1
            if not real or tuple(p.split(":")[0] for p in red) != tags:
                failures.append("{} under flip {}: expected red by {} alone, got {}".format(
                    label, flip, " and ".join(tags), red or ("green" if real else "no helper to flip")))
    return failures, runs


def _st_helper_vectors(ns):
    """The helpers' own vectors, calling each close helper through `ns` so a flip applies: the harness's
    own watch check, the four _close_fd_yielding vectors, then for each helper `ns` defines, V1 (the
    released number reused inline, once per errno, EINTR and EIO; red under R, P and F) and V2 (reused by a
    real second thread)."""
    import errno

    def devnull(fault, **arm):
        return fault.arm(os.open(os.devnull, os.O_RDONLY), **arm)

    sent = _StSentinel("in flight")

    def mask_finally(fault):
        fd = devnull(fault)
        try:
            raise sent
        finally:
            ns["_close_fd_yielding"](fd)

    def mask_except(fault):
        fd = devnull(fault)
        try:
            raise sent
        except _StSentinel:
            ns["_close_fd_yielding"](fd)
            raise

    def normal(fault):
        ns["_close_fd_yielding"](devnull(fault))

    def inner(fd):
        ns["_close_fd_yielding"](fd)

    def caller_except(fault):
        try:
            raise _StSentinel("handled by the caller")
        except _StSentinel:
            inner(devnull(fault))                         # reached normally from the caller's except block

    def direct(name, **arm):
        def call(fault):
            ns[name](devnull(fault, **arm))
        return call

    def watch(fault):
        """The watch itself: the watched set is exactly every touch below, each on the module it belongs to
        (an fcntl name on fcntl, never on os), each stand-in answers every capability probe as the real call
        does, and after a failed close each watched call on the released number is recorded once, under its
        own name, and os.stat of a path is not; the probes are then cleared and the close error raised, so a
        watch that drops or misplaces a call, misses one, records a path, or hides a call from
        os.supports_dir_fd is red by WRONG."""
        for module, name, real in fault._watched:
            for table in _st_supports(module):
                if (getattr(module, name) in table) != (real in table):
                    raise AssertionError("the stand-in for {} changes a capability probe".format(name))
        fd = devnull(fault)
        try:
            os.close(fd)
        except OSError as exc:
            first = exc
        else:
            raise AssertionError("the armed close did not fail")
        fcntl = _st_fcntl()
        os.stat(os.devnull)
        touches = ((os, "stat", lambda: os.stat(fd)), (os, "fstat", lambda: os.fstat(fd)),
                   (os, "fstatvfs", lambda: os.fstatvfs(fd)), (os, "lseek", lambda: os.lseek(fd, 0, os.SEEK_CUR)),
                   (os, "get_inheritable", lambda: os.get_inheritable(fd)), (os, "isatty", lambda: os.isatty(fd)))
        if fcntl:
            touches += ((fcntl, "fcntl", lambda: fcntl.fcntl(fd, fcntl.F_GETFD)),
                        (fcntl, "flock", lambda: fcntl.flock(fd, fcntl.LOCK_UN)),
                        (fcntl, "lockf", lambda: fcntl.lockf(fd, fcntl.LOCK_UN)),
                        (fcntl, "ioctl", lambda: fcntl.ioctl(fd, 0)))
        watched = sorted((module.__name__, name) for module, name, _real in fault._watched)
        required = sorted((module.__name__, name) for module, name, _touch in touches if hasattr(module, name))
        if watched != required:
            raise AssertionError("the PROBE watch covers {}, expected {}".format(watched, required))
        expected = []
        for module, name, touch in touches:
            if hasattr(module, name):
                expected.append((name, fd))
                try:
                    touch()
                except OSError:
                    pass
        seen, fault.probes[:] = list(fault.probes), []
        if seen != expected:
            raise AssertionError("the PROBE watch recorded {}, expected {}".format(seen, expected))
        raise first

    vectors = (("harness: the PROBE watch records every watched call on a released number", False, "", watch,
                None),)
    if "_close_fd_yielding" in ns:
        vectors += (("helper: finally while an exception unwinds", True, "AR", mask_finally,
                     lambda e: e is sent),
                    ("helper: except handler re-raising", True, "AR", mask_except, lambda e: e is sent),
                    ("helper: normal path", False, "BR", normal, None),
                    ("helper: normal path under a caller's except", False, "BCR", caller_except, None))
    for name, masking in (("_close_fd_propagating", False), ("_close_fd_quietly", None),
                          ("_close_fd_yielding", False)):
        if name not in ns:
            continue
        for errnum in (errno.EINTR, errno.EIO):
            vectors += (("helper {} V1: {} after the number is released and reused".format(
                name, errno.errorcode[errnum]), masking, "RPF", direct(name, errnum=errnum), None),)
        vectors += (("helper {} V2: a second thread takes the released number".format(name), masking, "R",
                     direct(name, thread=True), None),)
    return vectors


def _st_watch_drop_check():
    """The watch self-check's own flips: with fcntl.flock, then fcntl.ioctl, dropped from _ST_WATCHED, the
    watch vector must be red by WRONG alone, so losing either call from the watched set cannot pass
    unnoticed. Run where fcntl exists (elsewhere there is no fcntl name to drop). Returns (failures, runs)."""
    if _st_fcntl() is None:
        return [], 0
    label, masking, _flips, call, expect = _st_helper_vectors(globals())[0]
    real = _ST_WATCHED
    failures, runs = [], 0
    for dropped in ("flock", "ioctl"):
        globals()["_ST_WATCHED"] = (real[0], tuple(name for name in real[1] if name != dropped))
        try:
            red = _st_close_run(call, masking, expect)
        finally:
            globals()["_ST_WATCHED"] = real
        runs += 1
        if [problem.split(":")[0] for problem in red] != ["WRONG"]:
            failures.append("{} with {} dropped from the watch: expected red by WRONG alone, got {}".format(
                label, dropped, red or "green"))
    return failures, runs


# F-JOURNAL-HELD-FD-LISTING: the deterministic stale-listing seam. A listing read through a held directory
# descriptor can miss every entry created since that descriptor was opened, but only on some filesystems
# (btrfs does; tmpfs does not), so a vector that relies on the native behaviour discriminates only where its
# fixture happens to sit. Under this seam the stale view is served on ANY filesystem, so each fresh-descriptor
# listing site's vector fails if that site lists through the held descriptor or through a dup of it.

class _StStaleScandir:
    """The STALE view of a real os.scandir iterator: only the entries named at _StStaleListing.hold() (the
    real DirEntry objects, filtered), with the iterator's context-manager and close behaviour."""

    def __init__(self, it, names):
        self._it, self._names = it, names

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __iter__(self):
        return self

    def __next__(self):
        for entry in self._it:
            if entry.name in self._names:
                return entry
        raise StopIteration

    def close(self):
        self._it.close()


class _StStaleListing:
    """A deterministic model of a STALE directory listing (F-JOURNAL-HELD-FD-LISTING), so a vector proves
    that a listing reads through a FRESH descriptor whatever filesystem its fixture is on.

    hold(fd) records a held directory descriptor and the entry names its directory had at that moment (a
    vector calls it right after the open, before it creates the late entry). While the seam is entered (with
    seam:), a listing (os.listdir or os.scandir given an int) of a held directory is served
      STALE (only the entries named at hold(), less any since removed) through the held descriptor itself and
      through ANY descriptor that shares its open file description (os.dup, os.dup2, fcntl F_DUPFD and
      F_DUPFD_CLOEXEC). Sharing is decided by the kernel, not by bookkeeping: O_NONBLOCK, a status flag of
      the open file description, is flipped on the listed descriptor and read back on the held one;
      CURRENT (the real listing) only through a descriptor os.open returned for "." relative to the held one
      (dir_fd=held) that shares no open file description with a held one;
      STALE through any other descriptor of the held directory (a path-based reopen, say).
    Listings of any other directory, and of a path, are the real ones. Each decision is appended to `views`
    ("stale" or "current"), so a vector can also require that every listing of the held directory was fresh.
    hold_on_open(match, after) holds the first descriptor os.open returns for which match(path, kwargs) is
    true and then calls after(fd): the vector's late entry, created inside the code's own open-to-list
    window. recording(opener) wraps an opener captured before the seam was entered (one that bypasses the
    patched os.open) so its "." opens are recorded too. The seam's own vectors: _st_stale_listing_check.

    Imported only by a self-test; nothing here runs on a production path."""

    def __init__(self):
        self.held = {}                            # fd -> ((st_dev, st_ino), the entry names at hold())
        self.fresh = {}                           # fd -> (st_dev, st_ino) of the held directory it reopens
        self.views = []
        self._match = self._after = None
        self._saved = None
        self._real_open, self._real_close = os.open, os.close
        self._real_listdir, self._real_scandir = os.listdir, os.scandir

    def _ident(self, fd):
        st = os.fstat(fd)
        return (st.st_dev, st.st_ino)

    def hold(self, fd):
        lfd = self._real_open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        try:
            names = frozenset(self._real_listdir(lfd))
        finally:
            self._real_close(lfd)
        self.held[fd] = (self._ident(fd), names)

    def hold_on_open(self, match, after):
        self._match, self._after = match, after

    def _note(self, fd, path, kwargs):
        parent = kwargs.get("dir_fd")
        if path == "." and parent in self.held:
            self.fresh[fd] = self.held[parent][0]
        elif self._match is not None and self._match(path, kwargs):
            self._match = None
            self.hold(fd)
            self._after(fd)

    def recording(self, opener):
        def recorded(path, flags, *args, **kwargs):
            fd = opener(path, flags, *args, **kwargs)
            self._note(fd, path, kwargs)
            return fd
        return recorded

    def _shares(self, fd, held):
        before, mine = os.get_blocking(held), os.get_blocking(fd)
        os.set_blocking(fd, not mine)
        try:
            return os.get_blocking(held) != before
        finally:
            os.set_blocking(fd, mine)

    def _view(self, fd):
        ident = self._ident(fd)
        held = [h for h, (hid, _names) in self.held.items() if hid == ident]
        if not held:
            return None, None
        for h in held:
            if fd == h or self._shares(fd, h):
                return "stale", self.held[h][1]
        if self.fresh.get(fd) == ident:
            return "current", None
        return "stale", self.held[held[0]][1]

    def _listing(self, real, args, kwargs, wrap):
        target = args[0] if args else kwargs.get("path")
        view, names = self._view(target) if isinstance(target, int) else (None, None)
        if view is None:
            return real(*args, **kwargs)
        self.views.append(view)
        listed = real(*args, **kwargs)
        return listed if view == "current" else wrap(listed, names)

    def _open(self, path, flags, *args, **kwargs):
        return self.recording(self._real_open)(path, flags, *args, **kwargs)

    def _close(self, fd):
        self.held.pop(fd, None)
        self.fresh.pop(fd, None)
        self._real_close(fd)

    def _listdir(self, *args, **kwargs):
        return self._listing(self._real_listdir, args, kwargs,
                             lambda listed, names: [name for name in listed if name in names])

    def _scandir(self, *args, **kwargs):
        return self._listing(self._real_scandir, args, kwargs, _StStaleScandir)

    def __enter__(self):
        self._saved = (os.open, os.close, os.listdir, os.scandir)
        os.open, os.close, os.listdir, os.scandir = self._open, self._close, self._listdir, self._scandir
        return self

    def __exit__(self, *exc):
        os.open, os.close, os.listdir, os.scandir = self._saved


def _st_stale_listing_check(base):
    """The stale-listing seam's own vectors, on whatever filesystem `base` (an existing directory) is on: with
    `early` present at hold() and `late` created after it, the held descriptor, an os.dup, an os.dup2, a fcntl
    F_DUPFD and F_DUPFD_CLOEXEC dup of it and a path-based reopen each list only `early` (os.listdir and
    os.scandir), a "." reopen relative to the held descriptor lists both, and an unrelated directory lists
    its real entries; leaving the seam restores the os functions. Returns (failures, checks)."""
    import fcntl
    root = os.path.join(base, "stale-listing-seam")
    os.makedirs(os.path.join(root, "dir", "early"))
    os.makedirs(os.path.join(root, "other", "entry"))
    failures, checks, fds = [], 0, []
    try:
        held = os.open(os.path.join(root, "dir"), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fds.append(held)
        with _StStaleListing() as seam:
            seam.hold(held)
            os.mkdir("late", dir_fd=held)
            spare = os.open(os.devnull, os.O_RDONLY)
            fds.append(spare)
            os.dup2(held, spare)
            cases = [("held", held, ["early"]), ("os.dup2", spare, ["early"])]
            for label, opener, want in (
                    ("os.dup", lambda: os.dup(held), ["early"]),
                    ("F_DUPFD", lambda: fcntl.fcntl(held, fcntl.F_DUPFD, 0), ["early"]),
                    ("F_DUPFD_CLOEXEC", lambda: fcntl.fcntl(held, fcntl.F_DUPFD_CLOEXEC, 0), ["early"]),
                    ("path reopen", lambda: os.open(os.path.join(root, "dir"), os.O_RDONLY | os.O_DIRECTORY),
                     ["early"]),
                    ("fresh '.' reopen", lambda: os.open(".", os.O_RDONLY | os.O_DIRECTORY, dir_fd=held),
                     ["early", "late"]),
                    ("unrelated", lambda: os.open(os.path.join(root, "other"), os.O_RDONLY | os.O_DIRECTORY),
                     ["entry"])):
                fds.append(opener())                  # recorded at once, so the finally closes it
                cases.append((label, fds[-1], want))
            for label, number, want in cases:
                with os.scandir(number) as it:
                    scanned = sorted(entry.name for entry in it)
                for listing, got in (("os.listdir", sorted(os.listdir(number))), ("os.scandir", scanned)):
                    checks += 1
                    if got != want:
                        failures.append("stale-listing seam: {} through the {} descriptor: expected {}, got "
                                        "{}".format(listing, label, want, got))
        checks += 1
        if (os.open, os.close, os.listdir, os.scandir) != (seam._real_open, seam._real_close,
                                                           seam._real_listdir, seam._real_scandir):
            failures.append("stale-listing seam: leaving the seam did not restore the os functions")
    finally:
        for fd in fds:
            os.close(fd)
    return failures, checks
