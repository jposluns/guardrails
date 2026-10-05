#!/usr/bin/env python3
"""Nav-coverage gate: every site/*.html carries exactly one /disclosure link INSIDE its <nav>.

The disclosure matrix is the site's standing "what we claim, and what we do not" page; every public
page must point to it from its primary navigation so a reader is never more than one click from the
limitations. This gate asserts that link is present, and IN THE NAV, on every page EXCEPT an explicit
allowlist, and fails closed if a non-exempt page lacks it or the allowlist drifts. A /disclosure link
elsewhere in the page body or footer does NOT satisfy the requirement: only anchors nested inside a
<nav> element count, so the link cannot drift out of the nav and still pass. <nav> nesting is tracked,
so an anchor is attributed to the nav only while one is open.

(Historically this gate required the link in the <footer>; B-10 moved the canonical disclosure link
into the site nav and repurposed this gate accordingly. The filename is retained as its gate identity.)

Coverage spans TWO required roots: site/ (the aiqt.ai site, absolute /disclosure) and opf/site/ (the
opfiles.ai site, served from opf/site as its own root, whose pages link ./disclosure relative so it
resolves within opf/site; every opf/site page carries the nav, so nothing is exempt). Each tree is a REQUIRED
coverage input: if a root is absent, unreadable, or carries no .html pages, the gate fails closed (exit 2) rather than reporting a clean pass over nothing. A page must be a REGULAR
file, opened O_NOFOLLOW and confirmed regular via fstat on the opened fd, so a symlink/FIFO/socket/device
.html (or site/ itself as a symlink) is fail-closed and never read or followed, and the check-then-read
TOCTOU on the final path component is closed (the fd type-checked is the fd read). THREAT-MODEL BOUNDARY
(disclosed, Architect-ruled 2026-08-28): site/ is a TRUSTED, git-tracked, CI-checked-out tree, not an
adversarial input. This gate does NOT defend against a concurrent-write attacker or a parent-directory-
component symlink race (full component-wise containment is the OS/CI-isolation layer's job per
SYSTEM-HARDENING.md), nor cross-check the page set against an expected/tracked index (page presence and
git-tracking are gen_manifest's concern). Those are out of a trusted-input coverage gate's scope. The allowlist is empty:
every public page carries the nav, so none is exempt. It stays an EXPLICIT mechanism, not a silent skip:
an allowlisted page that is missing, or that starts carrying the link, is allowlist drift and fails.

  check_footer.py             scan site/*.html
  check_footer.py --self-test  assert the present/missing/drift and fail-closed paths each resolve

Exit 0 clean, 1 on any coverage finding, 2 on a missing/unreadable/empty required input (fail-closed).
"""
import sys
import os
import stat
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

DISCLOSURE_HREF = "/disclosure"
# Pages exempt from the nav-link requirement, relative to site/. Empty: every page carries the nav.
ALLOWLIST = frozenset()

# Coverage roots, each (subdir, disclosure_href, allowlist). Both are REQUIRED inputs (an absent,
# unreadable, or page-less root is fail-closed exit 2, never a clean pass over nothing). site/ is the
# aiqt.ai site, served at root, so its nav disclosure link is the absolute /disclosure. opf/site is the
# opfiles.ai site, served from opf/site as its own root (Cloudflare Pages builds opf/site), so its pages
# link the disclosure page RELATIVE (./disclosure): a relative link resolves within opf/site whatever the
# mount point. Every opf/site page carries the nav, so the allowlist is empty (like site/).
COVERAGE_ROOTS = (
    ("site", "/disclosure", frozenset()),
    ("opf/site", "./disclosure", frozenset()),
)


