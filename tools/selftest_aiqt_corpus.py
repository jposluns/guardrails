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
from unittest import mock

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


# The subprocess launchers that take a stdin argument, each with the subprocess
# constants it must be given as stdin= to keep the caller's standard input from
# the child. call and check_call never write to or close a PIPE, so a child
# that reads stdin waits on it forever: they take only DEVNULL.
ISOLATING_STDIN_VALUES = {
    "run": frozenset(("DEVNULL", "PIPE")),
    "call": frozenset(("DEVNULL",)),
    "check_call": frozenset(("DEVNULL",)),
    "check_output": frozenset(("DEVNULL", "PIPE")),
    "Popen": frozenset(("DEVNULL", "PIPE")),
}

# The subprocess launchers whose input= argument feeds the child through a pipe
# in place of the caller's standard input. run inherits stdin when input is
# None; check_output turns input=None into empty input.
INPUT_LAUNCHERS = frozenset(("run", "check_output"))

# The only attributes each watched module may be read through. Any other
# attribute, launcher or not, is flagged; a dunder attribute is flagged
# whatever it is read from. os.path is listed because it is itself a module;
# the launchers in ISOLATING_STDIN_VALUES are checked on their own.
PERMITTED_ATTRIBUTES = {
    "subprocess": frozenset(("DEVNULL", "PIPE", "STDOUT", "CalledProcessError", "TimeoutExpired",
                             "SubprocessError", "CompletedProcess")),
    "os": frozenset(("path", "environ", "getenv", "getcwd", "fspath", "sep", "devnull", "_exit")),
    "os.path": frozenset(("join", "exists", "isdir", "isfile", "dirname", "basename", "abspath",
                          "realpath", "relpath", "splitext")),
    "sys": frozenset(("argv", "stdin", "stdout", "stderr", "exit", "executable", "version_info")),
}

# The modules whose names the check resolves, under any alias.
WATCHED_MODULES = ("subprocess", "os", "sys")

# Names that read a namespace or a frame, and so reach a module without naming
# it: flagged as a name, an attribute of anything, an imported name, or the
# literal attribute of a getattr.
NAMESPACE_READERS = frozenset(("globals", "locals", "vars", "__builtins__", "f_globals", "f_locals",
                               "f_builtins", "f_back", "gi_frame", "cr_frame", "ag_frame", "tb_frame"))

# The only dunder name the module may read. A dunder it defines (a def name, an
# assignment target such as __all__ or __slots__) is not read, and __future__
# is allowed as the module of a from-import.
PERMITTED_DUNDER_NAMES = frozenset(("__name__",))

# The nodes that open a scope with its own imports.
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# Launches allowed to inherit stdin: (enclosing qualname, launch label, reason),
# the first two as stdin_inheriting_launches() reports them. Empty: every launch
# in aiqt_corpus.py isolates stdin. An entry needs a nonempty reason and must
# match exactly one reported launch, so it never covers a later one.
STDIN_EXEMPT_CALLS: tuple[tuple[str, str, str], ...] = ()


def is_dunder(name: str) -> bool:
    """Return whether ``name`` starts and ends with two underscores."""
    return name.startswith("__") and name.endswith("__")


def launch_label(path: tuple[str, ...]) -> str | None:
    """Return the launch label for ``path``, a watched module or a chain read from one, or ``None``.

    The label is ``path`` up to and including its first attribute that a
    watched module does not permit or that is a dunder, or the whole of
    ``path`` when it names a watched module itself. ``None`` means every
    attribute on the way is permitted.
    """
    for depth in range(1, len(path)):
        attr = path[depth]
        permitted = PERMITTED_ATTRIBUTES.get(".".join(path[:depth]))
        if is_dunder(attr):
            return ".".join(path[:depth + 1])
        if permitted is not None and attr not in permitted:
            return ".".join(path[:depth + 1])
    name = ".".join(path)
    return name if name in PERMITTED_ATTRIBUTES else None


def import_binding(node: ast.Import | ast.ImportFrom,
                   alias: ast.alias) -> tuple[str, tuple[str, ...]] | None:
    """Return the name ``alias`` binds and the watched path it binds it to, or ``None``."""
    if isinstance(node, ast.Import):
        path = tuple(alias.name.split("."))
        if path[0] not in WATCHED_MODULES:
            return None
        return (alias.asname, path) if alias.asname else (path[0], path[:1])
    if node.level or node.module.split(".")[0] not in WATCHED_MODULES or alias.name == "*":
        return None
    return alias.asname or alias.name, tuple(node.module.split(".")) + (alias.name,)


