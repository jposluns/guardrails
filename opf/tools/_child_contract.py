#!/usr/bin/env python3
"""The fail-closed child contract (secfcl), implemented ONCE (OPF-SELF-CONTAIN; merge train 2 QA r6).

Two children in this repository run other code and must prove the structured verdict their parent
accepts survived that code's cleanup: the OPF self-test unit child (opf/tools/opf.py,
_cmd_self_test_unit) and the hook-scripts parity child (tools/check_hook_scripts.py, parity_child).
Through merge train 2 QA rounds 2 to 5 each carried its OWN copy of the contract, and every round's
fix had to reach every copy; round 6 found the round-5 settle rule still accepting incomplete
cleanup in both copies at once. This module is the single implementation, extracted on the _semver
precedent: it lives under opf/tools/ so the OPF subtree stays dependency-closed (hold H-12; the
standalone-closure gate observes it), and tools/ callers import it from here, one source and no
fork. Its own self-test (python3 -I -B _child_contract.py --self-test, also registered as the
opf-child-contract unit of `opf.py --self-test`) exercises every channel below through real child
interpreters.

THE CONTRACT, in the order it runs:

1. RECORD FROM AN EXIT HANDLER REGISTERED FIRST. register() registers the record handler and
   requires it to be the only one (another exit handler already registered would be skipped by the
   handler's own os._exit, so register() unregisters and returns False and the caller refuses by
   name). register() also duplicates file descriptors 1 and 2 and saves them (saved_stdout,
   saved_stderr), before the loaded or tested code runs, for the writes that come after the final
   check (step 6). Exit handlers run last-registered-first, so being first puts the record AFTER every
   cleanup handler the loaded or tested code registers, and code that ends the process from its
   cleanup -- an os._exit, a fault that kills the process, a signal -- prevents the record, and the
   parent fails closed on the missing record (merge train 2 QA r2, codex MAJOR).
2. CLEANUP PROVEN COMPLETE. The handler collects cyclic garbage REPEATEDLY, at most GC_PASS_BOUND
   passes, until one pass CHANGES NOTHING: gc.collect() freed no object AND the identity set of
   gc-tracked objects is the same after the pass as before it (collection_pass_changed_nothing;
   the measurement's own artefacts are excluded). A pass that frees nothing does NOT settle on its
   own, and neither does one that leaves no more objects tracked than before it: a finalizer that
   RESURRECTS its object is not counted as freed (QA r5), and one that also RELEASES other tracked
   objects while it creates new cyclic cleanup work leaves the tracked COUNT equal or lower
   (QA r6), so both count rules accepted incomplete cleanup; the identity comparison sees the
   released and the created objects themselves, the pass does not settle, and the next pass
   collects the new work, running its finalizers before the record (QA r4). Between passes the
   reporting machinery is re-checked and restored, so a finalizer that replaces a stream or hook
   mid-collection is found. A run that never settles inside the bound is a fault, by name, never
   a verdict over cleanup work that cannot be shown complete.
3. REPORTING MACHINERY GUARDED. The reporting snapshot (sys.stderr, sys.excepthook,
   sys.unraisablehook, threading.excepthook) is taken at construction, before the loaded or tested
   code runs. arm_audit() arms an audit hook that sees every FAULT_EVENTS event: CPython raises
   `sys.unraisablehook` and `sys.excepthook` audit events BEFORE it calls whatever hook is
   installed, and an audit hook cannot be removed, so a silenced hook or a replaced stream cannot
   hide such a fault from it (merge train 2 QA r3, claude MINORs 1 and 2). restore_reporting()
   puts every replaced piece back and names it; the record handler runs it before, between and
   after the collection passes, and any replacement it finds, the original stderr closed, a fault
   event the audit hook saw, or a stream flush failure is a fault.
4. OS._EXIT WITH THE DECIDED CODE. With the collection settled and the machinery restored, the
   handler writes the caller's fault line (file descriptor 2) when there are faults, calls the
   caller's record callback with the decided code (the settled code, or fail_code when any fault
   was recorded), and ends the process ITSELF with os._exit(code), so no interpreter finalization
   runs after the record. The callback writes through the Python-level streams only what may come
   BEFORE the final check, and RETURNS the SEAL: the (descriptor, bytes) pairs that must come
   after it (a result's completeness terminator, written to saved_stdout), which the handler writes
   with os.write only when the record stands (step 6). The decided code is used ONLY when the callback RETURNED NORMALLY: an
   exception the callback raises (a stdout flush that fails after the result was written), or
   one raised anywhere in the handler before it, is caught, named in a one-line diagnostic on
   file descriptor 2 (best effort, itself guarded), and the exit is fail_code, so a record the
   callback wrote before it failed is never carried by a success exit (merge train 2 QA r7,
   codex MAJOR: a `finally: os._exit(code)` swallowed the exception and exited with the success
   code).
5. THE DECISION RE-CHECKED AFTER THE RECORD. A callback that RETURNS normally can still fault
   without raising: the record it writes runs whatever the loaded or tested code installed (a
   wrapped stdout, say), and that code can raise an unraisable exception (a finalizer that raises
   during a flush) and report it through a hook it installs only for the moment (merge train 2 QA
   r8, codex MAJOR, reproduced: the decided code was never reconsidered, and the parity child
   exited 0 with a complete result). So after the callback returns the handler runs _decide's
   checks AGAIN -- collection settled, reporting machinery restored, streams flushed, the
   audit-recorded faults -- and any fault NEW since the callback started voids the record: a
   one-line diagnostic on file descriptor 2 and the exit is fail_code. Each channel a callback
   can fault through without raising, and how it is handled:
     - an unraisable exception (a finalizer, an ignored exception in a flush): the audit hook sees
       the `sys.unraisablehook` event whatever hook is installed, so it is a new fault; only the
       DELETED-hook window of residual (b) hides it, in the callback as in cleanup;
     - a THREAD it starts: the interpreter joined its threads BEFORE the exit handlers ran, and
       the os._exit would end a thread started after that unobserved, so the audit hook counts
       every `_thread.start_joinable_thread` and `_thread.start_new_thread` event, and a thread
       started anywhere inside the record handler (by the callback or by a finalizer the
       collection runs) is a fault by name, whether it faults or not;
     - a WRITE TO THE ERROR STREAM: not seen by the child; both parents refuse it (the parity
       parent requires an empty error stream, the OPF unit runner one holding nothing but its
       boundary line and declared rows);
     - a REPLACED or SILENCED hook or stream: one left replaced when the re-check's flushes and
       collection passes have run (including one replaced BY those flushes or passes, step 6) is
       found, restored and refused by name; one replaced and put back inside the callback or
       inside the re-check's flushes hides no unraisable or excepthook fault (the audit event
       fires whatever hook is installed), and residual (a) (a thread's fault under a
       threading.excepthook replaced and put back) still applies to a thread started BEFORE the
       handler, while one started inside it is refused by the thread rule above.
6. NOTHING THE CHECKED CODE CAN REACH RUNS AFTER THE LAST CHECK (the ordering invariant; merge
   train 2 QA r9, codex MAJOR, reproduced: the re-check restored the machinery and settled the
   collection BEFORE its own sys.stdout and sys.stderr flushes, so a stdout wrapper whose SECOND
   flush replaced a hook, or dropped cyclic garbage whose finalizer raises, did so after the checks
   that cover it, and the parity child exited 0 with a complete result). Each check (_check) runs
   FIRST every operation that can execute code the loaded or tested module controls: the
   Python-level flushes of sys.stdout and sys.stderr (a wrapper's write or flush), then the
   collection passes (finalizers, gc callbacks, audit hooks on gc.get_objects), each pass judged
   by its own after-comparison; and only THEN the checks that cover them: the machinery restored
   and compared, the thread starts counted. After the FINAL check the handler runs only what that
   code cannot reach: os.write on saved_stdout and saved_stderr (the seal, or a diagnostic) and
   os._exit; no Python-level stream write or flush, no collection, no callback. The handler blocks
   every blockable signal in its thread when it starts, so a signal handler the loaded code
   installed does not run after the final check (one already pending runs at the next check's
   entry, before the checks; residual (c) bounds this for other threads).

DISCLOSED RESIDUALS -- the channels this contract CANNOT close, disclosed here ONCE (the two
callers' docstrings point here instead of restating them):

  (a) a THREAD's uncaught exception reaches threading.excepthook with no audit event before it, so
      cleanup code that replaces that hook and puts it back before the record hides the fault from
      this child; a replacement still in place at the record is found and refused, and the default
      hook's report lands on the child's error stream, which both parents refuse.
  (b) while cleanup code has DELETED sys.unraisablehook, CPython reports an unraisable fault (an
      exit handler's exception, a finalizer's) by writing it to sys.stderr with no audit event, so
      cleanup code that deletes that hook, replaces sys.stderr and restores both before the record
      hides such a fault (merge train 2 QA r5, codex MEDIUM, reproduced; the self-test measures
      it: the same fault with the hook present, even silenced, is caught by the audit event, so
      DELETED is the precise wording).
  (c) an object still REACHABLE at the record is never finalized (the os._exit ends the child), so
      its finalizer neither runs nor faults. Likewise a thread that cleanup code started BEFORE the
      record handler ran (an earlier exit handler's), still running at the record, is ended by
      the os._exit unobserved; only a thread started inside the record handler is refused. Such a
      thread also runs CONCURRENTLY with the handler, so step 6's ordering does not bind it: it can
      act after the final check (and a signal delivered to it runs its Python handler in the
      handler's thread at any point); the self-test measures the plain case (it passes).
  (d) the identity comparison is by id(): a finalizer that frees tracked objects and creates
      replacements that receive the SAME addresses within one pass, the counts equal, reads as a
      pass that changed nothing (CPython reuses freed addresses; not reproduced -- the probing
      hosts allocated fresh addresses for the reproduction shapes, which free list objects and
      create instances of new classes).

The parent-side halves of the contract stay with the parents: what each accepts (a complete record
bound to the child, an empty error stream), and the channel by which reviewed, pinned loaded code
replaces the reporting machinery outright (a forged record over a clean exit), each disclosed
there. Offline, stdlib only; imported by opf.py as a sibling and by tools/ callers through an
explicit opf/tools path insert.
"""
import gc
import os
import signal
import sys

