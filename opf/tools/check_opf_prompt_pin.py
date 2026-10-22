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
  prompt-read     the prompt is a readable regular file within the size bound (else CANNOT-EVALUATE)
  pin-shape       the prompt names at least three 40-lowercase-hex commits, in its raw text and
                  in the visible text of its rendered output alike, and every spelling either way
                  is identical, so a spelling assembled only by rendering (a character reference)
                  and a spelling hidden from rendering (an HTML comment) must both agree with the
                  pin; a commit-shaped spelling in the pin section that is not all lowercase is
                  refused rather than skipped
  pin-section     the prompt carries the pin section between its two fixed headings, each judged
                  on the vendored CommonMark parse as the level-two ATX heading node, a direct
                  child of the document, whose raw source line is byte-for-byte the fixed text,
                  present exactly once with the head before the tail, so a section hidden inside
                  an HTML comment or any other container, whose bytes survive but which never
                  renders, does not bound the pin checks
  pin-location    the pin sentence, the raw-URL template and the example URL each name the pinned
                  commit exactly once, so a movable name (a branch) in one of those places cannot
                  hide behind the spelling count elsewhere
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
  pack-facts      the format and version the prompt states match opf/prompt-pack/pack.toml
  quotation       every double-quoted span of MIN_QUOTE_CHARS or more normalized characters whose
                  nearby context (same paragraph, within _QUOTE_CONTEXT_CHARS before the opening
                  quote) attributes it to a pinned source appears word for word,
                  whitespace-insensitively, in one of the seven pinned files; the only allowed
                  non-matches are the named spans in QUOTE_EXEMPTIONS (the prompt quoting its own
                  headings and its own worked examples), and a paragraph with an unpaired double
                  quote is a finding because pairing cannot be trusted there
  required-rule   a fixed list of section 16.1 and section 9.2 sentences appears verbatim
                  (whitespace-insensitively) in both the specification and the prompt
  journal-grading the prompt carries no C0 control character other than LF and TAB, and no
                  U+0085, U+2028 or U+2029, anywhere (line models disagree over several of them:
                  CommonMark reads a lone CR as a line ending, marko also reads a form feed as
                  one, Python's splitlines also breaks on the vertical tab, the C0 file, group
                  and record separators (U+001C to U+001E), U+0085, U+2028 and U+2029, and
                  CommonMark substitutes U+FFFD for NUL, so those characters could move a
                  heading, fence, underline or clause between what renders and what another
                  reading of the same bytes sees; the whole range is refused by presence, and
                  the refusal names the character it finds), and the step 8
                  journal rule is judged on the vendored CommonMark parse (opf/tools/_vendor/
                  marko, loaded fail-closed with its provenance checked through
                  _commonmark_headings), never on a hand-written line model: the step 8 heading
                  is the level-three ATX heading node whose raw source line is byte-for-byte the
                  fixed text, a direct child of the document (so a spelling inside fenced or
                  indented code, an HTML block, a blockquote, a list or any other container is
                  never that heading), present exactly once; the step runs to the next top-level
                  ATX or setext heading of level one to three; raw HTML anywhere inside the step,
                  block or inline (a comment, a processing instruction, a declaration, a CDATA
                  section or a tag), is refused by name, because rendered output drops or
                  transforms it; the step must render exactly one list item, at any list depth,
                  whose visible text opens with the journal lead, and that item's visible text
                  must carry every named clause of JOURNAL_GRADING (whitespace-insensitively),
                  so the item's store-path conclusion stays limited to a store that has not
                  started an adoption, it keeps what the specification says of a store that has,
                  and it keeps the rule that the upgrade check counts every journal entry; a
                  clause of JOURNAL_GRADING_DENIED is refused where the step's visible text or
                  its raw source carries it. Visible text is derived from the vendored HTML
                  renderer's own output (_visible_from_html): comments and tags drop, character
                  references decode exactly once, and inline markup (a code span's delimiters,
                  emphasis markers, link brackets) leaves exactly the text the rendered output
                  shows, so a clause spelled through a character reference or split by inline
                  markup is judged as a reader sees it; a link or image destination or title, a
                  link reference definition and raw HTML never contribute. The clause literals
                  are rendered through the same pipeline, so both sides of every comparison
                  carry one form. Code-block content and image alt text render, but only as
                  literal code and as an attribute, so neither earns required-clause credit,
                  while the denied-clause scan reads both, and the step's raw source besides as
                  a backstop, keeping the denial wider than the credit. The rule deliberately
                  over-refuses (a heading spelled with trailing spaces or a closing hash run, a
                  required clause shown only inside a code block or image alt text, raw HTML
                  that hides nothing, and a refused control character whose rendering is
                  harmless all fail), so a prompt whose step
                  8 legitimately needs a refused form must extend this gate first; a parser or
                  renderer that cannot load, raises, or returns a parse that does not cover the
                  prompt is CANNOT-EVALUATE under journal-parse, never a fallback line reading

  check_opf_prompt_pin.py                    verify the shipped prompt beside this tool (../spec)
  check_opf_prompt_pin.py --self-test        synthetic trees assert each guard's exact status and
                                             name, that each result is stable across two runs, and
                                             that each red fixture covered by one module-level
                                             check turns green when exactly that check is disabled,
                                             and that each journal-grading fixture that removes or
                                             adds one clause turns green when exactly that clause
                                             is dropped
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
it reads the source correctly. The history leg authenticates nothing: it reads whatever object
store the checked tree carries, with replacement-object resolution disabled.
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
# must still carry; each is quoted from opf/spec/OPF-SPEC.md at the pinned commit (sections 9.2 and
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
    ("no-class-bound",
     "The file declares no class only where it omits its class and no other field: a file that is "
     "absent, or that also omits its release identity, its `spec_version`, its homes generation, "
     "or its worklog storage generation, is malformed and MUST yield cannot-evaluate."),
    ("imported-counter-ancestry",
     "Where imported ancestry exists in a namespace, the imported counter row added for it MUST "
     "hold the highest imported ID number in that namespace, the largest `<n>` of its "
     "`imported:<NS>-<n>` IDs."),
)

