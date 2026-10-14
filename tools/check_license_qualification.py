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
public-surface phrase waiving those rights. The licence identity is DECLARED, never inferred from
incidental text: the gate configuration .aiqt/license-qualification.toml carries `licence = "<identity>"`,
an identity from the CLOSED table LICENCE_IDENTITIES, VERIFIED against the licence file. Exactly one
licence file (LICENSE, LICENSE.md, LICENSE.txt, or COPYING) must be present; it must contain the declared
identity's CANONICAL OPENING TEMPLATE (the licence text's own opening, with controlled whitespace and
punctuation variation: \\s+ between tokens, each canonical comma point tolerating a comma, a dash, or a
line break, CRLF included) anchored at the FIRST NON-WHITESPACE COLUMN of a line, and it must not contain
any OTHER identity's opening. An incidental mid-sentence mention ("This is not CC0 1.0 Universal.") is
not a line-anchored opening and never decides, or un-decides, the identity. When the declaration is
absent the identity is INFERRED only when exactly one identity's opening matches and no other identity's
opening appears anywhere; otherwise cannot-evaluate (exit 2) with a message naming the `licence` key to
set. An unknown, ambiguous, duplicated, or unreadable licence file is cannot-evaluate (exit 2), NEVER a
clean pass, and nothing about any particular project is hardcoded (the licence file and configuration of
whatever repository is scanned decide). When the declared licence itself waives rights (CC0-1.0, the
Unlicense) class 3 is not applicable and produces no finding. When it reserves rights, each member of the
CLOSED waiver-phrase list WAIVER_PHRASES (`no rights reserved`, `all rights waived`, `dedicated to the
public domain`, `copyright waiver`, `cc0`) is matched on the same scanned surfaces classes 1 and 2 use,
case-insensitively, tokens joined by [\\s>]+ and \\b-anchored, so a line-wrapped waiver, a capitalized "No
Rights Reserved", the hyphen-adjacent "CC0-1.0", and a waiver wrapped across markdown blockquote
continuation lines ("> no rights" then "> reserved") are all caught, while "acc0unt" and the three-digit
CSS colour "#cc0" (a cc0 immediately after "#") are not. There is NO quoted-text exemption: a waiver
phrase inside a markdown blockquote, a fenced code block, or an HTML blockquote element is a finding like
any other, whether or not the quote names a licence (a genuinely quoted third-party licence text is
excused only by an explicit exception row). The ONLY exemption is the [[exception]] list in
.aiqt/license-qualification.toml: each row BINDS EXACTLY ONE occurrence through path (a repo-relative
scanned-surface path), phrase (a WAIVER_PHRASES member), a nonempty reason, and exactly one binder,
`line` (the exact text of the line the occurrence starts on, trailing carriage returns excluded) or
`occurrence` (the 1-based index of that phrase's match within that file, counting every match in position
order); a second occurrence beside an excused one still flags, and a STALE row (matching no present
occurrence) is cannot-evaluate (exit 2). An absent configuration file means no declaration and no
exceptions; a present but malformed one is cannot-evaluate (exit 2), never a clean pass.

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
required (exactly one of LICENSE, LICENSE.md, LICENSE.txt, COPYING): an absent, duplicated, unreadable,
unknown, or ambiguous licence file, a declaration the file does not verify, a malformed
.aiqt/license-qualification.toml, or a stale exception row is cannot-evaluate (exit 2), never a clean
pass. A class-3 cannot-evaluate NEVER hides class-1 or class-2 findings: those classes always run and
their findings are printed before the class-3 error line (the exit stays 2).

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
U+00A0 character IS whitespace and is caught); a cc0 immediately after "#" is read as the three-digit
CSS colour and never matched, so a genuine waiver spelled "#cc0" is not seen either; a quoted
third-party licence text on a scanned surface now FLAGS and needs an exception row binding each exact
occurrence, so an unmaintained quotation surfaces as a finding, never a silent exemption; and the
licence identity table is closed, so a licence it does not know (for instance LGPL) is cannot-evaluate
(exit 2), not guessed. Grow the vocabulary, and the licence identity table, when a new class is found.

  gen: python3 tools/check_license_qualification.py             scan the required public-facing surfaces
       python3 tools/check_license_qualification.py --self-test  run the in-memory fixture corpus

