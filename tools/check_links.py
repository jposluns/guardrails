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
  attributes (srcset, source, link, area and the rest) are not read. An http or https URL or a
  scheme-less one there is read as the WHATWG URL parser reads it: before any ? or #, a backslash is a
  slash, and two or more slashes after the scheme or at the start introduce a host (so /\\aiqt.ai/x and
  \\\\aiqt.ai/x name the host aiqt.ai). In an inline link or reference definition a backslash is kept as
  written, so /\\aiqt.ai/x there is a repository path and is checked as one.

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
   are served at (a protocol-relative destination //host/path in an inline link, image, reference
   definition or raw HTML attribute is read as https://host/path, as a browser resolves it on an https
   page, and is never resolved as a repository path; one on another host is external), or an href in a generated-Markdown source (GENERATED_MD_SOURCES), joined exactly as
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
   written with its trailing root-label dot, such as aiqt.ai., is the same host, and a percent-encoded
   host such as %61iqt.ai is decoded first, as a browser decodes it), and the URL path must reach a page
   of that tree. The path is first read as a browser sends it: dot segments (. and .., or a spelling
   with %2e) are removed exactly as the WHATWG URL parser removes them before the request is sent, with
   every empty segment kept (.. drops the segment before it even when that segment is empty, so
   /x//../present is /x/present, and .. at the top stays at /). A double slash that is still in the path
   after that (an empty segment before the last one, as in //present or /x//present) is refused as
   unconfirmed against the host, the stricter reading: it names no page, and the link fails whatever file
   or rule exists. How Cloudflare Pages serves a path holding a double slash (merging it, matching rules
   against it as sent, or neither) is unconfirmed against the host (its redirects page,
   developers.cloudflare.com/pages/configuration/redirects/, read 2026-11-15, does not say); every
   reading but this one can pass a page that is missing under another, and this one can fail a link the
   host would serve. The same holds for an encoded slash or backslash (%2F or %5C, in any letter case, as
   in /x%2Fpresent or /x%2F..%2Fpresent): the browser sends it as written and whether the host decodes
   it is unconfirmed, so no file and no rule is taken to serve it (it is kept encoded while the rest of
   the path is decoded, so a %2F that only decoding %252F produces is refused too). A host that is not ASCII is mapped through
   Python's IDNA codec first (aiqt.ai spelled with a full-width a, U+FF41, is aiqt.ai). The tree's _redirects is evaluated
   first, as Cloudflare Pages applies a
   rule even when a static asset matches the path: when a rule's source matches the path (exactly,
   through a trailing * splat or through a /:name placeholder), the first such rule in file order
   decides, and the path passes only when that rule counts, whether or not a file exists at the path.
   Only when no rule matches does a file decide: a regular file inside the tree at the path as written,
   at path + ".html", or at path/index.html ("/" is index.html), never a host configuration file at the
   tree's top (HOST_CONFIG_FILES: _headers and _redirects). Where Cloudflare Pages serves that file at
   another path (a link to /a that a/index.html serves is served at /a/, a link to /a.html at /a and a
   link to /a/index.html at /a/), the rules for that path are evaluated too, as for a further hop of a
   chain; that Pages redirects those spellings is not verified here, and evaluating them only adds
   findings, which is the stricter reading.
   First match in file order is the order this gate assumes Cloudflare Pages applies rules in; that is
   not verified against its documentation here. A rule counts only when its status is a redirect (301, 302,
   303, 307 or 308; a rule with no status is a 302) or a 200 rewrite, and its destination itself
   resolves. A destination is read as a browser reads the Location it is sent (a backslash and extra
   slashes as in raw HTML above, dot segments removed, and a protocol-relative //host/path read as
   https://host/path, so an own-site one is checked and not taken as external).
   A redirect sends the browser to a new request, so its destination path or own-site URL resolves by
   these same rules, the destination's own rules included (a chain that returns to a path it has
   visited loops, one that runs past MAX_REDIRECT_HOPS hops ends at the hop limit, and neither counts);
   an external destination counts for a redirect (whether it is live is out of scope) only when it is an
   http or https URL with a host: a destination with another scheme (mailto:, javascript:) or with a
   scheme and no host (https:/x, https:x) proves no page and does not count.
   A 200 rewrite is applied once, as Cloudflare Pages documents for proxying
   (developers.cloudflare.com/pages/configuration/redirects/: with /a /b 200 and /b /c 200, a request to
   /a renders /b, not /c): the rewritten path is served as a file, so it counts when a file serves that
   path (the file itself, its .html or its index.html, as above), and no rule is evaluated for that
   path or for any other spelling of its file (a rewrite /a /a.html 200 with a.html present counts,
   although Pages serves a.html at /a, and so does /start /a.html 200 beside a rule /a /missing 301).
   It counts only to a /path on the same site, never to an absolute URL, own-site or
   not (whether Cloudflare Pages proxies an absolute URL to its own origin is not verified; this is the
   stricter reading). Splats and placeholders: a rule whose source holds
   a * splat or a :name placeholder counts only when its destination carries every value it captures
   (:splat for the *, :name for each placeholder), and the destination is then resolved with those
   values substituted for the link being checked. A source matches the path exactly as written: a rule
   for /present does not match a link to /present/, a rule for /present/ does not match /present, and
   /blog/* does not match /blog. Whether Cloudflare Pages matches across a trailing slash, or lets a
   splat match the bare prefix, is unconfirmed against the host; if it does, such a link can pass here
   although the host would apply the rule. A rule that sends every match to one fixed destination, such
   as /* /index.html 200, is a catch-all that serves something for any path and proves no page, so it
   never counts and a link it matches fails, even a link to a page that exists, since rules come before
   files. Whether Cloudflare Pages applies such a catch-all ahead of an existing file is unconfirmed
   against the host; failing the link is the stricter reading. A _redirects line that is not
   "source destination [status]" with a numeric status is malformed, and so is a source holding a %
   (whether the host decodes a source before matching it is unconfirmed), a _redirects that starts
   with a byte order mark (whether the host strips it is unconfirmed) or that is a symbolic link
   (dangling or not) or not a regular file: exit 2. A tree absent from the checkout is not
   checked (an adopter vendoring this gate has no site/). A tree is read only when the scan needs it:
   when a checked link, a redirect destination or a PENDING_DECISION_ALLOWLIST entry names a host that
   tree serves. A tree path that is present but is not a directory is exit 2 when it is read that way;
   one that nothing names is not read and does not fail. The fragment and query are not checked;
   tools/check_site.py validates anchors inside the HTML.

Other external URLs, mailto and tel links, and pure anchors are out of scope: this gate answers "does the
target exist in this checkout", not "is the URL live". An autolink, bare URL or raw HTML link is checked
only when it is own-site.

PENDING_DECISION_ALLOWLIST names own-site links knowingly broken while a maintainer decides their fate.
Each entry is temporary, honoured only for its exact resolved URL (in any scanned file, not only the one
that motivated it), and printed on every run as temporary; an entry whose target page now exists is itself
a finding, so it is removed in the change that decides the link. An entry that no scanned link uses is not
flagged, and an entry carries no owner or expiry.

A control character in a printed finding or note (a NUL in a decoded path, for one) is printed as a \\xNN
escape, never raw.

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

# A protocol-relative destination: exactly two slashes, then a host.
_PROTOCOL_RELATIVE = re.compile(r"//[^/\\]")
# A URL scheme and its colon.
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*:")
# WHATWG URL single-dot and double-dot path segments, lowercased (a percent-encoded dot counts).
_SINGLE_DOT = frozenset({".", "%2e"})
_DOUBLE_DOT = frozenset({"..", ".%2e", "%2e.", "%2e%2e"})
# A percent-encoded slash or backslash, in any letter case, captured so re.split keeps it.
_ENCODED_SEPARATOR = re.compile(r"(%(?:2[fF]|5[cC]))")
# A C0 control character or DEL, printed as a \xNN escape.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _printable(text):
    """text with every control character written as a \\xNN escape, so none is printed raw."""
    return _CONTROL.sub(lambda m: "\\x{:02x}".format(ord(m.group())), text)


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

def browser_url(url):
    """url as the WHATWG URL parser reads an http or https URL or a scheme-less reference: before any ?
    or #, a backslash is a slash, and two or more slashes after the scheme or at the start introduce a
    host, so /\\aiqt.ai/x and ///aiqt.ai/x are //aiqt.ai/x. A URL with another scheme is returned as given."""
    cut = min((i for i in (url.find("?"), url.find("#")) if i >= 0), default=len(url))
    head, tail = url[:cut], url[cut:]
    scheme = _SCHEME.match(head)
    if scheme is not None and scheme.group().lower() not in ("http:", "https:"):
        return url
    prefix = scheme.group() if scheme is not None else ""
    rest = head[len(prefix):].replace("\\", "/")
    if rest.startswith("//"):
        rest = "//" + rest.lstrip("/")
    return prefix + rest + tail


def url_path(raw):
    """The decoded path requested for the raw URL path raw: dot segments removed exactly as the WHATWG
    URL parser's path state removes them (a percent-encoded dot counts; .. drops the segment before it,
    even an empty one, and at the top stays at /; a final . or .. leaves a trailing /), empty segments (a
    double slash) kept, then percent-decoded except an encoded slash or backslash (%2F, %5C), which is
    kept as written for unservable_segment to refuse; an empty path is "/". So /x//../present is
    /x/present."""
    segments = raw.split("/")[1:] if raw.startswith("/") else raw.split("/")
    out = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        lowered = segment.lower()
        if lowered in _DOUBLE_DOT:
            if out:
                out.pop()
            if last:
                out.append("")
        elif lowered in _SINGLE_DOT:
            if last:
                out.append("")
        else:
            out.append(segment)
    pieces = _ENCODED_SEPARATOR.split("/" + "/".join(out))
    return "".join(piece if index % 2 else unquote(piece) for index, piece in enumerate(pieces))


def unservable_segment(path):
    """Why the decoded path names no page, or None. An encoded slash or backslash (%2F or %5C, which
    url_path keeps as written), an empty segment (a double slash) before the last one, or a . or ..
    segment names no page: how Cloudflare Pages serves such a path is unconfirmed against the host, so
    no file and no rule is taken to serve it, the reading that cannot pass a missing page."""
    if _ENCODED_SEPARATOR.search(path):
        return ("the path holds an encoded slash or backslash (%2F or %5C), which no page is taken to "
                "serve; whether the host decodes one is unconfirmed")
    segments = path.split("/")[1:]
    if any(segment in (".", "..") for segment in segments):
        return "the decoded path holds a . or .. segment, which no page is taken to serve"
    if any(segment == "" for segment in segments[:-1]):
        return ("the path holds an empty segment (a double slash), which no page is taken to serve; how "
                "the host serves one is unconfirmed")
    return None


def own_site_target(url, site_roots):
    """(tree, path) when url is an http or https URL on an own-site host, else None. A host written with
    the trailing root-label dot (aiqt.ai.) names the same host, and a percent-encoded host is decoded
    first, as a browser decodes it. A host that is not ASCII is mapped through Python's IDNA codec
    (nameprep, so aiqt.ai with a full-width a, U+FF41, is aiqt.ai, as a browser's IDNA mapping reads
    it); a host the codec rejects is left as written. The path is url_path's."""
    try:
        parts = urlsplit(url)
        host = unquote(parts.hostname or "").lower()
    except ValueError:
        return None
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            pass
    if host.endswith("."):
        host = host[:-1]
    if parts.scheme.lower() not in ("http", "https"):
        return None
    tree = site_roots.get(host)
    if tree is None:
        return None
    return tree, url_path(parts.path)


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
    if text.startswith("\ufeff"):
        # whether Cloudflare Pages strips a byte order mark or reads it into the first rule's source is
        # unconfirmed, and either reading can pass a link the other fails: cannot evaluate
        raise ScanError("{} starts with a byte order mark (U+FEFF)".format(path))
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
        if "%" in fields[0]:
            # whether Cloudflare Pages decodes a source before matching it is unconfirmed, and either
            # reading can pass a link the other fails: cannot evaluate
            raise ScanError("{}: source {!r} holds a %; whether the host decodes a source before matching "
                            "it is unconfirmed".format(where, fields[0]))
        pattern, names = _rule_pattern(fields[0], where)
        rules.append((number, fields[0], fields[1], status, pattern, names))
    return rules


def served_path(tree_dir, path):
    """The path Cloudflare Pages serves path's file at, or None when tree_dir holds no regular file for
    path (as written, path + .html or path/index.html). A file named as written that ends in index.html
    is served at its directory and one that ends in .html without it; a file found at path/index.html
    for a path without a trailing / is served at path + "/" (assumed, not verified). A host
    configuration file at the tree's top (HOST_CONFIG_FILES) is not a page, and a path holding a NUL
    names no file, nor does one unservable_segment rejects."""
    if "\x00" in path or unservable_segment(path) is not None:
        return None
    rel = path.lstrip("/")
    if rel == "" or rel.endswith("/"):
        candidates = [(tree_dir / rel / "index.html", path)]
    else:
        if path.endswith("/index.html"):
            written = path[:-len("index.html")]
        elif path.endswith(".html"):
            written = path[:-len(".html")]
        else:
            written = path
        candidates = [(tree_dir / rel, written), (tree_dir / (rel + ".html"), path),
                      (tree_dir / rel / "index.html", path + "/")]
    base = tree_dir.resolve()
    for candidate, served in candidates:
        resolved = candidate.resolve()
        if resolved.parent == base and resolved.name in HOST_CONFIG_FILES:
            continue
        if resolved.is_relative_to(base) and resolved.is_file():
            return served
    return None


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
        """None when tree serves path (or the tree is absent), else why it does not. The _redirects rules
        are evaluated before any file, as Cloudflare Pages follows a redirect even when an asset matches
        the path: the first rule whose source matches decides. With no matching rule, a file decides, and
        when Pages serves that file at another path, the rules for that path are evaluated too. visited
        holds the (tree, path) hops already taken, so a chain that returns to one loops, one that runs
        past MAX_REDIRECT_HOPS ends at the hop limit, and neither counts."""
        state = self.state(tree)
        if state is None:
            return None
        tree_dir, rules = state
        segment_problem = unservable_segment(path)
        if segment_problem is not None:
            return segment_problem
        chain = visited + ((tree, path),)
        for rule in rules:
            match = rule[4].fullmatch(path)
            if match is not None:
                return self._follow(tree, rule, match, chain)
        served = served_path(tree_dir, path)
        if served is None:
            return "no file, no .html, no index.html, no _redirects rule"
        if served == path:
            return None
        if (tree, served) in chain:
            return "the file for {} is served at {}, which loops".format(path, served)
        if len(chain) > MAX_REDIRECT_HOPS:
            return "the file for {} is served at {}, past the hop limit of {} hops".format(
                path, served, MAX_REDIRECT_HOPS)
        why = self.missing(tree, served, chain)
        if why is None:
            return None
        return "the file for {} is served at {}, which is missing: {}".format(path, served, why)

    def _follow(self, tree, rule, match, chain):
        """None when the matched _redirects rule counts for the last hop of chain, else why it does not.
        A redirect is a new request, so its destination's rules apply; a 200 rewrite is applied once, so
        its destination counts when a file serves it, and no rule is evaluated for the destination or for
        any other spelling of its file."""
        number, source, dest, status, _pattern, names = rule
        rule = "{}/_redirects line {} ({} {} {})".format(tree, number, source, dest, status)
        if status not in REDIRECT_STATUSES and status != REWRITE_STATUS:
            return "{} has status {}, neither a redirect nor a 200 rewrite".format(rule, status)
        carried = set(_DEST_NAME.findall(dest))
        if not set(names) <= carried:
            return ("{} is a catch-all: its destination does not carry what its source captures, so it "
                    "proves no page".format(rule))
        target = _DEST_NAME.sub(
            lambda m: match.group(m.group(1)) if m.group(1) in names else m.group(0), dest)
        url = browser_url(target)
        if _PROTOCOL_RELATIVE.match(url):
            # //host/path names a host; a browser resolves it with the page's scheme (https here)
            url = "https:" + url
        try:
            parts = urlsplit(url)
        except ValueError:
            return "{} has a destination that is not a URL".format(rule)
        if parts.scheme or parts.netloc:
            try:
                host = parts.hostname
            except ValueError:
                host = None
            if parts.scheme.lower() not in ("http", "https") or not host:
                # https:/x or https:x (no host), mailto:, javascript: and the like name no page that can
                # be shown to exist, so they do not count, whatever a browser makes of them
                return ("{} has a destination that is not an http or https URL with a host, so it proves "
                        "no page".format(rule))
            own = own_site_target(url, self.site_roots)
            if status == REWRITE_STATUS:
                if own is None:
                    return "{} rewrites to another origin, which is not served".format(rule)
                return ("{} rewrites to an absolute URL; a 200 rewrite counts only to a /path on the same "
                        "site".format(rule))
            if own is None:
                return None
            next_hop = own
        elif url.startswith("/"):
            next_hop = (tree, url_path(parts.path))
        else:
            return "{} has a destination that is neither a /path nor an absolute URL".format(rule)
        if status == REWRITE_STATUS:
            # applied once: a file that serves the destination (as written, + .html or /index.html) is
            # rendered, and no rule is evaluated for the destination or for any other spelling of its file
            if served_path(self.state(tree)[0], next_hop[1]) is None:
                return ("{} leads to {}, which is missing: no file serves it (a 200 rewrite is applied once, "
                        "so the rules for {} are not evaluated)".format(rule, target, next_hop[1]))
            return None
        if next_hop in chain:
            return "{} loops".format(rule)
        if len(chain) > MAX_REDIRECT_HOPS:
            return "{} passes the hop limit of {} hops".format(rule, MAX_REDIRECT_HOPS)
        why = self.missing(next_hop[0], next_hop[1], chain)
        if why is None:
            return None
        return "{} leads to {}, which is missing: {}".format(rule, target, why)


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
        # a raw HTML attribute value is parsed by the browser's URL parser (a backslash is a slash)
        url = browser_url(target) if kind == "html" else target
        if check_own_site(where, target, url):
            return
        if kind in ("inline", "html") and _PROTOCOL_RELATIVE.match(url):
            # //host/path names a host, not a repository path; a browser resolves it with the page's
            # scheme (https here). Only an own-site one is checked.
            check_own_site(where, target, "https:" + url)
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
        print(f"error: cannot scan the tree ({_printable(str(exc))}); fail-closed", file=sys.stderr)
        return 2
    for note in notes:
        print(f"NOTE: {_printable(note)}")
    if findings:
        print(f"FAIL: {len(findings)} broken link(s)")
        for finding in findings:
            print(f"  {_printable(finding)}")
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
      "leads to /%00, which is missing", "no page for /\\x00 under site/", "FAIL: 3 broken link(s)")),
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
    # (7) a _redirects rule is evaluated before a file at the same path, as Cloudflare Pages follows a
    # redirect even when an asset matches; the path passes only when the rule's final destination resolves.
    ("redirects/rule-over-present-file-missing-destination-fails", {**_SITE, "site/present.html": "p",
     "site/_redirects": "/present /missing 301\n", "README.md": "[a](https://aiqt.ai/present)\n"}, 1,
     ("README.md:1: broken own-site link -> https://aiqt.ai/present (no page for /present under site/: "
      "site/_redirects line 1 (/present /missing 301) leads to /missing, which is missing",
      "FAIL: 1 broken link(s)")),
    ("redirects/rule-over-present-file-present-destination-passes", {**_SITE, "site/present.html": "p",
     "site/exists.html": "e", "site/_redirects": "/present /exists 301\n", "README.md":
     "[a](https://aiqt.ai/present)\n"}, 0, ()),
    ("redirects/chain-through-present-files-missing-fails", {**_SITE, "site/present.html": "p",
     "site/mid.html": "m", "site/a.html": "a", "site/b.html": "b", "site/_redirects":
     "/present /mid 301\n/mid /missing 302\n/a /b 301\n/b /a 301\n", "README.md":
     "[a](https://aiqt.ai/present) [b](https://aiqt.ai/a)\n"}, 1,
     ("(/present /mid 301) leads to /mid, which is missing: site/_redirects line 2 (/mid /missing 302) "
      "leads to /missing", "site/_redirects line 4 (/b /a 301) loops", "FAIL: 2 broken link(s)")),
    ("redirects/chain-through-present-files-passes", {**_SITE, "site/present.html": "p", "site/mid.html": "m",
     "site/exists.html": "e", "site/_redirects": "/present /mid 301\n/mid /exists 302\n", "README.md":
     "[a](https://aiqt.ai/present) [b](https://aiqt.ai/mid)\n"}, 0, ()),
    ("redirects/first-matching-rule-decides-fails", {**_SITE, "site/exists.html": "e", "site/present.html": "p",
     "site/_redirects": "/present /missing 301\n/present /exists 301\n", "README.md":
     "[a](https://aiqt.ai/present)\n"}, 1, ("(/present /missing 301) leads to /missing, which is missing",)),
    ("redirects/rule-on-served-spelling-fails", {**_SITE, "site/present.html": "p", "site/dir/index.html": "d",
     "site/_redirects": "/present /missing 301\n/dir/ /gone 301\n", "README.md":
     "[a](https://aiqt.ai/present.html) [b](https://aiqt.ai/dir) [c](https://aiqt.ai/dir/index.html)\n"}, 1,
     ("-> https://aiqt.ai/present.html (no page for /present.html under site/: the file for /present.html is "
      "served at /present, which is missing: site/_redirects line 1",
      "-> https://aiqt.ai/dir (no page for /dir under site/: the file for /dir is served at /dir/",
      "-> https://aiqt.ai/dir/index.html (", "FAIL: 3 broken link(s)")),
    # (8) a protocol-relative //host/path names a host: an own-site one is checked, another is external.
    ("protocol-relative/own-site-missing-fails", {**_SITE, "README.md":
     "<a href=\"//aiqt.ai/nope\">x</a> <img src=\"//aiqt.ai/nope.png\"> [a](//aiqt.ai/gone) [r]\n"
     "\n[r]: //www.aiqt.ai/def\n"}, 1,
     ("README.md:1: broken own-site link -> //aiqt.ai/nope (", "README.md:1: broken own-site link -> "
      "//aiqt.ai/nope.png (", "README.md:1: broken own-site link -> //aiqt.ai/gone (",
      "README.md:3: broken own-site link -> //www.aiqt.ai/def (", "FAIL: 4 broken link(s)")),
    ("protocol-relative/present-or-external-passes", {**_SITE, "site/present.html": "p", "README.md":
     "[a](//aiqt.ai/present) <a href=\"//aiqt.ai/present\">p</a> [b](//example.com/x) "
     "<a href=\"//example.com/y\">e</a>\n"}, 0, ()),
    # (9) a 200 rewrite is applied once: the rewritten path is served as a file and its rules are not
    # evaluated again; a redirect is a new request, so its destination's rules apply.
    ("redirects/rewrite-applied-once-passes", {**_SITE, "site/b.html": "b", "site/_redirects":
     "/a /b 200\n/b /c 200\n", "README.md": "[a](https://aiqt.ai/a)\n"}, 0, ()),
    ("redirects/rewrite-applied-once-fails", {**_SITE, "site/c.html": "c", "site/_redirects":
     "/a /b 200\n/b /c 200\n", "README.md": "[a](https://aiqt.ai/a)\n"}, 1,
     ("(/a /b 200) leads to /b, which is missing: no file serves it (a 200 rewrite is applied once",
      "FAIL: 1 broken link(s)")),
    ("redirects/redirect-destination-rules-apply-passes", {**_SITE, "site/c.html": "c", "site/o.html": "o",
     "site/_redirects": "/a /b 301\n/b /c 301\n/m /n 302\n/n /o 200\n", "README.md":
     "[a](https://aiqt.ai/a) [m](https://aiqt.ai/m)\n"}, 0, ()),
    ("redirects/redirect-destination-rules-apply-fails", {**_SITE, "site/b.html": "b", "site/n.html": "n",
     "site/_redirects": "/a /b 301\n/b /c 301\n/m /n 302\n/n /o 200\n", "README.md":
     "[a](https://aiqt.ai/a) [m](https://aiqt.ai/m)\n"}, 1,
     ("(/a /b 301) leads to /b, which is missing: site/_redirects line 2 (/b /c 301) leads to /c, which is "
      "missing", "(/m /n 302) leads to /n, which is missing: site/_redirects line 4 (/n /o 200) leads to /o",
      "FAIL: 2 broken link(s)")),
    # (10) a protocol-relative redirect destination names a host: an own-site one is checked.
    ("redirects/protocol-relative-destination-missing-fails", {**_SITE, "site/_redirects":
     "/r //aiqt.ai/missing 301\n/s //opfiles.ai/gone 302\n", "opf/site/index.html": "i", "README.md":
     "[r](https://aiqt.ai/r) [s](https://aiqt.ai/s)\n"}, 1,
     ("(/r //aiqt.ai/missing 301) leads to //aiqt.ai/missing, which is missing",
      "(/s //opfiles.ai/gone 302) leads to //opfiles.ai/gone, which is missing", "FAIL: 2 broken link(s)")),
    ("redirects/protocol-relative-destination-present-passes", {**_SITE, "site/present.html": "p",
     "site/_redirects": "/r //aiqt.ai/present 301\n/x //example.com/y 301\n", "README.md":
     "[r](https://aiqt.ai/r) [x](https://aiqt.ai/x)\n"}, 0, ()),
    # (11) dot segments are removed before rules are matched, so a rule over a present file still decides
    # for every spelling of its path; a double slash left in the path names no page.
    ("own-site/dot-segment-or-double-slash-rule-fails", {**_SITE, "site/present.html": "p", "site/_redirects":
     "/present /missing 301\n/r /x/../present 301\n", "README.md": "[a](https://aiqt.ai/x/../present) "
     "[b](https://aiqt.ai/./present) [c](https://aiqt.ai/x/%2e%2e/present) [d](https://aiqt.ai//present) "
     "[e](https://aiqt.ai/r)\n"}, 1,
     ("-> https://aiqt.ai/x/../present (no page for /present under site/: site/_redirects line 1",
      "-> https://aiqt.ai/./present (no page for /present under site/: site/_redirects line 1",
      "-> https://aiqt.ai/x/%2e%2e/present (no page for /present under site/: site/_redirects line 1",
      "-> https://aiqt.ai//present (no page for //present under site/: the path holds an empty segment",
      "(/r /x/../present 301) leads to /x/../present, which is missing: site/_redirects line 1",
      "FAIL: 5 broken link(s)")),
    ("own-site/dot-segment-or-double-slash-rule-passes", {**_SITE, "site/present.html": "p",
     "site/exists.html": "e", "site/_redirects": "/present /exists 301\n/r /x/../present 301\n", "README.md":
     "[a](https://aiqt.ai/x/../present) [b](https://aiqt.ai/./present) [c](https://aiqt.ai/x/%2e%2e/present) "
     "[e](https://aiqt.ai/r)\n"}, 0, ()),
    # (14) .. drops an empty segment as the WHATWG URL parser drops it: /x//../present is /x/present.
    ("own-site/empty-segment-then-dot-dot-passes", {**_SITE, "site/x/present.html": "p", "README.md":
     "[a](https://aiqt.ai/x//../present)\n"}, 0, ()),
    ("own-site/empty-segment-then-dot-dot-missing-fails", {**_SITE, "site/present.html": "p", "README.md":
     "[a](https://aiqt.ai/x//../present)\n"}, 1,
     ("-> https://aiqt.ai/x//../present (no page for /x/present under site/: no file", "FAIL: 1 broken link(s)")),
    ("own-site/empty-segment-then-dot-dot-rule-fails", {**_SITE, "site/present.html": "p", "site/_redirects":
     "/x/present /missing 301\n", "README.md": "[a](https://aiqt.ai/x//../present)\n"}, 1,
     ("-> https://aiqt.ai/x//../present (no page for /x/present under site/: site/_redirects line 1 "
      "(/x/present /missing 301) leads to /missing, which is missing", "FAIL: 1 broken link(s)")),
    ("own-site/empty-segment-then-dot-dot-rule-passes", {**_SITE, "site/exists.html": "e", "site/_redirects":
     "/x/present /exists 301\n", "README.md": "[a](https://aiqt.ai/x//../present)\n"}, 0, ()),
    # (15) a double slash left after dot segments, or a . or .. segment only percent-decoding produces,
    # names no page, in a link, a redirect destination and a rewrite destination alike.
    ("own-site/double-slash-or-encoded-dot-segment-fails", {**_SITE, "site/present.html": "p",
     "site/x/present.html": "p", "site/_redirects": "/present /missing 301\n/r /x//present 301\n"
     "/w /x//present 200\n", "README.md": "[a](https://aiqt.ai/x//present) [b](https://aiqt.ai/x%2F..%2Fpresent) "
     "[c](https://aiqt.ai/r) [d](https://aiqt.ai/w)\n"}, 1,
     ("-> https://aiqt.ai/x//present (no page for /x//present under site/: the path holds an empty segment",
      "-> https://aiqt.ai/x%2F..%2Fpresent (no page for /x%2F..%2Fpresent under site/: the path holds an "
      "encoded slash or backslash", "(/r /x//present 301) leads to /x//present, which is missing: the path holds an "
      "empty segment", "(/w /x//present 200) leads to /x//present, which is missing: no file serves it",
      "FAIL: 4 broken link(s)")),
    # (20) an encoded slash or backslash with no dot segment names no page either, in a link, a redirect
    # destination and a rewrite destination alike; the same page by plain slashes passes.
    ("own-site/encoded-slash-without-dot-segment-fails", {**_SITE, "site/x/present.html": "p",
     "site/_redirects": "/r /x%2Fpresent 301\n/w /x%5cpresent 200\n", "README.md":
     "[a](https://aiqt.ai/x%2Fpresent) [b](https://aiqt.ai/x%2fpresent) [c](https://aiqt.ai/r) "
     "[d](https://aiqt.ai/w)\n"}, 1,
     ("-> https://aiqt.ai/x%2Fpresent (no page for /x%2Fpresent under site/: the path holds an encoded slash",
      "-> https://aiqt.ai/x%2fpresent (no page for /x%2fpresent under site/: the path holds an encoded slash",
      "(/r /x%2Fpresent 301) leads to /x%2Fpresent, which is missing: the path holds an encoded slash",
      "(/w /x%5cpresent 200) leads to /x%5cpresent, which is missing: no file serves it",
      "FAIL: 4 broken link(s)")),
    ("own-site/encoded-slash-without-dot-segment-plain-passes", {**_SITE, "site/x/present.html": "p",
     "site/x/present/index.html": "i", "site/_redirects": "/r /x/present 301\n/w /x/present 200\n",
     "README.md": "[a](https://aiqt.ai/x/present) [b](https://aiqt.ai/x/present/) [c](https://aiqt.ai/r) "
     "[d](https://aiqt.ai/w) [e](https://aiqt.ai/x/pre%73ent)\n"}, 0, ()),
    # (21) a _redirects source holding a % cannot be evaluated: whether the host decodes it is unconfirmed.
    ("redirects/percent-in-source-cannot-evaluate", {**_SITE, "site/café.html": "c", "site/_redirects":
     "/caf%C3%A9 /missing 301\n", "README.md": "[a](https://aiqt.ai/café)\n"}, 2,
     ("source '/caf%C3%A9' holds a %", "fail-closed")),
    ("redirects/percent-free-source-passes", {**_SITE, "site/café.html": "c", "site/_redirects":
     "/café /index.html 301\n", "README.md": "[a](https://aiqt.ai/café) [b](https://aiqt.ai/caf%C3%A9)\n"},
     0, ()),
    # (16) a redirect destination counts as external only when it is an http or https URL with a host.
    ("redirects/hostless-or-other-scheme-destination-fails", {**_SITE, "site/_redirects":
     "/r https:/aiqt.ai/missing 301\n/s https:aiqt.ai/missing 301\n/m mailto:a@aiqt.ai 301\n"
     "/j javascript:void(0) 302\n", "README.md":
     "[r](https://aiqt.ai/r) [s](https://aiqt.ai/s) [m](https://aiqt.ai/m) [j](https://aiqt.ai/j)\n"}, 1,
     ("(/r https:/aiqt.ai/missing 301) has a destination that is not an http or https URL with a host",
      "(/s https:aiqt.ai/missing 301) has a destination that is not",
      "(/m mailto:a@aiqt.ai 301) has a destination that is not",
      "(/j javascript:void(0) 302) has a destination that is not", "FAIL: 4 broken link(s)")),
    # (17) a _redirects that starts with a byte order mark cannot be evaluated.
    ("redirects/byte-order-mark-cannot-evaluate", {**_SITE, "site/present.html": "p", "site/_redirects":
     "\ufeff/present /missing 301\n", "README.md": "[a](https://aiqt.ai/present)\n"}, 2,
     ("starts with a byte order mark (U+FEFF)", "fail-closed")),
    # (18) a host that is not ASCII is mapped as IDNA maps it: a full-width a (U+FF41) is a.
    ("own-site/full-width-host-missing-fails", {**_SITE, "README.md": "[a](https://\uff41iqt.ai/nope)\n"}, 1,
     ("-> https://\uff41iqt.ai/nope (no page for /nope under site/", "FAIL: 1 broken link(s)")),
    ("own-site/full-width-host-present-passes", {**_SITE, "site/present.html": "p", "README.md":
     "[a](https://\uff41iqt.ai/present)\n"}, 0, ()),
    # (19) a 200 rewrite is applied once: its destination counts when a file serves it, and no rule is
    # evaluated for the destination or for any other spelling of its file (b.html, which Pages serves at /b).
    ("redirects/rewrite-to-own-file-spelling-passes", {**_SITE, "site/a.html": "a", "site/_redirects":
     "/a /a.html 200\n", "README.md": "[a](https://aiqt.ai/a)\n"}, 0, ()),
    ("redirects/rewrite-to-served-spelling-rule-not-evaluated-passes", {**_SITE, "site/a.html": "a",
     "site/_redirects": "/start /a.html 200\n/a /missing 301\n", "README.md": "[s](https://aiqt.ai/start)\n"},
     0, ()),
    ("redirects/rewrite-to-index-or-html-spelling-passes", {**_SITE, "site/b.html": "b", "site/d/index.html": "d",
     "site/_redirects": "/a /b 200\n/b /missing 301\n/c /d/ 200\n/d/ /missing 301\n/e /d 200\n",
     "README.md": "[a](https://aiqt.ai/a) [c](https://aiqt.ai/c) [e](https://aiqt.ai/e)\n"}, 0, ()),
    ("redirects/rewrite-to-path-no-file-serves-fails", {**_SITE, "site/_redirects":
     "/a /a.html 200\n/start /a.html 200\n/b /missing.html 200\n/missing /exists 301\n", "site/exists.html": "e",
     "README.md": "[a](https://aiqt.ai/a) [s](https://aiqt.ai/start) [b](https://aiqt.ai/b)\n"}, 1,
     ("(/a /a.html 200) leads to /a.html, which is missing: no file serves it",
      "(/start /a.html 200) leads to /a.html, which is missing: no file serves it",
      "(/b /missing.html 200) leads to /missing.html, which is missing: no file serves it",
      "FAIL: 3 broken link(s)")),
    ("redirects/rewrite-to-served-spelling-rule-passes", {**_SITE, "site/b.html": "b", "site/c.html": "c",
     "site/_redirects": "/a /b.html 200\n/b /c 301\n", "README.md": "[a](https://aiqt.ai/a)\n"}, 0, ()),
    # (12) a chain the hop limit ends is reported as passing the hop limit, not as a loop.
    ("redirects/hop-limit-not-a-loop-fails", {**_SITE, "site/_redirects": "".join(
     "/h{} /h{} 301\n".format(i, i + 1) for i in range(1, 16)) + "/h16 /index.html 301\n"
     "/g0 /g1 301\n" + "".join("/g{} /g{} 301\n".format(i, i + 1) for i in range(1, 20)),
     "site/g20.html": "g", "README.md": "[h](https://aiqt.ai/h1) [g](https://aiqt.ai/g0)\n"}, 1,
     ("the file for /index.html is served at /, past the hop limit of 16 hops",
      "(/g16 /g17 301) passes the hop limit of 16 hops", "FAIL: 2 broken link(s)")),
    # (13) in raw HTML a backslash is a slash and a percent-encoded host is decoded, as a browser parses
    # the URL, so such an own-site href is checked; in an inline link /\host is a repository path.
    ("html/backslash-or-encoded-host-missing-fails", {**_SITE, "site/present.html": "p", "README.md":
     "<a href=\"/\\aiqt.ai/nope1\">a</a> <a href=\"\\\\aiqt.ai/nope2\">b</a> "
     "<a href=\"https:\\\\aiqt.ai\\nope3\">c</a> <a href=\"https://%61iqt.ai/nope4\">d</a>\n"
     "[e](https://%61iqt.ai/nope5) [f](//%61iqt.ai/nope6) [g](/\\aiqt.ai/present)\n"}, 1,
     ("README.md:1: broken own-site link -> /\\aiqt.ai/nope1 (no page for /nope1 under site/",
      "README.md:1: broken own-site link -> \\\\aiqt.ai/nope2 (no page for /nope2 under site/",
      "README.md:1: broken own-site link -> https:\\\\aiqt.ai\\nope3 (no page for /nope3 under site/",
      "README.md:1: broken own-site link -> https://%61iqt.ai/nope4 (no page for /nope4 under site/",
      "README.md:2: broken own-site link -> https://%61iqt.ai/nope5 (no page for /nope5 under site/",
      "README.md:2: broken own-site link -> //%61iqt.ai/nope6 (no page for /nope6 under site/",
      "README.md:2: broken link -> /\\aiqt.ai/present\n", "FAIL: 7 broken link(s)")),
    ("html/backslash-or-encoded-host-present-passes", {**_SITE, "site/present.html": "p", "README.md":
     "<a href=\"/\\aiqt.ai/present\">a</a> <a href=\"https:\\\\aiqt.ai\\present\">b</a> "
     "<a href=\"https://%61iqt.ai/present\">c</a> <a href=\"\\\\example.com/x\">d</a>\n"
     "[e](https://%61iqt.ai/present) [f](//%61iqt.ai/present) [g](https://%65xample.com/x)\n"}, 0, ()),
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
