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
  pin-shape       the prompt names at least three 40-lowercase-hex commits and they are identical
  pin-section     the prompt carries the pin section between its two fixed headings
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
  journal-grading the prompt carries the step 8 heading exactly once, outside fenced code
                  (fences are tracked by opening character and run length, closed only by a line
                  holding nothing but a run of the same character at least that long, with an
                  unclosed fence open to the end of the document, never by a delimiter-parity
                  count, which a longer run or the other fence character defeats), and that step
                  (the heading to the next column-0 heading of level one to three) carries
                  exactly one list item on `.working/journals/` (its bullet line and the lines
                  indented deeper than the bullet, up to a blank or whitespace-only line); that
                  item carries every named clause of JOURNAL_GRADING (whitespace-insensitively)
                  and nothing in step 8 carries a clause of JOURNAL_GRADING_DENIED, so the item's
                  store-path conclusion stays limited to a store that has not started an
                  adoption, it keeps what the specification says of a store that has, and it
                  keeps the rule that the upgrade check counts every journal entry; every line of
                  step 8 must take one of a closed set of shapes (a blank or whitespace-only
                  line, a column-0 space-led ATX heading of level one to six, a `- ` list bullet
                  or a continuation line at an indent an open list item supports, or a plain
                  paragraph line), and any other shape (a fence delimiter, a tab, an HTML comment
                  opener anywhere in a line, a line opening with <, a setext underline or
                  thematic break, an alternate list marker or blockquote opener, indented code,
                  an indented or malformed heading) is an unsupported form and a finding, refused
                  by name rather than read around, because Markdown renders each of those forms
                  away from what this space-and-line scan reads; the closed set deliberately
                  over-refuses, so a prompt that legitimately needs a refused form inside step 8
                  must extend this gate first

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
2 CANNOT-EVALUATE (unreadable input, a missing pinned commit under full history, a harness error).

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


def _check_pin_shape(commits):
    """At least MIN_PIN_OCCURRENCES 40-hex commit spellings, all identical."""
    _require(len(commits) >= MIN_PIN_OCCURRENCES, "pin-shape",
             "the prompt names the pinned commit {} time(s); expected at least {}".format(
                 len(commits), MIN_PIN_OCCURRENCES))
    _require(len(set(commits)) == 1, "pin-shape",
             "the prompt's pinned-commit spellings disagree: {}".format(sorted(set(commits))))


def _pin_section(prompt_text):
    head = prompt_text.find(_PIN_SECTION_HEAD)
    tail = prompt_text.find(_PIN_SECTION_TAIL)
    _require(0 <= head < tail, "pin-section",
             "the prompt lacks the pin section between {!r} and {!r}".format(
                 _PIN_SECTION_HEAD, _PIN_SECTION_TAIL))
    return prompt_text[head:tail]


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


def _is_fence(line):
    """A fenced-code delimiter line as this gate reads one: optional leading spaces, then a
    backtick or tilde run. Deliberately broader than Markdown's three-character minimum, so a
    malformed near-delimiter is refused rather than read as prose."""
    return line.lstrip(" ").startswith(("```", "~~~"))


_FENCE_OPEN_RE = re.compile(r" *(`{3,}|~{3,})")


def _open_fence_before(lines, stop):
    """The fence still open at the start of line index stop, as (character, run length), or None.
    Fences are tracked the way Markdown closes them, never by delimiter parity: a run of three or
    more backticks or tildes opens a fence, and only a line holding nothing but a run of the SAME
    character at least as LONG closes it, so a longer opening run or the other character is not
    closed by a bare three-backtick line, and an unclosed fence stays open to the end. Leading
    spaces are accepted on both delimiters, broader than Markdown's three-space bound, so a
    deeper-indented near-delimiter still flips the state here and the mismatch is refused rather
    than read around."""
    state = None
    for line in lines[:stop]:
        if state is None:
            match = _FENCE_OPEN_RE.match(line)
            if match is not None:
                run = match.group(1)
                state = (run[0], len(run))
        else:
            char, length = state
            body = line.strip()
            if body and body == char * len(body) and len(body) >= length:
                state = None
    return state


