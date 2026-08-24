#!/usr/bin/env python3
"""Generate AGENTS.md (the Codex adapter) from the .aiqt/core/rules/ sources.

Same core that feeds Claude's .claude/rules/ tree, so a Codex session gets identical AIQT governance. The
rules are concatenated in AIQT priority order (apex; then Accuracy, Integrity, Quality, Trust at tier 10;
then Progress, Speed, Cost; then the security family in CIA-plus-privacy order), each rule's title demoted
to a section heading. --check drift-gates AGENTS.md; exit 2 on a malformed source.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from _standards import dir_present  # noqa: E402
from gen_rules import load_corpus  # noqa: E402

AIQT_FACET_ORDER = {"ACCUR": 0, "INTEG": 1, "QUALI": 2, "TRUST": 3}
CIA_FACET_ORDER = {"SECC": 0, "SECI": 1, "SECA": 2, "SECP": 3}

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
# Renderer identity for the manifest-covered declaration (tools/gen_renderers.py; VER-CORE 6.5).
RENDERER_DECL = {"renderer-id": "agents", "semantics-revision": 1}
GENSRC_OUTPUTS = (
    {"target": "AGENTS.md", "kind": "file",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_agents.py"},
)


def sort_key(fm):
    if fm["family"] == "aiqt":
        if fm.get("apex") is True:
            return (0, 0, 0, str(fm["slug"]))
        return (1, int(fm["tier"]), AIQT_FACET_ORDER.get(fm.get("facet", ""), 9), str(fm["slug"]))
    return (2, 0, CIA_FACET_ORDER.get(fm.get("facet", ""), 9), str(fm["slug"]))


def body_of(path):
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    body = text[end + 5:].strip()
    if body.startswith("# "):        # demote the rule title to a section heading under the AGENTS.md H1
        body = "#" + body
    return body


def render(pairs):
    header = ["# AGENTS.md",
              "",
              "AIQT Guardrails governance for a Codex session. GENERATED from the same rule corpus that",
              "feeds Claude's .claude/rules/ tree (tools/gen_agents.py); do not hand-edit. Rules are in AIQT",
              "priority order: the apex, then Accuracy, Integrity, Quality, Trust, then Progress, Speed,",
              "Cost, then the security family.",
              ""]
    return "\n".join(header + [body_of(p) + "\n" for p, _ in pairs]).rstrip() + "\n"


def main():
    check = "--check" in sys.argv[1:]
    root = repo_root()
    src_dir = root / ".aiqt" / "core" / "rules"
    out = root / "AGENTS.md"
    try:
        # dir_present (not is_dir) inside the try: an unreadable .aiqt/ parent fails closed as exit 2,
        # not as an absent corpus (which would delete AGENTS.md as an orphan).
        if not dir_present(src_dir):
            if out.exists():
                if check:
                    print("drift: AGENTS.md exists with no sources")
                    return 1
                out.unlink()
            return 0
        pairs = [(src, fm) for src, fm, _ in load_corpus(src_dir)]
        pairs.sort(key=lambda pf: sort_key(pf[1]))
        # render() reads each source via body_of, and out.read_text reads AGENTS.md: keep these, and the
        # write/unlink, inside the fail-closed try so a read or write error is a clean exit 2, not a traceback.
        content = render(pairs)
        current = out.read_text(encoding="utf-8") if out.exists() else None
        if current != content:
            if check:
                print("drift: AGENTS.md")
                print("run tools/gen_agents.py to regenerate")
                return 1
            out.write_text(content, encoding="utf-8")
        return 0
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