# How many collection passes the record handler may make before one settles (changes nothing). A
# deeper chain of finalizers that keep creating work is refused by name, never shown complete.
GC_PASS_BOUND = 10
# The audit events CPython raises BEFORE it hands an exception to the reporting machinery
# (Python/errors.c: `sys.unraisablehook`; Python/pythonrun.c: `sys.excepthook`).
FAULT_EVENTS = frozenset(("sys.unraisablehook", "sys.excepthook"))
# The audit events CPython raises when it starts a thread (Modules/_threadmodule.c). A thread
# started inside the record handler runs after the interpreter's thread join and is ended by the
# os._exit unobserved, so the handler counts these and refuses one started inside it (QA r8).
THREAD_EVENTS = frozenset(("_thread.start_joinable_thread", "_thread.start_new_thread"))
# The reporting machinery a cleanup fault is reported through: the error stream and the three
# hooks CPython hands an uncaught or unraisable exception.
REPORTING = (("sys", "stderr"), ("sys", "excepthook"), ("sys", "unraisablehook"),
             ("threading", "excepthook"))


def reporting_snapshot():
    """The current reporting machinery, [(module, attribute, object)], for restore_reporting."""
    import threading
    owners = {"sys": sys, "threading": threading}
    return [(owners[owner], name, getattr(owners[owner], name, None))
            for owner, name in REPORTING]


