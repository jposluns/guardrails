#!/usr/bin/env python3
"""Site-integrity gate for site/*.html: en/em dashes, link/anchor resolution, and basic HTML validity.

Link classification uses urlsplit: an absolute URL on the scanned root's own host (aiqt.ai for site/,
opfiles.ai for opf/site/; ROOT_HOSTS) is internal, so a link to the other site is external; every
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

Logo consistency: within a root, every page's header logo block must be byte-identical to the majority
block of the pages not named 404.html. A 404 page is served at any missing URL, however deep, so it must
open its logo with the root-absolute home link href="/" where that block opens with the relative
href="./" (the relative form is a finding there); every other byte must still match, and no other page
gets this allowance.

404 references: for the same reason a page named 404.html (top-level or nested) is held to an allowlist read
from its raw markup, not from the parsed page: every occurrence of href, src, srcset (each candidate URL),
poster, data, action and xlink:href, duplicates and those inside svg and math included, must start with
exactly one "/" (followed by a character other than "/", a backslash, a tab or a newline, or nothing), or
with "#" or "?". The two loading pairs the live 404 pages use, script src and link href (any rel: they
load a stylesheet, an icon and a touch icon there), must be root-absolute in that one-"/" form only: a
fragment-only or query-only value resolves against the request URL, and https:// names another host. The
one other "https://" the live 404 pages write is on a href, so https:// is accepted on a href and on no
other attribute. The value is tested as written and again with its character references decoded, and is
never percent-decoded, so "%2F..." is refused. Anything else (a relative path, "./", "../", an empty value,
"//host", any other scheme) is a finding naming the attribute and value. A relative touch icon on a 404
page is reported by this rule and by the touch-icon rule below, so it is listed twice.

404 structure: that list of attributes cannot name every way markup loads a resource, so a 404 page is also
held, fail closed, to the element and attribute names the live 404 pages use: any other name the raw scan
finds is a finding naming the page, line and name. A 404 page the site template builds (a 404.html target
of tools/gen_draft_site.py GENSRC_OUTPUTS, read through tools/_template_pages.py: draft/404.html while
staged, the root's 404.html once promoted) is held the same way to the names the two template 404 pages
use instead; any other 404.html, under draft/ or not, keeps the live names. On the allowed names, any value and any script or style
text containing "url(" (any case, as written or decoded) is a finding, a meta element may carry only the
attributes the live pages use on meta, onclick and the SVG presentation attributes only the values they use,
a script without src only a body whose sha256 is pinned, and a script with src only an empty body. A
script start tag at or after the first svg start tag is refused outright: that covers every script inside
svg (where src is not a script attribute, so the body runs), whatever comment or end-tag markup sits
between them, and it also refuses an HTML script placed after an svg, which neither live 404 page has.
The rule reads markup only: it cannot analyse what an allowlisted inline script does at run time (it
trusts the pinned bodies), nor what the same-origin script, stylesheet or icon a root-absolute URL loads
contains, nor where an https:// a href leads once followed (a navigation, not a load).

Apple touch icon: every live page (one that is not a generated template page, 404 pages included) must carry a <link rel="apple-touch-icon"> whose href names an existing file under that root,
taken as the exact path (no .html or index.html fallback); a missing link, or one naming a missing file, a
directory, an external or protocol-relative URL or a path escaping the root, is a finding, and so is a
relative href on a 404 page. Template pages are built under the site template's own rules and are not checked
here; a page under draft/ that the generator does not list is a live page.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_site.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import os
import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
import _template_pages  # noqa: E402  which pages the site template generates (GENSRC_OUTPUTS, not a folder name)

EN, EM = "–", "—"
# Site host from AIQT_SITE_HOST (default, and empty-value fallback, aiqt.ai; lowercased), so a
# non-aiqt.ai adopter can vendor the pack unpatched. Unchanged when the variable is unset or empty.
_SITE_HOST = (os.environ.get("AIQT_SITE_HOST", "aiqt.ai") or "aiqt.ai").lower()
SITE_HOSTS = {_SITE_HOST, "www." + _SITE_HOST}
# The own-host set of each coverage root: an absolute URL on the root's own host is internal to that
# root, and a link to the other site is external. site/ keeps SITE_HOSTS; opf/site/ is opfiles.ai.
ROOT_HOSTS = {"site": SITE_HOSTS, "opf/site": {"opfiles.ai", "www.opfiles.ai"}}

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
# The Apple touch icon link every live page carries. A generated template page (built under the site
# template's own rules) is not held to the live pages' head; _template_pages says which pages those are.
TOUCH_ICON_REL = "apple-touch-icon"


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
        self.touch_icons = []     # (href, line) of each <link rel="apple-touch-icon">
        self.metas = {}           # the content of the first <meta> per property or name

    def handle_starttag(self, tag, attrs):
        if self.suspend_tag is not None:
            if tag == self.suspend_tag:
                self.suspend_count += 1
            return
        d = dict(attrs)
        for key in ("href", "src"):
            if d.get(key):
                self.links.append((d[key], self.getpos()[0]))
        if tag == "link" and TOUCH_ICON_REL in (d.get("rel") or "").lower().split():
            self.touch_icons.append(((d.get("href") or "").strip(), self.getpos()[0]))
        if tag == "meta" and (d.get("property") or d.get("name")):
            self.metas.setdefault(d.get("property") or d.get("name"), (d.get("content") or "").strip())
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


def classify(v, hosts=None):
    # `hosts` is the scanned root's own-host set (ROOT_HOSTS); None keeps SITE_HOSTS.
    # urlsplit does not percent-decode. The PATH is unquoted so an encoded link (my%20page.html) resolves
    # against the real file "my page.html". The FRAGMENT is returned RAW: HTML fragment navigation matches
    # an id equal to the raw fragment FIRST, then the percent-decoded form, so the anchor check tries both
    # (see main()). Unquoting the fragment here unconditionally would drop the raw-match case.
    own = SITE_HOSTS if hosts is None else hosts
    parts = urlsplit(v)
    if parts.scheme:
        if parts.scheme.lower() in ("http", "https") and parts.netloc.lower() in own:
            return unquote(parts.path), parts.fragment
        return None
    if parts.netloc:
        return None
    return unquote(parts.path), parts.fragment


_LOGO_RE = re.compile(r'<a class="logo".*?</a>', re.S)
# A 404 page is served at any missing URL, however deep, so a relative home link there resolves below the
# root. On a page named NOT_FOUND_PAGE the logo must therefore open with the root-absolute home link where
# the canonical opens with the relative one; every other byte must still match.
NOT_FOUND_PAGE = "404.html"
_LOGO_RELATIVE_HOME = '<a class="logo" href="./"'
_LOGO_ROOT_HOME = '<a class="logo" href="/"'


def logo_findings(pages):
    """Findings for header-logo drift across pages. `pages` is a list of (rel, text). The logo block is
    duplicated per page (no page generator yet), so every page that carries one must carry the SAME bytes
    as the majority (canonical) block of the pages not named 404.html (of all pages when only 404 pages
    carry one); any other block is reported. The one exception: a 404 page carries the canonical block
    with its relative home link href="./" written as the root-absolute href="/", and the relative form is
    reported there. A page without a logo is not a finding."""
    found = []
    for rel, text in pages:
        match = _LOGO_RE.search(text)
        if match:
            found.append((rel, match.group(0), Path(rel).name == NOT_FOUND_PAGE))
    counts = {}
    for _rel, block, _not_found in [item for item in found if not item[2]] or found:
        counts[block] = counts.get(block, 0) + 1
    if not counts:
        return []
    canonical = max(counts, key=lambda block: counts[block])
    accepted_not_found = {canonical}
    if canonical.startswith(_LOGO_RELATIVE_HOME):
        accepted_not_found = {_LOGO_ROOT_HOME + canonical[len(_LOGO_RELATIVE_HOME):]}
    out = []
    for rel, block, not_found in found:
        if block in (accepted_not_found if not_found else {canonical}):
            continue
        out.append("{}: header logo block differs from the canonical logo (logos must be "
                   "byte-identical across pages)".format(rel))
    return out


def _touch_icon_file(href, page_dir, site_root):
    """The existing regular file under site_root that href names as an exact path, or None. An external
    or empty-path href, a directory, a path escaping the root and a missing file all give None."""
    classified = classify(href)
    if classified is None or classified[0] == "":
        return None
    path = classified[0]
    base = site_root if path.startswith("/") else page_dir
    resolved = (base / path.lstrip("/")).resolve()
    if _under(site_root, resolved) and resolved != site_root and resolved.is_file():
        return resolved
    return None


def touch_icon_findings(label, page_rel, icons, page_dir, site_root, template=False):
    """Findings for one page's Apple touch icon links. `label` names the page in a finding, `page_rel` is
    its path relative to its root, `icons` its (href, line) list. A generated template page (`template`
    true, from _template_pages) is not checked; any other page needs at least one link, and every
    link must name an existing file. A 404 page is served at any missing URL, however deep, so its links
    must be root-absolute: a relative one resolves below the root there."""
    parts = Path(page_rel).parts
    if template:
        return []
    if not icons:
        return ['{}: missing <link rel="{}"> (every live page links the touch icon)'.format(
            label, TOUCH_ICON_REL)]
    out = []
    for href, line in icons:
        classified = classify(href)
        split = urlsplit(href)
        if split.netloc and not split.scheme:
            out.append("{}:{}: {} is a protocol-relative URL (//host/...), not a path to a file under the root; "
                       "it must be a root-absolute path -> {!r}".format(label, line, TOUCH_ICON_REL, href))
        elif parts[-1] == NOT_FOUND_PAGE and classified is not None and classified[0] != "" \
                and not classified[0].startswith("/"):
            out.append("{}:{}: {} on a 404 page is relative; a 404 page is served at any path, so it must "
                       "be root-absolute -> {!r}".format(label, line, TOUCH_ICON_REL, href))
        elif _touch_icon_file(href, page_dir, site_root) is None:
            out.append("{}:{}: {} names a file that does not exist under the root -> {!r}".format(
                label, line, TOUCH_ICON_REL, href))
    return out


# Shell-class rule: every served page carries the site template shell's root element, so no page can stay
# on a retired shell. The rule reads every .html of a coverage root outside the folders the template
# declaration stages drafts in (_template_pages.draft_folders); a generated template page is exempt (the
# generator writes the element on every page it builds, under its own refusals). A page that cannot carry
# the template shell yet is recorded in SHELL_CLASS_EXCLUSIONS with the reason it stays off it; an
# exclusion whose reason is empty is itself a finding, so the record can never decay into a silent skip.
SHELL_CLASS_KEYS = {"site": "aiqt", "opf/site": "opfiles"}
SHELL_CLASS_EXCLUSIONS = {}  # {"<root>/<page path>": "why the page cannot carry the template shell yet"}


def shell_class_findings(subdir, page_rel, label, text, template, draft_folders):
    """Findings for one page against the shell-class rule. `page_rel` is the page's POSIX path under its
    coverage root `subdir`, `label` names it in a finding, `template` says whether it is a generated
    template page, and `draft_folders` holds the root's staged-draft folders."""
    key = SHELL_CLASS_KEYS.get(subdir)
    if key is None or template or any(page_rel.startswith(folder) for folder in draft_folders):
        return []
    record = "{}/{}".format(subdir, page_rel)
    if record in SHELL_CLASS_EXCLUSIONS:
        if not SHELL_CLASS_EXCLUSIONS[record].strip():
            return ["{}: shell-class exclusion records no reason".format(label)]
        return []
    marker = '<html lang="en" class="site-{}">'.format(key)
    if marker not in text:
        return ["{}: not on the template shell (missing {}) and not a recorded exclusion".format(
            label, marker)]
    return []