Exit 0 clean, 1 on any finding, 2 on a read error (absent/unwalkable/wrong-type required surface) or a
class-3 cannot-evaluate (an absent, duplicated, unknown, ambiguous, or unreadable licence file; a
declaration the licence file does not verify; a malformed configuration; or a stale exception row).
Class-1 and class-2 findings are always printed before a class-3 cannot-evaluate error.
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

# The CLOSED licence identity table: one row per known licence identity, (identity, opening_template,
# reserves_rights). The opening template is that licence's CANONICAL OPENING text, compiled with
# controlled whitespace and punctuation variation (\s+ between tokens; each canonical comma point
# tolerates a comma, a dash, or a line break, so the Debian texts, a CRLF checkout, the one-line
# "Apache License, Version 2.0, January 2004" header, and the "Version 2.0 - January 2004" variant all
# match) and ANCHORED at the first non-whitespace column of a line, the position a licence text's own
# opening occupies. An incidental mid-sentence mention ("This is not CC0 1.0 Universal.") is not a
# line-anchored opening and never decides, or un-decides, the identity. The table is closed by design:
# the declaration is validated against it, an unmatched or multiply-matched licence file is
# cannot-evaluate (exit 2), never a clean pass, and nothing about any particular project is hardcoded
# (the licence file of whatever repository is scanned decides). Grow the table when a new licence is
# adopted, as the class-1 vocabulary grows.


def _opening(body):
    """Compile one canonical-opening template: case-insensitive and anchored at the first
    non-whitespace column of a line (the multiline ^ plus horizontal whitespace, never mid-sentence)."""
    return re.compile(r"(?m)^[^\S\n]*" + body, re.IGNORECASE)


LICENCE_IDENTITIES = (
    ("Apache-2.0", _opening(r"apache\s+license[\s,:]+version\s+2\.0[\s,-]+january\s+2004"), True),
    ("MIT", _opening(r"permission\s+is\s+hereby\s+granted,?\s+free\s+of\s+charge,?"
                     r"\s+to\s+any\s+person\s+obtaining\s+a\s+copy"), True),
    ("BSD-source-redistribution", _opening(r"redistribution\s+and\s+use\s+in\s+source\s+and\s+binary\s+forms"), True),
    ("GPL-3.0", _opening(r"gnu\s+general\s+public\s+license[\s,]+version\s+3[\s,-]+29\s+june\s+2007"), True),
    ("GPL-2.0", _opening(r"gnu\s+general\s+public\s+license[\s,]+version\s+2[\s,-]+june\s+1991"), True),
    ("MPL-2.0", _opening(r"mozilla\s+public\s+license[\s,]+version\s+2\.0"), True),
    ("ISC", _opening(r"permission\s+to\s+use,?\s+copy,?\s+modify,?\s+and(?:/or)?"
                     r"\s+distribute\s+this\s+software\s+for\s+any\s+purpose"), True),
    ("CC0-1.0", _opening(r"cc0\s+1\.0\s+universal"), False),
    ("Unlicense", _opening(r"this\s+is\s+free\s+and\s+unencumbered\s+software"
                           r"\s+released\s+into\s+the\s+public\s+domain"), False),
)
LICENCE_IDENTITY_NAMES = tuple(ident for ident, _, _ in LICENCE_IDENTITIES)

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
# ("acc0unt" is not cc0); a phrase immediately after "#" is not matched, so the three-digit CSS colour
# "#cc0" is not a waiver. Closed and documented: a paraphrased or markup-split waiver is a disclosed
# residual (module docstring), not a matcher ambition.
WAIVER_PHRASES = (
    "no rights reserved",
    "all rights waived",
    "dedicated to the public domain",
    "copyright waiver",
    "cc0",
)