def _tracked_ids():
    """The id set of every gc-tracked object. gc.get_objects() excludes the list it returns, and
    the set is built only after that snapshot, so neither artefact is in it."""
    objects = gc.get_objects()
    ids = set(map(id, objects))
    del objects
    return ids


def collection_pass_changed_nothing():
    """One garbage-collection pass; True when it CHANGED NOTHING: it freed no object and the
    identity set of tracked objects after it equals the set before it. The before-set, alive
    across the pass, is discarded from the after side (it is this function's own artefact). A
    resurrecting finalizer frees nothing, and one that releases tracked objects while creating
    new cyclic work keeps the count equal or lower, but either way the SET differs, so the pass
    does not read as settled (merge train 2 QA r5 and r6, codex MEDIUMs); residual (d) of the
    module docstring is the one shape this comparison cannot see. CPython's collector also
    untracks eligible tuples and dicts during a pass, which reads as a change; that settles by
    itself on a following pass."""
    before = _tracked_ids()
    freed = gc.collect()
    after = _tracked_ids()
    after.discard(id(before))
    return freed == 0 and after == before


class FailClosedChild:
    """One child's contract instance (the module docstring states the contract). `record(code)`
    writes the caller's structured verdict (the OPF completion record; the parity result and its
    terminator -- it may write nothing for a failing code when the failure is carried by the exit
    alone); `fault_line(faults)` returns the caller's named fault bytes for file descriptor 2;
    `fail_code` is the caller's cannot-evaluate exit. The reporting snapshot is taken HERE, at
    construction, before the loaded or tested code runs. `record` may RETURN the seal, a sequence
    of (descriptor, bytes) pairs written with os.write after the final check when the record
    stands (contract step 6); the descriptor is saved_stdout or one the caller saved as early."""

    def __init__(self, record, fault_line, fail_code=2):
        self.faults = []
        self.saved_stdout = self.saved_stderr = None
        self.fail_code = fail_code
        self._record = record
        self._fault_line = fault_line
        self._settled = []
        self._threads_started = []
        self._snapshot = reporting_snapshot()

    def register(self):
        """Register the record handler FIRST, before the caller imports or runs anything that
        registers an exit handler. True when it is the only exit handler; otherwise it is
        unregistered and False returned, and the caller refuses by name (an earlier handler would
        be skipped by the record's os._exit)."""
        import atexit
        atexit.register(self._record_at_exit)
        if getattr(atexit, "_ncallbacks", lambda: 1)() != 1:
            atexit.unregister(self._record_at_exit)
            return False
        # The descriptors the writes after the final check use (contract step 6), duplicated before
        # the loaded or tested code runs; they stay open until the handler's os._exit. One that
        # cannot be saved is a fault (the record then carries fail_code and no seal is written).
        try:
            self.saved_stdout = os.dup(1)
            self.saved_stderr = os.dup(2)
        except OSError as exc:
            self.faults.append("the output descriptors could not be saved ({})".format(
                type(exc).__name__))
        return True

    def arm_audit(self):
        """Arm the audit hook that sees every FAULT_EVENTS event, whatever hook or stream is
        installed. Callers arm it at their guard's start: the parity child before it loads either
        file, the unit child when the unit returns (a unit may legitimately exercise those hooks
        while it runs). An arming failure is itself a fault."""
        def audit(event, _args):
            if event in FAULT_EVENTS:
                self.faults.append("a cleanup fault reached {}".format(event))
            elif event in THREAD_EVENTS:
                self._threads_started.append(event)
        try:
            sys.addaudithook(audit)
        except Exception as exc:
            self.faults.append("the cleanup audit hook could not be armed ({})".format(
                type(exc).__name__))

    def restore_reporting(self):
        """Put every piece of the reporting machinery back where cleanup code replaced it, and
        return what was found replaced (or the snapshot's error stream closed, which cannot be
        undone), as names; an empty list means the machinery was intact."""
        swapped = []
        for owner, name, original in self._snapshot:
            if getattr(owner, name, None) is not original:
                swapped.append("{}.{}".format(owner.__name__, name))
                setattr(owner, name, original)
        if getattr(self._snapshot[0][2], "closed", True):
            swapped.append("sys.stderr (closed)")
        return swapped

    def settle(self, code):
        """The caller's verdict is in: the record handler may now write. An unsettled child leaves
        no record, and the parent fails closed on its absence."""
        self._settled.append(code)

    def _record_at_exit(self):
        # Registered FIRST, so it runs LAST among the exit handlers: after every cleanup handler
        # the loaded or tested code registered. An earlier handler that ends the process prevents
        # this write, and the parent fails closed on the missing record (secfcl).
        if not self._settled:
            return   # never settled: no record, and the parent fails closed
        stage = "before the record"
        try:
            if hasattr(signal, "pthread_sigmask"):
                # A signal handler the loaded code installed must not run after the final check
                # (contract step 6): blocked here, a signal stays pending until the os._exit.
                signal.pthread_sigmask(signal.SIG_BLOCK, signal.valid_signals())
            code = self._decide(self._settled[0], len(self._threads_started))
            seen, threads = len(self.faults), len(self._threads_started)
            stage = "in the record callback"
            seal = [(fd, bytes(data)) for fd, data in (self._record(code) or ())]
            # The callback returned normally, but it may have faulted without raising (module
            # docstring, contract step 5): the same checks again, the FINAL check, and a fault new
            # since the callback started voids the record (merge train 2 QA r8, codex MAJOR).
            stage = "after the record callback"
            self._check(threads)
            # From here on only os.write on the saved descriptors and the os._exit (contract
            # step 6, merge train 2 QA r9): no flush, no collection, no callback.
            if len(self.faults) > seen:
                code = self.fail_code
                self._callback_fault(self.faults[seen:])
            else:
                stage = "sealing the record"
                for fd, data in seal:
                    while data:
                        data = data[os.write(fd, data):]
        except BaseException as exc:
            # Never a `finally: os._exit(code)`: that swallowed the exception and kept the
            # success code (merge train 2 QA r7, codex MAJOR). A handler that did not return
            # normally exits fail_code, whatever the callback wrote before it raised.
            code = self.fail_code
            self._handler_fault(stage, exc)
        os._exit(code)

    def _callback_fault(self, faults):
        """The one-line diagnostic for a fault observed during a record callback that returned
        normally: best effort and itself guarded (the fail_code exit carries the failure whether
        or not this line is written)."""
        try:
            os.write(self._error_fd(), ("child-contract-fault: a fault was observed during the record callback "
                         "({}), so any record it wrote is void; exit {} (secfcl)\n".format(
                             "; ".join(faults)[:1000], self.fail_code)).encode("utf-8", "replace"))
        except BaseException:
            pass

    def _handler_fault(self, stage, exc):
        """The one-line diagnostic for a record handler that raised: best effort and itself
        guarded (the fail_code exit carries the failure whether or not this line is written)."""
        try:
            os.write(self._error_fd(), ("child-contract-fault: the record handler raised {} {}, so any record it "
                         "wrote is void; exit {} (secfcl)\n".format(
                             type(exc).__name__, stage, self.fail_code)).encode("utf-8", "replace"))
        except BaseException:
            pass

    def _error_fd(self):
        """The descriptor for this handler's own diagnostics: saved_stderr, or 2 when it could not
        be saved (that is already a fault)."""
        return 2 if self.saved_stderr is None else self.saved_stderr

    def _decide(self, code, threads):
        """Run the checks (_check) and return the code the record carries (fail_code, with the
        caller's fault line on file descriptor 2, when any fault was recorded)."""
        self._check(threads)
        if self.faults:
            code = self.fail_code
            try:
                os.write(self._error_fd(), self._fault_line(self.faults))
            except OSError:
                pass   # the fail_code exit still carries the fault
        return code

    def _check(self, threads):
        """Flush the streams, settle the collection and restore the reporting machinery, adding
        a fault for each check that fails, and one for any thread started since the audit hook
        had counted `threads` thread starts. Run before the record and again after the record
        callback returns (QA r8). The ORDER is contract step 6: the flushes (a wrapper the loaded
        code installed runs in them) and then the collection passes (finalizers, gc callbacks)
        come FIRST, each pass judged by its own after-comparison, and the machinery comparison and
        the thread count come LAST, so nothing runs after them that could invalidate either (QA
        r9: the flushes used to come after the machinery check and the collection)."""
        replaced = self.restore_reporting()
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception as exc:
            self.faults.append("the streams could not be flushed ({})".format(type(exc).__name__))
        replaced += self.restore_reporting()
        settled = False
        for _ in range(GC_PASS_BOUND):
            if collection_pass_changed_nothing():
                settled = True
                break
            replaced += self.restore_reporting()
        if not settled:
            self.faults.append(
                "garbage collection still freed or created objects after {} passes, so cleanup "
                "work its finalizers keep creating cannot be shown complete".format(GC_PASS_BOUND))
        replaced += self.restore_reporting()
        if replaced:
            self.faults.append("cleanup code replaced the reporting machinery ({})".format(
                ", ".join(sorted(set(replaced)))))
        started = len(self._threads_started) - threads
        if started:
            self.faults.append(
                "{} thread start(s) inside the record handler, after the interpreter joined its "
                "threads, so the work and any fault of such a thread cannot be seen".format(started))