# Section 4.2 gives legacy grading under pre-1.3.0 tooling only to a store that has not started an
# adoption, so the step 8 journal item may call a journal entry a store path only for such a store,
# and it states what section 4.2 says of a store that has started one. The quotation leg cannot hold
# this: the unqualified quotation is a substring of the qualified sentence, so the earlier,
# unqualified wording of the item still matches the specification. Each clause below is prompt text
# the item must carry; the denied clause is that earlier, unqualified conclusion, refused anywhere
# in step 8 (a continuation paragraph of the item sits past the blank line where the item scan
# stops, but it still renders inside the step). The rule that the upgrade check counts every
# journal entry is held as a clause too, because the qualification limits only the store-path
# reading, never the count.
_STEP8_HEAD = "### Step 8: the store upgrade to 1.3.0 (`opf upgrade`)"
_JOURNAL_ITEM_LEAD = "- `.working/journals/`"
JOURNAL_GRADING = (
    ("non-adopting-grading-quoted",
     '"a store that has not started an adoption keeps, within that tooling\'s own version ceiling, '
     "its legacy grading: the homes-2 names are ordinary store paths there, graded, detected, and "
     'dispositioned exactly as before"'),
    ("non-adopting-conclusion",
     "so in a homes-1 origin store that has not started an adoption a journal entry is a store "
     "path, not machine-local data."),
    ("adopting-store-distinction",
     "Of a store whose adoption home `.working/imported/adoption/` exists, section 4.2 says instead "
     'that it "has started an adoption and receives the adoption grading from the same pre-1.3.0 '
     'tooling", whose doctor "MUST NOT read the machine-local journal"; the check still counts that '
     "store's journal entries, as the rule above says of every entry."),
    ("every-entry-counts",
     "check counts every entry under `.working/journals/`, which is this prompt's choice"),
)
JOURNAL_GRADING_DENIED = (
    ("unqualified-conclusion", "in a homes-1 origin store a journal entry is a store path"),
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
     "`conformant_for_declared_scope` (OPFiles base, spec 1.3.0 on homes 1; self-asserted; "
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


def _check_pin_shape(raw_commits, visible_commits):
    """At least MIN_PIN_OCCURRENCES 40-hex commit spellings in the raw prompt text and in the
    visible text of its rendered output alike, and every spelling either way identical: a spelling
    assembled only by rendering (a character reference the raw scan cannot see) and a spelling
    hidden from rendering (an HTML comment the visible scan cannot see) must both agree with the
    pin, and a prompt whose rendered output shows fewer than the three structural spellings is
    refused even where its raw bytes still carry them."""
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


def _check_pin_locations(section, pin):
    """The three places that must each name the pinned commit: the pin sentence, the raw-URL
    template and the example URL. Counting identical spellings alone is not enough: one of these
    places could name a branch while the spelling count stays satisfied by text elsewhere."""
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
    malformed row is a finding rather than a line the row pattern silently drops."""
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
    # F-367: no detached auto-gc or auto-maintenance may outlive a call and churn .git while a
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


def _check_pack_facts(root, section):
    """The format and version the pin section states match opf/prompt-pack/pack.toml."""
    stated_fmt = re.findall(r"`format = \"([^\"`]+)\"`", section)
    stated_ver = re.findall(r"`version = \"([^\"`]+)\"`", section)
    _require(len(stated_fmt) == 1 and len(stated_ver) == 1, "pack-facts",
             "the pin section must state the pack format and version exactly once each; found "
             "{} format and {} version statement(s)".format(len(stated_fmt), len(stated_ver)))
    raw = _read_regular(root / "opf" / PACK_REL, "pack-manifest")
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
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


def _sources_norm(root):
    """The normalized quotation corpus: each pinned file, plus a comment-leader-stripped copy."""
    source_texts = [_read_regular(root / rel, "pinned-source").decode(
        "utf-8", errors="backslashreplace") for rel in TABLE_PATHS]
    return "\n".join(
        [_normalize(text) for text in source_texts]
        + [_normalize(_strip_comment_leaders(text)) for text in source_texts])


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


def _check_required_rules(spec_norm, prompt_norm):
    for name, sentence in REQUIRED_RULES:
        wanted = _normalize(sentence)
        _require(wanted in spec_norm, "required-rule",
                 "required rule {!r} is absent from the pinned specification".format(name))
        _require(wanted in prompt_norm, "required-rule",
                 "required rule {!r} is not restated in the prompt".format(name))


# Every C0 control character except LF and TAB, plus U+0085, U+2028 and U+2029. Line models
# disagree over several characters in this range: CommonMark reads a lone CR as a line ending,
# marko also reads a form feed as one, Python's splitlines also breaks on the vertical tab, the
# C0 file, group and record separators (U+001C to U+001E), U+0085, U+2028 and U+2029, and
# CommonMark substitutes U+FFFD for NUL, so those characters can move a heading, fence, underline
# or clause between what one reading of the bytes sees and what another renders; the whole range
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
    underline or clause between what one reading of the bytes sees and what another renders. The
    refusal is by presence, not by effect, over the whole range. The shipped prompt carries none
    of these characters, so nothing legitimate is refused."""
    match = _REFUSED_CONTROL_RE.search(prompt_text)
    found = match.group(0) if match else "\r"
    _require(match is None, "journal-grading",
             "the prompt carries {} at offset {}, a refused control character: every C0 control "
             "except LF and TAB, plus U+0085, U+2028 and U+2029, is refused because line models "
             "disagree over characters in that range, so text around one can render away from "
             "what another reading of the same bytes sees".format(
                 _REFUSED_CONTROL_NAMES.get(
                     found, "the C0 control U+{:04X}".format(ord(found))),
                 match.start() if match else -1))


def _journal_markdown(prompt_text):
    """The vendored CommonMark parse this gate judges the pin section, the pin spellings and
    step 8 on: the pinned marko under opf/tools/_vendor, loaded and provenance-checked by
    _commonmark_headings (version and import origin, fail-closed), parsed fresh per call with no
    extensions. The renderer modules the visible-text derivation uses (marko.renderer,
    marko.html_renderer) get the same import-origin check as the loader's own submodules, so an
    import hook cannot supply either from outside the vendored tree while the parser loads from
    inside it. The prompt is parsed exactly as read, ahead of the control-character refusal that
    the journal leg still requires: marko's preprocessing substitutions are length-preserving for
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
        raise _Refusal(CANNOT_EVALUATE, "journal-parse",
                       "the vendored CommonMark parser could not parse the prompt: {}".format(exc))
    _require(getattr(document, "source_span", None) == (0, len(prompt_text))
             and isinstance(getattr(document, "children", None), list), "journal-parse",
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
             and 0 <= span[0] <= span[1] <= len(prompt_text), "journal-parse",
             "a parsed node carries no usable source span", CANNOT_EVALUATE)
    return span


def _heading_level(node):
    level = getattr(node, "level", None)
    _require(isinstance(level, int), "journal-parse",
             "a parsed heading carries no integer level", CANNOT_EVALUATE)
    return level


def _step8_section(marko, document, prompt_text):
    """(the nodes of step 8, the step's raw source text). The step 8 heading is the ATX heading
    node of level three, a direct child of the document, whose raw source line is byte-for-byte
    _STEP8_HEAD: judged on the parse, a spelling inside fenced or indented code, an HTML block, a
    blockquote, a list or any other container is not that node, and neither is an indented,
    reworded or trailing-decorated spelling, so each of those reads as a missing heading (or, for
    a stray top-level copy, a duplicate) rather than being read around. Exactly one such node, or
    a finding. The step runs from that heading to the next direct-child heading of level one to
    three (ATX or setext, at any indent Markdown still renders), or to the end of the document."""
    children = document.children
    heads = []
    for index, child in enumerate(children):
        if isinstance(child, marko.block.Heading) and _heading_level(child) == 3:
            start, end = _node_span(prompt_text, child)
            raw = prompt_text[start:end]
            if raw.endswith("\n"):
                raw = raw[:-1]
            if raw == _STEP8_HEAD:
                heads.append(index)
    _require(len(heads) == 1, "journal-grading",
             "the prompt must render the heading {!r} exactly once, at the top level and outside "
             "any code, HTML or other container; the parse finds {}".format(
                 _STEP8_HEAD, len(heads)))
    end_index = len(children)
    for index in range(heads[0] + 1, len(children)):
        child = children[index]
        if (isinstance(child, (marko.block.Heading, marko.block.SetextHeading))
                and _heading_level(child) <= 3):
            end_index = index
            break
    raw_start = _node_span(prompt_text, children[heads[0]])[1]
    raw_end = (_node_span(prompt_text, children[end_index])[0] if end_index < len(children)
               else len(prompt_text))
    return children[heads[0] + 1:end_index], prompt_text[raw_start:raw_end]


def _check_step8_html(marko, section):
    """Raw HTML anywhere inside step 8, block or inline, is refused by name: rendered output drops
    or transforms raw HTML (a comment, a processing instruction, a declaration, a CDATA section or
    a tag), so text inside one never renders as the prompt text this gate credits, and what a
    renderer makes of the rest is outside what this gate can judge. This over-refuses HTML that
    hides nothing, by design."""
    for node in _md_walk(section):
        _require(not isinstance(node, marko.block.HTMLBlock), "journal-grading",
                 "step 8 carries a raw HTML block, an unsupported form here: rendered output "
                 "drops or transforms raw HTML, so nothing inside it is rendered prompt text")
        _require(not isinstance(node, marko.inline.InlineHTML), "journal-grading",
                 "step 8 carries inline raw HTML, an unsupported form here: rendered output "
                 "drops or transforms raw HTML, so nothing inside it is rendered prompt text")


# The rendered output's own markup, used to derive visible text from the vendored renderer's
# HTML. The renderer escapes every text and attribute character it emits, so no visible "<"
# survives to be read as a tag here; raw HTML it passes through verbatim is refused inside step 8
# before any scan, and elsewhere it is read conservatively (tags dropped, text kept). Its inline
# tags (a, code, em, strong) wrap text without separating it, so they drop to nothing and a clause
# split by inline markup reads exactly as rendered; every other tag opens or closes a block (or is
# a line break), so it reads as a line break and adjacent blocks never glue together.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
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
        raise _Refusal(CANNOT_EVALUATE, "journal-parse",
                       "the vendored renderer could not render the prompt: {}".format(exc))
    _require(isinstance(rendered, str), "journal-parse",
             "the vendored renderer returned a non-string rendering", CANNOT_EVALUATE)
    return rendered


def _visible_from_html(rendered, code_blocks=True, image_alt=True):
    """The text a reader of the rendered output sees, derived from the vendored renderer's own
    HTML with the standard library: comments drop (nothing inside one renders); code-block
    content stays only where code_blocks keeps it (it renders literally, but text shown only as
    code is not the prompt stating a rule, so required-clause credit excludes it); an image
    contributes its alt text only where image_alt keeps it (HTML shows alt only as an attribute);
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


def _clause_text(marko, text):
    """The normalized visible form of a clause or lead literal: parsed and rendered through the
    same vendored pipeline as the prompt, so both sides of every comparison carry one form (a
    code span's backticks drop on both sides, say, and the comparison is between what two
    rendered outputs show, never between markup spellings)."""
    try:
        document = _commonmark_headings._new_parser(marko).parse(text)
    except Exception as exc:
        raise _Refusal(CANNOT_EVALUATE, "journal-parse",
                       "the vendored parser could not parse a gate clause: {}".format(exc))
    return _normalize(_rendered_text(marko, document))


def _journal_item_node(marko, section):
    """The one list item of step 8 whose visible text opens with the journal lead. List items are
    searched at every depth of the step (the shipped item is nested inside the locations list),
    and the lead is matched on visible text, so lead text inside fenced or indented code is never
    an item (code renders no list item) and a second rendering of the lead, nested or not, is a
    duplicate. Exactly one, or a finding."""
    lead = _clause_text(marko, _JOURNAL_ITEM_LEAD[len("- "):])
    items = [node for node in _md_walk(section)
             if isinstance(node, marko.block.ListItem)
             and _normalize(_rendered_text(marko, node, code_blocks=False,
                                           image_alt=False)).startswith(lead)]
    _require(len(items) == 1, "journal-grading",
             "step 8 must render exactly one list item opening {!r}; the parse finds {}".format(
                 _JOURNAL_ITEM_LEAD, len(items)))
    return items[0]


def _check_journal_grading(marko, document, prompt_text):
    """Judge the step 8 journal rule on the vendored CommonMark parse and the vendored renderer's
    output, never on a line model: the heading, the step's extent and the item are taken from the
    parse, and every clause comparison runs on visible text derived from the rendered HTML
    (_visible_from_html), with the clause literals rendered through the same pipeline. The
    indentation, fence, setext, list and lazy-continuation reading is marko's, and it matches
    another renderer only as far as marko conforms to CommonMark. The required clauses must
    appear in the item's visible text,
    which excludes code-block content and image alt text (code renders only as code, and HTML
    shows alt only as an attribute); the denied clause is refused where the step's visible text,
    code-block content and image alt text included, or the step's raw source carries it, so an
    unqualified conclusion is refused even where only a renderer, or only a byte reading, would
    show it (a denied clause spelled through a character reference or split by inline markup is
    caught by the visible scan, which reads what the rendered output shows; one inside a code
    block by both scans; raw HTML in the step is refused outright before either scan runs). The
    control-character refusal runs here first, so a VALID prompt has LF as its only line ending
    and every reading agrees on where lines begin and end; the pin legs run ahead of this refusal
    on the same parse, which the covering-span requirement in _journal_markdown keeps sound."""
    _check_prompt_controls(prompt_text)
    section, step_raw = _step8_section(marko, document, prompt_text)
    _check_step8_html(marko, section)
    item = _journal_item_node(marko, section)
    item_text = _normalize(_rendered_text(marko, item, code_blocks=False, image_alt=False))
    for name, clause in JOURNAL_GRADING:
        _require(_clause_text(marko, clause) in item_text, "journal-grading",
                 "the step 8 journal item lacks the required clause {!r}".format(name))
    step_text = _normalize("\n".join(_rendered_text(marko, node) for node in section))
    for name, clause in JOURNAL_GRADING_DENIED:
        _require(_clause_text(marko, clause) not in step_text
                 and _normalize(clause) not in _normalize(step_raw),
                 "journal-grading",
                 "step 8 carries the denied clause {!r}".format(name))


def _validate(root):
    """The vendored parse and rendering back the pin legs and the journal leg alike. They run
    ahead of the control-character refusal, which stays inside the journal leg that VALID still
    requires, and they stay sound ahead of it: marko's preprocessing is length-preserving for
    every refused character except a CRLF pair, and a prompt whose parse does not cover its text
    is CANNOT-EVALUATE at journal-parse, never mis-sliced."""
    root = Path(root)
    prompt_text = _read_regular(root / "opf" / PROMPT_REL, "prompt").decode(
        "utf-8", errors="backslashreplace")
    marko, document = _journal_markdown(prompt_text)
    section, section_nodes = _pin_section(marko, document, prompt_text)
    raw_commits = _COMMIT_RE.findall(prompt_text)
    visible_commits = _COMMIT_RE.findall(_rendered_text(marko, document))
    _check_pin_shape(raw_commits, visible_commits)
    pin = raw_commits[0] if raw_commits else None
    section_visible = "\n".join(_rendered_text(marko, node) for node in section_nodes)
    _check_pin_spelling(section, section_visible)
    _check_pin_locations(section, pin)
    table = _parse_table(section)
    _check_drift(root, table)
    history = _check_history(root, pin, table) if pin else "history: skipped (no pin found)"
    _check_pack_facts(root, section)
    sources_norm = _sources_norm(root)
    spec_norm = _normalize(_read_regular(root / SPEC_TABLE_PATH, "pinned-source").decode(
        "utf-8", errors="backslashreplace"))
    _check_quotations(prompt_text, sources_norm)
    _check_required_rules(spec_norm, _normalize(prompt_text))
    _check_journal_grading(marko, document, prompt_text)
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
        prompt_text = _read_regular(root / "opf" / PROMPT_REL, "prompt").decode(
            "utf-8", errors="backslashreplace")
        sources = _sources_norm(root)
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
# The fixture's step 8 journal item, one clause per line, written out here rather than built from
# JOURNAL_GRADING, so the valid tree also checks those clauses against independent text. The base
# line is the item's earlier, unqualified wording, which also lacked the adopting-store sentence.
_FIXTURE_JOURNAL_LEAD = "  - `.working/journals/`, the journal home of section 4.2."
_FIXTURE_JOURNAL_EVERY_ENTRY = (
    "The check counts every entry under `.working/journals/`, which is this prompt's choice.")
_FIXTURE_JOURNAL_GRADING = (
    "Whichever generation it binds, section 4.2 says that under pre-1.3.0 tooling, the tooling of "
    'every origin store of this upgrade, "a store that has not started an adoption keeps, within '
    "that tooling's own version ceiling, its legacy grading: the homes-2 names are ordinary store "
    'paths there, graded, detected, and dispositioned exactly as before", and section 5.1 makes "An '
    'untracked or ignored `.working/` tree" a hard failure, so in a homes-1 origin store that has '
    "not started an adoption a journal entry is a store path, not machine-local data.")
_FIXTURE_JOURNAL_ADOPTING = (
    "Of a store whose adoption home `.working/imported/adoption/` exists, section 4.2 says instead "
    'that it "has started an adoption and receives the adoption grading from the same pre-1.3.0 '
    'tooling", whose doctor "MUST NOT read the machine-local journal"; the check still counts that '
    "store's journal entries, as the rule above says of every entry.")
_FIXTURE_JOURNAL_BASE_GRADING = (
    "Whichever generation it binds, section 4.2 says of a store under pre-1.3.0 tooling, as every "
    'origin store of this upgrade was, that "the homes-2 names are ordinary store paths there, '
    'graded, detected, and dispositioned exactly as before", and section 5.1 makes "An untracked or '
    'ignored `.working/` tree" a hard failure, so in a homes-1 origin store a journal entry is a '
    "store path, not machine-local data.")
_FIXTURE_JOURNAL_ITEM = "\n    ".join((_FIXTURE_JOURNAL_LEAD, _FIXTURE_JOURNAL_EVERY_ENTRY,
                                       _FIXTURE_JOURNAL_GRADING, _FIXTURE_JOURNAL_ADOPTING))
_FIXTURE_STEP9_HEAD = "### Step 9: fresh initialization"


def _fixture_spec():
    rules = "\n\n".join(sentence for _, sentence in REQUIRED_RULES)
    return ("# Specification\n\n" + rules +
            "\n\nViews regenerate cleanly. The emitter writes canonical bytes for every view it "
            "renders.\n\nUnder pre-1.3.0 tooling, a store that has not started an adoption keeps, "
            "within that tooling's own version ceiling, its legacy grading: the homes-2 names are "
            "ordinary store paths there, graded, detected, and dispositioned exactly as before. A "
            "store whose adoption home `.working/imported/adoption/` exists has started an "
            "adoption and receives the adoption grading from the same pre-1.3.0 tooling. The "
            "doctor MUST NOT read the machine-local journal.\n\nAn untracked or ignored "
            "`.working/` tree is a hard failure.\n")


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
            table + "\n\nEach raw URL names the commit:\n\n```text\nhttps://example.invalid/" + pin +
            "/<path>\n```\n\nFor example, https://example.invalid/" + pin + "/opf/spec/OPF-SPEC.md.\n\n" +
            'The pack manifest (`format = "opf.prompt-pack/v1"`, `version = "' + pack_version +
            '"`) lists no members.\n\n' + _PIN_SECTION_TAIL + "\n\n" +
            'The specification says "' + quote + '"\n\n' + "\n\n".join(rules) + "\n\n" +
            _STEP8_HEAD + "\n\n" + _FIXTURE_JOURNAL_ITEM + "\n\n" + _FIXTURE_STEP9_HEAD + "\n" +
            ("\n" + extra_text + "\n" if extra_text else ""))


def _build_tree(base, pack_text=_FIXTURE_PACK, source_overrides=None, **prompt_kwargs):
    root = Path(base)
    sources = dict(_FIXTURE_SOURCES)
    if source_overrides:
        sources.update(source_overrides)
    for rel, text in sources.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / SPEC_TABLE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / SPEC_TABLE_PATH).write_text(_fixture_spec(), encoding="utf-8")
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

    def journal_base_restored(base):
        # The item's earlier wording, whose every quotation still matches the specification.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_GRADING + "\n    " + _FIXTURE_JOURNAL_ADOPTING,
                     _FIXTURE_JOURNAL_BASE_GRADING)

    def journal_quote_unqualified(base):
        # The shorter quotation is still a substring of the specification's sentence.
        root = _build_tree(base)
        _edit_prompt(root, '"a store that has not started an adoption keeps, within that '
                     "tooling's own version ceiling, its legacy grading: the homes-2", '"the homes-2')

    def journal_conclusion_removed(base):
        root = _build_tree(base)
        _edit_prompt(root, " a hard failure, so in a homes-1 origin store that has not started an "
                     "adoption a journal entry is a store path, not machine-local data.",
                     " a hard failure.")

    def journal_adopting_removed(base):
        root = _build_tree(base)
        _edit_prompt(root, "\n    " + _FIXTURE_JOURNAL_ADOPTING, "")

    def journal_every_entry_removed(base):
        root = _build_tree(base)
        _edit_prompt(root, "\n    " + _FIXTURE_JOURNAL_EVERY_ENTRY, "")

    def journal_unqualified_added(base):
        # Every required clause stays; the earlier, unqualified conclusion is added beside them.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_ADOPTING, _FIXTURE_JOURNAL_ADOPTING +
                     "\n    In short, in a homes-1 origin store a journal entry is a store path.")

    def journal_item_missing(base):
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_LEAD, "  - `.working/journal/`, the journal home.")

    def journal_item_duplicate(base):
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_ITEM,
                     _FIXTURE_JOURNAL_ITEM + "\n" + _FIXTURE_JOURNAL_ITEM)

    def journal_step8_renamed(base):
        root = _build_tree(base)
        _edit_prompt(root, _STEP8_HEAD, "### Step 8: the store upgrade")

    def journal_item_after_step8(base):
        # Just past the step's end: the next level-three heading closes step 8.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_ITEM + "\n\n" + _FIXTURE_STEP9_HEAD,
                     _FIXTURE_STEP9_HEAD + "\n\n" + _FIXTURE_JOURNAL_ITEM)

    def journal_item_under_level_four(base):
        # Just inside: a level-four heading does not close step 8.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_ITEM, "#### Journals\n\n" + _FIXTURE_JOURNAL_ITEM)

    def every_entry_moved(indent, separator="\n"):
        # The every-entry clause moved to the end of the item at the given indentation (the
        # bullet sits at 2, so the item's content indent is 4), after the given separator.
        # Judged on the parse, CommonMark decides where the clause renders: with no blank line
        # the moved line is a lazy continuation of the item's paragraph at any indent, so the
        # clause stays inside the item; after a blank or whitespace-only line it is a second
        # paragraph of the item at the content indent (4) or deeper (5), and it falls outside
        # the item below that indent (3), where the required clause is missing from the item.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, "\n    " + _FIXTURE_JOURNAL_EVERY_ENTRY, "")
            _edit_prompt(root, _FIXTURE_JOURNAL_ADOPTING, _FIXTURE_JOURNAL_ADOPTING + separator +
                         " " * indent + _FIXTURE_JOURNAL_EVERY_ENTRY)
        return build

    def journal_item_in_fence(base):
        # The whole item inside fenced code, where it is literal text, not a list item.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_ITEM, "```text\n" + _FIXTURE_JOURNAL_ITEM + "\n```")

    def journal_step8_head_in_fence(base):
        # A fence left open just before the heading, so the heading sits inside fenced code.
        root = _build_tree(base)
        _edit_prompt(root, _STEP8_HEAD, "```text\n\n" + _STEP8_HEAD)

    def step9_head_indented(spaces):
        # The item moved after a step 9 heading indented by the given count (one to three):
        # Markdown still renders that heading, so a scan that only ends the step at column 0
        # would read past it and find the item inside step 8.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, _FIXTURE_JOURNAL_ITEM + "\n\n" + _FIXTURE_STEP9_HEAD,
                         " " * spaces + _FIXTURE_STEP9_HEAD + "\n\n" + _FIXTURE_JOURNAL_ITEM)
        return build

    def journal_denied_in_continuation(base):
        # The denied clause in a second paragraph of the item, after a blank line: it renders
        # inside step 8 (and inside the item), so it must still be refused.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_ADOPTING, _FIXTURE_JOURNAL_ADOPTING +
                     "\n\n    In short, in a homes-1 origin store a journal entry is a store "
                     "path.")

    def journal_item_before_step8(base):
        # Just before the step: text ahead of the heading is outside step 8, so a scan whose
        # slice started at the document's first line instead of the heading would find it.
        root = _build_tree(base)
        _edit_prompt(root, _STEP8_HEAD + "\n\n" + _FIXTURE_JOURNAL_ITEM,
                     _FIXTURE_JOURNAL_ITEM + "\n\n" + _STEP8_HEAD)

    def journal_step8_duplicated(base):
        # A second, empty copy of the step 8 heading after step 9: a count rule weakened to
        # at-least-once would still scan from the first copy and find exactly one item.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_STEP9_HEAD, _FIXTURE_STEP9_HEAD + "\n\n" + _STEP8_HEAD)

    def item_after_heading(level):
        # A column-0 heading of the given level (one or two) closes step 8 exactly as a
        # level-three heading does, so an item after it sits outside the step.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, _STEP8_HEAD + "\n\n",
                         _STEP8_HEAD + "\n\n" + "#" * level + " Later phases\n\n")
        return build

    def fence_mismatch(opener):
        # A fence whose opener a bare three-backtick line cannot close (a longer run, or the
        # tilde character), left open just before the heading: the delimiter count is even, but
        # the heading is literal text inside the still-open fence.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, _STEP8_HEAD, opener + "text\n```\n\n" + _STEP8_HEAD)
        return build

    def journal_item_indented_code(base):
        # The item re-indented by two more spaces after a new paragraph: with no list item open
        # that deep, Markdown renders the bullet as indented code, so no list item renders.
        root = _build_tree(base)
        reindented = "\n".join("  " + line if line.strip() else line
                               for line in _FIXTURE_JOURNAL_ITEM.split("\n"))
        _edit_prompt(root, _STEP8_HEAD + "\n\n" + _FIXTURE_JOURNAL_ITEM,
                     _STEP8_HEAD + "\n\nThe upgrade checks these locations.\n\n" + reindented)

    def journal_item_in_html_comment(base):
        # The whole item inside an HTML comment block: nothing of it renders.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_ITEM, "<!--\n" + _FIXTURE_JOURNAL_ITEM + "\n-->")

    def journal_conclusion_in_html_comment(base):
        # Only the required store-path conclusion wrapped in an inline HTML comment: rendered
        # output drops it, so inline raw HTML is refused by name.
        root = _build_tree(base)
        _edit_prompt(root, "so in a homes-1 origin store that has not started an adoption a "
                     "journal entry is a store path, not machine-local data.",
                     "<!-- so in a homes-1 origin store that has not started an adoption a "
                     "journal entry is a store path, not machine-local data. -->")

    def setext_heading(marker):
        # A paragraph plus a setext underline just after the step 8 heading: Markdown renders a
        # heading there, which ends step 8 before the item.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, _STEP8_HEAD + "\n\n",
                         _STEP8_HEAD + "\n\nLater phases\n" + marker * 12 + "\n\n")
        return build

    def journal_step9_heading_tab(base):
        # A tab instead of the space after the number-sign run: Markdown still renders the
        # heading, so step 9 begins there and the item after it sits outside step 8.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_ITEM + "\n\n" + _FIXTURE_STEP9_HEAD,
                     "###\tStep 9: fresh initialization\n\n" + _FIXTURE_JOURNAL_ITEM)

    def fence_near_heading(insert):
        # The given text just before the step 8 heading: each variant leaves a fence open there
        # under CommonMark (a closer indented four spaces or led by a tab does not close a
        # fence, and an opener indented four spaces is indented code, not a fence), so the
        # heading renders as fenced-code text, never as a heading.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, _STEP8_HEAD, insert + _STEP8_HEAD)
        return build

    def setext_bullet_underline(base):
        # A paragraph underlined by `- `: an empty list item cannot interrupt a paragraph, so
        # CommonMark reads the dash line as a setext underline and renders a level-two heading,
        # which ends step 8 before the item.
        root = _build_tree(base)
        _edit_prompt(root, _STEP8_HEAD + "\n\n", _STEP8_HEAD + "\n\nLater phases\n- \n\n")

    def step8_in_html_block(base):
        # The heading and the item inside an HTML comment block opened before the heading:
        # nothing between <!-- and --> renders, so no step 8 heading renders at all.
        root = _build_tree(base)
        _edit_prompt(root, _STEP8_HEAD + "\n\n" + _FIXTURE_JOURNAL_ITEM,
                     "<!--\n\n" + _STEP8_HEAD + "\n\n" + _FIXTURE_JOURNAL_ITEM + "\n\n-->")

    def step8_head_in_blockquote(base):
        # The heading inside a blockquote renders as quoted material, not as the top-level step
        # 8 heading.
        root = _build_tree(base)
        _edit_prompt(root, _STEP8_HEAD, "> " + _STEP8_HEAD)

    def cr_hides_step9_heading(base):
        # A lone CR joining a note to the step 9 heading: a byte reading sees one paragraph
        # line, but CommonMark reads the CR as a line ending, so a step 9 heading renders and
        # the item after it sits outside step 8.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_ITEM + "\n\n" + _FIXTURE_STEP9_HEAD,
                     "Note.\r" + _FIXTURE_STEP9_HEAD + "\n\n" + _FIXTURE_JOURNAL_ITEM)

    def cr_opens_fence(base):
        # A lone CR hiding a fence opener at the start of step 8: rendered, the fence swallows
        # the item.
        root = _build_tree(base)
        _edit_prompt(root, _STEP8_HEAD + "\n\n", _STEP8_HEAD + "\n\nNote.\r```\n\n")

    def cr_setext_underline(base):
        # A lone CR hiding a setext underline: rendered, a level-two heading ends step 8 before
        # the item.
        root = _build_tree(base)
        _edit_prompt(root, _STEP8_HEAD + "\n\n", _STEP8_HEAD + "\n\nLater phases\r---\n\n")

    def cr_harmless(base):
        # A CR whose rendered reading is harmless (a soft line break outside step 8): still
        # refused, because the control-character refusal is by presence, not by effect, so this
        # fixture discriminates that refusal alone.
        _build_tree(base, extra_text="A note line.\rIts continuation.")

    def ff_hides_step9_heading(base):
        # A form feed hiding a fence opener and a fence closer: marko reads the form feed as a
        # line ending, so its parse hides the step 9 heading inside fenced code and keeps the
        # item in step 8, while CommonMark reads the form feed as paragraph text, renders the
        # heading, and the item after it sits outside step 8.
        root = _build_tree(base)
        _edit_prompt(root, _STEP8_HEAD + "\n\n" + _FIXTURE_JOURNAL_ITEM,
                     _STEP8_HEAD + "\n\nNote.\f```\n" + _FIXTURE_STEP9_HEAD + "\nx\f```\n\n" +
                     _FIXTURE_JOURNAL_ITEM)

    def control_hides_step9_heading(control):
        # The given control character joining a note to the step 9 heading, mirroring the CR
        # fixture: CommonMark renders one paragraph, so the gate's parse keeps the item in step
        # 8, while a reading that breaks lines on the character (Python's splitlines does for
        # the vertical tab, the C0 information separators, U+0085, U+2028 and U+2029) sees a
        # step 9 heading there, and for NUL the U+FFFD substitution makes rendered text disagree
        # with the raw bytes. Refused by presence, not by effect.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, _FIXTURE_JOURNAL_ITEM + "\n\n" + _FIXTURE_STEP9_HEAD,
                         "Note." + control + _FIXTURE_STEP9_HEAD + "\n\n" +
                         _FIXTURE_JOURNAL_ITEM)
        return build

    def conclusion_in_link_destination(base):
        # The required conclusion moved into a link destination: a destination is not rendered
        # text, so the clause no longer renders inside the item.
        root = _build_tree(base)
        _edit_prompt(root, "so in a homes-1 origin store that has not started an adoption a "
                     "journal entry is a store path, not machine-local data.",
                     "[.](<so in a homes-1 origin store that has not started an adoption a "
                     "journal entry is a store path, not machine-local data.>)")

    def conclusion_in_processing_instruction(base):
        # The required conclusion wrapped in an inline processing instruction: rendered output
        # drops it, exactly as it drops an HTML comment.
        root = _build_tree(base)
        _edit_prompt(root, "so in a homes-1 origin store that has not started an adoption a "
                     "journal entry is a store path, not machine-local data.",
                     "<?x so in a homes-1 origin store that has not started an adoption a "
                     "journal entry is a store path, not machine-local data. ?>")

    def html_block_harmless(base):
        # An HTML comment block inside step 8 that hides no required text: refused by the
        # raw-HTML guard alone, so this fixture discriminates that guard.
        root = _build_tree(base)
        _edit_prompt(root, "\n\n" + _FIXTURE_STEP9_HEAD,
                     "\n\n<!-- a marker that hides nothing -->\n\n" + _FIXTURE_STEP9_HEAD)

    def inline_html_harmless(base):
        # An inline processing instruction appended after a required clause, hiding nothing:
        # refused by the raw-HTML guard alone.
        root = _build_tree(base)
        _edit_prompt(root, "which is this prompt's choice.",
                     "which is this prompt's choice. <?a marker that hides nothing?>")

    def conclusion_in_code_block(code):
        # The required conclusion moved into a code block inside the item (fenced at the item's
        # content indent, or indented four past it): the text renders, but only as code, which
        # is not the prompt stating its rule, so the clause is missing from the item.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, "so in a homes-1 origin store that has not started an adoption "
                         "a journal entry is a store path, not machine-local data.",
                         code.format("so in a homes-1 origin store that has not started an "
                                     "adoption a journal entry is a store path, not "
                                     "machine-local data."))
        return build

    def denied_in_code_block(base):
        # The denied clause inside a fenced code block of step 8: it is absent from the
        # rendered-text scan (code earns no clause credit), so only the raw-source scan of the
        # denied clause refuses it.
        root = _build_tree(base)
        _edit_prompt(root, "\n\n" + _FIXTURE_STEP9_HEAD,
                     "\n\n```text\nIn short, in a homes-1 origin store a journal entry is a "
                     "store path.\n```\n\n" + _FIXTURE_STEP9_HEAD)

    def denied_emphasised(base):
        # The denied clause interleaved with emphasis markers: the raw bytes never spell it, but
        # the rendered text does, so only the rendered-text scan of the denied clause refuses
        # it.
        root = _build_tree(base)
        _edit_prompt(root, "\n\n" + _FIXTURE_STEP9_HEAD,
                     "\n\nIn short, in a homes-1 origin store a *journal* entry is a store "
                     "path.\n\n" + _FIXTURE_STEP9_HEAD)

    def conclusion_in_image_alt(base):
        # The required conclusion moved into image alt text: HTML rendering shows alt text only
        # as an attribute, so the clause no longer renders as prompt text inside the item.
        root = _build_tree(base)
        _edit_prompt(root, "so in a homes-1 origin store that has not started an adoption a "
                     "journal entry is a store path, not machine-local data.",
                     "![so in a homes-1 origin store that has not started an adoption a "
                     "journal entry is a store path, not machine-local data.](x.png)")

    def denied_in_image_alt(base):
        # The denied clause interleaved with emphasis inside image alt text: the raw bytes never
        # spell it, so only the denied-clause scan, which reads alt text, refuses it; this
        # fixture holds the denial as wide as it was when alt text earned clause credit.
        root = _build_tree(base)
        _edit_prompt(root, "\n\n" + _FIXTURE_STEP9_HEAD,
                     "\n\n![In short, in a homes-1 origin store a *journal* entry is a store "
                     "path.](y.png)\n\n" + _FIXTURE_STEP9_HEAD)

    def clause_split_across_blocks(base):
        # The required conclusion split across two paragraphs of the item between "pa" and "th":
        # with block tags read as line breaks each paragraph stays its own line, the
        # whitespace-insensitive comparison reads the break as a space, and the clause does not
        # match; gluing adjacent blocks without a separator would re-assemble it, so this fixture
        # discriminates the block separation in _visible_from_html.
        root = _build_tree(base)
        _edit_prompt(root, "journal entry is a store path, not machine-local data.",
                     "journal entry is a store pa\n\n    th, not machine-local data.")

    def denied_text_in_step8(text):
        # The given paragraph appended inside step 8, just before the step 9 heading.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, "\n\n" + _FIXTURE_STEP9_HEAD,
                         "\n\n" + text + "\n\n" + _FIXTURE_STEP9_HEAD)
        return build

    # The denied clause with one letter written as a numeric character reference: the raw bytes
    # never spell it, but the rendered output decodes the reference, so only the visible-text
    # scan of the denied clause refuses it.
    denied_numeric_entity = denied_text_in_step8(
        "In short, in a homes-1 origin store a journ&#97;l entry is a store path.")

    # One space of the denied clause written as a named reference (a no-break space): rendered,
    # the whitespace-insensitive comparison reads it as a space.
    denied_named_entity = denied_text_in_step8(
        "In short, in a homes-1 origin store a journal entry is a store&nbsp;path.")

    # One word of the denied clause inside a code span: the rendered output still shows the word
    # (a font change hides nothing), so the visible-text scan refuses the clause.
    denied_code_span_word = denied_text_in_step8(
        "In short, in a homes-1 origin store a `journal` entry is a store path.")

    # Two words of the denied clause inside one code span, the same way.
    denied_code_span_words = denied_text_in_step8(
        "In short, in a homes-1 origin store a `journal entry` is a store path.")

    # A character reference that spells no denied clause: decoding references refuses nothing by
    # itself.
    entity_harmless = denied_text_in_step8("A caf&eacute; note sits here.")

    # A code span that spells no denied clause: reading code-span text as rendered refuses
    # nothing by itself.
    code_span_harmless = denied_text_in_step8("A `journal` note sits here.")

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
        # The example URL wrapped in an HTML comment: its bytes still satisfy the raw location
        # scan, but the rendered output shows only two pin spellings, so the rendered side of
        # the spelling count refuses a location a reader never sees. The comment carries an
        # inner ">", which only the comment rule of _visible_from_html spans, so this fixture
        # discriminates that rule from the generic tag rule behind it.
        root = _build_tree(base)
        _edit_prompt(root, "For example, https://example.invalid/" + _FIXTURE_PIN +
                     "/opf/spec/OPF-SPEC.md.",
                     "<!-- note > For example, https://example.invalid/" + _FIXTURE_PIN +
                     "/opf/spec/OPF-SPEC.md. -->")

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
        ("journal-base-paragraph-restored", INVALID, "journal-grading", "_check_journal_grading",
         journal_base_restored),
        ("journal-non-adopting-quote-unqualified", INVALID, "journal-grading",
         "_check_journal_grading", journal_quote_unqualified),
        ("journal-non-adopting-conclusion-removed", INVALID, "journal-grading",
         "_check_journal_grading", journal_conclusion_removed),
        ("journal-adopting-distinction-removed", INVALID, "journal-grading",
         "_check_journal_grading", journal_adopting_removed),
        ("journal-every-entry-rule-removed", INVALID, "journal-grading", "_check_journal_grading",
         journal_every_entry_removed),
        ("journal-unqualified-conclusion-added", INVALID, "journal-grading",
         "_check_journal_grading", journal_unqualified_added),
        ("journal-item-missing", INVALID, "journal-grading", "_check_journal_grading",
         journal_item_missing),
        ("journal-item-duplicate", INVALID, "journal-grading", "_check_journal_grading",
         journal_item_duplicate),
        ("journal-step8-heading-renamed", INVALID, "journal-grading", "_check_journal_grading",
         journal_step8_renamed),
        ("journal-item-after-step8", INVALID, "journal-grading", "_check_journal_grading",
         journal_item_after_step8),
        ("journal-item-under-level-four-heading", VALID, None, None,
         journal_item_under_level_four),
        ("journal-clause-indent-deeper", VALID, None, None, every_entry_moved(3)),
        ("journal-clause-indent-equal", VALID, None, None, every_entry_moved(2)),
        ("journal-clause-indent-shallower", VALID, None, None, every_entry_moved(1)),
        ("journal-clause-after-blank-line", VALID, None, None, every_entry_moved(4, "\n\n")),
        ("journal-clause-after-whitespace-line", VALID, None, None,
         every_entry_moved(4, "\n      \n")),
        ("journal-clause-after-blank-indent-five", VALID, None, None,
         every_entry_moved(5, "\n\n")),
        ("journal-clause-after-blank-outside-item", INVALID, "journal-grading",
         "_check_journal_grading", every_entry_moved(3, "\n\n")),
        ("journal-item-in-fence", INVALID, "journal-grading", "_check_journal_grading",
         journal_item_in_fence),
        ("journal-step8-heading-in-fence", INVALID, "journal-grading", "_check_journal_grading",
         journal_step8_head_in_fence),
        ("journal-step9-heading-indent-one", INVALID, "journal-grading",
         "_check_journal_grading", step9_head_indented(1)),
        ("journal-step9-heading-indent-three", INVALID, "journal-grading",
         "_check_journal_grading", step9_head_indented(3)),
        ("journal-denied-clause-after-blank-line", INVALID, "journal-grading",
         "_check_journal_grading", journal_denied_in_continuation),
        ("journal-item-before-step8", INVALID, "journal-grading", "_check_journal_grading",
         journal_item_before_step8),
        ("journal-step8-heading-duplicated", INVALID, "journal-grading",
         "_check_journal_grading", journal_step8_duplicated),
        ("journal-item-after-level-one-heading", INVALID, "journal-grading",
         "_check_journal_grading", item_after_heading(1)),
        ("journal-item-after-level-two-heading", INVALID, "journal-grading",
         "_check_journal_grading", item_after_heading(2)),
        ("journal-step8-heading-fence-longer-run", INVALID, "journal-grading",
         "_check_journal_grading", fence_mismatch("````")),
        ("journal-step8-heading-fence-tilde-mismatch", INVALID, "journal-grading",
         "_check_journal_grading", fence_mismatch("~~~")),
        ("journal-item-indented-code", INVALID, "journal-grading", "_check_journal_grading",
         journal_item_indented_code),
        ("journal-item-in-html-comment", INVALID, "journal-grading", "_check_journal_grading",
         journal_item_in_html_comment),
        ("journal-conclusion-in-html-comment", INVALID, "journal-grading",
         "_check_journal_grading", journal_conclusion_in_html_comment),
        ("journal-setext-underline-dashes", INVALID, "journal-grading",
         "_check_journal_grading", setext_heading("-")),
        ("journal-setext-underline-equals", INVALID, "journal-grading",
         "_check_journal_grading", setext_heading("=")),
        ("journal-step9-heading-tab", INVALID, "journal-grading", "_check_journal_grading",
         journal_step9_heading_tab),
        ("journal-fence-closer-indented", INVALID, "journal-grading", "_check_journal_grading",
         fence_near_heading("```text\n    ```\n\n")),
        ("journal-fence-closer-tab-led", INVALID, "journal-grading", "_check_journal_grading",
         fence_near_heading("```text\n\t```\n\n")),
        ("journal-fence-opener-indented", INVALID, "journal-grading", "_check_journal_grading",
         fence_near_heading("    ```\n\n```\n\n")),
        ("journal-setext-underline-dash-bullet", INVALID, "journal-grading",
         "_check_journal_grading", setext_bullet_underline),
        ("journal-step8-heading-in-html-block", INVALID, "journal-grading",
         "_check_journal_grading", step8_in_html_block),
        ("journal-step8-heading-in-blockquote", INVALID, "journal-grading",
         "_check_journal_grading", step8_head_in_blockquote),
        ("journal-cr-hides-step9-heading", INVALID, "journal-grading", "_check_journal_grading",
         cr_hides_step9_heading),
        ("journal-cr-opens-fence", INVALID, "journal-grading", "_check_journal_grading",
         cr_opens_fence),
        ("journal-cr-setext-underline", INVALID, "journal-grading", "_check_journal_grading",
         cr_setext_underline),
        ("journal-cr-harmless-rendering", INVALID, "journal-grading", "_check_prompt_controls",
         cr_harmless),
        ("journal-ff-hides-step9-heading", INVALID, "journal-grading", "_check_prompt_controls",
         ff_hides_step9_heading),
        ("journal-vt-hides-step9-heading", INVALID, "journal-grading", "_check_prompt_controls",
         control_hides_step9_heading("\x0b")),
        ("journal-nul-hides-step9-heading", INVALID, "journal-grading", "_check_prompt_controls",
         control_hides_step9_heading("\x00")),
        ("journal-c0-separator-hides-step9-heading", INVALID, "journal-grading",
         "_check_prompt_controls", control_hides_step9_heading("\x1c")),
        ("journal-nel-hides-step9-heading", INVALID, "journal-grading", "_check_prompt_controls",
         control_hides_step9_heading("\x85")),
        ("journal-ls-hides-step9-heading", INVALID, "journal-grading", "_check_prompt_controls",
         control_hides_step9_heading(chr(0x2028))),
        ("journal-ps-hides-step9-heading", INVALID, "journal-grading", "_check_prompt_controls",
         control_hides_step9_heading(chr(0x2029))),
        ("journal-conclusion-in-link-destination", INVALID, "journal-grading",
         "_check_journal_grading", conclusion_in_link_destination),
        ("journal-conclusion-in-processing-instruction", INVALID, "journal-grading",
         "_check_journal_grading", conclusion_in_processing_instruction),
        ("journal-html-block-harmless", INVALID, "journal-grading", "_check_step8_html",
         html_block_harmless),
        ("journal-inline-html-harmless", INVALID, "journal-grading", "_check_step8_html",
         inline_html_harmless),
        ("journal-conclusion-in-fenced-code", INVALID, "journal-grading",
         "_check_journal_grading",
         conclusion_in_code_block("\n\n    ```text\n    {}\n    ```")),
        ("journal-conclusion-in-indented-code", INVALID, "journal-grading",
         "_check_journal_grading", conclusion_in_code_block("\n\n        {}")),
        ("journal-denied-clause-in-code-block", INVALID, "journal-grading",
         "_check_journal_grading", denied_in_code_block),
        ("journal-denied-clause-emphasised", INVALID, "journal-grading",
         "_check_journal_grading", denied_emphasised),
        ("journal-conclusion-in-image-alt", INVALID, "journal-grading",
         "_check_journal_grading", conclusion_in_image_alt),
        ("journal-denied-clause-in-image-alt", INVALID, "journal-grading",
         "_check_journal_grading", denied_in_image_alt),
        ("journal-clause-split-across-blocks", INVALID, "journal-grading",
         "_check_journal_grading", clause_split_across_blocks),
        ("journal-denied-clause-numeric-entity", INVALID, "journal-grading",
         "_check_journal_grading", denied_numeric_entity),
        ("journal-denied-clause-named-entity", INVALID, "journal-grading",
         "_check_journal_grading", denied_named_entity),
        ("journal-denied-clause-code-span-word", INVALID, "journal-grading",
         "_check_journal_grading", denied_code_span_word),
        ("journal-denied-clause-code-span-words", INVALID, "journal-grading",
         "_check_journal_grading", denied_code_span_words),
        ("journal-entity-harmless", VALID, None, None, entity_harmless),
        ("journal-code-span-harmless", VALID, None, None, code_span_harmless),
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
        ("pin-example-url-in-html-comment", INVALID, "pin-shape", "_check_pin_shape",
         example_url_in_html_comment),
    ]


