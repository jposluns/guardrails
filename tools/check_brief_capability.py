#!/usr/bin/env python3
"""Brief capability lint (brief-capability): refuse a brief that asks a worker family to execute
something when the capability table says that family cannot execute.

A brief is the text sent to one worker family. When the family cannot run a command (its row in
.aiqt/core/worker-capabilities.toml says `execute = "no"`), a request to run, build, or measure
something can only come back as an asserted value that nobody measured. This lint reads the brief
before it is sent and refuses it, so the brief is rewritten or the item goes to a family that can
execute. For a family that can execute (`yes` or `read-only-sandbox`) none of the rules below apply
and the result is OK.

RULES (each runs on one sentence; a sentence ends at `.`, `!` or `?` before a capital letter, or at
`;`). A CLAUSE START is the start of a sentence, or the point just after a colon, an opening
parenthesis, `then`, or `and`, once list markers and emphasis are skipped, and again once lead-in words
(please, also, now, first, do, do not, never, must, ...) are skipped. A verb is NEGATED when `not`,
`never`, `no`, `nothing`, `without`, `cannot`, `nor`, or an `n't` form appears in the 60 characters
of the same sentence before it, or `nothing` follows it.

  R1 imperative    An execution verb (run, re-run, execute, invoke, launch, build, compile, install,
                   reproduce, benchmark, profile, measure, inject, trigger, fuzz, bisect, clone,
                   deploy, check out, `time the ...`, and observe in a sentence that also injects) at a
                   clause start, outside a code span, not negated, and not followed by a colon, a hyphen, or a
                   dot or slash joined to a word (so `install.sh` is a file name, not a verb).
                   Hard.
  R2 shell fence   A fenced block tagged as a shell (bash, sh, shell, console, zsh, and the like), or an
                   untagged fence whose first non-blank line starts with `$ `, `git `, `python ` or
                   `python3 `. Hard.
  R3 invocation    A tool invocation: `x.py --flag`, `python3 ... x.py`, `./x.sh`, `bash x.sh`, or a git
                   command that writes or fetches (commit, push, checkout, reset, clone, fetch, ...).
                   Hard only when the same sentence has an R1, R5, or hard R6 hit; otherwise INFO (a
                   mention, such as "CI failed on `x.py --self-test`", is context, not a request).
  R5 result        A reporting verb (report, paste, give, record, capture, attach, print) at a clause
                   start, not negated, followed in the sentence by an execution result: an exit code or
                   status, a return code, the last or final lines, stdout, stderr, counts, seconds,
                   elapsed or wall-clock time, timings, or a run time. Hard.
  R6 outcome       A question opened at a clause start by does, do, did, is, are, will, would, can, or
                   could, whose subject (between that word and the outcome) is a test, self-test, gate,
                   check, suite, CI, a `.py` or `.sh` file, or a `--check` or `--self-test` flag, and
                   whose outcome is pass, succeed, exit N, fail without, turn red, or go or stay green.
                   Hard when the subject names a tool (a `.py` or `.sh` file or one of those flags);
                   otherwise INFO.
  QUOTE DEMOTION   A sentence whose first clause starts with Quote, Find, or Cite, or that contains
                   "quote the", "quote every", or "quote each", asks for source text: its R1, R3, and R6
                   hits are dropped. R2 and R5 are never demoted.

SCOPE (skipped with --unscoped). A family marker `<family> leg:` (any family named in the table)
claims the rest of its paragraph, and, when that paragraph opens with the marker and ends with a
colon, the numbered items that follow it; a heading `## <family> leg` claims its section. Text claimed
by another family is dropped. In the text the linted family claims, "answer items 1, 2 and 4 only"
keeps only those numbered items, and "do not attempt item 3" (also do not answer, never attempt,
skip) drops that item. A numbered item carrying `(<other family> leg)` or `(<other family> only)` is
dropped. A numbered item is a line opening with `N.`, `N)`, `QN.`, or `Item N:`; indented lines below
it, and a fence indented below it, belong to it.

Outcomes, printed one finding per line and then one summary line:

  exit 0  OK               no hard hit (INFO findings may be printed).
  exit 1  REFUSE           at least one hard hit. Rewrite the brief, or send the item to a family that
                           can execute.
  exit 2  CANNOT_EVALUATE  the brief is missing, not a regular file, larger than MAX_BYTES, not UTF-8, or
                           blank; the family is not in the table; the table is missing, unreadable, or
                           malformed; or the arguments are wrong. A brief that cannot be evaluated is
                           never OK.

RESIDUALS (what it does not catch). An implicit request ("each fix has a test that fails without it"),
a verb outside the list, adversarial wording, and a request split across sentences are not caught. A
negation in the 60 characters before a verb also hides a request joined to it by a comma ("do not
edit files, run the tests"). Quote demotion hides an execution verb inside a quote request ("quote the
line, then run it"). Scope is lexical: item numbers are matched wherever they appear, so two numbered
lists in one brief share their numbers, and a family addressed without the `leg` marker ("Claude: run
...") is linted as if addressed to the linted family. The lint reads only the bytes it is given: run it
on the final per-family bytes that are sent, since a brief composed after the lint runs is not
covered. Run alone, it is advisory; it blocks only where the sender refuses to send on exit 1 or 2.

Usage:
  check_brief_capability.py                (no brief: validate the shipped capability table, exit 0 or 2)
  check_brief_capability.py --family NAME [--unscoped] [--table PATH] BRIEF
  check_brief_capability.py --self-test
  check_brief_capability.py --execution-report ABS_PATH   (the self-test, as the execution gate runs it)
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_brief_capability.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import bisect
import contextlib
import io
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    sys.stderr.write(
        "error: check_brief_capability.py cannot import tomllib, part of the Python standard library; "
        "this installation is incomplete. Nothing was run (cannot evaluate).\n")
    raise SystemExit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _selftest_exit_report  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TABLE_REL = ".aiqt/core/worker-capabilities.toml"
EXIT_OK, EXIT_REFUSE, EXIT_CANNOT_EVALUATE = 0, 1, 2
MAX_BYTES = 1 << 20
READ_CHUNK = 1 << 16
# Non-blocking, so a FIFO opens at once and is then refused by type; close-on-exec.
OPEN_FLAGS = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
TABLE_VOCABULARY = dict(
    execute=("yes", "read-only-sandbox", "no"),
    network=("yes", "no"),
    scratch=("persistent", "per-command", "none"),
    handback=("yes", "no"),
)
TABLE_VOCABULARY["repo-read"] = ("path",)
TABLE_VOCABULARY["git-read"] = ("yes", "no", "undeclared")
FAMILY_NAME_RE = re.compile(r"[a-z][a-z0-9-]*\Z")
NEGATION_WINDOW = 60
EXCERPT_CHARS = 120


class CannotEvaluate(Exception):
    pass


class Block:
    """A run of brief lines: kind "prose" (a paragraph, a heading, or a numbered item) or "fence"."""
    __slots__ = ("kind", "start", "lines", "item", "tag", "heading", "masked")

    def __init__(self, kind, start, item=None, tag="", heading=0):
        self.kind, self.start, self.item, self.tag, self.heading = kind, start, item, tag, heading
        self.lines = []      # [(line number, text)]; a fence holds its body lines only
        self.masked = None   # [start, end) ranges of the joined text that another family claims

    def text(self):
        return "\n".join(line for _, line in self.lines)


# ---------- inputs ----------

def _read_regular(path, what):
    """The bytes of a regular file of at most MAX_BYTES, or CannotEvaluate."""
    try:
        fd = os.open(path, OPEN_FLAGS)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot open {}: {}".format(what, path, exc.strerror or exc))
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CannotEvaluate("{}: {} is not a regular file".format(what, path))
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise CannotEvaluate("{}: {} is larger than {} bytes".format(what, path, MAX_BYTES))
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise CannotEvaluate("{}: cannot read {}: {}".format(what, path, exc.strerror or exc))
    finally:
        os.close(fd)


def parse_table(data):
    """The table as family -> (key -> value), or CannotEvaluate on anything outside its schema."""
    try:
        doc = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise CannotEvaluate("capability table: not valid UTF-8 TOML: {}".format(exc))
    if sorted(doc) != ["family", "format-version"] or doc["format-version"] != 1:
        raise CannotEvaluate("capability table: the top level must be exactly format-version = 1 and "
                             "[[family]] rows")
    rows = doc["family"]
    if not isinstance(rows, list) or not rows:
        raise CannotEvaluate("capability table: no [[family]] row")
    keys = sorted(["name"] + list(TABLE_VOCABULARY))
    table = dict()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or sorted(row) != keys:
            raise CannotEvaluate("capability table: family row {} must have exactly the keys {}".format(
                index, ", ".join(keys)))
        name = row["name"]
        if not isinstance(name, str) or not FAMILY_NAME_RE.match(name):
            raise CannotEvaluate("capability table: family row {} has an invalid name".format(index))
        if name in table:
            raise CannotEvaluate("capability table: family {!r} appears twice".format(name))
        for key, allowed in TABLE_VOCABULARY.items():
            if row[key] not in allowed:
                raise CannotEvaluate("capability table: family {!r} has {} = {!r}, not one of {}".format(
                    name, key, row[key], ", ".join(allowed)))
        table[name] = dict((key, row[key]) for key in TABLE_VOCABULARY)
    return table


def load_table(path):
    return parse_table(_read_regular(path, "capability table"))


def decode_brief(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("brief: not valid UTF-8 ({})".format(exc.reason))
    if not text.strip():
        raise CannotEvaluate("brief: blank")
    return text


# ---------- structure ----------

FENCE_OPEN_RE = re.compile(r"( {0,3})(`{3,}|~{3,})(.*)\Z")
HEADING_RE = re.compile(r" {0,3}(#{1,6})(?:\s|\Z)")
ITEM_RE = re.compile(r" {0,3}(?:[-*+]\s+)?(?:\*\*|__)?(?:Q|Item\s+|Question\s+)?(\d{1,3})(?:[.)]|:(?=\s))"
                     r"(?:\*\*|__)?(?=\s|\Z)", re.I)


def _fence_closes(line, opening):
    """Whether `line` closes a fence opened by the run `opening` (the same character, at least as many)."""
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return False
    run = len(stripped) - len(stripped.lstrip(opening[0]))
    return run >= len(opening) and not stripped[run:].strip()


def parse_blocks(text):
    """The brief as a list of Blocks: fences, headings, numbered items, and paragraphs."""
    blocks, current, item, fence, opening = [], None, None, None, ""
    for number, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        indented = line[:1] in (" ", "\t")
        if fence is not None:
            if _fence_closes(line, opening):
                fence = None
            else:
                fence.lines.append((number, line))
            continue
        opened = FENCE_OPEN_RE.match(line)
        if opened and not (opened.group(2)[0] == "`" and "`" in opened.group(3)):
            if not (indented and item is not None):
                item = None
            info = opened.group(3).strip().split()
            opening = opened.group(2)
            fence = Block("fence", number, item=item, tag=info[0].lower() if info else "")
            blocks.append(fence)
            current = None
            continue
        if not line.strip():
            current, item = None, None
            continue
        heading = HEADING_RE.match(line)
        if heading:
            current, item = None, None
            block = Block("prose", number, heading=len(heading.group(1)))
            block.lines.append((number, line))
            blocks.append(block)
            continue
        numbered = ITEM_RE.match(line)
        if numbered:
            item = int(numbered.group(1))
            current = Block("prose", number, item=item)
            current.lines.append((number, line))
            blocks.append(current)
            continue
        if current is not None and (current.item is None or indented):
            current.lines.append((number, line))
            continue
        if not indented:
            item = None
        current = Block("prose", number, item=item)
        current.lines.append((number, line))
        blocks.append(current)
    return blocks


# ---------- scope ----------

ITEM_LIST = (r"Q?\d{1,3}(?:\s*(?:,\s*(?:and\s+|or\s+)?|\band\s+|\bor\s+|-|\bto\s+|\bthrough\s+)\s*"
             r"Q?\d{1,3})*")
ONLY_RE = re.compile(r"\banswer\s+(?:only\s+)?(?:items?|questions?)\s+(" + ITEM_LIST + r")(\s+only)?", re.I)
ANSWER_ONLY_RE = re.compile(r"\banswer\s+only\s+(?:items?|questions?)\s+(" + ITEM_LIST + ")", re.I)
EXCLUDE_RE = re.compile(r"\b(?:(?:do\s+not|don't|never)\s+(?:attempt|answer)|skip)\s+(?:items?|questions?)\s+("
                        + ITEM_LIST + ")", re.I)
LEG_RE = re.compile(r"(?<![\w-])([A-Za-z][A-Za-z0-9-]*)\s+leg\s*:", re.I)
LEG_HEADING_RE = re.compile(r" {0,3}#{1,6}\s+(?:the\s+)?([A-Za-z][A-Za-z0-9-]*)\s+leg\b", re.I)
TAIL_MARKER_RE = re.compile(r"\((?:for\s+)?(?:the\s+)?([A-Za-z][A-Za-z0-9-]*)\s+(?:leg|only)\)", re.I)


def parse_item_list(spec):
    """The item numbers of "1, 2 and 4", "1-3", "Q1 to Q3"."""
    numbers = set()
    tokens = re.findall(r"\d{1,3}|-|\bto\b|\bthrough\b", spec, re.I)
    index = 0
    while index < len(tokens):
        first = int(tokens[index])
        if index + 2 < len(tokens) and not tokens[index + 1].isdigit():
            numbers.update(range(first, int(tokens[index + 2]) + 1))
            index += 3
        else:
            numbers.add(first)
            index += 1
    return numbers


def scope_directives(text):
    """(kept, dropped): the item numbers the linted family's own scope text keeps (None: every item) and
    the ones it drops."""
    kept = None
    for match in ONLY_RE.finditer(text):
        if match.group(2):
            kept = (kept or set()) | parse_item_list(match.group(1))
    for match in ANSWER_ONLY_RE.finditer(text):
        kept = (kept or set()) | parse_item_list(match.group(1))
    dropped = set()
    for match in EXCLUDE_RE.finditer(text):
        dropped |= parse_item_list(match.group(1))
    return kept, dropped


def leg_spans(blocks, families):
    """[(block index, start offset, family, extent)] for every family marker. The extent is "section" for
    a heading marker (offset 0), "items" for a paragraph that opens with the marker and ends with a colon,
    and "" otherwise."""
    spans = []
    for index, block in enumerate(blocks):
        if block.kind != "prose":
            continue
        text = block.text()
        if block.heading:
            found = LEG_HEADING_RE.match(text)
            if found and found.group(1).lower() in families:
                spans.append((index, 0, found.group(1).lower(), "section"))
            continue
        for found in LEG_RE.finditer(text):
            name = found.group(1).lower()
            if name not in families:
                continue
            leading = not re.sub(r"[\s*_>#-]", "", text[:found.start()])
            spans.append((index, found.start(), name,
                          "items" if leading and text.rstrip().endswith(":") else ""))
    return spans


def tail_marker_family(text, families):
    """The family a `(<family> leg)` or `(<family> only)` marker in a numbered item names, or None."""
    for found in TAIL_MARKER_RE.finditer(text):
        if found.group(1).lower() in families:
            return found.group(1).lower()
    return None


def apply_scope(blocks, family, families):
    """The blocks addressed to the linted family: text another family claims masked or dropped, and the
    numbered items the family's own scope text excludes dropped."""
    for block in blocks:
        block.masked = []
    spans = leg_spans(blocks, families)
    own_text, claimed = [], dict()   # claimed: block index -> the family that claims the whole block
    for index, start, name, extent in spans:
        block = blocks[index]
        if extent == "section":
            claimed[index] = name
            for later in range(index + 1, len(blocks)):
                if blocks[later].heading and blocks[later].heading <= block.heading:
                    break
                claimed[later] = name
            continue
        text = block.text()
        # a later marker in the same paragraph ends this one's span
        ends = [other for i, other, _, kind in spans if i == index and kind != "section" and other > start]
        end = min(ends) if ends else len(text)
        if name == family:
            own_text.append(text[start:end])
        else:
            block.masked.append((start, end))
        if extent == "items":
            for later in range(index + 1, len(blocks)):
                if blocks[later].item is None:
                    break
                claimed.setdefault(later, name)
    for index, name in claimed.items():
        if name == family:
            own_text.append(blocks[index].text())
    kept, dropped = scope_directives("\n".join(own_text))
    scoped = []
    for index, block in enumerate(blocks):
        owner = claimed.get(index)
        if owner is not None and owner != family:
            continue
        if block.item is not None:
            if (kept is not None and block.item not in kept) or block.item in dropped:
                continue
            marker = tail_marker_family(block.text(), families) if block.kind == "prose" else None
            if marker is not None and marker != family:
                continue
        scoped.append(block)
    return scoped


