#!/usr/bin/env python3
"""Generate the .claude/rules/ read tree from .aiqt/core/rules/ sources (the source-and-adapter machinery).

Each source is a Markdown rule with a minimal YAML `---` frontmatter carrying its classification; its read
path is DERIVED from that frontmatter per the two-axis taxonomy (aiqt/ numbered by priority, security/
coded CIA+P). The updater's write root is `.aiqt/core/`; CI runs this in --check so the read tree can never
silently drift (including orphaned generated files with no source). Vendored `external/` trees are untouched.
  gen_rules.py           regenerate .claude/rules/{aiqt,security}/
  gen_rules.py --check   fail (exit 1) on drift; exit 2 on a malformed source
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

TIER_FACETS = {"10": {"ACCUR", "INTEG", "QUALI", "TRUST"}, "20": {"PROGR"},
               "30": {"SPEED"}, "40": {"COST"}}
CIA_FACETS = {"CONFI", "INTEG", "AVAIL", "PRIV"}
BASE_KEYS = {"corpus-id", "origin", "family", "slug", "secondary"}
SLUG_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
CID_RE = re.compile(r'^[a-z0-9]{6,}$')


def _value(v):
    if v and v[0] in "\"'":
        if len(v) >= 2 and v[-1] == v[0]:
            return v[1:-1]
        raise ValueError("malformed quoted value {!r}".format(v))
    if v in ("true", "false"):
        return v == "true"
    if re.fullmatch(r'-?\d+', v):
        return int(v)
    return v


def parse_source(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("{}: no frontmatter".format(path.name))
    end = text.find("\n---\n", 4)
    if end == -1:
        raise ValueError("{}: unterminated frontmatter".format(path.name))
    fm = {}
    for line in text[4:end].splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError("{}: bad frontmatter line {!r}".format(path.name, line))
        key, val = line.split(":", 1)
        key = key.strip()
        if key in fm:
            raise ValueError("{}: duplicate key {}".format(path.name, key))
        try:
            fm[key] = _value(val.strip())
        except ValueError as exc:
            raise ValueError("{}: {}".format(path.name, exc))
    return fm


def _check_keys(fm, allowed, name):
    extra = set(fm) - allowed
    if extra:
        raise ValueError("{}: unknown or forbidden key(s): {}".format(name, ", ".join(sorted(extra))))


def derive(fm, name):
    for req in ("corpus-id", "origin", "family", "slug"):
        if req not in fm:
            raise ValueError("{}: missing required key '{}'".format(name, req))
    if not CID_RE.match(str(fm["corpus-id"])):
        raise ValueError("{}: corpus-id must match ^[a-z0-9]{{6,}}$".format(name))
    if fm["origin"] != "pack":
        raise ValueError("{}: origin must be 'pack'".format(name))
    if not SLUG_RE.match(str(fm["slug"])):
        raise ValueError("{}: slug must be kebab-case".format(name))
    family = fm["family"]
    if family == "aiqt":
        if fm.get("apex") is True:
            _check_keys(fm, BASE_KEYS | {"apex"}, name)
            if fm["slug"] != "project-integrity":
                raise ValueError("{}: apex slug must be 'project-integrity'".format(name))
            return "aiqt/00-project-integrity.md"
        _check_keys(fm, BASE_KEYS | {"tier", "facet"}, name)
        tier = str(fm.get("tier", ""))
        facet = fm.get("facet", "")
        if tier not in TIER_FACETS:
            raise ValueError("{}: tier must be 10/20/30/40".format(name))
        if facet not in TIER_FACETS[tier]:
            raise ValueError("{}: facet '{}' invalid for tier {}".format(name, facet, tier))
        return "aiqt/{}-{}-{}.md".format(tier, facet, fm["slug"])
    if family == "security":
        _check_keys(fm, BASE_KEYS | {"facet"}, name)
        facet = fm.get("facet", "")
        if facet not in CIA_FACETS:
            raise ValueError("{}: security facet must be CONFI/INTEG/AVAIL/PRIV".format(name))
        return "security/{}-{}.md".format(facet, fm["slug"])
    raise ValueError("{}: unknown family '{}'".format(name, family))


def load_corpus(src_dir):
    """Parse and fully validate every source (schema + unique corpus-id + unique derived path); raise
    ValueError on any malformed or duplicate. Returns [(path, frontmatter, derived_rel_path)]."""
    seen_ids, seen_paths, out = {}, {}, []
    for src in sorted(src_dir.rglob("*.md")):
        fm = parse_source(src)
        rel = derive(fm, src.name)
        cid = str(fm["corpus-id"])
        if cid in seen_ids:
            raise ValueError("{}: corpus-id {} already used by {}".format(src.name, cid, seen_ids[cid]))
        if rel in seen_paths:
            raise ValueError("{}: derives to {} already produced by {}".format(src.name, rel, seen_paths[rel]))
        seen_ids[cid] = src.name
        seen_paths[rel] = src.name
        out.append((src, fm, rel))
    return out


def main():
    check = "--check" in sys.argv[1:]
    root = repo_root()
    src_dir = root / ".aiqt" / "core" / "rules"
    out_dir = root / ".claude" / "rules"
    desired = {}
    if src_dir.is_dir():
        try:
            for src, _fm, rel in load_corpus(src_dir):
                desired[rel] = src.read_text(encoding="utf-8")
        except (ValueError, OSError) as exc:
            print("error: {}".format(exc))
            return 2
    # Reconcile even when src_dir is absent (desired empty) so orphaned generated files are never concealed.
    drift = []
    for rel, content in sorted(desired.items()):
        target = out_dir / rel
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current != content:
            drift.append(rel)
            if not check:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
    for family in ("aiqt", "security"):
        fam_dir = out_dir / family
        if fam_dir.is_dir():
            for f in sorted(fam_dir.rglob("*.md")):
                rel = str(f.relative_to(out_dir))
                if rel not in desired:
                    drift.append("orphan " + rel)
                    if not check:
                        f.unlink()
    if check and drift:
        print("drift: " + "; ".join(drift))
        print("run tools/gen_rules.py to regenerate")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
