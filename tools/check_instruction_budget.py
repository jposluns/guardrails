#!/usr/bin/env python3
"""Instruction-budget gate: hold what Claude Code loads unconditionally to a committed ratchet.

Claude Code reads every `.claude/rules/**/*.md` file that carries no `paths:` scope, and the project
CLAUDE.md, into every session before the first prompt. That text is paid for in every session, so this
gate measures it and fails when the pack's share grows.

What it measures, in UTF-16 code units (the length of the text encoded utf-16-le, divided by 2; a
character in the Basic Multilingual Plane is one unit, an astral character such as an emoji is two):

  RULE FILES. Every `.claude/rules/**/*.md` file that is unconditional. A leading frontmatter block is
      removed first (FRONTMATTER_RE below, first match only; its trailing `\\s*` also takes the blank line
      after the closing fence), then every HTML comment. A file whose frontmatter has a `paths:` key is
      conditional and is not counted, unless every glob in it is `**` once a single trailing `/**` is
      stripped, in which case it loads everywhere and is counted.
  MANAGED BLOCK. The pack-managed RULES-INDEX block of CLAUDE.md (the markers tools/gen_claude.py
      writes), HTML comments removed, plus every file it imports with Claude Code's `@path` syntax,
      followed recursively and counted once each.

Two totals are reported separately:

  PACK     the rule files plus the managed block plus its imports. This is the part the pack controls,
           and the only total the gate enforces.
  SESSION  the rule files plus the whole CLAUDE.md (HTML comments removed) plus every file it imports.
           Reported, not enforced: the bytes outside the managed block belong to the adopter.

The budget source, .aiqt/core/instruction-budget.toml, carries exactly four keys: `ratchet` (the PACK
figure enforced today; lower it as the pack shrinks, and raise it only with a reviewed reason), `ceiling`
(the target PACK figure, reported, not enforced), and `claude-code-version` and `binary-sha256`, which pin
the Claude Code build whose loading behaviour this gate models.

DISCLOSED RESIDUAL. This gate measures the repository's own files against a model of Claude Code's loading
rules; it does not run Claude Code. It does not measure user-level files (~/.claude/CLAUDE.md and
~/.claude/rules/), which load in every session too; an AGENTS.md or other file loaded through the
instructionFiles setting rather than an `@` import; CLAUDE.md files in other directories, or
CLAUDE.local.md; skills, hooks, tool definitions, or the system prompt; or loading by a Claude Code version
other than the one pinned in the budget source. Frontmatter is read with a small fail-closed subset of YAML
(top-level `key: value` lines, flow lists, and `- item` block lists), so a file using other YAML forms
exits 2 rather than being guessed at. Import detection skips fenced code blocks, inline code spans, and
HTML comments, and takes `@` only at a line start or after whitespace; an import that does not resolve to
a readable file inside the repository exits 2, where Claude Code may instead ignore it. A symlinked
directory under .claude/rules/ is not descended (a committed symlink is rejected repo-wide by the manifest
gate). The count is UTF-16 code units, not tokens, so it tracks size, not model cost.

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
# of the rule tree, so either is a finding whatever the measured total.
BANNED_IMPORTS = ("RULES-INDEX.md", "AGENTS.md")

# The leading frontmatter block, exactly as the approved design states it (first match only).
FRONTMATTER_RE = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?")
# A file that opens a frontmatter fence FRONTMATTER_RE cannot close is malformed, never body text.
FRONTMATTER_OPEN_RE = re.compile(r"^---\s*\n")
HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
# The pinned loader's Markdown lexer (marked, gfm off) and its comment rule, as extracted from the pinned
# binary: an `html` token whose raw starts with `<!--` and holds `-->` loses its comments, and is dropped
# whole when what is left is blank under JavaScript's trim(); every other token is kept as written.
COMMENT_TOKEN_RE = re.compile(r"<!--(?:-?>|[\s\S]*?(?:-->|\Z))[^\n]*(?:\n+|\Z)")
JS_WHITESPACE = (" \t\n\v\f\r\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008"
                 "\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")
FENCE_OPEN_RE = re.compile(r"(`{3,}(?=[^`]*$)|~{3,})")
RAW_HTML_OPEN_RE = re.compile(r"<(?:(script|pre|style|textarea)(?=[\s>]|$)|(\?)|(!\[CDATA\[)|(![A-Za-z])|([A-Za-z/]))",
                              re.I)
_CONTAINER = r"[ \t>]*(?:(?:[*+-]|\d{1,9}[.)])[ \t>]*)*"
FENCE_LIKE_RE = re.compile(_CONTAINER + r"(?:```|~~~)")
HTML_LIKE_RE = re.compile(_CONTAINER + r"<(?:script|pre|style|textarea|!|\?)", re.I)
INLINE_COMMENT_RE = re.compile(r"<!--(?!-?>)[\s\S]*?-->")
INLINE_TAG_RE = re.compile(r"<[A-Za-z/][^<>]*>|\[[^\[\]\n]*\]\([^\s()]*\)")
BLANK_LINE_RE = re.compile(r"\n(?:[ \t]*\r?\n)+")
ASCII_PUNCT = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
ESCAPED_SPACE = "\x5c\x20"
# The loader's own import token (`@path`, a backslash escaping a space), taken here after line start,
# whitespace, or any character that can end a Markdown inline token: a superset of where the loader's text
# tokens start.
IMPORT_RE = re.compile(r"(?:^|(?<=[^0-9A-Za-z]))@((?:[^\s\\]|\\[ ])+)", re.M)
YAML_TYPED_RE = re.compile(
    r"(?:null|Null|NULL|~|true|True|TRUE|false|False|FALSE|yes|Yes|YES|no|No|NO|on|On|ON|off|Off|OFF|y|Y|n|N)"
    r"|[-+]?(?:\d[\d_]*(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?|0x[0-9a-fA-F_]+|0o[0-7_]+"
    r"|\.(?:inf|Inf|INF|nan|NaN|NAN))")
KEY_RE = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.-]*)[ \t]*:(.*)$")
ITEM_RE = re.compile(r"^[ \t]*-[ \t]+(.*)$")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class GateError(Exception):
    """A fail-closed condition (an unreadable input, malformed frontmatter, markers, an unresolvable
    import, or a malformed budget source): exit 2."""


def utf16_units(text):
    """The length of `text` in UTF-16 code units: one per BMP character, two per astral character."""
    return len(text.encode("utf-16-le")) // 2


def _scan_blocks(text):
    """The top-level fenced code blocks and HTML comment blocks of `text` as the pinned loader's lexer
    sees them, from a line scan: a list of (kind, start, end), kind "fence" or "comment" (a comment token
    runs to the rest of the line that closes it and the newlines after that). Only a block opened at
    column 0 is reported. A fence or raw HTML opener that is indented, tab led, list or quote nested, or
    met inside an HTML block, an unterminated comment, or a lone CR leaves the scan unsure, and nothing
    after that point is reported, so a comment there is kept and counted, never guessed away."""
    spans = []
    if re.search(r"\r(?!\n)", text):
        return spans
    pos, state, start = 0, None, 0
    while pos < len(text):
        nl = text.find("\n", pos)
        end = len(text) if nl < 0 else nl + 1
        line = text[pos:end].rstrip("\n")
        if line.endswith("\r"):
            line = line[:-1]
        if state is None:
            if line.startswith("<!--"):
                m = COMMENT_TOKEN_RE.match(text, pos)
                if "-->" not in m.group(0):
                    return spans
                spans.append(("comment", pos, m.end()))
                pos = m.end()
                continue
            fence = FENCE_OPEN_RE.match(line)
            raw = RAW_HTML_OPEN_RE.match(line)
            if fence:
                state = ("fence", re.compile(r" {0,3}" + re.escape(fence.group(1)) + r"[~`]* *"))
                start = pos
            elif raw:
                tag, pi, cdata, decl = raw.group(1), raw.group(2), raw.group(3), raw.group(4)
                term = ("</" + tag + ">" if tag else r"\?>" if pi else r"\]\]>" if cdata
                        else ">" if decl else None)
                if term is None:
                    state = ("blank", None)
                elif not re.compile(term, re.I).search(line, raw.end()):
                    state = ("raw", re.compile(term, re.I))
            elif FENCE_LIKE_RE.match(line) or HTML_LIKE_RE.match(line):
                return spans
        elif state[0] == "fence":
            if state[1].fullmatch(line):
                spans.append(("fence", start, end))
                state = None
        elif FENCE_LIKE_RE.match(line) or HTML_LIKE_RE.match(line):
            return spans
        elif state[0] == "raw":
            if state[1].search(line):
                state = None
        elif not line.strip(" \t"):
            state = None
        pos = end
    if state is not None and state[0] == "fence":
        spans.append(("fence", start, len(text)))
    return spans


def strip_comments(text):
    """`text` as the pinned loader leaves it: each top-level HTML comment block _scan_blocks reports loses
    its comments, and goes whole when the rest is blank. A comment inside a paragraph, a list, a quote, a
    code block, or a code span is kept and counted, as the loader keeps it."""
    if "<!--" not in text:
        return text
    out, pos = [], 0
    for kind, start, end in _scan_blocks(text):
        if kind == "comment":
            rest = HTML_COMMENT_RE.sub("", text[start:end])
            out.append(text[pos:start] + (rest if rest.strip(JS_WHITESPACE) else ""))
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


def parse_frontmatter(block, where):
    """The frontmatter as {key: value}, read with a small fail-closed YAML subset: blank and `#`
    comment lines are skipped, a top-level `key: value` line takes gen_rules' validated scalar and
    flow-list parser, and `- item` lines form a block list under a key whose value is empty. Any other
    line, a duplicate key, or a malformed value is GateError."""
    fm = {}
    open_key = None
    for raw in block.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        item = ITEM_RE.match(line)
        if item and open_key is not None:
            if open_key == "paths":
                _plain_string(item.group(1).strip(), where)
            try:
                fm[open_key].append(gen_rules._unquote(item.group(1).strip()))
            except ValueError as exc:
                raise GateError("{}: frontmatter: {}".format(where, exc))
            continue
        kv = KEY_RE.match(line)
        if kv is None:
            raise GateError("{}: malformed frontmatter line {!r}".format(where, stripped))
        key, value = kv.group(1), kv.group(2).strip()
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
        try:
            fm[key] = gen_rules._value(value)
        except ValueError as exc:
            raise GateError("{}: frontmatter: {}".format(where, exc))
    return fm


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
    """None when the frontmatter has no `paths:` key; else its globs as a non-empty list of non-empty
    strings (a scalar is one glob). Any other shape is malformed: GateError."""
    if "paths" not in fm:
        return None
    value = fm["paths"]
    globs = [value] if isinstance(value, str) else value
    if (not isinstance(globs, list) or not globs
            or not all(isinstance(g, str) and g.strip() for g in globs)):
        raise GateError("{}: `paths:` must be a glob or a non-empty list of globs".format(where))
    return globs


def loads_everywhere(globs):
    """True when every glob is `**` once a single trailing `/**` is stripped (so `**` and `**/**`)."""
    return all((g[:-3] if g.endswith("/**") else g) == "**" for g in globs)


def rule_files(root):
    """Every `*.md` file under .claude/rules/, in a stable order. GateError when the tree is missing or
    cannot be walked."""
    base = root / RULES_REL
    if not base.is_dir():
        raise GateError("{} is missing or not a directory".format(RULES_REL))

    def _raise(exc):
        raise GateError("cannot walk {} ({})".format(RULES_REL, exc))

    found = []
    for dirpath, dirnames, filenames in os.walk(base, onerror=_raise):
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(".md"):
                found.append(Path(dirpath) / name)
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
            globs = path_globs(parse_frontmatter(front, where), where)
            if globs is not None and not loads_everywhere(globs):
                conditional += 1
                continue
        total += utf16_units(strip_comments(body))
        counted += 1
        origins.append((body, os.path.dirname(os.path.realpath(path)), where))
    return total, counted, conditional, origins


def managed_block(text):
    """The text between the RULES-INDEX markers gen_claude.py writes. Each marker must appear exactly
    once, BEGIN before END; otherwise GateError."""
    begin, end = gen_claude.BEGIN, gen_claude.END
    if text.count(begin) != 1 or text.count(end) != 1:
        raise GateError("{}: the RULES-INDEX markers must each appear exactly once".format(CLAUDE_REL))
    i, j = text.find(begin), text.find(end)
    if j < i:
        raise GateError("{}: the RULES-INDEX END marker precedes BEGIN".format(CLAUDE_REL))
    return text[i + len(begin):j]


def _inline_text(para):
    """`para` (one paragraph: no blank line) with its code spans and HTML comments replaced by a space.
    A backtick run opens a code span only when a run of the same length closes it in the same paragraph;
    an escaped backtick, or one inside an inline HTML tag or a link destination, is literal."""
    out, i, n = [], 0, len(para)
    while i < n:
        ch = para[i]
        if ch == "\\" and i + 1 < n and para[i + 1] in ASCII_PUNCT:
            out.append(para[i:i + 2])
            i += 2
            continue
        if ch in "<[":
            m = INLINE_COMMENT_RE.match(para, i) if ch == "<" else None
            if m:
                out.append(" ")
                i = m.end()
                continue
            m = INLINE_TAG_RE.match(para, i)
            if m:
                out.append(m.group(0))
                i = m.end()
                continue
        if ch == "`":
            j = i
            while j < n and para[j] == "`":
                j += 1
            k, close = j, -1
            while close < 0:
                k = para.find("`", k)
                if k < 0:
                    break
                e = k
                while e < n and para[e] == "`":
                    e += 1
                close = k if e - k == j - i else -1
                k = e
            if close >= 0:
                out.append(" ")
                i = close + j - i
            else:
                out.append(para[i:j])
                i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def import_targets(text):
    """The `@path` import tokens the pinned loader may follow in `text`, a superset of its own: outside
    the top-level fenced code blocks and HTML comment blocks _scan_blocks reports, with the code spans and
    inline comments of each paragraph removed, the `#` fragment cut, and the loader's own token filter
    applied. A construct this scan cannot place is scanned, so an import can be over-reported, never
    missed."""
    pieces, pos = [], 0
    for kind, start, end in _scan_blocks(text):
        pieces.append(text[pos:start])
        if kind == "comment":
            pieces.append(HTML_COMMENT_RE.sub(" ", text[start:end]))
        pos = end
    pieces.append(text[pos:])
    targets = []
    for piece in pieces:
        for para in BLANK_LINE_RE.split(piece):
            for token in IMPORT_RE.findall(_inline_text(para)):
                token = token.split("#", 1)[0].replace(ESCAPED_SPACE, " ")
                if (token and not token.startswith("@") and not re.match(r"[#%^&*()]+", token)
                        and (token.startswith(("./", "~/", "/")) and token != "/"
                             or re.match(r"[a-zA-Z0-9._-]", token))):
                    targets.append(token)
    return targets


def follow_imports(root, origins, findings):
    """{resolved path: units} for every file reachable through `@path` imports from `origins`, a list of
    (text, the directory it sits in, where), each counted once with frontmatter and HTML comments removed
    as for a rule file. An import naming a BANNED_IMPORTS file is appended to `findings`. An import that is
    home relative, absolute, escapes the repository, or does not resolve to a readable regular file is
    GateError."""
    real_root = os.path.realpath(root)
    seen = {}
    pending = list(origins)
    while pending:
        source, base, where = pending.pop()
        for token in import_targets(source):
            if token.startswith("~") or os.path.isabs(token):
                raise GateError("{}: import @{} is outside the repository; cannot evaluate".format(
                    where, token))
            target = os.path.realpath(os.path.join(base, token))
            try:
                inside = os.path.commonpath([target, real_root]) == real_root
            except ValueError:
                inside = False
            if not inside:
                raise GateError("{}: import @{} escapes the repository; cannot evaluate".format(
                    where, token))
            if Path(token).name in BANNED_IMPORTS or os.path.basename(target) in BANNED_IMPORTS:
                findings.append("{}: imports @{}; the pack must not import {}".format(
                    where, token, " or ".join(BANNED_IMPORTS)))
            if target in seen:
                continue
            if not os.path.isfile(target):
                raise GateError("{}: import @{} does not resolve to a file; cannot evaluate".format(
                    where, token))
            rel = Path(os.path.relpath(target, real_root)).as_posix()
            body = split_frontmatter(read_text(Path(target), rel), rel)[1]
            seen[target] = utf16_units(strip_comments(body))
            pending.append((body, os.path.dirname(target), rel))
    return seen


def measure(root):
    """Both totals and their parts. Banned imports reached from the managed block are returned as
    findings; every cannot-evaluate condition raises GateError."""
    rules, counted, conditional, origins = measure_rules(root)
    claude = read_text(root / CLAUDE_REL, CLAUDE_REL)
    block = managed_block(claude)
    findings = []
    real_root = os.path.realpath(root)
    # Claude Code follows imports in rule files as in CLAUDE.md, so theirs count into both totals.
    pack_imports = sum(follow_imports(
        root, origins + [(block, real_root, CLAUDE_REL + " managed block")], findings).values())
    # The adopter owns the bytes outside the block, so a banned import there is not the pack's finding:
    # only the rule files' and the managed block's findings fail the gate.
    session_imports = sum(follow_imports(root, origins + [(claude, real_root, CLAUDE_REL)], []).values())
    block_units = utf16_units(strip_comments(block))
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
# (suite instruction-budget-selftest), both in-run and by tools/check_selftest_execution.py. The two
# revert/ cases put the frontmatter removal and the ratchet comparison back to a broken form in a mutant
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
        # Counting: frontmatter goes with the blank line after it; an HTML comment goes; a non-ASCII BMP
        # character is one unit and an astral character two.
        front = _tree(tmp / "front", rules={"a.md": "---\ncorpus-id: x\n---\n\nBody\n"})
        check("count/frontmatter-and-blank-line-removed", _measured(measure, front)["rules"], 5)
        comment = _tree(tmp / "comment", rules={"a.md": "<!-- hidden\nline -->\nB\n"})
        check("count/html-comment-removed", _measured(measure, comment)["rules"], 2)
        # A comment the loader keeps (in a fence, in a code span, inside a paragraph) is counted.
        fenced = _tree(tmp / "fenced", rules={"a.md": "```\n<!--" + "X" * 100 + "-->\n```\n"})
        check("count/comment-in-fence-kept", _measured(measure, fenced)["rules"], 116)
        inline = _tree(tmp / "inline", rules={"a.md": "A <!--" + "X" * 100 + "--> B\n"})
        check("count/comment-in-paragraph-kept", _measured(measure, inline)["rules"], 112)
        span = _tree(tmp / "span", rules={"a.md": "`<!--" + "X" * 100 + "-->`\n"})
        check("count/comment-in-code-span-kept", _measured(measure, span)["rules"], 110)
        bom = _tree(tmp / "bom", rules={"a.md": "\ufeff---\nkind: x\n---\nA\n"})
        check("count/bom-before-frontmatter-removed", _measured(measure, bom)["rules"], 2)
        bmp = _tree(tmp / "bmp", rules={"a.md": "é"})
        check("count/bmp-character-one-unit", _measured(measure, bmp)["rules"], 1)
        astral = _tree(tmp / "astral", rules={"a.md": "\U0001F600"})
        check("count/astral-character-two-units", _measured(measure, astral)["rules"], 2)

        # Scope: `paths: ["**"]` loads everywhere and counts; a trailing /** is stripped before the test;
        # `paths: ["src/**"]` is conditional and is not counted.
        star = _tree(tmp / "star", rules={"a.md": '---\npaths: ["**"]\n---\nabc\n'})
        m = _measured(measure, star)
        check("scope/paths-double-star-counted", (m["rules"], m["rule_files"]), (4, 1))
        trail = _tree(tmp / "trail", rules={"a.md": '---\npaths:\n  - "**/**"\n---\nabc\n'})
        m = _measured(measure, trail)
        check("scope/paths-trailing-double-star-stripped-counted", (m["rules"], m["rule_files"]), (4, 1))
        for case, scope in (("null", "paths: null"), ("mapping", "paths: {bad: value}"),
                            ("bool-item", "paths:\n  - false"), ("number-item", "paths: [1]"),
                            ("empty-list", "paths: []")):
            typed = _tree(tmp / ("typed-" + case), rules={"a.md": "---\n" + scope + "\n---\n" + "X" * 100})
            check("exit/paths-" + case + "-2", _quiet(run, typed), 2)
        src = _tree(tmp / "src", rules={"a.md": '---\npaths: ["src/**"]\n---\nabc\n', "b.md": "xy\n"})
        m = _measured(measure, src)
        check("scope/paths-src-excluded", (m["rules"], m["rule_files"], m["conditional_files"]), (3, 1, 1))

        # Imports and totals: an @import in the managed block is followed and counted into PACK; SESSION
        # adds the bytes outside the block.
        imp = _tree(tmp / "import", block="@docs/extra.md", extra={"docs/extra.md": "Imported\n"})
        m = _measured(measure, imp)
        check("import/managed-block-import-followed", (m["block"], m["pack_imports"], m["pack"]), (14, 9, 23))
        ruleimp = _tree(tmp / "ruleimp", rules={"a.md": "@big.txt\n"},
                        extra={".claude/rules/big.txt": "---\nkind: x\n---\n" + "X" * 100})
        m = _measured(measure, ruleimp)
        check("import/rule-file-import-counted", (m["rules"], m["pack_imports"], m["pack"], m["session"]),
              (9, 100, 109, 109))
        ruleagents = _tree(tmp / "ruleagents", rules={"a.md": "@../../AGENTS.md\n"}, extra={"AGENTS.md": "x\n"})
        check("exit/rule-file-agents-import-1", _quiet(run, ruleagents), 1)
        rulemissing = _tree(tmp / "rulemissing", rules={"a.md": "@missing.txt\n"})
        check("exit/rule-file-missing-import-2", _quiet(run, rulemissing), 2)
        rulecycle = _tree(tmp / "rulecycle", rules={"a.md": "@b.txt\n"},
                          extra={".claude/rules/b.txt": "@c.txt\n", ".claude/rules/c.txt": "@b.txt\n"})
        check("import/rule-file-cycle-terminates", _measured(measure, rulecycle)["pack_imports"], 14)
        mismatched = _tree(tmp / "mismatched", block="` literal\n\n@AGENTS.md\n\n`` literal\n",
                           extra={"AGENTS.md": "X" * 100})
        check("exit/mismatched-backticks-agents-import-1", _quiet(run, mismatched), 1)
        spanned = _tree(tmp / "spanned", block="`@AGENTS.md` and ``@RULES-INDEX.md``\n")
        check("import/matched-code-span-skipped", _quiet(run, spanned), 0)
        session = _tree(tmp / "session", rules={"a.md": "abc\n"}, block="Block", outside="Hello\n")
        m = _measured(measure, session)
        check("totals/session-adds-outside-block", (m["pack"], m["session"]), (9, 15))

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
        badline = _tree(tmp / "badline", rules={"a.md": "---\nnot a key line\n---\nBody\n"})
        check("exit/malformed-frontmatter-line-2", _quiet(run, badline), 2)
        badbudget = _tree(tmp / "badbudget")
        (badbudget / BUDGET_REL).write_bytes(b"ratchet = true\nceiling = 90000\n")
        check("exit/malformed-budget-2", _quiet(run, badbudget), 2)
        nomarkers = _tree(tmp / "nomarkers")
        (nomarkers / CLAUDE_REL).write_bytes(b"# CLAUDE.md with no managed block\n")
        check("exit/missing-managed-block-markers-2", _quiet(run, nomarkers), 2)
        escape = _tree(tmp / "escape" / "repo", block="@../outside.md")
        (tmp / "escape" / "outside.md").write_bytes(b"outside\n")
        check("exit/import-escaping-repo-2", _quiet(run, escape), 2)

        # Red on revert: each fix put back in a mutant copy of the production code must turn its case red.
        unstripped = _mutant(tmp, "body = bare[m.end():]", "body = bare")
        check("revert/frontmatter-removal-red",
              (_measured(measure, front)["rules"], _measured(unstripped.measure, front)["rules"]), (5, 27))
        lenient = _mutant(tmp, 'if m["pack"] > ratchet:', 'if m["pack"] > ratchet + 1:')
        check("revert/ratchet-comparison-red", (_quiet(run, over), _quiet(lenient.run, over)), (1, 0))
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
          "line after it removed, an HTML comment removed, BMP and astral characters counted as 1 and 2 units, "
          "comments in a fence, a paragraph, and a code span kept, a BOM before frontmatter removed, paths ** "
          "counted and src/** excluded, a typed or empty paths value exit 2, rule file imports counted, banned "
          "and missing ones exit 1 and 2, cycles ending, mismatched backticks hiding no import, a managed block "
          "import followed, SESSION over PACK, the "
          "ratchet boundary, banned imports exit 1, unreadable, invalid UTF-8, malformed frontmatter, budget, "
          "markers, and an escaping import exit 2, and both red-on-revert flips turn red); execution set "
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
