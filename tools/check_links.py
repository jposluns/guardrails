#!/usr/bin/env python3
"""Fail on repository links whose target does not exist.

The Markdown forms read, line by line as CommonMark and GitHub Flavored Markdown render them:

- an inline link [text](dest) or image ![alt](dest), the destination read as CommonMark reads it: either
  <...> in angle brackets, or a run with no space or control character in which parentheses must
  balance (so [Guide](https://aiqt.ai/guide(v1)) reads https://aiqt.ai/guide(v1)), with backslash
  escapes and an optional title;
- a link reference definition [label]: dest, whose destination may stand on the next line; checking
  every definition checks the target of every reference-style link [text][label], [label][] or [label]
  and every reference-style image that uses it (an undefined reference renders as text, not a link);
- an autolink <scheme:...>;
- a bare URL, read as GitHub's extended autolink reads it: http://, https:// or www. at the start of a
  line or after whitespace, *, _, ~, ( or |, running to whitespace, < or |, less trailing ?!.,:*_~, an
  unbalanced trailing ) and a trailing entity reference (a www. URL renders as http://); a bare URL in
  an inline link's text or inside a raw HTML tag is not a link and is not read, and a | ends it even
  outside a table, where GitHub would keep it;
- raw HTML: the href of an a element and the src of an img element, in a start tag that CommonMark's
  open-tag grammar reads as raw HTML, written on one line (the first of two attributes with one name
  counts, as the HTML parser keeps it); only an own-site URL there is checked, since a root-relative
  href in a page source such as docs/*.md names a site path, not a repository file. Other elements and
  attributes (srcset, source, link, area and the rest) are not read.

Entity and numeric character references (&#115;, &#x73;, &amp;) are decoded before a URL is classified:
in an inline destination, a reference definition and an autolink, as cmark (which GitHub renders with)
decodes them, with the semicolon required and a backslash-escaped & left literal; in an HTML attribute
value, as the HTML parser decodes them. They are not decoded in a bare URL; whether GitHub decodes one
there before autolinking is not verified.

Code spans and fenced code blocks are read like any other text, so an own-site URL written in code is
checked too. A link whose text or destination spans two lines is not read.

Two kinds of target are checked:

1. A relative destination of an inline link, inline image or reference definition resolves against the
   file's directory (a root-relative one, starting with "/", against the repository root, as GitHub
   renders it) and must exist inside the repository; a path holding NUL names no file.
2. An own-site link: an absolute http or https URL, in any form above, on a host this project's sites
   are served at, or an href in a generated-Markdown source (GENERATED_MD_SOURCES), joined exactly as
   that source's generator renders it into the Markdown. tools/gen_roadmap.py renders every
   stage[].links[].href as site_base + href, whatever the href, so an href there must start with a
   single "/"; tools/gen_disclosure.py renders every row[].evidence[].href as the href itself when it
   starts with "https://" and as site_base + href otherwise, so an href there must be an https URL
   with a host or start with a single "/". An href the generator would render wrongly (one that does
   not start with "/", starts with "//" or, for roadmap.toml, is already absolute), an href that is
   not a string, a site_base that is not an http or https origin without a trailing "/", query or
   fragment, or a source shaped other than its generator reads, is malformed: exit 2. An href key
   elsewhere in a source is not rendered and is not read. The host picks the site tree that serves it
   (SITE_ROOTS, the same host model as tools/check_site.py: aiqt.ai and www.aiqt.ai, or the
   AIQT_SITE_HOST host and its www., serve site/; opfiles.ai and www.opfiles.ai serve opf/site/; a host
   written with its trailing root-label dot, such as aiqt.ai., is the same host), and the URL path must
   name a page of that tree: a regular file inside it at the path as written, at path + ".html", or at
   path/index.html ("/" is index.html), never a host configuration file at the tree's top
   (HOST_CONFIG_FILES: _headers and _redirects), or else the first rule in that tree's
   _redirects whose source matches the path (exactly, through a trailing * splat or through a /:name
   placeholder), when that rule counts. A rule counts only when its status is a redirect (301, 302,
   303, 307 or 308; a rule with no status is a 302) or a 200 rewrite, and its destination itself
   resolves: a destination path or own-site URL resolves by these same rules (a chain that returns to
   a path it has visited, or runs past MAX_REDIRECT_HOPS, loops and does not count); an external
   destination counts for a redirect (whether it is live is out of scope), and a 200 rewrite counts only
   to a /path on the same site, never to an absolute URL, own-site or not (whether Cloudflare Pages
   proxies an absolute URL to its own origin is not verified; this is the stricter reading). Splats and
   placeholders: a rule whose source holds
   a * splat or a :name placeholder counts only when its destination carries every value it captures
   (:splat for the *, :name for each placeholder), and the destination is then resolved with those
   values substituted for the link being checked; a rule that sends every match to one fixed
   destination, such as /* /index.html 200, is a catch-all that serves something for any path and
   proves no page, so it never counts and a link it matches fails. A _redirects line that is not
   "source destination [status]" with a numeric status is malformed, and so is a _redirects that is a
   symbolic link (dangling or not) or not a regular file: exit 2. A tree absent from the checkout is not
   checked (an adopter vendoring this gate has no site/); a tree path that is present but is not a
   directory is exit 2. The fragment and query are not checked; tools/check_site.py validates anchors
   inside the HTML.

Other external URLs, mailto and tel links, and pure anchors are out of scope: this gate answers "does the
target exist in this checkout", not "is the URL live". An autolink, bare URL or raw HTML link is checked
only when it is own-site.

PENDING_DECISION_ALLOWLIST names own-site links knowingly broken while a maintainer decides their fate.
Each entry is temporary, honoured only for its exact resolved URL (in any scanned file, not only the one
that motivated it), and printed on every run as temporary; an entry whose target page now exists is itself
a finding, so it is removed in the change that decides the link. An entry that no scanned link uses is not
flagged, and an entry carries no owner or expiry.

  check_links.py              scan the repository (exit 0 clean, 1 findings, 2 cannot evaluate)
  check_links.py --self-test  run every rule above over synthetic fixture trees
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_links.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import html
import html.entities
import os
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

SKIP_DIRS = {".git", "node_modules", "__pycache__"}
NOT_RELATIVE = ("http://", "https://", "mailto:", "tel:", "#")

# CommonMark: a backslash escapes any ASCII punctuation character.
_ESCAPABLE = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
# CommonMark autolink: <scheme:...> with a 2 to 32 character scheme and no space, control, < or >.
_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.\-]{1,31}:[^\x00-\x20<>]*)>")
# GitHub extended autolink: http://, https:// or www., then a domain with at least one period, then any
# run of characters other than whitespace and <; it starts a line or follows whitespace, *, _, ~ or (.
# A | is accepted before it and ends it too, so a URL that fills a table cell with no space is read as the
# cell holds it (outside a table, a | in a URL ends the URL here, though GitHub would keep it).
_BARE_URL = re.compile(r"(?<![^\s*_~(|])((?:https?://|www\.)[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+[^\s<|]*)",
                       re.IGNORECASE)
_TRAILING_ENTITY = re.compile(r"&[A-Za-z0-9]+;$")
# CommonMark entity and numeric character references: &name; for an HTML5 entity name, &#digits; with 1 to
# 7 digits, &#xhex; with 1 to 6 hex digits; the semicolon is required.
_CHAR_REF = re.compile(r"&(?:#([0-9]{1,7})|#[xX]([0-9A-Fa-f]{1,6})|([A-Za-z][A-Za-z0-9]{0,31}));")
# CommonMark raw HTML open tag of an a or img element, read with CommonMark's open-tag grammar (attribute
# name, optional value unquoted, '...' or "..."), on one line.
_HTML_ATTR_ONE = re.compile(
    r"([A-Za-z_:][A-Za-z0-9_.:-]*)(?:[ \t]*=[ \t]*([^\x00-\x20\"'=<>`]+|'[^']*'|\"[^\"]*\"))?")
_HTML_TAG = re.compile(
    r"<(a|img)((?:[ \t]+[A-Za-z_:][A-Za-z0-9_.:-]*(?:[ \t]*=[ \t]*(?:[^\x00-\x20\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*)"
    r"[ \t]*/?>", re.IGNORECASE)
# An HTML character reference in an attribute value, as the HTML parser reads it (semicolon optional).
_HTML_REF = re.compile(r"&(#[xX][0-9A-Fa-f]+;?|#[0-9]+;?|[A-Za-z][A-Za-z0-9]*;?)")
# CommonMark link reference definition: up to three spaces, [label], a colon, then the destination.
_DEFINITION = re.compile(r"^ {0,3}\[((?:[^\[\]\\]|\\.){1,999})\]:(.*)$")

# The hosts this project's sites are served at, each mapped to the tree that serves it. The site/ host
# follows tools/check_site.py (AIQT_SITE_HOST, default and empty-value fallback aiqt.ai, lowercased); the
# canonical and og:url tags of site/ name aiqt.ai and those of opf/site/ name opfiles.ai.
_SITE_HOST = (os.environ.get("AIQT_SITE_HOST", "aiqt.ai") or "aiqt.ai").lower()
SITE_ROOTS = {
    _SITE_HOST: "site",
    "www." + _SITE_HOST: "site",
    "opfiles.ai": "opf/site",
    "www.opfiles.ai": "opf/site",
}

# _redirects statuses (Cloudflare Pages): a rule with no status is a 302.
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
REWRITE_STATUS = 200
DEFAULT_STATUS = 302
MAX_REDIRECT_HOPS = 16
# Files at a site tree's top that configure the host and are not served as pages (Cloudflare Pages reads
# them; that it does not serve them is assumed here, which is the stricter reading).
HOST_CONFIG_FILES = frozenset({"_headers", "_redirects"})

# Own-site links knowingly broken pending a maintainer decision. TEMPORARY: each entry is keyed by the
# exact resolved URL (another spelling, host, scheme, case, trailing slash or fragment is not covered) and
# is named in every run's output.
PENDING_DECISION_ALLOWLIST = {
    # roadmap-teams-link: roadmap.toml links "Read about Teams & Enterprise." to /teams, which ROADMAP.md
    # renders as https://aiqt.ai/teams. The site cutover removed docs/teams.md and site/teams.html, and
    # site/_redirects has no rule for /teams. Restoring a page, adding a redirect or dropping the link is
    # a maintainer decision; remove this entry in the change that makes it.
    "https://aiqt.ai/teams": "roadmap-teams-link",
}

_PLACEHOLDER = re.compile(r"(\*|(?<=/):[A-Za-z_][A-Za-z0-9_]*)")
_DEST_NAME = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


class ScanError(Exception):
    """A tree or source this gate must read cannot be read as declared (exit 2)."""


# ---------------------------------------------------------------------------------------------------
# Markdown reading

def _char_ref_at(text, i):
    """(decoded text, end index) when a CommonMark entity or numeric character reference starts at
    text[i], else None. A numeric reference to 0, a surrogate or a code point past U+10FFFF is U+FFFD."""
    match = _CHAR_REF.match(text, i)
    if match is None:
        return None
    decimal, hexadecimal, name = match.groups()
    if name is not None:
        value = html.entities.html5.get(name + ";")
        return None if value is None else (value, match.end())
    code = int(decimal) if decimal is not None else int(hexadecimal, 16)
    if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        return "�", match.end()
    return chr(code), match.end()


def decode_char_refs(text):
    """text with every CommonMark entity and numeric character reference decoded (no backslash escapes)."""
    out = []
    i = 0
    while i < len(text):
        ref = _char_ref_at(text, i) if text[i] == "&" else None
        if ref is None:
            out.append(text[i])
            i += 1
        else:
            out.append(ref[0])
            i = ref[1]
    return "".join(out)


def _html_ref(match):
    """One HTML character reference in an attribute value, decoded as the HTML parser decodes it: the
    longest entity name wins, and a name without ';' followed by '=' or an ASCII letter or digit stays
    literal (the attribute-value rule)."""
    ref = match.group(1)
    if ref.startswith("#"):
        return html.unescape(match.group(0))
    for x in range(len(ref), 1, -1):
        name = ref[:x]
        if name in html.entities.html5:
            following = ref[x:x + 1] or match.string[match.end():match.end() + 1]
            if not name.endswith(";") and (following == "=" or (following.isascii() and following.isalnum())):
                return match.group(0)
            return html.entities.html5[name] + ref[x:]
    return match.group(0)


def _html_attr_value(raw):
    """An HTML attribute value with its quotes removed, character references decoded and the ASCII
    whitespace the URL parser strips removed from both ends."""
    if raw[:1] in ("\"", "'"):
        raw = raw[1:-1]
    return _HTML_REF.sub(_html_ref, raw).strip(" \t\n\r\f")


def _parse_destination(text, i):
    """The CommonMark link destination starting at text[i] as (destination, end index), or None.

    <...>: no line ending and no unescaped < or >. Otherwise: a nonempty run with no ASCII space or
    control character, in which unescaped parentheses must balance. Backslash escapes are removed and
    entity and numeric character references are decoded (a decoded character is never structural)."""
    n = len(text)
    out = []
    if i < n and text[i] == "<":
        j = i + 1
        while j < n:
            ch = text[j]
            if ch == "\\" and j + 1 < n and text[j + 1] in _ESCAPABLE:
                out.append(text[j + 1])
                j += 2
                continue
            ref = _char_ref_at(text, j) if ch == "&" else None
            if ref is not None:
                out.append(ref[0])
                j = ref[1]
                continue
            if ch == ">":
                return "".join(out), j + 1
            if ch in "<\n\r":
                return None
            out.append(ch)
            j += 1
        return None
    depth = 0
    j = i
    while j < n:
        ch = text[j]
        if ch == "\\" and j + 1 < n and text[j + 1] in _ESCAPABLE:
            out.append(text[j + 1])
            j += 2
            continue
        ref = _char_ref_at(text, j) if ch == "&" else None
        if ref is not None:
            out.append(ref[0])
            j = ref[1]
            continue
        if ord(ch) <= 0x20 or ord(ch) == 0x7F:
            break
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        out.append(ch)
        j += 1
    if j == i or depth != 0:
        return None
    return "".join(out), j


def _skip_space(text, i):
    while i < len(text) and text[i] in " \t":
        i += 1
    return i


def _parse_title(text, i):
    """The end index of a CommonMark link title starting at text[i] ("...", '...' or (...)), or None."""
    if i >= len(text) or text[i] not in "\"'(":
        return None
    close = ")" if text[i] == "(" else text[i]
    j = i + 1
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == close:
            return j + 1
        if close == ")" and ch == "(":
            return None
        j += 1
    return None


def _parse_inline_tail(text, i):
    """Parse '(dest "title")' after the ']' of a link, from the index just past '('. Returns
    (destination, end index past ')') or None when it is not an inline link."""
    i = _skip_space(text, i)
    if i < len(text) and text[i] == ")":
        return "", i + 1
    parsed = _parse_destination(text, i)
    if parsed is None:
        return None
    dest, j = parsed
    k = _skip_space(text, j)
    if k < len(text) and text[k] != ")":
        if k == j:
            return None
        end = _parse_title(text, k)
        if end is None:
            return None
        k = _skip_space(text, end)
    if k < len(text) and text[k] == ")":
        return dest, k + 1
    return None


def _match_bracket(text, i):
    """The index of the ']' that closes the '[' at text[i] (nested brackets balance, backslash escapes
    are skipped), or None."""
    depth = 0
    j = i
    while j < len(text):
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return None


def _gfm_trim(url):
    """Drop what GitHub's extended autolink leaves out at the end of a bare URL."""
    while url:
        last = url[-1]
        if last in "?!.,:*_~":
            url = url[:-1]
        elif last == ")" and url.count(")") > url.count("("):
            url = url[:-1]
        elif last == ";" and _TRAILING_ENTITY.search(url):
            url = url[:_TRAILING_ENTITY.search(url).start()]
        else:
            break
    return url


