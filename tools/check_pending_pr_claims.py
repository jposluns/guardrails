#!/usr/bin/env python3
"""Stale pending-claim check: a record sentence that says a named pull request is pending, open, under
review, stacked on or waiting, while that pull request has merged or closed.

  check_pending_pr_claims.py --check-inputs           offline: validate the declaration and every
                                                     declared surface, list the candidate claims;
                                                     never reads pull request state
  check_pending_pr_claims.py --all --live             read every declared surface and the live state
                                                     of every bound pull request
  check_pending_pr_claims.py --draft PATH --target T  judge the complete proposed content PATH as the
                                                     declared surface T (live)
  check_pending_pr_claims.py --staged --live          judge the staged blob of every declared surface
                                                     the index changes (live)
  check_pending_pr_claims.py --self-test              synthetic fixtures and stubbed gh processes
  check_pending_pr_claims.py --self-test --execution-report ABS_PATH
  any other argument list is refused with exit 2

Exit codes: 0 every bound claim was evaluated and none contradicts its pull request's lifecycle
(advisory warnings may remain); 1 one or more hard findings, evaluation otherwise complete; 2 the
evaluation is incomplete (pull request state could not be read, a binding is ambiguous, a declared
surface is missing or malformed, or a usage error). Definite findings are printed alongside the
unknowns. A run with no candidates means only that no supported current claim was found.

The declaration is .aiqt/pending-pr-claims.toml; the contract, grammar and residuals are in
.aiqt/pending-pr-claims.md. State is read by tools/_pr_state.py (gh api, host pinned to github.com);
a caller-supplied state map is never accepted as authority. The --check-inputs mode is the only
mode the Quality workflow runs: it is deterministic and offline and does not assess currency.

A negation, past-tense or conditional cue directly before a claim (not waiting for #17 to merge, had
been waiting, Neither #16 nor #17 is open, If #17 is still open) never exempts it: when the pull
request has merged or closed, the claim is a warning (exit 0, a WARNING line naming the sentence and
the pull request) instead of a finding. Only a claim with no such cue is a finding.

RESIDUALS (disclosed; the full list is in the contract): a prose grammar cannot be complete, and this
one is partial. The self-test covers only its rule and fix rows. Not caught: a claim before its
reference outside a Status cell (still pending: #17), words between a reference and its phrase (PR
#17, which fixes the parser, is still open), a Status cell whose state word is not first, a pull URL
suffix other than /files, /commits or /checks, a reference split inside a word, a current claim
added inside a blockquote that has a historical lead-in, and an issue claim (issue references are
never read). A cue further back than the words directly before a claim is not seen, so that claim
stays a finding. An imperative-looking `Open #N` at a clause start warns instead of finding.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_pending_pr_claims.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import errno
import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _pr_state  # noqa: E402  (the lifecycle reader beside this checker)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_REL = ".aiqt/pending-pr-claims.toml"
MAX_BYTES = 4 * 1024 * 1024
FORMATS = ("toml-strings", "markdown")
ROLES = ("source", "generated-view")
TOP_KEYS = {"schema-version", "limits", "surface"}
LIMIT_KEYS = {"max-prs": (1, 1000), "call-timeout-seconds": (1, 60), "deadline-seconds": (1, 600)}
SURFACE_KEYS = {"path", "format", "repository", "role"}
SURFACE_OPTIONAL = {"expected-repository", "generated-from", "status-column", "note"}

HARD, WARN, AMBIGUOUS, QUOTE = "hard", "warning", "ambiguous", "quote"


class InputError(Exception):
    """A declaration or declared surface that cannot be evaluated (exit 2)."""


# ---------------------------------------------------------------------------------------------------
# The declaration.

def _parse_toml(text, label):
    """(document, None), or (None, reason) for text that is not valid TOML. The one tomllib call site:
    a bare ValueError (an integer past the int-string limit), a RecursionError (deep nesting) and a
    MemoryError are each handled here, never re-raised."""
    try:
        return tomllib.loads(text), None
    except (tomllib.TOMLDecodeError, ValueError, RecursionError, MemoryError) as exc:
        return None, "{}: is not valid TOML ({}: {})".format(label, type(exc).__name__, exc)


def load_config(root):
    """The validated declaration as a dict; InputError on any problem (fail closed)."""
    data, why = _parse_toml(_read_text(Path(root) / CONFIG_REL, CONFIG_REL), CONFIG_REL)
    if why is not None:
        raise InputError(why)
    return validate_config(data)


def validate_config(data):
    if set(data) != TOP_KEYS:
        raise InputError("{}: top-level keys must be exactly {} (got {})".format(
            CONFIG_REL, sorted(TOP_KEYS), sorted(data)))
    if type(data["schema-version"]) is not int or data["schema-version"] != 1:
        raise InputError("{}: schema-version must be exactly the integer 1".format(CONFIG_REL))
    limits = data["limits"]
    if not isinstance(limits, dict) or set(limits) != set(LIMIT_KEYS):
        raise InputError("{}: [limits] keys must be exactly {}".format(CONFIG_REL, sorted(LIMIT_KEYS)))
    for key, (low, high) in LIMIT_KEYS.items():
        if type(limits[key]) is not int or not low <= limits[key] <= high:
            raise InputError("{}: limits.{} must be an integer from {} to {}".format(
                CONFIG_REL, key, low, high))
    surfaces = data["surface"]
    if not isinstance(surfaces, list) or not surfaces:
        raise InputError("{}: [[surface]] must be a non-empty array of tables".format(CONFIG_REL))
    seen = set()
    for index, row in enumerate(surfaces):
        where = "{}: surface[{}]".format(CONFIG_REL, index)
        if (not isinstance(row, dict) or not SURFACE_KEYS <= set(row)
                or not set(row) <= SURFACE_KEYS | SURFACE_OPTIONAL):
            raise InputError("{}: keys must be {} (optionally {})".format(
                where, sorted(SURFACE_KEYS), sorted(SURFACE_OPTIONAL)))
        for key, value in row.items():
            if not isinstance(value, str) or not value or any(ord(ch) < 32 for ch in value):
                raise InputError("{}: {} must be a non-empty single-line string".format(where, key))
        path = row["path"]
        if os.path.isabs(path) or ".." in Path(path).parts or "\\" in path or path in seen:
            raise InputError("{}: path {!r} must be a unique repo-relative path with no '..'".format(
                where, path))
        seen.add(path)
        if row["format"] not in FORMATS:
            raise InputError("{}: format must be one of {}".format(where, list(FORMATS)))
        if row["role"] not in ROLES:
            raise InputError("{}: role must be one of {}".format(where, list(ROLES)))
        if row["repository"] != "origin" and not _pr_state.valid_repo(row["repository"]):
            raise InputError("{}: repository must be \"origin\" or OWNER/REPO".format(where))
        if "expected-repository" in row and not _pr_state.valid_repo(row["expected-repository"]):
            raise InputError("{}: expected-repository must be OWNER/REPO".format(where))
        if "status-column" in row and row["format"] != "markdown":
            raise InputError("{}: status-column applies to a markdown surface only".format(where))
        if row["role"] == "generated-view" and "generated-from" not in row:
            raise InputError("{}: a generated-view surface must name generated-from".format(where))
    return data


_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


def _read_bytes(path, label):
    """The bytes of a regular, non-symlink file of at most MAX_BYTES; InputError otherwise. The file is
    opened once (O_NOFOLLOW: a symlink is refused; O_NONBLOCK: a FIFO is not waited on) and its type
    is checked on that descriptor, so the bytes read are the bytes of the file whose type was checked."""
    try:
        handle = os.open(path, _READ_FLAGS)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise InputError("{}: is not a regular file (a symlink or directory is refused)".format(label))
        raise InputError("{}: cannot be read ({})".format(label, exc.strerror or type(exc).__name__))
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise InputError("{}: is not a regular file (a symlink or directory is refused)".format(label))
        with os.fdopen(handle, "rb", closefd=False) as source:
            data = source.read(MAX_BYTES + 1)
    except OSError as exc:
        raise InputError("{}: cannot be read ({})".format(label, exc.strerror or type(exc).__name__))
    finally:
        os.close(handle)
    if len(data) > MAX_BYTES:
        raise InputError("{}: larger than {} bytes".format(label, MAX_BYTES))
    return data


def _decode(data, label):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise InputError("{}: is not UTF-8".format(label))
    if "\x00" in text:
        raise InputError("{}: carries a NUL character".format(label))
    return text


def _read_text(path, label):
    return _decode(_read_bytes(path, label), label)


# ---------------------------------------------------------------------------------------------------
# Record units: complete paragraphs, list items, table cells and decoded TOML strings, with locations.

def toml_units(text, label):
    """[(location, prose, context)] for every decoded string value of a TOML document."""
    data, why = _parse_toml(text, label)
    if why is not None:
        raise InputError(why)
    units = []

    def walk(value, where):
        if isinstance(value, str):
            units.append(("{}: {}".format(label, where), value, "prose"))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, "{}.{}".format(where, key) if where else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, "{}[{}]".format(where, index))

    walk(data, "")
    return units


# A fence run, matched after a line's indentation; whether it opens a fence is block_context's call.
FENCE_OPEN_RE = re.compile(r"(`{3,}|~{3,})")
# The deepest indentation, in columns past the containing block's content column (the left margin, or a
# list item's content column), at which a fence opens or closes (CommonMark); a deeper line is
# indented code, or paragraph text when a paragraph is open.
FENCE_MAX_RELATIVE_INDENT = 3
# An inline code span (CommonMark: a backtick run closed by a run of the same length).
CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)")
# A link reference definition ([label]: URL "title"); it is not record prose.
LINK_DEFINITION_RE = re.compile(
    r" {0,3}\[([^\]]{1,999})\]:\s*<?([^\s<>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?\s*$")
# A full ([text][label]), collapsed ([text][]) or shortcut ([label]) reference link.
REFERENCE_LINK_RE = re.compile(r"(!?)\[([^\[\]]*)\](?:\[([^\[\]]*)\])?(?![(:])")
HEADER_EMPHASIS_RE = re.compile(r"^(\*{1,2}|_{1,2}|`)(.+)\1$")
QUOTE_RE = re.compile(r" {0,3}>[ ]?(.*)")
HEADING_RE = re.compile(r" {0,3}#{1,6}(?:\s+(.*))?$")
ITEM_RE = re.compile(r"\s*(?:[-*+]|[0-9]{1,9}[.)])\s+(?:\[[ xX]\]\s+)?(.*)")
SEPARATOR_CELL_RE = re.compile(r"\s*:?-{3,}:?\s*")
HISTORY_INTRO_RE = re.compile(r"(?i)(?:earlier note|previously recorded)\s*:\s*$")
HISTORY_LEAD_RE = re.compile(r"(?i)\s*(?:earlier note|previously recorded)\s*:")


def _cells(line):
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.replace("\ue002", "\\|").strip() for cell in body.replace("\\|", "\ue002").split("|")]


def _header_name(cell):
    """A header cell's name with one wrapping emphasis or code span removed (**Status** is Status)."""
    match = HEADER_EMPHASIS_RE.match(cell)
    return match.group(2).strip() if match else cell


def _resolve_reference_links(prose, definitions):
    """prose with each reference link whose label is defined in the document rewritten as the inline
    link it stands for, so the markdown-link step reads its URL; an undefined one is left as text."""
    def replace(match):
        bang, text, label = match.group(1), match.group(2), match.group(3)
        key = " ".join((label if label else text).split()).casefold()
        if key not in definitions:
            return match.group(0)
        return "{}[{}]({})".format(bang, text, definitions[key])
    return REFERENCE_LINK_RE.sub(replace, prose) if definitions else prose


# The kinds block_context gives a Markdown line. The first five are never read: a blank line, a line
# of a fenced block, an indented code line, a line of an HTML comment block, and a line wholly inside
# an inline HTML comment (which, unlike the others, does not end the paragraph around it). The rest
# are the prose kinds, the only lines any reader (record units, link definitions, Status cells and so
# the references they resolve) consumes.
BLANK, FENCE, CODE, COMMENT, INLINE_COMMENT = "blank", "fence", "code", "comment", "inline-comment"
DEFINITION, QUOTE_LINE, TABLE_HEADER, TABLE_DELIMITER, TABLE_ROW, HEADING, ITEM, PARA = (
    "definition", "quote", "table-header", "table-delimiter", "table-row", "heading", "item", "para")
PROSE_KINDS = (DEFINITION, QUOTE_LINE, TABLE_HEADER, TABLE_DELIMITER, TABLE_ROW, HEADING, ITEM, PARA)


def _indent(text):
    """The columns of leading whitespace (a tab advances to the next multiple of four)."""
    expanded = text.expandtabs(4)
    return len(expanded) - len(expanded.lstrip(" "))


def _content_column(text, match):
    """A list item line's content column: the column its text starts at (after the marker, the spaces and
    any task box). CommonMark puts it at the marker's end plus one when five or more spaces follow the
    marker, which would make text wrapped under the item's first word indented code; the later column
    is kept because it reads more (a deeper code threshold, and a fence left of it is outside the item)."""
    return len(text[:match.start(1)].expandtabs(4))


def _comment_start(text):
    """The index in text of the first `<!--` outside a code span whose `-->` is not on the same line, or
    None (a comment that ends on its own line is left to the html-comment normalization)."""
    masked = CODE_SPAN_RE.sub(lambda match: " " * len(match.group(0)), text)
    position = 0
    while True:
        start = masked.find("<!--", position)
        if start < 0:
            return None
        end = masked.find("-->", start + 4)
        if end < 0:
            return start
        position = end + 3


def _delimiter_row(line):
    return "|" in line and all(SEPARATOR_CELL_RE.fullmatch(cell) for cell in _cells(line))


def _interrupts_paragraph(line):
    """True for a line that starts a block and so ends a paragraph: an inline HTML comment does not run
    past it."""
    body = line.lstrip()
    return (FENCE_OPEN_RE.match(body) is not None or HEADING_RE.match(body) is not None
            or QUOTE_RE.match(body) is not None or ITEM_RE.match(body) is not None)


