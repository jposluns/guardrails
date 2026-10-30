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
import symtable
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

# The only dunder names the module may read: each holds a string (or None), so
# it reaches neither a module nor a callable. A dunder it defines (a def name,
# an assignment target such as __all__ or __slots__) is not read, and
# __future__ is allowed as the module of a from-import.
PERMITTED_DUNDER_NAMES = frozenset(("__name__", "__file__", "__doc__"))

# The dunder attributes that hold a string (or None), read off anything but a
# watched module, which stays on its own closed list. __class__ is allowed
# only directly before one of them (self.__class__.__name__), so the class
# itself is never held.
STRING_DUNDER_ATTRIBUTES = frozenset(("__name__", "__qualname__", "__doc__", "__module__"))

# The dunder methods allowed as the callee of a call, read off any base but a
# watched module, a namespace reader or a chain already flagged
# (Exception.__init__(self, msg), super().__repr__(), super().__eq__(o)).
# Each is a protocol method whose result is None, a bool, a hash, a string or
# a value of its operands' own kind, never a module, a frame or a namespace,
# and the call's arguments are checked on their own: the initialisers and the
# finaliser, the class creation hooks, the comparison and hashing methods,
# the text forms, the arithmetic operators (binary, reflected, in-place and
# unary, as the data model lists them) and __setattr__ and __delattr__, whose
# literal attribute name is checked as one given to setattr is. Every other
# dunder stays flagged as a callee, among them those that return an
# arbitrary attribute, a reconstructor, a namespace, a class or a callable
# (__getattribute__, __getattr__, __reduce__, __reduce_ex__, __getstate__,
# __dir__, __subclasses__, __call__, __get__, __class_getitem__, __import__),
# and so is any of these held as a value rather than called.
DUNDER_CALLEES = frozenset((
    "__init__", "__post_init__", "__del__", "__new__", "__init_subclass__", "__set_name__",
    "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__", "__hash__",
    "__repr__", "__str__", "__format__",
    "__add__", "__sub__", "__mul__", "__matmul__", "__truediv__", "__floordiv__", "__mod__",
    "__divmod__", "__pow__", "__lshift__", "__rshift__", "__and__", "__xor__", "__or__",
    "__radd__", "__rsub__", "__rmul__", "__rmatmul__", "__rtruediv__", "__rfloordiv__", "__rmod__",
    "__rdivmod__", "__rpow__", "__rlshift__", "__rrshift__", "__rand__", "__rxor__", "__ror__",
    "__iadd__", "__isub__", "__imul__", "__imatmul__", "__itruediv__", "__ifloordiv__", "__imod__",
    "__ipow__", "__ilshift__", "__irshift__", "__iand__", "__ixor__", "__ior__",
    "__neg__", "__pos__", "__abs__", "__invert__",
    "__setattr__", "__delattr__",
))

# The callees, by name or final attribute, that read, write or delete the
# attribute an argument names, each with the position of that name keyed by
# the number of positional arguments (None for any other number): getattr,
# setattr and delattr take it second; __setattr__ and __delattr__ take it
# first called bound (super().__setattr__(k, v), super().__delattr__(k)) and
# second called through a class (object.__setattr__(self, k, v)), which the
# number of arguments tells apart, so a bound call's value is never read as a
# name. Any other number, which both forms reject, checks both positions.
ATTRIBUTE_ACCESSORS = {"getattr": {None: (1,)}, "setattr": {None: (1,)}, "delattr": {None: (1,)},
                       "__setattr__": {2: (0,), 3: (1,), None: (0, 1)},
                       "__delattr__": {1: (0,), 2: (1,), None: (0, 1)}}

# The keyword arguments a __setattr__ or __delattr__ defined in Python accepts
# (super().__setattr__(name='__dict__', value={})): each counts as an argument
# when the number of arguments picks the name's position, and a literal name=
# is checked as a positional name is. Any other keyword, which may carry the
# name under the method's own parameter name, and a name= that is not a plain
# string literal (name=f'__dict__') are flagged, since the attribute name
# cannot be read statically.
ACCESSOR_KEYWORDS = {"__setattr__": ("name", "value"), "__delattr__": ("name",)}

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


def string_read(attrs: list[str] | tuple[str, ...], index: int) -> bool:
    """Return whether ``attrs[index]`` is a string dunder or __class__ directly before one."""
    if attrs[index] == "__class__" and index + 1 < len(attrs):
        index += 1
    return attrs[index] in STRING_DUNDER_ATTRIBUTES


def allowed_dunder(attrs: list[str] | tuple[str, ...], index: int, callee: bool) -> bool:
    """Return whether ``attrs[index]`` is a string read or a DUNDER_CALLEES method ending a callee."""
    return (string_read(attrs, index)
            or callee and index == len(attrs) - 1 and attrs[index] in DUNDER_CALLEES)


def opaque_attribute(name: str) -> bool:
    """Return whether the attribute ``name``, read alone, is a namespace reader or a non-string dunder."""
    return name in NAMESPACE_READERS or is_dunder(name) and name not in STRING_DUNDER_ATTRIBUTES


def launch_label(path: tuple[str, ...], callee: bool = False) -> str | None:
    """Return the launch label for ``path``, a watched module or a chain read from one, or ``None``.

    The label is ``path`` up to and including its first attribute that a
    watched module does not permit or, read off anything else, that is a
    dunder other than one allowed_dunder() allows (``callee`` says the chain
    is the callee of a call), or the whole of ``path`` when it names a
    watched module itself. ``None`` means every attribute on the way is
    permitted.
    """
    for depth in range(1, len(path)):
        attr = path[depth]
        permitted = PERMITTED_ATTRIBUTES.get(".".join(path[:depth]))
        if is_dunder(attr) and (permitted is not None or not allowed_dunder(path, depth, callee)):
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


def accessor_reach(call: ast.Call) -> str | None:
    """Return the label of an ATTRIBUTE_ACCESSORS call the check cannot pass, or ``None``.

    The label is ``<accessor> <name>`` for a literal attribute name, in the
    accessor's name position for its number of arguments (positional ones
    and its ACCESSOR_KEYWORDS) or given as name=, that is a dunder (other
    than a string one) or a namespace reader,
    ``<accessor> *`` or ``<accessor> **``
    for a starred or double-starred argument, and, for an accessor in
    ACCESSOR_KEYWORDS, ``<accessor> <keyword>=`` for a keyword it does not
    list (``val=``) or a name= that is not a plain string literal
    (``name=f'__dict__'``), each of whose attribute name cannot be read
    statically.
    """
    func = call.func
    name = (func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute) else None)
    if name not in ATTRIBUTE_ACCESSORS:
        return None
    if any(isinstance(arg, ast.Starred) for arg in call.args):
        return name + " *"
    if any(keyword.arg is None for keyword in call.keywords):
        return name + " **"
    accepted = ACCESSOR_KEYWORDS.get(name, ())
    for keyword in call.keywords if name in ACCESSOR_KEYWORDS else ():
        literal = isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str)
        if keyword.arg not in accepted or keyword.arg == "name" and not literal:
            return name + " " + keyword.arg + "="
    given = [keyword for keyword in call.keywords if keyword.arg in accepted]
    positions = ATTRIBUTE_ACCESSORS[name]
    count = len(call.args) + len(given)
    keys = [call.args[position] for position in positions.get(count, positions[None])
            if position < len(call.args)]
    keys.extend(keyword.value for keyword in given if keyword.arg == "name")
    for key in keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str) and opaque_attribute(key.value):
            return name + " " + key.value
    return None


def opaque_reach(node: ast.Name | ast.Attribute, callee: bool) -> str | None:
    """Return the first dunder or namespace read on the chain that ``node`` ends, or ``None``.

    The chain runs down through attributes to a base that may be any
    expression. A read name counts when it is a namespace reader or a dunder
    not in PERMITTED_DUNDER_NAMES, and an attribute when it is a namespace
    reader or a dunder that allowed_dunder() does not allow; the label is
    that name, or the chain up to that attribute as source text
    (``e.__init__``, ``[x][0].__init__``). So a chain that is the callee of a
    call (``callee``) and ends in a DUNDER_CALLEES method
    (``super().__init__``, ``Exception.__init__``) returns ``None`` unless a
    read before that method is flagged.
    """
    chain: list[ast.Attribute] = []
    while isinstance(node, ast.Attribute):
        chain.insert(0, node)
        node = node.value
    if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
            and (node.id in NAMESPACE_READERS
                 or is_dunder(node.id) and node.id not in PERMITTED_DUNDER_NAMES)):
        return node.id
    attrs = [part.attr for part in chain]
    for index, part in enumerate(chain):
        if (part.attr in NAMESPACE_READERS
                or is_dunder(part.attr) and not allowed_dunder(attrs, index, callee)):
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