def line_targets(line):
    """Every (kind, target) one Markdown line links to, in reading order. kind is "inline" (an inline
    link or image destination), "autolink", "bare" or "html" (an a element's href or an img element's
    src in raw HTML). Character references are decoded in an inline destination and an autolink (as
    cmark, which GitHub renders with, decodes them) and in an HTML attribute value; a bare URL inside an
    inline link's text is not a link and is not read."""
    targets, masked, link_text = [], [], []
    n = len(line)
    i = 0
    while i < n:
        span = next((end for start, end in masked if start <= i < end), None)
        if span is not None:
            i = span
            continue
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "[":
            close = _match_bracket(line, i)
            if close is not None and close + 1 < n and line[close + 1] == "(":
                parsed = _parse_inline_tail(line, close + 2)
                if parsed is not None:
                    dest, end = parsed
                    if dest:
                        targets.append((i, "inline", dest))
                    masked.append((close + 1, end))
                    link_text.append((i + 1, close))
            i += 1
            continue
        if ch == "<":
            match = _AUTOLINK.match(line, i)
            if match:
                targets.append((i, "autolink", decode_char_refs(match.group(1))))
                masked.append((i, match.end()))
                i = match.end()
                continue
        i += 1
    for match in _HTML_TAG.finditer(line):
        link_text.append((match.start(), match.end()))  # a URL inside the tag is no bare URL
        wanted = "href" if match.group(1).lower() == "a" else "src"
        for attr in _HTML_ATTR_ONE.finditer(match.group(2)):
            if attr.group(1).lower() == wanted:
                # the HTML parser keeps the first of two attributes with one name
                if attr.group(2) is not None:
                    targets.append((match.start(), "html", _html_attr_value(attr.group(2))))
                break
    for match in _BARE_URL.finditer(line):
        if any(start <= match.start() < end for start, end in masked + link_text):
            continue
        url = _gfm_trim(match.group(1))
        if url.lower().startswith("www."):
            url = "http://" + url
        targets.append((match.start(), "bare", url))
    return [(kind, target) for _, kind, target in sorted(targets, key=lambda item: item[0])]


