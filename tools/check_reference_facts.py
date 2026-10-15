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
    primary control; this is defence in depth against a hand-edited page or export.

BEST-EFFORT RESIDUAL (disclosed per the pack's own rule): this gate checks the specific fact copies
enumerated above; it is not a semantic-equivalence prover for arbitrary prose, and `catalogue = "full"` is
a reviewed declaration this gate trusts, not a fact it can prove offline (no full edition is vendored to
compare against). It fails closed on an unreadable/malformed input and on a new publisher family that has
no alias, so a stale copy cannot ship silently.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, load_toml  # noqa: E402
from _standards import load_manifests, ManifestError, KINDS, STATUSES, CATALOGUES  # noqa: E402
from gen_mappings import RELATION, RELATION_PROSE, STATUS_PILL, STATUS_DESC  # noqa: E402
from check_standards_currency import POLICY  # noqa: E402

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


def run(root):
    findings = []
    try:
        manifests = load_manifests(root / ".aiqt" / "standards")
        if not manifests:
            print("error: no manifests under .aiqt/standards/; fail-closed", file=sys.stderr)
            return 2
        fams = expected_families(manifests)
        check_meta(root, fams, findings)
        check_disclosure(root, fams, findings)
        check_enum_table(root, findings)
        check_parity(findings)
        check_deferred(root, manifests, findings)
        check_subset_denominator(root, manifests, findings)
        check_json_subset_denominator(root, manifests, findings)
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
#  15. an unterminated code fence in the README -> exit 2 (the section reader cannot bound a section
#      through it, fail-closed), naming the file and the fence's opening line.
# (1, 7, 12, 13 are the invariant-bearing cases; the rest complete the fail-closed contract.)

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

_DISCLOSURE = (
    'title = "d"\nnote = "n"\nsite_base = "https://example.org"\n\n'
    '[[row]]\nid = "standards-mappings"\ntopic = "Standards mappings"\n'
    'claim = "A crosswalk to identifiers in frameworks (ISO/IEC, OWASP), each pinned."\n'
    'limitation = "A mapping asserts a relationship, not certification."\n'
    'evidence = [\n  { text = "How", href = "/mappings#methodology" },\n]\n'
)


def _write_fixture(base, manifests=None, readme=None, page=None, disclosure=None, json_export=None):
    """Write a complete clean fixture tree, overridable per component for a case's single mutation.
    `manifests` is a list of (stem, name, publisher, edition, kind, catalogue)."""
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
            def _deny_load(path, _real=saved_load):
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

        # 15. an unterminated code fence: the section reader cannot bound a section through it, so it
        #     fails closed (exit 2), never a partial or truncated section read.
        unfenced = tmp / "unfenced"
        _write_fixture(unfenced, readme=_README + "\n```text\nnever closed\n")
        rc, out = run_capture(unfenced)
        if rc != 2 or "standards README: unterminated code fence opened at line 13" not in out:
            failures.append("an unterminated code fence expected exit 2 naming standards README and "
                            "line 13 (got {}: {!r})".format(rc, out))
    finally:
        if unreadable_std is not None:
            os.chmod(unreadable_std, 0o755)  # restore even on an unexpected early exit
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("SELF-TEST FAIL:")
        for failure in failures:
            print("  - " + failure)
        return 1
    print("SELF-TEST PASS: a clean fixture and a vendored name in a section without the phrase pass; a "
          "meta/disclosure parenthetical mismatch, a schema-enum gap, a closed-set parity break, a vendored "
          "name in the same section as the deferred phrase (a paragraph, a table row in any pipe form "
          "with pipe-free rows included, a second paragraph, a case variant, a wrapped or emphasis-split "
          "or hyphenated phrase, a header-marked table, a column-0 lazy list continuation, a name before "
          "the phrase, a wrapped name, a name in another case, or a name elsewhere in the section: the "
          "disclosed over-refusal), a subset manifest rendered with an edition denominator in the "
          "registry row or the reverse-view <p>, and a subset framework carrying ids_total in the JSON "
          "export each report a finding (exit 1); the deferred span itself is read ONLY through the "
          "positive grammar (blank lines, plain paragraph or continuation text, flat plain list items, "
          "column-0 ATX headings), so a blockquote, a fence, a nested or indented list, list-item HTML, "
          "a backtick-info pseudo-fence or a U+00A0 pseudo-close between the phrase and the section end "
          "is cannot-evaluate (exit 2, naming the file and line); a missing page, a missing JSON export, "
          "an unreadable standards dir, an unterminated code fence (naming the file and the fence's "
          "opening line), a fence not at line start, list-item HTML, an HTML block other than a "
          "single-line comment, or a '#' line that is not a heading in the accepted grammar (one to six "
          "'#', then exactly U+0020 or the line end), or a '#' line with leading whitespace (an "
          "indented heading CommonMark can nest in a list item; the legal top-level form is "
          "over-refused, a disclosed conservative refusal), each refused anywhere from the top of the text to "
          "the section's end boundary (naming the file and line, the structure validated BEFORE a "
          "boundary may exclude text, a mis-started section included), a dangling-symlink manifest "
          "failing inside the real loader naming the manifest, a missing/invalid catalogue, and an "
          "unaliased publisher all fail closed (exit 2); a single-line HTML comment above the section "
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
          "staying read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
