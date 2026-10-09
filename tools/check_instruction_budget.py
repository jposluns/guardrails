#!/usr/bin/env python3
"""Instruction-budget gate: hold what Claude Code loads unconditionally to a committed ratchet.

Claude Code reads every `.claude/rules/**/*.md` file that carries no `paths:` scope, the project CLAUDE.md,
`.claude/CLAUDE.md` when it exists, and every file any of them imports, into every session before the first
prompt, each behind a header line naming the file. That text is paid for
in every session, so this gate measures it and fails when the pack's share grows. The loading rules below
are taken from the loader of the Claude Code build pinned in the budget source.

ENUMERATED GRAMMAR. The gate reads only input in the grammar stated below and exits 2 (cannot evaluate,
naming the file and line) on anything outside it. Matching an unspecified loader input by input cannot be
shown complete, so the gate does not try: within the grammar it counts every character of a counted file
and every file an `@` in it could name, and removes only what the loader removes (the frontmatter block,
and an HTML comment in the one shape COMMENTS states); outside the grammar it refuses rather than guesses.
The grammar is the gate's, not the loader's: some input the loader reads without trouble exits 2 here.

What it measures, in UTF-16 code units (the length of the text encoded utf-16-le, divided by 2; a
character in the Basic Multilingual Plane is one unit, an astral character such as an emoji is two):

  CHARACTERS. Every measured file (a rule file, CLAUDE.md, every import target) is strict UTF-8 holding
      no control character other than tab, LF, and the CR of a CR-LF pair; no Unicode space, line, or
      paragraph separator other than the ASCII space (U+0085 and U+00A0 among them); no format character
      (a byte order mark, a zero width space or joiner, a direction mark); and no code point this Python's
      Unicode database leaves unassigned. Whitespace is then the ASCII space, tab, CR, and LF alone, which
      Python and JavaScript read alike. Any other character, a non-ASCII letter included, is body text and
      is counted; the frontmatter and every import token must be printable ASCII.
  RULE FILES. Every file under `.claude/rules/` whose name ends in `.md` (case sensitive) and that is
      unconditional. The walk follows a symlinked directory whose target resolves inside the repository
      and counts the files under it. A symlink, to a directory or a file, that resolves outside the
      repository, a symlink loop, and a directory reached a second time through a symlink exit 2. A
      leading frontmatter block is removed (FRONTMATTER_RE below, first match only; its trailing `\\s*`
      also takes the blank line after the closing fence), then HTML comments as COMMENTS states. A rule
      file must be a regular file (for a symlink, its resolved target): a FIFO, socket, or device named
      `*.md` exits 2. Every file the gate measures is opened non-blocking and read only when it is a
      regular file, so no read can block. Two directories or `.md` entries (in any case) of one walked
      directory whose names differ only in case (`a.md` and `A.md`, `s/` and `S/`) exit 2: a
      case-insensitive file system holds only one of them, and which one is ambiguous; two other entries
      that differ only in case (`notes.txt` and `Notes.txt`) load nothing either way and pass. A rule
      symlink, a symlinked rule directory, and the rules base are resolved hop by hop: every link on the
      way is read with os.readlink (at most MAX_HOPS, so a loop exits 2), and every component and every
      link's own spelling is looked up in its directory before it is followed. Two entries there that can
      name it exit 2 (`Link.txt` beside `link.txt` on the way, though the resolved path no longer shows
      them; `Docs/` beside `docs/` too, whichever the next component names). A hop that leaves the
      repository exits 2.
  FIXED NAMES. The loader opens CLAUDE.md, `.claude/CLAUDE.md`, and `.claude/rules/` by those exact
      names, and on a case-insensitive file system a case variant of any of them (`claude.md`,
      `.Claude/`, `.claude/claude.md`, `.claude/Rules/`) is what it opens or walks. A case variant of
      CLAUDE.md or `.claude` at the repository root, or of CLAUDE.md or `rules` in `.claude/`, exits 2,
      whether or not the canonical name is there too. CLAUDE.md and `.claude/CLAUDE.md` are resolved hop
      by hop as a rule symlink is, and must resolve inside the repository to a regular file (os.lstat of
      the resolved path) before the gate opens them, so a FIFO or device, inside the repository or out,
      is never opened.
  FRONTMATTER. The block FRONTMATTER_RE matches, with the closing `---` at the start of a line and only
      spaces after it, and every character of the block and its fences printable ASCII. Blank lines and
      `#` lines at column 0 are skipped. Every other line is `key: value` with a key from FRONTMATTER_KEYS
      (the keys tools/gen_rules.py writes, plus `paths` and `description`) at column 0 and a one-line
      string or a flat one-line flow sequence of such strings as its value, or `key:` followed by `- item`
      lines at one indent, each holding one such string (`key:` with no item reads as null). A string is
      double quoted with no backslash, single quoted with no inner quote, or plain: it starts with no YAML
      indicator character and holds no `: ` or ` #` (in a flow sequence no comma, bracket, brace, or `?`
      either, as PyYAML ends a flow plain scalar at `?`; a quoted string there may hold a comma, and a
      flow sequence splits only on the commas outside quotes). A nested list, a flow mapping, an escape,
      a multi-line value, an unknown or duplicate key, or any other line exits 2.
  SCOPE. A file whose frontmatter has a `paths:` key is conditional and its own body is not counted (its
      imports are, as IMPORTS states), unless its globs load everywhere, and only when every entry is a
      string glob: a typed `paths:` value or entry, one YAML would read as null, a boolean, a number, a
      mapping, an alias, a tag, or a block scalar, exits 2 (the loader drops a scope it cannot read as
      strings and loads the file everywhere). That is an allowlist, not a list of typed forms: a quoted
      entry is a string, and a plain entry is accepted only when every character is a letter, a digit, or
      one of `_ . / * ? [ ] { } ! , @ + -` and it holds a `/`, `*`, `?`, `[` or `{`, or a letter, a `.`
      and a letter in a row (`README.md`) (PLAIN_GLOB_RE). No null, boolean, integer, float, timestamp,
      merge or value form of the YAML 1.1 types or the YAML 1.2 core schema holds either, so no resolver
      of either version can type it; every other plain entry exits 2: correctly for a value YAML does
      type (`2026-10-03`, `0b101`, `1:20`), and as a known over-refusal, which quoting avoids, for one it
      does not (`src`). The value (a string, or each entry of a list) is
      normalized in the loader's order: split on the commas outside braces, each piece trimmed, one level of
      brace alternation `{a,b}` expanded, one trailing `/**` stripped, and empty globs dropped. The file
      is unconditional, and counted, when no glob is left or every glob is `**` (so `**`, `**/**`, `/**`,
      `**,`, ` ** `, `{**,**}`, and `["**", "/**"]`). A glob the normalizer cannot read (nested braces, an
      unbalanced brace, a backslash escape) exits 2.
  COMMENTS. An HTML comment is removed only in the one shape the loader certainly removes: a line that is
      exactly one `<!-- ... -->` comment at column 0, with a blank line or the start of the file before it
      and a blank line or the end of the file after it, outside a fenced code block. The line and its
      newline go; the blank lines around it stay counted. Every other comment is counted. The line scan
      that finds fenced code blocks follows only fences opened at column 0; from the first line it cannot
      place (a fence opener that is not at column 0, any line that opens with `<` at any indent or inside
      a list or quote, or a fence-like line inside a fence that is not a plain close) to the end of the
      file it removes no comment, and a file holding a carriage return has no comment removed. A text
      holding `<!--` that does not end in a newline counts one unit more: the loader then rebuilds it from
      its Markdown lexer's tokens, which can add a final newline (a blockquote closing the file).
      The loader removes a comment before it looks for imports, so a comment can join an import path
      across it (`@docs/big<!---->.md` loads docs/big.md). The loader reads an `@` as an import at the start
      of a Markdown text token or after whitespace, and a text token also begins right after an inline token
      (`*see*@x`, `<b>@x</b>`). A measured file holding an `@` that begins an import token (at
      the start of a line, after whitespace, or right after a comment closer `-->`) on a line that also
      holds `<!--` or `-->`, or followed by `<!--` with only whitespace between them, exits 2 naming the
      line; the gate refuses the construct rather than model the join. An `@` inside a word, such as the
      one in an email address (`<!-- owner: ops@example.com -->`), begins no import and is exempt from this
      check (but not from the 8.3 short name refusal in IMPORTS, a disclosed over-refusal).
  IMPORTS. Every `@` in a rule file, in the managed block, in an imported file, and, for SESSION, in the
      whole CLAUDE.md is an import candidate. That includes a conditional rule file: the loader reads every
      rule file with its whole import tree in every session and then drops only the entries that carry their
      own scope, so a conditional file's imports load in every session though its own body is not counted;
      they are followed and counted into both totals, recursively, as for an unconditional file. Candidates
      are found with no exclusion for code spans, comments, or other Markdown structure (a comment next to
      an `@` exits 2, as COMMENTS states). The candidate is the run of non-whitespace characters after the
      `@` (a backslash-escaped space stays in the run); a run holding a character outside printable ASCII
      exits 2. The paths it may name are tested longest first: the whole run, then each prefix that ends
      just before a Markdown delimiter character (`*`, `_`, a backtick, `)`, `]`, `}`, `>`, `,`, `.`, `;`,
      `:`, `!`, `?`, quotes) or an inline opener (`<`, `[`, `(`, `{`, a backslash), each with its `#`
      fragment cut, read with the escaped space and also with Markdown backslash escapes undone. This takes
      trailing delimiters off one at a time as a special case, and also finds the path in `<b>@big.txt</b>`
      and `[@big.txt](x)`. A tested path is resolved from the real directory of the importing file, as the
      loader resolves it (for a symlinked CLAUDE.md, the directory of its target). A tested path with a
      `..` component, or passing through a symlink at any component, exits 2, so the lexical path and the real
      path are one file. Each component of a tested path is looked up case-insensitively in its directory's
      listing, whether or not the exact name is there. When exactly one entry matches at every component
      and the last is a regular file, that file is the import (under exact case, or the case variant the
      loader on a case-insensitive file system reads). When two or more entries of one directory match a
      component (two files or directories that differ only in case, the exact-case one included), the path
      exits 2: which one a case-insensitive file system holds is ambiguous. Two directories that differ
      only in case exit 2 even when the next component names an entry of only one of them: such a file
      system merges them, and the gate does not model what a merged directory's files then load. In every
      candidate, whatever precedes its `@` (an `@` right after an inline token, as in `*see*@PAYLOA~1.MD`,
      begins an import too), a component of the Windows 8.3 short name shape (one to six characters other
      than `.`, a `~` and a number from 1, and an optional extension of one to three characters, as in
      `PAYLOA~1.MD` or `A~VERY~1.MD`) exits 2: on NTFS with short names enabled it can open a long-named
      file the gate cannot identify. The one exemption is a run the loader's own path filter drops whatever
      precedes the `@` (_loader_rejects): one that starts with none of `./`, `~/`, `/` and not with a
      letter, a digit, `.`, `_` or `-`. That filter was read from a later loader build (2.1.288) than the
      pinned one, not from the pinned build. So prose such as `git diff @~1`, `npm i lodash@~4.17.21`,
      `git reset --hard HEAD@{1}~1` or `git diff @{u}~1` passes. Each target is
      counted once, recursively through imported files to any depth (a cycle ends), held to the grammar,
      its frontmatter and comments removed as for a counted rule file. An imported file's frontmatter is
      held to the FRONTMATTER grammar and its `paths:` value to the SCOPE grammar (a typed value, nested
      or unbalanced braces, or an escape exits 2), but a scope does not make an import conditional: the
      target is counted whatever its scope. Only a genuinely absent path (the
      file system reports that it does not exist) adds 0 and does not fail: a mention such as @alice, or
      an email address such as name@example.com. A path that holds only dot components names a directory
      and adds 0. A candidate the gate cannot settle exits 2 with a message that calls it ambiguous or
      cannot-evaluate: a home relative (`~/`) or absolute path; a path naming a directory or another
      entry that is not a regular file; a path the gate cannot list or read (a PermissionError or any
      other OSError, never read as missing); and a target that cannot be decoded as UTF-8.
  MANAGED BLOCK. The pack-managed RULES-INDEX block of CLAUDE.md (the markers tools/gen_claude.py
      writes), comments removed as COMMENTS states with the scan run over the whole CLAUDE.md (so a fence
      opened before the block keeps the block's comments counted), plus its imports.
  .claude/CLAUDE.md. The loader reads `.claude/CLAUDE.md` beside CLAUDE.md in every session. When it exists
      it is measured wherever it is, under the same grammar as CLAUDE.md (CHARACTERS, COMMENTS, IMPORTS),
      whole, with comments removed as COMMENTS states, and counted with its imports into both totals; its
      imports resolve from its real directory. It sits in the pack's own `.claude/` directory and the gate
      cannot tell who wrote it, so it counts into PACK as well as SESSION, and its banned imports are
      findings. A path there the gate cannot read, a directory or a broken symlink included, exits 2.
  HEADERS. The loader writes `Contents of <path> (project instructions, checked into the codebase):`
      and a blank line before each file it loads, and joins the files with a blank line. Each loaded file
      (an unconditional rule file, CLAUDE.md, `.claude/CLAUDE.md`, and every import target; not a
      conditional rule file, whose own body is not loaded) counts a modelled header: that fixed text, the
      type suffix the pinned loader writes for a project file, the colon, the blank-line separators, and the
      file's path RELATIVE to the repository root. That path is the one the loader writes: the resolved
      path (every symlink resolved, a symlinked rule file or one under a symlinked rule directory
      included), relative to the resolved repository root, not the name the link gives it. For CLAUDE.md
      and `.claude/CLAUDE.md`, which the loader opens by a fixed name, the gate has not established
      whether it writes that name or the resolved path, so it counts the longer of the two. The loader
      writes the absolute path; the machine-specific root prefix before the relative path is not counted,
      so the figure is the same on every machine. Once per session, before the first file, the loader also
      writes a fixed preamble (`Codebase and user instructions are shown below. ...`); it is a constant,
      not counted, so any growth in what loads still shows in both totals. The headers are counted into
      both totals and printed as their own part.

Two totals are reported separately:

  PACK     the rule files and their imports, the managed block and its imports, and `.claude/CLAUDE.md`
           and its imports, plus the HEADERS of every file loaded for them and of CLAUDE.md. This is the
           part the pack controls, and the only total the gate enforces.
  SESSION  the rule files plus the whole CLAUDE.md plus `.claude/CLAUDE.md` plus every file any of them
           imports, plus the HEADERS of all of them. Reported, not enforced: the bytes outside the managed
           block belong to the adopter.

A rule file, `.claude/CLAUDE.md`, or the managed block importing RULES-INDEX.md or AGENTS.md is a finding (exit 1) whatever the
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
  - An import inside a code span, a fenced code block, or an HTML comment spread over lines (on a line
    holding neither `<!--` nor `-->`) is counted although the loader skips it, and
    a banned name there exits 1.
  - Every target is followed without the loader's own path filter (which only exempts a run from the
    8.3 short name refusal) and to any depth, and two prefixes of one candidate that name two files both
    count.
  - A rule file reached under two names counts twice, and the unit added for a text holding `<!--` with
    no final newline counts whether or not the loader's rebuild adds it.
  - Each loaded file's header counts a joining blank line, though the loader writes one fewer than the
    files it joins, and the loader trims each file's content, which the gate does not.

DISCLOSED RESIDUAL. This gate measures the repository's own files; it does not run Claude Code. Its
loader model comes from reading the pinned build's source, and the grammar bounds the input it vouches
for; it is not a proof that the loader agrees on every input inside the grammar. It does not measure
user-level files (~/.claude/CLAUDE.md and ~/.claude/rules/), which load in every session too; an
AGENTS.md or other file loaded through the instructionFiles setting rather than an `@` import; CLAUDE.md
files, .claude/CLAUDE.md files or .claude/rules/ directories in other directories (above the repository
root or below it, which a session started there also loads), or CLAUDE.local.md; skills, hooks,
tool definitions, or the system prompt; or loading by a Claude Code version other than the one pinned in
the budget source. Of each loaded file's header (`Contents of <absolute path> (project instructions,
checked into the codebase):`, HEADERS) it counts the fixed text and the file's resolved path relative to
the resolved repository root (the longer of that and its fixed name for CLAUDE.md and
`.claude/CLAUDE.md`), but not the machine-specific absolute root prefix the loader writes before that
path. So each header is undercounted by that prefix's length, and by nothing else only as far as the
loader writes the resolved path, as the pinned build's rule walker does (the gate's loader model, not a
proof). That header model was verified against the pinned build only (the claude-code-version and
binary-sha256 of the budget source); it is not asserted for any later build, whose header text or path
spelling may differ. The loader's once-per-session preamble (HEADERS) is not counted either. Case
ambiguity is judged on two keys, Unicode NFD then str.casefold, and str.upper, and two names collide when
either matches (so the dotless i, and U+037E against `;`, collide): a file system whose folding matches
neither (an NTFS upcase table that differs from this Python's Unicode data, for example) can still merge
two names the gate reads as distinct, which is not modelled. A short name set by hand outside the 8.3
shape is not recognised. Two directories that differ only in case on a path the gate resolves exit 2 even
where the next component names an entry of only one of them, so a layout a case-insensitive file system
would load unambiguously can be refused (a known over-refusal). An `@` inside a word before a run the
loader's path filter keeps and a short-name-shaped component, such as `name@HOST~1`, exits 2 though the
loader does not read it as an import (another). The loader's own
120,000-character check
sums file contents only, so both totals, which include the headers, read a little high against that
floor. The count is UTF-16 code units, not tokens, so it tracks size, not model cost.

Usage:
  check_instruction_budget.py                          measure and enforce; print both totals
  check_instruction_budget.py --self-test              fixture trees only; assert every count and exit
  check_instruction_budget.py --execution-report ABS   the self-test, also writing the executed check ids
                                                       (the launch form tools/check_selftest_execution.py
                                                       uses for the instruction-budget-selftest suite)

Exit 0 clean; 1 on a finding (PACK over the ratchet, a banned import); 2 on a cannot-evaluate input
(fail-closed), so an unreadable, malformed, or out-of-grammar input can never read as clean.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_instruction_budget.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import contextlib
import errno
import io
import json
import os
import re
import shutil
import stat
import tempfile
import unicodedata
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    sys.stderr.write(
        "error: check_instruction_budget.py cannot import tomllib, part of the Python standard library; "
        "this installation is incomplete. Nothing was run (cannot evaluate).\n")
    raise SystemExit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_claude  # noqa: E402  the RULES-INDEX markers it writes, never a second copy of them
import gen_rules  # noqa: E402  its validated frontmatter value parsers, never a second one
import _selftest_exit_report  # noqa: E402

RULES_REL = ".claude/rules"
CLAUDE_REL = "CLAUDE.md"
# The loader reads this beside CLAUDE.md in every session (HEADERS and `.claude/CLAUDE.md` in the docstring).
DOT_CLAUDE_REL = ".claude/CLAUDE.md"
# The fixed text of the header the loader writes before each loaded file, `Contents of <path>` plus the type
# suffix it writes for a project file, a colon, and a blank line, and the blank line that joins the files.
HEADER_FIXED = "Contents of " + " (project instructions, checked into the codebase)" + ":\n\n" + "\n\n"
BUDGET_REL = ".aiqt/core/instruction-budget.toml"
BUDGET_KEYS = {"ratchet", "ceiling", "claude-code-version", "binary-sha256"}
# Importing either from the managed block loads the rule index or a second full copy of the corpus on top
# of the rule tree, so either is a finding whatever the measured total. Matched on the final path
# component in any case, as a case-insensitive file system reads it.
BANNED_IMPORTS = ("RULES-INDEX.md", "AGENTS.md")
BANNED_FOLDED = {name.casefold() for name in BANNED_IMPORTS}

# The leading frontmatter block, the loader's own expression (first match only). Within the enumerated
# character set (whitespace is the ASCII space, tab, CR and LF alone) Python and JavaScript read it alike.
FRONTMATTER_RE = re.compile(r"^---\s*\n([\s\S]*?)---\s*\n?")
# A file that opens a frontmatter fence FRONTMATTER_RE cannot close is malformed, never body text.
FRONTMATTER_OPEN_RE = re.compile(r"^---\s*\n")
JS_WHITESPACE = (" \t\n\v\f\r\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008"
                 "\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff")
# The one comment shape the gate removes: a whole line that is exactly one `<!-- ... -->` comment.
COMMENT_LINE_RE = re.compile(r"<!--(?:(?!-->)[^\n])*-->")
# An `@` with an HTML comment after it and only whitespace between: the loader may join a path across it.
COMMENT_AFTER_AT_RE = re.compile(r"@[ \t\r\n]*<!--")
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
# The only plain `paths:` entry the gate accepts (_plain_string): characters from this class, and a `/`,
# `*`, `?`, `[` or `{`, or a letter, `.` and letter in a row. None of the null, boolean, integer, float,
# timestamp, merge or value forms of YAML 1.1 or the YAML 1.2 core schema holds any of them (their only
# `.` follows a digit, a sign, `_`, another `.` or nothing), so no resolver of either version reads it as
# anything but a string. An allowlist: a typed form left off a denylist would load the rule everywhere.
PLAIN_GLOB_RE = re.compile(r"(?=.*(?:[/*?\[{]|[A-Za-z]\.[A-Za-z]))[A-Za-z0-9_./*?\[\]{}!,@+-]+")
# The strict line reader: a plain key (letters, digits, `_`, `-`) at column 0, a colon, and a space before
# any value; a `- item` line indented with spaces only.
KEY_RE = re.compile(r"([A-Za-z0-9_-]+):(?: +(.*))?")
ITEM_RE = re.compile(r" *- +(.*)")
QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
# A Windows 8.3 short name component (`PAYLOA~1.MD`, `A~VERY~1.MD`): one to six characters other than `.`,
# a `~` and a number, and an optional extension of one to three characters; on NTFS it can open a long-named
# file. Prose such as `git diff @~1` or `npm i lodash@~4.17.21` holds no such component.
SHORT_NAME_RE = re.compile(r"[^.]{1,6}~[1-9][0-9]{0,5}(?:\.[^.]{1,3})?")
# The start of an import run the loader's own path filter keeps (_loader_rejects), as read from a later
# loader build (2.1.288) than the pin: `./`, `~/`, `/`, or a letter, digit, `.`, `_` or `-`.
LOADER_PATH_RE = re.compile(r"\./|~/|/|[a-zA-Z0-9._-]")
# The most symlinks _resolve_checked follows for one path (the Linux limit); one more is a loop.
MAX_HOPS = 40
SHA256_RE = re.compile(r"[0-9a-f]{64}")
# The enumerated character set (CHARACTERS in the module docstring): a character outside tab, LF and
# printable ASCII is looked at; it is refused when it is a control, format, separator or unassigned code
# point, or any character Python reads as whitespace, except the CR of a CR-LF pair.
OFF_ASCII_RE = re.compile("[^\t\n -~]")
OFF_GRAMMAR_CATEGORIES = frozenset(("Cc", "Cf", "Cn", "Zl", "Zp", "Zs"))
# The frontmatter keys the grammar admits: those tools/gen_rules.py writes, plus `paths` and `description`.
FRONTMATTER_KEYS = frozenset(gen_rules.BASE_KEYS | gen_rules.SEQ_KEYS
                             | set(("apex", "tier", "facet", "paths", "description")))
# A plain scalar may not start with a YAML indicator character; a plain flow element may not hold these
# (PyYAML ends a flow plain scalar at `?` and rejects the block, so a loader may too).
PLAIN_FIRST = "-?:,[]{}#&*!|>'\"%@`"
FLOW_FORBIDDEN = "[]{},?"


class GateError(Exception):
    """A fail-closed condition (an unreadable input, malformed frontmatter, markers, an unresolvable
    import, or a malformed budget source): exit 2."""


def utf16_units(text):
    """The length of `text` in UTF-16 code units: one per BMP character, two per astral character."""
    return len(text.encode("utf-16-le")) // 2


def header_units(rel):
    """The units of the modelled header before a loaded file: HEADER_FIXED plus `rel`, its path relative
    to the repository root. The machine-specific absolute root prefix the loader writes is not counted."""
    return utf16_units(HEADER_FIXED) + utf16_units(rel)


def loaded_rel(path, real_root):
    """The path the loader writes in the header of the file `path`: its resolved path (every symlink
    resolved), relative to the resolved repository root `real_root`, never the name a link gives it."""
    return Path(os.path.relpath(os.path.realpath(path), real_root)).as_posix()


def fixed_name_header(root, real_root, rel):
    """The header units of the file the loader opens by the fixed name `rel` (CLAUDE.md,
    `.claude/CLAUDE.md`): the longer of `rel` and its loaded_rel, since which one the loader writes for these
    is not established."""
    return max(header_units(rel), header_units(loaded_rel(root / rel, real_root)))


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


def counted_units(body):
    """The units of `body` with the comments comment_spans reports removed, plus one when `body` holds
    `<!--` and does not end in a newline: the loader then rebuilds the text from its Markdown lexer's
    tokens, which can add a final newline (a blockquote closing the file)."""
    rebuilt = "<!--" in body and not body.endswith("\n")
    return utf16_units(strip_comments(body)) + (1 if rebuilt else 0)


def read_text(path, where):
    """The file's text decoded strictly as UTF-8 from its raw bytes, with no newline translation, so a
    CRLF file is counted as written. The file is opened non-blocking and read only when the opened entry is
    a regular file, so a FIFO or device can never block the gate. GateError on any open, read, or decode
    failure and on an entry that is not a regular file (fail-closed)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise GateError("cannot read {}: it is not a regular file; whether the loader reads it is "
                                "ambiguous; cannot evaluate".format(where))
            return handle.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError("cannot read {} ({})".format(where, exc))


