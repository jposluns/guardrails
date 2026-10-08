#!/usr/bin/env python3
"""Brief-claims lint: a dispatch brief's generated claims block must match each named review file's trailer.
Offline, stdlib only, fail-closed.

A dispatch brief often restates a named review file's verdict. When that restatement is carried over from
text about ANOTHER file (the template, the previous round, the other reviewer), the brief asserts what a
file says without having read it (read-before-characterizing, rdbchr) and hands a worker an unsound input
(guard-input-soundness, grdinp). This lint checks ONE structured input: a claims block that the lint's own
--emit mode generates from the review files. Free prose is never a blocking input; it is reported only.

THE ONLY BLOCKING INPUT: THE CLAIMS BLOCK. A brief carries at most one block, three kinds of line, each at
column 0, printable ASCII, matched as the WHOLE line (a final carriage return is dropped first):

  BEGIN BRIEF-CLAIMS 1
  <name> VERDICT <token>          one line per named review file
  END BRIEF-CLAIMS

<name> is a file basename (no slash or space) that full-matches the configured review-file-pattern and
resolves to a readable UTF-8 regular file inside --input-dir (a similarly named sibling is never
substituted); <token> is one configured verdict token. Fences, quotes and markdown are NOT parsed for the
block: a marker line anywhere in the brief counts. Cannot-evaluate (exit 2): a second block, an unclosed
block, an END with no BEGIN, an empty block, any other line inside the block (a blank line, an extra word,
a colon, trailing space, a lookalike or invisible character), a repeated name, and a line outside a block
that reads as a marker after normalization and punctuation removal but is not one (an indented, BOM-
prefixed, lower-case or wrong-version marker).

THE SOURCE VERDICT: THE TRAILER RECORD. A review file's verdict is read from its trailer only, and its body
is never read. Reading upward from the last line, blank lines (empty or spaces and tabs only) and trailer
lines (a line starting at column 0 with a configured trailer prefix) are skipped; the first other line
must be exactly `VERDICT: <token>` for one configured token, else the file is cannot-evaluate (no trailer,
prose or a closing fence after the record, a decorated, indented, BOM-prefixed, differently cased or
trailing-space record, or a value that is not exactly one token). Fences are not parsed here either.

COMPLETENESS. Every token in the brief's own text (outside fences, `>` lines, declared verbatim sections and
the block) whose basename full-matches the review-file-pattern needs a block line with that basename, else
the brief is a MISMATCH (exit 1, UNCLAIMED). This scan is a trigger only: a name it does not recognize
loses the completeness check, but it can never make a wrong block line pass.

ADVISORY PROSE (never blocking). The free-prose rules of the third QA round run on the brief's own text
and report every result as an ADVISORY line: verdict, id, grade and count restatements bound to named
files, verbatim excerpts, embedded verbatim sections, template carry-over (--template), and foreign item
ids (--registry). An advisory MISMATCH or CANNOT-EVALUATE, an unreadable file named only in prose, an
unreadable template or registry, and an adopter id-pattern over its bound never change the exit code.
Their recall and true-negative rate are not measured.

CONFIGURATION (`.aiqt/brief-claims.toml` at this file's repository root), every key REQUIRED:

  format-version = 1                  the exact integer 1 (true and 1.0 do not pass)
  families = ["alpha", "beta"]        reviewer family words (unique, case-insensitive; advisory binding)
  review-file-pattern = '...'         a regex (at most 512 characters) FULL-matched against a basename, with
                                      the named groups item, round (digits) and family
  verdicts = ["NO BLOCKERS", ...]     the exact verdict tokens: printable ASCII words joined by single spaces
  trailer-prefixes = ["WORKER_STATUS:"]  column-0 prefixes of the lines allowed after the VERDICT record (may
                                      be empty; printable ASCII; none may start with VERDICT or be a prefix
                                      of "VERDICT:")
  grades = ["BLOCKER", "MAJOR", ...]  finding grade words (advisory)
  grade-labels = ["Grade"]            label words of the "Label: GRADE" finding form (advisory)
  verbatim-begin = "BEGIN VERBATIM"   opens an embedded verbatim review; the first family word after it is
                                      the section's declared family (advisory; excluded from own text)
  verbatim-end = "END VERBATIM"       closes it
  regrade-token = "REGRADED"          an advisory prose segment carrying it is reported EXEMPT
  [aliases.<family>]                  per-family shorthand (M = "MAJOR"); advisory id claims only

With NO configuration the check prints NOT APPLICABLE and exits 0 (exit 2 under --require-config); a
present but unreadable, symlinked or malformed configuration is cannot-evaluate (exit 2).

MACHINE CONTRACT (for the dispatch-time hook; stable for format-version 1 and block version 1).

  check_brief_claims.py [--require-config] --input-dir DIR [--template PATH] [--registry PATH] BRIEF...
      Check each BRIEF. DIR holds the review files the block names. Exit codes:
        0  every block line equals its file's trailer verdict and every recognized named review file has a
           block line (or no review file is named and no block is present); NOT APPLICABLE with no
           configuration and no --require-config
        1  a block line's token differs from its file's trailer verdict, or a recognized named review file
           has no block line, and nothing is cannot-evaluate
        2  cannot evaluate: a block-named review file that is missing, unreadable, not UTF-8, a directory or
           outside DIR; a missing or malformed trailer; a malformed block; an unreadable brief; an unclosed
           fence or verbatim section in the brief; a malformed configuration; a missing DIR; an adopter
           review-file-pattern over its bound; a usage error; no configuration under --require-config
      With several briefs the exit code is the highest. A hook dispatches ONLY on exit 0 and refuses on every
      other status, including one this list does not name (an uncaught interpreter error exits 1; a signal
      death is negative). Output on stdout, one result per line, each starting with a fixed word:
        BRIEF <path> sha256=<hex>
        MATCH <name> VERDICT <token>: <path> sha256=<hex>
        MISMATCH <name> VERDICT <token>: <path> sha256=<hex>: the trailer VERDICT is <token>
        CANNOT-EVALUATE <subject>: <reason>
        UNCLAIMED <name>: brief line <n> names it and the claims block has no line for it
        ADVISORY <MATCH|WARN|CANNOT-EVALUATE|EXEMPT> <detail>   (never affects the exit code)
        brief-claims: <path>: <counts>; exit <0|1|2>             (the last line for each brief)
      A run-level failure (configuration, usage, DIR) prints one CANNOT-EVALUATE line instead.

  check_brief_claims.py --emit --input-dir DIR NAME...
      Print the claims block for the review files NAME... (basenames inside DIR, in the order given) on
      stdout, read from each file's trailer, and exit 0. On any failure (no configuration, a repeated name,
      a name with a slash or one that does not full-match the pattern, an unreadable file, a missing or
      malformed trailer, a usage error) print NOTHING on stdout, one CANNOT-EVALUATE line per problem on
      stderr, and exit 2. A pasted --emit block passes the check while the files' trailers are unchanged.

  check_brief_claims.py                 validate the configuration ONLY (the shipped CI form): it examines
                                        NO brief
  check_brief_claims.py --self-test     deterministic self-test over synthetic fixtures

WHAT IT DOES NOT PROVE (class c, partial). NOTHING BLOCKS UNLESS THIS RUNS AT DISPATCH: the shipped CI step
examines no brief, and the dispatch-time hook that runs the check form on every brief is built and shipped
outside this repository. Prose restatements, finding ids, grades, counts, template carry-over, embedded
sections and foreign ids are advisory only, and the advisory rules miss shapes (slash-joined tokens such
as "NO BLOCKERS/BLOCKERS FOUND respectively" read as an option list). The block compares verdict tokens only: it does not prove that
the brief's prose agrees with the block, that a file's body agrees with its trailer, or that a file did not
change after the check while keeping its verdict. A review file named in a form the completeness scan does
not recognize (a name split across lines, a path whose basename does not full-match the pattern, a name
inside a fence or quote) needs no block line. A prose path naming a same-named file in another directory
is covered by the block line for that basename, which reads the file inside DIR.

BOUNDS. The two adopter regexes (review-file-pattern, id-pattern) are capped at 512 characters and are
matched only in a child interpreter (python -I -B) under a CPU-time limit (RLIMIT_CPU, 10 seconds, the
first bound a CPU-bound regex reaches) and a 20-second wall-clock deadline (which bounds a child that is
not using CPU); on Linux the child dies with its parent (PR_SET_PDEATHSIG), and its address space is capped
at 1 GiB where the platform allows. An overrun, a failed child, a child that writes anything to stderr, and
a platform with no RLIMIT_CPU are cannot-evaluate: exit 2 for the review-file-pattern, an advisory line for
the id-pattern.
"""
import sys

if tuple(sys.version_info[:2]) < (3, 14):
    sys.stderr.write(
        "error: check_brief_claims.py requires Python 3.14 or newer; this is Python %d.%d.%d (%s). "
        "Nothing was run (cannot evaluate).\n"
        % (tuple(sys.version_info[:3]) + (sys.executable or "unknown interpreter",)))
    raise SystemExit(2)

import functools
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import unicodedata
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # not a version problem: every Python 3.14 ships tomllib
    sys.stderr.write(
        "error: check_brief_claims.py cannot import tomllib, part of the Python standard library; "
        "this installation is incomplete. Nothing was run (cannot evaluate).\n")
    raise SystemExit(2)

CONFIG_REL = ".aiqt/brief-claims.toml"
CONFIG_KEYS = frozenset(("format-version", "families", "review-file-pattern", "verdicts", "trailer-prefixes",
                         "grades", "grade-labels", "verbatim-begin", "verbatim-end", "regrade-token", "aliases"))
REGISTRY_KEYS = frozenset(("format-version", "target", "aliases", "dependencies", "id-pattern"))
BLOCK_BEGIN = "BEGIN BRIEF-CLAIMS 1"
BLOCK_END = "END BRIEF-CLAIMS"
MARKER_KEYS = ("beginbriefclaims", "endbriefclaims")  # a line reading as one of these is a marker or a near miss
NAME_RE = re.compile(r"[!-.0-~]+")  # a block name: printable ASCII, no space, no slash
VERDICT_TOKEN_RE = re.compile(r"[!-~]+(?: [!-~]+)*")
TRAILER_PREFIX_RE = re.compile(r"[!-~][ -~]*")
WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
ALIAS_KEY_RE = re.compile(r"^[A-Z]{1,2}$")
TOKEN_RE = re.compile(r"[^\s()\[\]{}<>,;\"'`|*"
                      "\u2018\u2019\u201a\u201b\u201c\u201d\u201e\u201f\u00ab\u00bb\u2039\u203a]+")
