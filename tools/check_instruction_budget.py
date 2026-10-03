#!/usr/bin/env python3
"""Instruction-budget gate: hold what Claude Code loads unconditionally to a committed ratchet.

Claude Code reads every `.claude/rules/**/*.md` file that carries no `paths:` scope, the project CLAUDE.md,
and every file either of them imports, into every session before the first prompt. That text is paid for
in every session, so this gate measures it and fails when the pack's share grows. The loading rules below
are taken from the loader of the Claude Code build pinned in the budget source.

OVER-COUNT BY CONSTRUCTION. A budget gate must never under-count, so the gate does not model the loader's
Markdown (code spans, paragraph boundaries, HTML blocks, comment placement). It counts every character of a
counted file and every file an `@` in it could name, and it removes only what the loader certainly
removes: a leading byte order mark before frontmatter, the frontmatter block, and an HTML comment in the
one shape COMMENTS states. The figure can be higher than what the loader keeps, never lower.

What it measures, in UTF-16 code units (the length of the text encoded utf-16-le, divided by 2; a
character in the Basic Multilingual Plane is one unit, an astral character such as an emoji is two):

  RULE FILES. Every file under `.claude/rules/` whose name ends in `.md` (case sensitive) and that is
      unconditional. The walk follows a symlinked directory whose target resolves inside the repository
      and counts the files under it. A symlink, to a directory or a file, that resolves outside the
      repository, a symlink loop, and a directory reached a second time through a symlink exit 2.
      A leading byte order mark (BOM) is removed before the frontmatter test, then a leading frontmatter
      block is removed (FRONTMATTER_RE below, first match only; its trailing `\\s*` also takes the blank
      line after the closing fence); with no frontmatter the BOM stays counted. HTML comments are then
      removed as COMMENTS states.
  SCOPE. A file whose frontmatter has a `paths:` key is conditional and is not counted, unless its globs
      load everywhere. The value (a scalar, or each item of a list) is normalized in the loader's order:
      split on the commas outside braces, each piece trimmed, one level of brace alternation `{a,b}`
      expanded, one trailing `/**` stripped, and empty globs dropped. The file is unconditional, and
      counted, when no glob is left or every glob is `**` (so `**`, `**/**`, `/**`, `**,`, ` ** `,
      `{**,**}`, and `["**", "/**"]`). A glob the normalizer cannot read (nested braces, an unbalanced
      brace, a backslash escape) exits 2. A typed `paths:` value or item, one YAML would read as null, a
      boolean, a number, a mapping, an alias, a tag, or a block scalar, exits 2: the loader drops a scope
      it cannot read as strings and loads the file everywhere, so the gate refuses rather than guesses.
  FRONTMATTER. A strict line reader: blank and `#` lines are skipped; it classifies a `key: value` line
      with a plain key (letters, digits, `_`, `-` only) at column 0 and a space after the colon, and a
      `- item` line (indented with spaces only) under a key whose value is empty. When the loader's YAML
      cannot parse frontmatter it loads the file everywhere, so any line the reader cannot classify (an
      indented or dotted key, a value holding a second `: ` or starting with `@`, a backtick, `|`, `>`,
      `&`, `*` or `!` outside quotes, a value the scalar parser refuses, any other line) makes the file
      unconditional: counted, never left out.
  COMMENTS. An HTML comment is removed only in the one shape the loader certainly removes: a line that is
      exactly one `<!-- ... -->` comment at column 0, with a blank line or the start of the file before it
      and a blank line or the end of the file after it, outside a fenced code block. The line and its
      newline go; the blank lines around it stay counted. Every other comment is counted. The line scan
      that finds fenced code blocks follows only fences opened at column 0; from the first line it cannot
      place (a fence opener that is not at column 0, any line that opens with `<` at any indent or inside
      a list or quote, or a fence-like line inside a fence that is not a plain close) to the end of the
      file it removes no comment, and a file holding a carriage return has no comment removed.
  IMPORTS. Every `@` in a counted rule file, in the managed block, in an imported file, and, for SESSION,
      in the whole CLAUDE.md is an import candidate, with no exclusion for code spans, comments, or other
      Markdown structure. The candidate is the run of non-whitespace characters after the `@` (a
      backslash-escaped space stays in the run). The paths it may name are tested longest first: the
      whole run, then each prefix that ends just before a Markdown delimiter character (`*`, `_`, a
      backtick, `)`, `]`, `}`, `>`, `,`, `.`, `;`, `:`, `!`, `?`, quotes) or an inline opener (`<`, `[`,
      `(`, `{`, a backslash), each with its `#` fragment cut, read with the escaped space and also with
      Markdown backslash escapes undone. This takes trailing delimiters off one at a time as a special
      case, and also finds the path in `<b>@big.txt</b>` and `[@big.txt](x)`. Every tested path that names
      an existing regular file inside the repository, resolved relative to the importing file, is an
      import, so the loader's own token, always one of these prefixes, is never missed. When a path names
      no file under exact case but one file inside the repository matches it case-insensitively, that file
      is the import (the loader on a case-insensitive file system reads it); two such matches exit 2. Each
      target is counted once, recursively through imported files to any depth (a cycle ends), its BOM,
      frontmatter, and comments removed as for a rule file. A candidate none of whose paths names a file
      adds 0 and does not fail: a mention such as @alice, or an email address such as name@example.com.
      A candidate the gate cannot settle exits 2 with a message that calls it ambiguous: a home relative
      (`~/`) or absolute path, a path that resolves outside the repository (what it loads depends on the
      machine), and a target that exists but is not a regular file or cannot be read or decoded as UTF-8.
  MANAGED BLOCK. The pack-managed RULES-INDEX block of CLAUDE.md (the markers tools/gen_claude.py
      writes), comments removed as COMMENTS states with the scan run over the whole CLAUDE.md (so a fence
      opened before the block keeps the block's comments counted), plus its imports.

Two totals are reported separately:

  PACK     the rule files and their imports plus the managed block and its imports. This is the part the
           pack controls, and the only total the gate enforces.
  SESSION  the rule files plus the whole CLAUDE.md plus every file either imports. Reported, not
           enforced: the bytes outside the managed block belong to the adopter.

A rule file or the managed block importing RULES-INDEX.md or AGENTS.md is a finding (exit 1) whatever the
total: a candidate any of whose tested paths, or any of whose targets, has either as its final path
component in any case, whether a file exists there or not. The same import in the adopter's text outside
the block is not.

The budget source, .aiqt/core/instruction-budget.toml, carries exactly four keys: `ratchet` (the PACK
figure enforced today; lower it as the pack shrinks, and raise it only with a reviewed reason), `ceiling`
(the target PACK figure, reported, not enforced), and `claude-code-version` and `binary-sha256`, which pin
the Claude Code build whose loading behaviour this gate follows.

KNOWN OVER-COUNTS, by design (each can make PACK or SESSION higher than what the loader keeps):
  - A comment in any other place (inside a paragraph, a list, a quote, a code block or span, not
    surrounded by blank lines, spread over lines, after the comment scan stops, or in a file holding a
    carriage return) is counted although the loader may remove it.
  - `@` text that happens to name a file is counted as an import: an email address, a mention, or a
    prefix of either that names a file, and an `@` in link destinations or HTML attributes.
  - An import inside a code span, a fenced code block, or an HTML comment is counted although the loader
    skips it, and a banned name there exits 1.
  - Every target is followed without the loader's own path filter and to any depth, and two prefixes of
    one candidate that name two files both count.
  - A frontmatter line the strict reader cannot classify makes a file with a `paths:` scope count.
  - A rule file reached under two names counts twice.

DISCLOSED RESIDUAL. This gate measures the repository's own files; it does not run Claude Code. It does
not measure user-level files (~/.claude/CLAUDE.md and ~/.claude/rules/), which load in every session too;
an AGENTS.md or other file loaded through the instructionFiles setting rather than an `@` import;
CLAUDE.md files in other directories, or CLAUDE.local.md; skills, hooks, tool definitions, or the system
prompt; or loading by a Claude Code version other than the one pinned in the budget source. The count is
UTF-16 code units, not tokens, so it tracks size, not model cost.

Usage:
  check_instruction_budget.py                          measure and enforce; print both totals
  check_instruction_budget.py --self-test              fixture trees only; assert every count and exit
  check_instruction_budget.py --execution-report ABS   the self-test, also writing the executed check ids
                                                       (the launch form tools/check_selftest_execution.py
                                                       uses for the instruction-budget-selftest suite)

Exit 0 clean; 1 on a finding (PACK over the ratchet, a banned import); 2 on a cannot-evaluate input
(fail-closed), so an unreadable or malformed input can never read as clean.
"""
import contextlib
import errno
import io
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: check_instruction_budget.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_claude  # noqa: E402  the RULES-INDEX markers it writes, never a second copy of them
import gen_rules  # noqa: E402  its validated frontmatter value parsers, never a second one