def _step_shapes(step):
    """Refuse every line of step 8 that is not one of the closed set of shapes the shipped prompt
    places there: a blank or whitespace-only line, a column-0 ATX heading of one to six number
    signs then a space (levels one to three never reach here, because they end the step), a `- `
    list bullet at an indent an open list item supports, a continuation line of an open list
    item, or a plain paragraph line. Every other shape is refused by name, because Markdown
    renders those forms (fenced or indented code, raw HTML and comments, setext underlines and
    thematic breaks, blockquotes, alternate list markers, tab-shifted indentation) away from what
    this space-and-line scan reads, so reading around them could pass step 8 text that never
    renders or miss a form that renders as something else. The closed set deliberately
    over-refuses (an emphasis-opening or plus-led paragraph line is refused too): a prompt that
    legitimately needs a refused form inside step 8 must extend this gate first."""
    lists = []  # content indents of the open list items, outermost first
    in_paragraph = False
    for line in step:
        _require("\t" not in line, "journal-grading",
                 "step 8 carries a tab, an unsupported character here: tabs move Markdown's "
                 "column counting away from this scan's space-only indent reading")
        _require("<!--" not in line, "journal-grading",
                 "step 8 carries an HTML comment opener, an unsupported form: rendered output "
                 "drops comment content, so text this scan reads there would never render")
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if not stripped:
            in_paragraph = False
            continue
        _require(not _is_fence(line), "journal-grading",
                 "step 8 carries a fenced-code delimiter, an unsupported form: text inside a "
                 "fence is literal, so this scan could read an item where none renders")
        if stripped.startswith("#"):
            _require(indent == 0, "journal-grading",
                     "step 8 carries an indented heading, an unsupported form: Markdown renders "
                     "it as a heading, but this scan would read it as ordinary step text")
            _require(re.match(r"#{1,6} ", line) is not None, "journal-grading",
                     "step 8 carries a number-sign line that is not a space-led ATX heading of "
                     "level one to six (a bare run, a run past six, or a tab or other character "
                     "after the run), an unsupported form: Markdown can still render it as a "
                     "heading this scan cannot place")
            lists = []
            in_paragraph = False
            continue
        _require(not stripped.startswith("<"), "journal-grading",
                 "step 8 carries a line opening with <, an unsupported form: raw HTML renders "
                 "away from what this scan reads")
        body = stripped.rstrip()
        _require(stripped.startswith("- ")
                 or not (set(body) <= {"-"} or set(body) <= {"="}), "journal-grading",
                 "step 8 carries a dash-only or equals-only line, an unsupported form: a setext "
                 "underline turns the paragraph above it into a heading that would end step 8, "
                 "and a thematic break is not step text")
        _require(stripped[0] not in ">*+", "journal-grading",
                 "step 8 opens a line with {!r}, an unsupported form: a blockquote or an "
                 "alternate list marker renders as a block this scan does not read".format(
                     stripped[0]))
        if stripped.startswith("- "):
            while lists and lists[-1] > indent:
                lists.pop()
            _require(indent <= 3 or (bool(lists) and indent <= lists[-1] + 3),
                     "journal-grading",
                     "step 8 carries a list bullet indented {} space(s) with no open list item "
                     "that deep, which Markdown renders as indented code, not a list "
                     "item".format(indent))
            lists.append(indent + 2)
            in_paragraph = True
            continue
        if indent == 0:
            # A column-0 paragraph line; where it follows a blank line it starts a new block,
            # which closes every open list item.
            if not in_paragraph:
                lists = []
            in_paragraph = True
            continue
        if in_paragraph:
            # A paragraph accepts a continuation line at any indent (indented code cannot
            # interrupt a paragraph), so this line renders as ordinary step text.
            continue
        while lists and lists[-1] > indent:
            lists.pop()
        _require(bool(lists), "journal-grading",
                 "step 8 carries an indented line that starts a block outside any open list "
                 "item, an unsupported form: at four or more spaces Markdown renders it as "
                 "indented code, and this prompt places no shallower stray indented paragraph")
        _require(indent <= lists[-1] + 3, "journal-grading",
                 "step 8 carries a line indented {} space(s) after a blank line, four or more "
                 "past the open list item's content indent of {}, which Markdown renders as "
                 "indented code, not step text".format(indent, lists[-1]))
        in_paragraph = True