FROM_ROUND_RE = re.compile(r"\bfrom\s+round\s+(\d+)\b", re.I)
TITLE_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
ROUND_RE = re.compile(r"\bround\s+(\d+)\b", re.I)
SIGNS = "-+\u2212\u00b1"
NUMBER = r"[-+\u2212\u00b1]?\d(?:[\d.,]*\d)?"  # every number-like token; _number decides if it is supported
NOT_AFTER = r"(?<![\w,.\-+\u2212\u00b1])"
PRED_RE = re.compile(NOT_AFTER + "(" + NUMBER + r")\s+([A-Za-z][A-Za-z-]*)")
PRED_OF_RE = re.compile(NOT_AFTER + "(" + NUMBER + r")\s+of\s+(" + NUMBER + r")\s+([A-Za-z][A-Za-z-]*)", re.I)
SUPPORTED_NUMBER_RE = re.compile(r"([-+\u2212]?)([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)")
SRC_WORD_RE = re.compile(NUMBER + r"|[A-Za-z][A-Za-z-]*")
CLAUSE_RE = re.compile(r"[,;:.!?](?=\s|$)|\n")
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
VERDICT_WORD_RE = re.compile(r"(?<![A-Za-z])verdicts?(?![A-Za-z])", re.I)
OPTION_GAP_RE = re.compile(r"[\s\"'*_:,]*(?:or|/)[\s\"'*_:,]*(?:verdict[\s*_]*:[\s\"'*_]*)?", re.I)
# Default_Ignorable_Code_Point (Unicode DerivedCoreProperties): removed before any advisory comparison.
IGNORABLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
             (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
             (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
             (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
# Characters that render as a blank (the Hangul fillers, the blank Braille pattern) read as a space.
BLANKS = frozenset((0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800))
LONG_LINE = 40       # an embedded line this long (stripped) votes on the section's source
MIN_EXCERPT = 24     # a claim segment this long may pass as a verbatim excerpt of its bound file
MAX_RANGE = 50       # a wider id range is cannot-evaluate, never expanded
MAX_PATTERN = 512    # an adopter regex longer than this is a malformed configuration or registry
MAX_ROUND_DIGITS = 9  # a longer round, finding number or grade count is not parsed (cannot-evaluate)
MAX_NUMBER_DIGITS = 18  # a predicate count with more digits is outside the supported number grammar
REGEX_DEADLINE = 20.0  # wall-clock seconds for one adopter-regex run in the child interpreter
CHILD_CPU = 10  # the child's CPU-time limit in seconds (RLIMIT_CPU): below the deadline, so a CPU-bound regex
#                meets it first; the deadline bounds a child that is not using CPU
CHILD_MEMORY = 1 << 30  # the child's address-space cap, where the platform allows one
MATCH, MISMATCH, CANNOT, WARN, EXEMPT, UNCLAIMED = (
    "MATCH", "MISMATCH", "CANNOT-EVALUATE", "WARN", "EXEMPT", "UNCLAIMED")


class GateError(Exception):
    """An input the lint cannot read, parse or resolve: reported as exit 2 (fail-closed)."""


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def read_text(path, what):
    """Read a UTF-8 regular file fail-closed: missing, a directory, unreadable or non-UTF-8 is a GateError."""
    try:
        st = os.stat(path)
    except OSError as exc:
        raise GateError("{} {} cannot be read ({})".format(what, path, exc.strerror or exc))
    if not stat.S_ISREG(st.st_mode):
        raise GateError("{} {} is not a regular file".format(what, path))
    try:
        data = _read_bytes(path)
    except OSError as exc:
        raise GateError("{} {} cannot be read ({})".format(what, path, exc.strerror or exc))
    try:
        return data.decode("utf-8"), hashlib.sha256(data).hexdigest()
    except UnicodeDecodeError:
        raise GateError("{} {} is not valid UTF-8".format(what, path))


def _load_toml(path, what):
    text, _ = read_text(path, what)
    try:
        return tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError, RecursionError) as exc:
        raise GateError("{} {} is malformed TOML ({})".format(what, path, exc))


def _keys(data, wanted, what):
    missing = sorted(wanted - set(data))
    extra = sorted(set(data) - wanted)
    if missing or extra:
        raise GateError("{}: missing key(s) {} / unknown key(s) {}".format(what, missing, extra))
    version = data["format-version"]
    if type(version) is not int or version != 1:
        raise GateError("{}: format-version must be the integer 1, got {!r}".format(what, version))


def _str(data, key, what):
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise GateError("{}: {!r} must be a non-empty string".format(what, key))
    return value.strip()


def _str_list(data, key, what, allow_empty=False, shape=None):
    value = data[key]
    if not isinstance(value, list) or (not value and not allow_empty) or not all(
            isinstance(x, str) and x.strip() for x in value):
        raise GateError("{}: {!r} must be a {}list of non-empty strings".format(
            what, key, "" if allow_empty else "non-empty "))
    items = [x.strip() for x in value]
    folded = [x.casefold() for x in items]
    if len(set(folded)) != len(folded):
        raise GateError("{}: {!r} has a duplicate entry".format(what, key))
    if shape is not None and not all(shape.match(x) for x in items):
        raise GateError("{}: {!r} has an entry outside the shape {}".format(what, key, shape.pattern))
    return items


def _alt(words):
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def _adopter_regex(source, key, what, before="", after=""):
    """Compile an adopter regex of bounded length (wrapped in before and after); it is MATCHED only in the
    child interpreter (bounded_regex)."""
    if len(source) > MAX_PATTERN:
        raise GateError("{}: {} is longer than {} characters".format(what, key, MAX_PATTERN))
    try:
        return re.compile(before + source + after)
    except (re.error, RecursionError, OverflowError) as exc:
        raise GateError("{}: {} does not compile ({})".format(what, key, exc))


def load_config(path):
    """Parse and validate the adopter configuration; every defect is a GateError (exit 2)."""
    what = "configuration {}".format(CONFIG_REL)
    data = _load_toml(path, "configuration")
    _keys(data, CONFIG_KEYS, what)
    families = [f.casefold() for f in _str_list(data, "families", what, shape=WORD_RE)]
    pattern = _adopter_regex(_str(data, "review-file-pattern", what), "review-file-pattern", what)
    if not {"item", "round", "family"} <= set(pattern.groupindex):
        raise GateError("{}: review-file-pattern must name the groups item, round and family".format(what))
    verdicts = _str_list(data, "verdicts", what)
    if not all(VERDICT_TOKEN_RE.fullmatch(v) for v in data["verdicts"]):
        raise GateError("{}: every verdict token must be printable ASCII words joined by single spaces".format(
            what))
    trailers = _str_list(data, "trailer-prefixes", what, allow_empty=True)
    for prefix in trailers:
        if not TRAILER_PREFIX_RE.fullmatch(prefix) or prefix.startswith("VERDICT") or "VERDICT:".startswith(prefix):
            raise GateError("{}: trailer prefix {!r} is not printable ASCII or would admit a VERDICT line as a "
                            "trailer line".format(what, prefix))
    grades = [g.upper() for g in _str_list(data, "grades", what, shape=re.compile(r"^[A-Za-z]+$"))]
    labels = _str_list(data, "grade-labels", what, shape=re.compile(r"^[A-Za-z][A-Za-z ]*$"))
    aliases = data["aliases"]
    if not isinstance(aliases, dict):
        raise GateError("{}: aliases must be a table".format(what))
    alias_map = {}
    for fam, table in aliases.items():
        if fam.casefold() not in families or not isinstance(table, dict):
            raise GateError("{}: aliases.{} names no declared family or is not a table".format(what, fam))
        for key, grade in table.items():
            if not ALIAS_KEY_RE.match(key) or not isinstance(grade, str) or grade.upper() not in grades:
                raise GateError("{}: alias {}.{} must map one or two capitals to a declared grade".format(
                    what, fam, key))
        alias_map[fam.casefold()] = {k: v.upper() for k, v in table.items()}
    g = _alt(grades)
    return {
        "families": families,
        "pattern": pattern,
        "verdicts": verdicts,
        "trailers": trailers,
        "verdict_res": [(t, re.compile(r"(?<![\w-])" + r"\s+".join(re.escape(w) for w in t.split())
                                       + r"(?![\w-])")) for t in verdicts],
        "grades": grades,
        "aliases": alias_map,
        "begin": _str(data, "verbatim-begin", what),
        "end": _str(data, "verbatim-end", what),
        "regrade": _str(data, "regrade-token", what),
        "family_re": re.compile(r"(?<![\w./-])(" + _alt(families) + r")(?![\w./-])", re.I),
        "numbered_re": re.compile(r"^\s*(\d+)[.)]\s+[*_\[]*\s*(" + g + r")\b", re.I),
        "heading_re": re.compile(r"^\s{0,3}#{1,6}\s+[*_]*(" + g + r")\b[*_]*[\s:-]*(?:n?(\d+)(?![\d.]))?",
                                 re.I),
        "label_re": re.compile(r"(?<!\w)(?:" + _alt(labels) + r")\s*:\s*[*_]*(" + g + r")\b", re.I),
        "count_re": re.compile(r"(?<![\w,.-])(\d+)\s+(" + g + r")(?:es|s)?(?![\w-])", re.I),
        "range_re": re.compile(r"(?<![\w.-])(" + g + r")[- ]?(\d+)\s*(?:-|to|through)\s*(?:(" + g
                               + r")[- ]?)?(\d+)(?![\w-]|\.\d)", re.I),
        "id_re": re.compile(r"(?<![\w.-])(" + g + r")[- ]?(\d+)(?![\w-]|\.\d)", re.I),
        "alias_re": re.compile(r"(?<![\w.-])([A-Z]{1,2})(\d+)(?:\s*(?:-|to)\s*\1(\d+))?(?![\w-]|\.\d)"),
        "colon_re": re.compile(r"(?<![\w-])(" + g + r"):", re.I),
    }


def load_registry(path):
    what = "registry {}".format(path)
    data = _load_toml(path, "registry")
    _keys(data, REGISTRY_KEYS, what)
    id_re = _adopter_regex(_str(data, "id-pattern", what), "id-pattern", what, r"(?<![\w-])(?:", r")(?![\w-])")
    allowed = {_str(data, "target", what)}
    allowed.update(_str_list(data, "aliases", what, allow_empty=True))
    allowed.update(_str_list(data, "dependencies", what, allow_empty=True))
    return {"id_re": id_re, "allowed": allowed}


# --- bounded adopter regexes ------------------------------------------------------------------------

def _child_limits(parent):
    """In the regex child only, before it runs: a CPU-time limit (RLIMIT_CPU: SIGXCPU after CHILD_CPU
    seconds), which bounds the child even when no parent is left to enforce the deadline; on Linux,
    SIGKILL when the parent dies (PR_SET_PDEATHSIG) and an immediate exit when the parent is already
    gone; and an address-space cap (best effort). A failure of the first two raises, so the child never
    starts unbounded (subprocess reports it and bounded_regex fails closed)."""
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (CHILD_CPU, CHILD_CPU + 1))
    if sys.platform.startswith("linux"):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, int(signal.SIGKILL), 0, 0, 0) != 0:  # 1 is PR_SET_PDEATHSIG
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) failed")
        if os.getppid() != parent:
            os._exit(2)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (CHILD_MEMORY, CHILD_MEMORY))
    except (ValueError, OSError):
        pass


def bounded_regex(jobs):
    """Match adopter regexes in a child interpreter under REGEX_DEADLINE seconds. A job is (pattern, op,
    items): op "fullmatch" answers a groupdict or None per item, op "findall" the matched texts per item.
    An overrun, a failed child, or a child that writes anything to stderr is a GateError (exit 2): an
    adopter regex can never hang the gate, and a child's answers count only from a clean run."""
    if not any(items for _, _, items in jobs):
        return [[] for _ in jobs]
    if not sys.executable:
        raise GateError("no interpreter path for the regex child")
    if os.name != "posix":
        raise GateError("the regex child needs a POSIX CPU-time limit, which this platform lacks")
    payload = json.dumps([dict(pattern=p, op=op, items=items) for p, op, items in jobs]).encode("ascii")
    argv = [sys.executable, "-I", "-B", os.path.abspath(__file__), "--regex-worker"]
    try:
        done = subprocess.run(argv, input=payload, capture_output=True, timeout=REGEX_DEADLINE,
                              preexec_fn=functools.partial(_child_limits, os.getpid()))
    except subprocess.TimeoutExpired:
        raise GateError("an adopter regex did not finish within the {:g}-second deadline (it exceeds "
                        "the bound)".format(REGEX_DEADLINE))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise GateError("the regex child could not run ({})".format(exc))
    xcpu = getattr(signal, "SIGXCPU", None)
    if xcpu is not None and done.returncode == -xcpu:
        raise GateError("the regex child exceeded its {}-second CPU-time limit (an adopter regex exceeds the "
                        "bound)".format(CHILD_CPU))
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        raise GateError("the regex child failed with exit {} ({})".format(done.returncode, " ".join(tail)))
    if done.stderr:
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-1:]
        raise GateError("the regex child wrote to stderr ({}); its answers do not count".format(" ".join(tail)))
    try:
        answers = json.loads(done.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GateError("the regex child answered unreadably ({})".format(exc))
    if not isinstance(answers, list) or [len(a) for a in answers] != [len(i) for _, _, i in jobs]:
        raise GateError("the regex child answered the wrong shape")
    return answers


def regex_worker():
    """The child side of bounded_regex: jobs as JSON on stdin, answers as JSON on stdout."""
    jobs = json.loads(sys.stdin.buffer.read().decode("ascii"))
    answers = []
    for job in jobs:
        regex = re.compile(job["pattern"])
        if job["op"] == "fullmatch":
            answers.append([None if m is None else m.groupdict() for m in map(regex.fullmatch, job["items"])])
        else:
            answers.append([[m.group(0) for m in regex.finditer(s)] for s in job["items"]])
    sys.stdout.write(json.dumps(answers))
    return 0


# --- text regions -----------------------------------------------------------------------------------

def _invisible(c):
    """A character that renders as nothing: a default-ignorable code point, a format character (Cf), or a
    control character (Cc) that is not whitespace."""
    o = ord(c)
    cat = unicodedata.category(c)
    return cat == "Cf" or (cat == "Cc" and not c.isspace()) or any(a <= o <= b for a, b in IGNORABLE)


def _clean(line):
    """One line with every invisible character removed and every whitespace or blank-rendering character
    a space. File names are read from this form."""
    if line.isascii() and line.isprintable():
        return line
    return "".join(" " if c.isspace() or ord(c) in BLANKS else c for c in line
                   if ord(c) in BLANKS or not _invisible(c))


def _norm(line):
    """The comparison form of one line, the same on the brief side and the source side: _clean, NFKC,
    then _clean again (NFKC can produce a blank or a space)."""
    if line.isascii() and line.isprintable():
        return line
    return _clean(unicodedata.normalize("NFKC", _clean(line)))


def _bounded_int(text, limit=MAX_ROUND_DIGITS):
    """int(text) for a decimal string of at most limit digits, else None: int() is never given an
    unbounded string (CPython refuses one over 4300 digits with a ValueError)."""
    if text and text.isdecimal() and len(text) <= limit:
        return int(text)
    return None


def _number(tok):
    """The signed value of a number token in the supported grammar (ASCII digits, optionally grouped by
    commas in threes, an optional + - or U+2212 sign, at most MAX_NUMBER_DIGITS digits), else None: a
    decimal, a malformed group, another sign or script is outside the grammar."""
    m = SUPPORTED_NUMBER_RE.fullmatch(tok)
    if m is None:
        return None
    value = _bounded_int(m.group(2).replace(",", ""), MAX_NUMBER_DIGITS)
    if value is None:
        return None
    return -value if m.group(1) in ("-", "\u2212") else value


def _fence(line):
    """The fence run a CommonMark fence opener starts, or None: at most three spaces of indentation,
    three or more backticks or tildes, and no backtick in a backtick fence's info string."""
    m = FENCE_OPEN_RE.match(line)
    if m is None or (m.group(1)[0] == "`" and "`" in m.group(2)):
        return None
    return m.group(1)


def _closes(line, fence):
    m = FENCE_CLOSE_RE.match(line)
    return bool(m) and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence)


def operative_lines(text):
    """A source text's lines outside fenced blocks and `>` quote lines: ([(lineno, line)], the line number
    of a fence that is never closed, or None)."""
    out, fence, opened = [], None, None
    for n, line in enumerate(text.splitlines(), 1):
        if fence is not None:
            if _closes(line, fence):
                fence = None
            continue
        f = _fence(line)
        if f:
            fence, opened = f, n
            continue
        if line.lstrip().startswith(">"):
            continue
        out.append((n, line))
    return out, (opened if fence is not None else None)