def definition_at(lines, index):
    """(destination, lines used) when lines[index] opens a link reference definition, else None."""
    match = _DEFINITION.match(lines[index])
    if match is None or not match.group(1).strip():
        return None
    rest, used = match.group(2), 1
    if not rest.strip():
        if index + 1 >= len(lines) or not lines[index + 1].strip():
            return None
        rest, used = lines[index + 1], 2
    i = _skip_space(rest, 0)
    parsed = _parse_destination(rest, i)
    if parsed is None:
        return None
    dest, j = parsed
    k = _skip_space(rest, j)
    if k < len(rest):
        if k == j:
            return None
        end = _parse_title(rest, k)
        if end is None or _skip_space(rest, end) < len(rest):
            return None
    return dest, used


# ---------------------------------------------------------------------------------------------------
# Site trees and _redirects

def own_site_target(url, site_roots):
    """(tree, path) when url is an http or https URL on an own-site host, else None. A host written with
    the trailing root-label dot (aiqt.ai.) names the same host."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if host.endswith("."):
        host = host[:-1]
    if parts.scheme.lower() not in ("http", "https"):
        return None
    tree = site_roots.get(host)
    if tree is None:
        return None
    return tree, unquote(parts.path) or "/"


def _rule_pattern(source, where):
    """(compiled full-match pattern, captured names) for a _redirects source."""
    names, pieces = [], []
    for piece in _PLACEHOLDER.split(source):
        if piece == "*":
            names.append("splat")
            pieces.append("(?P<splat>.*)")
        elif piece.startswith(":") and len(piece) > 1:
            names.append(piece[1:])
            pieces.append("(?P<{}>[^/]+)".format(piece[1:]))
        else:
            pieces.append(re.escape(piece))
    try:
        return re.compile("".join(pieces)), names
    except re.error as exc:
        raise ScanError("{}: source {!r} cannot be matched ({})".format(where, source, exc)) from None


def load_redirect_rules(tree_dir):
    """Every rule in tree_dir/_redirects as (line, source, destination, status, pattern, names), in file
    order (none when the file is absent). A line that is not 'source destination [status]' is malformed."""
    path = tree_dir / "_redirects"
    if not path.exists() and not path.is_symlink():
        return []
    if path.is_symlink() or not path.is_file():
        raise ScanError("{} is not a regular file".format(path))
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ScanError("{} is not UTF-8 ({})".format(path, exc)) from None
    rules = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        where = "{}:{}".format(path, number)
        fields = stripped.split()
        if len(fields) not in (2, 3):
            raise ScanError("{}: a rule is 'source destination [status]', not {!r}".format(where, stripped))
        if len(fields) == 3 and not (fields[2].isascii() and fields[2].isdigit()):
            raise ScanError("{}: status {!r} is not a number".format(where, fields[2]))
        status = int(fields[2]) if len(fields) == 3 else DEFAULT_STATUS
        pattern, names = _rule_pattern(fields[0], where)
        rules.append((number, fields[0], fields[1], status, pattern, names))
    return rules


def file_present(tree_dir, path):
    """True when tree_dir holds a regular file for path: as written, path + .html or path/index.html. A
    host configuration file at the tree's top (HOST_CONFIG_FILES) is not a page, and a path holding a
    NUL names no file."""
    if "\x00" in path:
        return False
    rel = path.lstrip("/")
    if rel == "" or rel.endswith("/"):
        candidates = [tree_dir / rel / "index.html"]
    else:
        candidates = [tree_dir / rel, tree_dir / (rel + ".html"), tree_dir / rel / "index.html"]
    base = tree_dir.resolve()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.parent == base and resolved.name in HOST_CONFIG_FILES:
            continue
        if resolved.is_relative_to(base) and resolved.is_file():
            return True
    return False


class Sites:
    """The site trees of one checkout, each read once."""

    def __init__(self, root, site_roots):
        self.root = root
        self.site_roots = site_roots
        self.trees = {}

    def state(self, tree):
        """(tree_dir, rules), or None when the tree is absent from the checkout."""
        if tree not in self.trees:
            tree_dir = self.root / tree
            if tree_dir.is_dir():
                self.trees[tree] = (tree_dir, load_redirect_rules(tree_dir))
            elif tree_dir.exists() or tree_dir.is_symlink():
                raise ScanError("{} is present but is not a directory".format(tree_dir))
            else:
                self.trees[tree] = None
        return self.trees[tree]

    def missing(self, tree, path, visited=()):
        """None when tree serves path (or the tree is absent), else why it does not."""
        state = self.state(tree)
        if state is None:
            return None
        tree_dir, rules = state
        if file_present(tree_dir, path):
            return None
        for number, source, dest, status, pattern, names in rules:
            match = pattern.fullmatch(path)
            if match is None:
                continue
            rule = "{}/_redirects line {} ({} {} {})".format(tree, number, source, dest, status)
            if status not in REDIRECT_STATUSES and status != REWRITE_STATUS:
                return "{} has status {}, neither a redirect nor a 200 rewrite".format(rule, status)
            carried = set(_DEST_NAME.findall(dest))
            if not set(names) <= carried:
                return ("{} is a catch-all: its destination does not carry what its source captures, so it "
                        "proves no page".format(rule))
            target = _DEST_NAME.sub(
                lambda m: match.group(m.group(1)) if m.group(1) in names else m.group(0), dest)
            try:
                parts = urlsplit(target)
            except ValueError:
                return "{} has a destination that is not a URL".format(rule)
            if parts.scheme or parts.netloc:
                own = own_site_target(target, self.site_roots)
                if status == REWRITE_STATUS:
                    if own is None:
                        return "{} rewrites to another origin, which is not served".format(rule)
                    return ("{} rewrites to an absolute URL; a 200 rewrite counts only to a /path on the same "
                            "site".format(rule))
                if own is None:
                    return None
                next_hop = own
            elif target.startswith("/"):
                next_hop = (tree, unquote(parts.path) or "/")
            else:
                return "{} has a destination that is neither a /path nor an absolute URL".format(rule)
            chain = visited + ((tree, path),)
            if next_hop in chain or len(chain) > MAX_REDIRECT_HOPS:
                return "{} loops".format(rule)
            why = self.missing(next_hop[0], next_hop[1], chain)
            if why is None:
                return None
            return "{} leads to {}, which is missing: {}".format(rule, target, why)
        return "no file, no .html, no index.html, no _redirects rule"


# ---------------------------------------------------------------------------------------------------
# Generated-Markdown sources

def _root_relative(href):
    return href.startswith("/") and not href.startswith("//")


def _roadmap_links(data, base):
    """(href, rendered URL) as tools/gen_roadmap.py render_md renders each: always base + href."""
    stages = data.get("stage")
    if not isinstance(stages, list) or not all(isinstance(stage, dict) for stage in stages):
        raise ScanError("has no [[stage]] array of tables, which gen_roadmap.py reads")
    for stage in stages:
        links = stage.get("links", [])
        if not isinstance(links, list):
            raise ScanError("a stage's links is not an array")
        for link in links:
            if not isinstance(link, dict) or not isinstance(link.get("href"), str):
                raise ScanError("a stage link has no string href")
            href = link["href"]
            if not _root_relative(href):
                raise ScanError("href {!r} does not start with a single '/'; gen_roadmap.py renders base + "
                                "href, so it would render as {!r}".format(href, base + href))
            yield href, base + href


def _disclosure_links(data, base):
    """(href, rendered URL) as tools/gen_disclosure.py render_md renders each: the href itself when it
    starts with https://, else base + href."""
    rows = data.get("row")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ScanError("has no [[row]] array of tables, which gen_disclosure.py reads")
    for row in rows:
        evidence = row.get("evidence")
        if not isinstance(evidence, list):
            raise ScanError("a row's evidence is not an array")
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("href"), str):
                raise ScanError("an evidence entry has no string href")
            href = item["href"]
            if href.startswith("https://"):
                try:
                    host = urlsplit(href).hostname
                except ValueError:
                    host = None
                if not host:
                    raise ScanError("href {!r} is an https URL with no host".format(href))
                yield href, href
            elif _root_relative(href):
                yield href, base + href
            else:
                raise ScanError("href {!r} is neither an https URL nor a single-'/' path; gen_disclosure.py "
                                "would render it as {!r}".format(href, base + href))


