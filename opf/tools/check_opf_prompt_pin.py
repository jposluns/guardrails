#!/usr/bin/env python3
"""OPF implementation-prompt pin drift gate.

opf/spec/OPF-IMPLEMENTATION-PROMPT.md pins the sources it quotes: one commit of
jposluns/guardrails (named in the pin sentence, the raw-URL template and the example URL), a table
of SHA-256 values for the seven pinned files, and the pack facts (format and version) it states for
opf/prompt-pack/pack.toml. The prompt can only pin an earlier commit of this repository, so without
a gate it drifts whenever a pinned source changes without a re-pin. This gate compares bytes, not
commits, so it fails in the same change that edits a pinned source, and it runs in a shallow clone
and in the isolated opf/ copy of the standalone-closure gate.

Checks, each a named guard:
  prompt-read     the prompt is a readable regular file within the size bound (else
                  CANNOT-EVALUATE); every file whose text a check reads (the prompt, each pinned
                  source, and pack.toml for the pack facts) is decoded as strict UTF-8, and one
                  that is not valid UTF-8 is CANNOT-EVALUATE under prompt-decode,
                  pinned-source-decode or pack-manifest-decode, naming the file, never read with
                  its invalid bytes replaced or escaped
  pin-shape       the prompt names at least three 40-lowercase-hex commits, in its raw text and
                  in the visible text of its rendered output alike, and every spelling either way
                  is identical, so a spelling assembled only by rendering (a character reference)
                  and a spelling hidden from rendering (an HTML comment) must both agree with the
                  pin; image alt text counts as visible text in that whole-prompt count although
                  HTML shows it only as an attribute, which can only add spellings that must agree
                  with the pin, never credit a location (the pin section refuses images); a
                  commit-shaped spelling in the pin section that is not all lowercase is refused
                  rather than skipped
  pin-section     the prompt carries the pin section between its two fixed headings, each judged
                  on the vendored CommonMark parse as the level-two ATX heading node, a direct
                  child of the document, whose raw source line is byte-for-byte the fixed text,
                  present exactly once with the head before the tail, so a section hidden inside
                  an HTML comment or any other container, whose bytes survive but which never
                  renders, does not bound the pin checks; and the section, judged on the same
                  parse before any spelling count or table read, carries only Markdown whose
                  source renders as text: raw HTML anywhere in it (a tag, a comment, a processing
                  instruction, a declaration or a CDATA section, block or inline, matched where
                  the vendored parser starts one) is refused by name,
                  and so is every other node whose source carries text a reader never sees (a
                  link reference definition, a link or an image, whose destination, title and
                  alt text render only as attributes or not at all, a fenced code block that
                  carries any info string at all, and any node kind that
                  _pin_section_text_kinds does not name), so every later reading of the section
                  (the location, table and pack-facts reads) sees only text a reader sees; this
                  deliberately over-refuses: an adopter cannot keep an HTML comment, a link, an
                  image or a fenced code block info string (even one bare language name such as
                  text or python) in the pin section, even one that hides nothing; and the
                  section's raw text and its rendered text carry no character other than
                  printable ASCII (U+0020 to U+007E), TAB and LF, refused by presence before the
                  section's own spelling, location, table and pack-facts reads, naming the first
                  such code point and its offset in that text: one rule, so no such read depends
                  on a list of the characters that can hide in or imitate a key, a value or a
                  commit spelling (an invisible character such as a zero-width space, a combining
                  grapheme joiner, a variation selector, a filler letter or a blank braille
                  pattern, a look-alike letter from another script, a fullwidth form); the
                  rendered side catches a character only a character reference builds, the raw
                  side one the parse drops (a non-ASCII space opening a paragraph); this
                  over-refuses every other character in the pin section, even one that hides
                  nothing (an accented letter, a typographic quote or dash)
  pin-location    the pin sentence, the raw-URL template and the example URL each name the pinned
                  commit exactly once, so a movable name (a branch) in one of those places cannot
                  hide behind the spelling count elsewhere, and none can sit where a reader never
                  sees it, because pin-section has refused every such place first
  table-shape     every pipe-led line of the pin section other than the fixed header and the
                  separator is a well-formed table row, and the rows list exactly the seven pinned
                  paths, each once, each digest 64 lowercase hex; a malformed row is a finding,
                  never silently dropped
  table-drift     each table digest equals the SHA-256 of that file's bytes in the checked tree
  pin-history     where the checked tree is a git repository with full history: the pinned commit
                  must be present (absent is CANNOT-EVALUATE) and each pinned blob at that commit
                  must hash to the table value, with replacement objects disabled so a replace ref
                  cannot substitute other bytes for the pinned ones; a shallow repository, or git's
                  own answer that there is no repository, skips this leg (the byte comparison above
                  still binds the table), and any other git failure is CANNOT-EVALUATE, never a
                  silent skip
  pack-facts      the format and version the prompt states match opf/prompt-pack/pack.toml: the
                  pin section states each exactly once in the code-span form the gate reads
                  (`format = "..."`, `version = "..."`), and its raw text and its rendered text
                  alike carry exactly one assignment-shaped spelling of each key (the key,
                  case-insensitively and not preceded by a letter, digit or underscore, then
                  optional whitespace, an ASCII `=`, optional whitespace and an ASCII single or
                  double quote), so a second spelling of that shape in prose, behind an escaped
                  backtick, in a code block, built from a character reference or spelled across a
                  setext underline is refused; the count reads text that pin-section has already
                  held to printable ASCII, TAB and LF, so a spelling that carries any other
                  character, visible or not, in the key, between its parts or in place of one, is
                  refused there first; that count is the only read of a second spelling, so a
                  second, conflicting value in any other ASCII shape is not read, and the gate
                  stays VALID: one with anything other than whitespace between the key, the
                  equals sign and an ASCII quote, or with one of those missing (unquoted, as in
                  version = 9.9.9, backticked, or after a colon), or the fact stated in other
                  words (version 9.9.9)
  quotation       every double-quoted span of MIN_QUOTE_CHARS or more normalized characters whose
                  nearby context (same paragraph, within _QUOTE_CONTEXT_CHARS before the opening
                  quote) attributes it to a pinned source appears word for word,
                  whitespace-insensitively, in one of the seven pinned files; the only allowed
                  non-matches are the named spans in QUOTE_EXEMPTIONS (the prompt quoting its own
                  headings and its own worked examples), and a paragraph with an unpaired double
                  quote is a finding because pairing cannot be trusted there
  required-rule   a fixed list of section 4.2 and section 16.1 sentences appears verbatim
                  (whitespace-insensitively) in both the specification and the prompt; the
                  prompt side compares each sentence, rendered through the same vendored pipeline,
                  with the visible text of the prompt's rendered output less its code-block
                  content and image alt text, so a sentence inside an HTML comment (block or
                  inline), shown only as code or carried only as alt text is not restated, while
                  one split by inline markup or spelled through a character reference is
  prompt-controls the prompt carries no C0 control character other than LF and TAB, and no
                  U+0085, U+2028 or U+2029, anywhere (line models disagree over several of them:
                  CommonMark reads a lone CR as a line ending, marko also reads a form feed as
                  one, Python's splitlines also breaks on the vertical tab, the C0 file, group
                  and record separators (U+001C to U+001E), U+0085, U+2028 and U+2029, and
                  CommonMark substitutes U+FFFD for NUL, so those characters could move a
                  heading, fence, underline or sentence between what renders and what another
                  reading of the same bytes sees; the whole range is refused by presence, and
                  the refusal names the character it finds); it deliberately over-refuses a
                  refused control character whose rendering is harmless
  prompt-parse    the pin legs read the prompt on the vendored CommonMark parse (opf/tools/
                  _vendor/marko, loaded fail-closed with its provenance checked through
                  _commonmark_headings), never on a hand-written line model; visible text is
                  derived from the vendored HTML renderer's own output (_visible_from_html):
                  comments (each ending where the vendored parser ends one, so `<!-->` and
                  `<!--->` are whole comments) drop, the inline tags a, code, em and strong drop
                  to nothing, so a run split by inline markup (a code span's delimiters,
                  emphasis markers, link brackets) reads joined exactly as the rendered output
                  shows it, every other tag (a block tag, a line break, any other raw HTML tag
                  outside the pin section) reads as a line break, so two runs it separates never
                  glue into one spelling, and character references decode exactly once; a parser or
                  renderer that cannot load, raises, or returns a parse that does not cover the
                  prompt is CANNOT-EVALUATE under prompt-parse, never a fallback line reading

  check_opf_prompt_pin.py                    verify the shipped prompt beside this tool (../spec)
  check_opf_prompt_pin.py --self-test        synthetic trees assert each guard's exact status and
                                             name, that each result is stable across two runs, and
                                             that each red fixture covered by one module-level
                                             check turns green when exactly that check is disabled,
                                             that each fixture another rule of the same guard
                                             would also refuse carries the intended rule's refusal
                                             wording, and that each fixture one visible-text
                                             reading decides (a comment, a code block, alt text,
                                             a block tag or an inline tag) changes its result when
                                             that reading is flipped back to a weaker one
  check_opf_prompt_pin.py --list-quotations  re-runnable probe: print every attributed quotation
                                             with its line and status (MATCH, EXEMPT with the
                                             exemption's name, or NO-MATCH) and any unpaired-quote
                                             paragraph; exit 0 only when nothing is NO-MATCH

Exit convention (matches the repo's gates): 0 VALID, 1 INVALID (a finding, such as pin drift),
2 CANNOT-EVALUATE (unreadable input, a missing pinned commit under full history, a vendored
parser that cannot load or parse, a harness error).

The quotation leg checks only spans whose nearby context names a pinned source; a quotation
attributed to a document outside the pin (the git man pages the prompt also quotes) or quoted
without nearby attribution is not checked, and an exact quotation does not prove the prose around
it reads the source correctly. The required-rule leg reads the prompt's visible text, so the
line-break reading of block tags means a required sentence split across two blocks (two
paragraphs, or runs either side of a raw block tag) still counts, and raw HTML outside the pin
section is read with its tags dropped and its text kept, so a sentence inside a script or style
element there still counts although a browser shows nothing of it; its specification side reads
the specification's raw text with whitespace collapsed, so a sentence inside an HTML comment in
the specification still counts there. The history leg authenticates nothing: it reads whatever
object store the checked tree carries, with replacement-object resolution disabled.

Retired and retargeted rules, each case accounted against main at 328660c87cd5:
  - The step 8 journal rule (the former journal-grading guard) is retired. Its cases were the
    heading "Step 8: the store upgrade to 1.3.0" rendered once at the top level, the step's
    extent, raw HTML refused inside the step, exactly one `.working/journals/` list item in it,
    that item's four required clauses (the quoted section 4.2 legacy grading of a store under
    pre-1.3.0 tooling that has not started an adoption, the store-path conclusion limited to such
    a store, what section 4.2 says of a store that has started one, and the upgrade check counting
    every journal entry) and the denied unqualified homes-1 store-path conclusion. Each is
    unreachable: commit 6474ea920464 removed that heading and its journal item from the prompt,
    whose step 8 is now the version and homes refusals with an `opf upgrade` that refuses every
    invocation, and commit 88cc05adc18f removed the pre-1.3.0 legacy-grading sentence from
    section 4.2, which now defines base spec 2.0.0 with homes 2 as the only base version; the
    merged prompt carries no such heading, no such item and no homes-1 wording, and the merged
    specification no such sentence.
    The rule's control-character refusal and its parse refusal judged the whole prompt, which
    survives, so they stay as prompt-controls and prompt-parse.
  - The no-class-bound sentence is retired for its classless-declaration case: commit
    88cc05adc18f removed the declaration class from section 16.1, and the merged prompt says
    section 16.1 defines none. Its malformed case (an absent declaration, or one that omits its
    release identity, `spec_version`, homes generation or worklog storage generation) survives in
    section 16.1 and in the prompt, so it is retargeted as the required rule
    malformed-declaration.
  - The imported-counter-ancestry sentence of section 9.2 was removed by commit 88cc05adc18f, but
    its subject, the value of an imported counter row added where imported ancestry exists,
    survives reworded in section 4.2 and in the prompt, so the rule keeps its name and is
    retargeted to that sentence.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    import os
    try:
        sys.stderr.write(
            "error: check_opf_prompt_pin.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
            "Nothing was run (cannot evaluate).\n"
            % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
        sys.stderr.flush()
    except BaseException:
        pass
    os._exit(2)

import hashlib
import html
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
import unicodedata
from pathlib import Path

# Required for direct execution under python3 -I, including an opf-only copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _opf_store import VALID, INVALID, CANNOT_EVALUATE  # noqa: E402
from _opf_observe import _scrubbed_env  # noqa: E402
# The vendored-marko loader (provenance-checked, fail-closed); marko itself is imported lazily at
# the first parse, so importing this gate never requires the vendored tree to be present.
import _commonmark_headings  # noqa: E402

PROMPT_REL = "spec/OPF-IMPLEMENTATION-PROMPT.md"
PACK_REL = "prompt-pack/pack.toml"
# The seven pinned paths, exactly the rows the prompt's table must carry (repo-root-relative).
TABLE_PATHS = (
    "opf/spec/OPF-SPEC.md",
    "opf/spec/OPF-QUICKSTART.md",
    "opf/spec/OPF-INIT-D2B.md",
    "opf/spec/OPF-INIT-D2B-REVIEW.md",
    "opf/enforcement/ci/opf-ci.sh",
    "opf/enforcement/ci/github-actions.yml",
    "opf/prompt-pack/pack.toml",
)
SPEC_TABLE_PATH = "opf/spec/OPF-SPEC.md"
MAX_FILE_BYTES = 16 * 1024 * 1024
MIN_PIN_OCCURRENCES = 3
# A normalized double-quoted span shorter than this is a token, not a quotation. 16 keeps every
# quotation-shaped span checked while leaving quoted tokens (IDs, verdict words) out.
MIN_QUOTE_CHARS = 16
# How far back, inside one paragraph, the attribution context may sit before an opening quote.
_QUOTE_CONTEXT_CHARS = 250
_COMMIT_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{40}(?![0-9a-fA-F])")
# Any commit-shaped 40-hex run, case-insensitively: inside the pin section, one that is not all
# lowercase is refused rather than skipped, because the lowercase scans above would read past it.
_COMMIT_ANYCASE_RE = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])")
_TABLE_HEADER = "| File | Role | SHA-256 at the pinned commit |"
_TABLE_SEPARATOR_RE = re.compile(r"\|(?:\s*:?-+:?\s*\|)+")
_TABLE_ROW_RE = re.compile(r"\|\s*`([^`|]+)`\s*\|[^|]*\|\s*`([0-9a-f]{64})`\s*\|")
_PIN_SENTENCE_RE = re.compile(r"pinned commit\s+`([0-9a-f]{40})`")
_URL_TEMPLATE_RE = re.compile(r"(?m)^https://\S+?/([0-9a-f]{40})/<path>$")
# A quotation is checked only where its context attributes it to a pinned source; the prompt also
# quotes documents outside the pin (git man pages), which this gate cannot pin.
_SOURCE_CONTEXT_RE = re.compile(
    r"(?i)\bsection\b|OPF-SPEC|QUICKSTART|OPF-INIT-D2B|\bspecification\b|opf-ci\.sh|"
    r"github-actions\.yml|pack\.toml|prompt pack|\brecipe\b|\btemplate\b")
_PIN_SECTION_HEAD = "## Read the specification first"
_PIN_SECTION_TAIL = "## What you must not do"
_GIT_TIMEOUT_S = 60
EXIT_CODES = {VALID: 0, INVALID: 1, CANNOT_EVALUATE: 2}
DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent

# Sentences the prompt must restate word for word (whitespace-insensitively) and the specification
# must still carry; each is quoted from opf/spec/OPF-SPEC.md at the pinned commit (sections 4.2 and
# 16.1). Deleting a restatement from the prompt, or changing the specification so one no longer
# appears there, is a finding that forces a reviewed re-pin.
REQUIRED_RULES = (
    ("one-file",
     "Each release MUST ship that declaration as one file, the declaration file."),
    ("carry-digest-apart",
     "Each release MUST also carry, in the implementation and apart from that file, the SHA-256 "
     "digest of that file's bytes as the release ships them (the shipped-file digest), and the "
     "documentation of each release MUST state where the implementation carries it. A release that "
     "carries no such digest, or whose documentation states no such place, does not conform to "
     "this section, and a conformance claim MUST NOT be made for it."),
    ("expected-report-digests",
     "each of its digests equals the SHA-256 digest of that file's bytes as the release ships "
     "them"),
    ("release-nonconformance",
     "Each such failure is a release nonconformance, never a runtime state of the declaration: it "
     "MUST NOT be treated as making the declaration malformed or contradictory, and it does not "
     "change what a runtime check of an installed copy yields."),
    # The malformed case of the retired no-class-bound sentence, which survives in section 16.1.
    ("malformed-declaration",
     "A declaration that is absent, or that omits its release identity, its `spec_version`, its "
     "homes generation, or its worklog storage generation, is malformed: it MUST yield "
     "cannot-evaluate and MUST NOT authorize any store operation."),
    # The imported counter row's value where imported ancestry exists, now stated in section 4.2.
    ("imported-counter-ancestry",
     "Enabling a further type or module MUST also add its imported counter row in the same act as "
     "its clean index, at zero only where no imported ancestry exists and otherwise at the largest "
     "`<n>` of that namespace's `imported:<NS>-<n>` IDs."),
)

# The named allowed non-matches of the quotation sweep, each an exact normalized span. The prompt
# quotes its own headings when it refers to itself, and it shows worked examples of forms it asks
# the implementer to write; neither is source text, so neither can appear in a pinned file. An
# entry here exempts exactly one span text, so any other non-matching attributed span stays a
# finding.
QUOTE_EXEMPTIONS = (
    ("own-heading-silence", "Where the specification is silent or open"),
    ("own-heading-choices", "Choices to make before you start"),
    ("citation-form-example", "OPF-SPEC.md, section 8.2 (ID namespaces and counters)"),
    ("conformance-statement-example",
     "`conformant_for_declared_scope` (OPFiles base, spec 2.0.0 on homes 2; self-asserted; "
     "profiles: none declared)"),
)


class _Refusal(Exception):
    def __init__(self, status, guard, detail):
        super().__init__(detail)
        self.status = status
        self.guard = guard


def _require(condition, guard, detail, status=INVALID):
    """Each named guard is also the discriminator the self-test asserts."""
    if not condition:
        raise _Refusal(status, guard, detail)


def _normalize(text):
    """One comparison form for quotation and rule matching: runs of whitespace collapse to one
    space, so re-wrapped lines still match, while every other byte stays significant."""
    return " ".join(text.split())


def _close_fd_exc_safe(fd):
    """A failing close never replaces an exception in flight in the CALLING frame, and still
    propagates fail-closed when none is, including under an exception a caller is merely handling.
    One os.close either way: a raising close has released the number (close(2)), which is never
    probed or closed again."""
    tb = sys.exc_info()[2]
    in_flight = tb is not None and tb.tb_frame is sys._getframe(1)
    try:
        os.close(fd)
    except OSError:
        if not in_flight:
            raise


def _read_regular(path, what):
    """Exact bytes of a regular file, bounded, never following a final symlink: the no-follow open
    yields the descriptor, fstat classifies and bounds that same open file (not a path a race could
    swap), and the read is capped at the bound plus one byte, so an oversize file is never read
    whole. The file object is made with closefd=False, so the finally's close is the one close of
    fd on every path."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError as exc:
        raise _Refusal(CANNOT_EVALUATE, what + "-read", "{} unreadable: {}".format(what, exc))
    try:
        st = os.fstat(fd)
        _require(stat.S_ISREG(st.st_mode), what + "-read",
                 "{} {!s} is not a regular file".format(what, path), CANNOT_EVALUATE)
        _require(st.st_size <= MAX_FILE_BYTES, what + "-bound",
                 "{} exceeds the size bound".format(what), CANNOT_EVALUATE)
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    finally:
        _close_fd_exc_safe(fd)
    _require(len(data) <= MAX_FILE_BYTES, what + "-bound",
             "{} exceeds the size bound".format(what), CANNOT_EVALUATE)
    return data


