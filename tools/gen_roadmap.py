#!/usr/bin/env python3
"""Generate ROADMAP.md and the site roadmap block from roadmap.toml.

Single source of truth for the two public roadmap faces, so they cannot diverge.
  gen_roadmap.py           regenerate ROADMAP.md and site/roadmap.html
  gen_roadmap.py --check   fail (exit 1) if either is out of date; exit 2 on error
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

import html
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


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. This entry point reads a
# child's exit status (the git queries of the special-file precheck in tools/_gen_common.py
# among them), so under an ignored SIGCHLD it would read a failing child as a pass; it refuses
# at its entry line instead, before main() runs.
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("gen_roadmap.py") or main())
