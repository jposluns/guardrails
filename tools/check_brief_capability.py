#!/usr/bin/env python3
"""Brief capability lint (brief-capability): refuse a brief that carries a structural execution request to
a worker family that cannot execute.

A brief is the text sent to one worker family. When the family cannot run a command (its row in
.aiqt/core/worker-capabilities.toml says `execute = "no"`), a request to run a command, a tool, or git
can only come back as an asserted value that nobody measured, or as UNVERIFIABLE. This lint reads the
brief before it is sent. It REFUSES (exit 1) only on a structural signal: an executable fence, or a
command line in the text the family is addressed by (S1 to S3), unless one of three explicit data contexts
demotes it. The signal is structural, not certain: the lint fails closed, so some text a human would pass
refuses (see RESIDUALS). Every rule that infers a request from free prose (R1, R5, R6, R7) is ADVISORY:
its hits are printed so a human sees them, and it never refuses. For a family that can execute (`yes` or
`read-only-sandbox`) no rule applies and the result is OK.

TEXT. Format characters (Unicode category Cf: a byte order mark, zero-width and bidirectional
controls) are removed first; a brief that is then empty or only whitespace and control characters is
blank. Every list item (a line opening with `-`, `*`, `+`, `N.` or `N)`, at any indentation) is its own
paragraph, so it starts a sentence. A fence opens at any indentation.

BLOCKING RULES (HARD; one hit refuses):

  S1 executable fence  A fenced block tagged as a shell or console session (EXEC_FENCE_TAGS: bash, sh,
                       shell, console, zsh, pycon, ...); a fence tagged python, python3, py or py3 whose
                       first non-blank line is a run prompt (`>>> `, `$ `, `In [1]:`); or a fence untagged
                       or tagged text, txt, plain, plaintext, none or code whose first non-blank line
                       starts with `$ `, or with a listed command (git, python, python3, python3.N, pytest,
                       cd, bash, sh, zsh, npm, npx, pip, uv, cargo, tox, nox, docker, curl, wget, chmod,
                       mkdir, rm, ls, grep, make, go; optionally after sudo, `NAME=value` assignments and
                       a directory path) or a path opening with `./` or `tools/`. INFO instead of HARD
                       only when the last sentence of the paragraph just before the fence says the fence
                       is not to be run ("do not run this", "do not run it", "never execute the
                       following", "not to be run"), holds no EXCEPTION word and no execution verb
                       (EXEC_WORD_RE) but the prohibited one, and that paragraph has no R1 or R5 hit.
  S2 command in item   A command line in a numbered item (with the indented lines, nested list items
                       and list items without a blank line before them that belong to it).
  S3 command in the    A command line anywhere else in the text the family is addressed by: the
     preamble          paragraphs, headings and unnumbered list items outside the numbered items. This
                       catches "Read only `git show <sha>:<path>`" sent to a family that cannot run git.

A COMMAND LINE, in prose or in a code span, is: git (optionally behind a directory path), any number of
global options (`-C` or `-c` with a value, `--git-dir`, `--work-tree`, `--namespace`, `--config-env` or
`--attr-source` with a separate value, or any other option word), then a git subcommand (GIT_SUBCOMMANDS:
show, diff, log, blame, status, rev-parse, show-ref, show-branch, annotate, checkout, commit, push, ...);
python, python3 or python3.N (optionally behind a directory path), any number of options (value-less flag
clusters such as `-I -B`, `-W` or `-X` with an attached or a separate value, long options), then `-c`,
`-m` (alone or closing a cluster: `-Ic`) or a `.py` path; a `.py` or `.sh` path then a flag (`t.py
--self-test`); `./x.sh` or `./x.py`; bash, sh or zsh then `-c` or a `.sh` path; pytest then a flag, a `.py`
path or a tests directory; or a code span that opens with make, pytest, tox, nox, npm, npx, cargo, go, uv,
pip, pip3 or docker and an argument. A bare path (`tools/t.py`) is a file name, not a command line.

DEMOTIONS. A command line is INFO (printed, never refusing) only in one of three explicit data contexts.
Each is HEURISTIC: it is read from listed words, so it can pass a request phrased to fit it and refuse
data phrased outside it. In all three, a sentence that holds a reversal word outside a code span (ignore,
disregard, override, instead, otherwise, rather, actually, anyway, regardless, but, then, now, also, again,
afterwards, later, yourself, manually, directly, live) demotes nothing, and so does every sentence of a
block (a numbered item or a paragraph) that also has an R1 or R5 hit: the demotion is cancelled and the
command line is HARD.
  QUOTED SOURCE TEXT  The command line is in the object of a Quote, Cite or Show-the-text-of request at a
                      clause start, not negated: a SOURCE NOUN (row, entry, line, docstring, example,
                      comment, string, sentence, usage, field, tuple, statement, command, call, code,
                      case, item, text, README, ...) comes between the verb and the command; no clause
                      break comes between them except a comma or `and` before a determiner ("the def
                      line and the line in it that runs ..."); no preposition that makes the command the
                      text's source comes right before it (of, from, in, by, via, using, through, on,
                      after, when, ...: "the first line of `git log`" refuses); and the sentence holds,
                      outside code spans and command lines, no result word (output, stdout, stderr,
                      result, prints, returns, emits, produces, shows, exit code, return code, log, ...)
                      and no execution verb (run, ran, execute, invoke, launch, call, spawn, use, try,
                      do, perform, reproduce, repeat, ...), except one that opens a relative clause
                      right after `that` or `which` ("the line that runs `git push`") and run, call, use
                      or try right after a determiner ("the call that obtains the git dir"). "Quote the
                      README command `make test`" is INFO; "Quote the output of `make test`" and "Quote
                      what `git log` prints" refuse.
  PROHIBITED          The command line is the object of a prohibition right before it (do not, don't,
                      never, must not, may not or should not, then run, execute, invoke, call, use or
                      try), in a sentence that holds exactly one execution verb (EXEC_WORD_RE) and no
                      EXCEPTION word (the reversal words, and except, only, other than, apart from,
                      unless, until, before, after, first, without, more than, alone, once, twice, if
                      (but not "even if"), when, whenever, where). "cannot run" is not a prohibition.
  PAST REPORT         The sentence opens with a named run (CI, or the, this, its, ... with at most two
                      words, then gate, check, job, build, run, test, self-test, suite, workflow,
                      pipeline, lint or step) and a past verb (failed, passed, crashed, ran, exited,
                      succeeded, errored), and the command line comes right after a past verb with at
                      most two place phrases between ("CI failed at abc1234 on `t.py --self-test`").
Nothing else demotes a command line: not a Find clause, not a code span, not quotes ("find the test by
running `t.py --self-test`" and "quote `git log --oneline` for the last commits" refuse).

ADVISORY RULES (each runs on one sentence; a sentence ends at `.`, `!` or `?` before a capital letter,
or at `;`). A CLAUSE START is the start of a sentence, or the point just after a colon, a comma, a
parenthesis, `and`, `then`, `but`, or `so`, once markup (list markers, emphasis, `Step N -`) is
skipped, and again once at most six lead-in words are skipped (please, also, now, first, do, do not,
never, must, can you, could you, you need to, make sure to, go ahead and, ...). NOUN USES open no
clause: a parenthesis that holds only one word or a list of single words joined by commas (an Oxford
comma included), slashes, `or` or `and` (each optionally led by an article, determiner or possessive:
"(execute)", "a stalled (TIMED-OUT) run", "(a lint, gate, test, build, or self-test)") is not a clause,
so neither its parentheses nor the breaks inside it are clause starts, except that the point after such
a parenthesis that itself opens a clause is one ("(Optional) Run the suite"); and in a list of at least
three single words joined by commas and closed by `or` and a last word, either before punctuation
("lint, gate, test, build or self-test.") or, when an article, determiner or possessive leads the list,
before a word that is not an article, determiner or pronoun ("each lint, gate, build or self-test writes
a log"), no comma or word after the first is a clause start. A verb is NEGATED when `not`, `never`, `no`,
`nothing`, `without`, `cannot`, `nor`, or an `n't` form, found in the whole sentence, ends at most 60
characters before the verb begins with no HARD BREAK between them, or `nothing` follows the verb. A hard
break is a colon, a parenthesis, `then`, `but`, or the comma that closes an opening clause led by if,
once, when, after, before, unless, until, while, since, as soon as, or without; a plain comma or `and` is
not one, so "do not run, build or install" stays negated.

  R1 imperative    Not read in a Markdown heading. An execution verb (run, re-run, execute, invoke,
                   launch, build, compile, install, reproduce, benchmark, profile, measure, inject,
                   trigger, fuzz, bisect, clone, deploy, `check out` before a branch, commit, revision, tag
                   or code span, `time the ...`, and observe in a sentence that also injects) at a clause
                   start, outside a code span, not negated, and not followed by a colon, a hyphen, a dot or
                   slash joined to a word (so `install.sh` is a file name), `is` or `are`, or a noun that
                   makes it a noun phrase (configuration, failures, steps, order, ...); `try`, `start by`
                   or `begin by` before a gerund (running, building, ...); or `must`, `need to` or `has
                   to` before `be run`, `be executed`, `be built` (and the like) in a sentence that is not
                   a question.
  R5 result        A reporting verb (report, paste, give, record, capture, attach, print, state, show,
                   share, post, count, tell me) at a clause start, not negated and not followed by a noun
                   such as statements or calls, then in the sentence an execution result (an exit code or
                   status, a return code, the last or final lines, output, stdout or stderr, counts,
                   seconds, timings, a run time), unless the sentence names a static source after the verb
                   ("in the source", "the docstring documents").
  R6 outcome       A question ending with `?` opened at a clause start by does, do, did, is, are, was,
                   were, will, would, can, or could, or a sentence opened at a clause start by confirm,
                   verify, check, ensure, show, prove, establish or make sure, whose subject (at most 200
                   characters) is a test, self-test, gate, check, suite, CI, a `.py` or `.sh` file, or a
                   `--check` or `--self-test` flag, and whose outcome is pass, succeed, exit N, fail
                   without, fail on the old tree, turn red, go or stay green, unless the outcome opens a
                   relative clause (right after that, which, who, or an opening parenthesis).
  R7 git fact      A fact only git or a run can establish (a commit's parents, SHA, hash or id; which or
                   what commit or revision did something; a diff or changes between commits or revisions;
                   changed lines; an observed or actual exit code, or one of a run; test counts), outside a
                   code span and not negated, in a sentence that asks for something (a question ending
                   with `?`, or a request verb or wh-word at a clause start). No marker exempts an item:
                   an advisory rule needs no exemption.

Outcomes, printed one finding per line (HARD, INFO, or ADVISORY) and then one summary line:

  exit 0  OK               no HARD hit (INFO and ADVISORY findings may be printed).
  exit 1  REFUSE           at least one HARD hit (S1, S2 or S3). Rewrite the brief, or send the item to a
                           family that can execute.
  exit 2  CANNOT_EVALUATE  the brief is missing, not a regular file, larger than MAX_BYTES, not UTF-8, or
                           blank; a scope range runs backwards; the family is not in the table; the
                           table is missing, unreadable, or malformed (format-version must be the
                           integer 1); or the arguments are wrong (a repeated --family or --table
                           included). A brief that cannot be evaluated is never OK.

Run with no arguments, it lints no brief: it validates the shipped capability table only and exits 0
when the table is valid, 2 when it is not.

RESIDUALS (what it does not catch; examples, not a complete list). A request with no fence and no
command line never refuses: "run the self-test and report its exit code", "which commit added the
helper?" and "build the docs" are ADVISORY only, so a human must read the ADVISORY lines; for the same
reason a demoted command line followed by a request in another sentence with no command line ("Quote the
README command `make test`. Then run it.") refuses only through the R1 or R5 hit that cancels the
demotion in the same block, and not when that request is in another block or phrased outside R1 and R5.
A command outside the COMMAND LINE list does not refuse ("mktemp -d", "go test ./..." or "make test"
outside a code span, "./x" with no `.sh` or `.py`, a git alias such as "git lg", a capitalised "Git log",
git or python options outside the listed value-taking ones whose value is a separate word, such as
"python3 --check-hash-based-pycs always t.py"), nor does a shell command in a fence tagged with another
language without a run prompt, a fence whose first line is not a listed command, or a command line split
by a line break inside a word. An indented code block with no fence is read as prose: a listed command
line in it refuses (S2 inside a numbered item, S3 outside), and any other command in it does not. The
demotions are heuristic (see DEMOTIONS): a request phrased as quoted source text, a bare prohibition or a
past report by a named run, with none of the listed result, execution, reversal or exception words, is
INFO. Some text refuses that a human would pass: a prose or code-span mention of a git subcommand or a
command line outside the three contexts ("the git log shows the fix", "a `git diff` excerpt follows",
"is `t.py --self-test` listed in CI?", a heading "PR 9 adds a git log parser"), a quoted command behind a
parenthesis or a colon ("Quote the row ('git show abc:t.py', 1)"), quoted source text in a sentence
that holds a listed result or execution word anywhere ("the line where `git log` is called"), a
prohibition with two execution verbs ("do not run or execute `x.sh`"), and a shell-tagged fence shown as
an example; rewrite it ("the text that ...") or drop the command. The advisory rules are lexical over
listed words and miss free English (an implicit request, an unlisted verb, a request split across
sentences, a request in a heading or a Find, Quote or Cite clause, an `or`-closed word list of
imperatives, a list led by a determiner before a bare plural object ("read the diff, compile or run
tests"), a git or run fact phrased outside the R7 list), and they also print hits a human would not call
requests (R7 on "quote the comment that states the exit code of the run"); being advisory, neither
refuses. Scope is lexical: item numbers are matched wherever they appear, so two numbered lists in one
brief share their numbers; a family addressed without the `leg` marker ("Claude: run ...") is linted as
if addressed to the linted family; and an operative marker for another family hides that family's text
up to the next marker or address, so an instruction for the linted family placed there without an
address is hidden. The lint reads only the bytes it is given: run it on the final per-family bytes that
are sent, since a brief composed after the lint runs is not covered. Run alone, it is advisory; it blocks
only where the sender refuses to send on exit 1 or 2.

TIME. Every scan is one pass per sentence (or per paragraph or line), with bisection over positions found
once: clause breaks, code spans, negations, noun uses, quote verbs, source nouns, and the last result and
static-source positions for R5 (each search resumes one character after the previous match, so every
offset is tried once). Markup runs and command-word option runs are walked once and remembered, so a run
shared by many clause starts or command words ("1) 1) 1)", "git -c git -c ... show") is not read again;
there is no bound on the number of git or python options. Per command line, a demotion reads only the
DEMOTION_WINDOW characters before it; per opener, R6 measures its subject's length before reading it;
the remaining bounds are the six lead-in words and token-start anchors on every word-run, command and
file-name pattern. The self-test measures it: in a child process capped at 1 GiB of address space
(RLIMIT_AS) with a LINEAR_TIMEOUT-second timeout, which must exit 0 and print two finite, nonnegative
times, each of 16 shapes at 1 MiB must take at most LINEAR_FACTOR times as long as at 128 KiB plus
LINEAR_SLACK seconds (the input is eight times larger; a pass whose time grows with the square of the
input takes about 64 times as long). Measured on 2026-10-08 on one shared host, each in such a child under
nice 10, with these 16 shapes: every shape took at most 0.23 s at 128 KiB and 1.88 s at 1 MiB, at most 9.0
times its 128 KiB time. On the previous revision the same harness (with a 60-second timeout) showed ten of
them quadratic: runs of "do not run `git status` and " took 13.1 s at 128 KiB, of "Do not run git show "
19.2 s, of "never run git log " in one item 17.7 s, of "report, " 58.8 s and of "observe, " 21.5 s, and
none of them finished 1 MiB within 60 s; runs of "report and ", of "1) " and of "Q1. " after a leg marker
did not finish even 128 KiB within 60 s; an indented "(zeta leg)" tail-marker run took 2.9 s at 128 KiB
and did not finish 1 MiB within 60 s; and "Alpha leg: x." lines took 0.17 s and 14.0 s. The other six (a single word, a noun
run, a git option run, a python option run, an R6 subject run and a quote-context run) passed on the
previous revision too, so they guard against regression and do not discriminate it (the R6 run took 3.77
s at 1 MiB, just inside its 3.91 s limit).

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
import subprocess
import tempfile
import unicodedata
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
R6_SUBJECT_MAX = 200
DEMOTION_WINDOW = 80
EXCERPT_CHARS = 120


class CannotEvaluate(Exception):
    pass


class Block:
    """A run of brief lines: kind "prose" (a paragraph, a heading, or a list item) or "fence"."""
    __slots__ = ("kind", "start", "lines", "item", "tag", "heading", "listed", "masked")

    def __init__(self, kind, start, item=None, tag="", heading=0, listed=False):
        self.kind, self.start, self.item, self.tag, self.heading = kind, start, item, tag, heading
        self.listed = listed  # a list item, numbered or not
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
    version = doc.get("format-version")
    # the exact integer 1: a bool (true == 1) or a float (1.0 == 1) is refused
    if sorted(doc) != ["family", "format-version"] or type(version) is not int or version != 1:
        raise CannotEvaluate("capability table: the top level must be exactly format-version = 1 (an "
                             "integer) and [[family]] rows")
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


def _blank_character(ch):
    return ch.isspace() or unicodedata.category(ch) in ("Cc", "Zs", "Zl", "Zp")


def decode_brief(data):
    """The brief's text with format characters removed, or CannotEvaluate when it is not UTF-8 or blank."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CannotEvaluate("brief: not valid UTF-8 ({})".format(exc.reason))
    # A byte order mark, a zero-width or a bidirectional control is invisible and can split a word.
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    if all(_blank_character(ch) for ch in text):
        raise CannotEvaluate("brief: blank")
    return text