def import_reach(node: ast.Import | ast.ImportFrom, alias: ast.alias) -> str | None:
    """Return the dunder or namespace reader that ``alias`` imports or imports from, or ``None``."""
    parts = alias.name.split(".")
    if isinstance(node, ast.ImportFrom) and node.module and node.module != "__future__":
        parts = node.module.split(".") + parts
    for part in parts:
        if is_dunder(part) or part in NAMESPACE_READERS:
            return part
    return None


def opaque_reach(node: ast.Name | ast.Attribute) -> str | None:
    """Return the first dunder or namespace read on the chain that ``node`` ends, or ``None``.

    The chain runs down through attributes to a base that may be any
    expression. A read name counts when it is a namespace reader or a dunder
    not in PERMITTED_DUNDER_NAMES, and an attribute when it is either of
    those; the label is that name, or the chain up to that attribute as source
    text (``e.__init__``, ``[x][0].__init__``).
    """
    chain: list[ast.Attribute] = []
    while isinstance(node, ast.Attribute):
        chain.insert(0, node)
        node = node.value
    if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            and (node.id in NAMESPACE_READERS
                 or is_dunder(node.id) and node.id not in PERMITTED_DUNDER_NAMES)):
        return node.id
    for part in chain:
        if is_dunder(part.attr) or part.attr in NAMESPACE_READERS:
            return ast.unparse(part)
    return None


def type_parts(annotation: ast.expr) -> list[ast.Name | ast.Attribute]:
    """Return the names and attribute chains in ``annotation`` that stand for a type.

    Only a subscript of a name or chain (``list[...]``), a tuple or list in
    one, and a union (``A | B``) are looked into, so a name or chain inside a
    call or any other expression is not a type.
    """
    parts: list[ast.Name | ast.Attribute] = []
    pending = [annotation]
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.Name, ast.Attribute)):
            parts.append(node)
        elif isinstance(node, ast.Subscript) and isinstance(node.value, (ast.Name, ast.Attribute)):
            pending.extend((node.value, node.slice))
        elif isinstance(node, (ast.Tuple, ast.List)):
            pending.extend(node.elts)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            pending.extend((node.left, node.right))
    return parts