RULES_REL = ".claude/rules"
CLAUDE_REL = "CLAUDE.md"
BUDGET_REL = ".aiqt/core/instruction-budget.toml"
BUDGET_KEYS = {"ratchet", "ceiling", "claude-code-version", "binary-sha256"}
# Importing either from the managed block loads the rule index or a second full copy of the corpus on top
# of the rule tree, so either is a finding whatever the measured total. Matched on the final path
# component in any case, as a case-insensitive file system reads it.
BANNED_IMPORTS = ("RULES-INDEX.md", "AGENTS.md")
BANNED_FOLDED = {name.casefold() for name in BANNED_IMPORTS}

# The leading frontmatter block, exactly as the approved design states it (first match only).
FRONTMATTER_RE = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?")
# A file that opens a frontmatter fence FRONTMATTER_RE cannot close is malformed, never body text.
FRONTMATTER_OPEN_RE = re.compile(r"^---\s*\n")
JS_WHITESPACE = (" \t\n\v\f\r\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008"
                 "\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")
# The one comment shape the gate removes: a whole line that is exactly one `<!-- ... -->` comment.
COMMENT_LINE_RE = re.compile(r"<!--(?:(?!-->)[^\n])*-->")
FENCE_OPEN_RE = re.compile(r"(`{3,})[^`]*$|(~{3,})")
FENCE_ANY_RE = re.compile(r"[ \t]*(?:`{3,}|~{3,})")
_CONTAINER = r"[ \t>]*(?:(?:[*+-]|\d{1,9}[.)])[ \t>]*)*"
# A line the comment scan cannot place: a fence opener that is not at column 0, or any line opening with
# `<` (an HTML block may run past blank lines), at any indent or container depth.
UNPLACED_RE = re.compile(_CONTAINER + r"(?:```|~~~|<)")
ESCAPED_SPACE = "\x5c\x20"
# An import candidate: the run of non-whitespace characters after an `@` (a backslash-escaped space stays
# in the run, as in the loader's own token).
CANDIDATE_RE = re.compile(r"(?:\\ |\S)+")
# A prefix of the run is tested wherever it ends just before one of these: the trailing Markdown delimiter
# characters, the openers of inline markup, and the backslash of an escape.
DELIMITERS = "*_`)]}>,.;:!?\"'"
CUT_BEFORE = DELIMITERS + "<[({\\"
MARKDOWN_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~ ])")
YAML_TYPED_RE = re.compile(
    r"(?:null|Null|NULL|~|true|True|TRUE|false|False|FALSE|yes|Yes|YES|no|No|NO|on|On|ON|off|Off|OFF|y|Y|n|N)"
    r"|[-+]?(?:\d[\d_]*(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|0x[0-9a-fA-F_]+|0o[0-7_]+"
    r"|\.(?:inf|Inf|INF|nan|NaN|NAN))")
# The strict line reader: a plain key (letters, digits, `_`, `-`) at column 0, a colon, and a space before
# any value; a `- item` line indented with spaces only.
KEY_RE = re.compile(r"([A-Za-z0-9_-]+):(?: +(.*))?")
ITEM_RE = re.compile(r" *- +(.*)")
QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class GateError(Exception):
    """A fail-closed condition (an unreadable input, malformed frontmatter, markers, an unresolvable
    import, or a malformed budget source): exit 2."""


def utf16_units(text):
    """The length of `text` in UTF-16 code units: one per BMP character, two per astral character."""
    return len(text.encode("utf-16-le")) // 2


def comment_spans(text):
    """The (start, end) spans of `text` the loader certainly removes as HTML comments: each a line that is
    exactly one `<!-- ... -->` comment at column 0, with a blank line or the start of `text` before it and
    a blank line or the end of `text` after it, outside a fenced code block, with its newline. The scan
    follows only fences opened at column 0; from the first line it cannot place (UNPLACED_RE, or a
    fence-like line inside a fence that is not a plain close) it reports nothing more, and a text holding a
    carriage return has no span, so a comment there is counted, never guessed away."""
    spans = []
    if "<!--" not in text or "\r" in text:
        return spans
    lines = text.split("\n")
    pos, fence = 0, None
    for idx, line in enumerate(lines):
        start, pos = pos, pos + len(line) + 1
        if fence is not None:
            if re.fullmatch(" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + ",} *", line):
                fence = None
            elif FENCE_ANY_RE.match(line):
                return spans
            continue
        if line.startswith("<!--") and "-->" in line[2:]:
            # A comment block that closes on its own line ends there, whatever follows on the line, so it
            # leaves the fence state unchanged; only the exact shape with blank neighbours is removed.
            if (COMMENT_LINE_RE.fullmatch(line)
                    and (idx == 0 or not lines[idx - 1].strip(" \t"))
                    and (idx == len(lines) - 1 or not lines[idx + 1].strip(" \t"))):
                spans.append((start, min(pos, len(text))))
            continue
        opener = FENCE_OPEN_RE.match(line)
        if opener:
            run = opener.group(1) or opener.group(2)
            fence = (run[0], len(run))
        elif UNPLACED_RE.match(line):
            return spans
    return spans


def strip_comments(text):
    """`text` without the comment lines comment_spans reports; every other comment stays counted."""
    out, pos = [], 0
    for start, end in comment_spans(text):
        out.append(text[pos:start])
        pos = end
    return "".join(out) + text[pos:]


def read_text(path, where):
    """The file's text decoded strictly as UTF-8 from its raw bytes, with no newline translation, so a
    CRLF file is counted as written. GateError on any read or decode failure (fail-closed)."""
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError("cannot read {} ({})".format(where, exc))


