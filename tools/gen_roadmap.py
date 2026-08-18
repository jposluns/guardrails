#!/usr/bin/env python3
"""Generate ROADMAP.md and the site roadmap block from roadmap.toml.

Single source of truth for the two public roadmap faces, so they cannot diverge.
  gen_roadmap.py           regenerate ROADMAP.md and site/roadmap.html
  gen_roadmap.py --check   fail (exit 1) if either is out of date; exit 2 on error
"""
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml, replace_block, reconcile  # noqa: E402


def _text(value):        # element text content
    return html.escape(value, quote=False)


def _attr(value):        # attribute value (must escape quotes)
    return html.escape(value, quote=True)


def render_md(data):
    base = data.get("site_base", "https://aiqt.ai")
    lines = ["# " + data["title"], "", data["note"], ""]
    for stage in data["stage"]:
        lines.append("## {}: {}".format(stage["pill_label"], stage["heading"]))
        lines.append("")
        body = stage["body"]
        for link in stage.get("links", []):
            body += " [{}]({}{})".format(link["text"], base, link["href"])
        lines.append(body)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_site(data):
    cards = []
    last = len(data["stage"]) - 1
    for idx, stage in enumerate(data["stage"]):
        style = ' style="margin-bottom:1.1rem"' if idx < last else ""
        para = _text(stage["body"])
        for link in stage.get("links", []):
            para += ' <a href="{}">{}</a>'.format(
                _attr(link["href"]), _text(link["text"]))
        cards.append(
            '      <div class="card"{}>\n'
            '        <span class="pill {}">{}</span>\n'
            '        <h3>{}</h3>\n'
            '        <p>{}</p>\n'
            '      </div>'.format(
                style, _attr(stage["pill_class"]), _text(stage["pill_label"]),
                _text(stage["heading"]), para))
    return "\n".join(cards)


def main():
    check = "--check" in sys.argv[1:]
    root = repo_root()
    try:
        data = load_toml(root / "roadmap.toml")
    except (OSError, ValueError) as exc:
        print("error: cannot read roadmap.toml: {}".format(exc))
        return 2
    try:
        md = render_md(data)
        site_inner = render_site(data)
    except (KeyError, TypeError) as exc:
        print("error: roadmap.toml is missing or misuses a key: {}".format(exc))
        return 2
    drift = False
    if reconcile(root / "ROADMAP.md", md, check):
        print("drift: ROADMAP.md"); drift = True
    site = root / "site" / "roadmap.html"
    if not site.exists():
        print("error: site/roadmap.html not found (expected generated target)")
        return 2
    try:
        new_html = replace_block(site.read_text(encoding="utf-8"), "ROADMAP", site_inner)
    except (ValueError, OSError) as exc:
        # OSError too: an unreadable site/roadmap.html is a read error, fail-closed exit 2, not a traceback.
        print("error: {}".format(exc))
        return 2
    if reconcile(site, new_html, check):
        print("drift: site/roadmap.html"); drift = True
    if check and drift:
        print("run tools/gen_roadmap.py to regenerate")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
