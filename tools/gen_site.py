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
  - HTML blocks: any line at column 0 starting with `<` starts an HTML block that runs until the next
    blank line, passed through verbatim. Type-1 HTML blocks whose opening tag is `<script>`, `<pre>`,
    `<style>`, or `<textarea>` are an exception (CommonMark §4.6): they continue THROUGH blank lines
    until a line contains the matching close tag, so an embedded blank line does not corrupt them.
    This is how the rule pages carry their `<div class="wrap">` and `<section id="...">` scaffolding
    today, so the pilot round-trips byte-exact.
  - ATX headings (# H1, ## H2, ### H3), paragraphs (blank-line-separated blocks of text), and hr
    (a line of exactly `---`). Enough to migrate simpler pages later without an HTML fallback.
  - Fenced code blocks and inline formatting stay a next-slice concern; the corpus we are migrating in
    this PR does not use them.

Frontmatter tokens are context-escaped for their HTML sink: `title` for text position (inside
`<title>...</title>`) and every other field for quoted-attribute position (inside `content="..."` or
`href="..."`). All substitution is single-pass via _TOKEN_RE so a substituted value is NEVER re-scanned
for further tokens (defeats the cascading-token vector). Body content is inserted at the shell's
single `{{content}}` position AFTER the frontmatter substitution, so a body carrying literal `{{name}}`
in prose is inert.

Residuals (disclosed per `10-ACCUR-disclose-guard-residuals`):
  - Frontmatter escaping covers the text and quoted-attribute HTML sinks the shell places tokens in;
    a future shell that places a token inside a `<script>`, `<style>`, unquoted attribute, or URL
    context would need a fresh sink kind added to FIELD_TO_TOKEN. The URL fields (canonical, og-url)
    are attribute-escaped, NOT URL-scheme-validated; a maintainer-authored `javascript:` value in the
    frontmatter would render literally into an href/content attribute.
  - md_to_html's HTML-block pass-through preserves the block bytes exactly for the type-1 subset and
    for lines beginning with `<`; heading/paragraph/hr blocks have their trailing whole-block
    whitespace normalized before classification, and paragraphs fold internal per-line trailing
    whitespace into single spaces on rewrap.
  - Source and target symlink protection is a pre-check (`is_symlink()` + `resolve()` bounding); a
    committed symlink is refused. The read/write race between the pre-check and the syscall is not
    race-free; a concurrent attacker with write access to the working tree could still swap a path.

  gen_site.py           regenerate every site/<name>.html declared in GENSRC_OUTPUTS
  gen_site.py --check   fail (exit 1) if any target is out of date; exit 2 on a bad source/shell
  gen_site.py --self-test  adversarial corpus over the bounded subset and the fail-closed paths
"""
import html
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
# docs/*.md -> site/*.html pairs, each as its own row, so the registry lists every generated page
# individually. Consistent with how gen_skill declares its per-artefact outputs. This tuple is the
# AUTHORITATIVE list of pages this generator produces: the `--check` path enforces every declared
# source is present as a regular non-symlink file (so a deleted or renamed docs/*.md fails closed
# rather than silently reading as an absent source that trips no check).
GENSRC_OUTPUTS = (
    {"target": "site/rule1.html", "kind": "file",
     "sources": ("docs/_shell.html", "docs/rule1.md"),
     "regenerate": "python3 tools/gen_site.py"},
)

DOCS_DIR = "docs"
SITE_DIR = "site"
SHELL_REL = "docs/_shell.html"
CONTENT_TOKEN = "{{content}}"
FENCE = "+++"

# The frontmatter fields the shell template consumes, mapped to (token, sink). A required field
# missing from a page's frontmatter is a SchemaError (fail-closed exit 2); an unexpected key is also
# a SchemaError so a typo cannot silently drop metadata. The sink names the HTML context the token
# sits in: 'text' is content position (inside `<title>...</title>` or visible text); 'attr' is
# quoted-attribute position (inside `content="..."` or `href="..."`). Each value is HTML-escaped for
# its sink before substitution; the text sink escapes `&<>` and the attr sink additionally escapes
# `"` and `'` so a value cannot escape its surrounding attribute quotes.
REQUIRED_FIELDS = ("title", "description", "canonical",
                   "og-title", "og-description", "og-url",
                   "sidebar-active")
OPTIONAL_FIELDS = ("hand-authored",)
FIELD_TO_TOKEN = {
    "title": ("{{title}}", "text"),
    "description": ("{{description}}", "attr"),
    "canonical": ("{{canonical}}", "attr"),
    "og-title": ("{{og_title}}", "attr"),
    "og-description": ("{{og_description}}", "attr"),
    "og-url": ("{{og_url}}", "attr"),
}

# Sidebar active-link map: slug -> the exact href the shell's `<a>` carries. A frontmatter
# `sidebar-active` naming a slug outside this map is a SchemaError, so a typo cannot silently produce
# a page with no active link. The map covers every INTERNAL page slug the shell's sidebar carries;
# external navlinks (e.g. the shell's `View on GitHub` link) are exempt from this map and reconciled
# separately by _validate_shell_sidebar. Extending the sidebar with a new page is a shell edit plus a
# matching entry here; the shell-load-time reconciliation surfaces the drift both ways.
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


def _escape_field(value, sink):
    """HTML-escape a frontmatter string for its shell sink. `text` is content position (inside a
    `<title>...</title>` or visible text node); `attr` is quoted-attribute position (inside
    `content="..."` or `href="..."`), which additionally escapes `"` and `'` so a value cannot
    escape its surrounding attribute quotes. Unrecognized sinks are a programming error, not a
    runtime input, so they fail loudly rather than default-open."""
    if sink == "text":
        return html.escape(value, quote=False)
    if sink == "attr":
        return html.escape(value, quote=True)
    raise SchemaError("internal: unknown escape sink {!r}".format(sink))


# ATX heading: `# ` up to `### `. The count of leading `#` is the heading level. A hash-only line is
# NOT a heading; a trailing space between the hashes and the text is required (CommonMark).
_ATX_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")

# CommonMark §4.6 type-1 HTML block opener: a line whose first characters at column 0 are one of the
# `<script>`, `<pre>`, `<style>`, or `<textarea>` open-tag forms. The tag name is followed by a
# whitespace character, `>`, or end of line. Once entered, the block continues through blank lines
# until any line contains the matching close tag (case-insensitive).
_TYPE1_OPEN = re.compile(r"^<(script|pre|style|textarea)(?:[\s>]|$)", re.IGNORECASE)

# CommonMark §2.1: a blank line is a line containing no characters, or a line containing only spaces
# (U+0020) or tabs (U+0009). This is stricter than `line == ""` and matches the paragraph-separator
# definition CommonMark uses in §4.9.
_BLANK = re.compile(r"^[ \t]*$")

# Placeholder token: `{{name}}` where `name` is `[\w-]+`. Frontmatter substitution runs a single-pass
# `re.sub` over the shell so a substituted value is never re-scanned for another token.
_TOKEN_RE = re.compile(r"\{\{([\w-]+)\}\}")

# Every `<a class="navlink" ...>` in the shell whose class begins with `navlink` (the shell has none
# with an extra modifier today, but the shell-load-time check tolerates one that renders active
# elsewhere). The href capture group is what the reconciliation compares against SIDEBAR_SLUG_TO_HREF.
_NAVLINK_RE = re.compile(r'<a class="navlink[^"]*" href="([^"]+)"')


def _escape_text(value):
    """Escape a Markdown paragraph or heading's inline text for HTML content position. Bounded subset:
    this is the SAFE encoding for text nodes, not a full CommonMark inline transformer. Angle brackets
    are escaped so the reader sees them; ampersand is escaped so `Foo & Bar` becomes `Foo &amp; Bar`.
    """
    return html.escape(value, quote=False)


def _split_into_blocks(body):
    """Split body into blocks, respecting CommonMark type-1 HTML block continuation through blank
    lines. A type-1 block (`<script>`, `<pre>`, `<style>`, `<textarea>`) at column 0 opens a block
    that runs until a line contains its matching close tag, preserving its internal blank lines
    byte-exact. Every other block is delimited by a blank line (space/tab-only per §2.1).
    """
    lines = body.split("\n")
    blocks = []
    buf = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = _TYPE1_OPEN.match(line)
        if m:
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            tag = m.group(1).lower()
            close_re = re.compile(r"</" + re.escape(tag) + r"\s*>", re.IGNORECASE)
            block_lines = [line]
            if close_re.search(line):
                blocks.append("\n".join(block_lines))
                i += 1
                continue
            i += 1
            while i < n:
                block_lines.append(lines[i])
                if close_re.search(lines[i]):
                    i += 1
                    break
                i += 1
            blocks.append("\n".join(block_lines))
            continue
        if _BLANK.match(line):
            if buf:
                blocks.append("\n".join(buf))
                buf = []
            i += 1
            continue
        buf.append(line)
        i += 1
    if buf:
        blocks.append("\n".join(buf))
    return blocks


def md_to_html(body):
    """Convert the bounded Markdown subset to an HTML content fragment.

    Blocks are separated by blank lines (`[ \\t]*$` per CommonMark §2.1). Each block is either an
    HTML block (starts at column 0 with `<`), an ATX heading (`#` `##` `###`), an hr (`---` on its
    own line), or a paragraph (all other text lines). An HTML block is passed through verbatim,
    preserving every byte so a page authored as raw HTML today round-trips byte-exact. Type-1 HTML
    blocks (script, pre, style, textarea) continue through blank lines until the matching close tag.

    Residuals: fenced code, inline formatting (`*em*`, `**strong**`, `[link](url)`, backticks), lists,
    blockquotes, and setext headings are NOT parsed today. A page that uses them will render literal
    text through _escape_text rather than rich HTML. The rule pages, the pilot's target corpus, do
    not use them; adding them is the next slice of this generator, gated by that page-set landing.
    Non-HTML blocks (heading, hr, paragraph) have their trailing whole-block whitespace stripped
    before classification.
    """
    if body == "":
        return ""
    raw_blocks = _split_into_blocks(body)
    out_blocks = []
    for block in raw_blocks:
        # HTML block: pass through verbatim, preserving internal blank lines and trailing whitespace.
        if block.startswith("<"):
            out_blocks.append(block)
            continue
        # Non-HTML block: normalize whole-block trailing whitespace before classification.
        block = block.rstrip()
        if block == "":
            continue
        if block == "---":
            out_blocks.append("<hr>")
            continue
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
    """Rewrite the sidebar's `<a class="navlink" href="/<slug>">` link to carry ` active` in its
    class and an `aria-current="page"` attribute, matching how the pre-migration hand-authored rule
    pages marked the current-page link. Relies on the shell having been sidebar-reconciled
    (_validate_shell_sidebar) so the pattern is guaranteed present exactly once; the local check
    below is defence-in-depth for a shell reused outside main() (e.g. in the self-test).
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


def _validate_shell_placeholders(shell, shell_rel):
    """Validate that the shell carries each expected placeholder token exactly once and no unknown
    token.

    Expected tokens = every entry in FIELD_TO_TOKEN plus `{{content}}`. A missing token would silently
    omit its content; a duplicate would duplicate the substitution; an unknown token would ship a
    literal `{{foo}}` to the site. All three are SchemaErrors so a shell edit that drops, duplicates,
    or misnames a token surfaces at load time rather than during rendering, and the render step no
    longer needs a post-hoc leftover-placeholder guard (which false-triggered on body prose that
    legitimately carried braces).
    """
    expected = set(t for (t, _) in FIELD_TO_TOKEN.values()) | {CONTENT_TOKEN}
    counts = {}
    for m in _TOKEN_RE.finditer(shell):
        tok = m.group(0)
        counts[tok] = counts.get(tok, 0) + 1
    for token in sorted(expected):
        c = counts.get(token, 0)
        if c == 0:
            raise SchemaError(
                "{}: shell is missing required placeholder {}".format(shell_rel, token))
        if c > 1:
            raise SchemaError(
                "{}: shell carries {} occurrences of {}, expected exactly one"
                .format(shell_rel, c, token))
    for token in sorted(counts):
        if token not in expected:
            raise SchemaError(
                "{}: shell carries unknown placeholder {}".format(shell_rel, token))


def _validate_shell_sidebar(shell, shell_rel):
    """Reconcile the shell's sidebar `<a class="navlink" href="...">` set against SIDEBAR_SLUG_TO_HREF.

    Every mapped href MUST appear exactly once as a navlink (drift in an unselected slug never
    surfaces during rendering otherwise). Every INTERNAL navlink href (starting with `/`) MUST be in
    the map (a shell edit that adds an internal link cannot silently produce a page whose slug list
    fell out of sync). EXTERNAL navlinks (starting with `http://` or `https://`) are exempt: the
    shell's `View on GitHub` link is external and not a page slug.
    """
    hrefs = _NAVLINK_RE.findall(shell)
    counts = {}
    for h in hrefs:
        counts[h] = counts.get(h, 0) + 1
    mapped_hrefs = set(SIDEBAR_SLUG_TO_HREF.values())
    for slug, href in SIDEBAR_SLUG_TO_HREF.items():
        c = counts.get(href, 0)
        if c == 0:
            raise SchemaError(
                "{}: shell has no navlink for mapped slug {!r} (href {})"
                .format(shell_rel, slug, href))
        if c > 1:
            raise SchemaError(
                "{}: shell carries {} navlinks for slug {!r} (href {}), expected exactly one"
                .format(shell_rel, c, slug, href))
    for href in sorted(counts):
        if href.startswith(("http://", "https://")):
            continue
        if href not in mapped_hrefs:
            raise SchemaError(
                "{}: shell has internal navlink {} not in SIDEBAR_SLUG_TO_HREF"
                .format(shell_rel, href))


def render_page(shell, source_text, source_rel):
    """Render one page: split frontmatter, convert body, substitute into the shell, mark active nav.

    The shell is expected to have been validated by _validate_shell_placeholders and
    _validate_shell_sidebar in main(); render_page assumes those checks passed. All substitutions
    (both frontmatter tokens and `{{content}}`) run in ONE `_TOKEN_RE.sub` pass over the shell so a
    substituted value cannot be re-scanned for another token: `re.sub` walks the ORIGINAL shell
    left-to-right and appends each replacement to the output without re-scanning it, so a title of
    `See {{og_title}}` stays literal after substitution, a body carrying `{{name}}` prose is inert,
    and a description value carrying a literal `{{content}}` is not re-inflated by body content.
    """
    fm_text, body = split_frontmatter(source_text, source_rel)
    data = parse_frontmatter(fm_text, source_rel)
    body_html = md_to_html(body)
    # Substitution map keyed by token NAME (the `{{name}}` capture group). Frontmatter values are
    # escaped for the sink their token sits in; `content` inserts the body HTML fragment verbatim
    # (md_to_html has already escaped its inline text). Both go through the same single-pass sub.
    substitutions = {"content": body_html}
    for field, (token, sink) in FIELD_TO_TOKEN.items():
        name = token[2:-2]  # strip `{{` and `}}`
        substitutions[name] = _escape_field(data[field], sink)

    def repl(m):
        name = m.group(1)
        if name in substitutions:
            return substitutions[name]
        # Shell was validated upstream; an unknown token at this point is a bug in the validator.
        raise SchemaError(
            "{}: shell carries unknown placeholder {} at render time"
            .format(source_rel, m.group(0)))

    page = _TOKEN_RE.sub(repl, shell)
    page = _mark_active(page, data["sidebar-active"], source_rel)
    return page


def _bound_under(path, root, label, root_label):
    """Verify `path.resolve()` stays under `root.resolve()`. Raise SchemaError if it escapes. The
    `strict=False` on resolve tolerates a missing final component (the pre-write target), while any
    ancestor symlink is resolved so a symlinked ancestor cannot silently redirect the check."""
    resolved = path.resolve(strict=False)
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise SchemaError(
            "{} {} resolves outside {} (resolved: {})".format(label, path, root_label, resolved))


def _validate_source_path(src, source_rel, docs_root):
    """Reject a declared source that is missing, non-regular, symlinked, or resolves outside docs/.

    A committed symlink under docs/ is refused because reading it would pull an out-of-tree file into
    site/*.html. `..` in the source path is caught by the `_bound_under` check on the resolved path;
    that check runs FIRST so a path that resolves outside docs/ surfaces its escape rather than
    misreading as a missing regular file.
    """
    _bound_under(src, docs_root, "source", DOCS_DIR + "/")
    if src.is_symlink():
        raise SchemaError(
            "declared source {} is a symbolic link; refusing to follow".format(source_rel))
    if not src.exists():
        raise SchemaError("declared source {} is missing".format(source_rel))
    if not src.is_file():
        raise SchemaError("declared source {} is not a regular file".format(source_rel))


def _validate_target_path(target, target_rel, site_root):
    """Reject a target that is a symlink or resolves outside site/. A symlinked target would redirect
    the write outside the pinned site/ root; a target whose resolved path escapes site/ is refused
    even if the direct filename is fine (an ancestor could be symlinked)."""
    if target.is_symlink():
        raise SchemaError(
            "target {} is a symbolic link; refusing to overwrite".format(target_rel))
    _bound_under(target, site_root, "target", SITE_DIR + "/")


def _declared_md_sources():
    """Return the per-entry (docs/*.md source, site/*.html target) declared in GENSRC_OUTPUTS.

    Each entry MUST declare exactly one docs/*.md source; a structural bug in the declaration (zero,
    two, or a non-docs source) fails closed here rather than being resolved by heuristic at run time.
    """
    result = []
    for entry in GENSRC_OUTPUTS:
        md = [s for s in entry["sources"]
              if s.endswith(".md") and s.startswith(DOCS_DIR + "/")]
        if len(md) != 1:
            raise SchemaError(
                "GENSRC_OUTPUTS entry for {} must declare exactly one {}/*.md source, has {}"
                .format(entry["target"], DOCS_DIR, len(md)))
        result.append((md[0], entry["target"]))
    return result


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
    docs_root = root / DOCS_DIR
    site_root = root / SITE_DIR
    shell_path = root / SHELL_REL
    # Reject a root directory that is itself a symlink; the resolved-path bounding rests on the pinned
    # roots being real directories.
    for name, d in ((DOCS_DIR, docs_root), (SITE_DIR, site_root)):
        if d.is_symlink():
            print("error: {}/ is a symbolic link; refusing".format(name), file=sys.stderr)
            return 2
        if not d.is_dir():
            print("error: {}/ is missing or not a directory".format(name), file=sys.stderr)
            return 2
    if shell_path.is_symlink() or not shell_path.is_file():
        print("error: {} is missing, not a regular file, or a symbolic link".format(SHELL_REL),
              file=sys.stderr)
        return 2
    shell = _read(shell_path)
    try:
        _validate_shell_placeholders(shell, SHELL_REL)
        _validate_shell_sidebar(shell, SHELL_REL)
        entries = _declared_md_sources()
    except SchemaError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    # Discovery reconciliation: any docs/*.md not in the declared set is a silent-drop hazard (a
    # maintainer adds docs/rule2.md but forgets to declare it, and the target is never generated).
    declared_sources = {src_rel for src_rel, _ in entries}
    discovered = tuple(sorted(p for p in docs_root.glob("*.md")
                              if not p.name.startswith("_") and not p.is_symlink()))
    for p in discovered:
        rel = p.relative_to(root).as_posix()
        if rel not in declared_sources:
            print("error: discovered docs source {} is not declared in GENSRC_OUTPUTS"
                  .format(rel), file=sys.stderr)
            return 2
    drift = False
    for source_rel, target_rel in entries:
        src = root / source_rel
        target = root / target_rel
        try:
            _validate_source_path(src, source_rel, docs_root)
            _validate_target_path(target, target_rel, site_root)
            page = render_page(shell, _read(src), source_rel)
        except SchemaError as exc:
            print("error: {}".format(exc), file=sys.stderr)
            return 2
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
    would ship a silently-wrong page. Most cases build inputs in memory; the symlink / declared-source
    cases build a small filesystem fixture under a tempdir so no host state is touched.
    """
    import os
    import stat
    import tempfile

    failures = []
    case_count = 0
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
        nonlocal case_count
        case_count += 1
        try:
            render_page(tiny_shell, src, "test.md")
        except SchemaError as exc:
            if needle not in str(exc):
                failures.append("{}: expected error containing {!r}, got {!r}".format(
                    label, needle, str(exc)))
            return
        failures.append("{}: expected SchemaError, got clean render".format(label))

    def _expect_render(label, src, needle):
        nonlocal case_count
        case_count += 1
        try:
            rendered = render_page(tiny_shell, src, "test.md")
        except SchemaError as exc:
            failures.append("{}: expected clean render, got SchemaError: {}".format(label, exc))
            return
        if needle not in rendered:
            failures.append("{}: expected rendered to contain {!r}".format(label, needle))

    def _expect_absent(label, src, needle):
        nonlocal case_count
        case_count += 1
        try:
            rendered = render_page(tiny_shell, src, "test.md")
        except SchemaError as exc:
            failures.append("{}: expected clean render, got SchemaError: {}".format(label, exc))
            return
        if needle in rendered:
            failures.append("{}: expected rendered NOT to contain {!r}".format(label, needle))

    def _expect_raises(label, fn, exc_type, needle):
        nonlocal case_count
        case_count += 1
        try:
            fn()
        except exc_type as exc:
            if needle not in str(exc):
                failures.append("{}: expected {} containing {!r}, got {!r}".format(
                    label, exc_type.__name__, needle, str(exc)))
            return
        except Exception as exc:  # noqa: BLE001
            failures.append("{}: expected {}, got {}: {}".format(
                label, exc_type.__name__, type(exc).__name__, exc))
            return
        failures.append("{}: expected {}, got no exception".format(label, exc_type.__name__))

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
    case_count += 1
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

    # ---- Frontmatter escaping (BLOCKER-1) --------------------------------------------------------
    # 15. Title containing `&<>` is text-escaped in <title>, not injected as markup.
    tricky_title_src = frontmatter.replace('title = "T"', 'title = "A & B <c> D"')
    _expect_render("title text-escape", tricky_title_src, "<title>A &amp; B &lt;c&gt; D</title>")
    # 16. Attribute-context value with `"` is quote-escaped; no attribute breakout.
    breakout = 'D">' + '<script>bad()</script>'
    tricky_desc_src = frontmatter.replace(
        'description = "D"', 'description = "' + breakout.replace('"', '\\"') + '"')
    _expect_absent("description attr-escape", tricky_desc_src, "<script>bad()</script>")
    _expect_render("description attr-escape retained",
                   tricky_desc_src, "&quot;&gt;&lt;script&gt;bad()&lt;/script&gt;")
    # 17. canonical URL with `<>&"` is attribute-escaped (BLOCKER-1 case j).
    tricky_canon_src = frontmatter.replace(
        'canonical = "https://aiqt.ai/x"',
        'canonical = "https://aiqt.ai/x?a=1&b=2\\"><c>"')
    _expect_render("canonical attr-escape",
                   tricky_canon_src,
                   'href="https://aiqt.ai/x?a=1&amp;b=2&quot;&gt;&lt;c&gt;"')

    # ---- Single-pass substitution (BLOCKER-5) ----------------------------------------------------
    # 18. A title containing another field's token is NOT re-scanned; the substituted value renders
    # literally (attribute-escaping the braces so they cannot be re-parsed downstream).
    cascade_src = frontmatter.replace(
        'title = "T"', 'title = "See {{og_title}} here"')
    _expect_render("cascade title literal", cascade_src, "<title>See {{og_title}} here</title>")
    _expect_absent("cascade title no re-scan", cascade_src, "<title>See OT here</title>")
    # 19. A description containing `{{content}}` does NOT swallow the body.
    swallow_src = frontmatter.replace(
        'description = "D"', 'description = "before {{content}} after"') + "BODY-TEXT\n"
    _expect_absent("cascade content no swallow", swallow_src,
                   'content="before BODY-TEXT after"')
    _expect_render("cascade content literal desc", swallow_src,
                   "before {{content}} after")

    # ---- Body braces inert (MAJOR-9) -------------------------------------------------------------
    # 20. Body prose with `{{ prose }}` renders literally; the guard does not false-trigger.
    prose_src = frontmatter + "Use the {{ username }} placeholder here.\n"
    _expect_render("body prose braces",
                   prose_src, "<p>Use the {{ username }} placeholder here.</p>")
    # 21. Body prose with `{{x}}` (tight braces) is inert after shell substitution.
    tight_src = frontmatter + "Literal {{x}} and {{content}} tokens in body.\n"
    _expect_render("body tight braces literal", tight_src,
                   "<p>Literal {{x}} and {{content}} tokens in body.</p>")

    # ---- Shell validation (BLOCKER-2) ----------------------------------------------------------
    # 22. A shell missing `{{content}}` fails closed at load time.
    _expect_raises(
        "shell missing content",
        lambda: _validate_shell_placeholders(
            tiny_shell.replace("{{content}}", ""), "shell.html"),
        SchemaError, "missing required placeholder {{content}}")
    # 23. A shell with duplicated `{{content}}` fails closed at load time.
    _expect_raises(
        "shell duplicated content",
        lambda: _validate_shell_placeholders(
            tiny_shell.replace("<body>{{content}}</body>",
                               "<body>{{content}}{{content}}</body>"),
            "shell.html"),
        SchemaError, "2 occurrences of {{content}}")
    # 24. A shell with an unknown `{{...}}` token fails closed at load time.
    _expect_raises(
        "shell unknown token",
        lambda: _validate_shell_placeholders(
            tiny_shell.replace("<body>{{content}}</body>",
                               "<body>{{content}}{{stray}}</body>"),
            "shell.html"),
        SchemaError, "unknown placeholder {{stray}}")
    # 25. A shell missing `{{title}}` fails closed at load time.
    _expect_raises(
        "shell missing title",
        lambda: _validate_shell_placeholders(
            tiny_shell.replace("<title>{{title}}</title>", "<title></title>"),
            "shell.html"),
        SchemaError, "missing required placeholder {{title}}")

    # ---- CommonMark type-1 blocks (MAJOR-6) ----------------------------------------------------
    # 26. A `<script>` block with an internal blank line is preserved as one block, not split.
    type1_body = "<script>\nconst a = 1;\n\nconst b = 2;\n</script>\n"
    type1_src = frontmatter + type1_body
    _expect_render("type1 script blank-line",
                   type1_src, "<script>\nconst a = 1;\n\nconst b = 2;\n</script>")
    _expect_absent("type1 script not paragraph-split",
                   type1_src, "<p>const b = 2;")
    # 27. `<pre>`, `<style>`, `<textarea>` also continue through blank lines.
    _expect_render("type1 pre blank-line",
                   frontmatter + "<pre>\nline1\n\nline2\n</pre>\n",
                   "<pre>\nline1\n\nline2\n</pre>")
    _expect_render("type1 style blank-line",
                   frontmatter + "<style>\n.a{}\n\n.b{}\n</style>\n",
                   "<style>\n.a{}\n\n.b{}\n</style>")
    _expect_render("type1 textarea blank-line",
                   frontmatter + "<textarea>\nfoo\n\nbar\n</textarea>\n",
                   "<textarea>\nfoo\n\nbar\n</textarea>")

    # ---- Space/tab-only blank lines split paragraphs (MAJOR-7) ---------------------------------
    # 28. `One\n   \nTwo` renders as two paragraphs (space-only blank line).
    space_blank_src = frontmatter + "One\n   \nTwo\n"
    _expect_render("space-only blank splits para 1", space_blank_src, "<p>One</p>")
    _expect_render("space-only blank splits para 2", space_blank_src, "<p>Two</p>")
    _expect_absent("space-only blank NOT one paragraph", space_blank_src, "<p>One  Two</p>")
    # 29. Tab-only blank line splits paragraphs too.
    tab_blank_src = frontmatter + "One\n\t\nTwo\n"
    _expect_render("tab-only blank splits para", tab_blank_src, "<p>Two</p>")

    # ---- Sidebar reconciliation (MAJOR-8) ------------------------------------------------------
    # 30. A shell with duplicated internal navlink (two `/rule1`) fails closed at load time.
    dup_nav_shell = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body>'
        '<a class="navlink" href="/rule1">A</a>'
        '<a class="navlink" href="/rule1">B</a>'
        + ''.join('<a class="navlink" href="{}">L</a>'.format(h)
                  for slug, h in SIDEBAR_SLUG_TO_HREF.items() if slug != "rule1")
        + '{{content}}</body>')
    _expect_raises(
        "shell duplicated navlink",
        lambda: _validate_shell_sidebar(dup_nav_shell, "shell.html"),
        SchemaError, "2 navlinks for slug 'rule1'")
    # 31. A shell missing a mapped navlink fails closed at load time.
    missing_rule2 = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body>'
        + ''.join('<a class="navlink" href="{}">L</a>'.format(h)
                  for slug, h in SIDEBAR_SLUG_TO_HREF.items() if slug != "rule2")
        + '{{content}}</body>')
    _expect_raises(
        "shell missing mapped navlink",
        lambda: _validate_shell_sidebar(missing_rule2, "shell.html"),
        SchemaError, "no navlink for mapped slug 'rule2'")
    # 32. A shell with an unknown INTERNAL navlink fails closed at load time.
    extra_internal = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body>'
        + ''.join('<a class="navlink" href="{}">L</a>'.format(h)
                  for h in SIDEBAR_SLUG_TO_HREF.values())
        + '<a class="navlink" href="/unmapped">X</a>'
        + '{{content}}</body>')
    _expect_raises(
        "shell unmapped internal navlink",
        lambda: _validate_shell_sidebar(extra_internal, "shell.html"),
        SchemaError, "not in SIDEBAR_SLUG_TO_HREF")
    # 33. External navlinks (http://, https://) are exempt from the map reconciliation.
    with_external = tiny_shell.replace(
        '<body>{{content}}</body>',
        '<body>'
        + ''.join('<a class="navlink" href="{}">L</a>'.format(h)
                  for h in SIDEBAR_SLUG_TO_HREF.values())
        + '<a class="navlink" href="https://github.com/x">GH</a>'
        + '{{content}}</body>')
    case_count += 1
    try:
        _validate_shell_sidebar(with_external, "shell.html")
    except SchemaError as exc:
        failures.append("external navlink exempt: unexpected SchemaError: {}".format(exc))

    # ---- Filesystem fail-closed cases (BLOCKER-3, BLOCKER-4) -----------------------------------
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        docs = td / "docs"
        docs.mkdir()
        site = td / "site"
        site.mkdir()
        # 34. A symlinked source is refused.
        good_body = frontmatter + "hello\n"
        (docs / "real.md").write_text(good_body, encoding="utf-8")
        link = docs / "link.md"
        try:
            os.symlink("real.md", link)
            symlink_ok = True
        except (OSError, NotImplementedError):
            symlink_ok = False  # platform without symlink support
        if symlink_ok:
            _expect_raises(
                "symlink source refused",
                lambda: _validate_source_path(link, "docs/link.md", docs),
                SchemaError, "symbolic link")
        # 35. A missing declared source is refused.
        _expect_raises(
            "missing declared source refused",
            lambda: _validate_source_path(docs / "nope.md", "docs/nope.md", docs),
            SchemaError, "missing")
        # 36. A non-regular (directory) source is refused.
        (docs / "adir").mkdir()
        _expect_raises(
            "non-regular source refused",
            lambda: _validate_source_path(docs / "adir", "docs/adir", docs),
            SchemaError, "not a regular file")
        # 37. A symlinked target is refused.
        if symlink_ok:
            outside = td / "victim.html"
            outside.write_text("keep-me\n", encoding="utf-8")
            target_link = site / "rule1.html"
            os.symlink("../victim.html", target_link)
            _expect_raises(
                "symlink target refused",
                lambda: _validate_target_path(target_link, "site/rule1.html", site),
                SchemaError, "symbolic link")
        # 38. A source resolving outside docs/ is refused (via `..`).
        _expect_raises(
            "source escape refused",
            lambda: _validate_source_path(docs / ".." / "site" / "x.md",
                                          "docs/../site/x.md", docs),
            SchemaError, "resolves outside")

    # ---- Preserve rstrip semantics for hr trailing whitespace (MINOR-11 residual) --------------
    # 39. hr with trailing whitespace still renders (documented residual: non-HTML block rstrip).
    _expect_render("hr trailing ws", frontmatter + "---   \n", "<hr>")

    _ = stat  # `stat` imported for potential platform check; retained if the fixture path grows.

    if failures:
        print("SELF-TEST FAIL:")
        for line in failures:
            print("  - " + line)
        return 1
    print("PASS: gen_site self-test ({} cases)".format(case_count))
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(_self_test())
    sys.exit(main(sys.argv[1:]))