def _waiver_regex(phrase):
    """Compile one waiver phrase: tokens joined by [\\s>]+ and \\b-anchored, case-insensitive, never
    immediately after "#" (the CSS colour form)."""
    body = r"[\s>]+".join(re.escape(tok) for tok in phrase.split())
    return re.compile(r"(?<!#)\b" + body + r"\b", re.IGNORECASE)


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
    `licence_name` and `licence_text` are the chosen licence file and its text. DECLARED: the
    declaration decides and is VERIFIED, so the file must contain the declared identity's canonical
    opening and no OTHER identity's opening. INFERRED (no declaration): accepted only when exactly one
    identity's opening matches and no other identity's opening appears anywhere. Every other outcome
    raises _FailClosed (exit 2) with a message naming the `licence` key: cannot-evaluate, never a clean
    pass, and never decided by an incidental mention (the templates are line-anchored openings)."""
    matched = [(ident, reserves) for ident, template, reserves in LICENCE_IDENTITIES
               if template.search(licence_text)]
    if declared is not None:
        if all(ident != declared for ident, _ in matched):
            raise _FailClosed(
                "{} declares {} = \"{}\" but {} does not contain that licence's canonical opening; fix "
                "the declaration or the licence file".format(
                    CONFIG_PATH, CONFIG_LICENCE_KEY, declared, licence_name))
        extra = [ident for ident, _ in matched if ident != declared]
        if extra:
            raise _FailClosed(
                "{} declares {} = \"{}\" but {} also contains the canonical opening of {}; a licence "
                "file must carry exactly one identity's opening".format(
                    CONFIG_PATH, CONFIG_LICENCE_KEY, declared, licence_name, ", ".join(extra)))
        return next((ident, reserves) for ident, reserves in matched if ident == declared)
    if len(matched) == 1:
        return matched[0]
    if not matched:
        raise _FailClosed(
            "{} matches no known licence identity (closed table); set {} = \"<identity>\" in {} to one "
            "of {} and keep {} on that licence's canonical text, or grow the table".format(
                licence_name, CONFIG_LICENCE_KEY, CONFIG_PATH,
                ", ".join(LICENCE_IDENTITY_NAMES), licence_name))
    raise _FailClosed(
        "{} contains the canonical opening of more than one licence identity ({}); reduce it to one "
        "licence's text, then set {} in {} to that identity".format(
            licence_name, ", ".join(ident for ident, _ in matched), CONFIG_LICENCE_KEY, CONFIG_PATH))

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
    is rel, given the parsed exception rows and the caller-owned parallel `consumed` flag list. Called
    only when the project licence reserves rights. Occurrences of one phrase are numbered 1-based in
    position order within this file; an UNCONSUMED row excuses one occurrence when its path and phrase
    match and its binder does (occurrence equals the index, or line equals the exact text of the line
    the occurrence starts on). A row is consumed by the first occurrence it excuses, so a SECOND
    occurrence beside an excused one still flags; the caller reports rows left unconsumed as stale."""
    findings = []
    for phrase, rx in WAIVER_RES:
        for index, m in enumerate(rx.finditer(text), start=1):
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
    unresolved identity or a stale exception row (cannot-evaluate, exit 2)."""
    identity, reserves = resolve_licence_identity(declared, licence_name, licence_text)
    if not reserves:
        # The project licence itself waives rights: class 3 is not applicable, nothing is scanned, and
        # no exception row can bind.
        return [], identity, reserves
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
    return findings, identity, reserves

def _valid_surface_path(path):
    """True iff path is a clean repo-relative POSIX path on a surface this gate scans: a NAMED_FILES
    member, an *.html under a SITE_HTML_DIRS root, or an *.md under a DOCS_MD_DIRS root."""
    if not path or path != path.strip() or "\\" in path or path.startswith("/"):
        return False
    if any(seg in ("", ".", "..") for seg in path.split("/")):
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

def _load_config(root):
    """Read the gate configuration; an absent file means no declaration and no exceptions, a malformed
    one raises _FailClosed (exit 2). Returns (declared, rows)."""
    p = root / CONFIG_PATH
    if not p.exists():
        return None, ()
    if not p.is_file():
        raise _FailClosed("{} exists but is not a regular file".format(CONFIG_PATH))
    try:
        data = load_toml(p)
    except ValueError as exc:
        raise _FailClosed("{}: {}".format(CONFIG_PATH, exc)) from exc
    return _parse_config(data, CONFIG_PATH)


def _read_licence(root):
    """Pick and read the licence file. Raises _FailClosed (exit 2) when not exactly one canonical name
    is present, the chosen one is not a regular file, or it cannot be read as UTF-8."""
    name = _pick_licence_file([n for n in LICENCE_FILE_NAMES if (root / n).exists()])
    p = root / name
    if not p.is_file():
        raise _FailClosed("{} exists but is not a regular file; cannot evaluate class 3".format(name))
    try:
        return name, p.read_text(encoding="utf-8")
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
    exits 2. Raises _FailClosed only for an absent/unwalkable/wrong-type required surface (caller ->
    exit 2); an OSError from a fail-closed walk or read likewise propagates."""
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
# LGPL, empty). The resolver needs only the opening region, so the texts are abbreviated below it.
_LIC_APACHE = ("                                 Apache License\n"
               "                           Version 2.0, January 2004\n"
               "                        http://www.apache.org/licenses/\n\n"
               "   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n")