def split_frontmatter(text, where):
    """(frontmatter text or None, body). The body is what follows the FIRST FRONTMATTER_RE match. A file
    that opens a frontmatter fence the regex cannot close is malformed: GateError."""
    # The loader removes a leading BOM before it matches frontmatter; with no frontmatter the BOM stays
    # counted (one unit more, never less).
    bare = text[1:] if text.startswith("\ufeff") else text
    m = FRONTMATTER_RE.match(bare)
    if m is None:
        if FRONTMATTER_OPEN_RE.match(bare):
            raise GateError("{}: unterminated frontmatter block".format(where))
        return None, text
    body = bare[m.end():]
    return m.group(1), body


def _odd_value(value):
    """True when a frontmatter value is one the loader's YAML may reject or read as other than a plain
    scalar: it starts with `@`, a backtick, `|`, `>`, `&`, `*` or `!`, or holds a second `: ` (or ends with
    `:`) outside quotes."""
    if value[:1] in "@`|>&*!":
        return True
    bare = QUOTED_RE.sub("", value)
    return ": " in bare or bare.endswith(":")


def parse_frontmatter(block, where):
    """({key: value}, unclassified) from the strict line reader: blank and `#` comment lines are skipped, a
    `key: value` line (KEY_RE) takes gen_rules' validated scalar and flow-list parser, and `- item` lines
    form a block list under a key whose value is empty. `unclassified` is True when any other line, or a
    value _odd_value flags or the scalar parser refuses, is met: the loader then may fail to parse the
    frontmatter and load the file everywhere, so the caller counts it. A `paths:` value or item that is
    not a plain string, and a duplicate key, are GateError."""
    fm = {}
    open_key = None
    unclassified = False
    for raw in block.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = ITEM_RE.fullmatch(line)
        if item and open_key is not None:
            tok = item.group(1).strip()
            if open_key == "paths":
                _plain_string(tok, where)
            elif _odd_value(tok):
                unclassified = True
            try:
                fm[open_key].append(gen_rules._unquote(tok))
            except ValueError as exc:
                if open_key == "paths":
                    raise GateError("{}: frontmatter: {}".format(where, exc))
                unclassified = True
            continue
        kv = KEY_RE.fullmatch(line)
        if kv is None:
            unclassified = True
            open_key = None
            continue
        key, value = kv.group(1), (kv.group(2) or "").strip()
        if key in fm:
            raise GateError("{}: duplicate frontmatter key {!r}".format(where, key))
        if value == "":
            fm[key] = []
            open_key = key
            continue
        open_key = None
        if key == "paths":
            inner = value[1:-1] if value.startswith("[") and value.endswith("]") else None
            for tok in ([value] if inner is None else [t.strip() for t in inner.split(",") if t.strip()]):
                _plain_string(tok, where)
        elif _odd_value(value):
            unclassified = True
        try:
            fm[key] = gen_rules._value(value)
        except ValueError as exc:
            if key == "paths":
                raise GateError("{}: frontmatter: {}".format(where, exc))
            fm[key] = value
            unclassified = True
    return fm, unclassified


def _plain_string(tok, where):
    """GateError unless the `paths:` token `tok` is a YAML string: quoted, or a plain scalar YAML would not
    read as null, a boolean, a number, a mapping, an alias, a tag, or a block scalar. The loader drops a
    scope it cannot read as strings, so such a value is cannot-evaluate, never a conditional file."""
    if tok[:1] in ("\"", "'"):
        return
    if (not tok or tok[0] in "{}&*!|>%@`#," or YAML_TYPED_RE.fullmatch(tok)
            or ": " in tok or tok.endswith(":") or " #" in tok):
        raise GateError("{}: `paths:` value {!r} is not a string glob; cannot evaluate".format(where, tok))


def path_globs(fm, where):
    """None when the frontmatter has no `paths:` key; else its values as a list of strings (a scalar is one
    value). Any other shape is malformed: GateError."""
    if "paths" not in fm:
        return None
    value = fm["paths"]
    globs = [value] if isinstance(value, str) else value
    if not isinstance(globs, list) or not all(isinstance(g, str) for g in globs):
        raise GateError("{}: `paths:` must be a glob or a list of globs".format(where))
    return globs


def _split_top(value, where):
    """`value` split on the commas outside braces. GateError on what the normalizer cannot read: a
    backslash escape, a nested brace, or an unbalanced brace."""
    parts, current, depth = [], [], 0
    for ch in value:
        if ch == "\\":
            raise GateError("{}: `paths:` glob {!r} holds an escape; cannot evaluate".format(where, value))
        if ch == "{":
            depth += 1
            if depth > 1:
                raise GateError("{}: `paths:` glob {!r} nests braces; cannot evaluate".format(where, value))
        elif ch == "}":
            depth -= 1
            if depth < 0:
                raise GateError("{}: `paths:` glob {!r} has an unbalanced brace; cannot evaluate".format(
                    where, value))
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if depth:
        raise GateError("{}: `paths:` glob {!r} has an unbalanced brace; cannot evaluate".format(where, value))
    parts.append("".join(current))
    return parts


def _expand_braces(glob):
    """Every expansion of the (non-nested) brace alternations in `glob`, each alternative trimmed."""
    expansions = [""]
    for piece in re.split(r"(\{[^{}]*\})", glob):
        if piece.startswith("{") and piece.endswith("}"):
            choices = [c.strip(JS_WHITESPACE) for c in piece[1:-1].split(",")]
        else:
            choices = [piece]
        expansions = [e + c for e in expansions for c in choices]
    return expansions


def normalize_globs(values, where):
    """The globs the loader matches for `paths:` values, in its own order: split on commas outside braces,
    trim, expand one level of braces, strip one trailing `/**`, drop empty globs."""
    out = []
    for value in values:
        for piece in _split_top(value, where):
            for glob in _expand_braces(piece.strip(JS_WHITESPACE)):
                glob = glob.strip(JS_WHITESPACE)
                if glob.endswith("/**"):
                    glob = glob[:-3]
                if glob:
                    out.append(glob)
    return out


def loads_everywhere(globs):
    """True when no normalized glob is left or every one is `**` (so `**`, `**/**`, `/**`, `**,`)."""
    return all(g == "**" for g in globs)


def _inside(path, real_root):
    """True when the resolved `path` is `real_root` or under it."""
    try:
        return os.path.commonpath([path, real_root]) == real_root
    except ValueError:
        return False


def rule_files(root):
    """Every `*.md` file under .claude/rules/, in a stable order, following a symlinked directory as the
    loader does. GateError when the tree is missing or cannot be walked, when a symlink resolves outside
    the repository or loops, and when a directory is reached a second time through a symlink (whether the
    loader then loads its files once or twice is ambiguous)."""
    base = root / RULES_REL
    if not base.is_dir():
        raise GateError("{} is missing or not a directory".format(RULES_REL))
    real_root = os.path.realpath(root)
    real_base = os.path.realpath(base)
    if not _inside(real_base, real_root):
        raise GateError("{} resolves outside the repository; cannot evaluate".format(RULES_REL))
    found, visited = [], {real_base}

    def _walk(directory, chain):
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda entry: entry.name)
        except OSError as exc:
            raise GateError("cannot walk {} ({})".format(RULES_REL, exc))
        subdirs = []
        for entry in entries:
            path = directory / entry.name
            where = path.relative_to(root).as_posix()
            if entry.is_symlink():
                try:
                    os.stat(path)
                except OSError as exc:
                    if exc.errno == errno.ELOOP:
                        raise GateError("{}: symlink loop; cannot evaluate".format(where))
                if not _inside(os.path.realpath(path), real_root):
                    raise GateError("{}: symlink resolves outside the repository; cannot evaluate".format(
                        where))
            if entry.is_dir():
                subdirs.append((path, where))
            elif entry.name.endswith(".md"):
                found.append(path)
        for path, where in subdirs:
            real = os.path.realpath(path)
            if real in chain:
                raise GateError("{}: symlink loop; cannot evaluate".format(where))
            if real in visited:
                raise GateError("{}: directory reached a second time through a symlink; whether the loader "
                                "loads it once or twice is ambiguous; cannot evaluate".format(where))
            visited.add(real)
            _walk(path, chain | {real})

    _walk(base, frozenset([real_base]))
    return found