def brief_regions(text, cfg, what="brief"):
    """Split a brief (or template) into its own text [(lineno, line with format characters removed and
    backticks blanked)] and its declared verbatim sections. An unterminated section or fence is a
    GateError (the rest of the text cannot be read)."""
    own, sections, fence, opened, sec = [], [], None, None, None
    for n, raw in enumerate(raw_lines(text), 1):
        line = _clean(raw)
        s = line.strip()
        if sec is not None:
            if s.startswith(cfg["end"]):
                sections.append(sec)
                sec = None
            else:
                sec["lines"].append(raw)
            continue
        if fence is not None:
            if _closes(line, fence):
                fence = None
            continue
        f = _fence(line)
        if f:
            fence, opened = f, n
            continue
        if s.startswith(">"):
            continue
        if s.startswith(cfg["begin"]):
            m = cfg["family_re"].search(s[len(cfg["begin"]):])
            sec = {"lineno": n, "family": m.group(1).casefold() if m else None, "lines": []}
            continue
        own.append((n, line.replace("`", " ")))
    if sec is not None:
        raise GateError("verbatim section opened at {} line {} is never closed".format(what, sec["lineno"]))
    if fence is not None:
        raise GateError("fence opened at {} line {} is never closed; the rest of the {} cannot be "
                        "read".format(what, opened, what))
    return own, sections


def _blank(m):
    return " " * len(m.group(0))


def _tokens(own):
    for n, line in own:
        for m in TOKEN_RE.finditer(line):
            tok = m.group(0).rstrip(".:!?")
            if tok:
                yield n, m.start(), tok


def _groups(found):
    """item, round and family of a review-file-pattern groupdict (None for no match)."""
    if found is None:
        return None
    return {"item": found.get("item"), "round": _bounded_int(found.get("round")),
            "family": (found.get("family") or "").casefold()}


def named_refs(own, groups_of, input_dir):
    """Every named-file token in the brief's own text: a basename full-matching the review-file pattern
    (groups_of, answered by the bounded child), or an absolute path under the input directory."""
    root = os.path.abspath(input_dir) if input_dir else None
    refs = []
    for n, start, tok in _tokens(own):
        base = tok.rsplit("/", 1)[-1]
        groups = groups_of.get(base)
        under = bool(root) and tok.startswith(root + os.sep)
        if groups is None and not under:
            continue
        refs.append({"lineno": n, "start": start, "end": start + len(tok), "token": tok, "base": base,
                     "groups": groups})
    return refs


def resolve_named(token, input_dir):
    """Resolve one named file inside the input directory, fail-closed. Returns (path, real, text, sha256)."""
    root = os.path.realpath(input_dir)
    path = token if token.startswith("/") else os.path.join(input_dir, token)
    try:
        os.lstat(path)
    except FileNotFoundError:
        raise GateError("named file {} does not exist (a similarly named sibling is never "
                        "substituted)".format(token))
    except OSError as exc:
        raise GateError("named file {} cannot be examined ({})".format(token, exc.strerror or exc))
    real = os.path.realpath(path)
    if os.path.commonpath([real, root]) != root:
        raise GateError("named file {} resolves outside --input-dir".format(token))
    text, digest = read_text(path, "named file")
    return os.path.abspath(path), real, text, digest


