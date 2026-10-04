#!/usr/bin/env python3
"""Single-source the secsec hook's secret patterns from tools/check_secrets.py (EN-5 PR-C).

tools/check_secrets.py is the SINGLE SOURCE OF TRUTH for the secret shapes: PREFIXES (a list of
(compiled_regex, label) provider-token shapes), ASSIGN (a credential-named assignment), and PLACEHOLDER
(an obvious non-secret). The secrets-shift-left PreToolUse hook in
.aiqt/core/hooks/scripts/aiqt_hooks.py must apply the SAME shapes, but the shipped plugin is standalone
and stdlib-only, so it can neither fork the regexes (they would drift) nor runtime-import check_secrets
(an authoring-tree module the plugin never carries). This generator renders the pattern SOURCE STRINGS
plus labels as plain Python literals into a sentinelled GENERATED REGION in aiqt_hooks.py; the hook
compiles them at module load. The region is drift-gated, so the source of truth stays single.

  gen_secret_patterns.py           rewrite the generated region in aiqt_hooks.py from check_secrets.py
  gen_secret_patterns.py --check   fail (exit 1) on drift; exit 2 on an unreadable source or target

Order matters in the pipeline: run this BEFORE tools/gen_hooks.py, so the region is up to date in the
source aiqt_hooks.py before gen_hooks.py copies that file byte-identical into the plugin surface.

Fail-closed posture mirrors gen_hooks.py: an unreadable check_secrets.py (import failure) or an
unreadable/unwritable aiqt_hooks.py exits 2, never a silent no-op. Deterministic by construction: the
PREFIXES order is check_secrets.py's own list order, and each pattern and label is rendered with repr(),
a stable canonical Python literal, so a second run makes no change.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root  # noqa: E402

# The source of truth and the render target, repo-root-relative.
SOURCE_REL = "tools/check_secrets.py"
TARGET_REL = ".aiqt/core/hooks/scripts/aiqt_hooks.py"

# The sentinels bounding the generated region. Both lines are preserved; only the body between them is
# rewritten. The BEGIN line names the source and this tool so a reader of aiqt_hooks.py knows the region
# is generated and never hand-edited.
BEGIN = ("# BEGIN generated secret patterns (source: tools/check_secrets.py; regenerate with "
         "tools/gen_secret_patterns.py)")
END = "# END generated secret patterns"

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata
# only, it does not affect what this generator produces. The target is a generated block inside the
# hand-authored aiqt_hooks.py (the sentinelled secret-patterns region), so it is recorded as kind block.
GENSRC_OUTPUTS = (
    {"target": ".aiqt/core/hooks/scripts/aiqt_hooks.py", "kind": "block",
     "sources": ("tools/check_secrets.py",), "regenerate": "python3 tools/gen_secret_patterns.py"},
)


def _load_check_secrets(root):
    """Import tools/check_secrets.py and return a dict of the single-sourced pattern material: the
    (raw_pattern_string, label) list for PREFIXES, the raw .pattern strings of ASSIGN, PLACEHOLDER, and
    (GD-121) ENTROPY_ASSIGN, the entropy length bounds and threshold, and the credential/metadata
    component sets rendered as SORTED TUPLES for determinism. An import failure (an unreadable or broken
    source) raises, and run() maps it to exit 2, so the generator never renders a region from a source it
    could not read."""
    tools_dir = str(root / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import check_secrets  # ImportError/OSError -> caller's fail-closed try
    prefix_sources = [(pattern.pattern, label) for pattern, label in check_secrets.PREFIXES]
    return {
        "prefix_sources": prefix_sources,
        "assign_source": check_secrets.ASSIGN.pattern,
        "placeholder_source": check_secrets.PLACEHOLDER.pattern,
        "entropy_assign_source": check_secrets.ENTROPY_ASSIGN.pattern,
        "entropy_min_len": check_secrets.ENTROPY_MIN_LEN,
        "entropy_max_len": check_secrets.ENTROPY_MAX_LEN,
        "entropy_threshold": check_secrets.ENTROPY_THRESHOLD,
        "credential_components": tuple(sorted(check_secrets.CREDENTIAL_COMPONENTS)),
        "metadata_components": tuple(sorted(check_secrets.METADATA_COMPONENTS)),
    }


def render_region(patterns):
    """The full generated region as text, sentinels included, deterministic. Each pattern string and
    label is emitted with repr() (a stable, valid, re-compilable Python literal); the PREFIXES order is
    check_secrets.py's own list order; frozensets render as SORTED tuples (this generator's determinism
    contract). The hook compiles _SECSEC_*_SOURCE at module load and reads the entropy constants/sets for
    its hand-mirrored decision logic."""
    lines = [BEGIN, "_SECSEC_PREFIX_SOURCES = ["]
    for pattern, label in patterns["prefix_sources"]:
        lines.append("    ({}, {}),".format(repr(pattern), repr(label)))
    lines.append("]")
    lines.append("_SECSEC_ASSIGN_SOURCE = {}".format(repr(patterns["assign_source"])))
    lines.append("_SECSEC_PLACEHOLDER_SOURCE = {}".format(repr(patterns["placeholder_source"])))
    lines.append("_SECSEC_ENTROPY_ASSIGN_SOURCE = {}".format(repr(patterns["entropy_assign_source"])))
    lines.append("_SECSEC_ENTROPY_MIN_LEN = {}".format(repr(patterns["entropy_min_len"])))
    lines.append("_SECSEC_ENTROPY_MAX_LEN = {}".format(repr(patterns["entropy_max_len"])))
    lines.append("_SECSEC_ENTROPY_THRESHOLD = {}".format(repr(patterns["entropy_threshold"])))
    lines.append("_SECSEC_CREDENTIAL_COMPONENTS = {}".format(repr(patterns["credential_components"])))
    lines.append("_SECSEC_METADATA_COMPONENTS = {}".format(repr(patterns["metadata_components"])))
    lines.append(END)
    return "\n".join(lines)


def _splice(text, region):
    """Replace the on-disk region (BEGIN..END inclusive) with the rendered region. Raises ValueError
    when a sentinel is missing, duplicated, or out of order, so a mangled, removed, or multi-region file
    fails closed rather than appending a second copy, writing nothing, or splicing only the first of
    several regions. Requiring EXACTLY ONE BEGIN and EXACTLY ONE END is what stops a second BEGIN..END
    block (which text.find would never inspect) from overriding the patterns undetected: both the regen
    path and the --check path reach the region through this function, so the count guard covers both."""
    begin_count = text.count(BEGIN)
    if begin_count != 1:
        raise ValueError("expected exactly one BEGIN sentinel in {}, found {}".format(TARGET_REL,
                                                                                      begin_count))
    end_count = text.count(END)
    if end_count != 1:
        raise ValueError("expected exactly one END sentinel in {}, found {}".format(TARGET_REL,
                                                                                    end_count))
    i = text.find(BEGIN)
    if i == -1:
        raise ValueError("BEGIN sentinel not found in {}".format(TARGET_REL))
    j = text.find(END, i)
    if j == -1:
        raise ValueError("END sentinel not found after BEGIN in {}".format(TARGET_REL))
    j_end = j + len(END)
    return text[:i] + region + text[j_end:]


def run(root, check):
    """Render the region into aiqt_hooks.py, or (check mode) report drift. Fail-closed (exit 2) on any
    unreadable source or unreadable/unwritable target, mirroring gen_hooks.py's posture."""
    target = root / TARGET_REL
    try:
        patterns = _load_check_secrets(root)
        current = target.read_text(encoding="utf-8")
        region = render_region(patterns)
        desired = _splice(current, region)
    except (OSError, ValueError, ImportError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    if current == desired:
        return 0
    if check:
        print("drift: the generated secret-pattern region in {} is out of date".format(TARGET_REL))
        print("run tools/gen_secret_patterns.py to regenerate")
        return 1
    try:
        target.write_text(desired, encoding="utf-8")
    except OSError as exc:
        print("error: cannot write {} ({}); fail-closed".format(target, exc), file=sys.stderr)
        return 2
    return 0


def main():
    return run(repo_root(), "--check" in sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
