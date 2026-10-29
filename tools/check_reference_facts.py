#!/usr/bin/env python3
"""Reference-facts consistency gate: hand-authored copies of manifest-derived facts must agree with the
manifests. Stdlib only, offline.

It covers the fact copies that have NO generator to derive them, plus the closed-set parity that a
generator alone cannot assert:
  - the publisher-family parenthetical in the site/mappings meta descriptions and in the disclosure.toml
    standards-mappings claim (recomputed from the manifests' publishers through an explicit alias table);
  - the schema enum table in .aiqt/standards/README.md (the kind, status, and catalogue rows) against the
    loader's closed sets KINDS/STATUSES/CATALOGUES;
  - closed-set parity between _standards.KINDS/STATUSES and their programmatic copies (gen_mappings
    RELATION/RELATION_PROSE/STATUS_PILL/STATUS_DESC and the currency POLICY);
  - the deferred-frameworks negative check (no vendored framework is still listed "Not yet vendored"):
    every occurrence of the phrase in .aiqt/standards/README.md is read, matched case-insensitively with
    its words joined by any mix of whitespace, line wraps, hyphens, inline emphasis or strikethrough
    markers, or blockquote markers; the checked region is the WHOLE SECTION the phrase occurs in (from
    the nearest preceding ATX heading, or the top of the file, to the next heading of the same or a
    higher level), with headings found fence-aware (a '#' line inside an open ``` or ~~~ fence is
    content, not a heading), a heading ACCEPTED only in the grammar this reader implements (one to six
    '#' at up to three spaces of indent, then exactly U+0020 or the end of the line; a Unicode
    whitespace separator such as U+00A0 is refused; a '#' line with LEADING WHITESPACE is refused
    outright anywhere from the file start to the section end, because CommonMark can read an
    indented heading as nested in a list item, so a legal indented top-level heading is
    over-refused, a disclosed conservative refusal), a fence OPENED only where CommonMark opens one (a
    backtick fence whose info string holds a backtick is not an opener), a fence CLOSED only by a line
    holding nothing after the fence run but U+0020 or tab (never str.strip's Unicode whitespace), and
    an unterminated fence failing closed (exit 2, naming the file and the fence's opening line).
    STRUCTURE IS VALIDATED BEFORE A BOUNDARY MAY EXCLUDE TEXT, in three layers. FIRST, the whole text
    from its top to the section's end boundary is REFUSED outright (exit 2, naming the file and line)
    when any line outside a recognized line-start fence holds an arrangement this line reader cannot
    prove it reads: a run of three or more fence characters anywhere but a line-start fence, a list
    item opening with '<' (list-item HTML such as '- <pre>'), any other line starting with '<' unless
    it is a single-line HTML comment ('<!--' at up to three spaces of indent with its '-->' on the
    same line), or a line opening with '#' that is not a heading in the accepted grammar. SECOND, the
    deferred span itself (from the phrase's line to the section's end boundary) is read ONLY through
    a POSITIVE grammar on the raw lines: blank lines, plain column-0 paragraph lines, flat column-0
    list items at most four spaces after their marker whose text starts with none of '<', '>', '#',
    a fence character or another list marker, column-0 ATX headings, and indented continuations
    that directly continue a non-blank, non-heading line and are plain text by the same test; ANY
    other line there (any blockquote, any fence, any HTML-led or nested container, indented code or
    an indented line after a blank line or a heading, a list-held heading or code, or a line of
    only '-', '=', '_' or '*' runs, which CommonMark reads as a setext underline or a thematic
    break, never plain text) is cannot-evaluate (exit 2, naming the file and line), so the reader
    accepts only what the grammar proves it reads instead of enumerating the containers it cannot;
    no CommonMark container model is attempted. THIRD, ATX headings are computed TWICE over the
    whole text, from the blockquote-stripped lines (which bound the section) and from the raw
    lines, each with its own fence state, and ANY disagreement between the two readings from the
    top of the text to the section's end boundary (the boundary line included) is cannot-evaluate
    (exit 2, naming the first disagreeing line), so a blockquoted heading, a decoy heading
    manufactured by stripping, or a blockquoted fence inverting one reading's fence state is
    refused, never a silent boundary (the earlier revision's decided boundary, a blockquoted
    heading ending a section like a bare one, is refused now). Each manifest name is
    matched case-insensitively with whitespace runs collapsed, anywhere in that region; a table with
    pipe-free rows and an emphasis-split phrase sit inside the region by construction, at the
    disclosed cost of OVER-REFUSAL (a vendored name mentioned anywhere else in the same section is a
    finding too, a refused arrangement anywhere above the section exits 2 even when it could not
    have affected it, and a blockquoted or nested-list spelling of the phrase or a name, which
    earlier revisions read, is now refused rather than read). The PHRASE match is CLAIMED only for
    the enumerated joiners (whitespace, line wraps, hyphens, the inline marker characters *, _ and
    ~, and blockquote markers, the last only above the phrase line); the NAME match is claimed only
    for whitespace, line wraps and blockquote markers. A phrase or name interrupted by any other
    inline markup (a backtick code span, link brackets; for the NAME also hyphens or
    emphasis/strikethrough markers) is NOT matched -- the claim is narrowed to these joiners rather
    than the match extended (a disclosed residual, beside the entity-encoded one);
  - the subset-denominator invariant, checked structure-aware wherever the coverage of a curated-subset
    manifest is rendered: the registry row (name and coverage in one <tr>), the reverse view (name in a
    <summary>, coverage in a later <p> of the same <details>), AND the machine-readable JSON export (a
    subset framework must not carry an ids_total edition denominator). The gen_mappings branch is the
    primary control; this is defence in depth against a hand-edited page or export;
  - the landing-page counts (rules, gates, hooks with their blocking/warning split, frameworks, and
    the enforced-rules figure): the committed text is RENDERED by tools/gen_reference_facts.py (which
    calls this module's expected_counts, so the renderer and this gate share one set of predicates)
    and drift-gated there; this leg recomputes the same figures through that one shared function, so
    it is defence in depth against a hand edit of the rendered text (the gen_mappings/subset-denominator
    pattern: the generator branch is the primary control), never against a wrong predicate inside
    expected_counts itself; the figures are each
    recomputed from the rule corpus, the gates and hooks manifests
    (structure-validated, filtered to the platforms the page names: "ci" gates, "claude-code" hooks,
    and with the hook total checked against its blocking/warning split, so the page cannot print a
    split that does not sum to the total beside it), the standards manifests, and the enforcement
    register's summary, then compared with README.md's anchored wordings, each bound to its one
    designated line: the proof line is the FIRST line that opens bold with a digit, and the proof
    wording must open it, while a table-row wording is read on every line its anchor names. Every
    occurrence of a wording on a designated line is read with the whole numeric field captured, and
    every run of numeric characters on a designated line (every character unicodedata.numeric()
    gives a value: decimal digits in any script, superscripts, subscripts, circled and Roman numeral
    characters, vulgar fractions and CJK numerals, never ASCII alone) must lie inside an anchored
    wording match, so a second, contradictory, malformed or merely unexplained figure written with
    numeric characters on a designated line fails loud in any wording, whatever noun, spacing,
    markup or no-break space surrounds it (a missing anchored wording is itself a finding, so a
    reworded count fails loud, never silently unchecked);
  - the disclosure's shipped-path declaration (the [[code]]/[[noncode]] prefix rows of disclosure.toml,
    the same validated source gen_disclosure renders the public shipped-code sentence from): every
    path the release manifest (.aiqt/manifest.toml) lists must fall under a declared prefix (a
    prefix ending in '/' declares a tree, one without declares exactly that file, and the longest
    declared prefix classifies a path, case-sensitively), every declared prefix must classify a
    listed path (one whose every match loses to a longer prefix is reported as overshadowed, not as
    matching nothing), and an independent code detector backs the split: a path with a suffix on a
    closed executable list, an HTML page containing "<script" anywhere, case-insensitively, a
    comment included, or an extensionless file opening with a shebang line must classify under a
    [[code]] prefix (the detector reads a file only for a release path whose longest declared
    prefix is [[noncode]]; there an HTML page is decoded as UTF-8, a leading byte-order mark
    allowed, and a page holding a NUL byte or not decoding as UTF-8 is cannot-evaluate, exit 2,
    never non-code), so the public sentence cannot read
    complete while the release ships code the declaration places under a non-code prefix or does
    not place at all, and a declared row cannot outlive the files it names;
  - the public instruction-budget section: README.md and INSTRUCTION-BUDGET.md each state the
    committed ratchet of .aiqt/core/instruction-budget.toml as the most PACK the budget gate allows;
    the stated figure must equal that ratchet, and the ratchet must still exceed the 120,000 figure
    the note says the pack may exceed. The other figures the section states are bound to one
    authoritative value each, on each page: the 150,000 session target (_SESSION_TARGET), the
    pinned Claude Code build (claude-code-version in the budget source), and the loader's
    120,000-character check (_BUDGET_FLOOR).

BEST-EFFORT RESIDUAL (disclosed per the pack's own rule): this gate checks the specific fact copies
enumerated above; it is not a semantic-equivalence prover for arbitrary prose, and `catalogue = "full"` is
a reviewed declaration this gate trusts, not a fact it can prove offline (no full edition is vendored to
compare against). It fails closed on an unreadable/malformed input and on a new publisher family that has
no alias, so a stale copy cannot ship silently. A count stated outside the designated proof line and
table rows is not reached (the location binding is what keeps a true explanatory sentence elsewhere
from being read as a second operative count), and on a designated line the numeric sweep inspects
the page's source characters, not rendered figures, so a number written with characters that carry
no Unicode numeric value is not read: a number spelled out in words, a Roman numeral typed with
Latin letters, and a numeric-looking symbol (keycap ten U+1F51F, the hundred points symbol
U+1F4AF, the die faces U+2680 to U+2685, for example). The shipped-path declaration leg
accounts every release path to a declared prefix, and its code detector is a CLOSED list (the
executable suffixes it names, a UTF-8 HTML page containing "<script" case-insensitively anywhere,
an extensionless shebang file): executable content of another kind under
a non-code prefix is not detected, the HTML leg deliberately over-detects (a comment-only
"<script>" inside the page classifies it as code, a conservative reading, never an
under-detection), and a row's plain-language label is a reviewed description this
gate cannot semantically verify against the files under its prefix, all disclosed residuals. The
instruction-budget
note is pinned to the COMMITTED RATCHET, not to a fresh measurement, and the pages state only the
ratchet, never a measured PACK: the budget gate holds PACK at or under the ratchet and prints a note
whenever PACK falls below it, so a stale ratchet over a shrunken pack is surfaced there, not here.
The 150,000 target and the 120,000-character loader check are bound to this gate's own constants,
not to Claude Code itself: whether a Claude Code build applies that check is not verified here.

  check_reference_facts.py              exit 0 clean, 1 finding, 2 unreadable/missing input (fail-closed)
  check_reference_facts.py --self-test
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_reference_facts.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import json
import re
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402
from _standards import load_manifests, ManifestError, KINDS, STATUSES, CATALOGUES  # noqa: E402
from gen_mappings import RELATION, RELATION_PROSE, STATUS_PILL, STATUS_DESC  # noqa: E402
from check_standards_currency import POLICY  # noqa: E402
from gen_disclosure import shipped_declaration  # noqa: E402

# Publisher -> the short family token the public parentheticals use. Deliberately explicit: a NEW publisher
# family fails closed here (exit 2 via ManifestError) until an alias is chosen, so a family can never ship
# with the parentheticals silently stale.
FAMILY_ALIAS = {"OWASP Foundation": "OWASP", "NIST": "NIST", "MITRE": "MITRE ATLAS",
                "ISO/IEC": "ISO/IEC", "Cloud Security Alliance": "CSA"}

PAREN = re.compile(r"frameworks \(([^)]*)\)")
# The deferred phrase, its words joined by whitespace, line wraps, hyphens, or the inline emphasis or
# strikethrough markers (*, _, ~), so "Not *yet* vendored" and "Not-yet-vendored" both match.
DEFERRED = re.compile(r"not[\s*_~-]+yet[\s*_~-]+vendored", re.I)
# An ATX heading in the ONLY grammar this reader accepts: one to six '#' at up to three spaces of
# indent, then exactly one U+0020 or the end of the line. \s is deliberately NOT used: it admits
# U+00A0 and other Unicode whitespace, which CommonMark does not, so a '#' line with such a
# separator would read as a false heading and truncate the section.
HEADING = re.compile(r"^ {0,3}(#{1,6})(?: |$)")
# A fenced-code-block fence (CommonMark: three or more backticks or tildes, up to 3 spaces of indent).
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# Any run of three or more fence characters, wherever it sits on the line: used to REFUSE a fence this
# line reader cannot bound (a list-item, indented or inline fence), never to open one.
FENCE_RUN = re.compile(r"`{3,}|~{3,}")
# A single-line HTML comment ('<!--' at up to three spaces of indent; the caller also requires its
# '-->' on the same line): CommonMark ends a type-2 HTML block on the line holding '-->', so this
# line is provably closed and cannot swallow a later heading. It is the ONLY '<' line the structure
# scan accepts (the real standards README carries its generated roster markers in this exact shape).
HTML_COMMENT_LINE = re.compile(r"^ {0,3}<!--")
# A list item whose content opens with '<' (list-item HTML such as '- <pre>'): the HTML block it
# opens is container-relative, so this line reader cannot bound it; refused.
LIST_HTML = re.compile(r"^ {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+<")
BLOCKQUOTE = re.compile(r"^ {0,3}> ?")
N_OF_M = re.compile(r"\b\d+ of \d+\b")
# The POSITIVE deferred-section grammar (applied by _require_section_grammar to the RAW lines from
# the deferred phrase to the section's end boundary): a column-0 ATX heading, a flat column-0 list
# item (its text must then not open another container or a '#' lead, and sit at most four spaces
# after the marker: five or more is indented code in CommonMark, and a TAB gap is refused too, a
# conservative over-refusal of a shape CommonMark can read as a plain item), the list-marker lead
# that text may not carry, and the setext-underline / thematic-break shape (a line of only '-',
# '=', '_' or '*' runs and blanks) that no span line may take, because CommonMark reads most
# such lines as a heading underline or a thematic break (a short '_', '=' or '*' run elsewhere
# is plain text and is over-refused, a conservative refusal), never as the plain paragraph text
# this reader would prove it reads.
COL0_HEADING = re.compile(r"^#{1,6}(?: |$)")
FLAT_LIST_ITEM = re.compile(r"^(?:[-*+]|\d{1,9}[.)])(?P<gap>[ \t]+)(?=\S)")
LIST_MARKER_LEAD = re.compile(r"^(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")
SETEXT_OR_BREAK = re.compile(r"^[ \t]*[-=_*][-=_* \t]*$")


def _fence_open(line):
    """The fence-OPENER test shared by _heading_levels and _refuse_unbounded: FENCE at up to three
    spaces of indent, REFINED per CommonMark for backtick fences, whose info string may not contain
    a backtick (a line such as ```x`y is NOT an opener). Without this rule the reader's fence
    state inverts against CommonMark's wherever such a line occurs and a '#' line the renderer shows
    as content reads as a heading (or the reverse); with it, such a line falls through to the
    FENCE_RUN refusal (a fence run that opens nothing), never to an inverted state. A tilde fence's
    info string may contain anything, exactly CommonMark."""
    fm = FENCE.match(line)
    if fm and fm.group(1)[0] == "`" and "`" in line[fm.end():]:
        return None
    return fm


def _fence_close(line, fence_char, fence_len):
    """The fence-CLOSE test shared by the same two scans: at least fence_len of the same fence
    character at up to three spaces of indent, then NOTHING but U+0020 or tab. CommonMark allows
    only spaces and tabs after a closing fence; str.strip() would also remove U+00A0 and other
    Unicode whitespace and so read a line CommonMark keeps as content as a close, truncating the
    section through a still-open fence."""
    fm = FENCE.match(line)
    return bool(fm and fm.group(1)[0] == fence_char and len(fm.group(1)) >= fence_len
                and not line[fm.end():].strip(" \t"))


def _read(path):
    """Fail-closed read: a surface this gate is contracted to check that is missing or unreadable is an
    error (exit 2 via OSError), never a silent skip."""
    if not path.is_file():
        raise OSError("required surface missing: {}".format(path))
    return path.read_text(encoding="utf-8")


def expected_families(manifests):
    fams = set()
    for m in manifests.values():
        alias = FAMILY_ALIAS.get(m.publisher)
        if alias is None:
            raise ManifestError("publisher {!r} has no family alias in check_reference_facts; add one and "
                                "update the public parentheticals".format(m.publisher))
        fams.add(alias)
    return fams


def check_parenthetical(text, where, fams, findings):
    hits = PAREN.findall(text)
    if not hits:
        findings.append("{}: no 'frameworks (...)' parenthetical found".format(where))
        return
    for hit in hits:
        got = {tok.strip() for tok in hit.split(",") if tok.strip()}
        if got != fams:
            findings.append("{}: parenthetical {{{}}} != publisher families {{{}}}".format(
                where, ", ".join(sorted(got)), ", ".join(sorted(fams))))


def check_meta(root, fams, findings):
    text = _read(root / "site" / "mappings.html")
    metas = [ln for ln in text.splitlines()
             if 'name="description"' in ln or 'property="og:description"' in ln]
    if len(metas) != 2:
        findings.append("site/mappings.html: expected 2 description metas, found {}".format(len(metas)))
    for ln in metas:
        check_parenthetical(ln, "site/mappings.html meta", fams, findings)


def check_disclosure(root, fams, findings):
    data = load_toml(root / "disclosure.toml")
    rows = [r for r in data.get("row", []) if r.get("id") == "standards-mappings"]
    if len(rows) != 1:
        findings.append("disclosure.toml: expected one standards-mappings row, found {}".format(len(rows)))
        return
    check_parenthetical(rows[0].get("claim", ""), "disclosure.toml standards-mappings claim", fams,
                        findings)


def check_enum_table(root, findings):
    text = _read(root / ".aiqt" / "standards" / "README.md")
    for field, expect in (("kind", KINDS), ("status", STATUSES), ("catalogue", CATALOGUES)):
        row = re.search(r"^\| `{}` \| (.+) \|$".format(field), text, re.M)
        if not row:
            findings.append("standards README: no schema row for `{}`".format(field))
            continue
        got = set(re.findall(r"`([a-z-]+)`", row.group(1)))
        if got != expect:
            findings.append("standards README: `{}` row lists {{{}}}, loader allows {{{}}}".format(
                field, ", ".join(sorted(got)), ", ".join(sorted(expect))))


def check_parity(findings):
    """Assert the programmatic closed-set copies still equal the loader's KINDS/STATUSES. The dicts are
    imported live, so a stale or superset key is an explicit finding rather than a dormant desync (a
    missing key already fails loud at render, a superset key is what this catches)."""
    for name, got, expect in (("gen_mappings.RELATION", set(RELATION), KINDS),
                              ("gen_mappings.RELATION_PROSE", set(RELATION_PROSE), KINDS),
                              ("gen_mappings.STATUS_PILL", set(STATUS_PILL), STATUSES),
                              ("gen_mappings.STATUS_DESC", set(STATUS_DESC), STATUSES),
                              ("check_standards_currency.POLICY", set(POLICY), STATUSES)):
        if got != expect:
            findings.append("{} keys {{{}}} != {{{}}}".format(
                name, ", ".join(sorted(got)), ", ".join(sorted(expect))))


def _unquoted_lines(text):
    """The text split into lines with blockquote markers stripped per line (repeatedly, so a nested
    quote unwraps too). The line count is unchanged, so a match's line number maps one to one; stripping
    lets the phrase and a name match across a quoted line wrap, which the raw text never can."""
    out = []
    for line in text.split("\n"):
        while BLOCKQUOTE.match(line):
            line = BLOCKQUOTE.sub("", line, count=1)
        out.append(line)
    return out


def _heading_levels(lines, where):
    """{line_index: level} for every ATX heading, FENCE-AWARE: a '#' line inside an open ``` or ~~~
    fenced code block is content, not a heading (so a fenced example cannot end a section early). A
    fence opens only on a line _fence_open accepts (a backtick fence whose info string holds a
    backtick is not an opener, exactly CommonMark) and closes only on a line holding at least as
    many of the same fence character and nothing else but spaces or tabs (_fence_close, never
    str.strip's Unicode whitespace); a fence still open at the end of the text means the section
    reader cannot bound a section through it, so this raises OSError naming the file and the
    fence's opening line (exit 2 via run(), fail-closed, never a partial read)."""
    headings = {}
    fence_char, fence_len, fence_line = None, 0, 0
    for k, line in enumerate(lines):
        if fence_char is not None:
            if _fence_close(line, fence_char, fence_len):
                fence_char = None
            continue
        fm = _fence_open(line)
        if fm:
            fence_char, fence_len, fence_line = fm.group(1)[0], len(fm.group(1)), k + 1
            continue
        hm = HEADING.match(line)
        if hm:
            headings[k] = len(hm.group(1))
    if fence_char is not None:
        raise OSError("{}: unterminated code fence opened at line {}; cannot bound the section "
                      "(fail-closed)".format(where, fence_line))
    return headings


def _refuse_unbounded(lines, end, where):
    """Refuse (OSError -> exit 2, naming the file and line) any arrangement, FROM THE TOP OF THE TEXT
    to the section's end boundary, that this line reader cannot prove it reads, instead of modelling
    CommonMark containers. Scanning from the top -- never only from the section's first line --
    validates the structure BEFORE a boundary may exclude text: a container opened above the section
    (a list fence, an HTML block, list-item HTML) can turn the very line chosen as the section's start
    or end into a false heading, and the opener always precedes the false heading it manufactures, so
    it sits in [0, end) and is caught here. Refused, outside recognized line-start fences: a run of
    three or more fence characters anywhere but a line-start fence (a list-item, indented or inline
    fence can open a container-relative code block whose '#' content reads as a false heading; a
    backtick fence whose info string holds a backtick, which CommonMark does not open, lands here
    too); a list item opening with '<' (list-item HTML such as '- <pre>'); any other line starting
    with '<' (an HTML block can hold '#' lines that are content, not headings) UNLESS it is a
    single-line HTML comment ('<!--' at up to three spaces of indent with its '-->' on the same line,
    which CommonMark closes at that line, so it cannot swallow a later heading); and any line opening
    with '#' that is not a heading in the accepted grammar (HEADING: one to six '#' at up to three
    spaces of indent, then exactly U+0020 or the line end). A '#'-led line WITH LEADING
    WHITESPACE is refused outright at every position OUTSIDE a recognized line-start fence,
    from the file start to the section end (the
    boundary line INCLUDED: an indented boundary heading can itself be list-nested, so it is
    refused before it may bound the section): CommonMark can read an indented
    heading as nested in a list item (`- x` then `  ###### note`), which this reader does not
    model, so the LEGAL indented top-level heading (up to three spaces, no list above) is
    over-refused too, a disclosed conservative refusal. Lines inside a recognized line-start
    fence are bounded content and exempt; the fence state is recomputed from the top of the text with
    the same _fence_open/_fence_close rules as _heading_levels, so the two scans can never disagree.
    DELIBERATE OVER-REFUSAL: a harmless list fence, autolink or '<div>' anywhere above the boundary
    exits 2 too; the alternative is a silent truncation or a silently mis-started section."""
    fence_char, fence_len = None, 0
    for k in range(min(end + 1, len(lines))):
        line = lines[k]
        if fence_char is not None:
            if _fence_close(line, fence_char, fence_len):
                fence_char = None
            continue
        if line[:1] in (" ", "\t") and line.lstrip(" \t").startswith("#"):
            raise OSError("{}:{}: a '#' line with leading whitespace (CommonMark can read it as a "
                          "heading nested in a list item, which this reader does not model; a "
                          "LEGAL indented top-level heading is over-refused by the same rule, a "
                          "disclosed conservative refusal); the section reader cannot bound the "
                          "section through it (fail-closed)".format(where, k + 1))
        if k >= end:
            # The boundary line itself (a heading by construction when end < len(lines)) takes
            # ONLY the indented-'#' test above: an indented boundary heading can be nested in a
            # list item, so it is refused BEFORE it may bound the section; the other refusals
            # keep their [0, end) span.
            break
        fm = _fence_open(line)
        if fm:
            fence_char, fence_len = fm.group(1)[0], len(fm.group(1))
            continue
        if FENCE_RUN.search(line):
            raise OSError("{}:{}: a code fence not at line start (a list-item, indented or inline "
                          "fence, or a backtick fence with a backtick in its info string); the "
                          "section reader cannot bound the section through it "
                          "(fail-closed)".format(where, k + 1))
        if LIST_HTML.match(line):
            raise OSError("{}:{}: a list item opening with '<' (list-item HTML this reader does not "
                          "model); the section reader cannot bound the section through it "
                          "(fail-closed)".format(where, k + 1))
        stripped = line.lstrip()
        if stripped.startswith("<") and not (HTML_COMMENT_LINE.match(line) and "-->" in line):
            raise OSError("{}:{}: a line starting with '<' that is not a single-line HTML comment "
                          "(an HTML block this reader does not model); the section reader cannot "
                          "bound the section through it (fail-closed)".format(where, k + 1))
        if stripped.startswith("#") and not HEADING.match(line):
            raise OSError("{}:{}: a '#' line that is not a heading in the accepted grammar (one to "
                          "six '#' at up to three spaces of indent, then exactly U+0020 or the line "
                          "end); the section reader cannot bound the section through it "
                          "(fail-closed)".format(where, k + 1))


def _require_section_grammar(raw_lines, start, end, where):
    """The POSITIVE grammar for the deferred span itself, applied to the RAW lines (blockquote
    markers NOT stripped) from the deferred phrase's line to the section's end boundary. A line is
    accepted ONLY as one of: a blank line (empty, or spaces and tabs only); a column-0 ATX heading
    (one to six '#', then exactly U+0020 or the line end); a flat column-0 list item at most four
    spaces after its marker (five or more is indented code in CommonMark; a tab gap is refused
    too, a conservative over-refusal of a shape CommonMark can read as a plain item) whose text
    starts with none of '<', '>', '#', a backtick or a tilde and is not another list marker; a
    plain column-0 paragraph line (first character none of '<', '>', '#', a backtick, a tilde, and
    not a list marker); or an indented continuation that DIRECTLY CONTINUES a non-blank,
    non-heading line and whose first non-blank character passes the same plain-text test (an
    indented line after a blank line or a heading is indented code or a new container in
    CommonMark, not a continuation; at ONE to three spaces after a blank line or a heading
    CommonMark reads a plain paragraph, which is refused too, a conservative over-refusal). A
    line of only '-', '=', '_' or '*' runs and blanks is
    refused at any position: CommonMark reads most such lines as a setext heading underline or a
    thematic break, so the phrase's own paragraph could become a heading whose section this
    reader does not model (the '_', '=' and '*' runs CommonMark keeps as plain text are refused
    too, a conservative over-refusal). EVERY other line -- any blockquote, any fence, any HTML-led line, any nested or
    indented container, any list-held heading or code, any indented continuation that is not plain
    text -- is REFUSED (OSError -> exit 2, naming the file and line): between the phrase and the
    boundary the reader accepts only what this grammar proves it reads as flat text, instead of
    enumerating the containers it cannot."""
    for k in range(start, end):
        line = raw_lines[k]
        if not line.strip(" \t"):
            continue
        if SETEXT_OR_BREAK.match(line):
            raise OSError("{}:{}: a line of only '-', '=', '_' or '*' runs (a setext heading "
                          "underline or a thematic break in CommonMark for most such lines; the "
                          "'_', '=' and '*' runs CommonMark keeps as plain text are refused "
                          "too, a conservative over-refusal); the "
                          "section reader cannot prove it reads this line "
                          "(fail-closed)".format(where, k + 1))
        if line[:1] not in (" ", "\t"):
            if COL0_HEADING.match(line):
                continue
            lm = FLAT_LIST_ITEM.match(line)
            if lm:
                gap = lm.group("gap")
                text = line[lm.end():]
                if ("\t" not in gap and len(gap) <= 4
                        and text[:1] not in ("<", ">", "#", "`", "~")
                        and not LIST_MARKER_LEAD.match(text)):
                    continue
            elif line[:1] not in ("<", ">", "#", "`", "~") and not LIST_MARKER_LEAD.match(line):
                continue
        else:
            prev = raw_lines[k - 1] if k > 0 else ""
            text = line.lstrip(" \t")
            if (prev.strip(" \t") and not COL0_HEADING.match(prev)
                    and text[:1] not in ("<", ">", "#", "`", "~")
                    and not LIST_MARKER_LEAD.match(text)):
                continue
        raise OSError("{}:{}: a line outside the deferred-section grammar (between the phrase and "
                      "the section end only blank lines, plain paragraph or continuation text, flat "
                      "plain list items, and column-0 ATX headings are read; a blockquote, a fence, "
                      "an HTML-led line, a nested or indented container, indented code, a list-held "
                      "heading, or an indented line that does not directly continue a text line is "
                      "not; a TAB list-item gap and a one- to three-space-indented paragraph "
                      "line, which CommonMark reads as plain text, are refused too, conservative "
                      "over-refusals); the section reader cannot prove it reads this line "
                      "(fail-closed)".format(where, k + 1))


def deferred_sections(text, where):
    """Yield (line_number, section) for every occurrence of the deferred phrase (case-insensitive, its
    words joined by any run of whitespace, line wraps, hyphens, inline emphasis/strikethrough markers
    or blockquote markers; blockquote markers are stripped per line before matching). The section is
    the WHOLE region from the nearest preceding ATX heading (or the top of the text) to the next
    heading of the same or a higher level (or any heading, for the pre-heading preamble), headings
    found fence-aware by _heading_levels. The structure is validated BEFORE a boundary may exclude
    text, in three layers. FIRST, the WHOLE TEXT from its top to the section's end boundary passes
    _refuse_unbounded: an arrangement the line reader cannot prove it reads (a fence run not at line
    start, list-item HTML, a line starting with '<' other than a single-line HTML comment, a '#' line
    that is not a heading in the accepted grammar, a '#' line with leading whitespace: an indented
    heading CommonMark can nest in a list item, its legal top-level form over-refused) is REFUSED (OSError -> exit 2, naming the file and
    line) wherever it sits in that span. SECOND, the deferred span itself (from the phrase's line to
    the end boundary) must pass the POSITIVE grammar of _require_section_grammar on the RAW lines:
    only blank lines, plain paragraph or continuation text (a continuation only directly under a
    non-blank, non-heading line), flat plain list items (at most four spaces after the marker, no
    '#' lead) and column-0 ATX headings are read there, and any blockquote, fence, HTML-led or
    nested container, indented code, list-held heading or code, setext underline or thematic break
    is refused rather than read. THIRD, ATX headings are computed TWICE over the whole text, from
    the blockquote-stripped lines (which bound the section) and from the RAW lines, each with its
    own fence state by the same _fence_open/_fence_close rules, and ANY disagreement between the
    two readings from the top of the text to the section's end boundary (the boundary line
    included) is REFUSED (OSError -> exit 2, naming the first disagreeing line): a blockquoted ATX
    heading ('> # Aside'), a decoy heading a stripped quote marker manufactures, and a blockquoted
    fence that inverts one reading's fence state each read as a heading in exactly one of the two
    readings, so the earlier revision's DECIDED boundary (a blockquoted heading silently ending a
    section like a bare one) is now cannot-evaluate instead of read; agreement of the two readings
    is a necessary condition, not a proof of CommonMark equivalence (a divergence affecting only
    lines beyond the section end is not examined). The DISCLOSED COST stays over-refusal: a
    vendored name mentioned anywhere else in the same section is read too, a refused arrangement
    above the section exits 2 even when it could not have affected it, and a blockquoted or
    nested-list spelling of the phrase or a name, which earlier revisions read, is refused."""
    lines = _unquoted_lines(text)
    raw_lines = text.split("\n")
    headings = _heading_levels(lines, where)
    headings_raw = _heading_levels(raw_lines, where)
    joined = "\n".join(lines)
    starts, pos = [], 0
    for line in lines:
        starts.append(pos)
        pos += len(line) + 1
    for m in DEFERRED.finditer(joined):
        i = max(k for k, start in enumerate(starts) if start <= m.start())
        preceding = [k for k in headings if k <= i]
        if preceding:
            first = max(preceding)
            level = headings[first]
            end = min((k for k in headings if k > i and headings[k] <= level), default=len(lines))
        else:
            first = 0
            end = min((k for k in headings if k > i), default=len(lines))
        _refuse_unbounded(lines, end, where)
        _require_section_grammar(raw_lines, i, end, where)
        for k in range(min(end + 1, len(lines))):
            if headings.get(k) != headings_raw.get(k):
                raise OSError("{}:{}: the raw and blockquote-stripped heading readings disagree (a "
                              "heading in one reading is absent or at another level in the other: "
                              "a blockquoted heading, a decoy heading a stripped quote marker "
                              "manufactures, or a blockquoted fence inverting one reading's fence "
                              "state); the section reader cannot bound the section "
                              "(fail-closed)".format(where, k + 1))
        yield i + 1, "\n".join(lines[first:end])


def _folded(s):
    return " ".join(s.split()).lower()


def check_deferred(root, manifests, findings):
    text = _read(root / ".aiqt" / "standards" / "README.md")
    for lineno, block in deferred_sections(text, "standards README"):
        folded = _folded(block)
        for man in manifests.values():
            if _folded(man.name) in folded:
                findings.append("standards README:{}: vendored framework {!r} still listed as not yet "
                                "vendored".format(lineno, man.name))


def check_subset_denominator(root, manifests, findings):
    """Defence in depth for the catalogue invariant: a subset manifest must never appear with an 'N of M'
    edition denominator anywhere it is rendered (the gen_mappings branch is the primary control; this
    catches a hand-edited page).

    Structure-aware, not same-line: the two render sites place the name and the coverage in different
    places. A registry row keeps both in one <tr>, but the reverse view renders the name in a <summary>
    and the coverage in a later <p> of the same <details>, so a same-line scan would miss a subset "N of
    M" there. The check splits the page into <tr> blocks and <details> blocks and associates a subset name
    with a denominator across the block, never by line proximity."""
    text = _read(root / "site" / "mappings.html")
    subset_names = {man.name for man in manifests.values() if man.catalogue == "subset"}
    # Registry rows: the name cell and the coverage cell live in one <tr>. A subset row must read
    # "N referenced (curated subset)", never "N of M".
    for row in re.findall(r"<tr\b.*?</tr>", text, re.S | re.I):
        if not N_OF_M.search(row):
            continue
        for name in subset_names:
            if name in row:
                findings.append("site/mappings.html: subset manifest {!r} rendered with an edition "
                                "denominator in a registry row".format(name))
    # Reverse view: the framework name is in the <summary>; its coverage is in a later <p> of the same
    # <details>. Match on the summary so a forward-view block (a rule-title summary) is not caught, then
    # scan the whole block so the name and the denominator are associated across the line break.
    for block in re.findall(r"<details\b.*?</details>", text, re.S | re.I):
        summ = re.search(r"<summary>(.*?)</summary>", block, re.S | re.I)
        if not summ:
            continue
        head = summ.group(1)
        for name in subset_names:
            if name in head and N_OF_M.search(block):
                findings.append("site/mappings.html: subset manifest {!r} rendered with an edition "
                                "denominator in the reverse view".format(name))


def check_json_subset_denominator(root, manifests, findings):
    """The catalogue invariant in the machine-readable export: a curated-subset framework in
    site/downloads/mappings.json must carry ids_total: null (the KEY present for schema compatibility,
    value null = no computable edition denominator); only a full-edition manifest carries a numeric
    ids_total. Fail-closed read (exit 2) on a missing/unreadable/invalid export or a non-object entry; a
    subset framework absent from the export, missing ids_total, or carrying a non-null ids_total is a
    finding (exit 1)."""
    text = _read(root / "site" / "downloads" / "mappings.json")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise OSError("site/downloads/mappings.json is not valid JSON: {}".format(exc))
    frameworks = data.get("frameworks") if isinstance(data, dict) else None
    if not isinstance(frameworks, dict):
        raise OSError("site/downloads/mappings.json: expected a top-level 'frameworks' object")
    for man in manifests.values():
        if man.catalogue != "subset":
            continue
        stem = man.map_key[4:]  # strip the "map-" prefix; the JSON keys frameworks by stem
        entry = frameworks.get(stem)
        if entry is None:
            findings.append("site/downloads/mappings.json: subset framework {!r} ({}) missing from "
                            "frameworks".format(man.name, stem))
            continue
        if not isinstance(entry, dict):
            raise OSError("site/downloads/mappings.json: framework {!r} entry is not an object "
                          "(malformed export)".format(stem))
        if "ids_total" not in entry:
            findings.append("site/downloads/mappings.json: subset framework {!r} is missing ids_total; a "
                            "subset must carry ids_total: null".format(man.name))
        elif entry["ids_total"] is not None:
            findings.append("site/downloads/mappings.json: subset framework {!r} carries a non-null edition "
                            "denominator (ids_total={!r}); a subset must be null".format(
                                man.name, entry["ids_total"]))


# The whole numeric field at a count position: the lookbehind rejects a figure entered mid-number or
# behind a sign or decimal point ("1,132", "-35", "3.5"), so a partial match can never satisfy a count;
# the wording then fails to match at its designated line, which is itself a finding. The lookbehind is
# the operative guard (the leading-sign vector at the rules row in self-test case 19 is caught by it
# alone); a trailing-context guard would be redundant and carries none: every wording follows the
# captured field with a literal space, so a successful match can never end inside a longer number.
_NUM = r"(?<![0-9,.+-])([0-9]+)"

# The landing page's anchored count wordings (label, line anchor, anchor mode, wording, expected-count
# keys). Each wording is REQUIRED and BOUND to its designated line of README.md. Mode "proof": the
# designated line is the FIRST line the anchor matches (the first line opening bold with a digit), and
# the wording must match exactly at the anchor's end, so a replaced proof line with its wording moved
# to a later bold numeric line is a missing-wording finding, never a silent relocation. Mode "row": the
# wording is read on every line the anchor names (the named cell opening its table row). On a
# designated line, every occurrence of the wording is read (finditer, bounded to the line) and the
# wording is searched nowhere else, and the numeric sweep below requires every numeric run on the
# line to sit inside an anchored wording match, so a stale count, a silently reworded count sentence,
# a wording split across a line break, and a second contradictory figure on the designated line all
# fail loud,
# while a true explanatory count sentence on any other line is never read as an operative count (a
# count stated outside the designated lines is not reached; disclosed in the module docstring).
# Neither an anchor nor a wording can span a line break: the wording search is bounded to the anchored
# line, and the wordings hold only literals that match no line ending.
README_COUNT_FACTS = (
    ("proof line",
     r"(?m)^\*\*(?=[0-9])",
     "proof",
     _NUM + r" rules\. " + _NUM + r" CI gates that check this repository\. " + _NUM +
     r" Claude Code hooks shipped as an opt-in plugin\. Mapped to " + _NUM + r" frameworks\.\*\*",
     ("rules", "gates", "hooks", "frameworks")),
    ("rules row",
     r"(?m)^\| \*\*The standard\*\* \| ",
     "row",
     _NUM + r" clear, single-behaviour rules\b",
     ("rules",)),
    ("controls row",
     r"(?m)^\| \*\*Technical controls\*\* \| ",
     "row",
     _NUM + r" CI gates that check this repository and " + _NUM +
     r" Claude Code hooks shipped as an opt-in plugin \(" + _NUM + r" blocking, " + _NUM + r" warning\)",
     ("gates", "hooks", "blocking", "warning")),
    ("enforcement figure",
     r"(?m)^\| \*\*Technical controls\*\* \| ",
     "row",
     r"\bToday " + _NUM + r" of the " + _NUM +
     r" rules are linked to at least one shipped gate or hook\b",
     ("enforced", "rules")),
    ("crosswalk row",
     r"(?m)^\| \*\*A standards crosswalk\*\* \| ",
     "row",
     r"\bto " + _NUM + r" pinned external control catalogues\b",
     ("frameworks",)),
)


# Every numeric character on a designated line, however worded: each maximal run of characters
# that unicodedata.numeric() gives a value must sit INSIDE one of the anchored wording matches read
# on that line (whose captured figures are checked against the recomputed counts), so a second,
# contradictory, malformed or merely unexplained figure on a designated line is a finding in any
# wording written with numeric characters ('(a draft said 999 rules)', '(999 rule.)', '(1e3 rules)',
# a no-break space, bold markup, or any noun the page might gain): there is no noun list to evade.
# The numeric class is the whole Unicode numeric property, never ASCII alone and never the decimal
# digits alone: decimal digits in every script (fullwidth and Arabic-Indic included), superscript
# and subscript digits, circled, parenthesized and dingbat numerals, Roman numeral characters,
# vulgar fractions, and CJK numerals. Lines other than the designated ones are not scanned (the
# disclosed location binding). The sweep inspects the page's source characters, not rendered
# figures, so the residual on a designated line is a number written with characters that carry no
# Unicode numeric value: a number spelled out in words ("nine hundred"), a Roman numeral typed
# with Latin letters ("IX"), or a numeric-looking symbol (keycap ten U+1F51F, the hundred points
# symbol U+1F4AF, the die faces U+2680 to U+2685, for example) is not read (disclosed in the
# module docstring; self-test case 20f pins each named symbol as not read).
def _numeric_runs(text, start, end):
    """Yield (run_start, run_end) for every maximal run of numeric characters (each one
    unicodedata.numeric() gives a value) in text[start:end]."""
    pos = start
    while pos < end:
        if unicodedata.numeric(text[pos], None) is None:
            pos += 1
            continue
        run_start = pos
        while pos < end and unicodedata.numeric(text[pos], None) is not None:
            pos += 1
        yield run_start, pos


def _manifest_entries(path, key, fields):
    """The [[key]] entries of the TOML manifest at `path`, validated BEFORE anything is counted: the
    value must be an array of tables, and every entry must carry each counted field as a string, so a
    malformed manifest (`gate = "ab"`, a non-table entry, a missing platform or default) raises OSError
    (exit 2 via run's fail-closed handler), never a length that happens to look right."""
    entries = load_toml(path).get(key)
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise OSError("{}: [[{}]] must be an array of tables".format(path, key))
    for pos, entry in enumerate(entries, 1):
        for field in fields:
            if not isinstance(entry.get(field), str):
                raise OSError("{}: [[{}]] entry {} has no string {!r}".format(path, key, pos, field))
    return entries


# The enforcement register's summary rows whose rules are linked to at least one SHIPPED gate or hook
# (the page's "Today N of the M rules are linked to at least one shipped gate or hook"): the gate-linked
# and hook-linked display labels gen_enforcement_register renders, each with and without its ", partial"
# suffix. Preview only (every linked control is a preview-channel hook an adopter installs by hand, not
# part of the plugin), Pending and No control are not counted. Every one of these rows is REQUIRED: a
# renamed or missing row fails closed (exit 2), never a silently smaller sum.
_LINKED_SUMMARY_LABELS = tuple(label + suffix
                               for label in ("Gate-linked (class a)", "Gate-linked (class c only)",
                                             "Hook-linked")
                               for suffix in ("", ", partial"))


def expected_counts(root, manifests):
    """Recompute the landing page's counts from the surfaces that define them, fail-closed on a missing or
    malformed one: rules = the .md files directly under .aiqt/core/rules/ (the corpus layout gen_rules
    validates), gates = the [[gate]] entries of .aiqt/core/gates/manifest.toml whose platform is "ci"
    (the page's predicate: CI gates), hooks (with the blocking/warning split from `default`) = the
    [[hook]] entries of .aiqt/core/hooks/manifest.toml whose platform is "claude-code", frameworks = the
    loaded standards manifests, and enforced = the sum of the enforcement register's linked summary rows
    (_LINKED_SUMMARY_LABELS; ENFORCEMENT.md is generated and drift-gated against its sources by
    gen_enforcement_register). Both manifests are structure-validated by _manifest_entries before
    anything is counted, and an entry of another platform is excluded from the count, never silently
    counted toward the page's claim."""
    rules_dir = root / ".aiqt" / "core" / "rules"
    if not rules_dir.is_dir():
        raise OSError("required surface missing: {}".format(rules_dir))
    rules = sum(1 for p in rules_dir.iterdir() if p.is_file() and p.suffix == ".md")
    gates = [g for g in _manifest_entries(root / ".aiqt" / "core" / "gates" / "manifest.toml",
                                          "gate", ("platform",)) if g["platform"] == "ci"]
    hooks = [h for h in _manifest_entries(root / ".aiqt" / "core" / "hooks" / "manifest.toml",
                                          "hook", ("platform", "default"))
             if h["platform"] == "claude-code"]
    if not rules or not gates or not hooks:
        raise OSError("no rule files, no ci gates, or no claude-code hooks")
    blocking = sum(1 for h in hooks if h["default"] == "block")
    warning = sum(1 for h in hooks if h["default"] == "warn")
    if blocking + warning != len(hooks):
        raise OSError("hooks manifest: {} claude-code hook(s) carry a default other than block or warn, "
                      "so the page's blocking/warning split cannot sum to the hook total it prints "
                      "beside it".format(len(hooks) - blocking - warning))
    register = _read(root / "ENFORCEMENT.md")
    enforced = 0
    for label in _LINKED_SUMMARY_LABELS:
        row = re.search(r"(?m)^\| " + re.escape(label) + r" \| (\d+) \|", register)
        if not row:
            raise OSError("ENFORCEMENT.md: no '| {} | N |' summary row".format(label))
        enforced += int(row.group(1))
    return {"rules": rules, "gates": len(gates), "hooks": len(hooks),
            "blocking": blocking, "warning": warning,
            "frameworks": len(manifests), "enforced": enforced}


def check_readme_counts(root, manifests, findings):
    """The landing page's count claims against the manifests they summarize. The committed counts are
    rendered between managed markers by tools/gen_reference_facts.py from this module's own
    expected_counts, and that generator's --check is the primary drift control; this leg stays as
    defence in depth (a hand edit of the rendered text, a figure stated outside the generated blocks
    on a designated line, and a wording desync between the renderer's templates and
    README_COUNT_FACTS each still fail loud here). Anchored, not fuzzy: each
    README_COUNT_FACTS wording must be present ON ITS DESIGNATED LINE (mode "proof": the first line its
    anchor matches, with the wording opening the line at the anchor's end; mode "row": every line its
    anchor names), every occurrence of the wording on a designated line is read (finditer bounded to
    the line), and the wording is searched nowhere else, so a true explanatory count sentence on
    another line is never read as an operative count. Every designated line is then swept with
    _numeric_runs: a run of numeric characters (every character unicodedata.numeric() gives a value,
    decimal digits in any script, superscripts, subscripts, circled and Roman numeral characters,
    vulgar fractions and CJK numerals included) that sits outside every anchored wording match on
    its line is a finding, whatever noun, spacing, markup or no-break space surrounds it, so a
    second, contradictory, malformed or merely unexplained figure written with numeric characters
    cannot ship on a designated line in any wording. The sweep inspects source characters, not
    rendered figures, so the residual is a number written with characters that carry no Unicode
    numeric value: a number spelled out in words, a Roman numeral typed with Latin letters, or a
    numeric-looking symbol (keycap ten, the hundred points symbol, a die face, for example) is not
    read."""
    exp = expected_counts(root, manifests)
    text = _read(root / "README.md")
    designated = {}
    for label, anchor, mode, wording, keys in README_COUNT_FACTS:
        want = tuple(str(exp[k]) for k in keys)
        wording_re = re.compile(wording)
        found = False
        starts = list(re.finditer(anchor, text))
        if mode == "proof":
            starts = starts[:1]
        for start in starts:
            end = text.find("\n", start.end())
            end = len(text) if end < 0 else end
            spans = designated.setdefault((start.start(), end), (label, []))[1]
            if mode == "proof" and wording_re.match(text, start.end(), end):
                found = True
            for hit in wording_re.finditer(text, start.end(), end):
                if mode != "proof":
                    found = True
                spans.append((hit.start(), hit.end()))
                if hit.groups() != want:
                    findings.append("README.md: {} states {} for ({}); the manifests give {}".format(
                        label, "/".join(hit.groups()), ", ".join(keys), "/".join(want)))
        if not found:
            findings.append("README.md: anchored count wording not found at its designated line ({}); "
                            "if the page was reworded or the proof line moved, update "
                            "README_COUNT_FACTS so the counts stay checked".format(label))
    for (line_start, line_end), (label, spans) in sorted(designated.items()):
        for run_start, run_end in _numeric_runs(text, line_start, line_end):
            if any(s <= run_start and run_end <= e for s, e in spans):
                continue
            findings.append("README.md: the numeric run {!r} on the designated {} line sits outside "
                            "every anchored count wording; a figure on a designated line must be "
                            "part of an anchored, checked wording (update README_COUNT_FACTS if the "
                            "page gained an operative count)".format(text[run_start:run_end], label))


# The independent code detector backing the shipped-path declaration: a release path is CODE when its
# suffix (case-insensitive) is on this CLOSED executable list, when it is an HTML page that
# decodes as UTF-8 (a leading byte-order mark allowed) and contains "<script" case-insensitively
# ANYWHERE (inside a comment included: a conservative over-detection, disclosed in _path_is_code),
# or when it is extensionless and opens with a shebang line. The detector runs only for a release
# path whose longest declared prefix is [[noncode]] (check_release_code_declaration), and there an
# HTML page holding a NUL byte or not decoding as UTF-8 is cannot-evaluate (exit 2), never
# non-code; a page under a [[code]] prefix is never read, so its encoding is not checked here.
# Closed by design:
# executable content of another kind is not detected (a disclosed residual); the declaration rows
# stay the reviewed source the detector cross-examines, never replaces.
_CODE_SUFFIXES = frozenset((".py", ".sh", ".bash", ".js", ".mjs", ".cjs"))
_HTML_SUFFIXES = frozenset((".html", ".htm"))


def _path_is_code(root, path):
    """True when the shipped file at `path` is executable content under the closed detector. The file
    is read only where the suffix alone cannot decide (an HTML page: does it contain "<script";
    an extensionless file: does it open with a shebang); a listed file that is then missing or
    unreadable raises OSError, so run() fails closed (exit 2) rather than guessing. An HTML page
    is DECODED AS UTF-8 (a leading UTF-8 byte-order mark allowed; whether that mark is stripped
    cannot change the result, since it can never sit inside "<script") and is code when it
    contains "<script" case-insensitively ANYWHERE, inside a comment included: a conservative
    over-detection (the comment-only vector in self-test case 22c pins it), never an
    under-detection. The operational contract is a refusal: a page that is not valid UTF-8, or
    whose decoded text holds a NUL, is cannot-evaluate (OSError, exit 2 via run()), never
    non-code. The NUL refusal is what catches the ASCII HTML fixtures of self-test case 22c
    encoded as UTF-16 or UTF-32 without a byte-order mark, which decode as UTF-8 holding NULs; a
    page with non-ASCII text in those encodings may instead fail to decode or decode without a
    NUL, so the refusal, not the encoding, is the contract. This fail-closed reading is bounded
    by the one caller, check_release_code_declaration, which calls the detector only for a
    release path whose longest declared prefix is [[noncode]]: a page under a [[code]] prefix is
    never read, so its encoding is not checked by this gate."""
    name = path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    suffix = name[dot:].lower() if dot > 0 else ""
    if suffix in _CODE_SUFFIXES:
        return True
    if suffix in _HTML_SUFFIXES:
        try:
            text = (root / path).read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise OSError("{}: HTML page does not decode as UTF-8, so the script detector cannot "
                          "evaluate it: {}".format(path, exc))
        if "\x00" in text:
            raise OSError("{}: HTML page holds a NUL byte (an ASCII page encoded as UTF-16 or "
                          "UTF-32 without a byte-order mark reads this way), so the script "
                          "detector cannot evaluate it".format(path))
        return "<script" in text.lower()
    if dot <= 0:
        with open(root / path, "rb") as handle:
            return handle.read(2) == b"#!"
    return False


def _prefix_matches(path, prefix):
    """A declared prefix ending in a slash matches every path in that tree; one without a trailing
    slash declares exactly that file, so "LICENSE" can never absorb a sibling "LICENSE-helper.sh" and
    "docs/_shell.html" can never absorb "docs/_shell.html.js"."""
    if prefix.endswith("/"):
        return path.startswith(prefix)
    return path == prefix


def check_release_code_declaration(root, findings, *, declaration=None, sources=None):
    """Every path the release manifest ships against the disclosure's [[code]]/[[noncode]] declaration
    (gen_disclosure.shipped_declaration, the same validated source the public shipped-code sentence is
    rendered from, so the prose and this gate cannot read two different shapes). A path is classified
    by the LONGEST declared prefix that matches it (case-sensitive, byte for byte; a prefix without a
    trailing slash matches only that exact path), so declaring a tree and a subtree is unambiguous
    and no ordering rule exists. The _path_is_code detector then backs the split: a detected code
    path classified under a [[noncode]] prefix is a finding naming both. A path under NO declared
    prefix is a finding; a declared prefix that classifies no path is a finding whose diagnostic
    distinguishes a prefix matching nothing from one overshadowed by longer prefixes; a missing or
    malformed declaration and a missing, empty, or malformed release manifest fail closed (exit 2).
    Row prose is never matched: the public sentence renders from the same declaration, so a path
    named in a limitation can no longer satisfy a category. `declaration` and `sources` override the
    rows and release paths read from the tree; the self-test uses them to prove, against the REAL
    declaration and release manifest, that removing any one [[code]] row and planting each reviewed
    rogue path turn the check red."""
    if declaration is None:
        try:
            declaration = shipped_declaration(load_toml(root / "disclosure.toml"))
        except ValueError as exc:
            raise OSError("disclosure.toml: {}".format(exc))
    code_rows, noncode_rows = declaration
    # noncode first, code second: under the longest-prefix rule the order cannot matter, and this
    # order makes the nested-prefix self-test red under a first-match regression (the broad noncode
    # parent would win and the detector would flag the code path under it).
    kinds = {row["prefix"]: "noncode" for row in noncode_rows}
    for row in code_rows:
        kinds[row["prefix"]] = "code"
    if sources is None:
        sources = load_toml(root / ".aiqt" / "manifest.toml").get("sources")
    if not isinstance(sources, list) or not sources or not all(isinstance(s, dict) for s in sources):
        raise OSError(".aiqt/manifest.toml: [[sources]] must be a non-empty array of tables")
    classified, matched = set(), set()
    for entry in sources:
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            raise OSError(".aiqt/manifest.toml: a [[sources]] entry has no string path")
        hits = [pref for pref in kinds if _prefix_matches(path, pref)]
        matched.update(hits)
        if not hits:
            findings.append(".aiqt/manifest.toml: release path {!r} falls under no declared [[code]] or "
                            "[[noncode]] prefix of disclosure.toml; declare it so the public "
                            "shipped-code list stays complete".format(path))
            continue
        best = max(hits, key=len)
        classified.add(best)
        if kinds[best] == "noncode" and _path_is_code(root, path):
            findings.append(".aiqt/manifest.toml: release path {!r} is code by this gate's detector "
                            "(an executable suffix, a script-holding HTML page, or a shebang) but "
                            "classifies under the [[noncode]] prefix {!r} of disclosure.toml; declare "
                            "it under a [[code]] row so the public shipped-code list names it".format(
                                path, best))
    for pref in kinds:
        if pref in classified:
            continue
        if pref in matched:
            findings.append("disclosure.toml: declared shipped-path prefix {!r} is overshadowed: every "
                            "release path it matches classifies under a longer declared prefix; "
                            "remove the row or narrow the longer rows".format(pref))
        else:
            findings.append("disclosure.toml: declared shipped-path prefix {!r} matches no "
                            "release-manifest path; remove the row or fix the prefix".format(pref))


# The two figures the pages state, each with one authoritative value: _BUDGET_FLOOR is the
# loader's character check and the "budget to" antecedent, and _SESSION_TARGET is the project's
# target for everything Claude Code loads at session start (and the ", not 150,000" aside).
_BUDGET_FLOOR = 120000
_SESSION_TARGET = 150000
# The full public sentence, every part bound: the "budget to 120,000" antecedent (the figure the
# note's "that figure" names; INSTRUCTION-BUDGET.md reads ", not 150,000" between them, the
# aside built from _SESSION_TARGET), the "may exceed that figure" clause (so a page reworded
# to "no longer exceeds" fails to match, a missing-note finding) and the committed ratchet with its
# unit and the unit's explanatory suffix
# (UTF-16 code units, the unit tools/check_instruction_budget.py counts and prints), bound through
# the sentence terminator (the period, then whitespace or the end of the page), so changing the
# unit, dropping it, rewording its suffix, extending the suffix ("counts in bytes"), or gluing
# text after the period ("counts.in bytes") is a missing-note finding. The sentence states the
# committed ratchet only, the most PACK the budget gate allows, never a measured PACK: this gate
# reads the ratchet, not a measurement, so the page claims no more than the gate binds.
_BUDGET_NOTE_RE = re.compile(
    r"budget to ([0-9][0-9,]*)(?:" + re.escape(", not " + format(_SESSION_TARGET, ",d")) + r")?\.\s*"
    r"Today the shipped pack may exceed that figure: the budget gate allows it a PACK of up to the "
    r"committed ratchet, ([0-9][0-9,]*) UTF-16 code units, the unit the budget gate counts\.(?!\S)")
# The other figures the budget section states, one wording per figure per page, each captured as
# the whole whitespace-free token (so a figure in another numeral form or with a suffix is read
# and compared, never skipped) and compared with its one authoritative value: the session target
# with _SESSION_TARGET, the build with claude-code-version in .aiqt/core/instruction-budget.toml,
# and the loader's character check with _BUDGET_FLOOR. Words are joined by \s+ because the pages
# wrap these sentences across lines. A page whose wording is absent is a finding, never a skip.
_BUDGET_FIGURE_WORDINGS = (
    ("README.md", "target", "the session target",
     re.compile(r"under\s+(\S+)\s+characters\s+in\s+total\.")),
    ("INSTRUCTION-BUDGET.md", "target", "the session target",
     re.compile(r"Treat\s+(\S+)\s+characters\s+as\s+this\s+project's\s+target")),
    ("README.md", "build", "the pinned Claude Code build",
     re.compile(r"the\s+pinned\s+Claude\s+Code\s+build\s+the\s+gate\s+models\s+\(([^\s,)]+)")),
    ("INSTRUCTION-BUDGET.md", "build", "the pinned Claude Code build",
     re.compile(r"the\s+pinned\s+Claude\s+Code\s+build\s+the\s+gate\s+models\s+\(([^\s,)]+)")),
    ("README.md", "loader", "the loader's character check",
     re.compile(r"carries\s+its\s+own\s+(\S+?)-character\s+check")),
    ("INSTRUCTION-BUDGET.md", "loader", "the loader's character check",
     re.compile(r"carries\s+its\s+own\s+(\S+?)-character\s+check")),
)


def check_budget_note(root, findings):
    """The public instruction-budget section against the values it states: README.md and
    INSTRUCTION-BUDGET.md each carry the _BUDGET_NOTE_RE sentence (the 120,000 antecedent the
    note's "that figure" names, the may-exceed-that-figure half,
    the figure, and its UTF-16 code-unit label with its explanatory suffix all bound through the
    sentence terminator, so rewording any of them away or extending the suffix is a
    finding), the stated antecedent must equal _BUDGET_FLOOR, the figure must equal the ratchet of
    .aiqt/core/instruction-budget.toml
    (thousands-separated, as the pages print it), and the ratchet must still exceed the
    _BUDGET_FLOOR figure the note says the shipped pack may exceed. Each page's
    _BUDGET_FIGURE_WORDINGS wording must be present, and every figure it captures must equal its
    one authoritative value: the session target _SESSION_TARGET and the loader's check
    _BUDGET_FLOOR (thousands-separated), and the build claude-code-version of the budget source.
    Pinned to the COMMITTED ratchet, not a fresh measurement, and the pages state only the
    ratchet (the budget gate holds PACK at or under the ratchet and prints a lower-the-ratchet
    note when PACK falls below it); a missing or non-integer ratchet, a missing or non-string
    claude-code-version, and a missing page, fail closed (exit 2)."""
    budget = load_toml(root / ".aiqt" / "core" / "instruction-budget.toml")
    ratchet = budget.get("ratchet")
    if not isinstance(ratchet, int) or isinstance(ratchet, bool) or ratchet <= 0:
        raise OSError(".aiqt/core/instruction-budget.toml: ratchet must be a positive integer")
    build = budget.get("claude-code-version")
    if not isinstance(build, str) or not build:
        raise OSError(".aiqt/core/instruction-budget.toml: claude-code-version must be a non-empty "
                      "string")
    want = format(ratchet, ",d")
    floor_want = format(_BUDGET_FLOOR, ",d")
    figure_want = {"target": format(_SESSION_TARGET, ",d"), "build": build, "loader": floor_want}
    texts = {}
    for rel in ("README.md", "INSTRUCTION-BUDGET.md"):
        texts[rel] = _read(root / rel)
    for rel, key, label, pattern in _BUDGET_FIGURE_WORDINGS:
        hits = pattern.findall(texts[rel])
        if not hits:
            findings.append("{}: the wording that states {} ({}) was not found; if the page was "
                            "reworded, update _BUDGET_FIGURE_WORDINGS so the figure stays "
                            "checked".format(rel, label, pattern.pattern))
        for got in hits:
            if got != figure_want[key]:
                findings.append("{}: the page states {} as {}; its authoritative value is "
                                "{}".format(rel, label, got, figure_want[key]))
    for rel in ("README.md", "INSTRUCTION-BUDGET.md"):
        text = texts[rel]
        hits = _BUDGET_NOTE_RE.findall(text)
        if not hits:
            findings.append("{}: the instruction-budget note (budget to 120,000 ... the shipped "
                            "pack may exceed that figure: the budget gate allows it a PACK of up "
                            "to the committed ratchet, N UTF-16 code units, the unit the budget "
                            "gate counts.) was not found; if the "
                            "page was reworded, update check_budget_note so the figure stays "
                            "checked".format(rel))
            continue
        for ante, got in hits:
            if ante != floor_want:
                findings.append("{}: the budget note names budget to {} as the antecedent of that "
                                "figure; this gate binds the antecedent to {}".format(
                                    rel, ante, floor_want))
            if got != want:
                findings.append("{}: the budget note states PACK {}; the committed ratchet is "
                                "{}".format(rel, got, want))
        if ratchet <= _BUDGET_FLOOR:
            findings.append("{}: the page says the shipped pack may exceed {}, but the committed "
                            "ratchet is {}; reword the note".format(rel, floor_want, want))


def run(root):
    findings = []
    try:
        manifests = load_manifests(root / ".aiqt" / "standards", root=root)
        if not manifests:
            print("error: no manifests under .aiqt/standards/; fail-closed", file=sys.stderr)
            return 2
        fams = expected_families(manifests)
        check_meta(root, fams, findings)
        check_disclosure(root, fams, findings)
        check_release_code_declaration(root, findings)
        check_budget_note(root, findings)
        check_enum_table(root, findings)
        check_parity(findings)
        check_deferred(root, manifests, findings)
        check_subset_denominator(root, manifests, findings)
        check_json_subset_denominator(root, manifests, findings)
        check_readme_counts(root, manifests, findings)
    except (ManifestError, OSError, ValueError, KeyError) as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} reference-fact issue(s):".format(len(findings)))
        for line in sorted(findings):
            print("  " + line)
        return 1
    print("PASS: reference facts agree with the manifests ({} manifest(s), {} families)".format(
        len(manifests), len(fams)))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test_main()
    return run(repo_root())


