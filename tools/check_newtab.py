#!/usr/bin/env python3
"""New-tab gate: every EXTERNAL link in site/*.html opens in a new tab, safely.

Every off-site link (a host other than aiqt.ai or a subdomain) must carry target="_blank" AND a rel that
includes "noopener" (target=_blank without noopener is a reverse-tabnabbing risk). Internal (relative) links
and same-site aiqt.ai links are out of scope. This keeps the new-tab behaviour from silently rotting when a
future hand-authored off-site link is added without the attributes.

Covered navigation surfaces: HTML <a href>, SVG <a xlink:href> (when plain href is absent), and <area href>;
plus any <base href> is flagged fail-closed (it can retarget relative links off-site). Residual (disclosed
per the pack's disclose-guard-residuals rule, and erring toward disclosure rather than silent coverage): the
gate operates on the project's OWN trusted, well-formed HTML and does not cover other or future navigation
surfaces (for example script-driven navigation, or link elements outside the set above). The project authors
none of those today; this is a mistake-catcher for a forgotten new-tab attribute, not an adversarial
validator of attacker-controlled hrefs.

site/ is a REQUIRED coverage input: absent, unreadable, or page-less site/ fails closed (exit 2), never a
clean pass over nothing. A page opened symlink-safe is not needed here (read-only text scan of tracked
pages); this gate reads the same files the site drift gates already cover.

  check_newtab.py             scan site/*.html
  check_newtab.py --self-test  present/missing-target/missing-rel/internal-skip/fail-closed cases

Exit 0 clean, 1 on any finding, 2 on a missing/unreadable required input (fail-closed).
"""
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk
from _gen_common import is_external_url  # noqa: E402


