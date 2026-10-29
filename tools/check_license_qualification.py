#!/usr/bin/env python3
"""Fail on an unqualified whole-project Apache-2.0 claim, an AIQT mark called registered, or a licence-waiver contradiction.

Three norms, on the public-facing surfaces, each a narrow LITERAL matcher (not a paraphrase detector):

CLASS 1, LICENCE QUALIFICATION. A whole-project license claim must be adjacent-qualified to except
vendored third-party material, so the claim never overstates the licence over material the project only
redistributes. A claim is a VERB-ANCHORED phrase: one of `open source`, `licensed`, `published`, or
`released` immediately governing `under the Apache License 2.0`, matched CASE-INSENSITIVELY and over
WHITESPACE-COLLAPSED text (the tokens are joined by `\\s+`, so a line-wrapped or capitalized claim is
caught: an "open source" wrapped onto the next line before "under the Apache License 2.0", or "Open source
under ...", or "published under ..."). A claim passes only when one of the qualifier markers (`vendored
third-party`, `under its own terms`, `third-party notices`, `see the NOTICE`, or an explicit component name
`Marko` or `CommonMark`) appears WITHIN THE SAME SENTENCE as the claim, on EITHER side of it (the sentence
is bounded by the nearest `.`, `!`, or `?` before and after the claim), so a preceding "Except for vendored
third-party material, X is open source under ..." clears while a marker that sits in a DIFFERENT sentence
("... 2.0. We do not bundle vendored third-party material.") does not.

The verb anchor is what keeps the following legitimate forms from flagging, none of which is a whole-project
claim: a SECTION REFERENCE (`Apache License 2.0, section 4(b)`) has no governing verb; a CONTRIBUTION-terms
sentence ("Contributions are welcome. Under the Apache License 2.0 (section 5), any contribution ...") opens
a sentence with a bare `Under`, not a claim verb; an ACTOR PREDICATE where the licence is the subject ("the
Apache License 2.0 provides the patent grant", "... covers", "... does not relicense", "... grants") has no
`verb under` phrase; and a bare REFCHIP label (`<span>Apache License 2.0</span>`) is neither. A MARKDOWN-LINKED
claim is matched: an optional `[` is allowed before `apache`, so "published under the [Apache License 2.0](LICENSE)"
is caught exactly as the unlinked form is (a claim whose phrase is the anchor text of a markdown link, where
only a `[` separates the verb's `under the` from the phrase, is still a whole-project claim and is not
exempt). Only that `[` is tolerated between the verb and the phrase: intervening `<a ...>` markup is NOT
matched, so a claim split by an inline HTML anchor (verb outside, phrase inside `<a>...</a>`) slips the
matcher (the HTML-ANCHOR-SPLIT residual, disclosed below). There is no reference-only bypass: a
whole-project claim is cleared only by a same-sentence qualifier marker, never by where it sits.

CLASS 2, TRADEMARK REGISTRATION ACCURACY. The AIQT marks are registration-PENDING, so they take the
unregistered-mark symbol U+2122 and are never called registered. Two forms flag. FORM A: `AIQT` or `AIQT
Guardrails`, then optional whitespace and/or the U+2122 mark, then the registered symbol U+00AE, so a
spaced or multi-symbol mark ("AIQT(U+00AE)", "AIQT (U+00AE)", "AIQT(U+2122)(U+00AE)") is caught while the
unregistered symbol U+2122 alone is not. FORM B: an AIQT / AIQT Guardrails SUBJECT (optionally the U+2122
mark) directly governing `is`/`are` and then `registered trademark`. Form B is SUBJECT-ANCHORED, not mere
proximity: a match immediately preceded by a preposition (`to`, `for`, `than`, `with`, `of`, `about`, `via`,
`from`, `by`) is the object of that preposition, not the clause subject ("An alternative to AIQT is a
registered trademark"), and does not flag; a third-party mark called registered in the same sentence ("AIQT
and MITRE ATT&CK, a registered trademark, ...", "OWASP is a registered trademark") is not the AIQT subject
and so does not trip class 2 either.

CLASS 3, LICENCE-WAIVER CONTRADICTION. A project whose licence RESERVES rights must not pair it with a
public-surface phrase waiving those rights. The licence identity is established ONLY by the LEADING
text of the licence file: the gate configuration .aiqt/license-qualification.toml carries `licence =
"<identity>"`, an identity from the CLOSED table LICENCE_IDENTITIES, VERIFIED against the licence
file. Exactly one licence file (LICENSE, LICENSE.md, LICENSE.txt, or COPYING) must be present; the
CANONICAL OPENING TEMPLATE of the declared identity (the opening of the licence text itself, with
controlled whitespace and punctuation variation: \\s+ between tokens, each canonical comma point
tolerating a comma, a dash, or a line break, CRLF included, plus the optional title/copyright preamble
of the licence itself) must be the FIRST NON-BLANK TEXT of the file (one leading U+FEFF byte-order
mark is dropped first) and end on a COMPLETE TOKEN BOUNDARY ("CC0 1.0 Universalist" never verifies
CC0-1.0), and the file must not contain the opening of any OTHER identity ANYWHERE, line-anchored or
not, where the search reads the markdown marker characters * _ ` # = + > - as spaces. A mention of
another identity (wrapped, quoted, or mid-sentence: "This is not CC0 1.0 Universal."; or wrapped in
markdown emphasis, heading, list, or blockquote markers: "**Apache License**", "# Apache License",
"> Apache License") therefore never DECIDES, or un-decides, an identity into use; it REFUSES the file
(cannot-evaluate, exit 2), which fails closed. A DECLARED RIGHTS-WAIVING identity (CC0-1.0, the
Unlicense), the declaration that switches class 3 off, is further verified against the WHOLE
canonical text of that licence (CANONICAL_WAIVER_TEXTS: the CC0 1.0 Universal legal code; the
Unlicense dedication, disclaimer, and closing reference line), token for token, case-insensitively,
with whitespace (reflow, CRLF, indentation) the only tolerated difference: a heading over other terms
("CC0 1.0 Universal" then a proprietary licence), an appended addendum, or one changed token is
cannot-evaluate (exit 2), never a waiver. When the declaration is absent, a rights-RESERVING identity
is INFERRED only when its opening is the leading text of the file and the opening of no other
identity appears anywhere; a RIGHTS-WAIVING identity (CC0-1.0, the Unlicense) is NEVER inferred,
because an inferred waiver would switch class 3 off: a licence file whose only match is a
rights-waiving opening is, without a declaration, cannot-evaluate (exit 2) with a message naming
that identity and the `licence` key to set. An unknown, ambiguous, duplicated, or
unreadable licence file is cannot-evaluate (exit 2), NEVER a clean pass, and nothing about any
particular project is hardcoded (the licence file and configuration of whatever repository is scanned
decide). When the declared and verified licence itself waives rights (CC0-1.0, the Unlicense) class 3
is not applicable and produces no finding, but every [[exception]] row is STILL validated against the
scanned surfaces: a stale row is cannot-evaluate (exit 2) under a rights-waiving licence too. When the
licence reserves rights, each member of the CLOSED waiver-phrase list WAIVER_PHRASES (`no rights
reserved`, `all rights waived`, `dedicated to the public domain`, `copyright waiver`, `cc0`) is
matched on the same scanned surfaces classes 1 and 2 use, case-insensitively, tokens joined by [\\s>]+
and \\b-anchored, so a line-wrapped waiver, a capitalized "No Rights Reserved", the hyphen-adjacent
"CC0-1.0", and a waiver wrapped across markdown blockquote continuation lines ("> no rights" then "
> reserved") are all caught, while "acc0unt" and the CSS HEX COLOUR "#cc0" IN CSS CONTEXT are not.
The colour exclusion requires real CSS context and the WHOLE hash token: a cc0 is excused only when
ALL of these hold: (1) a single "#" sits immediately before it, and the character before that "#" is
whitespace, a colon, an opening parenthesis, or a comma; (2) the token ends with the cc0 (the next
character is whitespace or one of ; } ) , ! or the CSS region ends there) and no "1.0" version
number follows; (3) it lies in a CSS REGION, either the body of an HTML <style> element closed by
</style>, inside a { } block (the nearest brace before it in that body is "{"), or the QUOTED value
of a style attribute of an HTML start tag (tag and attribute names case-insensitive); and (4) it is
a declaration VALUE: the text of its declaration, from just after the nearest { } or ; before it in
that region (or from the region start), opens with a property name and a colon ("color:",
"--accent:"), where a property name is a shape, not a list of known properties: up to two hyphens,
an ASCII letter, then any run of ASCII letters, digits, and hyphens. Any other name, one with an
underscore ("--my_accent:") or a non-ASCII character, is not admitted, so its "#cc0" is a finding
that needs an exception row. Everything else is a finding:
prose ("Our project licence is #cc0", "Licence: #cc0"), a markdown heading "#cc0", a CSS-looking
line in markdown text ("background: #cc0;"), a selector ("a:hover #cc0 {"), an unclosed <style>, a
style attribute written as text, unquoted, or inside another attribute's value, and "#CC0-1.0",
"#CC0 1.0 Universal", "##cc0", "x#cc0", "&#cc0;", "#cc0.5em", or a sentence-final "#cc0."
anywhere; a genuine colour use outside CSS context needs an exception row. No file type admits a
colour: no scanned surface is a stylesheet (they are *.html, *.md, and the three named files). A
six-digit colour such as "#cc0000" never matches the \\b-anchored cc0 phrase at all, so no length
rule applies; every OTHER waiver phrase immediately after "#" (a markdown heading "#no rights
reserved") IS a finding. There is
NO quoted-text exemption: a waiver phrase inside a markdown blockquote, a fenced code block, or an
HTML blockquote element is a finding like any other, whether or not the quote names a licence (a
genuinely quoted third-party licence text is excused only by an explicit exception row). The ONLY
exemption is the [[exception]] list in .aiqt/license-qualification.toml: each row BINDS EXACTLY ONE
occurrence through path (a repo-relative scanned-surface path), phrase (a WAIVER_PHRASES member), a
nonempty reason, and exactly one binder, `line` (the exact text of the line the occurrence starts on,
trailing carriage returns excluded) or `occurrence` (the 1-based index of the match of that phrase
within that file, counting every match in position order); a second occurrence beside an excused one
still flags, and a STALE row (matching no present occurrence) is cannot-evaluate (exit 2), whether or
not class 3 applies. An absent configuration file means no declaration and no exceptions; a present
but malformed one is cannot-evaluate (exit 2), never a clean pass.

SCANNED SURFACES (relative to the repo root): every *.html under site/ and under opf/site/ (recursively),
every *.md under docs/ and under opf/spec/ (recursively), and the named files disclosure.toml, DISCLOSURE.md,
README.md. Raw file text is scanned (not visible-text-stripped): a claim in a meta attribute or an SEO
snippet ships the same overstatement as one in the body, and the qualifier convention holds identically
there. The EXCLUDED surfaces the brief names (LICENSE, NOTICE, anything under tools/, .aiqt/, licenses/,
opf/tools/, and the pure first-party site/downloads/*.txt bundles) fall outside the scanned set by
construction: none is a scanned named file, and none lives under site/, opf/site/, docs/, or opf/spec/
(opf/tools/ is a sibling of opf/site/, not under it, and the .txt bundles are not *.html), so the scope
excludes them without a separate skip list. The .txt bundles carry the pure-first-party "Licensed under the
Apache License 2.0" line correctly and unqualified; the verb `licensed` is in class-1 scope, so those
bundles stay honest only because they are outside the scanned surfaces, not because the verb is excluded.

FAIL CLOSED (exit 2): each of site/, opf/site/, docs/, opf/spec/, disclosure.toml, DISCLOSURE.md, README.md
is a REQUIRED surface; an absent, unwalkable, or wrong-type one is a fail-closed error, never a silent clean
pass (check-fails-closed-on-unreadable). A UTF-8 decode failure on a scanned file is a FINDING (the surface
exists but is unreadable as text), not a crash and not a skip. For class 3 the licence file is likewise
required (exactly one of LICENSE, LICENSE.md, LICENSE.txt, COPYING). PRESENCE of a licence candidate and of
the configuration is decided with os.lstat, never Path.exists: FileNotFoundError is absence, and any OTHER
OSError (EACCES included) on a candidate or on .aiqt/license-qualification.toml is a NAMED class-3
cannot-evaluate (exit 2), never read as absence and never a silent pass. The regular-file test that
follows uses os.stat, never Path.is_file: a stat error on the present entry (a dangling symlink, an
EACCES on its target) is a NAMED cannot-evaluate carrying that error, never "not a regular file", and
an entry of another type is cannot-evaluate saying so. An absent, duplicated, unreadable,
unknown, or ambiguous licence file, a declaration the file does not verify, a rights-waiving opening
without a declaration, a malformed configuration, or a stale exception row is cannot-evaluate (exit 2),
never a clean pass. A class-3 cannot-evaluate NEVER hides class-1 or class-2 findings: those classes always
run, every OSError raised while reading the licence file or the configuration becomes the class-3 error
inside the collector, and the findings are printed, and stdout is FLUSHED, before the class-3 error line
reaches stderr, so the order holds on the terminal and in a merged capture alike (the exit stays 2).

BEST-EFFORT, NOT COMPLETE (residuals). The three classes are calibrated LITERAL matchers, not paraphrase
detectors, so completeness rests on enumeration and review, not on the gate alone; do not read a PASS as
proof the copy is free of such claims. Class 1 sees only the four listed verbs directly governing the exact
`under the Apache License 2.0` phrase: a reworded whole-project claim ("released as open source under Apache
2.0", "under the Apache 2.0 licence") is not seen, a BARE STANDALONE CARD claim with no governing verb
("Apache License 2.0. The standard permissive licence ...") is not seen, an HTML-ANCHOR-SPLIT claim whose
phrase sits inside an inline `<a ...>...</a>` tag with the verb outside it ("published under the <a
...>Apache License 2.0</a>") is not seen, because the matcher tolerates a markdown `[` between the verb's
`under the` and the phrase but not intervening `<a>` markup, an "APACHE 2.0" SHORT-FORM claim written "under
Apache 2.0" without the word "License" (for instance a section heading) is not seen, because the matcher
requires the literal `apache license 2.0` phrase, and the qualifier must sit in the claim's own sentence (a
marker one sentence away is not credited). The qualifier test is same-sentence marker
PRESENCE, not an affirmative-exception parser, so a marker appearing inside an INCLUSION ("AIQT including
Marko is open source under the Apache License 2.0.") or a NEGATION ("... under the Apache License 2.0 with no
exceptions for vendored third-party material.") can still wrongly clear a claim, because the words are
present in the sentence even though they assert no genuine exception. Class 2's Form B is subject-anchored
(`AIQT ... is/are [a] registered trademark`), so an indirect AIQT
assertion with words between the subject and its copula ("AIQT, as noted, is a registered trademark") or a
misstatement phrased without the literal words `registered trademark` or the U+00AE symbol is not caught.
Class 3 sees only the closed waiver-phrase list: a PARAPHRASED waiver ("we claim no
copyright", "released into the public domain", "free of all rights") is not seen; the token joiner spans
whitespace and markdown blockquote markers ONLY, so a waiver phrase whose tokens are split by inline
markup or an entity is not seen: an HTML-SPLIT phrase ("no <b>rights</b> reserved"), a markdown
EMPHASIS-SPLIT phrase ("no *rights* reserved"), and an entity-separated phrase ("no&nbsp;rights
reserved", where entity text, not a space, sits between the tokens) all slip the matcher (a literal
U+00A0 character IS whitespace and is caught); a "#cc0" that is the whole value token of a CSS
declaration in a CSS region (above) is never matched, and both are read SYNTACTICALLY: a <style>
element or a style attribute written as text (in a markdown code span or fence, or in an HTML
comment) still counts as CSS, any name of the admitted property-name shape counts as a property
name ('<p style="licence: #cc0">' is a declaration), and a nested selector that opens like a
declaration inside a { } block ("a { b:hover #cc0 { } }") reads as one, so a waiver spelled "#cc0"
there is not seen, though every OTHER waiver phrase after "#" IS a finding;
a waiver token fused to an underscore ("CC0_1.0" written with an underscore is one \\w+ token) has
no word boundary and is not seen; the other-identity refusal reads markdown markers as spaces but NOT
HTML tags or entities, so an opening split by them ("<b>Apache</b> License", "Apache&nbsp;License") is
not refused (a rights-waiving identity is accepted only as its whole canonical text, so such a missed
mention can only leave a rights-RESERVING identity in force, with class 3 on); a rights-waiving licence
file that differs from its canonical text in any token (a reworded disclaimer, an https reference line
in the Unlicense, an added copyright line) is cannot-evaluate (exit 2), never accepted; a quoted
third-party licence text on a scanned surface now FLAGS and needs an exception row binding each exact
occurrence, so an unmaintained quotation surfaces as a finding, never a silent exemption; and the
licence identity table is closed, so a licence it does not know (for instance LGPL) is cannot-evaluate
(exit 2), not guessed. Grow the vocabulary, and the licence identity table, when a new class is found.

  gen: python3 tools/check_license_qualification.py             scan the required public-facing surfaces
       python3 tools/check_license_qualification.py --self-test  run the fixture corpus (in-memory and
                                                                 temporary-tree legs)

Exit 0 clean, 1 on any finding, 2 on a read error (absent/unwalkable/wrong-type required surface) or a
class-3 cannot-evaluate (an absent, duplicated, unknown, ambiguous, or unreadable licence file or
configuration, an EACCES on either, a declaration the licence file does not verify, a rights-waiving
opening without a declaration, a malformed configuration, or a stale exception row). Class-1 and
class-2 findings are always printed, and flushed, before a class-3 cannot-evaluate error.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_license_qualification.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import argparse
import os
import re
import stat
from pathlib import Path

# Import the sibling module WITHOUT placing this script's own directory AHEAD of the stdlib on sys.path.
# Under `python3 -I` a sys.path insertion at index 0 would let a sibling os.py or shutil.py shadow a
# stdlib import and silently neuter this gate. Appending keeps stdlib precedence (a stdlib import still
# resolves from the stdlib first) while still resolving our own sibling module from this directory; _walk
# is tools-only, so append still finds it. This matches the house pattern (audit_reference.py,
# check_internal_names.py).
sys.path.append(str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)
from _gen_common import load_toml  # noqa: E402  shared fail-closed TOML loader (ValueError on bad TOML)

# CLASS 1: a whole-project license claim is one of the four claim verbs directly governing "under the
# Apache License 2.0". Matched case-insensitively, with \s+ between tokens so a line-wrapped claim is
# caught; \b anchors the verb so "relicensed"/"sublicensed" are not swept in. An optional "[" before
# "apache" matches a markdown-linked claim (under the [Apache License 2.0](LICENSE)). The claim passes only
# when a qualifier marker appears in the SAME SENTENCE (either side); see _has_qualifier.
CLASS1_CLAIM = re.compile(
    r"\b(?:open\s+source|licensed|published|released)\s+under\s+the\s+\[?apache\s+license\s+2\.0",
    re.IGNORECASE,
)
# The qualifier markers, lowercased (the sentence is whitespace-collapsed and lowercased before the search,
# so "see the NOTICE" and "Marko" match here). Component names Marko and CommonMark are the named vendored
# examples the NOTICE covers.
QUALIFIER_MARKERS = (
    "vendored third-party",
    "under its own terms",
    "third-party notices",
    "see the notice",
    "marko",
    "commonmark",
)
# A sentence terminator (. ! ?) counts only when followed by whitespace or end-of-string, so a period
# inside a token ("2.0", "apache.org", a URL ".../LICENSE-2.0)") is followed by a non-space char and does
# not end a sentence. The pattern is ASCII (no literal non-ASCII byte).
SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")

# CLASS 2: the registration-accuracy marks. U+00AE is the registered symbol (banned on an AIQT mark, which
# is registration-pending); U+2122 is the unregistered symbol (correct, never flagged). The symbols are
# built with chr() from their code points so the source stays ASCII (no literal non-ASCII byte in the file).
REGISTERED_SYMBOL = chr(0x00AE)  # (r) U+00AE registered symbol
TRADEMARK_SYMBOL = chr(0x2122)  # (tm) U+2122 unregistered / trademark symbol
# Form A: "AIQT" or "AIQT Guardrails", then optional whitespace and/or the U+2122 mark, then the registered
# symbol. This catches the spaced ("AIQT (r)") and multi-symbol ("AIQT(tm)(r)") variants, while U+2122 on
# its own never reaches the required U+00AE and so is not flagged.
CLASS2_REGISTERED = re.compile(
    r"\bAIQT(?: Guardrails)?\s*" + TRADEMARK_SYMBOL + r"?\s*" + REGISTERED_SYMBOL)
# Form B: an AIQT / AIQT Guardrails SUBJECT asserted to BE registered. The subject (optionally the U+2122
# mark, with tolerant surrounding whitespace) must directly govern "is"/"are" and then "registered
# trademark", with only an optional article between. Subject-anchoring is finished in code by
# _is_object_of_preposition, which rejects a match that is the object of a preceding preposition ("an
# alternative to AIQT is ...") rather than the clause subject.
CLASS2_REGISTERED_TM = re.compile(
    r"\bAIQT(?: Guardrails)?\s*" + TRADEMARK_SYMBOL + r"?\s+(?:is|are)\s+(?:an?\s+)?registered\s+trademark")
# Prepositions that, immediately before an AIQT Form-B match, make AIQT the prepositional object rather than
# the clause subject.
FORM_B_PREPOSITIONS = ("to", "for", "than", "with", "of", "about", "via", "from", "by")
_PRECEDING_PREP = re.compile(
    r"(?:^|\W)(?:" + "|".join(FORM_B_PREPOSITIONS) + r")$", re.IGNORECASE)

CLASS1_LABEL = "class1 (license qualification)"
CLASS2_LABEL = "class2 (trademark registration)"


class _FailClosed(Exception):
    """A required surface is absent, unwalkable, or of the wrong type; the caller maps this to exit 2."""


# The required public-facing surfaces. site/ (aiqt.ai) and opf/site/ (opfiles.ai) are scanned for *.html;
# docs/ and opf/spec/ for *.md; the three named files whole. Every one is REQUIRED (absent -> exit 2).
SITE_HTML_DIRS = ("site", "opf/site")
DOCS_MD_DIRS = ("docs", "opf/spec")
NAMED_FILES = ("disclosure.toml", "DISCLOSURE.md", "README.md")


# ----- CLASS 3: licence-waiver contradiction ------------------------------------------------------------

# The CLOSED licence identity table: one row per known licence identity, (identity, leading_template,
# anywhere_template, reserves_rights). The templates compile the CANONICAL OPENING text of that
# licence with controlled whitespace and punctuation variation (\s+ between tokens; each canonical
# comma point tolerates a comma, a dash, or a line break, so the Debian texts, a CRLF checkout, the
# one-line "Apache License, Version 2.0, January 2004" header, and the "Version 2.0 - January 2004"
# variant all match). The LEADING template is anchored to the START OF THE FILE: only whitespace and
# the optional title/copyright preamble of the licence itself may precede the opening, and the
# template ends on a complete token boundary. It is what ESTABLISHES an identity, so an opening
# buried below other terms never verifies or infers one; a DECLARED rights-waiving identity must
# further be the WHOLE canonical text (CANONICAL_WAIVER_TEXTS). The ANYWHERE template is unanchored
# and only ever REFUSES: the opening of any OTHER identity found anywhere in the file, even wrapped,
# quoted mid-sentence ("This is not CC0 1.0 Universal."), or wrapped in the markdown markers
# _MARKDOWN_MARKS reads as spaces ("**Apache License**", "> Apache License"), is cannot-evaluate
# (exit 2), never a decision. The table is closed by design: the declaration is validated against
# it, an unmatched or multiply-matched licence file is cannot-evaluate (exit 2), never a clean pass,
# and nothing about any particular project is hardcoded (the licence file of whatever repository is
# scanned decides). Grow the table when a new licence is adopted, as the class-1 vocabulary grows.


def _leading(preamble, body):
    """Compile one canonical-opening template anchored to the LEADING non-blank text of the licence
    file: the absolute start of the text, optional whitespace, the optional preamble of the identity
    itself, the opening body, and a complete token boundary at the end (so "CC0 1.0 Universalist" is
    not "CC0 1.0 Universal"). A match deeper in the file never establishes the identity."""
    return re.compile(r"\A\s*" + preamble + body + r"\b", re.IGNORECASE)


def _anywhere(body):
    """Compile one canonical opening for the APPEARS-ANYWHERE refusal test: unanchored and
    word-boundary-bounded, and searched over the licence text with the _MARKDOWN_MARKS characters
    read as spaces, so even a wrapped, quoted, or markdown-formatted mention of the opening of
    ANOTHER identity refuses the file (fail closed) instead of deciding, or silently coexisting with,
    the identity in use."""
    return re.compile(r"\b" + body + r"\b", re.IGNORECASE)


# The markdown marker characters the APPEARS-ANYWHERE search reads as spaces: emphasis (* _), code
# (`), ATX and setext headings (# = -), list items (+), and blockquotes (>). An opening wrapped in
# them ("**Apache License**", "> Apache License", "# Apache License") is still found. HTML tags and
# entities are NOT read through ("<b>Apache</b> License" is not found; a disclosed residual).
_MARKDOWN_MARKS = re.compile(r"[*_`#=+>-]")


# Optional copyright preamble lines ("Copyright (c) 2026 Example") tolerated before the openings whose
# canonical texts carry them (MIT, ISC, BSD). Every licence tolerating them RESERVES rights, so this
# tolerance can only ever turn class 3 ON; the rights-waiving openings tolerate no free-text preamble.
_COPYRIGHT_LINES = r"(?:copyright[^\n]*\n\s*)*"


def _identity(ident, preamble, body, reserves):
    return (ident, _leading(preamble, body), _anywhere(body), reserves)


LICENCE_IDENTITIES = (
    _identity("Apache-2.0", "",
              r"apache\s+license[\s,:]+version\s+2\.0[\s,-]+january\s+2004", True),
    _identity("MIT", r"(?:mit\s+license\s+)?" + _COPYRIGHT_LINES,
              r"permission\s+is\s+hereby\s+granted,?\s+free\s+of\s+charge,?"
              r"\s+to\s+any\s+person\s+obtaining\s+a\s+copy", True),
    _identity("BSD-source-redistribution", _COPYRIGHT_LINES + r"(?:all\s+rights\s+reserved[.\s]+)?",
              r"redistribution\s+and\s+use\s+in\s+source\s+and\s+binary\s+forms", True),
    _identity("GPL-3.0", "",
              r"gnu\s+general\s+public\s+license[\s,]+version\s+3[\s,-]+29\s+june\s+2007", True),
    _identity("GPL-2.0", "",
              r"gnu\s+general\s+public\s+license[\s,]+version\s+2[\s,-]+june\s+1991", True),
    _identity("MPL-2.0", "", r"mozilla\s+public\s+license[\s,]+version\s+2\.0", True),
    _identity("ISC", r"(?:isc\s+license\s+)?" + _COPYRIGHT_LINES,
              r"permission\s+to\s+use,?\s+copy,?\s+modify,?\s+and(?:/or)?"
              r"\s+distribute\s+this\s+software\s+for\s+any\s+purpose", True),
    _identity("CC0-1.0", r"(?:creative\s+commons\s+legal\s+code\s+)?",
              r"cc0\s+1\.0\s+universal", False),
    _identity("Unlicense", "", r"this\s+is\s+free\s+and\s+unencumbered\s+software"
              r"\s+released\s+into\s+the\s+public\s+domain", False),
)
LICENCE_IDENTITY_NAMES = tuple(ident for ident, _, _, _ in LICENCE_IDENTITIES)

# The WHOLE canonical text of each rights-waiving identity. A declared waiving identity switches class 3
# off, so its opening alone never verifies it: the licence file must BE this text, token for token
# (case-insensitively; whitespace, line breaks, and CRLF are the only tolerated difference), decided in
# resolve_licence_identity by _canonical_divergence. The CC0-1.0 entry is the Creative Commons CC0 1.0
# Universal legal code; the Unlicense entry is its dedication, warranty disclaimer, and closing
# reference line. The self-test pins both texts by SHA-256, so an edit to either fails it.
_CANON_CC0 = """\
Creative Commons Legal Code

