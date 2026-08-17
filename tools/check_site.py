#!/usr/bin/env python3
"""Site-integrity gate for site/*.html: en/em dashes, link/anchor resolution, and basic HTML validity.

Link classification uses urlsplit: an absolute URL on the site's own host (aiqt.ai) is internal; every
other scheme (http/https elsewhere, mailto, tel, ftp, javascript, data, ...) and protocol-relative
//host links are external and skipped. An internal path resolves to an existing file under site/ by
trying the path as-is, path + ".html", and path + "/index.html"; "/" resolves to index.html; a path
escaping site/ (e.g. /../x) is broken. A fragment (#id) is validated against the target page's ids; an
anchor-only or query-only href is validated against the current page.

Basic HTML validity: a page must have a non-empty <title>, and must not reuse an id (a duplicate id is
invalid and silently breaks in-page anchors).

Tag structure: open/close balance and nesting, with void elements, self-closing tags (a trailing slash
is honoured on void and foreign svg/math elements, ignored on non-void HTML so <div/> stays open), and
HTML5 optional-close tables. The subtree of svg/math and of <textarea> (and of <script>/<style> via
parser CDATA) is NOT structurally validated, so embedded content never false-positives; the root's own
open/close balance IS checked, so an unclosed one is caught, and links/ids inside such a subtree are not
validated (a deferred coverage gap). Non-nestable nesting IS detected (slice-2): a <form> inside a
<form>, and an interactive element (<a>/<button>) inside another interactive element, both of which the
parser accepts as well-balanced but which break rendering/behaviour. Download-artifact checksums are
tracked separately (they need a final content baseline). Exit 0 clean, 1 on any finding, 2 on a read error (unreadable dir/file, fail-closed).
"""
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

EN, EM = "–", "—"
SITE_HOSTS = {"aiqt.ai", "www.aiqt.ai"}