# ---------- sentences and clauses ----------

SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?])\s+(?=[*_\"'`(\[]*[A-Z])|;\s+")
MARKUP_RE = re.compile(r"(?:\s|[>*_#]|[-+](?=\s)|(?:Q|Item\s+|Question\s+)?\d{1,3}(?:[.)]|:)(?=\s))*", re.I)
LEAD_IN_RE = re.compile(r"(?:(?:please|also|then|now|first|next|finally|and|or|again|separately|additionally|"
                        r"instead|always|just|do\s+not|don't|do|never|you\s+must|must|you\s+should|should|"
                        r"you\s+may|may|you\s+can)\b[\s,]*)*", re.I)
CLAUSE_BREAK_RE = re.compile(r"(?::|\(|\band\b|\bthen\b)\s*", re.I)
CODE_SPAN_RE = re.compile(r"(`+)(?:(?!\1).)+?\1", re.S)
NEGATION_RE = re.compile(r"\b(?:not|never|no|nothing|without|cannot|nor)\b|n't\b", re.I)
NEGATED_AFTER_RE = re.compile(r"\s+nothing\b", re.I)


def sentences(text):
    """[(offset, sentence)] of a paragraph's text (newlines already turned into spaces)."""
    out, start = [], 0
    for found in SENTENCE_BREAK_RE.finditer(text):
        out.append((start, text[start:found.start()]))
        start = found.end()
    out.append((start, text[start:]))
    return [(offset, sentence) for offset, sentence in out if sentence.strip()]


def clause_starts(sentence):
    """Offsets in the sentence where a clause begins: after markup, and again after its lead-in words."""
    starts = set()
    for raw in [0] + [found.end() for found in CLAUSE_BREAK_RE.finditer(sentence)]:
        position = MARKUP_RE.match(sentence, raw).end()
        starts.add(position)
        starts.add(LEAD_IN_RE.match(sentence, position).end())
    return sorted(starts)


def code_spans(sentence):
    return [found.span() for found in CODE_SPAN_RE.finditer(sentence)]


def _in_spans(position, spans):
    return any(start <= position < end for start, end in spans)


def negated(sentence, start, end):
    """Whether the verb at [start, end) is negated: a negation in the NEGATION_WINDOW characters before
    it (within the sentence), or `nothing` right after it."""
    if NEGATION_RE.search(sentence[max(0, start - NEGATION_WINDOW):start]):
        return True
    return bool(NEGATED_AFTER_RE.match(sentence, end))


# ---------- rules ----------

R1_VERB_RE = re.compile(r"(?:re-?run|run|re-?execute|execute|invoke|launch|re-?build|build|compile|install|"
                        r"reproduce|benchmark|profile|measure|inject|trigger|fuzz|bisect|clone|deploy|"
                        r"check\s+out|time(?=\s+(?:the|each|every|it|how)\b)|observe)\b(?!\s*:|-|[./]\w)", re.I)
SHELL_TAGS = frozenset(("bash", "sh", "shell", "console", "zsh", "fish", "ksh", "dash", "shell-session",
                        "shellsession", "sh-session", "terminal", "powershell", "pwsh", "ps1", "cmd", "bat",
                        "batch"))
SHELL_FIRST_LINE_RE = re.compile(r"\s*(?:\$\s|git\s|python3?\s)")
R3_RE = re.compile(r"\bpython3?(?:\s+-[A-Za-z]+)*\s+[\w./-]+\.py\b"
                   r"|[\w./-]+\.(?:py|sh)\s+--?[A-Za-z][\w-]*"
                   r"|(?<![\w/.])\./[\w./-]+\.sh\b"
                   r"|\bbash\s+[\w./-]+\.sh\b"
                   r"|\bgit\s+(?:-[Cc]\s+\S+\s+)*(?:commit|push|checkout|switch|reset|merge|rebase|cherry-pick|"
                   r"revert|stash|apply|am|tag|clean|rm|mv|add|restore|init|clone|fetch|pull|worktree\s+add)\b")
R5_VERB_RE = re.compile(r"(?:report|paste|give|record|capture|attach|print)\b", re.I)
R5_RESULT_RE = re.compile(r"\b(?:exit\s+(?:codes?|status(?:es)?)|return\s*codes?|returncodes?|"
                          r"(?:last|final)\s+(?:\d+\s+)?lines|stdout|stderr|counts?|seconds|"
                          r"wall[\s-]?clock|elapsed|timings?|run\s*times?)\b", re.I)
R6_AUX_RE = re.compile(r"(?:does|do|did|is|are|will|would|can|could)\b", re.I)
R6_OUTCOME_RE = re.compile(r"\b(?:pass(?:es)?(?=\s*(?:[?.,;)]|\Z|\b(?:and|or|on|at|under|with|without|when|"
                           r"after|before|in|now|again|cleanly|locally|still|both)\b))"
                           r"|succeeds?\b|exits?\s+(?:with\s+)?(?:(?:exit\s+)?(?:code|status)\s+)?\d+\b"
                           r"|fails?\s+without\b|turns?\s+red\b|go(?:es)?\s+green\b|stays?\s+green\b)", re.I)
R6_SUBJECT_RE = re.compile(r"\b(?:tests?|self-tests?|gates?|checks?|suites?|CI)\b|--(?:check|self-test)\b"
                           r"|[\w./-]+\.(?:py|sh)\b", re.I)
NAMED_TOOL_RE = re.compile(r"[\w./-]+\.(?:py|sh)\b|--(?:check|self-test)\b")
QUOTE_START_RE = re.compile(r"(?:quote|find|cite)\b", re.I)
QUOTE_DIRECTIVE_RE = re.compile(r"\bquote\s+(?:the|every|each)\b", re.I)


def rule_r1(sentence, starts, spans):
    """[(offset, verb)]: an execution verb at a clause start, outside a code span, not negated."""
    hits = []
    for start in starts:
        found = R1_VERB_RE.match(sentence, start)
        if not found or _in_spans(start, spans) or negated(sentence, found.start(), found.end()):
            continue
        if found.group(0).lower() == "observe" and not re.search(r"\binject", sentence, re.I):
            continue
        hits.append((found.start(), found.group(0)))
    return hits


def rule_r2(block):
    """Whether a fence is a shell fence: a shell tag, or an untagged fence opening with a command."""
    if block.tag in SHELL_TAGS:
        return True
    if block.tag:
        return False
    for _, line in block.lines:
        if line.strip():
            return bool(SHELL_FIRST_LINE_RE.match(line))
    return False


def rule_r3(sentence):
    """[(offset, invocation)]: every tool invocation in the sentence, code spans included."""
    return [(found.start(), found.group(0)) for found in R3_RE.finditer(sentence)]


def rule_r5(sentence, starts):
    """[(offset, verb)]: a reporting verb at a clause start, not negated, then an execution result."""
    hits = []
    for start in starts:
        found = R5_VERB_RE.match(sentence, start)
        if not found or negated(sentence, found.start(), found.end()):
            continue
        if R5_RESULT_RE.search(sentence, found.end()):
            hits.append((found.start(), found.group(0)))
    return hits


def rule_r6(sentence, starts):
    """[(offset, names a tool)]: an outcome question about a test, a gate, or a tool."""
    hits = []
    for start in starts:
        aux = R6_AUX_RE.match(sentence, start)
        if not aux:
            continue
        outcome = R6_OUTCOME_RE.search(sentence, aux.end())
        if not outcome:
            continue
        subject = sentence[aux.end():outcome.start()]
        if R6_SUBJECT_RE.search(subject):
            hits.append((aux.start(), bool(NAMED_TOOL_RE.search(subject))))
    return hits


def quote_demoted(sentence):
    """Whether the sentence asks for source text: its first clause opens with Quote, Find, or Cite (lead-in
    words skipped), or it says "quote the", "quote every", or "quote each"."""
    position = MARKUP_RE.match(sentence).end()
    for start in (position, LEAD_IN_RE.match(sentence, position).end()):
        if QUOTE_START_RE.match(sentence, start):
            return True
    return bool(QUOTE_DIRECTIVE_RE.search(sentence))


# ---------- evaluation ----------

def _excerpt(text):
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS - 3] + "..."