def block_context(lines):
    """[(kind, text)], one per line of a Markdown document: the one place its block context is decided,
    before any line's text is read. A fence opens or closes only at most FENCE_MAX_RELATIVE_INDENT
    columns past its container's content column (the left margin, or the innermost list item's content
    column); four or more columns past it, outside a paragraph, is indented code. A line indented less
    than a list item's content column ends that item (a paragraph line aside: it may be a lazy
    continuation), and with it any fenced block or HTML comment block opened inside it. An HTML comment
    block starts at a line opening `<!--` (after at most three columns past the container) and runs to
    the line holding `-->`, across blank lines; an inline comment that `<!--` opens later in a line runs
    to the first later line holding `-->` before the paragraph ends (with none, `<!--` is text). text is
    the line for a prose kind, cut at an inline comment's start or after a comment's end; "" otherwise.
    InputError for a fenced block or HTML comment block that is never closed."""
    kinds = []
    items = []            # the content columns of the open list items, innermost last
    block = None          # (FENCE, fence run, container column) or (COMMENT, None, container column)
    hidden_until = -1     # the index of the line that ends the open inline comment
    para = quote = table = False
    for index, text in enumerate(lines):
        if index < hidden_until:
            kinds.append((INLINE_COMMENT, ""))
            continue
        if index == hidden_until:
            hidden_until = -1
            text = text[text.find("-->") + 3:]
            if not text.strip():
                kinds.append((INLINE_COMMENT, ""))
                continue
        indent = _indent(text)
        if block is not None:
            name, run, column = block
            if not text.strip() or indent >= column:
                if name == FENCE:
                    body = text.strip()
                    if (indent - column <= FENCE_MAX_RELATIVE_INDENT and body.startswith(run)
                            and set(body) == {run[0]}):
                        block = None
                    kinds.append((FENCE, ""))
                    continue
                end = text.find("-->")
                if end < 0:
                    kinds.append((COMMENT, ""))
                    continue
                block, text = None, text[end + 3:]
                if not text.strip():
                    kinds.append((COMMENT, ""))
                    continue
                indent = _indent(text)
            else:
                block = None  # the list item holding the block has ended, and the block with it
        if not text.strip():
            para = quote = table = False
            kinds.append((BLANK, ""))
            continue
        lead = len(text) - len(text.lstrip())
        nested = [column for column in items if column <= indent]
        column = nested[-1] if nested else 0
        if not para:
            items = nested
        opener = FENCE_OPEN_RE.match(text, lead)
        if opener and indent - column <= FENCE_MAX_RELATIVE_INDENT and not (
                opener.group(1)[0] == "`" and "`" in text[opener.end():]):
            # (A backtick fence's info string cannot hold a backtick: ```x``` is inline code.)
            items, block = nested, (FENCE, opener.group(1), column)
            para = quote = table = False
            kinds.append((FENCE, ""))
            continue
        if not para and not quote and indent - column >= 4:
            kinds.append((CODE, ""))
            continue
        start = _comment_start(text)
        html_block = text.startswith("<!--", lead) and indent - column <= 3
        if html_block and start == lead:
            items, block = nested, (COMMENT, None, column)
            para = quote = table = False
            kinds.append((COMMENT, ""))
            continue
        if start is not None and not html_block:
            for later in range(index + 1, len(lines)):
                if not lines[later].strip() or _interrupts_paragraph(lines[later]):
                    break
                if "-->" in lines[later]:
                    hidden_until = later
                    break
            if hidden_until > index:
                text = text[:start]
                if not text.strip():
                    kinds.append((INLINE_COMMENT, ""))
                    continue
        following = lines[index + 1] if index + 1 < len(lines) and hidden_until <= index else ""
        if not para and LINK_DEFINITION_RE.match(text):
            items, quote, table = nested, False, False
            kinds.append((DEFINITION, text))
            continue
        if QUOTE_RE.match(text):
            items, para, quote = nested, False, True
            kinds.append((QUOTE_LINE, text))
            continue
        quote = False
        # A table row: a line with a leading pipe, a row of the current table, or a header whose next
        # line is a delimiter row; the outer pipes are optional (GFM).
        if text.lstrip().startswith("|") or ("|" in text and (table or _delimiter_row(following))):
            items, para = nested, False
            if _delimiter_row(text):
                kinds.append((TABLE_DELIMITER, text))
            elif not table and _delimiter_row(following):
                table = True
                kinds.append((TABLE_HEADER, text))
            else:
                kinds.append((TABLE_ROW, text))
            continue
        table = False
        if HEADING_RE.match(text):
            items, para = nested, False
            kinds.append((HEADING, text))
            continue
        match = ITEM_RE.match(text)
        if match:
            items, para = nested + [_content_column(text, match)], True
            kinds.append((ITEM, text))
            continue
        para = True
        kinds.append((PARA, text))
    if block is not None:
        raise InputError("a fenced block opened is never closed" if block[0] == FENCE
                         else "an HTML comment opened is never closed")
    return kinds


def markdown_units(text, label, status_column=None):
    """[(location, prose, context)] for a Markdown document, read only from the lines block_context
    gives a prose kind (fenced blocks, indented code and HTML comments are never read, in a list item
    as outside one); a blockquote whose lead-in (the paragraph just before it, or its own first words)
    is `Earlier note:` or `Previously recorded:` is historical and skipped; any other blockquote is
    context "quote"; a cell of the declared status column is context "status"; everything else is
    "prose". Wrapped lines of one paragraph or list item are joined into one unit. A link reference
    definition (a definition line, so never one inside code or a comment) is not a unit; a reference
    link to it reads as an inline link. A leading byte order mark is dropped."""
    units = []
    if text.startswith("\ufeff"):
        text = text[1:]
    try:
        blocks = block_context(text.split("\n"))
    except InputError as exc:
        raise InputError("{}: {}".format(label, exc))
    definitions = {}
    para, para_line = [], 0
    quote, quote_line = [], 0
    last_prose = ""
    header = None

    def flush_para():
        nonlocal para, last_prose
        if para:
            joined = " ".join(para)
            units.append(("{}:{}".format(label, para_line), joined, "prose"))
            last_prose = joined
        para = []

    def flush_quote():
        nonlocal quote, last_prose
        if quote:
            joined = " ".join(quote)
            historical = (HISTORY_INTRO_RE.search(last_prose) is not None
                          or HISTORY_LEAD_RE.match(joined) is not None)
            if not historical:
                units.append(("{}:{} (blockquote)".format(label, quote_line), joined, "quote"))
            last_prose = ""
        quote = []

    for number, (kind, line) in enumerate(blocks, 1):
        if kind == INLINE_COMMENT:
            continue
        if kind == BLANK:
            flush_para()
            flush_quote()
            header = None
            continue
        if kind == CODE:
            last_prose = ""
            continue
        if kind not in PROSE_KINDS:  # a fence or HTML comment block line
            flush_para()
            flush_quote()
            header = None
            last_prose = ""
            continue
        if kind == DEFINITION:
            flush_quote()
            header = None
            match = LINK_DEFINITION_RE.match(line)
            definitions.setdefault(" ".join(match.group(1).split()).casefold(), match.group(2))
            continue
        if kind == QUOTE_LINE:
            flush_para()
            if not quote:
                quote_line = number
            quote.append(QUOTE_RE.match(line).group(1).strip())
            continue
        flush_quote()
        if kind in (TABLE_HEADER, TABLE_DELIMITER, TABLE_ROW):
            flush_para()
            last_prose = ""
            cells = _cells(line)
            if kind == TABLE_DELIMITER:
                continue
            if kind == TABLE_HEADER:
                header = [_header_name(cell) for cell in cells]
                continue
            for column, cell in enumerate(cells):
                name = header[column] if header is not None and column < len(header) else None
                context = "status" if status_column is not None and name == status_column else "prose"
                units.append(("{}:{} ({} cell)".format(label, number, name or "column {}".format(
                    column + 1)), cell, context))
            continue
        header = None
        if kind == HEADING:
            flush_para()
            units.append(("{}:{}".format(label, number), (HEADING_RE.match(line).group(1) or "").strip(),
                          "prose"))
            last_prose = units[-1][1]
            continue
        if kind == ITEM:
            flush_para()
            para, para_line = [ITEM_RE.match(line).group(1).strip()], number
            continue
        if not para:
            para_line = number
        para.append(line.strip())
    flush_para()
    flush_quote()
    return [(where, _resolve_reference_links(prose, definitions), context)
            for where, prose, context in units]


def surface_units(surface, data):
    """The record units of one declared surface given its complete bytes; InputError when malformed."""
    label = surface["path"]
    text = _decode(data, label)
    if surface["format"] == "toml-strings":
        return toml_units(text, label)
    return markdown_units(text, label, surface.get("status-column"))


# ---------------------------------------------------------------------------------------------------
# The grammar: pull request references, clause boundaries, and bound claim phrases.

_O = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
_R = r"[A-Za-z0-9._-]{1,100}"
REF_RE = re.compile(
    r"(?:\b(?:pull\s+requests?|PRs?)\s+)?"
    r"(?:https?://github\.com/(?P<uo>" + _O + r")/(?P<ur>" + _R + r")/pull/(?P<un>[0-9]{1,9})"
    r"(?:/(?:files|commits|checks))?/?(?![\w/])"
    r"|(?<![\w./-])(?P<qo>" + _O + r")/(?P<qr>" + _R + r")#(?P<qn>[0-9]{1,9})(?![\w])"
    r"|\bpr:#?(?P<pn>[0-9]{1,9})(?![\w])"
    r"|(?<![\w&#/])#(?P<hn>[0-9]{1,9})(?![\w]))",
    re.IGNORECASE)
# The placeholder a pull request reference is replaced with before the phrase rules run (private-use
# characters, so no record text can spell it).
REF_MARK = "\ue000[0-9]+\ue001"
REF_MARK_RE = re.compile("\ue000([0-9]+)\ue001")
LIST = r"(?P<l>{0}(?:\s*,\s*(?:and\s+|or\s+)?{0}|\s+(?:and|or|&)\s+{0})*)".format(REF_MARK)
# A verb, or `still` or `currently` standing for one, then up to two modifiers (#17 still currently open).
VERB = (r"(?:(?:is|are|remains?|stays?|(?:has|have)\s+been|still|currently)"
        r"(?:\s+(?:still|currently|now|also|both)){0,2})")
# A historical lead-in followed by a quoted span: only that span is exempt. A straight single-quoted
# span ends at a quote that no letter follows, so an apostrophe inside it (isn't) does not end it.
STRAIGHT_SPAN = r"'(?:[^']|'(?=\w))*'(?!\w)"


def _history_span_re(straight):
    return re.compile(
        r"(?i)\b(?:previously recorded|earlier note|the old note read|old note read|was recorded as|"
        r"previously read)\s*:?\s*(?:\u201c[^\u201d]*\u201d|\"[^\"]*\"|`[^`]*`|\u2018[^\u2019]*\u2019"
        r"|" + straight + r")")


HISTORY_SPAN_RE = _history_span_re(STRAIGHT_SPAN)
CLAUSE_SPLIT_RE = re.compile(r"[;:!?\u00a6]|\.(?=\s|$)|\s[\u2013\u2014]\s|\s-\s")
# A status cell keeps its colon (In progress: #17) inside one clause.
STATUS_CLAUSE_SPLIT_RE = re.compile(r"[;!?\u00a6]|\.(?=\s|$)|\s[\u2013\u2014]\s|\s-\s")
FORMATTING_RE = re.compile("[`\u201c\u201d\u2018\u2019\"*]|~~")
INSTRUCTION_WORDS = {"leave", "leaves", "left", "keep", "keeps", "kept", "to", "will", "should",
                     "must", "please", "re"}
# Cue words: one of these among the words directly before a claim (read back over CUE_PASSES words
# only) makes the claim negated, past, conditional or a question, so a merged or closed pull request
# gives a warning, never a finding and never a silent pass. "we'd" reads as "we d" (had or would).
CUES = {"not", "never", "longer", "no", "t", "isnt", "arent", "wasnt", "werent", "hasnt", "havent",
        "hadnt", "dont", "doesnt", "didnt", "cannot", "neither", "nor", "none", "was", "were", "had", "d",
        "did", "if", "whether", "unless", "previously", "formerly", "once", "merged", "closed", "landed"}
# Words read past while looking back for a cue: adverbs, auxiliaries, contraction stems and a few
# determiners ("have not only been waiting", "haven't been", "no pending PRs", "all pending PRs").
CUE_PASSES = {"still", "also", "then", "currently", "yet", "just", "only", "now", "already", "even",
              "been", "be", "being", "is", "are", "am", "have", "has", "s", "re", "ve", "do", "does",
              "isn", "aren", "wasn", "weren", "hasn", "haven", "hadn", "don", "doesn", "didn", "can",
              "won", "wouldn", "couldn", "shouldn", "the", "all", "any", "of"}
# An issue number, bare or qualified by its repository (acme/widgets#17). Only the plural `issues`
# takes a list: after `issue #12,` a following `#17` is read as a pull request.
_ISSUE_NUMBER = r"(?:" + _O + r"/" + _R + r")?#[0-9]{1,9}"
ISSUE_LIST_RE = re.compile(
    r"\bissue\s*(?P<one>" + _ISSUE_NUMBER + r")"
    r"|\bissues\s*(?P<l>" + _ISSUE_NUMBER + r"(?:\s*(?:,|/|&|\band\b|\bor\b)\s*(?:and\s+|or\s+)?"
    + _ISSUE_NUMBER + r")*)", re.I)
PULL_URL_RE = re.compile(r"https?://github\.com/" + _O + r"/" + _R + r"/pull/[0-9]{1,9}"
                         r"(?:/(?:files|commits|checks))?/?(?:#[\w-]*)?", re.ASCII)
ISSUE_URL_RE = re.compile(r"https?://github\.com/" + _O + r"/" + _R + r"/issues/(?P<n>[0-9]{1,9})/?(?:#[\w-]*)?",
                          re.ASCII)


def _link_target(text, url):
    """A link's replacement: a pull request URL stands for its target (it carries the repository); an
    issue URL becomes an issue reference; any other link keeps only its text."""
    if PULL_URL_RE.fullmatch(url):
        return " " + url.split("#")[0].rstrip("/") + " "
    issue = ISSUE_URL_RE.fullmatch(url)
    if issue:
        return " issue #{} ".format(issue.group("n"))
    return text


def _link(match):
    """A Markdown inline link or image."""
    return _link_target(match.group(1), match.group(2))


def _html_link(match):
    """An HTML anchor on one unit: <a href="URL">text</a>."""
    return _link_target(match.group(2), match.group(1))


def _issue(match):
    """An issue reference list: its numbers are not pull requests, so their '#' is replaced by a
    private-use mark no pull request reference matches."""
    return match.group(0).replace("#", "\ue003")