def _check_characters(text, where):
    """GateError naming the file and line of the first character of `text` outside the enumerated
    character set: a control character other than tab, LF, and the CR of a CR-LF pair; a space, line, or
    paragraph separator other than the ASCII space; a format character (a byte order mark among them); a
    code point this Python's Unicode database leaves unassigned; or any other character Python reads as
    whitespace. Whitespace is then the ASCII space, tab, CR and LF alone, read alike by Python and
    JavaScript. Any other non-ASCII character is body text and is counted."""
    for match in OFF_ASCII_RE.finditer(text):
        ch, idx = match.group(0), match.start()
        if ch == "\r" and text[idx + 1:idx + 2] == "\n":
            continue
        if ch.isascii() or ch.isspace() or unicodedata.category(ch) in OFF_GRAMMAR_CATEGORIES:
            raise GateError("{}:{}: character U+{:04X} is outside the enumerated grammar (no control, format, "
                            "or Unicode whitespace character but tab, LF, and CR-LF); cannot evaluate".format(
                                where, text.count("\n", 0, idx) + 1, ord(ch)))


def _import_start(text, pos):
    """True when the `@` at `pos` begins an import token: at the start of `text` or a line, after
    whitespace, or right after a comment closer `-->` (with the comment removed, what came before it may be
    whitespace). An `@` inside a word, such as the one in an email address, begins none. The loader also
    starts a text token, where an `@` begins an import, right after an inline token (`*see*@x`), which this
    does not model; the 8.3 short name refusal does not use it (_loader_rejects)."""
    return pos == 0 or text[pos - 1] in " \t\r\n" or text[pos - 3:pos] == "-->"


def _loader_rejects(run):
    """True when the loader's own path filter drops the import run `run` whatever precedes its `@`: it
    starts with none of `./`, `~/`, `/` and not with a letter, digit, `.`, `_` or `-` (LOADER_PATH_RE), as
    `{1}~1` in `HEAD@{1}~1`, `{u}~1` in `git diff @{u}~1` and `~1` in `git diff @~1` do. The filter was
    read from a later loader build (2.1.288) than the pin."""
    return not LOADER_PATH_RE.match(run)


def _check_comment_joins(text, where):
    """GateError naming the file and line of the first `@` an HTML comment could join to an import path:
    an `@` that begins an import token (_import_start) on a line that also holds `<!--` or `-->`, or
    followed by `<!--` with only whitespace between them. The loader removes comments before it looks for
    imports, so `@docs/big<!---->.md` loads docs/big.md; the gate refuses the construct rather than model
    the join. An `@` inside a word (`<!-- owner: ops@example.com -->`) is not refused."""
    pos = text.find("@")
    while pos >= 0:
        if _import_start(text, pos):
            number = text.count("\n", 0, pos) + 1
            end = text.find("\n", pos)
            line = text[text.rfind("\n", 0, pos) + 1:len(text) if end < 0 else end]
            if "<!--" in line or "-->" in line:
                raise GateError("{}:{}: an HTML comment opener or closer shares a line with an `@` that "
                                "begins an import; the loader removes the comment and may join an import "
                                "path across it; outside the enumerated grammar, cannot evaluate".format(
                                    where, number))
            if COMMENT_AFTER_AT_RE.match(text, pos):
                raise GateError("{}:{}: an HTML comment follows an `@` with only whitespace between them; the "
                                "loader removes the comment and may join an import path across it; outside "
                                "the enumerated grammar, cannot evaluate".format(where, number))
        pos = text.find("@", pos + 1)


def _check_ascii(text, where, first_line, what):
    """GateError naming the file and line of the first character of `text` (the frontmatter, or an import
    token) that is not printable ASCII, LF, or the CR of a CR-LF pair."""
    for idx, ch in enumerate(text):
        if not (" " <= ch <= "~" or ch == "\n" or (ch == "\r" and text[idx + 1:idx + 2] == "\n")):
            raise GateError("{}:{}: {} holds U+{:04X}, outside printable ASCII; outside the enumerated "
                            "grammar, cannot evaluate".format(
                                where, first_line + text.count("\n", 0, idx), what, ord(ch)))


def _body_line(text, body):
    """The line of `text` on which its tail `body` starts."""
    return text.count("\n", 0, len(text) - len(body)) + 1


