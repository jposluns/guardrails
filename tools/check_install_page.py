#!/usr/bin/env python3
"""Install-page tripwire: keep the platform picker static, visible, and complete.

site/install.html shows five AI-family setup sections (Claude, ChatGPT, Gemini, Copilot, and any other
assistant). A progressive-enhancement filter, site/js/install.js, lets a reader narrow the page to one
family: it sets a data-family attribute on the root element, and a CSS rule keyed on that attribute
collapses the other family sections. The point is that this is an ENHANCEMENT. With JavaScript off, failed,
or blocked, the attribute is never set, the guarded rule is inert, and all five sections show.

This is a TRIPWIRE for the common regressions in the STATIC MARKUP, plus a check that the guarded collapse
rule still exists. It is deliberately NOT a CSS engine or a browser: whether some NEW stylesheet rule hides
a family section without the guard is left to review and the cross-family QA, because a regex cannot model
CSS selector matching. It mirrors the approach of the sibling project's tools/check-install-page.py, adapted
to this repo's install page.

What it verifies (reliably):

  1. Each family id exists exactly once on a <section id=X data-family-section=X> with an <h2> that has
     visible text; no id in the document is duplicated.
  2. No family section carries, on itself, hidden, inert, aria-hidden="true", or the visually-hidden class,
     so none is default-hidden without JavaScript.
  3. No pre-set filter state: the root <html> has no data-family, and no picker link has aria-current.
  4. Each family has exactly one picker <a data-family-link=X href="#X"> inside the .platform-actions group
     (which install.js queries as '.platform-actions a[data-family-link]').
  5. Exactly one #family-reset button exists and is hidden in the static markup; the aria-live #family-status
     status region exists.
  6. install.js is referenced as an EXTERNAL <script src=".../js/install.js">, and neither the picker group
     nor any family section (their subtrees) carries an inline <script> or an on* event handler. This is
     SCOPED to the picker and sections: the shared header's theme/nav inline handlers, and the page's inline
     style attributes, are out of scope and never fail this gate.
  7. The guarded collapse rule is present in site/styles.css: a hiding rule whose selector carries a
     positive, boundary-anchored html[data-family] guard and names data-family-section. This catches the
     feature being deleted or its guard being renamed.

What it does NOT verify (the reviewer's and cross-family QA's job): it is not a CSS engine. It does not
detect a NEW stylesheet rule that hides a family section without the guard, a hiding ancestor, a custom
class that clips or zeroes opacity, or hiding by id. Those are visible regressions a browser check, a
reviewer, or the tri-family QA catches; a regex cannot decide them without false positives.

Usage:
  check_install_page.py             scan the page, styles, and script
  check_install_page.py --self-test build synthetic pages and assert a clean page passes and mutations fail

Exit 0 clean; 1 on a markup/picker/reset/guard regression (a finding); 2 on a missing or unreadable page,
stylesheet, or script (fail-closed), so an unreadable input can never read as clean.
"""
import re
import sys
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

PAGE_REL = "site/install.html"
STYLES_REL = "site/styles.css"
JS_REL = "site/js/install.js"

FAMILIES = ["claude", "chatgpt", "gemini", "copilot", "other"]
VOID = {"meta", "link", "img", "br", "hr", "input", "source", "wbr", "col"}
# Invisible characters a heading might be reduced to: zero-width spaces/joiner/BOM, no-break and soft-hyphen,
# word joiner, and the bidi marks.
ZERO_WIDTH = "\u200b\u200c\u200d\ufeff\u00a0\u2060\u200e\u200f\u00ad"

CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)
# A positive root guard: html[data-family] or html[data-family=...], boundary-anchored so a class (.html),
# a different element (xhtml), or a longer name (data-family-mode) does not count.
GUARD = re.compile(r"(?:^|[\s,>+~(])html\[data-family(?:[~^$*|]?=|\])", re.I)
# The page must reference install.js as an external script (with a src).
EXTERNAL_SCRIPT = re.compile(r'<script[^>]*\ssrc="[^"]*/js/install\.js"', re.I)
EXECUTABLE_TYPES = {"", "module", "text/javascript", "application/javascript", "text/ecmascript"}


