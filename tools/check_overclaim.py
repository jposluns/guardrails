#!/usr/bin/env python3
"""Site-wide overclaim lint for site/*.html.

AIQT's public copy is deliberately guidance-not-guarantee: it says what your assistant is asked and
required to do, never what AIQT itself guarantees the model will do. This gate catches a regression of
the guarantee-flavoured class the F-59/F-67 pass softened, so a reintroduced overclaim fails CI rather
than shipping. It scans the VISIBLE TEXT of each page (tags, <script>, and <style> stripped; entities
unescaped; whitespace collapsed), so a phrase that wraps across source lines is still one string and an
overclaim hidden in an attribute is not falsely flagged. Text from two SEPARATE block elements is kept
apart by a space (see BLOCK_TAGS): "<p>guarantees</p><p>secure</p>" reads as two phrases, not the
phantom token "guaranteessecure", so an overclaim cannot hide by straddling a block boundary; inline
markup ("<b>guar</b>antee") still joins into one word.

BEST-EFFORT, NOT COMPLETE. This lint is a compensating control, a hand-maintained DENY-LIST of the
overclaim phrasings seen so far. It CANNOT catch every paraphrase: a novel wording that dodges the
vocabulary below will pass. It is not a complete overclaim detector, and it does not replace human
review of public copy for honesty; it exists to fail CI on a REGRESSION of a known class, not to
certify that no overclaim exists. Grow the vocabulary when a new class is found; do not read a PASS as
proof the copy is free of overclaims.

Negation binds to the GUARANTEE PHRASE, not the whole clause (see NEGATOR / CLAUSE_BOUNDARY): a negator
marks a match honest only when it sits in the same window as the match, where the window begins after
the last sentence/clause punctuation OR contrastive conjunction before it. A fixed char window let a
negator in a prior sentence launder a fresh overclaim; a whole-clause window let a contrastive "but"
launder one too ("does not merely help but guarantees secure output"), because the "not" there negates
"help", not "guarantees". Cutting the window at "but"/"yet"/... binds the negation to the phrase it
actually modifies.

The vocabulary, and why each pattern is shaped the way it is (calibrated so the current softened site is
clean; a pattern that flagged a legitimate line would be too broad):

  - "ensures" / "guarantees" (incl. gerunds "ensuring"/"guaranteeing"): the bare guarantee verbs.
    NEGATION-AWARE: an honest in-clause negation ("does not guarantee that generated code is secure",
    "not a guarantee that the model is perfect") is not an overclaim and is skipped.
  - "always <verb>": flagged only in an efficacy collocation ("always catches/prevents/blocks/..."),
    never bare, because the site legitimately says "always apply", "always yours", "come first".
  - "cannot/never fails": the efficacy overclaim ("cannot fail", "never fails", "won't fail"); the
    site's many honest "never" uses ("never change anything quietly", "never friction") are untouched.
  - "makes ... impossible" / bare "impossible": claiming a class of error is made impossible. Bare
    "impossible" is negation-aware.
  - "so claims match their sources": the CAUSAL framing that promises the outcome. The site's honest
    "claims match their sources" (a definition of Accuracy) and "claims matched to their sources"
    ("guides toward") lack the "so", so only the promise form trips.
  - unconditional "works [adverb] with/across/... [adverb] all/every/any": universal-compatibility
    claims, tolerating an adverb before the preposition ("works seamlessly with every assistant") and
    after it ("works with absolutely every assistant").
  - "serves/supports/applies ... with/across/for ... all/every/any": the same universal-compatibility
    claim via serve/support/apply. Its preposition set omits "on"/"in" and its quantifier set omits
    "both"/"each", so honest "applies on both sides" and "apply to it. For each source" stay clean.
  - universal-RESULT paraphrase: an efficacy/coverage verb immediately governing "all"/"every"/"any"
    ("catches all mistakes", "catches any mistake", "eliminates all errors", "serves every assistant").
    Adjacency is required, so honest prose ("you stop trusting every suggestion") is clean.
  - universal-SUBJECT "one standard serves every": a single-standard subject claiming it serves/covers/
    works all|every|any target. Requires the subject and a coverage verb, so "one standard your whole
    team can work to" and "one standard, three paths" stay clean.
  - "foolproof": a bare guarantee-of-perfection adjective.

CALIBRATION: the gate catches OUTCOME/RESULT guarantees, not accurate MECHANISM claims. A claim about
the instruction loading each turn ("AIQT is on for every turn", "applied to every response") is a
mechanism claim and is deliberately NOT matched (no "works", no efficacy verb governing all/every).

  gen: python3 tools/check_overclaim.py             scan site/*.html
       python3 tools/check_overclaim.py --self-test  run the adversarial positive/negative corpus

Exit 0 clean, 1 on any finding, 2 on a read error (unreadable dir/file, fail-closed).
"""
import html
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