def _balanced(line, i):
    depth = 0
    for j in range(i, len(line)):
        if line[j] == "(":
            depth += 1
        elif line[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return None


def split_families(content, cfg):
    """Split a parenthetical at family words: [(family or None, segment text)]."""
    marks = list(cfg["family_re"].finditer(content))
    if not marks:
        return [(None, content)]
    out = []
    if content[:marks[0].start()].strip(" ,;:"):
        out.append((None, content[:marks[0].start()]))
    for k, m in enumerate(marks):
        stop = marks[k + 1].start() if k + 1 < len(marks) else len(content)
        out.append((m.group(1).casefold(), content[m.end():stop]))
    return out


# --- verdicts (one classifier for both sides) -------------------------------------------------------

def verdict_hits(text, cfg):
    """THE verdict classifier, shared by the brief side and the source side. Returns (hits, ambiguous),
    each [(start, end, token)] in text order. A space inside a token matches any run of whitespace; an
    occurrence lying inside a longer token's occurrence belongs to the longer token; occurrences that
    otherwise overlap are ambiguous."""
    occ = []
    for token, regex in cfg["verdict_res"]:
        occ.extend((m.start(), m.end(), token) for m in regex.finditer(text))
    occ = [o for o in occ if not any(p[0] <= o[0] and o[1] <= p[1] and p[1] - p[0] > o[1] - o[0]
                                     for p in occ)]
    amb = [o for k, o in enumerate(occ) if any(q != k and p[0] < o[1] and o[0] < p[1]
                                               for q, p in enumerate(occ))]
    return sorted(o for o in occ if o not in amb), sorted(amb)


def classify_verdict(value, cfg):
    """(token, None) when value carries exactly one configured verdict token, else (None, reason)."""
    hits, amb = verdict_hits(value, cfg)
    if amb:
        return None, "overlapping verdict tokens in {!r} (ambiguous)".format(value)
    tokens = sorted(set(h[2] for h in hits))
    if len(tokens) == 1:
        return tokens[0], None
    return None, "{!r} carries {} configured verdict token".format(value, "several" if tokens else "no")


def verdict_records(lines, cfg):
    """Every verdict record among raw operative lines as (lineno, token or None, problem or None). The one
    well-formed record is a line that starts at column 0 with `VERDICT:`; its value (in the _norm form) is
    classified by classify_verdict. Any OTHER line that names a verdict (the word verdict in any case or
    decoration) AND carries a configured verdict token is a competing record in another form (`Final
    VERDICT: X`, `1. VERDICT: X`, `VERDICT (revised): X`, `**Verdict:** X`, a table cell, an indented or
    BOM-prefixed record), so it is a problem, never skipped. A verdict line with no configured token
    (`**Verdict:** the change is sound`) is prose, not a record."""
    out = []
    for n, raw in lines:
        if raw.startswith("VERDICT:"):
            value = _norm(raw[len("VERDICT:"):]).strip()
            if not value:
                out.append((n, None, "line {} is an empty VERDICT line".format(n)))
            else:
                token, why = classify_verdict(value, cfg)
                out.append((n, token, None if why is None else "line {}: {}".format(n, why)))
            continue
        line = _norm(raw)
        if VERDICT_WORD_RE.search(line) and any(verdict_hits(line, cfg)):
            out.append((n, None, "line {} states a verdict token outside a column-0 VERDICT: record "
                                 "(indented, BOM-prefixed, decorated, numbered, qualified, tabulated or "
                                 "differently cased): {!r}".format(n, line.strip())))
    return out


def single_verdict(lines, unclosed, cfg):
    """(token, None) for exactly one well-formed operative verdict record, else (None, reason)."""
    if unclosed is not None:
        return None, "a fence opened at line {} is never closed".format(unclosed)
    recs = verdict_records(lines, cfg)
    if not recs:
        return None, "no operative VERDICT line"
    if len(recs) > 1:
        return None, "{} operative verdict lines (lines {}); exactly one is required{}".format(
            len(recs), ", ".join(str(r[0]) for r in recs), "".join("; " + r[2] for r in recs if r[2])[:300])
    return recs[0][1], recs[0][2]


# --- source analysis --------------------------------------------------------------------------------

def analyze_source(text, cfg):
    """Graded findings of a source file: numbered items, grade headings, and labelled lines (table rows
    skipped). Returns a dict: ids, the set of (grade, n); counts, findings per grade; text, the operative
    text; unclosed, the line of a fence never closed (or None); verdict, single_verdict's answer."""
    findings, ordinal, bad = [], dict(), []
    lines, unclosed = operative_lines(text)
    normed = [(n, _norm(line)) for n, line in lines]
    for n, line in normed:
        if line.lstrip().startswith("|"):
            continue
        m = cfg["numbered_re"].match(line)
        if m:
            num = _bounded_int(m.group(1))
            if num is None:
                bad.append(n)
            else:
                findings.append((m.group(2).upper(), num))
            continue
        m = cfg["heading_re"].match(line)
        if m:
            num = _bounded_int(m.group(2)) if m.group(2) else None
            if m.group(2) and num is None:
                bad.append(n)
            else:
                findings.append((m.group(1).upper(), num))
            continue
        m = cfg["label_re"].search(line)
        if m:
            findings.append((m.group(1).upper(), None))
    ids, counts = set(), dict()
    for grade, num in findings:
        counts[grade] = counts.get(grade, 0) + 1
        if num is None:
            ordinal[grade] = ordinal.get(grade, 0) + 1
            num = ordinal[grade]
        ids.add((grade, num))
    return dict(ids=ids, counts=counts, text="\n".join(l for _, l in normed), unclosed=unclosed,
                verdict=single_verdict(lines, unclosed, cfg),
                full="\n".join(_norm(l) for l in text.splitlines()),
                bad=("source line {} carries a finding number too long to read".format(bad[0]) if bad else None))


def id_present(info, grade, num):
    if (grade, num) in info["ids"]:
        return True
    lit = re.compile(r"(?<![\w.-])" + re.escape(grade) + r"[- ]?" + str(num) + r"(?![\w-]|\.\d)", re.I)
    return bool(lit.search(info["text"]))


def _reading(toks, i):
    """How the source pairs the predicate word toks[i]: ("of", N, M) for "N of M word"; ("one", N) for the
    one number among the three words before it; ("unclear", wording) for two numbers there, or a number
    that is itself the M of "N of M"; None for no number."""
    isnum = [t[0] in SIGNS or t[0].isdigit() for t in toks]
    if i >= 3 and isnum[i - 1] and toks[i - 2].lower() == "of" and isnum[i - 3]:
        return ("of", toks[i - 3], toks[i - 1])
    window = [j for j in range(max(0, i - 3), i) if isnum[j]]
    if not window:
        return None
    j = window[0]
    if len(window) > 1 or (j >= 2 and toks[j - 1].lower() == "of" and isnum[j - 2]):
        return ("unclear", " ".join(toks[max(0, min(j - 2, i - 3)):i + 1]))
    return ("one", toks[j])


def predicate_check(claim, pred, text):
    """(status, detail) for a predicate claim, or None when the source never pairs the word. claim is the
    brief's number tokens: (N,) for "N word", (N, M) for "N of M word". Clauses end at , ; : . ! ? before
    whitespace and at line ends; _reading pairs the word within its clause. A number keeps its sign; a
    number outside the supported grammar (_number) on either side, two numbers in the window, different
    readings in different clauses, or a bare count that equals one number of an "N of M" reading is
    ambiguous (cannot-evaluate), never a pairing across an intervening number."""
    readings, unclear = [], []
    for clause in CLAUSE_RE.split(text):
        toks = SRC_WORD_RE.findall(clause)
        for i, t in enumerate(toks):
            if t.lower() == pred:
                r = _reading(toks, i)
                if r is not None:
                    (unclear if r[0] == "unclear" else readings).append(r)
    if not readings and not unclear:
        return None
    if unclear:
        return CANNOT, "source wording {!r} pairs {!r} with more than one number".format(unclear[0][1], pred)
    bad = [x for r in readings for x in r[1:] if _number(x) is None] + [x for x in claim if _number(x) is None]
    if bad:
        return CANNOT, ("number {!r} is outside the supported grammar (ASCII digits, commas in threes, a sign, "
                        "at most {} digits)".format(bad[0], MAX_NUMBER_DIGITS))
    values = sorted(set(tuple(_number(x) for x in r[1:]) for r in readings))
    if len(values) > 1:
        return CANNOT, "source pairs {!r} with different numbers {}".format(pred, values)
    have, want = values[0], tuple(_number(x) for x in claim)
    shown = " of ".join(str(v) for v in have)
    if have == want:
        return MATCH, "source states {} {}".format(shown, pred)
    if len(have) == len(want) or not set(have) & set(want):
        return MISMATCH, "source binds {!r} to {}, not {}".format(pred, shown, " of ".join(claim))
    return CANNOT, "source wording '{} {}' pairs {!r} with more than one number".format(shown, pred, pred)


# --- claims -----------------------------------------------------------------------------------------

def _clusters(spans):
    out = []
    for a, b, _ in sorted(spans):
        if out and a < out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def parse_claims(text, cfg):
    """Claims in one bound segment as (kind, claim text, match or None), in a fixed order with each match
    masked from the later passes. Kind "verdict?" is an ambiguous verdict (overlapping tokens)."""
    claims = []
    buf = FROM_ROUND_RE.sub(_blank, text)
    hits, amb = verdict_hits(buf, cfg)
    options = _options(buf, hits)
    claims.extend(("verdict", h[2], None) for k, h in enumerate(hits) if k not in options)
    claims.extend(("verdict?", " ".join(buf[a:b].split()), None) for a, b in _clusters(amb))
    for a, b, _ in hits + amb:
        buf = buf[:a] + " " * (b - a) + buf[b:]

    def take(regex, kind):
        nonlocal buf
        claims.extend((kind, m.group(0).strip(), m) for m in regex.finditer(buf))
        buf = regex.sub(_blank, buf)

    take(cfg["count_re"], "count")
    take(cfg["range_re"], "range")
    take(cfg["id_re"], "id")
    take(cfg["alias_re"], "alias")
    take(cfg["colon_re"], "colon")
    for m in PRED_OF_RE.finditer(buf):
        claims.append(("pred", " ".join(m.group(0).split()), ((m.group(1), m.group(2)), m.group(3).lower())))
    buf = PRED_OF_RE.sub(_blank, buf)
    for m in PRED_RE.finditer(buf):
        word = m.group(2).upper()
        if word in cfg["grades"] or word.rstrip("S") in cfg["grades"]:
            continue
        plain = SUPPORTED_NUMBER_RE.fullmatch(m.group(1))
        if plain and "," not in plain.group(2) and len(plain.group(2)) < 2:
            continue  # a one-digit count is not a predicate claim
        claims.append(("pred", m.group(0).strip(), ((m.group(1),), m.group(2).lower())))
    return claims


def _options(buf, hits):
    """Indexes of verdict hits that form an option list: two or more different tokens joined only by "or"
    or "/" (an instruction such as "end with VERDICT: X or VERDICT: Y"). It asserts neither token, so it is
    not a claim; any other occurrence in the segment still is."""
    drop, run = set(), [0]
    for k in range(1, len(hits) + 1):
        if k < len(hits) and OPTION_GAP_RE.fullmatch(buf[hits[k - 1][1]:hits[k][0]]):
            run.append(k)
            continue
        if len(set(hits[j][2] for j in run)) > 1:
            drop.update(run)
        run = [k]
    return drop


def _res(status, lineno, claim, src, detail):
    return dict(status=status, lineno=lineno, claim=claim, src=src, detail=detail)


def _ids_of(kind, m, fam, cfg):
    """Expand an id, range or alias claim to [(grade, n)], or raise ValueError(reason) (cannot-evaluate)."""
    if None in [_bounded_int(x) for x in m.groups()[1:] if x and x.isdecimal()]:
        raise ValueError("a number in {!r} is longer than {} digits".format(m.group(0), MAX_ROUND_DIGITS))
    if kind == "id":
        return [(m.group(1).upper(), int(m.group(2)))]
    if kind == "range":
        g1, g2 = m.group(1).upper(), (m.group(3) or m.group(1)).upper()
        lo, hi = int(m.group(2)), int(m.group(4))
        if g1 != g2 or hi < lo or hi - lo > MAX_RANGE:
            raise ValueError("range {!r} cannot be expanded".format(m.group(0)))
        return [(g1, n) for n in range(lo, hi + 1)]
    letters = m.group(1)
    grade = cfg["aliases"].get(fam or "", dict()).get(letters)
    if grade is None:
        raise ValueError("alias {!r} is not declared for family {!r}".format(letters, fam))
    lo = int(m.group(2))
    hi = int(m.group(3)) if m.group(3) else lo
    if hi < lo or hi - lo > MAX_RANGE:
        raise ValueError("range {!r} cannot be expanded".format(m.group(0)))
    return [(grade, n) for n in range(lo, hi + 1)]


def _judge_verdict(kind, claim, info, cfg, lineno, src, regrade):
    if regrade:
        return _res(EXEMPT, lineno, claim, src,
                    "regrade disclosed ({}); verdict not compared".format(cfg["regrade"]))
    if kind == "verdict?":
        return _res(CANNOT, lineno, claim, src, "overlapping verdict tokens: the claim is ambiguous")
    token, why = info["verdict"]
    if why is not None:
        return _res(CANNOT, lineno, claim, src, "source: " + why)
    if token == claim:
        return _res(MATCH, lineno, claim, src, "VERDICT: " + token)
    return _res(MISMATCH, lineno, claim, src, "source VERDICT is {!r}".format(token))


def judge_segment(seg, files, cfg, infos):
    """Results for one claim segment. seg: lineno, text, token (bound file or None), why (if unbound),
    ambiguous (several candidate files)."""
    out = []
    n = seg["lineno"]
    claims = parse_claims(seg["text"], cfg)
    if seg["token"] is None:
        for kind, claim, _ in claims:
            out.append(_res(CANNOT, n, claim, None,
                            "no resolvable binding: " + seg["why"]))
        return out
    f, info = files[seg["token"]], infos[seg["token"]]
    src = (f["path"], f["sha"])
    excerpt = seg["text"].strip(" ,;:")
    long_excerpt = len(excerpt) >= MIN_EXCERPT
    if info["unclosed"] is not None:
        if claims or long_excerpt:
            out.append(_res(CANNOT, n, excerpt, src, "a fence opened at source line {} is never "
                            "closed; the operative text cannot be read".format(info["unclosed"])))
        return out
    excerpt_only = False  # an excerpt stands for its ids, grades and counts, never for a verdict claim in it
    if long_excerpt:
        others = sorted(o["path"] for t, o in files.items() if t != seg["token"] and excerpt in infos[t]["text"])
        if excerpt in info["text"]:
            excerpt_only = True
            out.append(_res(MATCH, n, excerpt, src, "verbatim excerpt of its bound file (a verdict "
                            "claim in it is judged on its own)"))
        elif others:
            excerpt_only = True
            out.append(_res(MISMATCH, n, excerpt, src,
                            "misattributed: a verbatim excerpt of {}, not of its bound file".format(others[0])))
    fam = (f["groups"] or dict()).get("family")
    regrade = cfg["regrade"] in seg["text"]
    for kind, claim, m in claims:
        if kind in ("verdict", "verdict?"):
            out.append(_judge_verdict(kind, claim, info, cfg, n, src, regrade))
        elif excerpt_only:
            continue
        elif kind != "pred" and info["bad"] is not None:
            out.append(_res(CANNOT, n, claim, src, info["bad"]))
        elif kind in ("id", "range", "alias"):
            try:
                wanted = _ids_of(kind, m, fam, cfg)
            except ValueError as exc:
                out.append(_res(CANNOT, n, claim, src, str(exc)))
                continue
            for grade, num in wanted:
                label = "{}-{}".format(grade, num)
                ok = id_present(info, grade, num)
                out.append(_res(MATCH if ok else MISMATCH, n, "id " + label, src,
                                "present" if ok else "no finding {} in the source".format(label)))
        elif kind == "colon":
            have = info["counts"].get(m.group(1).upper(), 0)
            out.append(_res(MATCH if have else MISMATCH, n, claim, src,
                            "{} {} finding(s) in the source".format(have, m.group(1).upper())))
        elif kind == "count":
            grade, num = m.group(2).upper(), _bounded_int(m.group(1))
            if num is None:
                out.append(_res(CANNOT, n, claim, src, "the count is longer than {} digits".format(
                    MAX_ROUND_DIGITS)))
                continue
            have = info["counts"].get(grade, 0)
            out.append(_res(MATCH if have == num else MISMATCH, n, claim, src,
                            "the source grades {} finding(s) {}".format(have, grade)))
        else:
            verdict = predicate_check(m[0], m[1], info["text"])
            if verdict is not None:
                out.append(_res(verdict[0], n, claim, src,
                                verdict[1]))
    return out


def _paren_after(line, end):
    """(open index, close index or None) of a parenthetical directly after position end, or None."""
    i = end
    while i < len(line) and line[i] in " \t":
        i += 1
    if i >= len(line) or line[i] != "(":
        return None
    return i, _balanced(line, i)


def _bind(refs, files, wanted, what):
    """Bind to the one named file whose groups satisfy wanted; tokens resolving to one path are one
    candidate. Several candidates is ambiguous, never a choice by order."""
    cands = dict()
    for ref in refs:
        if ref["groups"] is not None and wanted(ref["groups"]):
            cands.setdefault(files[ref["token"]]["real"], ref["token"])
    if len(cands) == 1:
        return dict(token=next(iter(cands.values())), why="", ambiguous=False)
    if cands:
        return dict(token=None, ambiguous=True, why="{}: ambiguous between {}".format(
            what, ", ".join(sorted(cands.values()))))
    return dict(token=None, ambiguous=False, why="{}: none named".format(what))


def collect_segments(own, refs, cfg, files):
    """Bind claim segments to named files. Returns (segments, bound parentheticals [(ref, content)])."""
    segs, parens, used = [], [], dict()
    lines = dict(own)
    for ref in refs:
        line = lines[ref["lineno"]]
        found = _paren_after(line, ref["end"])
        if found is None:
            continue
        i, j = found
        if j is None:
            segs.append(dict(lineno=ref["lineno"], text=_norm(line[i + 1:]), token=None, ambiguous=False,
                             why="unbalanced parenthetical after {}".format(ref["token"])))
            used.setdefault(ref["lineno"], []).append((i, len(line)))
            continue
        content = _norm(line[i + 1:j])
        used.setdefault(ref["lineno"], []).append((i, j + 1))
        parens.append((ref, content))
        g = ref["groups"]
        for fam, text in split_families(content, cfg):
            rounds = set(_bounded_int(r) for r in FROM_ROUND_RE.findall(text))
            if fam is None and not rounds:
                segs.append(dict(lineno=ref["lineno"], text=text, token=ref["token"], why="", ambiguous=False))
                continue
            what = "family {}".format(fam) if fam else "a round-qualified segment"
            if g is None:
                bound = dict(token=None, ambiguous=False,
                             why="{} carries no item and round for {}".format(ref["token"], what))
            elif None in rounds or len(rounds) > 1:
                bound = dict(token=None, ambiguous=True, why="{} names a round longer than {} digits, or "
                             "several rounds".format(what, MAX_ROUND_DIGITS))
            else:
                fam = fam or g["family"]
                rnd = rounds.pop() if rounds else g["round"]
                if fam == g["family"] and rnd == g["round"]:
                    bound = dict(token=ref["token"], why="", ambiguous=False)
                else:
                    key = (g["item"], rnd, fam)
                    bound = _bind(refs, files, lambda h, key=key: (h["item"], h["round"], h["family"]) == key,
                                  "named {} file for item {} round {}".format(fam, g["item"], rnd))
            segs.append(dict(lineno=ref["lineno"], text=text, **bound))
    # Family-attributed claims elsewhere in the brief's own text bind to the named file of the family.
    for n, line in own:
        spans = list(used.get(n, [])) + [(r["start"], r["end"]) for r in refs if r["lineno"] == n]
        for a, b in spans:
            line = line[:a] + " " * (b - a) + line[b:]
        line = _norm(line)
        marks = list(cfg["family_re"].finditer(line))
        for k, m in enumerate(marks):
            stop = marks[k + 1].start() if k + 1 < len(marks) else len(line)
            end = re.search(r"[.;](?:\s|$)", line[m.end():stop])
            text = line[m.end():m.end() + end.start()] if end else line[m.end():stop]
            fam = m.group(1).casefold()
            segs.append(dict(lineno=n, text=text, **_bind(refs, files, lambda h: h["family"] == fam,
                                                          "named {} file".format(fam))))
    return segs, parens


def check_sections(sections, files, cfg, infos):
    out = []
    for sec in sections:
        longs = [_norm(l).strip() for l in sec["lines"]]
        longs = [l for l in longs if len(l) >= LONG_LINE and not l.startswith("VERDICT:")]
        best = None
        for token in sorted(files):
            hits = sum(1 for l in longs if l in infos[token]["full"])
            if longs and hits * 2 > len(longs) and (best is None or hits > best[1]):
                best = (token, hits)
        n, claim = sec["lineno"], "embedded section ({})".format(sec["family"])
        if best is None:
            out.append(_res(CANNOT, n, claim, None, "no named file holds a majority of its long lines"))
            continue
        f = files[best[0]]
        src = (f["path"], f["sha"])
        sfam = (f["groups"] or dict()).get("family")
        if sec["family"] is None or sfam is None:
            out.append(_res(CANNOT, n, claim, src, "family not determinable"))
        elif sfam != sec["family"]:
            out.append(_res(MISMATCH, n, claim, src, "the source is family {!r}".format(sfam)))
        else:
            out.append(_res(MATCH, n, claim, src, "family attribution"))
        lines, unclosed = operative_lines("\n".join(sec["lines"]))
        if verdict_records(lines, cfg):
            embedded, e_why = single_verdict(lines, unclosed, cfg)
            real, r_why = infos[best[0]]["verdict"]
            label = "embedded VERDICT: {}".format(embedded)
            if e_why is not None or r_why is not None:
                out.append(_res(CANNOT, n, label, src, "embedded: {}; source: {}".format(
                    e_why or "one verdict", r_why or "one verdict")))
            elif real != embedded:
                out.append(_res(MISMATCH, n, label, src, "source VERDICT is {!r}".format(real)))
            else:
                out.append(_res(MATCH, n, label, src, "verdict"))
    return out


def _title(own):
    for n, line in own:
        m = TITLE_RE.match(line)
        if m:
            return n, " ".join(m.group(1).split())
    return None, None


def check_template(own, refs, parens, files, t_own, t_refs):
    out = []
    t_lines = dict(t_own)
    t_parens = []
    for ref in t_refs:
        found = _paren_after(t_lines[ref["lineno"]], ref["end"])
        if found is not None and found[1] is not None:
            t_parens.append((ref["base"], " ".join(_norm(t_lines[ref["lineno"]][found[0] + 1:found[1]]).split())))
    for ref, content in parens:
        norm = " ".join(content.split())
        moved = sorted(set(b for b, c in t_parens if c == norm and b != ref["base"]))
        f = files[ref["token"]]
        if moved:
            out.append(_res(MISMATCH, ref["lineno"], "({})".format(norm), (f["path"], f["sha"]),
                            "carried over: the template attaches this parenthetical to {}".format(moved[0])))
    n, title = _title(own)
    if title is not None and title == _title(t_own)[1]:
        m = ROUND_RE.search(title)
        rounds = set(r["groups"]["round"] for r in refs if r["groups"] and r["groups"]["round"] is not None)
        said = _bounded_int(m.group(1)) if m else None
        if m and rounds and said is None:
            out.append(_res(CANNOT, n, title, None, "the title's round number is longer than {} "
                            "digits".format(MAX_ROUND_DIGITS)))
        elif m and rounds and said not in rounds and max(rounds) > said:
            out.append(_res(MISMATCH, n, title, None,
                            "title carried over from the template names round {}; the named inputs are "
                            "round {}".format(m.group(1), max(rounds))))
    return out


def check_foreign(own, registry, found):
    """found: the registry id-pattern's matches per own line, answered by the bounded child."""
    out = []
    for (n, _), idents in zip(own, found):
        for ident in sorted(set(idents)):
            if ident not in registry["allowed"]:
                out.append(_res(MISMATCH, n, ident, None,
                                "foreign item id: not the target, a declared alias or a declared dependency"))
    return out
# --- the blocking path: trailer records and the claims block ------------------------------------------

def raw_lines(text):
    """The lines of a text split at LF only, each with one final CR dropped: no other character ends a
    line, so a vertical tab or U+2028 stays inside its line (and fails any exact-line grammar)."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def trailer_verdict(text, cfg):
    """(token, line number) of a review file's trailer record, or a GateError. Reading upward from the last
    line, blank lines (empty or spaces and tabs) and lines starting with a configured trailer prefix are
    skipped; the first other line must be exactly `VERDICT: <token>`. The body is never read."""
    lines = raw_lines(text)
    records = dict(("VERDICT: " + t, t) for t in cfg["verdicts"])
    for n in range(len(lines), 0, -1):
        line = lines[n - 1]
        if not line.strip(" \t") or any(line.startswith(p) for p in cfg["trailers"]):
            continue
        if line in records:
            return records[line], n
        raise GateError("no trailer record: line {}, the last line before the trailer lines, is not exactly "
                        "'VERDICT: <token>' for a configured token ({!r})".format(n, line[:120]))
    raise GateError("no trailer record: the file holds no line but blank and trailer lines")


def _marker_key(line):
    """A line's marker reading: normalized, case-folded, with every character but letters and digits
    removed. A line reading as BEGIN BRIEF-CLAIMS or END BRIEF-CLAIMS that is not exactly a marker is a
    near miss (an indented, BOM-prefixed, lower-case, re-spaced or wrong-version marker)."""
    return "".join(c for c in _norm(line).casefold() if c.isalnum())


def parse_block(lines, cfg):
    """The claims block of a brief's raw lines: (entries [(lineno, name, token)], the set of line numbers
    the block occupies, problems [reason]). A problem is cannot-evaluate."""
    entry_re = re.compile(r"(" + NAME_RE.pattern + r") VERDICT (" + _alt(cfg["verdicts"]) + r")")
    entries, used, problems, seen = [], set(), [], dict()
    opened, blocks, inside = None, 0, 0
    for n, line in enumerate(lines, 1):
        if line == BLOCK_BEGIN:
            used.add(n)
            if opened is not None:
                problems.append("line {}: BEGIN inside the block opened at line {}".format(n, opened))
                continue
            blocks += 1
            if blocks > 1:
                problems.append("line {}: a second claims block (a brief carries at most one)".format(n))
            opened, inside = n, 0
            continue
        if line == BLOCK_END:
            used.add(n)
            if opened is None:
                problems.append("line {}: END with no open block".format(n))
            elif inside == 0:
                problems.append("line {}: the block opened at line {} is empty".format(n, opened))
            opened = None
            continue
        if opened is not None:
            used.add(n)
            inside += 1
            m = entry_re.fullmatch(line)
            if m is None:
                problems.append("line {}: not '<name> VERDICT <token>' in printable ASCII with a configured "
                                "token: {!r}".format(n, line[:120]))
            elif m.group(1) in seen:
                problems.append("line {}: {} is already claimed at line {}".format(n, m.group(1), seen[m.group(1)]))
            else:
                seen[m.group(1)] = n
                entries.append((n, m.group(1), m.group(2)))
            continue
        key = _marker_key(line)
        if any(key.startswith(k) for k in MARKER_KEYS):
            problems.append("line {}: reads as a claims-block marker but is not exactly {!r} or {!r}: {!r}".format(
                n, BLOCK_BEGIN, BLOCK_END, line[:120]))
    if opened is not None:
        problems.append("the block opened at line {} is never closed".format(opened))
    return entries, used, problems


def _file_entry(name, cfg, opts, groups_of):
    """(path, sha256, token, trailer line) for one block or --emit name, or a GateError."""
    if not NAME_RE.fullmatch(name):
        raise GateError("{!r} is not a basename in printable ASCII with no space or slash".format(name))
    if groups_of.get(name) is None:
        raise GateError("{} does not full-match the review-file-pattern".format(name))
    path, _, text, digest = resolve_named(name, opts["input_dir"])
    try:
        token, line = trailer_verdict(text, cfg)
    except GateError as exc:
        raise GateError("{}: {}".format(path, exc))
    return path, digest, token, line


def check_block(lines, own, cfg, opts, groups_of, entries, problems):
    """The blocking results: [(status, line)]. Block problems and unreadable files are cannot-evaluate,
    a token differing from the trailer is a MISMATCH, and a recognized named file with no block line is
    UNCLAIMED (a mismatch)."""
    out = [(CANNOT, "CANNOT-EVALUATE block: " + p) for p in problems]
    for n, name, token in entries:
        try:
            path, digest, have, line = _file_entry(name, cfg, opts, groups_of)
        except GateError as exc:
            out.append((CANNOT, "CANNOT-EVALUATE {} (brief line {}): {}".format(name, n, exc)))
            continue
        where = "{} sha256={}".format(path, digest)
        if have == token:
            out.append((MATCH, "MATCH {} VERDICT {}: {}".format(name, token, where)))
        else:
            out.append((MISMATCH, "MISMATCH {} VERDICT {}: {}: the trailer VERDICT (line {}) is {}".format(
                name, token, where, line, have)))
    claimed = set(name for _, name, _ in entries)
    first = dict()
    for n, _, tok in _tokens(own):
        base = tok.rsplit("/", 1)[-1]
        if groups_of.get(base) is not None and base not in claimed:
            first.setdefault(base, n)
    for base in sorted(first, key=lambda b: (first[b], b)):
        out.append((UNCLAIMED, "UNCLAIMED {}: brief line {} names it and the claims block has no line for "
                               "it".format(base, first[base])))
    return out


# --- advisory prose (never blocking) ----------------------------------------------------------------

def _advisory_line(r):
    status = WARN if r["status"] == MISMATCH else r["status"]
    src = "{} sha256={}".format(*r["src"]) if r["src"] else "(no source)"
    return "ADVISORY {} brief line {}: {} -> {}: {}".format(status, r["lineno"], r["claim"], src, r["detail"])


def advisory_prose(own, sections, cfg, opts, groups_of):
    """The free-prose rules of QA round 3, reported only: [ADVISORY line]. Nothing here changes the exit
    code; an input it cannot read, or a fault of its own, is one ADVISORY CANNOT-EVALUATE line."""
    try:
        return _advisory(own, sections, cfg, opts, groups_of)
    except GateError as exc:
        return ["ADVISORY CANNOT-EVALUATE prose: {}; the prose was not analyzed".format(exc)]
    except Exception as exc:  # an advisory fault is reported, never a refusal
        return ["ADVISORY CANNOT-EVALUATE prose: internal error {}: {}; the prose was not analyzed".format(
            type(exc).__name__, exc)]


def _advisory(own, sections, cfg, opts, groups_of):
    out = []
    template_text = read_text(opts["template"], "template")[0] if opts["template"] else None
    registry = load_registry(opts["registry"]) if opts["registry"] else None
    t_own = brief_regions(template_text, cfg, "template")[0] if template_text is not None else []
    t_bases = sorted(set(tok.rsplit("/", 1)[-1] for _, _, tok in _tokens(t_own)))
    jobs = [(cfg["pattern"].pattern, "fullmatch", t_bases)]
    if registry is not None:
        jobs.append((registry["id_re"].pattern, "findall", [line for _, line in own]))
    answers = bounded_regex(jobs)
    t_groups = dict(groups_of)
    t_groups.update((b, _groups(found)) for b, found in zip(t_bases, answers[0]) if found is not None)
    refs = named_refs(own, groups_of, opts["input_dir"])
    files = dict()
    for ref in refs:
        if ref["token"] in files:
            continue
        try:
            p, real, t, d = resolve_named(ref["token"], opts["input_dir"])
        except GateError as exc:
            out.append("ADVISORY CANNOT-EVALUATE brief line {}: {}".format(ref["lineno"], exc))
            continue
        files[ref["token"]] = dict(path=p, real=real, text=t, sha=d, groups=ref["groups"])
    if out:
        return out + ["ADVISORY CANNOT-EVALUATE prose: a file named in prose could not be read; the prose "
                      "was not analyzed"]
    infos = dict((t, analyze_source(f["text"], cfg)) for t, f in files.items())
    segs, parens = collect_segments(own, refs, cfg, files)
    results = []
    for seg in segs:
        results.extend(judge_segment(seg, files, cfg, infos))
    results.extend(check_sections(sections, files, cfg, infos))
    if template_text is not None:
        results.extend(check_template(own, refs, parens, files, t_own, named_refs(t_own, t_groups, None)))
    if registry is not None:
        results.extend(check_foreign(own, registry, answers[1]))
    return [_advisory_line(r) for r in results]


# --- driver -----------------------------------------------------------------------------------------

def check_brief(path, cfg, opts):
    """Check one brief; print its blocking results, then its advisory lines, then its summary line.
    Returns the brief's exit code 0/1/2 (the advisory lines never change it)."""
    results, advisory = [], []
    try:
        text, digest = read_text(path, "brief")
        print("BRIEF {} sha256={}".format(path, digest))
        lines = raw_lines(text)
        entries, used, problems = parse_block(lines, cfg)
        own, sections = brief_regions("\n".join("" if n in used else l for n, l in enumerate(lines, 1)), cfg)
        names = sorted(set(tok.rsplit("/", 1)[-1] for _, _, tok in _tokens(own)) | set(e[1] for e in entries))
        found = bounded_regex([(cfg["pattern"].pattern, "fullmatch", names)])[0]
        groups_of = dict((b, _groups(f)) for b, f in zip(names, found) if f is not None)
        results = check_block(lines, own, cfg, opts, groups_of, entries, problems)
        advisory = advisory_prose(own, sections, cfg, opts, groups_of)
    except GateError as exc:
        results.append((CANNOT, "CANNOT-EVALUATE brief {}: {}".format(path, exc)))
    for _, line in results:
        print(line)
    for line in advisory:
        print(line)
    statuses = [s for s, _ in results]
    rc = 2 if CANNOT in statuses else 1 if (MISMATCH in statuses or UNCLAIMED in statuses) else 0
    tally = ", ".join("{} {}".format(statuses.count(s), s) for s in (MATCH, MISMATCH, UNCLAIMED, CANNOT)
                      if s in statuses) or "none"
    print("brief-claims: {}: {} blocking result(s) ({}); {} advisory line(s); exit {}".format(
        path, len(results), tally, len(advisory), rc))
    return rc


