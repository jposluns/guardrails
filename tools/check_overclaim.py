#!/usr/bin/env python3
"""Site-wide overclaim lint for site/*.html.

AIQT's public copy is deliberately guidance-not-guarantee: it says what your assistant is asked and
required to do, never what AIQT itself guarantees the model will do. This gate catches a regression of
the guarantee-flavoured class the F-59/F-67 pass softened, so a reintroduced overclaim fails CI rather
than shipping. It scans the VISIBLE TEXT of each page (tags, <script>, and <style> stripped; entities
unescaped; whitespace collapsed), so a phrase that wraps across source lines is still one string and an
overclaim hidden in an attribute is not falsely flagged.

The vocabulary, and why each pattern is shaped the way it is (calibrated so the current softened site is
clean; a pattern that flagged a legitimate line would be too broad):

  - "ensures" / "guarantees": the bare guarantee verbs. NEGATION-AWARE: an honest negation ("does not
    guarantee that generated code is secure", "not a guarantee that the model is perfect") is not an
    overclaim, so a match preceded by a negator in the same clause is skipped.
  - "always <verb>": flagged only in an efficacy collocation ("always catches/prevents/blocks/..."),
    never bare, because the site legitimately says "always apply", "always yours", "come first".
  - "never fails": the specific efficacy overclaim; the site's many honest "never" uses ("never change
    anything quietly", "never friction") are untouched.
  - "makes ... impossible" / bare "impossible": claiming a class of error is made impossible. Bare
    "impossible" is negation-aware.
  - "so claims match their sources": the CAUSAL framing that promises the outcome. The site's honest
    "claims match their sources" (a definition of Accuracy) and "claims matched to their sources"
    ("guides toward") lack the "so", so only the promise form trips.
  - unconditional "works with/across ... all/every/any": universal-compatibility claims.

Exit 0 clean, 1 on any finding, 2 on a read error (unreadable dir/file, fail-closed).
"""
import html
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _walk import walk_files  # noqa: E402  fail-closed tree walk (os.walk, not rglob)

# A negator anywhere in the ~55 chars before a negation-aware match marks the phrase honest.
NEGATOR = re.compile(
    r"\b(?:not|no|never|cannot|can't|without|nor|neither|hardly|rarely|"
    r"n't|doesn't|don't|isn't|aren't|won't|wouldn't)\b", re.IGNORECASE)
NEG_WINDOW = 55

# (name, pattern, negation_aware)
PATTERNS = [
    ("ensures", re.compile(r"\bensure[sd]?\b", re.IGNORECASE), True),
    ("guarantees", re.compile(r"\bguarantee[sd]?\b", re.IGNORECASE), True),
    ("always <efficacy verb>", re.compile(
        r"\balways\s+(?:catch(?:es)?|prevent(?:s)?|block(?:s)?|stop(?:s)?|find(?:s)?|"
        r"detect(?:s)?|fix(?:es)?|secure(?:s)?|guarantee(?:s)?|ensure(?:s)?|work(?:s)?)\b",
        re.IGNORECASE), False),
    ("never fails", re.compile(r"\bnever\s+fail(?:s)?\b", re.IGNORECASE), False),
    ("makes ... impossible", re.compile(
        r"\bmake[s]?\b[^.]{0,40}\bimpossible\b", re.IGNORECASE), False),
    ("impossible", re.compile(r"\bimpossible\b", re.IGNORECASE), True),
    ("so claims match their sources", re.compile(
        r"\bso\s+(?:that\s+)?claims?\s+match(?:es|ed)?\b", re.IGNORECASE), False),
    ("unconditional works with/across", re.compile(
        r"\bworks?\s+(?:with|across|on|for|in)\s+"
        r"(?:all|every|any|each|both|everything|the\s+full\s+range)\b", re.IGNORECASE), False),
]

SKIP_TEXT_TAGS = {"script", "style"}


class VisibleText(HTMLParser):
    """Accumulate visible text, dropping <script>/<style> bodies. Entities are converted (default)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TEXT_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in SKIP_TEXT_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            self.chunks.append(data)

    def text(self):
        return re.sub(r"\s+", " ", "".join(self.chunks)).strip()


def _snippet(text, start, end):
    a = max(0, start - 25)
    b = min(len(text), end + 25)
    return ("..." if a else "") + text[a:b].strip() + ("..." if b < len(text) else "")


def scan(text):
    """Return a list of (pattern_name, snippet) overclaim findings in one page's visible text."""
    findings = []
    for name, pat, neg_aware in PATTERNS:
        for m in pat.finditer(text):
            if neg_aware:
                window = text[max(0, m.start() - NEG_WINDOW):m.start()]
                if NEGATOR.search(window):
                    continue
            findings.append((name, _snippet(text, m.start(), m.end())))
    return findings


def main():
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
