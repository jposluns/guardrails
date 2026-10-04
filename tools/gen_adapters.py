#!/usr/bin/env python3
"""Generate the plain-Markdown adapter files GEMINI.md and .github/copilot-instructions.md.

Same core that feeds Claude's .claude/rules/ tree and Codex's AGENTS.md, so a Gemini CLI or GitHub
Copilot session gets identical AIQT governance. Each adapter is the corpus concatenated in AIQT
priority order (apex; then Accuracy, Integrity, Quality, Trust at tier 10; then Progress, Speed, Cost;
then the security family), with a per-tool header. Gemini CLI reads GEMINI.md and Copilot in the IDE
reads .github/copilot-instructions.md by default, neither of which loads AGENTS.md, so each needs its
own file. The body transform is identical to AGENTS.md; only the header and location differ.
--check drift-gates both files; exit 2 on a malformed source or a read/write failure.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from gen_rules import load_corpus  # noqa: E402
from gen_agents import body_of, sort_key  # noqa: E402  shared body transform and AIQT ordering

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
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_adapters.py"},
    {"target": ".github/copilot-instructions.md", "kind": "file",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_adapters.py"},
)


def _exists(path):
    """Fail-closed existence probe. Path.exists() swallows EACCES on Python 3.12 (returns False on an
    unreadable parent), which would mask a present-but-unreadable adapter as absent. Path.stat() raises
    on EACCES, so absent -> False and an unreadable parent -> OSError, which the caller's fail-closed try
    turns into exit 2. Only the nested .github/ adapter has a parent that can independently be unreadable;
    the root-level generators (AGENTS.md) never hit this, so they keep the plain exists() idiom."""
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def render(pairs, header):
    return "\n".join(header + [body_of(p) + "\n" for p, _ in pairs]).rstrip() + "\n"


def main():
    check = "--check" in sys.argv[1:]
    root = repo_root()
    src_dir = root / ".aiqt" / "core" / "rules"
    try:
        if not src_dir.is_dir():
            # No sources: a generated adapter left on disk is drift (in --check) or is removed.
            drift = False
            for adapter in ADAPTERS:
                out = root.joinpath(*adapter["parts"])
                if _exists(out):
                    if check:
                        print("drift: {} exists with no sources".format("/".join(adapter["parts"])))
                        drift = True
                    else:
                        out.unlink()
            return 1 if (check and drift) else 0
        pairs = [(src, fm) for src, fm, _ in load_corpus(src_dir)]
        pairs.sort(key=lambda pf: sort_key(pf[1]))
        # render() reads each source via body_of, and out.read_text reads the adapter: keep these, and
        # the write/unlink, inside the fail-closed try so a read or write error is a clean exit 2.
        drift = False
        for adapter in ADAPTERS:
            out = root.joinpath(*adapter["parts"])
            content = render(pairs, adapter["header"])
            current = out.read_text(encoding="utf-8") if _exists(out) else None
            if current != content:
                if check:
                    print("drift: {}".format("/".join(adapter["parts"])))
                    drift = True
                else:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(content, encoding="utf-8")
        if check and drift:
            print("run tools/gen_adapters.py to regenerate")
            return 1
        return 0
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
