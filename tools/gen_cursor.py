#!/usr/bin/env python3
"""Generate the .cursor/rules/ Cursor adapter tree from .aiqt/core/rules/ sources.

Same core that feeds Claude's .claude/rules/ tree, so a Cursor session gets identical AIQT governance.
Cursor loads *.mdc files under .cursor/rules/ (recursing into subdirectories; a plain .md there is
ignored), and an .mdc whose frontmatter says `alwaysApply: true` is always included, with `globs` and
`description` ignored, so each rule becomes its own .mdc carrying that one key and the rule body
verbatim (H1 intact: each .mdc is its own document, unlike the AGENTS.md/GEMINI.md concatenations,
which demote it). All output lives under the RESERVED subtree .cursor/rules/aiqt-guardrails/,
mirroring the two-axis taxonomy, so reconciliation (including orphan deletion) only ever touches the
pack's own subtree and never an adopter's own Cursor rules beside it. Format per
https://cursor.com/docs/rules (retrieved 2026-08-17).
  gen_cursor.py           regenerate .cursor/rules/aiqt-guardrails/{aiqt,security}/
  gen_cursor.py --check   fail (exit 1) on drift; exit 2 on a malformed source or a read/write failure
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from _standards import dir_present  # noqa: E402
from gen_rules import load_corpus  # noqa: E402

# The reserved output subtree (relative parts, joined under repo root). Everything under it is
# generated; nothing outside it is ever written or deleted, so an adopter's own .cursor/rules files
# are structurally out of reach of the orphan prune.
OUT_PARTS = (".cursor", "rules", "aiqt-guardrails")
# The one frontmatter key an always-apply Cursor rule needs. globs and description are ignored under
# alwaysApply: true (cursor.com/docs/rules), so neither is emitted as a dead key.
FRONTMATTER = "---\nalwaysApply: true\n---\n\n"

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces.
GENSRC_OUTPUTS = (
    {"target": ".cursor/rules/aiqt-guardrails/", "kind": "tree",
     "sources": (".aiqt/core/rules/",), "regenerate": "python3 tools/gen_cursor.py"},
)


def cursor_rel(rel):
    """The Cursor tree path for a derived rule path: the same two-axis rel with the required .mdc
    suffix (a plain .md in .cursor/rules is ignored, so the suffix swap is load-bearing)."""
    return rel[:-len(".md")] + ".mdc"


def render_rule(path):
    """The full .mdc content for one source rule: the Cursor frontmatter, then the rule body verbatim
    (source frontmatter stripped, H1 kept; do NOT reuse gen_agents.body_of, which demotes the H1 for
    the single-document concatenations). conformance.py imports this so the checker and the generator
    can never disagree on the shape."""
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    body = text[end + 5:].strip()
    return FRONTMATTER + body + "\n"


def main():
    check = "--check" in sys.argv[1:]
    root = repo_root()
    src_dir = root / ".aiqt" / "core" / "rules"
    out_dir = root.joinpath(*OUT_PARTS)
    desired = {}
    try:
        # dir_present (not is_dir) inside the try: an unreadable .aiqt/ parent must fail closed as exit 2,
        # not read as an absent corpus (which would delete every generated .mdc as an orphan below).
        if dir_present(src_dir):
            for src, _fm, rel in load_corpus(src_dir):
                desired[cursor_rel(rel)] = render_rule(src)
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2
    # Reconcile even when src_dir is absent (desired empty) so orphaned generated files are never concealed.
    # The whole reconcile is fail-closed: an unreadable generated file (target.read_text) or an unreadable
    # generated dir at ANY depth (os.walk(onerror=raise), not rglob, which silently skips an unlistable
    # subdir) becomes a clean exit 2 rather than a traceback or a concealed orphan.
    drift = []

    def _raise(exc):
        raise exc
    try:
        for rel, content in sorted(desired.items()):
            target = out_dir / rel
            current = target.read_text(encoding="utf-8") if target.exists() else None
            if current != content:
                drift.append(rel)
                if not check:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content, encoding="utf-8")
        # Orphan scan, scoped to the reserved subtree ONLY (it is 100% generated; siblings under
        # .cursor/rules/ may be the adopter's own and are never walked). dir_present, not is_dir: an
        # unreadable .cursor/ parent must fail closed, not skip the orphan scan.
        if dir_present(out_dir):
            for dirpath, _dirs, filenames in os.walk(out_dir, onerror=_raise):
                for fn in sorted(f for f in filenames if f.endswith(".mdc")):
                    f = Path(dirpath) / fn
                    # as_posix(), not str(): desired keys are forward-slash derive() paths, so a
                    # backslash from str() on Windows would flag every generated file as an orphan.
                    rel = f.relative_to(out_dir).as_posix()
                    if rel not in desired:
                        drift.append("orphan " + rel)
                        if not check:
                            f.unlink()
    except OSError as exc:
        print("error: {}".format(exc))
        return 2
    if check and drift:
        print("drift: " + "; ".join(drift))
        print("run tools/gen_cursor.py to regenerate")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