def _read_text(path, what):
    """The text of a regular file read through _read_regular, decoded as strict UTF-8: a file that
    is not valid UTF-8 is CANNOT-EVALUATE under the guard what plus "-decode" (prompt-decode for
    the prompt), naming the file, never read with its invalid bytes replaced or escaped, so bytes
    no UTF-8 reader can decode never reach a check as escaped text it then passes."""
    data = _read_regular(path, what)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _Refusal(CANNOT_EVALUATE, what + "-decode",
                       "{} {!s} is not valid UTF-8: {}".format(what, path, exc))


def _check_pin_shape(raw_commits, visible_commits):
    """At least MIN_PIN_OCCURRENCES 40-hex commit spellings in the raw prompt text and in the
    visible text of its rendered output alike, and every spelling either way identical: a spelling
    assembled only by rendering (a character reference the raw scan cannot see) and a spelling
    hidden from rendering (an HTML comment the visible scan cannot see) must both agree with the
    pin, and a prompt whose rendered output shows fewer than the three structural spellings is
    refused even where its raw bytes still carry them. The visible spellings include image alt
    text, which HTML shows only as an attribute: counting it can only add spellings that must
    agree with the pin, and no location can sit there, because the pin section refuses images."""
    for form, commits in (("raw", raw_commits), ("rendered", visible_commits)):
        _require(len(commits) >= MIN_PIN_OCCURRENCES, "pin-shape",
                 "the prompt's {} text names the pinned commit {} time(s); expected at least "
                 "{}".format(form, len(commits), MIN_PIN_OCCURRENCES))
    spellings = set(raw_commits) | set(visible_commits)
    _require(len(spellings) == 1, "pin-shape",
             "the prompt's pinned-commit spellings disagree: {}".format(sorted(spellings)))


def _check_pin_spelling(section_raw, section_visible):
    """Every commit-shaped 40-hex spelling in the pin section, raw or rendered, is canonical (all
    lowercase): a non-canonical spelling there is refused rather than skipped, because the
    lowercase pin scans would read past it while a reader takes it for a commit name."""
    for form, text in (("raw", section_raw), ("rendered", section_visible)):
        for spelling in _COMMIT_ANYCASE_RE.findall(text):
            _require(spelling == spelling.lower(), "pin-shape",
                     "the pin section's {} text spells a commit non-canonically (not all "
                     "lowercase): {!r}".format(form, spelling))


def _pin_section(marko, document, prompt_text):
    """The pin section as (raw text, parsed top-level nodes), bounded by its two fixed headings
    judged on the parse: each boundary is the level-two ATX heading node, a direct child of the
    document, whose raw source line is byte-for-byte the fixed text, present exactly once, with
    the head before the tail. A boundary spelling inside an HTML comment, a code block or any
    other container never renders as that heading, so a pin section hidden that way reads as
    missing rather than satisfying the pin checks with bytes a reader never sees."""
    children = document.children
    found = {_PIN_SECTION_HEAD: [], _PIN_SECTION_TAIL: []}
    for index, child in enumerate(children):
        if isinstance(child, marko.block.Heading) and _heading_level(child) == 2:
            start, end = _node_span(prompt_text, child)
            raw = prompt_text[start:end]
            if raw.endswith("\n"):
                raw = raw[:-1]
            if raw in found:
                found[raw].append(index)
    _require(len(found[_PIN_SECTION_HEAD]) == 1 and len(found[_PIN_SECTION_TAIL]) == 1
             and found[_PIN_SECTION_HEAD][0] < found[_PIN_SECTION_TAIL][0], "pin-section",
             "the prompt lacks the pin section between {!r} and {!r}: the parse finds {} head "
             "and {} tail heading(s) at the top level".format(
                 _PIN_SECTION_HEAD, _PIN_SECTION_TAIL,
                 len(found[_PIN_SECTION_HEAD]), len(found[_PIN_SECTION_TAIL])))
    head_index = found[_PIN_SECTION_HEAD][0]
    tail_index = found[_PIN_SECTION_TAIL][0]
    raw_start = _node_span(prompt_text, children[head_index])[0]
    raw_end = _node_span(prompt_text, children[tail_index])[0]
    return prompt_text[raw_start:raw_end], children[head_index:tail_index]


def _pin_section_text_kinds(marko):
    """The node kinds of the vendored parse whose source carries no text beyond markup that the
    rendered output does not show: headings, paragraphs, blank lines, lists and their items,
    blockquotes, code blocks, thematic breaks, and inline text, code spans, emphasis, line
    breaks, backslash escapes and autolinks (an autolink shows its own address as link text).
    Every other kind is refused in the pin section, so a kind this tuple does not name, including
    one a later vendored marko adds, fails closed."""
    block, inline = marko.block, marko.inline
    return (block.Heading, block.SetextHeading, block.Paragraph, block.BlankLine, block.List,
            block.ListItem, block.Quote, block.CodeBlock, block.FencedCode, block.ThematicBreak,
            inline.RawText, inline.CodeSpan, inline.Emphasis, inline.StrongEmphasis,
            inline.LineBreak, inline.Literal, inline.AutoLink)


def _check_pin_section_markup(marko, section_nodes):
    """The pin section carries only Markdown whose source renders as text, judged on the vendored
    parse before any spelling count or table read. Raw HTML anywhere in it, block or inline (a
    tag, a comment, a processing instruction, a declaration or a CDATA section, matched exactly
    where the vendored parser starts one), is refused by name: rendered output drops or
    transforms raw HTML, and a browser shows nothing inside a comment, a script or
    a style element. Every other node whose kind _pin_section_text_kinds does not name is refused
    too (a link reference definition, a link or an image, whose destination, title and alt text
    render only as attributes or not at all), and so is a fenced code block that carries any info
    string at all: an info string renders only as a class attribute (its first word) or not at
    all (the rest), and the vendored parser leaves the block's lang and extra both empty exactly
    where the info string is empty (an opener followed only by whitespace has none). The
    location, table and pack-facts reads take the section's raw text, so once this holds every
    byte they can credit is text a reader sees. This over-refuses by design: an HTML comment, a
    link, an image or an info string that hides nothing (a bare language name such as text or
    python) is refused as well, so an adopter cannot keep one in the pin section. A lookalike the
    vendored parser reads as text (an opener inside a code span or after a backslash escape, or a
    bare comment closer in prose) renders as text and is not refused."""
    text_kinds = _pin_section_text_kinds(marko)
    for node in _md_walk(section_nodes):
        _require(not isinstance(node, (marko.block.HTMLBlock, marko.inline.InlineHTML)),
                 "pin-section",
                 "the pin section carries raw HTML, an unsupported form here: rendered output "
                 "drops or transforms raw HTML, so nothing inside it is text a reader sees, while "
                 "the pin checks read the section's raw text")
        _require(type(node) in text_kinds, "pin-section",
                 "the pin section carries a node of kind {}, an unsupported form here: its "
                 "source holds text the rendered output never shows as text (a destination, "
                 "title, alt text or definition), while the pin checks read the section's raw "
                 "text".format(type(node).__name__))
        if isinstance(node, marko.block.FencedCode):
            _require(not node.lang and not node.extra, "pin-section",
                     "the pin section carries a fenced code block with the info string {!r}: a "
                     "fenced code block in the pin section may carry no info string at all, "
                     "because an info string renders only as an attribute or not at all, never "
                     "as text a reader sees, while the pin checks read the section's raw "
                     "text".format((node.lang + " " + node.extra).strip()))


# Any character other than printable ASCII (U+0020 to U+007E), TAB and LF: the pin section may
# carry none, in its raw text or its rendered text.
_PIN_SECTION_REFUSED_CHAR_RE = re.compile("[^\t\n\x20-\x7e]")


