#!/usr/bin/env python3
"""Self-test for the vendored-Marko CommonMark heading adapter (tools/_commonmark_headings.py).

These are the change-carries-a-check artefacts for the adapter: targeted hostile fixtures that pin the
direct-H2 selection, plus the fail-closed paths (parser exception, missing span, oversized input, wrong
import origin, and manifest drift). Judged on the adapter's returned values / raised errors, never by
grepping output. Reports the count of checks actually run. Runs as:

    python3 -I -B tools/selftest_commonmark_headings.py

Exit 0 clean, 1 on a failed check, 2 on a fail-closed harness error (the vendored marko cannot load). The
full CommonMark 0.31.2 conformance replay lives in tools/selftest_commonmark_conformance.py; this suite is
the targeted / adversarial complement to it.
"""
import os
import sys
import tempfile
import hashlib
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _commonmark_headings as ch        # noqa: E402  the adapter under test


def _entries(text):
    """Return the adapter's boundaries as a list of (kind, payload, entry_slice) in document order, where
    entry_slice is the heading-start-to-next-heading-or-EOF slice the caller would freeze."""
    scan = ch.scan_entry_headings(text)
    starts = [h.start for h in scan.headings]
    out = []
    for i, h in enumerate(scan.headings):
        end = starts[i + 1] if i + 1 < len(starts) else len(scan.normalized_text)
        out.append((h.kind, h.payload, scan.normalized_text[h.start:end]))
    return out


def _payloads(text):
    return [payload for _kind, payload, _slice in _entries(text)]


def _raises(reason, fn, *args, **kwargs):
    """True when calling fn raises HeadingScanError with the given .reason."""
    try:
        fn(*args, **kwargs)
    except ch.HeadingScanError as exc:
        return exc.reason == reason
    except Exception:
        return False
    return False