def measure_rules(root):
    """(counted units, counted file count, conditional file count, import origins) over the rule tree,
    the origins one (body, directory, where) per counted file, for follow_imports."""
    total = counted = conditional = 0
    origins = []
    for path in rule_files(root):
        where = path.relative_to(root).as_posix()
        front, body = split_frontmatter(read_text(path, where), where)
        if front is not None:
            fm, unclassified = parse_frontmatter(front, where)
            globs = path_globs(fm, where)
            if (globs is not None and not unclassified
                    and not loads_everywhere(normalize_globs(globs, where))):
                conditional += 1
                continue
        total += utf16_units(strip_comments(body))
        counted += 1
        origins.append((body, os.path.dirname(os.path.realpath(path)), where))
    return total, counted, conditional, origins


def managed_block(text):
    """(start, end) of the text between the RULES-INDEX markers gen_claude.py writes. Each marker must
    appear exactly once, BEGIN before END; otherwise GateError."""
    begin, end = gen_claude.BEGIN, gen_claude.END
    if text.count(begin) != 1 or text.count(end) != 1:
        raise GateError("{}: the RULES-INDEX markers must each appear exactly once".format(CLAUDE_REL))
    i, j = text.find(begin), text.find(end)
    if j < i:
        raise GateError("{}: the RULES-INDEX END marker precedes BEGIN".format(CLAUDE_REL))
    return i + len(begin), j


def import_candidates(text):
    """For every `@` in `text`, with no exclusion for code spans, comments, or other Markdown structure:
    the run of non-whitespace characters after it (CANDIDATE_RE), or "" when none follows."""
    runs = []
    pos = text.find("@")
    while pos >= 0:
        m = CANDIDATE_RE.match(text, pos + 1)
        runs.append(m.group(0) if m else "")
        pos = text.find("@", pos + 1)
    return runs


def candidate_paths(run):
    """The paths a candidate run may name, longest first: the whole run and each prefix that ends just
    before a CUT_BEFORE character, each with its `#` fragment cut, read with the loader's escaped space
    and also with every Markdown backslash escape undone."""
    cuts = sorted({len(run)} | {k for k, ch in enumerate(run) if ch in CUT_BEFORE}, reverse=True)
    paths = []
    for k in cuts:
        prefix = run[:k].split("#", 1)[0]
        for variant in (prefix.replace(ESCAPED_SPACE, " "), MARKDOWN_ESCAPE_RE.sub(r"\1", prefix), prefix):
            if variant and variant not in paths:
                paths.append(variant)
    return paths


def _casefold_matches(target, real_root):
    """The regular files inside the repository whose path matches `target` (an absolute path under
    `real_root`) component by component in any case."""
    rel = os.path.relpath(target, real_root)
    if rel == "." or rel.split(os.sep)[0] == "..":
        return []
    paths = [real_root]
    for comp in rel.split(os.sep):
        folded, step = comp.casefold(), []
        for directory in paths:
            try:
                names = os.listdir(directory)
            except OSError:
                continue
            step.extend(os.path.join(directory, n) for n in sorted(names) if n.casefold() == folded)
        paths = step
    return sorted({os.path.realpath(p) for p in paths
                   if os.path.isfile(p) and _inside(os.path.realpath(p), real_root)})


def resolve_candidate(path, base, real_root, where, token):
    """The regular file `path` names relative to the directory `base`, or None when it names nothing (a
    missing path, a directory). Under exact case first; else the one file matching it case-insensitively
    inside the repository. GateError when it resolves outside the repository, names something that is not
    a regular file or a directory, or matches two files case-insensitively."""
    if all(part in ("", ".", "..") for part in path.split("/")):
        return None  # only dot components: always a directory, never a file
    target = os.path.realpath(os.path.join(base, path))
    if not _inside(target, real_root):
        raise GateError("{}: import @{} escapes the repository, so what it loads depends on the "
                        "machine; ambiguous, cannot evaluate".format(where, token))
    if os.path.isdir(target):
        return None
    if os.path.lexists(target):
        if not os.path.isfile(target):
            rel = Path(os.path.relpath(target, real_root)).as_posix()
            raise GateError("{}: import @{} names {}, which is not a regular file; whether the loader loads "
                            "it is ambiguous; cannot evaluate".format(where, token, rel))
        return target
    matches = _casefold_matches(target, real_root)
    if len(matches) > 1:
        raise GateError("{}: import @{} matches {} files case-insensitively; which one a case-insensitive "
                        "file system loads is ambiguous; cannot evaluate".format(where, token, len(matches)))
    return matches[0] if matches else None


def follow_imports(root, origins, findings):
    """{resolved path: units} for every file reachable through `@` import candidates from `origins`, a
    list of (text, the directory it sits in, where), each counted once with frontmatter and comments
    removed as for a rule file. Every path candidate_paths gives that names a file is followed, so an
    import can be over-reported, never missed. A candidate whose tested paths or targets carry a
    BANNED_IMPORTS final component (any case) is appended to `findings`. GateError when a candidate is
    home relative or absolute (what it loads depends on the machine), when resolve_candidate fails, or
    when a target cannot be read and decoded."""
    real_root = os.path.realpath(root)
    seen = {}
    pending = list(origins)
    while pending:
        source, base, where = pending.pop()
        for run in import_candidates(source):
            token = run.split("#", 1)[0].replace(ESCAPED_SPACE, " ")
            if token.startswith("~/") or (token.startswith("/") and token != "/"):
                raise GateError("{}: import @{} is home relative or absolute, so what it loads depends on "
                                "the machine; ambiguous, cannot evaluate".format(where, token))
            banned, targets = False, []
            for path in candidate_paths(run):
                if os.path.basename(path.rstrip("/")).casefold() in BANNED_FOLDED:
                    banned = True
                target = resolve_candidate(path, base, real_root, where, run)
                if target is not None and target not in targets:
                    targets.append(target)
            if any(os.path.basename(t).casefold() in BANNED_FOLDED for t in targets):
                banned = True
            if banned:
                findings.append("{}: imports @{}; the pack must not import {}".format(
                    where, run, " or ".join(BANNED_IMPORTS)))
            for target in targets:
                if target in seen:
                    continue
                rel = Path(os.path.relpath(target, real_root)).as_posix()
                try:
                    text = read_text(Path(target), rel)
                except GateError as exc:
                    raise GateError("{}: import @{} names {}, which the gate cannot read ({}); whether the "
                                    "loader loads it is ambiguous; cannot evaluate".format(where, run, rel, exc))
                body = split_frontmatter(text, rel)[1]
                seen[target] = utf16_units(strip_comments(body))
                pending.append((body, os.path.dirname(target), rel))
    return seen