# --- self-test ----------------------------------------------------------------------------------------
# Deterministic fixtures assembled in a tempdir (the check_standards_currency/gen_gensrc pattern), each
# driving the real run()/checks so the mechanism under test is never mocked:
#   1. a clean fixture passes (exit 0),
#   2. a meta parenthetical missing one family -> finding (exit 1),
#   3. a disclosure claim naming a family with no manifest -> finding,
#   4. a schema `kind` row missing a kind -> finding,
#   5. a parity break (a POLICY key removed via a patched dict) -> finding,
#   6. a manifest whose name appears in the "Not yet vendored" paragraph -> finding,
#  6b-6f, 6h-6j. the same fact in the forms the first-match, after-the-phrase, case-sensitive read
#      missed, each -> finding: a table row whose name cell precedes the status cell, a second deferred
#      paragraph, a case variant, a phrase wrapped across a line, and a header row that marks the whole
#      table; 6h. a name on a paragraph line before the phrase, 6i. a name wrapped across a line and
#      6j. a name in another case -> finding;
#  6k-6m, 6p. the Markdown structures the pipe-led row read missed that the positive grammar still
#      reads, each -> finding: a table body row without outer pipes, a table delimiter row without
#      outer pipes, an ATX heading whose section lists the name, and a hyphenated phrase;
#  6r-6v, 6x, 6zb. the flat structures a per-block read still missed, each -> finding by the
#      whole-section read: a table body row with no pipe at all, a pipe-free row under a pipe-light
#      header (two-column and one-column), a column-0 lazy list continuation (plain and
#      name-splitting), an emphasis-split phrase, and the disclosed over-refusal (a vendored name
#      elsewhere in the same section, outside any deferred block, is a finding too);
#  6n, 6o, 6w, 6y, 6z, 6za. arrangements earlier revisions read through container stripping or
#      fence tracking are now OUTSIDE the positive deferred-section grammar and REFUSED (exit 2,
#      naming the file and line): a blockquote-wrapped phrase, a nested (indented) list under the
#      phrase item, a blockquoted lazy list continuation, an indented list after a lazy
#      continuation, an indented list under an indented phrase line, and a fence inside the
#      deferred section (its '#' content can no longer end the section: the fence itself is
#      refused);
#  6zq-6zs. three counter-examples, each REFUSED (exit 2, naming a line; 6zq and 6zr exit
#      through the FENCE check, the unterminated-fence refusal naming the fence's opening line,
#      never the span grammar; 6zs exits through the positive span grammar), never a clean pass
#      through a mis-bounded section: a backtick fence whose info string holds a backtick (not a
#      CommonMark opener; the old reader's fence state inverted and a '#' content line read as the
#      end boundary, 6zq), a pseudo-closing tilde fence followed by U+00A0 (only spaces and tabs
#      may follow a close, so the fence stays open and the text ends unterminated, 6zr), and a
#      list-item blockquote holding an HTML block whose stripped '> # fake' would otherwise read as
#      the end boundary (6zs);
#  6zu-6zv. the same two fence rules ABOVE the section: a backtick-info pseudo-fence is a refused
#      fence run (exit 2, 6zu), and a U+00A0 pseudo-close is content, so the fence closes at the
#      later bare line and the section below is READ (its planted name is the finding, exit 1,
#      6zv);
#  6zh. a fence ABOVE the section whose pseudo-closing line carries trailing words does not close
#      (the closing fence carries nothing but spaces or tabs): the later bare fence line is the
#      real close, the section below is read, and the name in it is a finding -> exit 1 (dropping
#      the nothing-else rule closes the fence at the pseudo-closing line and reopens an
#      unterminated fence at the bare line, exit 2);
#  6zt. the FORMER decided boundary, now refused: a blockquoted ATX heading after the phrase is a
#      heading in the blockquote-stripped reading only, so the raw-versus-stripped heading
#      agreement rule refuses the section (exit 2, naming the boundary line); the reader this
#      replaces read it as the section end and passed the name after it clean,
#  6zw-6zx. decoy headings above the phrase manufactured by blockquote stripping (a list-quoted
#      '###### fake'; a blockquoted backtick fence inverting the stripped reading's fence state):
#      each is a heading in exactly one of the two readings, so the agreement rule refuses
#      (exit 2, naming the first disagreeing line), where the reader this replaces passed the
#      planted name clean through a shrunken section,
#  6zy-6zz, 6zza-6zze. span lines CommonMark reads as non-text blocks, refused by the tightened
#      positive grammar (exit 2, naming the line): a setext underline under the phrase (clean on
#      the reader this replaces, the renderer's section continuing past the reader's boundary), a
#      thematic break, indented code after a blank line or directly under a heading, list-held
#      code (five spaces after the marker; a TAB gap is refused too, a conservative over-refusal
#      of a shape CommonMark reads as a plain item), and a list-held heading,
#  6zzf-6zzi. an indented '#' line (first non-space character '#', any leading whitespace),
#      anywhere from the file start to the section end, the end-boundary line included (6zzi, an
#      indented heading AT the boundary, list-nested in CommonMark), is refused (exit 2, naming
#      the line):
#      CommonMark can read it as a heading nested in a list item ('- x' then '  ###### note'),
#      which this reader does not model but HEADING would read as a top-level boundary; a LEGAL
#      indented top-level heading is over-refused by the same rule (disclosed). The 6zzh control
#      (a blockquote line, then a top-level column-0 '###### note') keeps its unchanged clean
#      result, exit 0;
#  6g. a vendored name in a section without the phrase, and 6q. one after the next same-level
#      heading, stay clean (the read is scoped to the section),
#  6zc-6zg. arrangements the section reader cannot prove it bounds are REFUSED (exit 2, naming the
#      file and line), never read through a false heading's truncation: a list-item fence whose '#'
#      content reads as a false heading even when a LATER INDEPENDENT fence rebalances the scanner
#      (6zc, the clean-pass path), two list-item fences (6zd), an HTML comment block (6ze), a <pre>
#      block (6zf), and a four-space-indented fence (6zg); each body keeps the vendored name in the
#      same rendered section (confirmed against markdown-it), so a truncating read would pass it
#      clean,
#  6zi-6zn. arrangements ABOVE or AT the section start are refused too (exit 2, naming the file and
#      line): the structure scan runs from the top of the text to the section's end boundary, so a
#      boundary is validated BEFORE it may exclude text. A list fence whose '#' content reads as the
#      section's START heading (6zi), an HTML block whose '#' content reads as the start heading
#      (6zj), a list fence above the phrase paragraph with a later independent fence rebalancing the
#      scanner (6zk), list-item HTML ('- <pre>') whose '#' content reads as the end boundary (6zl),
#      a '#' line separated by U+00A0 (not a heading in the accepted grammar: HEADING takes exactly
#      U+0020 or the line end, 6zm), and a separator-less '#' line (pinning the '#'-not-a-heading
#      refusal branch, 6zn); on the per-section reader this replaces, 6zi-6zm each passed clean
#      through a truncated or mis-started section,
#  6zp. a single-line HTML comment above the section ('<!-- ... -->', the real README's generated
#      roster-marker shape) is NOT over-refused AND the section below it is proven READ: the fixture
#      plants a vendored name in that section, so the expected result is the finding (exit 1), which
#      an over-refusal (exit 2) and an unread section (exit 0) would each miss,
#   7. a subset manifest rendered "3 of 3" in the fixture registry row -> finding (the CWE regression),
#   8. a missing site/mappings.html -> exit 2 (fail-closed, not a skip),
#   9. an unreadable standards dir (chmod 0, or an injected permission failure where chmod cannot
#      bite) -> exit 2; the case runs on every runner; 9b. a dangling-symlink manifest fails INSIDE
#      the REAL load_manifests (its open() raises on every runner, no seam and no chmod) -> exit 2,
#  10. a manifest without `catalogue`, and one with `catalogue = "partial"` -> ManifestError exit 2
#      (the loader end of the invariant),
#  11. a publisher with no FAMILY_ALIAS entry -> exit 2,
#  12. a subset reverse-view <p> coverage mutated to "N of M" -> finding (the structure-aware case: the
#      name is in the <summary>, the denominator in a later <p>, so a same-line scan would miss it),
#  13. a subset framework carrying ids_total in the JSON export -> finding (the export invariant),
#  14. a missing site/downloads/mappings.json -> exit 2 (fail-closed, not a skip),
#  14b. an unterminated code fence in the README -> exit 2 (the section reader cannot bound a section
#      through it, fail-closed), naming the file and the fence's opening line,
#  15. a README landing-page count changed -> finding (the page's counts are recomputed from their
#      manifests, so a stale count cannot ship),
#  16. a reworded anchored count wording -> finding (its absence is itself a finding, so a reword
#      cannot silently drop a count from coverage),
#  17. EVERY captured count figure, drifted alone to 999 at its designated line -> finding (one fixture
#      per figure, so dropping any one README_COUNT_FACTS pattern, or comparing only part of a hit's
#      figures, turns a case here red),
#  18. each wording, broken alone -> a missing-anchored-wording finding (per-pattern coverage),
#  19. an unsupported numeric form at each wording (a thousands separator, a leading sign; the
#      "1,132" / "-35" review forms) -> finding, never a partial-match pass (the rules-row leading
#      sign is caught by the _NUM lookbehind alone, so reverting _NUM to a bare ([0-9]+) turns it red),
#  20. a TRUE explanatory count sentence outside the designated lines -> exit 0, one per anchored
#      wording (the location binding: unanchoring ANY of the five patterns makes its wrong-figure
#      aside here an operative count and turns the case red),
#  20b. a second, contradictory enforcement figure LATER ON the designated controls line -> finding
#      (every occurrence on the designated line is read),
#  20c. the enforcement sentence or the crosswalk coverage split onto the next line -> a
#      missing-anchored-wording finding (no pattern matches across a line break),
#  20d. the proof line replaced and its full wording moved to a later bold numeric line -> a
#      missing-anchored-wording finding (the designated proof line is the FIRST bold-digit line and
#      the wording must open it),
#  20d2. a bold digit line INSERTED ABOVE the real proof line -> the missing-proof-wording finding,
#      asserted by its own message (the designated proof line is the FIRST bold-digit line; the
#      message assertion, not the exit code, is what turns red if the first-line rule is removed),
#  20e. a numeric run on a designated line outside every anchored wording -> finding (the
#      numeric sweep): a signed duplicate beside the valid wording, a reworded enforcement figure, a
#      thousands-separated duplicate, and the reviewed evasive forms a noun list would miss (bare,
#      draft, singular, and renamed nouns, a scientific-notation figure, a doubled space, a no-break
#      space, bold markup, fullwidth and Arabic-Indic digits, superscript, subscript, circled and
#      dingbat numerals, a Roman numeral character, a vulgar fraction, CJK numerals); the clean
#      fixture of case 1 is the positive control for a true designated line,
#  20f. the disclosed residual witness: each named numeric-looking symbol that carries no Unicode
#      numeric value (keycap ten, the hundred points symbol, each die face) on the designated
#      proof line -> exit 0 (not read by the sweep, as the module docstring discloses),
#  21. a missing ENFORCEMENT.md, rules folder, gates or hooks manifest -> exit 2, a malformed gates or
#      hooks manifest (a non-table `gate = "ab"` / `hook = "ab"` value, a non-table element inside the
#      array, an entry with no platform or a non-string platform, gate and hook forms both, and a
#      non-string default on ANY platform, an excluded one included) or an all-wrong-platform manifest
#      -> exit 2, a claude-code hook whose default is outside the blocking/warning split -> exit 2,
#      and a partially-off-platform gates manifest -> finding (the filtered count disagrees with the
#      page); the enforced figure summed over the register's linked rows: a partial linked row counts
#      (exit 0), a Preview only row does not (finding), and a missing linked row or a bare Enforced
#      row -> exit 2 naming the row; where the refusal's provenance carries the claim, its diagnostic
#      is asserted too (21b),
#  22. the shipped-path declaration leg: removing any declared row, a release path under no declared
#      prefix, a declared prefix matching no release path, and a fully overshadowed declared prefix
#      (asserted by its own diagnostic) -> finding; a nested prefix pair stays clean (the longest
#      declared prefix classifies, order-independent: the candidate list reads the broad noncode
#      parent first, so a first-match regression is red through the detector); a missing
#      declaration, and a missing or empty release manifest -> exit 2 (fail-closed),
#  22b. the REAL declaration and release manifest: removing each actual [[code]] row -> at least one
#      finding, each reviewed rogue path planted in the real manifest -> a finding naming it, and
#      the unmodified pair -> no findings (the control),
#  22c. the code detector branch by branch, each vector planted UNDER A POPULATED [[noncode]]
#      prefix so only _path_is_code can flag it: every closed-list suffix, a mixed-case and a
#      double suffix, a script-holding HTML page (a .htm page, a mixed-case "<ScRiPt>" tag, a
#      comment-only "<script>" and a UTF-8 byte-order-marked page included), and an
#      extensionless shebang file -> a detector finding naming the path and the prefix; a plain
#      .html and .htm page, a page naming "Script" without a tag, and a markdown file -> exit 0;
#      an HTML page that does not decode as UTF-8 or that holds a NUL byte (UTF-16 and UTF-32
#      without a byte-order mark, and a NUL inside UTF-8) -> exit 2 asserted by its diagnostic
#      (cannot-evaluate, never non-code); a listed file missing or unreadable, for each branch
#      that reads the file -> exit 2 (fail-closed),
#  23. the instruction-budget note: a figure that disagrees with the committed ratchet, a missing
#      note, a note whose exceeds half was reworded away, and a note kept after the ratchet no
#      longer exceeds 120,000 -> finding; a missing budget source -> exit 2 (fail-closed),
#  23b. the unit binding, on each public surface: the unit changed to "characters", the unit
#      omitted, its explanatory suffix altered, the suffix extended past its sentence end
#      ("counts in bytes.", "counts, in bytes."), or non-whitespace glued after the period
#      ("counts.in bytes.") -> the missing-note finding (asserted by its diagnostic naming the
#      surface); the period at the end of the page and the period followed by whitespace and
#      another sentence -> clean (the two ends the terminator accepts); an antecedent other
#      than 120,000 before "that figure" ->
#      a finding by its own antecedent diagnostic; the antecedent with the ", not 150,000" aside
#      (INSTRUCTION-BUDGET.md's real wording) -> clean, and an aside naming another figure -> the
#      missing-note finding,
#  23c. the exceeds boundary: a ratchet exactly at 120,000 -> finding (at-boundary); one unit
#      above -> clean (just outside),
#  23d. the other budget figures, on each page: the session target, the pinned build and the
#      loader's character check, each drifted alone -> a finding by its drift diagnostic; each
#      wording reworded away -> the not-found diagnostic; the budget source's build moved under
#      unchanged pages -> a build finding on both pages; a missing or non-string build -> exit 2;
#      the earlier "measured PACK" wording restored -> the missing-note finding.
# (1, 7, 12, 13, and 15 through 23 are the invariant-bearing cases; the rest complete the fail-closed
# contract.)