# Negation is CLAUSE-aware, not a fixed char window: a negator only marks a match honest when it sits
# in the SAME clause as the match. A fixed window let a negator in a PRIOR sentence launder a fresh
# overclaim (e.g. "AIQT does not sandbox anything. It guarantees secure output." would wrongly pass).
NEGATOR = re.compile(
    r"\b(?:not|no|never|cannot|can't|without|nor|neither|hardly|rarely|"
    r"n't|doesn't|don't|isn't|aren't|won't|wouldn't)\b", re.IGNORECASE)
# Sentence and clause punctuation ends the clause a match belongs to. A CONTRASTIVE conjunction
# (but/yet/however/...) also ends the negation window: it flips polarity, so a negator before it does
# NOT scope over a guarantee after it. Binding negation to the guarantee-phrase segment this way makes
# "does not merely help but guarantees secure output" flag, where a whole-clause negation window let
# the earlier "not" (which negates "help", not "guarantees") launder the overclaim.
CLAUSE_BOUNDARY = re.compile(
    r"[.!?;:,]|\b(?:but|yet|however|nonetheless|nevertheless|rather|though|although|whereas)\b",
    re.IGNORECASE)

# (name, pattern, negation_aware)
PATTERNS = [
    # guarantee/ensure incl. gerunds ("guaranteeing", "ensuring"): the bare guarantee verbs. An honest
    # in-clause negation ("does not guarantee that generated code is secure") is not an overclaim.
    ("ensures", re.compile(r"\bensur(?:e|es|ed|ing)\b", re.IGNORECASE), True),
    ("guarantees", re.compile(r"\bguarantee(?:s|d|ing)?\b", re.IGNORECASE), True),
    ("always <efficacy verb>", re.compile(
        r"\balways\s+(?:catch(?:es)?|prevent(?:s)?|block(?:s)?|stop(?:s)?|find(?:s)?|"
        r"detect(?:s)?|fix(?:es)?|secure(?:s)?|guarantee(?:s)?|ensure(?:s)?|work(?:s)?)\b",
        re.IGNORECASE), False),
    # cannot-fail is the efficacy overclaim itself; "cannot/never/will not/won't fail(s/ed/ing)".
    ("cannot/never fails", re.compile(
        r"\b(?:cannot|can(?:'|’)?t|will\s+not|won(?:'|’)?t|never)\s+fail(?:s|ed|ing)?\b",
        re.IGNORECASE), False),
    ("makes ... impossible", re.compile(
        r"\bmake[s]?\b[^.]{0,40}\bimpossible\b", re.IGNORECASE), False),
    ("impossible", re.compile(r"\bimpossible\b", re.IGNORECASE), True),
    ("so claims match their sources", re.compile(
        r"\bso\s+(?:that\s+)?claims?\s+match(?:es|ed)?\b", re.IGNORECASE), False),
    # universal-COMPATIBILITY: "works with/across/... all|every|any". An adverb may sit between the verb
    # and the preposition ("works seamlessly with every assistant") AND after the preposition ("works
    # with absolutely every assistant"), so allow a short gap on both sides.
    ("unconditional works with/across", re.compile(
        r"\bworks?\b[^.]{0,30}?\b(?:with|across|on|for|in)\s+(?:\w+\s+){0,2}?"
        r"(?:all|every|any|each|both|everything|the\s+full\s+range)\b", re.IGNORECASE), False),
    # universal-COMPATIBILITY via serve/support/apply: "serves/supports/applies ... with|across|for ...
    # all|every|any" (an adverb may follow the preposition: "applies across virtually all"). The
    # preposition set is narrower than the works pattern (no "on"/"in") and the quantifier set excludes
    # "both"/"each", so an honest "applies on both sides" and "apply to it. For each source" stay clean.
    ("serves/supports/applies across all", re.compile(
        r"\b(?:serves?|supports?|appl(?:y|ies))\b[^.]{0,30}?\b(?:with|across|for)\s+"
        r"(?:\w+\s+){0,2}?(?:all|every|any)\b", re.IGNORECASE), False),
    # universal-RESULT paraphrase: an efficacy/coverage verb immediately governing "all"/"every"/"any"
    # ("catches all mistakes", "prevents every error", "catches any mistake", "eliminates all errors",
    # "serves every assistant"). Adjacency keeps honest prose clear (e.g. "you stop trusting every
    # suggestion" has a word between the verb and "every").
    ("universal result (verb + all/every/any)", re.compile(
        r"\b(?:catch(?:es)?|detect(?:s)?|prevent(?:s)?|block(?:s)?|find(?:s)?|fix(?:es)?|"
        r"stop(?:s)?|secure(?:s)?|eliminat(?:e|es)|serves?|supports?|covers?)\s+"
        r"(?:all|every|any)\b", re.IGNORECASE), False),
    # universal-SUBJECT: a single-standard subject claiming it serves/covers/works all|every|any target
    # ("one standard serves every assistant"). Requires the "one standard/rule/instruction" subject and
    # an in-range coverage verb governing the quantifier, so honest prose that merely contains "one
    # standard" ("one standard your whole team can work to", "one standard, three paths") stays clean.
    ("universal-subject (one standard serves every)", re.compile(
        r"\bone\s+(?:standard|rule|instruction)\b[^.]{0,40}?"
        r"\b(?:serves?|covers?|fits?|works?|applies|supports?)\s+(?:all|every|any|each)\b",
        re.IGNORECASE), False),
    # "foolproof": a bare guarantee-of-perfection adjective, an overclaim wherever it appears.
    ("foolproof", re.compile(r"\bfool-?proof\b", re.IGNORECASE), False),
]