_LIC_APACHE_CRLF = _LIC_APACHE.replace("\n", "\r\n")
_LIC_APACHE_ONELINE = "Apache License, Version 2.0, January 2004\nTERMS AND CONDITIONS\n"
_LIC_APACHE_DASH = _LIC_APACHE.replace("Version 2.0, January 2004", "Version 2.0 - January 2004")
# An incidental MID-SENTENCE mention of another identity: never line-anchored, never decisive.
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
_LIC_CC0 = ("Creative Commons Legal Code\n\nCC0 1.0 Universal\n\n"
            "    CREATIVE COMMONS CORPORATION IS NOT A LAW FIRM\n")
_LIC_UNLICENSE = ("This is free and unencumbered software released into the public domain.\n\n"
                  "Anyone is free to copy, modify, publish, use, compile, sell, or\n"
                  "distribute this software.\n")
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

# (declared identity or None, licence text, expected (identity, reserves_rights) pair, or the specific
# _FailClosed diagnostic substring the resolver must raise). Expected OUTCOMES, not bare refusals: the
# self-test asserts the diagnostic too, so a refusal for the wrong reason fails the fixture.
LICENCE_IDENTITY_CASES = [
    (None, _LIC_APACHE, ("Apache-2.0", True)),
    (None, _LIC_APACHE_CRLF, ("Apache-2.0", True)),
    (None, _LIC_APACHE_ONELINE, ("Apache-2.0", True)),
    (None, _LIC_APACHE_DASH, ("Apache-2.0", True)),
    (None, _LIC_APACHE_MENTION, ("Apache-2.0", True)),
    (None, _LIC_MIT, ("MIT", True)),
    (None, _LIC_GPL2, ("GPL-2.0", True)),
    (None, _LIC_GPL3, ("GPL-3.0", True)),
    (None, _LIC_MPL, ("MPL-2.0", True)),
    (None, _LIC_BSD, ("BSD-source-redistribution", True)),
    (None, _LIC_ISC, ("ISC", True)),
    (None, _LIC_CC0, ("CC0-1.0", False)),
    (None, _LIC_UNLICENSE, ("Unlicense", False)),
    (None, _LIC_LGPL3, "matches no known licence identity"),
    (None, _LIC_SHORT_NOTICE, "matches no known licence identity"),
    (None, _LIC_UNKNOWN, "matches no known licence identity"),
    (None, "", "matches no known licence identity"),
    (None, _LIC_APACHE_PLUS_MIT, "more than one licence identity"),
    (None, _LIC_DUAL, "more than one licence identity"),
    ("Apache-2.0", _LIC_APACHE, ("Apache-2.0", True)),
    ("Apache-2.0", _LIC_APACHE_MENTION, ("Apache-2.0", True)),
    ("Apache-2.0", _LIC_MIT, "does not contain that licence"),
    ("Apache-2.0", _LIC_APACHE_PLUS_MIT, "also contains the canonical opening"),
    ("MIT", _LIC_APACHE_PLUS_MIT, "also contains the canonical opening"),
    ("CC0-1.0", _LIC_CC0, ("CC0-1.0", False)),
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

# Class-3 surface-scan fixtures: (text, repo-relative path, exception rows, expected finding count).
# There is NO quoted-text exemption, so the quoted first-party shapes here MUST flag; the exception
# rows bind one occurrence each.
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
    # an occurrence row for the second occurrence excuses the second, not the first.
    ("A first copyright waiver. The excused second copyright waiver.",
     "docs/second.md", (("docs/second.md", "copyright waiver", "occurrence", 2),), 1),
    # a row for a DIFFERENT path does not excuse the occurrence.
    ("Historical note: the 2019 draft shipped with a copyright waiver.",
     "docs/other.md", (_ROW_HIST_OCC,), 1),
    # a row whose line text no longer matches does not excuse the occurrence.
    ("A moved copyright waiver sentence.", "docs/history.md", (_ROW_HIST_LINE,), 1),
]