def _check_pin_section_chars(section, section_visible):
    """The pin section's raw text and its rendered text carry no character other than printable
    ASCII (U+0020 to U+007E), TAB and LF, refused by presence before the section's own spelling,
    location, table and pack-facts reads, and named with its code point and its offset in that
    text. One rule stands in for a list of the characters that can hide in or imitate a pack-fact
    key or value, or a commit spelling, that a reader reads whole while every scan of the section
    reads past it or reads it as something else: a format character (a zero-width space, a soft
    hyphen, a joiner, a bidirectional control), any other invisible or near-invisible character
    (a combining grapheme joiner, a variation selector, a filler letter, a blank braille pattern),
    a look-alike letter from another script and a fullwidth form are all refused alike, so no
    later read depends on enumerating them. The rendered side catches a character only a
    character reference builds; the raw side catches one the parse drops (a non-ASCII space
    opening a paragraph). The shipped pin section is pure ASCII. This over-refuses by design: an
    accented letter, a typographic quote or dash, or a non-breaking space is refused there too,
    even where it hides nothing."""
    for form, text in (("raw", section), ("rendered", section_visible)):
        match = _PIN_SECTION_REFUSED_CHAR_RE.search(text)
        if match is not None:
            found = match.group(0)
            raise _Refusal(INVALID, "pin-section",
                           "the pin section's {} text carries U+{:04X} ({}) at offset {} of that "
                           "text: the pin section may carry only printable ASCII, TAB and LF, so "
                           "no character a reader cannot see, or may misread, can split or "
                           "imitate a key, a value or a commit spelling".format(
                               form, ord(found), unicodedata.name(found, "unnamed"),
                               match.start()))


def _check_pin_locations(section, pin):
    """The three places that must each name the pinned commit: the pin sentence, the raw-URL
    template and the example URL. Counting identical spellings alone is not enough: one of these
    places could name a branch while the spelling count stays satisfied by text elsewhere. The
    section's raw text is read here only after _check_pin_section_markup has held it to text a
    reader sees, so a location hidden from rendering cannot satisfy this check."""
    sentence = _PIN_SENTENCE_RE.findall(section)
    _require(sentence == [pin], "pin-location",
             "the pin sentence must name the pinned commit exactly once; found {}".format(
                 sentence))
    template = _URL_TEMPLATE_RE.findall(section)
    _require(template == [pin], "pin-location",
             "the raw-URL template must name the pinned commit exactly once; found {}".format(
                 template))
    example = re.findall(r"https://\S+?/([0-9a-f]{40})/" + re.escape(SPEC_TABLE_PATH), section)
    _require(example == [pin], "pin-location",
             "the example URL must name the pinned commit exactly once; found {}".format(example))


def _parse_table(section):
    """The pin table as {path: digest}, parsed structurally: every pipe-led line of the pin
    section other than the fixed header and the separator must be a well-formed row, so a
    malformed row is a finding rather than a line the row pattern silently drops. Like the
    location check, it reads raw text that _check_pin_section_markup has already held to text a
    reader sees, so a row hidden from rendering is refused before it could count."""
    rows = []
    malformed = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        if line == _TABLE_HEADER or _TABLE_SEPARATOR_RE.fullmatch(line):
            continue
        match = _TABLE_ROW_RE.fullmatch(line)
        if match:
            rows.append(match.groups())
        else:
            malformed.append(line)
    _check_table_shape(rows, malformed)
    return dict(rows)


def _check_table_shape(rows, malformed):
    _require(not malformed, "table-shape",
             "the pin section carries {} pipe-led line(s) that are not well-formed table rows, "
             "first: {!r}".format(len(malformed), malformed[0] if malformed else None))
    found = [path for path, _ in rows]
    _require(sorted(found) == sorted(set(found)), "table-shape",
             "a pin-table path is listed twice: {}".format(
                 sorted(set(p for p in found if found.count(p) > 1))))
    _require(set(found) == set(TABLE_PATHS), "table-shape",
             "pin-table paths differ from the pinned set: missing {}, extra {}".format(
                 sorted(set(TABLE_PATHS) - set(found)), sorted(set(found) - set(TABLE_PATHS))))


def _check_drift(root, table):
    """Each table digest equals the SHA-256 of that file's bytes in the checked tree."""
    for rel in TABLE_PATHS:
        actual = hashlib.sha256(_read_regular(root / rel, "pinned-source")).hexdigest()
        _require(actual == table[rel], "table-drift",
                 "{} drifted from the prompt's pin table: pinned {}, actual {}".format(
                     rel, table[rel], actual))


def _git(root, args, env):
    # No detached auto-gc or auto-maintenance may outlive a call and churn .git while a
    # later read or a fixture teardown traverses it. --no-replace-objects: a replace ref would
    # resolve the pinned commit or its blobs to substituted objects, so every read here resolves
    # the real objects only.
    return subprocess.run(["git", "--no-replace-objects", "-C", str(root), "-c", "gc.auto=0",
                           "-c", "gc.autoDetach=false", "-c", "maintenance.auto=false"]
                          + list(args), stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=_GIT_TIMEOUT_S, env=env)


def _check_history(root, pin, table):
    """Where full history is present, the blobs at the pinned commit must match the table. Returns
    a one-line description of what ran, for the VALID detail. Only git's own answer that there is
    no repository skips the leg: any other discovery failure (dubious ownership, a bad config, a
    permission error) is CANNOT-EVALUATE, because treating an error as a skip would silently drop
    the leg on exactly the trees where git misbehaves."""
    if shutil.which("git") is None:
        return "history: skipped (no git)"
    env = _scrubbed_env()
    try:
        inside = _git(root, ["rev-parse", "--is-inside-work-tree"], env)
        if inside.returncode != 0:
            err = inside.stderr.decode(errors="backslashreplace").strip()
            if "not a git repository" in err:
                return "history: skipped (not a git repository)"
            raise _Refusal(CANNOT_EVALUATE, "pin-history",
                           "git repository discovery failed: {}".format(err))
        if inside.stdout.strip() != b"true":
            return "history: skipped (not inside a git work tree)"
        shallow = _git(root, ["rev-parse", "--is-shallow-repository"], env)
        if shallow.returncode != 0:
            raise _Refusal(CANNOT_EVALUATE, "pin-history",
                           "git rev-parse --is-shallow-repository failed: {}".format(
                               shallow.stderr.decode(errors="backslashreplace").strip()))
        if shallow.stdout.strip() == b"true":
            return "history: skipped (shallow repository)"
        present = _git(root, ["cat-file", "-e", pin + "^{commit}"], env)
        _require(present.returncode == 0, "pin-history",
                 "pinned commit {} is absent under full history".format(pin), CANNOT_EVALUATE)
        for rel in TABLE_PATHS:
            shown = _git(root, ["show", "{}:{}".format(pin, rel)], env)
            _require(shown.returncode == 0, "pin-history",
                     "pinned blob {}:{} is unreadable: {}".format(
                         pin, rel, shown.stderr.decode(errors="backslashreplace").strip()),
                     CANNOT_EVALUATE)
            actual = hashlib.sha256(shown.stdout).hexdigest()
            _require(actual == table[rel], "pin-history",
                     "{} at the pinned commit does not match the pin table: pinned {}, "
                     "at {} {}".format(rel, table[rel], pin, actual))
        return "history: verified at {}".format(pin)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _Refusal(CANNOT_EVALUATE, "pin-history", "git could not be run: {}".format(exc))


# The one shape of a pack-fact spelling the gate counts, read in the pin section's raw text and in
# its rendered text, both already held by _check_pin_section_chars to printable ASCII, TAB and LF:
# the key, case-insensitively and not preceded by a letter, digit or underscore (so `spec_version`
# is not the pack version), then optional whitespace, an ASCII `=`, optional whitespace and an
# ASCII single or double quote. The code-span statement the gate reads is one such spelling. A
# spelling that carries any other character, visible or not, never reaches this count. No other
# ASCII shape is read: a spelling with anything other than whitespace between the key, the equals
# sign and the quote, or with one of those missing (unquoted, backticked, after a colon, or a fact
# stated in other words), is not counted, so a second, conflicting value in such a spelling is not
# refused.
_PACK_FACT_SPELLING_RES = tuple(
    (key, re.compile(r"(?i)(?<![A-Za-z0-9_])" + key + r"\s*=\s*[\"']"))
    for key in ("format", "version"))


def _check_pack_facts(root, section, section_visible):
    """The format and version the pin section states match opf/prompt-pack/pack.toml, read from
    the section's raw text after _check_pin_section_markup and _check_pin_section_chars. The
    section must state each exactly once in the code-span form read here, and its raw text and
    its rendered text must each carry exactly one assignment-shaped spelling of each key
    (_PACK_FACT_SPELLING_RES), so a second spelling of that shape outside the code-span form (in
    prose, behind an escaped backtick, in a code block, built from a character reference only the
    rendered text decodes, or spelled across a setext underline only the raw text keeps) is
    refused rather than read past. Both texts reach this count already held to printable ASCII,
    TAB and LF, so a second spelling that carries any other character, visible or not (a
    look-alike letter, an invisible character in the key or between its parts, a fullwidth form),
    was refused by that one rule first. A second value in any other ASCII shape (anything other
    than whitespace between the key, the equals sign and the quote, or one of those missing:
    unquoted, backticked, after a colon, or stated in other words such as version 9.9.9) is not
    read, so it is not refused."""
    stated_fmt = re.findall(r"`format = \"([^\"`]+)\"`", section)
    stated_ver = re.findall(r"`version = \"([^\"`]+)\"`", section)
    _require(len(stated_fmt) == 1 and len(stated_ver) == 1, "pack-facts",
             "the pin section must state the pack format and version exactly once each; found "
             "{} format and {} version statement(s)".format(len(stated_fmt), len(stated_ver)))
    for key, spelling_re in _PACK_FACT_SPELLING_RES:
        for form, text in (("raw", section), ("rendered", section_visible)):
            spelled = len(spelling_re.findall(text))
            _require(spelled == 1, "pack-facts",
                     "the pin section's {} text spells the pack {} as an assignment {} time(s); "
                     "expected exactly one, the code-span statement this gate reads, so a "
                     "second spelling cannot state another value".format(form, key, spelled))
    text = _read_text(root / "opf" / PACK_REL, "pack-manifest")
    try:
        doc = tomllib.loads(text)
    except (ValueError, RecursionError, MemoryError) as exc:
        raise _Refusal(CANNOT_EVALUATE, "pack-manifest-parse",
                       "pack.toml does not parse: {}".format(exc))
    for key, stated in (("format", stated_fmt[0]), ("version", stated_ver[0])):
        actual = doc.get(key)
        _require(stated == actual, "pack-facts",
                 "the prompt states pack {} {!r}; pack.toml carries {!r}".format(
                     key, stated, actual))


def _attributed_quotes(prompt_text):
    """Every double-quoted span the prompt attributes to a pinned source. Double quotes pair up
    sequentially inside each blank-line-separated paragraph. A span joins the result where its
    normalized length is at least MIN_QUOTE_CHARS (shorter spans are tokens, not quotations) and
    the text before its opening quote, in the same paragraph and within _QUOTE_CONTEXT_CHARS,
    names a pinned source. Returns (quotes, unpaired): quotes as (line, normalized span) pairs,
    and unpaired as the starting line of each paragraph whose double-quote count is odd, because
    one unpaired quote shifts every later pairing in that paragraph."""
    quotes = []
    unpaired = []
    offset = 0
    for para in prompt_text.split("\n\n"):
        positions = [i for i, ch in enumerate(para) if ch == '"']
        if len(positions) % 2:
            unpaired.append(prompt_text.count("\n", 0, offset) + 1)
        for k in range(0, len(positions) - 1, 2):
            opening, closing = positions[k], positions[k + 1]
            quote = _normalize(para[opening + 1:closing])
            if len(quote) < MIN_QUOTE_CHARS:
                continue
            context = para[max(0, opening - _QUOTE_CONTEXT_CHARS):opening]
            if not _SOURCE_CONTEXT_RE.search(context):
                continue
            quotes.append((prompt_text.count("\n", 0, offset + opening) + 1, quote))
        offset += len(para) + 2
    return quotes, unpaired


def _strip_comment_leaders(text):
    """The prompt quotes shell and workflow comments as prose, without their leading `#` markers,
    so each source also joins the corpus with those markers stripped per line."""
    return "\n".join(re.sub(r"^\s*#+\s?", "", line) for line in text.splitlines())


def _source_texts(root):
    """Each pinned file's text, keyed by its path, read once through _read_text: the quotation
    corpus and the specification text the required-rule leg reads come from one strict decode of
    the same bytes."""
    return {rel: _read_text(root / rel, "pinned-source") for rel in TABLE_PATHS}


def _sources_norm(source_texts):
    """The normalized quotation corpus: each pinned file, plus a comment-leader-stripped copy."""
    texts = [source_texts[rel] for rel in TABLE_PATHS]
    return "\n".join(
        [_normalize(text) for text in texts]
        + [_normalize(_strip_comment_leaders(text)) for text in texts])


def _check_quotations(prompt_text, sources_norm):
    quotes, unpaired = _attributed_quotes(prompt_text)
    _require(not unpaired, "quotation",
             "a paragraph carries an unpaired double quote (line {}): quote pairing cannot be "
             "trusted there".format(", ".join(str(line) for line in unpaired)))
    exempt = {text: name for name, text in QUOTE_EXEMPTIONS}
    for line, quote in quotes:
        if quote in exempt:
            continue
        _require(quote in sources_norm, "quotation",
                 "the quotation at line {} is attributed to a pinned source but appears in no "
                 "pinned source: {!r}".format(line, quote[:160]))


def _check_required_rules(marko, document, spec_norm):
    """Each required sentence in the specification's raw text and in the prompt's visible text,
    whitespace-insensitively. The prompt side compares what a reader sees on both sides: the
    sentence rendered through the same vendored pipeline (_rule_text) against the prompt's
    credit text (_rule_credit_text), so a sentence inside an HTML comment, block or inline, shown
    only as code or carried only as image alt text is not restated, while one split by inline
    markup or spelled through a character reference is."""
    prompt_visible = _rule_credit_text(marko, document)
    for name, sentence in REQUIRED_RULES:
        _require(_normalize(sentence) in spec_norm, "required-rule",
                 "required rule {!r} is absent from the pinned specification".format(name))
        _require(_rule_text(marko, sentence) in prompt_visible, "required-rule",
                 "required rule {!r} is not restated in the prompt's visible text (outside "
                 "comments, code blocks and image alt text)".format(name))