class _Anchors(HTMLParser):
    """Collect (href, target, rel) for every <a>/<area>/SVG-<a xlink:href> link, and any <base href>."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.base_hrefs = []

    def _first_attrs(self, attrs):
        first = {}
        for k, v in attrs:          # HTML keeps the FIRST duplicate attribute; dict(attrs) would keep the last
            first.setdefault(k, v)
        return first

    def handle_starttag(self, tag, attrs):
        if tag in ("a", "area"):
            first = self._first_attrs(attrs)
            href = first.get("href")
            if href is None and tag == "a":
                href = first.get("xlink:href")   # SVG <a> is navigable via xlink:href when plain href is absent
            if href is not None:
                self.links.append((href, first.get("target") or "", first.get("rel") or ""))
        elif tag == "base":
            href = self._first_attrs(attrs).get("href")
            if href is not None:
                self.base_hrefs.append(href)


def page_findings(name, text):
    """Findings for one page: each external link missing target=_blank or a noopener rel, plus any
    <base href>, which is unsupported because it can silently retarget relative links off-site (the
    project's pages carry no <base>; the gate fails closed on one rather than mis-resolving against it)."""
    parser = _Anchors()
    parser.feed(text)
    out = []
    for base in parser.base_hrefs:
        out.append("{}: unsupported <base href> {!r} (it can retarget relative links off-site; "
                   "the new-tab gate does not resolve against it)".format(name, base))
    for href, target, rel in parser.links:
        if not is_external_url(href):
            continue
        if target.strip() != "_blank":
            out.append("{}: external link {!r} is missing target=\"_blank\"".format(name, href))
        elif "noopener" not in rel.lower().split():
            out.append("{}: external link {!r} has target=_blank without a noopener rel "
                       "(reverse-tabnabbing risk)".format(name, href))
    return out


def run(root):
    site = root / "site"
    if site.is_symlink() or not site.is_dir():
        print("error: site/ absent or a symlink; the new-tab gate cannot evaluate; fail-closed",
              file=sys.stderr)
        return 2
    try:
        html_files = sorted(walk_files(site, suffixes={".html"}))
    except OSError as exc:
        print("error: cannot scan site/ ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if not html_files:
        print("error: site/ contains no .html pages; a page-less required input is fail-closed",
              file=sys.stderr)
        return 2
    findings = []
    for f in html_files:
        name = str(f.relative_to(site))
        try:
            findings += page_findings(name, f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            print("error: cannot load {} ({}); fail-closed".format(f.relative_to(root), exc), file=sys.stderr)
            return 2
    if findings:
        print("FAIL: {} external link(s) not opening safely in a new tab".format(len(findings)))
        for x in sorted(set(findings)):
            print("  " + x)
        return 1
    print("PASS: every external site link opens in a new tab with a noopener rel")
    return 0


def _self_test():
    ext_ok = '<a href="https://github.com/x" target="_blank" rel="noopener noreferrer">gh</a>'
    cases = [
        ("external ok", ext_ok, []),
        ("external missing target",
         '<a href="https://github.com/x" rel="noopener">gh</a>',
         ['p.html: external link \'https://github.com/x\' is missing target="_blank"']),
        ("external target no noopener",
         '<a href="https://github.com/x" target="_blank" rel="noreferrer">gh</a>',
         ["p.html: external link 'https://github.com/x' has target=_blank without a noopener rel "
          "(reverse-tabnabbing risk)"]),
        ("internal link skipped", '<a href="/mappings">m</a>', []),
        ("aiqt.ai absolute skipped", '<a href="https://aiqt.ai/x">x</a>', []),
        ("aiqt.ai subdomain-prefix host is EXTERNAL",
         '<a href="https://aiqt.ai.evil.example/x">x</a>',
         ['p.html: external link \'https://aiqt.ai.evil.example/x\' is missing target="_blank"']),
        ("aiqt.ai in userinfo, host external",
         '<a href="https://aiqt.ai@evil.example/x">x</a>',
         ['p.html: external link \'https://aiqt.ai@evil.example/x\' is missing target="_blank"']),
        ("aiqt.ai in path, host external",
         '<a href="https://evil.example/path/aiqt.ai">x</a>',
         ['p.html: external link \'https://evil.example/path/aiqt.ai\' is missing target="_blank"']),
        ("uppercase scheme external",
         '<a href="HTTPS://evil.example/x">x</a>',
         ['p.html: external link \'HTTPS://evil.example/x\' is missing target="_blank"']),
    ]
    # Fail-closed classes (round-5, Codex): a browser-divergent backslash authority, a malformed
    # http(s) URL, and a protocol-relative URL each classify EXTERNAL, so an unattributed one is a
    # finding. Expected strings are computed with %r to match page_findings' own {!r} formatting.
    for _h in ("https://evil.example\\@aiqt.ai/x", "https://[invalid/x", "//evil.example/x"):
        cases.append(("fail-closed external %r" % _h, '<a href="%s">x</a>' % _h,
                      ['p.html: external link %r is missing target="_blank"' % _h]))
    # A genuine aiqt.ai host behind userinfo is internal (a browser agrees: host is aiqt.ai).
    cases.append(("userinfo with genuine aiqt.ai host is internal",
                  '<a href="https://user@aiqt.ai/x">x</a>', []))
    # Duplicate attributes: HTML keeps the FIRST (browser behaviour); the gate must classify on it.
    cases.append(("duplicate href: first (external) wins -> finding",
                  '<a href="https://evil.example/x" href="/internal">x</a>',
                  ['p.html: external link \'https://evil.example/x\' is missing target="_blank"']))
    cases.append(("duplicate target: first (_blank) wins -> external link is safe, no finding",
                  '<a href="https://evil.example/x" target="_blank" target="_self" rel="noopener">x</a>', []))
    # Missing-solidus special-scheme (WHATWG normalizes to '//'): each resolves to an off-site host.
    for _h in ("http:/evil.example/x", "http:\\evil.example/x", "http:evil.example/x"):
        cases.append(("missing-solidus external %r" % _h, '<a href="%s">x</a>' % _h,
                      ['p.html: external link %r is missing target="_blank"' % _h]))
    # Same-SITE links (other scheme/port on aiqt.ai, or a subdomain) are internal by design: no finding.
    for _h in ("http://aiqt.ai/x", "https://aiqt.ai:444/x", "https://sub.aiqt.ai/x"):
        cases.append(("same-site internal %r" % _h, '<a href="%s">x</a>' % _h, []))
    # A confusable registrable domain is EXTERNAL (pins the .aiqt.ai dot-boundary; endswith('aiqt.ai') would miss it).
    for _h in ("https://notaiqt.ai/x", "https://xaiqt.ai/x"):
        cases.append(("confusable registrable domain external %r" % _h, '<a href="%s">x</a>' % _h,
                      ['p.html: external link %r is missing target="_blank"' % _h]))
    # <base href> is unsupported: it can retarget relative links off-site, so the gate fails closed on it.
    cases.append(("base href flagged fail-closed",
                  '<base href="https://evil.example/root/"><a href="child">x</a>',
                  ["p.html: unsupported <base href> 'https://evil.example/root/' (it can retarget "
                   "relative links off-site; the new-tab gate does not resolve against it)"]))
    # SVG <a xlink:href> is navigable when plain href is absent; an off-site one needs the attrs.
    cases.append(("svg anchor via xlink:href external -> finding",
                  '<svg><a xlink:href="https://evil.example/x"><text>x</text></a></svg>',
                  ['p.html: external link \'https://evil.example/x\' is missing target="_blank"']))
    cases.append(("svg anchor plain href wins over xlink:href (mutation-sensitive)",
                  '<svg><a href="/internal" xlink:href="https://evil.example/x"><text>x</text></a></svg>',
                  []))
    # <area href> (image map) is a link too; an off-site one needs the attrs.
    cases.append(("area href external -> finding",
                  '<map><area href="https://evil.example/x"></map>',
                  ['p.html: external link \'https://evil.example/x\' is missing target="_blank"']))
    failures = []
    for label, html, expected in cases:
        got = sorted(page_findings("p.html", html))
        if got != sorted(expected):
            failures.append("{}: expected {} got {}".format(label, sorted(expected), got))
    # run() fail-closed on absent/empty site
    import tempfile
    import contextlib
    import io

    def quiet(r):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run(r)
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        if quiet(r) != 2:
            failures.append("absent site/ did not fail closed")
        (r / "site").mkdir()
        if quiet(r) != 2:
            failures.append("page-less site/ did not fail closed")
        (r / "site" / "a.html").write_text(ext_ok, encoding="utf-8")
        if quiet(r) != 0:
            failures.append("a covered page did not pass")
        (r / "site" / "b.html").write_text('<a href="https://x.com/y">y</a>', encoding="utf-8")
        if quiet(r) != 1:
            failures.append("an uncovered external link did not report a finding")
    if failures:
        print("FAIL: check_newtab self-test")
        for x in failures:
            print("  " + x)
        return 1
    print("PASS: check_newtab self-test ({} page cases + run() exit-code legs)".format(len(cases)))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    return run(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    sys.exit(main())
