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
from _standards import load_manifests, validate_mappings, ManifestError  # noqa: E402
import gen_rules  # noqa: E402  (reuse the one frontmatter parser + full corpus validation)


def main():
    root = repo_root()
    try:
        manifests = load_manifests(root / ".aiqt" / "standards")
    except (ManifestError, OSError) as exc:
        # OSError too: an unreadable manifest file, or an existing-but-unlistable standards dir
        # (load_manifests raises rather than reading it as empty), is a read error, not a clean skip.
        # Fail closed (exit 2) rather than escape as a traceback, matching conformance.py's C4.
        print("error: {}".format(exc))
        return 2
    src_dir = root / ".aiqt" / "core" / "rules"
    if not src_dir.is_dir():
        # Fail closed: an absent corpus must not read as "0 mappings, all clean". rglob on a missing
        # dir yields nothing, which would otherwise pass; make the anomaly explicit.
        print("error: rule corpus not found at {}".format(src_dir))
        return 2
    try:
        corpus = gen_rules.load_corpus(src_dir)
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc))
        return 2

    findings, mapped, tight, broad = validate_mappings(corpus, manifests)

    if findings:
        print("FAIL: {} mapping issue(s):".format(len(findings)))
        for line in sorted(findings):
            print("  {}".format(line))
        return 1
    n_rules = sum(1 for _s, fm, _r in corpus if any(k.startswith("map-") for k in fm))
    print("PASS: {} mapping id(s): {} tight, {} broad, across {} rule(s), validate against "
          "{} manifest(s)".format(mapped, tight, broad, n_rules, len(manifests)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