def lint_sentence(sentence, line):
    """[(rule, severity, line, message, excerpt)] for one sentence."""
    starts = clause_starts(sentence)
    demoted = quote_demoted(sentence)
    r1 = [] if demoted else rule_r1(sentence, starts, code_spans(sentence))
    r5 = rule_r5(sentence, starts)
    r6 = [] if demoted else rule_r6(sentence, starts)
    r3 = [] if demoted else rule_r3(sentence)
    request = bool(r1 or r5 or any(named for _, named in r6))
    excerpt = _excerpt(sentence)
    findings = []
    for _, verb in r1:
        findings.append(("R1", "HARD", line, "execution verb {!r} at a clause start".format(verb), excerpt))
    for _, verb in r5:
        findings.append(("R5", "HARD", line, "asks to {} an execution result".format(verb.lower()), excerpt))
    for _, named in r6:
        findings.append(("R6", "HARD" if named else "INFO", line,
                         "asks for a test outcome" + (" of a named tool" if named else ""), excerpt))
    for _, call in r3:
        findings.append(("R3", "HARD" if request else "INFO", line,
                         "tool invocation {!r} {}".format(call, "in a request" if request else "mentioned"),
                         excerpt))
    return findings


def _masked_text(block):
    text = block.text()
    for start, end in block.masked or ():
        text = text[:start] + " " * (end - start) + text[end:]
    return text


