"""Shared helpers for the single-source generators (roadmap, changelog). Stdlib only.

Requires Python 3.11+ for tomllib; CI pins 3.12. run_all_checks.sh runs these locally.
"""
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: the roadmap/changelog generators require Python 3.11+ (tomllib).")

from pathlib import Path
from urllib.parse import urlparse


def is_external_url(href):
    """True iff href is a link that leaves the aiqt.ai SITE, so it must carry a new-tab target/rel. A
    host equal to aiqt.ai or a subdomain of it is internal (same site); every other http(s) or
    protocol-relative destination is external. This is a SITE/host test, not an origin test: a different
    scheme or port on aiqt.ai (http://aiqt.ai, https://aiqt.ai:444) or a subdomain (https://sub.aiqt.ai)
    is deliberately internal, because a same-site link needs no new-tab safety.

    Classification follows how a browser parses an <a href>, so a naive-parser bypass cannot hide an
    off-site link: the HOSTNAME is parsed (aiqt.ai in a subdomain, userinfo, or path is not internal);
    backslashes fold to '/', ASCII tab/newline/CR are stripped, and any special-scheme slash count
    (http:/x, http:\\x, http:///x, http:x) normalizes to '//' before parsing, all per WHATWG; the scheme
    is matched case-insensitively; and it FAILS CLOSED (a malformed, hostless, or protocol-relative
    http(s) URL classifies EXTERNAL, never silently skipped). A relative path, fragment, mailto/tel, or
    other non-web href is not an off-site web link (False).

    Scope and residual: this classifies the project's own trusted, well-formed HTML to catch a forgotten
    target/rel on an off-site link; it is not a general validator of attacker-controlled hrefs. A host
    given as a raw IP or with a trailing dot is compared literally, and an internationalized host is not
    punycode-folded; each errs toward EXTERNAL, never toward a missed off-site link."""
    s = (href or "").strip().translate({9: None, 10: None, 13: None}).replace("\\", "/")
    low = s.lower()
    if low.startswith(("http:", "https:")):
        scheme, _, rest = s.partition(":")    # special scheme: any slash count (incl. 0) means authority follows (WHATWG)
        probe = scheme + "://" + rest.lstrip("/")
    elif low.startswith("//"):
        probe = "https:" + s                  # protocol-relative: give it a scheme so the authority parses
    else:
        return False                          # relative, fragment, mailto, tel, ...: not an off-site web link
    try:
        host = (urlparse(probe).hostname or "").lower()
    except ValueError:
        return True                           # a web URL we cannot parse: fail-closed to external
    if not host:
        return True                           # web URL with no resolvable host: fail-closed to external
    return not (host == "aiqt.ai" or host.endswith(".aiqt.ai"))


def repo_root(start=None):
    p = Path(start or __file__).resolve()
    for anc in [p, *p.parents]:
        if (anc / ".git").exists():
            return anc
    return Path.cwd()


def load_toml(path):
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def _markers(name):
    return ("<!-- {}:BEGIN (generated) -->".format(name),
            "<!-- {}:END -->".format(name))


def replace_block(html_text, name, inner):
    """Replace the content between the named markers, keeping the markers."""
    begin, end = _markers(name)
    i = html_text.find(begin)
    j = html_text.find(end)
    if i == -1 or j == -1 or j < i:
        raise ValueError("markers for {} not found in the page".format(name))
    return html_text[:i] + begin + "\n" + inner + "\n      " + html_text[j:]


def reconcile(path, new_text, check):
    """Write new_text to path, or (check mode) return True if it would change. Fail-closed: an OSError
    reading the current file or writing the new one exits 2 with a message rather than a raw traceback,
    so a drift gate or a regeneration never dies unhandled on a read-only fs, a permission error, or a
    full disk. An invalid-UTF-8 (non-decodable) existing target is mapped to the same exit 2: read_text
    decodes as UTF-8, so a corrupt target raises UnicodeDecodeError, and that is fail-closed too rather
    than a raw traceback."""
    path = Path(path)
    try:
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if check:
            return current != new_text
        path.write_text(new_text, encoding="utf-8")
        return False
    except (OSError, UnicodeError) as exc:
        print("error: cannot read or write {} ({}); fail-closed".format(path, exc), file=sys.stderr)
        raise SystemExit(2)