# --- self-test -----------------------------------------------------------------------------------
# Every channel of the contract, exercised through REAL child interpreters: each case seeds one
# shape of loaded-code cleanup into a child built on this module (the same record/fault-line
# wiring both callers use) and asserts the child's exit code, the record it left (or provably did
# not leave), and its error stream. The disclosed-window case (b) is MEASURED, not assumed: the
# deleted-hook window passes (the residual), and the same fault with the hook present, or merely
# silenced, is caught.

_CASE_HEAD = (
    "import atexit\n"
    "import gc\n"
    "import io\n"
    "import os\n"
    "import sys\n"
    "sys.path.insert(0, sys.argv[2])\n"
    "import _child_contract\n"
    "\n"
    "\n"
    "def record(code):\n"
    "    with open(sys.argv[1], \"w\", encoding=\"utf-8\") as handle:\n"
    "        handle.write(\"record {}\\n\".format(code))\n"
    "    sys.stdout.write(\"record {}\\n\".format(code))\n"
    "    sys.stdout.flush()\n"
    "    return [(contract.saved_stdout, \"seal {}\\n\".format(code).encode(\"ascii\"))]\n"
    "\n"
    "\n"
    "def fault_line(faults):\n"
    "    return (\"child-contract-fault: {}; failing closed (secfcl)\\n\"\n"
    "            .format(\"; \".join(faults)[:1000])).encode(\"utf-8\", \"replace\")\n"
    "\n"
    "\n"
    "contract = _child_contract.FailClosedChild(record=record, fault_line=fault_line)\n")