CC0 1.0 Universal

    CREATIVE COMMONS CORPORATION IS NOT A LAW FIRM AND DOES NOT PROVIDE
    LEGAL SERVICES. DISTRIBUTION OF THIS DOCUMENT DOES NOT CREATE AN
    ATTORNEY-CLIENT RELATIONSHIP. CREATIVE COMMONS PROVIDES THIS
    INFORMATION ON AN "AS-IS" BASIS. CREATIVE COMMONS MAKES NO WARRANTIES
    REGARDING THE USE OF THIS DOCUMENT OR THE INFORMATION OR WORKS
    PROVIDED HEREUNDER, AND DISCLAIMS LIABILITY FOR DAMAGES RESULTING FROM
    THE USE OF THIS DOCUMENT OR THE INFORMATION OR WORKS PROVIDED
    HEREUNDER.

Statement of Purpose

The laws of most jurisdictions throughout the world automatically confer
exclusive Copyright and Related Rights (defined below) upon the creator
and subsequent owner(s) (each and all, an "owner") of an original work of
authorship and/or a database (each, a "Work").

Certain owners wish to permanently relinquish those rights to a Work for
the purpose of contributing to a commons of creative, cultural and
scientific works ("Commons") that the public can reliably and without fear
of later claims of infringement build upon, modify, incorporate in other
works, reuse and redistribute as freely as possible in any form whatsoever
and for any purposes, including without limitation commercial purposes.
These owners may contribute to the Commons to promote the ideal of a free
culture and the further production of creative, cultural and scientific
works, or to gain reputation or greater distribution for their Work in
part through the use and efforts of others.

For these and/or other purposes and motivations, and without any
expectation of additional consideration or compensation, the person
associating CC0 with a Work (the "Affirmer"), to the extent that he or she
is an owner of Copyright and Related Rights in the Work, voluntarily
elects to apply CC0 to the Work and publicly distribute the Work under its
terms, with knowledge of his or her Copyright and Related Rights in the
Work and the meaning and intended legal effect of CC0 on those rights.

