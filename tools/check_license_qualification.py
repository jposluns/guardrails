#!/usr/bin/env python3
"""Fail on an unqualified whole-project Apache-2.0 claim, or an AIQT mark called registered.

Two norms, on the public-facing surfaces, each a narrow LITERAL matcher (not a paraphrase detector):

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
exists but is unreadable as text), not a crash and not a skip.

BEST-EFFORT, NOT COMPLETE (residuals). Both classes are calibrated LITERAL matchers, not paraphrase
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
Grow the vocabulary when a new class is found.

  gen: python3 tools/check_license_qualification.py             scan the required public-facing surfaces
       python3 tools/check_license_qualification.py --self-test  run the in-memory fixture corpus

Exit 0 clean, 1 on any finding, 2 on a read error (absent/unwalkable/wrong-type required surface).
"""
import argparse
import re
import sys
from pathlib import Path

# Import the sibling module WITHOUT placing this script's own directory AHEAD of the stdlib on sys.path.
# Under `python3 -I` a sys.path insertion at index 0 would let a sibling os.py or shutil.py shadow a
# stdlib import and silently neuter this gate. Appending keeps stdlib precedence (a stdlib import still
# resolves from the stdlib first) while still resolving our own sibling module from this directory; _walk
# is tools-only, so append still finds it. This matches the house pattern (audit_reference.py,
# check_internal_names.py).
sys.path.append(str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

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
    """Scan every required surface under `root`; return (findings, license_claim_count). Raises _FailClosed
    on an absent/unwalkable/wrong-type required surface (caller -> exit 2); an OSError from a fail-closed
    walk or read likewise propagates."""
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
    return findings, claims


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
    if failures:
        print("FAIL: check_license_qualification self-test")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: check_license_qualification self-test ({} clean, {} flagged fixtures)".format(
        len(CLEAN_FIXTURES), len(FLAGGED_FIXTURES)))
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Fail on an unqualified whole-project Apache-2.0 claim, or an AIQT mark called registered.")
    parser.add_argument("--self-test", action="store_true",
                        help="run the in-memory fixture corpus and exit (touches no file)")
    args = parser.parse_args()
    if args.self_test:
        return _self_test()
    root = Path(__file__).resolve().parents[1]
    try:
        findings, claims = _collect(root)
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
    print("PASS: {} whole-project Apache-2.0 claim(s) checked, all adjacent-qualified; "
          "no AIQT registered-mark misstatement".format(claims))
    return 0


if __name__ == "__main__":
    sys.exit(main())