_CASE_REGISTER = (
    "if not contract.register():\n"
    "    print(\"refused: another exit handler came first\", file=sys.stderr)\n"
    "    sys.exit(3)\n"
    "contract.arm_audit()\n")

_CASE_SETTLE = "contract.settle(0)\n"

# One seed per channel: the loaded-code cleanup shape a child runs after the contract is armed.
_SEED_BENIGN = """
class Benign:
    def __init__(self):
        self.cycle = self

    def __del__(self):
        pass


Benign()
atexit.register(lambda: None)
"""

_SEED_FAULT = "atexit.register(os.remove, \"/nonexistent-child-contract-selftest\")\n"

_SEED_CHAIN = """
class Inner:
    def __init__(self):
        self.cycle = self
        self.remove = os.remove

    def __del__(self):
        self.remove("/nonexistent-child-contract-chain")


class Outer:
    def __init__(self):
        self.cycle = self
        self.inner = Inner

    def __del__(self):
        self.inner()


atexit.register(Outer)
"""

# Merge train 2 QA r5 (codex MEDIUM): the finalizer resurrects its object, so the pass frees
# nothing, while it creates new cyclic cleanup work whose finalizer faults.
_SEED_RESURRECT = """
held = []


class Inner:
    def __init__(self):
        self.cycle = self
        self.remove = os.remove

    def __del__(self):
        self.remove("/nonexistent-child-contract-resurrect")


class Outer:
    def __init__(self):
        self.cycle = self
        self.held = held
        self.inner = Inner

    def __del__(self):
        self.held.append(self)
        self.inner()


def setup():
    gc.collect()
    gc.disable()
    Outer()


atexit.register(setup)
"""

# Merge train 2 QA r6 (codex MEDIUM): the resurrecting finalizer also RELEASES tracked objects
# while it creates the new work, so the tracked count stays equal or lower and the round-5
# count rule settled over the pending faulting Inner.
_SEED_NET_RELEASE = """
held = []


class Inner:
    def __init__(self):
        self.cycle = self

    def __del__(self):
        raise RuntimeError("new cyclic cleanup failed")


class Outer:
    def __init__(self):
        self.cycle = self
        self.held = held
        self.spare = [[] for _ in range(100)]
        self.inner = Inner

    def __del__(self):
        self.held.append(self)
        self.spare.clear()
        self.inner()


def setup():
    gc.collect()
    gc.disable()
    Outer()


atexit.register(setup)
"""

# Merge train 2 QA r6 (claude MINOR): the narrower variant, dropping exactly one tracked object.
_SEED_NET_SWAP = """
held = []


class Inner:
    def __init__(self):
        self.cycle = self

    def __del__(self):
        raise RuntimeError("new cyclic cleanup failed")


class Outer:
    def __init__(self):
        self.cycle = self
        self.held = held
        self.buf = [[]]
        self.inner = Inner

    def __del__(self):
        self.held.append(self)
        self.buf = None
        self.inner()


def setup():
    gc.collect()
    gc.disable()
    Outer()


atexit.register(setup)
"""

_SEED_UNSETTLED = """
def make_link(depth):
    class Link:
        def __init__(self, depth):
            self.cycle = self
            self.depth = depth
            self.make = make_link

        def __del__(self):
            if self.depth > 0:
                self.make(self.depth - 1)

    Link(depth)


atexit.register(make_link, _child_contract.GC_PASS_BOUND + 2)
"""

# Residual (b), measured: cleanup deletes the hook, replaces the stream, faults, restores both.
_SEED_WINDOW = """
saved = []


def hide():
    saved.append(sys.stderr)
    saved.append(sys.unraisablehook)
    sys.stderr = io.StringIO()
    del sys.unraisablehook


def fault():
    raise RuntimeError("hidden cleanup fault")


def restore():
    sys.stderr = saved[0]
    sys.unraisablehook = saved[1]


atexit.register(restore)
atexit.register(fault)
atexit.register(hide)
"""

# The control: the same fault with the hook PRESENT (stream still replaced and restored); the
# audit event fires, so only the DELETED hook opens the window.
_SEED_WINDOW_CONTROL = """
saved = []


def hide():
    saved.append(sys.stderr)
    sys.stderr = io.StringIO()


def fault():
    raise RuntimeError("hidden cleanup fault")


def restore():
    sys.stderr = saved[0]


atexit.register(restore)
atexit.register(fault)
atexit.register(hide)
"""

