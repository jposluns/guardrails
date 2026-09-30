"""Self-test harness for the #378 close vectors in the tools that carry a local copy of
_close_fd_propagating and _close_fd_yielding (check_footer, check_gensrc_failclose, check_overclaim,
check_release_cut, gen_crosswalk). It is a copy of the harness at the end of opf/tools/_journal.py, kept
here so those tools' --self-test runs without opf/tools present; keep the two in step. Each tool passes its
own module namespace, so the vectors and flips exercise that tool's helper copy and its own site.

Imported only by a self-test; nothing here runs on a production path."""

import os
import sys


class _StSentinel(Exception):
    """The in-flight exception a masking vector raises: not an OSError, so no site's `except OSError`
    converts it, and it must reach the caller as the same object."""


class _StCloseFault:
    """While active, the FIRST close of an ARMED descriptor raises EIO WITHOUT releasing it, so the
    helper's confirm-then-release path has to run; every other close is the real one. `fired` records, per
    injected failure, the descriptor's fstat at the fault (None if it was already closed): a vector whose
    fault never fired, or fired on a closed descriptor, proves nothing and is red (NOFIRE)."""

    def __init__(self):
        import errno
        self.armed = set()
        self.fired = []
        self.err = OSError(errno.EIO, "self-test injected close failure")
        self._close = os.close

    def arm(self, fd):
        self.armed.add(fd)
        return fd

    def _fake_close(self, fd):
        if fd not in self.armed:
            return self._close(fd)
        self.armed.discard(fd)
        try:
            self.fired.append(os.fstat(fd))
        except OSError:
            self.fired.append(None)
        raise self.err

    def __enter__(self):
        os.close = self._fake_close
        return self

    def __exit__(self, *exc_info):
        os.close = self._close
        return False


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


def _st_close_run(call, masking, expect):
    """Run one vector: call(fault) drives the site with the fault active. Returns its problems, each
    "TAG: detail": NOFIRE (the fault did not fire on an open descriptor), LEAK (a descriptor the call
    opened is still open), MASKED (the injected close error replaced the in-flight exception), SILENT (a
    normal-path failing close did not raise), WRONG (any other outcome). Empty means green."""
    before = _st_fd_table()
    fault = _StCloseFault()
    raised = None
    try:
        with fault:
            call(fault)
    except Exception as exc:  # noqa: BLE001  every outcome is classified below
        raised = exc
    after = _st_fd_table()
    problems = []
    if len(fault.fired) != 1 or fault.fired[0] is None:
        problems.append("NOFIRE: injected close failures {}".format(fault.fired))
    leaked = sorted(fd for fd, ident in after.items() if before.get(fd) != ident)
    for fd in leaked:
        try:
            os.close(fd)                                  # the harness's own cleanup of a flipped run
        except OSError:
            pass
    if leaked:
        problems.append("LEAK: descriptor(s) {} survived the failing close".format(leaked))
    if masking:
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
    assertion alone. `ns` is the namespace whose `_close_fd_yielding` the sites resolve at call time. A
    vector is (label, masking, flips, call, expect). The flips: A, _close_fd_yielding replaced by
    _close_fd_propagating, so every site is back to propagating (red: MASKED); B, the helper always quiet
    (red: SILENT); C, the caller-frame check removed (red: SILENT); L, the helper dropping the release
    after a failed close (red: LEAK), the companion that proves the no-descriptor-survives assertion can
    fail. Returns (failures, runs)."""
    prop = ns["_close_fd_propagating"]

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

    def leaky(fd):
        tb = sys.exc_info()[2]
        in_flight = tb is not None and tb.tb_frame is sys._getframe(1)
        try:
            os.close(fd)
        except OSError:
            if not in_flight:
                raise

    flips = {"A": (prop, "MASKED"), "B": (quiet, "SILENT"), "C": (frameless, "SILENT"), "L": (leaky, "LEAK")}
    real = ns["_close_fd_yielding"]
    failures, runs = [], 0
    for label, masking, want, call, expect in vectors:
        runs += 1
        got = _st_close_run(call, masking, expect)
        if got:
            failures.append("{}: {}".format(label, "; ".join(got)))
        for flip in want:
            fn, tag = flips[flip]
            ns["_close_fd_yielding"] = fn
            try:
                red = _st_close_run(call, masking, expect)
            finally:
                ns["_close_fd_yielding"] = real
            runs += 1
            if [p.split(":")[0] for p in red] != [tag]:
                failures.append("{} under flip {}: expected red by {} alone, got {}".format(
                    label, flip, tag, red or "green"))
    return failures, runs


def _st_helper_vectors(ns):
    """The helper's own vectors, calling `_close_fd_yielding` through `ns` so a flip applies."""
    def devnull(fault):
        return fault.arm(os.open(os.devnull, os.O_RDONLY))

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

    return (("helper: finally while an exception unwinds", True, "AL", mask_finally, lambda e: e is sent),
            ("helper: except handler re-raising", True, "AL", mask_except, lambda e: e is sent),
            ("helper: normal path", False, "BL", normal, None),
            ("helper: normal path under a caller's except", False, "BCL", caller_except, None))
