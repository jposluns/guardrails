#!/usr/bin/env python3
"""Footer-coverage gate: every site/*.html carries exactly one /disclosure footer link.

The disclosure matrix is the site's standing "what we claim, and what we do not" page; every public
page must point to it from its footer so a reader is never more than one click from the limitations.
This gate asserts that link is present on every page EXCEPT an explicit allowlist, and fails closed if
a non-exempt page lacks it or the allowlist drifts.

The allowlist is exactly the pre-launch splash (site/index.html), which carries no footer at all. It is
an EXPLICIT exemption, not a silent skip: if an allowlisted page is missing, or an allowlisted page
starts carrying the link, that is allowlist drift and fails, so the exemption cannot quietly outlive its
reason.

  check_footer.py             scan site/*.html
  check_footer.py --self-test  assert the present/missing/drift paths each resolve correctly

Exit 0 clean, 1 on any finding, 2 on a read error (unreadable dir/file, fail-closed).
"""
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

DISCLOSURE_HREF = "/disclosure"
# Pages exempt from the footer-link requirement, relative to site/. The pre-launch splash has no footer.
ALLOWLIST = frozenset({"index.html"})


class _Anchors(HTMLParser):
    """Collect the href of every <a> tag."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            d = dict(attrs)
            if d.get("href") is not None:
                self.hrefs.append(d["href"].strip())


def disclosure_link_count(text):
    """Number of <a href="/disclosure"> links in one page's HTML."""
    parser = _Anchors()
    parser.feed(text)
    return sum(1 for href in parser.hrefs if href == DISCLOSURE_HREF)


def check_pages(pages, allowlist):
    """pages: {name: html_text}. Return a sorted list of findings. Enforces exactly one /disclosure link
    on every page not in allowlist, and treats a missing or newly-linking allowlisted page as drift."""
    findings = []
    for name in sorted(allowlist):
        if name not in pages:
            findings.append(
                "{}: allowlisted for the footer link but no such page exists (allowlist drift)".format(name))
    for name in sorted(pages):
        count = disclosure_link_count(pages[name])
        if name in allowlist:
            if count:
                findings.append(
                    "{}: allowlisted as footer-exempt but now carries {} {} link(s); "
                    "remove it from the allowlist (allowlist drift)".format(name, count, DISCLOSURE_HREF))
        elif count == 0:
            findings.append("{}: missing the {} footer link".format(name, DISCLOSURE_HREF))
        elif count > 1:
            findings.append(
                "{}: carries {} {} links (expected exactly one)".format(name, count, DISCLOSURE_HREF))
    return findings


def _self_test():
    footer = '<footer><a href="/disclosure">Disclosure</a></footer>'
    cases = [
        ("present page passes", {"about.html": footer}, frozenset(), []),
        ("missing link fails", {"about.html": "<footer></footer>"}, frozenset(),
         ["about.html: missing the /disclosure footer link"]),
        ("duplicate link fails", {"about.html": footer + footer}, frozenset(),
         ["about.html: carries 2 /disclosure links (expected exactly one)"]),
        ("allowlisted page without link passes",
         {"index.html": "<footer></footer>"}, frozenset({"index.html"}), []),
        ("allowlisted page WITH link is drift",
         {"index.html": footer}, frozenset({"index.html"}),
         ["index.html: allowlisted as footer-exempt but now carries 1 /disclosure link(s); "
          "remove it from the allowlist (allowlist drift)"]),
        ("stale allowlist entry is drift", {"about.html": footer}, frozenset({"gone.html"}),
         ["gone.html: allowlisted for the footer link but no such page exists (allowlist drift)"]),
    ]
    failures = []
    for label, pages, allow, expected in cases:
        got = sorted(check_pages(pages, allow))
        if got != sorted(expected):
            failures.append("{}: expected {} got {}".format(label, sorted(expected), got))
    if failures:
        print("FAIL: check_footer self-test")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: check_footer self-test ({} cases)".format(len(cases)))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    root = Path(__file__).resolve().parents[1]
    site = root / "site"
    if not site.is_dir():
        print("PASS: no site/ directory")
        return 0
    try:
        html_files = sorted(walk_files(site, suffixes={".html"}))
    except OSError as exc:
        print("error: cannot scan site/ ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    pages = {}
    for f in html_files:
        name = str(f.relative_to(site))
        try:
            pages[name] = f.read_text(encoding="utf-8")
        except OSError as exc:
            print("error: cannot read {} ({}); fail-closed".format(f.relative_to(root), exc), file=sys.stderr)
            return 2
    findings = check_pages(pages, ALLOWLIST)
    if findings:
        print("FAIL: {} footer-coverage issue(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: every site page carries the /disclosure footer link (allowlist: {})".format(
        ", ".join(sorted(ALLOWLIST))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