def can_execute(row):
    """Whether a family's table row lets it run commands; the rules apply only to one that cannot."""
    return row["execute"] != "no"


def evaluate(data, family, table, scoped=True):
    """(exit code, findings) for a brief's bytes addressed to `family`, or CannotEvaluate. Findings are
    (rule, severity, line, message, excerpt), in brief order."""
    if family not in table:
        raise CannotEvaluate("family {!r} is not in the capability table (known: {})".format(
            family, ", ".join(sorted(table))))
    text = decode_brief(data)
    if can_execute(table[family]):
        return EXIT_OK, []
    blocks = parse_blocks(text)
    if scoped:
        blocks = apply_scope(blocks, family, set(table))
    findings = []
    for block in blocks:
        if block.kind == "fence":
            if rule_r2(block):
                first = next((line for _, line in block.lines if line.strip()), "")
                findings.append(("R2", "HARD", block.start, "shell fence ({})".format(block.tag or "untagged"),
                                 _excerpt(first)))
            continue
        line_starts, offset = [], 0
        for _, line in block.lines:
            line_starts.append(offset)
            offset += len(line) + 1
        for start, sentence in sentences(_masked_text(block).replace("\n", " ")):
            line = block.lines[bisect.bisect_right(line_starts, start) - 1][0]
            findings.extend(lint_sentence(sentence, line))
    hard = any(severity == "HARD" for _, severity, _, _, _ in findings)
    return (EXIT_REFUSE if hard else EXIT_OK), findings