def split_frontmatter(text, where):
    """(frontmatter text or None, body, the line the frontmatter text starts on). The body is what follows
    the FIRST FRONTMATTER_RE match. The block and its fences must be printable ASCII (_check_ascii), and
    the closing `---` must start a line and have only spaces after it. A file that opens a frontmatter
    fence the regex cannot close is malformed. Each is GateError."""
    m = FRONTMATTER_RE.match(text)
    if m is None:
        if FRONTMATTER_OPEN_RE.match(text):
            raise GateError("{}:1: unterminated frontmatter block; cannot evaluate".format(where))
        return None, text, 1
    _check_ascii(text[:m.end()], where, 1, "the frontmatter")
    front = m.group(1)
    if (front and not front.endswith("\n")) or (m.end() < len(text) and text[m.end() - 1] != "\n"):
        raise GateError("{}:{}: the closing frontmatter fence is not a `---` line of its own; outside the "
                        "enumerated grammar, cannot evaluate".format(where, _body_line(text, text[m.start(1) + len(front):])))
    return front, text[m.end():], text.count("\n", 0, m.start(1)) + 1


def _escape_free(tok):
    """False when `tok` is double quoted and holds a backslash: an escape the loader's YAML may reject."""
    return not (tok[:1] == '"' and "\\" in tok)


def _scalar_ok(tok, flow):
    """True when `tok` is a one-line string in the enumerated grammar: a double-quoted string with no
    backslash (_escape_free) or inner double quote, a single-quoted string with no inner single quote, or
    a plain scalar that starts with no PLAIN_FIRST character and holds no `: `, ` #`, or final `:`; in a
    flow sequence (`flow`) a plain element holds no FLOW_FORBIDDEN character either."""
    if not _escape_free(tok):
        return False
    if QUOTED_RE.fullmatch(tok):
        return True
    if not tok or tok[0] in PLAIN_FIRST or ": " in tok or " #" in tok or tok.endswith(":"):
        return False
    return not (flow and any(ch in FLOW_FORBIDDEN for ch in tok))


def _known_key(key):
    """True when `key` is one of FRONTMATTER_KEYS."""
    return key in FRONTMATTER_KEYS


def parse_frontmatter(block, where, first_line):
    """{key: value} from the enumerated grammar, the only frontmatter the gate reads. Blank lines and `#`
    lines at column 0 are skipped; every other line is `key: value` (KEY_RE, a _known_key at column 0)
    whose value is a one-line string (_scalar_ok) or a flat one-line flow sequence of such strings, or
    `key:` (null until an item follows) and then `- item` lines (ITEM_RE) at one indent, each one such
    string. A `paths:` string must also be a plain string glob (_plain_string). Any other line, key, or
    value (a nested list, a flow mapping, an escape, a multi-line value, a duplicate key) is GateError
    naming the line: the loader's YAML may read it otherwise or reject the block, so the gate refuses."""
    fm = {}
    open_key, indent = None, None
    for offset, raw in enumerate(block.split("\n")):
        line = (raw[:-1] if raw.endswith("\r") else raw).rstrip(" ")
        here = "{}:{}".format(where, first_line + offset)
        if not line or line.startswith("#"):
            continue
        item = ITEM_RE.fullmatch(line)
        if item and open_key is not None:
            lead = len(line) - len(line.lstrip(" "))
            indent = lead if indent is None else indent
            tok = item.group(1)
            if lead != indent or not _scalar_ok(tok, False):
                raise GateError("{}: frontmatter list item {!r} is outside the enumerated grammar (one indent, "
                                "one one-line string); cannot evaluate".format(here, line))
            if open_key == "paths":
                _plain_string(tok, here)
            fm[open_key] = (fm[open_key] or []) + [gen_rules._unquote(tok)]
            continue
        kv = KEY_RE.fullmatch(line)
        if kv is None or not _known_key(kv.group(1)):
            raise GateError("{}: frontmatter line {!r} is outside the enumerated grammar (a known key at "
                            "column 0, or a list item under one); cannot evaluate".format(here, line))
        key, value = kv.group(1), (kv.group(2) or "").strip(" ")
        if key in fm:
            raise GateError("{}: duplicate frontmatter key {!r}; cannot evaluate".format(here, key))
        open_key, indent = (key, None) if value == "" else (None, None)
        if value == "":
            fm[key] = None
            continue
        flow = value.startswith("[") and value.endswith("]")
        inner = value[1:-1].strip(" ") if flow else None
        elems = ([e.strip(" ") for e in _flow_split(inner)] if inner else []) if flow else [value]
        if not all(_scalar_ok(e, flow) for e in elems):
            raise GateError("{}: frontmatter value {!r} is outside the enumerated grammar (a one-line string or "
                            "a flat flow sequence of them); cannot evaluate".format(here, value))
        if key == "paths":
            for e in elems:
                _plain_string(e, here)
        fm[key] = [gen_rules._unquote(e) for e in elems] if flow else gen_rules._unquote(value)
    return fm


def _flow_split(inner):
    """The elements of a flow sequence's `inner` text, split on the commas outside quotes: a quote opens
    only at the start of an element and closes at the next same quote, so `"**,**"` is one element. A
    quote left open, or text after a closing quote, stays in its element for _scalar_ok to refuse."""
    parts, current, quote = [], [], None
    for ch in inner:
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in "\"'" and not "".join(current).strip(" "):
            quote = ch
        elif ch == ",":
            parts.append("".join(current))
            current = []
            continue
        current.append(ch)
    parts.append("".join(current))
    return parts


def _plain_string(tok, where):
    """GateError unless the `paths:` token `tok` is a YAML string under every YAML 1.1 and 1.2 resolver:
    quoted, or a plain scalar that starts with no PLAIN_FIRST character and matches PLAIN_GLOB_RE whole.
    Anything else (a null, a boolean, a number, a timestamp, a mapping, an alias, a tag, a block scalar,
    or a plain string the allowlist does not cover) is cannot-evaluate, never a conditional file: the
    loader drops a scope it cannot read as strings and loads the file everywhere."""
    if tok[:1] in ("\"", "'"):
        return
    if not tok or tok[0] in PLAIN_FIRST or not PLAIN_GLOB_RE.fullmatch(tok):
        raise GateError("{}: `paths:` value {!r} is not a string glob a YAML 1.1 or 1.2 resolver cannot type "
                        "(PLAIN_GLOB_RE; quote it); cannot evaluate".format(where, tok))


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


def _fold_keys(name):
    """The two keys on which a case-insensitive file system may compare `name`: its Unicode NFD form folded
    with str.casefold (the normalization APFS also folds), and str.upper (an upcase-table fold, which also
    merges the dotless i with i)."""
    return unicodedata.normalize("NFD", name).casefold(), name.upper()


def _same_name(a, b):
    """True when the names `a` and `b` can name one entry on a case-insensitive file system: either of
    their _fold_keys matches."""
    keys_a, keys_b = _fold_keys(a), _fold_keys(b)
    return keys_a[0] == keys_b[0] or keys_a[1] == keys_b[1]


def _case_collision(names, where):
    """GateError when two of `names` (the entries of one directory, by name or by path from the
    repository root) have final components that can name one entry (_same_name): a case-insensitive file
    system holds only one of them, and which one is ambiguous."""
    seen = ({}, {})
    for name in sorted(names):
        for table, key in zip(seen, _fold_keys(os.path.basename(name))):
            other = table.setdefault(key, name)
            if other != name:
                raise GateError("{}: {} and {} differ only in case; which one a case-insensitive file system "
                                "holds is ambiguous; cannot evaluate".format(where, other, name))


def _resolve_checked(root, rel, where):
    """The resolved path of `root / rel` (`rel` a POSIX path relative to `root`), found by walking it from
    the resolved repository root one component and one symlink hop at a time, each link read with
    os.readlink, so every spelling on the way is looked at, not only the final resolved path. Before a
    component is followed, every entry of its directory that can name it (_same_name) is collected: two or
    more are GateError (_case_collision), directories included, whichever the next component names.
    GateError when a hop leaves the repository, when more than MAX_HOPS links
    are followed (a loop), when a component is missing or a directory cannot be listed, and when the result
    is not os.path.realpath's (a link naming its target only in another case resolves on one file system
    and not on another)."""
    real_root = os.path.realpath(root)
    prefixes = [Path(real_root).parts, Path(os.path.abspath(root)).parts]
    pending = [part for part in rel.split("/") if part not in ("", ".")]
    hops, result = 0, real_root
    while pending:
        part = pending.pop(0)
        if part == "..":
            if result == real_root:
                raise GateError("{} resolves outside the repository; cannot evaluate".format(where))
            result = os.path.dirname(result)
            continue
        try:
            names = os.listdir(result)
        except OSError as exc:
            raise GateError("{}: cannot list {} ({}); cannot evaluate".format(where, result, exc))
        found = []
        for name in sorted(names):
            if _same_name(name, part):
                path = os.path.join(result, name)
                try:
                    found.append((path, os.lstat(path).st_mode))
                except OSError as exc:
                    raise GateError("cannot read {} ({}); cannot evaluate".format(where, exc))
        if not found:
            raise GateError("cannot read {}: {} is missing; cannot evaluate".format(
                where, os.path.join(result, part)))
        if len(found) > 1:
            _case_collision([os.path.relpath(path, real_root) for path, _ in found], where)
        found.sort(key=lambda entry: os.path.basename(entry[0]) != part)
        path, mode = found[0]
        if not stat.S_ISLNK(mode):
            result = path
            continue
        hops += 1
        if hops > MAX_HOPS:
            raise GateError("{}: symlink loop; cannot evaluate".format(where))
        try:
            target = os.readlink(path)
        except OSError as exc:
            raise GateError("cannot read {} ({}); cannot evaluate".format(where, exc))
        parts = Path(target).parts
        if target.startswith("/"):
            prefix = next((p for p in prefixes if parts[:len(p)] == p), None)
            if prefix is None:
                raise GateError("{} resolves outside the repository; cannot evaluate".format(where))
            parts, result = parts[len(prefix):], real_root
        else:
            result = os.path.dirname(path)
        pending = [part for part in parts if part not in ("", ".")] + pending
    if result != os.path.realpath(os.path.join(root, rel)):
        raise GateError("{} resolves to {} when matched case-insensitively but to {} on this file system; "
                        "ambiguous, cannot evaluate".format(where, os.path.relpath(result, real_root),
                                                            os.path.realpath(os.path.join(root, rel))))
    return result


def _walked(entry):
    """True for an entry of a walked rule directory that the loader may read: a directory, or a name ending
    in `.md` in any case (a case variant of a rule file name merges with it). Only these are held to
    _case_collision; two names that differ only in case among other entries load nothing either way."""
    folded, upper = _fold_keys(entry.name)
    if folded.endswith(".md") or upper.endswith(".MD"):
        return True
    try:
        return entry.is_dir()
    except OSError:  # a symlink loop: kept, and refused by the walk itself
        return True


def _regular_rule(path, where):
    """GateError unless the rule file `path` (for a symlink, its resolved target) is a regular file: a
    FIFO, socket, or device named `*.md` is refused before it is read."""
    try:
        mode = os.stat(path).st_mode
    except OSError as exc:
        raise GateError("cannot read {} ({})".format(where, exc))
    if not stat.S_ISREG(mode):
        raise GateError("{}: a rule file that is not a regular file (a FIFO, socket, or device); whether the "
                        "loader reads it is ambiguous; cannot evaluate".format(where))


def rule_files(root):
    """Every `*.md` file under .claude/rules/, in a stable order, following a symlinked directory as the
    loader does. GateError when the tree is missing or cannot be walked, when a symlink resolves outside
    the repository or loops, when a directory is reached a second time through a symlink (whether the
    loader then loads its files once or twice is ambiguous), when two entries of a walked directory differ
    only in case (_case_collision, among the entries _walked keeps) or a symlink fails _resolve_checked at any
    hop, and when a rule file is not a regular file (_regular_rule)."""
    base = root / RULES_REL
    if not base.is_dir():
        raise GateError("{} is missing or not a directory".format(RULES_REL))
    real_root = os.path.realpath(root)
    real_base = os.path.realpath(base)
    if not _inside(real_base, real_root):
        raise GateError("{} resolves outside the repository; cannot evaluate".format(RULES_REL))
    _resolve_checked(root, RULES_REL, RULES_REL)
    found, visited = [], {real_base}

    def _walk(directory, chain):
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda entry: entry.name)
        except OSError as exc:
            raise GateError("cannot walk {} ({})".format(RULES_REL, exc))
        _case_collision([entry.name for entry in entries if _walked(entry)], directory.relative_to(root).as_posix())
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
                _resolve_checked(root, where, where)
            if entry.is_dir():
                subdirs.append((path, where))
            elif entry.name.endswith(".md"):
                _regular_rule(path, where)
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


def _imported_scope(fm, where):
    """GateError when an imported file's frontmatter `fm` carries a `paths:` scope outside the SCOPE grammar
    (path_globs, normalize_globs). A scope does not make an import conditional; the target is counted."""
    globs = path_globs(fm, where)
    if globs is not None:
        normalize_globs(globs, where)


def measure_rules(root):
    """(counted units, counted file count, conditional file count, import origins, header units) over the
    rule tree, the origins one (body, real directory, where, first body line) per rule file, for
    follow_imports, and the header units the modelled header of each counted file (header_units). A
    conditional file's body is not counted, but it is an origin: the loader reads every rule file with its
    whole import tree in every session and drops only the entries that carry their own scope, so the
    imports of a conditional file load in every session."""
    total = counted = conditional = headers = 0
    origins = []
    real_root = os.path.realpath(root)
    for path in rule_files(root):
        where = path.relative_to(root).as_posix()
        text = read_text(path, where)
        _check_characters(text, where)
        _check_comment_joins(text, where)
        front, body, line = split_frontmatter(text, where)
        origin = (body, os.path.dirname(os.path.realpath(path)), where, _body_line(text, body))
        if front is not None:
            globs = path_globs(parse_frontmatter(front, where, line), where)
            if globs is not None and not loads_everywhere(normalize_globs(globs, where)):
                conditional += 1
                origins.append(origin)  # its body is not counted; its imports load in every session
                continue
        total += counted_units(body)
        headers += header_units(loaded_rel(path, real_root))  # the resolved path, not the link's name
        counted += 1
        origins.append(origin)
    return total, counted, conditional, origins, headers


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
    """(run, offset of its `@`) for every `@` in `text`, with no exclusion for code spans, comments, or
    other Markdown structure: the run of non-whitespace characters after it (CANDIDATE_RE), or the empty
    string when none follows."""
    runs = []
    pos = text.find("@")
    while pos >= 0:
        m = CANDIDATE_RE.match(text, pos + 1)
        runs.append((m.group(0) if m else "", pos))
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


def _strict(call, path, where, token):
    """`call(path)` (os.lstat or os.listdir), or None when `path` is genuinely absent (FileNotFoundError).
    Any other OSError (a permission error, a component that is not a directory) is GateError: what the
    loader reads there is unknown, so the gate never reads it as missing."""
    try:
        return call(path)
    except FileNotFoundError:
        return None
    except OSError as exc:  # anything but a genuinely absent entry
        raise GateError("{}: import @{} reaches {}, which the gate cannot list or read ({}); cannot "
                        "evaluate".format(where, token, path, exc))


def _entry_mode(path, where, token):
    """The lstat mode of `path`, or None when it is genuinely absent. A symlink is GateError: whether the
    loader resolves a later `..` or its base before or after it is ambiguous."""
    st = _strict(os.lstat, path, where, token)
    if st is None:
        return None
    if stat.S_ISLNK(st.st_mode):
        raise GateError("{}: import @{} passes through the symlink {}; ambiguous, outside the enumerated "
                        "grammar, cannot evaluate".format(where, token, path))
    return st.st_mode


