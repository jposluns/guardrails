#!/usr/bin/env python3
"""Generate ROADMAP.md from roadmap.toml.

The site no longer carries a roadmap page, so ROADMAP.md is the one public roadmap face.
  gen_roadmap.py           regenerate ROADMAP.md
  gen_roadmap.py --check   fail (exit 1) if it is out of date; exit 2 on error
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_roadmap.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from opf_render import run_generator, FileTarget  # noqa: E402

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
GENSRC_OUTPUTS = (
    {"target": "ROADMAP.md", "kind": "file",
     "sources": ("roadmap.toml",), "regenerate": "python3 tools/gen_roadmap.py"},
)


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


def main():
    return run_generator(
        sys.argv[1:],
        source="roadmap.toml",
        targets=(
            FileTarget("ROADMAP.md", render_md),
        ),
        regen_hint="run tools/gen_roadmap.py to regenerate",
        schema_excs=(KeyError, TypeError),
    )


if __name__ == "__main__":
    sys.exit(main())
