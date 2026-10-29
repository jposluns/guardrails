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
carries the WHOLE "What is inside" table, its prose-only row included: a Markdown table is one
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
from _gen_common import linked_dir_failures, repo_root, reconcile, target_dir_present  # noqa: E402
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


def _marked_span(text, name, where):
    """Locate the named marker pair and return (BEGIN marker start, END marker start). Fail closed if a
    marker is missing, misordered, or DUPLICATED: exactly one BEGIN and one END must be present. A
    second block with the same name would otherwise silently bypass the drift check, because both
    generate and --check act on the first marker pair only, so a stale duplicate could never be caught
    or updated. This runs on both paths (the builders feed generate and --check alike)."""
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
    return i, j


def _require_disjoint(text, names, where):
    """The named marker blocks must be separate, non-overlapping regions of the page. A block nested
    inside another block, or two blocks that cross, passes each pair's own marker checks and is then
    destroyed by the other block's replacement (replacing the outer block rewrites everything between
    its markers, the inner pair included), so generation would report success while deleting a managed
    block. Checked against the text as committed, BEFORE any replacement, so both generate and --check
    fail closed with a diagnostic that names the overlapping blocks, never a marker count measured
    after a replacement has already consumed a marker."""
    spans = []
    for name in names:
        i, j = _marked_span(text, name, where)
        spans.append((name, i, j + len(_markers(name)[1])))
    for pos, (a, a_start, a_stop) in enumerate(spans):
        for b, b_start, b_stop in spans[pos + 1:]:
            if a_start < b_stop and b_start < a_stop:
                raise ValueError("{} and {} marker blocks nest or cross in {}; each generated block "
                                 "must be a separate region; fail-closed".format(a, b, where))


