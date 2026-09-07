#!/usr/bin/env python3
"""Unit suite for the generic corpus-tool core (tools/aiqt_corpus.py).

Locks the behavioural contract of every shipped helper and constant so a
regression is caught, not shipped, and asserts the GENERICITY invariants the
module exists to hold: the exempt-dir default carries only the universal
VCS/build directories, the scan-root walker refuses to assume a repository
root, and the metadata parser is field-agnostic (it captures every field and
enforces no required-field list of its own).

Every filesystem fixture is synthetic and assembled in a tempdir; the git
helpers run against a throwaway repository. Nothing outside the tempdirs is
read or written. Exit convention matches the repo's selftests: 0 pass, 1 a
real assertion failure, 2 an error (a fixture or harness problem).
"""
import datetime
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aiqt_corpus as ac  # noqa: E402
from aiqt_corpus import (  # noqa: E402
    DEFAULT_EXEMPT_DIRS,
    MARKDOWN_SUFFIXES,
    MetadataBlock,
    add_months,
    git,
    git_show,
    head_version,
    is_fence_line,
    is_markdown_target,
    is_separator_row,
    is_target,
    iter_markdown_targets,
    iter_non_code_lines,
    iter_scan_roots_markdown,
    iter_targets,
    parse_iso_date,
    parse_metadata_block,
    read_text_safe,
    split_row,
    strip_code_spans,
)


class TargetingTests(unittest.TestCase):
    def test_is_target_suffix_and_defaults(self):
        self.assertTrue(is_target(Path("a/b/doc.md")))
        self.assertFalse(is_target(Path("a/b/doc.txt")))

    def test_is_target_exempt_dir_default(self):
        self.assertFalse(is_target(Path("repo/.git/x.md")))
        self.assertFalse(is_target(Path("repo/node_modules/p/x.md")))
        self.assertFalse(is_target(Path("repo/__pycache__/x.md")))

    def test_is_target_exempt_dir_is_passed_in_not_hardcoded(self):
        # A corpus-specific exempt directory is honoured only when the caller
        # passes it; it is NOT in the shipped default (the genericity contract).
        self.assertTrue(is_target(Path("repo/.working/x.md")))
        self.assertFalse(is_target(Path("repo/.working/x.md"), exempt_dirs={".working"}))

    def test_is_target_exempt_files(self):
        self.assertFalse(is_target(Path("a/README.md"), exempt_files={"README.md"}))

    def test_custom_suffixes(self):
        self.assertTrue(is_target(Path("a/x.rst"), suffixes={".rst"}))
        self.assertFalse(is_target(Path("a/x.md"), suffixes={".rst"}))

    def test_is_markdown_target_wrapper(self):
        self.assertTrue(is_markdown_target(Path("a/x.md")))
        self.assertFalse(is_markdown_target(Path("a/x.rst")))

    def test_iter_targets_dedup_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "sub").mkdir()
            (root / "a.md").write_text("a", encoding="utf-8")
            (root / "sub" / "b.md").write_text("b", encoding="utf-8")
            (root / "sub" / "c.txt").write_text("c", encoding="utf-8")
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "d.md").write_text("d", encoding="utf-8")
            got = iter_markdown_targets([root, root / "a.md"])
            names = sorted(p.name for p in got)
            self.assertEqual(names, ["a.md", "b.md"])
            # dedup: a.md passed both as dir member and explicitly
            self.assertEqual(len([p for p in got if p.name == "a.md"]), 1)

    def test_iter_targets_custom_suffix(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "x.rst").write_text("x", encoding="utf-8")
            (root / "y.md").write_text("y", encoding="utf-8")
            got = iter_targets([root], suffixes={".rst"})
            self.assertEqual([p.name for p in got], ["x.rst"])


