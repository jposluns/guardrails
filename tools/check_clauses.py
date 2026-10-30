#!/usr/bin/env python3
"""Clause-inventory and identifier-stability gate for the pack (VER-CORE Section 7). Offline, stdlib
only, fail-closed.

gen_rules.py owns corpus-id uniqueness and frontmatter shape; check_version_monotonicity.py owns the
SemVer release history. This gate owns the CLAUSE layer: it verifies the per-release clause inventory
(`.aiqt/core/clauses.toml`) and the cumulative id-history register (`.aiqt/core/id-history.toml`) against
the rule sources under `.aiqt/core/rules/`, enforcing the 7.1 clause-id scheme, the 7.3 cumulative
resurrection and completeness rules, and the 7.2 per-row span/text/digest integrity.

SCHEMAS this gate reads (proposed here for VER-CORE Section 7; the orchestrator finalizes them against
gen_manifest.py at build time).

  `.aiqt/core/clauses.toml` (7.2), the per-release inventory, author-maintained, manifest-covered. An
  array of `[[clause]]` tables, one per live normative obligation this release:
    clause-id       = "<corpus-id>.<ordinal>"   the stable clause identifier (7.1)
    corpus-id       = "<corpus-id>"             the owning rule's frontmatter corpus-id
    source-path     = "repo/relative/path.md"   the rule SOURCE file (under .aiqt/core/rules/)
    start-line      = <int>                      1-based first line of the clause span in that file
    end-line        = <int>                      1-based last line of the clause span (>= start-line)
    canonical-text  = "..."                      the obligation's own text, LF-preserving byte-exact (3.2)
    source-digest   = "<64 hex>"                 SHA-256 of the WHOLE rule source file's raw bytes (3.2)
  Model C: the [start-line, end-line] block is a LOCATING WINDOW of whole lines (the source file's
  lines[start-line-1 : end-line] joined with LF, no trailing newline). canonical-text must occur EXACTLY
  ONCE as a contiguous byte-exact substring within that window, and that one occurrence must BEGIN on
  start-line and END on end-line (a tight window). An embedded line-wrap LF in canonical-text is retained
  verbatim, with no normalization of any kind (3.2 byte-literal). Zero or more than one occurrence is a
  FAIL; the gate never picks the first occurrence. Uniqueness is within THAT ROW'S window only, not global,
  so several clause-ids may legitimately share one source sentence. (3.1 canonicalization of the source
  file itself is check_byte_canon.py's job, not re-scanned here.)
  WHOLE-CLAUSE EDGES: a window line may hold several sentences, so a canonical-text may be shorter than
  its window, but only by whole sentences or whole clauses. Both edges are judged against the WHOLE
  source file (a line edge is no free pass) by ONE boundary grammar and ONE enclosure scan.
  BOUNDARY: a position is a sentence or clause boundary when (c) it is at the start or end of the file,
  or a blank line touches it (a PARAGRAPH or FILE edge; a single line break never is, so a row cannot
  drop a wrapped line); (a) whitespace sits at it and the text before it, ignoring whitespace, ends with
  one of . ? ! either bare, when the text after it, ignoring whitespace, starts with no opener (a double
  quote, straight or curly, a bracket or a backtick) and, after a '?' or '!', with no lower-case ASCII
  letter; or followed directly by closing double quotes or brackets when all three of these hold: the text
  after it, ignoring whitespace, plainly starts a new sentence (it starts with an ASCII capital; a digit,
  another quotation or bracket, a closer, or anything else is no start); each closer closes an enclosure,
  or is a bracket that is text inside a quotation; and the outermost closed enclosure's opener itself sits
  at such a boundary. So '"Stop." Then wait.' holds two sentences, while in '"Done." is printed.', in
  '"Stop." (with no other words) is the command.', in 'Stop. (with no other words) is the command.' and in
  'Ask why? before you delete anything.' a longer sentence goes on from the quotation, the '.' or the '?',
  and neither side of it is a boundary. A bare '.' before a lower-case word stays a sentence end, since a
  sentence may start with a command name ('git checkout -- <path> overwrites the tree.'). (b) whitespace
  sits at it and the text before it ends with ';'; or it sits directly before a ';' that whitespace
  follows. A comma or a colon is never a boundary: a cut at ", and" may split a list as easily as two
  clauses, and a colon usually opens a list or an elaboration inside one sentence, so two obligations
  joined by ", and" share one whole-sentence text (two clause-ids may carry identical canonical-text) or
  are split at a ';' in the source.
  ENCLOSURE: each edge must also sit at enclosure depth 0, counted from the start of the edge's own
  paragraph in the SOURCE, never from the text alone, so an enclosure the text drops at both edges is
  seen. The scan pairs inline code spans (a run of backticks, closed by the next run of the same length;
  everything inside one is text, and one opens inside a quotation or bracket too), double quotes
  (straight, and curly U+201C/U+201D) and the brackets ( ) [ ] and braces: a straight quote closes an
  open straight quote on top and otherwise opens one; inside a quotation a bracket is text; a closer that
  closes nothing open on top is text ("a) build", or a ")" in a quoted literal). An enclosure the text
  touches at an edge is therefore one it contains whole, and a sentence end or ';' inside a code span, a
  quotation or a bracket is no edge.
  Every other edge is a FAIL, so, within one paragraph, a text cut short at a dropped final '.', a
  dropped word, a comma, a colon, a dropped wrapped line, inside a code span, a double quotation or a
  bracket, or at either side of a quoted or bracketed sentence that a longer sentence goes on from fails,
  whatever the next word starts with (a lower-case letter, a digit, another quotation or bracket, a
  closer, a code span, a single quote, markup), except for the residual shapes listed next. Two of those
  turn on the next word: (2) passes a cut at a bare '.', '?' or '!' that ends no sentence unless that
  word starts with an opener or, after a '?' or '!', a lower-case ASCII letter, and (6) passes a cut at
  either side of a quoted or bracketed sentence only when that word starts with an ASCII capital. A blank
  line is always an edge (c), so a cut there passes whatever sits on either side of it: '"Never delete."'
  and '(the old rule) no longer applies.' each pass when a blank line parts them (see also (5)).
  RESIDUAL, disclosed (shapes that still pass): (1) a row cut back to an EARLIER whole sentence or whole
  clause still passes when what it drops sits on its end-line (a dropped part that fills a later line
  breaks the tight window instead); (2) a bare sentence end (a '.', '?' or '!' that whitespace follows,
  with no closer) reads as a sentence end even where it ends none, unless the next word starts with an
  opener, or with a lower-case ASCII letter after a '?' or '!': so a cut passes at an abbreviation
  ("e.g. "), an ellipsis ("... ") or a list number ("1. "); at a '!' or '?' inside a sentence before a capital
  ('Yahoo!' and 'Mail is the only approved client.' from 'Yahoo! Mail is the only approved client.'); at a
  '.' before a lower-case word ("the linter, on every file." from "Use a tool, e.g. the linter, on every
  file."); and at any bare sentence end before anything else that is no opener, such as a digit, a single
  quote, a closer, markup or a non-ASCII letter ('Stop.' from 'Stop. 3 times in a row halts the run.'); a
  decimal point never reads as one, since no whitespace follows it; (3) single quotes are not paired,
  because an apostrophe is the same character, so a cut inside a single-quoted sentence ("Go.' is
  printed." from "'Stop. Go.' is printed.") passes; (4) a ';' that separates list items reads as a clause
  separator, so one item passes ("the country;" from "Record the city, region; the country; and the
  date."); (5) the enclosure scan works per paragraph, so a quotation, bracket or code span that spans a
  blank line is not seen, and a stray straight double quote (an inch mark) reads as an opener and reverses
  the pairing of the straight quotes after it in its paragraph, so a cut inside a later quotation there
  can read as outside one ("Go." from 'Use a 3" pipe. Say "Stop. Go. Wait." now.'); (6) a quoted or
  bracketed sentence that a longer sentence goes on from still reads as a whole sentence when the word
  after it starts with an ASCII capital (a name, an acronym, "I"), so '"Stop."' passes from '"Stop." CI
  treats as a hard halt.', and so does the rest of that sentence; when that word starts with a digit, a
  quotation or bracket opener, or anything else, both cuts fail. The gate has no record of where an
  obligation ends other than the source text.
  FAIL-CLOSED OVER-FIRES, disclosed (whole obligations the gate refuses, exit 1; none is in the
  register): a bullet item whose text leaves out its marker ("- ") or runs on to the next item with no
  ending; a text after a bold or emphasised lead-in ("**Note.** "); a whole bold sentence without its
  markup; a whole bold or italic sentence with its markup ("**Never push to main.**", "*Never push to
  main.*") when more text follows in its paragraph; a text after a blockquote marker ("> ") or inside a
  table cell ("| Keep the audit log. |"); a row on the line right after a heading with no blank line
  between; a text that ends on a comma ("Alpha holds," before " and beta"); a lead-in that ends at a ':'
  and a text after one; a sentence that ends inside a quotation or bracket opened mid-sentence ('Say
  "stop."', "Stop (always.)") when more text follows in its paragraph, and the sentence after it; a
  whole quoted or bracketed sentence, and the text after it, when that text starts with anything but an
  ASCII capital: a lower-case word, a digit ('"Stop." 3 files remain.'), another quoted or bracketed
  sentence (in '"Stop." "Go." Then wait.' every cut but the whole line fails: '"Stop."', '"Go."',
  '"Stop." "Go."' and "Then wait."), markup such as "**", a code span, a single quote, or a non-ASCII
  capital such as "\u00c9"; a sentence that ends inside single quotes and the sentence after it ("Then
  wait." after "'Stop.' "); a sentence after a bare '?' or '!' that starts with a lower-case word, and the
  sentence before it ("Is it gzip or zstd?" and "zstd is the default." from "Is it gzip or zstd? zstd is
  the default."); a sentence after a bare sentence end that starts with an opener, and the sentence before
  it ("Run it." and "`make check` must pass." from "Run it. `make check` must pass."), and so a whole
  quoted or bracketed sentence there and the sentence after it too (in "Run the suite. (Optional.) Then
  wait." every cut but the whole line fails); a quoted or bracketed sentence with whitespace between its
  sentence end and its closer, and the sentence after it ('"Stop. "' and "Then wait." from '"Stop. " Then
  wait.', with a space or a line break in the gap); a sentence that ends inside a code span and the
  sentence after it ("Then wait." after "Type `exit.` "); a whole sentence inside a quotation or bracket
  ("Rotate them." from 'He wrote: "Keep logs. Rotate them."'); a sentence after a sentence end and a
  closer that closes nothing ("Then wait." after "Stop.) "), or a closing quote that is text inside
  another quotation ("Then wait." after '"He said stop.\u201d" '); and every edge after an opener left
  unclosed in its paragraph, such as an inch mark ('Use a 3" pipe.'), a stray backtick ("Use the ` key.")
  or a half-open interval ("Keep values in [0, 1)."). The gate refuses such a row rather than guess; the
  row is rewritten to a whole sentence, or the source gains a blank line or a ';'.

  `.aiqt/core/id-history.toml` (7.3), the pack-owned, append-only, cumulative register of every corpus-id
  AND clause-id ever assigned. Three arrays:
    [[born]]       id = "<id>"  born-release    = "<semver>"                       one per id ever assigned
    [[tombstone]]  id = "<id>"  retired-release = "<semver>"                       a permanent retirement
    [[successor]]  id = "<id>"  retired-release = "<semver>"  successor-id = "<id>"  a rename/fold/split
  An id is a corpus-id (no dot) or a clause-id (exactly one dot). Tombstones and successors are BOTH
  retirement rows; a retired id never returns to a live inventory (resurrection).

  `.aiqt/core/clause-exemptions.toml`, the reviewed record of rule text that carries no clause row on
  purpose, author-maintained, manifest-covered. An array of `[[exempt]]` tables, each with exactly:
    corpus-id = "<corpus-id>"   the rule the text sits in
    text      = "..."           the exempted text, byte-exact (an embedded line-wrap LF kept): one whole
                                sentence or clause, or a run of them, found exactly once in the rule body
    reason    = "..."           why the text needs no clause row (a definition, a cross-reference, a
                                rationale, an example, a scope note); never empty
  The file is required, at the default path as when named with --exemptions, and must hold the `exempt`
  key: an absent file (a dangling symlink included), an unreadable one, and one with no `exempt` key (an
  empty file, a comment-only file, a symlink to /dev/null) are malformed input (exit 2), never read as no
  exemption, because the file carries findings of its own (a stale, repeated, redundant or reasonless
  exemption) that reading it as empty would hide. An intentionally empty register is the explicit
  `exempt = []`. A file that is not TOML, a top-level key other than `exempt`, and a row whose keys are not
  exactly these three or whose values are not strings (or whose corpus-id is not a well-formed corpus-id)
  are malformed input (exit 2) too. RESIDUAL, disclosed: the gate cannot detect a valid replacement of the
  exemption content; rows removed, or an `exempt = []` written in place of the register, read as the
  reviewed register. Removing a row a unit needs still surfaces that unit as uncovered (exit 1), but a
  removed stale, repeated, redundant or reasonless row leaves no trace, since removing it is its remedy.

LEGS (all run at the default step-1 invocation; none needs a manifest):
  ID SCHEME (7.1):      every rule corpus-id under rules-dir appears in the inventory; every clause-id is
                        `<corpus-id>.<ordinal>` with an UNPADDED positive-decimal ordinal (no leading
                        zero); the row's corpus-id matches the frontmatter corpus-id of its source file;
                        no duplicate clause-id. Ordinals are compared NUMERICALLY, never lexicographically.
  PER-ROW (7.2):        the [start,end] block resolves inside the named file and is treated as a locating
                        WINDOW of whole lines; canonical-text occurs exactly once as a contiguous
                        byte-exact substring within that window, beginning on start-line and ending on
                        end-line (a tight window); the source digest is the SHA-256 of the whole source
                        file. Zero or more than one occurrence, an untight occurrence, an empty
                        canonical-text, an edge that is not a whole-sentence or whole-clause edge of
                        the source file (a truncated text), or a digest disagreement is a FAIL.
  CUMULATIVE-MAX (7.1): each ordinal newly assigned this release (a born row at the newest release in the
                        register) strictly EXCEEDS the cumulative maximum ordinal EVER used for that
                        corpus-id, taken from the register's born rows (which persist for dead ids too), so
                        a dead ordinal's gap can never be refilled below the historical maximum (m-A).
  RESURRECTION (7.3):   the cumulative ever-retired id set is built FROM THE REGISTER; no id the inventory
                        asserts live (a clause-id or its corpus-id) equals any tombstoned or retired id.
  COMPLETENESS (7.3):   every id the inventory introduces has a born row; every retirement row's id has a
                        born row; no id carries more than one retirement row; a retired id is absent from
                        the live inventory. With --prev-inventory, the cross-release leg additionally
                        requires every id that DISAPPEARED since the predecessor inventory to carry exactly
                        one retirement row (zero unexplained disappearances). Without a predecessor that
                        cross-release leg reports NOT APPLICABLE (genesis / step-1 baseline).
  TEXT COVERAGE:        every sentence or clause of every corpus rule body (each rule source under the
                        rules dir; a pack file outside it, such as .claude/rules/README.md, is not checked)
                        lies inside the resolved occurrence of
                        some clause row's canonical-text or inside one recorded exemption. The checked text
                        is the rule body after the frontmatter and after a first-line `# ` title heading (a
                        title, not an obligation). The gate cuts it into units at every position the
                        WHOLE-CLAUSE EDGES grammar above reads as a sentence or clause boundary at enclosure
                        depth 0 (the same _is_boundary and _enclosures the per-row leg uses; there is no
                        second tokenizer), drops a bare ';' between two clauses, and reports each unit that
                        no row covers and no exemption names. An exemption is a finding when its reason is
                        empty or whitespace-only; when its text is empty, has edge whitespace, no longer
                        occurs in its rule's body (stale), occurs there more than once, does not begin and
                        end at a boundary at depth 0, or names no unit; when its corpus-id names no rule
                        (stale); when it repeats another exemption; and when a clause row already covers a
                        unit it names (redundant). This closes the hole an id-only completeness leaves:
                        removing a passage's clause rows and their id-history rows together while the
                        passage stays in the rule is now a finding, not a clean pass.
                        CANNOT EVALUATE (exit 2, never a pass): an absent or unreadable exemptions file, or
                        one with no `exempt` key; a rule file that cannot be read or is not UTF-8; a rule
                        file holding, anywhere (frontmatter, line start or mid-line), a character the
                        vendored renderer reads as a line ending, since the splitter cuts lines at LF only:
                        that class is exactly a carriage return (alone or before LF) and a form feed, which
                        _preprocess_text in opf/tools/_vendor/marko/source.py rewrites to LF before the
                        parser reads lines, cut at LF only (CommonMark reads a carriage return as a line
                        ending too); no other character ends a line there or in CommonMark, so a vertical
                        tab, U+0085, U+2028 and U+2029 stay inside their line; a checked line (the title is
                        never checked) holding '<!' or '<?' anywhere (an inline HTML comment, processing
                        instruction, declaration or CDATA section, which the vendored renderer passes through
                        as raw HTML that a browser does not show); a body with no frontmatter; a title
                        heading with no blank line after it; a line the splitter cannot classify; a
                        paragraph that ends with an opener still open (an inch mark, a stray backtick); and
                        a unit with no letter (str.isalpha) or decimal digit (str.isdecimal), so an
                        underscore or punctuation alone is no sentence.
                        A line the splitter cannot classify is one that may start a Markdown block where it
                        stands (CommonMark 0.31.2, the GFM table and the vendored parser's reading of
                        whitespace, read conservatively), so a wrapped line of prose that merely begins with
                        '#', '<' or a number is still prose. These, and only these, are refused; 'whitespace'
                        below is any character str.isspace admits, a no-break space and a vertical tab included.
                        On ANY line: an ATX heading other than the title (one to six '#' then whitespace or
                        the line end); a bullet item ('-', '*' or '+' then whitespace or the line end); an
                        ordered item numbered 1 (up to eight leading zeros, '1', then '.' or ')' and
                        whitespace or the line end), the only number that can interrupt a paragraph; a
                        blockquote ('>'); a line that opens with '|' (a table row, or a delimiter row with a
                        leading pipe); a GFM table delimiter row with no leading pipe (a line of only '-',
                        ':', '|' and whitespace that opens with '-', or with ':' and holds a '-', such as
                        '--|--' or ':-- | --:'), and any line the vendored GFM table reads as a delimiter
                        row (_is_table_delimiter_row: cut at each unescaped '|', an empty first and last
                        cell dropped, every cell left BEGINS with '-' or ':-', such as '--x|--y'); a code
                        fence (``` or ~~~); an HTML block start that can interrupt a paragraph ('<!' or '<?',
                        or an opening or closing tag whose name is one of the HTML block names in
                        _HTML_BLOCK_TAGS: CommonMark's kind 1 and kind 6 names, plus meta and source); a
                        thematic break or setext underline (three or more of '-' '*' '_' '=' with optional
                        whitespace, or a run of only '=' or only '-'); and a line that opens with whitespace
                        (an indented line). On a PARAGRAPH'S FIRST LINE also: an ordered item of any number
                        (one to nine decimal digits, any the vendored parser's \\d admits, so an Arabic-Indic
                        '\u0661.' too); a line that opens with '<' and then a letter or '/', and ends with
                        '>' before any trailing whitespace, which holds every HTML block of kind 7 (one
                        complete open or closing tag and nothing else, a quoted attribute value holding '<'
                        or '>' included); and a line that opens with '[' and so may be a link reference
                        definition: one with ']:' later on it, or one with no unescaped ']' on it, whose
                        label may close on a later line.
                        The class is wider than CommonMark on purpose: an empty list item, a '|' line no
                        table follows, a delimiter-shaped line no table header precedes, and an indented
                        continuation line are refused although CommonMark and GFM read them as paragraph
                        text; whitespace other than a space or a tab counts where CommonMark counts only
                        those two (the vendored parser reads a heading after '#' and a no-break space or a
                        vertical tab, and a setext underline with a trailing no-break space); a line whose
                        every '|'-separated cell begins with '-' or ':-', such as '--force is the flag.', is
                        refused wherever it stands, though only a header line holding a '|' with as many
                        cells above it makes it a delimiter row; '<!' or '<?' inside a code span is refused,
                        though a renderer shows it; as a paragraph's first line, '<b>bold</b>' and '<path> or
                        <ref>' are refused though they hold more than one tag, '[See the note]: it holds.' is
                        refused though no destination follows, and '[the note' is refused when its bracket
                        closes on a later line with no ':' after it; and a line such as '> 5 minutes is a
                        fault.' or '+ 1 to the count.' is refused because CommonMark reads it as a blockquote
                        or a list item.
                        RESIDUAL, disclosed: coverage is positional, not semantic. A unit counts as covered
                        when any row's resolved text contains it, whatever obligation that row names, and an
                        exemption's reason is read by a reviewer, never judged by the gate. The title
                        heading is never checked, so an obligation written only as the title passes. The
                        units come from the WHOLE-CLAUSE EDGES grammar, so a cut it reads where no sentence
                        ends (an abbreviation, a list number) splits one sentence into two units, each of
                        which must then be covered or exempted. Inline raw HTML other than '<!' and '<?' (a
                        tag mid-line, such as a span with a hidden attribute, or a script or style element
                        opened mid-line) is read as text its unit holds; whether a renderer shows it is not
                        judged.

DEFERRED manifest-SOURCES leg (7.2 + round-10 R10-2 / gemini R9-4): the leg that cross-checks each row's
source-digest against the manifest's SOURCES entry is OFF by default and reads no manifest. It runs only
when --with-manifest (or --manifest PATH) is passed, so a later build step can arm it AFTER gen_manifest.py
regenerates the release's OWN manifest. Running it at step 1 would compare an edited rule's new digest
against a PRIOR release's stale in-tree manifest and mis-fire. The assumed manifest shape is an array of
`[[sources]]` tables carrying `path` and `sha256`; the orchestrator reconciles this with gen_manifest.py.

GENESIS (2.5): at genesis there is no predecessor inventory and no tombstone; --genesis asserts that (the
register carries born rows only and covers the whole inventory) and passes cleanly. Absent --genesis and
--prev-inventory, the gate runs the same baseline with the cross-release disappearance leg NOT APPLICABLE.

RESIDUAL, disclosed (disclose-guard-residuals): this gate reads the register and the inventory in the
working tree; it does NOT here verify the register's append-only property across git history (that is the
prefix-identity discipline of check_version_monotonicity.py extended to the register, 2.4/7.3) nor that a
born row's release equals the release under build. A row mis-dated to an old release is not caught by the
in-file legs alone; the cross-history and manifest legs cover it at the release gate.

  check_clauses.py [--root DIR] [--rules-dir DIR] [--inventory FILE] [--register FILE]
                   [--exemptions FILE] [--prev-inventory FILE] [--genesis] [--with-manifest | --manifest FILE]
  check_clauses.py --self-test    deterministic self-test (synthetic fixtures in a private tempdir)

Exit convention (matches the repo's gates):
  0  clean, or a printed NOT APPLICABLE
  1  a real finding (a scheme, per-row, cumulative-max, resurrection, completeness, or text-coverage
     violation, including an uncovered rule sentence and a bad exemption, or an inventory row naming a
     missing or non-UTF-8 source file, which is exactly what this gate asserts against)
  2  malformed or unreadable required input (the inventory, the register, the rules dir, the exemptions
     file, an unparseable or unreadable source, rule text the coverage splitter cannot classify, or a
     permission error), fail-closed: an input the gate cannot read never reads as clean.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_clauses.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import io
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    import os
    try:
        sys.stderr.write(
            "error: check_clauses.py cannot import tomllib, part of the Python standard library; "
            "this installation is incomplete. Nothing was run (cannot evaluate).\n")
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml, read_source_bytes, precheck_special_files  # noqa: E402
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
from check_versions import _parse as _semver  # noqa: E402  reuse the shipped bare-SemVer parser
from gen_rules import parse_source, CID_RE  # noqa: E402  reuse the frontmatter parser and corpus-id regex

ORDINAL_RE = re.compile(r"^[1-9][0-9]*$")  # unpadded positive decimal: no leading zero, no sign, no zero
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")  # the source-digest syntax: 64 lowercase hex characters


class GateError(Exception):
    """An input the gate cannot read, parse, or resolve. Caught at run() and reported as exit 2
    (fail-closed): an unreadable or unparseable required input is never treated as an empty or clean
    result."""


# --- pure logic (clause-id parsing, ordinal comparison, register shape; always run in --self-test) ----

def split_clause_id(clause_id):
    """Split a clause-id into (corpus-id, ordinal_int), or return None if it is not a well-formed
    clause-id. A corpus-id is `[a-z0-9]{6,}` with no dot, so a clause-id carries exactly one dot; the
    ordinal is an UNPADDED positive decimal compared NUMERICALLY. A padded ordinal ('01'), a zero, a
    sign, extra dots, or a bad corpus-id all return None (the caller reports the scheme finding).
    RESIDUAL: the scheme sets no upper bound on an ordinal, but CPython's int() refuses to convert an
    integer string of more than 4300 digits (a ValueError). A pathological ordinal that long is treated
    as malformed input here (return None, so the caller reports a scheme finding and the gate fails closed
    with exit 2) rather than crashing with an unhandled ValueError. Real ordinals are tiny, so this bound
    never touches a legitimate id."""
    if not isinstance(clause_id, str) or clause_id.count(".") != 1:
        return None
    corpus, _dot, ordinal = clause_id.partition(".")
    if not CID_RE.fullmatch(corpus) or not ORDINAL_RE.fullmatch(ordinal):
        return None
    try:
        return corpus, int(ordinal)
    except ValueError:
        return None


def is_clause_id(any_id):
    """True if any_id parses as a well-formed clause-id (has an ordinal); False for a bare corpus-id or a
    malformed token. Used to pick clause-ids out of the mixed register id space for the ordinal legs."""
    return split_clause_id(any_id) is not None


def _valid_register_id(any_id):
    """True if any_id is a well-formed corpus-id (CID_RE) OR a well-formed clause-id. Used to fail closed
    on a register id (born/tombstone/successor) that is neither: such an id is malformed input (exit 2),
    not a content finding."""
    return isinstance(any_id, str) and (bool(CID_RE.fullmatch(any_id)) or is_clause_id(any_id))


def check_scheme(rows, rule_corpus_ids):
    """ID SCHEME (7.1). rows is a list of {'clause-id','corpus-id',...} dicts; rule_corpus_ids is the set
    of corpus-ids found in the rule sources. Returns a list of finding strings. Ordinals are validated by
    ORDINAL_RE (unpadded positive decimal) and never compared as text. A clause-id that does not parse, or
    a corpus-id that is missing, non-string, or not a well-formed corpus-id, is malformed input and raises
    GateError (fail-closed, exit 2). A well-formed-but-mismatched corpus-id, a duplicate clause-id, and a
    rule corpus with no clause row are genuine content violations, reported as findings (exit 1)."""
    findings = []
    seen = set()
    inventory_corpus_ids = set()
    for i, row in enumerate(rows, 1):
        cid = row.get("clause-id")
        parsed = split_clause_id(cid)
        if parsed is None:
            raise GateError("clause row #{}: clause-id {!r} is missing or not <corpus-id>.<ordinal> with "
                            "an unpadded positive-decimal ordinal".format(i, cid))
        parsed_corpus, _ordinal = parsed
        corpus = row.get("corpus-id")
        if not isinstance(corpus, str) or not CID_RE.fullmatch(corpus):
            raise GateError("clause row #{}: corpus-id {!r} is missing, non-string, or not a well-formed "
                            "corpus-id".format(i, corpus))
        if corpus != parsed_corpus:
            findings.append("clause row #{}: corpus-id {!r} does not match the clause-id's corpus part "
                            "{!r}".format(i, corpus, parsed_corpus))
        if cid in seen:
            findings.append("clause row #{}: duplicate clause-id {!r}".format(i, cid))
        seen.add(cid)
        inventory_corpus_ids.add(parsed_corpus)
    for corpus in sorted(rule_corpus_ids - inventory_corpus_ids):
        findings.append("rule corpus-id {!r} has no clause row in the inventory".format(corpus))
    return findings


def check_no_nesting(rows):
    """NO NESTED CLAUSES (enforces the shared-sentence decomposition model). Within one corpus-id, no
    clause's canonical-text may be a PROPER substring of another clause's canonical-text. Two clauses MAY
    carry identical canonical-text (the spec-intended form where one sentence holds several independently
    testable obligations, distinguished by clause-id, quoted per 8.4 by the excerpt that carries each);
    what is forbidden is a strict containment, which means one clause conflates into its span an obligation
    another isolates, a redundant or mis-bounded decomposition the per-row byte-substring leg cannot
    otherwise catch. Returns a list of finding strings."""
    findings = []
    by_corpus = {}
    for row in rows:
        cid = row.get("clause-id")
        text = row.get("canonical-text")
        if isinstance(cid, str) and isinstance(text, str):
            by_corpus.setdefault(cid.split(".", 1)[0], []).append((cid, text))
    for corpus, items in sorted(by_corpus.items()):
        for a_id, a_text in items:
            for b_id, b_text in items:
                if a_id != b_id and a_text != b_text and a_text in b_text:
                    findings.append("clause-id {!r} canonical-text is a proper substring of clause-id {!r} "
                                    "within corpus-id {!r}: a nested or redundant decomposition (two "
                                    "clauses may share identical text but never nest)".format(a_id, b_id, corpus))
    return findings


def check_cumulative_max(born_clause_ids):
    """CUMULATIVE-MAX (7.1 / m-A). Release-agnostic: for EVERY born clause-id, its ordinal must strictly
    exceed the maximum ordinal of any same-corpus-id clause-id born STRICTLY BEFORE its OWN born-release.
    born_clause_ids is a list of (clause-id, born-release-tuple) pairs; ordinals are compared NUMERICALLY.
    Each clause-id is checked against its own predecessors, never a single global newest release, so an
    unrelated later-dated row (for example a tombstone) cannot disable the check and a dead ordinal's gap
    can never be refilled below the historical maximum. Returns a list of finding strings."""
    findings = []
    for clause_id, born in born_clause_ids:
        corpus, ordinal = split_clause_id(clause_id)
        ceiling = None
        for other_id, other_born in born_clause_ids:
            if other_born >= born:
                continue
            other_corpus, other_ordinal = split_clause_id(other_id)
            if other_corpus != corpus:
                continue
            if ceiling is None or other_ordinal > ceiling:
                ceiling = other_ordinal
        if ceiling is not None and ordinal <= ceiling:
            findings.append("clause-id {!r} (ordinal {}) does not exceed the cumulative maximum ordinal {} "
                            "ever used for corpus-id {!r} before its born-release (a dead ordinal's gap "
                            "must not be refilled below the historical maximum)".format(
                                clause_id, ordinal, ceiling, corpus))
    return findings


def check_resurrection(inventory_ids, retired_ids):
    """RESURRECTION (7.3). inventory_ids is the set of ids the inventory asserts live (each clause-id and
    its corpus-id); retired_ids is the cumulative tombstoned/retired set from the register. Any overlap is
    a FAIL. Returns a list of finding strings."""
    findings = []
    for rid in sorted(inventory_ids & retired_ids):
        findings.append("id {!r} is live in the inventory but is tombstoned/retired in the id-history "
                        "register (resurrection of a dead id is forbidden)".format(rid))
    return findings


def check_completeness(inventory_ids, born_ids, retired_rows, live_ids, prev_ids):
    """COMPLETENESS (7.3). born_ids is the set with a born row; retired_rows is {id: count of retirement
    rows}; live_ids is the set of ids the inventory asserts live; prev_ids is the predecessor inventory id
    set or None. Every born id must be accounted for: it is either live in the inventory or carried by a
    retirement row; a born id that is neither is a phantom the register never assigned to a live obligation
    and never retired. Every born clause-id's owning corpus-id (the part before the dot) must ALSO have a
    born row; a born clause-id whose corpus was never born is an orphan (7.3 requires one born row per
    corpus-id and clause-id ever assigned). Returns (findings, na) where na is True when the cross-release leg did
    not run (no predecessor)."""
    findings = []
    for iid in sorted(inventory_ids - born_ids):
        findings.append("id {!r} appears in the inventory but has no born row in the register".format(iid))
    for bid in sorted(born_ids - live_ids):
        if retired_rows.get(bid, 0) == 0:
            findings.append("id {!r} has a born row but is neither live in the inventory nor carried by a "
                            "retirement row (a phantom born id)".format(bid))
    for bid in sorted(born_ids):
        parsed = split_clause_id(bid)
        if parsed is not None and parsed[0] not in born_ids:
            findings.append("id {!r} is a born clause-id but its owning corpus-id {!r} has no born row in "
                            "the register (an orphan clause-id with no born corpus)".format(bid, parsed[0]))
    for rid, count in sorted(retired_rows.items()):
        if rid not in born_ids:
            findings.append("id {!r} has a retirement row but no born row in the register".format(rid))
        if count > 1:
            findings.append("id {!r} carries {} retirement rows; an id retires exactly once".format(rid, count))
        if rid in live_ids:
            findings.append("id {!r} is retired in the register but still live in the inventory".format(rid))
    na = prev_ids is None
    if not na:
        for gone in sorted(prev_ids - live_ids):
            if retired_rows.get(gone, 0) == 0:
                findings.append("id {!r} disappeared since the predecessor inventory with no tombstone or "
                                "successor row (unexplained disappearance)".format(gone))
    return findings, na


def _successor_cycle_findings(edges):
    """Report each cycle in the successor graph. edges is {id: [successor-id, ...]}. A cycle means a chain
    of renames/folds/splits that loops back on itself, which the forward-only register semantics forbid;
    the per-row self-loop check catches only the single-edge case (id -> itself), so a multi-node cycle
    (a -> b -> a) needs this leg. Iterative depth-first search with a grey/black colouring: a node goes
    grey on entry and black only once all its descendants are explored (the explicit path mirrors the
    recursion stack), so a back-edge to a grey node on the current path is a cycle. Iterative rather than
    recursive so a long successor chain cannot overflow the stack. Returns a list of finding strings, one
    per distinct cycle found."""
    findings = []
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {}
    for start in sorted(edges):
        if colour.get(start, WHITE) != WHITE:
            continue
        colour[start] = GREY
        path = [start]
        stack = [(start, iter(edges.get(start, [])))]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                state = colour.get(nxt, WHITE)
                if state == GREY:
                    cycle = path[path.index(nxt):] + [nxt]
                    findings.append("id-history successor graph contains a cycle: {} (a rename/fold/split "
                                    "chain must not loop back on itself)".format(" -> ".join(cycle)))
                elif state == WHITE:
                    colour[nxt] = GREY
                    path.append(nxt)
                    stack.append((nxt, iter(edges.get(nxt, []))))
                    advanced = True
                    break
            if not advanced:
                colour[node] = BLACK
                path.pop()
                stack.pop()
    return findings


def check_register_integrity(born, retirements, successors):
    """REGISTER TEMPORAL + GRAPH INTEGRITY (7.3, forward-only semantics). born is {id: born-release-str};
    retirements is a list of (id, retired-release-tuple) for every tombstone and successor row; successors
    is a list of (id, successor-id, retired-release-tuple). Returns findings (exit 1) for: a retirement
    strictly before the id's own born-release; a successor-id born after the retirement-release of the row
    that points to it; and a cycle in the successor graph. Releases are compared as the SemVer tuples the
    register already parsed."""
    findings = []
    born_release = {rid: _semver(rel) for rid, rel in born.items()}
    for rid, retired_at in retirements:
        born_at = born_release.get(rid)
        if born_at is not None and retired_at < born_at:
            findings.append("id {!r} carries a retirement-release before its born-release (a retirement "
                            "cannot precede the birth)".format(rid))
    for rid, succ, retired_at in successors:
        succ_born = born_release.get(succ)
        if succ_born is not None and succ_born > retired_at:
            findings.append("id-history successor row {!r}: successor-id {!r} is born after the retirement "
                            "that points to it (a successor cannot postdate its retirement)".format(rid, succ))
    # A successor-id that was itself retired at a STRICTLY EARLIER release than the edge pointing to it
    # names a dead id as a successor: a resurrection through the register (7.3 forward-only). Same-release
    # retirement is a permitted one-release pass-through fold, so the comparison is strict (reconciliation
    # 6: the temporal form, not set membership, which would false-positive on every legal multi-step
    # chain a->b at R1, b later retired at R2).
    retired_release = {rid: retired_at for rid, retired_at in retirements}
    for rid, succ, retired_at in successors:
        succ_retired = retired_release.get(succ)
        if succ_retired is not None and succ_retired < retired_at:
            findings.append("id-history successor row {!r}: successor-id {!r} was already retired at an "
                            "earlier release (naming a dead id as a successor is a resurrection through "
                            "the register; 7.3 forward-only)".format(rid, succ))
    edges = {}
    for rid, succ, _retired_at in successors:
        edges.setdefault(rid, []).append(succ)
    findings += _successor_cycle_findings(edges)
    return findings


# --- register and inventory loading -----------------------------------------------------------------

def _require_str(table, key, where):
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GateError("{}: missing or non-string {!r}".format(where, key))
    return value


def _require_release(table, key, where):
    value = _require_str(table, key, where)
    # The shipped SemVer parser uses match() with a `$` anchor, so a trailing newline slips past it
    # ("1.1.0\n" parses). Reject any whitespace here before/around _semver so a newline- or space-tainted
    # release is malformed input (exit 2), never a silently accepted bare SemVer.
    if any(ch.isspace() for ch in value) or _semver(value) is None:
        raise GateError("{}: {!r} {!r} is not a bare SemVer".format(where, key, value))
    return value


def load_register(path):
    """Parse the id-history register. Returns (born, retired_rows, retired_ids, born_clause_ids,
    retirements, successors): born {id: born-release-str}, retired_rows {id: count}, retired_ids set,
    born_clause_ids a list of (clause-id, born-release-tuple) for the ordinal legs, retirements a list of
    (id, retired-release-tuple) for every tombstone and successor row, and successors a list of
    (id, successor-id, retired-release-tuple) for the successor-graph legs. Fail-closed (GateError, exit 2)
    on any malformed input: a born/tombstone/successor section that is present but not an array of tables, a
    row that is not a table, a born/tombstone/successor id that is neither a well-formed corpus-id nor
    clause-id, a born or retired release that is not a bare SemVer, a duplicate born row, or a successor-id
    that is empty, non-string, malformed, a self-loop, or dangling (no born row). An unreadable or malformed
    register must never read as an empty history."""
    try:
        data = load_toml(path)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read the id-history register {} ({})".format(path, exc))
    born = {}
    born_section = data.get("born", [])
    if not isinstance(born_section, list):
        raise GateError("id-history: the born section is not an array of tables")
    for row in born_section:
        if not isinstance(row, dict):
            raise GateError("id-history born row is not a table: {!r}".format(row))
        rid = _require_str(row, "id", "id-history born row")
        if not _valid_register_id(rid):
            raise GateError("id-history born row: id {!r} is neither a well-formed corpus-id nor a "
                            "well-formed clause-id".format(rid))
        rel = _require_release(row, "born-release", "id-history born row {!r}".format(rid))
        if rid in born:
            raise GateError("id-history: duplicate born row for {!r}".format(rid))
        born[rid] = rel
    retired_rows, retired_ids = {}, set()
    retirements, successors = [], []
    for kind in ("tombstone", "successor"):
        section = data.get(kind, [])
        if not isinstance(section, list):
            raise GateError("id-history: the {} section is not an array of tables".format(kind))
        for row in section:
            if not isinstance(row, dict):
                raise GateError("id-history {} row is not a table: {!r}".format(kind, row))
            rid = _require_str(row, "id", "id-history {} row".format(kind))
            if not _valid_register_id(rid):
                raise GateError("id-history {} row: id {!r} is neither a well-formed corpus-id nor a "
                                "well-formed clause-id".format(kind, rid))
            rel = _require_release(row, "retired-release", "id-history {} row {!r}".format(kind, rid))
            retired_at = _semver(rel)
            if kind == "successor":
                succ = _require_str(row, "successor-id", "id-history successor row {!r}".format(rid))
                if not _valid_register_id(succ):
                    raise GateError("id-history successor row {!r}: successor-id {!r} is neither a "
                                    "well-formed corpus-id nor a well-formed clause-id".format(rid, succ))
                if succ == rid:
                    raise GateError("id-history successor row {!r}: successor-id equals its own id "
                                    "(self-loop)".format(rid))
                if succ not in born:
                    raise GateError("id-history successor row {!r}: successor-id {!r} has no born row "
                                    "(dangling)".format(rid, succ))
                successors.append((rid, succ, retired_at))
            retirements.append((rid, retired_at))
            retired_rows[rid] = retired_rows.get(rid, 0) + 1
            retired_ids.add(rid)
    born_clause_ids = [(rid, _semver(rel)) for rid, rel in born.items() if is_clause_id(rid)]
    return born, retired_rows, retired_ids, born_clause_ids, retirements, successors


def _clause_rows(data, where):
    rows = data.get("clause")
    if not isinstance(rows, list):
        raise GateError("{}: no [[clause]] array".format(where))
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise GateError("{}: clause row #{} is not a table ({!r})".format(where, i, row))
    return rows


def load_inventory(path):
    """Parse the clause inventory into its list of row tables. Fail-closed on an unreadable file or a
    missing [[clause]] array."""
    try:
        data = load_toml(path)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read the clause inventory {} ({})".format(path, exc))
    return _clause_rows(data, "clause inventory {}".format(path))


def load_prev_ids(path):
    """Read ONLY the id set (each clause-id and its corpus-id) from a predecessor inventory, for the
    cross-release disappearance leg. Fail-closed on an unreadable file AND on any row whose clause-id is
    missing, non-string, or malformed (a guard is only as good as its input: a predecessor id this loader
    cannot parse must never silently shrink the disappearance leg's coverage; exit 2, never a skipped
    row). Spans and digests of a past release are not re-validated (they point at past sources this tree
    may not carry)."""
    try:
        data = load_toml(path)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read the predecessor inventory {} ({})".format(path, exc))
    ids = set()
    for i, row in enumerate(_clause_rows(data, "predecessor inventory {}".format(path)), 1):
        cid = row.get("clause-id")
        parsed = split_clause_id(cid) if isinstance(cid, str) else None
        if parsed is None:
            raise GateError("predecessor inventory {} row #{}: missing, non-string, or malformed "
                            "clause-id {!r}".format(path, i, cid))
        ids.add(cid)
        ids.add(parsed[0])
    return ids


def load_rule_corpus_ids(rules_dir):
    """Return {corpus-id: source Path} for every rule source under rules_dir, using gen_rules.parse_source
    for the frontmatter. Fail-closed (GateError) on an unlistable dir or a source whose frontmatter has no
    valid corpus-id: an unreadable corpus must never read as an empty set of rules."""
    try:
        sources = sorted(walk_files(rules_dir, {".git", "__pycache__"}, suffixes={".md"}))
    except OSError as exc:
        raise GateError("cannot list the rules dir {} ({})".format(rules_dir, exc))
    out = {}
    for src in sources:
        try:
            fm = parse_source(src)
        except (OSError, ValueError) as exc:
            raise GateError("cannot parse rule source {} ({})".format(src, exc))
        corpus = fm.get("corpus-id")
        if not isinstance(corpus, str) or not CID_RE.fullmatch(corpus):
            raise GateError("rule source {} has a missing or malformed corpus-id".format(src))
        if corpus in out:
            raise GateError("rule corpus-id {!r} used by both {} and {}".format(corpus, out[corpus], src))
        out[corpus] = src
    return out


# --- per-row span / text / digest (7.2 in-file legs) ------------------------------------------------

def _span_content(text, start, end):
    """The source file's lines[start-1:end] joined with LF, no trailing newline. Returns None if the span
    does not resolve inside the file (start < 1, end < start, or end past the last content line). A
    canonical file ends in exactly one LF, so split('\\n') yields a trailing '' that is not a content
    line; content lines are 1..len-1."""
    lines = text.split("\n")
    content_lines = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or end > content_lines:
        return None
    return "\n".join(lines[start - 1:end])


SENTENCE_ENDS = ".?!"  # a sentence end
CLAUSE_SEPARATOR = ";"  # the one separator that closes a whole clause (a ':' never does)
QUOTE_PAIRS = {"\u201c": "\u201d", '"': '"'}  # double quotes, curly and straight: opener -> closer
BRACKET_PAIRS = {"(": ")", "[": "]", "{": "}"}  # opener -> closer
_ENDING_RE = re.compile(r"[{}](?P<closers>[{}]*)\Z".format(
    re.escape(SENTENCE_ENDS), re.escape("".join(QUOTE_PAIRS.values()) + "".join(BRACKET_PAIRS.values()))))
# what may follow a sentence end that closing quotes or brackets follow: an ASCII capital only, the one
# plain start of a new sentence (a digit or another opener may go on with the same sentence, as in
# '"Stop." 3 times in a row halts' or '"Stop." (the default) halts'); the paragraph or file end is
# handled before this test
_NEXT_START_RE = re.compile(r"[A-Z]")
CODE_MARK = "`"  # a run of backticks opens an inline code span; the next run of the same length closes it
_CODE_RUN_RE = re.compile("`+")
# what may not follow a BARE sentence end (one with no closer): an opener the enclosure scan pairs, a
# double quote, a bracket or a backtick ('Stop. (with no other words) is the command'), and, after a '?'
# or '!' only, a lower-case ASCII letter ('Ask why? before you delete'); a '.' before a lower-case word
# stays a sentence end, since a sentence may start with a command name ('in it. git checkout -- <path>')
_BARE_GOES_ON_RE = re.compile("[{}]".format(
    re.escape("".join(QUOTE_PAIRS) + "".join(BRACKET_PAIRS) + CODE_MARK)))
LOWER_CASE_ENDS = "?!"
_LOWER_START_RE = re.compile(r"[a-z]")
_PARAGRAPH_GAP_RE = re.compile(r"\n\s*\n")


def _paragraph_start(source, pos):
    """The index where pos's paragraph begins: just after the last blank line before pos, or 0."""
    start = 0
    for gap in _PARAGRAPH_GAP_RE.finditer(source, 0, pos):
        start = gap.end()
    return start


