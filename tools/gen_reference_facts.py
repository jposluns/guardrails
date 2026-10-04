#!/usr/bin/env python3
"""Generate the framework-roster block in .aiqt/standards/README.md from the live manifests.

The roster used to be a hand-authored sentence ("... (16 in total)") that could silently desync when a
manifest landed or left. It is now DERIVED: the framework count and the canonical name/edition roster are
rendered from load_manifests, and the drift check fails when the committed block is stale. Stdlib only,
offline; it mirrors the sibling generators (gen_notice/gen_mappings): exit 0 clean, exit 1 on drift, exit
2 fail-closed on an unreadable/malformed input.

The README is Markdown, so the block is rewritten with a clean-Markdown marker replace (the gen_claude
precedent: no trailing indent) rather than the HTML-indented shared replace_block, which would leave the
end marker indented as a Markdown code block.

  gen_reference_facts.py            regenerate the roster block in .aiqt/standards/README.md
  gen_reference_facts.py --check    exit 1 if the block is stale; exit 2 on error (fail-closed)
  gen_reference_facts.py --self-test
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile  # noqa: E402
from _standards import load_manifests, ManifestError, natkey  # noqa: E402

README_REL = Path(".aiqt") / "standards" / "README.md"
MARKER = "ROSTER"
BEGIN = "<!-- {}:BEGIN (generated) -->".format(MARKER)
END = "<!-- {}:END -->".format(MARKER)

# Declares this generator's output for the gensrc registry (tools/gen_gensrc.py); additive metadata only,
# it does not affect what this generator produces. The roster is a generated block inside the hand-authored
# standards README, so it is recorded as kind block.
GENSRC_OUTPUTS = (
    {"target": ".aiqt/standards/README.md", "kind": "block",
     "sources": (".aiqt/standards/",),
     "regenerate": "python3 tools/gen_reference_facts.py"},
)


def render_roster(manifests):
    """The roster block body: the framework count plus a canonical name/edition list, ordered by framework
    name. Fail closed on an empty manifest set (a committed source-of-truth that is absent is an integrity
    anomaly, not an empty roster)."""
    if not manifests:
        raise ValueError("no manifests under .aiqt/standards/; fail-closed (no roster to render)")
    ordered = sorted(manifests.values(), key=lambda m: natkey(m.name))
    lines = ["Frameworks with a manifest and enabled key ({} in total):".format(len(manifests)), ""]
    lines += ["- {} (edition {})".format(m.name, m.edition) for m in ordered]
    return "\n".join(lines)


def _replace_roster(text, inner):
    """Replace the content between the ROSTER markers, keeping the markers, with clean Markdown spacing
    (no trailing indent). Fail closed if a marker is missing, misordered, or DUPLICATED: exactly one
    BEGIN and one END must be present. A second roster block would otherwise silently bypass the drift
    check, because both generate and --check act on the first marker pair only, so a stale duplicate could
    never be caught or updated. This runs on both paths (build feeds generate and --check alike)."""
    n_begin = text.count(BEGIN)
    n_end = text.count(END)
    if n_begin != 1 or n_end != 1:
        raise ValueError("ROSTER markers must appear exactly once each in .aiqt/standards/README.md "
                         "(found {} begin, {} end); fail-closed".format(n_begin, n_end))
    i = text.find(BEGIN)
    j = text.find(END)
    if j < i:
        raise ValueError("ROSTER markers misordered in .aiqt/standards/README.md")
    return text[:i] + BEGIN + "\n" + inner + "\n" + text[j:]


def build(root):
    """Return the standards README with its roster block regenerated. Raises ManifestError/OSError/
    ValueError (incl. a UnicodeDecodeError on an invalid-UTF-8 README, a subclass of ValueError) on an
    unreadable or malformed input; the caller maps those to exit 2."""
    manifests = load_manifests(root / ".aiqt" / "standards")
    text = (root / README_REL).read_text(encoding="utf-8")
    return _replace_roster(text, render_roster(manifests))


def run(root, check):
    try:
        text = build(root)
    except (ManifestError, OSError, ValueError, KeyError) as exc:
        print("error: cannot build the standards roster ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    if reconcile(root / README_REL, text, check):
        print("drift: .aiqt/standards/README.md roster is out of date; run tools/gen_reference_facts.py",
              file=sys.stderr)
        return 1
    if not check:
        print("wrote .aiqt/standards/README.md roster block")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run(repo_root(), "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Proves this generator's own invariants against synthetic trees (the sibling-generator pattern), so it
# never becomes an ungated generator:
#   (a) a conformant fixture (2 manifests) generates the right count+roster, then re-checks drift-clean,
#   (b) a hand-desynced count in the committed block is caught by --check (exit 1),
#   (c) a README missing the ROSTER markers fails closed (exit 2),
#   (d) an empty .aiqt/standards/ dir fails closed (exit 2), never a false-clean empty roster,
#   (e) an invalid-UTF-8 README fails closed (exit 2) rather than a raw traceback,
#   (f) a README with two ROSTER blocks fails closed (exit 2) on BOTH generate and --check, so a
#       duplicate block can never silently bypass the drift check.

_MANIFEST = (
    'map-key = "map-{k}"\nname = "{name}"\npublisher = "AIQT self-test"\nedition = "{ed}"\n'
    'kind = "control"\nstatus = "stable"\ncatalogue = "full"\ncitation-unit = "control"\n'
    'id-pattern = "ST[0-9]{{2}}"\nsource-artefact = "self-test fixture"\nretrieved = "2026-01-01"\n'
    '[[id]]\ncode = "ST01"\ntitle = "one"\n'
)

_README = (
    "# Standards id-manifests\n\nSome hand prose.\n\n"
    "<!-- ROSTER:BEGIN (generated) -->\n<!-- ROSTER:END -->\n\nMore hand prose.\n"
)


def _write_fixture(base, manifests, readme):
    std = base / ".aiqt" / "standards"
    std.mkdir(parents=True)
    for stem, name, ed in manifests:
        (std / (stem + ".toml")).write_text(_MANIFEST.format(k=stem, name=name, ed=ed), encoding="utf-8")
    (std / "README.md").write_text(readme, encoding="utf-8")


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    def run_quiet(root, check):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root, check)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-reference-facts-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    try:
        two = [("alpha", "Alpha Framework", "1.0"), ("beta", "Beta Framework", "2.0")]

        # (a) conformant tree generates the right roster, then re-checks drift-clean.
        good = tmp / "good"
        _write_fixture(good, two, _README)
        if run_quiet(good, check=False) != 0:
            failures.append("conformant tree: generation expected exit 0")
        readme_text = (good / README_REL).read_text(encoding="utf-8")
        if "Frameworks with a manifest and enabled key (2 in total):" not in readme_text:
            failures.append("conformant tree: expected the '2 in total' count in the generated roster")
        if "- Alpha Framework (edition 1.0)" not in readme_text \
                or "- Beta Framework (edition 2.0)" not in readme_text:
            failures.append("conformant tree: expected both framework roster lines")
        if run_quiet(good, check=True) != 0:
            failures.append("conformant tree: re-check expected drift-clean exit 0")

        # (b) a hand-desynced committed block is caught by --check (exit 1).
        desynced = (good / README_REL).read_text(encoding="utf-8").replace(
            "(2 in total)", "(9 in total)")
        (good / README_REL).write_text(desynced, encoding="utf-8")
        if run_quiet(good, check=True) != 1:
            failures.append("hand-desynced roster count expected exit 1 (drift)")

        # (c) a README missing the ROSTER markers fails closed (exit 2).
        nomarks = tmp / "nomarks"
        _write_fixture(nomarks, two, "# Standards id-manifests\n\nNo markers here.\n")
        if run_quiet(nomarks, check=True) != 2:
            failures.append("README missing ROSTER markers expected exit 2 (fail-closed)")

        # (d) an empty .aiqt/standards/ dir fails closed (exit 2), never a false-clean empty roster.
        emptydir = tmp / "empty"
        _write_fixture(emptydir, two, _README)
        for f in (emptydir / ".aiqt" / "standards").glob("*.toml"):
            f.unlink()
        if run_quiet(emptydir, check=True) != 2:
            failures.append("empty standards dir expected exit 2 (fail-closed)")

        # (e) an invalid-UTF-8 README fails closed (exit 2) rather than a raw traceback.
        badutf = tmp / "badutf"
        _write_fixture(badutf, two, _README)
        (badutf / README_REL).write_bytes(b"\xff\xfe not valid utf-8 \x80\x81")
        if run_quiet(badutf, check=True) != 2:
            failures.append("invalid-UTF-8 README expected exit 2 (fail-closed)")

        # (f) a README carrying two ROSTER blocks fails closed on BOTH generate and --check. Without the
        #     uniqueness guard a second stale block would silently bypass drift (both paths act only on
        #     the first marker pair).
        dup_readme = (
            "# Standards id-manifests\n\nSome hand prose.\n\n"
            "<!-- ROSTER:BEGIN (generated) -->\n<!-- ROSTER:END -->\n\nMiddle prose.\n\n"
            "<!-- ROSTER:BEGIN (generated) -->\n<!-- ROSTER:END -->\n\nMore hand prose.\n")
        dupgen = tmp / "dupgen"
        _write_fixture(dupgen, two, dup_readme)
        if run_quiet(dupgen, check=False) != 2:
            failures.append("duplicated ROSTER blocks expected exit 2 on generate (fail-closed)")
        dupcheck = tmp / "dupcheck"
        _write_fixture(dupcheck, two, dup_readme)
        if run_quiet(dupcheck, check=True) != 2:
            failures.append("duplicated ROSTER blocks expected exit 2 on --check (fail-closed)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant tree generates the right framework count and roster and "
          "re-checks drift-clean; a hand-desynced count fails --check (exit 1); and a README missing the "
          "ROSTER markers, a README with duplicated ROSTER blocks (on both generate and --check), an "
          "empty standards dir, and an invalid-UTF-8 README all fail closed (exit 2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
