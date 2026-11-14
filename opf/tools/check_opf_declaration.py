#!/usr/bin/env python3
"""OPF's own declaration gate (OPF-SPEC section 16.1), rooted at the OPF tree.

  check_opf_declaration.py                        check this OPF tree (exit 0 clean, 1 finding, 2 cannot evaluate)
  check_opf_declaration.py --self-test [--execution-report ABS_PATH]
  check_opf_declaration.py --execution-report ABS_PATH
                                                  the self-test (exit 0 pass, 1 fail, 2 cannot evaluate), the
                                                  last form as the parent's tools/check_selftest_execution.py
                                                  launches it while OPF is nested; any other argument list is
                                                  a usage error (exit 2)

Its root is the OPF tree, the directory above this file's tools/ directory (opf/ while OPF is nested in its parent
repository, the checkout root once it stands alone). Every path below is relative to that root, and the gate reads
nothing outside it: it imports only helpers beside this file (_opf_declaration, _opf_store and, for an
execution-reported self-test, _selftest_exit_report). It applies the section 16.1 rules that move with OPF:

  1. the declaration file tools/opf-declaration.toml is a regular file whose bytes are well-formed
     (_opf_declaration.parse_declaration: the four fields, typed, in the canonical form) and do not contradict the
     build (release RELEASE_IDENTITY and the supported spec_version, homes and worklog generations of _opf_store);
  2. its bytes equal SHIPPED_DECLARATION_BYTES and their SHA-256 digest equals SHIPPED_DECLARATION_SHA256, the
     shipped-file digest the implementation carries;
  3. RELEASE_IDENTITY equals OPF's own release version in VERSION, a regular file of at most MAX_OPF_VERSION_BYTES
     bytes read as exactly one nonempty line ended by a line feed with no other line ending (a larger file, a
     missing final line feed, a line feed before the final one or a carriage return anywhere is a cannot-evaluate;
     any other character is compared as part of the value);
  4. each documentation surface (DOC_SURFACES: spec/OPF-QUICKSTART.md and RELEASING.md) restates that
     declaration byte-equal to the file in its one restatement block: a restatement that gives another value, omits
     a field or states a field the file does not carry is malformed and a finding, and so is a missing one, a
     document with more than one block and a block whose opening line may lie inside an HTML comment;
  5. each surface states where the implementation carries the digest (DIGEST_LOCATION, both phrases);
  6. every Markdown or HTML document (a .md or .html file) of the OPF tree (a .git directory at its root aside, and
     no symlinked directory entered) with a line that opens a restatement block is a DOC_SURFACES entry;
  7. every DOC_SURFACES entry lies inside the OPF tree (in_tree: relative, and no component empty, "." or ".."),
     so the surfaces move with OPF; an entry outside it is a finding and is never read.

The restatement reader (the block forms, and the HTML comment rules for Markdown and HTML surfaces) is the one the
parent repository's tools/check_opf_declaration.py documents, carried here line for line. While OPF is nested,
that parent gate still checks the same rules over the whole repository and the parent's own release document, and
its self-test keeps its own registered checks; this gate is the OPF-local half that moves with the OPF tree.

An input that is not a regular file of at most MAX_READ_BYTES bytes (MAX_OPF_VERSION_BYTES for VERSION), cannot be
read or (but for the declaration file) is not UTF-8, a directory rule 6 cannot list, and a helper module that cannot
be imported are each a cannot-evaluate (exit 2), never a finding or a clean exit; so is any exception other than a
KeyboardInterrupt that leaves the gate body. Each such site catches with a bare except and takes the exception with
sys.exception(); a KeyboardInterrupt, told by its real type, propagates.

Its entry point opens with opf.py's canonical --self-test statement, which opf.py's aggregator requires first in
every opf/tools module that binds self_test, and then runs the child-status guard, an inline copy of
_child_status.py that the parent's tools/check_child_status_coverage.py pins to it (SELF_TEST_FIRST_COPIES), before
main(): where this process cannot read a child's exit status it writes a refusal naming this gate and exits 2. A
direct --self-test launch takes the first statement and so runs unguarded; the self-test starts no child process.

The self-test runs over scratch copies of this tree's inputs, each with one edit, and shows each rule red on its
edit and each revert red, a mutant of this file written to a scratch directory and loaded with importlib. Its check
ids are registered in the parent's tools/selftest_checks.toml (suite opf-declaration-local-selftest) and reconciled
by tools/check_selftest_execution.py while OPF is nested; in a standalone OPF tree the plain --self-test runs them
all with no registry to reconcile against.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_declaration.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import importlib.util
import io
import os
import re
import shutil
import stat
import tempfile
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# what the import fallback below records for an exception it cannot bind: one whose MRO leaves out BaseException,
# which no except BaseException clause matches and a bare except takes only through a call (sys.exception()),
# which tools/check_python_floor.py admits in no import fallback
_UNBOUND_HELPER_ERROR = ImportError("the execution-report helper's import raised an exception whose MRO leaves "
                                    "out BaseException, so it is not bound")
try:
    import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)
except BaseException as exc:   # the helper is missing, unreadable or fails to import: a reported run exits 2
    _selftest_exit_report = None
    _HELPER_ERROR = exc
except:   # the same, by an exception whose MRO leaves out BaseException
    _selftest_exit_report = None
    _HELPER_ERROR = _UNBOUND_HELPER_ERROR
else:
    _HELPER_ERROR = None

ROOT = Path(__file__).resolve().parents[1]   # the OPF tree: opf/ while nested, the checkout root standalone
# The helpers beside this file are imported by _load(), after the self-test arms its execution report: the
# report's finalizer must be the process's first atexit registration, and importing them may register others.
_opf_declaration = None
_opf_store = None
# The gate's own record that the self-test armed its execution report: set once _selftest_exit_report.arm()
# returns, which it does only having armed it. main() exits through exit_with only then.
_REPORT_ARMED = False

SUITE_ID = "opf-declaration-local-selftest"
DECLARATION_REL = "tools/opf-declaration.toml"
MODULE_REL = "tools/_opf_declaration.py"
VERSION_REL = "VERSION"   # OPF's own release version
DOC_SURFACES = ("spec/OPF-QUICKSTART.md", "RELEASING.md")
DIGEST_LOCATION = (MODULE_REL, "SHIPPED_DECLARATION_SHA256")
_HTML_OPEN = '<div class="tree"><pre id="opf-declaration">'
_HTML_CLOSE = "</pre></div>"
_MD_OPEN = "```toml opf-declaration"
_MD_CLOSE = "```"
_COMMENT_OPEN = "<!--"
_COMMENT_CLOSE = "-->"
_CODE_SPAN = re.compile(r"(`+)(?!`).*?(?<!`)\1(?!`)")
_MD_LINE_END = re.compile(r"\r\n|\r|\n")
# a block quote marker or a list item marker, after any indentation
_MD_CONTAINER = re.compile(r"[ \t]*(?:>|[-+*](?=[ \t]|\Z)|[0-9]{1,9}[.)](?=[ \t]|\Z))")
_MD_FENCE = re.compile(r"(`{3,})[^`]*\Z|(~{3,})")
_MD_FENCE_CLOSE = re.compile(r" {0,3}(`{3,}|~{3,})[ \t]*\Z")
# the start and end conditions of the CommonMark HTML blocks of types 1 to 5; any other line that begins with
# `<` may open a block of type 6 or 7, which a blank line ends
_MD_HTML_BLOCKS = (
    (re.compile(r"<(?:pre|script|style|textarea)(?:[ \t>]|\Z)", re.IGNORECASE),
     re.compile(r"</(?:pre|script|style|textarea)>", re.IGNORECASE)),
    (re.compile(re.escape(_COMMENT_OPEN)), re.compile(re.escape(_COMMENT_CLOSE))),
    (re.compile(r"<\?"), re.compile(r"\?>")),
    (re.compile(r"<![A-Za-z]"), re.compile(r">")),
    (re.compile(r"<!\[CDATA\["), re.compile(r"\]\]>")),
)
DOC_SUFFIXES = (".md", ".html")
MAX_READ_BYTES = 1 << 20
# The most bytes VERSION may hold: _opf_declaration.MAX_OPF_VERSION_BYTES, the one cap every reader of that file
# applies; the self-test holds the two equal.
MAX_OPF_VERSION_BYTES = 64


class CannotEvaluate(Exception):
    """An input the gate needs cannot be read."""


def _cannot_evaluate(what, exc, self_test=False):
    """Print the fixed cannot-evaluate line for `what`, then describe the exception `exc` that caused it, and
    return 2: `exc` may come from a helper, whose own __str__ (or type name) can raise any exception, whatever
    its MRO, a SystemExit included, so the description falls back to the type name, or to none. Each step catches
    with a bare except (so an exception whose MRO leaves out BaseException is caught too) and takes what it caught
    with sys.exception(). A KeyboardInterrupt raised while a line is printed or `exc` is described (a Ctrl-C, or a
    __str__ that raises one) propagates instead; each step tells one by its real type and never reads the raised
    exception's __class__, so an exception whose __class__ raises or claims KeyboardInterrupt is no
    KeyboardInterrupt there."""
    stream = sys.stdout if self_test else sys.stderr
    try:
        print(("SELF-TEST CANNOT EVALUATE: opf-declaration-local: " if self_test else
               "opf-declaration-local: CANNOT EVALUATE: ") + what, file=stream)
    except:   # the exit status still carries the cannot-evaluate
        error = sys.exception()
        if issubclass(type(error), KeyboardInterrupt):
            raise
    try:
        cause = "{}: {}".format(type(exc).__name__, exc)
    except:
        error = sys.exception()
        if issubclass(type(error), KeyboardInterrupt):
            raise
        try:
            cause = "{} (its message cannot be read)".format(type(exc).__name__)
        except:
            error = sys.exception()
            if issubclass(type(error), KeyboardInterrupt):
                raise
            cause = None
    if cause is not None:
        try:
            print("  cause: " + cause, file=stream)
        except:
            error = sys.exception()
            if issubclass(type(error), KeyboardInterrupt):
                raise
    return 2


def _read_regular(path, rel, limit=MAX_READ_BYTES):
    """The bytes of a regular, non-symlink file of at most `limit` bytes (MAX_READ_BYTES unless given)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise CannotEvaluate("{} cannot be opened ({})".format(rel, exc.strerror or exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise CannotEvaluate("{} is not a regular file of at most {} bytes".format(rel, limit))
        chunks = []
        total = 0
        while total <= limit:
            chunk = os.read(fd, limit + 1 - total)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            total += len(chunk)
        raise CannotEvaluate("{} grew past {} bytes while read".format(rel, limit))
    finally:
        os.close(fd)


def _scan_markers(text, is_open):
    """Whether a comment is open after `text`, from `is_open`: a `<!--` opens one and the next `-->` closes it."""
    at = 0
    while True:
        if is_open:
            at = text.find(_COMMENT_CLOSE, at)
            if at < 0:
                return True
            at += len(_COMMENT_CLOSE)
            is_open = False
        else:
            at = text.find(_COMMENT_OPEN, at)
            if at < 0:
                return False
            at += 2   # `<!-->` and `<!--->` close the comment they open
            is_open = True


def _md_fence_opener(line, in_html, unsure):
    """The marker run of a Markdown line that surely opens a fenced code block, or None: three or more backticks
    (with no backtick after them) or tildes at the left margin, outside any HTML block, and before any
    fence-like line the reader could not place."""
    found = None if in_html or unsure else _MD_FENCE.match(line)
    return found and (found.group(1) or found.group(2))


def _md_fence_closes(line, run):
    """Whether a Markdown line closes the fenced code block `run` opened: at most three spaces of indentation, a
    run of the same character at least as long, then only spaces or tabs."""
    found = _MD_FENCE_CLOSE.match(line)
    return bool(found) and found.group(1)[0] == run[0] and len(found.group(1)) >= len(run)


def _md_html_end(body):
    """The end condition of the HTML block a Markdown line beginning `body` may open: a pattern its last line
    contains, or None for a blank line."""
    return next((end for start, end in _MD_HTML_BLOCKS if start.match(body)), None)


def _md_comment_block(line, unsure):
    """Whether a Markdown line surely opens an HTML comment block, whose lines up to the first `-->` are raw
    HTML: a `<!--` at the left margin, before any fence-like line the reader could not place."""
    return line.startswith(_COMMENT_OPEN) and not unsure


def _md_text(line, in_html):
    """The text of a Markdown line in which comment markers count: outside an HTML block, without what the
    reader takes for its code spans (_CODE_SPAN, read lexically as the module docstring states)."""
    return line if in_html else _CODE_SPAN.sub("", line)


def _markdown_in_comment(text):
    """Whether Markdown `text` may leave an HTML comment open at its end (the rules are in the module
    docstring)."""
    is_open = False
    run = None       # the marker run of the fenced code block the line is in
    unsure = False   # a fence-like line the reader could not place has been seen
    block = None     # (surely a comment block, its pending end conditions) of the HTML block the line may be in
    for line in _MD_LINE_END.split(text):
        if run is not None:
            if _md_fence_closes(line, run):
                run = None
            continue
        body = line
        container = _MD_CONTAINER.match(body)
        while container:
            body = body[container.end():]
            container = _MD_CONTAINER.match(body)
        body = body.lstrip(" \t")
        run = _md_fence_opener(line, block is not None, unsure)
        if run:
            block = None
            continue
        if body.startswith(("```", "~~~")) and (block is None or not block[0]):
            unsure = True
        if block is None and body.startswith("<"):
            block = (_md_comment_block(line, unsure), {_md_html_end(body)})
        elif block is None:
            is_open = _scan_markers(_md_text(line, False), is_open)
            continue
        elif not block[0] and body.startswith("<"):
            block[1].add(_md_html_end(body))   # the line may open a block of its own, which may end later
        if block[0]:
            is_open = _scan_markers(_md_text(line, True), is_open)
        else:
            # a `-->` closes only a `<!--` on its own line: the block may end before it, leaving it text
            is_open = is_open or _scan_markers(_md_text(line, True), False)
        pending = {end for end in block[1] if not (end.search(line) if end else line.strip(" \t") == "")}
        if pending:
            block = (block[0], pending)
            continue
        block = None
        if is_open:   # a raw `<!--` no raw `-->` closes: a browser hides the rest of the surface
            return True
    return is_open


def _in_comment(rel, lines):
    """Whether `lines` of the surface `rel`, the lines before its opening line, may leave an HTML comment open."""
    text = "\n".join(lines)
    return _scan_markers(text, False) if rel.endswith(".html") else _markdown_in_comment(text)


def restatement(rel, text):
    """The restatement block of one documentation surface as bytes, or None where it has none (or more than
    one, or one that is not closed at its own indentation, or one whose opening line is inside a comment)."""
    lines = text.split("\n")
    html = rel.endswith(".html")
    opens = [i for i, line in enumerate(lines) if (line.strip() == _HTML_OPEN if html else
                                                   line.strip() == _MD_OPEN)]
    if len(opens) != 1:
        return None
    start = opens[0]
    if _in_comment(rel, lines[:start]):
        return None
    indent = "" if html else lines[start][:len(lines[start]) - len(lines[start].lstrip(" "))]
    block = []
    for line in lines[start + 1:]:
        if line.strip() == (_HTML_CLOSE if html else _MD_CLOSE) and (html or line == indent + _MD_CLOSE):
            return "".join(entry + "\n" for entry in block).encode("utf-8")
        if not line.startswith(indent):
            return None
        block.append(line[len(indent):])
    return None


def _restatement_problem(rel, block, fields):
    """Why a surface's restatement differs from the file's declaration, or None where it does not."""
    if block is None:
        return "{} restates no declaration (one block opened by {!r}, outside any comment, is required)".format(
            rel, _HTML_OPEN if rel.endswith(".html") else _MD_OPEN)
    stated, problem = _opf_declaration.parse_declaration(block)
    if stated is None:
        try:
            stated = tomllib.loads(block.decode("utf-8"))
        except (RecursionError, MemoryError, ValueError):
            return "{} restates the declaration malformed: it {}".format(rel, problem)
    differences = ["omits {}".format(key) for key in fields if key not in stated]
    differences += ["states {}, a field the file does not carry".format(key) for key in stated
                    if key not in fields]
    differences += ["gives {} = {!r}, the file {!r}".format(key, stated[key], fields[key]) for key in fields
                    if key in stated and stated[key] != fields[key]]
    if differences:
        return "{} restates the declaration malformed: it {}".format(rel, "; ".join(differences))
    if block != _opf_declaration.declaration_bytes(**fields):
        return "{} restates the declaration malformed: it {}".format(rel, problem or "is not byte-equal to the file")
    return None


def read_opf_version(text):
    """OPF's own release version from the text of opf/VERSION: its one line, where the text is exactly one
    nonempty line ended by a line feed with no other line ending. Only the line feed and the carriage return
    are read as line endings: a text that does not end with a line feed, an empty line, a line feed anywhere
    before the final one or a carriage return anywhere raises CannotEvaluate; any other character (a BOM,
    surrounding whitespace, a NUL, a NEL or a line separator included) is kept and compared as part of the
    value."""
    line = text[:-1]
    if not text.endswith("\n") or not line or "\n" in line or "\r" in line:
        raise CannotEvaluate("{} is not exactly one nonempty line ended by a line feed".format(VERSION_REL))
    return line


def file_problems(data, version):
    """The findings of rules 1 to 3 over the declaration file's bytes as shipped and OPF's own release version
    (the one line of opf/VERSION, read_opf_version)."""
    problems = []
    fields, problem = _opf_declaration.parse_declaration(data)
    if problem is not None:
        problems.append("{} is malformed: the declaration {}".format(DECLARATION_REL, problem))
    else:
        contradiction = _opf_declaration._contradiction(fields)
        if contradiction:
            problems.append("{} is contradictory: {}".format(DECLARATION_REL, contradiction))
    digest = hashlib.sha256(data).hexdigest()
    if digest != _opf_declaration.SHIPPED_DECLARATION_SHA256:
        problems.append("{} has sha256 {}, but {} carries the shipped-file digest {}".format(
            DECLARATION_REL, digest, MODULE_REL, _opf_declaration.SHIPPED_DECLARATION_SHA256))
    if data != _opf_declaration.SHIPPED_DECLARATION_BYTES:
        problems.append("{} differs from SHIPPED_DECLARATION_BYTES in {}".format(DECLARATION_REL, MODULE_REL))
    if hashlib.sha256(_opf_declaration.SHIPPED_DECLARATION_BYTES).hexdigest() != \
            _opf_declaration.SHIPPED_DECLARATION_SHA256:
        problems.append("{}: SHIPPED_DECLARATION_SHA256 is not the digest of SHIPPED_DECLARATION_BYTES".format(
            MODULE_REL))
    if version != _opf_declaration.RELEASE_IDENTITY:
        problems.append("{}: RELEASE_IDENTITY {!r} is not OPF's own release version {!r} in {}".format(
            MODULE_REL, _opf_declaration.RELEASE_IDENTITY, version, VERSION_REL))
    return problems


def doc_problems(data, surfaces):
    """The findings of rules 4 and 5 over each surface (rel -> text), against the file's bytes as shipped."""
    fields, problem = _opf_declaration.parse_declaration(data)
    if fields is None:
        return ["the documentation cannot be compared with a malformed {}".format(DECLARATION_REL)]
    problems = []
    for rel, text in surfaces.items():
        found = _restatement_problem(rel, restatement(rel, text), fields)
        if found is not None:
            problems.append(found)
        missing = [phrase for phrase in DIGEST_LOCATION if phrase not in text]
        if missing:
            problems.append("{} does not state where the shipped-file digest is carried (missing {})".format(
                rel, ", ".join(missing)))
    return problems


def _walk_error(exc):
    raise CannotEvaluate("{} cannot be listed ({})".format(exc.filename, exc.strerror or exc))


def restating_documents(root):
    """The repo-relative paths of the Markdown and HTML documents under root (the .git directory aside) with a
    line that opens a restatement block, of either form; each document is read as a surface is."""
    found = set()
    for directory, dirs, files in os.walk(root, onerror=_walk_error):
        dirs[:] = sorted(name for name in dirs if not (Path(directory) == Path(root) and name == ".git"))
        for name in sorted(files):
            if not name.endswith(DOC_SUFFIXES):
                continue
            rel = (Path(directory) / name).relative_to(root).as_posix()
            text = _read_regular(Path(directory) / name, rel).decode("utf-8")
            if any(line.strip() in (_HTML_OPEN, _MD_OPEN) for line in text.split("\n")):
                found.add(rel)
    return found


def surface_problems(documents):
    """The findings of rule 6: each document (repo-relative) that opens a restatement block and is not a
    DOC_SURFACES entry, whose restatement the gate would otherwise never compare."""
    return ["{} carries a declaration restatement block but is not in DOC_SURFACES, so it is never compared with "
            "the file".format(rel) for rel in sorted(set(documents) - set(DOC_SURFACES))]


def _load():
    """Bind the helpers beside this file that the gate reads (_opf_declaration and _opf_store)."""
    global _opf_declaration, _opf_store
    import _opf_declaration
    import _opf_store


def in_tree(rel):
    """Whether the OPF-relative path `rel` names an entry inside the OPF tree: nonempty, not absolute, and with no
    component empty, "." or ".."."""
    parts = rel.split("/")
    return bool(rel) and not rel.startswith("/") and all(part not in ("", ".", "..") for part in parts)


def scope_problems():
    """The findings of rule 7: each DOC_SURFACES entry lies inside the OPF tree, so it moves with OPF."""
    return ["{} is a DOC_SURFACES entry outside the OPF tree, so it would not move with OPF".format(rel)
            for rel in DOC_SURFACES if not in_tree(rel)]


def local_problems(data, version, surfaces, documents):
    """The findings of rules 1 to 7 over the inputs read from the OPF tree."""
    return (scope_problems() + file_problems(data, version) + doc_problems(data, surfaces)
            + surface_problems(documents))


def run(root):
    try:
        _load()
    except:   # a helper _load() imports is missing, unreadable or fails to import
        exc = sys.exception()
        if issubclass(type(exc), KeyboardInterrupt):
            raise
        return _cannot_evaluate("a helper module cannot be loaded", exc)
    try:
        data = _read_regular(Path(root) / DECLARATION_REL, DECLARATION_REL)
        version = read_opf_version(_read_regular(Path(root) / VERSION_REL, VERSION_REL,
                                                 MAX_OPF_VERSION_BYTES).decode("utf-8"))
        surfaces = dict((rel, _read_regular(Path(root) / rel, rel).decode("utf-8")) for rel in DOC_SURFACES
                        if in_tree(rel))
        documents = restating_documents(root)
    except:   # a CannotEvaluate, a UnicodeDecodeError, an OSError, or any other exception while the inputs are read
        exc = sys.exception()
        if issubclass(type(exc), KeyboardInterrupt):
            raise
        return _cannot_evaluate("an input the gate needs cannot be read", exc)
    problems = local_problems(data, version, surfaces, documents)
    if problems:
        print("opf-declaration-local: FAIL ({} finding(s))".format(len(problems)), file=sys.stderr)
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        return 1
    print("opf-declaration-local: OK ({} sha256 {}; release {}; {} documentation restatements equal)".format(
        DECLARATION_REL, _opf_declaration.SHIPPED_DECLARATION_SHA256, _opf_declaration.RELEASE_IDENTITY,
        len(surfaces)))
    return 0


# --- self-test ---------------------------------------------------------------------------------------------

def _copy_mutant(directory, name, old, new):
    """A copy of this file with `old` (present exactly once) replaced by `new`, written to its own scratch
    directory and loaded with importlib as `name`; the caller removes any sys.modules entry under that name."""
    source = Path(__file__).resolve().read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError("revert anchor found {} times: {!r}".format(source.count(old), old[:60]))
    home = Path(directory) / name
    home.mkdir()
    (home / (name + ".py")).write_text(source.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, home / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def _tree_runs(cases, gate):
    """name -> (the exit status of `gate`, a run function, and its stderr) over a scratch OPF tree of this tree's
    inputs with the case's edits applied (rel -> text, a callable taking the live text, or None to leave the file
    out)."""
    tmp = tempfile.mkdtemp(prefix="opf-declaration-local-selftest-")
    try:
        runs = {}
        for name, edits in cases.items():
            home = Path(tmp, name)
            for rel in (DECLARATION_REL, VERSION_REL) + tuple(rel for rel in DOC_SURFACES if in_tree(rel)):
                os.makedirs(Path(home, rel).parent, exist_ok=True)
                Path(home, rel).write_bytes((ROOT / rel).read_bytes())
            for rel, text in edits.items():
                if text is None:
                    os.unlink(Path(home, rel))
                    continue
                os.makedirs(Path(home, rel).parent, exist_ok=True)
                if callable(text):
                    text = text((ROOT / rel).read_text(encoding="utf-8"))
                Path(home, rel).write_text(text, encoding="utf-8")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
                runs[name] = (gate(home), err.getvalue())
        return runs
    finally:
        shutil.rmtree(tmp)


def _edited(old, new, count=1):
    """An edit of a live text replacing its `old`, present exactly `count` times (any number where count is
    None), by `new`."""
    def apply(text):
        if count is not None and text.count(old) != count:
            raise AssertionError("edit anchor found {} times: {!r}".format(text.count(old), old[:60]))
        return text.replace(old, new)
    return apply


def self_test(report_path=None):
    global _REPORT_ARMED
    results = []
    executed = []
    if report_path is not None:
        # The execution report is finalized at interpreter exit (_selftest_exit_report.py beside this file).
        try:
            _selftest_exit_report.arm(report_path, SUITE_ID, executed)
        except SystemExit:   # arm() refused the run, having printed why: its only SystemExit, not an escape
            print("opf-declaration-local: CANNOT EVALUATE: the execution-report harness refused the run",
                  file=sys.stderr)
            return 2
        _REPORT_ARMED = True
    try:
        _load()
    except:   # a helper is missing, unreadable or fails to import
        exc = sys.exception()
        if issubclass(type(exc), KeyboardInterrupt):
            raise
        return _cannot_evaluate("a helper module cannot be loaded", exc, self_test=True)
    try:
        _self_test_body(results, executed)
    except:   # an input the self-test builds its cases from cannot be read, or a fixture cannot be written
        exc = sys.exception()
        if issubclass(type(exc), KeyboardInterrupt):
            raise
        return _cannot_evaluate("the self-test could not build its cases", exc, self_test=True)
    failed = [(check_id, detail) for check_id, ok, detail in results if not ok]
    if failed:
        print("SELF-TEST FAIL: opf-declaration-local {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: opf-declaration-local {} checks (each rule red on a stale, missing, duplicate or "
          "commented-out restatement, a missing digest location, an undeclared restating document, a release "
          "other than VERSION, a changed declaration file and a surface outside the OPF tree; an unreadable input "
          "or helper a cannot-evaluate; each check's revert red)".format(len(results)))
    return 0


def _self_test_body(results, executed):
    """Every check of the self-test, each recorded in `results` (id, passed, detail) and `executed` (id) as it
    runs."""
    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    here = globals()
    shipped = _opf_declaration.SHIPPED_DECLARATION_BYTES
    version = read_opf_version(_read_regular(ROOT / VERSION_REL, VERSION_REL).decode("utf-8"))
    md_block = "{}\n{}{}\n".format(_MD_OPEN, shipped.decode("ascii"), _MD_CLOSE)
    stale_block = md_block.replace('release = "{}"'.format(version), 'release = "0.0.1"')

    # the live tree: the gate passes, and the documents that open a block are exactly the surfaces
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        live = run(ROOT)
    check("live/real-tree-passes", live == 0, live)
    documents = restating_documents(ROOT)
    check("live/restating-documents-are-the-surfaces", documents == set(DOC_SURFACES),
          sorted(documents ^ set(DOC_SURFACES)))

    # each rule over scratch copies of the live inputs, one edit per case
    cases = {
        "equal": {},
        "releasing-stale": {"RELEASING.md": _edited(md_block, stale_block)},
        "releasing-missing": {"RELEASING.md": _edited(md_block, "")},
        "releasing-duplicate": {"RELEASING.md": _edited(md_block, md_block + "\n" + md_block)},
        "releasing-commented": {"RELEASING.md": _edited(md_block, "<!--\n" + md_block + "-->\n")},
        "quickstart-stale": {"spec/OPF-QUICKSTART.md": _edited(md_block, stale_block)},
        "digest-location-missing": {"RELEASING.md": _edited("SHIPPED_DECLARATION_SHA256", "the digest", None)},
        "undeclared-document": {"spec/notes.md": "carried in {} as {}\n\n{}".format(*(DIGEST_LOCATION + (md_block,)))},
        "version-differs": {VERSION_REL: version + "-other\n"},
        "declaration-differs": {DECLARATION_REL: shipped.decode("ascii") + "# edited\n"},
        "version-unterminated": {VERSION_REL: version},
        "surface-absent": {"RELEASING.md": None},
        # the VERSION size bound: one line just inside, exactly at and just outside MAX_OPF_VERSION_BYTES
        "version-under-size-bound": {VERSION_REL: "9" * (MAX_OPF_VERSION_BYTES - 2) + "\n"},
        "version-at-size-bound": {VERSION_REL: "9" * (MAX_OPF_VERSION_BYTES - 1) + "\n"},
        "version-over-size-bound": {VERSION_REL: "9" * MAX_OPF_VERSION_BYTES + "\n"},
    }

    def finding(runs, name, text):
        """Whether the run `name` exits 1 with a finding line holding `text`."""
        rc, err = runs[name]
        return rc == 1 and any(line.startswith("  ") and text in line for line in err.splitlines())

    def cannot(runs, name, text):
        """Whether the run `name` exits 2 with its cannot-evaluate line for `text`."""
        rc, err = runs[name]
        return rc == 2 and "opf-declaration-local: CANNOT EVALUATE: " + text in err

    def rows(runs):
        """case name -> whether its run gives the expected outcome."""
        return {
            "equal": runs["equal"][0] == 0,
            "releasing-stale": finding(runs, "releasing-stale",
                                       "RELEASING.md restates the declaration malformed: it gives release = '0.0.1'"),
            "releasing-missing": finding(runs, "releasing-missing", "RELEASING.md restates no declaration"),
            "releasing-duplicate": finding(runs, "releasing-duplicate", "RELEASING.md restates no declaration"),
            "releasing-commented": finding(runs, "releasing-commented", "RELEASING.md restates no declaration"),
            "quickstart-stale": finding(runs, "quickstart-stale", "spec/OPF-QUICKSTART.md restates the declaration "
                                        "malformed: it gives release = '0.0.1'"),
            "digest-location-missing": finding(runs, "digest-location-missing",
                                               "RELEASING.md does not state where the shipped-file digest is carried"),
            "undeclared-document": finding(runs, "undeclared-document",
                                           "spec/notes.md carries a declaration restatement block"),
            "version-differs": finding(runs, "version-differs", "is not OPF's own release version"),
            "declaration-differs": finding(runs, "declaration-differs", "differs from SHIPPED_DECLARATION_BYTES"),
            "version-unterminated": cannot(runs, "version-unterminated", "an input the gate needs cannot be read"),
            "surface-absent": cannot(runs, "surface-absent", "an input the gate needs cannot be read"),
            "version-under-size-bound": finding(runs, "version-under-size-bound", "is not OPF's own release version"),
            "version-at-size-bound": finding(runs, "version-at-size-bound", "is not OPF's own release version"),
            "version-over-size-bound": cannot(runs, "version-over-size-bound", "an input the gate needs cannot be read")
            and "is not a regular file of at most {} bytes".format(MAX_OPF_VERSION_BYTES)
            in runs["version-over-size-bound"][1],
        }

    live_rows = rows(_tree_runs(cases, run))
    for check_id, case in (
        ("local/equal-passes", "equal"),
        ("local/releasing-stale-red", "releasing-stale"),
        ("local/releasing-block-missing-red", "releasing-missing"),
        ("local/releasing-block-duplicate-red", "releasing-duplicate"),
        ("local/releasing-block-commented-red", "releasing-commented"),
        ("local/quickstart-stale-red", "quickstart-stale"),
        ("local/digest-location-missing-red", "digest-location-missing"),
        ("local/undeclared-document-red", "undeclared-document"),
        ("local/version-disagreement-red", "version-differs"),
        ("local/declaration-bytes-differ-red", "declaration-differs"),
        ("local/version-unterminated-cannot-evaluate", "version-unterminated"),
        ("local/surface-absent-cannot-evaluate", "surface-absent"),
    ):
        check(check_id, live_rows[case] is True, (case, live_rows))
    bound_cases = ("version-under-size-bound", "version-at-size-bound", "version-over-size-bound")
    check("local/version-size-bound", all(live_rows[case] is True for case in bound_cases)
          and MAX_OPF_VERSION_BYTES == _opf_declaration.MAX_OPF_VERSION_BYTES,
          (dict((case, live_rows[case]) for case in bound_cases), MAX_OPF_VERSION_BYTES,
           _opf_declaration.MAX_OPF_VERSION_BYTES))

    # rule 7 at its boundary: just inside (a name that only starts with dots, a nested path), at it (the bare
    # parent name, an empty or "." component) and just outside (a parent path, an absolute path, a parent step
    # inside the path)
    boundary = {"RELEASING.md": True, "spec/OPF-QUICKSTART.md": True, "..RELEASING.md": True, "..": False, "": False,
                "./RELEASING.md": False, "spec//notes.md": False, "../RELEASING.md": False, "/RELEASING.md": False,
                "spec/../RELEASING.md": False}
    check("local/in-tree-boundary", all(in_tree(rel) is expected for rel, expected in boundary.items()),
          dict((rel, in_tree(rel)) for rel in boundary))
    outside = DOC_SURFACES + ("../RELEASING.md",)
    saved = here["DOC_SURFACES"]
    here["DOC_SURFACES"] = outside
    try:
        found = scope_problems()
        scope_runs = _tree_runs({"equal": {}}, run)
    finally:
        here["DOC_SURFACES"] = saved
    check("local/surface-outside-tree-red", found == ["../RELEASING.md is a DOC_SURFACES entry outside the OPF tree, "
                                                      "so it would not move with OPF"]
          and scope_runs["equal"][0] == 1 and found[0] in scope_runs["equal"][1], (found, scope_runs))

    # a helper that cannot be imported is a cannot-evaluate (exit 2)
    def missing_helper():
        raise ImportError("no module named _opf_declaration (a self-test stand-in)")

    saved_load = here["_load"]
    here["_load"] = missing_helper
    try:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            helper_rc = run(ROOT)
    finally:
        here["_load"] = saved_load
    check("local/helper-missing-cannot-evaluate", helper_rc == 2 and "CANNOT EVALUATE: a helper module cannot be "
          "loaded" in err.getvalue(), (helper_rc, err.getvalue()))

    # the reverts: each a mutant of this file loaded from a scratch directory, run over the case its check shows
    tmp = tempfile.mkdtemp(prefix="opf-declaration-local-selftest-")
    try:
        mutants = {}
        for name, old, new in (
            ("duplicate", "    if len(opens) != 1:\n        return None\n    start = opens[0]",
             "    if not opens:\n        return None\n    start = opens[0]"),
            ("comment", "    if _in_comment(rel, lines[:start]):\n        return None\n", ""),
            ("compare", "        found = _restatement_problem(rel, restatement(rel, text), fields)\n",
             "        found = None\n"),
            ("location", "        missing = [phrase for phrase in DIGEST_LOCATION if phrase not in text]\n",
             "        missing = []\n"),
            ("documents", "the file\".format(rel) for rel in sorted(set(documents) - set(DOC_SURFACES))]",
             "the file\".format(rel) for rel in sorted(set(documents) - set(DOC_SURFACES)) if False]"),
            ("version", "    if version != _opf_declaration.RELEASE_IDENTITY:\n", "    if False:\n"),
            ("file", "    return (scope_problems() + file_problems(data, version) + doc_problems(data, surfaces)\n",
             "    return (scope_problems() + doc_problems(data, surfaces)\n"),
            ("scope", "            for rel in DOC_SURFACES if not in_tree(rel)]\n",
             "            for rel in DOC_SURFACES if False]\n"),
            ("boundary", "all(part not in (\"\", \".\", \"..\") for part in parts)", "\"..\" not in parts[:1]"),
            ("helper", "        return _cannot_evaluate(\"a helper module cannot be loaded\", exc)\n",
             "        return 1\n"),
            ("input", "        return _cannot_evaluate(\"an input the gate needs cannot be read\", exc)\n",
             "        return 1\n"),
            ("version-bound", "VERSION_REL,\n                                                 MAX_OPF_VERSION_BYTES)",
             "VERSION_REL)"),
        ):
            module = "opf_declaration_local_mutant_" + name
            try:
                mutants[name] = _copy_mutant(tmp, module, old, new)
            except AssertionError as exc:
                mutants[name] = str(exc)
            finally:
                sys.modules.pop(module, None)

        def reverted(name, case):
            """(the case's rows entry, exit status, stderr) under the named mutant, or why it could not run."""
            mutant = mutants[name]
            if isinstance(mutant, str):
                return mutant
            runs = _tree_runs({case: cases[case]}, mutant.run)
            outcome = rows(runs | dict((other, (0, "")) for other in cases if other != case))[case]
            return outcome, runs[case][0], runs[case][1]

        # each row: the check id, the mutant, the case it is run over and the exit status the mutant gives there
        # (0 where the revert lets the case pass clean; 1 where another rule still reports a finding, or where the
        # revert turns a cannot-evaluate into a finding)
        for check_id, name, case, status in (
            ("revert/duplicate-block-read-by-first-red", "duplicate", "releasing-duplicate", 0),
            ("revert/comment-check-dropped-red", "comment", "releasing-commented", 0),
            ("revert/restatement-compare-dropped-red", "compare", "releasing-stale", 0),
            ("revert/quickstart-compare-dropped-red", "compare", "quickstart-stale", 0),
            ("revert/digest-location-check-dropped-red", "location", "digest-location-missing", 0),
            ("revert/undeclared-document-check-dropped-red", "documents", "undeclared-document", 0),
            ("revert/version-check-dropped-red", "version", "version-differs", 0),
            ("revert/file-rules-dropped-red", "file", "declaration-differs", 1),
            ("revert/input-cannot-evaluate-as-finding-red", "input", "surface-absent", 1),
            ("revert/version-size-bound-dropped-red", "version-bound", "version-over-size-bound", 1),
        ):
            outcome = reverted(name, case)
            check(check_id, not isinstance(outcome, str) and outcome[0] is False and outcome[1] == status
                  and "Traceback" not in outcome[2], (name, case, outcome))
        bounded = mutants["boundary"]
        moved = bounded if isinstance(bounded, str) else dict((rel, bounded.in_tree(rel)) for rel in boundary)
        check("revert/in-tree-boundary-components-red", isinstance(moved, dict) and any(
            moved[rel] is not expected for rel, expected in boundary.items()), moved)
        unscoped = mutants["scope"]
        if isinstance(unscoped, str):
            dropped = unscoped
        else:
            unscoped.DOC_SURFACES = outside
            dropped = unscoped.scope_problems()
        check("revert/scope-check-dropped-red", dropped == [], dropped)
        helper = mutants["helper"]
        if isinstance(helper, str):
            helper_reverted = helper
        else:
            helper._load = missing_helper
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                helper_reverted = helper.run(ROOT)
        check("revert/helper-cannot-evaluate-as-finding-red", helper_reverted == 1, helper_reverted)
    finally:
        shutil.rmtree(tmp)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    reported = (len(args) in (2, 3) and args[-2] == "--execution-report" and os.path.isabs(args[-1])
                and args[:-2] in ([], ["--self-test"]))
    if reported and _HELPER_ERROR is not None:
        if issubclass(type(_HELPER_ERROR), KeyboardInterrupt):
            raise _HELPER_ERROR   # here: tools/check_python_floor.py admits no raise in an import fallback
        return _cannot_evaluate("the execution-report helper cannot be loaded", _HELPER_ERROR)
    status = None
    try:
        if reported:
            status = self_test(args[-1])
        elif args == ["--self-test"]:
            status = self_test()
        elif not args:
            status = run(ROOT)
    except:   # anything that escapes the gate body, a helper's SystemExit included
        exc = sys.exception()
        if issubclass(type(exc), KeyboardInterrupt):
            raise
        status = _cannot_evaluate("an exception escaped the gate body", exc)
    if status is None:
        print("usage: check_opf_declaration.py [--self-test | [--self-test] --execution-report ABS_PATH]",
              file=sys.stderr)
        return 2
    if _REPORT_ARMED:
        _selftest_exit_report.exit_with(status)   # armed: raises the exit itself
    return status


# The child-status guard: an inline copy of the canonical guard in _child_status.py beside this file, which the
# parent's tools/check_child_status_coverage.py pins this copy to by AST. Inline, not imported, so the guard runs
# before any helper is loaded. Under an ignored SIGCHLD a failed child would read as a success; the entry runs the
# guard before main(). The status the probe child exits with; any value other than 0 and the refusal exit would do.
_CHILD_STATUS_PROBE_CODE = 77


def _child_status_refusal():
    """None when this process can read a child's exit status, otherwise the reason it cannot."""
    import os
    import signal
    if not hasattr(signal, "SIGCHLD"):
        return None
    disposition = signal.getsignal(signal.SIGCHLD)
    if disposition is not signal.SIG_DFL:
        return ("SIGCHLD is ignored (SIG_IGN) in this process" if disposition is signal.SIG_IGN
                else "SIGCHLD is not at its default disposition in this process")
    if not hasattr(os, "fork"):
        return None
    try:
        pid = os.fork()
    except OSError as exc:
        return "the child-status probe could not fork ({})".format(type(exc).__name__)
    if pid == 0:
        os._exit(_CHILD_STATUS_PROBE_CODE)
    try:
        waited, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return "the probe child was reaped before its exit status could be read (ECHILD)"
    except OSError as exc:
        return "the probe child's exit status could not be read ({})".format(type(exc).__name__)
    if waited != pid or os.waitstatus_to_exitcode(status) != _CHILD_STATUS_PROBE_CODE:
        return "a probe child that exited {} was read back as wait status {}".format(
            _CHILD_STATUS_PROBE_CODE, status)
    return None


def _child_status_refusal_exit(name):
    """0 when a child's exit status is observable; otherwise write the named refusal and exit 2 at once
    (os._exit, so no exit-time stream flush can replace the status)."""
    import os
    reason = _child_status_refusal()
    if reason is None:
        return 0
    try:
        sys.stderr.write("error: {} refuses to run: {}, so a failed child would read as a success. Nothing "
                         "was run (cannot evaluate). Start it from a parent that leaves SIGCHLD at "
                         "SIG_DFL.\n".format(name, reason))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(_child_status_refusal_exit("check_opf_declaration.py") or main())