def emit(cfg, opts):
    """--emit: the claims block for the named review files on stdout (exit 0), or nothing on stdout and
    one CANNOT-EVALUATE line per problem on stderr (exit 2)."""
    names = opts["briefs"]
    problems = []
    if not names:
        problems.append("--emit needs at least one review file name")
    for name in sorted(set(n for n in names if names.count(n) > 1)):
        problems.append("{} is named more than once".format(name))
    found = bounded_regex([(cfg["pattern"].pattern, "fullmatch", sorted(set(names)))])[0]
    groups_of = dict((b, _groups(f)) for b, f in zip(sorted(set(names)), found) if f is not None)
    block = [BLOCK_BEGIN]
    for name in names:
        try:
            block.append("{} VERDICT {}".format(name, _file_entry(name, cfg, opts, groups_of)[2]))
        except GateError as exc:
            problems.append(str(exc))
    if problems:
        for p in problems:
            print("CANNOT-EVALUATE {}; no block emitted".format(p), file=sys.stderr)
        return 2
    print("\n".join(block + [BLOCK_END]))
    return 0


def run(root, opts):
    """Check the named briefs (or --emit a block) under the adopter configuration at root. Returns 0/1/2."""
    config_path = Path(root) / CONFIG_REL
    stream = sys.stderr if opts["emit"] else sys.stdout
    try:
        try:
            st = os.lstat(config_path)
        except FileNotFoundError:
            if opts["emit"] or opts["require_config"]:
                raise GateError("no {} configuration; the brief-claims lint is adopter-configured".format(
                    CONFIG_REL))
            print("NOT APPLICABLE: no {} configuration; the brief-claims lint is adopter-configured and "
                  "inert without one ({} brief(s) named, none checked)".format(CONFIG_REL, len(opts["briefs"])))
            return 0
        except OSError as exc:
            raise GateError("configuration {} cannot be examined ({})".format(CONFIG_REL, exc.strerror or exc))
        if stat.S_ISLNK(st.st_mode):
            raise GateError("configuration {} is a symlink; it is not followed".format(CONFIG_REL))
        cfg = load_config(config_path)
        if not opts["briefs"] and not opts["emit"]:
            print("PASS: configuration {} is valid; no brief named, so NO brief was examined (this "
                  "configuration-only form is the shipped CI step)".format(CONFIG_REL))
            return 0
        if opts["input_dir"] is None or not os.path.isdir(opts["input_dir"]):
            raise GateError("--input-dir must name an existing directory when a brief or review file is named")
        if opts["emit"]:
            return emit(cfg, opts)
        rc = 0
        for brief in opts["briefs"]:
            rc = max(rc, check_brief(brief, cfg, opts))
        return rc
    except GateError as exc:
        print("CANNOT-EVALUATE: {}; fail-closed".format(exc), file=stream)
        return 2


def parse_args(argv):
    """The options, or None for a usage error (exit 2)."""
    opts = dict(self_test=False, emit=False, require_config=False, input_dir=None, template=None,
                registry=None, briefs=[])
    it = iter(argv)
    for arg in it:
        if arg in ("--self-test", "--emit", "--require-config"):
            opts[arg[2:].replace("-", "_")] = True
        elif arg in ("--input-dir", "--template", "--registry"):
            value = next(it, None)
            if value is None:
                return None
            opts[arg[2:].replace("-", "_")] = value
        elif arg.startswith("-"):
            return None
        else:
            opts["briefs"].append(arg)
    if opts["emit"] and (opts["template"] or opts["registry"]):
        return None
    return opts