def mangled(name: str, owner: str | None) -> str:
    """Return ``name`` as the compiler stores it inside class ``owner``: ``__p`` in ``C`` is ``_C__p``."""
    stem = (owner or "").lstrip("_")
    if not stem or not name.startswith("__") or name.endswith("__") or "." in name:
        return name
    return "_" + stem + name


def private_owners(tree: ast.Module) -> dict[int, str]:
    """Return the name of the innermost class around each node inside one, keyed by the node's id.

    A class's body and type parameters are inside it; its decorators, bases
    and keywords are read in the scope around it.
    """
    owners: dict[int, str] = {}
    pending: list[tuple[ast.AST, str | None]] = [(tree, None)]
    while pending:
        node, owner = pending.pop()
        if owner is not None:
            owners[id(node)] = owner
        outside: set[int] = set()
        if isinstance(node, ast.ClassDef):
            outside = {id(part) for part in (*node.decorator_list, *node.bases, *node.keywords)}
        for child in ast.iter_child_nodes(node):
            inside = isinstance(node, ast.ClassDef) and id(child) not in outside
            pending.append((child, node.name if inside else owner))
    return owners


class ScopeMarkers(ast.NodeTransformer):
    """Rewrite a copy of a module so that symtable reports the scope of each marked node.

    ``marks`` maps the id of a node of the copy to its marker, a name the
    module never uses: a marked name read becomes ``(marker, name)[1]``, a
    marked import is preceded by the statement ``marker``, and a match
    statement's subject becomes ``(marker, subject)[1]``, so each marker is
    read in the scope of the node it marks. Two rewrites keep scopes intact
    where symtable would drop them: a list, set or dict comprehension becomes
    a generator expression over the same clauses (the same scopes, but
    symtable folds an inlined comprehension into the scope around it, a
    class body included), and each annotation is also read by an expression
    statement just before its def or assignment (symtable records no scope
    for a function body's annotations, nor for any annotation under
    ``from __future__ import annotations``).
    """

    def __init__(self, marks: dict[int, str]) -> None:
        self.marks = marks

    @staticmethod
    def paired(marker: str, node: ast.expr) -> ast.expr:
        return ast.Subscript(ast.Tuple([ast.Name(marker, ast.Load()), node], ast.Load()),
                             ast.Constant(1), ast.Load())

    def visit_Name(self, node: ast.Name) -> ast.expr:
        return self.paired(self.marks[id(node)], node) if id(node) in self.marks else node

    def visit_Import(self, node: ast.Import | ast.ImportFrom) -> ast.stmt | list[ast.stmt]:
        if id(node) not in self.marks:
            return node
        return [ast.Expr(ast.Name(self.marks[id(node)], ast.Load())), node]

    visit_ImportFrom = visit_Import

    def visit_Match(self, node: ast.Match) -> ast.Match:
        marker = self.marks[id(node)]
        self.generic_visit(node)
        node.subject = self.paired(marker, node.subject)
        return node

    def visit_ListComp(self, node: ast.ListComp | ast.SetComp) -> ast.GeneratorExp:
        self.generic_visit(node)
        return ast.GeneratorExp(node.elt, node.generators)

    visit_SetComp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> ast.GeneratorExp:
        self.generic_visit(node)
        return ast.GeneratorExp(ast.Tuple([node.key, node.value], ast.Load()), node.generators)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST | list[ast.stmt]:
        self.generic_visit(node)
        args = node.args
        params = [param for param in (*args.posonlyargs, *args.args, args.vararg,
                                      *args.kwonlyargs, args.kwarg) if param is not None]
        annotations = [param.annotation for param in params if param.annotation is not None]
        if node.returns is not None:
            annotations.append(node.returns)
        if not annotations:
            return node
        for param in params:
            param.annotation = None
        node.returns = None
        return [ast.Expr(ast.Tuple(annotations, ast.Load())), node]

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_AnnAssign(self, node: ast.AnnAssign) -> list[ast.stmt]:
        self.generic_visit(node)
        annotation, node.annotation = node.annotation, ast.Constant(None)
        return [ast.Expr(annotation), node]


def binding_homes(source: str, tree: ast.Module,
                  names: set[str]) -> dict[int, list[tuple[int, str, bool]]] | None:
    """Return the binding scopes of each read of one of ``names`` and of each watched import.

    ``tree`` is ``source`` parsed. The result maps the id of each Name that
    reads one of ``names`` (in a match pattern too) and of each import alias
    that import_binding() resolves to a list of ``(table id, stored name,
    bound)``: the symtable scope that binds the name there, the name as that
    scope stores it, after private name mangling, and whether something
    binds it in that scope (an import, an assignment, a class, a def, a
    parameter, or, for the module, an assignment or import in any scope
    under a global declaration of the name). symtable reports no columns, so
    it is built for a copy of ``source`` with each such node marked
    (ScopeMarkers), and the scope holding a marker is the scope of its node.
    A name a scope declares global binds in the module; a free name (a
    nonlocal one too) binds in the nearest enclosing scope that is not a
    class body and holds it as a local; a scope that reads its class's
    namespace (``__classdict__`` is free in it, as in a type alias in a class
    body) reads a name that class body binds from it as the body does. A
    class body reads a name it binds itself from its own namespace once it
    has bound it there and from the module's globals before that, so such a
    read, and a read through ``__classdict__`` of a name the class binds,
    lists the class scope and then, when the module binds the name or some
    scope assigns or imports it under a global declaration, the module (a
    declaration alone binds nothing); every other read and every import
    lists one scope. ``None`` means symtable could not be built for
    ``source`` or its copy, or a marker or a name was not where it was
    placed.
    """
    try:
        symtable.symtable(source, "<source>", "exec")
    except SyntaxError:
        return None
    owners = private_owners(tree)
    in_patterns = {id(part) for node in ast.walk(tree) if isinstance(node, ast.match_case)
                   for part in ast.walk(node.pattern)}
    prefix = "scope_marker_"
    while prefix in source:
        prefix += "_"
    twin = ast.parse(source)
    marks: dict[int, str] = {}
    targets: dict[int, tuple[str, str, bool]] = {}
    for node, copy in zip(ast.walk(tree), ast.walk(twin)):
        marker = prefix + str(len(marks))
        if (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names
                and id(node) not in in_patterns):
            marks[id(copy)] = marker
            targets[id(node)] = (marker, mangled(node.id, owners.get(id(node))), True)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                binding = import_binding(node, alias)
                if binding is not None:
                    marks[id(copy)] = marker
                    targets[id(alias)] = (marker, mangled(binding[0], owners.get(id(node))), False)
        elif isinstance(node, ast.Match):
            marks[id(copy)] = marker
            for case in node.cases:
                for part in ast.walk(case.pattern):
                    if isinstance(part, ast.Name) and part.id in names:
                        targets[id(part)] = (marker, mangled(part.id, owners.get(id(part))), True)
    ScopeMarkers(marks).visit(twin)
    try:
        top = symtable.symtable(ast.unparse(twin), "<source>", "exec")
    except SyntaxError:
        return None
    tables: dict[str, symtable.SymbolTable] = {}
    parents: dict[int, symtable.SymbolTable] = {}
    # The names some scope assigns or imports under a global declaration, so
    # binds in the module; a global declaration with no such binding binds
    # nothing.
    global_bound: set[str] = set()
    pending = [top]
    while pending:
        table = pending.pop()
        for ident in table.get_identifiers():
            if ident.startswith(prefix):
                if ident in tables:
                    return None
                tables[ident] = table
        for symbol in table.get_symbols():
            if symbol.is_declared_global() and (symbol.is_assigned() or symbol.is_imported()):
                global_bound.add(symbol.get_name())
        for child in table.get_children():
            parents[child.get_id()] = table
            pending.append(child)

    def bound(table: symtable.SymbolTable, name: str) -> bool:
        return (name in table.get_identifiers() and table.lookup(name).is_local()
                or table is top and name in global_bound)

    def global_home(name: str) -> list[symtable.SymbolTable]:
        # The module, for a name it binds; a name it never binds reads nothing
        # from it.
        return [top] if bound(top, name) else []

    def binder(table: symtable.SymbolTable, name: str, read: bool) -> list[symtable.SymbolTable] | None:
        if name not in table.get_identifiers():
            return None
        symbol = table.lookup(name)
        if symbol.is_declared_global():
            return [top]
        if symbol.is_free():
            outer = parents.get(table.get_id())
            while outer is not None:
                if (outer.get_type() != "class" and name in outer.get_identifiers()
                        and outer.lookup(name).is_local()):
                    return [outer]
                outer = parents.get(outer.get_id())
            return None
        if symbol.is_local():
            # A class body reads its own name by LOAD_NAME: the class
            # namespace, then the module's globals until the class binds it.
            return [table] + global_home(name) if read and table.get_type() == "class" else [table]
        if (table.get_type() != "class" and "__classdict__" in table.get_identifiers()
                and table.lookup("__classdict__").is_free()):
            outer = parents.get(table.get_id())
            while outer is not None and outer.get_type() != "class":
                outer = parents.get(outer.get_id())
            if outer is not None and name in outer.get_identifiers() and outer.lookup(name).is_local():
                return [outer] + global_home(name)
        return [top]

    homes: dict[int, list[tuple[int, str, bool]]] = {}
    for key, (marker, name, read) in targets.items():
        table = tables.get(marker)
        found = None if table is None else binder(table, name, read)
        if found is None:
            return None
        homes[key] = [(home.get_id(), name, bound(home, name)) for home in found]
    return homes