_MANIFEST = (
    'map-key = "map-{k}"\nname = "{name}"\npublisher = "{pub}"\nedition = "{ed}"\n'
    'kind = "{kind}"\nstatus = "stable"\ncatalogue = "{cat}"\ncitation-unit = "control"\n'
    'id-pattern = "ST[0-9]{{2}}"\nsource-artefact = "self-test fixture"\nretrieved = "2026-01-01"\n'
    '[[id]]\ncode = "ST01"\ntitle = "one"\n[[id]]\ncode = "ST02"\ntitle = "two"\n'
    '[[id]]\ncode = "ST03"\ntitle = "three"\n'
)

_README = (
    "# Standards id-manifests\n\n## Manifest schema\n\n"
    "| field | meaning |\n| --- | --- |\n"
    "| `kind` | `risk` \\| `control` \\| `guidance` \\| `technique` \\| `weakness` (relation wording) |\n"
    "| `status` | `stable` \\| `beta` \\| `snapshot` (edition stability) |\n"
    "| `catalogue` | `full` \\| `subset` (complete edition or curated) |\n\n"
    "Not yet vendored (their keys stay inert until a manifest lands): Google SAIF and OWASP SCVS.\n"
)

# Two meta descriptions, a two-row registry, and a two-block reverse view mirroring the real render (the
# name in a <summary>, the coverage in a later <p>). The subset row and subset reverse block use the
# honest "curated subset" form; the full block legitimately reads "3 of 3".
_PAGE = (
    '<!doctype html>\n'
    '<meta name="description" content="A crosswalk to frameworks (ISO/IEC, OWASP). Titles only.">\n'
    '<meta property="og:description" content="A crosswalk to frameworks (ISO/IEC, OWASP). Titles only.">\n'
    '<tr><td>Fixture Full Framework</td><td>OWASP Foundation</td><td>1.0</td><td>3 of 3</td></tr>\n'
    '<tr><td>Fixture Subset Framework</td><td>ISO/IEC</td><td>2023</td>'
    '<td>2 referenced (curated subset)</td></tr>\n'
    '      <details class="more">\n'
    '        <summary>Fixture Full Framework (1.0)</summary>\n'
    '        <div class="inner">\n'
    '          <p>supports control. 3 of 3 identifiers referenced.</p>\n'
    '        </div>\n'
    '      </details>\n'
    '      <details class="more">\n'
    '        <summary>Fixture Subset Framework (2023)</summary>\n'
    '        <div class="inner">\n'
    '          <p>aligns with guidance. 2 identifiers referenced from a curated subset of the '
    'edition.</p>\n'
    '        </div>\n'
    '      </details>\n'
)

