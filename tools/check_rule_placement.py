#!/usr/bin/env python3
"""Placement gate for .claude/rules/ (corpus-management spec section 6, core violation codes).

Validates the read tree's STRUCTURE and NAMING independently of the generator: every rule file lives in a
recognized family with a name that both fits its family's derivation shape AND matches what its frontmatter
derives, none loose or misfiled, the apex is standard, and each vendored external/<source>/ carries its
provenance. Check-only; never writes.
  check_rule_placement.py [--root DIR]   exit 0 clean, 1 violations, 2 usage/read error

Emits the deterministic section 6.6 codes: loose, unknown-family, nested-dir, bad-name,
tier-code-mismatch, numbered-non-aiqt, apex-nonstandard, frontmatter-drift, missing-provenance. Each
violation line is `<relpath>: <code>: <detail>` per section 6.2. The adopter .corpus-config.yml support
(6.4/6.5 families and metadata, 6.7 pending: tolerance) and the pack-drift WARNING treatment (6.6) serve
the not-yet-built adopter cleanup workflow and are deferred (recorded as GD-9); this gate runs fail-closed
on the built-in aiqt/security/external families and accepts both pack- and adopter-origin rules.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_rules import parse_source, derive  # noqa: E402

METADATA = {"PROVENANCE.md", "README.md"}
BUILTIN_FAMILIES = {"aiqt", "security", "external"}

# The fixed AIQT facet-to-tier map (spec 6.6 tier-code-mismatch).
AIQT_TIER_OF = {
    "ACCUR": "10", "INTEG": "10", "QUALI": "10", "TRUST": "10",
    "PROGR": "20", "SPEED": "30", "COST": "40",
}
_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
# aiqt non-apex derivation shape <tier>-<facet>-<slug>.md; any tier with any AIQT facet is
# lexically well-formed (tier/facet consistency is checked separately as tier-code-mismatch).
AIQT_NONAPEX_RE = re.compile(r"^(10|20|30|40)-(ACCUR|INTEG|QUALI|TRUST|PROGR|SPEED|COST)-" + _SLUG + r"\.md$")
# security derivation shape <facet>-<slug>.md.
SECURITY_RE = re.compile(r"^(SECC|SECI|SECA|SECP)-" + _SLUG + r"\.md$")


def repo_root_default():
    p = Path.cwd().resolve()
    return next((a for a in [p, *p.parents] if (a / ".git").exists()), p)


def check_name(family, name):
    """Return a (code, detail) lexical violation for the file NAME alone, or None if well-formed.

    Name-only checks (no frontmatter): numbered-non-aiqt, apex-nonstandard, bad-name, tier-code-mismatch.
    """
    if family != "aiqt" and re.match(r"\d+-", name):
        return ("numbered-non-aiqt", "a leading digit-dash prefix is reserved for aiqt/")
    if family == "aiqt":
        if name.startswith("00-"):
            if name == "00-project-integrity.md":
                return None
            return ("apex-nonstandard", "only 00-project-integrity.md may take the 00- prefix")
        m = AIQT_NONAPEX_RE.match(name)
        if not m:
            return ("bad-name", "does not match the aiqt <tier>-<facet>-<slug>.md shape")
        tier, facet = m.group(1), m.group(2)
        if AIQT_TIER_OF[facet] != tier:
            return ("tier-code-mismatch", "facet {} belongs to tier {}, not {}".format(facet, AIQT_TIER_OF[facet], tier))
        return None
    if family == "security":
        if SECURITY_RE.match(name):
            return None
        return ("bad-name", "does not match the security <facet>-<slug>.md shape")
    return None


def check_drift(f, rules):
    """Return a (code, detail) frontmatter-drift finding for file f, or None. Raises OSError on read."""
    try:
        fm = parse_source(f)
    except ValueError as exc:
        return ("frontmatter-drift", str(exc))
    try:
        want = derive(fm, f.name, ("pack", "adopter"))
    except ValueError as exc:
        return ("frontmatter-drift", str(exc))
    if str(f.relative_to(rules)) != want:
        return ("frontmatter-drift", "derives {}".format(want))
    return None


def main():
    args = sys.argv[1:]
    root = None
    i = 0
    while i < len(args):
        if args[i] == "--root" and i + 1 < len(args):
            root = Path(args[i + 1])
            i += 2
        else:
            print("usage: check_rule_placement.py [--root DIR]", file=sys.stderr)
            return 2
    root = (root or repo_root_default()).resolve()
    rules = root / ".claude" / "rules"
    if not rules.is_dir():
        print("PASS: no .claude/rules/ tree")
        return 0
    findings = []

    def report(path, code, detail):
        findings.append("{}: {}: {}".format(path.relative_to(root), code, detail))

    try:
        for item in sorted(rules.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_file():
                if item.suffix == ".md" and item.name not in METADATA:
                    report(item, "loose", "a rule file at the tree root, not on the metadata allowlist")
                continue
            family = item.name
            if family not in BUILTIN_FAMILIES:
                report(item, "unknown-family", "not a registered family directory")
                continue
            if family == "external":
                for sub in sorted(p for p in item.iterdir() if p.is_dir()):
                    if not (sub / "PROVENANCE.md").exists() or not (sub / "LICENSE").exists():
                        report(sub, "missing-provenance", "a vendored source lacks PROVENANCE.md or LICENSE")
                continue
            # aiqt and security are FLAT families (spec 6.3): only *.md directly in the family dir.
            # Any nested directory is a nested-dir violation (empty or not); dotfiles and non-md files
            # are out of scope and skipped. iterdir (not rglob) surfaces a read error as exit 2.
            for entry in sorted(item.iterdir()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    report(entry, "nested-dir", "a flat family admits no subdirectory")
                    continue
                if entry.suffix != ".md":
                    continue
                nc = check_name(family, entry.name)
                if nc:
                    report(entry, nc[0], nc[1])
                    continue
                drift = check_drift(entry, rules)
                if drift:
                    report(entry, drift[0], drift[1])
    except OSError as exc:
        print("error: cannot read the rules tree: {}".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} placement violation(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: rule placement conforms to the taxonomy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