# Sources whose generator renders their hrefs into a committed Markdown file: each with the site_base its
# generator uses when the source sets none (tools/gen_roadmap.py render_md and tools/gen_disclosure.py
# DEFAULT_BASE) and the reader that yields each href with the URL that generator renders from it.
GENERATED_MD_SOURCES = {
    "roadmap.toml": ("https://aiqt.ai", _roadmap_links),
    "disclosure.toml": ("https://aiqt.ai", _disclosure_links),
}


def _check_base(base):
    """Raise ScanError unless base is an http or https origin that base + '/path' renders correctly."""
    if not isinstance(base, str):
        raise ScanError("site_base is not a string")
    try:
        parts = urlsplit(base)
        good = parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
    except ValueError:
        good = False
    if not good or base.endswith("/") or "?" in base or "#" in base:
        raise ScanError("site_base {!r} is not an http or https origin without a trailing '/', query or "
                        "fragment, so base + '/path' would render wrongly".format(base))


# ---------------------------------------------------------------------------------------------------
# Scan

def scan(root, site_roots=None, sources=None, allowlist=None):
    """Return (findings, notes) for the tree at root. Raises OSError or ScanError when it cannot read it."""
    site_roots = SITE_ROOTS if site_roots is None else site_roots
    sources = GENERATED_MD_SOURCES if sources is None else sources
    allowlist = PENDING_DECISION_ALLOWLIST if allowlist is None else allowlist
    findings, notes = [], []
    sites = Sites(root, site_roots)
    root_dir = root.resolve()

    def check_own_site(where, shown, url):
        target = own_site_target(url, site_roots)
        if target is None:
            return False
        tree, path = target
        why = sites.missing(tree, path)
        if why is None:
            return True
        if url in allowlist:
            notes.append("{}: temporarily allowlisted pending decision ({}) -> {}".format(
                where, allowlist[url], shown))
            return True
        findings.append("{}: broken own-site link -> {} (no page for {} under {}/: {})".format(
            where, shown, path, tree, why))
        return True

    def check_target(where, path, kind, target):
        if check_own_site(where, target, target):
            return
        if kind in ("autolink", "bare", "html") or target.startswith(NOT_RELATIVE):
            return
        clean = unquote(target.split("#", 1)[0])
        if not clean:
            return
        if "\x00" in clean:
            findings.append(f"{where}: broken link -> {target} (a path holding NUL names no file)")
            return
        # a root-relative destination resolves against the repository root, as GitHub renders it, never
        # against the filesystem root; a target outside the repository is not in this checkout
        base = root_dir if clean.startswith("/") else path.parent
        resolved = (base / clean.lstrip("/")).resolve()
        if not resolved.is_relative_to(root_dir) or not resolved.exists():
            findings.append(f"{where}: broken link -> {target}")

    for path in sorted(walk_files(root, SKIP_DIRS, suffixes={".md"})):
        rel = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # a Markdown file that is not utf-8 is a real docs-integrity finding, not a fail-closed
            # read error; keep it a finding (exit 1). An OSError (unreadable) fails closed.
            findings.append(f"{rel}: cannot read as utf-8 (UnicodeDecodeError)")
            continue
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            number = index + 1
            definition = definition_at(lines, index)
            if definition is not None:
                dest, used = definition
                if dest:
                    check_target(f"{rel}:{number}", path, "inline", dest)
                index += used
                continue
            for kind, target in line_targets(lines[index]):
                check_target(f"{rel}:{number}", path, kind, target)
            index += 1

    for name, (default_base, links) in sources.items():
        source = root / name
        if not source.exists():
            continue
        try:
            text = source.read_text(encoding="utf-8")
            data = tomllib.loads(text)
        except (ValueError, RecursionError, MemoryError) as exc:
            # ValueError covers tomllib.TOMLDecodeError and UnicodeDecodeError; a nesting bomb raises
            # RecursionError and a huge document MemoryError. Each is cannot-evaluate (exit 2).
            raise ScanError("{} cannot be parsed ({}: {})".format(name, type(exc).__name__, exc)) from None
        try:
            base = data.get("site_base", default_base)
            _check_base(base)
            rendered = list(links(data, base))
        except ScanError as exc:
            raise ScanError("{}: {}".format(name, exc)) from None
        lines = text.splitlines()
        cursor = 0
        for href, url in rendered:
            number = next((i + 1 for i in range(cursor, len(lines)) if '"{}"'.format(href) in lines[i]
                           or "'{}'".format(href) in lines[i]), 0)
            cursor = number if number else cursor
            where = "{}:{}".format(name, number) if number else name
            shown = href if url == href else "{} (renders as {})".format(href, url)
            check_own_site(where, shown, url)

    for url, label in sorted(allowlist.items()):
        target = own_site_target(url, site_roots)
        if target is None:
            continue
        if sites.state(target[0]) is not None and sites.missing(target[0], target[1]) is None:
            findings.append("allowlist entry {} ({}) is stale: the page now exists under {}/; remove the "
                            "entry".format(label, url, target[0]))
    return findings, notes


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        findings, notes = scan(root)
    except (OSError, ScanError) as exc:
        print(f"error: cannot scan the tree ({exc}); fail-closed", file=sys.stderr)
        return 2
    for note in notes:
        print(f"NOTE: {note}")
    if findings:
        print(f"FAIL: {len(findings)} broken link(s)")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print("PASS: all internal Markdown links and own-site links resolve")
    return 0