class _Anchors(HTMLParser):
    """Collect the href of every <a> that sits INSIDE a <nav> element. <nav> nesting is tracked so an
    anchor counts only while a nav is open; a link in the page body or footer is ignored, so it can
    never satisfy a gate that requires the link in the nav."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []
        self._nav_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == "nav":
            self._nav_depth += 1
        elif tag == "a" and self._nav_depth > 0:
            d = dict(attrs)
            if d.get("href") is not None:
                self.hrefs.append(d["href"].strip())

    def handle_endtag(self, tag):
        if tag == "nav" and self._nav_depth:
            self._nav_depth -= 1


def disclosure_link_count(text, href=DISCLOSURE_HREF):
    """Number of <a href="{href}"> links nested inside a <nav> in one page's HTML. The href is
    tree-appropriate: absolute /disclosure for site/, relative ./disclosure for the opf/site tree."""
    parser = _Anchors()
    parser.feed(text)
    return sum(1 for h in parser.hrefs if h == href)


def check_pages(pages, allowlist, href=DISCLOSURE_HREF):
    """pages: {name: html_text}. Return a sorted list of findings. Enforces exactly one `href` disclosure
    link in the nav on every page not in allowlist, and treats a missing or newly-linking allowlisted page
    as drift. Callers guarantee pages is non-empty; emptiness is a fail-closed input error handled in run()."""
    findings = []
    for name in sorted(allowlist):
        if name not in pages:
            findings.append(
                "{}: allowlisted for the nav link but no such page exists (allowlist drift)".format(name))
    for name in sorted(pages):
        count = disclosure_link_count(pages[name], href)
        if name in allowlist:
            if count:
                findings.append(
                    "{}: allowlisted as nav-exempt but now carries {} {} link(s); "
                    "remove it from the allowlist (allowlist drift)".format(name, count, href))
        elif count == 0:
            findings.append("{}: missing the {} nav link".format(name, href))
        elif count > 1:
            findings.append(
                "{}: carries {} {} links (expected exactly one)".format(name, count, href))
    return findings


def _close_fd_propagating(fd):
    """Close a descriptor on a FAIL-CLOSED path: the close error PROPAGATES. Single close (P1, #378):
    exactly ONE os.close; if it raises, the number counts as released (close(2) on Linux releases it early,
    even when the close then reports EINTR or EIO, and a retry can close another thread's reused
    descriptor: man 2 close), so it is never probed or closed again, and the ORIGINAL close error
    propagates unchanged. Inlined from opf/tools/_journal._close_fd_propagating (the same body) so this
    tool keeps working without opf/tools present (copied, mutated, or shipped alone)."""
    os.close(fd)


def _close_fd_yielding(fd):
    """Close a descriptor from an `except` handler or a `finally` block without letting a close error
    REPLACE the exception already in flight there: when an exception is unwinding through, or being handled
    in, the CALLING frame, the same single close still runs (P1: one os.close, the number released either
    way and never touched again) but its close error is dropped so the ORIGINAL exception keeps
    propagating; on the normal path this is exactly _close_fd_propagating, so a close error still fails
    closed. Inlined from opf/tools/_journal._close_fd_yielding (the same body) so this tool keeps working
    without opf/tools present."""
    tb = sys.exc_info()[2]
    if tb is None or tb.tb_frame is not sys._getframe(1):
        _close_fd_propagating(fd)
        return
    try:
        _close_fd_propagating(fd)
    except OSError:
        pass                                      # the in-flight exception wins; the fd was still released


def _read_regular_page(path):
    """Read a page's text through a fail-closed, symlink-safe open. Opens O_NOFOLLOW (a symlink at the final
    component fails - never followed) + O_NONBLOCK (a FIFO open returns instead of blocking), fstats the
    OPENED fd to confirm a regular file (closing the check-then-read TOCTOU: the fd type-checked is the fd
    read), then reads UTF-8. Raises OSError (open or non-regular type) or UnicodeDecodeError; the caller
    fails closed. A symlink, FIFO, socket, or device raises here rather than being read or followed.
    The file object is made with closefd=False, so it never closes fd: os.fdopen that fails after creating
    its raw file closes that file, and a dropped object closes on collection, but neither touches fd. The
    one close of fd is the finally's, on every path, so who closes fd is never in doubt (P1, #378)."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file (symlink, FIFO, socket, or device)")
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as fh:
            return fh.read()
    finally:
        _close_fd_yielding(fd)


def _run_one(root, subdir, href, allowlist):
    """Scan one coverage root (root/subdir). Return an exit code: 0 clean, 1 a coverage finding, 2 a
    missing/unreadable/empty required input (fail-closed). The root is a required coverage input: absent,
    unreadable, or page-less is exit 2, never a clean pass over nothing."""
    site = root / subdir
    if site.is_symlink():
        print("error: {}/ is a symlink; the coverage root must be a real directory in the tree; "
              "fail-closed".format(subdir), file=sys.stderr)
        return 2
    if not site.is_dir():
        print("error: required input {}/ is absent under {}; the nav-coverage gate cannot evaluate; "
              "fail-closed".format(subdir, root), file=sys.stderr)
        return 2
    try:
        html_files = sorted(walk_files(site, suffixes={".html"}))
    except OSError as exc:
        print("error: cannot scan {}/ ({}); fail-closed".format(subdir, exc), file=sys.stderr)
        return 2
    if not html_files:
        print("error: {}/ contains no .html pages to cover; a page-less required input is fail-closed"
              .format(subdir), file=sys.stderr)
        return 2
    pages = {}
    for f in html_files:
        name = str(f.relative_to(site))
        # A page is opened symlink-safe (O_NOFOLLOW) and TOCTOU-safe (fstat the opened fd), so a symlink,
        # FIFO, socket, or device is fail-closed, never read or followed. See _read_regular_page.
        try:
            pages[name] = _read_regular_page(f)
        except (OSError, UnicodeDecodeError) as exc:
            print("error: cannot load {} ({}); a symlink, non-regular, unreadable, or undecodable page is "
                  "fail-closed".format(f.relative_to(root), exc), file=sys.stderr)
            return 2
    findings = check_pages(pages, allowlist, href)
    if findings:
        print("FAIL: {} nav-coverage issue(s) under {}/".format(len(findings), subdir))
        for finding in sorted(set(findings)):
            print("  {}/{}".format(subdir, finding))
        return 1
    allow_str = ", ".join(sorted(allowlist)) or "none"
    print("PASS: every {}/ page carries the {} nav link (allowlist: {})".format(subdir, href, allow_str))
    return 0