# Each journal-grading fixture that removes or adds one clause, with that clause's name: the
# fixture must turn green when exactly that clause is dropped from the gate, so the clause, and no
# other clause or guard, is what refuses it.
_JOURNAL_CLAUSE_VECTORS = (
    ("journal-non-adopting-quote-unqualified", "non-adopting-grading-quoted"),
    ("journal-non-adopting-conclusion-removed", "non-adopting-conclusion"),
    ("journal-adopting-distinction-removed", "adopting-store-distinction"),
    ("journal-every-entry-rule-removed", "every-entry-counts"),
    ("journal-unqualified-conclusion-added", "unqualified-conclusion"),
    ("journal-clause-after-blank-outside-item", "every-entry-counts"),
    ("journal-conclusion-in-link-destination", "non-adopting-conclusion"),
    ("journal-conclusion-in-fenced-code", "non-adopting-conclusion"),
    ("journal-conclusion-in-indented-code", "non-adopting-conclusion"),
    ("journal-denied-clause-after-blank-line", "unqualified-conclusion"),
    ("journal-denied-clause-in-code-block", "unqualified-conclusion"),
    ("journal-denied-clause-emphasised", "unqualified-conclusion"),
    ("journal-conclusion-in-image-alt", "non-adopting-conclusion"),
    ("journal-denied-clause-in-image-alt", "unqualified-conclusion"),
    ("journal-clause-split-across-blocks", "non-adopting-conclusion"),
    ("journal-denied-clause-numeric-entity", "unqualified-conclusion"),
    ("journal-denied-clause-named-entity", "unqualified-conclusion"),
    ("journal-denied-clause-code-span-word", "unqualified-conclusion"),
    ("journal-denied-clause-code-span-words", "unqualified-conclusion"),
)


