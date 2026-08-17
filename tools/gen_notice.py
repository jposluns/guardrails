#!/usr/bin/env python3
"""Generate the root NOTICE file from .aiqt/attribution.toml plus the live standards manifests.

The pack itself is published under CC BY-SA 4.0 (see the LICENSE file). The pack's crosswalk mappings
reproduce third-party framework control/clause IDENTIFIERS and SHORT TITLES as navigational pointers
only; no specification prose, requirement text, control or clause bodies, figures, or tables are
reproduced. This generator renders the third-party attribution NOTICE from a single checked-in source
of truth so it cannot drift from the vendored manifest set.

Fail-closed contract: every publisher whose manifest is vendored under .aiqt/standards/ MUST have a
verified=true attribution block in .aiqt/attribution.toml, or this exits 2 and refuses to render. A
newly vendored framework therefore cannot ship without a verified attribution first.

  gen_notice.py           regenerate NOTICE
  gen_notice.py --check   exit 1 if NOTICE is out of date; exit 2 on error or an unverified licence
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml, reconcile  # noqa: E402
from _standards import load_manifests, ManifestError, natkey  # noqa: E402

ATTRIB_REL = Path(".aiqt") / "attribution.toml"
WRAP = 98  # wrap prose paragraphs at this column for a readable plain-text NOTICE

INTRO = (
    "This pack is published under the Creative Commons Attribution-ShareAlike 4.0 International "
    "License (CC BY-SA 4.0); see the LICENSE file. That licence covers the pack's own content, "
    "including its crosswalk mappings.\n\n"
    "The crosswalk mappings reference third-party security and AI-governance frameworks. Only control, "
    "clause, and risk IDENTIFIERS and their SHORT TITLES are reproduced, as navigational pointers. No "
    "specification prose, requirement text, control or clause bodies, figures, or tables from any "
    "framework are reproduced. Each framework's identifiers and titles remain the property of their "
    "respective publisher under the terms below, and no publisher listed here endorses, sponsors, or is "
    "affiliated with this pack."
)


def _wrap(text, indent=""):
    """Greedy word-wrap to WRAP columns, preserving blank-line paragraph breaks. Each returned line
    already includes its indent."""
    out = []
    for para in text.split("\n"):
        if not para:
            out.append("")
            continue
        line = ""
        for word in para.split():
            tentative = (indent + word) if not line else (line + " " + word)
            if line and len(tentative) > WRAP:
                out.append(line)
                line = indent + word
            else:
                line = tentative
        if line:
            out.append(line)
    return "\n".join(out)


def build(root):
    """Return the NOTICE text, or raise SystemExit(2) if a vendored publisher is unverified/unattributed."""
    manifests = load_manifests(root / ".aiqt" / "standards")  # raises ManifestError/OSError -> main exits 2
    attrib = load_toml(root / ATTRIB_REL)
    publishers = attrib.get("publisher", {})
    per_manifest = attrib.get("manifest", {})

    groups = {}
    for manifest in manifests.values():
        groups.setdefault(manifest.publisher, []).append(manifest)

    errors = []
    for pub in sorted(groups):
        block = publishers.get(pub)
        if block is None:
            errors.append("no attribution block for vendored publisher {!r}".format(pub))
        elif block.get("verified") is not True:
            errors.append("publisher {!r} attribution is not verified=true".format(pub))
    for stem, mblock in sorted(per_manifest.items()):
        if "licence" in mblock and mblock.get("verified") is not True:
            errors.append("manifest override {!r} changes the licence but is not verified=true".format(stem))
    if errors:
        for err in errors:
            print("error: " + err + "; fail-closed (NOTICE not rendered)", file=sys.stderr)
        raise SystemExit(2)

    lines = ["AIQT Guardrails: third-party attribution NOTICE", ""]
    lines.append(_wrap(INTRO))
    lines.append("")
    lines.append("=" * WRAP)

    for pub in sorted(groups):
        block = publishers[pub]
        lines.append("")
        lines.append(block["legal-name"])
        lines.append("-" * len(block["legal-name"]))
        lines.append("")
        lines.append("Frameworks referenced:")
        for manifest in sorted(groups[pub], key=lambda m: natkey(m.name)):
            stem = manifest.path.stem
            mblock = per_manifest.get(stem)
            line = "  - {} (edition {})".format(manifest.name, manifest.edition)
            if mblock and mblock.get("licence"):
                # This manifest's own licence differs from its publisher default; state it on the line
                # so the group "Licence:" heading below is never read as covering this framework too.
                line += " [licensed {}]".format(mblock["licence"])
            lines.append(line)
            if mblock and mblock.get("note"):
                lines.append(_wrap(mblock["note"], indent="      "))
        lines.append("")
        # If any manifest in this group carries its own licence (rendered above), flag the default
        # heading as not covering those exceptions. Wrapped like the other prose so a long licence URL
        # (CSA, ISO/IEC) stays within WRAP.
        group_has_licence_override = any(
            (per_manifest.get(m.path.stem) or {}).get("licence") for m in groups[pub])
        heading = "Licence: {} ({})".format(block["licence"], block["licence-url"])
        if group_has_licence_override:
            heading += " (per-framework exceptions noted above)"
        lines.append(_wrap(heading))
        lines.append("")
        lines.append(_wrap(block["basis"]))
        lines.append("")
        lines.append(_wrap("Attribution: " + block["attribution"]))
        if block.get("trademark"):
            lines.append("")
            lines.append(_wrap(block["trademark"]))
        if block.get("non-endorsement"):
            lines.append("")
            lines.append(_wrap(block["non-endorsement"]))
        lines.append("")
        lines.append("=" * WRAP)

    return "\n".join(lines).rstrip() + "\n"


def main():
    check = "--check" in sys.argv[1:]
    root = repo_root()
    try:
        text = build(root)
    except (ManifestError, OSError, ValueError, KeyError) as exc:
        print("error: cannot build NOTICE ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if reconcile(root / "NOTICE", text, check):
        print("drift: NOTICE is out of date; run tools/gen_notice.py", file=sys.stderr)
        return 1
    if not check:
        print("wrote NOTICE ({} publisher block(s))".format(text.count("=" * WRAP) - 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