def own_nodes(scope: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> list[ast.AST]:
    """Return the nodes of ``scope``'s body in source order, not those of nested functions and classes."""
    nodes: list[ast.AST] = []
    pending = list(reversed(scope.body))
    while pending:
        node = pending.pop()
        nodes.append(node)
        if not isinstance(node, SCOPES):
            pending.extend(reversed(list(ast.iter_child_nodes(node))))
    return nodes


def stdin_inheriting_launches(source: str) -> list[tuple[str, str, int]]:
    """Return ``(enclosing qualname, launch label, lineno)`` for each launch that may inherit stdin.

    :class:`StdinIsolationTests` states what is reported and what is not. The
    label is the launcher (``subprocess.run``), the first attribute read that
    is not permitted (``os.system``, ``subprocess.os``, ``sys.modules``,
    ``subprocess.__dict__``), the star import (``subprocess.*``), the bare
    module (``subprocess``, ``os.path``), ``__import__``, ``alias <name>``
    for a name that imports in one scope bind to two different watched paths,
    the first dunder or namespace read on any other chain (``globals``,
    ``e.__init__``), the dunder or namespace reader an import names, or
    ``getattr <name>`` for a getattr of one by a literal name.
    """
    tree = ast.parse(source)
    # Each scope's imports, keyed by the id of the scope they bind in: their own,
    # or the module for a name that scope declares global or nonlocal. homes
    # maps each import alias to that scope.
    imported: dict[int, dict[str, list[tuple[str, ...]]]] = {}
    homes: dict[int, int] = {}
    for scope in [tree, *(node for node in ast.walk(tree) if isinstance(node, SCOPES))]:
        nodes = own_nodes(scope)
        declared = {name for node in nodes if isinstance(node, (ast.Global, ast.Nonlocal))
                    for name in node.names}
        for node in nodes:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    binding = import_binding(node, alias)
                    if binding is None:
                        continue
                    home = tree if binding[0] in declared else scope
                    homes[id(alias)] = id(home)
                    paths = imported.setdefault(id(home), {}).setdefault(binding[0], [])
                    if binding[1] not in paths:
                        paths.append(binding[1])
    type_positions: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.arg, ast.AnnAssign)) and node.annotation is not None:
            type_positions.update(id(part) for part in type_parts(node.annotation))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            type_positions.update(id(part) for part in type_parts(node.returns))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "isinstance" and len(node.args) == 2):
            kinds = [node.args[1]]
            while kinds:
                kind = kinds.pop()
                if isinstance(kind, ast.Tuple):
                    kinds.extend(kind.elts)
                else:
                    type_positions.add(id(kind))
    callees = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    attribute_bases = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    def scope_env(outer: dict[str, tuple[str, ...]], scope: ast.AST) -> dict[str, tuple[str, ...]]:
        # A name bound to two paths in one scope is flagged at each import; uses resolve to the first.
        env = dict(outer)
        env.update((name, paths[0]) for name, paths in imported.get(id(scope), {}).items())
        return env

    def resolve(node: ast.expr, env: dict[str, tuple[str, ...]]) -> tuple[str, ...] | None:
        attrs: list[str] = []
        while isinstance(node, ast.Attribute):
            attrs.insert(0, node.attr)
            node = node.value
        if isinstance(node, ast.Name) and node.id in env:
            return env[node.id] + tuple(attrs)
        return None

    def checked_launcher(node: ast.expr, env: dict[str, tuple[str, ...]]) -> str | None:
        path = resolve(node, env)
        if (path is not None and len(path) == 2 and path[0] == "subprocess"
                and path[1] in ISOLATING_STDIN_VALUES):
            return path[1]
        return None

    def launch_of(node: ast.expr, env: dict[str, tuple[str, ...]]) -> str | None:
        part = node
        while isinstance(part, ast.Attribute):
            if part.attr == "__import__":
                return "__import__"
            part = part.value
        if isinstance(part, ast.Name) and part.id == "__import__":
            return "__import__"
        path = resolve(node, env)
        return None if path is None else launch_label(path)

    def isolates(call: ast.Call, launcher: str, env: dict[str, tuple[str, ...]]) -> bool:
        keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        stdin = keywords.get("stdin")
        isolating = {("subprocess", name) for name in ISOLATING_STDIN_VALUES[launcher]}
        if stdin is not None and resolve(stdin, env) in isolating:
            return True
        data = keywords.get("input")
        return (launcher in INPUT_LAUNCHERS and data is not None
                and not (launcher == "run" and isinstance(data, ast.Constant) and data.value is None))

    def getattr_reach(call: ast.Call) -> str | None:
        func = call.func
        name = (func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None)
        if name != "getattr" or len(call.args) < 2:
            return None
        key = call.args[1]
        if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                and (is_dunder(key.value) or key.value in NAMESPACE_READERS)):
            return "getattr " + key.value
        return None

    found: list[tuple[str, str, int]] = []

    def visit(node: ast.AST, scope: tuple[str, ...],
              env: dict[str, tuple[str, ...]], closure: dict[str, tuple[str, ...]]) -> None:
        # A function or class body sees its own imports over those of the
        # scopes around it, but a function does not see the imports of a class
        # body it is in. Decorators, defaults, annotations and bases are read
        # in the enclosing scope.
        body: set[int] = set()
        body_env, body_closure = env, closure
        if isinstance(node, SCOPES):
            body = {id(part) for part in node.body}
            body_env = scope_env(closure, node)
            body_closure = closure if isinstance(node, ast.ClassDef) else body_env
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, SCOPES):
                inner = scope + (child.name,)
            where = ".".join(inner) or "<module>"
            child_env, child_closure = (body_env, body_closure) if id(child) in body else (env, closure)
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    if (isinstance(child, ast.ImportFrom) and alias.name == "*" and child.level == 0
                            and child.module.split(".")[0] in WATCHED_MODULES):
                        found.append((where, child.module + ".*", child.lineno))
                    binding = import_binding(child, alias)
                    if binding is not None and len(imported[homes[id(alias)]][binding[0]]) > 1:
                        found.append((where, "alias " + binding[0], child.lineno))
                    label = import_reach(child, alias)
                    if label is not None:
                        found.append((where, label, child.lineno))
            elif isinstance(child, ast.Call):
                launcher = checked_launcher(child.func, child_env)
                if launcher is not None and not isolates(child, launcher, child_env):
                    found.append((where, "subprocess." + launcher, child.lineno))
                label = getattr_reach(child)
                if label is not None:
                    found.append((where, label, child.lineno))
            elif isinstance(child, (ast.Name, ast.Attribute)) and id(child) not in attribute_bases:
                label = launch_of(child, child_env) if isinstance(child.ctx, ast.Load) else None
                launcher = checked_launcher(child, child_env)
                as_type = launcher == "Popen" and id(child) in type_positions
                if launcher is not None and (id(child) in callees or as_type):
                    label = None
                elif label is None:
                    label = opaque_reach(child)
                if label is not None:
                    found.append((where, label, child.lineno))
            visit(child, inner, child_env, child_closure)

    module_env = scope_env({name: (name,) for name in WATCHED_MODULES}, tree)
    visit(tree, (), module_env, module_env)
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


