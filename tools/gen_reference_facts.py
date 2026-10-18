#!/usr/bin/env python3
"""Generate the manifest-derived reference facts rendered into the prose READMEs: the framework-roster
block in .aiqt/standards/README.md and the landing-page count blocks in README.md.

The roster used to be a hand-authored sentence ("... (16 in total)") that could silently desync when a
manifest landed or left, and the landing page's counts (rules, CI gates, Claude Code hooks with their
blocking/warning split, the enforced-rules figure, frameworks) were hand-edited on every change that
moved one, so every pull request that added a gate or hook touched the same README lines. Both are now
DERIVED: the roster is rendered from load_manifests, and the landing counts are rendered from
check_reference_facts.expected_counts, THE SAME function that gate recomputes its expectations with, so
the rendered figures and the check can never use two different predicates; the drift check fails when a
committed block is stale. Stdlib only, offline; it mirrors the sibling generators (gen_notice/
gen_mappings): exit 0 clean, exit 1 on drift, exit 2 fail-closed on an unreadable/malformed input.

Both READMEs are Markdown, so the blocks are rewritten with a clean-Markdown marker replace (the
gen_claude precedent: no trailing indent) rather than the HTML-indented shared replace_block, which
would leave the end marker indented as a Markdown code block. The landing page's COUNTS-TABLE block
carries the WHOLE "What is inside" table, its two prose-only rows included: a Markdown table is one
contiguous run of lines, so a marker line inside it would split the rendered table in two.

  gen_reference_facts.py            regenerate the roster and landing-count blocks
  gen_reference_facts.py --check    exit 1 if a block is stale; exit 2 on error (fail-closed)
  gen_reference_facts.py --self-test
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: gen_reference_facts.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile  # noqa: E402
from _standards import load_manifests, ManifestError, natkey  # noqa: E402
from check_reference_facts import expected_counts  # noqa: E402

README_REL = Path(".aiqt") / "standards" / "README.md"
LANDING_REL = Path("README.md")
MARKER = "ROSTER"
# The landing page's two generated blocks: the bold proof line and the "What is inside" table.
PROOF_MARKER = "COUNTS-PROOF"
TABLE_MARKER = "COUNTS-TABLE"

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata only,
# it does not affect what this generator produces. Each is a generated block inside a hand-authored
# Markdown page, so both are recorded as kind block. The landing sources are the surfaces
# expected_counts derives every figure from: the rule corpus, the gates and hooks manifests, the
# standards manifests, and the enforcement register's summary.
GENSRC_OUTPUTS = (
    {"target": ".aiqt/standards/README.md", "kind": "block",
     "sources": (".aiqt/standards/",),
     "regenerate": "python3 tools/gen_reference_facts.py"},
    {"target": "README.md", "kind": "block",
     "sources": (".aiqt/core/rules/", ".aiqt/core/gates/manifest.toml",
                 ".aiqt/core/hooks/manifest.toml", ".aiqt/standards/", "ENFORCEMENT.md"),
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


def render_proof_line(counts):
    """The landing page's bold proof line, rendered as ONE line: check_reference_facts binds the proof
    wording to the first line that opens bold with a digit, so the line may not wrap."""
    return ("**{rules} rules. {gates} CI gates that check this repository. {hooks} Claude Code hooks "
            "shipped as an opt-in plugin. Mapped to {frameworks} frameworks.**").format(**counts)


# The "What is inside" table, one template line per rendered line. Each row that states a figure keeps
# the exact wording check_reference_facts.README_COUNT_FACTS anchors to its table row, so the rendered
# text and the gate's anchored wordings stay one claim; rewording a row here without updating that
# table fails the gate loud (a missing anchored wording is itself a finding).
_INSIDE_TABLE_ROWS = (
    "| Piece | In one breath | Tag |",
    "| --- | --- | --- |",
    "| **The standard** | {rules} clear, single-behaviour rules across the four AIQT facets, plus a "
    "security floor covering confidentiality, integrity, availability, and privacy. | Rules |",
    "| **Technical controls** | {gates} CI gates that check this repository and {hooks} Claude Code "
    "hooks shipped as an opt-in plugin ({blocking} blocking, {warning} warning). Today {enforced} of "
    "the {rules} rules are linked to at least one shipped gate or hook; "
    "[ENFORCEMENT.md](ENFORCEMENT.md) lists each rule's status. | Controls |",
    "| **Generated adapters** | The Claude, AGENTS.md, Gemini, Copilot, and Cursor surfaces are "
    "generated from one source and drift-checked in CI. | Generated |",
    "| **A standards crosswalk** | Mappings from the rules to {frameworks} pinned external control "
    "catalogues, for teams that report against a framework. | Mappings |",
)


def render_inside_table(counts):
    """The landing page's "What is inside" table with every count rendered from expected_counts."""
    return "\n".join(row.format(**counts) for row in _INSIDE_TABLE_ROWS)