def measure(root):
    """Both totals and their parts. Banned imports reached from the rule files and the managed block are
    returned as findings; every cannot-evaluate condition raises GateError."""
    rules, counted, conditional, origins = measure_rules(root)
    claude = read_text(root / CLAUDE_REL, CLAUDE_REL)
    start, end = managed_block(claude)
    block = claude[start:end]
    findings = []
    real_root = os.path.realpath(root)
    # Claude Code follows imports in rule files as in CLAUDE.md, so theirs count into both totals.
    pack_imports = sum(follow_imports(
        root, origins + [(block, real_root, CLAUDE_REL + " managed block")], findings).values())
    # The adopter owns the bytes outside the block, so a banned import there is not the pack's finding:
    # only the rule files' and the managed block's findings fail the gate.
    session_imports = sum(follow_imports(root, origins + [(claude, real_root, CLAUDE_REL)], []).values())
    # The block's comments are placed in the whole CLAUDE.md, so a fence opened before the block counts.
    spans = comment_spans(claude)
    block_units = utf16_units(block) - sum(utf16_units(claude[s:e]) for s, e in spans if start <= s and e <= end)
    claude_units = utf16_units(strip_comments(claude))
    return {
        "rules": rules, "rule_files": counted, "conditional_files": conditional,
        "block": block_units, "pack_imports": pack_imports,
        "claude": claude_units, "session_imports": session_imports,
        "pack": rules + block_units + pack_imports,
        "session": rules + claude_units + session_imports,
        "findings": findings,
    }


def load_budget(root):
    """The validated budget source. GateError on a missing, unreadable, or malformed file: exactly the
    four BUDGET_KEYS, positive integer ratchet and ceiling (a bool is refused), an X.Y.Z version, and a
    lowercase hex SHA-256."""
    try:
        data = tomllib.loads(read_text(root / BUDGET_REL, BUDGET_REL))
    # ValueError and RecursionError too: tomllib raises a BARE ValueError on an integer literal past
    # CPython's int-string limit, and a RecursionError on a deeply nested array or inline table.
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise GateError("{} is not valid TOML ({})".format(BUDGET_REL, exc))
    if set(data) != BUDGET_KEYS:
        raise GateError("{}: keys must be exactly {} (got {})".format(
            BUDGET_REL, sorted(BUDGET_KEYS), sorted(data)))
    for key in ("ratchet", "ceiling"):
        if type(data[key]) is not int or data[key] <= 0:
            raise GateError("{}: {} must be a positive integer".format(BUDGET_REL, key))
    version = data["claude-code-version"]
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        raise GateError("{}: claude-code-version must be an X.Y.Z string".format(BUDGET_REL))
    digest = data["binary-sha256"]
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise GateError("{}: binary-sha256 must be 64 lowercase hex digits".format(BUDGET_REL))
    return data


def run(root):
    try:
        budget = load_budget(root)
        m = measure(root)
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    ratchet = budget["ratchet"]
    print("PACK: {} UTF-16 code units ({} unconditional rule files {}, managed block {}, "
          "its imports {})".format(m["pack"], m["rule_files"], m["rules"], m["block"], m["pack_imports"]))
    print("SESSION: {} UTF-16 code units (rule files {}, whole CLAUDE.md {}, its imports {})"
          .format(m["session"], m["rules"], m["claude"], m["session_imports"]))
    print("conditional rule files not counted: {}".format(m["conditional_files"]))
    print("ratchet {}; ceiling {} (the target, reported, not enforced); loading modelled on Claude "
          "Code {} (binary sha256 {})".format(ratchet, budget["ceiling"], budget["claude-code-version"],
                                                 budget["binary-sha256"]))
    findings = list(m["findings"])
    if m["pack"] > ratchet:
        findings.append("PACK {} exceeds the ratchet {} by {}; shrink the always-loaded pack "
                        "text, or raise the ratchet in {} only with a reviewed reason".format(
                            m["pack"], ratchet, m["pack"] - ratchet, BUDGET_REL))
    if findings:
        print("FAIL: {} instruction-budget finding(s)".format(len(findings)))
        for f in findings:
            print("  - " + f)
        return 1
    if m["pack"] < ratchet:
        print("note: PACK is {} under the ratchet; lower the ratchet to {} in {} to keep the "
              "saving".format(ratchet - m["pack"], m["pack"], BUDGET_REL))
    print("PASS: PACK {} is within the ratchet {}".format(m["pack"], ratchet))
    return 0


# SELF-TEST: production code ends here; the red-on-revert mutations below target only the text above.
# Fixture trees only, never the live corpus. Every case asserts its exact count or exit code through the
# instrumented check() choke point, and the executed id set is reconciled against tools/selftest_checks.toml
# (suite instruction-budget-selftest), both in-run and by tools/check_selftest_execution.py. The
# revert/ cases put the frontmatter removal, the ratchet comparison, the prefix scan, the scope normalizer,
# and the comment shape back to a broken form in a mutant
# copy of the production code above and require the matching case to turn red.

SUITE_ID = "instruction-budget-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
MUTATION_BOUNDARY = "# SELF-TEST:" + " production code ends here"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _budget_text(ratchet):
    return ('ratchet = {}\nceiling = 90000\nclaude-code-version = "2.1.287"\nbinary-sha256 = "{}"\n'
            .format(ratchet, "0" * 64))


def _tree(root, rules=None, block="", outside="", extra=None, ratchet=10 ** 6):
    """A fixture repository: rule files {rel: str or bytes} under .claude/rules/, a CLAUDE.md
    holding `outside` then the managed block around `block` (no added newlines, so the block counts
    exactly len(block)), any `extra` files {rel: text}, and a budget source with `ratchet`."""
    (root / RULES_REL).mkdir(parents=True)
    for rel, body in (rules or {}).items():
        path = root / RULES_REL / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
    claude = outside + gen_claude.BEGIN + block + gen_claude.END
    (root / CLAUDE_REL).write_bytes(claude.encode("utf-8"))
    for rel, body in (extra or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode("utf-8"))
    (root / BUDGET_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / BUDGET_REL).write_bytes(_budget_text(ratchet).encode("utf-8"))
    return root


def _quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args)


def _stderr_of(fn, *args):
    """(fn(*args), what it wrote to stderr)."""
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        code = fn(*args)
    return code, err.getvalue()


def _measured(fn, root):
    """`fn(root)`, or the string "GateError" when it fails closed."""
    try:
        return fn(root)
    except Exception as exc:  # the mutant module carries its own GateError class
        if type(exc).__name__ == "GateError":
            return "GateError"
        raise