def _enclosures(source, pos):
    """Scan pos's paragraph of the whole source from its start up to pos for inline code spans, double
    quotes and brackets. Returns (open, closed): open is the list of (opener, index) still open at pos,
    innermost last (its length is pos's enclosure depth), and closed maps the index of each closer that
    closed an enclosure to its opener's index. A run of backticks opens an inline code span and the next
    run of the same length closes it; inside a code span every other character is text, and a code span
    opens inside a quotation or bracket too. A straight double quote closes an open straight quote on top
    and otherwise opens one; a curly opening quote opens and a curly closing quote closes an open curly
    quote on top. Inside a quotation a bracket is text; outside one a bracket opener opens and a bracket
    closer closes the same kind on top. Any other closer (one with no matching opener on top) is text, so a
    lettered list marker ("a) build") or a ")" in a quoted literal never counts, while an opener left
    unclosed (an inch mark, a stray backtick, a half-open interval "[0, 1)") holds the rest of its
    paragraph inside an enclosure."""
    stack, closed = [], {}
    i = _paragraph_start(source, pos)
    while i < pos:
        ch = source[i]
        top = stack[-1][0] if stack else None
        if ch == CODE_MARK:
            run = _CODE_RUN_RE.match(source, i).group()
            if top == run:
                closed[i] = stack.pop()[1]
            elif top is None or top[0] != CODE_MARK:
                stack.append((run, i))
            i += len(run)
            continue
        if top is not None and top[0] == CODE_MARK:
            pass  # inside a code span every other character is text
        elif top is not None and ch == QUOTE_PAIRS.get(top):
            closed[i] = stack.pop()[1]
        elif ch in QUOTE_PAIRS:
            stack.append((ch, i))
        elif any(opener in QUOTE_PAIRS for opener, _at in stack):
            pass  # inside a quotation a bracket is text
        elif ch in BRACKET_PAIRS:
            stack.append((ch, i))
        elif top is not None and ch == BRACKET_PAIRS.get(top):
            closed[i] = stack.pop()[1]
        i += 1
    return stack, closed


def _quoted_text(source, i):
    """True when the bracket closer at index i is text inside a quotation, as _enclosures reads it."""
    still_open, _closed = _enclosures(source, i)
    return source[i] in BRACKET_PAIRS.values() and any(opener in QUOTE_PAIRS for opener, _at in still_open)


def _is_boundary(source, pos):
    """True when position pos of the whole decoded source file is a sentence or clause boundary: the ONE
    WHOLE-CLAUSE EDGES grammar, used for a text's leading and trailing edge alike. pos is a boundary when
    (c) it is at the start or end of the file, or a blank line touches it (a paragraph or file edge; a
    single line break is never enough, so a row cannot drop a wrapped line); (a) whitespace sits at pos
    and the text before it, ignoring whitespace, ends with one of . ? ! either bare, where then the text
    after pos, ignoring whitespace, begins with no opener (a double quote, a bracket or a backtick) and,
    after a '?' or '!', with no lower-case ASCII letter (the sentence may go on there, as in 'Stop. (with
    no other words) is' and 'Ask why? before'; a '.' before a lower-case word stays an end, since a
    sentence may start with a command name), or followed directly by closing quotes or brackets, where
    then the text after pos, ignoring whitespace, begins with an ASCII capital (a new sentence plainly
    starts; a digit, another opener or a closer may go on with the same one), each closer either closes an
    enclosure or is a bracket that is text inside a quotation (as _enclosures reads it), the last closer
    closes one, and the outermost closed enclosure's opener itself sits at such a boundary (a sentence end
    inside a quotation or bracket that opened mid-sentence, or a quoted or bracketed sentence that a
    longer sentence goes on from, is no boundary); (b) whitespace sits at pos and the text before it ends
    with ';'; or it sits directly before a ';' that whitespace follows. A comma or a colon is never a
    boundary.
    Enclosure DEPTH, including an inline code span, is judged separately (_edge_findings): this is the
    boundary test alone."""
    while True:
        left, right = source[:pos], source[pos:]
        kept, rest = left.rstrip(), right.lstrip()
        gap = left[len(kept):] + right[:len(right) - len(rest)]
        if not kept or not rest or gap.count("\n") >= 2:
            return True
        if right[:1] == CLAUSE_SEPARATOR and right[1:2].isspace():
            return True
        if not gap:
            return False
        if kept.endswith(CLAUSE_SEPARATOR):
            return True
        ending = _ENDING_RE.search(kept)
        if ending is None:
            return False
        closers = ending.group("closers")
        if not closers:
            # a bare end: the sentence goes on before an opener, or before a lower-case word after '?' or '!'
            return not (_BARE_GOES_ON_RE.match(rest)
                        or (kept[-1] in LOWER_CASE_ENDS and _LOWER_START_RE.match(rest)))
        if not _NEXT_START_RE.match(rest):
            return False  # the sentence goes on after the closer ('"Done." is printed')
        _open, closed = _enclosures(source, len(kept))
        if any(i not in closed and not _quoted_text(source, i)
               for i in range(len(kept) - len(closers), len(kept))):
            return False  # a closer that closes nothing here (a single quote, or a stray closer)
        if len(kept) - 1 not in closed:
            return False  # the last closer is a bracket inside a quotation still open
        pos = closed[len(kept) - 1]  # the outermost enclosure's opener must itself sit at a boundary