# The generated pages composed on the template shell outside the template declaration (tools/gen_site.py
# and tools/gen_enforcement_register.py write them, through the same compose_shell every template page is
# built from), held to the link-preview card exactly like a generated template page.
COMPOSED_PAGES = {"site": frozenset(("development.html", "evidence.html", "tech-details.html",
                                     "enforcement.html"))}


# The link-preview card metas every generated template page carries (tools/gen_draft_site.py), and the
# smallest card image the large link previews take.
CARD_METAS = ("og:type", "og:title", "og:description", "og:url", "og:site_name", "og:image", "og:image:width",
              "og:image:height", "og:image:alt", "twitter:card", "twitter:image")
CARD_MIN_SIZE = (1200, 627)
CARD_MAX_READ = 8 << 20


def image_size(data):
    """(width, height) of PNG or baseline/progressive JPEG bytes, or None for any other or a malformed file."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24 and data[12:16] == b"IHDR":
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if data[:2] != b"\xff\xd8":
        return None
    at = 2
    while at + 4 <= len(data):
        if data[at] != 0xFF:
            return None
        marker = data[at + 1]
        if marker == 0xFF:
            at += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            at += 2
            continue
        length = int.from_bytes(data[at + 2:at + 4], "big")
        if length < 2:
            return None
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if at + 9 > len(data) or length < 7:
                return None
            return int.from_bytes(data[at + 7:at + 9], "big"), int.from_bytes(data[at + 5:at + 7], "big")
        at += 2 + length
    return None


def card_findings(label, metas, site_root, hosts):
    """Findings for one generated page's link-preview card metas: each of CARD_METAS present and non-empty,
    twitter:card summary_large_image, og:url an https URL on the root's own host, and og:image (and
    twitter:image, the same URL) an https URL on that host naming an existing PNG or JPEG file inside the
    root, at least CARD_MIN_SIZE, whose size og:image:width and og:image:height state."""
    out = ["{}: missing or empty <meta> {}".format(label, key) for key in CARD_METAS if not metas.get(key)]
    if out:
        return out
    if metas["twitter:card"] != "summary_large_image":
        out.append("{}: twitter:card is {!r}, not 'summary_large_image'".format(label, metas["twitter:card"]))
    if metas["twitter:image"] != metas["og:image"]:
        out.append("{}: twitter:image differs from og:image".format(label))
    url = urlsplit(metas["og:url"])
    if url.scheme != "https" or url.hostname not in hosts:
        out.append("{}: og:url is not an https URL on this site -> {!r}".format(label, metas["og:url"]))
    image = urlsplit(metas["og:image"])
    if image.scheme != "https" or image.hostname not in hosts or image.query or image.fragment:
        out.append("{}: og:image is not an https URL on this site -> {!r}".format(label, metas["og:image"]))
        return out
    path = unquote(image.path).lstrip("/")
    resolved = (site_root / path).resolve()
    if not path or any(part in ("", ".", "..") for part in path.split("/")) \
            or not _under(site_root, resolved) or not resolved.is_file():
        out.append("{}: og:image names no file inside the site -> {!r}".format(label, metas["og:image"]))
        return out
    try:
        with open(resolved, "rb") as handle:
            size = image_size(handle.read(CARD_MAX_READ))
    except OSError as exc:
        out.append("{}: og:image cannot be read ({})".format(label, exc))
        return out
    if size is None:
        out.append("{}: og:image is not a PNG or JPEG file -> {!r}".format(label, metas["og:image"]))
    elif size[0] < CARD_MIN_SIZE[0] or size[1] < CARD_MIN_SIZE[1]:
        out.append("{}: og:image is {}x{}, below {}x{}".format(label, size[0], size[1], *CARD_MIN_SIZE))
    elif (metas["og:image:width"], metas["og:image:height"]) != (str(size[0]), str(size[1])):
        out.append("{}: og:image:width and og:image:height state {}x{}, the file is {}x{}".format(
            label, metas["og:image:width"], metas["og:image:height"], size[0], size[1]))
    return out


# The URL-bearing attributes a 404 page is held to (srcset is split into its candidate URLs), and the ASCII
# whitespace that separates attributes and srcset candidates in HTML.
NOT_FOUND_URL_ATTRS = {"href", "src", "srcset", "poster", "data", "action", "xlink:href"}
# The (element, attribute) pairs the live 404 pages load a resource through: these must be root-absolute (a
# fragment-only or query-only value resolves against the request URL, https:// names another host). The one
# pair the live 404 pages write https:// on is a href, so no other pair accepts it.
NOT_FOUND_LOADING_PAIRS = frozenset((("link", "href"), ("script", "src")))
NOT_FOUND_HTTPS_PAIRS = frozenset((("a", "href"),))
_HTML_SPACE = "\t\n\f\r "
_RAW_TAG_RE = re.compile(r"<[A-Za-z]")
# After a leading "/", a browser reads a backslash as a second slash and drops tabs and newlines, so any of
# these there can make a protocol-relative //host URL.
_SECOND_SLASH = ("/", "\\", "\t", "\n", "\r")

# The 404-page structural allowlist, fail closed: every element and attribute name raw_tags finds on a 404 page
# must be listed here. Derived by running raw_tags over the two live 404 pages (site/404.html and
# opf/site/404.html) and taking exactly the names, meta attributes, onclick values, SVG presentation values
# and no-src script bodies (sha256 of the UTF-8 text between the start tag and its end tag) they use; a name
# or value is added only when a live 404 page starts using it.
NOT_FOUND_ELEMENTS = frozenset((
    "a", "body", "button", "div", "footer", "h1", "h4", "head", "header", "html", "link", "main", "meta",
    "nav", "p", "script", "section", "span", "svg", "text", "title",
))
NOT_FOUND_ATTRIBUTES = frozenset((
    "aria-controls", "aria-expanded", "aria-hidden", "aria-label", "charset", "class", "content", "fill",
    "font-family", "font-size", "font-weight", "height", "href", "id", "lang", "name", "onclick", "rel", "src",
    "target", "text-anchor", "type", "viewbox", "width", "x", "y",
))
NOT_FOUND_META_ATTRIBUTES = frozenset(("charset", "content", "name"))
NOT_FOUND_ONCLICK = frozenset(("toggleNav()", "toggleTheme()"))
NOT_FOUND_PRESENTATION = {
    "fill": frozenset(("var(--accent)", "var(--accent-2)")),
    "font-family": frozenset(("system-ui,-apple-system,Segoe UI,sans-serif",)),
    "font-size": frozenset(("40", "42")),
    "font-weight": frozenset(("800",)),
    "text-anchor": frozenset(("middle",)),
}
NOT_FOUND_INLINE_SCRIPT_SHA256 = frozenset((
    "26b7f1c5bd8e7e7ac8723806089d3d541b9957a89cecd01a7a2bf2d96e622aae",
))
# The draft 404 pages (the 404.html directly under each root's top-level draft/, served at any missing /draft/
# URL) are
# built from the site template, not from the live pages, so they are held to their own structural allowlist,
# fail closed in the same way. Derived by running raw_tags over the two draft 404 pages (site/draft/404.html
# and opf/site/draft/404.html, rendered by tools/gen_draft_site.py) and taking exactly the names, meta
# attributes, onclick values, SVG presentation values and no-src script bodies they use (no onclick and no
# inline script); a name or value is added only when a draft 404 page starts using it.
NOT_FOUND_DRAFT_ELEMENTS = frozenset((
    "a", "body", "button", "circle", "div", "footer", "h1", "head", "header", "html", "img", "li", "link",
    "main", "meta", "nav", "p", "path", "script", "span", "svg", "title", "ul",
))
NOT_FOUND_DRAFT_ATTRIBUTES = frozenset((
    "alt", "aria-current", "aria-hidden", "aria-label", "aria-live", "aria-pressed", "charset", "class",
    "content", "cx", "cy", "d", "data-cfasync", "data-scheme-toggle", "fill", "focusable", "height", "hidden",
    "href", "id", "lang", "name", "property", "r", "rel", "role", "src", "stroke", "stroke-linecap",
    "stroke-linejoin", "stroke-width", "target", "type", "viewbox", "width",
))
# "property" is the og:description meta the generator writes from each page's description.
NOT_FOUND_DRAFT_META_ATTRIBUTES = frozenset(("charset", "content", "name", "property"))
NOT_FOUND_DRAFT_ONCLICK = frozenset()
NOT_FOUND_DRAFT_PRESENTATION = {
    "fill": frozenset(("none",)),
    "font-family": frozenset(),
    "font-size": frozenset(),
    "font-weight": frozenset(),
    "text-anchor": frozenset(),
}
NOT_FOUND_DRAFT_INLINE_SCRIPT_SHA256 = frozenset()
# A CSS url() loads relative to the page, so it is refused in any value or script or style text on a 404 page.
_URL_FUNCTION = "url("
_RAW_TEXT_END = {element: re.compile(r"</" + element + r"[\t\n\f\r />]", re.I | re.A)
                 for element in ("script", "style")}


def raw_tags(text):
    """Every start tag in the raw markup as (element, line, attributes, end): element and attribute names
    lowercased, each attribute a (name, value, line) with the value as written (character references and
    percent-escapes left alone; None for a name written without "="), duplicates included, and `end` the
    offset just past the tag's ">". A tag scan starts at every "<" followed by a letter, inside a comment,
    script, svg, math or attribute value too; markup a browser does not read as a tag can only add a finding.
    Markup a browser builds from text that holds no literal "<" (an entity-escaped srcdoc, a script that
    writes markup at run time) is not seen here. An unclosed quote runs to the end."""
    out = []
    n = len(text)
    for match in _RAW_TAG_RE.finditer(text):
        i = match.end()
        while i < n and text[i] not in _HTML_SPACE + "/>":
            i += 1
        element = text[match.start() + 1:i].lower()
        attributes = []
        while i < n and text[i] != ">":
            if text[i] in _HTML_SPACE + "/":
                i += 1
                continue
            start = i
            i += 1  # a leading "=" belongs to the name
            while i < n and text[i] not in _HTML_SPACE + "/>=":
                i += 1
            name = text[start:i].lower()
            line = text.count("\n", 0, start) + 1
            j = i
            while j < n and text[j] in _HTML_SPACE:
                j += 1
            if j >= n or text[j] != "=":
                attributes.append((name, None, line))
                continue
            j += 1
            while j < n and text[j] in _HTML_SPACE:
                j += 1
            if j < n and text[j] in "\"'":
                end = text.find(text[j], j + 1)
                end = n if end == -1 else end
                value, i = text[j + 1:end], end + 1
            else:
                end = j
                while end < n and text[end] not in _HTML_SPACE + ">":
                    end += 1
                value, i = text[j:end], end
            attributes.append((name, value, line))
        out.append((element, text.count("\n", 0, match.start()) + 1, attributes, min(i + 1, n)))
    return out


def raw_url_attributes(text):
    """Every (element, name, value, line) of a URL-bearing attribute that raw_tags finds, the value as
    written, duplicates included. It reads only the names in NOT_FOUND_URL_ATTRS; not_found_structure_findings
    holds every other name on a 404 page to an allowlist."""
    return [(element, name, value, line) for element, _line, attributes, _end in raw_tags(text)
            for name, value, line in attributes if name in NOT_FOUND_URL_ATTRS and value is not None]


def srcset_urls(value):
    """The candidate URLs of a srcset value, split as HTML does: skip spaces and commas, take the run of
    non-space characters (trailing commas dropped, which also ends the candidate), then skip its descriptors
    up to the next comma outside parentheses."""
    urls = []
    i, n = 0, len(value)
    while i < n:
        while i < n and (value[i] in _HTML_SPACE or value[i] == ","):
            i += 1
        start = i
        while i < n and value[i] not in _HTML_SPACE:
            i += 1
        if start == i:
            break
        url = value[start:i]
        urls.append(url.rstrip(","))
        if url.endswith(","):
            continue
        in_parens = False
        while i < n and (in_parens or value[i] != ","):
            if value[i] == "(":
                in_parens = True
            elif value[i] == ")":
                in_parens = False
            i += 1
    return urls


def not_found_reference_ok(element, name, value):
    """Whether a URL value of attribute `name` on `element`, taken as written, is safe on a 404 page (served
    at any path): exactly one leading "/" followed by a safe character or nothing; also fragment-only or
    query-only unless the pair loads a resource (NOT_FOUND_LOADING_PAIRS); also https:// on a pair in
    NOT_FOUND_HTTPS_PAIRS. Nothing is percent-decoded, so "%2F..." is not a slash."""
    if (element, name) not in NOT_FOUND_LOADING_PAIRS and value.startswith(("#", "?")):
        return True
    if (element, name) in NOT_FOUND_HTTPS_PAIRS and value.startswith("https://"):
        return True
    return value.startswith("/") and value[1:2] not in _SECOND_SLASH


def not_found_reference_findings(label, text):
    """Findings for the raw markup `text` of a 404 page named `label`: each URL-bearing attribute occurrence
    (each srcset candidate) that fails not_found_reference_ok as written or with its character references
    decoded, as a browser decodes them before reading the URL."""
    out = []
    for element, name, raw, line in raw_url_attributes(text):
        decoded = unescape(raw)
        if name == "srcset":
            candidates = dict.fromkeys(srcset_urls(raw) + srcset_urls(decoded))
            bad = [url for url in candidates if not not_found_reference_ok(element, name, url)]
        else:
            bad = [] if not_found_reference_ok(element, name, raw) and not_found_reference_ok(
                element, name, decoded) else [raw]
        for value in bad:
            if (element, name) in NOT_FOUND_LOADING_PAIRS:
                rule = ("<{}> {} on a 404 page must be root-absolute (one leading /) only; it loads a resource, "
                        "so a fragment-only or query-only value (resolved against the request URL) and another "
                        "host are refused").format(element, name)
            elif (element, name) in NOT_FOUND_HTTPS_PAIRS:
                rule = ("{} on a 404 page must be root-absolute (one leading /), fragment-only, query-only or "
                        "https://; a 404 page is served at any path").format(name)
            else:
                rule = ("{} on a 404 page must be root-absolute (one leading /), fragment-only or query-only (https:// "
                        "only on a href); a 404 page is served at any path").format(name)
            out.append("{}:{}: {} -> {!r}".format(label, line, rule, value))
    return out


def _raw_text_body(text, element, end):
    """The raw text of a script or style element whose start tag ends at `end`, up to its end tag (to the end
    of the text when there is none)."""
    match = _RAW_TEXT_END[element].search(text, end)
    return text[end:] if match is None else text[end:match.start()]


def not_found_structure_findings(label, text, draft=False):
    """Findings for the raw markup `text` of a 404 page named `label` against the structural allowlist (the
    NOT_FOUND_DRAFT_* sets in place of the NOT_FOUND_* ones when `draft` is true, for a draft 404 page): an
    element or attribute name raw_tags finds that is not in NOT_FOUND_ELEMENTS or NOT_FOUND_ATTRIBUTES, a meta
    attribute outside NOT_FOUND_META_ATTRIBUTES, an onclick or SVG presentation value the live 404 pages do
    not use, any value or script or style text that contains "url(" (any case, as written or with its
    character references decoded), a script without src whose body's sha256 is not pinned in
    NOT_FOUND_INLINE_SCRIPT_SHA256, a script with src whose body is not empty, and any script start tag at or
    after the first svg start tag (inside svg, src is not a script attribute and the body runs; taking every
    later script, not only those before a matching end tag, means no comment or stray end tag can hide one).
    Residual: the rule reads markup only. It cannot analyse what an allowlisted inline script does at run time
    (it trusts the pinned script bodies), nor what the same-origin script, stylesheet or icon a root-absolute
    URL loads contains."""
    if draft:
        kind, elements, attribute_names, meta_names = (
            "draft", NOT_FOUND_DRAFT_ELEMENTS, NOT_FOUND_DRAFT_ATTRIBUTES, NOT_FOUND_DRAFT_META_ATTRIBUTES)
        onclicks, presentation, inline_scripts = (
            NOT_FOUND_DRAFT_ONCLICK, NOT_FOUND_DRAFT_PRESENTATION, NOT_FOUND_DRAFT_INLINE_SCRIPT_SHA256)
    else:
        kind, elements, attribute_names, meta_names = (
            "live", NOT_FOUND_ELEMENTS, NOT_FOUND_ATTRIBUTES, NOT_FOUND_META_ATTRIBUTES)
        onclicks, presentation, inline_scripts = (
            NOT_FOUND_ONCLICK, NOT_FOUND_PRESENTATION, NOT_FOUND_INLINE_SCRIPT_SHA256)
    out = []
    svg_seen = False
    for element, line, attributes, end in raw_tags(text):
        if element == "script" and svg_seen:
            out.append("{}:{}: <script> at or after an <svg> start tag on a 404 page is refused (inside svg, src "
                       "is not a script attribute and the body runs)".format(label, line))
        svg_seen = svg_seen or element == "svg"
        if element not in elements:
            out.append("{}:{}: element <{}> is not in the {}404-page element allowlist".format(
                label, line, element, "draft " if draft else ""))
        for name, value, attr_line in attributes:
            if name not in attribute_names:
                out.append("{}:{}: attribute {} on <{}> is not in the {}404-page attribute allowlist".format(
                    label, attr_line, name, element, "draft " if draft else ""))
            if element == "meta" and name not in meta_names:
                out.append("{}:{}: attribute {} on <meta> is not one the {} 404 pages use on meta".format(
                    label, attr_line, name, kind))
            value = "" if value is None else value
            if _URL_FUNCTION in value.lower() or _URL_FUNCTION in unescape(value).lower():
                out.append("{}:{}: {} value on a 404 page contains url(, which loads relative to the page "
                           "-> {!r}".format(label, attr_line, name, value))
            if name == "onclick" and value not in onclicks:
                out.append("{}:{}: onclick value on a 404 page is not one the {} 404 pages use -> {!r}".format(
                    label, attr_line, kind, value))
            if name in presentation and value not in presentation[name]:
                out.append("{}:{}: {} value on a 404 page is not one the {} 404 pages use -> {!r}".format(
                    label, attr_line, name, kind, value))
        if element in _RAW_TEXT_END:
            body = _raw_text_body(text, element, end)
            if _URL_FUNCTION in body.lower() or _URL_FUNCTION in unescape(body).lower():
                out.append("{}:{}: <{}> text on a 404 page contains url(, which loads relative to the page".format(
                    label, line, element))
            has_src = any(name == "src" for name, _value, _line in attributes)
            if element == "script" and has_src and body:
                out.append("{}:{}: <script> with src on a 404 page must have an empty body (the body runs "
                           "wherever src is not read)".format(label, line))
            if element == "script" and not has_src:
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                if digest not in inline_scripts:
                    out.append("{}:{}: inline <script> on a 404 page is not a pinned script body (sha256 {})".format(
                        label, line, digest))
    return out


def _self_test():
    _use_staged_declaration(True)
    good = '<a class="logo" href="/">L</a>'
    bad = '<a class="logo" href="/">L&trade;</a>'
    failures = []
    cases = [
        ("all identical -> no finding", [("a.html", good), ("b.html", good), ("c.html", good)], 0),
        ("one outlier -> one finding", [("a.html", good), ("b.html", good), ("c.html", bad)], 1),
        ("page without a logo is not a finding", [("a.html", good), ("b.html", "<p>no logo</p>")], 0),
        ("empty set -> no finding", [], 0),
    ]
    for label, pages, want in cases:
        got = len(logo_findings(pages))
        if got != want:
            failures.append("{}: expected {} finding(s), got {}".format(label, want, got))
    # the outlier must be the one named (canonical is the majority)
    named = logo_findings([("keep.html", good), ("keep2.html", good), ("drift.html", bad)])
    if not (len(named) == 1 and named[0].startswith("drift.html")):
        failures.append("outlier naming: expected drift.html named, got {}".format(named))
    # A 404 page must open its logo with the root-absolute home link where the canonical is relative; any
    # other page may not, and a 404 page that differs in any other byte is still drift.
    rel_logo = '<a class="logo" href="./">L</a>'
    root_logo = '<a class="logo" href="/">L</a>'
    not_found_cases = [
        ("404 page with root-absolute home on a relative site", [("a.html", rel_logo), ("404.html", root_logo)],
         []),
        ("nested 404 page with root-absolute home", [("a.html", rel_logo), ("b/404.html", root_logo)], []),
        ("404 page with the canonical relative home", [("a.html", rel_logo), ("404.html", rel_logo)],
         ["404.html"]),
        ("nested 404 page with the canonical relative home", [("a.html", rel_logo), ("b/404.html", rel_logo)],
         ["b/404.html"]),
        ("only 404 pages, relative home", [("404.html", rel_logo), ("b/404.html", rel_logo)],
         ["404.html", "b/404.html"]),
        ("404 page with root-absolute home on a root-absolute site",
         [("a.html", root_logo), ("404.html", root_logo)], []),
        ("non-404 page with root-absolute home on a relative site",
         [("a.html", rel_logo), ("b.html", rel_logo), ("c.html", root_logo)], ["c.html"]),
        ("404 page with root-absolute home and other drift",
         [("a.html", rel_logo), ("404.html", '<a class="logo" href="/">L&trade;</a>')], ["404.html"]),
        ("404 majority does not set the canonical",
         [("a.html", rel_logo), ("404.html", bad), ("x/404.html", bad)], ["404.html", "x/404.html"]),
    ]
    for label, pages, want in not_found_cases:
        got = [line.split(":")[0] for line in logo_findings(pages)]
        if got != want:
            failures.append("{}: expected {} named, got {}".format(label, want, got))
    # AIQT_SITE_HOST derives SITE_HOSTS at import, so probe it in a child process under each env value.
    # Unset/empty -> default aiqt.ai (unchanged from today); a set host -> that host and its www.; the
    # env host is lowercased; a no-dot host derives its own exact pair.
    import subprocess
    tools_dir = str(Path(__file__).resolve().parent)
    probe = ("import sys; sys.path.insert(0, {!r}); import check_site; "
             "print(' '.join(sorted(check_site.SITE_HOSTS)))".format(tools_dir))

    def hosts_under(env_val):
        env = dict(os.environ)
        if env_val is None:
            env.pop("AIQT_SITE_HOST", None)
        else:
            env["AIQT_SITE_HOST"] = env_val
        out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True,
                             env=env, check=True)
        return set(out.stdout.split())

    host_cases = [
        ("unset -> default aiqt.ai", None, {"aiqt.ai", "www.aiqt.ai"}),
        ("empty -> fallback aiqt.ai", "", {"aiqt.ai", "www.aiqt.ai"}),
        ("set host -> host + www.", "example.test", {"example.test", "www.example.test"}),
        ("host lowercased", "EXAMPLE.TEST", {"example.test", "www.example.test"}),
        ("no-dot host derives its own pair", "localhost", {"localhost", "www.localhost"}),
    ]
    for label, env_val, want in host_cases:
        got = hosts_under(env_val)
        if got != want:
            failures.append("SITE_HOSTS {}: expected {} got {}".format(label, sorted(want), sorted(got)))

    # Coverage-root integration: _scan_root treats opf/site as its own link-containment boundary, so a
    # nested draft page's relative link resolves within opf/site (clean) while a dangling one is a finding;
    # main() skips absent roots (both absent -> clean pass). Output captured so it does not leak here.
    import tempfile
    import contextlib
    import io

    def quiet(fn, *a):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn(*a)
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        draft = r / "opf" / "site" / "draft"
        draft.mkdir(parents=True)
        (r / "opf" / "site" / "touch.png").write_bytes(b"png")
        (r / "opf" / "site" / "index.html").write_text(
            '<!doctype html><html lang="en" class="site-opfiles"><title>t</title>'
            '<link rel="apple-touch-icon" href="/touch.png"><nav></nav>',
            encoding="utf-8")
        # These pages are not generator targets, so they are live pages and carry the touch icon.
        icon = '<link rel="apple-touch-icon" href="/touch.png">'
        (draft / "disclosure.html").write_text("<!doctype html><title>t</title>" + icon + "<p>d</p>",
                                               encoding="utf-8")
        (draft / "manifest.html").write_text(
            '<!doctype html><title>t</title>' + icon + '<nav><a href="./disclosure">D</a></nav>', encoding="utf-8")
        if quiet(_scan_root, r, "opf/site") != 0:
            failures.append("coverage: a clean opf/site tree with relative links did not pass")
        (draft / "broken.html").write_text(
            '<!doctype html><title>t</title><a href="./nope">x</a>', encoding="utf-8")
        if quiet(_scan_root, r, "opf/site") != 1:
            failures.append("coverage: a dangling relative link under opf/site/draft was not caught")

    # The opfiles.ai draft under opf/site/draft/: nested pages, an asset, a fragment and an absolute
    # opfiles.ai URL resolve inside opf/site; a missing target, a missing fragment and an aiqt.ai URL
    # read as internal there are findings, while an aiqt.ai URL is external on opf/site.
    host_findings = []
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        draft = r / "opf" / "site" / "draft"
        (draft / "model").mkdir(parents=True)
        (draft / "assets" / "baseline").mkdir(parents=True)
        (draft / "assets" / "baseline" / "baseline.css").write_text("p{}", encoding="utf-8")
        (draft / "model" / "index.html").write_text(
            '<!doctype html><title>t</title>' + _card_head("opfiles.ai") + '<h2 id="box">B</h2>', encoding="utf-8")
        (r / "opf" / "site" / "card.png").write_bytes(_png(1200, 627))
        (draft / "index.html").write_text(
            '<!doctype html><title>t</title>' + _card_head("opfiles.ai") +
            '<link rel="stylesheet" href="/draft/assets/baseline/baseline.css">'
            '<a href="/draft/model/">M</a><a href="/draft/model/#box">B</a>'
            '<a href="https://opfiles.ai/draft/model/">O</a><a href="https://aiqt.ai/no-such-page">A</a>',
            encoding="utf-8")
        draft_cases = (
            ("draft/nested-assets-fragments-and-own-host-pass", None, 0),
            ("draft/missing-target", '<a href="/draft/core-files/">C</a>', 1),
            ("draft/missing-fragment", '<a href="/draft/model/#absent">X</a>', 1),
            ("draft/missing-asset", '<img src="/draft/assets/baseline/logos/absent.svg" alt="">', 1),
            ("draft/own-host-missing-target", '<a href="https://www.opfiles.ai/draft/absent/">X</a>', 1),
        )
        for label, extra, want in draft_cases:
            probe_page = draft / "probe.html"
            if extra is None:
                if probe_page.exists():
                    probe_page.unlink()
            else:
                probe_page.write_text("<!doctype html><title>t</title>" + extra, encoding="utf-8")
            got = quiet(_scan_root, r, "opf/site")
            if got != want:
                host_findings.append("{}: expected exit {}, got {}".format(label, want, got))
    for label, value, hosts, want in (
            ("hosts/opfiles-internal-on-opf-site", "https://opfiles.ai/draft/", ROOT_HOSTS["opf/site"],
             ("/draft/", "")),
            ("hosts/aiqt-external-on-opf-site", "https://aiqt.ai/draft/", ROOT_HOSTS["opf/site"], None),
            ("hosts/opfiles-external-on-site", "https://www.opfiles.ai/draft/", ROOT_HOSTS["site"], None),
            ("hosts/aiqt-internal-on-site", "https://www.aiqt.ai/x#y", ROOT_HOSTS["site"], ("/x", "y")),
            ("hosts/default-is-site-hosts", "https://aiqt.ai/x", None, ("/x", ""))):
        got = classify(value, hosts)
        if got != want:
            host_findings.append("{}: expected {!r}, got {!r}".format(label, want, got))
    if ROOT_HOSTS["site"] is not SITE_HOSTS or set(ROOT_HOSTS) != set(COVERAGE_ROOTS):
        host_findings.append("hosts/every-root-has-its-host-set: {!r}".format(sorted(ROOT_HOSTS)))
    failures.extend(host_findings)

    # The draft 404 structural allowlist: a name only the draft 404 pages use passes on a draft 404 page and
    # stays a finding on a live one, a name neither uses is a finding on both, and a live-only presentation
    # value is a finding on a draft 404 page. Through _scan_root, only a 404.html the generator lists takes
    # the draft list (a nested draft/ directory, and a 404.html in a subdirectory of the top-level draft/,
    # keep the live list) under both site/ and opf/site/, and the reference rule still holds a draft 404
    # page to root-absolute URLs.
    draft_404_cases = (
        ("draft-404/draft-name-passes-on-draft", '<img src="/t.png" alt="">', True, ""),
        ("draft-404/draft-name-fails-on-live", '<img src="/t.png" alt="">', False,
         "element <img> is not in the 404-page element allowlist"),
        ("draft-404/unlisted-element-fails-on-draft", "<style>p{}</style>", True,
         "element <style> is not in the draft 404-page element allowlist"),
        ("draft-404/unlisted-attribute-fails-on-draft", '<a href="/" ping="/p">H</a>', True,
         "attribute ping on <a> is not in the draft 404-page attribute allowlist"),
        ("draft-404/draft-fill-passes-on-draft", '<svg fill="none"></svg>', True, ""),
        ("draft-404/live-fill-fails-on-draft", '<svg fill="var(--accent)"></svg>', True,
         "fill value on a 404 page is not one the draft 404 pages use"),
        ("draft-404/onclick-fails-on-draft", '<button type="button" onclick="toggleNav()">B</button>', True,
         "onclick value on a 404 page is not one the draft 404 pages use"),
        ("draft-404/inline-script-fails-on-draft", "<script>x()</script>", True,
         "inline <script> on a 404 page is not a pinned script body"),
    )
    for label, markup, is_draft, message in draft_404_cases:
        got = not_found_structure_findings("404.html", markup, draft=is_draft)
        if (message == "" and got) or (message and not any(message in f for f in got)):
            failures.append("{}: expected {!r}, got {!r}".format(label, message or "no finding", got))
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        (r / "site" / "draft").mkdir(parents=True)
        (r / "site" / "sub" / "draft").mkdir(parents=True)
        (r / "site" / "draft" / "x" / "y").mkdir(parents=True)
        (r / "opf" / "site" / "sub" / "draft").mkdir(parents=True)
        (r / "opf" / "site" / "draft" / "x" / "y").mkdir(parents=True)
        card_host = {"site": "aiqt.ai", "opf/site": "opfiles.ai"}
        for subdir in COVERAGE_ROOTS:
            (r / subdir / "t.png").write_bytes(b"png")
            (r / subdir / "card.png").write_bytes(_png(1200, 627))
            (r / subdir / "index.html").write_text(
                '<!doctype html><html lang="en" class="site-{}"><title>t</title>'
                '<link rel="apple-touch-icon" href="/t.png">'.format(SHELL_CLASS_KEYS[subdir])
                + _card_head(card_host[subdir]), encoding="utf-8")
        draft_page = '<!doctype html><title>t</title>' + _card_head("CARD-HOST") + '<img src="/t.png" alt="">'
        scan_cases = (
            ("draft-404/scan-top-level-draft-passes", "site", "draft/404.html", draft_page, 0, ""),
            ("draft-404/scan-nested-draft-keeps-live-list", "site", "sub/draft/404.html", draft_page, 1,
             "element <img> is not in the 404-page element allowlist"),
            ("draft-404/scan-draft-subdir-keeps-live-list", "site", "draft/x/404.html", draft_page, 1,
             "element <img> is not in the 404-page element allowlist"),
            ("draft-404/scan-draft-deep-subdir-keeps-live-list", "site", "draft/x/y/404.html", draft_page, 1,
             "element <img> is not in the 404-page element allowlist"),
            ("draft-404/scan-draft-relative-src-fails", "site", "draft/404.html",
             '<!doctype html><title>t</title><img src="t.png" alt="">', 1,
             "src on a 404 page must be root-absolute"),
            ("draft-404/opf-scan-top-level-draft-passes", "opf/site", "draft/404.html", draft_page, 0, ""),
            ("draft-404/opf-scan-nested-draft-keeps-live-list", "opf/site", "sub/draft/404.html", draft_page, 1,
             "element <img> is not in the 404-page element allowlist"),
            ("draft-404/opf-scan-draft-subdir-keeps-live-list", "opf/site", "draft/x/404.html", draft_page, 1,
             "element <img> is not in the 404-page element allowlist"),
            ("draft-404/opf-scan-draft-deep-subdir-keeps-live-list", "opf/site", "draft/x/y/404.html",
             draft_page, 1, "element <img> is not in the 404-page element allowlist"),
            ("draft-404/opf-scan-draft-relative-src-fails", "opf/site", "draft/404.html",
             '<!doctype html><title>t</title><img src="t.png" alt="">', 1,
             "src on a 404 page must be root-absolute"),
        )
        # The same rule once the pages are promoted (the generator's own build at base "/"): the root's 404.html
        # takes the draft list and a 404.html left under draft/ keeps the live list, on both roots.
        promoted = _promoted_targets()
        promoted_scan_cases = (
            ("draft-404/promoted-root-404-passes", "site", "404.html", draft_page, 0, ""),
            ("draft-404/promoted-draft-404-keeps-live-list", "site", "draft/404.html", draft_page, 1,
             "element <img> is not in the 404-page element allowlist"),
            ("draft-404/opf-promoted-root-404-passes", "opf/site", "404.html", draft_page, 0, ""),
            ("draft-404/opf-promoted-draft-404-keeps-live-list", "opf/site", "draft/404.html", draft_page, 1,
             "element <img> is not in the 404-page element allowlist"),
            ("draft-404/promoted-root-relative-src-fails", "site", "404.html",
             '<!doctype html><title>t</title><img src="t.png" alt="">', 1,
             "src on a 404 page must be root-absolute"),
        )
        staged_scan_cases = (
            ("draft-404/staged-root-404-keeps-live-list", "site", "404.html", draft_page, 1,
             "element <img> is not in the 404-page element allowlist"),
            ("draft-404/opf-staged-root-404-keeps-live-list", "opf/site", "404.html", draft_page, 1,
             "element <img> is not in the 404-page element allowlist"),
        )
        scan_cases = scan_cases + staged_scan_cases
        for label, subdir, page_rel, markup, want, message, targets in (
                [case + (None,) for case in scan_cases] + [case + (promoted,) for case in promoted_scan_cases]):
            (r / subdir / page_rel).write_text(markup.replace("CARD-HOST", card_host[subdir]), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                got = _scan_root(r, subdir, targets)
            (r / subdir / page_rel).unlink()
            if got != want or message not in out.getvalue():
                failures.append("{}: expected exit {} and {!r}, got {} {!r}".format(
                    label, want, message, got, out.getvalue()))
    with tempfile.TemporaryDirectory() as d2:
        if quiet(run, Path(d2)) != 0:
            failures.append("coverage: neither root present did not pass clean")

    _touch_icon_self_test(quiet)
    _card_self_test(quiet)
    _not_found_links_self_test()
    _shell_class_self_test()
    _declaration_self_test()
    _use_staged_declaration(False)
    # The real declaration is the promoted one: the root's 404.html is a template page, a nested draft/ page
    # is not, and turning the staged default back on flips both (fails on purpose).
    real = _template_pages.template_targets()
    check("declaration/real-is-promoted", "site/404.html" in real and "opf/site/404.html" in real
          and "site/draft/404.html" not in real and "site/draft/index.html" in real, sorted(real))
    _use_staged_declaration(True)
    flipped = _template_pages.template_targets()
    _use_staged_declaration(False)
    check("declaration/staged-flip-differs", "site/404.html" not in flipped and "site/draft/404.html" in flipped,
          sorted(flipped))
    failures.extend(FAILURES)

    if failures:
        print("SELF-TEST FAIL:")
        for x in failures:
            print("  - " + x)
        return 1
    print("PASS: check_site self-test ({} logo cases + {} 404-page logo cases + {} SITE_HOSTS env cases + "
          "coverage-root legs + {} opfiles draft cases + 5 per-root host cases + {} draft 404 cases + {} "
          "registered checks)".format(len(cases), len(not_found_cases), len(host_cases), len(draft_cases),
                                      len(draft_404_cases) + len(scan_cases) + len(promoted_scan_cases),
                                      len(EXECUTED)))
    return 0


def _shell_class_self_test():
    """The shell-class rule: a page on the template shell passes; an old-shell page flips the rule red; a
    recorded exclusion with a reason passes; an exclusion whose reason is empty is itself a finding."""
    draft = frozenset(("draft/",))
    on_shell = '<!doctype html><html lang="en" class="site-aiqt"><title>t</title>'
    off_shell = '<!doctype html><html lang="en"><title>t</title>'
    got = shell_class_findings("site", "a.html", "site/a.html", on_shell, False, draft)
    check("shell-class/template-shell-page-passes", got == [], got)
    got = shell_class_findings("site", "a.html", "site/a.html", off_shell, False, draft)
    check("shell-class/old-shell-page-fails",
          len(got) == 1 and "not on the template shell" in got[0], got)
    saved = dict(SHELL_CLASS_EXCLUSIONS)
    SHELL_CLASS_EXCLUSIONS.clear()
    try:
        SHELL_CLASS_EXCLUSIONS["site/a.html"] = "stays on the retired shell until its generator lands"
        got = shell_class_findings("site", "a.html", "site/a.html", off_shell, False, draft)
        check("shell-class/recorded-exclusion-passes", got == [], got)
        SHELL_CLASS_EXCLUSIONS["site/a.html"] = " "
        got = shell_class_findings("site", "a.html", "site/a.html", off_shell, False, draft)
        check("shell-class/exclusion-without-reason-fails",
              len(got) == 1 and "records no reason" in got[0], got)
    finally:
        SHELL_CLASS_EXCLUSIONS.clear()
        SHELL_CLASS_EXCLUSIONS.update(saved)


def _declaration_self_test():
    """The template page declaration: a well-formed one is read, each malformed entry raises ValueError, and
    the scan of a root exits 2 on one (fail-closed) rather than crashing with exit 1."""
    import contextlib
    import io
    import tempfile
    entry = '{"target": "site/draft/index.html", "kind": "file", "sources": ("src.toml",), "regenerate": "x"}'
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)

        def declare(literal):
            fixture = base / "case{}".format(len(list(base.iterdir())))
            (fixture / "tools").mkdir(parents=True)
            (fixture / "site").mkdir()
            (fixture / "src.toml").write_text("x", encoding="utf-8")
            (fixture / "site" / "a.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
            (fixture / _template_pages.GENERATOR).write_text("GENSRC_OUTPUTS = {}\n".format(literal),
                                                             encoding="utf-8")
            return fixture

        def scan(fixture):
            saved = _template_pages.ROOT
            _template_pages.ROOT = fixture
            err = io.StringIO()
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                    code = _scan_root(fixture, "site")
            except Exception as exc:  # a crash is the failure this case pins, so it is recorded, not raised
                code = "{}: {}".format(type(exc).__name__, exc)
            finally:
                _template_pages.ROOT = saved
            return code, err.getvalue()

        good = declare("({},)".format(entry))
        try:
            got = _template_pages.template_targets(good)
        except (OSError, ValueError) as exc:
            got = exc
        check("declaration/well-formed-read", got == frozenset({"site/draft/index.html"}), got)
        got = scan(good)
        check("declaration/well-formed-scan-not-refused", got[0] != 2, got)
        # Each malformed entry (a literal the declaration reader accepts as a tuple of entries) must raise
        # ValueError, never a KeyError, TypeError or a non-string target.
        for name, scan_name, literal in (
                ("declaration/entry-without-target-refused", "declaration/entry-without-target-scan-exits-2",
                 '({"tool": "x"},)'),
                ("declaration/bare-string-entry-refused", "declaration/bare-string-entry-scan-exits-2",
                 '("site/draft/index.html",)'),
                ("declaration/list-target-refused", "declaration/list-target-scan-exits-2", '({"target": ["a"]},)'),
                ("declaration/int-target-refused", "declaration/int-target-scan-exits-2", '({"target": 5},)'),
                ("declaration/full-entry-list-target-refused", "declaration/full-entry-list-target-scan-exits-2",
                 "({},)".format(entry.replace('"site/draft/index.html"', '["a"]'))),
                ("declaration/full-entry-int-target-refused", "declaration/full-entry-int-target-scan-exits-2",
                 "({},)".format(entry.replace('"site/draft/index.html"', "5")))):
            fixture = declare(literal)
            try:
                got = _template_pages.template_targets(fixture)
            except ValueError as exc:
                got = exc
            except Exception as exc:  # a crash is the failure this case pins, so it is recorded, not raised
                got = "{}: {}".format(type(exc).__name__, exc)
            check(name, isinstance(got, ValueError), got)
            code, err = scan(fixture)
            check(scan_name, code == 2 and "cannot read the template page targets" in err, (code, err))


_REAL_TEMPLATE_TARGETS = _template_pages.template_targets


def _staged_targets():
    """The page targets tools/gen_draft_site.py builds in the staged form (each site's pages under its root's
    draft/ folder, base "/draft/"), the form before promotion."""
    import gen_draft_site
    return frozenset(gen_draft_site.build(sites=gen_draft_site.staged_sites(), registered=False))


def _use_staged_declaration(on):
    """While `on`, the declaration read with no fixture root is the staged one, so the self-test cases written
    for the staged state (a live 404.html beside a draft/ folder of template pages) keep their meaning now
    that the real declaration is promoted; the real-tree case runs with it off."""
    if on:
        staged = _staged_targets()
        real_root = _template_pages.ROOT
        _template_pages.template_targets = (
            lambda root=None: staged if root is None and _template_pages.ROOT == real_root
            else _REAL_TEMPLATE_TARGETS(root))
    else:
        _template_pages.template_targets = _REAL_TEMPLATE_TARGETS


def _promoted_targets():
    """The page targets tools/gen_draft_site.py builds at base "/" (each site's pages at its own root): the
    promoted state the self-tests hold each template-page rule to, beside today's staged declaration."""
    import gen_draft_site
    return frozenset(gen_draft_site.build(sites=gen_draft_site.promoted_sites(), registered=False))


# The registered execution-suite identity (tools/selftest_checks.toml) and the executed-check-id record
# the instrumented check() helper appends to; the execution gate reconciles the report against the
# registry.
SUITE_ID = "site-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


def _card_head(host):
    """Card metas a fixture's generated page carries: they name /card.png on host as og:image."""
    image = "https://{}/card.png".format(host)
    values = (("property", "og:type", "website"), ("property", "og:title", "T"),
              ("property", "og:description", "D"), ("property", "og:url", "https://{}/".format(host)),
              ("property", "og:site_name", "S"), ("property", "og:image", image),
              ("property", "og:image:width", "1200"), ("property", "og:image:height", "627"),
              ("property", "og:image:alt", "A"), ("name", "twitter:card", "summary_large_image"),
              ("name", "twitter:image", image))
    return "".join('<meta {}="{}" content="{}">'.format(*value) for value in values)


def _png(width, height):
    """The signature and IHDR chunk of a PNG of the given size: all image_size reads."""
    return (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + width.to_bytes(4, "big")
            + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + b"\x00" * 4)


def _jpeg(width, height):
    """A JPEG start, an APP0 segment and a baseline SOF0 frame header of the given size."""
    return (b"\xff\xd8" + b"\xff\xe0\x00\x04\x00\x00" + b"\xff\xc0\x00\x11\x08" + height.to_bytes(2, "big")
            + width.to_bytes(2, "big") + b"\x03" + b"\x00" * 9)


def _card_self_test(quiet):
    """The link-preview card check: a generated page passes only with every card meta and an og:image naming
    a PNG or JPEG inside the root of at least 1200x627, whose stated size matches; a live page is not held to
    it; the scan and the real tree agree."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        root = base / "site"
        (root / "assets").mkdir(parents=True)
        for name, data in (("card.png", _png(1200, 627)), ("wide.png", _png(1201, 628)),
                           ("narrow.png", _png(1199, 627)), ("short.png", _png(1200, 626)),
                           ("card.jpg", _jpeg(1200, 627)), ("small.jpg", _jpeg(600, 315)),
                           ("card.gif", b"GIF89a" + b"\x00" * 20), ("card.svg", b"<svg/>")):
            (root / "assets" / name).write_bytes(data)
        (base / "outside.png").write_bytes(_png(1200, 627))
        site_root = root.resolve()
        hosts = {"aiqt.ai", "www.aiqt.ai"}

        def metas(changes=None):
            got = {"og:type": "website", "og:title": "T", "og:description": "D", "og:url": "https://aiqt.ai/",
                   "og:site_name": "AIQT.ai", "og:image": "https://aiqt.ai/assets/card.png",
                   "og:image:width": "1200", "og:image:height": "627", "og:image:alt": "A",
                   "twitter:card": "summary_large_image", "twitter:image": "https://aiqt.ai/assets/card.png"}
            got.update(changes or {})
            return {key: value for key, value in got.items() if value is not None}

        def image(url, width="1200", height="627"):
            return card_findings("a.html", metas({"og:image": url, "twitter:image": url,
                                                  "og:image:width": width, "og:image:height": height}),
                                 site_root, hosts)

        check("card/complete-passes", card_findings("a.html", metas(), site_root, hosts) == [], None)
        covered = []
        for key, name in (("og:type", "card/missing-og-type-fails"), ("og:title", "card/missing-og-title-fails"),
                          ("og:description", "card/missing-og-description-fails"),
                          ("og:url", "card/missing-og-url-fails"),
                          ("og:site_name", "card/missing-og-site_name-fails"),
                          ("og:image", "card/missing-og-image-fails"),
                          ("og:image:width", "card/missing-og-image-width-fails"),
                          ("og:image:height", "card/missing-og-image-height-fails"),
                          ("og:image:alt", "card/missing-og-image-alt-fails"),
                          ("twitter:card", "card/missing-twitter-card-fails"),
                          ("twitter:image", "card/missing-twitter-image-fails")):
            covered.append(key)
            got = card_findings("a.html", metas({key: None}), site_root, hosts)
            check(name, len(got) == 1 and "missing or empty <meta> " + key in got[0], got)
        if tuple(covered) != CARD_METAS:
            raise AssertionError("card/missing-* vectors do not cover CARD_METAS exactly")
        got = card_findings("a.html", metas({"og:title": ""}), site_root, hosts)
        check("card/empty-og-title-fails", len(got) == 1 and "og:title" in got[0], got)
        got = card_findings("a.html", metas({"twitter:card": "summary"}), site_root, hosts)
        check("card/summary-card-fails", len(got) == 1 and "summary_large_image" in got[0], got)
        got = card_findings("a.html", metas({"twitter:image": "https://aiqt.ai/assets/wide.png"}), site_root, hosts)
        check("card/twitter-image-differs-fails", len(got) == 1 and "differs" in got[0], got)
        for name, url in (("card/og-url-other-host-fails", "https://opfiles.ai/"),
                          ("card/og-url-http-fails", "http://aiqt.ai/")):
            got = card_findings("a.html", metas({"og:url": url}), site_root, hosts)
            check(name, len(got) == 1 and "og:url" in got[0], got)
        check("card/exact-minimum-passes", image("https://aiqt.ai/assets/card.png") == [], None)
        check("card/above-minimum-passes", image("https://aiqt.ai/assets/wide.png", "1201", "628") == [], None)
        check("card/jpeg-passes", image("https://aiqt.ai/assets/card.jpg") == [], None)
        for name, url, width, height in (("card/one-pixel-narrow-fails", "narrow.png", "1199", "627"),
                                         ("card/one-pixel-short-fails", "short.png", "1200", "626"),
                                         ("card/small-jpeg-fails", "small.jpg", "600", "315")):
            got = image("https://aiqt.ai/assets/" + url, width, height)
            check(name, len(got) == 1 and "below 1200x627" in got[0], got)
        got = image("https://aiqt.ai/assets/wide.png")
        check("card/stated-size-mismatch-fails", len(got) == 1 and "the file is 1201x628" in got[0], got)
        for name, url in (("card/gif-fails", "card.gif"), ("card/svg-fails", "card.svg")):
            got = image("https://aiqt.ai/assets/" + url)
            check(name, len(got) == 1 and "not a PNG or JPEG" in got[0], got)
        for name, url, words in (("card/missing-file-fails", "https://aiqt.ai/assets/gone.png", "names no file"),
                                 ("card/directory-fails", "https://aiqt.ai/assets/", "names no file"),
                                 ("card/escaping-path-fails", "https://aiqt.ai/../outside.png", "names no file"),
                                 ("card/other-host-image-fails", "https://example.test/assets/card.png",
                                  "not an https URL"),
                                 ("card/relative-image-fails", "/assets/card.png", "not an https URL"),
                                 ("card/http-image-fails", "http://aiqt.ai/assets/card.png", "not an https URL")):
            got = image(url)
            check(name, len(got) == 1 and words in got[0], got)

        head = "".join('<meta property="{}" content="{}">'.format(key, value) for key, value in metas().items())
        page = Page()
        page.feed("<head>" + head + '<meta name="description" content="x"></head>')
        check("card/parser-reads-metas", page.metas.get("og:image") == "https://aiqt.ai/assets/card.png"
              and page.metas.get("description") == "x", page.metas)

        # A generated template page is held to the card; a composed page (COMPOSED_PAGES) is held too;
        # a live page on the template shell outside both sets is not.
        (root / "assets" / "t.png").write_bytes(b"png")
        template = '<!doctype html><title>t</title><link rel="apple-touch-icon" href="/assets/t.png">'
        card_head = head.replace('<meta property="twitter:', '<meta name="twitter:')
        (root / "index.html").write_text(template + card_head, encoding="utf-8")
        targets = frozenset({"site/index.html"})
        check("card/scan-passes-with-card", quiet(_scan_root, base, "site", targets) == 0, None)
        (root / "index.html").write_text(template, encoding="utf-8")
        check("card/scan-reports-missing-card", quiet(_scan_root, base, "site", targets) == 1, None)
        on_shell = ('<!doctype html><html lang="en" class="site-aiqt"><title>t</title>'
                    '<link rel="apple-touch-icon" href="/assets/t.png">')
        (root / "index.html").write_text(on_shell, encoding="utf-8")
        check("card/live-page-not-held", quiet(_scan_root, base, "site", frozenset()) == 0, None)
        composed = root / "development.html"
        composed.write_text(on_shell + card_head, encoding="utf-8")
        check("card/composed-page-with-card-passes", quiet(_scan_root, base, "site", frozenset()) == 0, None)
        composed.write_text(on_shell + card_head.replace(
            '<meta property="og:image" content="https://aiqt.ai/assets/card.png">', ""), encoding="utf-8")
        check("card/composed-page-missing-og-image-fails",
              quiet(_scan_root, base, "site", frozenset()) == 1, None)
        composed.unlink()


def _touch_icon_self_test(quiet):
    """The touch-icon check: a live page passes only with a link naming an existing file under its root;
    a generated template page is exempt (staged under draft/ or promoted to the root), any other page under
    draft/ or merely named draft is not, and the scan and the live tree agree."""
    import tempfile
    staged = _template_pages.template_targets()
    promoted = _promoted_targets()
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        root = base / "site"
        (root / "assets" / "logos").mkdir(parents=True)
        (root / "sub").mkdir()
        (root / "assets" / "logos" / "t.png").write_bytes(b"png")
        (base / "outside.png").write_bytes(b"png")
        site_root = root.resolve()

        def findings(page_rel, icons, targets=staged, subdir="site"):
            return touch_icon_findings(page_rel, page_rel, icons, root / Path(page_rel).parent, site_root,
                                       _template_pages.is_template_page(subdir, page_rel, targets))

        got = findings("a.html", [("/assets/logos/t.png", 3)])
        check("touch-icon/root-absolute-href-passes", got == [], got)
        got = findings("sub/a.html", [("../assets/logos/t.png", 3)])
        check("touch-icon/relative-href-passes", got == [], got)
        got = findings("404.html", [("/assets/logos/t.png?v=1#x", 3)])
        check("touch-icon/query-and-fragment-ignored", got == [], got)
        got = findings("a.html", [])
        check("touch-icon/missing-link-fails", len(got) == 1 and "missing <link" in got[0], got)
        got = findings("404.html", [])
        check("touch-icon/404-page-checked", len(got) == 1 and "missing <link" in got[0], got)
        got = findings("a.html", [("/assets/logos/gone.png", 3)])
        check("touch-icon/missing-file-fails", len(got) == 1 and "does not exist" in got[0], got)
        got = findings("a.html", [("/assets/logos/t.png", 3), ("/assets/logos/gone.png", 4)])
        check("touch-icon/second-link-missing-file-fails", len(got) == 1 and "a.html:4:" in got[0], got)
        for name, href in (("touch-icon/directory-href-fails", "/assets/logos/"),
                           ("touch-icon/root-href-fails", "/"),
                           ("touch-icon/empty-href-fails", ""),
                           ("touch-icon/html-fallback-not-taken", "/assets/logos/t"),
                           ("touch-icon/escaping-href-fails", "/../outside.png"),
                           ("touch-icon/external-href-fails", "https://example.test/t.png")):
            got = findings("a.html", [(href, 3)])
            check(name, len(got) == 1 and "does not exist" in got[0], got)
        got = findings("draft/index.html", [])
        check("touch-icon/draft-page-exempt", got == [], got)
        got = findings("draft/install/index.html", [("/nope.png", 3)])
        check("touch-icon/nested-draft-page-exempt", got == [], got)
        got = findings("draft/a.html", [])
        check("touch-icon/unlisted-draft-page-checked", len(got) == 1 and "missing <link" in got[0], got)
        got = findings("index.html", [], promoted)
        check("touch-icon/promoted-template-page-exempt", got == [], got)
        got = findings("install/index.html", [("/nope.png", 3)], promoted)
        check("touch-icon/promoted-nested-template-page-exempt", got == [], got)
        # The /draft/ placeholder is itself a generated template page once promoted (the generator writes its
        # touch icon); any other page under draft/ is a live page and is checked.
        got = findings("draft/index.html", [], promoted)
        check("touch-icon/promoted-draft-placeholder-template-exempt", got == [], got)
        got = findings("draft/a.html", [], promoted)
        check("touch-icon/promoted-unlisted-draft-page-checked", len(got) == 1 and "missing <link" in got[0], got)
        got = findings("index.html", [])
        check("touch-icon/staged-root-page-checked", len(got) == 1 and "missing <link" in got[0], got)
        got = findings("draft/model/index.html", [], staged, "opf/site")
        check("touch-icon/opf-template-page-exempt", got == [], got)
        got = findings("model/index.html", [], promoted, "opf/site")
        check("touch-icon/opf-promoted-template-page-exempt", got == [], got)
        got = findings("draft/model/index.html", [], staged, "site")
        check("touch-icon/other-site-template-page-checked", len(got) == 1, got)
        got = findings("draft.html", [])
        check("touch-icon/page-named-draft-checked", len(got) == 1, got)
        got = findings("drafts/a.html", [])
        check("touch-icon/drafts-directory-checked", len(got) == 1, got)
        got = findings("guide/draft/a.html", [])
        check("touch-icon/nested-draft-directory-checked", len(got) == 1 and "missing <link" in got[0], got)
        got = findings("404.html", [("assets/logos/t.png", 3)])
        check("touch-icon/404-relative-href-fails", len(got) == 1 and "must be root-absolute" in got[0], got)
        got = findings("sub/404.html", [("../assets/logos/t.png", 3)])
        check("touch-icon/nested-404-relative-href-fails", len(got) == 1 and "must be root-absolute" in got[0],
              got)
        for name, page_rel in (("touch-icon/404-protocol-relative-href-named", "404.html"),
                               ("touch-icon/nested-404-protocol-relative-href-named", "sub/404.html"),
                               ("touch-icon/protocol-relative-href-named", "a.html")):
            got = findings(page_rel, [("//aiqt.ai/assets/logos/t.png", 3)])
            check(name, len(got) == 1 and "is a protocol-relative URL" in got[0], got)

        page = Page()
        page.feed('<link rel="icon" href="/f.svg"><link rel="Apple-Touch-Icon" href=" /t.png ">'
                  '<link rel="stylesheet" href="/s.css">')
        check("touch-icon/parser-reads-link", page.touch_icons == [("/t.png", 1)], page.touch_icons)

        shell = '<!doctype html><html lang="en" class="site-aiqt">'
        (root / "index.html").write_text(
            shell + '<title>t</title><link rel="apple-touch-icon" href="/assets/logos/t.png">',
            encoding="utf-8")
        (root / "sub" / "a.html").write_text(shell + "<title>t</title><p>x</p>", encoding="utf-8")
        check("touch-icon/scan-reports-missing-link", quiet(_scan_root, base, "site") == 1, None)
        (root / "sub" / "a.html").write_text(
            shell + '<title>t</title><link rel="apple-touch-icon" href="../assets/logos/t.png">',
            encoding="utf-8")
        check("touch-icon/scan-passes-when-linked", quiet(_scan_root, base, "site") == 0, None)

    _use_staged_declaration(False)
    got = quiet(main)
    _use_staged_declaration(True)
    check("live/real-tree-passes", got == 0, got)


def _not_found_links_self_test():
    """The 404-page reference allowlist: every URL-bearing attribute value on a page named 404.html, top-level
    or nested, that is not root-absolute, fragment-only or query-only (https:// too on a href; root-absolute
    only on script src and link href) is a finding even where it resolves from the page's own directory, and
    the same relative links pass on any other page."""
    import contextlib
    import io
    import tempfile

    def scan(base):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            return _scan_root(base, "site"), out.getvalue()

    # The structural allowlist also reports the img, video, object, form, math and svg markup these reference
    # cases use, so they count the reference findings, and a passing case allows only allowlist findings.
    def ref_findings(output):
        return [line for line in output.splitlines() if "on a 404 page must be root-absolute" in line]

    def other_findings(output):
        return [line for line in output.splitlines()[1:] if not line.endswith(" allowlist")]

    icon ='<link rel="apple-touch-icon" href="/t.png">'
    shell = '<!doctype html><html lang="en" class="site-aiqt">'
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        root = base / "site"
        (root / "sub").mkdir(parents=True)
        for directory in (root, root / "sub"):
            for asset in ("s.css", "f.svg", "j.js", "i.png", "l.png", "t.png"):
                (directory / asset).write_bytes(b"x")
            (directory / "b.html").write_text(shell + "<title>t</title>" + icon, encoding="utf-8")
        (root / "index.html").write_text(shell + '<title>t</title>' + icon +
                                         '<a class="logo" href="./"><img src="l.png" alt=""></a>', encoding="utf-8")

        def page(head, body):
            return shell + "<title>t</title>" + head + "<body>" + body + "</body>"

        stylesheet = '<link rel="stylesheet" href="s.css">'
        favicon = '<link rel="icon" href="f.svg">'
        script = '<script src="j.js"></script>'
        img = '<img src="i.png" alt="">'
        nav = '<nav><a href="b">B</a></nav>'
        logo = '<a class="logo" href="/"><img src="l.png" alt=""></a>'
        for name, page_rel, head, body, value in (
                ("404-links/relative-stylesheet-fails", "404.html", icon + stylesheet, "", "s.css"),
                ("404-links/nested-relative-stylesheet-fails", "sub/404.html", icon + stylesheet, "", "s.css"),
                ("404-links/relative-icon-fails", "404.html", icon + favicon, "", "f.svg"),
                ("404-links/nested-relative-icon-fails", "sub/404.html", icon + favicon, "", "f.svg"),
                ("404-links/relative-script-fails", "404.html", icon + script, "", "j.js"),
                ("404-links/nested-relative-script-fails", "sub/404.html", icon + script, "", "j.js"),
                ("404-links/relative-img-fails", "404.html", icon, img, "i.png"),
                ("404-links/nested-relative-img-fails", "sub/404.html", icon, img, "i.png"),
                ("404-links/relative-nav-link-fails", "404.html", icon, nav, "b"),
                ("404-links/nested-relative-nav-link-fails", "sub/404.html", icon, nav, "b"),
                ("404-links/relative-logo-img-fails", "404.html", icon, logo, "l.png"),
                ("404-links/nested-relative-logo-img-fails", "sub/404.html", icon, logo, "l.png")):
            (root / page_rel).write_text(page(head, body), encoding="utf-8")
            got = scan(base)
            (root / page_rel).unlink()
            refs = ref_findings(got[1])
            check(name, got[0] == 1 and len(refs) == 1 and refs[0].endswith("-> " + repr(value)), got)

        absolute = page(icon + '<link rel="stylesheet" href="/s.css"><link rel="icon" href="/f.svg">'
                        '<script src="/j.js"></script>',
                        '<p id="top"><img src="/i.png" alt=""></p><nav><a href="/b">B</a>'
                        '<a href="#top">T</a></nav><a class="logo" href="/"><img src="/l.png" alt=""></a>')
        (root / "404.html").write_text(absolute, encoding="utf-8")
        (root / "index.html").write_text(shell + '<title>t</title>' + icon +
                                         '<a class="logo" href="./"><img src="/l.png" alt=""></a>', encoding="utf-8")
        got = scan(base)
        check("404-links/root-absolute-and-fragment-links-pass", got[0] in (0, 1) and not other_findings(got[1]),
              got)
        (root / "404.html").unlink()

        (root / "sub" / "c.html").write_text(page(icon + '<link rel="stylesheet" href="s.css">',
                                                  '<img src="i.png" alt=""><nav><a href="b">B</a></nav>'),
                                             encoding="utf-8")
        got = scan(base)
        check("404-links/relative-links-on-other-page-pass", got[0] == 0, got)

        (root / "sub" / "404.html").write_text(page('<link rel="apple-touch-icon" href="t.png">', ""),
                                               encoding="utf-8")
        got = scan(base)
        check("404-links/relative-touch-icon-reported-by-both-rules", got[0] == 1 and "FAIL: 2 site/" in got[1]
              and "apple-touch-icon on a 404 page is relative" in got[1]
              and "href on a 404 page must be root-absolute" in got[1], got)
        (root / "sub" / "404.html").unlink()

        # The allowlist reads the raw markup: each refused value is named with its attribute, and the number
        # of findings is exact. Each accepted form is shown passing on a page of its own.
        for name, page_rel, head, body, attr, value, count in (
                ("404-links/percent-encoded-slash-src-fails", "404.html", icon, '<img src="%2Fi.png" alt="">',
                 "src", "%2Fi.png", 1),
                ("404-links/nested-percent-encoded-slash-src-fails", "sub/404.html", icon,
                 '<img src="%2Fi.png" alt="">', "src", "%2Fi.png", 1),
                ("404-links/percent-encoded-slash-touch-icon-fails", "404.html",
                 '<link rel="apple-touch-icon" href="%2Ft.png">', "", "href", "%2Ft.png", 1),
                ("404-links/dot-slash-src-fails", "404.html", icon, '<img src="./i.png" alt="">', "src", "./i.png", 1),
                ("404-links/dot-dot-slash-href-fails", "sub/404.html", icon, '<a href="../b">B</a>', "href", "../b", 1),
                ("404-links/svg-use-href-fails", "404.html", icon, '<svg><use href="f.svg#a"></use></svg>',
                 "href", "f.svg#a", 1),
                ("404-links/svg-image-href-fails", "404.html", icon, '<svg><image href="i.png"/></svg>',
                 "href", "i.png", 1),
                ("404-links/svg-xlink-href-fails", "404.html", icon, '<svg><use xlink:href="f.svg#a"/></svg>',
                 "xlink:href", "f.svg#a", 1),
                ("404-links/math-href-fails", "404.html", icon, '<math><mi href="b">x</mi></math>', "href", "b", 1),
                ("404-links/srcset-relative-candidate-fails", "404.html", icon,
                 '<img src="/i.png" srcset="/i.png 1x, i.png 2x" alt="">', "srcset", "i.png", 1),
                ("404-links/poster-fails", "404.html", icon, '<video poster="i.png"></video>', "poster", "i.png", 1),
                ("404-links/object-data-fails", "404.html", icon, '<object data="f.svg"></object>', "data", "f.svg", 1),
                ("404-links/form-action-fails", "404.html", icon, '<form action="b"></form>', "action", "b", 1),
                ("404-links/duplicate-src-first-relative-fails", "404.html", icon,
                 '<img src="i.png" src="/i.png" alt="">', "src", "i.png", 1),
                ("404-links/protocol-relative-src-fails", "404.html", icon, '<img src="//aiqt.ai/i.png" alt="">',
                 "src", "//aiqt.ai/i.png", 1),
                ("404-links/entity-encoded-second-slash-fails", "404.html", icon,
                 '<img src="/&#47;aiqt.ai/i.png" alt="">', "src", "/&#47;aiqt.ai/i.png", 1),
                ("404-links/backslash-after-slash-fails", "404.html", icon, '<img src="/\\aiqt.ai/i.png" alt="">',
                 "src", "/\\aiqt.ai/i.png", 1),
                ("404-links/empty-href-fails", "404.html", icon, '<a href="">E</a>', "href", "", 1),
                ("404-links/other-scheme-fails", "404.html", icon, '<a href="http://aiqt.ai/b">B</a>',
                 "href", "http://aiqt.ai/b", 1),
                # A resource load from another host, and https:// on any attribute but a href.
                ("404-links/cross-host-script-src-fails", "404.html",
                 icon + '<script src="https://evil.example/x.js"></script>', "", "src", "https://evil.example/x.js", 1),
                ("404-links/cross-host-stylesheet-fails", "404.html",
                 icon + '<link rel="stylesheet" href="https://evil.example/s.css">', "", "href",
                 "https://evil.example/s.css", 1),
                ("404-links/cross-host-icon-fails", "404.html",
                 icon + '<link rel="icon" href="https://evil.example/f.svg">', "", "href",
                 "https://evil.example/f.svg", 1),
                ("404-links/cross-host-prefetch-fails", "404.html",
                 icon + '<link rel="prefetch" href="https://evil.example/p">', "", "href", "https://evil.example/p", 1),
                ("404-links/cross-host-preconnect-fails", "404.html",
                 icon + '<link rel="preconnect" href="https://evil.example">', "", "href", "https://evil.example", 1),
                ("404-links/https-img-src-fails", "404.html", icon, '<img src="https://example.test/i.png" alt="">',
                 "src", "https://example.test/i.png", 1),
                # A query-only or fragment-only load resolves against the request URL.
                ("404-links/query-only-script-src-fails", "404.html", icon + '<script src="?x"></script>', "",
                 "src", "?x", 1),
                ("404-links/fragment-only-script-src-fails", "404.html", icon + '<script src="#x"></script>', "",
                 "src", "#x", 1),
                ("404-links/query-only-stylesheet-fails", "404.html", icon + '<link rel="stylesheet" href="?v=1">',
                 "", "href", "?v=1", 1),
                ("404-links/fragment-only-icon-fails", "404.html", icon + '<link rel="icon" href="#">', "",
                 "href", "#", 1)):
            (root / page_rel).write_text(page(head, body), encoding="utf-8")
            got = scan(base)
            (root / page_rel).unlink()
            check(name, got[0] == 1 and len(ref_findings(got[1])) == count
                  and "{} on a 404 page must be root-absolute".format(attr) in got[1]
                  and "-> {}\n".format(repr(value)) in got[1], got)
        for name, body in (
                ("404-links/query-only-passes", '<a href="?q=1">Q</a><form action="?q=1"></form>'),
                ("404-links/fragment-only-passes", '<p id="top"></p><a href="#top">T</a>'
                 '<svg><use href="#top"></use></svg>'),
                ("404-links/root-absolute-passes", '<a href="/">H</a><img src="/i.png" srcset="/i.png 1x, /i.png 2x" '
                 'alt=""><video poster="/i.png"></video><object data="/f.svg"></object><form action="/b"></form>'),
                ("404-links/https-passes", '<a href="https://aiqt.ai/b">B</a><a href="https://example.test/b">E</a>')):
            (root / "404.html").write_text(page(icon, body), encoding="utf-8")
            got = scan(base)
            (root / "404.html").unlink()
            check(name, got[0] in (0, 1) and not other_findings(got[1]), got)

        # A tab or newline after the leading "/" can make a protocol-relative //host URL.
        for name, value in (("404-links/tab-after-slash-fails", "/\taiqt.ai/b"),
                            ("404-links/newline-after-slash-fails", "/\naiqt.ai/b")):
            (root / "404.html").write_text(page(icon, '<a href="{}">B</a>'.format(value)), encoding="utf-8")
            got = scan(base)
            (root / "404.html").unlink()
            refs = ref_findings(got[1])
            check(name, got[0] == 1 and len(refs) == 1 and refs[0].endswith("-> " + repr(value)), got)

        # The structural allowlist: each case is refused by the guard its message names, whatever else fires.
        for name, raw, message in (
                ("404-structure/formaction-fails", page(icon, '<button type="button" formaction="b">B</button>'),
                 "attribute formaction on <button> is not in the 404-page attribute allowlist"),
                ("404-structure/imagesrcset-fails", page(icon + '<link rel="preload" imagesrcset="i.png 1x">', ""),
                 "attribute imagesrcset on <link> is not in the 404-page attribute allowlist"),
                ("404-structure/background-fails",
                 "<!doctype html><title>t</title>" + icon + '<body background="i.png"></body>',
                 "attribute background on <body> is not in the 404-page attribute allowlist"),
                ("404-structure/ping-fails", page(icon, '<a href="/" ping="b">H</a>'),
                 "attribute ping on <a> is not in the 404-page attribute allowlist"),
                ("404-structure/meta-refresh-fails", page(icon + '<meta http-equiv="refresh" content="0;url=b">', ""),
                 "attribute http-equiv on <meta> is not one the live 404 pages use on meta"),
                ("404-structure/style-attribute-url-fails", page(icon, '<div style="background:url(i.png)"></div>'),
                 "style value on a 404 page contains url("),
                ("404-structure/entity-escaped-url-fails", page(icon, '<div class="u&#114;l&#40;i.png)"></div>'),
                 "class value on a 404 page contains url("),
                ("404-structure/style-element-import-fails", page(icon + '<style>@import "s.css";</style>', ""),
                 "element <style> is not in the 404-page element allowlist"),
                ("404-structure/style-element-url-fails", page(icon + "<style>p{background:URL(i.png)}</style>", ""),
                 "<style> text on a 404 page contains url("),
                ("404-structure/fill-url-fails", page(icon, '<svg><text fill="url(#g)">A</text></svg>'),
                 "fill value on a 404 page contains url("),
                ("404-structure/fill-unpinned-value-fails", page(icon, '<svg><text fill="red">A</text></svg>'),
                 "fill value on a 404 page is not one the live 404 pages use"),
                ("404-structure/svg-set-fails", page(icon, '<svg><set attributeName="href" to="b"/></svg>'),
                 "element <set> is not in the 404-page element allowlist"),
                ("404-structure/import-map-fails",
                 page(icon + '<script type="importmap">{"imports":{"a":"./j.js"}}</script>', ""),
                 "inline <script> on a 404 page is not a pinned script body"),
                ("404-structure/inline-module-fails",
                 page(icon + '<script type="module">import "./j.js";</script>', ""),
                 "inline <script> on a 404 page is not a pinned script body"),
                ("404-structure/unpinned-onclick-fails", page(icon, '<button type="button" onclick="go()">B</button>'),
                 "onclick value on a 404 page is not one the live 404 pages use"),
                ("404-structure/iframe-srcdoc-fails", page(icon, '<iframe srcdoc="&lt;img src=i.png&gt;"></iframe>'),
                 "attribute srcdoc on <iframe> is not in the 404-page attribute allowlist"),
                ("404-structure/base-href-fails", page('<base href="/sub/">' + icon, ""),
                 "element <base> is not in the 404-page element allowlist"),
                ("404-structure/svg-script-with-src-and-body-fails",
                 page(icon, '<svg><script src="/j.js">fetch("x")</script></svg>'),
                 "<script> at or after an <svg> start tag on a 404 page is refused"),
                ("404-structure/svg-script-after-comment-end-tag-fails",
                 page(icon, '<svg><!-- </svg> --><script src="/j.js"></script></svg>'),
                 "<script> at or after an <svg> start tag on a 404 page is refused"),
                ("404-structure/script-with-src-and-body-fails",
                 page(icon + '<script src="/j.js">fetch("x")</script>', ""),
                 "<script> with src on a 404 page must have an empty body")):
            (root / "404.html").write_text(raw, encoding="utf-8")
            got = scan(base)
            (root / "404.html").unlink()
            check(name, got[0] == 1 and message in got[1], got)
        # The edges of the two script rules: a script just before the first svg is not refused by the svg rule,
        # and a script with src whose body is one space is refused by the empty-body rule.
        got = not_found_structure_findings("404.html", '<script src="/j.js"></script><svg></svg>')
        check("404-structure/script-before-svg-passes", not any("at or after an <svg>" in f for f in got), got)
        got = not_found_structure_findings("404.html", '<script src="/j.js"> </script>')
        check("404-structure/script-with-src-and-space-body-fails",
              any("must have an empty body" in f for f in got), got)

        # A 404 page the parser rejects still gets both raw-markup rules (the parse failure is simulated: no
        # input tried makes this parser raise).
        (root / "404.html").write_text(page(icon, '<a href="b">B</a><b ping="b">P</b>'), encoding="utf-8")
        real_feed = Page.feed

        def failing_feed(self, data):
            raise ValueError("simulated parse failure")
        Page.feed = failing_feed
        try:
            got = scan(base)
        finally:
            Page.feed = real_feed
        (root / "404.html").unlink()
        check("404-structure/unparsed-page-still-checked", got[0] == 1 and "could not parse as HTML" in got[1]
              and "href on a 404 page must be root-absolute" in got[1]
              and "attribute ping on <b> is not in the 404-page attribute allowlist" in got[1], got)


# Coverage roots: site/ (aiqt.ai) and opf/site/ (opfiles.ai: the served site pages). Matching this gate's
# existing site/ idiom, each root is OPTIONAL: an absent root is skipped (not fail-closed), so a vendored
# pack with neither still passes; a present root is scanned, and an unreadable present root fails closed.
# Each root is its own link-containment boundary, so a page's relative links (./manifest, ./disclosure)
# resolve within opf/site and never escape.
COVERAGE_ROOTS = ("site", "opf/site")


def run(root):
    """Scan every present coverage root under `root`. Return the worst per-root exit code; roots are
    optional (absent is skipped), a present root is scanned, and an unreadable present root fails closed."""
    present = [sub for sub in COVERAGE_ROOTS if (root / sub).is_dir()]
    if not present:
        print("PASS: no site/ or opf/site/ directory")
        return 0
    worst = 0
    for sub in present:
        worst = max(worst, _scan_root(root, sub))
    return worst


def main():
    return run(Path(__file__).resolve().parents[1])


def _scan_root(root, subdir, targets=None):
    """Scan one coverage root. `targets` (a fixture's GENSRC_OUTPUTS targets) defaults to the generator's
    declaration; a declaration that cannot be read fails closed."""
    site = root / subdir
    site_root = site.resolve()
    hosts = ROOT_HOSTS.get(subdir, SITE_HOSTS)
    if targets is None:
        try:
            targets = _template_pages.template_targets()
        except (OSError, ValueError) as exc:
            print("error: cannot read the template page targets ({}); fail-closed".format(exc), file=sys.stderr)
            return 2
    draft_dirs = _template_pages.draft_folders(subdir, targets)
    docs, ids_by_path, findings = [], {}, []
    try:
        html_files = sorted(walk_files(site, suffixes={".html"}))
    except OSError as exc:
        # an unreadable directory under the root is a read error, not a clean skip: fail closed (exit 2)
        # so the site gate never reports clean without having scanned an unreadable subtree.
        print("error: cannot scan {}/ ({}); fail-closed".format(subdir, exc), file=sys.stderr)
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
        # Both 404 rules read the raw text, not the parsed page. A 404.html the site template generates is held
        # to the draft structural allowlist; every other 404.html keeps the live list. The reference rule is the
        # same for every 404 page.
        template = _template_pages.is_template_page(subdir, f.relative_to(site), targets)
        findings.extend(shell_class_findings(subdir, f.relative_to(site).as_posix(), str(rel), text,
                                             template, draft_dirs))
        if f.name == NOT_FOUND_PAGE:
            findings.extend(not_found_reference_findings(rel, text))
            findings.extend(not_found_structure_findings(rel, text, draft=template))
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
            classified = classify(v, hosts)
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
        findings.extend(touch_icon_findings(rel, f.relative_to(site), page.touch_icons, f.parent, site_root,
                                            template))
        if template or f.relative_to(site).as_posix() in COMPOSED_PAGES.get(subdir, frozenset()):
            findings.extend(card_findings(rel, page.metas, site_root, hosts))
    # Logo consistency: the header logo block is duplicated per page (no page generator yet), so it
    # must stay byte-identical on every page that carries it; an outlier is drift.
    findings.extend(logo_findings([(str(rel), text) for _f, rel, text, _p in docs]))
    if findings:
        print("FAIL: {} {}/ site-integrity issue(s)".format(len(findings), subdir))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: {}/ dashes, links, anchors, titles, unique ids, tag structure, touch icons and link-preview "
          "cards all check out".format(subdir))
    return 0


def _entry(argv):
    # The execution gate appends `--execution-report ABS_PATH` to the self-test launch; accepted only in
    # that trailing two-token form, with an absolute path, alone or after --self-test.
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        # Finalized at interpreter exit by tools/_selftest_exit_report.py; nothing writes it in band.
        import _selftest_exit_report
        _selftest_exit_report.arm(argv[-1], SUITE_ID, EXECUTED)
        return _self_test()
    if "--self-test" in argv:
        return _self_test()
    return main()


if __name__ == "__main__":
    _code = _entry(sys.argv[1:])
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
