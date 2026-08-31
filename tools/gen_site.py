#!/usr/bin/env python3
"""Generate site/<name>.html pages from docs/<name>.md sources via the shared themed shell.

Single source of truth for each documentation page: a Markdown file under docs/ with a TOML frontmatter
block (delimited by `+++` fences) carries the page's per-page metadata and content, and this generator
composes it into site/<name>.html by substituting into docs/_shell.html. Every page shares the same
themed head, topbar, sidebar, footer, and theme.js bootstrap; the frontmatter's `sidebar-active` slug
marks the current-page link in the shared sidebar. This lets the docs and the site never diverge and
lets a page migrate from hand-authored HTML to a Markdown source without changing what the site renders.

Pilot scope (OPF-1 Phase 2 first-page milestone): docs/rule1.md -> site/rule1.html, byte-parity round
trip against the pre-migration hand-authored rule1. Later PRs migrate rule2..5, then the non-rule
pages, then the landing page hand-authored escape hatch (frontmatter `hand-authored = true`), then CI
turns on the drift gate for the full site.

Body conversion is a bounded stdlib converter, deliberately NOT a full Markdown implementation: an
external dependency (markdown, mistune) would enter through the project's dependency-provenance gate.
The subset supported today is CommonMark-consistent HTML block pass-through plus enough Markdown
constructs to keep the rule pages' content authorable in prose:
  - HTML blocks: any line that begins at column 0 with `<` starts an HTML block that runs until the
    next blank line; passed through verbatim. This is how the rule pages carry their `<div class="wrap">`
    and `<section id="...">` scaffolding today, so the pilot round-trips byte-exact.
  - ATX headings (# H1, ## H2, ### H3), paragraphs (blank-line-separated blocks of text), and hr
    (a line of exactly `---`). These are enough to migrate simpler pages later without an HTML fallback.
  - Fenced code blocks and inline formatting stay a next-slice concern; the corpus we are migrating in
    this PR does not use them.
See the disclosed residuals in md_to_html; the converter is a small mistake-catcher, not a full parser.

  gen_site.py           regenerate every site/<name>.html that has a docs/<name>.md source
  gen_site.py --check   fail (exit 1) if any target is out of date; exit 2 on a bad source
  gen_site.py --self-test  adversarial corpus over the bounded subset and the fail-closed paths
"""
import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    sys.exit("error: gen_site.py requires Python 3.11+ (tomllib).")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gen_common import repo_root, reconcile  # noqa: E402

# The gensrc registry declaration. Only the pilot page is generated today; later PRs add the other
# docs/*.md -> site/*.html pairs, each as its own FileTarget row, so the registry lists every generated
# page individually. This is consistent with how gen_skill declares its per-artefact outputs.
GENSRC_OUTPUTS = (
    {"target": "site/rule1.html", "kind": "file",
     "sources": ("docs/_shell.html", "docs/rule1.md"),
     "regenerate": "python3 tools/gen_site.py"},
)

DOCS_DIR = "docs"
SHELL_REL = "docs/_shell.html"
FENCE = "+++"

# The frontmatter fields the shell template consumes, mapped to their placeholder tokens. A required
# field missing from a page's frontmatter is a SchemaError (fail-closed exit 2); an unexpected key is
# also a SchemaError so a typo cannot silently drop metadata.
REQUIRED_FIELDS = ("title", "description", "canonical",
                   "og-title", "og-description", "og-url",
                   "sidebar-active")
OPTIONAL_FIELDS = ("hand-authored",)
FIELD_TO_TOKEN = {
    "title": "{{title}}",
    "description": "{{description}}",
    "canonical": "{{canonical}}",
    "og-title": "{{og_title}}",
    "og-description": "{{og_description}}",
    "og-url": "{{og_url}}",
}