def _edge_findings(source, begin, finish):
    """WHOLE-CLAUSE EDGES (Model C). source is the whole decoded source file and [begin, finish) is the
    one occurrence of a canonical-text in it (non-empty, no edge whitespace). Returns a list of edge
    problems, empty when both edges are sentence or clause boundaries of the file (_is_boundary) and both
    sit at enclosure depth 0 of the SOURCE (_enclosures, scanned from the edge's paragraph start), so any
    quotation or bracket the text touches at an edge is one it contains whole. Judged on the file, not the
    window or the text alone, so an edge at a line end or line start is checked against the next or
    previous line too, and an enclosure dropped at both edges is seen. Anything else is a cut inside a
    sentence, a clause, a quotation or a bracket (a truncated text), or one of the disclosed fail-closed
    over-fires."""
    problems = []
    if not _is_boundary(source, begin):
        problems.append("begins inside a clause (after {!r})".format(source[max(0, begin - 20):begin]))
    if not _is_boundary(source, finish):
        problems.append("ends inside a clause (before {!r})".format(source[finish:finish + 20]))
    for name, pos in (("begins", begin), ("ends", finish)):
        still_open, _closed = _enclosures(source, pos)
        if still_open:
            opener, at = still_open[-1]
            problems.append("{} inside a quotation, bracket or code span (the {!r} opened after {!r})".format(
                name, opener, source[max(0, at - 20):at]))
    return problems


def check_rows(root, rows, manifest_sources, rule_sources, rules_dir, row_spans=None):
    """PER-ROW (7.2), Model C. For each row: the source file is read (its whole raw bytes hashed for the
    digest and its text sliced for the window); the [start,end] block resolves inside the file and is
    treated as a locating WINDOW of whole lines; canonical-text occurs EXACTLY ONCE as a contiguous
    byte-exact substring within that window (an embedded line-wrap LF retained verbatim, no normalization,
    per 3.2 byte-literal), and that one occurrence begins on start-line and ends on end-line (a tight
    window); source-digest equals the file digest; the row's corpus-id equals the source file's frontmatter
    corpus-id. rule_sources is the loader's {corpus-id: scanned source Path} map: the row's source-path must
    resolve to a file under rules_dir and equal the scanned source for the row's corpus-id, else it is a
    finding (an out-of-rules-dir or wrong-file path). An empty or whitespace-only canonical-text never
    passes, and a canonical-text that begins or ends with whitespace is a finding (a leading or trailing
    space, tab, or newline would otherwise falsely satisfy the tight begin/end-line check). Zero or more
    than one occurrence, an untight occurrence, an occurrence whose edges are not whole-sentence or
    whole-clause edges of the source file (_edge_findings: a truncated canonical-text), or a digest
    disagreement is a finding. When manifest_sources
    is not None (the DEFERRED leg armed) the digest is also cross-checked against the manifest's SOURCES
    entry. Returns a list of finding strings; a required field of the wrong type, including a non-integer or
    boolean start-line/end-line or a source-digest that is not 64 lowercase hex characters, is a GateError
    (fail-closed). A well-formed-but-wrong digest (correct syntax, wrong value) stays an exit-1 finding.
    When row_spans is a dict, each row whose canonical-text occurs exactly once in its window adds that
    occurrence's (begin, end) index pair in the decoded source to row_spans[resolved source Path], for the
    text-coverage leg."""
    findings = []
    digest_cache = {}
    for i, row in enumerate(rows, 1):
        where = "clause row #{} ({})".format(i, row.get("clause-id"))
        source_path = row.get("source-path")
        text_field = row.get("canonical-text")
        digest_field = row.get("source-digest")
        start, end = row.get("start-line"), row.get("end-line")
        if not isinstance(source_path, str) or not source_path.strip():
            raise GateError("{}: missing or non-string source-path".format(where))
        if not isinstance(text_field, str):
            raise GateError("{}: missing or non-string canonical-text".format(where))
        if not isinstance(digest_field, str):
            raise GateError("{}: missing or non-string source-digest".format(where))
        if not SHA256_RE.fullmatch(digest_field):
            raise GateError("{}: source-digest {!r} is not 64 lowercase hex characters (malformed digest "
                            "syntax)".format(where, digest_field))
        for _name, _val in (("start-line", start), ("end-line", end)):
            if isinstance(_val, bool) or not isinstance(_val, int):
                raise GateError("{}: {} must be an integer, not {!r}".format(where, _name, _val))
        abs_path = root / source_path
        resolved = abs_path.resolve()
        if Path(source_path).is_absolute():
            findings.append("{}: source-path {} is absolute; the schema requires a repo-relative path "
                            "under the rules dir {} (an absolute path discards the root and bypasses "
                            "containment)".format(where, source_path, rules_dir))
        elif not resolved.is_relative_to(rules_dir.resolve()):
            findings.append("{}: source-path {} does not resolve to a file under the rules dir {}"
                            .format(where, source_path, rules_dir))
        else:
            expected = rule_sources.get(row.get("corpus-id"))
            if expected is None or resolved != expected.resolve():
                findings.append("{}: source-path {} is not the scanned rule source for corpus-id {!r}"
                                .format(where, source_path, row.get("corpus-id")))
        if source_path not in digest_cache:
            try:
                raw = read_source_bytes(abs_path)
            except FileNotFoundError:
                digest_cache[source_path] = ("missing", None, None)
            except OSError as exc:  # a permission or I/O error is environmental: fail closed
                raise GateError("{}: cannot read source file {} ({})".format(where, source_path, exc))
            else:
                try:
                    decoded = raw.decode("utf-8")
                except UnicodeDecodeError:
                    digest_cache[source_path] = ("non-utf8", None, None)
                else:
                    digest_cache[source_path] = ("ok", hashlib.sha256(raw).hexdigest(), decoded)
        status, digest, decoded = digest_cache[source_path]
        if status == "missing":
            findings.append("{}: source file {} does not exist".format(where, source_path))
            continue
        if status == "non-utf8":
            findings.append("{}: source file {} is not valid UTF-8".format(where, source_path))
            continue
        window = _span_content(decoded, start, end)
        if window is None:
            findings.append("{}: source span (start-line {!r}, end-line {!r}) does not resolve inside {}"
                            .format(where, start, end, source_path))
        elif not text_field.strip():
            findings.append("{}: canonical-text is empty or whitespace-only in {}"
                            .format(where, source_path))
        elif text_field[0].isspace() or text_field[-1].isspace():
            findings.append("{}: canonical-text begins or ends with whitespace; a tight window requires "
                            "non-whitespace boundaries (a leading or trailing space, tab, or newline would "
                            "falsely satisfy the begin/end-line check) in {}".format(where, source_path))
        else:
            # Model C: canonical-text must occur EXACTLY ONCE as a contiguous byte-exact substring within
            # the window, and that one occurrence must begin on start-line and end on end-line (a tight
            # window). Count with find(sub, idx + 1) so overlapping occurrences are counted too (str.count
            # misses them); the gate never picks the first occurrence.
            first = window.find(text_field)
            count, idx = 0, first
            while idx != -1:
                count += 1
                idx = window.find(text_field, idx + 1)
            if count == 0:
                findings.append("{}: canonical-text not found as a contiguous substring within the "
                                "[start,end] window of {}".format(where, source_path))
            elif count > 1:
                findings.append("{}: canonical-text occurs {} times within the [start,end] window of {}; "
                                "narrow the window or reword the source (the gate never picks the first "
                                "occurrence)".format(where, count, source_path))
            else:
                begins = window[:first].count("\n") == 0
                ends = window[:first + len(text_field)].count("\n") == (end - start)
                if not (begins and ends):
                    findings.append("{}: canonical-text resolves inside the window but does not begin on "
                                    "start-line and end on end-line (window not tight) in {}"
                                    .format(where, source_path))
                window_at = len("\n".join(decoded.split("\n")[:start - 1])) + (1 if start > 1 else 0)
                problems = _edge_findings(decoded, window_at + first, window_at + first + len(text_field))
                if row_spans is not None:
                    row_spans.setdefault(resolved, []).append(
                        (window_at + first, window_at + first + len(text_field)))
                if problems:
                    findings.append("{}: canonical-text {} in {}; a canonical-text begins and ends at a "
                                    "sentence or clause boundary outside any quotation or bracket, so "
                                    "this is a truncated row or a shape the edge grammar refuses (see "
                                    "WHOLE-CLAUSE EDGES)".format(where, " and ".join(problems), source_path))
        if digest_field != digest:
            findings.append("{}: source-digest does not match the SHA-256 of {}".format(where, source_path))
        try:
            frontmatter = parse_source(abs_path)
            fm_corpus = frontmatter.get("corpus-id")
        except (OSError, ValueError):
            fm_corpus = None
        if fm_corpus != row.get("corpus-id"):
            findings.append("{}: source file {} frontmatter corpus-id {!r} does not match the row's "
                            "corpus-id {!r}".format(where, source_path, fm_corpus, row.get("corpus-id")))
        if manifest_sources is not None:
            expected = manifest_sources.get(source_path)
            if expected is None:
                findings.append("{}: source file {} has no SOURCES entry in the manifest".format(where, source_path))
            elif expected != digest_field:
                findings.append("{}: source-digest disagrees with the manifest SOURCES digest for {}"
                                .format(where, source_path))
    return findings


# --- text coverage: every rule sentence lies inside a clause row or a recorded exemption ------------

EXEMPTIONS_REL = ".aiqt/core/clause-exemptions.toml"
EXEMPTION_KEYS = frozenset({"corpus-id", "text", "reason"})
_FRONTMATTER_CLOSE = "\n---\n"  # gen_rules.parse_source's closing fence
_TITLE_RE = re.compile(r"# \S")  # the one heading a rule body opens with: its title, not an obligation
# the characters the vendored renderer reads as a line ending: _preprocess_text in
# opf/tools/_vendor/marko/source.py rewrites CRLF, a bare CR and a form feed to LF, and the parser then cuts lines
# at LF only, so no other character ends a line there; the LF-only splitter refuses each, wherever it stands
_RENDERER_LINE_BREAKS = (("\r", "carriage return"), ("\f", "form feed"))
# an inline HTML comment, processing instruction, declaration or CDATA section, which the vendored renderer
# passes through as raw HTML that a browser does not show, so its text would sit in a unit unseen
_HIDDEN_INLINE_RE = re.compile(r"<[!?]")
# the vendored GFM table's row splitter and delimiter-cell test (TableRow in
# opf/tools/_vendor/marko/ext/gfm/elements.py), for _is_table_delimiter_row
_TABLE_CELL_SPLIT_RE = re.compile(r"\s*(?<!\\)\|\s*")
_TABLE_DELIMITER_CELL_RE = re.compile(r":?-+:?")
# CommonMark's HTML block kind 1 and kind 6 tag names (a superset: meta and source as well), the tags that
# open an HTML block even in the middle of a paragraph
_HTML_BLOCK_TAGS = ("address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|"
                    "dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|"
                    "header|hr|html|iframe|legend|li|link|main|menu|menuitem|meta|nav|noframes|ol|optgroup|"
                    "option|p|param|pre|script|search|section|source|style|summary|table|tbody|td|textarea|"
                    "tfoot|th|thead|title|tr|track|ul")
# a body line the splitter cannot classify wherever it stands, markup the WHOLE-CLAUSE EDGES grammar does not
# model and a line that can start a Markdown block even inside a paragraph: a heading (other than the
# title), a bullet item, an ordered item numbered 1, a blockquote, a line that opens with '|', a GFM table
# delimiter row with no leading pipe, a code fence, an HTML block start, a thematic break or setext
# underline, and any line that opens with whitespace (an indented code block or a nested item); whitespace
# is any \s character, wider than CommonMark's space and tab, since the vendored parser counts more of it
_UNCLASSIFIABLE_LINE_RE = re.compile(
    r"(?:#{1,6}(?:\s|$)|[-*+](?:\s|$)|0{0,8}1[.)](?:\s|$)|>|\||```|~~~|<[!?]"
    r"|</?(?:" + _HTML_BLOCK_TAGS + r")(?:[\s>]|/>|$)"
    r"|(?:[-*_=]\s*){3,}$|(?:=+|-+)\s*$|(?:-|:[:|\s]*-)[-:|\s]*$|\s)", re.IGNORECASE)
# a line the splitter cannot classify as the first line of a paragraph, where it starts a block though it would
# continue one: an ordered item of any number (any \d digits, as the vendored parser reads them); a line that opens
# with '<' and a letter or '/' and ends with '>', which holds every one-tag line (HTML block kind 7), a quoted '<' or
# '>' in it included, and more; and a line that opens with '[' and may be a link reference definition, which renders
# as no text: one with ']:' later on it, or one with no unescaped ']' on it, whose label may close on a later line
_UNCLASSIFIABLE_FIRST_LINE_RE = re.compile(
    r"(?:\d{1,9}[.)](?:\s|$)|<[A-Za-z/].*>\s*$|\[.*\]:|\[(?:[^\\\]]|\\.)*\\?$)")
_CUT_CANDIDATE_RE = re.compile(r"\s+|;")  # a boundary has whitespace at it or sits right before a ';'


def _is_table_delimiter_row(line):
    """True when the vendored GFM table reads line as a delimiter row (TableRow.match): the stripped line is
    cut at each unescaped '|' with the whitespace around it, an empty first and an empty last cell are
    dropped, at least one cell is left, and every cell BEGINS with '-' or ':-' (a prefix match, so '--x|--y'
    qualifies though GFM wants only '-' and ':' in a cell). Below a header line holding a '|' with as many
    cells, such a line makes a table, so the splitter refuses it wherever it stands."""
    cells = _TABLE_CELL_SPLIT_RE.split(line.strip())
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return bool(cells) and all(_TABLE_DELIMITER_CELL_RE.match(cell) for cell in cells)


def _has_letter_or_digit(text):
    """True when text holds a letter (str.isalpha) or a decimal digit (str.isdecimal). An underscore, which
    the regex class \\w admits, and punctuation or a symbol alone are neither, so such a unit is no sentence
    the splitter knows."""
    return any(ch.isalpha() or ch.isdecimal() for ch in text)


def _read_rule_text(src):
    """The decoded text of one rule source for the text-coverage leg. A file that cannot be read or is not
    UTF-8 is a GateError (cannot evaluate, exit 2), never an empty rule."""
    try:
        raw = Path(src).read_bytes()
    except OSError as exc:
        raise GateError("cannot read rule source {} for the text-coverage leg ({})".format(src, exc))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError("rule source {} is not valid UTF-8, so the text-coverage leg cannot evaluate it "
                        "({})".format(src, exc))


def _refuse_open_enclosure(source, where, still_open):
    """A paragraph or the file that ends with an opener still open (an inch mark, a stray backtick, a
    half-open interval) has no cut after that opener, so the splitter cannot classify it: GateError."""
    if still_open:
        opener, at = still_open[-1]
        raise GateError("{} line {}: a paragraph ends with the {!r} opened after {!r} still open, so the "
                        "text-coverage splitter cannot classify it; cannot evaluate".format(
                            where, source.count("\n", 0, at) + 1, opener, source[max(0, at - 20):at]))


def rule_units(source, where):
    """Cut one decoded rule source into the units the text-coverage leg checks. Returns (text_start, units):
    text_start is the index where the checked text begins (after the frontmatter and, when the body's first
    non-blank line is a `# ` title, after that line), and units is the ordered list of (begin, end) index
    pairs, one per sentence or clause, with no edge whitespace. The cuts are the positions the WHOLE-CLAUSE
    EDGES grammar reads as a boundary (_is_boundary) at enclosure depth 0 (_enclosures): the very test both
    edges of a row's canonical-text must pass, so a row's occurrence is always a run of whole units. A bare
    ';' between two clauses is no unit. Text the grammar does not model raises GateError (cannot evaluate,
    exit 2): anywhere in the source, a character the vendored renderer reads as a line ending
    (_RENDERER_LINE_BREAKS: a carriage return or a form feed), since the lines here are cut at LF only; no
    frontmatter; a title with no blank line after it; a line _UNCLASSIFIABLE_LINE_RE matches or
    _is_table_delimiter_row accepts; a checked line holding '<!' or '<?' (_HIDDEN_INLINE_RE); a paragraph that
    ends with an opener still open; and a unit with no letter or digit (_has_letter_or_digit). A line
    _UNCLASSIFIABLE_FIRST_LINE_RE matches is refused only as a paragraph's first line."""
    breaks = [(source.index(char), name) for char, name in _RENDERER_LINE_BREAKS if char in source]
    if breaks:
        at, name = min(breaks)
        raise GateError("{} line {}: a {} ({!r}), which the vendored renderer reads as a line ending (CommonMark "
                        "reads a carriage return as one too), so the text-coverage splitter, which cuts lines at "
                        "LF only, cannot classify the text; cannot evaluate".format(
                            where, source.count("\n", 0, at) + 1, name, source[at]))
    if not source.startswith("---\n") or source.find(_FRONTMATTER_CLOSE, 4) == -1:
        raise GateError("{}: no frontmatter, so the text-coverage leg cannot find the rule body; cannot "
                        "evaluate".format(where))
    body = source.find(_FRONTMATTER_CLOSE, 4) + len(_FRONTMATTER_CLOSE)
    text_start, offset, first, after_title, para_start = body, body, True, False, True
    for number, line in enumerate(source[body:].split("\n"), source.count("\n", 0, body) + 1):
        if line.strip():
            if after_title:
                raise GateError("{} line {}: the title heading has no blank line after it, so the "
                                "text-coverage splitter cannot classify {!r}; cannot evaluate".format(
                                    where, number, line))
            if first and _TITLE_RE.match(line):
                text_start, after_title = offset + len(line), True
            elif (_UNCLASSIFIABLE_LINE_RE.match(line) or _is_table_delimiter_row(line)
                  or (para_start and _UNCLASSIFIABLE_FIRST_LINE_RE.match(line))):
                raise GateError("{} line {}: the text-coverage splitter cannot classify {!r} (markup "
                                "the sentence grammar does not model); cannot evaluate".format(where, number, line))
            elif _HIDDEN_INLINE_RE.search(line):
                raise GateError("{} line {}: the text-coverage splitter cannot classify {!r} (an inline HTML "
                                "comment, processing instruction or declaration, '<!' or '<?', whose text a "
                                "renderer does not show); cannot evaluate".format(where, number, line))
            first = para_start = False
        else:
            after_title, para_start = False, True
        offset += len(line) + 1
    cuts = {text_start, len(source)}
    for match in _CUT_CANDIDATE_RE.finditer(source, text_start):
        pos = match.start()
        if not _is_boundary(source, pos):
            continue
        still_open, _closed = _enclosures(source, pos)
        if not still_open:
            cuts.add(pos)
        elif match.group().count("\n") >= 2 or match.end() == len(source):
            _refuse_open_enclosure(source, where, still_open)
    _refuse_open_enclosure(source, where, _enclosures(source, len(source))[0])
    units = []
    ordered = sorted(cuts)
    for begin, end in zip(ordered, ordered[1:]):
        piece = source[begin:end]
        text = piece.strip()
        if not text or text == CLAUSE_SEPARATOR:
            continue
        if not _has_letter_or_digit(text):
            raise GateError("{} line {}: the text-coverage splitter cannot classify {!r} (no letter "
                            "or digit); cannot evaluate".format(where, source.count("\n", 0, begin) + 1, text))
        lead = len(piece) - len(piece.lstrip())
        units.append((begin + lead, begin + lead + len(text)))
    return text_start, units


def load_exemptions(path):
    """Parse the clause exemptions into their list of row tables. The file is required and must hold the
    `exempt` key: an absent file (a dangling symlink included) and a file with no `exempt` key (an empty
    file, a comment-only file, a symlink to /dev/null) are a GateError (exit 2), never read as no exemption,
    because the file carries findings of its own (a stale, repeated, redundant or reasonless exemption) that
    reading it as empty would hide; an intentionally empty register is the explicit `exempt = []`. Every
    other unreadable or malformed input is a GateError (exit 2) too: not readable, not TOML, a top-level key
    other than `exempt`, an `exempt` that is not an array of tables, a row whose keys are not exactly
    corpus-id, text and reason, a value that is not a string, or a corpus-id that is not a well-formed
    corpus-id. The gate cannot detect a valid replacement of the content: rows removed, or `exempt = []`
    written in place of the register, read as the reviewed register."""
    try:
        data = load_toml(path)
    except FileNotFoundError:
        raise GateError("the clause exemptions file {} does not exist; it is required, so an absent file is "
                        "a cannot-evaluate, never read as no exemption".format(path))
    except (OSError, ValueError) as exc:
        raise GateError("cannot read the clause exemptions {} ({})".format(path, exc))
    if set(data) - {"exempt"}:
        raise GateError("clause exemptions {}: the only top-level key is 'exempt' (found {})".format(
            path, sorted(data)))
    if "exempt" not in data:
        raise GateError("clause exemptions {} has no 'exempt' key (an empty or comment-only file), which is "
                        "never read as no exemption; an intentionally empty register is the explicit "
                        "'exempt = []'".format(path))
    rows = data["exempt"]
    if not isinstance(rows, list):
        raise GateError("clause exemptions {}: 'exempt' is not an array of tables".format(path))
    for i, row in enumerate(rows, 1):
        where = "clause exemption #{} in {}".format(i, path)
        if not isinstance(row, dict):
            raise GateError("{}: not a table ({!r})".format(where, row))
        if set(row) != EXEMPTION_KEYS:
            raise GateError("{}: the keys are exactly corpus-id, text and reason (found {})".format(
                where, sorted(row)))
        for key in sorted(EXEMPTION_KEYS):
            if not isinstance(row[key], str):
                raise GateError("{}: {} is not a string ({!r})".format(where, key, row[key]))
        if not CID_RE.fullmatch(row["corpus-id"]):
            raise GateError("{}: corpus-id {!r} is not a well-formed corpus-id".format(
                where, row["corpus-id"]))
    return rows


def _exemption_findings(i, row):
    """The findings (exit 1) on one well-typed exemption row by itself: an empty or whitespace-only reason,
    and a text that is empty, whitespace-only, or begins or ends with whitespace."""
    findings = []
    where = "clause exemption #{} ({!r})".format(i, row["corpus-id"])
    if not row["reason"].strip():
        findings.append("{}: no reason; an exemption records why its text needs no clause row".format(where))
    text = row["text"]
    if not text.strip():
        findings.append("{}: text is empty or whitespace-only".format(where))
    elif text[0].isspace() or text[-1].isspace():
        findings.append("{}: text begins or ends with whitespace; an exemption names whole sentences or "
                        "clauses".format(where))
    return findings


def _locate_exemption(source, text_start, text):
    """Find an exemption's text in a rule body. Returns ((begin, end), None) for its one occurrence at or
    after text_start whose edges are boundaries at enclosure depth 0 (_edge_findings, the row grammar), or
    (None, problem): no occurrence (stale), more than one, or an edge inside a sentence or enclosure."""
    first = source.find(text, text_start)
    count, idx = 0, first
    while idx != -1:
        count += 1
        idx = source.find(text, idx + 1)
    if count == 0:
        return None, "its text no longer occurs in the rule body (a stale exemption)"
    if count > 1:
        return None, "its text occurs {} times in the rule body; an exemption names one place".format(count)
    problems = _edge_findings(source, first, first + len(text))
    if problems:
        return None, "its text {}; an exemption names whole sentences or clauses".format(" and ".join(problems))
    return (first, first + len(text)), None


def _unknown_corpus_findings(by_corpus, rule_sources):
    """One finding per exemption whose corpus-id names no rule source (a stale exemption)."""
    findings = []
    for corpus in sorted(set(by_corpus) - set(rule_sources)):
        for i, _row in by_corpus[corpus]:
            findings.append("clause exemption #{}: corpus-id {!r} names no rule source (a stale "
                            "exemption)".format(i, corpus))
    return findings


def _uncovered_findings(corpus, src, source, units, covered, exempt):
    """One finding per unit that no clause row covers and no exemption names."""
    findings = []
    for k, (begin, end) in enumerate(units):
        if not covered[k] and not exempt[k]:
            findings.append("rule {} ({}) line {}: {!r} lies inside no clause row's canonical-text and "
                            "no recorded exemption; give it a clause row (with its id-history born row) or an "
                            "exemption with a reason in {}".format(
                                corpus, src, source.count("\n", 0, begin) + 1, source[begin:end],
                                EXEMPTIONS_REL))
    return findings