# The normalization steps tokenize applies, in order, before pull request references are found:
# (name, regex, replacement). The self-test removes one at a time by name (red-when-reverted).
NORMALIZE = (
    ("html-comment", re.compile(r"<!--.*?-->"), " "),
    ("markdown-link", re.compile(r"!?\[([^\]]*)\]\(\s*<?([^\s()<>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)"),
     _link),
    ("html-link", re.compile(r"(?i)<a\s+[^>]*?\bhref\s*=\s*[\"']([^\"'\s<>]+)[\"'][^>]*>(.*?)</a\s*>"), _html_link),
    ("autolink", re.compile(r"<(https?://github\.com/[^\s<>]+)>"), r" \1 "),
    # A bracket that is not a link ([#17] with no definition) is text: its brackets are dropped.
    ("bracket", re.compile(r"\[([^\[\]]*)\]"), r" \1 "),
    # A curly apostrophe inside a word (isn\u2019t, we\u2019d) is an apostrophe, not a closing quotation mark.
    ("apostrophe", re.compile(r"(?<=[A-Za-z])\u2019(?=[A-Za-z])"), "'"),
    ("history-span", HISTORY_SPAN_RE, " \u00a6 "),
    # GFM strikethrough: ~~text~~, or ~text~ with flanking tildes (so ~5 min and ~10 is not struck).
    ("strikethrough", re.compile(r"~~[^~]*~~|(?<![~\w])~(?![\s~])[^~]*(?<![\s~])~(?![~\w])"), " "),
    ("formatting", FORMATTING_RE, ""),
    ("underscore-emphasis", re.compile(r"(?<!\w)_{1,2}(?=#[0-9])|(?<=[0-9])_{1,2}(?!\w)"), ""),
    ("issue-reference", ISSUE_LIST_RE, _issue),
    ("glued-pr", re.compile(r"\b(PRs?)#(?=[0-9])", re.I), r"\1 #"),
    ("slash-list", re.compile(r"(?<=[0-9])\s*/\s*(?=#[0-9])"), " and "),
)

PATTERNS = (
    # (rule id, kind, phrase, regex, contexts) in priority order; a token bound HARD is not also a
    # warning. The self-test exercises every rule id on both sides and with the rule removed.
    ("status-in-progress", HARD, "in progress (an open pull request under review)",
     re.compile(r"^\s*in\s+progress\s*[(:]?\s*" + LIST, re.I), ("status",)),
    ("status-open", HARD, "open (status)",
     re.compile(r"^\s*(?:open|pending|under\s+review|in\s+review)\s*[(:]?\s*" + LIST, re.I), ("status",)),
    ("pending-verb", HARD, "pending", re.compile(LIST + r"\s+" + VERB + r"\s+pending\b", re.I), None),
    ("pending-paren", HARD, "pending", re.compile(LIST + r"\s*\(\s*pending\s*\)", re.I), None),
    ("open-paren", HARD, "open", re.compile(
        LIST + r"\s*\(\s*(?:open|in\s+progress|under\s+review|in\s+review)\s*\)", re.I), None),
    ("pending-batch", HARD, "a pending batch member",
     re.compile(r"\bpending\s+batch\s*\(\s*" + LIST + r"\s*\)", re.I), None),
    ("pending-lead", HARD, "pending", re.compile(r"\bpending\s+(?:merge\s+of\s+)?" + LIST, re.I), None),
    ("open-verb", HARD, "open", re.compile(LIST + r"\s+" + VERB + r"\s+open\b", re.I), None),
    ("open-lead", HARD, "open", re.compile(r"(?<![\w-])open\s+" + LIST, re.I), None),
    ("under-review", HARD, "under review", re.compile(
        LIST + r"\s+" + VERB + r"\s+(?:under|in)\s+review\b", re.I), None),
    ("awaiting-review", HARD, "under review", re.compile(
        LIST + r"\s+" + VERB + r"\s+(?:awaiting|waiting\s+(?:for|on))\s+(?:review|approval)\b", re.I), None),
    ("in-progress-verb", HARD, "in progress", re.compile(LIST + r"\s+" + VERB + r"\s+in\s+progress\b", re.I),
     None),
    ("waiting-to-merge", HARD, "yet to merge", re.compile(
        r"\b(?:waiting|wait|waits)\s+(?:for|on)\s+" + LIST + r"\s+to\s+(?:merge|land|be\s+merged)\b",
        re.I), None),
    ("awaiting-merge", HARD, "yet to merge", re.compile(r"\bawaiting\s+(?:the\s+)?merge\s+of\s+" + LIST, re.I),
     None),
    ("until-merge", HARD, "yet to merge", re.compile(
        r"\buntil\s+" + LIST + r"\s+(?:merges?|lands?|(?:is|are)\s+merged)\b", re.I), None),
    ("once-merge", HARD, "yet to merge", re.compile(
        r"\b(?:once|after)\s+" + LIST + r"\s+(?:merges?|lands?|(?:is|are)\s+merged)\b", re.I), None),
    ("has-yet-to-merge", HARD, "yet to merge", re.compile(
        LIST + r"\s+(?:has|have)\s+yet\s+to\s+(?:merge|land|be\s+merged)\b", re.I), None),
    ("yet-to-be-merged", HARD, "yet to merge", re.compile(
        LIST + r"\s+(?:is|are)\s+yet\s+to\s+be\s+(?:merged|landed)\b", re.I), None),
    ("not-yet-merged", HARD, "not yet merged", re.compile(LIST + r"\s+(?:is|are)\s+not\s+yet\s+merged\b",
                                                          re.I), None),
    ("has-not-merged", HARD, "not yet merged", re.compile(
        LIST + r"\s+(?:has|have)(?:\s+not|n'?t)\s+(?:yet\s+)?(?:been\s+)?(?:merged|landed)\b", re.I), None),
    ("waiting-dependency", WARN, "a dependency",
     re.compile(r"\b(?:waiting|wait|waits)\s+(?:for|on)\s+" + LIST, re.I), None),
    ("awaiting-dependency", WARN, "a dependency", re.compile(r"\bawaiting\s+" + LIST, re.I), None),
    ("blocked-dependency", WARN, "a dependency", re.compile(r"\bblocked\s+(?:on|by)\s+" + LIST, re.I), None),
    ("stacked", WARN, "a stack base", re.compile(r"\b(?:stacked|stacks)\s+(?:on|atop)\s+" + LIST, re.I), None),
)
AMBIGUOUS_RES = (
    re.compile(r"\bpending\s+(?:PRs?|pull\s+requests?)\b(?!\s*\ue000)", re.I),
    re.compile(r"\b(?:the|this|that)\s+(?:PR|pull\s+request)\s+" + VERB + r"\s+(?:pending|open)\b", re.I),
)
IMPERATIVE_OPEN = (WARN, "open (an instruction or a claim; review it)")
# An unnamed pending or open phrase after a cue (There are no pending PRs): a warning, not ambiguous.
CUED_AMBIGUOUS = "cued-ambiguous"


def tokenize(prose, context="prose"):
    """(clauses, refs): the prose normalized (NORMALIZE), each pull request reference replaced by a
    token, split into clauses; refs[k] = (repo or None for the surface default, number, original
    text)."""
    text = " ".join(prose.split())
    for _name, regex, replacement in NORMALIZE:
        text = regex.sub(replacement, text)
    text = " ".join(text.split())
    refs = []

    def replace(match):
        if match.group("un"):
            repo, number = "{}/{}".format(match.group("uo"), match.group("ur")), match.group("un")
        elif match.group("qn"):
            repo, number = "{}/{}".format(match.group("qo"), match.group("qr")), match.group("qn")
        else:
            repo, number = None, match.group("pn") or match.group("hn")
        number = int(number)
        if number < 1 or (repo is not None and not _pr_state.valid_repo(repo)):
            return match.group(0)
        refs.append((repo, number, match.group(0)))
        return "\ue000{}\ue001".format(len(refs) - 1)

    text = REF_RE.sub(replace, text)
    splitter = STATUS_CLAUSE_SPLIT_RE if context == "status" else CLAUSE_SPLIT_RE
    clauses = [clause.strip() for clause in splitter.split(text)]
    return [clause for clause in clauses if clause], refs


def _words_before(clause, start):
    return [word.lower() for word in re.findall(r"[A-Za-z]+", clause[:start])]


def _instruction_before(clause, start):
    words = _words_before(clause, start)
    return bool(words) and words[-1] in INSTRUCTION_WORDS


def _imperative_open(clause, start):
    """True when `open #N` starts its clause or follows a comma (Open #17 to see it; To review it,
    open #17): an instruction as likely as a claim, so it warns rather than finds."""
    before = clause[:start].rstrip()
    return before == "" or before.endswith(",")


def _cue_before(clause, start):
    """The cue word (CUES) among the words directly before a claim, read back over CUE_PASSES words
    only, or None: "not waiting", "have not only been waiting", "we'd been waiting", "were not only
    waiting", "Neither #16 nor #17 is open", "If #17 is still open", "no pending PRs". The cue never
    exempts a claim; it makes it a warning. Present perfect with no cue ("have been waiting", "it's
    been waiting") has none, so it stays a finding."""
    for word in reversed(_words_before(clause, start)):
        if word in CUES:
            return word
        if word not in CUE_PASSES:
            return None
    return None


def find_candidates(prose, context):
    """[(kind, phrase, ref, clause text)] for one unit. A quote context reports any claim as QUOTE (it
    needs a historical lead-in or a move out of the quote); an ambiguous phrase is AMBIGUOUS, whether
    or not a named claim also binds in the same clause, unless every match of it follows a cue
    (CUED_AMBIGUOUS, a warning). A claim after a cue binds as a warning naming the cue."""
    clauses, refs = tokenize(prose, context)
    found = []
    for clause in clauses:
        bound = {}
        for rule, kind, phrase, regex, contexts in PATTERNS:
            if contexts is not None and context not in contexts:
                continue
            for match in regex.finditer(clause):
                hit_kind, hit_phrase = kind, phrase
                if rule == "open-lead" and _instruction_before(clause, match.start()):
                    continue
                cue = _cue_before(clause, match.start())
                if cue is not None:
                    hit_kind, hit_phrase = WARN, "{} (after the cue {!r}: negated, past, conditional or a " \
                                                 "question; review it)".format(phrase, cue)
                elif rule == "open-lead" and context != "status" and _imperative_open(clause, match.start()):
                    hit_kind, hit_phrase = IMPERATIVE_OPEN
                for index in REF_MARK_RE.findall(match.group("l")):
                    index = int(index)
                    if index not in bound or (bound[index][0] == WARN and hit_kind == HARD):
                        bound[index] = (hit_kind, hit_phrase)
        shown = REF_MARK_RE.sub(lambda m: refs[int(m.group(1))][2], clause)
        for index in sorted(bound):
            kind, phrase = bound[index]
            if context == "quote":
                kind = QUOTE
            found.append((kind, phrase, refs[index], shown))
        cues = [_cue_before(clause, match.start()) for regex in AMBIGUOUS_RES for match in regex.finditer(clause)]
        if None in cues:
            found.append((AMBIGUOUS, "pending or open", None, shown))
        elif cues:
            found.append((CUED_AMBIGUOUS, cues[0], None, shown))
    return found


# ---------------------------------------------------------------------------------------------------
# Evaluation.

class Result:
    def __init__(self):
        self.hard, self.warnings, self.unknown, self.candidates = [], [], [], 0

    def exit_code(self):
        return 2 if self.unknown else (1 if self.hard else 0)


def collect(surfaces_with_bytes):
    """[(surface, location, kind, phrase, ref, clause)] over (surface, bytes) pairs; InputError
    propagates."""
    out = []
    for surface, data in surfaces_with_bytes:
        for location, prose, context in surface_units(surface, data):
            for kind, phrase, ref, clause in find_candidates(prose, context):
                out.append((surface, location, kind, phrase, ref, clause))
    return out


def evaluate(candidates, config, root, transport=None, git_runner=_pr_state.run_git_origin):
    """The Result of judging the candidates against live pull request state."""
    result = Result()
    result.candidates = len(candidates)
    origin_cache = {}
    keyed = []
    for surface, location, kind, phrase, ref, clause in candidates:
        if kind == AMBIGUOUS:
            result.unknown.append("{}: {!r}: a pending or open phrase whose pull request is not named; "
                                  "the binding is ambiguous".format(location, clause))
            continue
        if kind == CUED_AMBIGUOUS:
            result.warnings.append("{}: {!r}: a pending or open phrase whose pull request is not named, "
                                   "after the cue {!r} (negated, past, conditional or a question); no "
                                   "pull request was read; review it".format(location, clause, phrase))
            continue
        if kind == QUOTE:
            result.unknown.append("{}: {!r}: a claim inside a blockquote with no historical lead-in "
                                  "(Earlier note: or Previously recorded:); review it".format(
                                      location, clause))
            continue
        repo, number, text = ref
        if repo is None:
            repo, why = _surface_repo(surface, root, origin_cache, git_runner)
            if repo is None:
                result.unknown.append("{}: {!r}: {} cannot be bound to a repository: {}".format(
                    location, clause, text, why))
                continue
        keyed.append(((repo, number), location, kind, phrase, clause))
    limits = config["limits"]
    states = _pr_state.read_states([key for key, *_rest in keyed], transport=transport,
                                   max_prs=limits["max-prs"],
                                   call_timeout=limits["call-timeout-seconds"],
                                   deadline=limits["deadline-seconds"]) if keyed else {}
    for (repo, number), location, kind, phrase, clause in keyed:
        state, why, when = states[(repo, number)]
        name = "{}#{}".format(repo, number)
        read = " (read {})".format(when) if when else ""
        if state == _pr_state.UNKNOWN:
            result.unknown.append("{}: {!r}: {} state cannot be evaluated: {}{}".format(
                location, clause, name, why, read))
        elif state != _pr_state.OPEN:
            line = "{}: {!r}: says {} is {}, but it is {}{}".format(location, clause, name, phrase,
                                                                     state, read)
            (result.hard if kind == HARD else result.warnings).append(line)
    return result


def _surface_repo(surface, root, cache, git_runner):
    if surface["repository"] != "origin":
        return surface["repository"], None
    if "origin" not in cache:
        cache["origin"] = _pr_state.origin_repo(root, git_runner)
    repo, why = cache["origin"]
    expected = surface.get("expected-repository")
    if repo is not None and expected is not None and repo.casefold() != expected.casefold():
        return None, "the origin names {}, not the declared expected repository {}".format(repo, expected)
    return repo, why


def report(result, stream=None):
    stream = stream or sys.stdout
    for line in result.hard:
        print("FINDING: " + line, file=stream)
    for line in result.warnings:
        print("WARNING: " + line, file=stream)
    for line in result.unknown:
        print("CANNOT EVALUATE: " + line, file=stream)
    code = result.exit_code()
    if code == 0:
        if result.candidates:
            print("PASS: {} candidate claim(s) evaluated; none contradicts its pull request's lifecycle"
                  "{}".format(result.candidates, " (advisory warnings above)" if result.warnings else ""),
                  file=stream)
        else:
            print("PASS: no supported current pending-claim found (this is not proof that none exists)",
                  file=stream)
    elif code == 2:
        print("INCOMPLETE: the evaluation did not complete; this is not a pass", file=stream)
    return code


# ---------------------------------------------------------------------------------------------------
# Modes.

def _declared(config, rel):
    for surface in config["surface"]:
        if surface["path"] == rel:
            return surface
    return None


def check_inputs(root):
    """--check-inputs: offline validation; returns the exit code."""
    try:
        config = load_config(root)
        pairs = [(s, _read_bytes(Path(root) / s["path"], s["path"])) for s in config["surface"]]
        candidates = collect(pairs)
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    print("PASS: {} declared surface(s) parse; {} candidate claim(s) found. Offline input validation "
          "only: pull request currency was NOT assessed".format(len(pairs), len(candidates)))
    return 0


