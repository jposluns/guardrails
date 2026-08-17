#!/usr/bin/env python3
"""Generate the .claude/rules/ read tree from .aiqt/core/rules/ sources (the source-and-adapter machinery).

Each source is a Markdown rule with a minimal YAML `---` frontmatter carrying its classification; its read
path is DERIVED from that frontmatter per the two-axis taxonomy (aiqt/ numbered by priority, security/
coded CIA+P). The updater's write root is `.aiqt/core/`; CI runs this in --check so the read tree can never
silently drift (including orphaned generated files with no source). Vendored `external/` trees are untouched.
  gen_rules.py           regenerate .claude/rules/{aiqt,security}/
  gen_rules.py --check   fail (exit 1) on drift; exit 2 on a malformed source
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from _standards import map_keys  # noqa: E402

TIER_FACETS = {"10": {"ACCUR", "INTEG", "QUALI", "TRUST"}, "20": {"PROGR"},
               "30": {"SPEED"}, "40": {"COST"}}
CIA_FACETS = {"SECC", "SECI", "SECA", "SECP"}  # SECC=Confidentiality SECI=Integrity SECA=Availability SECP=Privacy
# The global known-facet set: any rule may carry ANY of these as a `secondary` tag, cross-family (an aiqt
# rule may tag a security facet and vice versa). Security codes are namespaced (SEC*) so none collides with
# an AIQT facet (e.g. SECI vs the AIQT INTEG). The secondary element rules (known-facet, differs-from-
# primary, no-repeat) come from the Architect's recorded decision (design-of-record: secondary = any known
# facet), NOT spec section 4, which only types `secondary` as a sequence of facet-code strings.
KNOWN_FACETS = set().union(*TIER_FACETS.values()) | CIA_FACETS
# Keys allowed for EVERY rule family. secondary is NOT here: spec section 4 forbids it on the apex and
# allows it only on aiqt-non-apex and security, so it is added to those allow-sets individually.
BASE_KEYS = {"corpus-id", "origin", "family", "slug"}
# Optional standards-mapping keys, allowed on security and aiqt-non-apex rules. Each value is a flow
# sequence of external control/subcategory IDs, for the public /mappings crosswalk. Adding a mapping key
# never affects a rule's derived path. The set is DERIVED from the vendored manifests under
# .aiqt/standards/ (one key per manifest): a mapping key is valid only if its framework's pinned id
# manifest exists, so a rule can never cite an unsourced framework. check_mappings then validates each
# cited id against that manifest's enumerated set.
try:
    MAP_KEYS = map_keys(repo_root())
except OSError as _exc:
    # map_keys fails closed on an existing-but-unlistable .aiqt/standards/. This binding runs at import,
    # so convert that read error into a clean exit 2 with a message rather than a bare traceback; every
    # tool that imports gen_rules (against its OWN broken standards dir) then fails closed uniformly.
    print("error: cannot read {}/.aiqt/standards/ (fail-closed): {}".format(repo_root(), _exc),
          file=sys.stderr)
    raise SystemExit(2)
# Keys whose value, when present, must be a flow sequence (a list): secondary and every mapping key.
SEQ_KEYS = {"secondary"} | MAP_KEYS
SLUG_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
CID_RE = re.compile(r'^[a-z0-9]{6,}$')


def _unquote(tok):
    """A single scalar STRING element: strip matching quotes, else the bare token. Fail-closed."""
    if tok and tok[0] in "\"'":
        if len(tok) >= 2 and tok[-1] == tok[0]:
            return tok[1:-1]
        raise ValueError("malformed quoted value {!r}".format(tok))
    return tok


def _value(v):
    if v and v[0] in "\"'":
        return _unquote(v)
    # Flow sequence of strings, e.g. [INTEG, QUALI] (spec section 4). Elements are strings; a bare
    # element is not coerced to int/bool. Malformed (unclosed, or an empty element) is fail-closed.
    if v.startswith("["):
        if not v.endswith("]"):
            raise ValueError("malformed flow sequence {!r} (unterminated)".format(v))
        inner = v[1:-1].strip()
        if inner == "":
            return []
        if "[" in inner or "]" in inner:
            raise ValueError("nested flow sequence not allowed in {!r} (strings only)".format(v))
        elems = []
        for part in inner.split(","):
            part = part.strip()
            if part == "":
                raise ValueError("empty element in flow sequence {!r}".format(v))
            elems.append(_unquote(part))
        return elems
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


def _check_secondary(fm, primary, name):
    # `secondary` is optional; when present it is already validated as a list (SEQ_KEYS). Each element
    # must be a known facet code, must differ from the primary facet (a facet is not its own secondary),
    # and must not repeat. Fail-closed so a typo or a duplicate is caught at generation, not shipped.
    seen = set()
    for s in fm.get("secondary", []):
        if s not in KNOWN_FACETS:
            raise ValueError("{}: secondary facet '{}' is not a known facet code".format(name, s))
        if s == primary:
            raise ValueError("{}: secondary facet '{}' duplicates the primary facet".format(name, s))
        if s in seen:
            raise ValueError("{}: secondary facet '{}' listed more than once".format(name, s))
        seen.add(s)


def derive(fm, name, allowed_origins=("pack",)):
    # allowed_origins defaults to pack-only so the generator (which sources only pack rules from
    # .aiqt/core/) stays strict; the placement gate passes ("pack", "adopter") because it must accept
    # correctly-placed adopter-authored rules too (spec sections 3 and 4). Origin does not affect the
    # derived path, only which origins are valid.
    for req in ("corpus-id", "origin", "family", "slug"):
        if req not in fm:
            raise ValueError("{}: missing required key '{}'".format(name, req))
    if not CID_RE.match(str(fm["corpus-id"])):
        raise ValueError("{}: corpus-id must match ^[a-z0-9]{{6,}}$".format(name))
    if fm["origin"] not in allowed_origins:
        raise ValueError("{}: origin must be one of {}".format(name, "/".join(allowed_origins)))
    if not SLUG_RE.match(str(fm["slug"])):
        raise ValueError("{}: slug must be kebab-case".format(name))
    for k in SEQ_KEYS:
        if k in fm and not isinstance(fm[k], list):
            raise ValueError("{}: {} must be a flow sequence".format(name, k))
    family = fm["family"]
    if family == "aiqt":
        if fm.get("apex") is True:
            _check_keys(fm, BASE_KEYS | {"apex"}, name)
            if fm["slug"] != "project-integrity":
                raise ValueError("{}: apex slug must be 'project-integrity'".format(name))
            return "aiqt/00-project-integrity.md"
        _check_keys(fm, BASE_KEYS | {"tier", "facet", "secondary"} | MAP_KEYS, name)
        tier = str(fm.get("tier", ""))
        facet = fm.get("facet", "")
        if tier not in TIER_FACETS:
            raise ValueError("{}: tier must be 10/20/30/40".format(name))
        if facet not in TIER_FACETS[tier]:
            raise ValueError("{}: facet '{}' invalid for tier {}".format(name, facet, tier))
        _check_secondary(fm, facet, name)
        return "aiqt/{}-{}-{}.md".format(tier, facet, fm["slug"])
    if family == "security":
        _check_keys(fm, BASE_KEYS | {"facet", "secondary"} | MAP_KEYS, name)
        facet = fm.get("facet", "")
        if facet not in CIA_FACETS:
            raise ValueError("{}: security facet must be SECC/SECI/SECA/SECP".format(name))
        _check_secondary(fm, facet, name)
        return "security/{}-{}.md".format(facet, fm["slug"])
    raise ValueError("{}: unknown family '{}'".format(name, family))


def load_corpus(src_dir):
    """Parse and fully validate every source (schema + unique corpus-id + unique derived path); raise
    ValueError on any malformed or duplicate. Returns [(path, frontmatter, derived_rel_path)]."""
    seen_ids, seen_paths, out = {}, {}, []
    # Collect *.md via os.walk with a raising onerror, NOT rglob: rglob (and a top-level-only
    # ensure_listable) SILENTLY skips an unreadable dir at ANY depth, so an unlistable family subdir
    # (e.g. .aiqt/core/rules/aiqt/) would read as an empty corpus (a false clean). os.walk(onerror=)
    # surfaces the read error at every level, so an unreadable dir fails closed as an OSError.
    def _raise(exc):
        raise exc
    md_files = []
    for dirpath, _dirs, filenames in os.walk(src_dir, onerror=_raise):
        md_files.extend(Path(dirpath) / fn for fn in filenames if fn.endswith(".md"))
    for src in sorted(md_files):
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
        for family in ("aiqt", "security"):
            fam_dir = out_dir / family
            if fam_dir.is_dir():
                for dirpath, _dirs, filenames in os.walk(fam_dir, onerror=_raise):
                    for fn in sorted(f for f in filenames if f.endswith(".md")):
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
        print("run tools/gen_rules.py to regenerate")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