def check_text_coverage(rule_sources, row_spans, exemptions):
    """TEXT COVERAGE. rule_sources is the loader's {corpus-id: source Path}; row_spans is check_rows'
    {resolved source Path: [(begin, end), ...]}; exemptions is load_exemptions' row list. Every unit of
    every rule body (rule_units) must lie inside some row span or inside one exemption's located text.
    Returns (findings, (units, covered, exempted)). A rule source that cannot be read or classified raises
    GateError (cannot evaluate), never a pass."""
    findings = []
    by_corpus = {}
    for i, row in enumerate(exemptions, 1):
        findings += _exemption_findings(i, row)
        by_corpus.setdefault(row["corpus-id"], []).append((i, row))
    findings += _unknown_corpus_findings(by_corpus, rule_sources)
    n_units = n_covered = n_exempt = 0
    for corpus, src in sorted(rule_sources.items()):
        source = _read_rule_text(src)
        text_start, units = rule_units(source, src)
        spans = row_spans.get(Path(src).resolve(), [])
        covered = [any(a <= begin and end <= b for a, b in spans) for begin, end in units]
        exempt = [False] * len(units)
        seen = {}
        for i, row in by_corpus.get(corpus, []):
            text = row["text"]
            if not text.strip() or text[0].isspace() or text[-1].isspace():
                continue  # _exemption_findings has reported it
            where = "clause exemption #{} ({!r} in {})".format(i, corpus, src)
            if text in seen:
                findings.append("{}: repeats exemption #{}".format(where, seen[text]))
                continue
            seen[text] = i
            hit, problem = _locate_exemption(source, text_start, text)
            if problem is not None:
                findings.append("{}: {}".format(where, problem))
                continue
            inside = [k for k, (begin, end) in enumerate(units) if hit[0] <= begin and end <= hit[1]]
            if not inside:
                findings.append("{}: its text names no sentence or clause".format(where))
            for k in inside:
                if covered[k]:
                    findings.append("{}: a clause row already covers {!r}, so the exemption is "
                                    "redundant; drop it".format(where, source[units[k][0]:units[k][1]]))
                exempt[k] = True
        findings += _uncovered_findings(corpus, src, source, units, covered, exempt)
        n_units += len(units)
        n_covered += sum(covered)
        n_exempt += sum(1 for k in range(len(units)) if exempt[k] and not covered[k])
    return findings, (n_units, n_covered, n_exempt)


def load_manifest_sources(path):
    """The DEFERRED leg's input: {repo-relative path: sha256} from the manifest's SOURCES section
    (reconciled with gen_manifest.py: an array of [[sources]] tables carrying exactly path, bytes, and
    sha256). Fail-closed on an unreadable or malformed manifest, a duplicate path (never a silent
    dict-overwrite), a non-64-lowercase-hex digest, or a negative or non-integer size."""
    try:
        data = load_toml(path)
    except (OSError, ValueError) as exc:
        raise GateError("cannot read the manifest {} ({})".format(path, exc))
    sources = data.get("sources")
    if not isinstance(sources, list):
        raise GateError("manifest {}: no [[sources]] array".format(path))
    out = {}
    for row in sources:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise GateError("manifest {}: a sources row's keys are not exactly path/bytes/sha256"
                            .format(path))
        p = _require_str(row, "path", "manifest sources row")
        digest = _require_str(row, "sha256", "manifest sources row")
        if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest):
            raise GateError("manifest {}: sha256 for {!r} is not 64 lowercase hex".format(path, p))
        if not isinstance(row["bytes"], int) or isinstance(row["bytes"], bool) or row["bytes"] < 0:
            raise GateError("manifest {}: bytes for {!r} is not a non-negative integer".format(path, p))
        if p in out:
            raise GateError("manifest {}: duplicate sources path {!r}".format(path, p))
        out[p] = digest
    return out


# --- orchestration ----------------------------------------------------------------------------------

def run(root, rules_dir, inventory_path, register_path, prev_inventory_path=None, genesis=False,
        manifest_path=None, exemptions_path=None):
    """Run every leg. Returns the exit code 0/1/2. Parameterized on paths so the self-test drives synthetic
    tempdir fixtures, never the real corpus. exemptions_path None reads no exemption file (every rule unit
    then needs a clause row), which only the self-test's fixtures use; the command line always names a
    file, and load_exemptions refuses an absent one (exit 2)."""
    try:
        rule_corpus_ids = load_rule_corpus_ids(rules_dir)
        rows = load_inventory(inventory_path)
        born, retired_rows, retired_ids, born_clause_ids, retirements, successors = load_register(register_path)
        prev_ids = load_prev_ids(prev_inventory_path) if prev_inventory_path is not None else None
        manifest_sources = load_manifest_sources(manifest_path) if manifest_path is not None else None
        exemptions = load_exemptions(exemptions_path) if exemptions_path is not None else []

        if genesis:
            if prev_inventory_path is not None:
                raise GateError("--genesis and --prev-inventory are mutually exclusive: genesis has no "
                                "predecessor inventory")
            if retired_ids:
                raise GateError("--genesis declared but the id-history register carries {} retirement "
                                "row(s); genesis has no tombstones yet".format(len(retired_ids)))

        live_ids, inventory_ids = set(), set()
        for row in rows:
            cid = row.get("clause-id")
            if isinstance(cid, str):
                live_ids.add(cid)
                inventory_ids.add(cid)
                parsed = split_clause_id(cid)
                if parsed is not None:
                    live_ids.add(parsed[0])
                    inventory_ids.add(parsed[0])
            corpus = row.get("corpus-id")
            if isinstance(corpus, str):
                inventory_ids.add(corpus)

        findings = []
        findings += check_scheme(rows, set(rule_corpus_ids))
        findings += check_no_nesting(rows)
        row_spans = {}
        findings += check_rows(root, rows, manifest_sources, rule_corpus_ids, rules_dir, row_spans)
        findings += check_cumulative_max(born_clause_ids)
        findings += check_register_integrity(born, retirements, successors)
        findings += check_resurrection(inventory_ids, retired_ids)
        completeness, na = check_completeness(inventory_ids, set(born), retired_rows, live_ids, prev_ids)
        findings += completeness
        coverage, (n_units, n_covered, n_exempt) = check_text_coverage(rule_corpus_ids, row_spans, exemptions)
        findings += coverage
    except GateError as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2

    print("checked {} clause row(s) against {} rule source(s); text coverage: {} sentence or clause "
          "unit(s), {} inside a clause row, {} exempted by {} exemption row(s); cross-release disappearance "
          "leg {}; manifest-SOURCES leg {}".format(
              len(rows), len(rule_corpus_ids), n_units, n_covered, n_exempt, len(exemptions),
              "NOT APPLICABLE (no predecessor inventory)" if na else "ran",
              "armed" if manifest_path is not None else "deferred (off)"))
    if findings:
        print("FAIL: {} clause-inventory finding(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: clause-id scheme, per-row span/text/digest, cumulative-max ordinal, resurrection, "
          "completeness, and text coverage all hold")
    return 0


# --- self-test --------------------------------------------------------------------------------------
# Pure-logic cases (clause-id parsing, numeric ordinal comparison, the completeness/resurrection set
# logic) always run and are deterministic. The end-to-end fixture cases build a synthetic corpus in a
# private tempdir; where no writable tempdir exists they are a NAMED NOT RUN failure (never a skip
# beside a PASS). No wall clock, no randomness, no network.

def _canon(lines):
    """A canonical file body: LF-joined lines with exactly one trailing newline (3.1)."""
    return "\n".join(lines) + "\n"


def _rule_lines(corpus_id, obligations, paragraphs=True):
    """A tiny canonical rule source: frontmatter carrying corpus-id, then one body line per obligation.
    Returns (lines, {ordinal_index -> 1-based body line number}). Obligations are placed on their own
    lines so each clause span is a single line, keeping fixtures trivially verifiable. With paragraphs
    (the default) a blank line precedes each obligation, so each is a whole paragraph and its edges are
    whole-clause edges; paragraphs=False keeps the obligation lines adjacent, for a multi-line span."""
    lines = ["---", "corpus-id: {}".format(corpus_id), "origin: pack", "slug: fixture", "---", "",
             "# Fixture rule"]
    if not paragraphs:
        lines.append("")
    line_of = {}
    for idx, text in enumerate(obligations):
        if paragraphs:
            lines.append("")
        lines.append(text)
        line_of[idx] = len(lines)  # 1-based line number of this obligation
    return lines, line_of