1. Copyright and Related Rights. A Work made available under CC0 may be
protected by copyright and related or neighboring rights ("Copyright and
Related Rights"). Copyright and Related Rights include, but are not
limited to, the following:

  i. the right to reproduce, adapt, distribute, perform, display,
     communicate, and translate a Work;
 ii. moral rights retained by the original author(s) and/or performer(s);
iii. publicity and privacy rights pertaining to a person's image or
     likeness depicted in a Work;
 iv. rights protecting against unfair competition in regards to a Work,
     subject to the limitations in paragraph 4(a), below;
  v. rights protecting the extraction, dissemination, use and reuse of data
     in a Work;
 vi. database rights (such as those arising under Directive 96/9/EC of the
     European Parliament and of the Council of 11 March 1996 on the legal
     protection of databases, and under any national implementation
     thereof, including any amended or successor version of such
     directive); and
vii. other similar, equivalent or corresponding rights throughout the
     world based on applicable law or treaty, and any national
     implementations thereof.

2. Waiver. To the greatest extent permitted by, but not in contravention
of, applicable law, Affirmer hereby overtly, fully, permanently,
irrevocably and unconditionally waives, abandons, and surrenders all of
Affirmer's Copyright and Related Rights and associated claims and causes
of action, whether now known or unknown (including existing as well as
future claims and causes of action), in the Work (i) in all territories
worldwide, (ii) for the maximum duration provided by applicable law or
treaty (including future time extensions), (iii) in any current or future
medium and for any number of copies, and (iv) for any purpose whatsoever,
including without limitation commercial, advertising or promotional
purposes (the "Waiver"). Affirmer makes the Waiver for the benefit of each
member of the public at large and to the detriment of Affirmer's heirs and
successors, fully intending that such Waiver shall not be subject to
revocation, rescission, cancellation, termination, or any other legal or
equitable action to disrupt the quiet enjoyment of the Work by the public
as contemplated by Affirmer's express Statement of Purpose.

3. Public License Fallback. Should any part of the Waiver for any reason
be judged legally invalid or ineffective under applicable law, then the
Waiver shall be preserved to the maximum extent permitted taking into
account Affirmer's express Statement of Purpose. In addition, to the
extent the Waiver is so judged Affirmer hereby grants to each affected
person a royalty-free, non transferable, non sublicensable, non exclusive,
irrevocable and unconditional license to exercise Affirmer's Copyright and
Related Rights in the Work (i) in all territories worldwide, (ii) for the
maximum duration provided by applicable law or treaty (including future
time extensions), (iii) in any current or future medium and for any number
of copies, and (iv) for any purpose whatsoever, including without
limitation commercial, advertising or promotional purposes (the
"License"). The License shall be deemed effective as of the date CC0 was
applied by Affirmer to the Work. Should any part of the License for any
reason be judged legally invalid or ineffective under applicable law, such
partial invalidity or ineffectiveness shall not invalidate the remainder
of the License, and in such case Affirmer hereby affirms that he or she
will not (i) exercise any of his or her remaining Copyright and Related
Rights in the Work or (ii) assert any associated claims and causes of
action with respect to the Work, in either case contrary to Affirmer's
express Statement of Purpose.

4. Limitations and Disclaimers.

 a. No trademark or patent rights held by Affirmer are waived, abandoned,
    surrendered, licensed or otherwise affected by this document.
 b. Affirmer offers the Work as-is and makes no representations or
    warranties of any kind concerning the Work, express, implied,
    statutory or otherwise, including without limitation warranties of
    title, merchantability, fitness for a particular purpose, non
    infringement, or the absence of latent or other defects, accuracy, or
    the present or absence of errors, whether or not discoverable, all to
    the greatest extent permissible under applicable law.
 c. Affirmer disclaims responsibility for clearing rights of other persons
    that may apply to the Work or any use thereof, including without
    limitation any person's Copyright and Related Rights in the Work.
    Further, Affirmer disclaims responsibility for obtaining any necessary
    consents, permissions or other rights required for any use of the
    Work.
 d. Affirmer understands and acknowledges that Creative Commons is not a
    party to this document and has no duty or obligation with respect to
    this CC0 or use of the Work.
"""
_CANON_UNLICENSE = """\
This is free and unencumbered software released into the public domain.

Anyone is free to copy, modify, publish, use, compile, sell, or distribute this
software, either in source code form or as a compiled binary, for any purpose,
commercial or non-commercial, and by any means.

In jurisdictions that recognize copyright laws, the author or authors of this
software dedicate any and all copyright interest in the software to the public
domain. We make this dedication for the benefit of the public at large and to
the detriment of our heirs and successors. We intend this dedication to be an
overt act of relinquishment in perpetuity of all present and future rights to
this software under copyright law.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

For more information, please refer to <http://unlicense.org/>
"""
CANONICAL_WAIVER_TEXTS = {"CC0-1.0": _CANON_CC0, "Unlicense": _CANON_UNLICENSE}


def _canonical_divergence(text, canon):
    """None when text IS canon token for token (case-insensitively; whitespace is the only tolerated
    difference), else a short description of the first divergence for the diagnostic: a changed
    token, text past the end of the canonical text, or text that stops short of it."""
    got = text.split()
    want = canon.split()
    for i, (g, w) in enumerate(zip(got, want)):
        if g.casefold() != w.casefold():
            return "token {} is {!r} where the canonical text has {!r}".format(i + 1, g, w)
    if len(got) > len(want):
        return "{} token(s) follow the end of the canonical text, starting {!r}".format(
            len(got) - len(want), got[len(want)])
    if len(got) < len(want):
        return "the text stops after token {} of {}; the canonical text continues {!r}".format(
            len(got), len(want), want[len(got)])
    return None


# The licence file: EXACTLY ONE of these canonical names must be present at the repository root. Zero
# candidates, or more than one, is cannot-evaluate (exit 2): the gate never guesses between files.
LICENCE_FILE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")

# The gate configuration: an optional `licence` declaration (the project licence identity, a
# LICENCE_IDENTITY_NAMES member, verified against the licence file) plus the explicit [[exception]]
# list. Each exception row BINDS EXACTLY ONE waiver-phrase occurrence: path (a repo-relative
# scanned-surface POSIX path), phrase (a WAIVER_PHRASES member), a nonempty reason, and exactly one
# binder, `line` (the exact text of the line the occurrence starts on) or `occurrence` (the 1-based
# index of that phrase's match within that file, counting every match in position order). An absent
# file means no declaration and no exceptions (adopters own their configuration, like
# .aiqt/newtab.toml); a present but malformed file is cannot-evaluate (exit 2), never a clean pass.
CONFIG_PATH = ".aiqt/license-qualification.toml"
CONFIG_LICENCE_KEY = "licence"

# The CLOSED waiver-phrase list. Tokens are joined by [\s>]+ (whitespace plus the markdown blockquote
# continuation marker, so a waiver wrapped across quoted lines stays one phrase) and \b-anchored
# ("acc0unt" is not cc0). The ONLY "#" exclusion is the CSS hex colour "#cc0" in CSS context, decided
# on the WHOLE hash token in _is_css_colour_cc0, not in the regex; every OTHER phrase after "#" (a
# markdown heading "#no rights reserved") flags. Closed and documented: a paraphrased or
# markup-split waiver is a disclosed residual (module docstring), not a matcher ambition.
WAIVER_PHRASES = (
    "no rights reserved",
    "all rights waived",
    "dedicated to the public domain",
    "copyright waiver",
    "cc0",
)

# The CSS colour exclusion, decided in CSS context on the WHOLE hash token: what may precede the "#"
# inside a declaration value (besides whitespace), what may end the token (besides whitespace and the
# end of the CSS region), the version number that makes a following cc0 name the dedication instead,
# and the property name and colon a declaration must open with.
_COLOUR_LEAD = ":(,"
_COLOUR_END = ";}),!"
_CC0_VERSION = re.compile(r"\s+1\.0\b")
_CSS_DECLARATION_HEAD = re.compile(r"\s*-{0,2}[A-Za-z][A-Za-z0-9-]*\s*:")
# The CSS regions: an HTML start tag with its attributes (each quoted value consumed whole, so a
# "style=" written inside another attribute's value is never read as an attribute), one attribute of
# it (group 2 or 3 is a quoted value), and the end tag that closes a <style> element.
_HTML_START_TAG = re.compile(
    r"<([A-Za-z][A-Za-z0-9-]*)((?:\s+[^\s\"'<>/=]+(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'<>=`]+))?)*)"
    r"\s*/?>")
_HTML_ATTRIBUTE = re.compile(r"\s+([^\s\"'<>/=]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|[^\s\"'<>=`]+))?")
_STYLE_END_TAG = re.compile(r"</style\s*>", re.IGNORECASE)


def _css_regions(text):
    """The CSS regions of text, as (start, end, block) triples: the body of every HTML <style> element
    closed by </style> (block True: its declarations sit inside { } blocks), and the quoted value of
    every style attribute of an HTML start tag (block False: the value is one declaration list). Tag
    and attribute names are case-insensitive. Found syntactically: an unclosed <style> element and an
    unquoted style value are no region."""
    regions = []
    for tag in _HTML_START_TAG.finditer(text):
        if tag.group(1).lower() == "style":
            close = _STYLE_END_TAG.search(text, tag.end())
            if close:
                regions.append((tag.end(), close.start(), True))
        for attr in _HTML_ATTRIBUTE.finditer(tag.group(2)):
            if attr.group(1).lower() != "style":
                continue
            for group in (2, 3):
                if attr.group(group) is not None:
                    base = tag.start(2)
                    regions.append((base + attr.start(group), base + attr.end(group), False))
    return regions


def _is_css_colour_cc0(text, start, end):
    """True iff the cc0 match text[start:end] is the CSS hex colour "#cc0" in CSS context, which needs
    ALL of: (1) a single "#" immediately before the match, after whitespace, a colon, an opening
    parenthesis, or a comma; (2) the token ends with the match (whitespace, one of ; } ) , !, or the
    end of the CSS region follows) and no "1.0" version number follows; (3) the "#" and the match lie
    in one _css_regions region, and in a <style> body the nearest brace before the "#" is "{"; and (4)
    the declaration holding it, from just after the nearest { } or ; before the "#" in that region (or
    from the region start), opens with a property name of the _CSS_DECLARATION_HEAD shape (up to two
    hyphens, an ASCII letter, then ASCII letters, digits, and hyphens) and a colon. So
    "<style>a{color:#cc0}</style>" and '<p style="color: #cc0">' are the colour, while prose ("Our
    project licence is #cc0"), a markdown heading "#cc0", "background: #cc0;" outside a style element
    or attribute, a selector, "##cc0", "x#cc0", "#cc0.5em", and "#CC0 1.0" are not. A six-digit colour
    such as "#cc0000" never matches the \\b-anchored cc0 phrase at all, so no length rule applies, and
    no other waiver phrase is ever excluded by this test."""
    hash_at = start - 1
    if hash_at < 1 or text[hash_at] != "#":
        return False
    if not (text[hash_at - 1].isspace() or text[hash_at - 1] in _COLOUR_LEAD):
        return False
    region = next(((a, b, block) for a, b, block in _css_regions(text) if a <= hash_at and end <= b),
                  None)
    if region is None:
        return False
    a, b, block = region
    if end < b and not (text[end].isspace() or text[end] in _COLOUR_END):
        return False
    if _CC0_VERSION.match(text, end, b):
        return False
    brace = max(text.rfind("{", a, hash_at), text.rfind("}", a, hash_at))
    if block and (brace == -1 or text[brace] != "{"):
        return False
    head = max(a, brace + 1, text.rfind(";", a, hash_at) + 1)
    return _CSS_DECLARATION_HEAD.match(text, head, hash_at) is not None


def _waiver_regex(phrase):
    """Compile one waiver phrase: tokens joined by [\\s>]+ and \\b-anchored, case-insensitive. The
    CSS colour exclusion is NOT in the regex (a lookbehind here would silence every phrase after "#");
    it is applied in scan_waivers through _is_css_colour_cc0, to the cc0 phrase alone."""
    body = r"[\s>]+".join(re.escape(tok) for tok in phrase.split())
    return re.compile(r"\b" + body + r"\b", re.IGNORECASE)


WAIVER_RES = tuple((phrase, _waiver_regex(phrase)) for phrase in WAIVER_PHRASES)

CLASS3_LABEL = "class3 (licence-waiver contradiction)"


def _pick_licence_file(present):
    """Choose the licence file from the canonical names present (an iterable of LICENCE_FILE_NAMES
    members); exactly one must be present. Raises _FailClosed (exit 2) on zero or several."""
    names = [n for n in LICENCE_FILE_NAMES if n in set(present)]
    if not names:
        raise _FailClosed(
            "no licence file found (exactly one of {} is required to evaluate class 3)".format(
                ", ".join(LICENCE_FILE_NAMES)))
    if len(names) > 1:
        raise _FailClosed(
            "more than one licence file found ({}); keep exactly one so the licence identity is "
            "unambiguous".format(", ".join(names)))
    return names[0]


def resolve_licence_identity(declared, licence_name, licence_text):
    """Resolve the project licence identity; return (identity, reserves_rights). `declared` is the
    validated `licence` declaration (a LICENCE_IDENTITY_NAMES member) or None when the key is absent;
    `licence_name` and `licence_text` are the chosen licence file and its text. An identity is
    ESTABLISHED only by the LEADING text of the file (the _leading templates); the unanchored
    _anywhere templates only ever REFUSE. DECLARED: the declaration decides and is VERIFIED, so the
    canonical opening of the declared identity must be the leading text of the file, the opening of
    no OTHER identity may appear anywhere in it (searched with the _MARKDOWN_MARKS read as spaces),
    and a declared RIGHTS-WAIVING identity must be the WHOLE canonical text of that licence
    (CANONICAL_WAIVER_TEXTS, compared by _canonical_divergence), because the declaration is what
    switches class 3 off. INFERRED (no declaration): accepted only for a rights-RESERVING identity
    whose opening leads the file, with no other opening anywhere; a RIGHTS-WAIVING identity (CC0-1.0,
    the Unlicense) is NEVER inferred, because inference would switch class 3 off on text nobody
    declared. One leading U+FEFF byte-order mark is dropped first (an encoding signature, not licence
    text). Every other outcome raises _FailClosed (exit 2) with a message naming the `licence` key:
    cannot-evaluate, never a clean pass, never decided by a mention."""
    if licence_text.startswith("\ufeff"):
        licence_text = licence_text[1:]
    leading = [(ident, reserves) for ident, lead_rx, _, reserves in LICENCE_IDENTITIES
               if lead_rx.match(licence_text)]
    flat = _MARKDOWN_MARKS.sub(" ", licence_text)
    mentioned = [ident for ident, _, any_rx, _ in LICENCE_IDENTITIES if any_rx.search(flat)]
    if declared is not None:
        if all(ident != declared for ident, _ in leading):
            raise _FailClosed(
                "{} declares {} = \"{}\" but {} does not open with the canonical opening of that"
                " licence (the declaration is verified against the leading text of the licence file);"
                " fix the declaration or the licence file".format(
                    CONFIG_PATH, CONFIG_LICENCE_KEY, declared, licence_name))
        extra = [ident for ident in mentioned if ident != declared]
        if extra:
            raise _FailClosed(
                "{} declares {} = \"{}\" but {} also contains the canonical opening of {}; a licence"
                " file must carry exactly one canonical opening".format(
                    CONFIG_PATH, CONFIG_LICENCE_KEY, declared, licence_name, ", ".join(extra)))
        identity = next((ident, reserves) for ident, reserves in leading if ident == declared)
        if not identity[1]:
            # A waiving identity missing from CANONICAL_WAIVER_TEXTS is compared with the empty text,
            # so it always refuses: a table row alone can never switch class 3 off.
            divergence = _canonical_divergence(licence_text, CANONICAL_WAIVER_TEXTS.get(declared, ""))
            if divergence is not None:
                raise _FailClosed(
                    "{} declares {} = \"{}\", a rights-waiving licence, but {} is not the whole"
                    " canonical text of that licence ({}); a declared waiver switches class 3 off only"
                    " on the complete text, so fix the declaration or the licence file".format(
                        CONFIG_PATH, CONFIG_LICENCE_KEY, declared, licence_name, divergence))
        return identity
    if len(leading) > 1:
        raise _FailClosed(
            "{} opens with the canonical opening of more than one licence identity ({}); reduce it to"
            " the text of one licence, then set {} in {} to that identity".format(
                licence_name, ", ".join(ident for ident, _ in leading), CONFIG_LICENCE_KEY,
                CONFIG_PATH))
    if len(leading) == 1:
        ident, reserves = leading[0]
        extra = [m for m in mentioned if m != ident]
        if extra:
            raise _FailClosed(
                "{} contains the canonical opening of more than one licence identity ({}); reduce it"
                " to the text of one licence, then set {} in {} to that identity".format(
                    licence_name, ", ".join([ident] + extra), CONFIG_LICENCE_KEY, CONFIG_PATH))
        if not reserves:
            raise _FailClosed(
                "{} opens with the rights-waiving licence {}, and a rights-waiving identity is never"
                " inferred (inference would switch class 3 off); set {} = \"{}\" in {} to declare"
                " it, verified against the leading text of the licence file".format(
                    licence_name, ident, CONFIG_LICENCE_KEY, ident, CONFIG_PATH))
        return ident, reserves
    waiving = [ident for ident, _, _, reserves in LICENCE_IDENTITIES
               if ident in mentioned and not reserves]
    if waiving:
        raise _FailClosed(
            "{} mentions the rights-waiving licence {} but does not open with it, and a rights-waiving"
            " identity is never inferred (inference would switch class 3 off); set {} = \"<identity>\""
            " in {} and keep {} on the canonical text of one licence".format(
                licence_name, ", ".join(waiving), CONFIG_LICENCE_KEY, CONFIG_PATH, licence_name))
    if mentioned:
        raise _FailClosed(
            "{} mentions {} but does not open with any known licence identity (the identity is decided"
            " only by the leading text of the licence file, never by a mention); set {} ="
            " \"<identity>\" in {} and keep {} on the canonical text of one licence".format(
                licence_name, ", ".join(mentioned), CONFIG_LICENCE_KEY, CONFIG_PATH, licence_name))
    raise _FailClosed(
        "{} matches no known licence identity (closed table); set {} = \"<identity>\" in {} to one"
        " of {} and keep {} on the canonical text of that licence, or grow the table".format(
            licence_name, CONFIG_LICENCE_KEY, CONFIG_PATH,
            ", ".join(LICENCE_IDENTITY_NAMES), licence_name))

def _line_at(text, pos):
    """The exact text of the line containing offset pos: the text between the surrounding line feeds,
    trailing carriage returns excluded (the `line` binder compares against exactly this)."""
    a = text.rfind("\n", 0, pos) + 1
    b = text.find("\n", pos)
    if b == -1:
        b = len(text)
    return text[a:b].rstrip("\r")


def scan_waivers(text, rel, rows, consumed):
    """Return the class-3 (label, snippet) findings in one surface text whose repo-relative POSIX path
    is rel, given the parsed exception rows and the caller-owned parallel `consumed` flag list.
    Occurrences of one phrase are numbered 1-based in position order within this file (a cc0 excluded
    as the CSS hex colour "#cc0" in CSS context is not an occurrence); an UNCONSUMED row excuses one
    occurrence when its path and phrase match and its binder does (occurrence equals the index, or
    line equals the exact text of the line the occurrence starts on). A row is consumed by the first
    occurrence it excuses, so a SECOND occurrence beside an excused one still flags, even on the same
    or an identical line; the caller reports rows left unconsumed as stale."""
    findings = []
    for phrase, rx in WAIVER_RES:
        matches = [m for m in rx.finditer(text)
                   if not (phrase == "cc0" and _is_css_colour_cc0(text, m.start(), m.end()))]
        for index, m in enumerate(matches, start=1):
            line_text = _line_at(text, m.start())
            excused = False
            for i, (path, row_phrase, kind, value) in enumerate(rows):
                if consumed[i] or path != rel or row_phrase != phrase:
                    continue
                if (kind == "occurrence" and value == index) or (kind == "line" and value == line_text):
                    consumed[i] = True
                    excused = True
                    break
            if not excused:
                findings.append((CLASS3_LABEL, _snippet(text, m.start(), m.end())))
    return findings


def evaluate_class3(declared, licence_name, licence_text, rows, surfaces):
    """The whole class-3 evaluation over in-memory inputs (pure: the self-test drives it directly and
    _collect feeds it the real files). surfaces is an iterable of (rel, text) pairs. Returns (findings,
    identity, reserves_rights) where each finding is (rel, label, snippet); raises _FailClosed on an
    unresolved identity or a stale exception row (cannot-evaluate, exit 2). Exception rows are
    validated whether or not class 3 applies: the scan always runs, so a stale row refuses under a
    rights-waiving licence too; under such a licence the scan findings are then discarded (class 3 is
    not applicable), never reported."""
    identity, reserves = resolve_licence_identity(declared, licence_name, licence_text)
    findings = []
    consumed = [False] * len(rows)
    for rel, text in surfaces:
        findings.extend((rel, label, snip) for label, snip in scan_waivers(text, rel, rows, consumed))
    stale = ["{} / {!r}".format(path, phrase) for (path, phrase, _, _), used in zip(rows, consumed)
             if not used]
    if stale:
        raise _FailClosed(
            "stale exception row(s) in {} (no matching occurrence): {}".format(
                CONFIG_PATH, "; ".join(stale)))
    if not reserves:
        # The project licence itself waives rights: class 3 is not applicable and the scan findings
        # are discarded; the scan above still validated (and consumed) every exception row first.
        return [], identity, reserves
    return findings, identity, reserves

def _valid_surface_path(path):
    """True iff path is a clean repo-relative POSIX path on a surface this gate scans: a NAMED_FILES
    member, an *.html under a SITE_HTML_DIRS root, or an *.md under a DOCS_MD_DIRS root. Only TWO
    shape checks sit before the NAMED/root/suffix tests, because only they DISCRIMINATE: an interior
    backslash ("docs/sub\\a.md") or a dot, parent, or empty segment ("docs/./a.md", "docs/../a.md",
    "docs//a.md") would otherwise pass those tests and only surface later as a confusing stale row.
    The former empty-, padded-, and absolute-path checks were fully shadowed (every such path already
    fails the NAMED membership, root-prefix, and suffix tests below) and are dropped."""
    if "\\" in path or any(seg in ("", ".", "..") for seg in path.split("/")):
        return False
    if path in NAMED_FILES:
        return True
    for roots, suffix in ((SITE_HTML_DIRS, ".html"), (DOCS_MD_DIRS, ".md")):
        for root in roots:
            if path.startswith(root + "/") and path.endswith(suffix):
                return True
    return False


def _parse_config(data, source):
    """Validate a parsed gate configuration; return (declared licence identity or None, the exception
    row tuple, each row (path, phrase, binder kind, binder value)). Any other shape raises _FailClosed
    (exit 2), each diagnostic naming its specific defect (the self-test asserts the diagnostics)."""
    if not isinstance(data, dict) or not set(data) <= {CONFIG_LICENCE_KEY, "exception"}:
        raise _FailClosed(
            "{}: expected only the {} key and [[exception]] rows".format(source, CONFIG_LICENCE_KEY))
    declared = data.get(CONFIG_LICENCE_KEY)
    if declared is not None:
        if not isinstance(declared, str):
            raise _FailClosed("{}: {} must be a string".format(source, CONFIG_LICENCE_KEY))
        if declared not in LICENCE_IDENTITY_NAMES:
            raise _FailClosed(
                "{}: {} = {!r} is not in the closed identity table ({})".format(
                    source, CONFIG_LICENCE_KEY, declared, ", ".join(LICENCE_IDENTITY_NAMES)))
    raw_rows = data.get("exception", [])
    if not isinstance(raw_rows, list):
        raise _FailClosed("{}: exception must be an array of tables".format(source))
    rows = []
    for i, row in enumerate(raw_rows):
        where = "{}: exception {}".format(source, i + 1)
        if not isinstance(row, dict):
            raise _FailClosed("{} must be a table".format(where))
        keys = set(row)
        if not {"path", "phrase", "reason"} <= keys or keys - {"path", "phrase", "reason", "line", "occurrence"}:
            raise _FailClosed(
                "{} must carry exactly path, phrase, reason, and one binder (line or occurrence)".format(
                    where))
        binders = keys & {"line", "occurrence"}
        if len(binders) != 1:
            raise _FailClosed(
                "{} must carry exactly one binder: line (the exact line text) or occurrence (the "
                "1-based match index)".format(where))
        for key in ("path", "phrase", "reason"):
            if not isinstance(row[key], str):
                raise _FailClosed("{} {} must be a string".format(where, key))
        if not row["reason"].strip():
            raise _FailClosed("{} needs a nonempty reason".format(where))
        if row["phrase"] not in WAIVER_PHRASES:
            raise _FailClosed(
                "{} phrase {!r} is not in the closed waiver-phrase list".format(where, row["phrase"]))
        if not _valid_surface_path(row["path"]):
            raise _FailClosed(
                "{} path {!r} is not a repo-relative scanned-surface path (a named file, a site or "
                "opf/site *.html, or a docs or opf/spec *.md)".format(where, row["path"]))
        kind = binders.pop()
        value = row[kind]
        if kind == "line" and (not isinstance(value, str) or not value):
            raise _FailClosed("{} line must be a nonempty string (the exact line text)".format(where))
        if kind == "occurrence" and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise _FailClosed("{} occurrence must be an integer of at least 1 (1-based)".format(where))
        entry = (row["path"], row["phrase"], kind, value)
        if entry in rows:
            raise _FailClosed(
                "{} duplicates an earlier row (same path, phrase, and binder)".format(where))
        rows.append(entry)
    return declared, tuple(rows)

# Injection seams for the file-layer self-test: an EACCES on a presence probe, a type probe, or a read
# is injected here (the testing account may be root, where a chmod 000 denies nothing), and production
# behaviour is identical to calling the underlying functions directly.
_lstat = os.lstat
_stat = os.stat
_load_toml_file = load_toml


def _read_file_text(p):
    return p.read_text(encoding="utf-8")


def _present(p, what):
    """True iff a directory entry exists at p, decided by os.lstat, NEVER Path.exists (which reads any
    OSError as absence): FileNotFoundError is absence; any other OSError (an EACCES on the entry or on
    a parent, an EIO, ...) raises a NAMED class-3 cannot-evaluate, because an unreadable licence
    candidate or configuration must never silently shrink the candidate set or drop the
    declaration."""
    try:
        _lstat(p)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _FailClosed(
            "{} cannot be inspected ({}); cannot evaluate class 3".format(what, exc)) from exc
    return True


def _require_regular_file(p, what):
    """Require the entry _present found at p to be a regular file, decided by os.stat (which follows
    a symlink), NEVER Path.is_file (which reads any OSError as "not a file"): a stat error on the
    present entry (a dangling symlink, an EACCES on its target) raises a NAMED class-3
    cannot-evaluate carrying that error, and an entry of another type raises one saying so."""
    try:
        mode = _stat(p).st_mode
    except OSError as exc:
        raise _FailClosed(
            "{} cannot be inspected ({}); cannot evaluate class 3".format(what, exc)) from exc
    if not stat.S_ISREG(mode):
        raise _FailClosed("{} exists but is not a regular file; cannot evaluate class 3".format(what))


def _load_config(root):
    """Read the gate configuration; an absent file means no declaration and no exceptions, a malformed
    one raises _FailClosed (exit 2), and an unreadable one is never read as absent (_present fails
    closed on a non-ENOENT OSError, _require_regular_file names a stat error; a read-time OSError
    propagates to _collect, which converts it to the class-3 error). Returns (declared, rows)."""
    p = root / CONFIG_PATH
    if not _present(p, CONFIG_PATH):
        return None, ()
    _require_regular_file(p, CONFIG_PATH)
    try:
        data = _load_toml_file(p)
    except ValueError as exc:
        raise _FailClosed("{}: {}".format(CONFIG_PATH, exc)) from exc
    return _parse_config(data, CONFIG_PATH)


def _read_licence(root):
    """Pick and read the licence file. Raises _FailClosed (exit 2) when not exactly one canonical name
    is present (an unreadable candidate fails closed in _present, never reads as absent), the chosen
    one cannot be stat-ed or is not a regular file (_require_regular_file), or it cannot be decoded
    as UTF-8; a read-time OSError propagates to _collect, which converts it to the class-3 error so
    class-1/2 findings stay visible."""
    name = _pick_licence_file([n for n in LICENCE_FILE_NAMES if _present(root / n, n)])
    p = root / name
    _require_regular_file(p, name)
    try:
        return name, _read_file_text(p)
    except UnicodeDecodeError as exc:
        raise _FailClosed(
            "{} could not be read as UTF-8 ({}); cannot evaluate class 3".format(name, exc)) from exc


def _snippet(text, start, end):
    """A short (~50 char) one-line context snippet around a match, whitespace collapsed, for a finding."""
    a = max(0, start - 25)
    b = min(len(text), end + 25)
    return ("..." if a else "") + re.sub(r"\s+", " ", text[a:b]).strip() + ("..." if b < len(text) else "")


def _sentence_bounds(text, start, end):
    """Return (left, right) offsets of the sentence containing text[start:end], bounded by the nearest
    sentence terminator (. ! ?) before `start` and at or after `end`. A terminator counts only when it is
    followed by whitespace or end-of-string, so a period INSIDE a token (the "." in "2.0" or "apache.org",
    or a URL such as ".../LICENSE-2.0)") is followed by a non-space char and is not mistaken for a sentence
    end. Terminators inside the matched span (start <= b < end) are ignored, so the claim's own "2.0" is
    never a boundary either."""
    left = 0
    right = len(text)
    for m in SENTENCE_END_RE.finditer(text):
        b = m.start()
        if b < start:
            left = b + 1
        elif b >= end:
            right = b
            break
    return left, right


def _has_qualifier(text, start, end):
    """True iff a qualifier marker appears in the claim's own sentence (either side of the claim)."""
    left, right = _sentence_bounds(text, start, end)
    sentence = re.sub(r"\s+", " ", text[left:right]).lower()
    return any(mk in sentence for mk in QUALIFIER_MARKERS)


def _is_object_of_preposition(text, start):
    """True iff the AIQT Form-B match at `start` is the object of an immediately preceding preposition (so
    AIQT is not the clause subject asserted to be registered)."""
    return bool(_PRECEDING_PREP.search(text[:start].rstrip()))


def scan_text(text):
    """Return a list of (label, snippet) findings in one surface's raw text (both classes)."""
    findings = []
    # CLASS 1: every verb-anchored claim whose own sentence carries no qualifier marker.
    for m in CLASS1_CLAIM.finditer(text):
        if _has_qualifier(text, m.start(), m.end()):
            continue
        findings.append((CLASS1_LABEL, _snippet(text, m.start(), m.end())))
    # CLASS 2 Form A: an AIQT mark carrying the registered symbol.
    for m in CLASS2_REGISTERED.finditer(text):
        findings.append((CLASS2_LABEL, _snippet(text, m.start(), m.end())))
    # CLASS 2 Form B: an AIQT subject asserted to be registered (subject-anchored, not a prepositional
    # object).
    for m in CLASS2_REGISTERED_TM.finditer(text):
        if _is_object_of_preposition(text, m.start()):
            continue
        findings.append((CLASS2_LABEL, _snippet(text, m.start(), m.end())))
    return findings


def _collect(root):
    """Scan every required surface under `root`; return (findings, license_claim_count,
    licence_identity, reserves_rights, class3_error). Classes 1 and 2 ALWAYS run and their findings are
    always returned; class 3 is evaluated after them, and any class-3 cannot-evaluate (licence file,
    declaration, configuration, or stale exception row) comes back as class3_error (identity and
    reserves are then None) so the caller reports the class-1 and class-2 findings FIRST and still
    exits 2; every OSError raised while reading the licence file or the configuration likewise
    becomes class3_error HERE, so an unreadable licence or configuration never hides them. Raises
    _FailClosed only for an absent/unwalkable/wrong-type required surface (caller -> exit 2); an
    OSError from a fail-closed surface walk or surface read still propagates."""
    surfaces = []
    for sub in SITE_HTML_DIRS:
        d = root / sub
        if not d.is_dir():
            raise _FailClosed("{} is a required surface but is absent or not a directory".format(sub))
        surfaces.extend(sorted(walk_files(d, suffixes={".html"})))
    for sub in DOCS_MD_DIRS:
        d = root / sub
        if not d.is_dir():
            raise _FailClosed("{} is a required surface but is absent or not a directory".format(sub))
        surfaces.extend(sorted(walk_files(d, suffixes={".md"})))
    for name in NAMED_FILES:
        p = root / name
        if not p.is_file():
            raise _FailClosed("required surface {} is absent or not a file".format(name))
        surfaces.append(p)

    findings = []
    claims = 0
    texts = []
    for f in surfaces:
        rel = f.relative_to(root)
        try:
            text = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append("{}: could not read as UTF-8".format(rel))
            continue
        claims += len(CLASS1_CLAIM.findall(text))
        for label, snip in scan_text(text):
            findings.append("{}: {} -> {}".format(rel, label, snip))
        texts.append((rel.as_posix(), text))

    identity = reserves = class3_error = None
    try:
        licence_name, licence_text = _read_licence(root)
        declared, rows = _load_config(root)
        waivers, identity, reserves = evaluate_class3(declared, licence_name, licence_text, rows, texts)
        for rel, label, snip in waivers:
            findings.append("{}: {} -> {}".format(rel, label, snip))
    except _FailClosed as exc:
        class3_error = str(exc)
    except OSError as exc:
        # Every OSError from the class-3 reads (the licence file or the configuration) becomes the
        # class-3 error here, inside _collect, so the class-1 and class-2 findings collected above
        # always render first and the exit stays 2.
        class3_error = ("the licence file or configuration could not be read ({}); "
                        "cannot evaluate class 3".format(exc))
    return findings, claims, identity, reserves, class3_error


# Fixture corpus. CLEAN lines MUST NOT flag; FLAGGED lines MUST flag (with the expected class). These map
# to the rework spec's clean and flagged cases, named here for what they do. The registered/unregistered
# symbols are injected from the chr()-built constants so this source stays ASCII (no literal symbol byte).
_R = REGISTERED_SYMBOL
_TM = TRADEMARK_SYMBOL
CLEAN_FIXTURES = [
    # class 1, adjacent-qualified in the SAME sentence, marker AFTER the claim.
    "AIQT is open source under the Apache License 2.0 (except vendored third-party material, which remains under its own terms).",
    "OPFiles is open source under the Apache License 2.0, except vendored third-party material, which remains under its own terms: Marko under the MIT licence.",
    "...open source under the Apache License 2.0 (vendored third-party material under its own terms).",
    # class 1, qualifier PRECEDING the claim in the same sentence (either-side association).
    "Except for vendored third-party material, AIQT is open source under the Apache License 2.0.",
    # class 1, component-name qualifier (Marko / CommonMark) rather than the generic marker.
    "OPFiles is open source under the Apache License 2.0, except the vendored Marko and CommonMark examples.",
    # class 1, the different verb "licensed" still respects the same-sentence qualifier.
    "This distribution is licensed under the Apache License 2.0 (vendored third-party material under its own terms).",
    # class 1 non-claims: section reference, contribution-terms sentence, actor predicate, and refchip
    # label (no whole-project claim, so none flags).
    "Modified files must carry a notice (Apache License 2.0, section 4(b)).",
    "Contributions are welcome. Under the Apache License 2.0 (section 5), any contribution you submit is under the same terms.",
    "The Apache License 2.0 provides the patent grant, and it does not relicense third-party material.",
    "<span class=\"refchip\">Apache License 2.0</span>",
    # class 1, a MARKDOWN-LINKED claim cleared by a same-sentence qualifier marker (README's own line 48 shape).
    "AIQT Guardrails is published under the [Apache License 2.0](LICENSE); see NOTICE for attribution and third-party notices.",
    # class 1, a markdown link whose URL carries dots (LICENSE-2.0, apache.org) does NOT end the sentence,
    # so the same-sentence component-name qualifier still clears the claim (Change 1 false-positive repro).
    "AIQT is published under the [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0), except Marko and CommonMark.",
    # class 2 correct: the unregistered symbol and "registration pending" are accurate, never flagged.
    "AIQT{tm} and AIQT Guardrails{tm} are trademarks of Jeff Posluns (registration pending).".format(tm=_TM),
    "This project uses the unregistered mark AIQT{tm} throughout.".format(tm=_TM),
    "AIQT Guardrails{tm} is a trademark of Jeff Posluns.".format(tm=_TM),
    # class 2 third-party: a non-AIQT mark called registered is not the AIQT subject and stays clean.
    "MITRE ATT&CK is a registered trademark of The MITRE Corporation.",
    "OWASP is a registered trademark of the OWASP Foundation.",
    # class 2 Form B prepositional object: AIQT is the object of "to", not the clause subject.
    "An alternative to AIQT is a registered trademark strategy.",
    # class 2 subject-anchoring: an AIQT token in the same sentence as a third-party registered mark stays
    # clean, because AIQT is not the thing asserted to be registered.
    "AIQT and MITRE ATT&CK, a registered trademark, are referenced together.",
    "AIQT maps controls; OWASP is a registered trademark of the OWASP Foundation.",
]
FLAGGED_FIXTURES = [
    # class 1: a claim whose sentence carries no qualifier marker, across the verb and wrapping variants.
    ("AIQT is open source\n under the Apache License 2.0 and nothing more.", "class1"),
    ("Open source under the Apache License 2.0 with no further notice.", "class1"),
    ("This project is published under the Apache License 2.0 today.", "class1"),
    ("It was released under the Apache License 2.0 last spring.", "class1"),
    # class 1: the qualifier sits in a DIFFERENT sentence, so it is not credited (codex repro).
    ("AIQT is open source under the Apache License 2.0. We do not bundle vendored third-party material.", "class1"),
    # class 1: a claim adjacent to an <article>/</a> still flags (no naive <a>-substring exemption).
    ("<article>AIQT is open source under the Apache License 2.0.</a>", "class1"),
    # class 1: a "spread the" preface does not exempt (no naive "read the"-substring exemption).
    ("We spread the open source under the Apache License 2.0 today.", "class1"),
    # class 1: an UNQUALIFIED markdown-linked claim flags (the "[" no longer hides it).
    ("AIQT is open source under the [Apache License 2.0](LICENSE) and nothing else.", "class1"),
    # class 2 Form A: spaced and multi-symbol registered marks, plus the immediate form.
    ("AIQT {r} is the mark shown here.".format(r=_R), "class2"),
    ("AIQT{tm}{r} appears in the footer.".format(tm=_TM, r=_R), "class2"),
    ("AIQT{r} is a registered trademark.".format(r=_R), "class2"),
    ("AIQT Guardrails{r} ships today.".format(r=_R), "class2"),
    # class 2 Form B: an AIQT / AIQT Guardrails subject asserted to be registered (subject-anchored).
    ("AIQT is a registered trademark of Jeff Posluns.", "class2"),
    ("AIQT{tm} is a registered trademark.".format(tm=_TM), "class2"),
    ("AIQT Guardrails are registered trademarks.", "class2"),
]

# Class-3 licence-file fixtures: realistic canonical openings (Debian-style layouts), the controlled
# whitespace and punctuation variants, and the fail-closed shapes (unknown, ambiguous, short-notice,
# LGPL, empty). For a rights-RESERVING identity the resolver reads only the opening region, so those
# texts are abbreviated below it; a declared rights-WAIVING identity is verified against the WHOLE
# canonical text, so those fixtures are the whole texts.
_LIC_APACHE = ("                                 Apache License\n"
               "                           Version 2.0, January 2004\n"
               "                        http://www.apache.org/licenses/\n\n"
               "   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n")
_LIC_APACHE_CRLF = _LIC_APACHE.replace("\n", "\r\n")
_LIC_APACHE_ONELINE = "Apache License, Version 2.0, January 2004\nTERMS AND CONDITIONS\n"
_LIC_APACHE_DASH = _LIC_APACHE.replace("Version 2.0, January 2004", "Version 2.0 - January 2004")
# An incidental MID-SENTENCE mention of another identity: it never DECIDES an identity, it REFUSES
# the file (fail closed), declared or not.
_LIC_APACHE_MENTION = _LIC_APACHE + "\nThis is not CC0 1.0 Universal.\n"
_LIC_MIT = ("MIT License\n\nCopyright (c) 2026 Example\n\n"
            "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
            "of this software and associated documentation files (the \"Software\"), to deal\n"
            "in the Software without restriction.\n")
_LIC_GPL2 = ("                    GNU GENERAL PUBLIC LICENSE\n"
             "                       Version 2, June 1991\n\n"
             " Copyright (C) 1989, 1991 Free Software Foundation, Inc.\n")
_LIC_GPL3 = ("                    GNU GENERAL PUBLIC LICENSE\n"
             "                       Version 3, 29 June 2007\n\n"
             " Copyright (C) 2007 Free Software Foundation, Inc.\n")
_LIC_MPL = "Mozilla Public License Version 2.0\n==================================\n\n1. Definitions\n"
_LIC_BSD = ("Redistribution and use in source and binary forms, with or without\n"
            "modification, are permitted provided that the following conditions are met:\n")
_LIC_ISC = ("ISC License\n\nCopyright (c) 2026\n\n"
            "Permission to use, copy, modify, and distribute this software for any\n"
            "purpose with or without fee is hereby granted.\n")
# A rights-waiving licence verifies only as its WHOLE canonical text: the canonical texts themselves,
# reflowed, CRLF, and BOM-prefixed copies verify; the bare opening, an appended or substituted
# proprietary term, and one changed token never do.
_LIC_CC0 = CANONICAL_WAIVER_TEXTS["CC0-1.0"]
_LIC_UNLICENSE = CANONICAL_WAIVER_TEXTS["Unlicense"]
_LIC_CC0_REFLOWED = " ".join(_LIC_CC0.split()) + "\n"
_LIC_UNLICENSE_CRLF = _LIC_UNLICENSE.replace("\n", "\r\n")
_LIC_CC0_OPENING_ONLY = ("Creative Commons Legal Code\n\nCC0 1.0 Universal\n\n"
                         "    CREATIVE COMMONS CORPORATION IS NOT A LAW FIRM\n")
_LIC_CC0_THEN_PROPRIETARY = ("CC0 1.0 Universal\n\nACME PROPRIETARY LICENCE. All rights reserved. No use"
                             " without a signed agreement.\n")
_LIC_CC0_PLUS_ADDENDUM = _LIC_CC0 + "\nACME ADDENDUM: all rights reserved; no use without a licence.\n"
_LIC_CC0_ONE_WORD = _LIC_CC0.replace("unconditionally waives,", "unconditionally reserves,")
_LIC_UNLICENSE_THEN_PROPRIETARY = ("This is free and unencumbered software released into the public"
                                   " domain.\n\nExcept that ACME reserves all rights in it.\n")
# Openings wrapped in markdown markers are still found anywhere, so they refuse the file.
_LIC_CC0_BOLD_APACHE = "CC0 1.0 Universal\n\n**Apache License**\nVersion 2.0, January 2004\n"
_LIC_CC0_QUOTED_APACHE = "CC0 1.0 Universal\n> Apache License\n> Version 2.0, January 2004\n"
_LIC_APACHE_QUOTED_CC0 = _LIC_APACHE + "\n> This is not CC0 1.0\n> Universal.\n"
_LIC_APACHE_BOLD_MPL = _LIC_APACHE + "\n**Mozilla Public License** Version 2.0\n"
_LIC_MIT_SETEXT_APACHE = _LIC_MIT + "\nApache License\n==============\nVersion 2.0, January 2004\n"
_LIC_MIT_ATX_APACHE = _LIC_MIT + "\n# Apache License\n# Version 2.0, January 2004\n"
# LGPL-3.0 with the canonical wrap: "GNU General Public\nLicense," is never a line-anchored GPL opening.
_LIC_LGPL3 = ("                   GNU LESSER GENERAL PUBLIC LICENSE\n"
              "                       Version 3, 29 June 2007\n\n"
              "  This version of the GNU Lesser General Public License incorporates\n"
              "the terms and conditions of version 3 of the GNU General Public\n"
              "License, supplemented by the additional permissions listed below.\n")
_LIC_APACHE_PLUS_MIT = _LIC_APACHE + "\n" + _LIC_MIT
_LIC_DUAL = "Licensed under either of\n\n" + _LIC_APACHE + "\nor\n\n" + _LIC_MIT
_LIC_SHORT_NOTICE = ("Licensed under the Apache License, Version 2.0 (the \"License\");\n"
                     "you may not use this file except in compliance with the License.\n")
_LIC_UNKNOWN = "You may use this software on alternate Tuesdays only.\n"
# The decided fail-open vectors: a rights-waiving opening that is not the leading text of the file
# (a wrapped proprietary note, a quoted vendor sentence), a wrapped denial, a suffix extension of a
# waiving opening, and an unsupported licence followed by a known heading. None may ever establish an
# identity; each must refuse (cannot-evaluate), never switch class 3 off.
_LIC_PROPRIETARY_CC0_WRAP = ("ACME PROPRIETARY SOFTWARE LICENCE\n\n"
                             "All rights reserved. The icons bundled in assets/ are under\n"
                             "CC0 1.0 Universal; everything else is reserved.\n")
_LIC_QUOTED_UNLICENSE = ("ACME internal licence. All rights reserved.\n\n"
                         "The vendor README quotes: \"This is free and unencumbered software\n"
                         "released into the public domain.\" That dedication is the vendor one, not ours.\n")
_LIC_APACHE_WRAPPED_DENIAL = _LIC_APACHE + "\nThis is not\nCC0 1.0 Universal.\n"
_LIC_CC0_SUFFIX = "CC0 1.0 Universalist Covenant: all rights reserved by the Universalist order.\n"
_LIC_UNKNOWN_PLUS_APACHE = _LIC_UNKNOWN + "\n" + _LIC_APACHE

# (declared identity or None, licence text, expected (identity, reserves_rights) pair, or the specific
# _FailClosed diagnostic substring the resolver must raise). Expected OUTCOMES, not bare refusals: the
# self-test asserts the diagnostic too, so a refusal for the wrong reason fails the fixture.
LICENCE_IDENTITY_CASES = [
    (None, _LIC_APACHE, ("Apache-2.0", True)),
    (None, _LIC_APACHE_CRLF, ("Apache-2.0", True)),
    (None, _LIC_APACHE_ONELINE, ("Apache-2.0", True)),
    (None, _LIC_APACHE_DASH, ("Apache-2.0", True)),
    (None, _LIC_MIT, ("MIT", True)),
    (None, _LIC_GPL2, ("GPL-2.0", True)),
    (None, _LIC_GPL3, ("GPL-3.0", True)),
    (None, _LIC_MPL, ("MPL-2.0", True)),
    (None, _LIC_BSD, ("BSD-source-redistribution", True)),
    (None, _LIC_ISC, ("ISC", True)),
    # a rights-waiving identity is NEVER inferred, however canonical the file: declare it instead.
    (None, _LIC_CC0, "never inferred"),
    (None, _LIC_UNLICENSE, "never inferred"),
    # declared, a rights-waiving identity verifies only as the WHOLE canonical text of that licence,
    # whitespace (reflow, CRLF) and one leading byte-order mark aside ...
    ("CC0-1.0", _LIC_CC0, ("CC0-1.0", False)),
    ("Unlicense", _LIC_UNLICENSE, ("Unlicense", False)),
    ("CC0-1.0", _LIC_CC0_REFLOWED, ("CC0-1.0", False)),
    ("Unlicense", _LIC_UNLICENSE_CRLF, ("Unlicense", False)),
    ("CC0-1.0", "\ufeff" + _LIC_CC0, ("CC0-1.0", False)),
    # ... so the bare heading, a heading over proprietary terms, an appended addendum, and one changed
    # token never switch class 3 off.
    ("CC0-1.0", _LIC_CC0_OPENING_ONLY, "not the whole canonical text"),
    ("CC0-1.0", _LIC_CC0_THEN_PROPRIETARY, "not the whole canonical text"),
    ("CC0-1.0", _LIC_CC0_PLUS_ADDENDUM, "not the whole canonical text"),
    ("CC0-1.0", _LIC_CC0_ONE_WORD, "not the whole canonical text"),
    ("Unlicense", _LIC_UNLICENSE_THEN_PROPRIETARY, "not the whole canonical text"),
    # a leading byte-order mark is dropped before the leading text is read.
    (None, "\ufeff" + _LIC_APACHE, ("Apache-2.0", True)),
    # the opening of another identity wrapped in markdown emphasis, blockquote, or heading markers
    # is still found anywhere in the file, so it refuses, declared or inferred.
    ("CC0-1.0", _LIC_CC0_BOLD_APACHE, "also contains the canonical opening of Apache-2.0"),
    ("CC0-1.0", _LIC_CC0_QUOTED_APACHE, "also contains the canonical opening of Apache-2.0"),
    ("Apache-2.0", _LIC_APACHE_QUOTED_CC0, "also contains the canonical opening of CC0-1.0"),
    (None, _LIC_APACHE_QUOTED_CC0, "more than one licence identity"),
    (None, _LIC_APACHE_BOLD_MPL, "more than one licence identity"),
    ("MIT", _LIC_MIT_SETEXT_APACHE, "also contains the canonical opening of Apache-2.0"),
    ("MIT", _LIC_MIT_ATX_APACHE, "also contains the canonical opening of Apache-2.0"),
    # a wrapped rights-waiving opening below proprietary terms never decides, inferred or declared.
    (None, _LIC_PROPRIETARY_CC0_WRAP, "never inferred"),
    ("CC0-1.0", _LIC_PROPRIETARY_CC0_WRAP, "does not open with the canonical opening"),
    # a quoted waiving opening (the Unlicense first sentence) is a mention, not an identity.
    (None, _LIC_QUOTED_UNLICENSE, "never inferred"),
    ("Unlicense", _LIC_QUOTED_UNLICENSE, "does not open with the canonical opening"),
    # a wrapped denial of a waiving identity refuses the file rather than deciding anything.
    (None, _LIC_APACHE_WRAPPED_DENIAL, "more than one licence identity"),
    ("Apache-2.0", _LIC_APACHE_WRAPPED_DENIAL, "also contains the canonical opening"),
    (None, _LIC_APACHE_MENTION, "more than one licence identity"),
    ("Apache-2.0", _LIC_APACHE_MENTION, "also contains the canonical opening"),
    # a suffix extension of a waiving opening is no opening at all (complete token boundary).
    (None, _LIC_CC0_SUFFIX, "matches no known licence identity"),
    ("CC0-1.0", _LIC_CC0_SUFFIX, "does not open with the canonical opening"),
    # an unsupported licence followed by a known heading does not lead the file and never decides.
    (None, _LIC_UNKNOWN_PLUS_APACHE, "does not open with any known licence identity"),
    ("Apache-2.0", _LIC_UNKNOWN_PLUS_APACHE, "does not open with the canonical opening"),
    (None, _LIC_LGPL3, "matches no known licence identity"),
    (None, _LIC_SHORT_NOTICE, "matches no known licence identity"),
    (None, _LIC_UNKNOWN, "matches no known licence identity"),
    (None, "", "matches no known licence identity"),
    (None, _LIC_APACHE_PLUS_MIT, "more than one licence identity"),
    (None, _LIC_DUAL, "does not open with any known licence identity"),
    ("Apache-2.0", _LIC_APACHE, ("Apache-2.0", True)),
    ("Apache-2.0", _LIC_MIT, "does not open with the canonical opening"),
    ("Apache-2.0", _LIC_APACHE_PLUS_MIT, "also contains the canonical opening"),
    ("MIT", _LIC_APACHE_PLUS_MIT, "does not open with the canonical opening"),
]

# Licence-file selection: exactly one canonical name may be present.
LICENCE_FILE_CASES = [
    ([], "no licence file found"),
    (["LICENSE"], "LICENSE"),
    (["LICENSE.md"], "LICENSE.md"),
    (["LICENSE.txt"], "LICENSE.txt"),
    (["COPYING"], "COPYING"),
    (["LICENSE", "COPYING"], "more than one licence file"),
    (["LICENSE.md", "LICENSE.txt"], "more than one licence file"),
]

# Class-3 surface-scan fixtures: (text, repo-relative path, exception rows, expected finding count[,
# required snippet substrings of the findings left standing, in position order]). There is NO
# quoted-text exemption, so the quoted first-party shapes here MUST flag; the exception rows bind
# one occurrence each, and the snippet substrings pin WHICH occurrence a row excused.
_ROW_HIST_OCC = ("docs/history.md", "copyright waiver", "occurrence", 1)
_ROW_HIST_LINE = ("docs/history.md", "copyright waiver", "line",
                  "Historical note: the 2019 draft shipped with a copyright waiver.")
CLASS3_SCAN_CASES = [
    # a waiver beside a rights-reserving licence is a finding on each public surface type.
    ("This project is offered with no rights reserved.", "site/index.html", (), 1),
    ("Everything here is dedicated to the public domain.", "docs/terms.md", (), 1),
    ("summary = \"All rights waived.\"", "disclosure.toml", (), 1),
    # a line-wrapped and a capitalized waiver are caught.
    ("The work ships with no rights\n    reserved going forward.", "docs/wrap.md", (), 1),
    ("No Rights Reserved.", "docs/caps.md", (), 1),
    # the bare CC0 identifier, including the hyphen-adjacent form, contradicts a reserving licence.
    ("This repository is CC0-1.0.", "docs/cc0.md", (), 1),
    # there is NO quoted-text exemption: a licence-naming markdown quote, a fenced code block, and an
    # HTML blockquote element all still flag (the former generic exemption is removed).
    ("> Our license: no rights reserved.\n", "docs/quote.md", (), 1),
    ("```\nOur license: no rights reserved.\n```\n", "docs/fence.md", (), 1),
    ("<blockquote>Our license: no rights reserved.</blockquote>", "site/quote.html", (), 1),
    # a waiver wrapped ACROSS blockquote continuation lines is one phrase and flags.
    ("> No rights\n> reserved.\n", "docs/wrapped-quote.md", (), 1),
    ("> This project keeps no rights\n> reserved at all.\n", "docs/wrapped-quote2.md", (), 1),
    # word-boundary and CSS-colour near misses are not waivers; the reserving formula is not a waiver.
    ("The acc0unt ledger reserves all rights.", "docs/accounts.md", (), 0),
    ("<style>a{color:#cc0}</style>", "site/style.html", (), 0),
    ("All rights reserved.", "docs/reserved.md", (), 0),
    # an occurrence-bound exception row excuses exactly its occurrence ...
    ("Historical note: the 2019 draft shipped with a copyright waiver.",
     "docs/history.md", (_ROW_HIST_OCC,), 0),
    # ... a line-bound row likewise ...
    ("Historical note: the 2019 draft shipped with a copyright waiver.",
     "docs/history.md", (_ROW_HIST_LINE,), 0),
    # ... and a SECOND occurrence beside the excused one still flags (one row, one occurrence).
    ("Historical note: the 2019 draft shipped with a copyright waiver. This project grants a "
     "copyright waiver.", "docs/history.md", (_ROW_HIST_OCC,), 1),
    ("Historical note: the 2019 draft shipped with a copyright waiver.\nA second copyright waiver "
     "line.\n", "docs/history.md", (_ROW_HIST_LINE,), 1),
    # an occurrence row for the second occurrence excuses the SECOND: the finding left standing is
    # the FIRST occurrence (the snippet assertion pins it, so any-occurrence consumption fails).
    ("A first copyright waiver. The excused second copyright waiver.",
     "docs/second.md", (("docs/second.md", "copyright waiver", "occurrence", 2),), 1,
     ("A first",)),
    # a row for a DIFFERENT path does not excuse the occurrence.
    ("Historical note: the 2019 draft shipped with a copyright waiver.",
     "docs/other.md", (_ROW_HIST_OCC,), 1),
    # a row whose line text no longer matches does not excuse the occurrence.
    ("A moved copyright waiver sentence.", "docs/history.md", (_ROW_HIST_LINE,), 1),
    # ONE row, one occurrence, even when both occurrences share one line: the second still flags.
    ("A copyright waiver and a second copyright waiver here.", "docs/oneline.md",
     (("docs/oneline.md", "copyright waiver", "line",
       "A copyright waiver and a second copyright waiver here."),), 1,
     ("second copyright waiver",)),
    # ... and even when two IDENTICAL lines each carry the phrase: one row excuses the FIRST of them
    # (occurrences are taken in position order), and the distinct context around each line pins
    # which one is left standing (the second, whose snippet carries the closing "omega" context).
    ("Opening context alpha.\nA copyright waiver.\n"
     "A long separating paragraph keeps the two snippets apart.\nA copyright waiver.\n"
     "Closing context omega.\n", "docs/dupline.md",
     (("docs/dupline.md", "copyright waiver", "line", "A copyright waiver."),), 1, ("omega",)),
    # the CSS-colour exclusion is cc0-only: every other waiver phrase after "#" flags (markdown
    # headings, quoted headings, fences, HTML), and so does "# cc0" ...
    ("#no rights reserved\n", "docs/hash-heading.md", (), 1),
    ("> #no rights reserved\n", "docs/hash-quote.md", (), 1),
    ("```\n#all rights waived\n```\n", "docs/hash-fence.md", (), 1),
    ("<blockquote>#copyright waiver</blockquote>", "site/hash-quote.html", (), 1),
    ("# cc0 is discussed in this heading.", "docs/hash-spaced.md", (), 1),
    # ... and "#cc0" OUTSIDE CSS context flags, whatever the file type: a first-party claim in prose
    # (with a label colon or in parentheses too), a bare markdown heading, a plain-text sentence, and
    # a CSS-looking line in markdown text that is in no <style> element or style attribute ...
    ("Our project licence is #cc0", "README.md", (), 1),
    ("Licence: #cc0", "docs/hash-label.md", (), 1),
    ("This work is free (#cc0).", "docs/hash-paren.md", (), 1),
    ("#cc0", "docs/hash-bare.md", (), 1),
    ("The accent colour is #cc0 in the banner.", "docs/palette.txt", (), 1),
    ("background: #cc0;", "docs/hash-colour.md", (), 1),
    ("border: 1px solid #cc0 !important", "docs/hash-spaced-end.md", (), 1),
    # ... as does a cc0 that is not the whole token after a single "#": a version suffix or a
    # following version number, a doubled or fused "#", an entity, and a token that runs on past the
    # cc0 (a dimension, a sentence-final full stop) ...
    ("#CC0-1.0", "docs/hash-version.md", (), 1),
    ("#CC0 1.0 Universal", "docs/hash-name.md", (), 1),
    ("##cc0", "docs/hash-double.md", (), 1),
    ("x#cc0", "docs/hash-fused.md", (), 1),
    ("&#cc0;", "docs/hash-entity.md", (), 1),
    ("#cc0.5em", "docs/hash-dimension.md", (), 1),
    ("The accent is #cc0.", "docs/hash-stop.md", (), 1),
    # ... and the near-CSS shapes the rule does not admit: element text after a style attribute, an
    # unclosed <style>, a style attribute written as text, unquoted, or inside another attribute's
    # value, a selector (outside any { } block, or after a closed one), a declaration with no
    # property name or with a name outside the admitted shape (an underscore), and inside a
    # declaration value a cc0 with no "#", a fused, doubled, or "=" led "#", a dimension or a quote
    # run on after it, or a following version number.
    ("<p style=\"color: red\">Licence: #cc0</p>", "site/hash-after-attr.html", (), 1),
    ("<style>a{color:#cc0}", "site/hash-unclosed.html", (), 1),
    ("style=\"color:#cc0\"", "docs/hash-attr-text.md", (), 1),
    ("<p style=color:#cc0>x</p>", "site/hash-unquoted.html", (), 1),
    ("<p title=\"style='color:#cc0'\">x</p>", "site/hash-in-title.html", (), 1),
    ("<style>a:hover #cc0 {color:red}</style>", "site/hash-selector.html", (), 1),
    ("<style>a{color:red} p:focus #cc0 {color:red}</style>", "site/hash-selector-after.html", (), 1),
    ("<style>p{ #cc0 }</style>", "site/hash-no-property.html", (), 1),
    ("<style>:root{--my_accent: #cc0}</style>", "site/a.html", (), 1),
    ("<style>p{font-family: cc0}</style>", "site/hash-no-hash.html", (), 1),
    ("<style>a{color:x#cc0}</style>", "site/hash-css-fused.html", (), 1),
    ("<style>a{color:##cc0}</style>", "site/hash-css-double.html", (), 1),
    ("<p style=\"color:=#cc0\">x</p>", "site/hash-css-equals.html", (), 1),
    ("<style>a{width:#cc0.5em}</style>", "site/hash-css-dimension.html", (), 1),
    ("<p style=\"color:#cc0'\">x</p>", "site/hash-css-quote.html", (), 1),
    ("<p style=\"color:#cc0 1.0\">x</p>", "site/hash-css-version.html", (), 1),
    # ... while the whole colour value of a declaration in a CSS region stays excluded: in a <style>
    # element (with attributes, multi-line, after a "(" or ended by "!important" or ",", inside a
    # nested at-rule block, upper-case, a custom property) and in a double- or single-quoted style
    # attribute (upper-case, raw HTML in markdown). A six-digit colour never matches the cc0 phrase.
    ("<style media=\"screen\">\n.banner {\n  border: 1px solid #cc0 !important;\n"
     "  background: linear-gradient(#cc0, #fff);\n}\n</style>", "site/hash-block.html", (), 0),
    ("<STYLE>@media print { :root { --accent: #CC0; } }</STYLE>", "site/hash-nested.html", (), 0),
    ("<p style=\"color:#cc0\">x</p>", "site/hash-attr.html", (), 0),
    ("<p class='a' STYLE='background: #cc0; color: #000'>x</p>", "site/hash-attr-single.html", (), 0),
    ("A <span style=\"color: #cc0\">yellow</span> accent.", "docs/hash-inline-html.md", (), 0),
    ("#cc0000", "docs/hash-six.md", (), 0),
]

# End-to-end class-3 evaluation fixtures over in-memory trees: (declared, licence text, exception rows,
# surfaces, expected). expected is (finding count, identity, reserves_rights) or a _FailClosed
# diagnostic substring. These drive evaluate_class3, the same function _collect calls, so the reserves
# branch, exception consumption and validation under BOTH reserving and waiving identities, and
# stale-row reporting are all exercised.
_SURF_WAIVER = (("site/index.html", "This project is offered with no rights reserved."),)
_SURF_HIST = (("docs/history.md", "Historical note: the 2019 draft shipped with a copyright waiver."),)
CLASS3_EVALUATE_CASES = [
    # a rights-reserving licence turns class 3 ON: the waiver is a finding.
    (None, _LIC_APACHE, (), _SURF_WAIVER, (1, "Apache-2.0", True)),
    # a DECLARED rights-waiving licence turns class 3 OFF: the same surface, no finding.
    ("CC0-1.0", _LIC_CC0, (), _SURF_WAIVER, (0, "CC0-1.0", False)),
    # an UNDECLARED rights-waiving licence never turns class 3 off: cannot-evaluate, never inferred.
    (None, _LIC_CC0, (), _SURF_WAIVER, "never inferred"),
    # an unknown licence is cannot-evaluate, never a pass.
    (None, _LIC_UNKNOWN, (), _SURF_WAIVER, "matches no known licence identity"),
    # a bound exception row excuses its one occurrence end to end.
    (None, _LIC_APACHE, (_ROW_HIST_OCC,), _SURF_HIST, (0, "Apache-2.0", True)),
    # a row matching no occurrence anywhere is STALE: cannot-evaluate.
    (None, _LIC_APACHE, (_ROW_HIST_OCC,), _SURF_WAIVER, "stale exception row"),
    # rows are validated under a rights-waiving licence too: a binding row is not stale (its excusal
    # is moot, class 3 being off), while a row matching nothing refuses just the same.
    ("CC0-1.0", _LIC_CC0, (_ROW_HIST_OCC,), _SURF_HIST, (0, "CC0-1.0", False)),
    ("CC0-1.0", _LIC_CC0, (_ROW_HIST_OCC,), _SURF_WAIVER, "stale exception row"),
    # the declaration is verified end to end.
    ("Apache-2.0", _LIC_MIT, (), _SURF_WAIVER, "does not open with the canonical opening"),
]

# Gate-configuration parsing fixtures: the valid shape parses to its declaration and rows; each
# malformed shape must fail closed (exit 2 through _FailClosed) WITH ITS SPECIFIC DIAGNOSTIC, so a
# refusal for the wrong reason is a self-test failure, never a pass.
_CFG_VALID = (
    "licence = \"Apache-2.0\"\n\n"
    "[[exception]]\npath = \"docs/history.md\"\nphrase = \"copyright waiver\"\noccurrence = 1\n"
    "reason = \"Describes the withdrawn 2019 draft, not this project.\"\n\n"
    "[[exception]]\npath = \"site/notes.html\"\nphrase = \"cc0\"\n"
    "line = \"The icon set was CC0 upstream.\"\n"
    "reason = \"Names the upstream dedication, not this project.\"\n")
_CFG_VALID_PARSED = ("Apache-2.0", (
    ("docs/history.md", "copyright waiver", "occurrence", 1),
    ("site/notes.html", "cc0", "line", "The icon set was CC0 upstream."),
))
_ROW_OK = "path = \"docs/x.md\"\nphrase = \"cc0\"\noccurrence = 1\n"
_CFG_MALFORMED = (
    ("unknown top-level key", "waivers = []\n", "expected only the licence key"),
    ("non-string licence", "licence = 3\n", "licence must be a string"),
    ("unknown licence identity", "licence = \"GPL\"\n", "not in the closed identity table"),
    ("wrong exception type", "exception = 3\n", "must be an array of tables"),
    ("non-table row", "exception = [3]\n", "exception 1 must be a table"),
    ("missing reason", "[[exception]]\n" + _ROW_OK, "must carry exactly path, phrase, reason"),
    ("extra key", "[[exception]]\n" + _ROW_OK + "reason = \"r\"\nextra = 1\n",
     "must carry exactly path, phrase, reason"),
    ("missing binder", "[[exception]]\npath = \"docs/x.md\"\nphrase = \"cc0\"\nreason = \"r\"\n",
     "exactly one binder"),
    ("both binders", "[[exception]]\n" + _ROW_OK + "line = \"x cc0 y\"\nreason = \"r\"\n",
     "exactly one binder"),
    ("non-string path", "[[exception]]\npath = 1\nphrase = \"cc0\"\noccurrence = 1\nreason = \"r\"\n",
     "path must be a string"),
    ("blank reason", "[[exception]]\n" + _ROW_OK + "reason = \" \"\n", "needs a nonempty reason"),
    ("unknown phrase", "[[exception]]\npath = \"docs/x.md\"\nphrase = \"public domain\"\n"
     "occurrence = 1\nreason = \"r\"\n", "not in the closed waiver-phrase list"),
    ("empty path", "[[exception]]\npath = \"\"\nphrase = \"cc0\"\noccurrence = 1\nreason = \"r\"\n",
     "not a repo-relative scanned-surface path"),
    ("blank path", "[[exception]]\npath = \" \"\nphrase = \"cc0\"\noccurrence = 1\nreason = \"r\"\n",
     "not a repo-relative scanned-surface path"),
    ("absolute path", "[[exception]]\npath = \"/tmp/a.md\"\nphrase = \"cc0\"\noccurrence = 1\n"
     "reason = \"r\"\n", "not a repo-relative scanned-surface path"),
    ("parent traversal", "[[exception]]\npath = \"../docs/a.md\"\nphrase = \"cc0\"\noccurrence = 1\n"
     "reason = \"r\"\n", "not a repo-relative scanned-surface path"),
    ("dot segment", "[[exception]]\npath = \"./docs/a.md\"\nphrase = \"cc0\"\noccurrence = 1\n"
     "reason = \"r\"\n", "not a repo-relative scanned-surface path"),
    ("backslash path", "[[exception]]\npath = 'docs\\a.md'\nphrase = \"cc0\"\noccurrence = 1\n"
     "reason = \"r\"\n", "not a repo-relative scanned-surface path"),
    ("unscanned root", "[[exception]]\npath = \"tools/a.md\"\nphrase = \"cc0\"\noccurrence = 1\n"
     "reason = \"r\"\n", "not a repo-relative scanned-surface path"),
    ("wrong suffix", "[[exception]]\npath = \"docs/a.txt\"\nphrase = \"cc0\"\noccurrence = 1\n"
     "reason = \"r\"\n", "not a repo-relative scanned-surface path"),
    ("interior dot segment", "[[exception]]\npath = \"docs/./a.md\"\nphrase = \"cc0\"\noccurrence = 1\n"
     "reason = \"r\"\n", "not a repo-relative scanned-surface path"),
    ("interior parent segment", "[[exception]]\npath = \"docs/../a.md\"\nphrase = \"cc0\"\n"
     "occurrence = 1\nreason = \"r\"\n", "not a repo-relative scanned-surface path"),
    ("interior backslash", "[[exception]]\npath = 'docs/sub\\a.md'\nphrase = \"cc0\"\noccurrence = 1\n"
     "reason = \"r\"\n", "not a repo-relative scanned-surface path"),
    ("doubled slash", "[[exception]]\npath = \"docs//a.md\"\nphrase = \"cc0\"\noccurrence = 1\n"
     "reason = \"r\"\n", "not a repo-relative scanned-surface path"),
    ("zero occurrence", "[[exception]]\npath = \"docs/a.md\"\nphrase = \"cc0\"\noccurrence = 0\n"
     "reason = \"r\"\n", "occurrence must be an integer of at least 1"),
    ("boolean occurrence", "[[exception]]\npath = \"docs/a.md\"\nphrase = \"cc0\"\noccurrence = true\n"
     "reason = \"r\"\n", "occurrence must be an integer of at least 1"),
    ("string occurrence", "[[exception]]\npath = \"docs/a.md\"\nphrase = \"cc0\"\noccurrence = \"1\"\n"
     "reason = \"r\"\n", "occurrence must be an integer of at least 1"),
    ("empty line binder", "[[exception]]\npath = \"docs/a.md\"\nphrase = \"cc0\"\nline = \"\"\n"
     "reason = \"r\"\n", "line must be a nonempty string"),
    ("duplicate row", "[[exception]]\n" + _ROW_OK + "reason = \"r\"\n[[exception]]\n" + _ROW_OK
     + "reason = \"other\"\n", "duplicates an earlier row"),
)


def _raise_memory_error(text):
    """A stand-in for tomllib.loads that fails as an exhausted allocation does. No fixture text can
    raise MemoryError without exhausting memory, so the parse handlers' MemoryError arm is driven
    through this loader instead."""
    raise MemoryError("injected allocation failure")


# Configuration texts that do not load, with the exception class each parse handler must name and the
# loader that reads them (None is tomllib.loads): a syntax error (tomllib.TOMLDecodeError, a
# ValueError), an array nested ten times past the default recursion limit (RecursionError), and a
# valid text whose loader raises MemoryError. Both legs' handlers are driven with each.
_CFG_UNPARSEABLE = (
    ("not TOML", "licence = \n", "TOMLDecodeError", None),
    ("nested too deep", "licence = " + "[" * 10000 + "]" * 10000 + "\n", "RecursionError", None),
    ("out of memory", "licence = \"Apache-2.0\"\n", "MemoryError", _raise_memory_error),
)

# Result-rendering fixtures: (findings, claims, identity, reserves, class3_error, expected exit code,
# required stdout substring or None, required stderr substring or None). The first case is the
# never-hidden guarantee: class-1/2 findings stay visible when class 3 cannot evaluate, exit still 2.
RENDER_CASES = [
    (["docs/a.md: class1 (license qualification) -> x"], 1, None, None, "licence trouble", 2,
     "class1 (license qualification)", "licence trouble"),
    ([], 0, None, None, "licence trouble", 2, None, "licence trouble"),
    (["docs/a.md: class1 (license qualification) -> x"], 1, "Apache-2.0", True, None, 1, "FAIL", None),
    ([], 3, "Apache-2.0", True, None, 0, "reserves rights", None),
    ([], 2, "CC0-1.0", False, None, 0, "not applicable", None),
]


def _valid_config_failures(text, want, loads=None):
    """The valid-configuration leg over one fixture text: [] when it parses as TOML and _parse_config
    returns want, else the NAMED failure line (a text that does not parse is reported by its exception
    class, never escapes). loads reads the text (None is tomllib.loads)."""
    import tomllib
    try:
        parsed = _parse_config(tomllib.loads(text) if loads is None else loads(text), "fixture")
    except (ValueError, RecursionError, MemoryError) as exc:
        return ["CONFIG PARSE: valid fixture did not parse as TOML: {}".format(type(exc).__name__)]
    except _FailClosed as exc:
        return ["CONFIG PARSE: valid fixture refused: {}".format(exc)]
    if parsed != want:
        return ["CONFIG PARSE: valid fixture parsed to {!r}".format((parsed,))]
    return []


def _malformed_config_failures(label, text, want, loads=None):
    """The malformed-configuration leg over one fixture text: [] when it parses as TOML and
    _parse_config refuses it with want in the diagnostic, else the NAMED failure line (a text that
    does not parse is reported by its exception class, never escapes). loads reads the text (None is
    tomllib.loads)."""
    import tomllib
    try:
        _parse_config(tomllib.loads(text) if loads is None else loads(text), "fixture")
    except (ValueError, RecursionError, MemoryError) as exc:
        return ["CONFIG PARSE: malformed fixture {} did not parse as TOML: {}".format(
            label, type(exc).__name__)]
    except _FailClosed as exc:
        if want not in str(exc):
            return ["CONFIG PARSE: {} refused with the wrong diagnostic ({!r} not in {!r})".format(
                label, want, str(exc))]
        return []
    return ["CONFIG PARSE: malformed fixture accepted ({})".format(label)]


def _self_test_class3():
    """The class-3 fixture legs; return the failure strings (the caller aggregates and reports)."""
    import hashlib
    failures = []
    # The canonical rights-waiving texts are pinned by SHA-256 (CC0 1.0 Universal legal code, 7048
    # bytes; the Unlicense with its closing reference line), and every rights-waiving identity in
    # the table must carry one.
    for ident, want_sha in (
            ("CC0-1.0", "a2010f343487d3f7618affe54f789f5487602331c0a8d03f49e9a7c547cf0499"),
            ("Unlicense", "4af8fb61da2cb1f815106d1e41f10836ed0b8f3dd830bfb3a8c02c2621bd76ad")):
        got_sha = hashlib.sha256(CANONICAL_WAIVER_TEXTS.get(ident, "").encode("utf-8")).hexdigest()
        if got_sha != want_sha:
            failures.append("CANONICAL TEXT: {} hashes to {}, expected {}".format(
                ident, got_sha, want_sha))
    waiving = {ident for ident, _, _, reserves in LICENCE_IDENTITIES if not reserves}
    if set(CANONICAL_WAIVER_TEXTS) != waiving:
        failures.append("CANONICAL TEXT: texts for {} but rights-waiving identities {}".format(
            sorted(CANONICAL_WAIVER_TEXTS), sorted(waiving)))
    for present, expected in LICENCE_FILE_CASES:
        try:
            got = _pick_licence_file(present)
        except _FailClosed as exc:
            got = str(exc)
        ok = (got == expected) if expected in LICENCE_FILE_NAMES else expected in got
        if not ok:
            failures.append("LICENCE FILE PICK: {!r} expected {!r} got {!r}".format(
                present, expected, got))
    for declared, lic_text, expected in LICENCE_IDENTITY_CASES:
        try:
            got = resolve_licence_identity(declared, "LICENSE", lic_text)
        except _FailClosed as exc:
            got = str(exc)
        ok = (got == expected) if isinstance(expected, tuple) else (
            isinstance(got, str) and expected in got)
        if not ok:
            failures.append("LICENCE IDENTITY: declared={!r} text={!r} expected {!r} got {!r}".format(
                declared, lic_text[:40], expected, got))
    for case in CLASS3_SCAN_CASES:
        text, rel, rows, want = case[:4]
        want_snips = case[4] if len(case) > 4 else ()
        consumed = [False] * len(rows)
        hits = scan_waivers(text, rel, rows, consumed)
        if len(hits) != want:
            failures.append("CLASS3 SCAN: {!r} on {} expected {} finding(s) got {}: {}".format(
                text[:60], rel, want, len(hits), [h[1] for h in hits]))
        elif hits and not all(label == CLASS3_LABEL for label, _ in hits):
            failures.append("CLASS3 WRONG CLASS: {!r} got {}".format(text[:60], [h[0] for h in hits]))
        else:
            for want_snip, (_, snip) in zip(want_snips, hits):
                if want_snip not in snip:
                    failures.append(
                        "CLASS3 SCAN OCCURRENCE: {!r} expected a finding carrying {!r}, got {!r}".format(
                            text[:60], want_snip, snip))
    for declared, lic_text, rows, surfaces, expected in CLASS3_EVALUATE_CASES:
        try:
            waivers, identity, reserves = evaluate_class3(
                declared, "LICENSE", lic_text, rows, surfaces)
            got = (len(waivers), identity, reserves)
        except _FailClosed as exc:
            got = str(exc)
        ok = (got == expected) if isinstance(expected, tuple) else (
            isinstance(got, str) and expected in got)
        if not ok:
            failures.append("CLASS3 EVALUATE: declared={!r} text={!r} expected {!r} got {!r}".format(
                declared, lic_text[:40], expected, got))
    failures.extend(_valid_config_failures(_CFG_VALID, _CFG_VALID_PARSED))
    for label, bad, want in _CFG_MALFORMED:
        failures.extend(_malformed_config_failures(label, bad, want))
    # Each leg's parse handler is driven with each of the three classes it catches: TOMLDecodeError (a
    # text that is not TOML), RecursionError (a text nested past the recursion limit), and MemoryError
    # (raised by the stand-in loader). Each must come back as that handler's NAMED failure line. An
    # exception that escapes (a deleted handler, or one narrowed to drop any of the three classes) is
    # caught here and fails by name, never passes.
    for label, text, exc_name, loads in _CFG_UNPARSEABLE:
        for leg, want_line in (
                ("valid", "CONFIG PARSE: valid fixture did not parse as TOML: " + exc_name),
                ("malformed", "CONFIG PARSE: malformed fixture {} did not parse as TOML: {}".format(
                    label, exc_name))):
            try:
                if leg == "valid":
                    got = _valid_config_failures(text, _CFG_VALID_PARSED, loads)
                else:
                    got = _malformed_config_failures(label, text, "", loads)
            except (ValueError, RecursionError, MemoryError) as exc:
                got = ["{} escaped the handler".format(type(exc).__name__)]
            if got != [want_line]:
                failures.append("CONFIG PARSE HANDLER: the {} leg on {} expected {!r} got {!r}".format(
                    leg, label, [want_line], got))
    for findings, claims, identity, reserves, c3err, want_code, want_out, want_err in RENDER_CASES:
        code, out, err = _render(list(findings), claims, identity, reserves, c3err)
        joined_out = "\n".join(out)
        joined_err = "\n".join(err)
        if code != want_code:
            failures.append("RENDER: expected exit {} got {} ({!r})".format(want_code, code, out + err))
        elif want_out is not None and want_out not in joined_out:
            failures.append("RENDER: {!r} missing from stdout {!r}".format(want_out, out))
        elif want_err is not None and want_err not in joined_err:
            failures.append("RENDER: {!r} missing from stderr {!r}".format(want_err, err))
        elif findings and not any(findings[0] in line for line in out):
            failures.append("RENDER: finding line hidden by the class-3 outcome: {!r}".format(out))
    return failures


# The file-layer fixture tree: every required surface present and clean, plus an Apache LICENSE, so
# each case below changes exactly the piece it exercises.
_TREE_BASE = {
    "site/index.html": "<p>hello</p>\n",
    "opf/site/index.html": "<p>hello</p>\n",
    "docs/index.md": "docs\n",
    "opf/spec/index.md": "spec\n",
    "disclosure.toml": "name = \"fixture\"\n",
    "DISCLOSURE.md": "Nothing to disclose.\n",
    "README.md": "A quiet fixture readme.\n",
    "LICENSE": _LIC_APACHE,
}

_README_FINDINGS = ("AIQT is open source under the Apache License 2.0 and nothing more.\n"
                    "AIQT is a registered trademark.\n")


def _write_tree(root, files):
    for rel, text in files.items():
        q = root / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            q.write_bytes(text)
        else:
            q.write_text(text, encoding="utf-8")


def _collect_shape_ok(got):
    """True iff got has the shape _collect documents, (findings, claim count, identity, reserves,
    class3_error): a list of str, an int, and identity (a str) and reserves (a bool) set exactly when
    class3_error (a str) is None. The file-layer cases read these fields, so a None or malformed
    result must fail by name, never skip their assertions."""
    if not (isinstance(got, tuple) and len(got) == 5):
        return False
    findings, claims, identity, reserves, c3err = got
    if not (isinstance(findings, list) and all(isinstance(f, str) for f in findings)):
        return False
    if isinstance(claims, bool) or not isinstance(claims, int):
        return False
    if c3err is None:
        return isinstance(identity, str) and isinstance(reserves, bool)
    return isinstance(c3err, str) and identity is None and reserves is None


def _source_dir_entries():
    """The names in this tool's source directory and in its __pycache__ (where bytecode lands), so
    the self-test can show it wrote nothing beside its sources."""
    src = Path(__file__).resolve().parent
    names = set(os.listdir(src))
    if (src / "__pycache__").is_dir():
        names |= {"__pycache__/" + n for n in os.listdir(src / "__pycache__")}
    return names


def _self_test_files():
    """The file-layer fixture legs: real temporary trees driven through _collect (and child processes
    through _emit and main for stream ordering), so licence-file discovery, configuration loading,
    the lstat-presence, stat, and EACCES fail-closed paths, and the never-hidden guarantee are
    exercised where they live, not only on in-memory lists. EACCES is injected through the _lstat /
    _stat / _read_file_text / _load_toml_file seams (a chmod 000 denies nothing under root). Every
    child runs as -I -B and first reports os.getcwd(), which must be exactly the working directory
    it was given inside the temporary root, and the root is checked to be gone afterwards. Returns
    (failures, case count)."""
    import subprocess
    import tempfile
    global _lstat, _stat, _read_file_text, _load_toml_file
    failures = []
    cases = []

    def run_collect(label, root):
        """_collect(root), or None after a NAMED failure (an escaped exception, or a None or
        malformed result), so no case built on it can pass with zero assertions."""
        cases.append(label)
        try:
            got = _collect(root)
        except (OSError, UnicodeDecodeError) as exc:
            failures.append("FILES {}: {} escaped _collect: {}".format(label, type(exc).__name__, exc))
            return None
        if not _collect_shape_ok(got):
            failures.append("FILES {}: _collect returned a malformed result: {!r}".format(label, got))
            return None
        return got

    cwd_mark = "CHILD CWD "

    def run_child(label, payload, cwd):
        """Run payload in a -I -B child with working directory cwd, which must lie inside the
        temporary root td. The child first prints os.getcwd(), which must resolve to exactly
        cwd.resolve() (a NAMED failure otherwise, so a child run anywhere else, the temporary root
        itself, the parent's own directory, or beside the sources, fails); that line is dropped from
        the returned capture."""
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-c",
             "import os; print({!r} + os.getcwd(), flush=True); {}".format(cwd_mark, payload)],
            cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120, text=True)
        head, _, rest = proc.stdout.partition("\n")
        reported = Path(head[len(cwd_mark):]) if head.startswith(cwd_mark) else None
        want = Path(cwd).resolve()
        if (reported is None or reported.resolve() != want
                or not want.is_relative_to(Path(td).resolve())):
            failures.append("FILES {}: the child must run in {}, inside the temporary root {}, it"
                            " reported {!r}".format(label, want, td, head))
        return subprocess.CompletedProcess(proc.args, proc.returncode, rest)

    def merged_order_failure(label, proc, want_code, first, then):
        """None when the child exited want_code and `first` precedes `then` in its merged capture,
        else the failure line: a child that crashed after printing in order still fails."""
        if proc.returncode != want_code:
            return "FILES {}: the child exited {} (expected {}): {!r}".format(
                label, proc.returncode, want_code, proc.stdout)
        a = proc.stdout.find(first)
        b = proc.stdout.find(then)
        if not 0 <= a < b:
            return "FILES {}: {!r} must precede {!r} in a merged capture, got {!r}".format(
                label, first, then, proc.stdout)
        return None

    def make_tree(td, name, extra):
        root = Path(td) / name
        files = dict(_TREE_BASE)
        files.update(extra)
        _write_tree(root, files)
        return root

    cfg_row = ("licence = \"Apache-2.0\"\n\n[[exception]]\npath = \"docs/index.md\"\n"
               "phrase = \"copyright waiver\"\noccurrence = 1\nreason = \"the 2019 draft note\"\n")
    with tempfile.TemporaryDirectory() as td:
        # 1. a clean tree with no configuration: the Apache identity is inferred from the leading text.
        got = run_collect("CLEAN", make_tree(td, "clean", {}))
        if got is not None and (got[0], got[2], got[3], got[4]) != ([], "Apache-2.0", True, None):
            failures.append("FILES CLEAN: expected a clean Apache pass, got {!r}".format(got))
        # 2. one row, one occurrence, END TO END through real files: two occurrences in one file, the
        # occurrence-1 row excuses the first, and the finding left standing is the SECOND.
        root = make_tree(td, "onerow", {
            "docs/index.md": ("A first copyright waiver line.\nThe standing second copyright waiver"
                              " line.\n"),
            ".aiqt/license-qualification.toml": cfg_row,
        })
        got = run_collect("ONE ROW ONE OCCURRENCE", root)
        if got is not None and not (
                got[4] is None and len(got[0]) == 1 and "second" in got[0][0]
                and CLASS3_LABEL in got[0][0]):
            failures.append("FILES ONE ROW: expected exactly the second occurrence, got {!r}".format(got))
        # 3. the licence READ fails (EACCES) while class-1 and class-2 findings exist: both findings
        # survive into the rendered stdout, the class-3 error maps to stderr, the exit stays 2.
        root = make_tree(td, "lic-read-denied", {"README.md": _README_FINDINGS})
        orig_read = _read_file_text

        def denying_read(q):
            raise PermissionError(13, "injected licence read denial", str(q))
        _read_file_text = denying_read
        try:
            got = run_collect("LICENCE READ DENIED", root)
        finally:
            _read_file_text = orig_read
        if got is not None:
            findings, claims, identity, reserves, c3err = got
            code, out, err = _render(list(findings), claims, identity, reserves, c3err)
            joined_out = "\n".join(out)
            if not (code == 2 and c3err is not None and "cannot evaluate class 3" in c3err
                    and CLASS1_LABEL in joined_out and CLASS2_LABEL in joined_out
                    and all(c3err not in line for line in out)):
                failures.append(
                    "FILES LICENCE READ DENIED: findings must render on stdout with the class-3 error"
                    " on stderr (exit 2), got code={} out={!r} err={!r}".format(code, out, err))
        # 4. EACCES on a licence CANDIDATE lstat is a named cannot-evaluate, never absence.
        orig_lstat = _lstat

        def denying_lstat(path):
            if Path(path).name == "LICENSE":
                raise PermissionError(13, "injected lstat denial", str(path))
            return orig_lstat(path)
        _lstat = denying_lstat
        try:
            got = run_collect("LICENCE LSTAT DENIED", make_tree(td, "lic-lstat", {}))
        finally:
            _lstat = orig_lstat
        if got is not None and not (got[4] is not None and "LICENSE" in got[4]
                                    and "cannot evaluate class 3" in got[4]):
            failures.append(
                "FILES LICENCE LSTAT DENIED: expected a named class-3 error, got {!r}".format(got))
        # 5. EACCES on the CONFIGURATION lstat likewise.

        def denying_cfg_lstat(path):
            if Path(path).name == "license-qualification.toml":
                raise PermissionError(13, "injected lstat denial", str(path))
            return orig_lstat(path)
        _lstat = denying_cfg_lstat
        try:
            got = run_collect("CONFIG LSTAT DENIED", make_tree(td, "cfg-lstat", {}))
        finally:
            _lstat = orig_lstat
        if got is not None and not (got[4] is not None and CONFIG_PATH in got[4]
                                    and "cannot evaluate class 3" in got[4]):
            failures.append(
                "FILES CONFIG LSTAT DENIED: expected a named class-3 error, got {!r}".format(got))
        # 6. EACCES on the configuration READ becomes the class-3 error inside _collect.
        root = make_tree(td, "cfg-read", {".aiqt/license-qualification.toml": cfg_row,
                                          "docs/index.md": "A first copyright waiver line.\n"})
        orig_load = _load_toml_file

        def denying_load(q):
            raise PermissionError(13, "injected config read denial", str(q))
        _load_toml_file = denying_load
        try:
            got = run_collect("CONFIG READ DENIED", root)
        finally:
            _load_toml_file = orig_load
        if got is not None and not (got[4] is not None and "cannot evaluate class 3" in got[4]):
            failures.append("FILES CONFIG READ DENIED: expected a class-3 error, got {!r}".format(got))
        # 7. a licence file that does not decode as UTF-8 is cannot-evaluate, never a crash.
        got = run_collect("LICENCE NOT UTF-8", make_tree(td, "lic-utf8", {"LICENSE": b"\xff\xfe no"}))
        if got is not None and not (got[4] is not None and "could not be read as UTF-8" in got[4]):
            failures.append("FILES LICENCE NOT UTF-8: expected a decode error, got {!r}".format(got))
        # 8. two licence candidates on disk refuse; a tree with none refuses too.
        got = run_collect("TWO LICENCE FILES", make_tree(td, "two-lic", {"COPYING": _LIC_APACHE}))
        if got is not None and not (got[4] is not None and "more than one licence file" in got[4]):
            failures.append("FILES TWO LICENCE FILES: expected a refusal, got {!r}".format(got))
        root = make_tree(td, "no-lic", {})
        (root / "LICENSE").unlink()
        got = run_collect("NO LICENCE FILE", root)
        if got is not None and not (got[4] is not None and "no licence file found" in got[4]):
            failures.append("FILES NO LICENCE FILE: expected a refusal, got {!r}".format(got))
        # 9. a licence entry present by lstat but failing os.stat (a dangling symlink) is reported as
        # that stat error, never as "not a regular file"; a directory is reported as the wrong type.
        root = make_tree(td, "lic-dangling", {})
        (root / "LICENSE").unlink()
        os.symlink("no-such-licence-target", root / "LICENSE")
        got = run_collect("LICENCE DANGLING SYMLINK", root)
        if got is not None and not (got[4] is not None and "LICENSE cannot be inspected" in got[4]
                                    and "not a regular file" not in got[4]):
            failures.append(
                "FILES LICENCE DANGLING SYMLINK: expected the named stat error, got {!r}".format(got))
        root = make_tree(td, "lic-dir", {})
        (root / "LICENSE").unlink()
        (root / "LICENSE").mkdir()
        got = run_collect("LICENCE IS A DIRECTORY", root)
        if got is not None and not (got[4] is not None
                                    and "LICENSE exists but is not a regular file" in got[4]):
            failures.append(
                "FILES LICENCE IS A DIRECTORY: expected the wrong-type error, got {!r}".format(got))
        # 10. EACCES on the CONFIGURATION stat, after a clean lstat, is that error, named.
        orig_stat = _stat

        def denying_cfg_stat(path):
            if Path(path).name == "license-qualification.toml":
                raise PermissionError(13, "injected stat denial", str(path))
            return orig_stat(path)
        _stat = denying_cfg_stat
        try:
            got = run_collect("CONFIG STAT DENIED", make_tree(
                td, "cfg-stat", {".aiqt/license-qualification.toml": "licence = \"Apache-2.0\"\n"}))
        finally:
            _stat = orig_stat
        if got is not None and not (got[4] is not None
                                    and CONFIG_PATH + " cannot be inspected" in got[4]
                                    and "injected stat denial" in got[4]):
            failures.append(
                "FILES CONFIG STAT DENIED: expected the named stat error, got {!r}".format(got))
        # 11. stream ordering: the findings reach a MERGED capture before the class-3 error line (the
        # stdout flush is what holds the order once both streams share one pipe), first through
        # _emit, whose child must exit 0 ...
        child_cwd = Path(td) / "child-cwd"
        child_cwd.mkdir()
        src_dir = str(Path(__file__).resolve().parent)
        stem = Path(__file__).stem
        emit_payload = ("import sys; sys.path.insert(0, {!r}); import importlib;"
                        " m = importlib.import_module({!r});"
                        " m._emit([\"FAIL: 1 license/trademark issue(s)\", \"  docs/a.md: class1 -> x\"],"
                        " [\"error: licence trouble; fail-closed\"])").format(src_dir, stem)
        cases.append("EMIT ORDER")
        bad = merged_order_failure("EMIT ORDER", run_child("EMIT ORDER", emit_payload, child_cwd), 0,
                                   "FAIL: 1 license/trademark issue(s)", "error: licence trouble")
        if bad:
            failures.append(bad)
        # ... while the check refuses a child that printed in that order and then crashed ...
        cases.append("EMIT ORDER CRASH CONTROL")
        crashed = run_child("EMIT ORDER CRASH CONTROL",
                            emit_payload + "; raise RuntimeError('injected crash after output')",
                            child_cwd)
        if merged_order_failure("EMIT ORDER CRASH CONTROL", crashed, 0,
                                "FAIL: 1 license/trademark issue(s)", "error: licence trouble") is None:
            failures.append("FILES EMIT ORDER CRASH CONTROL: the order check accepted a child that"
                            " exited {}: {!r}".format(crashed.returncode, crashed.stdout))
        # ... and then through main itself on a real tree (class-1 and class-2 findings beside a
        # class-3 refusal), which must exit 2. main resolves the repository root from __file__, so
        # the child points the module's __file__ into the fixture tree.
        root = make_tree(td, "main-order", {"README.md": _README_FINDINGS, "COPYING": _LIC_APACHE})
        main_payload = ("import sys; sys.path.insert(0, {!r}); import importlib;"
                        " m = importlib.import_module({!r}); m.__file__ = {!r};"
                        " sys.argv = [m.__file__]; sys.exit(m.main())").format(
                            src_dir, stem, str(root / "tools" / (stem + ".py")))
        cases.append("MAIN ORDER")
        bad = merged_order_failure("MAIN ORDER", run_child("MAIN ORDER", main_payload, child_cwd), 2,
                                   "FAIL: 2 license/trademark issue(s)",
                                   "error: more than one licence file")
        if bad:
            failures.append(bad)
    # 12. the temporary root, with every fixture tree and the child working directory, is gone.
    if os.path.lexists(td):
        failures.append("FILES CLEANUP: the temporary root {} is still present".format(td))
    return failures, len(cases)


