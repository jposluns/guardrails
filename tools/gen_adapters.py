#!/usr/bin/env python3
"""Generate the plain-Markdown adapter files GEMINI.md and .github/copilot-instructions.md.

Same core that feeds Claude's .claude/rules/ tree and Codex's AGENTS.md, so a Gemini CLI or GitHub
Copilot session gets identical AIQT governance. Each adapter is the corpus concatenated in AIQT
priority order (apex; then Accuracy, Integrity, Quality, Trust at tier 10; then Progress, Speed, Cost;
then the security family), with a per-tool header. Gemini CLI reads GEMINI.md and Copilot in the IDE
reads .github/copilot-instructions.md by default, neither of which loads AGENTS.md, so each needs its
own file. The body transform is identical to AGENTS.md; only the header and location differ. The layout
of each file comes from the block registry .aiqt/core/adapter-blocks.toml through
tools/_adapter_compose.py, the engine shared with gen_agents.py; both adapters are composed and judged
in one run, so a refused target means neither is written.
--check drift-gates both files byte for byte; exit 2 on a malformed source or registry, a read/write
failure, a symlinked target or parent directory, an absent rule corpus (nothing is deleted), or a write
that would erase a hand edit in a composed-layout target. A legacy-layout target is refused only when it
holds a pasted marker-like line; any other hand edit in it is lost on regeneration, though --check still
reports it as drift.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_adapters.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from _adapter_compose import legacy_render, run  # noqa: E402
from gen_agents import body_of, corpus_bodies  # noqa: E402  shared body transform and AIQT ordering

# Each adapter: the output path (relative parts, joined under repo root) and its header lines. The body
# after the header is identical across adapters (the AIQT-ordered corpus), so only these two differ.
ADAPTERS = (
    {
        "label": "gemini",
        "parts": ("GEMINI.md",),
        "header": [
            "# GEMINI.md",
            "",
            "AIQT Guardrails governance for a Gemini CLI session. GENERATED from the same rule corpus",
            "that feeds Claude's .claude/rules/ tree (tools/gen_adapters.py); do not hand-edit. Rules are",
            "in AIQT priority order: the apex, then Accuracy, Integrity, Quality, Trust, then Progress,",
            "Speed, Cost, then the security family.",
            "",
        ],
    },
    {
        "label": "copilot",
        "parts": (".github", "copilot-instructions.md"),
        "header": [
            "# AIQT Guardrails",
            "",
            "Repository custom instructions for GitHub Copilot. GENERATED from the same rule corpus that",
            "feeds Claude's .claude/rules/ tree (tools/gen_adapters.py); do not hand-edit. Rules are in",
            "AIQT priority order: the apex, then Accuracy, Integrity, Quality, Trust, then Progress, Speed,",
            "Cost, then the security family.",
            "",
        ],
    },
)

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py; VER-CORE 6.5).
RENDERER_DECL = {"renderer-id": "adapters", "semantics-revision": 1}
GENSRC_OUTPUTS = (
    {"target": "GEMINI.md", "kind": "file",
     "sources": (".aiqt/core/rules/", ".aiqt/core/adapter-blocks.toml"),
     "regenerate": "python3 tools/gen_adapters.py"},
    {"target": ".github/copilot-instructions.md", "kind": "file",
     "sources": (".aiqt/core/rules/", ".aiqt/core/adapter-blocks.toml"),
     "regenerate": "python3 tools/gen_adapters.py"},
)
# The (repo-relative path, header) pairs run() composes, in ADAPTERS order.
TARGETS = [("/".join(adapter["parts"]), adapter["header"]) for adapter in ADAPTERS]


def render(pairs, header):
    """The legacy adapter text for sorted (path, frontmatter) pairs; kept for existing importers."""
    return legacy_render(header, [body_of(p) for p, _ in pairs])


def main():
    return run(repo_root(), "--check" in sys.argv[1:], TARGETS, "tools/gen_adapters.py", corpus_bodies)


if __name__ == "__main__":
    sys.exit(main())