# Merge train 2 QA r7 (codex MAJOR), the reproduction: loaded code wraps sys.stdout so a flush
# raises once anything was written. The handler's own pre-record flush passes (nothing written
# yet); the record callback writes its genuine record and then its flush raises. The pinned
# `finally: os._exit(code)` swallowed that and exited 0 with an empty error stream.
_SEED_RECORD_FLUSH = """
class Output:
    def __init__(self, stream):
        self.stream, self.written = stream, False

    def write(self, text):
        result = self.stream.write(text)
        self.written = True
        return result

    def flush(self):
        self.stream.flush()
        if self.written:
            raise RuntimeError("result flush failed")


sys.stdout = Output(sys.stdout)
"""

# Merge train 2 QA r8 (codex MAJOR), the reproduction: the wrapped stdout's flush, once anything
# was written, installs an unraisable hook that absorbs the report, drops an object whose
# finalizer raises, and puts the hook back. The callback returns normally; the pinned handler
# never reconsidered its decided code and exited 0 with an empty error stream. The audit event
# fired, so the re-check after the callback sees the new fault.
_SEED_RECORD_UNRAISABLE = """
class Raiser:
    def __del__(self):
        raise RuntimeError("fault during result flush")


class Output:
    def __init__(self, stream):
        self.stream, self.written, self.log = stream, False, []

    def write(self, text):
        result = self.stream.write(text)
        self.written = True
        return result

    def flush(self):
        self.stream.flush()
        if self.written:
            saved = sys.unraisablehook
            sys.unraisablehook = lambda unraisable: self.log.append(unraisable.exc_value)
            Raiser()
            sys.unraisablehook = saved


sys.stdout = Output(sys.stdout)
"""

# QA r8, the thread channel: the record's write starts a thread, which the os._exit would end
# unobserved (the interpreter joined its threads before the exit handlers ran).
_SEED_RECORD_THREAD = """
import threading


class Output:
    def __init__(self, stream):
        self.stream, self.started = stream, False

    def write(self, text):
        if not self.started:
            self.started = True
            threading.Thread(target=lambda: None).start()
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()


sys.stdout = Output(sys.stdout)
"""

# QA r8, the same thread rule before the record: a finalizer the settling collection runs starts
# a thread (automatic collection disabled, so the object is still pending at the record).
_SEED_FINALIZER_THREAD = """
import threading


class Starter:
    def __init__(self):
        self.cycle = self
        self.thread = threading.Thread

    def __del__(self):
        self.thread(target=lambda: None).start()


def setup():
    gc.collect()
    gc.disable()
    Starter()


atexit.register(setup)
"""

# QA r8, the replaced-hook channel: the record's write leaves threading.excepthook replaced.
_SEED_RECORD_HOOK_LEFT = """
import threading


class Output:
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        threading.excepthook = print
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()


sys.stdout = Output(sys.stdout)
"""

# QA r8, residual (b) inside the callback, measured: the flush DELETES the hook and replaces the
# stream around the unraisable fault, restoring both, so no audit event fires and the child
# passes (the reproduction above, with the hook present, is caught).
_SEED_RECORD_WINDOW = """
class Raiser:
    def __del__(self):
        raise RuntimeError("fault during result flush")


class Output:
    def __init__(self, stream):
        self.stream, self.written = stream, False

    def write(self, text):
        result = self.stream.write(text)
        self.written = True
        return result

    def flush(self):
        self.stream.flush()
        if self.written:
            saved = (sys.stderr, sys.unraisablehook)
            sys.stderr = io.StringIO()
            del sys.unraisablehook
            Raiser()
            sys.stderr, sys.unraisablehook = saved


sys.stdout = Output(sys.stdout)
"""

# QA r8, the error-stream channel, measured: a write to stderr from the record's write is not a
# fault the child sees (it exits 0); the bytes stay on its error stream, which both parents
# refuse on a passing exit.
_SEED_RECORD_STDERR = """
class Output:
    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        sys.stderr.write("callback wrote to stderr\\n")
        sys.stderr.flush()
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()


sys.stdout = Output(sys.stdout)
"""

# Merge train 2 QA r9 (codex MAJOR), the reproductions: the wrapped stdout counts the flushes made
# once anything was written, and its SECOND (the re-check's own flush, after the callback's) either
# replaces threading.excepthook or drops cyclic garbage whose finalizer raises, with automatic
# collection disabled. The pinned re-check flushed AFTER its machinery check and its collection, so
# both exited 0 with a complete record; the flushes now come first (contract step 6).
_SEED_FINAL_FLUSH_HOOK = """
import threading


class Output:
    def __init__(self, stream):
        self.stream, self.written, self.flushes = stream, False, 0

    def write(self, text):
        self.written = True
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()
        if self.written:
            self.flushes += 1
            if self.flushes == 2:
                threading.excepthook = lambda args: None


sys.stdout = Output(sys.stdout)
"""

_SEED_FINAL_FLUSH_GARBAGE = """
class Raiser:
    def __init__(self):
        self.cycle = self

    def __del__(self):
        raise RuntimeError("cyclic cleanup dropped by the final flush")


class Output:
    def __init__(self, stream):
        self.stream, self.written, self.flushes = stream, False, 0

    def write(self, text):
        self.written = True
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()
        if self.written:
            self.flushes += 1
            if self.flushes == 2:
                gc.disable()
                Raiser()


sys.stdout = Output(sys.stdout)
"""