def run(root):
    """Scan every coverage root (site/ and opf/site/). Return the worst per-root exit code: 0 clean, 1 a
    coverage finding, 2 a missing/unreadable/empty required input (fail-closed). Each root is required."""
    worst = 0
    for subdir, href, allowlist in COVERAGE_ROOTS:
        worst = max(worst, _run_one(root, subdir, href, allowlist))
    return worst


def _close_vectors(base):
    """#378: the vectors for this tool's _close_fd_yielding copy and its one site, _read_regular_page, whose
    finally makes the only close of the page descriptor on every path: while an exception unwinds (os.fdopen
    refusing before it creates anything, and os.fdopen failing after its raw file exists, which io.open then
    closes), and on the normal path. The wrapping-failure vector is also run against the pre-fix body
    (fdopen taking fd, `fd = -1` as the with body's first statement) and must be red there by NOFIRE alone:
    the failed wrapper's own close released fd first, so the finally's close was a second close of a number
    already free. Returns (failures, runs)."""
    import inspect
    import _close_selftest
    page = base / "page.html"
    page.write_text("<nav></nav>", encoding="utf-8")
    ns = globals()
    sent = _close_selftest._StSentinel("in flight at _read_regular_page")

    def read_page(mode, site=None):
        def call(fault):
            real = os.fdopen

            def spy(fd, *args, **kwargs):
                fault.arm(fd)
                if mode == "refused":
                    raise sent
                handle = real(fd, *args, **kwargs)
                if mode == "wrap":
                    handle.close()                # as io.open closes the raw file of a wrapper that failed
                    raise sent
                return handle
            os.fdopen = spy
            try:
                (site or _read_regular_page)(page)
            finally:
                os.fdopen = real
        return call

    vectors = (("check_footer site _read_regular_page: finally while an exception unwinds", True, "AR",
                read_page("refused"), lambda e: e is sent),
               ("check_footer site _read_regular_page: finally when os.fdopen fails after its raw file exists",
                True, "AR", read_page("wrap"), lambda e: e is sent),
               ("check_footer site _read_regular_page: normal path", False, "BR", read_page("read"), None)
               ) + _close_selftest._st_helper_vectors(ns)
    failures, runs = _close_selftest._st_close_check(ns, vectors)
    source = inspect.getsource(_read_regular_page)
    new = ('        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as fh:\n'
           "            return fh.read()\n    finally:\n        _close_fd_yielding(fd)\n")
    old = ('        with os.fdopen(fd, "r", encoding="utf-8") as fh:\n            fd = -1\n'
           "            return fh.read()\n    finally:\n        if fd >= 0:\n            _close_fd_yielding(fd)\n")
    if source.count(new) != 1:
        return failures + ["check_footer _read_regular_page revert: target found {} times".format(
            source.count(new))], runs
    reverted = dict(ns)
    exec(compile(source.replace(new, old), __file__, "exec"), reverted)
    red = _close_selftest._st_close_run(read_page("wrap", reverted["_read_regular_page"]), True,
                                        lambda e: e is sent, False)
    runs += 1
    if [problem.split(":")[0] for problem in red] != ["NOFIRE"]:
        failures.append("check_footer site _read_regular_page under the pre-fix fdopen ownership: expected red "
                        "by NOFIRE alone, got {}".format(red or "green"))
    return failures, runs


def _close_vectors_guarded(base):
    """`_close_vectors(base)` with everything it runs in this process guarded: the lazy _close_selftest
    import, the later calls into it and the reverted body. A process-ending exit or a fault there
    (SystemExit 0 or None, GeneratorExit, any other BaseException or Exception) is reported and returns None,
    which _self_test makes CANNOT-EVALUATE (exit 2), never the vectors' own status. A KeyboardInterrupt of
    exactly that class propagates unchanged, so an operator's Ctrl-C stops the run; a subclass, which only
    loaded code raises, is cannot-evaluate too and is never re-raised. The message is fixed by the except clause
    that caught the exception and never inspects or formats the escaping object (no isinstance, attribute,
    repr or str of it), so a hostile exception (one whose __class__ property raises SystemExit 0, for
    example) cannot run code from the handler (other hostile objects: the one residual in the _opf_views
    class disclosure)."""
    try:
        return _close_vectors(base)
    except KeyboardInterrupt as exc:
        if type(exc) is KeyboardInterrupt:
            raise
        kind = "a KeyboardInterrupt subclass"
    except Exception:
        kind = "an exception"
    except BaseException:  # noqa: BLE001  a sibling ending the process is cannot-evaluate, never a pass
        kind = "a process-ending exception"
    print("CANNOT-EVALUATE: check_footer self-test: the #378 close vectors (the _close_selftest sibling) "
          "raised {}; fail-closed".format(kind), file=sys.stderr)
    return None