def run(brief_path, family, table_path, scoped=True):
    try:
        table = load_table(table_path)
        code, findings = evaluate(_read_regular(brief_path, "brief"), family, table, scoped)
    except CannotEvaluate as exc:
        print("CANNOT_EVALUATE: {}".format(exc))
        return EXIT_CANNOT_EVALUATE
    for rule, severity, line, message, excerpt in findings:
        print("{}:{}: {} {}: {}: {}".format(brief_path, line, severity, rule, message, excerpt))
    hard = [rule for rule, severity, _, _, _ in findings if severity == "HARD"]
    if code == EXIT_REFUSE:
        print("REFUSE: family {} cannot execute and the brief asks it to ({} hard hit(s): {}); rewrite the "
              "brief or send these items to a family that can execute".format(
                  family, len(hard), ", ".join("{} x{}".format(r, hard.count(r)) for r in sorted(set(hard)))))
    elif can_execute(table[family]):
        print("OK: family {} can execute ({}); the execution rules do not apply".format(
            family, table[family]["execute"]))
    else:
        print("OK: family {}: no hard hit ({} info)".format(family, len(findings)))
    return code


USAGE = ("usage: check_brief_capability.py --family NAME [--unscoped] [--table PATH] BRIEF\n"
         "       check_brief_capability.py --self-test | --execution-report ABS_PATH")


def check_table(path):
    """The repository-level run: the capability table parses and holds only known capabilities."""
    try:
        table = load_table(path)
    except CannotEvaluate as exc:
        print("CANNOT_EVALUATE: {}".format(exc))
        return EXIT_CANNOT_EVALUATE
    print("OK: capability table valid ({} families); pass --family NAME BRIEF to lint a brief".format(len(table)))
    return EXIT_OK