# The JSON export: a full framework carries ids_total, the subset omits it (the catalogue invariant in
# machine-readable form). Keys are the framework stems (map-key without the "map-" prefix).
_JSON = (
    '{\n'
    '  "frameworks": {\n'
    '    "full": {"name": "Fixture Full Framework", "catalogue": "full", "ids_cited": 3, '
    '"ids_total": 3},\n'
    '    "subset": {"name": "Fixture Subset Framework", "catalogue": "subset", "ids_cited": 2, '
    '"ids_total": null}\n'
    '  },\n'
    '  "mappings": []\n'
    '}\n'
)

# The disclosure fixture carries the standards-mappings row (the parenthetical leg), the two
# shipped-code prose rows, and the [[code]]/[[noncode]] declaration the shipped-path leg checks: every
# declared prefix is populated by a _RELEASE_MANIFEST path, and every _RELEASE_MANIFEST path falls
# under exactly one declared prefix, so each declaration-removal case leaves a path unplaced.
_DISCLOSURE = (
    'title = "d"\nnote = "n"\nsite_base = "https://example.org"\n\n'
    '[[row]]\nid = "standards-mappings"\ntopic = "Standards mappings"\n'
    'claim = "A crosswalk to identifiers in frameworks (ISO/IEC, OWASP), each pinned."\n'
    'limitation = "A mapping asserts a relationship, not certification."\n'
    'evidence = [\n  { text = "How", href = "/mappings#methodology" },\n]\n\n'
    '[[row]]\nid = "what-it-is-made-of"\ntopic = "What the product is made of"\n'
    'claim = "The chat artefacts are prose only. The repository also carries code: the tooling under '
    'tools/ and opf/, the preview hook scripts under .preview/, the generated hooks plugin, and the '
    'JavaScript under site/."\n'
    'limitation = "Fixture limitation."\n'
    'evidence = [\n  { text = "What", href = "/install#pick" },\n]\n\n'
    '[[row]]\nid = "data-handling"\ntopic = "Data handling"\n'
    'claim = "The repository ships code: the tooling under tools/ and opf/, the preview hook scripts '
    'under .preview/, the hooks plugin, and the JavaScript under site/."\n'
    'limitation = "Some checks and tools make network requests when run."\n'
    'evidence = [\n  { text = "Data", href = "/tech-details#qa-where" },\n]\n\n'
    '[[code]]\nprefix = "tools/"\nlabel = "the tooling under tools/"\n\n'
    '[[code]]\nprefix = "opf/tools/"\nlabel = "the tooling under opf/tools/"\n\n'
    '[[code]]\nprefix = ".preview/"\nlabel = "the preview hook scripts under .preview/"\n\n'
    '[[code]]\nprefix = "plugin/"\nlabel = "the hooks plugin"\n\n'
    '[[code]]\nprefix = ".aiqt/core/hooks/"\nlabel = "the hooks plugin source"\n\n'
    '[[code]]\nprefix = "site/"\nlabel = "the JavaScript under site/"\n\n'
    '[[noncode]]\nprefix = "README.md"\n\n'
    '[[noncode]]\nprefix = "ENFORCEMENT.md"\n'
)


# The landing-page fixture components: three rules, two gates, two hooks (one blocking, one warning),
# two framework manifests (the default `manifests`), and one enforced rule. _LANDING carries every
# README_COUNT_FACTS wording with exactly those figures.
_GATES_TOML = (
    '[[gate]]\nid = "fixture-gate-one"\nscript = "tools/one.py"\nrules = []\nplatform = "ci"\n'
    'default = "block"\nclass = "a"\nresidue = "fixture"\n\n'
    '[[gate]]\nid = "fixture-gate-two"\nscript = "tools/two.py"\nrules = []\nplatform = "ci"\n'
    'default = "block"\nclass = "a"\nresidue = "fixture"\n'
)

_HOOKS_TOML = (
    '[[hook]]\nid = "fixture-hook-block"\nrules = []\nplatform = "claude-code"\ndefault = "block"\n\n'
    '[[hook]]\nid = "fixture-hook-warn"\nrules = []\nplatform = "claude-code"\ndefault = "warn"\n'
)

_ENFORCEMENT_MD = (
    "# Fixture Enforcement Register\n\n## Summary\n\n| Status | Rules |\n|---|---:|\n"
    "| Gate-linked (class a) | 1 |\n| Gate-linked (class a), partial | 0 |\n"
    "| Gate-linked (class c only) | 0 |\n| Gate-linked (class c only), partial | 0 |\n"
    "| Hook-linked | 0 |\n| Hook-linked, partial | 0 |\n| Preview only | 0 |\n"
    "| Preview only, partial | 0 |\n| Pending | 0 |\n| No control | 2 |\n"
)

# The instruction-budget fixtures for the budget-note leg: the committed ratchet and the pinned
# build, the public note stating the ratchet (the same sentence in the landing README and in
# INSTRUCTION-BUDGET.md), and before it each page's session-target wording and the shared caveat
# naming the build and the loader's check (wrapped across lines, as the real pages wrap them; the
# landing page writes the build with a following comma, the budget page with a following space).
_BUDGET_TOML = 'ratchet = 132775\nclaude-code-version = "9.8.7"\n'
_BUDGET_CAVEAT = ("The loader of the pinned Claude Code build the gate\nmodels (9.8.7{after}) "
                  "carries its own\n120,000-character check, so ")
_BUDGET_NOTE = ("budget to 120,000. Today the shipped pack may exceed that figure: the budget gate "
                "allows it a PACK of up to the committed ratchet, 132,775 UTF-16 code units, the "
                "unit the budget gate counts.\n")
_BUDGET_MD = ("# Fixture Budget\n\nTreat 150,000 characters as this project's target. "
              + _BUDGET_CAVEAT.format(after=" in the budget source") + _BUDGET_NOTE)

_LANDING = (
    "# Fixture Landing\n\n"
    "**3 rules. 2 CI gates that check this repository. 2 Claude Code hooks shipped as an opt-in "
    "plugin. Mapped to 2 frameworks.**\n\n"
    "| **The standard** | 3 clear, single-behaviour rules across the fixture facets. | Rules |\n"
    "| **Technical controls** | 2 CI gates that check this repository and 2 Claude Code hooks shipped "
    "as an opt-in plugin (1 blocking, 1 warning). Today 1 of the 3 rules are linked to at least one "
    "shipped gate or hook. | Controls |\n"
    "| **A standards crosswalk** | Mappings from the rules to 2 pinned external control catalogues. "
    "| Mappings |\n"
    "\nKeep it under 150,000\ncharacters in total. " + _BUDGET_CAVEAT.format(after=", pinned")
    + _BUDGET_NOTE
)

# The release-manifest fixture: at least one path under every declared fixture prefix, code and
# non-code, so removing any single declared row leaves a path unplaced (a finding).
_RELEASE_MANIFEST = (
    'format-version = 1\nrelease-version = "0.0.0"\n\n'
    '[[sources]]\npath = "README.md"\n\n'
    '[[sources]]\npath = "ENFORCEMENT.md"\n\n'
    '[[sources]]\npath = "tools/fixture_check.py"\n\n'
    '[[sources]]\npath = "tools/fixture_status.sh"\n\n'
    '[[sources]]\npath = "opf/tools/fixture_tool.py"\n\n'
    '[[sources]]\npath = ".preview/fixture_preview.py"\n\n'
    '[[sources]]\npath = "plugin/fixture-hooks/hooks/scripts/fixture_hooks.py"\n\n'
    '[[sources]]\npath = ".aiqt/core/hooks/scripts/fixture_hooks.py"\n\n'
    '[[sources]]\npath = "site/js/fixture.js"\n'
)


def _write_fixture(base, manifests=None, readme=None, page=None, disclosure=None, json_export=None,
                   landing=None, release_manifest=None, budget_toml=None, budget_md=None):
    """Write a complete clean fixture tree, overridable per component for a case's single mutation.
    `manifests` is a list of (stem, name, publisher, edition, kind, catalogue); `landing` overrides the
    README.md the counts leg reads; `release_manifest` overrides the .aiqt/manifest.toml the
    shipped-path declaration leg reads; `budget_toml` and `budget_md` override the instruction-budget
    source and page the budget-note leg reads."""
    if manifests is None:
        manifests = [("full", "Fixture Full Framework", "OWASP Foundation", "1.0", "control", "full"),
                     ("subset", "Fixture Subset Framework", "ISO/IEC", "2023", "guidance", "subset")]
    std = base / ".aiqt" / "standards"
    std.mkdir(parents=True)
    for stem, name, pub, ed, kind, cat in manifests:
        (std / (stem + ".toml")).write_text(
            _MANIFEST.format(k=stem, name=name, pub=pub, ed=ed, kind=kind, cat=cat), encoding="utf-8")
    (std / "README.md").write_text(readme if readme is not None else _README, encoding="utf-8")
    site = base / "site"
    site.mkdir(parents=True)
    (site / "mappings.html").write_text(page if page is not None else _PAGE, encoding="utf-8")
    downloads = site / "downloads"
    downloads.mkdir()
    (downloads / "mappings.json").write_text(
        json_export if json_export is not None else _JSON, encoding="utf-8")
    (base / "disclosure.toml").write_text(
        disclosure if disclosure is not None else _DISCLOSURE, encoding="utf-8")
    core = base / ".aiqt" / "core"
    rules_dir = core / "rules"
    rules_dir.mkdir(parents=True)
    for stem in ("one", "two", "three"):
        (rules_dir / ("fixture-rule-" + stem + ".md")).write_text(
            "# fixture rule " + stem + "\n", encoding="utf-8")
    (core / "gates").mkdir()
    (core / "gates" / "manifest.toml").write_text(_GATES_TOML, encoding="utf-8")
    (core / "hooks").mkdir()
    (core / "hooks" / "manifest.toml").write_text(_HOOKS_TOML, encoding="utf-8")
    (base / "ENFORCEMENT.md").write_text(_ENFORCEMENT_MD, encoding="utf-8")
    (base / "README.md").write_text(landing if landing is not None else _LANDING, encoding="utf-8")
    (core / "instruction-budget.toml").write_text(
        budget_toml if budget_toml is not None else _BUDGET_TOML, encoding="utf-8")
    (base / "INSTRUCTION-BUDGET.md").write_text(
        budget_md if budget_md is not None else _BUDGET_MD, encoding="utf-8")
    (base / ".aiqt" / "manifest.toml").write_text(
        release_manifest if release_manifest is not None else _RELEASE_MANIFEST, encoding="utf-8")