# --- self-test --------------------------------------------------------------------------------------
# Every vector runs the real run() over a synthetic tempdir tree: placeholder families, items and files
# only. No randomness, no network; the clocks are the regex child's deadline (two vectors shorten it to
# two seconds), its CPU limit (one vector lowers it to one second), and the Linux parent-death probe (a
# copy of this gate in the tempdir, killed once its child exists). Every check id is a literal, and the
# executed set is reconciled with this suite's entry in tools/selftest_checks.toml (in-run here, and by
# tools/check_selftest_execution.py --suite brief-claims-selftest).

SUITE_ID = "brief-claims-selftest"
CHECKS_MANIFEST = Path(__file__).resolve().parent / "selftest_checks.toml"
EXECUTED = []
FAILURES = []
SKIPPED = []

GOOD_CFG = """format-version = 1
families = ["alpha", "beta"]
review-file-pattern = '(?P<item>[a-z0-9]+)-r(?P<round>[0-9]+)-(?P<family>[a-z]+)\\.txt'
verdicts = ["NO BLOCKERS", "BLOCKERS FOUND"]
trailer-prefixes = ["QA-COMPLETE", "WORKER_STATUS:"]
grades = ["BLOCKER", "MAJOR", "MEDIUM", "MINOR"]
grade-labels = ["Grade", "Defect"]
verbatim-begin = "BEGIN VERBATIM"
verbatim-end = "END VERBATIM"
regrade-token = "REGRADED"

[aliases.alpha]
M = "MAJOR"

[aliases.beta]
M = "MEDIUM"
"""

ALPHA5 = """Review of item seven, round five, by the first reviewer family.
1. **Blocker** - the parser drops the trailing record when the input ends without a newline.
2. **Major** - the retry loop never backs off between attempts on a busy server.
3. **Major** - the cache key ignores the locale and returns stale rows.
### MEDIUM 1
The error message names the wrong flag.
VERDICT: BLOCKERS FOUND
"""

BETA5 = """Review of item seven, round five, by the second reviewer family.
### MEDIUM 1
The help text omits the default value of the timeout option entirely.
### MEDIUM 2
A log line repeats the same word.
### MINOR n1
Typo.
### MINOR n2
Another typo.
VERDICT: NO BLOCKERS
"""

BLOCK_OK = ("BEGIN BRIEF-CLAIMS 1\nit7-r5-alpha.txt VERDICT BLOCKERS FOUND\nit7-r5-beta.txt VERDICT NO BLOCKERS\n"
            "END BRIEF-CLAIMS\n")
BLOCK_SWAPPED = ("BEGIN BRIEF-CLAIMS 1\nit7-r5-alpha.txt VERDICT NO BLOCKERS\nit7-r5-beta.txt VERDICT BLOCKERS "
                 "FOUND\nEND BRIEF-CLAIMS\n")
BLOCK_BETA = "BEGIN BRIEF-CLAIMS 1\nit7-r5-beta.txt VERDICT NO BLOCKERS\nEND BRIEF-CLAIMS\n"
BLOCK_ALPHA = "BEGIN BRIEF-CLAIMS 1\nit7-r5-alpha.txt VERDICT BLOCKERS FOUND\nEND BRIEF-CLAIMS\n"


def check(check_id, ok, detail=""):
    """The suite's one choke point: every check id it reaches is recorded for the execution report."""
    EXECUTED.append(check_id)
    if not ok:
        FAILURES.append((check_id, " | ".join(str(detail).strip().splitlines()[-4:])))


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data.encode("utf-8") if isinstance(data, str) else data)


def _quiet(root, argv):
    """run() in process with stdout and stderr captured: (rc, stdout, stderr); a usage error is rc 2."""
    import io
    from contextlib import redirect_stderr, redirect_stdout
    out, err = io.StringIO(), io.StringIO()
    opts = parse_args(argv)
    with redirect_stdout(out), redirect_stderr(err):
        rc = 2 if opts is None else run(root, opts)
    return rc, out.getvalue(), err.getvalue()


def _tree(base, name, files=None, cfg=GOOD_CFG, extra=None):
    """base/name with a configuration, an input dir holding files, and extra files: (root, input dir)."""
    root = os.path.join(base, name)
    indir = os.path.join(root, "in")
    os.makedirs(indir)
    if cfg is not None:
        _write(os.path.join(root, CONFIG_REL), cfg)
    if files is None:
        files = dict([("it7-r5-alpha.txt", ALPHA5), ("it7-r5-beta.txt", BETA5)])
    for rel, data in files.items():
        _write(os.path.join(indir, rel), data)
    for rel, data in (extra or dict()).items():
        _write(os.path.join(root, rel), data)
    return root, indir


def _scenario(base, name, brief, files=None, cfg=GOOD_CFG, args=(), extra=None):
    """Build a tree and a brief (@IN@ is the input dir), check the brief; return (rc, stdout + stderr)."""
    root, indir = _tree(base, name, files, cfg, extra)
    brief_path = os.path.join(root, "brief.md")
    if brief is not None:
        _write(brief_path, brief.replace("@IN@", indir))
    argv = [a.replace("@ROOT@", root) for a in args]
    rc, out, err = _quiet(root, ["--input-dir", indir] + argv + [brief_path])
    return rc, out + err


def _emit(base, name, names, files=None, cfg=GOOD_CFG):
    """--emit in a fresh tree: (rc, stdout, stderr, root, input dir)."""
    root, indir = _tree(base, name, files, cfg)
    rc, out, err = _quiet(root, ["--emit", "--input-dir", indir] + list(names))
    return rc, out, err, root, indir


def _cases_config(base):
    """Configuration, invocation and input fail-closed vectors."""
    sc = functools.partial(_scenario, base)
    tokens = '["NO BLOCKERS", "BLOCKERS FOUND"]'
    for cid, text in (
            ("cfg/version-float", GOOD_CFG.replace("format-version = 1", "format-version = 1.0")),
            ("cfg/version-bool", GOOD_CFG.replace("format-version = 1", "format-version = true")),
            ("cfg/missing-key", GOOD_CFG.replace('regrade-token = "REGRADED"\n', "")),
            ("cfg/missing-trailer-prefixes", GOOD_CFG.replace('trailer-prefixes = ["QA-COMPLETE", "WORKER_STATUS:"]\n',
                                                              "")),
            ("cfg/duplicate-family", GOOD_CFG.replace('"alpha", "beta"]', '"alpha", "beta", "Alpha"]')),
            ("cfg/empty-vocabulary", GOOD_CFG.replace(tokens, "[]")),
            ("cfg/verdict-token-not-ascii", GOOD_CFG.replace(tokens, '["NO\\u00a0BLOCKERS", "BLOCKERS FOUND"]')),
            ("cfg/verdict-token-double-space", GOOD_CFG.replace(tokens, '["NO  BLOCKERS", "BLOCKERS FOUND"]')),
            ("cfg/trailer-prefix-starts-verdict", GOOD_CFG.replace('"QA-COMPLETE", "WORKER', '"VERDICT", "WORKER')),
            ("cfg/trailer-prefix-of-verdict-colon", GOOD_CFG.replace('"QA-COMPLETE", "WORKER', '"VER", "WORKER')),
            ("cfg/trailer-prefix-not-ascii", GOOD_CFG.replace('"QA-COMPLETE", "WORKER', '"QA\\u2014DONE", "WORKER')),
            ("cfg/pattern-groups", GOOD_CFG.replace("(?P<family>[a-z]+)", "([a-z]+)")),
            ("cfg/malformed-toml", GOOD_CFG + "[[[\n"),
            ("cfg/alias-undeclared-grade", GOOD_CFG.replace('M = "MAJOR"', 'M = "SEVERE"')),
            ("cfg/alias-undeclared-family", GOOD_CFG.replace("[aliases.beta]", "[aliases.gamma]")),
            ("cfg/pattern-too-long", GOOD_CFG.replace("(?P<item>[a-z0-9]+)", "(?P<item>[a-z0-9]+)" + "(?:x)?" * 90))):
        rc, out = sc(cid.replace("/", "_"), "Nothing named.\n", cfg=text)
        check(cid, rc == 2 and "CANNOT-EVALUATE" in out, out)
    rc, out = sc("cfg_valid", "Nothing named here.\n")
    check("cfg/valid-nothing-named", rc == 0 and "0 blocking result(s)" in out, out)

    nocfg = os.path.join(base, "nocfg")
    os.makedirs(os.path.join(nocfg, "in"))
    _write(os.path.join(nocfg, "b.md"), "Inputs: it7-r5-alpha.txt\n")
    rc, out, _ = _quiet(nocfg, [])
    check("cfg/absent-not-applicable", rc == 0 and "NOT APPLICABLE" in out, out)
    rc, out, _ = _quiet(nocfg, ["--input-dir", os.path.join(nocfg, "in"), os.path.join(nocfg, "b.md")])
    check("cfg/absent-brief-not-applicable", rc == 0 and "NOT APPLICABLE" in out, out)
    rc, out, err = _quiet(nocfg, ["--require-config", "--input-dir", os.path.join(nocfg, "in"),
                                  os.path.join(nocfg, "b.md")])
    check("cfg/absent-require-config", rc == 2 and "CANNOT-EVALUATE" in out, out + err)
    rc, out, err = _quiet(nocfg, ["--emit", "--input-dir", os.path.join(nocfg, "in"), "it7-r5-alpha.txt"])
    check("cfg/absent-emit", rc == 2 and out == "" and "CANNOT-EVALUATE" in err, out + err)
    root = os.path.join(base, "cfglink")
    _write(os.path.join(root, "real.toml"), GOOD_CFG)
    os.makedirs(os.path.join(root, ".aiqt"))
    os.symlink(os.path.join(root, "real.toml"), os.path.join(root, CONFIG_REL))
    check("cfg/symlink", _quiet(root, [])[0] == 2)
    root = os.path.join(base, "ci_form")
    _write(os.path.join(root, CONFIG_REL), GOOD_CFG)
    rc, out, _ = _quiet(root, [])
    check("ci/configuration-only-examines-no-brief", rc == 0 and "NO brief was examined" in out, out)
    _write(os.path.join(root, "b.md"), BLOCK_OK)
    rc, out, _ = _quiet(root, [os.path.join(root, "b.md")])
    check("in/missing-input-dir", rc == 2 and "--input-dir" in out, out)
    rc, out = sc("unreadable_brief", None)
    check("in/unreadable-brief", rc == 2 and "CANNOT-EVALUATE brief" in out, out)
    check("usage/strict-removed", parse_args(["--strict", "b.md"]) is None)
    check("usage/emit-takes-no-template", parse_args(["--emit", "--template", "t.md", "x.txt"]) is None)
    check("usage/unknown-option", _quiet(root, ["--bogus"])[0] == 2)

    opened = []
    me = sys.modules[__name__]
    real_read = me._read_bytes
    me._read_bytes = lambda p: opened.append(os.path.basename(p)) or real_read(p)
    try:
        rc, out = sc("failed_sibling", BLOCK_ALPHA, files=dict([("it7-r5-alpha-FAILED.txt", ALPHA5)]))
    finally:
        me._read_bytes = real_read
    check("in/failed-sibling-not-substituted", rc == 2 and "it7-r5-alpha-FAILED.txt" not in opened
          and "does not exist" in out, out)
    rc, out = sc("missing_file", BLOCK_ALPHA, files=dict())
    check("in/missing-file", rc == 2 and "does not exist" in out, out)
    rc, out = sc("non_utf8", BLOCK_ALPHA, files=dict([("it7-r5-alpha.txt", b"\xff\xfeVERDICT: BLOCKERS FOUND\n")]))
    check("in/non-utf8", rc == 2 and "not valid UTF-8" in out, out)
    rc, out = sc("directory", BLOCK_ALPHA, files=dict([("it7-r5-alpha.txt/x", "x\n")]))
    check("in/directory", rc == 2 and "not a regular file" in out, out)
    root, indir = _tree(base, "escape", files=dict())
    _write(os.path.join(root, "outside", "it7-r5-alpha.txt"), ALPHA5)
    os.symlink(os.path.join(root, "outside", "it7-r5-alpha.txt"), os.path.join(indir, "it7-r5-alpha.txt"))
    _write(os.path.join(root, "b.md"), BLOCK_ALPHA)
    rc, out, _ = _quiet(root, ["--input-dir", indir, os.path.join(root, "b.md")])
    check("in/symlink-escape", rc == 2 and "outside --input-dir" in out, out)