def self_test():
    # Fail closed if the vendored marko cannot load at all: without it this suite cannot run, and a green
    # exit would be a false pass (verifier-delivery-completeness / check-fails-closed-on-unreadable).
    try:
        ch._load_marko()
    except ch.HeadingScanError as exc:
        print("COMMONMARK-HEADINGS SELF-TEST ERROR: vendored marko unavailable ({}: {}); fail-closed".format(
            exc.reason, exc), file=sys.stderr)
        return 2

    failures = []
    checked = 0

    def check(name, cond):
        nonlocal checked
        checked += 1
        if not cond:
            failures.append(name)

    # --- ATX / setext selection ----------------------------------------------------------------------
    check("atx-h2-is-boundary", _payloads("## 1.0.0 (2026-06-01)\n\n- x\n") == ["1.0.0 (2026-06-01)"])
    check("h1-not-boundary", _payloads("# Title\n\n- x\n") == [])
    check("h3-not-boundary", _payloads("### 1.0.0\n\n- x\n") == [])
    check("setext-h2-is-boundary",
          _entries("1.0.0 (2026-06-01)\n---\n\n- x\n") == [(ch.KIND_SETEXT, "1.0.0 (2026-06-01)",
                                                            "1.0.0 (2026-06-01)\n---\n\n- x\n")])
    check("setext-h1-not-boundary", _payloads("Title\n===\n\n- x\n") == [])
    check("multiline-setext-keeps-newline",
          _payloads("1.0.0\ntrailing title\n---\n") == ["1.0.0\ntrailing title"])
    check("atx-with-3-space-indent-is-boundary", _payloads("   ## 1.0.0\n") == ["1.0.0"])
    check("atx-empty-payload-is-boundary-with-empty-token", _payloads("##\n\n- x\n") == [""])
    check("atx-closing-hashes-stripped", _payloads("## 1.0.0 ##\n") == ["1.0.0"])

    # --- thematic-break ambiguity --------------------------------------------------------------------
    check("thematic-break-alone-not-a-heading", _payloads("- x\n\n---\n\n- y\n") == [])
    check("dashes-after-text-are-setext", _payloads("1.0.0\n---\n") == ["1.0.0"])

    # --- fences: a "##" inside a fence is not a boundary ---------------------------------------------
    check("backtick-fence-hides-hash",
          _payloads("## real\n\n```\n## not-a-heading\n```\n") == ["real"])
    check("tilde-fence-hides-hash",
          _payloads("## real\n\n~~~\n## not-a-heading\n~~~\n") == ["real"])
    check("info-string-fence-hides-hash",
          _payloads("## real\n\n```python\n## not-a-heading\n```\n") == ["real"])
    check("unterminated-fence-hides-trailing-hash",
          _payloads("## real\n\n```\n## not-a-heading\n") == ["real"])

    # --- indented code: a 4-space "##" is code; a 4-space fence does not hide a later "##" ------------
    check("indented-code-hash-not-a-boundary",
          _payloads("para\n\n    ## not-a-heading\n") == [])
    check("indented-fence-does-not-hide-later-heading",
          _payloads("## real\n\n    ```\n## 9.9.9\n") == ["real", "9.9.9"])

    # --- HTML blocks: a "##" inside any HTML-block category is not a boundary -------------------------
    # Assert the fenced/embedded tokens do not surface as payloads, and a real H2 after the block does.
    check("html-type1-pre-hides-hash",
          "## inside" not in "".join(_payloads("<pre>\n## inside\n</pre>\n\n## real\n"))
          and "real" in _payloads("<pre>\n## inside\n</pre>\n\n## real\n"))
    check("html-type2-comment-blanklines-hides-hash",
          _payloads("<!--\n## inside\n\n## still inside\n-->\n\n## real\n") == ["real"])
    check("html-type3-instruction-hides-hash",
          _payloads("<?php\n## inside\n?>\n\n## real\n") == ["real"])
    check("html-type4-declaration-hides-hash",
          _payloads("<!X\n## inside\n>\n\n## real\n") == ["real"])
    check("html-type5-cdata-hides-hash",
          _payloads("<![CDATA[\n## inside\n]]>\n\n## real\n") == ["real"])
    check("html-type6-block-tag-hides-hash",
          _payloads("<div>\n## inside\n\n## real\n") == ["real"])
    check("html-type7-complete-tag-hides-hash",
          _payloads('<a href="/x">\n## inside\n\n## real\n') == ["real"])

    # --- nested containers: a "##" inside a quote or list is not a top-level boundary -----------------
    check("blockquote-hash-not-a-boundary", _payloads("> ## not-a-heading\n\n## real\n") == ["real"])
    check("list-hash-not-a-boundary", _payloads("- item\n  ## not-a-heading\n\n## real\n") == ["real"])

    # --- CR/CRLF and NUL: normalized, offsets aligned, no crash --------------------------------------
    crlf = "## 1.1.0 (2026-06-15)\r\n\r\n- second\r\n\r\n## 1.0.0 (2026-06-01)\r\n\r\n- first\r\n"
    check("crlf-normalized-slices",
          _entries(crlf)[0] == (ch.KIND_ATX, "1.1.0 (2026-06-15)",
                                "## 1.1.0 (2026-06-15)\n\n- second\n\n"))
    lone_cr = "## 1.0.0 (2026-06-01)\r\r- first\r"
    check("lone-cr-normalized", _payloads(lone_cr) == ["1.0.0 (2026-06-01)"])
    nul = "## has\x00nul\n\n- x\n"
    scan_nul = ch.scan_entry_headings(nul)
    check("nul-preserved-in-normalized-text", "\x00" in scan_nul.normalized_text)
    check("nul-heading-still-recognized", _payloads(nul) == ["has\x00nul"])

    # --- digest boundary: the slice runs to the NEXT direct H2, not to EOF ----------------------------
    two = "## 1.1.0 (2026-06-15)\n\n- second release\n\n## 1.0.0 (2026-06-01)\n\n- first release\n"
    check("nonfinal-entry-slice-ends-at-next-h2",
          _entries(two)[0][2] == "## 1.1.0 (2026-06-15)\n\n- second release\n\n")
    check("final-entry-slice-runs-to-eof",
          _entries(two)[1][2] == "## 1.0.0 (2026-06-01)\n\n- first release\n")

    # --- FAIL-CLOSED: oversized input ----------------------------------------------------------------
    oversized = "#" * (ch.MAX_CHANGELOG_BYTES + 1)
    check("oversized-fails-closed", _raises(ch.REASON_OVERSIZED, ch.scan_entry_headings, oversized))
    at_ceiling = "## x\n" + "y\n" * ((ch.MAX_CHANGELOG_BYTES - 5) // 2)
    check("at-ceiling-does-not-fail-closed", len(at_ceiling.encode("utf-8")) <= ch.MAX_CHANGELOG_BYTES
          and ch.scan_entry_headings(at_ceiling) is not None)

    # --- FAIL-CLOSED: parser exception ---------------------------------------------------------------
    saved_new_parser = ch._new_parser

    class _Boom:
        def parse(self, _text):
            raise RuntimeError("simulated parser fault")

    ch._new_parser = lambda _marko: _Boom()
    try:
        check("parser-exception-fails-closed", _raises(ch.REASON_PARSER, ch.scan_entry_headings, "## x\n"))
    finally:
        ch._new_parser = saved_new_parser

    # --- FAIL-CLOSED: missing / invalid source span --------------------------------------------------
    marko = ch._load_marko()

    class _NoSpanHeading(marko.block.Heading):
        pass

    class _FakeDoc:
        def __init__(self, norm):
            self.source_span = (0, len(norm))
            bad = _NoSpanHeading.__new__(_NoSpanHeading)
            bad.level = 2
            bad.source_span = None        # a level-2 heading with no usable span
            self.children = [bad]

    class _FakeParser:
        def parse(self, text):
            return _FakeDoc(ch._normalize_lf(text))

    ch._new_parser = lambda _marko: _FakeParser()
    try:
        check("missing-span-fails-closed", _raises(ch.REASON_INVALID_SPAN, ch.scan_entry_headings, "## x\n"))
    finally:
        ch._new_parser = saved_new_parser

    # --- FAIL-CLOSED: structurally-degenerate parse result (F2) --------------------------------------
    class _NoChildrenDoc:
        def __init__(self, norm):
            self.source_span = (0, len(norm))   # a document with NO children attribute at all

    class _NoChildrenParser:
        def parse(self, text):
            return _NoChildrenDoc(ch._normalize_lf(text))

    ch._new_parser = lambda _marko: _NoChildrenParser()
    try:
        check("no-children-fails-closed", _raises(ch.REASON_PARSER, ch.scan_entry_headings, "## x\n"))
    finally:
        ch._new_parser = saved_new_parser

    class _NoLevelHeading(marko.block.Heading):
        pass

    class _NoLevelDoc:
        def __init__(self, norm):
            self.source_span = (0, len(norm))
            bad = _NoLevelHeading.__new__(_NoLevelHeading)
            bad.source_span = (0, 4)
            bad.level = None              # a heading node with no integer level is a malformed structure
            self.children = [bad]

    class _NoLevelParser:
        def parse(self, text):
            return _NoLevelDoc(ch._normalize_lf(text))

    ch._new_parser = lambda _marko: _NoLevelParser()
    try:
        check("no-level-heading-fails-closed", _raises(ch.REASON_PARSER, ch.scan_entry_headings, "## x\n"))
    finally:
        ch._new_parser = saved_new_parser

    # --- FAIL-CLOSED: wrong import origin / version --------------------------------------------------
    class _FakeModule:
        pass

    outside = _FakeModule()
    outside.__version__ = ch.MARKO_VERSION
    outside.__file__ = "/usr/lib/python3/dist-packages/marko/__init__.py"   # outside the vendored tree
    check("wrong-import-origin-fails-closed",
          _raises(ch.REASON_PROVENANCE, ch._verify_marko_provenance, outside, ch._VENDOR_DIR))

    wrong_version = _FakeModule()
    wrong_version.__version__ = "2.2.3"
    wrong_version.__file__ = os.path.join(ch._VENDOR_DIR, "marko", "__init__.py")
    check("wrong-version-fails-closed",
          _raises(ch.REASON_PROVENANCE, ch._verify_marko_provenance, wrong_version, ch._VENDOR_DIR))

    # The genuine vendored module passes provenance (positive control that the check is not always-fail).
    check("genuine-provenance-passes", (lambda: (ch._verify_marko_provenance(marko, ch._VENDOR_DIR), True)[1])())

    # --- FAIL-CLOSED: a submodule resolved outside the vendored tree (F6) ----------------------------
    check("submodule-origin-outside-fails-closed",
          _raises(ch.REASON_PROVENANCE, ch._verify_module_origin, outside, ch._VENDOR_DIR))
    no_file = _FakeModule()
    no_file.__name__ = "marko.parser"
    check("submodule-origin-nofile-fails-closed",
          _raises(ch.REASON_PROVENANCE, ch._verify_module_origin, no_file, ch._VENDOR_DIR))
    check("genuine-submodule-origin-passes",
          (lambda: (ch._verify_module_origin(marko.block, ch._VENDOR_DIR),
                    ch._verify_module_origin(marko.parser, ch._VENDOR_DIR), True)[2])())

    # --- FAIL-CLOSED: manifest drift over a synthetic vendored tree ----------------------------------
    tmp = tempfile.mkdtemp(prefix="commonmark-headings-selftest-")
    try:
        pkg = os.path.join(tmp, "marko")
        os.makedirs(pkg)
        f_init = os.path.join(pkg, "__init__.py")
        f_parser = os.path.join(pkg, "parser.py")
        with open(f_init, "wb") as fh:
            fh.write(b"# init\n")
        with open(f_parser, "wb") as fh:
            fh.write(b"# parser\n")

        def sha(path):
            with open(path, "rb") as fh:
                return hashlib.sha256(fh.read()).hexdigest()

        manifest_path = os.path.join(tmp, ch.MANIFEST_NAME)

        def write_manifest(entries):
            with open(manifest_path, "w", encoding="utf-8") as fh:
                for digest, rel in entries:
                    fh.write("{}  {}\n".format(digest, rel))

        good = [(sha(f_init), "marko/__init__.py"), (sha(f_parser), "marko/parser.py")]
        write_manifest(good)
        check("manifest-clean-verifies", ch.verify_vendor_manifest(vendor_dir=tmp, root=tmp) == 2)
        # F7: the REAL committed manifest (repo-root-relative paths) verifies against the real vendored tree
        # under DEFAULT arguments -- exercising the standing per-push wiring, not only synthetic trees.
        check("real-vendored-tree-verifies", ch.verify_vendor_manifest() == 26)

        # A drifted file (bytes differ from the recorded digest).
        with open(f_parser, "wb") as fh:
            fh.write(b"# parser CHANGED\n")
        check("manifest-drift-fails-closed",
              _raises(ch.REASON_MANIFEST_DRIFT, ch.verify_vendor_manifest, vendor_dir=tmp, root=tmp))

        # A missing recorded file.
        with open(f_parser, "wb") as fh:
            fh.write(b"# parser\n")        # restore bytes so only the removal matters
        os.remove(f_parser)
        check("manifest-missing-file-fails-closed",
              _raises(ch.REASON_MANIFEST_DRIFT, ch.verify_vendor_manifest, vendor_dir=tmp, root=tmp))

        # An extra, unlisted vendored file (bidirectional reconciliation).
        with open(f_parser, "wb") as fh:
            fh.write(b"# parser\n")
        write_manifest([(sha(f_init), "marko/__init__.py")])   # omit parser.py from the manifest
        check("manifest-extra-file-fails-closed",
              _raises(ch.REASON_MANIFEST_DRIFT, ch.verify_vendor_manifest, vendor_dir=tmp, root=tmp))

        # A missing manifest is unreadable, not "nothing to check".
        os.remove(manifest_path)
        check("manifest-absent-fails-closed",
              _raises(ch.REASON_VENDOR_UNREADABLE, ch.verify_vendor_manifest, vendor_dir=tmp, root=tmp))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("COMMONMARK-HEADINGS SELF-TEST: FAIL ({} of {} checks failed)".format(len(failures), checked))
        for name in failures:
            print("  FAILED: {}".format(name))
        return 1
    print("COMMONMARK-HEADINGS SELF-TEST: PASS ({} heading-selection and fail-closed checks)".format(checked))
    return 0


if __name__ == "__main__":
    sys.exit(self_test())