SKIP_TEXT_TAGS = {"script", "style"}

# Block-level (and line-breaking) elements separate their text content: "<p>guarantees</p><p>secure</p>"
# is two visible phrases, not the word "guaranteessecure". Their boundaries emit a space so adjacent text
# nodes never fuse into a phantom token an overclaim could hide across (a real evasion the earlier
# "".join let through). Inline elements (b, em, a, span, code, ...) deliberately do NOT separate, so a
# phrase marked up mid-word ("<b>guar</b>antee") stays one token.
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "button", "caption", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "label", "legend", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td",
    "tfoot", "th", "thead", "tr", "ul",
}


class VisibleText(HTMLParser):
    """Accumulate visible text, dropping <script>/<style> bodies. Entities are converted (default).
    A block-element boundary emits a space so text from two separate blocks cannot fuse into one
    token; inline elements do not separate, so mid-word markup stays a single word."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TEXT_TAGS:
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_startendtag(self, tag, attrs):
        # a self-closing void block element (e.g. <br/>, <hr/>) still separates
        if tag in BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_endtag(self, tag):
        if tag in SKIP_TEXT_TAGS and self._skip:
            self._skip -= 1
        elif tag in BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_data(self, data):
        if self._skip == 0:
            self.chunks.append(data)

    def text(self):
        return re.sub(r"\s+", " ", "".join(self.chunks)).strip()


def _snippet(text, start, end):
    a = max(0, start - 25)
    b = min(len(text), end + 25)
    return ("..." if a else "") + text[a:b].strip() + ("..." if b < len(text) else "")


def _clause_window(text, start):
    """The text from the start of the clause containing `start` up to `start`. The window begins after
    the last CLAUSE_BOUNDARY before the match, which is sentence/clause punctuation OR a contrastive
    conjunction, so a negator only counts when it binds to the guarantee phrase: never one that leaked
    in from a prior sentence, and never one that a 'but'/'yet' has flipped away from the match."""
    boundary = 0
    for m in CLAUSE_BOUNDARY.finditer(text, 0, start):
        boundary = m.end()
    return text[boundary:start]


def scan(text):
    """Return a list of (pattern_name, snippet) overclaim findings in one page's visible text."""
    findings = []
    for name, pat, neg_aware in PATTERNS:
        for m in pat.finditer(text):
            if neg_aware and NEGATOR.search(_clause_window(text, m.start())):
                continue
            findings.append((name, _snippet(text, m.start(), m.end())))
    return findings