# Every C0 control character except LF and TAB, plus U+0085, U+2028 and U+2029. Line models
# disagree over several characters in this range: CommonMark reads a lone CR as a line ending,
# marko also reads a form feed as one, Python's splitlines also breaks on the vertical tab, the
# C0 file, group and record separators (U+001C to U+001E), U+0085, U+2028 and U+2029, and
# CommonMark substitutes U+FFFD for NUL, so those characters can move a heading, fence, underline
# or sentence between what one reading of the bytes sees and what another renders; the whole range
# is refused by presence, so the rest of it (the unit separator U+001F among them) over-refuses
# by design rather than depending on any one reading.
_REFUSED_CONTROL_RE = re.compile("[\x00-\x08\x0b-\x1f\x85" + chr(0x2028) + chr(0x2029) + "]")
_REFUSED_CONTROL_NAMES = {
    "\r": "a carriage return (U+000D)",
    "\f": "a form feed (U+000C)",
    "\x0b": "a vertical tab (U+000B)",
    "\x00": "a NUL (U+0000)",
    "\x85": "a next-line control (U+0085)",
    chr(0x2028): "a line separator (U+2028)",
    chr(0x2029): "a paragraph separator (U+2029)",
}


def _check_prompt_controls(prompt_text):
    """Every C0 control character except LF and TAB, plus U+0085, U+2028 and U+2029, is refused
    anywhere in the prompt, and the refusal names the character it finds: line
    models disagree over several characters in this range (CommonMark reads a lone CR as a line
    ending, marko also reads a form feed as one, Python's splitlines also breaks on the vertical
    tab, the C0 file, group and record separators (U+001C to U+001E), U+0085, U+2028 and U+2029,
    and CommonMark substitutes U+FFFD for NUL), so those characters can move a heading, fence,
    underline or sentence between what one reading of the bytes sees and what another renders. The
    refusal is by presence, not by effect, over the whole range. The shipped prompt carries none
    of these characters, so nothing legitimate is refused."""
    match = _REFUSED_CONTROL_RE.search(prompt_text)
    found = match.group(0) if match else "\r"
    _require(match is None, "prompt-controls",
             "the prompt carries {} at offset {}, a refused control character: every C0 control "
             "except LF and TAB, plus U+0085, U+2028 and U+2029, is refused because line models "
             "disagree over characters in that range, so text around one can render away from "
             "what another reading of the same bytes sees".format(
                 _REFUSED_CONTROL_NAMES.get(
                     found, "the C0 control U+{:04X}".format(ord(found))),
                 match.start() if match else -1))


def _prompt_markdown(prompt_text):
    """The vendored CommonMark parse this gate judges the pin section and the pin spellings on:
    the pinned marko under opf/tools/_vendor, loaded and provenance-checked by
    _commonmark_headings (version and import origin, fail-closed), parsed fresh per call with no
    extensions. The renderer modules the visible-text derivation uses (marko.renderer,
    marko.html_renderer) get the same import-origin check as the loader's own submodules, so an
    import hook cannot supply either from outside the vendored tree while the parser loads from
    inside it. The prompt is parsed exactly as read, ahead of the control-character refusal that
    VALID still requires: marko's preprocessing substitutions are length-preserving for
    every character that refusal names except a CRLF pair, and the covering-span requirement
    below refuses a parse that does not cover the text, so every node's source span indexes
    prompt_text and a non-length-preserving input fails closed here rather than mis-slicing. A
    parser that cannot load, raises, or returns a document whose span does not cover the text is
    CANNOT-EVALUATE, never a silent pass or a fallback line reading. No pre-parse size ceiling
    applies here beyond the 16 MiB prompt-read bound: the 1 MiB ceiling the changelog adapter
    enforces before its own parses is not applied to the prompt, and marko's parse is worst-case
    superlinear on crafted input, so this gate bounds what an oversized prompt can pass, never
    how long one takes to parse."""
    try:
        marko = _commonmark_headings._load_marko()
        for module in (getattr(marko, "renderer", None), getattr(marko, "html_renderer", None)):
            _commonmark_headings._verify_module_origin(module, _commonmark_headings._VENDOR_DIR)
        document = _commonmark_headings._new_parser(marko).parse(prompt_text)
    except Exception as exc:
        raise _Refusal(CANNOT_EVALUATE, "prompt-parse",
                       "the vendored CommonMark parser could not parse the prompt: {}".format(exc))
    _require(getattr(document, "source_span", None) == (0, len(prompt_text))
             and isinstance(getattr(document, "children", None), list), "prompt-parse",
             "the parsed document does not cover the prompt text", CANNOT_EVALUATE)
    return marko, document


def _md_walk(nodes):
    """Every element node under nodes, depth first; leaf text (a str in a children slot) is not a
    node. Nesting depth is bounded by the prompt's own block nesting, far under the recursion
    limit."""
    for node in nodes:
        if isinstance(node, str):
            continue
        yield node
        children = getattr(node, "children", None)
        if isinstance(children, list):
            yield from _md_walk(children)


def _node_span(prompt_text, node):
    """The node's source span as (start, end) indices into prompt_text, fail-closed on a missing
    or malformed span rather than a wrong slice."""
    span = getattr(node, "source_span", None)
    _require(isinstance(span, tuple) and len(span) == 2
             and all(isinstance(value, int) for value in span)
             and 0 <= span[0] <= span[1] <= len(prompt_text), "prompt-parse",
             "a parsed node carries no usable source span", CANNOT_EVALUATE)
    return span


def _heading_level(node):
    level = getattr(node, "level", None)
    _require(isinstance(level, int), "prompt-parse",
             "a parsed heading carries no integer level", CANNOT_EVALUATE)
    return level


# The rendered output's own markup, used to derive visible text from the vendored renderer's
# HTML. The renderer escapes every text and attribute character it emits, so no visible "<"
# survives to be read as a tag here; raw HTML it passes through verbatim is refused inside the
# pin section before any scan, and elsewhere (the whole-prompt spelling count and the
# required-rule credit) it is read with its tags dropped and its text kept, comments aside, which
# drop whole. A comment ends exactly where the vendored
# inline parser ends one (opf/tools/_vendor/marko/inline.py: `<!-->` and `<!--->` are whole
# comments, as they are to a browser), so text after either one reads as the visible text it is
# rather than as part of a comment running on to a later closer. Its inline tags (a, code, em,
# strong) wrap text without separating it, so they drop to nothing and a spelling split by inline
# markup reads exactly as rendered; every other tag opens or closes a block (or is a line break),
# so it reads as a line break and adjacent blocks never glue together. The renderer itself writes a
# line break after each block it closes, so the self-test discriminates the line-break reading with
# a raw block tag between two runs inside one paragraph, which nothing else separates.
_HTML_COMMENT_RE = re.compile(r"<!--(?:>|->|.*?-->)", re.DOTALL)
_HTML_PRE_RE = re.compile(r"<pre>.*?</pre>", re.DOTALL)
_HTML_IMG_RE = re.compile(r"<img\b[^>]*>")
_HTML_IMG_ALT_RE = re.compile(r'\balt="([^"]*)"')
_HTML_INLINE_TAG_RE = re.compile(r"</?(?:a|code|em|strong)\b[^>]*>")
_HTML_ANY_TAG_RE = re.compile(r"<[^>]*>")


def _render_html(marko, node):
    """The vendored HTML renderer's output for node, from a fresh renderer per call inside the
    renderer context marko's own Markdown.render enters. A renderer that raises or returns a
    non-string is CANNOT-EVALUATE, never a silent empty rendering."""
    try:
        renderer = marko.html_renderer.HTMLRenderer()
        with renderer:
            rendered = renderer.render(node)
    except Exception as exc:
        raise _Refusal(CANNOT_EVALUATE, "prompt-parse",
                       "the vendored renderer could not render the prompt: {}".format(exc))
    _require(isinstance(rendered, str), "prompt-parse",
             "the vendored renderer returned a non-string rendering", CANNOT_EVALUATE)
    return rendered


def _visible_from_html(rendered, code_blocks=True, image_alt=True):
    """The text a reader of the rendered output sees, derived from the vendored renderer's own
    HTML with the standard library: comments drop (nothing inside one renders), each ending where
    the vendored parser ends one; code-block content stays only where code_blocks keeps it (it
    renders literally, but text shown only as code is not the prompt stating a rule, so
    required-rule credit excludes it); an image contributes its alt text only where image_alt
    keeps it (HTML shows alt only as an attribute, so counting it in the spelling count can only
    add spellings that must agree with the pin, while required-rule credit excludes it);
    inline tags drop to nothing; every other tag reads as a line break; and character references
    decode exactly once with html.unescape, after tag removal, so a reference the renderer left
    encoded reads as the character a reader sees while text the renderer escaped (a code span's
    literal ampersand run, say) reads as its own spelling."""
    text = _HTML_COMMENT_RE.sub("\n", rendered)
    if not code_blocks:
        text = _HTML_PRE_RE.sub("\n", text)
    if image_alt:
        text = _HTML_IMG_RE.sub(
            lambda tag: "\n" + " ".join(_HTML_IMG_ALT_RE.findall(tag.group(0))) + "\n", text)
    else:
        text = _HTML_IMG_RE.sub("\n", text)
    text = _HTML_INLINE_TAG_RE.sub("", text)
    text = _HTML_ANY_TAG_RE.sub("\n", text)
    return html.unescape(text)


def _rendered_text(marko, node, code_blocks=True, image_alt=True):
    """The visible text of one parsed node, through the renderer and _visible_from_html."""
    return _visible_from_html(_render_html(marko, node),
                              code_blocks=code_blocks, image_alt=image_alt)


def _rule_credit_text(marko, document):
    """The normalized text that can restate a required rule: the prompt's visible text less its
    code-block content and image alt text."""
    return _normalize(_rendered_text(marko, document, code_blocks=False, image_alt=False))


def _rule_text(marko, sentence):
    """The normalized visible form of a required sentence: parsed and rendered through the same
    vendored pipeline as the prompt, so both sides of the comparison carry one form (a code span's
    backticks drop on both sides, say, and the comparison is between what two rendered outputs
    show, never between markup spellings)."""
    try:
        document = _commonmark_headings._new_parser(marko).parse(sentence)
    except Exception as exc:
        raise _Refusal(CANNOT_EVALUATE, "prompt-parse",
                       "the vendored parser could not parse a required sentence: {}".format(exc))
    return _rule_credit_text(marko, document)


def _validate(root):
    """The vendored parse and rendering back the pin legs. They run ahead of the
    control-character refusal, which VALID still requires, and they stay sound ahead of it:
    marko's preprocessing is length-preserving for
    every refused character except a CRLF pair, and a prompt whose parse does not cover its text
    is CANNOT-EVALUATE at prompt-parse, never mis-sliced. The pin section's markup rule runs
    before any spelling count or table read, so every later reading of the section's raw text
    sees only text a reader sees, and its character rule (printable ASCII, TAB and LF only) runs
    before the section's own spelling, location, table and pack-facts reads."""
    root = Path(root)
    prompt_text = _read_text(root / "opf" / PROMPT_REL, "prompt")
    marko, document = _prompt_markdown(prompt_text)
    section, section_nodes = _pin_section(marko, document, prompt_text)
    _check_pin_section_markup(marko, section_nodes)
    raw_commits = _COMMIT_RE.findall(prompt_text)
    visible_commits = _COMMIT_RE.findall(_rendered_text(marko, document))
    _check_pin_shape(raw_commits, visible_commits)
    pin = raw_commits[0] if raw_commits else None
    section_visible = "\n".join(_rendered_text(marko, node) for node in section_nodes)
    _check_pin_section_chars(section, section_visible)
    _check_pin_spelling(section, section_visible)
    _check_pin_locations(section, pin)
    table = _parse_table(section)
    _check_drift(root, table)
    history = _check_history(root, pin, table) if pin else "history: skipped (no pin found)"
    _check_pack_facts(root, section, section_visible)
    source_texts = _source_texts(root)
    sources_norm = _sources_norm(source_texts)
    spec_norm = _normalize(source_texts[SPEC_TABLE_PATH])
    _check_quotations(prompt_text, sources_norm)
    _check_required_rules(marko, document, spec_norm)
    _check_prompt_controls(prompt_text)
    return "pin {}, {} table row(s), {}".format(pin, len(table), history)


def validate_tree(root):
    """(status, guard, detail) for the tree at root. Reads only; writes nothing."""
    try:
        detail = _validate(root)
    except _Refusal as refusal:
        return refusal.status, refusal.guard, str(refusal)
    return VALID, None, detail


def list_quotations(root=DEFAULT_ROOT):
    """Re-runnable review probe: print every attributed quotation with its line and status, plus
    any unpaired-quote paragraph. 0 nothing is NO-MATCH, 1 at least one NO-MATCH or unpaired
    paragraph, 2 an input cannot be read."""
    try:
        root = Path(root)
        prompt_text = _read_text(root / "opf" / PROMPT_REL, "prompt")
        sources = _sources_norm(_source_texts(root))
    except _Refusal as refusal:
        print("check_opf_prompt_pin: {} [{}] {}".format(
            refusal.status, refusal.guard, refusal), file=sys.stderr)
        return EXIT_CODES[refusal.status]
    quotes, unpaired = _attributed_quotes(prompt_text)
    exempt = {text: name for name, text in QUOTE_EXEMPTIONS}
    findings = 0
    for line in unpaired:
        print("UNPAIRED line {}: this paragraph's double quotes do not pair up".format(line))
        findings += 1
    for line, quote in quotes:
        if quote in exempt:
            print("EXEMPT({}) line {}: {}".format(exempt[quote], line, quote[:120]))
        elif quote in sources:
            print("MATCH line {}: {}".format(line, quote[:80]))
        else:
            print("NO-MATCH line {}: {}".format(line, quote[:160]))
            findings += 1
    print("{} attributed quotation(s), {} finding(s), floor {} characters, {} named "
          "exemption(s)".format(len(quotes), findings, MIN_QUOTE_CHARS, len(QUOTE_EXEMPTIONS)))
    return 1 if findings else 0


