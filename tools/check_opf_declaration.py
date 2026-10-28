#!/usr/bin/env python3
"""Release gate for the OPF reference tooling's implementation declaration (OPF-SPEC section 16.1).

  check_opf_declaration.py                        check this tree (exit 0 clean, 1 finding, 2 cannot evaluate)
  check_opf_declaration.py --self-test [--execution-report ABS_PATH]
  check_opf_declaration.py --execution-report ABS_PATH
                                                  the self-test (exit 0 pass, 1 fail, 2 cannot evaluate), the
                                                  last form as tools/check_selftest_execution.py launches it;
                                                  any other argument list is a usage error (exit 2)

The gate applies the section 16.1 rules to the declaration file opf/tools/opf-declaration.toml as the release
ships it, never to an installed copy and never taking a field from the documentation:

  1. the file is a regular file whose bytes are well-formed (_opf_declaration.parse_declaration: the four
     fields, typed, in the canonical form) and do not contradict the build (release RELEASE_IDENTITY and the
     supported spec_version, homes and worklog generations of _opf_store);
  2. its bytes equal SHIPPED_DECLARATION_BYTES and their SHA-256 digest equals SHIPPED_DECLARATION_SHA256,
     the shipped-file digest the implementation carries;
  3. RELEASE_IDENTITY equals the pack release version in VERSION (the release identity is that version);
  4. each documentation surface (DOC_SURFACES) restates that declaration byte-equal to the file: a restatement
     that gives another value, omits a field or states a field the file does not carry is malformed and a
     finding, so the release cannot be cut, and so is a missing restatement;
  5. each surface states where the implementation carries the digest (DIGEST_LOCATION, both phrases);
  6. every Markdown or HTML document (a .md or .html file) of the tree (the .git directory aside, and no
     symlinked directory entered) with a line that opens a restatement block is a DOC_SURFACES entry, so a
     surface dropped from that list is a finding.

A restatement is the block of an HTML surface between a line `<div class="tree"><pre id="opf-declaration">`
and the next line `</pre></div>`, and of a Markdown surface between a line "```toml opf-declaration" and the
next line "```" at the same indentation (that indentation is removed from each line of the block). A block
whose opening line may lie inside an HTML comment is no restatement, so a surface whose only block is commented
out is a finding for having none. Where the reader cannot tell whether a comment is open, it holds it open:

  - In an HTML surface a `<!--` opens a comment and the next `-->` closes it, read lexically, so a marker that
    is not a comment (in an attribute value, a script or a textarea) still counts and refuses the block.
  - In a Markdown surface a `<!--` counts unless it lies in a fenced code block, or, outside an HTML block, in
    what the reader takes for a code span: a run of backticks to the next run of exactly as many backticks on
    the same line, found lexically, so a backslash before the opening backtick does not stop it and the run may
    start inside a longer one (both "a \\`<!--` b" and "a ``<!--` b" lose their `<!--`, though CommonMark
    renders each as text, not code). Under CommonMark a `<!--` in a paragraph opens no comment the paragraph
    does not close, so a span read that way hides no comment there; a span over more than one line is not
    removed. A fenced code block is skipped only where its opening line starts at the left margin, outside any
    HTML block and before any fence-like line the reader cannot place (one that, after its indentation and any
    quote or list markers, starts with three backticks or tildes and that the reader does not take as surely
    opening a fence: one that is indented, inside a list item, a quote or an HTML block other than a comment
    block opened at the left margin, or a backtick run with a backtick after it; its end may close a fence
    there), and it ends at a closing line of at most three spaces
    of indentation. So an indented code block, an indented fence and every line after a fence-like line the
    reader cannot place keep their markers.
  - In a Markdown surface a line that begins with `<` (after any indentation and any quote or list markers)
    may open an HTML block, which lasts until its CommonMark end condition, or the later end of any block one
    of its lines may open; inside it no fence opens and no code span is removed. Inside an HTML block, a `-->`
    closes a comment opened on an earlier line only in an HTML comment block opened by a `<!--` at the left
    margin (before any fence-like line the reader cannot place); in any other HTML block it closes only a
    `<!--` on its own line, and an HTML block that ends with a comment open (a raw `<!--` that no raw `-->`
    closes, so a browser hides the rest of the page) leaves it open to the end of the surface. Outside an HTML
    block, a `<!--` in a paragraph counts, though CommonMark renders one that the paragraph does not close as
    text, and a `-->` there closes a comment opened on an earlier line.

A block a reader cannot see for another reason (a hidden element, a style, or a Markdown fence around it) is
not detected.

Its entry point first runs the child-status guard, an inline copy of opf/tools/_child_status.py that
tools/check_child_status_coverage.py pins to it: where this process cannot read a child's exit status (SIGCHLD
ignored), it writes a refusal naming this gate and exits 2 before main() runs; otherwise it changes nothing.

The gate exits 0 only by an explicit normal return, but for the loaded-code residual below: its entry point calls
main() inside a bare except and exits with main()'s status only when main() returns exactly the int 0, 1 or 2.
Where the self-test has armed its execution report, it exits through _selftest_exit_report.exit_with (which raises
that exit itself; should it return, the entry point raises SystemExit(2) after a fixed CANNOT EVALUATE line);
everywhere else (a plain run, a plain --self-test, or where that helper cannot be imported) it raises
SystemExit(status) itself and never calls exit_with, which unarmed exits through sys.exit, a function loaded code
can replace. It tells an armed report by the gate's own record that arm() returned (arm() returns only once it has
armed the report), never by reading that helper's state. Any other return value, and any exception that leaves
main() whatever its type or MRO, ends the gate with exit 2 after a fixed CANNOT EVALUATE line printed best effort,
reading nothing from the exception; only a KeyboardInterrupt by its real type propagates (on CPython 3.14.4 the
interpreter then ends the process by SIGINT, measured for a subclass too, one that is also a SystemExit included).
No in-process code closes that residual: code the gate loads runs inside its process with the gate's own
privileges, at import, when called, or later (an atexit hook, a thread, a signal handler, a destructor), and can
end the process at any time with any status or replace anything the gate uses (the record above included), so
these exit guarantees hold only while that code does neither. Under --execution-report a run that code ends before
the report's finalizer has written the report writes no finalized report, and tools/check_selftest_execution.py
refuses it, as it refuses one that code ends afterwards with a status other than the report's exit_code, unless
that code also forges the report or the finalizer's state, the loaded-code residual that gate discloses;
tools/_selftest_exit_report.py discloses its own loaded-code tier the same way. A plain run, which the CI Quality
workflow and tools/run_all_checks.sh launch, ends with whatever status that code chose (measured: a helper whose
import calls os._exit(0) ends the gate with exit 0 and no output).

Every helper module the gate imports that cannot be imported (it is missing or unreadable, or its import raises
any exception other than a KeyboardInterrupt, which is re-raised unchanged; a SyntaxError, a SystemExit of any
code, a bare sys.exit() and an exception whose MRO leaves out BaseException are included) is a cannot-evaluate
(exit 2), never a finding or a clean exit: tools/_selftest_exit_report.py and tools/check_selftest_execution.py,
opf/tools/_opf_declaration.py and opf/tools/_opf_store.py, and, for the self-test, opf/tools/opf.py and each
opf/tools helper its _bootstrap() imports (one that _bootstrap() itself maps to its exit 2 included). So is a
declaration file, VERSION, surface or rule 6 document that is not a regular file of at most MAX_READ_BYTES bytes,
cannot be read or (but for the declaration file) is not UTF-8, and a directory rule 6 cannot list, and any other
exception (a KeyboardInterrupt aside) raised while these inputs are read. So, last, is any escape from the gate
body: any exception other than a KeyboardInterrupt (re-raised unchanged) that leaves run() or the self-test,
whatever its type or MRO, a SystemExit of any code or an error raised by a helper the gate calls included, ends
the gate with exit 2, never with that SystemExit's code or a traceback's exit 1. Each site that catches one of
these does so with a bare except, which also catches an exception whose MRO leaves out BaseException (no except
BaseException clause matches one), and takes it with sys.exception(), which reads nothing from it. The tools-side
import is the one site that cannot take it so: an import fallback holds no call (tools/check_python_floor.py), so
it binds what its except BaseException clause catches and records any other exception, unbound, as a fixed
ImportError that says so, a cannot-evaluate even where that exception's MRO names KeyboardInterrupt. An exception
is a KeyboardInterrupt here by its real type alone: each such test applies issubclass to the exception's type(),
never isinstance, so the gate reads none of the exception's attributes (its __class__ included) before printing
its CANNOT EVALUATE line, and an exception whose __class__ raises (a SystemExit included) or claims
KeyboardInterrupt is a cannot-evaluate like any other. Each of these cannot-evaluates prints its fixed CANNOT
EVALUATE line first, then a cause line with the exception's type name and message; where reading the message
raises (any exception other than a KeyboardInterrupt, whatever its MRO, a SystemExit included), the cause line
gives the type name alone, or is left out where that cannot be read either, and the exit is 2 all the same. A
KeyboardInterrupt raised while a CANNOT EVALUATE line or the cause line is printed, or while the message or type
name is read (a Ctrl-C, or a __str__ that raises one), told by its real type in the same way, propagates
instead. The one SystemExit the gate expects, the execution-report harness refusing a --execution-report run that
tools/check_selftest_execution.py did not launch (it prints its own SELF-TEST HARNESS ERROR line), is labelled as
that refusal (exit 2), not as an escape.

The self-test runs every vector of opf/tools/_opf_declaration.py (the runtime check and the emitter) and every
spec 16.1 vector of opf/tools/opf.py (the gate before dispatch on each refusing state, the ungated verbs,
`--declaration` and the doctor report) under its own registered check id, shows each red under its revert (a
mutant of _opf_declaration.py or opf.py loaded from a scratch directory, or a patched attribute of opf.py,
_opf_declaration or _opf_store; under uid 0 the unreadable-mode vector is skipped and so not shown red), and
runs this gate's own document and helper-import vectors. Its check ids are registered in
tools/selftest_checks.toml (suite opf-declaration-selftest) and reconciled by tools/check_selftest_execution.py.
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

import contextlib
import hashlib
import importlib.util
import io
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# what the import fallback below records for an exception it cannot bind: one whose MRO leaves out BaseException,
# which no except BaseException clause matches and a bare except takes only through a call (sys.exception()),
# which tools/check_python_floor.py admits in no import fallback
_UNBOUND_HELPER_ERROR = ImportError("a tools-side helper's import raised an exception whose MRO leaves out "
                                    "BaseException, so it is not bound")
try:
    import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)
    import check_selftest_execution  # noqa: E402  (the authoritative registry schema)
except BaseException as exc:   # a tools-side helper is missing, unreadable or fails to import: main() exits 2
    _selftest_exit_report = check_selftest_execution = None
    _HELPER_ERROR = exc
except:   # the same, by an exception whose MRO leaves out BaseException: main() exits 2
    _selftest_exit_report = check_selftest_execution = None
    _HELPER_ERROR = _UNBOUND_HELPER_ERROR
else:
    _HELPER_ERROR = None

ROOT = Path(__file__).resolve().parents[1]
OPF_TOOLS = ROOT / "opf" / "tools"
# The opf/tools helpers are imported by _load(), after the self-test arms its execution report: the report's
# finalizer must be the process's first atexit registration, and importing them registers others.
_opf_declaration = None
_opf_store = None
# The gate's own record that the self-test armed its execution report: set once _selftest_exit_report.arm() returns,
# which it does only having armed it. The entry point exits through exit_with only then; unarmed, exit_with is sys.exit.
_REPORT_ARMED = False

SUITE_ID = "opf-declaration-selftest"
REGISTRY_REL = "tools/selftest_checks.toml"
DECLARATION_REL = "opf/tools/opf-declaration.toml"
MODULE_REL = "opf/tools/_opf_declaration.py"
VERSION_REL = "VERSION"
DOC_SURFACES = ("opf/site/tooling.html", "opf/spec/OPF-QUICKSTART.md", "RELEASING.md")
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
        print(("SELF-TEST CANNOT EVALUATE: opf-declaration: " if self_test else
               "opf-declaration: CANNOT EVALUATE: ") + what, file=stream)
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


def _load():
    """Bind the opf/tools helpers this gate reads (_opf_declaration and _opf_store)."""
    global _opf_declaration, _opf_store
    if str(OPF_TOOLS) not in sys.path:
        sys.path.insert(0, str(OPF_TOOLS))
    import _opf_declaration
    import _opf_store


def _load_opf():
    """opf/tools/opf.py, whose dispatcher vectors the self-test runs (after _load(), which puts it on the path)."""
    import opf
    return opf


def _read_regular(path, rel):
    """The bytes of a regular, non-symlink file of at most MAX_READ_BYTES bytes."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as exc:
        raise CannotEvaluate("{} cannot be opened ({})".format(rel, exc.strerror or exc))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_READ_BYTES:
            raise CannotEvaluate("{} is not a regular file of at most {} bytes".format(rel, MAX_READ_BYTES))
        chunks = []
        total = 0
        while total <= MAX_READ_BYTES:
            chunk = os.read(fd, MAX_READ_BYTES + 1 - total)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            total += len(chunk)
        raise CannotEvaluate("{} grew past {} bytes while read".format(rel, MAX_READ_BYTES))
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