def run_all(root, transport=None, git_runner=_pr_state.run_git_origin):
    try:
        config = load_config(root)
        pairs = [(s, _read_bytes(Path(root) / s["path"], s["path"])) for s in config["surface"]]
        candidates = collect(pairs)
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return report(evaluate(candidates, config, root, transport, git_runner))


def judge_draft_bytes(root, target, data, transport=None, git_runner=_pr_state.run_git_origin):
    """The exit code for complete proposed bytes of the declared surface target (a repo-relative path),
    with the findings printed. Used by --draft and by tools/apply_record_draft.py."""
    try:
        config = load_config(root)
        surface = _declared(config, target)
        if surface is None:
            raise InputError("{}: is not a declared surface in {}".format(target, CONFIG_REL))
        candidates = collect([(surface, data)])
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return report(evaluate(candidates, config, root, transport, git_runner))


def run_draft(root, draft, target, transport=None):
    try:
        data = _read_bytes(Path(draft), draft)
    except InputError as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    return judge_draft_bytes(root, target, data, transport)


STAGED_NAMES = ["diff", "--cached", "--name-only", "-z", "--no-renames"]
OBJECT_ID_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
# Index modes of a regular file; a symlink (120000), a gitlink (160000) or anything else is refused.
REGULAR_INDEX_MODES = ("100644", "100755")


def _git(root, args):
    """(returncode, stdout bytes) of one of the three read-only index reads run_staged makes: the staged
    path list (args == STAGED_NAMES), one path's index entry (args == ["ls-files", "--stage", "-z",
    "--", PATH], pathspecs read literally) or one blob by object id (args == ["cat-file", "blob",
    OID]). Each launch is a literal argument list; any other args is a ValueError."""
    if args == STAGED_NAMES:
        child = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--name-only", "-z",
                                "--no-renames"], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    elif len(args) == 5 and args[:4] == ["ls-files", "--stage", "-z", "--"]:
        child = subprocess.run(["git", "--literal-pathspecs", "-C", str(root), "ls-files", "--stage", "-z",
                                "--", args[4]], capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
    elif len(args) == 3 and args[:2] == ["cat-file", "blob"] and OBJECT_ID_RE.fullmatch(args[2]):
        child = subprocess.run(["git", "-C", str(root), "cat-file", "blob", args[2]], capture_output=True,
                               stdin=subprocess.DEVNULL, timeout=60)
    else:
        raise ValueError("unsupported git read {!r}".format(args))
    return child.returncode, child.stdout


def _staged_object(path, listing):
    """The object id of path's single stage-0 index entry given `git ls-files --stage -z` output;
    InputError when the entry is missing, unmerged, or not a regular file (a symlink, gitlink or other
    mode is refused as cannot-evaluate, as a non-regular declared surface is in the other modes)."""
    entries = []
    for record in listing.decode("utf-8", "surrogateescape").split("\0"):
        if not record:
            continue
        meta, _tab, name = record.partition("\t")
        fields = meta.split(" ")
        if name == path and len(fields) == 3:
            entries.append(fields)
    if not entries:
        raise InputError("{}: has no staged index entry (deleted or unreadable)".format(path))
    if len(entries) != 1 or entries[0][2] != "0":
        raise InputError("{}: the index entry is unmerged".format(path))
    mode, oid, _stage = entries[0]
    if mode not in REGULAR_INDEX_MODES:
        raise InputError("{}: is staged as mode {}, not a regular file (a symlink or other non-regular "
                         "entry is refused)".format(path, mode))
    if not OBJECT_ID_RE.fullmatch(oid):
        raise InputError("{}: the index entry names no valid object id".format(path))
    return oid


def run_staged(root, transport=None, git_runner=_pr_state.run_git_origin, git=_git):
    """--staged: judge the staged blob (complete content, unchanged context included) of every declared
    surface the index changes."""
    try:
        config = load_config(root)
        code, out = git(root, STAGED_NAMES)
        if code != 0:
            raise InputError("git diff --cached exited {}".format(code))
        staged = set(out.decode("utf-8", "surrogateescape").split("\0")) - {""}
        pairs = []
        for surface in config["surface"]:
            if surface["path"] not in staged:
                continue
            code, listing = git(root, ["ls-files", "--stage", "-z", "--", surface["path"]])
            if code != 0:
                raise InputError("{}: the index entry cannot be read (git exited {})".format(
                    surface["path"], code))
            code, blob = git(root, ["cat-file", "blob", _staged_object(surface["path"], listing)])
            if code != 0:
                raise InputError("{}: the staged blob cannot be read (git exited {})".format(
                    surface["path"], code))
            if len(blob) > MAX_BYTES:
                raise InputError("{}: staged blob larger than {} bytes".format(surface["path"], MAX_BYTES))
            pairs.append((surface, blob))
        candidates = collect(pairs)
    except (InputError, OSError, subprocess.SubprocessError) as exc:
        print("CANNOT EVALUATE: {}".format(exc), file=sys.stderr)
        return 2
    if not pairs:
        print("PASS: no declared record surface is staged; nothing to judge")
        return 0
    return report(evaluate(candidates, config, root, transport, git_runner))


# ---------------------------------------------------------------------------------------------------
# Self-test (synthetic lifecycle responses only; no network, no real gh).

SUITE_ID = "pending-pr-claims-selftest"
EXECUTED = []
FAILURES = []


def check(name, ok, detail=""):
    if name in EXECUTED:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    EXECUTED.append(name)
    if not ok:
        FAILURES.append("{}: {!r}".format(name, detail))


REPO = "acme/widgets"


def _response(number, state, merged_at=None, repo=REPO, **extra):
    import json
    obj = {"number": number, "state": state, "merged_at": merged_at,
           "html_url": "https://github.com/{}/pull/{}".format(repo, number),
           "base": {"repo": {"full_name": repo}}}
    obj.update(extra)
    return json.dumps(obj).encode("utf-8")


MERGED_AT = "2026-11-01T10:00:00Z"
LIFECYCLE = {"open": ("open", None), "merged": ("closed", MERGED_AT), "closed": ("closed", None)}


class FakeTransport:
    """In-process stand-in for gh: {(repo, number): lifecycle name or (code, stdout, stderr)}."""

    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, repo, number, timeout):
        self.calls.append((repo, number))
        entry = self.table.get((repo, number), (1, b"", b"gh: Not Found (HTTP 404)"))
        if isinstance(entry, str):
            state, merged_at = LIFECYCLE[entry]
            return 0, _response(number, state, merged_at, repo), b""
        if isinstance(entry, BaseException):
            raise entry
        return entry


def _origin_ok(_root):
    return 0, ("https://github.com/" + REPO + ".git\n").encode()


MD_SURFACE = {"path": "NOTES.md", "format": "markdown", "repository": "origin", "role": "source",
              "status-column": "Status"}
TOML_SURFACE = {"path": "changes.toml", "format": "toml-strings", "repository": "origin", "role": "source"}
CONFIG = {"schema-version": 1, "limits": {"max-prs": 100, "call-timeout-seconds": 10,
                                          "deadline-seconds": 120}, "surface": [MD_SURFACE, TOML_SURFACE]}


def _judge(text, table, surface=MD_SURFACE, config=CONFIG, git_runner=_origin_ok):
    """(exit code, Result, transport) for one synthetic surface body."""
    transport = FakeTransport(table)
    candidates = collect([(surface, text.encode("utf-8"))])
    result = evaluate(candidates, config, "/nonexistent", transport, git_runner)
    return result.exit_code(), result, transport


def _numbers(lines):
    return sorted(int(n) for line in lines for n in re.findall(r"#([0-9]+)", line.split(": says ")[-1]
                                                                 .split(" is ")[0]))


def _with_rules_removed(rules, fn):
    """Run fn() with the PATTERNS rows of the given rule ids removed (the red-when-reverted leg)."""
    global PATTERNS
    saved = PATTERNS
    unknown = set(rules) - {row[0] for row in saved}
    if unknown:
        raise ValueError("no such rule {}".format(sorted(unknown)))
    PATTERNS = tuple(row for row in saved if row[0] not in rules)
    try:
        return fn()
    finally:
        PATTERNS = saved


def _with_steps_removed(steps, fn):
    """Run fn() with the NORMALIZE steps of the given names removed (the red-when-reverted leg)."""
    global NORMALIZE
    saved = NORMALIZE
    unknown = set(steps) - {row[0] for row in saved}
    if unknown:
        raise ValueError("no such step {}".format(sorted(unknown)))
    NORMALIZE = tuple(row for row in saved if row[0] not in steps)
    try:
        return fn()
    finally:
        NORMALIZE = saved


def _without_imperative_open(fn):
    """Run fn() with the imperative `open #N` downgrade disabled (the red-when-reverted leg)."""
    global _imperative_open
    saved = _imperative_open
    _imperative_open = lambda _clause, _start: False  # noqa: E731
    try:
        return fn()
    finally:
        _imperative_open = saved


def _without_negation(fn):
    """Run fn() with the cue reading disabled, so no claim is downgraded (the red-when-reverted leg)."""
    return _with_globals({"_cue_before": lambda _clause, _start: None}, fn)


def _with_globals(replacements, fn):
    """Run fn() with the named module globals replaced, restoring them afterwards (a red leg)."""
    names = globals()
    saved = {name: names[name] for name in replacements}
    names.update(replacements)
    try:
        return fn()
    finally:
        names.update(saved)


def _status_table(cell):
    return "| ID | Status |\n| --- | --- |\n| X-1 | {} |\n".format(cell)


# Every grammar rule has one row in _rule_rows: (rule id, a text the rule alone binds to #17, its
# no-flag twin, then the five literal check ids). Each is checked merged, open and unreadable, its twin
# merged, and the text again with the rule removed.


def _rule_rows(m, o):
    kinds = {row[0]: row[1] for row in PATTERNS}
    seen = []
    for (rule, text, twin, merged_id, red_id, open_id, unknown_id, twin_id) in (
            ("status-in-progress", _status_table("In progress: #17"), _status_table("Done: #17"),
             "rule/status-in-progress/merged", "rule/status-in-progress/merged/red-when-reverted", "rule/status-in-progress/open-passes", "rule/status-in-progress/unknown-cannot-evaluate", "rule/status-in-progress/no-flag-twin"),
            ("status-open", _status_table("Open (#17)"), _status_table("Merged (#17)"),
             "rule/status-open/merged", "rule/status-open/merged/red-when-reverted", "rule/status-open/open-passes", "rule/status-open/unknown-cannot-evaluate", "rule/status-open/no-flag-twin"),
            ("pending-verb", "PR #17 remains pending.\n", "PR #17 was pending.\n",
             "rule/pending-verb/merged", "rule/pending-verb/merged/red-when-reverted", "rule/pending-verb/open-passes", "rule/pending-verb/unknown-cannot-evaluate", "rule/pending-verb/no-flag-twin"),
            ("pending-paren", "Fix in PR #17 (pending).\n", "Fix in PR #17 (merged).\n",
             "rule/pending-paren/merged", "rule/pending-paren/merged/red-when-reverted", "rule/pending-paren/open-passes", "rule/pending-paren/unknown-cannot-evaluate", "rule/pending-paren/no-flag-twin"),
            ("open-paren", "Fix in PR #17 (open).\n", "Fix in PR #17 (closed).\n",
             "rule/open-paren/merged", "rule/open-paren/merged/red-when-reverted", "rule/open-paren/open-passes", "rule/open-paren/unknown-cannot-evaluate", "rule/open-paren/no-flag-twin"),
            ("pending-batch", "This ships in the pending batch (#17).\n", "This shipped in the merged batch (#17).\n",
             "rule/pending-batch/merged", "rule/pending-batch/merged/red-when-reverted", "rule/pending-batch/open-passes", "rule/pending-batch/unknown-cannot-evaluate", "rule/pending-batch/no-flag-twin"),
            ("pending-lead", "The pending PR #17 needs a rebase.\n", "The merged PR #17 needed a rebase.\n",
             "rule/pending-lead/merged", "rule/pending-lead/merged/red-when-reverted", "rule/pending-lead/open-passes", "rule/pending-lead/unknown-cannot-evaluate", "rule/pending-lead/no-flag-twin"),
            ("open-verb", "PR #17 is still open.\n", "PR #17 is no longer open.\n",
             "rule/open-verb/merged", "rule/open-verb/merged/red-when-reverted", "rule/open-verb/open-passes", "rule/open-verb/unknown-cannot-evaluate", "rule/open-verb/no-flag-twin"),
            ("open-lead", "The open PR #17 needs a rebase.\n", "The closed PR #17 needed a rebase.\n",
             "rule/open-lead/merged", "rule/open-lead/merged/red-when-reverted", "rule/open-lead/open-passes", "rule/open-lead/unknown-cannot-evaluate", "rule/open-lead/no-flag-twin"),
            ("under-review", "PR #17 remains under review.\n", "PR #17 was under review.\n",
             "rule/under-review/merged", "rule/under-review/merged/red-when-reverted", "rule/under-review/open-passes", "rule/under-review/unknown-cannot-evaluate", "rule/under-review/no-flag-twin"),
            ("awaiting-review", "PR #17 is awaiting review.\n", "PR #17 was awaiting review.\n",
             "rule/awaiting-review/merged", "rule/awaiting-review/merged/red-when-reverted", "rule/awaiting-review/open-passes", "rule/awaiting-review/unknown-cannot-evaluate", "rule/awaiting-review/no-flag-twin"),
            ("in-progress-verb", "PR #17 is in progress.\n", "PR #17 was in progress.\n",
             "rule/in-progress-verb/merged", "rule/in-progress-verb/merged/red-when-reverted", "rule/in-progress-verb/open-passes", "rule/in-progress-verb/unknown-cannot-evaluate", "rule/in-progress-verb/no-flag-twin"),
            ("waiting-to-merge", "Waiting for #17 to merge.\n", "We waited for #17 to merge.\n",
             "rule/waiting-to-merge/merged", "rule/waiting-to-merge/merged/red-when-reverted", "rule/waiting-to-merge/open-passes", "rule/waiting-to-merge/unknown-cannot-evaluate", "rule/waiting-to-merge/no-flag-twin"),
            ("awaiting-merge", "Awaiting merge of #17.\n", "Awaiting deployment of #17.\n",
             "rule/awaiting-merge/merged", "rule/awaiting-merge/merged/red-when-reverted", "rule/awaiting-merge/open-passes", "rule/awaiting-merge/unknown-cannot-evaluate", "rule/awaiting-merge/no-flag-twin"),
            ("until-merge", "Hold the release until #17 merges.\n", "Held the release until #17 merged.\n",
             "rule/until-merge/merged", "rule/until-merge/merged/red-when-reverted", "rule/until-merge/open-passes", "rule/until-merge/unknown-cannot-evaluate", "rule/until-merge/no-flag-twin"),
            ("once-merge", "Rebase once #17 lands.\n", "Rebased once #17 landed.\n",
             "rule/once-merge/merged", "rule/once-merge/merged/red-when-reverted", "rule/once-merge/open-passes", "rule/once-merge/unknown-cannot-evaluate", "rule/once-merge/no-flag-twin"),
            ("has-yet-to-merge", "PR #17 has yet to merge.\n", "PR #17 has merged.\n",
             "rule/has-yet-to-merge/merged", "rule/has-yet-to-merge/merged/red-when-reverted", "rule/has-yet-to-merge/open-passes", "rule/has-yet-to-merge/unknown-cannot-evaluate", "rule/has-yet-to-merge/no-flag-twin"),
            ("yet-to-be-merged", "PR #17 is yet to be merged.\n", "PR #17 is merged.\n",
             "rule/yet-to-be-merged/merged", "rule/yet-to-be-merged/merged/red-when-reverted", "rule/yet-to-be-merged/open-passes", "rule/yet-to-be-merged/unknown-cannot-evaluate", "rule/yet-to-be-merged/no-flag-twin"),
            ("not-yet-merged", "#17 is not yet merged.\n", "#17 is merged now.\n",
             "rule/not-yet-merged/merged", "rule/not-yet-merged/merged/red-when-reverted", "rule/not-yet-merged/open-passes", "rule/not-yet-merged/unknown-cannot-evaluate", "rule/not-yet-merged/no-flag-twin"),
            ("has-not-merged", "PR #17 hasn't merged yet.\n", "PR #17 has merged since.\n",
             "rule/has-not-merged/merged", "rule/has-not-merged/merged/red-when-reverted", "rule/has-not-merged/open-passes", "rule/has-not-merged/unknown-cannot-evaluate", "rule/has-not-merged/no-flag-twin"),
            ("waiting-dependency", "Still waiting on #17.\n", "Waited on #17 last week.\n",
             "rule/waiting-dependency/merged", "rule/waiting-dependency/merged/red-when-reverted", "rule/waiting-dependency/open-passes", "rule/waiting-dependency/unknown-cannot-evaluate", "rule/waiting-dependency/no-flag-twin"),
            ("awaiting-dependency", "Awaiting #17.\n", "Awaited #17 last week.\n",
             "rule/awaiting-dependency/merged", "rule/awaiting-dependency/merged/red-when-reverted", "rule/awaiting-dependency/open-passes", "rule/awaiting-dependency/unknown-cannot-evaluate", "rule/awaiting-dependency/no-flag-twin"),
            ("blocked-dependency", "Blocked on #17.\n", "Unblocked by #17.\n",
             "rule/blocked-dependency/merged", "rule/blocked-dependency/merged/red-when-reverted", "rule/blocked-dependency/open-passes", "rule/blocked-dependency/unknown-cannot-evaluate", "rule/blocked-dependency/no-flag-twin"),
            ("stacked", "This branch is stacked on #17.\n", "This branch was unstacked from #17.\n",
             "rule/stacked/merged", "rule/stacked/merged/red-when-reverted", "rule/stacked/open-passes", "rule/stacked/unknown-cannot-evaluate", "rule/stacked/no-flag-twin"),
    ):
        seen.append((rule, merged_id, red_id, open_id, unknown_id, twin_id))
        hard = kinds.get(rule) == HARD
        code, res, t = _judge(text, m)
        flagged = (code == 1 and len(res.hard) == 1) if hard else (
            code == 0 and len(res.warnings) == 1 and not res.hard)
        check(merged_id, flagged and t.calls == [(REPO, 17)], (code, res.hard, res.warnings))
        red = _with_rules_removed({rule}, lambda: _judge(text, m)[1]) if rule in kinds else None
        check(red_id, red is not None and (not red.hard if hard else not red.warnings),
              None if red is None else (red.hard, red.warnings))
        code, res, _t = _judge(text, o)
        check(open_id, code == 0 and not res.hard and not res.warnings, (code, res.hard, res.warnings))
        code, res, _t = _judge(text, {})
        check(unknown_id, code == 2 and not res.hard, res.unknown)
        code, res, _t = _judge(twin, m)
        check(twin_id, code == 0 and not res.hard and not res.warnings, (code, res.hard, res.warnings))
    ids_match = all(all(cid.startswith("rule/{}/".format(row[0])) for cid in row[1:]) for row in seen)
    check("rule/every-rule-has-a-row", ids_match and sorted(row[0] for row in seen) == sorted(kinds),
          sorted(set(kinds) ^ {row[0] for row in seen}))


