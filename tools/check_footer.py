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

The site/ tree is a REQUIRED coverage input: if it is absent, unreadable, or carries no .html pages,
the gate fails closed (exit 2) rather than reporting a clean pass over nothing. A page must be a REGULAR
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


def disclosure_link_count(text):
    """Number of <a href="/disclosure"> links nested inside a <nav> in one page's HTML."""
    parser = _Anchors()
    parser.feed(text)
    return sum(1 for href in parser.hrefs if href == DISCLOSURE_HREF)


def check_pages(pages, allowlist):
    """pages: {name: html_text}. Return a sorted list of findings. Enforces exactly one /disclosure link
    in the nav on every page not in allowlist, and treats a missing or newly-linking allowlisted page as
    drift. Callers guarantee pages is non-empty; emptiness is a fail-closed input error handled in run()."""
    findings = []
    for name in sorted(allowlist):
        if name not in pages:
            findings.append(
                "{}: allowlisted for the nav link but no such page exists (allowlist drift)".format(name))
    for name in sorted(pages):
        count = disclosure_link_count(pages[name])
        if name in allowlist:
            if count:
                findings.append(
                    "{}: allowlisted as nav-exempt but now carries {} {} link(s); "
                    "remove it from the allowlist (allowlist drift)".format(name, count, DISCLOSURE_HREF))
        elif count == 0:
            findings.append("{}: missing the {} nav link".format(name, DISCLOSURE_HREF))
        elif count > 1:
            findings.append(
                "{}: carries {} {} links (expected exactly one)".format(name, count, DISCLOSURE_HREF))
    return findings


def _read_regular_page(path):
    """Read a page's text through a fail-closed, symlink-safe open. Opens O_NOFOLLOW (a symlink at the final
    component fails - never followed) + O_NONBLOCK (a FIFO open returns instead of blocking), fstats the
    OPENED fd to confirm a regular file (closing the check-then-read TOCTOU: the fd type-checked is the fd
    read), then reads UTF-8. Raises OSError (open or non-regular type) or UnicodeDecodeError; the caller
    fails closed. A symlink, FIFO, socket, or device raises here rather than being read or followed."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("not a regular file (symlink, FIFO, socket, or device)")
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = -1
            return fh.read()
    finally:
        if fd >= 0:
            os.close(fd)


def run(root):
    """Scan root/site. Return an exit code: 0 clean, 1 a coverage finding, 2 a missing/unreadable/empty
    required input (fail-closed). site/ is a required coverage input: absent, unreadable, or page-less
    site/ is exit 2, never a clean pass over nothing."""
    site = root / "site"
    if site.is_symlink():
        print("error: site/ is a symlink; the coverage root must be a real directory in the tree; "
              "fail-closed", file=sys.stderr)
        return 2
    if not site.is_dir():
        print("error: required input site/ is absent under {}; the nav-coverage gate cannot evaluate; "
              "fail-closed".format(root), file=sys.stderr)
        return 2
    try:
        html_files = sorted(walk_files(site, suffixes={".html"}))
    except OSError as exc:
        print("error: cannot scan site/ ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if not html_files:
        print("error: site/ contains no .html pages to cover; a page-less required input is fail-closed",
              file=sys.stderr)
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
    findings = check_pages(pages, ALLOWLIST)
    if findings:
        print("FAIL: {} nav-coverage issue(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    allow_str = ", ".join(sorted(ALLOWLIST)) or "none"
    print("PASS: every site page carries the /disclosure nav link (allowlist: {})".format(allow_str))
    return 0


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

    def quiet_run(r):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run(r)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        if quiet_run(root) != 2:
            failures.append("absent site/ did not fail closed (expected exit 2)")
        (root / "site").mkdir()
        if quiet_run(root) != 2:
            failures.append("page-less site/ did not fail closed (expected exit 2)")
        (root / "site" / "about.html").write_text(nav, encoding="utf-8")
        if quiet_run(root) != 0:
            failures.append("a covered site/ did not pass (expected exit 0)")
        (root / "site" / "bad.html").write_text("<nav></nav>", encoding="utf-8")
        if quiet_run(root) != 1:
            failures.append("an uncovered page did not report a finding (expected exit 1)")
        (root / "site" / "zz-undecodable.html").write_bytes(b"\xff\xfe<nav></nav>")
        if quiet_run(root) != 2:
            failures.append("an undecodable page did not fail closed (expected exit 2, not a traceback)")
    with tempfile.TemporaryDirectory() as d2:
        root2 = Path(d2)
        (root2 / "site").mkdir()
        (root2 / "site" / "ok.html").write_text(nav, encoding="utf-8")
        os.symlink("/etc/hostname", str(root2 / "site" / "link.html"))  # a non-regular page object
        if quiet_run(root2) != 2:
            failures.append("a non-regular (symlink) page did not fail closed (expected exit 2, no follow)")
        os.remove(str(root2 / "site" / "link.html"))
        os.mkfifo(str(root2 / "site" / "pipe.html"))  # a FIFO must not hang the open; fstat rejects it
        if quiet_run(root2) != 2:
            failures.append("a FIFO page did not fail closed (expected exit 2, no hang)")
        os.remove(str(root2 / "site" / "pipe.html"))
    with tempfile.TemporaryDirectory() as d3:
        root3 = Path(d3)
        (root3 / "realsite").mkdir()
        (root3 / "realsite" / "ok.html").write_text(nav, encoding="utf-8")
        os.symlink(str(root3 / "realsite"), str(root3 / "site"))  # site/ itself a symlink -> rejected
        if quiet_run(root3) != 2:
            failures.append("a symlinked site/ root did not fail closed (expected exit 2)")
    if failures:
        print("FAIL: check_footer self-test")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: check_footer self-test ({} check_pages cases + run() exit-code legs)".format(len(cases)))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    return run(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    sys.exit(main())