# QA r9, residual (c) measured: an earlier exit handler starts a daemon thread, waits until it
# runs and returns while the thread still waits on an event nobody sets. The thread started
# before the record handler, so it is not refused; the os._exit ends it unobserved and the child
# exits 0 with its record and seal, exactly as the residual discloses.
_SEED_EARLIER_HANDLER_THREAD = """
import threading


def start_waiter():
    started, never = threading.Event(), threading.Event()

    def wait():
        started.set()
        never.wait()

    threading.Thread(target=wait, daemon=True).start()
    started.wait()


atexit.register(start_waiter)
"""

# QA r9, the signal channel of contract step 6: the re-check's flush sends the child a signal
# whose Python handler, installed by the loaded code, would replace a hook and write to stderr.
# The record handler blocked every blockable signal when it started, so the handler never runs:
# exit 0, record and seal, empty error stream.
_SEED_FINAL_FLUSH_SIGNAL = """
import signal
import threading


def on_signal(signum, frame):
    threading.excepthook = print
    os.write(2, b"signal handler ran\\n")


signal.signal(signal.SIGUSR1, on_signal)


class Output:
    def __init__(self, stream):
        self.stream, self.written, self.flushes = stream, False, 0

    def write(self, text):
        self.written = True
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()
        if self.written:
            self.flushes += 1
            if self.flushes == 2:
                os.kill(os.getpid(), signal.SIGUSR1)


sys.stdout = Output(sys.stdout)
"""

# (name, source before register(), source after arm_audit(), expected exit, expected record line
# or None for provably no record, needles the error stream must carry, whether it must be empty).
_CASES = (
    ("clean", "", "", 0, "record 0", (), True),
    ("benign-cyclic-finalizer", "", _SEED_BENIGN, 0, "record 0", (), True),
    ("another-handler-first", "atexit.register(lambda: None)\n", "", 3, None,
     ("refused: another exit handler came first",), False),
    ("cleanup-exit-no-record", "", "atexit.register(os._exit, 0)\n", 0, None, (), True),
    ("builtin-cleanup-fault", "", _SEED_FAULT, 2, "record 2",
     ("child-contract-fault", "a cleanup fault reached sys.unraisablehook"), False),
    ("silenced-unraisablehook-fault", "",
     "sys.unraisablehook = lambda unraisable: None\n" + _SEED_FAULT, 2, "record 2",
     ("a cleanup fault reached sys.unraisablehook",), False),
    ("stderr-replaced-by-cleanup", "",
     "atexit.register(setattr, sys, \"stderr\", io.StringIO())\n", 2, "record 2",
     ("cleanup code replaced the reporting machinery (sys.stderr)",), False),
    ("excepthook-replaced-by-cleanup", "",
     "atexit.register(setattr, sys, \"excepthook\", print)\n", 2, "record 2",
     ("replaced the reporting machinery (sys.excepthook)",), False),
    ("chained-finalizer-fault", "", _SEED_CHAIN, 2, "record 2",
     ("a cleanup fault reached sys.unraisablehook",), False),
    ("resurrecting-finalizer-fault", "", _SEED_RESURRECT, 2, "record 2",
     ("a cleanup fault reached sys.unraisablehook",), False),
    ("net-release-resurrecting-fault", "", _SEED_NET_RELEASE, 2, "record 2",
     ("a cleanup fault reached sys.unraisablehook",), False),
    ("net-swap-resurrecting-fault", "", _SEED_NET_SWAP, 2, "record 2",
     ("a cleanup fault reached sys.unraisablehook",), False),
    ("unsettled-finalizer-chain", "", _SEED_UNSETTLED, 2, "record 2",
     ("still freed or created objects after",), False),
    ("stdout-closed-by-cleanup", "", "atexit.register(sys.stdout.close)\n", 2, "record 2",
     ("could not be flushed",), False),
    ("deleted-hook-window-passes", "", _SEED_WINDOW, 0, "record 0", (), True),
    ("kept-hook-window-fault", "", _SEED_WINDOW_CONTROL, 2, "record 2",
     ("a cleanup fault reached sys.unraisablehook",), False),
    ("record-callback-raises", "", _SEED_RECORD_FLUSH, 2, "record 0",
     ("the record handler raised RuntimeError in the record callback", "exit 2"), False),
    ("handler-raises-before-record", "", "atexit.register(setattr, gc, \"collect\", None)\n", 2,
     None, ("the record handler raised TypeError before the record",), False),
    ("record-callback-unraisable", "", _SEED_RECORD_UNRAISABLE, 2, "record 0",
     ("a fault was observed during the record callback",
      "a cleanup fault reached sys.unraisablehook", "exit 2"), False),
    ("record-callback-starts-thread", "", _SEED_RECORD_THREAD, 2, "record 0",
     ("a fault was observed during the record callback",
      "1 thread start(s) inside the record handler"), False),
    ("finalizer-starts-thread", "", _SEED_FINALIZER_THREAD, 2, "record 2",
     ("child-contract-fault", "1 thread start(s) inside the record handler"), False),
    ("record-callback-leaves-hook-replaced", "", _SEED_RECORD_HOOK_LEFT, 2, "record 0",
     ("a fault was observed during the record callback",
      "replaced the reporting machinery (threading.excepthook)"), False),
    ("record-callback-deleted-hook-window-passes", "", _SEED_RECORD_WINDOW, 0, "record 0", (),
     True),
    ("record-callback-stderr-write-left-to-parent", "", _SEED_RECORD_STDERR, 0, "record 0",
     ("callback wrote to stderr",), False),
    ("final-flush-replaces-hook", "", _SEED_FINAL_FLUSH_HOOK, 2, "record 0",
     ("a fault was observed during the record callback",
      "replaced the reporting machinery (threading.excepthook)", "exit 2"), False),
    ("final-flush-drops-cyclic-fault", "", _SEED_FINAL_FLUSH_GARBAGE, 2, "record 0",
     ("a fault was observed during the record callback",
      "a cleanup fault reached sys.unraisablehook", "exit 2"), False),
    ("final-flush-signal-blocked", "", _SEED_FINAL_FLUSH_SIGNAL, 0, "record 0", (), True),
    ("earlier-handler-thread-residual-passes", "", _SEED_EARLIER_HANDLER_THREAD, 0, "record 0",
     (), True),
)


