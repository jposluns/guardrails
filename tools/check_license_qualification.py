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

CLASS 3, LICENCE-WAIVER CONTRADICTION. A project whose declared licence RESERVES rights must not pair it
with a public-surface phrase waiving those rights. The declared licence is READ from the repository
LICENSE file and mapped through a CLOSED identity table (LICENCE_IDENTITIES: one lowercased,
whitespace-collapsed fingerprint substring per known licence, each row marked rights-reserving or
rights-waiving); an unknown, ambiguous, or unreadable LICENSE is cannot-evaluate (exit 2), NEVER a clean
pass, and nothing about any particular project is hardcoded (the LICENSE of whatever repository is
scanned decides). When the declared licence itself waives rights (CC0-1.0, the Unlicense) class 3 is not
applicable and produces no finding. When it reserves rights, each member of the CLOSED waiver-phrase
list WAIVER_PHRASES (`no rights reserved`, `all rights waived`, `dedicated to the public domain`,
`copyright waiver`, `cc0`) is matched on the same scanned surfaces classes 1 and 2 use,
case-insensitively over whitespace-collapsed text exactly as class 1 is (tokens joined by \\s+ and
\\b-anchored), so a line-wrapped waiver, a capitalized "No Rights Reserved", and the hyphen-adjacent
"CC0-1.0" are caught, while "acc0unt" is not. Two exemptions, both explicit. First, QUOTED THIRD-PARTY
LICENCE TEXT, recognized EXACTLY as follows: on a markdown surface, a maximal run of blockquote lines
(first non-whitespace character ">") or a fenced code block (a line whose first non-whitespace
characters are ``` through the next such line, an unclosed fence running to the end of the file); on an
HTML surface, a literal non-greedy `<blockquote ...>...</blockquote>` element pair (no nesting); on any
other surface (disclosure.toml), nothing; and in every case the region qualifies ONLY when its own text
contains a licence-name token (`license` or `licence`, any case), so a quoted waiver that names no
licence still flags. A match is exempt only when its WHOLE span lies inside one such region. Second, the
gate configuration .aiqt/license-waiver.toml: an explicit [[exception]] list whose rows each carry path,
phrase, and a nonempty reason; an absent file means no exceptions, and a malformed file is
cannot-evaluate (exit 2).

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
exists but is unreadable as text), not a crash and not a skip. For class 3 the project
LICENSE is likewise required: an absent, unreadable, unknown, or ambiguous LICENSE, or a malformed
.aiqt/license-waiver.toml exception configuration, is cannot-evaluate (exit 2), never a clean pass.

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
copyright", "released into the public domain", "free of all rights") is not seen; an HTML-SPLIT waiver
phrase whose tokens are separated by inline markup ("no <b>rights</b> reserved") is not seen, because
the tokens are joined across whitespace only, the same shape as the class-1 HTML-ANCHOR-SPLIT residual;
the quoted-licence recognizer is structural, so a FIRST-PARTY waiver placed inside a blockquote or fence
that also names a licence wrongly passes, a nested HTML blockquote ends its region at the first close
tag, and a genuine third-party licence quoted WITHOUT blockquote or fence markup (an indented or
<pre>-only quotation) wrongly flags and needs an exception entry; and the licence identity table is
closed, so a licence it does not fingerprint (for instance LGPL) is cannot-evaluate (exit 2), not
guessed. Grow the vocabulary, and the licence identity table, when a new class is found.

  gen: python3 tools/check_license_qualification.py             scan the required public-facing surfaces
       python3 tools/check_license_qualification.py --self-test  run the in-memory fixture corpus

Exit 0 clean, 1 on any finding, 2 on a read error (absent/unwalkable/wrong-type required surface) or a
class-3 cannot-evaluate (unknown, ambiguous, or unreadable LICENSE; malformed exception configuration).
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
import re
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

# The CLOSED licence identity table: one row per known licence identity, (identity, fingerprint,
# reserves_rights). The fingerprint is a lowercased, whitespace-collapsed substring of the canonical
# licence text; reserves_rights records whether that licence reserves rights (True) or waives them
# (False). The table is closed by design: a LICENSE matching no row, or more than one row, is
# cannot-evaluate (exit 2), never a clean pass, and nothing about any particular project is hardcoded
# (the LICENSE file of whatever repository is scanned decides). Grow the table when a new licence is
# adopted, as the class-1 vocabulary grows.
LICENCE_IDENTITIES = (
    ("Apache-2.0", "apache license version 2.0, january 2004", True),
    ("MIT", "permission is hereby granted, free of charge, to any person obtaining a copy", True),
    ("BSD-source-redistribution", "redistribution and use in source and binary forms", True),
    ("GPL-3.0", "gnu general public license version 3, 29 june 2007", True),
    ("GPL-2.0", "gnu general public license version 2, june 1991", True),
    ("MPL-2.0", "mozilla public license version 2.0", True),
    ("ISC", "permission to use, copy, modify, and/or distribute this software for any purpose", True),
    ("CC0-1.0", "cc0 1.0 universal", False),
    ("Unlicense", "free and unencumbered software released into the public domain", False),
)

# The CLOSED waiver-phrase list, matched case-insensitively over whitespace-collapsed text like class 1:
# tokens joined by \s+ and \b-anchored, so a line-wrapped or capitalized waiver and the hyphen-adjacent
# "CC0-1.0" are caught while "acc0unt" is not. Closed and documented: a paraphrased waiver is a disclosed
# residual (module docstring), not a matcher ambition.
WAIVER_PHRASES = (
    "no rights reserved",
    "all rights waived",
    "dedicated to the public domain",
    "copyright waiver",
    "cc0",
)


def _waiver_regex(phrase):
    """Compile one waiver phrase: tokens joined by \\s+ and \\b-anchored, case-insensitive, the same
    whitespace-collapsed shape the class-1 claim matcher uses."""
    return re.compile(
        r"\b" + r"\s+".join(re.escape(tok) for tok in phrase.split()) + r"\b", re.IGNORECASE)


WAIVER_RES = tuple((phrase, _waiver_regex(phrase)) for phrase in WAIVER_PHRASES)

CLASS3_LABEL = "class3 (licence-waiver contradiction)"

# The class-3 gate configuration: an explicit exception list, one [[exception]] row per excused
# occurrence, each carrying path (the surface, repo-relative POSIX), phrase (a WAIVER_PHRASES member),
# and a nonempty reason. An absent file means no exceptions (adopters own their exceptions, like
# .aiqt/newtab.toml); a present but malformed file is cannot-evaluate (exit 2), never a clean pass.
EXCEPTIONS_CONFIG = ".aiqt/license-waiver.toml"

# A licence-name token: a quoted region counts as third-party LICENCE text only when it names a licence.
_LICENCE_TOKEN = re.compile(r"\blicen[cs]e\b", re.IGNORECASE)

# An HTML blockquote element, literal and non-greedy, no nesting (a nested blockquote ends the region at
# the first close tag; a disclosed residual).
_HTML_BLOCKQUOTE = re.compile(r"<blockquote\b[^>]*>.*?</blockquote\s*>", re.IGNORECASE | re.DOTALL)


def licence_reserves_rights(license_text):
    """Map a LICENSE text onto the closed identity table; return (identity, reserves_rights). The text is
    lowercased and whitespace-collapsed before the fingerprint substring test. Raises _FailClosed when no
    row matches (unknown licence) or more than one row matches (ambiguous licence): cannot-evaluate, exit
    2, never a clean pass."""
    collapsed = re.sub(r"\s+", " ", license_text).lower()
    matches = [(ident, reserves) for ident, fingerprint, reserves in LICENCE_IDENTITIES
               if fingerprint in collapsed]
    if not matches:
        raise _FailClosed(
            "the project LICENSE matches no known licence identity (closed table); cannot evaluate class 3")
    if len(matches) > 1:
        raise _FailClosed(
            "the project LICENSE matches more than one licence identity ({}); cannot evaluate class 3".format(
                ", ".join(ident for ident, _ in matches)))
    return matches[0]


def _surface_kind(path):
    """The class-3 quoting mechanics for one surface: md (markdown blockquote and fence), html
    (blockquote element), or other (no quoting mechanism, e.g. disclosure.toml)."""
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "md"
    if suffix == ".html":
        return "html"
    return "other"


def _md_fence_regions(text):
    """(start, end) offsets of each markdown fenced code block: from the start of a line whose first
    non-whitespace characters are ``` to the end of the next such line; an unclosed fence runs to the end
    of the text (the CommonMark close-at-document-end behaviour)."""
    regions = []
    offset = 0
    fence_start = None
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            if fence_start is None:
                fence_start = offset
            else:
                regions.append((fence_start, offset + len(line)))
                fence_start = None
        offset += len(line)
    if fence_start is not None:
        regions.append((fence_start, len(text)))
    return regions


def _md_blockquote_regions(text, fences):
    """(start, end) offsets of each MAXIMAL run of blockquote lines (first non-whitespace character >)
    lying outside every fence region."""
    regions = []
    offset = 0
    run_start = None
    for line in text.splitlines(keepends=True):
        in_fence = any(a <= offset < b for a, b in fences)
        quoted = (not in_fence) and line.lstrip().startswith(">")
        if quoted and run_start is None:
            run_start = offset
        elif (not quoted) and run_start is not None:
            regions.append((run_start, offset))
            run_start = None
        offset += len(line)
    if run_start is not None:
        regions.append((run_start, len(text)))
    return regions


def _quoted_licence_regions(text, kind):
    """The spans this gate recognizes as QUOTED THIRD-PARTY LICENCE TEXT, exactly: on an md surface, a
    maximal blockquote run or a fenced code block; on an html surface, a blockquote element; on any other
    surface, nothing. A candidate region qualifies only when its own text contains a licence-name token
    (license or licence, any case): a quoted region that never names a licence is NOT licence text, so a
    merely blockquoted first-party waiver still flags."""
    if kind == "md":
        fences = _md_fence_regions(text)
        candidates = fences + _md_blockquote_regions(text, fences)
    elif kind == "html":
        candidates = [m.span() for m in _HTML_BLOCKQUOTE.finditer(text)]
    else:
        candidates = []
    return [(a, b) for a, b in candidates if _LICENCE_TOKEN.search(text[a:b])]


def scan_waivers(text, kind, rel, exceptions):
    """Return class-3 (label, snippet) findings in one surface, given its kind (md, html, other), its
    repo-relative POSIX path rel, and the parsed exception set of (path, phrase) pairs. Called only when
    the project licence reserves rights. A match is exempt when its whole span lies inside one recognized
    quoted-licence region, or when (rel, phrase) is excused by the exception list."""
    regions = _quoted_licence_regions(text, kind)
    findings = []
    for phrase, rx in WAIVER_RES:
        for m in rx.finditer(text):
            if any(a <= m.start() and m.end() <= b for a, b in regions):
                continue
            if (rel, phrase) in exceptions:
                continue
            findings.append((CLASS3_LABEL, _snippet(text, m.start(), m.end())))
    return findings


def _parse_exceptions(data, source):
    """Validate a parsed exception configuration; return the set of (path, phrase) pairs. Any shape other
    than an optional [[exception]] array of rows carrying exactly path, phrase, reason (phrase a
    WAIVER_PHRASES member, path and reason nonempty strings) raises _FailClosed (exit 2)."""
    if not isinstance(data, dict) or not set(data) <= {"exception"}:
        raise _FailClosed("{}: expected only [[exception]] rows".format(source))
    rows = data.get("exception", [])
    if not isinstance(rows, list):
        raise _FailClosed("{}: exception must be an array of tables".format(source))
    out = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"path", "phrase", "reason"}:
            raise _FailClosed(
                "{}: exception {} must carry exactly path, phrase, reason".format(source, i + 1))
        if not all(isinstance(row[key], str) for key in ("path", "phrase", "reason")):
            raise _FailClosed("{}: exception {} fields must be strings".format(source, i + 1))
        if not row["path"] or not row["reason"].strip():
            raise _FailClosed(
                "{}: exception {} needs a nonempty path and a nonempty reason".format(source, i + 1))
        if row["phrase"] not in WAIVER_PHRASES:
            raise _FailClosed(
                "{}: exception {} phrase {!r} is not in the closed waiver-phrase list".format(
                    source, i + 1, row["phrase"]))
        out.add((row["path"], row["phrase"]))
    return out


def _load_exceptions(root):
    """Read the exception configuration; an absent file means no exceptions, a malformed one raises
    _FailClosed (exit 2)."""
    p = root / EXCEPTIONS_CONFIG
    if not p.exists():
        return set()
    if not p.is_file():
        raise _FailClosed("{} exists but is not a regular file".format(EXCEPTIONS_CONFIG))
    try:
        data = load_toml(p)
    except ValueError as exc:
        raise _FailClosed("{}: {}".format(EXCEPTIONS_CONFIG, exc)) from exc
    return _parse_exceptions(data, EXCEPTIONS_CONFIG)


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
    """Scan every required surface under `root`; return (findings, license_claim_count, licence_identity,
    reserves_rights). Raises _FailClosed on an absent/unwalkable/wrong-type required surface, an absent,
    unreadable, unknown, or ambiguous project LICENSE, or a malformed exception configuration (caller ->
    exit 2); an OSError from a fail-closed walk or read likewise propagates."""
    lic = root / "LICENSE"
    if not lic.is_file():
        raise _FailClosed("LICENSE is required to evaluate class 3 but is absent or not a file")
    try:
        identity, reserves = licence_reserves_rights(lic.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise _FailClosed(
            "LICENSE could not be read as UTF-8 ({}); cannot evaluate class 3".format(exc)) from exc
    exceptions = _load_exceptions(root)
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
        if reserves:
            for label, snip in scan_waivers(text, _surface_kind(f), rel.as_posix(), exceptions):
                findings.append("{}: {} -> {}".format(rel, label, snip))
    return findings, claims, identity, reserves


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

# Class-3 fixture licences: in-memory LICENSE texts carrying one identity fingerprint each (abbreviated;
# the mapper needs only the fingerprint), plus an unknown and an ambiguous text for the fail-closed legs
# (each maps to exit 2 through _FailClosed in _collect, never to a clean pass).
_LIC_APACHE = "Apache License\n   Version 2.0, January 2004\n   TERMS AND CONDITIONS FOR USE"
_LIC_MIT = ("MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a "
            "copy of this software.")
_LIC_CC0 = "Creative Commons Legal Code\n\nCC0 1.0 Universal\n\nStatement of Purpose"
_LIC_UNKNOWN = "You may use this software on alternate Tuesdays only."
_LIC_AMBIGUOUS = _LIC_APACHE + "\n" + _LIC_CC0
# (licence text, expected (identity, reserves_rights) pair, or None where the mapper must fail closed).
LICENCE_IDENTITY_CASES = [
    (_LIC_APACHE, ("Apache-2.0", True)),
    (_LIC_MIT, ("MIT", True)),
    (_LIC_CC0, ("CC0-1.0", False)),
    (_LIC_UNKNOWN, None),
    (_LIC_AMBIGUOUS, None),
]

# Class-3 surface fixtures: (text, kind, rel, exceptions). CLEAN cases MUST NOT flag even under a
# rights-reserving licence; FLAGGED cases MUST flag. The flagged set covers each public surface type the
# gate scans (a site HTML page, a docs Markdown page, and the named disclosure.toml), plus the
# line-wrapped and capitalized variants.
_NO_EXC = frozenset()
CLASS3_CLEAN_FIXTURES = [
    # the same phrase inside a QUOTED third-party licence text passes: a markdown blockquote run ...
    ("Quoting the upstream licence text verbatim:\n\n"
     "> CC0 1.0 Universal license: dedicated to the public domain,\n"
     "> with no rights reserved and all rights waived.\n",
     "md", "docs/third-party.md", _NO_EXC),
    # ... a markdown fenced code block ...
    ("The vendored licence file reads:\n\n```\nCC0 1.0 Universal license text: no rights reserved.\n```\n",
     "md", "docs/vendored.md", _NO_EXC),
    # ... and an HTML blockquote element.
    ("<p>The upstream licence:</p><blockquote>CC0 1.0 Universal license: no rights reserved.</blockquote>",
     "html", "site/about.html", _NO_EXC),
    # an exception-list entry passes (path and phrase both anchored).
    ("Historical note: the 2019 draft shipped with a copyright waiver.",
     "md", "docs/history.md", frozenset({("docs/history.md", "copyright waiver")})),
    # a word-boundary near miss is not a waiver (acc0unt does not contain the token cc0).
    ("The acc0unt ledger reserves all rights.", "md", "docs/accounts.md", _NO_EXC),
    # the rights-RESERVING formula is not a waiver.
    ("All rights reserved.", "md", "docs/reserved.md", _NO_EXC),
]
CLASS3_FLAGGED_FIXTURES = [
    # a waiver beside a rights-reserving licence is a finding on each public surface type.
    ("This project is offered with no rights reserved.", "html", "site/index.html", _NO_EXC),
    ("Everything here is dedicated to the public domain.", "md", "docs/terms.md", _NO_EXC),
    ("summary = \"All rights waived.\"", "other", "disclosure.toml", _NO_EXC),
    # a line-wrapped and a capitalized waiver are caught (whitespace-collapsed, case-insensitive).
    ("The work ships with no rights\n    reserved going forward.", "md", "docs/wrap.md", _NO_EXC),
    ("No Rights Reserved.", "md", "docs/caps.md", _NO_EXC),
    # the bare CC0 identifier, including the hyphen-adjacent form, contradicts a reserving licence.
    ("This repository is CC0-1.0.", "md", "docs/cc0.md", _NO_EXC),
    # a blockquote that names no licence is NOT recognized as quoted licence text.
    ("> No rights reserved.\n", "md", "docs/bare-quote.md", _NO_EXC),
    # a waiver OUTSIDE the quoted region flags even when a licence-named quote sits beside it.
    ("<blockquote>The MIT license permits reuse.</blockquote><p>no rights reserved</p>",
     "html", "site/split.html", _NO_EXC),
    # quoting mechanics do not apply on the toml surface (no markdown or HTML structure there).
    ("note = \"> CC0 license: no rights reserved.\"", "other", "disclosure.toml", _NO_EXC),
    # an exception entry for a DIFFERENT path does not excuse the occurrence.
    ("Historical note: the 2019 draft shipped with a copyright waiver.",
     "md", "docs/other.md", frozenset({("docs/history.md", "copyright waiver")})),
]

# Exception-configuration parsing fixtures: the valid shape parses to its (path, phrase) set; each
# malformed shape must fail closed (exit 2 through _FailClosed), never parse as empty.
_EXC_VALID = ("[[exception]]\npath = \"docs/history.md\"\nphrase = \"copyright waiver\"\n"
              "reason = \"Describes the withdrawn 2019 draft, not this project.\"\n")
_EXC_MALFORMED = (
    ("missing reason", "[[exception]]\npath = \"docs/x.md\"\nphrase = \"cc0\"\n"),
    ("blank reason", "[[exception]]\npath = \"docs/x.md\"\nphrase = \"cc0\"\nreason = \" \"\n"),
    ("empty path", "[[exception]]\npath = \"\"\nphrase = \"cc0\"\nreason = \"r\"\n"),
    ("unknown phrase", "[[exception]]\npath = \"docs/x.md\"\nphrase = \"public domain\"\nreason = \"r\"\n"),
    ("extra key", "[[exception]]\npath = \"docs/x.md\"\nphrase = \"cc0\"\nreason = \"r\"\nextra = 1\n"),
    ("unknown top-level key", "waivers = []\n"),
    ("wrong exception type", "exception = 3\n"),
)


def _self_test_class3():
    """The class-3 fixture legs; return the failure strings (the caller aggregates and reports)."""
    import tomllib
    failures = []
    for lic_text, expected in LICENCE_IDENTITY_CASES:
        try:
            got = licence_reserves_rights(lic_text)
        except _FailClosed:
            got = None
        if got != expected:
            failures.append("LICENCE IDENTITY: {!r} expected {!r} got {!r}".format(
                lic_text[:40], expected, got))
    for text, kind, rel, exceptions in CLASS3_CLEAN_FIXTURES:
        hits = scan_waivers(text, kind, rel, exceptions)
        if hits:
            failures.append("CLASS3 FALSE POSITIVE: {!r} -> {}".format(text, [h[1] for h in hits]))
    for text, kind, rel, exceptions in CLASS3_FLAGGED_FIXTURES:
        hits = scan_waivers(text, kind, rel, exceptions)
        if not hits:
            failures.append("CLASS3 MISS (should flag): {!r}".format(text))
        elif not all(label == CLASS3_LABEL for label, _ in hits):
            failures.append("CLASS3 WRONG CLASS: {!r} got {}".format(text, [h[0] for h in hits]))
    try:
        parsed = _parse_exceptions(tomllib.loads(_EXC_VALID), "fixture")
        if parsed != {("docs/history.md", "copyright waiver")}:
            failures.append("EXCEPTION PARSE: valid fixture parsed to {!r}".format(sorted(parsed)))
    except _FailClosed as exc:
        failures.append("EXCEPTION PARSE: valid fixture refused: {}".format(exc))
    for label, bad in _EXC_MALFORMED:
        try:
            _parse_exceptions(tomllib.loads(bad), "fixture")
            failures.append("EXCEPTION PARSE: malformed fixture accepted ({})".format(label))
        except _FailClosed:
            pass
    return failures


def _self_test():
    """Run the in-memory fixtures; touch no file. Exit 0 iff every fixture behaves, else 1."""
    failures = []
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
    if failures:
        print("FAIL: check_license_qualification self-test")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: check_license_qualification self-test ({} clean, {} flagged fixtures; class 3: {} "
          "licence-identity, {} clean, {} flagged, {} exception-config cases)".format(
              len(CLEAN_FIXTURES), len(FLAGGED_FIXTURES), len(LICENCE_IDENTITY_CASES),
              len(CLASS3_CLEAN_FIXTURES), len(CLASS3_FLAGGED_FIXTURES), 1 + len(_EXC_MALFORMED)))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Fail on an unqualified whole-project Apache-2.0 claim, an AIQT mark called registered, or a licence-waiver contradiction.")
    parser.add_argument("--self-test", action="store_true",
                        help="run the in-memory fixture corpus and exit (touches no file)")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    root = Path(__file__).resolve().parents[1]
    try:
        findings, claims, identity, reserves = _collect(root)
    except _FailClosed as exc:
        print("error: {}; fail-closed".format(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print("error: license-qualification scan failed closed ({}); fail-closed".format(exc),
              file=sys.stderr)
        return 2
    if findings:
        print("FAIL: {} license/trademark issue(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: {} whole-project Apache-2.0 claim(s) checked, all adjacent-qualified; no AIQT "
          "registered-mark misstatement; no licence-waiver contradiction (project licence {} {})".format(
              claims, identity, "reserves rights" if reserves else "waives rights; class 3 not applicable"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