def file_problems(data, version):
    """The findings of rules 1 to 3 over the declaration file's bytes as shipped and the VERSION text."""
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
    if version.rstrip("\n") != _opf_declaration.RELEASE_IDENTITY:
        problems.append("{}: RELEASE_IDENTITY {!r} is not the pack release version {!r} in {}".format(
            MODULE_REL, _opf_declaration.RELEASE_IDENTITY, version.rstrip("\n"), VERSION_REL))
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
        version = _read_regular(Path(root) / VERSION_REL, VERSION_REL).decode("utf-8")
        surfaces = dict((rel, _read_regular(Path(root) / rel, rel).decode("utf-8")) for rel in DOC_SURFACES)
        documents = restating_documents(root)
    except:   # a CannotEvaluate, a UnicodeDecodeError, an OSError, or any other exception while the inputs are read
        exc = sys.exception()
        if issubclass(type(exc), KeyboardInterrupt):
            raise
        return _cannot_evaluate("an input the gate needs cannot be read", exc)
    problems = file_problems(data, version) + doc_problems(data, surfaces) + surface_problems(documents)
    if problems:
        print("opf-declaration: FAIL ({} finding(s))".format(len(problems)), file=sys.stderr)
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        return 1
    print("opf-declaration: OK ({} sha256 {}; release {}; {} documentation restatements equal)".format(
        DECLARATION_REL, _opf_declaration.SHIPPED_DECLARATION_SHA256, _opf_declaration.RELEASE_IDENTITY,
        len(DOC_SURFACES)))
    return 0


# --- self-test ---------------------------------------------------------------------------------------------

def _registered_check_ids():
    """This suite's registered check ids, read through the execution gate's schema: (set, None) or (None, why)."""
    try:
        data = tomllib.loads(_read_regular(ROOT / REGISTRY_REL, REGISTRY_REL).decode("utf-8"))
    except (CannotEvaluate, RecursionError, MemoryError, ValueError) as exc:
        return None, "{}: {}".format(REGISTRY_REL, exc)
    with contextlib.redirect_stderr(io.StringIO()):
        rows = check_selftest_execution._manifest_rows(data, REGISTRY_REL)
    row = next((row for row in rows or () if row["id"] == SUITE_ID), None)
    if row is None:
        return None, "{} registers no valid suite {}".format(REGISTRY_REL, SUITE_ID)
    return set(row["expected-check-ids"]), None


