#!/usr/bin/env python3
"""Generate the SKILL-DOWNLOAD block on site/install.html from the skill meta version.

The install page carries ONE generated region: the download button whose href names the version-numbered
skill zip. That name tracks the skill meta version in .aiqt/core/skill/skill-source.md, so a skill bump
must propagate to the button on regenerate and a hand-edit must read as drift. This is the SAME managed-
block mechanism gen_disclosure.py uses for site/disclosure.html: a generated region spliced between markers
inside an otherwise hand-authored page, reconciled and drift-gated, with the surrounding page chrome left to
human review.

This is a BLOCK generator with NO RENDERER_DECL, so it is registered in the gensrc registry (a kind=block
target) and drift-gated like gen_disclosure, but is never promoted into a renderer target. A block inside a
hand-authored page belongs in a managed-block artefact that digests only the block, not a whole-file one; a
generator that carried a RENDERER_DECL and declared this page would make gen_manifest record the ENTIRE
hand-authored install page as a whole-file artefact, falsely claiming a renderer produces the whole page.
Keeping this generator RENDERER_DECL-free is what keeps the install page out of the renderer roster and the
manifest's whole-file artefact set. It single-sources the skill version through gen_skill.parse_source and
the filename shape through gen_skill.versioned_zip_basename, so the button, the version-numbered zip name,
and the skill meta version cannot diverge.

  gen_install.py            regenerate the SKILL-DOWNLOAD block on site/install.html
  gen_install.py --check    exit 1 if the block is out of date; exit 2 on a malformed source, a missing
                            page, or missing markers (fail-closed)
  gen_install.py --self-test  build synthetic trees and assert drift is caught (exit 1) and a missing page
                              or missing markers each fail closed (exit 2)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, replace_block, reconcile  # noqa: E402
from gen_skill import parse_source, versioned_zip_basename, SKILL_SRC_PARTS  # noqa: E402

# The generated region and its home. INSTALL_PAGE_PARTS is the relative parts of the hand-authored page the
# block lives in; MARKER names the <!-- MARKER:BEGIN --> / <!-- MARKER:END --> pair replace_block splices
# between. The skill version is read from the SAME source gen_skill parses (SKILL_SRC_PARTS), so the two
# generators can never read a different version.
INSTALL_PAGE_PARTS = ("site", "install.html")
INSTALL_PAGE_REL = "/".join(INSTALL_PAGE_PARTS)
MARKER = "SKILL-DOWNLOAD"

# Declares this generator's outputs for the gensrc registry (tools/gen_gensrc.py); additive metadata only,
# it does not affect what this generator produces. site/install.html is a generated block inside a
# hand-authored page (the SKILL-DOWNLOAD markers), so it is recorded as kind block, exactly as
# gen_disclosure declares site/disclosure.html. The source is the skill meta version.
GENSRC_OUTPUTS = (
    {"target": "site/install.html", "kind": "block",
     "sources": (".aiqt/core/skill/skill-source.md",),
     "regenerate": "python3 tools/gen_install.py"},
)


def render_block(version):
    """The SKILL-DOWNLOAD block inner: the download button whose href names the version-numbered zip,
    rendered from the skill meta version so a bump propagates on regenerate and a hand-edit reads as drift.
    Only the button (the version-bearing href) sits in the block; the surrounding note and step text are
    version-free hand-authored chrome, so a bump touches only this one rendered line."""
    return ('      <a class="btn primary" href="/downloads/{}" download>'
            'Download the Skill (.zip)</a>').format(versioned_zip_basename(version))


def run_gen(root, check):
    """Reconcile the SKILL-DOWNLOAD block on the install page under root. Exit 0 in sync, 1 on drift (check
    mode), 2 on a malformed/unreadable skill source, a missing page, or missing markers. Mirrors
    gen_disclosure.main()'s fail-closed shape. Parameterized on root so the self-test can call it off a
    synthetic tree."""
    try:
        source = parse_source(root.joinpath(*SKILL_SRC_PARTS))
    except (OSError, ValueError) as exc:
        print("error: cannot read skill source: {}".format(exc), file=sys.stderr)
        return 2
    inner = render_block(source["meta"]["version"])
    page = root.joinpath(*INSTALL_PAGE_PARTS)
    if not page.exists():
        print("error: {} not found (expected generated block target)".format(INSTALL_PAGE_REL),
              file=sys.stderr)
        return 2
    try:
        new_html = replace_block(page.read_text(encoding="utf-8"), MARKER, inner)
    except (ValueError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    drift = reconcile(page, new_html, check)  # shared reconcile fail-closes (exit 2) on an OSError or an
                                              # invalid-UTF-8 target
    if drift:
        print("drift: {}".format(INSTALL_PAGE_REL))
        if check:
            print("run tools/gen_install.py to regenerate")
            return 1
    return 0


def main():
    args = sys.argv[1:]
    if "--self-test" in args:
        return self_test_main()
    return run_gen(repo_root(), "--check" in args)


# --- self-test ------------------------------------------------------------------------------------
# Proves the gate fails on the things it must catch, against synthetic temp trees, never the real tree:
#   1. a well-formed source renders the block and re-checks drift-clean, with the version-numbered href
#      present in the page,
#   2. a hand-edited (drifted) block makes --check report drift (exit 1),
#   3. a missing install page fails closed (exit 2),
#   4. missing SKILL-DOWNLOAD markers fail closed (exit 2).
# The skill-source fixture is gen_skill's own _SKILL_SRC constant with a pinned version, so the parse this
# generator delegates to gen_skill.parse_source is exercised on the same shape the sibling gate uses.

_FIXTURE_VERSION = "1.2.3"


def _write_fixture(root):
    """A synthetic tree carrying a valid skill source (gen_skill's own fixture, version-pinned) and a
    minimal install page with the SKILL-DOWNLOAD markers, so run_gen has a source to read and a target to
    splice into. Returns the install page path."""
    from gen_skill import _SKILL_SRC  # the sibling generator's parse fixture; version placeholder below

    src = root.joinpath(*SKILL_SRC_PARTS)
    src.parent.mkdir(parents=True)
    src.write_text(_SKILL_SRC.replace("__ZIPVER__", _FIXTURE_VERSION), encoding="utf-8")
    page = root.joinpath(*INSTALL_PAGE_PARTS)
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text('<div class="cta">\n'
                    '      <!-- SKILL-DOWNLOAD:BEGIN (generated) -->\n'
                    '      placeholder\n'
                    '      <!-- SKILL-DOWNLOAD:END -->\n'
                    '</div>\n', encoding="utf-8")
    return page


def self_test_main():
    import io
    import shutil
    import tempfile
    from contextlib import redirect_stdout, redirect_stderr

    def capture(root, check):
        buf = io.StringIO()
        with redirect_stdout(buf), redirect_stderr(buf):
            try:
                code = run_gen(root, check)
            except SystemExit as exc:  # reconcile's fail-closed path raises SystemExit(2)
                return "raised SystemExit({!r})".format(exc.code), buf.getvalue()
        return code, buf.getvalue()

    failures = []
    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-gen-install-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    try:
        # 1. Well-formed source renders and round-trips clean, and the version-numbered href is present.
        good = tmp / "good"
        good.mkdir()
        page = _write_fixture(good)
        code, out = capture(good, False)
        if code != 0:
            failures.append("well-formed generate expected exit 0, got {}\n{}".format(code, out))
        expected_href = "/downloads/{}".format(versioned_zip_basename(_FIXTURE_VERSION))
        if expected_href not in page.read_text(encoding="utf-8"):
            failures.append("generated page is missing the version-numbered href {!r}".format(expected_href))
        code, out = capture(good, True)
        if code != 0:
            failures.append("well-formed --check after generate expected exit 0 (clean), got {}\n{}".format(
                code, out))

        # 2. A hand-edited (drifted) block makes --check report drift (exit 1).
        drifted = tmp / "drifted"
        drifted.mkdir()
        dpage = _write_fixture(drifted)
        capture(drifted, False)  # generate a clean tree first
        dpage.write_text(dpage.read_text(encoding="utf-8").replace(
            "Download the Skill (.zip)", "Download the Skill (hand-edited)"), encoding="utf-8")
        code, out = capture(drifted, True)
        if code != 1:
            failures.append("drifted block expected --check exit 1, got {}\n{}".format(code, out))

        # 3. A missing install page fails closed (exit 2): the generator never silently skips a target.
        nopage = tmp / "nopage"
        nopage.mkdir()
        npage = _write_fixture(nopage)
        npage.unlink()
        code, out = capture(nopage, True)
        if code != 2:
            failures.append("missing install page expected exit 2 (fail-closed), got {}\n{}".format(
                code, out))

        # 4. Missing SKILL-DOWNLOAD markers fail closed (exit 2): replace_block cannot find its region.
        nomarker = tmp / "nomarker"
        nomarker.mkdir()
        mpage = _write_fixture(nomarker)
        mpage.write_text('<div class="cta">no markers here</div>\n', encoding="utf-8")
        code, out = capture(nomarker, True)
        if code != 2:
            failures.append("missing SKILL-DOWNLOAD markers expected exit 2 (fail-closed), got {}\n{}".format(
                code, out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    print("SELF-TEST PASS: a well-formed source renders the SKILL-DOWNLOAD block and re-checks drift-clean "
          "with the version-numbered href present; a drifted block is caught (exit 1); a missing install "
          "page and missing markers each fail closed (exit 2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