# End-to-end class-3 evaluation fixtures over in-memory trees: (declared, licence text, exception rows,
# surfaces, expected). expected is (finding count, identity, reserves_rights) or a _FailClosed
# diagnostic substring. These drive evaluate_class3, the same function _collect calls, so the reserves
# branch that turns class 3 on, exception consumption, and stale-row reporting are all exercised.
_SURF_WAIVER = (("site/index.html", "This project is offered with no rights reserved."),)
_SURF_HIST = (("docs/history.md", "Historical note: the 2019 draft shipped with a copyright waiver."),)
CLASS3_EVALUATE_CASES = [
    # a rights-reserving licence turns class 3 ON: the waiver is a finding.
    (None, _LIC_APACHE, (), _SURF_WAIVER, (1, "Apache-2.0", True)),
    # a rights-waiving licence turns class 3 OFF: the same surface, no finding.
    (None, _LIC_CC0, (), _SURF_WAIVER, (0, "CC0-1.0", False)),
    # an unknown licence is cannot-evaluate, never a pass.
    (None, _LIC_UNKNOWN, (), _SURF_WAIVER, "matches no known licence identity"),
    # a bound exception row excuses its one occurrence end to end.
    (None, _LIC_APACHE, (_ROW_HIST_OCC,), _SURF_HIST, (0, "Apache-2.0", True)),
    # a row matching no occurrence anywhere is STALE: cannot-evaluate.
    (None, _LIC_APACHE, (_ROW_HIST_OCC,), _SURF_WAIVER, "stale exception row"),
    # under a rights-waiving licence rows cannot bind, and that is not stale (class 3 is off).
    (None, _LIC_CC0, (_ROW_HIST_OCC,), _SURF_HIST, (0, "CC0-1.0", False)),
    # the declaration is verified end to end.
    ("Apache-2.0", _LIC_MIT, (), _SURF_WAIVER, "does not contain that licence"),
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

def _self_test_class3():
    """The class-3 fixture legs; return the failure strings (the caller aggregates and reports)."""
    import tomllib
    failures = []
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
    for text, rel, rows, want in CLASS3_SCAN_CASES:
        consumed = [False] * len(rows)
        hits = scan_waivers(text, rel, rows, consumed)
        if len(hits) != want:
            failures.append("CLASS3 SCAN: {!r} on {} expected {} finding(s) got {}: {}".format(
                text[:60], rel, want, len(hits), [h[1] for h in hits]))
        elif hits and not all(label == CLASS3_LABEL for label, _ in hits):
            failures.append("CLASS3 WRONG CLASS: {!r} got {}".format(text[:60], [h[0] for h in hits]))
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
    try:
        parsed = _parse_config(tomllib.loads(_CFG_VALID), "fixture")
        if parsed != _CFG_VALID_PARSED:
            failures.append("CONFIG PARSE: valid fixture parsed to {!r}".format((parsed,)))
    except _FailClosed as exc:
        failures.append("CONFIG PARSE: valid fixture refused: {}".format(exc))
    for label, bad, want in _CFG_MALFORMED:
        try:
            _parse_config(tomllib.loads(bad), "fixture")
            failures.append("CONFIG PARSE: malformed fixture accepted ({})".format(label))
        except _FailClosed as exc:
            if want not in str(exc):
                failures.append(
                    "CONFIG PARSE: {} refused with the wrong diagnostic ({!r} not in {!r})".format(
                        label, want, str(exc)))
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
          "licence-file, {} identity, {} scan, {} evaluate, {} config, {} render cases)".format(
              len(CLEAN_FIXTURES), len(FLAGGED_FIXTURES), len(LICENCE_FILE_CASES),
              len(LICENCE_IDENTITY_CASES), len(CLASS3_SCAN_CASES), len(CLASS3_EVALUATE_CASES),
              1 + len(_CFG_MALFORMED), len(RENDER_CASES)))
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


def main():
    parser = argparse.ArgumentParser(
        description="Fail on an unqualified whole-project Apache-2.0 claim, an AIQT mark called "
                    "registered, or a licence-waiver contradiction.")
    parser.add_argument("--self-test", action="store_true",
                        help="run the in-memory fixture corpus and exit (touches no file)")
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
    for line in out_lines:
        print(line)
    for line in err_lines:
        print(line, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