def _run_case(pre, body):
    """Run one seeded child; returns (exit code, the record line or None, stderr text, stdout
    text). The child
    is a real `python3 -I -B -c` interpreter wired exactly as the callers wire the contract."""
    import subprocess
    import tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    source = _CASE_HEAD + pre + _CASE_REGISTER + body + _CASE_SETTLE
    with tempfile.TemporaryDirectory(prefix="child-contract-selftest-") as box:
        result = os.path.join(box, "record")
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-c", source, result, here],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        record = None
        if os.path.exists(result):
            with open(result, "r", encoding="utf-8") as handle:
                record = handle.read().strip()
    return (proc.returncode, record, proc.stderr.decode("utf-8", "replace"),
            proc.stdout.decode("utf-8", "replace"))


def self_test():
    """Every channel of the contract through real children (_CASES), plus the disclosure floor:
    the module docstring must keep naming each channel the contract cannot close and the ordering
    invariant. The seal (written by os.write after the final check, contract step 6) must END the
    stdout of every passing child with a record, and no failing or record-less child may carry a
    passing seal."""
    failures = []
    for needle in ("DELETED sys.unraisablehook", "threading.excepthook", "SAME addresses",
                   "never finalized", "NOTHING THE CHECKED CODE CAN REACH RUNS AFTER THE LAST CHECK",
                   "CONCURRENTLY"):
        if needle not in (__doc__ or ""):
            failures.append("the module docstring no longer discloses {!r}".format(needle))
    for name, pre, body, want_code, want_record, needles, want_quiet in _CASES:
        try:
            code, record, err, out = _run_case(pre, body)
        except Exception as exc:
            failures.append("{}: the child could not be driven ({})".format(
                name, type(exc).__name__))
            continue
        if code != want_code:
            failures.append("{}: exit {} (expected {}); stderr tail {!r}".format(
                name, code, want_code, err[-240:]))
        if record != want_record:
            failures.append("{}: record {!r} (expected {!r})".format(name, record, want_record))
        missing = [needle for needle in needles if needle not in err]
        if missing:
            failures.append("{}: stderr lacks {!r} (tail {!r})".format(name, missing, err[-240:]))
        if want_code == 0 and want_record == "record 0" and not out.endswith("seal 0\n"):
            failures.append("{}: stdout does not end with the seal (tail {!r})".format(
                name, out[-240:]))
        if (want_code != 0 or want_record is None) and "seal 0" in out:
            failures.append("{}: a passing seal on a failing or record-less child ({!r})".format(
                name, out[-240:]))
        if want_quiet and err.strip():
            failures.append("{}: expected an empty error stream, got {!r}".format(
                name, err[-240:]))
    if failures:
        print("child-contract self-test: FAIL", file=sys.stderr)
        for failure in failures:
            print("  - " + failure, file=sys.stderr)
        return 1
    print("child-contract self-test: PASS (a clean child and a benign cyclic finalizer pass with "
          "their record; an exit handler registered before the record handler refuses by name; a "
          "cleanup os._exit leaves no record, so the parent fails closed on its absence; a "
          "builtin cleanup fault, the same fault under a silenced unraisablehook, a stderr or "
          "excepthook left replaced by cleanup, a finalizer-created cyclic fault, a resurrecting "
          "finalizer whose new work faults, one that also releases tracked objects while it "
          "creates that work (QA r6), its single-object swap variant, a finalizer chain "
          "outlasting the collection bound, and a closed stdout each fail closed by name with "
          "the exit-2 record; a record callback that raises after writing its record (QA r7) "
          "and a handler that raises before the record each exit 2 with a named diagnostic; "
          "a record callback that returns normally after an unraisable fault under a hook it "
          "puts back (QA r8), one that starts a thread, one that leaves threading.excepthook "
          "replaced, and a finalizer that starts a thread in the settling collection each exit "
          "2 by name; the deleted-unraisablehook window passes exactly as residual (b) "
          "discloses, in cleanup and in the record callback, while its kept-hook controls are "
          "caught by the audit event; a callback's stderr write exits 0 with the bytes on "
          "the error stream, which the parents refuse; the re-check's own second flush that "
          "replaces threading.excepthook or drops a cyclic raising finalizer (QA r9) exits 2 by "
          "name, a signal it sends finds its handler blocked, an earlier exit handler's waiting "
          "thread passes exactly as residual (c) discloses, and every passing child's stdout "
          "ends with the seal written after the final check)")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    print("usage: _child_contract.py --self-test", file=sys.stderr)
    sys.exit(2)