# Fixture vectors: (name, {relative path: content or None for a directory}, expected exit, substrings the
# output must hold). Each runs the script under test as a child process inside its own fixture tree, so
# the production constants (hosts, sources, allowlist) are what is exercised.
_SITE = {"site/index.html": "<title>t</title>"}
_DISCLOSURE = 'site_base = "https://aiqt.ai"\n[[row]]\nevidence = [{{ text = "x", href = "{}" }}]\n'
_ROADMAP = '[[stage]]\nlinks = [{{ text = "x", href = "{}" }}]\n'
SELF_TEST_VECTORS = (
    ("relative/present-passes", {"README.md": "[a](docs/a.md)\n", "docs/a.md": "x\n"}, 0, ()),
    ("relative/missing-fails", {"README.md": "[a](docs/none.md)\n"}, 1, ("broken link -> docs/none.md",)),
    ("relative/root-relative-in-repo-passes", {"README.md": "x\n", "docs/x.md": "[a](/README.md) [b](/docs/x.md)\n"},
     0, ()),
    ("relative/root-relative-or-outside-repo-fails", {"docs/x.md": "[a](/etc) [b](/usr)\n[c](" + "../" * 40
     + "etc)\n"}, 1, ("docs/x.md:1: broken link -> /etc\n", "docs/x.md:1: broken link -> /usr\n",
                      "docs/x.md:2: broken link -> ../../", "FAIL: 3 broken link(s)")),
    ("own-site/missing-page-fails", {**_SITE, "README.md": "[t](https://aiqt.ai/missing)\n"}, 1,
     ("README.md:1: broken own-site link -> https://aiqt.ai/missing", "no page for /missing under site/",
      "FAIL: 1 broken link(s)")),
    ("own-site/www-host-missing-fails", {**_SITE, "README.md": "[t](https://www.aiqt.ai/missing)\n"}, 1,
     ("broken own-site link -> https://www.aiqt.ai/missing",)),
    ("own-site/present-page-passes", {**_SITE, "site/present.html": "p", "README.md":
     "[a](https://aiqt.ai/present) [b](https://aiqt.ai/present.html) [c](https://aiqt.ai/) "
     "[d](https://aiqt.ai/present#frag) [e](https://aiqt.ai./present)\n"}, 0, ()),
    ("own-site/trailing-dot-host-missing-fails", {**_SITE, "README.md": "[a](https://aiqt.ai./nope)\n"}, 1,
     ("broken own-site link -> https://aiqt.ai./nope (no page for /nope under site/",)),
    ("own-site/nul-path-fails", {**_SITE, "site/_redirects": "/r /%00 301\n", "README.md":
     "[a](https://aiqt.ai/%00) [b](docs/x%00.md) [c](https://aiqt.ai/r)\n"}, 1,
     ("broken own-site link -> https://aiqt.ai/%00 (", "broken link -> docs/x%00.md (a path holding NUL",
      "leads to /%00, which is missing", "FAIL: 3 broken link(s)")),
    ("own-site/host-config-file-not-a-page-fails", {**_SITE, "site/_headers": "/*\n  X: y\n",
     "site/_redirects": "# none\n", "README.md": "[a](https://aiqt.ai/_headers) [b](https://aiqt.ai/_redirects)\n"},
     1, ("no page for /_headers under site/", "no page for /_redirects under site/")),
    ("own-site/nested-underscore-file-passes", {**_SITE, "site/sub/_headers": "h", "site/_headers": "h",
     "README.md": "[a](https://aiqt.ai/sub/_headers)\n"}, 0, ()),
    ("tree/site-not-a-directory-cannot-evaluate", {"site": "not a tree", "README.md": "[a](https://aiqt.ai/x)\n"},
     2, ("is present but is not a directory", "fail-closed")),
    ("own-site/directory-index-passes", {**_SITE, "site/dir/index.html": "d", "README.md":
     "[a](https://aiqt.ai/dir/) [b](https://aiqt.ai/dir)\n"}, 0, ()),
    ("own-site/directory-without-index-fails", {**_SITE, "site/bare/other.html": "o", "README.md":
     "[a](https://aiqt.ai/bare/)\n"}, 1, ("no page for /bare/ under site/",)),
    ("own-site/escape-out-of-tree-fails", {**_SITE, "README.md": "[a](https://aiqt.ai/../README.md)\n"}, 1,
     ("broken own-site link",)),
    ("external/not-checked", {**_SITE, "README.md": "[a](https://example.com/missing) "
     "[b](https://notaiqt.ai/missing) [c](https://aiqt.ai.example.com/missing) [d](mailto:x@example.com) "
     "<https://example.com/missing> https://example.com/missing <x@example.com>\n"}, 0, ()),
    ("opfiles/served-by-opf-site-only-fails", {**_SITE, "site/model.html": "m", "opf/site/index.html": "i",
     "README.md": "[a](https://opfiles.ai/model)\n"}, 1, ("no page for /model under opf/site/",)),
    ("opfiles/present-in-opf-site-passes", {**_SITE, "opf/site/model/index.html": "m", "README.md":
     "[a](https://opfiles.ai/model/) [b](https://www.opfiles.ai/model)\n"}, 0, ()),
    ("tree-absent/not-checked", {"README.md": "[a](https://aiqt.ai/missing) [b](https://opfiles.ai/x)\n"},
     0, ()),
    # (1) a destination is read as CommonMark reads it: balanced parentheses, <...>, escapes and a title.
    ("destination/balanced-parens-present-passes", {**_SITE, "site/guide(v1).html": "g", "docs/a b.md": "x",
     "docs/a(1).md": "x", "README.md": "[Guide](https://aiqt.ai/guide(v1)) [b](docs/a(1).md) "
     "[c](<docs/a b.md>) [d](https://aiqt.ai/guide\\(v1\\) \"Title\") [e](<https://aiqt.ai/guide(v1)> 'T')\n"},
     0, ()),
    ("destination/balanced-parens-missing-fails", {**_SITE, "site/guide(v1).html": "g", "README.md":
     "[Guide](https://aiqt.ai/guide(v2)) [b](docs/a(2).md)\n"}, 1,
     ("broken own-site link -> https://aiqt.ai/guide(v2) (", "broken link -> docs/a(2).md\n")),
    # (2) a _redirects rule counts only when it redirects or rewrites to a destination that resolves.
    ("redirects/counting-rules-pass", {**_SITE, "site/present.html": "p", "site/news/x/y.html": "n",
     "site/people/7.html": "p", "site/_redirects": "# comment\n/old /index.html 301\n"
     "/blog/* /news/:splat 301\n/u/:id /people/:id 308\n/r /present 200\n/two /old 302\n"
     "/ext https://example.com/x 301\n/plain /present\n/frag /present#a 303\n/abs https://aiqt.ai/present 301\n",
     "README.md": "[a](https://aiqt.ai/old) [b](https://aiqt.ai/blog/x/y) [c](https://aiqt.ai/u/7) "
     "[d](https://aiqt.ai/r) [e](https://aiqt.ai/two) [f](https://aiqt.ai/ext) [g](https://aiqt.ai/plain) "
     "[h](https://aiqt.ai/frag) [i](https://aiqt.ai/abs)\n"}, 0, ()),
    ("redirects/rewrite-to-absolute-own-site-fails", {**_SITE, "opf/site/model.html": "m", "site/_redirects":
     "/m https://opfiles.ai/model 200\n/n https://aiqt.ai/index.html 200\n", "README.md":
     "[a](https://aiqt.ai/m) [b](https://aiqt.ai/n)\n"}, 1,
     ("(/m https://opfiles.ai/model 200) rewrites to an absolute URL",
      "(/n https://aiqt.ai/index.html 200) rewrites to an absolute URL", "FAIL: 2 broken link(s)")),
    ("redirects/dangling-symlink-cannot-evaluate", {**_SITE, "site/_redirects": ("symlink", "nowhere"),
     "README.md": "[a](https://aiqt.ai/)\n"}, 2, ("_redirects is not a regular file", "fail-closed")),
    ("redirects/rule-not-a-prefix-fails", {**_SITE, "site/_redirects": "/old /index.html 301\n",
     "README.md": "[a](https://aiqt.ai/older)\n"}, 1, ("no page for /older under site/",)),
    ("redirects/non-redirect-status-fails", {**_SITE, "site/_redirects": "/gone /index.html 404\n",
     "README.md": "[a](https://aiqt.ai/gone)\n"}, 1, ("has status 404, neither a redirect nor a 200 rewrite",)),
    ("redirects/missing-destination-fails", {**_SITE, "site/_redirects": "/old /also-missing 301\n"
     "/r /nothing 200\n", "README.md": "[a](https://aiqt.ai/old) [b](https://aiqt.ai/r)\n"}, 1,
     ("leads to /also-missing, which is missing", "leads to /nothing, which is missing")),
    ("redirects/splat-destination-missing-fails", {**_SITE, "site/_redirects": "/blog/* /news/:splat 301\n",
     "README.md": "[a](https://aiqt.ai/blog/zzz)\n"}, 1, ("leads to /news/zzz, which is missing",)),
    ("redirects/loop-fails", {**_SITE, "site/_redirects": "/a /b 301\n/b /a 302\n/self /self 301\n",
     "README.md": "[a](https://aiqt.ai/a) [b](https://aiqt.ai/self)\n"}, 1,
     ("site/_redirects line 2 (/b /a 302) loops", "site/_redirects line 3 (/self /self 301) loops")),
    ("redirects/splat-catch-all-fails", {**_SITE, "site/_redirects": "/* /index.html 200\n", "README.md":
     "[a](https://aiqt.ai/nope)\n"}, 1, ("(/* /index.html 200) is a catch-all",)),
    ("redirects/rewrite-to-another-origin-fails", {**_SITE, "site/_redirects": "/p https://example.com/ 200\n",
     "README.md": "[a](https://aiqt.ai/p)\n"}, 1, ("rewrites to another origin",)),
    ("redirects/malformed-rule-cannot-evaluate", {**_SITE, "site/_redirects": "/x /y moved\n",
     "README.md": "x\n"}, 2, ("status 'moved' is not a number",)),
    # (3) autolinks, reference-style links and images, inline images and bare URLs are read.
    ("forms/present-pass", {**_SITE, "site/present.html": "p", "docs/i.png": "i", "README.md":
     "<https://aiqt.ai/present> [r][ref] ![i][img] [s] ![j](docs/i.png) [![k](docs/i.png)](https://aiqt.ai/)\n"
     "See https://aiqt.ai/present. (also www.aiqt.ai/present), https://opfiles.ai/x is absent here.\n"
     "| a |https://aiqt.ai/present|\n"
     "\n[ref]: https://aiqt.ai/present \"Title\"\n[img]: <docs/i.png>\n[s]:\n  https://aiqt.ai/present\n"},
     0, ()),
    ("forms/table-cell-bare-url-missing-fails", {**_SITE, "README.md": "| a |https://aiqt.ai/nope|\n"}, 1,
     ("README.md:1: broken own-site link -> https://aiqt.ai/nope (", "FAIL: 1 broken link(s)")),
    ("forms/url-in-link-text-passes", {**_SITE, "site/present.html": "p", "README.md":
     "[see https://aiqt.ai/present](https://aiqt.ai/present) [www.aiqt.ai/x](https://aiqt.ai/present)\n"},
     0, ()),
    ("forms/url-in-link-text-destination-missing-fails", {**_SITE, "site/present.html": "p", "README.md":
     "[see https://aiqt.ai/present](https://aiqt.ai/nope)\n"}, 1,
     ("README.md:1: broken own-site link -> https://aiqt.ai/nope (", "FAIL: 1 broken link(s)")),
    # (5) entity and numeric character references are decoded in a destination, an autolink and a
    # reference definition before the URL is classified, as cmark decodes them.
    ("charref/decoded-present-passes", {**_SITE, "site/present.html": "p", "site/a&b.html": "a",
     "docs/a.md": "x", "README.md": "[a](https://aiqt.ai/pre&#115;ent) [b](<https://aiqt.ai/pre&#x73;ent>) "
     "<https://aiqt.ai/pre&#115;ent> [c](https://aiqt.ai/a&amp;b) [d](docs/a&#46;md) [r]\n"
     "\n[r]: https://aiqt&#x2E;ai/pre&#115;ent\n"}, 0, ()),
    ("charref/encoded-host-missing-fails", {**_SITE, "README.md": "[a](https://aiqt&#46;ai/missing) "
     "<https://aiqt&period;ai/gone> [r]\n\n[r]: https://www&#46;aiqt.ai/def\n"}, 1,
     ("README.md:1: broken own-site link -> https://aiqt.ai/missing (",
      "README.md:1: broken own-site link -> https://aiqt.ai/gone (",
      "README.md:3: broken own-site link -> https://www.aiqt.ai/def (", "FAIL: 3 broken link(s)")),
    ("charref/escaped-or-unterminated-not-decoded-fails", {**_SITE, "site/present.html": "p", "README.md":
     "[a](https://aiqt.ai/pre\\&#115;ent) [b](https://aiqt.ai/pre&#115ent) [c](https://aiqt.ai/pre&nosuch;)\n"},
     1, ("-> https://aiqt.ai/pre&#115;ent (no page for /pre& under", "-> https://aiqt.ai/pre&#115ent (",
         "-> https://aiqt.ai/pre&nosuch; (", "FAIL: 3 broken link(s)")),
    # (6) an a element's href and an img element's src in raw HTML are read; only an own-site URL is checked.
    ("html/missing-fails", {**_SITE, "opf/site/index.html": "i", "README.md":
     "<a href=\"https://aiqt.ai/nope\">x</a> <img alt='i' src='https://aiqt.ai/nope.png'>\n"
     "<A CLASS=x HREF=https://opfiles.ai/gone>y</A> <a href=\"https://aiqt&#46;ai/enc\">z</a>\n"}, 1,
     ("README.md:1: broken own-site link -> https://aiqt.ai/nope (",
      "README.md:1: broken own-site link -> https://aiqt.ai/nope.png (",
      "README.md:2: broken own-site link -> https://opfiles.ai/gone (",
      "README.md:2: broken own-site link -> https://aiqt.ai/enc (", "FAIL: 4 broken link(s)")),
    ("html/present-passes", {**_SITE, "site/present.html": "p", "site/i.png": "i", "site/a&b.html": "a",
     "README.md": "<a class=\"btn\" href=\"https://aiqt.ai/present\">p</a> <img alt=\"i\" src=\"https://aiqt.ai/i.png\" />"
     " <a href=\"/install\">site path</a> <a href=\"https://example.com/x\">e</a>\n"
     "<a title=\"t\" href=\" https://aiqt.ai/pre&#115;ent \" href=\"https://aiqt.ai/nope\">first wins</a>"
     " <a href='https://aiqt.ai/a&amp;b'>amp</a> <abbr title=\"https://aiqt.ai/nope\">not a or img</abbr>\n"},
     0, ()),
    ("forms/autolink-missing-fails", {**_SITE, "README.md": "<https://aiqt.ai/nope>\n"}, 1,
     ("README.md:1: broken own-site link -> https://aiqt.ai/nope (", "FAIL: 1 broken link(s)")),
    ("forms/reference-link-missing-fails", {**_SITE, "README.md": "[a][r]\n\n[r]: https://aiqt.ai/nope\n"}, 1,
     ("README.md:3: broken own-site link -> https://aiqt.ai/nope (", "FAIL: 1 broken link(s)")),
    ("forms/reference-image-missing-fails", {"README.md": "![i][img]\n\n[img]: <docs/missing.png> 'T'\n"}, 1,
     ("README.md:3: broken link -> docs/missing.png",)),
    ("forms/inline-image-missing-fails", {"README.md": "![i](docs/none.png)\n"}, 1,
     ("README.md:1: broken link -> docs/none.png",)),
    ("forms/bare-url-missing-fails", {**_SITE, "README.md": "See https://aiqt.ai/nope.\n(www.aiqt.ai/gone)\n"},
     1, ("README.md:1: broken own-site link -> https://aiqt.ai/nope (",
         "README.md:2: broken own-site link -> http://www.aiqt.ai/gone (", "FAIL: 2 broken link(s)")),
    # (4) a generated source's href is joined exactly as its generator renders it.
    ("source/root-relative-href-missing-fails", {**_SITE, "roadmap.toml":
     'title = "T"\n[[stage]]\nlinks = [{ text = "x", href = "/gone" }]\n'}, 1,
     ("roadmap.toml:3: broken own-site link -> /gone (renders as https://aiqt.ai/gone)",)),
    ("source/root-relative-href-present-passes", {**_SITE, "site/here.html": "h", "roadmap.toml":
     _ROADMAP.format("/here#a") + 'other = { href = "not rendered" }\n', "disclosure.toml":
     'site_base = "https://aiqt.ai"\n[[row]]\nevidence = [{ text = "x", href = "/here#a" },'
     ' { text = "y", href = "https://example.com/z" }, { text = "z", href = "https://aiqt.ai/here" }]\n'},
     0, ()),
    ("source/roadmap-relative-href-malformed", {**_SITE, "roadmap.toml": _ROADMAP.format("nope")}, 2,
     ("href 'nope' does not start with a single '/'", "'https://aiqt.ainope'")),
    ("source/roadmap-absolute-href-malformed", {**_SITE, "roadmap.toml": _ROADMAP.format("https://aiqt.ai/")},
     2, ("'https://aiqt.aihttps://aiqt.ai/'",)),
    ("source/roadmap-non-string-href-malformed", {**_SITE, "roadmap.toml":
     '[[stage]]\nlinks = [{ text = "x", href = 5 }]\n'}, 2, ("a stage link has no string href",)),
    ("source/site-base-trailing-slash-malformed", {**_SITE, "roadmap.toml":
     'site_base = "https://aiqt.ai/"\n' + _ROADMAP.format("/x")}, 2, ("site_base 'https://aiqt.ai/'",)),
    ("source/disclosure-relative-href-malformed", {**_SITE, "disclosure.toml": _DISCLOSURE.format("here")}, 2,
     ("href 'here' is neither an https URL nor a single-'/' path",)),
    ("source/disclosure-http-href-malformed", {**_SITE, "disclosure.toml":
     _DISCLOSURE.format("http://aiqt.ai/")}, 2, ("href 'http://aiqt.ai/' is neither",)),
    ("source/malformed-cannot-evaluate", {**_SITE, "roadmap.toml": "title = \n"}, 2, ()),
    ("allowlist/exact-url-honoured", {**_SITE, "README.md": "[t](https://aiqt.ai/teams)\n", "roadmap.toml":
     '[[stage]]\nlinks = [{ text = "t", href = "/teams" }]\n'}, 0,
     ("temporarily allowlisted pending decision (roadmap-teams-link) -> https://aiqt.ai/teams",
      "roadmap.toml:2: temporarily allowlisted pending decision (roadmap-teams-link) -> /teams")),
    ("allowlist/trailing-slash-not-honoured", {**_SITE, "README.md": "[t](https://aiqt.ai/teams/)\n"}, 1,
     ("broken own-site link -> https://aiqt.ai/teams/",)),
    ("allowlist/other-host-not-honoured", {**_SITE, "README.md": "[t](https://www.aiqt.ai/teams)\n"}, 1,
     ("broken own-site link -> https://www.aiqt.ai/teams",)),
    ("allowlist/fragment-not-honoured", {**_SITE, "README.md": "[t](https://aiqt.ai/teams#x)\n"}, 1,
     ("broken own-site link -> https://aiqt.ai/teams#x",)),
    ("allowlist/other-scheme-not-honoured", {**_SITE, "README.md": "[t](http://aiqt.ai/teams)\n"}, 1,
     ("broken own-site link -> http://aiqt.ai/teams",)),
    ("allowlist/stale-entry-fails", {**_SITE, "site/teams.html": "t"}, 1,
     ("allowlist entry roadmap-teams-link (https://aiqt.ai/teams) is stale",)),
    ("allowlist/catch-all-does-not-make-it-stale", {**_SITE, "site/_redirects": "/* /index.html 200\n"}, 0,
     ()),
)


