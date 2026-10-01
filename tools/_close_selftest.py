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
    reports each reuser that lost its number (REUSE). While `watch` is set, every later os.fstat,
    os.close or fcntl.fcntl of a released number by the faulting thread is recorded in `probes` (PROBE)."""

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
        self._fcntl_module = _st_fcntl()
        self._fcntl = self._fcntl_module and self._fcntl_module.fcntl
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

    def _fake_fstat(self, fd, *args, **kwargs):
        if fd in self._released and self.watch and self._threading.get_ident() == self._faulting:
            self.probes.append(("fstat", fd))
        return self._fstat(fd, *args, **kwargs)

    def _fake_fcntl(self, fd, *args, **kwargs):
        if fd in self._released and self.watch and self._threading.get_ident() == self._faulting:
            self.probes.append(("fcntl", fd))
        return self._fcntl(fd, *args, **kwargs)

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
        os.fstat = self._fake_fstat
        if self._fcntl_module:
            self._fcntl_module.fcntl = self._fake_fcntl
        return self

    def __exit__(self, *exc_info):
        os.close = self._close
        os.fstat = self._fstat
        if self._fcntl_module:
            self._fcntl_module.fcntl = self._fcntl
        return False


def _st_fcntl():
    """The fcntl module, or None where it does not exist (it is POSIX-only): there nothing can call
    fcntl.fcntl, so there is nothing to watch, and the fcntl probe flip (F) is not run."""
    try:
        import fcntl
    except ImportError:
        return None
    return fcntl


def _st_fd_table():
    """The open descriptors below 1024 and the file each names, so a leak is found even when its number
    is reused by a different file."""
    table = {}
    for fd in range(1024):
        try:
            st = os.fstat(fd)
        except OSError:
            continue
        table[fd] = (st.st_dev, st.st_ino)
    return table


def _st_close_run(call, masking, expect, watch=True):
    """Run one vector: call(fault) drives the site with the fault active. Returns its problems, each
    "TAG: detail": NOFIRE (the fault did not fire on an open descriptor), REUSE (the descriptor that took
    the released number no longer owns it: the number was closed again), PROBE (the faulting thread
    fstat'ed, fcntl'ed or closed the released number after the failed close; checked only while `watch`),
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
    leaked = sorted(fd for fd, ident in after.items() if before.get(fd) != ident)
    for fd in leaked:
        try:
            os.close(fd)                                  # the harness's own cleanup of a flipped run
        except OSError:
            pass
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
    """The helpers' own vectors, calling each close helper through `ns` so a flip applies: the four
    _close_fd_yielding vectors, then for each helper `ns` defines, V1 (the released number reused inline,
    once per errno, EINTR and EIO; red under R, P and F) and V2 (reused by a real second thread)."""
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

    vectors = ()
    if "_close_fd_yielding" in ns:
        vectors = (("helper: finally while an exception unwinds", True, "AR", mask_finally,
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