# Sidebar active-link map: slug -> the exact href the shell's <a> carries. A frontmatter
# `sidebar-active` naming a slug outside this map is a SchemaError, so a typo cannot silently produce a
# page with no active link. The map covers every link that appears in docs/_shell.html today; extending
# the sidebar is a shell edit plus a matching entry here.
SIDEBAR_SLUG_TO_HREF = {
    "home": "/",
    "install": "/install",
    "install-claude": "/install#claude",
    "install-chatgpt": "/install#chatgpt",
    "install-gemini": "/install#gemini",
    "install-copilot": "/install#copilot",
    "install-other": "/install#other",
    "development": "/development",
    "tech-details": "/tech-details",
    "teams": "/teams",
    "learn": "/learn",
    "standard": "/standard",
    "mappings": "/mappings",
    "examples": "/examples",
    "roadmap": "/roadmap",
    "evidence": "/evidence",
    "rule1": "/rule1",
    "rule2": "/rule2",
    "rule3": "/rule3",
    "rule4": "/rule4",
    "rule5": "/rule5",
    "about": "/about",
    "disclosure": "/disclosure",
}


class SchemaError(Exception):
    """A source frontmatter or body that this generator cannot render; reported at exit 2."""


def split_frontmatter(source_text, source_rel):
    """Split a `+++`-fenced TOML frontmatter from the body of a source .md.

    The source MUST open with a line consisting of exactly `+++` (no trailing whitespace, no leading
    BOM), followed by TOML lines, followed by another `+++` line. Everything after the closing fence
    is the body. Missing fences, an unclosed frontmatter, or a fence not on its own line all raise
    SchemaError (fail-closed exit 2): a source that looks like it has frontmatter but does not close
    it is rejected rather than silently promoted to an all-body file with no metadata.
    """
    lines = source_text.split("\n")
    if not lines or lines[0] != FENCE:
        raise SchemaError("{}: expected frontmatter to open with '{}'".format(source_rel, FENCE))
    for idx in range(1, len(lines)):
        if lines[idx] == FENCE:
            fm_text = "\n".join(lines[1:idx])
            # Body: everything after the closing fence's newline. If the closing fence is followed by a
            # blank line (typical Markdown convention), strip that one leading blank line so the body
            # sits flush.
            body_lines = lines[idx + 1:]
            while body_lines and body_lines[0] == "":
                body_lines.pop(0)
            # Trailing blank lines are stripped so the substitution into `\n\n{{content}}\n\n` in the
            # shell composes to exactly one blank line on each side, not two.
            while body_lines and body_lines[-1] == "":
                body_lines.pop()
            return fm_text, "\n".join(body_lines)
    raise SchemaError("{}: frontmatter was opened but never closed with '{}'".format(source_rel, FENCE))


def parse_frontmatter(fm_text, source_rel):
    """Parse the frontmatter TOML string; validate required and optional keys.

    An unparseable TOML, a missing required key, a wrong-type value, or an unexpected key is a
    SchemaError (fail-closed exit 2), so a typo in a frontmatter key never silently substitutes an
    empty value into the shell.
    """
    try:
        data = tomllib.loads(fm_text)
    except tomllib.TOMLDecodeError as exc:
        raise SchemaError("{}: frontmatter is not valid TOML: {}".format(source_rel, exc))
    known = set(REQUIRED_FIELDS) | set(OPTIONAL_FIELDS)
    unknown = sorted(k for k in data if k not in known)
    if unknown:
        raise SchemaError("{}: frontmatter has unknown key(s): {}".format(source_rel, ", ".join(unknown)))
    for field in REQUIRED_FIELDS:
        if field not in data:
            raise SchemaError("{}: frontmatter is missing required key '{}'".format(source_rel, field))
        if not isinstance(data[field], str):
            raise SchemaError("{}: frontmatter key '{}' must be a string".format(source_rel, field))
    if "hand-authored" in data and not isinstance(data["hand-authored"], bool):
        raise SchemaError("{}: frontmatter key 'hand-authored' must be a bool".format(source_rel))
    slug = data["sidebar-active"]
    if slug and slug not in SIDEBAR_SLUG_TO_HREF:
        raise SchemaError("{}: frontmatter 'sidebar-active' = {!r} is not a known sidebar slug"
                          .format(source_rel, slug))
    return data