def self_test(script=None):
    """Run SELF_TEST_VECTORS against script (default: this file). Returns the process exit code."""
    import shutil
    import subprocess
    import tempfile
    script = Path(__file__).resolve() if script is None else Path(script)
    walk = Path(__file__).resolve().parent / "_walk.py"
    env = {k: v for k, v in os.environ.items() if k != "AIQT_SITE_HOST"}
    failures = []
    for name, files, want_exit, want_text in SELF_TEST_VECTORS:
        with tempfile.TemporaryDirectory(prefix="check_links_selftest_") as tmp:
            root = Path(tmp)
            (root / "tools").mkdir()
            shutil.copyfile(script, root / "tools" / "check_links.py")
            shutil.copyfile(walk, root / "tools" / "_walk.py")
            for rel, content in files.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                if isinstance(content, tuple):
                    # ("symlink", target): a symbolic link to target, which need not exist
                    (root / rel).symlink_to(content[1])
                else:
                    (root / rel).write_text(content, encoding="utf-8")
            proc = subprocess.run([sys.executable, "-I", "-B", str(root / "tools" / "check_links.py")],
                                  cwd=root, env=env, capture_output=True, text=True, timeout=120)
            out = proc.stdout + proc.stderr
            missing = [text for text in want_text if text not in out]
            if proc.returncode != want_exit or missing:
                failures.append("{}: expected exit {} got {}; missing {}; output {!r}".format(
                    name, want_exit, proc.returncode, missing, out))
    if PENDING_DECISION_ALLOWLIST != {"https://aiqt.ai/teams": "roadmap-teams-link"}:
        failures.append("allowlist/pinned: PENDING_DECISION_ALLOWLIST changed without updating the self-test")
    if failures:
        print("FAIL: check_links self-test")
        for failure in failures:
            print("  " + failure)
        return 1
    print("PASS: check_links self-test ({} fixture vectors)".format(len(SELF_TEST_VECTORS)))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    if sys.argv[1:]:
        print("usage: check_links.py [--self-test]", file=sys.stderr)
        sys.exit(2)
    sys.exit(main())