def _regular(target, mode, where, token):
    """`target` when `mode` is a regular file's; GateError for a directory or any other kind of entry."""
    if stat.S_ISDIR(mode):
        raise GateError("{}: import @{} names the directory {}; whether the loader reads anything there is "
                        "ambiguous; cannot evaluate".format(where, token, target))
    if not stat.S_ISREG(mode):
        raise GateError("{}: import @{} names {}, which is not a regular file; whether the loader loads it is "
                        "ambiguous; cannot evaluate".format(where, token, target))
    return target


def _case_walk(parts, base, where, token):
    """The one regular file under `base` matching the components `parts`, each looked up case-insensitively
    (_same_name) in its directory's listing whether or not the exact name is there (under exact case, or
    the variant the loader on a case-insensitive file system reads), or None when a component matches
    nothing. Each directory on the way is listed strictly (_strict) and each entry passed is checked
    (_entry_mode). Two or more entries matching one component, the exact-case one included and two
    directories included, are GateError: which one a case-insensitive file system holds, or what a
    directory it merges from them holds, is ambiguous."""
    entry, mode = base, None
    for part in parts:
        matches = []
        for name in sorted(_strict(os.listdir, entry, where, token) or []):
            if _same_name(name, part):
                path = os.path.join(entry, name)
                found = _entry_mode(path, where, token)
                if found is not None:
                    matches.append((path, found))
        if not matches:
            return None
        if len(matches) > 1:
            raise GateError("{}: import @{} has the component {!r}, which matches {} entries "
                            "case-insensitively; which one a case-insensitive file system holds is "
                            "ambiguous; cannot evaluate".format(where, token, part, len(matches)))
        entry, mode = matches[0]
    return _regular(entry, mode, where, token)


def resolve_candidate(path, base, where, token, kept=True):
    """The regular file `path` names relative to `base` (the real directory of the importing file, as the
    loader resolves it), or None when it is genuinely absent or holds only dot components, found by
    _case_walk. GateError for a `..` component, a Windows 8.3 short name component (SHORT_NAME_RE) when
    `kept` (the loader's path filter keeps the run whatever precedes its `@`, _loader_rejects), a
    symlink at any component, an entry that cannot be listed or read, a directory or other entry that is
    not a regular file, and two or more entries matching one component case-insensitively."""
    parts = [part for part in path.split("/") if part not in ("", ".")]
    if all(part == ".." for part in parts):
        return None  # only dot components: always a directory, never a file
    if ".." in parts:
        raise GateError("{}: import @{} has a `..` component; whether the loader resolves it before or after "
                        "a symlink is ambiguous; outside the enumerated grammar, cannot evaluate".format(
                            where, token))
    if kept and any(SHORT_NAME_RE.fullmatch(part) for part in parts):
        raise GateError("{}: import @{} has a Windows 8.3 short name component (such as PAYLOA~1.MD); on NTFS it "
                        "can open a long-named file the gate cannot identify; ambiguous, cannot "
                        "evaluate".format(where, token))
    return _case_walk(parts, base, where, token)


def follow_imports(root, origins, findings):
    """{resolved path: (units, header units)} for every file reachable through `@` import candidates from
    `origins`, a list of (text, the real directory it sits in, where, the line `text` starts on), each
    counted once with frontmatter and comments removed as for a rule file, its frontmatter held to the
    grammar and its `paths:` scope to the SCOPE grammar (_imported_scope), and its modelled header counted
    from its path relative to the repository root (header_units). Each run must be printable ASCII
    (_check_ascii), and every path candidate_paths gives that names a file is followed; a comment next to
    an `@` was refused before (_check_comment_joins). This is the gate's model of the pinned loader, not a
    proof that the loader loads no other file. A candidate whose tested paths
    or targets carry a BANNED_IMPORTS final component (any case) is appended to `findings`. GateError when
    a candidate is home relative or absolute (what it loads depends on the machine), when
    resolve_candidate fails, or when a target cannot be read, decoded, or held to the grammar."""
    real_root = os.path.realpath(root)
    seen = {}
    pending = list(origins)
    while pending:
        source, base, where, first_line = pending.pop()
        for run, pos in import_candidates(source):
            line = first_line + source.count("\n", 0, pos)
            here = "{}:{}".format(where, line)
            _check_ascii(run.replace(ESCAPED_SPACE, ""), where, line, "the import token " + repr(run))
            token = run.split("#", 1)[0].replace(ESCAPED_SPACE, " ")
            if token.startswith("~/") or (token.startswith("/") and token != "/"):
                raise GateError("{}: import @{} is home relative or absolute, so what it loads depends on "
                                "the machine; ambiguous, cannot evaluate".format(here, token))
            banned, targets = False, []
            for path in candidate_paths(run):
                if os.path.basename(path.rstrip("/")).casefold() in BANNED_FOLDED:
                    banned = True
                target = resolve_candidate(path, base, here, run, not _loader_rejects(run))
                if target is not None and target not in targets:
                    targets.append(target)
            if any(os.path.basename(t).casefold() in BANNED_FOLDED for t in targets):
                banned = True
            if banned:
                findings.append("{}: imports @{}; the pack must not import {}".format(
                    here, run, " or ".join(BANNED_IMPORTS)))
            for target in targets:
                if target in seen:
                    continue
                rel = loaded_rel(target, real_root)
                try:
                    text = read_text(Path(target), rel)
                except GateError as exc:
                    raise GateError("{}: import @{} names {}, which the gate cannot read ({}); whether the "
                                    "loader loads it is ambiguous; cannot evaluate".format(here, run, rel, exc))
                _check_characters(text, rel)
                _check_comment_joins(text, rel)
                front, body, front_line = split_frontmatter(text, rel)
                if front is not None:
                    _imported_scope(parse_frontmatter(front, rel, front_line), rel)
                seen[target] = (counted_units(body), header_units(rel))
                pending.append((body, os.path.dirname(target), rel, _body_line(text, body)))
    return seen


def _case_variants(root):
    """GateError when a case variant of a name the loader opens by a fixed name (CLAUDE.md and `.claude` at
    the root; CLAUDE.md and `rules` in `.claude/`) sits beside it: on a case-insensitive file system the
    loader opens or walks the variant, which the gate, reading the canonical name, does not measure."""
    for parent, names in (("", (CLAUDE_REL, ".claude")), (".claude", ("CLAUDE.md", "rules"))):
        directory = root / parent
        try:
            entries = os.listdir(directory)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise GateError("cannot list {} ({}); cannot evaluate".format(parent or ".", exc))
        for name in sorted(entries):
            for canonical in names:
                if name != canonical and _same_name(name, canonical):
                    raise GateError("{}: a case variant of {}, which the loader opens on a case-insensitive "
                                    "file system and the gate does not measure; ambiguous, cannot "
                                    "evaluate".format(os.path.join(parent, name), os.path.join(parent, canonical)))


def _fixed_file(root, rel):
    """The resolved path of the file the loader opens by the fixed name `rel` (CLAUDE.md,
    `.claude/CLAUDE.md`), checked before anything opens it: resolved hop by hop inside the repository
    (_resolve_checked), then by os.lstat of that resolved path a regular file. GateError otherwise, so a
    FIFO or device, inside the repository or out, is never opened."""
    real = _resolve_checked(root, rel, rel)
    try:
        mode = os.lstat(real).st_mode
    except OSError as exc:
        raise GateError("cannot read {} ({}); cannot evaluate".format(rel, exc))
    if not stat.S_ISREG(mode):
        raise GateError("cannot read {}: it is not a regular file; whether the loader reads it is ambiguous; "
                        "cannot evaluate".format(rel))
    return real


def _dot_claude(root):
    """[(text, real directory, where, 1)] for `.claude/CLAUDE.md`, held to the grammar as CLAUDE.md is, or
    [] when it is genuinely absent. Any other failure to look at or read it (a directory, a broken symlink,
    a permission error) is GateError, never read as absent; it is opened only after _fixed_file."""
    path = root / DOT_CLAUDE_REL
    try:
        os.lstat(path)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise GateError("cannot read {} ({}); cannot evaluate".format(DOT_CLAUDE_REL, exc))
    real = _fixed_file(root, DOT_CLAUDE_REL)
    text = read_text(real, DOT_CLAUDE_REL)
    _check_characters(text, DOT_CLAUDE_REL)
    _check_comment_joins(text, DOT_CLAUDE_REL)
    return [(text, os.path.dirname(real), DOT_CLAUDE_REL, 1)]


def measure(root):
    """Both totals and their parts. Banned imports reached from the rule files, `.claude/CLAUDE.md`, and
    the managed block are returned as findings; every cannot-evaluate condition raises GateError."""
    _case_variants(root)
    claude_path = _fixed_file(root, CLAUDE_REL)
    rules, counted, conditional, origins, rule_headers = measure_rules(root)
    claude = read_text(claude_path, CLAUDE_REL)
    _check_characters(claude, CLAUDE_REL)
    _check_comment_joins(claude, CLAUDE_REL)
    start, end = managed_block(claude)
    block = claude[start:end]
    findings = []
    real_root = os.path.realpath(root)
    # The loader resolves CLAUDE.md's imports from the real directory of the file, so a symlinked
    # CLAUDE.md resolves them from its target's directory.
    claude_base = os.path.dirname(claude_path)
    # The loader reads .claude/CLAUDE.md beside CLAUDE.md; it is counted whole, with its imports, into both.
    dot = _dot_claude(root)
    dot_units = sum(counted_units(text) for text, _, _, _ in dot)
    # Every loaded file carries a header: the rule files counted, CLAUDE.md, .claude/CLAUDE.md, each import.
    file_headers = rule_headers + sum(fixed_name_header(root, real_root, where)
                                      for where in [CLAUDE_REL] + [where for _, _, where, _ in dot])
    # Claude Code follows imports in rule files as in CLAUDE.md, so theirs count into both totals.
    pack_seen = follow_imports(
        root, origins + dot + [(block, claude_base, CLAUDE_REL, claude.count("\n", 0, start) + 1)], findings)
    # The adopter owns the bytes outside the block, so a banned import there is not the pack's finding:
    # only the rule files', .claude/CLAUDE.md's, and the managed block's findings fail the gate.
    session_seen = follow_imports(root, origins + dot + [(claude, claude_base, CLAUDE_REL, 1)], [])
    pack_imports = sum(units for units, _ in pack_seen.values())
    session_imports = sum(units for units, _ in session_seen.values())
    pack_headers = file_headers + sum(header for _, header in pack_seen.values())
    session_headers = file_headers + sum(header for _, header in session_seen.values())
    # The block's comments are placed in the whole CLAUDE.md, so a fence opened before the block counts.
    spans = comment_spans(claude)
    block_units = utf16_units(block) - sum(utf16_units(claude[s:e]) for s, e in spans if start <= s and e <= end)
    claude_units = counted_units(claude)
    return dict(rules=rules, rule_files=counted, conditional_files=conditional, block=block_units,
                dot_claude=dot_units, pack_imports=pack_imports, claude=claude_units,
                session_imports=session_imports, pack_headers=pack_headers, session_headers=session_headers,
                pack=rules + block_units + dot_units + pack_imports + pack_headers,
                session=rules + claude_units + dot_units + session_imports + session_headers,
                findings=findings)


def _budget_file(root):
    """The resolved path of the budget source, checked before anything opens it: inside the repository and,
    by os.lstat of that resolved path, a regular file. GateError otherwise, so a FIFO or device, inside the
    repository or out, is never opened."""
    real = os.path.realpath(root / BUDGET_REL)
    if not _inside(real, os.path.realpath(root)):
        raise GateError("{} resolves outside the repository; the gate reads its budget source only from "
                        "the repository; cannot evaluate".format(BUDGET_REL))
    try:
        mode = os.lstat(real).st_mode
    except OSError as exc:
        raise GateError("cannot read {} ({})".format(BUDGET_REL, exc))
    if not stat.S_ISREG(mode):
        raise GateError("cannot read {}: it is not a regular file; the gate reads its budget source only "
                        "from a regular file; cannot evaluate".format(BUDGET_REL))
    return real


def load_budget(root):
    """The validated budget source, opened only after _budget_file. GateError on a missing, unreadable, or
    malformed file: exactly the four BUDGET_KEYS, positive integer ratchet and ceiling (a bool is refused),
    an X.Y.Z version, and a lowercase hex SHA-256."""
    try:
        data = tomllib.loads(read_text(_budget_file(root), BUDGET_REL))
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
    print("PACK: {} UTF-16 code units ({} unconditional rule files {}, managed block {}, {} {}, "
          "their imports {}, file headers {})".format(m["pack"], m["rule_files"], m["rules"], m["block"],
                                                      DOT_CLAUDE_REL, m["dot_claude"], m["pack_imports"],
                                                      m["pack_headers"]))
    print("SESSION: {} UTF-16 code units (rule files {}, whole CLAUDE.md {}, {} {}, their imports {}, "
          "file headers {})".format(m["session"], m["rules"], m["claude"], DOT_CLAUDE_REL, m["dot_claude"],
                                    m["session_imports"], m["session_headers"]))
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
# Fixture trees only, never the live corpus (one check reads the budget source's comment text). Every case asserts its exact count or exit code through the
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


def _hdr(*rels):
    """The modelled header units of the loaded files `rels`, spelled out independently of header_units."""
    return sum(len("Contents of " + rel + " (project instructions, checked into the codebase):\n\n\n\n")
               for rel in rels)


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


def _mutant(tmp, old, new, *more):
    """A module loaded from the production prefix of this file with `old` replaced by `new` exactly once,
    written under the fixture directory `tmp` and loaded with importlib. A boundary or target that does
    not match exactly once is a harness error (exit 2)."""
    import importlib.util
    source = Path(__file__).read_text(encoding="utf-8")
    if source.count(MUTATION_BOUNDARY) != 1:
        print("SELF-TEST HARNESS ERROR: the mutation boundary is not unique", file=sys.stderr)
        sys.exit(2)
    production = source.split(MUTATION_BOUNDARY)[0]
    pairs = [(old, new)] + list(zip(more[::2], more[1::2]))
    for target, _ in pairs:
        if production.count(target) != 1:
            print("SELF-TEST HARNESS ERROR: revert target {!r} does not match exactly once".format(target),
                  file=sys.stderr)
            sys.exit(2)
    for target, replacement in pairs:
        production = production.replace(target, replacement, 1)
    path = Path(tempfile.mkdtemp(prefix="mutant-", dir=str(tmp))) / "check_instruction_budget_mutant.py"
    path.write_bytes(production.encode("utf-8"))
    spec = importlib.util.spec_from_file_location("check_instruction_budget_mutant", path)
    mutant = importlib.util.module_from_spec(spec)
    # The prefix puts its own directory on sys.path; restore it so the scratch copy never shadows tools/.
    saved = list(sys.path)
    try:
        spec.loader.exec_module(mutant)
    finally:
        sys.path[:] = saved
    return mutant


def _off_pairs(*names):
    """The _mutant replacement pairs that make each named production function return True: its rule
    removed."""
    pairs = []
    for name in names:
        pairs += ["def {}(".format(name), "def {}(*args):\n    return True\n\n\ndef _removed{}(".format(name, name)]
    return pairs


def _off(tmp, *names):
    """A _mutant in which each named production function returns True at once."""
    return _mutant(tmp, *_off_pairs(*names))


