#!/usr/bin/env python3
"""Generate the plain-Markdown adapter files GEMINI.md and .github/copilot-instructions.md.

Same core that feeds Claude's .claude/rules/ tree and Codex's AGENTS.md, so a Gemini CLI or GitHub
Copilot session gets identical AIQT governance. Each adapter is the corpus concatenated in AIQT
priority order (apex; then Accuracy, Integrity, Quality, Trust at tier 10; then Progress, Speed, Cost;
then the security family), with a per-tool header. Gemini CLI reads GEMINI.md and Copilot in the IDE
reads .github/copilot-instructions.md by default, neither of which loads AGENTS.md, so each needs its
own file. The body transform is identical to AGENTS.md (gen_agents.bodies_of_pairs: each rule's core
render at its place, then the detail layer of every rule using the `## Detail` split after the last
rule); only the header and location differ. The layout
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
from _gen_common import repo_root, unknown_args_refused  # noqa: E402
from _adapter_compose import legacy_render, run  # noqa: E402
from gen_agents import bodies_of_pairs, corpus_bodies  # noqa: E402  shared body transform and AIQT ordering

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
RENDERER_DECL = {"renderer-id": "adapters", "semantics-revision": 2}
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
    return legacy_render(header, bodies_of_pairs(pairs))


def main():
    argv = sys.argv[1:]
    # Unknown-option rejection precedes every dispatch: main() defines only --check, so any other token (a
    # misspelled --chek, a stray --help, a positional) is exit 2 with the usage line on stderr, never a
    # fall-through to a write-mode run. gen_agents.py's self-test drives this guard (vector T39), as this
    # generator has no self-test of its own.
    if unknown_args_refused(argv, "gen_adapters.py [--check]", flags=("--check",)):
        return 2
    return run(repo_root(), "--check" in argv, TARGETS, "tools/gen_adapters.py", corpus_bodies)


if __name__ == "__main__":
    sys.exit(main())