def _mutant(tmp, old, new):
    """A module loaded from the production prefix of this file with `old` replaced by `new` exactly once,
    written under the fixture directory `tmp` and loaded with importlib. A boundary or target that does
    not match exactly once is a harness error (exit 2)."""
    import importlib.util
    source = Path(__file__).read_text(encoding="utf-8")
    if source.count(MUTATION_BOUNDARY) != 1:
        print("SELF-TEST HARNESS ERROR: the mutation boundary is not unique", file=sys.stderr)
        sys.exit(2)
    production = source.split(MUTATION_BOUNDARY)[0]
    if production.count(old) != 1:
        print("SELF-TEST HARNESS ERROR: revert target {!r} does not match exactly once".format(old),
              file=sys.stderr)
        sys.exit(2)
    path = Path(tempfile.mkdtemp(prefix="mutant-", dir=str(tmp))) / "check_instruction_budget_mutant.py"
    path.write_bytes(production.replace(old, new, 1).encode("utf-8"))
    spec = importlib.util.spec_from_file_location("check_instruction_budget_mutant", path)
    mutant = importlib.util.module_from_spec(spec)
    # The prefix puts its own directory on sys.path; restore it so the scratch copy never shadows tools/.
    saved = list(sys.path)
    try:
        spec.loader.exec_module(mutant)
    finally:
        sys.path[:] = saved
    return mutant


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    # ValueError and RecursionError too, as in load_budget.
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc),
              file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(
        SUITE_ID, CHECKS_MANIFEST), file=sys.stderr)
    return None


def _write_report(report_path):
    if report_path is None:
        return True
    try:
        with open(report_path, "w", encoding="utf-8") as handle:
            json.dump({"format_version": 1, "suite": SUITE_ID, "check_ids": EXECUTED}, handle)
            handle.write("\n")
    except OSError as exc:
        print("SELF-TEST HARNESS ERROR: cannot write execution report {}: {}".format(
            report_path, exc), file=sys.stderr)
        return False
    return True