def main(argv):
    if not argv:
        return check_table(ROOT / TABLE_REL)
    if argv == ["--self-test"]:
        return self_test()
    if len(argv) == 2 and argv[0] == "--execution-report" and os.path.isabs(argv[1]):
        return self_test(argv[1])
    family, table_path, scoped, paths = None, ROOT / TABLE_REL, True, []
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--family" and args and family is None:
            family = args.pop(0)
        elif arg == "--table" and args:
            table_path = Path(args.pop(0))
        elif arg == "--unscoped":
            scoped = False
        elif arg.startswith("-"):
            paths = None
            break
        else:
            paths.append(arg)
    if family is None or paths is None or len(paths) != 1:
        print("CANNOT_EVALUATE: wrong arguments\n" + USAGE, file=sys.stderr)
        return EXIT_CANNOT_EVALUATE
    return run(paths[0], family, table_path, scoped)


# SELF-TEST: production code ends here. Every fixture below is synthetic (no real brief text, path, or
# revision), addressed to the fixture families alpha (executes), beta (executes in a read-only sandbox),
# and gamma (cannot execute). Each case asserts through the instrumented check() choke point, and the
# executed id set is reconciled against tools/selftest_checks.toml (suite brief-capability-selftest), both
# in-run and by tools/check_selftest_execution.py. The revert/ cases patch one rule out of this module's
# globals (no code is generated or evaluated) and require its fixture to flip.

SUITE_ID = "brief-capability-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
FAILURES = []
EXECUTED = []
_EXECUTED_SET = set()


def _family_row(name, execute, network, git_read, scratch, handback):
    return ('[[family]]\nname = "%s"\nexecute = "%s"\nnetwork = "%s"\nrepo-read = "path"\ngit-read = "%s"\n'
            'scratch = "%s"\nhandback = "%s"\n' % (name, execute, network, git_read, scratch, handback))


TABLE_FIXTURE = ("format-version = 1\n"
                 + _family_row("alpha", "yes", "yes", "yes", "persistent", "yes")
                 + _family_row("beta", "read-only-sandbox", "no", "yes", "per-command", "no")
                 + _family_row("gamma", "no", "no", "undeclared", "none", "no"))


def check(name, got, want):
    if name in _EXECUTED_SET:
        print("SELF-TEST HARNESS ERROR: duplicate check id {!r}".format(name), file=sys.stderr)
        sys.exit(2)
    _EXECUTED_SET.add(name)
    EXECUTED.append(name)
    if got != want:
        FAILURES.append("{}: got {!r}, want {!r}".format(name, got, want))


@contextlib.contextmanager
def _patched(**replacements):
    """Replace module-level names (a rule patched out) for the duration of the block, then restore them."""
    names = globals()
    saved = dict((name, names[name]) for name in replacements)
    names.update(replacements)
    try:
        yield
    finally:
        names.update(saved)


def _outcome(text, family="gamma", scoped=True):
    """(exit code, sorted distinct (rule, severity)) for a synthetic brief, or "CannotEvaluate"."""
    table = parse_table(TABLE_FIXTURE.encode("utf-8"))
    try:
        code, findings = evaluate(text.encode("utf-8") if isinstance(text, str) else text, family, table,
                                  scoped)
    except CannotEvaluate:
        return "CannotEvaluate"
    return code, sorted(set((rule, severity) for rule, severity, _, _, _ in findings))


def _table_outcome(text):
    try:
        return sorted(parse_table(text.encode("utf-8")))
    except CannotEvaluate:
        return "CannotEvaluate"


def _quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args)