class ScanRootTests(unittest.TestCase):
    def test_requires_repo_root_keyword(self):
        # repo_root is required: no module-level root default is assumed.
        with self.assertRaises(TypeError):
            iter_scan_roots_markdown(["docs"])  # type: ignore[call-arg]

    def test_module_has_no_repo_root_constant(self):
        self.assertFalse(hasattr(ac, "REPO_ROOT"))

    def test_scan_roots_files_and_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs").mkdir()
            (root / "docs" / "a.md").write_text("a", encoding="utf-8")
            (root / "docs" / "b.txt").write_text("b", encoding="utf-8")
            (root / "meta.md").write_text("m", encoding="utf-8")
            got = iter_scan_roots_markdown(["docs", "meta.md"], repo_root=root)
            # sorted() orders by full path; membership is the stable assertion.
            self.assertEqual({p.name for p in got}, {"a.md", "meta.md"})
            self.assertTrue(all(p.is_absolute() for p in got))

    def test_scan_roots_no_exempt_subtraction(self):
        # An allow-list scan root IS its scope: a nested exempt-looking dir is
        # still walked (no DEFAULT_EXEMPT_DIRS subtraction here).
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "node_modules").mkdir(parents=True)
            (root / "docs" / "node_modules" / "x.md").write_text("x", encoding="utf-8")
            got = iter_scan_roots_markdown(["docs"], repo_root=root)
            self.assertEqual({p.name for p in got}, {"x.md"})


