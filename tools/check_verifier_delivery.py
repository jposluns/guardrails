#!/usr/bin/env python3
"""Verifier delivery check: a QA delivery counts toward the merge quorum only when its quotes are
verbatim in the reviewed tree and its clean verdict covers answered items.

  check_verifier_delivery.py --brief FILE --delivery FILE --repo DIR
  check_verifier_delivery.py                 (validates its own static tables; reads no input, runs no git)
  check_verifier_delivery.py --self-test
  check_verifier_delivery.py [--self-test] --execution-report ABS_PATH   (the execution-set gate's launch)

The standing rule this enforces: a verifier's clean verdict counts only after its specific claims are
confirmed at source, and a delivery carrying a quote that is not verbatim in the reviewed tree is a
FAILED delivery. A whole-tree search alone is not enough: a sentence quoted as text from one file can
exist, reworded or not, only in another.

The reviewed tree is the commit named by the brief's one BRIEF-PIN line (a full 40-hex SHA; two lines,
even identical ones, are CANNOT_EVALUATE). Every byte of reviewed content comes from that commit through
git -C REPO --no-replace-objects (cat-file -t, ls-tree, cat-file blob, cat-file --batch; each launch also
carries the gc.auto=0, gc.autoDetach=false, maintenance.auto=false and core.commitGraph=false pins),
never from the working tree, under an environment that drops every ambient GIT_ variable and sets
GIT_NO_REPLACE_OBJECTS, GIT_GRAFT_FILE (an empty file, so no info/grafts entry is read),
GIT_NO_LAZY_FETCH, GIT_TERMINAL_PROMPT=0 and GIT_OPTIONAL_LOCKS=0. Every blob read, by cat-file blob or
by the whole-tree cat-file --batch, is re-hashed (SHA-1 of its git blob header and bytes) against the id
the tree lists, so a rewritten object file is CANNOT_EVALUATE, never read as the pinned content. An
unreadable, oversized, non-regular or non-UTF-8 input file, a missing or ambiguous BRIEF-PIN, a brief
with no numbered question, an unresolvable SHA or one that names no commit, a delivery outside the
closed shape below, a construct that can hide text from rendered view, a line that reads as an answer
marker without being one, an unclosed fenced block, an indented code block, more than MAX_QUOTES quotes,
a blob that does not hash to its id and every git failure are CANNOT_EVALUATE (exit 2), never a pass.

THE CLOSED DELIVERY SHAPE. Answer and finding boundaries are never inferred from free text; the
question-list template fixes them:
  * Line 1 is exactly the pinned SHA (surrounding whitespace aside), or exactly the harness's status
    line "WORKER_STATUS: <WORD> (account=<v>, rc=<v>, family=<v>, model=<v>, effort=<v>)" (each <v> a
    run without whitespace, commas or parentheses) with the SHA line as line 2. A differing or
    malformed SHA line is CANNOT_EVALUATE.
  * Then, for each of the brief's m numbered questions in order, a line starting at column 0 with
    "QN." (N = 1 to m in ASCII digits, each once, ascending; the "QN." ends the line or is followed by
    a space or tab) opens the answer to question N; the answer runs to the next such line, the
    findings line or the VERDICT line, and may start on its "QN." line. Only blank lines come between
    the SHA line and Q1.
  * Optionally, after the last answer, one line exactly "## Findings" (trailing spaces or tabs aside)
    opens the findings section, which runs to the VERDICT line. No line in it opens an answer.
  * Then one VERDICT line ("VERDICT:", "**VERDICT:**" or "**VERDICT**:", any case, leading spaces or
    tabs allowed; its value is the rest of the line), then, after blank lines only, a line
    "QA-COMPLETE". After it come only blank lines and, optionally, as the delivery's last non-blank
    line, the harness's closing line exactly "WORKER_STATUS: COMPLETE".
  Anything else is CANNOT_EVALUATE naming the line: text before Q1., a missing, repeated or out-of-order
  "QN.", a "QN." past Q{m}., a "QN." inside the findings section, a "QN." inside a fenced block, a
  second findings line, a VERDICT or QA-COMPLETE line out of place (a line starting "Verdict:" inside an
  answer is the VERDICT line), text after QA-COMPLETE (a second harness line, or one that is not
  exact, included), and a delivery that ends early. Inside an answer or the findings section every
  other line (a numbered list, a heading, a grade word such as MAJOR, a line starting WORKER_STATUS) is
  content: it moves no boundary and its quotes and file references are checked like any other.
  Also CANNOT_EVALUATE naming the line, anywhere in the body:
  * A line that reads as an answer marker but is not one, read as a renderer shows it (CommonMark
    backslash escapes of ASCII punctuation and named, decimal and hex character references, decoded
    once, left to right: a numeric reference is its code point, U+FFFD standing in for zero,
    surrogates and values past U+10FFFF, and a named reference is only an exact HTML5 entity-table
    name with its semicolon, an unknown name staying literal; renderers differ on control code
    points, so a line is refused when any of three readings scans as a marker: that CommonMark
    reading; the legacy html.unescape reading of the whole line, which remaps 128..159 through
    Windows-1252, drops some control code points and reads semicolonless legacy names and numeric
    references; and the escape-aware legacy reading, escapes honoured with each tokenized
    reference decoded through html.unescape, so a dropped code point such as "&#65535;" beside an
    escaped stop reads as a marker: a stated union. The whole-line reading honours no backslash
    escape, so a backslash-escaped reference that reads as a marker under it is refused too, a
    disclosed over-refusal):
    past leading whitespace, emphasis, heading, quote, bullet and ordered-list
    marks ("1. ", "1) "), backslashes, the blank glyph U+2800 and ignorable characters (category Cf,
    the pinned Default_Ignorable_Code_Point table and the non-whitespace control characters of
    category Cc), interleaved in any order and consumed whole,
    a Q (or a lookalike letter), digits of any script and a full stop (or a lookalike), emphasis runs
    permitted inside the marker, compared after NFKC normalisation of the marker-shaped head, then
    whitespace, emphasis, U+2800 or the line's end; so "Q\uff11.", "Q\u0663.", a zero-width or
    variation-selector character inside "Q4.", "  Q4.", "- Q4.", "**Q4.**", "\u00a0***Q4.***",
    "Q4\\.", "&#81;4.", "Q4&period;", "Q*4*.", "**Q**4.", "1. Q4.", "\u2800Q4.", "&#133;Q4.",
    "Q4.&#31;", "&nbspQ4.", "Q4&#46 x", "&#129;Q4." and a literal U+0001 inside "Q4." are refused,
    in answers, findings, blockquotes and fenced blocks alike, however long the prefix runs.
  * A numeric character reference whose digit run is longer than the grammar bound of seven
    decimal or six hexadecimal digits: html.unescape converts a decimal digit run through int(),
    which raises past the interpreter conversion limit (sys.get_int_max_str_digits(), 4300
    digits), so such a line is refused by a bounded pre-scan of digit-run length before any decode
    runs, in body lines and fenced content alike; an over-long or undecodable reference is
    CANNOT_EVALUATE, never a crash and never a pass.
  * A line that reads as a VERDICT line or as the QA-COMPLETE line under its CommonMark rendering,
    its legacy html.unescape reading or the raw line with the non-whitespace Cc controls dropped,
    while its raw text is not that line: the canonical boundary is read from the raw text alone
    (that parse is unchanged), so an encoded or control-padded boundary ("VERDICT&#58; BLOCKERS
    FOUND", "QA&#45;COMPLETE", "QA&#45COMPLETE", a U+0001 inside either word), duplicated or not,
    anywhere in the body, is refused, never read as content.
  * A VERDICT value holding a whitespace separator other than space or tab (U+001C and U+0085
    included): str.split would fold it into a clean verdict, so the value is refused, never
    normalised.
  * A construct that can hide text from rendered view, outside fenced blocks, inline code spans and
    blockquote lines: raw HTML ("<" then "!", "?", or an optional "/" and a tag name ending at
    whitespace, "/", ">" or the line's end, which opens a comment, an HTML block or inline HTML; an
    autolink such as "<https://...>" is not one), a link reference definition marker "]:", an image
    "![", an inline link "](" whose destination or title may run past its line, and a character
    reference that renders as a double quote mark ("&quot;"). A fenced block opened four or more
    columns in may render as text, so its content is scanned too.

Rules:
  C1 coverage. A verdict of any kind (VERDICT: NO BLOCKERS, VERDICT: BLOCKERS FOUND or another) is
     reclassed NO_VOTE (exit 1, reason named) when no answer is answered: a verdict counts only for
     answered items, so a BLOCKERS verdict with coverage 0/m is no vote. An answer is UNVERIFIABLE when
     the word UNVERIFIABLE (upper case) appears in it outside fenced blocks and blockquotes. Otherwise it
     is answered when it carries a quote; an answer to a question that asks for a quote (its text holds
     the word quote, quotes, quoted or quoting) is answered only with a quote, so prose alone is "no
     quote", not answered. An answer to any other question is also answered by answer content: a word
     (a run of letters or digits) outside its "QN." marker and the file names it holds, where its words
     are not exactly the words of its question (a restated question). So "Q1. ", "Q1. README.md", the
     question copied back and "Q2. Confirmed." under a quote question are not answered, and findings
     text never answers the last question. One UNVERIFIABLE answer among answered ones is legitimate.
     Coverage n/m is reported: n answered answers, m the brief's numbered questions.
  C2 verbatim. Every fenced code block, every blockquote run and every inline double-quoted span must
     appear byte for byte (UTF-8) as a contiguous span of one file of the pinned tree (a tree file stored
     with CRLF line ends also matches the quote's lines joined with CRLF). A quote may start or end
     anywhere in a line, a fenced one included, so an exact excerpt passes, and so does a quote cut at
     a line edge ("TIMEOUT = 12" of "TIMEOUT = 120") or a fenced block whose first line drops its own
     indentation: real deliveries fence mid-sentence excerpts, and a line-aligned rule would fail them.
     Joining non-adjacent lines, dropping a line, re-wrapping, changing the indentation of any line
     after the first, adding indentation to the first, and adding an escape fail. Leading indentation
     inside a fenced block counts (only the opening fence's own indentation is removed from each line,
     as CommonMark does for a list item's fence). Where a quote must match:
       * an answer: in the files the answer names anywhere in its own text (its "QN." line and body,
         outside its quotes); when it names none, in the files its question names; only when neither
         names a file, anywhere in the tree.
       * the findings section: in the files it names anywhere in its text, outside its quotes;
         otherwise anywhere in the tree.
       * the VERDICT line: anywhere in the tree.
     A named path with a slash binds exactly; a bare file name (no slash) binds to every file of the
     pinned tree with that basename, root or not (a root README.md and .preview/README.md are both
     named by "README.md"), and the result's found_in names the file that matched. Names never widen:
     a file reference (as the grammar below defines it; a token that is none, such as a directory of the
     tree, binds nothing) that resolves to no blob of the pinned tree is held, not dropped, so a quote found
     in none of the present named files fails, naming the absent one, and when every name is absent
     every quote under it fails. An answer's own file reference that resolves to no blob fails the
     answer outright, quote or not (reason named), unless its question names the same reference (an
     answer may say that a file the question asks about is absent). A match only in another file fails,
     naming both. Any failing quote or answer fails the whole delivery (exit 1); the first MAX_LISTED
     failures list the other files holding the quote and the nearest whitespace-normalised match (a
     diagnostic only, never a pass). A whole-tree search stops at its first matching file.

Output: one JSON object on stdout (verdict PASS, FAIL, NO_VOTE or CANNOT_EVALUATE; reasons; per-answer
results with every quote, each answer's state and its absent file references; coverage) and a one-line
human summary on stderr. Exit 0 PASS; 1 FAIL or NO_VOTE (FAIL wins when both apply); 2 CANNOT_EVALUATE
or a usage error.

The grammar read (a declared subset of Markdown, not a general parser; every scanner is linear in its
line, with no backtracking pattern):
  * Lines are split at LF; one trailing CR per line is the delivery's transport, not quote content.
  * A fenced block opens on a line of three or more backticks or tildes after any number of spaces (a
    list item's continuation fence included; a backtick fence's info string holds no backtick), and
    closes on a line of the same character, at least as long, with nothing after it but spaces or
    tabs and exactly the opening fence's indentation before it; a line of that shape indented
    otherwise (a tab included) is CANNOT_EVALUATE, since whether it closes depends on list nesting.
    A fence marker after a list marker ("- ```") or a tab is CANNOT_EVALUATE for the same reason.
    Every fenced block with a non-blank line is a quote, whatever its info string: a command or output
    shown in a fence is held to the same rule as a quote. A line indented four or more columns where
    no paragraph is open (after a blank line, a fenced block, an ATX heading, a thematic break or a
    setext underline, or at the start of the body) is CANNOT_EVALUATE: it is an indented code block or
    a list continuation paragraph, depending on list nesting this grammar does not track.
  * A blockquote run is consecutive lines starting with ">" (at most three spaces before it); one
    space after the ">" is removed and the run's lines are joined with LF into one quote. The line
    after a run is blank: a non-blank line there (a fence or a "QN." line included) is CANNOT_EVALUATE,
    since CommonMark reads a paragraph line there into the quote (a lazy continuation).
  * An inline code span is a run of n backticks closed by the next run of exactly n on the same line.
    That per-line reading is exact only while every backtick run of the paragraph so far closes on its
    own line and no backtick is backslash-escaped; from a line that breaks this to the next blank or
    blockquote line, no code span is masked when hidden constructs are sought.
  * An inline quote is the text between a straight double quote and the next one on the same line,
    or between a left and a right curly double quote, outside inline code spans and outside a
    parenthetical example: "(e.g." or "(for example"
    up to and including the next ")" (an example word is not a quote of the tree). An example opener
    with no ")" after it masks nothing, and "(i.e." opens no example (a restatement is a claim). A
    straight double quote right after a digit (an inch mark) opens nothing. A quote that does not close
    on its line is not read, and an opener with no closer drops out of the scan without hiding a later
    complete quote of the other kind.
  * The brief's questions are its first run of column-0 numbered lines counting 1, 2, 3 and so on
    ("1.", "Q1:", "**2.**", "### 3)" and "Item 4." forms; after a line reading QUESTIONS: when the
    brief has one); a number out of sequence ends the run, so a later numbered list that restarts at 1
    is not a question. A question's text is its line plus the lines after it up to a blank line.
  * A file reference is a token (split at whitespace, quotes, apostrophes, backticks, brackets,
    parentheses, commas and semicolons; a "#" anchor and a ":" suffix cut, then asterisks at its ends
    and trailing dots, "!" or "?" removed), whatever other characters it holds, that is: a blob path of the pinned
    tree; a path with a slash whose last part carries a file extension (a dot, then ASCII letters and
    digits holding a letter) or whose first part is a directory of the tree; an absolute or
    home-relative path whose last part carries an extension (one under the --repo directory, as given
    or resolved, is read as its repository path); or a bare name (no slash) that is a root blob, the
    basename of a blob with an extension, or a name carrying an extension some blob of the tree
    carries. So "nonexistent/source+test.py", "docs/retired/ALPHA" and "alpha_old.md" are references
    (absent ones), while "and/or", "2/2", "e.g." and "PinnedTree.read" are not. A URL, a directory of
    the tree and a token holding a glob or placeholder character (* ? < > { } $) are not references.

LIMITS. An input file is read up to MAX_INPUT_BYTES (16 MiB); a delivery is parsed in time linear in its
size (the self-test runs, in a capped child each under RUNTIME_CAP_SECONDS, a 16 MiB backtick run after a
quote, a 16 MiB run of inch marks in the last answer, a 16 MiB whitespace run on the VERDICT line and a
16 MiB zero-width-space run, digit run and U+FDFA ligature run (the longest NFKC expansion, 18
characters; bare and after a leading Q) on marker-shaped lines; the 8a61dfb9 forms of the code-span
and VERDICT scanners exceed that cap on the backtick and whitespace
runs). At most MAX_QUOTES quotes are checked; each costs at most one pass over the tree's blobs, which
are read into memory (bounded at MAX_TREE_BYTES) only when a quote needs a tree-wide search or a failure
needs its diagnostic. The brief's question text is joined line by line, which is not linear in a very
long question.

DISCLOSED RESIDUAL. C2 proves a quote EXISTS in a file the answer, its question or the findings section
names at the pinned SHA, not that it supports the grade the verifier gave; a grade resting on an earlier
answer is not linked to that answer; runtime claims (that a name exists, or does not, at runtime) are out
of scope here (a later rule). A Gemini-family verifier's actual read path is undeclared, so a verbatim
quote proves the text is in the tree, not that the verifier read it there. False PASS: inline code
spans, single-quoted text and text in other quotation marks (guillemets, low-9 quotes) are not read as
quotes, so a fabricated quote presented only that way passes unseen; so is text in a parenthetical
example ("(e.g. "...")"), and a straight double quote right after a digit is an inch mark, so in
'line 4"text"' no quote is read; a double-quoted span that breaks across lines or does not close on
its line is not read; a quote cut at a line edge passes (above); a marker shaped as an inline code
span ("`Q4.`") or as the text of an inline link ("[Q4.](x)") at the start of a line is not read as a
marker candidate, its first source character being a backtick or a bracket (the code-span and
link-text marker residual: the scan models escapes, character references, emphasis, list marks and
blank glyphs, not full inline parsing); a default-ignorable, format or non-whitespace control (Cc)
character directly after the stop of the marker is dropped, so "Q4." then U+3164 (or U+0001) then
text is read with the text against the stop and passes, though some fonts show a gap or a box; of
the glyphs that render blank without being whitespace, format,
non-whitespace control or default-ignorable, only U+2800 is consumed; a token that is no file reference (a
directory of the tree, a path without an extension whose first part is no directory of the tree, a
URL, a glob or placeholder path) binds nothing, so an answer naming only such tokens binds like one
naming none, and with a question naming none its quotes match anywhere in the tree; an answer naming several files
accepts a match in any one of them, and the findings section is one unit, so a finding's quote may
match a file another finding names; a findings section naming no file is checked against the whole
tree; findings written after the last answer without the exact "## Findings" line (a
bulleted "- MAJOR:", "### Findings" or "Findings:") are read as text of the last answer, so their words
can answer a question that does not ask for a quote and their quotes can answer one that does (each
quote still held to that answer's files); an answer word need not answer the question (only an exact restatement is caught); a literal format or
default-ignorable character inside a would-be VERDICT or QA-COMPLETE word is not stripped by the
boundary refusal (only the non-whitespace Cc controls are dropped), so such a line stays content. False FAIL: a
double-quoted word used as a scare quote ("belt and braces"), not an example in "(e.g. ...)", is held to
the tree; a quote of the brief's own text is held to the tree like any other; an answer that names a
file absent from the tree its question does not name (a placeholder path without a placeholder
character, a file outside the repository, or a product name such as "Node.js" when some blob carries a
.js extension) fails. Fail closed: a delivery outside the closed shape (answers numbered "1." or
"**Q1.**", answers in a table, a leading blank line, a byte-order mark or a "REVIEWED: <sha>" line
before the SHA, a harness status line in another form) is CANNOT_EVALUATE, and so is a delivery whose
rendered view is safe but whose text this grammar cannot prove safe: a "<" then a letter in prose
("List<String>", "a<b c"), a "]:" in prose ("items[0]: x"), a line beginning with a question
reference in marker form ("- Q2. missed"), a fence right after a blockquote line, a closing fence
indented otherwise than its opening fence, a raw-HTML-shaped text inside a multi-line code span, and
a line whose legacy html.unescape reading (the whole line decoded, semicolonless references
included) or whose escape-aware legacy reading (escapes honoured, each tokenized reference
decoded through html.unescape) scans as a marker even where CommonMark renders it literal (the
whole-line reading refuses a backslash-escaped reference that reads as a marker under it, an
over-refusal this grammar accepts; the escape-aware reading is kept beside the whole-line
reading as a further refusal branch, a stated union, never a replacement);
the UNVERIFIABLE and quote-question tests are word tests, so a stray
mention moves an answer toward NO_VOTE, never toward a pass. Object reads: commit and tree objects are
not re-hashed (only blobs are), so a rewritten loose tree or commit object could list other existing
blob ids; a shallow file cuts only parent lists, which no read here uses. git is resolved through PATH,
a trusted-toolchain concern; a repository in SHA-256 object format never resolves a 40-hex pin and so
cannot be evaluated.

SELF-TEST COVERAGE. Each rule has a fixture that changes result when the rule is removed; a flip/ check
patches a rule out in the suite and shows its fixture's result change for C1 (no answered item, answer
content, a quote question needs a quote, findings evidence is no answer), C2 (verbatim, inline, fenced,
list-item fences, blockquotes, the answer's names, absent references in an answer and in a question,
the findings section and its names, quoted text naming nothing, bare-name binding, absolute repository
paths, excerpts, indentation, examples and their closing ")", the example openers, CRLF tree files),
the closed shape (a heading inside a fence, the fence-close length and indentation, the harness's
first and last lines, a WORKER_STATUS-prefixed line read as content), hidden text (raw HTML, inline
code spans read across lines, the content of a fence indented four or more columns, link reference
definitions, images, link titles that run on, quote-mark character references), answer-marker
lookalikes, lazy blockquote continuation, indented code after a fence or a heading, a fence after a
list marker, the question marker, pin matching, repeated pins, commit-only pins, strict decoding, the numeric decode
bounds, the over-long reference pre-scan (live through the CLI, in a fence included), the encoded,
control-padded and semicolonless boundary refusal, the escape-aware legacy branch, the
verdict-value separator refusal, the missing-Cc-clause static guard, CRLF
transport, indented code blocks, ambient GIT_ variables, replace refs, blob re-hashing on both read
paths, the pinned tree and both runtime scanners; a recording seam also shows the NFKC input for the
two U+FDFA runtime fixtures is the bounded head only (whole-remainder normalisation fails those two
checks on any host, however fast). These have a fixture but no flip/ check, since they
sit inside the parser's loop or another guard also refuses the same input: each closed-shape refusal
(missing, repeated, out-of-order and extra answers, text before Q1., a heading in the findings
section, a second findings line, VERDICT and QA-COMPLETE placement, a "Verdict:" line inside an
answer, a second harness status line, a closing harness line that is not the last), raw HTML tags, a
fence after a tab and a closing fence after a tab, the other marker forms (non-ASCII digits, a
zero-width or default-ignorable character, indentation, bold, rendered backslash escapes and
character references (semicolon-terminated and the legacy semicolonless forms alike), ordered-list
marks, blank glyphs, whitespace and non-whitespace control characters (literal and decoded), decoration
and format runs of any length before the marker, emphasis and format runs inside it, a lookalike in
a fence), a "QN." line right after a quote,
the exemption of an absent reference its question names, the unmatched-opener rule of the inline
scanner, the non-regular-file check and
O_NONBLOCK (a FIFO or directory also yields an empty or unopenable input), git exit status (the type
and listing checks also refuse), the --batch truncation check (blob re-hashing also refuses), the
backtick info-string rule and the stop of the question run. The --no-replace-objects option and
GIT_NO_REPLACE_OBJECTS each back the other, so the replace-ref flip removes both. The gc, maintenance
and core.commitGraph pins and GIT_GRAFT_FILE have no fixture (no fixture builds a tampered
commit-graph; grafts change only parent lists); the repository's maintenance-pin scan enforces the gc
and maintenance pins on every git launch.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_verifier_delivery.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import html
import html.entities
import json
import os
import re
import stat
import subprocess
import tempfile
import unicodedata
import zlib
from pathlib import Path

TIMEOUT = 120
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_TREE_BYTES = 512 * 1024 * 1024
MAX_LISTED = 10
MAX_QUOTES = 500
SHA_RE = re.compile(r"[0-9a-f]{40}")
PIN_LINE_RE = re.compile(r"BRIEF-PIN:(.*)")
STATUS_PREFIX = "WORKER_STATUS"
HARNESS_FIRST_RE = re.compile(r"WORKER_STATUS: [A-Z][A-Z_]*+ \(account=[^\s,()]++, rc=[^\s,()]++, family=[^\s,()]++, "
                              r"model=[^\s,()]++, effort=[^\s,()]++\)")
HARNESS_LAST_LINE = "WORKER_STATUS: COMPLETE"
CLEAN_VERDICT = "NO BLOCKERS"
ANSWER_HEAD_RE = re.compile(r"Q([0-9]+)\.(?=[ \t]|$)")
# U+2800 BRAILLE PATTERN BLANK is no space and no format character, yet renders as a blank glyph.
MARKER_DECORATION = " \t*_#>+-\\\u2800"
# An ordered-list mark a renderer reads at the start of a list item: one to nine digits, "." or ")",
# then a space or tab (ten digits open no list).
LIST_MARK_RE = re.compile(r"[0-9]{1,9}[.)][ \t]")
MARKER_RE = re.compile(r"[\s*_#>+\u2800\\-]*+[Qq\u024a\u024b\u051a\u051b](?:[*_]*+\d++)++[*_]*+"
                       r"[.\u06d4\u3002\uff61](?=[\s*_\u2800]|$)")
# Default_Ignorable_Code_Point (Unicode 16.0.0 DerivedCoreProperties.txt): code points a renderer may
# show as nothing. unicodedata exposes no query for the property, so the ranges are pinned here and
# validated exactly (_static_config_problems): DEFAULT_IGNORABLE_COUNT code points in all, and the
# canonical range list ("00AD..00AD,..." in uppercase, four or more hex digits each) hashes to
# DEFAULT_IGNORABLE_SHA256; the marker scan drops these and category Cf alike (_marker_ignorable).
DEFAULT_IGNORABLE_RANGES = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
                            (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
                            (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
                            (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
                            (0xE0000, 0xE0FFF))
DEFAULT_IGNORABLE_COUNT = 4174
DEFAULT_IGNORABLE_SHA256 = "d1d78a34fea3a0f9cc46bbab9acf2686a8493e4cadf7c1f412a86f250e9b3624"
DEFAULT_IGNORABLE = frozenset(point for low, high in DEFAULT_IGNORABLE_RANGES for point in range(low, high + 1))
FINDINGS_HEADING = "## Findings"
COMPLETE_LINE = "QA-COMPLETE"
QUESTION_RE = re.compile(r"(#{1,6}[ \t]+)?(?:\*\*)?(?:Q|Item[ \t]+|Question[ \t]+)?(\d{1,3})[.):](?:\*\*)?(?=[ \t]|$)",
                         re.I)
QUESTIONS_MARKER_RE = re.compile(r"QUESTIONS:?", re.I)
QUOTE_WORD_RE = re.compile(r"\bquot(?:e|es|ed|ing)\b", re.I)
UNVERIFIABLE_RE = re.compile(r"\bUNVERIFIABLE\b")
FENCE_RE = re.compile(r"( *)(`{3,}|~{3,})(.*)$")
FENCE_CLOSE_RE = re.compile(r" *(`{3,}|~{3,})[ \t]*")
FENCE_SHAPE_RE = re.compile(r"[ \t]*+(?:(?:[-+*]|[0-9]{1,9}[.)])[ \t]++)*+(`{3,}+|~{3,}+)(.*)")
BLOCK_END_RE = re.compile(r" {0,3}(?:#{1,6}(?:[ \t]|$)|(?:(?:\*[ \t]*+){3,}|(?:_[ \t]*+){3,}|(?:-[ \t]*+)++|=++[ \t]*+)$)")
BLOCKQUOTE_RE = re.compile(r" {0,3}>[ ]?(.*)$")
HTML_START_RE = re.compile(r"<(?:[!?]|/?[A-Za-z][A-Za-z0-9-]*+(?=[\s/>]|$))")
LINK_DEFINITION_RE = re.compile(r"\]:")
IMAGE_RE = re.compile(r"!\[")
ENTITY_RE = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
BACKTICK_RUN_RE = re.compile(r"`+")
EXAMPLE_OPEN_RE = re.compile(r"\((?:e\.g\.|for example)", re.I)
INLINE_QUOTE_PAIRS = (('"', '"'), ("\u201c", "\u201d"))
PATH_TOKEN_RE = re.compile(r"[^\s`'\"()\[\],;\u2018\u2019\u201c\u201d]+")
TEMPLATE_CHARS = frozenset("*?<>{}$")
WORD_RE = re.compile(r"[^\W_]+")
WHOLE_TREE = "<pinned tree>"


class CannotEvaluate(RuntimeError):
    """An input, the pinned commit, or a git result could not answer the question."""


# ---------------------------------------------------------------------------------------------------
# The pinned tree
# ---------------------------------------------------------------------------------------------------

def _git_env():
    """Drop every ambient GIT_ variable (a GIT_DIR, GIT_INDEX_FILE or GIT_OBJECT_DIRECTORY would pick
    another repository's objects) and pin an offline, non-interactive, replace-free and graft-free
    posture (GIT_GRAFT_FILE names an empty file, so no info/grafts entry is read)."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_GRAFT_FILE"] = os.devnull
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _git(repo, *args, stdin=None):
    """git's stdout bytes for one read of the object database; any failure is CannotEvaluate."""
    try:
        result = subprocess.run(["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                                 "-c", "core.commitGraph=false", "-C", str(repo), "--no-replace-objects", *args],
                                input=stdin,
                                capture_output=True, timeout=TIMEOUT, env=_git_env(), check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CannotEvaluate("git {} could not run: {}".format(args[0], exc))
    if result.returncode != 0:
        raise CannotEvaluate("git {} exited {}: {}".format(
            " ".join(args[:2]), result.returncode,
            result.stderr.decode("utf-8", "replace").strip()[:300] or "no message"))
    return result.stdout


class PinnedTree:
    """The blobs of one commit's tree, read from the object database only."""

    def __init__(self, repo, sha):
        self.repo = repo
        self.sha = sha
        self.roots = sorted({str(Path(repo)).rstrip("/"), os.path.realpath(repo).rstrip("/")})
        _require_commit(sha, _git(repo, "cat-file", "-t", sha).strip())
        listing = _git(repo, "ls-tree", "-r", "-t", "-l", "-z", "--full-tree", sha)
        self.blobs = {}
        self.dirs = set()
        self.by_basename = {}
        self.extensions = set()
        self.undotted = set()
        self._content = {}
        for entry in listing.split(b"\0"):
            if not entry:
                continue
            meta, sep, raw_path = entry.partition(b"\t")
            fields = meta.split()
            if not sep or len(fields) != 4 or not raw_path:
                raise CannotEvaluate("git ls-tree returned an unparseable entry")
            path = raw_path.decode("utf-8", "surrogateescape")
            if fields[1] == b"blob":
                try:
                    size = int(fields[3])
                except ValueError:
                    raise CannotEvaluate("git ls-tree returned a non-numeric size for {}".format(path))
                self.blobs[path] = (fields[2].decode("ascii"), size)
                basename = path.rsplit("/", 1)[-1]
                self.by_basename.setdefault(basename, []).append(path)
                if _extension(basename):
                    self.extensions.add(_extension(basename).casefold())
                if "/" not in path and "." not in path:
                    self.undotted.add(path)
            elif fields[1] == b"tree":
                self.dirs.add(path)

    def paths(self):
        return sorted(self.blobs)

    def read(self, path):
        if path not in self._content:
            self._content[path] = _verified_blob(path, self.blobs[path][0],
                                                 _git(self.repo, "cat-file", "blob", self.blobs[path][0]))
        return self._content[path]

    def load_all(self):
        """Read every blob not yet read, through one cat-file --batch, within MAX_TREE_BYTES."""
        missing = [path for path in self.paths() if path not in self._content]
        if not missing:
            return
        if sum(size for _, size in self.blobs.values()) > MAX_TREE_BYTES:
            raise CannotEvaluate("pinned tree exceeds {} bytes; a tree-wide search is not attempted"
                                 .format(MAX_TREE_BYTES))
        oids = [self.blobs[path][0] for path in missing]
        data = _git(self.repo, "cat-file", "--batch", stdin=("\n".join(oids) + "\n").encode("ascii"))
        pos = 0
        for path, oid in zip(missing, oids):
            end = data.find(b"\n", pos)
            header = data[pos:end].split() if end >= 0 else []
            if len(header) != 3 or header[0].decode("ascii", "replace") != oid or header[1] != b"blob":
                raise CannotEvaluate("git cat-file --batch did not return blob {}".format(oid))
            size = int(header[2])
            content = data[end + 1:end + 1 + size]
            if len(content) != size or data[end + 1 + size:end + 2 + size] != b"\n":
                raise CannotEvaluate("git cat-file --batch returned a truncated blob {}".format(oid))
            self._content[path] = _verified_blob(path, oid, content)
            pos = end + 2 + size


def _require_commit(sha, kind):
    """The pin must name a commit: a tree or blob id would be read without its commit's provenance."""
    if kind != b"commit":
        raise CannotEvaluate("pinned SHA {} is a {}, not a commit".format(sha, kind.decode("utf-8", "replace")))


def _verified_blob(path, oid, content):
    """content, once it hashes to oid as a git blob; git does not re-hash an object it reads, so a
    rewritten object file would otherwise be read as the pinned content."""
    if hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest() != oid:
        raise CannotEvaluate("blob {} read for {} does not hash to its id; the object database is "
                             "inconsistent".format(oid, path))
    return content


def _blob_bytes(tree, path):
    return tree.read(path)


# ---------------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------------

def _read_input(path, label):
    """The bytes of one regular input file, opened non-blocking (a FIFO never stalls the check)."""
    try:
        with open(path, "rb", opener=lambda name, flags: os.open(name, flags | os.O_NONBLOCK)) as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise CannotEvaluate("{} {} is not a regular file".format(label, path))
            raw = handle.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise CannotEvaluate("{} {} unreadable: {}".format(label, path, exc))
    if len(raw) > MAX_INPUT_BYTES:
        raise CannotEvaluate("{} {} exceeds {} bytes".format(label, path, MAX_INPUT_BYTES))
    return raw


def _decode(raw, label):
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("{} is not valid UTF-8: {}".format(label, exc))


def _lines(text):
    """(line number, line) pairs split at LF, one trailing CR removed from each line."""
    return [(number, line[:-1] if line.endswith("\r") else line)
            for number, line in enumerate(text.split("\n"), 1)]


def _brief_pin(text):
    pins = [match.group(1).strip() for _, line in _lines(text) if (match := PIN_LINE_RE.match(line))]
    if len(pins) != 1:
        raise CannotEvaluate("the brief carries {} BRIEF-PIN lines; exactly one is required (a repeated "
                             "identical line is refused too)".format(len(pins)))
    pin = pins.pop()
    if not SHA_RE.fullmatch(pin):
        raise CannotEvaluate("the brief's BRIEF-PIN {!r} is not a full 40-hex SHA".format(pin[:80]))
    return pin


def _pin_matches(delivered, pin):
    return delivered == pin


def _delivery_body(text, pin):
    """The delivery's lines after its SHA line, once that line is proven to be the pin. The SHA line is
    line 1, or line 2 when line 1 is exactly the harness's status line (HARNESS_FIRST_RE); no other
    line is skipped."""
    lines = _lines(text)
    index = 1 if HARNESS_FIRST_RE.fullmatch(lines[0][1]) else 0
    if index >= len(lines):
        raise CannotEvaluate("the delivery has no SHA line")
    number, line = lines[index]
    candidate = line.strip()
    if not SHA_RE.fullmatch(candidate):
        raise CannotEvaluate("delivery line {} is not a full 40-hex SHA (the SHA line is line 1, or line 2 after "
                             "the harness status line \"WORKER_STATUS: <word> (account=..., rc=..., family=..., "
                             "model=..., effort=...)\")".format(number))
    if not _pin_matches(candidate, pin):
        raise CannotEvaluate("delivery SHA {} differs from BRIEF-PIN {}".format(candidate, pin))
    return lines[index + 1:]


# ---------------------------------------------------------------------------------------------------
# The declared Markdown subset
# ---------------------------------------------------------------------------------------------------

def _fence_content_line(line, indent):
    """A content line with up to `indent` leading spaces (the opening fence's own) removed."""
    count = 0
    while count < indent and count < len(line) and line[count] == " ":
        count += 1
    return line[count:]


def _segments(lines, strict=False):
    """("line", number, text, 0) for each line outside a fence and ("fence", number, content lines, the
    opening fence's indentation) for each fenced block; an unclosed fence is CannotEvaluate. When
    strict (a delivery), a line inside a fence shaped as its closing fence but indented otherwise than
    the opening fence is CannotEvaluate: whether it closes depends on list nesting."""
    out = []
    fence = None
    for number, line in lines:
        if fence is None:
            match = FENCE_RE.match(line)
            if match and not (match.group(2)[0] == "`" and "`" in match.group(3)):
                fence = (number, len(match.group(1)), match.group(2), [])
                continue
            out.append(("line", number, line, 0))
            continue
        start, indent, marker, content = fence
        stripped = line.lstrip(" \t")
        close = FENCE_CLOSE_RE.fullmatch(stripped if strict else line)
        if close and _fence_closes(close, marker):
            if strict and not _close_indent_matches(line[:len(line) - len(stripped)], indent):
                raise CannotEvaluate("delivery line {}: a closing fence indented otherwise than the fence opened at "
                                     "line {} (its reading depends on list nesting; indent both alike)"
                                     .format(number, start))
            out.append(("fence", start, content, indent))
            fence = None
            continue
        content.append(_fence_content_line(line, indent))
    if fence is not None:
        raise CannotEvaluate("unclosed fenced block opened at line {}".format(fence[0]))
    return out


def _close_indent_matches(leading, indent):
    """Whether a closing fence's leading whitespace is exactly its opening fence's indentation."""
    return leading == " " * indent


def _fence_closes(close, marker):
    """Whether a closing-fence line closes the fence its marker opened: the same character, at least
    as long (a shorter run is fence content, so a four-backtick fence is not closed by three)."""
    return close.group(1)[0] == marker[0] and len(close.group(1)) >= len(marker)


def _code_spans(line):
    """(start, end) of each inline code span of one line: a run of n backticks closed by the next run
    of exactly n (CommonMark). One linear pass over the runs; no backtracking pattern."""
    runs = [(match.start(), match.end()) for match in BACKTICK_RUN_RE.finditer(line)]
    following, last = [None] * len(runs), {}
    for index in range(len(runs) - 1, -1, -1):
        width = runs[index][1] - runs[index][0]
        following[index] = last.get(width)
        last[width] = index
    spans, index = [], 0
    while index < len(runs):
        closer = following[index]
        if closer is None:
            index += 1
            continue
        spans.append((runs[index][0], runs[closer][1]))
        index = closer + 1
    return spans


def _example_spans(line):
    """(start, end) of each parenthetical example of one line: "(e.g." or "(for example" up to and
    including the next ")". An opener with no ")" after it masks nothing, and neither can any later
    one, so the scan stops there. Linear: each search starts past the last span."""
    spans, at = [], 0
    while (match := EXAMPLE_OPEN_RE.search(line, at)) is not None:
        end = line.find(")", match.end())
        if end < 0:
            break
        spans.append((match.start(), end + 1))
        at = end + 1
    return spans


def _inline_quotes(line, spans=None):
    """The double-quoted texts of one line (see _inline_quote_spans), from spans when given."""
    return [line[start + 1:end - 1] for start, end in (_inline_quote_spans(line) if spans is None else spans)
            if line[start + 1:end - 1].strip()]


def _blanked(line, masks):
    """line with each (start, end) span of masks (they may overlap) replaced by as many NUL characters."""
    if not masks:
        return line
    pieces, at = [], 0
    for start, end in sorted(masks):
        if end <= at:
            continue
        start = max(start, at)
        pieces.append(line[at:start])
        pieces.append("\0" * (end - start))
        at = end
    pieces.append(line[at:])
    return "".join(pieces)


def _code_masks(line, carried):
    """(the inline code spans of line that are code in rendered view, whether the rest of its
    paragraph must be read with no code span masked). The per-line pairing of _code_spans is the
    paragraph's own only while every backtick run of the paragraph so far closes on its line and no
    backtick is backslash-escaped; from a line that breaks this to the paragraph's end (a blank line or
    a blockquote line), nothing is masked, so more text is read as raw (fail closed)."""
    if "`" not in line:
        return [], carried
    if carried or "\\`" in line:
        return [], True
    spans = _code_spans(line)
    inside = 0
    for match in BACKTICK_RUN_RE.finditer(line):
        while inside < len(spans) and spans[inside][1] <= match.start():
            inside += 1
        if inside == len(spans) or match.start() < spans[inside][0]:
            return spans, True
    return spans, False


def _link_runs_on(line):
    """Whether an inline link opened on this line ("](") may carry its destination or title past the
    line's end, where a title spanning lines is hidden from rendered view. Fail closed: a destination
    reaching the line's end or opening with "<" and a backslash in a destination or title count as
    running on. Linear: each search moves forward, and a title's closer is searched for again only
    once the scan has passed it."""
    at = line.find("](")
    end, following, closed = len(line), {}, {}

    def next_of(char, start):
        found = following.get(char, -2)
        if found == -2 or 0 <= found < start:
            found = following[char] = line.find(char, start)
        return found

    while at >= 0:
        pos, depth = at + 2, 0
        if pos < end and line[pos] == "<":
            return True
        while pos < end and line[pos] not in " \t":
            char = line[pos]
            if char == "\\":
                return True
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            pos += 1
        if pos >= end:
            return True
        if line[pos] == ")":
            at = line.find("](", pos + 1)
            continue
        while pos < end and line[pos] in " \t":
            pos += 1
        if pos >= end:
            return True
        closer = {'"': '"', "'": "'", "(": ")"}.get(line[pos])
        if closer is None:
            at = line.find("](", pos)
            continue
        close = next_of(closer, pos + 1)
        if close < 0:
            return True
        if 0 <= next_of("\\", pos + 1) < close:
            return True
        if close not in closed:
            rest = close + 1
            while rest < end and line[rest] in " \t":
                rest += 1
            closed[close] = rest < end and line[rest] == ")"
        # A title closed by ")" ends the link; otherwise it is no link, and a "](" inside it is read.
        at = line.find("](", close + 1 if closed[close] else pos + 1)
    return False


QUOTE_MARKS = frozenset('"\u201c\u201d')


def _hidden_construct(masked):
    """What in one line (its code spans blanked) can hide text from rendered view or render a double
    quote mark the inline scanner does not read, or None: raw HTML (a comment, tag, declaration or
    processing instruction, which can open an HTML block or an inline comment running over later
    lines), a link reference definition marker "]:" (a definition and its title, which may span
    lines, are not rendered), an image "![" (its alt text is not shown), an inline link that may run
    past its line, and a character reference that renders as a double quote mark."""
    match = HTML_START_RE.search(masked)
    if match:
        return "raw HTML {!r}".format(masked[match.start():match.start() + 12])
    if LINK_DEFINITION_RE.search(masked):
        return 'a link reference definition marker "]:"'
    if IMAGE_RE.search(masked):
        return 'an image "!["'
    if _link_runs_on(masked):
        return "an inline link whose destination or title may run past its line"
    for match in ENTITY_RE.finditer(masked):
        if html.unescape(match.group(0)) in QUOTE_MARKS:
            return "the character reference {} (it renders as a quote mark)".format(match.group(0))
    return None


def _marker_ignorable(char):
    """A character the marker scan drops before reading: category Cf, a Default_Ignorable_Code_Point
    (DEFAULT_IGNORABLE; the variation selectors and the combining grapheme joiner included), or a
    control character (category Cc) that is not whitespace (NUL, U+0001, DEL, U+0081: renderers show
    these as nothing, a box or a replacement glyph, never as marker text; the whitespace controls,
    tab to carriage return, the information separators and U+0085, stay whitespace to the scan).
    Most are rendered as nothing on their own; a few Cf characters (U+0600 ARABIC NUMBER SIGN,
    U+FFF9) render visibly, so dropping any of these errs toward refusing a line, never toward
    passing one."""
    category = unicodedata.category(char)
    return category == "Cf" or ord(char) in DEFAULT_IGNORABLE or (category == "Cc" and not char.isspace())


# CommonMark's inline reading the marker scan models: a backslash escape of ASCII punctuation, or a
# character reference (named, decimal or hex, semicolon-terminated; ENTITY_RE's grammar).
MARKER_SOURCE_RE = re.compile(r"\\([!-/:-@\[-`{-~])"
                              r"|&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
_NUMERIC_REFERENCE_RE = re.compile(r"&#(?:([0-9]{1,7})|[xX]([0-9A-Fa-f]{1,6}));")
# The bounded pre-scan of digit-run length: a numeric reference run past the grammar bound (seven
# decimal or six hexadecimal digits). html.unescape tokenizes runs of any length and converts a
# decimal run through int(), which raises past the interpreter conversion limit
# (sys.get_int_max_str_digits(), 4300 digits), so an over-long run is refused before any decode
# runs, never decoded; the conversion limit is never raised.
_OVERLONG_REFERENCE_RE = re.compile(r"&#(?:[0-9]{8,}|[xX][0-9A-Fa-f]{7,})")
# Characters whose NFKC forms a marker match can consume up to its one-character lookahead:
# MARKER_DECORATION, the Q forms and the stops; whitespace and digits of any script are tested per
# character (_marker_material), so fullwidth and mathematical forms stay in.
_MARKER_ALPHABET = frozenset(MARKER_DECORATION + "Qq\u024a\u024b\u051a\u051b.\u06d4\u3002\uff61")
_ASCII_MATERIAL = frozenset(char for char in map(chr, range(128))
                            if char.isspace() or char.isdigit() or char in _MARKER_ALPHABET)
# The head loop of _marker_scan drops these on its ASCII path, as _marker_ignorable does: the
# non-whitespace C0 controls and DEL.
_ASCII_IGNORABLE = frozenset(filter(_marker_ignorable, map(chr, range(128))))
_MATERIAL_CACHE = {}
# The seam _marker_scan normalises the head through, so the self-test can record its NFKC input.
_marker_normalize = unicodedata.normalize


def _reference_commonmark(reference):
    """reference (one MARKER_SOURCE_RE character reference) as CommonMark decodes it: a numeric
    reference is its code point by value, U+FFFD standing in for zero, surrogates and values past
    U+10FFFF; a named reference is looked up, semicolon included, in the HTML5 entity table, and an
    unknown name ("&notit;", "&ampx;") stays literal. html.unescape differs on both: it remaps
    128..159 through Windows-1252, drops some control code points and matches legacy names without
    their semicolon; _marker_candidate scans that reading of the whole line too."""
    match = _NUMERIC_REFERENCE_RE.fullmatch(reference)
    if match is None:
        return html.entities.html5.get(reference[1:], reference)
    point = int(match.group(1)) if match.group(1) is not None else int(match.group(2), 16)
    if point == 0 or 0xD800 <= point <= 0xDFFF or point > 0x10FFFF:
        return "\ufffd"
    return chr(point)


def _undecodable_reference(line):
    """The first numeric character reference of line whose digit run is longer than the grammar
    bound (_OVERLONG_REFERENCE_RE), shortened for the refusal message, or None. Decided BEFORE
    html.unescape is called anywhere on the line: the refusal never rests on catching a decode
    crash."""
    if "&#" not in line:
        return None
    match = _OVERLONG_REFERENCE_RE.search(line)
    if match is None:
        return None
    found = match.group(0)
    return found if len(found) <= 16 else found[:16] + "..."


def _undecodable_refusal(found):
    """The refusal reason for a reference _undecodable_reference found, named once for the line
    and fenced-content pre-scans alike."""
    return ("a numeric character reference whose digit run is longer than the grammar bound of"
            " seven decimal or six hexadecimal digits ({}): html.unescape converts a decimal"
            " digit run through int(), which raises past the interpreter conversion limit, so"
            " the line is refused by this bounded pre-scan before any decode, never decoded and"
            " never a crash".format(found))


def _marker_rendered(line):
    """line as a CommonMark renderer shows it to the marker scan: backslash escapes and character
    references (MARKER_SOURCE_RE) decoded once, left to right, through _reference_commonmark;
    decoded text is never rescanned, so a backslash-escaped reference stays literal text. An
    unknown entity name stays literal only under this CommonMark reading; the legacy html.unescape
    reading (_marker_candidate decodes the whole line with it) matches known entity prefixes and
    semicolonless forms instead."""
    if "\\" not in line and "&" not in line:
        return line
    pieces, at = [], 0
    for match in MARKER_SOURCE_RE.finditer(line):
        pieces.append(line[at:match.start()])
        pieces.append(match.group(1) or _reference_commonmark(match.group(0)))
        at = match.end()
    pieces.append(line[at:])
    return "".join(pieces)


# The escape-aware legacy decode, cached per token: one 16 MiB line can repeat a reference
# millions of times, and the cache keeps that at one html.unescape call per distinct token.
_LEGACY_TOKEN_CACHE = {}


def _legacy_reference(reference):
    """reference (one MARKER_SOURCE_RE character reference) as html.unescape decodes it whole:
    128..159 remapped through Windows-1252, the code points the HTML standard holds invalid
    (U+FFFF included) dropped, an unknown name read by its longest known prefix. Every token here
    is inside the grammar bound (at most seven decimal or six hexadecimal digits), so this decode
    never raises."""
    try:
        return _LEGACY_TOKEN_CACHE[reference]
    except KeyError:
        decoded = html.unescape(reference)
        if len(_LEGACY_TOKEN_CACHE) < 4096:
            _LEGACY_TOKEN_CACHE[reference] = decoded
        return decoded


def _marker_escape_aware(line):
    """The round-9 escape-aware legacy reading: backslash escapes decoded as _marker_rendered
    decodes them, and each tokenized character reference (MARKER_SOURCE_RE) decoded through
    html.unescape (_legacy_reference), so "Q&#65535;4\\. x", "Q4\\.&#65535; x" and
    "Q&#xFFFF;4\\. x" read "Q4. x" (U+FFFF is dropped where CommonMark substitutes U+FFFD). Kept
    beside the whole-line html.unescape reading as a further refusal branch, a union, never a
    replacement."""
    pieces, at = [], 0
    for match in MARKER_SOURCE_RE.finditer(line):
        pieces.append(line[at:match.start()])
        pieces.append(match.group(1) or _legacy_reference(match.group(0)))
        at = match.end()
    pieces.append(line[at:])
    return "".join(pieces)


def _marker_material(char):
    """Whether char can sit inside a marker match: every character of its NFKC form is whitespace, a
    digit or in _MARKER_ALPHABET. The first character of a head that is not material is kept as the
    match's one-character lookahead and ends the head, so NFKC is bounded at the first non-material
    character; an entirely marker-material line is normalised whole (U+FDFA, whose NFKC form is 18
    characters, is not marker material and ends the head, so a 16 MiB run of it feeds NFKC one or
    two characters, never the line)."""
    cached = _MATERIAL_CACHE.get(char)
    if cached is None:
        cached = all(c.isspace() or c.isdigit() or c in _MARKER_ALPHABET
                     for c in unicodedata.normalize("NFKC", char))
        _MATERIAL_CACHE[char] = cached
    return cached


def _marker_candidate(line):
    """Whether line reads as an answer marker "QN." to an eye on the rendered text. Three
    readings are scanned, a stated union erring toward refusal, never toward a pass: the
    CommonMark reading (backslash escapes and semicolon-terminated character references decoded
    once, _marker_rendered); the escape-aware legacy reading (_marker_escape_aware: escapes
    honoured, each tokenized reference decoded through html.unescape, the round-9 reading kept as
    a further refusal branch); and the legacy whole-line reading, html.unescape over the line (no
    reference tokenizer, so semicolonless legacy names and numeric references, "&nbspQ4." or
    "Q4&#46 x", decode too; backslash escapes are not honoured, so a backslash-escaped reference
    that reads as a marker under html.unescape is refused: the disclosed over-refusal). Renderers
    differ on control code points ("&#133;" is U+0085 NEL under the CommonMark code-point rule
    and U+2026 under the html.unescape HTML5 rule, which also drops "&#31;"), so the line is
    refused when any reading scans as a marker (_marker_scan). A line holding a reference
    _undecodable_reference refuses is refused here too, before any legacy decode, a conservative
    backstop: the parse refuses such a line outright first, naming the cause (_parse_delivery)."""
    rendered = _marker_rendered(line)
    if _marker_scan(rendered):
        return True
    if "&" not in line:
        # No reference: the legacy readings decode nothing the CommonMark reading does not, so
        # the text left to scan is the raw line, which scans as a marker only where its
        # CommonMark rendering does.
        return False
    if _undecodable_reference(line) is not None:
        # Refused outright by the parse before this scan, the cause named (_parse_delivery);
        # refused here too, before any legacy decode, as a conservative backstop.
        return True
    escape_aware = _marker_escape_aware(line)
    if escape_aware != rendered and _marker_scan(escape_aware):
        return True
    legacy = html.unescape(line)
    return legacy != rendered and _marker_scan(legacy)


def _marker_scan(line):
    """Whether the decoded line scans as an answer marker: past a permitted prefix of whitespace
    of any script, emphasis, heading, quote, bullet and ordered-list marks (LIST_MARK_RE), backslashes,
    the blank glyph U+2800 and ignorable characters (_marker_ignorable), interleaved in any order and
    consumed whole, a Q or a letter that looks like one, digits of any script and a full stop or one
    that looks like one, with emphasis runs permitted inside the marker, read after NFKC normalisation
    (so fullwidth and mathematical forms count), then whitespace, emphasis, U+2800 or the line's end.
    The head normalised is cut at the first character that cannot belong to a marker (_marker_material;
    that character stays as the lookahead), so the scan is one pass over the line and the NFKC
    input is the head alone: an entirely marker-material line is normalised whole, as
    _marker_material says, still linear in the line (MARKER_RE is possessive). Named residual (the module docstring, DISCLOSED
    RESIDUAL): a marker shaped as an inline code span ("`Q4.`") or as the text of an inline link
    ("[Q4.](x)") at the start of a line is not a candidate, its first source character being a
    backtick or a bracket, and an ignorable dropped directly after the stop leaves the lookahead on
    the character after it."""
    index, length = 0, len(line)
    while index < length:
        char = line[index]
        if char in MARKER_DECORATION or char.isspace() or _marker_ignorable(char):
            index += 1
            continue
        mark = LIST_MARK_RE.match(line, index)
        if mark is None:
            break
        index = mark.end()
    if index == length or (line[index] < "\x80" and line[index] not in "Qq"):
        return False
    head = []
    for char in line[index:]:
        if char < "\x80":
            if char in _ASCII_IGNORABLE:
                continue
            head.append(char)
            if char not in _ASCII_MATERIAL:
                break
        elif not _marker_ignorable(char):
            head.append(char)
            if not _marker_material(char):
                break
    return bool(MARKER_RE.match(_marker_normalize("NFKC", "".join(head))))


def _unread_fence(line):
    """Whether line holds a fence marker this grammar does not open but CommonMark may: one after a
    list marker ("- ```", "1. ~~~") or after a tab. Its reading depends on list nesting."""
    match = FENCE_SHAPE_RE.match(line)
    return bool(match) and bool(line[:match.start(1)].strip(" ")) \
        and not (match.group(1)[0] == "`" and "`" in match.group(2))


def _lazy_continuation(after_quote, line):
    """Whether a line directly after a blockquote line is no blockquote line yet not blank: CommonMark
    reads it into the quote (a lazy continuation), or ends the quote, as this grammar cannot tell."""
    return after_quote and bool(line.strip())


def _fence_may_be_text(indent):
    """Whether a fence opened at this indentation may render as text (an indented line after a
    paragraph line is its continuation, not a fence, outside a list): its content is then scanned
    for constructs that hide text."""
    return indent >= 4


def _inline_quote_spans(line):
    """(start, end) of each double-quoted span of one line, its quote marks included, read outside its
    inline code spans and outside a parenthetical example opened by "(e.g." or "(for example" and closed
    by the next ")"; a straight double quote right after a digit (an inch or seconds mark) opens
    nothing. An opener with no closer after it drops out of the scan without hiding a later complete
    quote of the other kind. Linear: each search moves forward."""
    if '"' not in line and "\u201c" not in line:
        return []
    masked = _blanked(line, _code_spans(line) + _example_spans(line))
    closers = dict(INLINE_QUOTE_PAIRS)
    following = {opener: -1 for opener in closers}
    spans = []
    index = 0
    while True:
        for opener in closers:
            # Each opener's next position is searched again only once the scan has passed it.
            if following[opener] is not None and following[opener] < index:
                found = masked.find(opener, index)
                following[opener] = found if found >= 0 else None
        starts = [at for at in following.values() if at is not None]
        if not starts:
            break
        start = min(starts)
        if masked[start] == '"' and start > 0 and masked[start - 1].isdigit():
            index = start + 1
            continue
        end = masked.find(closers[masked[start]], start + 1)
        if end < 0:
            # No closer follows this opener, nor any later one of its kind: that kind drops out.
            following[masked[start]] = None
            continue
        spans.append((start, end + 1))
        index = end + 1
    return spans


def _verdict_of(line):
    """The value of a VERDICT line ("VERDICT:", "**VERDICT:**", "**VERDICT**:", any case, leading
    spaces or tabs allowed), stripped of surrounding spaces and tabs; None for another line. A linear
    scan: a whitespace run costs one pass, never a backtracking search."""
    rest = line.lstrip(" \t")
    if rest.startswith("**"):
        rest = rest[2:]
    if rest[:7].upper() != "VERDICT":
        return None
    rest = rest[7:]
    if rest.startswith("**"):
        rest = rest[2:]
    rest = rest.lstrip(" \t")
    if not rest.startswith(":"):
        return None
    rest = rest[1:]
    if rest.startswith("**"):
        rest = rest[2:]
    return rest.strip(" \t")


# The non-whitespace Cc controls (C0, DEL and C1; the whitespace controls, tab to carriage
# return, the information separators U+001C to U+001F and U+0085, stay whitespace): a renderer
# shows one as nothing, a box or a replacement glyph, so a boundary word holding one can read as
# the boundary ("VER" then U+0001 then "DICT:").
_CC_NON_WHITESPACE = frozenset(char for char in map(chr, list(range(0x00, 0x20)) + list(range(0x7F, 0xA0)))
                               if not char.isspace())
_CC_STRIP_TABLE = {ord(char): None for char in _CC_NON_WHITESPACE}
_CC_TRIGGER_RE = re.compile(r"[\x00-\x08\x0e-\x1b\x7f-\x84\x86-\x9f]")
# The first character, spaces and tabs aside, that can open a boundary under some reading: V or
# v (VERDICT, any case), Q (QA-COMPLETE), an emphasis mark or an escape before VERDICT, a
# character reference, or a dropped Cc control. Any other first character survives every reading
# this grammar takes, so the line is no boundary candidate and nothing is decoded for it.
_BOUNDARY_HEAD_CHARS = frozenset("VvQ*&\\") | _CC_NON_WHITESPACE
# A whitespace separator inside a VERDICT value other than space or tab (U+001C and U+0085
# included): str.split would fold it into a clean verdict, so the value is refused
# (_parse_delivery), never normalised.
_ODD_WHITESPACE_RE = re.compile(r"[^\S \t]")


def _boundary_readings(line):
    """(name, reading) pairs under which a renderer can show line otherwise than its raw text:
    the CommonMark rendering (_marker_rendered), the legacy whole-line html.unescape reading, and
    the raw line with the non-whitespace Cc controls dropped. A reading equal to the raw line is
    not yielded, and no legacy decode runs on a line _undecodable_reference refuses (the parse
    refuses that line outright before any reading is taken)."""
    if _undecodable_reference(line) is not None:
        return
    if "\\" in line or "&" in line:
        rendered = _marker_rendered(line)
        if rendered != line:
            yield "CommonMark", rendered
    if "&" in line:
        legacy = html.unescape(line)
        if legacy != line:
            yield "legacy html.unescape", legacy
    if _CC_TRIGGER_RE.search(line):
        yield "control-stripped", line.translate(_CC_STRIP_TABLE)


def _boundary_shown(line):
    """A refusal description when a reading of line (_boundary_readings) is a VERDICT line or the
    QA-COMPLETE line while its raw text is neither, or None. The canonical boundary parse reads
    the raw text alone and is unchanged; this refuses a line a renderer can show as a boundary
    where that parse reads content ("VERDICT&#58; BLOCKERS FOUND", "QA&#45;COMPLETE", a U+0001
    inside either word), never a second boundary parse. DISCLOSED RESIDUAL: only the
    non-whitespace Cc controls are stripped, so a literal format or default-ignorable character
    inside a boundary word stays content."""
    head = line.lstrip(" \t")[:1]
    if not head or head not in _BOUNDARY_HEAD_CHARS:
        return None
    if _verdict_of(line) is not None or line.rstrip(" \t") == COMPLETE_LINE:
        return None
    for name, reading in _boundary_readings(line):
        if _verdict_of(reading) is not None:
            shown = "a VERDICT line"
        elif reading.rstrip(" \t") == COMPLETE_LINE:
            shown = "the {} line".format(COMPLETE_LINE)
        else:
            continue
        return ("a line that reads as {} under its {} reading while its raw text is not that line"
                " (the canonical boundary is read from the raw text alone; an encoded or"
                " control-padded boundary is refused, never read as content)".format(shown, name))
    return None


def _after_block_end(previous):
    """Whether no paragraph is open before a line: at the start of the body or after a fenced block
    (previous None), a blank line, an ATX heading, a thematic break or a setext underline."""
    return previous is None or not previous.strip() or bool(BLOCK_END_RE.match(previous))


def _indented_code_start(previous, line):
    """Whether line opens an indented code block: four or more columns of leading space where no
    paragraph is open (_after_block_end). Its CommonMark reading (code, or a list item's continuation
    paragraph) depends on list nesting this grammar does not track, so it is refused, not guessed."""
    return _after_block_end(previous) and line[:4].expandtabs(4).startswith("    ") and bool(line.strip())


def _new_item(number, line, heading, kind="item"):
    return {"item": number, "line": line, "heading": heading, "body": [], "quotes": [], "kind": kind,
            "names_text": []}


def _names_text(line, spans):
    """line with its inline quote spans blanked: the text whose tokens can name a file (a quoted span
    is quoted content, not the answer's own reference)."""
    if not spans:
        return line
    pieces, at = [], 0
    for start, end in spans:
        pieces.append(line[at:start])
        pieces.append(" ")
        at = end
    pieces.append(line[at:])
    return "".join(pieces)


def _fenced_answer_head(content):
    """The index of the first line of a fenced block's content that is shaped as an answer heading or
    reads as one (_marker_candidate)."""
    return next((index for index, text in enumerate(content) if ANSWER_HEAD_RE.match(text) or _marker_candidate(text)),
                None)


def _harness_line(payload, number, last, phase):
    """Whether a body line is the harness's closing status line: exactly HARNESS_LAST_LINE, the last
    non-blank line of the delivery, after QA-COMPLETE. Any other line starting WORKER_STATUS is
    content."""
    return phase == "complete" and number == last and payload == HARNESS_LAST_LINE


def _is_findings_heading(line):
    return line.rstrip(" \t") == FINDINGS_HEADING


def _parse_delivery(lines, question_count):
    """(answers, findings sections, verdict line) of the delivery body, read in the closed shape: for
    each question in order a column-0 line "QN." (N = 1 to question_count, each once, ascending) opening
    its answer; optionally one line "## Findings" opening a findings section, in which no line opens an
    answer; one VERDICT line; then a QA-COMPLETE line, after which come only blank lines and, as the
    delivery's last non-blank line, the harness line exactly. Any other shape, and any construct that
    can hide text from rendered view or reads as a marker without being one, is CannotEvaluate naming
    the line: a boundary is never inferred from free text."""
    segments = _segments(lines, strict=True)
    last = next((number for number, text in reversed(lines) if text.strip()), None)
    answers, sections = [], []
    current = None
    verdict = None
    phase = "answers"
    expected = 1
    quote_run = []
    count = [0]

    def refuse(number, what):
        raise CannotEvaluate("delivery line {}: {} (closed delivery shape: the SHA line, then Q1. to Q{}. in "
                             "order, an optional {!r} section, one VERDICT line, then {})".format(
                                 number, what, question_count, FINDINGS_HEADING, COMPLETE_LINE))

    def add(quote):
        count[0] += 1
        if count[0] > MAX_QUOTES:
            raise CannotEvaluate("the delivery carries more than {} quotes; it is not evaluated".format(MAX_QUOTES))
        current["quotes"].append(quote)

    def flush():
        if quote_run and any(text.strip() for _, text in quote_run):
            add({"kind": "blockquote", "line": quote_run[0][0], "text": "\n".join(text for _, text in quote_run)})
        quote_run.clear()

    previous = None
    after_quote = carried = False
    for kind, number, payload, indent in segments:
        if kind == "fence":
            flush()
            if _lazy_continuation(after_quote, "```"):
                refuse(number, "a fenced block directly after a blockquote line (leave a blank line after a quote)")
            for offset, text in enumerate(payload, 1):
                undecodable = _undecodable_reference(text)
                if undecodable:
                    refuse(number + offset, _undecodable_refusal(undecodable))
            inner = _fenced_answer_head(payload)
            if inner is not None:
                refuse(number + inner + 1, "an answer heading inside the fenced block opened at line {} (or a line "
                                           "that reads as one)".format(number))
            if phase not in ("answers", "findings") or current is None:
                refuse(number, "a fenced block outside an answer or the findings section")
            if _fence_may_be_text(indent):
                for offset, text in enumerate(payload, 1):
                    hidden = _hidden_construct(text)
                    if hidden:
                        refuse(number + offset, "{} inside a fenced block indented four or more columns, which may "
                                                "render as text".format(hidden))
                carried = True
            if any(text.strip() for text in payload):
                add({"kind": "fenced", "line": number, "text": "\n".join(payload)})
            previous = None
            after_quote = False
            continue
        if _indented_code_start(previous, payload):
            raise CannotEvaluate("delivery line {} opens an indented block where no paragraph is open (after a "
                                 "blank line, a fenced block or a heading); an indented code block is outside the "
                                 "declared grammar (fence the quote)".format(number))
        previous = payload
        quoted = BLOCKQUOTE_RE.match(payload)
        if not quoted and _lazy_continuation(after_quote, payload):
            refuse(number, "a line directly after a blockquote line that is not one (CommonMark reads it into the "
                           "quote; leave a blank line after a quote)")
        after_quote = False
        undecodable = _undecodable_reference(payload)
        if undecodable:
            refuse(number, _undecodable_refusal(undecodable))
        boundary = _boundary_shown(payload)
        if boundary:
            refuse(number, boundary)
        if not (payload.startswith("Q") and ANSWER_HEAD_RE.match(payload)) and _marker_candidate(payload):
            refuse(number, "a line that reads as an answer marker but is not \"QN.\" in ASCII at column 0 "
                           "(lookalike or non-ASCII characters, escapes or character references that render as one, "
                           "indentation, list or other marks before it, or emphasis inside it)")
        if _unread_fence(payload):
            refuse(number, "a fence marker after a list marker or a tab (its reading depends on list nesting; "
                           "open the fence at the start of its line)")
        if quoted and phase in ("answers", "findings") and current is not None:
            quote_run.append((number, quoted.group(1)))
            after_quote = True
            carried = False
            continue
        flush()
        if not payload.strip():
            carried = False
        else:
            masks, carried = _code_masks(payload, carried)
            hidden = _hidden_construct(_blanked(payload, masks))
            if hidden:
                refuse(number, "{} outside a fenced block, code span or blockquote, which can hide text from "
                               "rendered view".format(hidden))
        if _harness_line(payload, number, last, phase) or (phase in ("closed", "complete") and not payload.strip()):
            continue
        if phase == "complete":
            refuse(number, "text after the {} line".format(COMPLETE_LINE))
        if phase == "closed":
            if payload.rstrip(" \t") != COMPLETE_LINE:
                refuse(number, "text between the VERDICT line and {}".format(COMPLETE_LINE))
            phase = "complete"
            continue
        head = ANSWER_HEAD_RE.match(payload) if payload.startswith("Q") else None
        value = _verdict_of(payload)
        spans = _inline_quote_spans(payload)
        if head:
            if phase == "findings":
                refuse(number, "an answer heading inside the findings section")
            if expected > question_count:
                refuse(number, "an answer heading after Q{}., the brief's last question".format(question_count))
            if head.group(0) != "Q{}.".format(expected):
                refuse(number, "answer heading {} where Q{}. is expected".format(head.group(0), expected))
            current = _new_item(expected, number, payload)
            current["names_text"].append(_names_text(payload, spans)[head.end():])
            answers.append(current)
            expected += 1
        elif value is not None or _is_findings_heading(payload) or payload.rstrip(" \t") == COMPLETE_LINE:
            if expected <= question_count:
                refuse(number, "Q{}. is missing before this line{}".format(
                    expected, " (a line starting \"Verdict:\" is the VERDICT line, inside an answer too)"
                    if value is not None else ""))
            if value is None and _is_findings_heading(payload) and phase == "answers":
                current = _new_item(None, number, payload, "finding")
                sections.append(current)
                phase = "findings"
            elif value is None:
                refuse(number, "a second {!r} line".format(FINDINGS_HEADING) if _is_findings_heading(payload)
                       else "{} before the VERDICT line".format(COMPLETE_LINE))
            else:
                if _ODD_WHITESPACE_RE.search(value):
                    refuse(number, "a VERDICT value holding a whitespace separator other than space or tab "
                                   "(U+001C and U+0085 included): str.split would fold it into a clean "
                                   "verdict, so the value is refused, never normalised")
                current = verdict = _new_item(None, number, payload, "verdict")
                verdict["value"] = value
                phase = "closed"
        elif current is None:
            if payload.strip():
                refuse(number, "text before Q1.")
            continue
        else:
            current["body"].append(payload)
            current["names_text"].append(_names_text(payload, spans))
        for text in _inline_quotes(payload, spans):
            add({"kind": "inline", "line": number, "text": text})
    flush()
    end = lines[-1][0] if lines else 1
    if expected <= question_count:
        refuse(end, "the delivery ends before Q{}.".format(expected))
    if verdict is None:
        refuse(end, "no VERDICT line")
    if phase != "complete":
        refuse(end, "no {} line after the VERDICT line".format(COMPLETE_LINE))
    return answers, sections, verdict


def _parse_questions(lines):
    """The brief's numbered questions: its first run of column-0 numbered lines counting from 1."""
    text_lines = [(number, text) for kind, number, text, _ in _segments(lines) if kind == "line"]
    start = 0
    for index, (_, text) in enumerate(text_lines):
        if QUESTIONS_MARKER_RE.fullmatch(text.strip()):
            start = index + 1
            break
    questions = []
    current = None
    for number, text in text_lines[start:]:
        match = QUESTION_RE.match(text)
        if match:
            if int(match.group(2)) != len(questions) + 1:
                if questions:
                    break
                continue
            current = {"number": len(questions) + 1, "line": number, "text": text, "open": True}
            questions.append(current)
            continue
        if current is not None and current["open"]:
            if text.strip():
                current["text"] += "\n" + text
            else:
                current["open"] = False
    return questions


def _verdict_value(raw):
    return " ".join(raw.replace("*", " ").split()).rstrip(".").upper()[:200]


# ---------------------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------------------

def _extension(name):
    """The file extension of a name: the text after its last dot, when text other than dots stands
    before that dot and the extension is ASCII letters and digits holding a letter; otherwise ""."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem.strip(".") or not (ext.isascii() and ext.isalnum()) or ext.isdigit():
        return ""
    return ext


def _repository_path(token, tree):
    """The repository path of an absolute path under the --repo directory (as given or resolved), or
    None for any other absolute or home-relative path."""
    return next((token[len(root) + 1:] for root in tree.roots if token.startswith(root + "/")), None)


def _path_tokens(text, tree):
    """(blob path, None) for each file of the pinned tree text names, and (None, name) for each file
    reference that resolves to no blob. A token (split at whitespace, quotes, apostrophes, backticks,
    brackets, parentheses, commas and semicolons; a "#" anchor, a ":" suffix (so a URL keeps only its
    scheme), asterisks at its ends and trailing dots, "!" or "?" removed) is a file reference, whatever
    other characters it holds, when it
    is a blob path; a path with a slash whose last part carries a file extension or whose first part is
    a directory of the tree; an absolute or home-relative path whose last part carries an extension (a
    path under the --repo directory is read as its repository path); or a bare name that is a root blob
    or carries an extension some blob of the tree carries. A token holding a glob or placeholder
    character (* ? < > { } $) and a directory of the tree are not file references."""
    for match in PATH_TOKEN_RE.finditer(text):
        token = match.group(0)
        if "." not in token and "/" not in token and not (
                tree.undotted and token.strip("*").split(":", 1)[0] in tree.undotted):
            continue
        token = token.split("#", 1)[0].split(":", 1)[0].strip("*").rstrip(".!?").strip("*")
        if not token or TEMPLATE_CHARS.intersection(token):
            continue
        if token.startswith(("/", "~/")):
            local = _repository_path(token, tree)
            if local is None:
                if _extension(token.rsplit("/", 1)[-1]):
                    yield None, token + " (outside the repository)"
                continue
            token = local
        while token.startswith("./"):
            token = token[2:]
        if not token.strip("/."):
            continue
        if "/" in token:
            if token in tree.blobs:
                yield token, None
            elif token.rstrip("/") in tree.dirs:
                continue
            elif _extension(token.rstrip("/").rsplit("/", 1)[-1]) or token.split("/", 1)[0] in tree.dirs:
                yield None, token
        else:
            bound = _basename_bindings(token, tree)
            if bound:
                for path in bound:
                    yield path, None
            elif _extension(token).casefold() in tree.extensions:
                yield None, token


def _named_paths(text, tree):
    """(blob paths of the tree text names, file references of text absent from the tree)."""
    found, unresolved, seen = [], [], set()
    for path, absent in _path_tokens(text, tree):
        key = path or absent
        if key in seen:
            continue
        seen.add(key)
        if path is not None:
            found.append(path)
        else:
            unresolved.append(absent)
    return found, unresolved


def _basename_bindings(token, tree):
    """The blobs a bare name (no slash) names: every blob with that basename, the root one first, when
    the name is a root blob or carries a file extension; a name that is no basename binds nothing."""
    if "/" in token or not (token in tree.blobs or _extension(token)):
        return []
    return sorted(tree.by_basename.get(token, ()), key=lambda path: ("/" in path, path))


def _answer_names(item, tree):
    """The files an answer names anywhere in its own text (its QN. line and body, outside quotes)."""
    return _named_paths("\n".join(item["names_text"]), tree)


def _findings_names(section, tree):
    """The files the findings section names anywhere in its text, outside quotes."""
    return _named_paths("\n".join(section["names_text"]), tree)


def _quote_targets(item, questions, tree):
    """(files the item's quotes must match in, named files absent from the tree, what named them, the
    answer's own absent file references that its question does not name). An answer that names a
    file binds to the files it names, never to the whole tree; one that names none binds to its
    question's files; the whole tree only when neither names a file."""
    if item["kind"] == "verdict":
        return [], [], "none (the VERDICT line)", []
    if item["kind"] == "finding":
        found, unresolved = _findings_names(item, tree)
        return (found, unresolved, "findings section", []) if found or unresolved else ([], [], "none", [])
    found, unresolved = _answer_names(item, tree)
    question = questions.get(item["item"])
    asked = _named_paths(question["text"], tree) if question is not None else ([], [])
    if found or unresolved:
        return found, unresolved, "answer", [name for name in unresolved if name not in asked[1]]
    if asked[0] or asked[1]:
        return asked[0], asked[1], "question", []
    return [], [], "none", []


def _substance_words(text, tree):
    """The words of text that are neither a file name nor markup, lazily: what an answer says. Words
    live only in tokens (the separators hold no letter or digit); a token without a dot or slash that
    is no root blob cannot be a name."""
    for match in PATH_TOKEN_RE.finditer(text):
        token = match.group(0)
        if ("." in token or "/" in token or token in tree.blobs) and next(_path_tokens(token, tree), None):
            continue
        yield from WORD_RE.findall(token.casefold())


def _asks_for_quote(question):
    """Whether a question asks for a quote: it holds the word quote (quotes, quoted, quoting)."""
    return bool(QUOTE_WORD_RE.search(question["text"]))


def _answer_state(item, question, tree):
    """"answered", or why the item is not: UNVERIFIABLE, no quote (prose alone answering a question
    that asks for a quote), empty (no word beyond its QN. marker and file names, and no quote), or
    the question restated (exactly its words). Both word streams are read only as far as their first
    difference."""
    if item["unverifiable"]:
        return "unverifiable"
    if item["quotes"]:
        return "answered"
    if question is not None and _asks_for_quote(question):
        return "no quote"
    head = ANSWER_HEAD_RE.match(item["heading"])
    words = _substance_words("\n".join([item["heading"][head.end():] if head else item["heading"]]
                                        + item["body"]), tree)
    first = next(words, None)
    if first is None:
        return "empty"
    if question is not None:
        asked = QUESTION_RE.match(question["text"])
        wanted = _substance_words(question["text"][asked.end():] if asked else question["text"], tree)
        ours = iter([first])
        missing = object()
        while True:
            mine = next(ours, missing)
            if mine is missing:
                mine = next(words, missing)
            theirs = next(wanted, missing)
            if mine is missing and theirs is missing:
                return "question restated"
            if mine != theirs:
                break
    return "answered"


def _verbatim_in(content, data):
    """C2's match: data is one contiguous byte span of content (an excerpt may start or end mid-line).
    A file stored with CRLF line ends also matches a multi-line quote whose lines end in LF."""
    if data in content:
        return True
    return b"\r\n" in content and b"\n" in data and data.replace(b"\n", b"\r\n") in content


def _normalised(text):
    return " ".join(text.split())


def _normalise_with_map(text):
    """_normalised(text) with, for each of its characters, the index of the character it came from."""
    out, index = [], []
    pending = None
    for position, char in enumerate(text):
        if char.isspace():
            if out and pending is None:
                pending = position
            continue
        if pending is not None:
            out.append(" ")
            index.append(pending)
            pending = None
        out.append(char)
        index.append(position)
    return "".join(out), index


def _normalised_match(tree, text, targets):
    """The nearest whitespace-normalised match of a failing quote, named files first: a diagnostic."""
    wanted = _normalised(text)
    if not wanted:
        return None
    tree.load_all()
    for path in list(targets) + [path for path in tree.paths() if path not in targets]:
        if path not in tree.blobs:
            continue
        content = _blob_bytes(tree, path).decode("utf-8", "replace")
        if wanted not in _normalised(content):
            continue
        norm, index = _normalise_with_map(content)
        at = norm.find(wanted)
        start, end = index[at], index[at + len(wanted) - 1] + 1
        return {"file": path, "line": content.count("\n", 0, start) + 1, "excerpt": content[start:end][:400]}
    return None


def _check_quote(tree, quote, targets, unresolved, named_by, diagnose=True):
    """The C2 result for one quote; result["ok"] is the verdict. The other-file listing and the
    normalised diagnostic are computed only when diagnose is set (the first MAX_LISTED failures)."""
    data = quote["text"].encode("utf-8")
    result = {"kind": quote["kind"], "line": quote["line"], "text": quote["text"],
              "named_by": named_by, "searched": list(targets) + list(unresolved) or [WHOLE_TREE],
              "unresolved_named_paths": list(unresolved), "found_in": [], "other_files": [],
              "normalised_match": None, "ok": False, "reason": ""}
    if targets or unresolved:
        result["found_in"] = [path for path in targets if _verbatim_in(_blob_bytes(tree, path), data)]
        if not result["found_in"]:
            named = ", ".join(list(targets) + ["{} (absent from the pinned tree)".format(path)
                                               for path in unresolved])
            result["reason"] = "not verbatim in the named file {}".format(named)
            if diagnose:
                tree.load_all()
                others = []
                for path in tree.paths():
                    if path not in targets and _verbatim_in(_blob_bytes(tree, path), data):
                        others.append(path)
                        if len(others) >= MAX_LISTED:
                            break
                result["other_files"] = others
                result["reason"] += ("; found only in {}".format(", ".join(others)) if others else
                                     "; not verbatim anywhere in the pinned tree")
    else:
        tree.load_all()
        hit = next((path for path in tree.paths() if _verbatim_in(_blob_bytes(tree, path), data)), None)
        result["found_in"] = [hit] if hit else []
        if not hit:
            result["reason"] = "not verbatim anywhere in the pinned tree"
    result["ok"] = bool(result["found_in"])
    if not result["ok"] and diagnose:
        result["normalised_match"] = _normalised_match(tree, quote["text"], targets)
    return result


def _rule_all_unverifiable(verdict, answered, total):
    """C1: a verdict of any kind with no answered item is no vote (a verdict counts only for answered
    items)."""
    if verdict is not None and answered == 0:
        return "verdict {!r} with no answered item (every item UNVERIFIABLE, empty, restated or unquoted; " \
               "coverage 0/{})" \
            .format(verdict, total)
    return None


# ---------------------------------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------------------------------

def evaluate(brief_path, delivery_path, repo):
    """(report, exit code) for one delivery against one brief and repository."""
    report = {"tool": "check_verifier_delivery", "verdict": None, "reasons": [], "pin": None,
              "delivery_verdict": None, "clean": False, "coverage": None, "quotes_checked": 0,
              "quotes_failed": 0, "items": []}
    try:
        brief = _decode(_read_input(brief_path, "brief"), "brief")
        delivery = _decode(_read_input(delivery_path, "delivery"), "delivery")
        pin = _brief_pin(brief)
        report["pin"] = pin
        body = _delivery_body(delivery, pin)
        questions = _parse_questions(_lines(brief))
        if not questions:
            raise CannotEvaluate("the brief numbers no question; the closed delivery shape answers a numbered "
                                 "question list")
        items, sections, verdict_line = _parse_delivery(body, len(questions))
        tree = PinnedTree(repo, pin)
        by_number = {question["number"]: question for question in questions}
        failures = []
        for item in items + sections + [verdict_line]:
            targets, unresolved, named_by, stray = _quote_targets(item, by_number, tree)
            unverifiable = bool(UNVERIFIABLE_RE.search("\n".join([item["heading"]] + item["body"])))
            item["unverifiable"] = unverifiable
            if stray:
                failures.append("item {} line {} names {}{}, absent from the pinned tree (an answer's file reference "
                                "must resolve)".format(item["item"], item["line"], ", ".join(stray[:MAX_LISTED]),
                                                       " and {} more".format(len(stray) - MAX_LISTED)
                                                       if len(stray) > MAX_LISTED else ""))
            results = []
            for quote in item["quotes"]:
                result = _check_quote(tree, quote, targets, unresolved, named_by, diagnose=len(failures) < MAX_LISTED)
                results.append(result)
                if not result["ok"]:
                    failures.append("{} line {} {} quote: {}".format(
                        "item {}".format(item["item"]) if item["kind"] == "item" else item["kind"],
                        result["line"], result["kind"], result["reason"]))
            state = None
            if item["kind"] == "item":
                state = _answer_state(item, by_number.get(item["item"]), tree)
                item["state"] = state
            if item["kind"] == "item" or results:
                report["items"].append({"item": item["item"] if item["kind"] == "item" else item["kind"],
                                        "line": item["line"], "heading": item["heading"][:400],
                                        "unverifiable": unverifiable if item["kind"] == "item" else None,
                                        "answer": state, "absent_references": stray[:MAX_LISTED], "quotes": results})
            report["quotes_checked"] += len(results)
            report["quotes_failed"] += sum(1 for result in results if not result["ok"])
    except CannotEvaluate as exc:
        report["verdict"] = "CANNOT_EVALUATE"
        report["reasons"].append(str(exc))
        return report, 2
    report["delivery_verdict"] = _verdict_value(verdict_line["value"])
    report["clean"] = report["delivery_verdict"] == CLEAN_VERDICT
    total = len(questions)
    answered = sum(1 for item in items if item["state"] == "answered")
    report["coverage"] = {"answered": answered, "total": total, "text": "{}/{}".format(answered, total)}
    no_vote = [reason for reason in (_rule_all_unverifiable(report["delivery_verdict"], answered, total),) if reason]
    if failures:
        report["verdict"] = "FAIL"
        report["reasons"] = failures + no_vote
        return report, 1
    if no_vote:
        report["verdict"] = "NO_VOTE"
        report["reasons"] = no_vote
        return report, 1
    report["verdict"] = "PASS"
    return report, 0


def _summary(report):
    head = report["reasons"][0] if report["reasons"] else "every quote verbatim in the pinned tree"
    coverage = report["coverage"]["text"] if report["coverage"] else "n/a"
    return "check_verifier_delivery: {}: {}; coverage {}; {} of {} quote(s) failed".format(
        report["verdict"], head, coverage, report["quotes_failed"], report["quotes_checked"])


# ---------------------------------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------------------------------

SUITE_ID = "verifier-delivery-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()
OTHER_SHA = "0123456789abcdef" * 2 + "01234567"
FIXTURE_FILES = {
    "docs/alpha.md": "# Alpha notes\n\nThe first rule is plain.\nThe second rule follows it.\n"
                     "The third rule closes the set.\n",
    "src/beta.py": "def beta(ready):\n    if ready:\n        return 1\n    return 0\n",
    "docs/gamma.md": "Only gamma carries this exact sentence.\n",
    "README.md": "Only the root readme carries this line.\n",
    ".preview/README.md": "| Tool | Role |\n| pattern-self-match.py | matches its own pattern |\n",
    "docs/crlf.md": "Stored with CRLF line ends.\r\nSecond stored line.\r\n",
    "VERSION": "0.0.1-fixture\n",
}
BRIEF_MIXED = ("Synthetic review brief.\nBRIEF-PIN: {pin}\n\n"
               "1. Quote the first rule from docs/alpha.md.\n"
               "2. Quote the readiness guard from src/beta.py.\n"
               "3. Quote the sentence in docs/gamma.md.\n"
               "4. Does src/beta.py return 0 when not ready?\n\n"
               "Standing lines:\n1. This list restarts and holds no question.\n")
BRIEF_QUOTE_ONLY = BRIEF_MIXED.replace("4. Does src/beta.py return 0 when not ready?",
                                       "4. Quote the closing rule from docs/alpha.md.")
BRIEF_FIVE = BRIEF_MIXED.replace("\n\nStanding lines:", "\n5. Quote the closing rule from docs/alpha.md.\n\n"
                                                    "Standing lines:")
BRIEF_README = ("Synthetic review brief.\nBRIEF-PIN: {pin}\n\n"
                "1. Quote the README.md table row for pattern-self-match.py.\n")
BRIEF_SLASH = BRIEF_README.replace("the README.md table row", "the table row of .preview/README.md")
BRIEF_TWO = ("Synthetic review brief.\nBRIEF-PIN: {pin}\n\n"
             "1. Is the readiness guard of src/beta.py present?\n"
             "2. Does docs/alpha.md state the first rule?\n")
BRIEF_ABSENT = ("Synthetic review brief.\nBRIEF-PIN: {pin}\n\n"
                "1. Quote the first rule from docs/retired/alpha.md.\n")
BRIEF_MARKER = ("Synthetic review brief.\nBRIEF-PIN: {pin}\n\nBackground steps:\n"
                "1. Quote the first rule from docs/alpha.md.\n\nQUESTIONS:\n"
                "1. Quote the sentence in docs/gamma.md.\n")
BRIEF_ONE = "Synthetic review brief.\nBRIEF-PIN: {pin}\n\n1. Inspect docs/alpha.md and say whether it states a rule.\n"
BRIEF_OPEN = "Synthetic review brief.\nBRIEF-PIN: {pin}\n\n1. Inspect the tree and report one sentence of it.\n"
BRIEF_QUOTE_TWO = ("Synthetic review brief.\nBRIEF-PIN: {pin}\n\n"
                   "1. Quote the readiness guard from src/beta.py.\n"
                   "2. Quote the first rule from docs/alpha.md.\n")
BRIEF_ABSENT_ASKED = "Synthetic review brief.\nBRIEF-PIN: {pin}\n\n1. Does docs/retired/alpha.md still exist?\n"
BRIEF_VERSION = "Synthetic review brief.\nBRIEF-PIN: {pin}\n\n1. Quote the VERSION line as written.\n"
BRIEF_BARE_ABSENT = "Synthetic review brief.\nBRIEF-PIN: {pin}\n\n1. Quote the first rule from alpha_old.md.\n"
A1 = 'Q1. docs/alpha.md reads "The first rule is plain."'
A2 = "Q2. src/beta.py:\n```python\n    if ready:\n        return 1\n```"
A3 = 'Q3. docs/gamma.md says "Only gamma carries this exact sentence."'
A4 = 'Q4. Yes: "    return 0" ends beta().'
HARNESS_LEAD = "WORKER_STATUS: PASS (account=fixture, rc=0, family=fixture, model=fixture, effort=low)\n"


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


def _unverifiable(number):
    return "Q{}. UNVERIFIABLE: the file could not be read.".format(number)


def _delivery(pin, *items, verdict="VERDICT: NO BLOCKERS", lead="", findings=None):
    """A delivery in the closed shape: the SHA line, the answers, an optional findings section, the
    VERDICT line and QA-COMPLETE, then the transport's WORKER_STATUS line."""
    body = "\n\n".join(items) + ("\n\n{}\n{}".format(FINDINGS_HEADING, findings) if findings is not None else "")
    return "{}{}\n\n{}\n\n{}\n{}\n\nWORKER_STATUS: COMPLETE\n".format(lead, pin, body, verdict, COMPLETE_LINE)


def _mixed(pin, replace=None, **options):
    """The four answers A1 to A4 to BRIEF_MIXED, with the answers replace maps by number swapped in."""
    answers = {1: A1, 2: A2, 3: A3, 4: A4}
    answers.update(replace or {})
    return _delivery(pin, *(answers[number] for number in sorted(answers)), **options)


class _Fixture:
    """A throwaway repository with FIXTURE_FILES committed, plus brief and delivery files beside it."""

    def __init__(self, base):
        self.base = base
        self.repo = base / "repo"
        for rel, text in FIXTURE_FILES.items():
            (self.repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / rel).write_text(text, encoding="utf-8")
        self._git("init", "-q")
        self._git("add", "-A")
        self._git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture")
        self.pin = self._git("rev-parse", "HEAD").strip()
        self.count = 0

    def _git(self, *args):
        result = subprocess.run(["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                                 "-C", str(self.repo), *args], capture_output=True, text=True,
                                timeout=TIMEOUT, check=False)
        if result.returncode != 0:
            raise CannotEvaluate("fixture git {} failed: {}".format(args[0], result.stderr.strip()))
        return result.stdout

    def files(self, delivery, brief=BRIEF_MIXED, pin=None):
        self.count += 1
        brief_path = self.base / "brief-{}.md".format(self.count)
        delivery_path = self.base / "delivery-{}.md".format(self.count)
        brief_path.write_text(brief.format(pin=pin or self.pin), encoding="utf-8")
        if isinstance(delivery, bytes):
            delivery_path.write_bytes(delivery)
        else:
            delivery_path.write_text(delivery, encoding="utf-8")
        return str(brief_path), str(delivery_path)

    def run(self, delivery, brief=BRIEF_MIXED, pin=None, repo=None):
        brief_path, delivery_path = self.files(delivery, brief, pin)
        return evaluate(brief_path, delivery_path, str(repo or self.repo))[0]


def _patched(name, replacement, thunk):
    """thunk() with this module's attribute `name` replaced, restored afterwards."""
    module = sys.modules[__name__]
    if not hasattr(module, name):
        raise CannotEvaluate("self-test patches an unknown attribute {}".format(name))
    saved = getattr(module, name)
    setattr(module, name, replacement)
    try:
        return thunk()
    finally:
        setattr(module, name, saved)


def _normalised_containment(content, data):
    return _normalised(data.decode("utf-8", "replace")) in _normalised(content.decode("utf-8", "replace"))


def _whole_line_only(content, data):
    return (b"\n" + data + b"\n") in (b"\n" + content + b"\n")


def _self_test_cases(fx):
    sha = fx.pin
    verdict = lambda report: report["verdict"]
    refusal = lambda report, words: (report["verdict"], words in " ".join(report["reasons"]))
    refused = ("CANNOT_EVALUATE", True)
    never = re.compile(r"(?!)")

    # C1 coverage
    all_unverifiable = _delivery(sha, *(_unverifiable(n) for n in range(1, 5)))
    check("c1/all-unverifiable-no-vote", verdict(fx.run(all_unverifiable)), "NO_VOTE")
    check("flip/c1-all-unverifiable",
          verdict(_patched("_rule_all_unverifiable", lambda *a: None, lambda: fx.run(all_unverifiable))),
          "PASS")
    one = fx.run(_mixed(sha, {3: _unverifiable(3)}, lead=HARNESS_LEAD))
    check("c1/one-unverifiable-passes", (verdict(one), one["coverage"]["text"]), ("PASS", "3/4"))
    no_quotes = _delivery(sha, *("Q{}. Confirmed after reading the file.".format(n) for n in range(1, 5)))
    report = fx.run(no_quotes, BRIEF_QUOTE_ONLY)
    check("c1/quote-only-without-quotes-no-vote", (verdict(report), report["coverage"]["text"]), ("NO_VOTE", "0/4"))
    check("flip/c1-quote-only",
          verdict(_patched("_asks_for_quote", lambda question: False, lambda: fx.run(no_quotes, BRIEF_QUOTE_ONLY))),
          "PASS")
    check("c1/quote-only-with-quotes-passes", verdict(fx.run(
        _mixed(sha, {4: 'Q4. docs/alpha.md: "The third rule closes the set."'}), BRIEF_QUOTE_ONLY)), "PASS")
    report = fx.run(no_quotes)
    check("c1/mixed-list-without-quotes-passes", (verdict(report), report["coverage"]["text"]), ("PASS", "1/4"))
    prose = _delivery(sha, "Q1. src/beta.py:\n```\n    if ready:\n        return 1\n```", "Q2. Confirmed.")
    report = fx.run(prose, BRIEF_QUOTE_TWO)
    check("c1/quote-question-prose-not-answered",
          (verdict(report), report["coverage"]["text"], [item["answer"] for item in report["items"]]),
          ("PASS", "1/2", ["answered", "no quote"]))
    check("flip/c1-quote-question-prose", _patched(
        "_asks_for_quote", lambda question: False, lambda: fx.run(prose, BRIEF_QUOTE_TWO))["coverage"]["text"], "2/2")
    report = fx.run(prose.replace("Q2. Confirmed.", "Q2. UNVERIFIABLE"), BRIEF_QUOTE_TWO)
    check("c1/quote-question-unverifiable-word-passes",
          (verdict(report), [item["answer"] for item in report["items"]]), ("PASS", ["answered", "unverifiable"]))
    blockers_none = _delivery(sha, *(_unverifiable(n) for n in range(1, 6)), verdict="VERDICT: BLOCKERS FOUND")
    report = fx.run(blockers_none, BRIEF_FIVE)
    check("c1/blockers-zero-coverage-no-vote", (verdict(report), report["coverage"]["text"]), ("NO_VOTE", "0/5"))
    clean_only = lambda found, answered, total: "no vote" if found == CLEAN_VERDICT and answered == 0 else None
    check("flip/c1-blockers-zero-coverage",
          verdict(_patched("_rule_all_unverifiable", clean_only, lambda: fx.run(blockers_none, BRIEF_FIVE))), "PASS")
    report = fx.run(_delivery(sha, A1, *(_unverifiable(n) for n in range(2, 5)), verdict="VERDICT: BLOCKERS FOUND"))
    check("c1/blockers-with-answered-item-passes", (verdict(report), report["coverage"]["text"]), ("PASS", "1/4"))

    # The closed delivery shape
    check("shape/absent-verdict-cannot-evaluate", refusal(fx.run(
        _mixed(sha).replace("VERDICT: NO BLOCKERS\n{}\n".format(COMPLETE_LINE), "")), "no VERDICT line"), refused)
    check("shape/second-verdict-cannot-evaluate", refusal(fx.run(
        _mixed(sha, {4: A4 + "\n\nVERDICT: BLOCKERS"})), "between the VERDICT line"), refused)
    check("shape/missing-qa-complete-cannot-evaluate", refusal(fx.run(
        _mixed(sha).replace("\n{}\n\n{}\n".format(COMPLETE_LINE, HARNESS_LAST_LINE), "\n")), "no QA-COMPLETE line"),
        refused)
    check("shape/text-after-qa-complete-cannot-evaluate", refusal(fx.run(
        _mixed(sha).replace("WORKER_STATUS: COMPLETE", "A late note.")), "after the QA-COMPLETE line"), refused)
    check("shape/text-before-first-answer-cannot-evaluate", refusal(fx.run(
        _mixed(sha).replace("\n\nQ1.", '\nIntro "never in the tree"\n\nQ1.', 1)), "text before Q1."), refused)
    check("shape/legacy-numbered-answers-cannot-evaluate", refusal(fx.run(
        _delivery(sha, *(answer[1:] for answer in (A1, A2, A3, A4)))), "text before Q1."), refused)
    check("shape/duplicate-answer-cannot-evaluate",
          refusal(fx.run(_delivery(sha, A1, A1, A2, A3, A4)), "Q1. where Q2. is expected"), refused)
    check("shape/out-of-order-answer-cannot-evaluate",
          refusal(fx.run(_delivery(sha, A2, A1, A3, A4)), "Q2. where Q1. is expected"), refused)
    check("shape/missing-answer-cannot-evaluate",
          refusal(fx.run(_delivery(sha, A1, A2, A3)), "Q4. is missing"), refused)
    check("shape/truncated-delivery-cannot-evaluate",
          refusal(fx.run("{}\n\n{}\n\n{}\n".format(sha, A1, A2)), "the delivery ends before Q3."), refused)
    check("shape/answer-beyond-questions-cannot-evaluate", refusal(fx.run(
        _mixed(sha, {5: 'Q5. docs/alpha.md "The first rule is plain."'})), "after Q4."), refused)
    fenced_head = _mixed(sha, {1: "Q1. docs/alpha.md:\n```\nThe first rule is plain.\nQ2. src/beta.py\n```"})
    check("shape/answer-heading-inside-fence-cannot-evaluate",
          refusal(fx.run(fenced_head), "answer heading inside the fenced block"), refused)
    check("flip/shape-answer-heading-inside-fence",
          verdict(_patched("_fenced_answer_head", lambda content: None, lambda: fx.run(fenced_head))), "FAIL")
    below = _delivery(sha, "Q1. \nSymbol: `beta`\n```python\ndef beta(ready):\n```", "Q2. \n> The first rule is plain.")
    report = fx.run(below, BRIEF_QUOTE_TWO)
    check("shape/answer-below-its-heading-passes", (verdict(report), report["coverage"]["text"]), ("PASS", "2/2"))
    numbered = _mixed(sha, {1: A1 + "\n1. one\n2. two\n### 3. three",
                            2: A2 + "\nMAJOR: a grade word inside an answer\n## A sub-heading\n4. four"})
    check("shape/numbered-lines-inside-answer-are-content-passes", verdict(fx.run(numbered)), "PASS")
    check("shape/unclosed-fence-cannot-evaluate", refusal(fx.run(
        _delivery(sha, "Q1. docs/alpha.md:\n```\nThe first rule is plain."), BRIEF_ONE), "unclosed fenced block"),
        refused)
    short_close = _delivery(sha, "Q1. docs/alpha.md:\n````\nThe first rule is plain.\n```")
    check("shape/shorter-fence-close-cannot-evaluate",
          refusal(fx.run(short_close, BRIEF_ONE), "unclosed fenced block"), refused)
    same_char = lambda close, marker: close.group(1)[0] == marker[0]
    check("flip/shape-fence-close-length",
          verdict(_patched("_fence_closes", same_char, lambda: fx.run(short_close, BRIEF_ONE))), "PASS")
    misclosed = _mixed(sha, {2: "Q2. src/beta.py:\n```\n    if ready:\n  ```"})
    check("shape/fence-close-indent-mismatch-cannot-evaluate", refusal(fx.run(misclosed), "indented otherwise"), refused)
    check("flip/shape-fence-close-indent",
          verdict(_patched("_close_indent_matches", lambda *a: True, lambda: fx.run(misclosed))), "PASS")
    check("shape/tab-close-inside-fence-cannot-evaluate", refusal(fx.run(
        _mixed(sha, {2: "Q2. src/beta.py:\n```\n    if ready:\n\t```\n```"})), "indented otherwise"), refused)
    check("shape/verdict-line-inside-answer-cannot-evaluate", refusal(fx.run(
        _mixed(sha, {2: A2 + "\nVerdict: the guard holds."})), "is the VERDICT line"), refused)

    # Harness lines: only line 1 and the last line, each exact; any other WORKER_STATUS line is content
    check("shape/harness-first-line-passes", verdict(fx.run(_mixed(sha, lead=HARNESS_LEAD))), "PASS")
    fake_lead = _mixed(sha, lead='WORKER_STATUS_FAKE tools/absent_xyz.py "fabricated sentinel"\n')
    check("shape/status-lookalike-first-line-cannot-evaluate", refusal(fx.run(fake_lead), "is not a full 40-hex SHA"),
          refused)
    check("flip/shape-harness-first-line",
          verdict(_patched("HARNESS_FIRST_RE", re.compile(r"WORKER_STATUS.*"), lambda: fx.run(fake_lead))), "PASS")
    check("shape/second-status-line-cannot-evaluate", refusal(fx.run(
        _mixed(sha, lead=HARNESS_LEAD + "WORKER_STATUS: RUNNING\n")), "is not a full 40-hex SHA"), refused)
    status_content = _mixed(sha, {4: A4 + '\nWORKER_STATUS_FAKE src/beta.py reads "return FABRICATED"'})
    check("c2/status-prefixed-content-checked-fails", verdict(fx.run(status_content)), "FAIL")
    anywhere = lambda payload, *rest: payload.startswith(STATUS_PREFIX)
    check("flip/c2-status-prefixed-content",
          verdict(_patched("_harness_line", anywhere, lambda: fx.run(status_content))), "PASS")
    trailing = _mixed(sha).replace("WORKER_STATUS: COMPLETE", "WORKER_STATUS_FAKE arbitrary content")
    check("shape/status-lookalike-last-line-cannot-evaluate", refusal(fx.run(trailing), "after the QA-COMPLETE line"),
          refused)
    check("flip/shape-harness-last-line", verdict(_patched("_harness_line", anywhere, lambda: fx.run(trailing))), "PASS")
    check("shape/harness-line-not-last-cannot-evaluate", refusal(fx.run(
        _mixed(sha) + "WORKER_STATUS: COMPLETE\n"), "after the QA-COMPLETE line"), refused)

    # Text hidden from rendered view: raw HTML, link definitions, images, link titles that run on
    hide = lambda opener, closer: _mixed(sha, {3: A3 + "\n" + opener, 4: "Q4. Yes, src/beta.py returns 0.\n" + closer})
    comment = hide("<!--", "-->")
    at_line = comment.split("\n").index("<!--") + 1
    check("shape/html-comment-hides-answer-cannot-evaluate",
          refusal(fx.run(comment), "delivery line {}: raw HTML".format(at_line)), refused)
    report = _patched("HTML_START_RE", never, lambda: fx.run(comment))
    check("flip/shape-raw-html", (verdict(report), report["coverage"]["text"]), ("PASS", "4/4"))
    check("shape/html-tag-cannot-evaluate", refusal(fx.run(
        _mixed(sha, {4: A4 + "\n<details>\nhidden\n</details>"})), "raw HTML"), refused)
    check("shape/html-in-code-span-passes",
          verdict(fx.run(_mixed(sha, {4: A4 + "\nThe marker `<!--` opens a comment."}))), "PASS")
    check("shape/autolink-passes", verdict(fx.run(_mixed(sha, {4: A4 + "\nSee <https://example.invalid/beta>."}))),
          "PASS")
    carry = hide("The tick ` opens a span\n`<!--` closes it", "-->")
    check("shape/code-span-across-lines-cannot-evaluate", refusal(fx.run(carry), "raw HTML"), refused)
    check("flip/shape-code-span-carry", verdict(_patched(
        "_code_masks", lambda line, carried: (_code_spans(line), False), lambda: fx.run(carry))), "PASS")
    text_fence = hide("    ```\n    <!--\n    ```", "-->")
    check("shape/indented-fence-content-scanned-cannot-evaluate", refusal(fx.run(text_fence), "may render as text"),
          refused)
    check("flip/shape-indented-fence-content",
          verdict(_patched("_fence_may_be_text", lambda indent: False, lambda: fx.run(text_fence))), "FAIL")
    definition = hide("\n[note]: https://example.invalid/n '", "'")
    check("shape/link-definition-hides-answer-cannot-evaluate", refusal(fx.run(definition), "link reference"), refused)
    check("flip/shape-link-definition",
          verdict(_patched("LINK_DEFINITION_RE", never, lambda: fx.run(definition))), "PASS")
    image = hide("![", "](https://example.invalid/b.png)")
    check("shape/image-alt-hides-answer-cannot-evaluate", refusal(fx.run(image), "an image"), refused)
    check("flip/shape-image", verdict(_patched("IMAGE_RE", never, lambda: fx.run(image))), "PASS")
    title = hide("See [the notes](https://example.invalid/n 'opened", "')")
    check("shape/link-title-hides-answer-cannot-evaluate", refusal(fx.run(title), "inline link"), refused)
    check("flip/shape-link-title", verdict(_patched("_link_runs_on", lambda line: False, lambda: fx.run(title))), "PASS")
    check("c2/closed-inline-link-passes", verdict(fx.run(_mixed(
        sha, {1: "Q1. [docs/alpha.md](docs/alpha.md 'the notes') reads \"The first rule is plain.\""}))), "PASS")
    entity = _mixed(sha, {4: "Q4. src/beta.py: yes, &quot;return FABRICATED&quot;."})
    check("shape/quote-entity-cannot-evaluate", refusal(fx.run(entity), "character reference"), refused)
    check("flip/shape-quote-entity", verdict(_patched("ENTITY_RE", never, lambda: fx.run(entity))), "PASS")

    # Answer markers: only "QN." in ASCII at column 0; a line that reads as one otherwise is refused
    lookalike = _mixed(sha, findings="Q\uff11. Smuggled answer")
    check("shape/lookalike-marker-in-findings-cannot-evaluate",
          refusal(fx.run(lookalike), "reads as an answer marker"), refused)
    check("flip/shape-marker-candidates",
          verdict(_patched("_marker_candidate", lambda line: False, lambda: fx.run(lookalike))), "PASS")
    for check_id, line in (("shape/non-ascii-digit-marker-cannot-evaluate", "Q\u0663. Smuggled answer"),
                           ("shape/zero-width-marker-cannot-evaluate", "Q\N{ZERO WIDTH SPACE}4. Smuggled answer"),
                           ("shape/indented-marker-cannot-evaluate", "  Q4. Smuggled answer"),
                           ("shape/bold-marker-cannot-evaluate", "**Q4.** Smuggled answer")):
        check(check_id, refusal(fx.run(_mixed(sha, {4: A4 + "\n" + line})), "reads as an answer marker"), refused)
    check("shape/lookalike-marker-in-fence-cannot-evaluate", refusal(fx.run(
        _mixed(sha, {2: A2.replace("```python\n", "```python\nQ\uff12.\n")})), "inside the fenced block"), refused)
    # Format padding inside the marker, at the sizes that defeated earlier raw-prefix slices of 256:
    # with "Q" + padding + "4.", 254 pads keep the digit at raw index 255, inside such a slice, and
    # push only the stop out; 255 pads and more push the digit out too. The scan now reads the whole
    # line, so every size refuses alike.
    pad = "\N{ZERO WIDTH SPACE}"
    for check_id, padding in (("shape/padded-marker-digit-254-cannot-evaluate", 254),
                              ("shape/padded-marker-digit-255-cannot-evaluate", 255),
                              ("shape/padded-marker-digit-256-cannot-evaluate", 256)):
        check(check_id, refusal(fx.run(_mixed(sha, {4: A4 + "\nQ" + pad * padding + "4. Additional answer"})),
                                "reads as an answer marker"), refused)
    mixed_formats = "\u200b\u200c\u200d\u2060\u00ad\u200e\u200f\ufeff" * 75
    check("shape/mixed-format-run-marker-cannot-evaluate",
          refusal(fx.run(_mixed(sha, {4: A4 + "\nQ" + mixed_formats + "4. Additional answer"})),
                  "reads as an answer marker"), refused)
    # The permitted prefix is consumed whole: decoration after a non-ASCII space or a format
    # character no longer feeds a length budget, however the runs interleave.
    emphasis_run = "*" * 300 + "Q4." + "*" * 300 + " Additional answer"
    for check_id, lead in (("shape/nbsp-emphasis-run-marker-cannot-evaluate", "\u00a0"),
                           ("shape/zwsp-emphasis-run-marker-cannot-evaluate", "\u200b")):
        check(check_id, refusal(fx.run(_mixed(sha, {4: A4 + "\n" + lead + emphasis_run})),
                                "reads as an answer marker"), refused)
    seen_marks = []
    for check_id, mark in (("shape/zwsp-space-run-marker-cannot-evaluate", " "),
                           ("shape/zwsp-tab-run-marker-cannot-evaluate", "\t"),
                           ("shape/zwsp-asterisk-run-marker-cannot-evaluate", "*"),
                           ("shape/zwsp-underscore-run-marker-cannot-evaluate", "_"),
                           ("shape/zwsp-hash-run-marker-cannot-evaluate", "#"),
                           ("shape/zwsp-blockquote-mark-run-marker-cannot-evaluate", ">"),
                           ("shape/zwsp-plus-run-marker-cannot-evaluate", "+"),
                           ("shape/zwsp-hyphen-run-marker-cannot-evaluate", "-"),
                           ("shape/zwsp-backslash-run-marker-cannot-evaluate", "\\"),
                           ("shape/zwsp-braille-blank-run-marker-cannot-evaluate", "\u2800")):
        seen_marks.append(mark)
        check(check_id, refusal(fx.run(_mixed(sha, {4: A4 + "\n" + pad + mark * 254 + "Q4. Additional answer"})),
                                "reads as an answer marker"), refused)
    check("shape/zwsp-decoration-run-covers-every-mark",
          "".join(sorted(seen_marks)), "".join(sorted(MARKER_DECORATION)))
    check("shape/interleaved-asterisk-zwsp-marker-cannot-evaluate",
          refusal(fx.run(_mixed(sha, {4: A4 + "\n" + "*\u200b" * 256 + "Q4. Additional answer"})),
                  "reads as an answer marker"), refused)
    check("shape/long-digit-marker-cannot-evaluate",
          refusal(fx.run(_mixed(sha, {4: A4 + "\n" + pad + "Q" + "1" * 300 + ". Additional answer"})),
                  "reads as an answer marker"), refused)
    # Default-ignorable characters outside category Cf render as nothing too: the variation selector
    # VS16 and the combining grapheme joiner.
    for check_id, joiner in (("shape/variation-selector-marker-cannot-evaluate", "\ufe0f"),
                             ("shape/combining-grapheme-joiner-marker-cannot-evaluate", "\u034f")):
        check(check_id, refusal(fx.run(_mixed(sha, {4: A4 + "\nQ" + joiner + "4. Additional answer"})),
                                "reads as an answer marker"), refused)
    # Lines that render as the marker without carrying it in source: a backslash-escaped stop,
    # decimal and named character references, emphasis inside the marker, an ordered-list mark or
    # the blank glyph U+2800 before it. The form feed and unit separator discriminate the isspace
    # clause of the prefix loop; U+E0100, a supplementary variation selector, discriminates the
    # pinned default-ignorable table's E0000..E0FFF range. The NEL (U+0085) and unit-separator
    # references discriminate the CommonMark code-point decode against the html.unescape reading,
    # which maps them to U+2026 or drops them; either reading as a marker refuses the line. The
    # semicolonless rows decode only under the legacy html.unescape reading of the whole line, and
    # the escaped-reference row is the disclosed over-refusal: CommonMark renders the escaped
    # reference literal. The C1 and literal control rows discriminate the non-whitespace Cc clause
    # of _marker_ignorable, in a reference before the marker, inside it and as a literal code point.
    # The escape-aware rows decode only under the round-9 tokenized reading (_marker_escape_aware):
    # html.unescape drops U+FFFF where CommonMark substitutes U+FFFD, and the escaped stop reads as
    # a stop only with escapes honoured; the whole-line reading leaves the backslash in the head.
    for check_id, line in (
            ("shape/escaped-stop-marker-cannot-evaluate", "Q4\\. Additional answer"),
            ("shape/decimal-reference-q-marker-cannot-evaluate", "&#81;4. Additional answer"),
            ("shape/decimal-reference-stop-marker-cannot-evaluate", "Q4&#46; Additional answer"),
            ("shape/named-reference-stop-marker-cannot-evaluate", "Q4&period; Additional answer"),
            ("shape/nbsp-reference-marker-cannot-evaluate", "&nbsp;Q4. Additional answer"),
            ("shape/nel-decimal-reference-marker-cannot-evaluate", "&#133;Q4. Additional answer"),
            ("shape/nel-hex-reference-marker-cannot-evaluate", "&#x85;Q4. Additional answer"),
            ("shape/nel-reference-after-stop-marker-cannot-evaluate", "Q4.&#133;Additional answer"),
            ("shape/unit-separator-decimal-reference-marker-cannot-evaluate", "Q4.&#31;Additional answer"),
            ("shape/unit-separator-hex-reference-marker-cannot-evaluate", "Q4.&#x1f;Additional answer"),
            ("shape/semicolonless-nbsp-prefix-marker-cannot-evaluate", "&nbspQ4. Additional answer"),
            ("shape/semicolonless-nbsp-after-stop-marker-cannot-evaluate", "Q4.&nbspAdditional answer"),
            ("shape/semicolonless-decimal-stop-marker-cannot-evaluate", "Q4&#46 Additional answer"),
            ("shape/semicolonless-hex-stop-marker-cannot-evaluate", "Q4&#x2e Additional answer"),
            ("shape/escaped-reference-marker-cannot-evaluate", "\\&#81;4. Additional answer"),
            ("shape/inner-emphasis-digit-marker-cannot-evaluate", "Q*4*. Additional answer"),
            ("shape/inner-bold-q-marker-cannot-evaluate", "**Q**4. Additional answer"),
            ("shape/bold-escaped-stop-marker-cannot-evaluate", "**Q4\\.** Additional answer"),
            ("shape/ordered-list-dot-marker-cannot-evaluate", "1. Q4. Additional answer"),
            ("shape/ordered-list-paren-marker-cannot-evaluate", "1) Q4. Additional answer"),
            ("shape/braille-blank-marker-cannot-evaluate", "\u2800Q4. Additional answer"),
            ("shape/form-feed-marker-cannot-evaluate", "\x0cQ4. Additional answer"),
            ("shape/unit-separator-marker-cannot-evaluate", "\x1fQ4. Additional answer"),
            ("shape/c1-control-reference-marker-cannot-evaluate", "&#129;Q4. Additional answer"),
            ("shape/c1-control-reference-inside-marker-cannot-evaluate", "Q&#129;4. Additional answer"),
            ("shape/literal-control-inside-marker-cannot-evaluate", "Q\x014. Additional answer"),
            ("shape/variation-selector-supplement-marker-cannot-evaluate", "Q\U000e01004. Additional answer"),
            ("shape/escape-aware-noncharacter-marker-cannot-evaluate", "Q&#65535;4\\. Additional answer"),
            ("shape/escape-aware-noncharacter-after-stop-marker-cannot-evaluate", "Q4\\.&#65535; Additional answer"),
            ("shape/escape-aware-hex-noncharacter-marker-cannot-evaluate", "Q&#xFFFF;4\\. Additional answer")):
        check(check_id, refusal(fx.run(_mixed(sha, {4: A4 + "\n" + line})), "reads as an answer marker"),
              refused)

    # Encoded, control-padded and semicolonless boundary lines: a reading shows a VERDICT or
    # QA-COMPLETE boundary where the canonical parse reads content, so the line is refused
    # wherever it stands, duplicated or not; the canonical boundary parse itself is unchanged.
    for check_id, line in (
            ("shape/encoded-verdict-line-cannot-evaluate", "VERDICT&#58; BLOCKERS FOUND"),
            ("shape/encoded-complete-line-cannot-evaluate", "QA&#45;COMPLETE"),
            ("shape/control-inside-verdict-cannot-evaluate", "VER\x01DICT: BLOCKERS FOUND"),
            ("shape/control-inside-complete-cannot-evaluate", "QA-\x01COMPLETE"),
            ("shape/semicolonless-encoded-complete-cannot-evaluate", "QA&#45COMPLETE")):
        check(check_id, refusal(fx.run(_mixed(sha, {4: A4 + "\n" + line})), "while its raw text is not"), refused)
    check("shape/encoded-verdict-inside-answer-cannot-evaluate",
          refusal(fx.run(_mixed(sha, {2: A2 + "\nVERDICT&#58; BLOCKERS FOUND"})), "while its raw text is not"),
          refused)
    check("shape/encoded-verdict-duplicate-cannot-evaluate",
          refusal(fx.run(_mixed(sha).replace("\n" + COMPLETE_LINE + "\n",
                                             "\nVERDICT&#58; NO BLOCKERS\n" + COMPLETE_LINE + "\n")),
                  "while its raw text is not"), refused)
    for check_id, sep_verdict in (("shape/verdict-separator-unit-cannot-evaluate", "VERDICT: NO\x1cBLOCKERS"),
                                  ("shape/verdict-separator-nel-cannot-evaluate", "VERDICT: NO\x85BLOCKERS")):
        check(check_id, refusal(fx.run(_mixed(sha, verdict=sep_verdict)), "whitespace separator other than"),
              refused)

    # Quote and code forms CommonMark reads that this grammar does not: refused, never read as prose
    lazy = _mixed(sha, {1: "Q1. docs/alpha.md:\n> The first rule is plain.\nunless the caller overrides it, which is "
                           "fabricated."})
    check("c2/lazy-quote-continuation-cannot-evaluate", refusal(fx.run(lazy), "directly after a blockquote line"),
          refused)
    check("flip/c2-lazy-quote-continuation",
          verdict(_patched("_lazy_continuation", lambda *a: False, lambda: fx.run(lazy))), "PASS")
    check("shape/answer-heading-after-quote-cannot-evaluate", refusal(fx.run(_delivery(
        sha, A1, A2, "Q3. docs/gamma.md:\n> Only gamma carries this exact sentence.\n" + A4)),
        "directly after a blockquote line"), refused)
    old_boundary = lambda previous: previous is not None and not previous.strip()
    after_fence = _mixed(sha, {2: "Q2. src/beta.py:\n```\n    if ready:\n```\n        return FABRICATED"})
    check("items/indented-code-after-fence-cannot-evaluate", refusal(fx.run(after_fence), "no paragraph is open"),
          refused)
    check("flip/items-indented-code-after-fence",
          verdict(_patched("_after_block_end", old_boundary, lambda: fx.run(after_fence))), "PASS")
    after_heading = _mixed(sha, findings="    fabricated_call(ready=FABRICATED)")
    check("items/indented-code-after-heading-cannot-evaluate", refusal(fx.run(after_heading), "no paragraph is open"),
          refused)
    check("flip/items-indented-code-after-heading",
          verdict(_patched("_after_block_end", old_boundary, lambda: fx.run(after_heading))), "PASS")
    list_fence = _mixed(sha, {4: "Q4. src/beta.py: yes, it reads\n- ```\n  return FABRICATED"})
    check("shape/list-marker-fence-cannot-evaluate", refusal(fx.run(list_fence), "after a list marker"), refused)
    check("flip/shape-list-marker-fence",
          verdict(_patched("_unread_fence", lambda line: False, lambda: fx.run(list_fence))), "PASS")
    check("shape/tab-fence-cannot-evaluate", refusal(fx.run(
        _mixed(sha, {4: A4 + "\n\t```\n\treturn FABRICATED"})), "after a list marker or a tab"), refused)

    # Findings: after the last answer, under one "## Findings" line, never an answer
    unrelated = _delivery(sha, "Q1. src/beta.py: yes.", "Q2. docs/alpha.md: yes.",
                          findings="MAJOR: an unrelated sentence:\n> Only gamma carries this exact sentence.",
                          verdict="VERDICT: BLOCKERS FOUND")
    check("c2/findings-not-attributed-to-last-answer-passes", verdict(fx.run(unrelated, BRIEF_TWO)), "PASS")
    check("flip/c2-findings-section",
          verdict(_patched("FINDINGS_HEADING", "## Not the findings line", lambda: fx.run(unrelated, BRIEF_TWO))), "FAIL")
    empty_last = _delivery(sha, "Q1. src/beta.py: yes.", "Q2.", findings="MAJOR: the rule reads\n> The first rule is plain.")
    report = fx.run(empty_last, BRIEF_TWO)
    check("c1/findings-evidence-not-an-answer", (verdict(report), report["coverage"]["text"]), ("PASS", "1/2"))
    check("flip/c1-findings-evidence", _patched(
        "FINDINGS_HEADING", "## Not the findings line", lambda: fx.run(empty_last, BRIEF_TWO))["coverage"]["text"], "2/2")
    steps = "MAJOR: could not read the tree; steps:\n1. open it\n2. read it\n3. fail\n4. observe the error"
    check("shape/findings-steps-never-fill-answers-cannot-evaluate", refusal(fx.run(
        _delivery(sha, *(_unverifiable(n) for n in range(1, 4)), findings=steps)), "Q4. is missing"), refused)
    report = fx.run(_delivery(sha, *(_unverifiable(n) for n in range(1, 4)), "Q4.", findings=steps,
                              verdict="VERDICT: BLOCKERS FOUND"))
    check("c1/findings-steps-not-answers-no-vote", (verdict(report), report["coverage"]["text"]), ("NO_VOTE", "0/4"))
    check("shape/answer-heading-in-findings-cannot-evaluate", refusal(fx.run(
        _mixed(sha, findings="MAJOR: steps:\nQ4. observe the error")), "inside the findings section"), refused)
    check("shape/second-findings-heading-cannot-evaluate", refusal(fx.run(
        _mixed(sha, findings="MAJOR: one.\n{}\nMINOR: two.".format(FINDINGS_HEADING))), "a second"), refused)
    check("shape/findings-reproduction-list-passes", verdict(fx.run(_delivery(
        sha, "Q1. src/beta.py: yes.", "Q2. docs/alpha.md: yes.", findings="MAJOR: steps to see it:\n1. run it\n2. see it\n3. done"),
        BRIEF_TWO)), "PASS")
    named_finding = _delivery(sha, "Q1. src/beta.py: yes.", "Q2. docs/alpha.md: yes.",
                              findings="MAJOR: docs/gamma.md: the guard reads\n```\n    if ready:\n```")
    check("c2/findings-names-bind-fails", verdict(fx.run(named_finding, BRIEF_TWO)), "FAIL")
    check("flip/c2-findings-names",
          verdict(_patched("_findings_names", lambda *a: ([], []), lambda: fx.run(named_finding, BRIEF_TWO))), "PASS")
    check("c2/findings-contrast-quotes-pass", verdict(fx.run(_delivery(
        sha, "Q1. src/beta.py: yes.", "Q2. docs/alpha.md: yes.",
        findings='MINOR: docs/alpha.md says "The first rule is plain." where src/beta.py says "    return 0"'),
        BRIEF_TWO)), "PASS")
    evidence = "Q4. src/beta.py: yes, it returns 0.\n{}\n```\nOnly gamma carries this exact sentence.\n```"
    check("c2/sub-heading-keeps-answer-binding-fails",
          verdict(fx.run(_mixed(sha, {4: evidence.format("### Evidence")}))), "FAIL")
    check("c2/grade-word-line-keeps-answer-binding-fails",
          verdict(fx.run(_mixed(sha, {4: evidence.format("MINOR detail, the guard reads:")}))), "FAIL")

    # C2 verbatim
    check("c2/exact-delivery-passes", verdict(fx.run(_mixed(sha))), "PASS")
    joined = _mixed(sha, {1: 'Q1. docs/alpha.md reads "The first rule is plain. The second rule follows it."'})
    report = fx.run(joined)
    check("c2/joined-lines-fails", (verdict(report), report["quotes_failed"]), ("FAIL", 1))
    check("flip/c2-verbatim-joined",
          verdict(_patched("_verbatim_in", _normalised_containment, lambda: fx.run(joined))), "PASS")
    check("flip/c2-inline-quotes", verdict(_patched("INLINE_QUOTE_PAIRS", (), lambda: fx.run(joined))), "PASS")
    dropped = _mixed(sha, {1: "Q1. docs/alpha.md:\n```\nThe first rule is plain.\nThe third rule closes the set.\n```"})
    check("c2/dropped-middle-line-fails", verdict(fx.run(dropped)), "FAIL")
    check("flip/c2-fenced-blocks", verdict(_patched("FENCE_RE", never, lambda: fx.run(dropped))), "PASS")
    other = _mixed(sha, {1: 'Q1. The rule reads "Only gamma carries this exact sentence."'})
    report = fx.run(other)
    reason = " ".join(report["reasons"])
    check("c2/other-file-than-question-fails",
          (verdict(report), "docs/alpha.md" in reason, "docs/gamma.md" in reason), ("FAIL", True, True))
    check("flip/c2-named-file",
          verdict(_patched("_quote_targets", lambda *a: ([], [], "none", []), lambda: fx.run(other))), "PASS")
    precedence = _mixed(sha, {3: 'Q3. docs/alpha.md says "Only gamma carries this exact sentence."'})
    check("c2/answer-file-precedence-fails", verdict(fx.run(precedence)), "FAIL")
    check("flip/c2-answer-names",
          verdict(_patched("_answer_names", lambda *a: ([], []), lambda: fx.run(precedence))), "PASS")
    absent = _mixed(sha, {3: 'Q3. docs/missing.md says "Only gamma carries this exact sentence."'})
    check("c2/answer-names-absent-path-fails", verdict(fx.run(absent)), "FAIL")
    found_only = lambda text, tree: ([path for path, _ in _path_tokens(text, tree) if path], [])
    check("flip/c2-answer-absent-path", verdict(_patched("_named_paths", found_only, lambda: fx.run(absent))), "PASS")
    rewrapped = _mixed(sha, {1: "Q1. docs/alpha.md:\n```\nThe second rule\nfollows it.\n```"})
    report = fx.run(rewrapped)
    diagnostic = report["items"][0]["quotes"][0]["normalised_match"] if report["items"] else None
    check("c2/rewrapped-fails-with-diagnostic",
          (verdict(report), diagnostic and diagnostic["file"], diagnostic and diagnostic["excerpt"]),
          ("FAIL", "docs/alpha.md", "The second rule follows it."))
    check("flip/c2-normalised-is-not-a-pass",
          verdict(_patched("_verbatim_in", _normalised_containment, lambda: fx.run(rewrapped))), "PASS")
    excerpt = _mixed(sha, {1: 'Q1. docs/alpha.md: "second rule follows"'})
    check("c2/mid-line-excerpt-passes", verdict(fx.run(excerpt)), "PASS")
    check("flip/c2-excerpt", verdict(_patched("_verbatim_in", _whole_line_only, lambda: fx.run(excerpt))),
          "FAIL")
    indented = _mixed(sha)
    check("c2/fenced-indentation-exact-passes", verdict(fx.run(indented)), "PASS")
    check("flip/c2-fenced-indentation",
          verdict(_patched("_fence_content_line", lambda line, indent: line.lstrip(), lambda: fx.run(indented))),
          "FAIL")
    check("c2/fenced-indentation-changed-fails",
          verdict(fx.run(_mixed(sha, {2: "Q2. src/beta.py:\n```\nif ready:\n    return 1\n```"}))), "FAIL")
    check("c2/indented-fence-strips-only-its-own-indent-passes", verdict(fx.run(
        _mixed(sha, {2: "Q2. src/beta.py:\n  ```\n      if ready:\n          return 1\n  ```"}))), "PASS")
    blockquote = _mixed(sha, {1: "Q1. docs/alpha.md:\n> The first rule is plain\n> and the second follows."})
    check("c2/blockquote-reworded-fails", verdict(fx.run(blockquote)), "FAIL")
    check("flip/c2-blockquote", verdict(_patched("BLOCKQUOTE_RE", never, lambda: fx.run(blockquote))), "PASS")
    check("c2/blockquote-exact-passes", verdict(fx.run(
        _mixed(sha, {1: "Q1. docs/alpha.md:\n> The first rule is plain.\n> The second rule follows it."}))), "PASS")
    check("c2/curly-quote-fabricated-fails", verdict(fx.run(
        _mixed(sha, {1: "Q1. docs/alpha.md reads “The first rule is optional.”"}))), "FAIL")
    check("c2/code-span-not-a-quote-passes", verdict(fx.run(_mixed(
        sha, {1: 'Q1. docs/alpha.md: the flag `"made up"` is not a quote here; "The first rule is plain."'}))), "PASS")
    check("c2/escape-added-fails", verdict(fx.run(
        _mixed(sha, {2: "Q2. src/beta.py:\n```\n    if ready\\:\n```"}))), "FAIL")
    check("c2/verdict-line-quote-checked-fails", verdict(fx.run(
        _mixed(sha, verdict='VERDICT: NO BLOCKERS "never in the tree"'))), "FAIL")
    row = _delivery(sha, 'Q1. The row reads "| pattern-self-match.py | matches its own pattern |"')
    report = fx.run(row, BRIEF_README)
    check("c2/bare-name-binds-by-basename-passes",
          (verdict(report), report["items"][0]["quotes"][0]["found_in"] if report["items"] else None),
          ("PASS", [".preview/README.md"]))
    root_only = lambda token, tree: [token] if token in tree.blobs else []
    check("flip/c2-bare-name-root-only",
          verdict(_patched("_basename_bindings", root_only, lambda: fx.run(row, BRIEF_README))), "FAIL")
    check("c2/body-quoted-text-names-nothing-passes", verdict(fx.run(_delivery(
        sha, 'Q1. The row, as read:\n"| pattern-self-match.py | matches its own pattern |"'), BRIEF_README)), "PASS")
    check("flip/c2-quoted-text-names-nothing",
          verdict(_patched("_names_text", lambda line, spans: line, lambda: fx.run(row, BRIEF_README))), "FAIL")
    neither = _delivery(sha, 'Q1. The row reads "Only gamma carries this exact sentence."')
    report = fx.run(neither, BRIEF_README)
    reason = " ".join(report["reasons"])
    check("c2/bare-name-other-basename-fails",
          (verdict(report), ".preview/README.md" in reason, "docs/gamma.md" in reason), ("FAIL", True, True))
    report = _patched("_basename_bindings", lambda token, tree: [], lambda: fx.run(neither, BRIEF_README))
    check("flip/c2-bare-name-binding", (verdict(report), ".preview/README.md" in " ".join(report["reasons"])),
          ("FAIL", False))
    check("c2/slash-path-binds-exactly-fails", verdict(fx.run(
        _delivery(sha, 'Q1. The line reads "Only the root readme carries this line."'), BRIEF_SLASH)), "FAIL")

    # C1: an item counts as answered only with answer content
    empty = _delivery(sha, "Q1. ")
    report = fx.run(empty, BRIEF_ONE)
    check("c1/empty-item-not-answered", (verdict(report), report["coverage"]["text"]), ("NO_VOTE", "0/1"))
    answered_always = lambda item, question, tree: "unverifiable" if item["unverifiable"] else "answered"
    check("flip/c1-answer-content",
          verdict(_patched("_answer_state", answered_always, lambda: fx.run(empty, BRIEF_ONE))), "PASS")
    check("c1/file-name-only-not-answered", verdict(fx.run(_delivery(sha, "Q1. docs/alpha.md"), BRIEF_ONE)), "NO_VOTE")
    restated = _delivery(sha, "Q1. Inspect docs/alpha.md and say whether it states a rule.")
    check("c1/question-restated-not-answered", verdict(fx.run(restated, BRIEF_ONE)), "NO_VOTE")
    check("c1/short-answer-is-answered",
          verdict(fx.run(_delivery(sha, "Q1. docs/alpha.md: yes, three rules."), BRIEF_ONE)), "PASS")

    # Names never widen: every file reference of an answer resolves, or the item fails
    absent_question = _delivery(sha, 'Q1. The rule reads "The first rule is plain."')
    check("c2/question-names-absent-path-fails", verdict(fx.run(absent_question, BRIEF_ABSENT)), "FAIL")
    check("flip/c2-question-absent-path",
          verdict(_patched("_named_paths", found_only, lambda: fx.run(absent_question, BRIEF_ABSENT))), "PASS")
    check("c2/question-bare-absent-name-fails", verdict(fx.run(absent_question, BRIEF_BARE_ABSENT)), "FAIL")
    plus = _delivery(sha, 'Q1. nonexistent/source+test.py: "The first rule is plain."')
    report = fx.run(plus, BRIEF_OPEN)
    check("c2/answer-absent-name-any-characters-fails",
          (verdict(report), "nonexistent/source+test.py" in " ".join(report["reasons"])), ("FAIL", True))
    check("c2/answer-absent-bare-name-fails", verdict(fx.run(
        _delivery(sha, 'Q1. alpha_old.md: "The first rule is plain."'), BRIEF_OPEN)), "FAIL")
    check("c2/answer-absent-path-without-extension-fails", verdict(fx.run(
        _delivery(sha, 'Q1. docs/retired/ALPHA: "The first rule is plain."'), BRIEF_OPEN)), "FAIL")
    unquoted = _delivery(sha, "Q1. src/beta.py: yes.", "Q2. docs/retired/alpha.md states it.")
    check("c2/answer-absent-name-without-quote-fails", verdict(fx.run(unquoted, BRIEF_TWO)), "FAIL")
    targets = _quote_targets
    check("flip/c2-answer-absent-name",
          verdict(_patched("_quote_targets", lambda *a: targets(*a)[:3] + ([],), lambda: fx.run(unquoted, BRIEF_TWO))),
          "PASS")
    check("c2/question-named-absent-reference-passes", verdict(fx.run(
        _delivery(sha, "Q1. No: docs/retired/alpha.md is absent from the pinned tree."), BRIEF_ABSENT_ASKED)), "PASS")
    check("c2/non-file-tokens-pass", verdict(fx.run(_delivery(
        sha, 'Q1. **docs/alpha.md:1**: yes, and/or `PinnedTree.read`, e.g. 2/2 or 1/2.5 of src/gen_*.py at '
             '`<clone>/src/x.py`, see https://example.invalid/a.py; "The first rule is plain."'), BRIEF_OPEN)), "PASS")
    check("c2/bold-name-binds-fails", verdict(fx.run(
        _delivery(sha, 'Q1. **docs/gamma.md**: "The first rule is plain."'), BRIEF_OPEN)), "FAIL")
    check("c2/bare-root-name-binds-fails", verdict(fx.run(
        _delivery(sha, 'Q1. The line reads "The first rule is plain."'), BRIEF_VERSION)), "FAIL")
    check("c2/absolute-repository-path-normalised-fails", verdict(fx.run(_mixed(
        sha, {1: 'Q1. {}/docs/gamma.md reads "The first rule is plain."'.format(fx.repo)}))), "FAIL")
    repository_path = _mixed(sha, {1: 'Q1. {}/docs/alpha.md reads "The first rule is plain."'.format(fx.repo)})
    check("c2/absolute-repository-path-normalised-passes", verdict(fx.run(repository_path)), "PASS")
    check("flip/c2-absolute-repository-path",
          verdict(_patched("_repository_path", lambda token, tree: None, lambda: fx.run(repository_path))), "FAIL")
    check("c2/absolute-outside-path-fails", verdict(fx.run(_mixed(
        sha, {1: 'Q1. /elsewhere/docs/alpha.md reads "The first rule is plain."'}))), "FAIL")
    marker = _delivery(sha, 'Q1. The sentence reads "Only gamma carries this exact sentence."')
    check("questions/marker-starts-the-run-passes", verdict(fx.run(marker, BRIEF_MARKER)), "PASS")
    check("flip/questions-marker",
          verdict(_patched("QUESTIONS_MARKER_RE", never, lambda: fx.run(marker, BRIEF_MARKER))), "FAIL")
    check("questions/no-numbered-question-cannot-evaluate", refusal(fx.run(
        _mixed(sha), BRIEF_MIXED.split("\n\n1.", 1)[0] + "\n"), "numbers no question"), refused)

    # Quote grammar
    continuation = _mixed(sha, {2: "Q2. src/beta.py:\n    ```\n    TIMEOUT = 999  # fabricated\n    ```"})
    check("c2/list-continuation-fence-read-fails", verdict(fx.run(continuation)), "FAIL")
    check("flip/c2-list-continuation-fence",
          verdict(_patched("FENCE_RE", re.compile(r"( {0,3})(`{3,}|~{3,})(.*)$"), lambda: fx.run(continuation))), "PASS")
    example = _mixed(sha, {1: 'Q1. docs/alpha.md: a word cut at a slice edge (e.g., "knot" cut to "not") is read; '
                              '"The first rule is plain."'})
    check("c2/inline-example-not-a-quote-passes", verdict(fx.run(example)), "PASS")
    check("flip/c2-inline-example", verdict(_patched("EXAMPLE_OPEN_RE", never, lambda: fx.run(example))), "FAIL")
    unclosed = _mixed(sha, {4: 'Q4. src/beta.py: yes (e.g. it reads "return FABRICATED" when not ready.'})
    check("c2/unclosed-example-masks-nothing-fails", verdict(fx.run(unclosed)), "FAIL")
    to_end = lambda line: [(match.start(), len(line)) for match in EXAMPLE_OPEN_RE.finditer(line)]
    check("flip/c2-example-needs-its-close", verdict(_patched("_example_spans", to_end, lambda: fx.run(unclosed))),
          "PASS")
    restatement = _mixed(sha, {4: 'Q4. src/beta.py: yes (i.e. "return FABRICATED").'})
    check("c2/i-e-restatement-is-read-fails", verdict(fx.run(restatement)), "FAIL")
    check("flip/c2-example-openers", verdict(_patched(
        "EXAMPLE_OPEN_RE", re.compile(r"\((?:e\.g\.|i\.e\.|for example)", re.I), lambda: fx.run(restatement))), "PASS")
    check("c2/unmatched-curly-opener-hides-nothing-fails", verdict(fx.run(
        _mixed(sha, {4: 'Q4. src/beta.py: “ aside; "return FABRICATED"'}))), "FAIL")
    indented = _mixed(sha, {2: "Q2. src/beta.py:\n\n        TIMEOUT = 999"})
    check("items/indented-code-block-cannot-evaluate", verdict(fx.run(indented)), "CANNOT_EVALUATE")
    check("flip/items-indented-code-block",
          verdict(_patched("_indented_code_start", lambda *a: False, lambda: fx.run(indented))), "PASS")
    check("c2/inch-mark-opens-nothing-passes",
          verdict(fx.run(_mixed(sha, {1: 'Q1. docs/alpha.md: a 5" display shows "The first rule is plain."'}))), "PASS")
    crlf_tree = _mixed(sha, {1: "Q1. docs/crlf.md:\n```\nStored with CRLF line ends.\nSecond stored line.\n```"})
    check("c2/crlf-tree-file-passes", verdict(fx.run(crlf_tree)), "PASS")
    check("flip/c2-crlf-tree-file",
          verdict(_patched("_verbatim_in", lambda content, data: data in content, lambda: fx.run(crlf_tree))), "FAIL")
    crlf_delivery = _mixed(sha).replace("\n", "\r\n")
    check("input/crlf-delivery-passes", verdict(fx.run(crlf_delivery)), "PASS")
    check("flip/input-crlf",
          verdict(_patched("_lines", lambda text: list(enumerate(text.split("\n"), 1)), lambda: fx.run(crlf_delivery))),
          "CANNOT_EVALUATE")
    capped = _mixed(sha)
    check("input/quote-cap-cannot-evaluate", verdict(_patched("MAX_QUOTES", 3, lambda: fx.run(capped))), "CANNOT_EVALUATE")
    check("input/size-cap-cannot-evaluate",
          verdict(_patched("MAX_INPUT_BYTES", len(capped.encode("utf-8")) - 1, lambda: fx.run(capped))),
          "CANNOT_EVALUATE")
    check("tree/size-cap-cannot-evaluate", verdict(_patched(
        "MAX_TREE_BYTES", 8, lambda: fx.run(_mixed(sha, findings="> The first rule is plain.")))), "CANNOT_EVALUATE")

    # Runtime: adversarial deliveries at the 16 MiB cap, each in a capped child
    check("runtime/inch-mark-run-at-cap-bounded",
          _capped_run(fx, _padded(_mixed(sha), "text 5", '5"', "\nVERDICT:"), "linear"), (0, "PASS"))
    # The line holds a quote, so its code spans are scanned (a line with no double quote skips that scan).
    backticks = _padded(_mixed(sha), 'text "ready" ', "`", "\nVERDICT:")
    check("runtime/backtick-run-at-cap-bounded",
          (len(backticks.encode("utf-8")), _capped_run(fx, backticks, "linear")), (MAX_INPUT_BYTES, (0, "PASS")))
    check("flip/runtime-backtick-run-backtracking", _capped_run(fx, backticks, "backtracking"), "timeout")
    spaces = _padded(_mixed(sha).replace("VERDICT: NO BLOCKERS\n", ""), "VERDICT: x", " ", "\n" + COMPLETE_LINE)
    check("runtime/verdict-whitespace-at-cap-bounded",
          (len(spaces.encode("utf-8")), _capped_run(fx, spaces, "linear")), (MAX_INPUT_BYTES, (0, "PASS")))
    check("flip/runtime-verdict-whitespace-backtracking", _capped_run(fx, spaces, "backtracking"), "timeout")
    zwsp_run = _padded(_mixed(sha), "\u200b", "\u200b", "\nVERDICT:")
    check("runtime/marker-prefix-run-at-cap-bounded",
          (len(zwsp_run.encode("utf-8")), _capped_run(fx, zwsp_run, "linear")), (MAX_INPUT_BYTES, (0, "PASS")))
    digit_run = _padded(_mixed(sha), "\u200bQ", "1", "\nVERDICT:")
    check("runtime/marker-digit-run-at-cap-bounded",
          (len(digit_run.encode("utf-8")), _capped_run(fx, digit_run, "linear")), (MAX_INPUT_BYTES, (0, "PASS")))
    # U+FDFA normalises to 18 characters, so NFKC over a whole 16 MiB line of it would multiply the
    # text; the scan normalises the marker-shaped head only (_marker_material).
    ligature_run = _padded(_mixed(sha), "Q", "\ufdfa", "\nVERDICT:")
    check("runtime/marker-ligature-run-at-cap-bounded",
          (len(ligature_run.encode("utf-8")), _capped_run(fx, ligature_run, "linear")),
          (MAX_INPUT_BYTES, (0, "PASS")))
    bare_ligature = _padded(_mixed(sha), "\ufdfa", "\ufdfa", "\nVERDICT:")
    check("runtime/ligature-run-at-cap-bounded",
          (len(bare_ligature.encode("utf-8")), _capped_run(fx, bare_ligature, "linear")),
          (MAX_INPUT_BYTES, (0, "PASS")))
    # A reference-dense line: about 2.8 million NEL references at the 16 MiB cap. A runtime bound
    # only (performance coverage): it shows the readings stay linear in the line inside the cap,
    # not that any decode runs; decode discrimination lives in the decode/ and shape/ fixtures.
    reference_run = _padded(_mixed(sha), "text ", "&#133;", "\nVERDICT:")
    check("runtime/reference-run-at-cap-bounded",
          (len(reference_run.encode("utf-8")), _capped_run(fx, reference_run, "linear")),
          (MAX_INPUT_BYTES, (0, "PASS")))

    # The deterministic head bound: a recording seam shows the NFKC input is the head only (marker
    # material up to and including the first non-material character), whatever the host speed;
    # reverting to whole-remainder normalisation feeds the seam the entire line and fails these.
    def _normalised_heads(delivery, head):
        line = next((text for _, text in _lines(delivery) if text.startswith(head)), "")
        seen = []
        recorder = lambda form, text: seen.append(text) or unicodedata.normalize(form, text)
        _patched("_marker_normalize", recorder, lambda: _marker_candidate(line))
        return seen

    check("runtime/marker-ligature-nfkc-head-only", _normalised_heads(ligature_run, "Q\ufdfa"), ["Q\ufdfa"])
    check("runtime/ligature-nfkc-head-only", _normalised_heads(bare_ligature, "\ufdfa"), ["\ufdfa"])

    # Pin, decode, git
    mismatch = _mixed(OTHER_SHA)
    check("pin/sha-mismatch-cannot-evaluate", verdict(fx.run(mismatch)), "CANNOT_EVALUATE")
    check("flip/pin-match", verdict(_patched("_pin_matches", lambda *a: True, lambda: fx.run(mismatch))), "PASS")
    check("pin/short-sha-cannot-evaluate", verdict(fx.run(_mixed(sha[:12]))), "CANNOT_EVALUATE")
    repeated = BRIEF_MIXED.replace("BRIEF-PIN: {pin}\n", "BRIEF-PIN: {pin}\nBRIEF-PIN: {pin}\n")
    check("pin/repeated-identical-brief-pin-cannot-evaluate",
          verdict(fx.run(_mixed(sha), brief=repeated)), "CANNOT_EVALUATE")
    set_pins = lambda text: next(iter({m.group(1).strip() for _, line in _lines(text) if (m := PIN_LINE_RE.match(line))}))
    check("flip/pin-repeated-lines",
          verdict(_patched("_brief_pin", set_pins, lambda: fx.run(_mixed(sha), brief=repeated))), "PASS")
    tree_id = fx._git("rev-parse", "HEAD^{tree}").strip()
    tree_pin = _mixed(tree_id)
    check("pin/tree-id-cannot-evaluate", verdict(fx.run(tree_pin, pin=tree_id)), "CANNOT_EVALUATE")
    check("flip/pin-commit-only",
          verdict(_patched("_require_commit", lambda *a: None, lambda: fx.run(tree_pin, pin=tree_id))), "PASS")
    check("pin/missing-brief-pin-cannot-evaluate",
          verdict(fx.run(_mixed(sha), brief=BRIEF_MIXED.replace("BRIEF-PIN", "PIN"))), "CANNOT_EVALUATE")
    undecodable = _mixed(sha).encode("utf-8").replace(b"ends beta", b"ends \xff beta")
    check("decode/undecodable-delivery-cannot-evaluate", verdict(fx.run(undecodable)), "CANNOT_EVALUATE")
    check("flip/decode-strict",
          verdict(_patched("_decode", lambda raw, label: raw.decode("utf-8", "replace"),
                           lambda: fx.run(undecodable))), "PASS")
    check("decode/unknown-entity-name-stays-literal",
          (_marker_rendered("&notit;"), _marker_rendered("&ampx;")), ("&notit;", "&ampx;"))
    # The numeric decode bounds (_reference_commonmark): zero, both surrogate ends and their
    # neighbours, the last code point, the first value past it and 9999999 (seven digits: inside
    # the grammar bound, past the value ceiling), as decimal and hexadecimal references alike,
    # decode to U+FFFD or the code point without raising; a run longer than the grammar bound
    # never reaches a decoder (_undecodable_reference).
    bounds = ((0, "\ufffd"), (1, "\x01"), (0xd7ff, "\ud7ff"), (0xd800, "\ufffd"), (0xdfff, "\ufffd"),
              (0xe000, "\ue000"), (0x10ffff, "\U0010ffff"), (0x110000, "\ufffd"), (9999999, "\ufffd"))
    check("decode/numeric-reference-bounds",
          [(_marker_rendered("&#{};".format(point)), _marker_rendered("&#x{:x};".format(point)))
           for point, _ in bounds],
          [(expected, expected) for _, expected in bounds])
    check("git/unresolvable-sha-cannot-evaluate",
          refusal(fx.run(_mixed(OTHER_SHA), pin=OTHER_SHA), "git cat-file"), refused)
    plain = fx.base / "plain"
    plain.mkdir()
    check("git/not-a-repository-cannot-evaluate",
          refusal(fx.run(_mixed(sha), repo=plain), "git cat-file"), refused)
    check("input/directory-delivery-cannot-evaluate",
          verdict(evaluate(fx.files("x")[0], str(plain), str(fx.repo))[0]), "CANNOT_EVALUATE")

    # Object reads: ambient GIT_ variables, replace refs and rewritten objects
    ambient = _mixed(sha)
    saved_dir = os.environ.get("GIT_DIR")
    os.environ["GIT_DIR"] = str(plain)
    try:
        check("git/ambient-git-dir-ignored", verdict(fx.run(ambient)), "PASS")
        check("flip/git-ambient-env",
              verdict(_patched("_git_env", lambda: dict(os.environ), lambda: fx.run(ambient))), "CANNOT_EVALUATE")
    finally:
        if saved_dir is None:
            os.environ.pop("GIT_DIR", None)
        else:
            os.environ["GIT_DIR"] = saved_dir
    gamma_oid = fx._git("rev-parse", "HEAD:docs/gamma.md").strip()
    forged = fx._git("hash-object", "-w", str(fx.base / "forged.txt")) if (
        fx.base / "forged.txt").write_text("A forged gamma sentence.\n", encoding="utf-8") else ""
    fx._git("replace", gamma_oid, forged.strip())
    replaced = _mixed(sha, {3: 'Q3. docs/gamma.md says "A forged gamma sentence."'})
    check("git/replace-ref-ignored-fails", verdict(fx.run(replaced)), "FAIL")

    def honoring(repo, *args, stdin=None):
        result = subprocess.run(["git", "-c", "gc.auto=0", "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false",
                                 "-C", str(repo), *args], input=stdin, capture_output=True, timeout=TIMEOUT,
                                env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
                                check=False)
        if result.returncode != 0:
            raise CannotEvaluate("git {} failed".format(args[0]))
        return result.stdout

    check("flip/git-replace-objects", verdict(_patched("_git", honoring, lambda: fx.run(replaced))), "CANNOT_EVALUATE")
    check("flip/git-replace-objects-unhashed",
          verdict(_patched("_git", honoring, lambda: _patched("_verified_blob", lambda path, oid, content: content,
                                                               lambda: fx.run(replaced)))), "PASS")
    fx._git("replace", "-d", gamma_oid)
    loose = fx.repo / ".git" / "objects" / gamma_oid[:2] / gamma_oid[2:]
    body = "A rewritten gamma sentence.\n".encode("utf-8")
    loose.chmod(0o644)
    original = loose.read_bytes()
    loose.write_bytes(zlib.compress(b"blob %d\0" % len(body) + body))
    try:
        rewritten = _mixed(sha, {3: 'Q3. docs/gamma.md says "A rewritten gamma sentence."'})
        check("git/rewritten-object-cannot-evaluate", verdict(fx.run(rewritten)), "CANNOT_EVALUATE")
        check("flip/git-object-hash",
              verdict(_patched("_verified_blob", lambda path, oid, content: content, lambda: fx.run(rewritten))), "PASS")
        # Reached only through the whole-tree cat-file --batch read: the answer and its question name no file.
        whole_tree = _delivery(sha, 'Q1. The tree holds "A rewritten gamma sentence."')
        check("git/rewritten-object-whole-tree-cannot-evaluate",
              refusal(fx.run(whole_tree, BRIEF_OPEN), "does not hash to its id"), refused)
        check("flip/git-object-hash-whole-tree", verdict(_patched(
            "_verified_blob", lambda path, oid, content: content, lambda: fx.run(whole_tree, BRIEF_OPEN))), "PASS")
    finally:
        loose.write_bytes(original)

    # The pinned tree, never the working tree
    (fx.repo / "docs" / "alpha.md").write_text("A fabricated working-tree sentence.\n", encoding="utf-8")
    working = _mixed(sha, {1: 'Q1. docs/alpha.md reads "A fabricated working-tree sentence."'})
    check("tree/working-tree-text-fails", verdict(fx.run(working)), "FAIL")
    check("flip/tree-pinned-only",
          verdict(_patched("_blob_bytes", lambda tree, path: (Path(tree.repo) / path).read_bytes(),
                           lambda: fx.run(working))), "PASS")

    # The command line: exit codes, one JSON object on stdout, one summary line on stderr
    script = str(Path(__file__).resolve())
    for check_id, delivery, want_code, want_verdict in (
            ("cli/pass-exit-0", _mixed(sha), 0, "PASS"),
            ("cli/fail-exit-1", joined, 1, "FAIL"),
            ("cli/no-vote-exit-1", all_unverifiable, 1, "NO_VOTE"),
            ("cli/cannot-evaluate-exit-2", mismatch, 2, "CANNOT_EVALUATE")):
        brief_path, delivery_path = fx.files(delivery)
        result = subprocess.run([sys.executable, "-I", "-B", script, "--brief", brief_path,
                                 "--delivery", delivery_path, "--repo", str(fx.repo)],
                                capture_output=True, text=True, timeout=TIMEOUT, check=False)
        try:
            shown = json.loads(result.stdout)["verdict"]
        except (ValueError, KeyError, TypeError):
            shown = None
        check(check_id, (result.returncode, shown, len(result.stderr.splitlines())), (want_code, want_verdict, 1))
    result = subprocess.run([sys.executable, "-I", "-B", script, "--brief"], capture_output=True, text=True,
                            timeout=TIMEOUT, check=False)
    check("cli/usage-exit-2", result.returncode, 2)
    result = subprocess.run([sys.executable, "-I", "-B", script], capture_output=True, text=True,
                            timeout=TIMEOUT, check=False)
    check("cli/bare-ok-exit-0",
          (result.returncode, len(result.stdout.splitlines()), result.stdout.startswith("check_verifier_delivery: OK"),
           result.stderr), (0, 1, True, ""))
    # Over-long numeric references, through the CLI end to end: the bounded pre-scan refuses the
    # line before any decode (html.unescape converts a decimal digit run through int(), which
    # raises past the interpreter conversion limit, sys.get_int_max_str_digits(), 4300 digits),
    # so each run exits 2 with the JSON report on stdout naming the line and the cause. With the
    # pre-scan removed, the three decimal rows crash the child instead (exit 1, no JSON) and the
    # hexadecimal row passes: each row is red without the pre-scan.
    for check_id, reference in (
            ("decode/overlong-decimal-reference-cli-cannot-evaluate", "&#" + "9" * 4301 + ";"),
            ("decode/overlong-semicolonless-decimal-cli-cannot-evaluate", "&#" + "9" * 4301),
            ("decode/overlong-leading-zero-decimal-cli-cannot-evaluate", "&#" + "0" * 4299 + "81;"),
            ("decode/overlong-hex-reference-cli-cannot-evaluate", "&#x" + "f" * 4301 + ";")):
        brief_path, delivery_path = fx.files(_mixed(sha, {4: A4 + "\nNote " + reference + " end"}))
        result = subprocess.run([sys.executable, "-I", "-B", script, "--brief", brief_path,
                                 "--delivery", delivery_path, "--repo", str(fx.repo)],
                                capture_output=True, text=True, timeout=TIMEOUT, check=False)
        try:
            shown = json.loads(result.stdout)
            got = (result.returncode, shown["verdict"],
                   "digit run is longer than the grammar bound" in " ".join(shown["reasons"]))
        except (ValueError, KeyError, TypeError):
            got = (result.returncode, None, False)
        check(check_id, got, (2, "CANNOT_EVALUATE", True))
    check("decode/overlong-reference-in-fence-cannot-evaluate",
          refusal(fx.run(_mixed(sha, {2: "Q2. src/beta.py:\n```\n&#" + "9" * 4301 + ";\n```"})),
                  "digit run is longer than the grammar bound"), refused)
    check("bare/static-tables-valid", _static_config_problems(), [])
    check("bare/broken-grammar-refused", bool(_patched("ANSWER_HEAD_RE", never, _static_config_problems)), True)
    check("bare/broken-quote-pairs-refused",
          bool(_patched("INLINE_QUOTE_PAIRS", (('"',),), _static_config_problems)), True)
    shrunk = tuple(pair for pair in DEFAULT_IGNORABLE_RANGES if pair not in ((0x180B, 0x180F), (0xE0000, 0xE0FFF)))
    check("bare/ignorable-range-deletion-refused",
          bool(_patched("DEFAULT_IGNORABLE_RANGES", shrunk, _static_config_problems)), True)
    without_cc = lambda char: unicodedata.category(char) == "Cf" or ord(char) in DEFAULT_IGNORABLE
    check("bare/missing-cc-clause-refused",
          any("_marker_ignorable" in problem
              for problem in _patched("_marker_ignorable", without_cc, _static_config_problems)), True)


# The 8a61dfb9 forms of the two scanners, kept only so the runtime checks can show the old forms exceed
# the cap their replacements meet. Never used by an evaluation.
_BACKTRACKING_CODE_SPAN_RE = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)")
_BACKTRACKING_VERDICT_RE = re.compile(r"[ \t]*(?:\*\*)?VERDICT(?:\*\*)?[ \t]*:(?:\*\*)?[ \t]*(.*?)[ \t]*$", re.I)


def _backtracking_code_spans(line):
    return [match.span() for match in _BACKTRACKING_CODE_SPAN_RE.finditer(line)]


def _backtracking_verdict_of(line):
    match = _BACKTRACKING_VERDICT_RE.match(line)
    return match.group(1) if match else None


# The capped child for the runtime checks: its own address-space limit, then the tool's main(), with
# the scanners optionally swapped for their 8a61dfb9 forms. It is passed to the interpreter with -c.
CAPPED_CHILD = (
    "import resource, sys\n"
    "limit = int(sys.argv[2])\n"
    "resource.setrlimit(resource.RLIMIT_AS, (limit, limit))\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import check_verifier_delivery as tool\n"
    "if sys.argv[3] == 'backtracking':\n"
    "    tool._code_spans = tool._backtracking_code_spans\n"
    "    tool._verdict_of = tool._backtracking_verdict_of\n"
    "sys.exit(tool.main(sys.argv[4:]))\n")
CHILD_ADDRESS_LIMIT = 2 * 1024 * 1024 * 1024
RUNTIME_CAP_SECONDS = 20


def _capped_run(fx, delivery, scanners):
    """(exit code, verdict) of the tool on delivery in a capped child, or "timeout" past the cap."""
    brief_path, delivery_path = fx.files(delivery)
    try:
        result = subprocess.run([sys.executable, "-I", "-B", "-c", CAPPED_CHILD, str(Path(__file__).resolve().parent),
                                 str(CHILD_ADDRESS_LIMIT), scanners, "--brief", brief_path, "--delivery", delivery_path,
                                 "--repo", str(fx.repo)], capture_output=True, text=True,
                                timeout=RUNTIME_CAP_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return "timeout"
    try:
        return result.returncode, json.loads(result.stdout)["verdict"]
    except (ValueError, KeyError, TypeError):
        return result.returncode, None


def _padded(text, line_head, fill, anchor):
    """text with one line of line_head plus fill characters inserted before its last anchor (a line
    start, "\n" included), sized so the whole delivery is exactly MAX_INPUT_BYTES."""
    head, sep, tail = text.rpartition(anchor)
    room = MAX_INPUT_BYTES - len((head + "\n" + line_head + "\n" + sep + tail).encode("utf-8"))
    width = len(fill.encode("utf-8"))
    return head + "\n" + line_head + fill * (room // width) + "y" * (room % width) + "y\n" + sep[1:] + tail \
        if room > width else text


def _expected_check_ids():
    import tomllib
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc), file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids) \
                    and len(set(ids)) == len(ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(SUITE_ID, CHECKS_MANIFEST),
          file=sys.stderr)
    return None


def self_test(report_path=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    if report_path is not None:
        # The execution report is finalized at interpreter exit (tools/_selftest_exit_report.py).
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    from _git_fixture_env import fixture_git_lifecycle
    try:
        with fixture_git_lifecycle(), tempfile.TemporaryDirectory(prefix="verifier-delivery-selftest-") as raw:
            _self_test_cases(_Fixture(Path(raw)))
    except (CannotEvaluate, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print("SELF-TEST HARNESS ERROR: {}".format(exc), file=sys.stderr)
        return 2
    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("SELF-TEST FAIL:")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: {} unique checks executed (each rule red or green on its fixture; a flip/ check "
          "patches a rule out and shows the result change, for the guards the docstring's SELF-TEST COVERAGE "
          "names); execution set reconciled against tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


USAGE = ("usage: check_verifier_delivery.py --brief FILE --delivery FILE --repo DIR\n"
         "       check_verifier_delivery.py [--self-test] [--execution-report ABS_PATH]")
# The bare run's probes of the declared grammar: (pattern name, line, whether it must match).
GRAMMAR_PROBES = (
    ("ANSWER_HEAD_RE", "Q1. answer", True), ("ANSWER_HEAD_RE", "Q12.", True), ("ANSWER_HEAD_RE", "1. answer", False),
    ("ANSWER_HEAD_RE", "Q1: answer", False), ("ANSWER_HEAD_RE", " Q1. answer", False),
    ("ANSWER_HEAD_RE", "**Q1.** answer", False), ("ANSWER_HEAD_RE", "Q1.5 answer", False),
    ("QUESTION_RE", "1. question", True), ("QUESTION_RE", "Q1: question", True), ("QUESTION_RE", "1.5 x", False),
    ("FENCE_RE", "```python", True), ("FENCE_RE", "   ~~~", True), ("FENCE_RE", "      ```", True),
    ("FENCE_RE", "``", False), ("BLOCKQUOTE_RE", "> quoted", True), ("PIN_LINE_RE", "BRIEF-PIN: " + "0" * 40, True),
    ("_verdict_of", "**VERDICT:** NO BLOCKERS", True), ("_verdict_of", "verdict : clean", True),
    ("_verdict_of", "Overall VERDICT: clean", False), ("QUESTIONS_MARKER_RE", "QUESTIONS:", True),
    ("EXAMPLE_OPEN_RE", "(e.g. x)", True), ("EXAMPLE_OPEN_RE", "(For example x)", True),
    ("EXAMPLE_OPEN_RE", "(i.e. x)", False), ("_is_findings_heading", "## Findings", True),
    ("_is_findings_heading", "### Findings", False), ("_is_findings_heading", "## Findings:", False),
    ("HARNESS_FIRST_RE", "WORKER_STATUS: PASS (account=a, rc=0, family=b, model=c, effort=d)", True),
    ("HARNESS_FIRST_RE", "WORKER_STATUS_FAKE (account=a, rc=0, family=b, model=c, effort=d)", False),
    ("HTML_START_RE", "<!-- x", True), ("HTML_START_RE", "<details>", True), ("HTML_START_RE", "<https://x.invalid>", False),
    ("_marker_candidate", "Q\uff11. x", True), ("_marker_candidate", "  **Q4.** x", True),
    ("_marker_candidate", "Q\ufe0f4. x", True), ("_marker_candidate", "\u00a0" + "*" * 300 + "Q4. x", True),
    ("_marker_candidate", "Q4\\. x", True), ("_marker_candidate", "&#81;4. x", True),
    ("_marker_candidate", "Q4&#46; x", True), ("_marker_candidate", "Q4&period; x", True),
    ("_marker_candidate", "&nbsp;Q4. x", True), ("_marker_candidate", "Q*4*. x", True),
    ("_marker_candidate", "&#133;Q4. x", True), ("_marker_candidate", "&#x85;Q4. x", True),
    ("_marker_candidate", "Q4.&#133;x", True), ("_marker_candidate", "Q4.&#31;x", True),
    ("_marker_candidate", "Q4.&#x1f;x", True), ("_marker_candidate", "Q&#1;4. x", True),
    ("_marker_candidate", "&nbspQ4. x", True), ("_marker_candidate", "Q4&#46 x", True),
    ("_marker_candidate", "Q4&#x2e x", True), ("_marker_candidate", "&#129;Q4. x", True),
    ("_marker_candidate", "Q&#129;4. x", True), ("_marker_candidate", "Q\x014. x", True),
    ("_marker_candidate", "\x01Q4. x", True),
    ("_marker_candidate", "**Q**4. x", True), ("_marker_candidate", "**Q4\\.** x", True),
    ("_marker_candidate", "1. Q4. x", True), ("_marker_candidate", "1) Q4. x", True),
    ("_marker_candidate", "123456789. Q4. x", True), ("_marker_candidate", "\u2800Q4. x", True),
    ("_marker_candidate", "Q1.5 x", False), ("_marker_candidate", "Quote 1. x", False),
    ("_marker_candidate", "`Q4.` x", False), ("_marker_candidate", "[Q4.](x) y", False),
    ("_marker_candidate", "\\&#81;4. x", True), ("_marker_candidate", "1.Q4. x", False),
    ("_marker_candidate", "1234567890. Q4. x", False),
    ("_marker_candidate", "Q&#65535;4\\. x", True), ("_marker_candidate", "Q4\\.&#65535; x", True),
    ("_marker_candidate", "Q&#xFFFF;4\\. x", True),
    ("_undecodable_reference", "&#12345678; x", True), ("_undecodable_reference", "&#1234567; x", False),
    ("_undecodable_reference", "&#x1234567; x", True), ("_undecodable_reference", "&#x123456; x", False),
    ("_boundary_shown", "VERDICT&#58; BLOCKERS FOUND", True), ("_boundary_shown", "QA&#45;COMPLETE", True),
    ("_boundary_shown", "VER\x01DICT: BLOCKERS FOUND", True), ("_boundary_shown", "QA-\x01COMPLETE", True),
    ("_boundary_shown", "QA&#45COMPLETE", True), ("_boundary_shown", "VERDICT: BLOCKERS FOUND", False),
    ("_boundary_shown", "QA-COMPLETE", False),
    ("_unread_fence", "- ```", True), ("_unread_fence", "```", False),
)


def _static_config_problems():
    """The faults of this tool's own static tables (grammar patterns, quote pairs, limits); no input
    is read and no git is run."""
    module = sys.modules[__name__]
    problems = []
    for name, line, want in GRAMMAR_PROBES:
        pattern = getattr(module, name, None)
        if isinstance(pattern, re.Pattern):
            got = bool(pattern.match(line))
        elif callable(pattern):
            got = pattern(line) not in (None, False)
        else:
            got = None
        if got != want:
            problems.append("{} {} {!r}".format(name, "does not match" if want else "matches", line))
    canonical = ",".join("{:04X}..{:04X}".format(low, high) for low, high in DEFAULT_IGNORABLE_RANGES)
    if (hashlib.sha256(canonical.encode("ascii")).hexdigest() != DEFAULT_IGNORABLE_SHA256
            or len(DEFAULT_IGNORABLE) != DEFAULT_IGNORABLE_COUNT
            or DEFAULT_IGNORABLE != frozenset(
                point for low, high in DEFAULT_IGNORABLE_RANGES for point in range(low, high + 1))):
        problems.append("DEFAULT_IGNORABLE_RANGES does not hold the pinned Default_Ignorable_Code_Point table "
                        "(Unicode 16.0.0 DerivedCoreProperties.txt: {} code points; canonical range list "
                        "SHA-256 {})".format(DEFAULT_IGNORABLE_COUNT, DEFAULT_IGNORABLE_SHA256))
    if (not all(_marker_ignorable(char) for char in "\u00ad\u034f\u061c\u115f\u200b\u2060\u3164\ufe0f\ufeff\x01\x7f\u0081")
            or any(_marker_ignorable(char) for char in " \t*_#>+-\\Qq4.\u00a0\uff11\u2800\x1c\u0085")
            or not all(char.isspace() for char in "\x1c\x85")):
        problems.append("_marker_ignorable does not hold the declared ignorable classes: category Cf, the pinned "
                        "Default_Ignorable_Code_Point table and the non-whitespace Cc controls (NUL, U+0001, DEL "
                        "and U+0081 included; the whitespace controls, U+001C and U+0085 included, stay "
                        "whitespace to the scan)")
    if not FENCE_CLOSE_RE.fullmatch("```") or FENCE_CLOSE_RE.fullmatch("``` x"):
        problems.append("FENCE_CLOSE_RE does not hold the declared closing fence")
    if not (SHA_RE.fullmatch("0" * 40) and not SHA_RE.fullmatch("0" * 39)):
        problems.append("SHA_RE does not hold exactly 40 hex digits")
    if _verdict_value("**NO BLOCKERS.**") != CLEAN_VERDICT:
        problems.append("the clean verdict {!r} does not survive its own normalisation".format(CLEAN_VERDICT))
    if not (UNVERIFIABLE_RE.search("UNVERIFIABLE") and not UNVERIFIABLE_RE.search("unverifiable")):
        problems.append("UNVERIFIABLE_RE is not an upper-case word test")
    if not all(QUOTE_WORD_RE.search(word) for word in ("quote", "quotes", "quoted", "quoting")):
        problems.append("QUOTE_WORD_RE misses a declared form of quote")
    if not INLINE_QUOTE_PAIRS or not all(
            isinstance(pair, tuple) and len(pair) == 2 and all(isinstance(c, str) and len(c) == 1 for c in pair)
            for pair in INLINE_QUOTE_PAIRS):
        problems.append("INLINE_QUOTE_PAIRS is not a table of (opener, closer) characters")
    elif _code_spans("a `x` b ``y`z`` c `") != [(2, 5), (8, 15)] or _inline_quotes('`"a"` (e.g. "b") 5" "c"') != ["c"] \
            or _inline_quotes('\u201c x "d" (e.g. "e"') != ["d", "e"]:
        problems.append("the inline code span or inline quote scanner does not hold the declared grammar")
    for name in ("TIMEOUT", "MAX_INPUT_BYTES", "MAX_TREE_BYTES", "MAX_LISTED", "MAX_QUOTES"):
        value = getattr(module, name, None)
        if not isinstance(value, int) or value <= 0:
            problems.append("{} is not a positive integer".format(name))
    return problems


def _bare_run():
    problems = _static_config_problems()
    if problems:
        print("check_verifier_delivery: CANNOT_EVALUATE: static configuration invalid: {}".format(
            "; ".join(problems)), file=sys.stderr)
        return 2
    print("check_verifier_delivery: OK: static tables valid ({} grammar probes); lint a delivery with "
          "check_verifier_delivery.py --brief FILE --delivery FILE --repo DIR".format(len(GRAMMAR_PROBES)))
    return 0


def main(argv):
    if not argv:
        return _bare_run()
    if argv and argv[0] in ("--self-test", "--execution-report"):
        rest = argv[1:] if argv[0] == "--self-test" else list(argv)
        if not rest:
            return self_test()
        if len(rest) == 2 and rest[0] == "--execution-report" and os.path.isabs(rest[1]):
            return self_test(rest[1])
        print(USAGE, file=sys.stderr)
        return 2
    options = {}
    pairs = list(argv)
    while len(pairs) >= 2 and pairs[0] in ("--brief", "--delivery", "--repo") and pairs[0] not in options:
        options[pairs[0]] = pairs[1]
        pairs = pairs[2:]
    if pairs or len(options) != 3:
        report = {"tool": "check_verifier_delivery", "verdict": "CANNOT_EVALUATE",
                  "reasons": ["usage: --brief, --delivery and --repo are each required exactly once"]}
        print(json.dumps(report, indent=2))
        print(USAGE.splitlines()[0], file=sys.stderr)
        return 2
    report, code = evaluate(options["--brief"], options["--delivery"], options["--repo"])
    print(json.dumps(report, indent=2))
    print(_summary(report), file=sys.stderr)
    return code


if __name__ == "__main__":
    _code = main(sys.argv[1:])
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