def stdin_inheriting_launches(source: str) -> list[tuple[str, str, int]]:
    """Return ``(enclosing qualname, launch label, lineno)`` for each launch that may inherit stdin.

    :class:`StdinIsolationTests` states what is reported and what is not. The
    label is the launcher (``subprocess.run``), the first attribute read that
    is not permitted (``os.system``, ``subprocess.os``, ``sys.modules``,
    ``subprocess.__dict__``), the star import (``subprocess.*``), the bare
    module (``subprocess``, ``os.path``), ``__import__``, ``alias <name>``
    for a name that imports in one scope bind to two different watched paths,
    the first dunder or namespace read on any other chain (``globals``,
    ``e.__init__``), the dunder or namespace reader an import names,
    ``getattr <name>`` (or ``setattr``, ``delattr``, ``__setattr__``,
    ``__delattr__``) for an ATTRIBUTE_ACCESSORS call naming one by a literal
    name, ``getattr *`` or ``getattr **`` for one given a starred or
    double-starred argument, ``__setattr__ <keyword>=`` (or
    ``__delattr__``) for one given a keyword it does not accept or a name=
    that is not a plain string literal, ``store <module>.<attr>`` or ``delete
    <module>.<attr>`` for a store or delete of a watched module's attribute,
    ``match <name>`` for a class pattern keyword naming one, or ``symtable``
    (alone, at line 1) when binding_homes() cannot resolve the scopes.
    """
    tree = ast.parse(source)
    imports = sorted((node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))),
                     key=lambda node: (node.lineno, node.col_offset))
    bindings = [(alias, import_binding(node, alias)) for node in imports for alias in node.names]
    names = set(WATCHED_MODULES) | {binding[0] for _, binding in bindings if binding is not None}
    homes = binding_homes(source, tree, names)
    if homes is None:
        return [("<module>", "symtable", 1)]

    # Each watched import's paths, keyed by the id of the scope its name binds
    # in and the name as that scope stores it, in source order.
    imported: dict[int, dict[str, list[tuple[str, ...]]]] = {}
    for alias, binding in bindings:
        if binding is not None:
            home, name, _ = homes[id(alias)][0]
            paths = imported.setdefault(home, {}).setdefault(name, [])
            if binding[1] not in paths:
                paths.append(binding[1])
    # The paths each class attribute may hold, by attribute name, for each
    # one a class body assigns from a name or chain that reads a watched path
    # (``_os = _os``, as the standard library's dbm.dumb does); filled in
    # below, once resolve() can read the assigned values.
    members: dict[str, list[tuple[str, ...]]] = {}
    # The targets of an annotation with no value (subprocess.DEVNULL: int):
    # nothing is stored, so each is checked as a read.
    annotated_only = {id(node.target) for node in ast.walk(tree)
                      if isinstance(node, ast.AnnAssign) and node.value is None}
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
    # The __class__ names given as super's first argument, super(__class__,
    # self): the implicit cell holding the class being defined, which super()
    # with no arguments reads. Anywhere else __class__ is flagged.
    class_cells = {id(node.args[0]) for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "super" and len(node.args) == 2
                   and isinstance(node.args[0], ast.Name) and node.args[0].id == "__class__"}

    def resolve(node: ast.expr, held: bool = True) -> list[tuple[str, ...]]:
        # Each path the chain may read: a name reads, in each scope it may be
        # found in, the first path imported under it there or, failing that,
        # the watched module it names; with ``held``, a chain through a class
        # attribute in ``members`` also reads that attribute's paths. Without
        # ``held``, when a stdin value is credited, the name is taken for the
        # watched module it names only in a scope that does not bind it, and
        # a scope the name may be found in that has no such path binds it to
        # something else (DEVNULL = None in a class body, class subprocess,
        # a parameter named subprocess), so the chain reads nothing.
        attrs: list[str] = []
        while isinstance(node, ast.Attribute):
            attrs.insert(0, node.attr)
            node = node.value
        found: list[tuple[str, ...]] = []
        for home, name, bound in homes.get(id(node), []) if isinstance(node, ast.Name) else []:
            paths = imported.get(home, {}).get(name)
            module = node.id in WATCHED_MODULES and (held or not bound)
            path = paths[0] if paths else (node.id,) if module else None
            if path is None and not held:
                return []
            if path is not None and path + tuple(attrs) not in found:
                found.append(path + tuple(attrs))
        for index, attr in enumerate(attrs if held else []):
            if attr in members:
                found.extend(path + tuple(attrs[index + 1:]) for path in members[attr]
                             if path + tuple(attrs[index + 1:]) not in found)
                break
        return found

    def launcher_of(path: tuple[str, ...]) -> str | None:
        if len(path) == 2 and path[0] == "subprocess" and path[1] in ISOLATING_STDIN_VALUES:
            return path[1]
        return None

    def launch_of(node: ast.expr, callee: bool) -> str | None:
        # A launcher as the callee of a call is checked at the call, and
        # Popen as a type is allowed.
        part = node
        while isinstance(part, ast.Attribute):
            if part.attr == "__import__":
                return "__import__"
            part = part.value
        if isinstance(part, ast.Name) and part.id == "__import__":
            return "__import__"
        for path in resolve(node):
            launcher = launcher_of(path)
            if launcher is not None and (callee or launcher == "Popen" and id(node) in type_positions):
                continue
            label = launch_label(path, callee)
            if label is not None:
                return label
        return None

    def written(node: ast.Name | ast.Attribute) -> str | None:
        # A store or delete of an attribute of a watched module changes what
        # every later read of it sees (subprocess.DEVNULL = None); the base of
        # any other store is checked as a read.
        action = "delete " if isinstance(node.ctx, ast.Del) else "store "
        for path in resolve(node.value) if isinstance(node, ast.Attribute) else []:
            if ".".join(path) not in PERMITTED_ATTRIBUTES:
                label = launch_label(path)
            else:
                label = action + ".".join(path + (node.attr,))
            if label is not None:
                return label
        return None

    def isolates(call: ast.Call, launcher: str) -> bool:
        # A class attribute is never credited as an isolating value: another
        # class may bind the same attribute name to something else. Nor is a
        # name that any scope it may be read from binds other than by a
        # watched import.
        keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        stdin = keywords.get("stdin")
        isolating = {("subprocess", name) for name in ISOLATING_STDIN_VALUES[launcher]}
        paths = [] if stdin is None else resolve(stdin, held=False)
        if paths and all(path in isolating for path in paths):
            return True
        data = keywords.get("input")
        return (launcher in INPUT_LAUNCHERS and data is not None
                and not (launcher == "run" and isinstance(data, ast.Constant) and data.value is None))

    # A class body's plain assignments, outside its methods and nested
    # classes, in source order: a target name holds each path its value reads.
    classes = sorted((node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)),
                     key=lambda node: (node.lineno, node.col_offset))
    for cls in classes:
        pending: list[ast.AST] = list(reversed(cls.body))
        while pending:
            statement = pending.pop()
            if isinstance(statement, SCOPES):
                continue
            if isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value is not None:
                paths = resolve(statement.value)
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                for target in targets if paths else []:
                    if isinstance(target, ast.Name):
                        held = members.setdefault(target.id, [])
                        held.extend(path for path in paths if path not in held)
            pending.extend(reversed(list(ast.iter_child_nodes(statement))))

    found: list[tuple[str, str, int]] = []

    def visit(node: ast.AST, scope: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            inner = scope
            if isinstance(child, SCOPES):
                inner = scope + (child.name,)
            where = ".".join(inner) or "<module>"
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    if (isinstance(child, ast.ImportFrom) and alias.name == "*" and child.level == 0
                            and child.module.split(".")[0] in WATCHED_MODULES):
                        found.append((where, child.module + ".*", child.lineno))
                    binding = import_binding(child, alias)
                    if binding is not None:
                        home, name, _ = homes[id(alias)][0]
                        if len(imported[home][name]) > 1:
                            found.append((where, "alias " + binding[0], child.lineno))
                    label = import_reach(child, alias)
                    if label is not None:
                        found.append((where, label, child.lineno))
            elif isinstance(child, ast.Call):
                for path in resolve(child.func):
                    launcher = launcher_of(path)
                    if launcher is not None and not isolates(child, launcher):
                        found.append((where, "subprocess." + launcher, child.lineno))
                label = accessor_reach(child)
                if label is not None:
                    found.append((where, label, child.lineno))
            elif (isinstance(child, (ast.Name, ast.Attribute)) and id(child) not in attribute_bases
                  and id(child) not in class_cells):
                callee = id(child) in callees
                if isinstance(child.ctx, ast.Load) or id(child) in annotated_only:
                    label = launch_of(child, callee)
                else:
                    label = written(child)
                if label is None:
                    label = opaque_reach(child, callee)
                if label is not None:
                    found.append((where, label, child.lineno))
            elif isinstance(child, ast.MatchClass):
                for attr in child.kwd_attrs:
                    if opaque_attribute(attr):
                        found.append((where, "match " + attr, child.lineno))
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


def flagged_reason(label: str) -> str:
    """Say why a launch label from stdin_inheriting_launches() is flagged and what clears it."""
    if label == "symtable":
        return "scopes the check cannot resolve: symtable cannot be built for the module (fix its source)"
    action, _, target = label.partition(" ")
    if action in ("store", "delete") and target.split(".")[0] in WATCHED_MODULES:
        return (f"{action} of a watched module's attribute, which changes every later read of it "
                f"(remove it or add a reasoned exemption)")
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
    sys only in the ways listed below, and each form listed below that the
    check cannot follow is flagged rather than passed. It is not an
    adversarial boundary. Code written to evade a static check (eval, exec,
    compile, names built at run time, other modules and their helpers) is the
    disclosed residual under Not enforced.

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
      dunder name that is read. Allowed, because each holds a string (or
      None) and so reaches neither a module nor a callable: the attributes
      ``__name__``, ``__qualname__``, ``__doc__`` and ``__module__`` read off
      anything but a watched module (``fn.__name__``, ``type(x).__name__``),
      ``__class__`` directly before one of them
      (``self.__class__.__name__``), and the names ``__name__``,
      ``__file__`` and ``__doc__``;
    * a dunder method that is immediately called is allowed, on a base that
      is not a watched module, a namespace reader or a chain already flagged
      (``Exception.__init__(self, msg)``, ``B.__post_init__(self)``,
      ``super().__repr__()``, ``super().__new__(cls, v)``), when it is on
      this closed list of methods whose result is None, a bool, a hash, a
      string or a value of the operands' own kind, never a module, a frame
      or a namespace (DUNDER_CALLEES); the call's arguments are checked on
      their own:

      - the initialisers and the finaliser: __init__, __post_init__ and
        __del__;
      - the class creation hooks: __new__, __init_subclass__ and
        __set_name__;
      - comparison and hashing: __eq__, __ne__, __lt__, __le__, __gt__,
        __ge__ and __hash__;
      - the text forms: __repr__, __str__ and __format__;
      - the arithmetic operators the data model lists, binary (__add__,
        __sub__, __mul__, __matmul__, __truediv__, __floordiv__, __mod__,
        __divmod__, __pow__, __lshift__, __rshift__, __and__, __xor__,
        __or__), each also reflected (__radd__ and so on) and, but for
        __divmod__, in place (__iadd__ and so on), and unary (__neg__,
        __pos__, __abs__ and __invert__);
      - __setattr__ and __delattr__, whose literal attribute name is checked
        as one given to setattr below: first when the call is bound (two
        arguments to __setattr__, one to __delattr__, as in
        ``super().__setattr__(k, v)``) and second when it is through a class
        (three or two, as in ``object.__setattr__(self, k, v)``), so a bound
        call's value is not taken for a name; with any other number of
        arguments both positions are checked. A name= keyword (and value=
        to __setattr__), which a method defined in Python accepts, counts as
        an argument, and a literal name= is checked too
        (``super().__setattr__(name='__dict__', value={})``); any other
        keyword, which may carry the name under the method's own parameter
        name (``C.__setattr__(o, '__dict__', val={})``), and a name= that is
        not a plain string literal (``name=f'__dict__'``) are flagged;

      ``__class__`` is allowed as a name only as super's first argument
      (``super(__class__, self).__init__()``), where it is the implicit cell
      holding the class being defined. Any other dunder stays flagged as a
      callee, among them those that return an arbitrary attribute, a
      reconstructor, a namespace, a class or a callable (__getattribute__,
      __getattr__, __reduce__, __reduce_ex__, __getstate__, __dir__,
      __subclasses__, __call__, __get__, __class_getitem__, __import__), as
      is any listed method held as a value rather than called
      (``init = Exception.__init__``), and any other dunder or namespace read
      on such a chain (``super().__init__.__globals__``,
      ``fn.__name__.__class__``). A dunder the module defines (a def name, an
      assignment target such as ``__all__`` or ``__slots__``) is not read,
      and ``__future__`` is allowed as the module of a from-import;
    * a name or attribute that reads a namespace or a frame (globals, locals,
      vars, ``__builtins__``, f_globals, f_locals, f_builtins, f_back,
      gi_frame, cr_frame, ag_frame and tb_frame) is flagged whatever its base,
      as is an import of one of these names or of a dunder (``from builtins
      import __import__``, ``import __main__``), a getattr, setattr, delattr,
      __setattr__ or __delattr__ call whose attribute is a literal dunder
      (other than the four string attributes above) or one of these names, or
      that is given a starred or double-starred argument
      (``getattr(x, *['__init__'])``), whose attribute name cannot be read
      statically, and a class pattern keyword naming one
      (``case C(__init__=f)``), which reads that attribute off the subject;
    * ordinary reads stay flagged on purpose where they reach a namespace or
      a non-string dunder, so each needs a reasoned exemption:
      ``self.__dict__``, ``vars(args)``, an enum's ``__members__``, an
      exception's ``__cause__``, ``__annotations__`` and a read of
      ``__slots__``;
    * a store or delete of any attribute of a watched module
      (``subprocess.DEVNULL = None``, ``subprocess.PIPE = 0``, an augmented
      assignment, ``del subprocess.DEVNULL``) is flagged, since it changes
      every later launch that reads it, and a store or delete through any
      other chain from a watched module is checked as a read of its base
      (``subprocess.Popen.x = 1`` is flagged as ``subprocess.Popen``); an
      annotation with no value (``subprocess.DEVNULL: int``) stores nothing,
      so its target is checked as a read;
    * forms whose target cannot be read statically are flagged rather than
      passed: a launcher referenced other than as the callee of a call or as a
      type (held in a variable, given to functools.partial), subprocess, os,
      os.path or sys referenced other than as the base of an attribute (as in
      getattr), a star import from any of them, and any reference to
      ``__import__``;
    * a name that imports in one scope bind to two different modules or
      members is flagged at each of those imports;
    * source that parses but for which symtable cannot be built (a nonlocal
      name with no binding, a parameter declared global) flags the module.

    Names resolve through ``import`` of the watched modules (os.path included)
    under any alias and through ``from ... import`` of their members under any
    alias, in the scope that binds the name, as the standard library's
    symtable reports it for this Python (binding_homes()): an import binds in
    the scope it is in unless that scope declares the name global (then the
    module) or nonlocal (then the nearest enclosing function that holds it,
    however that function binds it: a parameter, an assignment, or a walrus
    in a comprehension or in a nested definition's defaults, decorators or
    bases), and a name read anywhere resolves to the imports of the scope
    symtable binds it in. So an import in a class body is not seen in that
    class's methods, nor in a lambda or comprehension in that body, though a
    comprehension's first iterable, read in the class body, sees it; and a
    parameter or comprehension variable shadows an import around it. Each
    annotation is read in the scope it is written in (a method's in its class
    body, a function body's in that body), under the annotations future
    import too. A class body reads a name it binds itself (by an import, a
    def, an assignment, an annotation alone, a loop, a walrus or a with
    target) from its own namespace once it has bound it there and from the
    module's globals before that, and a type alias in that body reads the
    class's names the same way. Such a read resolves to the imports of the
    class and, when the module binds the name, of the module: it is flagged
    if either path is (``import os as p`` then ``out = p.system('cat')``
    before ``def p`` in a class), and as a stdin value it is credited only
    when every scope it may be read from holds an isolating import (or, for
    the name subprocess, when no such scope binds it at all, as below), so a
    class that binds the name other than by a watched import (``DEVNULL =
    None``, ``for PIPE in (None,)``) is never credited through the module's
    import. The cost is that a read before such a binding, which does read
    the module's import, is flagged as well. A class attribute that a
    plain assignment in a class body sets from a name or chain resolving to
    a watched path (``_os = _os``) holds that path: an attribute of that
    name read off any base (``self._os.system``) resolves through it, though
    it is never credited as an isolating stdin value, since another class
    may give the name another value. The names subprocess, os and sys
    resolve to those modules in every scope where no import binds them to
    another path, so a read through them is checked as a read of the
    module. A stdin value read through subprocess with no import of it is
    credited only when no scope the read may resolve through binds the name
    (by an assignment, a class, a def, a parameter, an import of another
    name, or an assignment or import under a global or nonlocal
    declaration): any such binding without an isolating import of that
    scope (``class subprocess: DEVNULL = None``, ``def f(subprocess)``)
    means the value is not credited. A global declaration under which no
    scope assigns or imports the name binds nothing.

    Not enforced: launches through other modules (asyncio, pty, multiprocessing,
    posix, posixpath, importlib.import_module, inspect, gc); a read through
    another module's helper that takes the attribute name as data
    (``operator.attrgetter('__init__')``, ``string.Formatter().get_field``,
    getattr passed as a value as in ``functools.reduce(getattr, names, e)``);
    the attributes a class pattern reads by position through a class's
    ``__match_args__``; a lookup by a name built at run time
    (``getattr(builtins, name)``, ``getattr(e, '__in' + 'it__')``); code
    compiled and run at run time (eval, exec, compile); a tracked name
    rebound or unbound after its import in the scope that imports it
    (``DEVNULL = None``, an except handler's target as in ``except OSError
    as DEVNULL``, ``del DEVNULL``), including super, object
    or a base class rebound; an import's own binding rebound from another
    scope through a global or nonlocal declaration (``global DEVNULL`` then
    ``DEVNULL = None`` in a function); a class-body name supplied by the
    namespace a metaclass's ``__prepare__`` returns; a name supplied by
    builtins (``builtins.subprocess = fake``), which the check takes for
    the watched module it names when no scope binds it; an object whose
    ``__name__``, ``__qualname__``,
    ``__doc__`` or ``__module__`` holds something other than a string; a
    class whose DUNDER_CALLEES method returns something other than its
    protocol's kind of value; a launcher instance's __init__ run again
    (``p.__init__(['cat'])`` on a Popen instance,
    ``type(proc).__init__(proc, ['cat'])``), which starts a new process but
    is not flagged, since __init__ is allowed as a callee and the instance's
    class is not known statically; a private name read in a class whose
    mangled form only a module-level import binds (``import os as _C__p``
    read as ``__p`` in class C), which the check does not resolve; a class
    attribute set other than by a plain assignment of a name or chain
    (unpacked from a tuple, wrapped as in ``staticmethod(_run)``, set through
    setattr or from outside the class body); an input= value that is None
    only at run time; and modules other than aiqt_corpus.py.
    """

    MODULE = Path(__file__).resolve().parent / "aiqt_corpus.py"

    # A stdin value read through subprocess where a nearer scope binds the
    # name to something else: a class, a class in a function, a parameter.
    SUBPROCESS_REBOUND = (
        ("from subprocess import run\nclass subprocess:\n    DEVNULL = None\n"
         "run(['cat'], stdin=subprocess.DEVNULL)\n", [("<module>", "subprocess.run", 4)]),
        ("from subprocess import run\ndef f():\n    class subprocess:\n        DEVNULL = None\n"
         "    run(['cat'], stdin=subprocess.DEVNULL)\n", [("f", "subprocess.run", 5)]),
        ("from subprocess import run\ndef f(subprocess):\n"
         "    run(['cat'], stdin=subprocess.DEVNULL)\n", [("f", "subprocess.run", 3)]),
    )

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

    def test_detector_allows_string_dunder_reads_and_initialiser_callees(self):
        # Ordinary code: a dunder that holds a string, and the initialisers
        # and object.__setattr__ as callees.
        cases = (
            "class Failure(Exception):\n    def __init__(self, msg):\n        super().__init__(msg)\n",
            "class C(B):\n    def __init__(self):\n        super(C, self).__init__()\n",
            "def kind(x):\n    return type(x).__name__\n",
            "class C:\n    def __repr__(self):\n        return self.__class__.__name__\n",
            "def label(fn):\n"
            "    return fn.__name__ + fn.__qualname__ + fn.__module__ + (fn.__doc__ or '')\n",
            "from pathlib import Path\nROOT = Path(__file__).resolve().parent\nUSAGE = __doc__\n",
            "import dataclasses\n@dataclasses.dataclass(frozen=True)\nclass D(B):\n    a: int\n"
            "    def __post_init__(self):\n        super().__post_init__()\n"
            "        object.__setattr__(self, 'a', abs(self.a))\n",
            "import subprocess\nNAME = subprocess.CalledProcessError.__name__\n",
            "name = getattr(fn, '__name__', None)\nsetattr(wrapper, '__doc__', fn.__doc__)\n",
            "match e:\n    case C(__name__=n):\n        pass\n",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), [])

    def test_detector_flags_reads_past_the_allowed_dunders(self):
        # The allowed dunders reach nothing further: any other dunder or
        # namespace read on their chain, a watched module's own dunder, or one
        # of the callees held as a value is still flagged.
        cases = (
            ("super().__init__.__globals__['Popen']\n", [("<module>", "super().__init__", 1)]),
            ("init = super().__init__\n", [("<module>", "super().__init__", 1)]),
            ("super().__getattribute__('x')\n", [("<module>", "super().__getattribute__", 1)]),
            ("x.__class__.__init__.__globals__\n", [("<module>", "x.__class__", 1)]),
            ("fn.__name__.__class__\n", [("<module>", "fn.__name__.__class__", 1)]),
            ("setter = object.__setattr__\n", [("<module>", "object.__setattr__", 1)]),
            ("object.__getattribute__(x, 'y')\n", [("<module>", "object.__getattribute__", 1)]),
            ("object.__setattr__(x, '__class__', int)\n", [("<module>", "__setattr__ __class__", 1)]),
            ("setattr(x, '__dict__', {})\n", [("<module>", "setattr __dict__", 1)]),
            ("import os\nos.__name__\n", [("<module>", "os.__name__", 2)]),
            ("import os\nos.__class__.__name__\n", [("<module>", "os.__class__", 2)]),
            ("import subprocess\nsubprocess.CalledProcessError.__init__.__name__\n",
             [("<module>", "subprocess.CalledProcessError.__init__", 2)]),
            ("__qualname__\n", [("<module>", "__qualname__", 1)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_allows_protocol_dunder_callees(self):
        # A DUNDER_CALLEES method called off any base but a watched module,
        # and __class__ as super's first argument, are ordinary code.
        cases = (
            "class Failure(Exception):\n    def __init__(self, msg):\n"
            "        Exception.__init__(self, msg)\n",
            "class D(B):\n    def __post_init__(self):\n        B.__post_init__(self)\n",
            "class C:\n    def __repr__(self):\n        return 'C' + super().__repr__()\n",
            "class C:\n    def __setattr__(self, k, v):\n        super().__setattr__(k, v)\n"
            "    def __delattr__(self, k):\n        super().__delattr__(k)\n",
            "class C(int):\n    def __new__(cls, v):\n        return super().__new__(cls, v)\n",
            "class C:\n    def __eq__(self, o):\n        return super().__eq__(o)\n"
            "    def __hash__(self):\n        return super().__hash__()\n",
            "class C:\n    def __init_subclass__(cls, **kw):\n        super().__init_subclass__(**kw)\n",
            "class C(B):\n    def __init__(self):\n        super(__class__, self).__init__()\n",
            "super.__init__(x)\nother.__setattr__(x, 'a', 1)\nobject.__delattr__(x, 'a')\n",
            "super().__setattr__('scope', 'locals')\nobject.__setattr__(x, 'scope', 'locals')\n"
            "object.__delattr__('globals', 'scope')\n",
            "super().__setattr__(name='scope', value='locals')\n"
            "super().__setattr__('scope', value='locals')\nC.__setattr__(o, 'scope', value='locals')\n"
            "super().__delattr__(name='scope')\n",
            "class F:\n    def __set_name__(self, owner, name):\n"
            "        super().__set_name__(owner, name)\n"
            "    def __del__(self):\n        super().__del__()\n",
            "y = a.__add__(b), a.__radd__(b), a.__iadd__(b), a.__neg__(), a.__ror__(b), a.__lt__(b)\n",
            "text = x.__str__() + x.__format__('') + fn.__name__.__eq__('f').__repr__()\n",
            "import subprocess\nclass E(subprocess.CalledProcessError):\n    def __init__(self):\n"
            "        subprocess.CalledProcessError.__init__(self, 1, 'git')\n",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), [])

    def test_detector_flags_dunder_callees_off_the_list(self):
        # A dunder off DUNDER_CALLEES, a listed one held as a value, one read
        # off a watched module or after a flagged read, and __class__ other
        # than as super's first argument are still flagged, as is a
        # __setattr__ or __delattr__ given a keyword it does not list or a
        # name= that is not a plain string literal.
        cases = (
            ("super().__getattr__('x')\n", [("<module>", "super().__getattr__", 1)]),
            ("x.__reduce__()\nx.__reduce_ex__(2)\nx.__getstate__()\nx.__dir__()\n",
             [("<module>", "x.__reduce__", 1), ("<module>", "x.__reduce_ex__", 2),
              ("<module>", "x.__getstate__", 3), ("<module>", "x.__dir__", 4)]),
            ("type(x).__subclasses__()\nf.__call__()\nd.__get__(o)\nC.__class_getitem__(int)\n",
             [("<module>", "type(x).__subclasses__", 1), ("<module>", "f.__call__", 2),
              ("<module>", "d.__get__", 3), ("<module>", "C.__class_getitem__", 4)]),
            ("x.__bool__()\nx.__enter__()\n",
             [("<module>", "x.__bool__", 1), ("<module>", "x.__enter__", 2)]),
            ("init = Exception.__init__\neq = super().__eq__\n",
             [("<module>", "Exception.__init__", 1), ("<module>", "super().__eq__", 2)]),
            ("Exception.__init__.__globals__\n", [("<module>", "Exception.__init__", 1)]),
            ("import os\nos.__init__()\nos.path.__eq__(x)\n",
             [("<module>", "os.__init__", 2), ("<module>", "os.path.__eq__", 3)]),
            ("x.__dict__.__eq__(y)\nvars().__eq__(y)\n",
             [("<module>", "x.__dict__", 1), ("<module>", "vars", 2)]),
            ("super().__setattr__('__dict__', v)\nsuper().__delattr__('__class__')\n"
             "delattr(x, 'f_globals')\n",
             [("<module>", "__setattr__ __dict__", 1), ("<module>", "__delattr__ __class__", 2),
              ("<module>", "delattr f_globals", 3)]),
            ("x.__setattr__('locals')\nx.__setattr__(o, 'locals', v)\n"
             "x.__setattr__('a', 'locals', v, w)\nx.__delattr__(o, 'locals')\n"
             "x.__delattr__('a', 'locals', v)\n",
             [("<module>", "__setattr__ locals", 1), ("<module>", "__setattr__ locals", 2),
              ("<module>", "__setattr__ locals", 3), ("<module>", "__delattr__ locals", 4),
              ("<module>", "__delattr__ locals", 5)]),
            ("super().__setattr__(name='__dict__', value={})\nsuper().__delattr__(name='__class__')\n"
             "C.__setattr__(o, name='globals', value=v)\nC.__delattr__(o, name='f_locals')\n"
             "C.__setattr__(o, 'locals', value=v)\nx.__setattr__(value=v, name='__dict__')\n",
             [("<module>", "__setattr__ __dict__", 1), ("<module>", "__delattr__ __class__", 2),
              ("<module>", "__setattr__ globals", 3), ("<module>", "__delattr__ f_locals", 4),
              ("<module>", "__setattr__ locals", 5), ("<module>", "__setattr__ __dict__", 6)]),
            ("C.__setattr__(o, '__dict__', val={})\nsuper().__setattr__(key='__dict__', value={})\n"
             "super().__delattr__(attr='__class__')\nx.__delattr__(name='a', value=v)\n"
             "super().__setattr__(name=f'__dict__', value={})\nC.__delattr__(o, name=n)\n"
             "x.__setattr__(name=b'__dict__', value=v)\n",
             [("<module>", "__setattr__ val=", 1), ("<module>", "__setattr__ key=", 2),
              ("<module>", "__delattr__ attr=", 3), ("<module>", "__delattr__ value=", 4),
              ("<module>", "__setattr__ name=", 5), ("<module>", "__delattr__ name=", 6),
              ("<module>", "__setattr__ name=", 7)]),
            ("print(__class__)\nsuper(__class__)\nsuper(C, __class__)\n",
             [("<module>", "__class__", 1), ("<module>", "__class__", 2), ("<module>", "__class__", 3)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_keeps_flagging_reads_that_need_an_exemption(self):
        # Ordinary reads of a namespace or of a non-string dunder stay flagged
        # on purpose: each can reach a module, so each needs a reasoned
        # exemption.
        cases = (
            ("self.__dict__\n", [("<module>", "self.__dict__", 1)]),
            ("vars(args)\n", [("<module>", "vars", 1)]),
            ("Color.__members__\n", [("<module>", "Color.__members__", 1)]),
            ("e.__cause__\n", [("<module>", "e.__cause__", 1)]),
            ("f.__annotations__\n", [("<module>", "f.__annotations__", 1)]),
            ("C.__slots__\n", [("<module>", "C.__slots__", 1)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_flags_an_accessor_given_a_starred_argument(self):
        # A starred or double-starred argument hides the attribute name.
        cases = (
            ("getattr(x, *['__init__'])\n", [("<module>", "getattr *", 1)]),
            ("setattr(*args)\n", [("<module>", "setattr *", 1)]),
            ("delattr(x, **kw)\n", [("<module>", "delattr **", 1)]),
            ("object.__setattr__(*a)\n", [("<module>", "__setattr__ *", 1)]),
            ("import builtins\nbuiltins.getattr(*pair)\n", [("<module>", "getattr *", 2)]),
            ("getattr(x, 'a')\nsetattr(x, 'a', 1)\ndelattr(x, 'a')\n", []),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_flags_a_store_or_delete_on_a_watched_module(self):
        # A write to a watched module's attribute changes every later launch
        # that reads it; a write through any other watched chain is checked as
        # a read of its base; an annotation with no value stores nothing and
        # is checked as a read.
        cases = (
            ("import subprocess\nsubprocess.DEVNULL = None\n",
             [("<module>", "store subprocess.DEVNULL", 2)]),
            ("import subprocess\ndef f():\n    subprocess.PIPE = 0\n",
             [("f", "store subprocess.PIPE", 3)]),
            ("import subprocess\ndel subprocess.DEVNULL\n",
             [("<module>", "delete subprocess.DEVNULL", 2)]),
            ("import subprocess\nsubprocess.PIPE += 1\n", [("<module>", "store subprocess.PIPE", 2)]),
            ("import subprocess as sp\nsp.run, x = None, 1\n", [("<module>", "store subprocess.run", 2)]),
            ("from os import path\npath.sep = '/'\n", [("<module>", "store os.path.sep", 2)]),
            ("import os\nos.path.join = j\n", [("<module>", "store os.path.join", 2)]),
            ("import sys\nsys.stdin = None\n", [("<module>", "store sys.stdin", 2)]),
            ("import subprocess\nsubprocess.Popen.x = 1\n", [("<module>", "subprocess.Popen", 2)]),
            ("import subprocess\nsubprocess.DEVNULL: int\nsubprocess.PIPE: int = 0\n",
             [("<module>", "store subprocess.PIPE", 3)]),
            ("import os\nos.system: int\n", [("<module>", "os.system", 2)]),
            ("import os\nos.environ['A'] = 'b'\nself.DEVNULL = None\ndel x.PIPE\n", []),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_flags_a_class_pattern_keyword_that_reads_a_dunder(self):
        # A class pattern keyword reads that attribute off the subject.
        cases = (
            ("match e:\n    case C(__init__=init):\n        init.__globals__\n",
             [("<module>", "match __init__", 2), ("<module>", "init.__globals__", 3)]),
            ("match e:\n    case C(f_globals=g, __name__=n):\n        pass\n",
             [("<module>", "match f_globals", 2)]),
            ("def f(e):\n    match e:\n        case C(__class__=k):\n            pass\n",
             [("f", "match __class__", 3)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

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
            ("setattr __dict__", opaque),
            ("getattr *", opaque),
            ("__setattr__ val=", opaque),
            ("match __init__", opaque),
            ("store subprocess.DEVNULL", "store of a watched module's attribute, which changes every "
                                         "later read of it (remove it or add a reasoned exemption)"),
            ("delete os.path.sep", "delete of a watched module's attribute, which changes every "
                                   "later read of it (remove it or add a reasoned exemption)"),
            ("symtable", "scopes the check cannot resolve: symtable cannot be built for the module "
                         "(fix its source)"),
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

    def test_detector_resolves_global_nonlocal_and_class_body_scopes(self):
        # A nonlocal import binds in the enclosing function that binds the
        # name, not at module level; a global name resolves as at module
        # level; a lambda or comprehension in a class body does not see the
        # class's imports, but a comprehension's first iterable does, and
        # sees the module's too.
        cases = (
            ("def f():\n    p = None\n    def g():\n        nonlocal p\n        import os as p\n"
             "    return p.path.join('a')\np.system('cat')\n", []),
            ("def f():\n    p = None\n    def g():\n        def h():\n            nonlocal p\n"
             "            import os as p\n        p.system('cat')\n    p.system('cat')\n",
             [("f.g", "os.system", 7), ("f", "os.system", 8)]),
            ("import os as p\ndef f():\n    from subprocess import PIPE as p\n    def g():\n"
             "        global p\n        p.system('cat')\n", [("f.g", "os.system", 6)]),
            ("def f():\n    import os as p\n    def g():\n        global p\n"
             "        return p.system\n", []),
            ("import os as p\ndef f():\n    from subprocess import PIPE as p\n    class C:\n"
             "        global p\n        p.system('cat')\n"
             "        def m(self):\n            p.system('cat')\n",
             [("f.C", "os.system", 6)]),
            ("import os as p\nclass C:\n    from subprocess import PIPE as p\n"
             "    run = lambda: p.system('cat')\n    names = [p.system(x) for x in p.split()]\n",
             [("C", "os.system", 4), ("C", "os.system", 5), ("C", "os.split", 5)]),
            ("class C:\n    import os as p\n    run = lambda: p.system('cat')\n"
             "    names = {x: p.system(x) for x in ()}\n", []),
            ("class C:\n    import os as p\n    names = [x for x in p.system('cat')]\n"
             "    run = lambda x=p.system('cat'): x\n",
             [("C", "os.system", 3), ("C", "os.system", 4)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_reads_a_class_body_name_from_the_class_and_the_module(self):
        # A class body reads a name it binds itself from its own namespace
        # and, until it binds it there, from the module's globals, so a read
        # before a def, an assignment, an annotation or a loop that binds the
        # name in the class reads the module's import. A stdin value the
        # class binds other than by a watched import is not credited through
        # the module's import, nor, at the stated cost, read before that
        # binding; one the class does not bind, or binds only by an import
        # the module lacks, is credited, and a global declaration binds the
        # name in the module only where some scope assigns or imports it
        # under that declaration. A class attribute assigned from a
        # watched path holds it, read off any base, but is never credited as
        # an isolating stdin value.
        cases = (
            ("from subprocess import DEVNULL, run\nclass C:\n    DEVNULL = None\n"
             "    run(['cat'], stdin=DEVNULL)\n", [("C", "subprocess.run", 4)]),
            ("from subprocess import PIPE, run\nclass C:\n    PIPE: int = None\n"
             "    run(['cat'], stdin=PIPE)\n", [("C", "subprocess.run", 4)]),
            ("from subprocess import PIPE, run\nclass C:\n    for PIPE in (None,):\n        pass\n"
             "    run(['cat'], stdin=PIPE)\n", [("C", "subprocess.run", 5)]),
            ("from subprocess import DEVNULL, run\nclass C:\n    (DEVNULL := None)\n"
             "    run(['cat'], stdin=DEVNULL)\n", [("C", "subprocess.run", 4)]),
            ("from contextlib import nullcontext\nfrom subprocess import DEVNULL, run\nclass C:\n"
             "    with nullcontext() as DEVNULL:\n        pass\n    run(['cat'], stdin=DEVNULL)\n",
             [("C", "subprocess.run", 6)]),
            ("from subprocess import DEVNULL, run\nclass C:\n    DEVNULL = None\n"
             "    type T = run(['cat'], stdin=DEVNULL)\n", [("C", "subprocess.run", 4)]),
            ("from subprocess import DEVNULL, run\nclass C:\n    run(['cat'], stdin=DEVNULL)\n"
             "    DEVNULL = None\n", [("C", "subprocess.run", 3)]),
            ("from subprocess import DEVNULL, run\nclass C:\n    run(['cat'], stdin=DEVNULL)\n"
             "    type T = run(['cat'], stdin=DEVNULL)\n", []),
            ("from subprocess import run\nclass C:\n    from subprocess import DEVNULL\n"
             "    run(['cat'], stdin=DEVNULL)\n    type T = run(['cat'], stdin=DEVNULL)\n", []),
            ("from subprocess import DEVNULL, run\nclass C:\n    from subprocess import DEVNULL\n"
             "    run(['cat'], stdin=DEVNULL)\n", []),
            ("from subprocess import run\nclass C:\n    from subprocess import DEVNULL\n"
             "    run(['cat'], stdin=DEVNULL)\ndef g():\n    global DEVNULL\n", []),
            ("from subprocess import run\nclass C:\n    from subprocess import DEVNULL\n"
             "    run(['cat'], stdin=DEVNULL)\ndef g():\n    global DEVNULL\n    DEVNULL = None\n",
             [("C", "subprocess.run", 4)]),
            ("from subprocess import run\nclass C:\n    from subprocess import DEVNULL\n"
             "    run(['cat'], stdin=DEVNULL)\ndef g():\n    global DEVNULL\n"
             "    from subprocess import PIPE as DEVNULL\n", []),
            ("import os as p\nclass Task:\n    out = p.system('cat')\n    def p(self):\n        pass\n",
             [("Task", "os.system", 3)]),
            ("import os as p\nclass Task:\n    out = p.system('cat')\n    p = 1\n",
             [("Task", "os.system", 3)]),
            ("import os as p\nclass Task:\n    out = p.system('cat')\n    p: int\n",
             [("Task", "os.system", 3)]),
            ("import os as p\nclass Task:\n    out = p.system('cat')\n    for p in ():\n        pass\n",
             [("Task", "os.system", 3)]),
            ("from os import path as p\nclass Task:\n    out = p.system('cat')\n"
             "    def p(self):\n        pass\n", [("Task", "os.path.system", 3)]),
            ("import os as p\nclass Task:\n    type T = p.system('cat')\n    p = 1\n",
             [("Task", "os.system", 3)]),
            ("from subprocess import run as _run\nclass C:\n    _run = _run\n    def m(self):\n"
             "        self._run(['cat'])\n", [("C", "subprocess.run", 3), ("C.m", "subprocess.run", 5)]),
            ("import os as _os\nclass _Store:\n    _os = _os\n    def sync(self):\n"
             "        self._os.system('true')\n", [("_Store", "os", 3), ("_Store.sync", "os.system", 5)]),
            ("from subprocess import run as _run\nimport subprocess\nclass C:\n    _run = _run\n"
             "    def m(self):\n        self._run(['git'], stdin=subprocess.DEVNULL)\n",
             [("C", "subprocess.run", 4)]),
            ("import os\nclass C:\n    join = os.path.join\n    def m(self):\n"
             "        return self.join('a'), C.join.__name__\n", []),
            ("import subprocess\nclass C:\n    null = subprocess.DEVNULL\n    def m(self):\n"
             "        subprocess.run(['git'], stdin=self.null)\n", [("C.m", "subprocess.run", 5)]),
            ("import os as p\nclass Task:\n    p = 1\n    def m(self):\n        p.system('cat')\n",
             [("Task.m", "os.system", 5)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_credits_subprocess_unimported_only_where_no_scope_binds_it(self):
        # subprocess with no import of it is taken for the module, as a stdin
        # value, only when no scope the read may resolve through binds the
        # name: an assignment, a class, a def, a parameter, an import of
        # another name, or an assignment under a global or nonlocal
        # declaration makes the value credit nothing. An isolating import in
        # the scope that binds the name, whichever scope that is, is credited.
        flagged = self.SUBPROCESS_REBOUND + (
            ("from subprocess import run\nsubprocess = None\n"
             "run(['cat'], stdin=subprocess.DEVNULL)\n", [("<module>", "subprocess.run", 3)]),
            ("from subprocess import run\ndef subprocess():\n    pass\n"
             "run(['cat'], stdin=subprocess.DEVNULL)\n", [("<module>", "subprocess.run", 4)]),
            ("from subprocess import run\nimport json as subprocess\n"
             "run(['cat'], stdin=subprocess.DEVNULL)\n", [("<module>", "subprocess.run", 3)]),
            ("from subprocess import run\nclass C:\n    subprocess = None\n"
             "    run(['cat'], stdin=subprocess.PIPE)\n", [("C", "subprocess.run", 4)]),
            ("from subprocess import run\nclass C:\n    def m(self, subprocess=None):\n"
             "        run(['cat'], stdin=subprocess.DEVNULL)\n", [("C.m", "subprocess.run", 4)]),
            ("from subprocess import run\n"
             "null = lambda subprocess: run(['cat'], stdin=subprocess.DEVNULL)\n",
             [("<module>", "subprocess.run", 2)]),
            ("from subprocess import run\ndef g():\n    global subprocess\n    subprocess = None\n"
             "run(['cat'], stdin=subprocess.DEVNULL)\n", [("<module>", "subprocess.run", 5)]),
            ("from subprocess import run\ndef f():\n    subprocess = None\n    def g():\n"
             "        nonlocal subprocess\n        run(['cat'], stdin=subprocess.DEVNULL)\n",
             [("f.g", "subprocess.run", 6)]),
            ("import subprocess\ndef f(subprocess):\n"
             "    subprocess.run(['cat'], stdin=subprocess.DEVNULL)\n", [("f", "subprocess.run", 3)]),
        )
        credited = (
            ("import subprocess\nsubprocess.run(['cat'], stdin=subprocess.DEVNULL)\n", []),
            ("def f():\n    import subprocess\n"
             "    subprocess.run(['cat'], stdin=subprocess.DEVNULL)\n", []),
            ("class C:\n    import subprocess\n"
             "    subprocess.run(['cat'], stdin=subprocess.DEVNULL)\n", []),
            ("def f():\n    import subprocess\n    def g():\n"
             "        subprocess.run(['cat'], stdin=subprocess.PIPE)\n", []),
            ("def f():\n    global subprocess\n    import subprocess\n"
             "subprocess.run(['cat'], stdin=subprocess.DEVNULL)\n", []),
            ("def f():\n    subprocess = None\n    def g():\n        nonlocal subprocess\n"
             "        import subprocess\n"
             "    subprocess.run(['cat'], stdin=subprocess.DEVNULL)\n", []),
            ("def f():\n    from subprocess import PIPE, run\n    run(['cat'], stdin=PIPE)\n", []),
            ("from subprocess import run\ndef f():\n"
             "    run(['cat'], stdin=subprocess.DEVNULL)\n", []),
            ("from subprocess import run\ndef g():\n    global subprocess\n"
             "run(['cat'], stdin=subprocess.DEVNULL)\n", []),
        )
        for source, expected in flagged + credited:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_module_fallback_before_the_binding_check_credits_a_rebound_subprocess(self):
        # With every scope reported as not binding the name, the fallback to
        # the module applies before the every-scope check, as it once did,
        # and each rebinding the real check flags is credited.
        this = sys.modules[__name__]
        real = binding_homes

        def unbound(source: str, tree: ast.Module, names: set[str]):
            homes = real(source, tree, names)
            return None if homes is None else {key: [(home, name, False) for home, name, _ in found]
                                               for key, found in homes.items()}

        for source, expected in self.SUBPROCESS_REBOUND:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)
                with mock.patch.object(this, "binding_homes", unbound):
                    self.assertEqual(stdin_inheriting_launches(source), [])
        with mock.patch.object(this, "binding_homes", unbound):
            self.assertEqual(stdin_inheriting_launches("from subprocess import run\nrun(['cat'])\n"),
                             [("<module>", "subprocess.run", 2)])

    def test_detector_binds_a_nonlocal_import_where_a_walrus_binds_the_name(self):
        # A walrus in a comprehension, a generator expression, or the
        # defaults, decorators or bases of a nested definition binds in the
        # function around them, so a nonlocal import of that name binds there.
        tail = "    def g():\n        nonlocal p\n        import os as p\n    p.system('cat')\n"
        cases = (
            ("def f():\n    [(p := x) for x in ()]\n" + tail, [("f", "os.system", 6)]),
            ("def f():\n    any((p := x) for x in ())\n" + tail, [("f", "os.system", 6)]),
            ("def f():\n    def h(d=(p := None)):\n        pass\n" + tail, [("f", "os.system", 7)]),
            ("def f():\n    @deco(p := None)\n    def h():\n        pass\n" + tail,
             [("f", "os.system", 8)]),
            ("def f():\n    class K((p := object)):\n        pass\n" + tail, [("f", "os.system", 7)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_resolves_names_by_their_symtable_scope(self):
        # A parameter or a comprehension variable shadows an import around
        # it; an annotation is read in the scope it is written in, a class
        # body's included (where a name the class binds also reads the
        # module's), even under the annotations future import; a type alias
        # in a class body reads the class's names; a private name is
        # mangled; a match pattern is read in the match statement's scope.
        cases = (
            ("def f():\n    import os as p\n    def g(p):\n        return p.system\n", []),
            ("import os as p\ndef f(items):\n"
             "    return [p.system for p in items], {p: 1 for p in items}\n", []),
            ("class C:\n    import os as p\n    def m(self, x: p.system('cat')):\n        pass\n",
             [("C.m", "os.system", 3)]),
            ("import os as p\nclass C:\n    from subprocess import PIPE as p\n"
             "    def m(self, x: p.system('cat')) -> p.split:\n        y: p.system('cat') = 1\n",
             [("C.m", "os.system", 4), ("C.m", "os.system", 5), ("C.m", "os.split", 4)]),
            ("from __future__ import annotations\nimport subprocess\n"
             "def f() -> subprocess.os.system('cat'):\n    y: subprocess.os = 1\n",
             [("f", "subprocess.os", 4), ("f", "subprocess.os", 3)]),
            ("def f():\n    import os as p\n    y: p.system('cat')\n", [("f", "os.system", 3)]),
            ("class C:\n    import os as p\n    type T = p.system('cat')\n", [("C", "os.system", 3)]),
            ("class C:\n    import os as __p\n    __p.system('cat')\n", [("C", "os.system", 3)]),
            ("def f(x):\n    import os as p\n    match x:\n        case p.system():\n            pass\n",
             [("f", "os.system", 4)]),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), expected)

    def test_detector_flags_the_module_when_symtable_fails(self):
        # Source that parses but has no symbol table cannot be resolved, so
        # the module is flagged rather than passed.
        for source in ("def f():\n    nonlocal x\n", "nonlocal x\n", "def f(a):\n    global a\n"):
            with self.subTest(source=source):
                self.assertEqual(stdin_inheriting_launches(source), [("<module>", "symtable", 1)])

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
        # stdin: red with the real exemptions alone, green with one more that
        # names it.
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
            for exempt in (STDIN_EXEMPT_CALLS, STDIN_EXEMPT_CALLS + exemption):
                result = unittest.TestResult()
                with (mock.patch.object(StdinIsolationTests, "MODULE", module),
                      mock.patch.object(this, "STDIN_EXEMPT_CALLS", exempt)):
                    unittest.TestSuite(StdinIsolationTests(name) for name in names).run(result)
                outcomes.append((result.testsRun, len(result.failures), len(result.errors)))
        self.assertEqual(outcomes, [(4, 3, 0), (4, 0, 0)])

    def test_a_real_exemption_leaves_the_stdin_checks_green(self):
        # One reasoned exemption in STDIN_EXEMPT_CALLS, for a launch added to
        # aiqt_corpus.py, leaves every other check of this class green: none
        # of them sets the exemptions aside for its own.
        this = sys.modules[__name__]
        names = [name for name in unittest.TestLoader().getTestCaseNames(StdinIsolationTests)
                 if name != "test_a_real_exemption_leaves_the_stdin_checks_green"]
        extra = "\n\ndef read_pushed_refs():\n    return subprocess.run(['git', 'status']).returncode\n"
        exemption = (("read_pushed_refs", "subprocess.run", "reads the pushed refs on purpose"),)
        outcomes = []
        with tempfile.TemporaryDirectory() as d:
            module = Path(d) / "aiqt_corpus.py"
            module.write_text(self.MODULE.read_text(encoding="utf-8") + extra, encoding="utf-8")
            for exempt in (STDIN_EXEMPT_CALLS, STDIN_EXEMPT_CALLS + exemption):
                result = unittest.TestResult()
                with (mock.patch.object(StdinIsolationTests, "MODULE", module),
                      mock.patch.object(this, "STDIN_EXEMPT_CALLS", exempt)):
                    unittest.TestSuite(StdinIsolationTests(name) for name in names).run(result)
                outcomes.append((result.testsRun, len(result.errors),
                                 sorted(test.id().rpartition(".")[2] for test, _ in result.failures)))
        self.assertEqual(outcomes, [
            (len(names), 0, ["test_detector_flags_the_module_with_one_keyword_dropped",
                             "test_detector_flags_the_module_with_one_value_inheriting",
                             "test_every_launch_isolates_stdin",
                             "test_one_valid_exemption_leaves_the_module_checks_green"]),
            (len(names), 0, []),
        ])


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