# A definition inside a fenced block or an HTML comment, met first, must not shadow the live one after
# the claim; backtick lines six columns deep in a list item (four past its content column) are indented
# code, not a fence, so the list prose between them is read.
FENCED_DEFINITION = ("```md\n[pr17]: https://github.com/acme/widgets/issues/17\n```\n\n[#17][pr17] is pending.\n\n"
                     "[pr17]: https://github.com/acme/widgets/pull/17\n")
COMMENT_DEFINITION = ("<!--\n\n[pr17]: https://github.com/acme/widgets/issues/17\n\n-->\n\n[#17][pr17] is pending.\n\n"
                      "[pr17]: https://github.com/acme/widgets/pull/17\n")
OVERINDENTED_BACKTICKS = "- Item\n\n      ```\n\n  PR #17 is pending.\n\n      ```\n"
# The verb alternation before the modifier suffix applied to `still` and `currently` (a red leg).
VERB_BEFORE_STILL_MODIFIERS = (r"(?:(?:is|are|remains?|stays?|(?:has|have)\s+been)"
                               r"(?:\s+(?:still|currently|now|also|both)){0,2}|still|currently)")


def _no_block_context(lines):
    """block_context as it was before it existed for definitions: every non-blank line is read, and one
    shaped like a definition is collected wherever it stands (a red-when-reverted leg)."""
    return [(BLANK, "") if not line.strip() else (DEFINITION if LINK_DEFINITION_RE.match(line) else PARA, line)
            for line in lines]


def _block_context_rows(m):
    """The red-when-reverted legs of the block context rows and the verb modifier row, and an HTML
    comment that is never closed (cannot-evaluate)."""
    for name, replacements, text in (
            ("markdown/fenced-definition-not-collected/red-when-reverted", {"block_context": _no_block_context},
             FENCED_DEFINITION),
            ("markdown/comment-definition-not-collected/red-when-reverted",
             {"_comment_start": lambda _text: None}, COMMENT_DEFINITION),
            ("markdown/overindented-backticks-in-list-not-a-fence/red-when-reverted",
             {"FENCE_MAX_RELATIVE_INDENT": 99}, OVERINDENTED_BACKTICKS),
            ("verb/still-currently-open/red-when-reverted",
             {"PATTERNS": tuple((rule, kind, phrase, re.compile(regex.pattern.replace(
                 VERB, VERB_BEFORE_STILL_MODIFIERS), regex.flags), contexts)
                 for rule, kind, phrase, regex, contexts in PATTERNS)}, "#17 still currently open.\n")):
        try:
            code, res, t = _with_globals(replacements, lambda: _judge(text, m))
            ok, detail = code == 0 and t.calls == [] and not res.hard, (code, t.calls, res.hard)
        except InputError as exc:
            ok, detail = False, str(exc)
        check(name, ok, detail)
    try:
        _judge("Intro.\n\n<!-- PR #17 is pending\n\nnever closed\n", m)
        got = None
    except InputError as exc:
        got = str(exc)
    check("markdown/unclosed-comment-cannot-evaluate", got is not None and "never closed" in got, got)