def _journal_item(prompt_text):
    """(normalized step 8 list item on `.working/journals/`, normalized step 8 text). The step
    runs from its fixed heading to the next column-0 heading of level three or above; the item is
    its bullet line and every following line indented deeper than the bullet, ending before the
    first blank or whitespace-only line or line indented no deeper than the bullet. This parser
    is not a Markdown parser, so it refuses what it cannot place instead of reading around it:
    the heading must sit outside fenced code (_open_fence_before tracks fences by opening
    character and run length, so a mismatched pair cannot hide the heading behind an even
    delimiter count), and every line of the step must take a shape of the closed grammar in
    _step_shapes, which over-refuses by design. Exactly one heading and exactly one item, or a
    finding."""
    lines = prompt_text.split("\n")
    heads = [i for i, line in enumerate(lines) if line == _STEP8_HEAD]
    _require(len(heads) == 1, "journal-grading",
             "the prompt must carry the heading {!r} exactly once; found {}".format(
                 _STEP8_HEAD, len(heads)))
    open_fence = _open_fence_before(lines, heads[0])
    _require(open_fence is None, "journal-grading",
             "a fence of {!r} opened before the step 8 heading and no later line holds a run of "
             "that character at least that long alone, so the heading sits inside fenced code, "
             "where it is literal text, not a heading".format(
                 open_fence[0] * open_fence[1] if open_fence else ""))
    end = next((i for i in range(heads[0] + 1, len(lines)) if re.match(r"#{1,3} ", lines[i])),
               len(lines))
    step = lines[heads[0] + 1:end]
    _step_shapes(step)
    starts = [i for i, line in enumerate(step) if line.lstrip(" ").startswith(_JOURNAL_ITEM_LEAD)]
    _require(len(starts) == 1, "journal-grading",
             "step 8 must carry exactly one list item opening {!r}; found {}".format(
                 _JOURNAL_ITEM_LEAD, len(starts)))
    bullet = step[starts[0]]
    indent = len(bullet) - len(bullet.lstrip(" "))
    item = [bullet]
    for line in step[starts[0] + 1:]:
        if not line.strip() or len(line) - len(line.lstrip(" ")) <= indent:
            break
        item.append(line)
    return _normalize("\n".join(item)), _normalize("\n".join(step))


def _check_journal_grading(prompt_text):
    item, step = _journal_item(prompt_text)
    for name, clause in JOURNAL_GRADING:
        _require(_normalize(clause) in item, "journal-grading",
                 "the step 8 journal item lacks the required clause {!r}".format(name))
    # The denied clause is refused anywhere in step 8, not only in the item's first block: a
    # continuation paragraph of the item sits past the blank line where the item scan stops, but
    # it still renders inside the step.
    for name, clause in JOURNAL_GRADING_DENIED:
        _require(_normalize(clause) not in step,
                 "journal-grading",
                 "step 8 carries the denied clause {!r}".format(name))


def _validate(root):
    root = Path(root)
    prompt_text = _read_regular(root / "opf" / PROMPT_REL, "prompt").decode(
        "utf-8", errors="backslashreplace")
    commits = _COMMIT_RE.findall(prompt_text)
    _check_pin_shape(commits)
    pin = commits[0] if commits else None
    section = _pin_section(prompt_text)
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
    _check_journal_grading(prompt_text)
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
        # bullet sits at 2), after the given separator.
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
        # The denied clause in a continuation paragraph of the item, past the blank line where
        # the item scan stops; it still renders inside step 8, so it must still be refused.
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
        # Only the required store-path conclusion wrapped in an inline HTML comment: this scan's
        # text still carries the clause, but rendered output drops it.
        root = _build_tree(base)
        _edit_prompt(root, "so in a homes-1 origin store that has not started an adoption a "
                     "journal entry is a store path, not machine-local data.",
                     "<!-- so in a homes-1 origin store that has not started an adoption a "
                     "journal entry is a store path, not machine-local data. -->")

    def setext_heading(marker):
        # A paragraph plus a setext underline just after the step 8 heading: Markdown renders a
        # heading there, where a space-and-line scan reads two ordinary text lines.
        def build(base):
            root = _build_tree(base)
            _edit_prompt(root, _STEP8_HEAD + "\n\n",
                         _STEP8_HEAD + "\n\nLater phases\n" + marker * 12 + "\n\n")
        return build

    def journal_step9_heading_tab(base):
        # A tab instead of the space after the number-sign run: Markdown still renders the
        # heading, so step 9 begins there, but a space-only step-end scan reads past it and
        # finds the item inside step 8.
        root = _build_tree(base)
        _edit_prompt(root, _FIXTURE_JOURNAL_ITEM + "\n\n" + _FIXTURE_STEP9_HEAD,
                     "###\tStep 9: fresh initialization\n\n" + _FIXTURE_JOURNAL_ITEM)

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
        ("journal-clause-indent-equal", INVALID, "journal-grading", "_check_journal_grading",
         every_entry_moved(2)),
        ("journal-clause-indent-shallower", INVALID, "journal-grading", "_check_journal_grading",
         every_entry_moved(1)),
        ("journal-clause-after-blank-line", INVALID, "journal-grading", "_check_journal_grading",
         every_entry_moved(4, "\n\n")),
        ("journal-clause-after-whitespace-line", INVALID, "journal-grading",
         "_check_journal_grading", every_entry_moved(4, "\n      \n")),
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
    ("journal-clause-indent-equal", "every-entry-counts"),
    ("journal-clause-indent-shallower", "every-entry-counts"),
    ("journal-clause-after-blank-line", "every-entry-counts"),
    ("journal-clause-after-whitespace-line", "every-entry-counts"),
    ("journal-denied-clause-after-blank-line", "unqualified-conclusion"),
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
