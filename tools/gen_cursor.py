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
  gen_cursor.py --self-test  assert an invalid-UTF-8 generated .mdc target fails closed (exit 2)
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


def run(root, check):
    """Reconcile the reserved .cursor/rules/aiqt-guardrails/ subtree under root against the
    .aiqt/core/rules/ corpus. Exit 0 in sync, 1 on drift (check mode), 2 on a malformed source or a
    read/write failure. Parameterized on root (rather than calling repo_root() inline) so the self-test
    can drive it against a synthetic tempdir tree, never the real repo."""
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
    except (OSError, UnicodeError) as exc:
        # UnicodeError (UnicodeDecodeError) covers the generated-TARGET read above: a non-UTF-8 .mdc
        # target decodes as UTF-8 there, so a corrupt target fails closed (exit 2) rather than a raw
        # traceback, the same OSError path (a read-only fs, a permission error, a full disk) already
        # fails closed on.
        print("error: {}".format(exc))
        return 2
    if check and drift:
        print("drift: " + "; ".join(drift))
        print("run tools/gen_cursor.py to regenerate")
        return 1
    return 0


def main():
    argv = sys.argv[1:]
    if "--self-test" in argv:
        return self_test_main()
    return run(repo_root(), "--check" in argv)


# --- self-test ----------------------------------------------------------------------------------------
# One focused invariant (the sibling generators' idiom): an invalid-UTF-8 GENERATED .mdc TARGET fails
# closed (exit 2) rather than a raw UnicodeDecodeError traceback. The reconcile loop reads each desired
# target as UTF-8 (the drift compare), so a non-UTF-8 target must be caught by the widened (OSError,
# UnicodeError) arm (F-154). A revert to the narrow OSError-only arm makes run() RAISE instead of
# returning 2, so this case fails and guards the widening. Tempdir-only; never touches a real repo file.

_RULE_SRC = """---
corpus-id: selfc1
origin: pack
family: aiqt
tier: 10
facet: QUALI
slug: gen-cursor-selftest-target
---
# Gen-cursor self-test rule

A minimal rule so the reconcile has one desired .mdc target to read.
"""
_RULE_MDC_REL = "aiqt/10-QUALI-gen-cursor-selftest-target.mdc"


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def run_quiet(root, check):
        # A reverted narrow (OSError-only) arm raises UnicodeDecodeError out of run(); catch it and
        # return a non-int sentinel so it registers as a FAILURE against the expected exit code rather
        # than aborting the self-test or letting it exit early green.
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except Exception as exc:  # noqa: BLE001  a revert surfaces here as UnicodeDecodeError
                return "raised {}".format(type(exc).__name__)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-cursor-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    try:
        # A synthetic corpus (one source rule) so `desired` carries exactly one generated .mdc target,
        # then pre-write that target as invalid UTF-8 bytes so the reconcile's drift-compare read hits it.
        src = tmp / ".aiqt" / "core" / "rules"
        src.mkdir(parents=True)
        (src / "gen-cursor-selftest-target.md").write_text(_RULE_SRC, encoding="utf-8")
        target = tmp.joinpath(*OUT_PARTS) / _RULE_MDC_REL
        target.parent.mkdir(parents=True)
        target.write_bytes(b"\xff\xfe not utf-8")
        if run_quiet(tmp, check=True) != 2:
            failures.append("invalid-UTF-8 generated .mdc target expected exit 2 (fail-closed)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: an invalid-UTF-8 generated .mdc target fails closed (exit 2), not a raw "
          "UnicodeDecodeError traceback (guards the widened reconcile arm).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
