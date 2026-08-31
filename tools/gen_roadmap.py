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
from opf_render import run_generator, FileTarget, BlockTarget  # noqa: E402

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces. site/roadmap.html is a generated block inside
# a hand-authored page (the roadmap markers), so it is recorded as kind block.
GENSRC_OUTPUTS = (
    {"target": "ROADMAP.md", "kind": "file",
     "sources": ("roadmap.toml",), "regenerate": "python3 tools/gen_roadmap.py"},
    {"target": "site/roadmap.html", "kind": "block",
     "sources": ("roadmap.toml",), "regenerate": "python3 tools/gen_roadmap.py"},
)


def _text(value):        # element text content
    return html.escape(value, quote=False)


def _attr(value):        # attribute value (must escape quotes)
    return html.escape(value, quote=True)


def _md_text(value):     # markdown link text: neutralize brackets and backslashes so they cannot break the link
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _md_href(value):     # markdown link destination: angle-bracket it when it holds spaces or parens
    if any(ch in value for ch in " ()"):
        return "<" + value.replace("\\", "\\\\").replace("<", "\\<").replace(">", "\\>") + ">"
    return value


def render_md(data):
    base = data.get("site_base", "https://aiqt.ai")
    lines = ["# " + data["title"], "", data["note"], ""]
    for stage in data["stage"]:
        lines.append("## {}: {}".format(stage["pill_label"], stage["heading"]))
        lines.append("")
        body = stage["body"]
        for link in stage.get("links", []):
            body += " [{}]({})".format(_md_text(link["text"]), _md_href(base + link["href"]))
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
    return run_generator(
        sys.argv[1:],
        source="roadmap.toml",
        targets=(
            FileTarget("ROADMAP.md", render_md),
            BlockTarget("site/roadmap.html", "ROADMAP", render_site),
        ),
        regen_hint="run tools/gen_roadmap.py to regenerate",
        schema_excs=(KeyError, TypeError),
    )


if __name__ == "__main__":
    sys.exit(main())
