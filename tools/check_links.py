#!/usr/bin/env python3
"""Fail on repository links whose target does not exist.

Two kinds of link are checked:

1. A relative inline link [text](path) in a Markdown file resolves against the file's directory and must
   exist.
2. An own-site link: an absolute http or https URL on a host this project's sites are served at, in an
   inline Markdown link, or an href in a generated-Markdown source (GENERATED_MD_SOURCES, where a
   root-relative '/path' is joined to the source's site_base exactly as its generator renders it into the
   Markdown). The host picks the site tree that serves it (SITE_ROOTS, the same host model as
   tools/check_site.py: aiqt.ai and www.aiqt.ai, or the AIQT_SITE_HOST host and its www., serve site/;
   opfiles.ai and www.opfiles.ai serve opf/site/), and the URL path must name a page of that tree: a
   regular file inside it at the path as written, at path + ".html", or at path/index.html ("/" is
   index.html), or a rule in that tree's _redirects whose source matches the path (exactly, or through a
   trailing * splat or a /:name placeholder). A tree absent from the checkout is not checked (an adopter
   vendoring this gate has no site/). The fragment and query are not checked; tools/check_site.py
   validates anchors inside the HTML.

Other external URLs, mailto and tel links, and pure anchors are out of scope: this gate answers "does the
target exist in this checkout", not "is the URL live". A reference-style link, an autolink and a bare URL
are not read.

PENDING_DECISION_ALLOWLIST names own-site links knowingly broken while a maintainer decides their fate.
Each entry is temporary, honoured only for its exact resolved URL, and printed on every run; an entry
whose target page now exists is itself a finding, so it is removed in the change that decides the link.

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

import os
import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)]+)\)")
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
NOT_RELATIVE = ("http://", "https://", "mailto:", "tel:", "#")

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

# Sources whose generator renders their href fields into a committed Markdown file, each with the
# site_base its generator uses when the source sets none (tools/gen_roadmap.py render_md and
# tools/gen_disclosure.py DEFAULT_BASE). A root-relative href there becomes site_base + href.
GENERATED_MD_SOURCES = {
    "roadmap.toml": "https://aiqt.ai",
    "disclosure.toml": "https://aiqt.ai",
}

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


class ScanError(Exception):
    """A tree or source this gate must read cannot be read as declared (exit 2)."""


def own_site_target(url, site_roots):
    """(tree, path) when url is an http or https URL on an own-site host, else None."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    tree = site_roots.get(host)
    if tree is None:
        return None
    return tree, unquote(parts.path) or "/"


def _rule_matches(source, path):
    pattern = "".join(
        ".*" if piece == "*" else "[^/]+" if piece.startswith(":") and len(piece) > 1 else re.escape(piece)
        for piece in _PLACEHOLDER.split(source))
    return re.fullmatch(pattern, path) is not None


def load_redirect_sources(tree_dir):
    """The source path of every rule in tree_dir/_redirects (none when the file is absent)."""
    path = tree_dir / "_redirects"
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        raise ScanError("{} is not a regular file".format(path))
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ScanError("{} is not UTF-8 ({})".format(path, exc)) from None
    sources = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            sources.append(stripped.split()[0])
    return sources


def page_present(tree_dir, path, redirect_sources):
    """True when tree_dir serves path: a matching file, path + .html, path/index.html, or a _redirects rule."""
    if any(_rule_matches(source, path) for source in redirect_sources):
        return True
    rel = path.lstrip("/")
    if rel == "" or rel.endswith("/"):
        candidates = [tree_dir / rel / "index.html"]
    else:
        candidates = [tree_dir / rel, tree_dir / (rel + ".html"), tree_dir / rel / "index.html"]
    base = tree_dir.resolve()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(base) and resolved.is_file():
            return True
    return False