def _self_test():
    """Run the fixture corpus: the in-memory legs, then the file-layer legs in a private temporary tree. Exit 0 iff every fixture behaves, else 1."""
    failures = []
    source_entries = _source_dir_entries()
    for line in CLEAN_FIXTURES:
        hits = scan_text(line)
        if hits:
            failures.append("FALSE POSITIVE: {!r} -> {}".format(line, [h[0] for h in hits]))
    for line, expected in FLAGGED_FIXTURES:
        hits = scan_text(line)
        if not hits:
            failures.append("MISS (should flag): {!r}".format(line))
        elif not any(label.startswith(expected) for label, _ in hits):
            failures.append("WRONG CLASS: {!r} expected {} got {}".format(
                line, expected, [h[0] for h in hits]))
    failures.extend(_self_test_class3())
    file_failures, file_cases = _self_test_files()
    failures.extend(file_failures)
    gained = sorted(_source_dir_entries() - source_entries)
    if gained:
        failures.append("SOURCE DIR: the self-test wrote {} beside its sources (every child must run"
                        " as -I -B inside the temporary root)".format(gained))
    if failures:
        print("FAIL: check_license_qualification self-test")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: check_license_qualification self-test ({} clean, {} flagged fixtures; class 3: {} "
          "licence-file, {} identity, {} scan, {} evaluate, {} config, {} render, {} file cases)".format(
              len(CLEAN_FIXTURES), len(FLAGGED_FIXTURES), len(LICENCE_FILE_CASES),
              len(LICENCE_IDENTITY_CASES), len(CLASS3_SCAN_CASES), len(CLASS3_EVALUATE_CASES),
              1 + len(_CFG_MALFORMED) + len(_CFG_UNPARSEABLE), len(RENDER_CASES), file_cases))
    return 0