def has_visible_text(data):
    return bool(data.translate({ord(c): None for c in ZERO_WIDTH}).strip())


def is_hiding_element(attrs):
    classes = attrs.get("class", "").split()
    return (
        "hidden" in attrs
        or "inert" in attrs
        or attrs.get("aria-hidden", "").lower() == "true"
        or "visually-hidden" in classes
    )


def _hides(body):
    b = re.sub(r"\s+", "", body).lower()
    return "display:none" in b or "visibility:hidden" in b or "visibility:collapse" in b


def collapse_rule_present(css_text):
    """True iff some hiding rule's selector carries the positive html[data-family] guard and names
    data-family-section: the guarded collapse rule that hides the non-selected sections only when the
    attribute is set."""
    stripped = CSS_COMMENT.sub("", css_text)
    for selector, body in CSS_RULE.findall(stripped):
        if _hides(body) and "data-family-section" in selector.lower() and GUARD.search(selector):
            return True
    return False


class InstallParser(HTMLParser):
    """Collect the static-markup facts the tripwire needs, and, scoped to the picker group and the family
    sections only, any inline <script> or on* handler."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []                       # frames: {"tag", "region"} ; region marks a scoped subtree root
        self.html_attrs = {}
        self.family_count = {f: 0 for f in FAMILIES}
        self.family_has_h2 = {f: False for f in FAMILIES}
        self.family_hidden_self = []
        self.other_family_carriers = []
        self.id_counts = Counter()
        self.ids = set()
        self.family_links = {}
        self.family_link_href = {}
        self.family_links_outside_pa = set()
        self.family_links_current = set()
        self.reset_count = 0
        self.reset_hidden_count = 0
        self.status_ok = 0
        self.scoped_inline_scripts = 0
        self.scoped_inline_handlers = []
        self._h2_family = None

    def _in_region(self):
        return any(frame["region"] for frame in self.stack)

    def _nearest_family(self):
        for frame in reversed(self.stack):
            if frame.get("family"):
                return frame["family"]
        return None

    def handle_starttag(self, tag, attrs_list):
        attrs = {k.lower(): (v if v is not None else "") for k, v in attrs_list}
        if tag == "html":
            self.html_attrs = attrs
        if "id" in attrs:
            self.id_counts[attrs["id"]] += 1
            self.ids.add(attrs["id"])
        in_pa = any("platform-actions" in frame.get("cls", "").split() for frame in self.stack)

        fam_here = None
        if "data-family-section" in attrs:
            fam = attrs["data-family-section"]
            if tag == "section" and attrs.get("id") == fam and fam in FAMILIES:
                self.family_count[fam] += 1
                fam_here = fam
                if is_hiding_element(attrs):
                    self.family_hidden_self.append(fam)
            else:
                self.other_family_carriers.append(
                    "<{} data-family-section={!r}>".format(tag, attrs.get("data-family-section")))

        if tag == "a" and "data-family-link" in attrs:
            fam = attrs["data-family-link"]
            self.family_links[fam] = self.family_links.get(fam, 0) + 1
            self.family_link_href[fam] = attrs.get("href", "")
            if not in_pa:
                self.family_links_outside_pa.add(fam)
            if "aria-current" in attrs:
                self.family_links_current.add(fam)

        if tag == "button" and attrs.get("id") == "family-reset":
            self.reset_count += 1
            if "hidden" in attrs:
                self.reset_hidden_count += 1

        if attrs.get("id") == "family-status":
            if "aria-live" in attrs or attrs.get("role", "").lower() == "status":
                self.status_ok += 1

        # scoped inline-code check: within the picker group or a family section subtree.
        opens_region = (attrs.get("id") == "install-picker") or ("data-family-section" in attrs)
        if self._in_region() or opens_region:
            for key in attrs:
                if re.match(r"on[a-z]+$", key):
                    self.scoped_inline_handlers.append("<{} {}=...>".format(tag, key))
            if tag == "script" and "src" not in attrs \
                    and attrs.get("type", "").strip().lower() in EXECUTABLE_TYPES:
                self.scoped_inline_scripts += 1

        if tag == "h2":
            self._h2_family = self._nearest_family()

        if tag not in VOID:
            self.stack.append({"tag": tag, "region": bool(opens_region), "family": fam_here,
                               "cls": attrs.get("class", "")})

    def handle_endtag(self, tag):
        if tag == "h2":
            self._h2_family = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self._h2_family and has_visible_text(data):
            self.family_has_h2[self._h2_family] = True


def analyze(page_text, css_text):
    """The static findings for the page markup plus the stylesheet collapse rule. Returns a list of
    strings; an empty list is a clean pass."""
    parser = InstallParser()
    parser.feed(page_text)
    problems = []

    # 1. family sections static, complete, unique; ids unique
    for fam in FAMILIES:
        n = parser.family_count[fam]
        if n == 0:
            problems.append('no <section id="{0}" data-family-section="{0}"> found; every family section '
                            "must be present and statically visible.".format(fam))
        elif n > 1:
            problems.append("the {} family section appears {} times; it must appear exactly once.".format(fam, n))
        elif not parser.family_has_h2[fam]:
            problems.append("the {} family section has no <h2> with visible text; it may be an empty "
                            "stub.".format(fam))
    for fam in parser.family_hidden_self:
        problems.append("the {} family section carries hidden, inert, aria-hidden, or the visually-hidden "
                        "class in the static markup, so it would not appear without JavaScript.".format(fam))
    for extra in parser.other_family_carriers:
        problems.append("data-family-section appears on an unexpected element {}; only the five family "
                        "<section> elements may carry it.".format(extra))
    for dup_id, count in sorted(parser.id_counts.items()):
        if count > 1:
            problems.append('the id "{}" appears {} times; ids must be unique or getElementById and the '
                            "fragment anchors resolve to the wrong element.".format(dup_id, count))

    # 2. picker integrity
    for fam in FAMILIES:
        count = parser.family_links.get(fam, 0)
        if count == 0:
            problems.append('no picker link <a data-family-link="{}"> found.'.format(fam))
            continue
        if count > 1:
            problems.append("the {} picker link appears {} times; it must appear exactly once.".format(fam, count))
        href = parser.family_link_href.get(fam, "")
        if href != "#{}".format(fam):
            problems.append('the {0} picker link points at {1!r}, not "#{0}"; the deep link and the filter '
                            "would disagree.".format(fam, href))
    for fam in sorted(parser.family_links_outside_pa):
        problems.append("the {} picker link is not inside the .platform-actions group, so install.js (which "
                        "queries '.platform-actions a[data-family-link]') would not find it.".format(fam))

    # 3. no pre-set filter state
    if "data-family" in parser.html_attrs:
        problems.append("the root <html> element carries data-family in the static markup; the filter must "
                        "start unset so the unscripted page shows every family.")
    if parser.family_links_current:
        problems.append("a picker link carries aria-current in the static markup ({}); only install.js may "
                        "set it.".format(", ".join(sorted(parser.family_links_current))))

    # 4. exactly one hidden reset; the aria-live status region
    if parser.reset_count == 0:
        problems.append('the reset button (id="family-reset") is missing.')
    elif parser.reset_count > 1:
        problems.append('the reset button (id="family-reset") appears {} times.'.format(parser.reset_count))
    elif parser.reset_hidden_count != parser.reset_count:
        problems.append("the reset button is not hidden in the static markup; it does nothing without "
                        "JavaScript and must carry the hidden attribute.")
    if parser.status_ok == 0:
        problems.append('the aria-live status region (id="family-status" with aria-live or role="status") '
                        "is missing.")

    # 5. install.js external; no inline code inside the picker or the family sections
    if not EXTERNAL_SCRIPT.search(page_text):
        problems.append("the page does not reference /js/install.js as an external <script src=...>; the "
                        "picker behaviour would be gone.")
    if parser.scoped_inline_scripts:
        problems.append("the picker or a family section contains an inline <script>; move the code to "
                        "site/js/install.js.")
    for handler in parser.scoped_inline_handlers:
        problems.append("the picker or a family section carries an inline event handler {}; bind the "
                        "listener in site/js/install.js instead.".format(handler))

    # 6. the guarded collapse rule still exists
    if not collapse_rule_present(css_text):
        problems.append("no guarded collapse rule found in site/styles.css: a rule keyed on a positive "
                        "html[data-family] must hide the non-selected data-family-section. The selector "
                        "feature has rotted out, or its guard is no longer the html[data-family] form this "
                        "gate recognizes.")
    return problems


def run(root):
    page, styles, js = root / PAGE_REL, root / STYLES_REL, root / JS_REL
    for path, rel in ((page, PAGE_REL), (styles, STYLES_REL), (js, JS_REL)):
        if path.is_symlink() or not path.is_file():
            print("error: {} is absent or a symlink; the install-page gate cannot evaluate; "
                  "fail-closed".format(rel), file=sys.stderr)
            return 2
    try:
        page_text = page.read_text(encoding="utf-8")
        css_text = styles.read_text(encoding="utf-8")
        js.read_text(encoding="utf-8")   # opened so an unreadable script fails closed, not silently clean
    except (OSError, UnicodeDecodeError) as exc:
        print("error: cannot read an install-page input ({}); fail-closed".format(exc), file=sys.stderr)
        return 2

    problems = analyze(page_text, css_text)
    if problems:
        print("FAIL: {} install-page problem(s):".format(len(problems)))
        for problem in problems:
            print("  - " + problem)
        return 1
    print("PASS: the install page carries all five family sections statically and visibly, the picker and "
          "single hidden reset and status region are present with no pre-set filter state, no inline code in "
          "the picker or sections, and the guarded collapse rule is present.")
    return 0


# --- self-test ----------------------------------------------------------------------------------------
# A synthetic conformant page + stylesheet pass; each targeted mutation fails (exit 1); and a missing input
# fails closed (exit 2).

def _clean_page():
    picker = ['<div class="platform-actions" id="install-picker">']
    for fam in FAMILIES:
        picker.append('<a class="platform-button" href="#{0}" data-family-link="{0}">{0}</a>'.format(fam))
    picker.append('<button type="button" id="family-reset" hidden>Show all</button>')
    picker.append('<span role="status" aria-live="polite" id="family-status"></span>')
    picker.append("</div>")
    # A header inline handler and inline style OUTSIDE the picker and sections, which must NOT fail the gate.
    header = ('<header><button onclick="toggleNav()">menu</button>'
              '<p style="margin-top:1rem">intro</p></header>')
    sections = []
    for fam in FAMILIES:
        sections.append('<section id="{0}" data-family-section="{0}"><h2>Add AIQT to {0}</h2>'
                        '<p style="margin-top:1rem">steps</p></section>'.format(fam))
    body = header + "\n".join(picker) + "\n" + "\n".join(sections)
    return ('<!doctype html><html lang="en"><head>'
            '<script>var t=1;</script></head><body>' + body +
            '<script src="/js/install.js" defer></script></body></html>')


_CLEAN_CSS = ("body{color:#111}\n"
              "html[data-family] section[data-family-section]{display:none}\n"
              'html[data-family="claude"] section[data-family-section="claude"]{display:block}\n')


def _self_test():
    import contextlib
    import io
    import shutil
    import tempfile

    failures = []
    clean = _clean_page()

    # Pure analyze() clean pass.
    if analyze(clean, _CLEAN_CSS):
        failures.append("a clean page produced findings: {}".format(analyze(clean, _CLEAN_CSS)))

    mutations = [
        ("dropped family section",
         clean.replace('<section id="gemini" data-family-section="gemini"><h2>Add AIQT to gemini</h2>'
                       '<p style="margin-top:1rem">steps</p></section>', ""), _CLEAN_CSS),
        ("default-hidden section",
         clean.replace('<section id="copilot" data-family-section="copilot">',
                       '<section id="copilot" data-family-section="copilot" hidden>'), _CLEAN_CSS),
        ("picker link wrong href",
         clean.replace('href="#other" data-family-link="other"',
                       'href="#others" data-family-link="other"'), _CLEAN_CSS),
        ("pre-set data-family on root",
         clean.replace('<html lang="en">', '<html lang="en" data-family="claude">'), _CLEAN_CSS),
        ("reset not hidden",
         clean.replace('<button type="button" id="family-reset" hidden>Show all</button>',
                       '<button type="button" id="family-reset">Show all</button>'), _CLEAN_CSS),
        ("missing status region",
         clean.replace('<span role="status" aria-live="polite" id="family-status"></span>', ""), _CLEAN_CSS),
        ("inline handler on a section",
         clean.replace('<section id="claude" data-family-section="claude">',
                       '<section id="claude" data-family-section="claude" onclick="x()">'), _CLEAN_CSS),
        ("inline script inside the picker",
         clean.replace('<button type="button" id="family-reset" hidden>Show all</button>',
                       '<button type="button" id="family-reset" hidden>Show all</button><script>x()</script>'),
         _CLEAN_CSS),
        ("install.js no longer external",
         clean.replace('<script src="/js/install.js" defer></script>', ""), _CLEAN_CSS),
        ("collapse rule removed", clean, "body{color:#111}\n"),
        ("collapse guard renamed", clean,
         "html[data-family-mode] section[data-family-section]{display:none}\n"),
    ]
    for label, page, css in mutations:
        if not analyze(page, css):
            failures.append("mutation '{}' produced no finding".format(label))

    # run() exit-code legs on temp roots.
    def quiet(r):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run(r)

    tmp = Path(tempfile.mkdtemp(prefix="aiqt-install-page-selftest-"))
    try:
        good = tmp / "good"
        (good / "site" / "js").mkdir(parents=True)
        (good / PAGE_REL).write_text(clean, encoding="utf-8")
        (good / STYLES_REL).write_text(_CLEAN_CSS, encoding="utf-8")
        (good / JS_REL).write_text("// install\n", encoding="utf-8")
        if quiet(good) != 0:
            failures.append("a conformant tree did not pass (exit 0)")

        regressed = tmp / "regressed"
        (regressed / "site" / "js").mkdir(parents=True)
        (regressed / PAGE_REL).write_text(clean.replace('data-family-link="other"',
                                                         'data-family-link="other" aria-current="true"'),
                                          encoding="utf-8")
        (regressed / STYLES_REL).write_text(_CLEAN_CSS, encoding="utf-8")
        (regressed / JS_REL).write_text("// install\n", encoding="utf-8")
        if quiet(regressed) != 1:
            failures.append("a regressed page did not report a finding (exit 1)")

        missing = tmp / "missing"
        (missing / "site").mkdir(parents=True)
        if quiet(missing) != 2:
            failures.append("a missing page did not fail closed (exit 2)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if failures:
        print("FAIL: check_install_page self-test")
        for f in failures:
            print("  - " + f)
        return 1
    print("PASS: check_install_page self-test: a clean page passes; {} targeted mutations each fail; and a "
          "conformant tree (exit 0), a regressed page (exit 1), and a missing page (exit 2) resolve as "
          "expected.".format(len(mutations)))
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return _self_test()
    return run(Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    sys.exit(main())