def _files(*pairs):
    """A mapping of fixture paths to text, from alternating path and text arguments."""
    return dict(zip(pairs[::2], pairs[1::2]))


def _blocks(read, fifo):
    """True when `read(fifo, where)` is still blocked after a second; a blocked read is then released by
    opening the FIFO for writing, so the self-test never hangs."""
    import threading

    def _attempt():
        try:
            read(fifo, "fifo")
        except Exception:  # the mutant module carries its own GateError class
            pass
    worker = threading.Thread(target=_attempt, daemon=True)
    worker.start()
    worker.join(1.0)
    blocked = worker.is_alive()
    for _ in range(100):
        if not worker.is_alive():
            break
        try:
            writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:  # ENXIO until the reader is waiting in open()
            worker.join(0.05)
            continue
        worker.join(5)
        os.close(writer)
    return blocked


def _opened_fifo(fn, root):
    """(fn(root), whether os.open was called on a FIFO while it ran)."""
    opened, real_open = [], os.open

    def recording(path, *args, **kwargs):
        try:
            opened.append(stat.S_ISFIFO(os.stat(path).st_mode))
        except OSError:
            pass
        return real_open(path, *args, **kwargs)
    os.open = recording
    try:
        code = _quiet(fn, root)
    finally:
        os.open = real_open
    return code, any(opened)