def _with_check_disabled(name):
    """Replace the named module-level check with a no-op; returns the restorer. Attribute patching,
    so the disabled run exercises the real _validate with one guard removed."""
    real = globals()[name]

    def restore():
        globals()[name] = real
    globals()[name] = lambda *args, **kwargs: None
    return restore


def _without_journal_clause(name):
    """Drop exactly one named clause from JOURNAL_GRADING or JOURNAL_GRADING_DENIED; returns the
    restorer. A name that matches no clause is a harness error, never a vacuous green run."""
    saved = {key: globals()[key] for key in ("JOURNAL_GRADING", "JOURNAL_GRADING_DENIED")}
    if sum(1 for rows in saved.values() for row in rows if row[0] == name) != 1:
        raise RuntimeError("no single journal-grading clause is named {!r}".format(name))

    def restore():
        globals().update(saved)
    for key, rows in saved.items():
        globals()[key] = tuple(row for row in rows if row[0] != name)
    return restore


def _journal_clause_mutants(tmp, check):
    builders = {name: build for name, _, _, _, build in _vectors()}
    ran = 0
    for vector, clause in _JOURNAL_CLAUSE_VECTORS:
        base = Path(tmp) / ("clause-" + vector)
        base.mkdir()
        builders[vector](base)
        restore = _without_journal_clause(clause)
        try:
            dropped = validate_tree(base)
        finally:
            restore()
        ran += 1
        check(vector + "-without-" + clause, dropped, (VALID, None))
    return ran


def _journal_parse_probe(tmp, check):
    """The journal leg must fail closed where the vendored parser cannot load: the loader seam is
    patched to raise its own fail-closed error, and the result must be CANNOT-EVALUATE under
    journal-parse, never VALID and never a fallback line reading."""
    from unittest.mock import patch
    base = Path(tmp) / "journal-parse-unavailable"
    base.mkdir()
    _build_tree(base)

    def refuse():
        raise _commonmark_headings.HeadingScanError(
            "injected: the vendored parser cannot load",
            _commonmark_headings.REASON_VENDOR_UNREADABLE)

    with patch.object(_commonmark_headings, "_load_marko", refuse):
        got = validate_tree(base)
    check("journal-parse-unavailable", got, (CANNOT_EVALUATE, "journal-parse"))
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
            count += _journal_clause_mutants(tmp, check)
            count += _journal_parse_probe(tmp, check)
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
