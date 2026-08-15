"""Shared helpers for the single-source generators (roadmap, changelog). Stdlib only.

Requires Python 3.11+ for tomllib; CI pins 3.12. run_all_checks.sh runs these locally.
"""
import sys

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: the roadmap/changelog generators require Python 3.11+ (tomllib).")

from pathlib import Path


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
    """Write new_text to path, or (check mode) return True if it would change."""
    path = Path(path)
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if check:
        return current != new_text
    path.write_text(new_text, encoding="utf-8")
    return False