def _markers(name):
    return ("<!-- {}:BEGIN (generated) -->".format(name), "<!-- {}:END -->".format(name))


def _replace_marked(text, name, inner, where):
    """Replace the content between the named markers, keeping the markers, with clean Markdown spacing
    (no trailing indent). Fail closed if a marker is missing, misordered, or DUPLICATED: exactly one
    BEGIN and one END must be present. A second block with the same name would otherwise silently bypass
    the drift check, because both generate and --check act on the first marker pair only, so a stale
    duplicate could never be caught or updated. This runs on both paths (the builders feed generate and
    --check alike)."""
    begin, end = _markers(name)
    n_begin = text.count(begin)
    n_end = text.count(end)
    if n_begin != 1 or n_end != 1:
        raise ValueError("{} markers must appear exactly once each in {} "
                         "(found {} begin, {} end); fail-closed".format(name, where, n_begin, n_end))
    i = text.find(begin)
    j = text.find(end)
    if j < i:
        raise ValueError("{} markers misordered in {}".format(name, where))
    return text[:i] + begin + "\n" + inner + "\n" + text[j:]


def build(root, manifests):
    """Return the standards README with its roster block regenerated. Raises ManifestError/OSError/
    ValueError (incl. a UnicodeDecodeError on an invalid-UTF-8 README, a subclass of ValueError) on an
    unreadable or malformed input; the caller maps those to exit 2."""
    text = (root / README_REL).read_text(encoding="utf-8")
    return _replace_marked(text, MARKER, render_roster(manifests), README_REL)


def build_landing(root, manifests):
    """Return the landing README with its two count blocks regenerated from expected_counts (the gate's
    own predicates: .md rule files, "ci" gates, "claude-code" hooks with their block/warn split summing
    to the total, the standards manifests, and the enforcement register's summary row). Raises
    ManifestError/OSError/ValueError on an unreadable or malformed input; the caller maps those to
    exit 2."""
    counts = expected_counts(root, manifests)
    text = (root / LANDING_REL).read_text(encoding="utf-8")
    text = _replace_marked(text, PROOF_MARKER, render_proof_line(counts), LANDING_REL)
    return _replace_marked(text, TABLE_MARKER, render_inside_table(counts), LANDING_REL)