# ATX heading: `# ` up to `### `. The count of leading `#` is the heading level. A hash-only line is
# NOT a heading; a trailing space between the hashes and the text is required (CommonMark).
_ATX_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def _escape_text(value):
    """Escape a Markdown paragraph or heading's text for HTML content position. Bounded subset: this
    is the SAFE encoding for text nodes, not a full CommonMark inline transformer. Angle brackets are
    escaped so the reader sees them; ampersand is escaped so `Foo & Bar` becomes `Foo &amp; Bar`.
    """
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def md_to_html(body):
    """Convert the bounded Markdown subset to an HTML content fragment.

    Blocks are separated by blank lines. Each block is either an HTML block (starts at column 0 with
    `<`), an ATX heading (`#` `##` `###`), an hr (`---` on its own), or a paragraph (all other text
    lines). An HTML block is passed through verbatim, preserving every byte so a page authored as raw
    HTML today round-trips byte-exact. Heading/paragraph/hr are converted to `<h*>`, `<p>`, `<hr>`.

    Residuals (disclosed per the pack's disclose-guard-residuals rule): fenced code, inline formatting
    (`*em*`, `**strong**`, `[link](url)`, backticks), lists, blockquotes, and setext headings are
    NOT parsed today. A page that uses them will render literal text through _escape_text rather than
    rich HTML. The rule pages, the pilot's target corpus, do not use them; adding them is the next
    slice of this generator, gated by that page-set landing.
    """
    if body == "":
        return ""
    # A block boundary is one or more consecutive blank lines. Splitting on the empty string preserves
    # the blank line count within a block (there is none by definition; blank lines are the delimiter).
    raw_blocks = re.split(r"\n{2,}", body)
    out_blocks = []
    for block in raw_blocks:
        block = block.rstrip()
        if block == "":
            continue
        # HTML block: any line at column 0 starting with `<` opens an HTML block that runs to the end
        # of this raw block. Content passes through verbatim; a subsequent block boundary (a blank line)
        # ended the block already, so multi-line HTML blocks are supported so long as they carry no
        # embedded blank lines. This is a bounded but useful subset of CommonMark HTML-block behaviour.
        if block.startswith("<"):
            out_blocks.append(block)
            continue
        # hr: a line of exactly `---`.
        if block == "---":
            out_blocks.append("<hr>")
            continue
        # ATX heading.
        match = _ATX_HEADING.match(block)
        if match and "\n" not in block:
            level = len(match.group(1))
            text = _escape_text(match.group(2))
            out_blocks.append("<h{level}>{text}</h{level}>".format(level=level, text=text))
            continue
        # Paragraph: fold internal newlines into single spaces, escape, wrap in <p>.
        text = _escape_text(" ".join(part.strip() for part in block.split("\n")))
        out_blocks.append("<p>{}</p>".format(text))
    return "\n\n".join(out_blocks)


def _mark_active(shell, slug, source_rel):
    """Rewrite the sidebar's `<a class="navlink" href="/<slug>">` link to carry ` active` in its class
    and an `aria-current="page"` attribute, matching how the pre-migration hand-authored rule pages
    marked the current-page link. A shell whose sidebar does not carry the expected pattern raises
    SchemaError, so a mid-shell edit that drops or renames a nav link cannot silently produce a page
    with no active-link marker; the generator surfaces the drift.
    """
    if not slug:
        return shell
    href = SIDEBAR_SLUG_TO_HREF[slug]  # verified in parse_frontmatter
    pattern = '<a class="navlink" href="{}">'.format(href)
    replacement = '<a class="navlink active" href="{}" aria-current="page">'.format(href)
    if pattern not in shell:
        raise SchemaError(
            "{}: cannot mark sidebar-active {!r}: shell has no `{}` link"
            .format(source_rel, slug, pattern))
    return shell.replace(pattern, replacement, 1)