def _source_hrefs(value):
    """Every string href in a parsed TOML document, in document order."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "href" and isinstance(item, str):
                yield item
            else:
                yield from _source_hrefs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _source_hrefs(item)


def scan(root, site_roots=None, sources=None, allowlist=None):
    """Return (findings, notes) for the tree at root. Raises OSError or ScanError when it cannot read it."""
    site_roots = SITE_ROOTS if site_roots is None else site_roots
    sources = GENERATED_MD_SOURCES if sources is None else sources
    allowlist = PENDING_DECISION_ALLOWLIST if allowlist is None else allowlist
    findings, notes = [], []
    trees = {}

    def tree_state(tree):
        if tree not in trees:
            tree_dir = root / tree
            trees[tree] = (tree_dir, load_redirect_sources(tree_dir)) if tree_dir.is_dir() else None
        return trees[tree]

    def check_own_site(where, shown, url):
        target = own_site_target(url, site_roots)
        if target is None:
            return False
        tree, path = target
        state = tree_state(tree)
        if state is None or page_present(state[0], path, state[1]):
            return True
        if url in allowlist:
            notes.append("{}: allowlisted pending decision ({}) -> {}".format(where, allowlist[url], shown))
            return True
        findings.append("{}: broken own-site link -> {} (no page for {} under {}/: no file, no .html, no "
                        "index.html, no _redirects rule)".format(where, shown, path, tree))
        return True

    for path in sorted(walk_files(root, SKIP_DIRS, suffixes={".md"})):
        rel = path.relative_to(root)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # a Markdown file that is not utf-8 is a real docs-integrity finding, not a fail-closed
            # read error; keep it a finding (exit 1). An OSError (unreadable) fails closed.
            findings.append(f"{rel}: cannot read as utf-8 (UnicodeDecodeError)")
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for target in LINK.findall(line):
                target = target.split()[0].strip()
                if not target:
                    continue
                if check_own_site(f"{rel}:{number}", target, target):
                    continue
                if target.startswith(NOT_RELATIVE):
                    continue
                clean = unquote(target.split("#", 1)[0])
                if not clean:
                    continue
                resolved = (path.parent / clean).resolve()
                if not resolved.exists():
                    findings.append(f"{rel}:{number}: broken link -> {target}")

    for name, default_base in sources.items():
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
        base = data.get("site_base", default_base)
        if not isinstance(base, str):
            raise ScanError("{}: site_base is not a string".format(name))
        lines = text.splitlines()
        cursor = 0
        for href in _source_hrefs(data):
            number = next((i + 1 for i in range(cursor, len(lines)) if '"{}"'.format(href) in lines[i]
                           or "'{}'".format(href) in lines[i]), 0)
            cursor = number if number else cursor
            where = "{}:{}".format(name, number) if number else name
            url = base + href if href.startswith("/") and not href.startswith("//") else href
            shown = href if url == href else "{} (renders as {})".format(href, url)
            check_own_site(where, shown, url)

    for url, label in sorted(allowlist.items()):
        target = own_site_target(url, site_roots)
        if target is None:
            continue
        state = tree_state(target[0])
        if state is not None and page_present(state[0], target[1], state[1]):
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
SELF_TEST_VECTORS = (
    ("relative/present-passes", {"README.md": "[a](docs/a.md)\n", "docs/a.md": "x\n"}, 0, ()),
    ("relative/missing-fails", {"README.md": "[a](docs/none.md)\n"}, 1, ("broken link -> docs/none.md",)),
    ("own-site/missing-page-fails", {**_SITE, "README.md": "[t](https://aiqt.ai/missing)\n"}, 1,
     ("README.md:1: broken own-site link -> https://aiqt.ai/missing", "no page for /missing under site/")),
    ("own-site/www-host-missing-fails", {**_SITE, "README.md": "[t](https://www.aiqt.ai/missing)\n"}, 1,
     ("broken own-site link -> https://www.aiqt.ai/missing",)),
    ("own-site/present-page-passes", {**_SITE, "site/present.html": "p", "README.md":
     "[a](https://aiqt.ai/present) [b](https://aiqt.ai/present.html) [c](https://aiqt.ai/) "
     "[d](https://aiqt.ai/present#frag)\n"}, 0, ()),
    ("own-site/directory-index-passes", {**_SITE, "site/dir/index.html": "d", "README.md":
     "[a](https://aiqt.ai/dir/) [b](https://aiqt.ai/dir)\n"}, 0, ()),
    ("own-site/directory-without-index-fails", {**_SITE, "site/bare/other.html": "o", "README.md":
     "[a](https://aiqt.ai/bare/)\n"}, 1, ("no page for /bare/ under site/",)),
    ("own-site/redirect-rule-passes", {**_SITE, "site/_redirects": "/old /index.html 301\n/blog/* / 301\n"
     "/u/:id / 301\n", "README.md": "[a](https://aiqt.ai/old) [b](https://aiqt.ai/blog/x/y) "
     "[c](https://aiqt.ai/u/7)\n"}, 0, ()),
    ("own-site/redirect-rule-not-a-prefix-fails", {**_SITE, "site/_redirects": "/old /index.html 301\n",
     "README.md": "[a](https://aiqt.ai/older)\n"}, 1, ("no page for /older under site/",)),
    ("own-site/escape-out-of-tree-fails", {**_SITE, "README.md": "[a](https://aiqt.ai/../README.md)\n"}, 1,
     ("broken own-site link",)),
    ("external/not-checked", {**_SITE, "README.md": "[a](https://example.com/missing) "
     "[b](https://notaiqt.ai/missing) [c](https://aiqt.ai.example.com/missing) [d](mailto:x@example.com)\n"},
     0, ()),
    ("opfiles/served-by-opf-site-only-fails", {**_SITE, "site/model.html": "m", "opf/site/index.html": "i",
     "README.md": "[a](https://opfiles.ai/model)\n"}, 1, ("no page for /model under opf/site/",)),
    ("opfiles/present-in-opf-site-passes", {**_SITE, "opf/site/model/index.html": "m", "README.md":
     "[a](https://opfiles.ai/model/) [b](https://www.opfiles.ai/model)\n"}, 0, ()),
    ("tree-absent/not-checked", {"README.md": "[a](https://aiqt.ai/missing) [b](https://opfiles.ai/x)\n"},
     0, ()),
    ("source/root-relative-href-missing-fails", {**_SITE, "roadmap.toml":
     'title = "T"\n[[stage]]\nlinks = [{ text = "x", href = "/gone" }]\n'}, 1,
     ("roadmap.toml:3: broken own-site link -> /gone (renders as https://aiqt.ai/gone)",)),
    ("source/root-relative-href-present-passes", {**_SITE, "site/here.html": "h", "disclosure.toml":
     'site_base = "https://aiqt.ai"\n[[row]]\nevidence = [{ text = "x", href = "/here#a" },'
     ' { text = "y", href = "https://example.com/z" }]\n'}, 0, ()),
    ("source/malformed-cannot-evaluate", {**_SITE, "roadmap.toml": "title = \n"}, 2, ()),
    ("allowlist/exact-url-honoured", {**_SITE, "README.md": "[t](https://aiqt.ai/teams)\n", "roadmap.toml":
     '[[stage]]\nlinks = [{ text = "t", href = "/teams" }]\n'}, 0,
     ("allowlisted pending decision (roadmap-teams-link) -> https://aiqt.ai/teams",
      "roadmap.toml:2: allowlisted pending decision (roadmap-teams-link) -> /teams")),
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