def _toml_escape(value):
    """Escape a string for a TOML basic string. Only canonical-text needs it: an embedded line-wrap LF is
    emitted as a \\n escape that tomllib round-trips back to a real LF, so a multi-line Model C obligation
    can be expressed in a single-line TOML value."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _toml_clause(row):
    out = ["[[clause]]",
           'clause-id = "{}"'.format(row["clause-id"]),
           'corpus-id = "{}"'.format(row["corpus-id"]),
           'source-path = "{}"'.format(row["source-path"]),
           "start-line = {}".format(row["start-line"]),
           "end-line = {}".format(row["end-line"]),
           'canonical-text = "{}"'.format(_toml_escape(row["canonical-text"])),
           'source-digest = "{}"'.format(row["source-digest"]),
           ""]
    return "\n".join(out)


def _toml_register(born, tombstones, successors):
    out = []
    for rid, rel in born:
        out += ["[[born]]", 'id = "{}"'.format(rid), 'born-release = "{}"'.format(rel), ""]
    for rid, rel in tombstones:
        out += ["[[tombstone]]", 'id = "{}"'.format(rid), 'retired-release = "{}"'.format(rel), ""]
    for rid, rel, succ in successors:
        out += ["[[successor]]", 'id = "{}"'.format(rid), 'retired-release = "{}"'.format(rel),
                'successor-id = "{}"'.format(succ), ""]
    return "\n".join(out)


def _sha_of(lines):
    return hashlib.sha256(_canon(lines).encode("utf-8")).hexdigest()


def _write_corpus(base, rules, clause_rows, born, tombstones, successors):
    """Write a synthetic corpus under base: rule sources under .aiqt/core/rules/, the inventory
    .aiqt/core/clauses.toml, and the register .aiqt/core/id-history.toml. rules maps corpus-id -> lines;
    clause_rows is the list of already-built clause-row dicts. Returns base."""
    rules_dir = base / ".aiqt" / "core" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for corpus_id, lines in rules.items():
        (rules_dir / "{}.md".format(corpus_id)).write_text(_canon(lines), encoding="utf-8")
    (base / ".aiqt" / "core" / "clauses.toml").write_text(
        "".join(_toml_clause(r) + "\n" for r in clause_rows), encoding="utf-8")
    (base / ".aiqt" / "core" / "id-history.toml").write_text(
        _toml_register(born, tombstones, successors), encoding="utf-8")
    return base


def _good_genesis():
    """A well-formed genesis corpus description: two rules, two clauses each, all born at genesis 1.1.0,
    no tombstones. Returns (rules, clause_rows, born) with real spans and digests."""
    rules, clause_rows, born = {}, [], []
    plan = {"calpha": ["alpha obligation one", "alpha obligation two"],
            "cbeta1": ["beta obligation one", "beta obligation two"]}
    for corpus_id, obligations in plan.items():
        lines, line_of = _rule_lines(corpus_id, obligations)
        rules[corpus_id] = lines
        digest = _sha_of(lines)
        born.append((corpus_id, "1.1.0"))
        for idx, text in enumerate(obligations):
            ordinal = idx + 1
            clause_id = "{}.{}".format(corpus_id, ordinal)
            ln = line_of[idx]
            clause_rows.append({"clause-id": clause_id, "corpus-id": corpus_id,
                                "source-path": ".aiqt/core/rules/{}.md".format(corpus_id),
                                "start-line": ln, "end-line": ln, "canonical-text": text,
                                "source-digest": digest})
            born.append((clause_id, "1.1.0"))
    return rules, clause_rows, born


def _run_quiet(**kwargs):
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return run(**kwargs)


def _run_capture(**kwargs):
    """run() with its printed output captured: (exit code, stdout and stderr text)."""
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        code = run(**kwargs)
    return code, out.getvalue()


def _row_leg_quiet(base):
    """The PER-ROW leg's own verdict on a fixture corpus under base: 0 clean, 1 a finding, 2 malformed input
    (a GateError). The WHOLE-CLAUSE EDGES vectors use it because each fixture rule holds text beside its one
    row on purpose, and some hold markup the TEXT COVERAGE leg refuses, so the whole gate would answer about
    coverage rather than about the row; the TEXT COVERAGE leg has its own vectors."""
    rules_dir = base / ".aiqt" / "core" / "rules"
    try:
        sources = load_rule_corpus_ids(rules_dir)
        rows = load_inventory(base / ".aiqt" / "core" / "clauses.toml")
        return 1 if check_rows(base, rows, None, sources, rules_dir) else 0
    except GateError:
        return 2


def _toml_exemptions(rows):
    """Exemption rows as `[[exempt]]` tables; a string value is escaped and any other value written as is,
    so a fixture can carry a wrong-typed value."""
    out = []
    for row in rows:
        out.append("[[exempt]]")
        for key, value in row.items():
            out.append('{} = {}'.format(key, '"{}"'.format(_toml_escape(value)) if isinstance(value, str) else value))
        out.append("")
    return "\n".join(out)


def _paths(base, **overrides):
    kw = {"root": base, "rules_dir": base / ".aiqt" / "core" / "rules",
          "inventory_path": base / ".aiqt" / "core" / "clauses.toml",
          "register_path": base / ".aiqt" / "core" / "id-history.toml"}
    kw.update(overrides)
    return kw


def _not_run(failures, case, cause):
    """Record a self-test case that could not run as a NAMED failure (a counted cannot-evaluate that
    fails the run), never a printed note beside a PASS: coverage that did not run is not coverage."""
    failures.append("NOT RUN: {}: {}; coverage that did not run FAILS the self-test, never a silent "
                    "PASS".format(case, cause))


_E2E_CASES = "every end-to-end fixture case (a synthetic corpus per fail mode in a private tempdir)"


def _not_run_vector(failures):
    """Red-on-revert vector for the skip that printed SELF-TEST PASS (PARTIAL) and exited 0: a nested
    self-test run with an unavailable temp directory (mkdtemp raises OSError) must exit nonzero AND name
    the end-to-end fixture cases on a NOT RUN line."""
    def _unavailable(*_args, **_kwargs):
        raise OSError("injected: no writable temp directory")

    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rc = self_test_main(_mkdtemp=_unavailable, _vectors=False)
    want = "NOT RUN: {}: no writable temp directory".format(_E2E_CASES)
    if rc == 0 or want not in out.getvalue():
        failures.append("(NOT RUN vector) an unavailable temp directory must exit nonzero AND record {!r}, "
                        "never a PASS (PARTIAL) exit 0 (got exit {})".format(want, rc))


def self_test_main(_mkdtemp=None, _vectors=True):  # noqa: C901  a flat sequence of fixture cases
    failures = []

    # --- always-run pure-logic cases (no filesystem) ----------------------------------------------
    scheme_ok = [("calpha.1", ("calpha", 1)), ("calpha.10", ("calpha", 10)), ("calpha.100", ("calpha", 100))]
    for cid, expect in scheme_ok:
        if split_clause_id(cid) != expect:
            failures.append("split_clause_id({!r}) != {!r}".format(cid, expect))
    for bad in ["calpha.01", "calpha.0", "calpha.-1", "calpha.1.2", "calpha", "short.1", "calpha.", "calpha.1a",
                "calpha.1\n", "calpha\n.1", "calpha\n"]:
        if split_clause_id(bad) is not None:
            failures.append("split_clause_id({!r}) should be None (malformed)".format(bad))
    # A trailing-LF id is not a well-formed register id either (fullmatch, not $-anchored match).
    for bad in ["calpha\n", "calpha.1\n"]:
        if _valid_register_id(bad):
            failures.append("_valid_register_id({!r}) should be False (trailing newline)".format(bad))
    # An unbounded (>4300-digit) ordinal is treated as malformed rather than crashing CPython's int() guard:
    # split_clause_id returns None (the caller reports a scheme finding) instead of raising ValueError.
    if split_clause_id("calpha." + ("1" * 5000)) is not None:
        failures.append("split_clause_id should treat a >4300-digit ordinal as malformed (None), not crash")

    # Numeric, not lexicographic, ordinal comparison in cumulative-max, both directions.
    lex_trap_pass = [("calpha.9", (1, 1, 0)), ("calpha.10", (1, 2, 0))]  # 10 > 9 numerically -> clean
    if check_cumulative_max(lex_trap_pass):
        failures.append("cumulative-max wrongly flagged ordinal 10 vs prior 9 (lexicographic trap)")
    lex_trap_fail = [("calpha.10", (1, 1, 0)), ("calpha.2", (1, 2, 0))]  # 2 <= 10 numerically -> finding
    if not check_cumulative_max(lex_trap_fail):
        failures.append("cumulative-max missed ordinal 2 not exceeding prior 10")
    # Release-agnostic: a later-release clause-id below the historical maximum is caught regardless of any
    # unrelated newer row; no single global newest can disable the check.
    if not check_cumulative_max([("calpha.10", (1, 0, 0)), ("calpha.2", (1, 5, 0))]):
        failures.append("cumulative-max missed a later-release ordinal 2 below historical max 10 "
                        "(release-agnostic)")

    if check_resurrection({"calpha", "calpha.1"}, {"cbeta1.9"}):
        failures.append("resurrection wrongly flagged a disjoint retired set")
    if not check_resurrection({"calpha.2"}, {"calpha.2"}):
        failures.append("resurrection missed a live id equal to a tombstoned id")

    # check_no_nesting: identical sibling text is allowed (Model A); a proper substring is not; and
    # containment across DIFFERENT corpus-ids is ignored (the invariant is within-rule only).
    if check_no_nesting([{"clause-id": "calpha.1", "canonical-text": "same whole sentence"},
                         {"clause-id": "calpha.2", "canonical-text": "same whole sentence"}]):
        failures.append("no-nesting wrongly flagged two clauses sharing identical canonical-text")
    if not check_no_nesting([{"clause-id": "calpha.1", "canonical-text": "a fragment"},
                             {"clause-id": "calpha.2", "canonical-text": "a fragment plus more"}]):
        failures.append("no-nesting missed a clause whose text is a proper substring of another's")
    if check_no_nesting([{"clause-id": "calpha.1", "canonical-text": "alpha one"},
                         {"clause-id": "cbeta1.1", "canonical-text": "alpha"}]):
        failures.append("no-nesting wrongly flagged a substring across different corpus-ids")

    comp, na = check_completeness({"calpha.1"}, set(), {}, {"calpha.1"}, None)
    if not comp or not na:
        failures.append("completeness: a missing born row was not flagged, or na not reported without a predecessor")
    comp, _na = check_completeness({"calpha.1"}, {"calpha.1"}, {}, {"calpha.1"}, {"calpha.1", "cbeta1.2"})
    if not any("unexplained disappearance" in f for f in comp):
        failures.append("completeness: an unexplained disappearance was not flagged with a predecessor")

    # A born id that is neither live in the inventory nor carried by a retirement row is a phantom; a
    # born id that is live is not.
    comp, _na = check_completeness(set(), {"cghost.1"}, {}, set(), None)
    if not any("phantom born id" in f for f in comp):
        failures.append("completeness: a phantom born id (not live, not retired) was not flagged")
    comp, _na = check_completeness({"calpha.1"}, {"calpha.1"}, {}, {"calpha.1"}, None)
    if any("phantom born id" in f for f in comp):
        failures.append("completeness: a live born id was wrongly flagged as phantom")

    # A born clause-id whose owning corpus-id has no born row is an orphan; one whose corpus DOES have a
    # born row is not. (corphn.1 is retired, so it is neither live nor a phantom: only the orphan leg fires.)
    comp, _na = check_completeness(set(), {"corphn.1"}, {"corphn.1": 1}, set(), None)
    if not any("orphan clause-id" in f for f in comp):
        failures.append("completeness: an orphan-corpus born clause-id (no born corpus) was not flagged")
    comp, _na = check_completeness(set(), {"corphn", "corphn.1"}, {"corphn": 1, "corphn.1": 1}, set(), None)
    if any("orphan clause-id" in f for f in comp):
        failures.append("completeness: a born clause-id with a born corpus was wrongly flagged as orphan")

    # check_register_integrity: retirement before birth, a successor born after the retirement pointing to
    # it, and a multi-node successor cycle are findings; a valid retirement and an acyclic edge are not.
    if not check_register_integrity({"calpha.1": "2.0.0"}, [("calpha.1", _semver("1.0.0"))], []):
        failures.append("register-integrity missed a retirement-release before the born-release")
    if check_register_integrity({"calpha.1": "1.0.0"}, [("calpha.1", _semver("1.4.0"))], []):
        failures.append("register-integrity wrongly flagged a retirement at or after the born-release")
    if not check_register_integrity({"calpha.1": "1.0.0", "cbeta1.1": "2.0.0"},
                                    [("calpha.1", _semver("1.5.0"))],
                                    [("calpha.1", "cbeta1.1", _semver("1.5.0"))]):
        failures.append("register-integrity missed a successor born after the retirement pointing to it")
    if not check_register_integrity({"calpha.1": "1.0.0", "cbeta1.1": "1.0.0"}, [],
                                    [("calpha.1", "cbeta1.1", _semver("1.4.0")),
                                     ("cbeta1.1", "calpha.1", _semver("1.4.0"))]):
        failures.append("register-integrity missed a two-node successor cycle")
    if check_register_integrity({"calpha.1": "1.0.0", "cbeta1.1": "1.0.0"}, [],
                                [("calpha.1", "cbeta1.1", _semver("1.4.0"))]):
        failures.append("register-integrity wrongly flagged an acyclic successor edge")

    # Deferred item 2 (successor-already-retired temporal leg). An edge whose successor-id was itself
    # retired at a STRICTLY EARLIER release than the edge is a resurrection through the register.
    # born: calpha.1 and cbeta1.1 both at 1.0.0; retirements: cbeta1.1 tombstoned at 1.1.0; successor
    # edge calpha.1 -> cbeta1.1 at 1.2.0 (1.1.0 < 1.2.0) -> a finding.
    if not check_register_integrity(
            {"calpha.1": "1.0.0", "cbeta1.1": "1.0.0"},
            [("cbeta1.1", _semver("1.1.0")), ("calpha.1", _semver("1.2.0"))],
            [("calpha.1", "cbeta1.1", _semver("1.2.0"))]):
        failures.append("register-integrity missed a successor-id already retired at an earlier release")
    # A legal chain (a -> b at R1, b -> c at R2) keeps b in the cumulative retired set but never retires
    # b BEFORE the a->b edge, so the temporal form stays clean (membership would false-positive here).
    if check_register_integrity(
            {"ca.1": "1.0.0", "cb.1": "1.0.0", "cc.1": "1.0.0"},
            [("ca.1", _semver("1.1.0")), ("cb.1", _semver("1.2.0"))],
            [("ca.1", "cb.1", _semver("1.1.0")), ("cb.1", "cc.1", _semver("1.2.0"))]):
        failures.append("register-integrity wrongly flagged a legal multi-step successor chain")
    # A same-release pass-through fold (a and b both retired at R1, a -> b at R1) is permitted: the
    # comparison is strict, so an equal retirement release is not a resurrection.
    if check_register_integrity(
            {"ca.1": "1.0.0", "cb.1": "1.0.0"},
            [("ca.1", _semver("1.1.0")), ("cb.1", _semver("1.1.0"))],
            [("ca.1", "cb.1", _semver("1.1.0"))]):
        failures.append("register-integrity wrongly flagged a same-release pass-through fold")

    # TEXT COVERAGE splitter (pure): the units are the row grammar's own boundaries at depth 0, the title
    # is no unit, a bare ';' is no unit, a sentence end inside a bracket opened mid-sentence is no cut, and
    # text with no frontmatter is a cannot-evaluate.
    probe = "---\ncorpus-id: cprobe\n---\n\n# Title words\n\nAlpha holds; beta waits. Gamma ends.\n\nA (bracketed aside. Here.) closes.\n"
    try:
        _at, probe_units = rule_units(probe, "probe")
        got = [probe[b:e] for b, e in probe_units]
        if got != ["Alpha holds", "beta waits.", "Gamma ends.",
                   "A (bracketed aside. Here.) closes."]:
            failures.append("rule_units: unexpected units {!r}".format(got))
    except GateError as exc:
        failures.append("rule_units: a plain rule body raised {}".format(exc))
    try:
        rule_units("# Title\n\nAlpha holds.\n", "probe")
        failures.append("rule_units: a body with no frontmatter must be a cannot-evaluate (GateError)")
    except GateError:
        pass
    # A line that may start a Markdown block where it stands is refused; a wrapped prose line that merely
    # begins with '#', a number or '<' continues its paragraph. An ordered item of any number, a line that
    # opens with a tag and ends with '>' (a quoted '<' or '>' in an attribute value included), and a line that
    # may open a link reference definition (its label closed by ']:' or left open to a later line) are
    # refused only as a paragraph's first line. Each refusal must be the classifier's ("markup"), not a
    # later refusal such as a unit with no letter or digit.
    head = "---\ncorpus-id: cprobe\n---\n\n# Title words\n\n"
    for line in ("#507 and its review.", "<path> only after a backup.", "2) when unsure.", "3.",
                 "<b>bold</b> stays inline.", "1234567890. is no list.", '<span title="a>b">',
                 "[foo\nbar]: /url", "<b>bold</b>", "\u0661. Beta holds.", "the <span hidden>draft</span> text."):
        try:
            rule_units(head + "See the start of the PR\n" + line + "\n", "probe")
        except GateError as exc:
            failures.append("rule_units: the wrapped prose line {!r} was refused: {}".format(line, exc))
    for line in ("# Beta heading", "###### Beta", "- beta item", "* beta item", "+ beta item", "1. beta item",
                 "01) beta item", "> beta quote", "| beta | cell |", "```", "~~~", "<!-- beta -->", "<?beta?>",
                 "<!DOCTYPE html>", "<div>beta</div>", "</DIV>", "<pre>", "---", "***", "___", "===", "=", "--",
                 "    beta indented", "\tbeta indented", "#", "*", "+", "<div/>", "--|--", ":-- | --:", "-|-",
                 "\xa0# beta heading", "\x0b- beta item", "#\xa0beta heading", "#\x0bbeta heading",
                 "-\xa0beta item", "*\xa0beta item", "+\x0bbeta item", "-\xa0-\xa0-", "_\xa0_\xa0_", "=\xa0",
                 "==\xa0", "=\x0b", "1.\xa0Beta holds.", "01)\x0bBeta holds.", "<div\xa0data=x>", "</div\x0b>",
                 "--x|--y", "-x | :-y", "--force is the flag.", ":|-"):
        for text in (line + "\n", "See the start of the PR\n" + line + "\n"):
            try:
                rule_units(head + text, "probe")
                failures.append("rule_units: the block line {!r} was not refused in {!r}".format(line, text))
            except GateError as exc:
                if "markup" not in str(exc):
                    failures.append("rule_units: the block line {!r} in {!r} was refused for another reason: "
                                    "{}".format(line, text, exc))
    for line in ("2) when unsure.", "7. beta item", "<path>", "</span>", '<a href="x">', "[foo]: /url",
                 '<custom data=">">', '<span title="a>b">', "<a href='x>y'>", '<span title="a<b">', "<a>\xa0",
                 "<b>bold</b>", "[foo\nbar]: /url", "[foo\\]\nbar]: /url", "[foo\nbar\nbaz]: /url",
                 "2.\xa0Beta holds.", "2)\x0bBeta holds.", "\u0661. Alpha holds.", "\u0967\u0968) Alpha holds."):
        for text in (line + "\n", line + "\nAlpha holds.\n"):
            try:
                rule_units(head + text, "probe")
                failures.append("rule_units: {!r} as a paragraph's first line was not refused".format(text))
            except GateError as exc:
                if "markup" not in str(exc):
                    failures.append("rule_units: {!r} as a paragraph's first line was refused for another "
                                    "reason: {}".format(text, exc))
    # A carriage return and a form feed are line endings to the vendored renderer (marko's _preprocess_text) that
    # the LF-only splitter does not see: a cannot-evaluate wherever one stands, inside a line, at a line's start,
    # at its end, in the title and in the frontmatter, so a heading, list item or comment cannot hide behind one.
    breaks = []
    for char, needle in (("\r", "carriage return"), ("\x0c", "form feed")):
        for body in ("Alpha holds.{}# Beta heading\n", "Alpha holds.{}\n", "Alpha holds.{}- push to main.\n",
                     "Beta waits.{}<!-- hidden -->\n", "Alpha holds.\n1.{}Beta holds.\n", "2.{}Beta holds.\n",
                     "Alpha holds.\n<div{}data=x>\n", "Alpha holds\n={}\n", "{}Beta waits.\n",
                     "{}# Beta heading\n", "{}- beta item\n", "{}> beta quote\n"):
            breaks.append((head + body.format(char), needle))
        breaks.append(("---\ncorpus-id: cprobe{}\n---\n\n# Title words\n\nAlpha holds.\n".format(char), needle))
        breaks.append(("---\ncorpus-id: cprobe\n---\n\n# Title{}words\n\nAlpha holds.\n".format(char), needle))
    breaks.append((head + "Alpha holds.\r\n", "carriage return"))
    for text, needle in breaks:
        try:
            rule_units(text, "probe")
            failures.append("rule_units: the {} in {!r} was not refused".format(needle, text))
        except GateError as exc:
            if needle not in str(exc):
                failures.append("rule_units: {!r} was refused for another reason: {}".format(text, exc))
    # No other character ends a line for the vendored renderer or for CommonMark: a vertical tab, U+0085, U+2028
    # and U+2029 stay inside their line, which the renderer reads as one paragraph.
    for char in ("\x0b", "\x85", "\u2028", "\u2029", "\x1c"):
        try:
            rule_units(head + "Alpha holds.{}Beta waits.\n".format(char), "probe")
        except GateError as exc:
            failures.append("rule_units: {!r} inside a line was refused: {}".format(char, exc))
    # An inline HTML comment, processing instruction, declaration or CDATA section passes through as raw HTML a
    # browser does not show, so '<!' or '<?' anywhere in a body line is refused, on a first or a wrapped line.
    for line in ("Alpha holds <!-- hidden words --> today.", "Alpha holds <!--", "Alpha holds <?php hidden ?> today.",
                 "Alpha holds <![CDATA[hidden]]> today.", "Alpha holds <!X hidden> today.", "Use `<!--` here."):
        for text in (line + "\n", "See the start of the PR\n" + line + "\nhidden words --> today.\n"):
            try:
                rule_units(head + text, "probe")
                failures.append("rule_units: the inline '<!' or '<?' in {!r} was not refused".format(text))
            except GateError as exc:
                if "does not show" not in str(exc):
                    failures.append("rule_units: {!r} was refused for another reason: {}".format(text, exc))
    # The delimiter-row test is the vendored GFM table's own: every '|'-separated cell begins with '-' or ':-'.
    for line, want in (("--x|--y", True), (":-- | --:", True), ("|--|", True), ("-", True), ("--force is it.", True),
                       ("a | --", False), ("-- | a", False), ("--||--", False), (":|-", False), ("--\\|x", True),
                       ("", False), ("|", False)):
        if _is_table_delimiter_row(line) != want:
            failures.append("_is_table_delimiter_row({!r}) is not {}".format(line, want))
    # A unit needs a letter or a decimal digit; an underscore (which the regex class \w admits), punctuation
    # or a symbol alone is none.
    for text, want in (("_", False), ("__ -- ...", False), ("\u00b2", False), ("a", True), ("7", True),
                       ("\u00e9", True), ("\u0663", True)):
        if _has_letter_or_digit(text) != want:
            failures.append("_has_letter_or_digit({!r}) is not {}".format(text, want))

    # --- end-to-end fixture cases (a synthetic corpus per fail mode in a private tempdir) ----------
    import copy
    import os
    import shutil
    import tempfile

    try:
        base_tmp = Path((_mkdtemp or tempfile.mkdtemp)(prefix="aiqt-clauses-selftest-"))
    except OSError as exc:
        base_tmp = None
        _not_run(failures, _E2E_CASES, "no writable temp directory ({})".format(exc))

    if base_tmp is not None:
        n = [0]

        def _fresh(rules, clause_rows, born, tombstones=(), successors=()):
            n[0] += 1
            base = _write_corpus(base_tmp / "case{}".format(n[0]), rules, clause_rows,
                                 list(born), list(tombstones), list(successors))
            return base

        def _model_c_case(corpus, obligations, clause_text, start_off, end_off, paragraphs=True):
            """Build a one-corpus, one-clause genesis corpus whose single row spans the obligation lines
            [start_off, end_off] (0-based indices into obligations) with canonical-text clause_text, then
            return the per-row leg's verdict (_row_leg_quiet). Exercises the Model C window/substring
            resolution for a sub-line or multi-line obligation (paragraphs=False for adjacent lines)."""
            lines, line_of = _rule_lines(corpus, obligations, paragraphs)
            digest = _sha_of(lines)
            clause_id = "{}.1".format(corpus)
            row = {"clause-id": clause_id, "corpus-id": corpus,
                   "source-path": ".aiqt/core/rules/{}.md".format(corpus),
                   "start-line": line_of[start_off], "end-line": line_of[end_off],
                   "canonical-text": clause_text, "source-digest": digest}
            born = [(corpus, "1.1.0"), (clause_id, "1.1.0")]
            base = _fresh({corpus: lines}, [row], born)
            return _row_leg_quiet(base)

        try:
            # (0) the passing genesis corpus, with --genesis, is clean (exit 0).
            rules, rows, born = _good_genesis()
            base = _fresh(rules, copy.deepcopy(rows), born)
            if _run_quiet(**_paths(base, genesis=True)) != 0:
                failures.append("genesis: a well-formed genesis corpus expected exit 0")

            # The same corpus with the DEFERRED manifest leg armed and a matching manifest is clean; a
            # mismatching manifest fails; the default invocation reads no manifest at all.
            digest_alpha = _sha_of(rules["calpha"])
            digest_beta = _sha_of(rules["cbeta1"])
            bytes_alpha = (base / ".aiqt" / "core" / "rules" / "calpha.md").stat().st_size
            bytes_beta = (base / ".aiqt" / "core" / "rules" / "cbeta1.md").stat().st_size
            manifest = base / ".aiqt" / "manifest.toml"
            manifest.write_text(
                '[[sources]]\npath = ".aiqt/core/rules/calpha.md"\nbytes = {}\nsha256 = "{}"\n\n'
                '[[sources]]\npath = ".aiqt/core/rules/cbeta1.md"\nbytes = {}\nsha256 = "{}"\n'.format(
                    bytes_alpha, digest_alpha, bytes_beta, digest_beta), encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True, manifest_path=manifest)) != 0:
                failures.append("manifest leg: a matching manifest expected exit 0 when armed")
            bad_manifest = base / ".aiqt" / "manifest-bad.toml"
            bad_manifest.write_text(
                '[[sources]]\npath = ".aiqt/core/rules/calpha.md"\nbytes = {}\nsha256 = "{}"\n\n'
                '[[sources]]\npath = ".aiqt/core/rules/cbeta1.md"\nbytes = {}\nsha256 = "{}"\n'.format(
                    bytes_alpha, "0" * 64, bytes_beta, digest_beta), encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True, manifest_path=bad_manifest)) != 1:
                failures.append("manifest leg: a mismatching manifest expected exit 1 when armed")
            # The hardened loader (D10) fails closed on a duplicate path and on a row whose keys are not
            # exactly path/bytes/sha256.
            dup_manifest = base / ".aiqt" / "manifest-dup.toml"
            dup_manifest.write_text(
                '[[sources]]\npath = ".aiqt/core/rules/calpha.md"\nbytes = {}\nsha256 = "{}"\n\n'
                '[[sources]]\npath = ".aiqt/core/rules/calpha.md"\nbytes = {}\nsha256 = "{}"\n'.format(
                    bytes_alpha, digest_alpha, bytes_alpha, digest_alpha), encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True, manifest_path=dup_manifest)) != 2:
                failures.append("manifest leg: a duplicate sources path expected fail-closed exit 2")
            extra_manifest = base / ".aiqt" / "manifest-extra.toml"
            extra_manifest.write_text(
                '[[sources]]\npath = ".aiqt/core/rules/calpha.md"\nbytes = {}\nsha256 = "{}"\nextra = 1\n'
                .format(bytes_alpha, digest_alpha), encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True, manifest_path=extra_manifest)) != 2:
                failures.append("manifest leg: an extra-key sources row expected fail-closed exit 2")

            # (1) padded ordinal: a clause-id that does not parse is malformed input -> fail-closed exit 2
            # (not a valid-but-wrong ordinal, which stays an exit-1 finding).
            rules, rows, born = _good_genesis()
            rows[0]["clause-id"] = "calpha.01"
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("padded ordinal (malformed clause-id): expected fail-closed exit 2")

            # (2) duplicate clause-id -> scheme FAIL.
            rules, rows, born = _good_genesis()
            rows[1] = copy.deepcopy(rows[0])  # a second row reusing calpha.1
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("duplicate id: expected exit 1")

            # (3) corpus-id with no clause row in the inventory -> scheme FAIL.
            rules, rows, born = _good_genesis()
            rows = [r for r in rows if r["corpus-id"] != "cbeta1"]  # drop cbeta1's rows entirely
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("corpus-id missing from inventory: expected exit 1")

            # (4) span out of range -> per-row FAIL, and the output must carry the span diagnostic
            # itself: the invalid span also leaves its text uncovered, so the text-coverage leg exits 1
            # on this fixture too and an exit-only assertion would stay green with the span diagnostic
            # suppressed.
            rules, rows, born = _good_genesis()
            rows[0]["end-line"] = 9999
            base = _fresh(rules, rows, born)
            code, out = _run_capture(**_paths(base, genesis=True))
            if code != 1 or "does not resolve inside" not in out:
                failures.append("span out of range: expected exit 1 with the span diagnostic")

            # (4b) an invalid-span row BESIDE a valid covering row: every unit is covered, so no
            # coverage finding can stand in for the span diagnostic; only the per-row span check can
            # refuse this corpus, and it must (exit 1 with the span diagnostic). This is the case where
            # a suppressed span diagnostic would wave an invalid redundant row through.
            rules, rows, born = _good_genesis()
            extra = copy.deepcopy(rows[0])
            extra["clause-id"] = "calpha.3"
            extra["end-line"] = 9999
            rows.append(extra)
            born.append(("calpha.3", "1.1.0"))
            base = _fresh(rules, rows, born)
            code, out = _run_capture(**_paths(base, genesis=True))
            if code != 1 or "does not resolve inside" not in out:
                failures.append("invalid-span row beside a valid covering row: expected exit 1 with "
                                "the span diagnostic")

            # (5) text mismatch -> per-row FAIL.
            rules, rows, born = _good_genesis()
            rows[0]["canonical-text"] = "not the span content"
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("text mismatch: expected exit 1")

            # (6) digest mismatch -> per-row FAIL.
            rules, rows, born = _good_genesis()
            rows[0]["source-digest"] = "0" * 64
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("digest mismatch: expected exit 1")

            # (7) missing born row -> completeness FAIL.
            rules, rows, born = _good_genesis()
            born = [b for b in born if b[0] != rows[0]["clause-id"]]  # drop one clause's born row
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("missing born row: expected exit 1")

            # (8) resurrected tombstoned id -> resurrection FAIL (a non-genesis register with a tombstone).
            rules, rows, born = _good_genesis()
            dead = rows[0]["clause-id"]                       # this id is live in the inventory ...
            tomb = [(dead, "1.4.0")]                          # ... yet tombstoned in the register
            base = _fresh(rules, rows, born, tombstones=tomb)
            if _run_quiet(**_paths(base)) != 1:               # not --genesis: tombstones are allowed
                failures.append("resurrected tombstoned id: expected exit 1")

            # (9) cumulative-max: a new ordinal not exceeding the historical max -> FAIL. Register spans two
            # releases: calpha.1 and calpha.10 born at 1.1.0, calpha.2 born at 1.2.0 (2 <= 10 -> finding).
            lines, line_of = _rule_lines("calpha", ["ob one", "ob two", "ob ten"])
            digest = _sha_of(lines)
            sp = ".aiqt/core/rules/calpha.md"
            rows = [
                {"clause-id": "calpha.1", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[0], "end-line": line_of[0], "canonical-text": "ob one",
                 "source-digest": digest},
                {"clause-id": "calpha.10", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[2], "end-line": line_of[2], "canonical-text": "ob ten",
                 "source-digest": digest},
                {"clause-id": "calpha.2", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[1], "end-line": line_of[1], "canonical-text": "ob two",
                 "source-digest": digest},
            ]
            born = [("calpha", "1.1.0"), ("calpha.1", "1.1.0"), ("calpha.10", "1.1.0"), ("calpha.2", "1.2.0")]
            base = _fresh({"calpha": lines}, rows, born)
            if _run_quiet(**_paths(base)) != 1:
                failures.append("ordinal not exceeding cumulative max: expected exit 1")

            # (10) lexicographic-trap PASS: cumulative max 9, a new ordinal 10 -> clean (numeric compare).
            lines, line_of = _rule_lines("calpha", ["ob nine", "ob ten"])
            digest = _sha_of(lines)
            rows = [
                {"clause-id": "calpha.9", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[0], "end-line": line_of[0], "canonical-text": "ob nine",
                 "source-digest": digest},
                {"clause-id": "calpha.10", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[1], "end-line": line_of[1], "canonical-text": "ob ten",
                 "source-digest": digest},
            ]
            born = [("calpha", "1.1.0"), ("calpha.9", "1.1.0"), ("calpha.10", "1.2.0")]
            base = _fresh({"calpha": lines}, rows, born)
            if _run_quiet(**_paths(base)) != 0:
                failures.append("lexicographic trap: a new ordinal 10 over prior 9 expected exit 0")

            # (11) unexplained disappearance -> cross-release completeness FAIL. The predecessor inventory
            # carries an id (cbeta1.2) absent from the current inventory with no retirement row.
            rules, rows, born = _good_genesis()
            current = _fresh(rules, rows, born)
            prev = current / ".aiqt" / "core" / "prev-clauses.toml"
            prev_rows = copy.deepcopy(rows) + [{"clause-id": "cbeta1.9", "corpus-id": "cbeta1",
                                                "source-path": ".aiqt/core/rules/cbeta1.md",
                                                "start-line": 8, "end-line": 8,
                                                "canonical-text": "gone", "source-digest": "0" * 64}]
            prev.write_text("".join(_toml_clause(r) + "\n" for r in prev_rows), encoding="utf-8")
            if _run_quiet(**_paths(current, prev_inventory_path=prev)) != 1:
                failures.append("unexplained disappearance: expected exit 1")

            # (12) fail-closed: an unreadable/absent register is exit 2, never a clean empty history.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born)
            (base / ".aiqt" / "core" / "id-history.toml").unlink()
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("absent register: expected fail-closed exit 2")

            # (13) fail-closed: --genesis with a register that carries a tombstone is exit 2.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born, tombstones=[("calpha.1", "1.4.0")])
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("genesis with a tombstone: expected fail-closed exit 2")

            # (14) Model C sub-line obligation: canonical-text is a byte-exact substring of a wider source
            # line, in a tight 1-line window -> PASS.
            if _model_c_case("csubln", ["PREAMBLE. Obligation text here. TRAILER."],
                             "Obligation text here.", 0, 0) != 0:
                failures.append("Model C sub-line obligation: expected exit 0")

            # (15) Model C multi-line obligation: canonical-text carries an embedded line-wrap LF and spans
            # two source lines, in a tight 2-line window -> PASS (verifies the LF round-trips through TOML).
            if _model_c_case("cmulti", ["first half of the obligation", "second half of the obligation"],
                             "first half of the obligation\nsecond half of the obligation", 0, 1,
                             paragraphs=False) != 0:
                failures.append("Model C multi-line embedded-LF obligation: expected exit 0")

            # (16) Model C canonical-text absent from its window -> FAIL.
            if _model_c_case("cabsnt", ["the source line does not carry it"], "absent text", 0, 0) != 1:
                failures.append("Model C canonical-text absent from window: expected exit 1")

            # (17) Model C canonical-text occurring twice within the window (overlapping: 'aa' in 'aaa',
            # which str.count would miscount as one) -> FAIL on uniqueness.
            if _model_c_case("cdupdp", ["aaa"], "aa", 0, 0) != 1:
                failures.append("Model C canonical-text occurring twice in window: expected exit 1")

            # (18) Model C window too wide: the occurrence is present but does not begin on start-line (a
            # whole noise sentence precedes it inside the oversized 3-line window) -> FAIL on tightness.
            # Every line is a whole sentence, so the text's edges are clean sentence boundaries and only the
            # tightness check can refuse it; the same text in its tight 1-line window passes.
            wide = ["A noise sentence comes before.", "The obligation holds.", "A noise sentence follows."]
            if _model_c_case("cwidet", wide, "The obligation holds.", 1, 1, paragraphs=False) != 0:
                failures.append("Model C tight window over whole sentences: expected exit 0")
            if _model_c_case("cwidew", wide, "The obligation holds.", 0, 2, paragraphs=False) != 1:
                failures.append("Model C window not tight: expected exit 1")

            # (19) Model C empty canonical-text: an empty string trivially substring-matches, so the gate
            # rejects it rather than passing -> FAIL.
            if _model_c_case("cempty", ["a real obligation line"], "", 0, 0) != 1:
                failures.append("Model C empty canonical-text: expected exit 1")

            # (20) cumulative-max unrelated-tombstone bypass now FAILS. A later-dated UNRELATED tombstone
            # (coldid.1 retired at 2.0.0) must not lift a global "newest" that disables the check: calpha.2
            # born at 1.5.0 is still below the historical max 10 (calpha.10 born at 1.0.0) -> exit 1.
            lines, line_of = _rule_lines("calpha", ["ob two", "ob ten"])
            digest = _sha_of(lines)
            sp = ".aiqt/core/rules/calpha.md"
            rows = [
                {"clause-id": "calpha.2", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[0], "end-line": line_of[0], "canonical-text": "ob two",
                 "source-digest": digest},
                {"clause-id": "calpha.10", "corpus-id": "calpha", "source-path": sp,
                 "start-line": line_of[1], "end-line": line_of[1], "canonical-text": "ob ten",
                 "source-digest": digest},
            ]
            born = [("calpha", "1.0.0"), ("calpha.10", "1.0.0"), ("calpha.2", "1.5.0"),
                    ("coldid", "1.0.0"), ("coldid.1", "1.0.0")]
            base = _fresh({"calpha": lines}, rows, born, tombstones=[("coldid.1", "2.0.0")])
            if _run_quiet(**_paths(base)) != 1:
                failures.append("cumulative-max unrelated-tombstone bypass: expected exit 1")

            # (21) tightness: a TRAILING newline in canonical-text no longer falsely satisfies end-on-end
            # (before the fix "actual obligation\n" spanning lines 8-9 passed) -> exit 1.
            if _model_c_case("ctailn", ["actual obligation", "noise"],
                             "actual obligation\n", 0, 1, paragraphs=False) != 1:
                failures.append("tightness trailing-newline canonical-text: expected exit 1")

            # (22) tightness: a LEADING newline in canonical-text no longer falsely satisfies begin-on-start
            # -> exit 1.
            if _model_c_case("cleadn", ["noise", "actual obligation"],
                             "\nactual obligation", 0, 1, paragraphs=False) != 1:
                failures.append("tightness leading-newline canonical-text: expected exit 1")

            # (23) source-path outside the rules dir now FAILS. The row points at a copy under .aiqt/ (not
            # under .aiqt/core/rules/) with matching digest and frontmatter, so only the containment /
            # identity check can catch it -> exit 1 (before the fix this passed).
            lines, line_of = _rule_lines("coutsd", ["the only obligation"])
            digest = _sha_of(lines)
            row = {"clause-id": "coutsd.1", "corpus-id": "coutsd", "source-path": ".aiqt/outside.md",
                   "start-line": line_of[0], "end-line": line_of[0],
                   "canonical-text": "the only obligation", "source-digest": digest}
            base = _fresh({"coutsd": lines}, [row], [("coutsd", "1.1.0"), ("coutsd.1", "1.1.0")])
            (base / ".aiqt" / "outside.md").write_text(_canon(lines), encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("source-path outside rules dir: expected exit 1")

            # (24) inventory clause-id of the wrong type (a raw non-string) is malformed input -> exit 2.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born)
            (base / ".aiqt" / "core" / "clauses.toml").write_text(
                '[[clause]]\nclause-id = 7\ncorpus-id = "calpha"\n'
                'source-path = ".aiqt/core/rules/calpha.md"\nstart-line = 8\nend-line = 8\n'
                'canonical-text = "x"\nsource-digest = "x"\n', encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("non-string clause-id: expected fail-closed exit 2")

            # (25) inventory corpus-id failing CID_RE is malformed input -> exit 2.
            rules, rows, born = _good_genesis()
            rows[0]["corpus-id"] = "ab"
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("corpus-id failing CID_RE: expected fail-closed exit 2")

            # (26) inventory corpus-id well-formed but not matching the clause-id's corpus part stays a
            # genuine content finding -> exit 1 (not fail-closed).
            rules, rows, born = _good_genesis()
            rows[0]["corpus-id"] = "cbeta1"
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("mismatched (well-formed) corpus-id: expected exit 1")

            # (27) register born id that is neither a well-formed corpus-id nor clause-id -> exit 2.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born + [("bad!!!", "1.1.0")])
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("malformed born id: expected fail-closed exit 2")

            # (28) register successor-id that is malformed -> exit 2.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born, successors=[("calpha.1", "1.4.0", "!!!")])
            if _run_quiet(**_paths(base)) != 2:
                failures.append("malformed successor-id: expected fail-closed exit 2")

            # (29) register successor-id equal to its own id (self-loop) -> exit 2.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born, successors=[("calpha.1", "1.4.0", "calpha.1")])
            if _run_quiet(**_paths(base)) != 2:
                failures.append("self-loop successor-id: expected fail-closed exit 2")

            # (30) register successor-id with no born row (dangling) -> exit 2.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born, successors=[("calpha.1", "1.4.0", "cbeta1.9")])
            if _run_quiet(**_paths(base)) != 2:
                failures.append("dangling successor-id: expected fail-closed exit 2")

            # (31) boolean line numbers (bool is an int subclass) are the wrong type -> fail-closed exit 2.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born)
            digest = _sha_of(rules["calpha"])
            (base / ".aiqt" / "core" / "clauses.toml").write_text(
                '[[clause]]\nclause-id = "calpha.1"\ncorpus-id = "calpha"\n'
                'source-path = ".aiqt/core/rules/calpha.md"\nstart-line = true\nend-line = true\n'
                'canonical-text = "x"\nsource-digest = "{}"\n'.format(digest), encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("boolean line numbers: expected fail-closed exit 2")

            # (32) a SUCCESSOR row loads and a successor-retired id that is still live is caught as a
            # resurrection -> exit 1 (calpha.1 retired via a successor to calpha.2, yet live in inventory).
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born, successors=[("calpha.1", "1.4.0", "calpha.2")])
            if _run_quiet(**_paths(base)) != 1:
                failures.append("successor-retired id resurrection: expected exit 1")

            # (33) explained disappearance PASSES: an id in the predecessor and absent now, WITH a
            # retirement row, clears the cross-release leg -> exit 0. A whole corpus (cgamma + cgamma.1) is
            # retired, so both its ids carry a tombstone.
            rules, rows, born = _good_genesis()
            born = born + [("cgamma", "1.0.0"), ("cgamma.1", "1.0.0")]
            tombs = [("cgamma", "1.4.0"), ("cgamma.1", "1.4.0")]
            current = _fresh(rules, rows, born, tombstones=tombs)
            prev = current / ".aiqt" / "core" / "prev-clauses.toml"
            prev_rows = copy.deepcopy(rows) + [{"clause-id": "cgamma.1", "corpus-id": "cgamma",
                                                "source-path": ".aiqt/core/rules/cgamma.md",
                                                "start-line": 8, "end-line": 8,
                                                "canonical-text": "gone", "source-digest": "0" * 64}]
            prev.write_text("".join(_toml_clause(r) + "\n" for r in prev_rows), encoding="utf-8")
            if _run_quiet(**_paths(current, prev_inventory_path=prev)) != 0:
                failures.append("explained disappearance: expected exit 0")

            # (34) whitespace-only canonical-text is rejected (never a trivial substring match) -> exit 1.
            if _model_c_case("cwsonl", ["a real obligation line"], "   ", 0, 0) != 1:
                failures.append("Model C whitespace-only canonical-text: expected exit 1")

            # (35) legitimate shared-window rows PASS: two distinct clause-ids with identical canonical-text
            # in the same one-line window are clean (uniqueness is within each row's own window) -> exit 0.
            lines, line_of = _rule_lines("cshare", ["the shared obligation sentence"])
            digest = _sha_of(lines)
            sp = ".aiqt/core/rules/cshare.md"
            ln = line_of[0]
            shared = [
                {"clause-id": "cshare.1", "corpus-id": "cshare", "source-path": sp,
                 "start-line": ln, "end-line": ln, "canonical-text": "the shared obligation sentence",
                 "source-digest": digest},
                {"clause-id": "cshare.2", "corpus-id": "cshare", "source-path": sp,
                 "start-line": ln, "end-line": ln, "canonical-text": "the shared obligation sentence",
                 "source-digest": digest},
            ]
            born = [("cshare", "1.1.0"), ("cshare.1", "1.1.0"), ("cshare.2", "1.1.0")]
            base = _fresh({"cshare": lines}, shared, born)
            if _run_quiet(**_paths(base, genesis=True)) != 0:
                failures.append("legitimate shared-window rows: expected exit 0")

            # (50) WHOLE-CLAUSE EDGES: a canonical-text cut short inside a clause FAILS. Before the fix the
            # window was checked by line, not by character, so each of these passed (exit 0). The first is
            # the reported vector: only the final '.' removed, the cut inside the last line of the window.
            stop = "Every pause is continuation, not a stop."
            if _model_c_case("ctrnc1", [stop + " A later sentence follows."], stop, 0, 0) != 0:
                failures.append("whole-clause edges, the whole sentence: expected exit 0")
            if _model_c_case("ctrnc2", [stop + " A later sentence follows."], stop[:-1], 0, 0) != 1:
                failures.append("whole-clause edges, final '.' dropped inside the line: expected exit 1")
            if _model_c_case("ctrnc3", [stop], stop[:-1], 0, 0) != 1:
                failures.append("whole-clause edges, final '.' dropped at the window end: expected exit 1")
            if _model_c_case("ctrnc4", [stop], "Every pause is continuation, not a", 0, 0) != 1:
                failures.append("whole-clause edges, last word dropped: expected exit 1")
            if _model_c_case("ctrnc5", [stop], "Every pause is continuation", 0, 0) != 1:
                failures.append("whole-clause edges, cut at a comma with no conjunction: expected exit 1")
            if _model_c_case("ctrnc6", ["A first sentence ends. " + stop], stop[1:], 0, 0) != 1:
                failures.append("whole-clause edges, first character dropped: expected exit 1")
            # A dropped wrapped line with end-line moved up with it: the window is whole lines and tight,
            # but the sentence continues on the next source line -> FAIL.
            if _model_c_case("ctrnc7", ["Every pause is continuation and", "never a stop."],
                             "Every pause is continuation and", 0, 0, paragraphs=False) != 1:
                failures.append("whole-clause edges, wrapped last line dropped: expected exit 1")

            # (51) WHOLE-CLAUSE EDGES: the legitimate clause splits PASS (exit 0): a split before ';' and after
            # it (the clause after it keeping its own "and", the kltwat.3 shape), a second such split, a
            # sentence after a sentence end on the same line, and two clause-ids that carry one identical
            # whole sentence joined by ", and" (the tmrrst.4 / tmrrst.5 shape: a comma is never a boundary,
            # so two obligations in one ", and" sentence share its whole text).
            lines, line_of = _rule_lines("csplit", [
                "Alpha holds the lease; and beta waits for it; gamma logs the wait.",
                "Two steps hold; delta tests the build. Epsilon ships it.",
                "Zeta keeps the lock, and eta logs it."])
            digest = _sha_of(lines)
            sp = ".aiqt/core/rules/csplit.md"
            split_rows = []
            for ordinal, (off, text) in enumerate(((0, "Alpha holds the lease"), (0, "and beta waits for it"),
                                                   (0, "gamma logs the wait."), (1, "Two steps hold"),
                                                   (1, "delta tests the build."), (1, "Epsilon ships it."),
                                                   (2, "Zeta keeps the lock, and eta logs it."),
                                                   (2, "Zeta keeps the lock, and eta logs it.")), 1):
                split_rows.append({"clause-id": "csplit.{}".format(ordinal), "corpus-id": "csplit",
                                   "source-path": sp, "start-line": line_of[off], "end-line": line_of[off],
                                   "canonical-text": text, "source-digest": digest})
            born = [("csplit", "1.1.0")] + [("csplit.{}".format(k), "1.1.0") for k in range(1, 9)]
            base = _fresh({"csplit": lines}, split_rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 0:
                failures.append("whole-clause edges, legitimate clause splits: expected exit 0")

            # (52) WHOLE-CLAUSE EDGES at a line start: the start-line edge is judged against the previous
            # line, so a row that begins at the start of its line PASSES only when the sentence before it
            # has ended. The FAIL case is the shape a whole-line overwrite leaves: the tail of the earlier
            # sentence ("figures.") sat on the row's start-line and was overwritten with it, so the earlier
            # sentence now runs straight into the row -> exit 1.
            pinned = "A count is pinned to its predicate."
            if _model_c_case("cline1", ["The earlier sentence yields two", "figures. " + pinned],
                             pinned, 1, 1, paragraphs=False) != 0:
                failures.append("whole-clause edges, a row after a sentence end on its own line: expected "
                                "exit 0")
            if _model_c_case("cline2", ["The earlier sentence yields two", pinned],
                             pinned, 1, 1, paragraphs=False) != 1:
                failures.append("whole-clause edges, a row at a line start after an unended sentence: "
                                "expected exit 1")

            # (53) WHOLE-CLAUSE EDGES: one vector per round-1 reproduction and per sub-predicate of the
            # boundary grammar and the pairing check. Each row is (corpus, obligation lines, canonical-text,
            # first and last obligation index, paragraphs, expected exit, label); removing or loosening any
            # sub-predicate makes at least one row here, or in (50) to (52), disagree.
            acc = "Keep accuracy, integrity, and trust."
            phrase = 'The phrase "Stop. Go." is printed.'
            gain = ["A gain in progress, speed, or cost never justifies any loss on the AIQT", "tier."]
            dirs = "Never delete files, directories, or branches."
            count = "Record the count, the unit, and the predicate."
            v15 = "Version v1.5 holds."
            curly = "The phrase \u201cStop. Go.\u201d is printed."
            quoted = '"Alpha must retain records." Beta waits.'
            bracketed = "(Alpha must retain records.) Beta waits."
            lead3 = "Three things are never deleted: files, directories, and branches."
            never = "Never do the following: delete a branch."
            trig = ["A wind-down, or a turn handed back to the human, happens only on a named, "
                    "externally-observable",
                    "trigger: the task is complete; a human has explicitly stopped, paused, or changed the mode."]
            why = 'Ask "why?" before you delete anything.'
            edge_vectors = [
                # round 1 (review A, finding 1): a cut inside a list or a quotation fails
                ("wcx001", [acc], "trust.", 0, 0, True, 1, "list tail after ', and'"),
                ("wcx002", [acc], "Keep accuracy, integrity", 0, 0, True, 1, "list head before ', and'"),
                ("wcx003", [phrase], 'Go." is printed.', 0, 0, True, 1, "a cut inside a quotation"),
                # round 1 (review A, finding 2): whole quoted and bracketed sentences pass
                ("wcx004", [quoted], '"Alpha must retain records."', 0, 0, True, 0, "a whole quoted sentence"),
                ("wcx005", [quoted], "Beta waits.", 0, 0, True, 0, "a sentence after a quoted one"),
                ("wcx006", [bracketed], "(Alpha must retain records.)", 0, 0, True, 0,
                 "a whole bracketed sentence"),
                ("wcx007", [bracketed], "Beta waits.", 0, 0, True, 0, "a sentence after a bracketed one"),
                # round 1 (review B, finding 1): a cut at ', or' / ', and' inside a list fails (prjint1.1)
                ("wcl001", gain, "speed, or cost never justifies any loss on the AIQT\ntier.", 0, 1, False, 1,
                 "a start inside a list"),
                ("wcl002", gain, "or cost never justifies any loss on the AIQT\ntier.", 0, 1, False, 1,
                 "a start at ', or' inside a list"),
                ("wcl003", gain, "A gain in progress, speed", 0, 0, False, 1, "the subject alone"),
                ("wcl004", gain, "\n".join(gain), 0, 1, False, 0, "the whole wrapped sentence"),
                ("wcl005", [dirs], "Never delete files, directories", 0, 0, True, 1, "an end before ', or'"),
                ("wcl006", [dirs], "or branches.", 0, 0, True, 1, "a start at ', or'"),
                ("wcl007", [count], "Record the count, the unit", 0, 0, True, 1, "an end before ', and'"),
                # round 1 (review B, finding 3): mutants M2 to M5 each meet a FAIL vector
                ("wcm002", [stop], "not a stop.", 0, 0, True, 1, "a start after a plain comma"),
                ("wcm003", ["Alpha holds the lease, then beta waits."], "beta waits.", 0, 0, True, 1,
                 "a start after ', <word> '"),
                ("wcm004", [v15], "5 holds.", 0, 0, True, 1, "a start after '.' with no space"),
                ("wcm005", [v15], "Version v1.", 0, 0, True, 1, "an end on '.' with no following space"),
                # round 2 (review A, finding 1): a colon is never a boundary, so a cut at a colon fails; the
                # round-1 colon-split and mid-sentence quote-end vectors are flipped to FAIL (disclosed
                # over-fires where the text is a whole obligation)
                ("wcn001", ["Rule: never push to main."], "never push to main.", 0, 0, True, 1,
                 "a start after ': '"),
                ("wcn002", ["Never do these:", "- push to main"], "Never do these:", 0, 0, False, 1,
                 "a lead-in that ends on ':'"),
                ("wcn003", ['Say "stop." Then wait.'], 'Say "stop."', 0, 0, True, 1,
                 "an end on a quote opened mid-sentence"),
                ("wcn004", ["Stop (always.) Then wait."], "Stop (always.)", 0, 0, True, 1,
                 "an end on a ')' opened mid-sentence"),
                ("wcn005", [lead3], "files, directories, and branches.", 0, 0, True, 1, "a list after ': '"),
                ("wcn006", [lead3], "Three things are never deleted:", 0, 0, True, 1, "a lead-in ending on ':'"),
                ("wcn007", [never], "delete a branch.", 0, 0, True, 1, "a reversed obligation after ': '"),
                ("wcn008", [never], "Never do the following", 0, 0, True, 1, "a lead-in before ': '"),
                ("wcn009", trig, trig[0] + "\ntrigger", 0, 1, False, 1, "the cntdef.3 lead-in without its list"),
                ("wcn010", trig, "the task is complete;", 1, 1, False, 1, "the cntdef.3 first trigger alone"),
                # round 2 (review B, finding 1, and review A, finding 2): enclosure depth is judged from the
                # source paragraph, so a fragment from inside a quotation or bracket fails, and a sentence end
                # inside one that opened mid-sentence is no boundary
                ("wen001", ['The phrase "Stop. Go. Wait." is printed.'], "Go.", 0, 0, True, 1,
                 "a sentence inside a quotation"),
                ("wen002", ["Keep logs (Stop. Go. Wait.) for audit."], "Go.", 0, 0, True, 1,
                 "a sentence inside a bracket"),
                ("wen003", ['Print "Stop. Go." then "Wait. Stay." now.'], 'Go." then "Wait.', 0, 0, True, 1,
                 "a cut across two quotations"),
                ("wen004", [phrase], "is printed.", 0, 0, True, 1, "a start after a mid-sentence quotation"),
                ("wen005", [phrase], 'The phrase "Stop. Go."', 0, 0, True, 1,
                 "an end on a mid-sentence quotation"),
                ("wen006", [curly], "is printed.", 0, 0, True, 1, "a start after a mid-sentence curly quotation"),
                ("wen007", ["Keep logs (Stop. Go.) for audit."], "for audit.", 0, 0, True, 1,
                 "a start after a mid-sentence bracket"),
                ("wen008", ["Keep logs (Stop. Go.) for audit."], "Keep logs (Stop. Go.)", 0, 0, True, 1,
                 "an end on a mid-sentence bracket"),
                ("wen009", [why], "before you delete anything.", 0, 0, True, 1, "a start after a quoted '?'"),
                ("wen010", [why], 'Ask "why?"', 0, 0, True, 1, "an end on a quoted '?'"),
                ("wen011", ["Delete the branch (after review!) and tag it."], "and tag it.", 0, 0, True, 1,
                 "a start after a bracketed '!'"),
                ("wen012", ['Print "A. B." and "C. D." now.'], 'B." and "C.', 0, 0, True, 1,
                 "a cut across two straight quotations with an even quote count"),
                ("wen013", ["Keep (alpha; beta) waiting."], "beta) waiting.", 0, 0, True, 1,
                 "a start after a ';' inside a bracket"),
                ("wen014", ['Print the character ")". Then wait.'], 'Print the character ")".', 0, 0, True, 0,
                 "a closing bracket inside a quoted literal"),
                ("wen015", ['Print the character "(". Then wait.'], "Then wait.", 0, 0, True, 0,
                 "an opening bracket inside a quoted literal is text"),
                ("wen016", ["Steps: a) build, b) test."], "Steps: a) build, b) test.", 0, 0, True, 0,
                 "a closer with no opener is text"),
                ("wen017", ['Use a 3" pipe.', "Keep the log."], "Keep the log.", 1, 1, True, 0,
                 "an enclosure is scanned from its own paragraph start"),
                ("wen018", ['"Stop." "Go." Then wait.'], "Then wait.", 0, 0, True, 1,
                 "a start after two whole quoted sentences (a disclosed over-fire since round 4)"),
                ("wen019", ['("Stop.") Then wait.'], "Then wait.", 0, 0, True, 0,
                 "a start after nested enclosures, judged at the outermost opener"),
                # the boundary sub-predicates
                ("wsp001", ["Is it pinned? Then record it! Then stop."], "Then record it!", 0, 0, True, 0,
                 "a start after '?' and an end on '!'"),
                ("wsp002", ["Alpha holds the lease; beta waits."], "Alpha holds the lease;", 0, 0, True, 0,
                 "an end on ';'"),
                ("wsp003", ["Two steps hold: delta tests the build."], "Two steps hold", 0, 0, True, 1,
                 "an end before ': '"),
                ("wsp004", ["Fetch it from https://example.org today."], "Fetch it from https", 0, 0, True, 1,
                 "an end before a ':' with no whitespace after it"),
                ("wsp007", ["Alpha holds the lease ; beta waits."], "Alpha holds the lease", 0, 0, True, 1,
                 "an end with whitespace before a ';' (the ';' is not directly ahead)"),
                ("wsp008", ["Alpha holds the lease ; beta waits."], "Alpha holds the lease ;", 0, 0, True, 0,
                 "an end on a ';' after whitespace"),
                ("wsp009", ["Set x;y to zero."], "Set x", 0, 0, True, 1,
                 "an end before a ';' with no whitespace after it"),
                ("wsp005", ["Keep the log", "Rotate it weekly."], "Keep the log", 0, 0, True, 0,
                 "an end at a paragraph edge with no ending"),
                ("wsp006", ["Keep the log"], "Keep the log", 0, 0, True, 0, "an end at the file edge"),
                # the pairing check, for each quote and bracket kind
                ("wpr001", [curly], "Go.\u201d is printed.", 0, 0, True, 1, "an unopened curly quote"),
                ("wpr002", [curly], "The phrase \u201cStop.", 0, 0, True, 1, "an unclosed curly quote"),
                ("wpr003", ["Keep logs (Stop. Go.) for audit."], "Go.) for audit.", 0, 0, True, 1,
                 "an unopened ')'"),
                ("wpr004", ["Keep logs (Stop. Go.) for audit."], "Keep logs (Stop.", 0, 0, True, 1,
                 "an unclosed '('"),
                ("wpr005", ["Keep logs [Stop. Go.] for audit."], "Go.] for audit.", 0, 0, True, 1,
                 "an unopened ']'"),
                ("wpr006", ["Keep logs {Stop. Go.} for audit."], "Go.} for audit.", 0, 0, True, 1,
                 "an unopened brace"),
                ("wpr007", ["Note (one] holds."], "Note (one] holds.", 0, 0, True, 1, "a mismatched pair"),
                # the disclosed fail-closed over-fires are refused (exit 1), never guessed at
                ("wof001", ["Never do these:", "- push to main.", "- delete no branch."], "push to main.", 1, 1,
                 False, 1, "a bullet item without its marker"),
                ("wof002", ["**Note.** Never push to main."], "Never push to main.", 0, 0, True, 1,
                 "a text after a bold lead-in"),
                ("wof003", ["## Heading", "Never push to main."], "Never push to main.", 1, 1, False, 1,
                 "a row right after a heading line"),
                ("wof004", ["Alpha holds, and beta waits."], "Alpha holds,", 0, 0, True, 1,
                 "a text that ends on a comma"),
                ("wof005", ["Keep values in [0, 1). Then wait."], "Keep values in [0, 1).", 0, 0, True, 1,
                 "a half-open interval leaves its bracket open"),
                ("wof006", ['Use a 3" pipe.'], 'Use a 3" pipe.', 0, 0, True, 1,
                 "an inch mark leaves a quotation open"),
                ("wof007", ["**Never push to main.**"], "Never push to main.", 0, 0, True, 1,
                 "a whole bold sentence without its markup"),
                ("wof008", ['He wrote: "Keep logs. Rotate them."'], "Rotate them.", 0, 0, True, 1,
                 "a whole sentence inside a quotation"),
                ("wof009", ["Say 'stop.' Then wait."], "Then wait.", 0, 0, True, 1,
                 "a start after a single-quoted sentence end"),
                ("wof010", ["Stop.) Then wait."], "Then wait.", 0, 0, True, 1,
                 "a start after a sentence end and a closer that closes nothing"),
                # the disclosed residuals still pass (exit 0); a fix updates both the docstring and these
                ("wrs001", ["Record the city, region; the country; and the date."], "the country;", 0, 0,
                 True, 0,
                 "a ';' between list items reads as a clause separator"),
                ("wrs002", ["'Stop. Go.' is printed."], "Go.' is printed.", 0, 0, True, 0,
                 "a cut inside a single-quoted sentence"),
                # round 3 (review A, finding 1): a quoted or bracketed sentence that a longer sentence goes on
                # from is no sentence end, so a cut at either side of it fails; the next sentence must start
                # with an ASCII capital (since round 4; a digit or an opener no longer counts, see below)
                ("wqs001", ['"Never delete." does not apply to scratch files.'], '"Never delete."', 0, 0, True, 1,
                 "an end on a quoted sentence a longer sentence goes on from"),
                ("wqs002", ['"Done." is printed only after the work is verified.'],
                 "is printed only after the work is verified.", 0, 0, True, 1,
                 "a start after a quoted sentence a longer sentence goes on from"),
                ("wqs003", ['Run the suite. "Done." is printed only after it passes.'],
                 "is printed only after it passes.", 0, 0, True, 1, "a start after a mid-paragraph quoted subject"),
                ("wqs004", ['Run the suite. "Done." is printed only after it passes.'], 'Run the suite. "Done."', 0,
                 0, True, 1, "an end on a mid-paragraph quoted subject"),
                ("wqs005", ["(Optional.) is never a valid label for a mandatory step."],
                 "is never a valid label for a mandatory step.", 0, 0, True, 1,
                 "a start after a bracketed subject"),
                ("wqs006", ["\u201cStop.\u201d is the only word that halts a run."],
                 "is the only word that halts a run.", 0, 0, True, 1, "a start after a curly-quoted subject"),
                ("wqs007", ['Keep logs; "Rotate weekly!" is the team motto.'], "is the team motto.", 0, 0, True, 1,
                 "a start after a quoted subject opened at a ';'"),
                # round 3 (review B, finding 1): a bracket inside a quotation is text to the closer test too
                ("wqs008", ['"Keep logs (always.)" Then wait.'], '"Keep logs (always.)"', 0, 0, True, 0,
                 "a whole quoted sentence that ends inside a bracket"),
                ("wqs009", ['"Keep logs (always.)" Then wait.'], "Then wait.", 0, 0, True, 0,
                 "a start after a quoted sentence that ends inside a bracket"),
                # round 4 (reviews A and B): a whole sentence that starts with a digit or an opener after a
                # quoted one is refused (a disclosed over-fire), since a longer sentence may go on there
                ("wqs010", ['"Stop." 3 files remain.'], "3 files remain.", 0, 0, True, 1,
                 "a next sentence that starts with a digit"),
                ("wqs011", ['"Stop." "Go." Then wait.'], '"Go."', 0, 0, True, 1,
                 "a next sentence that starts with a straight quote"),
                ("wqs012", ['"Stop." \u201cGo.\u201d Then wait.'], "\u201cGo.\u201d", 0, 0, True, 1,
                 "a next sentence that starts with a curly quote"),
                ("wqs013", ['"Stop." (Go.) Then wait.'], "(Go.)", 0, 0, True, 1,
                 "a next sentence that starts with '('"),
                ("wqs014", ['"Stop." [Go.] Then wait.'], "[Go.]", 0, 0, True, 1,
                 "a next sentence that starts with '['"),
                ("wqs015", ['"Stop." {Go.} Then wait.'], "{Go.}", 0, 0, True, 1,
                 "a next sentence that starts with a brace"),
                ("wqs016", ['"Keep logs (always.) Then wait."'], 'Then wait."', 0, 0, True, 1,
                 "a start after a bracket inside a quotation still open"),
                # round 3 (review A, finding 2): an inline code span is an enclosure whose content is text
                ("wcd001", ["Set `a. b` to on before you deploy."], "b` to on before you deploy.", 0, 0, True, 1,
                 "a start after a '.' inside a code span"),
                ("wcd002", ["Use `x ? y : z` in the config."], "y : z` in the config.", 0, 0, True, 1,
                 "a start after a '?' inside a code span"),
                ("wcd003", ["Run `make clean; make` before you push."], "make` before you push.", 0, 0, True, 1,
                 "a start after a ';' inside a code span"),
                ("wcd004", ["Run `make clean; make` before you push."], "Run `make clean;", 0, 0, True, 1,
                 "an end on a ';' inside a code span"),
                ("wcd005", ["Set `a. b` to on before you deploy."], "Set `a. b` to on before you deploy.", 0, 0,
                 True, 0, "a whole sentence holding a code span"),
                ("wcd006", ['Type `"` to quote. Then wait.'], "Then wait.", 0, 0, True, 0,
                 "a quote inside a code span is text"),
                ("wcd007", ["Type `(` to group. Then wait."], "Then wait.", 0, 0, True, 0,
                 "a bracket inside a code span is text"),
                ("wcd008", ["Use ``a ` b`` here. Then wait."], "Then wait.", 0, 0, True, 0,
                 "a code span closes only at a backtick run of its own length"),
                ("wcd009", ['"Use `"` here." Then wait.'], "Then wait.", 0, 0, True, 0,
                 "a code span opens inside a quotation"),
                ("wcd010", ["(Use `)` here.) Then wait."], "Then wait.", 0, 0, True, 0,
                 "a code span opens inside a bracket"),
                # round 3 (review A, findings 3 and 4): the enclosure scan crosses a single line break, and a
                # straight quote opens inside a curly quotation
                ("wen020", ['The phrase "Stop.', 'Go. Wait." is printed.'], "Go.", 1, 1, False, 1,
                 "a sentence inside a quotation that a single line break wraps"),
                ("wen021", ["Keep logs (Stop.", "Go. Wait.) for audit."], "Go.", 1, 1, False, 1,
                 "a sentence inside a bracket that a single line break wraps"),
                ("wen022", ['\u201cHe said "stop. Go.\u201d now. Then wait.'], "now.", 0, 0, True, 1,
                 "a start inside a straight quote opened inside a curly quotation"),
                ("wen023", ['\u201cHe said "stop. Go.\u201d Now. Then wait.'], "Now.", 0, 0, True, 1,
                 "a start inside a straight quote opened inside a curly quotation, capitalised"),
                # round 3 (review A, finding 5, and review B, finding 2): further disclosed over-fires
                ("wof011", ["**Never push to main.** Then wait."], "**Never push to main.**", 0, 0, True, 1,
                 "a whole bold sentence with its markup before more text"),
                ("wof012", ["*Never push to main.* Then wait."], "*Never push to main.*", 0, 0, True, 1,
                 "a whole italic sentence with its markup before more text"),
                ("wof013", ["> Keep the audit log."], "Keep the audit log.", 0, 0, True, 1,
                 "a text after a blockquote marker"),
                ("wof014", ["| Keep the audit log. |"], "Keep the audit log.", 0, 0, True, 1,
                 "a text inside a table cell"),
                ("wof015", ["Type `exit.` Then wait."], "Then wait.", 0, 0, True, 1,
                 "a start after a sentence end inside a code span"),
                ("wof016", ["Use the ` key. Then wait."], "Then wait.", 0, 0, True, 1,
                 "a stray backtick leaves a code span open"),
                ("wof017", ['"Stop." **Then** wait.'], '"Stop."', 0, 0, True, 1,
                 "a whole quoted sentence before a sentence that starts with markup"),
                ("wof018", ['"Stop." \u00c9tape deux.'], '"Stop."', 0, 0, True, 1,
                 "a whole quoted sentence before a non-ASCII capital"),
                ("wof019", ['"He said stop.\u201d" Then wait.'], "Then wait.", 0, 0, True, 1,
                 "a curly closing quote that is text inside a straight quotation"),
                ("wof020", ["(Stop.]) Then wait."], "Then wait.", 0, 0, True, 1,
                 "a stray bracket closer outside a quotation before a closer that closes"),
                ("wrs003", ['"Stop." CI treats as a hard halt.'], '"Stop."', 0, 0, True, 0,
                 "a quoted object before an acronym reads as a whole sentence"),
                ("wrs004", ['"Stop." CI treats as a hard halt.'], "CI treats as a hard halt.", 0, 0, True, 0,
                 "the rest of a sentence after a quoted object and an acronym reads as a whole sentence"),
                # round 4 (review A, finding 1, and review B): a quoted or bracketed sentence before a digit,
                # another quotation or bracket, a code span or a single quote is no sentence end, so a cut
                # at either side of it fails; each opener kind and each refused start has its own vector
                ("wqo001", ['"Stop." (with no other words) is the shutdown command.'], '"Stop."', 0, 0, True,
                 1, "an end on a quoted subject before '('"),
                ("wqo002", ['"Stop." (with no other words) is the shutdown command.'],
                 "(with no other words) is the shutdown command.", 0, 0, True, 1,
                 "a start at '(' after a quoted subject"),
                ("wqo003", ['"Never delete." "unless approved" is the full instruction.'], '"Never delete."',
                 0, 0, True, 1, "an end on a quoted subject before a straight quote"),
                ("wqo004", ['"Never delete." "unless approved" is the full instruction.'],
                 '"unless approved" is the full instruction.', 0, 0, True, 1,
                 "a start at a straight quote after a quoted subject"),
                ("wqo005", ['"Never delete." (the old rule) no longer applies.'], '"Never delete."', 0, 0,
                 True, 1, "an end on a quoted subject before a bracketed aside"),
                ("wqo006", ['"Never delete." (the old rule) no longer applies.'],
                 "(the old rule) no longer applies.", 0, 0, True, 1, "a start at a bracketed aside"),
                ("wqo007", ['"Stop." (the default) halts the run.'], '"Stop."', 0, 0, True, 1,
                 "an end on a quoted subject before '(' and a verb"),
                ("wqo008", ['"Stop." "Go." are the two commands.'], '"Stop."', 0, 0, True, 1,
                 "an end on the first of two quoted subjects"),
                ("wqo009", ['"Stop." "Go." are the two commands.'], '"Go." are the two commands.', 0, 0, True,
                 1, "a start at the second of two quoted subjects"),
                ("wqo010", ["(Optional.) [Deprecated.] marks a step you may skip."],
                 "[Deprecated.] marks a step you may skip.", 0, 0, True, 1,
                 "a start at '[' after a bracketed label"),
                ("wqo011", ['"Stop." \u201cunless approved\u201d is the full instruction.'], '"Stop."', 0, 0,
                 True, 1, "an end on a quoted subject before a curly quote"),
                ("wqo012", ['"Stop." [the default] halts the run.'], '"Stop."', 0, 0, True, 1,
                 "an end on a quoted subject before '['"),
                ("wqo013", ['"Stop." {the default} halts the run.'], '"Stop."', 0, 0, True, 1,
                 "an end on a quoted subject before a brace"),
                ("wqo014", ['"Stop." 3 times in a row halts the run.'], '"Stop."', 0, 0, True, 1,
                 "an end on a quoted subject before a digit"),
                ("wqo015", ['"Stop." 3 times in a row halts the run.'], "3 times in a row halts the run.", 0, 0,
                 True, 1, "a start at a digit after a quoted subject"),
                ("wqo016", ['"Retry." `n` times is the limit.'], '"Retry."', 0, 0, True, 1,
                 "an end on a quoted subject before a code span"),
                ("wqo017", ['"Stop." \'halt\' and \'quit\' do the same.'], '"Stop."', 0, 0, True, 1,
                 "an end on a quoted subject before a single quote"),
                ("wqo018", ['"Stop." \u2018halt\u2019 does the same.'], '"Stop."', 0, 0, True, 1,
                 "an end on a quoted subject before a curly single quote"),
                ("wqo019", ['"Stop." Then wait.'], '"Stop."', 0, 0, True, 0,
                 "a whole quoted sentence before a capital still ends"),
                ("wqo020", ['"Stop." Then wait.'], "Then wait.", 0, 0, True, 0,
                 "a sentence after a whole quoted sentence and a capital still starts"),
                ("wqo021", ['"Stop." "Go." Then wait.'], '"Stop." "Go."', 0, 0, True, 1,
                 "two whole quoted sentences before a capital (a disclosed over-fire)"),
                # round 5 (review A, finding 1): a bare '?' or '!' before a lower-case word, and a bare
                # sentence end before an opener, is no sentence end, so a cut at either side of it fails;
                # each ending and each opener kind has its own vector
                ("wbe001", ["Ask why? before you delete anything."], "Ask why?", 0, 0, True, 1,
                 "an end on a bare '?' before a lower-case word"),
                ("wbe002", ["Ask why? before you delete anything."], "before you delete anything.", 0, 0, True,
                 1, "a start at a lower-case word after a bare '?'"),
                ("wbe003", ["Log in to Yahoo! with the shared account."], "Log in to Yahoo!", 0, 0, True, 1,
                 "an end on a bare '!' before a lower-case word"),
                ("wbe004", ["Log in to Yahoo! with the shared account."], "with the shared account.", 0, 0,
                 True, 1, "a start at a lower-case word after a bare '!'"),
                ("wbe005", ["Ask why? and wait for the answer."], "Ask why?", 0, 0, True, 1,
                 "an end on a bare '?' before a word that starts with 'a'"),
                ("wbe006", ["Stop. (with no other words) is the shutdown command."], "Stop.", 0, 0, True, 1,
                 "an end on a bare '.' before '('"),
                ("wbe007", ["Stop. (with no other words) is the shutdown command."],
                 "(with no other words) is the shutdown command.", 0, 0, True, 1,
                 "a start at '(' after a bare '.'"),
                ("wbe008", ["Stop. [the default] halts the run."], "Stop.", 0, 0, True, 1,
                 "an end on a bare '.' before '['"),
                ("wbe009", ["Stop. {the default} halts the run."], "Stop.", 0, 0, True, 1,
                 "an end on a bare '.' before a brace"),
                ("wbe010", ['Never delete. "unless approved" is the full instruction.'], "Never delete.", 0, 0,
                 True, 1, "an end on a bare '.' before a straight quote"),
                ("wbe011", ['Never delete. "unless approved" is the full instruction.'],
                 '"unless approved" is the full instruction.', 0, 0, True, 1,
                 "a start at a straight quote after a bare '.'"),
                ("wbe012", ["Stop. \u201cunless approved\u201d is the full instruction."], "Stop.", 0, 0, True,
                 1, "an end on a bare '.' before a curly quote"),
                ("wbe013", ["Retry. `n` times is the limit."], "Retry.", 0, 0, True, 1,
                 "an end on a bare '.' before a code span"),
                ("wbe014", ["Ask why? (in writing) before you delete anything."], "Ask why?", 0, 0, True, 1,
                 "an end on a bare '?' before '('"),
                ("wbe015", ['Stop now! "or else" is no excuse.'], "Stop now!", 0, 0, True, 1,
                 "an end on a bare '!' before a straight quote"),
                # ... and the disclosed over-fires of that rule are refused (exit 1)
                ("wbo001", ["Is it gzip or zstd? zstd is the default."], "zstd is the default.", 0, 0, True, 1,
                 "a sentence after a bare '?' that starts with a lower-case word"),
                ("wbo002", ["Run it. `make check` must pass."], "`make check` must pass.", 0, 0, True, 1,
                 "a sentence after a bare '.' that starts with a code span"),
                ("wbo007", ["Is it gzip or zstd? zstd is the default."], "Is it gzip or zstd?", 0, 0, True, 1,
                 "a sentence before one that starts with a lower-case word after its bare '?'"),
                ("wbo003", ["Run the suite. (Optional.) Then wait."], "Run the suite.", 0, 0, True, 1,
                 "a sentence before a whole bracketed sentence"),
                ("wbo004", ["Run the suite. (Optional.) Then wait."], "(Optional.)", 0, 0, True, 1,
                 "a whole bracketed sentence after a bare '.'"),
                ("wbo005", ["Run the suite. (Optional.) Then wait."], "Then wait.", 0, 0, True, 1,
                 "a sentence after a whole bracketed sentence that follows a bare '.'"),
                ("wbo006", ["Run the suite. (Optional.) Then wait."], "Run the suite. (Optional.) Then wait.",
                 0, 0, True, 0, "the whole line still passes"),
                ("wbo008", ['Run the suite. "Done." Then wait.'], "Then wait.", 0, 0, True, 1,
                 "a sentence after a whole quoted sentence that follows a bare '.'"),
                # round 5 (review B, finding 1): whitespace between a sentence end and its closer is refused
                ("wws001", ['"Stop. " Then wait.'], '"Stop. "', 0, 0, True, 1,
                 "a quoted sentence with a space before its closer"),
                ("wws002", ['"Stop. " Then wait.'], "Then wait.", 0, 0, True, 1,
                 "a start after a quoted sentence with a space before its closer"),
                ("wws003", ['"Stop.', '" Then wait.'], '"Stop.\n"', 0, 1, False, 1,
                 "a quoted sentence with a line break before its closer"),
                # round 5 (review A, finding 2): a closer is no next start either
                ("wnc001", ['"Stop." ) is the rule.'], '"Stop."', 0, 0, True, 1,
                 "an end on a quoted sentence before a stray ')'"),
                ("wnc002", ['"Stop." ] is the rule.'], '"Stop."', 0, 0, True, 1,
                 "an end on a quoted sentence before a stray ']'"),
                ("wnc003", ['"Stop." } is the rule.'], '"Stop."', 0, 0, True, 1,
                 "an end on a quoted sentence before a stray brace"),
                ("wnc004", ['"Stop." \u201d is the rule.'], '"Stop."', 0, 0, True, 1,
                 "an end on a quoted sentence before a stray curly closing quote"),
                # round 5: the disclosed residuals of residual (2), the bare sentence end, and of the
                # paragraph edge (c) still pass (exit 0)
                ("wrs005", ["Yahoo! Mail is the only approved client."], "Yahoo!", 0, 0, True, 0,
                 "a bare '!' inside a name before a capital reads as a sentence end"),
                ("wrs006", ["Yahoo! Mail is the only approved client."], "Mail is the only approved client.", 0,
                 0, True, 0, "the rest of a sentence after a bare '!' and a capital reads as a whole sentence"),
                ("wrs007", ["Use a tool, e.g. the linter, on every file."], "the linter, on every file.", 0, 0,
                 True, 0, "a bare '.' before a lower-case word reads as a sentence end"),
                ("wrs008", ["A revert drops the changes in it. git checkout -- <path> overwrites the tree."],
                 "git checkout -- <path> overwrites the tree.", 0, 0, True, 0,
                 "a sentence that starts with a lower-case command name after a bare '.'"),
                ("wrs009", ["Stop. 3 times in a row halts the run."], "Stop.", 0, 0, True, 0,
                 "a bare '.' before a digit reads as a sentence end"),
                ("wrs010", ["Stop. 'halt' does the same."], "Stop.", 0, 0, True, 0,
                 "a bare '.' before a single quote reads as a sentence end"),
                ("wrs011", ["Stop. **now** is the rule."], "Stop.", 0, 0, True, 0,
                 "a bare '.' before markup reads as a sentence end"),
                ("wrs012", ["Ask why? \u00e9tape is the word."], "Ask why?", 0, 0, True, 0,
                 "a bare '?' before a non-ASCII lower-case letter reads as a sentence end"),
                ("wrs013", ['"Never delete."', "(the old rule) no longer applies."], '"Never delete."', 0, 0,
                 True, 0, "a quoted sentence at a paragraph edge passes whatever the next paragraph starts with"),
                ("wrs014", ['"Never delete."', "(the old rule) no longer applies."],
                 "(the old rule) no longer applies.", 1, 1, True, 0,
                 "a text at a paragraph start passes whatever the paragraph before it ends with"),
            ]
            # Every closer that may follow a sentence end, one vector each: after a whole enclosed sentence
            # the next sentence starts cleanly (exit 0); single quotes are not paired, so a sentence end
            # before one is refused (exit 1, a disclosed over-fire).
            for k, (opener, closer, want) in enumerate((('"', '"', 0), ("'", "'", 1), ("\u201c", "\u201d", 0),
                                                        ("\u2018", "\u2019", 1), ("(", ")", 0), ("[", "]", 0),
                                                        ("{", "}", 0)), 1):
                edge_vectors.append(("wclo0{}".format(k), ["{}Stop.{} Then wait.".format(opener, closer)],
                                     "Then wait.", 0, 0, True, want, "a start after a closer, case {}".format(k)))
            for corpus, obligations, text, first, last, paras, want, label in edge_vectors:
                got = _model_c_case(corpus, obligations, text, first, last, paragraphs=paras)
                if got != want:
                    failures.append("whole-clause edges, {} ({}): expected exit {}, got {}".format(
                        label, corpus, want, got))

            # (36) absolute source-path now FAILS. The row points at the ABSOLUTE path of its real scanned
            # source (matching digest and frontmatter), so pathlib would discard the root and only the
            # absolute-path check can catch it -> exit 1 (before the fix this passed).
            lines, line_of = _rule_lines("cabspt", ["the only obligation"])
            digest = _sha_of(lines)
            good_row = {"clause-id": "cabspt.1", "corpus-id": "cabspt",
                        "source-path": ".aiqt/core/rules/cabspt.md",
                        "start-line": line_of[0], "end-line": line_of[0],
                        "canonical-text": "the only obligation", "source-digest": digest}
            base = _fresh({"cabspt": lines}, [dict(good_row)], [("cabspt", "1.1.0"), ("cabspt.1", "1.1.0")])
            abs_src = str((base / ".aiqt" / "core" / "rules" / "cabspt.md").resolve())
            (base / ".aiqt" / "core" / "clauses.toml").write_text(
                _toml_clause(dict(good_row, **{"source-path": abs_src})) + "\n", encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("absolute source-path: expected exit 1")

            # (37) phantom born id -> completeness FAIL. A born id absent from the inventory and carried by
            # no retirement row is neither live nor retired -> exit 1.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born + [("cghost", "1.1.0"), ("cghost.1", "1.1.0")])
            if _run_quiet(**_paths(base, genesis=True)) != 1:
                failures.append("phantom born id: expected exit 1")

            # (38) register temporal integrity: an id retired at a release BEFORE its born-release -> exit 1.
            # cretire / cretire.1 are born at 2.0.0 yet tombstoned at 1.0.0 (retired, not live -> no
            # resurrection or phantom finding, so the temporal leg is what fires).
            rules, rows, born = _good_genesis()
            born = born + [("cretire", "2.0.0"), ("cretire.1", "2.0.0")]
            tombs = [("cretire", "1.0.0"), ("cretire.1", "1.0.0")]
            base = _fresh(rules, rows, born, tombstones=tombs)
            if _run_quiet(**_paths(base)) != 1:
                failures.append("retirement-release before born-release: expected exit 1")

            # (39) register graph integrity: a two-node successor cycle (a -> b, b -> a; both born, both
            # absent from the inventory and retired via their successor rows) evades the per-row self-loop
            # check but the graph leg catches it -> exit 1.
            rules, rows, born = _good_genesis()
            born = born + [("ccyclea.1", "1.0.0"), ("ccycleb.1", "1.0.0")]
            succs = [("ccyclea.1", "1.4.0", "ccycleb.1"), ("ccycleb.1", "1.4.0", "ccyclea.1")]
            base = _fresh(rules, rows, born, successors=succs)
            if _run_quiet(**_paths(base)) != 1:
                failures.append("two-node successor cycle: expected exit 1")

            # (40) a born section that is a scalar (born = 7) is malformed input -> fail-closed exit 2, never
            # an uncaught TypeError crash.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born)
            (base / ".aiqt" / "core" / "id-history.toml").write_text("born = 7\n", encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("born section as a scalar: expected fail-closed exit 2")

            # (41) a tombstone section written as a table ([tombstone], not [[tombstone]]) is malformed input
            # -> fail-closed exit 2, never a silent empty read.
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born)
            reg = base / ".aiqt" / "core" / "id-history.toml"
            reg.write_text(reg.read_text(encoding="utf-8") + "\n[tombstone]\n", encoding="utf-8")
            if _run_quiet(**_paths(base)) != 2:
                failures.append("tombstone section as a table: expected fail-closed exit 2")

            # (42) round-4 A: a newline-tainted born-release is malformed input -> fail-closed exit 2. The
            # shipped SemVer parser's match()/`$` slips a trailing LF past ("1.1.0\n" parses); the register
            # value carries a REAL LF via the TOML \n escape (as the codex repro did), so this exercises the
            # whitespace guard in _require_release, not a TOML parse error. A clean release passes (case 0).
            rules, rows, born = _good_genesis()
            base = _fresh(rules, rows, born)
            reg = base / ".aiqt" / "core" / "id-history.toml"
            reg.write_text(reg.read_text(encoding="utf-8").replace(
                'born-release = "1.1.0"', 'born-release = "1.1.0\\n"', 1), encoding="utf-8")
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("newline-tainted born-release: expected fail-closed exit 2")

            # (43) round-4 B: an orphan-corpus born id -> completeness FAIL. corphan.1 has a born row but its
            # owning corpus-id corphan has none; it is retired (so neither live nor a phantom), so only the
            # orphan-corpus leg fires -> exit 1. A born clause-id whose corpus IS born passes (case 0).
            rules, rows, born = _good_genesis()
            born = born + [("corphan.1", "1.0.0")]
            base = _fresh(rules, rows, born, tombstones=[("corphan.1", "1.4.0")])
            if _run_quiet(**_paths(base)) != 1:
                failures.append("orphan-corpus born id: expected exit 1")

            # (44) round-4 C: an unbounded (5000-digit) ordinal is treated as malformed input -> fail-closed
            # exit 2, never an uncaught ValueError from CPython's int-conversion guard. A normal ordinal
            # passes (case 0). split_clause_id returns None so check_scheme reports a scheme finding.
            rules, rows, born = _good_genesis()
            rows[0]["clause-id"] = "calpha." + ("1" * 5000)
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("unbounded ordinal: expected fail-closed exit 2 (no crash)")

            # (45) round-4 D: a malformed source-digest syntax ("not-a-sha256") is malformed input ->
            # fail-closed exit 2, distinct from the exit-1 finding a well-formed-but-wrong digest gets
            # (case 6, "0" * 64). A valid 64-hex digest passes (case 0).
            rules, rows, born = _good_genesis()
            rows[0]["source-digest"] = "not-a-sha256"
            base = _fresh(rules, rows, born)
            if _run_quiet(**_paths(base, genesis=True)) != 2:
                failures.append("malformed source-digest syntax: expected fail-closed exit 2")

            # (46) deferred item 1: a predecessor inventory carrying a non-string (integer) clause-id is
            # malformed input; the hardened load_prev_ids fails closed (exit 2), never silently shrinking
            # the disappearance leg's coverage. (Before the fix the row was silently skipped.)
            rules, rows, born = _good_genesis()
            current = _fresh(rules, rows, born)
            prev = current / ".aiqt" / "core" / "prev-int.toml"
            prev.write_text("".join(_toml_clause(r) + "\n" for r in rows)
                            + '[[clause]]\nclause-id = 7\ncorpus-id = "calpha"\n'
                            'source-path = ".aiqt/core/rules/calpha.md"\nstart-line = 8\nend-line = 8\n'
                            'canonical-text = "x"\nsource-digest = "0"\n', encoding="utf-8")
            if _run_quiet(**_paths(current, prev_inventory_path=prev)) != 2:
                failures.append("predecessor inventory integer clause-id: expected fail-closed exit 2")

            # (47) deferred item 1: a predecessor inventory with a MALFORMED string clause-id ("calpha.01",
            # a padded ordinal) is malformed input -> fail-closed exit 2.
            rules, rows, born = _good_genesis()
            current = _fresh(rules, rows, born)
            prev = current / ".aiqt" / "core" / "prev-bad.toml"
            bad_row = dict(rows[0], **{"clause-id": "calpha.01"})
            prev.write_text("".join(_toml_clause(r) + "\n" for r in rows)
                            + _toml_clause(bad_row) + "\n", encoding="utf-8")
            if _run_quiet(**_paths(current, prev_inventory_path=prev)) != 2:
                failures.append("predecessor inventory malformed clause-id: expected fail-closed exit 2")

            # (48) deferred item 2 end-to-end: a successor edge at a LATER release naming an id already
            # tombstoned at an EARLIER release is a resurrection through the register -> exit 1. All four
            # ids are dead (absent from the inventory) each with its own retirement row, so completeness
            # and resurrection stay clean and the temporal leg is what fires: cdead.1 is tombstoned at
            # 1.1.0, and the successor edge cdead2.1 -> cdead.1 is at 1.2.0 (1.1.0 < 1.2.0).
            rules, rows, born = _good_genesis()
            born2 = born + [("cdeada", "1.0.0"), ("cdeada.1", "1.0.0"),
                            ("cdeadb", "1.0.0"), ("cdeadb.1", "1.0.0")]
            tombs = [("cdeada", "1.1.0"), ("cdeada.1", "1.1.0"), ("cdeadb", "1.2.0")]
            succs = [("cdeadb.1", "1.2.0", "cdeada.1")]
            base = _fresh(rules, rows, born2, tombstones=tombs, successors=succs)
            if _run_quiet(**_paths(base)) != 1:
                failures.append("successor-already-retired resurrection: expected exit 1")

            # (49) deferred item 2 end-to-end: a same-release pass-through fold is permitted -> exit 0.
            # cfold.1 and cfoldb.1 both born 1.0.0 and both retired at 1.1.0, with cfold.1 -> cfoldb.1 at
            # 1.1.0 (equal release, strict comparison so no resurrection). Both absent from the inventory,
            # each with its retirement row, so completeness stays clean too.
            rules, rows, born = _good_genesis()
            born3 = born + [("cfolda", "1.0.0"), ("cfolda.1", "1.0.0"),
                            ("cfoldb", "1.0.0"), ("cfoldb.1", "1.0.0")]
            tombs3 = [("cfolda", "1.1.0"), ("cfoldb", "1.1.0"), ("cfoldb.1", "1.1.0")]
            succs3 = [("cfolda.1", "1.1.0", "cfoldb.1")]
            base = _fresh(rules, rows, born3, tombstones=tombs3, successors=succs3)
            if _run_quiet(**_paths(base)) != 0:
                failures.append("same-release pass-through fold: expected exit 0")
            # (54) TEXT COVERAGE: every sentence or clause of a rule body lies inside a clause row or a
            # recorded exemption. _cover builds a one-rule corpus whose rows cover the listed (line index,
            # text) pairs, each with its born row, optionally writes an exemptions file, and returns
            # (exit code, output). The first vectors are the reported defect: a passage whose clause rows and
            # id-history rows are removed while its text stays in the rule passed the id-only legs.
            # final_newline=False writes the rule with no trailing LF; cover_bases keeps each corpus's base.
            cover_bases = {}

            def _cover(corpus, obligations, covered, exemptions=None, exemptions_text=None, extra_born=(),
                       tombstones=(), lines=None, final_newline=True, **overrides):
                if lines is None:
                    lines, line_of = _rule_lines(corpus, obligations)
                else:
                    line_of = {k: lines.index(text) + 1 for k, text in enumerate(obligations)}
                body = _canon(lines) if final_newline else "\n".join(lines)
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                rows, born = [], [(corpus, "1.1.0")]
                for ordinal, (idx, text) in enumerate(covered, 1):
                    rows.append({"clause-id": "{}.{}".format(corpus, ordinal), "corpus-id": corpus,
                                 "source-path": ".aiqt/core/rules/{}.md".format(corpus),
                                 "start-line": line_of[idx], "end-line": line_of[idx],
                                 "canonical-text": text, "source-digest": digest})
                    born.append(("{}.{}".format(corpus, ordinal), "1.1.0"))
                base = _fresh({corpus: lines}, rows, born + list(extra_born), tombstones=tombstones)
                (base / ".aiqt" / "core" / "rules" / "{}.md".format(corpus)).write_text(body, encoding="utf-8")
                cover_bases[corpus] = base
                kw = {"genesis": not tombstones}
                if exemptions is not None or exemptions_text is not None:
                    kw["exemptions_path"] = base / EXEMPTIONS_REL
                    kw["exemptions_path"].write_text(
                        exemptions_text if exemptions_text is not None else _toml_exemptions(exemptions),
                        encoding="utf-8")
                kw.update(overrides)
                return _run_capture(**_paths(base, **kw))

            two = ["Alpha holds the lease.", "Beta logs every wait."]
            uncovered = "lies inside no clause row"
            devnull_link = base_tmp / "devnull-exemptions.toml"
            devnull_link.symlink_to(os.devnull)
            cover_vectors = [
                ("both sentences carry a row", "ccova1", two, [(0, two[0]), (1, two[1])], {}, 0, ""),
                ("a passage stays while its clause row and id-history row are removed", "ccova2", two,
                 [(0, two[0])], {}, 1, uncovered),
                ("a passage stays while its clause row is retired by a tombstone", "ccova3", two, [(0, two[0])],
                 {"extra_born": [("ccova3.2", "1.1.0")], "tombstones": [("ccova3.2", "1.2.0")]}, 1, uncovered),
                ("the passage removed together with its rows and history", "ccova4", two[:1], [(0, two[0])], {},
                 0, ""),
                ("an exemption with a reason", "ccova5", two, [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccova5", "text": two[1], "reason": "A rationale."}]}, 0, ""),
                ("an exemption with an empty reason", "ccova6", two, [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccova6", "text": two[1], "reason": ""}]}, 1, "no reason"),
                ("an exemption with a whitespace-only reason", "ccova7", two, [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccova7", "text": two[1], "reason": " \t "}]}, 1, "no reason"),
                ("an exemption with no reason key", "ccova8", two, [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccova8", "text": two[1]}]}, 2, "keys are exactly"),
                ("an exemption whose reason is not a string", "ccova9", two, [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccova9", "text": two[1], "reason": 7}]}, 2, "not a string"),
                ("an exemption naming text the rule no longer holds", "ccovb1", two, [(0, two[0]), (1, two[1])],
                 {"exemptions": [{"corpus-id": "ccovb1", "text": "Gamma is gone.", "reason": "Gone."}]}, 1,
                 "stale exemption"),
                ("an exemption left behind when its sentence is reworded", "ccovb2",
                 [two[0], "Beta logs each wait."], [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccovb2", "text": two[1], "reason": "A rationale."}]}, 1,
                 "stale exemption"),
                ("an exemption naming a rule that does not exist", "ccovb3", two, [(0, two[0]), (1, two[1])],
                 {"exemptions": [{"corpus-id": "cgone1", "text": two[1], "reason": "A rationale."}]}, 1,
                 "names no rule source"),
                ("an exemption naming text a clause row covers", "ccovb4", two, [(0, two[0]), (1, two[1])],
                 {"exemptions": [{"corpus-id": "ccovb4", "text": two[1], "reason": "A rationale."}]}, 1,
                 "redundant"),
                ("an exemption whose text occurs twice", "ccovb5", [two[0], "Beta waits. Beta waits."], [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccovb5", "text": "Beta waits.", "reason": "A rationale."}]}, 1,
                 "occurs 2 times"),
                ("an exemption cut inside a sentence", "ccovb6", two, [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccovb6", "text": "logs every wait.", "reason": "A rationale."}]},
                 1, "names whole sentences"),
                ("an exemption repeated", "ccovb7", two, [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccovb7", "text": two[1], "reason": "A rationale."},
                                 {"corpus-id": "ccovb7", "text": two[1], "reason": "A rationale."}]}, 1, "repeats exemption"),
                ("an exemption that names only a ';'", "ccovb8", ["Alpha holds the lease; beta reads it."],
                 [(0, "Alpha holds the lease"), (0, "beta reads it.")],
                 {"exemptions": [{"corpus-id": "ccovb8", "text": ";", "reason": "A rationale."}]}, 1,
                 "names no sentence or clause"),
                ("an exemption with edge whitespace", "ccovb9", two, [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccovb9", "text": two[1] + " ", "reason": "A rationale."}]}, 1,
                 "begins or ends with whitespace"),
                ("an exemption with empty text", "ccovc1", two, [(0, two[0]), (1, two[1])],
                 {"exemptions": [{"corpus-id": "ccovc1", "text": "", "reason": "A rationale."}]}, 1,
                 "empty or whitespace-only"),
                ("an exemption of a whole sentence of two clauses", "ccovc2", [two[0], "Beta logs it; gamma reads it."],
                 [(0, two[0])], {"exemptions": [{"corpus-id": "ccovc2", "text": "Beta logs it; gamma reads it.",
                                              "reason": "A rationale."}]}, 0, ""),
                ("a row over one clause leaves the other clause of its sentence uncovered", "ccovc3",
                 ["Alpha holds the lease; beta reads it."], [(0, "Alpha holds the lease")], {}, 1, uncovered),
                ("a row over one clause and an exemption over the other", "ccovc4",
                 ["Alpha holds the lease; beta reads it."], [(0, "Alpha holds the lease")],
                 {"exemptions": [{"corpus-id": "ccovc4", "text": "beta reads it.", "reason": "A rationale."}]}, 0,
                 ""),
                ("an exemptions file with an unknown top-level key", "ccovc5", two, [(0, two[0]), (1, two[1])],
                 {"exemptions_text": "other = 1\n"}, 2, "only top-level key"),
                ("an exemptions file whose exempt is not an array of tables", "ccovc6", two,
                 [(0, two[0]), (1, two[1])], {"exemptions_text": "exempt = 7\n"}, 2, "not an array of tables"),
                ("an exemptions file that is not TOML", "ccovc7", two, [(0, two[0]), (1, two[1])],
                 {"exemptions_text": "[[exempt]\n"}, 2, "cannot read the clause exemptions"),
                ("an exemption with an extra key", "ccovc8", two, [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccovc8", "text": two[1], "reason": "A rationale.", "note": "x"}]},
                 2, "keys are exactly"),
                ("an exemption with a malformed corpus-id", "ccovc9", two, [(0, two[0])],
                 {"exemptions": [{"corpus-id": "Bad", "text": two[1], "reason": "A rationale."}]}, 2,
                 "not a well-formed corpus-id"),
            ]
            # CANNOT EVALUATE: a line the splitter cannot classify, an opener left open at a paragraph end,
            # and a unit with no letter or digit are exit 2. Each fixture's prose is covered by a row, so
            # without the refusal the leg would report the markup as uncovered text (exit 1), never 0.
            for k, markup in enumerate(["- beta item", "* beta item", "+ beta item", "1. beta item", "2) beta item",
                                        "> beta quote", "| beta | cell |", "```", "~~~", "## Beta heading",
                                        "<!-- beta -->", "<div>beta</div>", "    beta indented",
                                        "\tbeta indented", "---", "***", "==="], 1):
                cover_vectors.append(("an unclassifiable line {!r}".format(markup), "ccovm{}".format(k),
                                      [two[0], markup], [(0, two[0])], {}, 2, "cannot classify"))
            cover_vectors += [
                ("an inch mark leaves a quotation open at the paragraph end", "ccovn1",
                 [two[0], 'Use a 3" pipe.'], [(0, two[0])], {}, 2, "still open"),
                ("a stray backtick leaves a code span open at the paragraph end", "ccovn2",
                 [two[0], "Press the ` key."], [(0, two[0])], {}, 2, "still open"),
                ("a unit with no letter or digit", "ccovn3", [two[0], "Stop. ... Go."],
                 [(0, two[0]), (1, "Stop."), (1, "Go.")], {}, 2, "no letter or digit"),
                ("a title with no blank line after it", "ccovn4", [two[0]], [(0, two[0])],
                 {"lines": ["---", "corpus-id: ccovn4", "origin: pack", "slug: fixture", "---", "", "# Fixture rule",
                           two[0]]}, 2, "no blank line after it"),
                ("a second heading", "ccovn5", [two[0]], [(0, two[0])],
                 {"lines": ["---", "corpus-id: ccovn5", "origin: pack", "slug: fixture", "---", "", "# Fixture rule",
                           "", "# Another title", "", two[0]]}, 2, "cannot classify"),
                ("a body with no title is checked whole", "ccovn6", [two[0]], [(0, two[0])],
                 {"lines": ["---", "corpus-id: ccovn6", "origin: pack", "slug: fixture", "---", "", two[0]]}, 0, ""),
                ("an exemptions file that does not exist", "ccovn7", two, [(0, two[0]), (1, two[1])],
                 {"exemptions_path": Path("/nonexistent-aiqt-dir/clause-exemptions.toml")}, 2, "does not exist"),
                ("an absent exemptions file is a cannot-evaluate even with a sentence uncovered", "ccovn8", two,
                 [(0, two[0])], {"exemptions_path": Path("/nonexistent-aiqt-dir/clause-exemptions.toml")}, 2,
                 "does not exist"),
                ("an underscore-only unit no row covers", "ccovn10", [two[0], "_"], [(0, two[0])], {}, 2,
                 "no letter or digit"),
                ("an underscore-only unit an exemption names", "ccovn11", [two[0], "_"], [(0, two[0])],
                 {"exemptions": [{"corpus-id": "ccovn11", "text": "_", "reason": "Punctuation."}]}, 2,
                 "no letter or digit"),
                ("an inch mark leaves a quotation open at the end of a paragraph that is not the last", "ccovn12",
                 [two[0], 'Use a 3" pipe.', two[1]], [(0, two[0]), (2, two[1])], {}, 2, "still open"),
                ("an inch mark leaves a quotation open at the end of a file with no final newline", "ccovn13",
                 [two[0], 'Use a 3" pipe.'], [(0, two[0])], {"final_newline": False}, 2, "still open"),
                ("a file with no final newline and no open opener", "ccovn14", two, [(0, two[0]), (1, two[1])],
                 {"final_newline": False}, 0, ""),
                ("wrapped prose lines that begin with '#', a number or '<' continue their paragraph", "ccovn15",
                 [two[0], "#507 and its review.", "2) when unsure.", "<path> only after a backup."],
                 [(0, two[0]), (1, "#507 and its review."), (2, "2) when unsure."),
                  (3, "<path> only after a backup.")],
                 {"lines": ["---", "corpus-id: ccovn15", "origin: pack", "slug: fixture", "---", "", "# Fixture rule",
                           "", two[0], "#507 and its review.", "2) when unsure.", "<path> only after a backup."]},
                 0, ""),
                ("a paragraph that opens with a tag whose quoted value holds '>' is refused, even exempted",
                 "ccovn16", [two[0]], [(0, two[0])],
                 {"lines": ["---", "corpus-id: ccovn16", "origin: pack", "slug: fixture", "---", "", "# Fixture rule",
                            "", two[0], "", '<custom data=">">', "Hidden."],
                  "exemptions": [{"corpus-id": "ccovn16", "text": '<custom data=">">\nHidden.',
                                  "reason": "A rationale."}]}, 2, "cannot classify"),
                ("a link reference definition whose label spans lines is refused, even exempted", "ccovn17",
                 [two[0]], [(0, two[0])],
                 {"lines": ["---", "corpus-id: ccovn17", "origin: pack", "slug: fixture", "---", "", "# Fixture rule",
                            "", two[0], "", "[foo", "bar]: /url"],
                  "exemptions": [{"corpus-id": "ccovn17", "text": "[foo\nbar]: /url", "reason": "A rationale."}]},
                 2, "cannot classify"),
                ("a bare carriage return in a rule body is a cannot-evaluate, even exempted", "ccovn18",
                 [two[0]], [(0, two[0])],
                 {"lines": ["---", "corpus-id: ccovn18", "origin: pack", "slug: fixture", "---", "", "# Fixture rule",
                            "", two[0], "", "Beta waits.\r# Beta heading"],
                  "exemptions": [{"corpus-id": "ccovn18", "text": "Beta waits.", "reason": "A rationale."},
                                 {"corpus-id": "ccovn18", "text": "# Beta heading", "reason": "A rationale."}]},
                 2, "carriage return"),
                ("a GFM table delimiter row with no leading pipe is refused, even exempted", "ccovn19",
                 [two[0]], [(0, two[0])],
                 {"lines": ["---", "corpus-id: ccovn19", "origin: pack", "slug: fixture", "---", "", "# Fixture rule",
                            "", two[0], "", "Alpha a | b", ":-- | --:"],
                  "exemptions": [{"corpus-id": "ccovn19", "text": "Alpha a | b\n:-- | --:",
                                  "reason": "A rationale."}]}, 2, "cannot classify"),
                ("an empty exemptions file", "ccovn20", two, [(0, two[0]), (1, two[1])], {"exemptions_text": ""},
                 2, "no 'exempt' key"),
                ("a comment-only exemptions file", "ccovn21", two, [(0, two[0]), (1, two[1])],
                 {"exemptions_text": "# nothing exempted\n"}, 2, "no 'exempt' key"),
                ("an exemptions file that is a symlink to the null device", "ccovn22", two,
                 [(0, two[0]), (1, two[1])], {"exemptions_path": devnull_link}, 2, "no 'exempt' key"),
                ("an explicit empty register with every sentence covered", "ccovn23", two,
                 [(0, two[0]), (1, two[1])], {"exemptions_text": "exempt = []\n"}, 0, ""),
                ("an explicit empty register leaves an uncovered sentence a finding", "ccovn24", two, [(0, two[0])],
                 {"exemptions_text": "exempt = []\n"}, 1, uncovered),
                ("a form feed in a rule body is a cannot-evaluate, even exempted", "ccovn25",
                 [two[0]], [(0, two[0])],
                 {"lines": ["---", "corpus-id: ccovn25", "origin: pack", "slug: fixture", "---", "", "# Fixture rule",
                            "", two[0], "", "Beta waits.\x0c# Beta heading"],
                  "exemptions": [{"corpus-id": "ccovn25", "text": "Beta waits.", "reason": "A rationale."},
                                 {"corpus-id": "ccovn25", "text": "# Beta heading", "reason": "A rationale."}]},
                 2, "form feed"),
                ("an inline HTML comment in a rule body is refused, even exempted", "ccovn26",
                 [two[0]], [(0, two[0])],
                 {"lines": ["---", "corpus-id: ccovn26", "origin: pack", "slug: fixture", "---", "", "# Fixture rule",
                            "", two[0], "", "Alpha holds <!-- hidden words --> today."],
                  "exemptions": [{"corpus-id": "ccovn26", "text": "Alpha holds <!-- hidden words --> today.",
                                  "reason": "A rationale."}]}, 2, "does not show"),
                ("a delimiter row the vendored table reads, with letters after its dashes, is refused, even exempted",
                 "ccovn27", [two[0]], [(0, two[0])],
                 {"lines": ["---", "corpus-id: ccovn27", "origin: pack", "slug: fixture", "---", "", "# Fixture rule",
                            "", two[0], "", "Alpha a | b", "--x|--y"],
                  "exemptions": [{"corpus-id": "ccovn27", "text": "Alpha a | b\n--x|--y",
                                  "reason": "A rationale."}]}, 2, "cannot classify"),
            ]
            for label, corpus, obligations, covered, extra, want, needle in cover_vectors:
                got, out = _cover(corpus, obligations, covered, **extra)
                if got != want or needle not in out:
                    failures.append("text coverage, {} ({}): expected exit {} naming {!r}, got exit {}: "
                                    "{}".format(label, corpus, want, needle, got, out.strip()[-300:]))

            # Deleting the exemptions file never hides a finding. Through main() at the DEFAULT path: a stale
            # exemption is exit 1 with the file present, and with the file deleted the gate is exit 2, never the
            # clean pass that reading an absent file as no exemption gave (both sentences carry a row).
            _cover("ccovn9", two, [(0, two[0]), (1, two[1])],
                   exemptions=[{"corpus-id": "ccovn9", "text": "Gamma is gone.", "reason": "Gone."}])
            stale_root = cover_bases["ccovn9"]
            for label, want, needle in (("present", 1, "stale exemption"), ("deleted", 2, "does not exist")):
                if label == "deleted":
                    (stale_root / EXEMPTIONS_REL).unlink()
                out = io.StringIO()
                with redirect_stdout(out), redirect_stderr(out):
                    got = main(["--root", str(stale_root), "--genesis"])
                if got != want or needle not in out.getvalue():
                    failures.append("text coverage, a stale exemption with its file {} at the default path "
                                    "(ccovn9): expected exit {} naming {!r}, got exit {}: {}".format(
                                        label, want, needle, got, out.getvalue().strip()[-300:]))

            # The leg reads every rule itself and fails closed on one it cannot read: a missing file, a
            # directory, and a file that is not UTF-8 each raise GateError (cannot evaluate), never a pass.
            unreadable = base_tmp / "unreadable"
            (unreadable / "dir.md").mkdir(parents=True)
            (unreadable / "latin1.md").write_bytes(b"---\ncorpus-id: clatin\n---\n\nCaf\xe9 holds.\n")
            for name in ("missing.md", "dir.md", "latin1.md"):
                try:
                    check_text_coverage({"cunrd1": unreadable / name}, {}, [])
                    failures.append("text coverage: an unreadable rule source ({}) must raise GateError".format(name))
                except GateError:
                    pass
        finally:
            shutil.rmtree(base_tmp, ignore_errors=True)

    if _vectors:
        _not_run_vector(failures)

    if failures:
        print("SELF-TEST FAIL:")
        for f in failures:
            print("  - " + f)
        return 1
    core = ("clause-id parsing, numeric ordinal comparison (lexicographic trap both directions), "
            "resurrection and completeness set logic")
    print("SELF-TEST PASS: {}; and the end-to-end fixtures hold (passing genesis, deferred/armed "
          "manifest leg, padded-ordinal fail-closed, duplicate id, missing corpus-id, span out of "
          "range (the span diagnostic itself, alone and beside a valid covering row), text mismatch, "
          "digest mismatch, missing born row, resurrected id, cumulative-max "
          "fail, lexicographic-trap pass, unexplained disappearance, the fail-closed register cases, "
          "the Model C sub-line pass, multi-line embedded-LF pass, absent-text fail, twice-in-window "
          "fail, untight-window fail, and empty-canonical-text fail; and the round-2 fixes: "
          "cumulative-max unrelated-tombstone bypass fail, leading/trailing-newline tightness fails, "
          "out-of-rules-dir source-path fail, non-string clause-id / corpus-id-failing-CID_RE / "
          "malformed-born-id / malformed-, self-loop-, and dangling-successor-id / boolean-line-number "
          "fail-closed exit 2, well-formed corpus-id mismatch exit 1, successor-row load with "
          "successor-retired resurrection, explained-disappearance pass, whitespace-only canonical-text "
          "fail, and legitimate shared-window rows pass; and the round-3 fixes: trailing-LF clause-id "
          "reject, absolute source-path fail, phantom-born-id fail, retirement-before-birth fail, "
          "two-node successor-cycle fail, and born-scalar / tombstone-table fail-closed exit 2; and "
          "the round-4 fixes: newline-tainted born-release fail-closed exit 2, orphan-corpus born-id "
          "fail, unbounded-ordinal fail-closed exit 2, and malformed-digest-syntax fail-closed exit 2; "
          "and the Step-4 deferred items: a predecessor inventory with an integer or a malformed "
          "clause-id fails closed (exit 2, the hardened load_prev_ids), a successor-id already retired "
          "at an earlier release is a resurrection through the register (exit 1), and a same-release "
          "pass-through fold is permitted (exit 0); and the whole-clause edges: a whole sentence passes, a "
          "canonical-text cut short inside a clause fails (final '.' dropped inside the line or at the "
          "window end, last word dropped, cut at a plain comma, first character dropped, wrapped last "
          "line dropped, a row at a line start after an unended sentence), the legitimate ';' "
          "clause splits and a row after a sentence end on an earlier part of its line pass, the "
          "round-1 reproductions hold (list and quotation cuts at ', and' / ', or' fail; whole quoted "
          "and bracketed sentences pass), the round-2 reproductions hold (a cut at a colon fails; a "
          "fragment from inside a quotation or bracket, judged from the source paragraph, fails; a "
          "sentence end inside an enclosure opened mid-sentence is no boundary), the round-3 "
          "reproductions hold (a cut at either side of a quoted or bracketed sentence that a longer "
          "sentence goes on from fails; a whole quoted sentence that ends inside a bracket passes; a "
          "cut inside a code span fails; the scan crosses a single line break), the round-4 "
          "reproductions hold (a quoted or bracketed sentence ends only before an ASCII capital, so "
          "a cut at either side of one before a digit, another quotation or bracket, a code span or "
          "a single quote fails), the round-5 reproductions hold (a bare '?' or '!' before a lower-case "
          "word, or a bare sentence end before an opener, is no sentence end, and a closer is no next "
          "start), each boundary "
          "sub-predicate and each quote and bracket kind has its own vector, the disclosed fail-closed "
          "over-fires are refused, and the disclosed residuals pass; and the text-coverage leg: a passage "
          "whose clause rows and id-history rows are removed, or retired, while its text stays fails, an "
          "exemption with no reason, a stale exemption (text gone, rule gone), and a redundant, repeated, "
          "ambiguous, cut or empty exemption fail, a malformed exemptions file and every unclassifiable "
          "or unreadable rule text is a cannot-evaluate (exit 2), and covered or exempted text passes; an "
          "absent exemptions file is a cannot-evaluate, so deleting it cannot hide a stale exemption; an "
          "underscore-only unit, uncovered or exempted, is a cannot-evaluate; an opener left open at the end "
          "of a paragraph that is not the last, or of a file with no final newline, is a cannot-evaluate; "
          "and wrapped prose lines that begin with '#', a number or '<' pass while each listed block start "
          "is refused; and the round-3 reproductions hold: a paragraph's first line that opens with a tag "
          "and ends with '>' (a quoted '<' or '>' included) and one that may open a link reference "
          "definition whose label spans lines are refused, a carriage return is a cannot-evaluate, a GFM "
          "table delimiter row with no leading pipe is refused, an exemptions file with no exempt key "
          "(empty, comment-only, a symlink to the null device) is a cannot-evaluate, and an explicit "
          "exempt = [] reads as no exemption; and the round-4 reproductions hold: a form feed and a carriage "
          "return, each a line ending to the vendored renderer, are a cannot-evaluate inside a line, at a "
          "line start, in the title and in the frontmatter, while a vertical tab, U+0085, U+2028 and U+2029 "
          "stay prose; an inline '<!' or '<?' is refused; a line the vendored GFM table reads as a delimiter "
          "row ('--x|--y') is refused; an ordered item in non-ASCII decimal digits is refused as a paragraph's "
          "first line; and each whitespace-widened block start has a no-break-space or vertical-tab vector "
          "the classifier itself refuses); an unavailable temp directory "
          "is a NOT RUN failure (nonzero exit), never a PASS (PARTIAL)"
          .format(core))
    return 0


# --- argument handling (hand-rolled to match check_version_monotonicity.py / check_portability.py) --

def _parse_args(argv):
    opts = {"root": None, "rules_dir": None, "inventory": None, "register": None, "prev_inventory": None,
            "genesis": False, "manifest": None, "with_manifest": False, "self_test": False,
            "exemptions": None}
    i = 0
    single = {"--root": "root", "--rules-dir": "rules_dir", "--inventory": "inventory",
              "--register": "register", "--prev-inventory": "prev_inventory", "--manifest": "manifest",
              "--exemptions": "exemptions"}
    while i < len(argv):
        arg = argv[i]
        if arg in single and i + 1 < len(argv):
            opts[single[arg]] = argv[i + 1]
            i += 2
        elif arg == "--genesis":
            opts["genesis"] = True
            i += 1
        elif arg == "--with-manifest":
            opts["with_manifest"] = True
            i += 1
        elif arg == "--self-test":
            opts["self_test"] = True
            i += 1
        else:
            print("usage: check_clauses.py [--root DIR] [--rules-dir DIR] [--inventory FILE] "
                  "[--register FILE] [--exemptions FILE] [--prev-inventory FILE] [--genesis] "
                  "[--with-manifest | --manifest FILE] | --self-test", file=sys.stderr)
            return None
    return opts


def main(argv=None):
    opts = _parse_args(sys.argv[1:] if argv is None else argv)
    if opts is None:
        return 2
    if opts["self_test"]:
        return self_test_main()
    root = precheck_special_files(Path(opts["root"]).resolve()) if opts["root"] else repo_root()
    rules_dir = Path(opts["rules_dir"]) if opts["rules_dir"] else root / ".aiqt" / "core" / "rules"
    inventory = Path(opts["inventory"]) if opts["inventory"] else root / ".aiqt" / "core" / "clauses.toml"
    register = Path(opts["register"]) if opts["register"] else root / ".aiqt" / "core" / "id-history.toml"
    prev = Path(opts["prev_inventory"]) if opts["prev_inventory"] else None
    manifest = None
    if opts["manifest"]:
        manifest = Path(opts["manifest"])
    elif opts["with_manifest"]:
        manifest = root / ".aiqt" / "manifest.toml"
    exemptions = Path(opts["exemptions"]) if opts["exemptions"] else root / EXEMPTIONS_REL
    return run(root, rules_dir, inventory, register, prev, opts["genesis"], manifest, exemptions)


if __name__ == "__main__":
    sys.exit(main())