# The KeyboardInterrupt a poisoned _close_selftest sibling raises on purpose. Only it is recorded by
# _poisoned_close_outcome; any other KeyboardInterrupt, such as an operator's real Ctrl-C, propagates.
CLOSE_POISON_INTERRUPT = "check-footer-self-test-poisoned-close-selftest"


def _is_interrupt(exc, sent):
    """True only for an exact KeyboardInterrupt whose args are exactly (sent,), read through exact built-in
    types alone, so a recorder never runs code from the caught instance (an args property or an argument's
    __eq__ that raises SystemExit 0, for example); any other KeyboardInterrupt propagates."""
    if type(exc) is not KeyboardInterrupt:
        return False
    args = exc.args
    return len(args) == 1 and type(args[0]) is str and args[0] == sent


def _interrupt_to_raise(exc):
    """The KeyboardInterrupt _propagate_interrupt raises for the caught KeyboardInterrupt `exc`, chosen by the
    exact class alone: `exc` itself, unchanged, when its class is exactly KeyboardInterrupt (what an
    operator's Ctrl-C raises); for a subclass, which only loaded code raises, a fresh KeyboardInterrupt with
    its context suppressed, so no code from the caught instance runs (a __notes__ property that raises
    SystemExit 0 while the interpreter reports it, for example). It raises nothing, so the vector over it
    catches no KeyboardInterrupt."""
    if type(exc) is KeyboardInterrupt:
        return exc
    fresh = KeyboardInterrupt()
    fresh.__suppress_context__ = True
    return fresh


def _propagate_interrupt(exc):
    """Raise what _interrupt_to_raise gives for the caught KeyboardInterrupt `exc`: an exact one propagates
    unchanged, and a subclass is never re-raised."""
    raise _interrupt_to_raise(exc)


def _interrupt_filter_outcomes(sent):
    """The interrupt filters over hostile inputs: _is_interrupt of an exact KeyboardInterrupt(sent), of a
    subclass whose args property returns (sent,), and of an exact KeyboardInterrupt whose argument is a str
    subclass or an object whose __eq__ is always true; whether _interrupt_to_raise gives an exact
    KeyboardInterrupt back unchanged and, for that subclass, a fresh exact KeyboardInterrupt with no args and
    its context suppressed; and the names of any instance code they ran. Nothing here raises or catches a
    KeyboardInterrupt, so an operator's Ctrl-C arriving here propagates unchanged (_real_sigint_propagates
    is red if it does not). Expected: (True, False, False, False, True, [])."""
    ran = []

    class _ArgsProperty(KeyboardInterrupt):
        @property
        def args(self):
            ran.append("args")
            return (sent,)

        def __str__(self):
            ran.append("str")
            return sent

    class _StrEq(str):
        def __eq__(self, other):
            ran.append("str-subclass-eq")
            return True

        __hash__ = str.__hash__

    class _AnyEq:
        def __eq__(self, other):
            ran.append("eq")
            return True

        __hash__ = object.__hash__

    outcomes = [_is_interrupt(KeyboardInterrupt(sent), sent), _is_interrupt(_ArgsProperty(), sent),
                _is_interrupt(KeyboardInterrupt(_StrEq(sent)), sent),
                _is_interrupt(KeyboardInterrupt(_AnyEq()), sent)]
    own = KeyboardInterrupt(sent)
    fresh = _interrupt_to_raise(_ArgsProperty())
    outcomes.append(_interrupt_to_raise(own) is own and type(fresh) is KeyboardInterrupt and fresh.args == ()
                    and fresh.__suppress_context__ is True)
    return tuple(outcomes) + (ran,)


# Run in a child by _real_sigint_propagates: load the file named first by path, make its first
# _interrupt_to_raise call deliver a real SIGINT to the child (as an operator's Ctrl-C does; a call outside the
# main thread exits 3 instead), then run _interrupt_filter_outcomes. The child must end by that interrupt.
_REAL_SIGINT_PROBE = """import importlib.util, os, signal, sys, threading
signal.signal(signal.SIGINT, signal.default_int_handler)
sys.path.insert(0, os.path.dirname(sys.argv[1]))
spec = importlib.util.spec_from_file_location("_real_sigint_probe_target", sys.argv[1])
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
real = gate._interrupt_to_raise
def hooked(exc):
    gate._interrupt_to_raise = real
    if threading.current_thread() is not threading.main_thread():
        sys.exit(3)
    signal.raise_signal(signal.SIGINT)
    return real(exc)
gate._interrupt_to_raise = hooked
gate._interrupt_filter_outcomes(sys.argv[2])
print("returned")
"""