# ---------- structure ----------

FENCE_OPEN_RE = re.compile(r"([ \t]*)(`{3,}|~{3,})(.*)\Z")
HEADING_RE = re.compile(r" {0,3}(#{1,6})(?:\s|\Z)")
ITEM_RE = re.compile(r" {0,3}(?:[-*+]\s+)?(?:\*\*|__)?(?:Q|Item\s+|Question\s+)?(\d{1,3})(?:[.)]|:(?=\s))"
                     r"(?:\*\*|__)?(?=\s|\Z)", re.I)
LIST_LINE_RE = re.compile(r"[ \t]*(?:[-*+]|\d{1,3}[.)])[ \t]+\S")


def _fence_closes(line, opening):
    """Whether `line` closes a fence opened by the run `opening` (the same character, at least as many)."""
    stripped = line.lstrip(" \t")
    run = len(stripped) - len(stripped.lstrip(opening[0]))
    return run >= len(opening) and not stripped[run:].strip()


def parse_blocks(text):
    """The brief as a list of Blocks: fences, headings, list items, and paragraphs."""
    blocks, current, item, fence, opening, blank = [], None, None, None, "", False
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
            if not indented:
                item = None
            info = opened.group(3).strip().split()
            opening = opened.group(2)
            fence = Block("fence", number, item=item, tag=info[0].lower() if info else "")
            blocks.append(fence)
            current, blank = None, False
            continue
        if not line.strip():
            current, blank = None, True
            continue
        was_blank, blank = blank, False
        heading = HEADING_RE.match(line)
        if heading:
            current, item = None, None
            block = Block("prose", number, heading=len(heading.group(1)))
            block.lines.append((number, line))
            blocks.append(block)
            continue
        numbered = ITEM_RE.match(line)
        if numbered or LIST_LINE_RE.match(line):
            # every list item is its own block, so it starts a sentence and a clause
            if numbered:
                item = int(numbered.group(1))
            elif not indented and was_blank:
                item = None
            current = Block("prose", number, item=item, listed=True)
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


# ---------- sentences and clauses ----------

SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?])\s+(?=[*_\"'`(\[]*[A-Z])|;\s+")
MARKUP_TOKEN_RE = re.compile(r"\s|[>*_#]|[-+–—](?=\s)"
                             r"|(?:Q|Item\s+|Question\s+|Step\s+)?\d{1,3}(?:[.)]|:|\s+[-–—])(?=\s)", re.I)
MARKUP_RE = re.compile(r"(?:" + MARKUP_TOKEN_RE.pattern + r")*", re.I)
LEAD_IN_RE = re.compile(r"(?:(?:please|kindly|also|then|now|first|second|third|next|finally|and|or|but|so|"
                        r"again|separately|additionally|instead|always|just|do\s+not|don't|do|never|"
                        r"(?:can|could|would|will)\s+you|(?:you|we)\s+(?:must|should|may|can|need\s+to|"
                        r"have\s+to|will\s+need\s+to)|must|should|may|need\s+to|have\s+to|"
                        r"i\s+(?:need|want|would\s+like)\s+you\s+to|make\s+sure\s+(?:to|you)|be\s+sure\s+to|"
                        r"remember\s+to|go\s+ahead\s+and|try\s+to)\b[\s,]*){0,6}", re.I)
CLAUSE_BREAK_RE = re.compile(r"(?:[:(),]|\b(?:and|then|but|so)\b)\s*", re.I)
HARD_BREAK_RE = re.compile(r"[:()]|\b(?:then|but)\b", re.I)
SUBORDINATE_RE = re.compile(r"(?:if|once|when|whenever|after|before|unless|until|while|since|as\s+soon\s+as|"
                            r"without)\b", re.I)
NEGATION_RE = re.compile(r"\b(?:not|never|no|nothing|without|cannot|nor)\b|n't\b", re.I)
NEGATED_AFTER_RE = re.compile(r"\s+nothing\b", re.I)
BACKTICKS_RE = re.compile(r"`+")
NOUN_WORD = r"[A-Za-z][\w-]*+"
DETERMINER = r"(?:a|an|the|its|their|his|her|our|your|my|this|that|these|those|each|every|any|some|no|either)"
NOUN_PARENTHESIS_RE = re.compile(r"\(\s*(?:(?:or|and)\s+)?(?:" + DETERMINER + r"\s+)?" + NOUN_WORD
                                 + r"(?:\s*(?:,\s*(?:or|and)\b|,|/|\bor\b|\band\b)\s*(?:" + DETERMINER + r"\s+)?" + NOUN_WORD
                                 + r")*+\s*\)", re.I)
NOUN_WORD_RE = re.compile(NOUN_WORD)
# anchored at a token start: unanchored, a failed match restarts at every character of a long word, so
# the scan grows with the square of the word's length
WORD_RUN_RE = re.compile(r"(?<![\w-])" + NOUN_WORD + r"(?:\s*,\s*" + NOUN_WORD + r")++")
NOUN_LIST_TAIL_RE = re.compile(r"\s*,?\s+or\s+" + NOUN_WORD + r"(?=\s*(?:[)\].;:!?]|\Z))", re.I)
# a list led by a determiner is a noun list also before a word that is not an object ("each lint, gate,
# build or self-test writes a log"; not "the diff, build or run the parser")
LED_LIST_TAIL_RE = re.compile(r"\s*,?\s+or\s+" + NOUN_WORD + r"(?=\s+(?!(?:" + DETERMINER
                              + r"|it|them|all|both)\b)[A-Za-z])", re.I)
LEADING_DETERMINER_RE = re.compile(r"(?<![\w-])" + DETERMINER + r"\s+\Z", re.I)
OBJECT_CONTINUES_RE = re.compile(r"(?:and\s+)?(?:" + DETERMINER + r"|all|both)\b", re.I)


class MarkupEnds:
    """The end of the markup run (MARKUP_RE) from any offset of one text. The run is walked token by token
    and every offset walked is remembered, so a run that holds many clause or sentence starts ("1) 1) 1)")
    is read once, not once per start."""

    def __init__(self, text):
        self.text, self.memo = text, dict()

    def __call__(self, position):
        walked = []
        while position not in self.memo:
            found = MARKUP_TOKEN_RE.match(self.text, position)
            if not found:
                self.memo[position] = position
                break
            walked.append(position)
            position = found.end()
        end = self.memo[position]
        for offset in walked:
            self.memo[offset] = end
        return end


def sentences(text):
    """[(offset, sentence)] of a paragraph's text (newlines already turned into spaces)."""
    out, start = [], 0
    for found in SENTENCE_BREAK_RE.finditer(text):
        out.append((start, text[start:found.start()]))
        start = found.end()
    out.append((start, text[start:]))
    return [(offset, sentence) for offset, sentence in out if sentence.strip()]


def noun_ranges(sentence):
    """Sorted, disjoint [start, end) ranges of noun uses, which open no clause: a parenthesis holding one
    word or a list of single words (both parentheses included), and the part after the first word of an
    `or`-closed list of at least three single words that ends the clause or, led by a determiner, comes
    before a word that is not an object."""
    ranges = [found.span() for found in NOUN_PARENTHESIS_RE.finditer(sentence)]
    for run in WORD_RUN_RE.finditer(sentence):
        tail = NOUN_LIST_TAIL_RE.match(sentence, run.end())
        if not tail and LEADING_DETERMINER_RE.search(sentence, max(0, run.start() - 24), run.start()):
            tail = LED_LIST_TAIL_RE.match(sentence, run.end())
        if tail:
            ranges.append((NOUN_WORD_RE.match(sentence, run.start()).end(), tail.end()))
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def code_spans(sentence):
    """Sorted, disjoint [start, end) code spans: a backtick run closed by the next run of the same length."""
    runs = [found.span() for found in BACKTICKS_RE.finditer(sentence)]
    by_length = dict()
    for index, (start, end) in enumerate(runs):
        by_length.setdefault(end - start, []).append(index)
    spans, index = [], 0
    while index < len(runs):
        start, end = runs[index]
        same = by_length[end - start]
        later = bisect.bisect_right(same, index)
        if later < len(same):
            spans.append((start, runs[same[later]][1]))
            index = same[later] + 1
        else:
            index += 1
    return spans


class Sentence:
    """One sentence with its clause starts and breaks, code spans, negations and hard breaks, each found
    once, so every rule runs in about linear time."""

    def __init__(self, text):
        self.text = text
        nouns = noun_ranges(text)
        noun_starts = [start for start, _ in nouns]

        def noun_use(position):
            index = bisect.bisect_right(noun_starts, position) - 1
            return index >= 0 and position < nouns[index][1]

        # a break inside a noun use ("(execute)", "lint, gate, build or self-test") opens no clause
        found = [match for match in CLAUSE_BREAK_RE.finditer(text) if not noun_use(match.start())]
        self.breaks = [match.start() for match in found]
        # the breaks that end a quoted object: all but a comma or `and` before another object ("the def line
        # and the line in it that runs ...")
        self.stops = [match.start() for match in found if not (match.group(0)[:1] == ","
                      or match.group(0)[:3].lower() == "and") or not OBJECT_CONTINUES_RE.match(text, match.end())]
        self.markup = markup = MarkupEnds(text)
        starts = set()
        for position in [0] + [match.end() for match in found]:
            position = markup(position)
            starts.add(position)
            starts.add(LEAD_IN_RE.match(text, position).end())
        # a noun parenthesis that itself opens a clause ("(Optional) Run the suite") is followed by one
        for aside in NOUN_PARENTHESIS_RE.finditer(text):
            if aside.start() in starts:
                position = markup(aside.end())
                starts.add(position)
                starts.add(LEAD_IN_RE.match(text, position).end())
        self.starts = sorted(starts)
        self.spans = code_spans(text)
        self.span_starts = [start for start, _ in self.spans]
        self.negations = [match.span() for match in NEGATION_RE.finditer(text)]
        self.negation_ends = [end for _, end in self.negations]
        hard = [match.start() for match in HARD_BREAK_RE.finditer(text)]
        clause = 0   # the start of the current clause; `and` and `so` do not open a new one here
        for match in found:
            if match.group(0).startswith(","):
                opening = LEAD_IN_RE.match(text, markup(clause)).end()
                if SUBORDINATE_RE.match(text, opening):
                    hard.append(match.start())
            if match.group(0)[:1] in (",", ":", "(", ")") or match.group(0).lower().startswith(("then", "but")):
                clause = match.end()
        self.hard = sorted(hard)

    def in_span(self, position):
        index = bisect.bisect_right(self.span_starts, position) - 1
        return index >= 0 and position < self.spans[index][1]

    def negated(self, start, end):
        """Whether the word at [start, end) is negated: the nearest negation ending at most NEGATION_WINDOW
        characters before it with no hard break between, or `nothing` right after it."""
        index = bisect.bisect_right(self.negation_ends, start) - 1
        if index >= 0:
            neg_end = self.negations[index][1]
            if start - neg_end <= NEGATION_WINDOW:
                between = bisect.bisect_left(self.hard, neg_end)
                if between >= len(self.hard) or self.hard[between] >= start:
                    return True
        return bool(NEGATED_AFTER_RE.match(self.text, end))

    def clause_end(self, position):
        """The offset of the first clause break after `position`, or the sentence's end."""
        index = bisect.bisect_right(self.breaks, position)
        return self.breaks[index] if index < len(self.breaks) else len(self.text)


# ---------- scope ----------

ITEM_LIST = (r"Q?\d{1,3}(?:\s*(?:,\s*(?:and\s+|or\s+)?|\band\s+|\bor\s+|-|\bto\s+|\bthrough\s+)\s*"
             r"Q?\d{1,3})*")
ONLY_RE = re.compile(r"answer\s+(?:only\s+)?(?:items?|questions?)\s+(" + ITEM_LIST + r")(\s+only)?", re.I)
ANSWER_ONLY_RE = re.compile(r"answer\s+only\s+(?:items?|questions?)\s+(" + ITEM_LIST + ")", re.I)
EXCLUDE_RE = re.compile(r"(?:(?:do\s+not|don't|never)\s+(?:attempt|answer)|skip)\s+(?:items?|questions?)\s+("
                        + ITEM_LIST + ")", re.I)
LEG_RE = re.compile(r"(?:(?:for\s+)?the\s+)?([A-Za-z][A-Za-z0-9-]*)\s+leg(?:\*\*|__)?\s*:", re.I)
LEG_HEADING_RE = re.compile(r" {0,3}#{1,6}\s+(?:the\s+)?([A-Za-z][A-Za-z0-9-]*)\s+leg\b", re.I)
TAIL_MARKER_RE = re.compile(r"\((?:for\s+)?(?:the\s+)?([A-Za-z][A-Za-z0-9-]*)\s+(?:leg|only)\)", re.I)
LINE_END_RE = re.compile(r"[\s.,:;!?*_]*\Z")
ADDRESS_RE = re.compile(r"(?:(?:all|every|each|both)\s+(?:legs?|famil(?:y|ies)|workers?)"
                        r"|([A-Za-z][A-Za-z0-9-]*)(?:\s+leg)?)(?:\*\*|__)?\s*:", re.I)


def parse_item_list(spec):
    """The item numbers of "1, 2 and 4", "1-3", "Q1 to Q3"; a backwards range is CannotEvaluate."""
    numbers = set()
    tokens = re.findall(r"\d{1,3}|-|\bto\b|\bthrough\b", spec, re.I)
    index = 0
    while index < len(tokens):
        first = int(tokens[index])
        if index + 2 < len(tokens) and not tokens[index + 1].isdigit():
            last = int(tokens[index + 2])
            if last < first:
                raise CannotEvaluate("scope: the item range {}-{} runs backwards".format(first, last))
            numbers.update(range(first, last + 1))
            index += 3
        else:
            numbers.add(first)
            index += 1
    return numbers


def scope_directives(text):
    """(kept, dropped): the item numbers the linted family's own scope text keeps (None: every item) and
    the ones it drops. Only a directive at a clause start, outside a code span, counts."""
    kept, dropped = None, set()
    for _, raw in sentences(text.replace("\n", " ")):
        sentence = Sentence(raw)
        for start in sentence.starts:
            if sentence.in_span(start):
                continue
            found = ONLY_RE.match(raw, start)
            if found and found.group(2):
                kept = (kept or set()) | parse_item_list(found.group(1))
            found = ANSWER_ONLY_RE.match(raw, start)
            if found:
                kept = (kept or set()) | parse_item_list(found.group(1))
            found = EXCLUDE_RE.match(raw, start)
            if found:
                dropped |= parse_item_list(found.group(1))
    return kept, dropped


def leg_spans(blocks, families):
    """[(block index, start offset, family, extent)] for every operative family marker: a heading marker
    (extent "section", offset 0), or a `<family> leg:` marker at the start of a line once markup is
    skipped (extent "items" when it opens a paragraph that ends with a colon, "" otherwise). A marker
    in a code span, in quotes, or mid-line is not operative."""
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
        offset, colon = 0, text.rstrip().endswith(":")
        for number, (_, line) in enumerate(block.lines):
            found = LEG_RE.match(line, MARKUP_RE.match(line).end())
            if found and found.group(1).lower() in families:
                spans.append((index, offset + found.start(), found.group(1).lower(),
                              "items" if number == 0 and colon else ""))
            offset += len(line) + 1
    return spans


def tail_marker_family(text, families):
    """The family an operative `(<family> leg)` or `(<family> only)` marker names (at the start or the end
    of a line, outside a code span), or None."""
    spans, offset = code_spans(text), 0   # a code span can run across the item's lines
    span_starts = [start for start, _ in spans]
    for line in text.split("\n"):
        # only the line's last marker can end it: a later marker's parenthesis is not line-end text
        last = None
        for last in TAIL_MARKER_RE.finditer(line):
            pass
        candidates = [last] if last and LINE_END_RE.match(line, last.end()) else []
        at_start = TAIL_MARKER_RE.match(line, MARKUP_RE.match(line).end())
        if at_start:
            candidates.append(at_start)
        for found in candidates:
            index = bisect.bisect_right(span_starts, offset + found.start()) - 1
            if index >= 0 and offset + found.start() < spans[index][1]:
                continue
            if found.group(1).lower() in families:
                return found.group(1).lower()
        offset += len(line) + 1
    return None


def _addresses(text, families):
    """[(offset, family, or None for every leg)] for each sentence of a paragraph that opens by addressing
    a family or every leg."""
    flat, out = text.replace("\n", " "), []
    markup = MarkupEnds(flat)   # a markup run can hold many sentence starts ("Q1. Q1. Q1.")
    for offset, _ in sentences(flat):
        found = ADDRESS_RE.match(flat, markup(offset))
        if found and (found.group(1) is None or found.group(1).lower() in families):
            out.append((found.start(), found.group(1).lower() if found.group(1) else None))
    return out


def apply_scope(blocks, family, families):
    """The blocks addressed to the linted family: text another family claims masked or dropped, and the
    numbered items the family's own scope text excludes dropped."""
    for block in blocks:
        block.masked = []
    spans = leg_spans(blocks, families)
    by_block = dict()
    for span in spans:
        by_block.setdefault(span[0], []).append(span)
    own_text, claimed = [], dict()   # claimed: block index -> the family that claims the whole block
    for index, block_spans in by_block.items():
        block = blocks[index]
        if block_spans[0][3] == "section":
            name = block_spans[0][2]
            claimed[index] = name
            for later in range(index + 1, len(blocks)):
                if blocks[later].heading and blocks[later].heading <= block.heading:
                    break
                claimed[later] = name
            continue
        text = block.text()
        addresses = _addresses(text, families)
        positions = [address for address, _ in addresses]
        for position, (_, start, name, extent) in enumerate(block_spans):
            # the next marker, or a sentence addressing another family or every leg, ends this one's span
            end = block_spans[position + 1][1] if position + 1 < len(block_spans) else len(text)
            for following in range(bisect.bisect_right(positions, start), len(addresses)):
                address, addressed = addresses[following]
                if address >= end:
                    break
                if addressed != name:
                    end = address
                    break
            if name == family:
                own_text.append(text[start:end])
            else:
                block.masked.append((start, end))
            if extent == "items":
                for later in range(index + 1, len(blocks)):
                    after = blocks[later]
                    if later in by_block or not (after.kind == "fence" or after.listed or after.item is not None):
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


# ---------- rules ----------

R1_NOUNS = (r"configuration|config|failures?|errors?|steps?|order|conditions?|settings?|names?|logs?|matrix|"
            r"options?|artifacts?|numbers?|ids?|modes?|phases?|stages?|types?|results?|status|outputs?|"
            r"directory|cache|environment|variables?|instructions?|notes?|documentation|section|systems?|"
            r"timeouts?|time|times|history|metadata|rules?|policy|policies|reports?|pipeline|definitions?")
R1_VERB_RE = re.compile(r"(?:re-?run|run|re-?execute|execute|invoke|launch|re-?build|build|compile|install|"
                        r"reproduce|benchmark|profile|measure|inject|trigger|fuzz|bisect|clone|deploy|"
                        r"check\s+out(?=\s+(?:(?:the|a|an|that|this)\s+)?(?:branch|commit|revision|tag|ref|"
                        r"PR|pull|worktree|sha|parent|base|head|main|master|[0-9a-f]{7,40}\b|`))|"
                        r"time(?=\s+(?:the|each|every|it|how)\b)|observe)\b"
                        r"(?!\s*:|-|[./]\w|\s+(?:is|are|was|were|" + R1_NOUNS + r")\b)", re.I)
R1_GERUND_RE = re.compile(r"(?:try|start\s+by|begin\s+by)\s+(?:re-?running|running|re-?executing|executing|"
                          r"invoking|launching|re-?building|building|compiling|installing|reproducing|"
                          r"benchmarking|profiling|measuring|injecting|triggering|fuzzing|bisecting|cloning|"
                          r"deploying)\b", re.I)
R1_PASSIVE_RE = re.compile(r"\b(?:must|needs?\s+to|has\s+to|have\s+to)\s+be\s+(?:re-?)?(?:run|executed|built|"
                           r"compiled|installed|reproduced|benchmarked|profiled|measured|launched|invoked|"
                           r"deployed)\b", re.I)
SHELL_TAGS = frozenset(("bash", "sh", "shell", "console", "zsh", "fish", "ksh", "dash", "shell-session",
                        "shellsession", "sh-session", "terminal", "powershell", "pwsh", "ps1", "cmd", "bat",
                        "batch"))
EXEC_FENCE_TAGS = SHELL_TAGS | frozenset(("pycon", "python-console", "python-repl", "ipython", "repl"))
PYTHON_TAGS = frozenset(("python", "python3", "py", "py3"))
RUN_PROMPT_RE = re.compile(r"\s*(?:>>>|\$|In\s*\[\d*\]:)(?:\s|\Z)")
PLAIN_TAGS = frozenset(("", "text", "txt", "plain", "plaintext", "none", "code"))
SHELL_FIRST_LINE_RE = re.compile(r"\s*(?:\$\s|(?:sudo\s+)?(?:[A-Za-z_]\w*=\S*\s+)*(?:(?:[\w.~-]*/)*(?:git|"
                                 r"python(?:3(?:\.\d+)?)?|pytest|cd|bash|sh|zsh|npm|npx|pip3?|uv|cargo|tox|nox|"
                                 r"docker|curl|wget|chmod|mkdir|rm|ls|grep|make|go)(?=\s|\Z)|(?:\./|tools/)\S))")
REFERENCE_INTRO_RE = re.compile(r"\b(?:(?:do\s+not|don['’]t|never)\s+(?:run|execute)\s+(?:this|it|these|them|"
                                r"the\s+following|(?:the\s+)?(?:block|fence|commands?|snippet)\s+below)\b"
                                r"|not\s+to\s+be\s+(?:run|executed)\b)", re.I)
# Wording that reverses or qualifies a request ("ignore the instruction", "and do it now", "but also on the
# branch"): a sentence holding one outside a code span is never a data context, so it demotes nothing.
REVERSAL_WORDS = (r"ignore|disregard|override|instead|otherwise|rather|actually|anyway|regardless|but|then|now|"
                  r"also|again|afterwards?|later|yourself|manually|directly|live")
REVERSAL_RE = re.compile(r"(?<![\w./-])(?:" + REVERSAL_WORDS + r")(?![\w-]|[./]\w)", re.I)
# A prohibition or a not-to-be-run introduction is void when it also names an exception or a condition.
EXCEPTION_RE = re.compile(r"(?<![\w./-])(?:" + REVERSAL_WORDS + r"|except|excepting|only|other\s+than|apart\s+from|"
                          r"unless|until|before|after|first|without|more\s+than|alone|once|twice|(?<!even\s)if|when|"
                          r"whenever|where)(?![\w-]|[./]\w)", re.I)
# an execution verb anywhere in a sentence; a prohibition demotes only in a sentence that holds exactly one
EXEC_WORD_RE = re.compile(r"(?<![\w./-])(?:re-?run|run|runs|running|ran|re-?execute|execute|executes|executing|"
                          r"invoke|invokes|invoking|call|calls|calling|use|uses|using|try|tries|trying|launch|"
                          r"launches|launching)(?![\w-]|[./]\w)", re.I)
GIT_CONFIG = (r"config(?!\s+(?:files?|keys?|values?|settings?|options?|entr(?:y|ies)|surfaces?|variables?|"
              r"sections?|scopes?|layers?)\b)")   # "the git config file" is a noun
GIT_SUBCOMMANDS = (r"add|am|annotate|apply|archive|bisect|blame|branch|cat-file|check-attr|check-ignore|checkout|"
                   r"cherry|cherry-pick|clean|clone|commit|" + GIT_CONFIG + r"|count-objects|describe|diff|diff-tree|"
                   r"fetch|for-each-ref|format-patch|fsck|gc|grep|init|log|ls-files|ls-remote|ls-tree|merge|"
                   r"merge-base|merge-tree|mv|name-rev|notes|pull|push|range-diff|rebase|reflog|remote|reset|"
                   r"restore|rev-list|rev-parse|revert|rm|shortlog|show|show-branch|show-ref|stash|status|submodule|"
                   r"switch|symbolic-ref|tag|var|verify-commit|verify-tag|whatchanged|worktree")
# git or python (optionally behind a directory path) as a command word, at a token start, case-sensitive
COMMAND_WORD_RE = re.compile(r"(?<![\w./~-])(?:[\w.~-]*/)*(?:(git)|python(?:3(?:\.\d+)?)?)(?=\s)")
# one global git option: -C or -c and a value, a long option that takes a separate value, or any other option
GIT_OPTION_RE = re.compile(r"\s+(?:-[Cc]\s+\S+|--(?:git-dir|work-tree|namespace|config-env|attr-source)\s+\S+"
                           r"|--?[A-Za-z][\w-]*(?:=\S+)?)")
GIT_SUBCOMMAND_RE = re.compile(r"\s+(?:" + GIT_SUBCOMMANDS + r")(?![\w-])")
PY_FLAG = r"[A-VYZabd-lnoq-z]"   # a python flag letter that takes no value: every letter but c, m, W and X
# one python option: -W or -X with its value (attached or the next word), a cluster of value-less flags, or a
# long option; -c and -m end the options and are the target, so the option run never consumes them
PYTHON_OPTION_RE = re.compile(r"\s+(?:-" + PY_FLAG + r"*[WX](?:\S+|\s+\S+)|-" + PY_FLAG + r"+(?=\s)"
                              r"|--[a-z][\w-]*(?=\s))")
PYTHON_TARGET_RE = re.compile(r"\s+(?:-" + PY_FLAG + r"*[cm]|[\w./-]+\.py\b)")
# the other command-line forms, each anchored at a token start
COMMAND_FORMS = (
    r"(?<![\w./-])[\w./-]+\.(?:py|sh)\s+--?[A-Za-z][\w-]*",
    r"(?<![\w./-])\./[\w./-]+\.(?:sh|py)\b",
    r"(?<![\w./-])(?:bash|sh|zsh)\s+(?:-c\b|[\w./-]+\.sh\b)",
    r"(?<![\w./-])pytest\s+(?:-|[\w./-]+\.py\b|tests?/)",
)
COMMAND_RE = re.compile("|".join(COMMAND_FORMS))
# a code span that opens with one of these tools and an argument (matched at the span's opening backticks)
SPAN_COMMAND_RE = re.compile(r"`+\s*(?:\$\s+)?(?:make|pytest|tox|nox|npm|npx|cargo|go|uv|pip3?|docker)\s+[^`\s]")
# The three data contexts that demote a command line to INFO. QUOTED: a Quote, Cite or Show-the-text-of
# request whose object names source text that holds the command as text.
QUOTE_VERB_RE = re.compile(r"(?:quote|cite|show\s+(?:me\s+)?the\s+(?:exact\s+|verbatim\s+|full\s+)?text\s+of)\b",
                           re.I)
SOURCE_NOUN_RE = re.compile(r"\b(?:rows?|entr(?:y|ies)|lines?|docstrings?|examples?|comments?|strings?|literals?|"
                            r"sentences?|headings?|definitions?|snippets?|excerpts?|signatures?|usage|cells?|fields?|"
                            r"tuples?|assignments?|declarations?|statements?|commands?|calls?|code|cases?|items?|"
                            r"bullets?|text|README)\b", re.I)
# a result or an execution word anywhere in the sentence voids the quoted context ("the output of", "what it
# prints", "and run it"), unless it opens a relative clause on the source text ("the line that runs")
QUOTE_VETO_RE = re.compile(r"(?<![\w./-])(?:outputs?|stdout|stderr|results?|prints|printed|printing|returns|returned|"
                           r"returning|emits|emitted|produces|produced|yields|shows|showed|shown|exit\s+(?:codes?|"
                           r"status)|return\s*codes?|returncodes?|logs?|logged|re-?runs?|run|runs|running|ran|"
                           r"execut\w+|invok\w+|launch\w*|call|calls|called|calling|spawn\w*|use|uses|used|using|try|"
                           r"tries|tried|trying|do|does|did|doing|perform\w*|reproduc\w+|repeat\w*)(?![\w-]|[./]\w)",
                           re.I)
RELATIVE_BEFORE_RE = re.compile(r"\b(?:that|which)\s+\Z", re.I)
# after a determiner these are nouns ("the call that obtains the repository"), not requests
NOUN_AFTER_DETERMINER = frozenset(("run", "runs", "call", "calls", "use", "uses", "try", "tries"))
DETERMINER_BEFORE_RE = re.compile(r"\b" + DETERMINER + r"\s+\Z", re.I)
# a preposition right before the command makes it the source of the text ("the first line of `git log`")
SOURCE_OF_RE = re.compile(r"\b(?:of|from|in|by|via|using|through|on|after|when|while|during|against|at|under|into|"
                          r"onto|per)\s*[`\"'“]*\Z", re.I)
# PROHIBITED: the command is the object of a prohibition
PROHIBITED_RE = re.compile(r"\b(?:do\s+not|don['’]t|never|must\s+not|may\s+not|should\s+not)\s+(?:run|execute|"
                           r"invoke|call|use|try)\s*[`\"'“]*\Z", re.I)
# PAST REPORT: a sentence opened by a named run (CI, the gate, the self-test) and a past verb, with the
# command right after the past verb and at most two place phrases
PAST_SUBJECT_RE = re.compile(r"(?:CI|(?:the|this|that|its|their|our)\s+(?:[\w./-]+\s+){0,2}?(?:gate|check|job|build|"
                             r"run|test|self-test|suite|workflow|pipeline|lint|step))\s+(?:failed|passed|crashed|ran|"
                             r"exited|succeeded|errored)\b", re.I)
PAST_BEFORE_RE = re.compile(r"\b(?:failed|passed|crashed|ran|exited|succeeded|errored)"
                            r"(?:\s+(?:at|on|in|under|with|against|for|from)\s+[^\s,;:()`]+){0,2}"
                            r"(?:\s+(?:at|on|in|under|with|against|for|from))?\s*[`\"'“]*\Z", re.I)
R5_VERB_RE = re.compile(r"(?:report|paste|give|record|capture|attach|print|state|show|share|post|count|"
                        r"tell\s+(?:me|us))\b(?!\s+(?:statements?|calls?|functions?|helpers?|methods?)\b)", re.I)
R5_RESULT_RE = re.compile(r"\b(?:exit\s+(?:codes?|status(?:es)?)|return\s*codes?|returncodes?|"
                          r"(?:last|final)\s+(?:\d+\s+)?lines|lines\s+of\s+(?:the\s+|its\s+)?output|"
                          r"(?:full|complete|raw|whole|entire|console|terminal|command|test)\s+output|"
                          r"output\s+of\s+(?:running|the\s+(?:command|run|self-test|tests?|suite|check|tool|"
                          r"script))|"
                          r"(?:stdout|stderr)(?!\s+(?:handling|handler|stream|buffer|writes?|redirect\w*|"
                          r"parameter|argument|variable|usage|path)\b)|counts?|seconds|"
                          r"wall[\s-]?clock|elapsed|timings?|run\s*times?)\b"
                          r"|\boutput\s+of\s+`", re.I)
STATIC_SOURCE_RE = re.compile(r"\b(?:docstring|source|module|code|comments?|README|documentation|docs|diff|patch)"
                              r"\s+(?:documents|sets|defines|declares|lists|states|says|uses|describes|names|"
                              r"specifies)\b"
                              r"|\b(?:in|from)\s+the\s+(?:source|docstring|module|code|diff|patch|README|"
                              r"documentation|docs)\b(?!\s+(?:tree|checkout|directory))", re.I)
R6_AUX_RE = re.compile(r"(?:does|do|did|is|are|was|were|will|would|can|could)\b", re.I)
R6_CONFIRM_RE = re.compile(r"(?:confirm|verify|check|ensure|show|prove|establish|make\s+sure)\b"
                           r"(?:\s+(?:that|whether|if)\b)?", re.I)
R6_OUTCOME_RE = re.compile(r"\b(?:pass(?:es)?(?=\s*(?:[?.,;)]|\Z|\b(?:and|or|on|at|under|with|without|when|"
                           r"after|before|in|now|again|cleanly|locally|still|both)\b))"
                           r"|succeeds?\b|exits?\s+(?:with\s+)?(?:(?:exit\s+)?(?:code|status)\s+)?\d+\b"
                           r"|fails?\s+without\b|fails?\s+(?:on|at|against|with|under)\s+(?:the\s+)?(?:old|parent|"
                           r"base|previous|unfixed|original|pre-fix|prior)\b"
                           r"|turns?\s+red\b|go(?:es)?\s+green\b|stays?\s+green\b"
                           r"|(?:green|red)(?=\s*(?:[?.]|\Z|\b(?:on|at|in|under|now|again|locally)\b)))", re.I)
R6_RELATIVE_RE = re.compile(r"(?:\bthat|\bwhich|\bwho|\()\s*\Z", re.I)
R6_SUBJECT_RE = re.compile(r"\b(?:tests?|self-tests?|gates?|checks?|suites?|CI)\b|--(?:check|self-test)\b"
                           r"|(?<![\w./-])[\w./-]+\.(?:py|sh)\b", re.I)
NAMED_TOOL_RE = re.compile(r"(?<![\w./-])[\w./-]+\.(?:py|sh)\b|--(?:check|self-test)\b")
QUESTION_END_RE = re.compile(r"\?[\s*_\"')\]]*\Z")
INJECT_RE = re.compile(r"\binject", re.I)
R7_THE = r"(?:(?:the|this|that|a|an|each|every|its|their|your|two|both|these|those)\s+)*+"
R7_FACT_RE = re.compile(
    r"\b(?:commit|revision)(?:'s|\u2019s|s')\s+(?:parents?|SHAs?|hash(?:es)?|ids?|identity)\b"
    r"|\bparents?\s+(?:of|for)\s+" + R7_THE + r"(?:[\w-]+\s+)?(?:commits?|revisions?|merges?|HEAD)\b"
    r"|\bparent\s+(?:commits?|revisions?|SHAs?|hash(?:es)?)\b"
    r"|\b(?:in\s+)?(?:which|what)\s+(?:commits?|revisions?|merge)\s+(?:(?:first|last|originally|actually)\s+)?"
    r"(?:added|introduced|removed|deleted|changed|touched|modified|fixed|broke|landed|merged|reverted|created|"
    r"made|moved|renamed|is|was|holds|contains|did|does)\b"
    r"|\bin\s+(?:which|what)\s+(?:commits?|revisions?)\b"
    r"|\b(?:SHAs?|hash(?:es)?|ids?|identity)\s+of\s+" + R7_THE
    + r"(?:[\w-]+\s+)?(?:commits?|revisions?|merges?|HEAD|tip)\b"
    r"|\bdiff\s+(?:between|of)\s+" + R7_THE + r"(?:[\w-]+\s+)?(?:commits|revisions|merges|[0-9a-f]{7,40}\b)"
    r"|\b(?:what|which\s+[\w-]+)\s+changed\s+between\b"
    r"|\bchanges?\s+between\s+" + R7_THE + r"(?:commits|revisions|[0-9a-f]{7,40}\b)"
    r"|\bchanged\s+lines\b|\blines\s+(?:that\s+)?(?:were\s+)?changed\b"
    r"|\blines\s+(?:that\s+)?(?:were\s+)?(?:added|removed|deleted|modified|touched)\s+(?:by|in|between|since)\s+"
    + R7_THE + r"(?:[\w-]+\s+)?(?:commits?|revisions?|merges?|diff|change|patch|PR|pull\s+request)\b"
    r"|\b(?:observed|actual)\s+(?:exit\s+(?:codes?|status(?:es)?)|return\s*codes?|returncodes?)\b"
    r"|\b(?:exit\s+(?:codes?|status(?:es)?)|return\s*codes?|returncodes?)\s+(?:of|from)\s+" + R7_THE
    + r"(?:runs?|running|invocations?|executions?|command\s+you\s+ran|(?:self-)?test\s+runs?)\b"
    r"|\btest\s+counts?\b"
    r"|\bhow\s+many\s+tests?\s+(?:pass|passed|fail|failed|ran|were\s+run)\b"
    r"|\bnumber\s+of\s+(?:passing|failing|passed|failed)\s+tests\b"
    r"|\bnumber\s+of\s+tests\s+(?:that\s+)?(?:pass|passed|fail|failed|ran)\b", re.I)
R7_REQUEST_RE = re.compile(r"(?:quote|find|cite|name|list|give|report|state|identify|tell|show|say|determine|"
                           r"establish|confirm|verify|check(?!\s+out\b)|answer|print|paste|record|write|look\s+up|"
                           r"work\s+out|compute|what|which|who|when|where|how)\b", re.I)


def rule_r1(sentence):
    """[(offset, verb)]: an execution verb, or a try-gerund, at a clause start, outside a code span, not
    negated; and a must-be-run passive in a sentence that is not a question."""
    text, hits = sentence.text, []
    injects = bool(INJECT_RE.search(text))   # once per sentence, not once per verb
    for start in sentence.starts:
        if sentence.in_span(start):
            continue
        found = R1_VERB_RE.match(text, start) or R1_GERUND_RE.match(text, start)
        if not found or sentence.negated(found.start(), found.end()):
            continue
        if found.group(0).lower() == "observe" and not injects:
            continue
        hits.append((found.start(), found.group(0)))
    if not QUESTION_END_RE.search(text):
        for found in R1_PASSIVE_RE.finditer(text):
            if not sentence.in_span(found.start()) and not sentence.negated(found.start(), found.end()):
                hits.append((found.start(), found.group(0)))
    return hits


def r1_applies(block):
    """Whether R1 reads a prose block: a Markdown heading is a title, never a clause start."""
    return not block.heading


def _asks(sentence):
    """Whether a sentence asks for something: a question, or a request verb or wh-word at a clause start."""
    if QUESTION_END_RE.search(sentence.text):
        return True
    return any(R7_REQUEST_RE.match(sentence.text, start) for start in sentence.starts)


def rule_r7(sentence):
    """[(offset, fact)]: a fact only git or a run can establish, outside a code span and not negated, in a
    sentence that asks for something."""
    hits = []
    for found in R7_FACT_RE.finditer(sentence.text):
        if not sentence.in_span(found.start()) and not sentence.negated(found.start(), found.end()):
            hits.append((found.start(), found.group(0)))
    return hits if hits and _asks(sentence) else []


def rule_s1(block):
    """Whether a fence is executable: a shell or console tag, a python tag over a run prompt, or a plain or
    untagged fence opening with a command."""
    if block.tag in EXEC_FENCE_TAGS:
        return True
    first = next((line for _, line in block.lines if line.strip()), None)
    if first is None:
        return False
    if block.tag in PYTHON_TAGS:
        return bool(RUN_PROMPT_RE.match(first))
    return block.tag in PLAIN_TAGS and bool(SHELL_FIRST_LINE_RE.match(first))


def _outside(sentence, ranges, position):
    """Whether `position` is outside the sentence's code spans and outside the sorted, disjoint `ranges`."""
    index = bisect.bisect_right(ranges, (position, len(sentence.text) + 1)) - 1
    return not sentence.in_span(position) and not (index >= 0 and position < ranges[index][1])


def _words(regex, sentence, ranges=()):
    """The matches of `regex` in the sentence outside its code spans and `ranges`, in one pass."""
    return [found for found in regex.finditer(sentence.text) if _outside(sentence, ranges, found.start())]


def reference_intro(block):
    """Whether a prose block's last sentence introduces the fence after it as not to be run: it holds a
    not-to-be-run phrase, no exception or reversal word, and no execution verb but the prohibited one."""
    if block is None or block.kind != "prose":
        return False
    found = sentences(_masked_text(block).replace("\n", " "))
    if not found:
        return False
    last = Sentence(found[-1][1])
    return (bool(REFERENCE_INTRO_RE.search(last.text)) and not _words(EXCEPTION_RE, last)
            and len(_words(EXEC_WORD_RE, last)) <= 1)


class Demotions:
    """The data contexts of one sentence, each found in one pass over it, so that deciding a command line's
    demotion costs a bisection and a read of the DEMOTION_WINDOW characters before it."""

    def __init__(self, sentence, ranges):
        text = self.text = sentence.text
        self.sentence = sentence

        def exempt(found):
            """A veto word that opens a relative clause on the source text, or a verb-like noun."""
            window = max(0, found.start() - 16)
            return bool(RELATIVE_BEFORE_RE.search(text, window, found.start())
                        or (found.group(0).lower() in NOUN_AFTER_DETERMINER
                            and DETERMINER_BEFORE_RE.search(text, window, found.start())))

        reversal = bool(_words(REVERSAL_RE, sentence, ranges))
        self.quote_void = reversal or any(not exempt(found) for found in _words(QUOTE_VETO_RE, sentence, ranges))
        verbs = (QUOTE_VERB_RE.match(text, start) for start in sentence.starts if not sentence.in_span(start))
        self.quotes = sorted(found.end() for found in verbs
                             if found and not sentence.negated(found.start(), found.end()))
        self.nouns = [found.start() for found in SOURCE_NOUN_RE.finditer(text)]
        self.prohibition_void = (bool(_words(EXCEPTION_RE, sentence, ranges))
                                 or len(_words(EXEC_WORD_RE, sentence, ranges)) != 1)
        self.past = not reversal and bool(PAST_SUBJECT_RE.match(text, sentence.markup(0)))

    def quoted(self, start):
        """Whether the command line at `start` is in the object of a Quote, Cite or Show-the-text-of request
        that names source text before it, with no stop between the verb and the command."""
        index = bisect.bisect_right(self.quotes, start) - 1
        if self.quote_void or index < 0:
            return False
        verb_end, stops = self.quotes[index], self.sentence.stops
        stop = bisect.bisect_left(stops, verb_end)
        noun = bisect.bisect_left(self.nouns, verb_end)
        return ((stop == len(stops) or stops[stop] >= start) and noun < len(self.nouns) and self.nouns[noun] < start
                and not SOURCE_OF_RE.search(self.text, max(0, start - DEMOTION_WINDOW), start))

    def __call__(self, start):
        """The demotion of the command line at `start` ("quoted source text", "prohibited" or "past
        report"), or None."""
        window = max(0, start - DEMOTION_WINDOW)
        if self.quoted(start):
            return "quoted source text"
        if not self.prohibition_void and PROHIBITED_RE.search(self.text, window, start):
            return "prohibited"
        if self.past and PAST_BEFORE_RE.search(self.text, window, start):
            return "past report"
        return None


def _option_end(text, position, option_re, memo):
    """The offset after the run of options `option_re` matches from `position`, with no bound on their number.
    Every offset walked is remembered in `memo`, so the option runs of all the command words in a sentence
    cost one pass in total, however many words share one run ("git -c git -c ... show")."""
    walked = []
    while position not in memo:
        found = option_re.match(text, position)
        if not found:
            memo[position] = position
            break
        walked.append(position)
        position = found.end()
    end = memo[position]
    for offset in walked:
        memo[offset] = end
    return end


def option_commands(text):
    """[(start, end)] of the command lines that open with git (global options, then a git subcommand) or
    python (options, then -c, -m or a .py path)."""
    found, memos = [], (dict(), dict())
    for word in COMMAND_WORD_RE.finditer(text):
        git = word.group(1) is not None
        end = _option_end(text, word.end(), GIT_OPTION_RE if git else PYTHON_OPTION_RE, memos[0 if git else 1])
        target = (GIT_SUBCOMMAND_RE if git else PYTHON_TARGET_RE).match(text, end)
        if target:
            found.append((word.start(), target.end()))
    return found


def rule_commands(sentence):
    """[(offset, command line, demotion or None)]: every command line in the sentence, in prose and in code
    spans alike."""
    text = sentence.text
    found = option_commands(text) + [match.span() for match in COMMAND_RE.finditer(text)]
    found.sort()
    starts = [start for start, _ in found]
    for start, end in sentence.spans:
        index = bisect.bisect_left(starts, start)
        if (index == len(starts) or starts[index] >= end) and SPAN_COMMAND_RE.match(text, start):
            found.append((start, end))
    if not found:
        return []
    found.sort()
    ranges = []   # the command lines' own text, merged; no context word is read from it
    for start, end in found:
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))
    demotion = Demotions(sentence, ranges)
    return [(start, text[start:min(end, start + EXCERPT_CHARS // 2)].strip("`").strip(), demotion(start))
            for start, end in found]


def _last_start(regex, text):
    """The last offset at which `regex` matches in `text`, or -1. Each search resumes one character after the
    previous match's start, so every offset is tried once and an overlapping match is not missed."""
    last, position = -1, 0
    while True:
        found = regex.search(text, position)
        if not found:
            return last
        last, position = found.start(), found.start() + 1


def rule_r5(sentence):
    """[(offset, verb)]: a reporting verb at a clause start, not negated, then an execution result, in a
    sentence that names no static source after the verb."""
    text, hits, last = sentence.text, [], None
    for start in sentence.starts:
        found = R5_VERB_RE.match(text, start)
        if not found or sentence.negated(found.start(), found.end()):
            continue
        if last is None:   # the last result and the last static source, found once per sentence
            last = _last_start(R5_RESULT_RE, text), _last_start(STATIC_SOURCE_RE, text)
        if last[0] >= found.end() and last[1] < found.end():
            hits.append((found.start(), found.group(0)))
    return hits


def rule_r6(sentence):
    """[(offset, names a tool)]: an outcome question (ending with `?`), or a confirm-that sentence, about a
    test, a gate, or a tool."""
    text, hits = sentence.text, []
    question = bool(QUESTION_END_RE.search(text))
    outcomes = list(R6_OUTCOME_RE.finditer(text))
    outcome_starts = [found.start() for found in outcomes]
    for start in sentence.starts:
        opener = (R6_AUX_RE.match(text, start) if question else None) or R6_CONFIRM_RE.match(text, start)
        if not opener:
            continue
        index = bisect.bisect_left(outcome_starts, opener.end())
        if index >= len(outcomes):
            continue
        if outcomes[index].start() - opener.end() > R6_SUBJECT_MAX:   # measured before slicing
            continue
        subject = text[opener.end():outcomes[index].start()]
        if R6_RELATIVE_RE.search(subject):
            continue
        if R6_SUBJECT_RE.search(subject):
            hits.append((opener.start(), bool(NAMED_TOOL_RE.search(subject))))
    return hits


# ---------- evaluation ----------

def _excerpt(text):
    text = " ".join(text.split())
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS - 3] + "..."


def lint_sentence(raw, line, read_r1=True, structural="S3"):
    """[(rule, severity, line, message, excerpt)] for one sentence: its command lines under the structural
    rule `structural` (S2 in a numbered item, S3 outside; None reads none), HARD unless demoted, then the
    ADVISORY rules; `read_r1` is false in a heading."""
    sentence = Sentence(raw)
    excerpt = _excerpt(raw)
    findings = []
    if structural is not None:
        where = "in a numbered item" if structural == "S2" else "outside the numbered items"
        for _, command, demotion in rule_commands(sentence):
            if demotion is None:
                findings.append((structural, "HARD", line, "command line {!r} {}".format(command, where), excerpt))
            else:
                findings.append((structural, "INFO", line, "command line {!r} {}, {}: not a request".format(
                    command, where, demotion), excerpt))
    for _, verb in (rule_r1(sentence) if read_r1 else []):
        findings.append(("R1", "ADVISORY", line, "execution request {!r} at a clause start".format(verb), excerpt))
    for _, verb in rule_r5(sentence):
        findings.append(("R5", "ADVISORY", line, "asks to {} an execution result".format(verb.lower()), excerpt))
    for _, fact in rule_r7(sentence):
        findings.append(("R7", "ADVISORY", line, "asks for {!r}, which only git or a run can establish".format(
            fact), excerpt))
    for _, named in rule_r6(sentence):
        findings.append(("R6", "ADVISORY", line,
                         "asks for a test outcome" + (" of a named tool" if named else ""), excerpt))
    return findings


def _masked_text(block):
    text = block.text()
    if not block.masked:
        return text
    pieces, position = [], 0
    for start, end in sorted(block.masked):
        start = max(start, position)
        if end > start:
            pieces.extend((text[position:start], " " * (end - start)))
            position = end
    pieces.append(text[position:])
    return "".join(pieces)


def command_rule(block):
    """The blocking rule a command line in a prose block falls under: S2 in a numbered item, S3 outside."""
    return "S2" if block.item is not None else "S3"


def asks_to_run(findings):
    """Whether a block's findings hold an R1 or R5 hit: an execution request or a request for a result."""
    return any(rule in ("R1", "R5") for rule, _, _, _, _ in findings)


def cancel_demotions(findings):
    """A block's findings with every demoted command line HARD again: a block that also asks to run something
    or to report a result (R1 or R5) is not a data context."""
    return [(rule, "HARD", line, message.replace(": not a request", ", cancelled: the block also asks to run or "
                                                 "report (R1 or R5)"), excerpt)
            if severity == "INFO" else (rule, severity, line, message, excerpt)
            for rule, severity, line, message, excerpt in findings]


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
    findings, previous, previous_asks = [], None, False
    for block in blocks:
        if block.kind == "fence":
            if rule_s1(block):
                first = next((line for _, line in block.lines if line.strip()), "")
                intro = not previous_asks and reference_intro(previous)
                findings.append(("S1", "INFO" if intro else "HARD", block.start, "executable fence ({}{})".format(
                    block.tag or "untagged", ", introduced as not to be run" if intro else ""), _excerpt(first)))
            previous, previous_asks = block, False
            continue
        previous = block
        line_starts, offset = [], 0
        for _, line in block.lines:
            line_starts.append(offset)
            offset += len(line) + 1
        structural = command_rule(block)
        found = []
        for start, sentence in sentences(_masked_text(block).replace("\n", " ")):
            line = block.lines[bisect.bisect_right(line_starts, start) - 1][0]
            found.extend(lint_sentence(sentence, line, r1_applies(block), structural))
        previous_asks = asks_to_run(found)
        findings.extend(cancel_demotions(found) if previous_asks else found)
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
        print("REFUSE: family {} cannot execute and the brief carries a structural execution request ({} hard "
              "hit(s): {}); rewrite the brief or send these items to a family that can execute".format(
                  family, len(hard), ", ".join("{} x{}".format(r, hard.count(r)) for r in sorted(set(hard)))))
    elif can_execute(table[family]):
        print("OK: family {} can execute ({}); the execution rules do not apply".format(
            family, table[family]["execute"]))
    else:
        advisory = sum(1 for _, severity, _, _, _ in findings if severity == "ADVISORY")
        print("OK: family {}: no structural hit ({} advisory, {} info); read the ADVISORY lines".format(
            family, advisory, len(findings) - advisory))
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
    family, table_path, scoped, paths = None, None, True, []
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--family" and args and family is None:
            family = args.pop(0)
        elif arg == "--table" and args and table_path is None:
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
    return run(paths[0], family, ROOT / TABLE_REL if table_path is None else table_path, scoped)


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


RUN_ITEM = "3. Run `t.py --self-test` on a snapshot of the change.\n"
RUN_PROSE = "3. Run the parser on a snapshot of the change.\n"
NEGATED = "Do not run code or git.\n"
CLAUSE_MID = "Describe the build of the parser.\n"
BASH_FENCE = "Steps:\n\n```bash\npython3 t.py\n```\n"
PYCON_FENCE = "Steps:\n\n```pycon\n>>> import t\n```\n"
PYTHON_PROMPT_FENCE = "Steps:\n\n```python\n>>> import t\n```\n"
UNTAGGED_FENCE = "Steps:\n\n```\n$ git status\n```\n"
PLAIN_FENCE = "```\ncd repo\npython3 t.py\n```\n"
REFERENCE_FENCE = "The README shows this; do not run it:\n\n```bash\npython3 t.py\n```\n"
REFERENCE_UNTIL = "Do not run this until you read the notes:\n\n```bash\npython3 t.py\n```\n"
QUOTE_SELF_TEST = "Quote the code that runs `t.py --self-test`.\n"
MENTION = "CI failed at abc1234 on `t.py --self-test` at the parent.\n"
PROHIBITION = "Do not run `t.py --self-test` even if it passes.\n"
PROHIBITION_EXCEPT = "Do not run `t.py --self-test` without the flag.\n"
GIT_SHOW_PREAMBLE = "Read only `git show abc1234:<path>` in the worktree.\n\n1. Quote the helper.\n"
GIT_SHOW_PROSE = "Answer by reading files at commit abc1234 (git show abc1234:<path>).\n"
GIT_ITEM = "1. Answer from a git show attempt's result.\n"
PYTHON_COMMAND = "Read the output of python3 -I -B t.py first.\n"
DOT_SLASH = "1. Use ./t.sh for the setup.\n"
BASH_C = "1. Use bash -c with the helper.\n"
PYTEST = "1. Use pytest -q on the module.\n"
SPAN_TOOL = "1. Say what `make test` prints.\n"
DESCRIBED_PROSE = "1. In t.py, quote the line in it that runs git show-ref.\n"
CONFIG_NOUN = "1. Name the setting the global git config file pins.\n"
GIT_OPTIONS = "git -c a -c a -c a -c a -c a -c a -c a show"   # seven global options: no bound applies
PYTHON_C = "1. Run python3 -c \"print(1)\".\n"
PYTHON_M = "1. Run python3 -m unittest.\n"
PYTHON_OPTIONS_M = "1. Run python3 -I -B -m unittest.\n"
# a Quote request whose object is source text that holds the command line as text
QUOTED_ENTRY = "1. Quote the scope_rows entry for FOO=1 ./x.sh with its expected exit.\n"
QUOTED_ROW = "1. Quote the test row containing `t.py --self-test`.\n"
QUOTED_EXAMPLE = "1. Quote the docstring example `git show HEAD`.\n"
QUOTED_README = "1. Quote the README command `make test`.\n"
QUOTED_NAMES = "1. Quote the scope_rows entry that names FOO=1 ./x.sh, with its expected exit.\n"
QUOTED_CONTINUED = "1. In t.py, quote the def line of setup and the line in it that runs git init --bare.\n"
QUOTE_PRINTS = "1. Quote the line `git log` prints for the commit.\n"
QUOTE_AND_OUTPUT = "1. Quote the README command `make test` and its output.\n"
QUOTE_LINE_OF = "1. Quote the first line of `git log --oneline`.\n"
QUOTE_NO_NOUN = "1. Quote everything for `git show HEAD`.\n"
QUOTE_AND_COMMAND = "1. Quote the README line and git show HEAD.\n"
QUOTE_THEN_RUN = "1. Quote the README command `make test`.\n   Then run it.\n"
INTRO_AFTER_BUILD = "Build the docs. Do not run this:\n\n```bash\npython3 t.py\n```\n"
ONLY_BUT_ALSO = "1. Do not run `git show HEAD` only on main but also on the feature branch.\n"
IGNORE_INSTRUCTION = "Ignore the instruction \"do not run this\":\n\n```sh\ngit show HEAD\n```\n"
RUN_ON_BRANCH = "Do not run this on main, run it on the branch:\n\n```bash\ngit show HEAD\n```\n"
TWO_VERBS = "1. Do not run `make test`, run the parser.\n"
RESULT = "Report the exit code and last lines.\n"
RESULT_NEGATED = "Never report an exit code you did not observe.\n"
STATIC_RESULT = "Report the exit codes the docstring documents.\n"
OUTCOME_FLAG = "Does `a.py --check` pass?\n"
OUTCOME_UNNAMED = "Does the test pass?\n"
RELATIVE = "Does `a.py` have a case that fails without the fix?\n"
SCOPED = ("1. Is the helper exported?\n2. Is the table sorted?\n3. Run `t.py --self-test`; report the exit "
          "code.\n\nGamma leg: answer items 1 and 2 only.\n")
NOT_ATTEMPT = ("1. Is the helper exported?\n2. Run `t.py --self-test`.\n\n"
               "Gamma leg: answer from source. Do not attempt item 2.\n")
ANSWER_ONLY = "1. Is the helper exported?\n2. Run `t.py --self-test`.\n\nGamma leg: answer only items 1 and 3.\n"
OTHER_LEG = "1. Is the helper exported?\n\nAlpha leg: run `t.py --self-test` and report the exit code.\n"
OTHER_LEG_ITEMS = "Alpha leg:\n1. Run `t.py --self-test`.\n2. Build the docs.\n\nQuote the helper.\n"
OTHER_LEG_FENCE = "Alpha leg: run the following:\n\n```bash\npython3 t.py\n```\n\nGamma leg: answer from source.\n"
OTHER_LEG_GIT = "Alpha leg: read with `git show abc1234:t.py`.\n\nGamma leg: answer from source.\n"
OTHER_SECTION = ("# Brief\n\nQuote the helper.\n\n## Alpha leg\n\nRun `t.py --self-test`.\n\n## Done\n\n"
                 "Nothing else.\n")
SECTION_ENDS = "## Alpha leg\n\nRun `t.py --self-test`.\n\n## Gamma\n\nRun `t.py --self-test`.\n"
TAIL_MARKER = "1. Is the helper exported?\n2. Run `t.py --self-test` (alpha leg).\n"
TAIL_IN_CODE = "1. Is the helper exported?\n2. Run `t.py --self-test (alpha leg)` now.\n"
TAIL_MID_LINE = "1. Is the helper exported?\n2. Run `t.py --self-test` (alpha only) on the snapshot.\n"
TAIL_IN_WRAPPED_CODE = "1. Is the helper exported?\n2. Run `t.py --self-test` with `x\n   (alpha leg)` set.\n"
CODE_SPAN_DIRECTIVE = "1. Run `t.py --self-test`.\n\nGamma leg: see `x: answer items 2 only` in the notes.\n"
CODE_SPAN_MARKER = "Gamma leg: read the parser for `Alpha leg:` handling. Run `t.py --self-test`.\n"
MID_LINE_MARKER = "Read the notes on the alpha leg: run `t.py --self-test`.\n"
QUOTED_DIRECTIVE = "1. Run `t.py --self-test`.\n\nGamma leg: the parser accepts \"answer items 2 only\" as input.\n"
ADDRESS_ENDS = "Alpha leg: run `t.py --self-test`. Gamma: run `t.py --self-test` too.\n"
ALL_LEGS_ENDS = "Alpha leg: lint the tree. All legs: run `t.py --self-test`.\n"
REVERSED = "Gamma leg: answer items 2-1 only.\n\n1. Run the parser.\n"
DASH_LIST = "- Read the diff\n- Run `t.py --self-test`\n- Report the exit code\n"
BOM_BRIEF = b"\xef\xbb\xbf```bash\ncd repo\n```\n"   # a byte order mark before the fence opener
ZERO_WIDTH_SPLIT = "Read only `git sh" + chr(0x200B) + "ow abc1234:t.py`.\n"   # a zero-width space inside "show"
# "no" in "casino" ends exactly NEGATION_WINDOW characters before "run": a slice of the window would
# open on a false word boundary and read it as a negation.
SLICE_EDGE = "Read the casino" + " w" * 25 + " ww and run the parser.\n"
NEAR_NEGATION = "Do not" + " w" * 22 + " and run the parser.\n"     # 49 characters: negated
FAR_NEGATION = "Do not" + " w" * 30 + " and run the parser.\n"      # 65 characters: not negated
THEN_NEGATION = "Do not edit files, then run the parser.\n"
SUBORDINATE = "If the patch does not apply, run the parser.\n"
HEADING_NOUN = "# Notes (source only): parser train, rebuild 3; copy and launch wiring\n\nQuote the helper.\n"
HEADING_COMMAND = "# Step 2: git diff abc1234\n\nQuote the helper.\n"
PAREN_NOUN = "Quote the sentence naming the read (execute) flag.\n"
ADJECTIVE_NOUN = "Quote the code that turns a stalled (TIMED-OUT) run into the row's verdict.\n"
OPENING_PARENTHESIS = "2. (Optional) Run the benchmark on the snapshot.\n"
OR_LIST = "Each lint, gate, test, build or self-test writes a log.\n"
PUNCTUATED_LIST = "Name the stage: lint, gate, test, build or self-test.\n"
OXFORD_PARENTHESIS = "Quote the line (test, build, or run).\n"
GIT_PARENTS = "1. Quote the parents of the merge commit as the file headers list them.\n"
GIT_STATEMENT = "The merge commit's parents are listed in the notes.\n"
LINEAR_FACTOR, LINEAR_SLACK, LINEAR_TIMEOUT = 16, 1.0, 120
# The child caps its address space at 1 GiB, then times evaluate() on 128 KiB and 1 MiB briefs, each a prefix,
# one unit repeated, and a suffix.
LINEAR_CHILD = (
    "import resource, sys, time\n"
    "hard = resource.getrlimit(resource.RLIMIT_AS)[1]\n"
    "soft = 1 << 30 if hard == resource.RLIM_INFINITY else min(1 << 30, hard)\n"
    "resource.setrlimit(resource.RLIMIT_AS, (soft, hard))\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import check_brief_capability as m\n"
    "table = m.parse_table(m.TABLE_FIXTURE.encode('utf-8'))\n"
    "prefix, unit, suffix = sys.argv[2:5]\n"
    "for size in (1 << 17, 1 << 20):\n"
    "    body = size - len(prefix) - len(suffix)\n"
    "    data = (prefix + (unit * (body // len(unit) + 1))[:body] + suffix).encode('utf-8')\n"
    "    began = time.perf_counter()\n"
    "    m.evaluate(data, 'gamma', table)\n"
    "    print(time.perf_counter() - began)\n")


def _linear(unit, prefix="", suffix=""):
    """True when evaluate() on a 1 MiB brief (`prefix`, `unit` repeated, `suffix`) takes at most LINEAR_FACTOR
    times as long as on the 128 KiB one plus LINEAR_SLACK seconds, both timed in a child capped at 1 GiB
    (RLIMIT_AS) and stopped after LINEAR_TIMEOUT seconds; otherwise what went wrong. The child must exit 0
    and print exactly two finite, nonnegative times."""
    try:
        done = subprocess.run([sys.executable, "-I", "-B", "-c", LINEAR_CHILD, str(Path(__file__).resolve().parent),
                               prefix, unit, suffix], capture_output=True, text=True, timeout=LINEAR_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "timed out after {} s".format(LINEAR_TIMEOUT)
    try:
        small, large = (float(value) for value in done.stdout.split())
    except ValueError:
        small = large = float("nan")
    if done.returncode != 0 or not all(0.0 <= value < float("inf") for value in (small, large)):
        return "child exit {}, output {!r}: {}".format(done.returncode, done.stdout.strip()[-80:],
                                                      done.stderr.strip()[-200:])
    return large <= LINEAR_FACTOR * small + LINEAR_SLACK or "1 MiB took {:.3f} s, 128 KiB {:.3f} s".format(
        large, small)


def _bounded_option_end(text, position, option_re, memo):
    """_option_end with a bound of six options put back (the round-4 git option rule)."""
    for _ in range(6):
        found = option_re.match(text, position)
        if not found:
            break
        position = found.end()
    return position


class _EveryWordSentence(Sentence):
    """A Sentence whose every word is a clause start (the clause-start rule patched out)."""

    def __init__(self, text):
        super().__init__(text)
        self.starts = [found.start() for found in re.finditer(r"\b\w", text)]


def _legacy_leg_spans(blocks, families):
    """leg_spans with the operative-position rule patched out: a marker anywhere claims the paragraph."""
    spans = []
    for index, block in enumerate(blocks):
        if block.kind == "prose" and not block.heading:
            for found in re.finditer(r"([A-Za-z][A-Za-z0-9-]*)\s+leg\s*:", block.text()):
                if found.group(1).lower() in families:
                    spans.append((index, found.start(), found.group(1).lower(), ""))
    return spans


def _legacy_scope_directives(text):
    """scope_directives with the clause-start rule patched out: a directive anywhere counts."""
    kept = None
    for found in ONLY_RE.finditer(text):
        if found.group(2):
            kept = (kept or set()) | parse_item_list(found.group(1))
    return kept, set()


class _NoFormatRemoval:
    """A unicodedata stand-in that reports no format character (format-character removal patched out)."""

    @staticmethod
    def category(ch):
        return "Cc" if ch in "\x00" else ("Zs" if ch.isspace() else "Ll")


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
    check("table/bool-format-version-2",
          _table_outcome(TABLE_FIXTURE.replace("format-version = 1", "format-version = true")), "CannotEvaluate")
    check("table/float-format-version-2",
          _table_outcome(TABLE_FIXTURE.replace("format-version = 1", "format-version = 1.0")), "CannotEvaluate")
    check("table/invalid-family-name-2", _table_outcome(TABLE_FIXTURE.replace('"beta"', '"Beta two"')),
          "CannotEvaluate")
    check("table/not-toml-2", _table_outcome("[[family]\n"), "CannotEvaluate")
    # ---------- structure ----------
    check("structure/dash-list-refused", _outcome(DASH_LIST),
          (1, [("R1", "ADVISORY"), ("R5", "ADVISORY"), ("S3", "HARD")]))
    check("structure/nested-list-refused",
          _outcome("1. Read the diff:\n   - check the names\n   - run `t.py --self-test`\n"),
          (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("structure/indented-fence-refused",
          _outcome("1. Steps for the check:\n\n    ```bash\n    python3 t.py\n    ```\n"), (1, [("S1", "HARD")]))
    check("structure/bom-refused", _outcome(BOM_BRIEF), (1, [("S1", "HARD")]))
    check("structure/zero-width-split-refused", _outcome(ZERO_WIDTH_SPLIT), (1, [("S3", "HARD")]))
    check("structure/indented-code-refused", _outcome("    git show HEAD\n"), (1, [("S3", "HARD")]))
    # ---------- S1: an executable fence ----------
    check("s1/bash-fence-refused", _outcome(BASH_FENCE), (1, [("S1", "HARD")]))
    check("s1/pycon-fence-refused", _outcome(PYCON_FENCE), (1, [("S1", "HARD")]))
    check("s1/python-prompt-refused", _outcome(PYTHON_PROMPT_FENCE), (1, [("S1", "HARD")]))
    check("s1/untagged-command-fence-refused", _outcome(UNTAGGED_FENCE), (1, [("S1", "HARD")]))
    check("s1/plain-command-fence-refused", _outcome(PLAIN_FENCE), (1, [("S1", "HARD")]))
    check("s1/text-tagged-command-refused", _outcome("```text\npython3 t.py --self-test\n```\n"),
          (1, [("S1", "HARD")]))
    check("s1/first-line-forms-refused", [_outcome("```\n" + line + "\n```\n")[0] for line in (
        "python3.14 t.py --self-test", "FOO=1 python3 t.py", "make test", "/usr/bin/git show HEAD")], [1, 1, 1, 1])
    check("s1/python-fence-ok", _outcome("Steps:\n\n```python\nx = 1\n```\n"), (0, []))
    check("s1/fence-body-not-prose", _outcome("```text\nRun the parser.\n```\n"), (0, []))
    check("s1/reference-intro-info", _outcome(REFERENCE_FENCE), (0, [("S1", "INFO")]))
    check("s1/reference-until-refused", _outcome(REFERENCE_UNTIL), (1, [("S1", "HARD")]))
    check("s1/for-reference-refused", _outcome("For reference, run the following:\n\n```bash\npython3 t.py\n```\n"),
          (1, [("R1", "ADVISORY"), ("S1", "HARD")]))
    # ---------- S2 and S3: a command line, in a numbered item or outside ----------
    check("s2/tool-item-refused", _outcome(RUN_ITEM), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("s2/git-item-refused", _outcome(GIT_ITEM), (1, [("S2", "HARD")]))
    check("s2/dot-slash-refused", _outcome(DOT_SLASH), (1, [("S2", "HARD")]))
    check("s2/bash-c-refused", _outcome(BASH_C), (1, [("S2", "HARD")]))
    check("s2/pytest-refused", _outcome(PYTEST), (1, [("S2", "HARD")]))
    check("s2/span-tool-refused", _outcome(SPAN_TOOL), (1, [("S2", "HARD")]))
    check("s2/find-clause-not-demoted-refused", _outcome("1. Find the test by running `t.py --self-test`.\n"),
          (1, [("S2", "HARD")]))
    check("s2/quoted-span-not-demoted-refused", _outcome("1. Quote `git log --oneline` for the last commits.\n"),
          (1, [("S2", "HARD")]))
    check("s2/python-c-refused", _outcome(PYTHON_C), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("s2/python-m-refused", _outcome(PYTHON_M), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("s2/python-options-m-refused", _outcome(PYTHON_OPTIONS_M), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("s2/python-forms-refused", [_outcome("1. Use " + command + ".\n")[0] for command in (
        "python3 -X dev t.py", "python3 -W error t.py", "/usr/bin/python3 t.py", ".venv/bin/python t.py",
        "python3 -Ic pass")], [1, 1, 1, 1, 1])
    check("s2/git-forms-refused", [_outcome("1. Use " + command + ".\n")[0] for command in (
        "git -P log -1", "git --git-dir /r/.git log -1", "git show-branch", "git annotate t.py")], [1, 1, 1, 1])
    check("s2/quoted-relative-info", _outcome(DESCRIBED_PROSE), (0, [("S2", "INFO")]))
    check("s2/mentions-quoted-info", _outcome("1. Quote the sentence that mentions \"git branch --list\".\n"),
          (0, [("S2", "INFO")]))
    check("s2/file-name-ok", _outcome("1. In `tools/t.py`, quote the parser.\n"), (0, []))
    check("s2/config-noun-ok", _outcome(CONFIG_NOUN), (0, []))
    check("s2/git-word-ok", _outcome("1. Quote the git history notes; Git shows them.\n"), (0, []))
    check("s2/span-word-ok", _outcome("1. Quote the `make` target and the `pytest` fixture.\n"), (0, []))
    check("s3/git-show-refused", _outcome(GIT_SHOW_PREAMBLE), (1, [("S3", "HARD")]))
    check("s3/git-show-prose-refused", _outcome(GIT_SHOW_PROSE), (1, [("S3", "HARD")]))
    check("s3/python-refused", _outcome(PYTHON_COMMAND), (1, [("S3", "HARD")]))
    check("s3/heading-refused", _outcome(HEADING_COMMAND), (1, [("S3", "HARD")]))
    check("s3/quoted-relative-info", _outcome(QUOTE_SELF_TEST), (0, [("S3", "INFO")]))
    check("s3/past-report-info", _outcome(MENTION), (0, [("S3", "INFO")]))
    check("s3/prohibited-info", _outcome(PROHIBITION), (0, [("S3", "INFO")]))
    check("s3/prohibited-git-info", _outcome("Never use `git show`; read the files you are given.\n"),
          (0, [("S3", "INFO")]))
    check("s3/prohibition-exception-refused", _outcome(PROHIBITION_EXCEPT), (1, [("S3", "HARD")]))
    check("s3/described-other-clause-refused", _outcome("Quote the line that runs it, then git show abc1234.\n"),
          (1, [("S3", "HARD")]))
    check("s3/git-options-unbounded", (bool(option_commands("git -C x --no-pager show")),
                                       bool(option_commands(GIT_OPTIONS))), (True, True))
    # ---------- quoted source text: a command line held as data ----------
    check("quoted/scope-row-entry-ok", _outcome(QUOTED_ENTRY), (0, [("S2", "INFO")]))
    check("quoted/test-row-ok", _outcome(QUOTED_ROW), (0, [("S2", "INFO")]))
    check("quoted/docstring-example-ok", _outcome(QUOTED_EXAMPLE), (0, [("S2", "INFO")]))
    check("quoted/readme-command-ok", _outcome(QUOTED_README), (0, [("S2", "INFO")]))
    check("quoted/entry-that-names-ok", _outcome(QUOTED_NAMES), (0, [("S2", "INFO")]))
    check("quoted/continued-object-ok", _outcome(QUOTED_CONTINUED), (0, [("S2", "INFO")]))
    check("quoted/call-noun-ok", _outcome("1. In t.py, quote the line that runs git rev-parse --absolute-git-dir (or "
                                          "the call that obtains the git dir).\n"), (0, [("S2", "INFO")]))
    check("quoted/unnumbered-ok", _outcome(QUOTED_EXAMPLE[3:]), (0, [("S3", "INFO")]))
    check("quoted/output-of-refused", _outcome("1. Quote the output of `make test`.\n"), (1, [("S2", "HARD")]))
    check("quoted/what-prints-refused", _outcome("1. Quote what `git log` prints.\n"), (1, [("S2", "HARD")]))
    check("quoted/line-prints-refused", _outcome(QUOTE_PRINTS), (1, [("S2", "HARD")]))
    check("quoted/and-output-refused", _outcome(QUOTE_AND_OUTPUT), (1, [("S2", "HARD")]))
    check("quoted/line-of-refused", _outcome(QUOTE_LINE_OF), (1, [("S2", "HARD")]))
    check("quoted/no-source-noun-refused", _outcome(QUOTE_NO_NOUN), (1, [("S2", "HARD")]))
    check("quoted/and-command-refused", _outcome(QUOTE_AND_COMMAND), (1, [("S2", "HARD")]))
    check("quoted/and-run-refused", _outcome("1. Quote the docstring example `git show HEAD` and run it.\n"),
          (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("quoted/then-run-cancelled-refused", _outcome(QUOTE_THEN_RUN), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    # ---------- a reversal, an exception or a request never demotes ----------
    check("demote/do-it-now-refused", _outcome("1. Use the helper that runs `git show HEAD` and do it now.\n"),
          (1, [("S2", "HARD")]))
    check("demote/only-but-also-refused", _outcome(ONLY_BUT_ALSO), (1, [("S2", "HARD")]))
    check("demote/ignore-instruction-refused", _outcome(IGNORE_INSTRUCTION), (1, [("S1", "HARD")]))
    check("demote/run-it-on-branch-refused", _outcome(RUN_ON_BRANCH), (1, [("S1", "HARD")]))
    check("demote/intro-after-request-refused", _outcome(INTRO_AFTER_BUILD), (1, [("R1", "ADVISORY"), ("S1", "HARD")]))
    check("demote/two-verbs-refused", _outcome(TWO_VERBS), (1, [("S2", "HARD")]))
    check("demote/exception-words-refused", [_outcome(text)[0] for text in (
        "1. If you cannot run `t.py --self-test`, say so; otherwise paste its output.\n",
        "1. Do not run `t.py --self-test` more than once, and report its exit code.\n",
        "1. Don't use `git show` alone; use it with --stat.\n",
        "1. Do not use `git show abc` but rather its --stat form.\n",
        "1. Never call `t.py --self-test` directly; call it through tox and report.\n")], [1, 1, 1, 1, 1])
    check("demote/request-not-past-report-refused", [_outcome(text)[0] for text in (
        "1. Confirm the fix passed with `pytest -q tests/`.\n",
        "1. Paste the output from when you ran `pytest -q tests/`.\n")], [1, 1])
    # ---------- time: every scan is one pass per sentence ----------
    check("perf/word-run-token-start", WORD_RUN_RE.search("aaaa, b", 1), None)
    with _patched(LINEAR_CHILD="import sys\nprint(0.1)\nprint(0.2)\nsys.exit(9)\n"):
        failed_child = _linear("a")
    with _patched(LINEAR_CHILD="print('nan')\nprint('nan')\n"):
        nan_child = _linear("a")
    check("perf/failed-child-not-linear", (failed_child is True, nan_child is True), (False, False))
    for check_id, unit, prefix, suffix in (
            ("perf/single-word-linear", "a", "", ""),
            ("perf/noun-run-linear", "a, ", "", ""),
            ("perf/git-options-linear", "git -c ", "", ""),
            ("perf/python-options-linear", "python3 -X ", "", ""),
            ("perf/prohibited-span-linear", "do not run `git status` and ", "", ""),
            ("perf/prohibited-prose-linear", "Do not run git show ", "", ""),
            ("perf/prohibited-item-linear", "never run git log ", "1. ", ""),
            ("perf/report-and-linear", "report and ", "", ""),
            ("perf/report-comma-linear", "report, ", "", ""),
            ("perf/tail-marker-linear", "   `a` (zeta leg)\n", "1. x\n", ""),
            ("perf/leg-marker-linear", "Alpha leg: x.\n", "", ""),
            ("perf/markup-run-linear", "1) ", "", ""),
            ("perf/address-markup-linear", "Q1. ", "Alpha leg: x. ", ""),
            ("perf/observe-run-linear", "observe, ", "", ""),
            ("perf/outcome-subject-linear", "does, ", "", " the test pass?"),
            ("perf/quote-context-linear", "Quote the line for `git show` and ", "", "")):
        check(check_id, _linear(unit, prefix, suffix), True)
    # ---------- the advisory rules: printed, never refusing ----------
    check("r1/run-prose-advisory", _outcome(RUN_PROSE), (0, [("R1", "ADVISORY")]))
    check("r1/negated-ok", _outcome(NEGATED), (0, []))
    check("r1/mid-clause-verb-ok", _outcome(CLAUSE_MID), (0, []))
    check("r1/and-joined-advisory", _outcome("Read the diff and run the parser.\n"), (0, [("R1", "ADVISORY")]))
    check("r1/passive-advisory", _outcome("The parser must be run before you answer.\n"), (0, [("R1", "ADVISORY")]))
    check("r1/noun-phrase-ok", _outcome("Build failures in CI are listed in the log.\n\nRun order: which gate "
                                        "comes first?\n\nInstall steps are documented.\n"), (0, []))
    check("r1/heading-not-a-clause-ok", _outcome(HEADING_NOUN), (0, []))
    check("r1/parenthesis-noun-ok", _outcome(PAREN_NOUN), (0, []))
    check("r1/adjective-parenthesis-noun-ok", _outcome(ADJECTIVE_NOUN), (0, []))
    check("r1/oxford-parenthesis-ok", _outcome(OXFORD_PARENTHESIS), (0, []))
    check("r1/opening-parenthesis-advisory", _outcome(OPENING_PARENTHESIS), (0, [("R1", "ADVISORY")]))
    check("r1/or-noun-list-ok", _outcome(OR_LIST), (0, []))
    check("r1/punctuated-noun-list-ok", _outcome(PUNCTUATED_LIST), (0, []))
    check("r1/verb-in-code-span-ok", _outcome("See the `build and run` target.\n"), (0, []))
    check("negation/slice-edge-advisory", _outcome(SLICE_EDGE), (0, [("R1", "ADVISORY")]))
    check("negation/window-near-ok", _outcome(NEAR_NEGATION), (0, []))
    check("negation/window-far-advisory", _outcome(FAR_NEGATION), (0, [("R1", "ADVISORY")]))
    check("negation/then-advisory", _outcome(THEN_NEGATION), (0, [("R1", "ADVISORY")]))
    check("negation/subordinate-comma-advisory", _outcome(SUBORDINATE), (0, [("R1", "ADVISORY")]))
    check("r5/exit-code-advisory", _outcome(RESULT), (0, [("R5", "ADVISORY")]))
    check("r5/negated-ok", _outcome(RESULT_NEGATED), (0, []))
    check("r5/static-source-ok", _outcome(STATIC_RESULT), (0, []))
    check("r6/named-tool-advisory", _outcome(OUTCOME_FLAG), (1, [("R6", "ADVISORY"), ("S3", "HARD")]))
    check("r6/unnamed-advisory", _outcome(OUTCOME_UNNAMED), (0, [("R6", "ADVISORY")]))
    check("r6/relative-clause-ok", _outcome(RELATIVE), (0, []))
    check("r7/commit-parents-advisory", _outcome(GIT_PARENTS), (0, [("R7", "ADVISORY")]))
    check("r7/commit-sha-advisory", _outcome("What is the SHA of the commit?\n"), (0, [("R7", "ADVISORY")]))
    check("r7/which-commit-advisory", _outcome("1. Which commit added the helper?\n"), (0, [("R7", "ADVISORY")]))
    check("r7/marker-exempts-nothing",
          _outcome("1. Which commit added the helper? This item is orchestrator-answered.\n"),
          (0, [("R7", "ADVISORY")]))
    check("r7/code-span-ok", _outcome("Quote `which commit added` from the docstring.\n"), (0, []))
    check("r7/diff-of-strings-ok", _outcome("Quote the line that formats the diff between two strings.\n"), (0, []))
    check("r7/statement-ok", _outcome(GIT_STATEMENT), (0, []))
    check("r7/executing-family-ok", (_outcome(GIT_PARENTS, "alpha"), _outcome(RUN_ITEM, "beta")),
          ((0, []), (0, [])))
    # ---------- scope ----------
    check("scope/answer-only-ok", _outcome(SCOPED), (0, []))
    check("scope/answer-only-unscoped-refused", _outcome(SCOPED, scoped=False),
          (1, [("R1", "ADVISORY"), ("R5", "ADVISORY"), ("S2", "HARD")]))
    check("scope/executing-families-ok", (_outcome(SCOPED, "alpha", False), _outcome(SCOPED, "beta", False)),
          ((0, []), (0, [])))
    check("scope/answer-only-items-ok", _outcome(ANSWER_ONLY), (0, []))
    check("scope/do-not-attempt-ok", _outcome(NOT_ATTEMPT), (0, []))
    check("scope/other-leg-dropped", _outcome(OTHER_LEG), (0, []))
    check("scope/other-leg-items-dropped", _outcome(OTHER_LEG_ITEMS), (0, []))
    check("scope/other-leg-fence-dropped", _outcome(OTHER_LEG_FENCE), (0, []))
    check("scope/other-leg-git-dropped", _outcome(OTHER_LEG_GIT), (0, []))
    check("scope/other-leg-section-dropped", _outcome(OTHER_SECTION), (0, []))
    check("scope/section-ends-refused", _outcome(SECTION_ENDS), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/tail-marker-dropped", _outcome(TAIL_MARKER), (0, []))
    check("scope/tail-marker-in-code-refused", _outcome(TAIL_IN_CODE), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("scope/tail-marker-mid-line-refused", _outcome(TAIL_MID_LINE), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("scope/tail-marker-in-wrapped-code-refused", _outcome(TAIL_IN_WRAPPED_CODE),
          (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("scope/code-span-directive-refused", _outcome(CODE_SPAN_DIRECTIVE), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("scope/own-leg-kept", _outcome("Gamma leg: run `t.py --self-test`.\n"), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/code-span-marker-refused", _outcome(CODE_SPAN_MARKER), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/mid-line-marker-refused", _outcome(MID_LINE_MARKER), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/quoted-directive-refused", _outcome(QUOTED_DIRECTIVE), (1, [("R1", "ADVISORY"), ("S2", "HARD")]))
    check("scope/address-ends-claim-refused", _outcome(ADDRESS_ENDS), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/all-legs-ends-claim-refused", _outcome(ALL_LEGS_ENDS), (1, [("R1", "ADVISORY"), ("S3", "HARD")]))
    check("scope/reversed-range-2", _outcome(REVERSED), "CannotEvaluate")
    check("scope/item-list-parsed", (parse_item_list("1, 2 and 4"), parse_item_list("Q1 to Q3"),
                                     parse_item_list("2-3, 5")), (set((1, 2, 4)), set((1, 2, 3)), set((2, 3, 5))))
    # ---------- cannot evaluate ----------
    check("cannot/undecodable-2", _outcome(b"Run \xff the parser.\n"), "CannotEvaluate")
    check("cannot/unknown-family-2", _outcome(RUN_ITEM, "delta"), "CannotEvaluate")
    check("cannot/blank-2", _outcome(" \n\n"), "CannotEvaluate")
    check("cannot/nul-blank-2", _outcome(b"\x00\x00\n"), "CannotEvaluate")
    check("cannot/zero-width-blank-2", _outcome(chr(0x200B) + chr(0xFEFF) + "\n"), "CannotEvaluate")
    tmp = Path(tempfile.mkdtemp(prefix="aiqt-brief-capability-selftest-"))
    try:
        table_path = str(tmp / "table.toml")
        for name, body in (("table.toml", TABLE_FIXTURE), ("refuse.md", RUN_ITEM), ("ok.md", NEGATED),
                           ("large.md", "a" * (MAX_BYTES + 1))):
            with open(tmp / name, "w", encoding="utf-8") as handle:
                handle.write(body)
        tail = ["--family", "gamma", "--table", table_path]
        check("main/refuse-exit-1", _quiet(main, [str(tmp / "refuse.md")] + tail), 1)
        check("main/ok-exit-0", _quiet(main, [str(tmp / "ok.md")] + tail), 0)
        check("main/missing-brief-2", _quiet(main, [str(tmp / "absent.md")] + tail), 2)
        check("main/directory-brief-2", _quiet(main, [str(tmp)] + tail), 2)
        try:
            _read_regular(str(tmp), "brief")
            refused = "read"
        except CannotEvaluate as exc:
            refused = "not a regular file" in str(exc)
        check("main/directory-refused-by-type", refused, True)
        check("main/oversized-brief-2", _quiet(main, [str(tmp / "large.md")] + tail), 2)
        check("main/missing-table-2", _quiet(main, [str(tmp / "refuse.md"), "--family", "gamma", "--table",
                                                    str(tmp / "absent.toml")]), 2)
        check("main/no-family-2", _quiet(main, [str(tmp / "refuse.md")]), 2)
        check("main/unknown-flag-2", _quiet(main, [str(tmp / "refuse.md"), "--force"] + tail), 2)
        check("main/repeated-table-2", _quiet(main, [str(tmp / "ok.md"), "--table", table_path] + tail), 2)
        check("main/bare-shipped-table-0", _quiet(main, []), 0)
        os.makedirs(tmp / ".aiqt" / "core")
        with open(tmp / TABLE_REL, "w", encoding="utf-8") as handle:
            handle.write(TABLE_FIXTURE.replace('execute = "no"', 'execute = "sometimes"'))
        with _patched(ROOT=tmp):
            check("main/bare-malformed-table-2", _quiet(main, []), 2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # ---------- red on revert: each blocking rule, form and exemption patched out flips its fixture ----------
    nothing = lambda *args: []  # noqa: E731
    never = re.compile(r"(?!)")
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

    def without_form(index):
        """COMMAND_RE with one command-line form removed."""
        return re.compile("|".join(form for number, form in enumerate(COMMAND_FORMS) if number != index))

    def code(text):
        return lambda: _outcome(text)[0]

    check("revert/s1-red", *flip([code(BASH_FENCE), code(UNTAGGED_FENCE)], [0, 0], rule_s1=lambda block: False))
    check("revert/s1-exec-tags-red", *flip([code(BASH_FENCE), code(PYCON_FENCE)], [0, 0],
                                          EXEC_FENCE_TAGS=frozenset()))
    check("revert/s1-python-prompt-red", *flip([code(PYTHON_PROMPT_FENCE)], [0], RUN_PROMPT_RE=never))
    check("revert/s1-first-line-red", *flip([code(UNTAGGED_FENCE), code(PLAIN_FENCE)], [0, 0],
                                           SHELL_FIRST_LINE_RE=never))
    check("revert/s1-reference-intro-red", *flip([code(REFERENCE_FENCE)], [1], reference_intro=lambda block: False))
    check("revert/s1-reference-except-red", *flip([code(REFERENCE_UNTIL), code(IGNORE_INSTRUCTION)], [0, 0],
                                                 EXCEPTION_RE=never, REVERSAL_RE=never))
    check("revert/s2-red", *flip([code(RUN_ITEM), code(GIT_ITEM)], [0, 0],
                                command_rule=lambda block: None if block.item is not None else "S3"))
    check("revert/s3-red", *flip([code(GIT_SHOW_PREAMBLE), code(GIT_SHOW_PROSE)], [0, 0],
                                command_rule=lambda block: "S2" if block.item is not None else None))
    check("revert/command-lines-red", *flip([code(GIT_SHOW_PREAMBLE), code(RUN_ITEM)], [0, 0], rule_commands=nothing))
    check("revert/form-git-red", *flip([code(GIT_SHOW_PREAMBLE), code(GIT_ITEM)], [0, 0], GIT_SUBCOMMAND_RE=never))
    check("revert/form-python-red", *flip([code(PYTHON_COMMAND)], [0], PYTHON_TARGET_RE=never))
    check("revert/python-target-c-m-red", *flip([code(PYTHON_C), code(PYTHON_M), code(PYTHON_OPTIONS_M)], [0, 0, 0],
                                               PYTHON_TARGET_RE=re.compile(r"\s+[\w./-]+\.py\b")))
    # the round-4 option loop, which consumed -c and -m as options
    check("revert/python-option-loop-red", *flip([code(PYTHON_C), code(PYTHON_M), code(PYTHON_OPTIONS_M)],
                                                [0, 0, 0], PYTHON_OPTION_RE=re.compile(r"\s+-[A-Za-z]+")))
    check("revert/form-path-flag-red", *flip([code(RUN_ITEM)], [0], COMMAND_RE=without_form(0)))
    check("revert/form-dot-slash-red", *flip([code(DOT_SLASH)], [0], COMMAND_RE=without_form(1)))
    check("revert/form-shell-c-red", *flip([code(BASH_C)], [0], COMMAND_RE=without_form(2)))
    check("revert/form-pytest-red", *flip([code(PYTEST)], [0], COMMAND_RE=without_form(3)))
    check("revert/form-span-tool-red", *flip([code(SPAN_TOOL)], [0], SPAN_COMMAND_RE=never))
    check("revert/git-config-noun-red", *flip([code(CONFIG_NOUN)], [1], GIT_SUBCOMMAND_RE=re.compile(
        GIT_SUBCOMMAND_RE.pattern.replace(GIT_CONFIG, "config"))))
    check("revert/git-options-unbounded-red", *flip([lambda: bool(option_commands(GIT_OPTIONS))], [False],
                                                   _option_end=_bounded_option_end))
    check("revert/quoted-source-red", *flip([code(QUOTE_SELF_TEST), code(DESCRIBED_PROSE), code(QUOTED_ENTRY),
                                            code(QUOTED_ROW), code(QUOTED_EXAMPLE), code(QUOTED_README)],
                                           [1, 1, 1, 1, 1, 1], QUOTE_VERB_RE=never))
    check("revert/quote-veto-red", *flip([code(QUOTE_PRINTS), code(QUOTE_AND_OUTPUT)], [0, 0], QUOTE_VETO_RE=never))
    check("revert/source-of-red", *flip([code(QUOTE_LINE_OF)], [0], SOURCE_OF_RE=never))
    check("revert/source-noun-red", *flip([code(QUOTE_NO_NOUN)], [0], SOURCE_NOUN_RE=re.compile(r"\b\w")))
    check("revert/quote-stop-red", *flip([code(QUOTE_AND_COMMAND)], [0], OBJECT_CONTINUES_RE=re.compile("")))
    check("revert/past-report-red", *flip([code(MENTION)], [1], PAST_SUBJECT_RE=never))
    check("revert/prohibited-red", *flip([code(PROHIBITION)], [1], PROHIBITED_RE=never))
    check("revert/prohibition-except-red", *flip([code(PROHIBITION_EXCEPT), code(ONLY_BUT_ALSO)], [0, 0],
                                                EXCEPTION_RE=never))
    check("revert/exec-count-red", *flip([code(RUN_ON_BRANCH), code(TWO_VERBS)], [0, 0], EXEC_WORD_RE=re.compile(r"\A")))
    check("revert/demotion-cancel-red", *flip([code(QUOTE_THEN_RUN), code(INTRO_AFTER_BUILD)], [0, 0],
                                             asks_to_run=lambda findings: False))
    check("revert/word-run-anchor-red", *flip(
        [lambda: bool(WORD_RUN_RE.search("aaaa, b", 1))], [True],
        WORD_RUN_RE=re.compile(NOUN_WORD + r"(?:\s*,\s*" + NOUN_WORD + r")++")))
    check("revert/list-structure-red", *flip([lambda: _outcome("- Read the diff\n- Run the parser\n")], [(0, [])],
                                            LIST_LINE_RE=never))
    check("revert/format-characters-red", *flip([code(BOM_BRIEF), code(ZERO_WIDTH_SPLIT)], [0, 0],
                                               unicodedata=_NoFormatRemoval))
    check("revert/capability-gate-red", *flip([lambda: _outcome(RUN_ITEM, "alpha")[0],
                                              lambda: _outcome(RUN_ITEM, "beta")[0]], [1, 1],
                                             can_execute=lambda row: False))
    check("revert/scope-directives-red", *flip([code(SCOPED), code(NOT_ATTEMPT)], [1, 1],
                                              scope_directives=lambda text: (None, set())))
    check("revert/scope-directive-position-red", *flip([code(QUOTED_DIRECTIVE)], [0],
                                                      scope_directives=_legacy_scope_directives))
    check("revert/scope-legs-red", *flip([code(OTHER_LEG), code(OTHER_LEG_ITEMS), code(OTHER_SECTION),
                                         code(OTHER_LEG_GIT)], [1, 1, 1, 1], leg_spans=nothing))
    check("revert/scope-operative-marker-red", *flip([code(CODE_SPAN_MARKER), code(MID_LINE_MARKER)], [0, 0],
                                                    leg_spans=_legacy_leg_spans))
    check("revert/scope-address-red", *flip([code(ADDRESS_ENDS), code(ALL_LEGS_ENDS)], [0, 0], _addresses=nothing))
    check("revert/scope-reversed-range-red", *flip([lambda: _outcome(REVERSED)], [(0, [])],
                                                  parse_item_list=lambda spec: set()))
    check("revert/scope-tail-marker-red", *flip([code(TAIL_MARKER)], [1], tail_marker_family=lambda *args: None))
    check("revert/scope-red", *flip([code(SCOPED)], [1], apply_scope=lambda blocks, *args: blocks))
    widened = dict(TABLE_VOCABULARY)
    widened["execute"] = TABLE_VOCABULARY["execute"] + ("sometimes",)
    sometimes = TABLE_FIXTURE.replace('execute = "no"', 'execute = "sometimes"')
    check("revert/table-vocabulary-red", *flip(
        [lambda: _table_outcome(sometimes)], [["alpha", "beta", "gamma"]], TABLE_VOCABULARY=widened))
    # the advisory rules print what they find; each patched out drops its line
    check("revert/r1-red", *flip([lambda: _outcome(RUN_PROSE)], [(0, [])], rule_r1=nothing))
    check("revert/r1-negation-red", *flip([lambda: _outcome(NEGATED), lambda: _outcome(RESULT_NEGATED)],
                                         [(0, [("R1", "ADVISORY")]), (0, [("R5", "ADVISORY")])], NEGATION_RE=never))
    check("revert/negation-hard-break-red", *flip([lambda: _outcome(THEN_NEGATION), lambda: _outcome(SUBORDINATE)],
                                                 [(0, []), (0, [])], HARD_BREAK_RE=never, SUBORDINATE_RE=never))
    check("revert/r1-clause-start-red", *flip([lambda: _outcome(CLAUSE_MID)], [(0, [("R1", "ADVISORY")])],
                                             Sentence=_EveryWordSentence))
    check("revert/r1-heading-red", *flip([lambda: _outcome(HEADING_NOUN)], [(0, [("R1", "ADVISORY")])],
                                        r1_applies=lambda block: True))
    check("revert/noun-parenthesis-red", *flip([lambda: _outcome(PAREN_NOUN), lambda: _outcome(ADJECTIVE_NOUN)],
                                              [(0, [("R1", "ADVISORY")]), (0, [("R1", "ADVISORY")])],
                                              NOUN_PARENTHESIS_RE=never))
    check("revert/noun-list-red", *flip([lambda: _outcome(OR_LIST), lambda: _outcome(PUNCTUATED_LIST)],
                                       [(0, [("R1", "ADVISORY")]), (0, [("R1", "ADVISORY")])],
                                       NOUN_LIST_TAIL_RE=never, LED_LIST_TAIL_RE=never))
    check("revert/r1-code-span-red", *flip([lambda: _outcome("See the `build and run` target.\n")],
                                          [(0, [("R1", "ADVISORY")])], code_spans=nothing))
    check("revert/r5-red", *flip([lambda: _outcome(RESULT)], [(0, [])], rule_r5=nothing))
    check("revert/r5-static-source-red", *flip([lambda: _outcome(STATIC_RESULT)], [(0, [("R5", "ADVISORY")])],
                                              STATIC_SOURCE_RE=never))
    check("revert/r6-red", *flip([lambda: _outcome(OUTCOME_UNNAMED)], [(0, [])], rule_r6=nothing))
    check("revert/r6-relative-red", *flip([lambda: _outcome(RELATIVE)], [(0, [("R6", "ADVISORY")])],
                                         R6_RELATIVE_RE=never))
    check("revert/r7-red", *flip([lambda: _outcome(GIT_PARENTS)], [(0, [])], rule_r7=nothing))
    check("revert/r7-request-red", *flip([lambda: _outcome(GIT_STATEMENT)], [(0, [("R7", "ADVISORY")])],
                                        _asks=lambda sentence: True))
    check("revert/every-flip-ran", len(flipped), 57)

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
          "malformed table exits 2; S1, S2 and S3 refuse executable fences and command lines in numbered "
          "items and outside them, a command line held as quoted source text, prohibited or past-reported is "
          "INFO unless a reversal, an exception or a run request voids it, and every blocking rule, "
          "command-line form and demotion patched out flips its fixture; R1, R5, R6 and R7 print ADVISORY "
          "lines and never refuse; 16 repeated shapes each evaluate in linear time at 1 MiB in a capped child "
          "that must exit 0; scope keeps and drops numbered items, other families' legs, sections and "
          "tail markers; undecodable, blank, oversized, missing, non-regular, backwards-range and "
          "unknown-family input exits 2); execution set reconciled against tools/selftest_checks.toml".format(
              len(EXECUTED)))
    return 0


if __name__ == "__main__":
    _selftest_exit_report.exit_with(main(sys.argv[1:]))