def _replace_marked(text, name, inner, where):
    """Replace the content between the named markers, keeping the markers, with clean Markdown spacing
    (no trailing indent); _marked_span supplies the fail-closed marker validation."""
    begin, end = _markers(name)
    i, j = _marked_span(text, name, where)
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
    ManifestError/OSError/ValueError on an unreadable or malformed input, and on marker blocks that
    nest or cross (validated against the committed text before either replacement); the caller maps
    those to exit 2."""
    counts = expected_counts(root, manifests)
    text = (root / LANDING_REL).read_text(encoding="utf-8")
    _require_disjoint(text, (PROOF_MARKER, TABLE_MARKER), LANDING_REL)
    text = _replace_marked(text, PROOF_MARKER, render_proof_line(counts), LANDING_REL)
    return _replace_marked(text, TABLE_MARKER, render_inside_table(counts), LANDING_REL)


def run(root, check):
    # The directory probe reconcile runs (target_dir_present), for both pages before anything is read: an
    # .aiqt/standards/ that is there but cannot be read as a directory inside the repository exits 2 here, so
    # neither its README nor the manifests beside it are read through it.
    for rel in (README_REL, LANDING_REL):
        target_dir_present(root / rel, root)
    try:
        manifests = load_manifests(root / ".aiqt" / "standards", root=root)
        roster_text = build(root, manifests)
        landing_text = build_landing(root, manifests)
    except (ManifestError, OSError, ValueError, KeyError) as exc:
        print("error: cannot build the reference-facts blocks ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2
    drifted = False
    if reconcile(root / README_REL, roster_text, check, root):
        print("drift: .aiqt/standards/README.md roster is out of date; run tools/gen_reference_facts.py",
              file=sys.stderr)
        drifted = True
    if reconcile(root / LANDING_REL, landing_text, check, root):
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
#   (a) a conformant fixture (2 manifests; 6 rules, 5 ci gates, 7 claude-code hooks split 4/3, 1
#       enforced: every fixture count distinct, so a figure rendered into the wrong slot or two swapped
#       count keys can never collide into a passing value) generates the right roster AND every landing
#       figure in both blocks with the hand prose around the blocks untouched, then re-checks
#       drift-clean,
#   (b) a hand-desynced count in the committed roster block is caught by --check (exit 1),
#   (c) a README missing the ROSTER markers fails closed (exit 2),
#   (d) an empty .aiqt/standards/ dir fails closed (exit 2), never a false-clean empty roster,
#   (e) an invalid-UTF-8 README fails closed (exit 2) rather than a raw traceback,
#   (f) a README with two ROSTER blocks fails closed (exit 2) on BOTH generate and --check, so a
#       duplicate block can never silently bypass the drift check,
#   (g) a manifest change MOVES the rendered landing figure: adding a ci gate to the gates manifest
#       fails --check (exit 1, drift), and regenerating renders the new count into BOTH blocks (proof
#       line and table row, asserted literally) and re-checks clean; then EVERY count is moved at once
#       to a new, still-distinct value and each moved figure is asserted literally in the proof line,
#       the table and the roster, so a renderer that froze any single figure to a literal of the base
#       fixture value fails here rather than passing on a coincidentally-right value,
#   (h) a hand edit of the rendered landing text is caught by --check (exit 1),
#   (i) a landing README missing the COUNTS-PROOF markers fails closed (exit 2),
#   (j) a landing README missing the COUNTS-TABLE markers fails closed (exit 2),
#   (k) a landing README with duplicated COUNTS-PROOF markers fails closed (exit 2),
#   (l) a missing ENFORCEMENT.md (an expected_counts surface) fails closed (exit 2), never a guessed
#       enforced figure,
#   (m) a landing README whose marker blocks nest (either direction) or cross (either order) fails
#       closed (exit 2) on BOTH generate and --check with a diagnostic NAMING both overlapping blocks
#       and the overlap (never a marker count measured after a replacement), and the failed run writes
#       neither README (the landing page or the standards README, whose roster is reconciled by the
#       same run),
#   (n) a landing README with misordered COUNTS-PROOF markers (END before BEGIN) fails closed (exit 2)
#       on both generate and --check, the failed generate again writing neither README,
#   (o) disjoint blocks that merely TOUCH (the proof END marker immediately followed by the table
#       BEGIN marker) or sit in swapped order still generate and re-check clean (exit 0): the overlap
#       test is a strict interval intersection, so touching is the accepted boundary case between the
#       refused overlaps of (m) and the prose-separated standard fixture, and a guard tightened into
#       refusing touching blocks fails here instead of over-refusing a legal page.

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

# Six rule files, five ci gates (one other-platform entry proves the platform filter), seven
# claude-code hooks split four block / three warn (one other-platform entry again), and an enforcement
# summary of one enforced rule. Every fixture count is distinct (enforced 1, frameworks 2, warning 3,
# blocking 4, gates 5, rules 6, hooks 7), so a figure rendered into the wrong slot or two swapped count
# keys can never collide into a passing value.
_GATES_TOML = (
    '[[gate]]\nid = "g1"\nplatform = "ci"\n'
    '[[gate]]\nid = "g2"\nplatform = "ci"\n'
    '[[gate]]\nid = "g3"\nplatform = "ci"\n'
    '[[gate]]\nid = "g4"\nplatform = "ci"\n'
    '[[gate]]\nid = "g5"\nplatform = "ci"\n'
    '[[gate]]\nid = "g6"\nplatform = "other"\n'
)
_EXTRA_CI_GATE = '[[gate]]\nid = "g7"\nplatform = "ci"\n'
_HOOKS_TOML = (
    '[[hook]]\nid = "h1"\nplatform = "claude-code"\ndefault = "block"\n'
    '[[hook]]\nid = "h2"\nplatform = "claude-code"\ndefault = "block"\n'
    '[[hook]]\nid = "h3"\nplatform = "claude-code"\ndefault = "block"\n'
    '[[hook]]\nid = "h4"\nplatform = "claude-code"\ndefault = "block"\n'
    '[[hook]]\nid = "h5"\nplatform = "claude-code"\ndefault = "warn"\n'
    '[[hook]]\nid = "h6"\nplatform = "claude-code"\ndefault = "warn"\n'
    '[[hook]]\nid = "h7"\nplatform = "claude-code"\ndefault = "warn"\n'
    '[[hook]]\nid = "h8"\nplatform = "other"\ndefault = "block"\n'
)
_ENFORCEMENT = (
    "# Register\n\n| Status | Count |\n| --- | --- |\n"
    "| Gate-linked (class a) | 1 |\n| Gate-linked (class a), partial | 0 |\n"
    "| Gate-linked (class c only) | 0 |\n| Gate-linked (class c only), partial | 0 |\n"
    "| Hook-linked | 0 |\n| Hook-linked, partial | 0 |\n"
)

# Every rendered landing figure, asserted as a LITERAL expectation (never built with the live
# templates or renderers, which a regression under test would share): the whole proof line, then one
# literal per anchored table wording, together covering all twelve rendered figure positions. Every
# literal starts at FIXED text to the left of its first figure (the controls row at its cell opener),
# so a renderer that glues a digit onto the front of a figure cannot still contain the literal.
_PROOF_RENDERED = ("**6 rules. 5 CI gates that check this repository. 7 Claude Code hooks shipped as "
                   "an opt-in plugin. Mapped to 2 frameworks.**")
_TABLE_RENDERED = (
    "| **The standard** | 6 clear, single-behaviour rules",
    "| **Technical controls** | 5 CI gates that check this repository and 7 Claude Code hooks "
    "shipped as an opt-in plugin (4 blocking, 3 warning)",
    "Today 1 of the 6 rules are linked to at least one shipped gate or hook",
    "Mappings from the rules to 2 pinned external control catalogues",
)


def _write_fixture(base, manifests, readme, landing=None):
    std = base / ".aiqt" / "standards"
    std.mkdir(parents=True)
    for stem, name, ed in manifests:
        (std / (stem + ".toml")).write_text(_MANIFEST.format(k=stem, name=name, ed=ed), encoding="utf-8")
    (std / "README.md").write_text(readme, encoding="utf-8")
    core = base / ".aiqt" / "core"
    (core / "rules").mkdir(parents=True)
    for stem in ("r1", "r2", "r3", "r4", "r5", "r6"):
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

    def run_captured(root, check):
        """Run quietly and return (exit code, captured stderr text): the overlap vectors assert the
        diagnostic wording as well as the code, so a refusal that regressed into the misleading
        post-replacement marker-count message could not keep passing on its exit code alone."""
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                code = run(root, check)
            except SystemExit as exc:
                code = "raised SystemExit({!r})".format(exc.code)
        return code, err.getvalue()

    def run_quiet(root, check):
        return run_captured(root, check)[0]

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
        if _PROOF_RENDERED not in landing_text:
            failures.append("conformant tree: expected the rendered landing proof line")
        for rendered in _TABLE_RENDERED:
            if rendered not in landing_text:
                failures.append("conformant tree: expected the rendered table figure "
                                "({!r})".format(rendered))
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
        if ("**6 rules. 6 CI gates that check this repository. 7 Claude Code hooks shipped as "
                "an opt-in plugin. Mapped to 2 frameworks.**") not in moved:
            failures.append("gate-move tree: expected the regenerated proof line to state 6 CI gates")
        if ("| **Technical controls** | 6 CI gates that check this repository and 7 Claude Code "
                "hooks shipped as an opt-in plugin (4 blocking, 3 warning)") not in moved:
            failures.append("gate-move tree: expected the regenerated table row to state 6 CI gates")
        if run_quiet(gatemove, check=True) != 0:
            failures.append("gate-move tree: re-check after regeneration expected exit 0")
        # Then EVERY count moves at once to a new, still-distinct value (enforced 2, frameworks 3,
        # warning 5, blocking 6, gates 7, rules 9, hooks 11): a renderer that froze any figure to a
        # literal of the base fixture value, in either landing block or the roster, fails the
        # post-move assertions.
        gates_path.write_text(_GATES_TOML + _EXTRA_CI_GATE
                              + '[[gate]]\nid = "g8"\nplatform = "ci"\n', encoding="utf-8")
        for stem in ("r7", "r8", "r9"):
            (gatemove / ".aiqt" / "core" / "rules" / (stem + ".md")).write_text(
                "# a fixture rule\n", encoding="utf-8")
        (gatemove / ".aiqt" / "core" / "hooks" / "manifest.toml").write_text(_HOOKS_TOML + (
            '[[hook]]\nid = "h9"\nplatform = "claude-code"\ndefault = "block"\n'
            '[[hook]]\nid = "h10"\nplatform = "claude-code"\ndefault = "block"\n'
            '[[hook]]\nid = "h11"\nplatform = "claude-code"\ndefault = "warn"\n'
            '[[hook]]\nid = "h12"\nplatform = "claude-code"\ndefault = "warn"\n'), encoding="utf-8")
        (gatemove / ".aiqt" / "standards" / "gamma.toml").write_text(
            _MANIFEST.format(k="gamma", name="Gamma Framework", ed="3.0"), encoding="utf-8")
        (gatemove / "ENFORCEMENT.md").write_text(
            "# Register\n\n| Status | Count |\n| --- | --- |\n"
            "| Gate-linked (class a) | 1 |\n| Gate-linked (class a), partial | 0 |\n"
            "| Gate-linked (class c only) | 0 |\n| Gate-linked (class c only), partial | 0 |\n"
            "| Hook-linked | 1 |\n| Hook-linked, partial | 0 |\n", encoding="utf-8")
        if run_quiet(gatemove, check=True) != 1:
            failures.append("all-counts move expected exit 1 (every rendered figure must move)")
        if run_quiet(gatemove, check=False) != 0:
            failures.append("all-counts move: regeneration expected exit 0")
        moved = (gatemove / LANDING_REL).read_text(encoding="utf-8")
        for rendered in (
                "**9 rules. 7 CI gates that check this repository. 11 Claude Code hooks shipped as "
                "an opt-in plugin. Mapped to 3 frameworks.**",
                "| **The standard** | 9 clear, single-behaviour rules",
                "| **Technical controls** | 7 CI gates that check this repository and 11 Claude "
                "Code hooks shipped as an opt-in plugin (6 blocking, 5 warning)",
                "Today 2 of the 9 rules are linked to at least one shipped gate or hook",
                "Mappings from the rules to 3 pinned external control catalogues",
        ):
            if rendered not in moved:
                failures.append("all-counts move: expected the regenerated figure "
                                "({!r})".format(rendered))
        roster_moved = (gatemove / README_REL).read_text(encoding="utf-8")
        if "Frameworks with a manifest and enabled key (3 in total):" not in roster_moved \
                or "- Gamma Framework (edition 3.0)" not in roster_moved:
            failures.append("all-counts move: expected the regenerated roster to state 3 frameworks")
        if run_quiet(gatemove, check=True) != 0:
            failures.append("all-counts move: re-check after regeneration expected exit 0")

        # (h) a hand edit of the rendered landing text is caught by --check (exit 1).
        handedit = tmp / "handedit"
        _write_fixture(handedit, two, _README)
        if run_quiet(handedit, check=False) != 0:
            failures.append("hand-edit tree: initial generation expected exit 0")
        edited = (handedit / LANDING_REL).read_text(encoding="utf-8").replace(
            "5 CI gates that check this repository", "9 CI gates that check this repository")
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

        # (m) nested or crossing landing marker blocks fail closed (exit 2) on BOTH generate and
        #     --check, and a failed run writes nothing: without the disjointness guard the outer
        #     block's replacement deletes the inner pair after that pair has already validated and
        #     rendered, so generation reports success while destroying a managed block. Three layouts
        #     already exited 2 WITHOUT the guard, through the misleading marker-count error a
        #     replacement leaves behind, so the exit code alone cannot discriminate them: each run
        #     must also emit the overlap diagnostic that names BOTH blocks, and must leave BOTH
        #     outputs (the landing page and the standards README, whose roster the same run
        #     reconciles) byte-identical.
        proof_pair = "<!-- COUNTS-PROOF:BEGIN (generated) -->\n<!-- COUNTS-PROOF:END -->"
        table_pair = "<!-- COUNTS-TABLE:BEGIN (generated) -->\n<!-- COUNTS-TABLE:END -->"
        overlapping = (
            ("table-wraps-proof", "Prose.\n\n<!-- COUNTS-TABLE:BEGIN (generated) -->\n"
             + proof_pair + "\n<!-- COUNTS-TABLE:END -->\n\nProse.\n"),
            ("proof-wraps-table", "Prose.\n\n<!-- COUNTS-PROOF:BEGIN (generated) -->\n"
             + table_pair + "\n<!-- COUNTS-PROOF:END -->\n\nProse.\n"),
            ("crossing", "Prose.\n\n<!-- COUNTS-PROOF:BEGIN (generated) -->\n"
             "<!-- COUNTS-TABLE:BEGIN (generated) -->\n<!-- COUNTS-PROOF:END -->\n"
             "<!-- COUNTS-TABLE:END -->\n\nProse.\n"),
            ("crossing-reversed", "Prose.\n\n<!-- COUNTS-TABLE:BEGIN (generated) -->\n"
             "<!-- COUNTS-PROOF:BEGIN (generated) -->\n<!-- COUNTS-TABLE:END -->\n"
             "<!-- COUNTS-PROOF:END -->\n\nProse.\n"),
        )
        for label, landing in overlapping:
            overlap = tmp / ("overlap-" + label)
            _write_fixture(overlap, two, _README, landing=landing)
            before = ((overlap / LANDING_REL).read_bytes(), (overlap / README_REL).read_bytes())
            for check_mode, mode_label in ((False, "generate"), (True, "--check")):
                code, err = run_captured(overlap, check=check_mode)
                if code != 2:
                    failures.append("{} landing marker blocks expected exit 2 on {} "
                                    "(fail-closed)".format(label, mode_label))
                if PROOF_MARKER not in err or TABLE_MARKER not in err or "nest or cross" not in err:
                    failures.append("{} {} diagnostic must name both overlapping blocks and the "
                                    "overlap, never a leftover marker count "
                                    "({!r})".format(label, mode_label, err))
                if ((overlap / LANDING_REL).read_bytes(),
                        (overlap / README_REL).read_bytes()) != before:
                    failures.append("{} landing marker blocks: a failed {} must write nothing to "
                                    "either README".format(label, mode_label))

        # (n) misordered markers (END before BEGIN) fail closed (exit 2) on both generate and --check,
        #     the failed generate writing nothing to either README.
        misordered = tmp / "misordered"
        _write_fixture(misordered, two, _README, landing=_LANDING.replace(
            proof_pair, "<!-- COUNTS-PROOF:END -->\n<!-- COUNTS-PROOF:BEGIN (generated) -->"))
        before = ((misordered / LANDING_REL).read_bytes(), (misordered / README_REL).read_bytes())
        if run_quiet(misordered, check=False) != 2:
            failures.append("misordered COUNTS-PROOF markers expected exit 2 on generate (fail-closed)")
        if ((misordered / LANDING_REL).read_bytes(),
                (misordered / README_REL).read_bytes()) != before:
            failures.append("misordered COUNTS-PROOF markers: a failed generate must write nothing to "
                            "either README")
        if run_quiet(misordered, check=True) != 2:
            failures.append("misordered COUNTS-PROOF markers expected exit 2 on --check (fail-closed)")

        # (o) disjoint blocks that merely touch, or sit in swapped order, are still ACCEPTED: the
        #     overlap test is a strict interval intersection, so regions that only touch do not
        #     overlap. Touching is the boundary case exactly AT the interval edge (the overlaps of (m)
        #     are inside it, the prose-separated standard fixture is outside it); a guard tightened
        #     into refusing touching or reordered blocks fails here instead of over-refusing a page.
        touching_landing = (
            "# Fixture landing page\n\nHand prose above the counts.\n\n"
            "<!-- COUNTS-PROOF:BEGIN (generated) -->\n"
            "<!-- COUNTS-PROOF:END --><!-- COUNTS-TABLE:BEGIN (generated) -->\n"
            "<!-- COUNTS-TABLE:END -->\n\nHand prose below the counts.\n")
        swapped_landing = (
            "# Fixture landing page\n\nHand prose above the counts.\n\n"
            + table_pair + "\n\nHand prose between the blocks.\n\n"
            + proof_pair + "\n\nHand prose below the counts.\n")
        for label, landing in (("touching", touching_landing), ("swapped-order", swapped_landing)):
            disjoint = tmp / ("disjoint-" + label)
            _write_fixture(disjoint, two, _README, landing=landing)
            if run_quiet(disjoint, check=False) != 0:
                failures.append("{} disjoint landing blocks expected exit 0 on generate (the strict "
                                "overlap test must not refuse them)".format(label))
            accepted = (disjoint / LANDING_REL).read_text(encoding="utf-8")
            if _PROOF_RENDERED not in accepted or _TABLE_RENDERED[1] not in accepted:
                failures.append("{} disjoint landing blocks: expected both blocks "
                                "rendered".format(label))
            if run_quiet(disjoint, check=True) != 0:
                failures.append("{} disjoint landing blocks expected exit 0 on --check".format(label))

        # .aiqt/ above the roster README symlinked outside the root fails closed (exit 2) in --check and write
        # mode with the README there untouched and never read: the README there has no ROSTER markers, and each
        # run prints the link refusal, not the marker error; symlinked inside the root, it is read and the
        # README rewritten.
        failures.extend(linked_dir_failures(tmp, "aiqt", lambda base: _write_fixture(base, two, _README), run,
                                            ".aiqt", README_REL,
                                            unread=lambda p: p.write_text("no markers\n", encoding="utf-8")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a conformant tree generates the right framework count, roster and every "
          "landing figure in both blocks with the hand prose untouched and re-checks drift-clean, "
          "and disjoint landing blocks that touch or sit in swapped order stay accepted on both "
          "generate and --check; a "
          "hand-desynced roster count, a hand-edited landing count, and manifest changes that move "
          "one rendered figure and then every one of them each fail --check (exit 1), and "
          "regeneration renders each moved figure into both landing blocks and the roster; and a "
          "README missing the ROSTER markers, a landing README missing or "
          "duplicating a COUNTS marker pair, landing marker blocks that nest or cross in either "
          "order (on both generate and --check, each diagnostic naming both overlapping blocks, the "
          "failed run writing nothing to either README) or misorder (on both generate and --check, "
          "again writing nothing), duplicated ROSTER blocks "
          "(on both generate and --check), an empty standards dir, a missing ENFORCEMENT.md, and an "
          "invalid-UTF-8 README all fail closed (exit 2); an .aiqt/ symlinked outside the root fails closed "
          "(exit 2) with the README there untouched and never read, and one inside the root is followed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