# ---- self-test ------------------------------------------------------------------------------------


_FIXTURE_SOURCES = {
    "opf/spec/OPF-QUICKSTART.md": "# Quickstart\n\nA short orientation; the specification governs.\n",
    "opf/spec/OPF-INIT-D2B.md": "# Init contract\n\nThe coupled initialization contract.\n",
    "opf/spec/OPF-INIT-D2B-REVIEW.md": "# Review register\n\nThe review register for that contract.\n",
    "opf/enforcement/ci/opf-ci.sh": "#!/bin/sh\n# The reference CI recipe. Views regenerate cleanly.\n",
    "opf/enforcement/ci/github-actions.yml": "name: opf\n# The reference CI workflow template.\n",
}
_FIXTURE_PACK = ('format = "opf.prompt-pack/v1"\nversion = "0.5.1"\n'
                 'digest = "sha256:' + "0" * 64 + '"\nmembers = []\n')
_FIXTURE_PIN = "c" * 40
_FIXTURE_LATER_HEAD = "### Later steps"


def _fixture_spec(drop_rule=None):
    rules = "\n\n".join(sentence for name, sentence in REQUIRED_RULES if name != drop_rule)
    return ("# Specification\n\n" + rules +
            "\n\nViews regenerate cleanly. The emitter writes canonical bytes for every view it "
            "renders.\n")


def _fixture_prompt(root, pin=_FIXTURE_PIN,
                    quote="The emitter writes canonical bytes for every view it renders.",
                    pack_version="0.5.1", drop_rule=None, digest_overrides=None, extra_text=None):
    digests = {}
    for rel in TABLE_PATHS:
        digests[rel] = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    if digest_overrides:
        digests.update(digest_overrides)
    table = "\n".join("| `{}` | pinned | `{}` |".format(rel, digests[rel]) for rel in TABLE_PATHS)
    rules = []
    for name, sentence in REQUIRED_RULES:
        if name != drop_rule:
            rules.append('The specification says "{}"'.format(sentence))
    return (_PIN_SECTION_HEAD + "\n\nRead these files at the pinned commit `" + pin +
            "` of the repository.\n\n" + _TABLE_HEADER + "\n|---|---|---|\n" +
            table + "\n\nEach raw URL names the commit:\n\n```\nhttps://example.invalid/" + pin +
            "/<path>\n```\n\nFor example, https://example.invalid/" + pin + "/opf/spec/OPF-SPEC.md.\n\n" +
            'The pack manifest (`format = "opf.prompt-pack/v1"`, `version = "' + pack_version +
            '"`) lists no members.\n\n' + _PIN_SECTION_TAIL + "\n\n" +
            'The specification says "' + quote + '"\n\n' + "\n\n".join(rules) + "\n\n" +
            _FIXTURE_LATER_HEAD + "\n" + ("\n" + extra_text + "\n" if extra_text else ""))


def _build_tree(base, pack_text=_FIXTURE_PACK, source_overrides=None, spec_drop_rule=None,
                **prompt_kwargs):
    root = Path(base)
    sources = dict(_FIXTURE_SOURCES)
    if source_overrides:
        sources.update(source_overrides)
    for rel, text in sources.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / SPEC_TABLE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / SPEC_TABLE_PATH).write_text(_fixture_spec(spec_drop_rule), encoding="utf-8")
    (root / "opf" / PACK_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / "opf" / PACK_REL).write_text(pack_text, encoding="utf-8")
    prompt = root / "opf" / PROMPT_REL
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(_fixture_prompt(root, **prompt_kwargs), encoding="utf-8")
    return root


def _spec_edit(root):
    """One byte, in a sentence no fixture quotation and no required rule carries, so only the
    byte comparisons can catch it."""
    spec = root / SPEC_TABLE_PATH
    spec.write_text(spec.read_text(encoding="utf-8").replace(
        "regenerate cleanly", "regenerate Cleanly", 1), encoding="utf-8")


def _edit_prompt(root, old, new):
    """One textual fixture edit; the old text must be present, so a stale fixture fails loudly."""
    prompt = root / "opf" / PROMPT_REL
    text = prompt.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError("fixture edit target not found: {!r}".format(old[:80]))
    prompt.write_text(text.replace(old, new, 1), encoding="utf-8")