def _cases_trailer(base):
    """The source verdict is the trailer record, exactly; the body is never read."""
    sc = functools.partial(_scenario, base)
    found = BETA5.replace("VERDICT: NO BLOCKERS", "VERDICT: BLOCKERS FOUND")
    body = ("Round 4 verdict was BLOCKERS FOUND; this round differs.\n1. VERDICT: BLOCKERS FOUND\n"
            "Final VERDICT: BLOCKERS FOUND\n**VER**DICT: BLOCKERS FOUND\n| VERDICT: | BLOCKERS FOUND |\n"
            "Previous verdict: BLOCKERS FOUND\n```\nVERDICT: BLOCKERS FOUND\n")
    for cid, source, want in (
            ("trailer/plain-match", BETA5, 0),
            ("trailer/skips-blank-and-trailer-lines", BETA5 + "\n \t\nQA-COMPLETE\nWORKER_STATUS: COMPLETE\n\n", 0),
            ("trailer/crlf", BETA5.replace("\n", "\r\n"), 0),
            ("trailer/no-final-newline", BETA5.rstrip("\n"), 0),
            ("trailer/body-never-read", body + "```\n" + BETA5, 0),
            ("trailer/earlier-record-not-read", "VERDICT: BLOCKERS FOUND\n" + BETA5, 0),
            ("trailer/prefix-line-not-a-record", found + "WORKER_STATUS: VERDICT: NO BLOCKERS\n", 1),
            ("trailer/differs", found, 1),
            ("trailer/missing", BETA5.replace("VERDICT: NO BLOCKERS\n", ""), 2),
            ("trailer/prose-after-record", BETA5 + "Thanks for reading.\n", 2),
            ("trailer/closing-fence-after-record", "```\n" + BETA5 + "```\n", 2),
            ("trailer/decorated", BETA5.replace("VERDICT: NO BLOCKERS", "**VERDICT:** NO BLOCKERS"), 2),
            ("trailer/indented", BETA5.replace("VERDICT:", "  VERDICT:"), 2),
            ("trailer/bom-only", "\N{ZERO WIDTH NO-BREAK SPACE}VERDICT: NO BLOCKERS\n", 2),
            ("trailer/other-case", BETA5.replace("VERDICT:", "Verdict:"), 2),
            ("trailer/trailing-space", BETA5.replace("NO BLOCKERS", "NO BLOCKERS "), 2),
            ("trailer/value-with-comment", BETA5.replace("NO BLOCKERS", "NO BLOCKERS (two minors deferred)"), 2),
            ("trailer/no-token", BETA5.replace("VERDICT: NO BLOCKERS", "VERDICT: PASS"), 2),
            ("trailer/overlapping-tokens", BETA5.replace("NO BLOCKERS", "NO BLOCKERS FOUND"), 2),
            ("trailer/fullwidth-letters", BETA5.replace("NO BLOCKERS", "\N{FULLWIDTH LATIN CAPITAL LETTER N}"
                                                        "\N{FULLWIDTH LATIN CAPITAL LETTER O} BLOCKERS"), 2),
            ("trailer/nbsp-in-token", BETA5.replace("NO BLOCKERS", "NO\N{NO-BREAK SPACE}BLOCKERS"), 2),
            ("trailer/zero-width-in-token", BETA5.replace("NO BLOCKERS", "NO\N{ZERO WIDTH SPACE} BLOCKERS"), 2),
            ("trailer/line-separator", BETA5.replace("NO BLOCKERS", "NO BLOCKERS\N{LINE SEPARATOR}"), 2),
            ("trailer/vertical-tab", BETA5.replace("NO BLOCKERS", "NO BLOCKERS\x0b"), 2),
            ("trailer/empty-file", "", 2),
            ("trailer/only-trailer-lines", "WORKER_STATUS: COMPLETE\n", 2)):
        rc, out = sc(cid.replace("/", "_"), BLOCK_BETA, files=dict([("it7-r5-beta.txt", source)]))
        check(cid, rc == want and ("no trailer record" in out if want == 2 else True), out)


def _cases_block(base):
    """The claims block: a closed grammar; every deviation is cannot-evaluate."""
    sc = functools.partial(_scenario, base)
    head = "BEGIN BRIEF-CLAIMS 1\n"
    tail = "END BRIEF-CLAIMS\n"
    a_line = "it7-r5-alpha.txt VERDICT BLOCKERS FOUND\n"
    named = "Inputs: it7-r5-alpha.txt and it7-r5-beta.txt.\n"
    for cid, brief, want in (
            ("block/match", named + BLOCK_OK, 0),
            ("block/mismatch-swapped", named + BLOCK_SWAPPED, 1),
            ("block/names-only-in-block", BLOCK_OK, 0),
            ("block/inside-fence-read", "```\n" + BLOCK_OK + "```\n", 0),
            ("block/inside-fence-mismatch", "```\n" + BLOCK_SWAPPED + "```\n", 1),
            ("block/crlf", BLOCK_OK.replace("\n", "\r\n"), 0),
            ("block/two-blocks", BLOCK_OK + BLOCK_OK, 2),
            ("block/unclosed", head + a_line, 2),
            ("block/end-without-begin", tail, 2),
            ("block/empty", head + tail, 2),
            ("block/nested-begin", head + head + a_line + tail, 2),
            ("block/indented-marker", " " + BLOCK_ALPHA, 2),
            ("block/bom-marker", "\N{ZERO WIDTH NO-BREAK SPACE}" + BLOCK_ALPHA, 2),
            ("block/lower-case-marker", BLOCK_ALPHA.replace(head, head.lower()), 2),
            ("block/wrong-version", BLOCK_ALPHA.replace(head, "BEGIN BRIEF-CLAIMS 2\n"), 2),
            ("block/re-spaced-marker", BLOCK_ALPHA.replace(head, "BEGIN BRIEF CLAIMS 1\n"), 2),
            ("block/marker-trailing-space", BLOCK_ALPHA.replace(tail, "END BRIEF-CLAIMS \n"), 2),
            ("block/blank-line-inside", head + a_line + "\n" + tail, 2),
            ("block/colon-form", head + "it7-r5-alpha.txt VERDICT: BLOCKERS FOUND\n" + tail, 2),
            ("block/extra-word", head + "it7-r5-alpha.txt VERDICT BLOCKERS FOUND now\n" + tail, 2),
            ("block/line-trailing-space", head + "it7-r5-alpha.txt VERDICT BLOCKERS FOUND \n" + tail, 2),
            ("block/double-space", head + "it7-r5-alpha.txt  VERDICT BLOCKERS FOUND\n" + tail, 2),
            ("block/tab-separator", head + "it7-r5-alpha.txt\tVERDICT BLOCKERS FOUND\n" + tail, 2),
            ("block/unknown-token", head + "it7-r5-alpha.txt VERDICT PASS\n" + tail, 2),
            ("block/lower-case-token", head + "it7-r5-alpha.txt VERDICT blockers found\n" + tail, 2),
            ("block/nbsp-in-token", head + "it7-r5-beta.txt VERDICT NO\N{NO-BREAK SPACE}BLOCKERS\n" + tail, 2),
            ("block/hangul-filler", head + "it7-r5-beta.txt VERDICT NO\N{HANGUL FILLER}BLOCKERS\n" + tail, 2),
            ("block/zero-width-in-name", head + "it7-r5-\N{ZERO WIDTH SPACE}alpha.txt VERDICT BLOCKERS FOUND\n"
             + tail, 2),
            ("block/fullwidth-token", head + "it7-r5-beta.txt VERDICT \N{FULLWIDTH LATIN CAPITAL LETTER N}"
             "\N{FULLWIDTH LATIN CAPITAL LETTER O} BLOCKERS\n" + tail, 2),
            ("block/cyrillic-lookalike", head + "it7-r5-beta.txt VERDICT N\N{CYRILLIC CAPITAL LETTER O} BLOCKERS\n"
             + tail, 2),
            ("block/fullwidth-parenthesis-name", head + "it7-r5-alpha.txt\N{FULLWIDTH RIGHT PARENTHESIS} VERDICT "
             "BLOCKERS FOUND\n" + tail, 2),
            ("block/duplicate-name", head + a_line + a_line + tail, 2),
            ("block/name-not-pattern", head + "notes.txt VERDICT NO BLOCKERS\n" + tail, 2),
            ("block/name-with-slash", head + "in/it7-r5-alpha.txt VERDICT BLOCKERS FOUND\n" + tail, 2),
            ("block/mismatch-and-cannot", head + "it7-r5-alpha.txt VERDICT NO BLOCKERS\n"
             "it7-r9-alpha.txt VERDICT NO BLOCKERS\n" + tail, 2)):
        rc, out = sc(cid.replace("/", "_").replace("-", "_"), brief)
        check(cid, rc == want, out)


def _cases_completeness(base):
    """Every recognized review-file name in the brief's own text needs a block line."""
    sc = functools.partial(_scenario, base)
    for cid, brief, want in (
            ("complete/unclaimed-name", "Inputs: it7-r5-alpha.txt, it7-r5-beta.txt.\n" + BLOCK_ALPHA, 1),
            ("complete/incident-without-block", "Review it7-r5-alpha.txt (NO BLOCKERS).\n", 1),
            ("complete/missing-file-unclaimed", "Read it7-r9-alpha.txt.\n", 1),
            ("complete/zero-width-name-recognized", "Inputs: \N{ZERO WIDTH SPACE}it7-r5-alpha.txt.\n", 1),
            ("complete/nothing-named", "Fix the parser.\n", 0),
            ("complete/name-in-fence-not-required", "```\nit7-r5-alpha.txt\n```\n", 0),
            ("complete/name-in-quote-not-required", "> it7-r5-alpha.txt said NO BLOCKERS\n", 0),
            ("complete/path-covered-by-basename", "Read @IN@/it7-r5-alpha.txt first.\n" + BLOCK_ALPHA, 0),
            ("complete/unclosed-fence-cannot", "```\nInputs: it7-r5-alpha.txt\n" + BLOCK_ALPHA, 2),
            ("complete/unclosed-verbatim-cannot", BLOCK_ALPHA + "BEGIN VERBATIM alpha\nx\n", 2)):
        rc, out = sc(cid.replace("/", "_").replace("-", "_"), brief)
        check(cid, rc == want and ("UNCLAIMED" in out) == (want == 1), out)


def _cases_emit(base):
    """--emit writes the block from the trailers; a pasted block passes until a trailer changes."""
    rc, out, err, root, indir = _emit(base, "emit_ok", ["it7-r5-alpha.txt", "it7-r5-beta.txt"])
    check("emit/writes-block", rc == 0 and out == BLOCK_OK and err == "", out + err)
    _write(os.path.join(root, "b.md"), "Inputs: it7-r5-alpha.txt and it7-r5-beta.txt.\n" + out)
    rc2, out2, _ = _quiet(root, ["--input-dir", indir, os.path.join(root, "b.md")])
    check("emit/round-trip-passes", rc2 == 0 and "2 MATCH" in out2, out2)
    _write(os.path.join(indir, "it7-r5-beta.txt"), BETA5.replace("NO BLOCKERS", "BLOCKERS FOUND"))
    rc3, out3, _ = _quiet(root, ["--input-dir", indir, os.path.join(root, "b.md")])
    check("emit/then-trailer-changes", rc3 == 1 and "MISMATCH it7-r5-beta.txt" in out3, out3)
    os.unlink(os.path.join(indir, "it7-r5-beta.txt"))
    rc4, out4, _ = _quiet(root, ["--input-dir", indir, os.path.join(root, "b.md")])
    check("emit/then-file-removed", rc4 == 2 and "does not exist" in out4, out4)
    rc, out, err, _, _ = _emit(base, "emit_order", ["it7-r5-beta.txt", "it7-r5-alpha.txt"])
    check("emit/keeps-order", rc == 0 and out.splitlines()[1:3] == ["it7-r5-beta.txt VERDICT NO BLOCKERS",
                                                                    "it7-r5-alpha.txt VERDICT BLOCKERS FOUND"], out)
    for cid, names, files in (
            ("emit/malformed-trailer", ["it7-r5-beta.txt"], dict([("it7-r5-beta.txt", BETA5 + "Thanks.\n")])),
            ("emit/missing-file", ["it7-r9-beta.txt"], None),
            ("emit/name-not-pattern", ["notes.txt"], dict([("notes.txt", BETA5)])),
            ("emit/name-with-slash", ["in/it7-r5-beta.txt"], None),
            ("emit/duplicate-name", ["it7-r5-beta.txt", "it7-r5-beta.txt"], None),
            ("emit/no-names", [], None),
            ("emit/one-bad-name-emits-nothing", ["it7-r5-alpha.txt", "it7-r9-beta.txt"], None)):
        rc, out, err, _, _ = _emit(base, cid.replace("/", "_").replace("-", "_"), names, files)
        check(cid, rc == 2 and out == "" and "CANNOT-EVALUATE" in err, out + err)
    root, indir = _tree(base, "emit_nodir")
    rc, out, err = _quiet(root, ["--emit", "it7-r5-beta.txt"])
    check("emit/missing-input-dir", rc == 2 and out == "" and "--input-dir" in err, out + err)