def self_test(report_path=None):
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-instruction-budget-selftest-"))
    try:
        # Counting: frontmatter goes with the blank line after it; a whole-line comment between blank lines
        # goes, its blank lines stay; a non-ASCII BMP character is one unit and an astral character two.
        front = _tree(tmp / "front", rules={"a.md": "---\ncorpus-id: x\n---\n\nBody\n"})
        check("count/frontmatter-and-blank-line-removed", _measured(measure, front)["rules"], 5)
        comment = _tree(tmp / "comment", rules={"a.md": "<!-- hidden -->\n\nB\n"})
        check("count/html-comment-removed", _measured(measure, comment)["rules"], 3)
        # Every other comment is counted: in a fence, in a code span, inside a paragraph, not between blank
        # lines, spread over lines, inside an HTML block, after an indented HTML opener, in a CRLF file.
        fenced = _tree(tmp / "fenced", rules={"a.md": "```\n<!--" + "X" * 100 + "-->\n```\n"})
        check("count/comment-in-fence-kept", _measured(measure, fenced)["rules"], 116)
        inline = _tree(tmp / "inline", rules={"a.md": "A <!--" + "X" * 100 + "--> B\n"})
        check("count/comment-in-paragraph-kept", _measured(measure, inline)["rules"], 112)
        span = _tree(tmp / "span", rules={"a.md": "`<!--" + "X" * 100 + "-->`\n"})
        check("count/comment-in-code-span-kept", _measured(measure, span)["rules"], 110)
        for check_id, case, body in (
                ("count/comment-after-paragraph-line-kept", "after-line", "A\n<!--" + "X" * 100 + "-->\n"),
                ("count/multi-line-comment-kept", "multi-line", "<!--\n" + "X" * 100 + "\n-->\n"),
                ("count/comment-inside-pre-block-kept", "pre",
                 "<pre>\n\n<!--" + "X" * 100 + "-->\n\n</pre>\n"),
                ("count/indented-div-then-comment-kept", "div", "  <div>\n<!--" + "X" * 1000 + "-->\n"),
                ("count/list-item-comment-kept", "list-item", "- a <!--" + "X" * 100 + "-->\n"),
                ("count/crlf-comment-kept", "crlf", "\r\n<!--" + "X" * 100 + "-->\r\n\r\n")):
            kept = _tree(tmp / ("kept-" + case), rules={"a.md": body})
            check(check_id, _measured(measure, kept)["rules"], utf16_units(body))
        fenceblock = _tree(tmp / "fenceblock", outside="```\n", block="\n\n<!--" + "X" * 100 + "-->\n\n")
        check("count/block-comment-after-open-fence-kept", _measured(measure, fenceblock)["block"], 111)
        bom = _tree(tmp / "bom", rules={"a.md": "\ufeff---\nkind: x\n---\nA\n"})
        check("count/bom-before-frontmatter-removed", _measured(measure, bom)["rules"], 2)
        bmp = _tree(tmp / "bmp", rules={"a.md": "é"})
        check("count/bmp-character-one-unit", _measured(measure, bmp)["rules"], 1)
        astral = _tree(tmp / "astral", rules={"a.md": "\U0001F600"})
        check("count/astral-character-two-units", _measured(measure, astral)["rules"], 2)

        # Scope: `paths:` globs that load everywhere once normalized as the loader does are counted;
        # `paths: ["src/**"]` is conditional and is not counted.
        star = _tree(tmp / "star", rules={"a.md": '---\npaths: ["**"]\n---\nabc\n'})
        m = _measured(measure, star)
        check("scope/paths-double-star-counted", (m["rules"], m["rule_files"]), (4, 1))
        trail = _tree(tmp / "trail", rules={"a.md": '---\npaths:\n  - "**/**"\n---\nabc\n'})
        m = _measured(measure, trail)
        check("scope/paths-trailing-double-star-stripped-counted", (m["rules"], m["rule_files"]), (4, 1))
        for check_id, case, scope in (("scope/paths-slash-double-star-counted", "slash", 'paths: "/**"'),
                                      ("scope/paths-trailing-comma-counted", "comma", 'paths: "**,"'),
                                      ("scope/paths-list-slash-double-star-counted", "list",
                                       'paths: ["**", "/**"]'),
                                      ("scope/paths-spaced-double-star-counted", "spaced", 'paths: " ** "'),
                                      ("scope/paths-comma-split-counted", "split", 'paths: "**,**"'),
                                      ("scope/paths-brace-expanded-counted", "brace", 'paths: "{**,**}"'),
                                      ("scope/paths-empty-list-counted", "empty", "paths: []"),
                                      ("scope/dotted-key-frontmatter-counted", "dotted",
                                       'paths: ["src/**"]\nx.y: a: b'),
                                      ("scope/at-value-frontmatter-counted", "at-value",
                                       'paths: ["src/**"]\nv2: @owner'),
                                      ("scope/unclassified-frontmatter-line-counted", "unclassified",
                                       'paths: ["src/**"]\nnot a key line')):
            wide = _tree(tmp / ("wide-" + case), rules={"a.md": "---\n" + scope + "\n---\n" + "X" * 100})
            m = _measured(measure, wide)
            check(check_id, (m["rules"], m["rule_files"], m["conditional_files"]), (100, 1, 0))
        for check_id, case, scope in (("exit/paths-null-2", "null", "paths: null"),
                                      ("exit/paths-mapping-2", "mapping", "paths: {bad: value}"),
                                      ("exit/paths-bool-item-2", "bool-item", "paths:\n  - false"),
                                      ("exit/paths-number-item-2", "number-item", "paths: [1]"),
                                      ("exit/paths-nested-brace-2", "nested", 'paths: "{a,{b,c}}"'),
                                      ("exit/paths-unbalanced-brace-2", "unbalanced", 'paths: "src/{a,b"'),
                                      ("exit/paths-escape-2", "escape", "paths: 'src/\\*.md'")):
            typed = _tree(tmp / ("typed-" + case), rules={"a.md": "---\n" + scope + "\n---\n" + "X" * 100})
            check(check_id, _quiet(run, typed), 2)
        src = _tree(tmp / "src", rules={"a.md": '---\npaths: ["src/**"]\n---\nabc\n', "b.md": "xy\n",
                                        "c.md": '---\npaths: "{src,lib}/**"\n---\nabc\n'})
        m = _measured(measure, src)
        check("scope/paths-src-excluded", (m["rules"], m["rule_files"], m["conditional_files"]), (3, 1, 2))

        # Imports and totals: an @import in the managed block is followed and counted into PACK; SESSION
        # adds the bytes outside the block.
        imp = _tree(tmp / "import", block="@docs/extra.md", extra={"docs/extra.md": "Imported\n"})
        m = _measured(measure, imp)
        check("import/managed-block-import-followed", (m["block"], m["pack_imports"], m["pack"]), (14, 9, 23))
        ruleimp = _tree(tmp / "ruleimp", rules={"a.md": "@big.txt\n"},
                        extra={".claude/rules/big.txt": "---\nkind: x\n---\n" + "X" * 100})
        m = _measured(measure, ruleimp)
        check("import/rule-file-import-counted", (m["rules"], m["pack_imports"], m["pack"], m["session"]),
              (9, 100, 109, 171))
        # Every `@` is a candidate whatever Markdown surrounds it: delimiters after the path, a tag or a
        # link around it, a comment or a code span holding it, and backticks paired across blocks.
        big = {".claude/rules/big.txt": "X" * 1000}
        for check_id, case, body in (("import/bold-import-counted", "bold", "**@big.txt**\n"),
                                     ("import/underscore-import-counted", "underscore", "_@big.txt_\n"),
                                     ("import/html-tag-import-counted", "tag", "<b>@big.txt</b>\n"),
                                     ("import/link-import-counted", "link", "[see @big.txt](x)\n"),
                                     ("import/list-item-comment-import-counted", "list-comment",
                                      "- a <!-- @big.txt -->\n"),
                                     ("import/backticks-across-heading-counted", "heading",
                                      "` x\n# @big.txt\n` y\n"),
                                     ("import/backticks-across-list-items-counted", "list-items",
                                      "- ` x\n- @big.txt\n- ` y\n"),
                                     ("import/code-span-import-counted", "span", "`@big.txt`\n"),
                                     ("import/case-insensitive-import-counted", "case", "@BIG.txt\n")):
            marked = _tree(tmp / ("marked-" + case), rules={"a.md": body}, extra=big)
            check(check_id, _measured(measure, marked)["pack_imports"], 1000)
        prefixes = _tree(tmp / "prefixes", rules={"a.md": "@x.txt_b.txt\n"},
                         extra={".claude/rules/x.txt_b.txt": "X" * 10, ".claude/rules/x.txt": "Y" * 7})
        check("import/every-prefix-naming-a-file-counted", _measured(measure, prefixes)["pack_imports"], 17)
        twocase = _tree(tmp / "twocase", block="@Docs/Big.md", extra={"docs/big.md": "x", "DOCS/big.md": "y"})
        code, err = _stderr_of(run, twocase)
        check("exit/case-insensitive-two-matches-ambiguous-2", (code, "ambiguous" in err), (2, True))
        ruleagents = _tree(tmp / "ruleagents", rules={"a.md": "@../../AGENTS.md\n"}, extra={"AGENTS.md": "x\n"})
        check("exit/rule-file-agents-import-1", _quiet(run, ruleagents), 1)
        boldagents = _tree(tmp / "boldagents", rules={"a.md": "**@../../AGENTS.md**\n"},
                           extra={"AGENTS.md": "x\n"})
        check("exit/bold-agents-import-1", _quiet(run, boldagents), 1)
        listspan = _tree(tmp / "listspan", block="- see `cat @AGENTS.md now`\n", extra={"AGENTS.md": "x\n"})
        check("exit/list-item-code-span-agents-import-1", _quiet(run, listspan), 1)
        lowercase = _tree(tmp / "lowercase", block="@agents.md\n")
        check("exit/lowercase-agents-name-1", _quiet(run, lowercase), 1)
        # A candidate naming no file adds nothing and does not fail.
        rulemissing = _tree(tmp / "rulemissing", rules={"a.md": "@missing.txt\n"})
        check("import/missing-target-skipped-0",
              (_measured(measure, rulemissing)["pack_imports"], _quiet(run, rulemissing)), (0, 0))
        email = _tree(tmp / "email", outside="Mail name@example.com before merging.\n")
        check("exit/outside-block-email-prose-0", _quiet(run, email), 0)
        mention = _tree(tmp / "mention", outside="Ping @alice before merging...@...\n")
        check("exit/outside-block-mention-prose-0", _quiet(run, mention), 0)
        fragment = _tree(tmp / "fragment", block="@docs/x.md#usage", extra={"docs/x.md": "Doc\n"})
        check("import/fragment-cut-followed", _measured(measure, fragment)["pack_imports"], 4)
        # An import the gate cannot settle exits 2 and says it is ambiguous.
        home = _tree(tmp / "home", outside="See @~/notes.md first.\n")
        code, err = _stderr_of(run, home)
        check("exit/home-import-ambiguous-2", (code, "ambiguous" in err), (2, True))
        undecodable = _tree(tmp / "undecodable", block="@docs/x.md")
        (undecodable / "docs").mkdir()
        (undecodable / "docs" / "x.md").write_bytes(b"ok \xff\n")
        code, err = _stderr_of(run, undecodable)
        check("exit/undecodable-import-ambiguous-2", (code, "ambiguous" in err), (2, True))
        rulecycle = _tree(tmp / "rulecycle", rules={"a.md": "@b.txt\n"},
                          extra={".claude/rules/b.txt": "@c.txt\n", ".claude/rules/c.txt": "@b.txt\n"})
        check("import/rule-file-cycle-terminates", _measured(measure, rulecycle)["pack_imports"], 14)
        mismatched = _tree(tmp / "mismatched", block="` literal\n\n@AGENTS.md\n\n`` literal\n",
                           extra={"AGENTS.md": "X" * 100})
        check("exit/mismatched-backticks-agents-import-1", _quiet(run, mismatched), 1)
        spanned = _tree(tmp / "spanned", block="`@AGENTS.md` and ``@RULES-INDEX.md``\n")
        check("exit/code-span-banned-import-1", _quiet(run, spanned), 1)
        # Symlinks under .claude/rules: a directory resolving inside the repository is followed and
        # counted; one escaping the repository, a loop, or a second route to a directory exits 2.
        linked = _tree(tmp / "linked", rules={"a.md": "ab"}, extra={"shared/b.md": "X" * 50})
        os.symlink(os.path.join("..", "..", "shared"), str(linked / RULES_REL / "shared"))
        m = _measured(measure, linked)
        check("scope/symlinked-rule-dir-counted", (m["rules"], m["rule_files"]), (52, 2))
        outdir = tmp / "linkout" / "outside"
        outdir.mkdir(parents=True)
        (outdir / "b.md").write_bytes(b"X\n")
        linkout = _tree(tmp / "linkout" / "repo")
        os.symlink(str(outdir), str(linkout / RULES_REL / "out"))
        check("exit/symlinked-rule-dir-escaping-2", _quiet(run, linkout), 2)
        linkfile = _tree(tmp / "linkfile" / "repo")
        os.symlink(str(outdir / "b.md"), str(linkfile / RULES_REL / "b.md"))
        check("exit/symlinked-rule-file-escaping-2", _quiet(run, linkfile), 2)
        loop = _tree(tmp / "loop", rules={"sub/a.md": "a"})
        os.symlink("..", str(loop / RULES_REL / "sub" / "back"))
        check("exit/symlinked-rule-dir-loop-2", _quiet(run, loop), 2)
        selfloop = _tree(tmp / "selfloop")
        os.symlink("self", str(selfloop / RULES_REL / "self"))
        check("exit/symlink-self-loop-2", _quiet(run, selfloop), 2)
        twice = _tree(tmp / "twice", rules={"real/a.md": "a"})
        os.symlink("real", str(twice / RULES_REL / "alias"))
        code, err = _stderr_of(run, twice)
        check("exit/rule-dir-reached-twice-ambiguous-2", (code, "ambiguous" in err), (2, True))

        session = _tree(tmp / "session", rules={"a.md": "abc\n"}, block="Block", outside="Hello\n")
        m = _measured(measure, session)
        check("totals/session-adds-outside-block", (m["pack"], m["session"]), (9, 77))

        # Exit codes: at the ratchet passes; one unit over fails; a banned import fails.
        at = _tree(tmp / "at", rules={"a.md": "x" * 10}, ratchet=10)
        check("exit/at-ratchet-0", _quiet(run, at), 0)
        over = _tree(tmp / "over", rules={"a.md": "x" * 10}, ratchet=9)
        check("exit/one-over-ratchet-1", _quiet(run, over), 1)
        index = _tree(tmp / "index", block="@.claude/RULES-INDEX.md",
                      extra={".claude/RULES-INDEX.md": "x\n"})
        check("exit/rules-index-import-1", _quiet(run, index), 1)
        agents = _tree(tmp / "agents", block="@AGENTS.md", extra={"AGENTS.md": "x\n"})
        check("exit/agents-import-1", _quiet(run, agents), 1)

        # Fail-closed (exit 2).
        unreadable = _tree(tmp / "unreadable")
        os.symlink("missing-target.md", str(unreadable / RULES_REL / "broken.md"))
        check("exit/unreadable-rule-file-2", _quiet(run, unreadable), 2)
        badutf8 = _tree(tmp / "badutf8", rules={"a.md": b"ok \xff\xfe\n"})
        check("exit/invalid-utf8-rule-file-2", _quiet(run, badutf8), 2)
        unterminated = _tree(tmp / "unterminated", rules={"a.md": '---\npaths: ["**"]\nBody\n'})
        check("exit/unterminated-frontmatter-2", _quiet(run, unterminated), 2)
        dupkey = _tree(tmp / "dupkey", rules={"a.md": "---\nkind: a\nkind: b\n---\nBody\n"})
        check("exit/duplicate-frontmatter-key-2", _quiet(run, dupkey), 2)
        badbudget = _tree(tmp / "badbudget")
        (badbudget / BUDGET_REL).write_bytes(b"ratchet = true\nceiling = 90000\n")
        check("exit/malformed-budget-2", _quiet(run, badbudget), 2)
        nomarkers = _tree(tmp / "nomarkers")
        (nomarkers / CLAUDE_REL).write_bytes(b"# CLAUDE.md with no managed block\n")
        check("exit/missing-managed-block-markers-2", _quiet(run, nomarkers), 2)
        escape = _tree(tmp / "escape" / "repo", block="@../outside.md")
        (tmp / "escape" / "outside.md").write_bytes(b"outside\n")
        check("exit/import-escaping-repo-2", _quiet(run, escape), 2)

        # The module docstring states the model the code implements and its disclosed residuals.
        doc = sys.modules[__name__].__doc__ or ""
        check("doc/model-and-residuals-stated",
              [phrase for phrase in ("OVER-COUNT BY CONSTRUCTION", "KNOWN OVER-COUNTS", "column 0",
                                     "counted rule file", "typed `paths:`", "brace alternation",
                                     "cannot classify", "code span", "byte order mark",
                                     "recursively through imported", "symlinked directory", "name@example.com",
                                     "ambiguous", "case-insensitively", "never missed", "path filter",
                                     "a banned name there exits 1") if phrase not in doc], [])

        # Red on revert: each fix put back in a mutant copy of the production code must turn its case red.
        unstripped = _mutant(tmp, "body = bare[m.end():]", "body = bare")
        check("revert/frontmatter-removal-red",
              (_measured(measure, front)["rules"], _measured(unstripped.measure, front)["rules"]), (5, 27))
        lenient = _mutant(tmp, 'if m["pack"] > ratchet:', 'if m["pack"] > ratchet + 1:')
        check("revert/ratchet-comparison-red", (_quiet(run, over), _quiet(lenient.run, over)), (1, 0))
        bold = tmp / "marked-bold"
        whole = _mutant(tmp, "cuts = sorted({len(run)} | {k for k, ch in enumerate(run) if ch in CUT_BEFORE}, "
                             "reverse=True)", "cuts = [len(run)]")
        check("revert/prefix-scan-red",
              (_measured(measure, bold)["pack_imports"], _measured(whole.measure, bold)["pack_imports"]), (1000, 0))
        split = tmp / "wide-split"
        unsplit = _mutant(tmp, "for piece in _split_top(value, where):", "for piece in [value]:")
        check("revert/scope-normalizer-red",
              (_measured(measure, split)["rule_files"], _measured(unsplit.measure, split)["rule_files"]), (1, 0))
        shape = tmp / "kept-after-line"
        anywhere = _mutant(tmp, "                    and (idx == 0 or not lines[idx - 1].strip(\" \\t\"))\n", "")
        check("revert/comment-shape-red",
              (_measured(measure, shape)["rules"], _measured(anywhere.measure, shape)["rules"]), (110, 2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not _write_report(report_path):
        return 2
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("FAIL: check_instruction_budget self-test")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("PASS: check_instruction_budget self-test: {} unique checks executed (frontmatter and the blank "
          "line after it removed, a whole-line comment between blank lines removed and every other comment "
          "counted, BMP and astral characters counted as 1 and 2 units, a BOM before frontmatter removed, paths "
          "that normalize to ** counted and src/** excluded, unclassified frontmatter counted, typed or "
          "unreadable globs exit 2, every @ candidate followed whatever Markdown surrounds it, case-insensitive "
          "imports counted, banned names exit 1 in any case or code span, a candidate naming no file skipped, "
          "home relative, escaping, undecodable, and doubly matched imports exit 2 as ambiguous, symlinked rule "
          "directories counted and escaping, looping, or doubled symlinks exit 2, cycles ending, SESSION over "
          "PACK, the ratchet boundary, unreadable, invalid UTF-8, malformed frontmatter, budget, and markers "
          "exit 2, the docstring stating the model, and every red-on-revert flip turning red); execution set "
          "reconciled against tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


def main(argv):
    if not argv:
        return run(Path(__file__).resolve().parents[1])
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return self_test(argv[1])
    print("usage: check_instruction_budget.py [--self-test | --execution-report ABS_PATH]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
