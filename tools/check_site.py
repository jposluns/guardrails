#!/usr/bin/env python3
"""Site-integrity gate: en/em dashes, internal link resolution, and anchor validation for site/*.html.

Link classification uses urlsplit: an absolute URL on the site's own host (aiqt.ai) is treated as
internal; every other scheme (http/https elsewhere, mailto, tel, ftp, javascript, data, ...) and
protocol-relative //host links are external and skipped. An internal path resolves to an existing file
under site/ by trying the path as-is, path + ".html", and path + "/index.html" (so /roadmap, /styles.css,
and /section/ all work with no extension heuristic); "/" resolves to index.html; a path escaping site/
(e.g. /../x) is broken. A fragment (#id) is validated against the target page's element ids; an
anchor-only or query-only href is validated against the current page. Exit 0 clean, 1 on any finding.
"""
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

EN, EM = "–", "—"
SITE_HOSTS = {"aiqt.ai", "www.aiqt.ai"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []   # (value, line)
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for key in ("href", "src"):
            if d.get(key):
                self.links.append((d[key], self.getpos()[0]))
        for key in ("id", "name"):
            if d.get(key):
                self.ids.add(d[key])


def _under(site_root, resolved):
    return resolved == site_root or str(resolved).startswith(str(site_root) + os.sep)


def resolve_link(base, path, site_root):
    """Return the existing site file a path points to, or None (broken / escapes site)."""
    p = path.rstrip("/")
    if p == "":
        candidates = [site_root / "index.html"]
    else:
        rel = p.lstrip("/") if path.startswith("/") else p
        candidates = [base / rel, base / (rel + ".html"), base / rel / "index.html"]
    for cand in candidates:
        resolved = cand.resolve()
        if _under(site_root, resolved) and resolved.is_file():
            return resolved
    return None


def classify(v):
    """Return (pathpart, fragment) for an internal link, or None if external/skippable."""
    parts = urlsplit(v)
    if parts.scheme:
        if parts.scheme.lower() in ("http", "https") and parts.netloc.lower() in SITE_HOSTS:
            return parts.path, parts.fragment
        return None                     # any other scheme is external
    if parts.netloc:                    # protocol-relative //host
        return None
    return parts.path, parts.fragment


def main():
    root = Path(__file__).resolve().parents[1]
    site = root / "site"
    if not site.is_dir():
        print("PASS: no site/ directory")
        return 0
    site_root = site.resolve()
    docs, ids_by_path, findings = [], {}, []
    for f in sorted(site.rglob("*.html")):
        rel = f.relative_to(root)
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            findings.append("{}: could not read as UTF-8".format(rel))
            continue
        page = Page()
        try:
            page.feed(text)
        except (ValueError, AssertionError):
            page = None
        docs.append((f, rel, text, page))
        ids_by_path[str(f.resolve())] = (page.ids if page else None)
    for f, rel, text, page in docs:
        for number, line in enumerate(text.splitlines(), 1):
            if EN in line:
                findings.append("{}:{}: en dash".format(rel, number))
            if EM in line:
                findings.append("{}:{}: em dash".format(rel, number))
        if page is None:
            findings.append("{}: could not parse as HTML".format(rel))
            continue
        for value, line in page.links:
            v = value.strip()
            if not v:
                continue
            classified = classify(v)
            if classified is None:
                continue
            pathpart, frag = classified
            if pathpart == "":                        # anchor-only or query-only: the current page
                if not frag:
                    continue
                anchor_key = str(f.resolve())
            else:
                base = site_root if pathpart.startswith("/") else f.parent
                target = resolve_link(base, pathpart, site_root)
                if target is None:
                    findings.append("{}:{}: broken internal link -> {}".format(rel, line, v))
                    continue
                anchor_key = str(target)
            if frag:
                ids = ids_by_path.get(anchor_key)
                if ids is not None and frag not in ids:
                    findings.append("{}:{}: broken anchor -> {} (#{} not found)".format(rel, line, v, frag))
    if findings:
        print("FAIL: {} site-integrity issue(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: site dashes, internal links, and anchors all resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