def _fix_rows(m, o):
    """Reference spellings, Markdown structure, negation and binding fixes, each with a twin on the
    other side and, where a step or function carries the fix, its red-when-reverted leg."""
    two = {(REPO, 17): "merged", (REPO, 18): "merged"}
    link = "[#17](https://github.com/acme/widgets/pull/17) is pending.\n"
    for name, text, table, want, calls in (
            ("ref/markdown-link-hash", link, m, 1, [(REPO, 17)]),
            ("ref/markdown-link-words", "[PR #17](https://github.com/acme/widgets/pull/17) remains open.\n",
             m, 1, [(REPO, 17)]),
            ("ref/markdown-link-target-repository", "[#17](https://github.com/peer/repo/pull/17) is pending.\n",
             {("peer/repo", 17): "merged"}, 1, [("peer/repo", 17)]),
            ("ref/markdown-link-no-claim", "[#17](https://github.com/acme/widgets/pull/17) merged.\n", m, 0, []),
            ("ref/autolink", "<https://github.com/acme/widgets/pull/17> is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/glued-pr", "PR#17 is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/glued-pr-no-claim", "PR#17 merged.\n", m, 0, []),
            ("ref/underscore-emphasis", "_#17_ is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/underscore-emphasis-no-claim", "_#17_ merged.\n", m, 0, []),
            ("ref/slash-list", "#17/#18 are pending.\n", two, 1, [(REPO, 17), (REPO, 18)]),
            ("ref/slash-list-no-claim", "#17/#18 merged.\n", two, 0, []),
            ("ref/url-files-suffix", "https://github.com/acme/widgets/pull/17/files is pending.\n", m, 1,
             [(REPO, 17)]),
            ("ref/url-files-suffix-no-claim", "https://github.com/acme/widgets/pull/17/files merged.\n", m, 0, []),
            ("ref/issue-not-a-pull-request", "Issue #17 remains open.\n", {}, 0, []),
            ("ref/issue-list-not-pull-requests", "Issues #17 and #18 remain open.\n", {}, 0, []),
            ("ref/issue-link-not-a-pull-request",
             "[#17](https://github.com/acme/widgets/issues/17) remains open.\n", {}, 0, []),
            ("ref/pull-request-beside-issue", "Issue #18 is fixed; PR #17 remains open.\n", m, 1, [(REPO, 17)]),
            ("format/strikethrough-not-current", "~~PR #17 is pending~~ merged.\n", m, 0, []),
            ("format/strikethrough-word-removed", "PR #17 is ~~open~~ pending.\n", m, 1, [(REPO, 17)]),
            ("format/html-comment-not-current", "<!-- PR #17 is pending -->\n", m, 0, []),
            ("format/text-beside-html-comment", "<!-- note --> PR #17 is pending.\n", m, 1, [(REPO, 17)]),
            ("history/straight-single-quoted-span", "Previously recorded: 'PR #17 is pending.'\n", m, 0, []),
            ("history/straight-quote-then-current-claim",
             "Previously recorded: 'PR #17 isn't pending.' Now PR #18 remains open.\n",
             {(REPO, 18): "merged"}, 1, [(REPO, 18)]),
            ("markdown/indented-code-not-read", "Example:\n\n    PR #17 is pending\n", m, 0, []),
            ("markdown/indented-list-continuation-read", "- Item one\n\n    PR #17 is pending\n", m, 1,
             [(REPO, 17)]),
            ("markdown/inline-triple-backticks-not-a-fence", "```x``` is inline code.\n\nPR #17 is pending.\n",
             m, 1, [(REPO, 17)]),
            ("markdown/info-string-fence-skipped", "```python\nPR #17 is pending\n```\n", m, 0, []),
            ("markdown/table-without-outer-pipes", "ID | Status\n--- | ---\nX-1 | In progress (#17)\n", m, 1,
             [(REPO, 17)]),
            ("markdown/table-without-pipes-other-column",
             "ID | Status | Item\n--- | --- | ---\nX-1 | Planned | In progress (#17)\n", m, 0, []),
            ("negation/still-waiting-binds", "We are still waiting for #17 to merge.\n", m, 1, [(REPO, 17)]),
            ("negation/do-not-merge-until-binds", "Do not merge until #17 merges.\n", m, 1, [(REPO, 17)]),
            ("negation/present-perfect-binds", "We have been waiting for #17 to merge.\n", m, 1, [(REPO, 17)]),
            ("cue/contracted-present-perfect-binds", "It\u2019s been waiting for #17 to merge.\n", m, 1,
             [(REPO, 17)]),
            ("cue/cue-in-earlier-words-not-read", "We merged the fix and #17 remains open.\n", m, 1, [(REPO, 17)]),
            ("ref/reference-style-link", "[#17][pr17] is pending.\n\n[pr17]: https://github.com/peer/repo/pull/17\n",
             {("peer/repo", 17): "merged"}, 1, [("peer/repo", 17)]),
            ("ref/reference-style-link-no-claim",
             "[#17][pr17] merged.\n\n[pr17]: https://github.com/peer/repo/pull/17\n", m, 0, []),
            ("ref/shortcut-reference-link", "[#17] is pending.\n\n[#17]: https://github.com/peer/repo/pull/17\n",
             {("peer/repo", 17): "merged"}, 1, [("peer/repo", 17)]),
            ("ref/bracketed-reference-binds", "[#17] is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/autolink-trailing-slash", "<https://github.com/acme/widgets/pull/17/> is pending.\n", m, 1,
             [(REPO, 17)]),
            ("ref/url-trailing-slash", "https://github.com/acme/widgets/pull/17/ is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/http-url", "http://github.com/acme/widgets/pull/17 is pending.\n", m, 1, [(REPO, 17)]),
            ("ref/html-link", "<a href=\"https://github.com/peer/repo/pull/17\">#17</a> is pending.\n",
             {("peer/repo", 17): "merged"}, 1, [("peer/repo", 17)]),
            ("ref/html-link-no-claim", "<a href=\"https://github.com/peer/repo/pull/17\">#17</a> merged.\n", m, 0,
             []),
            ("markdown/fence-in-list-item-skipped", "- Item\n\n    ```\n    PR #17 is pending\n    ```\n", m,
             0, []),
            ("markdown/indented-code-in-list-item-skipped", "- Item\n\n      PR #17 is pending\n", m, 0, []),
            ("markdown/list-text-after-fence-read", "- Item\n\n  ```\n  code\n  ```\n\n  PR #17 is pending.\n", m,
             1, [(REPO, 17)]),
            ("status/no-separator-binds", _status_table("In progress #17"), m, 1, [(REPO, 17)]),
            ("status/emphasised-header-binds", "| ID | **Status** |\n| --- | --- |\n| X-1 | In progress (#17) |\n",
             m, 1, [(REPO, 17)]),
            ("status/byte-order-mark-binds", "\ufeff" + _status_table("In progress (#17)"), m, 1, [(REPO, 17)]),
            ("bind/ampersand-list-binds-both", "#17 & #18 are pending.\n", two, 1, [(REPO, 17), (REPO, 18)]),
            ("bind/singular-issue-then-pull-request", "Closes issue #12, and #17 is still open.\n", m, 1,
             [(REPO, 17)]),
            ("verb/present-perfect-open", "PR #17 has been open since March.\n", m, 1, [(REPO, 17)]),
            ("verb/both-still-open", "PRs #17 and #18 are both still open.\n", two, 1, [(REPO, 17), (REPO, 18)]),
            ("verb/also-pending", "PR #17 is also pending.\n", m, 1, [(REPO, 17)]),
            ("verb/past-perfect-open-no-claim", "PR #17 had been open since March.\n", m, 0, []),
            ("history/curly-span-apostrophe-inside",
             "Previously recorded: \u2018PR #18 isn\u2019t merged; PR #17 is pending\u2019\n", two, 0, []),
            ("history/straight-span-apostrophe-inside",
             "Previously recorded: 'PR #18 isn't merged; PR #17 is pending.'\n", two, 0, []),
            ("format/single-tilde-strikethrough-not-current", "~PR #17 is pending~ merged.\n", m, 0, []),
            ("format/approximate-tildes-not-struck", "Takes ~5 min; PR #17 is pending, ~10 min left.\n", m, 1,
             [(REPO, 17)]),
            ("ref/qualified-issue-not-a-pull-request", "Issue acme/widgets#17 remains open.\n", {}, 0, []),
            ("ref/mixed-issue-list-not-pull-requests", "Issues #18 and acme/widgets#17 remain open.\n", {}, 0,
             []),
            ("ref/pull-request-beside-qualified-issue",
             "Issue acme/widgets#18 is fixed; acme/widgets#17 remains open.\n", m, 1, [(REPO, 17)]),
            ("ref/markdown-link-ascii-fragment",
             "[#17](https://github.com/peer/repo/pull/17#issuecomment-1) is pending.\n",
             {("peer/repo", 17): "merged"}, 1, [("peer/repo", 17)]),
            ("ref/markdown-link-non-ascii-fragment-is-text",
             "[#17](https://github.com/peer/repo/pull/17#" + chr(0x661) + ") is pending.\n",
             {(REPO, 17): "open", ("peer/repo", 17): "merged"}, 0, [(REPO, 17)]),
            ("markdown/fenced-definition-not-collected", FENCED_DEFINITION, m, 1, [(REPO, 17)]),
            ("markdown/fenced-definition-not-collected-closed", FENCED_DEFINITION, {(REPO, 17): "closed"}, 1,
             [(REPO, 17)]),
            ("markdown/comment-definition-not-collected", COMMENT_DEFINITION, m, 1, [(REPO, 17)]),
            ("markdown/overindented-backticks-in-list-not-a-fence", OVERINDENTED_BACKTICKS, m, 1, [(REPO, 17)]),
            ("markdown/overindented-backticks-in-list-not-a-fence-closed", OVERINDENTED_BACKTICKS,
             {(REPO, 17): "closed"}, 1, [(REPO, 17)]),
            ("markdown/fence-three-past-item-column-skipped", "- Item\n\n     ```\n     PR #17 is pending\n     ```\n",
             m, 0, []),
            ("markdown/comment-spanning-blank-line-not-read", "<!--\nPR #17 is pending.\n\nPR #17 is open.\n-->\n", m,
             0, []),
            ("markdown/inline-comment-across-lines-read", "PR #17 <!-- a note\nthat wraps --> is pending.\n", m, 1,
             [(REPO, 17)]),
            ("markdown/unclosed-inline-comment-is-text", "PR #17 is pending <!-- no end\n\nNext.\n", m, 1,
             [(REPO, 17)]),
            ("verb/still-currently-open", "#17 still currently open.\n", m, 1, [(REPO, 17)]),
            ("verb/still-currently-merged-no-claim", "#17 still currently merged.\n", m, 0, []),
            ("format/currently-pending", "PR #17 is currently pending.\n", m, 1, [(REPO, 17)]),
            ("format/now-open", "PR #17 is now open.\n", m, 1, [(REPO, 17)]),
            ("format/currently-merged-no-claim", "PR #17 is currently merged.\n", m, 0, [])):
        try:
            code, res, t = _judge(text, table)
            ok, detail = code == want and sorted(t.calls) == calls, (code, t.calls, res.hard, res.unknown)
        except InputError as exc:
            ok, detail = False, str(exc)
        check(name, ok, detail)
    _cue_rows(m)
    code, res, _t = _judge("Earlier note:\n\n| A | B |\n| --- | --- |\n| x | y |\n\n> PR #17 is pending\n", m)
    check("markdown/lead-in-not-across-table", code == 2 and "blockquote" in " ".join(res.unknown), res.unknown)
    for name, red_name, text in (
            ("bind/imperative-open-warns", "bind/imperative-open-warns/red-when-reverted",
             "Open #17 to see the discussion.\n"),
            ("bind/imperative-open-after-comma-warns", "bind/imperative-open-after-comma-warns/red-when-reverted",
             "To review it, open #17 in a browser.\n")):
        code, res, _t = _judge(text, m)
        check(name, code == 0 and not res.hard and len(res.warnings) == 1, (code, res.hard, res.warnings))
        red = _without_imperative_open(lambda: _judge(text, m)[0])
        check(red_name, red == 1, red)
    code, res, _t = _judge(_status_table("Open #17"), m)
    check("bind/status-cell-open-still-hard", code == 1 and len(res.hard) == 1, res.hard)
    code, res, t = _judge("#17 remains open and the pending PR needs review.\n", o)
    check("ambiguous/beside-named-claim", code == 2 and not res.hard and t.calls == [(REPO, 17)]
          and "ambiguous" in " ".join(res.unknown), res.unknown)
    code, res, _t = _judge("#17 remains open and pending PR #18 needs review.\n",
                           {(REPO, 17): "open", (REPO, 18): "open"})
    check("ambiguous/named-only-passes", code == 0 and not res.unknown, res.unknown)
    for name, text, steps, table in (
            ("ref/markdown-link-hash/red-when-reverted", link, {"markdown-link"}, m),
            ("ref/glued-pr/red-when-reverted", "PR#17 is pending.\n", {"glued-pr"}, m),
            ("ref/underscore-emphasis/red-when-reverted", "_#17_ is pending.\n", {"underscore-emphasis"}, m),
            ("ref/slash-list/red-when-reverted", "#17/#18 are pending.\n", {"slash-list"}, two),
            ("format/strikethrough-not-current/red-when-reverted", "~~PR #17 is pending~~ merged.\n",
             {"strikethrough"}, m),
            ("format/html-comment-not-current/red-when-reverted", "<!-- PR #17 is pending -->\n",
             {"html-comment"}, m),
            ("history/straight-single-quoted-span/red-when-reverted",
             "Previously recorded: 'PR #17 is pending.'\n", {"history-span"}, m),
            ("ref/bracketed-reference-binds/red-when-reverted", "[#17] is pending.\n", {"bracket"}, m),
            ("ref/html-link/red-when-reverted",
             "<a href=\"https://github.com/peer/repo/pull/17\">#17</a> is pending.\n", {"html-link"},
             {("peer/repo", 17): "merged", (REPO, 17): "open"}),
            ("history/curly-span-apostrophe-inside/red-when-reverted",
             "Previously recorded: \u2018PR #18 isn\u2019t merged; PR #17 is pending\u2019\n", {"apostrophe"}, two),
            ("format/single-tilde-strikethrough-not-current/red-when-reverted", "~PR #17 is pending~ merged.\n",
             {"strikethrough"}, m)):
        code, res, _t = _with_steps_removed(steps, lambda: _judge(text, table))
        # Reverted, a flag row stops flagging (0) and a no-flag row starts flagging (1).
        want = 1 if name.split("/red-when-reverted")[0] in (
            "format/strikethrough-not-current", "format/html-comment-not-current",
            "history/straight-single-quoted-span", "history/curly-span-apostrophe-inside",
            "format/single-tilde-strikethrough-not-current") else 0
        check(name, code == want and (not res.hard if want == 0 else len(res.hard) >= 1), (code, res.hard))
    code, res, t = _with_steps_removed({"issue-reference"}, lambda: _judge("Issue #17 remains open.\n", {}))
    check("ref/issue-not-a-pull-request/red-when-reverted", code == 2 and t.calls == [(REPO, 17)], res.unknown)
    # The straight-quote alternative of the history span: the naive '[^']*' form ends the span at the
    # apostrophe of isn't, so the claim after it is read (red); the shipped form keeps it inside.
    code, res, _t = _with_globals(
        {"NORMALIZE": tuple((n, _history_span_re(r"'[^']*'") if n == "history-span" else r, s)
                            for n, r, s in NORMALIZE)},
        lambda: _judge("Previously recorded: 'PR #18 isn't merged; PR #17 is pending.'\n", two))
    check("history/straight-span-apostrophe-inside/red-when-reverted", code == 1 and len(res.hard) >= 1,
          (code, res.hard))
    code, _res, t = _with_globals({"_resolve_reference_links": lambda prose, _definitions: prose},
                                  lambda: _judge("[#17][pr17] is pending.\n\n"
                                                 "[pr17]: https://github.com/peer/repo/pull/17\n",
                                                 {("peer/repo", 17): "merged"}))
    check("ref/reference-style-link/red-when-reverted", code == 0 and t.calls == [], (code, t.calls))
    _block_context_rows(m)


def _cue_rows(m):
    """A claim after a negation, past-tense, conditional or question cue: a warning naming the clause
    and the pull request when it has merged (never a silent pass), a clean pass when it is open, and
    cannot-evaluate when its state is unreadable. With the cue reading removed, each is a finding."""
    three = {(REPO, n): "merged" for n in (16, 17, 18)}
    opened = {key: "open" for key in three}
    open_bad, unknown_bad = [], []
    for name, text, count in (
        ("negation/not-waiting", "We are not waiting for #17 to merge.\n", 1),
        ("negation/past-waiting", "We were waiting for #17 to merge.\n", 1),
        ("negation/contracted-not-waiting", "It isn't waiting on #17.\n", 1),
        ("negation/past-perfect-waiting", "We had been waiting for #17 to merge.\n", 1),
        ("negation/not-been-waiting", "We haven't been waiting for #17 to merge.\n", 1),
        ("negation/not-only-binds", "We are not only waiting for #17 to merge.\n", 1),
        ("negation/past-only-waiting", "We were only waiting for #17 to merge.\n", 1),
        ("negation/not-until-binds", "Not until #17 merges can we ship.\n", 1),
        ("negation/not-waiting-to-merge", "Not waiting for #17 to merge.\n", 1),
        ("negation/present-perfect-not-only-warns", "We have not only been waiting for #17 to merge.\n", 1),
        ("negation/curly-past-perfect-warns", "We\u2019d been waiting for #17 to merge.\n", 1),
        ("negation/straight-past-perfect-warns", "We'd been waiting for #17 to merge.\n", 1),
        ("negation/past-not-only-warns", "We were not only waiting for #17 to merge.\n", 1),
        ("negation/past-perfect-not-only-warns", "We had been not only waiting for #17 to merge.\n", 1),
        ("negation/cannot-wait-warns", "We cannot wait for #17 to merge.\n", 1),
        ("negation/contracted-cannot-wait-warns", "We can't wait for #17 to merge.\n", 1),
        ("cue/previously-pending-warns", "The previously pending PR #17 has merged.\n", 1),
        ("cue/formerly-open-warns", "The formerly open PR #17 was closed without merging.\n", 1),
        ("cue/once-pending-warns", "The once-pending PR #17 merged on Monday.\n", 1),
        ("cue/neither-nor-warns", "Neither #16 nor #17 is still open.\n", 1),
        ("cue/none-of-warns", "None of #16, #17 and #18 remain open.\n", 3),
        ("cue/question-warns", "Was #17 still open at the freeze?\n", 1),
        ("cue/conditional-warns", "If #17 is still open, rebase it.\n", 1),
        ("cue/whether-warns", "Check whether #17 is still open.\n", 1)):
        code, res, _t = _judge(text, three)
        named = all("acme/widgets#1" in line and "after the cue" in line for line in res.warnings)
        check(name, code == 0 and not res.hard and len(res.warnings) == count and named,
              (code, res.hard, res.warnings))
        code, res, _t = _judge(text, opened)
        if code != 0 or res.hard or res.warnings:
            open_bad.append((name, code, res.hard, res.warnings))
        code, res, _t = _judge(text, {})
        if code != 2 or res.hard:
            unknown_bad.append((name, code, res.hard))
    check("cue/every-cue-row-open-passes", not open_bad, open_bad)
    check("cue/every-cue-row-unknown-cannot-evaluate", not unknown_bad, unknown_bad)
    code, res, _t = _judge("We are not waiting for #17 to merge.\n", three)
    check("cue/warning-names-sentence-and-pull-request", len(res.warnings) == 1
          and "We are not waiting for #17 to merge" in res.warnings[0]
          and "acme/widgets#17" in res.warnings[0] and "MERGED" in res.warnings[0], res.warnings)
    for name, text in (("cue/no-pending-prs-warns", "There are no pending PRs.\n"),
                       ("cue/merged-all-pending-prs-warns", "Merged all pending pull requests for 1.2.\n")):
        code, res, t = _judge(text, three)
        check(name, code == 0 and not res.unknown and len(res.warnings) == 1 and t.calls == []
              and "not named" in res.warnings[0], (code, res.unknown, res.warnings))
    for name, text, want in (
            ("negation/not-waiting/red-when-reverted", "We are not waiting for #17 to merge.\n", 1),
            ("negation/past-waiting/red-when-reverted", "We were waiting for #17 to merge.\n", 1),
            ("cue/neither-nor-warns/red-when-reverted", "Neither #16 nor #17 is still open.\n", 1),
            ("negation/present-perfect-not-only-warns/red-when-reverted",
             "We have not only been waiting for #17 to merge.\n", 1),
            ("cue/no-pending-prs-warns/red-when-reverted", "There are no pending PRs.\n", 2)):
        red = _without_negation(lambda: _judge(text, three)[0])
        check(name, red == want, red)
    code, res, _t = _with_steps_removed({"apostrophe"}, lambda: _judge("We\u2019d been waiting for #17 to merge.\n",
                                                                         three))
    check("negation/curly-past-perfect-warns/red-when-reverted", code == 1 and len(res.hard) == 1,
          (code, res.hard))


