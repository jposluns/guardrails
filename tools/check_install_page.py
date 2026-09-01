#!/usr/bin/env python3
"""Install-page tripwire: keep the platform picker static, visible, and complete.

site/install.html shows six AI-family setup sections (Claude, ChatGPT, Gemini, Copilot, and any other
assistant). A progressive-enhancement filter, site/js/install.js, lets a reader narrow the page to one
family: it sets a data-family attribute on the root element, and a CSS rule keyed on that attribute
collapses the other family sections. The point is that this is an ENHANCEMENT. With JavaScript off, failed,
or blocked, the attribute is never set, the guarded rule is inert, and all six sections show.

This is a TRIPWIRE for the common regressions in the STATIC MARKUP, plus a check that the guarded collapse
rule still exists. It is deliberately NOT a CSS engine or a browser: whether some NEW stylesheet rule hides
a family section without the guard is left to review and the cross-family QA, because a regex cannot model
CSS selector matching. It mirrors the approach of the sibling project's tools/check-install-page.py, adapted
to this repo's install page.

What it verifies (reliably):

  1. Each family id exists exactly once on a <section id=X data-family-section=X> with an <h2> that has
     visible text; no id in the document is duplicated.
  2. No family section carries, on itself, hidden, inert, aria-hidden="true", the visually-hidden class, or
     an inline display:none / visibility:hidden style, so none is default-hidden without JavaScript.
  3. No pre-set filter state: the root <html> has no data-family, and no picker link has aria-current.
  4. Each family has exactly one picker <a data-family-link=X href="#X"> inside the .platform-actions group
     (which install.js queries as '.platform-actions a[data-family-link]').
  5. The #install-picker group is present and NOT hidden; exactly one #family-reset button exists and is
     hidden in the static markup; the #family-status region announces (aria-live polite/assertive, or
     role="status" with no aria-live="off" overriding it).
  6. install.js is referenced as an EXECUTABLE external <script src=".../js/install.js"> (not commented out,
     no non-JS type attribute), and site/js/install.js is non-empty and carries the real picker logic. Also
     neither the picker group nor any family section (their subtrees) carries an inline <script> or an on*
     event handler. That inline-code scope is keyed by STRUCTURE (the .platform-actions group, #install-picker,
     or a data-family-section), so removing the id does not move the picker's handlers out of scope; the
     shared header's theme/nav inline handlers and the page's inline style attributes stay out of scope.
  7. The guarded collapse rules are present in site/styles.css: the hide-all rule (a SINGLE selector part
     carrying a positive, non-negated html[data-family] presence guard AND the generic [data-family-section],
     with a real display:none) AND all six per-family reveal rules (each a single selector part with
     html[data-family="<fam>"] and [data-family-section="<fam>"] and display:block). The property is matched
     at a boundary, so a --custom property does not count; a :not()-negated guard, a guard and section split
     across comma selectors, or a missing reveal each fail. This catches the feature being deleted, its guard
     renamed or negated, or the page collapsed to hide everything.

What it does NOT verify (the reviewer's and cross-family QA's job): it is not a CSS engine. It does not
detect a NEW stylesheet rule (beyond the guarded ones above) that hides a family section without the guard,
a hiding ancestor, a custom class that clips or zeroes opacity, or hiding by id. It does not execute
install.js or prove the picker logic is correct, only that the expected functions are present. Those are
visible regressions a browser check, a reviewer, or the tri-family QA catches; a regex cannot decide them
without false positives.

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

FAMILIES = ["claude", "chatgpt", "gemini", "copilot", "copilotstudio", "other"]
VOID = {"meta", "link", "img", "br", "hr", "input", "source", "wbr", "col"}
# Invisible characters a heading might be reduced to: zero-width spaces/joiner/BOM, no-break and soft-hyphen,
# word joiner, and the bidi marks.
ZERO_WIDTH = "\u200b\u200c\u200d\ufeff\u00a0\u2060\u200e\u200f\u00ad"

CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)
# A hide/show declaration matched at a PROPERTY boundary, so a CSS custom property (--display:none) that
# sets a variable and hides nothing is NOT mistaken for the real display:none property. The (?<![\w-])
# lookbehind rejects a preceding word char or hyphen (the "--" of a custom property, or "x-display").
HIDE_DECL = re.compile(r"(?<![\w-])(?:display:none|visibility:(?:hidden|collapse))")
SHOW_DECL = re.compile(r"(?<![\w-])display:block")
# A :not(...) negation of the guard inverts its meaning (hiding when the attribute is ABSENT), so the
# guard is tested only AFTER the negations are stripped from a selector.
NOT_PSEUDO = re.compile(r":not\([^)]*\)", re.I)
# The presence guard html[data-family] (no value) and the family section section[data-family-section] (no
# value) that together form the hide-all rule; each boundary-anchored so .html, xhtml, or data-family-mode
# does not count, and the closing ] pins the attribute to its exact name (no value). The section attribute
# must sit on a `section` tag specifically, so a wrong-tag selector like html[data-family] x[data-family-
# section] (which matches no real <section> and would collapse nothing) does not count.
HIDE_GUARD = re.compile(r"(?:^|[\s>+~])html\[data-family\]", re.I)
GENERIC_SECTION = re.compile(r"(?:^|[\s>+~])section\[data-family-section\]", re.I)
# The page must reference install.js as an EXECUTABLE external script; parsed structurally (below) so a
# commented-out tag or a non-JS type attribute does not count.
EXECUTABLE_TYPES = {"", "module", "text/javascript", "application/javascript", "text/ecmascript"}
# The picker logic install.js must actually carry, so an empty or stub file is caught (matched
# case-insensitively as substrings). The tokens are sought only in CODE: JavaScript comments are stripped
# first (below), so a comment-only file (e.g. "// render apply family") no longer reads as real logic.
JS_LOGIC_TOKENS = ("render", "apply", "family")
# JavaScript comment forms, stripped before the picker-logic token test. This is a heuristic strip (it does
# not model strings or regex literals), sufficient for this tripwire: it prevents a comment-only stub from
# passing by mentioning the tokens, and never removes real code that carries them.
JS_LINE_COMMENT = re.compile(r"//[^\n]*")
JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def has_visible_text(data):
    return bool(data.translate({ord(c): None for c in ZERO_WIDTH}).strip())


def _style_hides(style):
    return bool(HIDE_DECL.search(re.sub(r"\s+", "", style).lower()))


def is_hiding_element(attrs):
    classes = attrs.get("class", "").split()
    return (
        "hidden" in attrs
        or "inert" in attrs
        or attrs.get("aria-hidden", "").lower() == "true"
        or "visually-hidden" in classes
        or _style_hides(attrs.get("style", ""))
    )


def _hides(body):
    return bool(HIDE_DECL.search(re.sub(r"\s+", "", body).lower()))


def _shows(body):
    return bool(SHOW_DECL.search(re.sub(r"\s+", "", body).lower()))


def _css_rules(css_text):
    return CSS_RULE.findall(CSS_COMMENT.sub("", css_text))


def _selector_parts(selector):
    """The comma-separated selectors of a rule, each with its :not() negations stripped, so a guard and a
    family-section named in DIFFERENT comma parts never satisfy a single-part requirement, and a negated
    guard never counts."""
    return [NOT_PSEUDO.sub("", part) for part in selector.split(",")]


def hide_all_rule_present(css_text):
    """True iff a single selector part carries BOTH the positive html[data-family] presence guard and the
    generic [data-family-section] (no value), with a real display:none / visibility:hidden body. This is
    the rule that collapses every non-selected family section only when the attribute is set."""
    for selector, body in _css_rules(css_text):
        if not _hides(body):
            continue
        for part in _selector_parts(selector):
            if HIDE_GUARD.search(part) and GENERIC_SECTION.search(part):
                return True
    return False


def family_reveal_present(css_text, fam):
    """True iff a single selector part carries BOTH html[data-family="<fam>"] and
    [data-family-section="<fam>"] with a real display:block body: the rule that re-shows the selected
    family. Without all six, a selected family (or all families) would stay hidden."""
    guard = re.compile(
        r'(?:^|[\s>+~])html\[data-family\s*[~^$*|]?=\s*["\']?' + re.escape(fam) + r'["\']?\s*\]', re.I)
    section = re.compile(
        r'(?:^|[\s>+~])section\[data-family-section\s*[~^$*|]?=\s*["\']?' + re.escape(fam)
        + r'["\']?\s*\]', re.I)
    for selector, body in _css_rules(css_text):
        if not _shows(body):
            continue
        for part in _selector_parts(selector):
            if guard.search(part) and section.search(part):
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
        self.picker_present = 0
        self.picker_hidden = 0
        self.install_script_ok = 0
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

        if attrs.get("id") == "install-picker":
            self.picker_present += 1
            if is_hiding_element(attrs):
                self.picker_hidden += 1

        if attrs.get("id") == "family-status":
            live = attrs.get("aria-live", "").strip().lower()
            role_status = attrs.get("role", "").strip().lower() == "status"
            # A live region announces only for aria-live polite/assertive, or an implicit role="status"
            # with no aria-live overriding it; an explicit aria-live="off" defeats the announcement even
            # under role="status", so "off" never counts as live.
            if (live in ("polite", "assertive") or role_status) and live != "off":
                self.status_ok += 1

        # install.js must be an EXECUTABLE external script. Parsed here (not by regex over the raw page) so
        # a commented-out <script> is invisible to the parser, and a non-JS type (application/json) or a
        # missing src does not count. The src is matched by its BASENAME (query and fragment stripped): the
        # basename must equal "install.js" exactly, so a suffixed path like /js/install.js.disabled, which a
        # substring test would accept, is rejected.
        if tag == "script" and "src" in attrs \
                and attrs.get("type", "").strip().lower() in EXECUTABLE_TYPES:
            src = attrs.get("src", "")
            basename = src.split("#", 1)[0].split("?", 1)[0].rsplit("/", 1)[-1]
            if basename == "install.js":
                self.install_script_ok += 1

        # scoped inline-code check: within the picker group or a family section subtree, keyed by STRUCTURE
        # (the .platform-actions group, the #install-picker id, or a data-family-section) so removing the id
        # cannot move the picker's own handlers out of scope.
        cls = attrs.get("class", "").split()
        opens_region = ("platform-actions" in cls) or (attrs.get("id") == "install-picker") \
            or ("data-family-section" in attrs)
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


def analyze(page_text, css_text, js_text):
    """The static findings for the page markup, the stylesheet collapse rules, and install.js. Returns a
    list of strings; an empty list is a clean pass."""
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
        problems.append("data-family-section appears on an unexpected element {}; only the six family "
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

    # the picker group itself must be present and not hidden
    if parser.picker_present == 0:
        problems.append('the picker group (id="install-picker") is missing; the reader has no control to '
                        "choose a family.")
    elif parser.picker_hidden:
        problems.append('the picker group (id="install-picker") is hidden in the static markup (hidden, '
                        "inert, aria-hidden, the visually-hidden class, or an inline display:none/"
                        "visibility:hidden style); the reader could not choose a family without JavaScript.")

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

    # 5. install.js is an executable external script carrying real logic; no inline code in the picker/sections
    if not parser.install_script_ok:
        problems.append("the page does not reference /js/install.js as an executable external "
                        "<script src=...> (it is commented out, carries a non-JS type attribute, or is "
                        "absent); the picker behaviour would be gone.")
    if not js_text.strip():
        problems.append("site/js/install.js is empty; the picker logic is gone.")
    else:
        # Strip JavaScript comments before the token test so a comment-only file cannot mention the tokens
        # and read as real logic.
        code = JS_LINE_COMMENT.sub("", JS_BLOCK_COMMENT.sub("", js_text)).lower()
        missing = [tok for tok in JS_LOGIC_TOKENS if tok not in code]
        if missing:
            problems.append("site/js/install.js does not carry the expected picker logic (missing: {}); it "
                            "may be a stub or a comment-only placeholder.".format(", ".join(missing)))
    if parser.scoped_inline_scripts:
        problems.append("the picker or a family section contains an inline <script>; move the code to "
                        "site/js/install.js.")
    for handler in parser.scoped_inline_handlers:
        problems.append("the picker or a family section carries an inline event handler {}; bind the "
                        "listener in site/js/install.js instead.".format(handler))

    # 6. the guarded collapse rules still exist: the hide-all rule AND all six per-family reveal rules
    if not hide_all_rule_present(css_text):
        problems.append("no guarded hide-all collapse rule found in site/styles.css: a single selector must "
                        "carry a positive html[data-family] presence guard AND the generic "
                        "[data-family-section] (guard not negated, both in the same selector) with a real "
                        "display:none. The feature has rotted out, its guard was renamed or negated, the "
                        "guard and section were split across selectors, or the property is a --custom one.")
    missing_reveals = [f for f in FAMILIES if not family_reveal_present(css_text, f)]
    if missing_reveals:
        problems.append("the per-family reveal rule is missing for: {}. Each family needs a single selector "
                        'html[data-family="<fam>"] ... [data-family-section="<fam>"] with display:block, or '
                        "the selected family (or every family) would stay hidden.".format(
                            ", ".join(missing_reveals)))
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
        js_text = js.read_text(encoding="utf-8")   # read so an unreadable OR empty script fails, not silently clean
    except (OSError, UnicodeDecodeError) as exc:
        print("error: cannot read an install-page input ({}); fail-closed".format(exc), file=sys.stderr)
        return 2

    problems = analyze(page_text, css_text, js_text)
    if problems:
        print("FAIL: {} install-page problem(s):".format(len(problems)))
        for problem in problems:
            print("  - " + problem)
        return 1
    print("PASS: the install page carries all six family sections statically and visibly, the visible "
          "picker and single hidden reset and announcing status region are present with no pre-set filter "
          "state, install.js is an executable external script carrying the picker logic with no inline code "
          "in the picker or sections, and the guarded hide-all plus all six per-family reveal rules are "
          "present.")
    return 0


# --- self-test ----------------------------------------------------------------------------------------
# A synthetic conformant page + stylesheet + script pass; each targeted mutation (including the hardened
# ones: inline-style hiding, aria-live="off", a missing or hidden picker, an inline handler on a de-ided
# .platform-actions, a commented-out or non-JS-type or suffixed-src (.disabled, fix B) or empty or stub or
# comment-only (fix C) install.js, a --custom-property or :not()-negated or split or wrong-tag (fix D)
# hide-all guard, wrong-tag (fix D) reveal rules, and all six reveal rules removed) fails; and a missing
# input fails closed (exit 2).

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


_REVEALS = ",\n".join(
    'html[data-family="{0}"] section[data-family-section="{0}"]'.format(f) for f in FAMILIES)
_CLEAN_CSS = ("body{color:#111}\n"
              "html[data-family] section[data-family-section]{display:none}\n"
              + _REVEALS + "{display:block}\n")

_CLEAN_JS = ("(function(){\n"
             "  var FAMILIES = ['claude','chatgpt','gemini','copilot','other'];\n"
             "  var root = document.documentElement;\n"
             "  function render(family){ root.setAttribute('data-family', family); }\n"
             "  function apply(){ render(FAMILIES[0]); }\n"
             "  apply();\n"
             "})();\n")


def _self_test():
    import contextlib
    import io
    import shutil
    import tempfile

    failures = []
    clean = _clean_page()

    # Pure analyze() clean pass.
    if analyze(clean, _CLEAN_CSS, _CLEAN_JS):
        failures.append("a clean page produced findings: {}".format(analyze(clean, _CLEAN_CSS, _CLEAN_JS)))

    # A split-selector hide-all (guard and section in DIFFERENT comma parts) and a :not()-negated guard:
    # both look superficially guarded but do not actually collapse the sections when the attribute is set.
    _split_css = ("html[data-family] p, section[data-family-section]{display:none}\n" + _REVEALS
                  + "{display:block}\n")
    _negated_css = (":not(html[data-family]) section[data-family-section]{display:none}\n" + _REVEALS
                    + "{display:block}\n")
    _custom_prop_css = ("html[data-family] section[data-family-section]{--display:none}\n" + _REVEALS
                        + "{display:block}\n")
    _no_reveals_css = "html[data-family] section[data-family-section]{display:none}\n"
    # Fix D: a wrong-tag selector carries the attribute on a non-section element (x[data-family-section]),
    # which matches no real <section> and collapses nothing; the gate must not accept it as the hide-all.
    _wrongtag_hide_css = ("html[data-family] x[data-family-section]{display:none}\n" + _REVEALS
                          + "{display:block}\n")
    # Fix D: the reveal rules on a wrong tag (x instead of section) re-show nothing real, so every reveal
    # is effectively missing.
    _wrongtag_reveals = ",\n".join(
        'html[data-family="{0}"] x[data-family-section="{0}"]'.format(f) for f in FAMILIES)
    _wrongtag_reveal_css = ("html[data-family] section[data-family-section]{display:none}\n"
                            + _wrongtag_reveals + "{display:block}\n")

    # Each mutation is (label, page, css, js); a mutation of one input keeps the others clean.
    mutations = [
        ("dropped family section",
         clean.replace('<section id="gemini" data-family-section="gemini"><h2>Add AIQT to gemini</h2>'
                       '<p style="margin-top:1rem">steps</p></section>', ""), _CLEAN_CSS, _CLEAN_JS),
        ("default-hidden section",
         clean.replace('<section id="copilot" data-family-section="copilot">',
                       '<section id="copilot" data-family-section="copilot" hidden>'), _CLEAN_CSS, _CLEAN_JS),
        ("inline-style hidden section",
         clean.replace('<section id="copilot" data-family-section="copilot">',
                       '<section id="copilot" data-family-section="copilot" style="display:none">'),
         _CLEAN_CSS, _CLEAN_JS),
        ("picker link wrong href",
         clean.replace('href="#other" data-family-link="other"',
                       'href="#others" data-family-link="other"'), _CLEAN_CSS, _CLEAN_JS),
        ("pre-set data-family on root",
         clean.replace('<html lang="en">', '<html lang="en" data-family="claude">'), _CLEAN_CSS, _CLEAN_JS),
        ("reset not hidden",
         clean.replace('<button type="button" id="family-reset" hidden>Show all</button>',
                       '<button type="button" id="family-reset">Show all</button>'), _CLEAN_CSS, _CLEAN_JS),
        ("missing status region",
         clean.replace('<span role="status" aria-live="polite" id="family-status"></span>', ""),
         _CLEAN_CSS, _CLEAN_JS),
        ("aria-live off is not live",
         clean.replace('aria-live="polite"', 'aria-live="off"'), _CLEAN_CSS, _CLEAN_JS),
        ("missing picker group",
         clean.replace(' id="install-picker"', ""), _CLEAN_CSS, _CLEAN_JS),
        ("hidden picker group",
         clean.replace('<div class="platform-actions" id="install-picker">',
                       '<div class="platform-actions" id="install-picker" hidden>'), _CLEAN_CSS, _CLEAN_JS),
        ("inline handler on a section",
         clean.replace('<section id="claude" data-family-section="claude">',
                       '<section id="claude" data-family-section="claude" onclick="x()">'),
         _CLEAN_CSS, _CLEAN_JS),
        ("inline handler on .platform-actions (id removed)",
         clean.replace(' id="install-picker"', ' onclick="x()"'), _CLEAN_CSS, _CLEAN_JS),
        ("inline script inside the picker",
         clean.replace('<button type="button" id="family-reset" hidden>Show all</button>',
                       '<button type="button" id="family-reset" hidden>Show all</button><script>x()</script>'),
         _CLEAN_CSS, _CLEAN_JS),
        ("install.js no longer external",
         clean.replace('<script src="/js/install.js" defer></script>', ""), _CLEAN_CSS, _CLEAN_JS),
        ("install.js script commented out",
         clean.replace('<script src="/js/install.js" defer></script>',
                       '<!-- <script src="/js/install.js" defer></script> -->'), _CLEAN_CSS, _CLEAN_JS),
        ("install.js non-JS type",
         clean.replace('<script src="/js/install.js" defer></script>',
                       '<script src="/js/install.js" type="application/json"></script>'),
         _CLEAN_CSS, _CLEAN_JS),
        ("install.js suffixed src (.disabled)",
         clean.replace('<script src="/js/install.js" defer></script>',
                       '<script src="/js/install.js.disabled" defer></script>'), _CLEAN_CSS, _CLEAN_JS),
        ("install.js empty", clean, _CLEAN_CSS, "\n"),
        ("install.js stub without logic", clean, _CLEAN_CSS, "// placeholder\n"),
        ("install.js comment-only mentioning the logic tokens", clean, _CLEAN_CSS,
         "// render apply family picker\n/* render apply family */\n"),
        ("collapse rule removed", clean, "body{color:#111}\n", _CLEAN_JS),
        ("collapse guard renamed", clean,
         "html[data-family-mode] section[data-family-section]{display:none}\n" + _REVEALS
         + "{display:block}\n", _CLEAN_JS),
        ("hide-all custom-property fragment", clean, _custom_prop_css, _CLEAN_JS),
        ("hide-all guard negated by :not()", clean, _negated_css, _CLEAN_JS),
        ("hide-all guard and section split across selectors", clean, _split_css, _CLEAN_JS),
        ("hide-all attribute on a wrong tag (not <section>)", clean, _wrongtag_hide_css, _CLEAN_JS),
        ("reveal rules attribute on a wrong tag (not <section>)", clean, _wrongtag_reveal_css, _CLEAN_JS),
        ("all six reveal rules removed", clean, _no_reveals_css, _CLEAN_JS),
    ]
    for label, page, css, js in mutations:
        if not analyze(page, css, js):
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
        (good / JS_REL).write_text(_CLEAN_JS, encoding="utf-8")
        if quiet(good) != 0:
            failures.append("a conformant tree did not pass (exit 0)")

        regressed = tmp / "regressed"
        (regressed / "site" / "js").mkdir(parents=True)
        (regressed / PAGE_REL).write_text(clean.replace('data-family-link="other"',
                                                         'data-family-link="other" aria-current="true"'),
                                          encoding="utf-8")
        (regressed / STYLES_REL).write_text(_CLEAN_CSS, encoding="utf-8")
        (regressed / JS_REL).write_text(_CLEAN_JS, encoding="utf-8")
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