# Tags that never take an end tag; never pushed on the open-tag stack.
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}
# Roots whose SUBTREE is not structurally validated in this slice: svg/math (foreign, XML-ish, with
# different self-closing rules) and textarea (raw text, not markup). Structure INSIDE them is a slice-2
# non-goal; the root's own open/close balance IS still checked, so an unclosed one is caught.
SUSPEND_ROOTS = {"svg", "math", "textarea"}
# HTML5 elements whose end tag may be legally omitted (left open or auto-closed);
# an implicit close of one of these is not a structural finding.
OPTIONAL_END_TAGS = {
    "html", "head", "body", "li", "p", "dt", "dd",
    "option", "optgroup", "thead", "tbody", "tfoot", "tr", "td", "th",
    "rt", "rp", "colgroup",
}
# Sibling start tags that auto-close an open optional-end-tag element
# (WHATWG tree construction, simplified to the common cases).
AUTOCLOSERS = {
    "li": {"li"},
    "p": {"address", "article", "aside", "blockquote", "details", "div", "dl", "fieldset",
          "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6",
          "header", "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "section",
          "summary", "table", "ul"},
    "option": {"option", "optgroup"},
    "optgroup": {"optgroup"},
    "tr": {"tr"},
    "td": {"td", "th", "tr"},
    "th": {"td", "th", "tr"},
    "thead": {"tbody", "tfoot"},
    "tbody": {"tbody", "tfoot"},
    "tfoot": {"tbody"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
}
# Non-nestable content models (WHATWG), the slice-2 subset: a <form> may not contain another <form>, and
# an interactive element (<a>/<button>) may not contain another interactive element. These nest silently
# in the parser (well-balanced) but break rendering/behaviour, so open/close balance never catches them.
INTERACTIVE = {"a", "button"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []       # (value, line)
        self.ids = set()
        self.id_list = []     # (value, line) for duplicate detection
        self.title_present = False
        self.title_text = ""
        self._in_title = False
        self.stack = []           # open non-void tags: (tag, line)
        self.tag_findings = []    # (line, msg) structural findings
        self.suspend_tag = None   # SUSPEND_ROOT we are inside (subtree not checked), or None
        self.suspend_count = 0    # nesting of that same root, so the matching close ends suspension

    def handle_starttag(self, tag, attrs):
        if self.suspend_tag is not None:
            if tag == self.suspend_tag:
                self.suspend_count += 1
            return
        d = dict(attrs)
        for key in ("href", "src"):
            if d.get(key):
                self.links.append((d[key], self.getpos()[0]))
        if d.get("id"):
            self.ids.add(d["id"])
            self.id_list.append((d["id"], self.getpos()[0]))
        # Only <a name="..."> is a fragment-navigable anchor target; a `name` on any other element
        # (input/meta/form/param/...) is not, so registering those would let a broken #anchor pass.
        if tag == "a" and d.get("name"):
            self.ids.add(d["name"])
        if tag == "title":
            self.title_present = True
            self._in_title = True
        while self.stack and tag in AUTOCLOSERS.get(self.stack[-1][0], ()):
            self.stack.pop()
        # Non-nestable check, before this tag is pushed. A <form> may not be nested in a <form>. For the
        # interactive family two distinct HTML rules apply: (1) any <a> forbids an <a> descendant (nested
        # anchors, regardless of href), and (2) <a>/<button> forbid an INTERACTIVE descendant, where <a>
        # is interactive only WITH an href (a bare <a> is legal phrasing content, e.g. inside a <button>)
        # and <button> is always interactive.
        if tag == "form":
            if any(t == "form" for t, _ in self.stack):
                self.tag_findings.append(
                    (self.getpos()[0], "<form> must not be nested inside another <form>"))
        elif tag in INTERACTIVE:
            if tag == "a" and any(t == "a" for t, _ in self.stack):
                self.tag_findings.append(
                    (self.getpos()[0], "<a> must not be nested inside another <a>"))
            elif tag == "button" or "href" in d:
                bad = next((t for t, _ in reversed(self.stack) if t in INTERACTIVE), None)
                if bad is not None:
                    self.tag_findings.append((self.getpos()[0],
                        "<{}> (interactive content) must not be nested inside <{}>".format(tag, bad)))
        if tag not in VOID_ELEMENTS:
            self.stack.append((tag, self.getpos()[0]))
            if tag in SUSPEND_ROOTS:
                self.suspend_tag = tag       # enter suspension; the root stays at the top of the stack
                self.suspend_count = 1

    def handle_startendtag(self, tag, attrs):
        if self.suspend_tag is not None:
            return
        if tag in VOID_ELEMENTS:
            self.handle_starttag(tag, attrs)   # extract href/src/id; void is not pushed on the stack
            return
        if tag in SUSPEND_ROOTS:
            d = dict(attrs)                    # <svg id=.. />: register id, but do not enter suspension.
            if d.get("id"):                    # (name on svg/math/textarea is not an <a> anchor target)
                self.ids.add(d["id"])
                self.id_list.append((d["id"], self.getpos()[0]))
            return
        # a non-void HTML element with a trailing slash is NOT self-closed (the parser ignores the
        # slash), so <div/> is an open <div> that must be closed later.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if self.suspend_tag is not None:
            if tag == self.suspend_tag:
                self.suspend_count -= 1
                if self.suspend_count == 0:
                    self.stack.pop()          # the suspend root is at the top; nothing pushed inside
                    self.suspend_tag = None
            return
        if tag in VOID_ELEMENTS:
            self.tag_findings.append(
                (self.getpos()[0], "end tag </{}> for a void element (invalid HTML)".format(tag)))
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for t, line in self.stack[i + 1:]:
                    if t not in OPTIONAL_END_TAGS:
                        self.tag_findings.append(
                            (line, "<{}> opened but never closed (implicitly closed by </{}> at line {})".format(
                                t, tag, self.getpos()[0])))
                del self.stack[i:]
                return
        self.tag_findings.append(
            (self.getpos()[0], "end tag </{}> has no matching open tag".format(tag)))

    def finalize(self):
        for tag, line in self.stack:
            if tag not in OPTIONAL_END_TAGS:
                self.tag_findings.append(
                    (line, "<{}> opened but never closed before end of document".format(tag)))
        self.stack = []

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
    # urlsplit does not percent-decode. The PATH is unquoted so an encoded link (my%20page.html) resolves
    # against the real file "my page.html". The FRAGMENT is returned RAW: HTML fragment navigation matches
    # an id equal to the raw fragment FIRST, then the percent-decoded form, so the anchor check tries both
    # (see main()). Unquoting the fragment here unconditionally would drop the raw-match case.
    parts = urlsplit(v)
    if parts.scheme:
        if parts.scheme.lower() in ("http", "https") and parts.netloc.lower() in SITE_HOSTS:
            return unquote(parts.path), parts.fragment
        return None
    if parts.netloc:
        return None
    return unquote(parts.path), parts.fragment


def main():
    root = Path(__file__).resolve().parents[1]
    site = root / "site"
    if not site.is_dir():
        print("PASS: no site/ directory")
        return 0
    site_root = site.resolve()
    docs, ids_by_path, findings = [], {}, []
    try:
        html_files = sorted(walk_files(site, suffixes={".html"}))
    except OSError as exc:
        # an unreadable directory under site/ is a read error, not a clean skip: fail closed (exit 2)
        # so the site gate never reports clean without having scanned an unreadable subtree.
        print("error: cannot scan site/ ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    for f in html_files:
        rel = f.relative_to(root)
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append("{}: could not read as UTF-8".format(rel))
            continue
        except OSError as exc:
            print("error: cannot read {} ({}); fail-closed".format(rel, exc), file=sys.stderr)
            return 2
        page = Page()
        try:
            page.feed(text)
            page.finalize()
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
        for line, msg in page.tag_findings:
            findings.append("{}:{}: {}".format(rel, line, msg))
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
                # Match the id against the RAW fragment first, then its percent-decoded form, mirroring
                # HTML fragment navigation (raw match, else decoded match).
                if ids is not None and frag not in ids and unquote(frag) not in ids:
                    findings.append("{}:{}: broken anchor -> {} (#{} not found)".format(rel, line, v, frag))
    if findings:
        print("FAIL: {} site-integrity issue(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: site dashes, links, anchors, titles, unique ids, and tag structure all check out")
    return 0


if __name__ == "__main__":
    sys.exit(main())