def run(root, check):
    try:
        manifests = load_manifests(root / ".aiqt" / "standards")
        roster_text = build(root, manifests)
        landing_text = build_landing(root, manifests)
    except (ManifestError, OSError, ValueError, KeyError) as exc:
        print("error: cannot build the reference-facts blocks ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2
    drifted = False
    if reconcile(root / README_REL, roster_text, check):
        print("drift: .aiqt/standards/README.md roster is out of date; run tools/gen_reference_facts.py",
              file=sys.stderr)
        drifted = True
    if reconcile(root / LANDING_REL, landing_text, check):
        print("drift: README.md landing-count blocks are out of date; run tools/gen_reference_facts.py",
              file=sys.stderr)
        drifted = True
    if drifted:
        return 1
    if not check:
        print("wrote the .aiqt/standards/README.md roster block and the README.md landing-count blocks")
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run(repo_root(), "--check" in args)


# --- self-test ----------------------------------------------------------------------------------------
# Proves this generator's own invariants against synthetic trees (the sibling-generator pattern), so it
# never becomes an ungated generator:
#   (a) a conformant fixture (2 manifests; 3 rules, 2 ci gates, 2 claude-code hooks split 1/1, 1
#       enforced) generates the right roster AND the right landing counts with the hand prose around the
#       blocks untouched, then re-checks drift-clean,
#   (b) a hand-desynced count in the committed roster block is caught by --check (exit 1),
#   (c) a README missing the ROSTER markers fails closed (exit 2),
#   (d) an empty .aiqt/standards/ dir fails closed (exit 2), never a false-clean empty roster,
#   (e) an invalid-UTF-8 README fails closed (exit 2) rather than a raw traceback,
#   (f) a README with two ROSTER blocks fails closed (exit 2) on BOTH generate and --check, so a
#       duplicate block can never silently bypass the drift check,
#   (g) a manifest change MOVES the rendered landing figure: adding a ci gate to the gates manifest
#       fails --check (exit 1, drift), and regenerating renders the new count and re-checks clean,
#   (h) a hand edit of the rendered landing text is caught by --check (exit 1),
#   (i) a landing README missing the COUNTS-PROOF markers fails closed (exit 2),
#   (j) a landing README missing the COUNTS-TABLE markers fails closed (exit 2),
#   (k) a landing README with duplicated COUNTS-PROOF markers fails closed (exit 2),
#   (l) a missing ENFORCEMENT.md (an expected_counts surface) fails closed (exit 2), never a guessed
#       enforced figure.

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

_LANDING = (
    "# Fixture landing page\n\nHand prose above the counts.\n\n"
    "<!-- COUNTS-PROOF:BEGIN (generated) -->\n<!-- COUNTS-PROOF:END -->\n\n"
    "Hand prose between the blocks.\n\n"
    "<!-- COUNTS-TABLE:BEGIN (generated) -->\n<!-- COUNTS-TABLE:END -->\n\n"
    "Hand prose below the counts.\n"
)

# Three rule files, two ci gates (one other-platform entry proves the platform filter), two claude-code
# hooks split one block / one warn, and an enforcement summary of one enforced rule.
_GATES_TOML = (
    '[[gate]]\nid = "g1"\nplatform = "ci"\n'
    '[[gate]]\nid = "g2"\nplatform = "ci"\n'
    '[[gate]]\nid = "g3"\nplatform = "other"\n'
)
_EXTRA_CI_GATE = '[[gate]]\nid = "g4"\nplatform = "ci"\n'
_HOOKS_TOML = (
    '[[hook]]\nid = "h1"\nplatform = "claude-code"\ndefault = "block"\n'
    '[[hook]]\nid = "h2"\nplatform = "claude-code"\ndefault = "warn"\n'
    '[[hook]]\nid = "h3"\nplatform = "other"\ndefault = "block"\n'
)
_ENFORCEMENT = "# Register\n\n| Status | Count |\n| --- | --- |\n| Enforced | 1 |\n"

_PROOF_TWO_GATES = ("**3 rules. 2 CI gates that check this repository. 2 Claude Code hooks shipped as "
                    "an opt-in plugin. Mapped to 2 frameworks.**")


def _write_fixture(base, manifests, readme, landing=None):
    std = base / ".aiqt" / "standards"
    std.mkdir(parents=True)
    for stem, name, ed in manifests:
        (std / (stem + ".toml")).write_text(_MANIFEST.format(k=stem, name=name, ed=ed), encoding="utf-8")
    (std / "README.md").write_text(readme, encoding="utf-8")
    core = base / ".aiqt" / "core"
    (core / "rules").mkdir(parents=True)
    for stem in ("r1", "r2", "r3"):
        (core / "rules" / (stem + ".md")).write_text("# a fixture rule\n", encoding="utf-8")
    (core / "gates").mkdir()
    (core / "gates" / "manifest.toml").write_text(_GATES_TOML, encoding="utf-8")
    (core / "hooks").mkdir()
    (core / "hooks" / "manifest.toml").write_text(_HOOKS_TOML, encoding="utf-8")
    (base / "ENFORCEMENT.md").write_text(_ENFORCEMENT, encoding="utf-8")
    (base / "README.md").write_text(landing if landing is not None else _LANDING, encoding="utf-8")


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

        # (a) conformant tree generates the right roster and landing counts, then re-checks drift-clean.
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
        landing_text = (good / LANDING_REL).read_text(encoding="utf-8")
        if _PROOF_TWO_GATES not in landing_text:
            failures.append("conformant tree: expected the rendered landing proof line")
        if "(1 blocking, 1 warning)" not in landing_text:
            failures.append("conformant tree: expected the rendered blocking/warning split")
        if "Today 1 of the 3 rules are linked to at least one shipped gate or hook" not in landing_text:
            failures.append("conformant tree: expected the rendered enforcement figure")
        for prose in ("Hand prose above the counts.", "Hand prose between the blocks.",
                      "Hand prose below the counts."):
            if prose not in landing_text:
                failures.append("conformant tree: hand prose around the blocks must stay untouched "
                                "({!r})".format(prose))
        if run_quiet(good, check=True) != 0:
            failures.append("conformant tree: re-check expected drift-clean exit 0")

        # (b) a hand-desynced committed roster block is caught by --check (exit 1).
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

        # (g) a manifest change MOVES the rendered landing figure: a new ci gate makes the committed
        #     block stale (exit 1), and regenerating renders the new count and re-checks clean.
        gatemove = tmp / "gatemove"
        _write_fixture(gatemove, two, _README)
        if run_quiet(gatemove, check=False) != 0:
            failures.append("gate-move tree: initial generation expected exit 0")
        gates_path = gatemove / ".aiqt" / "core" / "gates" / "manifest.toml"
        gates_path.write_text(_GATES_TOML + _EXTRA_CI_GATE, encoding="utf-8")
        if run_quiet(gatemove, check=True) != 1:
            failures.append("adding a ci gate expected exit 1 (the rendered figure must move)")
        if run_quiet(gatemove, check=False) != 0:
            failures.append("gate-move tree: regeneration expected exit 0")
        moved = (gatemove / LANDING_REL).read_text(encoding="utf-8")
        if "3 CI gates that check this repository" not in moved:
            failures.append("gate-move tree: expected the regenerated landing to state 3 CI gates")
        if run_quiet(gatemove, check=True) != 0:
            failures.append("gate-move tree: re-check after regeneration expected exit 0")

        # (h) a hand edit of the rendered landing text is caught by --check (exit 1).
        handedit = tmp / "handedit"
        _write_fixture(handedit, two, _README)
        if run_quiet(handedit, check=False) != 0:
            failures.append("hand-edit tree: initial generation expected exit 0")
        edited = (handedit / LANDING_REL).read_text(encoding="utf-8").replace(
            "2 CI gates that check this repository", "9 CI gates that check this repository")
        (handedit / LANDING_REL).write_text(edited, encoding="utf-8")
        if run_quiet(handedit, check=True) != 1:
            failures.append("hand-edited landing count expected exit 1 (drift)")

        # (i, j) a landing README missing either marker pair fails closed (exit 2).
        noproof = tmp / "noproof"
        _write_fixture(noproof, two, _README, landing=_LANDING.replace(
            "<!-- COUNTS-PROOF:BEGIN (generated) -->\n<!-- COUNTS-PROOF:END -->\n", ""))
        if run_quiet(noproof, check=True) != 2:
            failures.append("landing missing COUNTS-PROOF markers expected exit 2 (fail-closed)")
        notable = tmp / "notable"
        _write_fixture(notable, two, _README, landing=_LANDING.replace(
            "<!-- COUNTS-TABLE:BEGIN (generated) -->\n<!-- COUNTS-TABLE:END -->\n", ""))
        if run_quiet(notable, check=True) != 2:
            failures.append("landing missing COUNTS-TABLE markers expected exit 2 (fail-closed)")

        # (k) duplicated COUNTS-PROOF markers fail closed (exit 2), the same uniqueness guard as (f).
        dupland = tmp / "dupland"
        _write_fixture(dupland, two, _README, landing=_LANDING + (
            "\n<!-- COUNTS-PROOF:BEGIN (generated) -->\n<!-- COUNTS-PROOF:END -->\n"))
        if run_quiet(dupland, check=True) != 2:
            failures.append("duplicated COUNTS-PROOF blocks expected exit 2 (fail-closed)")

        # (l) a missing ENFORCEMENT.md (an expected_counts surface) fails closed (exit 2).
        noenf = tmp / "noenf"
        _write_fixture(noenf, two, _README)
        (noenf / "ENFORCEMENT.md").unlink()
        if run_quiet(noenf, check=True) != 2:
            failures.append("missing ENFORCEMENT.md expected exit 2 (fail-closed)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant tree generates the right framework count, roster and landing "
          "counts with the hand prose untouched and re-checks drift-clean; a hand-desynced roster "
          "count, a hand-edited landing count, and a manifest change that moves a rendered figure each "
          "fail --check (exit 1), and regeneration renders the moved figure; and a README missing the "
          "ROSTER markers, a landing README missing or duplicating a COUNTS marker pair, duplicated "
          "ROSTER blocks (on both generate and --check), an empty standards dir, a missing "
          "ENFORCEMENT.md, and an invalid-UTF-8 README all fail closed (exit 2)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