def _mutant(directory, number, old, new, declaration=None):
    """A copy of _opf_declaration.py with `old` (present exactly once) replaced by `new`, written to its own
    scratch directory beside a copy of the declaration file (or `declaration`), and loaded with importlib."""
    source = (OPF_TOOLS / "_opf_declaration.py").read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError("revert anchor found {} times: {!r}".format(source.count(old), old[:60]))
    home = Path(directory) / "mutant-{}".format(number)
    home.mkdir()
    (home / "_opf_declaration.py").write_text(source.replace(old, new), encoding="utf-8")
    (home / _opf_declaration.DECLARATION_NAME).write_bytes(
        (OPF_TOOLS / _opf_declaration.DECLARATION_NAME).read_bytes() if declaration is None else declaration)
    spec = importlib.util.spec_from_file_location("opf_declaration_mutant_{}".format(number),
                                                  home / "_opf_declaration.py")
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def _copy_mutant(directory, source_path, name, old, new):
    """A copy of the module at source_path with `old` (present exactly once) replaced by `new`, written to its own
    scratch directory and loaded with importlib as `name`, registered in sys.modules while it runs (opf.py's
    dispatcher vectors patch their own module through it); the caller removes that entry."""
    source = Path(source_path).read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise AssertionError("revert anchor found {} times: {!r}".format(source.count(old), old[:60]))
    home = Path(directory) / name
    home.mkdir()
    (home / (name + ".py")).write_text(source.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, home / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    return module


def _red(module, vector):
    """True where `vector` of the module is red (fails, or raises)."""
    ok, _detail = module.run_vector(vector)
    return not ok


def _sample_surfaces(block_html, block_md, location=True):
    where = "carried in {} as {}".format(*DIGEST_LOCATION) if location else "carried in the implementation"
    return {"doc.html": "<p>{}</p>\n{}\n{}{}\n</p>\n".format(where, _HTML_OPEN, block_html.decode("ascii"),
                                                             _HTML_CLOSE),
            "doc.md": "{}\n\n   {}\n{}   {}\n".format(where, _MD_OPEN, "".join(
                "   " + line + "\n" for line in block_md.decode("ascii").splitlines()), _MD_CLOSE)}


def self_test(report_path=None):
    global _REPORT_ARMED
    results = []
    executed = []
    if report_path is not None:
        # The execution report is finalized at interpreter exit (tools/_selftest_exit_report.py).
        try:
            _selftest_exit_report.arm(report_path, SUITE_ID, executed)
        except SystemExit:   # arm() refused the run, having printed why: its only SystemExit, not an escape
            print("opf-declaration: CANNOT EVALUATE: the execution-report harness refused the run",
                  file=sys.stderr)
            return 2
        _REPORT_ARMED = True

    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    try:
        _load()
        opf = _load_opf()
    except:   # a helper or opf.py is missing, unreadable or fails to import
        exc = sys.exception()
        if issubclass(type(exc), KeyboardInterrupt):
            raise
        return _cannot_evaluate("a helper module cannot be loaded", exc, self_test=True)
    from unittest.mock import patch   # after arm: it imports logging, whose import registers an atexit hook
    try:
        bootstrapped = opf._bootstrap()
    except:   # a helper opf.py's _bootstrap() imports raises past its own arms
        exc = sys.exception()
        if issubclass(type(exc), KeyboardInterrupt):
            raise
        return _cannot_evaluate("opf.py helpers could not be bootstrapped", exc, self_test=True)
    if bootstrapped != 0:
        print("SELF-TEST CANNOT EVALUATE: opf-declaration: opf.py helpers could not be bootstrapped")
        return 2
    shipped = _opf_declaration.SHIPPED_DECLARATION_BYTES

    # 1. every runtime-check and emitter vector of _opf_declaration.py, each under its own id (the rows are a
    # literal display in the for statement, the one loop form the execution gate's static layer resolves)
    covered = set()
    for check_id, vector in (
        ("runtime/shipped-digest-matches-file", "shipped-digest-matches-file"),
        ("runtime/valid-authorizes", "valid-authorizes"),
        ("runtime/absent-not-read", "absent-not-read"),
        ("runtime/unreadable-directory", "unreadable-directory"),
        ("runtime/unreadable-fifo", "unreadable-fifo"),
        ("runtime/unreadable-symlink", "unreadable-symlink"),
        ("runtime/unreadable-oversize", "unreadable-oversize"),
        ("runtime/size-cap-boundary", "size-cap-boundary"),
        ("runtime/crlf-is-digest-mismatch", "crlf-is-digest-mismatch"),
        ("runtime/field-edit-is-digest-mismatch", "field-edit-is-digest-mismatch"),
        ("runtime/malformed-missing-release", "malformed-missing-release"),
        ("runtime/malformed-missing-spec_version", "malformed-missing-spec_version"),
        ("runtime/malformed-missing-homes", "malformed-missing-homes"),
        ("runtime/malformed-missing-worklog", "malformed-missing-worklog"),
        ("runtime/malformed-extra-key", "malformed-extra-key"),
        ("runtime/malformed-bool-homes", "malformed-bool-homes"),
        ("runtime/malformed-bad-semver", "malformed-bad-semver"),
        ("runtime/malformed-unparseable", "malformed-unparseable"),
        ("runtime/malformed-comment", "malformed-comment"),
        ("runtime/contradictory-release", "contradictory-release"),
        ("runtime/contradictory-spec_version", "contradictory-spec_version"),
        ("runtime/contradictory-homes", "contradictory-homes"),
        ("runtime/contradictory-worklog", "contradictory-worklog"),
        ("runtime/report-four-parts", "report-four-parts"),
        ("runtime/report-pass-with-store-operation", "report-pass-with-store-operation"),
        ("runtime/report-not-read-pairs-cannot-evaluate", "report-not-read-pairs-cannot-evaluate"),
        ("runtime/report-restates-shipped-on-mismatch", "report-restates-shipped-on-mismatch"),
        ("runtime/report-digest-form", "report-digest-form"),
        ("runtime/declaration-tracks-constants", "declaration-tracks-constants"),
    ):
        check(check_id, *_opf_declaration.run_vector(vector))
        covered.add(vector)
    check("registry/runtime-vectors-covered", covered == set(name for name, _fn in _opf_declaration.VECTORS),
          sorted(covered ^ set(name for name, _fn in _opf_declaration.VECTORS)))

    # 2. each runtime vector red under its revert: a mutant of _opf_declaration.py, or a patched constant
    digest_line = 'SHIPPED_DECLARATION_SHA256 = "{}"'.format(_opf_declaration.SHIPPED_DECLARATION_SHA256)
    expected_line = '        ("{}", {}),\n'
    reverted = set()
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    number = 0
    for check_id, vector, old, new in (
        ("revert/shipped-digest-constant-alone-red", "shipped-digest-matches-file", digest_line,
         'SHIPPED_DECLARATION_SHA256 = "{}"'.format("0" * 64)),
        ("revert/never-authorizes-red", "valid-authorizes", '    return Check(OK, digest, data, "")',
         '    return Check(MALFORMED, digest, data, "")'),
        ("revert/absent-read-as-ok-red", "absent-not-read",
         '        return Check(ABSENT, NOT_READ, None, "no declaration file at {!r}".format(str(path)))',
         '        return Check(OK, NOT_READ, None, "")'),
        ("revert/directory-regular-file-check-dropped-red", "unreadable-directory",
         "        if not stat.S_ISREG(st.st_mode):", "        if False:"),
        ("revert/fifo-regular-file-check-dropped-red", "unreadable-fifo",
         "        if not stat.S_ISREG(st.st_mode):", "        if False:"),
        ("revert/symlink-followed-red", "unreadable-symlink", "os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK",
         "os.O_RDONLY | os.O_NONBLOCK"),
        ("revert/oversize-cap-dropped-red", "unreadable-oversize", "MAX_DECLARATION_BYTES = 4096",
         "MAX_DECLARATION_BYTES = 1 << 20"),
        ("revert/size-cap-off-by-one-red", "size-cap-boundary", "        if st.st_size > MAX_DECLARATION_BYTES:",
         "        if st.st_size >= MAX_DECLARATION_BYTES:"),
        ("revert/crlf-digest-step-dropped-red", "crlf-is-digest-mismatch",
         "    if digest != SHIPPED_DECLARATION_SHA256:", "    if False:"),
        ("revert/field-edit-digest-step-dropped-red", "field-edit-is-digest-mismatch",
         "    if digest != SHIPPED_DECLARATION_SHA256:", "    if False:"),
        ("revert/missing-release-check-dropped-red", "malformed-missing-release", "    if missing:", "    if False:"),
        ("revert/missing-spec_version-check-dropped-red", "malformed-missing-spec_version", "    if missing:",
         "    if False:"),
        ("revert/missing-homes-check-dropped-red", "malformed-missing-homes", "    if missing:", "    if False:"),
        ("revert/missing-worklog-check-dropped-red", "malformed-missing-worklog", "    if missing:", "    if False:"),
        ("revert/extra-key-check-dropped-red", "malformed-extra-key", "    if extra:", "    if False:"),
        ("revert/bool-admitted-as-integer-red", "malformed-bool-homes",
         "        if type(table[key]) is not int:", "        if not isinstance(table[key], int):"),
        ("revert/semver-check-dropped-red", "malformed-bad-semver",
         "        if type(table[key]) is not str or _parse_version(table[key]) is None:",
         "        if type(table[key]) is not str:"),
        ("revert/toml-error-not-handled-red", "malformed-unparseable",
         "    except (RecursionError, MemoryError, ValueError) as exc:", "    except (RecursionError, MemoryError) as exc:"),
        ("revert/canonical-form-check-dropped-red", "malformed-comment",
         '    if declaration_bytes(table["release"], table["spec_version"], table["homes"], table["worklog"]) != '
         'data:', "    if False:"),
        ("revert/release-cross-check-dropped-red", "contradictory-release",
         expected_line.format("release", "RELEASE_IDENTITY"), ""),
        ("revert/spec_version-cross-check-dropped-red", "contradictory-spec_version",
         expected_line.format("spec_version", "_opf_store.SUPPORTED_SPEC_VERSION"), ""),
        ("revert/homes-cross-check-dropped-red", "contradictory-homes",
         expected_line.format("homes", "_opf_store.SUPPORTED_HOMES"), ""),
        ("revert/worklog-cross-check-dropped-red", "contradictory-worklog",
         expected_line.format("worklog", "_opf_store.SUPPORTED_WORKLOG"), ""),
        ("revert/report-emits-pass-red", "report-four-parts",
         "    return RESULT_PASS if store_operations_performed else RESULT_FAIL", "    return RESULT_PASS"),
        ("revert/report-emits-fail-red", "report-pass-with-store-operation",
         "    return RESULT_PASS if store_operations_performed else RESULT_FAIL", "    return RESULT_FAIL"),
        ("revert/not-read-guard-dropped-red", "report-not-read-pairs-cannot-evaluate",
         "    if check.installed_sha256 == NOT_READ and result != RESULT_CANNOT_EVALUATE:", "    if False:"),
        ("revert/report-restates-installed-red", "report-restates-shipped-on-mismatch",
         '    stream.write("[declaration]\\n" + SHIPPED_DECLARATION_BYTES.decode("ascii")',
         '    stream.write("[declaration]\\n" + (check.data or SHIPPED_DECLARATION_BYTES).decode("ascii")'),
        ("revert/digest-form-unchecked-red", "report-digest-form", '_DIGEST_RE = re.compile(r"[0-9a-f]{64}\\Z")',
         '_DIGEST_RE = re.compile(r".*")'),
    ):
        number += 1
        try:
            red, detail = _red(_mutant(tmp, number, old, new), vector), vector
        except AssertionError as exc:
            red, detail = False, "{}: {}".format(vector, exc)
        check(check_id, red, detail)
        reverted.add(vector)
    edited = shipped.replace(b"homes = 1", b"homes = 2")
    check("revert/shipped-file-alone-red",
          _red(_mutant(tmp, "file", "MAX_DECLARATION_BYTES = 4096", "MAX_DECLARATION_BYTES = 4096", edited),
               "shipped-digest-matches-file"))
    shutil.rmtree(tmp)
    with patch.object(_opf_store, "SUPPORTED_HOMES", _opf_store.SUPPORTED_HOMES + 1):
        check("revert/constants-flipped-without-the-file-red",
              not _opf_declaration.run_vector("declaration-tracks-constants")[0])
    reverted |= {"declaration-tracks-constants"}
    check("registry/runtime-reverts-covered", reverted == covered, sorted(covered - reverted))

    # 3. the dispatcher's spec 16.1 vectors, each under its own id, and each red under its revert
    cli_covered = set()
    for check_id, vector in (
        ("cli/gate-refuses-every-store-verb", "gate-refuses-every-store-verb"),
        ("cli/gate-refuses-symlink", "gate-refuses-symlink"),
        ("cli/gate-refuses-crlf", "gate-refuses-crlf"),
        ("cli/gate-refuses-empty", "gate-refuses-empty"),
        ("cli/gate-refuses-directory", "gate-refuses-directory"),
        ("cli/gate-refuses-fifo", "gate-refuses-fifo"),
        ("cli/gate-refuses-oversize", "gate-refuses-oversize"),
        ("cli/gate-refuses-unreadable-mode", "gate-refuses-unreadable-mode"),
        ("cli/gate-refuses-malformed", "gate-refuses-malformed"),
        ("cli/gate-refuses-contradictory", "gate-refuses-contradictory"),
        ("cli/ungated-verbs-read-no-declaration", "ungated-verbs-read-no-declaration"),
        ("cli/report-documented-run", "report-documented-run"),
        ("cli/report-cannot-evaluate-run", "report-cannot-evaluate-run"),
        ("cli/doctor-carries-report", "doctor-carries-report"),
    ):
        check(check_id, *opf.run_declaration_cli_vector(vector))
        cli_covered.add(vector)
    check("registry/cli-vectors-covered", cli_covered == set(name for name, _fn in opf._DECLARATION_CLI_VECTORS))
    with patch.object(opf, "_DECLARATION_GATED", ()):
        check("revert/cli-gate-dropped-red", not opf.run_declaration_cli_vector("gate-refuses-every-store-verb")[0])
    with patch.object(opf, "_DECLARATION_GATED", tuple(opf.KNOWN_VERBS)):
        check("revert/cli-ungated-verbs-gated-red",
              not opf.run_declaration_cli_vector("ungated-verbs-read-no-declaration")[0])
    with patch.object(_opf_declaration, "runtime_result",
                      lambda found, performed: "pass" if found.ok else "cannot_evaluate"):
        check("revert/cli-report-emits-pass-red", not opf.run_declaration_cli_vector("report-documented-run")[0])
    with patch.object(_opf_declaration, "runtime_check",
                      lambda path: _opf_declaration.Check(_opf_declaration.OK, _opf_declaration.SHIPPED_DECLARATION_SHA256)):
        check("revert/cli-check-never-refuses-red",
              not opf.run_declaration_cli_vector("report-cannot-evaluate-run")[0])
    with patch.object(opf, "_DECLARATION_REPORTING", ()):
        check("revert/cli-doctor-report-dropped-red", not opf.run_declaration_cli_vector("doctor-carries-report")[0])
    # The gate narrowed to refuse on absent alone (a mutant of opf.py): every other refusing state then
    # dispatches, so each of its rows is red (the mode row is skipped, and so excluded, under uid 0), while the
    # absent row stays green, so the mutant is that narrowing and not a broken copy.
    states = ["gate-refuses-symlink", "gate-refuses-crlf", "gate-refuses-empty", "gate-refuses-directory",
              "gate-refuses-fifo", "gate-refuses-oversize", "gate-refuses-unreadable-mode",
              "gate-refuses-malformed", "gate-refuses-contradictory"]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        states.remove("gate-refuses-unreadable-mode")
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        narrowed = _copy_mutant(tmp, OPF_TOOLS / "opf.py", "opf_declaration_gate_mutant",
                                "        if not check.ok:\n            print(_opf_declaration.refusal_line(verb, check)",
                                "        if check.state == _opf_declaration.ABSENT:\n"
                                "            print(_opf_declaration.refusal_line(verb, check)")
        rows = dict((vector, narrowed.run_declaration_cli_vector(vector)[0])
                    for vector in ["gate-refuses-every-store-verb"] + states)
    except AssertionError as exc:
        rows = {"anchor": str(exc)}
    finally:
        sys.modules.pop("opf_declaration_gate_mutant", None)
        shutil.rmtree(tmp)
    check("revert/cli-gate-refuses-absent-alone-red", rows.get("gate-refuses-every-store-verb") is True
          and not any(rows.get(vector, True) for vector in states), rows)

    # 4. this gate's own rules over synthetic documentation and files
    homes2 = shipped.replace(b"homes = 1", b"homes = 2")
    no_worklog = b"".join(line for line in shipped.splitlines(keepends=True) if not line.startswith(b"worklog"))
    version = _opf_declaration.RELEASE_IDENTITY + "\n"
    check("doc/equal-passes", doc_problems(shipped, _sample_surfaces(shipped, shipped)) == [],
          doc_problems(shipped, _sample_surfaces(shipped, shipped)))
    found = doc_problems(shipped, _sample_surfaces(homes2, shipped))
    check("doc/value-changed-red", len(found) == 1 and "gives homes = 2" in found[0], found)
    found = doc_problems(shipped, _sample_surfaces(shipped, no_worklog))
    check("doc/field-omitted-red", len(found) == 1 and "omits worklog" in found[0], found)
    found = doc_problems(shipped, _sample_surfaces(shipped + b'class = "full"\n', shipped))
    check("doc/field-added-red", len(found) == 1 and "states class" in found[0], found)
    found = doc_problems(shipped, _sample_surfaces(shipped, shipped.replace(b"\n", b"\n\n")))
    check("doc/byte-variant-red", len(found) == 1 and "not in the canonical form" in found[0], found)
    found = doc_problems(shipped, {"doc.md": "carried in {} as {}\n".format(*DIGEST_LOCATION)})
    check("doc/restatement-missing-red", len(found) == 1 and "restates no declaration" in found[0], found)
    found = doc_problems(shipped, _sample_surfaces(shipped, shipped, location=False))
    check("doc/digest-location-missing-red", len(found) == 2 and all("does not state where" in problem
                                                                       for problem in found), found)
    found = file_problems(shipped.replace(b"\n", b"\r\n"), version)
    check("doc/file-crlf-red", any("malformed" in problem for problem in found)
          and any("shipped-file digest" in problem for problem in found), found)
    found = file_problems(shipped, "1.0.99\n")
    check("doc/release-not-version-red", len(found) == 1 and "pack release version" in found[0], found)
    with patch.object(sys.modules[__name__], "_restatement_problem", lambda rel, block, fields: None):
        found = doc_problems(shipped, _sample_surfaces(homes2, no_worklog))
    check("revert/doc-restatement-compare-dropped-red", found == [], found)
    here = sys.modules[__name__]
    commented = dict((rel, "<!--\n" + text + "-->\n") for rel, text in _sample_surfaces(shipped, shipped).items())
    found = doc_problems(shipped, commented)
    check("doc/commented-restatement-red", len(found) == 2 and all("restates no declaration" in problem
                                                                    for problem in found), found)
    found = doc_problems(shipped, dict((rel, "<!-- a note -->\n" + text)
                                       for rel, text in _sample_surfaces(shipped, shipped).items()))
    check("doc/closed-comment-before-passes", found == [], found)
    with patch.object(here, "_in_comment", lambda rel, lines: False):
        found = doc_problems(shipped, commented)
    check("revert/comment-check-dropped-red", found == [], found)
    sample = _sample_surfaces(shipped, shipped)
    literal = {"doc.md": "a `<!--` and ``x <!-- y`` in code\n\n```html\n<!--\n```\n\n" + sample["doc.md"]}
    found = doc_problems(shipped, literal)
    check("doc/markdown-code-marker-passes", found == [], found)
    found = doc_problems(shipped, {"doc.md": "a `code` then <!-- a comment\n\n" + sample["doc.md"],
                                   "doc.html": "<code><!--</code>\n" + sample["doc.html"]})
    check("doc/marker-outside-markdown-code-red", len(found) == 2 and all("restates no declaration" in problem
                                                                           for problem in found), found)
    with patch.object(here, "_markdown_in_comment", lambda text: _scan_markers(text, False)):
        found = doc_problems(shipped, literal)
    check("revert/markdown-code-not-skipped-red", len(found) == 1 and "restates no declaration" in found[0], found)
    # the code span read lexically: a backslash before the opening backtick, or an opening run inside a longer
    # one, still removes the `<!--` (CommonMark renders both as text, not a comment, so the restatement shows),
    # and a `-->` in a later paragraph closes a comment a paragraph opened
    for check_id, prefix in (
        ("doc/backslash-escaped-backtick-marker-passes", "a \\`<!--` b\n\n"),
        ("doc/longer-backtick-run-marker-passes", "a ``<!--` b\n\n"),
        ("doc/paragraph-closer-closes-earlier-comment-passes", "a <!-- b\n\nc --> d\n\n"),
    ):
        found = doc_problems(shipped, {"doc.md": prefix + sample["doc.md"]})
        check(check_id, found == [], found)

    # Markdown forms a CommonMark reader renders with the restatement inside a comment (raw HTML that opens one
    # and nothing raw closes), whose fence or HTML block a lexical reader misplaces: each is a finding, and red
    # under the revert of the rule it needs
    md = sample["doc.md"]
    hidden = {
        "html-block-fence": "<div>\n```\n</div>\n\n<!--\n" + md,
        "indented-code-fence": "    ```\n\n<!--\n" + md,
        "list-item-fence": "carried in {} as {}\n\n- item\n\n  ```\n- <!--\n\n".format(*DIGEST_LOCATION)
                           + md.split("\n\n", 1)[1],
        "html-pre-block-fence": "<pre>\n\n```\n</pre>\n\n<!--\n" + md,
        "indented-fence-then-margin-fence": "  ```\n  x\n```\n<!--\n" + md,
        "indented-fence-closer": "```\n    ```\n```\n\n<!--\n" + md,
        "html-block-code-span": "<div>`<!--`\n\n" + md,
        "quoted-html-escaped-closer": "> <div><!--\n-->\n\n" + md,
        "html-block-escaped-closer": "<div><!--\n\n-->\n\n" + md,
        "html-block-inside-paragraph": "<3\n<pre>\n\n```\n</pre>\n\n<!--\n" + md,
    }
    for check_id, name in (
        ("doc/html-block-fence-red", "html-block-fence"),
        ("doc/indented-code-fence-red", "indented-code-fence"),
        ("doc/list-item-fence-red", "list-item-fence"),
        ("doc/html-pre-block-fence-red", "html-pre-block-fence"),
        ("doc/indented-fence-then-margin-fence-red", "indented-fence-then-margin-fence"),
        ("doc/indented-fence-closer-red", "indented-fence-closer"),
        ("doc/html-block-code-span-red", "html-block-code-span"),
        ("doc/quoted-html-escaped-closer-red", "quoted-html-escaped-closer"),
        ("doc/html-block-escaped-closer-red", "html-block-escaped-closer"),
        ("doc/html-block-inside-paragraph-red", "html-block-inside-paragraph"),
    ):
        found = doc_problems(shipped, {"doc.md": hidden[name]})
        check(check_id, len(found) == 1 and "restates no declaration" in found[0], found)
    # and forms where the marker is not raw HTML (a comment closed before the block, a fence whose info has a
    # backtick after tildes) still pass
    found = doc_problems(shipped, {"doc.md": "<!--\n```\n-->\n\n" + md})
    check("doc/closed-comment-with-fence-line-passes", found == [], found)
    found = doc_problems(shipped, {"doc.md": "~~~ `x`\n<!--\n~~~\n\n" + md})
    check("doc/tilde-fence-backtick-info-passes", found == [], found)

    def loose_opener(line, in_html, unsure):   # a fence on any line whose stripped text starts with one
        found = _MD_FENCE.match(line.strip())
        return found and (found.group(1) or found.group(2))

    def clean_under(name, replaced):
        """True where the gate finds nothing in the hidden surface `name` with these attributes replaced."""
        with contextlib.ExitStack() as stack:
            for attribute, value in replaced.items():
                stack.enter_context(patch.object(here, attribute, value))
            return doc_problems(shipped, {"doc.md": hidden[name]}) == []

    sure_opener = _md_fence_opener
    for check_id, names, replaced in (
        ("revert/fence-placement-unchecked-red",
         ("html-block-fence", "indented-code-fence", "list-item-fence", "html-pre-block-fence"),
         {"_md_fence_opener": loose_opener}),
        ("revert/unplaced-fence-line-ignored-red", ("indented-fence-then-margin-fence",),
         {"_md_fence_opener": lambda line, in_html, unsure: sure_opener(line, in_html, False),
          "_md_comment_block": lambda line, unsure: line.startswith(_COMMENT_OPEN)}),
        ("revert/fence-closer-indentation-unchecked-red", ("indented-fence-closer",),
         {"_md_fence_closes": lambda line, run: set(line.strip()) == {run[0]} and len(line.strip()) >= len(run)}),
        ("revert/html-block-code-span-removed-red", ("html-block-code-span",),
         {"_md_text": lambda line, in_html: _CODE_SPAN.sub("", line)}),
        ("revert/every-html-block-closes-earlier-comment-red", ("quoted-html-escaped-closer",),
         {"_md_comment_block": lambda line, unsure: True}),
    ):
        check(check_id, all(clean_under(name, replaced) for name in names),
              [name for name in names if not clean_under(name, replaced)])
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        for check_id, name, old, new in (
            ("revert/html-block-open-comment-dropped-red", "html-block-escaped-closer",
             "        if is_open:   # a raw `<!--` no raw `-->` closes: a browser hides the rest of the "
             "surface", "        if False:"),
            ("revert/nested-html-block-ignored-red", "html-block-inside-paragraph",
             "            block[1].add(_md_html_end(body))   # the line may open a block of its own, which may end "
             "later", "            pass"),
        ):
            module = "opf_declaration_md_mutant_" + name.replace("-", "_")
            try:
                mutant = _copy_mutant(tmp, Path(__file__).resolve(), module, old, new)
                mutant._opf_declaration = _opf_declaration
                found = mutant.doc_problems(shipped, {"doc.md": hidden[name]})
            except AssertionError as exc:
                found = str(exc)
            finally:
                sys.modules.pop(module, None)
            check(check_id, found == [], found)
    finally:
        shutil.rmtree(tmp)

    # rule 6: the documents that open a restatement block are exactly DOC_SURFACES, and one dropped from it is red
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        for rel, text in (("sub/a.md", "   " + _MD_OPEN + "\n"), ("b.html", _HTML_OPEN + "\n"),
                          ("c.txt", _MD_OPEN + "\n"), (".git/d.md", _MD_OPEN + "\n"), ("e.md", "no block\n"),
                          ("f.md", "the line " + _MD_OPEN + " in prose\n")):
            os.makedirs(Path(tmp, rel).parent, exist_ok=True)
            Path(tmp, rel).write_text(text, encoding="utf-8")
        found = restating_documents(tmp)
    finally:
        shutil.rmtree(tmp)
    check("doc/restating-documents-found", found == {"sub/a.md", "b.html"}, sorted(found))
    documents = restating_documents(ROOT)
    check("live/restating-documents-are-the-surfaces", documents == set(DOC_SURFACES),
          sorted(documents ^ set(DOC_SURFACES)))
    dropped = tuple(rel for rel in DOC_SURFACES if rel != "RELEASING.md")
    with patch.object(here, "DOC_SURFACES", dropped), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()) as err:
        found, rc = surface_problems(documents), run(ROOT)
    check("live/surface-dropped-red", len(found) == 1 and found[0].startswith("RELEASING.md ") and rc == 1
          and found[0] in err.getvalue(), (found, rc))
    with patch.object(here, "DOC_SURFACES", dropped), patch.object(here, "surface_problems", lambda documents: []), \
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = run(ROOT)
    check("revert/surface-list-check-dropped-red", rc == 0, rc)

    # a helper module that cannot be imported is a cannot-evaluate (exit 2), and escapes (exit 1, a finding) with
    # _load() back outside the try
    def missing_helper():
        raise ModuleNotFoundError("No module named '_opf_declaration'", name="_opf_declaration")
    with patch.object(here, "_load", missing_helper), contextlib.redirect_stderr(io.StringIO()) as err:
        try:
            rc = run(ROOT)
        except ImportError as exc:
            rc = "raised {!r}".format(exc)
    check("live/missing-helper-cannot-evaluate", rc == 2 and "cannot be loaded" in err.getvalue(),
          (rc, err.getvalue()))
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        unguarded = _copy_mutant(tmp, Path(__file__).resolve(), "opf_declaration_load_mutant",
                                 "    try:\n        _load()\n    except:   # a helper _load()",
                                 "    _load()\n    try:\n        pass\n"
                                 "    except:   # a helper _load()")
        with patch.object(unguarded, "_load", missing_helper), contextlib.redirect_stderr(io.StringIO()):
            try:
                rc = unguarded.run(ROOT)
            except ImportError as exc:
                rc = "raised {!r}".format(exc)
    except AssertionError as exc:
        rc = 2, str(exc)
    finally:
        sys.modules.pop("opf_declaration_load_mutant", None)
        shutil.rmtree(tmp)
    check("revert/load-outside-try-red", rc != 2 and str(rc).startswith("raised"), rc)

    # the self-test too: a missing opf/tools helper or opf.py is a cannot-evaluate, and escapes with the load back
    # outside the try
    def missing_opf():
        raise ModuleNotFoundError("No module named 'opf'", name="opf")
    outcomes = []
    for replaced in ({"_load": missing_helper}, {"_load_opf": missing_opf}):
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()) as out:
            for attribute, value in replaced.items():
                stack.enter_context(patch.object(here, attribute, value))
            outcomes.append((self_test(), "cannot be loaded" in out.getvalue()))
    check("live/self-test-missing-helper-cannot-evaluate", outcomes == [(2, True), (2, True)], outcomes)
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        unguarded = _copy_mutant(tmp, Path(__file__).resolve(), "opf_declaration_self_test_load_mutant",
                                 "    try:\n        _load()\n        opf = _load_opf()\n    except",
                                 "    _load()\n    opf = _load_opf()\n    try:\n        pass\n    except")
        with patch.object(unguarded, "_load", lambda: None), patch.object(unguarded, "_load_opf", missing_opf), \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                rc = unguarded.self_test()
            except ImportError as exc:
                rc = "raised {!r}".format(exc)
    except AssertionError as exc:
        rc = 2, str(exc)
    finally:
        sys.modules.pop("opf_declaration_self_test_load_mutant", None)
        shutil.rmtree(tmp)
    check("revert/self-test-load-outside-try-red", str(rc).startswith("raised"), rc)

    # a missing tools-side helper (the execution report or the registry schema) is a cannot-evaluate in every
    # mode: a copy of this gate alone in a scratch tools directory, launched as its own process, exits 2; with
    # its imports unguarded it exits 1 on the escaping ModuleNotFoundError
    source = Path(__file__).resolve().read_text(encoding="utf-8")
    guard = ("except BaseException as exc:   # a tools-side helper "
             "is missing, unreadable or fails to import")
    # the bare handler after it, which catches what that clause cannot (an exception whose MRO leaves out
    # BaseException): each revert of the tools-side guard drops it as well
    unbound = ("except:   # the same, by an exception whose MRO leaves out " "BaseException: main() exits 2\n"
               "    _selftest_exit_report = check_selftest_execution = None\n"
               "    _HELPER_ERROR = _UNBOUND_HELPER_ERROR\n")
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        lone = []
        for name, text, args in (("guarded", source, []), ("guarded-self-test", source, ["--self-test"]),
                                 ("unguarded", source.replace(guard, "except SyntaxError as exc:  # ").replace(
                                     unbound, ""), [])):
            home = Path(tmp, name, "tools")
            home.mkdir(parents=True)
            (home / "check_opf_declaration.py").write_text(text, encoding="utf-8")
            done = subprocess.run([sys.executable, "-I", "-B", str(home / "check_opf_declaration.py")] + args,
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
            lone.append((done.returncode, done.stdout + done.stderr))
    finally:
        shutil.rmtree(tmp)
    check("live/missing-tools-helper-cannot-evaluate", source.count(guard) == source.count(unbound) == 1 and all(
        rc == 2 and "CANNOT EVALUATE" in output and "_selftest_exit_report" in output for rc, output in lone[:2]),
          lone[:2])
    check("revert/tools-helper-import-unguarded-red", lone[2][0] == 1 and "ModuleNotFoundError" in lone[2][1],
          lone[2])

    # a helper that is present but fails to import (a SyntaxError, or an error it raises) is a cannot-evaluate at
    # each of the three load sites: a copy of this gate in a scratch tree of stub helpers, one of them broken,
    # launched as its own process, exits 2; with that site's guard narrowed to (ImportError, OSError) the error
    # escapes and it exits 1
    guards = {
        "tools": guard,
        "run": "except:   # a helper _load() imports " "is missing, unreadable or fails to import",
        "self-test": "except:   # a helper or opf.py " "is missing, unreadable or fails to import",
    }
    stubs = {"tools/_selftest_exit_report.py": "import sys\nexit_with = sys.exit\n",
             "tools/check_selftest_execution.py": "", "opf/tools/_opf_declaration.py": "",
             "opf/tools/_opf_store.py": "", "opf/tools/opf.py": ""}
    syntax_error, raises = "def broken(:\n", "raise RuntimeError('broken at import')\n"
    # the entry point turns any exception that leaves main(), and any return value but 0, 1 or 2, into exit 2 by
    # itself, so each revert that must let such an outcome end the gate is applied to a copy (`called`) whose entry
    # point calls main() bare, as it did before it was guarded
    entry = source[source.rindex("\nif __name__ == " '"__main__":\n') + 1:]
    called = source.replace(entry, 'if __name__ == "__main__":\n    (sys.exit if _selftest_exit_report is None else '
                            '_selftest_exit_report.exit_with)(main())\n')
    # the guard in main() turns any escape from run() or the self-test into exit 2 by itself, so each revert of a
    # guard inside them is applied to a copy of this gate with that guard in main() removed as well (and with the
    # entry point calling main() bare, and the tools-side import's bare handler dropped)
    main_try = "    try:\n        if (len(args) in (2, 3)"
    main_guard = ("    except:   # anything that escapes the gate body, a helper's SystemExit "
                  "included\n        exc = sys.exception()\n        if issubclass(type(exc), KeyboardInterrupt):\n"
                  "            raise\n")
    at = called.find(main_guard)
    removed_main = (called[:at] + called[called.find('    print("usage:', at):]).replace(
        main_try, "    if True:\n        if (len(args) in (2, 3)").replace(unbound, "")

    def launch(home, site, rel, text, args, narrowing, helpers=stubs, gate=None):
        """The (exit status, output) of a copy of this gate (or `gate`) in a scratch tree of the stub `helpers` at
        `home`, the helper `rel` holding `text`, launched with `args`; with `narrowing`, the guard at `site`
        replaced by it, and the guard in main() removed."""
        for stub_rel, stub in dict(helpers, **{rel: text}).items():
            (home / stub_rel).parent.mkdir(parents=True, exist_ok=True)
            (home / stub_rel).write_text(stub, encoding="utf-8")
        gate = removed_main.replace(guards[site], narrowing) if narrowing else gate or source
        (home / "tools" / "check_opf_declaration.py").write_text(gate, encoding="utf-8")
        done = subprocess.run([sys.executable, "-I", "-B", str(home / "tools" / "check_opf_declaration.py")] + args,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
        return done.returncode, done.stdout + done.stderr
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        broken = {}
        for name, site, rel, text, args in (
            ("tools-syntax", "tools", "tools/check_selftest_execution.py", syntax_error, []),
            ("tools-raises", "tools", "tools/_selftest_exit_report.py", raises, ["--self-test"]),
            ("run-syntax", "run", "opf/tools/_opf_declaration.py", syntax_error, []),
            ("run-raises", "run", "opf/tools/_opf_store.py", raises, []),
            ("self-test-syntax", "self-test", "opf/tools/_opf_declaration.py", syntax_error, ["--self-test"]),
            ("self-test-raises", "self-test", "opf/tools/opf.py", raises, ["--self-test"]),
        ):
            for narrowed in (False, True):
                broken[name, narrowed] = launch(Path(tmp, "{}-{}".format(name, int(narrowed))), site, rel, text, args,
                                                "except (ImportError, OSError) as exc:   #" if narrowed else None)
    finally:
        shutil.rmtree(tmp)

    def broken_rows(site, narrowed):
        """The (exit status, error named) of the two broken-helper runs at `site`, guarded or narrowed: a guarded
        run names the error in its cannot-evaluate line, a narrowed one in its traceback."""
        rows = []
        for kind, error in (("syntax", "SyntaxError"), ("raises", "RuntimeError")):
            rc, output = broken["{}-{}".format(site, kind), narrowed]
            rows.append((rc, error in output and (narrowed or "CANNOT EVALUATE" in output)))
        return rows
    for check_id, site, narrowed, rc in (
        ("live/broken-tools-helper-cannot-evaluate", "tools", False, 2),
        ("revert/tools-helper-guard-narrowed-red", "tools", True, 1),
        ("live/broken-opf-helper-cannot-evaluate", "run", False, 2),
        ("revert/load-guard-narrowed-red", "run", True, 1),
        ("live/self-test-broken-helper-cannot-evaluate", "self-test", False, 2),
        ("revert/self-test-load-guard-narrowed-red", "self-test", True, 1),
    ):
        check(check_id, source.count(guards[site]) == 1 and broken_rows(site, narrowed) == [(rc, True)] * 2,
              broken_rows(site, narrowed))

    # a helper whose import ends by raising a BaseException that is not an Exception (a SystemExit of any code, a
    # bare sys.exit(), or a subclass of its own) is a cannot-evaluate at each load site too, never a clean exit:
    # with that site's guard narrowed back to Exception, a SystemExit(0) or a bare sys.exit() escapes and the gate
    # exits 0, and a subclass of its own escapes and it exits 1
    system_exit_0, bare_exit = "raise SystemExit(0)\n", "import sys\nsys.exit()\n"
    own_base = "class Stop(BaseException):\n    pass\n\n\nraise Stop('stopped at import')\n"
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        exiting = {}
        for site, kind, rel, text, args in (
            ("tools", "exit-0", "tools/check_selftest_execution.py", system_exit_0, []),
            ("tools", "bare-exit", "tools/_selftest_exit_report.py", bare_exit, []),
            ("tools", "own-base", "tools/check_selftest_execution.py", own_base, []),
            ("run", "exit-0", "opf/tools/_opf_declaration.py", system_exit_0, []),
            ("run", "bare-exit", "opf/tools/_opf_store.py", bare_exit, []),
            ("run", "own-base", "opf/tools/_opf_store.py", own_base, []),
            ("self-test", "exit-0", "opf/tools/opf.py", system_exit_0, ["--self-test"]),
            ("self-test", "bare-exit", "opf/tools/opf.py", bare_exit, ["--self-test"]),
            ("self-test", "own-base", "opf/tools/opf.py", own_base, ["--self-test"]),
        ):
            for narrowed in (False, True):
                exiting[site, kind, narrowed] = launch(Path(tmp, "{}-{}-{}".format(site, kind, int(narrowed))), site,
                                                       rel, text, args,
                                                       "except Exception as exc:   #" if narrowed else None)
    finally:
        shutil.rmtree(tmp)
    for check_id, site, kind, narrowed, rc in (
        ("live/tools-helper-system-exit-0-cannot-evaluate", "tools", "exit-0", False, 2),
        ("revert/tools-helper-guard-narrowed-system-exit-0-red", "tools", "exit-0", True, 0),
        ("live/tools-helper-bare-sys-exit-cannot-evaluate", "tools", "bare-exit", False, 2),
        ("revert/tools-helper-guard-narrowed-bare-sys-exit-red", "tools", "bare-exit", True, 0),
        ("live/opf-helper-system-exit-0-cannot-evaluate", "run", "exit-0", False, 2),
        ("revert/load-guard-narrowed-system-exit-0-red", "run", "exit-0", True, 0),
        ("live/opf-helper-bare-sys-exit-cannot-evaluate", "run", "bare-exit", False, 2),
        ("revert/load-guard-narrowed-bare-sys-exit-red", "run", "bare-exit", True, 0),
        ("live/self-test-helper-system-exit-0-cannot-evaluate", "self-test", "exit-0", False, 2),
        ("revert/self-test-load-guard-narrowed-system-exit-0-red", "self-test", "exit-0", True, 0),
        ("live/self-test-helper-bare-sys-exit-cannot-evaluate", "self-test", "bare-exit", False, 2),
        ("revert/self-test-load-guard-narrowed-bare-sys-exit-red", "self-test", "bare-exit", True, 0),
    ):
        found, output = exiting[site, kind, narrowed]
        check(check_id, source.count(guards[site]) == 1 and found == rc
              and (narrowed or "CANNOT EVALUATE" in output and "SystemExit" in output), (found, output))
    own = [exiting[site, "own-base", False] for site in guards]
    check("live/custom-base-exception-helper-cannot-evaluate",
          all(rc == 2 and "CANNOT EVALUATE" in output and "Stop" in output for rc, output in own), own)
    own = [exiting[site, "own-base", True] for site in guards]
    check("revert/custom-base-exception-guard-narrowed-red",
          all(rc == 1 and "Stop" in output for rc, output in own), own)

    # and so is one that opf.py's _bootstrap() imports for the self-test, past its ImportError and OSError arms: a
    # copy of this gate beside a copy of opf.py in a scratch tree of stub helpers, _opf_worklog ending its import
    # with a SystemExit(0) or a bare sys.exit(), exits 2; with the _bootstrap() call moved out of its guard the
    # SystemExit escapes and the self-test exits 0
    bootstrap_guard = "    try:\n        bootstrapped = opf._bootstrap()\n"
    outside = removed_main.replace(bootstrap_guard, "    bootstrapped = opf._bootstrap()\n    try:\n        pass\n")
    with_opf = dict(stubs, **{"opf/tools/opf.py": (OPF_TOOLS / "opf.py").read_text(encoding="utf-8")})
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        bootstrapping = {}
        for kind, text in (("exit-0", system_exit_0), ("bare-exit", bare_exit)):
            for removed in (False, True):
                bootstrapping[kind, removed] = launch(Path(tmp, "bootstrap-{}-{}".format(kind, int(removed))), None,
                                                      "opf/tools/_opf_worklog.py", text, ["--self-test"], None,
                                                      with_opf, outside if removed else None)
    finally:
        shutil.rmtree(tmp)
    for check_id, kind, removed, rc in (
        ("live/self-test-bootstrap-helper-system-exit-0-cannot-evaluate", "exit-0", False, 2),
        ("revert/self-test-bootstrap-guard-removed-system-exit-0-red", "exit-0", True, 0),
        ("live/self-test-bootstrap-helper-bare-sys-exit-cannot-evaluate", "bare-exit", False, 2),
        ("revert/self-test-bootstrap-guard-removed-bare-sys-exit-red", "bare-exit", True, 0),
    ):
        found, output = bootstrapping[kind, removed]
        check(check_id, source.count(bootstrap_guard) == 1 and found == rc
              and (removed or "CANNOT EVALUATE" in output and "bootstrapped\n  cause: SystemExit" in output),
              (found, output))

    # and so is any escape from the gate body once the helpers are loaded: a copy of this gate in a scratch tree of
    # stub helpers, one helper function raising a SystemExit(0) or a RuntimeError when the gate calls it during
    # run() or the self-test, exits 2; with the guard in main() removed the SystemExit escapes and the gate exits 0,
    # and the RuntimeError escapes and it exits 1
    tree = dict(stubs, **dict((rel, "") for rel in (DECLARATION_REL, VERSION_REL) + DOC_SURFACES))
    with_vectors = dict(stubs, **{"opf/tools/opf.py": "def _bootstrap():\n    return 0\n"})
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        escaping = {}
        for kind, helpers, text, args in (
            ("run-exit-0", tree, "def parse_declaration(data):\n    raise SystemExit(0)\n", []),
            ("self-test-exit-0", with_vectors,
             "SHIPPED_DECLARATION_BYTES = b''\n\n\ndef run_vector(vector):\n    raise SystemExit(0)\n", ["--self-test"]),
            ("run-error", tree, "def parse_declaration(data):\n    raise RuntimeError('raised by a helper call')\n", []),
        ):
            for removed in (False, True):
                escaping[kind, removed] = launch(Path(tmp, "escape-{}-{}".format(kind, int(removed))), None,
                                                 "opf/tools/_opf_declaration.py", text, args, None, helpers,
                                                 removed_main if removed else None)
    finally:
        shutil.rmtree(tmp)
    for check_id, kind, removed, rc, cause in (
        ("live/run-helper-call-system-exit-0-cannot-evaluate", "run-exit-0", False, 2,
         "escaped the gate body\n  cause: SystemExit: 0"),
        ("revert/main-guard-removed-run-system-exit-0-red", "run-exit-0", True, 0, ""),
        ("live/self-test-helper-call-system-exit-0-cannot-evaluate", "self-test-exit-0", False, 2,
         "escaped the gate body\n  cause: SystemExit: 0"),
        ("revert/main-guard-removed-self-test-system-exit-0-red", "self-test-exit-0", True, 0, ""),
        ("live/run-helper-call-error-cannot-evaluate", "run-error", False, 2,
         "escaped the gate body\n  cause: RuntimeError: raised by a helper call"),
        ("revert/main-guard-removed-run-error-red", "run-error", True, 1, "RuntimeError: raised by a helper call"),
    ):
        found, output = escaping[kind, removed]
        check(check_id, source.count(main_try) == 1 and source.count(main_guard) == 1 and found == rc
              and cause in output and (removed or "CANNOT EVALUATE" in output), (found, output))

    # a cause that cannot be described (an exception whose __str__ raises a SystemExit(0)) still ends the gate with
    # exit 2 at every site that describes a caught exception: the site prints its fixed line first and the cause
    # line falls back to the type name; with that site reverted to the single print that formats the cause into
    # its line, the SystemExit escapes and the gate exits 0 (a site inside run() or the self-test is reverted in a
    # copy with the guard in main() removed as well, and every site in a copy whose entry point calls main() bare)
    unprintable = "class Unprintable(Exception):\n    def __str__(self):\n        raise SystemExit(0)\n\n\n"

    def single_print(what, name, self_test=False):
        """A describing site's call, and the single print it reverts to."""
        call = 'return _cannot_evaluate("{}", {}{})'.format(what, name, ", self_test=True" if self_test else "")
        line = "SELF-TEST CANNOT EVALUATE: opf-declaration: " if self_test else "opf-declaration: CANNOT EVALUATE: "
        return call, 'return print("{}{} ({{}}: {{}})".format(type({}).__name__, {}), file=sys.{}) or 2'.format(
            line, what, name, name, "stdout" if self_test else "stderr")
    describing = {
        "tools": single_print("a helper module cannot be loaded", "_HELPER_ERROR"),
        "run": single_print("a helper module cannot be loaded", "exc"),
        "read": single_print("an input the gate needs cannot be read", "exc"),
        "self-test": single_print("a helper module cannot be loaded", "exc", True),
        "bootstrap": single_print("opf.py helpers could not be bootstrapped", "exc", True),
        "main": single_print("an exception escaped the gate body", "exc"),
    }
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        undescribed = {}
        for name, site, rel, text, args, helpers in (
            ("tools", "tools", "tools/check_selftest_execution.py", unprintable + "raise Unprintable()\n", [], stubs),
            ("run", "run", "opf/tools/_opf_store.py", unprintable + "raise Unprintable()\n", [], stubs),
            ("self-test", "self-test", "opf/tools/opf.py", unprintable + "raise Unprintable()\n", ["--self-test"],
             stubs),
            ("bootstrap", "bootstrap", "opf/tools/_opf_worklog.py", unprintable + "raise Unprintable()\n",
             ["--self-test"], with_opf),
            ("main-run", "main", "opf/tools/_opf_declaration.py",
             unprintable + "def parse_declaration(data):\n    raise Unprintable()\n", [], tree),
            ("main-self-test", "main", "opf/tools/_opf_declaration.py",
             unprintable + "SHIPPED_DECLARATION_BYTES = b''\n\n\ndef run_vector(vector):\n    raise Unprintable()\n",
             ["--self-test"], with_vectors),
        ):
            for reverted in (False, True):
                gate = (called if site in ("tools", "main") else removed_main).replace(*describing[site])
                undescribed[name, reverted] = launch(Path(tmp, "undescribed-{}-{}".format(name, int(reverted))), None,
                                                     rel, text, args, None, helpers, gate if reverted else source)
    finally:
        shutil.rmtree(tmp)
    for check_id, name, site, reverted, rc in (
        ("live/tools-helper-undescribable-cannot-evaluate", "tools", "tools", False, 2),
        ("revert/tools-helper-single-print-undescribable-red", "tools", "tools", True, 0),
        ("live/opf-helper-undescribable-cannot-evaluate", "run", "run", False, 2),
        ("revert/load-guard-single-print-undescribable-red", "run", "run", True, 0),
        ("live/self-test-helper-undescribable-cannot-evaluate", "self-test", "self-test", False, 2),
        ("revert/self-test-load-guard-single-print-undescribable-red", "self-test", "self-test", True, 0),
        ("live/self-test-bootstrap-helper-undescribable-cannot-evaluate", "bootstrap", "bootstrap", False, 2),
        ("revert/self-test-bootstrap-guard-single-print-undescribable-red", "bootstrap", "bootstrap", True, 0),
        ("live/run-helper-call-undescribable-cannot-evaluate", "main-run", "main", False, 2),
        ("revert/main-guard-single-print-run-undescribable-red", "main-run", "main", True, 0),
        ("live/self-test-helper-call-undescribable-cannot-evaluate", "main-self-test", "main", False, 2),
        ("revert/main-guard-single-print-self-test-undescribable-red", "main-self-test", "main", True, 0),
    ):
        found, output = undescribed[name, reverted]
        what = describing[site][0].split('"')[1]
        check(check_id, source.count(describing[site][0]) == 1 and found == rc and (reverted or (
            "CANNOT EVALUATE" in output and what + "\n  cause: Unprintable (its message cannot be read)\n" in output)),
              (found, output))

    # the same at the site that describes an input the gate cannot read, in this process: its read raising an
    # OSError whose __str__ raises a SystemExit(0), run() returns 2; reverted to the single print, the SystemExit
    # escapes run()
    class UnprintableError(OSError):
        def __str__(self):
            raise SystemExit(0)

    def unreadable(path, rel):
        raise UnprintableError()
    with patch.object(here, "_read_regular", unreadable), contextlib.redirect_stderr(io.StringIO()) as err:
        rc = run(ROOT)
    check("live/unreadable-input-undescribable-cannot-evaluate", source.count(describing["read"][0]) == 1 and rc == 2
          and "CANNOT EVALUATE: an input the gate needs cannot be read\n  cause: UnprintableError (its message "
          "cannot be read)\n" in err.getvalue(), (rc, err.getvalue()))
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        single = _copy_mutant(tmp, Path(__file__).resolve(), "opf_declaration_read_mutant", *describing["read"])
        with patch.object(single, "_read_regular", unreadable), contextlib.redirect_stderr(io.StringIO()):
            try:
                rc = single.run(ROOT)
            except SystemExit as exc:
                rc = "exit {!r}".format(exc.code)
    except AssertionError as exc:
        rc = 2, str(exc)
    finally:
        sys.modules.pop("opf_declaration_read_mutant", None)
        shutil.rmtree(tmp)
    check("revert/read-guard-single-print-undescribable-red", rc == "exit 0", rc)

    # and that site catches any other exception while the inputs are read, one the (CannotEvaluate,
    # UnicodeDecodeError, OSError) it once named does not: its read raising a RuntimeError, run() returns 2; with
    # the handler narrowed back to that tuple, the RuntimeError escapes run()
    read_guard = ("    except:   # a CannotEvaluate, a UnicodeDecodeError, an OSError, " "or any other exception while "
                  "the inputs are read\n        exc = sys.exception()\n"
                  "        if issubclass(type(exc), KeyboardInterrupt):\n            raise\n        "
                  + describing["read"][0] + "\n")
    read_narrowed = ("    except (CannotEvaluate, UnicodeDecodeError, OSError) as exc:\n        "
                     + describing["read"][0] + "\n")

    def failing_read(path, rel):
        raise RuntimeError("raised while the inputs are read")
    with patch.object(here, "_read_regular", failing_read), contextlib.redirect_stderr(io.StringIO()) as err:
        try:
            rc = run(ROOT)
        except RuntimeError as exc:
            rc = "raised {!r}".format(exc)
    check("live/unreadable-input-other-error-cannot-evaluate", source.count(read_guard) == 1 and rc == 2
          and "CANNOT EVALUATE: an input the gate needs cannot be read\n  cause: RuntimeError: raised while the "
          "inputs are read\n" in err.getvalue(), (rc, err.getvalue()))
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        tupled = _copy_mutant(tmp, Path(__file__).resolve(), "opf_declaration_read_narrowed_mutant", read_guard,
                              read_narrowed)
        with patch.object(tupled, "_read_regular", failing_read), contextlib.redirect_stderr(io.StringIO()):
            try:
                rc = tupled.run(ROOT)
            except RuntimeError as exc:
                rc = "raised {!r}".format(exc)
    except AssertionError as exc:
        rc = 2, str(exc)
    finally:
        sys.modules.pop("opf_declaration_read_narrowed_mutant", None)
        shutil.rmtree(tmp)
    check("revert/read-guard-narrowed-red", str(rc).startswith("raised RuntimeError"), rc)

    # a KeyboardInterrupt still propagates, at the tools import site and inside run() (at its helper load and from
    # a helper it calls), and so does one that a helper's exception raises from its __str__ while the guard in
    # main() describes it: the process ends by SIGINT, not exit 2; with the site's re-raise dropped it exits 2
    reraise = "\n        if issubclass(type(exc), KeyboardInterrupt):\n            raise\n"
    bind = "\n        exc = sys.exception()"   # how each bare guard takes what it caught, before its re-raise
    # the four steps of _cannot_evaluate that catch what printing or describing raises, each with its re-raise
    caught = {
        "print": "   # the exit status still carries the cannot-evaluate\n        error = sys.exception()\n"
                 "        if issubclass(type(error), KeyboardInterrupt):\n            raise\n",
        "message": "__name__, exc)\n    except:\n        error = sys.exception()\n"
                   "        if issubclass(type(error), KeyboardInterrupt):\n            raise\n",
        "type-name": "        except:\n            error = sys.exception()\n"
                     "            if issubclass(type(error), KeyboardInterrupt):\n                raise\n"
                     "            cause = None\n",
        "cause-print": "file=stream)\n        except:\n            error = sys.exception()\n"
                       "            if issubclass(type(error), KeyboardInterrupt):\n                raise\n    return 2\n",
    }

    def dropped(text):
        """`text` with its re-raise of a KeyboardInterrupt dropped."""
        return re.sub(r"( *)if issubclass\(type\(error\), KeyboardInterrupt\):\n *raise\n", r"\1pass\n", text)
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        interrupted = {}
        for site, rel, text, helpers, old, new in (
            ("tools", "tools/check_selftest_execution.py", "raise KeyboardInterrupt\n", stubs,
             "            raise _HELPER_ERROR" "   # here:", "            pass   # here:"),
            ("run", "opf/tools/_opf_store.py", "raise KeyboardInterrupt\n", stubs, guards["run"] + bind + reraise,
             guards["run"] + bind + "\n"),
            ("main", "opf/tools/_opf_declaration.py", "def parse_declaration(data):\n    raise KeyboardInterrupt\n",
             tree, main_guard, main_guard.replace(reraise, "\n")),
            ("describe", "opf/tools/_opf_declaration.py", "class Unsayable(Exception):\n    def __str__(self):\n"
             "        raise KeyboardInterrupt\n\n\ndef parse_declaration(data):\n    raise Unsayable()\n", tree,
             caught["message"], dropped(caught["message"])),
        ):
            for reverted in (False, True):
                interrupted[site, reverted] = (source.count(old) == 1,) + launch(
                    Path(tmp, "interrupt-{}-{}".format(site, int(reverted))), None, rel, text, [], None, helpers,
                    source.replace(old, new) if reverted else source)
    finally:
        shutil.rmtree(tmp)
    for check_id, site, reverted, rc in (
        ("live/tools-helper-keyboard-interrupt-propagates", "tools", False, -signal.SIGINT),
        ("revert/tools-helper-keyboard-interrupt-reraise-dropped-red", "tools", True, 2),
        ("live/opf-helper-keyboard-interrupt-propagates", "run", False, -signal.SIGINT),
        ("revert/load-guard-keyboard-interrupt-reraise-dropped-red", "run", True, 2),
        ("live/run-helper-call-keyboard-interrupt-propagates", "main", False, -signal.SIGINT),
        ("revert/main-guard-keyboard-interrupt-reraise-dropped-red", "main", True, 2),
        ("live/run-helper-call-undescribable-keyboard-interrupt-propagates", "describe", False, -signal.SIGINT),
        ("revert/cannot-evaluate-message-keyboard-interrupt-reraise-dropped-red", "describe", True, 2),
    ):
        anchored, found, output = interrupted[site, reverted]
        check(check_id, anchored and found == rc, (found, output))

    # whether a caught exception is a KeyboardInterrupt is told by its real type, never by its __class__, which the
    # exception defines: at each site that tells it, a helper's exception whose __class__ raises a SystemExit(0), or
    # claims KeyboardInterrupt, ends the gate with exit 2 and that site's fixed line; with the site's test back on
    # isinstance (a site inside run() or the self-test in a copy with the guard in main() removed as well, and every
    # site in a copy whose entry point calls main() bare) the first exits 0 on that SystemExit and the second is
    # re-raised, exiting 1 with a traceback
    class_raises = ("class ClassRaises(Exception):\n    @property\n    def __class__(self):\n"
                    "        raise SystemExit(0)\n\n\n")
    class_claims = "class ClaimsInterrupt(Exception):\n    __class__ = KeyboardInterrupt\n\n\n"
    told = {
        "tools": "    if _HELPER_ERROR is not None:\n"
                 "        if issubclass(type(_HELPER_ERROR), KeyboardInterrupt):\n",
        "run": guards["run"] + bind + reraise,
        "self-test": guards["self-test"] + bind + reraise,
        "bootstrap": "except:   # a helper opf.py's _bootstrap() " "imports raises past its own arms" + bind + reraise,
        "main": main_guard,
    }

    def on_isinstance(text):
        """`text` with each real-type KeyboardInterrupt test in it back on isinstance."""
        return re.sub(r"issubclass\(type\((\w+)\), KeyboardInterrupt\)", r"isinstance(\1, KeyboardInterrupt)", text)
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        lying = {}
        for site, rel, raising, args, helpers in (
            ("tools", "tools/check_selftest_execution.py", "raise {}\n", [], stubs),
            ("run", "opf/tools/_opf_store.py", "raise {}\n", [], stubs),
            ("self-test", "opf/tools/opf.py", "raise {}\n", ["--self-test"], stubs),
            ("bootstrap", "opf/tools/_opf_worklog.py", "raise {}\n", ["--self-test"], with_opf),
            ("main", "opf/tools/_opf_declaration.py", "def parse_declaration(data):\n    raise {}\n", [], tree),
        ):
            gate = (called if site in ("tools", "main") else removed_main).replace(told[site], on_isinstance(told[site]))
            for kind, text in (("raises", class_raises + raising.format("ClassRaises('its __class__ raises')")),
                               ("claims", class_claims + raising.format("ClaimsInterrupt('not an interrupt')"))):
                for reverted in (False, True):
                    lying[site, kind, reverted] = (source.count(told[site]) == 1,) + launch(
                        Path(tmp, "lying-{}-{}-{}".format(site, kind, int(reverted))), None, rel, text, args, None,
                        helpers, gate if reverted else source)
    finally:
        shutil.rmtree(tmp)
    for check_id, site, kind, reverted, rc in (
        ("live/tools-helper-class-raises-system-exit-0-cannot-evaluate", "tools", "raises", False, 2),
        ("revert/tools-helper-isinstance-class-raises-system-exit-0-red", "tools", "raises", True, 0),
        ("live/tools-helper-class-claims-keyboard-interrupt-cannot-evaluate", "tools", "claims", False, 2),
        ("revert/tools-helper-isinstance-class-claims-keyboard-interrupt-red", "tools", "claims", True, 1),
        ("live/opf-helper-class-raises-system-exit-0-cannot-evaluate", "run", "raises", False, 2),
        ("revert/load-guard-isinstance-class-raises-system-exit-0-red", "run", "raises", True, 0),
        ("live/opf-helper-class-claims-keyboard-interrupt-cannot-evaluate", "run", "claims", False, 2),
        ("revert/load-guard-isinstance-class-claims-keyboard-interrupt-red", "run", "claims", True, 1),
        ("live/self-test-helper-class-raises-system-exit-0-cannot-evaluate", "self-test", "raises", False, 2),
        ("revert/self-test-load-guard-isinstance-class-raises-system-exit-0-red", "self-test", "raises", True, 0),
        ("live/self-test-helper-class-claims-keyboard-interrupt-cannot-evaluate", "self-test", "claims", False, 2),
        ("revert/self-test-load-guard-isinstance-class-claims-keyboard-interrupt-red", "self-test", "claims", True, 1),
        ("live/self-test-bootstrap-helper-class-raises-system-exit-0-cannot-evaluate", "bootstrap", "raises", False,
         2),
        ("revert/self-test-bootstrap-guard-isinstance-class-raises-system-exit-0-red", "bootstrap", "raises", True,
         0),
        ("live/self-test-bootstrap-helper-class-claims-keyboard-interrupt-cannot-evaluate", "bootstrap", "claims",
         False, 2),
        ("revert/self-test-bootstrap-guard-isinstance-class-claims-keyboard-interrupt-red", "bootstrap", "claims",
         True, 1),
        ("live/run-helper-call-class-raises-system-exit-0-cannot-evaluate", "main", "raises", False, 2),
        ("revert/main-guard-isinstance-class-raises-system-exit-0-red", "main", "raises", True, 0),
        ("live/run-helper-call-class-claims-keyboard-interrupt-cannot-evaluate", "main", "claims", False, 2),
        ("revert/main-guard-isinstance-class-claims-keyboard-interrupt-red", "main", "claims", True, 1),
    ):
        anchored, found, output = lying[site, kind, reverted]
        cause = "ClassRaises: its __class__ raises" if kind == "raises" else "ClaimsInterrupt: not an interrupt"
        if reverted:
            shown = "CANNOT EVALUATE" not in output and (kind == "raises" or "Traceback" in output and cause in output)
        else:
            shown = describing[site][0].split('"')[1] + "\n  cause: " + cause + "\n" in output
        check(check_id, anchored and found == rc and shown, (found, output))

    # the same at each step of _cannot_evaluate that catches what printing or describing raises (the fixed line's
    # print, the message, the type name it falls back to and the cause line's print), in this process: there a
    # KeyboardInterrupt (a subclass of it here) propagates, and an error whose __class__ raises a SystemExit(0) or
    # claims KeyboardInterrupt is caught, so it returns 2; with the step's re-raise dropped the KeyboardInterrupt
    # returns 2, and with its test back on isinstance the SystemExit or the claiming error escapes
    class Interrupt(KeyboardInterrupt):
        pass

    class ClassRaises(Exception):
        @property
        def __class__(self):
            raise SystemExit(0)

    class ClaimsInterrupt(Exception):
        __class__ = KeyboardInterrupt

    class Unsayable(Exception):
        """An exception whose message raises `error`."""
        def __init__(self, error):
            super().__init__()
            self.error = error

        def __str__(self):
            raise self.error

    class Failing(io.StringIO):
        """A stream whose write of a text starting with `prefix` raises `error`."""
        def __init__(self, prefix, error):
            super().__init__()
            self.prefix, self.error = prefix, error

        def write(self, text):
            if text.startswith(self.prefix):
                raise self.error
            return super().write(text)

    def nameless(error):
        """An exception whose type name raises a RuntimeError when first read and `error` when read again."""
        reads = []

        class Unnamed(type):
            @property
            def __name__(cls):
                reads.append(cls)
                raise error if len(reads) > 1 else RuntimeError("the type name cannot be read")
        return Unnamed("Nameless", (Exception,), {})()
    raising = {
        "print": lambda error: (RuntimeError("plain"), Failing("opf-declaration: CANNOT EVALUATE: ", error)),
        "message": lambda error: (Unsayable(error), io.StringIO()),
        "type-name": lambda error: (nameless(error), io.StringIO()),
        "cause-print": lambda error: (RuntimeError("plain"), Failing("  cause: ", error)),
    }
    errors = {"interrupt": Interrupt, "raises": ClassRaises, "claims": ClaimsInterrupt}

    def outcome(cannot_evaluate, step, kind):
        """What `cannot_evaluate` does when its `step` raises an error of `kind`: the value it returns, or the type
        name of what escapes it."""
        exc, stream = raising[step](errors[kind]("raised while a cannot-evaluate is printed or described"))
        with contextlib.redirect_stderr(stream):
            try:
                return cannot_evaluate("an input the gate needs cannot be read", exc)
            except (Interrupt, SystemExit, ClaimsInterrupt) as escaped:
                return type(escaped).__name__
    steps = {}
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        for number, step in enumerate(caught):
            for kind in errors:
                steps[step, kind, None] = outcome(_cannot_evaluate, step, kind)
            for revert, new in (("dropped", dropped(caught[step])), ("isinstance", on_isinstance(caught[step]))):
                name = "opf_declaration_step_mutant_{}_{}".format(number, revert)
                try:
                    mutant = _copy_mutant(tmp, Path(__file__).resolve(), name, caught[step], new)
                    for kind in errors:
                        steps[step, kind, revert] = outcome(mutant._cannot_evaluate, step, kind)
                except AssertionError as exc:
                    for kind in errors:
                        steps[step, kind, revert] = str(exc)
                finally:
                    sys.modules.pop(name, None)
    finally:
        shutil.rmtree(tmp)
    for check_id, kind, revert, expected in (
        ("live/cannot-evaluate-keyboard-interrupt-propagates", "interrupt", None, "Interrupt"),
        ("revert/cannot-evaluate-keyboard-interrupt-reraise-dropped-red", "interrupt", "dropped", 2),
        ("live/cannot-evaluate-class-raises-system-exit-0-returns-2", "raises", None, 2),
        ("revert/cannot-evaluate-isinstance-class-raises-system-exit-0-red", "raises", "isinstance", "SystemExit"),
        ("live/cannot-evaluate-class-claims-keyboard-interrupt-returns-2", "claims", None, 2),
        ("revert/cannot-evaluate-isinstance-class-claims-keyboard-interrupt-red", "claims", "isinstance",
         "ClaimsInterrupt"),
    ):
        found = [steps[step, kind, revert] for step in caught]
        check(check_id, found == [expected] * len(caught), found)

    # an exception whose MRO leaves out BaseException (its metaclass's mro() returns (cls, SystemExit, object), or
    # (cls, object)), which no except BaseException clause matches, is caught at each of these sites too, in this
    # process: the steps of _cannot_evaluate that print its fixed line, read the type name it falls back to and
    # print the cause line, the helper load guards of run() and the self-test, run()'s read guard, the self-test's
    # bootstrap guard, and _entry_cannot_evaluate's print of the entry point's fixed line; raised there, it is caught
    # and the call returns 2 (a load, read or bootstrap guard after its fixed line), and with that one site back on
    # except BaseException it escapes the call (the message step, the guard in main(), the tools-side import and the
    # entry point's guard are shown below, each in a launched copy)
    def forged_error(after):
        """An exception whose metaclass's mro() returns the class itself, then `after`."""
        class Meta(type):
            def mro(cls):
                return (cls,) + after

        class Forged(SystemExit, metaclass=Meta):
            pass
        return Forged("raised by an exception whose MRO leaves out BaseException")

    class Unbootstrappable:
        """An opf.py whose _bootstrap() raises `error`."""
        def __init__(self, error):
            self.error = error

        def _bootstrap(self):
            raise self.error

    def reach(module, site, error):
        """Raise `error` at `site` of `module` (this gate or a copy of it): (what the call returns, or the type name
        of what escapes it whatever its MRO, and what it printed)."""
        def failing_load():
            raise error
        replaced = {"run": {"_load": failing_load}, "self-test": {"_load": failing_load},
                    "read": {"_load": lambda: None, "_read_regular": lambda path, rel: failing_load()},
                    "bootstrap": {"_load": lambda: None, "_load_opf": lambda: Unbootstrappable(error)}}.get(site, {})
        exc, stream = raising[site](error) if site in caught else (None, io.StringIO())
        if site == "entry":   # the entry point's fixed line goes to a stream whose write of it raises `error`
            stream = raising["print"](error)[1]
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()) as out:
            for attribute, value in replaced.items():
                stack.enter_context(patch.object(module, attribute, value))
            stack.enter_context(contextlib.redirect_stderr(stream))
            try:
                if site in caught:
                    found = module._cannot_evaluate("an input the gate needs cannot be read", exc)
                elif site == "entry":
                    found = module._entry_cannot_evaluate("an exception escaped main()")
                else:
                    found = module.run(ROOT) if site in ("run", "read") else module.self_test()
            except:   # what escapes the call, whatever its MRO
                escaped = sys.exception()
                if issubclass(type(escaped), KeyboardInterrupt):
                    raise
                found = type(escaped).__name__
        return found, out.getvalue() + stream.getvalue()
    inner = {
        "print": "    except:" + caught["print"],
        "type-name": caught["type-name"],
        "cause-print": caught["cause-print"],
        "run": guards["run"] + bind + reraise,
        "self-test": guards["self-test"] + bind + reraise,
        "read": read_guard,
        "bootstrap": told["bootstrap"],
        "entry": ("    except:   # the exit status still carries the cannot-evaluate\n"
                  "        if issubclass(type(sys.exception()), KeyboardInterrupt):\n"),
    }
    mros = ((SystemExit, object), (object,))
    forged_inner = {}
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        for number, (site, old) in enumerate(inner.items()):
            forged_inner[site, False] = [reach(here, site, forged_error(after)) for after in mros]
            name = "opf_declaration_forged_mutant_{}".format(number)
            try:
                mutant = _copy_mutant(tmp, Path(__file__).resolve(), name, old, old.replace(
                    "except:", "except BaseException as {}:".format("error" if site in caught else "exc"), 1))
                forged_inner[site, True] = [reach(mutant, site, forged_error(after)) for after in mros]
            except AssertionError as exc:
                forged_inner[site, True] = str(exc)
            finally:
                sys.modules.pop(name, None)
    finally:
        shutil.rmtree(tmp)
    for check_id, site, reverted in (
        ("live/cannot-evaluate-print-forged-mro-returns-2", "print", False),
        ("revert/cannot-evaluate-print-base-exception-forged-mro-red", "print", True),
        ("live/cannot-evaluate-type-name-forged-mro-returns-2", "type-name", False),
        ("revert/cannot-evaluate-type-name-base-exception-forged-mro-red", "type-name", True),
        ("live/cannot-evaluate-cause-print-forged-mro-returns-2", "cause-print", False),
        ("revert/cannot-evaluate-cause-print-base-exception-forged-mro-red", "cause-print", True),
        ("live/opf-helper-forged-mro-cannot-evaluate", "run", False),
        ("revert/load-guard-base-exception-forged-mro-red", "run", True),
        ("live/self-test-helper-forged-mro-cannot-evaluate", "self-test", False),
        ("revert/self-test-load-guard-base-exception-forged-mro-red", "self-test", True),
        ("live/unreadable-input-forged-mro-cannot-evaluate", "read", False),
        ("revert/read-guard-base-exception-forged-mro-red", "read", True),
        ("live/self-test-bootstrap-helper-forged-mro-cannot-evaluate", "bootstrap", False),
        ("revert/self-test-bootstrap-guard-base-exception-forged-mro-red", "bootstrap", True),
        ("live/entry-cannot-evaluate-print-forged-mro-returns-2", "entry", False),
        ("revert/entry-cannot-evaluate-print-base-exception-forged-mro-red", "entry", True),
    ):
        found = forged_inner[site, reverted]   # a list per MRO form, or why the site's revert anchor is not found
        check(check_id, type(found) is list and len(found) == len(mros) and all(
            rc == "Forged" if reverted else rc == 2 and (
                site in caught or site == "entry" or describing[site][0].split('"')[1] + "\n  cause: Forged" in output)
            for rc, output in found), found)

    # an exception whose MRO leaves out BaseException (its metaclass's mro() returns (cls, SystemExit, object), or
    # (cls, object)), which no except BaseException clause matches, is caught all the same: raised by a helper call
    # in run(), at the tools-side import, from the __str__ of a helper's exception that the guard in main()
    # describes, or by main() itself, it ends the gate with exit 2 and that site's line; with the site back on
    # except BaseException (a site other than the entry point in a copy whose entry point calls main() bare) the
    # first form ends the gate with its SystemExit code, 0, and the second with a traceback's exit 1
    forged = ("class Meta(type):\n    def mro(cls):\n        return {}\n\n\n"
              "class Forged(SystemExit, metaclass=Meta):\n    pass\n\n\n")
    head = "def main(argv=None):\n"
    entry_guard = "    try:\n        status = main()\n    except:\n"
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        mro_forged = {}
        for site, rel, text, helpers, old, new, raising in (
            ("run", "opf/tools/_opf_declaration.py", "def parse_declaration(data):\n    raise Forged(0)\n", tree,
             main_guard, main_guard.replace("except:", "except BaseException as exc:", 1), ""),
            ("tools", "tools/check_selftest_execution.py", "raise Forged(0)\n", stubs, unbound, "", ""),
            ("describe", "opf/tools/_opf_declaration.py", "class Unprintable(Exception):\n    def __str__(self):\n"
             "        raise Forged(0)\n\n\ndef parse_declaration(data):\n    raise Unprintable()\n", tree,
             caught["message"], caught["message"].replace("except:", "except BaseException as error:", 1), ""),
            ("entry", "opf/tools/_opf_declaration.py", "", tree, entry_guard,
             entry_guard.replace("except:", "except BaseException:"), "    _load()\n    raise _opf_declaration.Forged(0)\n"),
        ):
            for kind, mro in (("system-exit", "(cls, SystemExit, object)"), ("object", "(cls, object)")):
                for reverted in (False, True):
                    gate = (source if site == "entry" else called).replace(old, new) if reverted else source
                    mro_forged[site, kind, reverted] = (source.count(old) == 1,) + launch(
                        Path(tmp, "forged-{}-{}-{}".format(site, kind, int(reverted))), None, rel,
                        forged.format(mro) + text, [], None, helpers, gate.replace(head, head + raising))
    finally:
        shutil.rmtree(tmp)
    lines = {"run": "CANNOT EVALUATE: an exception escaped the gate body\n  cause: Forged",
             "tools": "CANNOT EVALUATE: a helper module cannot be loaded\n  cause: ImportError: a tools-side helper's "
                      "import raised an exception whose MRO leaves out BaseException",
             "describe": "CANNOT EVALUATE: an exception escaped the gate body\n  cause: Unprintable (its message "
                         "cannot be read)\n",
             "entry": "opf-declaration: CANNOT EVALUATE: an exception escaped main()\n"}
    for check_id, site, kind, reverted, rc in (
        ("live/run-helper-call-forged-system-exit-mro-cannot-evaluate", "run", "system-exit", False, 2),
        ("revert/main-guard-base-exception-forged-system-exit-mro-red", "run", "system-exit", True, 0),
        ("live/run-helper-call-forged-object-mro-cannot-evaluate", "run", "object", False, 2),
        ("revert/main-guard-base-exception-forged-object-mro-red", "run", "object", True, 1),
        ("live/tools-helper-forged-system-exit-mro-cannot-evaluate", "tools", "system-exit", False, 2),
        ("revert/tools-helper-guard-base-exception-forged-system-exit-mro-red", "tools", "system-exit", True, 0),
        ("live/tools-helper-forged-object-mro-cannot-evaluate", "tools", "object", False, 2),
        ("revert/tools-helper-guard-base-exception-forged-object-mro-red", "tools", "object", True, 1),
        ("live/run-helper-call-undescribable-forged-system-exit-mro-cannot-evaluate", "describe", "system-exit",
         False, 2),
        ("revert/cannot-evaluate-message-base-exception-forged-system-exit-mro-red", "describe", "system-exit",
         True, 0),
        ("live/run-helper-call-undescribable-forged-object-mro-cannot-evaluate", "describe", "object", False, 2),
        ("revert/cannot-evaluate-message-base-exception-forged-object-mro-red", "describe", "object", True, 1),
        ("live/main-forged-system-exit-mro-cannot-evaluate", "entry", "system-exit", False, 2),
        ("revert/entry-guard-base-exception-forged-system-exit-mro-red", "entry", "system-exit", True, 0),
        ("live/main-forged-object-mro-cannot-evaluate", "entry", "object", False, 2),
        ("revert/entry-guard-base-exception-forged-object-mro-red", "entry", "object", True, 1),
    ):
        anchored, found, output = mro_forged[site, kind, reverted]
        check(check_id, anchored and found == rc and (
            ("Traceback" in output or rc == 0) if reverted else lines[site] in output), (found, output))

    # and so does main() returning anything but exactly the int 0, 1 or 2: a copy of this gate whose main() returns
    # None, or the int 3, exits 2 with the entry point's fixed line; with its entry point calling main() bare it
    # exits 0 on the None and 3 on the 3
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        statusless = {}
        for value in ("None", "3"):
            for reverted in (False, True):
                statusless[value, reverted] = launch(
                    Path(tmp, "statusless-{}-{}".format(value, int(reverted))), None, "opf/tools/_opf_declaration.py",
                    "", [], None, tree, (called if reverted else source).replace(head, head + "    return " + value + "\n"))
    finally:
        shutil.rmtree(tmp)
    for check_id, value, reverted, rc in (
        ("live/main-non-int-return-cannot-evaluate", "None", False, 2),
        ("revert/entry-point-bare-call-non-int-return-red", "None", True, 0),
        ("live/main-out-of-range-int-return-cannot-evaluate", "3", False, 2),
        ("revert/entry-point-bare-call-out-of-range-int-return-red", "3", True, 3),
    ):
        found, output = statusless[value, reverted]
        check(check_id, source.count(head) == 1 and called != source and found == rc and (
            reverted or "opf-declaration: CANNOT EVALUATE: main() returned no exit status\n" in output), (found, output))

    # and an armed exit_with() that returns instead of ending the process ends the gate with exit 2 and the entry
    # point's fixed line even where main() returned 0: a copy of this gate whose self-test returns 0 once arm() has
    # returned, launched with --execution-report beside a stub _selftest_exit_report whose arm() and exit_with()
    # return, exits 2; with the entry point's last line dropped it exits 0
    fallback = ('    raise SystemExit(_entry_cannot_evaluate("exit_with() '
                'returned instead of ending the process"))\n')
    record = "        _REPORT_ARMED = True\n"   # the self-test's record that arm() returned
    armed = record + "        return 0\n"
    stub_arm = "def arm(report_path, suite_id, executed):\n    return None\n\n\n"
    returning = dict(tree, **{"tools/_selftest_exit_report.py": stub_arm + "def exit_with(code):\n    return None\n"})
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        report = ["--execution-report", str(Path(tmp, "report.json"))]
        unended = [launch(Path(tmp, "unended-{}".format(int(reverted))), None, "opf/tools/_opf_declaration.py", "",
                          report, None, returning, (source.replace(fallback, "") if reverted else source).replace(
                              record, armed)) for reverted in (False, True)]
    finally:
        shutil.rmtree(tmp)
    check("live/exit-with-returning-cannot-evaluate", source.count(fallback) == source.count(record) == 1
          and unended[0][0] == 2
          and "opf-declaration: CANNOT EVALUATE: exit_with() returned instead of ending the process\n" in unended[0][1],
          unended[0])
    check("revert/exit-with-fallback-dropped-red", unended[1][0] == 0, unended[1])

    # and the entry point raises its own exits rather than calling sys.exit, which loaded code can replace with a
    # function that returns: a copy of this gate beside a tools-side helper whose import replaces sys.exit with a no-op
    # exits 2, where that helper's import then fails (the entry point raises main()'s 2 itself, no report being
    # armed) and where an armed exit_with() then returns (it raises the 2 of its fixed line itself); with the first
    # exit back on sys.exit the gate falls through to exit_with() on None and exits 1 on the traceback, and with the
    # second it exits 0
    no_op_exit = "import sys\nsys.exit = lambda *args: None\n"
    direct = "    if not _REPORT_ARMED:\n        raise SystemExit(" "status)\n"
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        report = ["--execution-report", str(Path(tmp, "report.json"))]
        replaced_exit = {}
        for kind, rel, text, helpers, old, args in (
            ("import", "tools/check_selftest_execution.py", no_op_exit + raises, stubs, direct, []),
            ("returning", "tools/_selftest_exit_report.py",
             no_op_exit + "\n\n" + stub_arm + "def exit_with(code):\n    return None\n", tree, fallback, report),
        ):
            for reverted in (False, True):
                gate = source.replace(old, old.replace("raise SystemExit(", "sys.exit(")) if reverted else source
                replaced_exit[kind, reverted] = (source.count(old) == 1,) + launch(
                    Path(tmp, "replaced-exit-{}-{}".format(kind, int(reverted))), None, rel, text, args, None,
                    helpers, gate.replace(record, armed))
    finally:
        shutil.rmtree(tmp)
    for check_id, kind, reverted, rc, shown in (
        ("live/tools-helper-replaced-sys-exit-cannot-evaluate", "import", False, 2,
         "opf-declaration: CANNOT EVALUATE: a helper module cannot be loaded\n  cause: RuntimeError: broken at "
         "import\n"),
        ("revert/entry-point-sys-exit-call-red", "import", True, 1,
         "AttributeError: 'NoneType' object has no attribute"),
        ("live/exit-with-returning-replaced-sys-exit-cannot-evaluate", "returning", False, 2,
         "opf-declaration: CANNOT EVALUATE: exit_with() returned instead of ending the process\n"),
        ("revert/exit-with-fallback-sys-exit-call-red", "returning", True, 0, ""),
    ):
        anchored, found, output = replaced_exit[kind, reverted]
        check(check_id, anchored and found == rc and shown in output and (reverted or "Traceback" not in output),
              (found, output))

    # the execution-report harness refusing a --execution-report run it did not launch is labelled as that
    # refusal, not as an escape: a copy of this gate beside the real tools/_selftest_exit_report.py, launched
    # directly, exits 2 with the harness error and the refusal; with arm() moved out of its try the same run is
    # labelled an escape
    arm_call = "        try:\n            _selftest_exit_report.arm(report_path, SUITE_ID, executed)\n        except"
    arm_outside = ("        _selftest_exit_report.arm(report_path, SUITE_ID, executed)\n        try:\n"
                   "            pass\n        except")
    with_harness = dict(stubs, **{"tools/_selftest_exit_report.py": (ROOT / "tools" / "_selftest_exit_report.py")
                                  .read_text(encoding="utf-8")})
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        refused = [launch(Path(tmp, "refusal-{}".format(int(reverted))), None, "tools/check_selftest_execution.py", "",
                          ["--execution-report", str(Path(tmp, "report-{}.json".format(int(reverted))))], None,
                          with_harness, source.replace(arm_call, arm_outside) if reverted else source)
                   for reverted in (False, True)]
    finally:
        shutil.rmtree(tmp)
    check("live/harness-refusal-labelled", source.count(arm_call) == 1 and refused[0][0] == 2
          and "SELF-TEST HARNESS ERROR" in refused[0][1] and "harness refused the run" in refused[0][1]
          and "escaped the gate body" not in refused[0][1], refused[0])
    check("revert/harness-refusal-arm-outside-try-red", refused[1][0] == 2
          and "escaped the gate body\n  cause: SystemExit: 2" in refused[1][1], refused[1])

    # and where no report is armed (a plain run or a plain --self-test) the entry point never calls exit_with(), which
    # exits through sys.exit there: a copy of this gate beside the real tools/_selftest_exit_report.py and a
    # tools-side helper whose import rebinds sys.exit to a function that calls the real sys.exit(0), in a tree with no
    # declaration file, exits 2 with its cannot-evaluate; with the entry point calling exit_with() on that unarmed
    # path it exits 0
    to_zero = "import sys\n\n\ndef _exit(code=None, *, _real=sys.exit):\n    _real(0)\n\n\nsys.exit = _exit\n"
    unarmed = "    if not _REPORT_ARMED:\n"
    tmp = tempfile.mkdtemp(prefix="opf-declaration-selftest-")
    try:
        unarmed_runs = [launch(Path(tmp, "unarmed-{}".format(int(reverted))), None, "tools/check_selftest_execution.py",
                               to_zero, [], None, with_harness, source.replace(
                                   unarmed, "    if _selftest_exit_report is None:\n") if reverted else source)
                        for reverted in (False, True)]
    finally:
        shutil.rmtree(tmp)
    check("live/unarmed-replaced-sys-exit-0-cannot-evaluate", source.count(unarmed) == 1 and unarmed_runs[0][0] == 2
          and "opf-declaration: CANNOT EVALUATE: an input the gate needs cannot be read\n" in unarmed_runs[0][1]
          and "Traceback" not in unarmed_runs[0][1], unarmed_runs[0])
    check("revert/entry-point-unarmed-exit-with-call-red", unarmed_runs[1][0] == 0, unarmed_runs[1])
    check("live/real-tree-passes", run(ROOT) == 0)

    # the in-run self-guard: the executed set is exactly this suite's registered set
    expected, problem = _registered_check_ids()
    harness = []
    if problem is not None:
        harness.append(("execution-set/registry", problem))
    elif len(set(executed)) != len(executed) or set(executed) != expected:
        harness.append(("execution-set/reconciled", dict(missing=sorted(expected - set(executed)),
                                                         extra=sorted(set(executed) - expected))))
    failed = [(check_id, detail) for check_id, ok, detail in results if not ok] + harness
    if failed:
        print("SELF-TEST FAIL: opf-declaration {} of {} check(s) failed".format(len(failed), len(results)))
        for check_id, detail in failed:
            print("  {}: {!r}".format(check_id, detail))
        return 1
    print("SELF-TEST PASS: opf-declaration {} checks ({} runtime vectors and {} dispatcher vectors, each red "
          "under its revert; the documentation and file rules red on a changed, omitted or added field, a byte "
          "variant, a missing or commented-out restatement or digest location, a Markdown comment behind a fence "
          "or HTML block the reader cannot place, a surface dropped from the list, a CRLF file and a release "
          "other than VERSION; a missing or broken helper module, tools-side or opf-side, a cannot-evaluate)".format(
              len(results), len(covered), len(cli_covered)))
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if _HELPER_ERROR is not None:
        if issubclass(type(_HELPER_ERROR), KeyboardInterrupt):
            raise _HELPER_ERROR   # here: tools/check_python_floor.py admits no raise in an import fallback
        return _cannot_evaluate("a helper module cannot be loaded", _HELPER_ERROR)
    try:
        if (len(args) in (2, 3) and args[-2] == "--execution-report" and os.path.isabs(args[-1])
                and args[:-2] in ([], ["--self-test"])):
            return self_test(args[-1])
        if args == ["--self-test"]:
            return self_test()
        if not args:
            return run(ROOT)
    except:   # anything that escapes the gate body, a helper's SystemExit included
        exc = sys.exception()
        if issubclass(type(exc), KeyboardInterrupt):
            raise
        return _cannot_evaluate("an exception escaped the gate body", exc)
    print("usage: check_opf_declaration.py [--self-test | [--self-test] --execution-report ABS_PATH]",
          file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. Inline, not imported, because the self-test runs
# copies of this file alone in scratch trees. The self-test launches children and reads their exit status, so
# under an ignored SIGCHLD a failed child would read as a success; _entry() runs it first instead, before main().
# The status the probe child exits with; any value other than 0 and the refusal exit would do.
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


def _entry_cannot_evaluate(what):
    """Print the entry point's fixed cannot-evaluate line for `what`, best effort, reading nothing from any
    exception, and return 2; a KeyboardInterrupt raised while it is printed, told by its real type, propagates."""
    try:
        print("opf-declaration: CANNOT EVALUATE: " + what, file=sys.stderr)
    except:   # the exit status still carries the cannot-evaluate
        if issubclass(type(sys.exception()), KeyboardInterrupt):
            raise
    return 2


def _entry():
    # exit 0 (or 1) only by an explicit normal return: the status is main()'s only when it returns exactly the
    # int 0, 1 or 2; any other return value, and any exception that leaves main() whatever its type or MRO (the
    # bare except catches one whose MRO leaves out BaseException too), ends the gate with exit 2, but for a
    # KeyboardInterrupt by its real type, which propagates. Unarmed (a plain run or a plain --self-test), exit_with is
    # sys.exit, which loaded code can replace with a function that exits with another status, so the entry point
    # raises the exit itself there and calls exit_with only once arm() has returned (_REPORT_ARMED), where exit_with
    # raises the exit itself (the module docstring states what loaded code can still do). The child-status guard runs
    # first: where a child's exit status cannot be read it writes its refusal and exits 2 (os._exit) before main()
    # runs, and otherwise returns 0 and changes nothing
    _child_status_refusal_exit("check_opf_declaration.py")
    try:
        status = main()
    except:
        if issubclass(type(sys.exception()), KeyboardInterrupt):
            raise
        status = _entry_cannot_evaluate("an exception escaped main()")
    else:
        if type(status) is not int or status not in (0, 1, 2):
            status = _entry_cannot_evaluate("main() returned no exit status")
    if not _REPORT_ARMED:
        raise SystemExit(status)
    _selftest_exit_report.exit_with(status)   # armed: raises the exit itself
    raise SystemExit(_entry_cannot_evaluate("exit_with() returned instead of ending the process"))


if __name__ == "__main__":
    _entry()