class ReadTextSafeTests(unittest.TestCase):
    def test_utf8_ok(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.md"
            p.write_text("héllo", encoding="utf-8")
            self.assertEqual(read_text_safe(p), "héllo")

    def test_non_utf8_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.md"
            p.write_bytes(b"\xff\xfe\x00binary")
            self.assertIsNone(read_text_safe(p))

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            read_text_safe(Path("/nonexistent/does/not/exist.md"))


class TablePrimitiveTests(unittest.TestCase):
    def test_split_row_drops_bounding_pipes(self):
        self.assertEqual(split_row("| a | b | c |"), ["a", "b", "c"])
        self.assertEqual(split_row("a | b"), ["a", "b"])

    def test_is_separator_row(self):
        self.assertTrue(is_separator_row(split_row("|---|:--:|")))
        self.assertFalse(is_separator_row(split_row("| a | b |")))
        self.assertFalse(is_separator_row([]))


class CodeSpanFenceTests(unittest.TestCase):
    def test_strip_code_spans_multibacktick(self):
        self.assertEqual(strip_code_spans("a `code` b"), "a  b")
        self.assertEqual(strip_code_spans("x ``a`b`` y"), "x  y")

    def test_is_fence_line(self):
        self.assertTrue(is_fence_line("```python"))
        self.assertTrue(is_fence_line("   ~~~"))
        self.assertFalse(is_fence_line("not a fence"))

    def test_iter_non_code_lines_skips_fenced(self):
        text = "a\n```\nb\nc\n```\nd\n"
        got = list(iter_non_code_lines(text))
        self.assertEqual(got, [(1, "a"), (6, "d")])

    def test_iter_non_code_lines_tilde_fence(self):
        text = "a\n~~~\nsecret\n~~~\nb\n"
        self.assertEqual([line for _, line in iter_non_code_lines(text)], ["a", "b"])

    def test_iter_non_code_lines_unterminated(self):
        text = "a\n```\nb\nc\n"
        self.assertEqual([line for _, line in iter_non_code_lines(text)], ["a"])


class DateTests(unittest.TestCase):
    def test_parse_iso_date_exact(self):
        self.assertEqual(parse_iso_date("2026-07-01"), datetime.date(2026, 7, 1))

    def test_parse_iso_date_rejects_annotation(self):
        self.assertIsNone(parse_iso_date("2026-07-01 (draft)"))
        self.assertIsNone(parse_iso_date("2026-7-1"))
        self.assertIsNone(parse_iso_date("2026-13-01"))

    def test_add_months_simple(self):
        self.assertEqual(add_months(datetime.date(2026, 1, 15), 1), datetime.date(2026, 2, 15))

    def test_add_months_month_end_clamp(self):
        self.assertEqual(add_months(datetime.date(2026, 1, 31), 1), datetime.date(2026, 2, 28))
        # leap year February
        self.assertEqual(add_months(datetime.date(2024, 1, 31), 1), datetime.date(2024, 2, 29))

    def test_add_months_year_roll(self):
        self.assertEqual(add_months(datetime.date(2026, 12, 15), 1), datetime.date(2027, 1, 15))

    def test_add_months_multi_year(self):
        self.assertEqual(add_months(datetime.date(2026, 3, 31), 13), datetime.date(2027, 4, 30))


class MetadataTests(unittest.TestCase):
    def test_parse_metadata_block_basic(self):
        text = "# Title\n**Version:** 1.2.3\n**Date:** 2026-07-01\\\n\nbody\n"
        block = parse_metadata_block(text)
        self.assertIsInstance(block, MetadataBlock)
        self.assertEqual(block.fields["Version"], "1.2.3")
        # trailing backslash stripped
        self.assertEqual(block.fields["Date"], "2026-07-01")
        self.assertEqual(block.raw_lines["Version"][0], 2)

    def test_parse_metadata_first_occurrence_wins(self):
        text = "**Version:** 1.0\n**Version:** 2.0\n"
        self.assertEqual(parse_metadata_block(text).fields["Version"], "1.0")

    def test_parse_metadata_is_field_agnostic(self):
        # The parser enforces no required-field list; it captures whatever
        # fields are present, whatever their names.
        text = "**Anything:** yes\n**Custom Corpus Field:** ok\n"
        fields = parse_metadata_block(text).fields
        self.assertEqual(fields["Anything"], "yes")
        self.assertEqual(fields["Custom Corpus Field"], "ok")

    def test_parse_metadata_tolerant_of_annotation(self):
        block = parse_metadata_block("**Date:** 2026-07-01 (draft)\n")
        self.assertEqual(block.fields["Date"], "2026-07-01 (draft)")
        # the caller distinguishes malformed-present from absent
        self.assertIsNone(parse_iso_date(block.fields["Date"]))

    def test_parse_metadata_head_window(self):
        lines = ["filler"] * 40 + ["**Version:** 9.9"]
        self.assertNotIn("Version", parse_metadata_block("\n".join(lines)).fields)

    def test_head_version_precedence(self):
        self.assertEqual(head_version("**Version:** 1.0\n**Library Version:** 2.0\n"), "1.0")
        self.assertEqual(head_version("**Library Version:** 2.0\n"), "2.0")

    def test_head_version_precedence_independent_of_position(self):
        # Version wins by FIELD NAME, not by position in the window: even when
        # Library Version appears FIRST, the Version field still takes precedence.
        self.assertEqual(head_version("**Library Version:** 2.0\n**Version:** 1.0\n"), "1.0")

    def test_head_version_empty_is_none(self):
        self.assertIsNone(head_version("**Version:** \n"))

    def test_head_version_readme_version_not_matched(self):
        self.assertIsNone(head_version("**README Version:** 3.0\n"))

    def test_head_version_none_text(self):
        self.assertIsNone(head_version(None))


class GitTests(unittest.TestCase):
    def _init_repo(self, d):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
        subprocess.run(["git", "init", "-q"], cwd=d, check=True, env=env)
        (Path(d) / "f.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "f.md"], cwd=d, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=d, check=True, env=env)
        return env

    def test_git_returns_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            self._init_repo(d)
            cwd = os.getcwd()
            try:
                os.chdir(d)
                out = git("rev-parse", "--is-inside-work-tree")
                self.assertEqual(out, "true")
            finally:
                os.chdir(cwd)

    def test_git_preserves_surrounding_whitespace(self):
        # git() strips ONLY trailing newlines, never surrounding spaces: a -z /
        # NUL caller's first path may carry leading whitespace that .strip()
        # would corrupt. Drive it on output with leading and trailing spaces
        # plus trailing newlines and assert only the newlines are removed.
        with tempfile.TemporaryDirectory() as d:
            env = self._init_repo(d)
            (Path(d) / "spaced.md").write_text("  x y  \n\n", encoding="utf-8")
            subprocess.run(["git", "add", "spaced.md"], cwd=d, check=True, env=env)
            subprocess.run(["git", "commit", "-q", "-m", "spaced"], cwd=d, check=True, env=env)
            cwd = os.getcwd()
            try:
                os.chdir(d)
                # trailing newlines gone, leading/trailing spaces preserved.
                self.assertEqual(git("show", "HEAD:spaced.md"), "  x y  ")
            finally:
                os.chdir(cwd)

    def test_git_show_present_and_absent(self):
        with tempfile.TemporaryDirectory() as d:
            self._init_repo(d)
            cwd = os.getcwd()
            try:
                os.chdir(d)
                self.assertEqual(git_show("HEAD", "f.md"), "hello\n")
                self.assertIsNone(git_show("HEAD", "no_such_file.md"))
            finally:
                os.chdir(cwd)


class GenericityTests(unittest.TestCase):
    def test_default_exempt_dirs_are_only_universal(self):
        # The shipped default must NOT leak any corpus-specific directory.
        self.assertEqual(DEFAULT_EXEMPT_DIRS, frozenset({".git", "node_modules", "__pycache__"}))

    def test_markdown_suffixes(self):
        self.assertEqual(MARKDOWN_SUFFIXES, frozenset({".md"}))

    def test_public_api_is_exactly_all(self):
        # __all__ is pinned to the exact public surface. A self-referential
        # iterate-__all__ check would pass with __all__ = [] or with an extra
        # export, so the surface is asserted against a hardcoded expected list:
        # adding OR removing any name fails here.
        expected = [
            # discovery / targeting
            "is_target",
            "is_markdown_target",
            "iter_targets",
            "iter_markdown_targets",
            "iter_scan_roots_markdown",
            "read_text_safe",
            # text / markdown primitives
            "split_row",
            "is_separator_row",
            "strip_code_spans",
            "is_fence_line",
            "iter_non_code_lines",
            # date
            "parse_iso_date",
            "add_months",
            # git
            "git",
            "git_show",
            # metadata parse mechanism
            "MetadataBlock",
            "parse_metadata_block",
            "head_version",
            # constants
            "MARKDOWN_SUFFIXES",
            "DEFAULT_EXEMPT_DIRS",
            "CODE_SPAN_RE",
            "SIMPLE_CODE_SPAN_RE",
            "METADATA_FIELD_RE",
            "METADATA_HEAD_LINES",
        ]
        self.assertEqual(ac.__all__, expected)
        # every pinned name is importable, and nothing corpus-specific hides.
        for name in expected:
            self.assertTrue(hasattr(ac, name), name)

    def test_simple_code_span_re_matches_backtick_span(self):
        # SIMPLE_CODE_SPAN_RE recognizes a single-backtick inline code span; its
        # [^`]* body stops at the next backtick, so adjacent spans do not merge.
        # Fails if the pattern is ever replaced with a never-match regex.
        self.assertEqual(ac.SIMPLE_CODE_SPAN_RE.search("a `code` b").group(0), "`code`")
        # stops at the FIRST closing backtick (does not run across two spans).
        self.assertEqual(ac.SIMPLE_CODE_SPAN_RE.search("`a` x `b`").group(0), "`a`")
        # no backtick pair: no match.
        self.assertIsNone(ac.SIMPLE_CODE_SPAN_RE.search("no code here"))


def run_self_test():
    """Run the suite and return the repo exit convention: 0 pass, 1 fail, 2 error."""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    total = result.testsRun
    if result.errors:
        print("ERROR: aiqt_corpus self-test ({} tests, {} errors)".format(total, len(result.errors)))
        return 2
    if result.failures:
        print("FAIL: aiqt_corpus self-test ({} tests, {} failures)".format(total, len(result.failures)))
        return 1
    print("PASS: aiqt_corpus self-test ({} tests)".format(total))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_self_test())