def _real_sigint_propagates(sent):
    """True when a real SIGINT delivered inside _interrupt_filter_outcomes, in a child process, ends that child
    as an uncaught KeyboardInterrupt. Red if anything there catches a KeyboardInterrupt in the main thread (the
    child then returns) or classifies in a worker thread (the hook exits 3 there). This process catches no
    KeyboardInterrupt here: an operator's Ctrl-C reaches subprocess.run, which re-raises it."""
    import signal
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory(prefix="interrupt-filter-sigint-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_REAL_SIGINT_PROBE, encoding="utf-8")
        child = subprocess.run([sys.executable, "-I", "-B", str(probe), str(Path(__file__).resolve()), sent],
                               capture_output=True, text=True, timeout=120)
    return (child.returncode in (-signal.SIGINT, 130) and "returned" not in child.stdout
            and "KeyboardInterrupt" in child.stderr)


# An exception whose __class__ property raises SystemExit(0): a guard that inspects the caught instance
# (isinstance included) runs that property and ends the process with status 0 from its own handler.
CLOSE_DESCRIPTOR_EXIT = ("class _ClassExits(BaseException):\n    @property\n    def __class__(self):\n"
                         "        raise SystemExit(0)\n\n\n")
# A KeyboardInterrupt subclass whose __str__ and __notes__ raise SystemExit(0): re-raised as the caught
# instance, it ends the interpreter's report with status 0.
CLOSE_INTERRUPT_SUBCLASS_EXIT = ("class _InterruptExits(KeyboardInterrupt):\n    def __str__(self):\n"
                                 "        raise SystemExit(0)\n\n    @property\n    def __notes__(self):\n"
                                 "        raise SystemExit(0)\n\n\n")


def _poisoned_close_outcome(base, tag, body):
    """What _close_vectors_guarded gives with a _close_selftest sibling whose source is `body` (written under
    `base`): its return value, "KeyboardInterrupt" for CLOSE_POISON_INTERRUPT, or the class name of any other
    escape. Any other KeyboardInterrupt propagates."""
    import contextlib
    import io
    poison = base / "poisoned-{}".format(tag)
    poison.mkdir()
    (poison / "_close_selftest.py").write_text(body, encoding="utf-8")
    saved = sys.modules.pop("_close_selftest", None)
    sys.path.insert(0, str(poison))
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return _close_vectors_guarded(poison)
    except KeyboardInterrupt as exc:
        if not _is_interrupt(exc, CLOSE_POISON_INTERRUPT):
            _propagate_interrupt(exc)
        return "KeyboardInterrupt"
    except BaseException as exc:  # noqa: BLE001  recorded, never the self-test's own end
        return type(exc).__name__
    finally:
        sys.path.remove(str(poison))
        sys.modules.pop("_close_selftest", None)
        if saved is not None:
            sys.modules["_close_selftest"] = saved


def _close_guard_vectors(base):
    """The vectors for _close_vectors_guarded: a _close_selftest sibling that ends the process or faults at
    import or in a later call returns None (cannot-evaluate), an exception whose __class__ property raises
    SystemExit(0) included, and the sibling's own KeyboardInterrupt propagates. Only that KeyboardInterrupt
    (carrying CLOSE_POISON_INTERRUPT) is recorded; any other, such as an operator's real Ctrl-C landing in this
    window, propagates, at import and in a later call. Red if the guard is removed, red if it inspects the
    caught instance, and red if the recorder records another KeyboardInterrupt. Returns (failures, cases)."""
    sent = CLOSE_POISON_INTERRUPT
    later = "class _StSentinel(Exception):\n    pass\n\n\ndef _st_helper_vectors(ns):\n    {}\n"
    cases = (("import SystemExit(0)", "raise SystemExit(0)\n", None),
             ("import SystemExit(None)", "raise SystemExit\n", None),
             ("import GeneratorExit", "raise GeneratorExit\n", None),
             ("import ValueError", "raise ValueError('poisoned')\n", None),
             ("later call SystemExit(0)", later.format("raise SystemExit(0)"), None),
             ("later call SystemExit(None)", later.format("raise SystemExit"), None),
             ("later call BaseException subclass", later.format("raise type('B', (BaseException,), {})()"),
              None),
             ("import KeyboardInterrupt", "raise KeyboardInterrupt({!r})\n".format(sent), "KeyboardInterrupt"),
             ("later call KeyboardInterrupt", later.format("raise KeyboardInterrupt({!r})".format(sent)),
              "KeyboardInterrupt"),
             ("import exception whose __class__ exits 0", CLOSE_DESCRIPTOR_EXIT + "raise _ClassExits()\n", None),
             ("later call exception whose __class__ exits 0",
              CLOSE_DESCRIPTOR_EXIT + later.format("raise _ClassExits()"), None),
             ("import KeyboardInterrupt subclass that exits 0",
              CLOSE_INTERRUPT_SUBCLASS_EXIT + "raise _InterruptExits()\n", None),
             ("later call KeyboardInterrupt subclass that exits 0",
              CLOSE_INTERRUPT_SUBCLASS_EXIT + later.format("raise _InterruptExits()"), None))
    failures = []
    for k, (label, body, want) in enumerate(cases):
        got = _poisoned_close_outcome(base, k, body)
        if got != want:
            failures.append("a _close_selftest sibling poisoned at {}: got {!r}, want {!r}".format(
                label, got, want))
    # Any KeyboardInterrupt but CLOSE_POISON_INTERRUPT (here one carrying another value, standing in for a real
    # Ctrl-C) propagates out of the recorder, at import and in a later call.
    other = sent + "-other"
    other_cases = (("import", "raise KeyboardInterrupt({!r})\n".format(other)),
                   ("later call", later.format("raise KeyboardInterrupt({!r})".format(other))))
    for label, body in other_cases:
        try:
            got = _poisoned_close_outcome(base, "other-" + label.replace(" ", "-"), body)
        except KeyboardInterrupt as exc:
            if not _is_interrupt(exc, other):
                _propagate_interrupt(exc)
        else:
            failures.append("another KeyboardInterrupt at {} was recorded as {!r}, not propagated".format(
                label, got))
    # The recorder filters run no code from the caught instance.
    filters = _interrupt_filter_outcomes(sent)
    if filters != (True, False, False, False, True, []):
        failures.append("the interrupt filters over hostile inputs gave {!r}".format(filters))
    # A real SIGINT inside the interrupt filters ends the run as an interrupt. Red if a probe there catches it.
    if not _real_sigint_propagates(sent):
        failures.append("a real SIGINT inside the interrupt filters did not end the child as an interrupt")
    return failures, len(cases) + len(other_cases) + 2


def _self_test():
    import tempfile
    nav = '<nav><a href="/disclosure">Disclosure</a></nav>'
    cases = [
        ("present page passes", {"about.html": nav}, frozenset(), []),
        ("missing link fails", {"about.html": "<nav></nav>"}, frozenset(),
         ["about.html: missing the /disclosure nav link"]),
        ("body-only link (not in nav) fails",
         {"about.html": '<main><a href="/disclosure">Disclosure</a></main><nav></nav>'}, frozenset(),
         ["about.html: missing the /disclosure nav link"]),
        ("footer-only link (not in nav) fails",
         {"about.html": '<footer><a href="/disclosure">Disclosure</a></footer><nav></nav>'}, frozenset(),
         ["about.html: missing the /disclosure nav link"]),
        ("no-nav page fails",
         {"about.html": '<main><a href="/disclosure">Disclosure</a></main>'}, frozenset(),
         ["about.html: missing the /disclosure nav link"]),
        ("body link plus nav link counts only the nav one (passes)",
         {"about.html": '<main><a href="/disclosure">body</a></main>' + nav}, frozenset(), []),
        ("duplicate link fails", {"about.html": nav + nav}, frozenset(),
         ["about.html: carries 2 /disclosure links (expected exactly one)"]),
        ("allowlisted page without link passes",
         {"splash.html": "<nav></nav>"}, frozenset({"splash.html"}), []),
        ("allowlisted page WITH link is drift",
         {"splash.html": nav}, frozenset({"splash.html"}),
         ["splash.html: allowlisted as nav-exempt but now carries 1 /disclosure link(s); "
          "remove it from the allowlist (allowlist drift)"]),
        ("stale allowlist entry is drift", {"about.html": nav}, frozenset({"gone.html"}),
         ["gone.html: allowlisted for the nav link but no such page exists (allowlist drift)"]),
    ]
    failures = []
    for label, pages, allow, expected in cases:
        got = sorted(check_pages(pages, allow))
        if got != sorted(expected):
            failures.append("{}: expected {} got {}".format(label, sorted(expected), got))
    # run() fail-closed on a missing/empty/unreadable required input; clean on a good tree (exit codes).
    # run()'s own stdout/stderr is captured so its diagnostic lines do not leak into the self-test output.
    import contextlib
    import io

    def quiet_one(r, subdir="site", href=DISCLOSURE_HREF, allow=frozenset()):
        # Exercise the single-root engine directly, so the site/-tree exit-code legs stay independent of
        # the opf/site coverage root (which run() also requires). run() itself is exercised by the
        # two-root legs below.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return _run_one(r, subdir, href, allow)

    def quiet_run(r):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run(r)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        if quiet_one(root) != 2:
            failures.append("absent site/ did not fail closed (expected exit 2)")
        (root / "site").mkdir()
        if quiet_one(root) != 2:
            failures.append("page-less site/ did not fail closed (expected exit 2)")
        (root / "site" / "about.html").write_text(nav, encoding="utf-8")
        if quiet_one(root) != 0:
            failures.append("a covered site/ did not pass (expected exit 0)")
        (root / "site" / "bad.html").write_text("<nav></nav>", encoding="utf-8")
        if quiet_one(root) != 1:
            failures.append("an uncovered page did not report a finding (expected exit 1)")
        (root / "site" / "zz-undecodable.html").write_bytes(b"\xff\xfe<nav></nav>")
        if quiet_one(root) != 2:
            failures.append("an undecodable page did not fail closed (expected exit 2, not a traceback)")
    with tempfile.TemporaryDirectory() as d2:
        root2 = Path(d2)
        (root2 / "site").mkdir()
        (root2 / "site" / "ok.html").write_text(nav, encoding="utf-8")
        os.symlink("/etc/hostname", str(root2 / "site" / "link.html"))  # a non-regular page object
        if quiet_one(root2) != 2:
            failures.append("a non-regular (symlink) page did not fail closed (expected exit 2, no follow)")
        os.remove(str(root2 / "site" / "link.html"))
        os.mkfifo(str(root2 / "site" / "pipe.html"))  # a FIFO must not hang the open; fstat rejects it
        if quiet_one(root2) != 2:
            failures.append("a FIFO page did not fail closed (expected exit 2, no hang)")
        os.remove(str(root2 / "site" / "pipe.html"))
    with tempfile.TemporaryDirectory() as d3:
        root3 = Path(d3)
        (root3 / "realsite").mkdir()
        (root3 / "realsite" / "ok.html").write_text(nav, encoding="utf-8")
        os.symlink(str(root3 / "realsite"), str(root3 / "site"))  # site/ itself a symlink -> rejected
        if quiet_one(root3) != 2:
            failures.append("a symlinked site/ root did not fail closed (expected exit 2)")
    # opf/site coverage: the relative ./disclosure nav link is tree-appropriate (opf/site is served as its
    # own root), every page carries the nav (empty allowlist), a page missing the link is a finding, and an
    # absent opf/site root fails closed. run() requires BOTH roots, so it is exercised over a two-root tree.
    rel_nav = '<nav><a href="./disclosure">Disclosure</a></nav>'
    with tempfile.TemporaryDirectory() as d4:
        root4 = Path(d4)
        (root4 / "site").mkdir()
        (root4 / "site" / "about.html").write_text(nav, encoding="utf-8")
        if quiet_run(root4) != 2:
            failures.append("run() with site/ present but opf/site absent did not fail closed (expected 2)")
        (root4 / "opf" / "site").mkdir(parents=True)
        (root4 / "opf" / "site" / "index.html").write_text(rel_nav, encoding="utf-8")
        (root4 / "opf" / "site" / "manifest.html").write_text(rel_nav, encoding="utf-8")
        if quiet_run(root4) != 0:
            failures.append("a covered two-root tree (opf/site pages carry the relative ./disclosure) did not pass")
        # the relative-href engine rejects a page that carries only the absolute /disclosure link
        (root4 / "opf" / "site" / "stale.html").write_text(nav, encoding="utf-8")
        if quiet_run(root4) != 1:
            failures.append("an opf/site page missing the ./disclosure nav link was not reported (expected 1)")
    with tempfile.TemporaryDirectory() as d5:
        closed = _close_vectors_guarded(Path(d5))
    if closed is None:
        return 2
    close_failures, close_runs = closed
    failures.extend(close_failures)
    with tempfile.TemporaryDirectory() as d6:
        guard_failures, guard_cases = _close_guard_vectors(Path(d6))
    failures.extend(guard_failures)
    # The background fault channels of code loaded in process (atexit, worker thread, destructor), each
    # driven through the real _fail_closed_main entry in a child.
    failures.extend(_fault_channel_vectors())
    if failures:
        print("FAIL: check_footer self-test")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: check_footer self-test ({} check_pages cases + run() exit-code legs + {} #378 close-vector "
          "runs + {} close-vector guard cases)".format(len(cases), close_runs, guard_cases))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    return run(Path(__file__).resolve().parents[1])



# --- background fault channels of code loaded in process (atexit, worker thread, destructor) ----------
# Fail-closed settlement for the background fault channels of the code this gate loads and runs in
# process. _fail_closed_main installs the recorders BEFORE the gate's work (so before any in-process
# load) and settles them AFTER it: a fault the interpreter reports only to stderr (a destructor, weakref
# or similar callback through sys.unraisablehook; an unhandled exception ending a worker thread through
# threading.excepthook) is recorded and forces exit 2 over a passing verdict, never a silent pass; the
# verdict then ends the process with os._exit, so an atexit callback registered by loaded code can never
# run after it (that channel is unreachable, not merely disclosed). A SystemExit ending a worker thread
# is the interpreter's normal thread exit (default-hook parity) and is not recorded. Each recorder
# chains to the hook it wrapped, so the usual traceback still reaches stderr after the marker line.
# Paths that end outside _gate_exit (a propagating KeyboardInterrupt, an escaping exception) already end
# non-zero, and an ACCIDENTAL fault in an atexit callback cannot turn that non-zero end into exit 0, so
# no background fault reads as a pass there. Residual: loaded code replacing these hooks, this record or
# the exit itself is the reporting-machinery channel disclosed once in the _opf_views class disclosure
# (D-411-HOSTILE-DISCLOSED).
_LOADED_FAULTS = []
_FAULT_MARKER = "LOADED-CODE-FAULT"


def _install_fault_hooks():
    import threading

    previous_unraisable = sys.unraisablehook
    previous_thread = threading.excepthook

    def _record(channel, chained, args):
        _LOADED_FAULTS.append(channel)
        try:
            sys._loaded_code_fault = True
            sys.stderr.write("{}: a {} fault was recorded; a passing verdict will fail closed\n".format(
                _FAULT_MARKER, channel))
        finally:
            chained(args)

    def _record_unraisable(args):
        _record("destructor-or-callback", previous_unraisable, args)

    def _record_thread(args):
        if args.exc_type is not None and issubclass(args.exc_type, SystemExit):
            previous_thread(args)
        else:
            _record("worker-thread", previous_thread, args)

    sys.unraisablehook = _record_unraisable
    threading.excepthook = _record_thread


def _gate_exit(code):
    """Settle and END the process: flush both streams, force a recorded background fault to exit 2 over a
    passing verdict, then os._exit, so no atexit callback registered by loaded code runs after the verdict
    (the gate's own cleanup runs inside its work; this gate registers no atexit work of its own)."""
    import os
    if type(code) is bool:
        code = 1 if code else 0
    elif code is None:
        code = 0
    elif type(code) is not int:
        # The code object is never formatted: a loaded object's __str__ must not run here.
        sys.stderr.write("{}: a non-int SystemExit code at the gate entry; exit 1\n".format(_FAULT_MARKER))
        code = 1
    if code == 0 and (_LOADED_FAULTS or getattr(sys, "_loaded_code_fault", False)):
        sys.stderr.write("{}: {} background fault(s) from loaded code over a passing verdict; "
                         "fail-closed\n".format(_FAULT_MARKER, len(_LOADED_FAULTS) or 1))
        code = 2
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        if code == 0:
            code = 2
    os._exit(code)


def _fail_closed_main(run):
    """The canonical process entry: install the fault recorders, run the gate's own work, settle, end."""
    _install_fault_hooks()
    try:
        code = run()
    except SystemExit as exc:
        code = exc.code
    _gate_exit(code)


# The background fault channels, each driven through the gate's REAL _fail_closed_main in a child that
# loads one fixture the way this gate loads code in process: a clean load passes; an atexit callback
# registered by loaded code never runs (unreachable behind os._exit); a worker-thread fault and a
# destructor fault are recorded and force exit 2.
_FAULT_CHANNEL_PROBE = """import importlib.util, sys
tool, fixture = sys.argv[1:3]
sys.path.insert(0, tool.rsplit("/", 1)[0])
spec = importlib.util.spec_from_file_location("_fault_channel_probe_target", tool)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
def load():
    case = importlib.util.spec_from_file_location("_fault_channel_fixture", fixture)
    module = importlib.util.module_from_spec(case)
    case.loader.exec_module(module)
    return 0
gate._fail_closed_main(load)
"""

# (label, fixture source, expected exit, marker required, text that must NOT appear on stderr)
_FAULT_CHANNEL_CASES = (
    ("clean", "x = 1\n", 0, False, None),
    ("atexit", "import atexit, sys\n"
     "def callback():\n"
     "    sys.stderr.write('FAULT-FIXTURE-ATEXIT-RAN\\n')\n"
     "    raise RuntimeError('fault-fixture-atexit')\n"
     "atexit.register(callback)\n", 0, False, "FAULT-FIXTURE-ATEXIT-RAN"),
    ("joined-thread", "import threading\n"
     "def fault():\n"
     "    raise RuntimeError('fault-fixture-thread')\n"
     "worker = threading.Thread(target=fault)\n"
     "worker.start()\n"
     "worker.join()\n", 2, True, None),
    ("destructor", "import gc\n"
     "class Fault:\n"
     "    def __del__(self):\n"
     "        raise RuntimeError('fault-fixture-destructor')\n"
     "Fault()\n"
     "gc.collect()\n", 2, True, None),
)


def _fault_channel_vectors():
    """One failure string per fault-channel case whose child did not end as required: the expected exit
    code, the fault marker exactly when a fault must be recorded, and for the atexit case no trace of the
    callback (it must never run). Red without the entry wrapper (the probe then ends 1, AttributeError on
    _fail_closed_main, where clean and atexit expect 0) and red with the wrapper reverted to a plain exit
    (the thread and destructor children then end 0 with the default hooks' output alone, where exit 2 with
    the marker is required, and the atexit child then runs the callback)."""
    import subprocess
    import tempfile
    failures = []
    tool = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory(prefix="check-footer-fault-channel-") as tmp:
        probe = Path(tmp) / "probe.py"
        probe.write_text(_FAULT_CHANNEL_PROBE, encoding="utf-8")
        for index, (label, body, expected, marked, absent) in enumerate(_FAULT_CHANNEL_CASES):
            case = Path(tmp) / "fault_channel_{}.py".format(index)
            case.write_text(body, encoding="utf-8")
            try:
                child = subprocess.run([sys.executable, "-I", "-B", str(probe), tool, str(case)],
                                       capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                failures.append("fault-channel/{}: probe did not run ({})".format(label, type(exc).__name__))
                continue
            if child.returncode != expected:
                failures.append("fault-channel/{}: expected exit {}, got {}".format(
                    label, expected, child.returncode))
            elif marked != ((_FAULT_MARKER + ":") in child.stderr):
                failures.append("fault-channel/{}: fault marker {}".format(
                    label, "absent" if marked else "present"))
            elif absent is not None and absent in child.stderr:
                failures.append("fault-channel/{}: the atexit callback ran".format(label))
    return failures


if __name__ == "__main__":
    _fail_closed_main(main)