def flagged_reason(label: str) -> str:
    """Say why a launch label from stdin_inheriting_launches() is flagged and what clears it."""
    module, _, attr = label.rpartition(".")
    if module == "subprocess" and attr in ISOLATING_STDIN_VALUES:
        return "launch that may inherit stdin (pass stdin=subprocess.DEVNULL or add a reasoned exemption)"
    if (module in PERMITTED_ATTRIBUTES and attr.isidentifier() and not is_dunder(attr)
            and attr not in PERMITTED_ATTRIBUTES[module]):
        return (f"{attr} is not on PERMITTED_ATTRIBUTES for {module} "
                f"(read {module} only through that table or add a reasoned exemption)")
    return "read the check cannot follow statically (remove it or add a reasoned exemption)"


class StdinIsolationTests(unittest.TestCase):
    """Every process launch in aiqt_corpus.py keeps the caller's standard input.

    A helper called from inside a git hook would otherwise hand the hook's own
    stdin (a pre-push hook's ref lines) to the child, which can consume it.

    Threat model: the check guards aiqt_corpus.py against an accidental launch
    that inherits stdin, by allowlist: the module may reach subprocess, os and
    sys only in the ways listed below, and any read the check cannot follow is
    flagged rather than passed. It is not an adversarial boundary. Code written
    to evade a static check (eval, exec, compile, names built at run time,
    other modules) is the disclosed residual under Not enforced.

    Enforced, by reading the module's syntax tree, unless one exemption names
    the launch by enclosing qualname and launch label:

    * each call to subprocess.run, check_output or Popen passes
      stdin=subprocess.DEVNULL or stdin=subprocess.PIPE, and each call to
      subprocess.call or check_call passes stdin=subprocess.DEVNULL (they never
      close a PIPE, so a child that reads stdin waits on it forever); run may
      pass input= with any value but a literal None instead, and check_output
      input= with any value. Any other stdin value (None, sys.stdin, a
      descriptor number) or a ``**`` mapping, whose keys cannot be read
      statically, is flagged;
    * the watched modules (subprocess, os, os.path and sys) are read only
      through this closed list, and any other attribute of them is flagged
      wherever it appears, launcher or not
      (subprocess.getoutput, os.system, os.fork, sys.modules, subprocess.os):

      - subprocess: DEVNULL, PIPE, STDOUT, CalledProcessError, TimeoutExpired,
        SubprocessError and CompletedProcess, plus the five launchers above as
        callees, and Popen also as a type (as a name or chain in an
        annotation, alone, subscripted or in a union but not inside a call,
        or as the second argument of isinstance, alone or in a tuple at any
        depth);
      - os: path, environ, getenv, getcwd, fspath, sep, devnull and _exit;
      - os.path: join, exists, isdir, isfile, dirname, basename, abspath,
        realpath, relpath and splitext;
      - sys: argv, stdin, stdout, stderr, exit, executable and version_info;

    * a dunder attribute is flagged wherever it appears, whatever it is read
      from: a watched module, a local name, a call's or a subscript's result
      (subprocess.__dict__, e.__init__ for any e, f().__init__); so is a
      dunder name that is read, other than ``__name__``. A dunder the module
      defines (a def name, an assignment target such as ``__all__`` or
      ``__slots__``) is not read, and ``__future__`` is allowed as the module
      of a from-import;
    * a name or attribute that reads a namespace or a frame (globals, locals,
      vars, ``__builtins__``, f_globals, f_locals, f_builtins, f_back,
      gi_frame, cr_frame, ag_frame and tb_frame) is flagged whatever its base,
      as is an import of one of these names or of a dunder (``from builtins
      import __import__``, ``import __main__``) and a getattr whose attribute
      is a literal dunder or one of these names;
    * forms whose target cannot be read statically are flagged rather than
      passed: a launcher referenced other than as the callee of a call or as a
      type (held in a variable, given to functools.partial), subprocess, os,
      os.path or sys referenced other than as the base of an attribute (as in
      getattr), a star import from any of them, and any reference to
      ``__import__``;
    * a name that imports in one scope bind to two different modules or
      members is flagged at each of those imports.

    Names resolve through ``import`` of the watched modules (os.path included)
    under any alias and through ``from ... import`` of their members under any
    alias, per scope: an import is seen in the scope it is in and in the
    functions nested in it, an import in a class body is not seen in that
    class's methods, and an import of a name its scope declares global or
    nonlocal is taken as a module-level import. The names subprocess, os and
    sys resolve to those modules in every scope.

    Not enforced: launches through other modules (asyncio, pty, multiprocessing,
    posix, posixpath, importlib.import_module, inspect, gc); a lookup by a name
    built at run time (``getattr(builtins, name)``, ``getattr(e, '__in' +
    'it__')``); code compiled and run at run time (eval, exec, compile); a
    tracked name rebound after its import (``DEVNULL = None``); an input=
    value that is None only at run time; and modules other than
    aiqt_corpus.py.
    """

    MODULE = Path(__file__).resolve().parent / "aiqt_corpus.py"

    def test_every_launch_isolates_stdin(self):
        source = self.MODULE.read_text(encoding="utf-8")
        missing = unexempted_launches(stdin_inheriting_launches(source), STDIN_EXEMPT_CALLS)
        self.assertEqual(missing, [], "; ".join(f"{name} line {lineno} {label}: {flagged_reason(label)}"
                                                for name, label, lineno in missing))

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
            ("import subprocess\nsubprocess.call(['cat'], stdin=subprocess.PIPE)\n",
             [("<module>", "subprocess.call", 2)]),
            ("from subprocess import PIPE, check_call\ncheck_call(['cat'], stdin=PIPE)\n",
             [("<module>", "subprocess.check_call", 2)]),
            ("import subprocess\nsubprocess.run(['git'], input=None)\n",
             [("<module>", "subprocess.run", 2)]),
            ("import subprocess\nsubprocess.Popen(['git'], input='x')\n",
             [("<module>", "subprocess.Popen", 2)]),
            ("import subprocess\nsubprocess.check_call(['git'], stdin=subprocess.DEVNULL)\n", []),
            ("import subprocess as sp\nsp.Popen(['git'], stdin=sp.PIPE)\n", []),
            ("import subprocess\nsubprocess.run(['git'], stdin=subprocess.PIPE)\n"
             "subprocess.check_output(['git'], stdin=subprocess.PIPE)\n", []),
            ("from subprocess import DEVNULL as null, run\nrun(['git'], stdin=null)\n", []),
            ("import subprocess\ndef f(data):\n    subprocess.run(['git'], input=data)\n", []),
            ("import subprocess\nsubprocess.check_output(['git'], input=None)\n", []),
            ("import os, sys\nos._exit(0)\nos.path.join('a')\nsys.stderr.write('a')\n", []),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_reads_watched_modules_only_through_permitted_attributes(self):
        # An attribute of a watched module off the permitted list, or a dunder
        # anywhere on the chain, reaches other code and is flagged.
        cases = (
            ("import subprocess\nsubprocess.os.system('cat')\n",
             [("<module>", "subprocess.os", 2)]),
            ("import os\nos.sys.modules['subprocess'].run(['cat'])\n",
             [("<module>", "os.sys", 2)]),
            ("import subprocess\nsubprocess.__dict__['run'](['cat'])\n",
             [("<module>", "subprocess.__dict__", 2)]),
            ("import subprocess\nsubprocess.__getattribute__('run')(['cat'])\n",
             [("<module>", "subprocess.__getattribute__", 2)]),
            ("import subprocess\nsubprocess.CalledProcessError.__init__.__globals__['Popen']\n",
             [("<module>", "subprocess.CalledProcessError.__init__", 2)]),
            ("from subprocess import os as o\no.system('cat')\n", [("<module>", "subprocess.os", 2)]),
            ("import os\nos.path.os.system('cat')\n", [("<module>", "os.path.os", 2)]),
            ("import os.path as osp\nosp.os.system('cat')\n", [("<module>", "os.path.os", 2)]),
            ("from os import path\ngetattr(path, 'os').system('cat')\n", [("<module>", "os.path", 2)]),
            ("import os\nos.fork()\n", [("<module>", "os.fork", 2)]),
            ("import subprocess\ndef f() -> subprocess.os.system('cat'):\n    pass\n",
             [("f", "subprocess.os", 2)]),
            ("import subprocess\nisinstance(subprocess.Popen, type)\n",
             [("<module>", "subprocess.Popen", 2)]),
            ("import subprocess\nsubprocess.run(['git'], stdin=subprocess.DEVNULL)\n"
             "subprocess.CalledProcessError(1, 'git').returncode\n", []),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_flags_a_dunder_or_namespace_read_on_any_base(self):
        # A dunder or a namespace read reaches a module without naming it, so
        # it is flagged whatever the chain starts at: a local alias, a call or
        # subscript result, an except name, or no watched name at all.
        cases = (
            ("import subprocess\ne = subprocess.CalledProcessError\n"
             "e.__init__.__globals__['Popen'](['cat'])\n", [("<module>", "e.__init__", 3)]),
            ("import os\nenv = os.environ\nenv.__class__.__init__.__globals__['system']('cat')\n",
             [("<module>", "env.__class__", 3)]),
            ("import os\nj = os.path.join\nj.__globals__['os'].system('cat')\n",
             [("<module>", "j.__globals__", 3)]),
            ("import os\ng = os.getcwd\ng.__self__.system('cat')\n", [("<module>", "g.__self__", 3)]),
            ("import subprocess\n"
             "subprocess.CalledProcessError(1, 'x').__init__.__globals__['Popen'](['cat'])\n",
             [("<module>", "subprocess.CalledProcessError(1, 'x').__init__", 2)]),
            ("import subprocess\n"
             "[subprocess.CalledProcessError][0].__init__.__globals__['Popen'](['cat'])\n",
             [("<module>", "[subprocess.CalledProcessError][0].__init__", 2)]),
            ("import subprocess\ndef f():\n    try:\n"
             "        subprocess.run(['git'], stdin=subprocess.DEVNULL, check=True)\n"
             "    except subprocess.CalledProcessError as e:\n"
             "        e.__init__.__globals__['Popen'](['cat'])\n", [("f", "e.__init__", 6)]),
            ("import subprocess\nglobals()['subprocess'].Popen(['cat'])\n", [("<module>", "globals", 2)]),
            ("import subprocess\n(x for x in ()).gi_frame.f_globals['subprocess'].Popen(['cat'])\n",
             [("<module>", "(x for x in ()).gi_frame", 2)]),
            ("__builtins__['__import__']('subprocess').Popen(['cat'])\n",
             [("<module>", "__builtins__", 1)]),
            ("import builtins\nbuiltins.__dict__['__import__']('subprocess').Popen(['cat'])\n",
             [("<module>", "builtins.__dict__", 2)]),
            ("__spec__.loader\n", [("<module>", "__spec__", 1)]),
            ("x.__class__ = int\n", [("<module>", "x.__class__", 1)]),
            ("from builtins import __import__ as load\nload('subprocess')\n",
             [("<module>", "__import__", 1)]),
            ("from builtins import globals as g\ng()['subprocess']\n", [("<module>", "globals", 1)]),
            ("import __main__\n__main__.subprocess\n",
             [("<module>", "__main__", 1), ("<module>", "__main__", 2)]),
            ("import os\ngetattr(os.getcwd, '__self__').system('cat')\n",
             [("<module>", "getattr __self__", 2)]),
            ("import builtins\nbuiltins.getattr(gen, 'gi_frame')\n",
             [("<module>", "getattr gi_frame", 2)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_flags_every_namespace_reader(self):
        readers = ("globals", "locals", "vars", "__builtins__", "f_globals", "f_locals", "f_builtins",
                   "f_back", "gi_frame", "cr_frame", "ag_frame", "tb_frame")
        for name in readers:
            with self.subTest(name=name):
                self.assertEqual(stdin_inheriting_launches(f"{name}\n"), [("<module>", name, 1)])
                self.assertEqual(stdin_inheriting_launches(f"x().{name}.y\n"),
                                 [("<module>", f"x().{name}", 1)])

    def test_detector_allows_the_dunders_the_module_defines(self):
        # The dunders aiqt_corpus.py has: definitions, the __name__ read and
        # the __future__ import. None of them is read through an attribute.
        source = ("from __future__ import annotations\n__all__ = ['C']\nclass C:\n"
                  "    __slots__ = ('a',)\n    def __init__(self) -> None:\n"
                  "        self.a = '__pycache__'\nif __name__ == '__main__':\n    getattr(C(), 'a')\n")
        self.assertEqual(stdin_inheriting_launches(source), [])

    def test_failure_message_names_the_table_for_an_attribute(self):
        launcher = ("launch that may inherit stdin "
                    "(pass stdin=subprocess.DEVNULL or add a reasoned exemption)")
        opaque = "read the check cannot follow statically (remove it or add a reasoned exemption)"
        cases = (
            ("subprocess.run", launcher),
            ("os.system", "system is not on PERMITTED_ATTRIBUTES for os "
                          "(read os only through that table or add a reasoned exemption)"),
            ("os.path.os", "os is not on PERMITTED_ATTRIBUTES for os.path "
                           "(read os.path only through that table or add a reasoned exemption)"),
            ("subprocess.getoutput",
             "getoutput is not on PERMITTED_ATTRIBUTES for subprocess "
             "(read subprocess only through that table or add a reasoned exemption)"),
            ("subprocess.*", opaque),
            ("os.path", opaque),
            ("subprocess.__dict__", opaque),
            ("e.__init__", opaque),
            ("globals", opaque),
            ("alias p", opaque),
        )
        for label, reason in cases:
            with self.subTest(label=label):
                self.assertEqual(flagged_reason(label), reason)
        # The module check reports the reason with each launch.
        result = unittest.TestResult()
        with tempfile.TemporaryDirectory() as d:
            module = Path(d) / "aiqt_corpus.py"
            source = self.MODULE.read_text(encoding="utf-8") + "\nos.system('cat')\n"
            module.write_text(source, encoding="utf-8")
            with mock.patch.object(StdinIsolationTests, "MODULE", module):
                StdinIsolationTests("test_every_launch_isolates_stdin").run(result)
        self.assertEqual(len(result.failures), 1)
        self.assertIn("os.system: system is not on PERMITTED_ATTRIBUTES for os", result.failures[0][1])

    def test_detector_flags_a_name_bound_to_two_watched_paths(self):
        # Two imports of one name in one scope are flagged at each; a global or
        # nonlocal declaration puts a function's import in the module scope.
        cases = (
            ("import subprocess as _sp\nimport os as _sp\n",
             [("<module>", "alias _sp", 1), ("<module>", "alias _sp", 2)]),
            ("import subprocess as _sp\ndef f():\n    global _sp\n    import os as _sp\n",
             [("<module>", "alias _sp", 1), ("f", "alias _sp", 4)]),
            ("def f():\n    import subprocess as _sp\ndef g():\n    import os as _sp\n", []),
            ("import sys\ndef f():\n    import sys\n    sys.exit(0)\n", []),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_resolves_imports_per_scope(self):
        # One scope's alias must not resolve another scope's use of the name,
        # and must resolve its own uses and those of functions nested in it.
        cases = (
            ("def f():\n    import os.path as p\n    return p.join('a')\n"
             "def g(p):\n    return p.system\n", []),
            ("class C:\n    import os.path as p\n    def m(self, p):\n        return p.system\n", []),
            ("def f():\n    import subprocess as _sp\n    _sp.run(['git'], stdin=_sp.DEVNULL)\n"
             "def g():\n    import os as _sp\n    _sp.system('cat')\n",
             [("g", "os.system", 6)]),
            ("def f():\n    import os as p\n    def g():\n        p.system('cat')\n",
             [("f.g", "os.system", 4)]),
            ("import subprocess as p\ndef f():\n    import os as p\n    p.system('cat')\n",
             [("f", "os.system", 4)]),
            ("def f():\n    global p\n    import os as p\ndef g():\n    p.system('cat')\n",
             [("g", "os.system", 5)]),
            ("def f():\n    p = None\n    def g():\n        nonlocal p\n        import os as p\n"
             "    p.system('cat')\n", [("f", "os.system", 6)]),
            ("import os as p\ndef f(x=p.system('cat')):\n    import subprocess as p\n",
             [("f", "os.system", 2)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_accepts_popen_as_a_type(self):
        cases = (
            "import subprocess\ndef f(p: subprocess.Popen) -> subprocess.Popen | None:\n"
            "    q: list[subprocess.Popen] = [p]\n    return q[0]\n",
            "import subprocess\ndef f(p):\n    return isinstance(p, subprocess.Popen)\n",
            "from subprocess import Popen\ndef f(p):\n    return isinstance(p, (Popen, int))\n",
            "import subprocess\ndef f(p):\n    return isinstance(p, (int, (subprocess.Popen,)))\n",
            "import subprocess, typing\ndef f(p: typing.Callable[[subprocess.Popen], None]) -> None:\n"
            "    q: dict[str, subprocess.Popen | int] = {}\n",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), [])

    def test_detector_flags_popen_passed_inside_an_annotation(self):
        # Only a name or chain, alone, subscripted or in a union, is a type;
        # Popen handed to a call or to another expression in an annotation is
        # held by that code and flagged.
        cases = (
            ("import subprocess\ndef f(p: wrap(subprocess.Popen)):\n    pass\n",
             [("f", "subprocess.Popen", 2)]),
            ("import subprocess\ndef f() -> make()[subprocess.Popen]:\n    pass\n",
             [("f", "subprocess.Popen", 2)]),
            ("import subprocess\nq: [subprocess.Popen][0] = None\n",
             [("<module>", "subprocess.Popen", 2)]),
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
             [("<module>", "os.spawnv", 2), ("<module>", "os.P_WAIT", 2)]),
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
        missing = unexempted_launches(stdin_inheriting_launches(mutant), STDIN_EXEMPT_CALLS)
        self.assertEqual([launch[:2] for launch in missing], [("git_show", "subprocess.check_output")])

    def test_detector_flags_the_module_with_one_value_inheriting(self):
        source = self.MODULE.read_text(encoding="utf-8")
        needle = "stdin=subprocess.DEVNULL).rstrip"
        self.assertEqual(source.count(needle), 1)
        mutant = source.replace(needle, "stdin=None).rstrip", 1)
        missing = unexempted_launches(stdin_inheriting_launches(mutant), STDIN_EXEMPT_CALLS)
        self.assertEqual([launch[:2] for launch in missing], [("git", "subprocess.check_output")])

    def test_one_valid_exemption_leaves_the_module_checks_green(self):
        # The module checks run on aiqt_corpus.py plus one launch that inherits
        # stdin: red without an exemption, green with one that names it.
        names = ("test_every_launch_isolates_stdin",
                 "test_exemptions_have_reasons_and_match_one_launch",
                 "test_detector_flags_the_module_with_one_keyword_dropped",
                 "test_detector_flags_the_module_with_one_value_inheriting")
        extra = "\n\ndef read_hook_input():\n    return subprocess.run(['git', 'status']).returncode\n"
        exemption = (("read_hook_input", "subprocess.run", "reads the hook input on purpose"),)
        source = self.MODULE.read_text(encoding="utf-8") + extra
        self.assertIn(("read_hook_input", "subprocess.run"),
                      [launch[:2] for launch in stdin_inheriting_launches(source)])
        this = sys.modules[__name__]
        outcomes = []
        with tempfile.TemporaryDirectory() as d:
            module = Path(d) / "aiqt_corpus.py"
            module.write_text(source, encoding="utf-8")
            for exempt in ((), exemption):
                result = unittest.TestResult()
                with (mock.patch.object(StdinIsolationTests, "MODULE", module),
                      mock.patch.object(this, "STDIN_EXEMPT_CALLS", exempt)):
                    unittest.TestSuite(StdinIsolationTests(name) for name in names).run(result)
                outcomes.append((result.testsRun, len(result.failures), len(result.errors)))
        self.assertEqual(outcomes, [(4, 3, 0), (4, 0, 0)])


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