def render_page(shell, source_text, source_rel):
    """Render one page: split frontmatter, convert body, substitute into the shell, mark active nav."""
    fm_text, body = split_frontmatter(source_text, source_rel)
    data = parse_frontmatter(fm_text, source_rel)
    content = md_to_html(body)
    page = shell
    for field, token in FIELD_TO_TOKEN.items():
        page = page.replace(token, data[field])
    page = page.replace("{{content}}", content)
    page = _mark_active(page, data["sidebar-active"], source_rel)
    # A page with a leftover placeholder is a fail-closed error: it would ship a literal `{{foo}}` to
    # the site, which is exactly the silent-drift shape this generator exists to prevent.
    if "{{" in page and "}}" in page:
        raise SchemaError("{}: rendered page still carries a `{{...}}` placeholder".format(source_rel))
    return page


def _discover_sources(root):
    """Enumerate docs/*.md sources (excluding underscore-prefixed private files). Sorted for determinism."""
    docs = root / DOCS_DIR
    if not docs.is_dir():
        return ()
    return tuple(sorted(p for p in docs.glob("*.md")
                        if not p.name.startswith("_") and p.is_file()))


def _read(path):
    """UTF-8 read; fail-closed exit 2 on any read error (matches reconcile's fail-closed shape)."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print("error: cannot read {} ({}); fail-closed".format(path, exc), file=sys.stderr)
        raise SystemExit(2)


def main(argv):
    check = "--check" in argv
    root = repo_root()
    shell_path = root / SHELL_REL
    if not shell_path.is_file():
        print("error: {} not found".format(SHELL_REL))
        return 2
    shell = _read(shell_path)
    sources = _discover_sources(root)
    if not sources:
        # No sources yet is a legitimate clean state (the generator has landed but no page has migrated).
        # This is a bootstrap window measured in one PR, not an open-ended silent-nothing.
        print("PASS: no docs/*.md sources yet; nothing to generate")
        return 0
    drift = False
    for src in sources:
        source_rel = src.relative_to(root).as_posix()
        try:
            page = render_page(shell, _read(src), source_rel)
        except SchemaError as exc:
            print("error: {}".format(exc))
            return 2
        target_rel = "site/{}.html".format(src.stem)
        target = root / target_rel
        if reconcile(target, page, check):
            print("drift: {}".format(target_rel))
            drift = True
    if check and drift:
        print("run tools/gen_site.py to regenerate")
        return 1
    return 0


def _self_test():
    """Adversarial self-test: fail-closed on malformed inputs, correct on the bounded subset.

    Each case is an assertion the pilot's design MUST hold; a regression that breaks one of these
    would ship a silently-wrong page. The self-test builds every input in memory (no filesystem
    fixture) so it always runs.
    """
    failures = []
    # A minimal well-formed shell (no sidebar) for content-substitution cases.
    tiny_shell = ("<!doctype html><html><head><title>{{title}}</title>"
                  "<meta name=\"description\" content=\"{{description}}\">"
                  "<link rel=\"canonical\" href=\"{{canonical}}\">"
                  "<meta property=\"og:title\" content=\"{{og_title}}\">"
                  "<meta property=\"og:description\" content=\"{{og_description}}\">"
                  "<meta property=\"og:url\" content=\"{{og_url}}\">"
                  "</head><body>{{content}}</body></html>")
    frontmatter = (
        '+++\n'
        'title = "T"\n'
        'description = "D"\n'
        'canonical = "https://aiqt.ai/x"\n'
        'og-title = "OT"\n'
        'og-description = "OD"\n'
        'og-url = "https://aiqt.ai/x"\n'
        'sidebar-active = ""\n'
        '+++\n'
        '\n'
    )

    def _expect_error(label, src, needle):
        try:
            render_page(tiny_shell, src, "test.md")
        except SchemaError as exc:
            if needle not in str(exc):
                failures.append("{}: expected error containing {!r}, got {!r}".format(
                    label, needle, str(exc)))
            return
        failures.append("{}: expected SchemaError, got clean render".format(label))

    def _expect_render(label, src, needle):
        try:
            rendered = render_page(tiny_shell, src, "test.md")
        except SchemaError as exc:
            failures.append("{}: expected clean render, got SchemaError: {}".format(label, exc))
            return
        if needle not in rendered:
            failures.append("{}: expected rendered to contain {!r}".format(label, needle))

    # 1. Missing frontmatter fences fail closed.
    _expect_error("no frontmatter open", "hello world\n", "expected frontmatter to open")
    # 2. Unclosed frontmatter fails closed.
    _expect_error("no frontmatter close", "+++\ntitle = \"T\"\nhello\n", "never closed")
    # 3. Malformed TOML fails closed.
    _expect_error("bad toml", "+++\ntitle = not valid\n+++\n", "not valid TOML")
    # 4. Missing required key fails closed.
    _expect_error("missing key", '+++\ntitle = "T"\n+++\n', "missing required key")
    # 5. Unknown key fails closed.
    _expect_error("unknown key", frontmatter.replace('+++\n\n', 'foo = "bar"\n+++\n\n'),
                  "unknown key")
    # 6. Wrong-type key fails closed.
    _expect_error("wrong type", frontmatter.replace('title = "T"\n', 'title = 42\n'),
                  "must be a string")
    # 7. Unknown sidebar-active slug fails closed.
    _expect_error("bad slug", frontmatter.replace('sidebar-active = ""', 'sidebar-active = "nope"'),
                  "not a known sidebar slug")
    # 8. HTML block passes through verbatim (byte-exact).
    _expect_render("html block passthrough",
                   frontmatter + '<div id="x">hello</div>\n',
                   '<body><div id="x">hello</div></body>')
    # 9. ATX heading converts.
    _expect_render("atx h1", frontmatter + '# Hello world\n', '<h1>Hello world</h1>')
    _expect_render("atx h2", frontmatter + '## Two\n', '<h2>Two</h2>')
    _expect_render("atx h3", frontmatter + '### Three\n', '<h3>Three</h3>')
    # 10. Paragraph folds internal newlines and escapes ampersand/lt/gt.
    _expect_render("paragraph escape", frontmatter + 'A & B\n', '<p>A &amp; B</p>')
    _expect_render("paragraph fold", frontmatter + 'One\ntwo\n', '<p>One two</p>')
    # 11. hr converts.
    _expect_render("hr", frontmatter + '---\n', '<hr>')
    # 12. Blank body renders empty content (not an error).
    _expect_render("empty body", frontmatter, '<body></body>')
    # 13. sidebar_active slug marks the shell's link active.
    slug_shell = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body><a class="navlink" href="/rule1">Rule 1</a>{{content}}</body>')
    fm_slug = frontmatter.replace('sidebar-active = ""', 'sidebar-active = "rule1"')
    try:
        rendered = render_page(slug_shell, fm_slug + '<p>ok</p>\n', "test.md")
        needle = '<a class="navlink active" href="/rule1" aria-current="page">Rule 1</a>'
        if needle not in rendered:
            failures.append("sidebar active: expected {!r} in rendered".format(needle))
    except SchemaError as exc:
        failures.append("sidebar active: unexpected SchemaError: {}".format(exc))
    # 14. sidebar_active slug whose href is not in the shell fails closed.
    _expect_error(
        "sidebar link absent",
        frontmatter.replace('sidebar-active = ""', 'sidebar-active = "rule1"'),
        "no `<a class=\"navlink\" href=\"/rule1\">` link")
    # 15. A placeholder that escapes substitution (via source-injected text) fails closed rather than
    # shipping. This catches a future field-add regression that forgets to register the token.
    stray_shell = tiny_shell.replace('<body>{{content}}</body>', '<body>{{content}}{{stray}}</body>')
    try:
        render_page(stray_shell, frontmatter, "test.md")
        failures.append("stray placeholder: expected SchemaError, got clean render")
    except SchemaError as exc:
        if "placeholder" not in str(exc):
            failures.append("stray placeholder: expected error mentioning placeholder, got {!r}"
                            .format(str(exc)))
    if failures:
        print("SELF-TEST FAIL:")
        for line in failures:
            print("  - " + line)
        return 1
    print("PASS: gen_site self-test ({} cases)".format(15))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    sys.exit(main(sys.argv[1:]))