def _vectors():
    """(name, expected status, expected guard, covering check, builder(base)). The covering check
    is the module function whose removal must turn the fixture green; None where the refusal has
    no single module-level check to disable (a read refusal, or a parse shape the later checks
    cannot survive without)."""
    bad = "d" * 64
    spare_pin = "The pinned commit `" + _FIXTURE_PIN + "` is named once more here."

    def valid(base):
        _build_tree(base)

    def spec_edited(base):
        _spec_edit(_build_tree(base))

    def digest_edited(base):
        _build_tree(base, digest_overrides={SPEC_TABLE_PATH: bad})

    def pins_disagree(base):
        # The three structural locations stay intact; a fourth spelling disagrees, so only the
        # identical-spellings comparison can catch it.
        _build_tree(base, extra_text="A stray spelling `" + "e" * 40 + "` also appears here.")

    def pack_bumped(base):
        # The prompt's stated version drifts from pack.toml; the pack bytes themselves stay
        # pinned, so only the pack-facts comparison can catch it.
        _build_tree(base, pack_version="0.6.0")

    def rule_deleted(base):
        _build_tree(base, drop_rule="one-file")

    def quote_reworded(base):
        _build_tree(base, quote="The emitter writes non-canonical bytes for every view it renders.")

    def quote_at_floor(base):
        # Exactly MIN_QUOTE_CHARS: the shortest span the quotation leg still checks.
        _build_tree(base, quote="z" * MIN_QUOTE_CHARS)

    def quote_below_floor(base):
        # One character under the floor: a token, never a quotation, so it is not checked.
        _build_tree(base, quote="z" * (MIN_QUOTE_CHARS - 1))

    def quote_no_introducer(base):
        # Attributed by nearby context alone, with no verb introducing it: still checked.
        _build_tree(base, extra_text='Open point 1 notes that section 3 of the specification '
                    'covers rendering ("The emitter renders stale views at homes nine").')

    def quote_short_stale(base):
        # Nineteen normalized characters: above the floor, below the old 40-character cut.
        _build_tree(base, extra_text='Section 2 says "Stores erase weekly" about retention.')

    def quote_unpaired(base):
        _build_tree(base, extra_text='Section 6 text has a stray " character in this paragraph.')

    def quote_exempt(base):
        # A named exemption: the prompt quoting one of its own headings is not source text.
        _build_tree(base, extra_text='The specification heading convention mirrors "Choices to '
                    'make before you start" in this prompt.')

    def quote_unattributed(base):
        # No pinned source named nearby, so the sweep does not check the span.
        _build_tree(base, extra_text='Your working notes could read "no store writes happened '
                    'during this probing run today".')

    def prompt_absent(base):
        root = _build_tree(base)
        (root / "opf" / PROMPT_REL).unlink()

    def table_row_malformed(base):
        # The malformed row sits inside the table block; it must be a finding, not a dropped line.
        root = _build_tree(base)
        _edit_prompt(root, "\n\nEach raw URL names the commit:",
                     "\n| `opf/spec/OPF-SPEC.md` | pinned | `INVALID-DIGEST` |"
                     "\n\nEach raw URL names the commit:")

    def table_row_duplicate(base):
        root = _build_tree(base)
        text = (root / "opf" / PROMPT_REL).read_text(encoding="utf-8")
        row = re.search(r"(?m)^\| `{}` \| pinned \| `[0-9a-f]{{64}}` \|$".format(
            re.escape(SPEC_TABLE_PATH)), text).group(0)
        _edit_prompt(root, row, row + "\n" + row)

    def table_row_missing(base):
        root = _build_tree(base)
        text = (root / "opf" / PROMPT_REL).read_text(encoding="utf-8")
        row = re.search(r"(?m)^\| `opf/prompt-pack/pack\.toml` \| pinned \| `[0-9a-f]{64}` \|$",
                        text).group(0)
        _edit_prompt(root, row + "\n", "")

    def section_missing(base):
        root = _build_tree(base)
        _edit_prompt(root, _PIN_SECTION_HEAD, "## Read these files first")

    def sentence_missing(base):
        root = _build_tree(base, extra_text=spare_pin)
        _edit_prompt(root, "at the pinned commit `" + _FIXTURE_PIN + "` of the repository.",
                     "at the commit named below.")

    def template_names_branch(base):
        root = _build_tree(base, extra_text=spare_pin)
        _edit_prompt(root, "https://example.invalid/" + _FIXTURE_PIN + "/<path>",
                     "https://example.invalid/main/<path>")

    def example_url_missing(base):
        root = _build_tree(base, extra_text=spare_pin)
        _edit_prompt(root, "For example, https://example.invalid/" + _FIXTURE_PIN +
                     "/opf/spec/OPF-SPEC.md.\n\n", "")

    def source_missing(base):
        root = _build_tree(base)
        (root / "opf/spec/OPF-QUICKSTART.md").unlink()

    def source_symlink(base):
        root = _build_tree(base)
        quickstart = root / "opf/spec/OPF-QUICKSTART.md"
        target = root / "quickstart-real.md"
        target.write_text(quickstart.read_text(encoding="utf-8"), encoding="utf-8")
        quickstart.unlink()
        quickstart.symlink_to(target)

    def source_at_bound(base):
        # Exactly MAX_FILE_BYTES: the largest pinned source the gate still reads.
        text = _FIXTURE_SOURCES["opf/spec/OPF-QUICKSTART.md"]
        _build_tree(base, source_overrides={
            "opf/spec/OPF-QUICKSTART.md": text + "z" * (MAX_FILE_BYTES - len(text))})

    def source_oversize(base):
        # One byte over the bound: refused before the bytes are read whole.
        text = _FIXTURE_SOURCES["opf/spec/OPF-QUICKSTART.md"]
        _build_tree(base, source_overrides={
            "opf/spec/OPF-QUICKSTART.md": text + "z" * (MAX_FILE_BYTES - len(text) + 1)})

    def pack_unparseable(base):
        _build_tree(base, pack_text='format = "opf.prompt-pack/v1"\nmembers = [\n')

    def rule_deleted_named(name):
        # The named required rule dropped from the prompt, the specification unchanged.
        def build(base):
            _build_tree(base, drop_rule=name)
        return build

    def rule_absent_from_spec(base):
        # The specification no longer carries a required rule, while the prompt still states it as
        # plain prose rather than as a quotation, so the quotation leg has nothing to compare and
        # only the specification side of the required-rule leg can catch it.
        _build_tree(base, spec_drop_rule="malformed-declaration", drop_rule="malformed-declaration",
                    extra_text=dict(REQUIRED_RULES)["malformed-declaration"])

    def control_in_prompt(char):
        # The given character inside a paragraph after the pin section, where no other check
        # reads anything of it: the control-character refusal is by presence, not by effect, so a
        # refused character there is caught by that refusal alone, and a character just outside
        # the refused ranges passes.
        def build(base):
            _build_tree(base, extra_text="A note line." + char + "Its continuation.")
        return build

    def section_in_html_comment(base):
        # The whole pin section inside an HTML comment: its bytes survive, but nothing of it
        # renders, so the parse finds no boundary headings and the section reads as missing.
        root = _build_tree(base)
        _edit_prompt(root, _PIN_SECTION_HEAD, "<!--\n" + _PIN_SECTION_HEAD)
        _edit_prompt(root, _PIN_SECTION_TAIL, "-->\n\n" + _PIN_SECTION_TAIL)

    def section_head_decoy_in_fence(base):
        # The head heading's text inside a fenced code block elsewhere: literal code text, never
        # a heading node, so the real section still bounds the pin checks.
        _build_tree(base, extra_text="```text\n" + _PIN_SECTION_HEAD + "\n```")

    def stale_in_section(text):
        # The given sentence inserted inside the pin section, before the pack-facts sentence.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, "\n\nThe pack manifest",
                         "\n\n" + text + "\n\nThe pack manifest")
        return build

    # A stale commit whose first character is a numeric reference: the raw scan sees only 39
    # characters, while the rendered output shows a full 40-hex spelling that disagrees with the
    # pin, so only the rendered side of the spelling comparison refuses it.
    stale_commit_entity = stale_in_section(
        "Older notes name the commit &#100;" + "d" * 39 + " here.")

    # A stale commit in uppercase hex: no lowercase scan reads it, so the canonical-spelling rule
    # alone refuses it.
    stale_commit_uppercase = stale_in_section(
        "Older notes name the commit " + "D" * 40 + " here.")

    # Both at once: the raw section never carries 40 contiguous hex characters, so only the
    # rendered side of the canonical-spelling rule refuses it.
    stale_commit_entity_uppercase = stale_in_section(
        "Older notes name the commit &#68;" + "D" * 39 + " here.")

    def uppercase_commit_outside_section(base):
        # The same non-canonical spelling outside the pin section: the canonical-spelling rule
        # binds the pin section alone, and no lowercase scan reads an uppercase spelling.
        _build_tree(base, extra_text="Historic note: " + "D" * 40 + " was a draft id.")

    def example_url_in_html_comment(base):
        # The example URL wrapped in an HTML comment: its bytes would still satisfy the raw
        # location scan, so the pin section's markup rule refuses the comment before any count;
        # with that rule disabled the rendered side of the spelling count still refuses it (only
        # two pin spellings render), so no single check covers it.
        root = _build_tree(base)
        _edit_prompt(root, "For example, https://example.invalid/" + _FIXTURE_PIN +
                     "/opf/spec/OPF-SPEC.md.",
                     "<!-- note > For example, https://example.invalid/" + _FIXTURE_PIN +
                     "/opf/spec/OPF-SPEC.md. -->")

    example_url = "https://example.invalid/" + _FIXTURE_PIN + "/opf/spec/OPF-SPEC.md"
    example_sentence = "For example, " + example_url + "."

    def example_replaced(new, extra_text=None):
        # The example URL sentence replaced by the given text; extra_text (a spare visible pin
        # spelling) keeps the rendered spelling count at three where the replacement hides the
        # example, so only a rule that refuses the hiding form itself can catch it.
        def build(base):
            root = _build_tree(base, extra_text=extra_text)
            _edit_prompt(root, example_sentence, new)
        return build

    def section_body_in_element(tag):
        # The whole pin section body inside the given raw HTML element: a browser shows nothing
        # inside a script or style element, while a reading that drops tags and keeps their text
        # still finds every location and row, so only the pin section's markup rule refuses it.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, _PIN_SECTION_HEAD + "\n\n",
                         _PIN_SECTION_HEAD + "\n\n<" + tag + ">\n")
            _edit_prompt(root, "\n\n" + _PIN_SECTION_TAIL,
                         "\n</" + tag + ">\n\n" + _PIN_SECTION_TAIL)
        return build

    def table_wrapped(opener, closer):
        # The whole pin table, header and separator included, between the given opener and
        # closer lines: every row would still satisfy the raw table read, so only the pin
        # section's markup rule refuses it.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, _TABLE_HEADER, opener + "\n" + _TABLE_HEADER)
            _edit_prompt(root, "\n\nEach raw URL names the commit:",
                         "\n" + closer + "\n\nEach raw URL names the commit:")
        return build

    def section_comment_harmless(base):
        # An inline HTML comment in the pin section that hides nothing: refused by the pin
        # section's markup rule alone, so this fixture discriminates that rule's over-refusal.
        root = _build_tree(base)
        _edit_prompt(root, "` of the repository.",
                     "` of the repository. <!-- a note that hides nothing -->")

    def pack_facts_in_fence_info(base):
        # The pack facts moved into a tilde fence's info string, which renders nothing of them,
        # while the visible sentence states another version, each `=` written as a character
        # reference: the raw text then carries one assignment-shaped spelling of each key (the
        # hidden one) and the rendered text one (the visible one), so the raw pack-facts read
        # and both spelling counts would still pass, and only the pin section's markup rule
        # refuses it.
        root = _build_tree(base)
        _edit_prompt(root, '(`format = "opf.prompt-pack/v1"`, `version = "0.5.1"`)',
                     '(format &#61; "opf.prompt-pack/v1", version &#61; "0.6.0")')
        _edit_prompt(root, "\n\nThe pack manifest",
                     '\n\n~~~text `format = "opf.prompt-pack/v1"` `version = "0.5.1"`\nx\n~~~'
                     "\n\nThe pack manifest")

    def html_lookalikes_render_as_text(base):
        # A comment opener inside a code span, one after a backslash escape, and a bare comment
        # closer in prose: the vendored parser starts no raw HTML at any of them, they render as
        # text a reader sees, and the pin section's markup rule does not refuse them.
        root = _build_tree(base)
        _edit_prompt(root, "` of the repository.",
                     "` of the repository; `<!--` in a code span, \\<!-- after an escape and a "
                     "bare --> closer all render as text.")

    # A stale commit after an empty comment `<!-->` inside the pin section: the vendored parser
    # ends that comment at once, so the rendered output shows the entity-built stale spelling,
    # which a comment pattern running on to the later closer would hide from the visible scan.
    # The markup rule refuses the comments first; with it disabled, the rendered side of the
    # spelling comparison still refuses the stale spelling, so no single check covers it.
    stale_after_empty_comment = stale_in_section(
        "Older notes name the commit <!--> &#100;" + "d" * 39 + " here. <!-- -->")

    def stale_after_comment_outside_section(opener):
        # The same stale spelling after the given empty comment outside the pin section, where
        # raw HTML is read rather than refused: only the comment rule of _visible_from_html,
        # ending the comment where the vendored parser does, lets the rendered side of the
        # spelling comparison see and refuse it.
        def build(base):
            _build_tree(base, extra_text="Older notes name the commit " + opener + " &#100;" +
                        "d" * 39 + " here. <!-- -->")
        return build

    def stale_inside_comment_outside_section(base):
        # An entity-built stale spelling inside a whole comment outside the pin section, after
        # an inner ">": nothing of it renders and the raw text never spells 40 hex characters,
        # so it passes; a reading that ended the comment at the inner ">" (the generic tag rule)
        # would expose the spelling, so this fixture discriminates the comment rule's span.
        _build_tree(base, extra_text="Older notes <!-- note > &#100;" + "d" * 39 + " --> end.")

    def stale_in_image_alt_outside_section(base):
        # An entity-built stale spelling in image alt text outside the pin section: HTML shows alt
        # text only as an attribute, but the whole-prompt rendered count reads it, so the rendered
        # side of the spelling comparison refuses it; a count that left alt text out would pass
        # it.
        _build_tree(base, extra_text="![Older notes name &#100;" + "d" * 39 + "](x.png)")

    def rule_hidden(name, wrap):
        # The named required rule dropped from the prompt's prose and carried only in the form
        # wrap gives it after the pin section: its raw bytes stay in the prompt, so a raw reading
        # would still credit it, while the visible reading does not.
        def build(base):
            _build_tree(base, drop_rule=name, extra_text=wrap(dict(REQUIRED_RULES)[name]))
        return build

    def stale_runs_outside_section(separator, closer=""):
        # Two 20-character runs of a stale commit joined by the given separator, the second
        # followed by closer, after the pin section: the raw text never spells 40 hex characters,
        # so only the rendered reading of the markup decides whether the visible text spells a
        # stale commit.
        def build(base):
            _build_tree(base, extra_text="Older notes name " + "d" * 20 + separator + "d" * 20
                        + closer + " here.")
        return build

    def format_char_outside_section(base):
        # A word joiner (U+2060) just outside the pin section: the character rule binds the pin
        # section alone.
        _build_tree(base, extra_text="A plain" + chr(0x2060) + " note sits here.")

    def fence_in_section(opener):
        # A fenced code block opened by the given line, holding a line that hides nothing, inside
        # the pin section before the pack-facts sentence; the closer is the opener's fence run.
        return stale_in_section(opener + "\nA plain line.\n" + opener[:3])

    def prompt_invalid_utf8(base):
        # A line carrying the byte 0xff, which is not valid UTF-8, appended to the end of an
        # otherwise valid prompt: read with that byte escaped, every pin, quotation, rule and
        # control check still passes, so only the strict decode refuses it.
        prompt = _build_tree(base) / "opf" / PROMPT_REL
        prompt.write_bytes(prompt.read_bytes() + b"\nA note \xff here.\n")

    def source_invalid_utf8(rel):
        # A comment line carrying the byte 0xff appended to the given pinned file, with the prompt
        # rewritten afterwards so its table pins the new bytes: read with that byte escaped, the
        # file still passes every check that reads it, so only the strict decode refuses it.
        def build(base):
            root = _build_tree(base)
            path = root / rel
            path.write_bytes(path.read_bytes() + b"# A note \xff here.\n")
            (root / "opf" / PROMPT_REL).write_text(_fixture_prompt(root), encoding="utf-8")
        return build

    return [
        ("valid-tree", VALID, None, None, valid),
        ("spec-one-byte-edit", INVALID, "table-drift", "_check_drift", spec_edited),
        ("table-digest-edited", INVALID, "table-drift", "_check_drift", digest_edited),
        ("pin-occurrences-disagree", INVALID, "pin-shape", "_check_pin_shape", pins_disagree),
        ("pack-version-bumped", INVALID, "pack-facts", "_check_pack_facts", pack_bumped),
        ("required-rule-deleted", INVALID, "required-rule", "_check_required_rules", rule_deleted),
        ("quotation-reworded", INVALID, "quotation", "_check_quotations", quote_reworded),
        ("quotation-at-floor", INVALID, "quotation", "_check_quotations", quote_at_floor),
        ("quotation-below-floor", VALID, None, None, quote_below_floor),
        ("quotation-attributed-no-introducer", INVALID, "quotation", "_check_quotations",
         quote_no_introducer),
        ("quotation-short-stale", INVALID, "quotation", "_check_quotations", quote_short_stale),
        ("quotation-unpaired", INVALID, "quotation", "_check_quotations", quote_unpaired),
        ("quotation-exempt-span", VALID, None, None, quote_exempt),
        ("quotation-unattributed-span", VALID, None, None, quote_unattributed),
        ("prompt-absent", CANNOT_EVALUATE, "prompt-read", None, prompt_absent),
        ("table-row-malformed", INVALID, "table-shape", "_check_table_shape",
         table_row_malformed),
        ("table-row-duplicate", INVALID, "table-shape", "_check_table_shape",
         table_row_duplicate),
        ("table-row-missing", INVALID, "table-shape", None, table_row_missing),
        ("pin-section-missing", INVALID, "pin-section", None, section_missing),
        ("pin-sentence-missing", INVALID, "pin-location", "_check_pin_locations",
         sentence_missing),
        ("url-template-names-branch", INVALID, "pin-location", "_check_pin_locations",
         template_names_branch),
        ("example-url-missing", INVALID, "pin-location", "_check_pin_locations",
         example_url_missing),
        ("pinned-source-missing", CANNOT_EVALUATE, "pinned-source-read", None, source_missing),
        ("pinned-source-symlink", CANNOT_EVALUATE, "pinned-source-read", None, source_symlink),
        ("pinned-source-at-bound", VALID, None, None, source_at_bound),
        ("pinned-source-oversize", CANNOT_EVALUATE, "pinned-source-bound", None, source_oversize),
        ("pack-manifest-unparseable", CANNOT_EVALUATE, "pack-manifest-parse",
         "_check_pack_facts", pack_unparseable),
        ("required-rule-malformed-declaration-deleted", INVALID, "required-rule",
         "_check_required_rules", rule_deleted_named("malformed-declaration")),
        ("required-rule-imported-counter-ancestry-deleted", INVALID, "required-rule",
         "_check_required_rules", rule_deleted_named("imported-counter-ancestry")),
        ("required-rule-absent-from-spec", INVALID, "required-rule", "_check_required_rules",
         rule_absent_from_spec),
        # Each named refused character: a lone CR (CommonMark reads it as a line ending), a form
        # feed (marko does too), the vertical tab, the C0 file separator (U+001C), U+0085, U+2028
        # and U+2029 (Python's splitlines breaks on each), and NUL (CommonMark substitutes U+FFFD).
        ("prompt-control-cr", INVALID, "prompt-controls", "_check_prompt_controls",
         control_in_prompt("\r")),
        ("prompt-control-form-feed", INVALID, "prompt-controls", "_check_prompt_controls",
         control_in_prompt("\f")),
        ("prompt-control-vertical-tab", INVALID, "prompt-controls", "_check_prompt_controls",
         control_in_prompt("\x0b")),
        ("prompt-control-nul", INVALID, "prompt-controls", "_check_prompt_controls",
         control_in_prompt("\x00")),
        ("prompt-control-file-separator", INVALID, "prompt-controls", "_check_prompt_controls",
         control_in_prompt("\x1c")),
        ("prompt-control-next-line", INVALID, "prompt-controls", "_check_prompt_controls",
         control_in_prompt("\x85")),
        ("prompt-control-line-separator", INVALID, "prompt-controls", "_check_prompt_controls",
         control_in_prompt(chr(0x2028))),
        ("prompt-control-paragraph-separator", INVALID, "prompt-controls",
         "_check_prompt_controls", control_in_prompt(chr(0x2029))),
        # The ends of the refused ranges, each just inside, at and just outside: U+0008 and U+000B
        # refused around the admitted TAB (U+0009) and LF; U+001F refused below the admitted
        # space; DEL (U+007F) and U+0084 admitted below the refused U+0085, and U+0086 above it;
        # U+2027 and U+202A admitted around the refused U+2028 and U+2029.
        ("prompt-control-backspace", INVALID, "prompt-controls", "_check_prompt_controls",
         control_in_prompt("\x08")),
        ("prompt-tab-admitted", VALID, None, None, control_in_prompt("\t")),
        ("prompt-control-unit-separator", INVALID, "prompt-controls", "_check_prompt_controls",
         control_in_prompt("\x1f")),
        ("prompt-delete-admitted", VALID, None, None, control_in_prompt("\x7f")),
        ("prompt-u0084-admitted", VALID, None, None, control_in_prompt("\x84")),
        ("prompt-u0086-admitted", VALID, None, None, control_in_prompt("\x86")),
        ("prompt-u2027-admitted", VALID, None, None, control_in_prompt(chr(0x2027))),
        ("prompt-u202a-admitted", VALID, None, None, control_in_prompt(chr(0x202A))),
        ("pin-section-in-html-comment", INVALID, "pin-section", None, section_in_html_comment),
        ("pin-section-head-decoy-in-fence", VALID, None, None, section_head_decoy_in_fence),
        ("pin-stale-commit-entity-built", INVALID, "pin-shape", "_check_pin_shape",
         stale_commit_entity),
        ("pin-stale-commit-uppercase", INVALID, "pin-shape", "_check_pin_spelling",
         stale_commit_uppercase),
        ("pin-stale-commit-entity-built-uppercase", INVALID, "pin-shape",
         "_check_pin_spelling", stale_commit_entity_uppercase),
        ("pin-uppercase-commit-outside-section", VALID, None, None,
         uppercase_commit_outside_section),
        ("pin-example-url-in-html-comment", INVALID, "pin-section", None,
         example_url_in_html_comment),
        ("pin-section-body-in-script", INVALID, "pin-section", "_check_pin_section_markup",
         section_body_in_element("script")),
        ("pin-section-body-in-style", INVALID, "pin-section", "_check_pin_section_markup",
         section_body_in_element("style")),
        ("pin-example-url-in-script", INVALID, "pin-section", "_check_pin_section_markup",
         example_replaced("<script>\n" + example_sentence + "\n</script>")),
        ("pin-example-url-in-html-comment-spare-mention", INVALID, "pin-section",
         "_check_pin_section_markup",
         example_replaced("<!-- " + example_sentence + " -->", extra_text=spare_pin)),
        ("pin-table-in-html-comment", INVALID, "pin-section", "_check_pin_section_markup",
         table_wrapped("<!--", "-->")),
        ("pin-section-comment-harmless", INVALID, "pin-section", "_check_pin_section_markup",
         section_comment_harmless),
        ("pin-stale-commit-after-empty-comment", INVALID, "pin-section", None,
         stale_after_empty_comment),
        ("pin-stale-commit-after-empty-comment-outside-section", INVALID, "pin-shape",
         "_check_pin_shape", stale_after_comment_outside_section("<!-->")),
        ("pin-stale-commit-after-dash-empty-comment-outside-section", INVALID, "pin-shape",
         "_check_pin_shape", stale_after_comment_outside_section("<!--->")),
        ("pin-stale-commit-inside-comment-outside-section", VALID, None, None,
         stale_inside_comment_outside_section),
        ("pin-example-url-in-link-reference-definition", INVALID, "pin-section",
         "_check_pin_section_markup",
         example_replaced("[e]: " + example_url, extra_text=spare_pin)),
        ("pin-example-url-in-link-destination", INVALID, "pin-section",
         "_check_pin_section_markup",
         example_replaced("For example, [the specification](" + example_url + ").",
                          extra_text=spare_pin)),
        ("pin-example-url-in-image-alt", INVALID, "pin-section", "_check_pin_section_markup",
         example_replaced("For example, ![" + example_url + "](x.png).")),
        ("pin-example-url-in-fence-info-string", INVALID, "pin-section",
         "_check_pin_section_markup",
         example_replaced("```" + example_url + "\nx\n```", extra_text=spare_pin)),
        ("pin-table-in-link-title", INVALID, "pin-section", "_check_pin_section_markup",
         table_wrapped('[t]: /u\n"', '"')),
        ("pin-pack-facts-in-fence-info-string", INVALID, "pin-section",
         "_check_pin_section_markup", pack_facts_in_fence_info),
        ("pin-example-url-autolink", VALID, None, None,
         example_replaced("For example, <" + example_url + ">.")),
        ("pin-html-lookalikes-render-as-text", VALID, None, None, html_lookalikes_render_as_text),
        ("prompt-invalid-utf8", CANNOT_EVALUATE, "prompt-decode", None, prompt_invalid_utf8),
        ("pinned-source-invalid-utf8", CANNOT_EVALUATE, "pinned-source-decode", None,
         source_invalid_utf8("opf/spec/OPF-QUICKSTART.md")),
        ("pinned-spec-invalid-utf8", CANNOT_EVALUATE, "pinned-source-decode", None,
         source_invalid_utf8(SPEC_TABLE_PATH)),
        ("pack-manifest-invalid-utf8", CANNOT_EVALUATE, "pack-manifest-decode", None,
         source_invalid_utf8("opf/" + PACK_REL)),
        ("pin-fence-info-empty", VALID, None, None, fence_in_section("~~~")),
        ("pin-fence-info-whitespace-only", VALID, None, None, fence_in_section("```   ")),
        ("pin-fence-info-one-character", INVALID, "pin-section", "_check_pin_section_markup",
         fence_in_section("```x")),
        ("pin-fence-info-python", INVALID, "pin-section", "_check_pin_section_markup",
         fence_in_section("```python")),
        ("pin-fence-info-c-sharp", INVALID, "pin-section", "_check_pin_section_markup",
         fence_in_section("```c#")),
        ("pin-fence-info-commit", INVALID, "pin-section", "_check_pin_section_markup",
         fence_in_section("~~~" + _FIXTURE_PIN)),
        ("pin-stale-commit-entity-in-image-alt-outside-section", INVALID, "pin-shape",
         "_check_pin_shape", stale_in_image_alt_outside_section),
        # A raw block tag pair between two runs inside one paragraph: it reads as a line break,
        # so the runs never glue into a stale spelling (the renderer separates its own blocks
        # with a line break already, so only raw block tags inside a paragraph discriminate it).
        ("pin-stale-runs-split-by-block-tag-outside-section", VALID, None, None,
         stale_runs_outside_section("</p><p>")),
        # Emphasis around the second run: inline tags drop to nothing, so the visible text spells
        # the stale commit whole and the rendered side of the spelling comparison refuses it.
        ("pin-stale-runs-joined-by-emphasis-outside-section", INVALID, "pin-shape",
         "_check_pin_shape", stale_runs_outside_section("*", "*")),
        # A required sentence present only where a reader never sees it as stated text: inside
        # an HTML comment (a block one, and an inline one inside a paragraph), shown only as code
        # in a fenced code block, or carried only as image alt text.
        ("required-rule-in-block-comment", INVALID, "required-rule", "_check_required_rules",
         rule_hidden("malformed-declaration", lambda text: "<!--\n" + text + "\n-->")),
        ("required-rule-in-inline-comment", INVALID, "required-rule", "_check_required_rules",
         rule_hidden("imported-counter-ancestry",
                     lambda text: "A note <!-- " + text + " --> ends here.")),
        ("required-rule-in-code-block", INVALID, "required-rule", "_check_required_rules",
         rule_hidden("one-file", lambda text: "```\n" + text + "\n```")),
        ("required-rule-in-image-alt", INVALID, "required-rule", "_check_required_rules",
         rule_hidden("one-file", lambda text: "![" + text + "](x.png)")),
        # A required sentence split by inline emphasis: the visible text states it whole, so it
        # is restated, which a raw reading would miss.
        ("required-rule-split-by-emphasis", VALID, None, None,
         rule_hidden("one-file", lambda text: text.replace("that declaration",
                                                           "*that declaration*", 1))),
        # The stated residuals: a required sentence split across two paragraphs, and one inside
        # a script element outside the pin section, each still counts as restated.
        ("required-rule-split-across-paragraphs-residual", VALID, None, None,
         rule_hidden("one-file",
                     lambda text: text.replace(" as one file,", "\n\nas one file,", 1))),
        ("required-rule-in-script-element-residual", VALID, None, None,
         rule_hidden("one-file", lambda text: "<script>\n" + text + "\n</script>")),
        ("pin-pack-version-second-spelling-prose", INVALID, "pack-facts", "_check_pack_facts",
         stale_in_section('Older notes say version = "9.9.9" here.')),
        ("pin-pack-version-second-spelling-escaped-backticks", INVALID, "pack-facts",
         "_check_pack_facts", stale_in_section('Older notes say \\`version = "9.9.9"\\` here.')),
        ("pin-pack-version-second-spelling-entity", INVALID, "pack-facts", "_check_pack_facts",
         stale_in_section('Older notes say version &#61; "9.9.9" here.')),
        ("pin-pack-version-second-spelling-single-quoted", INVALID, "pack-facts",
         "_check_pack_facts", stale_in_section("Older notes say `version = '9.9.9'` here.")),
        ("pin-pack-format-second-spelling-prose", INVALID, "pack-facts", "_check_pack_facts",
         stale_in_section('Older notes say format = "x/v2" here.')),
        # A setext underline turns the `=` into heading markup, so the rendered text shows no
        # assignment while the raw source still spells one across lines: only the raw count
        # refuses it.
        ("pin-pack-version-second-spelling-setext-underline", INVALID, "pack-facts",
         "_check_pack_facts", stale_in_section('Older notes name the version\n=\n\n"9.9.9" was '
                                               "its value.")),
        ("pin-pack-spec-version-spelling", VALID, None, None,
         stale_in_section('The store declares `spec_version = "1.3.0"` here.')),
        # A fullwidth equals sign (U+FF1D), which a reader takes for `=`: the assignment count
        # reads only an ASCII equals sign, so only the character rule refuses it.
        ("pin-pack-version-second-spelling-fullwidth-equals", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say version " + chr(0xFF1D) + ' "9.9.9" here.')),
        # A zero-width space (U+200B) between the key and the equals sign: no pattern reads the
        # key across it, so only the character rule refuses it.
        ("pin-pack-version-second-spelling-zero-width-space", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say version" + chr(0x200B) + ' = "9.9.9" here.')),
        # A soft hyphen built from a character reference inside the key: the raw text is pure
        # ASCII, so only the rendered side of the character rule refuses it.
        ("pin-pack-version-second-spelling-soft-hyphen-entity", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section('Older notes say ver&shy;sion = "9.9.9" here.')),
        # A combining grapheme joiner (U+034F) built from a character reference after the key:
        # the raw text is pure ASCII, so only the rendered side of the character rule refuses it.
        ("pin-pack-version-second-value-cgj-entity-after-key", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section('Older notes say version&#x34F; = "9.9.9" here.')),
        # An ideographic space (U+3000) opening a paragraph: the parse drops it, so the rendered
        # text carries none and only the raw side of the character rule refuses it.
        ("pin-non-ascii-space-opening-paragraph", INVALID, "pin-section",
         "_check_pin_section_chars", stale_in_section(chr(0x3000) + "A plain note sits here.")),
        # A word joiner (U+2060) that splits nothing a scan reads: refused by presence, so this
        # fixture holds the rule's over-refusal.
        ("pin-format-char-harmless", INVALID, "pin-section", "_check_pin_section_chars",
         stale_in_section("A plain" + chr(0x2060) + " note sits here.")),
        # A visible accented letter (U+00E9) that hides nothing: refused all the same, because
        # the rule admits printable ASCII, TAB and LF and nothing else.
        ("pin-non-ascii-letter-harmless", INVALID, "pin-section", "_check_pin_section_chars",
         stale_in_section("A caf" + chr(0x00E9) + " note sits here.")),
        ("pin-format-char-outside-section", VALID, None, None, format_char_outside_section),
        # An unquoted second value: the assignment count needs an ASCII quote after the equals
        # sign, so this conflicting version is not read and the tree stays VALID, as the
        # pack-facts residual states.
        ("pin-pack-version-second-value-unquoted-not-read", VALID, None, None,
         stale_in_section("Older notes say version = 9.9.9 here.")),
        # A second value under a key whose e is a Cyrillic ie (U+0435), or a format key whose o
        # is a Greek omicron (U+03BF): no pattern reads a look-alike letter as the key, so only
        # the character rule refuses it.
        ("pin-pack-version-second-value-look-alike-key", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say v" + chr(0x0435) + 'rsion = "9.9.9" here.')),
        ("pin-pack-format-second-value-look-alike-key", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say f" + chr(0x03BF) + 'rmat = "x/v2" here.')),
        # A key split by a combining grapheme joiner (U+034F), and a stale commit split by one:
        # no pattern reads either across it, so only the character rule refuses them.
        ("pin-pack-version-second-value-cgj-in-key", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say ver" + chr(0x034F) + 'sion = "9.9.9" here.')),
        ("pin-stale-commit-cgj-split", INVALID, "pin-section", "_check_pin_section_chars",
         stale_in_section("Older notes name the commit " + "d" * 20 + chr(0x034F) + "d" * 20
                          + " here.")),
        # An invisible or blank character between the whole key and the equals sign, or between
        # the equals sign and the quote (a combining grapheme joiner U+034F, a variation selector
        # U+FE0F, a Hangul filler U+3164, a blank braille pattern U+2800): the assignment count
        # lets only whitespace sit there, so only the character rule refuses each.
        ("pin-pack-version-second-value-cgj-after-key", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say version" + chr(0x034F) + ' = "9.9.9" here.')),
        ("pin-pack-version-second-value-variation-selector-after-key", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say version" + chr(0xFE0F) + ' = "9.9.9" here.')),
        ("pin-pack-version-second-value-hangul-filler-after-key", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say version" + chr(0x3164) + '= "9.9.9" here.')),
        ("pin-pack-version-second-value-braille-blank-after-key", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say version" + chr(0x2800) + '= "9.9.9" here.')),
        ("pin-pack-version-second-value-cgj-after-equals", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say version =" + chr(0x034F) + '"9.9.9" here.')),
        ("pin-pack-version-second-value-variation-selector-after-equals", INVALID,
         "pin-section", "_check_pin_section_chars",
         stale_in_section("Older notes say version =" + chr(0xFE0F) + '"9.9.9" here.')),
        ("pin-pack-version-second-value-hangul-filler-after-equals", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say version =" + chr(0x3164) + '"9.9.9" here.')),
        ("pin-pack-version-second-value-braille-blank-after-equals", INVALID, "pin-section",
         "_check_pin_section_chars",
         stale_in_section("Older notes say version =" + chr(0x2800) + '"9.9.9" here.')),
    ]


# Each red fixture that a second rule of the same guard would also refuse, with a phrase only the
# intended rule's refusal carries: with the intended rule removed the status and guard stay the
# same, so the fixture's refusal must carry that phrase. The raw-HTML refusal names raw HTML,
# which the node-kind rule, refusing the same nodes by kind, never does; the fence rule says a
# fence may carry no info string at all, which a one-word rule would not say.
_REFUSAL_WORDING_VECTORS = (
    ("pin-section-comment-harmless", "carries raw HTML"),
    ("pin-table-in-html-comment", "carries raw HTML"),
    ("pin-fence-info-c-sharp", "may carry no info string at all"),
)


def _with_check_disabled(name):
    """Replace the named module-level check with a no-op; returns the restorer. Attribute patching,
    so the disabled run exercises the real _validate with one guard removed."""
    return _with_globals({name: lambda *args, **kwargs: None})


def _with_globals(replacements):
    """Replace the named module-level attributes; returns the restorer, which puts every original
    back."""
    real = {name: globals()[name] for name in replacements}

    def restore():
        globals().update(real)
    globals().update(replacements)
    return restore


class _SubWith:
    """A stand-in for one of the rendered-markup patterns whose sub ignores the replacement the
    caller passes and uses its own, so a reading flip changes what one kind of tag reads as."""

    def __init__(self, pattern, replacement):
        self.pattern = pattern
        self.replacement = replacement

    def sub(self, _replacement, text):
        return self.pattern.sub(self.replacement, text)


def _reading_flip(name, base):
    """The module-level replacements that flip one reading back to a weaker one, for the fixture
    tree at base: raw-rule-reading restores the raw reading of the required-rule leg (the prompt's
    raw text and each sentence's own spelling, whitespace collapsed), code-block-credit and
    image-alt-credit let code-block content or image alt text restate a rule, block-tags-glue
    reads every block tag as nothing, and inline-tags-break reads every inline tag as a line
    break."""
    prompt = Path(base) / "opf" / PROMPT_REL
    flips = {
        "raw-rule-reading": lambda: {
            "_rule_credit_text": lambda marko, document: _normalize(
                prompt.read_text(encoding="utf-8")),
            "_rule_text": lambda marko, sentence: _normalize(sentence)},
        "code-block-credit": lambda: {
            "_rule_credit_text": lambda marko, document: _normalize(
                _rendered_text(marko, document, code_blocks=True, image_alt=False))},
        "image-alt-credit": lambda: {
            "_rule_credit_text": lambda marko, document: _normalize(
                _rendered_text(marko, document, code_blocks=False, image_alt=True))},
        "block-tags-glue": lambda: {"_HTML_ANY_TAG_RE": _SubWith(_HTML_ANY_TAG_RE, "")},
        "inline-tags-break": lambda: {"_HTML_INLINE_TAG_RE": _SubWith(_HTML_INLINE_TAG_RE, "\n")},
    }
    return flips[name]()


# Each fixture whose result one reading decides, with a flip of that reading back to a weaker one
# and the result the fixture must then give: a red result must turn green and a green one red, so
# each fixture discriminates the reading it names rather than passing under either.
_READING_FLIP_VECTORS = (
    ("required-rule-in-block-comment", "raw-rule-reading", VALID, None),
    ("required-rule-in-inline-comment", "raw-rule-reading", VALID, None),
    ("required-rule-in-code-block", "raw-rule-reading", VALID, None),
    ("required-rule-in-code-block", "code-block-credit", VALID, None),
    ("required-rule-in-image-alt", "raw-rule-reading", VALID, None),
    ("required-rule-in-image-alt", "image-alt-credit", VALID, None),
    ("required-rule-split-by-emphasis", "raw-rule-reading", INVALID, "required-rule"),
    ("pin-stale-runs-split-by-block-tag-outside-section", "block-tags-glue", INVALID,
     "pin-shape"),
    ("pin-stale-runs-joined-by-emphasis-outside-section", "inline-tags-break", VALID, None),
)


def _reading_flip_checks(tmp, check):
    builders = {name: build for name, _, _, _, build in _vectors()}
    ran = 0
    for vector, flip, status, guard in _READING_FLIP_VECTORS:
        base = Path(tmp) / ("flip-" + vector + "-" + flip)
        base.mkdir()
        builders[vector](base)
        restore = _with_globals(_reading_flip(flip, base))
        try:
            got = validate_tree(base)
        finally:
            restore()
        ran += 1
        check(vector + "-flip-" + flip, got, (status, guard))
    return ran


def _refusal_wording_checks(tmp, check):
    builders = {name: build for name, _, _, _, build in _vectors()}
    ran = 0
    for vector, phrase in _REFUSAL_WORDING_VECTORS:
        base = Path(tmp) / ("wording-" + vector)
        base.mkdir()
        builders[vector](base)
        got = validate_tree(base)
        ran += 1
        check(vector + "-wording",
              ("worded", None) if phrase in got[2]
              else ("unworded", None, "wanted {!r} in {!r}".format(phrase, got[2])),
              ("worded", None))
    return ran


def _list_quotations_probe(tmp, check):
    """The --list-quotations probe reads the prompt and the pinned sources through the same strict
    decode as the gate: a file that is not valid UTF-8 must exit 2 under its decode guard, never
    list the quotations of text read with the invalid byte escaped."""
    import contextlib
    import io
    builders = {name: build for name, _, _, _, build in _vectors()}
    statuses = {code: name for name, code in EXIT_CODES.items()}
    ran = 0
    for vector, guard in (("prompt-invalid-utf8", "prompt-decode"),
                          ("pinned-source-invalid-utf8", "pinned-source-decode")):
        base = Path(tmp) / ("list-quotations-" + vector)
        base.mkdir()
        builders[vector](base)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = list_quotations(base)
        ran += 1
        check("list-quotations-" + vector,
              (statuses.get(rc, "exit {!r}".format(rc)),
               guard if "[{}]".format(guard) in err.getvalue() else None,
               "exit {!r}; stderr {!r}".format(rc, err.getvalue())),
              (CANNOT_EVALUATE, guard))
    return ran


def _prompt_parse_probe(tmp, check):
    """The gate must fail closed where the vendored parser cannot load: the loader seam is
    patched to raise its own fail-closed error, and the result must be CANNOT-EVALUATE under
    prompt-parse, never VALID and never a fallback line reading."""
    from unittest.mock import patch
    base = Path(tmp) / "prompt-parse-unavailable"
    base.mkdir()
    _build_tree(base)

    def refuse():
        raise _commonmark_headings.HeadingScanError(
            "injected: the vendored parser cannot load",
            _commonmark_headings.REASON_VENDOR_UNREADABLE)

    with patch.object(_commonmark_headings, "_load_marko", refuse):
        got = validate_tree(base)
    check("prompt-parse-unavailable", got, (CANNOT_EVALUATE, "prompt-parse"))
    return 1


def _history_vectors(tmp, check):
    """The pin-history leg needs real repositories, so these vectors run only where git is on
    PATH; the in-tree byte checks above stay git-independent for the standalone-closure copy."""
    if shutil.which("git") is None:
        print("SKIP history vectors: git is not on PATH")
        return 0
    env = _scrubbed_env()

    def run_git(root, args):
        proc = _git(root, args, env)
        if proc.returncode != 0:
            raise RuntimeError("fixture git failed: {}".format(
                proc.stderr.decode(errors="backslashreplace")))
        return proc

    def repo(base):
        root = _build_tree(base)
        for args in (["init", "-q"], ["add", "-A"],
                     ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                      "commit", "-q", "-m", "fixture"]):
            run_git(root, args)
        head = run_git(root, ["rev-parse", "HEAD"]).stdout.decode().strip()
        prompt = root / "opf" / PROMPT_REL
        prompt.write_text(prompt.read_text(encoding="utf-8").replace(_FIXTURE_PIN, head),
                          encoding="utf-8")
        return root, head

    def repoint_spec_digest(root):
        _spec_edit(root)
        prompt = root / "opf" / PROMPT_REL
        new_digest = hashlib.sha256((root / SPEC_TABLE_PATH).read_bytes()).hexdigest()
        text = prompt.read_text(encoding="utf-8")
        old_row = re.search(r"\| `{}` \| pinned \| `([0-9a-f]{{64}})` \|".format(
            re.escape(SPEC_TABLE_PATH)), text).group(1)
        prompt.write_text(text.replace(old_row, new_digest), encoding="utf-8")

    ran = 0
    root, _ = repo(Path(tmp) / "history-valid")
    got = validate_tree(root)
    ran += 1
    check("history-valid", got, (VALID, None))

    root, _ = repo(Path(tmp) / "history-drifted")
    repoint_spec_digest(root)
    got = validate_tree(root)
    ran += 1
    check("history-blob-mismatch", got, (INVALID, "pin-history"))
    restore = _with_check_disabled("_check_history")
    try:
        disabled = validate_tree(root)
    finally:
        restore()
    ran += 1
    check("history-blob-mismatch-disabled", disabled, (VALID, None))

    root, head = repo(Path(tmp) / "history-missing")
    prompt = root / "opf" / PROMPT_REL
    prompt.write_text(prompt.read_text(encoding="utf-8").replace(head, "f" * 40),
                      encoding="utf-8")
    got = validate_tree(root)
    ran += 1
    check("history-commit-missing", got, (CANNOT_EVALUATE, "pin-history"))

    # A git failure that is not "no repository" must fail closed, never read as a skip.
    root, _ = repo(Path(tmp) / "history-discovery-error")
    (root / ".git" / "config").write_text("[\n", encoding="utf-8")
    got = validate_tree(root)
    ran += 1
    check("history-discovery-error", got, (CANNOT_EVALUATE, "pin-history"))

    # A replace ref must not substitute another commit's blobs for the pinned commit's.
    root, head = repo(Path(tmp) / "history-replaced")
    repoint_spec_digest(root)
    run_git(root, ["add", "-A"])
    run_git(root, ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                   "commit", "-q", "-m", "edit"])
    second = run_git(root, ["rev-parse", "HEAD"]).stdout.decode().strip()
    run_git(root, ["replace", head, second])
    got = validate_tree(root)
    ran += 1
    check("history-replaced-commit", got, (INVALID, "pin-history"))
    restore = _with_check_disabled("_check_history")
    try:
        disabled = validate_tree(root)
    finally:
        restore()
    ran += 1
    check("history-replaced-commit-disabled", disabled, (VALID, None))
    return ran


def self_test():
    """Isolate fixture git configuration and restore the caller even on failure: the fixture git
    calls (built through _scrubbed_env, which carries HOME) must read a fresh, empty HOME and
    XDG_CONFIG_HOME with the system config disabled, never the caller's or the host's, so the
    self-test stays hermetic under an injected caller or system git configuration. The direct
    `--self-test` entry calls this without main()'s backstop, so a harness error raised here,
    outside the suite's own guard (the fixture HOME cannot be created, say), is cannot-evaluate
    (2) too, never an uncaught exception that exits 1."""
    from unittest.mock import patch
    try:
        with tempfile.TemporaryDirectory(prefix="opf-prompt-pin-selftest-home-") as home:
            with patch.dict(os.environ, HOME=home, XDG_CONFIG_HOME=home,
                            GIT_CONFIG_NOSYSTEM="1"):
                return _self_test_isolated()
    except Exception as exc:  # noqa: BLE001  a harness error is cannot-evaluate, never a pass
        print("OPF-PROMPT-PIN SELF-TEST: CANNOT EVALUATE ({!r})".format(exc), file=sys.stderr)
        return 2


def _direct_entry_harness_error():
    """Run self_test() with the fixture HOME refused, as the direct `--self-test` entry would meet
    it; the result must be cannot-evaluate (exit 2), never an exception escaping the entry. The
    inner run's diagnostic is captured, so it is not read as this suite's own verdict."""
    import contextlib
    import io
    from unittest.mock import patch

    def refuse(*args, **kwargs):
        raise OSError("injected: the fixture HOME cannot be created")

    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured), patch.object(tempfile, "TemporaryDirectory",
                                                                 refuse):
            rc = self_test()
    except Exception as exc:  # noqa: BLE001  an escaping exception is this vector's finding
        return ("raised", None, repr(exc))
    status = {code: name for name, code in EXIT_CODES.items()}.get(rc, "exit {!r}".format(rc))
    return (status, None, "self_test() returned {!r}; stderr {!r}".format(rc, captured.getvalue()))


def _self_test_isolated():
    """0 every vector returned its exact status and guard, repeated it on a second run, and turned
    green with its covering check disabled where it names one; 1 a discriminator failed, 2 harness
    error."""
    failures = []
    count = 0

    def check(name, got, wanted):
        ok = got[:2] == wanted
        print("{} {}: {} {}".format("PASS" if ok else "FAIL", name, got[0], got[1] or "").rstrip())
        if not ok:
            failures.append("{} (wanted {} {}, got {} {}: {})".format(
                name, wanted[0], wanted[1], got[0], got[1], got[2]))

    try:
        with tempfile.TemporaryDirectory(prefix="opf-prompt-pin-selftest-") as tmp:
            for index, (name, status, guard, covering, build) in enumerate(_vectors()):
                base = Path(tmp) / "case{}".format(index)
                base.mkdir()
                build(base)
                got = validate_tree(base)
                again = validate_tree(base)
                count += 1
                check(name, got, (status, guard))
                # The second run must repeat the first exactly; a tuple that differs in any
                # element, the detail included, is its own failure, never a passed detail.
                count += 1
                check(name + "-stable",
                      ("stable", None) if again == got
                      else ("unstable", None, "first {!r}, second {!r}".format(got, again)),
                      ("stable", None))
                if covering is not None:
                    restore = _with_check_disabled(covering)
                    try:
                        disabled = validate_tree(base)
                    finally:
                        restore()
                    count += 1
                    check(name + "-disabled-" + covering, disabled, (VALID, None))
            count += _history_vectors(tmp, check)
            count += _refusal_wording_checks(tmp, check)
            count += _reading_flip_checks(tmp, check)
            count += _list_quotations_probe(tmp, check)
            count += _prompt_parse_probe(tmp, check)
            count += 1
            check("direct-entry-harness-error", _direct_entry_harness_error(),
                  (CANNOT_EVALUATE, None))
    except Exception as exc:  # noqa: BLE001  a harness error is cannot-evaluate, never a pass
        print("OPF-PROMPT-PIN SELF-TEST: CANNOT EVALUATE ({!r})".format(exc), file=sys.stderr)
        return 2
    if failures:
        print("OPF-PROMPT-PIN SELF-TEST: FAIL ({} of {} checks failed): {}".format(
            len(failures), count, "; ".join(failures)))
        return 1
    print("OPF-PROMPT-PIN SELF-TEST: PASS ({} checks)".format(count))
    return 0


def main(argv=None):
    try:
        args = list(sys.argv[1:] if argv is None else argv)
        if args == ["--self-test"]:
            return self_test()
        if args == ["--list-quotations"]:
            return list_quotations()
        if args:
            print("usage: check_opf_prompt_pin.py [--self-test | --list-quotations]",
                  file=sys.stderr)
            return 2
        status, guard, detail = validate_tree(DEFAULT_ROOT)
        if status == VALID:
            print("OPF PROMPT PIN: VALID ({})".format(detail))
        else:
            print("OPF PROMPT PIN: {} [{}] {}".format(status, guard, detail), file=sys.stderr)
        return EXIT_CODES[status]
    except Exception as exc:  # noqa: BLE001  fail-closed backstop, never a false 0
        print("check_opf_prompt_pin: cannot evaluate: unexpected error ({!r})".format(exc),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    sys.exit(main())
