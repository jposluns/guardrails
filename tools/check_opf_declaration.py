#!/usr/bin/env python3
"""Release gate for the OPF reference tooling's implementation declaration (OPF-SPEC section 16.1).

  check_opf_declaration.py                        check this tree (exit 0 clean, 1 finding, 2 cannot evaluate)
  check_opf_declaration.py --self-test [--execution-report ABS_PATH]

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
  6. every Markdown or HTML document of the tree (the .git directory aside) with a line that opens a
     restatement block is a DOC_SURFACES entry, so a surface dropped from that list is a finding.

A restatement is the block of an HTML surface between a line `<div class="tree"><pre id="opf-declaration">`
and the next line `</pre></div>`, and of a Markdown surface between a line "```toml opf-declaration" and the
next line "```" at the same indentation (that indentation is removed from each line of the block). A block
whose opening line lies inside an HTML comment (after a `<!--` with no `-->` after it, the comment form of both
HTML and Markdown; in Markdown a marker inside a fenced code block or a one-line code span is literal text and
does not count) is no restatement, so a surface whose only block is commented out is a finding for having
none. The comment is found lexically: a marker elsewhere that is not a comment (an HTML attribute value or
script, an indented Markdown code block) counts and refuses the block, and a block a reader cannot see for
another reason (a hidden element, a style, or a Markdown fence around it) is not detected.

A helper module that cannot be imported is a cannot-evaluate (exit 2), never a finding.

The self-test runs every vector of opf/tools/_opf_declaration.py (the runtime check and the emitter) and every
spec 16.1 vector of opf/tools/opf.py (the gate before dispatch on each refusing state, the ungated verbs,
`--declaration` and the doctor report) under its own registered check id, shows each red under its revert (a
mutant of _opf_declaration.py or opf.py loaded from a scratch directory, or a patched attribute of opf.py), and
runs this gate's own document vectors. Its check ids are registered in tools/selftest_checks.toml (suite
opf-declaration-selftest) and reconciled by tools/check_selftest_execution.py.
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
import stat
import tempfile
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402  (the execution report, finalized at interpreter exit)
import check_selftest_execution  # noqa: E402  (the authoritative registry schema)

ROOT = Path(__file__).resolve().parents[1]
OPF_TOOLS = ROOT / "opf" / "tools"
# The opf/tools helpers are imported by _load(), after the self-test arms its execution report: the report's
# finalizer must be the process's first atexit registration, and importing them registers others.
_opf_declaration = None
_opf_store = None

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
DOC_SUFFIXES = (".md", ".html")
MAX_READ_BYTES = 1 << 20


class CannotEvaluate(Exception):
    """An input the gate needs cannot be read."""


def _load():
    """Bind the opf/tools helpers this gate reads (_opf_declaration and _opf_store)."""
    global _opf_declaration, _opf_store
    if str(OPF_TOOLS) not in sys.path:
        sys.path.insert(0, str(OPF_TOOLS))
    import _opf_declaration
    import _opf_store


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


def _in_comment(before):
    """True where text ending at `before` leaves an HTML comment open (a `<!--` with no `-->` after it)."""
    return before.rfind(_COMMENT_OPEN) > before.rfind(_COMMENT_CLOSE)


def _comment_text(rel, lines):
    """The text of `lines` in which a comment can open or close: for a Markdown surface, without its fenced code
    blocks and its inline code spans on one line, where a comment marker is literal text."""
    if rel.endswith(".html"):
        return "\n".join(lines)
    kept = []
    fence = None
    for line in lines:
        stripped = line.strip()
        # a fence opens on three or more backticks or tildes; a backtick in the rest of the line opens none
        if fence is None and stripped[:3] in ("```", "~~~") and "`" not in stripped.lstrip(stripped[0]):
            fence = stripped[:len(stripped) - len(stripped.lstrip(stripped[0]))]
        elif fence is not None:
            if set(stripped) == {fence[0]} and len(stripped) >= len(fence):
                fence = None
        else:
            kept.append(_CODE_SPAN.sub("", line))
    return "\n".join(kept)


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
    if _in_comment(_comment_text(rel, lines[:start])):
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
        data = _read_regular(Path(root) / DECLARATION_REL, DECLARATION_REL)
        version = _read_regular(Path(root) / VERSION_REL, VERSION_REL).decode("utf-8")
        surfaces = dict((rel, _read_regular(Path(root) / rel, rel).decode("utf-8")) for rel in DOC_SURFACES)
        documents = restating_documents(root)
    except (CannotEvaluate, UnicodeDecodeError) as exc:
        print("opf-declaration: CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    except (ImportError, OSError) as exc:   # a helper _load() imports is missing or cannot be read
        print("opf-declaration: CANNOT EVALUATE: a helper module cannot be loaded ({}: {})".format(
            type(exc).__name__, exc), file=sys.stderr)
        return 2
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
    results = []
    executed = []
    if report_path is not None:
        # The execution report is finalized at interpreter exit (tools/_selftest_exit_report.py).
        _selftest_exit_report.arm(report_path, SUITE_ID, executed)

    def check(check_id, ok, detail=""):
        results.append((check_id, bool(ok), detail))
        executed.append(check_id)

    try:
        _load()
    except (ImportError, OSError) as exc:
        print("SELF-TEST CANNOT EVALUATE: opf-declaration: a helper module cannot be loaded ({}: {})".format(
            type(exc).__name__, exc))
        return 2
    from unittest.mock import patch   # after arm: it imports logging, whose import registers an atexit hook
    import opf
    if opf._bootstrap() != 0:
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
    with patch.object(here, "_in_comment", lambda before: False):
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
    with patch.object(here, "_comment_text", lambda rel, lines: "\n".join(lines)):
        found = doc_problems(shipped, literal)
    check("revert/markdown-code-not-skipped-red", len(found) == 1 and "restates no declaration" in found[0], found)

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

    # a helper module that cannot be imported is a cannot-evaluate (exit 2), and a finding (exit 1, the escaping
    # ImportError) with _load() back outside the try
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
                                 "    try:\n        _load()\n        data = _read_regular(",
                                 "    _load()\n    try:\n        data = _read_regular(")
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
          "variant, a missing or commented-out restatement or digest location, a surface dropped from the list, "
          "a CRLF file and a release other than VERSION; a missing helper module a cannot-evaluate)".format(
              len(results), len(covered), len(cli_covered)))
    return 0


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if (len(args) in (2, 3) and args[-2] == "--execution-report" and os.path.isabs(args[-1])
            and args[:-2] in ([], ["--self-test"])):
        return self_test(args[-1])
    if args == ["--self-test"]:
        return self_test()
    if not args:
        return run(ROOT)
    print("usage: check_opf_declaration.py [--self-test] [--execution-report ABS_PATH]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main())