def _render(findings, claims, identity, reserves, class3_error):
    """Pure result rendering: return (exit code, stdout lines, stderr lines). Findings (classes 1 and
    2, plus class 3 when it evaluated) are ALWAYS rendered, BEFORE any class-3 cannot-evaluate error,
    and a cannot-evaluate maps to exit 2 even when findings exist beside it (both are reported; the
    findings stay visible)."""
    out = []
    err = []
    if findings:
        out.append("FAIL: {} license/trademark issue(s)".format(len(findings)))
        out.extend("  " + finding for finding in sorted(set(findings)))
    if class3_error is not None:
        err.append("error: {}; fail-closed".format(class3_error))
        return 2, out, err
    if findings:
        return 1, out, err
    out.append("PASS: {} whole-project Apache-2.0 claim(s) checked, all adjacent-qualified; no AIQT "
               "registered-mark misstatement; no licence-waiver contradiction (project licence {} "
               "{})".format(claims, identity,
                            "reserves rights" if reserves else "waives rights; class 3 not applicable"))
    return 0, out, err


def _emit(out_lines, err_lines):
    """Print a rendered result: every stdout line, then a stdout FLUSH, then every stderr line. The
    flush is what keeps the findings AHEAD of the class-3 error when both streams are captured into
    one pipe (block-buffered stdout would otherwise sit behind unbuffered stderr), so the documented
    findings-before-error order holds on a terminal and in a merged capture alike."""
    for line in out_lines:
        print(line)
    sys.stdout.flush()
    for line in err_lines:
        print(line, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Fail on an unqualified whole-project Apache-2.0 claim, an AIQT mark called "
                    "registered, or a licence-waiver contradiction.")
    parser.add_argument("--self-test", action="store_true",
                        help="run the fixture corpus and exit (in-memory legs plus file-layer legs in a private temporary tree)")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    root = Path(__file__).resolve().parents[1]
    try:
        findings, claims, identity, reserves, class3_error = _collect(root)
    except _FailClosed as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: license-qualification scan failed closed ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2
    code, out_lines, err_lines = _render(findings, claims, identity, reserves, class3_error)
    _emit(out_lines, err_lines)
    return code


if __name__ == "__main__":
    sys.exit(main())
