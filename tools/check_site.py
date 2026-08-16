#!/usr/bin/env python3
"""Site-integrity gate for site/*.html: en/em dashes, link/anchor resolution, and basic HTML validity.

Link classification uses urlsplit: an absolute URL on the site's own host (aiqt.ai) is internal; every
other scheme (http/https elsewhere, mailto, tel, ftp, javascript, data, ...) and protocol-relative
//host links are external and skipped. An internal path resolves to an existing file under site/ by
trying the path as-is, path + ".html", and path + "/index.html"; "/" resolves to index.html; a path
escaping site/ (e.g. /../x) is broken. A fragment (#id) is validated against the target page's ids; an
anchor-only or query-only href is validated against the current page.

Basic HTML validity: a page must have a non-empty <title>, and must not reuse an id (a duplicate id is
invalid and silently breaks in-page anchors). Deeper validation and download-artifact checksums are
tracked separately (they need a final content baseline). Exit 0 clean, 1 on any finding.
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
        self.links = []       # (value, line)
        self.ids = set()
        self.id_list = []     # (value, line) for duplicate detection
        self.title_present = False
        self.title_text = ""
        self._in_title = False
        self.svg_depth = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for key in ("href", "src"):
            if d.get(key):
                self.links.append((d[key], self.getpos()[0]))
        if d.get("id"):
            self.ids.add(d["id"])
            self.id_list.append((d["id"], self.getpos()[0]))
        if d.get("name"):
            self.ids.add(d["name"])
        if tag == "svg":
            self.svg_depth += 1
        if tag == "title" and self.svg_depth == 0:
            self.title_present = True
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "svg":
            self.svg_depth = max(0, self.svg_depth - 1)
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title_text += data


def _under(site_root, resolved):
    return resolved == site_root or str(resolved).startswith(str(site_root) + os.sep)


def resolve_link(base, path, site_root):
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
    parts = urlsplit(v)
    if parts.scheme:
        if parts.scheme.lower() in ("http", "https") and parts.netloc.lower() in SITE_HOSTS:
            return parts.path, parts.fragment
        return None
    if parts.netloc:
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
        if not page.title_present or not page.title_text.strip():
            findings.append("{}: missing or empty <title>".format(rel))
        seen = {}
        for value, line in page.id_list:
            seen.setdefault(value, []).append(line)
        for value, lines in seen.items():
            if len(lines) > 1:
                findings.append("{}:{}: duplicate id '{}' (repeated at line(s) {})".format(
                    rel, lines[0], value, ", ".join(str(n) for n in lines[1:])))
        for value, line in page.links:
            v = value.strip()
            if not v:
                continue
            classified = classify(v)
            if classified is None:
                continue
            pathpart, frag = classified
            if pathpart == "":
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
    print("PASS: site dashes, links, anchors, titles, and unique ids all check out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