def _self_test():
    import contextlib
    import io
    import tempfile
    import time
    m = {(REPO, 17): "merged"}
    c = {(REPO, 17): "closed"}
    o = {(REPO, 17): "open"}

    # --- lifecycle outcomes for one hard claim ---
    code, res, _t = _judge("PR #17 remains pending.\n", m)
    check("state/merged-hard-finding", code == 1 and len(res.hard) == 1 and "MERGED" in res.hard[0], res.hard)
    red = _with_rules_removed({"pending-verb"}, lambda: _judge("PR #17 remains pending.\n", m)[0])
    check("state/merged-hard-finding/red-when-reverted", red == 0, red)
    code, res, _t = _judge("PR #17 remains pending.\n", c)
    check("state/closed-hard-finding-names-closed", code == 1 and "CLOSED" in res.hard[0]
          and "MERGED" not in res.hard[0], res.hard)
    code, res, _t = _judge("PR #17 remains pending.\n", o)
    check("state/open-passes", code == 0 and not res.hard and not res.unknown, (code, res.hard))
    code, res, _t = _judge("PR #17 remains pending.\n", {})
    check("state/unknown-404-cannot-evaluate", code == 2 and not res.hard and "404" in res.unknown[0],
          res.unknown)

    # --- reference forms ---
    for name, text in (("ref/pr-colon", "Fix is pending pr:17.\n"), ("ref/pr-colon-hash", "Fix is pending pr:#17.\n"),
                       ("ref/pull-request-words", "pull request #17 is still pending\n"),
                       ("ref/github-url", "https://github.com/acme/widgets/pull/17 is pending\n")):
        code, _res, t = _judge(text, m)
        check(name, code == 1 and t.calls == [(REPO, 17)], (code, t.calls))
    code, _res, t = _judge("PR 17 remains pending\n", m)
    check("ref/bare-number-not-a-reference", code == 0 and t.calls == [], (code, t.calls))
    code, _res, t = _judge("peer/repo#17 is pending\n", {("peer/repo", 17): "merged", (REPO, 17): "open"})
    check("ref/explicit-repository-never-local", code == 1 and t.calls == [("peer/repo", 17)], t.calls)

    # --- batch and lists ---
    text = "This is stacked on the pending batch (#17, #18).\n"
    code, res, _t = _judge(text, {(REPO, 17): "closed", (REPO, 18): "closed"})
    check("batch/both-closed-two-findings", code == 1 and _numbers(res.hard) == [17, 18], res.hard)
    red = _with_rules_removed({"pending-batch"},
                              lambda: _judge(text, {(REPO, 17): "closed", (REPO, 18): "closed"})[1])
    check("batch/both-closed-two-findings/red-when-reverted", not red.hard, red.hard)
    code, res, _t = _judge(text, {(REPO, 17): "open", (REPO, 18): "merged"})
    check("batch/only-stale-member", code == 1 and _numbers(res.hard) == [18], res.hard)
    code, res, t = _judge("#17 merged; #18 remains pending\n", {(REPO, 17): "merged", (REPO, 18): "merged"})
    check("bind/semicolon-binds-only-second", code == 1 and t.calls == [(REPO, 18)], t.calls)
    code, res, t = _judge("#17 and #18 remain open\n", {(REPO, 17): "merged", (REPO, 18): "closed"})
    check("bind/coordinated-list-binds-both", code == 1 and _numbers(res.hard) == [17, 18], res.hard)
    red = _with_rules_removed({"open-verb"}, lambda: _judge("#17 and #18 remain open\n",
                                                       {(REPO, 17): "merged", (REPO, 18): "closed"})[0])
    check("bind/coordinated-list-binds-both/red-when-reverted", red == 0, red)
    code, res, _t = _judge("The open pull requests #17 and #18 carry it.\n",
                           {(REPO, 17): "merged", (REPO, 18): "open"})
    check("bind/open-noun-phrase", code == 1 and _numbers(res.hard) == [17], res.hard)
    code, _res, t = _judge("Leave open #17 for the maintainer.\n", m)
    check("bind/instruction-not-a-claim", code == 0 and t.calls == [], t.calls)

    # --- warnings versus hard findings ---
    code, res, _t = _judge("This branch is stacked on #17.\n", m)
    check("warn/stacked-merged-is-warning", code == 0 and len(res.warnings) == 1 and not res.hard,
          (code, res.warnings))
    red = _with_rules_removed({"stacked"}, lambda: _judge("This branch is stacked on #17.\n", m)[1])
    check("warn/stacked-merged-is-warning/red-when-reverted", not red.warnings, red.warnings)
    code, res, _t = _judge("Still waiting on #17.\n", c)
    check("warn/waiting-on-closed-is-warning", code == 0 and len(res.warnings) == 1, res.warnings)
    code, res, _t = _judge("Waiting for #17 to merge.\n", c)
    check("hard/waiting-to-merge-closed", code == 1 and len(res.hard) == 1 and not res.warnings,
          (res.hard, res.warnings))
    red = _with_rules_removed({"waiting-to-merge"}, lambda: _judge("Waiting for #17 to merge.\n", c)[1])
    check("hard/waiting-to-merge-closed/red-when-reverted", not red.hard and len(red.warnings) == 1,
          (red.hard, red.warnings))
    code, res, _t = _judge("#17 is not yet merged\n", m)
    check("hard/not-yet-merged", code == 1, res.hard)
    code, res, _t = _judge("PR #17 is under review\n", c)
    check("hard/under-review", code == 1, res.hard)

    # --- the TODO Status cell ---
    table = "| ID | Status | Item |\n| --- | --- | --- |\n| X-1 | In progress (#17, #18) | Do it |\n"
    code, res, t = _judge(table, {(REPO, 17): "open", (REPO, 18): "merged"})
    check("status/in-progress-binds-both", code == 1 and sorted(t.calls) == [(REPO, 17), (REPO, 18)]
          and _numbers(res.hard) == [18], (t.calls, res.hard))
    red = _with_rules_removed({"status-in-progress"},
                              lambda: _judge(table, {(REPO, 17): "open", (REPO, 18): "merged"})[0])
    check("status/in-progress-binds-both/red-when-reverted", red == 0, red)
    code, _res, t = _judge("| ID | Status | Item |\n| --- | --- | --- |\n| X-1 | Planned | In progress (#17) |\n", m)
    check("status/only-the-status-column", code == 0 and t.calls == [], t.calls)
    code, _res, t = _judge("- [ ] Tidy the widget (fixed by #17)\n", m)
    check("checkbox/open-checkbox-no-claim", code == 0 and t.calls == [], t.calls)

    # --- what must not trigger ---
    for name, text in (
            ("quiet/deployment-pending-after-merge", "Deployment is pending after #17 merged.\n"),
            ("quiet/waiting-for-deployment", "Waiting for deployment of #17.\n"),
            ("quiet/no-longer-pending", "#17 is no longer pending.\n"),
            ("quiet/unmerged-closed", "#17 is unmerged.\n"),
            ("quiet/past-tense", "#17 was pending before the merge.\n"),
            ("quiet/earlier-attempt", "An earlier attempt, closed pull request #17, was not merged.\n"),
            ("quiet/no-cross-sentence", "The release is pending. #17 merged the fix.\n"),
            ("quiet/history-quoted-span", "Previously recorded: \u201cPR #17 is pending.\u201d\n"),
            ("quiet/history-backtick-span", "The old note read `waiting on #17`.\n"),
            ("quiet/history-blockquote", "Earlier note:\n\n> waiting for #17 to merge\n"),
            ("quiet/fenced-example", "```\nPR #17 is pending\n```\n")):
        code, res, t = _judge(text, {(REPO, 17): "closed"})
        check(name, code == 0 and t.calls == [] and not res.hard and not res.warnings, (code, t.calls))
    red = _with_steps_removed({"history-span"},
                              lambda: _judge("Previously recorded: \u201cPR #17 is pending.\u201d\n", c)[0])
    check("quiet/history-quoted-span/red-when-reverted", red == 1, red)

    code, res, _t = _judge("Previously recorded: \u201cPR #17 is pending.\u201d Now PR #18 remains open.\n",
                           {(REPO, 18): "merged"})
    check("history/current-assertion-after-quote", code == 1 and _numbers(res.hard) == [18], res.hard)
    code, res, _t = _judge("#17 was pending; #18 remains pending\n", {(REPO, 18): "merged"})
    check("history/only-current-clause", code == 1 and _numbers(res.hard) == [18], res.hard)
    code, res, _t = _judge("PR `#17` is \u201cpending\u201d.\n", m)
    check("format/inline-formatting-does-not-hide", code == 1, res.hard)
    code, res, _t = _judge("Previously blocked; #17 remains pending\n", m)
    check("format/previously-does-not-exempt-row", code == 1, res.hard)
    code, res, _t = _judge("> PR #17 is pending\n", m)
    check("quote/bare-blockquote-incomplete", code == 2 and not res.hard and "blockquote" in res.unknown[0],
          res.unknown)
    code, res, _t = _judge("The pending PR still needs review.\n", m)
    check("ambiguous/unnamed-target-incomplete", code == 2 and "ambiguous" in res.unknown[0], res.unknown)

    # --- every grammar rule on both sides, and the reference, structure and negation fixes ---
    _rule_rows(m, o)
    _fix_rows(m, o)

    # --- TOML decoding and wrapped prose ---
    toml_text = 'items = ["""Fix is\\u0020waiting for #17\n   to merge."""]\n'
    code, res, _t = _judge(toml_text, c, surface=TOML_SURFACE)
    check("toml/escaped-wrapped-string-decoded", code == 1, (code, res.hard))
    code, res, _t = _judge("Fix is waiting for\n#17 to merge, as noted.\n", c)
    check("markdown/wrapped-paragraph-joined", code == 1, (code, res.hard))
    try:
        _judge("items = [\n", c, surface=TOML_SURFACE)
        got = None
    except InputError as exc:
        got = str(exc)
    check("toml/malformed-input-error", got is not None and "not valid TOML" in got, got)

    # --- deduplication ---
    code, res, t = _judge("PR #17 remains pending. PR #17 is still open.\n", m)
    check("dedupe/one-read-shared-result", t.calls == [(REPO, 17)] and len(res.hard) == 2, (t.calls, res.hard))

    # --- the reader's validation ---
    for name, body in (
        ("reader/wrong-number", _response(18, "open")),
        ("reader/wrong-repository", _response(17, "open", repo="peer/repo")),
        ("reader/invalid-timestamp", _response(17, "closed", "2026-11-01 10:00:00")),
        ("reader/open-with-merge-time", _response(17, "open", MERGED_AT)),
        ("reader/missing-merged-at", b'{"number": 17, "state": "closed", "html_url": '
                                     b'"https://github.com/acme/widgets/pull/17", '
                                     b'"base": {"repo": {"full_name": "acme/widgets"}}}'),
        ("reader/invalid-json", b"{not json"),
        ("reader/boolean-number", _response(17, "open").replace(b'"number": 17', b'"number": true')),
        ("reader/merged-flag-contradiction", _response(17, "closed", MERGED_AT, merged=False)),
        ("reader/unknown-state", _response(17, "draft")),
        ("reader/two-objects", _response(17, "open") + _response(17, "open")),
    ):
        code, res, _t = _judge("PR #17 remains pending\n", {(REPO, 17): (0, body, b"")})
        check(name, code == 2 and not res.hard, (code, res.unknown))
    for name, entry in (("reader/denied-403", (1, b"", b"gh: Forbidden (HTTP 403)")),
                        ("reader/timeout", subprocess.TimeoutExpired(["gh"], 1)),
                        ("reader/launch-failure", FileNotFoundError(2, "gh"))):
        code, res, _t = _judge("PR #17 remains pending\n", {(REPO, 17): entry})
        check(name, code == 2 and not res.hard, (code, res.unknown))
    code, res, t = _judge("PR #17 remains pending\n", {(REPO, 17): (1, b"", b"gh: HTTP 502")})
    check("reader/transient-retried-once", code == 2 and len(t.calls) == 2, t.calls)
    many = " ".join("#{} is pending.".format(n) for n in range(1, 4)) + "\n"
    small = dict(CONFIG, limits={"max-prs": 2, "call-timeout-seconds": 10, "deadline-seconds": 120})
    code, res, t = _judge(many, {(REPO, n): "open" for n in range(1, 4)}, config=small)
    check("bound/request-limit-names-unread", code == 2 and len(t.calls) == 2
          and "request limit" in " ".join(res.unknown), res.unknown)
    ticks = iter([0.0, 0.0, 500.0, 500.0, 500.0])
    states = _pr_state.read_states([(REPO, 1), (REPO, 2)], FakeTransport({(REPO, 1): "open", (REPO, 2): "open"}),
                                   deadline=120, clock=lambda: next(ticks))
    check("bound/deadline-names-unread", states[(REPO, 1)][0] == "OPEN"
          and states[(REPO, 2)][0] == "UNKNOWN" and "deadline" in states[(REPO, 2)][1], states)
    code, res, t = _judge("Train subject (#17) shipped. PR #17 remains pending.\n", o)
    check("reader/direct-state-not-subject", code == 0, (code, res.hard))
    code, res, _t = _judge("PR #17 remains pending. PR #18 remains pending.\n",
                           {(REPO, 17): "merged", (REPO, 18): (1, b"", b"HTTP 404")})
    check("result/hard-and-unknown-both-reported", code == 2 and len(res.hard) == 1 and len(res.unknown) == 1,
          (res.hard, res.unknown))

    # --- origin binding ---
    for name, runner in (("origin/missing", lambda _r: (2, b"")),
                         ("origin/unsupported-host", lambda _r: (0, b"https://gitlab.com/acme/widgets\n")),
                         ("origin/two-urls", lambda _r: (0, b"https://github.com/a/b\nhttps://github.com/c/d\n")),
                         ("origin/credential-url", lambda _r: (0, b"https://tok@github.com/acme/widgets\n"))):
        code, res, t = _judge("PR #17 remains pending\n", m, git_runner=runner)
        check(name, code == 2 and t.calls == [] and "tok" not in " ".join(res.unknown), res.unknown)
    pinned = dict(MD_SURFACE, **{"expected-repository": "other/widgets"})
    code, res, t = _judge("PR #17 remains pending\n", m, surface=pinned)
    check("origin/expected-repository-mismatch", code == 2 and t.calls == [], res.unknown)
    for name, url in (("origin/accepted-form/scp", "git@github.com:acme/widgets.git"),
                      ("origin/accepted-form/ssh", "ssh://git@github.com/acme/widgets"),
                      ("origin/accepted-form/https", "https://github.com/acme/widgets")):
        check(name, _pr_state.repo_from_url(url) == REPO, url)

    # --- declaration validation and declared files ---
    for name, mutate in (("config/unknown-top-key", lambda d: d.update(extra=1)),
                         ("config/bad-format", lambda d: d["surface"][0].update(format="html")),
                         ("config/escaping-path", lambda d: d["surface"][0].update(path="../x.md")),
                         ("config/limit-out-of-range", lambda d: d["limits"].update({"max-prs": 0}))):
        data = {"schema-version": 1, "limits": dict(CONFIG["limits"]),
                "surface": [dict(MD_SURFACE), dict(TOML_SURFACE)]}
        mutate(data)
        try:
            validate_config(data)
            got = None
        except InputError as exc:
            got = str(exc)
        check(name, got is not None, got)
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-") as tmp:
        try:
            _read_bytes(Path(tmp) / "absent.md", "absent.md")
            got = None
        except InputError as exc:
            got = str(exc)
        check("input/unreadable-declared-file", got is not None and "cannot be read" in got, got)

    # --- real processes: a stub gh executable behind the production transport ---
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-gh-") as tmp:
        stub = Path(tmp) / "gh"
        stub.write_text(
            "#!{} -I\n"
            "import os, sys, signal, time\n"
            "mode = open(os.path.join(os.path.dirname(sys.argv[0]), 'mode')).read()\n"
            "assert sys.argv[1:5] == ['api', '--hostname', 'github.com', '-H'], sys.argv\n"
            "assert 'GH_HOST' not in os.environ\n"
            "if mode == 'merged':\n"
            "    sys.stdout.write({!r})\n"
            "elif mode == 'signal':\n"
            "    os.kill(os.getpid(), signal.SIGTERM)\n"
            "elif mode == 'exit3':\n"
            "    sys.exit(3)\n"
            "elif mode == 'slow':\n"
            "    time.sleep(30)\n".format(sys.executable, _response(17, "closed", MERGED_AT).decode()))
        os.chmod(stub, 0o700)
        transport = _pr_state.GhTransport(search_path=tmp)
        os.environ["GH_HOST"] = "evil.example"
        try:
            for name, mode, want in (("process/stub-gh-merged", "merged", 1),
                                     ("process/signal-termination", "signal", 2),
                                     ("process/unexpected-exit", "exit3", 2)):
                (Path(tmp) / "mode").write_text(mode)
                candidates = collect([(MD_SURFACE, b"PR #17 remains pending\n")])
                got = evaluate(candidates, CONFIG, "/nonexistent", transport, _origin_ok).exit_code()
                check(name, got == want, got)
            (Path(tmp) / "mode").write_text("slow")
            quick = dict(CONFIG, limits={"max-prs": 100, "call-timeout-seconds": 1, "deadline-seconds": 3})
            began = time.monotonic()
            candidates = collect([(MD_SURFACE, b"PR #17 remains pending\n")])
            got = evaluate(candidates, quick, "/nonexistent", transport, _origin_ok)
            check("process/timeout-bounded", got.exit_code() == 2 and time.monotonic() - began < 15,
                  got.unknown)
        finally:
            del os.environ["GH_HOST"]
        missing = _pr_state.GhTransport(search_path=str(Path(tmp) / "absent-dir"))
        candidates = collect([(MD_SURFACE, b"PR #17 remains pending\n")])
        got = evaluate(candidates, CONFIG, "/nonexistent", missing, _origin_ok).exit_code()
        check("process/launch-failure", got == 2, got)

    # --- staged content, draft application, commit adapter ---
    _self_test_adapters(tempfile, contextlib, io)

    got = [_main_exit(argv) for argv in (["--bogus"], [], ["--all"], ["--draft", "x"],
                                         ["--execution-report", "relative/report"])]
    check("cli/unrecognized-arguments-exit-2", got == [2, 2, 2, 2, 2], got)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        got = check_inputs(ROOT)
    check("live/real-tree-inputs-valid", got == 0, got)

    if FAILURES:
        print("SELF-TEST FAIL: check_pending_pr_claims {} of {} check(s) failed".format(
            len(FAILURES), len(EXECUTED)))
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: check_pending_pr_claims {} checks over synthetic responses: each of the {} "
          "grammar rules binds its example to a merged pull request (finding or warning), "
          "passes it when open, is cannot-evaluate when unreadable and leaves its no-flag twin alone, and "
          "with that rule removed its example loses that finding or warning (another rule may still "
          "warn on it); the listed reference spellings, Markdown structures, cue rows, historical spans "
          "and ambiguous phrases are exercised by the named rows, not all of them with a twin; a prose "
          "grammar cannot be complete, and wording outside those rows is not claimed to be covered (see "
          "the contract's residuals)".format(len(EXECUTED), len(PATTERNS)))
    return 0