@contextlib.contextmanager
def _denied(directory):
    """`directory` with its permissions removed, so listing it or reading under it raises PermissionError.
    Permissions do not stop root, so as root os.lstat and os.listdir raise it there instead."""
    saved = os.lstat, os.listdir
    prefix = str(directory)

    def _deny(call):
        def wrapped(path, *args, **kwargs):
            text = os.fspath(path)
            if text.startswith(prefix + os.sep) or (call is saved[1] and text == prefix):
                raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), text)
            return call(path, *args, **kwargs)
        return wrapped

    os.chmod(prefix, 0)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        os.lstat, os.listdir = _deny(saved[0]), _deny(saved[1])
    try:
        yield
    finally:
        os.lstat, os.listdir = saved
        os.chmod(prefix, 0o700)


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


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
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
        bom = _tree(tmp / "bom", rules=_files("a.md", chr(0xFEFF) + "---\ncorpus-id: x\n---\nA\n"))
        check("exit/byte-order-mark-2", _quiet(run, bom), 2)
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
                                      ("scope/paths-empty-list-counted", "empty", "paths: []")):
            wide = _tree(tmp / ("wide-" + case), rules={"a.md": "---\n" + scope + "\n---\n" + "X" * 100})
            m = _measured(measure, wide)
            check(check_id, (m["rules"], m["rule_files"], m["conditional_files"]), (100, 1, 0))
        for check_id, case, scope in (("exit/paths-null-2", "null", "paths: null"),
                                      ("exit/paths-mapping-2", "mapping", "paths: {bad: value}"),
                                      ("exit/paths-bool-item-2", "bool-item", "paths:\n  - false"),
                                      ("exit/paths-number-item-2", "number-item", "paths: [1]"),
                                      ("exit/paths-nested-brace-2", "nested", 'paths: "{a,{b,c}}"'),
                                      ("exit/paths-unbalanced-brace-2", "unbalanced", 'paths: "src/{a,b"'),
                                      ("exit/paths-escape-2", "escape", "paths: 'src/\\*.md'"),
                                      ("exit/dotted-key-frontmatter-2", "dotted", 'paths: ["src/**"]\nx.y: a: b'),
                                      ("exit/at-value-frontmatter-2", "at-value", "paths: src/**\nslug: @owner"),
                                      ("exit/unclassified-frontmatter-line-2", "unclassified",
                                       "paths: src/**\nnot a key line"),
                                      ("exit/paths-nested-list-item-2", "nested-item", "paths:\n  - []"),
                                      ("exit/quoted-escape-frontmatter-2", "escape-q",
                                       'paths: src/**\ndescription: "bad\\q"'),
                                      ("exit/flow-mapping-frontmatter-value-2", "flow-map",
                                       "paths: src/**\ndescription: " + chr(0x7B) + "a"),
                                      ("exit/unknown-frontmatter-key-2", "unknown-key", "paths: src/**\nk2: a"),
                                      ("exit/non-ascii-frontmatter-2", "non-ascii",
                                       "paths: src/**\ndescription: caf" + chr(0xE9))):
            typed = _tree(tmp / ("typed-" + case), rules={"a.md": "---\n" + scope + "\n---\n" + "X" * 100})
            check(check_id, _quiet(run, typed), 2)
        # A plain entry is held to an allowlist (PLAIN_GLOB_RE), not a list of typed forms: the YAML 1.1
        # binary, base-60 and timestamp forms a denylist missed exit 2, as a value and as a list item,
        # while realistic globs, plain or quoted, stay conditional.
        yaml11 = ("2026-10-03", "0b101", "1:20", "2026-10-03T10:00:00Z", "190:20:30")
        for check_id, case, lead in (("exit/paths-yaml11-typed-value-2", "value", "paths: "),
                                     ("exit/paths-yaml11-typed-item-2", "item", "paths:\n  - src/**\n  - ")):
            check(check_id, [_quiet(run, _tree(tmp / "yaml11-{}-{}".format(case, i),
                                               rules={"a.md": "---\n" + lead + v + "\n---\n" + "X" * 100}))
                             for i, v in enumerate(yaml11)], [2] * len(yaml11))
        globs = ("paths: src/**/*.py", 'paths: "*.md"', "paths: docs/**", "paths: tools/check_*.py",
                 "paths: README.md", 'paths:\n  - "*.md"\n  - docs/**', "paths: [src/**/*.py, tools/check_*.py]",
                 "paths: src/a?.md", 'paths: ["src/a?.md"]')
        got = []
        for i, scope in enumerate(globs):
            m = _measured(measure, _tree(tmp / "glob-{}".format(i),
                                         rules={"a.md": "---\n" + scope + "\n---\n" + "X" * 100}))
            got.append(m if isinstance(m, str) else (m["rules"], m["rule_files"], m["conditional_files"]))
        check("scope/paths-realistic-globs-conditional", got, [(0, 0, 1)] * len(globs))
        # PyYAML ends a flow plain scalar at `?` and rejects the block, so a plain flow element holding one
        # exits 2 (FLOW_FORBIDDEN); quoted, or as a block value, it is read above.
        flowq = _tree(tmp / "flow-question", rules={"a.md": "---\npaths: [src/a?.md]\n---\n" + "X" * 100})
        check("exit/paths-flow-question-mark-2", _quiet(run, flowq), 2)
        src = _tree(tmp / "src", rules={"a.md": '---\npaths: ["src/**"]\n---\nabc\n', "b.md": "xy\n",
                                        "c.md": '---\npaths: "{src,lib}/**"\n---\nabc\n'})
        m = _measured(measure, src)
        check("scope/paths-src-excluded", (m["rules"], m["rule_files"], m["conditional_files"]), (3, 1, 2))
        # A conditional rule file's own body is not counted, but the loader reads its import tree in every
        # session and drops only the entries that carry their own scope, so its imports count, to any depth.
        condimp = _tree(tmp / "cond-import", rules={"c.md": "---\npaths: src/**\n---\nSee @big.txt\n",
                                                    "big.txt": "X" * 1000})
        m = _measured(measure, condimp)
        check("scope/conditional-file-import-counted",
              (m["rules"], m["rule_files"], m["conditional_files"], m["pack_imports"], m["pack"]),
              (0, 0, 1, 1000, 1000 + _hdr("CLAUDE.md", ".claude/rules/big.txt")))
        condchain = _tree(tmp / "cond-chain", rules={"c.md": "---\npaths: src/**\n---\n@a.txt\n",
                                                     "a.txt": "@b.txt\n", "b.txt": "X" * 1000})
        m = _measured(measure, condchain)
        check("scope/conditional-file-two-deep-import-counted",
              (m["rules"], m["pack_imports"], m["session_imports"]), (0, 1007, 1007))
        # A quoted flow-list item may hold a comma: the sequence splits only outside quotes, and the
        # loader's normalizer then splits the string itself.
        for check_id, case, scope, want in (
                ("scope/flow-quoted-comma-counted", "comma", 'paths: ["**,**"]', (4, 1, 0)),
                ("scope/flow-quoted-brace-comma-counted", "brace", "paths: ['{**,**}', \"/**\"]", (4, 1, 0)),
                ("scope/flow-quoted-comma-src-excluded", "comma-src", 'paths: ["src/**,lib/**", "**"]', (0, 0, 1))):
            flowed = _tree(tmp / ("flow-" + case), rules={"a.md": "---\n" + scope + "\n---\nabc\n"})
            m = _measured(measure, flowed)
            check(check_id, (m["rules"], m["rule_files"], m["conditional_files"]), want)
        badflow = _tree(tmp / "flow-text-after-quote", rules={"a.md": '---\npaths: ["a"b, c]\n---\nabc\n'})
        check("exit/flow-text-after-quote-2", _quiet(run, badflow), 2)

        # Imports and totals: an @import in the managed block is followed and counted into PACK; SESSION
        # adds the bytes outside the block.
        imp = _tree(tmp / "import", block="\n@docs/extra.md\n", extra={"docs/extra.md": "Imported\n"})
        m = _measured(measure, imp)
        check("import/managed-block-import-followed", (m["block"], m["pack_imports"], m["pack"]),
              (16, 9, 25 + _hdr("CLAUDE.md", "docs/extra.md")))
        ruleimp = _tree(tmp / "ruleimp", rules={"a.md": "@big.txt\n"},
                        extra={".claude/rules/big.txt": "---\ncorpus-id: x\n---\n" + "X" * 100})
        m = _measured(measure, ruleimp)
        ruleimp_hdr = _hdr(".claude/rules/a.md", "CLAUDE.md", ".claude/rules/big.txt")
        check("import/rule-file-import-counted", (m["rules"], m["pack_imports"], m["pack"], m["session"]),
              (9, 100, 109 + ruleimp_hdr, 172 + ruleimp_hdr))
        # Every `@` is a candidate whatever Markdown surrounds it: delimiters after the path, a tag or a
        # link around it, a comment or a code span holding it, and backticks paired across blocks.
        big = {".claude/rules/big.txt": "X" * 1000}
        for check_id, case, body in (("import/bold-import-counted", "bold", "**@big.txt**\n"),
                                     ("import/underscore-import-counted", "underscore", "_@big.txt_\n"),
                                     ("import/html-tag-import-counted", "tag", "<b>@big.txt</b>\n"),
                                     ("import/link-import-counted", "link", "[see @big.txt](x)\n"),
                                     ("import/list-item-comment-import-counted", "list-comment",
                                      "- a <!--\n  @big.txt\n  -->\n"),
                                     ("import/backticks-across-heading-counted", "heading",
                                      "` x\n# @big.txt\n` y\n"),
                                     ("import/backticks-across-list-items-counted", "list-items",
                                      "- ` x\n- @big.txt\n- ` y\n"),
                                     ("import/code-span-import-counted", "span", "`@big.txt`\n"),
                                     ("import/case-insensitive-import-counted", "case", "@BIG.txt\n")):
            marked = _tree(tmp / ("marked-" + case), rules={"a.md": body}, extra=big)
            check(check_id, _measured(measure, marked)["pack_imports"], 1000)
        # A comment beside an `@` exits 2: the loader removes it before it looks for imports, so it can join
        # an import path across it (`@docs/big<!---->.md` loads docs/big.md), in any measured file.
        bigdoc = {"docs/big.md": "X" * 1000}
        joined = []
        for check_id, case, parts in (
                ("exit/comment-split-rule-import-2", "rule",
                 dict(rules={"a.md": "<!-- a -->@big<!-- b -->.txt\n", "big.txt": "X" * 1000})),
                ("exit/comment-split-block-import-2", "block",
                 dict(block="\n<!-- a -->@docs/big<!---->.md\n", extra=bigdoc)),
                ("exit/comment-split-agents-import-2", "agents",
                 dict(block="\n<!-- a -->@AGE<!---->NTS.md\n", extra={"AGENTS.md": "x\n"})),
                ("exit/comment-split-outside-block-2", "outside",
                 dict(outside="@docs/big<!---->.md\n", extra=bigdoc)),
                ("exit/comment-split-in-imported-file-2", "target",
                 dict(rules={"a.md": "@mid.txt\n", "mid.txt": "@big<!---->.txt\n", "big.txt": "X" * 1000})),
                ("exit/comment-split-conditional-rule-2", "conditional",
                 dict(rules={"c.md": "---\npaths: src/**\n---\n@big<!---->.txt\n", "big.txt": "X" * 1000})),
                ("exit/comment-after-at-across-lines-2", "lines",
                 dict(rules={"a.md": "@\n<!-- a -->big.txt\n", "big.txt": "X" * 1000})),
                ("exit/comment-split-after-space-2", "space",
                 dict(rules={"a.md": "See @big<!---->.txt\n", "big.txt": "X" * 1000}))):
            joined.append(_tree(tmp / ("join-" + case), **parts))
            code, err = _stderr_of(run, joined[-1])
            check(check_id, (code, "HTML comment" in err), (2, True))
        # An `@` inside a word begins no import (the loader reads one only at the start or after whitespace),
        # so an email address inside an HTML comment is not refused, in a rule file or in CLAUDE.md.
        owner = "<!-- owner: ops@example.com -->\n\n"
        email_comment = _tree(tmp / "email-comment", rules={"a.md": owner + "A\n"}, outside=owner)
        check("exit/comment-email-0", _quiet(run, email_comment), 0)
        # .claude/CLAUDE.md loads beside CLAUDE.md: counted whole, with its imports, into both totals.
        dotclaude = _tree(tmp / "dot-claude", rules={"a.md": "A\n"},
                          extra={".claude/CLAUDE.md": "X" * 50000 + "\n@big.txt\n", ".claude/big.txt": "Y" * 1000})
        m = _measured(measure, dotclaude)
        dot_hdr = _hdr(".claude/rules/a.md", "CLAUDE.md", ".claude/CLAUDE.md", ".claude/big.txt")
        check("count/dot-claude-claude-md-counted",
              (m["dot_claude"], m["pack_imports"], m["session_imports"], m["pack_headers"], m["pack"],
               m["session"] - m["claude"]),
              (50010, 1000, 1000, dot_hdr, 2 + 50010 + 1000 + dot_hdr, 2 + 50010 + 1000 + dot_hdr))
        # Each loaded file counts a modelled header: the fixed text and its path relative to the root.
        headed = _tree(tmp / "headed", rules={"a.md": "A\n", "c.md": "---\npaths: src/**\n---\nC\n"})
        m = _measured(measure, headed)
        check("count/loaded-file-header-counted", (m["pack_headers"], m["session_headers"], m["pack"]),
              (_hdr(".claude/rules/a.md", "CLAUDE.md"), _hdr(".claude/rules/a.md", "CLAUDE.md"),
               2 + _hdr(".claude/rules/a.md", "CLAUDE.md")))
        # An imported file's frontmatter scope is held to the SCOPE grammar, as a rule file's is.
        scoped = []
        for case, scope in (("empty", "paths:"), ("nested", 'paths: "{a,{b,c}}"'),
                            ("unbalanced", 'paths: "src/{a,b"')):
            scoped.append(_tree(tmp / ("imported-scope-" + case), block="\n@x.txt\n",
                                extra={"x.txt": "---\n" + scope + "\n---\n" + "X" * 1000}))
        check("exit/imported-file-scope-grammar-2", [_quiet(run, r) for r in scoped], [2, 2, 2])
        prefixes = _tree(tmp / "prefixes", rules={"a.md": "@x.txt_b.txt\n"},
                         extra={".claude/rules/x.txt_b.txt": "X" * 10, ".claude/rules/x.txt": "Y" * 7})
        check("import/every-prefix-naming-a-file-counted", _measured(measure, prefixes)["pack_imports"], 17)
        twocase = _tree(tmp / "twocase", block="\n@Docs/Big.md\n",
                        extra={"docs/big.md": "x", "DOCS/big.md": "y"})
        code, err = _stderr_of(run, twocase)
        check("exit/case-insensitive-two-matches-ambiguous-2", (code, "ambiguous" in err), (2, True))
        ruleagents = _tree(tmp / "ruleagents", rules={"a.md": "@AGENTS.md\n"}, extra={"AGENTS.md": "x\n"})
        check("exit/rule-file-agents-import-1", _quiet(run, ruleagents), 1)
        boldagents = _tree(tmp / "boldagents", rules={"a.md": "**@AGENTS.md**\n"},
                           extra={"AGENTS.md": "x\n"})
        check("exit/bold-agents-import-1", _quiet(run, boldagents), 1)
        listspan = _tree(tmp / "listspan", block="\n- see `cat @AGENTS.md now`\n", extra={"AGENTS.md": "x\n"})
        check("exit/list-item-code-span-agents-import-1", _quiet(run, listspan), 1)
        lowercase = _tree(tmp / "lowercase", block="\n@agents.md\n")
        check("exit/lowercase-agents-name-1", _quiet(run, lowercase), 1)
        # A candidate naming no file adds nothing and does not fail.
        rulemissing = _tree(tmp / "rulemissing", rules={"a.md": "@missing.txt\n"})
        check("import/missing-target-skipped-0",
              (_measured(measure, rulemissing)["pack_imports"], _quiet(run, rulemissing)), (0, 0))
        email = _tree(tmp / "email", outside="Mail name@example.com before merging.\n")
        check("exit/outside-block-email-prose-0", _quiet(run, email), 0)
        mention = _tree(tmp / "mention", outside="Ping @alice before merging...@...\n")
        check("exit/outside-block-mention-prose-0", _quiet(run, mention), 0)
        fragment = _tree(tmp / "fragment", block="\n@docs/x.md#usage\n", extra={"docs/x.md": "Doc\n"})
        check("import/fragment-cut-followed", _measured(measure, fragment)["pack_imports"], 4)
        # An import the gate cannot settle exits 2 and says it is ambiguous.
        home = _tree(tmp / "home", outside="See @~/notes.md first.\n")
        code, err = _stderr_of(run, home)
        check("exit/home-import-ambiguous-2", (code, "ambiguous" in err), (2, True))
        undecodable = _tree(tmp / "undecodable", block="\n@docs/x.md\n")
        (undecodable / "docs").mkdir()
        (undecodable / "docs" / "x.md").write_bytes(b"ok \xff\n")
        code, err = _stderr_of(run, undecodable)
        check("exit/undecodable-import-ambiguous-2", (code, "ambiguous" in err), (2, True))
        rulecycle = _tree(tmp / "rulecycle", rules={"a.md": "@b.txt\n"},
                          extra={".claude/rules/b.txt": "@c.txt\n", ".claude/rules/c.txt": "@b.txt\n"})
        check("import/rule-file-cycle-terminates", _measured(measure, rulecycle)["pack_imports"], 14)
        mismatched = _tree(tmp / "mismatched", block="\n` literal\n\n@AGENTS.md\n\n`` literal\n",
                           extra={"AGENTS.md": "X" * 100})
        check("exit/mismatched-backticks-agents-import-1", _quiet(run, mismatched), 1)
        spanned = _tree(tmp / "spanned", block="\n`@AGENTS.md` and ``@RULES-INDEX.md``\n")
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
        # A header names the resolved path, as the loader writes it, not the link's name: lengthening the
        # target of a symlinked rule file or rule directory by 200 characters grows PACK by exactly 200, and
        # so does lengthening the target of a symlinked CLAUDE.md or .claude/CLAUDE.md (the longer of its
        # fixed name and that path).
        linked_packs = dict()
        for case, n in (("short", 1), ("long", 201)):
            filelink = _tree(tmp / ("hdr-file-" + case), extra=_files("docs/" + "x" * n + ".txt", "X" * 1000))
            os.symlink(os.path.join("..", "..", "docs", "x" * n + ".txt"), str(filelink / RULES_REL / "a.md"))
            dirlink = _tree(tmp / ("hdr-dir-" + case), extra=_files("docs/" + "d" * n + "/b.md", "X" * 1000))
            os.symlink(os.path.join("..", "..", "docs", "d" * n), str(dirlink / RULES_REL / "sub"))
            claudelink = _tree(tmp / ("hdr-claude-" + case), extra=_files("c" * n + "/keep", ""))
            os.rename(str(claudelink / CLAUDE_REL), str(claudelink / ("c" * n) / CLAUDE_REL))
            os.symlink(os.path.join("c" * n, CLAUDE_REL), str(claudelink / CLAUDE_REL))
            dotlink = _tree(tmp / ("hdr-dot-" + case), extra=_files("e" * (n + 7) + "/CLAUDE.md", "X"))
            os.symlink(os.path.join("..", "e" * (n + 7), "CLAUDE.md"), str(dotlink / DOT_CLAUDE_REL))
            linked_packs[case] = [_measured(measure, r)["pack"] for r in (filelink, dirlink, claudelink, dotlink)]
        short, long_ = linked_packs["short"], linked_packs["long"]
        check("count/symlinked-rule-file-header-resolved-path", (short[0], long_[0] - short[0]),
              (1000 + _hdr("docs/x.txt", "CLAUDE.md"), 200))
        check("count/symlinked-rule-dir-header-resolved-path", (short[1], long_[1] - short[1]),
              (1000 + _hdr("docs/d/b.md", "CLAUDE.md"), 200))
        check("count/symlinked-claude-md-header-resolved-path",
              (short[2], long_[2] - short[2], short[3], long_[3] - short[3]),
              (_hdr("c/CLAUDE.md"), 200, 1 + _hdr("CLAUDE.md", "eeeeeeee/CLAUDE.md"), 200))
        # A case variant of a name the loader opens by a fixed name exits 2: a case-insensitive file system
        # opens or walks it, and the gate, reading the canonical name, would not measure it.
        variants = []
        for check_id, case, extra in (("exit/case-variant-root-claude-md-2", "root-claude", _files("claude.md", "X")),
                                      ("exit/case-variant-dot-claude-dir-2", "dot-dir",
                                       _files(".Claude/CLAUDE.md", "X")),
                                      ("exit/case-variant-dot-claude-md-2", "dot-claude",
                                       _files(".claude/claude.md", "X")),
                                      ("exit/case-variant-rules-dir-2", "rules-dir",
                                       _files(".claude/Rules/b.md", "X"))):
            variant = _tree(tmp / ("case-" + case), rules=_files("a.md", "a"), extra=extra)
            variants.append(variant)
            code, err = _stderr_of(run, variant)
            check(check_id, (code, "case variant" in err), (2, True))
        # Case ambiguity is checked at every component even when the exact name exists: an import, a rule
        # symlink's resolved target, and two rule entries that differ only in case exit 2, since a
        # case-insensitive file system holds only one of each pair.
        big_b = "B" * 50000
        collisions, rule_collisions = [], []
        for check_id, case, block, extra in (
                ("exit/case-ambiguous-import-exact-file-2", "file", "\n@docs/X.md\n",
                 _files("docs/X.md", "s", "docs/x.md", big_b)),
                ("exit/case-ambiguous-import-exact-dir-2", "dir", "\n@Docs/a.md\n",
                 _files("Docs/a.md", "s", "docs/a.md", big_b)),
                ("exit/case-ambiguous-nested-import-exact-2", "nested", "\n@n/a.md\n",
                 _files("n/a.md", "@X.txt\n", "n/X.txt", "s", "n/x.txt", big_b))):
            collided = _tree(tmp / ("collide-" + case), block=block, extra=extra)
            collisions.append(collided)
            code, err = _stderr_of(run, collided)
            check(check_id, (code, "ambiguous" in err), (2, True))
        sym_target = _tree(tmp / "collide-symlink", extra=_files("docs/X.md", "s", "docs/x.md", big_b))
        os.symlink(os.path.join("..", "..", "docs", "X.md"), str(sym_target / RULES_REL / "a.md"))
        for check_id, collided in (
                ("exit/case-ambiguous-rule-symlink-target-2", sym_target),
                ("exit/case-colliding-rule-files-2",
                 _tree(tmp / "collide-rule-files", rules=_files("a.md", "a", "A.md", big_b))),
                ("exit/case-colliding-rule-dirs-2",
                 _tree(tmp / "collide-rule-dirs", rules=_files("s/a.md", "a", "S/b.md", big_b)))):
            rule_collisions.append(collided)
            code, err = _stderr_of(run, collided)
            check(check_id, (code, "differ only in case" in err), (2, True))
        # A rule file that is not a regular file exits 2 and is never read; a Windows 8.3 short name
        # component in an import exits 2.
        fifo = _tree(tmp / "fifo")
        os.mkfifo(str(fifo / RULES_REL / "a.md"))
        code, err = _stderr_of(run, fifo)
        check("exit/fifo-rule-file-2", (code, "not a regular file" in err), (2, True))
        shortname = _tree(tmp / "shortname", block="\n@docs/PAYLOA~1.MD\n",
                          extra=_files("docs/payload-long-name.md", big_b))
        code, err = _stderr_of(run, shortname)
        check("exit/windows-short-name-import-2", (code, "8.3 short name" in err), (2, True))
        # `~` and a digit in prose is no 8.3 short name: git's `@~1` and an npm tilde range pass.
        tilde_prose = [_tree(tmp / "tilde-git", outside="Review with `git diff @~1` before pushing.\n"),
                       _tree(tmp / "tilde-npm", rules=_files("a.md", "Pin it: `npm i lodash@~4.17.21`.\n"))]
        check("exit/tilde-prose-not-short-name-0", [_quiet(run, r) for r in tilde_prose], [0, 0])
        # An 8.3 short name whose base holds a `~` (`A~VERY~1.MD`) is one too.
        tilde_base = _tree(tmp / "short-tilde-base", block="\n@docs/A~VERY~1.MD\n",
                           extra=_files("docs/a~very-long-payload.md", big_b))
        code, err = _stderr_of(run, tilde_base)
        check("exit/short-name-tilde-in-base-2", (code, "8.3 short name" in err), (2, True))
        # The loader's path filter drops a run starting with `{`, so git's reflog ancestry in prose passes.
        in_word = [_tree(tmp / "in-word-reflog", outside="Undo with `git reset --hard HEAD@{1}~1` first.\n"),
                   _tree(tmp / "in-word-rule", rules=_files("a.md", "Then `git reset --hard HEAD@{1}~1`.\n"))]
        check("exit/in-word-at-not-short-name-0", [_quiet(run, r) for r in in_word], [0, 0])
        # The loader starts a text token right after an inline token, so an `@` there begins an import and
        # a short name in it exits 2 (N1 to N8: emphasis, HTML, a code span, a link, strong, underscore
        # emphasis, an escape, and a conditional rule file).
        long_md, long_txt = _files("docs/payload-long-name.md", big_b), _files(
            ".claude/rules/sub/payload-long-name.txt", big_b, ".claude/rules/payload-long-name.txt", big_b)
        after_inline = [
            _tree(tmp / "inline-em", block="\n*see*@docs/PAYLOA~1.MD\n", extra=long_md),
            _tree(tmp / "inline-html", block="\n<b>@docs/PAYLOA~1.MD</b> x\n", extra=long_md),
            _tree(tmp / "inline-code", block="\n`x`@docs/PAYLOA~1.MD\n", extra=long_md),
            _tree(tmp / "inline-link", block="\n[l](u)@docs/PAYLOA~1.MD\n", extra=long_md),
            _tree(tmp / "inline-strong", rules=_files("a.md", "Read **this**@sub/PAYLOA~1.TXT now.\n"),
                  extra=long_txt),
            _tree(tmp / "inline-dot-claude", extra=_files(".claude/CLAUDE.md", "_x_@PAYLOA~1.MD\n",
                                                          ".claude/payload-long-name.md", big_b)),
            _tree(tmp / "inline-escape", rules=_files("a.md", "\\*@PAYLOA~1.TXT\n"), extra=long_txt),
            _tree(tmp / "inline-conditional", rules=_files("a.md", '---\npaths: ["src/**"]\n---\n*e*@PAYLOA~1.TXT\n'),
                  extra=long_txt)]
        check("exit/short-name-after-inline-token-2",
              [(lambda got: (got[0], "8.3 short name" in got[1]))(_stderr_of(run, r)) for r in after_inline],
              [(2, True)] * 8)
        # Both git prose forms the loader's path filter drops pass, in plain prose and after an inline token.
        git_prose = [_tree(tmp / "git-reflog-prose", rules=_files("a.md", "Undo with git reset --hard HEAD@{1}~1 first.\n")),
                     _tree(tmp / "git-upstream-prose", block="\nCompare with git diff @{u}~1 before pushing.\n"),
                     _tree(tmp / "git-reflog-strong", outside="Then **HEAD**@{1}~1 again.\n")]
        check("exit/git-prose-loader-filter-0", [_quiet(run, r) for r in git_prose], [0, 0, 0])
        # A case collision on a symlink met on the way to a target (Link.txt beside link.txt), which the
        # fully resolved path no longer shows, exits 2 at every entry point the loader resolves.
        hop_collisions = []
        for case, files, links in (
                ("rule-file", _files("docs/small.txt", "s", "docs/large.txt", big_b),
                 (("docs/Link.txt", "small.txt"), ("docs/link.txt", "large.txt"),
                  (RULES_REL + "/a.md", "../../docs/Link.txt"))),
                ("rule-dir", _files("docs/small/b.md", "s", "docs/large/b.md", big_b),
                 (("docs/Link", "small"), ("docs/link", "large"), (RULES_REL + "/sub", "../../docs/Link"))),
                ("rules-base", _files("docs/small/b.md", "s", "docs/large/b.md", big_b),
                 (("docs/Link", "small"), ("docs/link", "large"), (RULES_REL, "../docs/Link"))),
                ("claude-md", _files("docs/small.md", gen_claude.BEGIN + gen_claude.END,
                                     "docs/large.md", big_b + gen_claude.BEGIN + gen_claude.END),
                 (("docs/Link.md", "small.md"), ("docs/link.md", "large.md"), (CLAUDE_REL, "docs/Link.md"))),
                ("dot-claude-md", _files("docs/small.md", "s", "docs/large.md", big_b),
                 (("docs/Link.md", "small.md"), ("docs/link.md", "large.md"), (DOT_CLAUDE_REL, "../docs/Link.md")))):
            hop = _tree(tmp / ("hop-" + case), extra=files)
            for rel, target in links:
                if (hop / rel).is_dir():
                    os.rmdir(str(hop / rel))
                elif (hop / rel).exists():
                    os.unlink(str(hop / rel))
                os.symlink(target, str(hop / rel))
            hop_collisions.append(hop)
        check("exit/case-collision-on-symlink-hop-2",
              [(lambda out: (out[0], "differ only in case" in out[1]))(_stderr_of(run, r)) for r in hop_collisions],
              [(2, True)] * 5)
        # Two names that differ only in case among rule-directory entries that are neither directories nor
        # `.md` files load nothing either way and pass.
        other_files = _tree(tmp / "other-files", rules=_files("a.md", "a", "notes.txt", "n", "Notes.txt", "N"))
        check("exit/case-non-rule-entries-0", _quiet(run, other_files), 0)
        # Two directories that differ only in case exit 2 on every path the gate resolves, whichever the next
        # component names: an import (and an import's own import), a rule symlink, CLAUDE.md, `.claude/CLAUDE.md`,
        # a rule directory and the rules base, through a `..` or a relative link after the pair too. The merged
        # directory a case-insensitive file system makes is not modelled (the first two pass there: an
        # over-refusal).
        dir_pairs = []
        dotdot = ("Docs/sub/k.txt", "k", "docs/sub/k.txt", "k")
        for case, block, files, links in (
                ("import", "\n@docs/a.md\n", _files("docs/a.md", "s", "Docs/other.txt", "o"), ()),
                ("link", "", _files("docs/a.md", "s", "Docs/other.txt", "o"),
                 ((RULES_REL + "/a.md", "../../docs/a.md"),)),
                ("import-chain", "\n@docs/a.md\n", _files("docs/a.md", "@y.md\n", "Docs/y.md", big_b), ()),
                ("import-chain-sub", "\n@docs/a.md\n",
                 _files("docs/a.md", "see @sub/y.md\n", "Docs/sub/y.md", big_b), ()),
                ("import-chain-dot-claude", "",
                 _files(DOT_CLAUDE_REL, "@x/a.md\n", ".claude/x/a.md", "@y.md\n", ".claude/X/y.md", big_b), ()),
                ("dotdot-rule-file", "", _files(*dotdot, "Docs/x.md", "s", "docs/x.md", big_b),
                 ((RULES_REL + "/a.md", "../../Docs/sub/../x.md"),)),
                ("dotdot-claude-md", "", _files(*dotdot, "Docs/x.md", gen_claude.BEGIN + gen_claude.END,
                                                "docs/x.md", big_b + gen_claude.BEGIN + gen_claude.END),
                 ((CLAUDE_REL, "Docs/sub/../x.md"),)),
                ("dotdot-rules-base", "", _files(*dotdot, "Docs/r/b.md", "s", "docs/r/b.md", big_b),
                 ((RULES_REL, "../Docs/sub/../r"),)),
                ("relative-rule-file", "", _files("Docs/x.md", big_b, "docs/x.md", "s"),
                 (("docs/lnk", "x.md"), (RULES_REL + "/a.md", "../../docs/lnk"))),
                ("relative-dot-claude-md", "", _files("Docs/x.md", big_b, "docs/x.md", "s"),
                 (("docs/lnk", "x.md"), (DOT_CLAUDE_REL, "../docs/lnk"))),
                ("relative-rule-dir", "", _files("Docs/rr/b.md", big_b, "docs/rr/b.md", "s"),
                 (("docs/lnk", "rr"), (RULES_REL + "/sub", "../../docs/lnk")))):
            pair = _tree(tmp / ("dir-pair-" + case), block=block, extra=files)
            for rel, target in links:
                if (pair / rel).is_dir():
                    os.rmdir(str(pair / rel))
                elif (pair / rel).exists():
                    os.unlink(str(pair / rel))
                os.symlink(target, str(pair / rel))
            dir_pairs.append(pair)
        check("exit/case-variant-directory-pair-2", [_quiet(run, r) for r in dir_pairs], [2] * 11)
        # Names an upcase table or a normalizing file system merges, which str.casefold alone keeps apart (a
        # dotless i, U+037E against `;`), collide too.
        folds = [_tree(tmp / "fold-dotless-import", block="\n@docs/file.md\n",
                       extra=_files("docs/file.md", "s", "docs/f\u0131le.md", big_b)),
                 _tree(tmp / "fold-greek-question", block="\n@docs/a;b.md\n",
                       extra=_files("docs/a;b.md", "s", "docs/a\u037eb.md", big_b)),
                 _tree(tmp / "fold-dotless-rules", rules=_files("file.md", "a", "f\u0131le.md", big_b))]
        check("exit/case-collision-upper-and-nfd-folds-2", [_quiet(run, r) for r in folds], [2, 2, 2])
        # CLAUDE.md and .claude/CLAUDE.md are checked to resolve inside the repository and to be regular files
        # before anything opens them: a FIFO outside the repository or inside it is never opened.
        os.makedirs(str(tmp / "fixed-outside"))
        os.mkfifo(str(tmp / "fixed-outside" / "fifo"))
        fixed_fifos = []
        for case, rel, target in (("claude-out", CLAUDE_REL, "../fixed-outside/fifo"),
                                  ("dot-out", DOT_CLAUDE_REL, "../../fixed-outside/fifo"),
                                  ("claude-in", CLAUDE_REL, "fifo"), ("dot-in", DOT_CLAUDE_REL, "../fifo")):
            fixed = _tree(tmp / case)
            if rel == CLAUDE_REL:
                os.unlink(str(fixed / rel))
            if not target.startswith("../fixed") and not target.startswith("../../fixed"):
                os.mkfifo(str(fixed / "fifo"))
            os.symlink(target, str(fixed / rel))
            fixed_fifos.append(fixed)
        check("exit/fixed-file-checked-before-open-2", [_opened_fifo(run, r) for r in fixed_fifos],
              [(2, False)] * 4)
        # The budget source too: linked to a FIFO outside the repository or inside it, it is never opened.
        budget_fifos = []
        for case, target in (("budget-out", "../../../fixed-outside/fifo"), ("budget-in", "../../fifo")):
            linked = _tree(tmp / case)
            if case == "budget-in":
                os.mkfifo(str(linked / "fifo"))
            os.unlink(str(linked / BUDGET_REL))
            os.symlink(target, str(linked / BUDGET_REL))
            budget_fifos.append(linked)
        check("exit/budget-checked-before-open-2", [_opened_fifo(run, r) for r in budget_fifos], [(2, False)] * 2)

        # The enumerated grammar: a character outside it anywhere in a measured file, a non-ASCII import
        # token, a `..` component or a symlink on an import path, and a directory or unreadable target exit 2;
        # a symlinked CLAUDE.md resolves its imports from its target's directory; a text holding `<!--`
        # with no final newline counts the newline the loader's rebuild can add.
        nel, bom_char = chr(0x85), chr(0xFEFF)
        for check_id, case, rules, extra, block in (
                ("exit/nel-in-import-token-2", "nel-token", _files("a.md", "@big" + nel + "file.txt\n"),
                 _files(".claude/rules/big" + nel + "file.txt", "X" * 1000), ""),
                ("exit/bom-after-import-token-2", "bom-token", _files("a.md", "@big.txt" + bom_char + "suffix\n"),
                 big, ""),
                ("exit/nel-in-body-2", "nel-body", _files("a.md", "---\ncorpus-id: x\n---\n" + nel + "X"), None, ""),
                ("exit/control-character-frontmatter-fence-2", "fs-fence",
                 _files("a.md", "---" + chr(0x1C) + '\npaths: ["src/**"]\n---\n' + "X" * 1000), None, ""),
                ("exit/nel-frontmatter-fence-2", "nel-fence",
                 _files("a.md", "---" + nel + '\npaths: ["src/**"]\n---\n' + "X" * 1000), None, ""),
                ("exit/bom-after-block-import-2", "bom-block", None, _files("big.txt", "X" * 1000),
                 "\nSee @big.txt" + bom_char + " now\n")):
            check(check_id, _quiet(run, _tree(tmp / ("grammar-" + case), rules=rules, extra=extra, block=block)), 2)
        cafe = _tree(tmp / "cafe", rules=_files("a.md", "@caf" + chr(0xE9) + ".txt\n"),
                     extra=_files(".claude/rules/caf" + chr(0xE9) + ".txt", "X" * 1000))
        check("exit/non-ascii-import-token-2", _quiet(run, cafe), 2)
        folder = _tree(tmp / "folder", rules=_files("a.md", "@folder\n"))
        (folder / RULES_REL / "folder").mkdir()
        check("exit/directory-import-2", _quiet(run, folder), 2)
        dotdot = _tree(tmp / "dotdot", rules=_files("a.md", "@sub/../big.txt\n"),
                       extra=_files(".claude/rules/big.txt", "X" * 1000, "docs/deep/k.txt", "k"))
        os.symlink(os.path.join("..", "..", "docs", "deep"), str(dotdot / RULES_REL / "sub"))
        check("exit/dotdot-after-symlinked-dir-2", _quiet(run, dotdot), 2)
        symimport = _tree(tmp / "symimport", rules=_files("a.md", "@sub/big.txt\n"),
                          extra=_files("docs/deep/big.txt", "X" * 1000))
        os.symlink(os.path.join("..", "..", "docs", "deep"), str(symimport / RULES_REL / "sub"))
        check("exit/symlinked-import-component-2", _quiet(run, symimport), 2)
        private = _tree(tmp / "private", block="\n@private/big.txt\n",
                        extra=_files("private/big.txt", "X" * 1000))
        with _denied(private / "private"):
            check("exit/unreadable-import-directory-2", _quiet(run, private), 2)
            lenient_read = _mutant(tmp, "    except OSError as exc:  # anything but a genuinely absent entry\n",
                                   "    except OSError as exc:  # anything but a genuinely absent entry\n"
                                   "        return None\n")
            check("revert/unreadable-import-red", _quiet(lenient_read.run, private), 0)
        linkclaude = _tree(tmp / "linkclaude", block="\n@big.md\n", extra=_files("docs/big.md", "X" * 1000))
        os.rename(str(linkclaude / CLAUDE_REL), str(linkclaude / "docs" / CLAUDE_REL))
        os.symlink(os.path.join("docs", CLAUDE_REL), str(linkclaude / CLAUDE_REL))
        check("import/symlinked-claude-md-real-base-counted", _measured(measure, linkclaude)["pack_imports"], 1000)
        lexer = _tree(tmp / "lexer", rules=_files("a.md", "> 1)  1) #<!--- <\n==="))
        check("count/comment-file-without-final-newline-plus-one", _measured(measure, lexer)["rules"], 22)

        session = _tree(tmp / "session", rules={"a.md": "abc\n"}, block="Block", outside="Hello\n")
        m = _measured(measure, session)
        session_hdr = _hdr(".claude/rules/a.md", "CLAUDE.md")
        check("totals/session-adds-outside-block", (m["pack"], m["session"]), (9 + session_hdr, 78 + session_hdr))

        # Exit codes: at the ratchet passes; one unit over fails; a banned import fails. PACK is the ten
        # characters plus the headers of the rule file and CLAUDE.md.
        at = _tree(tmp / "at", rules={"a.md": "x" * 10}, ratchet=10 + session_hdr)
        check("exit/at-ratchet-0", _quiet(run, at), 0)
        over = _tree(tmp / "over", rules={"a.md": "x" * 10}, ratchet=9 + session_hdr)
        check("exit/one-over-ratchet-1", _quiet(run, over), 1)
        index = _tree(tmp / "index", block="\n@.claude/RULES-INDEX.md\n",
                      extra={".claude/RULES-INDEX.md": "x\n"})
        check("exit/rules-index-import-1", _quiet(run, index), 1)
        agents = _tree(tmp / "agents", block="\n@AGENTS.md\n", extra={"AGENTS.md": "x\n"})
        check("exit/agents-import-1", _quiet(run, agents), 1)

        # Fail-closed (exit 2).
        unreadable = _tree(tmp / "unreadable")
        os.symlink("missing-target.md", str(unreadable / RULES_REL / "broken.md"))
        check("exit/unreadable-rule-file-2", _quiet(run, unreadable), 2)
        badutf8 = _tree(tmp / "badutf8", rules={"a.md": b"ok \xff\xfe\n"})
        check("exit/invalid-utf8-rule-file-2", _quiet(run, badutf8), 2)
        unterminated = _tree(tmp / "unterminated", rules={"a.md": '---\npaths: ["**"]\nBody\n'})
        check("exit/unterminated-frontmatter-2", _quiet(run, unterminated), 2)
        dupkey = _tree(tmp / "dupkey", rules={"a.md": "---\nslug: a\nslug: b\n---\nBody\n"})
        check("exit/duplicate-frontmatter-key-2", _quiet(run, dupkey), 2)
        badbudget = _tree(tmp / "badbudget")
        (badbudget / BUDGET_REL).write_bytes(b"ratchet = true\nceiling = 90000\n")
        check("exit/malformed-budget-2", _quiet(run, badbudget), 2)
        nomarkers = _tree(tmp / "nomarkers")
        (nomarkers / CLAUDE_REL).write_bytes(b"# CLAUDE.md with no managed block\n")
        check("exit/missing-managed-block-markers-2", _quiet(run, nomarkers), 2)
        escape = _tree(tmp / "escape" / "repo", block="\n@../outside.md\n")
        (tmp / "escape" / "outside.md").write_bytes(b"outside\n")
        check("exit/import-escaping-repo-2", _quiet(run, escape), 2)

        # The module docstring states the model the code implements and its disclosed residuals.
        doc = sys.modules[__name__].__doc__ or ""
        check("doc/model-and-residuals-stated",
              [phrase for phrase in ("ENUMERATED GRAMMAR", "exits 2", "KNOWN OVER-COUNTS", "column 0",
                                     "counted rule file", "typed `paths:`", "brace alternation",
                                     "FRONTMATTER_KEYS", "code span", "byte order mark", "U+0085",
                                     "recursively through imported", "symlinked directory", "name@example.com",
                                     "ambiguous", "case-insensitively", "`..` component", "real directory",
                                     "PermissionError", "never read as missing", "path filter",
                                     "a banned name there exits 1", "not a proof", "its own body is not counted",
                                     "begins an import token", "Contents of", ".claude/CLAUDE.md",
                                     "RELATIVE to the repository root", "root prefix", "inside a word",
                                     "SCOPE grammar", "resolved path", "case variant", "preamble",
                                     "not the name the link gives it", "pinned build only", "8.3 short name",
                                     "later loader build", "right after an inline token",
                                     "differ only in case", "not a regular file",
                                     "an allowlist, not a list of typed forms", "YAML 1.2 core schema")
               if phrase not in doc]
              + [phrase for phrase in ("never missed", "never under", "OVER-COUNT BY CONSTRUCTION", "not dropped")
                 if phrase in doc], [])

        # The budget source's own comment describes PACK as the gate counts it (it is read for its comment
        # text only; no count is taken from the live corpus).
        budget_doc = (Path(__file__).resolve().parents[1] / BUDGET_REL).read_text(encoding="utf-8")
        budget_doc = " ".join(line.lstrip("# ") for line in budget_doc.split("\n") if line.startswith("#"))
        check("doc/budget-source-pack-description",
              [phrase for phrase in (".claude/CLAUDE.md", "conditional", "header", "relative to the repository",
                                     "whole-line HTML comment", "resolved path", "predates header counting")
               if phrase not in budget_doc]
              + [phrase for phrase in ("after frontmatter and HTML comments are removed",) if phrase in budget_doc],
              [])

        # Red on revert: each fix put back in a mutant copy of the production code must turn its case red.
        unstripped = _mutant(tmp, "return front, text[m.end():], ", "return front, text, ")
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
        # Red on revert for the enumerated grammar: each rule removed in a mutant turns its cases from 2 to 0.
        chars = [tmp / ("grammar-" + case) for case in ("nel-token", "bom-token", "nel-body", "fs-fence",
                                                       "nel-fence", "bom-block")] + [bom]
        # The closing-fence check also refuses a fence followed by U+0085, so it is removed with them.
        no_charset = _mutant(tmp, *(_off_pairs("_check_characters", "_check_ascii") + [
            '    if (front and not front.endswith("\\n")) or (m.end() < len(text) and text[m.end() - 1] != "\\n"):',
            "    if False:"]))
        check("revert/character-set-red",
              ([_quiet(run, r) for r in chars], [_quiet(no_charset.run, r) for r in chars]), ([2] * 7, [0] * 7))
        ascii_cases = [cafe, tmp / "typed-non-ascii"]
        no_ascii = _off(tmp, "_check_ascii")
        check("revert/ascii-token-and-frontmatter-red",
              ([_quiet(run, r) for r in ascii_cases], [_quiet(no_ascii.run, r) for r in ascii_cases]),
              ([2, 2], [0, 0]))
        scalar_cases = [tmp / "typed-nested-item", tmp / "typed-flow-map"]
        # The `paths:` allowlist (_plain_string) refuses the `[]` item too, so it is removed with the grammar.
        no_scalar = _off(tmp, "_scalar_ok", "_plain_string")
        check("revert/frontmatter-scalar-red",
              ([_quiet(run, r) for r in scalar_cases], [_quiet(no_scalar.run, r) for r in scalar_cases]),
              ([2, 2], [0, 0]))
        escape_case = tmp / "typed-escape-q"
        check("revert/quoted-escape-red",
              (_quiet(run, escape_case), _quiet(_off(tmp, "_escape_free").run, escape_case)), (2, 0))
        key_case = tmp / "typed-unknown-key"
        check("revert/frontmatter-key-red",
              (_quiet(run, key_case), _quiet(_off(tmp, "_known_key").run, key_case)), (2, 0))
        any_dir = _mutant(tmp, "    if stat.S_ISDIR(mode):\n", "    if stat.S_ISDIR(mode):\n        return None\n")
        check("revert/directory-import-red", (_quiet(run, folder), _quiet(any_dir.run, folder)), (2, 0))
        follow_link = ("    if stat.S_ISLNK(st.st_mode):\n",
                       "    if stat.S_ISLNK(st.st_mode):\n        return os.stat(path).st_mode\n")
        check("revert/symlinked-import-red",
              (_quiet(run, symimport), _quiet(_mutant(tmp, *follow_link).run, symimport)), (2, 0))
        lexical = _mutant(tmp, *(follow_link + ('    if ".." in parts:', "    if False:")
                                 + ("    if stat.S_ISDIR(mode):\n", "    if stat.S_ISDIR(mode):\n        return None\n")))
        check("revert/dotdot-import-red", (_quiet(run, dotdot), _quiet(lexical.run, dotdot)), (2, 0))
        root_base = _mutant(tmp, "claude_base = os.path.dirname(claude_path)",
                            "claude_base = real_root")
        check("revert/claude-md-real-base-red",
              (_measured(measure, linkclaude)["pack_imports"], _measured(root_base.measure, linkclaude)["pack_imports"]),
              (1000, 0))
        uncond_only = _mutant(tmp, "                origins.append(origin)  # its body is not counted; its "
                                   "imports load in every session\n", "")
        check("revert/conditional-file-imports-red",
              ([_measured(measure, r)["pack_imports"] for r in (condimp, condchain)],
               [_measured(uncond_only.measure, r)["pack_imports"] for r in (condimp, condchain)]),
              ([1000, 1007], [0, 0]))
        no_join = _off(tmp, "_check_comment_joins")
        check("revert/comment-join-red",
              ([_quiet(run, r) for r in joined], [_quiet(no_join.run, r) for r in joined]), ([2] * 8, [0] * 8))
        check("revert/comment-email-red",
              (_quiet(run, email_comment), _quiet(_off(tmp, "_import_start").run, email_comment)), (0, 2))
        no_dot = _mutant(tmp, "    dot = _dot_claude(root)\n", "    dot = []\n")
        check("revert/dot-claude-claude-md-red",
              (_measured(measure, dotclaude)["pack"], _measured(no_dot.measure, dotclaude)["pack"]),
              (2 + 50010 + 1000 + dot_hdr, 2 + _hdr(".claude/rules/a.md", "CLAUDE.md")))
        no_header = _mutant(tmp, "    return utf16_units(HEADER_FIXED) + utf16_units(rel)\n", "    return 0\n")
        check("revert/loaded-file-header-red",
              (_measured(measure, headed)["pack"], _measured(no_header.measure, headed)["pack"]),
              (2 + _hdr(".claude/rules/a.md", "CLAUDE.md"), 2))
        check("revert/imported-file-scope-grammar-red",
              ([_quiet(run, r) for r in scoped], [_quiet(_off(tmp, "_imported_scope").run, r) for r in scoped]),
              ([2, 2, 2], [0, 0, 0]))
        flows = [tmp / ("flow-" + case) for case in ("comma", "brace", "comma-src")]
        comma_split = _mutant(tmp, "_flow_split(inner)] if inner", 'inner.split(",")] if inner')
        check("revert/flow-quoted-comma-red",
              ([_quiet(run, r) for r in flows], [_quiet(comma_split.run, r) for r in flows]), ([0] * 3, [2] * 3))
        no_rebuild = _mutant(tmp, "(1 if rebuilt else 0)", "0")
        check("revert/rebuilt-newline-red",
              (_measured(measure, lexer)["rules"], _measured(no_rebuild.measure, lexer)["rules"]), (22, 21))
        link_named = _mutant(tmp, "headers += header_units(loaded_rel(path, real_root))",
                             "headers += header_units(where)")
        fixed_named = _mutant(tmp, "return max(header_units(rel), header_units(loaded_rel(root / rel, real_root)))",
                              "return header_units(rel)")
        growth = []
        for mutant, kind in ((link_named, "file"), (link_named, "dir"), (fixed_named, "claude"), (fixed_named, "dot")):
            growth.append(_measured(mutant.measure, tmp / ("hdr-" + kind + "-long"))["pack"]
                          - _measured(mutant.measure, tmp / ("hdr-" + kind + "-short"))["pack"])
        check("revert/symlinked-header-resolved-path-red",
              ([b - a for a, b in zip(short, long_)], growth), ([200] * 4, [0] * 4))
        # _resolve_checked's _case_collision is a second layer for some variants, so both are removed.
        no_case = _off(tmp, "_case_variants", "_case_collision")
        check("revert/case-variant-red",
              ([_quiet(run, r) for r in variants], [_quiet(no_case.run, r) for r in variants]), ([2] * 4, [0] * 4))
        exact_first = _mutant(tmp, "        if len(matches) > 1:\n",
                              "        matches.sort(key=lambda match: os.path.basename(match[0]) != part)\n"
                              "        if len(matches) > 1 and os.path.basename(matches[0][0]) != part:\n")
        check("revert/case-ambiguous-import-red",
              ([_quiet(run, r) for r in collisions], [_quiet(exact_first.run, r) for r in collisions]),
              ([2] * 3, [0] * 3))
        no_collision = _off(tmp, "_case_collision")
        check("revert/case-colliding-rule-entries-red",
              ([_quiet(run, r) for r in rule_collisions], [_quiet(no_collision.run, r) for r in rule_collisions]),
              ([2] * 3, [0] * 3))
        no_regular = _mutant(tmp, *(_off_pairs("_regular_rule") + [
            "            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):\n", "            if False:\n"]))
        check("revert/fifo-rule-file-red", (_quiet(run, fifo), _quiet(no_regular.run, fifo)), (2, 0))
        blocking = _mutant(tmp, 'os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)', "os.O_RDONLY")
        check("revert/fifo-read-never-blocks-red",
              (_blocks(read_text, fifo / RULES_REL / "a.md"), _blocks(blocking.read_text, fifo / RULES_REL / "a.md")),
              (False, True))
        no_short = _mutant(tmp, "    if kept and any(SHORT_NAME_RE.fullmatch(part) for part in parts):\n",
                           "    if False:\n")
        check("revert/windows-short-name-red", (_quiet(run, shortname), _quiet(no_short.run, shortname)), (2, 0))
        any_tilde = _mutant(tmp, "if kept and any(SHORT_NAME_RE.fullmatch(part)",
                            "if any(SHORT_NAME_RE.search(part)",
                            'SHORT_NAME_RE = re.compile(r"[^.]{1,6}~[1-9][0-9]{0,5}(?:\\.[^.]{1,3})?")',
                            'SHORT_NAME_RE = re.compile(r"~\\d", re.ASCII)')
        check("revert/tilde-prose-not-short-name-red",
              ([_quiet(run, r) for r in tilde_prose], [_quiet(any_tilde.run, r) for r in tilde_prose]),
              ([0, 0], [2, 2]))
        no_tilde_base = _mutant(tmp, 'SHORT_NAME_RE = re.compile(r"[^.]{1,6}~', 'SHORT_NAME_RE = re.compile(r"[^.~]{1,6}~')
        check("revert/short-name-tilde-in-base-red", (_quiet(run, tilde_base), _quiet(no_tilde_base.run, tilde_base)),
              (2, 0))
        every_at = _mutant(tmp, "    if kept and any(SHORT_NAME_RE", "    if any(SHORT_NAME_RE")
        check("revert/in-word-at-not-short-name-red",
              ([_quiet(run, r) for r in in_word], [_quiet(every_at.run, r) for r in in_word]), ([0, 0], [2, 2]))
        check("revert/git-prose-loader-filter-red",
              ([_quiet(run, r) for r in git_prose], [_quiet(every_at.run, r) for r in git_prose]),
              ([0, 0, 0], [2, 2, 2]))
        whitespace_only = _mutant(tmp, "resolve_candidate(path, base, here, run, not _loader_rejects(run))",
                                  "resolve_candidate(path, base, here, run, _import_start(source, pos))")
        check("revert/short-name-after-inline-token-red",
              ([_quiet(run, r) for r in after_inline], [_quiet(whitespace_only.run, r) for r in after_inline]),
              ([2] * 8, [0] * 8))
        final_only = _mutant(tmp, "        if len(found) > 1:\n",
                             "        if len(found) > 1 and not all(stat.S_ISLNK(mode) for _, mode in found):\n")
        check("revert/case-collision-on-symlink-hop-red",
              ([_quiet(run, r) for r in hop_collisions], [_quiet(final_only.run, r) for r in hop_collisions]),
              ([2] * 5, [0] * 5))
        check("revert/case-non-rule-entries-red", (_quiet(run, other_files), _quiet(_off(tmp, "_walked").run, other_files)),
              (0, 2))
        # A directory pair read as one spelling (the exact one, as a case-sensitive file system does) undercounts.
        one_spelling = _mutant(
            tmp, "        if len(matches) > 1:\n",
            "        if len(matches) > 1 and all(stat.S_ISDIR(found) for _, found in matches):\n"
            "            matches = [m for m in matches if os.path.basename(m[0]) == part] or matches[:1]\n"
            "        if len(matches) > 1:\n",
            "        if len(found) > 1:\n",
            "        if len(found) > 1 and all(stat.S_ISDIR(mode) for _, mode in found):\n"
            "            found = [f for f in found if os.path.basename(f[0]) == part] or found[:1]\n"
            "        if len(found) > 1:\n")
        check("revert/case-variant-directory-pair-red",
              ([_quiet(run, r) for r in dir_pairs], [_quiet(one_spelling.run, r) for r in dir_pairs]),
              ([2] * 11, [0] * 11))
        casefold_only = _mutant(tmp, 'return unicodedata.normalize("NFD", name).casefold(), name.upper()',
                                "return name.casefold(), name.casefold()")
        check("revert/case-collision-upper-and-nfd-folds-red",
              ([_quiet(run, r) for r in folds], [_quiet(casefold_only.run, r) for r in folds]), ([2] * 3, [0] * 3))
        open_first = _mutant(tmp, "    claude_path = _fixed_file(root, CLAUDE_REL)\n",
                             "    claude_path = (read_text(root / CLAUDE_REL, CLAUDE_REL), _fixed_file(root, CLAUDE_REL))[1]\n",
                             "    real = _fixed_file(root, DOT_CLAUDE_REL)\n",
                             "    real = (read_text(path, DOT_CLAUDE_REL), _fixed_file(root, DOT_CLAUDE_REL))[1]\n")
        check("revert/fixed-file-checked-before-open-red",
              ([_opened_fifo(run, r) for r in fixed_fifos], [_opened_fifo(open_first.run, r) for r in fixed_fifos]),
              ([(2, False)] * 4, [(2, True)] * 4))
        budget_first = _mutant(tmp, "read_text(_budget_file(root), BUDGET_REL)", "read_text(root / BUDGET_REL, BUDGET_REL)")
        check("revert/budget-checked-before-open-red",
              ([_opened_fifo(run, r) for r in budget_fifos], [_opened_fifo(budget_first.run, r) for r in budget_fifos]),
              ([(2, False)] * 2, [(2, True)] * 2))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
          "counted, a text holding a comment with no final newline counted one unit more, BMP and astral "
          "characters counted as 1 and 2 units, paths that normalize to ** counted and src/** excluded, "
          "out-of-grammar characters, frontmatter, import tokens, `..` components, symlinked import paths, "
          "directory and unreadable targets exit 2, typed or unreadable globs exit 2, every @ candidate "
          "followed whatever Markdown surrounds it, case-insensitive imports counted, a symlinked CLAUDE.md "
          "resolving imports from its target's directory, banned names exit 1 in any case or code span, a "
          "candidate naming no file skipped, home relative, escaping, undecodable, and doubly matched imports "
          "exit 2 as ambiguous, symlinked rule directories counted and escaping, looping, or doubled "
          "symlinks exit 2, cycles ending, SESSION over PACK, the ratchet boundary, unreadable, invalid "
          "UTF-8, malformed frontmatter, budget, and markers exit 2, the docstring stating the model, and "
          "every red-on-revert flip turning red, a conditional rule file's imports counted to any depth, an "
          "HTML comment beside an @ that begins an import exit 2 and an email address in a comment pass, "
          "quoted commas in a flow list read, .claude/CLAUDE.md and its imports counted, a modelled header "
          "counted per loaded file naming its resolved path, case variants of the fixed names exit 2, case "
          "ambiguity at any import component, at every symlink hop and in case-colliding rule entries exit 2 "
          "and two directories differing only in case exit 2 on every resolved path while non-rule entries pass, "
          "upper and NFD folds collide, `@~1`, `@{{u}}~1` and in-word `@` prose pass while a short name after an inline "
          "token exits 2 and a `~` in a short name's base exits 2, "
          "CLAUDE.md, .claude/CLAUDE.md and the budget source checked before they are opened, a non-regular rule "
          "file and a Windows 8.3 short name import exit 2 and no read blocking, an "
          "imported file's scope held to the grammar, and the budget source "
          "describing PACK); execution set "
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
    _selftest_exit_report.exit_with(main(sys.argv[1:]))