def _cases_advisory(base):
    """QA rounds 1 to 3 on the prose side: every prose result is an ADVISORY line, and the exit code is
    decided by the block alone (here a correct block, so 0). The round 3 "respectively" swap is a recall
    miss of the prose rules (slash-joined tokens read as an option list); the block decides that case
    (block/mismatch-swapped)."""
    sc = functools.partial(_scenario, base)
    named = "Inputs: it7-r5-alpha.txt and it7-r5-beta.txt.\n"
    sweep = dict([("it7-r5-alpha.txt", "20 seeded, 10 accepted. Sweep: 41 of 42 mutants killed; 2 of 3 MAJORs "
                   "fixed; latency 2 seconds.\nVERDICT: BLOCKERS FOUND\n"), ("it7-r5-beta.txt", BETA5)])
    reg = dict([("reg.toml", 'format-version = 1\ntarget = "ITEM-7"\naliases = []\ndependencies = []\n'
                 "id-pattern = 'ITEM-[0-9]+'\n"), ("tmpl.md", "# Fix item seven round 4\n"
                 "Inputs: it7-r4-alpha.txt (alpha MEDIUM-1 and MAJOR-2 remain open)\n")])
    for cid, brief, files, args, needle in (
            ("advisory/prose-verdict-contradiction", "Inputs: it7-r5-alpha.txt (NO BLOCKERS).\n", None, (),
             "ADVISORY WARN"),
            ("advisory/option-list-instruction", "alpha leg: end with VERDICT: BLOCKERS FOUND or VERDICT: NO "
             "BLOCKERS\n", None, (), "blocking result(s)"),
            ("advisory/respectively-swap-missed", "Inputs: it7-r5-alpha.txt, it7-r5-beta.txt (NO BLOCKERS/BLOCKERS "
             "FOUND respectively)\n", None, (), "(2 MATCH); 0 advisory line(s)"),
            ("advisory/predicate-crossing", "Inputs: it7-r5-alpha.txt (20 accepted)\n", sweep, (), "ADVISORY WARN"),
            ("advisory/n-of-m-restated", "Inputs: it7-r5-alpha.txt (41 of 42 mutants killed)\n", sweep, (),
             "ADVISORY MATCH"),
            ("advisory/n-of-m-grades", "Inputs: it7-r5-alpha.txt (1 of 3 MAJORs fixed)\n", sweep, (), "ADVISORY"),
            ("advisory/decimal-latency", "Inputs: it7-r5-alpha.txt (latency 1.5 seconds)\n", sweep, (), "ADVISORY"),
            ("advisory/fullwidth-parentheses", "Inputs: it7-r5-alpha.txt \N{FULLWIDTH LEFT PARENTHESIS}NO "
             "BLOCKERS\N{FULLWIDTH RIGHT PARENTHESIS}\n", None, (), "blocking result(s)"),
            ("advisory/nbsp-before-parenthetical", "Inputs: it7-r5-alpha.txt\N{NO-BREAK SPACE}(NO BLOCKERS)\n", None,
             (), "ADVISORY WARN"),
            ("advisory/hangul-filler-prose", "Inputs: it7-r5-alpha.txt (NO\N{HANGUL FILLER} BLOCKERS)\n", None, (),
             "ADVISORY WARN"),
            ("advisory/not-regraded", "Inputs: it7-r5-alpha.txt (not REGRADED, NO BLOCKERS)\n", None, (),
             "ADVISORY EXEMPT"),
            ("advisory/unreadable-prose-path", "Read @IN@/notes/summary.md first.\n", None, (),
             "ADVISORY CANNOT-EVALUATE"),
            ("advisory/template-carry-over", "# Fix item seven round 5\nInputs: it7-r5-alpha.txt (alpha MEDIUM-1 "
             "and MAJOR-2 remain open)\n", None, ("--template", "@ROOT@/tmpl.md"), "carried over"),
            ("advisory/template-unreadable", named, None, ("--template", "@ROOT@/none.md"),
             "ADVISORY CANNOT-EVALUATE"),
            ("advisory/registry-foreign-id", "TASK: ITEM-9.\n", None, ("--registry", "@ROOT@/reg.toml"),
             "foreign item id"),
            ("advisory/registry-malformed", named, None, ("--registry", "@ROOT@/tmpl.md"),
             "ADVISORY CANNOT-EVALUATE"),
            ("advisory/embedded-swapped-family", named + "BEGIN VERBATIM beta\n3. **Major** - the cache key "
             "ignores the locale and returns stale rows.\nEND VERBATIM\n", None, (), "the source is family")):
        rc, out = sc(cid.replace("/", "_").replace("-", "_"), brief + BLOCK_OK, files=files, args=args, extra=reg)
        check(cid, rc == 0 and needle in out, out)
    me = sys.modules[__name__]
    real = me._advisory

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic advisory fault")
    me._advisory = broken
    try:
        rc, out = sc("adv_fault", named + BLOCK_OK)
    finally:
        me._advisory = real
    check("advisory/internal-fault-reported-not-refused", rc == 0 and "internal error RuntimeError" in out, out)
    rc, out = sc("adv_block_wins", "Inputs: it7-r5-alpha.txt (BLOCKERS FOUND).\n" + BLOCK_SWAPPED)
    check("advisory/prose-match-does-not-rescue-block", rc == 1 and "MISMATCH it7-r5-alpha.txt" in out, out)


def _cases_regex(base):
    """The adopter regex child: bounded, isolated, fail-closed for the blocking pattern."""
    sc = functools.partial(_scenario, base)
    me = sys.modules[__name__]
    slow = GOOD_CFG.replace("(?P<item>[a-z0-9]+)", "(?P<item>(a+)+)")
    saved = (me.REGEX_DEADLINE, me.CHILD_CPU)
    me.REGEX_DEADLINE = 2.0
    try:
        rc, out = sc("rx_deadline", "Inputs: " + "a" * 40 + "x\n", cfg=slow)
        check("regex/review-file-pattern-deadline", rc == 2 and "deadline" in out, out)
        reg = dict([("reg.toml", 'format-version = 1\ntarget = "ITEM-7"\naliases = []\ndependencies = []\n'
                     "id-pattern = '(a+)+b'\n")])
        rc, out = sc("rx_idpat", "TASK: " + "a" * 40 + "c\n" + BLOCK_OK, extra=reg,
                     args=["--registry", "@ROOT@/reg.toml"])
        check("regex/id-pattern-deadline-advisory", rc == 0 and "ADVISORY CANNOT-EVALUATE" in out
              and "deadline" in out, out)
    finally:
        me.REGEX_DEADLINE, me.CHILD_CPU = saved
    me.REGEX_DEADLINE, me.CHILD_CPU = 60.0, 1
    try:
        rc, out = sc("rx_cpu", "Inputs: " + "a" * 40 + "x\n", cfg=slow)
    finally:
        me.REGEX_DEADLINE, me.CHILD_CPU = saved
    check("regex/child-cpu-limit", rc == 2 and "CPU-time limit" in out and "deadline" not in out, out)
    real_run = me.subprocess.run

    def refuse(*args, **kwargs):
        raise ValueError("preexec_fn is not supported on this platform")

    def noisy(*args, **kwargs):
        done = real_run(*args, **kwargs)
        return subprocess.CompletedProcess(done.args, done.returncode, done.stdout, b"Warning: noise\n")
    me.subprocess.run = refuse
    try:
        rc, out = sc("rx_start", BLOCK_OK)
    finally:
        me.subprocess.run = real_run
    check("regex/child-start-failure", rc == 2 and "could not run" in out, out)
    me.subprocess.run = noisy
    try:
        rc, out = sc("rx_stderr", BLOCK_OK)
    finally:
        me.subprocess.run = real_run
    check("regex/child-stderr-fails-closed", rc == 2 and "wrote to stderr" in out, out)
    planted = os.path.join(base, "planted")
    _write(os.path.join(planted, "hashlib.py"), "raise SystemExit(7)\n")
    saved_env = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = planted
    try:
        rc, out = sc("rx_isolated", BLOCK_SWAPPED)
    finally:
        if saved_env is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = saved_env
    check("regex/child-isolated", rc == 1 and "MISMATCH it7-r5-alpha.txt" in out, out)
    if sys.platform.startswith("linux"):
        ok, detail = _orphan_probe(base)
    else:
        ok, detail = True, "not run"
        SKIPPED.append("regex/child-dies-with-parent (Linux only)")
    check("regex/child-dies-with-parent", ok, detail)


def _cases_contract(base):
    """The machine contract: the summary line, and the highest exit across briefs."""
    root, indir = _tree(base, "contract")
    for rel, text in (("ok.md", BLOCK_OK), ("bad.md", BLOCK_SWAPPED), ("cannot.md", BLOCK_OK + BLOCK_OK)):
        _write(os.path.join(root, rel), text)
    rc, out, _ = _quiet(root, ["--input-dir", indir, os.path.join(root, "ok.md")])
    last = out.splitlines()[-1] if out else ""
    check("contract/summary-last-line", rc == 0 and re.fullmatch(
        r"brief-claims: \S+: 2 blocking result\(s\) \(2 MATCH\); \d+ advisory line\(s\); exit 0", last), out)
    rc, out, _ = _quiet(root, ["--input-dir", indir, os.path.join(root, "ok.md"), os.path.join(root, "bad.md")])
    check("contract/several-briefs-highest-mismatch", rc == 1, out)
    rc, out, _ = _quiet(root, ["--input-dir", indir, os.path.join(root, "bad.md"), os.path.join(root, "cannot.md")])
    check("contract/several-briefs-highest-cannot", rc == 2, out)
    rc, out, _ = _quiet(root, ["--input-dir", indir, os.path.join(root, "bad.md")])
    words = ("BRIEF ", "MATCH ", "MISMATCH ", "CANNOT-EVALUATE ", "UNCLAIMED ", "ADVISORY ", "brief-claims: ")
    check("contract/every-line-starts-with-a-fixed-word", rc == 1 and out and all(
        line.startswith(words) for line in out.splitlines()), out)


def _proc_fields(pid):
    """Linux: the /proc stat fields of pid from its state onward ([state, ppid, ...]), or None if gone."""
    try:
        with open("/proc/{}/stat".format(pid), "rb") as handle:
            data = handle.read().decode("ascii", "replace")
    except OSError:
        return None
    return data[data.rindex(")") + 2:].split()


def _orphan_probe(base):
    """Linux: run a copy of this gate on a catastrophic adopter regex, SIGKILL the gate once its regex child
    exists, and require the child to be gone within five seconds. Returns (ok, detail)."""
    import shutil
    import signal
    import time
    root = os.path.join(base, "orphan")
    indir = os.path.join(root, "in")
    os.makedirs(indir)
    _write(os.path.join(root, CONFIG_REL), GOOD_CFG.replace("(?P<item>[a-z0-9]+)", "(?P<item>(a+)+)"))
    _write(os.path.join(root, "brief.md"), "Inputs: " + "a" * 40 + "x\n")
    gate = os.path.join(root, "tools", os.path.basename(__file__))
    os.makedirs(os.path.dirname(gate))
    shutil.copyfile(os.path.abspath(__file__), gate)
    child = None
    with subprocess.Popen([sys.executable, "-I", "-B", gate, "--input-dir", indir, os.path.join(root, "brief.md")],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as proc:
        until = time.monotonic() + 15
        while child is None and time.monotonic() < until and proc.poll() is None:
            child = next((int(p) for p in os.listdir("/proc") if p.isdigit()
                          and (_proc_fields(p) or ["", ""])[1] == str(proc.pid)), None)
            time.sleep(0.05)
        proc.kill()
    if child is None:
        return False, "no regex child was seen"
    until = time.monotonic() + 5
    while time.monotonic() < until:
        fields = _proc_fields(child)
        if fields is None or fields[0] in ("Z", "X"):
            return True, ""
        time.sleep(0.05)
    os.kill(child, signal.SIGKILL)
    return False, "regex child {} outlived its parent".format(child)


def _expected_check_ids():
    """This suite's registered check ids from tools/selftest_checks.toml, or None (a harness error)."""
    try:
        manifest = tomllib.loads(read_text(CHECKS_MANIFEST, "check manifest")[0])
    except (GateError, tomllib.TOMLDecodeError) as exc:
        print("SELF-TEST HARNESS ERROR: cannot read {}: {}".format(CHECKS_MANIFEST, exc), file=sys.stderr)
        return None
    for row in manifest.get("suite", []):
        if isinstance(row, dict) and row.get("id") == SUITE_ID:
            ids = row.get("expected-check-ids")
            if isinstance(ids, list) and all(isinstance(i, str) for i in ids) and len(set(ids)) == len(ids):
                return set(ids)
            break
    print("SELF-TEST HARNESS ERROR: missing or malformed suite {!r} in {}".format(SUITE_ID, CHECKS_MANIFEST),
          file=sys.stderr)
    return None


def self_test(report_path=None):
    import shutil
    import tempfile
    if report_path is not None:
        # The execution report is finalized at interpreter exit, after this run's cleanup
        # (tools/_selftest_exit_report.py); nothing writes it in band. Imported only here: the parent-death
        # probe runs a copy of this gate in a tempdir that does not carry it.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import _selftest_exit_report
        _selftest_exit_report.arm(report_path, SUITE_ID, EXECUTED)
    base = tempfile.mkdtemp(prefix="brief-claims-selftest-")
    try:
        _cases_config(base)
        _cases_trailer(base)
        _cases_block(base)
        _cases_completeness(base)
        _cases_emit(base)
        _cases_advisory(base)
        _cases_regex(base)
        _cases_contract(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    expected = _expected_check_ids()
    if expected is None:
        return 2
    executed = set(EXECUTED)
    missing, extra = sorted(expected - executed), sorted(executed - expected)
    repeated = sorted(set(i for i in EXECUTED if EXECUTED.count(i) > 1))
    failures = list(FAILURES) + [("execution-set/missing", m) for m in missing] + [
        ("execution-set/extra", e) for e in extra] + [("execution-set/repeated", r) for r in repeated]
    if failures:
        print("SELF-TEST FAIL: {} of {} vector(s)".format(len(failures), len(EXECUTED)))
        for name, out in failures:
            print("  - FAIL {}: {}".format(name, out))
        return 1
    summary = ("{} vector(s) held (configuration and input fail-closed, the trailer record, the claims-block "
               "grammar, completeness, --emit, the round 1 to 3 prose reproductions as advisory lines, regex "
               "bounds, the machine contract); execution set reconciled against tools/selftest_checks.toml".format(
                   len(EXECUTED)))
    if SKIPPED:
        print("SELF-TEST PASS (PARTIAL): {}; SKIPPED (UNVERIFIED this run): {}".format(summary, "; ".join(SKIPPED)))
    else:
        print("SELF-TEST PASS: " + summary)
    return 0


def main(argv):
    if argv == ["--regex-worker"]:
        return regex_worker()
    if (len(argv) in (2, 3) and argv[-2] == "--execution-report" and os.path.isabs(argv[-1])
            and argv[:-2] in ([], ["--self-test"])):
        return self_test(argv[-1])
    opts = parse_args(argv)
    if opts is None or (opts["self_test"] and argv != ["--self-test"]):
        print("usage: check_brief_claims.py [--require-config] [--input-dir DIR] [--template PATH] "
              "[--registry PATH] [BRIEF...] | --emit --input-dir DIR NAME... | --self-test "
              "[--execution-report ABS_PATH]; fail-closed", file=sys.stderr)
        return 2
    if opts["self_test"]:
        return self_test()
    return run(Path(__file__).resolve().parents[1], opts)


if __name__ == "__main__":
    _code = main(sys.argv[1:])
    _finalizer = sys.modules.get("_selftest_exit_report")
    (sys.exit if _finalizer is None else _finalizer.exit_with)(_code)
