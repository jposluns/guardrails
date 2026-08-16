#!/usr/bin/env python3
"""Validate every standards-mapping id in the rule corpus against its vendored, pinned manifest.

Offline, stdlib only: no network, no dependency outside the repo. This is the crosswalk's no-fabrication
gate. An id a rule cites in a `map-<key>` frontmatter list must appear verbatim in that framework's
manifest under .aiqt/standards/, or CI fails, so a fabricated or typo'd public mapping id cannot ship.
The mapping KEYS are already constrained by gen_rules (MAP_KEYS is derived from the same manifests); this
gate checks the VALUES.

  check_mappings.py    exit 0 clean, 1 mapping finding, 2 malformed manifest or corpus (fail-closed)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402
from _standards import load_manifests, natkey, ManifestError  # noqa: E402
import gen_rules  # noqa: E402  (reuse the one frontmatter parser + full corpus validation)


def main():
    root = repo_root()
    try:
        manifests = load_manifests(root / ".aiqt" / "standards")
    except ManifestError as exc:
        print("error: {}".format(exc))
        return 2
    try:
        corpus = gen_rules.load_corpus(root / ".aiqt" / "core" / "rules")
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2

    findings = []
    mapped = 0
    for src, fm, _rel in corpus:
        for key in sorted(k for k in fm if k.startswith("map-")):
            ids = fm[key]
            if not isinstance(ids, list):
                findings.append("{}: {}: value must be a flow sequence".format(src.name, key))
                continue
            mapped += len(ids)
            manifest = manifests.get(key)
            if manifest is None:
                # Unreachable while MAP_KEYS is derived from the same manifests (load_corpus would reject
                # the key first); kept as a guard against a hand-edited or out-of-sync tree.
                findings.append("{}: {}: no manifest under .aiqt/standards/ for this key".format(
                    src.name, key))
                continue
            if len(ids) != len(set(ids)):
                dupes = sorted({c for c in ids if ids.count(c) > 1})
                findings.append("{}: {}: duplicate id(s): {}".format(src.name, key, ", ".join(dupes)))
            if ids != sorted(ids, key=natkey):
                findings.append("{}: {}: ids must be natural-sorted; got [{}]".format(
                    src.name, key, ", ".join(ids)))
            for cid in ids:
                if not manifest.id_pattern.fullmatch(cid):
                    findings.append("{}: {}: id '{}' is malformed for {} ({})".format(
                        src.name, key, cid, manifest.name, manifest.edition))
                elif cid not in manifest.id_set:
                    findings.append("{}: {}: id '{}' is not in {} {}".format(
                        src.name, key, cid, manifest.name, manifest.edition))

    if findings:
        print("FAIL: {} mapping issue(s):".format(len(findings)))
        for line in sorted(findings):
            print("  {}".format(line))
        return 1
    print("PASS: {} mapping id(s) across {} rule(s) validate against {} manifest(s)".format(
        mapped, sum(1 for _s, fm, _r in corpus if any(k.startswith("map-") for k in fm)), len(manifests)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
