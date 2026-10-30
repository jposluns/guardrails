#!/usr/bin/env python3
"""Unit suite for the generic corpus-tool core (tools/aiqt_corpus.py).

Locks the behavioural contract of every shipped helper and constant so a
regression is caught, not shipped, and asserts the GENERICITY invariants the
module exists to hold: the exempt-dir default carries only the universal
VCS/build directories, the scan-root walker refuses to assume a repository
root, and the metadata parser is field-agnostic (it captures every field and
enforces no required-field list of its own).

Every filesystem fixture is synthetic and assembled in a tempdir; the git
helpers run against a throwaway repository. Apart from reading the sibling
aiqt_corpus.py source (the stdin-isolation check parses it), nothing outside the
tempdirs is read or written. Exit convention matches the repo's selftests: 0 pass, 1 a
real assertion failure, 2 an error (a fixture or harness problem).
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: selftest_aiqt_corpus.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import ast
import datetime
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _git_fixture_env import git_fixture_env, scrub_git_environment  # noqa: E402
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

    def test_zero_to_three_spaces_toggle(self):
        for marker in ("```", "~~~"):
            for spaces in range(4):
                with self.subTest(marker=marker, spaces=spaces):
                    fence = " " * spaces + marker
                    indented_code = "    " + marker
                    self.assertTrue(is_fence_line(fence))
                    text = "\n".join((
                        "before", fence, "hidden", indented_code,
                        "still hidden", fence, "after",
                    ))
                    self.assertEqual(
                        list(iter_non_code_lines(text)),
                        [(1, "before"), (7, "after")],
                    )

    def test_four_or_more_columns_do_not_toggle(self):
        for marker in ("```", "~~~"):
            for prefix in ("    ", "     ", "        ",
                           "\t", " \t", "  \t", "   \t"):
                with self.subTest(marker=marker, prefix=prefix):
                    line = prefix + marker
                    self.assertFalse(is_fence_line(line))
                    text = "\n".join(("before", line, "visible"))
                    self.assertEqual(
                        list(iter_non_code_lines(text)),
                        [(1, "before"), (2, line), (3, "visible")],
                    )

    def test_unindented_fence_after_indented_code_opens(self):
        for marker in ("```", "~~~"):
            for prefix in ("    ", "\t"):
                with self.subTest(marker=marker, prefix=prefix):
                    line = prefix + marker
                    text = "\n".join((
                        line, "visible", marker, "hidden", marker, "after",
                    ))
                    self.assertEqual(
                        list(iter_non_code_lines(text)),
                        [(1, line), (2, "visible"), (6, "after")],
                    )


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
    def setUp(self):
        # Hermetic fixtures on EVERY invocation surface (test-hermeticity): the git()/git_show()
        # calls under test read os.environ at CALL time, so an entry-point scrub alone leaves an
        # importer's inherited GIT_DIR live (run_self_test() called from another module, or
        # unittest loading GitTests directly, would read the CALLER's repository). Scrub in the
        # fixture lifecycle itself and restore the caller's environment afterwards (a test leaves
        # the host as it found it).
        saved = dict(os.environ)

        def _restore():
            os.environ.clear()
            os.environ.update(saved)

        self.addCleanup(_restore)
        scrub_git_environment()

    def _init_repo(self, d):
        # Hermetic fixture env (test-hermeticity): an inherited GIT_INDEX_FILE / GIT_DIR /
        # GIT_WORK_TREE (git exports these to hook children) would redirect these writes into
        # the CALLER's repository; the shared scrub drops them, keeping only what we set.
        env = git_fixture_env(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
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


# The subprocess launchers that take a stdin argument: a call must pass one of
# ISOLATING_STDIN_VALUES.
SUBPROCESS_LAUNCHERS = frozenset(("run", "call", "check_call", "check_output", "Popen"))

# The subprocess launchers that take no stdin argument, so the child always
# inherits the caller's standard input: every use is flagged.
SUBPROCESS_INHERITING_LAUNCHERS = frozenset(("getoutput", "getstatusoutput"))

# The subprocess constants that keep the caller's standard input from the child.
ISOLATING_STDIN_VALUES = frozenset(("DEVNULL", "PIPE"))

# The os functions that start a program on the caller's standard input: these
# names, plus every name that begins with one of the prefixes.
OS_LAUNCHERS = frozenset(("system", "popen"))
OS_LAUNCHER_PREFIXES = ("spawn", "exec", "posix_spawn")

# The modules whose names the check resolves, under any alias.
WATCHED_MODULES = ("subprocess", "os", "sys")

# Launches allowed to inherit stdin: (enclosing qualname, launch label, reason),
# the first two as stdin_inheriting_launches() reports them. Empty: every launch
# in aiqt_corpus.py isolates stdin. An entry needs a nonempty reason and must
# match exactly one reported launch, so it never covers a later one.
STDIN_EXEMPT_CALLS: tuple[tuple[str, str, str], ...] = ()


def launch_label(module: str, attr: str) -> str | None:
    """Return the launch label for ``module.attr``, or ``None`` when it is not a launch route."""
    if module == "subprocess" and attr in SUBPROCESS_LAUNCHERS | SUBPROCESS_INHERITING_LAUNCHERS:
        return "subprocess." + attr
    if module == "os" and (attr in OS_LAUNCHERS or attr.startswith(OS_LAUNCHER_PREFIXES)):
        return "os." + attr
    if module == "sys" and attr == "modules":
        return "sys.modules"
    return None


def stdin_inheriting_launches(source: str) -> list[tuple[str, str, int]]:
    """Return ``(enclosing qualname, launch label, lineno)`` for each launch that may inherit stdin.

    :class:`StdinIsolationTests` states what is reported and what is not. The
    label is the launcher (``subprocess.run``, ``os.system``), the star import
    (``subprocess.*``), the bare module (``subprocess``), ``__import__`` or
    ``sys.modules``.
    """
    tree = ast.parse(source)
    modules = {name: name for name in WATCHED_MODULES}
    bound: dict[str, str] = {}
    stdin_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname and alias.name in WATCHED_MODULES:
                    modules[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in WATCHED_MODULES:
            for alias in node.names:
                label = launch_label(node.module, alias.name)
                if label is not None:
                    bound[alias.asname or alias.name] = label
                elif node.module == "subprocess" and alias.name in ISOLATING_STDIN_VALUES:
                    stdin_names.add(alias.asname or alias.name)
    callees = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    attribute_bases = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    stdin_checked = {"subprocess." + name for name in SUBPROCESS_LAUNCHERS}

    def launch_of(node: ast.expr) -> str | None:
        if isinstance(node, ast.Attribute):
            if node.attr == "__import__":
                return "__import__"
            if isinstance(node.value, ast.Name) and node.value.id in modules:
                return launch_label(modules[node.value.id], node.attr)
            return None
        if isinstance(node, ast.Name):
            if node.id == "__import__":
                return "__import__"
            if node.id in modules and id(node) not in attribute_bases:
                return modules[node.id]
            return bound.get(node.id)
        return None

    def isolates(value: ast.expr) -> bool:
        if isinstance(value, ast.Attribute):
            return (value.attr in ISOLATING_STDIN_VALUES and isinstance(value.value, ast.Name)
                    and modules.get(value.value.id) == "subprocess")
        return isinstance(value, ast.Name) and value.id in stdin_names

    found: list[tuple[str, str, int]] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                inner = scope + (child.name,)
            where = ".".join(inner) or "<module>"
            if isinstance(child, ast.ImportFrom) and child.module in WATCHED_MODULES:
                if any(alias.name == "*" for alias in child.names):
                    found.append((where, child.module + ".*", child.lineno))
            elif isinstance(child, ast.Call):
                label = launch_of(child.func)
                if label in stdin_checked:
                    stdin = [kw.value for kw in child.keywords if kw.arg == "stdin"]
                    if not (stdin and isolates(stdin[0])):
                        found.append((where, label, child.lineno))
            elif isinstance(child, (ast.Name, ast.Attribute)) and isinstance(child.ctx, ast.Load):
                label = launch_of(child)
                if label is not None and not (label in stdin_checked and id(child) in callees):
                    found.append((where, label, child.lineno))
            visit(child, inner)

    visit(tree, ())
    return found


def unexempted_launches(found: list[tuple[str, str, int]],
                        exemptions: tuple[tuple[str, str, str], ...]) -> list[tuple[str, str, int]]:
    """Return the launches in ``found`` whose (qualname, label) no exemption names."""
    keys = {(name, label) for name, label, _ in exemptions}
    return [launch for launch in found if launch[:2] not in keys]


def exemption_errors(found: list[tuple[str, str, int]],
                     exemptions: tuple[tuple[str, str, str], ...]) -> list[str]:
    """Describe each exemption that lacks a reason or does not match exactly one launch."""
    errors = []
    for name, label, reason in exemptions:
        if not reason.strip():
            errors.append(f"{name} {label}: exemption without a reason")
        matches = sum(1 for launch in found if launch[:2] == (name, label))
        if matches != 1:
            errors.append(f"{name} {label}: exemption matches {matches} launches, not exactly one")
    return errors


class StdinIsolationTests(unittest.TestCase):
    """Every process launch in aiqt_corpus.py keeps the caller's standard input.

    A helper called from inside a git hook would otherwise hand the hook's own
    stdin (a pre-push hook's ref lines) to the child, which can consume it.

    Enforced, by reading the module's syntax tree, unless one exemption names
    the launch by enclosing qualname and launch label:

    * each call to subprocess.run, call, check_call, check_output or Popen
      passes stdin=subprocess.DEVNULL or stdin=subprocess.PIPE; any other value
      (None, sys.stdin, a descriptor number) or a ``**`` mapping, whose keys
      cannot be read statically, is flagged;
    * subprocess.getoutput and getstatusoutput, which cannot isolate stdin, and
      os.system, os.popen, os.spawn*, os.exec* and os.posix_spawn* are flagged
      wherever they appear;
    * forms whose target cannot be read statically are flagged rather than
      passed: a launcher referenced other than as the callee of a call (held in
      a variable, given to functools.partial), the subprocess, os or sys module
      referenced other than as the base of an attribute (as in getattr), a star
      import from any of the three, any reference to ``__import__`` and any
      reference to sys.modules.

    Names resolve through ``import`` of the three modules under any alias and
    through ``from ... import`` of their members under any alias.

    Not enforced: launches through other modules (asyncio, pty, multiprocessing,
    posix, importlib.import_module, or a builtins lookup by computed name);
    os.fork and os.forkpty; code compiled and run at run time; a tracked name
    rebound after its import (``DEVNULL = None``); and modules other than
    aiqt_corpus.py.
    """

    MODULE = Path(__file__).resolve().parent / "aiqt_corpus.py"

    def test_every_launch_isolates_stdin(self):
        source = self.MODULE.read_text(encoding="utf-8")
        missing = unexempted_launches(stdin_inheriting_launches(source), STDIN_EXEMPT_CALLS)
        self.assertEqual(missing, [], "launch that may inherit stdin "
                         "(pass stdin=subprocess.DEVNULL or add a reasoned exemption)")

    def test_exemptions_have_reasons_and_match_one_launch(self):
        source = self.MODULE.read_text(encoding="utf-8")
        self.assertEqual(
            exemption_errors(stdin_inheriting_launches(source), STDIN_EXEMPT_CALLS), [])

    def test_detector_flags_a_launch_without_isolated_stdin(self):
        # The detector must see a launch that omits stdin or passes an
        # inheriting value, through each import spelling, and must accept one
        # that passes DEVNULL or PIPE.
        cases = (
            ("import subprocess\ndef f():\n    subprocess.run(['git'])\n",
             [("f", "subprocess.run", 3)]),
            ("import subprocess as sp\ndef f():\n    sp.Popen(['git'])\n",
             [("f", "subprocess.Popen", 3)]),
            ("from subprocess import check_output as co\nco(['git'])\n",
             [("<module>", "subprocess.check_output", 2)]),
            ("import subprocess\nclass C:\n    def m(self, **kw):\n"
             "        subprocess.call(['git'], **kw)\n", [("C.m", "subprocess.call", 4)]),
            ("import subprocess\nsubprocess.run(['git'], stdin=None)\n",
             [("<module>", "subprocess.run", 2)]),
            ("import subprocess, sys\nsubprocess.run(['git'], stdin=sys.stdin)\n",
             [("<module>", "subprocess.run", 2)]),
            ("import subprocess\nsubprocess.run(['git'], stdin=0)\n",
             [("<module>", "subprocess.run", 2)]),
            ("import subprocess\nsubprocess.getoutput('git')\n",
             [("<module>", "subprocess.getoutput", 2)]),
            ("from subprocess import getstatusoutput\ndef f():\n    getstatusoutput('git')\n",
             [("f", "subprocess.getstatusoutput", 3)]),
            ("import subprocess\nsubprocess.check_call(['git'], stdin=subprocess.DEVNULL)\n", []),
            ("import subprocess as sp\nsp.Popen(['git'], stdin=sp.PIPE)\n", []),
            ("from subprocess import DEVNULL as null, run\nrun(['git'], stdin=null)\n", []),
            ("import os, sys\nos._exit(0)\nos.path.join('a')\nsys.stderr.write('a')\n", []),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_flags_an_indirect_launch(self):
        # A launch the detector cannot follow statically is flagged, never passed.
        cases = (
            ("from subprocess import *\nrun(['git'])\n", [("<module>", "subprocess.*", 1)]),
            ("import subprocess\nlaunch = subprocess.Popen\nlaunch(['git'])\n",
             [("<module>", "subprocess.Popen", 2)]),
            ("import functools, subprocess\n"
             "launch = functools.partial(subprocess.run, stdin=subprocess.DEVNULL)\n",
             [("<module>", "subprocess.run", 2)]),
            ("import subprocess\ngetattr(subprocess, 'run')(['git'])\n",
             [("<module>", "subprocess", 2)]),
            ("__import__('subprocess').run(['git'])\n", [("<module>", "__import__", 1)]),
            ("import builtins\nbuiltins.__import__('subprocess')\n",
             [("<module>", "__import__", 2)]),
            ("import sys\nsys.modules['subprocess'].run(['git'])\n",
             [("<module>", "sys.modules", 2)]),
            ("import os\nos.system('git status')\n", [("<module>", "os.system", 2)]),
            ("import os\nos.popen('git status')\n", [("<module>", "os.popen", 2)]),
            ("import os as o\no.spawnv(o.P_WAIT, '/usr/bin/git', ['git'])\n",
             [("<module>", "os.spawnv", 2)]),
            ("from os import execv as ev\ndef f():\n    ev('/usr/bin/git', ['git'])\n",
             [("f", "os.execv", 3)]),
            ("import os\nos.posix_spawnp('git', ['git'], {})\n",
             [("<module>", "os.posix_spawnp", 2)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_exemption_covers_exactly_one_launch(self):
        one = "import subprocess\ndef f():\n    subprocess.run(['git'])\n"
        two = one + "    subprocess.getoutput('git')\n    subprocess.run(['git'])\n"
        exemption = (("f", "subprocess.run", "reads the hook input on purpose"),)
        self.assertEqual(unexempted_launches(stdin_inheriting_launches(one), exemption), [])
        self.assertEqual(exemption_errors(stdin_inheriting_launches(one), exemption), [])
        self.assertEqual(unexempted_launches(stdin_inheriting_launches(two), exemption),
                         [("f", "subprocess.getoutput", 4)])
        self.assertEqual(exemption_errors(stdin_inheriting_launches(two), exemption),
                         ["f subprocess.run: exemption matches 2 launches, not exactly one"])
        self.assertEqual(exemption_errors([], (("f", "subprocess.run", " "),)),
                         ["f subprocess.run: exemption without a reason",
                          "f subprocess.run: exemption matches 0 launches, not exactly one"])

    def test_detector_flags_the_module_with_one_keyword_dropped(self):
        source = self.MODULE.read_text(encoding="utf-8")
        needle = "            stdin=subprocess.DEVNULL,\n"
        self.assertEqual(source.count(needle), 1)
        mutant = source.replace(needle, "", 1)
        self.assertEqual([launch[:2] for launch in stdin_inheriting_launches(mutant)],
                         [("git_show", "subprocess.check_output")])

    def test_detector_flags_the_module_with_one_value_inheriting(self):
        source = self.MODULE.read_text(encoding="utf-8")
        needle = "stdin=subprocess.DEVNULL).rstrip"
        self.assertEqual(source.count(needle), 1)
        mutant = source.replace(needle, "stdin=None).rstrip", 1)
        self.assertEqual([launch[:2] for launch in stdin_inheriting_launches(mutant)],
                         [("git", "subprocess.check_output")])


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
    # Defence-in-depth for the direct run: GitTests.setUp scrubs the fixture lifecycle itself
    # (covering the imported surfaces, run_self_test() and unittest-loaded GitTests, too); this
    # entry-point scrub backstops anything outside the unittest lifecycle.
    scrub_git_environment()
    raise SystemExit(run_self_test())