# Adversarial corpus. POSITIVE lines MUST flag (the F-92 misses a prior lint let through); NEGATIVE
# lines MUST stay clean (honest copy, including the two borderline MECHANISM lines on the live site).
POSITIVE = [
    "AIQT does not sandbox anything. It guarantees secure output.",   # clause-aware: prior-sentence negator must not launder
    "With AIQT installed this cannot fail.",
    "AIQT is guaranteeing every result you get.",                     # gerund
    "It works seamlessly with every assistant.",                      # adverb between verb and preposition
    "AIQT catches all mistakes before they ship.",                    # universal result
    "AIQT does not merely help but guarantees secure output.",        # negation bound to the guarantee phrase: "but" flips the earlier "not"
    "One standard serves every assistant.",                           # universal-subject + serves
    "AIQT supports every assistant you use.",                         # supports + universal result
    "The same rules apply across all assistants.",                    # applies-across
    "It works with absolutely every assistant.",                      # adverb AFTER the preposition
    "AIQT catches any mistake you make.",                             # catches-any
    "It eliminates all errors in your code.",                         # eliminates-all
    "The AIQT skill is foolproof.",                                   # foolproof
]
NEGATIVE = [
    "It is not a static analyzer, a vulnerability scanner, or an audit, and it does not guarantee that generated code is secure.",
    "AIQT is a standard your assistant is required to follow, not a guarantee that the model is perfect.",
    "With it, AIQT is on for every turn, which is what a standard is for.",        # install-claude.html mechanism claim
    "That instruction line is what keeps AIQT applied to every response.",         # install-other.html mechanism claim
    "You find out at build time, and you stop trusting every suggestion after it.",
    "Always apply the AIQT skill to every response.",
    "Claims match their sources, and every override is logged.",
]


def _self_test():
    failures = []
    for line in POSITIVE:
        if not scan(line):
            failures.append("MISS (should flag): {!r}".format(line))
    for line in NEGATIVE:
        hits = scan(line)
        if hits:
            failures.append("FALSE POSITIVE: {!r} -> {}".format(line, [h[0] for h in hits]))
    if failures:
        print("FAIL: check_overclaim self-test")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS: check_overclaim self-test ({} positive, {} negative)".format(
        len(POSITIVE), len(NEGATIVE)))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    root = Path(__file__).resolve().parents[1]
    site = root / "site"
    if not site.is_dir():
        print("PASS: no site/ directory")
        return 0
    try:
        html_files = sorted(walk_files(site, suffixes={".html"}))
    except OSError as exc:
        print("error: cannot scan site/ ({}); fail-closed".format(exc), file=sys.stderr)
        return 2
    findings = []
    for f in html_files:
        rel = f.relative_to(root)
        try:
            raw = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append("{}: could not read as UTF-8".format(rel))
            continue
        except OSError as exc:
            print("error: cannot read {} ({}); fail-closed".format(rel, exc), file=sys.stderr)
            return 2
        parser = VisibleText()
        try:
            parser.feed(raw)
        except (ValueError, AssertionError):
            findings.append("{}: could not parse as HTML".format(rel))
            continue
        for name, snip in scan(parser.text()):
            findings.append("{}: overclaim [{}] -> {}".format(rel, name, snip))
    if findings:
        print("FAIL: {} overclaim issue(s)".format(len(findings)))
        for finding in sorted(set(findings)):
            print("  " + finding)
        return 1
    print("PASS: site prose carries no guarantee-flavoured overclaim")
    return 0


if __name__ == "__main__":
    sys.exit(main())