def _expected_check_ids():
    try:
        with open(CHECKS_MANIFEST, "rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc), file=sys.stderr)
        return None
    for row in data.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and ids and all(isinstance(item, str) and item for item in ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(SUITE_ID, CHECKS_MANIFEST),
          file=sys.stderr)
    return None


RUN_ITEM = "3. Run `t.py` on a snapshot of the change.\n"
NEGATED = "Do not run code or git.\n"
CLAUSE_MID = "Describe the build step of the parser.\n"
BASH_FENCE = "Steps:\n\n```bash\npython3 t.py\n```\n"
UNTAGGED_FENCE = "Steps:\n\n```\n$ git status\n```\n"
QUOTE_SELF_TEST = "Quote the code that runs `t.py --self-test`.\n"
MENTION = "CI failed on `t.py --self-test` at the parent.\n"
RESULT = "Report the exit code and last lines.\n"
RESULT_NEGATED = "Never report an exit code you did not observe.\n"
OUTCOME_NAMED = "5. Do `a.py` and `b.py --check` pass?\n"
OUTCOME_UNNAMED = "Does the test pass?\n"
SCOPED = ("1. Is the helper exported?\n2. Is the table sorted?\n3. Run `t.py --self-test`; report the exit "
          "code.\n\nGamma leg: answer items 1 and 2 only.\n")
NOT_ATTEMPT = ("1. Is the helper exported?\n2. Run `t.py --self-test`.\n\n"
               "Gamma leg: answer from source. Do not attempt item 2.\n")
OTHER_LEG = "1. Is the helper exported?\n\nAlpha leg: run `t.py --self-test` and report the exit code.\n"
OTHER_LEG_ITEMS = "Alpha leg:\n1. Run `t.py --self-test`.\n2. Build the docs.\n\nQuote the helper.\n"
OTHER_SECTION = ("# Brief\n\nQuote the helper.\n\n## Alpha leg\n\nRun `t.py --self-test`.\n\n## Done\n\n"
                 "Nothing else.\n")
TAIL_MARKER = "1. Is the helper exported?\n2. Run `t.py --self-test` (alpha leg).\n"
OUTCOME_FLAG = "Does `a.py --check` pass?\n"


def self_test(report_path=None):
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band.
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    # ---------- the capability table ----------
    try:
        shipped = dict((name, row["execute"]) for name, row in load_table(ROOT / TABLE_REL).items())
    except CannotEvaluate as exc:
        shipped = str(exc)
    check("table/shipped-table-loads", shipped, dict(claude="yes", codex="read-only-sandbox", gemini="no"))
    check("table/fixture-loads", _table_outcome(TABLE_FIXTURE), ["alpha", "beta", "gamma"])
    check("table/unknown-key-2",
          _table_outcome(TABLE_FIXTURE.replace('scratch = "none"\n', 'scratch = "none"\ncolour = "x"\n')),
          "CannotEvaluate")
    check("table/missing-key-2", _table_outcome(TABLE_FIXTURE.replace('network = "no"\n', "", 1)),
          "CannotEvaluate")
    check("table/value-outside-vocabulary-2",
          _table_outcome(TABLE_FIXTURE.replace('execute = "no"', 'execute = "sometimes"')), "CannotEvaluate")
    check("table/duplicate-family-2", _table_outcome(TABLE_FIXTURE.replace('"beta"', '"alpha"')),
          "CannotEvaluate")
    check("table/wrong-format-version-2",
          _table_outcome(TABLE_FIXTURE.replace("format-version = 1", "format-version = 2")), "CannotEvaluate")
    check("table/not-toml-2", _table_outcome("[[family]\n"), "CannotEvaluate")
    # ---------- the rules, addressed to a family that cannot execute ----------
    check("r1/run-item-refused", _outcome(RUN_ITEM), (1, [("R1", "HARD")]))
    check("r1/negated-ok", _outcome(NEGATED), (0, []))
    check("r1/mid-clause-verb-ok", _outcome(CLAUSE_MID), (0, []))
    check("r1/parenthesis-start-refused", _outcome("Check the flag (run `t.py` first).\n"), (1, [("R1", "HARD")]))
    check("r1/and-joined-refused", _outcome("Read the diff and run the parser.\n"), (1, [("R1", "HARD")]))
    check("r1/inject-refused", _outcome("Inject a fault into the helper and observe the result.\n"),
          (1, [("R1", "HARD")]))
    check("r1/label-colon-ok", _outcome("Run: the second pass of the parser.\n"), (0, []))
    check("r1/file-name-ok", _outcome("Read the hook and install.sh from source.\n"), (0, []))
    check("r1/verb-in-code-span-ok", _outcome("See the `build and run` target.\n"), (0, []))
    check("r2/bash-fence-refused", _outcome(BASH_FENCE), (1, [("R2", "HARD")]))
    check("r2/untagged-command-fence-refused", _outcome(UNTAGGED_FENCE), (1, [("R2", "HARD")]))
    check("r2/python-fence-ok", _outcome("Steps:\n\n```python\nx = 1\n```\n"), (0, []))
    check("r2/fence-body-not-prose", _outcome("```text\nRun the parser.\n```\n"), (0, []))
    check("r3/quote-demoted-ok", _outcome(QUOTE_SELF_TEST), (0, []))
    check("r3/mention-info", _outcome(MENTION), (0, [("R3", "INFO")]))
    check("r3/negated-request-info", _outcome("Do not run `t.py --self-test`.\n"), (0, [("R3", "INFO")]))
    check("r3/request-hard", _outcome("Run `t.py --self-test` now.\n"), (1, [("R1", "HARD"), ("R3", "HARD")]))
    check("r5/exit-code-refused", _outcome(RESULT), (1, [("R5", "HARD")]))
    check("r5/negated-ok", _outcome(RESULT_NEGATED), (0, []))
    check("r6/named-tool-refused", _outcome(OUTCOME_NAMED), (1, [("R3", "HARD"), ("R6", "HARD")]))
    check("r6/unnamed-info", _outcome(OUTCOME_UNNAMED), (0, [("R6", "INFO")]))
    check("r6/pass-an-argument-ok", _outcome("Does `a.py` pass the flag to the helper?\n"), (0, []))
    # ---------- scope ----------
    check("scope/answer-only-ok", _outcome(SCOPED), (0, []))
    check("scope/answer-only-unscoped-refused", _outcome(SCOPED, scoped=False),
          (1, [("R1", "HARD"), ("R3", "HARD"), ("R5", "HARD")]))
    check("scope/executing-families-ok", (_outcome(SCOPED, "alpha", False), _outcome(SCOPED, "beta", False)),
          ((0, []), (0, [])))
    check("scope/do-not-attempt-ok", _outcome(NOT_ATTEMPT), (0, []))
    check("scope/other-leg-dropped", _outcome(OTHER_LEG), (0, []))
    check("scope/other-leg-items-dropped", _outcome(OTHER_LEG_ITEMS), (0, []))
    check("scope/other-leg-section-dropped", _outcome(OTHER_SECTION), (0, []))
    check("scope/tail-marker-dropped", _outcome(TAIL_MARKER), (0, []))
    check("scope/own-leg-kept", _outcome("Gamma leg: run `t.py --self-test`.\n"),
          (1, [("R1", "HARD"), ("R3", "HARD")]))
    check("scope/item-list-parsed", (parse_item_list("1, 2 and 4"), parse_item_list("Q1 to Q3"),
                                     parse_item_list("2-3, 5")), (set((1, 2, 4)), set((1, 2, 3)), set((2, 3, 5))))
    # ---------- cannot evaluate ----------
    check("cannot/undecodable-2", _outcome(b"Run \xff the parser.\n"), "CannotEvaluate")
    check("cannot/unknown-family-2", _outcome(RUN_ITEM, "delta"), "CannotEvaluate")
    check("cannot/blank-2", _outcome(" \n\n"), "CannotEvaluate")
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-brief-capability-selftest-"))
    try:
        table_path = str(tmp / "table.toml")
        for name, body in (("table.toml", TABLE_FIXTURE), ("refuse.md", RUN_ITEM), ("ok.md", NEGATED)):
            with open(tmp / name, "w", encoding="utf-8") as handle:
                handle.write(body)
        tail = ["--family", "gamma", "--table", table_path]
        check("main/refuse-exit-1", _quiet(main, [str(tmp / "refuse.md")] + tail), 1)
        check("main/ok-exit-0", _quiet(main, [str(tmp / "ok.md")] + tail), 0)
        check("main/missing-brief-2", _quiet(main, [str(tmp / "absent.md")] + tail), 2)
        check("main/directory-brief-2", _quiet(main, [str(tmp)] + tail), 2)
        check("main/missing-table-2", _quiet(main, [str(tmp / "refuse.md"), "--family", "gamma", "--table",
                                                    str(tmp / "absent.toml")]), 2)
        check("main/no-family-2", _quiet(main, [str(tmp / "refuse.md")]), 2)
        check("main/unknown-flag-2", _quiet(main, [str(tmp / "refuse.md"), "--force"] + tail), 2)
        check("main/bare-shipped-table-0", _quiet(main, []), 0)
        os.makedirs(tmp / ".aiqt" / "core")
        with open(tmp / TABLE_REL, "w", encoding="utf-8") as handle:
            handle.write(TABLE_FIXTURE.replace('execute = "no"', 'execute = "sometimes"'))
        with _patched(ROOT=tmp):
            check("main/bare-malformed-table-2", _quiet(main, []), 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # ---------- red on revert: each rule patched out flips its fixture ----------
    nothing = lambda *args: []  # noqa: E731
    flipped = []

    def flip(cases, want_patched, **replacements):
        """(got, want) for check(): the (unpatched, patched) outcomes of each fixture, and the unpatched
        ones beside the outcomes the patch must produce."""
        before = [outcome() for outcome in cases]
        with _patched(**replacements):
            after = [outcome() for outcome in cases]
        flipped.append(sorted(replacements))
        if before == want_patched:   # a fixture the rule never changed proves nothing
            FAILURES.append("revert of %s: the unpatched outcome already equals the patched one"
                            % ", ".join(sorted(replacements)))
        return (before, after), (before, want_patched)

    check("revert/r1-red", *flip(
        [lambda: _outcome(RUN_ITEM)[0], lambda: _outcome("Read the diff and run the parser.\n")[0]], [0, 0],
        rule_r1=nothing))
    check("revert/r1-negation-red", *flip(
        [lambda: _outcome(NEGATED)[0], lambda: _outcome(RESULT_NEGATED)[0]], [1, 1],
        negated=lambda *args: False))
    check("revert/r1-clause-start-red", *flip(
        [lambda: _outcome(CLAUSE_MID)[0]], [1],
        clause_starts=lambda sentence: [found.start() for found in re.finditer(r"\b\w", sentence)]))
    check("revert/r1-code-span-red", *flip(
        [lambda: _outcome("See the `build and run` target.\n")[0]], [1], code_spans=nothing))
    check("revert/r2-red", *flip(
        [lambda: _outcome(BASH_FENCE)[0], lambda: _outcome(UNTAGGED_FENCE)[0]], [0, 0],
        rule_r2=lambda block: False))
    check("revert/r3-red", *flip([lambda: _outcome(MENTION)], [(0, [])], rule_r3=nothing))
    check("revert/quote-demotion-red", *flip(
        [lambda: _outcome(QUOTE_SELF_TEST)], [(0, [("R3", "INFO")])], quote_demoted=lambda sentence: False))
    check("revert/r5-red", *flip([lambda: _outcome(RESULT)[0]], [0], rule_r5=nothing))
    check("revert/r6-red", *flip([lambda: _outcome(OUTCOME_FLAG)[0]], [0], rule_r6=nothing))
    check("revert/r6-named-red", *flip(
        [lambda: _outcome(OUTCOME_FLAG)], [(0, [("R3", "INFO"), ("R6", "INFO")])],
        NAMED_TOOL_RE=re.compile(r"(?!)")))
    check("revert/scope-directives-red", *flip(
        [lambda: _outcome(SCOPED)[0], lambda: _outcome(NOT_ATTEMPT)[0]], [1, 1],
        scope_directives=lambda text: (None, set())))
    check("revert/scope-legs-red", *flip(
        [lambda: _outcome(OTHER_LEG)[0], lambda: _outcome(OTHER_LEG_ITEMS)[0], lambda: _outcome(OTHER_SECTION)[0]],
        [1, 1, 1], leg_spans=nothing))
    check("revert/scope-tail-marker-red", *flip(
        [lambda: _outcome(TAIL_MARKER)[0]], [1], tail_marker_family=lambda *args: None))
    check("revert/scope-red", *flip([lambda: _outcome(SCOPED)[0]], [1], apply_scope=lambda blocks, *args: blocks))
    widened = dict(TABLE_VOCABULARY)
    widened["execute"] = TABLE_VOCABULARY["execute"] + ("sometimes",)
    sometimes = TABLE_FIXTURE.replace('execute = "no"', 'execute = "sometimes"')
    check("revert/table-vocabulary-red", *flip(
        [lambda: _table_outcome(sometimes)], [["alpha", "beta", "gamma"]], TABLE_VOCABULARY=widened))
    check("revert/capability-gate-red", *flip(
        [lambda: _outcome(RUN_ITEM, "alpha")[0], lambda: _outcome(RUN_ITEM, "beta")[0]], [1, 1],
        can_execute=lambda row: False))
    check("revert/every-flip-ran", len(flipped), 16)

    expected = _expected_check_ids()
    if expected is None:
        return 2
    for check_id in sorted(expected - _EXECUTED_SET):
        FAILURES.append("execution-set/missing: {}".format(check_id))
    for check_id in sorted(_EXECUTED_SET - expected):
        FAILURES.append("execution-set/extra: {}".format(check_id))
    if FAILURES:
        print("FAIL: check_brief_capability self-test")
        for failure in FAILURES:
            print("  - " + failure)
        return 1
    print("PASS: check_brief_capability self-test: {} unique checks executed (the shipped table loads and a "
          "malformed table exits 2; R1, R2, R3, R5 and R6 fire on synthetic fixtures and stay quiet on negated, "
          "mid-clause, quoted, labelled and code-span text; scope keeps and drops numbered items, other "
          "families' legs, sections and tail markers; undecodable, blank, missing, non-regular and "
          "unknown-family input exits 2; every rule patched out flips its fixture); execution set "
          "reconciled against tools/selftest_checks.toml".format(len(EXECUTED)))
    return 0


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main(sys.argv[1:]))