def self_test_main():
    import io
    import os
    import shutil
    import tempfile
    from contextlib import redirect_stderr, redirect_stdout

    def run_quiet(root):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            try:
                return run(root)
            except SystemExit as exc:
                return "raised SystemExit({!r})".format(exc.code)

    def run_err(root):
        """(exit, stderr) so a fail-closed case can assert its refusing diagnostic, not only its exit
        code (the structure-check provenance the 21b comment claims)."""
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                code = run(root)
            except SystemExit as exc:
                code = "raised SystemExit({!r})".format(exc.code)
        return code, err.getvalue()

    def run_out(root):
        """(exit, stdout) so a finding-bearing case can assert WHICH finding fired, not only the
        exit code (the first-bold-line vector depends on it: its fixture is red for a second reason
        too, the inserted digit run, so only the message proves the first-line rule)."""
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            try:
                code = run(root)
            except SystemExit as exc:
                code = "raised SystemExit({!r})".format(exc.code)
        return code, out.getvalue()

    def run_capture(root):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = run(root)
            except SystemExit as exc:
                rc = "raised SystemExit({!r})".format(exc.code)
        return rc, out.getvalue() + err.getvalue()

    try:
        tmp = Path(tempfile.mkdtemp(prefix="aiqt-reference-facts-gate-selftest-"))
    except OSError as exc:
        print("SELF-TEST ERROR: no writable temporary directory: {}".format(exc), file=sys.stderr)
        return 2
    failures = []
    skipped = []
    unreadable_std = None
    try:
        # 1. clean fixture passes.
        clean = tmp / "clean"
        _write_fixture(clean)
        if run_quiet(clean) != 0:
            failures.append("clean fixture expected exit 0")

        # 2. a meta parenthetical missing one family -> finding.
        meta = tmp / "meta"
        _write_fixture(meta, page=_PAGE.replace("frameworks (ISO/IEC, OWASP)", "frameworks (OWASP)", 1))
        if run_quiet(meta) != 1:
            failures.append("meta parenthetical missing a family expected exit 1")

        # 3. a disclosure claim naming a family with no manifest -> finding.
        disc = tmp / "disc"
        _write_fixture(disc, disclosure=_DISCLOSURE.replace(
            "frameworks (ISO/IEC, OWASP)", "frameworks (ISO/IEC, OWASP, NIST)"))
        if run_quiet(disc) != 1:
            failures.append("disclosure claim naming an absent family expected exit 1")

        # 4. a schema `kind` row missing a kind -> finding.
        enum = tmp / "enum"
        _write_fixture(enum, readme=_README.replace(
            "`risk` \\| `control` \\| `guidance` \\| `technique`", "`risk` \\| `control` \\| `guidance`"))
        if run_quiet(enum) != 1:
            failures.append("schema kind row missing a kind expected exit 1")

        # 4b. the schema `catalogue` row missing a value -> finding (guards catalogue-row coverage).
        cat_enum = tmp / "cat_enum"
        _write_fixture(cat_enum, readme=_README.replace(
            "`full` \\| `subset` (complete edition or curated)", "`full` (complete edition or curated)"))
        if run_quiet(cat_enum) != 1:
            failures.append("schema catalogue row missing a value expected exit 1")

        # 5. a parity break: temporarily remove a POLICY key via a patched module global, then restore.
        parity = tmp / "parity"
        _write_fixture(parity)
        module = sys.modules[__name__]
        saved = module.POLICY
        module.POLICY = {k: v for k, v in saved.items() if k != "beta"}
        try:
            rc = run_quiet(parity)
        finally:
            module.POLICY = saved
        if rc != 1:
            failures.append("a POLICY parity break expected exit 1")

        # 6. a manifest name in the "Not yet vendored" paragraph -> finding.
        deferred = tmp / "deferred"
        _write_fixture(deferred, readme=_README.replace(
            "Google SAIF and OWASP SCVS", "Google SAIF, OWASP SCVS, and Fixture Full Framework"))
        if run_quiet(deferred) != 1:
            failures.append("a vendored name in the deferred paragraph expected exit 1")

        # 6b-6f, 6h-6j. the deferred fact in the forms a first-match, after-the-phrase, case-sensitive
        #        read missed; each must be a finding (exit 1). All but 6f pass that read.
        prose = "Not yet vendored (their keys stay inert until a manifest lands): Google SAIF and OWASP SCVS.\n"
        for label, readme in (
                ("6b table row, name cell before the status cell", _README.replace(prose, (
                    "| Framework | Status |\n| --- | --- |\n"
                    "| Fixture Full Framework | Not yet vendored |\n| Google SAIF | Not yet vendored |\n"))),
                ("6c second deferred paragraph", _README + (
                    "\nA later note, also Not yet vendored: Fixture Full Framework.\n")),
                ("6d case variant", _README.replace(prose, (
                    "NOT YET VENDORED (their keys stay inert until a manifest lands): Google SAIF, OWASP "
                    "SCVS and Fixture Full Framework.\n"))),
                ("6e phrase wrapped across a line", _README.replace(prose, (
                    "Not yet\nvendored (their keys stay inert until a manifest lands): Google SAIF and "
                    "Fixture Full Framework.\n"))),
                ("6f header row marking the whole table", _README.replace(prose, (
                    "| Not yet vendored | Reason |\n| --- | --- |\n| Google SAIF | licence |\n"
                    "| Fixture Full Framework | none |\n"))),
                ("6h name on a paragraph line before the phrase", _README.replace(prose, (
                    "Google SAIF and Fixture Full Framework are both\nNot yet vendored; their keys stay "
                    "inert.\n"))),
                ("6i name wrapped across a line", _README.replace(prose, (
                    "Not yet vendored (their keys stay inert until a manifest lands): Google SAIF and Fixture "
                    "Full\nFramework.\n"))),
                ("6j name in another case", _README.replace(prose, (
                    "Not yet vendored (their keys stay inert until a manifest lands): Google SAIF and FIXTURE "
                    "FULL FRAMEWORK.\n"))),
                ("6k table body row without outer pipes", _README.replace(prose, (
                    "| Not yet vendored | Reason |\n| --- | --- |\nFixture Full Framework | none\n"))),
                ("6l table delimiter row without outer pipes", _README.replace(prose, (
                    "| Not yet vendored | Reason |\n--- | ---\n| Fixture Full Framework | none |\n"))),
                ("6m heading whose section lists the name", _README.replace(prose, (
                    "### Not yet vendored\n\n- Fixture Full Framework\n"))),
                ("6p hyphenated phrase", _README.replace(prose, (
                    "Not-yet-vendored: Fixture Full Framework.\n"))),
                ("6r table body row with no pipe at all", _README.replace(prose, (
                    "| Not yet vendored | Reason |\n| --- | --- |\n| Google SAIF | none |\n"
                    "Fixture Full Framework\n"))),
                ("6s pipe-free row under a pipe-light header", _README.replace(prose, (
                    "Not yet vendored | Reason\n--- | ---\nFixture Full Framework\n"))),
                ("6t pipe-free row under a one-column header", _README.replace(prose, (
                    "Not yet vendored |\n--- |\nFixture Full Framework\n"))),
                ("6u lazy list continuation", _README.replace(prose, (
                    "- Not yet vendored: Google SAIF and\nFixture Full Framework.\n"))),
                ("6v name split across a lazy continuation", _README.replace(prose, (
                    "- Not yet vendored: Google SAIF and Fixture Full\nFramework.\n"))),
                ("6x emphasis-split phrase", _README.replace(prose, (
                    "Not *yet* vendored: Fixture Full Framework.\n"))),
                ("6zb disclosed over-refusal (a name elsewhere in the same section)",
                 _README + "\nFixture Full Framework is vendored and mapped.\n")):
            case = tmp / ("deferred-" + label.split()[0])
            _write_fixture(case, readme=readme)
            if run_quiet(case) != 1:
                failures.append("a vendored name listed as not yet vendored ({}) expected exit 1".format(label))

        # 6g. a vendored name in a SECTION that does not carry the phrase stays clean (the section
        #     read is bounded by headings; the same text without the heading is case 6zb's disclosed
        #     over-refusal finding).
        scoped = tmp / "deferred-scoped"
        _write_fixture(scoped, readme=_README +
                       "\n## Vendored\n\nFixture Full Framework is vendored and mapped.\n")
        if run_quiet(scoped) != 0:
            failures.append("a vendored name in a section without the phrase expected exit 0")

        # 6q. a heading's section ends at the next heading of the same or a higher level: a vendored
        #     name after that boundary stays clean (the heading read is scoped, not greedy to the end).
        scoped_heading = tmp / "deferred-6q"
        _write_fixture(scoped_heading, readme=_README.replace(prose,
            "### Not yet vendored\n\n- Google SAIF\n\n### Vendored\n\nFixture Full Framework is mapped.\n"))
        if run_quiet(scoped_heading) != 0:
            failures.append("a vendored name after the next same-level heading expected exit 0")

        # 6zc-6zg. arrangements the section reader cannot prove it bounds are refused (exit 2, naming
        #     the file and line), never read through a false heading's truncation. Each offending line
        #     is line 13 of its fixture README by construction; markdown-it renders none of the '#'
        #     lines as a heading and keeps the vendored name in the same section, so a truncating
        #     read passes each body clean.
        for label, readme in (
                ("6zc list fence with a later independent fence", _README.replace(prose, (
                    "## Not yet vendored\n\n- ```\n  # example\n  ```\n\n"
                    "- Fixture Full Framework\n\n```text\nexample\n```\n"))),
                ("6zd list-item fences", _README.replace(prose, (
                    "Not yet vendored:\n\n- ```text\n  # one\n  ```\n- ```text\n  # two\n  ```\n"
                    "- Fixture Full Framework\n"))),
                ("6ze HTML comment block", _README.replace(prose, (
                    "Not yet vendored:\n\n<!--\n# maintainer note\n-->\n\n"
                    "- Fixture Full Framework\n"))),
                ("6zf pre block", _README.replace(prose, (
                    "Not yet vendored:\n\n<pre>\n# example\n</pre>\n\n"
                    "- Fixture Full Framework\n"))),
                ("6zg four-space-indented fence", _README.replace(prose, (
                    "Not yet vendored:\n\n    ```\n    # fake\n    ```\n\n"
                    "- Fixture Full Framework\n")))):
            case = tmp / ("deferred-" + label.split()[0])
            _write_fixture(case, readme=readme)
            rc, out = run_capture(case)
            if rc != 2 or "standards README:13:" not in out:
                failures.append("an unbounded section arrangement ({}) expected exit 2 naming "
                                "standards README:13 (got {}: {!r})".format(label, rc, out))

        # 6zi-6zn. arrangements above or at the section start are refused (exit 2, naming the file
        #     and line): the structure scan covers the whole text up to the section's end boundary,
        #     so a false heading manufactured by a container opened ABOVE the section (which a
        #     per-section scan missed: the mis-started section no longer contains the opener) is
        #     caught at its opener, and a '#' line that is not a heading in the accepted grammar
        #     (a U+00A0 separator, no separator) is refused rather than read as a boundary.
        for label, lineno, readme in (
                ("6zi list fence manufacturing the section-start heading", 11, _README.replace(prose, (
                    "- ```\n  ## a\n  not yet vendored\n  ## b\n  ```\n\n"
                    "- Fixture Full Framework\n\n```text\nexample\n```\n"))),
                ("6zj HTML block manufacturing the section-start heading", 11, _README.replace(prose, (
                    "<div>\n## a\nnot yet vendored\n## b\nFixture Full Framework\n</div>\n"))),
                ("6zk list fence above the phrase with a later independent fence", 13,
                 _README.replace(prose, (
                    "- Fixture Full Framework\n\n- ```text\n  # example\n  ```\n\n"
                    "Not yet vendored.\n\n```text\nexample\n```\n"))),
                ("6zl list-item HTML manufacturing the end boundary", 13, _README.replace(prose, (
                    "Not yet vendored:\n\n- <pre>\n  # example\n  </pre>\n\n"
                    "- Fixture Full Framework\n"))),
                ("6zm U+00A0-separated '#' line (not a heading in the accepted grammar)", 13,
                 _README.replace(prose, (
                    "Not yet vendored:\n\n#\u00a0example\n\n- Fixture Full Framework\n"))),
                ("6zn separator-less '#' line (pins the '#'-not-a-heading refusal)", 13,
                 _README.replace(prose, (
                    "Not yet vendored:\n\n#example\n\n- Fixture Full Framework\n")))):
            case = tmp / ("deferred-" + label.split()[0])
            _write_fixture(case, readme=readme)
            rc, out = run_capture(case)
            if rc != 2 or "standards README:{}:".format(lineno) not in out:
                failures.append("a structure arrangement above or at the section start ({}) expected "
                                "exit 2 naming standards README:{} (got {}: {!r})".format(
                                    label, lineno, rc, out))

        # 6n, 6o, 6w, 6y, 6z, 6za, 6zq-6zs. lines outside the POSITIVE deferred-section grammar
        #     (any blockquote, any fence, any nested or indented container between the phrase and
        #     the section end) are refused (exit 2, naming the file and line), never read through
        #     container stripping and never a clean pass through a mis-bounded section. 6zq-6zs are
        #     three counter-examples (6zq and 6zr exit through the FENCE check, the
        #     unterminated-fence refusal naming the opening line, never this span grammar; 6zs
        #     exits through the grammar): on the reader this replaces, 6zq and 6zs each passed
        #     CLEAN (a fence-state inversion through a backtick info string; a stripped '> # fake'
        #     read as the end boundary) and 6zr passed clean through a U+00A0 pseudo-close; 6n, 6o,
        #     6w, 6y, 6z and 6za were findings that the grammar now refuses instead (fail-closed,
        #     never silently narrowed).
        for label, marker, readme in (
                ("6n blockquote-wrapped phrase", "standards README:11:", _README.replace(prose, (
                    "> Not yet\n> vendored: Fixture Full Framework.\n"))),
                ("6o nested list under the phrase item", "standards README:13:", _README.replace(
                    prose, "- Not yet vendored:\n\n  - Fixture Full Framework\n")),
                ("6w blockquoted lazy list continuation", "standards README:11:", _README.replace(
                    prose, "> - Not yet vendored: Google SAIF and\n> Fixture Full Framework.\n")),
                ("6y indented list after a lazy continuation", "standards README:14:",
                 _README.replace(prose, (
                    "- Not yet vendored:\ncontinued explanation\n\n  - Fixture Full Framework\n"))),
                ("6z indented list under an indented phrase line", "standards README:14:",
                 _README.replace(prose, (
                    "- Status:\n  Not yet vendored:\n\n  - Fixture Full Framework\n"))),
                ("6za fence inside the deferred section", "standards README:13:",
                 _README.replace(prose, (
                    "### Not yet vendored\n\n```text\n# example\n```\n\n"
                    "- Fixture Full Framework\n"))),
                ("6zq backtick fence with a backtick info string (not a CommonMark opener)",
                 "unterminated code fence opened at line 18", _README.replace(prose, (
                    "Not yet vendored:\n\n```x`y\n```\n# fake\n```\n"
                    "- Fixture Full Framework\n```\n"))),
                ("6zr U+00A0 after a closing fence is content, not a close",
                 "unterminated code fence opened at line 18", _README.replace(prose, (
                    "Not yet vendored:\n\n~~~\n~~~\u00a0\n# fake\n~~~\n"
                    "- Fixture Full Framework\n~~~\n"))),
                ("6zs list-item blockquote holding an HTML block", "standards README:13:",
                 _README.replace(prose, (
                    "Not yet vendored:\n\n- > <pre>\n  > # fake\n  > </pre>\n\n"
                    "- Fixture Full Framework\n")))):
            case = tmp / ("deferred-" + label.split()[0])
            _write_fixture(case, readme=readme)
            rc, out = run_capture(case)
            if rc != 2 or marker not in out:
                failures.append("a line outside the deferred-section grammar ({}) expected exit 2 "
                                "with {!r} (got {}: {!r})".format(label, marker, rc, out))

        # 6zt. the FORMER decided boundary, now REFUSED: a blockquoted ATX heading after the
        #     phrase is a heading only in the blockquote-stripped reading (markdown-it keeps it
        #     inside the quote), so the raw and stripped heading readings disagree at the boundary
        #     line and the section cannot be bounded (exit 2, naming that line); the reader this
        #     replaces read the stripped heading as the section end and passed the name after it
        #     CLEAN (exit 0).
        quoted_heading = tmp / "deferred-6zt"
        _write_fixture(quoted_heading, readme=_README.replace(prose, (
            "Not yet vendored:\n\n> # Aside\n\n- Fixture Full Framework\n")))
        rc, out = run_capture(quoted_heading)
        if rc != 2 or "standards README:13:" not in out or "disagree" not in out:
            failures.append("a blockquoted heading after the phrase expected exit 2 naming "
                            "standards README:13 with the heading-disagreement diagnostic "
                            "(got {}: {!r})".format(rc, out))

        # 6zw-6zx. decoy headings ABOVE the phrase, manufactured by blockquote stripping: a
        #     list-quote HTML line whose quoted '###### fake' is a heading only in the stripped
        #     reading (6zw), and a blockquoted backtick fence that inverts the stripped reading's
        #     fence state so a bare '###### fake' is a heading in exactly one reading (6zx). Each
        #     shrank the section in the stripped reading alone and passed the planted name CLEAN
        #     (exit 0) on the reader this replaces; the heading-agreement rule refuses both
        #     (exit 2, naming the first disagreeing line).
        for label, lineno, readme in (
                ("6zw list-quoted decoy heading above the phrase", 14, _README.replace(prose, (
                    "## Deferred\n\n- > <pre>\n  > ###### fake\n\n"
                    "Not yet vendored: Google SAIF.\n\n### Detail\n\n"
                    "- Fixture Full Framework\n"))),
                ("6zx blockquoted fences inverting the stripped fence state", 15,
                 _README.replace(prose, (
                    "## Deferred\n\n> ```\n```\n###### fake\n```\n> ```\n\n"
                    "Not yet vendored: Google SAIF.\n\n### Detail\n\n"
                    "- Fixture Full Framework\n")))):
            case = tmp / ("deferred-" + label.split()[0])
            _write_fixture(case, readme=readme)
            rc, out = run_capture(case)
            if rc != 2 or "standards README:{}:".format(lineno) not in out or "disagree" not in out:
                failures.append("a stripped-versus-raw heading disagreement ({}) expected exit 2 "
                                "naming standards README:{} (got {}: {!r})".format(
                                    label, lineno, rc, out))

        # 6zy-6zz, 6zza-6zze. lines INSIDE the deferred span that CommonMark reads as a non-text
        #     block (or, for the 6zzd tab gap, a shape CommonMark reads as a plain item that this
        #     grammar refuses as a disclosed conservative over-refusal) but the earlier grammar
        #     accepted as plain text, each now refused (exit 2,
        #     naming the line). On the reader this replaces, 6zy passed CLEAN (the renderer's
        #     setext section continues past the ATX heading the reader stops at, so the name was
        #     unread) and the others were findings read through text the renderer shows as code, a
        #     break or a heading.
        for label, lineno, readme in (
                ("6zy setext underline under the phrase", 12, _README.replace(prose, (
                    "Not yet vendored: Google SAIF.\n===\n\n## Later\n\n"
                    "- Fixture Full Framework\n"))),
                ("6zz thematic break inside the span", 13, _README.replace(prose, (
                    "Not yet vendored:\n\n---\n\n- Fixture Full Framework\n"))),
                ("6zza indented code after a blank line", 13, _README.replace(prose, (
                    "Not yet vendored:\n\n    indented code\n\n- Fixture Full Framework\n"))),
                ("6zzb indented text directly under a heading", 12, _README.replace(prose, (
                    "### Not yet vendored\n    indented code\n\n- Fixture Full Framework\n"))),
                ("6zzc list-held code (five spaces after the marker)", 13, _README.replace(prose, (
                    "Not yet vendored:\n\n-     code in a list\n\n"
                    "- Fixture Full Framework\n"))),
                ("6zzd a tab between the list marker and the text (a conservative over-refusal: "
                 "CommonMark reads it as a plain item)", 13, _README.replace(prose, (
                    "Not yet vendored:\n\n-\tFixture note\n\n- Fixture Full Framework\n"))),
                ("6zze list-held heading", 13, _README.replace(prose, (
                    "Not yet vendored:\n\n- # Nested heading\n\n"
                    "- Fixture Full Framework\n")))):
            case = tmp / ("deferred-" + label.split()[0])
            _write_fixture(case, readme=readme)
            rc, out = run_capture(case)
            if rc != 2 or "standards README:{}:".format(lineno) not in out:
                failures.append("a span line outside the tightened grammar ({}) expected exit 2 "
                                "naming standards README:{} (got {}: {!r})".format(
                                    label, lineno, rc, out))

        # 6zzf-6zzj. an indented '#' line (first non-space character '#', any leading
        #     whitespace), anywhere from the file start to the section end, the end-boundary
        #     line included (6zzi: an indented heading AT the boundary, which CommonMark can
        #     nest in the list item above it, is refused BEFORE it may bound the section), is
        #     refused (exit 2,
        #     naming the line): CommonMark can read it as a heading NESTED IN A LIST ITEM, which
        #     this reader does not model but HEADING would read as a top-level section boundary;
        #     a LEGAL indented top-level heading is over-refused by the same rule, a disclosed
        #     conservative refusal. On the reader this replaces, 6zzf and 6zzg each read as a
        #     top-level heading that STARTED the section (a mis-started span), while 6zzi read
        #     as the section's false END boundary (excluding the span's content); each fixture
        #     passed clean (exit 0). Two controls: 6zzh (a blockquote line, then a top-level
        #     column-0 heading, ABOVE the phrase) and 6zzj (an indented '#' line AFTER the
        #     section's end boundary, outside the refusal's span) each keep their clean
        #     result (exit 0).
        for label, lineno, readme in (
                ("6zzf list item then a two-space-indented heading", 12, _README.replace(prose, (
                    "- x\n  ###### note\n\n" + prose))),
                ("6zzg ordered item, blank line, then a three-space-indented heading", 13,
                 _README.replace(prose, ("1. x\n\n   ###### note\n\n" + prose))),
                ("6zzi indented heading AT the end boundary (list-nested in CommonMark)", 14,
                 _README.replace(prose, ("### Not yet vendored\n\n- item\n   ## fake\n\n"
                                         "- Fixture Full Framework\n")))):
            case = tmp / ("deferred-" + label.split()[0])
            _write_fixture(case, readme=readme)
            rc, out = run_capture(case)
            if rc != 2 or "standards README:{}:".format(lineno) not in out:
                failures.append("an indented '#' line ({}) expected exit 2 naming "
                                "standards README:{} (got {}: {!r})".format(
                                    label, lineno, rc, out))
        control = tmp / "deferred-6zzh"
        _write_fixture(control, readme=_README.replace(prose, ("> x\n###### note\n\n" + prose)))
        if run_quiet(control) != 0:
            failures.append("a blockquote line then a top-level column-0 heading (the 6zzh "
                            "control) expected exit 0 (unchanged result)")
        after_end = tmp / "deferred-6zzj"
        _write_fixture(after_end, readme=_README.replace(prose, (
            "### Not yet vendored\n\n- item\n\n### Detail\n\n  ###### note\n\n"
            "- Fixture Full Framework\n")))
        if run_quiet(after_end) != 0:
            failures.append("an indented '#' line AFTER the section's end boundary (the 6zzj "
                            "control) expected exit 0 (the refusal's span ends at the "
                            "boundary line)")

        # 6zu. the backtick-info opener rule ABOVE the section: a ```x`y line is NOT a fence
        #     opener (CommonMark forbids a backtick in a backtick fence's info string), so it is a
        #     fence run outside a line-start fence and is refused (exit 2, naming the line); on the
        #     reader this replaces it OPENED a fence that never closed, so that reader exited 2
        #     only as an unterminated fence crediting the pseudo-fence line as an opener.
        info_fence = tmp / "deferred-6zu"
        _write_fixture(info_fence, readme=_README.replace(prose, (
            "```x`y\n# fake\n\n## Deferred\n\n"
            "Not yet vendored: Fixture Full Framework.\n")))
        rc, out = run_capture(info_fence)
        if rc != 2 or "standards README:11:" not in out:
            failures.append("a backtick-info pseudo-fence above the section expected exit 2 naming "
                            "standards README:11 (got {}: {!r})".format(rc, out))

        # 6zv. the U+00A0 close rule ABOVE the section: a pseudo-closing tilde line followed by
        #     U+00A0 is CONTENT (only spaces and tabs may follow a close), the later bare fence line
        #     is the real close, and the section below is read (its name is the finding, exit 1); on
        #     the reader this replaces, str.strip read the U+00A0 line as the close, the bare line
        #     reopened a fence, and the fixture failed as an unterminated fence (exit 2).
        nbsp_close = tmp / "deferred-6zv"
        _write_fixture(nbsp_close, readme=_README.replace(prose, (
            "~~~\n~~~\u00a0\n# fake\n~~~\n\n## Deferred\n\n"
            "Not yet vendored: Fixture Full Framework.\n")))
        rc, out = run_capture(nbsp_close)
        if rc != 1 or "standards README:18:" not in out:
            failures.append("a U+00A0 pseudo-close above the section expected exit 1 with the name "
                            "found at standards README:18 (got {}: {!r})".format(rc, out))

        # 6zh. a fence ABOVE the section whose pseudo-closing line carries trailing words does not
        #     close (the closing fence carries nothing but spaces or tabs): the later bare fence
        #     line is the real close, the section below is read, and the name in it is a finding
        #     (exit 1). Dropping the nothing-else rule closes the fence at the pseudo-closing line
        #     and reopens an unterminated fence at the bare line (exit 2). Fences INSIDE the
        #     deferred span are 6za's refusal.
        pseudo_close = tmp / "deferred-6zh"
        _write_fixture(pseudo_close, readme=_README.replace(prose, (
            "```text\ncontent\n``` unrelated words\n```\n\n### Not yet vendored\n\n"
            "- Fixture Full Framework\n")))
        rc, out = run_capture(pseudo_close)
        if rc != 1 or "standards README:16:" not in out:
            failures.append("a pseudo-closing fence with trailing words above the section expected "
                            "exit 1 with the name found at standards README:16 (got {}: {!r})"
                            .format(rc, out))

        # 6zp. a single-line HTML comment above the section (the real README's generated roster
        #     markers carry this exact shape) is NOT over-refused, and the section below it is
        #     proven READ: the fixture plants a vendored name in that section, so the expected
        #     result is the FINDING (exit 1, naming the phrase line), which an over-refusal (exit
        #     2) and an unread section (exit 0) would each miss.
        roster = tmp / "deferred-6zp"
        _write_fixture(roster, readme=_README.replace(prose, (
            "<!-- ROSTER:BEGIN (generated) -->\n\n## Deferred\n\n"
            "Not yet vendored (their keys stay inert until a manifest lands): Google SAIF and "
            "Fixture Full Framework.\n")))
        rc, out = run_capture(roster)
        if rc != 1 or "standards README:15:" not in out:
            failures.append("a single-line HTML comment above the section expected exit 1 with the "
                            "planted name found at standards README:15 (no over-refusal, the "
                            "section read; got {}: {!r})".format(rc, out))

        # 7. a subset manifest rendered "3 of 3" -> finding (the CWE regression case).
        denom = tmp / "denom"
        _write_fixture(denom, page=_PAGE.replace(
            "<td>2 referenced (curated subset)</td>", "<td>3 of 3</td>"))
        if run_quiet(denom) != 1:
            failures.append("a subset manifest rendered 'N of M' expected exit 1")

        # 8. a missing site/mappings.html -> exit 2 (fail-closed, not a skip).
        nopage = tmp / "nopage"
        _write_fixture(nopage)
        (nopage / "site" / "mappings.html").unlink()
        if run_quiet(nopage) != 2:
            failures.append("a missing site/mappings.html expected exit 2 (fail-closed)")

        # 9. an unreadable standards dir (chmod 0) -> exit 2. Where the runner bypasses DAC (root), the
        #    permission failure is injected through the module's load_manifests binding instead, so the
        #    guarded exit-2 observation is made on every runner, never skipped.
        unread = tmp / "unread"
        _write_fixture(unread)
        unreadable_std = unread / ".aiqt" / "standards"
        os.chmod(unreadable_std, 0)
        if os.access(unreadable_std, os.R_OK):
            saved_load = module.load_manifests
            def _deny_load(path, *, root, _real=saved_load):
                raise PermissionError(13, "injected permission denied", str(path))
            module.load_manifests = _deny_load
            try:
                rc = run_quiet(unread)
            finally:
                module.load_manifests = saved_load
        else:
            rc = run_quiet(unread)
        if rc != 2:
            failures.append("an unreadable standards dir expected exit 2 (fail-closed)")
        os.chmod(unreadable_std, 0o755)
        unreadable_std = None

        # 9b. a manifest that fails INSIDE the real loader on every runner (no seam, no chmod): a
        #     dangling-symlink *.toml raises inside load_manifests' open() -> exit 2 (fail-closed).
        unread_real = tmp / "unread-real"
        _write_fixture(unread_real)
        os.symlink("no-such-manifest.toml", str(unread_real / ".aiqt" / "standards" / "ghost.toml"))
        rc, out = run_capture(unread_real)
        if rc != 2 or "ghost.toml" not in out:
            failures.append("a dangling-symlink manifest expected exit 2 from inside the real loader "
                            "naming ghost.toml (fail-closed; got {}: {!r})".format(rc, out))

        # 10. a manifest without `catalogue`, and one with `catalogue = "partial"` -> exit 2.
        nocat = tmp / "nocat"
        _write_fixture(nocat)
        (nocat / ".aiqt" / "standards" / "full.toml").write_text(
            _MANIFEST.format(k="full", name="Fixture Full Framework", pub="OWASP Foundation",
                             ed="1.0", kind="control", cat="full").replace(
                'catalogue = "full"\n', ""), encoding="utf-8")
        if run_quiet(nocat) != 2:
            failures.append("a manifest without catalogue expected exit 2 (fail-closed)")
        badcat = tmp / "badcat"
        _write_fixture(badcat)
        (badcat / ".aiqt" / "standards" / "full.toml").write_text(
            _MANIFEST.format(k="full", name="Fixture Full Framework", pub="OWASP Foundation",
                             ed="1.0", kind="control", cat="partial"), encoding="utf-8")
        if run_quiet(badcat) != 2:
            failures.append("a manifest with catalogue='partial' expected exit 2 (fail-closed)")

        # 11. a publisher with no FAMILY_ALIAS entry -> exit 2.
        noalias = tmp / "noalias"
        _write_fixture(noalias, manifests=[
            ("full", "Fixture Full Framework", "Unlisted Publisher", "1.0", "control", "full")])
        if run_quiet(noalias) != 2:
            failures.append("a publisher with no family alias expected exit 2 (fail-closed)")

        # 12. a subset reverse-view coverage mutated to "N of M" -> finding. The denominator lands in the
        #     reverse-view <p>, on a different line from the framework name in the <summary>, so this
        #     fails only because the check is structure-aware, not same-line.
        revsub = tmp / "revsub"
        _write_fixture(revsub, page=_PAGE.replace(
            "2 identifiers referenced from a curated subset of the edition", "2 of 61"))
        if run_quiet(revsub) != 1:
            failures.append("a subset reverse-view coverage of 'N of M' expected exit 1")

        # 13. a subset framework carrying a NON-NULL ids_total in the JSON export -> finding.
        jsondenom = tmp / "jsondenom"
        _write_fixture(jsondenom, json_export=_JSON.replace('"ids_total": null}', '"ids_total": 61}'))
        if run_quiet(jsondenom) != 1:
            failures.append("a subset framework carrying a non-null ids_total expected exit 1")

        # 13c. a subset framework MISSING ids_total (the key must be present and null) -> finding.
        jsonabsent = tmp / "jsonabsent"
        _write_fixture(jsonabsent, json_export=_JSON.replace(', "ids_total": null}', '}'))
        if run_quiet(jsonabsent) != 1:
            failures.append("a subset framework missing ids_total expected exit 1 (must be present and null)")

        # 13b. a subset framework entry that is valid JSON but NOT an object (a string) -> exit 2
        #      (malformed structure fails closed, not a clean pass).
        jsonstr = tmp / "jsonstr"
        _write_fixture(jsonstr, json_export=_JSON.replace(
            '"subset": {"name": "Fixture Subset Framework", "catalogue": "subset", "ids_cited": 2, '
            '"ids_total": null}',
            '"subset": "malformed-but-valid-json"'))
        if run_quiet(jsonstr) != 2:
            failures.append("a non-object subset framework entry expected exit 2 (fail-closed)")

        # 14. a missing site/downloads/mappings.json -> exit 2 (fail-closed, not a skip).
        nojson = tmp / "nojson"
        _write_fixture(nojson)
        (nojson / "site" / "downloads" / "mappings.json").unlink()
        if run_quiet(nojson) != 2:
            failures.append("a missing mappings.json expected exit 2 (fail-closed)")

        # 14b. an unterminated code fence: the section reader cannot bound a section through it, so it
        #     fails closed (exit 2), never a partial or truncated section read.
        unfenced = tmp / "unfenced"
        _write_fixture(unfenced, readme=_README + "\n```text\nnever closed\n")
        rc, out = run_capture(unfenced)
        if rc != 2 or "standards README: unterminated code fence opened at line 13" not in out:
            failures.append("an unterminated code fence expected exit 2 naming standards README and "
                            "line 13 (got {}: {!r})".format(rc, out))

        # 15. a README landing-page count changed (the proof line now claims 999 rules) -> finding.
        #     MUTATION: removing the check_readme_counts call from run() makes this case pass clean
        #     and the test fails.
        badcount = tmp / "badcount"
        _write_fixture(badcount, landing=_LANDING.replace("**3 rules.", "**999 rules.", 1))
        if run_quiet(badcount) != 1:
            failures.append("a README proof-line count mismatch expected exit 1")

        # 16. a reworded anchored count wording (both lines carrying it) -> finding: the wording is
        #     REQUIRED, so a reword cannot silently drop a count from coverage.
        reword = tmp / "reword"
        _write_fixture(reword, landing=_LANDING.replace("CI gates that check this repository",
                                                        "CI gates that verify this repository"))
        if run_quiet(reword) != 1:
            failures.append("a reworded anchored count wording expected exit 1")

        # 17. every captured figure, drifted alone to 999 at its designated line -> finding.
        drifts = (
            ("proof-rules", "**3 rules.", "**999 rules."),
            ("proof-gates", "2 CI gates that check this repository. ",
             "999 CI gates that check this repository. "),
            ("proof-hooks", "2 Claude Code hooks shipped as an opt-in plugin. ",
             "999 Claude Code hooks shipped as an opt-in plugin. "),
            ("proof-frameworks", "Mapped to 2 frameworks", "Mapped to 999 frameworks"),
            ("rules-row-rules", "3 clear, single-behaviour rules", "999 clear, single-behaviour rules"),
            ("controls-gates", "2 CI gates that check this repository and",
             "999 CI gates that check this repository and"),
            ("controls-hooks", "and 2 Claude Code hooks", "and 999 Claude Code hooks"),
            ("controls-blocking", "(1 blocking", "(999 blocking"),
            ("controls-warning", "1 warning)", "999 warning)"),
            ("enforced-numerator", "Today 1 of the", "Today 999 of the"),
            ("enforced-denominator", "of the 3 rules are linked", "of the 999 rules are linked"),
            ("crosswalk-frameworks", "2 pinned external control catalogues",
             "999 pinned external control catalogues"),
        )
        for tag, old, new in drifts:
            if _LANDING.count(old) != 1:
                failures.append("count-drift fixture {}: its target must appear exactly once in "
                                "_LANDING".format(tag))
                continue
            drifted = tmp / ("drift-" + tag)
            _write_fixture(drifted, landing=_LANDING.replace(old, new, 1))
            if run_quiet(drifted) != 1:
                failures.append("count drift {} expected exit 1".format(tag))

        # 18. each wording, broken alone -> a missing-anchored-wording finding (per-pattern coverage;
        #     the enforcement break restores the retired "automated enforcement" form, which must no
        #     longer count as the anchored wording).
        rewords = (
            ("proof", "Mapped to ", "Linked to "),
            ("rules-row", "clear, single-behaviour rules", "clear single-behaviour rules"),
            ("controls-row", " blocking, ", " hard-blocking, "),
            ("enforcement", "are linked to at least one shipped gate or hook",
             "have automated enforcement"),
            ("crosswalk", "pinned external control catalogues", "pinned external catalogues"),
        )
        for tag, old, new in rewords:
            if _LANDING.count(old) != 1:
                failures.append("wording-removal fixture {}: its target must appear exactly once in "
                                "_LANDING".format(tag))
                continue
            broken = tmp / ("reword-" + tag)
            _write_fixture(broken, landing=_LANDING.replace(old, new, 1))
            if run_quiet(broken) != 1:
                failures.append("broken wording {} expected exit 1".format(tag))

        # 19. unsupported numeric syntax at each wording (the "1,132" and "-35" review forms) ->
        #     finding: the field no longer parses as a whole standalone number, so the designated line
        #     fails to match, which is itself a finding, never a partial-match pass.
        bad_numbers = (
            ("proof-separator", "**3 rules.", "**1,003 rules."),
            ("proof-sign", "**3 rules.", "**-3 rules."),
            ("rules-separator", "3 clear, single-behaviour rules",
             "1,003 clear, single-behaviour rules"),
            ("rules-sign", "3 clear, single-behaviour rules", "-3 clear, single-behaviour rules"),
            ("controls-separator", "2 CI gates that check this repository and",
             "1,002 CI gates that check this repository and"),
            ("controls-sign", "(1 blocking", "(-1 blocking"),
            ("enforced-separator", "Today 1 of the", "Today 1,001 of the"),
            ("enforced-sign", "Today 1 of the", "Today -1 of the"),
            ("crosswalk-separator", "2 pinned external control catalogues",
             "1,002 pinned external control catalogues"),
            ("crosswalk-sign", "2 pinned external control catalogues",
             "-2 pinned external control catalogues"),
        )
        for tag, old, new in bad_numbers:
            if _LANDING.count(old) != 1:
                failures.append("bad-number fixture {}: its target must appear exactly once in "
                                "_LANDING".format(tag))
                continue
            badnum = tmp / ("badnum-" + tag)
            _write_fixture(badnum, landing=_LANDING.replace(old, new, 1))
            if run_quiet(badnum) != 1:
                failures.append("unsupported numeric form {} expected exit 1".format(tag))

        # 20. a TRUE explanatory count sentence outside the designated lines -> exit 0, one per
        #     anchored wording (the location binding; with any one pattern unanchored, its wrong-figure
        #     sentence below would be read as a second operative count and fail, so each aside holds a
        #     full wording with drifted figures).
        asides = (
            ("rules-aside",
             "\nIt would be incorrect to claim 999 clear, single-behaviour rules.\n"),
            ("enforcement-aside",
             "\nA later release may raise the figure; it would still be wrong to claim: Today 999 of "
             "the 3 rules are linked to at least one shipped gate or hook.\n"),
            ("proof-aside",
             "\nA stale draft once read: 999 rules. 999 CI gates that check this repository. 999 "
             "Claude Code hooks shipped as an opt-in plugin. Mapped to 999 frameworks.** That line "
             "was corrected before release.\n"),
            ("controls-aside",
             "\nNo release may again print 999 CI gates that check this repository and 999 Claude "
             "Code hooks shipped as an opt-in plugin (999 blocking, 999 warning) anywhere here.\n"),
            ("crosswalk-aside",
             "\nIt would be wrong to map the rules to 999 pinned external control catalogues.\n"),
        )
        for tag, sentence in asides:
            aside = tmp / ("aside-" + tag)
            _write_fixture(aside, landing=_LANDING + sentence)
            if run_quiet(aside) != 0:
                failures.append("explanatory sentence {} outside its designated line expected exit 0"
                                .format(tag))

        # 20b. a SECOND, contradictory enforcement figure later on the designated controls line ->
        #      finding: every occurrence of a wording on its designated line is read, so a duplicate
        #      with a drifted figure cannot hide behind a correct first occurrence.
        twice = tmp / "twice"
        _write_fixture(twice, landing=_LANDING.replace(
            "shipped gate or hook. ",
            "shipped gate or hook, and it would be wrong to print: Today 999 of the 3 rules are "
            "linked to at least one shipped gate or hook. ", 1))
        if run_quiet(twice) != 1:
            failures.append("a second contradictory count on the designated line expected exit 1")

        # 20c. the designated-line binding across a line break (the newline-split review vectors): the
        #      enforcement sentence or the crosswalk coverage moved to the NEXT line is no longer on
        #      its designated line, so its absence is a finding, never a match spanning the break.
        splitenf = tmp / "splitenf"
        _write_fixture(splitenf, landing=_LANDING.replace(". Today ", ".\nToday ", 1))
        if run_quiet(splitenf) != 1:
            failures.append("the enforcement sentence split onto the next line expected exit 1")
        splitcross = tmp / "splitcross"
        _write_fixture(splitcross, landing=_LANDING.replace(
            "Mappings from the rules to ", "Mappings from the rules\nto ", 1))
        if run_quiet(splitcross) != 1:
            failures.append("the crosswalk coverage split onto the next line expected exit 1")

        # 20d. the proof line REPLACED and the full wording moved to a later bold numeric line -> a
        #      missing-anchored-wording finding: the designated proof line is the FIRST bold-digit
        #      line, and the wording must open it, so a relocated proof line can never stand in.
        proofmoved = tmp / "proofmoved"
        _write_fixture(proofmoved, landing=_LANDING.replace(
            "**3 rules.", "**Proof removed.\n**2026 note: 3 rules.", 1))
        if run_quiet(proofmoved) != 1:
            failures.append("a replaced proof line with its wording on a later bold line expected "
                            "exit 1")

        # 20d2. a bold digit line INSERTED ABOVE the real proof line: the designated proof line is
        #       the FIRST bold-digit line, so the proof wording is missing at its designated line,
        #       asserted by the finding's own message. The inserted line's digit run also fails the
        #       numeric sweep, so the message assertion, not the exit code, is what turns red when
        #       the first-line rule (starts[:1]) is removed.
        proofpushed = tmp / "proofpushed"
        _write_fixture(proofpushed, landing=_LANDING.replace(
            "**3 rules.", "**2026 edition.**\n**3 rules.", 1))
        code, out = run_out(proofpushed)
        if code != 1 or "anchored count wording not found at its designated line (proof line)" not in out:
            failures.append("a bold digit line inserted above the proof line expected exit 1 with "
                            "the missing-proof-wording finding")

        # 20e. a numeric run on a designated line outside every anchored wording -> finding (the
        #      numeric sweep): a signed duplicate beside the valid wording, a reworded enforcement
        #      figure, a thousands-separated duplicate after the valid split, and the reviewed
        #      evasive forms the retired noun list missed: bare and draft nouns (gates, hooks,
        #      rules, warnings, total rules, enforced rules, pinned catalogues), singular nouns
        #      (rule., CI gate, Claude Code hook), a scientific-notation figure (1e3), a doubled
        #      space, a no-break space, bold markup, fullwidth and Arabic-Indic digit runs, and
        #      non-decimal numeric characters (superscript, subscript, circled and dingbat
        #      numerals, a Roman numeral character, a vulgar fraction, CJK numerals: the sweep
        #      reads every character unicodedata.numeric() gives a value, never decimal digits
        #      alone). The clean fixture of case 1 is the positive control: a true designated
        #      line, every numeric run inside an anchored wording, passes.
        loose_vectors = (
            ("dupmalformed", "3 clear, single-behaviour rules",
             "-3 clear, single-behaviour rules; 3 clear, single-behaviour rules"),
            ("draftrules", "3 clear, single-behaviour rules across",
             "3 clear, single-behaviour rules (a draft said 999 rules) across"),
            ("enfreword", "shipped gate or hook. ",
             "shipped gate or hook. Today 999 of the 3 rules are enforced. "),
            ("parenrules", "Mapped to 2 frameworks.**", "Mapped to 2 frameworks.** (999 rules.)"),
            ("dupseparated", "(1 blocking, 1 warning).",
             "(1 blocking, 1 warning) (a draft said 1,001 blocking)."),
            ("draftgates", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (a draft said 999 gates)"),
            ("drafthooks", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (a draft said 999 hooks)"),
            ("scinum", "Mapped to 2 frameworks.**", "Mapped to 2 frameworks.** (1e3 rules)"),
            ("doublespace", "Mapped to 2 frameworks.**", "Mapped to 2 frameworks.** (999  rules)"),
            ("totalrules", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (999 total rules)"),
            ("cigate", "Mapped to 2 frameworks.**", "Mapped to 2 frameworks.** (999 CI gate)"),
            ("singularrule", "Mapped to 2 frameworks.**", "Mapped to 2 frameworks.** (999 rule.)"),
            ("enforcedrules", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (999 enforced rules)"),
            ("earlier", "shipped gate or hook. ",
             "shipped gate or hook. Earlier 999 of 132 rules were linked. "),
            ("nbsp", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (999\u00a0rules)"),
            ("boldrun", "Mapped to 2 frameworks.**", "Mapped to 2 frameworks.** (__999__ rules)"),
            ("warnings", "Mapped to 2 frameworks.**", "Mapped to 2 frameworks.** (999 warnings)"),
            ("pinnedcat", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (999 pinned catalogues)"),
            ("cchook", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (999 Claude Code hook)"),
            ("fullwidthrun", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (\uff19\uff19\uff19 rules)"),
            ("arabicindicrun", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (\u0669\u0669\u0669 rules)"),
            ("superscriptrun", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (\u2079\u2079\u2079 rules)"),
            ("subscriptrun", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (\u2089\u2089\u2089 rules)"),
            ("circledrun", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (\u2468\u2468\u2468 rules)"),
            ("dingbatnumeral", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (\u277e rules)"),
            ("romannumeral", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (\u2168 rules)"),
            ("vulgarfraction", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (\u00bd rules)"),
            ("cjknumerals", "Mapped to 2 frameworks.**",
             "Mapped to 2 frameworks.** (\u4e5d\u767e rules)"),
        )
        for tag, old, new in loose_vectors:
            if _LANDING.count(old) != 1:
                failures.append("loose-count fixture {}: its target must appear exactly once in "
                                "_LANDING".format(tag))
                continue
            loosed = tmp / ("loose-" + tag)
            _write_fixture(loosed, landing=_LANDING.replace(old, new, 1))
            if run_quiet(loosed) != 1:
                failures.append("a contradictory or malformed figure in another wording ({}) expected "
                                "exit 1".format(tag))

        # 20f. the disclosed residual witness: the sweep inspects source characters, so each named
        #      numeric-looking symbol that carries no Unicode numeric value (keycap ten U+1F51F,
        #      the hundred points symbol U+1F4AF, and each die face U+2680 to U+2685) on the
        #      designated proof line is NOT read and the page stays clean (exit 0). A sweep
        #      widened to read any of them turns its vector red, so the module docstring's
        #      disclosure must change with it. The anchor must occur exactly once in _LANDING
        #      first, so a fixture edit that drops it is a failure, never a vector that passes
        #      with its symbol never inserted.
        for tag, symbol in (("keycapten", "\U0001f51f"), ("hundredpoints", "\U0001f4af"),
                            ("dieface1", "\u2680"), ("dieface2", "\u2681"),
                            ("dieface3", "\u2682"), ("dieface4", "\u2683"),
                            ("dieface5", "\u2684"), ("dieface6", "\u2685")):
            if _LANDING.count("Mapped to 2 frameworks.**") != 1:
                failures.append("residual fixture {}: its anchor must appear exactly once in "
                                "_LANDING".format(tag))
                continue
            residual = tmp / ("residual-" + tag)
            _write_fixture(residual, landing=_LANDING.replace(
                "Mapped to 2 frameworks.**", "Mapped to 2 frameworks.** ({} rules)".format(symbol), 1))
            if run_quiet(residual) != 0:
                failures.append("a numeric-looking symbol with no Unicode numeric value ({}) on the "
                                "proof line expected exit 0 (the disclosed residual: the sweep "
                                "does not read it)".format(tag))

        # 21. missing or malformed count sources fail closed (exit 2), never a silent pass: a missing
        #     ENFORCEMENT.md, rules folder, or hooks manifest; a gates manifest that is not an array of
        #     tables (`gate = "ab"`) or whose entry lacks the counted field; and an all-wrong-platform
        #     manifest (nothing matches the page's predicate). A PARTIALLY off-platform gates manifest
        #     is a finding instead: the filtered count disagrees with the page's claim.
        noenf = tmp / "noenf"
        _write_fixture(noenf)
        (noenf / "ENFORCEMENT.md").unlink()
        if run_quiet(noenf) != 2:
            failures.append("a missing ENFORCEMENT.md expected exit 2 (fail-closed)")
        norules = tmp / "norules"
        _write_fixture(norules)
        shutil.rmtree(norules / ".aiqt" / "core" / "rules")
        if run_quiet(norules) != 2:
            failures.append("a missing rules folder expected exit 2 (fail-closed)")
        nohooks = tmp / "nohooks"
        _write_fixture(nohooks)
        (nohooks / ".aiqt" / "core" / "hooks" / "manifest.toml").unlink()
        if run_quiet(nohooks) != 2:
            failures.append("a missing hooks manifest expected exit 2 (fail-closed)")
        badgate = tmp / "badgate"
        _write_fixture(badgate)
        (badgate / ".aiqt" / "core" / "gates" / "manifest.toml").write_text(
            'gate = "ab"\n', encoding="utf-8")
        if run_quiet(badgate) != 2:
            failures.append("a gates manifest of gate = \"ab\" expected exit 2 (fail-closed)")
        nofield = tmp / "nofield"
        _write_fixture(nofield)
        (nofield / ".aiqt" / "core" / "gates" / "manifest.toml").write_text(
            '[[gate]]\nid = "fixture-gate-one"\n', encoding="utf-8")
        if run_quiet(nofield) != 2:
            failures.append("a gate entry with no platform field expected exit 2 (fail-closed)")
        offgate = tmp / "offgate"
        _write_fixture(offgate)
        (offgate / ".aiqt" / "core" / "gates" / "manifest.toml").write_text(
            _GATES_TOML.replace('platform = "ci"', 'platform = "manual"'), encoding="utf-8")
        if run_quiet(offgate) != 2:
            failures.append("an all-manual-platform gates manifest expected exit 2 (no ci gates)")
        offhook = tmp / "offhook"
        _write_fixture(offhook)
        (offhook / ".aiqt" / "core" / "hooks" / "manifest.toml").write_text(
            _HOOKS_TOML.replace('platform = "claude-code"', 'platform = "other"'), encoding="utf-8")
        if run_quiet(offhook) != 2:
            failures.append("an all-other-platform hooks manifest expected exit 2 (no claude-code "
                            "hooks)")
        halfgate = tmp / "halfgate"
        _write_fixture(halfgate)
        (halfgate / ".aiqt" / "core" / "gates" / "manifest.toml").write_text(
            _GATES_TOML.replace('platform = "ci"', 'platform = "manual"', 1), encoding="utf-8")
        if run_quiet(halfgate) != 1:
            failures.append("a partially off-platform gates manifest expected exit 1 (the filtered "
                            "count disagrees with the page)")
        # The enforced figure is the sum of the register's linked summary rows: the one linked rule
        # moved to the hook-linked partial row still counts (exit 0); moved to Preview only it is no
        # longer linked to a shipped control (exit 1, the page's 1 disagrees); a missing linked row and
        # a register carrying only a bare Enforced row each fail closed (exit 2, naming the row).
        linked = "| Gate-linked (class a) | 1 |\n"
        for tag, register, want, needle in (
                ("enfpartial", _ENFORCEMENT_MD.replace(linked, "| Gate-linked (class a) | 0 |\n").replace(
                    "| Hook-linked, partial | 0 |", "| Hook-linked, partial | 1 |"), 0, None),
                ("enfpreview", _ENFORCEMENT_MD.replace(linked, "| Gate-linked (class a) | 0 |\n").replace(
                    "| Preview only | 0 |", "| Preview only | 1 |"), 1, None),
                ("enfnorow", _ENFORCEMENT_MD.replace("| Hook-linked, partial | 0 |\n", ""), 2,
                 "no '| Hook-linked, partial | N |' summary row"),
                ("enfbare", "## Summary\n\n| Status | Rules |\n|---|---:|\n| Enforced | 1 |\n", 2,
                 "no '| Gate-linked (class a) | N |' summary row")):
            if register == _ENFORCEMENT_MD:
                failures.append("self-test setup: the {} register mutation changed nothing".format(tag))
                continue
            tree = tmp / tag
            _write_fixture(tree)
            (tree / "ENFORCEMENT.md").write_text(register, encoding="utf-8")
            code, err = run_err(tree)
            if code != want or (needle is not None and needle not in err):
                failures.append("the {} register expected exit {}{} (got {!r})".format(
                    tag, want, "" if needle is None else " naming " + needle, code))

        # 21b. malformed hooks and gates manifests fail closed (exit 2), with the refusing diagnostic
        #      asserted where the claim depends on WHICH validation refused. Measured mutant matrix
        #      over two exactly defined mutants of _manifest_entries, not an aspiration: mutant one
        #      drops "default" from the validated hook field tuple; mutant two removes the WHOLE
        #      structure check (the array-of-tables refusal AND the per-entry string-field loop),
        #      returning the raw TOML value. offbad is red under both (exit 0 instead of 2);
        #      badhplat and badgplat are red only under mutant two (exit 1 instead of 2); badhook
        #      and mixedhook are red only under mutant two, through an uncaught TypeError that
        #      crashes the self-test (red, never a silent pass); nohplat and nogates are red under
        #      NEITHER mutant and stay as plain fail-closed regression coverage (without the
        #      structure check a missing platform still exits 2 through the caught KeyError, and a
        #      missing gates manifest never reaches hook validation).
        offbad = tmp / "offbad"
        _write_fixture(offbad)
        (offbad / ".aiqt" / "core" / "hooks" / "manifest.toml").write_text(
            _HOOKS_TOML + '\n[[hook]]\nid = "fixture-hook-off"\nrules = []\n'
            'platform = "other"\ndefault = 123\n', encoding="utf-8")
        code, err = run_err(offbad)
        if code != 2 or "has no string 'default'" not in err:
            failures.append("a non-string hook default on an excluded platform expected exit 2 naming "
                            "the default field (fail-closed)")
        nohplat = tmp / "nohplat"
        _write_fixture(nohplat)
        (nohplat / ".aiqt" / "core" / "hooks" / "manifest.toml").write_text(
            '[[hook]]\nid = "fixture-hook-bare"\nrules = []\ndefault = "block"\n', encoding="utf-8")
        if run_quiet(nohplat) != 2:
            failures.append("a hook entry with no platform expected exit 2 (fail-closed)")
        badhplat = tmp / "badhplat"
        _write_fixture(badhplat)
        (badhplat / ".aiqt" / "core" / "hooks" / "manifest.toml").write_text(
            _HOOKS_TOML.replace('platform = "claude-code"', "platform = 3", 1), encoding="utf-8")
        code, err = run_err(badhplat)
        if code != 2 or "has no string 'platform'" not in err:
            failures.append("a non-string hook platform expected exit 2 naming the platform field")
        badgplat = tmp / "badgplat"
        _write_fixture(badgplat)
        (badgplat / ".aiqt" / "core" / "gates" / "manifest.toml").write_text(
            _GATES_TOML.replace('platform = "ci"', "platform = 3", 1), encoding="utf-8")
        code, err = run_err(badgplat)
        if code != 2 or "has no string 'platform'" not in err:
            failures.append("a non-string gate platform expected exit 2 naming the platform field")
        badhook = tmp / "badhook"
        _write_fixture(badhook)
        (badhook / ".aiqt" / "core" / "hooks" / "manifest.toml").write_text(
            'hook = "ab"\n', encoding="utf-8")
        code, err = run_err(badhook)
        if code != 2 or "must be an array of tables" not in err:
            failures.append("a hooks manifest of hook = \"ab\" expected exit 2 naming the structure")
        mixedhook = tmp / "mixedhook"
        _write_fixture(mixedhook)
        (mixedhook / ".aiqt" / "core" / "hooks" / "manifest.toml").write_text(
            'hook = [ { id = "a", rules = [], platform = "claude-code", default = "block" }, "ab" ]\n',
            encoding="utf-8")
        code, err = run_err(mixedhook)
        if code != 2 or "must be an array of tables" not in err:
            failures.append("a non-table element inside the hook array expected exit 2 naming the "
                            "structure")
        nogates = tmp / "nogates"
        _write_fixture(nogates)
        (nogates / ".aiqt" / "core" / "gates" / "manifest.toml").unlink()
        if run_quiet(nogates) != 2:
            failures.append("a missing gates manifest expected exit 2 (fail-closed)")
        oddhook = tmp / "oddhook"
        _write_fixture(oddhook, landing=_LANDING.replace("2 Claude Code hooks", "3 Claude Code hooks"))
        (oddhook / ".aiqt" / "core" / "hooks" / "manifest.toml").write_text(
            _HOOKS_TOML + '\n[[hook]]\nid = "fixture-hook-audit"\nrules = []\n'
            'platform = "claude-code"\ndefault = "audit"\n', encoding="utf-8")
        if run_quiet(oddhook) != 2:
            failures.append("a claude-code hook with a default outside the split expected exit 2 "
                            "(fail-closed)")

        # 22. the shipped-path declaration leg: removing ANY declared row leaves its release path
        #     unplaced -> finding; each shipped form the retired suffix map missed (another suffix, a
        #     case variant, an HTML shell, a CI template, an extensionless hook) added to the release
        #     manifest -> finding; a declared prefix matching no release path, and one overshadowed
        #     by longer prefixes, each asserted by its own diagnostic -> finding; a nested prefix
        #     pair classifies by the longest prefix -> clean (order-independent, see the nestdecl
        #     comment); a missing declaration, and a missing or empty release manifest -> exit 2
        #     (fail-closed). Case 22b then interrogates the REAL declaration and release manifest.
        decl_rows = (
            ("notools", '[[code]]\nprefix = "tools/"\nlabel = "the tooling under tools/"\n\n'),
            ("noopftools", '[[code]]\nprefix = "opf/tools/"\nlabel = "the tooling under opf/tools/"\n\n'),
            ("nopreview", '[[code]]\nprefix = ".preview/"\nlabel = "the preview hook scripts under .preview/"\n\n'),
            ("noplugin", '[[code]]\nprefix = "plugin/"\nlabel = "the hooks plugin"\n\n'),
            ("nohookssrc", '[[code]]\nprefix = ".aiqt/core/hooks/"\nlabel = "the hooks plugin source"\n\n'),
            ("nosite", '[[code]]\nprefix = "site/"\nlabel = "the JavaScript under site/"\n\n'),
            ("noreadme", '[[noncode]]\nprefix = "README.md"\n\n'),
            ("noenfmd", '[[noncode]]\nprefix = "ENFORCEMENT.md"\n'),
        )
        for tag, row_text in decl_rows:
            if _DISCLOSURE.count(row_text) != 1:
                failures.append("declaration-removal fixture {}: its row must appear exactly once in "
                                "_DISCLOSURE".format(tag))
                continue
            dropped = tmp / ("drop-" + tag)
            _write_fixture(dropped, disclosure=_DISCLOSURE.replace(row_text, "", 1))
            if run_quiet(dropped) != 1:
                failures.append("removing the {} declaration row expected exit 1 (its release path is "
                                "left unplaced)".format(tag))
        rogue_paths = (
            "opf/site/theme.js", "docs/helper.mjs", "scripts/run.bash", "scripts/Tool.PY",
            "docs/_shell.html", "ci/github-actions.yml", "scripts/rogue.mjs",
            "scripts/rogue_helper.py", "opf/enforcement/precommit/pre-commit",
        )
        for pos, rogue in enumerate(rogue_paths):
            rogued = tmp / ("rogue-{}".format(pos))
            _write_fixture(rogued, release_manifest=_RELEASE_MANIFEST +
                           '\n[[sources]]\npath = "{}"\n'.format(rogue))
            if run_quiet(rogued) != 1:
                failures.append("release path {!r} under no declared prefix expected exit 1".format(
                    rogue))
        staledecl = tmp / "staledecl"
        _write_fixture(staledecl, disclosure=_DISCLOSURE +
                       '\n[[code]]\nprefix = "ci/"\nlabel = "a CI template"\n')
        if run_quiet(staledecl) != 1:
            failures.append("a declared prefix matching no release path expected exit 1")
        # A prefix whose every match loses to a longer prefix is reported as overshadowed, never as
        # matching no path: an exact [[code]] row for the one release path under site/ leaves the
        # site/ tree row with matches but no classifications.
        shadowdecl = tmp / "shadowdecl"
        _write_fixture(shadowdecl, disclosure=_DISCLOSURE +
                       '\n[[code]]\nprefix = "site/js/fixture.js"\nlabel = "the JavaScript under site/"\n')
        code, out = run_out(shadowdecl)
        if code != 1 or "is overshadowed" not in out:
            failures.append("a fully overshadowed declared prefix expected exit 1 with the "
                            "overshadowed diagnostic")
        # The nested pair stays clean only under the longest-prefix rule: the combined candidate list
        # reads noncode before code, so a first-match regression classifies docs/_shell.html under
        # the broad noncode docs/ parent and the detector turns this case red (the fixture files
        # below exist on disk so the detector can read them under that regression).
        nestdecl = tmp / "nestdecl"
        _write_fixture(
            nestdecl,
            disclosure=_DISCLOSURE +
            '\n[[code]]\nprefix = "docs/_shell.html"\nlabel = "an HTML shell with an inline script"\n'
            '\n[[noncode]]\nprefix = "docs/"\n',
            release_manifest=_RELEASE_MANIFEST +
            '\n[[sources]]\npath = "docs/_shell.html"\n\n[[sources]]\npath = "docs/about.md"\n')
        (nestdecl / "docs").mkdir()
        (nestdecl / "docs" / "_shell.html").write_text(
            "<!doctype html>\n<script>window.x = 1;</script>\n", encoding="utf-8")
        (nestdecl / "docs" / "about.md").write_text("# fixture about\n", encoding="utf-8")
        if run_quiet(nestdecl) != 0:
            failures.append("a nested prefix pair (docs/ and docs/_shell.html) expected exit 0 (the "
                            "longest declared prefix classifies the path)")
        nodecl = tmp / "nodecl"
        _write_fixture(nodecl, disclosure=_DISCLOSURE[:_DISCLOSURE.index("[[code]]")])
        if run_quiet(nodecl) != 2:
            failures.append("a disclosure without the shipped-path declaration expected exit 2 "
                            "(fail-closed)")
        emptyrel = tmp / "emptyrel"
        _write_fixture(emptyrel, release_manifest=
                       'format-version = 1\nrelease-version = "0.0.0"\nsources = []\n')
        if run_quiet(emptyrel) != 2:
            failures.append("an empty release-manifest sources array expected exit 2 (fail-closed)")
        nomanifest = tmp / "nomanifest"
        _write_fixture(nomanifest)
        (nomanifest / ".aiqt" / "manifest.toml").unlink()
        if run_quiet(nomanifest) != 2:
            failures.append("a missing release manifest expected exit 2 (fail-closed)")
        # 22b. the REAL declaration, not only the fixture: removing any [[code]] row of the
        #      repository's own disclosure.toml must turn the check red against the real release
        #      manifest (the row's code paths fall under a broader [[noncode]] prefix, where the
        #      detector names them, or under no prefix at all), each reviewed rogue path planted in
        #      the real manifest must be a finding naming it, and the unmodified pair is the clean
        #      control.
        try:
            real_root = repo_root()
            real_decl = shipped_declaration(load_toml(real_root / "disclosure.toml"))
            real_sources = load_toml(real_root / ".aiqt" / "manifest.toml").get("sources")
            real_code, real_noncode = real_decl
            for pos in range(len(real_code)):
                pruned = (real_code[:pos] + real_code[pos + 1:], real_noncode)
                real_findings = []
                check_release_code_declaration(real_root, real_findings, declaration=pruned,
                                               sources=real_sources)
                if not real_findings:
                    failures.append("removing the real [[code]] row {!r} expected at least one "
                                    "finding".format(real_code[pos]["prefix"]))
            for rogue in (".claude/hooks/guard.py", ".aiqt/core/tools/run.sh", "docs/app.js",
                          ".cursor/hooks/x.js", "LICENSE-helper.sh", "README.md.js",
                          "docs/_shell.html.js"):
                real_findings = []
                check_release_code_declaration(real_root, real_findings, declaration=real_decl,
                                               sources=real_sources + [{"path": rogue}])
                if not any(rogue in line for line in real_findings):
                    failures.append("planting rogue path {!r} in the real release manifest expected "
                                    "a finding naming it".format(rogue))
            real_findings = []
            check_release_code_declaration(real_root, real_findings, declaration=real_decl,
                                           sources=real_sources)
            if real_findings:
                failures.append("the real declaration and release manifest expected no findings "
                                "(control): {!r}".format(real_findings[:3]))
        except (OSError, ValueError, ManifestError) as exc:
            failures.append("the real-declaration sweep could not run: {}".format(exc))

        # 22c. the code detector, branch by branch, through the real check: each vector is
        #      planted under a POPULATED [[noncode]] docs/ prefix of the fixture declaration,
        #      so a finding can only come from _path_is_code, and the finding must name both
        #      the path and the docs/ prefix: every closed-list suffix, a mixed-case suffix, a
        #      double suffix, a script-holding HTML page (a .htm page, so dropping ".htm" from
        #      _HTML_SUFFIXES is red; a mixed-case "<ScRiPt>" tag, so removing the case fold is
        #      red; a comment-only "<script>", the disclosed conservative over-detection; and a
        #      UTF-8 byte-order-marked page, which pins only that such a page is read and
        #      classified, never rejected: whether the decoder strips the mark cannot change the
        #      result, as the mark can never sit inside "<script"), and an extensionless shebang
        #      file -> a detector finding; a plain .html and .htm page, a page naming "Script"
        #      without a tag, and a markdown file -> exit 0 (the populated-prefix controls); an
        #      HTML page that does not decode as UTF-8 (a UTF-16 script page with its byte-order
        #      mark) or that holds a NUL byte (the ASCII script page encoded as UTF-16 and UTF-32
        #      without a mark, which decodes as UTF-8 holding NULs, and a NUL inside a UTF-8
        #      page) -> exit 2
        #      asserted by its diagnostic (cannot-evaluate, never non-code); a listed file that
        #      is missing or unreadable,
        #      for each branch that reads the file (HTML, extensionless) -> exit 2
        #      (fail-closed).
        docs_decl = _DISCLOSURE + '\n[[noncode]]\nprefix = "docs/"\n'

        def _docs_fixture(tag, relname, payload):
            base = tmp / ("det-" + tag)
            _write_fixture(base, disclosure=docs_decl,
                           release_manifest=_RELEASE_MANIFEST +
                           '\n[[sources]]\npath = "docs/{}"\n'.format(relname))
            (base / "docs").mkdir()
            if payload is not None:
                (base / "docs" / relname).write_bytes(payload)
            return base

        detector_code = (
            ("suffix-py", "a.py", b"print(1)\n"),
            ("suffix-sh", "b.sh", b"echo 1\n"),
            ("suffix-bash", "c.bash", b"echo 1\n"),
            ("suffix-js", "d.js", b"x = 1\n"),
            ("suffix-mjs", "e.mjs", b"export var x = 1\n"),
            ("suffix-cjs", "f.cjs", b"x = 1\n"),
            ("mixed-case", "Tool.PY", b"print(1)\n"),
            ("double-suffix", "notes.md.js", b"x = 1\n"),
            ("html-script", "shell.html", b"<!doctype html>\n<script>x = 1</script>\n"),
            ("html-comment-only", "commented.html",
             b"<!doctype html>\n<!-- <script> example -->\n"),
            ("html-bom", "bom.html",
             "\ufeff<!doctype html>\n<script>x = 1</script>\n".encode("utf-8")),
            ("htm-script", "page.htm", b"<!doctype html>\n<script>x = 1</script>\n"),
            ("html-mixed-case-tag", "upper.html", b"<!doctype html>\n<ScRiPt>x = 1</ScRiPt>\n"),
            ("extensionless-shebang", "hook", b"#!/bin/sh\necho 1\n"),
        )
        for tag, relname, payload in detector_code:
            code, out = run_out(_docs_fixture(tag, relname, payload))
            named = "release path 'docs/{}' is code by this gate's detector".format(relname)
            if code != 1 or named not in out or "prefix 'docs/'" not in out:
                failures.append("detector vector {} (docs/{}) expected exit 1 with the detector "
                                "finding naming the path and the docs/ prefix".format(tag, relname))
        for tag, relname, payload in (
                ("html-plain", "plain.html", b"<!doctype html>\n<p>no executable content</p>\n"),
                ("htm-plain", "plain.htm", b"<!doctype html>\n<p>no executable content</p>\n"),
                ("html-script-word", "word.html",
                 b"<!DOCTYPE HTML>\n<P>Script notes: no SCRIPT tag here.</P>\n"),
                ("markdown", "about.md", b"# fixture about\n")):
            if run_quiet(_docs_fixture(tag, relname, payload)) != 0:
                failures.append("non-code vector {} (docs/{}) expected exit 0".format(tag, relname))
        utf16page = _docs_fixture("html-utf16", "wide.html",
                                  "<!doctype html>\n<script>x = 1</script>\n".encode("utf-16"))
        code, err = run_err(utf16page)
        if code != 2 or "does not decode as UTF-8" not in err:
            failures.append("an HTML page that does not decode as UTF-8 expected exit 2 naming "
                            "the cannot-evaluate cause, never a non-code pass")
        script_page = "<!doctype html>\n<script>x = 1</script>\n"
        for tag, payload in (
                ("utf16le", script_page.encode("utf-16-le")),
                ("utf16be", script_page.encode("utf-16-be")),
                ("utf32le", script_page.encode("utf-32-le")),
                ("utf32be", script_page.encode("utf-32-be")),
                ("utf8nul", b"<!doctype html>\n<p>a\x00b</p>\n")):
            code, err = run_err(_docs_fixture("html-nul-" + tag, "nul-{}.html".format(tag), payload))
            if code != 2 or "docs/nul-{}.html: HTML page holds a NUL byte".format(tag) not in err:
                failures.append("an HTML page holding a NUL byte ({}) expected exit 2 naming the "
                                "NUL cannot-evaluate cause, never a non-code pass".format(tag))
        for tag, relname in (("html-missing", "ghost.html"), ("extensionless-missing", "ghostbin")):
            if run_quiet(_docs_fixture(tag, relname, None)) != 2:
                failures.append("a listed but missing docs/{} expected exit 2 (fail-closed)".format(
                    relname))
        for tag, relname, payload in (
                ("html-unreadable", "locked.html", b"<!doctype html>\n<script>x = 1</script>\n"),
                ("extensionless-unreadable", "lockedbin", b"#!/bin/sh\n")):
            base = _docs_fixture(tag, relname, payload)
            unreadable_std = base / "docs" / relname
            os.chmod(unreadable_std, 0)
            if os.access(unreadable_std, os.R_OK):
                skipped.append("22c " + tag)
            elif run_quiet(base) != 2:
                failures.append("an unreadable docs/{} expected exit 2 (fail-closed)".format(
                    relname))
            os.chmod(unreadable_std, 0o644)
            unreadable_std = None

        # 23. the instruction-budget note: a figure that disagrees with the committed ratchet, a
        #     missing note, and a note kept after the ratchet no longer exceeds 120,000 -> finding; a
        #     missing budget source -> exit 2 (fail-closed).
        budgetdrift = tmp / "budgetdrift"
        _write_fixture(budgetdrift, landing=_LANDING.replace("132,775 UTF-16 code units",
                                                             "132,776 UTF-16 code units"))
        if run_quiet(budgetdrift) != 1:
            failures.append("a budget note stating a figure other than the ratchet expected exit 1")
        budgetmissing = tmp / "budgetmissing"
        _write_fixture(budgetmissing, budget_md="# Fixture Budget\n\nNo note here.\n")
        if run_quiet(budgetmissing) != 1:
            failures.append("a page without the budget note expected exit 1")
        exceedsgone = tmp / "exceedsgone"
        _write_fixture(exceedsgone, landing=_LANDING.replace(
            "may exceed that figure", "no longer exceeds that figure", 1))
        if run_quiet(exceedsgone) != 1:
            failures.append("a budget note rewritten to say the pack no longer exceeds the 120,000 "
                            "figure expected exit 1 (the exceeds half is bound)")
        budgetlow = tmp / "budgetlow"
        _write_fixture(budgetlow,
                       landing=_LANDING.replace("132,775 UTF-16 code units", "100,000 UTF-16 code units"),
                       budget_md=_BUDGET_MD.replace("132,775 UTF-16 code units", "100,000 UTF-16 code units"),
                       budget_toml=_BUDGET_TOML.replace("132775", "100000"))
        if run_quiet(budgetlow) != 1:
            failures.append("a budget note kept after the ratchet fell to 120,000 or below expected "
                            "exit 1")
        nobudget = tmp / "nobudget"
        _write_fixture(nobudget)
        (nobudget / ".aiqt" / "core" / "instruction-budget.toml").unlink()
        if run_quiet(nobudget) != 2:
            failures.append("a missing instruction-budget source expected exit 2 (fail-closed)")

        # 23b. the unit binding on EACH public surface (README.md and INSTRUCTION-BUDGET.md): the
        #      unit changed to "characters", the unit omitted, the explanatory suffix altered, and
        #      the suffix extended past its sentence end ("counts in bytes.", "counts, in bytes.")
        #      and non-whitespace glued after the period ("counts.in bytes.", the vector that
        #      turns red when only the (?!\S) end condition is removed) are each a missing-note
        #      finding asserted by its diagnostic naming the surface (the regex binds the whole
        #      unit tail through the sentence terminator, so any of these rewords unbinds the
        #      match); the period at the end of the page and the period followed by a space and
        #      another sentence stay clean (the terminator's two accepting ends);
        #      and the antecedent is bound: a page saying "budget to 140,000" before "that
        #      figure" is a finding by the antecedent diagnostic.
        unit_rewords = (
            ("unit-characters", "132,775 UTF-16 code units", "132,775 characters"),
            ("unit-omitted", "132,775 UTF-16 code units, the unit", "132,775, the unit"),
            ("unit-suffix", "the unit the budget gate counts", "the unit the budget gate prints"),
            ("unit-suffix-extended", "the unit the budget gate counts.",
             "the unit the budget gate counts in bytes."),
            ("unit-suffix-clause", "the unit the budget gate counts.",
             "the unit the budget gate counts, in bytes."),
            ("unit-suffix-glued", "the unit the budget gate counts.",
             "the unit the budget gate counts.in bytes."),
        )
        for tag, old, new in unit_rewords:
            if _BUDGET_NOTE.count(old) != 1:
                failures.append("unit fixture {}: its target must appear exactly once in "
                                "_BUDGET_NOTE".format(tag))
                continue
            for rel, overrides in (
                    ("README.md", {"landing": _LANDING.replace(old, new, 1)}),
                    ("INSTRUCTION-BUDGET.md", {"budget_md": _BUDGET_MD.replace(old, new, 1)})):
                unitfix = tmp / ("unit-{}-{}".format(tag, rel.split(".")[0].lower()))
                _write_fixture(unitfix, **overrides)
                code, out = run_out(unitfix)
                if (code != 1 or "{}: the instruction-budget note".format(rel) not in out
                        or "was not found" not in out):
                    failures.append("budget unit vector {} on {} expected exit 1 with the "
                                    "missing-note diagnostic".format(tag, rel))
        # The terminator's accepting ends, on each surface: the period as the last character of
        # the page (the note's trailing newline removed; a terminator that demands a following
        # whitespace character turns this red) and the period followed by a space and another
        # sentence (a terminator that demands a line end turns this red) -> clean.
        for tag, what, old, new in (
                ("end-of-page", "the end of the page", "the unit the budget gate counts.\n",
                 "the unit the budget gate counts."),
                ("space-after", "a space and another sentence", "the unit the budget gate counts.\n",
                 "the unit the budget gate counts. The gate prints it.\n")):
            for rel, page in (("README.md", _LANDING), ("INSTRUCTION-BUDGET.md", _BUDGET_MD)):
                if not page.endswith(old):
                    failures.append("terminator control {}: {} fixture must end with the note's "
                                    "period and newline".format(tag, rel))
                    continue
                key = "landing" if rel == "README.md" else "budget_md"
                endfix = tmp / ("term-{}-{}".format(tag, rel.split(".")[0].lower()))
                _write_fixture(endfix, **{key: page[:-len(old)] + new})
                if run_quiet(endfix) != 0:
                    failures.append("a budget note whose period is followed by {} on {} expected "
                                    "exit 0".format(what, rel))
        for rel, overrides in (
                ("README.md",
                 {"landing": _LANDING.replace("budget to 120,000.", "budget to 140,000.", 1)}),
                ("INSTRUCTION-BUDGET.md",
                 {"budget_md": _BUDGET_MD.replace("budget to 120,000.", "budget to 140,000.", 1)})):
            antefix = tmp / ("ante-" + rel.split(".")[0].lower())
            _write_fixture(antefix, **overrides)
            code, out = run_out(antefix)
            if code != 1 or "as the antecedent of that figure" not in out:
                failures.append("a budget-to-140,000 antecedent on {} expected exit 1 with the "
                                "antecedent diagnostic".format(rel))
        # The antecedent aside: INSTRUCTION-BUDGET.md reads "budget to 120,000, not 150,000.",
        # and the regex accepts exactly that aside, so the page carrying it stays clean (removing
        # the aside group from _BUDGET_NOTE_RE turns this red), while an aside naming another
        # figure is the missing-note finding (widening the aside group turns that red).
        asidekept = tmp / "aside-kept"
        _write_fixture(asidekept, budget_md=_BUDGET_MD.replace(
            "budget to 120,000.", "budget to 120,000, not 150,000.", 1))
        if run_quiet(asidekept) != 0:
            failures.append("a budget note with the ', not 150,000' aside expected exit 0 (the "
                            "aside INSTRUCTION-BUDGET.md carries is accepted)")
        asideother = tmp / "aside-other"
        _write_fixture(asideother, budget_md=_BUDGET_MD.replace(
            "budget to 120,000.", "budget to 120,000, not 160,000.", 1))
        code, out = run_out(asideother)
        if (code != 1 or "INSTRUCTION-BUDGET.md: the instruction-budget note" not in out
                or "was not found" not in out):
            failures.append("a budget note with an aside naming another figure expected exit 1 "
                            "with the missing-note diagnostic")

        # 23c. the exceeds boundary is exact: a ratchet of exactly _BUDGET_FLOOR (120,000) no
        #      longer exceeds the antecedent -> finding; one unit above (120,001) still exceeds
        #      it -> clean (the at-boundary and just-outside values of the <= comparison).
        atfloor = tmp / "atfloor"
        _write_fixture(atfloor,
                       landing=_LANDING.replace("132,775 UTF-16 code units",
                                                "120,000 UTF-16 code units"),
                       budget_md=_BUDGET_MD.replace("132,775 UTF-16 code units",
                                                    "120,000 UTF-16 code units"),
                       budget_toml=_BUDGET_TOML.replace("132775", "120000"))
        if run_quiet(atfloor) != 1:
            failures.append("a ratchet exactly at the 120,000 floor expected exit 1 (at-boundary)")
        overfloor = tmp / "overfloor"
        _write_fixture(overfloor,
                       landing=_LANDING.replace("132,775 UTF-16 code units",
                                                "120,001 UTF-16 code units"),
                       budget_md=_BUDGET_MD.replace("132,775 UTF-16 code units",
                                                    "120,001 UTF-16 code units"),
                       budget_toml=_BUDGET_TOML.replace("132775", "120001"))
        if run_quiet(overfloor) != 0:
            failures.append("a ratchet one unit above the 120,000 floor expected exit 0 "
                            "(just-outside boundary)")

        # 23d. every other figure the budget section states, drifted alone on each page -> a
        #      finding asserted by its own diagnostic naming the page, the figure and the
        #      drifted value (removing that page's _BUDGET_FIGURE_WORDINGS entry, or comparing
        #      with anything but the authoritative value, turns its vector red); each wording
        #      reworded away on each page -> the not-found diagnostic; the budget source's
        #      claude-code-version moved while the pages keep the old build -> a finding on BOTH
        #      pages (the build is read from the budget source, not from a literal); a missing or
        #      non-string claude-code-version -> exit 2 (fail-closed). Every anchor must occur
        #      exactly once in its page fixture first.
        figure_drifts = (
            ("target", "the session target", "under 150,000\ncharacters", "under 160,000\ncharacters",
             "Treat 150,000 characters", "Treat 160,000 characters", "160,000"),
            ("build", "the pinned Claude Code build", "(9.8.7,", "(9.8.6,", "(9.8.7 in",
             "(9.8.6 in", "9.8.6"),
            ("loader", "the loader's character check", "120,000-character", "100,000-character",
             "120,000-character", "100,000-character", "100,000"),
        )
        for key, label, land_old, land_new, md_old, md_new, shown in figure_drifts:
            for rel, kwarg, page, old, new in (("README.md", "landing", _LANDING, land_old, land_new),
                                               ("INSTRUCTION-BUDGET.md", "budget_md", _BUDGET_MD,
                                                md_old, md_new)):
                if page.count(old) != 1:
                    failures.append("budget figure fixture {}: its anchor {!r} must appear exactly "
                                    "once in the {} fixture".format(key, old, rel))
                    continue
                figfix = tmp / ("figure-{}-{}".format(key, rel.split(".")[0].lower()))
                _write_fixture(figfix, **{kwarg: page.replace(old, new, 1)})
                code, out = run_out(figfix)
                if code != 1 or "{}: the page states {} as {};".format(rel, label, shown) not in out:
                    failures.append("budget figure {} drifted on {} expected exit 1 with its "
                                    "drift diagnostic".format(key, rel))
        figure_rewords = (
            ("target", "README.md", "landing", _LANDING, "characters in total.", "characters overall."),
            ("target", "INSTRUCTION-BUDGET.md", "budget_md", _BUDGET_MD,
             "as this project's target", "as our target"),
            ("build", "README.md", "landing", _LANDING, "build the gate\nmodels (", "build ("),
            ("build", "INSTRUCTION-BUDGET.md", "budget_md", _BUDGET_MD, "build the gate\nmodels (",
             "build ("),
            ("loader", "README.md", "landing", _LANDING, "-character check", "-char check"),
            ("loader", "INSTRUCTION-BUDGET.md", "budget_md", _BUDGET_MD, "-character check",
             "-char check"),
        )
        for key, rel, kwarg, page, old, new in figure_rewords:
            if page.count(old) != 1:
                failures.append("budget wording fixture {}: its anchor {!r} must appear exactly once "
                                "in the {} fixture".format(key, old, rel))
                continue
            wordfix = tmp / ("wording-{}-{}".format(key, rel.split(".")[0].lower()))
            _write_fixture(wordfix, **{kwarg: page.replace(old, new, 1)})
            code, out = run_out(wordfix)
            if code != 1 or "{}: the wording that states".format(rel) not in out:
                failures.append("budget wording {} reworded on {} expected exit 1 with the "
                                "not-found diagnostic".format(key, rel))
        buildmoved = tmp / "buildmoved"
        _write_fixture(buildmoved, budget_toml=_BUDGET_TOML.replace('"9.8.7"', '"9.8.8"'))
        code, out = run_out(buildmoved)
        if (code != 1
                or "README.md: the page states the pinned Claude Code build as 9.8.7;" not in out
                or "INSTRUCTION-BUDGET.md: the page states the pinned Claude Code build as 9.8.7;"
                not in out):
            failures.append("a budget source whose claude-code-version moved under unchanged pages "
                            "expected exit 1 with a build finding on both pages")
        for tag, toml in (("nobuild", "ratchet = 132775\n"),
                          ("intbuild", "ratchet = 132775\nclaude-code-version = 987\n")):
            buildbad = tmp / tag
            _write_fixture(buildbad, budget_toml=toml)
            code, err = run_err(buildbad)
            if code != 2 or "claude-code-version must be a non-empty string" not in err:
                failures.append("a budget source with a missing or non-string claude-code-version "
                                "({}) expected exit 2 with its diagnostic".format(tag))
        # The pages state the committed ratchet only: the earlier "measured PACK" claim, which
        # this gate cannot bind, restored on either page -> the missing-note finding.
        for rel, kwarg, page in (("README.md", "landing", _LANDING),
                                 ("INSTRUCTION-BUDGET.md", "budget_md", _BUDGET_MD)):
            old = ("may exceed that figure: the budget gate allows it a PACK of up to the committed "
                   "ratchet,")
            new = "itself exceeds that figure: its measured PACK stands at the committed ratchet,"
            if page.count(old) != 1:
                failures.append("measured-claim fixture: its anchor must appear exactly once in the "
                                "{} fixture".format(rel))
                continue
            measfix = tmp / ("measured-" + rel.split(".")[0].lower())
            _write_fixture(measfix, **{kwarg: page.replace(old, new, 1)})
            code, out = run_out(measfix)
            if (code != 1 or "{}: the instruction-budget note".format(rel) not in out
                    or "was not found" not in out):
                failures.append("a budget note claiming a measured PACK on {} expected exit 1 with "
                                "the missing-note diagnostic".format(rel))
    finally:
        if unreadable_std is not None:
            os.chmod(unreadable_std, 0o755)  # restore even on an unexpected early exit
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    note = ("" if not skipped else
            " NOTE: skipped {} case(s) the runner cannot exercise (chmod-0 still readable): {}"
            .format(len(skipped), ", ".join(skipped)))
    print("SELF-TEST PASS: a clean fixture and a vendored name in a section without the phrase pass; a "
          "meta/disclosure parenthetical mismatch, a schema-enum "
          "gap, a closed-set parity break, a vendored name in the same section as the deferred phrase (a "
          "paragraph, a table row in any pipe form with pipe-free rows included, a second paragraph, a "
          "case variant, a wrapped or emphasis-split or hyphenated phrase, a header-marked table, a "
          "column-0 lazy list continuation, a name before the phrase, a wrapped name, a name in another "
          "case, or a name elsewhere in the section: the disclosed over-refusal), a subset manifest "
          "rendered with an edition denominator in the registry row or the reverse-view <p>, a subset "
          "framework carrying ids_total in the JSON export, a landing-page count that disagrees with its "
          "manifests (every captured figure drifted alone), a missing or broken anchored count wording "
          "(every wording broken alone, the enforcement and crosswalk wordings split across a line "
          "break, the proof line replaced with its wording moved to a later bold numeric line, and a "
          "bold digit line inserted above the proof line, asserted by its own message), a numeric run "
          "on a designated line outside every anchored count wording (a signed and a "
          "thousands-separated duplicate and the reviewed evasive noun, spacing, markup, "
          "no-break-space, fullwidth-digit, Arabic-Indic-digit, superscript, subscript, circled, "
          "dingbat, Roman-numeral, vulgar-fraction, and CJK-numeral forms included), an unsupported "
          "numeric form (a thousands separator or a leading sign at every wording), a partially "
          "off-platform gates manifest, a removed shipped-path declaration row (in the fixture and, "
          "for every [[code]] row, in the repository's real declaration), a release path under no "
          "declared prefix, a detector-code path classified under a [[noncode]] prefix (the reviewed "
          "rogue paths, planted in the real release manifest, and every detector branch through a "
          "populated fixture [[noncode]] prefix: each closed-list suffix, a mixed-case and a double "
          "suffix, a script-holding .html and .htm page, a mixed-case-tag, a comment-only, and a "
          "byte-order-marked HTML page, and an "
          "extensionless shebang file, each finding naming the path and the prefix), a declared "
          "prefix matching no release "
          "path or overshadowed by longer prefixes (each asserted by its own diagnostic), and a "
          "budget note whose "
          "figure disagrees with the committed ratchet, is absent, loses its exceeds half, its "
          "UTF-16 unit, or the unit's explanatory suffix to a rewording, an extension past the "
          "sentence end, or text glued after the period (on each public surface), names an "
          "antecedent other than 120,000 before "
          "that figure or an aside other than \", not 150,000\", claims a measured PACK, or "
          "outlives a ratchet at or under "
          "120,000, and a session target, pinned build, or loader character check that differs "
          "from its authoritative value or whose wording is absent (each on each page, the "
          "build also moved in the budget source) "
          "each report a finding (exit 1); the deferred span itself is read ONLY through the "
          "positive grammar (blank lines, plain paragraph or continuation text, flat plain list items, "
          "column-0 ATX headings), so a blockquote, a fence, a nested or indented list, list-item HTML, "
          "a backtick-info pseudo-fence or a U+00A0 pseudo-close between the phrase and the section end "
          "is cannot-evaluate (exit 2, naming the file and line); a true explanatory count sentence "
          "outside its "
          "designated line, a keycap ten, hundred points, or die face symbol on the proof line "
          "(the disclosed residual: no Unicode numeric value, not read), a nested declared "
          "prefix pair resolved by the longest prefix (order "
          "independent), a plain .html and .htm page, a page naming Script without a tag, and a "
          "markdown file under a populated [[noncode]] "
          "prefix, a budget note carrying the \", not 150,000\" aside, a budget note whose period "
          "ends the page or is followed by a space and another sentence (on each public "
          "surface), a ratchet one unit above "
          "the 120,000 floor, "
          "and the repository's real declaration against its real release manifest "
          "(the control), stay "
          "clean (exit 0); a missing page, a missing JSON export, an unreadable standards dir, an "
          "unterminated code fence (naming the file and the fence's opening line), a fence not at line "
          "start, list-item HTML, an HTML block other than a single-line comment, or a '#' line that is "
          "not a heading in the accepted grammar (one to six '#', then exactly U+0020 or the line end), "
          "or a '#' line with leading whitespace (an indented heading CommonMark can nest in a list "
          "item; the legal top-level form is over-refused, a disclosed conservative refusal), each "
          "refused anywhere from the top of the text to the section's end boundary (naming the file "
          "and line, the structure validated BEFORE a boundary may exclude text, a mis-started section "
          "included), a dangling-symlink manifest failing inside the real loader naming the manifest, "
          "a missing/invalid catalogue, an unaliased publisher, a missing enforcement register, rules "
          "folder, gates or hooks manifest, a malformed gates or hooks manifest (a non-table value, a "
          "non-table element inside the array, a missing or non-string platform, gate and hook forms "
          "both, and a non-string hook default, even on an excluded platform, the structural refusals "
          "asserted by their diagnostics), an all-wrong-platform gates or hooks manifest, a "
          "claude-code hook default outside the blocking/warning split, a missing shipped-path "
          "declaration, a missing or empty release manifest, an HTML page that does not decode as "
          "UTF-8 or holds a NUL byte, a listed detector file that is missing or unreadable, a missing "
          "instruction-budget source, and a missing or non-string claude-code-version in it "
          "all fail closed (exit 2); "
          "a single-line HTML comment above the section "
          "is not over-refused and its section is proven read (the planted name is the finding); the raw "
          "and blockquote-stripped heading readings must agree up to the section end (a "
          "blockquoted heading, a stripping-manufactured decoy heading, and a blockquoted fence "
          "inverting one reading's fence state are each cannot-evaluate, exit 2, naming the first "
          "disagreeing line); a setext underline or thematic-break line, indented code after a "
          "blank line or a heading, list-held code (a five-space gap), a tab between a list "
          "marker and its text (a conservative refusal of a shape CommonMark reads as a plain "
          "item) and a list-held "
          "heading in the deferred span are cannot-evaluate (exit 2); an indented '#' line "
          "after the section's end boundary stays clean (the refusal's span ends at the "
          "boundary line); and a pseudo-closing "
          "fence above the section (trailing words or U+00A0) does not close, the section below "
          "staying read" + note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