def _self_test_adapters(tempfile, contextlib, io):
    import errno
    import signal
    import apply_record_draft
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-stage-") as tmp:
        root = Path(tmp)
        os.makedirs(root / ".aiqt")
        (root / CONFIG_REL).write_text(
            'schema-version = 1\n[limits]\nmax-prs = 100\ncall-timeout-seconds = 10\ndeadline-seconds = 120\n'
            '[[surface]]\npath = "NOTES.md"\nformat = "markdown"\nrepository = "origin"\nrole = "source"\n'
            '[[surface]]\npath = "sub/LOG.md"\nformat = "markdown"\nrepository = "origin"\nrole = "source"\n')
        (root / "NOTES.md").write_text("PR #17 remains pending.\n")
        oid = "a" * 40
        blobs = {oid: b"Nothing current here.\n"}
        index = {"NOTES.md": "100644 {} 0\tNOTES.md\0".format(oid)}

        def fake_git(_root, args):
            if args[:2] == ["diff", "--cached"]:
                return 0, b"NOTES.md\0"
            if args[0] == "ls-files":
                return 0, index.get(args[-1], "").encode()
            return (0, blobs[args[2]]) if args[2] in blobs else (128, b"")

        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/uses-staged-bytes", got == 0, (got, sink.getvalue()))
        blobs[oid] = b"Fix is waiting for\n#17 to merge.\n"
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/complete-unit-evaluated", got == 1, got)

        # A declared surface staged as a symlink (mode 120000; its blob is the link target's path) or
        # as an unmerged entry is cannot-evaluate, as a non-regular declared file is in --all.
        link_oid = "b" * 40
        blobs[link_oid] = b"REAL.md"
        index["NOTES.md"] = "120000 {} 0\tNOTES.md\0".format(link_oid)
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/symlink-entry-refused", got == 2 and "120000" in sink.getvalue(), (got, sink.getvalue()))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = _with_globals({"REGULAR_INDEX_MODES": REGULAR_INDEX_MODES + ("120000",)},
                                lambda: run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git))
        check("staged/symlink-entry-refused/red-when-reverted", got == 0, got)
        index["NOTES.md"] = "".join("100644 {} {}\tNOTES.md\0".format(oid, stage) for stage in (1, 2, 3))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/unmerged-entry-refused", got == 2, got)
        index["NOTES.md"] = "100755 {} 0\tNOTES.md\0".format(oid)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            got = run_staged(root, FakeTransport({(REPO, 17): "merged"}), _origin_ok, fake_git)
        check("staged/executable-regular-entry-read", got == 1, got)

        # The draft is opened once and its type checked on that descriptor: a draft swapped for a
        # symlink just after a path-based type check (simulated by an os.lstat that swaps it) is not
        # followed, because no path-based check precedes the open.
        draft_path, secret = root / "swap-draft.md", root / "swap-secret.md"
        draft_path.write_bytes(b"draft bytes\n")
        secret.write_bytes(b"secret bytes\n")
        real_lstat = os.lstat
        swapped = []

        def swapping_lstat(path, *args, **kwargs):
            result = real_lstat(path, *args, **kwargs)
            if not swapped and Path(path) == draft_path:
                swapped.append(path)
                os.unlink(draft_path)
                os.symlink(secret, draft_path)
            return result
        os.lstat = swapping_lstat
        try:
            got = _read_bytes(draft_path, "swap-draft.md")
        except InputError as exc:
            got = str(exc)
        finally:
            os.lstat = real_lstat
        check("input/draft-type-checked-on-descriptor", got == b"draft bytes\n", got)
        os.unlink(draft_path)
        os.symlink(secret, draft_path)
        try:
            got = _read_bytes(draft_path, "swap-draft.md")
        except InputError as exc:
            got = str(exc)
        check("input/draft-symlink-refused", isinstance(got, str) and "not a regular file" in got, got)
        os.unlink(draft_path)
        os.mkfifo(draft_path)
        try:
            got = _read_bytes(draft_path, "swap-draft.md")
        except InputError as exc:
            got = str(exc)
        check("input/draft-fifo-refused-without-blocking", isinstance(got, str) and "not a regular file" in got,
              got)
        os.unlink(draft_path)

        target = root / "NOTES.md"
        before = target.read_bytes()
        for name, body, table, want in (
                ("apply/withheld-on-finding", b"PR #17 remains pending.\n", {(REPO, 17): "merged"}, 1),
                ("apply/withheld-on-unknown", b"PR #17 remains pending.\n", {}, 2)):
            draft = root / "draft.md"
            draft.write_bytes(body)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport(table), _origin_ok)
            check(name, got == want and target.read_bytes() == before, got)
        draft = root / "draft.md"
        draft.write_bytes(b"PR #17 remains pending.\n")

        def mutate():
            target.write_bytes(b"changed underneath\n")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok, after_judge=mutate)
        check("apply/destination-changed-withheld", got == 2
              and target.read_bytes() == b"changed underneath\n", got)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/clean-draft-written", got == 0 and target.read_bytes() == b"PR #17 remains pending.\n", got)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "OTHER.md", FakeTransport({}), _origin_ok)
        check("apply/undeclared-target-withheld", got == 2 and not (root / "OTHER.md").exists(), got)

        # A FIFO target with no writer is refused at once, never waited on: an alarm turns a blocked
        # open into a failed row, so the row is bounded. A regular target is written (the twin).
        class _Blocked(Exception):
            pass

        def _alarm(_signum, _frame):
            raise _Blocked()
        os.unlink(target)
        os.mkfifo(target)
        previous = signal.signal(signal.SIGALRM, _alarm)
        signal.setitimer(signal.ITIMER_REAL, 5)
        try:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                               _origin_ok)
        except _Blocked:
            got = "still blocked in open() after 5 seconds"
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
        check("apply/fifo-target-refused", got == 2 and stat.S_ISFIFO(os.lstat(target).st_mode), got)
        os.unlink(target)
        target.write_bytes(b"old\n")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/regular-target-written", got == 0 and target.read_bytes() == b"PR #17 remains pending.\n",
              got)

        # A read error after the target is opened is cannot-evaluate at the first preimage read and at
        # the second (after the judgement), and the target is left as it was; with reads restored the
        # draft is written (the twin).
        real_read = apply_record_draft._read_descriptor

        def failing_read(_fd, _limit):
            raise OSError(errno.EIO, os.strerror(errno.EIO))

        def fail_reads():
            apply_record_draft._read_descriptor = failing_read
        for name, early in (("apply/read-error-first-preimage-cannot-evaluate", True),
                            ("apply/read-error-second-preimage-cannot-evaluate", False)):
            target.write_bytes(b"old\n")
            if early:
                fail_reads()
            try:
                with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                    got = apply_record_draft.apply(root, str(draft), "NOTES.md",
                                                   FakeTransport({(REPO, 17): "open"}), _origin_ok,
                                                   after_judge=None if early else fail_reads)
            except OSError as exc:
                got = "escaped {}".format(type(exc).__name__)
            finally:
                apply_record_draft._read_descriptor = real_read
            check(name, got == 2 and target.read_bytes() == b"old\n", got)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/reads-restored-written", got == 0 and target.read_bytes() == b"PR #17 remains pending.\n",
              got)

        # The target's permission bits survive the replace (0644 and 0640 both kept).
        for name, mode in (("apply/mode-kept-0644", 0o644), ("apply/mode-kept-0640", 0o640)):
            target.write_bytes(b"old\n")
            os.chmod(target, mode)
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                got = apply_record_draft.apply(root, str(draft), "NOTES.md", FakeTransport({(REPO, 17): "open"}),
                                               _origin_ok)
            check(name, got == 0 and stat.S_IMODE(os.stat(target).st_mode) == mode,
                  (got, oct(os.stat(target).st_mode)))

        # A symlinked parent directory is refused; a real one is written (the twin).
        outside = Path(tmp) / "outside"
        os.makedirs(outside)
        os.symlink(outside, root / "sub")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "sub/LOG.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/symlinked-parent-refused", got == 2 and not (outside / "LOG.md").exists(), got)
        os.unlink(root / "sub")
        os.makedirs(root / "sub")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "sub/LOG.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok)
        check("apply/real-parent-written", got == 0 and (root / "sub" / "LOG.md").read_bytes()
              == b"PR #17 remains pending.\n" and not (outside / "LOG.md").exists(), got)

        # A parent swapped for a symlink after it was opened withholds the write.
        def swap():
            os.rename(root / "sub", root / "sub-held")
            os.symlink(outside, root / "sub")
        os.unlink(root / "sub" / "LOG.md")
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            got = apply_record_draft.apply(root, str(draft), "sub/LOG.md", FakeTransport({(REPO, 17): "open"}),
                                           _origin_ok, after_judge=swap)
        check("apply/parent-swapped-after-open-withheld", got == 2 and not (outside / "LOG.md").exists()
              and not (root / "sub-held" / "LOG.md").exists(), got)

    adapter = ROOT / "tools" / "check_pending_pr_commit.sh"
    with tempfile.TemporaryDirectory(prefix="pending-pr-claims-hook-") as tmp:
        bindir = Path(tmp)
        (bindir / "git").write_text("#!/bin/sh\nprintf '%s\\n' \"" + tmp + "\"\n")
        os.makedirs(bindir / "tools")
        (bindir / "tools" / "check_pending_pr_claims.py").write_text("")
        for name, code, needle in (("commit/advisory-continues-on-incomplete", 2, "INCOMPLETE"),
                                   ("commit/advisory-continues-on-finding", 1, "ADVISORY")):
            (bindir / "python3").write_text("#!/bin/sh\nexit {}\n".format(code))
            for exe in ("git", "python3"):
                os.chmod(bindir / exe, 0o700)
            child = subprocess.run(["/bin/sh", str(adapter)], capture_output=True, stdin=subprocess.DEVNULL,
                                    env={"PATH": str(bindir)}, timeout=60)
            check(name, child.returncode == 0 and needle in child.stderr.decode("utf-8", "replace"),
                  (child.returncode, child.stderr))


def _main_exit(argv):
    """main()'s exit code for the argument list argv, its output captured."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return main(list(argv))


def main(argv=None):
    if argv is None:
        # The process entry: the command line, ended through the execution-report finalizer when the
        # self-test armed it.
        _code = main(sys.argv[1:])
        _finalizer = sys.modules.get("_selftest_exit_report")
        (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
    report_path = None
    if len(argv) >= 2 and argv[-2] == "--execution-report" and os.path.isabs(argv[-1]) \
            and argv[:-2] in ([], ["--self-test"]):
        report_path, argv = argv[-1], ["--self-test"]
    if argv == ["--self-test"]:
        if report_path is not None:
            import _selftest_exit_report
            _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
        return _self_test()
    if argv == ["--check-inputs"]:
        return check_inputs(ROOT)
    if argv == ["--all", "--live"]:
        return run_all(ROOT)
    if argv == ["--staged", "--live"]:
        return run_staged(ROOT)
    if len(argv) == 4 and argv[0] == "--draft" and argv[2] == "--target":
        return run_draft(ROOT, argv[1], argv[3])
    print("error: unrecognized arguments {!r}; expected --check-inputs, --all --live, --staged --live, "
          "--draft PATH --target PATH or --self-test; fail closed".format(argv), file=sys.stderr)
    return 2


# The child-status guard: an inline copy of the canonical guard in opf/tools/_child_status.py, which
# tools/check_child_status_coverage.py pins this copy to by AST. The live modes start gh and git and read
# their exit status (subprocess), so under an ignored SIGCHLD a failing read would read as a pass; the
# guard runs at the entry line, before main() dispatches.
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


if __name__ == "__main__":
    sys.exit(_child_status_refusal_exit("check_pending_pr_claims.py") or main())
